"""Concrete volume, floor area and their ratio in m3/m2 (``sdt quantities``).

The command reads the model open in ETABS and gives, for each story and for
the building, the volume of concrete, the floor area, and the volume over the
area. It changes nothing in the model and needs no analysis.

What is measured, so that no concrete is counted twice:

* **Columns**: section by length, joint to joint (floor to floor).
* **Beams**: width by the depth *below the slab* by the clear length between
  the faces of their supports. The part of a beam inside the slab is slab.
* **Slabs**: the area of each floor shell, less its openings and less the part
  of each column section that lies in it, by its thickness.
* **Walls**: thickness by height by length.
* **Footings and tie beams**: from the plan of the foundation model saved by
  ``sdt foundation``, when it is included.
* **Slab on grade**: not modelled in ETABS as a rule, so its area is the
  footprint of the building, the outline of all its floors seen in plan, and
  its thickness is asked.

The **floor area** of a story is the area of its floor shells less their
openings; a level with no floor shell, such as one of grade beams, adds none.
The slab on grade adds the footprint as the ground floor. The ratio is the
total volume over the total floor area.

The functions that only work on plain data need no ETABS.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from etabs_api.workflows.model_3d import BEAM, COLUMN, TIE_BEAM, WALL, Bar

MM3, MM2 = 1e9, 1e6            # mm3 in a m3, mm2 in a m2
UNDER_SLAB = 10.0              # mm: a beam this close to a slab outline is under it
GROUND, FOUNDATION = "Slab on grade", "Foundation"


@dataclass
class FloorShell:
    """A floor shell: a slab with its thickness, or an opening."""

    name: str
    story: str
    points: list[tuple[float, float]]
    thickness: float = 0.0
    opening: bool = False


@dataclass
class StoryQuantity:
    story: str
    columns: float = 0.0        # m3
    beams: float = 0.0
    walls: float = 0.0
    slabs: float = 0.0
    area: float = 0.0           # m2 of floor; 0 for a level with no floor shell

    @property
    def volume(self) -> float:
        return self.columns + self.beams + self.walls + self.slabs

    @property
    def ratio(self) -> float:
        return self.volume / self.area if self.area > 0 else math.nan


@dataclass
class Quantities:
    model_path: str = ""
    stories: list[StoryQuantity] = field(default_factory=list)      # from the top down
    footings: float = 0.0             # m3
    tie_beams: float = 0.0
    foundation_included: bool = False
    grade_thickness: float = 0.0      # mm; 0 when no slab on grade is counted
    grade_area: float = 0.0           # m2: the footprint
    notes: list[str] = field(default_factory=list)

    @property
    def grade_volume(self) -> float:
        return self.grade_area * self.grade_thickness / 1000.0

    @property
    def superstructure(self) -> float:
        return sum(s.volume for s in self.stories)

    @property
    def volume(self) -> float:
        return self.superstructure + self.footings + self.tie_beams + self.grade_volume

    @property
    def floor_area(self) -> float:
        return sum(s.area for s in self.stories) + (
            self.grade_area if self.grade_thickness > 0 else 0.0)

    @property
    def ratio(self) -> float:
        return self.volume / self.floor_area if self.floor_area > 0 else math.nan

    @property
    def elevated_area(self) -> float:
        return sum(s.area for s in self.stories)


# =============================================================================
# MEASURING (plain data)
# =============================================================================
def _polygon(points):
    from shapely.geometry import Polygon

    if len(points) < 3:
        return None
    polygon = Polygon(points)
    polygon = polygon if polygon.is_valid else polygon.buffer(0)
    return polygon if polygon.area > 0 else None


def bar_volume(bar: Bar, depth: float | None = None) -> float:
    """The volume of a member solid in m3; ``depth`` replaces its depth (a beam
    measured below the slab)."""
    if bar.circular:
        return math.pi * bar.width**2 / 4.0 * bar.length / MM3
    return bar.width * max(bar.depth if depth is None else depth, 0.0) * bar.length / MM3


def column_outline(bar: Bar):
    """The section of a column in plan, at its top, as a polygon."""
    from shapely.geometry import Point, Polygon

    x, y = (bar.end if bar.end[2] >= bar.start[2] else bar.start)[:2]
    if bar.circular:
        return Point(x, y).buffer(bar.width / 2.0, 32)
    size = math.hypot(bar.up[0], bar.up[1]) or 1.0
    ux, uy = bar.up[0] / size, bar.up[1] / size          # along the depth
    vx, vy = -uy, ux                                     # along the width
    return Polygon([(x + a * bar.depth / 2.0 * ux + b * bar.width / 2.0 * vx,
                     y + a * bar.depth / 2.0 * uy + b * bar.width / 2.0 * vy)
                    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))])


def measure_story(story: str, bars: list[Bar], shells: list[FloorShell]) -> StoryQuantity:
    """The quantities of one story from its member solids (``model_3d.Bar``, the
    beams already cut at the faces of their supports) and its floor shells."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    out = StoryQuantity(story)
    slabs = [(s, _polygon(s.points)) for s in shells if not s.opening and s.thickness > 0]
    slabs = [(s, p) for s, p in slabs if p is not None]
    holes = [p for p in (_polygon(s.points) for s in shells if s.opening) if p is not None]
    open_area = unary_union(holes) if holes else None
    columns = [b for b in bars if b.kind == COLUMN]
    sections = [column_outline(b) for b in columns]

    for shell, polygon in slabs:
        net = polygon.difference(open_area) if open_area is not None else polygon
        # the part of each column section that is inside this slab
        through = sum(net.intersection(section).area for section in sections
                      if net.intersects(section))
        out.slabs += max(net.area - through, 0.0) * shell.thickness / MM3
    if slabs:
        floor = unary_union([p for _, p in slabs])
        if open_area is not None:
            floor = floor.difference(open_area)
        out.area = floor.area / MM2

    for bar in bars:
        if bar.kind == COLUMN:
            out.columns += bar_volume(bar)
        elif bar.kind == WALL:
            out.walls += bar.width * bar.depth * bar.length / MM3
        elif bar.kind == BEAM:
            middle = Point((bar.start[0] + bar.end[0]) / 2.0, (bar.start[1] + bar.end[1]) / 2.0)
            over = [s.thickness for s, p in slabs
                    if p.buffer(bar.width / 2.0 + UNDER_SLAB).contains(middle)
                    and (open_area is None or not open_area.contains(middle))]
            out.beams += bar_volume(bar, bar.depth - (max(over) if over else 0.0))
    return out


