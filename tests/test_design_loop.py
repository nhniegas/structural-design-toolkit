"""
tests/test_design_loop.py
=========================
Checks for the resizing decisions of etabs_api/workflows/design_loop.py.
No ETABS or Excel is needed.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows.design_loop import (  # noqa: E402
    LoopSettings,
    beam_actions,
    column_actions,
    column_needs,
    uls_combinations,
)
from etabs_api.workflows.sections import Limits, Section  # noqa: E402

RANGES = {
    "G": {"width": [300, 600, 100], "depth": [500, 1000, 100]},
    "B": {"width": [200, 400, 100], "depth": [400, 800, 100]},
    "CR": {"size": [400, 1000, 100]},
}
SETTINGS = LoopSettings(combos=[], limits=Limits(), ranges=RANGES)


def g(width, depth):
    return Section("G", width, depth, "C05", "G60")


def cr(width, depth):
    return Section("CR", width, depth, "C05", "G60")


def beam_rows(name, status="OK", top=(2, 0, 0), bottom=(0, 0, 2), width=300, depth=500,
              support="Supported Both Ends", vu=50.0, defl=0.2):
    rows = []
    for face, counts in (("TOP", top), ("BOTTOM", bottom)):
        row = {"UniqueName": name, "Face": face, "Design_Status": status, "Width": width,
               "Depth": depth, "f'c": 34.47, "fy": 413.69, "cc": 40, "ds": 10, "dm": 20,
               "SupportStatus": support, "Vu_left": vu, "Vu_right": vu, "Vu_mid_2h": vu / 2,
               "Defl_ratio": defl}
        for zone in ("left", "mid", "right"):
            for k, n in zip((1, 2, 3), counts):
                row[f"n_{zone}_L{k}"] = n
        rows.append(row)
    return rows


def test_failing_beams_grow_by_their_failure():
    results = pd.DataFrame(
        beam_rows("2GX-1", "FAILED: MAX BARS EXCEEDED (>3 LAYERS)")
        + beam_rows("2GY-1", "FAILED: SHEAR SPACING < 75mm")
        + beam_rows("2GX-5", "FAILED: DEFLECTION (ACI 24.2.2)"))
    sections = {n: g(300, 500) for n in ("2GX-1", "2GY-1", "2GX-5")}
    lengths = {n: 6000.0 for n in sections}
    actions = beam_actions(results, sections, lengths, set(), SETTINGS)
    assert actions["2GX-1"][0] == g(300, 600)
    assert actions["2GY-1"][0] == g(400, 500)
    assert actions["2GX-5"][0] == g(300, 600)


def test_a_beam_line_shares_the_size_unless_the_lengths_differ():
    results = pd.DataFrame(beam_rows("2GX-3", "FAILED: MAX BARS EXCEEDED (>3 LAYERS)")
                           + beam_rows("2GX-3A") + beam_rows("2GX-3B"))
    sections = {n: g(300, 500) for n in ("2GX-3", "2GX-3A", "2GX-3B")}
    lengths = {"2GX-3": 6000.0, "2GX-3A": 6500.0, "2GX-3B": 3000.0}
    actions = beam_actions(results, sections, lengths, set(), SETTINGS, allow_shrink=False)
    assert actions["2GX-3"][0] == g(300, 600)
    assert actions["2GX-3A"][0] == g(300, 600)  # within 30 %
    assert "2GX-3B" not in actions              # half the length: sized on its own


def test_a_comfortable_beam_shrinks_but_not_below_l_over_16():
    results = pd.DataFrame(beam_rows("2GX-7", width=300, depth=700))
    sections = {"2GX-7": g(300, 700)}
    shrunk = beam_actions(results, sections, {"2GX-7": 6000.0}, set(), SETTINGS)
    assert shrunk["2GX-7"][0] == g(300, 600)
    limited = beam_actions(results, sections, {"2GX-7": 11000.0}, set(), SETTINGS)
    assert "2GX-7" not in limited  # 600 < 11000 / 16


def test_a_beam_that_grew_is_not_shrunk_again():
    results = pd.DataFrame(beam_rows("2GX-7", width=300, depth=700))
    actions = beam_actions(results, {"2GX-7": g(300, 700)}, {"2GX-7": 6000.0}, {"2GX-7"},
                           SETTINGS)
    assert actions == {}


def test_a_busy_beam_is_not_shrunk():
    results = pd.DataFrame(beam_rows("2GX-8", bottom=(0, 3, 5), depth=500))
    assert beam_actions(results, {"2GX-8": g(300, 500)}, {"2GX-8": 6000.0}, set(),
                        SETTINGS) == {}


def column_rows(name, **values):
    base = {"UniqueName": name, "Flexure_Check": "PASS", "Axial_Check": "PASS",
            "Shear_Check": "PASS", "SMRF_Dimension_Check": "PASS",
            "Design_Status_Reason": "All applicable checks passed.",
            "Column_Design_Status": "PASS", "Reinforcement_Ratio": 0.02,
            "Flexure_Utilization": 0.9, "Shear_Utilization": 0.5,
            "BCC_Ratio_X": 1.5, "BCC_Ratio_Y": 1.5,
            "Joint_Shear_Utilization_X": 0.6, "Joint_Shear_Utilization_Y": 0.6}
    base.update(values)
    return [base]


def test_column_needs():
    assert column_needs(pd.DataFrame(column_rows("C", Joint_Shear_Utilization_X=1.2))) == {"X"}
    assert column_needs(pd.DataFrame(column_rows("C", BCC_Ratio_Y=1.1))) == {"Y"}
    assert column_needs(pd.DataFrame(column_rows("C", Flexure_Check="FAIL"))) == {"square"}
    assert column_needs(pd.DataFrame(column_rows("C", Reinforcement_Ratio=0.065))) == {"square"}
    assert column_needs(pd.DataFrame(column_rows("C"))) == set()


def test_column_grows_along_the_failing_direction():
    report = pd.DataFrame(column_rows("2-C1", Joint_Shear_Utilization_X=1.3))
    sections = {"2-C1": cr(400, 400)}
    along_x = column_actions(report, sections, {"2-C1": 0.0}, {}, set(), SETTINGS)
    assert along_x["2-C1"][0] == cr(400, 500)  # depth (local 2) runs along X
    rotated = column_actions(report, sections, {"2-C1": 90.0}, {}, set(), SETTINGS)
    assert rotated["2-C1"][0] == cr(500, 400)


def test_a_lower_column_is_never_smaller_than_the_one_above():
    report = pd.DataFrame(column_rows("2-C1", Flexure_Check="FAIL") + column_rows("GF-C1"))
    sections = {"2-C1": cr(500, 500), "GF-C1": cr(500, 500)}
    above = {"GF-C1": "2-C1"}  # 2-C1 stands on GF-C1
    actions = column_actions(report, sections, {}, above, set(), SETTINGS, allow_shrink=False)
    assert actions["2-C1"][0] == cr(600, 600)
    assert actions["GF-C1"][0] == cr(600, 600)
    assert actions["GF-C1"][1] == "not smaller than 2-C1 above"


def test_a_column_that_grew_itself_keeps_its_own_reason():
    report = pd.DataFrame(column_rows("2-C1", Flexure_Check="FAIL")
                          + column_rows("GF-C1", Flexure_Check="FAIL"))
    sections = {"2-C1": cr(600, 600), "GF-C1": cr(500, 500)}
    actions = column_actions(report, sections, {}, {"GF-C1": "2-C1"}, set(), SETTINGS,
                             allow_shrink=False)
    assert actions["GF-C1"][0] == cr(700, 700)
    assert actions["GF-C1"][1].startswith("grow (square)")


def test_uls_combinations_by_seismic_choice():
    names = ["ULS 100 1.4 DL", "ULS 105 1.2 DL + f LL + 0.5 Lr + 1.0 WX",
             "ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3", "ULS 107 (1.2 + Ev) DL + f LL + 1.0 RSA3",
             "SLS 100 1.0 DL + 1.0 LL + 1.0 Lr", "DEF 100 1.0 DL"]
    assert uls_combinations(names, "EQ") == names[:3]
    assert uls_combinations(names, "RSA") == names[:2] + [names[3]]
    assert uls_combinations(names, "Both") == names[:4]
