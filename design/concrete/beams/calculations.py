"""The beam calculation report (PDF)."""

import math
import pandas as pd
from design.criteria import dcr_targets
from design.criteria.code_config import CODE, AciCode
from utilities._calc_report import (
    is_blank,
    MemberReport,
    ReportTable,
    Tex,
    build_calc_report,
    number,
)
from design.concrete.beams.supports import (
    SMRF_SHORT_SPAN,
    smrf_applies,
)
from design.concrete.beams.sections import (
    BeamFlexureDesign,
    BeamSeismicDesign,
    BeamShearDesign,
    BeamTorsionDesign,
)
from design.concrete.beams.design import (
    COMPATIBILITY_TORSION,
    _torsion_code,
)


# =============================================================================
# CALCULATION REPORT (PDF)
# =============================================================================
_BEAM_LOCATIONS = (("left", "Left support"), ("mid", "Midspan"), ("right", "Right support"))


def _beam_deflection_table(top: pd.Series) -> ReportTable | None:
    """The deflection checks of one beam (ACI 318M-14 24.2), from its result row.

    None when the results were made without the deflection check. A beam with
    no deflection data gets a table that says why.
    """
    if "Deflection_Check" not in top.index:
        return None
    check = str(top.get("Deflection_Check"))
    if is_blank(top.get("Defl_ratio")):
        return ReportTable(
            "Deflection (ACI 24.2)", ["Parameter", "Value"],
            [["Deflection check", check if check not in ("None", "nan", "") else
              "Not checked - no service moments for this beam"]], "lr")

    def pair(value, limit, missing: str) -> list:
        if is_blank(value):
            return [missing, "--", "--"]
        ratio = (float(value) / float(limit)) if not is_blank(limit) and float(limit) > 0 else None
        return [number(value), number(limit), number(ratio) if ratio is not None else "--"]

    body = [
        ["Immediate, live load (L/360)",
         *pair(top.get("Defl_live_mm"), top.get("Defl_live_limit_mm"), "N/A")],
        ["Immediate, roof live load (L/180)",
         *pair(top.get("Defl_roof_mm"), top.get("Defl_roof_limit_mm"), "N/A - not a roof beam")],
        ["After attachment of the partitions (long-term part + live load not sustained)",
         *pair(top.get("Defl_long_mm"), top.get("Defl_long_limit_mm"), "N/A")],
    ]

    def information(label: str, key: str) -> None:
        if key in top.index and not is_blank(top.get(key)):
            body.append([label, number(top.get(key)), "--", "--"])

    information("For information: long-term part alone (creep and shrinkage, sustained load)",
                "Defl_creep_mm")
    information("For information: total, immediate + long-term", "Defl_total_mm")
    information("Stage: when the dead load is first carried", "Defl_first_load_mm")
    information("Stage: just before the partitions are built", "Defl_at_partitions_mm")
    information("Long-term deflection before the partitions (deducted above)",
                "Defl_deducted_mm")
    stages_text = str(top.get("Defl_stages", "") or "")
    stages_note = (f" Stages: {stages_text}; the long-term deflection before the partitions "
                   "is deducted (ACI Table 24.2.2)." if stages_text not in ("", "None", "nan")
                   else " All the long-term deflection is counted after the partitions.")
    return ReportTable(
        "Deflection (ACI 24.2)",
        ["Check", Tex(r"$\Delta$ (mm)"), "Limit (mm)", Tex(r"$\Delta$ / limit")],
        body, "lrrr",
        "Effective moment of inertia Ie (mm4): left "
        f"{number(top.get('Defl_Ie_left'), 0)}, midspan {number(top.get('Defl_Ie_mid'), 0)}, "
        f"right {number(top.get('Defl_Ie_right'), 0)}. Long-term factor "
        f"{number(top.get('Defl_lambda'), 3)}.{stages_note} Governing ratio "
        f"{number(top.get('Defl_ratio'), 3)}. The segments of one beam line between supports "
        f"are checked as one span. Deflection check: {check}.",
    )


