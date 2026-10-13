"""The look of the PDF calculation reports, shared by every command that writes one.

A report has, on every page, a header with the program's mark, the title, the
design code, what it was made from and the page number over an amber rule, and
a footer with the version and the file. Its text and equations are in one sans
typeface, in the graphite of the program; section titles sit on a grey band,
tables have a grey head and light rules, and a result is a ratio with a PASS or
FAIL tag.

``new_document`` gives a PyLaTeX document set up this way; ``title_block``,
``tag`` and ``ratio`` give its pieces. A missing LaTeX package never stops a
report: without the sans font, the header package or the table colours, the
report is written plainer. "Page x of y" needs no package.
"""

from __future__ import annotations

import os
from datetime import datetime

# The graphite and amber of the icon (tools/make_icon.py), and the greys of a page.
INK, AMBER = "1C1E24", "FFAA2C"
SOFT, LINE, BAND = "6B7280", "D5D8DE", "F3F4F6"
PASS_TEXT, PASS_FILL = "1E7A34", "E4F4E7"
FAIL_TEXT, FAIL_FILL = "B3261E", "FBE6E4"
WARN_TEXT, WARN_FILL = "9A5B00", "FFF1D6"

MARGINS = {"a4paper": True, "left": "18mm", "right": "18mm", "top": "30mm", "bottom": "22mm",
           "headheight": "17mm", "headsep": "7mm", "footskip": "11mm"}
PASSING = ("PASS", "OK", "PASSED", "ADEQUATE")
FAILING = ("FAIL", "NOT OK", "NG", "OVERSTRESSED", "INADEQUATE")


def escape(text) -> str:
    """Plain text made safe for LaTeX (the few characters a title or a file name has)."""
    out = str(text)
    for old, new in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
                     ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
                     ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")):
        if old == "\\":
            out = out.replace(old, "\x00")
        else:
            out = out.replace(old, new)
    return out.replace("\x00", r"\textbackslash{}")


def program_version() -> str:
    try:
        from main import VERSION

        return str(VERSION)
    except Exception:  # noqa: BLE001 - a report can be written without the command line
        return ""


