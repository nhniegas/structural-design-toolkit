"""Geometric tributary areas of beams and columns, for the NSCP live load reduction.

The tributary area of NSCP 2015 205.5 is the floor area a member supports by
geometry: the floor closer to it than to its neighbours (the halfway lines).
It does not depend on member sizes, so it stays the same while members are
resized.

* Columns: on each floor level, every point of the floor belongs to the
  nearest column whose top is at that level. A column's area is its share on
  its own level plus the area of the column standing on it.
* Beams: every point of the floor belongs to the nearest beam at that level.
  A beam that ends on another member, not on a column, passes half of its
  area to that end; the members that continue straight through the joint
  (the girder) share it.

Floors are sampled on a grid (200 mm by default), so areas are exact to
about the grid size times the floor perimeter.

Each floor also has a reducible live intensity (kPa): the uniform loads of
the Reducible Live patterns on it, from load sets and from direct uniform
loads. A member supporting more than ``HEAVY_TOLERANCE_M2`` of floor above
4.8 kPa takes the NSCP rule for heavy live loads.

Coordinates are in mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

HEAVY_KPA = 4.8
HEAVY_TOLERANCE_M2 = 0.5
LEVEL_TOLERANCE = 10.0  # mm
MAX_POINTS_PER_LEVEL = 400_000


@dataclass
class Floor:
    """A floor area object: its outline at one elevation and its reducible live load."""

    name: str
    z: float
    outline: list[tuple[float, float]]
    reducible_kpa: float = 0.0


@dataclass
class Frame:
    """A beam or column: its end joints and their coordinates."""

    name: str
    joint_i: str
    joint_j: str
    i: tuple[float, float, float]
    j: tuple[float, float, float]
    depth: float = 0.0


@dataclass
class Tributary:
    """The floor a member supports."""

    area_m2: float = 0.0
    load_kn: float = 0.0        # reducible live load on that area (kPa x m2)
    heavy_m2: float = 0.0       # part of the area with reducible live above 4.8 kPa
    levels: int = 0             # floor levels contributing (columns)
    own_m2: float = 0.0         # the member's own share, before what it receives
    sources: set = field(default_factory=set)  # floors (names) with heavy live

    @property
    def reducible_kpa(self) -> float:
        return self.load_kn / self.area_m2 if self.area_m2 > 0 else 0.0

    def add(self, other: "Tributary", fraction: float = 1.0) -> None:
        self.area_m2 += fraction * other.area_m2
        self.load_kn += fraction * other.load_kn
        self.heavy_m2 += fraction * other.heavy_m2
        self.sources |= other.sources


# =============================================================================
# SAMPLING
# =============================================================================
def _sample(floors: list[Floor], spacing: float):
    """Grid points inside the floors: coordinates, cell area (m2), intensity, floor index."""
    from shapely import contains_xy
    from shapely.geometry import Polygon

    polygons = [Polygon(f.outline) for f in floors]
    polygons = [p if p.is_valid else p.buffer(0) for p in polygons]
    x0, y0, x1, y1 = np.array([p.bounds for p in polygons]).T
    x0, y0, x1, y1 = x0.min(), y0.min(), x1.max(), y1.max()
    total = sum(p.area for p in polygons)
    spacing = max(spacing, math.sqrt(total / MAX_POINTS_PER_LEVEL))
    xs = np.arange(x0 + spacing / 2, x1, spacing)
    ys = np.arange(y0 + spacing / 2, y1, spacing)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    which = np.full(gx.shape, -1)
    for index, polygon in enumerate(polygons):
        # grown by 1 mm so points on shared edges count, once (first floor wins)
        inside = contains_xy(polygon.buffer(1.0), gx, gy) & (which < 0)
        which[inside] = index
    keep = which >= 0
    points = np.column_stack([gx[keep], gy[keep]])
    which = which[keep]
    kpa = np.array([floors[k].reducible_kpa for k in which]) if len(which) else np.zeros(0)
    return points, spacing * spacing / 1e6, kpa, which


def _nearest(points: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Index of the nearest segment (a point is a segment of zero length) to each point."""
    best = np.full(len(points), -1)
    best_d = np.full(len(points), np.inf)
    for k, (a, b) in enumerate(zip(starts, ends)):
        ab = b - a
        length2 = float(ab @ ab)
        if length2 > 0:
            t = np.clip(((points - a) @ ab) / length2, 0.0, 1.0)
            closest = a + t[:, None] * ab
        else:
            closest = a
        d = np.einsum("ij,ij->i", points - closest, points - closest)
        better = d < best_d
        best[better], best_d[better] = k, d[better]
    return best


