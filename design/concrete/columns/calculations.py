"""The column calculation report (PDF)."""

import os
import re
import numpy as np
import pandas as pd
from design.criteria import dcr_targets
from utilities._calc_report import (
    MemberReport,
    ReportTable,
    Tex,
    build_calc_report,
    is_blank,
    number,
)
from design.concrete.columns.layouts import (
    _build_column_section,
)
from design.concrete.columns.transverse import (
    _decode_bar_layout,
)


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
            "interaction surface (figure below)."
            + (" " + dcr_targets.note((dcr_targets.COLUMN,))
               if dcr_targets.note((dcr_targets.COLUMN,)) else ""),
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

    slenderness = _column_slenderness_table(rows)
    if slenderness is not None:
        tables.insert(2, slenderness)

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


def _numbers_of(rows: pd.DataFrame, column: str) -> pd.Series:
    if column not in rows.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(rows[column], errors="coerce").dropna()


def _column_slenderness_table(rows: pd.DataFrame) -> ReportTable | None:
    """The slenderness of one column about each axis: the largest magnifier of
    all its combinations, with the values behind it."""
    if "Slenderness_Check" not in rows.columns:
        return None
    body = []
    for axis, label in (("3", "About 3 (over the depth)"), ("2", "About 2 (over the width)")):
        ratio = _numbers_of(rows, f"Slender_ratio{axis}")
        if ratio.empty:
            continue
        delta = _numbers_of(rows, f"Slender_delta{axis}")
        limit = _numbers_of(rows, f"Slender_limit{axis}")
        worst = rows.loc[delta.idxmax()] if not delta.empty else rows.iloc[0]
        unstable = rows[f"Slender_delta{axis}"].astype(str).eq("Unstable").any()
        slender = (ratio.max() > limit.min() + 1e-9) if not limit.empty else False
        body.append([
            label,
            number(worst.get(f"Slender_lu{axis}_mm"), 0),
            number(worst.get(f"Slender_k{axis}"), 2),
            number(ratio.max(), 1),
            number(limit.min(), 1) if not limit.empty else "--",
            "Yes" if slender else "No",
            number(worst.get(f"Slender_Cm{axis}"), 2) if slender else "--",
            number(worst.get(f"Slender_Pc{axis}_kN"), 0) if slender else "--",
            "Unstable" if unstable else number(delta.max() if not delta.empty else 1.0, 2),
        ])
    if not body:
        return None
    checks = rows["Slenderness_Check"].astype(str)
    failing = checks[checks.str.startswith("FAIL")]
    status = failing.iloc[0] if len(failing) else (
        "PASS - slender, moments magnified" if checks.str.contains("magnified").any()
        else "PASS - not slender")
    beta = _numbers_of(rows, "Slender_beta_dns")
    return ReportTable(
        "Slenderness (ACI 6.2.5, 6.6.4.5)",
        ["Axis", Tex("$l_u$ (mm)"), Tex("$k$"), Tex("$k l_u/r$"), "Limit", "Slender",
         Tex("$C_m$"), Tex("$P_c$ (kN)"), Tex(r"$\delta_{ns}$")],
        body, "lrrrrlrrr",
        "Sway (P-Delta) effects are in the forces of the ETABS P-delta analysis (ACI 6.7). "
        "Member (P-delta) effects are added here by moment magnification: Mu2 and Mu3 in the "
        "table above are the magnified design moments. Limit = 34 + 12 M1/M2, at most 40; "
        "k from the alignment chart of a braced column, at most 1.0; (EI)eff = 0.4 Ec Ig / "
        f"(1 + beta_dns), beta_dns up to {number(beta.max() if not beta.empty else 0.6, 2)}. "
        + (f"Base: {rows['Slender_base'].astype(str).iloc[0]}. "
           if "Slender_base" in rows.columns else "")
        + f"Check: {status}.",
    )


def _bcc_caption(rows: pd.DataFrame) -> str:
    """One line on the strong column - weak beam check, for under the interaction plot."""
    parts = []
    for axis in ("X", "Y"):
        ratios = _numbers_of(rows, f"BCC_Ratio_{axis}")
        if not ratios.empty:
            parts.append(f"along {axis} {ratios.min():.2f}")
    status = rows.get("BCC_Status", pd.Series(dtype=str)).astype(str)
    failing = status.str.startswith("FAIL").any()
    required = dcr_targets.limit(dcr_targets.COLUMN, dcr_targets.STRONG_COLUMN)
    if parts:
        return (" Strong column - weak beam is a separate check at the joints, on nominal "
                f"strengths, and is not drawn here: lowest sum Mnc / sum Mnb {', '.join(parts)} "
                f"(required {required:g}): {'FAIL' if failing else 'PASS'}; see the table above.")
    reason = next((text for text in status if text and text.lower() != "nan"), "")
    return (" Strong column - weak beam is a separate check at the joints and is not drawn "
            f"here: {reason or 'no ratio for this column'}.")


def column_interaction_figure(rows: pd.DataFrame, path: str, dmain: float, dties: float,
                              cover: float, is_smrf: bool) -> str | None:
    """Save the 3D P-Mx-My surface of a column's final layout with its demands (PNG, or a
    compact JPEG when the path ends in .jpg, as the calculation report asks).

    The demands are every combination at both ends; the hull vertices and the
    governing demand are marked. None when the layout has no surface.
    """
    from design.concrete.columns.interaction import (
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
            relative = f"{figure_dir}/{safe}.jpg"
            if column_interaction_figure(rows, os.path.join(folder, relative), **figure_options):
                figures[name] = (relative, "Design interaction surface (phi Pn, phi Mnx, phi "
                                 "Mny) of the final bar layout with every combination at both "
                                 "ends; the hull vertices decide the check, the star governs."
                                 + _bcc_caption(rows))
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
            "p{1.4cm}p{2.9cm}p{1.3cm}p{2.3cm}p{1.1cm}p{1.9cm}p{1.9cm}p{2.6cm}",
            command="sdt columns",
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
        ("Second-order effects", "Sway: ETABS P-delta analysis (ACI 6.7). Member: moment "
                                 "magnification (ACI 6.6.4.5)"
         if "Slenderness_Check" in report.columns else "Not included: the forces must hold them"),
    ]
    return build_column_calc_report(report, path, information, {
        "dmain": dmain, "dties": dties, "cover": cover, "is_smrf": is_smrf})
