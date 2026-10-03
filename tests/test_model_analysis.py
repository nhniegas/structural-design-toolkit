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
