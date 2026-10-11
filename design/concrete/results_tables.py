"""The beam and column results in the terminal: one row per member and the
governing equations.

``sdt beams``, ``sdt columns`` and ``sdt design`` print these when they end.
The full results are in the workbooks; a model can have thousands of members,
so the terminal lists every member only for a small model, and otherwise the
members that fail (``MAX_ROWS``). The equations are printed once, as the key
to the table, not for each member: the calculation of each member is in the
PDF report.
"""

from __future__ import annotations

import pandas as pd

from utilities.terminal import Report

MAX_ROWS = 40     # a model with more members than this lists the failing ones

BEAM_EQUATIONS = [
    "Flexure:     φMₙ ≥ Mᵤ,  Mₙ = Aₛ fy (d − a/2) + A′ₛ f′ₛ (d − d′),  a = (Aₛ fy − A′ₛ f′ₛ) / (0.85 f′c b)   (ACI 22.2)",
    "Shear:       φVₙ = φ (Vc + Vₛ) ≥ Vᵤ,  Vc = 0.17 √f′c b d,  Vₛ = Aᵥ fyt d / s ≤ 0.66 √f′c b d   (ACI 22.5)",
    "Torsion:     Tᵤ ≤ φTₙ,  Tₙ = 2 Aₒ Aₜ fyt / s;  compatibility torsion: Tᵤ ≤ φTcr   (ACI 22.7)",
    "SMRF shear:  Vₑ = (Mpr₁ + Mpr₂) / lₙ + V_gravity,  Mpr at 1.25 fy   (ACI 18.6.5.1)",
    "SMRF bars:   ρ ≤ 0.025;  M⁺ₙ ≥ ½ M⁻ₙ at a support face;  Mₙ ≥ ¼ Mₙ,max at every section   (ACI 18.6.3)",
    "Deflection:  Iₑ = (Mcr/Mₐ)³ Ig + [1 − (Mcr/Mₐ)³] Icr ≤ Ig,  λΔ = ξ / (1 + 50 ρ′)   (ACI 24.2)",
    "             Δ after partitions = λΔ Δ(D + 0.25 L) + Δ(D + L) − Δ(D + 0.25 L) ≤ L/480 or L/240",
]
COLUMN_EQUATIONS = [
    "Axial load and bending:  Mᵤ / φMₙ ≤ 1 at φPₙ = Pᵤ, on the biaxial surface;  φPₙ,max = 0.80 φ [0.85 f′c (Ag − Aₛₜ) + fy Aₛₜ]   (ACI 22.4)",
    "Slenderness:  k lᵤ / r ≤ 34 + 12 (M₁/M₂) ≤ 40;  else Mc = δₙₛ M₂,  δₙₛ = Cm / (1 − Pᵤ / 0.75 Pc) ≤ 1.4   (ACI 6.2.5, 6.6.4.5)",
    "Shear:        φVₙ = φ (Vc + Vₛ) ≥ Vᵤ;  SMRF: Vₑ = (Mpr,top + Mpr,bottom) / lᵤ   (ACI 22.5, 18.7.6)",
    "Strong column - weak beam:  ΣMₙc ≥ 1.2 ΣMₙb   (ACI 18.7.3.2)",
    "Joint shear:  Vⱼ ≤ φVₙ = 0.85 γ √f′c Aⱼ,  γ = 1.7, 1.2 or 1.0 by the faces confined   (ACI 18.8.4)",
    "Confinement:  Aₛₕ / (s bc) ≥ 0.3 (Ag/Ach − 1) f′c / fyt  and  0.09 f′c / fyt   (ACI 18.7.5.4)",
]


def _bars(row, zone: str) -> str:
    counts = [int(float(row.get(f"n_{zone}_L{k}", 0) or 0)) for k in (1, 2, 3)]
    counts = [c for c in counts if c]
    return "+".join(map(str, counts)) if counts else "-"


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _shown(names: list[str], failing: set[str]) -> tuple[list[str], str]:
    """The members to list, and the line that says what was left out."""
    if len(names) <= MAX_ROWS:
        return names, ""
    listed = [name for name in names if name in failing][:MAX_ROWS]
    passing = len(names) - len(failing)
    if not failing:
        return listed, f"All {len(names)} members pass; they are listed in the results workbook."
    note = (f"{len(names)} members: the {len(listed)} listed fail"
            + (f" ({len(failing) - len(listed)} more fail)" if len(failing) > len(listed) else "")
            + f"; the {passing} that pass are in the results workbook.")
    return listed, note


