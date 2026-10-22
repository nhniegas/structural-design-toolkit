"""The framing of each floor: girders, beams and slabs from the columns and walls (``sdt model``).

Everything here works on plain data, in millimetres, with no ETABS:

* **Girders** join each column (or wall end) to its nearest neighbour along its
  framing directions, within ``FramingSettings.off_line`` of the line: global X
  and Y, or, when the drawing has grid lines, the directions of the grid lines
  through the column (its perpendicular too where only one passes), so girders
  run along the grids at any angle. A girder that meets a wall stops at it; one
  that would run along a wall, or cross a large void, is not made.
* **Bays** are the faces the girders, the walls and the slab edge close. Each has
  its own axes, along its girders. A bay with girders or walls all around is a
  panel; one open to the slab edge or to a void is framed with beams from its
  longest girder: cantilevers to the edge, with edge beams along the edge
  (each straight piece; arcs are read as pieces of about 2 m). A slab edge within
  ``SLAB_ONLY_OVERHANG`` of its girder cantilevers with no beams.
* **Openings** at least ``trimmer_from`` across that lie inside one bay get
  trimmer beams; larger voids that cross girders become slab edge.
* **The lowest floor without a slab** (``Floor.ties_only``) gets tie beams along
  the girder lines and no slab.
* **Each panel** is either a two-way slab (NSCP Table 408.3.1.2; ACI Table
  8.3.1.2) or a one-way slab (NSCP Table 407.3.1.1; ACI Table 7.3.1.1) on
  beams along its X or Y axis at equal spacing. Beam and girder depths follow NSCP
  Table 409.3.1.1 (ACI Table 9.3.1.1), with the factor (0.4 + fy / 700) of
  409.3.1.1.1 and 407.3.1.1.1 for fy other than 420 MPa, rounded up to the
  setup sizes; the width is the least setup width with b ≥ 0.3 h (NSCP
  418.6.2.1(b); ACI 18.6.2.1(b)).
* **A framing** holds one slab thickness for the floor. Each panel takes the
  option of least concrete that the thickness allows; neighbouring panels keep
  one beam direction where the volumes are close.
* **Frame action** is screened by rules: every column held by a girder along
  each of its framing directions, girder lines of at least two bays, no girder with a clear span under
  4d (NSCP 418.6.2.1(a); ACI 18.6.2.1(a)), the centre of rigidity near the
  centre of mass.

The thicknesses are the code minimums for deflection without calculation; the
sizes are rule sizes, which ``sdt design`` checks and resizes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

GIRDER, BEAM, CANTILEVER, EDGE, TRANSFER, TRIMMER, TIE = "G", "B", "CB", "EB", "TG", "TB", "FT"
KIND_NAMES = {GIRDER: "girder", BEAM: "beam", CANTILEVER: "cantilever beam",
              EDGE: "edge beam", TRANSFER: "transfer girder", TRIMMER: "trimmer beam",
              TIE: "tie beam"}
TWO_WAY, ONE_WAY_X, ONE_WAY_Y, OVERHANG = "two-way", "one-way X", "one-way Y", "overhang"
NEAR = 1.0                   # mm: points this close are the same
CLOSE_VOLUME = 0.05          # neighbours keep one beam direction within this share
ECCENTRICITY_LIMIT = 0.10    # centre of rigidity to centre of mass, share of the plan size
MIN_BEAM_SPACING = 1200.0    # mm: beams closer than this are not tried
TWO_WAY_RATIO = 2.0          # long / short span above this: one-way (NSCP 408.3.1.1)
COVER_TO_D = 65.0            # mm: d = h - this, for the 4d clear span rule
ANGLE_TOLERANCE = 5.0        # degrees: members this close in direction are in line
SLAB_ONLY_OVERHANG = 1000.0  # mm: a slab edge this close beyond its girder needs no beams
MIN_PIECE = 300.0            # mm: a beam cut shorter than this by its bay is not made
MIN_FRAME_GIRDER = 1500.0    # mm: a girder added for frame action is not made shorter
EDGE_SUPPORT_SPACING = 6000.0  # mm: cantilevers along an edge with no other support


def longest_span(depths: list[float], fy: float) -> float:
    """The longest member the deepest setup size allows, with no continuous end
    (NSCP Table 409.3.1.1, span / 16)."""
    return max(depths) / fy_factor(fy) * BEAM_RATIO[0] if depths else math.inf


def line_angle(dx: float, dy: float) -> float:
    """Direction of a line, degrees in [0, 180)."""
    return math.degrees(math.atan2(dy, dx)) % 180.0


def parallel(a: float, b: float, tolerance: float = ANGLE_TOLERANCE) -> bool:
    """Whether two directions (degrees) are the same within ``tolerance``."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d) <= tolerance


def axis_name(angle: float) -> str:
    """X, Y, or the angle of a direction, for the notes."""
    if parallel(angle, 0.0, 1.0):
        return "X"
    if parallel(angle, 90.0, 1.0):
        return "Y"
    return f"{angle % 180.0:.0f} degrees"


