"""Girders with a clear span under 4d can be designed without the SMRF rules."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_beam_designer_aci318 as beam_tests  # noqa: E402

beam = beam_tests.beam


def test_a_span_is_short_below_four_times_the_effective_depth():
    # 600 deep, 40 cover, 10 stirrup, 25 bar: d = 537.5, 4d = 2150
    assert beam.short_span_girder(2100, 600, 40, 10, 25)
    assert not beam.short_span_girder(2200, 600, 40, 10, 25)


@pytest.mark.parametrize("status, span, exempt, expected", [
    ("Supported Both Ends", 2000, False, beam.SMRF_APPLIED),      # the default: as before
    ("Supported Both Ends", 2000, True, beam.SMRF_SHORT_SPAN),
    ("Supported Both Ends", 6000, True, beam.SMRF_APPLIED),       # an ordinary girder
    ("Cantilever (Free at PtJ)", 1500, True, beam.SMRF_APPLIED),  # a cantilever keeps its rules
    (beam.GRAVITY_BEAM_STATUS, 2000, False, beam.SMRF_GRAVITY),   # never part of the frame
    (beam.GRAVITY_BEAM_STATUS, 6000, True, beam.SMRF_GRAVITY),
])
def test_which_beams_get_the_smrf_rules(status, span, exempt, expected):
    assert beam.seismic_rules_of(status, span, 600, 40, 10, 25, exempt) == expected


def design(span: float, exempt: bool) -> pd.Series:
    props = beam_tests._mock_beam_properties()
    forces = beam_tests._mock_force_table(span=span)
    results = beam.execute_beam_design(props, forces, True, "GRAV", exempt_short_spans=exempt)
    return results[results["Face"] == "TOP"].iloc[0]


def test_a_short_girder_keeps_the_smrf_rules_unless_the_engineer_leaves_it_out():
    kept, left_out = design(2000, False), design(2000, True)
    assert "Seismic_Rules" not in kept.index              # the column shows only with the option
    assert kept["SMRF_Flexure_Ratio_Check"] != "N/A" and kept["V_sway_max_kN"] > 0
    assert left_out["Seismic_Rules"] == beam.SMRF_SHORT_SPAN
    assert left_out["SMRF_Flexure_Ratio_Check"] == "N/A" and left_out["SMRF_Rho_Check"] == "N/A"
    assert left_out["V_sway_max_kN"] == 0                 # no probable-moment shear
    assert left_out["Ve_left_kN"] < kept["Ve_left_kN"]
    assert not beam.smrf_applies(left_out) and beam.smrf_applies(kept)


def test_an_ordinary_girder_is_designed_the_same_with_the_option_on():
    off, on = design(6000, False), design(6000, True)
    assert on["Seismic_Rules"] == beam.SMRF_APPLIED and beam.smrf_applies(on)
    same = ["SMRF_Flexure_Ratio_Check", "SMRF_Rho_Check", "V_sway_max_kN", "Ve_left_kN",
            "Spacing_2H", "Spacing_Mid", "n_left_L1", "n_mid_L1", "n_right_L1", "Design_Status"]
    assert [on[c] for c in same] == [off[c] for c in same]


def test_a_gravity_beam_never_counts_as_designed_with_the_smrf_rules():
    assert not beam.smrf_applies({"SupportStatus": beam.GRAVITY_BEAM_STATUS})
    assert beam.smrf_applies({"SupportStatus": "Supported Both Ends"})   # results without the option


def test_the_question_defaults_to_keeping_the_rules_and_remembers_the_last_answer(monkeypatch):
    from design import concrete_workflow as cw
    from utilities import _gui_helpers as gui

    monkeypatch.setattr(gui, "select_option",
                        lambda title, prompt, options, default_index=0: options[default_index])
    assert cw.ask_short_span_girders("t") is False
    assert cw.ask_short_span_girders("t", {"exempt_short_spans": True}) is True
    monkeypatch.setattr(gui, "select_option", lambda *a, **k: None)
    assert cw.ask_short_span_girders("t") is None


def test_the_summary_lists_the_girders_left_out_and_says_what_is_not_covered():
    from design import concrete_workflow as cw
    from utilities.run_summary import RunSummary

    results = pd.DataFrame({"UniqueName": ["G1", "G1", "G2", "G2"],
                            "Seismic_Rules": [beam.SMRF_SHORT_SPAN] * 2 + [beam.SMRF_APPLIED] * 2})
    assert cw.short_span_members(results) == ["G1"]
    summary = RunSummary("sdt beams", "m.EDB")
    cw.add_short_spans_to(summary, results, True)
    text = summary.text()
    assert "designed without the SMRF rules: G1" in text and "ACI 9.9" in text

    quiet = RunSummary("sdt beams", "m.EDB")
    cw.add_short_spans_to(quiet, results, False)             # the option off: nothing is added
    assert "SMRF rules" not in quiet.text()

    none = RunSummary("sdt beams", "m.EDB")
    cw.add_short_spans_to(none, results.iloc[2:], True)
    assert "none found" in none.text()


def test_the_loop_does_not_hold_a_girder_left_out_to_the_smrf_steel_limit():
    from etabs_api.workflows import design_loop as dl

    row = {"SupportStatus": "Supported Both Ends", "Seismic_Rules": beam.SMRF_SHORT_SPAN,
           "f'c": 28.0, "fy": 414.0, "Width": 300.0, "Depth": 600.0, "cc": 40.0}
    assert not beam.smrf_applies(row)
    assert dl.SMRF_RHO_LIMIT == 0.025