def footprint(shells: list[FloorShell], columns: list[Bar]) -> float:
    """The area in m2 of the building seen in plan: the outline of all its floor
    shells, with no opening taken out. With no floor shell, the outline around
    its columns."""
    from shapely.geometry import MultiPoint, Polygon
    from shapely.ops import unary_union

    polygons = [p for p in (_polygon(s.points) for s in shells if not s.opening) if p is not None]
    if polygons:
        seen = unary_union([p.buffer(1.0) for p in polygons]).buffer(-1.0)
        parts = getattr(seen, "geoms", [seen])
        return sum(Polygon(part.exterior).area for part in parts) / MM2
    points = [(b.start[0], b.start[1]) for b in columns]
    return MultiPoint(points).convex_hull.area / MM2 if len(points) >= 3 else 0.0


def foundation_volumes(plan: dict) -> tuple[float, float]:
    """(footings, tie beams) in m3 from a saved foundation plan. A tie beam is
    measured between the faces of the columns, at its full depth."""
    from etabs_api.workflows.model_3d import foundation_parts

    bars, plates = foundation_parts(plan)
    footings = 0.0
    for plate in plates:
        polygon = _polygon(plate.outline)
        if polygon is not None:
            footings += polygon.area * plate.thickness / MM3
    return footings, sum(bar_volume(b) for b in bars if b.kind == TIE_BEAM)


def measure(bars: list[Bar], shells: list[FloorShell], order: list[str],
            grade_thickness: float = 0.0, plan: dict | None = None,
            model_path: str = "") -> Quantities:
    """The quantities of the building. ``order``: its stories from the top down."""
    out = Quantities(model_path, grade_thickness=max(grade_thickness, 0.0))
    for story in order:
        own_bars = [b for b in bars if b.group == story]
        own_shells = [s for s in shells if s.story == story]
        if not own_bars and not own_shells:
            continue
        out.stories.append(measure_story(story, own_bars, own_shells))
    out.grade_area = footprint(shells, [b for b in bars if b.kind == COLUMN])
    if plan is not None:
        out.footings, out.tie_beams = foundation_volumes(plan)
        out.foundation_included = True
    bare = [s.story for s in out.stories if s.area <= 0]
    if bare:
        out.notes.append("No floor shell at " + ", ".join(bare) + ": its columns, beams and "
                         "walls are counted, and it adds no floor area. A level on the ground "
                         "has the slab on grade as its floor; model the slab of any other.")
    if not any(s.thickness > 0 and not s.opening for s in shells):
        out.notes.append("The model has no floor slab: the footprint is the outline around the "
                         "columns.")
    return out


