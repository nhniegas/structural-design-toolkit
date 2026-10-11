"""The foundation as it is modelled, saved for the framing plans and the 3D model.

ETABS has one model open at a time, and ``sdt plans`` runs on the
superstructure. So the foundation model is read once, when it is open
(``sdt foundation``, "Save the foundation plan"), and what the drawings need
is kept beside it in ``<foundation>.plan.json``:

* **footings**: each slab shell of the foundation model, or each group of
  shells that touch, is one footing, with its outline, thickness and the
  columns that stand on it;
* **marks**: a footing under one column is an isolated footing; those of the
  same size share a mark (``F1``, ``F2``, ...). A footing under two columns
  or more is a combined footing and has a mark of its own (``CF1``, ...);
* **tie beams**: the beams of the foundation model, with their width, depth
  and name (``FTB-1``, ... when ETABS left them a number);
* **columns**: the support joints with the column on each, from the file the
  foundation model was made with.

The functions that only work on plain data need no ETABS.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field

PLAN_SUFFIX = ".plan.json"
SIZE_STEP = 10.0          # mm: footings whose sides agree to this are the same size
INSIDE = 5.0              # mm: a column this close to a footing edge is on the footing


@dataclass
class Shell:
    """A horizontal shell of the foundation model."""

    name: str
    points: list[tuple[float, float]]
    z: float
    thickness: float = 0.0
    section: str = ""
    opening: bool = False        # an opening in the floor, not concrete


@dataclass
class Footing:
    mark: str
    kind: str                                   # "isolated" or "combined"
    outline: list[tuple[float, float]]
    z: float                                    # elevation of its shells (their top)
    thickness: float
    length: float                               # the longer side of its outline, mm
    width: float
    columns: list[str] = field(default_factory=list)     # the marks of the columns on it
    shells: list[str] = field(default_factory=list)


@dataclass
class TieBeam:
    mark: str
    x1: float
    y1: float
    x2: float
    y2: float
    z: float
    width: float
    depth: float
    joint_i: str = ""
    joint_j: str = ""


@dataclass
class PlanColumn:
    mark: str
    name: str
    joint: str
    x: float
    y: float
    z: float
    width: float
    depth: float
    angle: float = 0.0
    circular: bool = False


def plan_path(foundation_path: str) -> str:
    return os.path.splitext(foundation_path)[0] + PLAN_SUFFIX


def column_mark(name: str) -> str:
    """``GF-C1`` gives ``C1``: the mark of a column without its level."""
    import re

    match = re.match(r"^[^-\s]+-(.+)$", str(name).strip())
    return match.group(1) if match else str(name).strip()


def _sides(outline: list[tuple[float, float]]) -> tuple[float, float]:
    """(longer, shorter) side of the smallest rectangle around an outline, at any angle."""
    from shapely.geometry import Polygon

    box = Polygon(outline).minimum_rotated_rectangle
    corners = list(box.exterior.coords)
    a = math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
    b = math.hypot(corners[2][0] - corners[1][0], corners[2][1] - corners[1][1])
    return max(a, b), min(a, b)


def group_footings(shells: list[Shell], columns: list[PlanColumn]) -> list[Footing]:
    """The footings of the shells: shells that touch are one footing.

    Marks, in reading order (from the top of the plan down, then left to
    right): isolated footings share a mark with those of the same length,
    width and thickness; each combined footing has its own.
    """
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    polygons = []
    for shell in shells:
        if len(shell.points) < 3:
            continue
        polygon = Polygon(shell.points)
        polygon = polygon if polygon.is_valid else polygon.buffer(0)
        if polygon.area > 0:
            polygons.append((shell, polygon))
    if not polygons:
        return []
    # shells that only meet at an edge are joined by growing them a hair first
    merged = unary_union([polygon.buffer(1.0) for _, polygon in polygons])
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    found = []
    for part in parts:
        own = [shell for shell, polygon in polygons if part.intersects(polygon.centroid)
               or part.contains(polygon.representative_point())]
        if not own:
            continue
        exact = unary_union([polygon for shell, polygon in polygons if shell in own])
        exact = exact if exact.geom_type == "Polygon" else part.buffer(-1.0)
        if exact.geom_type != "Polygon" or exact.is_empty:
            exact = part
        outline = [(round(x, 3), round(y, 3)) for x, y in list(exact.exterior.coords)[:-1]]
        on_it = [c for c in columns if part.buffer(INSIDE).contains(Point(c.x, c.y))]
        length, width = _sides(outline)
        found.append({
            "outline": outline, "z": own[0].z, "thickness": max(s.thickness for s in own),
            "length": length, "width": width, "columns": [c.mark for c in on_it],
            "shells": [s.name for s in own],
            "centre": (exact.centroid.x, exact.centroid.y)})
    found.sort(key=lambda f: (-round(f["centre"][1] / 500.0), f["centre"][0]))
    marks: dict[tuple, str] = {}
    combined = 0
    out = []
    for item in found:
        kind = "combined" if len(item["columns"]) > 1 else "isolated"
        if kind == "combined":
            combined += 1
            mark = f"CF{combined}"
        else:
            size = (round(item["length"] / SIZE_STEP), round(item["width"] / SIZE_STEP),
                    round(item["thickness"] / SIZE_STEP))
            mark = marks.setdefault(size, f"F{len(marks) + 1}")
        out.append(Footing(mark, kind, item["outline"], item["z"], item["thickness"],
                           item["length"], item["width"], item["columns"], item["shells"]))
    return out


def name_tie_beams(beams: list[TieBeam]) -> list[TieBeam]:
    """Give a tie beam that still has its ETABS number the mark ``FTB-n``, counted
    from the top of the plan down, then left to right. A named beam keeps its name."""
    unnamed = [b for b in beams if str(b.mark).strip().isnumeric() or not str(b.mark).strip()]
    unnamed.sort(key=lambda b: (-round(((b.y1 + b.y2) / 2.0) / 500.0), (b.x1 + b.x2) / 2.0))
    used = {b.mark for b in beams if b not in unnamed}
    number = 0
    for beam in unnamed:
        number += 1
        while f"FTB-{number}" in used:
            number += 1
        beam.mark = f"FTB-{number}"
    return beams


def schedule_lines(footings: list[Footing]) -> list[str]:
    """One line for each footing mark: its size, and how many there are."""
    seen: dict[str, list[Footing]] = {}
    for footing in footings:
        seen.setdefault(footing.mark, []).append(footing)
    lines = []
    for mark, same in seen.items():
        first = same[0]
        size = f"{first.length:,.0f} x {first.width:,.0f} x {first.thickness:,.0f} thk"
        if first.kind == "combined":
            lines.append(f"{mark}: {size}, combined footing of {', '.join(first.columns)}")
        else:
            lines.append(f"{mark}: {size}" + (f" ({len(same)} footings)" if len(same) > 1 else ""))
    return lines


# =============================================================================
# FILE
# =============================================================================
def save_plan(foundation_path: str, footings: list[Footing], tie_beams: list[TieBeam],
              columns: list[PlanColumn]) -> str:
    path = plan_path(foundation_path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"made_by": "sdt foundation", "foundation": foundation_path,
                   "saved": time.strftime("%Y-%m-%d %H:%M"),
                   "footings": [asdict(f) for f in footings],
                   "tie_beams": [asdict(b) for b in tie_beams],
                   "columns": [asdict(c) for c in columns]}, handle, indent=1)
    return path


def load_plan(foundation_path: str) -> dict | None:
    """The saved plan of a foundation model, as its objects; None when there is none."""
    try:
        with open(plan_path(foundation_path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None

    def made(kind, items):
        fields = kind.__dataclass_fields__
        out = []
        for item in items or []:
            values = {k: v for k, v in item.items() if k in fields}
            if "outline" in values:
                values["outline"] = [tuple(p) for p in values["outline"]]
            out.append(kind(**values))
        return out

    return {"saved": data.get("saved", ""), "foundation": data.get("foundation", foundation_path),
            "footings": made(Footing, data.get("footings")),
            "tie_beams": made(TieBeam, data.get("tie_beams")),
            "columns": made(PlanColumn, data.get("columns"))}


# =============================================================================
# ETABS
# =============================================================================
def columns_of_manifest(manifest: dict) -> list[PlanColumn]:
    """The columns on the support joints, from the file of the foundation model."""
    out = []
    for item in manifest.get("supports", []):
        if item.get("kind") != "column" or not item.get("width") or not item.get("depth"):
            continue
        out.append(PlanColumn(column_mark(item.get("member", "")), str(item.get("member", "")),
                              str(item.get("foundation_joint", item.get("joint", ""))),
                              float(item["x"]), float(item["y"]), float(item["z"]),
                              float(item["width"]), float(item["depth"]),
                              float(item.get("angle", 0.0)), bool(item.get("circular"))))
    return out


def read_shells(model, xyz: dict[str, tuple[float, float, float]],
                openings: bool = False) -> list[Shell]:
    """The horizontal shells of the model open in ETABS, with the thickness of
    their slab property (0 for a deck or another property). Walls and ramps are
    left out, and so are openings unless ``openings`` asks for them (they then
    come with ``opening`` set). ``xyz``: the coordinates of the joints by name."""
    from etabs_api.core.helpers import as_list, return_code

    thickness: dict[str, float] = {}
    shells = []
    for name in [str(n) for n in as_list(model.AreaObj.GetNameList(0, [])[1])]:
        got = model.AreaObj.GetPoints(name, 0, [])
        if return_code(got) != 0:
            continue
        corners = [xyz[str(p)] for p in as_list(got[1]) if str(p) in xyz]
        if len(corners) < 3 or max(c[2] for c in corners) - min(c[2] for c in corners) > 1.0:
            continue                      # not a horizontal shell: a wall, a ramp
        try:
            if model.AreaObj.GetOpening(name, False)[0]:
                if openings:
                    shells.append(Shell(name, [(c[0], c[1]) for c in corners], corners[0][2],
                                        opening=True))
                continue
        except Exception:  # noqa: BLE001 - no opening flag: a plain shell
            pass
        prop = model.AreaObj.GetProperty(name)
        section = str(prop[0]) if return_code(prop) == 0 else ""
        if section and section not in thickness:
            try:
                slab = model.PropArea.GetSlab(section)
                thickness[section] = float(slab[3]) if return_code(slab) == 0 else 0.0
            except Exception:  # noqa: BLE001 - a deck or a wall property
                thickness[section] = 0.0
        shells.append(Shell(name, [(c[0], c[1]) for c in corners], corners[0][2],
                            thickness.get(section, 0.0), section))
    return shells


def read_foundation(connector, manifest: dict) -> tuple[list[Footing], list[TieBeam],
                                                         list[PlanColumn]]:
    """The footings, tie beams and columns of the foundation model open in ETABS."""
    from etabs_api.workflows.foundation_model import _number, _table

    model = connector.sap_model
    points = _table(connector, "Point Object Connectivity")
    xyz = {str(n): (_number(x), _number(y), _number(z)) for n, x, y, z in zip(
        points["UniqueName"], points["X"], points["Y"], points["Z"])} if len(points) else {}
    columns = columns_of_manifest(manifest)
    # the joints may have moved in the foundation model: the columns follow them
    for column in columns:
        if column.joint in xyz:
            column.x, column.y, column.z = xyz[column.joint]
    shells = read_shells(model, xyz)

    beams = _table(connector, "Beam Object Connectivity")
    assigned = _table(connector, "Frame Assignments - Section Properties")
    section_of = dict(zip(assigned.get("UniqueName", []), assigned.get("SectProp", []))) \
        if len(assigned) else {}
    rectangles = _table(connector, "Frame Section Property Definitions - Concrete Rectangular")
    size_of = {str(n): (_number(t2), _number(t3)) for n, t2, t3 in zip(
        rectangles["Name"], rectangles["t2"], rectangles["t3"])} if len(rectangles) else {}
    tie_beams = []
    if len(beams):
        for name, i, j in zip(beams["UniqueName"].astype(str), beams["UniquePtI"].astype(str),
                              beams["UniquePtJ"].astype(str)):
            if i not in xyz or j not in xyz:
                continue
            width, depth = size_of.get(str(section_of.get(name, "")), (0.0, 0.0))
            tie_beams.append(TieBeam(name, xyz[i][0], xyz[i][1], xyz[j][0], xyz[j][1],
                                     (xyz[i][2] + xyz[j][2]) / 2.0, width, depth, i, j))
    return group_footings(shells, columns), name_tie_beams(tie_beams), columns
