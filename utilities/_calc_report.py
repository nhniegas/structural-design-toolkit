"""
Single-column PDF calculation reports (PyLaTeX) shared by the beam and column designers.

A report is a title block, the design basis, a summary table of all members,
then one section per member holding tables of design values. No equations are
written out: the tables carry the intermediate and final values only. The look
(header, footer, typeface, bands, tags) is that of ``utilities/report_style.py``.
"""

import math
import os
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
    # (image path relative to the PDF's folder, caption), shown after the tables
    figures: list = field(default_factory=list)


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


LONG_CELL = 46        # characters: a cell longer than this is wrapped
WRAP_WIDTH = 62       # mm, the width it is wrapped to


def status_cell(value) -> str:
    """A table cell: a result (PASS, FAIL, OK, with or without its reason) as a
    tag, anything else as safe text."""
    from utilities import report_style as style

    if isinstance(value, Tex) or is_blank(value) or isinstance(value, bool):
        return latex_text(value)
    text = str(value).strip()
    first = text.upper().split(":")[0].split(" - ")[0].split("(")[0].strip()
    if first in style.PASSING:
        return r"\sdtpass{" + latex_text(first) + "}" + _after(text, first)
    if first in style.FAILING:
        return r"\sdtfail{" + latex_text(first) + "}" + _after(text, first)
    if len(text) > LONG_CELL:      # a sentence in a cell: wrapped, so the table keeps its width
        return (r"\parbox[t]{" + f"{WRAP_WIDTH}mm" + r"}{\raggedright\strut "
                + latex_text(value) + r"\strut}")
    return latex_text(value)


def _after(text: str, first: str) -> str:
    rest = text[len(first):].strip()
    if not rest:
        return ""
    return ("" if rest[0] in ":,;." else " ") + latex_text(rest)


def _table_latex(table: ReportTable) -> str:
    """LaTeX for one table, kept on one page together with its title: the title
    in bold, the head on a grey band, a light rule under each row."""
    width = len(table.header)
    spec = table.column_spec or ("l" + "r" * (width - 1))
    lines = [
        r"\noindent\begin{minipage}{\textwidth}",
        rf"{{\bfseries\small {latex_text(table.title)}}}\\[0.12cm]",
        # set in a box first: a table wider than the page is scaled down to fit it
        rf"{{\small\setlength{{\tabcolsep}}{{5pt}}\sbox{{\sdttable}}{{\begin{{tabular}}{{{spec}}}",
        r"\rowcolor{sdtband}",
        " & ".join(r"\bfseries " + latex_text(cell) for cell in table.header) + r" \\",
    ]
    for index, row in enumerate(table.rows):
        end = r" \\" if index == len(table.rows) - 1 else r" \\ \hline"
        lines.append(" & ".join(status_cell(cell) for cell in row) + end)
    lines += [r"\end{tabular}}%",
              r"\ifdim\wd\sdttable>\linewidth\resizebox{\linewidth}{!}{\usebox{\sdttable}}"
              r"\else\usebox{\sdttable}\fi}"]
    if table.note:
        lines.append(rf"\\[0.1cm]{{\footnotesize\sdtsoft{{{latex_text(table.note)}}}}}")
    lines += [r"\end{minipage}", r"\par\vspace{0.35cm}", ""]
    return "\n".join(lines)


def _figure_latex(image: str, caption: str) -> str:
    """LaTeX for one centred image and its caption (path relative to the PDF folder)."""
    path = str(image).replace("\\", "/")
    return "\n".join([
        r"\begin{center}",
        r"\includegraphics[width=0.78\textwidth]{" + path + "}",
        r"\\[0.1cm]{\footnotesize\sdtsoft{" + latex_text(caption) + "}}",
        r"\end{center}",
        "",
    ])


def split_title(title: str) -> tuple[str, str]:
    """``Concrete Beam Design Calculations (ACI 318M-14)`` as its title and its code."""
    text = str(title).strip()
    if text.endswith(")") and " (" in text:
        head, _, code = text.rpartition(" (")
        return head.strip(), code[:-1].strip()
    return text, ""


