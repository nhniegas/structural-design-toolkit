"""
tests/test_model_3d.py
======================
The 3D model (etabs_api/workflows/model_3d.py): where each solid is, which
members share a shape, what comes from the saved foundation plan, and the STEP
file that is written. No ETABS is needed; the tests of the file need build123d.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import foundation_plan as fp  # noqa: E402
from etabs_api.workflows import model_3d as m3  # noqa: E402
from etabs_api.workflows.model_3d import Bar, Model3D, Plate  # noqa: E402


# --------------------------------------------------------------------------
# WHERE THE SOLIDS ARE
# --------------------------------------------------------------------------
def test_a_column_runs_from_joint_to_joint_with_its_depth_along_its_angle():
    bar = m3.column_bar("GF-C1", "2F", (1000.0, 2000.0, 0.0), (1000.0, 2000.0, 3000.0),
                        400.0, 600.0, angle=90.0)
    assert (bar.kind, bar.length, bar.width, bar.depth) == ("column", 3000.0, 400.0, 600.0)
    assert bar.up == pytest.approx((0.0, 1.0, 0.0))      # the depth of 600 is along Y
    assert m3.column_bar("x", "2F", (0, 0, 0), (0, 0, 3000.0), 0.0, 0.0) is None


def test_a_beam_has_its_top_on_its_joints_and_stops_at_the_faces():
    bar = m3.beam_bar("B1", "2F", (0.0, 0.0, 3000.0), (6000.0, 0.0, 3000.0), 300.0, 500.0,
                      trims=(250.0, 300.0))
    assert bar.start == pytest.approx((250.0, 0.0, 2750.0))      # centre line: 250 below the top
    assert bar.end == pytest.approx((5700.0, 0.0, 2750.0))
    assert bar.up == pytest.approx((0.0, 0.0, 1.0))
    assert bar.length == pytest.approx(5450.0)


def test_a_beam_that_slopes_keeps_its_depth_square_to_it_and_its_cut_in_plan():
    bar = m3.beam_bar("R1", "RF", (0.0, 0.0, 0.0), (4000.0, 0.0, 3000.0), 300.0, 500.0,
                      trims=(400.0, 0.0))
    assert bar.up == pytest.approx((-0.6, 0.0, 0.8))
    # cut 400 in plan is 500 along a beam at 3 : 4, then 250 down square to it
    assert bar.start == pytest.approx((400.0 + 150.0, 0.0, 300.0 - 200.0))
    assert sum(a * b for a, b in zip(bar.up, (0.8, 0.0, 0.6))) == pytest.approx(0.0)


def test_a_beam_inside_its_supports_and_a_beam_with_no_depth_are_not_drawn():
    assert m3.beam_bar("B", "2F", (0, 0, 0), (500.0, 0, 0), 300.0, 500.0, (250.0, 250.0)) is None
    assert m3.beam_bar("B", "2F", (0, 0, 0), (5000.0, 0, 0), 300.0, 0.0) is None
    assert m3.beam_bar("B", "2F", (0, 0, 0), (0, 0, 3000.0), 300.0, 500.0) is None   # upright


def test_a_wall_is_its_thickness_by_its_height():
    bar = m3.wall_bar("W1", "2F", 0.0, 0.0, 0.0, 4000.0, 0.0, 3000.0, 250.0)
    assert (bar.width, bar.depth, bar.length) == (250.0, 3000.0, 4000.0)
    assert bar.start == (0.0, 0.0, 1500.0) and bar.up == (0.0, 0.0, 1.0)
    assert m3.wall_bar("W", "2F", 0.0, 0.0, 0.0, 4000.0, 0.0, 0.0, 250.0) is None


def test_members_of_one_size_are_one_shape():
    a = m3.column_bar("C1", "2F", (0, 0, 0), (0, 0, 3000.0), 400.0, 600.0)
    b = m3.column_bar("C2", "3F", (5000.0, 0, 3000.0), (5000.0, 0, 6000.0), 400.0, 600.0, 90.0)
    c = m3.column_bar("C3", "2F", (0, 0, 0), (0, 0, 3500.0), 400.0, 600.0)
    assert m3.bar_shape(a) == m3.bar_shape(b) != m3.bar_shape(c)
    one = Plate("slab", "1", "2F", [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], 3000.0, 150.0)
    two = Plate("slab", "2", "3F", [(4000, 0), (8000, 0), (8000, 3000), (4000, 3000)], 6000.0,
                150.0)
    thick = Plate("slab", "3", "2F", one.outline, 3000.0, 200.0)
    assert m3.plate_shape(one) == m3.plate_shape(two) != m3.plate_shape(thick)


def test_an_outline_has_no_corner_twice():
    assert m3.clean_outline([(0, 0), (0, 0.2), (1000, 0), (1000, 1000), (0, 0)]) == [
        (0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]


def test_a_slab_belongs_to_the_story_at_its_level():
    stories = [("Base", 0.0), ("2F", 3000.0), ("3F", 6000.0)]
    assert m3.nearest_story(2999.5, stories) == "2F" and m3.nearest_story(5000.0, stories) == "3F"


def test_square_up_of_a_direction_along_the_axis_is_still_square():
    up = m3.square_up((0.0, 0.0, 1.0), (0.0, 0.0, 1.0))
    assert up[2] == pytest.approx(0.0) and math.hypot(*up[:2]) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# THE FOUNDATION
# --------------------------------------------------------------------------
def foundation_plan(tmp_path):
    columns = [fp.PlanColumn("C1", "GF-C1", "1", 0.0, 0.0, -1000.0, 600.0, 600.0),
               fp.PlanColumn("C2", "GF-C2", "2", 6000.0, 0.0, -1000.0, 600.0, 600.0)]
    half = 1000.0
    shells = [fp.Shell(str(i), [(x - half, -half), (x + half, -half), (x + half, half),
                                (x - half, half)], -1000.0, 500.0) for i, x in enumerate((0.0, 6000.0))]
    beams = fp.name_tie_beams([fp.TieBeam("5", 0.0, 0.0, 6000.0, 0.0, -1000.0, 300.0, 500.0,
                                          "1", "2")])
    target = str(tmp_path / "T - FOUNDATION.EDB")
    fp.save_plan(target, fp.group_footings(shells, columns), beams, columns)
    return fp.load_plan(target)


def test_the_foundation_gives_footings_below_their_level_and_tie_beams_between_the_columns(
        tmp_path):
    bars, plates = m3.foundation_parts(foundation_plan(tmp_path))
    assert [(p.kind, p.name, p.group, p.top, p.thickness) for p in plates] == [
        ("footing", "F1", "FOUNDATION", -1000.0, 500.0)] * 2
    tie = bars[0]
    assert (tie.kind, tie.name, tie.group) == ("tie beam", "FTB-1", "FOUNDATION")
    # from the face of one column to the face of the other, its top at the joints
    assert tie.start == pytest.approx((300.0, 0.0, -1250.0))
    assert tie.end == pytest.approx((5700.0, 0.0, -1250.0))


# --------------------------------------------------------------------------
# THE FILE
# --------------------------------------------------------------------------
def frame(tmp_path=None) -> Model3D:
    model = Model3D()
    for story, base in (("2F", 0.0), ("3F", 3000.0)):
        for name, x in (("C1", 0.0), ("C2", 6000.0)):
            model.bars.append(m3.column_bar(f"{story}-{name}", story, (x, 0.0, base),
                                            (x, 0.0, base + 3000.0), 400.0, 600.0))
        model.bars.append(m3.beam_bar(f"{story}-B1", story, (0.0, 0.0, base + 3000.0),
                                      (6000.0, 0.0, base + 3000.0), 300.0, 500.0, (300.0, 300.0)))
        model.plates.append(Plate("slab", f"{story}-S1", story, [
            (0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0)], base + 3000.0, 150.0))
    model.bars.append(Bar("column", "RC", "2F", (3000.0, 4000.0, 0.0), (3000.0, 4000.0, 3000.0),
                          500.0, 500.0, (1.0, 0.0, 0.0), circular=True))
    if tmp_path is not None:
        bars, plates = m3.foundation_parts(foundation_plan(tmp_path))
        model.bars += bars
        model.plates += plates
    return model


def test_the_solids_are_where_the_members_are():
    pytest.importorskip("build123d")
    assembly, shapes = m3.build_assembly(frame(), "T")
    assert [group.label for group in assembly.children] == ["2F", "3F"]
    by_name = {solid.label: solid for group in assembly.children for solid in group.children}
    assert len(by_name) == 9 and shapes == 4      # column, beam, slab, round column

    def box(label):
        b = by_name[label].bounding_box()
        return (round(b.min.X), round(b.min.Y), round(b.min.Z),
                round(b.max.X), round(b.max.Y), round(b.max.Z))

    assert box("COLUMN 3F-C2") == (5700, -200, 3000, 6300, 200, 6000)     # depth 600 along X
    assert box("BEAM 2F-B1") == (300, -150, 2500, 5700, 150, 3000)        # top at the floor
    assert box("SLAB 3F-S1") == (0, 0, 5850, 6000, 4000, 6000)            # down from the floor
    assert box("COLUMN RC") == (2750, 3750, 0, 3250, 4250, 3000)
    assert by_name["COLUMN 2F-C1"].volume == pytest.approx(400.0 * 600.0 * 3000.0)
    assert by_name["COLUMN RC"].volume == pytest.approx(math.pi * 250.0**2 * 3000.0, rel=1e-6)


def test_a_turned_column_has_its_depth_along_its_angle():
    pytest.importorskip("build123d")
    model = Model3D(bars=[m3.column_bar("C", "2F", (0, 0, 0), (0, 0, 3000.0), 400.0, 600.0, 90.0)])
    solid = m3.build_assembly(model)[0].children[0].children[0]
    b = solid.bounding_box()
    assert (round(b.max.X), round(b.max.Y)) == (200, 300)


def test_the_step_file_holds_every_member_by_name_and_one_shape_for_each_size(tmp_path):
    bd = pytest.importorskip("build123d")
    model = frame(tmp_path)
    path = str(tmp_path / "T - 3D Model.step")
    totals = m3.write_step(model, path, "T")
    assert totals["solids"] == 12 and totals["shapes"] == 6
    assert (totals["column"], totals["beam"], totals["slab"], totals["footing"],
            totals["tie beam"]) == (5, 2, 2, 2, 1)
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for name in ("COLUMN 2F-C1", "BEAM 3F-B1", "SLAB 2F-S1", "FOOTING F1", "TIE BEAM FTB-1",
                 "FOUNDATION"):
        assert f"'{name}'" in text
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
    back = bd.import_step(path)
    assert len(back.solids()) == 12
    b = back.bounding_box()
    assert (round(b.min.X), round(b.min.Z), round(b.max.X), round(b.max.Z)) == (
        -1000, -1500, 7000, 6000)


def test_a_solid_that_cannot_be_made_is_counted_and_the_rest_is_written(tmp_path):
    pytest.importorskip("build123d")
    model = frame()
    model.plates.append(Plate("slab", "bad", "2F", [(0.0, 0.0), (1000.0, 0.0)], 3000.0, 150.0))
    totals = m3.write_step(model, str(tmp_path / "m.step"))
    assert totals["solids"] == 9 and model.left_out == {m3.NOT_MADE: 1}


def test_the_summary_says_what_was_left_out(tmp_path):
    model = Model3D(sections={"W10X49"})
    model.skip("beams", 3)
    model.skip(m3.NOT_SLABS, 2)
    totals = dict.fromkeys(m3.KINDS, 0) | {"solids": 0, "shapes": 0, "size": 1_500_000}
    text = m3.model_summary(totals, model, "m.EDB", "m.step", True, None).text()
    assert "3 beams are not drawn" in text and "W10X49" in text
    assert "2 floors are not concrete slabs" in text and "1.5 MB" in text
    assert "not included" in text      # the foundation