def _rotate(x: float, y: float, angle: float) -> tuple[float, float]:
    """A point turned by ``angle`` degrees about the origin."""
    if not angle:
        return x, y
    a = math.radians(angle)
    c, s_ = math.cos(a), math.sin(a)
    return x * c - y * s_, x * s_ + y * c


def _unit(angle: float) -> tuple[float, float]:
    """The unit vector of a direction, exact along X and Y."""
    ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return (0.0 if abs(ux) < 1e-12 else ux), (0.0 if abs(uy) < 1e-12 else uy)


def _segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


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
    grid_lines: list[tuple[float, float, float, float]] = field(default_factory=list)
    ties_only: bool = False      # the lowest floor with no slab: tie beams only
    _directions: dict = field(default_factory=dict, repr=False, compare=False)


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
    trimmer_from: float = 1000.0            # mm: openings this large get beams around them
    max_cantilever: float = 3000.0          # mm: longest cantilever beam from a column to an edge
    edge_span: float = 12000.0              # mm: an edge with no support over more than this ...
    long_cantilever: float = 8000.0         # mm: ... gets cantilever beams from its girder this long
    tie_widths: list[float] = field(default_factory=lambda: [300, 400, 500])
    tie_depths: list[float] = field(default_factory=lambda: [400, 500, 600, 700, 800])


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

    @property
    def angle(self) -> float:
        return line_angle(self.x2 - self.x1, self.y2 - self.y1)

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


def node_directions(x: float, y: float, floor: Floor, off_line: float = 300.0) -> list[float]:
    """The directions (degrees) a column or wall end at (x, y) frames along: X and Y
    with no grid lines; otherwise the directions of the grid lines within ``off_line``
    of it, its perpendicular added where only one passes, or those of the nearest
    grid line where none does."""
    if not floor.grid_lines:
        return [0.0, 90.0]
    key = (round(x), round(y), round(off_line))
    if key in floor._directions:
        return floor._directions[key]
    found, nearest = [], None
    for x1, y1, x2, y2 in floor.grid_lines:
        distance = _segment_distance(x, y, x1, y1, x2, y2)
        angle = line_angle(x2 - x1, y2 - y1)
        if distance <= off_line:
            found.append(angle)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, angle)
    if not found and nearest is not None:
        found = [nearest[1]]
    out: list[float] = []
    for angle in found:
        if not any(parallel(angle, b) for b in out):
            out.append(angle)
    if len(out) == 1:
        out.append((out[0] + 90.0) % 180.0)
    floor._directions[key] = out
    return out


def girders(floor: Floor, off_line: float, voids: list | None = None,
            max_span: float = math.inf) -> list[Member]:
    """A girder from each column or wall end to its nearest neighbour along each of its
    framing directions (``node_directions``; with no grid lines, along +X and +Y).

    A girder that crosses a wall stops at it; one that runs along a wall, whose
    middle is off the slab, that crosses one of ``voids`` (shapely polygons), or that
    is longer than ``max_span``, is not made.
    """
    from shapely.geometry import LineString, Point

    nodes = [(s.x, s.y) for s in floor.supports] + wall_nodes(floor.walls)
    walls = _wall_lines(floor.walls)
    slab = floor_polygon(floor)
    reach = slab.buffer(off_line) if slab is not None else None
    signs = (1.0, -1.0) if floor.grid_lines else (1.0,)
    found: dict[frozenset, Member] = {}
    for x, y in nodes:
        start = Point(x, y)
        for angle in node_directions(x, y, floor, off_line):
            ux, uy = _unit(angle)
            for sign in signs:
                best = None
                for nx, ny in nodes:
                    dx, dy = nx - x, ny - y
                    along = sign * (dx * ux + dy * uy)
                    if along <= NEAR or abs(dy * ux - dx * uy) > off_line:
                        continue
                    if best is None or along < best[0]:
                        best = (along, (nx, ny))
                if best is None:
                    continue
                end = best[1]
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
                if end is None or not NEAR <= math.dist((x, y), end) <= max_span:
                    continue
                middle = Point((x + end[0]) / 2.0, (y + end[1]) / 2.0)
                if reach is not None and not reach.contains(middle):
                    continue
                if voids and any(LineString([(x, y), end]).intersects(v) for v in voids):
                    continue
                key = frozenset({_key((x, y)), _key(end)})
                found.setdefault(key, Member(GIRDER, x, y, end[0], end[1]))
    return list(found.values())


FOR_FRAME_ACTION = "for frame action"


