"""Grids, stories, columns and walls of an ETABS model from framing plans in one DXF file.

Run this file to use it:

    python etabs_api/workflows/grid_column_model.py

The drawing rules (see ``edb/plan dxf`` for samples), all in millimetres:

* ``S-STORY``   one closed rectangle around each plan and, inside it, one text
                ``STORY <name>   HEIGHT <story height>``; plans go left to
                right, lowest level first
* ``S-ORIGIN``  one point per plan at the same building point
* ``S-GRID``    one line per grid, with its label as a text at one end
* ``S-COLUMN``  each column as its outline: a closed rectangle or a circle
* ``S-WALL``    walls and core walls, drawn either way:
                a polyline along the wall centre lines with its width set to the
                wall thickness (one polyline can trace a whole core), or
                a closed rectangle around one wall (the short side is the thickness)

The columns of a plan are the ones below that level. Optionally a footing
level is added: the base drops to minus the embedment depth, a ground story
(``GF``) ends at elevation 0 below the lowest plan, and the columns and walls
of the lowest plan continue down to the footings. Run on a model that
already has columns, the script updates it: columns that did not change are
left alone, moved ones are moved with the beams framing into them, and the
rest are added or removed. A wall that changed is removed and drawn again.
The changes are listed for confirmation first.

Reading the drawing and working out the changes need no ETABS.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field

if __package__ in (None, ""):
    # Run as a script: make the project folder (two levels up) importable.
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etabs_api.workflows import model_setup as ms
from etabs_api.core.helpers import as_list, return_code

DEFAULT_LAYERS = {"story": "S-STORY", "origin": "S-ORIGIN", "grid": "S-GRID",
                  "column": "S-COLUMN", "wall": "S-WALL"}
MIN_WALL_LENGTH = 100.0  # mm; shorter pieces of a wall polyline are ignored
LABEL_REACH = 2500.0  # mm from a grid line end to its label
SAME_PLACE = 5.0  # mm; a column this close to its old place has not moved
ON_GRID = 100.0  # mm; a column this close to a grid line is on it
MOVE_LIMIT = 2500.0  # mm; farthest an off-grid column is followed as "moved"
ANGLE_TOLERANCE = 0.5  # degrees
_STORY_TEXT = re.compile(r"STORY\s+(.+?)\s+HEIGHT\s+([\d.]+)", re.IGNORECASE)


# =============================================================================
# READING THE DRAWING (no ETABS)
# =============================================================================
@dataclass(frozen=True)
class GridLine:
    label: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class PlanColumn:
    """A column of one story: centre, section and rotation.

    ``depth`` is measured along the direction ``angle`` (the ETABS local 2
    axis) and ``width`` across it. For a circular column both are the diameter.
    """

    story: str
    x: float
    y: float
    width: float
    depth: float
    angle: float = 0.0
    circular: bool = False


@dataclass(frozen=True)
class PlanWall:
    """A straight wall of one story, by the two ends of its centre line."""

    story: str
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float


def plan_wall(story: str, a: tuple, b: tuple, thickness: float) -> PlanWall:
    """A wall with its ends in a fixed order, so the same wall always reads the same."""
    a, b = sorted([(round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3))])
    return PlanWall(story, a[0], a[1], b[0], b[1], round(thickness))


@dataclass
class Building:
    stories: list[tuple[str, float]] = field(default_factory=list)  # lowest first
    grids: list[GridLine] = field(default_factory=list)
    columns: list[PlanColumn] = field(default_factory=list)
    walls: list[PlanWall] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    base_elevation: float = 0.0  # mm; negative with a footing level


def add_footing_level(building: Building, depth: float, ground: str = "GF") -> Building:
    """The building with a ground story below its lowest plan, ``depth`` deep.

    The base goes to ``-depth``, so the ground level (``ground``) is at
    elevation 0 and the lowest plan keeps its height above it. The columns and
    walls of the lowest plan are repeated in the ground story, from the
    footings up to the ground level.
    """
    from dataclasses import replace

    if depth <= 0:
        raise ValueError("The embedment depth must be more than zero.")
    if not building.stories:
        raise ValueError("The drawing has no stories.")
    if ground in dict(building.stories):
        raise ValueError(f"Story {ground} is already in the drawing; choose another name "
                         "for the ground level.")
    lowest = building.stories[0][0]
    return Building(
        stories=[(ground, float(depth))] + list(building.stories),
        grids=list(building.grids),
        columns=[replace(c, story=ground) for c in building.columns if c.story == lowest]
        + list(building.columns),
        walls=[replace(w, story=ground) for w in building.walls if w.story == lowest]
        + list(building.walls),
        warnings=list(building.warnings),
        base_elevation=-float(depth),
    )


def rectangle_column(story: str, corners: list[tuple[float, float]]) -> PlanColumn:
    """Centre, sides and rotation of a rectangular outline.

    The rotation is kept between 0 and 90 degrees, so the same outline always
    gives the same section and angle whichever corner it was drawn from.
    """
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = corners
    cx, cy = (x0 + x1 + x2 + x3) / 4.0, (y0 + y1 + y2 + y3) / 4.0
    along = math.hypot(x1 - x0, y1 - y0)  # side from the first to the second corner
    across = math.hypot(x2 - x1, y2 - y1)
    angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
    if angle >= 90.0 - 1e-6:  # describe it by its other side instead
        angle -= 90.0
        along, across = across, along
    if abs(angle) < 1e-6:
        angle = 0.0
    return PlanColumn(story, cx, cy, width=round(across), depth=round(along),
                      angle=round(angle, 3))


def read_framing_plans(path: str, layers: dict | None = None) -> Building:
    """Read every framing plan of a DXF file into stories, grids and columns."""
    import ezdxf

    layers = {**DEFAULT_LAYERS, **(layers or {})}
    names = {key: value.strip().upper() for key, value in layers.items()}
    modelspace = ezdxf.readfile(path).modelspace()

    def on(entity, key: str) -> bool:
        return entity.dxf.layer.upper() == names[key]

    def text_of(entity) -> tuple[str, float, float]:
        if entity.dxftype() == "MTEXT":
            point = entity.dxf.insert
            return entity.plain_text().strip(), point.x, point.y
        aligned = entity.dxf.get("halign", 0) or entity.dxf.get("valign", 0)
        point = (entity.dxf.get("align_point") if aligned else None) or entity.dxf.insert
        return entity.dxf.text.strip(), point.x, point.y

    def corners_of(entity) -> list[tuple[float, float]]:
        points = [(p[0], p[1]) for p in entity.get_points()]
        if len(points) > 1 and math.dist(points[0], points[-1]) < 1e-6:
            points = points[:-1]
        return points

    texts = {key: [text_of(e) for e in modelspace.query("TEXT MTEXT") if on(e, key)]
             for key in ("story", "grid")}
    frames = []
    for entity in modelspace.query("LWPOLYLINE"):
        if on(entity, "story"):
            points = corners_of(entity)
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            frames.append((min(xs), min(ys), max(xs), max(ys)))
    if not frames:
        raise ValueError(f"No plan boundary was found on layer {layers['story']}.")
    frames.sort()  # left to right = lowest level first

    def inside(frame, x: float, y: float) -> bool:
        return frame[0] <= x <= frame[2] and frame[1] <= y <= frame[3]

    building = Building()
    grids: dict[str, GridLine] = {}
    for frame in frames:
        title = next((_STORY_TEXT.search(t) for t, x, y in texts["story"]
                      if inside(frame, x, y) and _STORY_TEXT.search(t)), None)
        if title is None:
            raise ValueError("A plan has no 'STORY <name>   HEIGHT <height>' text inside "
                             f"its boundary on layer {layers['story']}.")
        story, height = title.group(1).strip(), float(title.group(2))
        if story in dict(building.stories):
            raise ValueError(f"Story {story} is drawn twice.")
        origins = [e.dxf.location for e in modelspace.query("POINT")
                   if on(e, "origin") and inside(frame, e.dxf.location.x, e.dxf.location.y)]
        if len(origins) != 1:
            raise ValueError(f"Plan {story} needs exactly one point on layer "
                             f"{layers['origin']}; it has {len(origins)}.")
        ox, oy = origins[0].x, origins[0].y
        building.stories.append((story, height))

        labels = [(t, x, y) for t, x, y in texts["grid"] if inside(frame, x, y)]
        for entity in modelspace.query("LINE"):
            if not on(entity, "grid"):
                continue
            a, b = entity.dxf.start, entity.dxf.end
            if not inside(frame, (a.x + b.x) / 2, (a.y + b.y) / 2):
                continue
            near = min(
                ((min(math.hypot(x - a.x, y - a.y), math.hypot(x - b.x, y - b.y)), t)
                 for t, x, y in labels), default=(math.inf, ""),
            )
            if near[0] > LABEL_REACH:
                building.warnings.append(f"{story}: a grid line has no label near its ends.")
                continue
            # The lowest plan that shows a grid defines it.
            grids.setdefault(near[1], GridLine(near[1], a.x - ox, a.y - oy, b.x - ox, b.y - oy))

        for entity in modelspace.query("LWPOLYLINE CIRCLE"):
            if not on(entity, "column"):
                continue
            if entity.dxftype() == "CIRCLE":
                centre, diameter = entity.dxf.center, round(2.0 * entity.dxf.radius)
                if inside(frame, centre.x, centre.y):
                    building.columns.append(PlanColumn(
                        story, centre.x - ox, centre.y - oy, diameter, diameter, 0.0, True))
                continue
            points = corners_of(entity)
            if not inside(frame, points[0][0], points[0][1]):
                continue
            if len(points) != 4:
                building.warnings.append(
                    f"{story}: a column outline with {len(points)} corners was skipped.")
                continue
            building.columns.append(
                rectangle_column(story, [(x - ox, y - oy) for x, y in points]))
        for entity in modelspace.query("LWPOLYLINE LINE"):
            if not on(entity, "wall"):
                continue
            if entity.dxftype() == "LINE":
                building.warnings.append(
                    f"{story}: a plain line on the wall layer was skipped (it has no thickness).")
                continue
            points = corners_of(entity)
            if not points or not inside(frame, points[0][0], points[0][1]):
                continue
            local = [(x - ox, y - oy) for x, y in points]
            width = float(entity.dxf.get("const_width", 0.0) or 0.0)
            if width > 0:  # centre lines, the polyline width is the thickness
                ends = list(zip(local, local[1:]))
                if entity.closed and len(local) > 2:
                    ends.append((local[-1], local[0]))
                for a, b in ends:
                    if math.dist(a, b) >= MIN_WALL_LENGTH:
                        building.walls.append(plan_wall(story, a, b, width))
            elif len(local) == 4 and entity.closed:  # the outline of one wall
                side_a, side_b = math.dist(local[0], local[1]), math.dist(local[1], local[2])
                if side_a >= side_b:  # the long sides run from corner 0 to 1 and 3 to 2
                    start = ((local[0][0] + local[3][0]) / 2, (local[0][1] + local[3][1]) / 2)
                    end = ((local[1][0] + local[2][0]) / 2, (local[1][1] + local[2][1]) / 2)
                else:
                    start = ((local[0][0] + local[1][0]) / 2, (local[0][1] + local[1][1]) / 2)
                    end = ((local[3][0] + local[2][0]) / 2, (local[3][1] + local[2][1]) / 2)
                building.walls.append(plan_wall(story, start, end, min(side_a, side_b)))
            else:
                building.warnings.append(
                    f"{story}: a wall polyline with no width and {len(local)} corners was skipped.")
    building.grids = list(grids.values())
    return building


# =============================================================================
# COMPARING WITH THE MODEL (no ETABS)
# =============================================================================
@dataclass(frozen=True)
class ExistingColumn:
    """A column that is in the ETABS model."""

    name: str
    story: str
    x: float
    y: float
    section: str = ""
    angle: float = 0.0
    joint_bottom: str = ""
    joint_top: str = ""


@dataclass(frozen=True)
class ExistingWall:
    """A wall panel that is in the ETABS model."""

    name: str
    story: str
    x1: float
    y1: float
    x2: float
    y2: float
    section: str = ""


@dataclass
class Changes:
    unchanged: list = field(default_factory=list)  # (existing, plan column)
    updated: list = field(default_factory=list)  # same place, new section or rotation
    moved: list = field(default_factory=list)
    added: list = field(default_factory=list)  # plan columns
    removed: list = field(default_factory=list)  # existing columns
    walls_unchanged: list = field(default_factory=list)  # (existing, plan wall)
    walls_updated: list = field(default_factory=list)  # same place, new thickness
    walls_added: list = field(default_factory=list)
    walls_removed: list = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return bool(self.updated or self.moved or self.added or self.removed
                    or self.walls_updated or self.walls_added or self.walls_removed)


def section_of(column: PlanColumn, concrete_ksi: float, rebar_ksi: float) -> dict:
    """The frame section of a plan column, in the form ``model_setup`` creates."""
    concrete, rebar = ms.grade_name("C", concrete_ksi), ms.grade_name("G", rebar_ksi)
    if column.circular:
        name = f"C_{column.depth:g}_{concrete}_{rebar}"
        kind = "circle"
    else:
        name = f"CR_{column.width:g}X{column.depth:g}_{concrete}_{rebar}"
        kind = "column"
    return {"name": name, "kind": kind, "width": column.width, "depth": column.depth,
            "material": concrete, "rebar": rebar}


def wall_section_name(thickness: float, concrete_ksi: float, rebar_ksi: float) -> str:
    """``SW_300_C05_G60`` for a 300 mm wall."""
    return (f"SW_{thickness:g}_{ms.grade_name('C', concrete_ksi)}_"
            f"{ms.grade_name('G', rebar_ksi)}")


def wall_changes(
    existing: list[ExistingWall], building: Building, changes: Changes,
    concrete_ksi: float, rebar_ksi: float,
) -> None:
    """Pair the drawn walls with the model walls that have the same two ends.

    A wall at the same place is unchanged, or updated when its thickness
    differs. Any other difference is a removal and an addition: walls are not
    followed when they move.
    """
    def same(a, b) -> bool:
        return (math.hypot(a.x1 - b.x1, a.y1 - b.y1) <= SAME_PLACE
                and math.hypot(a.x2 - b.x2, a.y2 - b.y2) <= SAME_PLACE)

    left = list(existing)
    for wall in building.walls:
        match = next((old for old in left if old.story == wall.story and same(old, wall)), None)
        if match is None:
            changes.walls_added.append(wall)
            continue
        left.remove(match)
        name = wall_section_name(wall.thickness, concrete_ksi, rebar_ksi)
        (changes.walls_unchanged if match.section == name
         else changes.walls_updated).append((match, wall))
    changes.walls_removed.extend(left)


def grid_key(x: float, y: float, grids: list[GridLine]) -> frozenset | None:
    """The labels of the grid lines a point sits on; None unless at least two cross there.

    Only the drawn length of a grid line counts. The grid of one wing, extended,
    would otherwise pass through columns of another wing by chance.
    """
    labels = set()
    for line in grids:
        dx, dy = line.x2 - line.x1, line.y2 - line.y1
        length_squared = dx * dx + dy * dy
        if length_squared < 1e-9:
            continue
        t = ((x - line.x1) * dx + (y - line.y1) * dy) / length_squared
        t = max(0.0, min(1.0, t))
        if math.hypot(x - (line.x1 + t * dx), y - (line.y1 + t * dy)) <= ON_GRID:
            labels.add(line.label)
    return frozenset(labels) if len(labels) >= 2 else None


def plan_changes(
    existing: list[ExistingColumn],
    old_grids: list[GridLine],
    building: Building,
    concrete_ksi: float,
    rebar_ksi: float,
) -> Changes:
    """What must change in the model so its columns match the drawing.

    Story by story, a drawn column is paired with a model column:

    1. at the same place: unchanged, or updated when its section or rotation differs;
    2. on the same grid intersection (old grids for the model, new grids for the
       drawing): moved. A whole grid or wing can shift and its columns still pair up;
    3. off the grids, the nearest remaining pair within ``MOVE_LIMIT``: moved.

    Whatever is left is added or removed.
    """
    changes = Changes()
    for story in {c.story for c in existing} | {c.story for c in building.columns}:
        old = [c for c in existing if c.story == story]
        new = [c for c in building.columns if c.story == story]

        def take(pairs):
            for a, b in pairs:
                old.remove(a)
                new.remove(b)

        # 1. same place
        pairs = []
        for a in old:
            b = next((b for b in new if math.hypot(a.x - b.x, a.y - b.y) <= SAME_PLACE
                      and all(b is not p[1] for p in pairs)), None)
            if b is not None:
                pairs.append((a, b))
        take(pairs)
        for a, b in pairs:
            same = (a.section == section_of(b, concrete_ksi, rebar_ksi)["name"]
                    and abs(a.angle - b.angle) <= ANGLE_TOLERANCE)
            (changes.unchanged if same else changes.updated).append((a, b))

        # 2. same grid intersection
        old_keys, new_keys = {}, {}
        for a in old:
            old_keys.setdefault(grid_key(a.x, a.y, old_grids), []).append(a)
        for b in new:
            new_keys.setdefault(grid_key(b.x, b.y, building.grids), []).append(b)
        pairs = [(old_keys[key][0], new_keys[key][0]) for key in old_keys
                 if key is not None and len(old_keys[key]) == 1 and len(new_keys.get(key, [])) == 1]
        take(pairs)
        changes.moved.extend(pairs)

        # 3. nearest, for columns off the grids
        candidates = sorted(
            (math.hypot(a.x - b.x, a.y - b.y), i, j)
            for i, a in enumerate(old) for j, b in enumerate(new)
            if math.hypot(a.x - b.x, a.y - b.y) <= MOVE_LIMIT
        )
        used_old, used_new, pairs = set(), set(), []
        for _, i, j in candidates:
            if i not in used_old and j not in used_new:
                used_old.add(i)
                used_new.add(j)
                pairs.append((old[i], new[j]))
        take(pairs)
        changes.moved.extend(pairs)

        changes.removed.extend(old)
        changes.added.extend(new)
    return changes


def describe_changes(changes: Changes, building: Building, limit: int | None = None) -> list[str]:
    """One line per change, for the confirmation dialog and the report file."""
    def place(c) -> str:
        key = grid_key(c.x, c.y, building.grids) if isinstance(c, PlanColumn) else None
        where = "-".join(sorted(key)) if key else f"({c.x:.0f}, {c.y:.0f})"
        return f"{c.story} {where}"

    lines = []
    for old, new in changes.moved:
        distance = math.hypot(old.x - new.x, old.y - new.y)
        lines.append(f"MOVE    {old.name}  to {place(new)}  ({distance:.0f} mm)")
    for old, new in changes.updated:
        size = f"D{new.depth:g}" if new.circular else f"{new.width:g}x{new.depth:g}"
        lines.append(f"UPDATE  {old.name}  at {place(new)}  to {size}, {new.angle:g} deg")
    for new in changes.added:
        size = f"D{new.depth:g}" if new.circular else f"{new.width:g}x{new.depth:g}"
        lines.append(f"ADD     {place(new)}  {size}")
    for old in changes.removed:
        lines.append(f"REMOVE  {old.name}  ({old.story}, at {old.x:.0f}, {old.y:.0f})")

    def span(w) -> str:
        return f"{w.story} ({w.x1:.0f}, {w.y1:.0f}) to ({w.x2:.0f}, {w.y2:.0f})"

    for old, new in changes.walls_updated:
        lines.append(f"UPDATE  wall {old.name}  {span(new)}  to {new.thickness:g} thick")
    for new in changes.walls_added:
        lines.append(f"ADD     wall {span(new)}  {new.thickness:g} thick")
    for old in changes.walls_removed:
        lines.append(f"REMOVE  wall {old.name}  {span(old)}")
    if limit is not None and len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more (see the report file)"]
    return lines


# =============================================================================
# ETABS
# =============================================================================
def _table(model, key: str) -> list[dict]:
    """An ETABS table as a list of rows; empty when ETABS has none."""
    try:
        result = model.DatabaseTables.GetTableForDisplayArray(key, [], "", 0, [], 0, [])
    except Exception:
        return []
    fields, values = [str(f) for f in as_list(result[2])], as_list(result[4])
    if not fields or fields == ["None"]:
        return []
    return [dict(zip(fields, values[i:i + len(fields)])) for i in range(0, len(values), len(fields))]


def read_model_walls(model) -> list[ExistingWall]:
    """The wall panels of the model, each by the two ends of its plan line."""
    points = {str(r["UniqueName"]): (float(r["X"]), float(r["Y"]))
              for r in _table(model, "Point Object Connectivity")}
    sections = {str(r["UniqueName"]): str(r["SectProp"])
                for r in _table(model, "Area Assignments - Section Properties")}
    walls = []
    for row in _table(model, "Wall Object Connectivity"):
        corners = {points[str(row[f"UniquePt{i}"])] for i in range(1, 5)
                   if str(row.get(f"UniquePt{i}")) in points}
        corners = {(round(x, 3), round(y, 3)) for x, y in corners}
        if len(corners) != 2:
            continue  # not a plain vertical panel
        a, b = sorted(corners)
        name = str(row["UniqueName"])
        walls.append(ExistingWall(name, str(row["Story"]), a[0], a[1], b[0], b[1],
                                  sections.get(name, "")))
    return walls


def read_model(model) -> tuple[list[ExistingColumn], list[GridLine], list[tuple[str, float]]]:
    """The columns, grid lines and stories that are in the model now."""
    points = {str(r["UniqueName"]): (float(r["X"]), float(r["Y"]), float(r["Z"]))
              for r in _table(model, "Point Object Connectivity")}
    summary = {str(r["UniqueName"]): r for r in _table(model, "Frame Assignments - Summary")}
    columns = []
    for row in _table(model, "Column Object Connectivity"):
        name, a, b = str(row["UniqueName"]), str(row["UniquePtI"]), str(row["UniquePtJ"])
        if a not in points or b not in points:
            continue
        bottom, top = (a, b) if points[a][2] <= points[b][2] else (b, a)
        info = summary.get(name, {})
        columns.append(ExistingColumn(
            name, str(row["Story"]), points[bottom][0], points[bottom][1],
            # the design section reads "N/A" until the model has been designed
            str(info.get("AnalysisSect") or info.get("DesignSect") or ""),
            float(info.get("AxisAngle") or 0.0), bottom, top,
        ))
    grids = []
    for row in _table(model, "Grid Definitions - Grid Lines"):
        if str(row.get("LineType", "")).startswith("General") and row.get("X1") not in (None, ""):
            grids.append(GridLine(str(row["ID"]), float(row["X1"]), float(row["Y1"]),
                                  float(row["X2"]), float(row["Y2"])))
    stories = model.Story.GetStories()
    names, heights = as_list(stories[1])[1:], as_list(stories[3])[1:]
    return columns, grids, [(str(n), float(h)) for n, h in zip(names, heights)]


def update_story_heights(model, building: Building, current: list[tuple[str, float]],
                         log) -> bool:
    """Set the base elevation and the story heights of a model that has members.

    The stories must have the same names as in the drawing; ETABS moves the
    levels above, with their members, when a height changes. A story that is
    new or missing cannot be inserted under existing members.
    """
    if [n for n, _ in current] != [n for n, _ in building.stories]:
        log.problems.append(
            "The stories of the drawing differ in name from the model, which already has "
            "members, so they were not changed (start from a new model to add or remove "
            f"stories): drawing {[n for n, _ in building.stories]}, model "
            f"{[n for n, _ in current]}")
        return False
    ok = log.check(model.Story.SetElevation("Base", building.base_elevation), "base elevation")
    for (name, height), (_, old) in zip(building.stories, current):
        if abs(height - old) > 0.5:
            ok = log.check(model.Story.SetHeight(name, height), f"story {name} height") and ok
    return ok


def apply_to_model(
    model, building: Building, changes: Changes, concrete_ksi: float, rebar_ksi: float,
    progress=None,
) -> ms.SetupLog:
    """Stories, grids and the column changes. The caller saves the model."""
    log = ms.SetupLog()

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    units = int(model.GetPresentUnits())
    model.SetPresentUnits(ms.UNITS_N_MM)
    try:
        existing, _, stories = read_model(model)
        # ---- stories ----
        say("Stories and levels")
        before = model.Story.GetStories()
        base_now = float(as_list(before[2])[0]) if as_list(before[2]) else 0.0
        if stories != building.stories or abs(base_now - building.base_elevation) > 0.5:
            if existing or _table(model, "Beam Object Connectivity"):
                # Members exist: change the heights in place; ETABS moves the
                # levels above with their members.
                if update_story_heights(model, building, stories, log):
                    log.done("story levels updated", len(building.stories))
            else:
                count = len(building.stories)
                done = model.Story.SetStories_2(
                    building.base_elevation, count, [n for n, _ in building.stories],
                    [h for _, h in building.stories], [False] * count, ["None"] * count,
                    [False] * count, [0.0] * count, [0] * count)
                if log.check(done, "stories"):
                    log.done("stories", count)
        # the seismic and wind patterns follow the stories (bottom = ground level)
        ms.set_lateral_story_range(model, log)
        levels = model.Story.GetStories()
        top_of = dict(zip((str(n) for n in as_list(levels[1])), as_list(levels[2])))
        height_of = dict(zip((str(n) for n in as_list(levels[1])), as_list(levels[3])))

        # ---- grids: the drawing replaces the grid lines of the first grid system ----
        say("Grid lines")
        systems = _table(model, "Grid Definitions - General")
        system = str(systems[0]["Name"]) if systems else "G1"
        rows = [{"Name": system, "LineType": "General (Cartesian)", "ID": g.label,
                 "X1": g.x1, "Y1": g.y1, "X2": g.x2, "Y2": g.y2, "BubbleLoc": "Start",
                 "Visible": "Yes"} for g in building.grids]
        if rows and ms._edit_table(model, "Grid Definitions - Grid Lines", rows, log,
                                   replace_all=True):
            log.done("grid lines", len(rows))

        # ---- materials and the sections the columns need ----
        say("Materials and column sections")
        needed = {}
        for column in building.columns:
            section = section_of(column, concrete_ksi, rebar_ksi)
            needed[section["name"]] = section
        have = {str(r["Name"]) for r in _table(model, "Frame Section Property Definitions - Summary")}
        materials = {str(r["Material"]) for r in _table(model, "Material Properties - General")}
        grades = {"concrete_ksi": [concrete_ksi], "rebar_ksi": [rebar_ksi]}
        if not {ms.grade_name("C", concrete_ksi), ms.grade_name("G", rebar_ksi)} <= materials:
            ms.define_materials(model, grades, log)
        for name, section in needed.items():
            if name not in have and ms.create_section(model.PropFrame, section, log):
                log.done("frame sections")

        frames = model.FrameObj

        def restyle(name: str, column: PlanColumn, what: str) -> bool:
            section = section_of(column, concrete_ksi, rebar_ksi)["name"]
            ok = log.check(frames.SetSection(name, section), f"{what} {name}: section")
            return log.check(frames.SetLocalAxes(name, column.angle), f"{what} {name}: angle") and ok

        def add(column: PlanColumn) -> bool:
            if column.story not in top_of:
                log.problems.append(f"column on story {column.story}: no such story in the model")
                return False
            top = float(top_of[column.story])
            made = frames.AddByCoord(
                column.x, column.y, top - float(height_of[column.story]), column.x, column.y, top,
                "", section_of(column, concrete_ksi, rebar_ksi)["name"], "", "Global")
            if not log.check(made, f"new column on {column.story}"):
                return False
            return log.check(frames.SetLocalAxes(str(made[0]), column.angle), "new column: angle")

        # ---- removed ----
        say("Removing columns")
        for old in changes.removed:
            if log.check(frames.Delete(old.name), f"removing {old.name}"):
                log.done("columns removed")

        # ---- moved: shift the joints, so the beams on them follow ----
        say("Moving columns (the beams follow)")
        target: dict[str, tuple[float, float]] = {}
        clash: set[str] = set()
        for old, _ in changes.unchanged + changes.updated:  # these joints must stay
            for joint in (old.joint_bottom, old.joint_top):
                target[joint] = (old.x, old.y)
        for old, new in changes.moved:
            for joint in (old.joint_bottom, old.joint_top):
                if joint in target and math.dist(target[joint], (new.x, new.y)) > SAME_PLACE:
                    clash.add(joint)
                target.setdefault(joint, (new.x, new.y))
        by_move, by_replace = [], []
        for old, new in changes.moved:
            (by_replace if {old.joint_bottom, old.joint_top} & clash else by_move).append((old, new))
        if by_move:
            moving = {j: (new.x, new.y) for old, new in by_move
                      for j in (old.joint_bottom, old.joint_top)}
            tables = model.DatabaseTables
            current = tables.GetTableForEditingArray("Point Object Connectivity", "", 0, [], 0, [])
            fields, values = [str(f) for f in as_list(current[1])], as_list(current[3])
            ix, iy = fields.index("X"), fields.index("Y")
            table = []
            for start in range(0, len(values), len(fields)):
                row = ["" if v is None else str(v) for v in values[start:start + len(fields)]]
                if row[0] in moving:
                    row[ix], row[iy] = (repr(float(v)) for v in moving[row[0]])
                table.append(row)
            staged = tables.SetTableForEditingArray(
                "Point Object Connectivity", 0, fields, len(table),
                [value for row in table for value in row])
            applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
            if return_code(staged) == 0 and not (int(applied[0]) or int(applied[1])):
                for old, new in by_move:
                    restyle(old.name, new, "moved column")
                log.done("columns moved", len(by_move))
            else:
                log.problems.append("the joints of the moved columns could not be moved: "
                                    + " ".join(str(applied[4]).split())[-300:])
        # A column that shares a joint with one staying put cannot be moved: replace it.
        for old, new in by_replace:
            if log.check(frames.Delete(old.name), f"replacing {old.name}") and add(new):
                log.done("columns replaced (joint shared with a column that stays)")

        # ---- updated in place ----
        say("Updating column sections and angles")
        for old, new in changes.updated:
            if restyle(old.name, new, "column"):
                log.done("columns updated")

        # ---- walls ----
        say("Walls")
        areas = model.AreaObj
        made_wall_sections = set()

        def wall_section(thickness: float) -> str:
            name = wall_section_name(thickness, concrete_ksi, rebar_ksi)
            if name not in made_wall_sections:
                existing_walls = {str(r["Name"]) for r in
                                  _table(model, "Wall Property Definitions - Specified")}
                # (name, specified wall, thin shell, material, thickness)
                if name not in existing_walls:
                    log.check(model.PropArea.SetWall(
                        name, 1, 1, ms.grade_name("C", concrete_ksi), float(thickness)),
                        f"wall section {name}")
                made_wall_sections.add(name)
            return name

        for old in changes.walls_removed:
            if log.check(areas.Delete(old.name), f"removing wall {old.name}"):
                log.done("walls removed")
        for old, new in changes.walls_updated:
            if log.check(areas.SetProperty(old.name, wall_section(new.thickness)),
                         f"wall {old.name}: thickness"):
                log.done("walls updated")
        for new in changes.walls_added:
            if new.story not in top_of:
                log.problems.append(f"wall on story {new.story}: no such story in the model")
                continue
            top = float(top_of[new.story])
            bottom = top - float(height_of[new.story])
            made = areas.AddByCoord(
                4, [new.x1, new.x2, new.x2, new.x1], [new.y1, new.y2, new.y2, new.y1],
                [bottom, bottom, top, top], "", wall_section(new.thickness), "", "Global")
            if log.check(made, f"new wall on {new.story}"):
                log.done("walls added")

        # ---- added ----
        say("Adding columns")
        for index, new in enumerate(changes.added):
            if progress is not None and index % 25 == 0:
                progress(f"Column {index + 1} of {len(changes.added)}")
            if add(new):
                log.done("columns added")
    finally:
        model.SetPresentUnits(units)
    return log


# =============================================================================
# ENTRY POINTS
# =============================================================================
def report_text(dxf_path: str, building: Building, changes: Changes, log=None) -> str:
    lines = [
        f"Drawing: {dxf_path}",
        "Stories (lowest first): " + ", ".join(f"{n} ({h:g})" for n, h in building.stories)
        + (f"; base at {building.base_elevation:g} mm" if building.base_elevation else ""),
        f"Grid lines: {len(building.grids)} | Columns drawn: {len(building.columns)}",
        f"Unchanged: {len(changes.unchanged)} | Moved: {len(changes.moved)} | "
        f"Updated: {len(changes.updated)} | Added: {len(changes.added)} | "
        f"Removed: {len(changes.removed)}",
        f"Walls drawn: {len(building.walls)} | unchanged: {len(changes.walls_unchanged)} | "
        f"updated: {len(changes.walls_updated)} | added: {len(changes.walls_added)} | "
        f"removed: {len(changes.walls_removed)}",
        "",
    ]
    lines += describe_changes(changes, building)
    if building.warnings:
        lines += ["", "Warnings from the drawing:"] + building.warnings
    if log is not None:
        lines += ["", "Applied:"] + [f"  {count} {what}" for what, count in log.counts.items()]
        if log.problems:
            lines += ["", "Failed:"] + [f"  {p}" for p in log.problems]
    return "\n".join(lines) + "\n"


def build_grid_column_model(
    dxf_path: str,
    concrete_ksi: float,
    rebar_ksi: float = 60,
    target: str = "open",
    path: str | None = None,
    layers: dict | None = None,
    confirm=None,
    progress=None,
    footing_depth: float = 0.0,
    ground_story: str = "GF",
):
    """Build or update the grids and columns without dialogs.

    With ``footing_depth`` (mm) a footing level is added below the lowest plan
    (see ``add_footing_level``).

    ``target`` is ``"open"`` (the model open in ETABS) or ``"new"`` (a blank
    model saved at ``path``). ``confirm(building, changes)`` may return False
    to stop before anything is changed. Returns ``(building, changes, log,
    model path)``; ``log`` is None when it was stopped.
    """
    if progress is not None:
        progress("Reading the framing plans")
    building = read_framing_plans(dxf_path, layers)
    if footing_depth:
        building = add_footing_level(building, footing_depth, ground_story)
    new_model = target == "new"
    if new_model and not path:
        raise ValueError("A new model needs the path to save it at.")
    model = ms._attach_or_start(start=new_model)
    if new_model:
        path = os.path.splitext(os.path.normpath(os.path.abspath(path)))[0] + ".EDB"
        existing, old_grids, old_walls = [], [], []
    else:
        path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
        if not os.path.isfile(path):
            raise RuntimeError("Save the ETABS model first: it has no file yet.")
        units = int(model.GetPresentUnits())
        model.SetPresentUnits(ms.UNITS_N_MM)  # the drawing is in millimetres
        if progress is not None:
            progress("Reading the columns, walls and grids of the model")
        try:
            existing, old_grids, _ = read_model(model)
            old_walls = read_model_walls(model)
        finally:
            model.SetPresentUnits(units)
    if progress is not None:
        progress("Working out what changed")
    changes = plan_changes(existing, old_grids, building, concrete_ksi, rebar_ksi)
    wall_changes(old_walls, building, changes, concrete_ksi, rebar_ksi)
    if confirm is not None and not confirm(building, changes):
        return building, changes, None, path
    if new_model:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        model.InitializeNewModel(ms.UNITS_N_MM)
        model.File.NewBlank()
    if return_code(model.File.Save(path)) != 0:
        raise RuntimeError(f"ETABS could not save the model: {path}")
    log = apply_to_model(model, building, changes, concrete_ksi, rebar_ksi, progress)
    model.File.Save(path)
    try:
        model.View.RefreshView()
    except Exception:
        pass
    with open(os.path.splitext(path)[0] + " - plan changes.txt", "w", encoding="utf-8") as handle:
        handle.write(report_text(dxf_path, building, changes, log))
    return building, changes, log, path


def _defaults_path() -> str:
    from utilities.user_settings import settings_path as user_settings_path

    return user_settings_path("grid_column_model.json")


def run_grid_column_model() -> str | None:
    """Entry point: ask for the drawing and the inputs, confirm the changes, apply them."""
    from utilities._gui_helpers import (
        LoadingWindow,
        enter_values,
        select_open_path,
        select_option,
        select_save_path,
        show_warning,
    )

    title = "Grids and Columns from DXF"
    dxf_path = select_open_path("Select the framing plan DXF", ".dxf", "DXF drawing")
    if not dxf_path:
        return None
    saved = ms.load_settings(_defaults_path()) or {}
    fields = {
        "Story layer": saved.get("story", DEFAULT_LAYERS["story"]),
        "Origin layer": saved.get("origin", DEFAULT_LAYERS["origin"]),
        "Grid layer": saved.get("grid", DEFAULT_LAYERS["grid"]),
        "Column layer": saved.get("column", DEFAULT_LAYERS["column"]),
        "Wall layer": saved.get("wall", DEFAULT_LAYERS["wall"]),
        "Concrete of the columns and walls (ksi)": saved.get("concrete_ksi", 5),
        "Rebar of the columns and walls (ksi)": saved.get("rebar_ksi", 60),
    }
    answers = enter_values(title, "Layers of the drawing and the material of the columns "
                           "and walls.",
                           list(fields), {k: str(v) for k, v in fields.items()})
    if answers is None:
        return None
    values = [answers[label].strip() for label in fields]
    layers = dict(zip(("story", "origin", "grid", "column", "wall"), values[:5]))
    concrete_ksi, rebar_ksi = float(values[5]), float(values[6])

    footing = select_option(
        title, "Add a footing level below the lowest plan? The ground level becomes "
        "elevation 0, the base goes down by the embedment depth, and the columns and walls "
        "of the lowest plan continue down to the footings.",
        ["Yes, add a footing level", "No"],
        default_index=0 if saved.get("footing_depth") else 1)
    if footing is None:
        return None
    footing_depth, ground_story = 0.0, saved.get("ground_story", "GF")
    if footing.startswith("Yes"):
        labels = {"Embedment depth below the ground level (mm)": "footing_depth",
                  "Name of the ground level": "ground_story"}
        typed = enter_values(title, "Footing level.", list(labels), {
            "Embedment depth below the ground level (mm)": f"{saved.get('footing_depth') or 1500:g}",
            "Name of the ground level": ground_story})
        if typed is None:
            return None
        try:
            footing_depth = float(typed["Embedment depth below the ground level (mm)"])
            if footing_depth <= 0:
                raise ValueError
        except ValueError:
            show_warning("The embedment depth must be a number above zero.", title=title)
            return None
        ground_story = typed["Name of the ground level"].strip() or "GF"

    source = select_option(title, "Which model should get the grids and columns?", [
        "The model that is open in ETABS", "A new blank model",
    ])
    if source is None:
        return None
    path = None
    if source.startswith("A new"):
        path = select_save_path("Save the new ETABS model as", "New Model", ".EDB", "ETABS model")
        if not path:
            return None

    def confirm(building, changes) -> bool:
        counts = (
            f"Stories: {', '.join(n for n, _ in building.stories)}"
            + (f"  (base at {building.base_elevation:g} mm)" if building.base_elevation else "")
            + "\n"
            f"Grid lines: {len(building.grids)}   Columns drawn: {len(building.columns)}   "
            f"Walls drawn: {len(building.walls)}\n\n"
            f"Columns: unchanged {len(changes.unchanged)},  moved {len(changes.moved)},  "
            f"updated {len(changes.updated)},  added {len(changes.added)},  removed "
            f"{len(changes.removed)}\n"
            f"Walls: unchanged {len(changes.walls_unchanged)},  updated "
            f"{len(changes.walls_updated)},  added {len(changes.walls_added)},  removed "
            f"{len(changes.walls_removed)}\n\n"
        )
        listed = "\n".join(describe_changes(changes, building, limit=18))
        window.stop()  # the confirmation dialog takes the screen
        answer = select_option(title, counts + listed, ["Apply these changes", "Cancel"])
        apply = bool(answer) and answer.startswith("Apply")
        if apply:
            window.start()
            window.update("Applying the changes")
        return apply

    window = LoadingWindow("Grids, columns and walls from the DXF")
    window.start()
    try:
        building, changes, log, path = build_grid_column_model(
            dxf_path, concrete_ksi, rebar_ksi, "new" if path else "open", path, layers, confirm,
            progress=window.update, footing_depth=footing_depth, ground_story=ground_story)
    except (ValueError, RuntimeError) as error:
        window.stop()
        show_warning(str(error), title=title)
        return None
    finally:
        window.stop()
    if log is None:
        return None
    ms.save_settings({**layers, "concrete_ksi": concrete_ksi, "rebar_ksi": rebar_ksi,
                      "footing_depth": footing_depth, "ground_story": ground_story},
                     _defaults_path())
    message = "Done:\n" + "\n".join(f"{count} {what}" for what, count in log.counts.items())
    message += f"\n\nSaved as:\n{path}\n\nThe list of changes is in the text file beside it."
    if log.problems:
        message += f"\n\n{len(log.problems)} items failed:\n" + "\n".join(log.problems[:10])
    show_warning(message, title=title)
    return path


if __name__ == "__main__":
    run_grid_column_model()
