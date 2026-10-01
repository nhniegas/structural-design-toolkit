"""ACI 318M-14 reinforced-concrete column design and Excel integration.

The engine uses millimetres, MPa, kN, and kN-m at its Excel interface.
Section-library forces and moments are converted at the calculation boundary.
"""

import math
import os
import re
import numpy as np
import pandas as pd
import xlwings as xw
import ezdxf
from dataclasses import dataclass
from design.aci318_config import CODE, AciCode
from scipy.optimize import brentq
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from sectionproperties.pre.library import rectangular_section, circular_section
import concreteproperties.stress_strain_profile as ssp
from concreteproperties import (
    Concrete,
    ConcreteSection,
    SteelBar,
    add_bar,
    add_bar_circular_array,
)
from concreteproperties.results import UltimateBendingResults
from concreteproperties.stress_strain_profile import (
    RectangularStressBlock,
    SteelElasticPlastic,
)


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
            is_smrf: Whether ACI Chapter 18 SMRF column detailing applies.
            code: ACI constants (see aci318_config.py).
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
        cross-section shape per ACI 318M-14 Table 21.2.2. The bending angle is
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
                "Applied axial load exceeds the ACI 318M-14 maximum nominal "
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
    cached = getattr(geometry, "_column_concrete_section", None)
    if cached is None:
        cached = ConcreteSection(geometry)
        setattr(geometry, "_column_concrete_section", cached)
    return cached


def _read_excel_table(sheet, start_cell: str) -> pd.DataFrame:
    """Read a headered Excel table and normalize its column names."""
    frame = (
        sheet.range(start_cell)
        .options(pd.DataFrame, header=1, index=False, expand="table")
        .value
    )
    if frame is None:
        return pd.DataFrame()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError(f"Expected a tabular dataset at {sheet.name}!{start_cell}.")
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _normalize_object_name(value) -> str:
    """Normalize Excel/ETABS object identifiers, including numeric IDs like 502.0."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        integer_text = text[:-2]
        if integer_text.isdigit() and (
            integer_text == "0" or not integer_text.startswith("0")
        ):
            return integer_text
    return text


class IncompleteJointDataError(ValueError):
    """Signal that SMRF joint checks lack a framing member's design/load data."""


def _deduplicate_frame_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse repeated frame rows after verifying each member has one property set."""
    keys = ["UniqueName"]
    property_columns = [
        column
        for column in ("DesignType", "SectProp", "f'c", "Width", "Depth", "Diameter", "fy", "fys")
        if column in frame.columns
    ]
    unique_properties = frame.groupby(keys, dropna=False)[property_columns].nunique(
        dropna=False
    )
    inconsistent = unique_properties.index[(unique_properties > 1).any(axis=1)]
    if len(inconsistent):
        names = ", ".join(map(str, inconsistent[:10]))
        raise ValueError(
            "FRAME DATA contains conflicting rows for the same member: "
            f"{names}. Refresh the source table before designing."
        )
    return frame.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)


def _require_columns(
    frame: pd.DataFrame, required: set[str], source_name: str
) -> None:
    """Raise a source-specific error when an input table is missing required fields."""
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"{source_name} is missing required columns: {', '.join(sorted(missing))}. "
            f"Available columns: {', '.join(map(str, frame.columns))}"
        )


def _numeric(value, field_name: str, object_name: str) -> float:
    """Convert a required table value to a finite number with a useful error."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} for {object_name} must be numeric; received {value!r}."
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            f"{field_name} for {object_name} must be finite; received {value!r}."
        )
    return number


def _as_etabs_dataframe(result, table_name: str) -> pd.DataFrame:
    """Validate and normalize a DataFrame returned by the ETABS table API."""
    if isinstance(result, dict):
        raise RuntimeError(
            f"ETABS failed to retrieve '{table_name}': "
            f"{result.get('error', result)}"
        )
    if not isinstance(result, pd.DataFrame):
        raise RuntimeError(
            f"ETABS returned no tabular data for '{table_name}' "
            f"({type(result).__name__})."
        )
    result = result.copy()
    result.columns = [str(column).strip() for column in result.columns]
    if result.empty:
        raise RuntimeError(f"ETABS returned an empty '{table_name}' table.")
    return result


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...], source: str) -> str:
    """Find a case-insensitive field alias or report the received schema."""
    lookup = {str(column).strip().casefold(): column for column in frame.columns}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    raise ValueError(
        f"Could not identify {source} in table columns "
        f"{', '.join(map(str, frame.columns))}."
    )


def _extract_point_coordinates(point_table: pd.DataFrame) -> dict[str, np.ndarray]:
    """Build point coordinates from ETABS' Point Object Connectivity table.

    Returns:
        A map from normalized ETABS point unique names to global XYZ coordinates in
        the model's current length units.
    """
    point_name = _find_column(
        point_table, ("Point", "PointName", "UniqueName", "Name", "Label"), "point name"
    )
    x_column = _find_column(point_table, ("X", "X-coordinate", "Global X"), "X coordinate")
    y_column = _find_column(point_table, ("Y", "Y-coordinate", "Global Y"), "Y coordinate")
    z_column = _find_column(point_table, ("Z", "Z-coordinate", "Global Z"), "Z coordinate")

    coordinates: dict[str, np.ndarray] = {}
    for _, row in point_table.iterrows():
        name = _normalize_object_name(row[point_name])
        coordinates[name] = np.array(
            [
                _numeric(row[x_column], str(x_column), name),
                _numeric(row[y_column], str(y_column), name),
                _numeric(row[z_column], str(z_column), name),
            ],
            dtype=float,
        )
    return coordinates


def _extract_frame_angles(frame_table: pd.DataFrame) -> dict[str, float]:
    """Read local-axis rotation angles, in degrees, keyed by ETABS UniqueName.

    Returns:
        A map from normalized frame unique names to their ETABS beta angles.
    """
    name_column = _find_column(
        frame_table, ("UniqueName", "Frame", "Name", "Label"), "frame name"
    )
    angle_column = _find_column(
        frame_table,
        ("Angle", "Local Axis Angle", "Angle (deg)", "AngleDeg"),
        "local-axis angle",
    )
    angles: dict[str, float] = {}
    for _, row in frame_table.iterrows():
        name = _normalize_object_name(row[name_column])
        angle = _numeric(row[angle_column], str(angle_column), name)
        angles[name] = angle
    return angles


def _extract_frame_labels(frame_table: pd.DataFrame) -> dict[str, str]:
    """Read ETABS display labels, keyed by normalized frame UniqueName."""
    name_column = _find_column(
        frame_table, ("UniqueName", "Frame", "Name"), "frame unique name"
    )
    label_column = _find_column(
        frame_table, ("Label", "Frame Label"), "ETABS frame label"
    )
    labels = {}
    for _, row in frame_table.iterrows():
        name = _normalize_object_name(row[name_column])
        label = str(row[label_column]).strip()
        if name and label:
            labels[name] = label
    return labels