# =============================================================================
# REPORT
# =============================================================================
def _m3(value: float) -> str:
    return f"{value:,.2f}"


def quantities_report(q: Quantities):
    from utilities.terminal import Report

    report = Report("CONCRETE QUANTITIES", [f"Model: {q.model_path}"])
    report.heading("By story")
    rows = [[s.story, _m3(s.columns), _m3(s.beams), _m3(s.walls), _m3(s.slabs), _m3(s.volume),
             _m3(s.area), f"{s.ratio:.3f}" if s.ratio == s.ratio else "-"] for s in q.stories]
    rows.append(["Total", _m3(sum(s.columns for s in q.stories)),
                 _m3(sum(s.beams for s in q.stories)), _m3(sum(s.walls for s in q.stories)),
                 _m3(sum(s.slabs for s in q.stories)), _m3(q.superstructure),
                 _m3(q.elevated_area), f"{q.superstructure / q.elevated_area:.3f}"
                 if q.elevated_area > 0 else "-"])
    report.table(["Story", "Columns (m3)", "Beams (m3)", "Walls (m3)", "Slabs (m3)",
                  "Concrete (m3)", "Floor area (m2)", "m3/m2"], rows)
    report.heading("Building")
    parts = [["Superstructure (the stories above)", _m3(q.superstructure), _m3(q.elevated_area)]]
    if q.grade_thickness > 0:
        parts.append([f"{GROUND}, {q.grade_thickness:g} mm on the footprint", _m3(q.grade_volume),
                      _m3(q.grade_area)])
    else:
        parts.append([GROUND, "not counted", "-"])
    if q.foundation_included:
        parts.append(["Footings", _m3(q.footings), "-"])
        parts.append(["Tie beams", _m3(q.tie_beams), "-"])
    else:
        parts.append([FOUNDATION, "not included", "-"])
    parts.append(["Total", _m3(q.volume), _m3(q.floor_area)])
    report.table(["Part", "Concrete (m3)", "Floor area (m2)"], parts)
    if q.floor_area > 0:
        report.equations([f"V / A = {q.volume:,.2f} m³ / {q.floor_area:,.2f} m² = "
                          f"{q.ratio:.3f} m³/m²"], title="Concrete ratio")
    report.lines([
        "Beams are measured below the slab and between the faces of their supports; slabs "
        "less their openings and the columns through them; columns floor to floor.",
        *q.notes])
    return report


def save_workbook(q: Quantities, path: str) -> str:
    import pandas as pd

    stories = pd.DataFrame([{
        "Story": s.story, "Columns (m3)": s.columns, "Beams (m3)": s.beams, "Walls (m3)": s.walls,
        "Slabs (m3)": s.slabs, "Concrete (m3)": s.volume, "Floor area (m2)": s.area,
        "m3/m2": s.ratio} for s in q.stories])
    building = pd.DataFrame([
        ("Superstructure", q.superstructure, q.elevated_area),
        (f"{GROUND} ({q.grade_thickness:g} mm)", q.grade_volume,
         q.grade_area if q.grade_thickness > 0 else 0.0),
        ("Footings", q.footings, 0.0), ("Tie beams", q.tie_beams, 0.0),
        ("Total", q.volume, q.floor_area),
        ("Concrete ratio (m3/m2)", q.ratio, None)],
        columns=["Part", "Concrete (m3)", "Floor area (m2)"])
    with pd.ExcelWriter(path) as writer:
        stories.round(3).to_excel(writer, sheet_name="By story", index=False)
        building.round(3).to_excel(writer, sheet_name="Building", index=False)
    return path


def quantities_summary(q: Quantities, files: dict[str, str] | None = None):
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt quantities", q.model_path)
    summary.add("Concrete of the stories", f"{q.superstructure:,.2f} m3")
    summary.add(GROUND, f"{q.grade_volume:,.2f} m3 ({q.grade_thickness:g} mm on "
                f"{q.grade_area:,.2f} m2)" if q.grade_thickness > 0 else "not counted")
    summary.add(FOUNDATION, f"{q.footings + q.tie_beams:,.2f} m3 (footings {q.footings:,.2f}, "
                f"tie beams {q.tie_beams:,.2f})" if q.foundation_included else "not included")
    summary.add("Total concrete", f"{q.volume:,.2f} m3")
    summary.add("Total floor area", f"{q.floor_area:,.2f} m2")
    summary.add("Concrete ratio", f"{q.ratio:.3f} m3/m2" if q.ratio == q.ratio else "-")
    for note in q.notes:
        summary.note(note)
    summary.note("The model was not changed.")
    for label, path in (files or {}).items():
        summary.file(label, path)
    return summary


