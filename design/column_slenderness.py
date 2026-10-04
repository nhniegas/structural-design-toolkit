"""Slenderness of columns along their length (ACI 318M-14 6.2.5 and 6.6.4.5).

Second-order effects have two parts:

* **Sway (P-Delta).** The storey drifts. It is in the forces already: the
  ETABS analysis runs with iterative P-delta on cracked stiffness, an elastic
  second-order analysis (ACI 6.7). No sway magnifier is computed here.
* **Member (P-delta).** The bow of the column between its ends. ETABS does not
  capture it unless every column is subdivided. It is added here by the
  moment magnification of ACI 6.6.4.5, on the forces of the analysis.

For each column, each bending axis and each load combination:

1. ``k lu / r`` with ``lu`` the clear height in that direction, ``r`` = 0.30 h
   (0.25 D for a circular column) and ``k`` from the alignment chart of a
   braced column (ACI R6.2.5), at most 1.0;
2. slenderness may be neglected when ``k lu / r <= 34 + 12 (M1/M2) <= 40``;
3. otherwise ``Pc = pi^2 (EI)eff / (k lu)^2`` with ``(EI)eff = 0.4 Ec Ig /
   (1 + beta_dns)``, ``Cm = 0.6 - 0.4 (M1/M2)``, ``delta = Cm / (1 - Pu /
   0.75 Pc) >= 1`` and ``Mc = delta M2`` with ``M2 >= Pu (15 + 0.03 h)``;
4. ``Pu >= 0.75 Pc`` or ``delta > 1.4`` (ACI 6.2.6) fails: the column is too
   slender for its load, which bars cannot fix.

M1/M2 is negative for single curvature (ACI sign). ETABS gives the moment
diagram, so end moments of the same sign are single curvature.

Everything here works on plain numbers and tables; it needs no ETABS.
Forces are in kN and kN-m, lengths in mm, stresses in MPa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from design.code_config import CODE, AciCode

AXES = ("2", "3")  # bending about local 2 (over the width) and local 3 (over the depth)
NOT_SLENDER = "Not slender"
NO_COMPRESSION = "No compression"


# =============================================================================
# FORMULAS
# =============================================================================
def radius_of_gyration(dimension: float, circular: bool, code: AciCode = CODE) -> float:
    """r of the gross section: 0.30 h, or 0.25 D for a circular column."""
    cfg = code.column_slenderness
    return dimension * (cfg.radius_factor_circular if circular
                        else cfg.radius_factor_rectangular)


def gross_inertia(width: float, depth: float, circular: bool) -> float:
    """Ig for bending over ``depth`` (mm4); ``width`` is ignored for a circle."""
    if circular:
        return math.pi * depth**4 / 64.0
    return width * depth**3 / 12.0


def concrete_modulus(fc: float, code: AciCode = CODE) -> float:
    return code.material.concrete_modulus_coeff * math.sqrt(max(fc, 0.0))


def braced_k(psi_a: float, psi_b: float, code: AciCode = CODE) -> float:
    """Effective length factor of a braced column from the stiffness ratios at
    its two ends (the alignment chart in the form of ACI R6.2.5), at most 1.0."""
    cfg = code.column_slenderness
    low = min(psi_a, psi_b)
    if not math.isfinite(low):
        return cfg.k_max
    by_sum = cfg.k_sum_base + cfg.k_sum_coeff * (psi_a + psi_b)
    by_min = cfg.k_min_base + cfg.k_min_coeff * low
    return float(min(by_sum, by_min, cfg.k_max))


def slenderness_limit(m1_over_m2: float, code: AciCode = CODE) -> float:
    """k lu / r below which slenderness is neglected in a braced column."""
    cfg = code.column_slenderness
    return min(cfg.limit_base + cfg.limit_moment_coeff * m1_over_m2, cfg.limit_max)


def cm_factor(m1_over_m2: float, code: AciCode = CODE) -> float:
    cfg = code.column_slenderness
    return cfg.cm_base - cfg.cm_moment_coeff * m1_over_m2


def critical_load(ec: float, inertia: float, beta_dns: float, k: float, lu: float,
                  code: AciCode = CODE) -> float:
    """Pc in kN, with (EI)eff = 0.4 Ec Ig / (1 + beta_dns)."""
    if k * lu <= 0:
        return math.inf
    stiffness = code.column_slenderness.stiffness_factor * ec * inertia / (1.0 + beta_dns)
    return math.pi**2 * stiffness / (k * lu) ** 2 / 1000.0


def minimum_moment(pu: float, dimension: float, code: AciCode = CODE) -> float:
    """M2,min in kN-m for Pu in kN and the section dimension in mm."""
    cfg = code.column_slenderness
    return max(pu, 0.0) * (cfg.min_eccentricity
                           + cfg.min_eccentricity_depth_factor * dimension) / 1000.0


# =============================================================================
# ONE COLUMN
# =============================================================================
@dataclass(frozen=True)
class AxisGeometry:
    """What the slenderness about one bending axis needs from the frame."""

    lu: float                 # mm, clear unsupported length in the bending plane
    k: float = 1.0
    k_basis: str = "k = 1.0"  # how k was found, for the report
    psi_bottom: float = math.nan
    psi_top: float = math.nan


@dataclass(frozen=True)
class ColumnSection:
    """Gross section of a column: ``depth`` along local 2, ``width`` along local 3."""

    width: float
    depth: float
    circular: bool
    fc: float

    def dimension(self, axis: str) -> float:
        """Section dimension in the plane of bending about ``axis``."""
        if self.circular:
            return self.depth
        return self.depth if axis == "3" else self.width

    def inertia(self, axis: str) -> float:
        if self.circular:
            return gross_inertia(self.depth, self.depth, True)
        if axis == "3":
            return gross_inertia(self.width, self.depth, False)
        return gross_inertia(self.depth, self.width, False)


@dataclass
class AxisResult:
    """Slenderness about one axis for one load combination."""

    slenderness: float            # k lu / r
    limit: float
    slender: bool
    cm: float = math.nan
    pc: float = math.nan          # kN
    delta: float = 1.0
    minimum: float = math.nan     # kN-m, M2,min (only when slender)
    design: float = math.nan      # kN-m, Mc (magnitude) at the governing end
    governing_end: str = ""
    status: str = NOT_SLENDER


def axis_result(end_i: float, end_j: float, pu: float, geometry: AxisGeometry,
                section: ColumnSection, axis: str, beta_dns: float,
                unknown_curvature: bool = False, code: AciCode = CODE) -> AxisResult:
    """Slenderness about ``axis`` for the end moments (kN-m, signed as ETABS
    gives them) and the axial load ``pu`` (kN, compression positive).

    ``unknown_curvature`` is for response spectrum combinations, whose end
    moments have no sign: single curvature is taken (Cm = 1.0).
    """
    cfg = code.column_slenderness
    dimension = section.dimension(axis)
    radius = radius_of_gyration(dimension, section.circular, code)
    ratio_klr = geometry.k * geometry.lu / radius if radius > 0 else math.inf
    larger, smaller = max(abs(end_i), abs(end_j)), min(abs(end_i), abs(end_j))
    governing_end = "I" if abs(end_i) >= abs(end_j) else "J"
    if larger <= 1e-9:
        m1_over_m2 = -1.0  # no moment at all: the minimum moment acts in single curvature
    else:
        single = unknown_curvature or (end_i * end_j > 0)
        m1_over_m2 = (-1.0 if single else 1.0) * smaller / larger
        if unknown_curvature:
            m1_over_m2 = -1.0
    limit = slenderness_limit(m1_over_m2, code)
    if pu <= 0:
        return AxisResult(ratio_klr, limit, False, design=larger,
                          governing_end=governing_end, status=NO_COMPRESSION)
    if ratio_klr <= limit:
        return AxisResult(ratio_klr, limit, False, design=larger,
                          governing_end=governing_end)
    minimum = minimum_moment(pu, dimension, code)
    cm = 1.0 if larger < minimum else cm_factor(m1_over_m2, code)
    design = max(larger, minimum)
    pc = critical_load(concrete_modulus(section.fc, code), section.inertia(axis), beta_dns,
                       geometry.k, geometry.lu, code)
    capacity = cfg.stiffness_reduction * pc
    if pu >= capacity:
        return AxisResult(ratio_klr, limit, True, cm, pc, math.inf, minimum,
                          cfg.max_magnifier * design, governing_end,
                          f"FAIL: Pu {pu:.0f} kN >= 0.75 Pc {capacity:.0f} kN (ACI 6.6.4.5.2)")
    delta = max(1.0, cm / (1.0 - pu / capacity))
    if delta > cfg.max_magnifier + 1e-9:
        return AxisResult(ratio_klr, limit, True, cm, pc, delta, minimum,
                          cfg.max_magnifier * design, governing_end,
                          f"FAIL: magnifier {delta:.2f} > {cfg.max_magnifier:g} (ACI 6.2.6)")
    return AxisResult(ratio_klr, limit, True, cm, pc, delta, minimum, delta * design,
                      governing_end, "Slender: moment magnified")


def _spectral_combos(forces: pd.DataFrame) -> set[str]:
    """Internal combination labels that hold a response spectrum case."""
    combos = forces["Combo"].astype(str)
    if "Spectral" in forces.columns:
        flagged = forces["Spectral"].fillna(False).astype(bool)
        return set(combos[flagged])
    # Data extracted before the flag existed: a spectrum combination is the one
    # with the eight sign permutations of P, M2 and M3.
    if "Permutation" not in forces.columns:
        return set()
    plain = combos.str.replace(r"-\d+$", "", regex=True)
    counts = pd.to_numeric(forces["Permutation"], errors="coerce").groupby(plain).nunique()
    eight = set(counts[counts == 8].index)
    return set(combos[plain.isin(eight)])


@dataclass
class MemberSlenderness:
    """Magnified forces of one column and the slenderness values behind them."""

    forces: pd.DataFrame                       # the member's forces with Mc at the ends
    records: dict[tuple[str, str], dict] = field(default_factory=dict)  # (combo, end) -> values
    failed: bool = False
    slender: bool = False


def _fields(axis: str, geometry: AxisGeometry, result: AxisResult, analysis: float) -> dict:
    slender = result.slender
    reason = result.status if not slender else ""
    return {
        f"Slender_lu{axis}_mm": geometry.lu,
        f"Slender_k{axis}": geometry.k,
        f"Slender_ratio{axis}": result.slenderness,
        f"Slender_limit{axis}": result.limit,
        f"Slender_Cm{axis}": result.cm if slender else reason,
        f"Slender_Pc{axis}_kN": result.pc if slender else reason,
        f"Slender_delta{axis}": (result.delta if math.isfinite(result.delta)
                                 else "Unstable") if slender else 1.0,
        f"Slender_Mmin{axis}_kNm": result.minimum if slender else reason,
        f"Mu{axis}_analysis_kNm": analysis,
    }


def magnify_member(forces: pd.DataFrame, section: ColumnSection,
                   geometry: dict[str, AxisGeometry], code: AciCode = CODE
                   ) -> MemberSlenderness:
    """The forces of one column with the member slenderness applied.

    ``forces`` is the member's force table with compression positive and one
    label per permutation in ``Combo`` (as the column design holds it). The
    first and the last station of each combination are its I and J ends. The
    returned table has the design moments ``Mc`` there; nothing else changes.
    The minimum moment is applied about both axes at once, which is on the
    safe side (ACI applies it about each axis separately).
    """
    cfg = code.column_slenderness
    table = forces.copy()
    stations = pd.to_numeric(table["Station"], errors="coerce")
    combos = table["Combo"].astype(str)
    spectral = _spectral_combos(table)
    has_sustained = "P_sustained" in table.columns
    out = MemberSlenderness(table)
    moment_columns = {axis: table.columns.get_loc(f"M{axis}") for axis in AXES}
    for column in ("M2", "M3"):
        table[column] = pd.to_numeric(table[column], errors="coerce").astype(float)
    for combo, index in stations.dropna().groupby(combos, sort=False).groups.items():
        rows = stations.loc[index]
        at = {"I": rows.idxmin(), "J": rows.idxmax()}
        ends = {end: table.loc[label] for end, label in at.items()}
        axial = {end: float(pd.to_numeric(row["P"], errors="coerce")) for end, row in ends.items()}
        pu = max(axial.values())
        beta = cfg.default_sustained_ratio
        beta_basis = "assumed"
        if has_sustained and pu > 0:
            sustained = max(float(pd.to_numeric(ends[end].get("P_sustained"), errors="coerce"))
                            for end in ends)
            if math.isfinite(sustained):
                beta = min(max(sustained / pu, 0.0), 1.0)
                beta_basis = "dead load share of Pu"
        shared = {"Slender_beta_dns": beta, "Slender_beta_basis": beta_basis}
        statuses = []
        per_end = {end: dict(shared) for end in ends}
        for axis in AXES:
            moment = {end: float(row[f"M{axis}"]) for end, row in ends.items()}
            result = axis_result(moment["I"], moment["J"], pu, geometry[axis], section, axis,
                                 beta, combo in spectral, code)
            if result.slender:
                out.slender = True
                end = result.governing_end
                sign = -1.0 if moment[end] < 0 else 1.0
                table.iat[table.index.get_loc(at[end]), moment_columns[axis]] = sign * result.design
            if result.status.startswith("FAIL"):
                out.failed = True
                statuses.append(f"axis {axis}: {result.status}")
            for end in ends:
                per_end[end].update(_fields(axis, geometry[axis], result, moment[end]))
        if statuses:
            check = "FAIL: SLENDERNESS - " + "; ".join(
                s.replace("FAIL: ", "") for s in statuses)
        elif any(isinstance(per_end["I"][f"Slender_Cm{axis}"], float) for axis in AXES):
            check = "PASS - slender, moments magnified"
        else:
            check = "PASS - not slender"
        for end in ends:
            per_end[end]["Slenderness_Check"] = check
            out.records[(str(combo), end)] = per_end[end]
    return out


# =============================================================================
# THE FRAME: UNBRACED LENGTHS AND EFFECTIVE LENGTH FACTORS
# =============================================================================
@dataclass
class FrameColumn:
    name: str
    bottom: str
    top: str
    length: float
    section: ColumnSection
    local_2: tuple[float, float]  # plan direction of local 2 (the depth)


@dataclass
class FrameBeam:
    name: str
    joint_i: str
    joint_j: str
    length: float
    width: float
    depth: float
    fc: float
    direction: tuple[float, float]  # unit plan direction


class FrameModel:
    """Columns and beams with their joints, for lu and k of every column.

    ``columns`` and ``beams`` are keyed by name. A column's bottom joint with
    no column and no beam on it is its footing.
    """

    def __init__(self, columns: dict[str, FrameColumn], beams: dict[str, FrameBeam],
                 code: AciCode = CODE):
        self.columns, self.beams, self.code = columns, beams, code
        self.columns_at: dict[str, list[str]] = {}
        self.beams_at: dict[str, list[str]] = {}
        self.column_above: dict[str, str] = {}
        self.column_below: dict[str, str] = {}
        for column in columns.values():
            for joint in (column.bottom, column.top):
                self.columns_at.setdefault(joint, []).append(column.name)
        for beam in beams.values():
            for joint in (beam.joint_i, beam.joint_j):
                self.beams_at.setdefault(joint, []).append(beam.name)
        self._bottoms = {c.bottom for c in columns.values()}
        self._tops = {c.top for c in columns.values()}
        bottom_of = {c.bottom: c.name for c in columns.values()}
        for column in columns.values():
            upper = bottom_of.get(column.top)
            if upper is not None and upper != column.name:
                self.column_above[column.name] = upper
                self.column_below[upper] = column.name

    # ---- beams ----
    def _alignment(self, beam: FrameBeam, direction: tuple[float, float]) -> float:
        """cos^2 of the angle between a beam and a plan direction."""
        cosine = beam.direction[0] * direction[0] + beam.direction[1] * direction[1]
        return cosine * cosine

    def _span(self, beam: FrameBeam, start: str) -> float:
        """Length of the beam line from ``start`` to the next joint with a column.

        ETABS splits a beam where another member frames into it; the pieces up
        to the next column are one span.
        """
        total, current, joint = 0.0, beam, start
        seen = set()
        while current.name not in seen:
            seen.add(current.name)
            total += current.length
            far = current.joint_j if current.joint_i == joint else current.joint_i
            if far in self.columns_at:
                break
            following = [
                self.beams[name] for name in self.beams_at.get(far, [])
                if name != current.name and self._alignment(self.beams[name],
                                                            current.direction) > 0.93
            ]
            if not following:
                break
            current, joint = following[0], far
        return total

    def beam_stiffness(self, joint: str, direction: tuple[float, float]) -> float:
        """Sum of E I / l of the beams at a joint that restrain bending in the
        vertical plane along ``direction`` (cracked I, ACI Table 6.6.3.1.1(a))."""
        cfg = self.code.column_slenderness
        total = 0.0
        for name in self.beams_at.get(joint, []):
            beam = self.beams[name]
            share = self._alignment(beam, direction)
            if share < cfg.brace_alignment:
                continue
            span = self._span(beam, joint)
            if span <= 0:
                continue
            inertia = cfg.beam_inertia_factor * gross_inertia(beam.width, beam.depth, False)
            total += share * concrete_modulus(beam.fc, self.code) * inertia / span
        return total

    def beam_depth(self, joint: str, direction: tuple[float, float]) -> float:
        """Depth of the deepest beam at a joint along ``direction``."""
        cfg = self.code.column_slenderness
        depths = [self.beams[name].depth for name in self.beams_at.get(joint, [])
                  if self._alignment(self.beams[name], direction) >= cfg.brace_alignment]
        return max(depths, default=0.0)

    # ---- columns ----
    def column_stiffness(self, joint: str, direction: tuple[float, float]) -> float:
        cfg = self.code.column_slenderness
        total = 0.0
        for name in self.columns_at.get(joint, []):
            column = self.columns[name]
            if column.length <= 0:
                continue
            section = column.section
            along_depth = (column.local_2[0] * direction[0] + column.local_2[1] * direction[1]) ** 2
            inertia = (along_depth * section.inertia("3")
                       + (1.0 - along_depth) * section.inertia("2"))
            total += (concrete_modulus(section.fc, self.code) * cfg.column_inertia_factor
                      * inertia / column.length)
        return total

    def is_footing(self, joint: str) -> bool:
        """The foot of the lowest column of a stack, with no beam on it."""
        return (joint in self._bottoms and joint not in self._tops
                and not self.beams_at.get(joint))

    def braced(self, joint: str, direction: tuple[float, float]) -> bool:
        return self.is_footing(joint) or self.beam_stiffness(joint, direction) > 0.0

    def psi(self, joint: str, direction: tuple[float, float]) -> float:
        if self.is_footing(joint):
            return self.code.column_slenderness.psi_fixed_base
        beams = self.beam_stiffness(joint, direction)
        if beams <= 0:
            return math.inf
        return self.column_stiffness(joint, direction) / beams

    def axis_geometry(self, name: str, axis: str) -> AxisGeometry:
        """lu and k of a column for bending about ``axis``.

        Bending about local 3 moves the column along local 2, so the beams
        along local 2 brace and restrain it; about local 2, the beams along
        local 3. A joint with no beam in that direction is not a brace: the
        unbraced length runs on to the next joint that has one.
        """
        column = self.columns[name]
        depth_direction = column.local_2
        direction = depth_direction if axis == "3" else (-depth_direction[1], depth_direction[0])
        length = column.length
        bottom, lower = column.bottom, name
        while not self.braced(bottom, direction) and lower in self.column_below:
            lower = self.column_below[lower]
            length += self.columns[lower].length
            bottom = self.columns[lower].bottom
        top, upper = column.top, name
        while not self.braced(top, direction) and upper in self.column_above:
            upper = self.column_above[upper]
            length += self.columns[upper].length
            top = self.columns[upper].top
        lu = max(length - self.beam_depth(top, direction), 0.0)
        psi_bottom, psi_top = self.psi(bottom, direction), self.psi(top, direction)
        if not self.braced(top, direction) or not self.braced(bottom, direction):
            return AxisGeometry(lu, self.code.column_slenderness.k_max,
                                "k = 1.0: no beam in this direction at an end",
                                psi_bottom, psi_top)
        k = braced_k(psi_bottom, psi_top, self.code)
        return AxisGeometry(lu, k, "alignment chart (ACI R6.2.5), braced", psi_bottom, psi_top)

    def geometry(self, name: str) -> dict[str, AxisGeometry]:
        return {axis: self.axis_geometry(name, axis) for axis in AXES}


def simple_geometry(length: float, top_beam_depth: float = 0.0) -> dict[str, AxisGeometry]:
    """lu and k when the frame around the column is not known: the joint to joint
    length less the beam at the top, and k = 1.0."""
    lu = max(length - top_beam_depth, 0.0)
    basis = "k = 1.0: joint coordinates not available"
    return {axis: AxisGeometry(lu, CODE.column_slenderness.k_max, basis) for axis in AXES}


def plan_direction(vector) -> tuple[float, float]:
    """Unit plan (x, y) direction of a 3D vector; (1, 0) when it is vertical."""
    x, y = float(vector[0]), float(vector[1])
    size = math.hypot(x, y)
    return (x / size, y / size) if size > 1e-9 else (1.0, 0.0)


SLENDERNESS_REPORT_FIELDS = [
    "Slender_lu3_mm", "Slender_k3", "Slender_ratio3", "Slender_limit3", "Slender_Cm3",
    "Slender_Pc3_kN", "Slender_delta3", "Slender_Mmin3_kNm", "Mu3_analysis_kNm",
    "Slender_lu2_mm", "Slender_k2", "Slender_ratio2", "Slender_limit2", "Slender_Cm2",
    "Slender_Pc2_kN", "Slender_delta2", "Slender_Mmin2_kNm", "Mu2_analysis_kNm",
    "Slender_beta_dns", "Slender_beta_basis", "Slenderness_Check",
]

__all__ = [
    "AXES", "AxisGeometry", "AxisResult", "ColumnSection", "FrameBeam", "FrameColumn",
    "FrameModel", "MemberSlenderness", "SLENDERNESS_REPORT_FIELDS", "axis_result", "braced_k",
    "cm_factor", "concrete_modulus", "critical_load", "gross_inertia", "magnify_member",
    "minimum_moment", "plan_direction", "radius_of_gyration", "simple_geometry",
    "slenderness_limit",
]

# numpy is imported for callers that pass arrays as vectors to plan_direction
_ = np
