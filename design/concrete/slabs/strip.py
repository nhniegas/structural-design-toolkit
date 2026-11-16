"""A 1 m wide slab strip: steel for a moment, bar spacing, minimum steel, shear and thickness.

NSCP 2015 (ACI 318M-14):
* flexure, rectangular stress block: NSCP 422.2 (ACI 22.2); phi 0.90 tension-controlled,
  NSCP Table 421.2.2 (ACI Table 21.2.2); two-way slabs need a net tensile strain of at
  least 0.004, NSCP 408.3.3.1 (ACI 8.3.3.1);
* minimum flexural steel: NSCP Table 407.6.1.1 (one-way) and Table 408.6.1.1 (two-way),
  ACI Tables 7.6.1.1 / 8.6.1.1: 0.0020 Ag for fy < 420, else the greater of
  0.0018 x 420 / fy Ag and 0.0014 Ag; shrinkage and temperature steel of one-way slabs
  the same ratios, NSCP Table 424.4.3.2 (ACI Table 24.4.3.2);
* spacing: one-way main bars the lesser of 3h and 450 mm, NSCP 407.7.2.3 (ACI 7.7.2.3),
  their shrinkage bars 5h and 450 mm, NSCP 407.7.2.4 (ACI 7.7.2.4); two-way bars 2h and
  450 mm at critical sections, NSCP 408.7.2.2 (ACI 8.7.2.2); one-way bars also by crack
  control, NSCP 407.7.2.2 and 424.3.2 (ACI 7.7.2.2, 24.3.2); and the office maximum;
* shear without stirrups: phi Vc = 0.75 x 0.17 lambda sqrt(f'c) b d, NSCP 422.5.5.1
  (ACI 22.5.5.1), stirrups are not used in these slabs (NSCP 407.6.3.1);
* thickness: NSCP Table 407.3.1.1 with 407.3.1.1.1 (one-way), Tables 408.3.1.1 and
  408.3.1.2 with 408.3.1.2.1 (two-way).

Units: mm, MPa, kN-m per m, kN per m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from design.criteria.code_config import CODE, AciCode

ROUND_SPACING = 25.0     # spacings are rounded down to 25 mm
MIN_SPACING = 75.0       # a closer spacing is flagged (practical placing of slab bars)


def bar_area(diameter: float) -> float:
    return math.pi * diameter ** 2 / 4.0


def as_min_ratio(fy: float, code: AciCode = CODE) -> float:
    """As,min / Ag of a slab: NSCP Tables 407.6.1.1, 408.6.1.1, 424.4.3.2."""
    s = code.slab
    if fy < s.as_min_fy_limit:
        return s.as_min_low_fy
    return max(s.as_min_ratio_420 * s.as_min_fy_limit / fy, s.as_min_floor)


def steel_for_moment(mu: float, d: float, fc: float, fy: float, code: AciCode = CODE
                     ) -> tuple[float, float]:
    """(As required in mm2 per m, net tensile strain) for Mu (kN-m per m) on a 1 m strip
    of effective depth d, with phi 0.90. As is inf when the section cannot carry Mu."""
    if mu <= 0:
        return 0.0, math.inf
    b = 1000.0
    phi = code.strength.tension_controlled
    alpha = code.material.stress_block_alpha
    rn = mu * 1e6 / (phi * b * d * d)
    inside = 1.0 - 2.0 * rn / (alpha * fc)
    if inside < 0:
        return math.inf, 0.0
    rho = alpha * fc / fy * (1.0 - math.sqrt(inside))
    steel = rho * b * d
    a = steel * fy / (alpha * fc * b)
    c = a / code.beta1(fc)
    strain = code.material.concrete_ultimate_strain * (d - c) / c
    return steel, strain


def spacing_for(steel: float, bar: float, limit: float) -> float:
    """Largest spacing (mm, rounded down to 25 mm, at most ``limit``) giving ``steel``
    mm2 per m with bars of ``bar`` mm."""
    if steel <= 0:
        return math.floor(limit / ROUND_SPACING) * ROUND_SPACING
    s = min(limit, 1000.0 * bar_area(bar) / steel)
    return math.floor(s / ROUND_SPACING + 1e-9) * ROUND_SPACING


def phi_vc(d: float, fc: float, code: AciCode = CODE, lam: float = 1.0) -> float:
    """phi Vc of a 1 m strip in kN per m (NSCP 422.5.5.1)."""
    return code.strength.shear * code.slab.shear_vc_coeff * lam * math.sqrt(fc) * 1000.0 * d / 1e3


@dataclass(frozen=True)
class BarResult:
    """The bars of one location of a strip."""

    location: str        # e.g. "Short, top, continuous edge"
    mu: float            # kN-m per m
    d: float             # mm
    as_required: float   # mm2 per m from Mu
    as_min: float        # mm2 per m
    as_design: float     # the larger
    spacing_limit: float  # mm, code and office
    spacing: float       # mm, chosen
    as_provided: float   # mm2 per m
    strain: float        # net tensile strain at As required
    phi_mn: float        # kN-m per m with As provided
    passed: bool
    note: str = ""


def design_bars(location: str, mu: float, d: float, h: float, fc: float, fy: float, bar: float,
                spacing_limit: float, min_strain: float = 0.0, code: AciCode = CODE
                ) -> BarResult:
    """Bars of ``bar`` mm for Mu (kN-m per m) at effective depth d in a slab of
    thickness h: the steel of Mu or the minimum, at the largest spacing within
    ``spacing_limit``, rounded down to 25 mm."""
    required, strain = steel_for_moment(mu, d, fc, fy, code)
    minimum = as_min_ratio(fy, code) * 1000.0 * h
    design = max(required, minimum)
    note = ""
    if math.isinf(design):
        return BarResult(location, mu, d, required, minimum, design, spacing_limit, 0.0, 0.0,
                         0.0, 0.0, False, "THE SLAB CANNOT CARRY THE MOMENT: THICKER SLAB")
    spacing = spacing_for(design, bar, spacing_limit)
    provided = 1000.0 * bar_area(bar) / spacing if spacing > 0 else 0.0
    alpha = code.material.stress_block_alpha
    a = provided * fy / (alpha * fc * 1000.0)
    phi_mn = code.strength.tension_controlled * provided * fy * (d - a / 2.0) / 1e6
    passed = spacing >= MIN_SPACING
    if not passed:
        note = f"BARS CLOSER THAN {MIN_SPACING:.0f} mm: LARGER BAR OR THICKER SLAB"
    if required > 0 and strain < max(min_strain, code.strength.tension_controlled_strain):
        if strain < min_strain:
            passed = False
            note = f"NET TENSILE STRAIN {strain:.4f} UNDER {min_strain}: THICKER SLAB"
        else:
            passed = False
            note = f"NOT TENSION-CONTROLLED (STRAIN {strain:.4f}): THICKER SLAB"
    return BarResult(location, mu, d, required, minimum, design, spacing_limit, spacing,
                     provided, strain, phi_mn, passed, note)


def one_way_spacing_limit(h: float, fy: float, office_max: float, code: AciCode = CODE) -> float:
    """Main bars of a one-way slab: 3h and 450 mm (NSCP 407.7.2.3), crack control with
    the slab cover (NSCP 407.7.2.2, 424.3.2) and the office maximum."""
    s = code.slab
    crack = code.crack_control_spacing(fy, s.cover)
    return min(s.one_way_max_spacing_h * h, s.max_spacing_cap, crack, office_max)


def temperature_spacing_limit(h: float, office_max: float, code: AciCode = CODE) -> float:
    """Shrinkage and temperature bars: 5h and 450 mm (NSCP 407.7.2.4)."""
    s = code.slab
    return min(s.temperature_max_spacing_h * h, s.max_spacing_cap, office_max)


def two_way_spacing_limit(h: float, office_max: float, code: AciCode = CODE) -> float:
    """Two-way bars at critical sections: 2h and 450 mm (NSCP 408.7.2.2)."""
    s = code.slab
    return min(s.two_way_critical_spacing_h * h, s.max_spacing_cap, office_max)


def fy_factor(fy: float, code: AciCode = CODE) -> float:
    """(0.4 + fy / 700) for fy other than 420 MPa (NSCP 407.3.1.1.1)."""
    s = code.slab
    return s.h_fy_base + fy / s.h_fy_divisor


def one_way_min_thickness(span: float, support: str, fy: float, code: AciCode = CODE) -> float:
    """Minimum h (mm) of a one-way slab of span ``span`` (mm) by NSCP Table 407.3.1.1;
    ``support`` is "simple", "one end", "both ends" or "cantilever"."""
    s = code.slab
    divisor = {"simple": s.one_way_h_simple, "one end": s.one_way_h_one_end,
               "both ends": s.one_way_h_both_ends, "cantilever": s.one_way_h_cantilever}[support]
    return span / divisor * fy_factor(fy, code)


def two_way_min_thickness(ln_long: float, beta: float, alpha_fm: float, fy: float,
                          discontinuous_without_edge_beam: bool, exterior: bool = False,
                          code: AciCode = CODE) -> float:
    """Minimum h (mm) of a two-way slab with beams on all sides, NSCP Table 408.3.1.2:
    ``ln_long`` the clear long span (mm), ``beta`` long / short clear span, ``alpha_fm``
    the mean alpha_f of its edge beams. Under alpha_fm 0.2 the table of slabs without
    interior beams (NSCP Table 408.3.1.1, fy 420, without drop panels) applies. With a
    discontinuous edge whose edge beam has alpha_f under 0.8, (b) and (d) are increased
    10 percent (NSCP 408.3.1.2.1)."""
    s = code.slab
    if alpha_fm <= s.alpha_fm_low:
        column = 2 if not exterior else (1 if not discontinuous_without_edge_beam else 0)
        return max(ln_long / flat_divisor(fy, column), s.flat_h_floor)
    top = ln_long * (s.two_way_h_fy_base + fy / s.two_way_h_fy_divisor)
    if alpha_fm <= s.alpha_fm_high:
        formula = top / (s.two_way_h_mid_constant + s.two_way_h_mid_beta * beta
                         * (alpha_fm - s.alpha_fm_low))
        floor = s.two_way_h_mid_floor
    else:
        formula = top / (s.two_way_h_stiff_constant + s.two_way_h_stiff_beta * beta)
        floor = s.two_way_h_stiff_floor
    if discontinuous_without_edge_beam:
        formula *= s.discontinuous_edge_increase
    return max(formula, floor)


# NSCP Table 408.3.1.1, slabs without drop panels: ln / divisor for fy (MPa) of
# (exterior without edge beams, exterior with edge beams, interior)
FLAT_DIVISORS = {280.0: (33.0, 36.0, 36.0), 420.0: (30.0, 33.0, 33.0), 520.0: (28.0, 31.0, 31.0)}


def flat_divisor(fy: float, column: int) -> float:
    """ln / h of NSCP Table 408.3.1.1 (without drop panels) at fy, the thickness
    interpolated linearly between the fy of the table (its note 2)."""
    levels = sorted(FLAT_DIVISORS)
    fy = min(max(fy, levels[0]), levels[-1])
    for low, high in zip(levels, levels[1:]):
        if low <= fy <= high:
            t_low = 1.0 / FLAT_DIVISORS[low][column]
            t_high = 1.0 / FLAT_DIVISORS[high][column]
            return 1.0 / (t_low + (fy - low) / (high - low) * (t_high - t_low))
    return FLAT_DIVISORS[levels[-1]][column]
