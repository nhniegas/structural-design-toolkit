"""The wall schedule (DXF): one section detail per wall and story, drawn in plan at
one scale, with the vertical bars and the special boundary element zones, and
rows for the thickness, the bars and the boundary elements.

A wall whose legs meet (an L, a U, a core) is one section detail. The schedule
is drawn in millimetres on paper; the sections are at 1 : ``scale``.
"""

from __future__ import annotations

import math

import ezdxf
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from design.concrete.walls import LegShape, WallSection

TEXT = 3.0
ROW = 7.0
HEADER = 9.0
LEFT = 30.0          # the story column
FIELDS = ("THICKNESS", "VERTICAL BARS", "HORIZONTAL BARS", "BOUNDARY ELEMENTS")


def leg_polygon(leg: LegShape) -> Polygon:
    u = leg.unit
    n = (-u[1] * leg.thickness / 2, u[0] * leg.thickness / 2)
    return Polygon([(leg.x1 + n[0], leg.y1 + n[1]), (leg.x2 + n[0], leg.y2 + n[1]),
                    (leg.x2 - n[0], leg.y2 - n[1]), (leg.x1 - n[0], leg.y1 - n[1])])


def boundary_zone(outline, section: WallSection, axis: float, end: str, length: float):
    """The part of the section within ``length`` of its compression face for the
    boundary element of ``axis`` (degrees) and ``end`` (+ or -)."""
    t = math.radians(axis)
    sign = 1.0 if end == "+" else -1.0
    across = (-math.sin(t) * sign, math.cos(t) * sign)
    coords = outline.exterior.coords if hasattr(outline, "exterior") else \
        [c for geom in outline.geoms for c in geom.exterior.coords]
    reach = max(across[0] * (x - section.xc) + across[1] * (y - section.yc) for x, y in coords)
    big = 1e6
    # a half-plane beyond the line at reach - length, across the axis
    cx, cy = section.xc + across[0] * (reach - length), section.yc + across[1] * (reach - length)
    ux, uy = across[1], -across[0]
    half = Polygon([(cx - ux * big, cy - uy * big), (cx + ux * big, cy + uy * big),
                    (cx + ux * big + across[0] * big, cy + uy * big + across[1] * big),
                    (cx - ux * big + across[0] * big, cy - uy * big + across[1] * big)])
    return outline.intersection(half)


def write_wall_schedule(path: str, walls: list[dict], scale: float = 50.0) -> str:
    """``walls``: one dict per wall with ``name`` and ``stories``, a list (top story
    first) of dicts with ``story``, ``legs`` (LegShape list), ``section`` (the designed
    WallSection), ``boundary`` (the boundary element dicts of design_story), and the
    texts ``thickness``, ``vertical``, ``horizontal``, ``elements``."""
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    for layer, colour in (("TABLE", 7), ("CONCRETE", 7), ("BARS", 1), ("BOUNDARY", 3)):
        doc.layers.new(layer, dxfattribs={"color": colour})
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})
    msp = doc.modelspace()
    f = 1.0 / scale

    def text(value, x, y, height=TEXT, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER):
        msp.add_text(str(value), dxfattribs={"height": height, "style": "SIMPLEX",
                                             "layer": "TABLE"}).set_placement((x, y), align=align)

    def cell(x1, y1, x2, y2):
        msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True,
                           dxfattribs={"layer": "TABLE"})

    # every wall's cell is as wide as its largest section at the scale
    widths, heights = [], []
    for wall in walls:
        w = h = 0.0
        for story in wall["stories"]:
            outline = unary_union([leg_polygon(leg) for leg in story["legs"]])
            minx, miny, maxx, maxy = outline.bounds
            w, h = max(w, (maxx - minx) * f), max(h, (maxy - miny) * f)
        widths.append(max(70.0, w + 20.0))
        heights.append(h + 16.0)
    detail = max(heights + [50.0])
    stories = []
    for wall in walls:
        for story in wall["stories"]:
            if story["story"] not in stories:
                stories.append(story["story"])
    xs = [LEFT + 45.0]
    for width in widths:
        xs.append(xs[-1] + width)
    text(f"WALL SECTIONS DRAWN TO SCALE 1:{scale:g} (THE SCHEDULE IN mm ON PAPER)", 0.0,
         HEADER * 2, TEXT * 1.5, ezdxf.enums.TextEntityAlignment.LEFT)
    y = 0.0
    cell(0, y, LEFT, y - HEADER)
    text("STORY", LEFT / 2, y - HEADER / 2)
    cell(LEFT, y, xs[0], y - HEADER)
    for i, wall in enumerate(walls):
        cell(xs[i], y, xs[i + 1], y - HEADER)
        text(wall["name"], (xs[i] + xs[i + 1]) / 2, y - HEADER / 2)
    y -= HEADER
    block = detail + ROW * len(FIELDS)
    for name in stories:
        cell(0, y, LEFT, y - block)
        text(name, LEFT / 2, y - block / 2, TEXT * 1.3)
        cell(LEFT, y, xs[0], y - detail)
        for k, field in enumerate(FIELDS):
            top = y - detail - k * ROW
            cell(LEFT, top, xs[0], top - ROW)
            text(field, (LEFT + xs[0]) / 2, top - ROW / 2, TEXT * 0.8)
        for i, wall in enumerate(walls):
            x1, x2 = xs[i], xs[i + 1]
            cell(x1, y, x2, y - detail)
            story = next((s for s in wall["stories"] if s["story"] == name), None)
            if story is None:
                text("-", (x1 + x2) / 2, y - detail / 2)
            else:
                _draw_section(msp, story, (x1 + x2) / 2, y - detail / 2, f)
            for k, field in enumerate(("thickness", "vertical", "horizontal", "elements")):
                top = y - detail - k * ROW
                cell(x1, top, x2, top - ROW)
                value = "-" if story is None else story[field]
                text(value, (x1 + x2) / 2, top - ROW / 2,
                     min(TEXT, (x2 - x1) / (0.75 * max(len(str(value)), 1))))
        y -= block
    doc.saveas(path)
    return path


def _draw_section(msp, story: dict, cx: float, cy: float, f: float) -> None:
    legs = story["legs"]
    section: WallSection = story["section"]
    outline = unary_union([leg_polygon(leg) for leg in legs])
    minx, miny, maxx, maxy = outline.bounds
    ox, oy = (minx + maxx) / 2, (miny + maxy) / 2

    def at(x, y):
        return cx + (x - ox) * f, cy + (y - oy) * f

    for geom in getattr(outline, "geoms", [outline]):
        msp.add_lwpolyline([at(x, y) for x, y in geom.exterior.coords], close=True,
                           dxfattribs={"layer": "CONCRETE", "lineweight": 35})
    radius = max(section.bar * f / 2.0, 0.25)
    for x, y in zip(section.bx, section.by):
        msp.add_circle(at(x, y), radius, dxfattribs={"layer": "BARS"})
    for element in story.get("boundary", []):
        if not element.get("needed"):
            continue
        zone = boundary_zone(outline, section, element["axis"], element["end"],
                             element["length"])
        for geom in getattr(zone, "geoms", [zone]):
            if geom.is_empty or not hasattr(geom, "exterior"):
                continue
            msp.add_lwpolyline([at(x, y) for x, y in geom.exterior.coords], close=True,
                               dxfattribs={"layer": "BOUNDARY", "linetype": "DASHED"
                                           if "DASHED" in msp.doc.linetypes else "CONTINUOUS"})


__all__ = ["write_wall_schedule", "boundary_zone", "leg_polygon", "box"]
