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
    joint_min_side,
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


def test_a_cantilever_and_the_span_next_to_it_do_not_pass_their_size_to_each_other():
    cantilever = "Cantilever (Free at PtJ)"
    lengths = {"2GX-4": 3000.0, "2GX-4A": 3200.0, "2GX-4B": 3100.0}   # all within 30 %
    sections = {n: g(300, 500) for n in lengths}

    # the cantilever fails: the spans between columns keep their size
    results = pd.DataFrame(
        beam_rows("2GX-4", "FAILED: DEFLECTION (ACI 24.2.2)", support=cantilever)
        + beam_rows("2GX-4A") + beam_rows("2GX-4B"))
    actions = beam_actions(results, sections, lengths, set(), SETTINGS, allow_shrink=False)
    assert actions["2GX-4"][0] == g(300, 600)
    assert "2GX-4A" not in actions and "2GX-4B" not in actions

    # a span fails: the other span follows it, the cantilever does not
    results = pd.DataFrame(
        beam_rows("2GX-4", support=cantilever)
        + beam_rows("2GX-4A", "FAILED: DEFLECTION (ACI 24.2.2)") + beam_rows("2GX-4B"))
    actions = beam_actions(results, sections, lengths, set(), SETTINGS, allow_shrink=False)
    assert actions["2GX-4A"][0] == g(300, 600) and actions["2GX-4B"][0] == g(300, 600)
    assert "2GX-4" not in actions


def test_a_comfortable_span_can_shrink_whatever_the_cantilever_of_its_line_needs():
    from etabs_api.workflows.design_loop import support_kinds

    cantilever = "Cantilever (Free at PtJ)"
    results = pd.DataFrame(beam_rows("2GX-6", support=cantilever, depth=700, vu=400.0, defl=0.95)
                           + beam_rows("2GX-6A", depth=700, vu=20.0, defl=0.1))
    assert support_kinds(results) == {"2GX-6": "cantilever", "2GX-6A": "span"}
    sections = {"2GX-6": g(300, 700), "2GX-6A": g(300, 700)}
    actions = beam_actions(results, sections, {"2GX-6": 3000.0, "2GX-6A": 3000.0}, set(), SETTINGS)
    assert "2GX-6" not in actions                 # the cantilever is not comfortable: it stays
    assert actions["2GX-6A"][0].depth < 700       # the span shrinks without waiting for it


def test_members_of_a_line_share_a_size_only_up_to_the_largest_bend():
    import math
    from dataclasses import replace

    import pytest

    from etabs_api.workflows.design_loop import beam_directions

    # 2GX-5 runs along X; 2GX-5A bends 10 degrees from it, 2GX-5B bends 30 degrees
    points = pd.DataFrame({
        "UniqueName": ["1", "2", "3", "4"],
        "X": [0.0, 6000.0, 6000.0 + 6000.0 * math.cos(math.radians(10)),
              6000.0 - 6000.0 * math.cos(math.radians(30))],
        "Y": [0.0, 0.0, 6000.0 * math.sin(math.radians(10)), 6000.0 * math.sin(math.radians(30))],
    })
    connectivity = pd.DataFrame({
        "UniqueName": ["2GX-5", "2GX-5A", "2GX-5B", "C1"],
        "DesignType": ["Beam", "Beam", "Beam", "Column"],
        "UniquePtI": ["1", "2", "2", "1"], "UniquePtJ": ["2", "3", "4", "1"]})
    directions = beam_directions(connectivity, points)
    assert set(directions) == {"2GX-5", "2GX-5A", "2GX-5B"}     # the column is left out
    assert directions["2GX-5"] == pytest.approx((1.0, 0.0))

    results = pd.DataFrame(beam_rows("2GX-5", "FAILED: DEFLECTION (ACI 24.2.2)")
                           + beam_rows("2GX-5A") + beam_rows("2GX-5B"))
    sections = {n: g(300, 500) for n in ("2GX-5", "2GX-5A", "2GX-5B")}
    lengths = {n: 6000.0 for n in sections}

    def followers(limit):
        actions = beam_actions(results, sections, lengths, set(),
                               replace(SETTINGS, line_max_bend=limit), allow_shrink=False,
                               directions=directions)
        return sorted(n for n in actions if n != "2GX-5")

    assert followers(15.0) == ["2GX-5A"]               # 10 degrees follows, 30 does not
    assert followers(45.0) == ["2GX-5A", "2GX-5B"]     # the bend the tags allow
    assert followers(5.0) == []                        # each on its own
    # 2GX-5B runs the other way along its line: the direction of a beam has no sign
    assert followers(31.0) == ["2GX-5A", "2GX-5B"]

    # without the coordinates the bend is not checked
    actions = beam_actions(results, sections, lengths, set(), SETTINGS, allow_shrink=False)
    assert sorted(actions) == ["2GX-5", "2GX-5A", "2GX-5B"]
    assert beam_directions(connectivity, None) == {} and beam_directions(None, points) == {}


