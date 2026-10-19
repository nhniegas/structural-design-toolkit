"""Torsion amplification Ax, the redundancy factor rho and the 90 % spectrum scaling
(``sdt analyze``).

* **Ax** (NSCP 208.5.1.4, Eq. 208-7): where a level's largest displacement is over
  1.2 times its average, its accidental torsion is multiplied by
  Ax = (dmax / 1.2 davg)^2, between 1.0 and 3.0. ETABS has no per-level
  eccentricity for UBC 97 patterns, so the part added, (Ax - 1) times the level's
  accidental torque, is a load pattern of its own beside each static seismic
  pattern (``AX EQXPE`` ...), a moment on the level's rigid diaphragm, in every
  combination with its pattern. The accidental torque of a level is half the
  difference of the story torques of the + and - eccentricity patterns. Ax is
  read again from the displacements with the added torque until it settles. The
  spectrum cases take 5 % times the largest Ax as their eccentricity.
* **rho** (NSCP 208.6.1, Eq. 208-20): rho = 2 - 6.1 / (rmax sqrt(AB)), AB the
  ground floor area in m2, rmax the largest element-story shear ratio at or below
  two-thirds of the height. The story shear is what the story's columns and walls
  carry in the direction (or ETABS's story force when larger: a podium's walls
  can carry it back, leaving a small net force). For moment frames ri is the
  largest shear of two adjacent columns of a frame bay over the story shear (70 % for a column with
  frame bays on both sides); for walls the wall shear times 3 / lw (m) over the
  story shear; for dual systems rho need not exceed 80 % of the value. Between
  1.0 and 1.5, and at most 1.25 for special moment frames (more bays otherwise).
  1.0 in Seismic Zone 2 and for drift. Applied per direction: the strength and
  service seismic combinations use directional combinations ``EQ_RHO_nn`` with
  rho x on the X cases and rho y on the Y cases; the Em combinations keep
  Omega0 Eh without rho.
* **90 %** (NSCP 208.5.3.5.4 item 1): the spectrum may be scaled to 90 % of the
  static base shear for a regular structure. ``sdt irregularity`` writes whether
  the structure is regular; ``sdt analyze`` uses 90 % only when it is and the
  engineer confirms the types it cannot check are absent.

The plain functions need no ETABS.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

AX_MAX = 3.0
AX_TORSION_LIMIT = 1.2
AX_SETTLED = 0.05            # rounds stop when no Ax moves more than this
AX_ROUNDS = 3
AX_PREFIX = "AX "            # the added torsion pattern of EQXPE is "AX EQXPE"
ECCENTRICITY = 0.05
SHARED_COLUMN = 0.70         # a column with frame bays on both sides
WALL_LENGTH_FACTOR = 3.0     # ri of a wall: V x 3 / lw (lw in m)
RHO_MIN, RHO_MAX, RHO_SMRF = 1.0, 1.5, 1.25
DUAL_SHARE = 0.80
RHO_SET = "RHO"              # EQ_RHO_01 ... the directional combinations with rho
REGULAR_TARGET, IRREGULAR_TARGET = 0.90, 1.00
STATIC_PAIRS = (("EQXPE", "EQXNE", "X"), ("EQYPE", "EQYNE", "Y"))


# =============================================================================
# AX (plain data)
# =============================================================================
def ax_of(most: float, average: float) -> float:
    """Ax = (dmax / 1.2 davg)^2, between 1.0 and 3.0 (NSCP 208.5.1.4, Eq. 208-7)."""
    if average <= 1e-12:
        return 1.0
    return min(max((most / (AX_TORSION_LIMIT * average)) ** 2, 1.0), AX_MAX)


def level_ax(rows: list[tuple]) -> dict[tuple[str, str], float]:
    """(story, direction) -> the largest Ax of the cases in that direction.
    ``rows``: (story, case, direction, maximum, average) in each case's own direction."""
    out: dict[tuple[str, str], float] = {}
    for story, _, direction, most, average in rows:
        key = (str(story), str(direction))
        out[key] = max(out.get(key, 1.0), ax_of(float(most), float(average)))
    return out


def accidental_torques(story_torque: dict[tuple[str, str], float], order: list[str],
                       plus: str, minus: str) -> dict[str, float]:
    """The accidental torque applied at each level by the + eccentricity pattern:
    half the difference of the story torques of ``plus`` and ``minus``, taken level
    by level (the story torque adds the levels above). ``story_torque``: (story,
    case) -> T at the story, ``order`` from the top down. The - pattern has the
    same torques with the other sign."""
    out = {}
    above = 0.0
    for story in order:
        if (story, plus) not in story_torque or (story, minus) not in story_torque:
            continue
        total = (story_torque[(story, plus)] - story_torque[(story, minus)]) / 2.0
        out[story] = total - above
        above = total
    return out