def frame_girders(floor: Floor, members: list[Member], tolerance: float = 50.0,
                  off_line: float = 300.0, voids: list | None = None,
                  max_span: float = math.inf) -> list[Member]:
    """Girders added so every column is held along each of its framing directions:
    from a column with no girder along a direction to the nearest member or wall it
    meets that way, on each side (a column off every line, such as one standing on
    a transfer girder below). None crosses one of ``voids``."""
    from shapely.geometry import LineString, Point

    lines = [LineString(m.ends()) for m in members] + _wall_lines(floor.walls)
    slab = floor_polygon(floor)
    reach = 1e6 if slab is None else max(slab.bounds[2] - slab.bounds[0],
                                         slab.bounds[3] - slab.bounds[1])
    out = []
    for s in floor.supports:
        here = Point(s.x, s.y)
        for angle in node_directions(s.x, s.y, floor, off_line):
            if any(parallel(m.angle, angle) and min(math.dist((s.x, s.y), e) for e in m.ends())
                   <= tolerance for m in members + out):
                continue
            ux, uy = _unit(angle)
            for sign in (1.0, -1.0):
                far = (s.x + sign * reach * ux, s.y + sign * reach * uy)
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
                if not MIN_FRAME_GIRDER <= end.distance(here) <= max_span:
                    continue
                new = LineString([(s.x, s.y), (end.x, end.y)])
                if slab is not None and not slab.buffer(tolerance).contains(
                        new.interpolate(0.5, normalized=True)):
                    continue
                if voids and any(new.intersects(v) for v in voids):
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
            if any(j != i and parallel(other.angle, m.angle)
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
    angle: float = 0.0              # degrees: the bay's own X axis, along its girders

    @property
    def local(self):
        """The bay turned onto its own axes (about the origin)."""
        if not self.angle:
            return self.polygon
        if "_local" not in self.__dict__:
            from shapely import affinity

            self.__dict__["_local"] = affinity.rotate(self.polygon, -self.angle, origin=(0, 0))
        return self.__dict__["_local"]

    def to_global(self, x: float, y: float) -> tuple[float, float]:
        return _rotate(x, y, self.angle)

    @property
    def lx(self) -> float:
        x0, _, x1, _ = self.local.bounds
        return x1 - x0

    @property
    def ly(self) -> float:
        _, y0, _, y1 = self.local.bounds
        return y1 - y0

    def continuous(self, along_x: bool) -> int:
        """Sides normal to X (along_x) or to Y with a panel beyond them: 0, 1 or 2."""
        keys = ("W", "E") if along_x else ("S", "N")
        return sum(1 for k in keys if self.sides.get(k))


def _bay_angle(face, members: list[Member], lines: list, tree, tolerance: float) -> float:
    """The axis of a bay: the mean direction of the members along its sides (each by
    the length it shares), within 90 degrees; 0 when within ANGLE_TOLERANCE of X or Y."""
    if tree is None:
        return 0.0
    ring = face.exterior.buffer(tolerance)
    sx = sy = 0.0
    for i in tree.query(ring):
        shared = lines[i].intersection(ring).length
        if shared <= 10 * tolerance:
            continue
        a = math.radians(4.0 * (members[i].angle % 90.0))
        sx += shared * math.cos(a)
        sy += shared * math.sin(a)
    if abs(sx) + abs(sy) < 1e-9:
        return 0.0
    angle = (math.degrees(math.atan2(sy, sx)) / 4.0) % 90.0
    return 0.0 if parallel(angle, 0.0) or parallel(angle, 90.0) else angle


def bays(floor: Floor, members: list[Member], tolerance: float = 50.0, slab=None) -> list[Bay]:
    """The faces closed by the members, the walls and the slab edge, on the slab
    (``slab``: the slab polygon to use, with its voids; the floor's outline when
    None). Each bay takes the axis of its girders."""
    from shapely import affinity
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    from shapely.strtree import STRtree

    member_lines = [LineString(m.ends()) for m in members]
    lines = member_lines + _wall_lines(floor.walls)
    supports = unary_union(lines).buffer(tolerance) if lines else None
    slab = floor_polygon(floor) if slab is None else slab
    edge = slab.boundary if slab is not None else None
    if edge is not None:
        lines.append(edge)
    faces = [f for f in polygonize(unary_union(lines)) if f.area > 1e4]
    if slab is not None:
        faces = [f for f in faces if slab.buffer(NEAR).contains(f.representative_point())]
    tree = STRtree(member_lines) if member_lines else None
    out = []
    for face in faces:
        ring = face.exterior
        open_length = ring.difference(supports).length if supports is not None else ring.length
        touches = edge is not None and ring.intersection(edge.buffer(tolerance)).length > tolerance
        out.append(Bay(face, panel=open_length <= tolerance * 2, exterior=bool(touches),
                       angle=_bay_angle(face, members, member_lines, tree, tolerance)))
    shapes = STRtree([b.polygon for b in out]) if out else None
    for bay in out:
        x0, y0, x1, y1 = bay.local.bounds
        for j in shapes.query(bay.polygon.buffer(NEAR * 2)):
            other = out[int(j)]
            if other is bay or not other.panel:
                continue
            shared = bay.polygon.buffer(NEAR * 2).intersection(other.polygon.buffer(NEAR * 2))
            if shared.is_empty:
                continue
            if bay.angle:
                shared = affinity.rotate(shared, -bay.angle, origin=(0, 0))
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
    x0, y0, x1, y1 = bay.local.bounds
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
                        beams += bay_member(bay, x0, y, x1, y, section)
                    else:
                        x = x0 + k * spacing
                        beams += bay_member(bay, x, y0, x, y1, section)
            out.append(Option(kind, need, beams, spacing))
    return out


def _is_box(polygon) -> bool:
    return abs(polygon.envelope.area - polygon.area) <= 1e-6 * max(polygon.area, 1.0)


def bay_member(bay: Bay, x1: float, y1: float, x2: float, y2: float,
               section: tuple[float, float] = (0.0, 0.0), kind: str = BEAM) -> list[Member]:
    """A member drawn on the bay's own axes, in global coordinates, cut to the bay
    (a bay that is not a rectangle on its axes); pieces under MIN_PIECE are dropped."""
    a, b = bay.to_global(x1, y1), bay.to_global(x2, y2)
    if not bay.angle and _is_box(bay.polygon):
        return [Member(kind, a[0], a[1], b[0], b[1], *section)]
    from shapely.geometry import LineString

    cut = LineString([a, b]).intersection(bay.polygon)
    out = []
    for piece in getattr(cut, "geoms", [cut]):
        if piece.geom_type == "LineString" and piece.length > MIN_PIECE:
            (px, py), (qx, qy) = piece.coords[0], piece.coords[-1]
            out.append(Member(kind, px, py, qx, qy, *section))
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
                     spacing: float, tolerance: float = 50.0, walls: list[Wall] | None = None,
                     max_reach: float = math.inf) -> list[Member] | None:
    """Beams for a bay open to the slab edge (or to a void).

    They leave the longest girder (or wall) side of the bay at right angles, at each
    column on that line and between them at no more than ``spacing``. Each runs to
    the first thing it meets: a member (a beam) or the slab edge (a cantilever beam).
    Edge beams follow the open edge, each straight piece of it, from tip to tip and
    to the girders it reaches. None when the edge is within SLAB_ONLY_OVERHANG of the
    girder: the slab cantilevers with no beams. An empty list when there is no girder
    to start from, or the edge is farther than ``max_reach`` from it.
    """
    from shapely import affinity
    from shapely.geometry import LineString, Point
    from shapely.ops import nearest_points, unary_union

    ring = bay.polygon.exterior
    sides = []
    for m in members:
        shared = ring.intersection(LineString(m.ends()).buffer(tolerance))
        if not shared.is_empty and shared.length > 10 * tolerance:
            sides.append((shared.length, m))
    if not sides:
        return []
    _, base = max(sides, key=lambda s: s[0])
    angle = base.angle

    def local(geom):
        return affinity.rotate(geom, -angle, origin=(0, 0)) if angle else geom

    def back(x: float, y: float) -> tuple[float, float]:
        return _rotate(x, y, angle)

    poly = local(bay.polygon)
    yb = sum(_rotate(x, y, -angle)[1] for x, y in base.ends()) / 2.0
    line = []
    for _, m in sides:
        if not parallel(m.angle, angle):
            continue
        (ax, ay), (cx, cy) = (_rotate(x, y, -angle) for x, y in m.ends())
        if abs((ay + cy) / 2.0 - yb) <= tolerance:
            line.append((min(ax, cx), max(ax, cx)))
    x0, y0, x1, y1 = poly.bounds
    sign = 1.0 if poly.centroid.y > yb else -1.0
    reach = (y1 - yb) if sign > 0 else (yb - y0)
    if reach <= SLAB_ONLY_OVERHANG + tolerance:
        return None
    if reach > max_reach + tolerance:
        return []
    low, high = max(x0, min(a for a, _ in line)), min(x1, max(b for _, b in line))
    columns = []
    for s in supports:
        sx, sy = _rotate(s.x, s.y, -angle)
        if abs(sy - yb) <= tolerance:
            columns.append(sx)
    support_lines = unary_union([local(LineString(m.ends())) for m in members]
                                + [local(w) for w in _wall_lines(walls or [])])
    held = support_lines.buffer(tolerance)
    edge = poly.boundary              # the outer ring and any ring inside the bay
    out, tips = [], []
    for x in _stations(low, high, spacing, columns):
        ray = LineString([(x, yb), (x, yb + sign * (reach + 10 * tolerance))])
        cut = ray.intersection(edge)
        points = []
        for g in getattr(cut, "geoms", [cut]):     # a ray along a side meets it in a line
            if g.geom_type == "Point":
                points.append(g)
            elif g.geom_type == "LineString":
                points += [Point(c) for c in g.coords]
        points = [p for p in points if abs(p.y - yb) > 2 * tolerance]
        if not points:
            continue
        hit = min(points, key=lambda p: abs(p.y - yb))
        if held.contains(hit):
            out.append(Member(BEAM, x, yb, hit.x, hit.y))
        else:
            out.append(Member(CANTILEVER, x, yb, hit.x, hit.y))
            tips.append(hit)
    rays = [LineString(m.ends()) for m in out if m.kind == CANTILEVER]
    free = edge.difference(held)
    for part in getattr(free, "geoms", [free]):
        if part.geom_type != "LineString" or part.length <= 2 * tolerance:
            continue
        coords = list(part.coords)
        for index in (0, -1):            # back onto the girder it was cut from
            end = Point(coords[index])
            if not support_lines.is_empty and support_lines.distance(end) <= 2 * tolerance:
                snapped = nearest_points(support_lines, end)[0]
                coords[index] = (snapped.x, snapped.y)
        path = LineString(coords)
        stops = sorted({path.project(Point(c)) for c in coords}
                       | {path.project(t) for t in tips if path.distance(t) <= tolerance})
        points = [path.interpolate(d) for d in stops]
        for a, b in zip(points, points[1:]):
            if a.distance(b) <= NEAR:
                continue
            if any(r.distance(a) <= tolerance and r.distance(b) <= tolerance for r in rays):
                continue                 # along a cantilever: the cantilever is there
            out.append(Member(EDGE, a.x, a.y, b.x, b.y))
    result = []
    for m in out:
        (ax, ay), (bx, by) = back(m.x1, m.y1), back(m.x2, m.y2)
        result.append(replace(m, x1=ax, y1=ay, x2=bx, y2=by))
    return result


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


