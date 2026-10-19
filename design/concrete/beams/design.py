"""The beam design from the extracted tables (``design_beams``)."""

import math
import numpy as np
import pandas as pd
from design.criteria import dcr_targets
from design.criteria.code_config import CODE, AciCode, override
from design.concrete.beams.deflection import (
    LIMIT_DAMAGED,
    DeflectionStages,
    add_deflection_columns,
)
from design.concrete.beams.supports import (
    SMRF_APPLIED,
    identify_cantilever_beams,
    seismic_rules_of,
)
from design.concrete.beams.sections import (
    BeamFlexureDesign,
    BeamSeismicDesign,
    BeamShearDesign,
    BeamTorsionDesign,
    check_alternating_tie_legs,
    check_stirrup_leg_anchorage,
    distribute_longitudinal_torsion_and_skin,
    get_layer_columns,
    get_location_effective_depth,
)
from design.concrete.beams.results import (
    _beam_sort_key,
    sort_beam_rows,
)


COMPATIBILITY_TORSION = ("Compatibility torsion: Tu taken as at most phi Tcr (NSCP 422.7.3.2; ACI 22.7.3.2); "
                         "Tu in the table is the analysis value")


ANALYSIS_TORSION = "Analysis torsion (a cantilever cannot shed its torsion)"


def torsion_basis_of(support_status: object, compatibility_torsion: bool) -> str:
    """Which torsion a beam is designed for, as text for the results."""
    if not compatibility_torsion:
        return "Analysis torsion"
    return ANALYSIS_TORSION if "Cantilever" in str(support_status) else COMPATIBILITY_TORSION


def _torsion_code(torsion_basis: str, code: AciCode) -> AciCode:
    """The code values with the torsion reduction of NSCP 422.7.3.2 (ACI 22.7.3.2) on or off."""
    if torsion_basis == COMPATIBILITY_TORSION:
        return override(code, beam_torsion__allow_redistribution=True)
    return code


def _smallest(values: np.ndarray):
    """The smallest of ``values`` as a table column gives it: NaN left out, NaN when none is left."""
    valid = values[~np.isnan(values)]
    return valid.min() if valid.size else np.nan


def _largest(values: np.ndarray):
    """The largest of ``values``, NaN left out; NaN when none is left."""
    valid = values[~np.isnan(values)]
    return valid.max() if valid.size else np.nan


def _beam_design_part(arguments: tuple) -> pd.DataFrame:
    """The design of one part of the beams, in a process of its own."""
    targets, props, forces, options = arguments
    dcr_targets.use(targets)     # the active target ratios are per process
    return _execute_beam_design(props, forces, progress=None, **options)


def execute_beam_design(
    df_beam_props: pd.DataFrame,
    df_frame_forces: pd.DataFrame,
    enable_seismic_design: bool,
    gravity_combo_name: str,
    Pu_axial_load: float = 50.0,
    code: AciCode = CODE,
    progress=None,
    compatibility_torsion: bool = False,
    exempt_short_spans: bool = False,
) -> pd.DataFrame:
    """The beam design, shared between several processes on a large model.

    Each beam is designed on its own forces, whatever the others are, so the
    beams are split into consecutive parts of their sorted names, each part
    is designed by ``_execute_beam_design`` in a process, and the results are
    joined in that order: the rows and the values are those of one process.
    One process is used when ``design.parallel`` is not enabled, on a small
    model, or if the processes fail to start.
    """
    from design import parallel

    options = dict(enable_seismic_design=enable_seismic_design,
                   gravity_combo_name=gravity_combo_name, Pu_axial_load=Pu_axial_load,
                   code=code, compatibility_torsion=compatibility_torsion,
                   exempt_short_spans=exempt_short_spans)
    names = df_beam_props["UniqueName"] if "UniqueName" in df_beam_props.columns else []
    workers = parallel.workers_for(len(names))
    if workers > 1 and all(isinstance(name, str) for name in names):
        try:
            ordered = sorted(set(names))                    # the order of the results
            parts = parallel.chunks(ordered, workers)
            part_of = {name: index for index, part in enumerate(parts) for name in part}
            prop_part = df_beam_props["UniqueName"].map(part_of)
            force_part = df_frame_forces["UniqueName"].map(part_of)
            targets = dcr_targets.active()
            jobs = [(targets, df_beam_props[prop_part == index],
                     df_frame_forces[force_part == index], options)
                    for index in range(len(parts))]
            if progress is not None:
                progress(f"Beam design in {workers} processes\n{len(ordered)} beams")
            done, results = 0, []
            for part, result in zip(parts, parallel.pool(workers).map(_beam_design_part, jobs)):
                results.append(result)
                done += len(part)
                if progress is not None:
                    progress(f"Beam design in {workers} processes\n"
                             f"{done} of {len(ordered)} beams designed")
            results = [r for r in results if not r.empty]
            return pd.concat(results, ignore_index=True) if results else pd.DataFrame()
        except Exception:   # the processes could not start or stopped: design here
            parallel.shutdown()
    return _execute_beam_design(df_beam_props, df_frame_forces, progress=progress, **options)


