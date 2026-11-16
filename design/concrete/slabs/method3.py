"""ACI Method 3 (the coefficient method of ACI 318-63) for two-way slabs on stiff beams or walls.

NSCP 2015 has no tables for two-way slabs on beams: NSCP 408.2.1 (ACI 8.2.1) permits any
procedure that satisfies equilibrium and geometric compatibility, and the moments and
shears of Method 3 are such a procedure, the one of the office sheets SLAB DESIGN.xls
and SLAB DESIGNER v1.0.xls.

Source of the tables: SLAB DESIGNER v1.0.xls (sheet 2way), checked cell by cell against
SLAB DESIGN.xls (sheet INPUT). The two differ in four cells, where SLAB DESIGN.xls has
typing errors that break the smooth run of its own column (Ca,neg case 9 m 0.80: 0.072,
the value of m 0.85; Ca,dl case 2 m 0.90: 0.0252; Ca,dl case 6 m 0.85: 0.0472; Cb,ll
case 9 m 0.80: 0.0174); the values here are those of v1.0. The load ratio table of v1.0
has its short and long direction labels swapped (it gives Wa = 0.06 at m = 0.5 for case
1); the values here are its "long direction" block, Wa, which published copies of the
table confirm (Wa = 0.94, 0.94, 0.76, 0.94, 0.99, 0.97, 0.86, 0.89, 0.97 at m = 0.5 and
0.85, 0.85, 0.53, 0.85, 0.96, 0.93, 0.69, 0.74, 0.92 at m = 0.65; from the web, as the
reference library has no copy of the 1963 tables).

The cases by continuous edges (the edges that run along the long span support the short
span strips, la):

    case  1 2 3 4 5 6 7 8 9
    long edges continuous   0 2 0 1 2 1 0 1 2
    short edges continuous  0 2 2 1 0 0 1 2 1

Each case is read from the tables themselves: Ca,neg is zero where the short strips have
no continuous end (cases 1, 3, 7), Cb,neg where the long strips have none (1, 5, 6), and
Wa is above 0.5 where the short strips are the stiffer (5, 6, 9).

Moments per unit width, la the clear short span, lb the clear long span, m = la / lb:
    Ma,neg = Ca,neg wu la^2           (wu: total factored load)
    Ma,pos = Ca,dl wu,d la^2 + Ca,ll wu,l la^2
and the same in b with lb. At a discontinuous edge the method takes a negative moment of
one third of the positive moment. Shear along the long edges Va = Wa wu la / 2, along
the short edges Vb = Wb wu lb / 2 (Wb = 1 - Wa). Values between the m of the tables are
interpolated linearly.
"""

from __future__ import annotations

from dataclasses import dataclass

