"""Framing plans as DXF: the geometry and the file written (no ETABS)."""

import math
from collections import Counter

import ezdxf
import pytest

from etabs_api.workflows import framing_plans as fp


def square(name, x, y, top, size=500.0, angle=0.0, bottom=""):
    return fp.PlanColumn(name, x, y, size, size, angle, False, top, bottom or top + "0")


def bay() -> fp.FloorPlan:
    """One bay, 6 m by 5 m: girders on four columns, the X girders split at mid
    span by a beam along Y that they carry."""
    columns = [square("GF-C1", 0, 0, "a"), fp.PlanColumn("GF-C2", 6000, 0, 400, 800, 0, False, "b", "b0"),
               fp.PlanColumn("GF-C3", 0, 5000, 600, 600, 0, True, "c", "c0"),
               square("GF-C4", 6000, 5000, "d")]
    beams = [
        fp.PlanBeam("GFGX-1", 0, 0, 3000, 0, 300, "a", "m", True),
        fp.PlanBeam("GFGX-1A", 3000, 0, 6000, 0, 300, "m", "b", True),
        fp.PlanBeam("GFGY-1", 0, 0, 0, 5000, 400, "a", "c", True),
        fp.PlanBeam("GFBY-1", 3000, 0, 3000, 5000, 250, "m", "n", False),
        fp.PlanBeam("GFGX-2", 0, 5000, 3000, 5000, 300, "c", "n", True),
        fp.PlanBeam("GFGX-2A", 3000, 5000, 6000, 5000, 300, "n", "d", True),
    ]
    return fp.FloorPlan("GF", 0.0, beams, columns, [fp.PlanWall("W1", 6000, 1000, 6000, 4000, 200)])


# ----------------------------------------------------------------- marks
@pytest.mark.parametrize("name, mark", [
    ("GF-C1", "C1"), ("2-C1A", "C1A"), ("RD-C12", "C12"), ("PD1-C3", "C3"),
    ("5031", "5031"), ("C7", "C7"),
])
def test_a_column_mark_is_the_name_without_its_level(name, mark):
    assert fp.column_mark(name) == mark


def test_marks_on_every_floor_or_only_where_a_column_starts():
    lower = fp.FloorPlan("GF", 0.0, columns=[square("GF-C1", 0, 0, "a"), square("GF-C2", 6000, 0, "b")])
    upper = fp.FloorPlan("2F", 3000.0, columns=[square("2-C1", 0, 0, "e"), square("2-C2", 6000, 0, "f"),
                                                square("2-C9", 3000, 0, "g")])   # planted on GF
    every = fp.marked_columns([lower, upper], fp.EVERY_LEVEL)
    assert len(every) == 5
    bottom = fp.marked_columns([lower, upper], fp.BOTTOM_ONLY)
    assert bottom == {("GF", "GF-C1"), ("GF", "GF-C2"), ("2F", "2-C9")}


# ----------------------------------------------------------------- column faces
def test_the_face_of_a_rectangular_column_follows_its_rotation():
    column = fp.PlanColumn("C", 0, 0, 400, 800, 0, False)      # 800 deep along X, 400 wide
    assert fp.face_distance(column, (1.0, 0.0)) == pytest.approx(400.0)
    assert fp.face_distance(column, (0.0, 1.0)) == pytest.approx(200.0)
    turned = fp.PlanColumn("C", 0, 0, 400, 800, 90, False)     # the depth now along Y
    assert fp.face_distance(turned, (1.0, 0.0)) == pytest.approx(200.0)
    assert fp.face_distance(turned, (0.0, 1.0)) == pytest.approx(400.0)
    diagonal = (math.cos(math.radians(45)), math.sin(math.radians(45)))
    assert fp.face_distance(square("S", 0, 0, "j"), diagonal) == pytest.approx(250.0 * math.sqrt(2))