def _levels(floors: list[Floor]) -> dict[float, list[Floor]]:
    levels: dict[float, list[Floor]] = {}
    for floor in floors:
        key = next((z for z in levels if abs(z - floor.z) <= LEVEL_TOLERANCE), floor.z)
        levels.setdefault(key, []).append(floor)
    return levels


def _shares(floors: list[Floor], starts, ends, spacing: float) -> list[Tributary]:
    """The floor points nearest each segment, as tributary shares."""
    out = [Tributary() for _ in starts]
    if not floors or not len(starts):
        return out
    points, cell, kpa, which = _sample(floors, spacing)
    if not len(points):
        return out
    nearest = _nearest(points, np.asarray(starts, float), np.asarray(ends, float))
    for k, share in enumerate(out):
        mine = nearest == k
        share.area_m2 = float(mine.sum() * cell)
        share.load_kn = float(kpa[mine].sum() * cell)
        heavy = mine & (kpa > HEAVY_KPA)
        share.heavy_m2 = float(heavy.sum() * cell)
        share.sources = {floors[i].name for i in np.unique(which[heavy])}
        share.own_m2 = share.area_m2
    return out


# =============================================================================
# COLUMNS
# =============================================================================
def column_tributary(floors: list[Floor], columns: list[Frame], spacing: float = 200.0
                     ) -> dict[str, Tributary]:
    """Tributary area of every column, summed over the floors it supports."""
    levels = _levels(floors)
    top = {c.name: max(c.i[2], c.j[2]) for c in columns}
    own: dict[str, Tributary] = {c.name: Tributary() for c in columns}
    for z, at_level in levels.items():
        supports = [c for c in columns if abs(top[c.name] - z) <= LEVEL_TOLERANCE]
        if not supports:
            continue
        xy = [(c.j[:2] if c.j[2] >= c.i[2] else c.i[:2]) for c in supports]
        for column, share in zip(supports, _shares(at_level, xy, xy, spacing)):
            own[column.name] = share
    # the column standing on each column: its bottom joint is this column's top joint
    by_bottom = {}
    for c in columns:
        bottom_joint = c.joint_i if c.i[2] <= c.j[2] else c.joint_j
        by_bottom[bottom_joint] = c.name
    above = {}
    for c in columns:
        top_joint = c.joint_j if c.j[2] >= c.i[2] else c.joint_i
        if top_joint in by_bottom:
            above[c.name] = by_bottom[top_joint]
    done: dict[str, Tributary] = {}

    def total(name: str, depth: int = 0) -> Tributary:
        if name in done:
            return done[name]
        result = Tributary(own_m2=own[name].area_m2)
        result.add(own[name])
        result.levels = 1 if own[name].area_m2 > 0 else 0
        upper = above.get(name)
        if upper and depth < 200:
            from_above = total(upper, depth + 1)
            result.add(from_above)
            result.levels += from_above.levels
        done[name] = result
        return result

    return {c.name: total(c.name) for c in columns}


# =============================================================================
# BEAMS
# =============================================================================
def _direction(frame: Frame, joint: str) -> np.ndarray:
    a, b = (np.array(frame.i[:2]), np.array(frame.j[:2]))
    vector = (b - a) if frame.joint_i == joint else (a - b)
    length = np.linalg.norm(vector)
    return vector / length if length > 0 else vector


