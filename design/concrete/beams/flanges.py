"""Slab flanges of the beams, for the deflection check of T- and L-beams.

A beam cast with the slab bends with part of it: the slab is a flange on the
side or sides where it is. NSCP 406.3.2.1 (ACI 6.3.2.1) gives the effective
flange width, the web width bw plus an overhang on each side with a slab
(Table 406.3.2.1), h the slab thickness, sw the clear distance to the
adjacent web and ln the clear span:

* slab on both sides (T-beam), each side: the least of 8h, sw/2 and ln/8;
* slab on one side (L-beam):             the least of 6h, sw/2 and ln/12.

``slab_flanges`` reads, from the geometry of a floor level, which sides of
each beam have a slab, its thickness, and sw on each side. ``t_section``
then gives the gross and cracked section properties the deflection check
needs: Ig, the centroid (for yt of Mcr = fr Ig / yt, NSCP 424.2.3.5b, yt from
the centroid of the gross section to the tension face) and Icr with the
flange in compression.

The slab top is taken level with the beam top, as ETABS draws a beam at the
floor level. Under a hogging moment the flange is in tension: it adds to Ig
and Mcr (yt is then the distance to the top face), and once cracked the
section is the web alone with its bars, the slab bars left out (the safe
side).

Plain data, no ETABS. Lengths in mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NONE_FOUND = math.inf        # sw when no other web is found on that side
PROBE = 100.0                # mm beyond the beam face where the slab is looked for
SEARCH = 50_000.0            # mm: the farthest adjacent web looked for
PARALLEL = math.radians(30)  # a web within this angle of the beam is "adjacent"
LEVEL_TOLERANCE = 10.0       # mm


@dataclass(frozen=True)
class Flange:
    """The slab on each side of a beam: side 1 is to the left going from I to J.

    ``hf_1`` and ``hf_2`` are the slab thicknesses (0 when there is no slab on
    that side); ``sw_1`` and ``sw_2`` the clear distances to the adjacent webs
    (``NONE_FOUND`` when there is none).
    """

    hf_1: float = 0.0
    hf_2: float = 0.0
    sw_1: float = NONE_FOUND
    sw_2: float = NONE_FOUND

    @property
    def sides(self) -> int:
        return int(self.hf_1 > 0) + int(self.hf_2 > 0)

    def overhangs(self, ln: float) -> tuple[float, float]:
        """The effective overhang on each side, NSCP Table 406.3.2.1."""
        both = self.sides == 2
        out = []
        for hf, sw in ((self.hf_1, self.sw_1), (self.hf_2, self.sw_2)):
            if hf <= 0:
                out.append(0.0)
                continue
            limits = [(8.0 if both else 6.0) * hf, sw / 2.0]
            if ln > 0:
                limits.append(ln / (8.0 if both else 12.0))
            out.append(max(min(limits), 0.0))
        return out[0], out[1]


# =============================================================================
# SECTION
# =============================================================================
@dataclass(frozen=True)
class FlangedSection:
    """Gross properties of the web and its effective overhangs (depths from the top face)."""

    bw: float
    h: float
    parts: tuple[tuple[float, float], ...]   # (width, depth from the top) of each rectangle
    area: float
    centroid: float                          # from the top face
    ig: float
    overhangs: tuple[float, float] = (0.0, 0.0)
    hf: float = 0.0                          # the thinner flange, for the description

    @property
    def width(self) -> float:
        """The effective flange width bf (bw for a rectangle)."""
        return self.bw + sum(self.overhangs)

    def kind(self) -> str:
        count = sum(1 for o in self.overhangs if o > 0)
        return "T-beam" if count == 2 else ("L-beam" if count else "Rectangular")

    def describe(self) -> str:
        if self.kind() == "Rectangular":
            return "Rectangular"
        return f"{self.kind()}: bf {self.width:,.0f} mm, hf {self.hf:g} mm"


def t_section(bw: float, h: float, flange: Flange | None, ln: float) -> FlangedSection:
    """The web and its overhangs as rectangles hanging from the top face."""
    parts = [(bw, h)]
    used = [0.0, 0.0]
    thick = []
    if flange is not None:
        for k, (overhang, hf) in enumerate(zip(flange.overhangs(ln), (flange.hf_1, flange.hf_2))):
            if overhang > 0 and hf > 0:
                parts.append((overhang, min(hf, h)))
                used[k] = overhang
                thick.append(min(hf, h))
    area = sum(w * d for w, d in parts)
    centroid = sum(w * d * d / 2.0 for w, d in parts) / area
    ig = sum(w * d**3 / 12.0 + w * d * (centroid - d / 2.0) ** 2 for w, d in parts)
    return FlangedSection(bw, h, tuple(parts), area, centroid, ig, tuple(used),
                          min(thick) if thick else 0.0)


def cracked_inertia_flanged(parts, d: float, as_tension: float, d_prime: float,
                            as_compression: float, n: float) -> float:
    """Icr of a cracked transformed section whose compression face is the top.

    ``parts`` are rectangles (width, depth from the top). The neutral axis
    depth c satisfies the balance of first moments: the compressed concrete
    and (n - 1) As' above it against n As below it. For one rectangle this is
    the rectangular Icr.
    """
    if as_tension <= 0 or d <= 0:
        return 0.0

    def first_moment(c: float) -> float:
        concrete = sum(w * min(c, depth) * (c - min(c, depth) / 2.0) for w, depth in parts)
        return (concrete + (n - 1.0) * as_compression * (c - d_prime)
                - n * as_tension * (d - c))

    low, high = 0.0, d
    for _ in range(200):          # first_moment rises with c: bisection
        mid = (low + high) / 2.0
        if first_moment(mid) > 0:
            high = mid
        else:
            low = mid
        if high - low < 1e-9 * d:
            break
    c = (low + high) / 2.0
    concrete = 0.0
    for w, depth in parts:
        t = min(c, depth)
        concrete += w * t**3 / 12.0 + w * t * (c - t / 2.0) ** 2
    return (concrete + n * as_tension * (d - c) ** 2
            + (n - 1.0) * as_compression * (c - d_prime) ** 2)


# =============================================================================
# GEOMETRY
# =============================================================================
@dataclass
class FloorArea:
    """A floor area object at one elevation: a slab of a thickness, or an opening."""

    outline: list[tuple[float, float]]
    z: float
    thickness: float = 0.0
    opening: bool = False


def slab_flanges(points: dict[str, tuple[float, float, float]],
                 beams: list[tuple[str, str, str]], widths: dict[str, float],
                 floors: list[FloorArea], probe: float = PROBE) -> dict[str, Flange]:
    """The slab on each side of every level beam.

    ``beams`` are (name, joint I, joint J), ``widths`` the web widths. A side
    has a slab when two of three points, at a quarter, half and three
    quarters of the beam and ``probe`` beyond its face, are on a slab and not
    in an opening. Its thickness is the thinnest slab found there. sw is the
    clear distance, square to the beam from its middle, to the nearest other
    beam of the level within 30 degrees of it.
    """
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    def polygon(outline):
        if len(outline) < 3:
            return None
        shape = Polygon(outline)
        shape = shape if shape.is_valid else shape.buffer(0)
        return shape if not shape.is_empty and shape.area > 0 else None

    levels: dict[float, list[FloorArea]] = {}
    for floor in floors:
        key = next((z for z in levels if abs(z - floor.z) <= LEVEL_TOLERANCE), floor.z)
        levels.setdefault(key, []).append(floor)

    def level_of(z: float):
        return next((k for k in levels if abs(k - z) <= LEVEL_TOLERANCE), None)

    found: dict[float, tuple] = {}
    for key, items in levels.items():
        slabs = [(polygon(f.outline), f.thickness) for f in items
                 if not f.opening and f.thickness > 0]
        slabs = [(p, t) for p, t in slabs if p is not None]
        holes = [p for p in (polygon(f.outline) for f in items if f.opening) if p is not None]
        found[key] = (STRtree([p for p, _ in slabs]) if slabs else None, slabs,
                      unary_union(holes) if holes else None)

    level_beams: dict[float, list] = {}
    for name, i, j in beams:
        if i not in points or j not in points:
            continue
        a, b = points[i], points[j]
        if abs(a[2] - b[2]) > LEVEL_TOLERANCE:
            continue
        key = level_of((a[2] + b[2]) / 2.0)
        if key is None:
            continue
        level_beams.setdefault(key, []).append((name, a, b))

    out: dict[str, Flange] = {}
    for key, members in level_beams.items():
        tree, slabs, holes = found[key]
        segments = [LineString([m[1][:2], m[2][:2]]) for m in members]
        beam_tree = STRtree(segments)
        for index, (name, a, b) in enumerate(members):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            bw = float(widths.get(name, 0.0) or 0.0)
            if length <= 0 or bw <= 0 or tree is None:
                out[name] = Flange()
                continue
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux                          # side 1: to the left of I -> J
            values = {}
            for side, sign in ((1, 1.0), (2, -1.0)):
                thick = []
                for t in (0.25, 0.5, 0.75):
                    reach = sign * (bw / 2.0 + probe)
                    spot = Point(a[0] + t * dx + reach * nx, a[1] + t * dy + reach * ny)
                    if holes is not None and holes.contains(spot):
                        continue
                    here = [slabs[k][1] for k in tree.query(spot)
                            if slabs[k][0].covers(spot)]
                    if here:
                        thick.append(min(here))
                hf = min(thick) if len(thick) >= 2 else 0.0
                sw = NONE_FOUND
                if hf > 0:
                    mx, my = a[0] + dx / 2.0, a[1] + dy / 2.0
                    ray = LineString([(mx, my), (mx + sign * SEARCH * nx, my + sign * SEARCH * ny)])
                    for k in beam_tree.query(ray):
                        if k == index:
                            continue
                        other, oa, ob = members[k]
                        ox, oy = ob[0] - oa[0], ob[1] - oa[1]
                        olen = math.hypot(ox, oy)
                        if olen <= 0:
                            continue
                        angle = math.acos(min(1.0, abs(ux * ox + uy * oy) / olen))
                        if angle > PARALLEL:
                            continue
                        hit = ray.intersection(segments[k])
                        if hit.is_empty:
                            continue
                        distance = Point(mx, my).distance(hit)
                        clear = distance - bw / 2.0 - float(widths.get(other, 0.0) or 0.0) / 2.0
                        if 0 < clear < sw:
                            sw = clear
                values[side] = (hf, sw)
            out[name] = Flange(values[1][0], values[2][0], values[1][1], values[2][1])
    return out


# =============================================================================
# TABLE
# =============================================================================
FLANGE_TABLE = "SLAB FLANGES"
FLANGE_FIELDS = ("UniqueName", "hf_1", "hf_2", "sw_1", "sw_2")


def flanges_to_rows(flanges: dict[str, Flange]) -> list[dict]:
    """The flanges as table rows; an sw with no web found is left blank."""
    return [{"UniqueName": name, "hf_1": f.hf_1, "hf_2": f.hf_2,
             "sw_1": None if math.isinf(f.sw_1) else f.sw_1,
             "sw_2": None if math.isinf(f.sw_2) else f.sw_2} for name, f in flanges.items()]


def flanges_from_table(table) -> dict[str, Flange]:
    """The SLAB FLANGES table back to flanges (an empty dict for no table)."""
    if table is None or len(table) == 0:
        return {}

    def value(row, key: str, missing: float) -> float:
        try:
            out = float(row[key])
        except (TypeError, ValueError, KeyError):
            return missing
        return missing if math.isnan(out) else out

    return {str(row["UniqueName"]): Flange(value(row, "hf_1", 0.0), value(row, "hf_2", 0.0),
                                           value(row, "sw_1", NONE_FOUND),
                                           value(row, "sw_2", NONE_FOUND))
            for _, row in table.iterrows()}
