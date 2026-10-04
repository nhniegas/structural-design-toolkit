"""ACI 318M-14 reinforced-concrete column design, SMRF checks and schedules.

The terminal workflow (``sdt columns``) is in ``design/concrete_workflow.py``;
this module designs from tables (``design_columns``) and writes the results
file, the DXF schedule and the calculation report. Its tables use mm, MPa,
kN and kN-m; section-library forces are converted at the calculation boundary.
"""

import math
import os
import re
import numpy as np
import pandas as pd
import ezdxf
from dataclasses import dataclass
from design.code_config import CODE, AciCode
from design.column_interaction import clip_moments, section_rings
from scipy.optimize import brentq
from shapely.geometry import LineString
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
from utilities._calc_report import (
    MemberReport,
    ReportTable,
    Tex,
    build_calc_report,
    is_blank,
    number,
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
            code: ACI constants (see code_config.py).
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
    if isinstance(geometry, LazyColumnSection):
        geometry = geometry.geometry()
    cached = getattr(geometry, "_column_concrete_section", None)
    if cached is None:
        cached = ConcreteSection(geometry)
        setattr(geometry, "_column_concrete_section", cached)
    return cached


def _clean_table(frame: pd.DataFrame | None) -> pd.DataFrame:
    """A copy of a table with stripped column names (empty when None)."""
    if frame is None:
        return pd.DataFrame()
    frame = frame.copy()
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
    """Extract the shared column mark from a floor-qualified unique name.

    ``2F - C1`` and ``2GC-1`` both give ``C1``; a planted column ``PD2PC-1A``
    gives ``PC1A``.
    """
    text = _normalize_object_name(unique_name)
    match = re.search(r"(?:GC|(P)C|C)-?(\d+[A-Z]*)\s*$", text, flags=re.IGNORECASE)
    if not match:
        return text
    return f"{'P' if match.group(1) else ''}C{match.group(2)}".upper()


def _story_order_from_stacks(
    connectivity: pd.DataFrame, story_of: dict[str, str]
) -> list[str]:
    """Stories from the bottom up, read from how the columns stand on each other.

    ETABS draws a column from its bottom joint (I) to its top joint (J), so a
    column whose bottom joint is another column's top joint is one story higher.
    Story names are free text (``UG``, ``PD1``, ``2F``) and cannot be ordered
    reliably by name; stories the stacks do not relate fall back to the name.
    """
    stories = list(dict.fromkeys(str(story) for story in story_of.values()))
    if connectivity.empty or not {"UniqueName", "UniquePtI", "UniquePtJ"} <= set(
        connectivity.columns
    ):
        return sorted(stories, key=_column_story_sort_key)
    ends = {
        _normalize_object_name(row.UniqueName): (
            _normalize_object_name(row.UniquePtI), _normalize_object_name(row.UniquePtJ)
        )
        for row in connectivity.itertuples()
    }
    story_at_top = {
        ends[member][1]: str(story) for member, story in story_of.items() if member in ends
    }
    above: dict[str, set[str]] = {story: set() for story in stories}
    for member, story in story_of.items():
        below = story_at_top.get(ends.get(member, ("", ""))[0])
        if below is not None and below != str(story):
            above[below].add(str(story))
    waiting = {story: 0 for story in stories}
    for higher in above.values():
        for story in higher:
            waiting[story] += 1
    ordered: list[str] = []
    while len(ordered) < len(stories):
        ready = [s for s in stories if s not in ordered and waiting[s] == 0]
        if not ready:  # stacks contradict each other: finish by name
            ready = [s for s in stories if s not in ordered]
            ordered.extend(sorted(ready, key=_column_story_sort_key))
            break
        lowest = min(ready, key=_column_story_sort_key)
        ordered.append(lowest)
        for story in above[lowest]:
            waiting[story] -= 1
    return ordered


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


def _section_bending_angle(moment_2: float, moment_3: float) -> float:
    """Bending angle of the section for moments about ETABS local 2 and local 3.

    ETABS puts the section depth (t3, ``Depth``) along local 2 and the width
    (t2, ``Width``) along local 3. M3 therefore bends the section over its
    depth, which is angle 0 here, and M2 bends it over its width, angle pi/2.
    """
    return math.atan2(moment_2, moment_3)


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


def _build_column_section(
    row: pd.Series,
    n_bars: int,
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    bundle_layout: list[tuple[float, float, int]] | None = None,
) -> tuple[ColumnFlexureDesign, object]:
    """Create a column engine and reinforced geometry for a frame-data row.

    A row with a ``DesignCover`` value uses it in place of ``cover``.
    """
    member = str(row["UniqueName"])
    own_cover = row.get("DesignCover")
    if own_cover is not None and pd.notna(own_cover):
        cover = float(own_cover)
    diameter = row.get("Diameter")
    is_circular = pd.notna(diameter) and float(diameter) > 0
    width = 0.0 if is_circular else _numeric(row["Width"], "Width", member)
    height = 0.0 if is_circular else _numeric(row["Depth"], "Depth", member)
    diameter_value = _numeric(diameter, "Diameter", member) if is_circular else 0.0

    # The section depends only on its size, materials, bars and cover: build each
    # one once (meshing is slow) and share it between members, joints and checks.
    key = (width, height, diameter_value, _numeric(row["f'c"], "f'c", member),
           _numeric(row["fy"], "fy", member), _numeric(row["fys"], "fys", member),
           float(dmain), float(dties), float(cover), bool(is_smrf), int(n_bars),
           tuple(tuple(float(x) for x in item) for item in (bundle_layout or ())))
    if key not in _SECTION_CACHE:
        _SECTION_CACHE[key] = _new_column_section(
            width, height, diameter_value, is_circular, row, member, n_bars, dmain, dties,
            cover, is_smrf, bundle_layout)
    return _SECTION_CACHE[key]


_SECTION_CACHE: dict[tuple, tuple] = {}


def _new_column_section(width, height, diameter_value, is_circular, row, member, n_bars,
                        dmain, dties, cover, is_smrf, bundle_layout):
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
    return engine, LazyColumnSection(engine, n_bars, bundle_layout)


class LazyColumnSection:
    """The reinforced geometry of a column, built (and meshed) on first use only.

    The bar layout search and the interaction surface need only the outline and
    the bars (``column_interaction.layout_section_data``); the concreteproperties
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
    """Check clear distances between equivalent circular bar-bundle envelopes.

    Column bars need a clear spacing of at least 40 mm, 1.5 db and 4/3 of the
    aggregate (NSCP 425.2.3 / ACI 25.2.3); a bundle counts as one bar of the
    same area (ACI 25.6.1.6).
    """
    if aggregate_size is None:
        aggregate_size = CODE.column_strength.default_aggregate_size
    equivalent_diameters = np.array([bar_diameter * math.sqrt(count) for count in bundle_counts])
    points = np.asarray(positions, dtype=float).reshape(-1, 2)
    if len(points) < 2:
        return True
    distance = np.hypot(points[:, None, 0] - points[None, :, 0],
                        points[:, None, 1] - points[None, :, 1])
    mean_diameter = (equivalent_diameters[:, None] + equivalent_diameters[None, :]) / 2.0
    minimum_clear = _column_clear_spacing(mean_diameter, aggregate_size)
    upper = np.triu_indices(len(points), 1)
    return bool(np.all(distance[upper] + 1e-8 >= (mean_diameter + minimum_clear)[upper]))


def _column_clear_spacing(bar_diameter, aggregate_size: float, code: AciCode = CODE):
    """Minimum clear spacing of longitudinal column bars (NSCP 425.2.3 / ACI 25.2.3)."""
    strength = code.column_strength
    return np.maximum(
        np.maximum(strength.min_clear_spacing, strength.clear_spacing_bar_multiple * bar_diameter),
        code.beam_detailing.aggregate_spacing_factor * aggregate_size,
    )


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
        min_pitch = float(_column_clear_spacing(bar_diameter, aggregate_size, engine.code)) \
            + bar_diameter
        max_nx = max(min_nx, int(span_x // min_pitch))
        max_ny = max(min_ny, int(span_y // min_pitch))
        # A square column gets the same bars on all four faces: nothing on site
        # tells its faces apart, so an unequal cage could be placed turned 90 degrees.
        is_square = abs(engine.width - engine.height) <= 1e-6

        for nx in range(min_nx, max_nx + 1):
            if span_x / nx > max_spacing + 1e-8 or span_x / nx < min_pitch - 1e-8:
                continue
            for ny in range(min_ny, max_ny + 1):
                if is_square and ny != nx:
                    continue
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


# Each member's force table, indexed once by combination: (table, {combo: (I row, J row)}).
# The table itself is kept so its id stays unique while the entry exists.
_END_FORCE_INDEX: dict[int, tuple[pd.DataFrame, dict]] = {}


def _clear_end_force_index() -> None:
    _END_FORCE_INDEX.clear()
    _DEMAND_INDEX.clear()


def _end_force_rows(forces: pd.DataFrame) -> dict:
    entry = _END_FORCE_INDEX.get(id(forces))
    if entry is not None and entry[0] is forces:
        return entry[1]
    stations = pd.to_numeric(forces["Station"], errors="coerce")
    table = forces.copy()
    table["Station"] = stations
    names = table["Combo"].astype(str)
    index: dict = {combo: None for combo in names.unique()}
    valid = table[stations.notna()]
    if not valid.empty:
        grouped = valid["Station"].groupby(names[valid.index], sort=False)
        lowest, highest = grouped.idxmin(), grouped.idxmax()
        for combo in lowest.index:
            index[combo] = (valid.loc[lowest[combo]], valid.loc[highest[combo]])
    _END_FORCE_INDEX[id(forces)] = (forces, index)
    return index


def _column_force_at_end(
    forces: pd.DataFrame, combo: str, at_i_end: bool
) -> pd.Series:
    """Select the first/last station for a member and load combination.

    ETABS frame stations are treated as increasing from connectivity I to J.
    The member's table is indexed by combination the first time it is used.
    """
    index = _end_force_rows(forces)
    if str(combo) not in index:
        raise ValueError(f"No column force stations were found for combo {combo!r}.")
    rows = index[str(combo)]
    if rows is None:
        raise ValueError(f"Column force stations for combo {combo!r} are not numeric.")
    return (rows[0] if at_i_end else rows[1]).copy()


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

    from design.column_interaction import surface_for

    surface = surface_for(engine, section, bar_layout) if USE_INTERACTION_SURFACE else None
    if surface is not None:
        return _evaluate_on_surface(member, force_rows, surface, engine, n_bars, dmain,
                                    stop_at_first_failure, progress, strength_limits)

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
            theta = _section_bending_angle(m2_kNm, m3_kNm)
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


USE_INTERACTION_SURFACE = True  # False: the per-demand solver (Mn at Pn = Pu, then phi)


def column_demands(member: str, force_rows: pd.DataFrame) -> list[tuple[str, str, float, float, float]]:
    """(combo, end, Pu kN, Mu2 kN-m, Mu3 kN-m) of every combination at both ends."""
    entry = _DEMAND_INDEX.get(id(force_rows))
    if entry is not None and entry[0] is force_rows:
        return entry[1]
    index = _end_force_rows(force_rows)
    out = []
    for combo in sorted(force_rows["Combo"].dropna().astype(str).unique()):
        rows = index.get(combo)
        if rows is None:
            raise ValueError(f"Column force stations for combo {combo!r} are not numeric.")
        for endpoint, row in (("I", rows[0]), ("J", rows[1])):
            out.append((combo, endpoint, _numeric(row["P"], "P", member),
                        _numeric(row["M2"], "M2", member), _numeric(row["M3"], "M3", member)))
    _DEMAND_INDEX[id(force_rows)] = (force_rows, out)
    return out


# Each member's demands, read once from its force table (cleared with the end-force index).
_DEMAND_INDEX: dict[int, tuple[pd.DataFrame, list]] = {}


def _evaluate_on_surface(member, force_rows, surface, engine, n_bars, dmain,
                         stop_at_first_failure, progress, strength_limits):
    """Flexure and axial checks on the layout's interaction surface (ACI design
    surface, phi Pn = Pu). Every demand is looked up at once (one vectorized call).
    While searching for a layout (``stop_at_first_failure``) the convex hull
    vertices of the demands are checked first: they are the demands most likely
    to fail, so a layout that cannot work is rejected before the others are
    looked up. The others are then confirmed, because the phi-scaled surface is
    not strictly convex where phi changes from 0.65 to 0.90."""
    from design.column_interaction import hull_vertices

    demands = column_demands(member, force_rows)
    data = np.array([[p, m2, m3] for _, _, p, m2, m3 in demands], dtype=float).reshape(-1, 3)
    # Section moments: Mx = M3, My = -M2 (column_interaction.demand_moments), in N and N-mm.
    points = np.column_stack([data[:, 0] * 1e3, data[:, 2] * 1e6, -data[:, 1] * 1e6])
    tension_capacity = (engine.code.strength.tension_controlled * n_bars * math.pi * dmain**2
                        / 4.0 * engine.fy / 1000.0)
    if progress is not None and demands:
        progress(member, "I and J", "Flexure and axial (P-M)", demands[0][0])
    if stop_at_first_failure and len(demands) > 4:
        hull = hull_vertices(points)
        groups = [hull, np.setdiff1d(np.arange(len(demands)), hull)]
    else:
        groups = [np.arange(len(demands))]
    checks, passed = [], True
    for indices in groups:
        indices = np.asarray(indices, dtype=int)
        if not len(indices):
            continue
        pu, mx, my = points[indices].T
        capacity, phi = surface.capacities(pu, mx, my)
        demand = np.hypot(data[indices, 1], data[indices, 2])
        capacity_kNm = capacity / 1e6
        with np.errstate(divide="ignore", invalid="ignore"):
            utilization = np.where(
                capacity_kNm > 0, demand / capacity_kNm,
                np.where((demand <= 1e-9) & ~np.isnan(phi), 0.0, math.inf))
        moment_pass = utilization <= 1.0
        axial = data[indices, 0]
        axial_pass = np.where(axial >= 0, pu <= surface.p_cap,
                              np.abs(axial) <= tension_capacity)
        for k, index in enumerate(indices):
            combo, endpoint, axial_kN, m2_kNm, m3_kNm = demands[int(index)]
            ok_m, ok_p = bool(moment_pass[k]), bool(axial_pass[k])
            f = float(phi[k])
            checks.append({
                "UniqueName": member, "Combo": combo, "End": endpoint, "Pu_kN": axial_kN,
                "Mu2_kNm": m2_kNm, "Mu3_kNm": m3_kNm, "Mu_resultant_kNm": float(demand[k]),
                "Mn_kNm": float(capacity_kNm[k]) / f if f == f and f > 0 else np.nan,
                "phi_Mn_kNm": float(capacity_kNm[k]), "Phi": f,
                "phi_Pn_max_kN": surface.p_cap / 1000.0, "phi_Pn_tension_kN": tension_capacity,
                "Flexure_Utilization": float(utilization[k]),
                "Axial_Check": "PASS" if ok_p else "FAIL",
                "Flexure_Check": "PASS" if ok_m else "FAIL",
                "Strength_Check": "PASS" if ok_m and ok_p else "FAIL",
            })
        passed = passed and bool(moment_pass.all() and axial_pass.all())
        if stop_at_first_failure and not passed:
            return False, checks, strength_limits
    if not stop_at_first_failure:
        order = {(c, e): i for i, (c, e, *_rest) in enumerate(demands)}
        checks.sort(key=lambda check: order[(check["Combo"], check["End"])])
    return passed, checks, strength_limits


def _strong_column_hull_combos(combos: list[str], columns: list[str], joint: str,
                               force_groups: dict, get_end_info) -> list[str]:
    """The combinations that can give a joint its lowest sum of column strengths.

    At a joint, for one frame direction, the sum of the column nominal strengths
    depends on the combination only through each column's axial load, and each
    column's Mn(P) along a fixed direction is concave (the nominal interaction
    surface is convex). The sum is therefore concave in (P_1, P_2) and its minimum
    over the combinations lies at a vertex of their convex hull: one or two
    columns give 2 or a few combinations instead of all of them. The beam strengths
    and the joint shear do not depend on the combination. Every combination is
    kept when an axial load cannot be read (the loop then reports what is missing).
    """
    from design.column_interaction import hull_vertices

    if len(combos) <= 3 or not columns:
        return list(combos)
    points = []
    try:
        ends = {column: get_end_info(column, joint, False)[0] == "I" for column in columns}
        for combo in combos:
            points.append([
                _numeric(_column_force_at_end(force_groups[column], combo, ends[column])["P"],
                         "P", column)
                for column in columns])
    except (KeyError, ValueError, IndexError):
        return list(combos)
    points = np.asarray(points, dtype=float)
    if points.shape[1] == 1:  # one column: the smallest and largest axial load
        keep = {int(np.argmin(points[:, 0])), int(np.argmax(points[:, 0]))}
    else:
        keep = set(int(i) for i in hull_vertices(points))
    return [combo for index, combo in enumerate(combos) if index in keep]


def _smrf_so_limit(code: AciCode, hx: float | None = None) -> float:
    """Hoop spacing limit 'so' of ACI 18.7.5.3: 100 + (350 - hx)/3, kept within 100..150 mm."""
    cfg = code.column_seismic
    hx = cfg.hx_assumed if hx is None else hx
    so = cfg.so_min + (cfg.so_hx_reference - hx) / cfg.so_hx_divisor
    return min(cfg.so_max, max(cfg.so_min, so))


def _minimum_tie_diameter(engine: ColumnFlexureDesign, bar_layout=None) -> float:
    """Smallest permitted transverse bar diameter (NSCP 425.7.2.2 / 425.7.3.2), in mm.

    Ties are 12 mm for bars larger than 32 mm and for BUNDLED bars, 10 mm otherwise.
    """
    cfg = engine.code.column_transverse
    if engine.shape == "circular":
        return cfg.spiral_diameter_min
    bundled = bar_layout is not None and any(count > 1 for _, _, count in bar_layout)
    if engine.dmain > cfg.large_bar_threshold or bundled:
        return cfg.tie_diameter_large_bars
    return cfg.tie_diameter_small_bars


def _confined_core(engine: ColumnFlexureDesign) -> tuple[float, float]:
    """bc along the width and along the depth: the core to the OUTSIDE edges of the
    hoops (ACI 18.7.5.4 notation), in mm."""
    return engine.width - 2.0 * engine.cc, engine.height - 2.0 * engine.cc


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
            len(bar_layout),
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
        core_width, core_height = _confined_core(engine)
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

    minimum_tie_diameter = _minimum_tie_diameter(engine, bar_layout)
    seismic_cfg = engine.code.column_seismic
    detailing_passes = (
        not is_smrf
        or (
            short_dimension >= seismic_cfg.min_dimension
            and short_dimension / long_dimension >= seismic_cfg.min_aspect_ratio
            and confinement_passes
            and transverse["Transverse_Spacing_Check"] == "PASS"
            # "N/A for continuous spiral": a spiral supports every bar
            and support_check.startswith(("PASS", "N/A"))
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
    n_bars: int,  # bar positions (a bundle counts once), for kn
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
        # Dc and Ach are measured to the OUTSIDE edge of the spiral (ACI 25.7.3.3,
        # 18.7.5.4): rho_s = 4 Asp / (Dc s), the ratio of spiral to core volume.
        core_diameter = engine.diameter - 2.0 * engine.cc
        if core_diameter <= 0:
            raise ValueError("Circular column core diameter must be positive.")
        ach = math.pi * core_diameter ** 2 / 4.0
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
            "Required_Legs_X": 1,
            "Required_Legs_Y": 1,
            "Alternating_Support_Check": "N/A for continuous spiral",
        }

    # bc and Ach are measured to the OUTSIDE edges of the hoops (ACI 18.7.5.4,
    # NSCP 418.7.5.4 notation): Ash >= ratio * s * bc.
    core_width, core_height = _confined_core(engine)
    if min(core_width, core_height) <= 0:
        raise ValueError("Rectangular column core dimensions must be positive.")
    ach = core_width * core_height

    # kn = nl / (nl - 2): nl counts the bars or BUNDLES laterally supported around
    # the perimeter (ACI 18.7.5.4), so a bundle is one; callers pass positions.
    nl = max(n_bars, 4)
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
        # Legs counted along the X edge run parallel to Y and confine the core
        # width; legs counted along the Y edge confine the core depth.
        "Required_Legs_X": required_legs_x,
        "Required_Legs_Y": required_legs_y,
        "Alternating_Support_Check": "Pending post-check",
    }


def _alternating_support_legs(
    engine: ColumnFlexureDesign, bundle_layout: list[tuple[float, float, int]]
) -> tuple[int, int]:
    """Tie legs needed along the X edge and along the Y edge to support alternate bars.

    The two hoop legs hold the corner bars; every second bar position between
    them needs a crosstie leg. A circular column has a continuous spiral instead.
    """
    if engine.shape == "circular":
        return 1, 1
    positions = [(x, y) for x, y, _ in bundle_layout]
    min_x, max_x = min(x for x, _ in positions), max(x for x, _ in positions)
    min_y, max_y = min(y for _, y in positions), max(y for _, y in positions)
    tolerance = 1e-6
    bars_along_x_edge = max(
        len({round(x, 5) for x, y in positions if abs(y - face) <= tolerance})
        for face in (min_y, max_y)
    )
    bars_along_y_edge = max(
        len({round(y, 5) for x, y in positions if abs(x - face) <= tolerance})
        for face in (min_x, max_x)
    )
    return (
        max(2, 2 + math.ceil(max(0, bars_along_x_edge - 2) / 2)),
        max(2, 2 + math.ceil(max(0, bars_along_y_edge - 2) / 2)),
    )


def _bars_per_edge(
    engine: ColumnFlexureDesign, bundle_layout: list[tuple[float, float, int]]
) -> tuple[int | None, int | None]:
    """Number of bars on one X edge and on one Y edge (corner bars count on both)."""
    if engine.shape == "circular":
        return None, None
    min_x = min(x for x, _, _ in bundle_layout)
    min_y = min(y for _, y, _ in bundle_layout)
    return (
        sum(count for x, y, count in bundle_layout if abs(y - min_y) <= 1e-6),
        sum(count for x, y, count in bundle_layout if abs(x - min_x) <= 1e-6),
    )


def _post_check_alternating_support(
    engine: ColumnFlexureDesign,
    bundle_layout: list[tuple[float, float, int]],
    provided_legs: int,
) -> tuple[int, int, str]:
    """Add transverse legs as needed to support alternate perimeter bar groups."""
    if engine.shape == "circular":
        return provided_legs, 0, "N/A for continuous spiral"

    required_legs = max(_alternating_support_legs(engine, bundle_layout))
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


def _column_stacks(ends: dict[str, tuple[str, str]]) -> list[list[str]]:
    """Group columns that stand on top of each other, top level first.

    ``ends`` maps each column to its ``(bottom joint, top joint)``. A column is
    directly above another when its bottom joint is the other's top joint.
    """
    by_bottom_joint = {bottom: member for member, (bottom, _) in ends.items()}
    by_top_joint = {top: member for member, (_, top) in ends.items()}
    stacks = []
    for member, (_, top) in ends.items():
        if top in by_bottom_joint:
            continue  # another column stands on this one, so it is not the top
        stack, current = [], member
        while current is not None and current not in stack:
            stack.append(current)
            current = by_top_joint.get(ends[current][0])
        stacks.append(stack)
    return stacks


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
    confinement_legs: int | tuple[int, int],
    is_smrf: bool,
    bundle_layout: list[tuple[float, float, int]] | None = None,
    progress=None,
    clear_height: float | None = None,
    beam_moment_limits: dict[tuple[str, str], float] | None = None,
) -> tuple[list[dict], int]:
    """Check column shear in both local directions and size transverse legs.

    ``clear_height`` is the clear height lu between the beams (mm) for the
    capacity shear Ve of a special moment frame column. ``beam_moment_limits``
    maps (shear direction "V2"/"V3", end "I"/"J") to the end moment the beams can
    deliver to this column, kN-m: ACI 18.7.6.1.1 lets Ve stop at the shear that
    the joint strengths (beam Mpr) can develop.

    The function compares analysis shear with the capacity-based probable-moment
    shear, then selects, for each direction, a leg count sufficient for all force
    combinations. ``confinement_legs`` is the starting count: one number for both
    directions, or ``(legs along the X edge, legs along the Y edge)``.
    Forces are kN, moments kN-m, dimensions mm, and stresses MPa at the interface.

    Returns the check rows (each with the legs provided in its direction) and
    the larger of the two leg counts.
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
    if isinstance(confinement_legs, (tuple, list)):
        legs_along_x, legs_along_y = (max(1, int(value)) for value in confinement_legs)
    else:
        legs_along_x = legs_along_y = max(1, int(confinement_legs))
    # V2 acts along local 2, the depth. It is carried by the legs that run
    # parallel to the depth, which are the ones counted along the X edge.
    # V3 acts along the width and uses the legs counted along the Y edge.
    starting_legs = {"V2": legs_along_x, "V3": legs_along_y}
    provided_legs = dict(starting_legs)

    station_values = pd.to_numeric(forces["Station"], errors="coerce").dropna()
    if station_values.empty or station_values.max() <= station_values.min():
        raise ValueError(
            f"Column {member} needs distinct numeric end stations for capacity shear."
        )
    # Ve = (Mpr,top + Mpr,bottom) / lu over the CLEAR height (ACI 18.7.6.1.1); the
    # station range is the joint-to-joint length, used when lu is not known.
    clear_length = float(station_values.max() - station_values.min())
    if clear_height is not None and 0 < clear_height < clear_length:
        clear_length = float(clear_height)
    if engine.shape == "rectangular":
        axis_dimensions = {
            # (breadth, overall depth). V2 acts along local 2, the section depth.
            "V2": (engine.width, engine.height),
            "V3": (engine.height, engine.width),
        }
    else:
        axis_dimensions = {
            "V2": (web_width, section_depth),
            "V3": (web_width, section_depth),
        }
    beam_shear = engine.code.beam_shear
    root_fc = math.sqrt(engine.fc)
    output: list[dict] = []

    # The probable-strength section (1.25 fy) is the same for every combination,
    # direction and end, so it is built once and only the axial load changes.
    # Capacity design applies to special moment frames only (ACI 18.7.6.1).
    probable_surface = probable_engine = probable_section = None
    if is_smrf:
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
        if USE_INTERACTION_SURFACE:
            from design.column_interaction import demand_moments, surface_for

            probable_surface = surface_for(probable_engine, probable_section, bundle_layout)

    # Find one transverse-leg count that satisfies every combo/end/direction.
    for combo in sorted(forces["Combo"].dropna().astype(str).unique()):
        end_forces = {
            end: _column_force_at_end(forces, combo, at_i_end=end == "I")
            for end in ("I", "J")
        }
        for shear_name, (breadth, overall_depth) in axis_dimensions.items():
            v_name = shear_name
            moment_name = "M3" if shear_name == "V2" else "M2"
            moment_theta = (
                _section_bending_angle(0.0, 1.0)
                if moment_name == "M3"
                else _section_bending_angle(1.0, 0.0)
            )
            capacity_shear_kN = 0.0
            if is_smrf:
                probable_moments = []
                for end in ("I", "J"):
                    if progress is not None:
                        progress(
                            member, end, f"Column shear, {shear_name} (capacity design)", combo
                        )
                    axial = _numeric(end_forces[end]["P"], "P", member) * 1000.0
                    if probable_surface is not None:
                        # Mpr along the principal axis at this axial load (1.25 fy section)
                        m2_unit, m3_unit = (0.0, 1.0) if moment_name == "M3" else (1.0, 0.0)
                        probable_mn = probable_surface.nominal_capacity_fast(
                            axial, *demand_moments(m2_unit, m3_unit))
                    else:
                        probable_mn, _, _, _, _ = probable_engine.solve_moment_capacity(
                            probable_section,
                            axial_load=axial,
                            bending_angle=moment_theta,
                        )
                    end_moment = abs(float(probable_mn)) / 1e6
                    limit = (beam_moment_limits or {}).get((shear_name, end))
                    if limit is not None and math.isfinite(limit):
                        end_moment = min(end_moment, limit)
                    probable_moments.append(end_moment)
                # Capacity-based Ve is the probable end-moment sum divided by lu.
                capacity_shear_kN = sum(probable_moments) * 1000.0 / clear_length

            effective_depth = max(
                1.0,
                overall_depth - engine.cc - engine.dties - engine.dmain / 2.0,
            )
            # Vs may not exceed 0.66 sqrt(fc') b d (ACI 22.5.1.2): beyond it the
            # section, not the ties, is too small.
            vs_max = beam_shear.vs_max_coeff * root_fc * breadth * effective_depth

            for end in ("I", "J"):
                force = end_forces[end]
                axial_N = _numeric(force["P"], "P", member) * 1000.0
                shear_concrete = 0.0
                if not is_smrf:
                    # ACI 22.5.6.1 (compression) and 22.5.7.1 (tension, Nu negative)
                    divisor = (shear_cfg.axial_divisor if axial_N >= 0
                               else shear_cfg.tension_axial_divisor)
                    axial_factor = max(0.0, 1.0 + axial_N / (divisor * ag))
                    shear_concrete = min(
                        shear_cfg.vc_coeff * root_fc * breadth * effective_depth * axial_factor,
                        shear_cfg.vc_upper_coeff * root_fc * breadth * effective_depth,
                    )
                analysis_shear = abs(_numeric(force[v_name], v_name, member))
                design_shear = max(analysis_shear, capacity_shear_kN)
                # Convert design shear to required steel shear after subtracting Vc.
                required_vs = max(
                    0.0, design_shear * 1000.0 / phi_shear - shear_concrete
                )
                # Ties at no more than d/2, d/4 for a large Vs (ACI 10.7.6.5.2), and
                # at least Av,min (ACI 10.6.2.2) once Vu > 0.5 phi Vc.
                shear_spacing_limit = math.inf
                minimum_legs = 1
                if design_shear * 1000.0 > 0.5 * phi_shear * shear_concrete:
                    tight = required_vs > beam_shear.vs_spacing_threshold_coeff * root_fc \
                        * breadth * effective_depth
                    shear_spacing_limit = effective_depth * (
                        beam_shear.s_max_d_fraction_high if tight
                        else beam_shear.s_max_d_fraction_low)
                    av_min = max(beam_shear.av_min_coeff_sqrt_fc * root_fc,
                                 beam_shear.av_min_coeff_fyt) * breadth * spacing / fyt
                    minimum_legs = math.ceil(av_min / tie_area - 1e-9)
                required_legs = max(
                    1,
                    minimum_legs,
                    math.ceil(
                        min(required_vs, vs_max) * spacing / (fyt * effective_depth * tie_area)
                        - 1e-9
                    ),
                )
                required_legs = max(required_legs, starting_legs[shear_name])
                provided_legs[shear_name] = max(provided_legs[shear_name], required_legs)
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
                        "Shear_Spacing_Limit_mm": shear_spacing_limit,
                        "Clear_Height_mm": clear_length,
                        "_effective_depth_mm": effective_depth,
                        "_shear_concrete_N": shear_concrete,
                        "_vs_max_N": vs_max,
                        "Vc_Assumption": (
                            "0 used conservatively for all SMRF checks"
                            if is_smrf
                            else "ACI 22.5.6 / 22.5.7 with the axial load of the end"
                        ),
                    }
                )
    for item in output:
        direction_legs = provided_legs[item["Shear_Direction"]]
        steel_shear = min(
            direction_legs * tie_area * fyt * item["_effective_depth_mm"] / spacing,
            item["_vs_max_N"],
        )
        shear_capacity = phi_shear * (item["_shear_concrete_N"] + steel_shear) / 1000.0
        item["Provided_Transverse_Legs"] = direction_legs
        item["phi_Vn_kN"] = shear_capacity
        item["Shear_Utilization"] = (
            item["Design_Shear_kN"] / shear_capacity
            if shear_capacity > 0
            else math.inf
        )
        item["Shear_Check"] = (
            "PASS" if item["Design_Shear_kN"] <= shear_capacity + 1e-9
            else "FAIL: Vs above 0.66 sqrt(fc') b d (ACI 22.5.1.2) - enlarge the section"
        )
        del item["_effective_depth_mm"]
        del item["_shear_concrete_N"]
        del item["_vs_max_N"]
    return output, max(provided_legs.values())


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
    ends_of = {
        str(member): (_normalize_object_name(i), _normalize_object_name(j))
        for member, i, j in zip(connection_by_name.index, connection_by_name["UniquePtI"],
                                connection_by_name["UniquePtJ"])
    }
    design_by_name = frame_data.drop_duplicates("UniqueName").set_index("UniqueName")
    result_by_name = column_results.drop_duplicates("UniqueName").set_index("UniqueName")
    result_rows = {
        str(name): row for name, row in zip(result_by_name.index,
                                             result_by_name.to_dict("records"))
    }
    beam_result_groups = {
        name: rows.copy()
        for name, rows in beam_design.groupby("UniqueName", dropna=True)
    }
    force_groups = {
        _normalize_object_name(name): rows.copy()
        for name, rows in factored_loads.groupby("UniqueName", dropna=True)
    }
    # The checks vary only with the column axial load, so they run over the
    # column combinations (with their permutations). Beams may have other
    # permutations (their force envelope); their bars are used, not their forces.
    is_column = connectivity["DesignType"].astype(str).str.strip().str.casefold().eq("column")
    column_names = set(connectivity.loc[is_column, "UniqueName"])
    column_loads = factored_loads[factored_loads["UniqueName"].isin(column_names)]
    all_combos = sorted(
        (column_loads if not column_loads.empty else factored_loads)["Combo"]
        .dropna().astype(str).unique()
    )

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

    frame_rows: dict[str, pd.Series] = {}

    def frame_data_row(member: str) -> pd.Series:
        """Return the unique properties row for a member or explain what is missing."""
        if member not in frame_rows:
            if member not in design_by_name.index:
                raise ValueError(f"FRAME DATA is missing framing member {member}.")
            frame_rows[member] = design_by_name.loc[member]
        return frame_rows[member]

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
            column_result = result_rows[member]
            end = get_end_info(member, joint, require_force_data=False)[0]
            summaries.append(
                f"{member} ({end}): "
                f"{int(column_result['Longitudinal_Bars'])} bars "
                f"D{float(column_result['Main_Bar_mm']):g}; "
                f"{column_result['Longitudinal_Bar_Layout']}"
            )
        return "; ".join(summaries) if summaries else "N/A"

    end_info_cache: dict[tuple[str, str, bool], tuple[str, str, np.ndarray, float]] = {}

    def get_end_info(
        member: str, joint: str, require_force_data: bool = True
    ) -> tuple[str, str, np.ndarray, float]:
        """Return joint end, far endpoint, outward vector, and member length.

        It depends only on the member and the joint, so it is worked out once:
        the joint checks ask for it hundreds of thousands of times.
        """
        key = (member, joint, require_force_data)
        if key not in end_info_cache:
            end_info_cache[key] = _end_info(member, joint, require_force_data)
        end, far_point, outward, length = end_info_cache[key]
        return end, far_point, outward.copy(), length

    def _end_info(
        member: str, joint: str, require_force_data: bool
    ) -> tuple[str, str, np.ndarray, float]:
        point_i, point_j = ends_of[member]
        if point_i == joint:
            end, far_point = "I", point_j
        elif point_j == joint:
            end, far_point = "J", point_i
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
        if member not in frame_angles:
            raise ValueError(f"Local-axis angle is missing for column {member}.")
        _, local_2, local_3 = member_axes(member, frame_angles[member])
        m2_direction = float(np.dot(moment_axis, local_2))
        m3_direction = float(np.dot(moment_axis, local_3))
        theta = _section_bending_angle(m2_direction, m3_direction)
        forces = force_groups[member]
        force = _column_force_at_end(forces, combo, at_i_end=end == "I")
        axial = _numeric(force["P"], "P", member) * 1000.0
        # A column keeps one bar layout for the whole evaluation, so its section
        # is built once per strength factor and reused for every joint and combo.
        section_key = (member, fy_factor)
        if section_key not in column_sections:
            bars = int(result_rows[member]["Longitudinal_Bars"])
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
        if USE_INTERACTION_SURFACE:
            from design.column_interaction import demand_moments, surface_for

            surface = surface_for(engine, section, column_layouts.get(member))
            if surface is not None:
                nominal = surface.nominal_capacity_fast(
                    axial, *demand_moments(m2_direction, m3_direction))
                return float(nominal) / 1e6, axial
        nominal, _, _, _, _ = engine.solve_moment_capacity(
            section, axial_load=axial, bending_angle=theta
        )
        return float(nominal) / 1e6, axial

    low_axial_cache: dict[str, bool] = {}

    def column_low_axial(column: str) -> bool:
        """Pu < 0.1 Ag fc' in every combination (ACI 18.7.3.1)."""
        if column not in low_axial_cache:
            row = frame_data_row(column)
            diameter = pd.to_numeric(pd.Series([row.get("Diameter")]), errors="coerce").iloc[0]
            if pd.notna(diameter) and diameter > 0:
                area = math.pi * float(diameter) ** 2 / 4.0
            else:
                area = _numeric(row["Width"], "Width", column) * _numeric(
                    row["Depth"], "Depth", column)
            fc = _numeric(row["f'c"], "f'c", column)
            axial = pd.to_numeric(force_groups[column]["P"], errors="coerce").max()
            low_axial_cache[column] = bool(
                pd.notna(axial) and axial * 1000.0
                < CODE.column_seismic.bcc_exempt_axial_fraction * area * fc)
        return low_axial_cache[column]

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

    def column_frame_axis(column: str, frame_direction: np.ndarray) -> str:
        """Column axis a beam line runs along: 'X' (width, local 3) or 'Y' (depth, local 2).

        Empty when the column's orientation is not known.
        """
        if column not in frame_angles or column not in connection_by_name.index:
            return ""
        point_i, point_j = ends_of[column]
        if point_i not in point_coordinates or point_j not in point_coordinates:
            return ""
        _, axis_2, axis_3 = member_axes(column, frame_angles[column])
        along_2 = abs(float(np.dot(frame_direction, axis_2)))
        along_3 = abs(float(np.dot(frame_direction, axis_3)))
        return "Y" if along_2 >= along_3 else "X"

    axes_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def member_axes(member: str, angle: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """ETABS local axes of a member, worked out once."""
        if member not in axes_cache:
            point_i, point_j = ends_of[member]
            axes_cache[member] = _frame_local_axes(
                point_coordinates[point_i], point_coordinates[point_j], angle)
        return axes_cache[member]

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
            if USE_INTERACTION_SURFACE:
                combo_names = _strong_column_hull_combos(
                    combo_names, connected_columns, joint, force_groups, get_end_info)
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
                                    f":{column_frame_axis(column, representative)}"
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
                    # The queried ETABS Local Axes table is column-only; horizontal
                    # beams therefore use the explicit default beta=0 basis.
                    local_1, _, local_3 = member_axes(beam, frame_angles.get(beam, 0.0))
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
                    column_result = result_rows[column]
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

                # ACI 18.7.3.1: no strong column - weak beam check where the column
                # stops at this joint (none above) and its Pu < 0.1 Ag fc' (every
                # combination is used, which covers those with earthquake).
                bcc_exempt = False
                above_columns = [
                    column for column in connected_columns
                    if point_coordinates[get_end_info(column, joint, False)[1]][2]
                    > point_coordinates[joint][2] + 1e-6
                ]
                if not above_columns:
                    bcc_exempt = all(
                        column_low_axial(column) for column in connected_columns)

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
                    if bcc_exempt:
                        b_c_check = BCC_EXEMPT_TEXT

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
                        if column not in frame_angles:
                            raise ValueError(
                                f"Local-axis angle is missing for column {column}."
                            )
                        _, column_axis_2, column_axis_3 = member_axes(
                            column, frame_angles[column])
                        # Column depth lies along local 2 and width along local 3.
                        joint_depth = (
                            abs(float(np.dot(representative, column_axis_2))) * col_d
                            + abs(float(np.dot(representative, column_axis_3))) * col_w
                        )
                        joint_width = (
                            abs(float(np.dot(moment_axis, column_axis_2))) * col_d
                            + abs(float(np.dot(moment_axis, column_axis_3))) * col_w
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
                    # Joint dimensions (NSCP 418.8.2.3, 418.8.2.4): where the beam bars
                    # run through the joint (beams on both sides), the column side along
                    # them is at least 20 db; the joint is at least half the beam depth.
                    largest_bar = max(beam_strengths[beam]["Largest_Beam_Bar_mm"]
                                      for beam in members)
                    deepest_beam = max(beam_strengths[beam]["Beam_Depth_mm"]
                                       for beam in members)
                    through_bars = in_plane_faces == 2
                    dimension_ratio = max(
                        (CODE.column_seismic.joint_bar_diameter_multiple * largest_bar
                         / joint_depth) if through_bars else 0.0,
                        deepest_beam / (CODE.column_seismic.joint_beam_depth_limit
                                        * joint_depth),
                    )
                    dimension_problems = []
                    if through_bars and joint_depth < (
                            CODE.column_seismic.joint_bar_diameter_multiple * largest_bar - 1e-6):
                        dimension_problems.append(
                            f"ACI 18.8.2.3: column side {joint_depth:.0f} mm < 20 x "
                            f"{largest_bar:g} mm beam bar")
                    if deepest_beam > CODE.column_seismic.joint_beam_depth_limit * joint_depth + 1e-6:
                        dimension_problems.append(
                            f"ACI 18.8.2.4: joint depth {joint_depth:.0f} mm < half of the "
                            f"{deepest_beam:.0f} mm beam")
                    if dimension_problems:
                        joint_shear_check = "FAIL: " + "; ".join(dimension_problems)
                        # the loop grows the column side through the joint utilization
                        shear_ratio = max(shear_ratio, dimension_ratio)
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
                                f":{column_frame_axis(column, representative)}"
                                for column in connected_columns
                            ),
                            "Column_Axial_Loads_kN": ", ".join(
                                f"{value:.2f}" for value in axial_values
                            ),
                            "Sum_Column_Mn_kNm": column_capacity,
                            "Sum_Beam_Mn_kNm": beam_nominal,
                            "Column_Beam_Ratio": np.nan if bcc_exempt else ratio,
                            "Required_Ratio": required_ratio,
                            "Strong_Column_Check": b_c_check,
                            "Joint_Check_Reason": BCC_EXEMPT_TEXT if bcc_exempt else "",
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
                            "Joint_Dimension_Check": (
                                "; ".join(dimension_problems) or "PASS"
                            ),
                            "BCC_Exempt": bcc_exempt,
                        }
                    )
    return pd.DataFrame(rows)