CA_NEG = {
    1.00: (0, 0.045, 0, 0.05, 0.075, 0.071, 0, 0.033, 0.061),
    0.95: (0, 0.05, 0, 0.055, 0.079, 0.075, 0, 0.038, 0.065),
    0.90: (0, 0.055, 0, 0.06, 0.08, 0.079, 0, 0.043, 0.068),
    0.85: (0, 0.06, 0, 0.066, 0.082, 0.083, 0, 0.049, 0.072),
    0.80: (0, 0.065, 0, 0.071, 0.083, 0.086, 0, 0.055, 0.075),
    0.75: (0, 0.069, 0, 0.076, 0.085, 0.088, 0, 0.061, 0.078),
    0.70: (0, 0.074, 0, 0.081, 0.086, 0.091, 0, 0.068, 0.081),
    0.65: (0, 0.077, 0, 0.085, 0.087, 0.093, 0, 0.074, 0.083),
    0.60: (0, 0.081, 0, 0.089, 0.088, 0.095, 0, 0.08, 0.085),
    0.55: (0, 0.084, 0, 0.092, 0.089, 0.096, 0, 0.085, 0.086),
    0.50: (0, 0.086, 0, 0.094, 0.09, 0.097, 0, 0.089, 0.088),
}
CB_NEG = {
    1.00: (0, 0.045, 0.076, 0.05, 0, 0, 0.071, 0.061, 0.033),
    0.95: (0, 0.041, 0.072, 0.045, 0, 0, 0.067, 0.056, 0.029),
    0.90: (0, 0.037, 0.07, 0.04, 0, 0, 0.062, 0.052, 0.025),
    0.85: (0, 0.031, 0.065, 0.034, 0, 0, 0.057, 0.046, 0.021),
    0.80: (0, 0.027, 0.061, 0.029, 0, 0, 0.051, 0.041, 0.017),
    0.75: (0, 0.022, 0.056, 0.024, 0, 0, 0.044, 0.036, 0.014),
    0.70: (0, 0.017, 0.05, 0.019, 0, 0, 0.038, 0.029, 0.011),
    0.65: (0, 0.014, 0.043, 0.015, 0, 0, 0.031, 0.024, 0.008),
    0.60: (0, 0.01, 0.035, 0.011, 0, 0, 0.024, 0.018, 0.006),
    0.55: (0, 0.007, 0.028, 0.008, 0, 0, 0.019, 0.014, 0.005),
    0.50: (0, 0.006, 0.022, 0.006, 0, 0, 0.014, 0.01, 0.003),
}
CA_DL = {
    1.00: (0.036, 0.018, 0.018, 0.027, 0.027, 0.033, 0.027, 0.02, 0.023),
    0.95: (0.04, 0.02, 0.021, 0.03, 0.028, 0.036, 0.031, 0.022, 0.024),
    0.90: (0.045, 0.022, 0.025, 0.033, 0.029, 0.039, 0.035, 0.025, 0.026),
    0.85: (0.05, 0.024, 0.029, 0.036, 0.031, 0.042, 0.04, 0.029, 0.028),
    0.80: (0.056, 0.026, 0.034, 0.039, 0.032, 0.045, 0.045, 0.032, 0.029),
    0.75: (0.061, 0.028, 0.04, 0.043, 0.033, 0.048, 0.051, 0.036, 0.031),
    0.70: (0.068, 0.03, 0.046, 0.046, 0.035, 0.051, 0.058, 0.04, 0.033),
    0.65: (0.074, 0.032, 0.054, 0.05, 0.036, 0.054, 0.065, 0.044, 0.034),
    0.60: (0.081, 0.034, 0.062, 0.053, 0.037, 0.056, 0.073, 0.048, 0.036),
    0.55: (0.088, 0.035, 0.071, 0.056, 0.038, 0.058, 0.081, 0.052, 0.037),
    0.50: (0.095, 0.037, 0.08, 0.059, 0.039, 0.061, 0.089, 0.056, 0.038),
}
CB_DL = {
    1.00: (0.036, 0.018, 0.027, 0.027, 0.018, 0.027, 0.033, 0.023, 0.02),
    0.95: (0.033, 0.016, 0.025, 0.024, 0.015, 0.024, 0.031, 0.021, 0.017),
    0.90: (0.029, 0.014, 0.024, 0.022, 0.013, 0.021, 0.028, 0.019, 0.015),
    0.85: (0.026, 0.012, 0.022, 0.019, 0.011, 0.017, 0.025, 0.017, 0.013),
    0.80: (0.023, 0.011, 0.02, 0.016, 0.009, 0.015, 0.022, 0.015, 0.01),
    0.75: (0.019, 0.009, 0.018, 0.013, 0.007, 0.012, 0.02, 0.013, 0.007),
    0.70: (0.016, 0.007, 0.016, 0.011, 0.005, 0.009, 0.017, 0.011, 0.006),
    0.65: (0.013, 0.006, 0.014, 0.009, 0.004, 0.007, 0.014, 0.009, 0.005),
    0.60: (0.01, 0.004, 0.011, 0.007, 0.003, 0.006, 0.012, 0.007, 0.004),
    0.55: (0.008, 0.003, 0.009, 0.005, 0.002, 0.004, 0.009, 0.005, 0.003),
    0.50: (0.006, 0.002, 0.007, 0.004, 0.001, 0.003, 0.007, 0.004, 0.002),
}
CA_LL = {
    1.00: (0.036, 0.027, 0.027, 0.032, 0.032, 0.035, 0.032, 0.028, 0.03),
    0.95: (0.04, 0.03, 0.031, 0.035, 0.034, 0.038, 0.036, 0.031, 0.032),
    0.90: (0.045, 0.034, 0.035, 0.039, 0.037, 0.042, 0.04, 0.035, 0.036),
    0.85: (0.05, 0.037, 0.04, 0.043, 0.041, 0.046, 0.045, 0.04, 0.039),
    0.80: (0.056, 0.041, 0.045, 0.048, 0.044, 0.051, 0.051, 0.044, 0.042),
    0.75: (0.061, 0.045, 0.051, 0.052, 0.047, 0.055, 0.056, 0.049, 0.046),
    0.70: (0.068, 0.049, 0.057, 0.057, 0.051, 0.06, 0.063, 0.054, 0.05),
    0.65: (0.074, 0.053, 0.064, 0.062, 0.055, 0.064, 0.07, 0.059, 0.054),
    0.60: (0.081, 0.058, 0.071, 0.067, 0.059, 0.068, 0.077, 0.065, 0.059),
    0.55: (0.088, 0.062, 0.08, 0.072, 0.063, 0.073, 0.085, 0.07, 0.063),
    0.50: (0.095, 0.066, 0.088, 0.077, 0.067, 0.078, 0.092, 0.076, 0.067),
}
CB_LL = {
    1.00: (0.036, 0.027, 0.032, 0.032, 0.027, 0.032, 0.035, 0.03, 0.028),
    0.95: (0.033, 0.025, 0.029, 0.029, 0.024, 0.029, 0.032, 0.027, 0.025),
    0.90: (0.029, 0.022, 0.027, 0.026, 0.021, 0.025, 0.029, 0.024, 0.022),
    0.85: (0.026, 0.019, 0.024, 0.023, 0.019, 0.022, 0.026, 0.022, 0.02),
    0.80: (0.023, 0.017, 0.022, 0.02, 0.016, 0.019, 0.023, 0.019, 0.017),
    0.75: (0.019, 0.014, 0.019, 0.016, 0.013, 0.016, 0.02, 0.016, 0.013),
    0.70: (0.016, 0.012, 0.016, 0.014, 0.011, 0.013, 0.017, 0.014, 0.011),
    0.65: (0.013, 0.01, 0.014, 0.011, 0.009, 0.01, 0.014, 0.011, 0.009),
    0.60: (0.01, 0.007, 0.011, 0.009, 0.007, 0.008, 0.011, 0.009, 0.007),
    0.55: (0.008, 0.006, 0.009, 0.007, 0.005, 0.006, 0.009, 0.007, 0.006),
    0.50: (0.006, 0.004, 0.007, 0.005, 0.004, 0.005, 0.007, 0.005, 0.004),
}
WA = {
    1.00: (0.5, 0.5, 0.17, 0.5, 0.83, 0.71, 0.29, 0.33, 0.67),
    0.95: (0.55, 0.55, 0.2, 0.55, 0.86, 0.75, 0.33, 0.38, 0.71),
    0.90: (0.6, 0.6, 0.23, 0.6, 0.88, 0.79, 0.38, 0.43, 0.75),
    0.85: (0.66, 0.66, 0.28, 0.66, 0.9, 0.83, 0.43, 0.49, 0.79),
    0.80: (0.71, 0.71, 0.33, 0.71, 0.92, 0.86, 0.49, 0.55, 0.83),
    0.75: (0.76, 0.76, 0.39, 0.76, 0.94, 0.88, 0.56, 0.61, 0.86),
    0.70: (0.81, 0.81, 0.45, 0.81, 0.95, 0.91, 0.62, 0.68, 0.89),
    0.65: (0.85, 0.85, 0.53, 0.85, 0.96, 0.93, 0.69, 0.74, 0.92),
    0.60: (0.89, 0.89, 0.61, 0.89, 0.97, 0.95, 0.76, 0.8, 0.94),
    0.55: (0.92, 0.92, 0.69, 0.92, 0.98, 0.96, 0.81, 0.85, 0.95),
    0.50: (0.94, 0.94, 0.76, 0.94, 0.99, 0.97, 0.86, 0.89, 0.97),
}

