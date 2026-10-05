"""Framing plans of the open ETABS model as one DXF (``sdt plans``).

Every floor of the model is drawn at 1:1 in mm, the floors side by side in
one row, each with its title:

* **Beams and girders** are multilines (MLINE) at their true width, dashed.
  A multiline keeps its width when an end point is dragged in AutoCAD. Each
  one stops at the face of the column it frames into, or at the face of the
  girder that carries it.
* **Columns** are solid, at their true size and rotation. A floor shows the
  columns below it (the columns of that ETABS story).
* **Walls** are solid at their true thickness.
* **Marks**: the name of every beam beside it; the mark of every column, on
  every floor or only where the column starts.
* **Grids**, optional, with their bubbles and, optionally, the dimensions
  between them.

Nothing in the model is changed. The functions that draw need no ETABS.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

LAYER_GIRDER, LAYER_BEAM, LAYER_COLUMN = "S-GIRDER", "S-BEAM", "S-COLUMN"
LAYER_WALL, LAYER_GRID, LAYER_TEXT, LAYER_DIM = "S-WALL", "S-GRID", "S-TEXT", "S-DIM"
# name, AutoCAD colour index, linetype
LAYERS = (
    (LAYER_GIRDER, 3, "Continuous"), (LAYER_BEAM, 4, "Continuous"),
    (LAYER_COLUMN, 7, "Continuous"), (LAYER_WALL, 8, "Continuous"),
    (LAYER_GRID, 8, "CENTER"), (LAYER_TEXT, 2, "Continuous"), (LAYER_DIM, 1, "Continuous"),
)
BEAM_STYLE = "SDT_BEAM"       # the multiline style: two dashed lines, one unit apart
DIM_STYLE = "SDT_PLAN"
PAPER_TEXT = 2.5              # mm on paper; the text height is this times the plot scale
EVERY_LEVEL, BOTTOM_ONLY = "every", "bottom"
COLLINEAR = math.cos(math.radians(15.0))   # beams within 15 degrees continue each other
ON_LINE = 10.0                # mm: a joint this close to a centre line is on it
MIN_SKEW = math.sin(math.radians(20.0))    # a face flatter than this to the beam: square end


# =============================================================================
# THE PLAN (plain data)
# =============================================================================
@dataclass(frozen=True)
class PlanBeam:
    """A beam in plan, joint to joint. ``width`` is 0 when its section is not a
    rectangle (it is then drawn as one line). ``girder``: it frames into a column."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    joint_i: str = ""
    joint_j: str = ""
    girder: bool = False


@dataclass(frozen=True)
class PlanColumn:
    """A column in plan. ``depth`` is along ``angle`` (the ETABS local 2 axis,
    degrees from global X) and ``width`` across it; both are the diameter of a
    circular column. ``top`` and ``bottom`` are its joints."""

    name: str
    x: float
    y: float
    width: float
    depth: float
    angle: float = 0.0
    circular: bool = False
    top: str = ""
    bottom: str = ""


@dataclass(frozen=True)
class PlanWall:
    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float


@dataclass(frozen=True)
class PlanGrid:
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    bubble_at_end: bool = False


@dataclass
class FloorPlan:
    story: str
    elevation: float
    beams: list[PlanBeam] = field(default_factory=list)
    columns: list[PlanColumn] = field(default_factory=list)   # below this floor
    walls: list[PlanWall] = field(default_factory=list)
    # columns that start on this floor (no column below): beams stop at them too
    columns_above: list[PlanColumn] = field(default_factory=list)


@dataclass
class PlanOptions:
    grids: bool = True
    dimensions: bool = False
    column_marks: str = EVERY_LEVEL
    scale: float = 100.0      # the plot scale 1 : scale, for the text and the dashes

    @property
    def text_height(self) -> float:
        return PAPER_TEXT * self.scale


# =============================================================================
# GEOMETRY
# =============================================================================
def column_mark(name: str) -> str:
    """The mark of a column without its level: ``GF-C1`` and ``2-C1A`` give
    ``C1`` and ``C1A``. A name with no level in front is kept."""
    match = re.match(r"^[^-\s]+-(.+)$", str(name).strip())
    return match.group(1) if match else str(name).strip()


