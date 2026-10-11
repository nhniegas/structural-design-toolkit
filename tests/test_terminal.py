"""
tests/test_terminal.py
======================
Tables and equations in the terminal (utilities/terminal.py) and the reports
the commands build with them. Nothing here needs a terminal: a report is
checked as plain text and as the text ``rich`` draws.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utilities import terminal  # noqa: E402
from utilities.terminal import Report, math  # noqa: E402


# --------------------------------------------------------------------------
# EQUATIONS
# --------------------------------------------------------------------------
@pytest.mark.parametrize("latex, text", [
    (r"\phi M_n = 0.9 \times 250", "φ Mₙ = 0.9 × 250"),
    (r"V_e = \frac{M_{pr1} + M_{pr2}}{l_n}", "Vₑ = (Mₚᵣ₁ + Mₚᵣ₂)/lₙ"),
    (r"\Delta_M = 0.7 R \Delta_S \le 0.020 h", "ΔM = 0.7 R ΔS ≤ 0.020 h"),
    (r"P_n &= F_uA_e \times10^{-3} &= 2625.5~\text{kN}", "Pₙ = FᵤAₑ ×10⁻³ = 2625.5 kN"),
    (r"\sqrt{f_c}", "√(fc)"),
    (r"\phi_c P_n, b_f / 2 t_f, V_{n,x}, M_{cx}", "φc Pₙ, bf / 2 tf, Vn,x, Mcx"),
    (r"h_n^{3/4}, x^{0.75}, a^{\alpha}", "hₙ³⁄⁴, x⁰·⁷⁵, aᵅ"),
    (r"\lambda = \frac{\xi}{1 + 50\rho}", "λ = ξ/(1 + 50ρ)"),
    (r"$\textbf{OK}$ and \%", "OK and %"),
    (r"A_g (mm$^2$)", "Ag (mm²)"),
])
def test_latex_becomes_the_symbols_of_a_terminal(latex, text):
    assert math(latex) == text


def test_a_subscript_with_no_symbol_is_written_beside_its_letter():
    """There is no small b or g below the line: Lb and Ag, as engineers type them."""
    assert math(r"L_b") == "Lb" and math(r"A_g") == "Ag"
    assert math(r"M_{ry}") == "Mᵣᵧ"


def test_an_exponent_that_is_an_expression_keeps_its_mark():
    """0.658 to the power Fy/Fe cannot be raised: without ^ it would read as a product."""
    assert math(r"0.658^{F_y/F_e} F_y") == "0.658^(Fᵧ/Fₑ) Fᵧ"


def test_what_is_still_equation_source_is_found():
    from utilities.terminal import unrendered

    assert unrendered(["φMₙ ≥ Mᵤ", "Ag = 300 × 500", "0.658^(Fᵧ/Fₑ) Fᵧ", "hₙ³⁄⁴"]) == []
    for source in (r"\delta_{max} / 2", "T_A = 0.5", "h^0.75", "q_z = K_z q", r"\phi M_n"):
        assert unrendered([source]) == [source]


def test_a_report_gives_its_equation_lines():
    out = Report("T")
    out.equations(["a = b", "c = d"]).table(["x"], [["T_A"]]).equations(["e = f"])
    assert out.equation_lines() == ["a = b", "c = d", "e = f"]


# --------------------------------------------------------------------------
# THE REPORT
# --------------------------------------------------------------------------
def report() -> Report:
    out = Report("BEAM DESIGN", ["Model: t.EDB"])
    out.heading("Beams")
    out.table(["Beam", "Mu (kN-m)", "Ratio", "Status"],
              [["2GX-1", 120.5, 0.873, "OK"], ["2GX-12", 1088.25, 1.204, "FAIL: flexure"]])
    out.equations(["φMₙ ≥ Mᵤ"], "Flexure")
    out.lines(["Two beams."])
    return out


def test_plain_text_has_every_part_with_its_columns_aligned():
    lines = report().plain().splitlines()
    assert "BEAM DESIGN" in lines and "Model: t.EDB" in lines and "--- Beams ---" in lines
    header = next(line for line in lines if line.startswith("Beam "))
    first = next(line for line in lines if line.startswith("2GX-1 "))
    second = next(line for line in lines if line.startswith("2GX-12"))
    # numbers end in the same column, under the end of their header
    assert first.index("120.50") + 6 == second.index("1,088.25") + 8 == header.index("Mu (kN-m)") + 9
    assert first.rstrip().endswith("OK") and second.rstrip().endswith("FAIL: flexure")
    assert "    φMₙ ≥ Mᵤ" in lines and "Two beams." in lines


def test_the_drawn_report_holds_the_same_content():
    drawn = report().drawn_text(100)
    for text in ("BEAM DESIGN", "Model: t.EDB", "Beams", "2GX-12", "1,088.25", "1.204",
                 "FAIL: flexure", "φMₙ ≥ Mᵤ", "Two beams."):
        assert text in drawn


def test_numbers_of_a_column_keep_the_same_decimals():
    assert terminal._cell(120.5) == "120.50" and terminal._cell(88.123456) == "88.12"
    assert terminal._cell(0.8731) == "0.873" and terminal._cell(1.2) == "1.200"
    assert terminal._cell(4.0) == "4" and terminal._cell(12) == "12"
    assert terminal._cell(float("nan")) == "" and terminal._cell(None) == ""


@pytest.mark.parametrize("text, style", [
    ("OK", "green"), ("PASS", "green"), ("FAIL", "bold red"), ("FAIL: shear", "bold red"),
    ("WARN", "yellow"), ("N/A", "dim"), ("OKAY", ""), ("2GX-1", ""), ("Failing", ""),
])
def test_only_a_status_is_coloured(text, style):
    assert terminal.status_style(text) == style


def test_an_empty_table_or_equation_block_is_left_out():
    out = Report("T").table(["a"], []).equations([]).lines([])
    assert out.blocks == []


def test_a_table_without_headers_has_no_header_line():
    text = Report("T").table(["", ""], [["Beams designed", 102]]).plain()
    assert "Beams designed  102" in text and "---  ---" not in text


def test_output_that_is_not_a_terminal_gets_plain_text(capsys, monkeypatch):
    """A file, a pipe, a test run: the report is text, with nothing drawn."""
    monkeypatch.delenv("SDT_PLAIN", raising=False)
    report().show()
    out = capsys.readouterr().out
    assert "--- Beams ---" in out and "╔" not in out and "\x1b[" not in out


def test_plain_can_be_asked_for_in_a_terminal(monkeypatch):
    class Terminal:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.delenv("SDT_PLAIN", raising=False)
    assert terminal.drawn() is True
    monkeypatch.setenv("SDT_PLAIN", "1")
    assert terminal.drawn() is False


def test_a_symbol_the_output_cannot_hold_is_replaced_not_an_error(monkeypatch):
    import io

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    terminal.write("φMₙ ≥ Mᵤ = 120")
    stream.flush()
    assert b"= 120" in raw.getvalue()


# --------------------------------------------------------------------------
# THE REPORTS OF THE COMMANDS
# --------------------------------------------------------------------------
def test_the_summary_of_a_command_is_a_report():
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt beams", r"C:\models\t34.EDB")
    summary.add("Beams designed", 102).add("Failing", 2)
    summary.fail("Deflection: 2GX-1, 2GX-2").note("A note.").file("Results", "out.xlsx")
    drawn = summary.report().drawn_text(100)
    for text in ("SUMMARY - sdt beams", "Model: t34.EDB", "Beams designed", "102",
                 "Needs attention (1)", "Deflection: 2GX-1, 2GX-2", "Note: A note.", "out.xlsx"):
        assert text in drawn
    assert "Nothing needs attention." in RunSummary("sdt check").report().plain()


def test_the_model_check_shows_the_equation_behind_a_check():
    from etabs_api.workflows import model_check as mc

    text = mc.base_shear_equation(0.44, 0.64, 1.0, 8.5, 1.176, 0.4, 1.0)
    assert text.startswith("V/W = Cᵥ I / (R T) = 0.64 × 1 / (8.5 × 1.176) = 0.0640")
    assert "≤ 2.5 Cₐ I / R = 0.1294" in text and "≥ 0.11 Cₐ I = 0.0484" in text
    assert "≥ 0.8 Z Nᵥ I / R = 0.0376" in text
    # drift: the 0.11 Ca I minimum is not part of it (NSCP 208.6.5.2)
    assert "0.11" not in mc.base_shear_equation(0.44, 0.64, 1.0, 8.5, 2.0, 0.4, 1.0, drift=True)

    findings = [mc.Finding("Seismic", mc.OK, "EQXPE: V/W 0.0640 at T 1.176 s", "NSCP 208.5.2.1",
                           equation="EQXPE: " + text),
                mc.Finding("Model", mc.WARN, "3 beams without a tag")]
    report = mc.findings_report(mc.ModelData(path="t.EDB", analysed=True), findings)
    plain = report.plain()
    assert "--- Seismic ---" in plain and "--- Model ---" in plain
    assert "    EQXPE: V/W = Cᵥ I / (R T)" in plain
    assert "NSCP 208.5.2.1" in report.drawn_text(160)


def test_a_drift_finding_carries_its_equation():
    from etabs_api.workflows import model_check as mc

    seismic = pd.DataFrame({"Name": ["EQXSD"], "R": [8.5], "TUsed": [1.2]})
    data = mc.ModelData(pattern_types={"EQXSD": mc.SEISMIC_DRIFT},
                        drifts={"DRIFT 100 x EQXSD": (0.002, "2F")}, drift_from="combinations")
    finding = mc._check_drift(data, seismic)[0]
    assert finding.equation == ("EQXSD: ΔM = 0.7 R ΔS = 0.7 × 8.5 × 0.00200 = 0.0119 ≤ "
                                "0.020 h")


def test_the_analysis_report_has_the_period_equations():
    from etabs_api.workflows.model_analysis import AnalysisReport, ShearScaling

    report = AnalysisReport(model_path="t.EDB", method_a=0.859, cap=1.3, height=26_700.0,
                            governing_period={"X": 2.322}, mass_sum={"X": 0.983},
                            seismic_weight=14_567_500.0)
    report.scaling = [ShearScaling("X", "EQXNE", 1_144_300.0, "RSAX", 561_200.0, 2.0392,
                                   1_144_300.0)]
    plain = report.report().plain()
    assert "RSAX: factor = Vstatic / Vspectrum = 1,144.3 / 561.2 = 2.0392" in plain
    assert "TA = Cₜ hₙ³⁄⁴ = 0.03 × 87.60⁰·⁷⁵ = 0.859 s" in plain
    assert "T ≤ 1.3 TA = 1.117 s" in plain
    assert "14,567.5" in plain and "98.3" in plain


def test_the_standalone_checks_have_a_terminal_report_with_equations():
    from utilities.terminal import unrendered

    from design.loads import wind_directional_asce7 as wind
    from design.steel import composite_column_aiscDG06 as composite
    from design.steel import wide_flange_aisc360 as steel

    for module, wanted in ((steel, "Pₙ"), (composite, "Pₙₒ = Fy Aₛ + 0.85 f′c"),
                           (wind, "q = 0.613 Kzt Kd Kₑ V²")):
        result = module.calculate({f.key: f.default for f in module.INPUTS})
        report = module.terminal_report(result)
        assert wanted in report.plain()
        assert report.drawn_text(140)       # it can be drawn
        assert "\\" not in report.plain()    # no LaTeX is left in what is shown
        assert unrendered(report.equation_lines()) == []
        assert not [line for line in report.plain().split("\n")
                    if "_" in line and not line.startswith("Model")], "an underscore is shown"


def test_beam_and_column_tables_list_every_member_of_a_small_model():
    from design.concrete import results_tables as rt

    beams = pd.DataFrame({
        "UniqueName": ["2GX-1", "2GX-1", "2BY-3", "2BY-3"], "Face": ["TOP", "BOTTOM"] * 2,
        "Story": ["2F"] * 4, "Width": [300.0] * 4, "Depth": [600.0, 600.0, 500.0, 500.0],
        "n_left_L1": [4, 2, 2, 2], "n_left_L2": [2, 0, 0, 0], "n_left_L3": [0] * 4,
        "n_mid_L1": [2, 3, 2, 3], "n_mid_L2": [0] * 4, "n_mid_L3": [0] * 4,
        "n_right_L1": [4, 2, 2, 2], "n_right_L2": [0] * 4, "n_right_L3": [0] * 4,
        "Stirrup_Legs": [2] * 4, "Spacing_2H": [100] * 4, "Spacing_Mid": [200] * 4,
        "Defl_ratio": [0.41, 0.41, 1.12, 1.12],
        "Design_Status": ["OK", "OK", "FAILED: DEFLECTION (ACI 24.2.2)", "OK"]})
    plain = rt.beam_results_report(beams).plain()
    row = next(line for line in plain.splitlines() if line.startswith("2GX-1"))
    assert "300 x 600" in row and "4+2 | 2 | 4" in row and "2 | 3 | 2" in row
    assert "2 legs @ 100 / 200" in row and row.rstrip().endswith("OK")
    failing = next(line for line in plain.splitlines() if line.startswith("2BY-3"))
    assert failing.rstrip().endswith("FAIL: DEFLECTION (ACI 24.2.2)")
    assert "φMₙ ≥ Mᵤ" in plain and "Vₑ = (Mpr₁ + Mpr₂) / lₙ" in plain


def test_a_large_model_lists_the_members_that_fail(monkeypatch):
    from design.concrete import results_tables as rt

    monkeypatch.setattr(rt, "MAX_ROWS", 3)
    names = [f"GF-C{n}" for n in range(1, 11)]
    table = pd.DataFrame({
        "UniqueName": names, "Story": ["GF"] * 10, "Section": ["CR_500X500_C04_G60"] * 10,
        "Longitudinal_Bars": [12] * 10, "Reinforcement_Ratio": [0.0236] * 10,
        "Flexure_Utilization": [0.5] * 10, "Shear_Utilization": [0.4] * 8 + [1.3, 1.6],
        "BCC_Ratio_X": [1.5] * 10, "BCC_Ratio_Y": [2.0] * 10,
        "Joint_Shear_Utilization_X": [0.6] * 10, "Joint_Shear_Utilization_Y": [0.7] * 10,
        "Column_Design_Status": ["PASS"] * 8 + ["FAIL", "FAIL"]})
    plain = rt.column_results_report(table).plain()
    assert "--- Columns that fail ---" in plain
    assert "GF-C9" in plain and "GF-C10" in plain and "GF-C1 " not in plain
    assert "10 members: the 2 listed fail; the 8 that pass are in the results workbook." in plain
    row = next(line for line in plain.splitlines() if line.startswith("GF-C10"))
    assert "2.36 %" in row and "1.60" in row and "1.50" in row and row.rstrip().endswith("FAIL")
    all_pass = table.assign(Column_Design_Status="PASS")
    assert "All 10 members pass" in rt.column_results_report(all_pass).plain()


def test_no_command_prints_equation_source():
    """Every equation a command shows is written with symbols: no LaTeX command,
    no underscore before a subscript, no caret before a plain exponent."""
    from design.concrete import results_tables as rt
    from etabs_api.workflows import concrete_quantities as cq
    from etabs_api.workflows import irregularity as ir
    from etabs_api.workflows import model_check as mc
    from etabs_api.workflows.model_3d import column_bar
    from etabs_api.workflows.model_analysis import AnalysisReport, ShearScaling
    from utilities.terminal import unrendered

    assert unrendered(rt.BEAM_EQUATIONS + rt.COLUMN_EQUATIONS + rt.DEFLECTION_EQUATIONS) == []

    analysis = AnalysisReport(model_path="t.EDB", method_a=0.859, cap=1.3, height=26_700.0,
                              governing_period={"X": 2.322}, mass_sum={"X": 0.983},
                              seismic_weight=14_567_500.0)
    analysis.scaling = [ShearScaling("X", "EQXNE", 1_144_300.0, "RSAX", 561_200.0, 2.0392,
                                     1_144_300.0)]
    assert unrendered(analysis.report().equation_lines()) == []

    assert unrendered([mc.base_shear_equation(0.44, 0.64, 1.0, 8.5, 1.176, 0.4, 1.0)]) == []

    order = ["RF", "4F", "3F"]
    stiff = [(s, "EQX", "X", k) for s, k in (("RF", 100e3), ("4F", 50e3), ("3F", 60e3))]
    irregularity = ir.IrregularityReport("m.EDB", cases=["EQX"], items=[
        ir.soft_story_item(stiff, order),
        ir.mass_item([("RF", 100.0), ("4F", 200.0), ("3F", 310.0)]),
        ir.geometry_item([("RF", 12e3, 10e3), ("4F", 18e3, 10e3)]),
        ir.weak_story_item([("RF", 1000.0, 8), ("4F", 700.0, 8)]),
        ir.torsion_item([("RF", "EQX", "X", 13.0, 10.0)], [("RF", "EQX", "X", 13.0, 10.0)], order),
        ir.plan_items([("RF", ir.plan_shape([[(0, 0), (9e3, 0), (9e3, 5e3), (0, 5e3)]]))])[0],
        ir.stability_item([("4F", "EQX", "X", 6e6, 6e5, 15.0, 3000.0)], True)])
    lines = irregularity.report().equation_lines()
    assert len(lines) >= 9 and unrendered(lines) == []

    column = column_bar("C", "2F", (0, 0, 0), (0, 0, 3000.0), 400.0, 400.0)
    quantities = cq.measure([column], [cq.FloorShell("S", "2F", [
        (0, 0), (6000.0, 0), (6000.0, 4000.0), (0, 4000.0)], 150.0)], ["2F"], 100.0)
    assert unrendered(cq.quantities_report(quantities).equation_lines()) == []


def test_a_symbol_the_console_cannot_encode_does_not_stop_a_command(tmp_path):
    """A report printed to a file in a Windows code page: the symbols are replaced."""
    import subprocess

    code = ("import sys; sys.path.insert(0, r'%s'); from utilities.terminal import safe_output; "
            "safe_output(); print('theta \u2264 0.10, \u03b4\u2098\u2090\u2093 \u21d2 irregular')"
            % str(Path(__file__).resolve().parents[1]))
    done = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code], capture_output=True,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "cp1252"})
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert b"irregular" in done.stdout and b"0.10" in done.stdout