def test_beams_without_a_support_status_share_their_line_as_before():
    results = pd.DataFrame(beam_rows("2GX-9", "FAILED: DEFLECTION (ACI 24.2.2)")
                           + beam_rows("2GX-9A")).drop(columns="SupportStatus")
    sections = {n: g(300, 500) for n in ("2GX-9", "2GX-9A")}
    actions = beam_actions(results, sections, {"2GX-9": 6000.0, "2GX-9A": 6000.0}, set(),
                           SETTINGS, allow_shrink=False)
    assert actions["2GX-9A"][0] == g(300, 600)


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


def test_column_grows_the_side_the_failing_joint_is_measured_along():
    """The report's X and Y are the column's own axes: X the beams along its width
    (local 3), Y those along its depth (local 2). The joint depth is the column
    side along the beams, so that side grows, whatever the column's rotation."""
    sections = {"2-C1": cr(500, 500)}
    in_x = pd.DataFrame(column_rows("2-C1", Joint_Shear_Utilization_X=1.3))
    in_y = pd.DataFrame(column_rows("2-C1", Joint_Shear_Utilization_Y=1.3))
    for angle in (0.0, 90.0, 30.0):
        grown_x = column_actions(in_x, sections, {"2-C1": angle}, {}, set(), SETTINGS)
        grown_y = column_actions(in_y, sections, {"2-C1": angle}, {}, set(), SETTINGS)
        assert grown_x["2-C1"][0] == cr(600, 500)      # the width
        assert grown_y["2-C1"][0] == cr(500, 600)      # the depth
    assert "grow the depth" in grown_y["2-C1"][1]


def test_a_column_is_not_made_smaller_than_the_joint_needs():
    """20 bar diameters of the beam bars (ACI 18.8.2.3): 25 mm bars need 500 mm.

    Shrinking to 400 would fail the joint, grow the column again and, having
    grown, keep it from ever being made smaller."""
    comfortable = column_rows("2-C1", Flexure_Utilization=0.2, Shear_Utilization=0.2,
                              BCC_Ratio_X=5.0, BCC_Ratio_Y=5.0,
                              Joint_Shear_Utilization_X=0.3, Joint_Shear_Utilization_Y=0.3,
                              Reinforcement_Ratio=0.011)
    report = pd.DataFrame(comfortable)
    smrf = LoopSettings(combos=[], limits=Limits(), ranges=RANGES, smrf=True,
                        beam_bars={"dm": 25.0})
    assert joint_min_side(smrf) == 500.0
    assert column_actions(report, {"2-C1": cr(600, 600)}, {}, {}, set(), smrf)["2-C1"][0]         in (cr(500, 600), cr(600, 500))
    assert "2-C1" not in column_actions(report, {"2-C1": cr(500, 500)}, {}, {}, set(), smrf)
    gravity = LoopSettings(combos=[], limits=Limits(), ranges=RANGES, smrf=False)
    assert joint_min_side(gravity) == 0.0
    assert column_actions(report, {"2-C1": cr(500, 500)}, {}, {}, set(), gravity)["2-C1"][0]         in (cr(400, 500), cr(500, 400))


def test_the_loop_dialog_has_boxes_for_the_columns_and_the_iterations():
    from etabs_api.workflows.design_loop import LOOP_FIELDS

    keys = [key for key, _ in LOOP_FIELDS.values()]
    for key in ("column_max", "column_max_ratio", "max_inner", "max_inner_columns"):
        assert key in keys
    assert dict(LOOP_FIELDS.values())["column_max_ratio"] == 2.0


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


def test_a_failing_column_jumps_to_the_first_size_that_passes_on_the_forces():
    """With a sizer, one analysis moves the column straight to the size that works."""
    report = pd.DataFrame(column_rows("2-C1", Flexure_Check="FAIL"))
    sections = {"2-C1": cr(400, 400)}
    tried = []

    def sizer(member, sizes):
        tried.extend(sizes)
        return next(i for i, size in enumerate(sizes) if size.width >= 700)

    actions = column_actions(report, sections, {}, {}, set(), SETTINGS, allow_shrink=False,
                             sizer=sizer)
    assert actions["2-C1"][0] == cr(700, 700)
    assert tried[:3] == [cr(500, 500), cr(600, 600), cr(700, 700)]
    assert "first size that passes" in actions["2-C1"][1]


