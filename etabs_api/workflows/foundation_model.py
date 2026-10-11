"""A foundation model from the support reactions of the superstructure (``sdt foundation``).

Modelling the foundation with the superstructure makes the analysis heavy.
This command keeps them apart. From the model open in ETABS (analysed) it
makes a second model that holds only the support joints, each loaded with
what the superstructure delivers there, as the starting point for modelling
the footings and mats as shells and designing them in ETABS.

**Create.** The model is saved as ``<model> - FOUNDATION.EDB`` and in that copy:

* every frame, shell and joint is deleted except the support joints;
* each support joint loses its restraint (the foundation you model carries it)
  and gets, in every load pattern, the opposite of its reaction in the
  superstructure: forces, and moments where the support is fixed. A pinned
  support gives no moment, as in the model;
* the grids, stories, materials and gravity load patterns are kept. The
  seismic and wind patterns become plain patterns that hold the exported
  loads, since ETABS cannot generate them on a model with no structure;
* a wind case with several steps gets one pattern for each step (``WX-1`` to
  ``WX-12``), and every combination that uses it becomes one combination for
  each step with their envelope under its own name;
* a response spectrum case has no sign: its reactions are peak values, not a
  set of forces in equilibrium. It is left out (the static seismic cases
  carry the earthquake), or exported with the sign of the static seismic case
  of its direction, joint by joint, as you choose;
* the combinations are made again from those of the superstructure;
* P-delta is switched off: the reactions already hold it.

**Update.** After the superstructure changes and is analysed again, the same
command replaces the exported loads in the foundation model and leaves what
you modelled there. New supports are added; a support that is gone is
reported, and its joint is left when something is attached to it.

Beside the foundation model: ``<foundation>.foundation.json`` (the supports,
what was exported and how) and ``<foundation> - Reactions.xlsx`` (the
reactions of every case and combination at every support, and the check that
the loads applied in each pattern add up to the base reaction of the
superstructure).

Forces in N and moments in N-mm, as read from ETABS; the workbook is in kN
and kN-m.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etabs_api.core.helpers import as_list, return_code  # noqa: E402

SUFFIX = " - FOUNDATION"
MANIFEST_SUFFIX = ".foundation.json"
COMPONENTS = ("F1", "F2", "F3", "M1", "M2", "M3")
STATIC, STEP, SPECTRUM = "static", "step", "spectrum"
OTHER_PATTERN = 8                      # eLoadPatternType: Other
LATERAL_TYPES = (5, 6, 61)             # seismic, wind, seismic (drift): auto lateral patterns
REDUCIBLE_LIVE = 4
SAME_PLACE = 1.0                       # mm: a support this close to a stored one is the same
ZERO_FORCE = 1e-6                      # N or N-mm: a smaller reaction is none
COMBO_TYPES = {"Linear Add": 0, "Envelope": 1, "Absolute Add": 2, "SRSS": 3, "Range Add": 4}
SPECTRUM_OFF, SPECTRUM_SIGNED = "static only", "signed"
EQUILIBRIUM_TOLERANCE = 1e-6           # of the largest force of the pattern
SAME_FORCE, SAME_MOMENT = 10.0, 1e4    # N, N-mm: a load that moved less is the same (0.01 kN)


# =============================================================================
# PLAIN DATA (no ETABS)
# =============================================================================
@dataclass
class Support:
    """A support joint of the superstructure and what stands on it."""

    joint: str
    x: float
    y: float
    z: float
    restraint: list[bool] = field(default_factory=lambda: [False] * 6)   # UX UY UZ RX RY RZ
    kind: str = ""               # "column", "wall" or "" (nothing found on it)
    member: str = ""             # the column (or the first wall) on the joint
    section: str = ""
    width: float = 0.0           # mm; a circular column has its diameter in both
    depth: float = 0.0
    circular: bool = False
    angle: float = 0.0           # degrees, of the column's local axes
    live_factor: float = 1.0     # on the reducible live load, when it is reduced

    @property
    def fixed(self) -> bool:
        return any(self.restraint[3:])


@dataclass
class Pattern:
    """A load pattern of the foundation model: where its loads come from."""

    name: str
    case: str                    # the load case of the superstructure
    kind: str                    # static, step or spectrum
    step: int = 0
    partner: str = ""            # spectrum: the static case its signs are taken from
    loads: dict[str, list[float]] = field(default_factory=dict)   # joint -> 6 applied values
    reduced: bool = False        # a reducible live case with the live load reduction on it


@dataclass
class Combo:
    name: str
    kind: str                                    # "Linear Add", "Envelope", ...
    members: list[tuple[str, bool, float]]       # (name, is a combination, factor)


@dataclass
class Options:
    spectrum: str = SPECTRUM_OFF                 # or SPECTRUM_SIGNED
    reduce_live: bool = False
    live_code: str = "NSCP"                      # or "ASCE"


def pattern_name(case: str, step: int) -> str:
    """The pattern of one step of a load case with several: ``WX-3``."""
    return f"{case}-{step}"


def build_patterns(reactions: pd.DataFrame, supports: list[Support], options: Options,
                   partners: dict[str, str] | None = None,
                   reducible: tuple = ()) -> tuple[list[Pattern], list[str]]:
    """The load patterns of the foundation model from the reactions.

    ``reactions`` has Joint, Case, Kind, Step and the six components: what
    each support gives the superstructure. The foundation gets the opposite.
    A spectrum case is left out, or takes for each component the sign of its
    static partner at that joint (``partners``: spectrum case -> static case).
    ``reducible`` are the reducible live cases, multiplied by the live load
    factor of the column on each joint when the live load is reduced.
    Returns the patterns and notes on what was left out.
    """
    partners = partners or {}
    by_joint = {s.joint: s for s in supports}
    wanted = set(by_joint)
    table = reactions[reactions["Joint"].astype(str).isin(wanted)]
    notes, out = [], []
    static_of: dict[tuple[str, str], list[float]] = {}
    for case, joint, kind, step, *values in zip(
            table["Case"].astype(str), table["Joint"].astype(str), table["Kind"], table["Step"],
            *(table[c] for c in COMPONENTS)):
        # the signs of a partner with several steps (a drift pattern with both
        # eccentricities) are those of its first step
        if kind == STATIC or (kind == STEP and int(step) == 1):
            static_of[(case, joint)] = [float(v) for v in values]
    groups = table.groupby([table["Case"].astype(str), table["Kind"], table["Step"]], sort=False)
    for (case, kind, step), rows in groups:
        step = int(step)
        partner = ""
        if kind == SPECTRUM:
            if options.spectrum != SPECTRUM_SIGNED:
                continue
            partner = partners.get(case, "")
            if not partner:
                notes.append(f"{case}: no static seismic case of its direction to take the "
                             "signs from; it was left out")
                continue
        pattern = Pattern(pattern_name(case, step) if kind == STEP else case, case, kind, step,
                          partner, reduced=options.reduce_live and case in reducible)
        for joint, *values in zip(rows["Joint"].astype(str), *(rows[c] for c in COMPONENTS)):
            reaction = [float(v) for v in values]
            if kind == SPECTRUM:
                reference = static_of.get((partner, joint), [0.0] * 6)
                reaction = [math.copysign(abs(v), r) if abs(r) > ZERO_FORCE else abs(v)
                            for v, r in zip(reaction, reference)]
            factor = by_joint[joint].live_factor if (options.reduce_live
                                                     and case in reducible) else 1.0
            applied = [-factor * v for v in reaction]
            if any(abs(v) > ZERO_FORCE for v in applied):
                pattern.loads[joint] = [0.0 if abs(v) <= ZERO_FORCE else v for v in applied]
        out.append(pattern)
    if options.spectrum != SPECTRUM_SIGNED:
        left = sorted(set(table.loc[table["Kind"] == SPECTRUM, "Case"].astype(str)))
        if left:
            notes.append("Response spectrum cases left out (their reactions have no sign): "
                         + ", ".join(left))
    return out, notes


def read_combos(definitions: pd.DataFrame) -> list[Combo]:
    """The combinations of the table "Load Combination Definitions", in its order."""
    if definitions is None or definitions.empty or "Name" not in definitions.columns:
        return []
    table = definitions.copy()
    table["Name"] = table["Name"].replace({"": None, "None": None}).ffill().astype(str)
    names = list(dict.fromkeys(table["Name"]))
    is_combo = set(names)
    out = []
    for name in names:
        rows = table[table["Name"] == name]
        kinds = [str(k) for k in rows["Type"] if k is not None and str(k) not in ("None", "nan", "")]
        members = []
        for load, factor in zip(rows["LoadName"], rows["SF"]):
            if load is None or str(load) in ("", "None", "nan"):
                continue
            members.append((str(load), str(load) in is_combo, float(factor)))
        out.append(Combo(name, kinds[0] if kinds else "Linear Add", members))
    return out


def plan_combinations(combos: list[Combo], patterns: list[Pattern], cases: set[str]
                      ) -> tuple[list[Combo], list[str]]:
    """The combinations of the foundation model, in an order they can be made in.

    ``patterns`` are those that exist there; ``cases`` every load case of the
    superstructure. A case that was not exported (a response spectrum case
    left out) takes with it every linear combination that uses it: without
    one of its terms it would be another combination. A case with several
    steps turns a linear combination into one combination for each step
    (``<name> [3]``) and their envelope under the name of the combination,
    so that what uses that name still finds it.
    """
    steps: dict[str, list[str]] = {}
    exported = set()
    for pattern in patterns:
        exported.add(pattern.case)
        if pattern.kind == STEP:
            steps.setdefault(pattern.case, []).append(pattern.name)
    missing = {case for case in cases if case not in exported}
    by_name = {combo.name: combo for combo in combos}
    notes, out, done, dropped = [], [], set(), set()

    def make(name: str, trail: tuple = ()) -> None:
        if name in done or name in dropped or name in trail:
            return
        combo = by_name[name]
        for member, is_combo, _ in combo.members:
            if is_combo and member in by_name:
                make(member, trail + (name,))
        members, lost = [], []
        for member, is_combo, factor in combo.members:
            gone = (member in dropped) if is_combo else (member in missing)
            unknown = not is_combo and member not in cases
            if gone or unknown:
                lost.append(member)
            else:
                members.append((member, is_combo, factor))
        linear = COMBO_TYPES.get(combo.kind, 0) == 0
        if (lost and linear) or not members:
            dropped.add(name)
            notes.append(f"{name}: not made (it uses {', '.join(dict.fromkeys(lost))}, which "
                         "is not in the foundation model)")
            return
        stepped = [(member, factor) for member, is_combo, factor in members
                   if not is_combo and member in steps]
        if not stepped:
            out.append(Combo(name, combo.kind, members))
        elif not linear:      # an envelope takes every step as it is
            new = []
            for member, is_combo, factor in members:
                if not is_combo and member in steps:
                    new += [(pattern, False, factor) for pattern in steps[member]]
                else:
                    new.append((member, is_combo, factor))
            out.append(Combo(name, combo.kind, new))
        else:
            fixed = [m for m in members if m[1] or m[0] not in steps]
            subs = []
            for picks in itertools.product(*[steps[member] for member, _ in stepped]):
                label = "-".join(p.rsplit("-", 1)[-1] for p in picks)
                sub = Combo(f"{name} [{label}]", combo.kind,
                            fixed + [(pattern, False, factor)
                                     for pattern, (_, factor) in zip(picks, stepped)])
                out.append(sub)
                subs.append(sub.name)
            out.append(Combo(name, "Envelope", [(sub, True, 1.0) for sub in subs]))
        done.add(name)

    for combo in combos:
        make(combo.name)
    return out, notes


def equilibrium_rows(patterns: list[Pattern], base: pd.DataFrame) -> list[dict]:
    """For each pattern: the sum of the loads applied, against the base reaction
    of its case in the superstructure (which they must cancel).

    ``base`` has Case, Step, FX, FY, FZ. A spectrum pattern is not a set in
    equilibrium: its row says so and is not a failure.
    """
    reaction = {(str(c), int(s)): (float(x), float(y), float(z)) for c, s, x, y, z in zip(
        base["Case"], base["Step"], base["FX"], base["FY"], base["FZ"])} if len(base) else {}
    rows = []
    for pattern in patterns:
        applied = [sum(load[i] for load in pattern.loads.values()) for i in range(3)]
        wanted = reaction.get((pattern.case, pattern.step))
        row = {"Pattern": pattern.name, "Case": pattern.case, "Kind": pattern.kind,
               "Sum FX (kN)": applied[0] / 1e3, "Sum FY (kN)": applied[1] / 1e3,
               "Sum FZ (kN)": applied[2] / 1e3}
        if pattern.kind == SPECTRUM:
            row["Check"] = ("INFO: peak values with the signs of " + pattern.partner
                            + "; not a set of forces in equilibrium")
        elif wanted is None:
            row["Check"] = "N/A: no base reaction read for this case"
        else:
            gap = max(abs(a + w) for a, w in zip(applied, wanted))
            scale = max([abs(w) for w in wanted] + [abs(a) for a in applied] + [1.0])
            row["Base reaction FX (kN)"] = wanted[0] / 1e3
            row["Base reaction FY (kN)"] = wanted[1] / 1e3
            row["Base reaction FZ (kN)"] = wanted[2] / 1e3
            if gap <= EQUILIBRIUM_TOLERANCE * scale + 1e-3:
                row["Check"] = "OK"
            elif pattern.reduced:
                share = abs(applied[2] / wanted[2]) * 100.0 if abs(wanted[2]) > 1.0 else 0.0
                row["Check"] = (f"INFO: reduced live load: the loads are {share:.1f} % of the "
                                "base reaction of the unreduced case")
            else:
                row["Check"] = (f"FAIL: the loads differ from the base reaction by "
                                f"{gap / 1e3:.3f} kN (a reaction at a joint that is not a "
                                "restrained support, such as a spring)")
        rows.append(row)
    return rows


def match_supports(stored: list[Support], current: list[Support]
                   ) -> tuple[dict[str, str], list[Support], list[Support]]:
    """Pair the supports of the foundation model with those of the superstructure now.

    A support is the same when it is at the same place (within ``SAME_PLACE``),
    whatever its joint is called now. Returns ({joint now: joint stored},
    supports that are new, stored supports that are gone).
    """
    left = list(stored)
    pairs: dict[str, str] = {}
    new = []
    for support in current:
        found = None
        for old in left:
            if (abs(old.x - support.x) <= SAME_PLACE and abs(old.y - support.y) <= SAME_PLACE
                    and abs(old.z - support.z) <= SAME_PLACE):
                found = old
                break
        if found is None:
            new.append(support)
        else:
            pairs[support.joint] = found.joint
            left.remove(found)
    return pairs, new, left


def manifest_path(foundation_path: str) -> str:
    return os.path.splitext(foundation_path)[0] + MANIFEST_SUFFIX


def foundation_path_for(model_path: str) -> str:
    return os.path.splitext(model_path)[0] + SUFFIX + ".EDB"


def load_manifest(foundation_path: str) -> dict | None:
    try:
        with open(manifest_path(foundation_path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_manifest(foundation_path: str, data: dict) -> str:
    path = manifest_path(foundation_path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    return path


# =============================================================================
# READING THE SUPERSTRUCTURE
# =============================================================================
def _table(connector, name: str) -> pd.DataFrame:
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay([])
    tables.SetLoadCombinationsSelectedForDisplay([])
    try:
        return connector._read_database_table(name)
    except Exception:  # noqa: BLE001 - a table the model does not have
        return pd.DataFrame()


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def read_supports(connector) -> list[Support]:
    """The restrained joints of the open model, with the column or wall on each."""
    model = connector.sap_model
    restraints = _table(connector, "Joint Assignments - Restraints")
    points = _table(connector, "Point Object Connectivity")
    if restraints.empty or points.empty:
        return []
    xyz = {str(n): (_number(x), _number(y), _number(z)) for n, x, y, z in zip(
        points["UniqueName"], points["X"], points["Y"], points["Z"])}
    columns = _table(connector, "Column Object Connectivity")
    walls = _table(connector, "Wall Object Connectivity")
    assigned = _table(connector, "Frame Assignments - Section Properties")
    section_of = dict(zip(assigned.get("UniqueName", pd.Series(dtype=str)).astype(str),
                          assigned.get("SectProp", pd.Series(dtype=str)).astype(str)))
    rectangles = _table(connector, "Frame Section Property Definitions - Concrete Rectangular")
    circles = _table(connector, "Frame Section Property Definitions - Concrete Circle")
    size_of: dict[str, tuple[float, float, bool]] = {}
    if not rectangles.empty:
        for name, t2, t3 in zip(rectangles["Name"].astype(str), rectangles["t2"],
                                rectangles["t3"]):
            size_of[name] = (_number(t2), _number(t3), False)
    if not circles.empty:
        for name, t3 in zip(circles["Name"].astype(str), circles["t3"]):
            size_of[name] = (_number(t3), _number(t3), True)
    column_at: dict[str, str] = {}
    if not columns.empty:
        for name, i, j in zip(columns["UniqueName"].astype(str), columns["UniquePtI"].astype(str),
                              columns["UniquePtJ"].astype(str)):
            low = i if xyz.get(i, (0, 0, 0))[2] <= xyz.get(j, (0, 0, 0))[2] else j
            column_at.setdefault(low, name)
    wall_at: dict[str, str] = {}
    if not walls.empty:
        for row in walls.to_dict("records"):
            for key in ("UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4"):
                if key in row and row[key] is not None:
                    wall_at.setdefault(str(row[key]), str(row["UniqueName"]))
    out = []
    for row in restraints.to_dict("records"):
        joint = str(row["UniqueName"])
        if joint not in xyz:
            continue
        held = [str(row.get(k)).strip().casefold() == "yes" for k in
                ("UX", "UY", "UZ", "RX", "RY", "RZ")]
        if not any(held):
            continue
        support = Support(joint, *xyz[joint], restraint=held)
        if joint in column_at:
            member = column_at[joint]
            section = section_of.get(member, "")
            width, depth, circular = size_of.get(section, (0.0, 0.0, False))
            try:
                angle = float(model.FrameObj.GetLocalAxes(member, 0.0, False)[0])
            except Exception:  # noqa: BLE001
                angle = 0.0
            support.kind, support.member, support.section = "column", member, section
            support.width, support.depth, support.circular = width, depth, circular
            support.angle = angle
        elif joint in wall_at:
            support.kind, support.member = "wall", wall_at[joint]
        out.append(support)
    return out


def read_reactions(connector, cases: dict[str, str], supports: list[Support]
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The reactions of ``cases`` at the supports, and their base reactions.

    ``cases`` maps each load case to its ETABS type. Returns (reactions,
    base): reactions with Joint, Case, Kind, Step and the six components;
    base with Case, Kind, Step, FX, FY, FZ, MX, MY, MZ.
    """
    results = connector.sap_model.Results
    setup = results.Setup
    setup.DeselectAllCasesAndCombosForOutput()
    for case in cases:
        setup.SetCaseSelectedForOutput(case)
    setup.SetOptionMultiStepStatic(2)      # every step of a case, not its envelope
    got = results.JointReact("All", 2, 0, [], [], [], [], [], [], [], [], [], [], [])
    if return_code(got) != 0:
        raise RuntimeError("ETABS gave no joint reactions: analyse the model first.")
    table = pd.DataFrame({name: as_list(values) for name, values in zip(
        ("Joint", "Element", "Case", "StepType", "Step", *COMPONENTS), got[1:12])})
    wanted = {s.joint for s in supports}
    table["Joint"] = table["Joint"].astype(str)
    table = table[table["Joint"].isin(wanted)].copy()

    def kinds(frame: pd.DataFrame) -> pd.DataFrame:
        step_type = frame["StepType"].astype(str)
        frame = frame[~step_type.eq("Min")].copy()         # a spectrum case: its peak values
        step_type = frame["StepType"].astype(str)
        frame["Kind"] = [SPECTRUM if cases.get(str(c), "").startswith("Response") or t == "Max"
                         else (STEP if t.startswith("Step") else STATIC)
                         for c, t in zip(frame["Case"], step_type)]
        frame["Step"] = [int(float(s)) if k == STEP else 0
                         for s, k in zip(frame["Step"], frame["Kind"])]
        return frame.drop(columns=["StepType"])

    table = kinds(table).drop(columns=["Element"])
    base = results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
    if return_code(base) != 0:
        raise RuntimeError("ETABS gave no base reactions: analyse the model first.")
    base_table = kinds(pd.DataFrame({name: as_list(values) for name, values in zip(
        ("Case", "StepType", "Step", "FX", "FY", "FZ", "MX", "MY", "MZ"), base[1:10])}))
    return table.reset_index(drop=True), base_table.reset_index(drop=True)