def trimmers(floor: Floor, panels: list[Bay], settings: FramingSettings,
             openings: list | None = None) -> list[Member]:
    """Trimmer beams around each opening of ``trimmer_from`` or more (``openings``;
    the floor's when None): along its sides parallel to the short span of its bay,
    from girder to girder, and headers between them along the other two sides, on
    the bay's own axes."""
    from shapely import affinity
    from shapely.geometry import Polygon

    out = []
    for points in (floor.openings if openings is None else openings):
        hole = Polygon(points)
        ox0, oy0, ox1, oy1 = hole.bounds
        if max(ox1 - ox0, oy1 - oy0) < settings.trimmer_from:
            continue
        bay = next((b for b in panels if b.polygon.intersects(hole)), None)
        if bay is None:
            continue
        if bay.angle:
            ox0, oy0, ox1, oy1 = affinity.rotate(hole, -bay.angle, origin=(0, 0)).bounds
        x0, y0, x1, y1 = bay.local.bounds
        if bay.lx <= bay.ly:   # trimmers along X, girder to girder; headers along Y
            sides = [(x0, oy0, x1, oy0), (x0, oy1, x1, oy1), (ox0, oy0, ox0, oy1),
                     (ox1, oy0, ox1, oy1)]
        else:
            sides = [(ox0, y0, ox0, y1), (ox1, y0, ox1, y1), (ox0, oy0, ox1, oy0),
                     (ox0, oy1, ox1, oy1)]
        for x_1, y_1, x_2, y_2 in sides:
            out += bay_member(bay, x_1, y_1, x_2, y_2, kind=TRIMMER)
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


