"""
Structural walls: NSCP 2015 Section 418.10 (special structural walls) and
Section 411 (walls), on the assembled section of the legs that meet.

* **The section** is the union of the legs (rectangles along each leg's line,
  as thick as the leg), with the distributed vertical bars of every leg in one
  or two curtains. Its flexure and axial strength is found by strain
  compatibility on a grid of concrete fibres and the bars (NSCP 422.2, 422.4;
  418.10.5.1: the flanges are effective), about each principal axis of the
  section, checked apart (the engineer's choice for cores under skew bending).
* **Shear** is checked leg by leg: special walls by Eq. 418.10.4.1 with αc
  from hw/lw, Vn of a leg at most 0.83 Acw √f'c and of the legs sharing the
  force at most 0.66 Acv √f'c (418.10.4.4); ordinary walls by 411.5.4
  (Vc = 0.17 λ √f'c h d, d = 0.8 lw; Vn at most 0.83 √f'c h d). φ is 0.75, or
  0.60 where Vn is under the shear at the nominal moment strength (421.2.4.1).
* **Reinforcement limits:** special walls ρl and ρt at least 0.0025
  (418.10.2.1), two curtains when Vu > 0.17 Acv λ √f'c or hw/lw ≥ 2
  (418.10.2.2), spacing at most 450 mm; ordinary walls Table 411.6.1 when Vu
  ≤ 0.5 φVc, else 411.6.2; spacing at most min(3h, 450) (411.7.2.1, 411.7.3.1).
* **Boundary elements** (special walls): the displacement method of
  418.10.6.2 (c ≥ lw / (600 (δu / hw)), δu / hw at least 0.005, for walls
  with hw/lw ≥ 2 continuous over their height) and the stress method of
  418.10.6.3 (σ = P/A + M c / I over 0.2 f'c, gross section). Both are given;
  the engineer chooses which decides when both apply. The length is the
  larger of c − 0.1 lw and c/2 (418.10.6.4(a)), rounded up to a module.

Units: N, mm, MPa; forces in kN and kN-m at the interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ES = 200_000.0
EPS_CU = 0.003
PHI_SHEAR, PHI_SHEAR_LOW = 0.75, 0.60
FIBRE = 25.0            # mm: the concrete fibres along a leg
FIBRE_ACROSS = 12.5     # mm: and across its thickness


def beta1(fc: float) -> float:
    return max(0.65, min(0.85, 0.85 - 0.05 * (fc - 28.0) / 7.0))


@dataclass(frozen=True)
class LegShape:
    """A straight leg of the wall in plan: its two ends (centre line) and thickness."""

    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def unit(self) -> tuple[float, float]:
        L = self.length or 1.0
        return (self.x2 - self.x1) / L, (self.y2 - self.y1) / L


@dataclass
class WallSection:
    """The assembled section with its fibres and bars (coordinates about the
    centroid of the concrete)."""

    legs: list[LegShape]
    fc: float
    fy: float
    cover: float
    bar: float              # vertical bar diameter
    spacing: float          # vertical bar spacing in each curtain
    curtains: int           # 1 or 2
    x: np.ndarray = field(default_factory=lambda: np.zeros(0))   # concrete fibres
    y: np.ndarray = field(default_factory=lambda: np.zeros(0))
    a: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bx: np.ndarray = field(default_factory=lambda: np.zeros(0))  # bars
    by: np.ndarray = field(default_factory=lambda: np.zeros(0))
    ba: float = 0.0
    xc: float = 0.0
    yc: float = 0.0

    def __post_init__(self):
        self._fibres()
        self._bars()

    # ---- geometry ----
    def _fibres(self) -> None:
        xs, ys, areas = [], [], []
        seen: list[LegShape] = []
        for leg in self.legs:
            u = leg.unit
            n = (-u[1], u[0])
            # fine across the thickness, so a shallow block across it is not one coarse row
            count_l = max(1, int(math.ceil(leg.length / FIBRE)))
            count_t = max(1, int(math.ceil(leg.thickness / FIBRE_ACROSS)))
            dl, dt = leg.length / count_l, leg.thickness / count_t
            # the leg runs from end to end plus half the thickness of a leg it meets
            for i in range(count_l):
                s = (i + 0.5) * dl
                for j in range(count_t):
                    t = -leg.thickness / 2 + (j + 0.5) * dt
                    px, py = leg.x1 + s * u[0] + t * n[0], leg.y1 + s * u[1] + t * n[1]
                    if any(_inside(px, py, other) for other in seen):
                        continue              # a corner already counted by another leg
                    xs.append(px)
                    ys.append(py)
                    areas.append(dl * dt)
            seen.append(leg)
        self.x, self.y, self.a = np.array(xs), np.array(ys), np.array(areas)
        total = self.a.sum()
        self.xc = float((self.a * self.x).sum() / total)
        self.yc = float((self.a * self.y).sum() / total)

    def _bars(self) -> None:
        bx, by = [], []
        for leg in self.legs:
            u = leg.unit
            n = (-u[1], u[0])
            count = max(2, int(math.floor((leg.length - 2 * self.cover) / self.spacing)) + 1)
            run = leg.length - 2 * self.cover
            offsets = ([0.0] if self.curtains == 1 else
                       [-(leg.thickness / 2 - self.cover), leg.thickness / 2 - self.cover])
            for i in range(count):
                s = self.cover + run * i / (count - 1)
                for t in offsets:
                    px, py = leg.x1 + s * u[0] + t * n[0], leg.y1 + s * u[1] + t * n[1]
                    if any(math.hypot(px - qx, py - qy) < self.bar for qx, qy in zip(bx, by)):
                        continue
                    bx.append(px)
                    by.append(py)
        self.bx, self.by = np.array(bx), np.array(by)
        self.ba = math.pi * self.bar ** 2 / 4.0

    @property
    def area(self) -> float:
        return float(self.a.sum())

    @property
    def steel(self) -> float:
        return float(len(self.bx) * self.ba)

    def inertia(self) -> tuple[float, float, float]:
        dx, dy = self.x - self.xc, self.y - self.yc
        return (float((self.a * dy ** 2).sum()), float((self.a * dx ** 2).sum()),
                float((self.a * dx * dy).sum()))

    def principal_angle(self) -> float:
        """The angle (degrees) of the first principal axis from global X."""
        ixx, iyy, ixy = self.inertia()
        return math.degrees(0.5 * math.atan2(-2 * ixy, ixx - iyy))

    # ---- strength ----
    def _resultants(self, angle: float, c: float, sign: float) -> tuple[float, float, float]:
        """Pn (tension positive), Mn about the axis at ``angle`` (the moment vector),
        and the net tensile strain εt, for a neutral axis depth c measured from the
        compression face (on the side ``sign`` of the axis)."""
        t = math.radians(angle)
        # distance across the axis: positive toward the compression side
        d_conc = sign * (-(self.x - self.xc) * math.sin(t) + (self.y - self.yc) * math.cos(t))
        d_bars = sign * (-(self.bx - self.xc) * math.sin(t) + (self.by - self.yc) * math.cos(t))
        top = float(max(d_conc.max(), d_bars.max() if len(d_bars) else -1e9))
        b1 = beta1(self.fc)
        block = d_conc >= top - b1 * c
        force_c = -0.85 * self.fc * (self.a * block)              # compression negative
        strain = EPS_CU * (d_bars - (top - c)) / c                 # compression positive
        stress = np.clip(strain * ES, -self.fy, self.fy)
        # bars inside the block take the concrete they displace off
        stress = np.where((d_bars >= top - b1 * c) & (stress > 0),
                          stress - 0.85 * self.fc, stress)
        force_s = -stress * self.ba
        pn = float(force_c.sum() + force_s.sum())
        # moment: the internal force at distance d makes M = -F d (compression at +d
        # gives a positive moment about the axis)
        mn = float(-(force_c * d_conc).sum() - (force_s * d_bars).sum()) * sign
        eps_t = float(EPS_CU * ((top - c) - d_bars.min()) / c) if len(d_bars) else 0.0
        return pn, mn, eps_t

    def interaction(self, angle: float, points: int = 60) -> list[tuple[float, float]]:
        """φPn, φMn about the axis at ``angle``, both signs of moment: a list of
        (φPn in kN, tension positive; φMn in kN-m, signed)."""
        out = []
        depth = self.depth_across(angle)
        po = 0.85 * self.fc * (self.area - self.steel) + self.fy * self.steel
        pmax = -0.80 * 0.65 * po                                  # 422.4.2.1, tied
        eps_y = self.fy / ES
        for sign in (1.0, -1.0):
            for c in np.geomspace(0.005 * depth, 3.0 * depth, points):
                pn, mn, eps_t = self._resultants(angle, float(c), sign)
                if eps_t >= 0.005:
                    phi = 0.90
                elif eps_t <= eps_y:
                    phi = 0.65
                else:
                    phi = 0.65 + 0.25 * (eps_t - eps_y) / (0.005 - eps_y)
                out.append((max(phi * pn, pmax) / 1e3, phi * mn / 1e6))
        return out

    def depth_across(self, angle: float) -> float:
        """The depth of the section across the axis at ``angle`` (mm), face to face."""
        t = math.radians(angle)
        across = -(self.x - self.xc) * math.sin(t) + (self.y - self.yc) * math.cos(t)
        return float(np.ptp(across)) + self._fibre_size(angle)

    def _fibre_size(self, angle: float) -> float:
        """The size of a fibre across the axis (to measure the section face to face)."""
        t = math.radians(angle)
        sizes = []
        for leg in self.legs:
            u = leg.unit
            across_leg = abs(-u[0] * math.sin(t) + u[1] * math.cos(t))  # leg direction across
            dl = leg.length / max(1, math.ceil(leg.length / FIBRE))
            dt = leg.thickness / max(1, math.ceil(leg.thickness / FIBRE_ACROSS))
            sizes.append(dl * across_leg + dt * (1 - across_leg))
        return max(sizes)

    def capacity_at(self, angle: float, pu: float, sign: float) -> float:
        """φMn (kN-m, positive) at the axial load ``pu`` (kN, tension positive) for
        the moment of sign ``sign`` about the axis at ``angle``."""
        curve = [(p, m) for p, m in self.interaction(angle) if m * sign > 0]
        curve.sort()
        ps = np.array([p for p, _ in curve])
        ms = np.array([abs(m) for _, m in curve])
        if not len(ps) or pu < ps.min() or pu > ps.max():
            return 0.0
        return float(np.interp(pu, ps, ms))

    def neutral_axis(self, angle: float, pu: float, sign: float) -> float:
        """The neutral axis depth c (mm) at the nominal strength with the axial load
        ``pu`` (kN, tension positive), for the boundary element check."""
        depth = self.depth_across(angle)
        lo, hi = 0.001 * depth, 3.0 * depth
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            pn, _, _ = self._resultants(angle, mid, sign)
            if pn / 1e3 > pu:        # too much tension: deepen the compression zone
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def _inside(px: float, py: float, leg: LegShape) -> bool:
    u = leg.unit
    s = (px - leg.x1) * u[0] + (py - leg.y1) * u[1]
    t = -(px - leg.x1) * u[1] + (py - leg.y1) * u[0]
    return -1e-6 <= s <= leg.length + 1e-6 and abs(t) <= leg.thickness / 2 + 1e-6


def moments_about(angle: float, mx: float, my: float) -> float:
    """The component of the moment (mx, my) about the axis at ``angle``."""
    t = math.radians(angle)
    return mx * math.cos(t) + my * math.sin(t)


# =============================================================================
# SHEAR (leg by leg)
# =============================================================================
def alpha_c(hw_lw: float) -> float:
    """NSCP 418.10.4.1: 0.25 up to hw/lw 1.5, 0.17 from 2.0, linear between."""
    if hw_lw <= 1.5:
        return 0.25
    if hw_lw >= 2.0:
        return 0.17
    return 0.25 + (0.17 - 0.25) * (hw_lw - 1.5) / 0.5


def special_shear_steel(vu: float, length: float, thickness: float, fc: float, fy: float,
                        hw_lw: float, v_at_mn: float | None = None) -> dict:
    """ρt needed for Vu (kN) on a leg of a special wall (Eq. 418.10.4.1), with φ by
    421.2.4.1, and its limits. Returns ρt, φ, Vn (kN) and whether the section is
    large enough (Vn at most 0.83 Acw √f'c, 418.10.4.4)."""
    acv = length * thickness
    root = math.sqrt(fc)
    ac = alpha_c(hw_lw)
    cap = 0.83 * acv * root / 1e3
    need_low = vu / PHI_SHEAR_LOW
    need_high = max(vu / PHI_SHEAR, v_at_mn or 0.0)
    needed = min(need_low, need_high)
    phi = PHI_SHEAR_LOW if needed == need_low and need_low < need_high else PHI_SHEAR
    rho = max((needed * 1e3 / acv - ac * root) / fy, 0.0)
    return {"rho_t": rho, "phi": phi, "Vn_needed": needed, "Vn_max": cap,
            "fits": needed <= cap + 1e-9, "alpha_c": ac}


def ordinary_shear_steel(vu: float, length: float, thickness: float, fc: float, fy: float
                         ) -> dict:
    """411.5.4: Vc = 0.17 λ √f'c h d with d = 0.8 lw; Vs = Av fy d / s; Vn at most
    0.83 √f'c h d."""
    d = 0.8 * length
    root = math.sqrt(fc)
    vc = 0.17 * root * thickness * d / 1e3
    needed = vu / PHI_SHEAR
    vs = max(needed - vc, 0.0)
    rho = vs * 1e3 / (fy * d * thickness) if vs > 0 else 0.0
    cap = 0.83 * root * thickness * d / 1e3
    return {"rho_t": rho, "phi": PHI_SHEAR, "Vn_needed": needed, "Vn_max": cap,
            "fits": needed <= cap + 1e-9, "Vc": vc}


def minimum_ratios(special: bool, vu: float, length: float, thickness: float, fc: float,
                   fy: float, bar: float, hw_lw: float) -> tuple[float, float]:
    """(ρl, ρt) minimum: 418.10.2.1 for special walls; Table 411.6.1 or 411.6.2."""
    root = math.sqrt(fc)
    acv = length * thickness
    if special:
        if vu * 1e3 <= 0.083 * acv * root:   # 418.10.2.1: Section 411.6 then applies
            special = False
        else:
            return 0.0025, 0.0025
    vc = 0.17 * root * thickness * 0.8 * length / 1e3
    if vu <= 0.5 * PHI_SHEAR * vc:
        if bar <= 16.0:
            return (0.0012, 0.0020) if fy >= 420 else (0.0015, 0.0025)
        return 0.0015, 0.0025
    rho_t = 0.0025
    rho_l = max(0.0025 + 0.5 * (2.5 - hw_lw) * (rho_t - 0.0025), 0.0025)
    return min(rho_l, rho_t), rho_t


def curtains_needed(special: bool, vu: float, length: float, thickness: float, fc: float,
                    hw_lw: float) -> int:
    """418.10.2.2 (special) or 411.7.2.3 (h over 250 mm): two curtains, else one."""
    if special and (vu * 1e3 > 0.17 * length * thickness * math.sqrt(fc) or hw_lw >= 2.0):
        return 2
    return 2 if thickness > 250.0 else 1


def max_spacing(special: bool, thickness: float, length: float, shear_steel: bool) -> float:
    if special:
        return 450.0
    limit = min(3.0 * thickness, 450.0)
    return min(limit, length / 3.0) if shear_steel else limit


def spacing_for(rho: float, thickness: float, bar: float, curtains: int, limit: float,
                step: float = 25.0) -> float:
    """The bar spacing for a ratio ρ, rounded down to ``step``, at most ``limit``."""
    area = curtains * math.pi * bar ** 2 / 4.0
    if rho <= 0:
        return limit
    s = area / (rho * thickness)
    return max(step, min(limit, math.floor(s / step) * step))


# =============================================================================
# BOUNDARY ELEMENTS (special walls)
# =============================================================================
def boundary_displacement(c: float, lw: float, hw: float, delta_u: float) -> dict:
    """418.10.6.2: needed when c ≥ lw / (600 δu/hw), δu/hw at least 0.005."""
    ratio = max(delta_u / hw, 0.005) if hw > 0 else 0.005
    limit = lw / (600.0 * ratio)
    return {"needed": c >= limit, "c": c, "limit": limit, "drift_ratio": ratio,
            "length": max(c - 0.1 * lw, c / 2.0)}


def boundary_stress(pu: float, mu: float, area: float, inertia: float, extreme: float,
                    fc: float) -> dict:
    """418.10.6.3: needed where the extreme fibre stress P/A + M y / I (gross section,
    compression positive) exceeds 0.2 f'c; may stop where it is under 0.15 f'c."""
    stress = (-pu * 1e3) / area + abs(mu) * 1e6 * extreme / inertia
    return {"needed": stress > 0.2 * fc, "stress": stress, "limit": 0.2 * fc}


def round_up(length: float, module: float) -> float:
    return math.ceil(length / module - 1e-9) * module if module > 0 else length


# =============================================================================
# DESIGN OF ONE WALL AT ONE STORY
# =============================================================================
@dataclass
class StoryForces:
    """The forces at the base of one story of a wall: per combination, the axial
    load and moments at the centroid of the assembled section, and the in-plane
    shear of each leg (kN, kN-m; P tension positive)."""

    combo: str
    p: float
    mx: float
    my: float
    leg_shear: dict[str, float]


@dataclass
class WallInputs:
    special: bool
    fc: float
    fy: float
    fyt: float
    cover: float
    bar_v: float
    bar_h: float
    module: float = 50.0           # boundary element length rounded up to this (mm)
    decides: str = "displacement"  # which method decides when both apply


@dataclass
class StoryDesign:
    story: str
    vertical_spacing: float = 0.0
    curtains: int = 2
    rho_l: float = 0.0
    horizontal: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # (ρt, s, φ)
    pm_ratio: float = 0.0
    governing: str = ""
    boundary: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # kept for the report: the forces, the designed section, Vu of each leg, hw
    forces: list = field(default_factory=list)
    section: object = None
    leg_vu: dict[str, float] = field(default_factory=dict)
    hw: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.problems and self.pm_ratio <= 1.0 + 1e-9


def design_story(story: str, legs: list[LegShape], forces: list[StoryForces], hw: float,
                 delta_u: dict[str, float], inputs: WallInputs) -> StoryDesign:
    """Design the vertical and horizontal bars of one story of a wall, and its
    boundary elements. ``hw`` is the height of the whole wall; ``delta_u`` the
    design displacement at its top in each direction (mm), for 418.10.6.2."""
    out = StoryDesign(story, forces=list(forces), hw=hw)
    t_min = min(leg.thickness for leg in legs)
    # ---- shear, leg by leg ----
    rho_l_min = 0.0
    curtains = 1
    shear_steel = False
    for leg in legs:
        vu = max((abs(f.leg_shear.get(leg.label, 0.0)) for f in forces), default=0.0)
        out.leg_vu[leg.label] = vu
        hw_lw = hw / leg.length if leg.length else 99.0
        lmin, tmin = minimum_ratios(inputs.special, vu, leg.length, leg.thickness, inputs.fc,
                                    inputs.fy, inputs.bar_v, hw_lw)
        if inputs.special:
            need = special_shear_steel(vu, leg.length, leg.thickness, inputs.fc, inputs.fyt,
                                       hw_lw)
        else:
            need = ordinary_shear_steel(vu, leg.length, leg.thickness, inputs.fc, inputs.fyt)
        if not need["fits"]:
            out.problems.append(
                f"{leg.label}: Vu {vu:.0f} kN needs Vn {need['Vn_needed']:.0f} kN, over the "
                f"limit {need['Vn_max']:.0f} kN: a thicker or longer wall is needed "
                "(418.10.4.4 / 411.5.4.3)")
        rho_t = max(need["rho_t"], tmin)
        shear_steel = shear_steel or need["rho_t"] > 0
        leg_curtains = curtains_needed(inputs.special, vu, leg.length, leg.thickness,
                                       inputs.fc, hw_lw)
        curtains = max(curtains, leg_curtains)
        limit = max_spacing(inputs.special, leg.thickness, leg.length, need["rho_t"] > 0)
        s = spacing_for(rho_t, leg.thickness, inputs.bar_h, leg_curtains, limit)
        out.horizontal[leg.label] = (rho_t, s, need["phi"])
        rho_l_min = max(rho_l_min, lmin, rho_t if inputs.special and hw_lw <= 2.0 else 0.0)
    out.curtains = curtains
    # ---- vertical bars: the spacing of the minimum, closer until P-M passes ----
    limit = max_spacing(inputs.special, t_min, min(leg.length for leg in legs), shear_steel)
    spacing = spacing_for(rho_l_min, t_min, inputs.bar_v, curtains, limit)
    while True:
        section = WallSection(legs, inputs.fc, inputs.fy, inputs.cover, inputs.bar_v, spacing,
                              curtains)
        axes = (section.principal_angle(), section.principal_angle() + 90.0)
        worst, where = 0.0, ""
        for f in forces:
            for axis in axes:
                m = moments_about(axis, f.mx, f.my)
                if abs(m) < 1e-6:
                    continue
                cap = section.capacity_at(axis, f.p, 1.0 if m > 0 else -1.0)
                ratio = abs(m) / cap if cap > 0 else math.inf
                if ratio > worst:
                    worst, where = ratio, f"{f.combo}, about the axis at {axis % 180:.0f} deg"
        if worst <= 1.0 or spacing <= 75.0:
            break
        spacing -= 25.0
    out.vertical_spacing = spacing
    out.section = section
    out.rho_l = section.steel / section.area
    out.pm_ratio, out.governing = worst, where
    if worst > 1.0:
        out.problems.append(
            f"axial load and bending {worst:.2f} of the strength even with {inputs.bar_v:g} mm "
            "bars at 75 mm: a larger bar or a thicker wall is needed")
    # ---- boundary elements (special walls) ----
    if inputs.special:
        for axis in (section.principal_angle(), section.principal_angle() + 90.0):
            t = math.radians(axis)
            across = (-(section.x - section.xc) * math.sin(t)
                      + (section.y - section.yc) * math.cos(t))
            inertia = float((section.a * across ** 2).sum())
            fibre = section._fibre_size(axis)
            lw = float(np.ptp(across)) + fibre          # to the faces, not the fibre centres
            if lw <= 2.0 * max(leg.thickness for leg in legs):
                continue        # bending out of the plane of every leg: no boundary element
            for sign in (1.0, -1.0):
                worst_c, worst_s = None, None
                for f in forces:
                    m = moments_about(axis, f.mx, f.my)
                    if m * sign <= 0:
                        continue
                    c = section.neutral_axis(axis, f.p, sign)
                    extreme = float((across * sign).max()) + fibre / 2.0
                    stress = boundary_stress(f.p, m, section.area, inertia, extreme, inputs.fc)
                    if worst_c is None or c > worst_c[0]:
                        worst_c = (c, f.combo)
                    if worst_s is None or stress["stress"] > worst_s["stress"]:
                        worst_s = {**stress, "combo": f.combo}
                if worst_c is None:
                    continue
                # the drift direction: the compression side runs across the axis
                direction = "Y" if abs(math.cos(t)) > abs(math.sin(t)) else "X"
                disp = boundary_displacement(worst_c[0], lw, hw, delta_u.get(direction, 0.0))
                applies = lw > 0 and hw / lw >= 2.0
                by_disp = disp["needed"] if applies else None
                by_stress = worst_s["needed"]
                if by_disp is None:
                    needed = by_stress
                    method = "stress (418.10.6.3); the displacement method needs hw/lw >= 2"
                elif inputs.decides == "displacement":
                    needed, method = by_disp, "displacement (418.10.6.2)"
                else:
                    needed, method = by_stress, "stress (418.10.6.3)"
                out.boundary.append({
                    "axis": axis % 180.0, "end": "+" if sign > 0 else "-", "c": worst_c[0],
                    "combo": worst_c[1], "lw": lw, "by_displacement": by_disp,
                    "c_limit": disp["limit"], "by_stress": by_stress,
                    "stress": worst_s["stress"], "needed": needed, "method": method,
                    "length": round_up(disp["length"], inputs.module) if needed else 0.0})
    return out
