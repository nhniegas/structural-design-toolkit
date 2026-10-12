"""
tests/test_concrete_quantities.py
=================================
The concrete quantities (etabs_api/workflows/concrete_quantities.py): the
volume of each kind of member with nothing counted twice, the floor area, the
slab on grade on the footprint, the foundation, and the ratio in m3/m2.
No ETABS is needed.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import concrete_quantities as cq  # noqa: E402
from etabs_api.workflows import foundation_plan as fp  # noqa: E402
from etabs_api.workflows import model_3d as m3  # noqa: E402
from etabs_api.workflows.concrete_quantities import FloorShell  # noqa: E402

FLOOR = [(0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0)]      # 24 m2


def bay(story: str = "2F", base: float = 0.0, slab: bool = True):
    """Four 400 x 400 columns 3 m high, four 300 x 500 beams between their faces,
    and a 150 slab of 6 x 4 m."""
    top = base + 3000.0
    corners = [(0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0)]
    bars = [m3.column_bar(f"{story}-C{i}", story, (x, y, base), (x, y, top), 400.0, 400.0)
            for i, (x, y) in enumerate(corners)]
    for i, (a, b) in enumerate(zip(corners, corners[1:] + corners[:1])):
        bars.append(m3.beam_bar(f"{story}-B{i}", story, (*a, top), (*b, top), 300.0, 500.0,
                                (200.0, 200.0)))
    shells = [FloorShell("S", story, FLOOR, 150.0)] if slab else []
    return bars, shells


def test_a_column_is_its_section_by_its_height_and_a_round_one_its_circle():
    square = m3.column_bar("C", "2F", (0, 0, 0), (0, 0, 3000.0), 400.0, 500.0)
    round_one = m3.column_bar("C", "2F", (0, 0, 0), (0, 0, 3000.0), 500.0, 500.0, circular=True)
    assert cq.bar_volume(square) == pytest.approx(0.6)
    assert cq.bar_volume(round_one) == pytest.approx(math.pi * 0.25**2 * 3.0)


def test_a_story_is_measured_with_nothing_counted_twice():
    bars, shells = bay()
    story = cq.measure_story("2F", bars, shells)
    assert story.columns == pytest.approx(4 * 0.4 * 0.4 * 3.0)                 # 1.92
    # beams: 300 wide, 500 - 150 below the slab, clear between the column faces
    assert story.beams == pytest.approx(0.3 * 0.35 * (2 * 5.6 + 2 * 3.6))      # 1.932
    # slab: 24 m2 less the quarter of each corner column that is in it, 150 thick
    assert story.slabs == pytest.approx((24.0 - 4 * 0.04) * 0.15)              # 3.576
    assert story.area == pytest.approx(24.0)
    assert story.volume == pytest.approx(1.92 + 1.932 + 3.576)
    assert story.ratio == pytest.approx(story.volume / 24.0)


def test_a_beam_with_no_slab_over_it_is_measured_at_its_full_depth():
    bars, _ = bay(slab=False)
    story = cq.measure_story("2F", bars, [])
    assert story.beams == pytest.approx(0.3 * 0.5 * (2 * 5.6 + 2 * 3.6))
    assert story.slabs == 0.0 and story.area == 0.0      # no floor shell: no floor area


def test_an_opening_is_taken_out_of_the_slab_and_of_the_floor_area():
    bars, shells = bay()
    shells.append(FloorShell("O", "2F", [(2000.0, 1000.0), (4000.0, 1000.0), (4000.0, 3000.0),
                                         (2000.0, 3000.0)], opening=True))             # 4 m2
    story = cq.measure_story("2F", bars, shells)
    assert story.area == pytest.approx(20.0)
    assert story.slabs == pytest.approx((20.0 - 4 * 0.04) * 0.15)


def test_a_beam_under_an_opening_keeps_its_full_depth():
    bars, shells = bay()
    bars.append(m3.beam_bar("2F-B9", "2F", (0.0, 2000.0, 3000.0), (6000.0, 2000.0, 3000.0),
                            300.0, 500.0))
    shells.append(FloorShell("O", "2F", [(2000.0, 1000.0), (4000.0, 1000.0), (4000.0, 3000.0),
                                         (2000.0, 3000.0)], opening=True))
    with_opening = cq.measure_story("2F", bars, shells).beams
    assert with_opening == pytest.approx(0.3 * 0.35 * (2 * 5.6 + 2 * 3.6) + 0.3 * 0.5 * 6.0)


def test_a_wall_is_its_thickness_by_its_height_by_its_length():
    wall = m3.wall_bar("W1", "2F", 0.0, 0.0, 5000.0, 0.0, 0.0, 3000.0, 250.0)
    assert cq.measure_story("2F", [wall], []).walls == pytest.approx(0.25 * 3.0 * 5.0)


def test_the_footprint_is_the_outline_of_all_the_floors_with_no_opening_taken_out():
    upper = FloorShell("U", "3F", [(0, 0), (3000.0, 0), (3000.0, 4000.0), (0, 4000.0)], 150.0)
    lower = FloorShell("L", "2F", FLOOR, 150.0)
    hole = FloorShell("O", "2F", [(1000.0, 1000.0), (2000.0, 1000.0), (2000.0, 2000.0),
                                  (1000.0, 2000.0)], opening=True)
    assert cq.footprint([upper, lower, hole], []) == pytest.approx(24.0, abs=0.01)
    bars, _ = bay()
    columns = [b for b in bars if b.kind == "column"]
    assert cq.footprint([], columns) == pytest.approx(24.0)       # no floor: around the columns


def test_the_ratio_is_the_total_volume_over_the_total_floor_area():
    upper = bay("3F", 3000.0)
    lower = bay("2F", 0.0)
    found = cq.measure(upper[0] + lower[0], upper[1] + lower[1], ["3F", "2F"], 100.0)
    one = 1.92 + 1.932 + 3.576
    assert [s.story for s in found.stories] == ["3F", "2F"]
    assert found.superstructure == pytest.approx(2 * one)
    assert found.grade_area == pytest.approx(24.0, abs=0.01)
    assert found.grade_volume == pytest.approx(2.4, abs=0.001)
    assert found.floor_area == pytest.approx(72.0, abs=0.01)          # two floors and the ground
    assert found.ratio == pytest.approx((2 * one + 2.4) / 72.0, rel=1e-4)


def test_with_no_slab_on_grade_the_ground_floor_is_not_in_the_area():
    bars, shells = bay()
    found = cq.measure(bars, shells, ["2F"], 0.0)
    assert found.grade_volume == 0.0 and found.floor_area == pytest.approx(24.0)
    assert found.ratio == pytest.approx(found.superstructure / 24.0)


def test_the_foundation_adds_its_footings_and_tie_beams(tmp_path):
    columns = [fp.PlanColumn("C1", "GF-C1", "1", 0.0, 0.0, -1000.0, 600.0, 600.0),
               fp.PlanColumn("C2", "GF-C2", "2", 6000.0, 0.0, -1000.0, 600.0, 600.0)]
    shells = [fp.Shell(str(i), [(x - 1000.0, -1000.0), (x + 1000.0, -1000.0), (x + 1000.0, 1000.0),
                                (x - 1000.0, 1000.0)], -1000.0, 500.0)
              for i, x in enumerate((0.0, 6000.0))]
    beams = fp.name_tie_beams([fp.TieBeam("5", 0.0, 0.0, 6000.0, 0.0, -1000.0, 300.0, 500.0,
                                          "1", "2")])
    target = str(tmp_path / "T - FOUNDATION.EDB")
    fp.save_plan(target, fp.group_footings(shells, columns), beams, columns)
    plan = fp.load_plan(target)
    footings, tie_beams = cq.foundation_volumes(plan)
    assert footings == pytest.approx(2 * 4.0 * 0.5)
    assert tie_beams == pytest.approx(0.3 * 0.5 * 5.4)           # between the column faces
    bars, floor = bay()
    found = cq.measure(bars, floor, ["2F"], 0.0, plan)
    assert found.foundation_included
    assert found.volume == pytest.approx(found.superstructure + 4.0 + 0.81)
    assert found.floor_area == pytest.approx(24.0)               # a foundation adds no floor


def test_a_story_with_no_floor_shell_is_said(tmp_path):
    bars, _ = bay(slab=False)
    found = cq.measure(bars, [], ["2F"], 100.0)
    assert any("No floor shell at 2F" in note for note in found.notes)
    assert any("no floor slab" in note for note in found.notes)


def test_the_report_and_the_workbook_give_the_ratio(tmp_path):
    bars, shells = bay()
    found = cq.measure(bars, shells, ["2F"], 100.0, model_path="m.EDB")
    text = cq.quantities_report(found).plain()
    for expected in ("CONCRETE QUANTITIES", "By story", "Slab on grade, 100 mm on the footprint",
                     "Foundation", "not included", "Concrete ratio", f"{found.ratio:.3f}"):
        assert expected in text
    summary = cq.quantities_summary(found).text()
    assert f"{found.ratio:.3f} m3/m2" in summary and "48.00 m2" in summary
    import pandas as pd

    path = cq.save_workbook(found, str(tmp_path / "q.xlsx"))
    building = pd.read_excel(path, sheet_name="Building")
    total = building[building["Part"] == "Total"].iloc[0]
    assert total["Concrete (m3)"] == pytest.approx(found.volume, abs=0.001)
    assert pd.read_excel(path, sheet_name="By story")["Story"].tolist() == ["2F"]


def test_a_column_inside_the_floor_is_taken_out_whole_and_a_turned_one_by_its_own_outline():
    inner = m3.column_bar("C", "2F", (3000.0, 2000.0, 0.0), (3000.0, 2000.0, 3000.0), 400.0,
                          600.0, angle=30.0)
    round_one = m3.column_bar("R", "2F", (1500.0, 2000.0, 0.0), (1500.0, 2000.0, 3000.0), 500.0,
                              500.0, circular=True)
    story = cq.measure_story("2F", [inner, round_one], [FloorShell("S", "2F", FLOOR, 200.0)])
    assert cq.column_outline(inner).area == pytest.approx(240000.0)
    assert story.slabs == pytest.approx((24.0 - 0.24 - math.pi * 0.25**2) * 0.2, rel=2e-3)


def test_the_ratio_is_printed_with_its_units_not_as_latex():
    bars, shells = bay()
    text = cq.quantities_report(cq.measure(bars, shells, ["2F"], 100.0)).plain()
    assert "m³/m²" in text and "^" not in text and "\\" not in text