def spectrum_partners(base: pd.DataFrame, pattern_types: dict[str, int]) -> dict[str, str]:
    """The static seismic case each spectrum case takes its signs from: the one
    with the largest base shear in the direction of the spectrum case. A drift
    spectrum case (a name ending in D) takes a drift pattern when there is one."""
    shear = {str(c): (abs(float(x)), abs(float(y))) for c, x, y in zip(
        base["Case"], base["FX"], base["FY"])}
    spectra = set(base.loc[base["Kind"] == SPECTRUM, "Case"].astype(str))
    strength = [c for c, t in pattern_types.items() if t == 5 and c in shear]
    drift = [c for c, t in pattern_types.items() if t == 61 and c in shear]
    out = {}
    for case in spectra:
        axis = 0 if shear[case][0] >= shear[case][1] else 1
        pool = drift if case.upper().endswith("D") and drift else strength
        pool = [c for c in pool if shear[c][axis] >= shear[c][1 - axis]]
        if pool:
            out[case] = max(pool, key=lambda c: shear[c][axis])
    return out


def live_factors(connector, code: str) -> dict[str, float]:
    """The factor on the reducible live load of each column, by the rule of
    ``code`` ("NSCP" or "ASCE") with geometric tributary areas, as the column
    design reduces it."""
    from etabs_api.workflows.analysis_forces import ForceOptions, factored_forces

    was = getattr(connector, "include_numeric_members", False)
    connector.include_numeric_members = True       # every column, tagged or not
    try:
        found = factored_forces(connector, [], None, ForceOptions(reduce_live=True, code=code))
    finally:
        connector.include_numeric_members = was
    return {str(member): float(r.factor) for member, r in found.reductions.items()}