def _common_column_mark(unique_name: object) -> str:
    """Extract the shared column mark from a floor-qualified unique name."""
    text = _normalize_object_name(unique_name)
    match = re.search(r"(C\d+[A-Z]*)\s*$", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else text


def _unit_vector(vector: np.ndarray, description: str) -> np.ndarray:
    """Return a normalized 3D vector, rejecting degenerate frame geometry."""
    length = float(np.linalg.norm(vector))
    if length <= 1e-9:
        raise ValueError(f"Cannot determine {description} from coincident endpoints.")
    return vector / length


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
            "concrete": concrete_geometry.geom,
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
    concrete_geometry = cached["concrete"]

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
            margin = angle_data["margin"]
            u_min = angle_data["u_min"] - margin
            u_max = angle_data["u_max"] + margin
            v_upper = v_max + margin

            def local_to_global(u: float, v: float) -> tuple[float, float]:
                return (
                    u * cosine - v * sine,
                    u * sine + v * cosine,
                )

            compression_polygon = Polygon(
                [
                    local_to_global(u_min, compression_boundary),
                    local_to_global(u_max, compression_boundary),
                    local_to_global(u_max, v_upper),
                    local_to_global(u_min, v_upper),
                ]
            )
            clipped = concrete_geometry.intersection(compression_polygon)
            concrete_area = float(clipped.area)
            if concrete_area:
                concrete_cx = float(clipped.centroid.x)
                concrete_cy = float(clipped.centroid.y)
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


def _frame_local_axes(
    point_i: np.ndarray, point_j: np.ndarray, angle_degrees: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Construct ETABS local 1/2/3 axes from endpoints and beta angle.

    ETABS local 1 follows I-to-J. The unrotated 2/3 basis is chosen using global
    vertical as the reference; ETABS' beta rotation then rotates axes 2 and 3.
    """
    local_1 = _unit_vector(point_j - point_i, "frame local axis 1")
    vertical = np.array([0.0, 0.0, 1.0])
    local_2 = vertical - np.dot(vertical, local_1) * local_1
    if np.linalg.norm(local_2) <= 1e-8:
        reference = np.array([1.0, 0.0, 0.0])
        local_2 = reference - np.dot(reference, local_1) * local_1
    local_2 = _unit_vector(local_2, "frame local axis 2")
    local_3 = _unit_vector(np.cross(local_1, local_2), "frame local axis 3")

    beta = math.radians(angle_degrees)
    rotated_2 = math.cos(beta) * local_2 + math.sin(beta) * local_3
    rotated_3 = -math.sin(beta) * local_2 + math.cos(beta) * local_3
    return local_1, rotated_2, rotated_3


def _to_compression_positive(
    factored_loads: pd.DataFrame, code: AciCode = CODE
) -> pd.DataFrame:
    """Return a copy of the ETABS force table with axial force compression-positive.

    ETABS reports compression as negative. Every check in this module treats
    compression as positive, so the sign of ``P`` is flipped here, once.
    The input table is not modified.
    """
    converted = factored_loads.copy()
    if code.conventions.etabs_compression_is_negative:
        converted["P"] = -pd.to_numeric(converted["P"], errors="coerce")
    return converted


def _expand_combo_permutations(
    factored_loads: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Give every ETABS permutation of a load combination its own combo label.

    One combination with a response-spectrum or multi-direction case produces
    several permutations, each a different P-M2-M3 set. A column must be checked
    against every set, so each permutation becomes a separate internal combo
    (``"<combo>-<permutation>"``).

    Returns:
        The force table with internal combo labels, and a map from each internal
        label back to the plain combination name shown to the user.
    """
    converted = factored_loads.copy()
    names = converted["Combo"].astype(str).str.strip()
    if "Permutation" not in converted.columns:
        converted["Combo"] = names
        return converted, {name: name for name in names.unique()}
    permutation = pd.to_numeric(converted["Permutation"], errors="coerce")
    internal = names.where(
        permutation.isna(),
        names + "-" + permutation.fillna(0).astype(int).astype(str),
    )
    converted["Combo"] = internal
    return converted, dict(zip(internal, names))


def _collapse_combo_permutations(
    load_checks: pd.DataFrame,
    shear_checks: pd.DataFrame,
    joint_results: pd.DataFrame,
    combo_display: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Relabel permutation-level results with the plain combination name.

    Flexure/axial keeps one row per column, combination and end: the governing
    permutation, i.e. a failing one if any, otherwise the highest utilization.
    Shear and joint rows are only relabelled; the report builder already takes
    the worst value of every row that shares a column, combination and end.
    """
    def plain(series: pd.Series) -> pd.Series:
        text = series.astype(str)
        return text.map(combo_display).fillna(text)

    if not load_checks.empty:
        load_checks = load_checks.copy()
        load_checks["Combo"] = plain(load_checks["Combo"])
        utilization = pd.to_numeric(
            load_checks.get("Flexure_Utilization"), errors="coerce"
        )
        load_checks["_not_pass"] = load_checks["Strength_Check"].astype(str).ne("PASS")
        load_checks["_utilization"] = utilization.fillna(math.inf)
        load_checks = (
            load_checks.sort_values(
                ["_not_pass", "_utilization"], ascending=False, kind="stable"
            )
            .drop_duplicates(subset=["UniqueName", "Combo", "End"], keep="first")
            .sort_index()
            .drop(columns=["_not_pass", "_utilization"])
            .reset_index(drop=True)
        )
    if not shear_checks.empty:
        shear_checks = shear_checks.copy()
        shear_checks["Combo"] = plain(shear_checks["Combo"])
    if not joint_results.empty and "Load_Combo" in joint_results.columns:
        joint_results = joint_results.copy()
        joint_results["Load_Combo"] = plain(joint_results["Load_Combo"])
    return load_checks, shear_checks, joint_results


def _new_output_sheet(wb, sheet_name: str):
    """Return or create an output sheet and clear only its prior output table.

    Existing sheet formatting and unrelated cells are preserved.
    """
    try:
        sheet = wb.sheets[sheet_name]
    except KeyError:
        sheet = wb.sheets.add(sheet_name)
    used_last_cell = sheet.used_range.last_cell
    if used_last_cell.row >= 2 and used_last_cell.column >= 2:
        sheet.range(
            (2, 2), (used_last_cell.row, used_last_cell.column)
        ).clear_contents()
    return sheet


def _build_column_section(
    row: pd.Series,
    n_bars: int,
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    bundle_layout: list[tuple[float, float, int]] | None = None,
) -> tuple[ColumnFlexureDesign, object]:
    """Create a column engine and reinforced geometry for a frame-data row."""
    member = str(row["UniqueName"])
    diameter = row.get("Diameter")
    is_circular = pd.notna(diameter) and float(diameter) > 0
    width = 0.0 if is_circular else _numeric(row["Width"], "Width", member)
    height = 0.0 if is_circular else _numeric(row["Depth"], "Depth", member)
    diameter_value = _numeric(diameter, "Diameter", member) if is_circular else 0.0

    engine = ColumnFlexureDesign(
        width=width,
        height=height,
        diameter=diameter_value,
        fc=_numeric(row["f'c"], "f'c", member),
        fy=_numeric(row["fy"], "fy", member),
        fyt=_numeric(row["fys"], "fys", member),
        dmain=dmain,
        dties=dties,
        cc=cover,
        shape="circular" if is_circular else "rectangular",
        is_smrf=is_smrf,
    )
    concrete, steel = engine.define_materials()
    section = engine.define_section(height, width, diameter_value, concrete)
    section = engine.add_reinf(
        section,
        dmain,
        steel,
        initial_bars=n_bars,
        bundle_layout=bundle_layout,
    )
    return engine, section


def _perimeter_bar_positions(
    engine: ColumnFlexureDesign, nx: int, ny: int
) -> list[tuple[float, float]]:
    """Return unique, symmetric rectangular perimeter bar-center positions."""
    offset = engine.cc + engine.dties + engine.dmain / 2.0
    span_x = engine.width - 2.0 * offset
    span_y = engine.height - 2.0 * offset
    if min(span_x, span_y) <= 0:
        raise ValueError("Column core dimensions must be positive for bar placement.")

    positions = []
    # Include each corner once. The previous four independent loops repeated
    # every corner, which made the DXF appear to double-count corner bars.
    for index in range(nx + 1):
        positions.append((offset + index * span_x / nx, offset))
    for index in range(1, ny + 1):
        positions.append((offset + span_x, offset + index * span_y / ny))
    for index in range(nx - 1, -1, -1):
        positions.append((offset + index * span_x / nx, offset + span_y))
    for index in range(ny - 1, 0, -1):
        positions.append((offset, offset + index * span_y / ny))
    return positions


def _symmetric_position_groups(
    positions: list[tuple[float, float]], width: float, height: float
) -> list[list[int]]:
    """Group positions into mirror-symmetric tiers, corners before interior bars."""
    scale = 6
    index_by_point = {
        (round(x, scale), round(y, scale)): index
        for index, (x, y) in enumerate(positions)
    }
    unseen = set(range(len(positions)))
    groups = []
    while unseen:
        index = min(unseen)
        x, y = positions[index]
        orbit = {
            index_by_point[(round(xv, scale), round(yv, scale))]
            for xv in (x, width - x)
            for yv in (y, height - y)
            if (round(xv, scale), round(yv, scale)) in index_by_point
        }
        unseen.difference_update(orbit)
        groups.append(sorted(orbit))
    two_position_groups = [group for group in groups if len(group) == 2]
    groups = [group for group in groups if len(group) != 2]
    for index in range(0, len(two_position_groups), 2):
        paired_groups = two_position_groups[index : index + 2]
        groups.append(sorted(position for group in paired_groups for position in group))
    return sorted(
        groups,
        key=lambda group: (
            min(
                min(positions[i][0], width - positions[i][0])
                + min(positions[i][1], height - positions[i][1])
                for i in group
            ),
            min(group),
        ),
    )


def _bundle_spacing_is_valid(
    positions: list[tuple[float, float]],
    bundle_counts: list[int],
    bar_diameter: float,
    aggregate_size: float | None = None,
) -> bool:
    """Check clear distances between equivalent circular bar-bundle envelopes."""
    if aggregate_size is None:
        aggregate_size = CODE.column_strength.default_aggregate_size
    equivalent_diameters = [
        bar_diameter * math.sqrt(count) for count in bundle_counts
    ]
    for first in range(len(positions)):
        for second in range(first + 1, len(positions)):
            center_distance = math.dist(positions[first], positions[second])
            first_diameter = equivalent_diameters[first]
            second_diameter = equivalent_diameters[second]
            mean_diameter = (first_diameter + second_diameter) / 2.0
            minimum_clear = max(
                CODE.beam_detailing.min_clear_spacing,
                mean_diameter,
                CODE.beam_detailing.aggregate_spacing_factor * aggregate_size,
            )
            if center_distance + 1e-8 < mean_diameter + minimum_clear:
                return False
    return True


def _enumerate_column_bar_layouts(
    engine: ColumnFlexureDesign,
    max_bars: int,
    max_spacing: float | None = None,
    max_bundle_size: int | None = None,
    aggregate_size: float | None = None,
) -> list[list[tuple[float, float, int]]]:
    """Enumerate symmetric perimeter layouts, then bundle symmetric bar tiers.

    Bar bundles are limited to four bars, consistent with the usual ACI bundle
    limit. Rectangular tiers begin at the corners; matching reflected positions
    are incremented together, producing symmetric 4- or 8-bar additions where
    the section geometry permits. Circular layouts bundle uniformly around the
    circumference.
    """
    strength_cfg = engine.code.column_strength
    max_spacing = strength_cfg.max_longitudinal_spacing if max_spacing is None else max_spacing
    max_bundle_size = strength_cfg.max_bundle_size if max_bundle_size is None else max_bundle_size
    aggregate_size = (
        strength_cfg.default_aggregate_size if aggregate_size is None else aggregate_size
    )
    bar_diameter = engine.dmain
    layouts: dict[tuple, list[tuple[float, float, int]]] = {}

    def add_layout(positions: list[tuple[float, float]], counts: list[int]) -> None:
        """Store a candidate only when its bar count and bundle clearances pass."""
        total_bars = sum(counts)
        if total_bars > max_bars or not _bundle_spacing_is_valid(
            positions, counts, bar_diameter, aggregate_size
        ):
            return
        signature = tuple(
            sorted(
                (
                    round(x, 4),
                    round(y, 4),
                    count,
                )
                for (x, y), count in zip(positions, counts)
            )
        )
        layouts.setdefault(
            signature,
            [
                (float(x), float(y), int(count))
                for (x, y), count in zip(positions, counts)
            ],
        )

    if engine.shape == "circular":
        radius = engine.diameter / 2.0 - engine.cc - engine.dties - bar_diameter / 2.0
        if radius <= 0:
            raise ValueError("Circular column core radius must be positive.")
        circumference = 2.0 * math.pi * radius
        minimum_positions = max(
            strength_cfg.min_bars_circular if engine.is_smrf else strength_cfg.min_bars_tied,
            math.ceil(circumference / max_spacing),
        )
        for count in range(minimum_positions, max_bars + 1):
            positions = [
                (
                    radius * math.cos(2.0 * math.pi * index / count),
                    radius * math.sin(2.0 * math.pi * index / count),
                )
                for index in range(count)
            ]
            if not _bundle_spacing_is_valid(
                positions, [1] * count, bar_diameter, aggregate_size
            ):
                break
            add_layout(positions, [1] * count)
        for bundle_size in range(2, max_bundle_size + 1):
            for count in range(minimum_positions, max_bars // bundle_size + 1):
                positions = [
                    (
                        radius * math.cos(2.0 * math.pi * index / count),
                        radius * math.sin(2.0 * math.pi * index / count),
                    )
                    for index in range(count)
                ]
                add_layout(positions, [bundle_size] * count)
    else:
        offset = engine.cc + engine.dties + bar_diameter / 2.0
        span_x = engine.width - 2.0 * offset
        span_y = engine.height - 2.0 * offset
        if min(span_x, span_y) <= 0:
            raise ValueError("Column core dimensions must be positive.")
        min_nx = max(1, math.ceil(span_x / max_spacing))
        min_ny = max(1, math.ceil(span_y / max_spacing))
        min_pitch = (
            max(
                engine.code.beam_detailing.min_clear_spacing,
                bar_diameter,
                engine.code.beam_detailing.aggregate_spacing_factor * aggregate_size,
            )
            + bar_diameter
        )
        max_nx = max(min_nx, int(span_x // min_pitch))
        max_ny = max(min_ny, int(span_y // min_pitch))

        for nx in range(min_nx, max_nx + 1):
            if span_x / nx > max_spacing + 1e-8 or span_x / nx < min_pitch - 1e-8:
                continue
            for ny in range(min_ny, max_ny + 1):
                if span_y / ny > max_spacing + 1e-8 or span_y / ny < min_pitch - 1e-8:
                    continue
                positions = _perimeter_bar_positions(engine, nx, ny)
                counts = [1] * len(positions)
                add_layout(positions, counts)
                tiers = _symmetric_position_groups(
                    positions, engine.width, engine.height
                )
                for bundle_size in range(2, max_bundle_size + 1):
                    stalled = False
                    for tier in tiers:
                        if len(tier) not in (4, 8):
                            stalled = True
                            break
                        updated_counts = counts.copy()
                        if any(count != bundle_size - 1 for count in (counts[i] for i in tier)):
                            continue
                        for index in tier:
                            updated_counts[index] = bundle_size
                        if not _bundle_spacing_is_valid(
                            positions,
                            updated_counts,
                            bar_diameter,
                            aggregate_size,
                        ):
                            stalled = True
                            break
                        counts = updated_counts
                        add_layout(positions, counts)
                    if stalled:
                        break

    return sorted(
        layouts.values(),
        key=lambda layout: (
            sum(count for _, _, count in layout),
            max(count for _, _, count in layout),
            len(layout),
        ),
    )


def _column_force_at_end(
    forces: pd.DataFrame, combo: str, at_i_end: bool
) -> pd.Series:
    """Select the first/last station for a member and load combination.

    ETABS frame stations are treated as increasing from connectivity I to J.
    """
    combo_forces = forces.loc[forces["Combo"].astype(str).eq(str(combo))].copy()
    if combo_forces.empty:
        raise ValueError(f"No column force stations were found for combo {combo!r}.")
    combo_forces["Station"] = pd.to_numeric(combo_forces["Station"], errors="coerce")
    combo_forces = combo_forces.dropna(subset=["Station"])
    if combo_forces.empty:
        raise ValueError(f"Column force stations for combo {combo!r} are not numeric.")
    index = (
        combo_forces["Station"].idxmin()
        if at_i_end
        else combo_forces["Station"].idxmax()
    )
    return combo_forces.loc[index]


def _evaluate_column_candidate(
    frame_row: pd.Series,
    force_rows: pd.DataFrame,
    bar_layout: list[tuple[float, float, int]],
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    stop_at_first_failure: bool = False,
    progress=None,
) -> tuple[bool, list[dict], dict]:
    """Check one bundled longitudinal layout against every combo/end force pair.

    The resultant of M2/M3 is compared with the uniaxial section capacity at
    that moment direction; this radial check is not a full biaxial interaction
    surface.
    Returns:
        Overall pass status, detailed end/combination checks, and steel ratio data.
    """
    member = str(frame_row["UniqueName"])
    n_bars = sum(count for _, _, count in bar_layout)
    engine, section = _build_column_section(
        frame_row,
        n_bars,
        dmain,
        dties,
        cover,
        is_smrf,
        bundle_layout=bar_layout,
    )
    strength_limits = engine.check_reinforcement_limits(n_bars)
    if strength_limits["status"] != "Pass":
        return False, [], strength_limits

    checks: list[dict] = []
    passed = True
    for combo in sorted(force_rows["Combo"].dropna().astype(str).unique()):
        for endpoint, at_i in (("I", True), ("J", False)):
            if progress is not None:
                progress(member, endpoint, "Flexure and axial (P-M)", combo)
            force = _column_force_at_end(force_rows, combo, at_i)
            axial_kN = _numeric(force["P"], "P", member)
            m2_kNm = _numeric(force["M2"], "M2", member)
            m3_kNm = _numeric(force["M3"], "M3", member)
            moment_demand = math.hypot(m2_kNm, m3_kNm)
            theta = math.atan2(m3_kNm, m2_kNm)
            try:
                nominal, design, _, design_p, phi = engine.solve_moment_capacity(
                    section,
                    axial_load=axial_kN * 1000.0,
                    bending_angle=theta,
                )
            except ValueError as exc:
                checks.append(
                    {
                        "UniqueName": member,
                        "Combo": combo,
                        "End": endpoint,
                        "Pu_kN": axial_kN,
                        "Mu2_kNm": m2_kNm,
                        "Mu3_kNm": m3_kNm,
                        "Mu_resultant_kNm": moment_demand,
                        "Mn_kNm": np.nan,
                        "phi_Mn_kNm": np.nan,
                        "Phi": np.nan,
                        "phi_Pn_max_kN": np.nan,
                        "Axial_Check": f"ERROR: {exc}",
                        "Flexure_Check": f"ERROR: {exc}",
                        "Strength_Check": f"ERROR: {exc}",
                    }
                )
                passed = False
                if stop_at_first_failure:
                    return False, checks, strength_limits
                continue

            moment_capacity_kNm = float(design) / 1e6
            capacity_ratio = (
                moment_demand / moment_capacity_kNm
                if moment_capacity_kNm > 0
                else math.inf
            )
            moment_pass = moment_demand <= moment_capacity_kNm
            tension_capacity = (
                engine.code.strength.tension_controlled
                * n_bars
                * math.pi
                * dmain**2
                / 4.0
                * engine.fy
                / 1000.0
            )
            axial_pass = (
                axial_kN * 1000.0 <= float(design_p)
                if axial_kN >= 0
                else abs(axial_kN) <= tension_capacity
            )
            checks.append(
                {
                    "UniqueName": member,
                    "Combo": combo,
                    "End": endpoint,
                    "Pu_kN": axial_kN,
                    "Mu2_kNm": m2_kNm,
                    "Mu3_kNm": m3_kNm,
                    "Mu_resultant_kNm": moment_demand,
                    "Mn_kNm": float(nominal) / 1e6,
                    "phi_Mn_kNm": moment_capacity_kNm,
                    "Phi": float(phi),
                    "phi_Pn_max_kN": float(design_p) / 1000.0,
                    "phi_Pn_tension_kN": tension_capacity,
                    "Flexure_Utilization": capacity_ratio,
                    "Axial_Check": "PASS" if axial_pass else "FAIL",
                    "Flexure_Check": "PASS" if moment_pass else "FAIL",
                    "Strength_Check": "PASS" if moment_pass and axial_pass else "FAIL",
                }
            )
            passed = passed and moment_pass and axial_pass
            if stop_at_first_failure and not passed:
                return False, checks, strength_limits
    return passed, checks, strength_limits


def _smrf_so_limit(code: AciCode, hx: float | None = None) -> float:
    """Hoop spacing limit 'so' of ACI 18.7.5.3: 100 + (350 - hx)/3, kept within 100..150 mm."""
    cfg = code.column_seismic
    hx = cfg.hx_assumed if hx is None else hx
    so = cfg.so_min + (cfg.so_hx_reference - hx) / cfg.so_hx_divisor
    return min(cfg.so_max, max(cfg.so_min, so))


def _minimum_tie_diameter(engine: ColumnFlexureDesign) -> float:
    """Smallest permitted transverse bar diameter (ACI 25.7.2.2 / 25.7.3.2), in mm."""
    cfg = engine.code.column_transverse
    if engine.shape == "circular":
        return cfg.spiral_diameter_min
    if engine.dmain > cfg.large_bar_threshold:
        return cfg.tie_diameter_large_bars
    return cfg.tie_diameter_small_bars


def _column_transverse_candidate_passes(
    frame_row: pd.Series,
    force_rows: pd.DataFrame,
    engine: ColumnFlexureDesign,
    bar_layout: list[tuple[float, float, int]],
    max_compression: float,
    is_smrf: bool,
) -> tuple[bool, list[dict] | None, int]:
    """Check whether a longitudinal layout can satisfy its transverse detailing."""
    short_dimension = (
        min(engine.width, engine.height)
        if engine.shape == "rectangular"
        else engine.diameter
    )
    long_dimension = (
        max(engine.width, engine.height)
        if engine.shape == "rectangular"
        else engine.diameter
    )
    if not is_smrf:
        spacing_limit = engine.solve_max_spacing()["max_tie_spacing_mm"]
        transverse = {
            "Transverse_Legs_Per_Direction": 2,
            "Transverse_Spacing_Provided_mm": spacing_limit,
        }
    elif engine.shape == "rectangular":
        seismic = engine.code.column_seismic
        spacing_limit = min(
            engine.solve_max_spacing()["max_tie_spacing_mm"],
            seismic.spacing_dimension_fraction * short_dimension,
            seismic.spacing_bar_multiple * engine.dmain,
            _smrf_so_limit(engine.code),
        )
    else:
        seismic = engine.code.column_seismic
        spacing_limit = min(
            engine.solve_max_spacing()["max_tie_spacing_mm"],
            seismic.spacing_dimension_fraction * short_dimension,
            seismic.spacing_bar_multiple * engine.dmain,
            seismic.spiral_pitch_offset + engine.dties,
        )
    if is_smrf:
        transverse = _smrf_transverse_design(
            engine,
            sum(count for _, _, count in bar_layout),
            max_compression,
            spacing_limit,
        )
        provided_legs, _, support_check = _post_check_alternating_support(
            engine,
            bar_layout,
            transverse["Transverse_Legs_Per_Direction"],
        )
    else:
        provided_legs = 2
        support_check = "PASS"
    if is_smrf and engine.shape == "rectangular":
        core_width = engine.width - 2.0 * (engine.cc + engine.dties / 2.0)
        core_height = engine.height - 2.0 * (engine.cc + engine.dties / 2.0)
        tie_area = math.pi * engine.dties**2 / 4.0
        provided_x = provided_legs * tie_area / (
            transverse["Transverse_Spacing_Provided_mm"] * core_width
        )
        provided_y = provided_legs * tie_area / (
            transverse["Transverse_Spacing_Provided_mm"] * core_height
        )
        confinement_passes = (
            provided_x >= transverse["Required_Confinement_Ratio_X"]
            and provided_y >= transverse["Required_Confinement_Ratio_Y"]
        )
    elif is_smrf:
        confinement_passes = (
            transverse["Confinement_Check"] == "PASS"
        )
    else:
        confinement_passes = True

    minimum_tie_diameter = _minimum_tie_diameter(engine)
    seismic_cfg = engine.code.column_seismic
    detailing_passes = (
        not is_smrf
        or (
            short_dimension >= seismic_cfg.min_dimension
            and short_dimension / long_dimension >= seismic_cfg.min_aspect_ratio
            and confinement_passes
            and transverse["Transverse_Spacing_Check"] == "PASS"
            and support_check.startswith("PASS")
            and engine.dties >= minimum_tie_diameter
        )
    )
    return detailing_passes, None, 0


def _encode_bar_layout(layout: list[tuple[float, float, int]]) -> str:
    """Write a bar layout as text, ``"x,y,count;x,y,count;..."`` (mm, bars per position)."""
    return ";".join(f"{x:.2f},{y:.2f},{int(count)}" for x, y, count in layout)


def _decode_bar_layout(text: object) -> list[tuple[float, float, int]] | None:
    """Read a layout written by ``_encode_bar_layout``; ``None`` if the cell holds none."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None
    entries = [entry for entry in str(text).split(";") if entry.strip()]
    if not entries:
        return None
    layout = []
    for entry in entries:
        parts = entry.split(",")
        if len(parts) != 3:
            raise ValueError(f"Unrecognized bar layout data: {text!r}")
        layout.append((float(parts[0]), float(parts[1]), int(float(parts[2]))))
    return layout


def _column_layout_summary(layout: list[tuple[float, float, int]]) -> str:
    """Format the bundle count summary used in column-design report cells."""
    return "; ".join(
        (
            f"{sum(count == size for _, _, count in layout)} single bars"
            if size == 1
            else (
                f"{sum(count == size for _, _, count in layout)} "
                f"bundles of {size} bars"
            )
        )
        for size in sorted({count for _, _, count in layout})
    )


def _smrf_transverse_design(
    engine: ColumnFlexureDesign,
    n_bars: int,
    max_compression: float,
    spacing_limit: float,
) -> dict:
    """Estimate SMRF confinement demand and propose transverse reinforcement.

    ``max_compression`` is in N; all section dimensions, bar sizes, and spacing
    are in mm. The returned leg counts are a calculated minimum, not a bar-layout
    drawing or a complete seismic detailing audit.
    """
    # Ag is gross area; Ach is the core bounded by the transverse reinforcement.
    ag = (
        engine.width * engine.height
        if engine.shape == "rectangular"
        else math.pi * engine.diameter**2 / 4.0
    )
    tie_area = math.pi * engine.dties**2 / 4.0
    seismic = engine.code.column_seismic
    fyt = min(engine.fyt, engine.code.material.max_fyt_confinement)
    kf = max(1.0, engine.fc / seismic.kf_fc_divisor + seismic.kf_offset)
    # Trigger the additional confinement term for high axial load or high-strength
    # concrete. The code threshold is 0.3 Ag fc (all in N).
    high_axial_or_high_strength = (
        max_compression > seismic.high_axial_fraction * ag * engine.fc
        or engine.fc > seismic.high_strength_fc
    )

    if engine.shape == "circular":
        # Core diameter to the spiral centreline (used for the spiral ratio) ...
        core_diameter = engine.diameter - 2.0 * (engine.cc + engine.dties / 2.0)
        if core_diameter <= 0:
            raise ValueError("Circular column core diameter must be positive.")
        # ... but Ach is measured to the OUTSIDE edge of the spiral (ACI 18.7.5.4 notation).
        ach = math.pi * (engine.diameter - 2.0 * engine.cc) ** 2 / 4.0
        confinement_expressions = {
            "18.7.5(d)": seismic.spiral_coeff_d * (ag / ach - 1.0) * engine.fc / fyt,
            "18.7.5(e)": seismic.spiral_coeff_e * engine.fc / fyt,
        }
        if high_axial_or_high_strength:
            confinement_expressions["18.7.5(f)"] = (
                seismic.spiral_coeff_f * kf * max_compression / (fyt * ach)
            )
        controlling_expression, required_ratio = max(
            confinement_expressions.items(), key=lambda item: item[1]
        )
        pitch_for_confinement = (
            4.0 * tie_area / (required_ratio * core_diameter)
            if required_ratio > 0
            else math.inf
        )
        spacing = min(spacing_limit, pitch_for_confinement)
        provided_ratio = 4.0 * tie_area / (core_diameter * spacing)
        clear_spacing = spacing - engine.dties
        check = (
            "PASS"
            if provided_ratio >= required_ratio
            and engine.code.column_transverse.spiral_clear_spacing_min
            <= clear_spacing
            <= engine.code.column_transverse.spiral_clear_spacing_max
            else "FAIL"
        )
        return {
            "Transverse_Type": "Spiral",
            "Core_Dimension_mm": core_diameter,
            "Confinement_18_7_5_d": confinement_expressions["18.7.5(d)"],
            "Confinement_18_7_5_e": confinement_expressions["18.7.5(e)"],
            "Confinement_18_7_5_f": confinement_expressions.get(
                "18.7.5(f)", np.nan
            ),
            "Kf": kf,
            "Kn": np.nan,
            "Confinement_Criteria_Governing": controlling_expression,
            "Required_Ash_s_Ratio_X": required_ratio,
            "Required_Ash_s_Ratio_Y": required_ratio,
            "Provided_Ash_s_Ratio_X": provided_ratio,
            "Provided_Ash_s_Ratio_Y": provided_ratio,
            "Required_Confinement_Ratio": required_ratio,
            "Provided_Confinement_Ratio": provided_ratio,
            "Confinement_Check": check,
            "Transverse_Spacing_Provided_mm": spacing,
            "Transverse_Spacing_Check": (
                "PASS" if spacing <= spacing_limit + 1e-8 else "FAIL"
            ),
            "High_Axial_or_High_fc_Check": (
                "APPLIED" if high_axial_or_high_strength else "NOT APPLIED"
            ),
            "Spiral_Clear_Spacing_mm": clear_spacing,
            "Transverse_Legs_Per_Direction": 1,
            "Alternating_Support_Check": "N/A for continuous spiral",
        }

    core_width = engine.width - 2.0 * (engine.cc + engine.dties / 2.0)
    core_height = engine.height - 2.0 * (engine.cc + engine.dties / 2.0)
    if min(core_width, core_height) <= 0:
        raise ValueError("Rectangular column core dimensions must be positive.")
    # Ach is measured to the OUTSIDE edges of the transverse reinforcement
    # (ACI 18.7.5.4); the hoop centreline dimensions above are used for Ash/(s bc).
    ach = (engine.width - 2.0 * engine.cc) * (engine.height - 2.0 * engine.cc)

    nl = max(n_bars, 4)
    # ACI rectilinear-hoop confinement factor based on longitudinal bar count.
    kn = nl / (nl - 2.0)
    base_a = seismic.rect_coeff_a * (ag / ach - 1.0) * engine.fc / fyt
    base_b = seismic.rect_coeff_b * engine.fc / fyt
    confinement_expressions = {
        "18.7.5(a)": base_a,
        "18.7.5(b)": base_b,
    }
    if high_axial_or_high_strength:
        base_c = seismic.rect_coeff_c * kf * kn * max_compression / (fyt * ach)
        confinement_expressions["18.7.5(c)"] = base_c
    controlling_expression, required_ratio = max(
        confinement_expressions.items(), key=lambda item: item[1]
    )

    short_dimension = min(engine.width, engine.height)
    hx_limit = (
        seismic.hx_max_high_axial if high_axial_or_high_strength else seismic.hx_max
    )
    hx = min(seismic.hx_assumed, hx_limit)
    so = _smrf_so_limit(engine.code, hx)
    spacing = min(
        spacing_limit,
        seismic.spacing_dimension_fraction * short_dimension,
        seismic.spacing_bar_multiple * engine.dmain,
        so,
    )

    # The perimeter layout uses interior support legs in addition to the closed hoop.
    max_leg_spacing = engine.code.column_strength.max_longitudinal_spacing
    nx = max(1, math.ceil(core_width / max_leg_spacing))
    ny = max(1, math.ceil(core_height / max_leg_spacing))
    minimum_legs = max(2, nx, ny)
    required_legs_x = max(
        minimum_legs,
        math.ceil(required_ratio * spacing * core_width / tie_area),
    )
    required_legs_y = max(
        minimum_legs,
        math.ceil(required_ratio * spacing * core_height / tie_area),
    )
    provided_ratio_x = required_legs_x * tie_area / (spacing * core_width)
    provided_ratio_y = required_legs_y * tie_area / (spacing * core_height)
    confinement_pass = (
        provided_ratio_x >= required_ratio
        and provided_ratio_y >= required_ratio
    )

    return {
        "Transverse_Type": "Rectilinear hoops and crossties",
        "Core_Dimension_mm": f"{core_width:.1f} x {core_height:.1f}",
        "Confinement_18_7_5_a": base_a,
        "Confinement_18_7_5_b": base_b,
        "Confinement_18_7_5_c": confinement_expressions.get(
            "18.7.5(c)", np.nan
        ),
        "Kf": kf,
        "Kn": kn,
        "Confinement_Criteria_Governing": controlling_expression,
        "Required_Ash_s_Ratio_X": required_ratio,
        "Required_Ash_s_Ratio_Y": required_ratio,
        "Provided_Ash_s_Ratio_X": provided_ratio_x,
        "Provided_Ash_s_Ratio_Y": provided_ratio_y,
        "Required_Confinement_Ratio_X": required_ratio,
        "Required_Confinement_Ratio_Y": required_ratio,
        "Provided_Confinement_Ratio_X": provided_ratio_x,
        "Provided_Confinement_Ratio_Y": provided_ratio_y,
        "Required_Confinement_Ratio": required_ratio,
        "Provided_Confinement_Ratio": min(provided_ratio_x, provided_ratio_y),
        "Confinement_Check": "PASS" if confinement_pass else "FAIL",
        "Transverse_Spacing_Provided_mm": spacing,
        "Transverse_Spacing_Check": (
            "PASS"
            if spacing <= spacing_limit + 1e-8
            and spacing <= seismic.spacing_dimension_fraction * short_dimension + 1e-8
            and spacing <= seismic.spacing_bar_multiple * engine.dmain + 1e-8
            and spacing <= so + 1e-8
            else "FAIL"
        ),
        "High_Axial_or_High_fc_Check": (
            "APPLIED" if high_axial_or_high_strength else "NOT APPLIED"
        ),
        "Hx_Limit_mm": hx_limit,
        "Hx_Assumed_mm": hx,
        "Transverse_Legs_Per_Direction": max(required_legs_x, required_legs_y),
        "Alternating_Support_Check": "Pending post-check",
    }


def _post_check_alternating_support(
    engine: ColumnFlexureDesign,
    bundle_layout: list[tuple[float, float, int]],
    provided_legs: int,
) -> tuple[int, int, str]:
    """Add transverse legs as needed to support alternate perimeter bar groups."""
    if engine.shape == "circular":
        return provided_legs, 0, "N/A for continuous spiral"

    positions = [(x, y) for x, y, _ in bundle_layout]
    min_x = min(x for x, _ in positions)
    max_x = max(x for x, _ in positions)
    min_y = min(y for _, y in positions)
    max_y = max(y for _, y in positions)
    tolerance = 1e-6
    bars_on_horizontal_face = max(
        len({round(x, 5) for x, y in positions if abs(y - face) <= tolerance})
        for face in (min_y, max_y)
    )
    bars_on_vertical_face = max(
        len({round(y, 5) for x, y in positions if abs(x - face) <= tolerance})
        for face in (min_x, max_x)
    )
    required_horizontal = max(
        2, 2 + math.ceil(max(0, bars_on_horizontal_face - 2) / 2)
    )
    required_vertical = max(
        2, 2 + math.ceil(max(0, bars_on_vertical_face - 2) / 2)
    )
    required_legs = max(required_horizontal, required_vertical)
    final_legs = max(provided_legs, required_legs)
    added_legs = final_legs - provided_legs
    status = (
        f"PASS - {final_legs} legs support alternating perimeter bar groups"
        if added_legs == 0
        else (
            f"PASS - increased transverse legs by {added_legs} "
            f"to {final_legs} for alternating support"
        )
    )
    return final_legs, added_legs, status


def _beam_end_reinforcement(
    beam_row: pd.Series,
    beam_face_rows: pd.DataFrame,
    end_name: str,
    fy_multiplier: float,
):
    """Calculate beam Mn and probable bar tension at a joint end.

    Beam design supplies the TOP/BOTTOM layer counts; slab reinforcement is
    intentionally excluded. ``fy_multiplier`` is 1.0 for nominal strength and
    1.25 for probable joint-force calculations.
    """
    from design.beam_designer_aci318 import BeamFlexureDesign

    member = str(beam_row["UniqueName"])
    width = _numeric(beam_row["Width"], "Width", member)
    depth = _numeric(beam_row.get("Depth", beam_row.get("Height")), "Depth", member)
    fc = _numeric(beam_row["f'c"], "f'c", member)
    fy = _numeric(beam_row["fy"], "fy", member)
    dmain = _numeric(beam_row.get("dm"), "dm", member)
    stirrup_diameter = _numeric(beam_row.get("ds", dmain), "ds", member)
    cover = _numeric(beam_row.get("cc", 40.0), "cc", member)
    end_prefix = "n_left" if end_name == "I" else "n_right"
    layer_columns = [f"{end_prefix}_L{layer}" for layer in (1, 2, 3)]
    _require_columns(beam_face_rows, set(layer_columns) | {"Face"}, f"BEAM DESIGN ({member})")

    top_rows = beam_face_rows.loc[beam_face_rows["Face"].astype(str).str.upper().eq("TOP")]
    bottom_rows = beam_face_rows.loc[
        beam_face_rows["Face"].astype(str).str.upper().eq("BOTTOM")
    ]
    if top_rows.empty or bottom_rows.empty:
        raise ValueError(
            f"BEAM DESIGN must contain TOP and BOTTOM reinforcement rows for {member}."
        )
    top_row = top_rows.iloc[0]
    bottom_row = bottom_rows.iloc[0]

    def bar_count(row: pd.Series) -> int:
        """Sum reinforcement counts in the three reported layers at one beam face."""
        return sum(
            max(0, int(float(row[column])))
            for column in layer_columns
            if pd.notna(row[column])
        )

    n_top = bar_count(top_row)
    n_bottom = bar_count(bottom_row)

    flexure = BeamFlexureDesign(
        width,
        depth,
        fc,
        fy * fy_multiplier,
        fy * fy_multiplier,
        dmain,
        stirrup_diameter,
        cover,
    )
    flexure.n_top = n_top
    flexure.n_bot = n_bottom
    negative = flexure.solve_moment_capacity(is_negative_moment=True)
    positive = flexure.solve_moment_capacity(is_negative_moment=False)
    tension_area_top = n_top * math.pi * dmain**2 / 4.0
    tension_area_bottom = n_bottom * math.pi * dmain**2 / 4.0
    return {
        "Mn_top_kNm": float(negative["Mn"]),
        "Mn_bottom_kNm": float(positive["Mn"]),
        "T_top_kN": tension_area_top * fy * fy_multiplier / 1000.0,
        "T_bottom_kN": tension_area_bottom * fy * fy_multiplier / 1000.0,
        "n_top": n_top,
        "n_bottom": n_bottom,
        "Largest_Beam_Bar_mm": dmain,
        "Beam_Depth_mm": depth,
        "Beam_Width_mm": width,
        "Beam_fc_MPa": fc,
    }


def _column_shear_checks(
    row: pd.Series,
    forces: pd.DataFrame,
    engine: ColumnFlexureDesign,
    n_bars: int,
    spacing: float,
    confinement_legs: int,
    is_smrf: bool,
    bundle_layout: list[tuple[float, float, int]] | None = None,
    progress=None,
) -> tuple[list[dict], int]:
    """Check column shear in both local directions and size transverse legs.

    The function compares analysis shear with the capacity-based probable-moment
    shear, then selects one leg count sufficient for all force combinations.
    Forces are kN, moments kN-m, dimensions mm, and stresses MPa at the interface.
    """
    member = str(row["UniqueName"])
    if engine.shape == "circular":
        section_depth = engine.diameter
        web_width = engine.diameter
    else:
        section_depth = engine.height
        web_width = engine.width
    ag = (
        engine.width * engine.height
        if engine.shape == "rectangular"
        else math.pi * engine.diameter**2 / 4.0
    )
    tie_area = math.pi * engine.dties**2 / 4.0
    fyt = engine.fyt
    phi_shear = engine.code.strength.shear
    seismic_cfg = engine.code.column_seismic
    shear_cfg = engine.code.column_shear
    maximum_legs = max(1, confinement_legs)

    station_values = pd.to_numeric(forces["Station"], errors="coerce").dropna()
    if station_values.empty or station_values.max() <= station_values.min():
        raise ValueError(
            f"Column {member} needs distinct numeric end stations for capacity shear."
        )
    clear_length = float(station_values.max() - station_values.min())
    if engine.shape == "rectangular":
        axis_dimensions = {
            # V2 acts along local 2: breadth and effective depth are along local 3/2.
            "V2": (engine.height, engine.width),
            "V3": (engine.width, engine.height),
        }
    else:
        axis_dimensions = {
            "V2": (web_width, section_depth),
            "V3": (web_width, section_depth),
        }
    output: list[dict] = []

    # The probable-strength section (1.25 fy) is the same for every combination,
    # direction and end, so it is built once and only the axial load changes.
    probable_row = row.copy()
    probable_row["UniqueName"] = member
    probable_row["fy"] = engine.fy * seismic_cfg.probable_stress_factor
    probable_engine, probable_section = _build_column_section(
        probable_row,
        n_bars,
        engine.dmain,
        engine.dties,
        engine.cc,
        is_smrf,
        bundle_layout=bundle_layout,
    )

    # Find one transverse-leg count that satisfies every combo/end/direction.
    for combo in sorted(forces["Combo"].dropna().astype(str).unique()):
        end_forces = {
            end: _column_force_at_end(forces, combo, at_i_end=end == "I")
            for end in ("I", "J")
        }
        for shear_name, (breadth, overall_depth) in axis_dimensions.items():
            v_name = shear_name
            moment_name = "M3" if shear_name == "V2" else "M2"
            moment_theta = math.pi / 2.0 if moment_name == "M3" else 0.0
            probable_moments = []
            for end in ("I", "J"):
                if progress is not None:
                    progress(
                        member, end, f"Column shear, {shear_name} (capacity design)", combo
                    )
                axial = _numeric(end_forces[end]["P"], "P", member) * 1000.0
                probable_mn, _, _, _, _ = probable_engine.solve_moment_capacity(
                    probable_section,
                    axial_load=axial,
                    bending_angle=moment_theta,
                )
                probable_moments.append(abs(float(probable_mn)) / 1e6)
            # Capacity-based Ve is the probable end-moment sum divided by clear span.
            capacity_shear_kN = (
                sum(probable_moments) * 1000.0 / clear_length
            )

            effective_depth = max(
                1.0,
                overall_depth - engine.cc - engine.dties - engine.dmain / 2.0,
            )
            compression = max(
                0.0,
                max(
                    _numeric(end_forces[end]["P"], "P", member) * 1000.0
                    for end in ("I", "J")
                ),
            )
            shear_concrete = 0.0
            if not is_smrf:
                axial_factor = max(
                    0.0, 1.0 + compression / (shear_cfg.axial_divisor * ag)
                )
                vc_upper = (
                    shear_cfg.vc_coeff
                    * math.sqrt(engine.fc)
                    * breadth
                    * effective_depth
                    * axial_factor
                )
                vc_limit = (
                    shear_cfg.vc_upper_coeff
                    * math.sqrt(engine.fc)
                    * breadth
                    * effective_depth
                )
                shear_concrete = min(vc_upper, vc_limit)

            for end in ("I", "J"):
                force = end_forces[end]
                analysis_shear = abs(_numeric(force[v_name], v_name, member))
                design_shear = max(analysis_shear, capacity_shear_kN)
                # Convert design shear to required steel shear after subtracting Vc.
                required_vs = max(
                    0.0, design_shear * 1000.0 / phi_shear - shear_concrete
                )
                required_legs = max(
                    1,
                    math.ceil(
                        required_vs * spacing / (fyt * effective_depth * tie_area)
                    ),
                )
                required_legs = max(required_legs, confinement_legs)
                maximum_legs = max(maximum_legs, required_legs)
                output.append(
                    {
                        "UniqueName": member,
                        "Combo": combo,
                        "End": end,
                        "Shear_Direction": shear_name,
                        "Analysis_Shear_kN": analysis_shear,
                        "Capacity_Based_Ve_kN": capacity_shear_kN,
                        "Design_Shear_kN": design_shear,
                        "Vc_kN": shear_concrete / 1000.0,
                        "Concrete_Shear_Strength_Neglected": is_smrf,
                        "Required_Transverse_Legs": required_legs,
                        "_effective_depth_mm": effective_depth,
                        "_shear_concrete_N": shear_concrete,
                        "Vc_Assumption": (
                            "0 used conservatively for all SMRF checks"
                            if is_smrf
                            else "ACI 22.5 axial-compression expression"
                        ),
                    }
                )
    for item in output:
        shear_capacity = phi_shear * (
            item["_shear_concrete_N"]
            + maximum_legs
            * tie_area
            * fyt
            * item["_effective_depth_mm"]
            / spacing
        ) / 1000.0
        item["Provided_Transverse_Legs"] = maximum_legs
        item["phi_Vn_kN"] = shear_capacity
        item["Shear_Utilization"] = (
            item["Design_Shear_kN"] / shear_capacity
            if shear_capacity > 0
            else math.inf
        )
        item["Shear_Check"] = (
            "PASS" if item["Design_Shear_kN"] <= shear_capacity else "FAIL"
        )
        del item["_effective_depth_mm"]
        del item["_shear_concrete_N"]
    return output, maximum_legs


def _evaluate_smrf_joints(
    column_results: pd.DataFrame,
    connectivity: pd.DataFrame,
    frame_data: pd.DataFrame,
    factored_loads: pd.DataFrame,
    beam_design: pd.DataFrame,
    point_coordinates: dict[str, np.ndarray],
    frame_angles: dict[str, float],
    is_smrf: bool,
    dmain: float,
    dties: float,
    cover: float,
    column_layouts: dict[str, list[tuple[float, float, int]]],
    progress=None,
) -> pd.DataFrame:
    """Evaluate SMRF column/beam ratio, panel shear, and continuity at each joint.

    Beam reinforcement comes from BEAM DESIGN and is held constant across
    combinations; column nominal strength uses the combination-specific axial
    force. The local beam basis uses beta=0 when the ETABS table supplies no beam
    angle (the current Local Axes table contains columns only).
    """
    if not is_smrf:
        return pd.DataFrame()

    from design.beam_designer_aci318 import restore_beam_result_labels

    connectivity = connectivity.copy()
    frame_data = frame_data.copy()
    factored_loads = factored_loads.copy()
    # The design export labels its first/second/third bar layers per beam end.
    # Restore those stable names before mapping I/J-end steel into joint checks.
    beam_design = restore_beam_result_labels(beam_design)
    for frame in (connectivity, frame_data, factored_loads, beam_design, column_results):
        if "UniqueName" in frame.columns:
            frame["UniqueName"] = frame["UniqueName"].map(_normalize_object_name)
    for point_column in ("UniquePtI", "UniquePtJ"):
        connectivity[point_column] = connectivity[point_column].map(
            _normalize_object_name
        )
    _require_columns(
        beam_design,
        {"UniqueName", "Face", "Width", "Depth", "f'c", "fy", "dm"},
        "BEAM DESIGN",
    )

    connection_by_name = connectivity.drop_duplicates("UniqueName").set_index(
        "UniqueName"
    )
    design_by_name = frame_data.drop_duplicates("UniqueName").set_index("UniqueName")
    result_by_name = column_results.drop_duplicates("UniqueName").set_index("UniqueName")
    beam_result_groups = {
        name: rows.copy()
        for name, rows in beam_design.groupby("UniqueName", dropna=True)
    }
    force_groups = {
        _normalize_object_name(name): rows.copy()
        for name, rows in factored_loads.groupby("UniqueName", dropna=True)
    }
    all_combos = sorted(factored_loads["Combo"].dropna().astype(str).unique())

    columns_at_point: dict[str, list[str]] = {}
    beams_at_point: dict[str, list[str]] = {}
    for member, row in connection_by_name.iterrows():
        point_i = _normalize_object_name(row["UniquePtI"])
        point_j = _normalize_object_name(row["UniquePtJ"])
        kind = str(row["DesignType"]).strip().casefold()
        point_map = columns_at_point if kind == "column" else beams_at_point
        if kind not in {"column", "beam"}:
            continue
        point_map.setdefault(point_i, []).append(member)
        point_map.setdefault(point_j, []).append(member)

    column_sections: dict[tuple[str, float], tuple[ColumnFlexureDesign, object]] = {}
    beam_end_strengths: dict[tuple[str, str, float], dict] = {}

    def beam_end_strength(beam: str, end_name: str, fy_multiplier: float) -> dict:
        """Return a beam end's strength, calculated once (it does not depend on the combo)."""
        key = (beam, end_name, fy_multiplier)
        if key not in beam_end_strengths:
            beam_end_strengths[key] = _beam_end_reinforcement(
                beam_result_groups[beam].iloc[0],
                beam_result_groups[beam],
                end_name,
                fy_multiplier,
            )
        return dict(beam_end_strengths[key])

    def frame_data_row(member: str) -> pd.Series:
        """Return the unique properties row for a member or explain what is missing."""
        if member not in design_by_name.index:
            raise ValueError(f"FRAME DATA is missing framing member {member}.")
        return design_by_name.loc[member]

    def beam_geometry_row(member: str) -> pd.Series:
        """Return beam geometry from BEAM DESIGN, falling back to FRAME DATA."""
        if member in beam_result_groups:
            return beam_result_groups[member].iloc[0]
        return frame_data_row(member)

    def joint_column_reinforcement(joint: str, members: list[str]) -> str:
        """Summarize reinforcement for every designed column framing into a joint."""
        summaries = []
        for member in members:
            if member not in result_by_name.index:
                summaries.append(f"{member}: MISSING column design")
                continue
            column_result = result_by_name.loc[member]
            end = get_end_info(member, joint, require_force_data=False)[0]
            summaries.append(
                f"{member} ({end}): "
                f"{int(column_result['Longitudinal_Bars'])} bars "
                f"D{float(column_result['Main_Bar_mm']):g}; "
                f"{column_result['Longitudinal_Bar_Layout']}"
            )
        return "; ".join(summaries) if summaries else "N/A"

    def get_end_info(
        member: str, joint: str, require_force_data: bool = True
    ) -> tuple[str, str, np.ndarray, float]:
        """Return joint end, far endpoint, outward vector, and member length."""
        connection = connection_by_name.loc[member]
        if _normalize_object_name(connection["UniquePtI"]) == joint:
            end, far_point = "I", _normalize_object_name(connection["UniquePtJ"])
        elif _normalize_object_name(connection["UniquePtJ"]) == joint:
            end, far_point = "J", _normalize_object_name(connection["UniquePtI"])
        else:
            raise ValueError(f"Member {member} is not connected to joint {joint}.")
        if joint not in point_coordinates or far_point not in point_coordinates:
            raise ValueError(f"Point coordinates are missing for member {member}.")
        outward = point_coordinates[far_point] - point_coordinates[joint]
        force_data = force_groups.get(member)
        if force_data is None:
            if require_force_data:
                raise ValueError(f"FACTORED LOADS is missing member {member}.")
            length = float(np.linalg.norm(outward))
        else:
            length = float(
                pd.to_numeric(force_data["Station"], errors="coerce").max()
                - pd.to_numeric(force_data["Station"], errors="coerce").min()
            )
        if not math.isfinite(length) or length <= 0:
            length = float(np.linalg.norm(outward))
        return end, far_point, outward, max(length, 1e-6)

    def column_nominal_capacity(
        member: str, joint: str, combo: str, moment_axis: np.ndarray, fy_factor: float = 1.0
    ) -> tuple[float, float]:
        """Return nominal column moment and axial force at a joint for one combo."""
        end, _, _, _ = get_end_info(member, joint)
        row = frame_data_row(member)
        connection = connection_by_name.loc[member]
        point_i = _normalize_object_name(connection["UniquePtI"])
        point_j = _normalize_object_name(connection["UniquePtJ"])
        if member not in frame_angles:
            raise ValueError(f"Local-axis angle is missing for column {member}.")
        _, local_2, local_3 = _frame_local_axes(
            point_coordinates[point_i], point_coordinates[point_j], frame_angles[member]
        )
        m2_direction = float(np.dot(moment_axis, local_2))
        m3_direction = float(np.dot(moment_axis, local_3))
        theta = math.atan2(m3_direction, m2_direction)
        forces = force_groups[member]
        force = _column_force_at_end(forces, combo, at_i_end=end == "I")
        axial = _numeric(force["P"], "P", member) * 1000.0
        # A column keeps one bar layout for the whole evaluation, so its section
        # is built once per strength factor and reused for every joint and combo.
        section_key = (member, fy_factor)
        if section_key not in column_sections:
            bars = int(result_by_name.loc[member]["Longitudinal_Bars"])
            capacity_row = row.copy()
            capacity_row["UniqueName"] = member
            capacity_row["fy"] = _numeric(row["fy"], "fy", member) * fy_factor
            column_sections[section_key] = _build_column_section(
                capacity_row,
                bars,
                dmain,
                dties,
                cover,
                is_smrf,
                bundle_layout=column_layouts.get(member),
            )
        engine, section = column_sections[section_key]
        nominal, _, _, _, _ = engine.solve_moment_capacity(
            section, axial_load=axial, bending_angle=theta
        )
        return float(nominal) / 1e6, axial

    def column_continuity(joint: str, members: list[str]) -> tuple[str, float]:
        """Check adjacent upper/lower column alignment against the 1:6 limit."""
        above: list[tuple[str, str, float]] = []
        below: list[tuple[str, str, float]] = []
        z_joint = point_coordinates[joint][2]
        for member in members:
            _, far_point, _, length = get_end_info(member, joint)
            delta_z = point_coordinates[far_point][2] - z_joint
            if delta_z > 1e-6:
                above.append((member, far_point, length))
            elif delta_z < -1e-6:
                below.append((member, far_point, length))
        if not above or not below:
            return "N/A - terminal column joint", np.nan
        _, upper_far, _ = min(
            above, key=lambda item: abs(point_coordinates[item[1]][2] - z_joint)
        )
        _, lower_far, _ = min(
            below, key=lambda item: abs(point_coordinates[item[1]][2] - z_joint)
        )
        lower_point = point_coordinates[lower_far]
        upper_point = point_coordinates[upper_far]
        vertical_run = abs(upper_point[2] - lower_point[2])
        if vertical_run <= 1e-6:
            return "FAIL - no vertical continuation", math.inf
        lateral_offset = float(np.linalg.norm(upper_point[:2] - lower_point[:2]))
        offset_ratio = lateral_offset / vertical_run
        return (
            "PASS"
            if offset_ratio <= CODE.column_seismic.column_offset_limit
            else "FAIL - offset exceeds 1:6",
            offset_ratio,
        )

    rows: list[dict] = []
    for joint, connected_beams in beams_at_point.items():
        connected_columns = list(dict.fromkeys(columns_at_point.get(joint, [])))
        if not connected_columns:
            continue
        undesigned = [
            column
            for column in connected_columns
            if column not in result_by_name.index
        ]
        if joint not in point_coordinates:
            raise ValueError(f"Point coordinates are missing for joint {joint}.")
        beam_vectors: list[tuple[str, np.ndarray, str, float]] = []
        for beam in dict.fromkeys(connected_beams):
            end, _, outward, _ = get_end_info(
                beam, joint, require_force_data=False
            )
            horizontal = np.array([outward[0], outward[1], 0.0])
            direction = _unit_vector(horizontal, f"plan direction of beam {beam}")
            canonical = direction if (direction[0] > 1e-8 or (
                abs(direction[0]) <= 1e-8 and direction[1] >= 0
            )) else -direction
            beam_vectors.append(
                (
                    beam,
                    canonical,
                    end,
                    1.0 if np.dot(direction, canonical) >= 0 else -1.0,
                )
            )

        direction_groups: list[list[tuple[str, np.ndarray, str, float]]] = []
        for item in beam_vectors:
            for group in direction_groups:
                if abs(float(np.dot(item[1], group[0][1]))) >= 1.0 - 1e-8:
                    group.append(item)
                    break
            else:
                direction_groups.append([item])

        for group in direction_groups:
            representative = group[0][1]
            moment_axis = np.array([-representative[1], representative[0], 0.0])
            members = [item[0] for item in group]
            combo_names = all_combos
            if not combo_names:
                raise ValueError(
                    f"No load combinations are available for beams at joint {joint}."
                )

            transverse_beams = [
                item
                for other_group in direction_groups
                if other_group is not group
                for item in other_group
            ]
            for combo in combo_names:
                missing_column_forces = sorted(
                    column
                    for column in connected_columns
                    if column not in force_groups
                    or not force_groups[column]["Combo"]
                    .astype(str)
                    .eq(str(combo))
                    .any()
                )
                missing_column_angles = sorted(
                    column
                    for column in connected_columns
                    if column not in frame_angles
                )
                missing_beams = sorted(
                    beam
                    for beam in members
                    if beam not in beam_result_groups or beam not in force_groups
                )
                if (
                    missing_beams
                    or undesigned
                    or missing_column_forces
                    or missing_column_angles
                    or not connected_columns
                ):
                    missing_parts = []
                    if missing_beams:
                        missing_parts.append(
                            "beam input missing: " + ", ".join(missing_beams)
                        )
                    if undesigned:
                        missing_parts.append(
                            "column design missing: " + ", ".join(undesigned)
                        )
                    if missing_column_forces:
                        missing_parts.append(
                            "column forces missing: "
                            + ", ".join(missing_column_forces)
                        )
                    if missing_column_angles:
                        missing_parts.append(
                            "column local-axis angle missing: "
                            + ", ".join(missing_column_angles)
                        )
                    if not connected_columns:
                        missing_parts.append("no column connected at joint")
                    missing_reason = "; ".join(missing_parts)
                    blocked_reason = (
                        f"BLOCKED at joint {joint}, combo {combo}: "
                        f"{missing_reason}"
                    )
                    for sway_direction in (-1.0, 1.0):
                        rows.append(
                            {
                                "Joint_Point": joint,
                                "Load_Combo": combo,
                                "Frame_Direction_deg": math.degrees(
                                    math.atan2(
                                        representative[1], representative[0]
                                    )
                                )
                                % 180.0,
                                "Sway_Direction": (
                                    "Positive"
                                    if sway_direction > 0
                                    else "Negative"
                                ),
                                "Framing_Beams": ", ".join(members),
                                "Framing_Beam_Reinforcement": (
                                    "MISSING: " + missing_reason
                                ),
                                "Columns_At_Joint": ", ".join(connected_columns),
                                "Column_Reinforcement_At_Joint": (
                                    joint_column_reinforcement(
                                        joint, connected_columns
                                    )
                                ),
                                "Column_End_Members": ", ".join(
                                    f"{column}:{get_end_info(column, joint, False)[0]}"
                                    for column in connected_columns
                                    if column in connection_by_name.index
                                ),
                                "Strong_Column_Check": blocked_reason,
                                "Joint_Shear_Check": blocked_reason,
                                "Joint_Check_Reason": blocked_reason,
                            }
                        )
                    continue

                beam_strengths = {}
                for beam, _, end, side_sign in group:
                    if beam not in beam_result_groups:
                        raise ValueError(
                            f"Framing beam {beam} at joint {joint} is missing from "
                            "BEAM DESIGN; include and design it before column design."
                        )
                    beam_strengths[beam] = beam_end_strength(beam, end, 1.0)
                    beam_connection = connection_by_name.loc[beam]
                    beam_i = _normalize_object_name(beam_connection["UniquePtI"])
                    beam_j = _normalize_object_name(beam_connection["UniquePtJ"])
                    # The queried ETABS Local Axes table is column-only; horizontal
                    # beams therefore use the explicit default beta=0 basis.
                    local_1, _, local_3 = _frame_local_axes(
                        point_coordinates[beam_i],
                        point_coordinates[beam_j],
                        frame_angles.get(beam, 0.0),
                    )
                    beam_strengths[beam]["Moment_Projection"] = abs(
                        float(np.dot(local_3, moment_axis))
                    )
                    beam_strengths[beam]["Force_Projection"] = abs(
                        float(np.dot(local_1, representative))
                    )

                column_capacity = 0.0
                # Half-height sum of the connected columns (inflection at mid-height).
                column_shear_denominator = 0.0
                axial_values = []
                column_reinforcement_summary = []
                for column in connected_columns:
                    column_result = result_by_name.loc[column]
                    if progress is not None:
                        progress(
                            column,
                            get_end_info(column, joint, False)[0],
                            "Strong column - weak beam and joint shear",
                            combo,
                        )
                    capacity, axial = column_nominal_capacity(
                        column, joint, combo, moment_axis
                    )
                    column_capacity += capacity
                    axial_values.append(axial / 1000.0)
                    _, _, _, length = get_end_info(column, joint)
                    column_shear_denominator += 0.5 * length
                    column_end = get_end_info(column, joint, False)[0]
                    column_reinforcement_summary.append(
                        f"{column} ({column_end}): "
                        f"{int(column_result['Longitudinal_Bars'])} bars "
                        f"D{float(column_result['Main_Bar_mm']):g}; "
                        f"{column_result['Longitudinal_Bar_Layout']}"
                    )

                for sway_direction in (-1.0, 1.0):
                    beam_nominal = 0.0
                    beam_tension = 0.0
                    beam_probable_sum = 0.0
                    for beam, _, _, side_sign in group:
                        beam_info = beam_strengths[beam]
                        top_controls = side_sign * sway_direction > 0
                        beam_nominal += beam_info[
                            "Mn_top_kNm" if top_controls else "Mn_bottom_kNm"
                        ] * beam_info["Moment_Projection"]
                        probable = beam_end_strength(
                            beam,
                            get_end_info(beam, joint)[0],
                            CODE.column_seismic.probable_stress_factor,
                        )
                        beam_tension += probable[
                            "T_top_kN" if top_controls else "T_bottom_kN"
                        ] * beam_info["Force_Projection"]
                        beam_probable_sum += probable[
                            "Mn_top_kNm" if top_controls else "Mn_bottom_kNm"
                        ] * beam_info["Moment_Projection"]

                    # ACI 18.8.2.1 / R18.8.2: the column shear in the joint free body is
                    # the joint moment divided by the mid-height-to-mid-height distance.
                    # The joint moment is what the BEAMS deliver at their probable
                    # strength (sum of Mpr), not the capacity of the columns.
                    column_shear = (
                        beam_probable_sum * 1000.0 / column_shear_denominator
                        if column_shear_denominator > 0
                        else 0.0
                    )

                    ratio = (
                        column_capacity / beam_nominal
                        if beam_nominal > 0
                        else math.inf
                    )
                    required_ratio = CODE.column_seismic.strong_column_ratio
                    b_c_check = "PASS" if ratio >= required_ratio else "FAIL"

                    # Determine transverse-beam confinement and effective joint area.
                    section_row = frame_data_row(connected_columns[0])
                    is_circle = pd.notna(section_row.get("Diameter")) and float(
                        section_row.get("Diameter")
                    ) > 0
                    col_w = (
                        float(section_row["Diameter"])
                        if is_circle
                        else _numeric(section_row["Width"], "Width", joint)
                    )
                    col_d = (
                        float(section_row["Diameter"])
                        if is_circle
                        else _numeric(section_row["Depth"], "Depth", joint)
                    )
                    if is_circle:
                        joint_depth = joint_width = math.sqrt(col_w * col_d)
                    else:
                        column = connected_columns[0]
                        connection = connection_by_name.loc[column]
                        point_i = _normalize_object_name(connection["UniquePtI"])
                        point_j = _normalize_object_name(connection["UniquePtJ"])
                        if column not in frame_angles:
                            raise ValueError(
                                f"Local-axis angle is missing for column {column}."
                            )
                        _, column_axis_2, column_axis_3 = _frame_local_axes(
                            point_coordinates[point_i],
                            point_coordinates[point_j],
                            frame_angles[column],
                        )
                        joint_depth = (
                            abs(float(np.dot(representative, column_axis_2))) * col_w
                            + abs(float(np.dot(representative, column_axis_3))) * col_d
                        )
                        joint_width = (
                            abs(float(np.dot(moment_axis, column_axis_2))) * col_w
                            + abs(float(np.dot(moment_axis, column_axis_3))) * col_d
                        )
                    largest_beam_width = max(
                        beam_strengths[beam]["Beam_Width_mm"] for beam in members
                    )
                    effective_joint_width = min(
                        joint_width, largest_beam_width + joint_depth
                    )
                    aj = joint_depth * effective_joint_width
                    adequate_transverse_sides: set[int] = set()
                    missing_transverse_geometry = [
                        beam
                        for beam, _, _, _ in transverse_beams
                        if beam not in beam_result_groups
                        and beam not in design_by_name.index
                    ]
                    for beam, direction, _, _ in transverse_beams:
                        if beam in missing_transverse_geometry:
                            continue
                        beam_row = beam_geometry_row(beam)
                        beam_width = _numeric(beam_row["Width"], "Width", beam)
                        beam_depth = _numeric(
                            beam_row.get("Depth", beam_row.get("Height")),
                            "Depth",
                            beam,
                        )
                        width_ok = (
                            beam_width
                            >= CODE.column_seismic.transverse_beam_width_fraction
                            * effective_joint_width
                        )
                        _, _, _, extension_length = get_end_info(
                            beam, joint, require_force_data=False
                        )
                        extension_ok = extension_length >= (
                            0.5 * joint_depth + beam_depth
                        )
                        if width_ok and extension_ok:
                            _, _, outward, _ = get_end_info(
                                beam, joint, require_force_data=False
                            )
                            side_sign = (
                                1
                                if np.dot(outward[:2], direction[:2]) >= 0
                                else -1
                            )
                            adequate_transverse_sides.add(side_sign)
                    in_plane_sides = {
                        1 if item[3] > 0 else -1 for item in group
                    }
                    in_plane_faces = min(len(in_plane_sides), 2)
                    face_count = in_plane_faces + min(
                        len(adequate_transverse_sides), 2
                    )
                    joint_cfg = CODE.column_seismic
                    coefficient = (
                        joint_cfg.joint_shear_coeff_4_faces
                        if face_count >= 4
                        else (
                            joint_cfg.joint_shear_coeff_3_faces
                            if face_count >= 3 or in_plane_faces == 2
                            else joint_cfg.joint_shear_coeff_other
                        )
                    )
                    fc = _numeric(section_row["f'c"], "f'c", joint)
                    phi_vn_kN = (
                        CODE.strength.joint_shear * coefficient * math.sqrt(fc) * aj / 1000.0
                    )
                    joint_shear_demand = abs(beam_tension - column_shear)
                    shear_ratio = (
                        joint_shear_demand / phi_vn_kN
                        if phi_vn_kN > 0
                        else math.inf
                    )
                    joint_shear_check = (
                        "PASS" if joint_shear_demand <= phi_vn_kN else "FAIL"
                    )
                    if missing_transverse_geometry:
                        joint_shear_check = (
                            "BLOCKED: transverse beam geometry missing for "
                            + ", ".join(missing_transverse_geometry)
                        )
                    continuity_status, column_offset_ratio = column_continuity(
                        joint, connected_columns
                    )
                    beam_reinforcement_summary = "; ".join(
                        (
                            f"{beam} ({get_end_info(beam, joint)[0]}): "
                            f"top {beam_strengths[beam]['n_top']}D"
                            f"{beam_strengths[beam]['Largest_Beam_Bar_mm']:g}, "
                            f"bottom {beam_strengths[beam]['n_bottom']}D"
                            f"{beam_strengths[beam]['Largest_Beam_Bar_mm']:g}"
                        )
                        for beam in members
                    )

                    rows.append(
                        {
                            "Joint_Point": joint,
                            "Load_Combo": combo,
                            "Frame_Direction_deg": math.degrees(
                                math.atan2(representative[1], representative[0])
                            )
                            % 180.0,
                            "Sway_Direction": "Positive" if sway_direction > 0 else "Negative",
                            "Framing_Beams": ", ".join(members),
                            "Framing_Beam_Reinforcement": beam_reinforcement_summary,
                            "Beam_Local_Axis_Assumption": (
                                "beta=0 deg; beam angle absent from column-only ETABS table"
                                if any(beam not in frame_angles for beam in members)
                                else "ETABS beta angle"
                            ),
                            "Columns_At_Joint": ", ".join(connected_columns),
                            "Column_Reinforcement_At_Joint": "; ".join(
                                column_reinforcement_summary
                            ),
                            "Column_End_Members": ", ".join(
                                f"{column}:{get_end_info(column, joint, False)[0]}"
                                for column in connected_columns
                            ),
                            "Column_Axial_Loads_kN": ", ".join(
                                f"{value:.2f}" for value in axial_values
                            ),
                            "Sum_Column_Mn_kNm": column_capacity,
                            "Sum_Beam_Mn_kNm": beam_nominal,
                            "Column_Beam_Ratio": ratio,
                            "Required_Ratio": required_ratio,
                            "Strong_Column_Check": b_c_check,
                            "Beam_Probable_Mn_kNm": beam_probable_sum,
                            "Beam_Tension_Force_kN": beam_tension,
                            "Capacity_Based_Column_Shear_kN": column_shear,
                            "Joint_Shear_Demand_kN": joint_shear_demand,
                            "Joint_Area_mm2": aj,
                            "Joint_Confined_Faces": face_count,
                            "Column_Continuity_Check": continuity_status,
                            "Column_Offset_Ratio": column_offset_ratio,
                            "Nominal_Joint_Shear_Coefficient": coefficient,
                            "phi_Vn_kN": phi_vn_kN,
                            "Joint_Shear_Utilization": shear_ratio,
                            "Joint_Shear_Check": joint_shear_check,
                            "Slab_Steel_Included": False,
                            "Joint_Depth_Check": (
                                "PASS"
                                if all(
                                    beam_strengths[beam]["Beam_Depth_mm"]
                                    <= CODE.column_seismic.joint_beam_depth_limit
                                    * joint_depth
                                    for beam in members
                                )
                                else "FAIL: ACI 18.8.2.4"
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def _build_consolidated_column_report(
    column_results: pd.DataFrame,
    load_checks: pd.DataFrame,
    shear_checks: pd.DataFrame,
    joint_results: pd.DataFrame,
    column_labels: dict[str, str] | None = None,
    level_elevations: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, list[tuple[str, list[str]]]]:
    """Combine column, force, shear, and joint checks into one I/J report."""
    def summarize_status(values: list[str]) -> str:
        """Apply conservative PASS/FAIL/BLOCKED precedence to paired joint checks."""
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        blocked = [value for value in normalized if value.startswith("BLOCKED")]
        if blocked:
            return " / ".join(blocked)
        if any(value.startswith("FAIL") for value in normalized):
            return "FAIL"
        if normalized and all(value.startswith("PASS") for value in normalized):
            return "PASS"
        return " / ".join(normalized) if normalized else "N/A"

    groups: list[tuple[str, list[str]]] = [
        (
            "COLUMN IDENTIFICATION",
            ["Column_Label", "UniqueName", "Story", "Level_Elevation_m", "Combo"],
        ),
        (
            "SECTION / REINFORCEMENT",
            [
                "Section",
                "Shape",
                "Width_mm",
                "Depth_mm",
                "Diameter_mm",
                "f'c_MPa",
                "fy_MPa",
                "fyt_MPa",
                "Longitudinal_Bars",
                "Bundle_Layout",
                "Bar_Layout_Data",
                "Reinforcement_Ratio",
                "Reinforcement_Ratio_Limit",
            ],
        ),
    ]
    for end in ("I", "J"):
        groups.append(
            (
                f"FLEXURE / AXIAL - {end}",
                [
                    f"Pu_kN_{end}",
                    f"Mu2_kNm_{end}",
                    f"Mu3_kNm_{end}",
                    f"phi_Mn_kNm_{end}",
                    f"Flexure_Utilization_{end}",
                    f"Axial_Check_{end}",
                    f"Flexure_Check_{end}",
                ],
            )
        )
    for end in ("I", "J"):
        groups.append(
            (
                f"SHEAR - {end}",
                [
                    f"Analysis_Vu_kN_{end}",
                    f"Probable_Ve_kN_{end}",
                    f"Design_Vu_kN_{end}",
                    f"Vc_kN_{end}",
                    f"Concrete_Shear_Neglected_{end}",
                    f"phi_Vn_kN_{end}",
                    f"Shear_Utilization_{end}",
                    f"Shear_Check_{end}",
                ],
            )
        )
    for end in ("I", "J"):
        groups.append(
            (
                f"BEAM-COLUMN CAPACITY - {end}",
                [
                    f"Beam_Reinforcement_{end}",
                    f"Column_Reinforcement_At_Joint_{end}",
                    f"BCC_Ratio_{end}",
                    f"BCC_Status_{end}",
                ],
            )
        )
    for end in ("I", "J"):
        groups.append(
            (
                f"JOINT SHEAR - {end}",
                [
                    f"Joint_Shear_Demand_kN_{end}",
                    f"Joint_Shear_Capacity_kN_{end}",
                    f"Joint_Shear_Utilization_{end}",
                    f"Joint_Shear_Status_{end}",
                ],
            )
        )
    groups.append(
        (
            "TRANSVERSE DETAILING",
            [
                "Transverse_Type",
                "Transverse_Provision_Summary",
                "Tie_Bar_mm",
                "Transverse_Spacing_mm",
                "Transverse_Legs_X",
                "Transverse_Legs_Y",
                "Confinement_Criteria_Governing",
                "High_Axial_or_High_fc_Check",
                "Confinement_18_7_5_a",
                "Confinement_18_7_5_b",
                "Confinement_18_7_5_c",
                "Confinement_18_7_5_d",
                "Confinement_18_7_5_e",
                "Confinement_18_7_5_f",
                "Kf",
                "Kn",
                "Required_Ash_s_Ratio_X",
                "Provided_Ash_s_Ratio_X",
                "Required_Ash_s_Ratio_Y",
                "Provided_Ash_s_Ratio_Y",
                "Confinement_Check",
                "Transverse_Spacing_Check",
                "Alternating_Support_Check",
                "Alternating_Support_Added_Legs",
                "Tie_Diameter_Check",
                "SMRF_Dimension_Check",
                "Transverse_Reinforcement_Check",
                "Column_Design_Status",
            ],
        )
    )

    force_map = {
        (str(row["UniqueName"]), str(row["Combo"]), str(row["End"])): row
        for _, row in load_checks.iterrows()
    }
    shear_groups = {
        (str(member), str(combo), str(end)): rows
        for (member, combo, end), rows in shear_checks.groupby(
            ["UniqueName", "Combo", "End"], dropna=False
        )
    } if not shear_checks.empty else {}
    joint_groups: dict[tuple[str, str, str], list[pd.Series]] = {}
    if not joint_results.empty:
        for _, joint_row in joint_results.iterrows():
            combo = str(joint_row.get("Load_Combo", ""))
            for entry in str(joint_row.get("Column_End_Members", "")).split(","):
                if ":" not in entry:
                    continue
                member, end = (part.strip() for part in entry.rsplit(":", 1))
                joint_groups.setdefault((member, combo, end), []).append(joint_row)

    report_rows: list[dict] = []
    for _, column in column_results.iterrows():
        member = str(column["UniqueName"])
        combos = sorted(
            {
                combo
                for (force_member, combo, _end) in force_map
                if force_member == member
            }
        )
        if not combos:
            combos = [""]
        for combo in combos:
            report_row = {
                "UniqueName": member,
                "Column_Label": (column_labels or {}).get(member, member),
                "Story": column.get("Story"),
                "Level_Elevation_m": (level_elevations or {}).get(member, np.nan),
                "Combo": combo,
                "Section": column.get("SectProp"),
                "Shape": column.get("Shape"),
                "Width_mm": column.get("Width_mm"),
                "Depth_mm": column.get("Depth_mm"),
                "Diameter_mm": column.get("Diameter_mm"),
                "f'c_MPa": column.get("f'c_MPa"),
                "fy_MPa": column.get("fy_MPa"),
                "fyt_MPa": column.get("fyt_MPa"),
                "Longitudinal_Bars": column.get("Longitudinal_Bars"),
                "Bundle_Layout": column.get("Longitudinal_Bar_Layout"),
                "Bar_Layout_Data": column.get("Longitudinal_Bar_Coordinates"),
                "Reinforcement_Ratio": column.get("Reinforcement_Ratio"),
                "Reinforcement_Ratio_Limit": column.get(
                    "Reinforcement_Ratio_Limit"
                ),
                "Transverse_Type": column.get("Transverse_Type"),
                "Transverse_Provision_Summary": column.get(
                    "Transverse_Provision_Summary"
                ),
                "Tie_Bar_mm": column.get("Tie_Bar_mm"),
                "Transverse_Spacing_mm": column.get(
                    "Transverse_Spacing_Provided_mm"
                ),
                "Transverse_Legs_X": column.get("Transverse_Legs_X"),
                "Transverse_Legs_Y": column.get("Transverse_Legs_Y"),
                "Confinement_Criteria_Governing": column.get(
                    "Confinement_Criteria_Governing"
                ),
                "High_Axial_or_High_fc_Check": column.get(
                    "High_Axial_or_High_fc_Check"
                ),
                "Confinement_18_7_5_a": column.get(
                    "Confinement_18_7_5_a"
                ),
                "Confinement_18_7_5_b": column.get(
                    "Confinement_18_7_5_b"
                ),
                "Confinement_18_7_5_c": column.get(
                    "Confinement_18_7_5_c"
                ),
                "Confinement_18_7_5_d": column.get(
                    "Confinement_18_7_5_d"
                ),
                "Confinement_18_7_5_e": column.get(
                    "Confinement_18_7_5_e"
                ),
                "Confinement_18_7_5_f": column.get(
                    "Confinement_18_7_5_f"
                ),
                "Kf": column.get("Kf"),
                "Kn": column.get("Kn"),
                "Required_Ash_s_Ratio_X": column.get(
                    "Required_Ash_s_Ratio_X"
                ),
                "Provided_Ash_s_Ratio_X": column.get(
                    "Provided_Ash_s_Ratio_X"
                ),
                "Required_Ash_s_Ratio_Y": column.get(
                    "Required_Ash_s_Ratio_Y"
                ),
                "Provided_Ash_s_Ratio_Y": column.get(
                    "Provided_Ash_s_Ratio_Y"
                ),
                "Confinement_Check": column.get("Confinement_Check"),
                "Transverse_Spacing_Check": column.get(
                    "Transverse_Spacing_Check"
                ),
                "Alternating_Support_Check": column.get(
                    "Alternating_Support_Check"
                ),
                "Alternating_Support_Added_Legs": column.get(
                    "Alternating_Support_Added_Legs"
                ),
                "Tie_Diameter_Check": column.get("Tie_Diameter_Check"),
                "SMRF_Dimension_Check": column.get("SMRF_Dimension_Check"),
                "Transverse_Reinforcement_Check": column.get(
                    "Transverse_Reinforcement_Check"
                ),
                "Column_Design_Status": column.get("Design_Status"),
            }
            for end in ("I", "J"):
                force = force_map.get((member, combo, end))
                for key, source in (
                    (f"Pu_kN_{end}", "Pu_kN"),
                    (f"Mu2_kNm_{end}", "Mu2_kNm"),
                    (f"Mu3_kNm_{end}", "Mu3_kNm"),
                    (f"phi_Mn_kNm_{end}", "phi_Mn_kNm"),
                    (f"Flexure_Utilization_{end}", "Flexure_Utilization"),
                    (f"Axial_Check_{end}", "Axial_Check"),
                    (f"Flexure_Check_{end}", "Flexure_Check"),
                ):
                    report_row[key] = force.get(source) if force is not None else "N/A"

                shear_rows = shear_groups.get((member, combo, end))
                if shear_rows is None:
                    for prefix in (
                        "Analysis_Vu_kN",
                        "Probable_Ve_kN",
                        "Design_Vu_kN",
                        "Vc_kN",
                        "Concrete_Shear_Neglected",
                        "phi_Vn_kN",
                        "Shear_Utilization",
                        "Shear_Check",
                    ):
                        report_row[f"{prefix}_{end}"] = "N/A"
                else:
                    report_row[f"Analysis_Vu_kN_{end}"] = shear_rows[
                        "Analysis_Shear_kN"
                    ].max()
                    report_row[f"Probable_Ve_kN_{end}"] = shear_rows[
                        "Capacity_Based_Ve_kN"
                    ].max()
                    report_row[f"Design_Vu_kN_{end}"] = shear_rows[
                        "Design_Shear_kN"
                    ].max()
                    report_row[f"Vc_kN_{end}"] = shear_rows["Vc_kN"].min()
                    report_row[f"Concrete_Shear_Neglected_{end}"] = (
                        "YES"
                        if shear_rows[
                            "Concrete_Shear_Strength_Neglected"
                        ].eq(True).all()
                        else "NO"
                    )
                    report_row[f"phi_Vn_kN_{end}"] = shear_rows[
                        "phi_Vn_kN"
                    ].min()
                    report_row[f"Shear_Utilization_{end}"] = shear_rows[
                        "Shear_Utilization"
                    ].max()
                    report_row[f"Shear_Check_{end}"] = (
                        "PASS"
                        if shear_rows["Shear_Check"].eq("PASS").all()
                        else "FAIL"
                    )

                joint_rows = joint_groups.get((member, combo, end), [])
                if not joint_rows:
                    for prefix in (
                        "Beam_Reinforcement",
                        "Column_Reinforcement_At_Joint",
                        "BCC_Ratio",
                        "BCC_Status",
                        "Joint_Shear_Demand_kN",
                        "Joint_Shear_Capacity_kN",
                        "Joint_Shear_Utilization",
                        "Joint_Shear_Status",
                    ):
                        report_row[f"{prefix}_{end}"] = "N/A"
                else:
                    beam_summaries = list(
                        dict.fromkeys(
                            str(item.get("Framing_Beam_Reinforcement", ""))
                            for item in joint_rows
                            if pd.notna(item.get("Framing_Beam_Reinforcement"))
                        )
                    )
                    report_row[f"Beam_Reinforcement_{end}"] = " | ".join(
                        beam_summaries
                    )
                    report_row[f"Column_Reinforcement_At_Joint_{end}"] = " | ".join(
                        list(
                            dict.fromkeys(
                                str(item.get("Column_Reinforcement_At_Joint", ""))
                                for item in joint_rows
                                if pd.notna(
                                    item.get("Column_Reinforcement_At_Joint")
                                )
                            )
                        )
                    )
                    bcc_values = [
                        pd.to_numeric(
                            pd.Series([item.get("Column_Beam_Ratio")]),
                            errors="coerce",
                        ).iloc[0]
                        for item in joint_rows
                    ]
                    finite_bcc = [value for value in bcc_values if pd.notna(value)]
                    report_row[f"BCC_Ratio_{end}"] = (
                        min(finite_bcc) if finite_bcc else "N/A"
                    )
                    report_row[f"BCC_Status_{end}"] = summarize_status(
                        [
                            str(item.get("Strong_Column_Check", "N/A"))
                            for item in joint_rows
                        ]
                    )
                    demand_values = [
                        pd.to_numeric(
                            pd.Series([item.get("Joint_Shear_Demand_kN")]),
                            errors="coerce",
                        ).iloc[0]
                        for item in joint_rows
                    ]
                    capacity_values = [
                        pd.to_numeric(
                            pd.Series([item.get("phi_Vn_kN")]), errors="coerce"
                        ).iloc[0]
                        for item in joint_rows
                    ]
                    ratio_values = [
                        pd.to_numeric(
                            pd.Series([item.get("Joint_Shear_Utilization")]),
                            errors="coerce",
                        ).iloc[0]
                        for item in joint_rows
                    ]
                    report_row[f"Joint_Shear_Demand_kN_{end}"] = (
                        max(value for value in demand_values if pd.notna(value))
                        if any(pd.notna(value) for value in demand_values)
                        else "N/A"
                    )
                    report_row[f"Joint_Shear_Capacity_kN_{end}"] = (
                        min(value for value in capacity_values if pd.notna(value))
                        if any(pd.notna(value) for value in capacity_values)
                        else "N/A"
                    )
                    report_row[f"Joint_Shear_Utilization_{end}"] = (
                        max(value for value in ratio_values if pd.notna(value))
                        if any(pd.notna(value) for value in ratio_values)
                        else "N/A"
                    )
                    report_row[f"Joint_Shear_Status_{end}"] = summarize_status(
                        [
                            str(item.get("Joint_Shear_Check", "N/A"))
                            for item in joint_rows
                        ]
                    )
            report_rows.append(report_row)

    report = pd.DataFrame(
        report_rows,
        columns=[column for _, column_names in groups for column in column_names],
    )
    report["_story_sort"] = report["Story"].astype(str)
    report = (
        report.sort_values(
            ["Column_Label", "Level_Elevation_m", "_story_sort", "UniqueName", "Combo"],
            ascending=[True, False, True, True, True],
            kind="stable",
        )
        .drop(columns="_story_sort")
        .round(2)
        .reset_index(drop=True)
    )
    return _expand_column_report_hierarchy(report)


def _expand_column_report_hierarchy(
    wide_report: pd.DataFrame,
) -> tuple[pd.DataFrame, list[tuple[str, list[str]]]]:
    """Expand I/J columns into hierarchical label, member, end, and combo rows."""
    shared_columns = [
        "Section",
        "Shape",
        "Width_mm",
        "Depth_mm",
        "Diameter_mm",
        "f'c_MPa",
        "fy_MPa",
        "fyt_MPa",
        "Longitudinal_Bars",
        "Bundle_Layout",
        "Bar_Layout_Data",
        "Reinforcement_Ratio",
        "Reinforcement_Ratio_Limit",
    ]
    end_groups = {
        "FLEXURE / AXIAL": [
            "Pu_kN",
            "Mu2_kNm",
            "Mu3_kNm",
            "phi_Mn_kNm",
            "Flexure_Utilization",
            "Axial_Check",
            "Flexure_Check",
        ],
        "COLUMN SHEAR": [
            "Analysis_Vu_kN",
            "Probable_Ve_kN",
            "Design_Vu_kN",
            "Vc_kN",
            "Concrete_Shear_Neglected",
            "phi_Vn_kN",
            "Shear_Utilization",
            "Shear_Check",
        ],
        "BEAM-COLUMN CAPACITY": [
            "Column_Reinforcement_At_Joint",
            "Beam_Reinforcement",
            "BCC_Ratio",
            "BCC_Status",
        ],
        "JOINT SHEAR": [
            "Joint_Shear_Demand_kN",
            "Joint_Shear_Capacity_kN",
            "Joint_Shear_Utilization",
            "Joint_Shear_Status",
        ],
    }
    detailing_columns = [
        "Transverse_Type",
        "Transverse_Provision_Summary",
        "Tie_Bar_mm",
        "Transverse_Spacing_mm",
        "Transverse_Legs_X",
        "Transverse_Legs_Y",
        "Confinement_Criteria_Governing",
        "High_Axial_or_High_fc_Check",
        "Confinement_18_7_5_a",
        "Confinement_18_7_5_b",
        "Confinement_18_7_5_c",
        "Confinement_18_7_5_d",
        "Confinement_18_7_5_e",
        "Confinement_18_7_5_f",
        "Kf",
        "Kn",
        "Required_Ash_s_Ratio_X",
        "Provided_Ash_s_Ratio_X",
        "Required_Ash_s_Ratio_Y",
        "Provided_Ash_s_Ratio_Y",
        "Confinement_Check",
        "Transverse_Spacing_Check",
        "Alternating_Support_Check",
        "Alternating_Support_Added_Legs",
        "Tie_Diameter_Check",
        "SMRF_Dimension_Check",
        "Transverse_Reinforcement_Check",
    ]
    identity_columns = [
        "Column_Label",
        "UniqueName",
        "Story",
        "End",
        "Combo",
    ]
    report_groups: list[tuple[str, list[str]]] = [
        ("COLUMN LABEL / LEVEL", identity_columns),
        ("SECTION / LONGITUDINAL REINFORCEMENT", shared_columns),
    ]
    for group_name, names in end_groups.items():
        report_groups.append((group_name, names))
    report_groups.extend(
        [
            ("TRANSVERSE REINFORCEMENT DETAILING", detailing_columns),
            ("DESIGN STATUS", ["Column_Design_Status", "Design_Status_Reason"]),
        ]
    )

    long_rows: list[dict] = []
    for _, wide_row in wide_report.iterrows():
        for end in ("I", "J"):
            result = {
                key: wide_row.get(key)
                for key in ("Column_Label", "UniqueName", "Story", "Combo")
            }
            result["End"] = end
            result.update(
                {key: wide_row.get(key) for key in shared_columns + detailing_columns}
            )
            for names in end_groups.values():
                for name in names:
                    result[name] = wide_row.get(f"{name}_{end}")
            statuses = [
                str(result.get(name, ""))
                for name in (
                    "Flexure_Check",
                    "Axial_Check",
                    "Shear_Check",
                    "BCC_Status",
                    "Joint_Shear_Status",
                    "Confinement_Check",
                    "Transverse_Spacing_Check",
                    "Alternating_Support_Check",
                    "Tie_Diameter_Check",
                    "SMRF_Dimension_Check",
                    "Transverse_Reinforcement_Check",
                )
            ]
            statuses.append(
                str(wide_row.get("Column_Design_Status", "")).strip()
            )
            statuses = [status.strip() for status in statuses if status.strip()]
            normalized_statuses = [status.upper() for status in statuses]
            blocked_reasons = list(
                dict.fromkeys(
                    status
                    for status, normalized in zip(statuses, normalized_statuses)
                    if normalized.startswith("BLOCKED")
                )
            )
            if any(status.startswith("FAIL") for status in normalized_statuses):
                result["Column_Design_Status"] = "FAIL"
                result["Design_Status_Reason"] = "; ".join(
                    status
                    for status, normalized in zip(statuses, normalized_statuses)
                    if normalized.startswith("FAIL")
                )
            elif any(status.startswith("ERROR") for status in normalized_statuses):
                result["Column_Design_Status"] = "ERROR"
                result["Design_Status_Reason"] = "; ".join(
                    status
                    for status, normalized in zip(statuses, normalized_statuses)
                    if normalized.startswith("ERROR")
                )
            elif any(status.startswith("BLOCKED") for status in normalized_statuses):
                result["Column_Design_Status"] = "BLOCKED"
                result["Design_Status_Reason"] = "; ".join(blocked_reasons)
            else:
                result["Column_Design_Status"] = "PASS"
                result["Design_Status_Reason"] = "All applicable checks passed."
            long_rows.append(result)

    long_report = pd.DataFrame(
        long_rows,
        columns=[
            column
            for _, column_names in report_groups
            for column in column_names
        ],
    )
    long_report["_end_order"] = long_report["End"].map({"I": 0, "J": 1})
    long_report["_elevation"] = [
        float(wide_report.loc[index // 2, "Level_Elevation_m"])
        if pd.notna(wide_report.loc[index // 2, "Level_Elevation_m"])
        else -math.inf
        for index in range(len(long_report))
    ]
    long_report = (
        long_report.assign(
            _label_sort=long_report["Column_Label"].astype(str),
            _story_sort=long_report["Story"].astype(str),
            _member_sort=long_report["UniqueName"].astype(str),
        )
        .sort_values(
            [
                "_label_sort",
                "_elevation",
                "_story_sort",
                "_member_sort",
                "_end_order",
                "Combo",
            ],
            ascending=[True, False, True, True, True, True],
            kind="stable",
        )
        .drop(columns=["_end_order", "_elevation", "_label_sort", "_story_sort", "_member_sort"])
        .round(2)
        .reset_index(drop=True)
    )
    return long_report, report_groups


def _write_consolidated_column_report(
    sheet, report: pd.DataFrame, groups: list[tuple[str, list[str]]]
) -> None:
    """Write the report values in one range transfer, then apply worksheet formatting."""
    display_names = {
        "Column_Label": "Column Label",
        "UniqueName": "Unique Name",
        "Story": "Story",
        "End": "End",
        "Combo": "Load Combination",
        "Section": "Section",
        "Shape": "Shape",
        "Width_mm": "Width (mm)",
        "Depth_mm": "Depth (mm)",
        "Diameter_mm": "Diameter (mm)",
        "f'c_MPa": "f′c (MPa)",
        "fy_MPa": "fᵧ (MPa)",
        "fyt_MPa": "fᵧₜ (MPa)",
        "Longitudinal_Bars": "Longitudinal Bars",
        "Bundle_Layout": "Bundle Layout",
        "Bar_Layout_Data": "Bar Layout Data (x, y, n)",
        "Reinforcement_Ratio": "ρ Longitudinal",
        "Reinforcement_Ratio_Limit": "ρ Limit",
        "Pu_kN": "Pᵤ (kN)",
        "Mu2_kNm": "Mᵤ₂ (kN·m)",
        "Mu3_kNm": "Mᵤ₃ (kN·m)",
        "phi_Mn_kNm": "ϕMₙ (kN·m)",
        "Flexure_Utilization": "Flexure Utilization",
        "Axial_Check": "Axial Check",
        "Flexure_Check": "Flexure Check",
        "Analysis_Vu_kN": "Analysis Vᵤ (kN)",
        "Probable_Ve_kN": "Probable Vₑ (kN)",
        "Design_Vu_kN": "Design Vᵤ (kN)",
        "Vc_kN": "V꜀ (kN)",
        "Concrete_Shear_Neglected": "Concrete Shear Neglected",
        "phi_Vn_kN": "ϕVₙ (kN)",
        "Shear_Utilization": "Shear Utilization",
        "Shear_Check": "Shear Check",
        "Column_Reinforcement_At_Joint": "Column Bars Contributing at Joint",
        "Beam_Reinforcement": "Beam Bars at Joint",
        "BCC_Ratio": "ΣMₙ,Column / ΣMₙ,Beam",
        "BCC_Status": "BCC Check",
        "Joint_Shear_Demand_kN": "Joint Shear Demand (kN)",
        "Joint_Shear_Capacity_kN": "ϕ Joint Shear Capacity (kN)",
        "Joint_Shear_Utilization": "Joint Shear Utilization",
        "Joint_Shear_Status": "Joint Shear Check",
        "Transverse_Type": "Hoop / Spiral Type",
        "Transverse_Provision_Summary": "Transverse Reinforcement Provision",
        "Tie_Bar_mm": "Tie Bar Diameter (mm)",
        "Transverse_Spacing_mm": "Tie / Spiral Spacing (mm)",
        "Transverse_Legs_X": "Hoop Legs X",
        "Transverse_Legs_Y": "Hoop Legs Y",
        "Confinement_Criteria_Governing": "Governing ACI 18.7.5 Expression",
        "High_Axial_or_High_fc_Check": "High Pᵤ / f′c Condition",
        "Confinement_18_7_5_a": "ρₛ,req from 18.7.5(a)",
        "Confinement_18_7_5_b": "ρₛ,req from 18.7.5(b)",
        "Confinement_18_7_5_c": "ρₛ,req from 18.7.5(c)",
        "Confinement_18_7_5_d": "ρₛ,req from 18.7.5(d)",
        "Confinement_18_7_5_e": "ρₛ,req from 18.7.5(e)",
        "Confinement_18_7_5_f": "ρₛ,req from 18.7.5(f)",
        "Kf": "kᶠ",
        "Kn": "kₙ",
        "Required_Ash_s_Ratio_X": "Required Aₛₕ/(s b꜀), X",
        "Provided_Ash_s_Ratio_X": "Provided Aₛₕ/(s b꜀), X",
        "Required_Ash_s_Ratio_Y": "Required Aₛₕ/(s b꜀), Y",
        "Provided_Ash_s_Ratio_Y": "Provided Aₛₕ/(s b꜀), Y",
        "Confinement_Check": "Confinement Ratio Check",
        "Transverse_Spacing_Check": "Tie Spacing Check",
        "Alternating_Support_Check": "Longitudinal Bar Support Check",
        "Alternating_Support_Added_Legs": "Added Hoop Legs",
        "Tie_Diameter_Check": "Tie Diameter Check",
        "SMRF_Dimension_Check": "SMRF Column Dimension Check",
        "Transverse_Reinforcement_Check": "Transverse Reinforcement Check",
        "Column_Design_Status": "Overall Design Status",
        "Design_Status_Reason": "Design Status Reason",
    }
    clean_report = report.rename(
        columns={
            name: display_names.get(name, name.replace("_", " "))
            for name in report.columns
        }
    )
    header_row = 9
    first_col = 2
    old_last_row = max(sheet.used_range.last_cell.row, header_row + len(clean_report))
    old_last_col = max(
        sheet.used_range.last_cell.column,
        first_col + len(clean_report.columns) - 1,
    )
    sheet.range((8, first_col), (8, old_last_col)).api.UnMerge()
    sheet.range((header_row + 1, first_col), (old_last_row, first_col + 3)).api.UnMerge()
    sheet.range((8, first_col), (old_last_row, old_last_col)).clear_formats()
    title_range = sheet.range("B2:H2")
    title_range.api.UnMerge()
    title_range.merge()
    title_range.value = "COLUMN DESIGN - CONSOLIDATED CHECKS"
    title_range.api.Font.Name = "Calibri"
    title_range.api.Font.Size = 14
    title_range.api.Font.Bold = True
    title_range.api.HorizontalAlignment = -4131

    report_values = [clean_report.columns.tolist()]
    report_values.extend(
        clean_report.astype(object)
        .where(pd.notna(clean_report), None)
        .values.tolist()
    )
    last_row = header_row + len(clean_report)
    last_col = first_col + len(clean_report.columns) - 1
    sheet.range(
        (header_row, first_col), (last_row, last_col)
    ).value = report_values
    column_index = first_col
    group_colors = {
        "COLUMN LABEL / LEVEL": (189, 215, 238),
        "SECTION / LONGITUDINAL REINFORCEMENT": (226, 239, 218),
        "FLEXURE / AXIAL": (226, 239, 218),
        "COLUMN SHEAR": (255, 242, 204),
        "BEAM-COLUMN CAPACITY": (252, 228, 214),
        "JOINT SHEAR": (252, 228, 214),
        "TRANSVERSE REINFORCEMENT DETAILING": (222, 235, 247),
        "DESIGN STATUS": (217, 210, 233),
    }
    for label, names in groups:
        last_col = column_index + len(names) - 1
        group_range = sheet.range((8, column_index), (8, last_col))
        group_range.merge()
        group_range.value = label
        group_range.color = group_colors.get(label, (189, 215, 238))
        group_range.api.Font.Bold = True
        group_range.api.HorizontalAlignment = -4108
        group_range.api.VerticalAlignment = -4108
        column_index = last_col + 1

    table = sheet.range((8, first_col), (last_row, last_col))
    table.api.Font.Name = "Calibri"
    table.api.Font.Size = 10
    table.api.Borders.LineStyle = -4142
    group_header = sheet.range((8, first_col), (8, last_col))
    group_header.api.Borders(8).LineStyle = 1
    group_header.api.Borders(8).Weight = 3
    group_header.api.Borders(9).LineStyle = 1
    group_header.api.Borders(9).Weight = 3

    leaf_header = sheet.range((header_row, first_col), (header_row, last_col))
    leaf_header.color = (242, 242, 242)
    leaf_header.api.Font.Bold = True
    leaf_header.api.WrapText = True
    leaf_header.api.HorizontalAlignment = -4108
    leaf_header.api.Borders(9).LineStyle = 1
    leaf_header.api.Borders(9).Weight = 2
    leaf_header.api.Borders(8).LineStyle = 1
    leaf_header.api.Borders(8).Weight = 2

    body = sheet.range((header_row + 1, first_col), (last_row, last_col))
    body.number_format = "0.00"
    body.api.WrapText = False
    body.api.ShrinkToFit = True
    body.api.VerticalAlignment = -4108
    body.api.Borders(9).LineStyle = 1
    body.api.Borders(9).Weight = 2
    body.api.Font.Size = 9
    sheet.api.Rows(f"{header_row + 1}:{last_row}").RowHeight = 22
    if old_last_row > last_row:
        sheet.api.Rows(f"{last_row + 1}:{old_last_row}").RowHeight = (
            sheet.api.StandardHeight
        )

    name_to_col = {
        name: first_col + index
        for index, name in enumerate(clean_report.columns)
    }
    widths = {
        "Column Label": 13,
        "Unique Name": 17,
        "Story": 10,
        "End": 6,
        "Load Combination": 36,
        "Section": 22,
        "Bundle Layout": 25,
        "Column Bars Contributing at Joint": 48,
        "Beam Bars at Joint": 42,
        "Transverse Reinforcement Provision": 36,
        "Governing ACI 18.7.5 Expression": 17,
        "ρₛ,req from 18.7.5(a)": 17,
        "ρₛ,req from 18.7.5(b)": 17,
        "ρₛ,req from 18.7.5(c)": 17,
        "ρₛ,req from 18.7.5(d)": 17,
        "ρₛ,req from 18.7.5(e)": 17,
        "ρₛ,req from 18.7.5(f)": 17,
        "Overall Design Status": 17,
        "Longitudinal Bar Support Check": 38,
    }
    for name, width in widths.items():
        if name in name_to_col:
            sheet.range(
                (header_row + 1, name_to_col[name]),
                (last_row, name_to_col[name]),
            ).column_width = width
    for name in clean_report.columns:
        if name not in widths and name not in {
            "Column Label",
            "Unique Name",
            "Story",
            "End",
            "Load Combination",
            "Section",
        }:
            sheet.range(
                (header_row + 1, name_to_col[name]),
                (last_row, name_to_col[name]),
            ).column_width = 14
    leaf_header.row_height = 36

    # Merge repeated hierarchy labels while retaining one value per visible group.
    hierarchy = ("Column Label", "Unique Name", "Story", "End")
    data_records = clean_report.to_dict("records")
    group_keys: dict[str, tuple[str, ...]] = {
        "Column Label": ("Column Label",),
        "Unique Name": ("Column Label", "Unique Name"),
        "Story": ("Column Label", "Unique Name", "Story"),
        "End": ("Column Label", "Unique Name", "Story", "End"),
    }
    for name in hierarchy:
        column = name_to_col[name]
        parent_keys = group_keys[name]
        start = 0
        while start < len(data_records):
            key = tuple(data_records[start][field] for field in parent_keys)
            end = start + 1
            while end < len(data_records) and tuple(
                data_records[end][field] for field in parent_keys
            ) == key:
                end += 1
            first_row = header_row + 1 + start
            final_row = header_row + end
            if name == "End":
                end_fill = (
                    (235, 243, 250)
                    if key[-1] == "I"
                    else (250, 242, 232)
                )
                sheet.range((first_row, column), (final_row, last_col)).color = (
                    end_fill
                )
            if end - start > 1:
                cell_range = sheet.range((first_row, column), (final_row, column))
                cell_range.merge()
                cell_range.api.VerticalAlignment = -4108
            if name in {"Unique Name", "Story", "End"}:
                row_border = sheet.range(
                    (final_row, first_col), (final_row, last_col)
                ).api.Borders(9)
                row_border.LineStyle = 1
                if name == "Unique Name":
                    row_border.Weight = 3
                    row_border.Color = 31 + 78 * 256 + 121 * 65536
                elif name == "Story":
                    row_border.Weight = 2
                    row_border.Color = 91 + 155 * 256 + 213 * 65536
                else:
                    row_border.Weight = 1
                    row_border.Color = 191 + 191 * 256 + 191 * 65536
            start = end

    # Separate ETABS labels clearly and close the report with a strong bottom rule.
    start = 0
    while start < len(data_records):
        end = start + 1
        while (
            end < len(data_records)
            and data_records[end]["Column Label"]
            == data_records[start]["Column Label"]
        ):
            end += 1
        if end < len(data_records):
            divider = sheet.range(
                (header_row + end, first_col),
                (header_row + end, last_col),
            ).api.Borders(9)
            divider.LineStyle = 1
            divider.Weight = 3
            divider.Color = 31 + 78 * 256 + 121 * 65536
        start = end

    label_divider = sheet.range(
        (8, name_to_col["Column Label"]),
        (last_row, name_to_col["Column Label"]),
    ).api.Borders(10)
    label_divider.LineStyle = 1
    label_divider.Weight = 3
    label_divider.Color = 31 + 78 * 256 + 121 * 65536

    # Apply a stronger end boundary after each visual data group on the right.
    group_start = first_col
    for label, names in groups:
        group_end = group_start + len(names) - 1
        divider = sheet.range((8, group_end), (last_row, group_end))
        divider.api.Borders(10).LineStyle = 1
        divider.api.Borders(10).Weight = 3
        divider.api.Borders(10).Color = 31 + 78 * 256 + 121 * 65536
        group_start = group_end + 1

    closing_bar = sheet.range((last_row, first_col), (last_row, last_col)).api.Borders(9)
    closing_bar.LineStyle = 1
    closing_bar.Weight = 3
    closing_bar.Color = 31 + 78 * 256 + 121 * 65536


def _run_column_design_from_excel(
    write_joint_sheet: bool = True,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Design frame-data columns and run SMRF checks using Excel and ETABS inputs.

    Required Excel tables are read from FRAME DATA!B2, CONNECTIVITY!B2,
    FACTORED LOADS!B2, and BEAM DESIGN!B8. The SMRF toggle and column bar,
    tie, and cover inputs are read from OVERWRITES!F3 and I10:I12.

    Args:
        write_joint_sheet: Retained for existing macro compatibility. Joint checks
            are now included in the consolidated COLUMN DESIGN output only.
        progress: Optional callable that receives one status text at a time
            (column mark, level, end, load combination and current check) for the
            loading window.
    """
    try:
        workbook = xw.Book.caller()
    except Exception:
        workbook = xw.books.active

    overwrites = workbook.sheets["OVERWRITES"]
    frame_data = _read_excel_table(workbook.sheets["FRAME DATA"], "B2")
    connectivity = _read_excel_table(workbook.sheets["CONNECTIVITY"], "B2")
    factored_loads = _read_excel_table(workbook.sheets["FACTORED LOADS"], "B2")
    beam_design = _read_excel_table(workbook.sheets["BEAM DESIGN"], "B8")

    is_smrf = overwrites.range("F3").value is True
    dmain = _numeric(overwrites.range("I10").value, "I10 (column main bar)", "OVERWRITES")
    dties = _numeric(overwrites.range("I11").value, "I11 (column tie bar)", "OVERWRITES")
    cover = _numeric(overwrites.range("I12").value, "I12 (column cover)", "OVERWRITES")
    if min(dmain, dties, cover) <= 0:
        raise ValueError("Column main-bar diameter, tie diameter, and cover must be positive.")

    _require_columns(frame_data, {"UniqueName", "DesignType", "f'c", "fy", "fys"}, "FRAME DATA")
    _require_columns(
        connectivity,
        {"UniqueName", "DesignType", "UniquePtI", "UniquePtJ"},
        "CONNECTIVITY",
    )
    _require_columns(
        factored_loads,
        {"UniqueName", "Combo", "Station", "P", "V2", "V3", "M2", "M3"},
        "FACTORED LOADS",
    )

    if frame_data.empty or factored_loads.empty:
        raise ValueError("FRAME DATA and FACTORED LOADS must contain data.")
    # ETABS: compression is negative.  Designers below: compression is positive.
    factored_loads = _to_compression_positive(factored_loads)
    # Every ETABS permutation is checked as its own load set; the report and the
    # loading window show only the plain combination name.
    factored_loads, combo_display = _expand_combo_permutations(factored_loads)
    for frame in (frame_data, connectivity, factored_loads, beam_design):
        if "UniqueName" in frame.columns:
            frame["UniqueName"] = frame["UniqueName"].map(_normalize_object_name)
    for point_column in ("UniquePtI", "UniquePtJ"):
        if point_column in connectivity.columns:
            connectivity[point_column] = connectivity[point_column].map(
                _normalize_object_name
            )

    if is_smrf:
        from etabs_api import ETABSConnector

        etabs = ETABSConnector()
        if not etabs.connect():
            raise RuntimeError("Could not connect to ETABS for column orientation tables.")
        local_axes = _as_etabs_dataframe(
            etabs.get_data("Frame Assignments - Local Axes"),
            "Frame Assignments - Local Axes",
        )
        point_table = _as_etabs_dataframe(
            etabs.get_data("Point Object Connectivity"), "Point Object Connectivity"
        )
        frame_angles = _extract_frame_angles(local_axes)
        frame_labels = _extract_frame_labels(local_axes)
        frame_labels = {
            member: _common_column_mark(member)
            for member in frame_labels
        }
        point_coordinates = _extract_point_coordinates(point_table)
    else:
        frame_angles = {}
        frame_labels = {}
        point_coordinates = {}

    if beam_design.empty:
        raise ValueError(
            "BEAM DESIGN contains no reinforcement results. Run beam design before "
            "column design so beam-column checks use designed longitudinal bars."
        )

    column_rows = frame_data.loc[
        frame_data["DesignType"].astype(str).str.strip().str.casefold().eq("column")
    ].copy()
    column_rows = _deduplicate_frame_data(column_rows)
    if column_rows.empty:
        raise ValueError("FRAME DATA contains no rows with DesignType='Column'.")

    story_by_member = (
        dict(zip(column_rows["UniqueName"].astype(str), column_rows["Story"]))
        if "Story" in column_rows.columns
        else {}
    )

    def report_progress(
        member: str, end: str, check: str, combo: str | None = None
    ) -> None:
        """Send the column mark, level, end, combo and check to the loading window."""
        if progress is None:
            return
        level = story_by_member.get(str(member))
        level_text = "-" if level is None or pd.isna(level) else str(level)
        combo_line = (
            "" if combo is None else f"Combo: {combo_display.get(str(combo), combo)}\n"
        )
        progress(
            f"Column {_common_column_mark(member)}  |  Level {level_text}  |  End {end}\n"
            f"{combo_line}Check: {check}"
        )

    frame_data = _deduplicate_frame_data(frame_data)
    connectivity = connectivity.loc[
        connectivity["DesignType"]
        .astype(str)
        .str.strip()
        .str.casefold()
        .isin({"beam", "column"})
        & connectivity["UniquePtI"].ne("")
        & connectivity["UniquePtJ"].ne("")
    ].drop_duplicates(subset=["UniqueName"], keep="first")
    if connectivity.empty:
        raise ValueError(
            "CONNECTIVITY has no beam/column rows with both endpoints populated."
        )
    force_frames = {
        _normalize_object_name(member): rows.copy()
        for member, rows in factored_loads.groupby("UniqueName", dropna=True)
    }
    connection_by_name = connectivity.drop_duplicates("UniqueName").set_index("UniqueName")
    level_elevations: dict[str, float] = {}
    for member in column_rows["UniqueName"].astype(str):
        if member not in connection_by_name.index:
            continue
        connection = connection_by_name.loc[member]
        point_i = _normalize_object_name(connection["UniquePtI"])
        point_j = _normalize_object_name(connection["UniquePtJ"])
        if point_i in point_coordinates and point_j in point_coordinates:
            level_elevations[member] = float(
                (point_coordinates[point_i][2] + point_coordinates[point_j][2])
                / 2.0
            )
        else:
            level_elevations[member] = np.nan

    column_candidates: dict[str, dict] = {}
    candidate_capacity_cache: dict[tuple, float] = {}
    candidate_section_cache: dict[tuple, tuple[ColumnFlexureDesign, object]] = {}

    for _, row in column_rows.iterrows():
        member = str(row["UniqueName"])
        if member not in force_frames:
            raise ValueError(
                f"Column {member} is in FRAME DATA but has no FACTORED LOADS rows."
            )
        if member not in connection_by_name.index:
            raise ValueError(
                f"Column {member} is in FRAME DATA but is absent from CONNECTIVITY."
            )
        forces = force_frames[member]
        engine, _ = _build_column_section(row, 4, dmain, dties, cover, is_smrf)
        ratio_limit = (
            engine.code.column_strength.rho_max_smrf
            if is_smrf
            else engine.code.column_strength.rho_max
        )
        gross_area = (
            engine.width * engine.height
            if engine.shape == "rectangular"
            else math.pi * engine.diameter**2 / 4.0
        )
        max_bars = int(
            ratio_limit * gross_area / (math.pi * dmain**2 / 4.0)
        )
        layout_options = _enumerate_column_bar_layouts(engine, max_bars=max_bars)
        if not layout_options:
            raise ValueError(
                f"Column {member} has no physically valid longitudinal bar layout "
                f"within the {ratio_limit:.0%} reinforcement limit."
            )
        axial_values = pd.to_numeric(forces["P"], errors="coerce").dropna()
        if axial_values.empty:
            raise ValueError(f"Column {member} has no numeric axial force values.")
        candidate_cache: dict[int, dict] = {}

        def evaluate_candidate(
            index: int,
            _row=row,
            _forces=forces,
            _layout_options=layout_options,
            _candidate_cache=candidate_cache,
            _axial_values=axial_values,
        ) -> dict:
            """Cache strength and transverse checks for one member layout."""
            if index not in _candidate_cache:
                layout = _layout_options[index]
                passes_flexure, checks, limits = _evaluate_column_candidate(
                    _row,
                    _forces,
                    layout,
                    dmain,
                    dties,
                    cover,
                    is_smrf,
                    stop_at_first_failure=True,
                    progress=report_progress,
                )
                if passes_flexure:
                    report_progress(
                        str(_row["UniqueName"]), "I and J", "Transverse detailing"
                    )
                    candidate_engine, _ = _build_column_section(
                        _row,
                        sum(count for _, _, count in layout),
                        dmain,
                        dties,
                        cover,
                        is_smrf,
                        bundle_layout=layout,
                    )
                    (
                        passes_detailing,
                        shear_checks,
                        required_shear_legs,
                    ) = _column_transverse_candidate_passes(
                        _row,
                        _forces,
                        candidate_engine,
                        layout,
                        max(0.0, float(_axial_values.max()) * 1000.0),
                        is_smrf,
                    )
                else:
                    passes_detailing = False
                    shear_checks = None
                    required_shear_legs = 0
                _candidate_cache[index] = {
                    "layout": layout,
                    "checks": checks,
                    "checks_complete": passes_flexure,
                    "limits": limits,
                    "passes_flexure": passes_flexure,
                    "passes_detailing": passes_detailing,
                    "passes": passes_flexure and passes_detailing,
                    "shear_checks": shear_checks,
                    "required_shear_legs": required_shear_legs,
                }
            return _candidate_cache[index]

        state = {
            "row": row,
            "forces": forces,
            "layout_options": layout_options,
            "candidate_cache": candidate_cache,
            "evaluate_candidate": evaluate_candidate,
            "selected_index": None,
        }
        first_flexure_index = None
        selected_index = None
        for index in range(len(layout_options)):
            candidate = evaluate_candidate(index)
            if candidate["passes_flexure"] and first_flexure_index is None:
                first_flexure_index = index
            if candidate["passes"]:
                selected_index = index
                break
        if selected_index is None:
            selected_index = (
                first_flexure_index
                if first_flexure_index is not None
                else len(layout_options) - 1
            )
        state["selected_index"] = selected_index
        column_candidates[member] = state

    def selected_candidate(member: str) -> dict:
        """Return the cached candidate currently selected for a column."""
        state = column_candidates[member]
        candidate = state["evaluate_candidate"](state["selected_index"])
        if not candidate["checks_complete"]:
            _, checks, limits = _evaluate_column_candidate(
                state["row"],
                state["forces"],
                candidate["layout"],
                dmain,
                dties,
                cover,
                is_smrf,
                progress=report_progress,
            )
            candidate["checks"] = checks
            candidate["limits"] = limits
            candidate["checks_complete"] = True
        return candidate

    for state in column_candidates.values():
        state["initial_index"] = state["selected_index"]

    if is_smrf:
        column_members_at_joint: dict[str, list[str]] = {}
        for member, connection in connection_by_name.iterrows():
            if str(connection["DesignType"]).strip().casefold() != "column":
                continue
            for point in ("UniquePtI", "UniquePtJ"):
                joint = _normalize_object_name(connection[point])
                column_members_at_joint.setdefault(joint, []).append(member)

        def candidate_joint_capacity(
            member: str,
            joint_row: pd.Series,
            candidate_index: int,
            fy_factor: float = 1.0,
        ) -> float:
            """Calculate nominal column moment for a candidate at a BCC joint."""
            joint = str(joint_row["Joint_Point"])
            combo = str(joint_row["Load_Combo"])
            direction = round(float(joint_row["Frame_Direction_deg"]), 8)
            cache_key = (member, joint, combo, direction, candidate_index, fy_factor)
            if cache_key in candidate_capacity_cache:
                return candidate_capacity_cache[cache_key]
            connection = connection_by_name.loc[member]
            point_i = _normalize_object_name(connection["UniquePtI"])
            point_j = _normalize_object_name(connection["UniquePtJ"])
            if joint == point_i:
                at_i_end = True
            elif joint == point_j:
                at_i_end = False
            else:
                raise ValueError(f"Column {member} is not connected to joint {joint}.")
            if member not in frame_angles:
                raise ValueError(f"Local-axis angle is missing for column {member}.")
            _, local_2, local_3 = _frame_local_axes(
                point_coordinates[point_i],
                point_coordinates[point_j],
                frame_angles[member],
            )
            direction_radians = math.radians(direction)
            moment_axis = np.array(
                [-math.sin(direction_radians), math.cos(direction_radians), 0.0]
            )
            theta = math.atan2(
                float(np.dot(moment_axis, local_3)),
                float(np.dot(moment_axis, local_2)),
            )
            force = _column_force_at_end(
                force_frames[member], combo, at_i_end=at_i_end
            )
            axial = _numeric(force["P"], "P", member) * 1000.0
            layout = column_candidates[member]["layout_options"][candidate_index]
            section_key = (member, candidate_index, fy_factor)
            if section_key not in candidate_section_cache:
                capacity_row = column_candidates[member]["row"].copy()
                capacity_row["fy"] = _numeric(
                    capacity_row["fy"], "fy", member
                ) * fy_factor
                candidate_section_cache[section_key] = _build_column_section(
                    capacity_row,
                    sum(count for _, _, count in layout),
                    dmain,
                    dties,
                    cover,
                    is_smrf,
                    bundle_layout=layout,
                )
            engine, section = candidate_section_cache[section_key]
            nominal, _, _, _, _ = engine.solve_moment_capacity(
                section, axial_load=axial, bending_angle=theta
            )
            capacity = float(nominal) / 1e6
            candidate_capacity_cache[cache_key] = capacity
            return capacity

        def current_joint_ratio(joint_row: pd.Series) -> float:
            """Recalculate one BCC ratio after candidate-layout upgrades."""
            if pd.isna(joint_row.get("Sum_Column_Mn_kNm")) or pd.isna(
                joint_row.get("Sum_Beam_Mn_kNm")
            ):
                return math.inf
            joint = str(joint_row["Joint_Point"])
            base_total = float(joint_row["Sum_Column_Mn_kNm"])
            for member in column_members_at_joint.get(joint, []):
                state = column_candidates.get(member)
                if state is None:
                    continue
                selected_index = state["selected_index"]
                if selected_index == state.get("initial_index"):
                    continue
                baseline = candidate_joint_capacity(
                    member, joint_row, state["initial_index"]
                )
                updated = candidate_joint_capacity(
                    member, joint_row, selected_index
                )
                base_total += updated - baseline
            beam_capacity = float(joint_row["Sum_Beam_Mn_kNm"])
            return base_total / beam_capacity if beam_capacity > 0 else math.inf

        def next_bcc_improving_candidate(
            member: str, joint_row: pd.Series
        ) -> tuple[int, dict, float] | None:
            """Find a larger passing layout that increases this joint's Mn."""
            state = column_candidates[member]
            current_index = state["selected_index"]
            current_layout = state["layout_options"][current_index]
            current_bars = sum(count for _, _, count in current_layout)
            current_capacity = candidate_joint_capacity(
                member, joint_row, current_index
            )
            for index in range(current_index + 1, len(state["layout_options"])):
                layout = state["layout_options"][index]
                if sum(count for _, _, count in layout) <= current_bars:
                    continue
                candidate = state["evaluate_candidate"](index)
                if not candidate["passes"]:
                    continue
                next_capacity = candidate_joint_capacity(member, joint_row, index)
                if next_capacity > current_capacity + 1e-9:
                    return index, candidate, next_capacity
            return None

        preliminary_columns = [
            {
                "UniqueName": member,
                "Longitudinal_Bars": sum(
                    count for _, _, count in selected_candidate(member)["layout"]
                ),
                "Main_Bar_mm": dmain,
                "Longitudinal_Bar_Layout": _column_layout_summary(
                    selected_candidate(member)["layout"]
                ),
            }
            for member in column_candidates
        ]
        initial_joints = _evaluate_smrf_joints(
            column_results=pd.DataFrame(preliminary_columns),
            connectivity=connectivity,
            frame_data=frame_data,
            factored_loads=factored_loads,
            beam_design=beam_design,
            point_coordinates=point_coordinates,
            frame_angles=frame_angles,
            is_smrf=is_smrf,
            dmain=dmain,
            dties=dties,
            cover=cover,
            column_layouts={
                member: selected_candidate(member)["layout"]
                for member in column_candidates
            },
            progress=report_progress,
        )

        while not initial_joints.empty:
            failing_rows = initial_joints.loc[
                initial_joints["Sum_Column_Mn_kNm"].notna()
                & initial_joints["Sum_Beam_Mn_kNm"].notna()
                & initial_joints.apply(
                    lambda row: current_joint_ratio(row)
                    < CODE.column_seismic.strong_column_ratio - 1e-9,
                    axis=1,
                )
            ]
            if failing_rows.empty:
                break

            best_upgrades = []
            for joint in failing_rows["Joint_Point"].drop_duplicates():
                joint_rows = initial_joints.loc[
                    initial_joints["Joint_Point"].eq(joint)
                ]
                critical_row = min(
                    (row for _, row in joint_rows.iterrows()),
                    key=current_joint_ratio,
                )
                current_minimum = current_joint_ratio(critical_row)
                best_joint_upgrade = None
                for member in column_members_at_joint.get(str(joint), []):
                    state = column_candidates.get(member)
                    if state is None:
                        continue
                    next_candidate = next_bcc_improving_candidate(
                        member, critical_row
                    )
                    if next_candidate is None:
                        continue
                    next_index, candidate, next_capacity = next_candidate
                    current_capacity = candidate_joint_capacity(
                        member, critical_row, state["selected_index"]
                    )
                    beam_capacity = float(critical_row["Sum_Beam_Mn_kNm"])
                    trial_minimum = (
                        current_minimum
                        + (next_capacity - current_capacity) / beam_capacity
                        if beam_capacity > 0
                        else math.inf
                    )
                    current_layout = state["layout_options"][state["selected_index"]]
                    added_bars = (
                        sum(count for _, _, count in candidate["layout"])
                        - sum(count for _, _, count in current_layout)
                    )
                    score = (trial_minimum, -added_bars)
                    if trial_minimum > current_minimum + 1e-9 and (
                        best_joint_upgrade is None
                        or score > best_joint_upgrade["score"]
                    ):
                        best_joint_upgrade = {
                            "member": member,
                            "index": next_index,
                            "score": score,
                        }
                if best_joint_upgrade is not None:
                    best_upgrades.append(best_joint_upgrade)
            if not best_upgrades:
                break
            for upgrade in best_upgrades:
                state = column_candidates[upgrade["member"]]
                state["selected_index"] = max(
                    state["selected_index"], upgrade["index"]
                )

        for joint_index, joint_row in initial_joints.iterrows():
            joint = str(joint_row["Joint_Point"])
            nominal_delta = 0.0
            updated_reinforcement = []
            for member in column_members_at_joint.get(joint, []):
                state = column_candidates.get(member)
                if state is None:
                    continue
                selected_layout = state["layout_options"][state["selected_index"]]
                connection = connection_by_name.loc[member]
                if _normalize_object_name(connection["UniquePtI"]) == joint:
                    end = "I"
                elif _normalize_object_name(connection["UniquePtJ"]) == joint:
                    end = "J"
                else:
                    raise ValueError(
                        f"Column {member} is not connected to joint {joint}."
                    )
                updated_reinforcement.append(
                    f"{member} ({end}): "
                    f"{sum(count for _, _, count in selected_layout)} bars "
                    f"D{dmain:g}; {_column_layout_summary(selected_layout)}"
                )
                if state["selected_index"] == state["initial_index"]:
                    continue
                baseline_index = state["initial_index"]
                selected_index = state["selected_index"]
                nominal_delta += candidate_joint_capacity(
                    member, joint_row, selected_index
                ) - candidate_joint_capacity(member, joint_row, baseline_index)

            total_column_capacity = (
                float(joint_row["Sum_Column_Mn_kNm"]) + nominal_delta
            )
            beam_capacity = float(joint_row["Sum_Beam_Mn_kNm"])
            ratio = (
                total_column_capacity / beam_capacity
                if beam_capacity > 0
                else math.inf
            )
            initial_joints.at[joint_index, "Sum_Column_Mn_kNm"] = (
                total_column_capacity
            )
            initial_joints.at[joint_index, "Column_Beam_Ratio"] = ratio
            initial_joints.at[joint_index, "Strong_Column_Check"] = (
                "PASS" if ratio >= CODE.column_seismic.strong_column_ratio else "FAIL"
            )
            initial_joints.at[
                joint_index, "Column_Reinforcement_At_Joint"
            ] = "; ".join(updated_reinforcement)

            # Joint shear demand uses the BEAMS' probable moments only, so adding
            # column bars changes the strong-column ratio but not the joint shear.
        joint_results = initial_joints

    output_rows: list[dict] = []
    force_check_rows: list[dict] = []
    shear_check_rows: list[dict] = []
    column_layouts: dict[str, list[tuple[float, float, int]]] = {}
    for _, row in column_rows.iterrows():
        member = str(row["UniqueName"])
        if member not in force_frames:
            raise ValueError(
                f"Column {member} is in FRAME DATA but has no FACTORED LOADS rows."
            )
        if member not in connection_by_name.index:
            raise ValueError(
                f"Column {member} is in FRAME DATA but is absent from CONNECTIVITY."
            )
        forces = force_frames[member]
        chosen_candidate = selected_candidate(member)
        selected_layout = chosen_candidate["layout"]
        selected_checks = chosen_candidate["checks"]
        selected_limits = chosen_candidate["limits"]
        if not chosen_candidate["passes_flexure"]:
            status = "FAIL: No passing bar count within reinforcement limit"
        elif not chosen_candidate["passes_detailing"]:
            status = (
                "FAIL: Transverse detailing requirements not met within "
                "reinforcement limit"
            )
        else:
            status = "PASS"

        selected_bars = sum(count for _, _, count in selected_layout)
        column_layouts[member] = selected_layout
        final_engine, _ = _build_column_section(
            row,
            selected_bars,
            dmain,
            dties,
            cover,
            is_smrf,
            bundle_layout=selected_layout,
        )
        max_spacing = final_engine.solve_max_spacing()
        spacing_provided = max_spacing["max_tie_spacing_mm"]
        dimension_check = "PASS"
        if is_smrf:
            short_dim = (
                min(final_engine.width, final_engine.height)
                if final_engine.shape == "rectangular"
                else final_engine.diameter
            )
            long_dim = (
                max(final_engine.width, final_engine.height)
                if final_engine.shape == "rectangular"
                else final_engine.diameter
            )
            seismic_cfg = final_engine.code.column_seismic
            if (
                short_dim < seismic_cfg.min_dimension
                or short_dim / long_dim < seismic_cfg.min_aspect_ratio
            ):
                dimension_check = "FAIL: ACI 18.7.2.1"
                status = "FAIL: SMRF dimensional limits"
            if final_engine.shape == "rectangular":
                tie_spacing_limit = min(
                    seismic_cfg.spacing_dimension_fraction * short_dim,
                    seismic_cfg.spacing_bar_multiple * dmain,
                    _smrf_so_limit(final_engine.code),
                )
            else:
                tie_spacing_limit = min(
                    seismic_cfg.spacing_dimension_fraction * short_dim,
                    seismic_cfg.spacing_bar_multiple * dmain,
                    seismic_cfg.spiral_pitch_offset + dties,
                )
            spacing_provided = min(spacing_provided, tie_spacing_limit)
            axial_values = pd.to_numeric(forces["P"], errors="coerce").dropna()
            if axial_values.empty:
                raise ValueError(f"Column {member} has no numeric axial force values.")
            maximum_compression = max(0.0, float(axial_values.max()) * 1000.0)
            transverse = _smrf_transverse_design(
                final_engine,
                selected_bars,
                maximum_compression,
                spacing_provided,
            )
            leg_count, added_legs, alternating_status = (
                _post_check_alternating_support(
                    final_engine,
                    selected_layout,
                    transverse["Transverse_Legs_Per_Direction"],
                )
            )
            transverse["Transverse_Legs_Per_Direction"] = leg_count
            transverse["Transverse_Legs_X"] = leg_count
            transverse["Transverse_Legs_Y"] = leg_count
            transverse["Alternating_Support_Check"] = alternating_status
            transverse["Alternating_Support_Added_Legs"] = added_legs
            if final_engine.shape == "rectangular":
                core_width = final_engine.width - 2.0 * (
                    final_engine.cc + final_engine.dties / 2.0
                )
                core_height = final_engine.height - 2.0 * (
                    final_engine.cc + final_engine.dties / 2.0
                )
                tie_area = math.pi * final_engine.dties**2 / 4.0
                provided_ratio_x = (
                    leg_count * tie_area / (spacing_provided * core_width)
                )
                provided_ratio_y = (
                    leg_count * tie_area / (spacing_provided * core_height)
                )
                transverse["Provided_Ash_s_Ratio_X"] = provided_ratio_x
                transverse["Provided_Ash_s_Ratio_Y"] = provided_ratio_y
                transverse["Provided_Confinement_Ratio_X"] = provided_ratio_x
                transverse["Provided_Confinement_Ratio_Y"] = provided_ratio_y
                transverse["Provided_Confinement_Ratio"] = min(
                    provided_ratio_x, provided_ratio_y
                )
                transverse["Confinement_Check"] = (
                    "PASS"
                    if provided_ratio_x
                    >= transverse["Required_Confinement_Ratio_X"]
                    and provided_ratio_y
                    >= transverse["Required_Confinement_Ratio_Y"]
                    else "FAIL"
                )
            spacing_provided = transverse["Transverse_Spacing_Provided_mm"]
            if transverse["Confinement_Check"] != "PASS":
                status = "FAIL: SMRF transverse reinforcement"
        else:
            transverse = {
                "Transverse_Type": "Ties" if final_engine.shape == "rectangular" else "Spiral",
                "Transverse_Legs_Per_Direction": 2,
                "Confinement_Check": "N/A - non-SMRF detailing",
                "Alternating_Support_Check": "N/A - non-SMRF detailing",
                "Alternating_Support_Added_Legs": 0,
                "Transverse_Legs_X": 2,
                "Transverse_Legs_Y": 2,
                "Transverse_Spacing_Check": "N/A - non-SMRF detailing",
                "Confinement_Criteria_Governing": "N/A - non-SMRF detailing",
                "Required_Ash_s_Ratio_X": np.nan,
                "Required_Ash_s_Ratio_Y": np.nan,
                "Provided_Ash_s_Ratio_X": np.nan,
                "Provided_Ash_s_Ratio_Y": np.nan,
                "High_Axial_or_High_fc_Check": "N/A - non-SMRF detailing",
                "Confinement_18_7_5_a": np.nan,
                "Confinement_18_7_5_b": np.nan,
                "Confinement_18_7_5_c": np.nan,
                "Confinement_18_7_5_d": np.nan,
                "Confinement_18_7_5_e": np.nan,
                "Confinement_18_7_5_f": np.nan,
                "Kf": np.nan,
                "Kn": np.nan,
            }

        required_tie_diameter = _minimum_tie_diameter(final_engine)
        tie_diameter_check = (
            "PASS"
            if dties >= required_tie_diameter
            else (
                f"FAIL: minimum transverse bar is "
                f"{required_tie_diameter:g} mm for this longitudinal bar"
            )
        )
        if tie_diameter_check.startswith("FAIL"):
            status = "FAIL: transverse bar diameter"
        transverse_detail_checks = (
            transverse["Confinement_Check"],
            transverse["Transverse_Spacing_Check"],
            tie_diameter_check,
            transverse["Alternating_Support_Check"],
        )
        transverse["Transverse_Reinforcement_Check"] = (
            "PASS"
            if all(
                check == "PASS"
                or str(check).startswith(("PASS", "N/A"))
                for check in transverse_detail_checks
            )
            else "FAIL"
        )

        member_shear_checks = chosen_candidate["shear_checks"]
        required_shear_legs = chosen_candidate["required_shear_legs"]
        if member_shear_checks is None:
            member_shear_checks, required_shear_legs = _column_shear_checks(
                row,
                forces,
                final_engine,
                selected_bars,
                spacing_provided,
                transverse["Transverse_Legs_Per_Direction"],
                is_smrf,
                bundle_layout=selected_layout,
                progress=report_progress,
            )
        transverse["Transverse_Legs_Per_Direction"] = max(
            transverse["Transverse_Legs_Per_Direction"], required_shear_legs
        )
        transverse["Transverse_Legs_X"] = max(
            transverse["Transverse_Legs_X"], required_shear_legs
        )
        transverse["Transverse_Legs_Y"] = max(
            transverse["Transverse_Legs_Y"], required_shear_legs
        )
        if final_engine.shape == "rectangular" and is_smrf:
            core_width = final_engine.width - 2.0 * (
                final_engine.cc + final_engine.dties / 2.0
            )
            core_height = final_engine.height - 2.0 * (
                final_engine.cc + final_engine.dties / 2.0
            )
            tie_area = math.pi * final_engine.dties**2 / 4.0
            transverse["Provided_Confinement_Ratio"] = min(
                transverse["Transverse_Legs_X"]
                * tie_area
                / (spacing_provided * core_width),
                transverse["Transverse_Legs_Y"]
                * tie_area
                / (spacing_provided * core_height),
            )
            transverse["Provided_Ash_s_Ratio_X"] = (
                transverse["Transverse_Legs_X"]
                * tie_area
                / (spacing_provided * core_width)
            )
            transverse["Provided_Ash_s_Ratio_Y"] = (
                transverse["Transverse_Legs_Y"]
                * tie_area
                / (spacing_provided * core_height)
            )
            transverse["Provided_Confinement_Ratio_X"] = transverse[
                "Provided_Ash_s_Ratio_X"
            ]
            transverse["Provided_Confinement_Ratio_Y"] = transverse[
                "Provided_Ash_s_Ratio_Y"
            ]
            transverse["Confinement_Check"] = (
                "PASS"
                if transverse["Provided_Ash_s_Ratio_X"]
                >= transverse["Required_Confinement_Ratio_X"]
                and transverse["Provided_Ash_s_Ratio_Y"]
                >= transverse["Required_Confinement_Ratio_Y"]
                else "FAIL"
            )
        if transverse["Confinement_Check"] == "FAIL":
            status = "FAIL: SMRF transverse reinforcement"
        transverse["Transverse_Reinforcement_Check"] = (
            "PASS"
            if all(
                check == "PASS" or str(check).startswith(("PASS", "N/A"))
                for check in (
                    transverse["Confinement_Check"],
                    transverse["Transverse_Spacing_Check"],
                    tie_diameter_check,
                    transverse["Alternating_Support_Check"],
                )
            )
            else "FAIL"
        )
        if any(check["Shear_Check"] != "PASS" for check in member_shear_checks):
            status = "FAIL: column shear strength"
        shear_check_rows.extend(member_shear_checks)

        force_check_rows.extend(selected_checks)
        layout_summary = _column_layout_summary(selected_layout)
        transverse_summary = (
            f"{dties:g} mm {transverse['Transverse_Type']} @ "
            f"{spacing_provided:.0f} mm; "
            f"X: {transverse['Transverse_Legs_X']} legs, "
            f"Y: {transverse['Transverse_Legs_Y']} legs"
        )
        output_rows.append(
            {
                "Story": row.get("Story"),
                "UniqueName": member,
                "Column_Label": frame_labels.get(member, member),
                "Level_Elevation_m": level_elevations.get(member, np.nan),
                "SectProp": row.get("SectProp"),
                "Shape": final_engine.shape.capitalize(),
                "Width_mm": final_engine.width or np.nan,
                "Depth_mm": final_engine.height or np.nan,
                "Diameter_mm": final_engine.diameter or np.nan,
                "f'c_MPa": final_engine.fc,
                "fy_MPa": final_engine.fy,
                "fyt_MPa": final_engine.fyt,
                "Main_Bar_mm": dmain,
                "Tie_Bar_mm": dties,
                "Cover_mm": cover,
                "Longitudinal_Bars": selected_bars,
                "Longitudinal_Spacing_Limit_mm": (
                    final_engine.code.column_strength.max_longitudinal_spacing
                ),
                "Reinforcement_Ratio": selected_limits.get("rho", np.nan),
                "Reinforcement_Ratio_Limit": ratio_limit,
                "Transverse_Spacing_Provided_mm": spacing_provided,
                "Transverse_Provision_Summary": transverse_summary,
                "Transverse_Type": transverse["Transverse_Type"],
                "Transverse_Legs_Per_Direction": transverse[
                    "Transverse_Legs_Per_Direction"
                ],
                "Transverse_Legs_X": transverse["Transverse_Legs_X"],
                "Transverse_Legs_Y": transverse["Transverse_Legs_Y"],
                "Confinement_Criteria_Governing": transverse[
                    "Confinement_Criteria_Governing"
                ],
                "High_Axial_or_High_fc_Check": transverse[
                    "High_Axial_or_High_fc_Check"
                ],
                "Confinement_18_7_5_a": transverse.get(
                    "Confinement_18_7_5_a", np.nan
                ),
                "Confinement_18_7_5_b": transverse.get(
                    "Confinement_18_7_5_b", np.nan
                ),
                "Confinement_18_7_5_c": transverse.get(
                    "Confinement_18_7_5_c", np.nan
                ),
                "Confinement_18_7_5_d": transverse.get(
                    "Confinement_18_7_5_d", np.nan
                ),
                "Confinement_18_7_5_e": transverse.get(
                    "Confinement_18_7_5_e", np.nan
                ),
                "Confinement_18_7_5_f": transverse.get(
                    "Confinement_18_7_5_f", np.nan
                ),
                "Kf": transverse.get("Kf", np.nan),
                "Kn": transverse.get("Kn", np.nan),
                "Required_Ash_s_Ratio_X": transverse[
                    "Required_Ash_s_Ratio_X"
                ],
                "Provided_Ash_s_Ratio_X": transverse[
                    "Provided_Ash_s_Ratio_X"
                ],
                "Required_Ash_s_Ratio_Y": transverse[
                    "Required_Ash_s_Ratio_Y"
                ],
                "Provided_Ash_s_Ratio_Y": transverse[
                    "Provided_Ash_s_Ratio_Y"
                ],
                "Confinement_Check": transverse["Confinement_Check"],
                "Transverse_Spacing_Check": transverse[
                    "Transverse_Spacing_Check"
                ],
                "Tie_Diameter_Check": tie_diameter_check,
                "Alternating_Support_Check": transverse[
                    "Alternating_Support_Check"
                ],
                "Alternating_Support_Added_Legs": transverse[
                    "Alternating_Support_Added_Legs"
                ],
                "Transverse_Reinforcement_Check": transverse[
                    "Transverse_Reinforcement_Check"
                ],
                "Longitudinal_Bar_Layout": layout_summary,
                "Longitudinal_Bar_Coordinates": _encode_bar_layout(selected_layout),
                "Aggregate_Clear_Spacing_Check": (
                    "NOT CHECKED - aggregate size is unavailable"
                    if is_smrf and final_engine.shape == "circular"
                    else "N/A"
                ),
                "SMRF_Dimension_Check": dimension_check,
                "Flexure_Axial_Check": (
                    "PASS"
                    if selected_checks
                    and all(
                        check["Strength_Check"] == "PASS"
                        for check in selected_checks
                    )
                    else "FAIL"
                ),
                "Design_Status": status,
            }
        )

    column_results = pd.DataFrame(output_rows)
    load_checks = pd.DataFrame(force_check_rows)

    if not is_smrf:
        joint_results = pd.DataFrame()
    load_checks, shear_checks, joint_results = _collapse_combo_permutations(
        load_checks, pd.DataFrame(shear_check_rows), joint_results, combo_display
    )
    report, report_groups = _build_consolidated_column_report(
        column_results,
        load_checks,
        shear_checks,
        joint_results,
        column_labels=frame_labels,
        level_elevations=level_elevations,
    )
    if progress is not None:
        progress("Writing the COLUMN DESIGN report to Excel...")
    output_sheet = _new_output_sheet(workbook, "COLUMN DESIGN")
    _write_consolidated_column_report(output_sheet, report, report_groups)
    workbook.save()
    return report, joint_results


def run_column_design_from_excel(
    write_joint_sheet: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the column design workflow while showing its active process in a GUI."""
    from utilities._gui_helpers import LoadingWindow

    with LoadingWindow("Designing columns...") as window:
        return _run_column_design_from_excel(write_joint_sheet, progress=window.update)


def _column_story_sort_key(story: object) -> tuple[int, str]:
    """Return a practical bottom-to-top order for common ETABS story labels."""
    text = str(story).strip().upper()
    if text in {"GF", "GROUND", "GROUND FLOOR"}:
        return 0, text
    basement = re.search(r"(?:B|BASEMENT)\s*(\d+)", text)
    if basement:
        return -int(basement.group(1)), text
    floor = re.search(r"(?:P|PENTHOUSE)\s*(\d+)", text)
    if floor:
        return 10000 + int(floor.group(1)), text
    number = re.search(r"-?\d+", text)
    if number:
        return int(number.group()), text
    return 0, text


def _bundle_histogram(summary: str) -> dict[int, int]:
    """Parse the report bundle summary into bundle-size to package-count pairs."""
    result: dict[int, int] = {}
    for part in summary.split(";"):
        single_match = re.fullmatch(r"\s*(\d+)\s+single bars\s*", part)
        bundle_match = re.fullmatch(
            r"\s*(\d+)\s+bundles of\s+(\d+)\s+bars\s*", part
        )
        if single_match:
            result[1] = int(single_match.group(1))
        elif bundle_match:
            result[int(bundle_match.group(2))] = int(bundle_match.group(1))
        elif part.strip():
            raise ValueError(f"Unrecognized column bundle summary: {summary!r}")
    return result


def _column_layout_from_report(
    row: pd.Series,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    cover: float,
    is_smrf: bool,
) -> list[tuple[float, float, int]]:
    """Return the bar layout the design selected for this report row.

    The report stores the exact coordinates in ``Bar Layout Data (x, y, n)``, so
    the drawing always shows the designed arrangement. Reports written before
    that column existed fall back to the first enumerated layout that matches
    the bar total and bundle summary.
    """
    stored_layout = _decode_bar_layout(row.get("Bar Layout Data (x, y, n)"))
    if stored_layout is not None:
        return stored_layout
    is_circular = (
        pd.notna(row.get("Diameter (mm)"))
        and float(row["Diameter (mm)"]) > 0
    )
    engine = ColumnFlexureDesign(
        width=0.0 if is_circular else float(row["Width (mm)"]),
        height=0.0 if is_circular else float(row["Depth (mm)"]),
        diameter=float(row["Diameter (mm)"]) if is_circular else 0.0,
        fc=float(row["f′c (MPa)"]),
        fy=415.0,
        fyt=415.0,
        dmain=main_bar_diameter,
        dties=tie_bar_diameter,
        cc=cover,
        shape="circular" if is_circular else "rectangular",
        is_smrf=is_smrf,
    )
    total_bars = int(float(row["Longitudinal Bars"]))
    ratio_limit = (
        engine.code.column_strength.rho_max_smrf
        if is_smrf
        else engine.code.column_strength.rho_max
    )
    gross_area = (
        math.pi * engine.diameter**2 / 4.0
        if is_circular
        else engine.width * engine.height
    )
    max_bars = int(
        ratio_limit * gross_area / (math.pi * main_bar_diameter**2 / 4.0)
    )
    layouts = _enumerate_column_bar_layouts(engine, max_bars=max_bars)
    expected_histogram = _bundle_histogram(str(row["Bundle Layout"]))
    for layout in layouts:
        histogram: dict[int, int] = {}
        for _, _, count in layout:
            histogram[count] = histogram.get(count, 0) + 1
        if (
            sum(count for _, _, count in layout) == total_bars
            and histogram == expected_histogram
        ):
            return layout
    raise ValueError(
        f"Could not reconstruct the selected bar coordinates for "
        f"{row['Unique Name']} from its report bundle summary."
    )


# ---------------------------------------------------------------------------
# DXF TIE DRAWING
#
# Every tie bar (hoop pieces and crossties) is described as a centreline path
# plus a STACKING LEVEL. A bar with a higher level lies on top of a bar with a
# lower level, so wherever two bars cross, the lower bar's outline is trimmed
# away underneath the upper bar. This is what makes the drawing read like real
# reinforcement instead of wire-frame lines passing through each other.
#
# Stacking order, bottom to top:
#   0        hoop leg that comes up the LEFT side (the end laid first)
#   1        hoop leg that comes along the TOP from the RIGHT (the end laid
#            last): it lies on top of the left leg wherever the two overlap
#   2 .. 3   interior ties parallel to Y (vertical), later ties on top of earlier
#   3 .. 4   interior ties parallel to X (horizontal), later ties on top of earlier
# Interior ties are crossties or closed inner hoops (see _crosstie_bars).
# ---------------------------------------------------------------------------
LEVEL_HOOP_LEFT_LEG = 0.0
LEVEL_HOOP_RIGHT_LEG = 1.0
LEVEL_CROSSTIE_Y = 2.0  # ties parallel to Y; x-ties (below) sit on top of them
LEVEL_CROSSTIE_X = 3.0


@dataclass(frozen=True)
class TieBar:
    """A tie bar centreline, its DXF layer, and where it sits in the stack."""

    path: list[tuple[float, float]]
    level: float
    layer: str


def _rotate_dxf_vector(
    vector: tuple[float, float], angle: float
) -> tuple[float, float]:
    """Rotate a two-dimensional DXF vector counter-clockwise."""
    cosine, sine = math.cos(angle), math.sin(angle)
    return (
        vector[0] * cosine - vector[1] * sine,
        vector[0] * sine + vector[1] * cosine,
    )


def _hook_tail_length_dxf(tie_bar_diameter: float) -> float:
    """Return the ACI 25.3.2 hook extension in millimetres (6 db, at least 75 mm)."""
    cfg = CODE.drawing
    return max(cfg.hook_tail_bar_multiple * tie_bar_diameter, cfg.hook_tail_min)


def _hook_points_dxf(
    end: tuple[float, float],
    direction: tuple[float, float],
    turn_degrees: float,
    side: int,
    bend_radius: float,
    tail_length: float,
) -> list[tuple[float, float]]:
    """Build explicit bend-arc and straight-tail points for a tie hook."""
    steps = CODE.drawing.arc_steps
    turn = math.radians(turn_degrees) * side
    normal = (-direction[1], direction[0])
    center = (
        end[0] + side * bend_radius * normal[0],
        end[1] + side * bend_radius * normal[1],
    )
    radius_vector = (end[0] - center[0], end[1] - center[1])
    points = []
    for step in range(1, steps + 1):
        dx, dy = _rotate_dxf_vector(radius_vector, turn * step / steps)
        points.append((center[0] + dx, center[1] + dy))
    tail_x, tail_y = _rotate_dxf_vector(direction, turn)
    arc_end = points[-1]
    points.append(
        (
            arc_end[0] + tail_length * tail_x,
            arc_end[1] + tail_length * tail_y,
        )
    )
    return points


def _arc_points_dxf(
    center: tuple[float, float],
    radius: float,
    start_degrees: float,
    end_degrees: float,
) -> list[tuple[float, float]]:
    """Return sampled points along a circular DXF arc."""
    steps = CODE.drawing.arc_steps
    points = []
    for index in range(steps + 1):
        angle = math.radians(
            start_degrees + (end_degrees - start_degrees) * index / steps
        )
        points.append(
            (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
        )
    return points


# ---------------------------------------------------------------------------
# BUNDLED BARS IN THE DRAWING
#
# A layout position holds 1 to 4 bars. The position itself is the bar that sits
# against the tie; the other bars of the bundle are placed like this:
#
#   bars  corner position                      face position
#   2     second bar on the diagonal, inward   second bar directly behind the first
#   3     L-shape: one bar along each face     two along the face, third behind the
#                                              bar on the tie-shaft side
#   4     2 x 2 square                         2 x 2 square
#
# On a circular column there is no tie shaft to make room for, so the third bar
# of a 3-bar bundle sits centred behind the other two (a triangle).
#
# A tie cannot be bent around a bundle with a normal bend when another bar of
# the bundle lies in the way (bundles of 3 or 4, or a second bar stacked behind
# the bar a hoop closes on). There the tie turns with a sharp corner, runs flat
# along two bars, and then bends 45 degrees toward the core. The hook extension
# leaves the bundle diagonally into the core, 135 degrees from the leg the tie
# arrived on, and clears every bar of the bundle.
# ---------------------------------------------------------------------------
BUNDLE_FLAT_HOOK_MIN = 3  # bars in a bundle from which the flat-run hook is used
INNER_TIE_STYLES = ("crossties", "hoops")


@dataclass(frozen=True)
class BarSite:
    """One layout position and the directions its bundle is arranged along.

    For a face position ``along`` runs along the face and ``inward`` points to
    the column core. For a corner position both are the inward face directions.
    ``shaft_side`` is +1 or -1 along ``along``: the side of the position on
    which the tie shaft (or inner-hoop leg) passes. ``centred_third`` places the
    third bar of a 3-bar bundle between the other two instead of behind one.
    """

    x: float
    y: float
    count: int
    along: tuple[float, float]
    inward: tuple[float, float]
    is_corner: bool = False
    shaft_side: float = -1.0
    centred_third: bool = False


def _bundle_bar_offsets(site: BarSite, bar_diameter: float) -> list[tuple[float, float]]:
    """Offsets of every bar of a bundle from its layout position (bars touch)."""
    d = bar_diameter
    (tx, ty), (nx, ny) = site.along, site.inward
    if site.count == 1:
        return [(0.0, 0.0)]
    if site.count > 4:
        raise ValueError(f"Bundles of {site.count} bars cannot be drawn (maximum is 4).")
    if site.is_corner:
        diagonal = d / math.sqrt(2.0)
        if site.count == 2:
            return [(0.0, 0.0), (diagonal * (tx + nx), diagonal * (ty + ny))]
        offsets = [(0.0, 0.0), (d * tx, d * ty), (d * nx, d * ny)]
        if site.count == 4:
            offsets.append((d * (tx + nx), d * (ty + ny)))
        return offsets
    if site.count == 2:
        return [(0.0, 0.0), (d * nx, d * ny)]
    half = site.shaft_side * d / 2.0
    first = (half * tx, half * ty)  # bar on the tie-shaft side
    second = (-half * tx, -half * ty)
    if site.count == 3 and site.centred_third:
        depth = d * math.sqrt(3.0) / 2.0  # equilateral triangle: all three bars touch
        return [first, second, (depth * nx, depth * ny)]
    offsets = [first, second, (first[0] + d * nx, first[1] + d * ny)]
    if site.count == 4:
        offsets.append((second[0] + d * nx, second[1] + d * ny))
    return offsets


def _interior_tie_coordinates(
    points: list[tuple[float, float]],
) -> tuple[list[float], list[float]]:
    """Coordinates of interior bars present on both opposite faces (x list, y list)."""
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs = sorted(
        x
        for x in {x for x, y in points if y == y_min} & {x for x, y in points if y == y_max}
        if x_min < x < x_max
    )
    horizontal_ys = sorted(
        y
        for y in {y for x, y in points if x == x_min} & {y for x, y in points if x == x_max}
        if y_min < y < y_max
    )
    return vertical_xs, horizontal_ys


def _shaft_sides(coordinates: list[float], inner_tie_style: str) -> dict[float, float]:
    """Side on which each interior tie passes its bar position (+1 or -1).

    Crossties alternate, so neighbouring hooks point away from each other.
    Inner hoops enclose positions in pairs, so each leg runs on the outside of
    its pair; an unpaired last position gets a single crosstie. The last tie
    always has its shaft on the far side, so its hook extension points away from
    the corner bundle next to it.
    """
    sides = {}
    for index, coordinate in enumerate(coordinates):
        if inner_tie_style == "hoops":
            paired_right = index % 2 == 1
            sides[coordinate] = 1.0 if paired_right else -1.0
        else:
            sides[coordinate] = -1.0 if index % 2 == 0 else 1.0
    if len(coordinates) > 1:
        sides[coordinates[-1]] = 1.0
    return sides


def _rect_bar_sites(
    layout: list[tuple[float, float, int]], inner_tie_style: str = "crossties"
) -> list[BarSite]:
    """Describe every position of a rectangular layout for drawing bars and ties."""
    if inner_tie_style not in INNER_TIE_STYLES:
        raise ValueError(f"inner_tie_style must be one of {INNER_TIE_STYLES}.")
    points = [(round(x, 4), round(y, 4)) for x, y, _ in layout]
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs, horizontal_ys = _interior_tie_coordinates(points)
    x_sides = _shaft_sides(vertical_xs, inner_tie_style)
    y_sides = _shaft_sides(horizontal_ys, inner_tie_style)

    sites = []
    for (x, y), (_, _, count) in zip(points, layout):
        on_x_face, on_y_face = x in (x_min, x_max), y in (y_min, y_max)
        inward_x = (1.0, 0.0) if x == x_min else (-1.0, 0.0)
        inward_y = (0.0, 1.0) if y == y_min else (0.0, -1.0)
        if on_x_face and on_y_face:
            sites.append(BarSite(x, y, count, inward_x, inward_y, is_corner=True))
        elif on_y_face:  # bottom or top face: ties run vertically
            sites.append(
                BarSite(x, y, count, (1.0, 0.0), inward_y, shaft_side=x_sides.get(x, -1.0))
            )
        else:  # left or right face: ties run horizontally
            sites.append(
                BarSite(x, y, count, (0.0, 1.0), inward_x, shaft_side=y_sides.get(y, -1.0))
            )
    return sites


def _circular_bar_sites(layout: list[tuple[float, float, int]]) -> list[BarSite]:
    """Describe every position of a circular layout (bundles stack toward the centre)."""
    sites = []
    for x, y, count in layout:
        radius = math.hypot(x, y)
        inward = (-x / radius, -y / radius) if radius > 0 else (0.0, -1.0)
        along = (-inward[1], inward[0])
        sites.append(BarSite(x, y, count, along, inward, centred_third=True))
    return sites


def _rect_hoop_bars(
    c_left: float,
    c_bottom: float,
    c_right: float,
    c_top: float,
    radius: float,
    tail_length: float,
    bar_pitch: float,
    neighbours: tuple[bool, bool, bool],
    left_level: float,
    right_level: float,
    layer: str,
) -> list[TieBar]:
    """Describe a closed rectangular hoop as two stacked legs closing at the top-left.

    ``c_*`` are the centres of the four corner bars; the hoop centreline runs one
    bend radius outside them. Both hook ends are at the top-left corner. The leg
    that arrives from the RIGHT (along the top, then around the hoop) is laid
    last, so it lies on top of the leg that comes up the LEFT side.

    ``neighbours`` tells which other bars of the bundle touch the top-left corner
    bar: ``(beside it along the top, below it along the left side, diagonal)``,
    each one ``bar_pitch`` away. A hook end whose normal bend would run into a
    neighbour instead turns the corner sharply, runs flat past two bars, and then
    bends 45 degrees into the core (see the notes above ``BUNDLE_FLAT_HOOK_MIN``).
    """
    x0, y0 = c_left - radius, c_bottom - radius
    x1, y1 = c_right + radius, c_top + radius
    hook_angle = CODE.drawing.hook_angle_degrees
    beside, below, _ = neighbours
    flat_turn = hook_angle - 90.0  # the sharp corner already turned 90 degrees
    corner = (x0, y1)  # sharp corner used by the flat-run ends

    if below:
        # Leg from the right: turns down the left side past two bars, then bends.
        down_end = (x0, c_top - bar_pitch)
        right_hook = _hook_points_dxf(
            down_end, (0.0, -1.0), flat_turn, 1, radius, tail_length
        )
        right_head = [*reversed(right_hook), down_end, corner]
    else:
        # Leg from the right: along the top edge, hooks back around the corner bar.
        right_start = (c_left, y1)
        right_hook = _hook_points_dxf(
            right_start, (-1.0, 0.0), hook_angle, 1, radius, tail_length
        )
        right_head = [*reversed(right_hook), right_start]

    if beside:
        # Leg from the left: turns along the top past two bars, then bends.
        across_end = (c_left + bar_pitch, y1)
        left_hook = _hook_points_dxf(
            across_end, (1.0, 0.0), flat_turn, -1, radius, tail_length
        )
        left_tail = [corner, across_end, *left_hook]
    else:
        # Leg from the left: up the left side, hooks around the corner bar.
        left_end = (x0, c_top)
        left_hook = _hook_points_dxf(
            left_end, (0.0, 1.0), hook_angle, -1, radius, tail_length
        )
        left_tail = [left_end, *left_hook]

    split = (x0, c_bottom)  # where the two legs meet at the lower-left
    right_leg = [
        *right_head,
        (c_right, y1),
        *_arc_points_dxf((c_right, c_top), radius, 90.0, 0.0),
        (x1, c_bottom),
        *_arc_points_dxf((c_right, c_bottom), radius, 0.0, -90.0),
        (c_left, y0),
        *_arc_points_dxf((c_left, c_bottom), radius, -90.0, -180.0),
    ]
    left_leg = [split, *left_tail]
    return [
        TieBar(left_leg, left_level, layer),
        TieBar(right_leg, right_level, layer),
    ]


def _hook_corner_neighbours(site: BarSite, hoop_is_vertical: bool) -> tuple[bool, bool, bool]:
    """Which bundle bars touch the bar a hoop closes around (see ``_rect_hoop_bars``).

    ``site`` is the bar position at the hoop's top-left corner. ``hoop_is_vertical``
    is True for the perimeter hoop and for inner hoops spanning bottom to top.
    """
    if site.count >= 4:
        return True, True, True
    if site.count == 3:
        return True, True, False  # L-shape
    if site.count == 2 and not site.is_corner:
        # Second bar stacked toward the core: below a top-face bar, beside a
        # left-face bar.
        return (False, True, False) if hoop_is_vertical else (True, False, False)
    return False, False, False  # single bar, or a corner pair on the diagonal


def _hoop_tie_bars(
    left: float,
    bottom: float,
    right: float,
    top: float,
    cover: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    scale: float,
    hook_corner_count: int = 1,
) -> list[TieBar]:
    """Describe the perimeter hoop as two stacked legs meeting at the top-left corner.

    Both 135-degree hook ends wrap the same corner bar:
        * left leg  - up the left face and around the corner   (level 0)
        * right leg - top edge, right side, bottom, up to the left face
                      and its hook at the corner                (level 1)

    ``hook_corner_count`` is the number of bars bundled at that corner. With
    three or more the hook ends use the flat run described in ``_rect_hoop_bars``.
    """
    if hook_corner_count >= 4:
        neighbours = (True, True, True)
    elif hook_corner_count == 3:
        neighbours = (True, True, False)
    else:
        neighbours = (False, False, False)
    thickness = tie_bar_diameter * scale
    radius = (main_bar_diameter + tie_bar_diameter) / 2.0 * scale
    tail_length = _hook_tail_length_dxf(tie_bar_diameter) * scale
    inset = cover * scale + thickness / 2.0
    return _rect_hoop_bars(
        left + inset + radius,
        bottom + inset + radius,
        right - inset - radius,
        top - inset - radius,
        radius,
        tail_length,
        main_bar_diameter * scale,
        neighbours,
        LEVEL_HOOP_LEFT_LEG,
        LEVEL_HOOP_RIGHT_LEG,
        "TIES",
    )


def _crosstie_end_points(
    bar: tuple[float, float],
    count: int,
    outward: tuple[float, float],
    toward_tail: tuple[float, float],
    bar_diameter: float,
    bend_radius: float,
    tail_length: float,
) -> list[tuple[float, float]]:
    """Centreline of one crosstie end, from the shaft to the tip of the hook.

    ``outward`` points from the core to the face; ``toward_tail`` points along
    the face to the side the hook curls to (the shaft is on the other side).
    A single bar, or two bars stacked inward, get the normal hook around the bar
    against the face. A bundle of three or four gets a sharp corner, a flat run
    across its two outer bars, then a 45-degree bend toward the core, so the
    extension leaves the bundle diagonally, away from the shaft.
    """
    hook_angle = CODE.drawing.hook_angle_degrees
    if count < BUNDLE_FLAT_HOOK_MIN:
        end = (
            bar[0] - bend_radius * toward_tail[0],
            bar[1] - bend_radius * toward_tail[1],
        )
        normal = (-outward[1], outward[0])
        side = 1 if toward_tail[0] * normal[0] + toward_tail[1] * normal[1] > 0 else -1
        return [
            end,
            *_hook_points_dxf(end, outward, hook_angle, side, bend_radius, tail_length),
        ]

    half = bar_diameter / 2.0
    first = (bar[0] - half * toward_tail[0], bar[1] - half * toward_tail[1])
    second = (bar[0] + half * toward_tail[0], bar[1] + half * toward_tail[1])
    corner = (
        first[0] - bend_radius * toward_tail[0] + bend_radius * outward[0],
        first[1] - bend_radius * toward_tail[1] + bend_radius * outward[1],
    )
    flat_end = (
        second[0] + bend_radius * outward[0],
        second[1] + bend_radius * outward[1],
    )
    normal = (-toward_tail[1], toward_tail[0])
    side = 1 if -(outward[0] * normal[0] + outward[1] * normal[1]) > 0 else -1
    return [
        corner,
        flat_end,
        *_hook_points_dxf(
            flat_end, toward_tail, hook_angle - 90.0, side, bend_radius, tail_length
        ),
    ]


def _crosstie_bars(
    layout: list[tuple[float, float, int]],
    left: float,
    bottom: float,
    scale: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    inner_tie_style: str = "crossties",
) -> list[TieBar]:
    """Describe the interior ties, each with its own stacking level.

    ``inner_tie_style`` is ``"crossties"`` (one tie with a hook at each end per
    pair of opposite bars) or ``"hoops"`` (closed hoops, each enclosing two
    neighbouring bar positions on opposite faces; an unpaired position keeps a
    crosstie). Ties parallel to Y lie below ties parallel to X, and within one
    direction each later tie sits slightly higher than the earlier one.
    """
    sites = {(site.x, site.y): site for site in _rect_bar_sites(layout, inner_tie_style)}
    points = list(sites)
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs, horizontal_ys = _interior_tie_coordinates(points)
    bar_diameter = main_bar_diameter * scale
    bend_radius = (main_bar_diameter + tie_bar_diameter) / 2.0 * scale
    tail_length = _hook_tail_length_dxf(tie_bar_diameter) * scale

    def to_drawing(x: float, y: float) -> tuple[float, float]:
        """Convert ideal section coordinates into drawing coordinates."""
        return left + x * scale, bottom + y * scale

    def outer_shift(site: BarSite) -> float:
        """Distance from a position to the centre of its bar at the hoop corner."""
        return bar_diameter / 2.0 if site.count >= BUNDLE_FLAT_HOOK_MIN else 0.0

    def crosstie(first: BarSite, last: BarSite, axis: tuple[float, float]) -> list:
        """Path of one crosstie from the ``first`` face position to the ``last``."""
        ends = []
        for site, outward in ((first, (-axis[0], -axis[1])), (last, axis)):
            toward_tail = (
                -site.shaft_side * site.along[0],
                -site.shaft_side * site.along[1],
            )
            ends.append(
                _crosstie_end_points(
                    to_drawing(site.x, site.y),
                    site.count,
                    outward,
                    toward_tail,
                    bar_diameter,
                    bend_radius,
                    tail_length,
                )
            )
        return [*reversed(ends[0]), *ends[1]]

    def groups(coordinates: list[float]) -> list[tuple[float, ...]]:
        """Pair neighbouring positions for inner hoops; singles stay crossties."""
        if inner_tie_style != "hoops":
            return [(coordinate,) for coordinate in coordinates]
        paired = [
            tuple(coordinates[index : index + 2]) for index in range(0, len(coordinates), 2)
        ]
        return paired

    bars: list[TieBar] = []
    for base_level, coordinates, vertical in (
        (LEVEL_CROSSTIE_Y, vertical_xs, True),
        (LEVEL_CROSSTIE_X, horizontal_ys, False),
    ):
        tie_groups = groups(coordinates)
        for index, group in enumerate(tie_groups):
            level = base_level + (index + 1) / (len(tie_groups) + 1)
            if len(group) == 1:
                if vertical:
                    first, last = sites[(group[0], y_min)], sites[(group[0], y_max)]
                    path = crosstie(first, last, (0.0, 1.0))
                else:
                    first, last = sites[(x_min, group[0])], sites[(x_max, group[0])]
                    path = crosstie(first, last, (1.0, 0.0))
                bars.append(TieBar(path, level, "CROSSTIES"))
                continue

            low, high = group
            if vertical:
                hook_site = sites[(low, y_max)]
                c_left = to_drawing(low, y_min)[0] - outer_shift(sites[(low, y_min)])
                c_right = to_drawing(high, y_min)[0] + outer_shift(sites[(high, y_min)])
                c_bottom, c_top = to_drawing(low, y_min)[1], to_drawing(low, y_max)[1]
            else:
                hook_site = sites[(x_min, high)]
                c_bottom = to_drawing(x_min, low)[1] - outer_shift(sites[(x_min, low)])
                c_top = to_drawing(x_min, high)[1] + outer_shift(sites[(x_min, high)])
                c_left, c_right = to_drawing(x_min, low)[0], to_drawing(x_max, low)[0]
            bars.extend(
                _rect_hoop_bars(
                    c_left,
                    c_bottom,
                    c_right,
                    c_top,
                    bend_radius,
                    tail_length,
                    bar_diameter,
                    _hook_corner_neighbours(hook_site, vertical),
                    level,
                    level + 0.4 / (len(tie_groups) + 1),
                    "CROSSTIES",
                )
            )
    return bars


def _visible_tie_shapes(
    bars: list[TieBar], bar_thickness: float
) -> list[tuple[TieBar, object]]:
    """Return each bar's outline shape with the parts hidden under higher bars removed."""
    full_shapes = [
        LineString(bar.path).buffer(
            bar_thickness / 2.0, cap_style="flat", join_style="round"
        )
        for bar in bars
    ]
    # Shrink the cutting shapes a hair so bars that merely touch end-to-end
    # (e.g. hoop body and its last-laid end) are not sliced by rounding noise.
    tolerance = bar_thickness * 1e-3
    visible = []
    for bar, shape in zip(bars, full_shapes):
        higher = [
            other.buffer(-tolerance)
            for other_bar, other in zip(bars, full_shapes)
            if other_bar.level > bar.level
        ]
        visible.append((bar, shape.difference(unary_union(higher)) if higher else shape))
    return visible


def _draw_tie_bars(modelspace, bars: list[TieBar], bar_thickness: float) -> None:
    """Draw the visible outline of every tie bar as closed polylines."""
    for bar, shape in _visible_tie_shapes(bars, bar_thickness):
        for polygon in getattr(shape, "geoms", [shape]):
            if polygon.geom_type != "Polygon" or polygon.is_empty:
                continue
            for ring in (polygon.exterior, *polygon.interiors):
                modelspace.add_lwpolyline(
                    list(ring.coords)[:-1],
                    close=True,
                    dxfattribs={"layer": bar.layer, "lineweight": 18},
                )


def _draw_column_section(
    modelspace,
    row: pd.Series,
    x_center: float,
    y_center: float,
    drawing_width: float,
    drawing_height: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str = "crossties",
) -> None:
    """Draw a scaled column section, bar bundles, hoops, inner ties, and hooks."""
    diameter = (
        float(row["Diameter (mm)"])
        if pd.notna(row["Diameter (mm)"])
        else 0.0
    )
    is_circular = diameter > 0
    width = diameter if is_circular else float(row["Width (mm)"])
    height = diameter if is_circular else float(row["Depth (mm)"])
    scale = min(drawing_width / width, drawing_height / height)
    scaled_width, scaled_height = width * scale, height * scale
    left, bottom = x_center - scaled_width / 2.0, y_center - scaled_height / 2.0
    right, top = left + scaled_width, bottom + scaled_height
    tie_offset = cover * scale
    tie_thickness = tie_bar_diameter * scale
    bar_radius = main_bar_diameter * scale / 2.0

    if is_circular:
        modelspace.add_circle(
            (x_center, y_center),
            diameter * scale / 2.0,
            dxfattribs={"layer": "CONCRETE", "lineweight": 25},
        )
    else:
        modelspace.add_lwpolyline(
            [(left, bottom), (right, bottom), (right, top), (left, top)],
            close=True,
            dxfattribs={"layer": "CONCRETE", "lineweight": 25},
        )
    layout = _column_layout_from_report(
        row, main_bar_diameter, tie_bar_diameter, cover, is_smrf
    )

    if is_circular:
        center = (x_center, y_center)
        modelspace.add_circle(
            center,
            diameter * scale / 2.0 - tie_offset,
            dxfattribs={"layer": "TIES", "lineweight": 18},
        )
        modelspace.add_circle(
            center,
            diameter * scale / 2.0 - tie_offset - tie_thickness,
            dxfattribs={"layer": "TIES", "lineweight": 18},
        )
        sites = _circular_bar_sites(layout)
        origin = center
    else:
        sites = _rect_bar_sites(layout, inner_tie_style)
        origin = (left, bottom)
        hook_corner = min(
            (site for site in sites if site.is_corner),
            key=lambda site: (site.x, -site.y),  # top-left corner, where the hoop closes
        )
        # Hoop and inner ties are drawn together so the stacking order
        # (inner ties over hoop, x-ties over y-ties, last hoop end on top)
        # is resolved across ALL bars at once.
        tie_bars = _hoop_tie_bars(
            left,
            bottom,
            right,
            top,
            cover,
            main_bar_diameter,
            tie_bar_diameter,
            scale,
            hook_corner_count=hook_corner.count,
        ) + _crosstie_bars(
            layout,
            left,
            bottom,
            scale,
            main_bar_diameter,
            tie_bar_diameter,
            inner_tie_style,
        )
        _draw_tie_bars(modelspace, tie_bars, tie_thickness)

    for site in sites:
        for offset_x, offset_y in _bundle_bar_offsets(site, main_bar_diameter):
            modelspace.add_circle(
                (
                    origin[0] + (site.x + offset_x) * scale,
                    origin[1] + (site.y + offset_y) * scale,
                ),
                bar_radius,
                dxfattribs={"layer": "LONGITUDINAL", "lineweight": 18},
            )


def generate_dxf_column_schedule(
    report: pd.DataFrame,
    output_filepath: str,
    main_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str = "crossties",
) -> None:
    """Create one grouped column schedule DXF for the supplied floor report.

    ``inner_tie_style`` is ``"crossties"`` or ``"hoops"`` (closed inner hoops).
    """
    if report.empty:
        raise ValueError("Column design report has no rows to export.")

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    for layer_name, color in (
        ("TABLE", 7),
        ("CONCRETE", 7),
        ("TIES", 7),
        ("CROSSTIES", 7),
        ("HOOKS", 7),
        ("LONGITUDINAL", 7),
    ):
        if layer_name not in doc.layers:
            doc.layers.new(layer_name, dxfattribs={"color": color})
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})
    modelspace = doc.modelspace()

    # Each ETABS column label is a vertical group. Its members are stacked from
    # the highest available floor to the lowest, rather than regrouping by floor.
    grouped_labels = []
    report = report.copy()
    report["_DXF Mark"] = report["Unique Name"].map(_common_column_mark)
    for label, members in report.groupby("_DXF Mark", sort=False):
        elevation_column = next(
            (
                column
                for column in ("Level Elevation (m)", "Level_Elevation_m")
                if column in members.columns
            ),
            None,
        )
        if elevation_column is None:
            members = members.assign(
                _elevation=members["Story"].map(
                    lambda value: _column_story_sort_key(value)[0]
                )
            )
        else:
            members = members.assign(
                _elevation=pd.to_numeric(
                    members[elevation_column], errors="coerce"
                )
            )
        grouped_labels.append(
            (
                label,
                members.sort_values(
                    ["_elevation", "Story", "Unique Name"],
                    ascending=[False, False, True],
                    kind="stable",
                ).drop_duplicates("Unique Name"),
            )
        )
    left_widths = [20.0, 22.0, 25.0]
    detail_width = 90.0
    header_height = 10.0
    drawing_height = 76.0
    data_height = 8.0
    field_names = (
        "SIZE",
        "VERTICAL BARS",
        "JOINT REIN.",
        "CONFINMT",
        "TIES",
    )

    def add_text(text: object, x: float, y: float, height: float, width: float) -> None:
        """Place centered single-line text in a DXF schedule cell."""
        entity = modelspace.add_text(
            str(text),
            dxfattribs={
                "style": "SIMPLEX",
                "height": height,
                "width": 1.0,
                "layer": "TABLE",
            },
        )
        entity.set_placement(
            (x + width / 2.0, y),
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )

    def add_cell(x1: float, y1: float, x2: float, y2: float) -> None:
        """Draw a single schedule-cell boundary."""
        modelspace.add_lwpolyline(
            [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
            close=True,
            dxfattribs={"layer": "TABLE", "lineweight": 18},
        )

    # Arrange the schedule by story so the level properties are written once
    # and shared by every column mark on that level.
    story_values = sorted(
        report["Story"].dropna().astype(str).unique(),
        key=_column_story_sort_key,
        reverse=True,
    )
    labels = list(dict.fromkeys(report["_DXF Mark"].astype(str)))
    story_content_height = drawing_height + len(field_names) * data_height
    story_x_positions = [0.0]
    for width in left_widths:
        story_x_positions.append(story_x_positions[-1] + width)
    for _ in labels:
        story_x_positions.append(story_x_positions[-1] + detail_width)

    y_top = 0.0
    for story_index, story in enumerate(story_values):
        has_header = story_index == 0
        story_header_height = header_height if has_header else 0.0
        story_block_height = story_header_height + story_content_height
        story_rows = report.loc[report["Story"].astype(str).eq(story)]
        by_label = {
            str(row["_DXF Mark"]): row
            for _, row in story_rows.drop_duplicates("_DXF Mark").iterrows()
        }
        block_bottom = y_top - story_block_height
        fc_values = pd.to_numeric(story_rows["f′c (MPa)"], errors="coerce").dropna().unique()
        fc_value = f"{float(fc_values[0]):g}" if len(fc_values) else "-"

        for column_index, title in enumerate(("F'c (MPa)", "FLOOR LEVEL")):
            add_cell(
                story_x_positions[column_index],
                y_top,
                story_x_positions[column_index + 1],
                y_top - story_header_height,
            )
            add_cell(
                story_x_positions[column_index],
                y_top - story_header_height,
                story_x_positions[column_index + 1],
                block_bottom,
            )
            if has_header:
                add_text(
                    title,
                    story_x_positions[column_index],
                    y_top - story_header_height / 2.0,
                    2.0,
                    left_widths[column_index],
                )
        add_cell(
            story_x_positions[2],
            y_top,
            story_x_positions[3],
            y_top - story_header_height,
        )
        add_cell(
            story_x_positions[2],
            y_top - story_header_height,
            story_x_positions[3],
            block_bottom,
        )
        if has_header:
            add_text(
                "MARK",
                story_x_positions[2],
                y_top - story_header_height / 2.0,
                2.0,
                left_widths[2],
            )
        add_text(fc_value, story_x_positions[0],         y_top - story_header_height - story_content_height / 2.0,
        2.8,
        left_widths[0],
        )
        add_text(
        story,
        story_x_positions[1],
        y_top - story_header_height - story_content_height / 2.0,
        2.8,
        left_widths[1],
        )

        for field_index, field_name in enumerate(field_names):
            row_top = (
                y_top
                - story_header_height
                - drawing_height
                - field_index * data_height
            )
            row_bottom = row_top - data_height
            add_cell(story_x_positions[2], row_top, story_x_positions[3], row_bottom)
            add_text(
                field_name,
                story_x_positions[2],
                row_top - data_height / 2.0,
                1.8,
                left_widths[2],
            )

        for label_index, label in enumerate(labels):
            row = by_label.get(label)
            x_left = story_x_positions[3 + label_index]
            x_right = story_x_positions[4 + label_index]
            add_cell(x_left, y_top, x_right, y_top - story_header_height)
            if has_header:
                add_text(
                    label,
                    x_left,
                    y_top - story_header_height / 2.0,
                    1.8,
                    detail_width,
                )
            add_cell(
                x_left,
                y_top - story_header_height,
                x_right,
                y_top - story_header_height - drawing_height,
            )
            if row is not None:
                _draw_column_section(
                    modelspace,
                    row,
                    (x_left + x_right) / 2.0,
                    y_top - story_header_height - drawing_height / 2.0,
                    detail_width * 0.78,
                    drawing_height * 0.82,
                    main_bar_diameter,
                    float(row["Tie Bar Diameter (mm)"]),
                    cover,
                    is_smrf,
                    inner_tie_style,
                )
            else:
                add_text(
                    "-",
                    x_left,
                    y_top - story_header_height - drawing_height / 2.0,
                    2.8,
                    detail_width,
                )

            for field_index, field_name in enumerate(field_names):
                row_top = (
                    y_top
                    - story_header_height
                    - drawing_height
                    - field_index * data_height
                )
                row_bottom = row_top - data_height
                add_cell(x_left, row_top, x_right, row_bottom)
                if row is None:
                    value = "-"
                else:
                    if field_name == "SIZE":
                        diameter = float(row["Diameter (mm)"] or 0.0)
                        value = f"Ø{diameter:.0f}" if diameter > 0 else f"{float(row['Width (mm)']):.0f}x{float(row['Depth (mm)']):.0f}"
                    elif field_name == "VERTICAL BARS":
                        value = f"{int(float(row['Longitudinal Bars']))}-D{main_bar_diameter:g}"
                    elif field_name == "JOINT REIN.":
                        value = (
                            f"D{float(row['Tie Bar Diameter (mm)']):g} @ "
                            f"{CODE.drawing.column_joint_tie_spacing:g}"
                        )
                    elif field_name == "CONFINMT":
                        value = f"D{float(row['Tie Bar Diameter (mm)']):g} @ {float(row['Tie / Spiral Spacing (mm)']):g}"
                    else:
                        value = (
                            f"D{float(row['Tie Bar Diameter (mm)']):g} @ "
                            f"{CODE.drawing.column_general_tie_spacing:g}"
                        )
                add_text(value, x_left, row_top - data_height / 2.0, 1.8, detail_width)
        y_top = block_bottom

    doc.saveas(output_filepath)
    return


INNER_TIE_STYLE_LABELS = {
    "Crossties (one tie with a hook at each end)": "crossties",
    "Closed inner hoops (each enclosing two bar positions)": "hoops",
}


def export_column_cad_drawings(
    output_directory: str | None = None, inner_tie_style: str | None = None
) -> list[str]:
    """Export one stacked all-story column schedule DXF.

    Both arguments are asked for in a dialog when they are not given:
    the output folder, and whether interior ties are drawn as crossties or as
    closed inner hoops.
    """
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_output_directory,
    )

    destination = output_directory or select_output_directory()
    if not destination:
        return []
    if not os.path.isdir(destination):
        raise NotADirectoryError(f"DXF output directory does not exist: {destination}")
    if inner_tie_style is None:
        chosen = select_option(
            "Column Schedule - Interior Ties",
            "How should the interior ties be drawn?",
            list(INNER_TIE_STYLE_LABELS),
        )
        if chosen is None:
            return []  # dialog closed without confirming
        inner_tie_style = INNER_TIE_STYLE_LABELS[chosen]
    if inner_tie_style not in INNER_TIE_STYLES:
        raise ValueError(f"inner_tie_style must be one of {INNER_TIE_STYLES}.")

    try:
        workbook = xw.Book.caller()
    except Exception:
        workbook = xw.books.active
    sheet = workbook.sheets["COLUMN DESIGN"]
    used_last_row = sheet.used_range.last_cell.row
    header_values = sheet.range((9, 2), (9, sheet.used_range.last_cell.column)).value
    last_column = max(
        index + 2 for index, value in enumerate(header_values) if value is not None
    )
    headers = sheet.range((9, 2), (9, last_column)).value
    values = sheet.range((10, 2), (used_last_row, last_column)).options(ndim=2).value
    report = pd.DataFrame(values, columns=headers).dropna(how="all")
    for column in ("Column Label", "Unique Name", "Story", "End"):
        report[column] = report[column].ffill()
    report = report.dropna(subset=["Unique Name"]).drop_duplicates(
        subset=["Unique Name"], keep="first"
    )
    if report.empty:
        raise ValueError("COLUMN DESIGN contains no designed columns to export.")

    overwrite_sheet = workbook.sheets["OVERWRITES"]
    main_bar_diameter = _numeric(
        overwrite_sheet.range("I10").value, "I10 (column main bar)", "OVERWRITES"
    )
    cover = _numeric(
        overwrite_sheet.range("I12").value, "I12 (column cover)", "OVERWRITES"
    )
    is_smrf = overwrite_sheet.range("F3").value is True
    os.makedirs(destination, exist_ok=True)
    output_path = os.path.join(destination, "Column_Schedule.dxf")
    with LoadingWindow("Generating DXF column schedules and reinforced sections..."):
        generate_dxf_column_schedule(
            report,
            output_path,
            main_bar_diameter,
            cover,
            is_smrf,
            inner_tie_style,
        )
    return [output_path]


if __name__ == "__main__":
    # Test 1: Rectangular Column
    print("--- Rectangular Column Test ---")
    rect_col = ColumnFlexureDesign(
        width=400.0,
        height=500.0,
        diameter=0.0,
        fc=28.0,
        fy=415.0,
        fyt=415.0,
        dmain=25.0,
        dties=10.0,
        cc=40.0,
        m_x=150e6,
        m_y=100e6,
        axial_load=1500e3,  # e.g., 1500 kN applied axial load
        shape="rectangular",
    )

    rect_results = rect_col.perform_column_flexure_design(initial_bars=4)
    for key, val in rect_results.items():
        if isinstance(val, float):
            print(f"{key}: {val:.2f}")
        else:
            print(f"{key}: {val}")

    print("\n--- Circular Column Test ---")
    # Test 2: Circular Column
    circ_col = ColumnFlexureDesign(
        width=0.0,
        height=0.0,
        diameter=450.0,
        fc=35.0,
        fy=420.0,
        fyt=420.0,
        dmain=28.0,
        dties=10.0,
        cc=40.0,
        m_x=200e6,
        m_y=0.0,
        axial_load=2000e3,  # e.g., 2000 kN applied axial load
        shape="circular",
    )

    circ_results = circ_col.perform_column_flexure_design(initial_bars=6)
    for key, val in circ_results.items():
        if isinstance(val, float):
            print(f"{key}: {val:.2f}")
        else:
            print(f"{key}: {val}")
