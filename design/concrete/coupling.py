"""
Coupling beams of special structural walls (NSCP 2015 418.10.7) and the degree
of coupling of a pair of walls.

A coupling beam is a beam that joins two separate walls (not two legs of one
wall). By 418.10.7:

* ln/h ≥ 4: designed as a beam of a special moment frame (418.6), the wall
  boundary taken as the column (418.10.7.1);
* ln/h < 2 and Vu > 0.33 λ √f'c Acw: two intersecting groups of diagonal bars
  (418.10.7.2), Vn = 2 Avd fy sin α ≤ 0.83 √f'c Acw (Eq. 418.10.7.4), φ = 0.85
  (421.2.4.3);
* otherwise: either the diagonal bars or the beam rules of 418.6.3 to 418.6.5
  (418.10.7.3).

The degree of coupling of two walls under a lateral load is the share of the
overturning moment the coupling beams carry by the axial couple of the walls:
T L / (T L + M1 + M2), with T the sum of the coupling beam shears over the
height, L the distance between the wall centroids, M1 and M2 the walls' own
base moments about the axis across the line between them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PHI_DIAGONAL = 0.85     # NSCP 421.2.4.3, diagonally reinforced coupling beams
MIN_DIAGONAL_BARS = 4   # each group, 418.10.7.4(b)


@dataclass
class CouplingResult:
    name: str
    walls: tuple[str, str]
    story: str
    ln: float
    h: float
    b: float
    vu: float
    rule: str
    clause: str
    avd: float = 0.0               # area of one diagonal group, mm2
    bars: int = 0                  # diagonal bars per group
    vn_max: float = 0.0            # kN
    passed: bool = True
    note: str = ""


def diagonal_angle(h: float, ln: float, inset: float) -> float:
    """α: the slope of the diagonal groups, their centroids ``inset`` from the faces
    at the wall faces."""
    return math.atan2(max(h - 2.0 * inset, 1.0), ln)


def design_coupling_beam(name: str, walls: tuple[str, str], story: str, ln: float, h: float,
                         b: float, vu: float, fc: float, fy: float, bar: float,
                         inset: float = 100.0) -> CouplingResult:
    """The rule of 418.10.7 for one coupling beam, and the diagonal bars when they are
    required or allowed (Vu in kN; dimensions in mm)."""
    root = math.sqrt(fc)
    acw = b * h
    ratio = ln / h if h else math.inf
    vn_max = 0.83 * root * acw / 1e3
    out = CouplingResult(name, walls, story, ln, h, b, vu, "", "", vn_max=vn_max)
    if ratio >= 4.0:
        out.rule = "beam of a special moment frame (wall boundary as the column)"
        out.clause = "NSCP 418.10.7.1, 418.6"
        out.note = "design it with sdt beams, SMRF on"
        return out
    diagonal_required = ratio < 2.0 and vu * 1e3 > 0.33 * root * acw
    alpha = diagonal_angle(h, ln, inset)
    out.avd = vu * 1e3 / (PHI_DIAGONAL * 2.0 * fy * math.sin(alpha))
    area = math.pi * bar ** 2 / 4.0
    out.bars = max(MIN_DIAGONAL_BARS, math.ceil(out.avd / area - 1e-9))
    if diagonal_required:
        out.rule = (f"two groups of diagonal bars, {out.bars} x {bar:g} mm each "
                    f"(α {math.degrees(alpha):.1f}°)")
        out.clause = "NSCP 418.10.7.2, 418.10.7.4"
    else:
        out.rule = (f"diagonal bars ({out.bars} x {bar:g} mm per group) or the beam rules of "
                    "418.6.3 to 418.6.5")
        out.clause = "NSCP 418.10.7.3"
    if vu / PHI_DIAGONAL > vn_max + 1e-9:
        out.passed = False
        out.note = (f"Vu / φ = {vu / PHI_DIAGONAL:.0f} kN over 0.83 √f'c Acw = {vn_max:.0f} kN: "
                    "a deeper or wider beam is needed")
    return out


def degree_of_coupling(beam_shears: list[float], distance: float, m1: float, m2: float
                       ) -> float:
    """T L / (T L + |M1| + |M2|): T the sum of the coupling beam shears over the height
    (kN), L the distance between the wall centroids (m), M1, M2 the base moments of the
    walls about the axis across the line between them (kN-m)."""
    couple = abs(sum(beam_shears)) * distance
    total = couple + abs(m1) + abs(m2)
    return couple / total if total > 0 else 0.0
