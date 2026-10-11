"""Beam-column joints of a special moment frame: strong column - weak beam and joint shear (ACI 18.7.3, 18.8)."""

import math
import numpy as np
import pandas as pd
from design.criteria.code_config import CODE
from design.criteria import dcr_targets
from design.concrete.columns.section import (
    ColumnFlexureDesign,
)
from design.concrete.columns.tables import (
    _frame_local_axes,
    _normalize_names,
    _normalize_object_name,
    _numeric,
    _require_columns,
    _section_bending_angle,
    _unit_vector,
)
from design.concrete.columns.layouts import (
    _build_column_section,
)
from design.concrete.columns.flexure import (
    USE_INTERACTION_SURFACE,
    _column_force_at_end,
    _strong_column_hull_combos,
)
from design.concrete.columns.shear import (
    _beam_end_reinforcement,
)
from design.concrete.columns.report import (
    BCC_EXEMPT_TEXT,
)


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

    from design.concrete.beams.results import restore_beam_result_labels

    connectivity = connectivity.copy()
    frame_data = frame_data.copy()
    factored_loads = factored_loads.copy()
    # The design export labels its first/second/third bar layers per beam end.
    # Restore those stable names before mapping I/J-end steel into joint checks.
    beam_design = restore_beam_result_labels(beam_design)
    for frame in (connectivity, frame_data, factored_loads, beam_design, column_results):
        if "UniqueName" in frame.columns:
            frame["UniqueName"] = _normalize_names(frame["UniqueName"])
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

    beam_geometry_rows: dict[str, pd.Series] = {}

    def beam_geometry_row(member: str) -> pd.Series:
        """Return beam geometry from BEAM DESIGN, falling back to FRAME DATA (read once
        for each beam: the joint checks ask for it tens of thousands of times)."""
        if member not in beam_geometry_rows:
            beam_geometry_rows[member] = (beam_result_groups[member].iloc[0]
                                          if member in beam_result_groups
                                          else frame_data_row(member))
        return beam_geometry_rows[member]

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
            from design.concrete.columns.interaction import demand_moments, surface_for

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
                    required_ratio = dcr_targets.limit(dcr_targets.COLUMN,
                                                       dcr_targets.STRONG_COLUMN)
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
                    joint_target = dcr_targets.limit(dcr_targets.COLUMN,
                                                     dcr_targets.JOINT_SHEAR)
                    joint_shear_check = (
                        "PASS" if joint_shear_demand <= phi_vn_kN * joint_target
                        else "FAIL: joint shear above its strength (ACI 18.8.4)"
                        if joint_shear_demand > phi_vn_kN
                        else f"FAIL: joint shear above the target ratio {joint_target:g}"
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