def face_distance(column: PlanColumn, direction: tuple[float, float]) -> float:
    """From the centre of a column to its face along a plan direction (unit vector)."""
    if column.circular:
        return column.depth / 2.0
    angle = math.radians(column.angle)
    along = abs(direction[0] * math.cos(angle) + direction[1] * math.sin(angle))
    across = abs(-direction[0] * math.sin(angle) + direction[1] * math.cos(angle))
    reach = []
    if along > 1e-9:
        reach.append(column.depth / 2.0 / along)
    if across > 1e-9:
        reach.append(column.width / 2.0 / across)
    return min(reach) if reach else 0.0


def column_outline(column: PlanColumn) -> list[tuple[float, float]]:
    """The four corners of a rectangular column, in order."""
    angle = math.radians(column.angle)
    ux, uy = math.cos(angle), math.sin(angle)            # along the depth
    vx, vy = -uy, ux                                      # along the width
    half_d, half_w = column.depth / 2.0, column.width / 2.0
    return [(column.x + sd * half_d * ux + sw * half_w * vx,
             column.y + sd * half_d * uy + sw * half_w * vy)
            for sd, sw in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def wall_outline(wall: PlanWall) -> list[tuple[float, float]]:
    length = math.hypot(wall.x2 - wall.x1, wall.y2 - wall.y1)
    if length <= 0:
        return []
    nx, ny = -(wall.y2 - wall.y1) / length, (wall.x2 - wall.x1) / length
    half = wall.thickness / 2.0
    return [(wall.x1 + nx * half, wall.y1 + ny * half), (wall.x2 + nx * half, wall.y2 + ny * half),
            (wall.x2 - nx * half, wall.y2 - ny * half), (wall.x1 - nx * half, wall.y1 - ny * half)]


def _direction(beam: PlanBeam, joint: str) -> tuple[float, float] | None:
    """Unit direction of a beam, leaving ``joint``."""
    start, end = (beam.x1, beam.y1), (beam.x2, beam.y2)
    if joint != beam.joint_i:
        start, end = end, start
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    return ((end[0] - start[0]) / length, (end[1] - start[1]) / length) if length > 1e-6 else None


def column_face(column: PlanColumn, direction: tuple[float, float]
                ) -> tuple[float, tuple[float, float] | None]:
    """Where a beam leaving the centre of a column along ``direction`` crosses its
    face: (distance from the centre, direction of that face). A circular
    column has no straight face: None."""
    distance = face_distance(column, direction)
    if column.circular:
        return distance, None
    angle = math.radians(column.angle)
    local_2, local_3 = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
    along = abs(direction[0] * local_2[0] + direction[1] * local_2[1])
    across = abs(direction[0] * local_3[0] + direction[1] * local_3[1])
    reach_2 = column.depth / 2.0 / along if along > 1e-9 else math.inf
    reach_3 = column.width / 2.0 / across if across > 1e-9 else math.inf
    # leaving through the face square to local 2: that face runs along local 3
    return distance, (local_3 if reach_2 <= reach_3 else local_2)


def _passes(other: PlanBeam, x: float, y: float) -> str:
    """How the centre line of ``other`` meets the point: "through" (the point is
    inside its length), "end" (at one of its ends) or "" (it does not)."""
    dx, dy = other.x2 - other.x1, other.y2 - other.y1
    length = math.hypot(dx, dy)
    if length <= ON_LINE:
        return ""
    along = ((x - other.x1) * dx + (y - other.y1) * dy) / length
    off = abs((x - other.x1) * dy - (y - other.y1) * dx) / length
    if off > ON_LINE or along < -ON_LINE or along > length + ON_LINE:
        return ""
    return "through" if ON_LINE < along < length - ON_LINE else "end"


def beam_end_cuts(floor: FloorPlan) -> dict[str, tuple[tuple, tuple]]:
    """Where each beam stops at its I and J ends: {beam: (at I, at J)}, each
    (distance cut back from the joint, direction of the face it stops at).

    At a column: the face of the column, whatever the rotation of the column
    or the direction of the beam. Elsewhere: the face of the girder that
    carries it, which is any beam whose centre line passes through that end,
    as one member or as two pieces in line. Beams that only continue each
    other are not cut. The face direction lets the end of the beam follow a
    face it meets at a skew; it is None for a square end.
    """
    column_at: dict[str, PlanColumn] = {}
    for column in floor.columns_above:
        column_at[column.bottom] = column
    for column in floor.columns:          # the column below the floor comes first
        column_at[column.top] = column

    def cut(beam: PlanBeam, joint: str) -> tuple[float, tuple[float, float] | None]:
        direction = _direction(beam, joint)
        if direction is None:
            return 0.0, None
        if joint in column_at:
            return column_face(column_at[joint], direction)
        x, y = (beam.x1, beam.y1) if joint == beam.joint_i else (beam.x2, beam.y2)
        meeting = []
        for other in floor.beams:
            if other.name == beam.name or other.width <= 0:
                continue
            how = _passes(other, x, y)
            if not how:
                continue
            length = math.hypot(other.x2 - other.x1, other.y2 - other.y1)
            along = ((other.x2 - other.x1) / length, (other.y2 - other.y1) / length)
            meeting.append((other, how, along))
        best: tuple[float, tuple[float, float] | None] = (0.0, None)
        for other, how, along in meeting:
            cosine = direction[0] * along[0] + direction[1] * along[1]
            if abs(cosine) >= COLLINEAR:
                continue                   # it continues this beam, or lies on it
            if how == "end":               # two pieces in line make the girder
                partner = any(abs(along[0] * a[0] + along[1] * a[1]) >= COLLINEAR
                              for third, _, a in meeting if third.name != other.name)
                if not partner:
                    continue
            sine = math.sqrt(max(1.0 - cosine * cosine, 1e-9))
            distance = other.width / 2.0 / sine
            if distance > best[0]:
                best = (distance, along)
        return best

    return {beam.name: (cut(beam, beam.joint_i), cut(beam, beam.joint_j))
            for beam in floor.beams}


def beam_trims(floor: FloorPlan) -> dict[str, tuple[float, float]]:
    """How much each beam is cut back at its I and J ends (see ``beam_end_cuts``)."""
    return {name: (at_i[0], at_j[0]) for name, (at_i, at_j) in beam_end_cuts(floor).items()}


def skew_end(direction: tuple[float, float], face: tuple[float, float] | None
             ) -> tuple[tuple[float, float], float] | None:
    """For a beam running along ``direction`` that stops at a face running along
    ``face``: (the direction of its end cut, pointing to the left of the beam,
    and how much longer than the width that cut is). None for a square end,
    and for a face nearly in line with the beam."""
    if face is None:
        return None
    left = (-direction[1], direction[0])
    across = face[0] * left[0] + face[1] * left[1]
    if abs(across) < MIN_SKEW or abs(across) > 1.0 - 1e-9:
        return None
    if across < 0:
        face, across = (-face[0], -face[1]), -across
    return face, 1.0 / across


def trimmed_ends(beam: PlanBeam, trims: tuple[float, float]
                 ) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The two ends of a beam after its cuts; None when nothing is left of it."""
    length = math.hypot(beam.x2 - beam.x1, beam.y2 - beam.y1)
    if length <= trims[0] + trims[1] + 1.0:
        return None
    ux, uy = (beam.x2 - beam.x1) / length, (beam.y2 - beam.y1) / length
    return ((beam.x1 + ux * trims[0], beam.y1 + uy * trims[0]),
            (beam.x2 - ux * trims[1], beam.y2 - uy * trims[1]))


def marked_columns(floors: list[FloorPlan], where: str) -> set[tuple[str, str]]:
    """(story, column name) of the columns that get their mark.

    ``BOTTOM_ONLY`` marks each column where it starts: on the lowest floor that
    shows a column at that place.
    """
    marked, seen = set(), set()
    for floor in floors:                   # from the bottom up
        for column in floor.columns:
            place = (round(column.x / 10.0), round(column.y / 10.0))
            if where != BOTTOM_ONLY or place not in seen:
                marked.add((floor.story, column.name))
            seen.add(place)
    return marked


def extent(floors: list[FloorPlan]) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) of everything drawn, the same for every floor."""
    xs, ys = [], []
    for floor in floors:
        for beam in floor.beams:
            xs += [beam.x1, beam.x2]
            ys += [beam.y1, beam.y2]
        for column in floor.columns + floor.columns_above:
            reach = max(column.width, column.depth) / 2.0
            xs += [column.x - reach, column.x + reach]
            ys += [column.y - reach, column.y + reach]
        for wall in floor.walls:
            xs += [wall.x1, wall.x2]
            ys += [wall.y1, wall.y2]
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def grid_in_box(grid: PlanGrid, box: tuple[float, float, float, float]) -> PlanGrid:
    """The grid line running across ``box`` (xmin, ymin, xmax, ymax), end to end;
    unchanged when it does not cross the box."""
    dx, dy = grid.x2 - grid.x1, grid.y2 - grid.y1
    lower, upper = -math.inf, math.inf
    for start, delta, low, high in ((grid.x1, dx, box[0], box[2]), (grid.y1, dy, box[1], box[3])):
        if abs(delta) < 1e-9:
            if not low - 1e-6 <= start <= high + 1e-6:
                return grid
            continue
        a, b = (low - start) / delta, (high - start) / delta
        lower, upper = max(lower, min(a, b)), min(upper, max(a, b))
    if not lower < upper or math.isinf(lower):
        return grid
    return PlanGrid(grid.label, grid.x1 + lower * dx, grid.y1 + lower * dy,
                    grid.x1 + upper * dx, grid.y1 + upper * dy, grid.bubble_at_end)


def readable_angle(dx: float, dy: float) -> float:
    """Angle of a text along (dx, dy), in degrees, so it reads from the bottom
    or from the right of the sheet."""
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90.0 + 1e-6:
        angle -= 180.0
    elif angle <= -90.0 + 1e-6:
        angle += 180.0
    return angle


# =============================================================================
# DRAWING
# =============================================================================
def _new_document(options: PlanOptions):
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4                                  # millimetres
    doc.header["$LTSCALE"] = 10.0 * options.scale  # dashes about 2.5 mm long on paper
    doc.header["$MEASUREMENT"] = 1
    if "HIDDEN" not in doc.linetypes:   # the dashes of a member seen from above
        doc.linetypes.add("HIDDEN", pattern=[0.375, 0.25, -0.125],
                          description="Hidden __ __ __ __ __ __ __ __")
    for name, color, linetype in LAYERS:
        doc.layers.add(name, color=color, linetype=linetype)
    style = doc.mline_styles.new(BEAM_STYLE)
    style.elements.append(0.5, 256, "HIDDEN")
    style.elements.append(-0.5, 256, "HIDDEN")
    height = options.text_height
    doc.dimstyles.new(DIM_STYLE, dxfattribs={
        "dimtxt": height, "dimasz": 0.6 * height, "dimtsz": 0.5 * height,
        "dimexe": 0.5 * height, "dimexo": 0.5 * height, "dimgap": 0.3 * height,
        "dimtad": 1, "dimdec": 0, "dimlfac": 1.0, "dimscale": 1.0, "dimtih": 0, "dimtoh": 0,
    })
    return doc


def _text(msp, text: str, point, height: float, angle: float = 0.0, align: str = "MIDDLE_CENTER",
          layer: str = LAYER_TEXT) -> None:
    from ezdxf.enums import TextEntityAlignment

    entity = msp.add_text(str(text), height=height,
                          dxfattribs={"layer": layer, "rotation": angle})
    entity.set_placement(point, align=getattr(TextEntityAlignment, align))


def _solid(msp, points: list[tuple[float, float]], layer: str) -> None:
    hatch = msp.add_hatch(color=256, dxfattribs={"layer": layer})
    hatch.paths.add_polyline_path(points, is_closed=True)
    msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})


