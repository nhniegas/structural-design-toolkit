"""Moments and shears of one-way slab strips.

A strip is a row of spans along the direction of the main bars, from one discontinuous
end to the other. Each span has its clear span ln, its centre-to-centre span, and its
factored dead and live loads (kPa, so the results are per metre of width).

* When the strip meets NSCP 406.5.1 (ACI 6.5.1), (a) prismatic, (b) uniform loads,
  (c) L <= 3D, (d) at least two spans, (e) the longer of two adjacent spans within
  20 percent of the shorter, the moments are those of NSCP Table 406.5.2 and the shears
  those of Table 406.5.4 (ACI Tables 6.5.2, 6.5.4). The negative moment at an interior
  support uses the average of the clear spans on its two sides (note 1 of the table).
* Otherwise the strip is analysed as a continuous beam on knife-edge supports at the
  centre-to-centre spans, with the live load arranged by NSCP 406.4.2 (ACI 6.4.2): on
  the span and alternate spans for the positive moment, on the two adjacent spans for
  the negative moment at a support. Ends are pinned; where an end is built with a
  spandrel beam or column, the Table 406.5.2 value at that end (wu ln^2 / 24 or / 16)
  is kept as its least negative moment.
* A cantilever: wu ln^2 / 2 and wu ln at the support.

Units: m, kPa, kN-m per m, kN per m.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from design.criteria.code_config import CODE, AciCode

# discontinuous end of a strip
UNRESTRAINED = "unrestrained"   # on a wall or a support that does not restrain it
SPANDREL = "spandrel"           # built with a spandrel (edge) beam
COLUMN = "column"               # built with a column


@dataclass
class Span:
    """One span of a strip. ``ln`` clear span, ``l`` centre-to-centre span (m);
    ``wu_dead``, ``wu_live`` factored loads; ``dead``, ``live`` service loads (kPa)."""

    ln: float
    l: float
    wu_dead: float
    wu_live: float
    dead: float
    live: float
    thickness: float = 0.0


@dataclass
class SpanResult:
    """Moments of one span (kN-m per m, negatives as positive numbers) and shears."""

    m_left: float
    m_pos: float
    m_right: float
    v_left: float
    v_right: float


@dataclass
class StripResult:
    method: str                       # "NSCP Table 406.5.2" or "strip analysis (406.4.2)"
    spans: list[SpanResult]
    reasons: list[str] = field(default_factory=list)   # why 406.5.1 is not met


def coefficient_conditions(spans: list[Span], code: AciCode = CODE) -> list[str]:
    """The conditions of NSCP 406.5.1 the strip does not meet (empty when it meets all)."""
    s = code.slab
    reasons = []
    if len({round(sp.thickness, 1) for sp in spans}) > 1:
        reasons.append("406.5.1(a): spans of different thickness")
    if any(sp.live > s.live_to_dead_max * sp.dead for sp in spans):
        reasons.append("406.5.1(c): live load over three times the dead load")
    if len(spans) < 2:
        reasons.append("406.5.1(d): a single span")
    for a, b in zip(spans, spans[1:]):
        if max(a.ln, b.ln) > s.adjacent_span_ratio_max * min(a.ln, b.ln):
            reasons.append("406.5.1(e): adjacent spans differ by more than 20 percent")
            break
    return reasons


def coefficient_moments(spans: list[Span], left_end: str, right_end: str,
                        code: AciCode = CODE) -> list[SpanResult]:
    """Moments and shears of NSCP Tables 406.5.2 and 406.5.4 for a strip that meets 406.5.1."""
    s = code.slab
    n = len(spans)
    short = all(sp.ln <= s.short_span_limit / 1000.0 for sp in spans)
    out = []
    for i, sp in enumerate(spans):
        wu = sp.wu_dead + sp.wu_live

        def negative(j: int) -> float:
            """Moment at interior support j (between spans j-1 and j)."""
            ln = 0.5 * (spans[j - 1].ln + spans[j].ln)
            w = max(spans[j - 1].wu_dead + spans[j - 1].wu_live,
                    spans[j].wu_dead + spans[j].wu_live)
            if short:
                return w * ln ** 2 / s.coeff_short_spans
            first = j == 1 or j == n - 1
            if first:
                divisor = s.coeff_first_interior_two_spans if n == 2 else \
                    s.coeff_first_interior_more_spans
            else:
                divisor = s.coeff_other_supports
            return w * ln ** 2 / divisor

        def exterior(end: str) -> float:
            if short and end != UNRESTRAINED:
                return wu * sp.ln ** 2 / s.coeff_short_spans
            if end == SPANDREL:
                return wu * sp.ln ** 2 / s.coeff_ext_support_spandrel
            if end == COLUMN:
                return wu * sp.ln ** 2 / s.coeff_ext_support_column
            return 0.0

        m_left = exterior(left_end) if i == 0 else negative(i)
        m_right = exterior(right_end) if i == n - 1 else negative(i + 1)
        if i in (0, n - 1):
            end = left_end if i == 0 else right_end
            divisor = s.coeff_end_unrestrained if end == UNRESTRAINED else s.coeff_end_integral
        else:
            divisor = s.coeff_interior_positive
        m_pos = wu * sp.ln ** 2 / divisor
        half = wu * sp.ln / 2.0
        # exterior face of the first interior support: the end spans' inner ends
        v_left = half * (s.shear_first_interior if n > 1 and i == n - 1 else 1.0)
        v_right = half * (s.shear_first_interior if n > 1 and i == 0 else 1.0)
        out.append(SpanResult(m_left, m_pos, m_right, v_left, v_right))
    return out


def _continuous_beam(lengths: list[float], loads: list[float]) -> tuple[np.ndarray, list]:
    """Support moments (hogging positive) of a continuous beam pinned at every support,
    by the three-moment equation, under uniform loads per span."""
    n = len(lengths)
    if n == 1:
        return np.zeros(2), [(lengths[0], loads[0], 0.0, 0.0)]
    size = n - 1
    a = np.zeros((size, size))
    rhs = np.zeros(size)
    for k in range(size):          # support k+1, between spans k and k+1
        l1, l2 = lengths[k], lengths[k + 1]
        a[k, k] = 2.0 * (l1 + l2)
        if k > 0:
            a[k, k - 1] = l1
        if k < size - 1:
            a[k, k + 1] = l2
        rhs[k] = (loads[k] * l1 ** 3 + loads[k + 1] * l2 ** 3) / 4.0
    inner = np.linalg.solve(a, rhs)
    support = np.concatenate([[0.0], inner, [0.0]])
    return support, [(lengths[i], loads[i], support[i], support[i + 1]) for i in range(n)]


def _span_extremes(l: float, w: float, m_left: float, m_right: float) -> tuple[float, float, float]:
    """(largest sagging moment, shear at the left, shear at the right) of a span."""
    v_left = w * l / 2.0 + (m_left - m_right) / l
    x = min(max(v_left / w, 0.0), l) if w > 0 else 0.0
    sag = v_left * x - w * x * x / 2.0 - m_left
    v_right = w * l - v_left
    return max(sag, 0.0), v_left, v_right


def strip_analysis(spans: list[Span], left_end: str, right_end: str,
                   code: AciCode = CODE) -> list[SpanResult]:
    """Continuous strip with the live load patterns of NSCP 406.4.2."""
    s = code.slab
    n = len(spans)
    lengths = [sp.l for sp in spans]
    dead = [sp.wu_dead for sp in spans]
    live = [sp.wu_live for sp in spans]
    patterns = []
    for start in (0, 1):            # span and alternate spans loaded (positive moment)
        patterns.append([dead[i] + (live[i] if i % 2 == start else 0.0) for i in range(n)])
    for j in range(1, n):           # the two spans beside support j loaded (negative moment)
        patterns.append([dead[i] + (live[i] if i in (j - 1, j) else 0.0) for i in range(n)])
    patterns.append([dead[i] + live[i] for i in range(n)])
    support = np.zeros(n + 1)
    pos = np.zeros(n)
    v_l = np.zeros(n)
    v_r = np.zeros(n)
    for loads in patterns:
        moments, parts = _continuous_beam(lengths, loads)
        support = np.maximum(support, moments)
        for i, (l, w, ml, mr) in enumerate(parts):
            sag, vl, vr = _span_extremes(l, w, ml, mr)
            pos[i] = max(pos[i], sag)
            v_l[i] = max(v_l[i], vl)
            v_r[i] = max(v_r[i], vr)
    first, last = spans[0], spans[-1]
    for k, (end, sp) in enumerate(((left_end, first), (right_end, last))):
        wu = sp.wu_dead + sp.wu_live
        least = 0.0
        if end == SPANDREL:
            least = wu * sp.ln ** 2 / s.coeff_ext_support_spandrel
        elif end == COLUMN:
            least = wu * sp.ln ** 2 / s.coeff_ext_support_column
        idx = 0 if k == 0 else n
        support[idx] = max(support[idx], least)
    return [SpanResult(float(support[i]), float(pos[i]), float(support[i + 1]), float(v_l[i]),
                       float(v_r[i])) for i in range(n)]


def cantilever(span: Span) -> SpanResult:
    """A cantilever strip: the moment and shear at its support (left)."""
    wu = span.wu_dead + span.wu_live
    return SpanResult(wu * span.ln ** 2 / 2.0, 0.0, 0.0, wu * span.ln, 0.0)


def design_strip(spans: list[Span], left_end: str, right_end: str,
                 code: AciCode = CODE) -> StripResult:
    """Moments and shears of a strip: NSCP Table 406.5.2 when 406.5.1 is met, else the
    strip analysis with the live load patterns of 406.4.2."""
    reasons = coefficient_conditions(spans, code)
    if not reasons:
        return StripResult("NSCP Table 406.5.2",
                           coefficient_moments(spans, left_end, right_end, code))
    return StripResult("strip analysis, live load by NSCP 406.4.2",
                       strip_analysis(spans, left_end, right_end, code), reasons)