@dataclass
class Superstructure:
    """What the foundation needs from the superstructure, read in one go."""

    path: str
    supports: list[Support]
    patterns: list[Pattern]
    combos: list[Combo]
    notes: list[str]
    reactions: pd.DataFrame
    base: pd.DataFrame
    combo_reactions: pd.DataFrame
    pattern_types: dict[str, int]
    self_weight: dict[str, float]
    cases: dict[str, str]
    analysed_now: bool = False
    modal_cases: list[str] = field(default_factory=list)


def read_superstructure(connector, options: Options, progress=None) -> Superstructure:
    """Everything the foundation model is made from, from the open model."""
    from etabs_api.core.connection import model_file

    say = progress or (lambda text: None)
    model = connector.sap_model
    say("Reading the supports")
    supports = read_supports(connector)
    if not supports:
        raise RuntimeError("The model has no restrained joint: there is no support to export.")
    summary = _table(connector, "Load Case Definitions - Summary")
    cases = {str(n): str(t) for n, t in zip(summary["Name"], summary["Type"])
             if not str(t).startswith("Modal")}
    modal = [str(n) for n, t in zip(summary["Name"], summary["Type"])
             if str(t).startswith("Modal")]
    names = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    pattern_types = {n: int(model.LoadPatterns.GetLoadType(n)[0]) for n in names}
    self_weight = {n: float(model.LoadPatterns.GetSelfWTMultiplier(n)[0]) for n in names}
    status = {row["case"]: row["status"] for row in connector.analysis.status()}
    analysed_now = False
    if any(status.get(case) != "Finished" for case in cases):
        say("Running the analysis: not every load case has results")
        connector.analysis.run()
        analysed_now = True
    if options.reduce_live:
        say("Live load reduction of the columns")
        factors = live_factors(connector, options.live_code)
        for support in supports:
            if support.kind == "column":
                support.live_factor = factors.get(support.member, 1.0)
    say("Reading the reactions of every load case")
    reactions, base = read_reactions(connector, cases, supports)
    partners = spectrum_partners(base, pattern_types)
    reducible = tuple(c for c in cases if pattern_types.get(c) == REDUCIBLE_LIVE)
    patterns, notes = build_patterns(reactions, supports, options, partners, reducible)
    combos, more = plan_combinations(read_combos(_table(connector, "Load Combination Definitions")),
                                     patterns, set(cases))
    say("Reading the reactions of the combinations")
    combo_reactions = read_combination_reactions(connector, supports)
    return Superstructure(model_file(model), supports, patterns, combos, notes + more, reactions,
                          base, combo_reactions, pattern_types, self_weight, cases, analysed_now,
                          modal)