def _skew_ends(line, width: float, faces: tuple) -> None:
    """Make the two ends of a beam multiline follow the faces they stop at.

    A multiline stores, at each vertex, the direction of its end cut and how
    far along it each of its lines starts. Square ends are what it has when
    it is made; here an end that meets a face at a skew is cut along that face.
    """
    if len(line.vertices) != 2:
        return
    for vertex, face in zip(line.vertices, faces):
        direction = (vertex.line_direction.x, vertex.line_direction.y)
        skew = skew_end(direction, face)
        if skew is None:
            continue
        (mx, my), stretch = skew
        vertex.miter_direction = type(vertex.miter_direction)(mx, my, 0.0)
        vertex.line_params = [(width / 2.0 * stretch, 0.0), (-width / 2.0 * stretch, 0.0)]


def draw_floor(msp, floor: FloorPlan, offset: tuple[float, float], options: PlanOptions,
               marked: set[tuple[str, str]]) -> dict[str, int]:
    """Draw one floor with its lower left moved by ``offset``. Returns the counts."""
    from ezdxf.entities import MLine

    ox, oy = offset
    height = options.text_height
    counts = {"beams": 0, "single_line": 0, "columns": 0, "walls": 0}
    cuts = beam_end_cuts(floor)
    for beam in floor.beams:
        at_i, at_j = cuts[beam.name]
        ends = trimmed_ends(beam, (at_i[0], at_j[0]))
        if ends is None:
            continue
        (x1, y1), (x2, y2) = ends
        layer = LAYER_GIRDER if beam.girder else LAYER_BEAM
        if beam.width > 0:
            line = msp.add_mline([(x1 + ox, y1 + oy), (x2 + ox, y2 + oy)], dxfattribs={
                "layer": layer, "style_name": BEAM_STYLE, "scale_factor": beam.width,
                "justification": MLine.ZERO})
            _skew_ends(line, beam.width, (at_i[1], at_j[1]))
        else:   # a section that is not a rectangle: its centre line
            msp.add_line((x1 + ox, y1 + oy), (x2 + ox, y2 + oy),
                         dxfattribs={"layer": layer, "linetype": "HIDDEN"})
            counts["single_line"] += 1
        counts["beams"] += 1
        length = math.hypot(x2 - x1, y2 - y1)
        nx, ny = -(y2 - y1) / length, (x2 - x1) / length
        angle = readable_angle(x2 - x1, y2 - y1)
        if nx * math.cos(math.radians(angle + 90.0)) + ny * math.sin(math.radians(angle + 90.0)) < 0:
            nx, ny = -nx, -ny              # the mark sits above the text line
        gap = beam.width / 2.0 + 0.8 * height
        _text(msp, beam.name, ((x1 + x2) / 2.0 + nx * gap + ox, (y1 + y2) / 2.0 + ny * gap + oy),
              height, angle)
    for wall in floor.walls:
        outline = wall_outline(wall)
        if outline:
            _solid(msp, [(x + ox, y + oy) for x, y in outline], LAYER_WALL)
            counts["walls"] += 1
    for column in floor.columns:
        if column.circular:
            hatch = msp.add_hatch(color=256, dxfattribs={"layer": LAYER_COLUMN})
            edge = hatch.paths.add_edge_path()
            edge.add_arc((column.x + ox, column.y + oy), column.depth / 2.0, 0, 360)
            msp.add_circle((column.x + ox, column.y + oy), column.depth / 2.0,
                           dxfattribs={"layer": LAYER_COLUMN})
        else:
            _solid(msp, [(x + ox, y + oy) for x, y in column_outline(column)], LAYER_COLUMN)
        counts["columns"] += 1
        if (floor.story, column.name) in marked:
            reach = column.depth / 2.0 if column.circular else max(
                abs(x - column.x) for x, _ in column_outline(column))
            rise = column.depth / 2.0 if column.circular else max(
                abs(y - column.y) for _, y in column_outline(column))
            _text(msp, column_mark(column.name),
                  (column.x + reach + 0.4 * height + ox, column.y + rise + 0.4 * height + oy),
                  height, align="BOTTOM_LEFT")
    return counts


