"""Slab panels from the floor objects of a model: geometry only, no ETABS here.

A panel is bounded by beam or wall lines on its sides (NSCP 408.4.1.7). The floor area
objects of a level are joined into panels across every shared edge that no beam or wall
runs along (an object split by the mesher, or drawn in pieces, is one panel) when they
have the same thickness. Each panel is then taken as its bounding rectangle (a panel that
fills less than 90 percent of it is flagged) with four sides, W (least x), E, S (least y)
and N, and for each side:

* the share of its length on a beam or wall (``supported``) and the widest of them;
* the share with another panel beyond it at the same level (``continuous``).

A panel is described in its own axes: x along the sides of its least rotated rectangle
nearest to the global X (``angle``, radians from global X), so that the bays of a skewed
grid are rectangles too. ``box`` is in those axes; W, E, S and N are its sides in them.

A side at least half on a support is supported; a side at least half continuous is
continuous. Coordinates in mm.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

TOLERANCE = 100.0        # mm, an edge lies on a beam line within this distance
LEVEL_TOLERANCE = 1.0    # mm
SHARE = 0.5              # a side at least half on a support / a neighbour
RECTANGLE = 0.90     # a panel filling less of its rectangle is flagged
SIDES = ("W", "E", "S", "N")
OPPOSITE = {"W": "E", "E": "W", "S": "N", "N": "S"}

Point = tuple[float, float]


@dataclass
class SlabArea:
    """A floor area object: outline (mm) at level z, thickness h (mm), f'c (MPa), the
    superimposed dead and live loads on it (kPa, service)."""

    name: str
    story: str
    z: float
    outline: list[Point]
    h: float
    section: str = ""
    fc: float = 28.0
    dead: float = 0.0
    live: float = 0.0
    load_note: str = ""


@dataclass
class Support:
    """A beam or wall line at a level: its ends (mm), web width bw and total depth (mm)."""

    name: str
    story: str
    z: float
    a: Point
    b: Point
    width: float
    depth: float
    kind: str = "beam"        # "beam" or "wall"
    fc: float = 28.0


@dataclass
class Side:
    supported: float = 0.0          # share of the side on beams / walls
    continuous: float = 0.0         # share with a panel beyond it
    support: Support | None = None  # the support covering most of the side
    neighbour: int | None = None    # index of the panel beyond it (largest overlap)

    @property
    def is_supported(self) -> bool:
        return self.supported >= SHARE

    @property
    def is_continuous(self) -> bool:
        return self.continuous >= SHARE


@dataclass
class Panel:
    index: int
    story: str
    z: float
    areas: list[str]
    box: tuple[float, float, float, float]       # x0, y0, x1, y1 in the panel's axes
    area: float                                  # mm2
    h: float
    fc: float
    section: str
    dead: float
    live: float
    sides: dict[str, Side] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    load_note: str = ""
    angle: float = 0.0                           # radians, panel x from global X

    def to_global(self, x: float, y: float) -> Point:
        c, s = math.cos(self.angle), math.sin(self.angle)
        return x * c - y * s, x * s + y * c

    @property
    def centre(self) -> Point:
        """The centre of the panel's rectangle, in global coordinates (mm)."""
        return self.to_global((self.box[0] + self.box[2]) / 2.0, (self.box[1] + self.box[3]) / 2.0)

    @property
    def corners(self) -> list[Point]:
        x0, y0, x1, y1 = self.box
        return [self.to_global(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]

    @property
    def lx(self) -> float:
        """Centre-to-centre span in x (mm)."""
        return self.box[2] - self.box[0]

    @property
    def ly(self) -> float:
        return self.box[3] - self.box[1]

    def half_width(self, side: str) -> float:
        s = self.sides[side]
        return s.support.width / 2.0 if s.is_supported and s.support else 0.0

    @property
    def clear_x(self) -> float:
        """Clear span in x (mm): between the faces of the W and E supports."""
        return self.lx - self.half_width("W") - self.half_width("E")

    @property
    def clear_y(self) -> float:
        return self.ly - self.half_width("S") - self.half_width("N")

    @property
    def rectangular(self) -> float:
        return self.area / (self.lx * self.ly) if self.lx * self.ly else 0.0


def polygon_area(points: list[Point]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


def edges(points: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:] + points[:1]))