def _basis_latex(information: list) -> str:
    """The design basis: label and value pairs, two to a line."""
    pairs = [(latex_text(label), latex_text(value)) for label, value in information]
    if len(pairs) % 2:
        pairs.append(("", ""))
    label = r">{\leavevmode\color{sdtsoft}}p{42mm}"
    value = r">{\raggedright\arraybackslash}p{\dimexpr0.5\textwidth-42mm-4\tabcolsep}"
    lines = [r"\noindent{\small\begin{tabular}{@{}" + f"{label}{value}{label}{value}" + r"@{}}"]
    for index in range(0, len(pairs), 2):
        (a, b), (c, d) = pairs[index], pairs[index + 1]
        lines.append(f"{a} & {b} & {c} & {d}" + r" \\")
    lines.append(r"\end{tabular}}\par")
    return "\n".join(lines)


def build_calc_report(
    title: str,
    information: list,
    summary_header: list,
    members: list,
    filepath: str,
    summary_spec: str = "",
    command: str = "",
) -> str | None:
    """Write the A4 PDF and return its path, or None when LaTeX fails.

    ``title`` may end with its design code in brackets; the code then goes in
    the header of every page. ``information`` is a list of ``(label, value)``
    shown as the design basis. ``summary_spec`` should use fixed-width
    ``p{..}`` columns: the summary runs over several pages and LaTeX is run
    once for it, so widths cannot be measured. ``command`` (``sdt beams``) is
    named in the footer. ``filepath`` may be given with or without ``.pdf``.
    """
    from pylatex import Section
    from pylatex.utils import NoEscape

    from utilities import report_style as style

    if filepath.lower().endswith(".pdf"):
        filepath = filepath[:-4]
    heading, code = split_title(title)
    source = style.source_name(filepath)
    doc = style.new_document(heading, code, f"Model: {source}", command,
                             os.path.basename(filepath) + ".pdf")
    doc.preamble.append(NoEscape(r"\setlength{\LTleft}{0pt}\setlength{\LTpre}{0pt}"))
    date = datetime.today().strftime("%d %B %Y").lstrip("0")
    doc.append(NoEscape(style.title_block(heading, "", [
        ("Model", source), ("Date", date), ("Design code", code),
        ("Prepared with", f"{command or 'Structural Design Toolkit'} "
                          f"{style.program_version()}".strip())])))

    with doc.create(Section("Design basis")):
        doc.append(NoEscape(_basis_latex(information)))

    with doc.create(Section("Summary of results")):
        spec = summary_spec or ("l" * len(summary_header))
        head = " & ".join(r"\bfseries " + latex_text(cell) for cell in summary_header) + r" \\"
        lines = [
            rf"{{\small\setlength{{\tabcolsep}}{{5pt}}\begin{{longtable}}{{@{{}}{spec}@{{}}}}",
            r"\rowcolor{sdtband}", head, r"\endhead",
        ]
        for member in members:
            lines.append(" & ".join(status_cell(cell) for cell in member.summary)
                         + r" \\ \hline")
        lines.append(r"\end{longtable}}")
        doc.append(NoEscape("\n".join(lines)))

    for member in members:
        doc.append(NoEscape(r"\sdtband{" + latex_text(member.heading) + "}"))
        for table in member.tables:
            doc.append(NoEscape(_table_latex(table)))
        for image, caption in member.figures:
            doc.append(NoEscape(_figure_latex(image, caption)))

    try:
        # twice: the header says "Page x of y", which LaTeX knows after one pass
        doc.generate_pdf(filepath, clean=False, clean_tex=False, compiler="pdflatex")
        doc.generate_pdf(filepath, clean_tex=True, compiler="pdflatex")
        return filepath + ".pdf"
    except Exception as error:
        print(f"PDF Error: {error}")
        return None