BCC_EXEMPT_TEXT = ("N/A - ACI 18.7.3.1: the column stops at this joint and "
                   "Pu < 0.1 Ag fc'")


# Per-end report fields of the joint checks. X is the column's width direction
# (local 3) and Y its depth direction (local 2): "X" values are for the beams
# framing along X.
BCC_REPORT_FIELDS = [
    "Column_Reinforcement_At_Joint",
    "Beam_Reinforcement_X",
    "Sum_Column_Mn_X_kNm",
    "Sum_Beam_Mn_X_kNm",
    "BCC_Ratio_X",
    "Beam_Reinforcement_Y",
    "Sum_Column_Mn_Y_kNm",
    "Sum_Beam_Mn_Y_kNm",
    "BCC_Ratio_Y",
    "BCC_Status",
]
JOINT_SHEAR_REPORT_FIELDS = [
    "Joint_Shear_Demand_X_kN",
    "Joint_Shear_Capacity_X_kN",
    "Joint_Shear_Utilization_X",
    "Joint_Shear_Demand_Y_kN",
    "Joint_Shear_Capacity_Y_kN",
    "Joint_Shear_Utilization_Y",
    "Joint_Shear_Status",
]


def _build_consolidated_column_report(
    column_results: pd.DataFrame,
    load_checks: pd.DataFrame,
    shear_checks: pd.DataFrame,
    joint_results: pd.DataFrame,
    column_labels: dict[str, str] | None = None,
    level_elevations: dict[str, float] | None = None,
    joint_na_reason: str = "N/A",
) -> tuple[pd.DataFrame, list[tuple[str, list[str]]]]:
    """Combine column, force, shear, and joint checks into one I/J report.

    Joint checks are reported for each column axis. The values shown for an axis
    belong to one governing case: the lowest strong-column ratio, and the highest
    joint shear utilization. ``joint_na_reason`` is written where an end has no
    joint check at all.
    """
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
                "Cover_mm",
                "f\'c_MPa",
                "fy_MPa",
                "fyt_MPa",
                "Longitudinal_Bars",
                "Bars_X_Edge",
                "Bars_Y_Edge",
                "Bundle_Layout",
                "Vertical_Bar_Continuity",
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
                [f"{name}_{end}" for name in BCC_REPORT_FIELDS],
            )
        )
    for end in ("I", "J"):
        groups.append(
            (
                f"JOINT SHEAR - {end}",
                [f"{name}_{end}" for name in JOINT_SHEAR_REPORT_FIELDS],
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
                "Alternating_Support_Check_X",
                "Alternating_Support_Check_Y",
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
        for row in load_checks.to_dict("records")
    }
    combos_of: dict[str, set[str]] = {}
    for force_member, combo, _end in force_map:
        combos_of.setdefault(force_member, set()).add(combo)
    # Shear rows of one column, combination and end, reduced to their worst values once.
    shear_groups: dict[tuple[str, str, str], dict] = {}
    if not shear_checks.empty:
        table = shear_checks.assign(
            _member=shear_checks["UniqueName"].astype(str),
            _combo=shear_checks["Combo"].astype(str),
            _end=shear_checks["End"].astype(str),
            _neglected=shear_checks["Concrete_Shear_Strength_Neglected"].eq(True),
            _passes=shear_checks["Shear_Check"].eq("PASS"),
        )
        reduced = table.groupby(["_member", "_combo", "_end"], dropna=False).agg(
            Analysis_Shear_kN=("Analysis_Shear_kN", "max"),
            Capacity_Based_Ve_kN=("Capacity_Based_Ve_kN", "max"),
            Design_Shear_kN=("Design_Shear_kN", "max"),
            Vc_kN=("Vc_kN", "min"),
            neglected=("_neglected", "all"),
            phi_Vn_kN=("phi_Vn_kN", "min"),
            Shear_Utilization=("Shear_Utilization", "max"),
            passes=("_passes", "all"),
        )
        shear_groups = {key: values for key, values in zip(
            reduced.index, reduced.to_dict("records"))}
    # Joint rows per column, combination, end and column axis. Entries are
    # "member:end:axis"; an empty axis means the column orientation is unknown.
    joint_groups: dict[tuple[str, str, str, str], list[pd.Series]] = {}
    if not joint_results.empty:
        for joint_row in joint_results.to_dict("records"):
            combo = str(joint_row.get("Load_Combo", ""))
            for entry in str(joint_row.get("Column_End_Members", "")).split(","):
                parts = [part.strip() for part in entry.rsplit(":", 2)]
                if len(parts) == 3 and parts[1] in ("I", "J"):
                    member, end, axis = parts
                elif len(parts) >= 2:
                    member, end, axis = ":".join(parts[:-1]), parts[-1], ""
                else:
                    continue
                joint_groups.setdefault((member, combo, end, axis), []).append(joint_row)

    def number(item, key: str) -> float:
        """Read a numeric joint value; NaN when it is missing or text."""
        try:
            return float(item.get(key))
        except (TypeError, ValueError):
            return np.nan

    def unique_text(items: list[pd.Series], key: str) -> str:
        """Join the distinct text values of one joint field."""
        return " | ".join(
            dict.fromkeys(str(item.get(key)) for item in items if pd.notna(item.get(key)))
        )

    report_rows: list[dict] = []
    for column in column_results.to_dict("records"):
        member = str(column["UniqueName"])
        combos = sorted(combos_of.get(member, ()))
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
                "Cover_mm": column.get("Cover_mm"),
                "f'c_MPa": column.get("f'c_MPa"),
                "fy_MPa": column.get("fy_MPa"),
                "fyt_MPa": column.get("fyt_MPa"),
                "Longitudinal_Bars": column.get("Longitudinal_Bars"),
                "Bars_X_Edge": column.get("Bars_X_Edge"),
                "Bars_Y_Edge": column.get("Bars_Y_Edge"),
                "Bundle_Layout": column.get("Longitudinal_Bar_Layout"),
                "Vertical_Bar_Continuity": column.get("Vertical_Bar_Continuity"),
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
                "Alternating_Support_Check_X": column.get(
                    "Alternating_Support_Check_X"
                ),
                "Alternating_Support_Check_Y": column.get(
                    "Alternating_Support_Check_Y"
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
                    report_row[f"Analysis_Vu_kN_{end}"] = shear_rows["Analysis_Shear_kN"]
                    report_row[f"Probable_Ve_kN_{end}"] = shear_rows["Capacity_Based_Ve_kN"]
                    report_row[f"Design_Vu_kN_{end}"] = shear_rows["Design_Shear_kN"]
                    report_row[f"Vc_kN_{end}"] = shear_rows["Vc_kN"]
                    report_row[f"Concrete_Shear_Neglected_{end}"] = (
                        "YES" if shear_rows["neglected"] else "NO"
                    )
                    report_row[f"phi_Vn_kN_{end}"] = shear_rows["phi_Vn_kN"]
                    report_row[f"Shear_Utilization_{end}"] = shear_rows["Shear_Utilization"]
                    report_row[f"Shear_Check_{end}"] = (
                        "PASS" if shear_rows["passes"] else "FAIL"
                    )

                end_rows: list[pd.Series] = []
                for axis in ("X", "Y"):
                    axis_rows = joint_groups.get(
                        (member, combo, end, axis), []
                    ) + joint_groups.get((member, combo, end, ""), [])
                    end_rows.extend(axis_rows)
                    missing = (
                        unique_text(axis_rows, "Joint_Check_Reason")
                        if axis_rows
                        else f"N/A - no beam frames in along {axis}"
                    )
                    # Strong column - weak beam: the case with the lowest ratio.
                    rated = [
                        item for item in axis_rows
                        if pd.notna(number(item, "Column_Beam_Ratio"))
                    ]
                    if rated:
                        governing = min(
                            rated, key=lambda item: number(item, "Column_Beam_Ratio")
                        )
                        report_row[f"Beam_Reinforcement_{axis}_{end}"] = governing.get(
                            "Framing_Beam_Reinforcement"
                        )
                        report_row[f"Sum_Column_Mn_{axis}_kNm_{end}"] = number(
                            governing, "Sum_Column_Mn_kNm"
                        )
                        report_row[f"Sum_Beam_Mn_{axis}_kNm_{end}"] = number(
                            governing, "Sum_Beam_Mn_kNm"
                        )
                        report_row[f"BCC_Ratio_{axis}_{end}"] = number(
                            governing, "Column_Beam_Ratio"
                        )
                    else:
                        for prefix in (
                            f"Beam_Reinforcement_{axis}",
                            f"Sum_Column_Mn_{axis}_kNm",
                            f"Sum_Beam_Mn_{axis}_kNm",
                            f"BCC_Ratio_{axis}",
                        ):
                            report_row[f"{prefix}_{end}"] = missing
                    # Joint shear: the case with the highest utilization.
                    sheared = [
                        item for item in axis_rows
                        if pd.notna(number(item, "Joint_Shear_Utilization"))
                    ]
                    if sheared:
                        governing = max(
                            sheared,
                            key=lambda item: number(item, "Joint_Shear_Utilization"),
                        )
                        report_row[f"Joint_Shear_Demand_{axis}_kN_{end}"] = number(
                            governing, "Joint_Shear_Demand_kN"
                        )
                        report_row[f"Joint_Shear_Capacity_{axis}_kN_{end}"] = number(
                            governing, "phi_Vn_kN"
                        )
                        report_row[f"Joint_Shear_Utilization_{axis}_{end}"] = number(
                            governing, "Joint_Shear_Utilization"
                        )
                    else:
                        for prefix in (
                            f"Joint_Shear_Demand_{axis}_kN",
                            f"Joint_Shear_Capacity_{axis}_kN",
                            f"Joint_Shear_Utilization_{axis}",
                        ):
                            report_row[f"{prefix}_{end}"] = missing

                if end_rows:
                    report_row[f"Column_Reinforcement_At_Joint_{end}"] = unique_text(
                        end_rows, "Column_Reinforcement_At_Joint"
                    )
                    report_row[f"BCC_Status_{end}"] = summarize_status(
                        [str(item.get("Strong_Column_Check", "N/A")) for item in end_rows]
                    )
                    report_row[f"Joint_Shear_Status_{end}"] = summarize_status(
                        [str(item.get("Joint_Shear_Check", "N/A")) for item in end_rows]
                    )
                else:
                    for name in BCC_REPORT_FIELDS + JOINT_SHEAR_REPORT_FIELDS:
                        report_row[f"{name}_{end}"] = joint_na_reason
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
        "Cover_mm",
        "f\'c_MPa",
        "fy_MPa",
        "fyt_MPa",
        "Longitudinal_Bars",
        "Bars_X_Edge",
        "Bars_Y_Edge",
        "Bundle_Layout",
        "Vertical_Bar_Continuity",
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
        "BEAM-COLUMN CAPACITY": list(BCC_REPORT_FIELDS),
        "JOINT SHEAR": list(JOINT_SHEAR_REPORT_FIELDS),
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
        "Alternating_Support_Check_X",
        "Alternating_Support_Check_Y",
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
    for wide_row in wide_report.to_dict("records"):
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
                    "Alternating_Support_Check_X",
                    "Alternating_Support_Check_Y",
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
    long_report["_end_order"] = long_report["End"].map({"J": 0, "I": 1})
    elevations = pd.to_numeric(wide_report["Level_Elevation_m"], errors="coerce")
    long_report["_elevation"] = np.repeat(elevations.fillna(-math.inf).to_numpy(float), 2)
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
        .reset_index(drop=True)
    )
    return long_report, report_groups


# Worksheet header of every report field. The sheet is read back through the
# same names (DXF schedule, calculation report).
COLUMN_REPORT_LABELS = {
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
    "Cover_mm": "Concrete Cover (mm)",
    "f'c_MPa": "f′c (MPa)",
    "fy_MPa": "fᵧ (MPa)",
    "fyt_MPa": "fᵧₜ (MPa)",
    "Longitudinal_Bars": "Longitudinal Bars",
    "Bars_X_Edge": "Bars on X Edge",
    "Bars_Y_Edge": "Bars on Y Edge",
    "Bundle_Layout": "Bundle Layout",
    "Vertical_Bar_Continuity": "Vertical Bar Continuity",
    "Bar_Layout_Data": "Bar Layout Data (x, y, n)",
    "Beam_Reinforcement_X": "Beam Bars at Joint, X",
    "Sum_Column_Mn_X_kNm": "ΣMₙ,Column X (kN·m)",
    "Sum_Beam_Mn_X_kNm": "ΣMₙ,Beam X (kN·m)",
    "BCC_Ratio_X": "ΣMₙ,Column / ΣMₙ,Beam, X",
    "Beam_Reinforcement_Y": "Beam Bars at Joint, Y",
    "Sum_Column_Mn_Y_kNm": "ΣMₙ,Column Y (kN·m)",
    "Sum_Beam_Mn_Y_kNm": "ΣMₙ,Beam Y (kN·m)",
    "BCC_Ratio_Y": "ΣMₙ,Column / ΣMₙ,Beam, Y",
    "Joint_Shear_Demand_X_kN": "Joint Shear Demand X (kN)",
    "Joint_Shear_Capacity_X_kN": "ϕ Joint Shear Capacity X (kN)",
    "Joint_Shear_Utilization_X": "Joint Shear Utilization X",
    "Joint_Shear_Demand_Y_kN": "Joint Shear Demand Y (kN)",
    "Joint_Shear_Capacity_Y_kN": "ϕ Joint Shear Capacity Y (kN)",
    "Joint_Shear_Utilization_Y": "Joint Shear Utilization Y",
    "Alternating_Support_Check_X": "Bar Support Check, X Edge",
    "Alternating_Support_Check_Y": "Bar Support Check, Y Edge",
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
    "Transverse_Legs_X": "Tie Legs along X Edge",
    "Transverse_Legs_Y": "Tie Legs along Y Edge",
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


_COLUMN_GROUP_FILLS = {
    "COLUMN LABEL / LEVEL": "BDD7EE",
    "SECTION / LONGITUDINAL REINFORCEMENT": "E2EFDA",
    "FLEXURE / AXIAL": "E2EFDA",
    "COLUMN SHEAR": "FFF2CC",
    "BEAM-COLUMN CAPACITY": "FCE4D6",
    "JOINT SHEAR": "FCE4D6",
    "TRANSVERSE REINFORCEMENT DETAILING": "DEEBF7",
    "DESIGN STATUS": "D9D2E9",
}


def column_report_display(report: pd.DataFrame) -> pd.DataFrame:
    """The report with its readable headers and ends (Bottom (I), Top (J))."""
    clean = report.rename(columns={
        name: COLUMN_REPORT_LABELS.get(name, name.replace("_", " ")) for name in report.columns
    })
    if "End" in clean.columns:
        clean["End"] = clean["End"].map(
            lambda end: {"I": "Bottom (I)", "J": "Top (J)"}.get(end, end))
    return clean


def write_column_results_xlsx(report: pd.DataFrame, groups: list[tuple[str, list[str]]],
                              path: str) -> str:
    """The column design report as a formatted Excel file (no Excel needed)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ordered = [name for _, names in groups for name in names if name in report.columns]
    clean = column_report_display(report[ordered])
    book = Workbook()
    sheet = book.active
    sheet.title = "COLUMN DESIGN"
    sheet["A1"] = "COLUMN DESIGN - CONSOLIDATED CHECKS"
    sheet["A1"].font = Font(size=14, bold=True)
    thick, thin = Side(style="medium"), Side(style="thin")
    column = 1
    for label, names in groups:
        present = [n for n in names if n in report.columns]
        if not present:
            continue
        last = column + len(present) - 1
        sheet.merge_cells(start_row=2, start_column=column, end_row=2, end_column=last)
        cell = sheet.cell(row=2, column=column, value=label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        for index in range(column, last + 1):
            sheet.cell(row=2, column=index).fill = PatternFill(
                "solid", fgColor=_COLUMN_GROUP_FILLS.get(label, "BDD7EE"))
            sheet.cell(row=2, column=index).border = Border(top=thick, bottom=thick)
        column = last + 1
    for index, name in enumerate(clean.columns, start=1):
        cell = sheet.cell(row=3, column=index, value=name)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F2F2F2")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(bottom=thin)
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(12, len(str(name)) * 0.9), 30)
    values = clean.astype(object).where(pd.notna(clean), None).values.tolist()
    names = clean["Unique Name"].tolist() if "Unique Name" in clean.columns else []
    grey = PatternFill("solid", fgColor="F2F2F2")
    shade, previous = False, None
    for offset, row in enumerate(values):
        excel_row = 4 + offset
        if names and names[offset] != previous:
            shade, previous = not shade, names[offset]
            if offset:
                for index in range(1, len(row) + 1):
                    cell = sheet.cell(row=excel_row - 1, column=index)
                    cell.border = Border(bottom=thick)
        for index, value in enumerate(row, start=1):
            cell = sheet.cell(row=excel_row, column=index, value=value)
            if shade:
                cell.fill = grey
            text = str(value).upper() if value is not None else ""
            if text.startswith("FAIL"):
                cell.font = Font(color="C00000", bold=True)
    sheet.freeze_panes = "E4"
    book.save(path)
    return path


def design_columns(
    tables: dict,
    beam_design: pd.DataFrame,
    is_smrf: bool,
    dmain: float,
    dties: float,
    cover: float,
    progress=None,
    continuous_vertical_bars: bool = False,
    bottom_story_cover: str = "none",
) -> tuple[pd.DataFrame, list, pd.DataFrame]:
    """Design every column and run the SMRF checks.

    ``tables`` holds FRAME DATA, CONNECTIVITY and FACTORED LOADS, and for SMRF
    LOCAL AXES and POINTS (the column orientation). ``beam_design`` is the beam
    design result (its bars are used at the joints). Returns the report, its
    column groups and the joint results.

    Args:
        progress: Optional callable that receives one status text at a time
            (column mark, level, end, load combination and current check) for the
            loading window.
        continuous_vertical_bars: Carry the larger number of vertical bars of an
            upper level down to the levels below it. A lower column then uses the
            lightest of its own layouts that has at least as many bars as the
            level above and passes every check.
    """
    _clear_end_force_index()
    frame_data = _clean_table(tables.get("FRAME DATA"))
    connectivity = _clean_table(tables.get("CONNECTIVITY"))
    factored_loads = _clean_table(tables.get("FACTORED LOADS"))
    beam_design = _clean_table(beam_design)
    dmain, dties, cover = float(dmain), float(dties), float(cover)
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
        local_axes = _as_etabs_dataframe(tables.get("LOCAL AXES"),
                                         "Frame Assignments - Local Axes")
        point_table = _as_etabs_dataframe(tables.get("POINTS"), "Point Object Connectivity")
        frame_angles = _extract_frame_angles(local_axes)
        # ETABS lists only frames with an assigned angle; the others have the default 0
        if "UniqueName" in connectivity.columns:
            for name in connectivity["UniqueName"].dropna():
                frame_angles.setdefault(name, 0.0)
        point_coordinates = _extract_point_coordinates(point_table)
    else:
        frame_angles = {}
        point_coordinates = {}
    # The mark comes from the unique name, so every column has one whether or
    # not ETABS is connected and whatever its local-axis angle is.
    frame_labels = {
        member: _common_column_mark(member)
        for member in frame_data["UniqueName"].astype(str)
    }

    if beam_design.empty:
        raise ValueError(
            "BEAM DESIGN contains no reinforcement results. Run beam design before "
            "column design so beam-column checks use designed longitudinal bars."
        )

    # Before the column rows are taken, so every later step sees the same section.
    frame_data, _ = _apply_bottom_story_cover(
        frame_data, connectivity, cover, bottom_story_cover
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
    if not any(pd.notna(value) for value in level_elevations.values()):
        # No joint coordinates (ETABS not connected): order the levels of a mark
        # by how the columns stand on each other.
        story_of = dict(
            zip(column_rows["UniqueName"].astype(str), column_rows["Story"].astype(str))
        )
        rank = {
            story: float(index)
            for index, story in enumerate(_story_order_from_stacks(connectivity, story_of))
        }
        level_elevations = {member: rank[story] for member, story in story_of.items()}

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

    continuity_notes: dict[str, str] = {}
    for state in column_candidates.values():
        layout = state["layout_options"][state["selected_index"]]
        state["own_design_bars"] = sum(count for _, _, count in layout)

    def column_ends(member: str) -> tuple[str, str]:
        """Return the (bottom joint, top joint) of a column."""
        connection = connection_by_name.loc[member]
        point_i = _normalize_object_name(connection["UniquePtI"])
        point_j = _normalize_object_name(connection["UniquePtJ"])
        if point_i in point_coordinates and point_j in point_coordinates:
            if point_coordinates[point_i][2] > point_coordinates[point_j][2]:
                return point_j, point_i
        return point_i, point_j  # ETABS draws columns from the bottom (I) up (J)

    def apply_vertical_bar_continuity() -> None:
        """Give each lower level at least the bar count of the level above it."""
        stacks = _column_stacks({member: column_ends(member) for member in column_candidates})
        for stack in stacks:
            required = 0
            for member in stack:  # top level first
                state = column_candidates[member]
                options = state["layout_options"]
                bars = sum(count for _, _, count in options[state["selected_index"]])
                if bars >= required:
                    required = bars
                    continue
                report_progress(member, "I and J", "Vertical bar continuity")
                match = None
                for index in range(state["selected_index"] + 1, len(options)):
                    if sum(count for _, _, count in options[index]) < required:
                        continue
                    if state["evaluate_candidate"](index)["passes"]:
                        match = index
                        break
                if match is None:
                    continuity_notes[member] = (
                        f"Could not match the {required} bars of the level above; "
                        f"kept {bars}"
                    )
                    continue
                state["selected_index"] = match
                new_bars = sum(count for _, _, count in options[match])
                continuity_notes[member] = (
                    f"Raised from {state['own_design_bars']} to {new_bars} bars "
                    "to match the level above"
                )
                required = max(required, new_bars)

    if continuous_vertical_bars:
        apply_vertical_bar_continuity()

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
            theta = _section_bending_angle(
                float(np.dot(moment_axis, local_2)),
                float(np.dot(moment_axis, local_3)),
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
            surface = None
            if USE_INTERACTION_SURFACE:
                from design.column_interaction import demand_moments, surface_for

                surface = surface_for(engine, section, layout)
            if surface is not None:
                m2_direction = float(np.dot(moment_axis, local_2))
                m3_direction = float(np.dot(moment_axis, local_3))
                nominal = surface.nominal_capacity_fast(
                    axial, *demand_moments(m2_direction, m3_direction))
            else:
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
            exempt = (initial_joints["BCC_Exempt"].eq(True)
                      if "BCC_Exempt" in initial_joints.columns
                      else pd.Series(False, index=initial_joints.index))
            failing_rows = initial_joints.loc[
                initial_joints["Sum_Column_Mn_kNm"].notna()
                & initial_joints["Sum_Beam_Mn_kNm"].notna()
                & ~exempt
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

        # Strong-column upgrades can add bars to a lower level only, so the
        # continuity rule is applied once more before the ratios are finalised.
        if continuous_vertical_bars:
            apply_vertical_bar_continuity()

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

            initial_joints.at[
                joint_index, "Column_Reinforcement_At_Joint"
            ] = "; ".join(updated_reinforcement)
            # A BLOCKED row (missing data) has no sums, and an exempt top joint
            # (ACI 18.7.3.1) no ratio: their check text stays as it is.
            if bool(joint_row.get("BCC_Exempt", False)) is True or pd.isna(
                joint_row.get("Sum_Column_Mn_kNm")
            ) or pd.isna(joint_row.get("Sum_Beam_Mn_kNm")):
                continue
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

            # Joint shear demand uses the BEAMS' probable moments only, so adding
            # column bars changes the strong-column ratio but not the joint shear.
        joint_results = initial_joints

    # Clear height of each column for its capacity shear: the joint-to-joint length
    # less the deepest beam framing into its top joint (beams hang from the floor).
    beam_depth_at_joint: dict[str, float] = {}
    beam_depths = {}
    if {"Depth", "DesignType"} <= set(frame_data.columns):
        is_beam = frame_data["DesignType"].astype(str).str.strip().str.casefold().eq("beam")
        beam_depths = dict(zip(frame_data.loc[is_beam, "UniqueName"].astype(str),
                               pd.to_numeric(frame_data.loc[is_beam, "Depth"], errors="coerce")))
    for member, connection in connection_by_name.iterrows():
        depth = beam_depths.get(str(member))
        if depth is None or not math.isfinite(depth):
            continue
        for point in ("UniquePtI", "UniquePtJ"):
            joint = _normalize_object_name(connection[point])
            beam_depth_at_joint[joint] = max(beam_depth_at_joint.get(joint, 0.0), float(depth))
    clear_heights: dict[str, float] = {}
    for member in column_rows["UniqueName"].astype(str):
        if member not in connection_by_name.index or member not in force_frames:
            continue
        stations = pd.to_numeric(force_frames[member]["Station"], errors="coerce").dropna()
        if stations.empty:
            continue
        _, top_joint = column_ends(member)
        clear_heights[member] = float(stations.max() - stations.min()) \
            - beam_depth_at_joint.get(top_joint, 0.0)

    # ACI 18.7.6.1.1: the column end moment for Ve need not exceed what the beams
    # deliver at their probable strength. At each joint the beams' sum of Mpr is
    # shared equally by the columns there (the largest over both sway directions).
    # Beams along the column's Y axis (local 2) bend it about local 3 (V2); beams
    # along X (local 3) about local 2 (V3).
    beam_moment_limits: dict[str, dict[tuple[str, str], float]] = {}
    if is_smrf and not joint_results.empty and "Beam_Probable_Mn_kNm" in joint_results:
        for joint_row in joint_results.to_dict("records"):
            try:
                beam_mpr = float(joint_row.get("Beam_Probable_Mn_kNm"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(beam_mpr):
                continue
            entries = [entry.strip().rsplit(":", 2)
                       for entry in str(joint_row.get("Column_End_Members", "")).split(",")
                       if entry.strip()]
            entries = [entry for entry in entries if len(entry) == 3]
            if not entries:
                continue
            share = beam_mpr / len(entries)
            for member, end, axis in entries:
                if axis not in ("X", "Y"):
                    continue
                key = ("V2" if axis == "Y" else "V3", end)
                limits = beam_moment_limits.setdefault(member, {})
                limits[key] = max(limits.get(key, 0.0), share)

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
                len(selected_layout),
                maximum_compression,
                spacing_provided,
            )
            support_x, support_y = _alternating_support_legs(
                final_engine, selected_layout
            )
            legs_x = max(transverse["Required_Legs_X"], support_x)
            legs_y = max(transverse["Required_Legs_Y"], support_y)
            transverse["Transverse_Legs_X"] = legs_x
            transverse["Transverse_Legs_Y"] = legs_y
            transverse["Transverse_Legs_Per_Direction"] = max(legs_x, legs_y)
            transverse["Alternating_Support_Added_Legs"] = max(
                0, support_x - transverse["Required_Legs_X"]
            ) + max(0, support_y - transverse["Required_Legs_Y"])
            if final_engine.shape == "circular":
                support_text = "N/A for continuous spiral"
                transverse["Alternating_Support_Check"] = support_text
                transverse["Alternating_Support_Check_X"] = support_text
                transverse["Alternating_Support_Check_Y"] = support_text
            else:
                transverse["Alternating_Support_Check"] = "PASS"
                transverse["Alternating_Support_Check_X"] = (
                    f"PASS - {support_x} legs needed along the X edge"
                )
                transverse["Alternating_Support_Check_Y"] = (
                    f"PASS - {support_y} legs needed along the Y edge"
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
                "Alternating_Support_Check_X": "N/A - non-SMRF detailing",
                "Alternating_Support_Check_Y": "N/A - non-SMRF detailing",
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

        required_tie_diameter = _minimum_tie_diameter(final_engine, selected_layout)
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

        def shear_checks_at(tie_spacing: float) -> list[dict]:
            checks, _ = _column_shear_checks(
                row,
                forces,
                final_engine,
                selected_bars,
                tie_spacing,
                (transverse["Transverse_Legs_X"], transverse["Transverse_Legs_Y"]),
                is_smrf,
                bundle_layout=selected_layout,
                progress=report_progress,
                clear_height=clear_heights.get(member),
                beam_moment_limits=beam_moment_limits.get(member),
            )
            return checks

        member_shear_checks = shear_checks_at(spacing_provided)
        # Shear ties at no more than d/2 (d/4 for a large Vs), ACI 10.7.6.5.2.
        shear_spacing = min(
            (check["Shear_Spacing_Limit_mm"] for check in member_shear_checks),
            default=math.inf,
        )
        if shear_spacing < spacing_provided - 1e-6:
            spacing_provided = math.floor(shear_spacing / 5.0) * 5.0
            member_shear_checks = shear_checks_at(spacing_provided)
        # Shear along the depth (V2) uses the legs counted along the X edge and
        # shear along the width (V3) the legs counted along the Y edge.
        for check in member_shear_checks:
            key = (
                "Transverse_Legs_X"
                if check["Shear_Direction"] == "V2"
                else "Transverse_Legs_Y"
            )
            transverse[key] = max(transverse[key], check["Provided_Transverse_Legs"])
        transverse["Transverse_Legs_Per_Direction"] = max(
            transverse["Transverse_Legs_X"], transverse["Transverse_Legs_Y"]
        )
        if final_engine.shape == "rectangular" and is_smrf:
            core_width, core_height = _confined_core(final_engine)
            tie_area = math.pi * final_engine.dties**2 / 4.0
            transverse["Provided_Ash_s_Ratio_X"] = (
                transverse["Transverse_Legs_X"] * tie_area / (spacing_provided * core_width)
            )
            transverse["Provided_Ash_s_Ratio_Y"] = (
                transverse["Transverse_Legs_Y"] * tie_area / (spacing_provided * core_height)
            )
            transverse["Provided_Confinement_Ratio_X"] = transverse[
                "Provided_Ash_s_Ratio_X"
            ]
            transverse["Provided_Confinement_Ratio_Y"] = transverse[
                "Provided_Ash_s_Ratio_Y"
            ]
            transverse["Provided_Confinement_Ratio"] = min(
                transverse["Provided_Ash_s_Ratio_X"], transverse["Provided_Ash_s_Ratio_Y"]
            )
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
                "Cover_mm": final_engine.cc,
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
                "Alternating_Support_Check_X": transverse[
                    "Alternating_Support_Check_X"
                ],
                "Alternating_Support_Check_Y": transverse[
                    "Alternating_Support_Check_Y"
                ],
                "Bars_X_Edge": _bars_per_edge(final_engine, selected_layout)[0],
                "Bars_Y_Edge": _bars_per_edge(final_engine, selected_layout)[1],
                "Vertical_Bar_Continuity": continuity_notes.get(
                    member,
                    "Own design" if continuous_vertical_bars else "Not applied",
                ),
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
        joint_na_reason=(
            "N/A - no beam frames into this end"
            if is_smrf
            else "N/A - seismic design is off"
        ),
    )
    _clear_end_force_index()
    return report, report_groups, joint_results


def column_size_passes(
    frame_row: pd.Series,
    forces: pd.DataFrame,
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
) -> tuple[bool, str]:
    """Whether a column section can be reinforced for its forces, and why not.

    The same member checks as ``design_columns`` that depend on the section size
    alone: a bar layout within the steel limit that passes flexure and axial load
    on the interaction surface, its transverse detailing, the SMRF dimensions and
    the column shear (the steel limit Vs <= 0.66 sqrt(fc') b d). ``forces`` is the
    member's FACTORED LOADS rows as ETABS gives them (compression negative). The
    joint checks need the beams and the columns around the joint, so they are
    left to the full design.
    """
    forces, _ = _expand_combo_permutations(_to_compression_positive(_clean_table(forces)))
    forces["UniqueName"] = forces["UniqueName"].map(_normalize_object_name)
    row = frame_row.copy()
    row["UniqueName"] = _normalize_object_name(row["UniqueName"])
    engine, _ = _build_column_section(row, 4, dmain, dties, cover, is_smrf)
    if is_smrf:
        seismic = engine.code.column_seismic
        sides = ((engine.diameter, engine.diameter) if engine.shape == "circular"
                 else (min(engine.width, engine.height), max(engine.width, engine.height)))
        if sides[0] < seismic.min_dimension or sides[0] / sides[1] < seismic.min_aspect_ratio:
            return False, "SMRF dimensions (ACI 18.7.2.1)"
    strength = engine.code.column_strength
    ratio_limit = strength.rho_max_smrf if is_smrf else strength.rho_max
    gross = (engine.width * engine.height if engine.shape == "rectangular"
             else math.pi * engine.diameter**2 / 4.0)
    max_bars = int(ratio_limit * gross / (math.pi * dmain**2 / 4.0))
    try:
        layouts = _enumerate_column_bar_layouts(engine, max_bars=max_bars)
    except ValueError as exc:
        return False, str(exc)
    axial = pd.to_numeric(forces["P"], errors="coerce").dropna()
    max_compression = max(0.0, float(axial.max()) * 1000.0) if not axial.empty else 0.0
    reason = f"no bar layout within the {ratio_limit:.0%} limit passes flexure and axial load"
    try:
        for layout in layouts:
            passes, _, _ = _evaluate_column_candidate(
                row, forces, layout, dmain, dties, cover, is_smrf, stop_at_first_failure=True)
            if not passes:
                continue
            bars = sum(count for _, _, count in layout)
            layout_engine, _ = _build_column_section(
                row, bars, dmain, dties, cover, is_smrf, bundle_layout=layout)
            detailing, _, _ = _column_transverse_candidate_passes(
                row, forces, layout_engine, layout, max_compression, is_smrf)
            if not detailing:
                reason = "transverse detailing"
                continue
            spacing = layout_engine.solve_max_spacing()["max_tie_spacing_mm"]
            if is_smrf:
                spacing = min(spacing, _smrf_transverse_design(
                    layout_engine, len(layout), max_compression, spacing
                )["Transverse_Spacing_Provided_mm"])
            shear, _ = _column_shear_checks(
                row, forces, layout_engine, bars, spacing, 2, is_smrf, bundle_layout=layout)
            if all(check["Shear_Check"] == "PASS" for check in shear):
                return True, "passes"
            return False, "column shear (section too small for the shear steel)"
    finally:
        _clear_end_force_index()
    return False, reason


BOTTOM_COVER_MODES = ("none", "enlarge", "bars")
BOTTOM_COVER_QUESTION = {
    "Yes - use {cover:g} mm cover on the bottom-most story": True,
    "No - keep {normal:g} mm cover on every story": False,
}
BOTTOM_COVER_MODE_OPTIONS = {
    "Enlarge the section - add {extra:g} mm on every face, the bars stay where they are": "enlarge",
    "Keep the section size - move the vertical bars inward": "bars",
}


def _apply_bottom_story_cover(
    frame_data: pd.DataFrame,
    connectivity: pd.DataFrame,
    cover: float,
    mode: str,
    code: AciCode = CODE,
) -> tuple[pd.DataFrame, str | None]:
    """Give the columns of the bottom-most story the earth-contact cover.

    ``mode`` is ``"enlarge"`` (each face moves out by the extra cover, so every
    dimension grows by twice that and the bars keep their position) or
    ``"bars"`` (the section is kept and the bars move inward). The bottom-most
    story is read from how the columns stand on each other.

    Returns the frame data and the name of that story (None when nothing changed).
    """
    if mode not in BOTTOM_COVER_MODES:
        raise ValueError(f"bottom_story_cover must be one of {BOTTOM_COVER_MODES}.")
    bottom_cover = code.column_strength.earth_contact_cover
    extra = bottom_cover - cover
    if mode == "none" or extra <= 0:
        return frame_data, None
    is_column = frame_data["DesignType"].astype(str).str.strip().str.casefold().eq("column")
    story_of = dict(zip(
        frame_data.loc[is_column, "UniqueName"].astype(str),
        frame_data.loc[is_column, "Story"].astype(str),
    ))
    order = _story_order_from_stacks(connectivity, story_of)
    if not order:
        return frame_data, None
    frame_data = frame_data.copy()
    rows = is_column & frame_data["Story"].astype(str).eq(order[0])
    if "DesignCover" not in frame_data.columns:
        frame_data["DesignCover"] = np.nan
    frame_data.loc[rows, "DesignCover"] = bottom_cover
    if mode == "enlarge":
        for name in ("Width", "Depth", "Diameter"):
            if name not in frame_data.columns:
                continue
            values = pd.to_numeric(frame_data[name], errors="coerce")
            grow = rows & values.gt(0)
            frame_data[name] = frame_data[name].astype(object)
            frame_data.loc[grow, name] = values[grow] + 2.0 * extra
    return frame_data, order[0]


VERTICAL_BAR_OPTIONS = {
    "Yes - lower levels use at least the bars of the level above": True,
    "No - design every level on its own": False,
}


def ask_column_design_options(
    continuous_vertical_bars: bool | None = None,
    bottom_story_cover: str | None = None,
    normal_cover: float = 40.0,
) -> tuple[bool, str] | None:
    """Ask the column design questions that are not answered yet.

    Returns ``(continuous_vertical_bars, bottom_story_cover)``, or ``None`` when
    a dialog is closed. An automatic resize loop asks once and passes the
    answers to every run.
    """
    from utilities._gui_helpers import select_option

    if bottom_story_cover is None:
        normal = float(normal_cover)
        bottom = CODE.column_strength.earth_contact_cover
        bottom_story_cover = "none"
        if bottom > normal:
            questions = {
                text.format(cover=bottom, normal=normal): value
                for text, value in BOTTOM_COVER_QUESTION.items()
            }
            chosen = select_option(
                "Column Design - Bottom Story Cover",
                f"Use {bottom:g} mm concrete cover for the columns of the bottom-most story?",
                list(questions),
                default_index=1,
            )
            if chosen is None:
                return None
            if questions[chosen]:
                modes = {
                    text.format(extra=bottom - normal): value
                    for text, value in BOTTOM_COVER_MODE_OPTIONS.items()
                }
                chosen = select_option(
                    "Column Design - Bottom Story Cover",
                    f"How should the {bottom:g} mm cover be provided?",
                    list(modes),
                )
                if chosen is None:
                    return None
                bottom_story_cover = modes[chosen]

    if continuous_vertical_bars is None:
        chosen = select_option(
            "Column Design - Vertical Bars",
            "Carry the larger number of vertical bars of an upper level down to the "
            "levels below it?",
            list(VERTICAL_BAR_OPTIONS),
        )
        if chosen is None:
            return None
        continuous_vertical_bars = VERTICAL_BAR_OPTIONS[chosen]
    return continuous_vertical_bars, bottom_story_cover


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
    story_order: list[str] | None = None,
) -> None:
    """Create one grouped column schedule DXF for the supplied floor report.

    ``inner_tie_style`` is ``"crossties"`` or ``"hoops"`` (closed inner hoops).
    ``story_order`` lists the stories from the bottom up; without it the order
    is guessed from the story names.
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
    known = [str(story) for story in (story_order or [])]
    story_names = report["Story"].dropna().astype(str).unique()
    full_order = known + sorted(
        (story for story in story_names if story not in known), key=_column_story_sort_key
    )
    story_rank = {story: index for index, story in enumerate(full_order)}
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
                _elevation=members["Story"].map(lambda value: story_rank.get(str(value), -1))
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
    # Proportions of the office column schedule: F'c, floor level and mark
    # columns, then one detail column per mark.
    left_widths = [30.0, 50.0, 59.0]
    detail_width = 90.0
    header_height = 8.0
    drawing_height = 88.0
    data_height = 7.0
    text_height = 3.5  # headers, row names and values
    level_text_height = 4.5  # F'c and floor level
    diameter_sign = "%%C"  # drawn as the diameter symbol in CAD
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
    story_values = sorted(story_names, key=story_rank.get, reverse=True)
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
                    text_height,
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
                text_height,
                left_widths[2],
            )
        level_y = y_top - story_header_height - story_content_height / 2.0
        add_text(fc_value, story_x_positions[0], level_y, level_text_height, left_widths[0])
        # A column runs from the level below to its own story: "3F TO PD1".
        rank = story_rank[story]
        below = full_order[rank - 1] if rank > 0 else CODE.drawing.column_schedule_base_label
        level_range = f"{below} TO {story}"
        add_text(
            level_range,
            story_x_positions[1],
            level_y,
            # long story names are written smaller so they stay inside the cell
            min(level_text_height, left_widths[1] / (0.95 * len(level_range))),
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
                text_height,
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
                    text_height,
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
                    # a column designed with its own cover is drawn with it
                    float(row["Concrete Cover (mm)"])
                    if pd.notna(row.get("Concrete Cover (mm)"))
                    else cover,
                    is_smrf,
                    inner_tie_style,
                )
            else:
                add_text(
                    "-",
                    x_left,
                    y_top - story_header_height - drawing_height / 2.0,
                    level_text_height,
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
                    tie = f"{float(row['Tie Bar Diameter (mm)']):g}mm{diameter_sign}"
                    if field_name == "SIZE":
                        diameter = float(row["Diameter (mm)"] or 0.0)
                        value = (
                            f"{diameter:.0f}mm{diameter_sign}"
                            if diameter > 0
                            else f"{float(row['Width (mm)']):.0f}X{float(row['Depth (mm)']):.0f}"
                        )
                    elif field_name == "VERTICAL BARS":
                        value = (
                            f"{int(float(row['Longitudinal Bars']))}-"
                            f"{main_bar_diameter:g}mm{diameter_sign}"
                        )
                    elif field_name == "JOINT REIN.":
                        value = f"{tie} @ {CODE.drawing.column_joint_tie_spacing:g}mm"
                    elif field_name == "CONFINMT":
                        # The standard spacing, unless the design needs a closer one.
                        designed = pd.to_numeric(
                            row.get("Tie / Spiral Spacing (mm)"), errors="coerce"
                        )
                        spacing = CODE.drawing.column_confinement_tie_spacing
                        if pd.notna(designed) and designed < spacing:
                            spacing = float(designed)
                        value = f"{tie} @ {spacing:g}mm"
                    else:
                        value = f"{tie} @ {CODE.drawing.column_general_tie_spacing:g}mm"
                add_text(value, x_left, row_top - data_height / 2.0, text_height, detail_width)
        y_top = block_bottom

    doc.saveas(output_filepath)
    return


INNER_TIE_STYLE_LABELS = {
    "Crossties (one tie with a hook at each end)": "crossties",
    "Closed inner hoops (each enclosing two bar positions)": "hoops",
}


def ask_inner_tie_style() -> str | None:
    """Ask whether interior ties are drawn as crossties or closed inner hoops."""
    from utilities._gui_helpers import select_option

    chosen = select_option(
        "Column Schedule - Interior Ties",
        "How should the interior ties be drawn?",
        list(INNER_TIE_STYLE_LABELS),
    )
    return None if chosen is None else INNER_TIE_STYLE_LABELS[chosen]


def export_column_cad_drawings(
    report: pd.DataFrame,
    output_directory: str,
    main_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str,
    connectivity: pd.DataFrame,
) -> list[str]:
    """Export one stacked all-story column schedule DXF from the design report."""
    destination = output_directory
    if not os.path.isdir(destination):
        raise NotADirectoryError(f"DXF output directory does not exist: {destination}")
    if inner_tie_style not in INNER_TIE_STYLES:
        raise ValueError(f"inner_tie_style must be one of {INNER_TIE_STYLES}.")

    report = column_report_display(report)
    if not report.empty:
        report = report.drop_duplicates(subset=["Unique Name"], keep="first")
    if report.empty:
        raise ValueError("There are no designed columns to export.")
    os.makedirs(destination, exist_ok=True)
    output_path = os.path.join(destination, "Column_Schedule.dxf")
    generate_dxf_column_schedule(
        report,
        output_path,
        main_bar_diameter,
        cover,
        is_smrf,
        inner_tie_style,
        story_order=_story_order_from_stacks(
            _clean_table(connectivity),
            dict(zip(report["Unique Name"].astype(str), report["Story"].astype(str))),
        ),
    )
    return [output_path]


# =============================================================================
# CALCULATION REPORT (PDF)
# =============================================================================
def _governing_row(rows: pd.DataFrame, value: str, check: str, lowest: bool = False):
    """Row of the governing combination: a failing one first, then the extreme value."""
    values = pd.to_numeric(rows[value], errors="coerce")
    rated = rows[values.notna()]
    if rated.empty:
        return None
    failing = rated[rated[check].astype(str).str.startswith("FAIL")]
    pool = failing if not failing.empty else rated
    pool_values = pd.to_numeric(pool[value], errors="coerce")
    return pool.loc[pool_values.idxmin() if lowest else pool_values.idxmax()]


def _column_calc_member(rows: pd.DataFrame) -> MemberReport:
    """Build the report tables of one column from its report rows (internal names)."""
    first = rows.iloc[0]
    name = str(first["UniqueName"])
    circular = not is_blank(first.get("Diameter_mm")) and float(first["Diameter_mm"]) > 0
    ends = [
        (end, label, rows[rows["End"] == end])
        for end, label in (("J", "Top (J)"), ("I", "Bottom (I)"))
    ]

    size_rows = (
        [[Tex("Diameter (mm)"), number(first.get("Diameter_mm"), 0), "Shape", first.get("Shape")]]
        if circular
        else [[Tex("Width, X (mm)"), number(first.get("Width_mm"), 0),
               Tex("Depth, Y (mm)"), number(first.get("Depth_mm"), 0)]]
    )
    section = ReportTable(
        "Section and vertical reinforcement",
        ["Parameter", "Value", "Parameter", "Value"],
        [["Section", first.get("Section"), "Column mark", first.get("Column_Label")]]
        + size_rows
        + [
            [Tex(r"$f'_c$ (MPa)"), number(first.get("f'c_MPa")),
             Tex("$f_y$ (MPa)"), number(first.get("fy_MPa"))],
            [Tex("$f_{yt}$ (MPa)"), number(first.get("fyt_MPa")),
             "Vertical bars", number(first.get("Longitudinal_Bars"), 0)],
            ["Bars on X edge", number(first.get("Bars_X_Edge"), 0),
             "Bars on Y edge", number(first.get("Bars_Y_Edge"), 0)],
            [Tex(r"Steel ratio, $\rho$"), number(first.get("Reinforcement_Ratio"), 4),
             Tex(r"Limit on $\rho$"), number(first.get("Reinforcement_Ratio_Limit"), 4)],
            ["Bundles", first.get("Bundle_Layout"),
             "Vertical bar continuity", first.get("Vertical_Bar_Continuity")],
        ],
        "lp{4.6cm}lp{6.2cm}",
    )

    flexure_rows, shear_rows, capacity_rows, joint_rows = [], [], [], []
    worst_flexure = worst_shear = float("nan")
    for end, label, end_rows in ends:
        if end_rows.empty:
            continue
        governing = _governing_row(end_rows, "Flexure_Utilization", "Flexure_Check")
        if governing is not None:
            utilization = float(governing["Flexure_Utilization"])
            worst_flexure = max(utilization, worst_flexure) if worst_flexure == worst_flexure else utilization
            flexure_rows.append([
                label, governing.get("Combo"), number(governing.get("Pu_kN")),
                number(governing.get("Mu2_kNm")), number(governing.get("Mu3_kNm")),
                number(governing.get("phi_Mn_kNm")), number(utilization),
                governing.get("Axial_Check"), governing.get("Flexure_Check"),
            ])
        governing = _governing_row(end_rows, "Shear_Utilization", "Shear_Check")
        if governing is not None:
            utilization = float(governing["Shear_Utilization"])
            worst_shear = max(utilization, worst_shear) if worst_shear == worst_shear else utilization
            shear_rows.append([
                label, governing.get("Combo"), number(governing.get("Analysis_Vu_kN")),
                number(governing.get("Probable_Ve_kN")), number(governing.get("Design_Vu_kN")),
                number(governing.get("Vc_kN")), number(governing.get("phi_Vn_kN")),
                number(utilization), governing.get("Shear_Check"),
            ])
        for axis in ("X", "Y"):
            governing = _governing_row(end_rows, f"BCC_Ratio_{axis}", "BCC_Status", lowest=True)
            if governing is None:
                capacity_rows.append(
                    [label, axis, end_rows.iloc[0].get(f"BCC_Ratio_{axis}"), "--", "--", "--", "--"]
                )
            else:
                capacity_rows.append([
                    label, axis, governing.get("Combo"),
                    number(governing.get(f"Sum_Column_Mn_{axis}_kNm")),
                    number(governing.get(f"Sum_Beam_Mn_{axis}_kNm")),
                    number(governing.get(f"BCC_Ratio_{axis}")), governing.get("BCC_Status"),
                ])
            governing = _governing_row(
                end_rows, f"Joint_Shear_Utilization_{axis}", "Joint_Shear_Status"
            )
            if governing is None:
                joint_rows.append(
                    [label, axis, "--", "--", "--",
                     end_rows.iloc[0].get(f"Joint_Shear_Utilization_{axis}")]
                )
            else:
                joint_rows.append([
                    label, axis,
                    number(governing.get(f"Joint_Shear_Demand_{axis}_kN")),
                    number(governing.get(f"Joint_Shear_Capacity_{axis}_kN")),
                    number(governing.get(f"Joint_Shear_Utilization_{axis}")),
                    governing.get("Joint_Shear_Status"),
                ])

    combo = "p{5.4cm}"
    tables = [
        section,
        ReportTable(
            "Axial load and flexure - governing combination at each end",
            ["End", "Combination", Tex("$P_u$"), Tex("$M_{u2}$"), Tex("$M_{u3}$"),
             Tex(r"$\phi M_n$"), Tex(r"$M_u/\phi M_n$"), "Axial", "Flexure"],
            flexure_rows, "l" + combo + "rrrrrll",
            "Forces in kN, moments in kN-m. phi Mn is the design strength along the direction of "
            "the Mu2-Mu3 resultant where phi Pn = Pu, read from the section's biaxial "
            "interaction surface (figure below).",
        ),
        ReportTable(
            "Column shear - governing combination at each end",
            ["End", "Combination", Tex("$V_u$"), Tex("$V_e$"), Tex("Design $V_u$"),
             Tex("$V_c$"), Tex(r"$\phi V_n$"), Tex(r"$V_u/\phi V_n$"), "Check"],
            shear_rows, "l" + combo + "rrrrrrl",
            "Forces in kN. Ve is the shear from the probable moment strengths at the column ends.",
        ),
        ReportTable(
            "Strong column - weak beam",
            ["End", "Axis", "Combination", Tex(r"$\Sigma M_{nc}$"), Tex(r"$\Sigma M_{nb}$"),
             Tex(r"$\Sigma M_{nc}/\Sigma M_{nb}$"), "Check"],
            capacity_rows, "ll" + combo + "rrrl",
            "Moments in kN-m. Each axis shows the combination with the lowest ratio.",
        ),
        ReportTable(
            "Joint shear",
            ["End", "Axis", Tex("$V_j$"), Tex(r"$\phi V_n$"),
             Tex(r"$V_j/\phi V_n$"), "Check"],
            joint_rows, "llrrrl",
            "Forces in kN. The demand comes from the beam bars at 1.25 fy and the capacity from "
            "the joint size and confinement, so the check is the same for every load combination.",
        ),
    ]

    confinement = [
        [f"Required steel ratio, 18.7.5({letter})", number(first.get(f"Confinement_18_7_5_{letter}"), 4)]
        for letter in "abcdef"
        if not is_blank(first.get(f"Confinement_18_7_5_{letter}"))
    ]
    tables.append(ReportTable(
        "Transverse reinforcement",
        ["Parameter", "Value"],
        [
            ["Type", first.get("Transverse_Type")],
            ["Provided", first.get("Transverse_Provision_Summary")],
            ["Tie bar diameter (mm)", number(first.get("Tie_Bar_mm"), 0)],
            ["Spacing (mm)", number(first.get("Transverse_Spacing_mm"), 0)],
            ["Tie legs along X edge", number(first.get("Transverse_Legs_X"), 0)],
            ["Tie legs along Y edge", number(first.get("Transverse_Legs_Y"), 0)],
            ["Governing ACI 18.7.5 expression", first.get("Confinement_Criteria_Governing")],
            ["High axial load or high concrete strength rule", first.get("High_Axial_or_High_fc_Check")],
        ]
        + confinement
        + [
            [Tex("$k_f$"), number(first.get("Kf"), 3)],
            [Tex("$k_n$"), number(first.get("Kn"), 3)],
            [Tex("Required $A_{sh}/(s\\,b_c)$, X"), number(first.get("Required_Ash_s_Ratio_X"), 4)],
            [Tex("Provided $A_{sh}/(s\\,b_c)$, X"), number(first.get("Provided_Ash_s_Ratio_X"), 4)],
            [Tex("Required $A_{sh}/(s\\,b_c)$, Y"), number(first.get("Required_Ash_s_Ratio_Y"), 4)],
            [Tex("Provided $A_{sh}/(s\\,b_c)$, Y"), number(first.get("Provided_Ash_s_Ratio_Y"), 4)],
            ["Confinement ratio check", first.get("Confinement_Check")],
            ["Tie spacing check", first.get("Transverse_Spacing_Check")],
            ["Bar support check, X edge", first.get("Alternating_Support_Check_X")],
            ["Bar support check, Y edge", first.get("Alternating_Support_Check_Y")],
            ["Tie diameter check", first.get("Tie_Diameter_Check")],
            ["SMRF column dimension check", first.get("SMRF_Dimension_Check")],
            ["Transverse reinforcement check", first.get("Transverse_Reinforcement_Check")],
        ],
        "lp{10cm}",
    ))
    status = str(first.get("Column_Design_Status"))
    tables.append(ReportTable(
        "Design status",
        ["Parameter", "Value"],
        [["Overall design status", status], ["Reason", first.get("Design_Status_Reason")]],
        "lp{13cm}",
    ))

    size = (
        f"D{float(first['Diameter_mm']):g}" if circular
        else f"{float(first['Width_mm']):g} x {float(first['Depth_mm']):g}"
    )
    return MemberReport(
        heading=f"Column {name} ({first.get('Story')})",
        summary=[first.get("Column_Label"), name, first.get("Story"), size,
                 number(first.get("Longitudinal_Bars"), 0), number(worst_flexure),
                 number(worst_shear), status],
        tables=tables,
    )


def column_interaction_figure(rows: pd.DataFrame, path: str, dmain: float, dties: float,
                              cover: float, is_smrf: bool) -> str | None:
    """Save the 3D P-Mx-My surface of a column's final layout with its demands (PNG).

    The demands are every combination at both ends; the hull vertices and the
    governing demand are marked. None when the layout has no surface.
    """
    from design.column_interaction import (
        demand_moments,
        hull_vertices,
        plot_surface,
        surface_for,
    )

    first = rows.iloc[0]
    layout = _decode_bar_layout(first.get("Bar_Layout_Data"))
    frame = pd.Series({
        "UniqueName": first["UniqueName"], "Width": first.get("Width_mm"),
        "Depth": first.get("Depth_mm"), "Diameter": first.get("Diameter_mm"),
        "f'c": first.get("f'c_MPa"), "fy": first.get("fy_MPa"), "fys": first.get("fyt_MPa"),
        "DesignCover": first.get("DesignCover"),
    })
    try:
        bars = int(first["Longitudinal_Bars"])
        engine, section = _build_column_section(frame, bars, dmain, dties, cover, is_smrf,
                                                bundle_layout=layout)
        surface = surface_for(engine, section, layout)
    except (ValueError, KeyError, TypeError):
        return None
    if surface is None:
        return None
    forces = pd.DataFrame({key: pd.to_numeric(rows.get(key), errors="coerce")
                           for key in ("Pu_kN", "Mu2_kNm", "Mu3_kNm")}).dropna()
    points = np.array([[p * 1e3, *demand_moments(m2 * 1e6, m3 * 1e6)]
                       for p, m2, m3 in forces.itertuples(index=False)]).reshape(-1, 3)
    hull = hull_vertices(points) if len(points) else np.array([], dtype=int)
    utilization = [surface.utilization(*point) for point in points]
    governing = int(np.argmax(utilization)) if utilization else None
    title = f"{first['UniqueName']} - {first.get('Section')} - {bars} bars"
    return plot_surface(surface, points, path, title, hull, governing)


def build_column_calc_report(report: pd.DataFrame, filepath: str, information: list,
                             figure_options: dict | None = None) -> str | None:
    """Write the column calculation PDF from the report (internal field names).

    ``figure_options`` (dmain, dties, cover, is_smrf) adds each column's 3D
    interaction surface with its demands.
    """
    import shutil

    folder = os.path.dirname(os.path.abspath(filepath))
    figure_dir = "figures_tmp"
    figures: dict[str, tuple[str, str]] = {}
    if figure_options:
        os.makedirs(os.path.join(folder, figure_dir), exist_ok=True)
        for name, rows in report.groupby("UniqueName", sort=False):
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(name))
            relative = f"{figure_dir}/{safe}.png"
            if column_interaction_figure(rows, os.path.join(folder, relative), **figure_options):
                figures[name] = (relative, "Design interaction surface (phi Pn, phi Mnx, phi "
                                 "Mny) of the final bar layout with every combination at both "
                                 "ends; the hull vertices decide the check, the star governs.")
    members = []
    for name, rows in report.groupby("UniqueName", sort=False):
        member = _column_calc_member(rows)
        if name in figures:
            member.figures.append(figures[name])
        members.append(member)
    if not members:
        raise ValueError("COLUMN DESIGN contains no designed columns to report.")
    try:
        return build_calc_report(
            "Concrete Column Design Calculations (ACI 318M-14)",
            list(information) + [("Columns reported", len(members))],
            ["Mark", "Column", "Story", "Size (mm)", "Bars", "Flexure D/C", "Shear D/C",
             "Status"],
            members,
            filepath,
            "p{1.5cm}p{3cm}p{1.4cm}p{2.4cm}p{1.2cm}p{2cm}p{2cm}p{2.8cm}",
        )
    finally:
        shutil.rmtree(os.path.join(folder, figure_dir), ignore_errors=True)


def export_column_pdf(report: pd.DataFrame, path: str, is_smrf: bool, dmain: float,
                      dties: float, cover: float) -> str | None:
    """The column calculation report (PDF); None when LaTeX fails."""
    information = [
        ("Design code", "ACI 318M-14"),
        ("Seismic design (SMRF)", "Yes" if is_smrf else "No"),
        ("Vertical bar diameter (mm)", number(dmain, 0)),
        ("Tie bar diameter (mm)", number(dties, 0)),
        ("Concrete cover (mm)", number(cover, 0)),
        ("Values shown", "Governing load combination at each end of each column"),
        ("Flexure and axial", "ACI design surface (phi Pn, phi Mnx, phi Mny), capacity along "
                              "the demand's moment direction at phi Pn = Pu"),
    ]
    return build_column_calc_report(report, path, information, {
        "dmain": dmain, "dties": dties, "cover": cover, "is_smrf": is_smrf})


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