def read_combination_reactions(connector, supports: list[Support]) -> pd.DataFrame:
    """The reactions of every combination at the supports, for the record."""
    model = connector.sap_model
    names = [str(n) for n in as_list(model.RespCombo.GetNameList(0, [])[1])]
    if not names:
        return pd.DataFrame()
    setup = model.Results.Setup
    setup.DeselectAllCasesAndCombosForOutput()
    for name in names:
        setup.SetComboSelectedForOutput(name)
    got = model.Results.JointReact("All", 2, 0, [], [], [], [], [], [], [], [], [], [], [])
    if return_code(got) != 0:
        return pd.DataFrame()
    table = pd.DataFrame({name: as_list(values) for name, values in zip(
        ("Joint", "Element", "Combination", "StepType", "Step", *COMPONENTS), got[1:12])})
    wanted = {s.joint for s in supports}
    table["Joint"] = table["Joint"].astype(str)
    return table[table["Joint"].isin(wanted)].drop(columns=["Element"]).reset_index(drop=True)


# =============================================================================
# WRITING THE FOUNDATION MODEL
# =============================================================================
@dataclass
class Outcome:
    """What a create or an update did."""

    action: str
    foundation_path: str = ""
    supports: int = 0
    patterns: int = 0
    combinations: int = 0
    loads: int = 0
    added: list[str] = field(default_factory=list)       # supports added (update)
    gone: list[str] = field(default_factory=list)        # supports no longer there
    kept: list[str] = field(default_factory=list)        # gone, but something is attached
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    equilibrium: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)    # update: how the loads moved
    files: dict[str, str] = field(default_factory=dict)


def _ok(result) -> bool:
    return return_code(result) == 0


