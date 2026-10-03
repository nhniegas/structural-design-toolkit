"""Factored member forces built from ETABS analysis results, without ETABS design.

ETABS concrete design is not used: the design forces are put together here
from the results of each load case, so every step can be checked.

* ``combination_terms``  each load combination as its load cases and factors,
                         with nested combinations expanded
* ``combine``            the factored forces of the chosen combinations, in the
                         layout of the ``FACTORED LOADS`` sheet
* ``live_load_reduction``  NSCP 2015 Section 205 reduction factors per member
* pattern live load      ACI 318-14 6.4.2, two extra permutations per beam

Response spectrum results have no sign, so a combination with a spectrum case
is the static part plus or minus the spectrum part. Columns get the eight
sign permutations of P, M2 and M3 (the shears and torsion follow the moment
or axial force they belong to); each is one P-M2-M3 set for the interaction
check. A load case with several steps (the ASCE 7 wind cases, one step per
wind load case) gives one permutation per step. Beams only need the
envelope, so every beam combination becomes two permutations: the maximum
and the minimum of every force.

Forces are in N and moments in N-mm, as read from ETABS.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from etabs_api.workflows.tributary import HEAVY_KPA, HEAVY_TOLERANCE_M2, Tributary

FORCES = ("P", "V2", "V3", "T", "M2", "M3")
KEY = ("Story", "Label", "UniqueName", "Element", "ElemStation", "Station")

# ETABS load pattern types (eLoadPatternType)
DEAD_TYPES = {1, 2}           # Dead, Super Dead
LIVE_TYPES = {3, 4}           # Live, Reducible Live
REDUCIBLE_LIVE_TYPES = {4}
SPECTRUM_CASE_TYPES = {"Response Spectrum", "LinRespSpec"}

# Sign of each force in a column permutation: index into (sP, s2, s3).
_COLUMN_SIGN = {"P": 0, "T": 0, "M2": 1, "V3": 1, "M3": 2, "V2": 2}


# =============================================================================
# COMBINATIONS
# =============================================================================
@dataclass
class ComboTerms:
    """A linear combination: factor per load case, split into static and spectrum."""

    name: str
    static: dict[str, float] = field(default_factory=dict)
    spectral: dict[str, float] = field(default_factory=dict)

    @property
    def cases(self) -> dict[str, float]:
        return {**self.static, **self.spectral}


def combination_terms(definitions: pd.DataFrame, case_types: dict[str, str]) -> dict[str, ComboTerms]:
    """Every linear-add combination of the "Load Combination Definitions" table.

    ``case_types`` maps each load case to its ETABS type ("Linear Static",
    "Response Spectrum", ...). A combination inside a combination is expanded
    with its factor. Envelope combinations are left out: they are not one set
    of forces.
    """
    table = definitions.copy()
    table["Name"] = table["Name"].ffill().astype(str)
    table["Type"] = table["Type"].where(table["Type"].notna() & (table["Type"] != "None"))
    kinds = table.groupby("Name", sort=False)["Type"].first().to_dict()
    items: dict[str, list[tuple[str, float]]] = {}
    for name, load, factor in zip(table["Name"], table["LoadName"], table["SF"]):
        items.setdefault(name, [])
        if load is not None and str(load) not in ("", "None", "nan") and factor is not None:
            items[name].append((str(load), float(factor)))

    def expand(name: str, factor: float, seen: tuple[str, ...]) -> dict[str, float]:
        if name in seen:
            raise ValueError(f"Load combination {name!r} refers to itself.")
        if kinds.get(name) != "Linear Add":
            raise ValueError(f"Load combination {name!r} is a {kinds.get(name)} combination.")
        total: dict[str, float] = {}
        for load, sf in items[name]:
            parts = expand(load, factor * sf, seen + (name,)) if load in items else {load: factor * sf}
            for case, value in parts.items():
                total[case] = total.get(case, 0.0) + value
        return total

    out = {}
    for name in items:
        if kinds.get(name) != "Linear Add":
            continue
        try:
            cases = expand(name, 1.0, ())
        except ValueError:
            continue  # refers to an envelope; not a single set of forces
        terms = ComboTerms(name)
        for case, factor in cases.items():
            if factor == 0:
                continue
            spectral = case_types.get(case) in SPECTRUM_CASE_TYPES
            (terms.spectral if spectral else terms.static)[case] = factor
        out[name] = terms
    return out


# =============================================================================
# CASE RESULTS
# =============================================================================
@dataclass
class CaseForces:
    """Forces of every load case on the same rows (member stations)."""

    rows: pd.DataFrame                 # the KEY columns, one row per station
    values: dict[str, np.ndarray]      # case -> (rows, 6) array in FORCES order
    steps: dict[str, list[str]] = field(default_factory=dict)  # case -> its step keys

    def __contains__(self, case: str) -> bool:
        return case in self.values or case in self.steps

    def get(self, case: str) -> np.ndarray:
        """The forces of a single-step case."""
        if case not in self.values:
            raise KeyError(f"No analysis results for load case {case!r}. Run the analysis.")
        return self.values[case]

    def options(self, case: str) -> list[np.ndarray]:
        """The forces of every step of a case (one entry for a single-step case)."""
        if case in self.steps:
            return [self.values[key] for key in self.steps[case]]
        return [self.get(case)]


def case_forces(table: pd.DataFrame, label_column: str) -> CaseForces:
    """Arrange an "Element Forces - Beams/Columns" table read for load cases.

    Spectrum cases keep their "Max" rows: the magnitude of each force. A
    step-by-step static case keeps each step, as ``"<case>#<step>"``. Modal
    results are left out.
    """
    data = table.rename(columns={label_column: "Label"}).copy()
    if "StepType" not in data.columns:
        data["StepType"] = None
    step_type = data["StepType"].astype(str)
    stepped = step_type.eq("Step By Step") & data.get("CaseType", "").astype(str).eq("LinStatic")
    data = data[step_type.isin(["None", "nan", "Max", ""]) | stepped].copy()
    stepped = stepped.loc[data.index]
    data["OutputCase"] = data["OutputCase"].astype(str)
    steps: dict[str, list[str]] = {}
    if stepped.any():
        numbers = data.loc[stepped, "StepNumber"].astype(str)
        data.loc[stepped, "OutputCase"] = data.loc[stepped, "OutputCase"] + "#" + numbers
        for key in dict.fromkeys(data.loc[stepped, "OutputCase"]):
            steps.setdefault(key.split("#")[0], []).append(key)
    for column in ("Station", "ElemStation", *FORCES):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if "Element" not in data.columns:
        data["Element"] = data["UniqueName"]
    data["Element"] = data["Element"].astype(str)
    data["_order"] = data.groupby(["OutputCase", "UniqueName"]).cumcount()
    key = list(KEY)
    rows = (data[key + ["_order"]].drop_duplicates(subset=["UniqueName", "_order"])
            .sort_values(["UniqueName", "_order"]).reset_index(drop=True))
    index = pd.MultiIndex.from_frame(rows[["UniqueName", "_order"]])
    values = {}
    for case, group in data.groupby("OutputCase", sort=False):
        frame = group.set_index(["UniqueName", "_order"])[list(FORCES)]
        frame = frame[~frame.index.duplicated()].reindex(index).fillna(0.0)
        values[str(case)] = frame.to_numpy(dtype=float)
    return CaseForces(rows.drop(columns="_order"), values, steps)


# =============================================================================
# PATTERN LIVE LOAD  (beams)
# =============================================================================
def _integral(y: np.ndarray, x: np.ndarray) -> float:
    """Simpson integral over the distinct stations (exact for uniform loads)."""
    from scipy.integrate import simpson

    x_unique, first = np.unique(x, return_index=True)
    if len(x_unique) < 3:
        return float(np.trapezoid(y[first], x_unique))
    return float(simpson(y[first], x=x_unique))


def _end_moments(x: np.ndarray, m0: np.ndarray, fixed_i: bool, fixed_j: bool) -> tuple[float, float]:
    """End moments that make the end rotations zero at the fixed ends (prismatic member).

    ``m0`` is the simple-span moment (sagging positive). With t = x / L, a fixed
    end needs  integral (m0 + Mi (1 - t) + Mj t) * (1 - t) dx = 0  at i and the
    same with t at j.
    """
    length = x[-1] - x[0]
    if length <= 0 or not (fixed_i or fixed_j):
        return 0.0, 0.0
    t = (x - x[0]) / length
    ai, aj = _integral(m0 * (1 - t), x), _integral(m0 * t, x)
    if fixed_i and fixed_j:
        a = np.array([[length / 3, length / 6], [length / 6, length / 3]])
        mi, mj = np.linalg.solve(a, [-ai, -aj])
        return float(mi), float(mj)
    if fixed_i:
        return float(-3 * ai / length), 0.0
    return 0.0, float(-3 * aj / length)


def pattern_variants(
    x: np.ndarray, forces: np.ndarray, released_i: bool = False, released_j: bool = False
) -> dict[str, np.ndarray]:
    """Live load forces of one beam span for the two pattern arrangements.

    ``"pinned"``: the span loaded with its neighbours unloaded, the most
    sagging moment: the simple-span moment. ``"fixed"``: the span and its
    neighbours loaded, the most hogging moment: the fixed-end moments. Only
    M3 and V2 change; with V2 = -dM3/dx (ETABS sign) a linear change of moment
    shifts the shear by a constant. A cantilever (free end) is returned as is.
    """
    m = forces[:, 5]
    v = forces[:, 1]
    peak_m, peak_v = np.abs(m).max(initial=0.0), np.abs(v).max(initial=0.0)
    for end in (0, -1):
        free = abs(m[end]) <= 1e-6 * max(peak_m, 1.0) and abs(v[end]) <= 1e-3 * max(peak_v, 1.0)
        if free:
            return {"pinned": forces.copy(), "fixed": forces.copy()}
    length = x[-1] - x[0]
    if length <= 0:
        return {"pinned": forces.copy(), "fixed": forces.copy()}
    t = (x - x[0]) / length
    mi, mj = m[0], m[-1]
    m0 = m - (mi * (1 - t) + mj * t)
    v0 = v + (mj - mi) / length
    fi, fj = _end_moments(x, m0, not released_i, not released_j)
    pinned, fixed = forces.copy(), forces.copy()
    pinned[:, 5], pinned[:, 1] = m0, v0
    fixed[:, 5] = m0 + fi * (1 - t) + fj * t
    fixed[:, 1] = v0 - (fj - fi) / length
    return {"pinned": pinned, "fixed": fixed}


# =============================================================================
# LIVE LOAD REDUCTION  (NSCP 2015 Section 205)
# =============================================================================
def member_load(rows: pd.DataFrame, forces: np.ndarray, kind: str) -> pd.Series:
    """Total gravity load a member carries, per member (N).

    Beams: the change of shear along the span (V2 at the J end minus the I
    end). Columns: the largest axial compression.
    """
    frame = pd.DataFrame({"UniqueName": rows["UniqueName"].to_numpy(),
                          "V2": forces[:, 1], "P": forces[:, 0]})
    grouped = frame.groupby("UniqueName", sort=False)
    if kind == "column":
        return grouped["P"].apply(lambda p: max(0.0, -p.min()))
    return grouped["V2"].apply(lambda s: abs(s.iloc[-1] - s.iloc[0]))


@dataclass
class Reduction:
    """The live load reduction of one member."""

    area_m2: float
    reducible_kpa: float
    percent: float
    limit: str
    heavy_m2: float = 0.0
    levels: int = 1
    method: str = ""

    @property
    def factor(self) -> float:
        return 1.0 - self.percent / 100.0


def nscp_reduction(area_m2: float, reducible_kpa: float, dead_over_live: float,
                   one_level: bool, r: float = 0.86, heavy: bool | None = None) -> Reduction:
    """NSCP 2015 205.5: R = r (A - 14) percent, A the tributary area in m2.

    R is at most 40 % for horizontal members and members receiving load from
    one level only, 60 % for other members, and 23.1 (1 + D/L) %. Reducible
    live loads above 4.8 kPa are not reduced, except by 20 % for members
    supporting more than one floor. ``heavy`` says whether the member supports
    such a load (by default: when ``reducible_kpa`` is above 4.8).
    """
    if heavy is None:
        heavy = reducible_kpa > HEAVY_KPA
    if reducible_kpa <= 0:
        return Reduction(area_m2, reducible_kpa, 0.0, "no reducible live")
    if area_m2 <= 14.0:
        return Reduction(area_m2, reducible_kpa, 0.0, "A <= 14 m2")
    if heavy:
        percent = 0.0 if one_level else 20.0
        return Reduction(area_m2, reducible_kpa, percent, "L > 4.8 kPa")
    limits = {
        "r (A - 14)": r * (area_m2 - 14.0),
        "40 % (one level)" if one_level else "60 %": 40.0 if one_level else 60.0,
        "23.1 (1 + D/L)": 23.1 * (1.0 + max(dead_over_live, 0.0)),
    }
    limit = min(limits, key=limits.get)
    return Reduction(area_m2, reducible_kpa, round(limits[limit], 3), limit)


def load_share_tributary(
    forces: CaseForces,
    kind: str,
    unit_case: str,
    unit_kpa: float,
    geometric: dict[str, Tributary] | None = None,
    column_above: dict[str, str] | None = None,
) -> dict[str, Tributary]:
    """Tributary areas as the share of a unit load on all floors (analysis).

    The load a member takes from ``unit_case`` (``unit_kpa`` on every floor)
    is its area, as the analysis distributes it: with continuity, frame action
    and member stiffness. The heavy area and the reducible live intensity are
    taken from the ``geometric`` areas, which follow the floors' own loads.
    A column takes load from more than one level when both its own floor and
    the column above it (``column_above``) bring it load.
    """
    unit = member_load(forces.rows, forces.get(unit_case), kind)
    out = {}
    for member, load in unit.items():
        area = load / (unit_kpa * 1e-3) / 1e6  # N / (N/mm2) = mm2 -> m2
        geo = (geometric or {}).get(member, Tributary())
        levels = 1
        if kind == "column":
            above = (column_above or {}).get(member)
            from_above = unit.get(above, 0.0) if above else 0.0
            own_floor = load - from_above
            levels = 1 if min(from_above, own_floor) <= 0.05 * load else 2
        out[member] = Tributary(area_m2=area, load_kn=geo.reducible_kpa * area,
                                heavy_m2=geo.heavy_m2, levels=levels, sources=geo.sources)
    return out


def live_load_reduction(
    forces: CaseForces,
    kind: str,
    tributary: dict[str, Tributary],
    dead_cases: list[str],
    live_cases: list[str],
    method: str = "geometric",
) -> dict[str, Reduction]:
    """NSCP reduction of every member, from its tributary area.

    D/L is the member's dead load over its live load, both from the analysis.
    A beam receives load from one level; a column from as many levels as
    bring it floor area. A member supporting more than 0.5 m2 of floor with
    reducible live above 4.8 kPa takes the rule for heavy live loads.
    """
    rows = forces.rows

    def total(cases: list[str]) -> pd.Series:
        loads = [member_load(rows, forces.get(c), kind) for c in cases if c in forces.values]
        return sum(loads) if loads else pd.Series(dtype=float)

    dead, live = total(dead_cases), total(live_cases)
    out = {}
    for member in dict.fromkeys(rows["UniqueName"]):
        area = tributary.get(member, Tributary())
        live_load = float(live.get(member, 0.0)) if len(live) else 0.0
        ratio = float(dead.get(member, 0.0)) / live_load if live_load > 0 else 0.0
        one_level = kind == "beam" or area.levels <= 1
        result = nscp_reduction(area.area_m2, area.reducible_kpa, ratio, one_level,
                                heavy=area.heavy_m2 > HEAVY_TOLERANCE_M2)
        result.heavy_m2, result.levels, result.method = area.heavy_m2, area.levels, method
        if result.limit == "L > 4.8 kPa" and area.sources:
            result.limit = "L > 4.8 kPa (" + ", ".join(sorted(area.sources))[:60] + ")"
        out[member] = result
    return out


# =============================================================================
# FACTORED FORCES
# =============================================================================
def combine(
    forces: CaseForces,
    combos: list[ComboTerms],
    kind: str,
    live_cases: list[str] = (),
    reducible_cases: list[str] = (),
    reductions: dict[str, Reduction] | None = None,
    pattern_factor: float | None = None,
    releases: dict[str, tuple[bool, bool]] | None = None,
) -> pd.DataFrame:
    """Factored forces of ``combos`` in the ``FACTORED LOADS`` layout.

    ``kind`` is "beam" or "column". ``reductions`` scales the reducible live
    cases of each member. ``pattern_factor`` (beams only) adds the pattern
    live load arrangements, with the live load multiplied by it.
    """
    rows = forces.rows
    names = rows["UniqueName"].to_numpy()
    n = len(rows)

    scale = np.ones(n)
    if reductions:
        scale = np.array([reductions[m].factor if m in reductions else 1.0 for m in names])

    def scaled(case: str, value: np.ndarray) -> np.ndarray:
        return value * scale[:, None] if case in reducible_cases else value

    def case_values(case: str) -> np.ndarray:
        return scaled(case, forces.get(case))

    patterns: dict[str, dict[str, np.ndarray]] = {}
    if kind == "beam" and pattern_factor:
        groups = pd.Series(np.arange(n)).groupby(names, sort=False).indices
        stations = rows["Station"].to_numpy(dtype=float)
        for case in live_cases:
            if case not in forces.values:
                continue  # missing, or a multi-step case: no pattern
            base = case_values(case)
            out = {"pinned": base.copy(), "fixed": base.copy()}
            for member, index in groups.items():
                released = (releases or {}).get(member, (False, False))
                variants = pattern_variants(stations[index], base[index], *released)
                for label in out:
                    out[label][index] = variants[label]
            patterns[case] = {label: value * pattern_factor for label, value in out.items()}

    frames = []
    for combo in combos:
        single = {c: f for c, f in combo.static.items() if c not in forces.steps}
        stepped = {c: f for c, f in combo.static.items() if c in forces.steps}
        static = sum((f * case_values(c) for c, f in single.items()), np.zeros((n, 6)))
        spectral = sum((abs(f) * np.abs(case_values(c)) for c, f in combo.spectral.items()),
                       np.zeros((n, 6)))
        variants = [static]
        live_in = {c: f for c, f in single.items() if c in patterns}
        if live_in:
            for label in ("pinned", "fixed"):
                variants.append(static + sum(f * (patterns[c][label] - case_values(c))
                                             for c, f in live_in.items()))
        if stepped:
            choices = [[f * scaled(c, v) for v in forces.options(c)] for c, f in stepped.items()]
            variants = [base + sum(pick) for base in variants
                        for pick in itertools.product(*choices)]
        sets = []
        for base in variants:
            if not spectral.any():
                sets.append(base)
            elif kind == "beam":
                sets += [base + spectral, base - spectral]
            else:
                for signs in itertools.product((1.0, -1.0), repeat=3):
                    sign = np.array([signs[_COLUMN_SIGN[f]] for f in FORCES])
                    sets.append(base + sign * spectral)
        if kind == "beam" and len(sets) > 2:
            stacked = np.stack(sets)
            sets = [stacked.max(axis=0), stacked.min(axis=0)]
        for number, values in enumerate(sets, start=1):
            frame = rows.copy()
            frame.insert(3, "Combo", combo.name)
            frame.insert(4, "Permutation", number)
            frame[list(FORCES)] = values
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[*KEY[:3], "Combo", "Permutation", *KEY[3:], *FORCES])
    out = pd.concat(frames, ignore_index=True)
    return out.drop(columns=["Element", "ElemStation"])


# =============================================================================
# FROM ETABS
# =============================================================================
UNIT_PATTERN = "TRIBUTARY UNIT"  # 1 kPa on every floor; its forces give tributary areas
UNIT_KPA = 1.0
PATTERN_LIVE_FACTOR = 0.75


@dataclass
class ForceOptions:
    """What is added to the analysis results.

    ``reduce_live``: NSCP 2015 205.5 live load reduction of the reducible live
    patterns. ``pattern_factor``: pattern live load on the beams with this
    factor on the live load (ACI 318-14 6.4.2), or None for no pattern.
    """

    reduce_live: bool = False
    pattern_factor: float | None = None
    tributary: str = "geometric"   # or "load share"


@dataclass
class FactoredForces:
    """Factored forces of beams and columns, with how they were obtained."""

    table: pd.DataFrame                       # FACTORED LOADS layout, N and N-mm
    reductions: dict[str, Reduction] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _read(connector, name: str, cases: list[str] | None = None) -> pd.DataFrame:
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases or []))
    tables.SetLoadCombinationsSelectedForDisplay([])
    return connector._read_database_table(name)


def pattern_types(connector) -> dict[str, int]:
    """ETABS type of every load pattern (see DEAD_TYPES, LIVE_TYPES)."""
    patterns = connector.sap_model.LoadPatterns
    names = list(patterns.GetNameList(0, [])[1])
    return {str(name): int(patterns.GetLoadType(name)[0]) for name in names}


def ensure_unit_pattern(connector) -> bool:
    """Add the 1 kPa tributary-area pattern on every floor. True when the model changed.

    The pattern is of type Other: it is in no combination and not in the mass
    source, so it does not change the design.
    """
    if UNIT_PATTERN in pattern_types(connector):
        return False
    model = connector.sap_model
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)  # an analysed model is locked; its results are dropped
    connector.loads.define_pattern(UNIT_PATTERN, 8, 0.0)
    floors = _read(connector, "Floor Object Connectivity")
    with connector.extraction_units():  # N-mm: 1 kPa = 0.001 N/mm2
        for name in floors["UniqueName"].astype(str):
            connector.loads.assign_area_uniform_load(name, UNIT_PATTERN, UNIT_KPA * 1e-3)
    return True


def ensure_analysis(connector, cases: list[str]) -> bool:
    """Run the analysis when any of ``cases`` has no results. True when it ran."""
    status = {row["case"]: row["status"] for row in connector.analysis.status()}
    if all(status.get(case) == "Finished" for case in cases):
        return False
    connector.analysis.run()
    return True


def frame_releases(connector) -> dict[str, tuple[bool, bool]]:
    """Moment (M3) releases at the I and J ends of every frame that has one."""
    table = _read(connector, "Frame Assignments - Releases and Partial Fixity")
    if table.empty:
        return {}
    yes = table[["M3I", "M3J"]].astype(str).eq("Yes")
    return dict(zip(table["UniqueName"].astype(str), zip(yes["M3I"], yes["M3J"])))


def columns_above(connector) -> dict[str, str]:
    """The column standing on each column (its I joint is the other's J joint)."""
    table = _read(connector, "Column Object Connectivity")
    by_bottom = dict(zip(table["UniquePtI"].astype(str), table["UniqueName"].astype(str)))
    return {str(name): by_bottom[str(top)]
            for name, top in zip(table["UniqueName"], table["UniquePtJ"])
            if str(top) in by_bottom}


def factored_forces(
    connector,
    combo_names: list[str],
    members: list[str] | None = None,
    options: ForceOptions | None = None,
) -> FactoredForces:
    """Factored forces of the chosen combinations from the analysis results.

    Adds the tributary-area pattern and runs the analysis when needed.
    """
    options = options or ForceOptions()
    cases_table = _read(connector, "Load Case Definitions - Summary")
    case_types = dict(zip(cases_table["Name"].astype(str), cases_table["Type"].astype(str)))
    terms = combination_terms(_read(connector, "Load Combination Definitions"), case_types)
    missing = [name for name in combo_names if name not in terms]
    if missing:
        raise ValueError("These combinations are not linear-add combinations of load cases: "
                         + ", ".join(missing))
    combos = [terms[name] for name in combo_names]
    types = pattern_types(connector)
    dead = [p for p, t in types.items() if t in DEAD_TYPES and p in case_types]
    live = [p for p, t in types.items() if t in LIVE_TYPES and p in case_types]
    reducible = [p for p, t in types.items() if t in REDUCIBLE_LIVE_TYPES and p in case_types]
    notes = []

    needed = set().union(*(set(c.cases) for c in combos)) if combos else set()
    load_share = options.reduce_live and options.tributary == "load share"
    if options.reduce_live:
        needed |= {*dead, *live}
    if load_share:
        if ensure_unit_pattern(connector):
            notes.append(f"Load pattern {UNIT_PATTERN!r} (1 kPa on every floor) added to "
                         "find tributary areas; it is in no combination.")
            case_types[UNIT_PATTERN] = "Linear Static"
        needed.add(UNIT_PATTERN)
    if ensure_analysis(connector, sorted(needed)):
        notes.append("The analysis was run.")

    geometry = None
    if options.reduce_live:
        from etabs_api.workflows.tributary import geometric_tributary

        geometry = geometric_tributary(connector, reducible)
        for source, pattern, kpa in geometry.heavy_loads:
            notes.append(f"{source} puts {kpa:.1f} kPa on {pattern} (reducible). Above "
                         "4.8 kPa NSCP allows no reduction: should it be a non-reducible "
                         "live pattern?")
    out, reductions = [], {}
    releases = frame_releases(connector) if options.pattern_factor else {}
    for kind, table_name, label in (("beam", "Element Forces - Beams", "Beam"),
                                    ("column", "Element Forces - Columns", "Column")):
        table = _read(connector, table_name, sorted(needed))
        if table.empty:
            continue
        if members:
            table = table[table["UniqueName"].astype(str).isin(set(members))]
        table = table[~table["UniqueName"].astype(str).str.isnumeric()]
        if table.empty:
            continue
        forces = case_forces(table, label)
        member_reductions = None
        if options.reduce_live:
            areas = geometry.columns if kind == "column" else geometry.beams
            if load_share:
                above = columns_above(connector) if kind == "column" else None
                areas = load_share_tributary(forces, kind, UNIT_PATTERN, UNIT_KPA, areas, above)
            member_reductions = live_load_reduction(forces, kind, areas, dead, live,
                                                    options.tributary)
            reductions.update(member_reductions)
        out.append(combine(forces, combos, kind, live, reducible, member_reductions,
                           options.pattern_factor if kind == "beam" else None, releases))
    table = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return FactoredForces(table, reductions, notes)


def ask_force_options() -> ForceOptions | None:
    """Ask whether to reduce live load and add pattern live load. None when cancelled."""
    from utilities._gui_helpers import enter_values, select_option, show_warning

    title = "Factored Forces"
    reduce = select_option(
        title,
        "Reduce the reducible live load (NSCP 2015 205.5)?",
        ["Yes", "No"],
    )
    if reduce is None:
        return None
    method = "geometric"
    if reduce == "Yes":
        chosen = select_option(
            title,
            "Tributary area for the live load reduction:",
            ["Geometric: halfway lines (NSCP 205.5, as ETABS)",
             "Analysis load share: 1 kPa on every floor, as the frame carries it"],
        )
        if chosen is None:
            return None
        method = "geometric" if chosen.startswith("Geometric") else "load share"
    pattern = select_option(
        title,
        "Include pattern live load on the beams (ACI 318-14 6.4.2)?\n\n"
        "Each span is also designed for its live load as a simple span (largest "
        "sagging) and with fixed ends (largest hogging), times the factor.",
        ["Yes", "No"],
    )
    if pattern is None:
        return None
    factor = None
    while pattern == "Yes":
        label = "Factor on the live load"
        typed = enter_values(title, "Pattern live load factor.", [label],
                             {label: f"{PATTERN_LIVE_FACTOR:g}"})
        if typed is None:
            return None
        try:
            factor = float(typed[label])
            if not 0 < factor <= 1.0:
                raise ValueError
            break
        except ValueError:
            show_warning("The factor must be a number above 0 and at most 1.", title=title)
    return ForceOptions(reduce_live=reduce == "Yes", pattern_factor=factor, tributary=method)