def test_the_face_of_a_circular_column_is_its_radius_in_every_direction():
    column = fp.PlanColumn("C", 0, 0, 600, 600, 0, True)
    assert fp.face_distance(column, (0.6, 0.8)) == pytest.approx(300.0)


def test_the_outline_of_a_rotated_column():
    corners = fp.column_outline(fp.PlanColumn("C", 1000, 2000, 400, 800, 90, False))
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    assert (min(xs), max(xs)) == pytest.approx((800.0, 1200.0))    # 400 wide across Y's depth
    assert (min(ys), max(ys)) == pytest.approx((1600.0, 2400.0))


# ----------------------------------------------------------------- where a beam stops
def test_beams_stop_at_column_faces_and_at_the_girder_that_carries_them():
    trims = fp.beam_trims(bay())
    assert trims["GFGX-1"] == pytest.approx((250.0, 0.0))     # column face; continues at mid span
    assert trims["GFGX-1A"] == pytest.approx((0.0, 400.0))    # the 800 deep column
    assert trims["GFGY-1"] == pytest.approx((250.0, 300.0))   # square column; circular column
    assert trims["GFBY-1"] == pytest.approx((150.0, 150.0))   # half the 300 mm girder each end


def test_a_beam_ends_at_the_trimmed_points_and_a_beam_inside_a_column_is_dropped():
    floor = bay()
    ends = fp.trimmed_ends(floor.beams[0], fp.beam_trims(floor)["GFGX-1"])
    assert ends == ((pytest.approx(250.0), pytest.approx(0.0)), (pytest.approx(3000.0), pytest.approx(0.0)))
    stub = fp.PlanBeam("S", 0, 0, 400, 0, 300, "a", "z")
    assert fp.trimmed_ends(stub, (250.0, 250.0)) is None


def test_two_beams_meeting_at_a_corner_without_a_column_are_not_cut():
    floor = fp.FloorPlan("2F", 0.0, beams=[
        fp.PlanBeam("B1", 0, 0, 3000, 0, 300, "p", "q"), fp.PlanBeam("B2", 3000, 0, 3000, 4000, 300, "q", "r")])
    assert fp.beam_trims(floor) == {"B1": (0.0, 0.0), "B2": (0.0, 0.0)}


def test_beams_stop_at_a_column_that_starts_on_the_floor():
    planted = square("3-C9", 3000, 0, "t", bottom="m")
    floor = fp.FloorPlan("2F", 0.0, beams=[fp.PlanBeam("B1", 0, 0, 3000, 0, 300, "p", "m")],
                         columns_above=[planted])
    assert fp.beam_trims(floor)["B1"] == pytest.approx((0.0, 250.0))


# ----------------------------------------------------------------- grids and text
def test_a_grid_line_runs_across_the_box():
    line = fp.grid_in_box(fp.PlanGrid("A", 2000, 0, 2000, 100), (-500, -500, 6500, 5500))
    assert (line.x1, line.y1, line.x2, line.y2) == pytest.approx((2000, -500, 2000, 5500))
    skew = fp.grid_in_box(fp.PlanGrid("S", 0, 0, 1000, 1000), (0, 0, 4000, 2000))
    assert (skew.x2, skew.y2) == pytest.approx((2000, 2000))
    outside = fp.PlanGrid("Z", 9000, 0, 9000, 100)
    assert fp.grid_in_box(outside, (0, 0, 4000, 2000)) is outside


@pytest.mark.parametrize("dx, dy, angle", [(1, 0, 0), (0, 1, 90), (-1, 0, 0), (0, -1, 90),
                                           (1, 1, 45), (-1, -1, 45), (-1, 1, -45)])
def test_text_along_a_beam_reads_from_the_bottom_or_the_right(dx, dy, angle):
    assert fp.readable_angle(dx, dy) == pytest.approx(angle)


def test_the_text_height_follows_the_plot_scale():
    assert fp.PlanOptions(scale=100).text_height == 250.0
    assert fp.PlanOptions(scale=50).text_height == 125.0


