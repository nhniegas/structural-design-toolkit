"""The column design from the extracted tables (``design_columns``) and the size trial of the design loop."""

import math
import numpy as np
import pandas as pd
from design.criteria import dcr_targets
from design.concrete.columns.slenderness import (
    ColumnSection,
    FrameBeam,
    FrameColumn,
    FrameModel,
    MemberSlenderness,
    magnify_member,
    plan_direction,
    simple_geometry,
)
from utilities._calc_report import number
from design.concrete.columns.section import (
    ColumnFlexureDesign,
)
from design.concrete.columns.tables import (
    _as_etabs_dataframe,
    _clean_table,
    _collapse_combo_permutations,
    _common_column_mark,
    _deduplicate_frame_data,
    _expand_combo_permutations,
    _extract_frame_angles,
    _extract_point_coordinates,
    _frame_local_axes,
    _normalize_names,
    _normalize_object_name,
    _numeric,
    _require_columns,
    _section_bending_angle,
    _story_order_from_stacks,
    _to_compression_positive,
)
from design.concrete.columns.layouts import (
    _build_column_section,
    _enumerate_column_bar_layouts,
)
from design.concrete.columns.flexure import (
    USE_INTERACTION_SURFACE,
    _clear_end_force_index,
    _column_force_at_end,
    _evaluate_column_candidate,
)
from design.concrete.columns.transverse import (
    _alternating_support_legs,
    _bars_per_edge,
    _column_layout_summary,
    _column_transverse_candidate_passes,
    _confined_core,
    _encode_bar_layout,
    _meets,
    _minimum_tie_diameter,
    _smrf_so_limit,
    _smrf_transverse_design,
)
from design.concrete.columns.shear import (
    _column_shear_checks,
    _column_stacks,
    spiral_pitch,
)
from design.concrete.columns.joints import (
    _evaluate_smrf_joints,
)
from design.concrete.columns.report import (
    CAPACITY_SHEAR_SKIPPED,
    FOUNDATION_LEVEL_SKIPPED,
    TOP_CAPACITY_SHEAR_SKIPPED,
    TOP_LEVEL_SKIPPED,
    _build_consolidated_column_report,
    _skip_joint_checks,
)
from design.concrete.columns.options import (
    _apply_bottom_story_cover,
)



