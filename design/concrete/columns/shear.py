"""Column shear: the analysis shear and the probable-moment shear Ve (NSCP 422.5, 418.7.6; ACI 22.5, 18.7.6)."""

import math
import pandas as pd
from design.criteria import dcr_targets
from design.concrete.columns.section import (
    ColumnFlexureDesign,
)
from design.concrete.columns.tables import (
    _numeric,
    _require_columns,
    _section_bending_angle,
)
from design.concrete.columns.layouts import (
    _build_column_section,
)
from design.concrete.columns.flexure import (
    USE_INTERACTION_SURFACE,
    _column_force_at_end,
)
from design.concrete.columns.report import (
    CAPACITY_SHEAR_SKIPPED,
)


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
    from design.concrete.beams.sections import BeamFlexureDesign

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


SPIRAL_LEGS = 2                 # a spiral crosses a shear plane twice (ACI 22.5.10.5.3)
CIRCULAR_DEPTH_FACTOR = 0.8     # d of a circular section, as a share of D (ACI 22.5.2.2)
SPIRAL_SHEAR_FAIL = ("FAIL: the spiral cannot carry the shear at its closest pitch "
                     "- larger spiral bar or section")


def spiral_pitch(pitch: float, least: float) -> float:
    """The pitch a spiral is built at, mm: ``pitch`` taken down to 5 mm, or to
    1 mm where 5 would bring it under ``least`` (the bar and the least clear
    spacing, NSCP 425.7.3.1 (ACI 25.7.3.1)). Never taken up: a pitch rounded up gives less
    spiral than was counted."""
    for step in (5.0, 1.0):
        rounded = math.floor(pitch / step + 1e-9) * step
        if rounded >= least - 1e-9:
            return rounded
    return pitch


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
    capacity_design: bool = True,
    capacity_note: str | None = None,
) -> tuple[list[dict], int]:
    """Check column shear in both local directions and size transverse legs.

    ``capacity_design`` False leaves out the probable-moment shear Ve of a
    special moment frame column and designs for the analysis shear (the user's
    choice for the short foundation-level columns and for the topmost
    columns); Vc is still taken as zero. ``capacity_note`` is what the
    results then say in place of Ve.

    ``clear_height`` is the clear height lu between the beams (mm) for the
    capacity shear Ve of a special moment frame column. ``beam_moment_limits``
    maps (shear direction "V2"/"V3", end "I"/"J") to the end moment the beams can
    deliver to this column, kN-m: NSCP 418.7.6.1.1 (ACI 18.7.6.1.1) lets Ve stop at the shear that
    the joint strengths (beam Mpr) can develop.

    A circular column has a spiral, not ties: it has two legs across any
    shear plane and no more can be added (NSCP 422.5.10.5.3; ACI 22.5.10.5.3), and its d is 0.8 of
    the diameter (NSCP 422.5.2.2; ACI 22.5.2.2). The shear it cannot carry at the given pitch
    is answered with a closer pitch: ``Shear_Spacing_Limit_mm`` then holds the
    pitch the shear needs.

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
    spiral = engine.shape == "circular"
    if spiral:
        starting_legs = {"V2": SPIRAL_LEGS, "V3": SPIRAL_LEGS}
    provided_legs = dict(starting_legs)

    shear_target = dcr_targets.limit(dcr_targets.COLUMN, dcr_targets.SHEAR)
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
    use_capacity = is_smrf and capacity_design
    if use_capacity:
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
            from design.concrete.columns.interaction import demand_moments, surface_for

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
            probable_moments = [math.nan, math.nan]   # bottom (I) and top (J), kN-m
            if use_capacity:
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

            effective_depth = CIRCULAR_DEPTH_FACTOR * overall_depth if spiral else max(
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
                # With a target ratio below 1 the ties are sized for the shear
                # divided by it, so Vu / phi Vn stays at or below the target.
                required_vs = max(
                    0.0, design_shear / shear_target * 1000.0 / phi_shear - shear_concrete
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
                if spiral:
                    # no leg can be added to a spiral: the pitch answers the shear
                    required_legs = SPIRAL_LEGS
                    needed = min(required_vs, vs_max)
                    if needed > 0:
                        shear_spacing_limit = min(
                            shear_spacing_limit,
                            SPIRAL_LEGS * tie_area * fyt * effective_depth / needed)
                provided_legs[shear_name] = max(provided_legs[shear_name], required_legs)
                output.append(
                    {
                        "UniqueName": member,
                        "Combo": combo,
                        "End": end,
                        "Shear_Direction": shear_name,
                        "Analysis_Shear_kN": analysis_shear,
                        "Capacity_Based_Ve_kN": capacity_shear_kN,
                        "Capacity_Shear_Note": (
                            "" if use_capacity or not is_smrf
                            else capacity_note or CAPACITY_SHEAR_SKIPPED),
                        "Design_Shear_kN": design_shear,
                        "Vc_kN": shear_concrete / 1000.0,
                        "Concrete_Shear_Strength_Neglected": is_smrf,
                        "Required_Transverse_Legs": required_legs,
                        "Shear_Spacing_Limit_mm": shear_spacing_limit,
                        "Clear_Height_mm": clear_length,
                        # what Ve is made of: (Mpr bottom + Mpr top) / lu, each Mpr
                        # already limited to what the beams can deliver
                        "Mpr_Bottom_kNm": probable_moments[0],
                        "Mpr_Top_kNm": probable_moments[1],
                        "_effective_depth_mm": effective_depth,
                        "_shear_concrete_N": shear_concrete,
                        "_vs_max_N": vs_max,
                        "Vc_Assumption": (
                            "0 used conservatively for all SMRF checks"
                            if is_smrf
                            else "NSCP 422.5.6 / 422.5.7 (ACI 22.5.6 / 22.5.7) with the axial load of the end"
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
        at_vs_max = steel_shear >= item["_vs_max_N"] - 1e-6
        item["Shear_Check"] = (
            "PASS" if item["Design_Shear_kN"] <= shear_capacity * shear_target + 1e-9
            else SPIRAL_SHEAR_FAIL if spiral and not at_vs_max
            else "FAIL: Vs above 0.66 sqrt(fc') b d (NSCP 422.5.1.2; ACI 22.5.1.2) - enlarge the section"
            if item["Design_Shear_kN"] > shear_capacity + 1e-9
            else f"FAIL: above the target ratio {shear_target:g} at the largest Vs "
                 "- enlarge the section"
        )
        del item["_effective_depth_mm"]
        del item["_shear_concrete_N"]
        del item["_vs_max_N"]
    return output, max(provided_legs.values())