_SKELETONS: dict = {}


def large_openings(floor: Floor, settings: FramingSettings, lines: list[Member],
                   tolerance: float) -> tuple[list, list]:
    """The openings of ``trimmer_from`` or more: (voids, inside), voids crossing a girder
    of ``lines`` (shapely polygons, become slab edge), the others (point lists, get
    trimmer beams)."""
    from shapely.geometry import LineString, Polygon

    voids, inside = [], []
    for points in floor.openings:
        if len(points) < 3:
            continue
        hole = Polygon(points).buffer(0)
        ox0, oy0, ox1, oy1 = hole.bounds
        if max(ox1 - ox0, oy1 - oy0) < settings.trimmer_from:
            continue
        core = hole.buffer(-tolerance)
        if not core.is_empty and any(LineString(m.ends()).intersects(core) for m in lines):
            voids.append(hole)
        else:
            inside.append(points)
    return voids, inside


def slab_shape(floor: Floor, voids: list):
    """The slab outline without the voids (None with no outline)."""
    slab = floor_polygon(floor)
    if slab is None or not voids:
        return slab
    from shapely.ops import unary_union

    return slab.difference(unary_union(voids))


def skeleton(floor: Floor, settings: FramingSettings):
    """What every thickness and scheme of a floor share: the girders, transfer girders
    and trimmers, sized, the bays to frame, the notes and the problems. Worked out
    once per floor and settings."""
    from shapely.geometry import Polygon

    key = (id(floor), settings.off_line, settings.trimmer_from, settings.fy,
           tuple(settings.girder_depths), tuple(settings.girder_widths))
    cached = _SKELETONS.get(key)
    if cached is not None and cached[0] is floor:
        return cached[1]
    tolerance = max(settings.off_line, 50.0)
    span = longest_span(settings.girder_depths, settings.fy)
    lines = girders(floor, settings.off_line, max_span=span)
    voids, inside = large_openings(floor, settings, lines, tolerance)
    cores = [v.buffer(-SLAB_ONLY_OVERHANG) for v in voids]   # a girder along the edge stays
    cores = [c for c in cores if not c.is_empty]
    if cores:
        lines = girders(floor, settings.off_line, cores, span)
    added = frame_girders(floor, lines, off_line=settings.off_line, voids=cores, max_span=span)
    lines += added
    slab = slab_shape(floor, voids)
    first = bays(floor, lines, tolerance, slab=slab)
    lines, notes = transfer_girders(floor, lines, [b for b in first if b.panel], tolerance)
    if added:
        notes.append(f"{len(added)} girders added so that every column is held along each of "
                     "its framing directions (frame action)")
    if voids:
        notes.append(f"{len(voids)} voids cross the girders: their edges are slab edge, with "
                     "edge beams")
    lines += trimmers(floor, [b for b in first if b.panel], settings, inside)
    stubs, stub_notes = edge_stubs(floor, lines, slab, settings)
    lines += stubs
    long_stubs, long_notes = long_edge_supports(slab, lines, floor.supports, settings)
    lines += long_stubs
    stubs += long_stubs
    stub_notes += long_notes
    edges = edge_members(slab, lines, floor.supports, max_piece=2 * settings.edge_span)
    lines += edges
    notes += stub_notes
    if edges:
        notes.append(f"{len(edges)} edge beams along the slab edge and the voids, "
                     f"{len(stubs)} cantilever beams out to them")
    problems: list[str] = []
    framed = size_members(lines, settings.girder_depths, settings.girder_widths, settings.fy,
                          problems)
    holes = [Polygon(p) for p in floor.openings if len(p) >= 3]
    panels = [b for b in bays(floor, framed, tolerance, slab=slab)
              if not any(h.contains(b.polygon.representative_point()) for h in holes)]
    result = (framed, panels, notes, problems)
    _SKELETONS[key] = (floor, result)
    return result


