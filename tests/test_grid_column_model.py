"""
tests/test_grid_column_model.py
===============================
Checks for etabs_api/workflows/grid_column_model.py: reading framing plans from a DXF
file and working out what must change in the model. Neither needs ETABS.

The drawings are made here with ezdxf, following the rules in the module.
"""

import math
import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import grid_column_model as gc  # noqa: E402

X_GRIDS = (("A", 0.0), ("B", 6000.0), ("C", 12000.0))
Y_GRIDS = (("1", 0.0), ("2", 5000.0))


def _box(x, y, width, depth, angle=0.0):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(x + a * c - b * s, y + a * s + b * c)
            for a, b in ((-width / 2, -depth / 2), (width / 2, -depth / 2),
                         (width / 2, depth / 2), (-width / 2, depth / 2))]


def write_plans(path, plans, x_grids=X_GRIDS, y_grids=Y_GRIDS, walls=()):
    """``plans`` is a list of (story, height, columns); a column is a dict with x, y and
    either diameter or width, depth and angle. Plans are placed left to right."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    def text(value, x, y, layer):
        msp.add_text(value, dxfattribs={"layer": layer, "height": 300}).set_placement(
            (x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    for index, (story, height, columns) in enumerate(plans):
        ox = 100000.0 + index * 40000.0          # away from the drawing origin on purpose
        oy = 50000.0
        msp.add_lwpolyline([(ox - 8000, oy - 8000), (ox + 20000, oy - 8000),
                            (ox + 20000, oy + 13000), (ox - 8000, oy + 13000)],
                           close=True, dxfattribs={"layer": "S-STORY"})
        text(f"STORY {story}   HEIGHT {height:g}", ox + 6000, oy - 7000, "S-STORY")
        msp.add_point((ox, oy), dxfattribs={"layer": "S-ORIGIN"})
        for label, x in x_grids:
            msp.add_line((ox + x, oy - 2000), (ox + x, oy + 7000), dxfattribs={"layer": "S-GRID"})
            text(label, ox + x, oy + 7600, "S-GRID")
        for label, y in y_grids:
            msp.add_line((ox - 2000, oy + y), (ox + x_grids[-1][1] + 2000, oy + y),
                         dxfattribs={"layer": "S-GRID"})
            text(label, ox - 2600, oy + y, "S-GRID")
        for column in columns:
            if "diameter" in column:
                msp.add_circle((ox + column["x"], oy + column["y"]), column["diameter"] / 2,
                               dxfattribs={"layer": "S-COLUMN"})
            else:
                msp.add_lwpolyline(
                    _box(ox + column["x"], oy + column["y"], column["width"], column["depth"],
                         column.get("angle", 0.0)),
                    close=True, dxfattribs={"layer": "S-COLUMN"})
        for wall in walls:  # (points, width, closed)
            points, width, closed = wall
            msp.add_lwpolyline([(ox + x, oy + y) for x, y in points], close=closed,
                               dxfattribs={"layer": "S-WALL", "const_width": width})
    doc.saveas(path)
    return str(path)


def on_grids(size=600.0, skip=(), x_grids=X_GRIDS, y_grids=Y_GRIDS):
    return [{"x": x, "y": y, "width": size, "depth": size}
            for gx, x in x_grids for gy, y in y_grids if f"{gx}-{gy}" not in skip]


# --------------------------------------------------------------------------
# READING
# --------------------------------------------------------------------------
def test_plans_are_read_left_to_right_as_lowest_story_first(tmp_path):
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids()), ("RD", 3500, on_grids())])
    building = gc.read_framing_plans(path)
    assert building.stories == [("2F", 4500.0), ("RD", 3500.0)]
    assert len(building.columns) == 12 and not building.warnings


def test_positions_are_measured_from_the_origin_point_of_each_plan(tmp_path):
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids()), ("RD", 3500, on_grids())])
    building = gc.read_framing_plans(path)
    for story in ("2F", "RD"):
        places = sorted((c.x, c.y) for c in building.columns if c.story == story)
        assert places[0] == pytest.approx((0.0, 0.0)) and places[-1] == pytest.approx((12000.0, 5000.0))


def test_grid_lines_take_the_label_at_their_end(tmp_path):
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())])
    grids = {g.label: g for g in gc.read_framing_plans(path).grids}
    assert sorted(grids) == ["1", "2", "A", "B", "C"]
    assert (grids["B"].x1, grids["B"].x2) == pytest.approx((6000.0, 6000.0))
    assert (grids["2"].y1, grids["2"].y2) == pytest.approx((5000.0, 5000.0))


def test_rectangular_column_gives_size_and_rotation():
    """800 along X by 600: depth 800 along the local 2 axis at 0 degrees."""
    column = gc.rectangle_column("2F", _box(0, 0, 800, 600))
    assert (column.width, column.depth, column.angle) == (600, 800, 0.0)


def test_rotation_is_the_same_whichever_corner_the_outline_starts_from():
    box = _box(1000, 2000, 800, 600, 30.0)
    readings = {
        (c.width, c.depth, round(c.angle, 2))
        for c in (gc.rectangle_column("2F", box[i:] + box[:i]) for i in range(4))
    }
    assert readings == {(600, 800, 30.0)}
    turned = gc.rectangle_column("2F", _box(0, 0, 800, 600, 120.0))   # same as 600 x 800 at 30
    assert (turned.width, turned.depth, round(turned.angle, 2)) == (800, 600, 30.0)


def test_circular_and_rotated_columns_are_read_from_the_drawing(tmp_path):
    columns = [{"x": 0, "y": 0, "diameter": 700},
               {"x": 6000, "y": 0, "width": 800, "depth": 600, "angle": 25.0}]
    building = gc.read_framing_plans(write_plans(tmp_path / "p.dxf", [("2F", 4500, columns)]))
    round_one = next(c for c in building.columns if c.circular)
    turned = next(c for c in building.columns if not c.circular)
    assert (round_one.depth, round_one.x) == (700, pytest.approx(0.0))
    assert (turned.width, turned.depth, turned.angle) == (600, 800, pytest.approx(25.0))


def test_drawing_without_plans_or_origin_is_refused(tmp_path):
    empty = tmp_path / "empty.dxf"
    ezdxf.new("R2010").saveas(empty)
    with pytest.raises(ValueError, match="No plan boundary"):
        gc.read_framing_plans(str(empty))
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())])
    with pytest.raises(ValueError, match="exactly one point"):
        gc.read_framing_plans(path, {"origin": "NO-SUCH-LAYER"})


def test_section_names_follow_the_office_convention():
    rect = gc.PlanColumn("2F", 0, 0, width=600, depth=800)
    circle = gc.PlanColumn("2F", 0, 0, width=700, depth=700, circular=True)
    assert gc.section_of(rect, 5, 60)["name"] == "CR_600X800_C05_G60"
    assert gc.section_of(circle, 6, 60) == {
        "name": "C_700_C06_G60", "kind": "circle", "width": 700, "depth": 700,
        "material": "C06", "rebar": "G60"}


# --------------------------------------------------------------------------
# COMPARING WITH THE MODEL
# --------------------------------------------------------------------------
def as_model(building, concrete=5, rebar=60):
    """The model as it would be after the drawing was built into it."""
    return [
        gc.ExistingColumn(f"col{i}", c.story, c.x, c.y,
                          gc.section_of(c, concrete, rebar)["name"], c.angle, f"b{i}", f"t{i}")
        for i, c in enumerate(building.columns)
    ]


def revise(tmp_path, columns, x_grids=X_GRIDS):
    first = gc.read_framing_plans(write_plans(tmp_path / "a.dxf", [("2F", 4500, on_grids())]))
    second = gc.read_framing_plans(
        write_plans(tmp_path / "b.dxf", [("2F", 4500, columns)], x_grids=x_grids))
    return first, second, gc.plan_changes(as_model(first), first.grids, second, 5, 60)


def test_empty_model_gets_every_column_added(tmp_path):
    building = gc.read_framing_plans(write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())]))
    changes = gc.plan_changes([], [], building, 5, 60)
    assert len(changes.added) == 6 and not (changes.moved or changes.removed or changes.updated)


def test_same_drawing_again_changes_nothing(tmp_path):
    _, _, changes = revise(tmp_path, on_grids())
    assert len(changes.unchanged) == 6 and not changes.anything


def test_columns_follow_a_grid_that_moves(tmp_path):
    """Grid B moves 1500 mm: its two columns are moved, not removed and added."""
    moved_grids = (("A", 0.0), ("B", 7500.0), ("C", 12000.0))
    _, second, changes = revise(tmp_path, on_grids(x_grids=moved_grids), moved_grids)
    assert len(changes.moved) == 2 and len(changes.unchanged) == 4
    assert not (changes.added or changes.removed)
    assert {new.x for _, new in changes.moved} == {7500.0}
    assert all(old.x == 6000.0 for old, _ in changes.moved)


def test_every_column_is_followed_when_the_whole_grid_shifts_far(tmp_path):
    """A shift larger than the nearest-neighbour limit is still matched, by grid labels."""
    shifted = tuple((label, x + 7000.0) for label, x in X_GRIDS)   # stays inside the plan frame
    _, _, changes = revise(tmp_path, on_grids(x_grids=shifted), shifted)
    assert len(changes.moved) == 6 and not (changes.added or changes.removed)


def test_resized_column_is_updated_in_place(tmp_path):
    columns = on_grids()
    columns[0] = {**columns[0], "width": 900.0, "depth": 700.0}
    _, _, changes = revise(tmp_path, columns)
    assert len(changes.updated) == 1 and len(changes.unchanged) == 5
    assert changes.updated[0][1].depth == 900 and not changes.moved


def test_added_and_removed_columns_are_listed_as_such(tmp_path):
    columns = on_grids(skip=("C-2",)) + [{"x": 3000.0, "y": 2500.0, "diameter": 600.0}]
    _, second, changes = revise(tmp_path, columns)
    assert len(changes.added) == 1 and changes.added[0].circular
    assert len(changes.removed) == 1 and (changes.removed[0].x, changes.removed[0].y) == (12000.0, 5000.0)
    lines = gc.describe_changes(changes, second)
    assert any(line.startswith("ADD") for line in lines)
    assert any(line.startswith("REMOVE  col") for line in lines)


def test_off_grid_column_is_followed_only_when_it_moves_a_little(tmp_path):
    def run(dx):
        first_columns = on_grids() + [{"x": 3000.0, "y": 2500.0, "width": 500.0, "depth": 500.0}]
        second_columns = on_grids() + [{"x": 3000.0 + dx, "y": 2500.0, "width": 500.0, "depth": 500.0}]
        first = gc.read_framing_plans(write_plans(tmp_path / "a.dxf", [("2F", 4500, first_columns)]))
        second = gc.read_framing_plans(write_plans(tmp_path / "b.dxf", [("2F", 4500, second_columns)]))
        return gc.plan_changes(as_model(first), first.grids, second, 5, 60)

    near, far = run(800.0), run(4000.0)
    assert len(near.moved) == 1 and not (near.added or near.removed)
    assert len(far.added) == 1 and len(far.removed) == 1 and not far.moved


def test_grid_key_needs_two_crossing_grids(tmp_path):
    building = gc.read_framing_plans(write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())]))
    assert gc.grid_key(6000.0, 5000.0, building.grids) == frozenset({"B", "2"})
    assert gc.grid_key(6000.0, 2500.0, building.grids) is None     # on one grid only
    assert gc.grid_key(3000.0, 2500.0, building.grids) is None


# --------------------------------------------------------------------------
# WALLS
# --------------------------------------------------------------------------
CORE = ([(2000.0, 1000.0), (4000.0, 1000.0), (4000.0, 4000.0), (2000.0, 4000.0)], 300.0, True)


def test_core_drawn_as_one_wide_polyline_gives_one_wall_per_side(tmp_path):
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())], walls=[CORE])
    walls = gc.read_framing_plans(path).walls
    assert len(walls) == 4 and {w.thickness for w in walls} == {300}
    assert sorted(round(math.hypot(w.x2 - w.x1, w.y2 - w.y1)) for w in walls) == [2000, 2000, 3000, 3000]


def test_wall_drawn_as_its_outline_gives_the_centre_line_and_thickness(tmp_path):
    """A 4000 x 250 rectangle turned 30 degrees: centre line 4000 long, 250 thick."""
    outline = (_box(5000.0, 2500.0, 4000.0, 250.0, 30.0), 0.0, True)
    path = write_plans(tmp_path / "p.dxf", [("2F", 4500, on_grids())], walls=[outline])
    (wall,) = gc.read_framing_plans(path).walls
    assert wall.thickness == 250
    assert math.hypot(wall.x2 - wall.x1, wall.y2 - wall.y1) == pytest.approx(4000.0)
    assert math.degrees(math.atan2(wall.y2 - wall.y1, wall.x2 - wall.x1)) == pytest.approx(30.0)
    assert ((wall.x1 + wall.x2) / 2, (wall.y1 + wall.y2) / 2) == pytest.approx((5000.0, 2500.0))


def _wall_changes(tmp_path, second_walls):
    first = gc.read_framing_plans(
        write_plans(tmp_path / "a.dxf", [("2F", 4500, on_grids())], walls=[CORE]))
    second = gc.read_framing_plans(
        write_plans(tmp_path / "b.dxf", [("2F", 4500, on_grids())], walls=second_walls))
    model = [gc.ExistingWall(f"w{i}", w.story, w.x1, w.y1, w.x2, w.y2,
                             gc.wall_section_name(w.thickness, 5, 60))
             for i, w in enumerate(first.walls)]
    changes = gc.Changes()
    gc.wall_changes(model, second, changes, 5, 60)
    return changes


def test_same_walls_again_change_nothing(tmp_path):
    changes = _wall_changes(tmp_path, [CORE])
    assert len(changes.walls_unchanged) == 4 and not changes.anything


def test_thicker_wall_is_updated_and_a_moved_one_is_replaced(tmp_path):
    thicker = _wall_changes(tmp_path, [(CORE[0], 400.0, True)])
    assert len(thicker.walls_updated) == 4 and not (thicker.walls_added or thicker.walls_removed)
    shifted = _wall_changes(tmp_path, [([(x + 500.0, y) for x, y in CORE[0]], 300.0, True)])
    assert len(shifted.walls_added) == 4 and len(shifted.walls_removed) == 4
    assert gc.wall_section_name(300, 5, 60) == "SW_300_C05_G60"


def test_a_footing_level_puts_the_ground_at_zero():
    building = gc.Building(
        stories=[("2F", 4500.0), ("3F", 3500.0)],
        columns=[gc.PlanColumn("2F", 0, 0, 400, 400), gc.PlanColumn("3F", 0, 0, 400, 400)],
        walls=[gc.PlanWall("2F", 0, 0, 3000, 0, 200)],
    )
    footed = gc.add_footing_level(building, 1500, "GF")
    assert footed.stories == [("GF", 1500.0), ("2F", 4500.0), ("3F", 3500.0)]
    assert footed.base_elevation == -1500.0
    assert [c.story for c in footed.columns] == ["GF", "2F", "3F"]
    assert [w.story for w in footed.walls] == ["GF", "2F"]
    assert building.base_elevation == 0.0  # the drawing itself is not changed


def test_the_ground_level_name_must_be_new():
    building = gc.Building(stories=[("GF", 4000.0)])
    with pytest.raises(ValueError, match="already in the drawing"):
        gc.add_footing_level(building, 1500, "GF")
    with pytest.raises(ValueError, match="more than zero"):
        gc.add_footing_level(gc.Building(stories=[("2F", 4000.0)]), 0)