def _clear_structure(model, keep: set[str], outcome: Outcome) -> None:
    """Delete every object and joint except the joints of ``keep``."""
    for joint in keep:
        model.PointObj.SetSpecialPoint(joint, True)   # a joint with nothing on it stays
    for api in (model.FrameObj, model.AreaObj, getattr(model, "LinkObj", None),
                getattr(model, "TendonObj", None)):
        if api is None:
            continue
        try:
            names = [str(n) for n in as_list(api.GetNameList(0, [])[1])]
        except Exception:  # noqa: BLE001 - an object type this model has none of
            continue
        failed = sum(1 for name in names if not _ok(api.Delete(name)))
        if failed:
            outcome.problems.append(f"{failed} objects could not be deleted")
    for joint in [str(n) for n in as_list(model.PointObj.GetNameList(0, [])[1])]:
        if joint not in keep:
            model.PointObj.DeleteSpecialPoint(joint)
    left = int(model.PointObj.Count())
    if left != len(keep):
        outcome.problems.append(f"{left} joints are left, for {len(keep)} supports")


def _make_patterns(model, sup: Superstructure, outcome: Outcome) -> None:
    """The load patterns and cases of the foundation model."""
    wanted = {p.name for p in sup.patterns}
    existing = {str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])}
    cases = {str(n) for n in as_list(model.LoadCases.GetNameList(0, [])[1])}
    # a response spectrum case becomes a static case of the same name, or goes
    for case, kind in sup.cases.items():
        if kind.startswith("Response") and case in cases:
            model.LoadCases.Delete(case)
    for case in sup.modal_cases:              # nothing uses the modes once the spectra are gone
        if case in cases:
            model.LoadCases.Delete(case)
    stepped = {p.case for p in sup.patterns if p.kind == STEP}
    for case in stepped:                      # its steps take its place
        if case in cases:
            model.LoadCases.Delete(case)
        if case in existing:
            model.LoadPatterns.Delete(case)
            existing.discard(case)
    for name in sorted(existing - wanted - stepped):
        if sup.pattern_types.get(name) in LATERAL_TYPES:
            # a lateral pattern that was not exported: it would generate nothing here
            model.LoadCases.Delete(name)
            model.LoadPatterns.Delete(name)
    for pattern in sup.patterns:
        if pattern.name in existing:
            if sup.pattern_types.get(pattern.name) in LATERAL_TYPES:
                if not _ok(model.LoadPatterns.SetLoadType(pattern.name, OTHER_PATTERN)):
                    outcome.problems.append(f"pattern {pattern.name} is still an auto "
                                            "lateral pattern")
        elif not _ok(model.LoadPatterns.Add(pattern.name, OTHER_PATTERN, 0.0, True)):
            outcome.problems.append(f"pattern {pattern.name} could not be added")
    outcome.patterns = len(sup.patterns)


def _apply_loads(model, patterns: list[Pattern], joints: dict[str, str], clear: list[str],
                 outcome: Outcome) -> None:
    """Put the loads of ``patterns`` on the joints; ``joints`` maps a support of
    the superstructure to its joint in the foundation model. The loads these
    patterns had on the joints of ``clear`` are removed first."""
    count = 0
    for pattern in patterns:
        for joint in clear:
            model.PointObj.DeleteLoadForce(joint, pattern.name, 0)
        for source, values in pattern.loads.items():
            target = joints.get(source)
            if target is None:
                continue
            if _ok(model.PointObj.SetLoadForce(target, pattern.name, list(values), True,
                                               "Global", 0)):
                count += 1
            else:
                outcome.problems.append(f"load of {pattern.name} on joint {target} was refused")
    outcome.loads = count


def _make_combinations(model, combos: list[Combo], outcome: Outcome,
                       only: set[str] | None = None) -> None:
    """Delete the combinations (all, or those named in ``only``) and make ``combos``."""
    existing = [str(n) for n in as_list(model.RespCombo.GetNameList(0, [])[1])]
    doomed = [n for n in existing if only is None or n in only]
    for _ in range(len(doomed) + 1):          # one that another still uses goes on a later pass
        doomed = [n for n in doomed if not _ok(model.RespCombo.Delete(n))]
        if not doomed:
            break
    made = 0
    for combo in combos:
        if not _ok(model.RespCombo.Add(combo.name, COMBO_TYPES.get(combo.kind, 0))):
            outcome.problems.append(f"combination {combo.name} could not be made")
            continue
        good = True
        for member, is_combo, factor in combo.members:
            good = _ok(model.RespCombo.SetCaseList(combo.name, 1 if is_combo else 0, member,
                                                   factor)) and good
        if good:
            made += 1
        else:
            outcome.problems.append(f"combination {combo.name}: a term was refused")
    outcome.combinations = made


def _pdelta_off(model, outcome: Outcome) -> None:
    tables = model.DatabaseTables
    try:
        tables.SetTableForEditingArray("P-Delta Option Definition", 0,
                                       ["LoadPattern", "ScaleFactor", "RelConTol", "AutoMethod"],
                                       1, ["", "", "", "None"])
        applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
        if return_code(applied) != 0 or int(applied[0]) or int(applied[1]):
            raise RuntimeError(str(applied[4])[:200])
    except Exception as error:  # noqa: BLE001
        outcome.problems.append(f"P-delta could not be switched off: {error}")


def _manifest(sup: Superstructure, options: Options, joints: dict[str, str],
              foundation_path: str, combo_names: list[str]) -> dict:
    return {
        "made_by": "sdt foundation",
        "superstructure": sup.path,
        "foundation": foundation_path,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "options": asdict(options),
        "supports": [{**asdict(s), "foundation_joint": joints.get(s.joint, s.joint)}
                     for s in sup.supports],
        "patterns": [{"name": p.name, "case": p.case, "kind": p.kind, "step": p.step,
                      "partner": p.partner, "reduced": p.reduced, "loads": p.loads}
                     for p in sup.patterns],
        "combinations": combo_names,
        "combination_plan": [[c.name, c.kind, [list(m) for m in c.members]] for c in sup.combos],
    }


def create_foundation(connector, foundation_path: str, options: Options,
                      progress=None) -> Outcome:
    """Make the foundation model from the model open in ETABS (see the module).

    ETABS has the foundation model open afterwards; the superstructure file
    is not changed.
    """
    from etabs_api.core.connection import use_working_units

    say = progress or (lambda text: None)
    model = connector.sap_model
    sup = read_superstructure(connector, options, say)
    outcome = Outcome("created", foundation_path, notes=list(sup.notes))
    if sup.analysed_now:
        outcome.notes.append("The superstructure was analysed first: not every case had results.")
    say("Saving the copy that becomes the foundation model")
    if not _ok(model.File.Save(foundation_path)):
        raise RuntimeError(f"ETABS could not save {foundation_path}.")
    use_working_units(model)
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    keep = {s.joint for s in sup.supports}
    say("Deleting the structure, except the support joints")
    _clear_structure(model, keep, outcome)
    for joint in keep:
        if not _ok(model.PointObj.SetRestraint(joint, [False] * 6)):
            outcome.problems.append(f"the restraint of joint {joint} could not be removed")
    say("Load patterns")
    _make_patterns(model, sup, outcome)
    say("Loads on the support joints")
    joints = {joint: joint for joint in keep}
    _apply_loads(model, sup.patterns, joints, [], outcome)
    say(f"Combinations ({len(sup.combos)})")
    _make_combinations(model, sup.combos, outcome)
    _pdelta_off(model, outcome)
    if not _ok(model.File.Save(foundation_path)):
        outcome.problems.append("the foundation model could not be saved")
    outcome.supports = len(sup.supports)
    outcome.equilibrium = equilibrium_rows(sup.patterns, sup.base)
    _write_files(sup, options, joints, outcome)
    return outcome


