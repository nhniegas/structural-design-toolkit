"""
tests/test_calc_report.py
=========================
Checks for utilities/_calc_report.py, the shared PDF calculation report builder.

Only the text preparation is tested here; writing the PDF needs a LaTeX
install and is not run in the tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utilities._calc_report import ReportTable, Tex, _table_latex, latex_text, number  # noqa: E402


def test_special_characters_are_escaped():
    assert latex_text("G_500X600 & 50%") == r"G\_500X600 \& 50\%"


def test_sheet_symbols_become_latex():
    """Sheet values hold characters pdflatex cannot read directly."""
    assert latex_text("ϕVₙ ≥ Vᵤ") == r"$\phi$Vn $\ge$ Vu"


def test_latex_text_is_left_alone():
    assert latex_text(Tex(r"$\phi M_n$")) == r"$\phi M_n$"


def test_blank_values_show_a_dash():
    assert latex_text(None) == "--"
    assert latex_text(float("nan")) == "--"
    assert number(None) == "--"


def test_numbers_are_formatted_and_text_passes_through():
    assert number(1234.567) == "1,234.57"
    assert number(18.0, 0) == "18"
    assert number("N/A - no beam") == "N/A - no beam"


def test_table_has_one_line_per_row():
    table = ReportTable("Flexure", ["Location", Tex("$M_u$")], [["Left", "1.00"], ["Right", "2.00"]])
    text = _table_latex(table)
    assert r"\begin{tabular}{lr}" in text
    assert r"Left & 1.00 \\" in text and r"Right & 2.00 \\" in text
