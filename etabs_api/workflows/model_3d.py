"""The concrete of the model as 3D solids, in one STEP file (``sdt 3d``).

Columns, beams, walls and slabs of the model open in ETABS are written as
solids at their true size, in mm, in a STEP file that AutoCAD and other CAD
programs import. The foundation is added from the plan saved by
``sdt foundation`` (footings and tie beams). The model is read, not changed.

* **Concrete only.** A frame whose section is not a concrete rectangle or
  circle, and a floor that is not a slab (a deck), is left out and counted.
* **Reinforcing bars, if asked for.** The bars of the last beam and column
  designs saved for the model, straight and with no laps or hooks
  (``model_3d_bars.py``).
* **One shape, many places.** Members of the same size are one shape in the
  file, placed once for each member with its name. A building of thousands of
  members stays a small file.

The solids are made with build123d, which is loaded only when the file is
written. The functions that only work on plain data need no ETABS and no
build123d.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

Point = tuple[float, float, float]

COLUMN, BEAM, WALL, SLAB, FOOTING, TIE_BEAM = (
    "column", "beam", "wall", "slab", "footing", "tie beam")
REBAR, TIE = "bar", "tie"          # a longitudinal bar; a stirrup, tie or spiral ring
KINDS = (COLUMN, BEAM, WALL, SLAB, FOOTING, TIE_BEAM, REBAR, TIE)
# red, green, blue of each kind in the file (0 to 1)
COLOURS = {COLUMN: (0.55, 0.55, 0.58), BEAM: (0.72, 0.72, 0.70), WALL: (0.62, 0.60, 0.55),
           SLAB: (0.85, 0.85, 0.83), FOOTING: (0.60, 0.52, 0.45), TIE_BEAM: (0.66, 0.60, 0.52),
           REBAR: (0.75, 0.20, 0.15), TIE: (0.15, 0.35, 0.70)}
FOUNDATION_GROUP = "FOUNDATION"
SAME_SIZE = 1.0          # mm: members whose sizes agree to this are one shape
MIN_LENGTH = 1.0         # mm: nothing shorter is drawn
SHORT = "beams with nothing left between their supports"
NOT_SLABS = "floors that are not slabs"
NOT_MADE = "solids that could not be made"


@dataclass
class Bar:
    """A straight member of constant section: a column, beam, wall or tie beam.

    ``start`` to ``end`` is the centre line of the solid. ``width`` is across
    it and ``depth`` along ``up`` (for a wall: its thickness and its height).
    A circular bar has its diameter in both.
    """

    kind: str
    name: str
    group: str               # the story it belongs to, or FOUNDATION
    start: Point
    end: Point
    width: float
    depth: float
    up: Point = (0.0, 0.0, 1.0)
    circular: bool = False

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)


@dataclass
class Plate:
    """A flat member of constant thickness: a slab or a footing. ``outline`` is
    its plan, ``top`` the elevation of its top face."""

    kind: str
    name: str
    group: str
    outline: list[tuple[float, float]]
    top: float
    thickness: float


@dataclass
class Hoop:
    """A stirrup, a column tie or one ring of a spiral: a closed rectangle (or
    a circle) of bar, square to the member. ``width`` (to the side) and
    ``depth`` (along ``up``) are those of its centre line; ``inner_up`` and
    ``inner_side`` are its inner legs along ``up`` and to the side."""

    kind: str
    name: str                # the member it belongs to
    group: str
    centre: Point
    axis: Point              # along the member: the hoop is square to it
    up: Point
    width: float
    depth: float
    diameter: float
    inner_up: int = 0
    inner_side: int = 0
    circular: bool = False


@dataclass
class Model3D:
    bars: list[Bar] = field(default_factory=list)
    plates: list[Plate] = field(default_factory=list)
    hoops: list[Hoop] = field(default_factory=list)
    left_out: dict[str, int] = field(default_factory=dict)     # what was not drawn, counted
    sections: set[str] = field(default_factory=set)            # the frame sections left out

    def count(self, kind: str) -> int:
        return sum(1 for item in self.bars + self.plates + self.hoops if item.kind == kind)

    def skip(self, what: str, number: int = 1) -> None:
        if number:
            self.left_out[what] = self.left_out.get(what, 0) + number


# =============================================================================
# GEOMETRY (plain data)
# =============================================================================
def _unit(vector: Point) -> Point | None:
    size = math.sqrt(sum(v * v for v in vector))
    return None if size < 1e-9 else (vector[0] / size, vector[1] / size, vector[2] / size)


def _cross(a: Point, b: Point) -> Point:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def square_up(axis: Point, up: Point) -> Point:
    """The direction nearest ``up`` that is square to ``axis`` (both unit vectors)."""
    along = sum(a * u for a, u in zip(axis, up))
    squared = _unit(tuple(u - along * a for a, u in zip(axis, up)))
    if squared is not None:
        return squared
    # up runs along the axis: any direction square to it
    other = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(axis, other))


def column_bar(name: str, group: str, bottom: Point, top: Point, width: float, depth: float,
               angle: float = 0.0, circular: bool = False) -> Bar | None:
    """A column from its bottom joint to its top joint. Its depth is along
    ``angle`` (degrees from global X, the ETABS local 2 axis), its width across."""
    if math.dist(bottom, top) < MIN_LENGTH or width <= 0 or depth <= 0:
        return None
    turn = math.radians(angle)
    return Bar(COLUMN, name, group, bottom, top, width, depth,
               (math.cos(turn), math.sin(turn), 0.0), circular)


def beam_bar(name: str, group: str, joint_i: Point, joint_j: Point, width: float, depth: float,
             trims: tuple[float, float] = (0.0, 0.0), kind: str = BEAM) -> Bar | None:
    """A beam with its top on the line of its joints (the ETABS insertion point
    of a beam, top centre), cut back at each end by ``trims`` (in plan, mm)."""
    axis = _unit(tuple(b - a for a, b in zip(joint_i, joint_j)))
    if axis is None or width <= 0 or depth <= 0:
        return None
    level = math.hypot(axis[0], axis[1])           # 1 for a level beam, less when it slopes
    if level < 1e-6:
        return None                                # it stands upright: not a beam
    up = square_up(axis, (0.0, 0.0, 1.0))
    cut_i, cut_j = trims[0] / level, trims[1] / level
    if math.dist(joint_i, joint_j) - cut_i - cut_j < MIN_LENGTH:
        return None                                # nothing is left between its supports
    start = tuple(p + cut_i * a - 0.5 * depth * u for p, a, u in zip(joint_i, axis, up))
    end = tuple(p - cut_j * a - 0.5 * depth * u for p, a, u in zip(joint_j, axis, up))
    return Bar(kind, name, group, start, end, width, depth, up)


def wall_bar(name: str, group: str, x1: float, y1: float, x2: float, y2: float,
             bottom: float, top: float, thickness: float) -> Bar | None:
    """A wall panel between two plan points, from ``bottom`` to ``top``."""
    if math.hypot(x2 - x1, y2 - y1) < MIN_LENGTH or top - bottom < MIN_LENGTH or thickness <= 0:
        return None
    middle = 0.5 * (bottom + top)
    return Bar(WALL, name, group, (x1, y1, middle), (x2, y2, middle), thickness, top - bottom)


def bar_shape(bar: Bar) -> tuple:
    """What makes two bars the same solid: their section and their length."""
    step = SAME_SIZE
    return ("circle" if bar.circular else "box", round(bar.length / step),
            round(bar.width / step), round(bar.depth / step))


def plate_shape(plate: Plate) -> tuple:
    """What makes two plates the same solid: their outline from its first
    corner, and their thickness."""
    x0, y0 = plate.outline[0]
    return ("plate", round(plate.thickness / SAME_SIZE),
            tuple((round((x - x0) / SAME_SIZE), round((y - y0) / SAME_SIZE))
                  for x, y in plate.outline))


def clean_outline(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """An outline with no corner repeated, the first one not given again at the end."""
    out: list[tuple[float, float]] = []
    for x, y in points:
        if not out or math.hypot(x - out[-1][0], y - out[-1][1]) > 0.5:
            out.append((float(x), float(y)))
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 0.5:
        out.pop()
    return out


def nearest_story(z: float, stories: list[tuple[str, float]]) -> str:
    """The story whose elevation is nearest ``z``."""
    return min(stories, key=lambda story: abs(story[1] - z))[0] if stories else ""


def foundation_parts(plan: dict) -> tuple[list[Bar], list[Plate]]:
    """The footings and tie beams of a saved foundation plan
    (``foundation_plan.load_plan``). A footing goes down from the level of its
    shells by its thickness; a tie beam has its top on its joints and stops at
    the column faces, as on the foundation plan."""
    from etabs_api.workflows.framing_plans import beam_trims, foundation_floor

    plates = []
    for footing in plan["footings"]:
        outline = clean_outline(footing.outline)
        if len(outline) >= 3 and footing.thickness > 0:
            plates.append(Plate(FOOTING, footing.mark, FOUNDATION_GROUP, outline, footing.z,
                                footing.thickness))
    trims = beam_trims(foundation_floor(plan))
    bars = []
    for beam in plan["tie_beams"]:
        bar = beam_bar(beam.mark, FOUNDATION_GROUP, (beam.x1, beam.y1, beam.z),
                       (beam.x2, beam.y2, beam.z), beam.width, beam.depth,
                       trims.get(beam.mark, (0.0, 0.0)), TIE_BEAM)
        if bar is not None:
            bars.append(bar)
    return bars, plates


# =============================================================================
# SOLIDS AND THE FILE (build123d)
# =============================================================================
def build_assembly(model: Model3D, label: str = "MODEL"):
    """The solids of the model as one build123d assembly: a group for each story
    (and one for the foundation), each member in it with its name. Returns the
    assembly and the number of different shapes it is made of."""
    import build123d as bd

    shapes: dict[tuple, object] = {}

    def coloured(solid, kind: str):
        solid.color = bd.Color(*COLOURS[kind])
        return solid

    def bar_solid(bar: Bar):
        key = (bar.kind, *bar_shape(bar))
        if key not in shapes:
            if bar.circular:
                solid = bd.Solid.make_cylinder(bar.depth / 2.0, bar.length,
                                               bd.Plane((0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)))
            else:
                solid = bd.Solid.make_box(bar.length, bar.width, bar.depth, bd.Plane(
                    (0.0, -bar.width / 2.0, -bar.depth / 2.0)))
            shapes[key] = coloured(solid, bar.kind)
        axis = _unit(tuple(b - a for a, b in zip(bar.start, bar.end)))
        up = square_up(axis, _unit(bar.up) or (0.0, 0.0, 1.0))
        return key, shapes[key].moved(bd.Location(bd.Plane(bar.start, x_dir=axis, z_dir=up)))

    def plate_solid(plate: Plate):
        key = (plate.kind, *plate_shape(plate))
        x0, y0 = plate.outline[0]
        if key not in shapes:
            face = bd.Face(bd.Wire.make_polygon(
                [bd.Vector(x - x0, y - y0, 0.0) for x, y in plate.outline], close=True))
            solid = bd.Solid.extrude(face, bd.Vector(0, 0, -plate.thickness))
            if len(plate.outline) < 3 or not solid.is_valid or solid.volume <= 0:
                raise ValueError(f"no solid for the outline of {plate.name}")
            shapes[key] = coloured(solid, plate.kind)
        return key, shapes[key].moved(bd.Location((x0, y0, plate.top)))

    def hoop_solid(hoop: Hoop):
        step = SAME_SIZE
        key = (hoop.kind, hoop.circular, round(hoop.width / step), round(hoop.depth / step),
               round(hoop.diameter / step), hoop.inner_up, hoop.inner_side)
        if key not in shapes:
            radius, w, d = hoop.diameter / 2.0, hoop.width, hoop.depth
            if hoop.circular:           # in the plane square to the member (local X)
                shape = bd.Solid.make_torus(w / 2.0, radius, bd.Plane((0, 0, 0), z_dir=(1, 0, 0)))
            else:
                def leg(start, direction, length):
                    return bd.Solid.make_cylinder(radius, length, bd.Plane(start, z_dir=direction))

                legs = [leg((0, -w / 2 - radius, z), (0, 1, 0), w + 2 * radius)
                        for z in (-d / 2, d / 2)]
                legs += [leg((0, y, -d / 2 - radius), (0, 0, 1), d + 2 * radius)
                         for y in (-w / 2, w / 2)]
                # The inner legs lie against the hoop, one set on each side of it, as
                # crossties do. Bars that crossed in one plane gave a solid that did
                # not read back as valid.
                beside = 0.8 * hoop.diameter
                legs += [leg((beside, -w / 2 + w * k / (hoop.inner_up + 1), -d / 2 - radius),
                             (0, 0, 1), d + 2 * radius) for k in range(1, hoop.inner_up + 1)]
                legs += [leg((-beside, -w / 2 - radius, -d / 2 + d * k / (hoop.inner_side + 1)),
                             (0, 1, 0), w + 2 * radius) for k in range(1, hoop.inner_side + 1)]
                # one solid, so that the file holds it once and places it many times
                shape = legs[0].fuse(*legs[1:]).clean()
                if not isinstance(shape, bd.Solid):
                    solids = shape.solids()
                    shape = solids[0] if len(solids) == 1 else bd.Compound(legs)
            shapes[key] = coloured(shape, hoop.kind)
        axis = _unit(hoop.axis)
        up = square_up(axis, _unit(hoop.up) or (0.0, 0.0, 1.0))
        return key, shapes[key].moved(bd.Location(bd.Plane(hoop.centre, x_dir=axis, z_dir=up)))

    groups: dict[str, list] = {}
    shown: set[tuple] = set()
    for item in model.bars + model.plates + model.hoops:
        try:
            key, solid = (bar_solid(item) if isinstance(item, Bar) else
                          hoop_solid(item) if isinstance(item, Hoop) else plate_solid(item))
        except Exception:  # noqa: BLE001 - an outline the kernel cannot make a face of
            model.skip(NOT_MADE)
            continue
        solid.label = f"{item.kind.upper()} {item.name}"
        # The colour goes on the first placement of a shape only: the file then holds
        # it once, with the shape, and not once more for each of its placements.
        solid.color = None if key in shown else bd.Color(*COLOURS[item.kind])
        shown.add(key)
        groups.setdefault(item.group or "MODEL", []).append(solid)
    children = [bd.Compound(label=name, children=solids) for name, solids in groups.items()]
    return bd.Compound(label=label, children=children), len(shapes)


def write_step(model: Model3D, path: str, label: str = "MODEL") -> dict:
    """Write the model to a STEP file (mm). Returns the counts of what it holds."""
    import build123d as bd

    assembly, shapes = build_assembly(model, label)
    if not bd.export_step(assembly, path, unit=bd.Unit.MM, write_pcurves=False):
        raise OSError(f"The STEP file was not written: {path}")
    totals = {kind: model.count(kind) for kind in KINDS}
    totals.update(solids=len(assembly.leaves), shapes=shapes, size=os.path.getsize(path))
    return totals


# =============================================================================
# ETABS
# =============================================================================
def read_model(model, slabs: bool = True) -> Model3D:
    """The concrete members of the model open in ETABS (in N-mm).

    Columns run from joint to joint. Beams have their top on their joints and
    stop at the faces of the columns and of the girders that carry them, as on
    the framing plans. Walls are the vertical panels of two plan points. Slabs
    go down from their joints by the thickness of their property.
    """
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows.framing_plans import _number, beam_trims, read_floor_plans
    from etabs_api.workflows.grid_column_model import _table

    out = Model3D()
    points = {str(r["UniqueName"]): (_number(r["X"]), _number(r["Y"]), _number(r["Z"]))
              for r in _table(model, "Point Object Connectivity")}
    summary = {str(r["UniqueName"]): r for r in _table(model, "Frame Assignments - Summary")}
    rectangles = {str(r["Name"]): (_number(r.get("t2")), _number(r.get("t3")))   # width, depth
                  for r in _table(model, "Frame Section Property Definitions - Concrete Rectangular")}

    def section_of(name: str) -> str:
        info = summary.get(name, {})
        return str(info.get("AnalysisSect") or info.get("DesignSect") or "")

    floors, _, _ = read_floor_plans(model)
    drawn = set()
    for floor in floors:
        trims = beam_trims(floor)
        for column in floor.columns:
            bar = column_bar(column.name, floor.story, points[column.bottom], points[column.top],
                             column.width, column.depth, column.angle, column.circular)
            drawn.add(column.name)
            if bar is not None:
                out.bars.append(bar)
        for beam in floor.beams:
            depth = rectangles.get(section_of(beam.name), (0.0, 0.0))[1]
            bar = beam_bar(beam.name, floor.story, points[beam.joint_i], points[beam.joint_j],
                           beam.width, depth, trims.get(beam.name, (0.0, 0.0)))
            if bar is not None:
                out.bars.append(bar)
                drawn.add(beam.name)
            elif beam.width > 0 and depth > 0:
                out.skip(SHORT)
                drawn.add(beam.name)

    for table, what in (("Column Object Connectivity", "columns"),
                        ("Beam Object Connectivity", "beams"),
                        ("Brace Object Connectivity", "braces")):
        for row in _table(model, table):
            name = str(row["UniqueName"])
            if name not in drawn:
                out.skip(what)
                out.sections.add(section_of(name) or "no section")

    heights: dict[str, tuple[float, float]] = {}
    for row in _table(model, "Wall Object Connectivity"):
        levels = [points[str(row[f"UniquePt{i}"])][2] for i in range(1, 5)
                  if str(row.get(f"UniquePt{i}")) in points]
        if levels:
            heights[str(row["UniqueName"])] = (min(levels), max(levels))
    walls = 0
    for floor in floors:
        for wall in floor.walls:
            bottom, top = heights.get(wall.name, (0.0, 0.0))
            bar = wall_bar(wall.name, floor.story, wall.x1, wall.y1, wall.x2, wall.y2,
                           bottom, top, wall.thickness)
            if bar is not None:
                out.bars.append(bar)
                walls += 1
    out.skip("walls", len(heights) - walls)

    if slabs:
        from etabs_api.workflows.foundation_plan import read_shells

        got = model.Story.GetStories()
        stories = list(zip([str(n) for n in as_list(got[1])], [float(z) for z in as_list(got[2])]))
        for shell in read_shells(model, points):
            outline = clean_outline(shell.points)
            if shell.thickness <= 0 or len(outline) < 3:
                out.skip(NOT_SLABS)
                continue
            out.plates.append(Plate(SLAB, shell.name, nearest_story(shell.z, stories), outline,
                                    shell.z, shell.thickness))
    return out


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
def model_summary(totals: dict, model: Model3D, model_path: str | None, path: str,
                  with_slabs: bool, foundation: dict | None, bars: dict | None = None):
    """The closing summary of the export, for its window. ``bars``: the counts of
    ``model_3d_bars.add_reinforcement`` with ``level`` and ``note``, or None when
    the bars were not asked for."""
    from utilities.run_summary import RunSummary, listed

    summary = RunSummary("sdt 3d", model_path)
    summary.add("Columns", totals[COLUMN])
    summary.add("Beams and girders", totals[BEAM])
    summary.add("Walls", totals[WALL])
    summary.add("Slabs", totals[SLAB] if with_slabs else "not included")
    if foundation is not None:
        summary.add("Foundation", f"{totals[FOOTING]} footings, {totals[TIE_BEAM]} tie beams "
                                  f"(plan saved on {foundation['saved']})")
    else:
        summary.add("Foundation", "not included")
    if bars is None:
        summary.add("Reinforcing bars", "not included")
    else:
        summary.add("Reinforcing bars", f"{totals[REBAR]:,} bars"
                    + (f", {totals[TIE]:,} stirrups and ties" if bars.get("ties") else
                       " (stirrups and ties not included)")
                    + f", in {bars['beams']} beams and {bars['columns']} columns")
        without = bars["beams_without"] + bars["columns_without"]
        if without:
            summary.note(f"{bars['beams_without']} beams and {bars['columns_without']} columns "
                         "have no design saved for this model: they are drawn without bars.")
        for line in bars.get("notes", []):
            summary.note(line)
        summary.note("The bars are straight and stop at the ends of each member: the designs "
                     "give no laps, hooks, bends or cut-off points. Extra bars at a support or "
                     "at midspan are drawn over a fixed share of the clear span.")
    summary.add("Solids", f"{totals['solids']:,} ({totals['shapes']:,} different shapes)")
    summary.add("File size", f"{totals['size'] / 1e6:,.1f} MB")
    for what, number in model.left_out.items():
        if what == NOT_SLABS:
            summary.note(f"{number} floors are not concrete slabs (a deck, or a property with "
                         "no thickness): not drawn.")
        elif what == SHORT:
            summary.note(f"{number} beams lie inside their supports, with nothing left between "
                         "the faces: not drawn.")
        elif what == NOT_MADE:
            summary.fail(f"{number} solids could not be made (an outline that crosses itself): "
                         "not drawn.")
        elif what == "walls":
            summary.note(f"{number} walls are not plain vertical panels, or have no thickness: "
                         "not drawn.")
        else:
            summary.note(f"{number} {what} are not drawn: their section is not a concrete "
                         "rectangle or circle (" + listed(sorted(model.sections), 6) + ").")
    summary.note("In mm, at the true sizes. Members overlap "
                 "where they meet (a slab and its beams, a column and the floor): do not take "
                 "volumes from this file. The model was not changed.")
    summary.file("3D model", path)
    return summary


BARS_NONE = "No"
BARS_MAIN = "The main bars only"
BARS_ALL = "The main bars with the stirrups and ties"


def run_model_3d() -> str | None:
    """Entry point: ask the options and write the STEP file of the open model."""
    from etabs_api.core.connection import attach_running_etabs
    from etabs_api.workflows.framing_plans import saved_foundation_plan
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_output_directory,
        show_warning,
    )

    title = "3D Model"
    connector = attach_running_etabs(title)
    if connector is None:
        return None
    model = connector.sap_model
    model_path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    stem = os.path.splitext(os.path.basename(model_path))[0] or "model"

    slabs = select_option(title, "Include the floor slabs?\n\nColumns, beams and walls are "
                          "always in the 3D model. Each story is a group of its own, so the "
                          "slabs can also be hidden later in the CAD program.", ["Yes", "No"])
    if slabs is None:
        return None
    foundation = saved_foundation_plan(model_path)
    if foundation is not None:
        include = select_option(
            title, "Include the foundation?\n\nA plan of the foundation model of this model "
            f"was saved on {foundation['saved']}: {len(foundation['footings'])} footings, "
            f"{len(foundation['tie_beams'])} tie beams. If the foundation changed since, open "
            "its model and save the plan again with sdt foundation.", ["Yes", "No"])
        if include is None:
            return None
        if include == "No":
            foundation = None
    from design.concrete.workflow import DesignStore
    from etabs_api.workflows import model_3d_bars as rebar

    store = DesignStore.load(model_path)
    designed = store is not None and (store.beam_results is not None
                                      or store.column_report is not None)
    level = rebar.NO_BARS
    if designed:
        has = " and ".join(text for text, there in (
            ("the beam design", store.beam_results is not None),
            ("the column design", store.column_report is not None)) if there)
        choice = select_option(
            title, f"Include the reinforcing bars?\n\nThis model has {has} saved. The bars "
            "are drawn straight, with no laps, hooks or bends. Stirrups and ties add thousands "
            "of solids: the file is larger and slower to open.",
            [BARS_NONE, BARS_MAIN, BARS_ALL])
        if choice is None:
            return None
        level = {BARS_MAIN: rebar.MAIN_BARS, BARS_ALL: rebar.ALL_BARS}.get(choice, rebar.NO_BARS)
    folder = select_output_directory("Folder for the 3D model (.step)")
    if not folder:
        return None
    path = os.path.join(folder, f"{stem} - 3D Model.step")

    try:
        with LoadingWindow("3D model") as window:
            window.update("Reading the frames, sections, walls and slabs")
            found = read_model(model, slabs == "Yes")
            if foundation is not None:
                bars, plates = foundation_parts(foundation)
                found.bars += bars
                found.plates += plates
            placed = None
            if level != rebar.NO_BARS:
                window.update("Placing the reinforcing bars of the saved designs")
                placed = rebar.add_reinforcement(
                    found, store.beam_results, store.column_report,
                    float((store.inputs.get("column_bars") or {}).get("dmain", 0.0)), level)
                placed["ties"] = level == rebar.ALL_BARS
                placed["notes"] = (["The designs are older than the model file: run sdt beams "
                                    "and sdt columns again if the members changed."]
                                   if store.is_stale() else [])
                if store.column_report is None:
                    placed["notes"].append("No column design is saved: run sdt columns for the "
                                           "column bars.")
            if not found.bars and not found.plates:
                show_warning("The model has no concrete columns, beams, walls or slabs to draw.",
                             title=title)
                return None
            window.update("Starting the 3D kernel (build123d)")
            try:
                import build123d  # noqa: F401
            except ImportError:
                show_warning("The 3D model needs the package build123d, which is not installed "
                             "here. Install it with: pip install build123d", title=title)
                return None
            window.update(f"Making {len(found.bars) + len(found.plates) + len(found.hoops):,} "
                          "solids and writing the file")
            totals = write_step(found, path, stem)
    except PermissionError:
        show_warning(f"{path} is open in another program. Close it and run sdt 3d again.",
                     title=title)
        return None
    print(f"3D model saved: {path}")
    model_summary(totals, found, model_path, path, slabs == "Yes", foundation,
                  placed).show(popup=True)
    return path


if __name__ == "__main__":
    run_model_3d()
