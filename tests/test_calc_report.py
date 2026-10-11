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


# --------------------------------------------------------------------------
# THE LOOK OF THE REPORTS (utilities/report_style.py)
# --------------------------------------------------------------------------
def test_a_result_in_a_cell_is_a_tag_and_other_text_is_left():
    from utilities._calc_report import status_cell

    assert status_cell("PASS") == r"\sdtpass{PASS}"
    assert status_cell("OK") == r"\sdtpass{OK}"
    assert status_cell("FAIL: flexure") == r"\sdtfail{FAIL}: flexure"
    assert status_cell("PASS - 3 legs needed") == r"\sdtpass{PASS} - 3 legs needed"
    assert status_cell("Passing lane") == "Passing lane"          # a word, not a result
    assert status_cell(1.25) == "1.25" and status_cell(None) == "--"
    assert status_cell(Tex(r"$\phi$")) == r"$\phi$"


def test_a_sentence_in_a_cell_is_wrapped_so_the_table_keeps_its_width():
    from utilities._calc_report import status_cell

    long = "N/A - the joint is checked in the combinations that govern it (see the other rows)"
    assert status_cell(long).startswith(r"\parbox[t]{62mm}{\raggedright")
    assert status_cell("ULS 105 1.2 DL + 1.0 LL") == "ULS 105 1.2 DL + 1.0 LL"


def test_a_table_wider_than_the_page_is_scaled_to_it():
    text = _table_latex(ReportTable("Shear", ["Zone", "Vu"], [["Left", "PASS"]]))
    assert r"\rowcolor{sdtband}" in text and r"\bfseries Zone & \bfseries Vu" in text
    assert r"\ifdim\wd\sdttable>\linewidth\resizebox{\linewidth}{!}" in text
    assert r"\sdtpass{PASS}" in text


def test_the_code_of_a_title_goes_to_the_header():
    from utilities._calc_report import split_title

    assert split_title("Concrete Beam Design Calculations (ACI 318M-14)") == (
        "Concrete Beam Design Calculations", "ACI 318M-14")
    assert split_title("Wind Loads") == ("Wind Loads", "")


def test_the_style_names_what_the_report_is_of_and_marks_a_ratio_over_its_limit():
    from utilities import report_style as style

    assert style.source_name(r"C:\jobs\t34 - Beam Design.pdf") == "t34"
    assert style.source_name("W14X90.pdf") == "W14X90"
    assert style.ratio(0.874) == "0.87" and style.ratio(1.0) == "1.00"
    assert style.ratio(1.06) == r"\sdtover{1.06}" and style.ratio("N/A") == "N/A"
    assert style.tag("PASS (OK)") == r"\sdtpass{PASS} (OK)"
    assert style.tag("FAIL") == r"\sdtfail{FAIL}" and style.tag("Compact") == "Compact"
    assert style.escape(r"B_300X500 & 50% of C:\x") == (
        r"B\_300X500 \& 50\% of C:\textbackslash{}x")


def test_the_preamble_has_the_header_the_footer_and_a_fallback_for_each_package():
    from utilities import report_style as style

    text = style.preamble("Beam Design", "ACI 318M-14", "Model: t34", "sdt beams", "t34.pdf",
                          "11 October 2026")
    for expected in ("Beam Design", "ACI 318M-14", "Model: t34", "11 October 2026",
                     "sdt beams", "t34.pdf", "Check the results independently",
                     r"\definecolor{sdtamber}{HTML}{FFAA2C}", r"Page \sdtpages"):
        assert expected in text
    # a missing package gives a plainer report, never a failed one
    for package in ("tgheros", "sansmath", "colortbl", "lastpage", "fancyhdr"):
        assert rf"\IfFileExists{{{package}.sty}}" in text
    assert style.INK == "1C1E24" and style.AMBER == "FFAA2C"       # those of the icon


def test_the_title_block_sets_its_pairs_two_to_a_line():
    from utilities import report_style as style

    text = style.title_block("Beam Design", "Special moment frame", [
        ("Model", "t34"), ("Date", "11 October 2026"), ("Design code", "ACI 318M-14"),
        ("Empty", "")])
    assert r"{\LARGE\bfseries Beam Design}" in text and "Special moment frame" in text
    assert r"Model & t34 & Date & 11 October 2026 \\ \hline" in text
    assert "Empty" not in text and r"Design code & ACI 318M-14 &  &  \\ \hline" in text