def overlap(p: Point, q: Point, r: Point, s: Point, tolerance: float = TOLERANCE) -> float:
    """Length (mm) over which segment r-s lies along segment p-q (within ``tolerance``)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < 1.0:
        return 0.0
    ux, uy = dx / length, dy / length
    off_r = abs((r[0] - p[0]) * uy - (r[1] - p[1]) * ux)
    off_s = abs((s[0] - p[0]) * uy - (s[1] - p[1]) * ux)
    if off_r > tolerance or off_s > tolerance:
        return 0.0
    t1 = (r[0] - p[0]) * ux + (r[1] - p[1]) * uy
    t2 = (s[0] - p[0]) * ux + (s[1] - p[1]) * uy
    return max(0.0, min(length, max(t1, t2)) - max(0.0, min(t1, t2)))


def _box(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def rotate(point: Point, angle: float) -> Point:
    """``point`` in axes turned by ``angle`` (radians) from the global ones."""
    c, s = math.cos(angle), math.sin(angle)
    return point[0] * c + point[1] * s, -point[0] * s + point[1] * c


def axes_angle(outlines: list[list[Point]]) -> float:
    """The angle (radians, -45 to 45 degrees) of the least rotated rectangle around the
    outlines: 0 for a panel square to the global axes."""
    from shapely.geometry import MultiPoint

    points = [pt for outline in outlines for pt in outline]
    rect = MultiPoint(points).minimum_rotated_rectangle
    coords = list(getattr(rect, "exterior", rect).coords) if rect.geom_type == "Polygon" else []
    if len(coords) < 3:
        return 0.0
    (x1, y1), (x2, y2) = coords[0], coords[1]
    angle = math.atan2(y2 - y1, x2 - x1)
    quarter = math.pi / 2.0
    angle = (angle + math.pi / 4.0) % quarter - math.pi / 4.0
    return 0.0 if abs(angle) < math.radians(0.5) else angle


def _side_of(p: Point, q: Point, ccw: bool, angle: float = 0.0) -> str:
    """W/E/S/N of an edge by its outward normal, in axes turned by ``angle``."""
    p, q = rotate(p, angle), rotate(q, angle)
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    sign = 1.0 if ccw else -1.0
    nx = sign * (q[1] - p[1]) / length
    ny = -sign * (q[0] - p[0]) / length
    if abs(nx) >= abs(ny):
        return "E" if nx > 0 else "W"
    return "N" if ny > 0 else "S"


def _on_support(p: Point, q: Point, supports: list[Support]) -> tuple[float, Support | None]:
    best, found, total = 0.0, None, 0.0
    for s in supports:
        o = overlap(p, q, s.a, s.b)
        if o > 0:
            total += o
            if o > best:
                best, found = o, s
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    return min(total, length), found


MIN_PIECE = 0.05e6     # mm2: a piece of a cut smaller than this is a sliver, dropped


def split_at_supports(areas: list[SlabArea], supports: list[Support]) -> list[SlabArea]:
    """Cut every area object along the beam and wall lines that cross it, as ETABS models
    often have one slab object over several bays. A piece keeps the properties and
    loads of its object and is named ``<object>:<n>`` (n from the south-west); an object
    that no line crosses is kept whole."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union

    out = []
    for a in areas:
        shape = Polygon(a.outline).buffer(0)
        if shape.is_empty or shape.area <= 0:
            out.append(a)
            continue
        reach = shape.buffer(TOLERANCE)
        lines = []
        for s in supports:
            if s.story != a.story or abs(s.z - a.z) > TOLERANCE:
                continue
            line = LineString([s.a, s.b])
            if line.length < 1.0 or not line.intersects(reach):
                continue
            lines.append(line.intersection(shape.buffer(1.0)))
        if not lines:
            out.append(a)
            continue
        cells = [c for c in polygonize(unary_union([shape.exterior, *lines]))
                 if shape.contains(c.representative_point()) and c.area >= MIN_PIECE]
        if len(cells) <= 1:
            out.append(a)
            continue
        cells.sort(key=lambda c: (round(c.centroid.y), round(c.centroid.x)))
        for n, cell in enumerate(cells, start=1):
            coords = [(float(x), float(y)) for x, y in list(cell.exterior.coords)[:-1]]
            out.append(SlabArea(f"{a.name}:{n}", a.story, a.z, coords, a.h, a.section, a.fc,
                                a.dead, a.live, a.load_note))
    return out