def beam_tributary(floors: list[Floor], beams: list[Frame], column_joints: set[str],
                   spacing: float = 200.0) -> dict[str, Tributary]:
    """Tributary area of every beam, with what beams framing into it pass on."""
    own: dict[str, Tributary] = {b.name: Tributary() for b in beams}
    by_level: dict[float, list[Frame]] = {}
    for beam in beams:
        z = (beam.i[2] + beam.j[2]) / 2
        key = next((k for k in by_level if abs(k - z) <= LEVEL_TOLERANCE), z)
        by_level.setdefault(key, []).append(beam)
    levels = _levels(floors)
    for z, at_level in by_level.items():
        floor_key = next((k for k in levels if abs(k - z) <= LEVEL_TOLERANCE), None)
        if floor_key is None:
            continue
        shares = _shares(levels[floor_key], [b.i[:2] for b in at_level],
                         [b.j[:2] for b in at_level], spacing)
        for beam, share in zip(at_level, shares):
            own[beam.name] = share

    # who carries the load arriving at a joint without a column
    at_joint: dict[str, list[Frame]] = {}
    for beam in beams:
        at_joint.setdefault(beam.joint_i, []).append(beam)
        at_joint.setdefault(beam.joint_j, []).append(beam)
    supporters: dict[str, list[str]] = {}
    for joint, members in at_joint.items():
        if joint in column_joints or len(members) < 2:
            continue
        pairs = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                da, db = _direction(members[a], joint), _direction(members[b], joint)
                if float(da @ db) < -math.cos(math.radians(10)):  # straight through
                    pairs.append((min(members[a].depth, members[b].depth), a, b))
        if pairs:
            _, a, b = max(pairs)
            supporters[joint] = [members[a].name, members[b].name]
    supported_at = {joint: [m.name for m in members if m.name not in supporters[joint]]
                    for joint, members in at_joint.items() if joint in supporters}
    ends = {b.name: (b.joint_i, b.joint_j) for b in beams}
    done: dict[str, Tributary] = {}

    def total(name: str, stack: tuple = ()) -> Tributary:
        if name in done:
            return done[name]
        result = Tributary(own_m2=own[name].area_m2, levels=1)
        result.add(own[name])
        if name not in stack and len(stack) < 50:
            for joint in ends[name]:
                if name in supporters.get(joint, ()):
                    carriers = len(supporters[joint])
                    for other in supported_at[joint]:
                        result.add(total(other, stack + (name,)), 0.5 / carriers)
        done[name] = result
        return result

    return {b.name: total(b.name) for b in beams}


# =============================================================================
# FROM ETABS
# =============================================================================
@dataclass
class GeometricTributary:
    """Tributary areas of the model and the heavy reducible live loads found."""

    beams: dict[str, Tributary]
    columns: dict[str, Tributary]
    heavy_loads: list[tuple[str, str, float]]   # (source, pattern, kPa) above 4.8 kPa


def _table(connector, name: str, patterns: list[str] | None = None):
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay([])
    tables.SetLoadCombinationsSelectedForDisplay([])
    if patterns is not None:
        tables.SetLoadPatternsSelectedForDisplay(patterns)
    try:
        return connector._read_database_table(name)
    except Exception:
        import pandas as pd

        return pd.DataFrame()


