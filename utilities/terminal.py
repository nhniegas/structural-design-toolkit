"""Tables and equations in the terminal.

A command builds a ``Report``: headings, tables, equations and lines of
text. ``Report.show()`` prints it. In a terminal it is drawn with ``rich``:
bordered tables with their numbers aligned, the equations set apart, and
OK / WARN / FAIL in colour. Anywhere else (a file, a pipe, a test run) it is
the same content as plain text, from ``Report.plain()``.

A terminal cannot draw LaTeX. The equations are written with the symbols a
terminal has: Greek letters, the root sign, subscripts and superscripts
(phi Mn becomes φMₙ). ``math`` turns the LaTeX of a calculation report into
that form, so one equation serves the PDF and the terminal.

``rich`` is imported only when something is printed, and nothing here is
called inside a design: the time of a design is not changed.

``SDT_PLAIN=1`` in the environment keeps plain text in a terminal too.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

STATUS_STYLES = {
    "OK": "green", "PASS": "green", "PASSED": "green",
    "FAIL": "bold red", "FAILED": "bold red", "NOT OK": "bold red", "ERROR": "bold red",
    "WARN": "yellow", "INFO": "cyan", "N/A": "dim", "ADJUSTED": "yellow",
}
WIDTH = 78


# =============================================================================
# EQUATIONS: LATEX TO THE SYMBOLS OF A TERMINAL
# =============================================================================
_SYMBOLS = {
    r"\Rightarrow": "⇒", r"\Leftrightarrow": "⇔",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\Delta": "Δ",
    r"\epsilon": "ε", r"\varepsilon": "ε", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
    r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ", r"\phi": "φ", r"\varphi": "φ",
    r"\psi": "ψ", r"\omega": "ω", r"\Omega": "Ω", r"\xi": "ξ", r"\eta": "η", r"\kappa": "κ",
    r"\Sigma": "Σ", r"\Phi": "Φ", r"\Lambda": "Λ",
    r"\times": "×", r"\cdot": "·", r"\le": "≤", r"\leq": "≤", r"\ge": "≥", r"\geq": "≥",
    r"\ne": "≠", r"\neq": "≠", r"\approx": "≈", r"\pm": "±", r"\to": "→", r"\rightarrow": "→",
    r"\infty": "∞", r"\sum": "Σ", r"\quad": "  ", r"\qquad": "    ", r"\,": " ", r"\;": " ",
    r"\:": " ", r"\!": "", r"\ ": " ", r"\%": "%", r"\_": "_", r"\&": "&", r"\$": "$",
    r"\left": "", r"\right": "", r"\displaystyle": "", r"\max": "max", r"\min": "min",
    r"\prime": "′", r"\degree": "°", r"\circ": "°", r"\ldots": "...", r"\dots": "...",
}
# (there is no small y below the line: the Greek one, ᵧ, is the usual stand-in)
_SUB = dict(zip("0123456789+-=()aehijklmnoprstuvxy", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵧ"))
_SUP = dict(zip("0123456789+-=()ni/.abcdefghjklmoprstuvwxyzαβ",
                "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ⁄·ᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵅᵝ"))
_GROUP = r"\{((?:[^{}]|\{[^{}]*\})*)\}"       # {...} with one level of braces inside


def _script(text: str, table: dict, mark: str) -> str:
    """``text`` as a subscript or a superscript.

    Unicode has no small b, c, d, f, g, q, w or z below the line, and no
    capital. A subscript with such a letter is written beside its symbol, as
    engineers type it: ``A_g`` gives ``Ag``, ``\\phi_c`` gives ``φc``. An
    exponent that cannot be raised keeps its mark, ``^(...)``, since without it
    the value would read as a product.
    """
    text = text.strip()
    if text and all(char in table for char in text):
        return "".join(table[char] for char in text)
    if mark == "_":
        return text.replace(" ", "") + _END      # see math(): a space before a next letter
    return f"{mark}{text}" if len(text) == 1 else f"{mark}({text})"


_END = "\x00"      # the end of a subscript written beside its letter


_UNRENDERED = re.compile(r"\\[A-Za-z]|[_^]\{|[^\s_]_[A-Za-z0-9(]|\^[A-Za-z0-9]")


def unrendered(lines: list[str]) -> list[str]:
    """The equation lines that still hold equation source: a LaTeX command, a
    ``_`` before a subscript or a ``^`` before a plain exponent. Empty when all
    are as a reader should see them. ``^(...)`` is allowed: an exponent that is
    itself an expression. It is for equations only: a section name or a path
    also has underscores and backslashes."""
    return [line for line in lines if _UNRENDERED.search(str(line))]


def math(latex: str) -> str:
    """LaTeX of an equation as terminal text.

    ``\\phi M_n = 0.9 \\times 250`` gives ``φMₙ = 0.9 × 250``. Fractions become
    ``(a)/(b)``, roots ``√(x)``, and what has no symbol is left readable.
    """
    text = str(latex).replace("$", "")
    text = text.replace("\\\\", " ")   # the line break of an aligned equation
    text = re.sub(r"\\(?:text|textbf|mathrm|mathbf|textit|mathit|operatorname)\s*" + _GROUP,
                  r"\1", text)
    for _ in range(4):   # nested fractions and roots, the innermost first
        text = re.sub(r"\\[dt]?frac\s*" + _GROUP + r"\s*" + _GROUP,
                      lambda m: f"({m.group(1)})/({m.group(2)})", text)
        text = re.sub(r"\\sqrt\s*" + _GROUP, lambda m: f"√({m.group(1)})", text)
    for name in sorted(_SYMBOLS, key=len, reverse=True):
        text = re.sub(re.escape(name) + (r"(?![A-Za-z])" if name[-1].isalpha() else ""),
                      lambda _m, new=_SYMBOLS[name]: new, text)
    text = text.replace("~", " ").replace("&", "").replace("\\\\", "")
    text = re.sub(r"_\s*" + _GROUP, lambda m: _script(m.group(1), _SUB, "_"), text)
    text = re.sub(r"_([A-Za-z0-9])", lambda m: _script(m.group(1), _SUB, "_"), text)
    text = re.sub(r"\^\s*" + _GROUP, lambda m: _script(m.group(1), _SUP, "^"), text)
    text = re.sub(r"\^([A-Za-z0-9′])", lambda m: _script(m.group(1), _SUP, "^"), text)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)          # a command with no symbol: its name
    text = text.replace("{", "").replace("}", "")
    # bf tf, not bftf: a subscript written beside its letter is set off from the next symbol
    text = re.sub(_END + r"(?=[A-Za-zΑ-ωφ])", " ", text).replace(_END, "")
    text = re.sub(r"\(([\w.φλΔβρ′]+)\)/", r"\1/", text)     # (a)/(b) with one term: a/(b)
    text = re.sub(r"/\(([\w.φλΔβρ′]+)\)", r"/\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


# =============================================================================
# THE REPORT
# =============================================================================
@dataclass
class Table:
    headers: list[str]
    rows: list[list]
    title: str = ""
    right: tuple = ()         # indexes of the columns set to the right (numbers are, anyway)


@dataclass
class Equations:
    lines: list[str]          # each as terminal text ("V/W = Cᵥ I / (R T) = 0.0871")
    title: str = ""


@dataclass
class Lines:
    lines: list[str]
    style: str = ""           # rich style of the lines, such as "bold red"


@dataclass
class Heading:
    text: str


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if value != value:
            return ""
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        # the same number of decimals down a column: two for sizes, three for ratios
        return f"{value:,.2f}" if abs(value) >= 10 else f"{value:.3f}"
    return str(value)


def _is_number(text: str) -> bool:
    return bool(re.fullmatch(r"[-+]?[\d,]*\.?\d+(?:[eE][-+]?\d+)?%?", text.strip()))


def status_style(text: str) -> str:
    """The colour of a cell that holds a status (``FAIL: ...`` is red), or ""."""
    upper = str(text).strip().upper()
    for status, style in STATUS_STYLES.items():
        if upper == status or upper.startswith(status + ":") or upper.startswith(status + " "):
            return style
    return ""


@dataclass
class Report:
    """What a command shows when it ends: the same content for the terminal and as text."""

    title: str
    subtitle: list[str] = field(default_factory=list)
    blocks: list = field(default_factory=list)

    def heading(self, text: str) -> "Report":
        self.blocks.append(Heading(text))
        return self

    def table(self, headers: list[str], rows: list[list], title: str = "",
              right: tuple = ()) -> "Report":
        if rows:
            self.blocks.append(Table(list(headers), [list(row) for row in rows], title,
                                     tuple(right)))
        return self

    def equations(self, lines: list[str], title: str = "") -> "Report":
        lines = [line for line in lines if line]
        if lines:
            self.blocks.append(Equations(lines, title))
        return self

    def equation_lines(self) -> list[str]:
        """Every equation line of the report, in the order shown."""
        return [line for block in self.blocks if isinstance(block, Equations)
                for line in block.lines]

    def lines(self, lines: list[str], style: str = "") -> "Report":
        lines = [line for line in lines if line is not None]
        if lines:
            self.blocks.append(Lines(lines, style))
        return self

    # ---- plain text ----
    def plain(self) -> str:
        out = ["", "=" * WIDTH, self.title, "=" * WIDTH] + list(self.subtitle)
        for block in self.blocks:
            if isinstance(block, Heading):
                out += ["", f"--- {block.text} ---"]
            elif isinstance(block, Lines):
                out += block.lines
            elif isinstance(block, Equations):
                if block.title:
                    out += ["", block.title]
                out += [f"    {line}" for line in block.lines]
            elif isinstance(block, Table):
                out += plain_table(block)
        out.append("=" * WIDTH)
        return "\n".join(out)

    # ---- the terminal ----
    def show(self) -> None:
        """Print the report: drawn with rich in a terminal, plain text elsewhere."""
        if not drawn():
            write(self.plain())
            return
        try:
            self._draw()
        except Exception:  # noqa: BLE001 - a console that cannot draw still gets the report
            write(self.plain())

    def drawn_text(self, width: int = 120) -> str:
        """The report as the terminal draws it, as text (for a look at it, and the tests)."""
        import io

        from rich.console import Console

        stream = io.StringIO()
        self._draw(Console(file=stream, width=width, force_terminal=False, highlight=False))
        return stream.getvalue()

    def _draw(self, console=None) -> None:
        from rich import box
        from rich.console import Console
        from rich.padding import Padding
        from rich.panel import Panel
        from rich.rule import Rule
        from rich.table import Table as RichTable
        from rich.text import Text

        console = console or Console(highlight=False)
        console.print()
        head = Text(self.title, style="bold")
        for line in self.subtitle:
            head.append("\n" + line, style="not bold dim")
        console.print(Panel(head, box=box.DOUBLE, expand=False, padding=(0, 2)))
        for block in self.blocks:
            if isinstance(block, Heading):
                console.print()
                console.print(Rule(Text(block.text, style="bold cyan"), align="left",
                                   style="cyan"))
            elif isinstance(block, Lines):
                for line in block.lines:
                    console.print(Text(line, style=block.style or status_style(
                        line.strip("[] ").split("]")[0])), soft_wrap=True)
            elif isinstance(block, Equations):
                body = Text("\n".join(block.lines), style="bold")
                console.print(Padding(Panel(body, title=block.title or None, title_align="left",
                                            box=box.ROUNDED, expand=False, padding=(0, 2),
                                            border_style="blue"), (0, 0, 0, 2)))
            elif isinstance(block, Table):
                cells = [[_cell(value) for value in row] for row in block.rows]
                table = RichTable(title=block.title or None, title_justify="left",
                                  box=box.SIMPLE_HEAVY, header_style="bold", pad_edge=False,
                                  title_style="bold",
                                  show_header=any(str(h).strip() for h in block.headers))
                for index, header in enumerate(block.headers):
                    numbers = [row[index] for row in cells if index < len(row) and row[index]]
                    to_right = index in block.right or (
                        bool(numbers) and all(_is_number(n) for n in numbers))
                    table.add_column(str(header), justify="right" if to_right else "left",
                                     overflow="fold")
                for row in cells:
                    row = row + [""] * (len(block.headers) - len(row))
                    table.add_row(*[Text(value, style=status_style(value)) for value in row])
                console.print(table)
        console.print()


def plain_table(table: Table) -> list[str]:
    """A table as lines of text, its columns aligned (numbers to the right)."""
    cells = [[_cell(value) for value in row] for row in table.rows]
    count = len(table.headers)
    cells = [row + [""] * (count - len(row)) for row in cells]
    widths = [max([len(str(table.headers[i]))] + [len(row[i]) for row in cells])
              for i in range(count)]
    right = [i in table.right or (any(row[i] for row in cells)
                                  and all(_is_number(row[i]) for row in cells if row[i]))
             for i in range(count)]

    def line(values) -> str:
        parts = [str(v).rjust(widths[i]) if right[i] else str(v).ljust(widths[i])
                 for i, v in enumerate(values)]
        return ("  ".join(parts)).rstrip()

    out = ["", table.title] if table.title else [""]
    if any(str(header).strip() for header in table.headers):
        out += [line(table.headers), line(["-" * w for w in widths])]
    out += [line(row) for row in cells]
    return out


# =============================================================================
# WHERE THE OUTPUT GOES
# =============================================================================
def drawn() -> bool:
    """Whether the output is a terminal that tables and colours can be drawn in."""
    if os.environ.get("SDT_PLAIN", "").strip() not in ("", "0"):
        return False
    stream = sys.stdout
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError):
        return False


def write(text: str) -> None:
    """Print text; a symbol the output cannot hold is replaced, never an error."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), flush=True)


def show_text(text: str, style: str = "") -> None:
    """A line or a block of plain text, in colour when the terminal draws."""
    if not drawn() or not style:
        write(text)
        return
    try:
        from rich.console import Console
        from rich.text import Text

        Console(highlight=False).print(Text(text, style=style), soft_wrap=True)
    except Exception:  # noqa: BLE001
        write(text)