def draw_grids(msp, grids: list[PlanGrid], box: tuple[float, float, float, float],
               offset: tuple[float, float], options: PlanOptions) -> None:
    """The grid lines across ``box`` with their bubbles and, when asked, the
    dimensions between the grids that run along X or along Y."""
    ox, oy = offset
    height = options.text_height
    radius = 1.6 * height
    margin = 12.0 * height
    wide = (box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin)
    vertical, horizontal = [], []
    for grid in grids:
        line = grid_in_box(grid, wide)
        dx, dy = line.x2 - line.x1, line.y2 - line.y1
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        ux, uy = dx / length, dy / length
        # the bubble sits at the left or the bottom end, whichever way the line was drawn
        if (abs(ux) >= abs(uy) and ux < 0) or (abs(ux) < abs(uy) and uy < 0):
            line = PlanGrid(line.label, line.x2, line.y2, line.x1, line.y1)
            ux, uy = -ux, -uy
        msp.add_line((line.x1 + ox, line.y1 + oy), (line.x2 + ox, line.y2 + oy),
                     dxfattribs={"layer": LAYER_GRID})
        centre = (line.x1 - ux * radius + ox, line.y1 - uy * radius + oy)
        msp.add_circle(centre, radius, dxfattribs={"layer": LAYER_GRID, "linetype": "Continuous"})
        _text(msp, line.label, centre, height, layer=LAYER_GRID)
        if abs(ux) < 1e-6:
            vertical.append(line.x1)
        elif abs(uy) < 1e-6:
            horizontal.append(line.y1)
    if not options.dimensions:
        return
    near, far = margin - 5.0 * height, margin - 8.5 * height   # from the line ends inward

    def chain(values: list[float], along_x: bool) -> None:
        values = sorted(set(round(v, 3) for v in values))
        pairs = list(zip(values, values[1:]))
        if len(values) > 2:
            pairs.append((values[0], values[-1]))                # the overall dimension
        for index, (a, b) in enumerate(pairs):
            overall = len(values) > 2 and index == len(pairs) - 1
            inward = far if overall else near
            if along_x:   # grids along Y, spaced in X: the dimension line is below the plan
                y = wide[1] + inward + oy
                dim = msp.add_linear_dim(base=((a + b) / 2.0 + ox, y), p1=(a + ox, y),
                                         p2=(b + ox, y), angle=0, dimstyle=DIM_STYLE,
                                         dxfattribs={"layer": LAYER_DIM})
            else:
                x = wide[0] + inward + ox
                dim = msp.add_linear_dim(base=(x, (a + b) / 2.0 + oy), p1=(x, a + oy),
                                         p2=(x, b + oy), angle=90, dimstyle=DIM_STYLE,
                                         dxfattribs={"layer": LAYER_DIM})
            dim.render()

    chain(vertical, along_x=True)
    chain(horizontal, along_x=False)