def beam_results_report(results: pd.DataFrame, title: str = "BEAM DESIGN - RESULTS") -> Report:
    """One row per beam: section, bars at the left support, midspan and right
    support (the layers joined by +), stirrups, deflection ratio and status."""
    report = Report(title)
    if results is None or len(results) == 0:
        return report.lines(["No beam was designed."])
    names = list(dict.fromkeys(results["UniqueName"].astype(str)))
    by_name = {name: rows for name, rows in results.groupby(results["UniqueName"].astype(str),
                                                            sort=False)}
    status = {name: next((s for s in rows["Design_Status"].astype(str) if s != "OK"), "OK")
              for name, rows in by_name.items()}
    failing = {name for name, text in status.items() if text != "OK"}
    listed, note = _shown(names, failing)
    rows = []
    for name in listed:
        faces = by_name[name]
        face = faces["Face"].astype(str) if "Face" in faces.columns else pd.Series(dtype=str)
        top = faces[face.eq("TOP")].iloc[0] if face.eq("TOP").any() else faces.iloc[0]
        bottom = faces[face.eq("BOTTOM")].iloc[0] if face.eq("BOTTOM").any() else faces.iloc[-1]
        legs, near, mid = (_number(top.get(key)) for key in
                           ("Stirrup_Legs", "Spacing_2H", "Spacing_Mid"))
        stirrups = "-" if legs is None else (
            f"{legs:.0f} legs @ {near:.0f} / {mid:.0f}" if near is not None and mid is not None
            else f"{legs:.0f} legs")
        ratio = _number(top.get("Defl_ratio"))
        width, depth = _number(top.get("Width")), _number(top.get("Depth"))
        rows.append([
            name, str(top.get("Story", "")),
            f"{width:.0f} x {depth:.0f}" if width and depth else str(top.get("SectProp", "")),
            " | ".join(_bars(top, zone) for zone in ("left", "mid", "right")),
            " | ".join(_bars(bottom, zone) for zone in ("left", "mid", "right")),
            stirrups, "-" if ratio is None else f"{ratio:.2f}",
            "OK" if status[name] == "OK" else status[name].replace("FAILED: ", "FAIL: "),
        ])
    report.heading("Beams" if not note else "Beams that fail")
    report.table(["Beam", "Level", "b x h (mm)", "Top bars L | M | R", "Bottom bars L | M | R",
                  "Stirrups (mm)", "Δ / limit", "Status"], rows)
    if note:
        report.lines([note])
    report.heading("Equations of the checks")
    report.equations(BEAM_EQUATIONS)
    return report