# =============================================================================
# ETABS
# =============================================================================
def read_quantities(connector, model_path: str, grade_thickness: float, plan: dict | None,
                    progress=None) -> Quantities:
    """Measure the model open in ETABS (in N-mm)."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows.foundation_plan import read_shells
    from etabs_api.workflows.framing_plans import _number
    from etabs_api.workflows.grid_column_model import _table
    from etabs_api.workflows.model_3d import nearest_story, read_model

    say = progress or (lambda text: None)
    model = connector.sap_model
    say("Reading the columns, beams and walls")
    found = read_model(model, slabs=False)
    say("Reading the floors and their openings")
    points = {str(r["UniqueName"]): (_number(r["X"]), _number(r["Y"]), _number(r["Z"]))
              for r in _table(model, "Point Object Connectivity")}
    got = model.Story.GetStories()
    stories = list(zip([str(n) for n in as_list(got[1])], [float(z) for z in as_list(got[2])]))
    shells = [FloorShell(s.name, nearest_story(s.z, stories), list(s.points), s.thickness,
                         s.opening) for s in read_shells(model, points, openings=True)]
    decks = sum(1 for s in shells if not s.opening and s.thickness <= 0)
    order = [name for name, _ in stories][::-1]
    out = measure(found.bars, shells, order, grade_thickness, plan, model_path)
    if decks:
        out.notes.append(f"{decks} floors are not concrete slabs (a deck, or a property with no "
                         "thickness): they are not in the volume or the floor area.")
    for what, number in found.left_out.items():
        if what in ("columns", "beams", "braces"):
            out.notes.append(f"{number} {what} are not concrete rectangles or circles: not "
                             "counted.")
        elif what == "walls":
            out.notes.append(f"{number} walls are not plain vertical panels: not counted.")
    return out


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
GRADE_LABEL = "Slab on grade thickness (mm), 0 for none"


def run_quantities() -> Quantities | None:
    """Entry point: ask the slab on grade and the foundation, measure the open
    model, print the report and save it beside the model."""
    from design.concrete.workflow import attach_etabs
    from etabs_api.core.connection import model_file
    from etabs_api.workflows.framing_plans import saved_foundation_plan
    from utilities import terminal
    from utilities._gui_helpers import LoadingWindow, enter_values, select_option, show_warning

    title = "Concrete Quantities"
    connector = attach_etabs()
    if connector is None:
        return None
    path = model_file(connector.sap_model)
    if not path or not os.path.isfile(path):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    typed = enter_values(
        title, "Slab on grade\n\nThe slab on grade is not in the ETABS model as a rule. Its area "
        "is taken as the footprint of the building (the outline of all its floors in plan), and "
        "counts as the ground floor in the floor area. Type its thickness, or 0 to leave it out.",
        [GRADE_LABEL], {GRADE_LABEL: "100"})
    if typed is None:
        return None
    try:
        thickness = float(typed[GRADE_LABEL])
        if thickness < 0:
            raise ValueError
    except ValueError:
        show_warning("The thickness must be a number, 0 or more.", title=title)
        return None
    plan = saved_foundation_plan(path)
    if plan is not None:
        include = select_option(
            title, "Include the foundation?\n\nA plan of the foundation model of this model was "
            f"saved on {plan['saved']}: {len(plan['footings'])} footings, "
            f"{len(plan['tie_beams'])} tie beams. Their concrete is added to the volume.",
            ["Yes", "No"])
        if include is None:
            return None
        if include == "No":
            plan = None
    with LoadingWindow("Concrete quantities") as window:
        quantities = read_quantities(connector, path, thickness, plan, window.update)
    report = quantities_report(quantities)
    if terminal.drawn():
        report.show()
    else:
        print(report.plain())
    from utilities.output_folders import beside

    files = {}
    try:
        text_path = beside(path, " - Concrete Quantities.txt")
        with open(text_path, "w", encoding="utf-8") as handle:
            handle.write(report.plain().lstrip("\n") + "\n")
        files["Report"] = text_path
        files["Workbook"] = save_workbook(quantities, beside(path, " - Concrete Quantities.xlsx"))
    except PermissionError:
        show_warning("The quantities workbook is open in another program: close it and run sdt "
                     "quantities again to save it.", title=title)
    quantities_summary(quantities, files).show(popup=True, echo=False)
    return quantities


if __name__ == "__main__":
    run_quantities()