def added_torques(ax: dict[tuple[str, str], float], accidental: dict[str, float],
                  direction: str, sign: float) -> dict[str, float]:
    """(Ax - 1) times the accidental torque of each level, for the pattern of
    ``direction`` with eccentricity ``sign`` (+1 or -1)."""
    return {story: (ax.get((story, direction), 1.0) - 1.0) * sign * torque
            for story, torque in accidental.items()
            if ax.get((story, direction), 1.0) > 1.0 + 1e-9}


def settled(old: dict, new: dict) -> bool:
    keys = set(old) | set(new)
    return all(abs(old.get(k, 1.0) - new.get(k, 1.0)) <= AX_SETTLED for k in keys)


# =============================================================================
# RHO (plain data)
# =============================================================================
@dataclass
class Rho:
    direction: str
    rho: float = 1.0
    computed: float = 1.0           # before the limits
    r_max: float = 0.0
    story: str = ""
    element: str = ""
    area: float = 0.0               # AB, m2
    notes: list[str] = field(default_factory=list)
    over_smrf: bool = False         # more bays are needed (NSCP 208.6.1)


def rho_value(r_max: float, area_m2: float, system=None, zone_factor: float = 0.4
              ) -> tuple[float, float, list[str], bool]:
    """(rho, rho before the limits, notes, over the SMRF limit) by NSCP 208.6.1."""
    notes = []
    if zone_factor <= 0.2 + 1e-9:
        return 1.0, 1.0, ["Seismic Zone 2: rho = 1.0 (NSCP 208.6.1)."], False
    if r_max <= 0 or area_m2 <= 0:
        return 1.0, 1.0, ["No element story shear ratio: rho = 1.0."], False
    computed = 2.0 - 6.1 / (r_max * math.sqrt(area_m2))
    value = computed
    family = getattr(system, "family", "") or ""
    if family.startswith("Dual"):
        value *= DUAL_SHARE
        notes.append("Dual system: rho need not exceed 80 % of the value.")
    value = min(max(value, RHO_MIN), RHO_MAX)
    over = False
    if getattr(system, "key", "") == "C1" and value > RHO_SMRF + 1e-9:
        over = True
        notes.append(f"Special moment frames: rho {value:.3f} over {RHO_SMRF}: the number of "
                     "bays is to be increased until rho is 1.25 or less (NSCP 208.6.1). "
                     "1.25 is not applied in its place.")
    return value, computed, notes, over


def frame_bays(columns: dict[str, tuple[float, float]], beams: list[tuple[str, str]],
               along_x: bool) -> list[tuple[str, str]]:
    """Pairs of adjacent columns of a story joined by a beam along X (or Y).
    ``columns``: name -> (x, y) of its top; ``beams``: the joints of each beam as
    the two column names it joins (beams joining anything else left out)."""
    out = []
    for a, b in beams:
        if a not in columns or b not in columns or a == b:
            continue
        (x1, y1), (x2, y2) = columns[a], columns[b]
        if (abs(x2 - x1) >= abs(y2 - y1)) == along_x:
            out.append((a, b))
    return out


def story_ratio(shears: dict[str, float], bays: list[tuple[str, str]],
                story_shear: float) -> tuple[float, str]:
    """The largest ri of a moment frame story: the sum of the shears of two adjacent
    columns of a bay over the story shear, 70 % for a column with bays on both sides
    in that direction. (ri, the bay)."""
    if story_shear <= 0 or not bays:
        return 0.0, ""
    count: dict[str, int] = {}
    for a, b in bays:
        count[a] = count.get(a, 0) + 1
        count[b] = count.get(b, 0) + 1
    best = (0.0, "")
    for a, b in bays:
        total = sum(abs(shears.get(c, 0.0)) * (SHARED_COLUMN if count[c] >= 2 else 1.0)
                    for c in (a, b))
        if total / story_shear > best[0]:
            best = (total / story_shear, f"{a} and {b}")
    return best


def wall_ratio(shear: float, length_m: float, story_shear: float) -> float:
    """ri of a wall: its shear times 3 / lw (lw in m) over the story shear."""
    if story_shear <= 0 or length_m <= 0:
        return 0.0
    return abs(shear) * WALL_LENGTH_FACTOR / length_m / story_shear