# (continuous long edges, continuous short edges) -> case
CASES = {(0, 0): 1, (2, 2): 2, (0, 2): 3, (1, 1): 4, (2, 0): 5,
         (1, 0): 6, (0, 1): 7, (1, 2): 8, (2, 1): 9}
M_MIN = 0.5
DISCONTINUOUS_SHARE = 1.0 / 3.0   # negative moment at a discontinuous edge


def case_of(long_edges_continuous: int, short_edges_continuous: int) -> int:
    """Method 3 case (1 to 9) of a panel by how many of its long and short edges are
    continuous (0, 1 or 2 each)."""
    return CASES[(int(long_edges_continuous), int(short_edges_continuous))]


def coefficient(table: dict, m: float, case: int) -> float:
    """A coefficient of ``table`` for ratio m (0.5 to 1.0, interpolated) and case 1-9."""
    if not 1 <= case <= 9:
        raise ValueError(f"Method 3 has cases 1 to 9, not {case}")
    m = min(1.0, max(M_MIN, m))
    ratios = sorted(table)
    for low, high in zip(ratios, ratios[1:]):
        if low <= m <= high:
            share = (m - low) / (high - low)
            return table[low][case - 1] + share * (table[high][case - 1] - table[low][case - 1])
    return table[ratios[-1]][case - 1]


