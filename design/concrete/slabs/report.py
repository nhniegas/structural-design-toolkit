"""The slab calculation report (PDF): the design basis, a summary of every panel, then
each panel: its geometry and loads, the method and its moments, the bars at each
location, shear and thickness, each with its clause."""

from __future__ import annotations

import math

from design.concrete.slabs.design import TWO_WAY, PanelDesign, SlabOptions
from design.concrete.slabs.schedule import column_label
from utilities._calc_report import MemberReport, ReportTable, Tex, build_calc_report, number


def _check(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def member_report(d: PanelDesign, options: SlabOptions) -> MemberReport:
    p = d.panel
    name = f"{d.mark or '-'}: {' '.join(p.areas)}"
    geometry = ReportTable(
        "Panel and loads", ["Item", "Value"],
        [["Story, area objects", f"{p.story}, {' '.join(p.areas)}"],
         ["Centre (m), axes", f"({p.centre[0] / 1000:.3f}, {p.centre[1] / 1000:.3f}), x at "
                              f"{math.degrees(p.angle):.1f} deg from global X"],
         ["Spans centre to centre x, y (m)", f"{p.lx / 1000:.3f}, {p.ly / 1000:.3f}"],
         ["Kind, short direction", f"{d.kind}, {d.short_dir}"],
         ["Clear spans short, long (m)", f"{d.short_clear / 1000:.3f}, {d.long_clear / 1000:.3f}"],
         ["Sides W E S N", "  ".join(
             f"{k}:{'beam' if s.is_supported and s.support and s.support.kind != 'wall' else 'wall' if s.is_supported else 'free'}"
             f"{'/cont' if s.is_continuous else ''}" for k, s in p.sides.items())],
         ["h, f'c, fy", f"{p.h:g} mm, {p.fc:g} MPa, {options.fy:g} MPa"],
         ["Self weight, SDL, live (kPa)",
          f"{d.self_weight:.2f}, {p.dead:.2f}, {p.live:.2f} ({p.load_note or 'no load in model'})"],
         ["wu (kPa)", Tex(f"${options.dead_factor:g}(SW + SDL) + {options.live_factor:g}L "
                          f"= {d.wu:.2f}$, at least ${options.dead_only_factor:g}D$ (NSCP 203-1, 203-2)")],
         ["Method", d.method or "-"]]
        + ([["alpha_fm", f"{d.alpha_fm:.2f} (Method 3 at {options.alpha_limit:g} and over)"]]
           if d.kind == TWO_WAY else []),
        "p{45mm}p{120mm}")
    rows = []
    for key, b in d.bars.items():
        rows.append([column_label(key), number(b.mu, 2), number(b.d, 0), number(b.as_required, 0),
                     number(b.as_min, 0), number(b.spacing_limit, 0),
                     f"{options.bar:g} @ {b.spacing:g}", number(b.as_provided, 0),
                     number(b.phi_mn, 2), _check(b.passed)])
    bars = ReportTable(
        "Bars per metre width",
        ["Location", Tex("$M_u$"), Tex("$d$"), Tex("$A_s$ req."), Tex("$A_{s,min}$"),
         Tex("$s_{max}$"), "Bars", Tex("$A_s$ prov."), Tex(r"$\phi M_n$"), "Check"],
        rows, "lrrrrrlrrl",
        ("Mu, phiMn in kN-m/m; d, s in mm; As in mm2/m. As from the rectangular stress block, "
         "phi 0.90 (NSCP 422.2, Table 421.2.2); As,min NSCP Table 407.6.1.1 / 408.6.1.1 "
         "(temperature bars Table 424.4.3.2); s,max NSCP 407.7.2.3, 407.7.2.4, 408.7.2.2, "
         "424.3.2 and the office maximum. Two-way bars: net tensile strain at least 0.004 "
         "(408.3.3.1)."))
    checks = ReportTable(
        "Shear and thickness", ["Check", "Demand", "Capacity", "Result"],
        [[Tex(r"Shear, $\phi V_c = 0.75 \times 0.17 \sqrt{f'_c}\, b\, d$ (NSCP 422.5.5.1)"),
          f"{d.vu:.1f} kN/m", f"{d.phi_vc:.1f} kN/m", _check(d.vu <= d.phi_vc + 1e-9)],
         ["Least thickness (NSCP " + ("Table 408.3.1.2" if d.kind == TWO_WAY else
                                      "Table 407.3.1.1") + ")",
          f"{d.h_min:.0f} mm", f"{p.h:g} mm", _check(p.h >= d.h_min - 1e-6)]],
        "p{80mm}rrl")
    tables = [geometry]
    if d.bars:
        tables += [bars, checks]
    if d.flags:
        tables.append(ReportTable("Flags", ["Flag"], [[f] for f in d.flags], "p{165mm}"))
    return MemberReport(name, [d.mark or "-", " ".join(p.areas), d.kind, d.method.split(" (")[0],
                               f"{p.h:g}", _check(d.passed)], tables)


def export_slab_pdf(designs: list[PanelDesign], options: SlabOptions, path: str) -> str | None:
    members = [member_report(d, options) for d in sorted(
        designs, key=lambda d: (d.panel.story, d.mark, d.panel.areas))]
    information = [
        ("Design code", "NSCP 2015 Sections 406, 407, 408 (ACI 318M-14)"),
        ("One-way slabs", "NSCP Table 406.5.2 when 406.5.1 holds; else a strip analysis with "
                          "the live load of 406.4.2"),
        ("Two-way slabs", "ACI Method 3 (NSCP 408.2.1) on stiff beams; the Direct Design Method "
                          "(408.10) on flexible beams when its limits hold"),
        ("Bars", f"{options.bar:g} mm both ways, spacing at most {options.max_spacing:g} mm, "
                 "rounded down to 25 mm"),
        ("Panels", "Not resized: a panel that fails is flagged"),
    ]
    return build_calc_report(
        "Slab Design Calculations (NSCP 2015)", information,
        ["Mark", "Area objects", "Kind", "Method", "h", "Result"], members, path,
        "p{22mm}p{32mm}p{22mm}p{50mm}p{10mm}p{14mm}", command="sdt slabs")