# ----------------------------------------------------------------- the file
def written(tmp_path, options, floors=None, grids=None):
    floors = floors or [bay(), fp.FloorPlan("2F", 3000.0, bay().beams, bay().columns)]
    grids = grids if grids is not None else [
        fp.PlanGrid("A", 0, -1000, 0, 6000), fp.PlanGrid("B", 6000, -1000, 6000, 6000),
        fp.PlanGrid("1", -1000, 0, 7000, 0), fp.PlanGrid("2", -1000, 5000, 7000, 5000)]
    path = tmp_path / "plans.dxf"
    totals = fp.write_framing_plans(floors, grids, str(path), options)
    doc = ezdxf.readfile(str(path))
    return totals, doc, list(doc.modelspace())


def test_the_dxf_has_multilines_at_the_beam_width_on_the_girder_and_beam_layers(tmp_path):
    totals, doc, entities = written(tmp_path, fp.PlanOptions(grids=False))
    assert totals == {"floors": 2, "beams": 12, "single_line": 0, "columns": 8, "walls": 1}
    assert not doc.audit().errors
    multilines = [e for e in entities if e.dxftype() == "MLINE"]
    assert len(multilines) == 12
    assert Counter(e.dxf.layer for e in multilines) == {fp.LAYER_GIRDER: 10, fp.LAYER_BEAM: 2}
    assert sorted({e.dxf.scale_factor for e in multilines}) == [250.0, 300.0, 400.0]
    style = doc.mline_styles.get(fp.BEAM_STYLE)
    assert [(e.offset, e.linetype) for e in style.elements.elements] == [(0.5, "HIDDEN"),
                                                                         (-0.5, "HIDDEN")]
    first = multilines[0]
    assert first.dxf.justification == 1                       # on the centre line
    assert [round(v.location.x) for v in first.vertices] == [250, 3000]   # cut at the column face


def test_columns_and_walls_are_solid(tmp_path):
    _, _, entities = written(tmp_path, fp.PlanOptions(grids=False))
    hatches = Counter(e.dxf.layer for e in entities if e.dxftype() == "HATCH")
    assert hatches == {fp.LAYER_COLUMN: 8, fp.LAYER_WALL: 1}
    assert sum(1 for e in entities if e.dxftype() == "CIRCLE") == 2   # the circular column, twice


def test_marks_titles_and_the_floors_side_by_side(tmp_path):
    _, _, entities = written(tmp_path, fp.PlanOptions(grids=False, column_marks=fp.BOTTOM_ONLY))
    texts = Counter(e.dxf.text for e in entities if e.dxftype() == "TEXT")
    assert texts["GFGX-1"] == 2 and texts["GFBY-1"] == 2          # a name on every floor
    assert texts["C1"] == 1 and texts["C3"] == 1                  # marked where they start
    assert texts["GF FRAMING PLAN"] == 1 and texts["2F FRAMING PLAN"] == 1
    assert texts["ELEV. +3.00 m"] == 1
    starts = sorted(round(e.vertices[0].location.x) for e in entities
                    if e.dxftype() == "MLINE" and e.dxf.scale_factor == 400.0)
    assert starts[0] == 0 and starts[1] > 6000                    # the second floor is beside


def test_grids_with_bubbles_and_dimensions_when_asked(tmp_path):
    _, _, plain = written(tmp_path, fp.PlanOptions(grids=True, dimensions=False),
                          floors=[bay()])
    assert sum(1 for e in plain if e.dxf.layer == fp.LAYER_GRID and e.dxftype() == "LINE") == 4
    assert sum(1 for e in plain if e.dxf.layer == fp.LAYER_GRID and e.dxftype() == "CIRCLE") == 4
    assert not [e for e in plain if e.dxftype() == "DIMENSION"]
    _, _, dimensioned = written(tmp_path, fp.PlanOptions(grids=True, dimensions=True),
                                floors=[bay()])
    dimensions = [e for e in dimensioned if e.dxftype() == "DIMENSION"]
    assert len(dimensions) == 2                                   # A to B, and 1 to 2
    assert sorted(round(d.get_measurement()) for d in dimensions) == [5000, 6000]