def test_without_a_passing_size_the_column_goes_to_the_largest():
    report = pd.DataFrame(column_rows("2-C1", Flexure_Check="FAIL"))
    actions = column_actions(report, {"2-C1": cr(1100, 1100)}, {}, {}, set(), SETTINGS,
                             allow_shrink=False, sizer=lambda member, sizes: None)
    assert actions["2-C1"][0] == cr(1200, 1200)


def test_joint_failures_still_grow_one_side_one_step():
    report = pd.DataFrame(column_rows("2-C1", Joint_Shear_Utilization_X=1.3))
    actions = column_actions(report, {"2-C1": cr(400, 400)}, {"2-C1": 0.0}, {}, set(),
                             SETTINGS, sizer=lambda member, sizes: 3)
    assert actions["2-C1"][0] == cr(500, 400)  # X: the width, one step


def test_column_size_passes_on_real_forces():
    """A 300 mm column cannot carry 4000 kN; an 600 mm column can."""
    import numpy as np

    from design.column_designer_aci318 import column_size_passes

    def row(side):
        return pd.Series({"UniqueName": "2-C1", "Story": "2F", "DesignType": "Column",
                          "Width": side, "Depth": side, "Diameter": np.nan,
                          "f'c": 28.0, "fy": 415.0, "fys": 415.0})

    forces = pd.DataFrame([{"UniqueName": "2-C1", "Combo": "ULS1", "Station": st, "P": -4000.0,
                            "V2": 50.0, "V3": 20.0, "M2": 60.0, "M3": 150.0}
                           for st in (0.0, 3000.0)])
    small, reason = column_size_passes(row(300.0), forces, 25.0, 10.0, 40.0, False)
    large, _ = column_size_passes(row(600.0), forces, 25.0, 10.0, 40.0, False)
    assert not small and "flexure" in reason
    assert large
    smrf_small, reason = column_size_passes(row(250.0), forces, 25.0, 10.0, 40.0, True)
    assert not smrf_small and "18.7.2.1" in reason


def test_workbench_sizer_tries_the_sizes_on_the_extracted_forces():
    import numpy as np

    from etabs_api.workflows.design_loop import Workbench

    bench = Workbench.__new__(Workbench)
    bench.settings = LoopSettings(combos=[], smrf=False,
                                  column_bars={"dmain": 25, "dties": 10, "cover": 40})
    bench.progress, bench.stage, bench.last = (lambda text: None), "", ""
    bench.tables = {
        "FRAME DATA": pd.DataFrame([{"UniqueName": "2-C1", "Story": "2F", "DesignType": "Column",
                                     "Width": 300.0, "Depth": 300.0, "Diameter": np.nan,
                                     "f'c": 28.0, "fy": 415.0, "fys": 415.0}]),
        "FACTORED LOADS": pd.DataFrame([{"UniqueName": "2-C1", "Combo": "ULS1", "Station": st,
                                         "P": -4000.0, "V2": 50.0, "V3": 20.0, "M2": 60.0,
                                         "M3": 150.0} for st in (0.0, 3000.0)]),
    }
    sizer = bench.column_sizer()
    index = sizer("2-C1", [cr(350, 350), cr(400, 400), cr(500, 500), cr(600, 600)])
    assert index in (1, 2)  # 400 or 500: the first that carries 4000 kN
    assert sizer("missing", [cr(400, 400)]) is None


def test_the_range_shown_holds_the_sizes_the_model_has():
    from etabs_api.workflows.design_loop import ModelSections, sizes_in_model, suggested_range
    from etabs_api.workflows.sections import Section

    found = ModelSections(sections={
        "1": Section("G", 500, 500, "C04", "G60"), "2": Section("G", 300, 700, "C04", "G60"),
        "3": Section("CR", 600, 800, "C04", "G60"),
        "4": Section("C", 600, 600, "C04", "G60", True),
        "5": Section("C", 700, 700, "C04", "G60", True)})
    present = sizes_in_model(found)
    assert present == {"G": {"width": [300, 500], "depth": [500, 700]},
                       "CR": {"size": [600, 800]}, "C": {"diameter": [600, 700]}}
    # a default range is widened to hold the model's sizes, and otherwise kept
    assert suggested_range([250, 400, 50], [300, 500]) == [250, 500, 50]
    assert suggested_range([250, 400, 50], []) == [250, 400, 50]
    # no default range (circular columns): from the model's sizes
    assert suggested_range(None, [600, 700], 100) == [600, 900, 100]
    assert suggested_range(None, []) == []
