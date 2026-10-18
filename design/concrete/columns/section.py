"""The column section: materials, bars, limits and the exact P-M solver (ACI 318M-14)."""

import math
import numpy as np
from design.criteria.code_config import CODE, AciCode
from design.concrete.columns.interaction import clip_moments, section_rings
from scipy.optimize import brentq
from sectionproperties.pre.library import rectangular_section, circular_section
import concreteproperties.stress_strain_profile as ssp
from concreteproperties import Concrete, ConcreteSection, SteelBar, add_bar, add_bar_circular_array
from concreteproperties.results import UltimateBendingResults
from concreteproperties.stress_strain_profile import RectangularStressBlock, SteelElasticPlastic


class ColumnFlexureDesign:
    """A structural design engine for reinforced concrete columns per ACI 318M-14.

    This class handles the definition of materials, cross-sections, and reinforcement,
    and provides a framework for evaluating moment capacity and code compliance.
    """

    def __init__(
        self,
        width: float,
        height: float,
        diameter: float,
        fc: float = 28.0,
        fy: float = 415.0,
        fyt: float = 415.0,
        dmain: float = 25.0,
        dties: float = 10.0,
        cc: float = 40.0,
        m_x: float = 0.0,
        m_y: float = 0.0,
        axial_load: float = 0.0,
        shape: str = "rectangular",
        is_smrf: bool = False,
        code: AciCode = CODE,
    ) -> None:
        """Initialize section geometry, material strengths, actions, and seismic category.

        Args:
            width, height, diameter: Rectangular dimensions or circular diameter in mm.
            fc, fy, fyt: Concrete and reinforcement strengths in MPa.
            dmain, dties, cc: Longitudinal bar, transverse bar, and cover dimensions in mm.
            m_x, m_y: Optional applied moments in N-mm.
            axial_load: Optional applied axial force in N; compression is positive.
            shape: Either ``"rectangular"`` or ``"circular"``.
            is_smrf: Whether NSCP Section 418 (ACI Chapter 18) SMRF column detailing applies.
            code: ACI constants (see criteria/code_config.py).
        """
        self.code = code
        self.width = width
        self.height = height
        self.diameter = diameter
        self.fc = fc
        self.fy = fy
        self.fyt = fyt
        self.dmain = dmain
        self.dties = dties
        self.cc = cc
        self.m_x = m_x
        self.m_y = m_y
        self.axial_load = axial_load
        self.shape = shape.lower()
        self.is_smrf = is_smrf
        self.design_results = {}

    def define_materials(self, fc_name: str = "Concrete", fy_name: str = "SteelBar"):
        """Define the nonlinear material models for concrete and steel.

        Uses ACI 318M-14 provisions to generate a Rectangular Stress Block
        for concrete and an Elastic-Plastic profile for the reinforcing steel.

        Args:
            fc_name (str): The label for the concrete material.
            fy_name (str): The label for the steel material.

        Returns:
            tuple: A tuple containing the initialized (Concrete, SteelBar) objects.
        """

        material = self.code.material

        concrete = Concrete(
            name=fc_name,
            density=material.concrete_density,  # kg/m^3
            # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
            stress_strain_profile=ssp.ConcreteLinear(
                elastic_modulus=material.concrete_modulus_coeff * (self.fc**0.5)
            ),  # MPa
            ultimate_stress_strain_profile=ssp.RectangularStressBlock(
                compressive_strength=self.fc,  # MPa
                alpha=material.stress_block_alpha,
                gamma=self.code.beta1(self.fc),
                ultimate_strain=material.concrete_ultimate_strain,
            ),
            flexural_tensile_strength=material.modulus_of_rupture_coeff
            * (self.fc**0.5),  # MPa
            colour="lightgrey",
        )

        steel = SteelBar(
            name=fy_name,
            density=material.steel_density,  # kg/m^3
            stress_strain_profile=ssp.SteelElasticPlastic(
                yield_strength=self.fy,  # MPa
                elastic_modulus=material.steel_elastic_modulus,  # MPa
                fracture_strain=material.steel_fracture_strain,
            ),
            colour="grey",
        )

        return concrete, steel

    def define_section(self, height, width, diameter, concrete):
        """Create the geometric cross-section based on self.shape."""
        if self.shape == "rectangular":
            section = rectangular_section(d=height, b=width, material=concrete)
        elif self.shape == "circular":
            # For circular sections, width is typically treated as the diameter
            section = circular_section(d=diameter, n=20, material=concrete)
        else:
            raise ValueError("Shape must be 'rectangular' or 'circular'.")

        return section

    def _calculate_required_bars(
        self, initial_bars: int, max_spacing: float | None = None
    ) -> int:
        """Determines total required bars, ensuring corners are populated and spacing is met."""
        strength = self.code.column_strength
        if max_spacing is None:
            max_spacing = strength.max_longitudinal_spacing
        if self.shape == "rectangular":
            core_width = self.width - (2 * self.cc) - (2 * self.dties) - self.dmain
            core_height = self.height - (2 * self.cc) - (2 * self.dties) - self.dmain

            # Determine minimum bar spaces per edge to satisfy max spacing
            nx = math.ceil(core_width / max_spacing)
            ny = math.ceil(core_height / max_spacing)

            required_bars = 2 * nx + 2 * ny

            # Symmetrically add bars to the longest spacing intervals until initial_bars is met
            while required_bars < initial_bars:
                if (core_width / nx) > (core_height / ny):
                    nx += 1
                else:
                    ny += 1
                required_bars = 2 * nx + 2 * ny

            return required_bars

        elif self.shape == "circular":
            core_radius = (self.diameter / 2) - self.cc - self.dties - (self.dmain / 2)
            perimeter = 2 * math.pi * core_radius
            required_bars = math.ceil(perimeter / max_spacing)
            return max(
                initial_bars,
                required_bars,
                strength.min_bars_circular if self.is_smrf else strength.min_bars_tied,
            )

    def solve_max_spacing(self) -> dict:
        """Evaluate maximum allowed spacing for transverse ties per ACI 318M-14.

        Calculates tie spacing limits based on the main longitudinal bar diameter,
        tie diameter, and the smallest overall column dimension.

        Returns:
            dict: A dictionary containing 'max_tie_spacing_mm' and 'max_long_spacing_mm' limits.
        """
        min_col_dim = (
            min(self.width, self.height)
            if self.shape == "rectangular"
            else self.diameter
        )
        transverse = self.code.column_transverse
        long_spacing = self.code.column_strength.max_longitudinal_spacing
        if self.shape == "circular":
            max_spiral_pitch = transverse.spiral_clear_spacing_max + self.dties
            return {
                "max_tie_spacing_mm": max_spiral_pitch,
                "max_long_spacing_mm": long_spacing,
                "min_spiral_pitch_mm": transverse.spiral_clear_spacing_min + self.dties,
                "max_spiral_clear_spacing_mm": transverse.spiral_clear_spacing_max,
            }

        max_tie_spacing = min(
            transverse.tie_spacing_bar_multiple * self.dmain,
            transverse.tie_spacing_tie_multiple * self.dties,
            min_col_dim,
        )
        return {
            "max_tie_spacing_mm": max_tie_spacing,
            "max_long_spacing_mm": long_spacing,
        }

    def check_reinforcement_limits(
        self, n_bars: int, is_smrf: bool | None = None
    ) -> dict:
        """Check longitudinal steel ratio (rho) against ACI 318M-14 requirements.

        Calculates the gross concrete area (Ag) and the total area of longitudinal
        steel (Ast) to ensure the reinforcement ratio falls between the 1% and 8% code limits.

        Args:
            n_bars (int): Total number of longitudinal reinforcing bars provided.

        Returns:
            dict: A dictionary detailing Ag, Ast, rho, upper/lower limits, and the compliance status ('Pass' or 'Fail').
        """
        ag = (
            self.width * self.height
            if self.shape == "rectangular"
            else (math.pi * (self.diameter**2)) / 4
        )
        ast = n_bars * (math.pi * (self.dmain**2) / 4)
        rho = ast / ag
        smrf = self.is_smrf if is_smrf is None else is_smrf
        strength = self.code.column_strength
        min_ratio = strength.rho_min
        max_ratio = strength.rho_max_smrf if smrf else strength.rho_max
        status = "Pass" if min_ratio <= rho <= max_ratio else "Fail"

        return {
            "Ag_mm2": ag,
            "Ast_mm2": ast,
            "rho": rho,
            "status": status,
            "limits": (min_ratio, max_ratio),
        }

    def add_reinf(
        self,
        section,
        bar_diameter,
        steelbar,
        initial_bars=4,
        bundle_layout: list[tuple[float, float, int]] | None = None,
    ):
        """Add longitudinal bars, optionally modeling each bundle at its centroid.

        A bundled group is represented by one equivalent steel area at the
        group's centroid. The physical bar count is preserved in that area.
        """
        area = (math.pi * (bar_diameter**2)) / 4

        if bundle_layout is not None:
            for x, y, count in bundle_layout:
                section = add_bar(
                    geometry=section,
                    area=area * count,
                    material=steelbar,
                    x=x,
                    y=y,
                )
            return section

        if self.shape == "rectangular":
            offset_x = self.cc + self.dties + (bar_diameter / 2)
            offset_y = self.cc + self.dties + (bar_diameter / 2)
            core_w = self.width - 2 * offset_x
            core_h = self.height - 2 * offset_y

            # Recalculate segments identical to the helper method
            max_spacing = self.code.column_strength.max_longitudinal_spacing
            nx = math.ceil(core_w / max_spacing)
            ny = math.ceil(core_h / max_spacing)
            required_bars = 2 * nx + 2 * ny

            while required_bars < initial_bars:
                if (core_w / nx) > (core_h / ny):
                    nx += 1
                else:
                    ny += 1
                required_bars = 2 * nx + 2 * ny

            # Map out exact coordinates for each edge (preventing corner duplication)
            bar_coords = []

            # Bottom edge (Includes bottom-left corner)
            for i in range(nx):
                bar_coords.append((offset_x + i * (core_w / nx), offset_y))

            # Right edge (Includes bottom-right corner)
            for i in range(ny):
                bar_coords.append((offset_x + core_w, offset_y + i * (core_h / ny)))

            # Top edge (Includes top-right corner)
            for i in range(nx):
                bar_coords.append(
                    (offset_x + core_w - i * (core_w / nx), offset_y + core_h)
                )

            # Left edge (Includes top-left corner)
            for i in range(ny):
                bar_coords.append((offset_x, offset_y + core_h - i * (core_h / ny)))

            # Draw all mapped bars
            for x, y in bar_coords:
                section = add_bar(
                    geometry=section, area=area, material=steelbar, x=x, y=y
                )

        elif self.shape == "circular":
            n_bars = self._calculate_required_bars(initial_bars)
            r_array = (self.diameter / 2) - self.cc - self.dties - (bar_diameter / 2)
            section = add_bar_circular_array(
                geometry=section,
                area=area,
                material=steelbar,
                n_bar=n_bars,
                r_array=r_array,
                ctr=(0, 0),
            )

        return section

    def solve_moment_capacity(
        self, section, axial_load=0.0, bending_angle=None
    ) -> tuple:
        """Calculate the nominal and design moment capacities (Mn, phi*Mn).

        Evaluates the capacity reduction factor based on strain conditions and
        cross-section shape per NSCP Table 421.2.2 (ACI Table 21.2.2). The bending angle is
        derived from the arctan of the applied biaxial moments if not specified.

        Args:
            section (Geometry): The reinforced concrete section.
            axial_load (float): The applied axial load in N. Defaults to 0.0.
            bending_angle (float, optional): The bending angle in radians. Defaults to arctan(My/Mx).

        Returns:
            tuple: (nominal_moment_capacity, ultimate_moment_capacity, capacity_reduction_factor)
        """
        section = _concrete_section_for(section)

        # Ag and Ast stay in mm2; MPa * mm2 therefore gives newtons.
        gross_area = (
            self.width * self.height
            if self.shape == "rectangular"
            else math.pi * self.diameter**2 / 4
        )
        steel_area = sum(
            geometry.calculate_area() for geometry in section.reinf_geometries_lumped
        )
        concrete_strength = self.fc
        steel_strength = self.fy

        # ACI 318M-14 Eq. 22.4.2.2 excludes longitudinal steel from the
        # concrete area before adding its fy*Ast contribution.
        # Po is the nominal concentric strength before the tied/spiral axial cap.
        strength_cfg = self.code.column_strength
        nominal_p0 = (
            self.code.material.stress_block_alpha * concrete_strength
            * (gross_area - steel_area)
            + steel_strength * steel_area
        )
        max_axial_factor = (
            strength_cfg.pn_max_factor_spiral
            if self.shape == "circular"
            else strength_cfg.pn_max_factor_tied
        )
        nominal_axial_capacity = max_axial_factor * nominal_p0
        nominal_tensile_capacity = steel_strength * steel_area

        if axial_load > nominal_axial_capacity or axial_load < -nominal_tensile_capacity:
            raise ValueError(
                "Applied axial load exceeds the NSCP 2015 (ACI 318M-14) maximum nominal "
                "compression or tension strength for this reinforcement layout."
            )

        n = axial_load

        # Calculate bending angle from applied moments if not overridden
        if bending_angle is None:
            if self.m_x == 0 and self.m_y == 0:
                bending_angle = np.pi / 2
            else:
                bending_angle = np.arctan2(self.m_x, self.m_y) - np.pi / 2

        fast_capacity = _fast_rectangular_block_capacity(
            section, bending_angle, n
        )
        if fast_capacity is None:
            nominal_moment_capacity = section.ultimate_bending_capacity(
                theta=bending_angle, n=n
            )
            stress_diagram = section.calculate_ultimate_stress(
                nominal_moment_capacity
            )
            strain_data = stress_diagram.lumped_reinforcement_strains
            # Strains are positive in compression. Only tension counts for phi,
            # so a section that is entirely in compression has zero tensile strain.
            extreme_strain = max(0.0, -float(np.min(strain_data)))
        else:
            nominal_moment_capacity, extreme_strain = fast_capacity

        # ACI 318M-14 Table 21.2.2: phi depends on the extreme tension strain
        # and on whether the column is tied or spirally reinforced.
        is_spiral = self.shape == "circular"
        capacity_reduction_factor = self.code.phi_flexure(
            extreme_strain, self.fy, spiral=is_spiral
        )

        ultimate_moment_capacity = (
            capacity_reduction_factor * nominal_moment_capacity.m_xy
        )
        # Pure-compression axial strength uses the compression-controlled phi,
        # not the moment-point phi returned for this combined Pu-Mn state.
        axial_capacity_reduction_factor = (
            self.code.strength.compression_spiral
            if is_spiral
            else self.code.strength.compression_tied
        )
        ultimate_axial_capacity = (
            axial_capacity_reduction_factor * nominal_axial_capacity
        )

        return (
            nominal_moment_capacity.m_xy,
            ultimate_moment_capacity,
            nominal_axial_capacity,
            ultimate_axial_capacity,
            capacity_reduction_factor,
        )

    def perform_column_flexure_design(self, initial_bars: int = 4) -> dict:
        """Execute the full column design, modeling, and evaluation workflow."""
        concrete, steel = self.define_materials()
        section = self.define_section(self.height, self.width, self.diameter, concrete)
        n_bars = self._calculate_required_bars(initial_bars)
        section = self.add_reinf(section, self.dmain, steel, n_bars)

        # Pass the initialized axial load to the solver
        (
            nom_moment_capacity,
            ult_moment_capacity,
            nom_axial_capacity,
            ult_axial_capacity,
            phi_factor,
        ) = self.solve_moment_capacity(section, axial_load=self.axial_load)

        spacing_limits = self.solve_max_spacing()
        rho_limits = self.check_reinforcement_limits(n_bars)

        self.design_results = {
            "shape": self.shape.capitalize(),
            "total_longitudinal_bars": n_bars,
            "tie_spacing_provided": min(
                spacing_limits["max_tie_spacing_mm"],
                self.code.column_transverse.tie_spacing_abs_max,
            ),
            "max_tie_spacing_allowed": spacing_limits["max_tie_spacing_mm"],
            "reinforcement_ratio": round(rho_limits["rho"], 4),
            "ratio_compliance": rho_limits["status"],
            "nominal_moment_capacity": nom_moment_capacity,
            "ultimate_moment_capacity": ult_moment_capacity,
            "nominal_axial_capacity": nom_axial_capacity,
            "ultimate_axial_capacity": ult_axial_capacity,
            "capacity_reduction_factor": phi_factor,
        }

        return self.design_results