def _points_of(geometry) -> list:
    out = []
    for g in getattr(geometry, "geoms", [geometry]):
        if g.geom_type == "Point":
            out.append(g)
        elif g.geom_type in ("LineString", "LinearRing"):
            from shapely.geometry import Point

            out += [Point(c) for c in g.coords]
    return out


def edge_stubs(floor: Floor, members: list[Member], slab, settings: FramingSettings
               ) -> tuple[list[Member], list[str]]:
    """Cantilever beams from the columns out to the slab edge (or a void's edge) along
    their framing directions, where the edge is the first thing met, more than
    SLAB_ONLY_OVERHANG and at most ``max_cantilever`` away. Edge farther than that
    from every column is named in the notes."""
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    if slab is None:
        return [], []
    boundary = slab.boundary
    corners = [c for g in getattr(boundary, "geoms", [boundary]) for c in g.coords]
    lines = unary_union([LineString(m.ends()) for m in members] + _wall_lines(floor.walls))
    far = max(slab.bounds[2] - slab.bounds[0], slab.bounds[3] - slab.bounds[1])
    out, farther = [], 0
    for s in floor.supports:
        here = Point(s.x, s.y)
        if not slab.buffer(NEAR).contains(here):
            continue
        for angle in node_directions(s.x, s.y, floor, settings.off_line):
            ux, uy = _unit(angle)
            for sign in (1.0, -1.0):
                ray = LineString([(s.x, s.y), (s.x + sign * far * ux, s.y + sign * far * uy)])
                edge = [p for p in _points_of(ray.intersection(boundary)) if p.distance(here) > NEAR]
                if not edge:
                    continue
                hit = min(edge, key=lambda p: p.distance(here))
                member = [p for p in _points_of(ray.intersection(lines))
                          if p.distance(here) > 2 * NEAR]
                if member and min(p.distance(here) for p in member) < hit.distance(here) - NEAR:
                    continue                      # a member is met first
                length = hit.distance(here)
                if length <= SLAB_ONLY_OVERHANG:
                    continue                      # the slab cantilevers on its own
                middle = LineString([(s.x, s.y), (hit.x, hit.y)]).interpolate(0.5, normalized=True)
                if boundary.distance(middle) <= 50.0 and not any(
                        hit.distance(Point(c)) <= 50.0 for c in corners):
                    continue                      # along the edge, not to a corner
                if length > settings.max_cantilever:
                    farther += 1
                    continue
                out.append(Member(CANTILEVER, s.x, s.y, hit.x, hit.y))
    notes = [f"{farther} slab edges farther than {settings.max_cantilever / 1000:g} m from the "
             "column facing them: no cantilever beam there"] if farther else []
    return out, notes


def _edge_pieces(slab, members: list[Member], supports: list[Support], tolerance: float):
    """(a, b, kept, a_from, b_from) for each straight piece of the slab and void edges,
    split where a member or a column reaches the edge, or stands within
    SLAB_ONLY_OVERHANG of it (``*_from``: that member end or column, when it is off the
    edge). kept is False for a piece along a member, or within SLAB_ONLY_OVERHANG of a
    member in line with it (the slab overhangs that member a little)."""
    from shapely.geometry import LineString, Point

    member_lines = [(LineString(m.ends()), m) for m in members]
    marks = [Point(e) for m in members for e in m.ends()] + [Point(s.x, s.y) for s in supports]
    near = SLAB_ONLY_OVERHANG + tolerance
    for polygon in getattr(slab, "geoms", [slab]):
        for ring in [polygon.exterior] + list(polygon.interiors):
            ring_line = LineString(ring.coords)
            stops = {ring_line.project(Point(c)): None for c in ring.coords}
            stops[ring_line.length] = None        # the ring closes on its first point
            for p in marks:
                gap = ring_line.distance(p)
                if gap <= near:
                    d = ring_line.project(p)
                    if gap > tolerance and stops.get(d) is None:
                        stops[d] = p
                    else:
                        stops.setdefault(d, None)
            order = sorted(stops)
            points = [(ring_line.interpolate(d), stops[d]) for d in order]
            for (a, a_from), (b, b_from) in zip(points, points[1:]):
                if a.distance(b) <= 10 * tolerance:
                    continue
                piece = LineString([a, b])
                angle = line_angle(b.x - a.x, b.y - a.y)
                kept = True
                for line, m in member_lines:
                    if not parallel(m.angle, angle):
                        continue
                    if line.distance(a) <= tolerance and line.distance(b) <= tolerance:
                        kept = False      # a member runs along this edge
                        break
                    if (line.distance(a) <= near and line.distance(b) <= near
                            and line.length >= 0.5 * piece.length):
                        kept = False      # the slab overhangs a girder a little
                        break
                yield a, b, kept, a_from, b_from


