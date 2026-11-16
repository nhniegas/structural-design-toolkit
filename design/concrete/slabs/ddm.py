"""Direct Design Method, NSCP 408.10 (ACI 8.10), for two-way slab panels on beams too
flexible for Method 3, and the beam stiffness ratios it and the thickness rules use.

* alpha_f = Ecb Ib / (Ecs Is), NSCP Eq. 408.10.2.7b, the beam a T- or L-section with the
  slab on each side as far as the beam projects below it, at most 4h (NSCP 408.4.1.8);
  Is of the slab between the panel centrelines on each side of the beam.
* beta_t = Ecb C / (2 Ecs Is), NSCP Eq. 408.10.5.2a, C = sum (1 - 0.63 x/y) x^3 y / 3
  (408.10.5.2b) of the edge beam split into rectangles (408.10.5.3), the larger of the
  two ways of splitting; Is of the slab of width l2.
* Limits for the method, NSCP 408.10.2.1 to 408.10.2.7.
* Mo = qu l2 ln^2 / 8 with ln >= 0.65 l1 (NSCP 408.10.3.2, 408.10.3.2.1); interior spans
  0.65 / 0.35 (408.10.4.1), end spans by Table 408.10.4.2; column strip shares by Tables
  408.10.5.1, 408.10.5.2 and 408.10.5.5, the beam's share of the column strip by Table
  408.10.5.7.1, the rest to the middle strips (408.10.6.1). Column strip width: a quarter
  of the lesser of l1 and l2 on each side of the column line (NSCP 408.4.1.5).

Units: m, kPa, kN-m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from design.criteria.code_config import CODE, AciCode

# Table 408.10.4.2 (interior negative, positive, exterior negative) by exterior edge
END_SPAN = {
    "unrestrained": (0.75, 0.63, 0.0),     # exterior edge unrestrained
    "beams": (0.70, 0.57, 0.16),           # slab with beams between all supports
    "restrained": (0.65, 0.35, 0.65),      # exterior edge fully restrained
}
L2_L1 = (0.5, 1.0, 2.0)
CS_INTERIOR_STIFF = (0.90, 0.75, 0.45)      # Table 408.10.5.1, alpha_f1 l2/l1 >= 1.0
CS_INTERIOR_FLEXIBLE = 0.75                 # alpha_f1 l2/l1 = 0
CS_POSITIVE_STIFF = (0.90, 0.75, 0.45)      # Table 408.10.5.5
CS_POSITIVE_FLEXIBLE = 0.60
CS_EXTERIOR_BETA = 2.5                      # Table 408.10.5.2: beta_t 0 and >= 2.5
BEAM_SHARE_STIFF = 0.85                     # Table 408.10.5.7.1


def _by_ratio(values: tuple[float, float, float], ratio: float) -> float:
    ratio = min(max(ratio, L2_L1[0]), L2_L1[-1])
    for (r0, v0), (r1, v1) in zip(zip(L2_L1, values), zip(L2_L1[1:], values[1:])):
        if r0 <= ratio <= r1:
            return v0 + (ratio - r0) / (r1 - r0) * (v1 - v0)
    return values[-1]


def _by_stiffness(flexible: float, stiff: tuple, alpha_l: float, ratio: float) -> float:
    t = min(max(alpha_l, 0.0), 1.0)
    return flexible + t * (_by_ratio(stiff, ratio) - flexible)


def cs_interior_negative(alpha_l: float, ratio: float) -> float:
    """Column strip share of interior negative Mu, Table 408.10.5.1 (alpha_l = alpha_f1 l2/l1)."""
    return _by_stiffness(CS_INTERIOR_FLEXIBLE, CS_INTERIOR_STIFF, alpha_l, ratio)


def cs_positive(alpha_l: float, ratio: float) -> float:
    """Column strip share of positive Mu, Table 408.10.5.5."""
    return _by_stiffness(CS_POSITIVE_FLEXIBLE, CS_POSITIVE_STIFF, alpha_l, ratio)


def cs_exterior_negative(alpha_l: float, beta_t: float, ratio: float) -> float:
    """Column strip share of exterior negative Mu, Table 408.10.5.2: 1.0 at beta_t 0,
    the Table 408.10.5.1 values at beta_t >= 2.5, linear between."""
    at_stiff_torsion = _by_stiffness(0.75, (0.90, 0.75, 0.45), alpha_l, ratio)
    t = min(max(beta_t, 0.0), CS_EXTERIOR_BETA) / CS_EXTERIOR_BETA
    return 1.0 + t * (at_stiff_torsion - 1.0)


def beam_share(alpha_l: float) -> float:
    """Share of the column strip moment taken by the beam, Table 408.10.5.7.1."""
    return BEAM_SHARE_STIFF * min(max(alpha_l, 0.0), 1.0)


# ---- beam stiffness ---------------------------------------------------------
def flange_width(bw: float, hb: float, hs: float, sides: int, code: AciCode = CODE) -> float:
    """Width (mm) of the beam with its slab, NSCP 408.4.1.8."""
    each = min(max(hb - hs, 0.0), code.slab.beam_flange_slab_multiple * hs)
    return bw + sides * each


def flanged_inertia(bw: float, hb: float, hs: float, sides: int, code: AciCode = CODE) -> float:
    """Gross moment of inertia (mm4) of a T (sides 2) or L (sides 1) beam of web bw,
    total depth hb, slab hs (NSCP 408.4.1.8)."""
    b = flange_width(bw, hb, hs, sides, code)
    parts = [(b, hs, hs / 2.0), (bw, hb - hs, hs + (hb - hs) / 2.0)]
    area = sum(w * t for w, t, _ in parts)
    y = sum(w * t * c for w, t, c in parts) / area
    return sum(w * t ** 3 / 12.0 + w * t * (c - y) ** 2 for w, t, c in parts)


def _c_rect(x: float, y: float) -> float:
    x, y = min(x, y), max(x, y)
    if x <= 0:
        return 0.0
    return (1.0 - 0.63 * x / y) * x ** 3 * y / 3.0


def torsion_constant(bw: float, hb: float, hs: float, sides: int, code: AciCode = CODE) -> float:
    """C (mm4) of NSCP Eq. 408.10.5.2b, the larger of the two ways of splitting the T or
    L beam into rectangles (408.10.5.3)."""
    each = min(max(hb - hs, 0.0), code.slab.beam_flange_slab_multiple * hs)
    web_full = _c_rect(bw, hb) + sides * _c_rect(each, hs)
    flange_full = _c_rect(bw + sides * each, hs) + _c_rect(bw, hb - hs)
    return max(web_full, flange_full)


def alpha_f(bw: float, hb: float, hs: float, sides: int, slab_width: float,
            ec_beam: float = 1.0, ec_slab: float = 1.0, code: AciCode = CODE) -> float:
    """alpha_f = Ecb Ib / (Ecs Is), NSCP Eq. 408.10.2.7b; ``slab_width`` (mm) between the
    panel centrelines on each side of the beam (for an edge beam, to the slab edge)."""
    if hb <= hs:
        return 0.0
    i_s = slab_width * hs ** 3 / 12.0
    return ec_beam * flanged_inertia(bw, hb, hs, sides, code) / (ec_slab * i_s)


def beta_t(bw: float, hb: float, hs: float, l2: float, ec_beam: float = 1.0,
           ec_slab: float = 1.0, code: AciCode = CODE) -> float:
    """beta_t = Ecb C / (2 Ecs Is) of an edge beam (NSCP Eq. 408.10.5.2a), Is of the
    slab of width l2 (mm)."""
    if hb <= hs:
        return 0.0
    i_s = l2 * hs ** 3 / 12.0
    return ec_beam * torsion_constant(bw, hb, hs, 1, code) / (2.0 * ec_slab * i_s)


# ---- limits -----------------------------------------------------------------
def limits(spans_1: list[float], spans_2: list[float], l1: float, l2: float, dead: float,
           live: float, alpha_l1: float, alpha_l2: float, offset: float,
           code: AciCode = CODE) -> list[str]:
    """The limits of NSCP 408.10.2 a panel does not meet. ``spans_1`` and ``spans_2`` the
    centre-to-centre spans of the continuous row of panels in each direction; ``alpha_l1``
    = alpha_f1 l2^2 and ``alpha_l2`` = alpha_f2 l1^2 (408.10.2.7a); ``offset`` the largest
    column offset as a share of the span (408.10.2.4)."""
    s = code.slab
    out = []
    if min(len(spans_1), len(spans_2)) < s.ddm_min_spans:
        out.append("408.10.2.1: fewer than three continuous spans in a direction")
    for row in (spans_1, spans_2):
        if any(abs(a - b) > s.ddm_span_difference * max(a, b) for a, b in zip(row, row[1:])):
            out.append("408.10.2.2: successive spans differ by more than one third")
            break
    if max(l1, l2) > s.ddm_max_aspect * min(l1, l2):
        out.append("408.10.2.3: panel longer than twice its width")
    if offset > 0.10:
        out.append("408.10.2.4: column offset over 10 percent of the span")
    if live > s.ddm_live_to_dead_max * dead:
        out.append("408.10.2.6: live load over twice the dead load")
    if alpha_l2 > 0 and alpha_l1 > 0:
        ratio = alpha_l1 / alpha_l2
        if not s.ddm_alpha_ratio_min <= ratio <= s.ddm_alpha_ratio_max:
            out.append(f"408.10.2.7: relative beam stiffness {ratio:.2f} outside 0.2 to 5.0")
    return out


# ---- moments ----------------------------------------------------------------
@dataclass(frozen=True)
class DdmDirection:
    """Moments of one direction of a panel (kN-m for the strips, kN-m per m for the slab)."""

    mo: float
    neg_interior: float
    positive: float
    neg_exterior: float          # 0 for an interior span
    cs_width: float              # m
    ms_width: float              # m
    beam_share: float
    slab_neg_interior: float     # kN-m per m, the larger of the column strip slab and middle strip
    slab_positive: float
    slab_neg_exterior: float


def direction_moments(l1: float, l2: float, ln: float, qu: float, end_span: bool,
                      exterior: str, alpha_f1: float, beta: float, code: AciCode = CODE
                      ) -> DdmDirection:
    """DDM moments of a panel in the direction of l1: ``l1``, ``l2`` centre-to-centre spans
    (m), ``ln`` clear span, ``qu`` factored load (kPa), ``end_span`` when one end of the
    row is a discontinuous edge, ``exterior`` the kind of that edge ("beams",
    "restrained" or "unrestrained"), ``alpha_f1`` of the beams along l1, ``beta`` beta_t
    of the edge beam."""
    s = code.slab
    ln = max(ln, s.ddm_min_clear_span_ratio * l1)
    mo = qu * l2 * ln ** 2 / 8.0
    if end_span:
        k_int, k_pos, k_ext = END_SPAN[exterior]
    else:
        k_int, k_pos, k_ext = s.ddm_interior_negative, s.ddm_interior_positive, 0.0
    ratio = l2 / l1
    alpha_l = alpha_f1 * ratio
    shares = {
        "int": cs_interior_negative(alpha_l, ratio),
        "pos": cs_positive(alpha_l, ratio),
        "ext": cs_exterior_negative(alpha_l, beta, ratio),
    }
    beam = beam_share(alpha_l)
    cs_width = 0.5 * min(l1, l2)
    ms_width = max(l2 - cs_width, 1e-9)

    def per_metre(total: float, key: str) -> float:
        cs = total * shares[key]
        slab_cs = cs * (1.0 - beam) / cs_width
        ms = (total - cs) / ms_width
        return max(slab_cs, ms)

    m_int, m_pos, m_ext = k_int * mo, k_pos * mo, k_ext * mo
    return DdmDirection(mo, m_int, m_pos, m_ext, cs_width, ms_width, beam,
                        per_metre(m_int, "int"), per_metre(m_pos, "pos"),
                        per_metre(m_ext, "ext") if m_ext else 0.0)


def elastic_modulus_ratio(fc_beam: float, fc_slab: float) -> float:
    """Ecb / Ecs for normal weight concrete, Ec = 4700 sqrt(f'c) (NSCP 419.2.2.1)."""
    return math.sqrt(fc_beam / fc_slab)
