"""The wall calculation report (PDF), in the format of the column report: one part
per wall and story with the shear of each leg, the axial load and bending about
both principal axes with the P-M curves and the demands, the minimum steel and
both boundary element checks, each with its clause."""

from __future__ import annotations

import math
import os

from design.concrete.walls import (
    alpha_c,
    max_spacing,
    moments_about,
)
from utilities._calc_report import MemberReport, ReportTable, Tex, build_calc_report, number


def _pm_figure(design, folder: str, name: str) -> str | None:
    """The P-M curves about the two principal axes with the demands; the image path
    relative to the report's folder."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    section = design.section
    axes = (section.principal_angle(), section.principal_angle() + 90.0)
    fig, panels = plt.subplots(1, 2, figsize=(7.2, 3.0))
    for panel, axis in zip(panels, axes):
        curve = section.interaction(axis)
        positive = sorted((p, m) for p, m in curve if m >= 0)
        negative = sorted((p, m) for p, m in curve if m < 0)
        for branch in (positive, negative):
            panel.plot([m for _, m in branch], [-p for p, _ in branch], color="#2b6cb0", lw=1.2)
        for f in design.forces:
            m = moments_about(axis, f.mx, f.my)
            panel.plot(m, -f.p, "o", ms=3, color="#c53030")
        panel.set_title(f"About the axis at {axis % 180:.0f}°", fontsize=8)
        panel.set_xlabel("φMn, Mu (kN-m)", fontsize=7)
        panel.set_ylabel("φPn, Pu (kN, compression +)", fontsize=7)
        panel.tick_params(labelsize=6)
        panel.axhline(0, color="#999", lw=0.5)
        panel.axvline(0, color="#999", lw=0.5)
    fig.tight_layout()
    figures = os.path.join(folder, "wall figures")
    os.makedirs(figures, exist_ok=True)
    path = os.path.join(figures, f"{name}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return os.path.relpath(path, folder).replace("\\", "/")


def member_report(wall, special: bool, legs, design, inputs, folder: str) -> MemberReport:
    name = f"W{wall.number} {design.story}"
    rows = []
    for leg in legs:
        rho_t, s, phi = design.horizontal.get(leg.label, (0.0, 0.0, 0.0))
        vu = design.leg_vu.get(leg.label, 0.0)
        acv = leg.length * leg.thickness
        root = math.sqrt(inputs.fc)
        if special:
            ac = alpha_c(design.hw / leg.length if leg.length else 99.0)
            provided = design.curtains * math.pi * inputs.bar_h ** 2 / 4 / (s * leg.thickness)
            phivn = phi * acv * (ac * root + provided * inputs.fyt) / 1e3
            cap = 0.83 * acv * root / 1e3
            eq = "αc"
        else:
            ac = None
            d = 0.8 * leg.length
            provided = design.curtains * math.pi * inputs.bar_h ** 2 / 4 / (s * leg.thickness)
            phivn = phi * (0.17 * root * leg.thickness * d + provided * inputs.fyt * leg.thickness * d) / 1e3
            cap = 0.83 * root * leg.thickness * d / 1e3
            eq = "-"
        rows.append([leg.label, number(leg.length, 0), number(leg.thickness, 0), number(vu, 1),
                     number(ac, 2) if ac is not None else eq,
                     f"{design.curtains} x {inputs.bar_h:g} @ {s:g}", number(provided, 4),
                     number(phi, 2), number(phivn, 1), number(cap, 1),
                     "PASS" if vu <= phivn + 1e-9 else "FAIL"])
    shear = ReportTable(
        "Shear, leg by leg", ["Leg", Tex("$l_w$"), Tex("$h$"), Tex("$V_u$"), Tex(r"$\alpha_c$"),
                              "Horizontal bars", Tex(r"$\rho_t$"), Tex(r"$\phi$"),
                              Tex(r"$\phi V_n$"), Tex(r"$V_n$ limit"), "Check"],
        rows, "lrrrrlrrrrl",
        ("Lengths in mm, forces in kN. Special walls: Vn = Acv (αc √f'c + ρt fy) (NSCP "
         "Eq. 418.10.4.1), limit 0.83 Acw √f'c (418.10.4.4), φ 0.75 or 0.60 by 421.2.4.1. "
         "Ordinary walls: Vc = 0.17 √f'c h d, d = 0.8 lw (411.5.4)."))
    section = design.section
    pm_rows = []
    for axis in (section.principal_angle(), section.principal_angle() + 90.0):
        worst = None
        for f in design.forces:
            m = moments_about(axis, f.mx, f.my)
            if abs(m) < 1e-6:
                continue
            cap = section.capacity_at(axis, f.p, 1.0 if m > 0 else -1.0)
            ratio = abs(m) / cap if cap > 0 else math.inf
            if worst is None or ratio > worst[0]:
                worst = (ratio, f.combo, f.p, m, cap)
        if worst:
            pm_rows.append([f"{axis % 180:.0f}°", worst[1], number(-worst[2], 1),
                            number(worst[3], 1), number(worst[4], 1), number(worst[0], 3),
                            "PASS" if worst[0] <= 1.0 else "FAIL"])
    flexure = ReportTable(
        "Axial load and bending, about each principal axis",
        ["Axis", "Combination", Tex("$P_u$ (+ comp.)"), Tex("$M_u$"), Tex(r"$\phi M_n$"),
         "Ratio", "Check"], pm_rows, "llrrrrl",
        (f"Forces in kN, moments in kN-m. Vertical bars {design.curtains} x {inputs.bar_v:g} mm "
         f"@ {design.vertical_spacing:g} mm, ρl = {design.rho_l:.4f}. Strain compatibility on "
         "the assembled section (NSCP 422.2, 422.4; 418.10.5.1), φ by 421.2.2, φPn,max by "
         "422.4.2.1."))
    limits = ReportTable(
        "Minimum steel and spacing", ["Rule", "Value"],
        [["Vertical ratio ρl", number(design.rho_l, 4)],
         ["Curtains", str(design.curtains)],
         ["Largest spacing", f"{max_spacing(special, min(l.thickness for l in legs), min(l.length for l in legs), True):g} mm"],
         ["Clauses", "NSCP 418.10.2.1, 418.10.2.2, 418.10.4.3" if special
          else "NSCP Table 411.6.1, 411.6.2, 411.7.2, 411.7.3"]], "ll")
    tables = [shear, flexure, limits]
    if special and design.boundary:
        tables.append(ReportTable(
            "Special boundary elements", ["Axis", "End", "Combination", Tex("$c$"),
                                          Tex("$c$ limit"), "By displacement", Tex(r"$\sigma$"),
                                          "By stress", "Decided by", "Length"],
            [[f"{b['axis']:.0f}°", b["end"], b["combo"], number(b["c"], 0),
              number(b["c_limit"], 0),
              "-" if b["by_displacement"] is None else ("needed" if b["by_displacement"] else "no"),
              number(b["stress"], 2), "needed" if b["by_stress"] else "no",
              b["method"].split(" (")[0], number(b["length"], 0) if b["needed"] else "-"]
             for b in design.boundary], "llllrrlrlll",
            ("c in mm, σ in MPa. Displacement method: needed when c ≥ lw / (600 δu/hw), "
             "δu/hw at least 0.005 (418.10.6.2), for hw/lw ≥ 2. Stress method: needed when "
             "P/A + M c/I exceeds 0.2 f'c (418.10.6.3). Length: the larger of c − 0.1 lw and "
             "c/2 (418.10.6.4(a)), rounded up.")))
    figure = _pm_figure(design, folder, name.replace(" ", "_"))
    return MemberReport(
        name, [name, "special" if special else "ordinary",
               f"{design.curtains} x {inputs.bar_v:g} @ {design.vertical_spacing:g}",
               number(design.pm_ratio, 3),
               "yes" if any(b["needed"] for b in design.boundary) else "no",
               "PASS" if design.passed else "FAIL"],
        tables, [(figure, "P-M curves about the principal axes (blue) and the demands "
                          "(red)")] if figure else [])


def export_wall_pdf(designs, inputs_of, path: str) -> str | None:
    """The PDF of every wall and story; ``inputs_of(wall)`` gives its WallInputs."""
    folder = os.path.dirname(path)
    members = [member_report(wall, special, legs, design, inputs_of(wall), folder)
               for wall, special, legs, design in designs]
    information = [("Design code", "NSCP 2015 Section 418.10 and Section 411"),
                   ("Section", "The legs that meet, as one section (418.10.5.1)"),
                   ("Values shown", "The governing combination about each principal axis, the "
                                    "shear of each leg")]
    return build_calc_report(
        "Wall Design Calculations (NSCP 2015)", information,
        ["Wall", "Kind", "Vertical bars", "P-M ratio", "Boundary el.", "Result"], members, path,
        "p{28mm}p{20mm}p{34mm}p{18mm}p{20mm}p{14mm}", command="sdt walls", member_pages=True)