def long_edge_supports(slab, members: list[Member], supports: list[Support],
                       settings: FramingSettings, tolerance: float = 50.0
                       ) -> tuple[list[Member], list[str]]:
    """Cantilever beams for the edge pieces with no support over more than
    ``edge_span``: at about EDGE_SUPPORT_SPACING along the piece, each square to the
    edge from the first member met inside the slab, at most ``long_cantilever`` long."""
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    if slab is None:
        return [], []
    lines = unary_union([LineString(m.ends()) for m in members])
    out, missing = [], 0
    for a, b, kept, _, _ in _edge_pieces(slab, members, supports, tolerance):
        length = a.distance(b)
        if not kept or length <= settings.edge_span:
            continue
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        mid = Point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        nx, ny = -uy, ux                         # the normal into the slab
        if not slab.contains(Point(mid.x + nx * 200.0, mid.y + ny * 200.0)):
            nx, ny = uy, -ux
        held = [0.0, length]
        # first the columns standing within long_cantilever of the edge, square to it
        for s_ in supports:
            t = (s_.x - a.x) * ux + (s_.y - a.y) * uy
            depth = (s_.x - a.x) * nx + (s_.y - a.y) * ny
            if not (0.0 < t < length and SLAB_ONLY_OVERHANG < depth <= settings.long_cantilever):
                continue
            foot = (a.x + ux * t, a.y + uy * t)
            link = LineString([(s_.x, s_.y), foot])
            crossing = [p for p in _points_of(link.intersection(lines))
                        if p.distance(Point(s_.x, s_.y)) > 2 * tolerance]
            if crossing:
                continue                          # a member lies between: it carries the edge
            out.append(Member(CANTILEVER, s_.x, s_.y, foot[0], foot[1]))
            held.append(t)
        # then the stretches still longer than edge_span, from the first member met
        held.sort()
        for t0, t1 in zip(held, held[1:]):
            gap = t1 - t0
            if gap <= settings.edge_span:
                continue
            count = max(2, math.ceil(gap / EDGE_SUPPORT_SPACING))
            for k in range(1, count):
                t = t0 + gap * k / count
                px, py = a.x + ux * t, a.y + uy * t
                ray = LineString([(px, py), (px + nx * settings.long_cantilever,
                                             py + ny * settings.long_cantilever)])
                hits = [p for p in _points_of(ray.intersection(lines))
                        if p.distance(Point(px, py)) > SLAB_ONLY_OVERHANG]
                if not hits:
                    missing += 1
                    continue
                root = min(hits, key=lambda p: p.distance(Point(px, py)))
                out.append(Member(CANTILEVER, root.x, root.y, px, py))
    notes = [f"{missing} points of long slab edges have no girder within "
             f"{settings.long_cantilever / 1000:g} m to cantilever from"] if missing else []
    return out, notes


def edge_members(slab, members: list[Member], supports: list[Support],
                 tolerance: float = 50.0, max_piece: float = math.inf) -> list[Member]:
    """Edge beams along the slab edge and the voids' edges: each straight piece of the
    outline, split where a member or a column reaches it. A piece along a member, or
    within SLAB_ONLY_OVERHANG of a member in line with it (a short slab overhang), or
    longer than ``max_piece`` (no support along it: the bay behind it is framed with
    cantilever beams from its girder, ``overhang_members``), is not made."""
    if slab is None:
        return []
    out, links = [], {}
    for a, b, kept, a_from, b_from in _edge_pieces(slab, members, supports, tolerance):
        if not kept or a.distance(b) > max_piece:
            continue
        out.append(Member(EDGE, a.x, a.y, b.x, b.y))
        for point, source in ((a, a_from), (b, b_from)):
            if source is not None:        # a short link from the column or girder end
                links[_key((point.x, point.y))] = Member(CANTILEVER, source.x, source.y,
                                                         point.x, point.y)
    return out + list(links.values())