def transverse_text(tie: float, kind: str, spacing: float, legs_x, legs_y) -> str:
    """The transverse reinforcement of a column in words. A spiral has a pitch and
    no legs along an edge; ties have their legs along X and along Y."""
    if "spiral" in str(kind).lower():
        return f"{tie:g} mm {kind} @ {spacing:.0f} mm pitch"
    return f"{tie:g} mm {kind} @ {spacing:.0f} mm; X: {legs_x} legs, Y: {legs_y} legs"


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
    slenderness: bool = True,
    check_top_level: bool = True,
    check_foundation_level: bool = True,
) -> tuple[pd.DataFrame, list, pd.DataFrame]:
    """Design every column and run the SMRF checks.

    ``slenderness`` adds the member slenderness of ACI 6.6.4.5 (see
    ``design/concrete/columns/slenderness.py``): the moments at the column ends are
    magnified before the bars are chosen. The sway effects must be in the
    forces already (the ETABS P-delta analysis).

    ``check_top_level`` False leaves out the strong column - weak beam and
    joint shear checks at the joints on top of the columns (no column above),
    and designs the shear of those topmost columns for the analysis shear
    instead of the probable-moment shear Ve.
    ``check_foundation_level`` False leaves them out at the joints of the
    bottom-most story columns, whose shear is then designed for the analysis
    shear instead of the probable-moment shear Ve. Both are asked by
    ``sdt columns``; what is left out is stated in the report.

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
            frame["UniqueName"] = _normalize_names(frame["UniqueName"])
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
    # The slenderness needs the frame around each column (lu and k) whether or
    # not the seismic checks are on; without the joint coordinates it uses the
    # joint to joint length and k = 1.0.
    slender_angles, slender_points = frame_angles, point_coordinates
    if slenderness and not is_smrf:
        try:
            slender_points = _extract_point_coordinates(
                _as_etabs_dataframe(tables.get("POINTS"), "Point Object Connectivity"))
            slender_angles = _extract_frame_angles(
                _as_etabs_dataframe(tables.get("LOCAL AXES"), "Frame Assignments - Local Axes"))
        except (ValueError, KeyError, TypeError):
            slender_angles, slender_points = {}, {}
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
    slender_results: dict[str, MemberSlenderness] = {}
    slender_geometry: dict[str, dict] = {}
    if slenderness:
        from design.concrete.columns.slenderness import supports_from_table

        supports = tables.get("SUPPORTS")
        frame_model = _slenderness_frame_model(
            frame_data, connection_by_name, slender_points, slender_angles,
            supports_from_table(supports if isinstance(supports, pd.DataFrame) else None))
        for _, row in column_rows.iterrows():
            member = str(row["UniqueName"])
            if member not in force_frames:
                continue
            geometry = _slenderness_geometry(member, frame_model, force_frames[member],
                                             frame_data, connection_by_name)
            engine, _ = _build_column_section(row, 4, dmain, dties, cover, is_smrf)
            result = magnify_member(force_frames[member], _slenderness_section(engine), geometry)
            slender_results[member] = result
            slender_geometry[member] = geometry
            force_frames[member] = result.forces
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

    # Levels where the user chose not to run the capacity-design checks.
    skipped_joints: dict[str, str] = {}
    foundation_columns: set[str] = set()
    top_columns: set[str] = set()       # no column stands on them
    if is_smrf and not (check_top_level and check_foundation_level):
        ends_of = {member: column_ends(member) for member in column_candidates}
        if not check_top_level:
            bottoms = {bottom for bottom, _ in ends_of.values()}
            for member, (_, top) in ends_of.items():
                if top not in bottoms:
                    skipped_joints[top] = TOP_LEVEL_SKIPPED
                    top_columns.add(member)
        if not check_foundation_level:
            story_of = {str(member): str(story) for member, story in story_by_member.items()}
            order = _story_order_from_stacks(connectivity, story_of)
            for member, (bottom, top) in ends_of.items():
                if order and story_of.get(member) == order[0]:
                    foundation_columns.add(member)
                    skipped_joints[bottom] = FOUNDATION_LEVEL_SKIPPED
                    skipped_joints[top] = FOUNDATION_LEVEL_SKIPPED

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
                from design.concrete.columns.interaction import demand_moments, surface_for

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
        initial_joints = _skip_joint_checks(initial_joints, skipped_joints)

        # the joints as plain records, read at every pass of the loop below
        joint_records = initial_joints.to_dict("records")
        while not initial_joints.empty:
            exempt = (initial_joints["BCC_Exempt"].eq(True)
                      if "BCC_Exempt" in initial_joints.columns
                      else pd.Series(False, index=initial_joints.index))
            strong_column = dcr_targets.limit(dcr_targets.COLUMN, dcr_targets.STRONG_COLUMN)
            below = pd.Series([current_joint_ratio(record) < strong_column - 1e-9
                               for record in joint_records], index=initial_joints.index,
                              dtype=bool)
            failing_rows = initial_joints.loc[
                initial_joints["Sum_Column_Mn_kNm"].notna()
                & initial_joints["Sum_Beam_Mn_kNm"].notna()
                & ~exempt
                & below
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
                "PASS" if ratio >= dcr_targets.limit(dcr_targets.COLUMN,
                                                     dcr_targets.STRONG_COLUMN) else "FAIL"
            )

            # Joint shear demand uses the BEAMS' probable moments only, so adding
            # column bars changes the strong-column ratio but not the joint shear.
        joint_results = initial_joints

    # Clear height of each column for its capacity shear (capacity_clear_height).
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
        bottom_joint, top_joint = column_ends(member)
        joint_length = 0.0
        if bottom_joint in point_coordinates and top_joint in point_coordinates:
            joint_length = float(np.linalg.norm(
                point_coordinates[top_joint] - point_coordinates[bottom_joint]))
        elif "Length" in connection_by_name.columns:
            length = pd.to_numeric(connection_by_name.loc[member].get("Length"), errors="coerce")
            joint_length = float(length) if length == length else 0.0
        clear_heights[member] = capacity_clear_height(
            joint_length, float(stations.max() - stations.min()),
            beam_depth_at_joint.get(top_joint, 0.0))

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
                capacity_design=member not in foundation_columns and member not in top_columns,
                capacity_note=(CAPACITY_SHEAR_SKIPPED if member in foundation_columns
                               else TOP_CAPACITY_SHEAR_SKIPPED),
            )
            return checks

        member_shear_checks = shear_checks_at(spacing_provided)
        # Shear ties at no more than d/2 (d/4 for a large Vs), ACI 10.7.6.5.2.
        shear_spacing = min(
            (check["Shear_Spacing_Limit_mm"] for check in member_shear_checks),
            default=math.inf,
        )
        if shear_spacing < spacing_provided - 1e-6:
            if final_engine.shape == "circular":
                # A spiral takes no added leg: the shear is answered with a closer
                # pitch, down to the least clear spacing of ACI 25.7.3.1. Under
                # that the shear check fails.
                least = final_engine.solve_max_spacing()["min_spiral_pitch_mm"]
                spacing_provided = spiral_pitch(max(shear_spacing, least), least)
            else:
                spacing_provided = math.floor(shear_spacing / 5.0) * 5.0
            member_shear_checks = shear_checks_at(spacing_provided)
            if final_engine.shape == "circular" and is_smrf:
                core = final_engine.diameter - 2.0 * final_engine.cc
                provided = math.pi * final_engine.dties ** 2 / (core * spacing_provided)
                for key in ("Provided_Ash_s_Ratio_X", "Provided_Ash_s_Ratio_Y",
                            "Provided_Confinement_Ratio"):
                    transverse[key] = provided
                transverse["Spiral_Clear_Spacing_mm"] = spacing_provided - final_engine.dties
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
                if _meets(transverse["Provided_Ash_s_Ratio_X"],
                          transverse["Required_Confinement_Ratio_X"])
                and _meets(transverse["Provided_Ash_s_Ratio_Y"],
                           transverse["Required_Confinement_Ratio_Y"])
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

        member_slenderness = slender_results.get(member)
        if member_slenderness is not None:
            for check in selected_checks:
                check.update(member_slenderness.records.get(
                    (str(check["Combo"]), str(check["End"])), {}))
            if member_slenderness.failed:
                status = "FAIL: column slenderness (ACI 6.2.6) - enlarge the section"
        force_check_rows.extend(selected_checks)
        layout_summary = _column_layout_summary(selected_layout)
        transverse_summary = transverse_text(
            dties, transverse["Transverse_Type"], spacing_provided,
            transverse["Transverse_Legs_X"], transverse["Transverse_Legs_Y"])
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
                # a spiral has no legs along an edge
                "Transverse_Legs_X": (np.nan if final_engine.shape == "circular"
                                      else transverse["Transverse_Legs_X"]),
                "Transverse_Legs_Y": (np.nan if final_engine.shape == "circular"
                                      else transverse["Transverse_Legs_Y"]),
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
    # lu and k of every column, for sizing trial sections on the same forces
    report.attrs["slenderness_geometry"] = slender_geometry
    report.attrs["foundation_columns"] = sorted(foundation_columns)
    report.attrs["top_columns"] = sorted(top_columns)
    # what the capacity shear Ve of each column was worked out with, for the same trials
    report.attrs["clear_heights"] = dict(clear_heights)
    report.attrs["beam_moment_limits"] = {member: dict(limits) for member, limits
                                          in beam_moment_limits.items()}
    report.attrs["skipped_joints"] = dict(skipped_joints)
    return report, report_groups, joint_results


def capacity_clear_height(joint_length: float, station_span: float,
                          top_beam_depth: float) -> float:
    """Clear height lu of a column for its capacity shear Ve = sum of Mpr / lu, mm.

    It is the joint-to-joint length less the deepest beam at the top joint
    (beams hang below the floor). The force stations of ETABS already stop at
    the rigid end zones, so their span has the beam taken out once; taking it
    out of that span again made lu too short, and far too short for a column
    whose end zone ETABS makes shorter than the beam, such as a corner column
    of a footing story. The station span is the upper limit, and what is used
    when the joint-to-joint length is not known.
    """
    if joint_length > 0:
        clear = joint_length - max(top_beam_depth, 0.0)
        if clear > 0:
            return min(clear, station_span) if station_span > 0 else clear
    return station_span


def _slenderness_section(engine: ColumnFlexureDesign) -> ColumnSection:
    if engine.shape == "circular":
        return ColumnSection(engine.diameter, engine.diameter, True, engine.fc)
    return ColumnSection(engine.width, engine.height, False, engine.fc)


def _slenderness_frame_model(frame_data: pd.DataFrame, connection_by_name: pd.DataFrame,
                             point_coordinates: dict, frame_angles: dict,
                             supports: dict | None = None) -> FrameModel | None:
    """The columns and beams with their joints, or None without joint coordinates.

    ``supports`` is the model's joint restraints (``supports_from_table``);
    None when they were not read."""
    if not point_coordinates:
        return None
    data = frame_data.drop_duplicates("UniqueName").set_index("UniqueName")
    columns: dict[str, FrameColumn] = {}
    beams: dict[str, FrameBeam] = {}

    def number(value) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    for member, connection in connection_by_name.iterrows():
        member = str(member)
        if member not in data.index:
            continue
        row = data.loc[member]
        point_i = _normalize_object_name(connection["UniquePtI"])
        point_j = _normalize_object_name(connection["UniquePtJ"])
        if point_i not in point_coordinates or point_j not in point_coordinates:
            continue
        start, end = point_coordinates[point_i], point_coordinates[point_j]
        length = float(np.linalg.norm(end - start))
        if length <= 0:
            continue
        kind = str(connection["DesignType"]).strip().casefold()
        fc = number(row.get("f'c"))
        if kind == "column":
            if start[2] > end[2]:
                point_i, point_j, start, end = point_j, point_i, end, start
            diameter = number(row.get("Diameter"))
            width, depth = number(row.get("Width")), number(row.get("Depth"))
            circular = diameter > 0 and not (width > 0 and depth > 0)
            section = (ColumnSection(diameter, diameter, True, fc) if circular
                       else ColumnSection(width, depth, False, fc))
            try:
                _, local_2, _ = _frame_local_axes(start, end, frame_angles.get(member, 0.0))
            except ValueError:
                local_2 = np.array([1.0, 0.0, 0.0])
            columns[member] = FrameColumn(member, point_i, point_j, length, section,
                                          plan_direction(local_2))
        elif kind == "beam":
            beams[member] = FrameBeam(member, point_i, point_j, length, number(row.get("Width")),
                                      number(row.get("Depth")), fc, plan_direction(end - start))
    return FrameModel(columns, beams, supports=supports) if columns else None


def _slenderness_geometry(member: str, frame_model: FrameModel | None, forces: pd.DataFrame,
                          frame_data: pd.DataFrame, connection_by_name: pd.DataFrame) -> dict:
    """lu and k of a column about its two axes."""
    if frame_model is not None and member in frame_model.columns:
        return frame_model.geometry(member)
    stations = pd.to_numeric(forces["Station"], errors="coerce").dropna()
    length = float(stations.max() - stations.min()) if not stations.empty else 0.0
    top_depth = 0.0
    if member in connection_by_name.index and {"Depth", "DesignType"} <= set(frame_data.columns):
        top = _normalize_object_name(connection_by_name.loc[member]["UniquePtJ"])
        beams = frame_data.loc[
            frame_data["DesignType"].astype(str).str.strip().str.casefold().eq("beam"),
            ["UniqueName", "Depth"]]
        depth_of = dict(zip(beams["UniqueName"].astype(str),
                            pd.to_numeric(beams["Depth"], errors="coerce")))
        for name, connection in connection_by_name.iterrows():
            if str(name) in depth_of and top in (
                    _normalize_object_name(connection["UniquePtI"]),
                    _normalize_object_name(connection["UniquePtJ"])):
                depth = depth_of[str(name)]
                if depth == depth:
                    top_depth = max(top_depth, float(depth))
    return simple_geometry(length, top_depth)
