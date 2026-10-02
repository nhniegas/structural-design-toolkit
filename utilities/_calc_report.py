"""
Single-column PDF calculation reports (PyLaTeX) shared by the beam and column designers.

A report is a title, a summary table of all members, then one section per
member holding tables of design values. No equations are written out: the
tables carry the intermediate and final values only.
"""

import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


class Tex(str):
    """Text that is already LaTeX and must not be escaped."""


@dataclass
class ReportTable:
    """One table of values. ``header`` and ``rows`` hold plain text or ``Tex``."""

    title: str
    header: list
    rows: list
    column_spec: str = ""  # LaTeX column spec; left then right-aligned by default
    note: str = ""


@dataclass
class MemberReport:
    """All tables of one member, plus its row in the summary table."""

    heading: str
    summary: list
    tables: list = field(default_factory=list)


# Characters used in sheet values and combination names that pdflatex cannot
# read directly. Anything else outside ASCII is reduced to its plain letter.
_SYMBOLS = {
    "ϕ": r"$\phi$", "φ": r"$\phi$", "ρ": r"$\rho$", "Σ": r"$\Sigma$",
    "≥": r"$\ge$", "≤": r"$\le$", "×": r"$\times$", "·": r"$\cdot$",
    "′": "'", "’": "'", "–": "--", "—": "--", "°": r"$^\circ$",
}
_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "<": r"$<$", ">": r"$>$",
}


def latex_text(value) -> str:
    """Turn any cell value into text that is safe inside a LaTeX table."""
    if isinstance(value, Tex):
        return str(value)
    if is_blank(value):
        return "--"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    pieces = []
    for character in str(value):
        if character in _SYMBOLS:
            pieces.append(_SYMBOLS[character])
        elif character in _ESCAPES:
            pieces.append(_ESCAPES[character])
        elif ord(character) < 128:
            pieces.append(character)
        else:
            plain = unicodedata.normalize("NFKD", character)
            pieces.append("".join(c for c in plain if ord(c) < 128))
    return "".join(pieces)


def is_blank(value) -> bool:
    """True for None, NaN and empty text."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def number(value, decimals: int = 2) -> str:
    """Format a number for the report; text (such as 'N/A') is passed through."""
    if is_blank(value):
        return "--"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _table_latex(table: ReportTable) -> str:
    """LaTeX for one table, kept on one page together with its title."""
    width = len(table.header)
    spec = table.column_spec or ("l" + "r" * (width - 1))
    lines = [
        r"\noindent\begin{minipage}{\textwidth}",
        rf"\textbf{{{latex_text(table.title)}}}\\[0.1cm]",
        rf"{{\small\setlength{{\tabcolsep}}{{4pt}}\begin{{tabular}}{{{spec}}}",
        r"\toprule",
        " & ".join(latex_text(cell) for cell in table.header) + r" \\",
        r"\midrule",
    ]
    for row in table.rows:
        lines.append(" & ".join(latex_text(cell) for cell in row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}"]
    if table.note:
        lines.append(rf"\\[0.1cm]{{\footnotesize {latex_text(table.note)}}}")
    lines += [r"\end{minipage}", r"\par\vspace{0.35cm}", ""]
    return "\n".join(lines)


def build_calc_report(
    title: str,
    information: list,
    summary_header: list,
    members: list,
    filepath: str,
    summary_spec: str = "",
) -> str | None:
    """Write the A4 PDF and return its path, or None when LaTeX fails.

    ``information`` is a list of ``(label, value)`` shown under the title.
    ``summary_spec`` should use fixed-width ``p{..}`` columns: the summary runs
    over several pages and LaTeX is run once, so widths cannot be measured.
    ``filepath`` may be given with or without the ``.pdf`` extension.
    """
    from pylatex import Document, Package, Section
    from pylatex.utils import NoEscape

    if filepath.lower().endswith(".pdf"):
        filepath = filepath[:-4]

    doc = Document(geometry_options={"a4paper": True, "margin": "0.5in"})
    doc.packages.append(Package("booktabs"))
    doc.packages.append(Package("amsmath"))
    doc.packages.append(Package("longtable"))
    doc.preamble.append(NoEscape(r"\pagestyle{plain}"))
    doc.preamble.append(NoEscape(r"\setlength{\parindent}{0pt}"))
    doc.preamble.append(NoEscape(r"\setlength{\LTleft}{0pt}"))

    doc.append(NoEscape(r"\begin{center}"))
    doc.append(NoEscape(rf"{{\LARGE \textbf{{{latex_text(title)}}}}}\\[0.35cm]"))
    doc.append(
        NoEscape(r"{\normalsize " + datetime.today().strftime("%B %d, %Y") + r"}")
    )
    doc.append(NoEscape(r"\end{center}"))
    doc.append(NoEscape(r"\vspace{0.4cm}"))

    with doc.create(Section("Design Basis")):
        rows = [[label, value] for label, value in information]
        doc.append(NoEscape(_table_latex(
            ReportTable("Parameters used for this report", ["Parameter", "Value"], rows, "ll")
        )))

    with doc.create(Section("Summary of Results")):
        spec = summary_spec or ("l" * len(summary_header))
        lines = [
            rf"{{\small\setlength{{\tabcolsep}}{{4pt}}\begin{{longtable}}{{{spec}}}",
            r"\toprule",
            " & ".join(latex_text(cell) for cell in summary_header) + r" \\",
            r"\midrule",
            r"\endhead",
            r"\bottomrule",
            r"\endfoot",
        ]
        for member in members:
            lines.append(" & ".join(latex_text(cell) for cell in member.summary) + r" \\")
        lines.append(r"\end{longtable}}")
        doc.append(NoEscape("\n".join(lines)))

    for member in members:
        with doc.create(Section(NoEscape(latex_text(member.heading)))):
            for table in member.tables:
                doc.append(NoEscape(_table_latex(table)))

    try:
        doc.generate_pdf(filepath, clean_tex=True, compiler="pdflatex")
        return filepath + ".pdf"
    except Exception as error:
        print(f"PDF Error: {error}")
        return None