def frame_ties(floor: Floor, settings: FramingSettings, out: FloorFraming) -> FloorFraming:
    """The lowest floor with no slab: tie beams along the girder lines (FTB sizes)."""
    depths = settings.tie_depths or settings.girder_depths
    span = longest_span(depths, settings.fy)
    lines = girders(floor, settings.off_line, max_span=span)
    lines += frame_girders(floor, lines, off_line=settings.off_line, max_span=span)
    ties = [replace(m, kind=TIE, note="") for m in lines]
    out.members = size_members(ties, settings.tie_depths or settings.girder_depths,
                               settings.tie_widths or settings.girder_widths, settings.fy,
                               out.problems)
    out.volume = sum(m.width * m.depth * m.length for m in out.members) / 1e9
    out.formwork = sum(2 * m.depth * m.length for m in out.members) / 1e6
    out.notes.append(f"no slab: {len(out.members)} tie beams along the girder lines")
    return out


def frame_floor(floor: Floor, settings: FramingSettings, thickness: float,
                scheme: str = "least", stories: list[str] | None = None) -> FloorFraming:
    """Frame one floor holding the slab ``thickness``: girders, transfer girders and
    trimmers, then each panel by ``scheme`` ("least", "X" or "Y", on each bay's own
    axes), then the bays open to the slab edge or a void. Problems name what the
    thickness or the setup sizes cannot do. ``stories``: every floor framed alike
    (a typical floor), for the depth limits. A floor with ``ties_only`` gets tie
    beams and no slab."""
    out = FloorFraming(floor.story, thickness, scheme, stories=list(stories or [floor.story]))
    if floor.ties_only:
        return frame_ties(floor, settings, out)
    framed, panels, notes, problems = skeleton(floor, settings)
    out.notes, out.problems = list(notes), list(problems)
    girder = max(((m.width, m.depth) for m in framed if m.kind == GIRDER),
                 default=(settings.girder_widths[0], settings.girder_depths[0]))
    prefer = SCHEMES.get(scheme)
    for bay in panels:
        if not bay.panel:
            continue
        options = panel_options(bay, girder, settings, thickness, out.problems)
        chosen = best_option(options, thickness, settings, prefer, strict=prefer is not None)
        if chosen is None:
            c = bay.polygon.representative_point()
            out.problems.append(f"a bay of {bay.lx / 1000:.2f} x {bay.ly / 1000:.2f} m at "
                                f"({c.x / 1000:.1f}, {c.y / 1000:.1f}) m needs more than "
                                f"{thickness:.0f} mm of slab: its slab is drawn with no beams")
            out.choices.append((bay, Option(TWO_WAY, thickness, [], max(bay.lx, bay.ly))))
            continue
        out.choices.append((bay, chosen))
    if scheme == "least":
        _keep_directions(out, settings, thickness, girder)
    beams = [b for _, option in out.choices for b in option.beams]
    slab_only = 0
    for bay in panels:
        if bay.panel:
            continue
        spacing = thickness * SLAB_RATIO[1] / fy_factor(settings.fy)
        extra = overhang_members(bay, framed, floor.supports, spacing, 50.0, floor.walls,
                                 settings.long_cantilever)
        if extra is None:
            out.choices.append((bay, Option(OVERHANG, 0.0, [], spacing)))
            slab_only += 1
            continue
        if not extra:
            c = bay.polygon.representative_point()
            out.problems.append(f"an overhang of {bay.polygon.area / 1e6:.1f} m2 at "
                                f"({c.x / 1000:.1f}, {c.y / 1000:.1f}) m has no girder to "
                                f"cantilever from within {settings.long_cantilever / 1000:g} "
                                "m: its slab is drawn with no beams")
            out.choices.append((bay, Option(OVERHANG, 0.0, [], spacing)))
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
    if slab_only:
        out.notes.append(f"{slab_only} slab edges within {SLAB_ONLY_OVERHANG / 1000:g} m of their "
                         "girder cantilever with no beams")
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
    if floor.ties_only:
        return FrameCheck(True, [])
    lateral = [m for m in framing.members if m.kind in (GIRDER, TRANSFER)]
    for s in floor.supports:
        ends = [m for m in lateral
                if any(math.dist((s.x, s.y), e) <= tolerance for e in m.ends())]
        missing = [axis_name(a) for a in node_directions(s.x, s.y, floor)
                   if not any(parallel(m.angle, a) for m in ends)]
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
        joined = [c for c in chains if parallel(c[0].angle, m.angle) and any(
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
    _SKELETONS.clear()
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


def floors_from_building(building, skip_ground: bool | None = None,
                         ground_ties: bool = False) -> list[Floor]:
    """One Floor for the level at the top of each story of a ``grid_column_model``
    Building, lowest first. The ground story a footing level adds (no plan of its
    own) is left out unless ``skip_ground`` is False. With ``ground_ties`` the lowest
    floor, when it has no slab outline, gets tie beams only (``Floor.ties_only``).
    The grid lines of the drawing set the framing directions."""
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
            zones=list(shapes.get("load", [])), planted=planted,
            grid_lines=[(g.x1, g.y1, g.x2, g.y2) for g in getattr(building, "grids", [])],
            ties_only=bool(ground_ties and not out and not shapes.get("slab"))))
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