def write_framing_plans(floors: list[FloorPlan], grids: list[PlanGrid], path: str,
                        options: PlanOptions | None = None) -> dict[str, int]:
    """Write the DXF of every floor, side by side from the lowest. Returns the
    counts of what was drawn."""
    options = options or PlanOptions()
    doc = _new_document(options)
    msp = doc.modelspace()
    height = options.text_height
    box = extent(floors)
    room = 12.0 * height + 4.0 * height if options.grids and grids else 4.0 * height
    pitch = (box[2] - box[0]) + 2.0 * room + 10.0 * height
    marked = marked_columns(floors, options.column_marks)
    totals = {"floors": 0, "beams": 0, "single_line": 0, "columns": 0, "walls": 0}
    for index, floor in enumerate(floors):
        offset = (index * pitch, 0.0)
        if options.grids and grids:
            draw_grids(msp, grids, box, offset, options)
        counts = draw_floor(msp, floor, offset, options, marked)
        for key, value in counts.items():
            totals[key] += value
        totals["floors"] += 1
        middle = (box[0] + box[2]) / 2.0 + offset[0]
        base = box[1] - room - 4.0 * height
        _text(msp, f"{floor.story} FRAMING PLAN", (middle, base), 1.6 * height)
        _text(msp, f"ELEV. {floor.elevation / 1000.0:+.2f} m", (middle, base - 2.6 * height),
              height)
    doc.saveas(path)
    return totals


