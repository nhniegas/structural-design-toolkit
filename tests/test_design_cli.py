"""
tests/test_design_cli.py
========================
Checks for the terminal workflow of the design checks (utilities/design_cli.py)
and the uniform names every design module offers: INPUTS, calculate,
summary_text, export_pdf and run. The dialogs are replaced, so nothing opens.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design import composite_column_designer_aiscDG06 as composite  # noqa: E402
from design import general_steel_section_designer_aisc360 as steel  # noqa: E402
from design import wind_calculator_directional_asce7 as wind  # noqa: E402
from utilities import _gui_helpers, design_cli  # noqa: E402
from utilities.design_cli import Field, dialog_label, parse_inputs  # noqa: E402

MODULES = [composite, steel, wind]


def defaults(module) -> dict:
    return {field.key: field.default for field in module.INPUTS}


# --------------------------------------------------------------------------- #
# Uniform module names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_every_module_offers_the_same_names(module):
    for name in ("INPUTS", "calculate", "summary_text", "export_pdf", "run"):
        assert hasattr(module, name), name
    assert all(isinstance(field, Field) for field in module.INPUTS)


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_the_dialog_defaults_calculate_and_print(module):
    text = module.summary_text(module.calculate(defaults(module)))
    assert isinstance(text, str) and len(text.splitlines()) > 5


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_no_module_needs_excel(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "xlwings" not in source


def test_composite_summary_shows_capacity_and_interaction():
    text = composite.summary_text(composite.calculate(defaults(composite)))
    assert "phi Pn (kN)" in text
    assert "Interaction Ratio (Standard)" in text


def test_steel_blank_lengths_take_ly():
    values = defaults(steel)
    check = steel.calculate(values)
    assert check.member.Lx == check.member.Ly == check.member.Lb == values["Ly"] * 1e3


def test_steel_summary_ends_with_the_governing_ratio():
    text = steel.summary_text(steel.calculate(defaults(steel)))
    assert "W14X90" in text
    assert "PASS" in text or "FAIL" in text


def test_wind_summary_lists_both_directions():
    text = wind.summary_text(wind.calculate(defaults(wind)))
    assert "Normal Wind Direction" in text and "Parallel Wind Direction" in text


def test_wind_blank_heights_still_calculate():
    values = defaults(wind) | {"raw_heights": None}
    assert not wind.calculate(values).velocity_pressure_table.empty


# --------------------------------------------------------------------------- #
# parse_inputs / dialog_label
# --------------------------------------------------------------------------- #
FIELDS = [
    Field("P", "Axial", 1.0),
    Field("name", "Section", "W14X90", kind="text"),
    Field("method", "Method", "LRFD", kind="choice", choices=("LRFD", "ASD")),
    Field("L", "Length", None, optional=True),
]


def test_parse_inputs_converts_each_kind():
    values = parse_inputs(FIELDS, {"Axial": " 12.5 ", "Section": "W8X10", "Method": "asd",
                                   "Length": ""})
    assert values == {"P": 12.5, "name": "W8X10", "method": "ASD", "L": None}


@pytest.mark.parametrize("typed, message", [
    ({"Axial": "abc", "Section": "W", "Method": "LRFD"}, "Axial must be a number"),
    ({"Axial": "1", "Section": "", "Method": "LRFD"}, "Section is required"),
    ({"Axial": "1", "Section": "W", "Method": "X"}, "Method must be one of"),
])
def test_parse_inputs_names_the_bad_field(typed, message):
    with pytest.raises(ValueError, match=message):
        parse_inputs(FIELDS, typed)


def test_dialog_label_lists_choices_and_marks_optional():
    assert dialog_label(FIELDS[2]) == "Method [LRFD / ASD]"
    assert dialog_label(FIELDS[3]) == "Length (optional)"


# --------------------------------------------------------------------------- #
# run_design with the dialogs replaced
# --------------------------------------------------------------------------- #
@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    """Replace the dialogs; ``answers`` sets what each one returns."""
    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    answers = {"values": [], "option": None, "save": None}
    warnings = []

    def enter_values(title, prompt, labels, shown):
        return answers["values"].pop(0) if answers["values"] else None

    monkeypatch.setattr(_gui_helpers, "enter_values", enter_values)
    monkeypatch.setattr(_gui_helpers, "select_option", lambda *a, **k: answers["option"])
    monkeypatch.setattr(_gui_helpers, "select_save_file", lambda **k: answers["save"])
    monkeypatch.setattr(_gui_helpers, "show_warning", lambda message, **k: warnings.append(message))
    answers["warnings"] = warnings
    return answers


def typed_defaults(module) -> dict:
    shown = {}
    for field in module.INPUTS:
        value = field.default
        shown[dialog_label(field)] = "" if value is None else str(value)
    return shown


def test_run_prints_when_asked(dialogs, capsys):
    dialogs["values"] = [typed_defaults(composite)]
    dialogs["option"] = "Print the results in the terminal"
    assert composite.run() is not None
    assert "phi Pn (kN)" in capsys.readouterr().out


def test_run_exports_when_asked(dialogs, monkeypatch, tmp_path, capsys):
    written = []
    monkeypatch.setattr(wind, "export_pdf", lambda calc, path: written.append(path) or path)
    dialogs["values"] = [typed_defaults(wind)]
    dialogs["option"] = "Export a PDF calculation report"
    dialogs["save"] = str(tmp_path / "wind")
    wind.run()
    out = capsys.readouterr().out
    assert written == [str(tmp_path / "wind.pdf")]
    assert "Normal Wind Direction" not in out  # export only, no printout


def test_run_asks_again_after_a_bad_value(dialogs):
    bad = typed_defaults(steel) | {dialog_label(steel.INPUTS[1]): "abc"}
    dialogs["values"] = [bad, typed_defaults(steel)]
    dialogs["option"] = None
    assert steel.run() is not None
    assert len(dialogs["warnings"]) == 1 and "must be a number" in dialogs["warnings"][0]


def test_run_cancelled_returns_none(dialogs):
    assert composite.run() is None


def test_run_offers_last_inputs_again(dialogs, monkeypatch):
    first = typed_defaults(composite) | {dialog_label(composite.INPUTS[0]): "5000"}
    dialogs["values"] = [first]
    composite.run()
    seen = []
    monkeypatch.setattr(_gui_helpers, "enter_values",
                        lambda title, prompt, labels, shown: seen.append(shown) or None)
    composite.run()
    assert seen[0][dialog_label(composite.INPUTS[0])] == "5000"
