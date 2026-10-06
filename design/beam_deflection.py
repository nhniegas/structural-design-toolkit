"""Beam deflection checks, ACI 318M-14 24.2, from the designed bars and the service moments.

For each beam, after its bars are chosen:

1. Section properties of each zone (left end, midspan, right end; the zones
   of the flexural design): Ec = 4700 sqrt(f'c), Ig = b h^3 / 12,
   fr = 0.62 sqrt(f'c), Mcr = fr Ig / (h / 2), and Icr of the cracked
   transformed section with the zone's tension and compression bars.
2. Ie of each zone (Eq. 24.2.3.5a) at Ma = the largest moment of the full
   service load (DL + LL) in the zone. The same Ie is used for every load:
   once cracked under the full service load the beam stays cracked.
3. Deflection along the span: the curvature M / (Ec Ie) integrated twice,
   with zero deflection at both supports. A cantilever is fixed at its root
   and the deflection from the support rotation of the analysis is added.
   The whole beam line is taken as one chain and cut where it is supported:
   at a joint with a column below it or a wall; at a joint, or inside a
   member, where the shear of the full service load jumps up, which is a
   support pushing up (a girder that carries the line); and at an end where
   another beam can reach a support without passing through this line
   (``beam_carriers.BeamNetwork``). A planted column and a beam that only
   hangs on the line are loads: the span runs on through them, and an end
   that carries only such a beam is a free end.
4. Long-term factor lambda = xi / (1 + 50 rho'), rho' the compression steel
   at midspan (at the support of a cantilever); xi = 2.0 for 5 years or more.
5. Checks (Table 24.2.2):

   * immediate live load       D(DL+LL) - D(DL)                           L/360
   * immediate roof live load  D(DL+Lr) - D(DL), roof level only          L/180
   * after attachment of the partitions: the long-term part plus the live
     load that is not sustained
                               lambda D(DL+0.25LL) + D(DL+LL) - D(DL+0.25LL)
                               L/480 (partitions likely to be damaged) or L/240
6. For information: the long-term part alone, lambda D(DL+0.25LL), and the
   total, D(DL+LL) + lambda D(DL+0.25LL). The code sets no limit on them.
7. With ``DeflectionStages`` the long-term deflection that happens before
   the partitions are built is deducted from the after-attachment
   deflection, as the footnote of Table 24.2.2 allows:
   lambda(t) x share x D(DL), where t is the time from when the dead load is
   first carried to when the partitions are built, lambda(t) uses xi(t) of
   Table 24.2.4.1.3 and ``share`` is the part of the dead load in place
   before the partitions. Without stages nothing is deducted, which is the
   safe side.

Lengths in mm, moments in kN-m, stresses in MPa.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from design import dcr_targets

ES = 200000.0           # MPa
XI_LONG_TERM = 2.0      # 5 years or more, ACI Table 24.2.4.1.3
# (months under load, xi): ACI Table 24.2.4.1.3, straight lines between its values
TIME_FACTORS = ((0.0, 0.0), (3.0, 1.0), (6.0, 1.2), (12.0, 1.4), (60.0, XI_LONG_TERM))
LAYER_CLEAR = 25.0      # mm between layers (BeamDetailingConfig.layer_clear_spacing)
ZONE_FRACTION = 0.25    # end zones of the span (BeamDetailingConfig.moment_zone_fraction)

COMBO_DEAD = "DEF 100 1.0 DL"
COMBO_FULL = "DEF 101 1.0 DL + 1.0 LL"
COMBO_SUSTAINED = "DEF 102 1.0 DL + 0.25 LL"
COMBO_ROOF = "DEF 103 1.0 DL + 1.0 Lr"
DEFLECTION_COMBOS = (COMBO_DEAD, COMBO_FULL, COMBO_SUSTAINED, COMBO_ROOF)

LIMIT_DAMAGED = 480     # partitions likely to be damaged
LIMIT_NOT_DAMAGED = 240


def time_factor(months: float) -> float:
    """xi for a load sustained ``months``: ACI Table 24.2.4.1.3 (1.0 at 3 months,
    1.2 at 6, 1.4 at 12, 2.0 at 60 or more), straight lines in between and
    from zero at the start."""
    months = max(0.0, float(months))
    for (m0, x0), (m1, x1) in zip(TIME_FACTORS, TIME_FACTORS[1:]):
        if months <= m1:
            return x0 + (x1 - x0) * (months - m0) / (m1 - m0)
    return XI_LONG_TERM


@dataclass(frozen=True)
class DeflectionStages:
    """When the partitions are built, for the after-attachment deflection.

    ``months_before_partitions``: from the time the beam first carries its
    dead load (the forms are removed) to the time the partitions are built.
    ``dead_share_before``: the part of the dead load, 0 to 1, in place before
    the partitions (self weight, slab, and what else is there by then; not
    the partitions and what comes after them).
    """

    months_before_partitions: float = 0.0
    dead_share_before: float = 1.0

    @property
    def active(self) -> bool:
        return self.months_before_partitions > 0 and self.dead_share_before > 0

    def describe(self) -> str:
        if not self.active:
            return "all the long-term deflection counted after the partitions (the safe side)"
        return (f"partitions built {self.months_before_partitions:g} months after the dead load "
                f"is first carried (xi {time_factor(self.months_before_partitions):.2f}); "
                f"{self.dead_share_before * 100:g} % of the dead load in place before them")

    def to_saved(self) -> dict:
        return {"months_before_partitions": self.months_before_partitions,
                "dead_share_before": self.dead_share_before}

    @classmethod
    def from_saved(cls, saved) -> "DeflectionStages":
        try:
            stages = cls(float(saved["months_before_partitions"]),
                         float(saved["dead_share_before"]))
        except (TypeError, KeyError, ValueError):
            return cls()
        if stages.months_before_partitions < 0 or not 0 <= stages.dead_share_before <= 1:
            return cls()
        return stages


# =============================================================================
# SECTION
# =============================================================================
def cracked_inertia(b: float, d: float, as_tension: float, d_prime: float,
                    as_compression: float, n: float) -> float:
    """Icr of a rectangular section with tension and compression steel (transformed)."""
    if as_tension <= 0 or d <= 0:
        return 0.0
    # b c^2 / 2 + (n - 1) As' (c - d') = n As (d - c)
    a = b / 2.0
    bq = (n - 1.0) * as_compression + n * as_tension
    cq = -((n - 1.0) * as_compression * d_prime + n * as_tension * d)
    c = (-bq + math.sqrt(bq * bq - 4 * a * cq)) / (2 * a)
    return (b * c**3 / 3.0 + n * as_tension * (d - c) ** 2
            + (n - 1.0) * as_compression * (c - d_prime) ** 2)


def effective_inertia(ma: float, mcr: float, ig: float, icr: float) -> float:
    """ACI 318-14 Eq. 24.2.3.5a; Ig when the section does not crack."""
    if ma <= mcr or ma <= 0:
        return ig
    ratio = (mcr / ma) ** 3
    return min(ig, ratio * ig + (1.0 - ratio) * icr)


def layer_depths(h: float, cover: float, stirrup: float, bar: float, top: bool) -> list[float]:
    """Depth from the top face of the bar layers 1, 2, 3 (as the design labels them).

    Top bars: layer 1 is the topmost. Bottom bars: layer 3 is the bottommost.
    """
    first = cover + stirrup + bar / 2.0
    spacing = bar + max(LAYER_CLEAR, bar)
    if top:
        return [first, first + spacing, first + 2 * spacing]
    return [h - first - 2 * spacing, h - first - spacing, h - first]


def steel(counts: list[float], depths: list[float], bar: float) -> tuple[float, float]:
    """(area, centroid depth) of bar layers."""
    area_one = math.pi * bar * bar / 4.0
    total = sum(counts)
    if total <= 0:
        return 0.0, depths[0]
    return total * area_one, sum(n * z for n, z in zip(counts, depths)) / total


# =============================================================================
# DEFLECTION
# =============================================================================
def _cumulative(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    out = np.zeros_like(y)
    out[1:] = np.cumsum((y[1:] + y[:-1]) / 2.0 * np.diff(x))
    return out


def deflection_profile(x: np.ndarray, moment: np.ndarray, ei: np.ndarray,
                       root: str | None = None) -> np.ndarray:
    """Downward deflection along the span from M (sagging +) and EI.

    ``root`` None: supported at both ends (deflection relative to the chord).
    ``root`` "start" or "end": a cantilever fixed at that end.
    """
    x = np.asarray(x, float)
    order = np.argsort(x, kind="stable")
    xs, curvature = x[order], (np.asarray(moment, float) / np.asarray(ei, float))[order]
    if root == "end":
        xs, curvature = xs[-1] - xs[::-1], curvature[::-1]
    slope = _cumulative(curvature, xs)
    upward = _cumulative(slope, xs)
    if root is None:
        span = xs[-1] - xs[0]
        upward = upward - upward[-1] * (xs - xs[0]) / span if span > 0 else upward
    down = -upward
    if root == "end":
        down = down[::-1]
    out = np.empty_like(down)
    out[order] = down
    return out


@dataclass
class BeamSection:
    """What the deflection of one beam needs from its design."""

    width: float
    depth: float
    fc: float
    cover: float
    stirrup: float
    bar: float
    top: dict[str, list[float]]      # zone -> bar counts of layers 1..3 (top face)
    bottom: dict[str, list[float]]   # zone -> bar counts of layers 1..3 (bottom face)


@dataclass
class DeflectionResult:
    ie: dict[str, float]
    lam: float
    live: float
    roof: float | None
    long_term: float                # after attachment of the partitions: the checked value
    span: float
    live_limit: float
    roof_limit: float | None
    long_limit: float
    creep: float = 0.0              # the long-term part alone, lambda D(DL + 0.25 LL)
    total: float = 0.0              # immediate + long-term, D(DL + LL) + lambda D(DL + 0.25 LL)
    deducted: float = 0.0           # long-term deflection before the partitions (stages)
    at_first_load: float | None = None    # when the dead load is first carried (stages)
    at_partitions: float | None = None    # just before the partitions are built (stages)

    @property
    def ratio(self) -> float:
        ratios = [self.live / self.live_limit, self.long_term / self.long_limit]
        if self.roof is not None and self.roof_limit:
            ratios.append(self.roof / self.roof_limit)
        return max(ratios)

    @property
    def passed(self) -> bool:
        return self.ratio <= 1.0


# =============================================================================
# BEAM DESIGN TABLE
# =============================================================================
DEFLECTION_COLUMNS = {
    "Defl_Ie_left": "Iₑ, left (mm⁴)",
    "Defl_Ie_mid": "Iₑ, mid (mm⁴)",
    "Defl_Ie_right": "Iₑ, right (mm⁴)",
    "Defl_lambda": "λΔ (long-term)",
    "Defl_live_mm": "Δ live (mm)",
    "Defl_live_limit_mm": "Δ live limit L/360 (mm)",
    "Defl_roof_mm": "Δ roof live (mm)",
    "Defl_roof_limit_mm": "Δ roof limit L/180 (mm)",
    "Defl_long_mm": "Δ after attachment of partitions (mm)",
    "Defl_long_limit_mm": "Δ after attachment limit (mm)",
    "Defl_creep_mm": "Δ long-term part, λΔ·Δ(DL + 0.25 LL) (mm)",
    "Defl_total_mm": "Δ total, immediate + long-term (mm)",
    "Defl_first_load_mm": "Δ when the dead load is first carried (mm)",
    "Defl_at_partitions_mm": "Δ just before the partitions are built (mm)",
    "Defl_deducted_mm": "Δ long-term before the partitions, deducted (mm)",
    "Defl_stages": "Deflection stages",
    "Defl_ratio": "Δ / limit (governing)",
    "Deflection_Check": "Deflection check",
}
# shown only when the engineer gave the stages
STAGE_COLUMNS = ("Defl_first_load_mm", "Defl_at_partitions_mm", "Defl_deducted_mm", "Defl_stages")
DEFLECTION_FAILED = "FAILED: DEFLECTION (ACI 24.2.2)"
_ZONES = ("left", "mid", "right")
_TIP_I = "Tip from rotation at I (mm)"
_TIP_J = "Tip from rotation at J (mm)"


def _counts(row, zone: str) -> list[float]:
    return [float(row.get(f"n_{zone}_L{k}", 0) or 0) for k in (1, 2, 3)]


@dataclass
class SpanPart:
    """One ETABS segment of a span: its section, positions along the span and moments."""

    name: str
    section: BeamSection
    x: np.ndarray                    # positions along the span (mm)
    moments: dict[str, np.ndarray]   # DEF combination -> M3 (kN-m) at x
    compression_zone: str = "mid"    # where rho' is taken
    zones: np.ndarray | None = None  # zone of each position, when the part is a cut of a member


def _zones(x: np.ndarray) -> np.ndarray:
    """Zone (left, mid, right) of each position of a member: its end quarters and the rest."""
    length = float(x.max() - x.min())
    t = (x - x.min()) / length if length > 0 else np.zeros_like(x)
    return np.where(t <= ZONE_FRACTION, "left", np.where(t >= 1 - ZONE_FRACTION, "right", "mid"))


SUPPORT_SHARE = 0.05   # a shear jump above this share of the largest shear nearby is a support
SUPPORT_FLOOR = 0.5    # kN: smaller jumps are not read at all


def statics_shear(pieces: list[tuple[np.ndarray, np.ndarray, np.ndarray]]
                  ) -> list[np.ndarray] | None:
    """The shear of each piece with the sign of dM/dx, whatever sign the analysis uses.

    ``pieces`` are (x, M, V) along the span, x rising. With Q = dM/dx a load
    pushing down makes Q fall along the span and a support pushing up makes
    it jump up. None when the moments and shears do not tell the sign apart.
    """
    total = 0.0
    for x, moment, shear in pieces:
        dx = np.diff(x)
        ok = dx > 1e-6
        if ok.any():
            total += float(np.sum(np.diff(moment)[ok] * ((shear[1:] + shear[:-1]) / 2.0)[ok]))
    if abs(total) < 1e-9:
        return None
    sign = 1.0 if total > 0 else -1.0
    return [sign * np.asarray(shear, float) for _, _, shear in pieces]


def support_cuts(xs: list[np.ndarray], qs: list[np.ndarray],
                 share: float = SUPPORT_SHARE) -> list[tuple[int, int]]:
    """Where a chain of pieces is supported, from its shear Q = dM/dx.

    A cut (k, i) is in front of position i of piece k; (0, 0) is the start
    of the chain and (number of pieces, 0) its end. Supported means an upward
    force acts there: Q above zero at the start, below zero at the end, and
    a jump up between two pieces or inside one (the analysis gives two values
    at such a position). A jump down is a load: the span runs on through it.
    """
    peaks = [float(np.max(np.abs(q))) if len(q) else 0.0 for q in qs]

    def enough(value: float, *pieces: int) -> bool:
        return value > max(share * max(peaks[k] for k in pieces), SUPPORT_FLOOR)

    count = len(qs)
    cuts = []
    if count and len(qs[0]) and enough(qs[0][0], 0):
        cuts.append((0, 0))
    for k in range(count):
        x, q = xs[k], qs[k]
        if k and len(q) and len(qs[k - 1]) and enough(q[0] - qs[k - 1][-1], k - 1, k):
            cuts.append((k, 0))
        for i in range(1, len(q)):
            if x[i] - x[i - 1] <= 1e-6 and enough(q[i] - q[i - 1], k):
                cuts.append((k, i))
    if count and len(qs[-1]) and enough(-qs[-1][-1], count - 1):
        cuts.append((count, 0))
    return cuts


def _stiffness(part: SpanPart) -> tuple[dict[str, float], np.ndarray, float]:
    """Ie of each zone of a segment, E Ie along it (N-mm2 / 1e6) and its lambda."""
    sec = part.section
    b, h, fc = sec.width, sec.depth, sec.fc
    ec = 4700.0 * math.sqrt(fc)
    n = ES / ec
    ig = b * h**3 / 12.0
    mcr = 0.62 * math.sqrt(fc) * ig / (h / 2.0) / 1e6  # kN-m
    x = part.x
    zone_of = _zones(x) if part.zones is None else np.asarray(part.zones)
    full = np.asarray(part.moments[COMBO_FULL], float)
    top_depths = layer_depths(h, sec.cover, sec.stirrup, sec.bar, top=True)
    bottom_depths = layer_depths(h, sec.cover, sec.stirrup, sec.bar, top=False)
    ie: dict[str, float] = {}
    for zone in _ZONES:
        in_zone = zone_of == zone
        if not in_zone.any():
            ie[zone] = ig
            continue
        peak = full[in_zone][np.argmax(np.abs(full[in_zone]))]
        as_top, z_top = steel(sec.top[zone], top_depths, sec.bar)
        as_bot, z_bot = steel(sec.bottom[zone], bottom_depths, sec.bar)
        if peak < 0:  # hogging: top bars in tension, depths measured from the bottom face
            icr = cracked_inertia(b, h - z_top, as_top, h - z_bot, as_bot, n)
        else:
            icr = cracked_inertia(b, z_bot, as_bot, z_top, as_top, n)
        ie[zone] = effective_inertia(abs(peak), mcr, ig, icr)
    # E I in N-mm2 / 1e6, so that M in kN-m over it is the curvature in 1/mm
    ei = np.array([ec * ie[z] for z in zone_of]) / 1e6
    zone = part.compression_zone
    if zone == "mid":  # positive moment: top bars in compression
        as_comp = steel(sec.top["mid"], top_depths, sec.bar)[0]
        d_eff = steel(sec.bottom["mid"], bottom_depths, sec.bar)[1]
    else:  # cantilever support: bottom bars in compression
        as_comp = steel(sec.bottom[zone], bottom_depths, sec.bar)[0]
        d_eff = h - steel(sec.top[zone], top_depths, sec.bar)[1]
    rho_prime = as_comp / (b * d_eff) if d_eff > 0 else 0.0
    return ie, ei, XI_LONG_TERM / (1.0 + 50.0 * rho_prime)


def span_deflection(parts: list[SpanPart], cantilever_root: str | None = None,
                    root_rotation: dict[str, float] | None = None, roof: bool = False,
                    long_limit_divisor: float = LIMIT_DAMAGED,
                    stages: DeflectionStages | None = None) -> dict[str, DeflectionResult]:
    """Deflection checks of a span made of one or more segments, per segment.

    ``stages`` deducts the long-term deflection that happens before the
    partitions are built and reports the deflection at those stages.

    The span is supported at both ends, or is a cantilever fixed at
    ``cantilever_root`` ("start" or "end" of the positions). ``root_rotation``
    maps a combination to the downward deflection per mm of distance from the
    root caused by the rotation of the support (a rigid rotation). The limits
    use the whole span.
    """
    stiffness = [_stiffness(part) for part in parts]
    x = np.concatenate([part.x for part in parts])
    ei = np.concatenate([s[1] for s in stiffness])
    lam = np.concatenate([np.full(len(part.x), s[2]) for part, s in zip(parts, stiffness)])
    owner = np.concatenate([np.full(len(part.x), i) for i, part in enumerate(parts)])
    span = float(x.max() - x.min())
    combos = set.intersection(*(set(part.moments) for part in parts))

    def profile(combo: str) -> np.ndarray:
        moment = np.concatenate([np.asarray(part.moments[combo], float) for part in parts])
        curve = deflection_profile(x, moment, ei, cantilever_root)
        if cantilever_root and root_rotation:
            reach = (x - x.min()) if cantilever_root == "start" else (x.max() - x)
            curve = curve + root_rotation.get(combo, 0.0) * reach
        return curve

    dead, full, sustained = profile(COMBO_DEAD), profile(COMBO_FULL), profile(COMBO_SUSTAINED)
    live_curve = full - dead
    creep_curve = lam * sustained
    staged = stages is not None and stages.active
    if staged:   # the dead load before the partitions creeps until they are built
        first_curve = stages.dead_share_before * dead
        deducted_curve = (lam / XI_LONG_TERM) * time_factor(
            stages.months_before_partitions) * first_curve
    else:
        first_curve, deducted_curve = None, np.zeros_like(dead)
    long_curve = creep_curve - deducted_curve + full - sustained
    total_curve = full + creep_curve
    roof_curve = profile(COMBO_ROOF) - dead if roof and COMBO_ROOF in combos else None
    out = {}
    for i, (part, (ie, _, part_lam)) in enumerate(zip(parts, stiffness)):
        mine = owner == i
        out[part.name] = DeflectionResult(
            ie=ie, lam=part_lam, live=float(live_curve[mine].max()),
            roof=None if roof_curve is None else float(roof_curve[mine].max()),
            long_term=float(long_curve[mine].max()), span=span,
            live_limit=span / 360.0,
            roof_limit=span / 180.0 if roof_curve is not None else None,
            long_limit=span / long_limit_divisor,
            creep=float(creep_curve[mine].max()), total=float(total_curve[mine].max()),
            deducted=float(deducted_curve[mine].max()),
            at_first_load=float(first_curve[mine].max()) if staged else None,
            at_partitions=float((first_curve + deducted_curve)[mine].max()) if staged else None,
        )
    return out


def beam_deflection(section: BeamSection, x: np.ndarray, moments: dict[str, np.ndarray],
                    cantilever_root: str | None = None,
                    root_rotation: dict[str, float] | None = None,
                    roof: bool = False, long_limit_divisor: float = LIMIT_DAMAGED,
                    stages: DeflectionStages | None = None) -> DeflectionResult:
    """Deflection checks of a single-segment span (see ``span_deflection``).

    ``root_rotation`` here is the downward deflection at the tip (mm).
    """
    x = np.asarray(x, float)
    length = float(x.max() - x.min())
    zone = "mid" if not cantilever_root else ("left" if cantilever_root == "start" else "right")
    part = SpanPart("member", section, x, moments, zone)
    per_mm = None
    if root_rotation and length > 0:
        per_mm = {combo: value / length for combo, value in root_rotation.items()}
    return span_deflection([part], cantilever_root, per_mm, roof, long_limit_divisor,
                           stages)["member"]


# =============================================================================
# SPANS FROM THE ETABS SEGMENTS
# =============================================================================
_MARK = re.compile(r"^(.*?)(BX|BY|GX|GY)-(\d+)([A-Z]*)$", re.IGNORECASE)


@dataclass
class Span:
    """Segments of one beam line between supports, in order along the span."""

    members: list[str]
    reversed: list[bool]             # segment runs J to I along the span
    offsets: list[float]             # position of each segment's start (mm)
    lengths: list[float]             # joint-to-joint length of each segment (mm)
    start_supported: bool = True
    end_supported: bool = True


def beam_spans(connectivity, members: list[str], points=None,
               through_supports: bool = False) -> list[Span]:
    """Join the segments of each tagged beam line into spans.

    Segments of one line (same level, type and number: 2GX-1, 2GX-1A, ...)
    meeting at a joint with no column or wall continue one span. A span end
    is supported when a column, a wall or another member is there; it is free
    when nothing else connects (a cantilever tip). With ``points`` (the joint
    coordinates) an end that rests on a girder ETABS has not split there is
    supported too: the centre line of that girder passes through the joint,
    but no member of it ends there, so counting the members at the joint
    misses it and a supported line was checked as a cantilever.
    ``through_supports`` joins the segments of a line through columns and
    walls as well: the whole line as one chain, for ``support_cuts`` to cut
    where the forces say it is supported. The line of a member comes
    from the ``Line`` column of the table when it has one (the extraction
    fills it: from the tag, or from the geometry for members with other
    names); without it, members without a tag are spans of their own.
    """
    conn = connectivity.copy()
    conn["UniqueName"] = conn["UniqueName"].astype(str)
    kind = conn["DesignType"].astype(str)
    supports = set()
    for column in ("UniquePtI", "UniquePtJ", "UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4"):
        if column in conn.columns:
            supports |= set(conn.loc[kind.isin(["Column", "Wall"]), column].dropna().astype(str))
    count: dict[str, int] = {}
    for column in ("UniquePtI", "UniquePtJ", "UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4"):
        if column in conn.columns:
            for joint in conn[column].dropna().astype(str):
                count[joint] = count.get(joint, 0) + 1
    beams = conn[kind.eq("Beam")].drop_duplicates("UniqueName").set_index("UniqueName")
    wanted = [str(m) for m in members if str(m) in beams.index]
    ends = {m: (str(beams.at[m, "UniquePtI"]), str(beams.at[m, "UniquePtJ"])) for m in wanted}
    length = {m: float(beams.at[m, "Length"]) if "Length" in beams.columns else 0.0
              for m in wanted}

    held: dict[tuple[str, str], str] = {}
    if points is not None and len(points):
        from design.beam_carriers import FREE_END, _name, end_conditions

        for (member, joint), state in end_conditions(connectivity, points).items():
            if state != FREE_END:
                held[(str(member), str(joint))] = state
        # the same names as here, whatever their type in the tables
        held.update({(_name(m), _name(j)): s for (m, j), s in list(held.items())})

    line_of = {}
    if "Line" in beams.columns:
        line_of = {m: str(beams.at[m, "Line"]) for m in wanted
                   if beams.at[m, "Line"] is not None and str(beams.at[m, "Line"]) != "nan"}

    def line(name: str) -> str:
        if name in line_of:
            return line_of[name]
        match = _MARK.match(name)
        return (match.group(1) + match.group(2) + "-" + match.group(3)).upper() if match else name

    groups: dict[str, list[str]] = {}
    for m in wanted:
        groups.setdefault(line(m), []).append(m)
    spans = []
    for segments in groups.values():
        # joints shared by two segments of the line, away from columns, link them
        at_joint: dict[str, list[str]] = {}
        for m in segments:
            for joint in ends[m]:
                at_joint.setdefault(joint, []).append(m)
        links = {joint: ms for joint, ms in at_joint.items()
                 if len(ms) == 2 and (through_supports or joint not in supports)}
        seen: set[str] = set()
        for first in segments:
            if first in seen:
                continue
            # walk to one end of the chain
            chain_joint = None
            current, previous_joint = first, None
            for _ in range(len(segments)):
                nxt = [j for j in ends[current] if j in links and j != previous_joint]
                if not nxt:
                    break
                joint = nxt[0]
                other = [m for m in links[joint] if m != current][0]
                if other == first:
                    break
                current, previous_joint = other, joint
            start = current
            start_joint = ends[start][0] if ends[start][1] == previous_joint else (
                ends[start][1] if ends[start][0] == previous_joint else None)
            if start_joint is None:  # single segment or the walk did not move
                start_joint = next((j for j in ends[start] if j not in links), ends[start][0])
            members_in, flipped, offsets, lengths = [], [], [], []
            joint, current, position = start_joint, start, 0.0
            while current and current not in seen:
                seen.add(current)
                i, j = ends[current]
                flip = joint == j
                members_in.append(current)
                flipped.append(flip)
                offsets.append(position)
                lengths.append(length[current])
                position += length[current]
                far = i if flip else j
                chain_joint = far
                nxt = [m for m in links.get(far, []) if m != current]
                current = nxt[0] if nxt else None
                joint = far
            first_joint = start_joint

            def supported(jt: str, member: str) -> bool:
                return jt in supports or count.get(jt, 0) > 1 or (member, jt) in held

            spans.append(Span(members_in, flipped, offsets, lengths,
                              supported(first_joint, members_in[0]),
                              supported(chain_joint, members_in[-1])))
    return spans


def add_deflection_columns(results, service, long_limit_divisor: float = LIMIT_DAMAGED,
                           connectivity=None, progress=None,
                           stages: DeflectionStages | None = None, points=None):
    """Add the deflection checks to the beam design results (TOP and BOTTOM rows).

    ``service`` is the SERVICE LOADS table and ``connectivity`` the
    CONNECTIVITY table; with it the segments of a beam line are checked as
    one span. A beam that fails turns from OK to DEFLECTION_FAILED.
    ``stages`` (when the partitions are built) deducts the long-term
    deflection before them and adds the deflection at those stages.
    ``points`` (the joint coordinates) lets an end on an unsplit girder count
    as supported (see ``beam_spans``).
    """
    import pandas as pd

    staged = stages is not None and stages.active
    out = results.copy()
    for column in DEFLECTION_COLUMNS:
        if staged or column not in STAGE_COLUMNS:
            out[column] = None
    if service is None or len(service) == 0:
        out["Deflection_Check"] = "NO DEF COMBOS"
        return out
    service = service.copy()
    for column in ("Station", "M3", _TIP_I, _TIP_J):
        if column in service.columns:
            service[column] = pd.to_numeric(service[column], errors="coerce")
    by_member = {str(k): v for k, v in service.groupby(service["UniqueName"].astype(str))}
    rows_of = {str(k): v for k, v in out.groupby(out["UniqueName"].astype(str))}
    names = list(rows_of)
    has_lines = connectivity is not None and len(connectivity)
    spans = beam_spans(connectivity, names, points) if has_lines else []
    in_span = {m for span in spans for m in span.members}
    for m in names:
        if m not in in_span:
            spans.append(Span([m], [False], [0.0], [0.0]))

    def section_of(name: str):
        rows = rows_of[name]
        top = rows[rows["Face"].astype(str).eq("TOP")]
        bottom = rows[rows["Face"].astype(str).eq("BOTTOM")]
        if top.empty or bottom.empty:
            return None, None
        top, bottom = top.iloc[0], bottom.iloc[0]
        return top, BeamSection(
            width=float(top["Width"]), depth=float(top["Depth"]), fc=float(top["f'c"]),
            cover=float(top["cc"]), stirrup=float(top["ds"]), bar=float(top["dm"]),
            top={z: _counts(top, z) for z in _ZONES},
            bottom={z: _counts(bottom, z) for z in _ZONES},
        )

    def moments_of(name: str):
        svc = by_member.get(name)
        if svc is None:
            return None, None
        full = svc[svc["Combo"].astype(str).eq(COMBO_FULL)].sort_values("Station", kind="stable")
        if full.empty or COMBO_DEAD not in set(svc["Combo"].astype(str)):
            return None, None
        x = full["Station"].to_numpy(float)
        moments = {}
        for combo, group in svc.groupby(svc["Combo"].astype(str)):
            group = group.sort_values("Station", kind="stable")
            if len(group) == len(x):
                moments[combo] = group["M3"].to_numpy(float)
        if COMBO_SUSTAINED not in moments:
            moments[COMBO_SUSTAINED] = moments[COMBO_DEAD] + 0.25 * (
                moments[COMBO_FULL] - moments[COMBO_DEAD])
        return x, moments

    def shear_of(name: str, count: int):
        """V2 of the full service load at the positions of ``moments_of``, or None."""
        svc = by_member.get(name)
        if svc is None or "V2" not in svc.columns:
            return None
        full = svc[svc["Combo"].astype(str).eq(COMBO_FULL)].sort_values("Station", kind="stable")
        shear = pd.to_numeric(full["V2"], errors="coerce").to_numpy(float)
        return shear if len(shear) == count and not np.isnan(shear).any() else None

    def on_roof(name: str) -> bool:
        svc = by_member[name]
        return "Roof level" in svc.columns and bool(
            svc["Roof level"].astype(str).str.upper().eq("TRUE").any())

    def rotation_of(name: str, at_i: bool, length: float):
        """Downward movement per mm of reach from the rotation of a member's end."""
        svc = by_member[name]
        column = _TIP_I if at_i else _TIP_J
        if column not in svc.columns or length <= 0:
            return None
        return {c: v / length for c, v in
                svc.groupby(svc["Combo"].astype(str))[column].first().items()}

    def tell(span: Span) -> None:
        if progress is not None:
            story = rows_of[span.members[0]].iloc[0].get("Story", "-")
            members = span.members[0] if len(span.members) == 1 else \
                f"{span.members[0]} to {span.members[-1]} ({len(span.members)} segments)"
            progress(f"Beam {members}  |  Level {story}\nCombo: DEF 100 to DEF 103 "
                     "(service)\nCheck: Deflection (Ie, long-term, live load and after "
                     "attachment of partitions)")

    def by_geometry(span: Span):
        """The checks of a span whose supports come from what meets its ends."""
        parts, roof = [], False
        single = len(span.members) == 1 and span.lengths[0] == 0.0
        for name, flip, offset, seg in zip(span.members, span.reversed, span.offsets,
                                           span.lengths):
            top, section = section_of(name)
            x, moments = moments_of(name)
            if section is None or x is None:
                return None
            seg = seg or float(x.max())
            position = offset + (seg - x if flip else x)
            parts.append(SpanPart(name, section, position, moments))
            roof = roof or on_roof(name)
        root, rotation = None, None
        if single:
            status = str(rows_of[span.members[0]].iloc[0].get("SupportStatus", ""))
            if "Free at PtJ" in status:
                root = "start"
            elif "Free at PtI" in status:
                root = "end"
        elif span.start_supported != span.end_supported:
            root = "start" if span.start_supported else "end"
        if root:
            index = 0 if root == "start" else -1
            name, flip = span.members[index], span.reversed[index]
            support_at_i = (root == "start") != flip
            seg = span.lengths[index] or float(parts[index].x.max() - parts[index].x.min())
            rotation = rotation_of(name, support_at_i, seg)
            parts[index].compression_zone = "left" if support_at_i else "right"
        return span_deflection(parts, root, rotation, roof, long_limit_divisor, stages)

    def by_structure(chain: Span):
        """The checks of a whole beam line, cut where it is supported.

        None when the data of a member is missing, or when nothing shows a
        support: the line is then checked by ``by_geometry``.
        """
        pieces = []
        position = 0.0
        for name, flip, seg in zip(chain.members, chain.reversed, chain.lengths):
            top, section = section_of(name)
            x, moments = moments_of(name)
            if section is None or x is None or name not in network.beams:
                return None
            shear = shear_of(name, len(x))
            seg = seg or float(x.max())
            if flip:   # the member runs against the line: turn it round
                along = (seg - x)[::-1]
                moments = {c: m[::-1] for c, m in moments.items()}
                shear = None if shear is None else -shear[::-1]
            else:
                along = x
            i, j = network.beams[name]
            pieces.append({"name": name, "flip": flip, "length": seg, "section": section,
                           "x": position + along, "moments": moments, "shear": shear,
                           "zones": _zones(along), "from": j if flip else i,
                           "to": i if flip else j})
            position += seg
        count = len(pieces)
        own = set(chain.members)
        cuts = set()
        if network.held_at(pieces[0]["from"], own):
            cuts.add((0, 0))
        if network.held_at(pieces[-1]["to"], own):
            cuts.add((count, 0))
        for k in range(1, count):
            if pieces[k]["from"] in network.supports:
                cuts.add((k, 0))
        if all(q["shear"] is not None for q in pieces):
            shears = statics_shear([(q["x"], q["moments"][COMBO_FULL], q["shear"])
                                    for q in pieces])
            if shears is not None:   # a support pushing up inside the line
                cuts |= {c for c in support_cuts([q["x"] for q in pieces], shears)
                         if c not in ((0, 0), (count, 0))}
        if not cuts:
            return None
        cuts = sorted(cuts)
        first, last = (0, 0), (count, 0)
        stretches = []                         # (from, to, root of a cantilever or None)
        if cuts[0] != first:
            stretches.append((first, cuts[0], "end"))
        stretches += [(a, b, None) for a, b in zip(cuts, cuts[1:])]
        if cuts[-1] != last:
            stretches.append((cuts[-1], last, "start"))
        found: dict[str, DeflectionResult] = {}
        for (k0, i0), (k1, i1), root in stretches:
            parts, used = [], []
            for k in range(k0, min(k1, count - 1) + 1):
                piece = pieces[k]
                lo = i0 if k == k0 else 0
                hi = i1 if k == k1 else len(piece["x"])
                if hi - lo < 2:
                    continue
                parts.append(SpanPart(
                    piece["name"], piece["section"], piece["x"][lo:hi],
                    {c: m[lo:hi] for c, m in piece["moments"].items()},
                    zones=piece["zones"][lo:hi]))
                used.append((k, lo, hi))
            if not parts:
                continue
            rotation = None
            if root:
                index = 0 if root == "start" else -1
                k, lo, hi = used[index]
                piece = pieces[k]
                at_joint = lo == 0 if root == "start" else hi == len(piece["x"])
                support_at_i = (root == "start") != piece["flip"]
                if at_joint:   # the rotation of the analysis is known at the joints only
                    rotation = rotation_of(piece["name"], support_at_i, piece["length"])
                parts[index].compression_zone = "left" if support_at_i else "right"
            roof = any(on_roof(part.name) for part in parts)
            for name, result in span_deflection(parts, root, rotation, roof,
                                                long_limit_divisor, stages).items():
                if name not in found or result.ratio > found[name].ratio:
                    found[name] = result      # a member cut in two keeps its worse stretch
        return found if set(found) >= own else None

    checked: dict[str, DeflectionResult] = {}
    if has_lines:
        from design.beam_carriers import BeamNetwork

        network = BeamNetwork(connectivity, points)
        for chain in beam_spans(connectivity, names, points, through_supports=True):
            tell(chain)
            found = by_structure(chain)
            if found is not None:
                checked.update(found)
    for span in spans:
        if all(name in checked for name in span.members):
            continue
        tell(span)
        found = by_geometry(span)
        if found is None:
            for name in span.members:
                if name not in checked:
                    out.loc[rows_of[name].index, "Deflection_Check"] = "NO SERVICE LOADS"
            continue
        for name, result in found.items():
            checked.setdefault(name, result)

    for name, result in checked.items():
        index = rows_of[name].index
        values = {
            "Defl_Ie_left": round(result.ie["left"]),
            "Defl_Ie_mid": round(result.ie["mid"]),
            "Defl_Ie_right": round(result.ie["right"]),
            "Defl_lambda": round(result.lam, 3),
            "Defl_live_mm": round(result.live, 2),
            "Defl_live_limit_mm": round(result.live_limit, 2),
            "Defl_roof_mm": None if result.roof is None else round(result.roof, 2),
            "Defl_roof_limit_mm": (None if result.roof_limit is None
                                   else round(result.roof_limit, 2)),
            "Defl_long_mm": round(result.long_term, 2),
            "Defl_long_limit_mm": round(result.long_limit, 2),
            "Defl_creep_mm": round(result.creep, 2),
            "Defl_total_mm": round(result.total, 2),
            "Defl_ratio": round(result.ratio, 3),
        }
        if staged:
            values.update({
                "Defl_first_load_mm": round(result.at_first_load, 2),
                "Defl_at_partitions_mm": round(result.at_partitions, 2),
                "Defl_deducted_mm": round(result.deducted, 2),
                "Defl_stages": stages.describe(),
            })
        # the target ratio of this member type (1.0 unless the engineer set one)
        status = rows_of[name].iloc[0].get("SupportStatus", "")
        target = dcr_targets.limit(dcr_targets.beam_type(status), dcr_targets.DEFLECTION)
        passed = result.ratio <= target + 1e-9
        values["Deflection_Check"] = "PASS" if passed else (
            "FAIL" if target >= 1.0 else f"FAIL: above the target ratio {target:g}")
        for key, value in values.items():
            out.loc[index, key] = value
        if not passed:
            passing = out.loc[index, "Design_Status"].astype(str).eq("OK").to_numpy()
            out.loc[index[passing], "Design_Status"] = DEFLECTION_FAILED
    order = [c for c in out.columns if c != "Design_Status"] + ["Design_Status"]
    return out[order]
