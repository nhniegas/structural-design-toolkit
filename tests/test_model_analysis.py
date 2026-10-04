"""
tests/test_model_analysis.py
============================
Checks for etabs_api/workflows/model_analysis.py that need no ETABS.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import model_analysis as ma  # noqa: E402


def test_method_a_period_uses_ct_in_ft_units():
    # hn = 100 ft, Ct = 0.030: T = 0.030 * 100^0.75 = 0.949 s
    assert ma.method_a_period(0.030, 100 * 304.8) == pytest.approx(0.030 * 100 ** 0.75)


@pytest.mark.parametrize("zone, cap", [(0.4, 1.3), (0.3, 1.4), (0.075, 1.4)])
def test_method_b_cap_depends_on_the_zone(zone, cap):
    assert ma.period_cap(zone) == cap


def test_drift_spectrum_cases_follow_the_drift_patterns(monkeypatch):
    # strength: RSAX to EQXPE (capped period); drift: RSAXD to EQXSD (uncapped)
    shears = {"EQXPE": 1000e3, "EQXSD": 500e3, "RSAX": 400e3, "RSAXD": 300e3}
    monkeypatch.setattr(ma, "seismic_static_cases", lambda c, t=ma.SEISMIC_PATTERN_TYPE:
                        ["EQXPE"] if t == ma.SEISMIC_PATTERN_TYPE else ["EQXSD"])
    monkeypatch.setattr(ma, "spectrum_cases", lambda c: {"RSAX": "X", "RSAXD": "X"})
    monkeypatch.setattr(ma, "base_shears", lambda c, cases: pd.DataFrame(
        {"FX": [shears[k] for k in cases], "FY": [0.0] * len(cases)}, index=cases))

    def scale(connector, case, factor):
        shears[case] *= factor

    monkeypatch.setattr(ma, "multiply_spectrum_scale", scale)
    result = {s.spectrum_case: s for s in ma.scale_spectrum_to_static(None, lambda: None)}
    assert result["RSAX"].static_case == "EQXPE"
    assert result["RSAXD"].static_case == "EQXSD"
    assert shears["RSAX"] == pytest.approx(1000e3)
    assert shears["RSAXD"] == pytest.approx(500e3)


def test_report_text_lists_scaling_periods_and_warnings():
    report = ma.AnalysisReport(
        model_path="M.EDB",
        scaling=[ma.ShearScaling("X", "EQXPE", 1000e3, "RSAX", 500e3, 2.0, 1000e3)],
        governing_period={"X": 1.2}, method_a=0.8, cap=1.3,
        mass_sum={"X": 0.95}, seismic_weight=10e6, reaction_weight=10e6,
        story_weights=pd.DataFrame({"Story": ["2F"], "Weight (kN)": [10000.0]}),
        warnings=["something"],
    )
    text = report.text()
    assert "RSAX 500.0 kN -> 1,000.0 kN (scale factor x 2.0000)" in text
    assert "1.3 T_A = 1.040 s" in text
    assert "Sum X: 95.0 %" in text
    assert "something" in text
