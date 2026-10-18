"""The PDF calculation report of ``sdt pilecap``.

One report for one pile cap, in the format of the other reports
(``utilities/report_style.py``): the design basis and references, a plan of the
cap, the pile reactions, and every check with its equation, its numbers, its
clause and a PASS or FAIL tag.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from datetime import datetime

REFERENCES = [
    ("NSCP 2015", "National Structural Code of the Philippines, Volume 1, 7th Edition, 2015. "
                  "The governing code."),
    ("ACI 318M-14", "Building Code Requirements for Structural Concrete and Commentary, the basis "
                    "of NSCP 2015 Chapter 4; cited for its commentary (R13.2.6.3, R13.4.2.4, "
                    "R13.4.2.5)."),
    ("CRSI 2015", "Concrete Reinforcing Steel Institute, Design Guide for Pile Caps, 1st Edition, "
                  "2015 (written to ACI 318-14)."),
    ("FHWA GEC 12", "FHWA-NHI-16-009, Design and Construction of Driven Pile Foundations, "
                    "Volume I, 2016; it quotes AASHTO LRFD Bridge Design Specifications, "
                    "Article 10.7.1.2."),
]


def _e(text) -> str:
    from utilities.report_style import escape

    return escape(text)


def _n(value, digits=1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not math.isfinite(value):
        return r"$\infty$"
    return f"{value:,.{digits}f}"


def _table(title: str, header: list[str], rows: list[list[str]], spec: str,
           note: str = "", long: bool = False) -> str:
    """A table in the look of the reports; ``rows`` are LaTeX already."""
    head = r"\rowcolor{sdtband}" + " & ".join(r"\bfseries " + _e(h) for h in header) + r" \\"
    body = [" & ".join(row) + r" \\ \hline" for row in rows]
    if body:
        body[-1] = body[-1][: -len(r" \hline")]
    lines = [rf"{{\bfseries\small {_e(title)}}}\par\vspace{{0.1cm}}"]
    if long:
        lines += [r"{\small\setlength{\tabcolsep}{4pt}"
                  rf"\begin{{longtable}}{{@{{}}{spec}@{{}}}}",
                  head, r"\endhead"] + body + [r"\end{longtable}}"]
    else:
        lines += [r"\noindent{\small\setlength{\tabcolsep}{4pt}\sbox{\sdttable}{"
                  rf"\begin{{tabular}}{{{spec}}}", head] + body + [
            r"\end{tabular}}%",
            r"\ifdim\wd\sdttable>\linewidth\resizebox{\linewidth}{!}{\usebox{\sdttable}}"
            r"\else\usebox{\sdttable}\fi}\par"]
    if note:
        lines.append(rf"{{\footnotesize\sdtsoft{{{_e(note)}}}}}\par")
    lines.append(r"\vspace{0.3cm}")
    return "\n".join(lines)


def _status(check) -> str:
    from design.concrete.pile_cap import status
    from utilities.report_style import tag

    text = status(check)
    if text in ("PASS", "OK", "INFO OK"):
        return tag("PASS" if text == "PASS" else "OK")
    if text in ("FAIL", "INFO EXCEEDED"):
        return tag("FAIL") if text == "FAIL" else r"\sdtwarn{EXCEEDED}"
    if text == "NOT MET":
        return r"\sdtwarn{NOT MET}"
    return _e(text)


def draw_plan(cap, path: str) -> str:
    """A plan of the cap: piles numbered, the column, the critical sections."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    from design.concrete.pile_cap import pile_shape

    v = cap.values
    g = cap.geometry
    fig, ax = plt.subplots(figsize=(6.2, 6.2 * cap.Ly / max(cap.Lx, cap.Ly) + 0.6), dpi=130)
    ax.add_patch(Rectangle((-cap.Lx / 2, -cap.Ly / 2), cap.Lx, cap.Ly, fill=True,
                           facecolor="#F3F4F6", edgecolor="#1C1E24", lw=1.4))
    for i, (x, y) in enumerate(cap.piles):
        shape = pile_shape(v, x, y)
        xs, ys = shape.exterior.xy
        ax.fill(xs, ys, facecolor="#FFFFFF", edgecolor="#1C1E24", lw=1.0)
        ax.annotate(str(i + 1), (x, y), ha="center", va="center", fontsize=9, color="#1C1E24")
        region = shape.buffer(g["d_avg"] / 2).intersection(g["cap"])
        if not region.is_empty and region.geom_type == "Polygon":
            rx, ry = region.exterior.xy
            ax.plot(rx, ry, color="#6B7280", lw=0.6, ls=":")
    col = g["column"]
    cx, cy = col.exterior.xy
    ax.fill(cx, cy, facecolor="#FFAA2C", edgecolor="#1C1E24", lw=1.0, alpha=0.9)
    region = g.get("column_perimeter")
    if region is not None and not region.is_empty and region.geom_type == "Polygon":
        rx, ry = region.exterior.xy
        ax.plot(rx, ry, color="#B3261E", lw=0.8, ls="--", label="two-way section at d/2")
    for axis in ("X", "Y"):
        half = (g["cx_eq"] if axis == "X" else g["cy_eq"]) / 2
        s = half + g["d"][axis]
        if axis == "X" and s < cap.Lx / 2:
            for side in (1, -1):
                ax.plot([side * s, side * s], [-cap.Ly / 2, cap.Ly / 2], color="#1E7A34", lw=0.7,
                        ls="-.", label="one-way section at d" if side == 1 else None)
        if axis == "Y" and s < cap.Ly / 2:
            for side in (1, -1):
                ax.plot([-cap.Lx / 2, cap.Lx / 2], [side * s, side * s], color="#1E7A34", lw=0.7,
                        ls="-.")
    ax.add_patch(Circle((0, 0), 1, color="#1C1E24"))
    ax.set_aspect("equal")
    pad = 0.08 * max(cap.Lx, cap.Ly)
    ax.set_xlim(-cap.Lx / 2 - pad, cap.Lx / 2 + pad)
    ax.set_ylim(-cap.Ly / 2 - pad, cap.Ly / 2 + pad)
    ax.set_xlabel("X (mm)", fontsize=8)
    ax.set_ylabel("Y (mm)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(color="#D5D8DE", lw=0.4)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(fontsize=7, loc="upper right", framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def report_body(cap, figure: str) -> str:
    """The LaTeX of the report after its title block."""
    from design.concrete import pile_cap as pc
    from utilities.report_style import tag

    v = cap.values
    g = cap.geometry
    parts = []

    # ---- summary of the design
    rows = [[_e(line)] for line in pc.headline(cap)]
    parts.append(r"\section{Result}")
    parts.append(r"\begin{itemize}" + "".join(rf"\item {_e(line)}" for line in pc.headline(cap))
                 + r"\end{itemize}")
    del rows
    bar_rows = [[_e(b.face), _e(b.direction), _e(f"{b.count} - {b.diameter:g} mm @ {b.spacing:g}"),
                 _n(b.Mu), _n(b.d, 0), _n(b.As_req, 0), _n(b.As_prov, 0), _e(b.anchorage)]
                for b in cap.bars]
    parts.append(_table("Reinforcement", ["Face", "Along", "Bars", "Mu (kN-m)", "d (mm)",
                                          "As required (mm2)", "As provided (mm2)", "Ends"],
                        bar_rows, "llllrrrl",
                        "As required: the larger of the flexural steel (with the minimum) and "
                        "the strut-and-tie tie; over the full width of the cap."))

    # ---- design basis
    parts.append(r"\section{Design Basis}")
    basis = [
        ("Column", f"{v['column_shape'].lower()}, {v['cx']:g}" +
         (f" x {v['cy']:g}" if v["column_shape"] == "Rectangular" else " dia.") + " mm"),
        ("Cap", f"{cap.Lx:.0f} x {cap.Ly:.0f} x {cap.h:.0f} mm"),
        ("Piles", f"{cap.n} x {cap.dp:g} mm {v['pile_shape'].lower()}, embedded "
                  f"{v['embed']:g} mm, heads {v['head'].lower()}"),
        ("Concrete", f"f'c = {cap.fc:g} MPa, {v['gamma_c']:g} kN/m3"),
        ("Bars", f"fy = {cap.fy:g} MPa; bottom {v['bottom_bar']:g} mm, top {v['top_bar']:g} mm"),
        ("Cover", f"{v['clear_above_pile']:g} mm clear above the piles; top {v['top_cover']:g} mm"),
        ("Over the cap", f"soil {v['soil_h']:g} m at {v['gamma_s']:g} kN/m3, surcharge "
                         f"{v['surcharge']:g} kPa"),
        ("Pile capacity", f"{v['capacity_method']}: compression {v['q_comp']:g} kN, tension "
                          f"{v['q_tens']:g} kN, lateral {v['q_lat']:g} kN"),
        ("Effective depth", f"d along X {g['d']['X']:.0f} mm, along Y {g['d']['Y']:.0f} mm, "
                            f"average {g['d_avg']:.0f} mm"),
        ("Tolerance", f"piles taken {v.get('tolerance') or 0:g} mm farther from the column "
                      "(CRSI 5.2)"),
    ]
    parts.append(_table("Inputs", ["Item", "Value"],
                        [[_e(a), _e(b)] for a, b in basis],
                        r"l>{\raggedright\arraybackslash}p{0.68\linewidth}"))
    parts.append(
        r"{\small Signs: $P$ is compression positive; $M_x$ increases the reaction of the piles "
        r"on $+Y$, $M_y$ those on $+X$; $V_x$, $V_y$ act at the top of the cap. The cap is "
        r"rigid and the pile heads pinned (CRSI 3.3, 6.2):}"
        r"\[ R_i = \frac{P + W}{n} + \frac{M_y'\,x_i}{\sum x^2} + \frac{M_x'\,y_i}{\sum y^2}, "
        r"\quad M_y' = M_y + V_x\,(h - e_{pile}) \]"
        r"{\small with $W$ the weight of the cap, the soil and the surcharge, and $x_i$, $y_i$ "
        r"from the centroid of the piles (a skewed group uses the product of inertia too). A "
        r"factored combination is checked with 1.2 and 0.9 on $W$ (1.6 and 0 on the "
        r"surcharge), a service one with 1.0 and 0.6, unless it gives its own factors.}\par"
        r"\vspace{0.2cm}")

    # ---- plan
    parts.append(r"\section{Plan}")
    parts.append(r"\begin{center}\includegraphics[width=0.72\textwidth]{" +
                 figure.replace("\\", "/") + r"}\\[0.1cm]{\footnotesize\sdtsoft{Piles numbered; "
                 r"dotted: two-way sections at d/2 around the piles; dashed: around the column; "
                 r"dash-dot: one-way sections at d from the column.}}\end{center}")
    pile_rows = [[str(i + 1), _n(x, 0), _n(y, 0)] for i, (x, y) in enumerate(cap.piles)]
    parts.append(_table("Pile coordinates (mm, from the column centre)", ["Pile", "X", "Y"],
                        pile_rows, "rrr"))

    # ---- reactions
    parts.append(r"\section{Pile Reactions}")
    labels = list(cap.reactions)
    rows = [[_e(label)] + [_n(r) for r in cap.reactions[label]] for label in labels]
    spec = "l" + "r" * cap.n
    parts.append(_table("Pile reactions (kN, compression positive)",
                        ["Combination"] + [str(i + 1) for i in range(cap.n)], rows, spec,
                        long=True))

    # ---- checks by group
    groups = []
    for c in cap.checks:
        if c.group not in groups:
            groups.append(c.group)
    intro = {
        "Pile capacity": r"Service reactions against the allowable capacity (NSCP 413.4.1.1, "
                         r"203.4), or factored reactions against the factored resistance. "
                         r"The lateral load is shared equally (CRSI 3.3).",
        "Pile group": r"Piles in one line resist no moment about that line.",
        "Flexure": r"Moment at the critical section, all forces on one side "
                   r"(NSCP 413.2.6.4, 413.2.7.1; a circular column as a square of equal area, "
                   r"413.2.7.3), pile reactions at the pile centres (413.4.2.2): "
                   r"\[ \phi M_n = 0.90\,A_s f_y \left(d - \frac{a}{2}\right) \ge M_u \] "
                   r"Minimum steel as CRSI 4.3 reads NSCP 409.6.1.2, 409.6.1.3 and Table "
                   r"424.4.3.2: $A_s \ge \min\!\left[\max\left(\frac{0.25\sqrt{f'_c}}{f_y},"
                   r"\frac{1.4}{f_y}\right)b\,d,\ \max\left(\frac{4}{3}A_{s,req},\ "
                   rf"\rho_{{st}}\,b\,h\right)\right]$, $\rho_{{st}} = "
                   rf"{pc.shrinkage_ratio(cap.fy):.4f}$. Bars developed from the column "
                   r"face (NSCP 413.2.8.3): straight $\ell_d$ (the lesser of Table 425.4.2.2 and "
                   r"Eq. 425.4.2.3a), or hooked $\ell_{dh}$ (425.4.3.1).",
        "One-way shear": r"At $d$ from the column (NSCP 413.2.7.2), with the pile reactions "
                         r"counted by NSCP 413.4.2.5: "
                         r"\[ \phi V_c = 0.75 \times 0.17\,\lambda\sqrt{f'_c}\,b\,d "
                         r"\quad \text{(NSCP 422.5.5.1)} \]",
        "Two-way shear": r"At $d/2$ around the column (NSCP 422.6.4.1): "
                         r"\[ v_c = \min\left[0.33,\ 0.17\left(1+\frac{2}{\beta}\right),\ "
                         r"0.083\left(2+\frac{\alpha_s d}{b_0}\right)\right]\lambda\sqrt{f'_c} "
                         r"\quad \text{(NSCP Table 422.6.5.2)} \]",
        "Pile punching": r"Two-way shear around each pile, around piles whose sections overlap, "
                         r"and around corner piles to the cap edges; one-way shear across a "
                         r"corner pile (CRSI 5.4, limit states P1 to P4), with the same $v_c$.",
        "Deep cap (CRSI)": r"Piles closer to the column face than $d$ (one-way) or $d/2$ "
                           r"(two-way): the sections at $d$ miss their reactions, so the shear "
                           r"is checked at the column face with the higher strength of a deep "
                           r"member (CRSI 5.3; ACI R13.4.2.5): "
                           r"\[ v_c = \frac{d}{w}\left(1+\frac{d}{c}\right)0.166\sqrt{f'_c} \le "
                           r"2.66\sqrt{f'_c} \quad\text{(two-way)} \]"
                           r"\[ v_c = \frac{d}{w}\left(3.5-2.5\frac{M_u}{V_u d}\right)"
                           r"\left(0.158 + 0.0083\frac{V_u d}{M_u}\right)\sqrt{f'_c} \le "
                           r"0.83\sqrt{f'_c} \quad\text{(one-way)} \]",
        "Strut-and-tie": r"Three-dimensional model (NSCP 413.2.6.3, Section 423; ACI R13.2.6.3): "
                         r"a strut from the column to each pile, ties along X and Y at the "
                         r"bottom bars. "
                         r"\[ \phi F_{ns} = 0.75 \times 0.85\,\beta_s f'_c A_{cs},\ \beta_s = 0.60 "
                         r"\ \text{(413.4.2.4)}; \quad \phi F_{nt} = 0.75\,A_s f_y \]",
        "Bearing": r"The column on the cap (NSCP 422.8.3; Table 421.2.1(d)), where no "
                   r"strut-and-tie model governs (422.8.1.2): "
                   r"\[ \phi B_n = 0.65 \times \min\left(\sqrt{A_2/A_1},\ 2\right) 0.85 f'_c A_1 "
                   r"\quad \text{(NSCP Table 422.8.3.2)} \]"
                   r"with $A_2$ the base of the frustum sloped 1 vertical to 2 horizontal within "
                   r"the cap. A load over $\phi B_n$ is carried by the column bars or dowels "
                   r"(NSCP 416.3.1.2), at least $0.005A_g$ (416.3.4.1). "
                   r"Nodal zones of a strut-and-tie model (NSCP 423.9): $\phi F_{nn} = 0.75 "
                   r"\times 0.85\,\beta_n f'_c A_{nz}$, $\beta_n$ = 1.0 (CCC), 0.80 (CCT), "
                   r"0.60 (CTT).",
        "Detailing": r"Code limits of detailing.",
        "Recommendation": r"Recommendations of the CRSI guide and of AASHTO LRFD (as quoted by "
                          r"FHWA GEC 12). They are not limits of NSCP 2015 and do not fail the "
                          r"design.",
    }
    parts.append(r"\section{Checks}")
    for group in groups:
        parts.append(r"\subsection*{" + _e(group) + "}")
        if group in intro:
            parts.append(r"{\small " + intro[group] + r"}\par\vspace{0.1cm}")
        rows = []
        for c in [c for c in cap.checks if c.group == group]:
            rows.append([_e(c.name), _n(c.demand), _n(c.capacity), _e(c.unit),
                         _n(c.ratio, 3) if c.ratio is not None else "-", _status(c),
                         _e(c.clause), _e((c.case + ("; " if c.case and c.note else "") + c.note))])
        parts.append(_table(group, ["Check", "Demand", "Capacity", "Unit", "Ratio", "Result",
                                    "Clause", "Combination, notes"], rows,
                            r">{\raggedright\arraybackslash}p{0.22\linewidth}rrlrl"
                            r">{\raggedright\arraybackslash}p{0.13\linewidth}"
                            r">{\raggedright\arraybackslash}p{0.2\linewidth}", long=True))

    if cap.searched:
        rows = [[_n(h, 0), tag("PASS") if ok else tag("FAIL"), _e(why or "-")]
                for h, ok, why in cap.searched]
        parts.append(r"\section{Least Thickness}")
        parts.append(_table("Thicknesses tried (mm)", ["h", "Result", "What fails"], rows,
                            r"rl>{\raggedright\arraybackslash}p{0.6\linewidth}", long=True))

    if cap.notes:
        parts.append(r"\section{Notes}")
        parts.append(r"\begin{itemize}" + "".join(rf"\item {_e(n)}" for n in cap.notes)
                     + r"\end{itemize}")
    parts.append(r"\section{References}")
    parts.append(r"\begin{itemize}" + "".join(rf"\item \textbf{{{_e(a)}}}: {_e(b)}"
                                               for a, b in REFERENCES) + r"\end{itemize}")
    return "\n".join(parts)


def export_pdf(cap, filepath_with_ext: str):
    """Write the PDF calculation report; returns its path, or None when LaTeX fails."""
    from pylatex.utils import NoEscape

    from utilities import report_style as style

    filepath = filepath_with_ext[:-4] if filepath_with_ext.lower().endswith(".pdf") \
        else filepath_with_ext
    folder = tempfile.mkdtemp(prefix="sdt_pilecap_", dir=os.path.dirname(os.path.abspath(filepath))
                              or None)
    try:
        figure = draw_plan(cap, os.path.join(folder, "plan.png"))
        size = f"{cap.Lx:.0f} x {cap.Ly:.0f} x {cap.h:.0f} mm, {cap.n} piles"
        doc = style.new_document("Pile Cap Design", "NSCP 2015", f"Pile cap: {size}",
                                 "sdt pilecap", os.path.basename(filepath) + ".pdf",
                                 packages=("float",))
        doc.append(NoEscape(style.title_block(
            "Pile Cap Design", "Flexure, shear, strut-and-tie, pile capacity and detailing", [
                ("Pile cap", size), ("Date", datetime.today().strftime("%d %B %Y").lstrip("0")),
                ("Design code", "NSCP 2015"),
                ("Prepared with", f"sdt pilecap {style.program_version()}".strip())])))
        doc.append(NoEscape(report_body(cap, os.path.relpath(
            figure, os.path.dirname(os.path.abspath(filepath))).replace("\\", "/"))))
        from utilities.latex_help import compiler_args

        latex_args = compiler_args()
        doc.generate_pdf(filepath, clean=False, clean_tex=False, compiler="pdflatex",
                         compiler_args=latex_args)
        doc.generate_pdf(filepath, clean_tex=True, compiler="pdflatex",
                         compiler_args=latex_args)
        return filepath + ".pdf"
    except Exception as error:  # noqa: BLE001 - the calculation stands without its PDF
        print(f"PDF Error: {error}")
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)