def make_panels(areas: list[SlabArea], supports: list[Support]) -> list[Panel]:
    """Cut the area objects at the beam and wall lines, join the pieces into panels and
    describe the sides of each."""
    areas = split_at_supports(areas, supports)
    by_level: dict[tuple[str, float], list[SlabArea]] = defaultdict(list)
    for a in areas:
        by_level[(a.story, round(a.z))].append(a)
    panels: list[Panel] = []
    for (story, _), level in by_level.items():
        z = level[0].z
        level_supports = [s for s in supports
                          if s.story == story and abs(s.z - z) <= TOLERANCE]
        parent = {a.name: a.name for a in level}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        shared: dict[str, list] = defaultdict(list)   # area -> [(other, length, edge)]
        boxes = {a.name: _box(a.outline) for a in level}
        for i, a in enumerate(level):
            for b in level[i + 1:]:
                ba, bb = boxes[a.name], boxes[b.name]
                if (ba[2] < bb[0] - TOLERANCE or bb[2] < ba[0] - TOLERANCE
                        or ba[3] < bb[1] - TOLERANCE or bb[3] < ba[1] - TOLERANCE):
                    continue
                for e in edges(a.outline):
                    for g in edges(b.outline):
                        o = overlap(e[0], e[1], g[0], g[1], tolerance=LEVEL_TOLERANCE + 49)
                        if o > 50.0:
                            shared[a.name].append((b.name, o, e))
                            shared[b.name].append((a.name, o, g))
                            on, _ = _on_support(e[0], e[1], level_supports)
                            if on < SHARE * o and abs(a.h - b.h) < 1.0:
                                parent[find(a.name)] = find(b.name)
        groups: dict[str, list[SlabArea]] = defaultdict(list)
        for a in level:
            groups[find(a.name)].append(a)
        start = len(panels)
        owner: dict[str, int] = {}
        for members in groups.values():
            index = len(panels)
            for m in members:
                owner[m.name] = index
            angle = axes_angle([m.outline for m in members])
            box = _box([rotate(pt, angle) for m in members for pt in m.outline])
            area = sum(abs(polygon_area(m.outline)) for m in members)
            first = max(members, key=lambda m: abs(polygon_area(m.outline)))
            weight = sum(abs(polygon_area(m.outline)) for m in members) or 1.0
            dead = sum(m.dead * abs(polygon_area(m.outline)) for m in members) / weight
            live = sum(m.live * abs(polygon_area(m.outline)) for m in members) / weight
            notes = sorted({m.load_note for m in members if m.load_note})
            panels.append(Panel(index, story, z, sorted(m.name for m in members), box, area,
                                first.h, first.fc, first.section, dead, live,
                                load_note="; ".join(notes), angle=angle))
        for panel in panels[start:]:
            names = set(panel.areas)
            totals = {k: 0.0 for k in SIDES}
            sides = {k: Side() for k in SIDES}
            cover = {k: 0.0 for k in SIDES}
            cont = {k: 0.0 for k in SIDES}
            reach: dict[str, dict[int, float]] = {k: defaultdict(float) for k in SIDES}
            best_support: dict[str, dict[str, tuple[float, Support]]] = {k: {} for k in SIDES}
            for m in (a for a in level if a.name in names):
                ccw = polygon_area(m.outline) > 0
                for p, q in edges(m.outline):
                    length = math.hypot(q[0] - p[0], q[1] - p[1])
                    if length < 1.0:
                        continue
                    inner = sum(o for other, o, e in shared[m.name]
                                if e == (p, q) and other in names)
                    if inner > SHARE * length:
                        continue                     # an edge inside the panel
                    k = _side_of(p, q, ccw, panel.angle)
                    totals[k] += length
                    on, sup = _on_support(p, q, level_supports)
                    cover[k] += on
                    if sup is not None:
                        prev = best_support[k].get(sup.name)
                        best_support[k][sup.name] = ((prev[0] if prev else 0.0) + on, sup)
                    for other, o, e in shared[m.name]:
                        if e == (p, q) and other not in names:
                            cont[k] += o
                            reach[k][owner[other]] += o
            for k in SIDES:
                t = totals[k]
                side = sides[k]
                if t > 0:
                    side.supported = min(1.0, cover[k] / t)
                    side.continuous = min(1.0, cont[k] / t)
                if best_support[k]:
                    side.support = max(best_support[k].values(), key=lambda v: v[0])[1]
                if reach[k]:
                    side.neighbour = max(reach[k].items(), key=lambda v: v[1])[0]
            panel.sides = sides
            if panel.rectangular < RECTANGLE:
                panel.flags.append("NOT RECTANGULAR: DESIGNED ON ITS BOUNDING RECTANGLE")
    return panels


def row(panels: list[Panel], start: int, direction: str) -> list[int]:
    """The panels in a continuous row through ``start`` along x ("x") or y ("y"),
    following continuous sides, in order."""
    back, ahead = ("W", "E") if direction == "x" else ("S", "N")
    out = [start]
    seen = {start}
    for side, add in ((back, lambda i: out.insert(0, i)), (ahead, out.append)):
        current = start
        while True:
            s = panels[current].sides[side]
            nxt = s.neighbour if s.is_continuous else None
            if nxt is None or nxt in seen:
                break
            seen.add(nxt)
            add(nxt)
            current = nxt
    return out