def test_three_grids_get_the_overall_dimension_too(tmp_path):
    grids = [fp.PlanGrid(label, x, -1000, x, 6000) for label, x in (("A", 0), ("B", 3000), ("C", 6000))]
    _, _, entities = written(tmp_path, fp.PlanOptions(grids=True, dimensions=True),
                             floors=[bay()], grids=grids)
    assert sorted(round(e.get_measurement()) for e in entities
                  if e.dxftype() == "DIMENSION") == [3000, 3000, 6000]


def test_a_beam_that_is_not_a_rectangle_is_one_dashed_line(tmp_path):
    floor = fp.FloorPlan("2F", 0.0, beams=[fp.PlanBeam("W14", 0, 0, 5000, 0, 0.0, "p", "q")])
    totals, _, entities = written(tmp_path, fp.PlanOptions(grids=False), floors=[floor], grids=[])
    assert totals["single_line"] == 1
    lines = [e for e in entities if e.dxftype() == "LINE"]
    assert len(lines) == 1 and lines[0].dxf.linetype == "HIDDEN"


# ----------------------------------------------------------------- reading the model
class FakeTables:
    def __init__(self, tables):
        self.tables = tables

    def GetTableForDisplayArray(self, key, *_):
        rows = self.tables.get(key, [])
        if not rows:
            return (0, [], ["None"], 0, [], 0)
        fields = list(dict.fromkeys(key for row in rows for key in row))   # every column
        return (0, [], fields, len(rows), [row.get(f) for row in rows for f in fields], 0)


class FakeModel:
    def __init__(self, tables):
        self.DatabaseTables = FakeTables(tables)

    class Story:
        @staticmethod
        def GetStories():
            return (2, ["Base", "GF", "2F"], [-1000.0, 0.0, 3000.0], [0, 1000.0, 3000.0])