def _execute_beam_design(
    df_beam_props: pd.DataFrame,
    df_frame_forces: pd.DataFrame,
    enable_seismic_design: bool,
    gravity_combo_name: str,
    Pu_axial_load: float = 50.0,
    code: AciCode = CODE,
    progress=None,
    compatibility_torsion: bool = False,
    exempt_short_spans: bool = False,
) -> pd.DataFrame:
    """Executes the full beam design pipeline and returns the results DataFrame.

    ``exempt_short_spans`` leaves the girders with a clear span under 4d out
    of the SMRF rules (``seismic_rules_of``); the results then say for every
    beam which treatment it got.

    ``compatibility_torsion`` designs for a torsion of at most phi Tcr (ACI
    22.7.3.2), which the code allows where the torsion comes from the twist
    of a statically indeterminate frame and can redistribute after cracking.
    A cantilever keeps its analysis torsion: nothing else can take it.

    ``progress`` (optional) receives one status text at a time: the beam, its
    level, the load combination and the check being performed.
    """

    def report(member, story, combo, check) -> None:
        if progress is not None:
            progress(f"Beam {member}  |  Level {story}\nCombo: {combo}\nCheck: {check}")

    detailing = code.beam_detailing
    seismic_cfg = code.beam_seismic

    # =============================================================================
    # 2. BUILD EXTRACTION DEMANDS DATAFRAME PER COMBO
    # =============================================================================
    design_rows = []
    # The rows of each beam, found once: looking through the whole force table again for
    # every beam took most of the time on a large model. The rows keep their order.
    forces_of = {name: rows for name, rows in df_frame_forces.groupby("UniqueName", sort=False)}
    no_forces = df_frame_forces.iloc[0:0]

    for _, prop_row in df_beam_props.iterrows():
        u_name = prop_row["UniqueName"]
        h = prop_row.get("Depth", prop_row.get("Height", 500))
        two_h = seismic_cfg.hoop_zone_depth_factor * h

        df_forces_beam = forces_of.get(u_name, no_forces)

        if not df_forces_beam.empty:
            min_st = df_forces_beam["Station"].min()
            max_st = df_forces_beam["Station"].max()
            span_length = max_st - min_st
            # Clear span ln between the support faces, for the probable-moment
            # shear Ve = (Mpr1 + Mpr2) / ln (ACI 18.6.5.1).
            support_half = sum(
                0.5 * float(prop_row.get(key) or 0.0)
                for key in ("SupportWidth_I", "SupportWidth_J")
                if pd.notna(prop_row.get(key))
            )
            clear_span = clear_span_of(span_length, prop_row.get("JointLength"), support_half)

            # Moment design zones use the end quarter of the modeled span.
            m_left_boundary = min_st + detailing.moment_zone_fraction * span_length
            # Mirror the left end zone so the central flexural region excludes both end quarters.
            m_right_boundary = max_st - detailing.moment_zone_fraction * span_length

            # Shear/torsion end zones extend 2h from each beam end.
            v_left_boundary = min_st + two_h
            v_right_boundary = max_st - two_h

            combos = df_forces_beam["Combo"].unique()
            # one line for the beam: the lines of its combinations come faster than
            # the window shows them, and only the first one was ever seen
            report(u_name, prop_row.get("Story", "-"),
                   f"all {len(combos)} combinations" if len(combos) != 1 else combos[0],
                   "Demands: Mu, Vu and Tu at the supports and midspan")

            # The numbers of the beam as arrays, taken once: the zones of every
            # combination are then cut from them, not from the table each time.
            combo_of = df_forces_beam["Combo"].to_numpy()
            station = df_forces_beam["Station"].to_numpy(dtype=float)
            moment = df_forces_beam["M3"].to_numpy(dtype=float)
            shear = np.abs(df_forces_beam["V2"].to_numpy(dtype=float))
            torsion = np.abs(df_forces_beam["T"].to_numpy(dtype=float))
            base_row = prop_row.to_dict()

            for combo in combos:
                own = combo_of == combo
                at, m3 = station[own], moment[own]
                v2, tu = shear[own], torsion[own]

                left_m = at <= m_left_boundary
                mid_m = (at > m_left_boundary) & (at < m_right_boundary)
                right_m = at >= m_right_boundary

                left_vt = at <= v_left_boundary
                mid_vt = (at >= v_left_boundary) & (at <= v_right_boundary)
                right_vt = at >= v_right_boundary

                # Convert negative M3 values into positive design-demand magnitudes for the top face.
                Mneg_left = abs(min(0.0, _smallest(m3[left_m]))) if left_m.any() else 0.0
                # Positive M3 envelope is assigned to bottom-face flexural design.
                Mpos_left = max(0.0, _largest(m3[left_m])) if left_m.any() else 0.0

                Mneg_mid = abs(min(0.0, _smallest(m3[mid_m]))) if mid_m.any() else 0.0
                Mpos_mid = max(0.0, _largest(m3[mid_m])) if mid_m.any() else 0.0

                Mneg_right = abs(min(0.0, _smallest(m3[right_m]))) if right_m.any() else 0.0
                Mpos_right = max(0.0, _largest(m3[right_m])) if right_m.any() else 0.0

                # Absolute peak shear in each end zone is used as the transverse design demand.
                Vd_left = _largest(v2[left_vt]) if left_vt.any() else 0.0
                V2h = _largest(v2[mid_vt]) if mid_vt.any() else 0.0
                Vd_right = _largest(v2[right_vt]) if right_vt.any() else 0.0

                # Absolute peak torsion is paired with the shear demand for each zone.
                Td_left = _largest(tu[left_vt]) if left_vt.any() else 0.0
                T2h = _largest(tu[mid_vt]) if mid_vt.any() else 0.0
                Td_right = _largest(tu[right_vt]) if right_vt.any() else 0.0

                # Top Row
                top_row = dict(base_row)
                top_row["Combo"] = combo
                top_row["Face"] = "TOP"
                top_row["ClearSpan_Ln"] = clear_span
                top_row["Mu_left"] = Mneg_left
                top_row["Mu_mid"] = Mneg_mid
                top_row["Mu_right"] = Mneg_right
                top_row["Vu_left"] = Vd_left
                top_row["Vu_mid_2h"] = V2h
                top_row["Vu_right"] = Vd_right
                top_row["Tu_left"] = Td_left
                top_row["Tu_mid_2h"] = T2h
                top_row["Tu_right"] = Td_right
                design_rows.append(top_row)

                # Bottom Row
                bot_row = dict(base_row)
                bot_row["Combo"] = combo
                bot_row["Face"] = "BOTTOM"
                bot_row["ClearSpan_Ln"] = clear_span
                bot_row["Mu_left"] = Mpos_left
                bot_row["Mu_mid"] = Mpos_mid
                bot_row["Mu_right"] = Mpos_right
                bot_row["Vu_left"] = Vd_left
                bot_row["Vu_mid_2h"] = V2h
                bot_row["Vu_right"] = Vd_right
                bot_row["Tu_left"] = Td_left
                bot_row["Tu_mid_2h"] = T2h
                bot_row["Tu_right"] = Td_right
                design_rows.append(bot_row)

    df_beam_design_demands = pd.DataFrame(design_rows)
    if df_beam_design_demands.empty:
        return pd.DataFrame()

    # =============================================================================
    # 3. CONVERGED MULTI-COMBO REINFORCEMENT & POST-CHECK SOLVER
    # =============================================================================
    results_list = []

    for unique_name, df_b in df_beam_design_demands.groupby("UniqueName"):
        b_row = df_b.iloc[0]
        b_width = b_row.get("Width", b_row.get("b", 300))
        b_height = b_row.get("Depth", b_row.get("Height", 500))
        fc_val = b_row.get("f'c", 28)
        fy_val = b_row.get("fy", 413.69)
        fyt_val = b_row.get("fyw", b_row.get("fyt", 414))
        d_m = b_row.get("dm", b_row.get("d_main", 12))
        d_s = b_row.get("ds", b_row.get("d_stirrup", 12))
        d_side = b_row.get("dw", b_row.get("d_side", d_m))
        c_cover = b_row.get("cc", 40)
        span_ln = b_row.get("ClearSpan_Ln", 6000)

        # Seismic provisions apply to the frame members only: not to gravity beams,
        # and not to short-span girders when the engineer left those out.
        seismic_rules = seismic_rules_of(
            b_row.get("SupportStatus", ""), span_ln, b_height, c_cover, d_s, d_m,
            exempt_short_spans, code)
        seismic_here = enable_seismic_design and seismic_rules == SMRF_APPLIED

        # Target ratios of this member type: the bars and stirrups are chosen for
        # the demand divided by the target, so demand / capacity stays at or
        # below it. The demands that are reported stay the real ones.
        member_type = dcr_targets.beam_type(b_row.get("SupportStatus", ""))
        flexure_target = dcr_targets.limit(member_type, dcr_targets.FLEXURE)
        shear_target = dcr_targets.limit(member_type, dcr_targets.SHEAR)
        torsion_basis = torsion_basis_of(b_row.get("SupportStatus", ""), compatibility_torsion)
        torsion_code = _torsion_code(torsion_basis, code)

        df_grav = df_b[df_b["Combo"] == gravity_combo_name]
        Vu_grav_left = df_grav["Vu_left"].max() if not df_grav.empty else 0.0
        Vu_grav_right = df_grav["Vu_right"].max() if not df_grav.empty else 0.0
        Vu_grav_mid = df_grav["Vu_mid_2h"].max() if not df_grav.empty else 0.0

        prev_state = None
        smrf_flexure_check = "N/A"
        smrf_rho_check = "N/A"

        story = b_row.get("Story", "-")
        moments = df_b[["Mu_left", "Mu_mid", "Mu_right"]].max(axis=1)
        governing = df_b.loc[moments.idxmax(), "Combo"] if len(df_b) else "-"
        # The envelopes of the beam, taken once: they do not change while its bars are
        # chosen, and asking the table for them again at every pass took most of the time.
        combo_names = df_b["Combo"].unique()
        combo_count = df_b["Combo"].nunique()
        moment_columns = ["Mu_left", "Mu_mid", "Mu_right"]
        face_max = {face: df_b.loc[df_b["Face"] == face, moment_columns].max()
                    for face in ("TOP", "BOTTOM")}
        all_max = df_b[["Vu_left", "Vu_mid_2h", "Vu_right",
                        "Tu_left", "Tu_mid_2h", "Tu_right"]].max()
        top_face = df_b[df_b["Face"] == "TOP"]
        top_of: dict = {}
        for position, combo_name in enumerate(top_face["Combo"].to_numpy()):
            if combo_name not in top_of:
                top_of[combo_name] = top_face.iloc[position]
        while True:
            # --- STEP 3A: Flexure Design ---
            report(unique_name, story,
                   f"envelope of {combo_count} (largest moment: {governing})",
                   "Flexure at the left support, midspan and right support")
            flex_eng_left, flex_eng_mid, flex_eng_right = (
                BeamFlexureDesign(
                    b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover,
                    code=code,
                )
                for _ in range(3)
            )

            flex_eng_left.Mu_neg = face_max["TOP"]["Mu_left"] \
                / flexure_target
            flex_eng_left.Mu_pos = face_max["BOTTOM"]["Mu_left"] \
                / flexure_target

            flex_eng_mid.Mu_neg = face_max["TOP"]["Mu_mid"] \
                / flexure_target
            flex_eng_mid.Mu_pos = face_max["BOTTOM"]["Mu_mid"] \
                / flexure_target

            flex_eng_right.Mu_neg = face_max["TOP"]["Mu_right"] \
                / flexure_target
            flex_eng_right.Mu_pos = face_max["BOTTOM"]["Mu_right"] \
                / flexure_target

            max_Tu_left, max_Tu_mid, max_Tu_right = (
                all_max["Tu_left"],
                all_max["Tu_mid_2h"],
                all_max["Tu_right"],
            )
            max_Vu_left, max_Vu_mid, max_Vu_right = (
                all_max["Vu_left"],
                all_max["Vu_mid_2h"],
                all_max["Vu_right"],
            )
            real_Vu_left, real_Vu_right = max_Vu_left, max_Vu_right
            max_Tu_left, max_Tu_mid, max_Tu_right = (
                max_Tu_left / shear_target, max_Tu_mid / shear_target,
                max_Tu_right / shear_target)
            max_Vu_left, max_Vu_mid, max_Vu_right = (
                max_Vu_left / shear_target, max_Vu_mid / shear_target,
                max_Vu_right / shear_target)

            # Initial effective-depth estimate for torsion steel distribution before flexure layers are known.
            d_eff_guess = b_height - c_cover - d_s - (d_m / 2.0)
            torsion_eng_left, torsion_eng_mid, torsion_eng_right = (
                BeamTorsionDesign(
                    b_width, b_height, d_eff_guess, fc_val, fy_val, fyt_val, d_s, c_cover,
                    code=torsion_code,
                )
                for _ in range(3)
            )

            t_res_left = torsion_eng_left.solve_torsion_capacity(
                max_Tu_left, max_Vu_left
            )
            t_res_mid = torsion_eng_mid.solve_torsion_capacity(max_Tu_mid, max_Vu_mid)
            t_res_right = torsion_eng_right.solve_torsion_capacity(
                max_Tu_right, max_Vu_right
            )

            side_dist_left = distribute_longitudinal_torsion_and_skin(
                t_res_left["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
                code,
            )
            side_dist_mid = distribute_longitudinal_torsion_and_skin(
                t_res_mid["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
                code,
            )
            side_dist_right = distribute_longitudinal_torsion_and_skin(
                t_res_right["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
                code,
            )

            flex_eng_left.design_beam(
                side_dist_left["Al_top_req"], side_dist_left["Al_bot_req"]
            )
            flex_eng_mid.design_beam(
                side_dist_mid["Al_top_req"], side_dist_mid["Al_bot_req"]
            )
            flex_eng_right.design_beam(
                side_dist_right["Al_top_req"], side_dist_right["Al_bot_req"]
            )

            is_cantilever = "Cantilever" in str(b_row.get("SupportStatus", ""))
            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top, flex_eng_mid.n_top, flex_eng_right.n_top
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot, flex_eng_mid.n_bot, flex_eng_right.n_bot
                )
                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

                # --- ADD CLEANUP HERE ---
                flex_eng_left.clean_single_bars()
                flex_eng_mid.clean_single_bars()
                flex_eng_right.clean_single_bars()

            flex_engines = {
                "Left Support Face": flex_eng_left,
                "Midspan Zone": flex_eng_mid,
                "Right Support Face": flex_eng_right,
            }
            side_distributions = {
                "Left Support (d_eff)": side_dist_left,
                "Interior Web (Gov. 2h)": side_dist_mid,
                "Right Support (d_eff)": side_dist_right,
            }

            # --- STEP 3B: Seismic & Shear Design ---
            if seismic_here:
                report(unique_name, story, gravity_combo_name or "-",
                       "SMRF moment strength ratios and probable-moment shear")
            seismic_checker = BeamSeismicDesign(
                flex_engines, span_ln, Pu_axial_load, seismic_here, code
            )

            # ACI 18.6.3: strength ratios along the span and the 2.5 % steel limit.
            # A cantilever is not part of the moment frame, so it is left out.
            if seismic_here and not is_cantilever:
                ratios_met = seismic_checker.enforce_flexural_capacity_ratios()
                for eng in flex_engines.values():
                    eng.clean_single_bars()
                smrf_flexure_check = "PASS" if ratios_met else "FAIL"
                smrf_rho_check = (
                    "PASS"
                    if seismic_checker.check_reinforcement_ratio_limits()["all_passed"]
                    else "FAIL"
                )

            if is_cantilever and seismic_here:
                # "Free at PtI" means the support is at the J end (right engine).
                supported_eng = (
                    flex_eng_right
                    if "Free at PtI" in str(b_row.get("SupportStatus", ""))
                    else flex_eng_left
                )
                Mpr_sup = seismic_checker.compute_probable_moment(
                    eng=supported_eng, is_negative_moment=True
                )
                V_sway_max = Mpr_sup / (span_ln / 1000.0) if span_ln > 0 else 0.0
                seismic_res = {
                    "V_sway_max": V_sway_max,
                    "Vu_seismic_left": Vu_grav_left + V_sway_max,
                    "Vu_seismic_right": Vu_grav_right + V_sway_max,
                    "Vc_zero_left": True,
                    "Vc_zero_right": True,
                }
            elif is_cantilever:
                # Without seismic design, retain gravity demand and concrete shear strength.
                seismic_res = {
                    "V_sway_max": 0.0,
                    "Vu_seismic_left": abs(Vu_grav_left),
                    "Vu_seismic_right": abs(Vu_grav_right),
                    "Vc_zero_left": False,
                    "Vc_zero_right": False,
                }
            else:
                seismic_res = seismic_checker.evaluate_seismic_shear_demands(
                    Vu_grav_left,
                    Vu_grav_right,
                    side_distributions,
                    Vu_envelope_max_left=real_Vu_left,
                    Vu_envelope_max_right=real_Vu_right,
                )

            min_legs = detailing.min_stirrup_legs
            gov_legs_left = gov_legs_mid = gov_legs_right = min_legs
            min_s_left = min_s_mid = min_s_right = detailing.max_spacing_default
            section_failures: list[str] = []

            for combo_name in combo_names:
                df_c_top = top_of[combo_name]

                Vu_L = (
                    max(df_c_top["Vu_left"], seismic_res["Vu_seismic_left"])
                    if seismic_here
                    else df_c_top["Vu_left"]
                )
                # ACI 18.6.5.1: the probable-moment shear acts along the whole span,
                # so beyond 2h it is the sway shear plus the gravity shear there.
                Vu_M = (
                    max(df_c_top["Vu_mid_2h"],
                        seismic_res["V_sway_max"] + abs(Vu_grav_mid))
                    if seismic_here and not is_cantilever
                    else df_c_top["Vu_mid_2h"]
                )
                Vu_R = (
                    max(df_c_top["Vu_right"], seismic_res["Vu_seismic_right"])
                    if seismic_here
                    else df_c_top["Vu_right"]
                )

                for zone_name, Vu_val, Tu_val, leg_var in [
                    ("Left Support (d_eff)", Vu_L, df_c_top["Tu_left"], "L"),
                    ("Interior Web (Gov. 2h)", Vu_M, df_c_top["Tu_mid_2h"], "M"),
                    ("Right Support (d_eff)", Vu_R, df_c_top["Tu_right"], "R"),
                ]:
                    Vu_val, Tu_val = Vu_val / shear_target, Tu_val / shear_target
                    report(unique_name, story, combo_name,
                           f"Shear and torsion, {zone_name.split(' (')[0].lower()}")
                    # Use the shallower face-specific effective depth as the conservative transverse-design depth.
                    d_eff_z = get_location_effective_depth(zone_name, flex_engines)
                    n_legs = min_legs
                    while True:
                        shear = BeamShearDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fyt_val,
                            d_s,
                            n_legs,
                            code=code,
                            suppress_Vc=(
                                seismic_res["Vc_zero_left"]
                                if "Left" in zone_name
                                else (
                                    seismic_res["Vc_zero_right"]
                                    if "Right" in zone_name
                                    else False
                                )
                            ),
                        )
                        torsion = BeamTorsionDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fy_val,
                            fyt_val,
                            d_s,
                            c_cover,
                            code=torsion_code,
                        )

                        s_r = shear.solve_shear_capacity(Vu_val)
                        t_r = torsion.solve_torsion_capacity(Tu_val, Vu_val)
                        if s_r["shear_failed"] or t_r["torsion_failed"]:
                            failure = (
                                "SHEAR STRENGTH: Vu > phi(Vc + 0.66 sqrt(fc') bw d), "
                                "NSCP 422.5.1.2 (ACI 22.5.1.2)" if s_r["shear_failed"]
                                else "SHEAR STRENGTH: shear + torsion stress above "
                                "NSCP 422.7.7.1 (ACI 22.7.7.1)"
                            )
                            if failure not in section_failures:
                                section_failures.append(failure)

                        # Required transverse steel ratio must satisfy shear-plus-torsion and torsion minimums.
                        des_ratio = max(
                            s_r["Av_s_demand"] + 2 * t_r["At_s_demand"],
                            t_r["Av_2At_s_min"],
                        )
                        s_max = min(s_r["s_max_code"], t_r["s_max_torsion"])

                        # SEISMIC SPACING OVERRIDE
                        if seismic_here and leg_var in ["L", "R"]:
                            s_max_seismic = min(
                                d_eff_z * seismic_cfg.hoop_spacing_d_fraction,
                                seismic_cfg.hoop_spacing_bar_multiple * d_m,
                                seismic_cfg.hoop_spacing_abs,
                            )
                            s_max = min(s_max, s_max_seismic)

                        Av_prov = n_legs * ((math.pi / 4) * d_s**2)
                        s_raw = (
                            min(Av_prov / des_ratio, s_max) if des_ratio > 0 else s_max
                        )
                        # Round spacing down to the nearest step (25 mm) so rounding cannot exceed the limit.
                        step = detailing.stirrup_spacing_step
                        s_rec = int((s_raw // step) * step)

                        if (
                            s_rec < detailing.min_practical_spacing
                            and n_legs < flex_eng_mid.max_bar_per_layer
                        ):
                            n_legs += 1
                        else:
                            break

                    if leg_var == "L":
                        gov_legs_left = max(gov_legs_left, n_legs)
                        min_s_left = min(min_s_left, s_rec)
                    elif leg_var == "M":
                        gov_legs_mid = max(gov_legs_mid, n_legs)
                        min_s_mid = min(min_s_mid, s_rec)
                    elif leg_var == "R":
                        gov_legs_right = max(gov_legs_right, n_legs)
                        min_s_right = min(min_s_right, s_rec)

            # One common stirrup-leg arrangement must satisfy the most demanding beam zone.
            governing_beam_legs = max(gov_legs_left, gov_legs_mid, gov_legs_right)
            # Support-zone spacing is governed by the tighter of the two beam ends.
            s_2h = min(min_s_left, min_s_right)
            s_mid = min_s_mid

            # --- STEP 3C: Post-Checks ---
            for eng in flex_engines.values():
                alt_legs = check_alternating_tie_legs(
                    eng.n_top, eng.n_bot, governing_beam_legs, eng.max_bar_per_layer
                )
                governing_beam_legs = max(governing_beam_legs, alt_legs)

            anchorage_passed_all = True
            for loc_name, eng in flex_engines.items():
                try:
                    anch_check = check_stirrup_leg_anchorage(
                        eng.n_top,
                        eng.n_bot,
                        governing_beam_legs,
                        eng.max_bar_per_layer,
                        d_m,
                        d_s,
                        b_width,
                        c_cover,
                    )
                except ValueError:
                    # The width cannot hold a bar for every stirrup leg: this beam
                    # fails, and the other beams are still designed.
                    failure = (f"WIDTH FITS {eng.max_bar_per_layer} BAR(S) PER LAYER, "
                               f"{governing_beam_legs} STIRRUP LEGS NEED {governing_beam_legs} "
                               "(WIDEN THE BEAM, OR REDUCE THE BAR SIZE OR COVER)")
                    if failure not in section_failures:
                        section_failures.append(failure)
                    continue
                if not anch_check["anchorage_passed"]:
                    eng.n_top = anch_check["final_n_top"]
                    eng.n_bot = anch_check["final_n_bot"]

                    # --- ADD CLEANUP HERE ---
                    eng.clean_single_bars()

                    anchorage_passed_all = False

            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top, flex_eng_mid.n_top, flex_eng_right.n_top
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot, flex_eng_mid.n_bot, flex_eng_right.n_bot
                )
                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

                # --- ADD CLEANUP HERE ---
                flex_eng_left.clean_single_bars()
                flex_eng_mid.clean_single_bars()
                flex_eng_right.clean_single_bars()

            current_state = (
                [(eng.n_top, eng.n_bot) for eng in flex_engines.values()],
                governing_beam_legs,
            )
            if current_state == prev_state:
                break
            else:
                prev_state = current_state

        # =============================================================================
        # 4. CONSOLIDATE RESULTS (TOP & BOTTOM)
        # =============================================================================
        for is_top_face in [True, False]:
            face_str = "TOP" if is_top_face else "BOTTOM"
            summary = b_row.to_dict()
            summary["Combo"] = "ENVELOPE (ALL COMBOS)"
            summary["Face"] = face_str
            summary["Mu_left"] = face_max[face_str]["Mu_left"]
            summary["Mu_mid"] = face_max[face_str]["Mu_mid"]
            summary["Mu_right"] = face_max[face_str]["Mu_right"]

            # Shear/Torsion envelope is identical for top and bottom rows
            summary["Vu_left"] = all_max["Vu_left"]
            summary["Vu_mid_2h"] = all_max["Vu_mid_2h"]
            summary["Vu_right"] = all_max["Vu_right"]
            summary["Tu_left"] = all_max["Tu_left"]
            summary["Tu_mid_2h"] = all_max["Tu_mid_2h"]
            summary["Tu_right"] = all_max["Tu_right"]

            summary["n_left_L1"], summary["n_left_L2"], summary["n_left_L3"] = (
                get_layer_columns(flex_eng_left, is_top=is_top_face)
            )
            summary["n_mid_L1"], summary["n_mid_L2"], summary["n_mid_L3"] = (
                get_layer_columns(flex_eng_mid, is_top=is_top_face)
            )
            summary["n_right_L1"], summary["n_right_L2"], summary["n_right_L3"] = (
                get_layer_columns(flex_eng_right, is_top=is_top_face)
            )

            # Web Reinforcement
            summary["n_side_per_face_gov"] = max(
                side_dist_left["n_side_per_face"],
                side_dist_mid["n_side_per_face"],
                side_dist_right["n_side_per_face"],
            )

            # Stirrups & Spacing
            summary["Stirrup_Legs"] = governing_beam_legs
            summary["Spacing_2H"] = s_2h
            summary["Spacing_Mid"] = s_mid

            # Post-Checks
            summary["V_sway_max_kN"] = seismic_res["V_sway_max"]
            # Design shear at each end: gravity shear plus the sway shear above.
            summary["Ve_left_kN"] = seismic_res["Vu_seismic_left"]
            summary["Ve_right_kN"] = seismic_res["Vu_seismic_right"]
            summary["Vc_zero_left"] = seismic_res["Vc_zero_left"]
            summary["Vc_zero_right"] = seismic_res["Vc_zero_right"]
            summary["Anchorage_Check"] = (
                "PASSED" if anchorage_passed_all else "ADJUSTED"
            )
            summary["Alternating_Tie_Check"] = "PASSED"
            if compatibility_torsion:   # shown only when the option is on
                summary["Torsion_Basis"] = torsion_basis
            if exempt_short_spans and enable_seismic_design:   # shown only when the option is on
                summary["Seismic_Rules"] = seismic_rules
            if dcr_targets.active().changed:   # shown only when the engineer set targets
                summary["Target_DCR_Flexure"] = flexure_target
                summary["Target_DCR_Shear"] = shear_target
                summary["Target_DCR_Deflection"] = dcr_targets.limit(
                    member_type, dcr_targets.DEFLECTION)
            summary["SMRF_Flexure_Ratio_Check"] = smrf_flexure_check
            summary["SMRF_Rho_Check"] = smrf_rho_check

            # --- NEW: Catch Congestion Warnings ---
            if (
                flex_eng_left.rebar_congestion_exceeded
                or flex_eng_mid.rebar_congestion_exceeded
                or flex_eng_right.rebar_congestion_exceeded
            ):
                summary["Design_Status"] = "FAILED: MAX BARS EXCEEDED (>3 LAYERS)"
            elif smrf_flexure_check == "FAIL":
                summary["Design_Status"] = (
                    "FAILED: SMRF MOMENT STRENGTH RATIOS (NSCP 418.6.3.2; ACI 18.6.3.2)"
                )
            elif smrf_rho_check == "FAIL":
                summary["Design_Status"] = (
                    "FAILED: SMRF STEEL RATIO ABOVE 2.5% (NSCP 418.6.3.1; ACI 18.6.3.1)"
                )
            elif section_failures:  # the section, not the stirrups, is too small
                summary["Design_Status"] = "FAILED: " + "; ".join(section_failures)
            elif (
                s_2h < detailing.min_acceptable_spacing
                or s_mid < detailing.min_acceptable_spacing
            ):  # Flag if shear spacing is unrealistically tight
                summary["Design_Status"] = (
                    f"FAILED: SHEAR SPACING < {detailing.min_acceptable_spacing:g}mm"
                )
            else:
                summary["Design_Status"] = "OK"

            results_list.append(summary)

    df_beam_design_results = pd.DataFrame(results_list).round(2)
    return df_beam_design_results


BEAM_BAR_INPUTS = ("dm", "ds", "dw", "fyw", "cc")  # main, stirrup, web bar, web fy, cover


def clear_span_of(station_span: float, joint_length, support_half: float) -> float:
    """The clear span of a beam between the faces of its supports, mm.

    It is the joint-to-joint length less half of each supporting column. ETABS
    gives the forces of a member between its end length offsets, so on a model
    with those offsets (the usual one) the stations already run from face to
    face: taking half of each column off them again made the span too short by
    that much, the seismic shear Ve = (Mpr1 + Mpr2) / ln too large, and a girder
    could count as shorter than 4d when it was not. The span is never longer
    than the stations of the member.

    ``station_span``: the last station less the first. ``joint_length``: the
    length between the joints, when it is known. ``support_half``: half of the
    column at each end, added up.
    """
    try:
        joint = float(joint_length)
    except (TypeError, ValueError):
        joint = float("nan")
    if joint == joint and joint > 0:
        clear = joint - support_half
        return min(clear, station_span) if clear > 0 else station_span
    # the joint length is not known: the stations are the joint-to-joint length
    return station_span - support_half if station_span > support_half else station_span


def _support_widths(frame_df: pd.DataFrame, conn_df: pd.DataFrame) -> pd.DataFrame:
    """The largest side of the column at each end of every beam (0 when none), mm.

    The beam direction is not used, so the larger column side is taken: the
    clear span, and with it the probable-moment shear, errs on the safe side.
    """
    frame = frame_df.copy()
    frame.columns = [str(c).strip() for c in frame.columns]
    conn = conn_df.copy()
    conn.columns = [str(c).strip() for c in conn.columns]
    if not {"UniqueName", "DesignType", "UniquePtI", "UniquePtJ"} <= set(conn.columns):
        return pd.DataFrame(columns=["UniqueName", "SupportWidth_I", "SupportWidth_J",
                                     "JointLength"])
    sizes = {}
    for row in frame.to_dict("records"):
        values = [pd.to_numeric(row.get(key), errors="coerce")
                  for key in ("Width", "Depth", "Diameter")]
        values = [float(v) for v in values if pd.notna(v) and float(v) > 0]
        if values:
            sizes[str(row.get("UniqueName"))] = max(values)
    width_at: dict[str, float] = {}
    columns = conn[conn["DesignType"].astype(str).eq("Column")]
    for row in columns.to_dict("records"):
        side = sizes.get(str(row["UniqueName"]), 0.0)
        for point in (row["UniquePtI"], row["UniquePtJ"]):
            width_at[point] = max(width_at.get(point, 0.0), side)
    beams = conn[conn["DesignType"].astype(str).eq("Beam")]
    lengths = pd.to_numeric(beams["Length"], errors="coerce").to_numpy() \
        if "Length" in beams.columns else [float("nan")] * len(beams)
    return pd.DataFrame({
        "UniqueName": beams["UniqueName"].to_numpy(),
        "SupportWidth_I": [width_at.get(p, 0.0) for p in beams["UniquePtI"]],
        "SupportWidth_J": [width_at.get(p, 0.0) for p in beams["UniquePtJ"]],
        "JointLength": lengths,      # joint to joint, for the clear span
    })


def prepare_beam_table(frame_df: pd.DataFrame, conn_df: pd.DataFrame,
                       bars: dict, earth_cover_stories=(),
                       points: pd.DataFrame | None = None,
                       pushes: dict | None = None) -> pd.DataFrame:
    """The beams of FRAME DATA with their support status and the bar inputs.

    ``bars`` has dm (main bar), ds (stirrup), dw (web bar) in mm, fyw (web bar
    yield strength, MPa) and cc (cover, mm). The beams of the stories in
    ``earth_cover_stories`` (cast against or exposed to earth: footing tie
    beams, ground beams) get the earth-contact cover instead, 75 mm. One row
    per beam, sorted by floor, type and mark.
    """
    frame_df = frame_df.copy()
    conn_df = conn_df.copy()
    frame_df.columns = [str(c).strip() for c in frame_df.columns]
    conn_df.columns = [str(c).strip() for c in conn_df.columns]
    support_df = identify_cantilever_beams(frame_df, conn_df, points, pushes)
    support_df = support_df.merge(_support_widths(frame_df, conn_df), on="UniqueName",
                                  how="left")
    beam_df = frame_df.merge(support_df, on="UniqueName", how="left")
    if "DesignType" in beam_df.columns:
        beam_df = beam_df[beam_df["DesignType"] == "Beam"].copy()
    beam_df = beam_df.drop_duplicates(subset=["UniqueName"], keep="first")
    cols = [c for c in beam_df.columns if c != "SupportStatus"]
    if "SectProp" in cols:
        cols.insert(cols.index("SectProp") + 1, "SupportStatus")
    beam_df = beam_df[cols]
    for key in BEAM_BAR_INPUTS:
        beam_df[key] = float(bars[key])
    stories = {str(story) for story in earth_cover_stories or ()}
    if stories and "Story" in beam_df.columns:
        on_earth = beam_df["Story"].astype(str).isin(stories)
        beam_df.loc[on_earth, "cc"] = max(float(bars["cc"]),
                                          CODE.beam_detailing.earth_contact_cover)
    beam_df["_sort_key"] = beam_df["UniqueName"].apply(_beam_sort_key)
    return beam_df.sort_values("_sort_key").drop(columns="_sort_key").reset_index(drop=True)


def load_path_pushes(tables: dict, gravity_combo: str | None = None) -> dict:
    """What the analysis says holds each beam end, for the support status and
    the carrier rule: the shear and moment diagrams and, with the service
    loads, the deflected shape (``carriers.LoadPath``).

    Read under the full service load (DEF 101) when the model has the
    deflection combinations, otherwise under the factored gravity
    combination of the seismic shear. Empty when neither is there: the
    geometry of the framing then decides.
    """
    from design.concrete.beams.carriers import read_load_path
    from design.concrete.beams.deflection import COMBO_FULL

    connectivity = tables.get("CONNECTIVITY")
    service = tables.get("SERVICE LOADS")
    if service is not None and len(service) and "V2" in service.columns:
        path = read_load_path(service, COMBO_FULL, connectivity)
        if path:
            return path
    return read_load_path(tables.get("FACTORED LOADS"), gravity_combo, connectivity)


def design_beams(tables: dict, smrf: bool, gravity_combo: str | None, bars: dict,
                 long_limit_divisor: int = LIMIT_DAMAGED, progress=None,
                 earth_cover_stories=(), carrier_depth: bool = False,
                 compatibility_torsion: bool = False,
                 office_bar_spacing: bool = False,
                 exempt_short_spans: bool = False,
                 deflection_stages: DeflectionStages | None = None,
                 load_path: dict | None = None) -> pd.DataFrame:
    """Design every beam from the extracted tables; deflection when service loads exist.

    ``tables`` holds FACTORED LOADS, FRAME DATA, CONNECTIVITY and (optional)
    SERVICE LOADS. ``earth_cover_stories`` are the levels whose beams get the
    75 mm earth-contact cover. ``carrier_depth`` also requires a beam to be at
    least as deep as the beams it carries. ``compatibility_torsion`` limits
    the design torsion to phi Tcr (NSCP 422.7.3.2; ACI 22.7.3.2). ``office_bar_spacing`` False
    drops the office rule of 150 mm clear between bars: the bar count for
    spacing then comes from crack control (NSCP 424.3.2; ACI 24.3.2). ``exempt_short_spans``
    designs the girders with a clear span under 4d without the SMRF rules
    (NSCP 418.6.2.1(a); ACI 18.6.2.1(a)). ``deflection_stages`` (when the partitions are built)
    deducts the long-term deflection before them. ``load_path`` is what holds
    each beam end (``load_path_pushes``); it is read from the tables when not
    given, and the design loop gives the reading of its first analysis so
    that it does not change as the sizes do. The target ratios are those that
    are active (``dcr_targets.use``). Returns the results, two rows (TOP,
    BOTTOM) per beam.
    """
    from design.concrete.beams.carriers import forget_networks

    forget_networks()   # the tables of this design are new: nothing kept from another one
    pushes = load_path_pushes(tables, gravity_combo) if load_path is None else load_path
    beam_props = prepare_beam_table(tables["FRAME DATA"], tables["CONNECTIVITY"], bars,
                                    earth_cover_stories, tables.get("POINTS"), pushes)
    results = execute_beam_design(
        df_beam_props=beam_props,
        df_frame_forces=tables["FACTORED LOADS"],
        enable_seismic_design=smrf,
        gravity_combo_name=gravity_combo,
        progress=progress,
        compatibility_torsion=compatibility_torsion,
        exempt_short_spans=exempt_short_spans,
        code=CODE if office_bar_spacing else override(
            CODE, beam_detailing__limit_clear_spacing=False),
    )
    service = tables.get("SERVICE LOADS")
    if service is not None and len(service) and not results.empty:
        results = add_deflection_columns(results, service, long_limit_divisor,
                                         tables["CONNECTIVITY"], progress=progress,
                                         stages=deflection_stages, points=tables.get("POINTS"),
                                         pushes=pushes)
    if carrier_depth and not results.empty:
        from design.concrete.beams.carriers import add_carrier_depth_check

        results = add_carrier_depth_check(results, tables["CONNECTIVITY"], tables.get("POINTS"),
                                          pushes)
    return sort_beam_rows(results)
