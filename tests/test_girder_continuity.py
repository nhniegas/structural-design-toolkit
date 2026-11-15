"""Girder bars through the columns (v0.17.0): layer floors, pairs, matching."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design.concrete.beams import continuity  # noqa: E402
from design.concrete.beams import design as beam_design  # noqa: E402
from design.concrete.beams.schedule import through_ends  # noqa: E402
from design.concrete.beams.sections import BeamFlexureDesign  # noqa: E402


# --------------------------------------------------------------------------
# the layer floor of the flexure engine
# --------------------------------------------------------------------------
def test_without_a_floor_the_layers_fill_one_after_the_other():
    engine = BeamFlexureDesign(300.0, 600.0, dmain=25.0, dstirrup=10.0, cc=40.0)
    most = engine.max_bar_per_layer
    assert engine.calculate_layer_distribution(most + 2) == [most, 2]


def test_a_floor_keeps_its_layers_and_extra_bars_fill_the_outer_layer_first():
    engine = BeamFlexureDesign(400.0, 600.0, dmain=25.0, dstirrup=10.0, cc=40.0)
    assert engine.max_bar_per_layer >= 5
    engine.set_layer_floor(top=[3, 2])
    assert engine.n_top >= 5
    engine.n_top = 5
    assert engine.top_layers() == [3, 2]
    engine.n_top = 6
    assert engine.top_layers() == [4, 2]        # the extra bar goes to the outer layer


def test_a_floor_is_capped_at_what_a_layer_of_this_width_holds():
    engine = BeamFlexureDesign(250.0, 500.0, dmain=25.0, dstirrup=10.0, cc=40.0)
    most = engine.max_bar_per_layer
    engine.set_layer_floor(top=[most + 3, 2])
    assert engine.floor_top == [most, 2]


def _props(name="B1"):
    return pd.DataFrame([{
        "UniqueName": name, "Story": "L2", "SectProp": "B400x600",
        "SupportStatus": "Supported Both Ends", "Width": 400.0, "Depth": 600.0,
        "f'c": 28.0, "fy": 414.0, "fyw": 414.0,
        "dm": 25.0, "ds": 10.0, "dw": 16.0, "cc": 40.0,
    }])


def _forces(name="B1", span=6000.0, wu=40.0):
    rows = []
    length = span / 1000.0
    for i in range(13):
        x = length * i / 12
        rows.append({"UniqueName": name, "Combo": "GRAV", "Station": x * 1000.0,
                     "V2": wu * (length / 2 - x), "M3": wu * x * (length - x) / 2, "T": 0.0})
    return pd.DataFrame(rows)


def test_the_design_keeps_the_floor_at_the_matched_support():
    plain = beam_design.execute_beam_design(_props(), _forces(), False, "GRAV")
    top = plain[plain["Face"] == "TOP"].iloc[0]
    floor = [int(top["n_left_L1"]), int(top["n_left_L1"])]   # a full second layer
    matched = beam_design.execute_beam_design(
        _props(), _forces(), False, "GRAV", layer_floors={"B1": {("left", "top"): floor}})
    top = matched[matched["Face"] == "TOP"].iloc[0]
    assert int(top["n_left_L1"]) >= floor[0]
    assert int(top["n_left_L2"]) >= floor[1]
    # the other end keeps its own bars
    assert int(top["n_right_L2"]) == int(plain[plain["Face"] == "TOP"].iloc[0]["n_right_L2"])


# --------------------------------------------------------------------------
# girders in line at a column
# --------------------------------------------------------------------------
def _model(angle_point=(12000.0, 0.0), extra_beam=None):
    """G1 (1 -> 2) and G2 (2 -> 3) meet at joint 2 on column K1; G3 (2 -> 5) crosses."""
    points = pd.DataFrame([
        {"UniqueName": "1", "X": 0.0, "Y": 0.0, "Z": 3000.0},
        {"UniqueName": "2", "X": 6000.0, "Y": 0.0, "Z": 3000.0},
        {"UniqueName": "3", "X": angle_point[0], "Y": angle_point[1], "Z": 3000.0},
        {"UniqueName": "4", "X": 6000.0, "Y": 0.0, "Z": 0.0},
        {"UniqueName": "5", "X": 6000.0, "Y": 6000.0, "Z": 3000.0},
    ])
    rows = [
        {"UniqueName": "G1", "UniquePtI": "1", "UniquePtJ": "2", "DesignType": "Beam"},
        {"UniqueName": "G2", "UniquePtI": "2", "UniquePtJ": "3", "DesignType": "Beam"},
        {"UniqueName": "G3", "UniquePtI": "2", "UniquePtJ": "5", "DesignType": "Beam"},
        {"UniqueName": "K1", "UniquePtI": "4", "UniquePtJ": "2", "DesignType": "Column"},
    ]
    if extra_beam:
        rows.append(extra_beam)
    frame = pd.DataFrame([{"UniqueName": "K1", "Width": 600.0, "Depth": 500.0}])
    return pd.DataFrame(rows), points, frame


def _rows(name, top_left, top_right, bot_left, bot_right, depth=600.0,
          status="Supported Both Ends"):
    """Result rows; layers given outer first (bottom ones are stored from the top down)."""
    def cols(end, layers, top):
        stored = (layers + [0, 0, 0])[:3]
        stored = stored if top else stored[::-1]
        return {f"n_{end}_L{k + 1}": stored[k] for k in range(3)}
    return [
        {"UniqueName": name, "Face": "TOP", "Depth": depth, "SupportStatus": status,
         **cols("left", top_left, True), **cols("right", top_right, True)},
        {"UniqueName": name, "Face": "BOTTOM", "Depth": depth, "SupportStatus": status,
         **cols("left", bot_left, False), **cols("right", bot_right, False)},
    ]


def _results(depth_g2=600.0):
    return pd.DataFrame(
        _rows("G1", [3], [4, 2], [3], [3])
        + _rows("G2", [5], [3], [2, 2], [3], depth=depth_g2)
        + _rows("G3", [3], [3], [3], [3]))


def test_girders_in_line_at_a_column_are_paired():
    connectivity, points, frame = _model()
    pairs = continuity.girder_pairs(_results(), connectivity, points, frame)
    assert len(pairs) == 1
    pair = pairs[0]
    assert {(pair.first, pair.first_end), (pair.second, pair.second_end)} == {
        ("G1", "right"), ("G2", "left")}
    assert pair.column_side == 500.0


def test_a_bend_of_one_in_six_is_in_line_and_steeper_is_not():
    within = _model(angle_point=(12000.0, 6000.0 / 6.0 - 1.0))[0:2]
    assert len(continuity.girder_pairs(_results(), *within)) == 1
    beyond = _model(angle_point=(12000.0, 6000.0 / 6.0 + 50.0))[0:2]
    assert continuity.girder_pairs(_results(), *beyond) == []


def test_gravity_beams_are_not_paired():
    connectivity, points, frame = _model()
    results = _results()
    results.loc[results["UniqueName"] == "G2", "SupportStatus"] = "Beam-Framed / Floating"
    assert continuity.girder_pairs(results, connectivity, points, frame) == []


def test_layers_take_the_larger_count_of_the_two_spans():
    connectivity, points, frame = _model()
    results = _results()
    pairs = continuity.girder_pairs(results, connectivity, points, frame)
    floors = continuity.match_girder_bars(results, pairs)
    # top: G1 right 4+2, G2 left 5 -> both 5+2
    assert floors["G1"][("right", "top")] == [5, 2]
    assert floors["G2"][("left", "top")] == [5, 2]
    # bottom: G1 right 3, G2 left 2+2 -> both 3+2 (same depth)
    assert floors["G1"][("right", "bot")] == [3, 2]
    assert floors["G2"][("left", "bot")] == [3, 2]
    assert "G3" not in floors


def test_bottom_bars_are_left_alone_when_the_depth_step_is_over_one_in_six():
    connectivity, points, frame = _model()
    deep = _results(depth_g2=700.0)     # 100 mm step over a 500 mm column: 1 in 5
    floors = continuity.match_girder_bars(
        deep, continuity.girder_pairs(deep, connectivity, points, frame))
    assert ("right", "bot") not in floors["G1"]
    assert floors["G1"][("right", "top")] == [5, 2]
    assert continuity.bottom_bars_bend(600.0, 680.0, 500.0)      # 80 <= 83
    assert not continuity.bottom_bars_bend(600.0, 700.0, 500.0)  # 100 > 83


def test_the_note_says_what_runs_through_and_what_anchors():
    connectivity, points, frame = _model()
    results = pd.DataFrame(_rows("G1", [3], [6], [3], [3]) + _rows("G2", [4], [3], [3], [3])
                           + _rows("G3", [3], [3], [3], [3]))
    pairs = continuity.girder_pairs(results, connectivity, points, frame)
    notes = continuity.describe_continuity(results, pairs)
    g1 = notes[results["UniqueName"] == "G1"].iloc[0]
    assert g1 == "right (to G2): top 4 through, 2 anchored; bottom 3 through"
    assert through_ends(g1) == ["R"]
    assert notes[results["UniqueName"] == "G3"].iloc[0] == "-"


def test_through_ends_reads_both_ends():
    assert through_ends("left (to A): top 3 through | right (to B): top 2 through") == ["L", "R"]
    assert through_ends("-") == []


@pytest.mark.parametrize("value", [None, float("nan"), ""])
def test_through_ends_of_a_blank(value):
    assert through_ends(value) == []