def model_tables() -> dict:
    return {
        "Point Object Connectivity": [
            {"UniqueName": n, "X": x, "Y": y, "Z": z} for n, x, y, z in (
                ("1", 0, 0, -1000), ("2", 0, 0, 0), ("3", 6000, 0, -1000), ("4", 6000, 0, 0),
                ("5", 0, 0, 3000), ("6", 6000, 0, 3000), ("7", 3000, 0, 3000), ("8", 3000, 4000, 3000))],
        "Frame Assignments - Summary": [
            {"UniqueName": "GF-C1", "AnalysisSect": "COL 500", "AxisAngle": None},
            {"UniqueName": "GF-C2", "AnalysisSect": "COL D600", "AxisAngle": None},
            {"UniqueName": "2-C1", "AnalysisSect": "COL 500", "AxisAngle": 30},
            {"UniqueName": "2-C2", "AnalysisSect": "COL D600", "AxisAngle": None},
            {"UniqueName": "GFGX-1", "AnalysisSect": "RB 300x500", "AxisAngle": None},
            {"UniqueName": "2GX-1", "AnalysisSect": "RB 300x500", "AxisAngle": None},
            {"UniqueName": "2GX-1A", "AnalysisSect": "RB 300x500", "AxisAngle": None},
            {"UniqueName": "2BY-1", "AnalysisSect": "W14X90", "AxisAngle": None}],
        "Frame Section Property Definitions - Concrete Rectangular": [
            {"Name": "COL 500", "t3": 500, "t2": 500}, {"Name": "RB 300x500", "t3": 500, "t2": 300}],
        "Frame Section Property Definitions - Concrete Circle": [{"Name": "COL D600", "t3": 600}],
        "Column Object Connectivity": [
            {"UniqueName": "GF-C1", "Story": "GF", "UniquePtI": "1", "UniquePtJ": "2"},
            {"UniqueName": "GF-C2", "Story": "GF", "UniquePtI": "3", "UniquePtJ": "4"},
            {"UniqueName": "2-C1", "Story": "2F", "UniquePtI": "2", "UniquePtJ": "5"},
            {"UniqueName": "2-C2", "Story": "2F", "UniquePtI": "4", "UniquePtJ": "6"}],
        "Beam Object Connectivity": [
            {"UniqueName": "GFGX-1", "Story": "GF", "UniquePtI": "2", "UniquePtJ": "4"},
            {"UniqueName": "2GX-1", "Story": "2F", "UniquePtI": "5", "UniquePtJ": "7"},
            {"UniqueName": "2GX-1A", "Story": "2F", "UniquePtI": "7", "UniquePtJ": "6"},
            {"UniqueName": "2BY-1", "Story": "2F", "UniquePtI": "7", "UniquePtJ": "8"}],
        "Grid Definitions - General": [{"Name": "G1", "Ux": 0, "Uy": 0, "Rz": 0},
                                       {"Name": "G2", "Ux": 1000, "Uy": 2000, "Rz": 90}],
        "Grid Definitions - Grid Lines": [
            {"Name": "G1", "LineType": "General (Cartesian)", "ID": "A", "X1": 0, "Y1": -2500,
             "X2": 0, "Y2": 6500, "BubbleLoc": "Start", "Visible": "Yes"},
            {"Name": "G1", "LineType": "X (Cartesian)", "ID": "B", "Ordinate": 6000,
             "BubbleLoc": "End", "Visible": "Yes"},
            {"Name": "G1", "LineType": "Y (Cartesian)", "ID": "1", "Ordinate": 0,
             "BubbleLoc": "Start", "Visible": "Yes"},
            {"Name": "G1", "LineType": "Y (Cartesian)", "ID": "hidden", "Ordinate": 9,
             "BubbleLoc": "Start", "Visible": "No"},
            {"Name": "G2", "LineType": "General (Cartesian)", "ID": "R", "X1": 0, "Y1": 0,
             "X2": 1000, "Y2": 0, "BubbleLoc": "Start", "Visible": "Yes"}],
    }


def test_the_floors_are_read_from_the_model_with_sizes_angles_and_girders():
    floors, grids, single_line = fp.read_floor_plans(FakeModel(model_tables()))
    assert [f.story for f in floors] == ["GF", "2F"] and floors[1].elevation == 3000.0
    ground, second = floors
    assert [(c.name, c.width, c.circular) for c in ground.columns] == [
        ("GF-C1", 500.0, False), ("GF-C2", 600.0, True)]
    assert second.columns[0].angle == 30.0 and second.columns[0].top == "5"
    by_name = {b.name: b for b in second.beams}
    assert by_name["2GX-1"].girder and by_name["2GX-1"].width == 300.0
    assert not by_name["2BY-1"].girder and by_name["2BY-1"].width == 0.0   # not a rectangle
    assert single_line == ["W14X90"]
    # the columns of the story above start on the GF floor's own columns: none is planted
    assert ground.columns_above == []


def test_grid_lines_are_read_in_global_coordinates():
    _, grids, _ = fp.read_floor_plans(FakeModel(model_tables()))
    by_label = {g.label: g for g in grids}
    assert set(by_label) == {"A", "B", "1", "R"}                   # the hidden one is left out
    assert (by_label["A"].x1, by_label["A"].y1, by_label["A"].y2) == (0, -2500, 6500)
    assert by_label["B"].x1 == by_label["B"].x2 == 6000 and by_label["B"].bubble_at_end
    assert by_label["1"].y1 == by_label["1"].y2 == 0
    # a line of a system at (1000, 2000) turned 90 degrees: local X becomes global Y
    turned = by_label["R"]
    assert (turned.x1, turned.y1) == pytest.approx((1000.0, 2000.0))
    assert (turned.x2, turned.y2) == pytest.approx((1000.0, 3000.0))