def lower_two_thirds(elevations: dict[str, float], base: float) -> list[str]:
    """The stories at or below two-thirds of the height above ``base``."""
    if not elevations:
        return []
    height = max(elevations.values()) - base
    return [s for s, z in elevations.items() if z - base <= 2.0 / 3.0 * height + 1e-6]


# =============================================================================
# THE REGULARITY RECORD (written by sdt irregularity)
# =============================================================================
def case_direction(case: str) -> str:
    """X or Y of a seismic case of a directional combination (EQXPE, RSAX, AX EQYNE)."""
    up = case.upper()
    return "X" if ("EQX" in up or "RSAX" in up) else "Y"


def regularity_path(model_path: str) -> str:
    from utilities.output_folders import SUMMARIES, beside

    return beside(model_path, " - regularity.json", SUMMARIES)


def save_regularity(model_path: str, irregular: list[str], judged: list[str]) -> str:
    """Write whether the structure was found regular: the irregular types and the
    types left to the engineer."""
    path = regularity_path(model_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"irregular": irregular, "judged": judged,
                   "model_time": os.path.getmtime(model_path)
                   if os.path.isfile(model_path) else None}, handle, indent=2)
    return path


def load_regularity(model_path: str) -> dict | None:
    try:
        with open(regularity_path(model_path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# =============================================================================
# ETABS
# =============================================================================
def _num(value, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _table(connector, name: str, cases: list[str] | None = None, combos: list[str] | None = None):
    import pandas as pd

    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases or []))
    tables.SetLoadCombinationsSelectedForDisplay(list(combos or []))
    try:
        return connector._read_database_table(name)
    except Exception:  # noqa: BLE001 - a table the model does not have
        return pd.DataFrame()


def _names(api) -> list[str]:
    from etabs_api.core.helpers import as_list

    return [str(n) for n in as_list(api.GetNameList(0, [])[1])]


def story_order(model) -> list[str]:
    from etabs_api.core.helpers import as_list

    return [str(n) for n in as_list(model.Story.GetStories()[1])][::-1]   # the top first


def displacement_rows(connector, cases: list[str] | None = None,
                      combos: list[str] | None = None) -> list[tuple]:
    """(story, case, direction, maximum, average) of the static seismic cases (or the
    check combinations), each in its own direction."""
    from etabs_api.workflows.irregularity import own_direction

    table = _table(connector, "Story Max Over Avg Displacements", cases, combos)
    if table.empty:
        return []
    rows = [(str(r["Story"]), str(r["OutputCase"]), str(r["Direction"]), _num(r["Maximum"]),
             _num(r["Average"])) for _, r in table.iterrows()]
    return own_direction(rows)


def story_torques(connector, cases: list[str]) -> dict[tuple[str, str], float]:
    """(story, case) -> the story torque T of each case (present units)."""
    table = _table(connector, "Story Forces", cases)
    out = {}
    for _, r in table.iterrows():
        if str(r.get("Location")) == "Top":
            out[(str(r["Story"]), str(r["OutputCase"]))] = _num(r["T"])
    return out


def diaphragm_joints(model) -> dict[str, str]:
    """A joint on the rigid diaphragm of each story (story -> joint)."""
    from etabs_api.workflows.grid_column_model import _table as rows

    out = {}
    for r in rows(model, "Joint Assignments - Diaphragms"):
        if str(r.get("Diaphragm") or "") not in ("", "None") and r.get("Story"):
            out.setdefault(str(r["Story"]), str(r["UniqueName"]))
    return out


@dataclass
class AxResult:
    ax: dict[tuple[str, str], float] = field(default_factory=dict)
    rounds: int = 0
    patterns: list[str] = field(default_factory=list)
    spectrum_eccentricity: float | None = None
    notes: list[str] = field(default_factory=list)
    settled: bool = False
    change: float = 0.0

    @property
    def applied(self) -> bool:
        return any(v > 1.0 + 1e-9 for v in self.ax.values())


def _add_to_combinations(model, pattern: str, added: str) -> int:
    """Put ``added`` in every combination that has the load case ``pattern``, with
    its factor. Returns how many."""
    from etabs_api.core.helpers import as_list

    count = 0
    for combo in _names(model.RespCombo):
        got = model.RespCombo.GetCaseList(combo, 0, [], [], [])
        kinds, names, factors = as_list(got[1]), as_list(got[2]), as_list(got[3])
        items = dict(((int(k), str(n)), float(f)) for k, n, f in zip(kinds, names, factors))
        if (0, pattern) in items and (0, added) not in items:
            model.RespCombo.SetCaseList(combo, 0, added, items[(0, pattern)])
            count += 1
    return count


def apply_ax(connector, run, progress=None) -> AxResult:
    """Amplify the accidental torsion of each level by its Ax (see the module), and
    analyse again until Ax settles (at most AX_ROUNDS). ``run`` runs the analysis;
    the model is analysed when this is called."""
    from etabs_api.workflows import model_setup as ms
    from etabs_api.workflows.model_analysis import spectrum_cases

    say = progress or (lambda text: None)
    model = connector.sap_model
    out = AxResult()
    cases = set(_names(model.LoadCases))
    pairs = [(p, m, d) for p, m, d in STATIC_PAIRS if p in cases and m in cases]
    if not pairs:
        out.notes.append("Ax: no EQXPE / EQXNE (EQYPE / EQYNE) static seismic cases; not "
                         "applied.")
        return out
    units = int(model.GetPresentUnits())
    model.SetPresentUnits(ms.UNITS_N_MM)
    try:
        order = story_order(model)
        statics = [c for p, m, _ in pairs for c in (p, m)]
        ax = level_ax(displacement_rows(connector, statics))
        out.ax = dict(ax)
        if not out.applied:
            out.notes.append("Ax: every level is within 1.2 of its average displacement; "
                             "no torsion amplified.")
            return out
        joints = diaphragm_joints(model)
        accidental = {p: accidental_torques(story_torques(connector, [p, m]), order, p, m)
                      for p, m, _ in pairs}
        checks = {}
        for round_number in range(1, AX_ROUNDS + 1):
            say(f"Ax round {round_number}: amplifying the accidental torsion")
            if model.GetModelIsLocked():
                model.SetModelIsLocked(False)
            for plus, minus, direction in pairs:
                for pattern, sign in ((plus, 1.0), (minus, -1.0)):
                    added = AX_PREFIX + pattern
                    if added not in _names(model.LoadPatterns):
                        model.LoadPatterns.Add(added, 8, 0.0, True)   # eLoadPatternType Other
                        _add_to_combinations(model, pattern, added)
                        out.patterns.append(added)
                    torques = added_torques(ax, accidental[plus], direction, sign)
                    for story in order:
                        joint = joints.get(story)
                        if joint is None:
                            continue
                        model.PointObj.SetLoadForce(
                            joint, added, [0.0, 0.0, 0.0, 0.0, 0.0, torques.get(story, 0.0)],
                            True, "Global", 0)
                    check = f"AXCHK {pattern}"
                    if check not in _names(model.RespCombo):
                        model.RespCombo.Add(check, 0)
                        model.RespCombo.SetCaseList(check, 0, pattern, 1.0)
                        model.RespCombo.SetCaseList(check, 0, added, 1.0)
                    checks[pattern] = check
            largest = max(ax.values())
            out.spectrum_eccentricity = ECCENTRICITY * largest
            for case in spectrum_cases(connector):
                model.LoadCases.ResponseSpectrum.SetEccentricity(case, out.spectrum_eccentricity)
            run()
            out.rounds = round_number
            again = level_ax(displacement_rows(connector, combos=list(checks.values())))
            if settled(ax, again):
                ax = again
                out.settled = True
                break
            out.change = max(abs(again.get(k, 1.0) - ax.get(k, 1.0)) for k in set(ax) | set(again))
            ax = again
        out.ax = dict(ax)
        if not out.settled:
            out.notes.append(f"Ax had not settled after {AX_ROUNDS} rounds (the last change "
                             f"{out.change:.2f}): the amplified torsion raises the drift ratio "
                             "again; the values of the last round are applied. A level at 3.00 "
                             "is at the cap of Eq. 208-7.")
        missing = [s for s in order if s not in joints]
        if missing:
            out.notes.append("Ax: no rigid diaphragm joint at " + ", ".join(missing)
                             + ": no torsion added there.")
    finally:
        model.SetPresentUnits(units)
    return out


def ground_area(connector, base_story: str | None) -> float:
    """AB: the plan area in m2 of the lowest floor above the base, overhangs in."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows.concrete_quantities import FloorShell, footprint
    from etabs_api.workflows.foundation_plan import read_shells
    from etabs_api.workflows.framing_plans import _number
    from etabs_api.workflows.grid_column_model import _table as rows
    from etabs_api.workflows.model_3d import nearest_story

    model = connector.sap_model
    points = {str(r["UniqueName"]): (_number(r["X"]), _number(r["Y"]), _number(r["Z"]))
              for r in rows(model, "Point Object Connectivity")}
    got = model.Story.GetStories()
    stories = list(zip([str(n) for n in as_list(got[1])], [float(z) for z in as_list(got[2])]))
    names = [n for n, _ in stories]
    lowest = names.index(base_story) + 1 if base_story in names else 1
    shells = [FloorShell(s.name, nearest_story(s.z, stories), list(s.points), s.thickness,
                         s.opening) for s in read_shells(model, points)]
    for story in names[lowest:]:
        own = [s for s in shells if s.story == story]
        if own:
            return footprint(own, [])
    return 0.0


def compute_rho(connector, setup: dict | None = None, progress=None) -> dict[str, Rho]:
    """rho in X and Y from the + eccentricity static cases (see the module)."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows import model_setup as ms
    from etabs_api.workflows.grid_column_model import _table as rows
    from etabs_api.workflows.irregularity import _seismic_base

    say = progress or (lambda text: None)
    model = connector.sap_model
    setup = ms.merge_settings(setup)
    system = ms.system_of(setup)
    zone = float(setup["seismic"].get("zone_factor", 0.4))
    units = int(model.GetPresentUnits())
    model.SetPresentUnits(ms.UNITS_N_MM)
    out: dict[str, Rho] = {}
    try:
        got = model.Story.GetStories()
        elevations = dict(zip((str(n) for n in as_list(got[1])), (float(z) for z in
                                                                     as_list(got[2]))))
        base = _seismic_base(model)
        if base in elevations:
            base_z = elevations[base]
        else:       # the bottom of the lowest story
            base_z = float(as_list(got[2])[0]) - float(as_list(got[3])[0]) if elevations else 0.0
        stories = lower_two_thirds({s: z for s, z in elevations.items() if z > base_z + 1.0},
                                   base_z)
        say("Redundancy factor: the ground floor area")
        area = ground_area(connector, base)
        points = {str(r["UniqueName"]): (_num(r["X"]), _num(r["Y"]), _num(r["Z"]))
                  for r in rows(model, "Point Object Connectivity")}
        tops: dict[str, dict[str, str]] = {}     # story -> top joint -> column
        top_xy: dict[str, dict[str, tuple[float, float]]] = {}
        angle: dict[str, float] = {}
        story_of: dict[str, str] = {}
        for r in rows(model, "Column Object Connectivity"):
            a, b = str(r["UniquePtI"]), str(r["UniquePtJ"])
            if a not in points or b not in points:
                continue
            top = a if points[a][2] > points[b][2] else b
            name, story = str(r["UniqueName"]), str(r["Story"])
            story_of[name] = story
            tops.setdefault(story, {})[top] = name
            top_xy.setdefault(story, {})[name] = points[top][:2]
            angle[name] = _num(model.FrameObj.GetLocalAxes(name)[0])
        beams: dict[str, list[tuple[str, str]]] = {}
        for r in rows(model, "Beam Object Connectivity"):
            story = str(r["Story"])
            ends = [tops.get(story, {}).get(str(r[k])) for k in ("UniquePtI", "UniquePtJ")]
            if all(ends):
                beams.setdefault(story, []).append((ends[0], ends[1]))
        for plus, _, direction in STATIC_PAIRS:
            if plus not in _names(model.LoadCases):
                continue
            say(f"Redundancy factor: element story shears of {plus}")
            forces = _table(connector, "Story Forces", [plus])
            shear = {str(r["Story"]): abs(_num(r["VX" if direction == "X" else "VY"]))
                     for _, r in forces.iterrows() if str(r.get("Location")) == "Bottom"}
            columns = _table(connector, "Element Forces - Columns", [plus])
            column_shear: dict[str, float] = {}
            for _, r in columns.iterrows():
                name = str(r.get("UniqueName"))
                a = math.radians(angle.get(name, 0.0))
                v2, v3 = _num(r.get("V2")), _num(r.get("V3"))
                along = v2 * math.cos(a) - v3 * math.sin(a) if direction == "X" else \
                    v2 * math.sin(a) + v3 * math.cos(a)
                column_shear[name] = max(column_shear.get(name, 0.0), abs(along))
            piers = _table(connector, "Pier Forces", [plus])
            props = _table(connector, "Pier Section Properties")
            length, runs_x = {}, {}
            for _, r in props.iterrows():
                key = (str(r.get("Story")), str(r.get("Pier")))
                length[key] = max(_num(r.get("WidthBot")), _num(r.get("WidthTop"))) / 1000.0
                axis = _num(r.get("AxisAngle")) % 180.0
                runs_x[key] = axis < 45.0 or axis > 135.0
            # the story shear: the shears the story's columns and walls carry in the
            # direction, or the story force when larger. Where walls carry the shear
            # back (a podium, a backstay), ETABS gives a small net story force that
            # would make every column look like the whole story
            carried: dict[str, float] = {}
            for name, value in column_shear.items():
                carried[story_of.get(name, "")] = carried.get(story_of.get(name, ""), 0.0) + value
            for _, r in piers.iterrows():
                key = (str(r.get("Story")), str(r.get("Pier")))
                if str(r.get("Location")) == "Bottom" and runs_x.get(key, True) == (
                        direction == "X"):
                    carried[key[0]] = carried.get(key[0], 0.0) + abs(_num(r.get("V2")))
            best = Rho(direction, area=area)
            for story in stories:
                v = max(shear.get(story, 0.0), carried.get(story, 0.0))
                bays = frame_bays(top_xy.get(story, {}), beams.get(story, []), direction == "X")
                ratio, where = story_ratio(column_shear, bays, v)
                if ratio > best.r_max:
                    best.r_max, best.story, best.element = ratio, story, f"columns {where}"
                for _, r in piers.iterrows():
                    if str(r.get("Story")) != story or str(r.get("Location")) != "Bottom":
                        continue
                    pier = str(r.get("Pier"))
                    if runs_x.get((story, pier), True) != (direction == "X"):
                        continue        # a wall resists the shear along its length
                    lw = length.get((story, pier), 0.0)
                    ratio = wall_ratio(_num(r.get("V2")), lw, v)
                    if ratio > best.r_max:
                        best.r_max, best.story, best.element = ratio, story, f"wall {pier}"
            best.rho, best.computed, best.notes, best.over_smrf = rho_value(
                best.r_max, area, system, zone)
            out[direction] = best
    finally:
        model.SetPresentUnits(units)
    return out


def rho_combinations(model, rho: dict[str, float], log=None) -> int:
    """Put rho x and rho y in the strength and service seismic combinations of the
    setup: EQ_RHO_nn and RSA_RHO_nn, the directional combinations with rho x on the X
    cases and rho y on the Y cases, take the place of EQ_COMBO_nn and RSA_COMBO_nn in
    every ULS and SLS combination (their factor divided by the rho it had). The Em
    combinations keep the directional combinations without rho. Returns how many
    combinations now carry rho."""
    from design.criteria.code_config import NSCP
    from etabs_api.core.helpers import as_list

    baseline = NSCP.load_factors.rho
    combos = _names(model.RespCombo)
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    done = 0
    for label in ("EQ", "RSA"):
        for n in range(1, 9):
            plain, rhoed = f"{label}_COMBO_{n:02d}", f"{label}_{RHO_SET}_{n:02d}"
            if plain not in combos:
                continue
            got = model.RespCombo.GetCaseList(plain, 0, [], [], [])
            items = list(zip((int(k) for k in as_list(got[1])), (str(c) for c in
                                                                  as_list(got[2])),
                             (float(f) for f in as_list(got[3]))))
            if rhoed in combos:
                model.RespCombo.Delete(rhoed)
            model.RespCombo.Add(rhoed, 0)
            for kind, case, factor in items:
                model.RespCombo.SetCaseList(rhoed, kind, case,
                                            factor * rho[case_direction(case)])
            for combo in combos:
                if not combo.startswith(("ULS ", "SLS ")):
                    continue
                got = model.RespCombo.GetCaseList(combo, 0, [], [], [])
                found = {(int(k), str(c)): float(f) for k, c, f in zip(
                    as_list(got[1]), as_list(got[2]), as_list(got[3]))}
                factor = found.get((1, plain))
                if factor is None:
                    old = found.get((1, rhoed))
                    if old is None:
                        continue
                    factor = old       # already moved: keep its factor
                else:
                    model.RespCombo.DeleteCase(combo, 1, plain)
                    factor /= baseline
                model.RespCombo.SetCaseList(combo, 1, rhoed, factor)
                done += 1
    if log is not None:
        log.done("combinations with rho", done)
    return done
