"""
tests/test_foundation_plan.py
=============================
The foundation plan (etabs_api/workflows/foundation_plan.py) and its drawing on
the framing plans: which shells are one footing, the marks of isolated and
combined footings, the names of the tie beams, and what ``sdt plans`` draws.
No ETABS is needed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import foundation_plan as fp  # noqa: E402
from etabs_api.workflows import framing_plans as plans  # noqa: E402
from etabs_api.workflows.foundation_plan import PlanColumn, Shell, TieBeam  # noqa: E402


def square(name: str, x: float, y: float, side: float = 2000.0, thick: float = 500.0) -> Shell:
    half = side / 2.0
    return Shell(name, [(x - half, y - half), (x + half, y - half), (x + half, y + half),
                        (x - half, y + half)], -1000.0, thick, f"FTG_{thick:.0f}")


def column(mark: str, x: float, y: float) -> PlanColumn:
    return PlanColumn(mark, f"GF-{mark}", mark, x, y, -1000.0, 600.0, 600.0)


COLUMNS = [column("C1", 0.0, 10000.0), column("C2", 6000.0, 10000.0), column("C3", 0.0, 0.0),
           column("C4", 0.0, 5000.0), column("C5", 6000.0, 0.0)]


def footings():
    shells = [square("a", 0.0, 10000.0), square("b", 6000.0, 10000.0, 2500.0),
              Shell("c", [(-1000.0, -1000.0), (1000.0, -1000.0), (1000.0, 6000.0),
                          (-1000.0, 6000.0)], -1000.0, 600.0),
              square("d", 6000.0, 0.0)]
    return fp.group_footings(shells, COLUMNS)


def test_isolated_footings_of_one_size_share_a_mark_and_a_combined_one_has_its_own():
    found = {tuple(f.columns): f for f in footings()}
    assert found[("C1",)].mark == "F1" and found[("C5",)].mark == "F1"       # 2000 x 2000 x 500
    assert found[("C2",)].mark == "F2"                                       # 2500 x 2500 x 500
    combined = found[("C3", "C4")]
    assert (combined.mark, combined.kind) == ("CF1", "combined")
    assert (combined.length, combined.width, combined.thickness) == (7000.0, 2000.0, 600.0)
    assert found[("C1",)].kind == "isolated"


def test_marks_follow_the_reading_order_of_the_plan():
    """From the top of the plan down, then left to right: the first size met is F1."""
    assert [f.mark for f in footings()] == ["F1", "F2", "CF1", "F1"]


def test_shells_that_touch_are_one_footing():
    """A mat or a footing drawn as several shells is one footing with one outline."""
    left = Shell("l", [(0.0, 0.0), (2000.0, 0.0), (2000.0, 3000.0), (0.0, 3000.0)], 0.0, 600.0)
    right = Shell("r", [(2000.0, 0.0), (5000.0, 0.0), (5000.0, 3000.0), (2000.0, 3000.0)], 0.0,
                  600.0)
    apart = Shell("s", [(9000.0, 0.0), (11000.0, 0.0), (11000.0, 2000.0), (9000.0, 2000.0)], 0.0,
                  500.0)
    found = fp.group_footings([left, right, apart],
                              [column("C1", 1000.0, 1500.0), column("C2", 4000.0, 1500.0)])
    assert len(found) == 2
    joined = next(f for f in found if len(f.shells) == 2)
    assert sorted(joined.shells) == ["l", "r"] and joined.kind == "combined"
    assert (joined.length, joined.width) == (5000.0, 3000.0)
    xs = [x for x, _ in joined.outline]
    assert (min(xs), max(xs)) == (0.0, 5000.0)
    assert next(f for f in found if f.shells == ["s"]).columns == []      # no column on it


def test_a_footing_keeps_its_true_sides_when_it_is_turned():
    import math

    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    turned = Shell("t", [(x * c - y * s, x * s + y * c) for x, y in
                         ((0, 0), (3000, 0), (3000, 1500), (0, 1500))], 0.0, 500.0)
    footing = fp.group_footings([turned], [])[0]
    assert footing.length == pytest.approx(3000.0, abs=0.5)
    assert footing.width == pytest.approx(1500.0, abs=0.5)


def test_a_tie_beam_with_an_etabs_number_gets_a_mark_and_a_named_one_keeps_its_name():
    beams = [TieBeam("12", 0.0, 0.0, 6000.0, 0.0, 0.0, 300.0, 500.0),
             TieBeam("FTB-2", 0.0, 0.0, 0.0, 5000.0, 0.0, 300.0, 500.0),
             TieBeam("7", 0.0, 10000.0, 6000.0, 10000.0, 0.0, 300.0, 500.0)]
    fp.name_tie_beams(beams)
    # the upper one first; FTB-2 is taken, so the lower one is FTB-3
    assert [b.mark for b in beams] == ["FTB-3", "FTB-2", "FTB-1"]


def test_the_sizes_are_listed_once_for_each_mark():
    assert fp.schedule_lines(footings()) == [
        "F1: 2,000 x 2,000 x 500 thk (2 footings)", "F2: 2,500 x 2,500 x 500 thk",
        "CF1: 7,000 x 2,000 x 600 thk, combined footing of C3, C4"]


def test_columns_come_from_the_file_of_the_foundation_model():
    manifest = {"supports": [
        {"joint": "1", "foundation_joint": "1", "x": 0.0, "y": 0.0, "z": -1000.0,
         "kind": "column", "member": "GF-C3", "width": 600.0, "depth": 800.0, "angle": 90.0},
        {"joint": "9", "x": 5.0, "y": 0.0, "z": 0.0, "kind": "wall", "member": "W1",
         "width": 0.0, "depth": 0.0}]}
    columns = fp.columns_of_manifest(manifest)
    assert len(columns) == 1          # a wall support has no column to draw
    assert (columns[0].mark, columns[0].name, columns[0].depth, columns[0].angle) == (
        "C3", "GF-C3", 800.0, 90.0)


def test_a_saved_plan_is_read_back_as_it_was(tmp_path):
    target = str(tmp_path / "T - FOUNDATION.EDB")
    beams = fp.name_tie_beams([TieBeam("3", 0.0, 0.0, 6000.0, 0.0, -1000.0, 300.0, 500.0,
                                       "C3", "C5")])
    assert fp.load_plan(target) is None
    path = fp.save_plan(target, footings(), beams, COLUMNS)
    assert path.endswith("T - FOUNDATION.plan.json")
    plan = fp.load_plan(target)
    assert [f.mark for f in plan["footings"]] == ["F1", "F2", "CF1", "F1"]
    assert plan["footings"][0].outline[0] == (-1000.0, 9000.0)
    assert plan["tie_beams"][0].mark == "FTB-1" and plan["columns"][2].mark == "C3"
    assert plan["saved"]


# --------------------------------------------------------------------------
# ON THE FRAMING PLANS
# --------------------------------------------------------------------------
def plan(tmp_path):
    target = str(tmp_path / "T - FOUNDATION.EDB")
    beams = fp.name_tie_beams([TieBeam("3", 0.0, 0.0, 6000.0, 0.0, -1000.0, 300.0, 500.0,
                                       "C3", "C5")])
    fp.save_plan(target, footings(), beams, COLUMNS)
    return fp.load_plan(target)


def test_the_foundation_is_a_floor_of_its_own_with_marks_and_sizes(tmp_path):
    floor = plans.foundation_floor(plan(tmp_path))
    assert floor.title == "FOUNDATION PLAN" and floor.elevation == -1000.0
    assert len(floor.footings) == 4 and len(floor.columns) == 5
    beam = floor.beams[0]
    assert (beam.name, beam.width, beam.girder) == ("FTB-1", 300.0, True)   # it ends on columns
    assert floor.notes[0] == "F1: 2,000 x 2,000 x 500 thk (2 footings)"


def test_the_plan_of_a_model_is_found_beside_its_foundation_model(tmp_path):
    model = str(tmp_path / "T.EDB")
    assert plans.saved_foundation_plan(model) is None
    plan(tmp_path)
    assert len(plans.saved_foundation_plan(model)["footings"]) == 4


def test_the_drawing_has_the_footings_their_marks_the_tie_beams_and_the_columns(tmp_path):
    import ezdxf

    foundation = plans.foundation_floor(plan(tmp_path))
    floor = plans.FloorPlan("2F", 3000.0, columns=[
        plans.PlanColumn("2-C3", 0.0, 0.0, 600.0, 600.0, top="t", bottom="b")])
    path = str(tmp_path / "plans.dxf")
    totals = plans.write_framing_plans([foundation, floor], [], path,
                                       plans.PlanOptions(column_marks=plans.BOTTOM_ONLY))
    assert totals["floors"] == 2
    msp = ezdxf.readfile(path).modelspace()
    on = lambda layer, kind: [e for e in msp if e.dxf.layer == layer and e.dxftype() == kind]  # noqa: E731
    outlines = on(plans.LAYER_FOOTING, "LWPOLYLINE")
    assert len(outlines) == 4 and all(e.closed for e in outlines)
    assert sorted(e.dxf.text for e in on(plans.LAYER_FOOTING, "TEXT")) == ["CF1", "F1", "F1", "F2"]
    tie_beams = on(plans.LAYER_TIE_BEAM, "MLINE")
    assert len(tie_beams) == 1 and tie_beams[0].dxf.scale_factor == 300.0
    # the tie beam stops at the faces of the two columns: 6000 less two halves of 600
    ends = [v.location for v in tie_beams[0].vertices]
    assert abs(ends[1].x - ends[0].x) == pytest.approx(5400.0)
    texts = [e.dxf.text for e in msp if e.dxftype() == "TEXT"]
    assert "FOUNDATION PLAN" in texts and "2F FRAMING PLAN" in texts and "FTB-1" in texts
    assert "CF1: 7,000 x 2,000 x 600 thk, combined footing of C3, C4" in texts
    # every column of the foundation plan has its mark, whatever the choice for the floors
    assert {"C1", "C2", "C3", "C4", "C5"} <= set(texts)