def _stored_supports(manifest: dict) -> list[Support]:
    fields = Support.__dataclass_fields__
    return [Support(**{k: v for k, v in item.items() if k in fields})
            for item in manifest.get("supports", [])]


def load_changes(old: list[dict], new: list[Pattern]) -> list[dict]:
    """How far the loads of each pattern moved in an update: the largest change of
    a force and of a moment at any support, and the vertical total before and after."""
    before = {p["name"]: p.get("loads", {}) for p in old}
    rows = []
    for pattern in new:
        was = before.get(pattern.name)
        if was is None:
            rows.append({"Pattern": pattern.name, "Change": "new pattern"})
            continue
        force = moment = 0.0
        for joint in set(was) | set(pattern.loads):
            a, b = was.get(joint, [0.0] * 6), pattern.loads.get(joint, [0.0] * 6)
            force = max(force, max(abs(x - y) for x, y in zip(a[:3], b[:3])))
            moment = max(moment, max(abs(x - y) for x, y in zip(a[3:], b[3:])))
        rows.append({
            "Pattern": pattern.name,
            "Sum FZ before (kN)": sum(v[2] for v in was.values()) / 1e3,
            "Sum FZ now (kN)": sum(v[2] for v in pattern.loads.values()) / 1e3,
            "Largest change of a force (kN)": force / 1e3,
            "Largest change of a moment (kN-m)": moment / 1e6,
            "Change": "same" if force < SAME_FORCE and moment < SAME_MOMENT else "changed"})
    for name in before:
        if name not in {p.name for p in new}:
            rows.append({"Pattern": name, "Change": "no longer exported"})
    return rows


def update_foundation(connector, foundation_path: str, options: Options | None = None,
                      progress=None) -> Outcome:
    """Replace the exported loads of a foundation model with the reactions of the
    superstructure as it is now (open in ETABS, analysed).

    What was modelled in the foundation model stays. ETABS has the foundation
    model open afterwards.
    """
    from etabs_api.core.connection import use_working_units

    say = progress or (lambda text: None)
    manifest = load_manifest(foundation_path)
    if manifest is None:
        raise RuntimeError(f"{manifest_path(foundation_path)} was not found: this foundation "
                           "model was not made by sdt foundation, or its file was moved.")
    if options is None:
        saved = manifest.get("options", {})
        options = Options(**{k: v for k, v in saved.items() if k in Options.__dataclass_fields__})
    model = connector.sap_model
    sup = read_superstructure(connector, options, say)
    outcome = Outcome("updated", foundation_path, notes=list(sup.notes))
    stored = _stored_supports(manifest)
    joint_then = {item["joint"]: item.get("foundation_joint", item["joint"])
                  for item in manifest.get("supports", [])}
    pairs, new, gone = match_supports(stored, sup.supports)
    say("Opening the foundation model")
    if not _ok(model.File.OpenFile(foundation_path)):
        raise RuntimeError(f"ETABS could not open {foundation_path}.")
    use_working_units(model)
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    present = {str(n) for n in as_list(model.PointObj.GetNameList(0, [])[1])}
    joints: dict[str, str] = {}
    for now, then in pairs.items():
        target = joint_then.get(then, then)
        if target in present:
            joints[now] = target
        else:
            outcome.problems.append(f"the joint of support {then} is no longer in the "
                                    "foundation model: its loads were not applied")
    for support in new:
        added = model.PointObj.AddCartesian(support.x, support.y, support.z, "", "", "Global",
                                            False, 0)
        if _ok(added):
            name = str(added[0])
            model.PointObj.SetSpecialPoint(name, True)
            joints[support.joint] = name
            outcome.added.append(f"{support.joint} at ({support.x:,.0f}, {support.y:,.0f}, "
                                 f"{support.z:,.0f})")
        else:
            outcome.problems.append(f"a joint for the new support {support.joint} could not "
                                    "be added")
    clear = [joint_then.get(s.joint, s.joint) for s in stored]
    clear = [joint for joint in clear if joint in present]
    say("Load patterns")
    old_patterns = manifest.get("patterns", [])
    _make_update_patterns(model, sup, old_patterns, clear, outcome)
    say("Loads on the support joints")
    _apply_loads(model, sup.patterns, joints, clear, outcome)
    for support in gone:
        joint = joint_then.get(support.joint, support.joint)
        if joint not in present:
            continue
        label = (f"{support.joint} at ({support.x:,.0f}, {support.y:,.0f}, {support.z:,.0f})")
        connected = model.PointObj.GetConnectivity(joint)
        if return_code(connected) == 0 and int(connected[0]) > 0:
            outcome.kept.append(label)
        else:
            model.PointObj.DeleteSpecialPoint(joint)
            outcome.gone.append(label)
    plan = [[c.name, c.kind, [list(m) for m in c.members]] for c in sup.combos]
    if plan != manifest.get("combination_plan"):
        say(f"Combinations ({len(sup.combos)})")
        _make_combinations(model, sup.combos, outcome,
                           set(manifest.get("combinations", [])) | {c.name for c in sup.combos})
        outcome.notes.append("The combinations of the superstructure changed: those of the "
                             "foundation model that came from it were made again.")
    else:
        outcome.combinations = len(sup.combos)
    if not _ok(model.File.Save(foundation_path)):
        outcome.problems.append("the foundation model could not be saved")
    outcome.supports = len(joints)
    outcome.equilibrium = equilibrium_rows(sup.patterns, sup.base)
    outcome.changes = load_changes(old_patterns, sup.patterns)
    _write_files(sup, options, joints, outcome)
    return outcome


def _make_update_patterns(model, sup: Superstructure, old: list[dict], clear: list[str],
                          outcome: Outcome) -> None:
    """Add the patterns that are new; empty those that are no longer exported."""
    existing = {str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])}
    for pattern in sup.patterns:
        if pattern.name not in existing:
            if _ok(model.LoadPatterns.Add(pattern.name, OTHER_PATTERN, 0.0, True)):
                outcome.notes.append(f"New load pattern: {pattern.name}")
            else:
                outcome.problems.append(f"pattern {pattern.name} could not be added")
    now = {p.name for p in sup.patterns}
    for item in old:
        if item["name"] not in now and item["name"] in existing:
            for joint in clear:
                model.PointObj.DeleteLoadForce(joint, item["name"], 0)
            outcome.notes.append(f"{item['name']} is no longer exported: its loads on the "
                                 "support joints were removed (the pattern is left)")
    outcome.patterns = len(sup.patterns)