def floor_intensities(connector, reducible_patterns: list[str]):
    """Reducible live kPa of every floor, and the loads above 4.8 kPa (source, pattern, kPa)."""
    import pandas as pd

    patterns = connector.sap_model.LoadPatterns
    all_patterns = [str(n) for n in patterns.GetNameList(0, [])[1]]
    reducible = set(reducible_patterns)
    kpa: dict[str, float] = {}
    heavy: list[tuple[str, str, float]] = []
    sets = _table(connector, "Shell Uniform Load Sets", all_patterns)
    if not sets.empty:
        sets = sets.copy()
        sets["Name"] = sets["Name"].replace({None: pd.NA, "": pd.NA}).ffill()
        sets["kPa"] = pd.to_numeric(sets["LoadValue"], errors="coerce") * 1e3
        live = sets[sets["LoadPattern"].astype(str).isin(reducible)]
        set_kpa = live.groupby("Name")["kPa"].sum().to_dict()
        heavy += [(f"load set {n}", str(p), float(v)) for n, p, v in
                  zip(live["Name"], live["LoadPattern"], live["kPa"]) if v > HEAVY_KPA]
        assigned = _table(connector, "Area Load Assignments - Uniform Load Sets", all_patterns)
        for name, load_set in zip(assigned.get("UniqueName", []), assigned.get("LoadSet", [])):
            kpa[str(name)] = kpa.get(str(name), 0.0) + set_kpa.get(str(load_set), 0.0)
    direct = _table(connector, "Area Load Assignments - Uniform", all_patterns)
    if not direct.empty:
        direct = direct[direct["LoadPattern"].astype(str).isin(reducible)]
        gravity = direct["Dir"].astype(str).str.contains("Gravity|Z", case=False, regex=True)
        for name, pattern, value in zip(direct["UniqueName"][gravity], direct["LoadPattern"][gravity],
                                        pd.to_numeric(direct["Load"][gravity], errors="coerce")):
            value = abs(float(value)) * 1e3
            kpa[str(name)] = kpa.get(str(name), 0.0) + value
            if value > HEAVY_KPA:
                heavy.append((f"floor {name}", str(pattern), value))
    return kpa, heavy


def geometric_tributary(connector, reducible_patterns: list[str],
                        spacing: float = 200.0) -> GeometricTributary:
    """Tributary areas of every beam and column of the open model."""
    import pandas as pd

    points = _table(connector, "Point Object Connectivity")
    xyz = {str(n): (float(x), float(y), float(z)) for n, x, y, z in
           zip(points["UniqueName"], pd.to_numeric(points["X"]), pd.to_numeric(points["Y"]),
               pd.to_numeric(points["Z"]))}
    kpa, heavy = floor_intensities(connector, reducible_patterns)
    area_api = connector.sap_model.AreaObj
    floors = []
    for name in _table(connector, "Floor Object Connectivity")["UniqueName"].astype(str):
        try:
            if area_api.GetOpening(name, False)[0]:
                continue
        except Exception:
            pass
        corners = [str(p) for p in area_api.GetPoints(name, 0, [])[1]]
        corners = [xyz[p] for p in corners if p in xyz]
        if len(corners) < 3:
            continue
        floors.append(Floor(name, sum(p[2] for p in corners) / len(corners),
                            [(p[0], p[1]) for p in corners], kpa.get(name, 0.0)))

    def frames(table_name: str) -> list[Frame]:
        table = _table(connector, table_name)
        out = []
        for name, i, j in zip(table["UniqueName"].astype(str), table["UniquePtI"].astype(str),
                              table["UniquePtJ"].astype(str)):
            if i in xyz and j in xyz:
                out.append(Frame(name, i, j, xyz[i], xyz[j]))
        return out

    beams, columns = frames("Beam Object Connectivity"), frames("Column Object Connectivity")
    sections = _table(connector, "Frame Assignments - Section Properties")
    rect = _table(connector, "Frame Section Property Definitions - Concrete Rectangular")
    if not sections.empty and not rect.empty:
        depth = dict(zip(rect["Name"].astype(str), pd.to_numeric(rect["t3"], errors="coerce")))
        assigned = dict(zip(sections["UniqueName"].astype(str), sections["SectProp"].astype(str)))
        for beam in beams:
            beam.depth = float(depth.get(assigned.get(beam.name, ""), 0.0) or 0.0)
    column_joints = {c.joint_i for c in columns} | {c.joint_j for c in columns}
    return GeometricTributary(
        beam_tributary(floors, beams, column_joints, spacing),
        column_tributary(floors, columns, spacing),
        heavy,
    )
