"""The framing of each floor: girders, beams and slabs from the columns and walls (``sdt model``).

Everything here works on plain data, in millimetres, with no ETABS:

* **Girders** join each column (or wall end) to its nearest neighbour along X
  and along Y, within ``FramingSettings.off_line`` of the line. A girder that
  meets a wall stops at it; one that would run along a wall is not made.
* **Bays** are the faces the girders, the walls and the slab edge close. A bay
  with girders or walls all around is a panel; one open to the slab edge is an
  overhang, framed with cantilever beams and an edge beam.
* **Each panel** is either a two-way slab (NSCP Table 408.3.1.2; ACI Table
  8.3.1.2) or a one-way slab (NSCP Table 407.3.1.1; ACI Table 7.3.1.1) on
  beams along X or Y at equal spacing. Beam and girder depths follow NSCP
  Table 409.3.1.1 (ACI Table 9.3.1.1), with the factor (0.4 + fy / 700) of
  409.3.1.1.1 and 407.3.1.1.1 for fy other than 420 MPa, rounded up to the
  setup sizes; the width is the least setup width with b ≥ 0.3 h (NSCP
  418.6.2.1(b); ACI 18.6.2.1(b)).
* **A framing** holds one slab thickness for the floor. Each panel takes the
  option of least concrete that the thickness allows; neighbouring panels keep
  one beam direction where the volumes are close.
* **Frame action** is screened by rules: every column held by a girder along X
  and Y, girder lines of at least two bays, no girder with a clear span under
  4d (NSCP 418.6.2.1(a); ACI 18.6.2.1(a)), the centre of rigidity near the
  centre of mass.

The thicknesses are the code minimums for deflection without calculation; the
sizes are rule sizes, which ``sdt design`` checks and resizes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

GIRDER, BEAM, CANTILEVER, EDGE, TRANSFER, TRIMMER = "G", "B", "CB", "EB", "TG", "TB"
KIND_NAMES = {GIRDER: "girder", BEAM: "beam", CANTILEVER: "cantilever beam",
              EDGE: "edge beam", TRANSFER: "transfer girder", TRIMMER: "trimmer beam"}
TWO_WAY, ONE_WAY_X, ONE_WAY_Y, OVERHANG = "two-way", "one-way X", "one-way Y", "overhang"
NEAR = 1.0                   # mm: points this close are the same
CLOSE_VOLUME = 0.05          # neighbours keep one beam direction within this share
ECCENTRICITY_LIMIT = 0.10    # centre of rigidity to centre of mass, share of the plan size
MIN_BEAM_SPACING = 1200.0    # mm: beams closer than this are not tried
TWO_WAY_RATIO = 2.0          # long / short span above this: one-way (NSCP 408.3.1.1)
COVER_TO_D = 65.0            # mm: d = h - this, for the 4d clear span rule


# =============================================================================
# INPUT
# =============================================================================
@dataclass(frozen=True)
class Support:
    """A column of the story below the floor: centre and plan size along X and Y."""

    x: float
    y: float
    size_x: float = 400.0
    size_y: float = 400.0
    name: str = ""


@dataclass(frozen=True)
class Wall:
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float = 200.0


@dataclass
class Floor:
    """What the drawing gives for one floor (the level at the top of ``story``)."""

    story: str
    supports: list[Support]
    walls: list[Wall] = field(default_factory=list)
    outline: list[list[tuple[float, float]]] = field(default_factory=list)   # S-SLAB
    openings: list[list[tuple[float, float]]] = field(default_factory=list)  # S-OPENING
    zones: list[tuple[str, list[tuple[float, float]]]] = field(default_factory=list)  # S-LOAD
    planted: list[Support] = field(default_factory=list)  # columns above with none below


@dataclass
class FramingSettings:
    fy: float = 414.0                       # MPa
    girder_widths: list[float] = field(default_factory=lambda: [300, 400, 500, 600])
    girder_depths: list[float] = field(default_factory=lambda: [500, 600, 700, 800, 900, 1000])
    beam_widths: list[float] = field(default_factory=lambda: [200, 300, 400])
    beam_depths: list[float] = field(default_factory=lambda: [400, 500, 600, 700, 800])
    thicknesses: list[float] = field(default_factory=lambda: [100, 125, 150, 200])
    hold: str = "volume"                    # "volume", "thickness" or "spacing"
    held: float = 0.0                       # the thickness or the spacing held (mm)
    max_depth: dict[str, float] = field(default_factory=dict)   # story: deepest member (mm)
    off_line: float = 300.0                 # mm: a column this far off a line still frames
    trimmer_from: float = 1000.0            # mm: openings this large get trimmer beams


# =============================================================================
# MEMBERS AND RULE SIZES
# =============================================================================
@dataclass(frozen=True)
class Member:
    kind: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.0
    depth: float = 0.0
    note: str = ""

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def along_x(self) -> bool:
        return abs(self.x2 - self.x1) >= abs(self.y2 - self.y1)

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.x1, self.y1), (self.x2, self.y2)


def fy_factor(fy: float) -> float:
    """The factor on the span ratios for fy other than 420 MPa (NSCP 407.3.1.1.1,
    409.3.1.1.1; ACI 7.3.1.1.1, 9.3.1.1.1)."""
    return 0.4 + fy / 700.0


BEAM_RATIO = {0: 16.0, 1: 18.5, 2: 21.0}      # continuous ends: NSCP Table 409.3.1.1
SLAB_RATIO = {0: 20.0, 1: 24.0, 2: 28.0}      # NSCP Table 407.3.1.1
CANTILEVER_BEAM, CANTILEVER_SLAB = 8.0, 10.0


def beam_depth(span: float, continuous_ends: int, fy: float) -> float:
    """Least depth of a beam (NSCP Table 409.3.1.1; ACI Table 9.3.1.1)."""
    return span / BEAM_RATIO[max(0, min(2, continuous_ends))] * fy_factor(fy)


def one_way_thickness(span: float, continuous_ends: int, fy: float) -> float:
    """Least thickness of a one-way slab (NSCP Table 407.3.1.1; ACI Table 7.3.1.1)."""
    return span / SLAB_RATIO[max(0, min(2, continuous_ends))] * fy_factor(fy)


def two_way_thickness(long_clear: float, beta: float, alpha_fm: float, fy: float,
                      exterior: bool = False) -> float:
    """Least thickness of a two-way slab with beams on all sides (NSCP Table 408.3.1.2;
    ACI Table 8.3.1.2). ``long_clear`` is the clear span in the long direction, ``beta``
    the ratio of the clear spans. At alpha_fm 0.2 or less, Table 408.3.1.1 (no interior
    beams, no drop panels) applies; ``exterior`` for a panel at the slab edge."""
    if alpha_fm <= 0.2:
        return max(long_clear / _flat_ratio(fy, exterior), 125.0)
    top = long_clear * (0.8 + fy / 1400.0)
    if alpha_fm <= 2.0:
        return max(top / (36.0 + 5.0 * beta * (alpha_fm - 0.2)), 125.0)
    return max(top / (36.0 + 9.0 * beta), 90.0)


def _flat_ratio(fy: float, exterior: bool) -> float:
    """ln / h of NSCP Table 408.3.1.1 without drop panels, interpolated on fy: an
    exterior panel without edge beams, or an interior one."""
    points = [(280.0, 33.0, 36.0), (420.0, 30.0, 33.0), (520.0, 28.0, 31.0)]
    fy = min(max(fy, 280.0), 520.0)
    for (f1, e1, i1), (f2, e2, i2) in zip(points, points[1:]):
        if fy <= f2:
            t = (fy - f1) / (f2 - f1)
            return (e1 + t * (e2 - e1)) if exterior else (i1 + t * (i2 - i1))
    return points[-1][1] if exterior else points[-1][2]


def round_up(value: float, sizes: list[float]) -> float | None:
    """The least size at or above ``value``; None when every size is smaller."""
    return next((float(s) for s in sorted(sizes) if s >= value - 1e-6), None)


def rule_section(depth_needed: float, depths: list[float], widths: list[float]
                 ) -> tuple[float, float] | None:
    """(width, depth) of the setup sizes: the depth rounded up, the least width with
    b ≥ 0.3 h (NSCP 418.6.2.1(b); ACI 18.6.2.1(b), and the setup's own rule)."""
    depth = round_up(depth_needed, depths)
    if depth is None:
        return None
    width = next((float(w) for w in sorted(widths) if w >= 0.3 * depth - 1e-6), None)
    return (width, depth) if width is not None else None


# =============================================================================
# GIRDERS
# =============================================================================
def _wall_lines(walls: list[Wall]):
    from shapely.geometry import LineString

    return [LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in walls]


def wall_nodes(walls: list[Wall]) -> list[tuple[float, float]]:
    return [(w.x1, w.y1) for w in walls] + [(w.x2, w.y2) for w in walls]


def floor_polygon(floor: Floor):
    """The slab of the floor (its S-SLAB outlines); None when none is drawn."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    shapes = [Polygon(points).buffer(0) for points in floor.outline if len(points) >= 3]
    shapes = [s for s in shapes if s.area > 0]
    return unary_union(shapes) if shapes else None


def girders(floor: Floor, off_line: float) -> list[Member]:
    """A girder from each column or wall end to its nearest neighbour along +X and +Y.

    A girder that crosses a wall stops at it; one that runs along a wall, or whose
    middle is off the slab, is not made.
    """
    from shapely.geometry import LineString, Point

    nodes = [(s.x, s.y) for s in floor.supports] + wall_nodes(floor.walls)
    walls = _wall_lines(floor.walls)
    slab = floor_polygon(floor)
    reach = slab.buffer(off_line) if slab is not None else None
    found: dict[frozenset, Member] = {}
    for x, y in nodes:
        start = Point(x, y)
        for along_x in (True, False):
            ahead = [(nx, ny) for nx, ny in nodes
                     if ((nx - x) if along_x else (ny - y)) > NEAR
                     and abs((ny - y) if along_x else (nx - x)) <= off_line]
            if not ahead:
                continue
            end = min(ahead, key=lambda p: (p[0] - x) if along_x else (p[1] - y))
            line = LineString([(x, y), end])
            for wall, data in zip(walls, floor.walls):
                if line.distance(wall) > NEAR:
                    continue
                if _runs_along(line, wall, data.thickness):
                    end = None
                    break
                cut = line.intersection(wall)
                points = [cut] if cut.geom_type == "Point" else [
                    p for p in getattr(cut, "geoms", []) if p.geom_type == "Point"]
                points = [p for p in points if p.distance(start) > NEAR]
                if points:
                    first = min(points, key=lambda p: p.distance(start))
                    if first.distance(start) < math.dist((x, y), end):
                        end = (first.x, first.y)
            if end is None or math.dist((x, y), end) < NEAR:
                continue
            middle = Point((x + end[0]) / 2.0, (y + end[1]) / 2.0)
            if reach is not None and not reach.contains(middle):
                continue
            key = frozenset({_key((x, y)), _key(end)})
            found.setdefault(key, Member(GIRDER, x, y, end[0], end[1]))
    return list(found.values())


FOR_FRAME_ACTION = "for frame action"


def frame_girders(floor: Floor, members: list[Member], tolerance: float = 50.0
                  ) -> list[Member]:
    """Girders added so every column is held along X and along Y: from a column with
    no girder along an axis to the nearest member or wall it meets that way, on
    each side (a column off every line, such as one standing on a transfer girder
    below)."""
    from shapely.geometry import LineString, Point

    lines = [LineString(m.ends()) for m in members] + _wall_lines(floor.walls)
    slab = floor_polygon(floor)
    reach = 1e6 if slab is None else max(slab.bounds[2] - slab.bounds[0],
                                         slab.bounds[3] - slab.bounds[1])
    out = []
    for s in floor.supports:
        here = Point(s.x, s.y)
        for along_x in (True, False):
            if any(m.along_x == along_x and min(math.dist((s.x, s.y), e) for e in m.ends())
                   <= tolerance for m in members + out):
                continue
            for sign in (1.0, -1.0):
                far = (s.x + sign * reach, s.y) if along_x else (s.x, s.y + sign * reach)
                ray = LineString([(s.x, s.y), far])
                hits = []
                for line in lines:
                    cut = ray.intersection(line)
                    for p in [cut] if cut.geom_type == "Point" else [
                            g for g in getattr(cut, "geoms", []) if g.geom_type == "Point"]:
                        if p.distance(here) > tolerance:
                            hits.append(p)
                if not hits:
                    continue
                end = min(hits, key=lambda p: p.distance(here))
                if slab is not None and not slab.buffer(tolerance).contains(
                        LineString([(s.x, s.y), (end.x, end.y)]).interpolate(0.5, True)):
                    continue
                out.append(Member(GIRDER, s.x, s.y, end.x, end.y, note=FOR_FRAME_ACTION))
    return out


def _runs_along(line, wall, thickness: float) -> bool:
    """Whether a girder line lies along a wall: in its direction (within 10 degrees),
    with its middle within the wall."""
    (ax, ay), (bx, by) = line.coords[0], line.coords[-1]
    (cx, cy), (dx, dy) = wall.coords[0], wall.coords[-1]
    u, v = (bx - ax, by - ay), (dx - cx, dy - cy)
    cosine = abs(u[0] * v[0] + u[1] * v[1]) / max(math.hypot(*u) * math.hypot(*v), 1e-9)
    middle = line.interpolate(0.5, normalized=True)
    return cosine >= math.cos(math.radians(10.0)) and wall.distance(middle) <= thickness / 2 + NEAR


def _key(point: tuple[float, float]) -> tuple[int, int]:
    return (round(point[0]), round(point[1]))


def continuity(members: list[Member]) -> dict[int, int]:
    """For each member (by index): how many of its ends continue into a member
    in line with it (0, 1 or 2)."""
    out = {}
    for i, m in enumerate(members):
        count = 0
        for end in m.ends():
            if any(j != i and other.along_x == m.along_x
                   and any(math.dist(end, e) <= 5 * NEAR for e in other.ends())
                   for j, other in enumerate(members)):
                count += 1
        out[i] = count
    return out


def size_members(members: list[Member], depths: list[float], widths: list[float], fy: float,
                 problems: list[str], continuous: dict[int, int] | None = None) -> list[Member]:
    """Give each member its rule size (Table 409.3.1.1; a cantilever ℓ/8)."""
    continuous = continuity(members) if continuous is None else continuous
    out = []
    for i, m in enumerate(members):
        needed = (m.length / CANTILEVER_BEAM * fy_factor(fy) if m.kind == CANTILEVER
                  else beam_depth(m.length, continuous.get(i, 0), fy))
        section = rule_section(needed, depths, widths)
        if section is None:
            section = (float(max(widths)), float(max(depths)))
            problems.append(f"a {KIND_NAMES[m.kind]} of {m.length / 1000:.2f} m needs "
                            f"{needed:.0f} mm, deeper than the largest setup size")
        out.append(replace(m, width=section[0], depth=section[1]))
    return out


# =============================================================================
# BAYS
# =============================================================================
@dataclass
class Bay:
    polygon: object                 # shapely Polygon
    panel: bool                     # supported all around
    exterior: bool                  # touches the slab edge
    sides: dict = field(default_factory=dict)   # "W", "E", "S", "N": a panel beyond it

    @property
    def lx(self) -> float:
        x0, _, x1, _ = self.polygon.bounds
        return x1 - x0

    @property
    def ly(self) -> float:
        _, y0, _, y1 = self.polygon.bounds
        return y1 - y0

    def continuous(self, along_x: bool) -> int:
        """Sides normal to X (along_x) or to Y with a panel beyond them: 0, 1 or 2."""
        keys = ("W", "E") if along_x else ("S", "N")
        return sum(1 for k in keys if self.sides.get(k))


def bays(floor: Floor, members: list[Member], tolerance: float = 50.0) -> list[Bay]:
    """The faces closed by the members, the walls and the slab edge, on the slab."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    lines = [LineString(m.ends()) for m in members] + _wall_lines(floor.walls)
    supports = unary_union(lines).buffer(tolerance) if lines else None
    slab = floor_polygon(floor)
    edge = slab.boundary if slab is not None else None
    if edge is not None:
        lines.append(edge)
    faces = [f for f in polygonize(unary_union(lines)) if f.area > 1e4]
    if slab is not None:
        faces = [f for f in faces if slab.buffer(NEAR).contains(f.representative_point())]
    out = []
    for face in faces:
        ring = face.exterior
        open_length = ring.difference(supports).length if supports is not None else ring.length
        touches = edge is not None and ring.intersection(edge.buffer(tolerance)).length > tolerance
        out.append(Bay(face, panel=open_length <= tolerance * 2, exterior=bool(touches)))
    for bay in out:
        x0, y0, x1, y1 = bay.polygon.bounds
        for other in out:
            if other is bay or not other.panel:
                continue
            shared = bay.polygon.buffer(NEAR * 2).intersection(other.polygon.buffer(NEAR * 2))
            if shared.is_empty:
                continue
            sx0, sy0, sx1, sy1 = shared.bounds
            if (sy1 - sy0) > 10 * (sx1 - sx0):          # a side normal to X
                middle = (sx0 + sx1) / 2.0
                bay.sides["W" if abs(middle - x0) < abs(middle - x1) else "E"] = True
            elif (sx1 - sx0) > 10 * (sy1 - sy0):        # a side normal to Y
                middle = (sy0 + sy1) / 2.0
                bay.sides["S" if abs(middle - y0) < abs(middle - y1) else "N"] = True
    return out


# =============================================================================
# PANEL OPTIONS
# =============================================================================
@dataclass
class Option:
    kind: str                       # TWO_WAY, ONE_WAY_X, ONE_WAY_Y, OVERHANG
    thickness: float                # least slab thickness it needs (mm)
    beams: list[Member] = field(default_factory=list)   # sized
    spacing: float = math.inf       # mm between the beams (and the girders)

    def beam_volume(self, thickness: float) -> float:
        """Concrete of its beams below the slab, m3."""
        return sum(b.width * max(b.depth - thickness, 0.0) * b.length for b in self.beams) / 1e9


def alpha_f(beam: tuple[float, float], slab_width: float, thickness: float) -> float:
    """Ecb Ib / Ecs Is of an edge beam (NSCP 408.10.2.7; ACI 8.10.2.7), the beam as a
    plain rectangle (its flange left out, which gives a lower alpha)."""
    width, depth = beam
    return (width * depth ** 3) / (slab_width * thickness ** 3)


def panel_options(bay: Bay, girder: tuple[float, float], settings: FramingSettings,
                  thickness: float, problems: list[str] | None = None) -> list[Option]:
    """The ways to frame one panel, each with the slab thickness it needs.

    ``girder`` is the (width, depth) of the girders around it and ``thickness``
    the slab tried (alpha_f depends on it).
    """
    fy = settings.fy
    lx, ly = bay.lx, bay.ly
    x0, y0, x1, y1 = bay.polygon.bounds
    out = []
    long_clear, short_clear = max(lx, ly) - girder[0], min(lx, ly) - girder[0]
    if short_clear > 0 and long_clear / short_clear <= TWO_WAY_RATIO:
        alpha = sum(alpha_f(girder, width, thickness) for width in (ly, ly, lx, lx)) / 4.0
        need = two_way_thickness(long_clear, long_clear / short_clear, alpha, fy, bay.exterior)
        if bay.exterior and alpha_f(girder, max(lx, ly), thickness) < 0.8:
            need *= 1.10   # NSCP 408.3.1.2.1 (ACI 8.3.1.2.1)
        out.append(Option(TWO_WAY, need, [], max(lx, ly)))
    for kind, span, across in ((ONE_WAY_X, lx, ly), (ONE_WAY_Y, ly, lx)):
        along_x = kind == ONE_WAY_X
        beam_ends = bay.continuous(along_x)           # the beams continue past these sides
        slab_ends = bay.continuous(not along_x)       # the slab continues past these
        for count in range(max(1, int(across // MIN_BEAM_SPACING))):
            spacing = across / (count + 1)
            ends = (2 if slab_ends >= 2 else 1) if count else slab_ends
            need = one_way_thickness(spacing, ends, fy)
            beams = []
            if count:
                section = rule_section(beam_depth(span, beam_ends, fy), settings.beam_depths,
                                       settings.beam_widths)
                if section is None:
                    if problems is not None:
                        problems.append(f"a beam of {span / 1000:.2f} m is deeper than the "
                                        "largest setup size")
                    break
                for k in range(1, count + 1):
                    if along_x:
                        y = y0 + k * spacing
                        beams.append(Member(BEAM, x0, y, x1, y, *section))
                    else:
                        x = x0 + k * spacing
                        beams.append(Member(BEAM, x, y0, x, y1, *section))
            out.append(Option(kind, need, beams, spacing))
    return out


def best_option(options: list[Option], thickness: float, settings: FramingSettings,
                prefer: str | None = None, strict: bool = False) -> Option | None:
    """The option of least beam volume that ``thickness`` allows (and, holding a
    spacing, with its beams no farther apart than it); ``prefer``, a kind taken
    when it is within CLOSE_VOLUME of the least, or, ``strict``, whenever the
    panel needs beams and that kind fits."""
    fit = [o for o in options if o.thickness <= thickness + 1e-6]
    if settings.hold == "spacing" and settings.held > 0:
        fit = [o for o in fit if o.kind == TWO_WAY or o.spacing <= settings.held + 1e-6]
    if not fit:
        return None
    if prefer and strict:
        kept = [o for o in fit if o.kind in (prefer, TWO_WAY)]
        fit = kept or fit
    least = min(fit, key=lambda o: (o.beam_volume(thickness), len(o.beams)))
    if prefer and not strict:
        limit = least.beam_volume(thickness) * (1 + CLOSE_VOLUME) + 1e-9
        alike = [o for o in fit if o.kind == prefer and o.beam_volume(thickness) <= limit]
        if alike:
            return min(alike, key=lambda o: o.beam_volume(thickness))
    return least


# =============================================================================
# OVERHANGS, TRANSFER GIRDERS, OPENINGS
# =============================================================================
def overhang_members(bay: Bay, members: list[Member], supports: list[Support],
                     spacing: float, tolerance: float = 50.0) -> list[Member]:
    """Cantilever beams and an edge beam for a bay open to the slab edge.

    The cantilevers leave the longest girder (or wall) side of the bay at right
    angles, at each column on it and between them at no more than ``spacing``;
    the edge beam joins their tips along the free edge, one member from tip to tip.
    """
    from shapely.geometry import LineString

    ring = bay.polygon.exterior
    sides = []
    for m in members:
        shared = ring.intersection(LineString(m.ends()).buffer(tolerance))
        if not shared.is_empty and shared.length > 10 * tolerance:
            sides.append((shared.length, m))
    if not sides:
        return []
    _, base = max(sides, key=lambda s: s[0])
    # every member on the same line as the longest one bears the overhang
    line = [m for _, m in sides if m.along_x == base.along_x
            and (abs(m.y1 - base.y1) <= tolerance if base.along_x
                 else abs(m.x1 - base.x1) <= tolerance)]
    x0, y0, x1, y1 = bay.polygon.bounds
    out = []
    if base.along_x:
        y = base.y1
        tip = y1 if abs(y1 - y) > abs(y0 - y) else y0
        low = max(x0, min(min(m.x1, m.x2) for m in line))
        high = min(x1, max(max(m.x1, m.x2) for m in line))
        stations = _stations(low, high, spacing, [s.x for s in supports
                                                  if abs(s.y - y) <= tolerance])
        out += [Member(CANTILEVER, x, y, x, tip) for x in stations]
        out += [Member(EDGE, a, tip, b, tip) for a, b in zip(stations, stations[1:])]
    else:
        x = base.x1
        tip = x1 if abs(x1 - x) > abs(x0 - x) else x0
        low = max(y0, min(min(m.y1, m.y2) for m in line))
        high = min(y1, max(max(m.y1, m.y2) for m in line))
        stations = _stations(low, high, spacing, [s.y for s in supports
                                                  if abs(s.x - x) <= tolerance])
        out += [Member(CANTILEVER, x, y, tip, y) for y in stations]
        out += [Member(EDGE, tip, a, tip, b) for a, b in zip(stations, stations[1:])]
    return out


def _stations(low: float, high: float, spacing: float, columns: list[float]) -> list[float]:
    """Points from ``low`` to ``high``: the ends, the columns between, and more
    points so that none are farther apart than ``spacing``."""
    points = sorted({low, high, *[c for c in columns if low - NEAR <= c <= high + NEAR]})
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        parts = max(1, math.ceil((b - a) / spacing - 1e-9))
        out += [a + (b - a) * k / parts for k in range(1, parts + 1)]
    return out


def transfer_girders(floor: Floor, members: list[Member], panels: list[Bay],
                     tolerance: float) -> tuple[list[Member], list[str]]:
    """The members with a transfer girder under each planted column: a girder it
    sits on becomes one, otherwise a new one crosses its bay the short way."""
    from shapely.geometry import LineString, Point

    out = list(members)
    notes = []
    for column in floor.planted:
        point = Point(column.x, column.y)
        on = [i for i, m in enumerate(out) if LineString(m.ends()).distance(point) <= tolerance]
        if on:
            i = on[0]
            out[i] = replace(out[i], kind=TRANSFER, note=f"under {column.name or 'a column'}")
            notes.append(f"{column.name or 'column'} at ({column.x / 1000:.2f}, "
                         f"{column.y / 1000:.2f}) m sits on a girder, made a transfer girder")
            continue
        bay = next((b for b in panels if b.polygon.buffer(NEAR).contains(point)), None)
        if bay is None:
            notes.append(f"{column.name or 'column'} at ({column.x / 1000:.2f}, "
                         f"{column.y / 1000:.2f}) m is off the slab: no transfer girder")
            continue
        x0, y0, x1, y1 = bay.polygon.bounds
        if bay.lx <= bay.ly:   # span the short way, between the girders across X
            new = Member(TRANSFER, x0, column.y, x1, column.y)
        else:
            new = Member(TRANSFER, column.x, y0, column.x, y1)
        out.append(replace(new, note=f"under {column.name or 'a column'}"))
        notes.append(f"{column.name or 'column'} at ({column.x / 1000:.2f}, "
                     f"{column.y / 1000:.2f}) m: a transfer girder of "
                     f"{new.length / 1000:.2f} m added under it")
    return out, notes


def trimmers(floor: Floor, panels: list[Bay], settings: FramingSettings) -> list[Member]:
    """Trimmer beams around each opening of ``trimmer_from`` or more: along its sides
    parallel to the short span of its bay, from girder to girder, and headers
    between them along the other two sides."""
    from shapely.geometry import Polygon

    out = []
    for points in floor.openings:
        hole = Polygon(points)
        ox0, oy0, ox1, oy1 = hole.bounds
        if max(ox1 - ox0, oy1 - oy0) < settings.trimmer_from:
            continue
        bay = next((b for b in panels if b.polygon.intersects(hole)), None)
        if bay is None:
            continue
        x0, y0, x1, y1 = bay.polygon.bounds
        if bay.lx <= bay.ly:   # trimmers along X, girder to girder; headers along Y
            out += [Member(TRIMMER, x0, oy0, x1, oy0), Member(TRIMMER, x0, oy1, x1, oy1),
                    Member(TRIMMER, ox0, oy0, ox0, oy1), Member(TRIMMER, ox1, oy0, ox1, oy1)]
        else:
            out += [Member(TRIMMER, ox0, y0, ox0, y1), Member(TRIMMER, ox1, y0, ox1, y1),
                    Member(TRIMMER, ox0, oy0, ox1, oy0), Member(TRIMMER, ox0, oy1, ox1, oy1)]
    return out


# =============================================================================
# THE FRAMING OF A FLOOR
# =============================================================================
@dataclass
class FloorFraming:
    story: str
    thickness: float
    scheme: str
    members: list[Member] = field(default_factory=list)
    choices: list[tuple[Bay, Option]] = field(default_factory=list)
    slab_area: float = 0.0          # m2, openings out
    volume: float = 0.0             # m3: slab + beams and girders below it
    formwork: float = 0.0           # m2: slab soffit + beam sides and bottoms
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stories: list[str] = field(default_factory=list)   # every floor framed alike

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def copies(self) -> int:
        return max(1, len(self.stories))

    @property
    def label(self) -> str:
        return self.story if self.copies == 1 else f"{self.stories[0]} to {self.stories[-1]}"


SCHEMES = {"least": None, "X": ONE_WAY_X, "Y": ONE_WAY_Y}
SCHEME_NAMES = {"least": "least concrete bay by bay", "X": "beams along X",
                "Y": "beams along Y"}


def frame_floor(floor: Floor, settings: FramingSettings, thickness: float,
                scheme: str = "least", stories: list[str] | None = None) -> FloorFraming:
    """Frame one floor holding the slab ``thickness``: girders, transfer girders and
    trimmers, then each panel by ``scheme`` ("least", "X" or "Y"), then the
    overhangs. Problems name what the thickness or the setup sizes cannot do.
    ``stories``: every floor framed alike (a typical floor), for the depth limits."""
    from shapely.geometry import Polygon

    out = FloorFraming(floor.story, thickness, scheme, stories=list(stories or [floor.story]))
    tolerance = max(settings.off_line, 50.0)
    lines = girders(floor, settings.off_line)
    added = frame_girders(floor, lines)
    lines += added
    first = bays(floor, lines)
    lines, out.notes = transfer_girders(floor, lines, [b for b in first if b.panel], tolerance)
    if added:
        out.notes.append(f"{len(added)} girders added so that every column is held along X "
                         "and Y (frame action)")
    lines += trimmers(floor, [b for b in first if b.panel], settings)
    framed = size_members(lines, settings.girder_depths, settings.girder_widths, settings.fy,
                          out.problems)
    girder = max(((m.width, m.depth) for m in framed if m.kind == GIRDER),
                 default=(settings.girder_widths[0], settings.girder_depths[0]))
    holes = [Polygon(p) for p in floor.openings if len(p) >= 3]
    panels = [b for b in bays(floor, framed) if not any(h.contains(b.polygon.representative_point())
                                                       for h in holes)]
    prefer = SCHEMES.get(scheme)
    for bay in panels:
        if not bay.panel:
            continue
        options = panel_options(bay, girder, settings, thickness, out.problems)
        chosen = best_option(options, thickness, settings, prefer, strict=prefer is not None)
        if chosen is None:
            out.problems.append(f"a bay of {bay.lx / 1000:.2f} x {bay.ly / 1000:.2f} m needs "
                                f"more than {thickness:.0f} mm of slab")
            continue
        out.choices.append((bay, chosen))
    if scheme == "least":
        _keep_directions(out, settings, thickness, girder)
    beams = [b for _, option in out.choices for b in option.beams]
    for bay in panels:
        if bay.panel:
            continue
        spacing = thickness * SLAB_RATIO[1] / fy_factor(settings.fy)
        extra = overhang_members(bay, framed, floor.supports, spacing)
        if not extra:
            out.problems.append(f"an overhang of {bay.polygon.area / 1e6:.1f} m2 has no girder "
                                "to cantilever from")
            continue
        # a cantilever has no continuous end; an edge beam continues into the next one
        edges = [i for i, m in enumerate(extra) if m.kind == EDGE]
        edge_ends = continuity([extra[i] for i in edges])
        ends = {i: 0 for i in range(len(extra))}
        for k, i in enumerate(edges):
            ends[i] = edge_ends[k]
        extra = size_members(extra, settings.beam_depths + settings.girder_depths,
                             settings.beam_widths + settings.girder_widths, settings.fy,
                             out.problems, ends)
        out.choices.append((bay, Option(OVERHANG, 0.0, extra, spacing)))
    out.members = framed + beams + [b for bay, o in out.choices if o.kind == OVERHANG
                                    for b in o.beams]
    limits = [settings.max_depth[s] for s in out.stories if settings.max_depth.get(s)]
    deepest = min(limits) if limits else None
    if deepest:
        over = [m for m in out.members if m.depth > deepest + 1e-6]
        if over:
            out.problems.append(f"{len(over)} members deeper than the {deepest:.0f} mm the "
                                f"architect allows (the deepest {max(m.depth for m in over):.0f}"
                                " mm)")
    slab = sum(bay.polygon.area for bay, _ in out.choices) / 1e6
    out.slab_area = slab
    below = sum(m.width * max(m.depth - thickness, 0.0) * m.length for m in out.members) / 1e9
    out.volume = slab * thickness / 1000.0 + below
    out.formwork = slab + sum((2 * max(m.depth - thickness, 0.0)) * m.length
                              for m in out.members) / 1e6
    return out


def _keep_directions(framing: FloorFraming, settings: FramingSettings, thickness: float,
                     girder: tuple[float, float]) -> None:
    """Neighbouring one-way panels keep one beam direction where the volume of the
    other direction is within CLOSE_VOLUME of their own."""
    for _ in range(3):
        changed = False
        for index, (bay, option) in enumerate(framing.choices):
            if option.kind not in (ONE_WAY_X, ONE_WAY_Y):
                continue
            around = [o.kind for b, o in framing.choices if b is not bay
                      and o.kind in (ONE_WAY_X, ONE_WAY_Y)
                      and b.polygon.buffer(2 * NEAR).intersects(bay.polygon)]
            if not around:
                continue
            common = max(set(around), key=around.count)
            if common == option.kind:
                continue
            options = panel_options(bay, girder, settings, thickness)
            other = best_option(options, thickness, settings, common)
            if other is not None and other.kind == common:
                framing.choices[index] = (bay, other)
                changed = True
        if not changed:
            return


# =============================================================================
# FRAME ACTION
# =============================================================================
@dataclass
class FrameCheck:
    passed: bool
    findings: list[str]
    eccentricity: tuple[float, float] = (0.0, 0.0)   # share of the plan size, X and Y


def frame_action(floor: Floor, framing: FloorFraming, tolerance: float = 50.0) -> FrameCheck:
    """The rules a lateral frame must meet, on one floor."""
    findings = []
    lateral = [m for m in framing.members if m.kind in (GIRDER, TRANSFER)]
    for s in floor.supports:
        ends = [m for m in lateral
                if any(math.dist((s.x, s.y), e) <= tolerance for e in m.ends())]
        missing = [axis for axis, along in (("X", True), ("Y", False))
                   if not any(m.along_x == along for m in ends)]
        if missing:
            findings.append(f"column {s.name or ''} at ({s.x / 1000:.2f}, {s.y / 1000:.2f}) m "
                            f"has no girder along {' and '.join(missing)}".replace("  ", " "))
    for line in girder_lines([m for m in lateral if m.kind == GIRDER]):
        if len(line) < 2:
            m = line[0]
            findings.append(f"a girder line of one bay at ({m.x1 / 1000:.2f}, "
                            f"{m.y1 / 1000:.2f}) m")
    sizes = {(round(s.x), round(s.y)): s for s in floor.supports}
    for m in lateral:
        faces = sum(min(sizes[_key(e)].size_x if m.along_x else sizes[_key(e)].size_y,
                        m.length) / 2.0 for e in m.ends() if _key(e) in sizes)
        clear = m.length - faces
        if clear < 4 * (m.depth - COVER_TO_D):
            findings.append(f"a girder of {m.length / 1000:.2f} m has a clear span under 4d "
                            "(NSCP 418.6.2.1(a))")
    ex, ey = rigidity_eccentricity(floor, framing)
    if max(ex, ey) > ECCENTRICITY_LIMIT:
        findings.append(f"centre of rigidity {ex * 100:.0f} % (X) and {ey * 100:.0f} % (Y) of the "
                        f"plan from the centre of mass, over {ECCENTRICITY_LIMIT * 100:.0f} %")
    return FrameCheck(not findings, findings, (ex, ey))


def girder_lines(members: list[Member], tolerance: float = 5.0) -> list[list[Member]]:
    """The members joined end to end in line, each chain once."""
    chains: list[list[Member]] = []
    for m in members:
        joined = [c for c in chains if c[0].along_x == m.along_x and any(
            math.dist(e, f) <= tolerance for e in m.ends() for o in c for f in o.ends())]
        if not joined:
            chains.append([m])
            continue
        merged = [m] + [o for c in joined for o in c]
        chains = [c for c in chains if not any(c is j for j in joined)] + [merged]
    return chains


def rigidity_eccentricity(floor: Floor, framing: FloorFraming) -> tuple[float, float]:
    """Distance from the centre of rigidity of the columns and walls to the centre of
    the slab, as a share of the plan size along X and along Y.

    A column's stiffness in a direction is taken as its size across times its size
    along cubed; a wall's as its thickness times its length cubed along its line.
    """
    from shapely.ops import unary_union

    shapes = [bay.polygon for bay, _ in framing.choices]
    if not shapes:
        return 0.0, 0.0
    slab = unary_union(shapes)
    cx, cy = slab.centroid.x, slab.centroid.y
    x0, y0, x1, y1 = slab.bounds
    kx = [(s.size_y * s.size_x ** 3, s.y) for s in floor.supports]     # resists along X
    ky = [(s.size_x * s.size_y ** 3, s.x) for s in floor.supports]
    for w in floor.walls:
        length = math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        k = w.thickness * length ** 3
        if abs(w.x2 - w.x1) >= abs(w.y2 - w.y1):
            kx.append((k, (w.y1 + w.y2) / 2.0))
        else:
            ky.append((k, (w.x1 + w.x2) / 2.0))
    ry = sum(k * y for k, y in kx) / sum(k for k, _ in kx) if kx else cy
    rx = sum(k * x for k, x in ky) / sum(k for k, _ in ky) if ky else cx
    return abs(rx - cx) / max(x1 - x0, 1.0), abs(ry - cy) / max(y1 - y0, 1.0)


# =============================================================================
# CANDIDATES
# =============================================================================
@dataclass
class Candidate:
    """One framing of every floor: a slab thickness and a scheme."""

    thickness: float
    scheme: str
    floors: list[FloorFraming]
    checks: list[FrameCheck]

    @property
    def volume(self) -> float:
        return sum(f.volume * f.copies for f in self.floors)

    @property
    def formwork(self) -> float:
        return sum(f.formwork * f.copies for f in self.floors)

    @property
    def count(self) -> int:
        return sum(f.count * f.copies for f in self.floors)

    @property
    def problems(self) -> list[str]:
        return [f"{f.label}: {p}" for f in self.floors for p in f.problems]

    @property
    def frame_findings(self) -> list[str]:
        return [f"{f.label}: {x}" for f, c in zip(self.floors, self.checks) for x in c.findings]

    @property
    def valid(self) -> bool:
        return not self.problems

    @property
    def frames(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def name(self) -> str:
        return f"{self.thickness:.0f} mm slab, {SCHEME_NAMES[self.scheme]}"


def thicknesses_to_try(settings: FramingSettings) -> list[float]:
    if settings.hold == "thickness" and settings.held > 0:
        return [float(settings.held)]
    return sorted(float(t) for t in settings.thicknesses)


def candidates(floors: list[Floor], settings: FramingSettings, progress=None
               ) -> list[Candidate]:
    """Every thickness and scheme on every floor, ranked: framings without problems
    first, those meeting the frame action rules before the others, then by volume.
    Typical floors (``typical_groups``) are framed once."""
    say = progress or (lambda text: None)
    groups = typical_groups(floors)
    out = []
    for thickness in thicknesses_to_try(settings):
        for scheme in SCHEMES:
            say(f"Framing with a {thickness:.0f} mm slab, {SCHEME_NAMES[scheme]}")
            framed = [frame_floor(g[0], settings, thickness, scheme, [f.story for f in g])
                      for g in groups]
            out.append(Candidate(thickness, scheme, framed,
                                 [frame_action(g[0], x) for g, x in zip(groups, framed)]))
    unique: dict[tuple, Candidate] = {}
    for c in out:
        key = tuple(sorted((m.kind, round(m.x1), round(m.y1), round(m.x2), round(m.y2),
                            m.depth) for f in c.floors for m in f.members)) + (c.thickness,)
        unique.setdefault(key, c)
    return sorted(unique.values(), key=lambda c: (not c.valid, not c.frames, c.volume))


# =============================================================================
# FROM THE DRAWING
# =============================================================================
SAME_COLUMN = 100.0   # mm: a column above this close to one below sits on it


def plan_size(width: float, depth: float, angle: float) -> tuple[float, float]:
    """Extent along X and Y of a column ``depth`` along ``angle`` and ``width`` across."""
    a = math.radians(angle)
    return (abs(depth * math.cos(a)) + abs(width * math.sin(a)),
            abs(depth * math.sin(a)) + abs(width * math.cos(a)))


def floors_from_building(building, skip_ground: bool | None = None) -> list[Floor]:
    """One Floor for the level at the top of each story of a ``grid_column_model``
    Building, lowest first. The ground story a footing level adds (no plan of its
    own) is left out unless ``skip_ground`` is False."""
    names = [name for name, _ in building.stories]
    if skip_ground is None:
        skip_ground = building.base_elevation < 0 and names and names[0] not in building.floors
    out = []
    for index, story in enumerate(names):
        if skip_ground and index == 0:
            continue
        supports = []
        for c in building.columns:
            if c.story != story:
                continue
            sx, sy = plan_size(c.width, c.depth, 0.0 if c.circular else c.angle)
            supports.append(Support(c.x, c.y, sx, sy))
        above = names[index + 1] if index + 1 < len(names) else None
        planted = []
        for c in building.columns:
            if c.story != above:
                continue
            if any(math.dist((c.x, c.y), (s.x, s.y)) <= SAME_COLUMN for s in supports):
                continue
            sx, sy = plan_size(c.width, c.depth, 0.0 if c.circular else c.angle)
            planted.append(Support(c.x, c.y, sx, sy, f"the {above} column"))
        shapes = building.floors.get(story, {})
        out.append(Floor(
            story, supports,
            [Wall(w.x1, w.y1, w.x2, w.y2, w.thickness) for w in building.walls
             if w.story == story],
            outline=list(shapes.get("slab", [])), openings=list(shapes.get("opening", [])),
            zones=list(shapes.get("load", [])), planted=planted))
    return out


def signature(floor: Floor) -> tuple:
    """What makes two floors frame alike: supports, walls, slab, openings, zones and
    planted columns, rounded to the millimetre."""
    def pts(points):
        return tuple((round(x), round(y)) for x, y in points)

    return (tuple(sorted((round(s.x), round(s.y), round(s.size_x), round(s.size_y))
                         for s in floor.supports)),
            tuple(sorted((round(w.x1), round(w.y1), round(w.x2), round(w.y2),
                          round(w.thickness)) for w in floor.walls)),
            tuple(sorted(pts(p) for p in floor.outline)),
            tuple(sorted(pts(p) for p in floor.openings)),
            tuple(sorted((name, pts(p)) for name, p in floor.zones)),
            tuple(sorted((round(s.x), round(s.y)) for s in floor.planted)))


def typical_groups(floors: list[Floor]) -> list[list[Floor]]:
    """The floors grouped by ``signature`` (typical floors framed once), in the
    order of their lowest floor."""
    groups: dict[tuple, list[Floor]] = {}
    for floor in floors:
        groups.setdefault(signature(floor), []).append(floor)
    return list(groups.values())