def column_results_report(report_table: pd.DataFrame,
                          title: str = "COLUMN DESIGN - RESULTS") -> Report:
    """One row per column: section, bars, steel ratio, the largest utilizations,
    the lowest strong column ratio and the status."""
    report = Report(title)
    if report_table is None or len(report_table) == 0:
        return report.lines(["No column was designed."])
    table = report_table
    names = list(dict.fromkeys(table["UniqueName"].astype(str)))
    by_name = {name: rows for name, rows in table.groupby(table["UniqueName"].astype(str),
                                                          sort=False)}
    failing = {name for name, rows in by_name.items()
               if rows["Column_Design_Status"].astype(str).eq("FAIL").any()}
    listed, note = _shown(names, failing)

    def extreme(rows, columns, largest=True) -> str:
        values = [v for column in columns if column in rows.columns
                  for v in pd.to_numeric(rows[column], errors="coerce").dropna().tolist()]
        if not values:
            return "-"
        return f"{max(values) if largest else min(values):.2f}"

    rows = []
    for name in listed:
        own = by_name[name]
        first = own.iloc[0]
        rho = _number(first.get("Reinforcement_Ratio"))
        rows.append([
            name, str(first.get("Story", "")), str(first.get("Section", "")),
            str(first.get("Longitudinal_Bars", "")),
            "-" if rho is None else f"{rho * 100:.2f} %",
            extreme(own, ["Flexure_Utilization"]), extreme(own, ["Shear_Utilization"]),
            extreme(own, ["BCC_Ratio_X", "BCC_Ratio_Y"], largest=False),
            extreme(own, ["Joint_Shear_Utilization_X", "Joint_Shear_Utilization_Y"]),
            "FAIL" if name in failing else "OK",
        ])
    report.heading("Columns" if not note else "Columns that fail")
    report.table(["Column", "Level", "Section", "Vertical bars", "ρ", "Mᵤ/φMₙ", "Vᵤ/φVₙ",
                  "ΣMnc/ΣMnb", "Vⱼ/φVₙ", "Status"], rows)
    if note:
        report.lines([note])
    report.heading("Equations of the checks")
    report.equations(COLUMN_EQUATIONS)
    return report


DEFLECTION_EQUATIONS = [
    "Iₑ = (Mcr/Mₐ)³ Ig + [1 − (Mcr/Mₐ)³] Icr ≤ Ig,  Mcr = fr Ig / (h/2),  fr = 0.62 √f′c   (ACI 24.2.3.5)",
    "Δ = ∫∫ M / (Ec Iₑ) dx dx  along the span, zero at its supports;  Ec = 4700 √f′c",
    "λΔ = ξ / (1 + 50 ρ′),  ξ = 2.0 for 5 years or more   (ACI 24.2.4.1)",
    "Live load:         Δ(D + L) − Δ(D) ≤ L/360          (roof: Δ(D + Lr) − Δ(D) ≤ L/180)",
    "After partitions:  λΔ Δ(D + 0.25 L) + Δ(D + L) − Δ(D + 0.25 L) ≤ L/480 or L/240   (ACI Table 24.2.2)",
]


def deflection_results_report(table: pd.DataFrame,
                              title: str = "BEAM DEFLECTION - RESULTS") -> Report:
    """One row per beam of the deflection workbook (its column names): the span
    checked, the live and after-partitions deflections with their limits, the
    governing ratio and the check."""
    report = Report(title)
    if table is None or len(table) == 0:
        return report.lines(["No beam was checked."])
    names = table["UniqueName"].astype(str).tolist() if "UniqueName" in table.columns else []
    check = table.get("Deflection check", pd.Series([""] * len(table))).astype(str).tolist()
    failing = {name for name, text in zip(names, check) if text.startswith("FAIL")}
    listed, note = _shown(names, failing)
    wanted = set(listed)
    columns = [("Story", "Level"), ("Span checked (members and kind)", "Span checked"),
               ("Span length checked (mm)", "L (mm)"), ("Δ live (mm)", "Δ live"),
               ("Δ live limit L/360 (mm)", "L/360"),
               ("Δ after attachment of partitions (mm)", "Δ after partitions"),
               ("Δ after attachment limit (mm)", "limit"),
               ("Δ / limit (governing)", "Δ / limit"), ("Deflection check", "Check")]
    columns = [(source, label) for source, label in columns if source in table.columns]
    rows = []
    for index, name in enumerate(names):
        if name not in wanted:
            continue
        row = [name]
        for source, _ in columns:
            value = table[source].iloc[index]
            number = _number(value)
            row.append("" if value is None or value != value else
                       (f"{number:.2f}" if number is not None and "mm" in source
                        and "length" not in source else
                        (f"{number:,.0f}" if number is not None and "length" in source else
                         (f"{number:.2f}" if number is not None else str(value)))))
        rows.append(row)
    report.heading("Beams" if len(names) <= MAX_ROWS else "Beams that fail")
    report.table(["Beam"] + [label for _, label in columns], rows)
    if note:
        report.lines([note])
    report.heading("Equations of the check")
    report.equations(DEFLECTION_EQUATIONS)
    return report