def preamble(title: str, code: str = "", source: str = "", command: str = "",
             file_name: str = "", date: str = "") -> str:
    """The LaTeX preamble of a report: fonts, colours, the header and footer, and
    the commands the report is written with."""
    date = date or datetime.today().strftime("%d %B %Y").lstrip("0")
    version = program_version()
    left_foot = r"\ \ $\cdot$\ \ ".join(escape(part) for part in (
        f"Structural Design Toolkit {version}".strip(), command, file_name) if part)
    right_head = r"\ \ $\cdot$\ \ ".join(escape(part) for part in (source, date) if part)
    return r"""
\usepackage[T1]{fontenc}
\IfFileExists{tgheros.sty}{\usepackage{tgheros}}{\usepackage{helvet}}
\renewcommand{\familydefault}{\sfdefault}
\IfFileExists{sansmath.sty}{\usepackage{sansmath}\sansmath}{}
\usepackage{xcolor,array}
\IfFileExists{colortbl.sty}{\usepackage{colortbl}}{%
  \providecommand{\rowcolor}[1]{}\providecommand{\columncolor}[1]{}%
  \providecommand{\arrayrulecolor}[1]{}}
\definecolor{sdtink}{HTML}{""" + INK + r"""}\definecolor{sdtamber}{HTML}{""" + AMBER + r"""}
\definecolor{sdtsoft}{HTML}{""" + SOFT + r"""}\definecolor{sdtline}{HTML}{""" + LINE + r"""}
\definecolor{sdtband}{HTML}{""" + BAND + r"""}
\definecolor{sdtokfg}{HTML}{""" + PASS_TEXT + r"""}\definecolor{sdtokbg}{HTML}{""" + PASS_FILL + r"""}
\definecolor{sdtnofg}{HTML}{""" + FAIL_TEXT + r"""}\definecolor{sdtnobg}{HTML}{""" + FAIL_FILL + r"""}
\definecolor{sdtwarnfg}{HTML}{""" + WARN_TEXT + r"""}\definecolor{sdtwarnbg}{HTML}{""" + WARN_FILL + r"""}
\color{sdtink}
\setlength{\parindent}{0pt}
\renewcommand{\arraystretch}{1.2}
\setlength{\arrayrulewidth}{0.4pt}
\arrayrulecolor{sdtline}
\newcommand{\sdtmark}{\colorbox{sdtink}{\textcolor{sdtamber}{\bfseries\small\,SDT\,}}}
\newcommand{\sdtpass}[1]{\colorbox{sdtokbg}{\textcolor{sdtokfg}{\bfseries\scriptsize\strut\ #1\ }}}
\newcommand{\sdtfail}[1]{\colorbox{sdtnobg}{\textcolor{sdtnofg}{\bfseries\scriptsize\strut\ #1\ }}}
\newcommand{\sdtwarn}[1]{\colorbox{sdtwarnbg}{\textcolor{sdtwarnfg}{\bfseries\scriptsize\strut\ #1\ }}}
\newcommand{\sdtsoft}[1]{\textcolor{sdtsoft}{#1}}
\newcommand{\sdtover}[1]{\textcolor{sdtnofg}{\bfseries #1}}
% a section title on a grey band with an amber mark, as wide as the line it is on
\newcommand{\sdtband}[1]{\par\vspace{3mm}%
  {\setlength{\fboxsep}{2mm}\noindent\colorbox{sdtband}{\parbox{\dimexpr\linewidth-4mm}{%
  \textcolor{sdtamber}{\rule[-0.6mm]{1.1mm}{3.6mm}}\hspace{2mm}\bfseries #1}}}\par\nopagebreak\vspace{2mm}}
\newcommand{\sdtsub}[1]{\par\vspace{2.5mm}{\bfseries\small #1}\par\nopagebreak\vspace{0.8mm}%
  {\color{sdtline}\hrule height 0.4pt}\nopagebreak\vspace{1.6mm}}
\makeatletter
\renewcommand{\section}{\@ifstar{\sdtband}{\sdt@section}}
\newcommand{\sdt@section}[1]{\refstepcounter{section}\sdtband{\thesection\quad #1}}
\renewcommand{\subsection}{\@ifstar{\sdtsub}{\sdt@subsection}}
\newcommand{\sdt@subsection}[1]{\refstepcounter{subsection}\sdtsub{#1}}
\makeatother
\newsavebox{\sdttable}
% lists set close: a summary of five lines is not half a page
\let\sdtitemize\itemize
\renewcommand{\itemize}{\sdtitemize\setlength{\itemsep}{1pt}\setlength{\parskip}{0pt}\setlength{\parsep}{0pt}}
% the tables of the standalone reports are written with the three rules of booktabs:
% the first becomes the grey band of the head, the others light lines
\renewcommand{\toprule}{\rowcolor{sdtband}}
\renewcommand{\midrule}{\hline}
\renewcommand{\bottomrule}{\hline}
% "Page x of y" with no package: the number of the last page is written to the
% .aux file at the end, and read on the second pass
\makeatletter
\newcommand{\sdtpages}{\thepage\ of \@ifundefined{r@sdtlastpage}{\thepage}{\pageref{sdtlastpage}}}
\AtEndDocument{\clearpage\addtocounter{page}{-1}%
  \immediate\write\@auxout{\string\newlabel{sdtlastpage}{{}{\thepage}}}\addtocounter{page}{1}}
\makeatother
\IfFileExists{fancyhdr.sty}{\usepackage{fancyhdr}\pagestyle{fancy}\fancyhf{}%
  \renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0pt}%
  \fancyhead[C]{\parbox{\textwidth}{%
    \sdtmark\ \ {\bfseries """ + escape(title) + r"""}\hfill{\small Page \sdtpages}\\[0.6mm]
    {\footnotesize\sdtsoft{""" + escape(code) + r"""}}\hfill{\footnotesize\sdtsoft{""" + right_head + r"""}}\\[0.8mm]
    {\color{sdtamber}\rule{\textwidth}{1.1pt}}}}%
  \fancyfoot[C]{\parbox{\textwidth}{{\color{sdtline}\rule{\textwidth}{0.4pt}}\\[0.6mm]
    {\scriptsize\sdtsoft{""" + left_foot + r"""}}\hfill
    {\scriptsize\sdtsoft{A calculation aid. Check the results independently before use.}}}}%
}{\pagestyle{plain}}
"""