def _concrete_section_for(geometry) -> ConcreteSection:
    """Return the analysis section for a reinforced geometry, building it only once.

    Building a ``ConcreteSection`` meshes the concrete, which is slow. The result
    is stored on the geometry, so repeated capacity checks of the same section
    (different axial loads or bending angles) reuse it and its capacity cache.
    """
    if isinstance(geometry, ConcreteSection):
        return geometry
    if isinstance(geometry, LazyColumnSection):
        geometry = geometry.geometry()
    cached = getattr(geometry, "_column_concrete_section", None)
    if cached is None:
        cached = ConcreteSection(geometry)
        setattr(geometry, "_column_concrete_section", cached)
    return cached


def _fast_rectangular_block_capacity(
    section: ConcreteSection, theta: float, axial_load: float
) -> tuple[UltimateBendingResults, float] | None:
    """Solve rectangular-block capacity directly for meshed concrete/lumped bars.

    ConcreteProperties splits and reintegrates the mesh during every root-finding
    iteration. For the project's rectangular stress block and lumped elastic-plastic
    reinforcement, a clipped concrete polygon and vectorized bar resultants give the
    same section actions without repeated mesh construction. Unsupported sections
    return ``None`` and use the library implementation.
    """
    if (
        len(section.meshed_geometries) != 1
        or section.reinf_geometries_meshed
        or section.strand_geometries
        or not section.reinf_geometries_lumped
    ):
        return None

    concrete_geometry = section.meshed_geometries[0]
    concrete_profile = getattr(
        concrete_geometry.material, "ultimate_stress_strain_profile", None
    )
    if not isinstance(concrete_profile, RectangularStressBlock):
        return None
    if any(
        not isinstance(bar.material.stress_strain_profile, SteelElasticPlastic)
        for bar in section.reinf_geometries_lumped
    ):
        return None

    cached = getattr(section, "_column_rectangular_block_cache", None)
    if cached is None:
        vertices = np.asarray(section.compound_geometry.points, dtype=float)
        bars = section.reinf_geometries_lumped
        steel_profile = bars[0].material.stress_strain_profile
        cached = {
            "vertices": vertices,
            "rings": section_rings(concrete_geometry.geom),
            "concrete_area": float(concrete_geometry.geom.area),
            "concrete_centroid": (
                float(concrete_geometry.geom.centroid.x),
                float(concrete_geometry.geom.centroid.y),
            ),
            "bar_xy": np.asarray(
                [bar.calculate_centroid() for bar in bars], dtype=float
            ),
            "bar_areas": np.asarray(
                [bar.calculate_area() for bar in bars], dtype=float
            ),
            "steel_strains": np.asarray(steel_profile.strains, dtype=float),
            "steel_stresses": np.asarray(steel_profile.stresses, dtype=float),
            "concrete_stress": float(concrete_profile.stresses[2]),
            "concrete_threshold": float(concrete_profile.strains[1]),
            "ultimate_strain": float(concrete_profile.ultimate_strain),
            "moment_centroid": tuple(map(float, section.moment_centroid)),
            "angle_cache": {},
        }
        setattr(section, "_column_rectangular_block_cache", cached)

    angle_key = float(theta)
    angle_data = cached["angle_cache"].get(angle_key)
    if angle_data is None:
        cosine = math.cos(theta)
        sine = math.sin(theta)
        vertices = cached["vertices"]
        bar_xy = cached["bar_xy"]
        projection_normal = np.array([-sine, cosine])
        projection_axis = np.array([cosine, sine])
        vertex_v = vertices @ projection_normal
        vertex_u = vertices @ projection_axis
        v_max = float(np.max(vertex_v))
        v_min = float(np.min(vertex_v))
        u_min = float(np.min(vertex_u))
        u_max = float(np.max(vertex_u))
        angle_data = {
            "cosine": cosine,
            "sine": sine,
            "v_max": v_max,
            "v_min": v_min,
            "depth": v_max - v_min,
            "u_min": u_min,
            "u_max": u_max,
            "margin": max(v_max - v_min, u_max - u_min, 1.0),
            "bar_v": bar_xy @ projection_normal,
        }
        cached["angle_cache"][angle_key] = angle_data

    depth = angle_data["depth"]
    if depth <= 0:
        return None

    cosine = angle_data["cosine"]
    sine = angle_data["sine"]
    v_max = angle_data["v_max"]
    v_min = angle_data["v_min"]
    centroid_x, centroid_y = cached["moment_centroid"]
    bar_xy = cached["bar_xy"]
    bar_v = angle_data["bar_v"]
    bar_areas = cached["bar_areas"]
    steel_strains = cached["steel_strains"]
    steel_stresses = cached["steel_stresses"]
    ultimate_strain = cached["ultimate_strain"]
    concrete_stress = cached["concrete_stress"]

    def section_actions(neutral_axis_depth: float) -> tuple[float, float, float]:
        """Return axial force and global moments for one neutral-axis depth."""
        compression_boundary = v_max - (
            1.0 - cached["concrete_threshold"] / ultimate_strain
        ) * neutral_axis_depth
        if compression_boundary >= v_max:
            concrete_area = 0.0
            concrete_cx, concrete_cy = centroid_x, centroid_y
        elif compression_boundary <= v_min:
            concrete_area = cached["concrete_area"]
            concrete_cx, concrete_cy = cached["concrete_centroid"]
        else:
            area, first_x, first_y = clip_moments(
                cached["rings"], -sine, cosine, compression_boundary
            )
            concrete_area = float(area)
            if concrete_area > 0:
                concrete_cx = float(first_x) / concrete_area
                concrete_cy = float(first_y) / concrete_area
            else:
                concrete_cx, concrete_cy = centroid_x, centroid_y

        concrete_force = concrete_area * concrete_stress
        axial = concrete_force
        moment_x = concrete_force * (concrete_cy - centroid_y)
        moment_y = concrete_force * (concrete_cx - centroid_x)

        neutral_axis_v = v_max - neutral_axis_depth
        strains = ultimate_strain * (bar_v - neutral_axis_v) / neutral_axis_depth
        stresses = np.asarray(
            np.interp(strains, steel_strains, steel_stresses), dtype=float
        )
        below_profile = strains < steel_strains[0]
        above_profile = strains > steel_strains[-1]
        if np.any(below_profile):
            stresses[below_profile] = steel_stresses[0] + (
                (strains[below_profile] - steel_strains[0])
                * (steel_stresses[1] - steel_stresses[0])
                / (steel_strains[1] - steel_strains[0])
            )
        if np.any(above_profile):
            stresses[above_profile] = steel_stresses[-1] + (
                (strains[above_profile] - steel_strains[-1])
                * (steel_stresses[-1] - steel_stresses[-2])
                / (steel_strains[-1] - steel_strains[-2])
            )

        bar_forces = stresses * bar_areas
        axial += float(np.sum(bar_forces))
        moment_x += float(np.dot(bar_forces, bar_xy[:, 1] - centroid_y))
        moment_y += float(np.dot(bar_forces, bar_xy[:, 0] - centroid_x))
        return axial, moment_x, moment_y

    try:
        neutral_axis_depth = brentq(
            lambda value: section_actions(value)[0] - axial_load,
            1e-6 * depth,
            6.0 * depth,
            xtol=1e-3,
            rtol=1e-6,
        )
    except ValueError:
        return None

    result = UltimateBendingResults(
        default_units=section.default_units,
        theta=theta,
    )
    result.d_n = neutral_axis_depth
    result.n, result.m_x, result.m_y = section_actions(neutral_axis_depth)
    result.m_xy = math.hypot(result.m_x, result.m_y)
    bar_depths = v_max - bar_v
    with np.errstate(divide="ignore", invalid="ignore"):
        neutral_axis_ratios = np.divide(neutral_axis_depth, bar_depths)
    result.k_u = float(np.min(neutral_axis_ratios))
    tensile_strains = ultimate_strain * (
        bar_v - (v_max - neutral_axis_depth)
    ) / neutral_axis_depth
    # Positive strain is compression; only the tension side matters for phi.
    extreme_tensile_strain = max(0.0, -float(np.min(tensile_strains)))
    return result, extreme_tensile_strain


class LazyColumnSection:
    """The reinforced geometry of a column, built (and meshed) on first use only.

    The bar layout search and the interaction surface need only the outline and
    the bars (``interaction.layout_section_data``); the concreteproperties
    geometry, which takes about 0.1 s per layout, is built only when the exact
    per-point solver or a drawing asks for it.
    """

    def __init__(self, engine: ColumnFlexureDesign, n_bars: int, bundle_layout) -> None:
        self.engine, self.n_bars, self.bundle_layout = engine, n_bars, bundle_layout
        self._geometry = None

    @property
    def is_built(self) -> bool:
        return self._geometry is not None

    def geometry(self):
        if self._geometry is None:
            concrete, steel = self.engine.define_materials()
            section = self.engine.define_section(
                self.engine.height, self.engine.width, self.engine.diameter, concrete)
            self._geometry = self.engine.add_reinf(
                section, self.engine.dmain, steel, initial_bars=self.n_bars,
                bundle_layout=self.bundle_layout)
        return self._geometry