def _beam_calc_member(
    top: pd.Series, bottom: pd.Series, seismic: bool, code: AciCode = CODE
) -> MemberReport:
    """Build the report tables of one beam from its TOP and BOTTOM result rows.

    The bar counts, stirrup legs and spacings are the designed ones. Depths,
    strengths and ratios are worked out again from them with the design engines.
    """
    name = str(top["UniqueName"])
    width, height = float(top["Width"]), float(top["Depth"])
    fc, fy = float(top["f'c"]), float(top["fy"])
    fyt = float(top.get("fyw", fy))
    d_main, d_stirrup, cover = float(top["dm"]), float(top["ds"]), float(top["cc"])
    span = float(top["ClearSpan_Ln"])
    is_cantilever = "Cantilever" in str(top.get("SupportStatus", ""))
    short_span = str(top.get("Seismic_Rules", "")).startswith(SMRF_SHORT_SPAN)
    seismic = seismic and smrf_applies(top)

    def bars(row: pd.Series, location: str) -> list[int]:
        return [int(float(row.get(f"n_{location}_L{layer}") or 0)) for layer in (1, 2, 3)]

    engines = {}
    for location, _ in _BEAM_LOCATIONS:
        engine = BeamFlexureDesign(
            width, height, fc, fy, fyt, d_main, d_stirrup, cover, code=code
        )
        engine.n_top = sum(bars(top, location))
        engine.n_bot = sum(bars(bottom, location))
        engines[location] = engine

    section = ReportTable(
        "Section and materials",
        ["Parameter", "Value", "Parameter", "Value"],
        [
            ["Section", top.get("SectProp"), "Support condition", top.get("SupportStatus")],
            [Tex("Width, $b$ (mm)"), number(width, 0), Tex("Depth, $h$ (mm)"), number(height, 0)],
            [Tex("Clear span, $L_n$ (mm)"), number(span, 0), Tex("Cover (mm)"), number(cover, 0)],
            [Tex(r"$f'_c$ (MPa)"), number(fc), Tex("$f_y$ (MPa)"), number(fy)],
            [Tex("$f_{yt}$ (MPa)"), number(fyt), Tex("Main bar (mm)"), number(d_main, 0)],
            ["Stirrup bar (mm)", number(d_stirrup, 0), "Web bar (mm)", number(top.get("dw"), 0)],
        ],
        "llll",
    )

    member_type = dcr_targets.beam_type(top.get("SupportStatus", ""))
    flexure_target = dcr_targets.limit(member_type, dcr_targets.FLEXURE)
    shear_target = dcr_targets.limit(member_type, dcr_targets.SHEAR)
    target_note = dcr_targets.note((member_type,))
    flexure_rows = []
    flexure_passes = True
    for location, label in _BEAM_LOCATIONS:
        for face, row, negative in (("Top", top, True), ("Bottom", bottom, False)):
            layers = bars(row, location)
            # Layer 1 is the topmost; a bottom face fills from layer 3 upward.
            layout = "+".join(str(n) for n in (layers if negative else layers[::-1]) if n)
            demand = float(row.get(f"Mu_{location}") or 0.0)
            engine = engines[location]
            try:
                result = engine.solve_moment_capacity(is_negative_moment=negative)
                depth = engine.compute_effective_depths(negative)[0]
                ratio = demand / result["phi_Mn"] if result["phi_Mn"] > 0 else float("nan")
                passed = demand <= result["phi_Mn"] * flexure_target + 1e-6
                flexure_passes = flexure_passes and passed
                flexure_rows.append([
                    label, face, number(demand), layout or "0",
                    number(result["As_provided"], 0), number(depth, 1),
                    number(result["a"], 1), number(result["et"], 4),
                    number(result["phi"], 2), number(result["Mn"]),
                    number(result["phi_Mn"]), number(ratio), "PASS" if passed else "FAIL",
                ])
            except (ValueError, ZeroDivisionError):
                flexure_rows.append(
                    [label, face, number(demand), layout or "0"] + ["--"] * 8
                    + ["PASS" if demand <= 1e-6 else "FAIL"]
                )
    flexure = ReportTable(
        "Flexure",
        ["Location", "Face", Tex("$M_u$"), "Bars", Tex("$A_s$"), Tex("$d$"), Tex("$a$"),
         Tex(r"$\varepsilon_t$"), Tex(r"$\phi$"), Tex("$M_n$"), Tex(r"$\phi M_n$"),
         Tex(r"$M_u/\phi M_n$"), "Check"],
        flexure_rows,
        "ll" + "r" * 10 + "l",
        "Moments in kN-m, areas in mm2, depths in mm. Bars are listed per layer, from the "
        "face inward." + (" " + target_note if target_note else ""),
    )

    legs = int(float(top.get("Stirrup_Legs") or 0))
    stirrup_area = legs * math.pi * d_stirrup**2 / 4.0
    fyt_shear = min(fyt, code.material.max_fyt_shear)
    phi_shear = code.strength.shear
    shear_rows = []
    for location, label, v_key, t_key, s_key in (
        ("left", "Left support", "Vu_left", "Tu_left", "Spacing_2H"),
        ("mid", "Beyond 2h", "Vu_mid_2h", "Tu_mid_2h", "Spacing_Mid"),
        ("right", "Right support", "Vu_right", "Tu_right", "Spacing_2H"),
    ):
        engine = engines[location]
        depth = min(
            engine.compute_effective_depths(True)[0],
            engine.compute_effective_depths(False)[0],
        )
        analysis_shear = float(top.get(v_key) or 0.0)
        capacity_shear = (
            float(top.get(f"Ve_{location}_kN") or 0.0)
            if seismic and location != "mid"
            else float("nan")
        )
        design_shear = (
            analysis_shear if math.isnan(capacity_shear)
            else max(analysis_shear, capacity_shear)
        )
        torsion_demand = float(top.get(t_key) or 0.0)
        suppressed = location != "mid" and top.get(f"Vc_zero_{location}") in (True, "True", 1)
        spacing = float(top.get(s_key) or 0.0)
        shear = BeamShearDesign(
            width, height, depth, fc, fyt, d_stirrup, legs, suppress_Vc=suppressed, code=code
        )
        shear_result = shear.solve_shear_capacity(design_shear)
        torsion_result = BeamTorsionDesign(
            width, height, depth, fc, fy, fyt, d_stirrup, cover,
            code=_torsion_code(str(top.get("Torsion_Basis", "")), code)
        ).solve_torsion_capacity(torsion_demand, design_shear)
        steel_shear = (
            stirrup_area * fyt_shear * depth / spacing / 1000.0 if spacing > 0 else 0.0
        )
        steel_shear_limit = (
            shear_result["max_allowable_phi_Vn"] / phi_shear - shear_result["Vc"]
        )
        capacity = phi_shear * (shear_result["Vc"] + min(steel_shear, steel_shear_limit))
        ratio = design_shear / capacity if capacity > 0 else float("nan")
        shear_rows.append([
            label, number(analysis_shear), number(capacity_shear), number(design_shear),
            number(torsion_demand), number(depth, 1), number(shear_result["Vc"]),
            legs, number(spacing, 0), number(steel_shear), number(capacity), number(ratio),
            number(torsion_result["At_s_demand"], 3), number(torsion_result["Al_design"], 0),
            "PASS" if design_shear <= capacity * shear_target + 1e-6 else "FAIL",
        ])
    shear = ReportTable(
        "Shear and torsion",
        ["Zone", Tex("$V_u$"), Tex("$V_e$"), Tex("Design $V_u$"), Tex("$T_u$"), Tex("$d$"),
         Tex("$V_c$"), "Legs", Tex("$s$"), Tex("$V_s$"), Tex(r"$\phi V_n$"),
         Tex(r"$V_u/\phi V_n$"), Tex("$A_t/s$"), Tex("$A_l$"), "Check"],
        shear_rows,
        "l" + "r" * 13 + "l",
        "Forces in kN, torsion in kN-m, lengths in mm, At/s in mm2/mm, Al in mm2. "
        "Vu and Tu are the envelopes of all combinations. Vc is taken as zero where the "
        "seismic rule requires it. The stirrups also carry the torsion steel At/s."
        + (" Torsion: " + str(top.get("Torsion_Basis")) + "."
           if str(top.get("Torsion_Basis", "")).startswith(COMPATIBILITY_TORSION[:13]) else "")
        + (" Seismic (SMRF) rules: " + str(top.get("Seismic_Rules")) + "." if short_span else "")
        + (" " + target_note if target_note else ""),
    )

    tables = [section, flexure, shear]
    if seismic and not is_cantilever:
        try:
            checker = BeamSeismicDesign(
                {"Left Support Face": engines["left"], "Midspan Zone": engines["mid"],
                 "Right Support Face": engines["right"]},
                span, code=code,
            )
            strengths = checker.check_flexural_capacity_ratios()
            seismic_rows = [
                [location, number(values["Mn_neg"]), number(values["Mn_pos"]),
                 number(values["Mn_pos"] / values["Mn_neg"]) if values["Mn_neg"] else "--"]
                for location, values in strengths["locations"].items()
            ]
            tables.append(ReportTable(
                "Seismic moment strengths (SMRF)",
                ["Location", Tex("$M_n^-$ (kN-m)"), Tex("$M_n^+$ (kN-m)"), Tex("$M_n^+/M_n^-$")],
                seismic_rows,
                "lrrr",
            ))
        except (ValueError, KeyError, ZeroDivisionError):
            pass
    if seismic:
        tables.append(ReportTable(
            "Seismic design shear (SMRF)",
            ["Parameter", "Value"],
            [
                [Tex("Sway shear from probable moments, $V_{sway}$ (kN)"), number(top.get("V_sway_max_kN"))],
                [Tex("Design shear, left, $V_e$ (kN)"), number(top.get("Ve_left_kN"))],
                [Tex("Design shear, right, $V_e$ (kN)"), number(top.get("Ve_right_kN"))],
                ["Concrete shear taken as zero, left", top.get("Vc_zero_left") in (True, "True", 1)],
                ["Concrete shear taken as zero, right", top.get("Vc_zero_right") in (True, "True", 1)],
                ["Moment strength ratio check (ACI 18.6.3.2)", top.get("SMRF_Flexure_Ratio_Check")],
                ["Steel ratio check (ACI 18.6.3.1)", top.get("SMRF_Rho_Check")],
            ],
            "lr",
        ))

    deflection = _beam_deflection_table(top)
    if deflection is not None:
        tables.append(deflection)

    status = str(top.get("Design_Status"))
    tables.append(ReportTable(
        "Detailing and status",
        ["Parameter", "Value"],
        [
            ["Stirrup legs", legs],
            ["Stirrup spacing within 2h of supports (mm)", number(top.get("Spacing_2H"), 0)],
            ["Stirrup spacing elsewhere (mm)", number(top.get("Spacing_Mid"), 0)],
            ["Side bars per face", number(top.get("n_side_per_face_gov"), 0)],
            ["Stirrup anchorage check", top.get("Anchorage_Check")],
            ["Alternating tie check", top.get("Alternating_Tie_Check")],
            ["Design status", status],
        ],
        "lr",
    ))

    return MemberReport(
        heading=f"Beam {name} ({top.get('Story')})",
        summary=[name, top.get("Story"), top.get("SectProp"),
                 "PASS" if flexure_passes else "FAIL", status],
        tables=tables,
    )