# =============================================================================
# FILES
# =============================================================================
def reactions_workbook(sup: Superstructure, outcome: Outcome, path: str) -> str:
    """The record of the export: supports, applied loads, reactions, the check."""
    supports = pd.DataFrame([{
        "Joint": s.joint, "X (mm)": s.x, "Y (mm)": s.y, "Z (mm)": s.z,
        "Support": "fixed" if s.fixed else "pinned",
        "Carries": s.kind or "-", "Member": s.member, "Section": s.section,
        "Width (mm)": s.width or None, "Depth (mm)": s.depth or None,
        "Live load factor": s.live_factor} for s in sup.supports])
    applied = pd.DataFrame([{
        "Pattern": p.name, "Joint": joint,
        **{f"{c} (kN)" if c[0] == "F" else f"{c} (kN-m)":
           v / (1e3 if c[0] == "F" else 1e6) for c, v in zip(COMPONENTS, values)}}
        for p in sup.patterns for joint, values in p.loads.items()])

    def in_kn(table: pd.DataFrame) -> pd.DataFrame:
        table = table.copy()
        for c in COMPONENTS:
            if c in table.columns:
                table[c] = pd.to_numeric(table[c], errors="coerce") / (1e3 if c[0] == "F" else 1e6)
                table = table.rename(columns={c: f"{c} (kN)" if c[0] == "F" else f"{c} (kN-m)"})
        return table

    with pd.ExcelWriter(path) as writer:
        supports.to_excel(writer, sheet_name="SUPPORTS", index=False)
        pd.DataFrame(outcome.equilibrium).to_excel(writer, sheet_name="EQUILIBRIUM CHECK",
                                                   index=False)
        if outcome.changes:
            pd.DataFrame(outcome.changes).to_excel(writer, sheet_name="CHANGES", index=False)
        applied.to_excel(writer, sheet_name="LOADS APPLIED", index=False)
        in_kn(sup.reactions).to_excel(writer, sheet_name="REACTIONS BY CASE", index=False)
        if len(sup.combo_reactions):
            in_kn(sup.combo_reactions).to_excel(writer, sheet_name="REACTIONS BY COMBINATION",
                                                index=False)
    return path


def _write_files(sup: Superstructure, options: Options, joints: dict[str, str],
                 outcome: Outcome) -> None:
    path = outcome.foundation_path
    outcome.files["Foundation model"] = path
    outcome.files["Supports and exported loads"] = save_manifest(
        path, _manifest(sup, options, joints, path, [c.name for c in sup.combos]))
    workbook = os.path.splitext(path)[0] + " - Reactions.xlsx"
    try:
        outcome.files["Reactions and check"] = reactions_workbook(sup, outcome, workbook)
    except OSError as error:
        outcome.problems.append(f"the reactions workbook was not written ({error}): close it "
                                "in Excel and update again")


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
CREATE = "Create the foundation model from this model"
UPDATE = "Update the forces of its foundation model (this model changed)"
SPECTRUM_CHOICES = {
    "Static seismic cases only (the response spectrum cases are left out)": SPECTRUM_OFF,
    "Also the response spectrum cases, with the signs of the static case of their direction":
        SPECTRUM_SIGNED,
}
LIVE_CHOICES = {
    "No: the full live load (the safe side)": (False, "NSCP"),
    "Yes, NSCP 2015 205.5, with the factor of the column on each support": (True, "NSCP"),
    "Yes, ASCE 7-10 4.7, with the factor of the column on each support": (True, "ASCE"),
}


def outcome_report(outcome: Outcome):
    """The terminal report of a create or an update: the equilibrium check as a table."""
    from utilities.terminal import Report

    report = Report(f"FOUNDATION MODEL - {outcome.action}",
                    [f"Model: {outcome.foundation_path}"])
    report.heading("Loads applied against the base reactions of the superstructure")
    report.equations(["Σ F applied in a pattern = − base reaction of its load case",
                      "load on a support joint = − reaction of the support"])
    report.table(["Pattern", "ΣFX (kN)", "ΣFY (kN)", "ΣFZ (kN)", "Check"],
                 [[row["Pattern"], f"{row['Sum FX (kN)']:,.1f}", f"{row['Sum FY (kN)']:,.1f}",
                   f"{row['Sum FZ (kN)']:,.1f}", row["Check"].split(":")[0]]
                  for row in outcome.equilibrium])
    changed = [row for row in outcome.changes if row.get("Change") != "same"]
    if outcome.changes:
        report.heading("What changed since the last export")
        report.table(["Pattern", "ΣFZ before (kN)", "ΣFZ now (kN)", "Largest ΔF (kN)",
                      "Largest ΔM (kN-m)", "Change"],
                     [[row["Pattern"],
                       *(f"{row[k]:,.1f}" if k in row else "-" for k in (
                           "Sum FZ before (kN)", "Sum FZ now (kN)",
                           "Largest change of a force (kN)",
                           "Largest change of a moment (kN-m)")),
                       row["Change"]] for row in changed])
        if not changed:
            report.lines(["No load changed."], "green")
    if outcome.notes:
        report.heading("Notes")
        report.lines(outcome.notes)
    return report


def foundation_summary(outcome: Outcome, source: str):
    from utilities.run_summary import RunSummary, listed

    summary = RunSummary("sdt foundation", outcome.foundation_path)
    summary.add("Foundation model", outcome.action)
    summary.add("Superstructure", os.path.basename(source))
    summary.add("Support joints", outcome.supports)
    summary.add("Load patterns", outcome.patterns)
    summary.add("Joint loads applied", outcome.loads)
    summary.add("Combinations", outcome.combinations)
    failing = [row["Pattern"] for row in outcome.equilibrium
               if row["Check"].startswith("FAIL")]
    summary.add("Equilibrium check", f"{len(failing)} patterns failing" if failing
                else "the loads of every pattern add up to its base reaction")
    if outcome.added:
        summary.add("Supports added", listed(outcome.added, 6))
    if outcome.gone:
        summary.add("Supports removed", listed(outcome.gone, 6))
    changed = [row["Pattern"] for row in outcome.changes if row.get("Change") == "changed"]
    if outcome.changes:
        summary.add("Patterns whose loads changed", f"{len(changed)} of {len(outcome.changes)}")
    if failing:
        summary.fail("Loads that do not add up to the base reaction: " + listed(failing))
    if outcome.kept:
        summary.fail("Supports that are gone from the superstructure, but something is "
                     "attached to their joint (left as they are, with no load): "
                     + listed(outcome.kept, 6))
    for problem in outcome.problems:
        summary.fail(problem)
    for note in outcome.notes[:12]:
        summary.note(note)
    if len(outcome.notes) > 12:
        summary.note(f"{len(outcome.notes) - 12} more notes are in the terminal.")
    summary.note("The support joints have no restraint in the foundation model: it is "
                 "unstable until the footings and their soil springs are modelled.")
    summary.note("P-delta is off there: the reactions already hold it.")
    for label, path in outcome.files.items():
        summary.file(label, path)
    return summary