# =============================================================================
# ETABS
# =============================================================================
def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def read_floor_plans(model) -> tuple[list[FloorPlan], list[PlanGrid], list[str]]:
    """The floors, the grid lines and the sections drawn as one line, from the
    open model (in N-mm). Floors come from the lowest up; a story with no
    beam, column or wall is left out."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows.grid_column_model import _table

    points = {str(r["UniqueName"]): (_number(r["X"]), _number(r["Y"]), _number(r["Z"]))
              for r in _table(model, "Point Object Connectivity")}
    summary = {str(r["UniqueName"]): r for r in _table(model, "Frame Assignments - Summary")}
    rectangles = {str(r["Name"]): (_number(r.get("t2")), _number(r.get("t3")))   # width, depth
                  for r in _table(model, "Frame Section Property Definitions - Concrete Rectangular")}
    circles = {str(r["Name"]): _number(r.get("t3"))
               for r in _table(model, "Frame Section Property Definitions - Concrete Circle")}

    def section_of(name: str) -> str:
        info = summary.get(name, {})
        return str(info.get("AnalysisSect") or info.get("DesignSect") or "")

    stories = model.Story.GetStories()
    names = [str(n) for n in as_list(stories[1])]
    elevations = [float(z) for z in as_list(stories[2])]
    floors = {name: FloorPlan(name, elevation) for name, elevation in zip(names[1:], elevations[1:])}

    columns_of: dict[str, list[PlanColumn]] = {}
    for row in _table(model, "Column Object Connectivity"):
        name, a, b = str(row["UniqueName"]), str(row["UniquePtI"]), str(row["UniquePtJ"])
        if a not in points or b not in points:
            continue
        bottom, top = (a, b) if points[a][2] <= points[b][2] else (b, a)
        section = section_of(name)
        angle = _number(summary.get(name, {}).get("AxisAngle"))
        if section in circles:
            width = depth = circles[section]
            circular = True
        else:
            width, depth = rectangles.get(section, (0.0, 0.0))
            circular = False
        if width <= 0 or depth <= 0:
            continue   # not a rectangle or a circle: nothing true to draw
        columns_of.setdefault(str(row["Story"]), []).append(PlanColumn(
            name, points[top][0], points[top][1], width, depth, angle, circular, top, bottom))
    for story, columns in columns_of.items():
        if story in floors:
            floors[story].columns = columns
    order = names[1:]
    for index, story in enumerate(order[:-1]):       # columns that start on this floor
        below = {column.top for column in floors[story].columns}
        floors[story].columns_above = [
            PlanColumn(c.name, points[c.bottom][0], points[c.bottom][1], c.width, c.depth,
                       c.angle, c.circular, c.top, c.bottom)
            for c in floors[order[index + 1]].columns if c.bottom not in below]

    single_line = set()
    for row in _table(model, "Beam Object Connectivity"):
        name, a, b = str(row["UniqueName"]), str(row["UniquePtI"]), str(row["UniquePtJ"])
        story = str(row["Story"])
        if a not in points or b not in points or story not in floors:
            continue
        section = section_of(name)
        width = rectangles.get(section, (0.0, 0.0))[0]
        if width <= 0:
            single_line.add(section or "no section")
        floor = floors[story]
        supports = ({c.top for c in floor.columns} | {c.bottom for c in floor.columns_above})
        floor.beams.append(PlanBeam(name, points[a][0], points[a][1], points[b][0], points[b][1],
                                    width, a, b, girder=a in supports or b in supports))

    wall_section = {str(r["UniqueName"]): str(r["SectProp"])
                    for r in _table(model, "Area Assignments - Section Properties")}
    thickness = {str(r["Name"]): _number(r.get("Thickness"))
                 for r in _table(model, "Wall Property Definitions - Specified")}
    for row in _table(model, "Wall Object Connectivity"):
        corners = {(round(points[str(row[f"UniquePt{i}"])][0], 3),
                    round(points[str(row[f"UniquePt{i}"])][1], 3))
                   for i in range(1, 5) if str(row.get(f"UniquePt{i}")) in points}
        story = str(row["Story"])
        if len(corners) != 2 or story not in floors:
            continue   # not a plain vertical panel
        (x1, y1), (x2, y2) = sorted(corners)
        name = str(row["UniqueName"])
        thick = thickness.get(wall_section.get(name, ""), 0.0)
        if thick > 0:
            floors[story].walls.append(PlanWall(name, x1, y1, x2, y2, thick))

    plans = [floors[name] for name in order
             if floors[name].beams or floors[name].columns or floors[name].walls]
    return plans, read_grids(model, extent(plans)), sorted(single_line)


def read_grids(model, box: tuple[float, float, float, float]) -> list[PlanGrid]:
    """The visible grid lines of every grid system, in global coordinates.

    A grid system has its own origin and rotation. A general line is given by
    its two ends; an X or Y line by its ordinate, and then runs across ``box``.
    """
    from etabs_api.workflows.grid_column_model import _table

    systems = {str(r.get("Name")): (_number(r.get("Ux")), _number(r.get("Uy")),
                                    math.radians(_number(r.get("Rz"))))
               for r in _table(model, "Grid Definitions - General")}
    reach = max(box[2] - box[0], box[3] - box[1], 1000.0)

    def to_global(system: str, x: float, y: float) -> tuple[float, float]:
        ux, uy, rz = systems.get(system, (0.0, 0.0, 0.0))
        return (ux + x * math.cos(rz) - y * math.sin(rz), uy + x * math.sin(rz) + y * math.cos(rz))

    grids = []
    for row in _table(model, "Grid Definitions - Grid Lines"):
        if str(row.get("Visible", "Yes")).strip().casefold() == "no":
            continue
        kind, system = str(row.get("LineType", "")), str(row.get("Name"))
        at_end = str(row.get("BubbleLoc", "Start")).strip().casefold() == "end"
        if kind.startswith("General") and row.get("X1") not in (None, "", "None"):
            ends = [(_number(row["X1"]), _number(row["Y1"])), (_number(row["X2"]), _number(row["Y2"]))]
        elif kind.startswith("X") and row.get("Ordinate") not in (None, "", "None"):
            ordinate = _number(row["Ordinate"])
            ends = [(ordinate, -reach * 3.0), (ordinate, reach * 3.0)]
        elif kind.startswith("Y") and row.get("Ordinate") not in (None, "", "None"):
            ordinate = _number(row["Ordinate"])
            ends = [(-reach * 3.0, ordinate), (reach * 3.0, ordinate)]
        else:
            continue
        (x1, y1), (x2, y2) = (to_global(system, *end) for end in ends)
        grids.append(PlanGrid(str(row.get("ID")), x1, y1, x2, y2, at_end))
    return grids


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
SCALES = {"1 : 100": 100.0, "1 : 50": 50.0, "1 : 200": 200.0}


def plans_summary(totals: dict[str, int], options: PlanOptions, model_path: str | None,
                  path: str, single_line: list[str], skipped: int = 0):
    """The closing summary of the export, for its window."""
    from utilities.run_summary import RunSummary, listed

    summary = RunSummary("sdt plans", model_path)
    summary.add("Floors", totals["floors"])
    summary.add("Beams and girders", totals["beams"])
    summary.add("Columns", totals["columns"])
    summary.add("Walls", totals["walls"])
    summary.add("Grids", ("with dimensions" if options.dimensions else "with bubbles")
                if options.grids else "not drawn")
    summary.add("Column marks", "where each column starts"
                if options.column_marks == BOTTOM_ONLY else "on every floor")
    summary.add("Text", f"{options.text_height:g} mm high, for a plot at 1 : {options.scale:g}")
    if single_line:
        summary.note(f"{totals['single_line']} beams are drawn as one line: their section is "
                     "not a concrete rectangle (" + listed(single_line, 6) + ").")
    summary.note("Drawn at 1 : 1 in mm. Beams are multilines: they keep their width when an end "
                 "is dragged. The model was not changed.")
    summary.file("Framing plans", path)
    return summary


def run_framing_plans() -> str | None:
    """Entry point: ask the options and write the DXF of the open model."""
    from etabs_api.core.connection import attach_running_etabs
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_output_directory,
        show_warning,
    )

    title = "Framing Plans"
    connector = attach_running_etabs(title)
    if connector is None:
        return None
    model = connector.sap_model
    model_path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    stem = os.path.splitext(os.path.basename(model_path))[0] or "model"

    options = PlanOptions()
    grids = select_option(title, "Draw the grid lines with their bubbles?",
                          ["Yes", "Yes, with the dimensions between the grids", "No"])
    if grids is None:
        return None
    options.grids, options.dimensions = grids != "No", "dimensions" in grids
    marks = select_option(
        title, "Column marks (the mark is the name without its level, such as C1):",
        ["On every floor", "Only where each column starts (the bottom-most level)"])
    if marks is None:
        return None
    options.column_marks = BOTTOM_ONLY if marks.startswith("Only") else EVERY_LEVEL
    scale = select_option(title, "Plot scale, for the text height and the dashes (the plan "
                          "itself is drawn at 1 : 1 in mm):", list(SCALES))
    if scale is None:
        return None
    options.scale = SCALES[scale]
    folder = select_output_directory("Folder for the framing plans (.dxf)")
    if not folder:
        return None
    path = os.path.join(folder, f"{stem} - Framing Plans.dxf")

    try:
        with LoadingWindow("Framing plans") as window:
            window.update("Reading the frames, sections, walls and grids")
            floors, grid_lines, single_line = read_floor_plans(model)
            if not floors:
                show_warning("The model has no beams, columns or walls to draw.", title=title)
                return None
            window.update(f"Drawing {len(floors)} floors")
            totals = write_framing_plans(floors, grid_lines, path, options)
    except PermissionError:
        show_warning(f"{path} is open in another program. Close it and run sdt plans again.",
                     title=title)
        return None
    print(f"Framing plans saved: {path}")
    plans_summary(totals, options, model_path, path, single_line).show(popup=True)
    return path


if __name__ == "__main__":
    run_framing_plans()
