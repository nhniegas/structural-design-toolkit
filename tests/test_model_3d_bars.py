"""
tests/test_model_3d_bars.py
===========================
The reinforcing bars of the 3D model (etabs_api/workflows/model_3d_bars.py):
where the bars, stirrups and ties of a designed beam and column are, and the
solids written for them. No ETABS is needed; the tests of the solids need
build123d.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import model_3d as m3  # noqa: E402
from etabs_api.workflows import model_3d_bars as rebar  # noqa: E402
from etabs_api.workflows.model_3d import Model3D  # noqa: E402


def beam():
    """A 300 x 500 beam, 6000 clear, along X with its top at z = 3000."""
    return m3.beam_bar("2GX-1", "2F", (0.0, 0.0, 3000.0), (6000.0, 0.0, 3000.0), 300.0, 500.0)


def face(**counts) -> dict:
    row = {"dm": 20.0, "ds": 10.0, "dw": 12.0, "cc": 40.0, "Stirrup_Legs": 2,
           "Spacing_2H": 100.0, "Spacing_Mid": 200.0, "n_side_per_face_gov": 0}
    row.update({f"n_{zone}_L{layer}": 0 for zone in ("left", "mid", "right") for layer in (1, 2, 3)})
    row.update(counts)
    return row


# --------------------------------------------------------------------------
# PIECES OF A LAYER, AND STATIONS
# --------------------------------------------------------------------------
def test_bars_at_both_supports_and_midspan_run_the_whole_length():
    assert rebar.layer_pieces(2, 2, 2, 6000.0) == [(0, 2, 0.0, 6000.0), (1, 2, 0.0, 6000.0)]


def test_extra_bars_at_the_supports_are_drawn_over_their_share_of_the_span():
    pieces = rebar.layer_pieces(4, 2, 3, 6000.0)
    whole = [p for p in pieces if (p[2], p[3]) == (0.0, 6000.0)]
    assert [p[0] for p in whole] == [0, 3]                    # the two outer bars
    assert (1, 4, 0.0, 1800.0) in pieces and (1, 4, 4200.0, 6000.0) in pieces   # third bar
    assert (2, 4, 0.0, 1800.0) in pieces and len(pieces) == 5                   # fourth: left only


def test_an_extra_bar_at_midspan_is_drawn_over_the_middle_half():
    pieces = rebar.layer_pieces(2, 3, 2, 6000.0)
    assert (1, 3, 1500.0, 4500.0) in pieces


def test_a_bar_needed_at_a_support_and_at_midspan_is_one_bar():
    # the zones overlap between 25 % and 30 % of the span: one bar from the support to 75 %
    assert (1, 3, 0.0, 4500.0) in rebar.layer_pieces(3, 3, 2, 6000.0)


def test_the_outer_positions_are_filled_first():
    assert rebar.outer_first(5) == [0, 4, 1, 3, 2]
    assert rebar.spread(3, 100.0) == [-100.0, 0.0, 100.0] and rebar.spread(1, 100.0) == [0.0]


def test_stirrups_are_closer_within_2h_of_each_support():
    stations = rebar.tie_stations(6000.0, 100.0, 200.0, 1000.0)
    assert stations[0] == 50.0 and stations[-1] == 5950.0
    gaps = [round(b - a) for a, b in zip(stations, stations[1:])]
    assert gaps[:9] == [100] * 9 and gaps[-9:] == [100] * 9      # up to 950 from each end
    # 4100 are left between the zones: 21 equal spaces of 195, none over the 200 asked
    assert set(gaps[9:-9]) == {195} and len(gaps[9:-9]) == 21
    # the space left between the zones is shared out, never wider than the spacing
    odd = rebar.tie_stations(6130.0, 100.0, 200.0, 1000.0)
    assert max(b - a for a, b in zip(odd, odd[1:])) <= 200.0 + 1e-6
    assert stations == sorted(stations)


def test_a_short_member_has_one_spacing_and_one_with_no_spacing_has_no_stirrup():
    assert rebar.tie_stations(1500.0, 100.0, 200.0, 1000.0) == [50.0 + 100.0 * i for i in range(15)]
    assert rebar.tie_stations(3000.0, 0.0, 0.0, 600.0) == []
    assert rebar.tie_stations(3000.0, 0.0, 150.0, 600.0)[1] == 200.0     # one spacing given


# --------------------------------------------------------------------------
# A BEAM
# --------------------------------------------------------------------------
def test_the_top_bars_are_under_the_top_cover_and_the_bottom_bars_in_layer_3():
    top = face(n_left_L1=2, n_mid_L1=2, n_right_L1=2)
    bottom = face(n_left_L3=3, n_mid_L3=3, n_right_L3=3)
    bars, hoops = rebar.beam_reinforcement(beam(), top, bottom, ties=False)
    assert len(bars) == 5 and hoops == []
    # 40 cover + 10 stirrup + half of 20: 60 from each face
    assert sorted({round(b.start[2]) for b in bars}) == [2560, 2940]
    upper = sorted(round(b.start[1]) for b in bars if round(b.start[2]) == 2940)
    lower = sorted(round(b.start[1]) for b in bars if round(b.start[2]) == 2560)
    assert upper == [-90, 90] and lower == [-90, 0, 90]
    assert all(b.kind == "bar" and b.circular and b.width == 20.0 for b in bars)
    assert all((b.start[0], b.end[0]) == (0.0, 6000.0) for b in bars)
    assert {b.name for b in bars} == {"2GX-1"} and {b.group for b in bars} == {"2F"}


def test_a_second_layer_is_one_bar_and_a_gap_further_in():
    top = face(n_left_L1=2, n_mid_L1=2, n_right_L1=2, n_left_L2=2, n_right_L2=2)
    bottom = face(n_left_L3=2, n_mid_L3=2, n_right_L3=2, n_mid_L2=2)
    bars, _ = rebar.beam_reinforcement(beam(), top, bottom, ties=False)
    levels = sorted({round(b.start[2]) for b in bars})
    assert levels == [2560, 2605, 2895, 2940]            # 20 bar + 25 clear between layers
    second_top = [b for b in bars if round(b.start[2]) == 2895]
    assert sorted((round(b.start[0]), round(b.end[0])) for b in second_top) == [
        (0, 1800), (0, 1800), (4200, 6000), (4200, 6000)]
    second_bottom = [b for b in bars if round(b.start[2]) == 2605]
    assert {(round(b.start[0]), round(b.end[0])) for b in second_bottom} == {(1500, 4500)}


def test_the_stirrups_fit_inside_the_cover_and_the_web_bars_run_the_whole_length():
    top = face(n_left_L1=2, n_mid_L1=2, n_right_L1=2, n_side_per_face_gov=1, Stirrup_Legs=3)
    bottom = face(n_left_L3=2, n_mid_L3=2, n_right_L3=2)
    bars, hoops = rebar.beam_reinforcement(beam(), top, bottom, ties=True)
    web = [b for b in bars if b.width == 12.0]
    assert len(web) == 2 and {round(b.start[2]) for b in web} == {2750}       # at mid-depth
    assert sorted(round(b.start[1]) for b in web) == [-94, 94]
    first = hoops[0]
    assert (first.width, first.depth, first.diameter, first.inner_up) == (210.0, 410.0, 10.0, 1)
    assert first.centre == pytest.approx((50.0, 0.0, 2750.0)) and first.axis == (1.0, 0.0, 0.0)
    assert len(hoops) == len(rebar.tie_stations(6000.0, 100.0, 200.0, 1000.0))


# --------------------------------------------------------------------------
# A COLUMN
# --------------------------------------------------------------------------
def column(angle: float = 0.0, circular: bool = False):
    size = (600.0, 600.0) if circular else (400.0, 600.0)
    return m3.column_bar("2-C1", "2F", (0.0, 0.0, 0.0), (0.0, 0.0, 3000.0), *size, angle, circular)


SPEC = {"layout": [(70.0, 70.0, 1), (330.0, 70.0, 1), (330.0, 530.0, 1), (70.0, 530.0, 1)],
        "main": 25.0, "tie": 10.0, "cover": 40.0, "spacing": 150.0, "legs_x": 2, "legs_y": 3}


def test_the_column_bars_stand_at_their_designed_positions():
    bars, hoops = rebar.column_reinforcement(column(), SPEC, ties=False)
    assert hoops == [] and len(bars) == 4
    # the depth of 600 is along X (angle 0) and the width of 400 across it
    assert sorted((round(abs(b.start[0])), round(abs(b.start[1]))) for b in bars) == [(230, 130)] * 4
    assert all((b.start[2], b.end[2]) == (0.0, 3000.0) and b.width == 25.0 for b in bars)


def test_a_turned_column_turns_its_bars_with_it():
    bars, _ = rebar.column_reinforcement(column(angle=90.0), SPEC, ties=False)
    assert sorted((round(abs(b.start[0])), round(abs(b.start[1]))) for b in bars) == [(130, 230)] * 4


def test_the_ties_are_at_the_designed_spacing_with_their_inner_legs():
    _, hoops = rebar.column_reinforcement(column(), SPEC, ties=True)
    assert [round(h.centre[2]) for h in hoops[:3]] == [50, 200, 350] and len(hoops) == 20
    tie = hoops[0]
    assert (tie.width, tie.depth, tie.diameter) == (310.0, 510.0, 10.0)
    assert (tie.inner_up, tie.inner_side) == (1, 0)          # 3 legs along the depth, 2 across
    assert tie.axis == (0.0, 0.0, 1.0)


def test_a_bundle_is_its_bars_one_behind_the_other_and_a_spiral_is_rings():
    spec = dict(SPEC, layout=[(240.0, 0.0, 2), (-240.0, 0.0, 1)], spacing=75.0)
    bars, hoops = rebar.column_reinforcement(column(circular=True), spec, ties=True)
    import math

    assert sorted(round(math.hypot(b.start[0], b.start[1])) for b in bars) == [215, 240, 240]
    assert hoops[0].circular and hoops[0].width == 510.0 and len(hoops) == 39


def test_the_layout_of_the_report_is_read_and_a_bad_entry_left_out():
    assert rebar.decode_layout("72.00,72.00,1;428.00,72.00,2;x,y,z;") == [
        (72.0, 72.0, 1), (428.0, 72.0, 2)]
    assert rebar.decode_layout(None) == []


def test_a_column_takes_its_closest_tie_spacing_and_most_legs_from_its_two_ends():
    report = pd.DataFrame([
        ("2-C1", "I", "70,70,1;330,530,1", 10.0, 40.0, 100.0, 3, 2),
        ("2-C1", "J", "70,70,1;330,530,1", 10.0, 40.0, 150.0, 2, 4),
        ("2-C2", "I", "", 10.0, 40.0, 150.0, 2, 2),
    ], columns=["UniqueName", "End", "Bar_Layout_Data", "Tie_Bar_mm", "Cover_mm",
                "Transverse_Spacing_mm", "Transverse_Legs_X", "Transverse_Legs_Y"])
    specs = rebar.column_specs(report, 25.0)
    assert list(specs) == ["2-C1"]            # a column with no layout has no bars to draw
    assert (specs["2-C1"]["spacing"], specs["2-C1"]["legs_x"], specs["2-C1"]["legs_y"],
            specs["2-C1"]["main"]) == (100.0, 3, 4, 25.0)


# --------------------------------------------------------------------------
# THE MODEL AND THE FILE
# --------------------------------------------------------------------------
def designed_model():
    model = Model3D(bars=[beam(), column(),
                          m3.beam_bar("2B-9", "2F", (0.0, 4000.0, 3000.0), (6000.0, 4000.0, 3000.0),
                                      250.0, 400.0)])
    beams = pd.DataFrame([dict(face(n_left_L1=2, n_mid_L1=2, n_right_L1=2), UniqueName="2GX-1",
                               Face="TOP"),
                          dict(face(n_left_L3=2, n_mid_L3=2, n_right_L3=2), UniqueName="2GX-1",
                               Face="BOTTOM")])
    columns = pd.DataFrame([("2-C1", "I", "70,70,1;330,70,1;330,530,1;70,530,1", 10.0, 40.0,
                             150.0, 2, 3)],
                           columns=["UniqueName", "End", "Bar_Layout_Data", "Tie_Bar_mm",
                                    "Cover_mm", "Transverse_Spacing_mm", "Transverse_Legs_X",
                                    "Transverse_Legs_Y"])
    return model, beams, columns


def test_only_designed_members_get_bars_and_the_others_are_counted():
    model, beams, columns = designed_model()
    counts = rebar.add_reinforcement(model, beams, columns, 25.0, rebar.MAIN_BARS)
    assert counts == {"beams": 1, "columns": 1, "beams_without": 1, "columns_without": 0}
    assert model.count("bar") == 8 and model.hoops == []
    with_ties, beams, columns = designed_model()
    rebar.add_reinforcement(with_ties, beams, columns, 25.0, rebar.ALL_BARS)
    assert with_ties.count("tie") == len(with_ties.hoops) > 40


def test_no_bars_are_added_when_none_are_asked_for_or_no_design_is_saved():
    model, beams, columns = designed_model()
    assert rebar.add_reinforcement(model, beams, columns, 25.0, rebar.NO_BARS)["beams"] == 0
    counts = rebar.add_reinforcement(model, None, None, 0.0, rebar.ALL_BARS)
    assert counts["beams_without"] == 2 and counts["columns_without"] == 1
    assert len(model.bars) == 3 and not model.hoops


def test_the_file_holds_the_bars_and_ties_as_valid_solids_inside_their_members(tmp_path):
    bd = pytest.importorskip("build123d")
    model, beams, columns = designed_model()
    rebar.add_reinforcement(model, beams, columns, 25.0, rebar.ALL_BARS)
    path = str(tmp_path / "bars.step")
    totals = m3.write_step(model, path, "T")
    assert totals["bar"] == 8 and totals["tie"] == len(model.hoops)
    assert totals["solids"] == 3 + 8 + len(model.hoops)
    # every stirrup of the beam is one shape, and every tie of the column another
    assert totals["shapes"] == 3 + 2 + 2
    back = bd.import_step(path)
    solids = back.solids()
    assert len(solids) == totals["solids"] and all(s.is_valid for s in solids)
    box = back.bounding_box()            # nothing sticks out of the concrete
    assert (round(box.min.X), round(box.min.Z), round(box.max.Z)) == (-300, 0, 3000)
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    assert "'BAR 2GX-1'" in text and "'TIE 2-C1'" in text
    assert text.count("OVER_RIDING_STYLED_ITEM") < 20       # one colour for a shape, not a bar


def test_a_tie_with_inner_legs_is_one_valid_solid(tmp_path):
    bd = pytest.importorskip("build123d")
    model = Model3D(hoops=[m3.Hoop("tie", "C1", "2F", (0.0, 0.0, 500.0), (0.0, 0.0, 1.0),
                                   (1.0, 0.0, 0.0), 500.0, 600.0, 16.0, 2, 2)])
    assembly, shapes = m3.build_assembly(model)
    tie = assembly.children[0].children[0]
    assert isinstance(tie, bd.Solid) and tie.is_valid and shapes == 1
    path = str(tmp_path / "tie.step")
    m3.write_step(model, path)
    assert all(s.is_valid for s in bd.import_step(path).solids())


def test_the_summary_says_how_many_bars_and_which_members_have_none():
    model, beams, columns = designed_model()
    counts = rebar.add_reinforcement(model, beams, columns, 25.0, rebar.MAIN_BARS)
    counts.update(ties=False, notes=["The designs are older than the model file."])
    totals = {kind: model.count(kind) for kind in m3.KINDS} | {
        "solids": 11, "shapes": 5, "size": 2_000_000}
    text = m3.model_summary(totals, model, "m.EDB", "m.step", True, None, counts).text()
    assert "8 bars (stirrups and ties not included), in 1 beams and 1 columns" in text
    assert "1 beams and 0 columns have no design saved" in text
    assert "no laps, hooks, bends" in text and "older than the model file" in text
    assert "Reinforcing bars  not included" in " ".join(
        m3.model_summary(totals, model, "m.EDB", "m.step", True, None).text().split("  ")) or \
        "not included" in m3.model_summary(totals, model, "m.EDB", "m.step", True, None).text()