SAVE_PLAN = "Save the foundation plan (for sdt plans and the 3D model)"


def save_foundation_plan(connector, foundation_path: str, manifest: dict, title: str):
    """With the foundation model open: read its footings, tie beams and columns and
    keep them beside it, for the framing plans and the 3D model of the superstructure."""
    from etabs_api.workflows import foundation_plan as fp
    from utilities._gui_helpers import LoadingWindow, select_option
    from utilities.run_summary import RunSummary
    from utilities.terminal import Report

    source = os.path.basename(str(manifest.get("superstructure", "")))
    chosen = select_option(
        title, "The model open in ETABS is a foundation model.\n\nIts footings, tie beams and "
        "columns can be saved, so that sdt plans and the 3D model of the superstructure "
        f"({source}) can include the foundation. To update its forces, open {source}, analyse "
        "it and run sdt foundation there.", [SAVE_PLAN, "Cancel"])
    if chosen != SAVE_PLAN:
        return None
    with LoadingWindow("Foundation plan") as window:
        window.update("Reading the footings, tie beams and columns")
        footings, tie_beams, columns = fp.read_foundation(connector, manifest)
        path = fp.save_plan(foundation_path, footings, tie_beams, columns)
    report = Report("FOUNDATION PLAN - saved", [f"Model: {foundation_path}"])
    report.heading("Footings")
    report.table(["Mark", "Kind", "Length (mm)", "Width (mm)", "Thickness (mm)", "Columns"],
                 [[f.mark, f.kind, f"{f.length:,.0f}", f"{f.width:,.0f}", f"{f.thickness:,.0f}",
                   ", ".join(f.columns) or "-"] for f in footings])
    report.heading("Tie beams")
    report.table(["Mark", "b x h (mm)", "Length (mm)"],
                 [[b.mark, f"{b.width:,.0f} x {b.depth:,.0f}",
                   f"{math.hypot(b.x2 - b.x1, b.y2 - b.y1):,.0f}"] for b in tie_beams])
    report.show()
    summary = RunSummary("sdt foundation", foundation_path)
    summary.add("Foundation plan", "saved")
    summary.add("Footings", f"{len(footings)} ({sum(1 for f in footings if f.kind == 'combined')}"
                            " combined)")
    summary.add("Footing marks", ", ".join(dict.fromkeys(f.mark for f in footings)) or "-")
    summary.add("Tie beams", len(tie_beams))
    summary.add("Columns", len(columns))
    bare = [c.mark for c in columns
            if not any(c.mark in f.columns for f in footings)]
    if not footings:
        summary.fail("The model has no horizontal shell yet: no footing was saved.")
    elif bare:
        from utilities.run_summary import listed

        summary.fail("Columns with no footing under them: " + listed(bare))
    summary.note("sdt plans and the 3D model read this file from the superstructure. Save the "
                 "plan again after the foundation changes.")
    summary.file("Foundation plan", path)
    summary.show(popup=True, echo=False)
    return {"footings": footings, "tie_beams": tie_beams, "columns": columns, "path": path}


def run_foundation() -> Outcome | None:
    """Entry point: create the foundation model of the open model, or update it."""
    from etabs_api.core.connection import attach_running_etabs, model_file
    from utilities._gui_helpers import (
        LoadingWindow,
        select_open_path,
        select_option,
        show_warning,
    )

    title = "Foundation Model"
    connector = attach_running_etabs(title)
    if connector is None:
        return None
    source = model_file(connector.sap_model)
    if not source or not os.path.isfile(source):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    own = load_manifest(source)
    if own is not None and own.get("foundation") and os.path.normcase(
            own["foundation"]) == os.path.normcase(source):
        return save_foundation_plan(connector, source, own, title)
    target = foundation_path_for(source)
    exists = os.path.isfile(target) and load_manifest(target) is not None
    action = select_option(
        title, "The foundation model holds only the support joints of this model, loaded with "
        "its reactions, to model and design the foundation apart from the superstructure."
        + (f"\n\nA foundation model of this model exists: {os.path.basename(target)}."
           if exists else ""),
        [UPDATE, CREATE] if exists else [CREATE, UPDATE])
    if action is None:
        return None
    options = None
    if action == UPDATE:
        if not exists:
            target = select_open_path("Select the foundation model to update", ".EDB",
                                      "ETABS model")
            if not target:
                return None
            if load_manifest(target) is None:
                show_warning(f"{os.path.basename(manifest_path(target))} was not found beside "
                             "that model: it was not made by sdt foundation.", title=title)
                return None
        saved = (load_manifest(target) or {}).get("options", {})
        keep = select_option(
            title, "Export with the same choices as before?\n\n"
            f"Response spectrum cases: {saved.get('spectrum', SPECTRUM_OFF)}\n"
            f"Live load reduction: "
            + (f"yes, {saved.get('live_code', 'NSCP')}" if saved.get("reduce_live") else "no"),
            ["Yes, the same choices", "No, choose again"])
        if keep is None:
            return None
        if keep.startswith("Yes"):
            options = Options(**{k: v for k, v in saved.items()
                                 if k in Options.__dataclass_fields__})
    elif exists:
        again = select_option(
            title, f"{os.path.basename(target)} exists. Creating it again replaces it, with "
            "everything that was modelled in it. To keep that and only refresh the forces, "
            "choose the update.", ["Stop", "Replace it"])
        if again != "Replace it":
            return None
    if options is None:
        spectrum = select_option(
            title, "Which seismic cases load the foundation?\n\nThe reactions of a response "
            "spectrum case are peak values with no sign: they are not one set of forces in "
            "equilibrium. With signs, each takes the sign of the static seismic case of its "
            "direction at each support.", list(SPECTRUM_CHOICES))
        if spectrum is None:
            return None
        live = select_option(
            title, "Reduce the reducible live load on the foundation?\n\nThe reactions of the "
            "live load cases are unreduced. With a reduction, the reducible live load at each "
            "support is multiplied by the factor of the column that stands on it (geometric "
            "tributary area, as in the column design). Supports of walls are not reduced.",
            list(LIVE_CHOICES))
        if live is None:
            return None
        options = Options(SPECTRUM_CHOICES[spectrum], *LIVE_CHOICES[live])
    try:
        with LoadingWindow("Foundation model") as window:
            if action == CREATE:
                outcome = create_foundation(connector, target, options, window.update)
            else:
                outcome = update_foundation(connector, target, options, window.update)
    except RuntimeError as error:
        show_warning(str(error), title=title)
        return None
    outcome_report(outcome).show()
    foundation_summary(outcome, source).show(popup=True, echo=False)
    return outcome


if __name__ == "__main__":
    run_foundation()