@dataclass(frozen=True)
class Method3Result:
    """Moments (kN-m/m) and shears (kN/m) of a panel by Method 3. ``a`` is the short
    direction (bars spanning la), ``b`` the long one. A negative moment at a continuous
    edge and at a discontinuous edge (1/3 of the positive)."""

    case: int
    m: float
    ma_neg_cont: float
    ma_pos: float
    ma_neg_disc: float
    mb_neg_cont: float
    mb_pos: float
    mb_neg_disc: float
    va: float
    vb: float
    wa: float
    coefficients: dict


def panel_moments(la: float, lb: float, wu_dead: float, wu_live: float, case: int
                  ) -> Method3Result:
    """Method 3 moments of a panel: clear spans ``la`` <= ``lb`` (m), the factored dead
    and live loads (kPa). Moments in kN-m per m, shears in kN per m."""
    if la > lb:
        raise ValueError("la is the short span")
    m = la / lb
    wu = wu_dead + wu_live
    c = {
        "Ca,neg": coefficient(CA_NEG, m, case), "Cb,neg": coefficient(CB_NEG, m, case),
        "Ca,dl": coefficient(CA_DL, m, case), "Cb,dl": coefficient(CB_DL, m, case),
        "Ca,ll": coefficient(CA_LL, m, case), "Cb,ll": coefficient(CB_LL, m, case),
        "Wa": coefficient(WA, m, case),
    }
    ma_pos = (c["Ca,dl"] * wu_dead + c["Ca,ll"] * wu_live) * la ** 2
    mb_pos = (c["Cb,dl"] * wu_dead + c["Cb,ll"] * wu_live) * lb ** 2
    wa = c["Wa"]
    return Method3Result(
        case=case, m=m,
        ma_neg_cont=c["Ca,neg"] * wu * la ** 2, ma_pos=ma_pos,
        ma_neg_disc=DISCONTINUOUS_SHARE * ma_pos,
        mb_neg_cont=c["Cb,neg"] * wu * lb ** 2, mb_pos=mb_pos,
        mb_neg_disc=DISCONTINUOUS_SHARE * mb_pos,
        va=wa * wu * la / 2.0, vb=(1.0 - wa) * wu * lb / 2.0, wa=wa, coefficients=c,
    )