def new_document(title: str, code: str = "", source: str = "", command: str = "",
                 file_name: str = "", date: str = "", documentclass: str = "article",
                 font_size: int = 10, packages: tuple[str, ...] = (),
                 default_filepath: str = "default_filepath"):
    """A PyLaTeX document with the look of the reports. ``title`` and ``code`` go
    in the header of every page, ``source`` (the model or the member) at its
    right, ``command`` and ``file_name`` in the footer."""
    from pylatex import Document, Package
    from pylatex.utils import NoEscape

    doc = Document(default_filepath, documentclass=documentclass,
                   document_options=[f"{font_size}pt"],
                   geometry_options=dict(MARGINS), lmodern=False, textcomp=False,
                   fontenc=None, inputenc="utf8", page_numbers=True)
    for name in ("booktabs", "amsmath", "longtable", "graphicx", *packages):
        doc.packages.append(Package(name))
    doc.preamble.append(NoEscape(preamble(title, code, source, command, file_name, date)))
    return doc


def title_block(title: str, subtitle: str = "", rows: list[tuple[str, str]] | None = None) -> str:
    """The opening of a report: its title, a line under it, and a ruled block of
    (label, value) pairs set two to a line."""
    lines = [r"{\LARGE\bfseries " + escape(title) + r"}\par"]
    if subtitle:
        lines.append(r"\vspace{1mm}{\large\sdtsoft{" + escape(subtitle) + r"}}\par")
    rows = [(label, value) for label, value in (rows or []) if str(value).strip()]
    if rows:
        if len(rows) % 2:
            rows.append(("", ""))
        label = r">{\columncolor{sdtband}\leavevmode\small\color{sdtsoft}}p{27mm}"
        value = (r">{\raggedright\arraybackslash}"
                 r"p{\dimexpr0.5\textwidth-27mm-4\tabcolsep-1.5\arrayrulewidth}")
        lines.append(r"\vspace{4mm}\noindent\begin{tabular}{|" + f"{label}|{value}|{label}|{value}|"
                     + r"}\hline")
        for index in range(0, len(rows), 2):
            cells = [escape(text) for pair in rows[index:index + 2] for text in pair]
            lines.append(" & ".join(cells) + r" \\ \hline")
        lines.append(r"\end{tabular}\par")
    lines.append(r"\vspace{1mm}")
    return "\n".join(lines)


def tag(status) -> str:
    """A result as a tag: green for a pass, red for a fail, amber for the rest.
    Text that is not a status is returned as it is."""
    text = str(status).strip()
    first = text.upper().split(":")[0].split(" - ")[0].split("(")[0].strip()
    if first in PASSING:
        return r"\sdtpass{" + escape(first) + "}" + _rest(text, first)
    if first in FAILING:
        return r"\sdtfail{" + escape(first) + "}" + _rest(text, first)
    if first in ("WARN", "WARNING", "CHECK", "N/A"):
        return r"\sdtwarn{" + escape(first) + "}" + _rest(text, first)
    return escape(text)


def _rest(text: str, first: str) -> str:
    rest = text[len(first):].strip()
    if not rest:
        return ""
    return ("" if rest[0] in ":,;." else " ") + escape(rest)


def ratio(value, limit: float = 1.0, decimals: int = 2) -> str:
    """A demand over capacity ratio: the number, in red when it is over ``limit``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return escape(value)
    text = f"{number:.{decimals}f}"
    return r"\sdtover{" + text + "}" if number > limit + 1e-9 else text


def source_name(path: str) -> str:
    """What a report file is of, from its name: ``t34 - Beam Design.pdf`` is of ``t34``."""
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    return stem.split(" - ")[0] if " - " in stem else stem