def build_beam_calc_report(
    df_results: pd.DataFrame, filepath: str, seismic: bool, gravity_combo: object = None
) -> str | None:
    """Write the beam calculation PDF from the BEAM DESIGN result table."""
    members = []
    for _, rows in df_results.groupby("UniqueName", sort=False):
        top = rows[rows["Face"] == "TOP"]
        bottom = rows[rows["Face"] == "BOTTOM"]
        if top.empty or bottom.empty:
            continue
        members.append(_beam_calc_member(top.iloc[0], bottom.iloc[0], seismic))
    if not members:
        raise ValueError("BEAM DESIGN contains no designed beams to report.")
    information = [
        ("Design code", "ACI 318M-14"),
        ("Seismic design (SMRF)", "Yes" if seismic else "No"),
        ("Factored gravity combination", gravity_combo if seismic else "Not used"),
        ("Design forces", "Envelope of all extracted load combinations"),
        ("Beams reported", len(members)),
    ]
    return build_calc_report(
        "Concrete Beam Design Calculations (ACI 318M-14)",
        information,
        ["Beam", "Story", "Section", "Flexure", "Design status"],
        members,
        filepath,
        "p{3.0cm}p{1.3cm}p{5.0cm}p{1.6cm}p{5.2cm}",
        command="sdt beams",
    )


def export_beam_pdf(results: pd.DataFrame, path: str, seismic: bool,
                    gravity_combo: str | None) -> str | None:
    """The beam calculation report (PDF); None when LaTeX fails."""
    return build_beam_calc_report(results, path, seismic=seismic, gravity_combo=gravity_combo)
