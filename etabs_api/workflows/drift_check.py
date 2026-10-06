"""Story drift with the drift stiffness (``python main.py drift`` / ``sdt drift``).

ETABS keeps stiffness modifiers per member, not per load case, so the drift
needs its own analysis. On the model open in ETABS (no copy):

1. Remember the frame modifiers, the response spectrum scale factors and
   whether the model had results, and judge the modifiers you assigned:
   effective I (frame x section) of 0.35 / 0.70, mass and weight 1.
2. For each stiffness level, run, scale the spectrum cases (RSAXD / RSAYD to
   the drift patterns) and read the drift of the DRIFT and WDRIFT
   combinations (NSCP 208.6.4.1):

   * as modelled: your modifiers, unchanged;
   * strength level: beams 0.35, columns 0.70 (ACI 6.6.3.1.1, NSCP 406.6.3.1.1);
   * service level: 1.4 times those, at most the gross section (ACI 6.6.3.2.2).

3. Put the modifiers and scale factors back and, when the model had results,
   analyse it again: it is left as it was, for the strength design.

The drift is read at the diaphragm centre of mass, or at the four outermost
column joints of each story (the extreme joints along the two diagonals).
Seismic drift is checked as Delta_M = 0.7 R Delta_S against 0.025 or 0.020 of
the story height (NSCP 208.6.5.1); wind drift against h / the typed limit.
The values are in design/code_config.py.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import NSCP  # noqa: E402
from etabs_api.core.helpers import as_list, return_code  # noqa: E402
from etabs_api.workflows import model_check as mc  # noqa: E402

CENTER, CORNERS = "center", "corners"
STATIC, SPECTRUM, BOTH = "static", "spectrum", "both"  # which seismic drift combinations
SEISMIC_TABLE = "Load Pattern Definitions - Auto Seismic - UBC 97"
I22, I33 = 4, 5  # positions in the eight ETABS modifiers


@dataclass
class StiffnessLevel:
    name: str
    beam: float | None  # None: the modifiers as assigned in the model
    column: float | None
    reference: str
    at_least_modelled: bool = False  # a member modelled stiffer than the level keeps its own I

    @property
    def as_modelled(self) -> bool:
        return self.beam is None


def stiffness_levels(service_factor: float | None = None) -> list[StiffnessLevel]:
    """As modelled, strength level (ACI 6.6.3.1.1) and service level.

    The service level is ``service_factor`` times the strength level, 1.4 by
    the code (ACI 6.6.3.2.2) unless the engineer gives another, never above
    the gross stiffness. At the service level a member that was modelled
    stiffer than the level keeps its own stiffness, so that a beam given
    0.5 Ig for its flange is not made softer than it was modelled.
    """
    k = NSCP.analysis
    factor = k.service_stiffness_factor if not service_factor else float(service_factor)

    def service(value: float) -> float:
        return min(factor * value, k.max_inertia)

    return [
        StiffnessLevel("As modelled", None, None, "your frame modifiers"),
        StiffnessLevel("Strength level", k.beam_inertia, k.column_inertia,
                       "ACI 6.6.3.1.1, NSCP 406.6.3.1.1"),
        StiffnessLevel(f"Service level ({factor:g} x)",
                       service(k.beam_inertia), service(k.column_inertia),
                       "ACI 6.6.3.2.2, NSCP 406.6.3.2.2" if factor == k.service_stiffness_factor
                       else f"your factor; ACI 6.6.3.2.2 gives {k.service_stiffness_factor:g}",
                       at_least_modelled=True),
    ]


# =============================================================================
# PLAIN DATA
# =============================================================================
def outer_corners(points: pd.DataFrame, columns: pd.DataFrame) -> dict[str, list[str]]:
    """The outermost column joints of each story: the extremes along the two diagonals.

    The column tops (``UniquePtJ``) of each story are the candidates; the
    corners are those with the largest x + y, x - y, -x + y and -x - y. A story
    with fewer distinct corners (a single line of columns) gets fewer.
    """
    xy = {str(n): (float(x), float(y)) for n, x, y in zip(
        points["UniqueName"], pd.to_numeric(points["X"]), pd.to_numeric(points["Y"]))}
    out: dict[str, list[str]] = {}
    for story, rows in columns.groupby(columns["Story"].astype(str), sort=False):
        joints = [str(j) for j in rows["UniquePtJ"] if str(j) in xy]
        if not joints:
            continue
        corners = []
        for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            best = max(joints, key=lambda j: sx * xy[j][0] + sy * xy[j][1])
            if best not in corners:
                corners.append(best)
        out[story] = corners
    return out


def corner_drifts(table: pd.DataFrame, corners: dict[str, list[str]]
                  ) -> dict[str, tuple[float, str]]:
    """Largest drift ratio of each combination at the corner joints (``Joint Drifts``)."""
    wanted = {(story, joint) for story, joints in corners.items() for joint in joints}
    if table.empty:
        return {}
    keys = list(zip(table["Story"].astype(str), table["UniqueName"].astype(str)))
    rows = table[[key in wanted for key in keys]].copy()
    rows["ratio"] = pd.concat([pd.to_numeric(rows["DriftX"], errors="coerce").abs(),
                               pd.to_numeric(rows["DriftY"], errors="coerce").abs()],
                              axis=1).max(axis=1)
    out = {}
    for combo, group in rows.groupby(rows["OutputCase"].astype(str)):
        at = group["ratio"].idxmax()
        out[combo] = (float(group.loc[at, "ratio"]),
                      f"{group.loc[at, 'Story']} (joint {group.loc[at, 'UniqueName']})")
    return out


def center_drifts(table: pd.DataFrame, base_elevation: float) -> dict[str, tuple[float, str]]:
    """Largest drift ratio of each combination at the diaphragm centres of mass.

    The drift of a story is the change of the centre-of-mass displacement from
    the story below (the base below the lowest one), over the story height.
    Spectrum combinations come as Max and Min envelopes; each is taken in turn.
    """
    if table.empty:
        return {}
    data = table.copy()
    for column in ("UX", "UY", "Z"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    out: dict[str, tuple[float, str]] = {}
    keys = [data["OutputCase"].astype(str), data["Diaphragm"].astype(str),
            data["StepType"].astype(str)]
    for (combo, diaphragm, _), rows in data.groupby(keys, sort=False):
        rows = rows.sort_values("Z")
        below_z, below_u = base_elevation, (0.0, 0.0)
        for _, r in rows.iterrows():
            height = r["Z"] - below_z
            if height > 0:
                ratio = max(abs(r["UX"] - below_u[0]), abs(r["UY"] - below_u[1])) / height
                if ratio > out.get(combo, (-1.0, ""))[0]:
                    out[combo] = (float(ratio), f"{r['Story']} ({diaphragm})")
            below_z, below_u = r["Z"], (r["UX"], r["UY"])
    return out


def corner_story_drifts(table: pd.DataFrame, corners: dict[str, list[str]]
                        ) -> dict[tuple[str, str], float]:
    """Largest drift ratio of each (combination, story) at the corner joints."""
    wanted = {(story, joint) for story, joints in corners.items() for joint in joints}
    if table.empty:
        return {}
    keys = list(zip(table["Story"].astype(str), table["UniqueName"].astype(str)))
    rows = table[[key in wanted for key in keys]].copy()
    rows["ratio"] = pd.concat([pd.to_numeric(rows["DriftX"], errors="coerce").abs(),
                               pd.to_numeric(rows["DriftY"], errors="coerce").abs()],
                              axis=1).max(axis=1)
    worst = rows.groupby([rows["OutputCase"].astype(str), rows["Story"].astype(str)],
                         sort=False)["ratio"].max()
    return {(combo, story): float(ratio) for (combo, story), ratio in worst.items()
            if ratio == ratio}


def center_story_drifts(table: pd.DataFrame, base_elevation: float
                        ) -> dict[tuple[str, str], float]:
    """Largest drift ratio of each (combination, story) at the diaphragm centres of mass
    (see ``center_drifts``)."""
    if table.empty:
        return {}
    data = table.copy()
    for column in ("UX", "UY", "Z"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    out: dict[tuple[str, str], float] = {}
    keys = [data["OutputCase"].astype(str), data["Diaphragm"].astype(str),
            data["StepType"].astype(str)]
    for (combo, _, _), rows in data.groupby(keys, sort=False):
        rows = rows.sort_values("Z")
        below_z, below_u = base_elevation, (0.0, 0.0)
        for _, r in rows.iterrows():
            height = r["Z"] - below_z
            if height > 0:
                ratio = max(abs(r["UX"] - below_u[0]), abs(r["UY"] - below_u[1])) / height
                key = (combo, str(r["Story"]))
                out[key] = max(float(ratio), out.get(key, 0.0))
            below_z, below_u = r["Z"], (r["UX"], r["UY"])
    return out


def story_table(by_story: dict[tuple[str, str], float], order: list[str], is_wind,
                r_factor: float | None) -> list[tuple]:
    """One row per story, the top first: (story, seismic drift ratio, Delta_M = 0.7 R x it,
    its combination, wind drift ratio, its combination). None where there is none."""
    rows = []
    stories = [s for s in order if any(story == s for _, story in by_story)]
    stories += sorted({story for _, story in by_story} - set(stories))
    for story in stories:
        seismic = [(ratio, combo) for (combo, at), ratio in by_story.items()
                   if at == story and not is_wind(combo)]
        wind = [(ratio, combo) for (combo, at), ratio in by_story.items()
                if at == story and is_wind(combo)]
        s_ratio, s_combo = max(seismic) if seismic else (None, None)
        w_ratio, w_combo = max(wind) if wind else (None, None)
        delta_m = None if s_ratio is None or not r_factor else 0.7 * r_factor * s_ratio
        rows.append((story, s_ratio, delta_m, s_combo, w_ratio, w_combo))
    return rows


def story_table_text(rows: list[tuple]) -> list[str]:
    """The lines of ``story_table`` for the report."""
    if not rows:
        return []
    lines = ["Drift of every story (the largest of the combinations checked):",
             f"  {'Story':<20}{'seismic':>10}{'Delta_M':>10}  {'combination':<44}"
             f"{'wind':>9}  combination"]
    for story, s_ratio, delta_m, s_combo, w_ratio, w_combo in rows:
        seismic = "-" if s_ratio is None else f"{s_ratio:.5f}"
        inelastic = "-" if delta_m is None else f"{delta_m:.4f}"
        wind = "-" if not w_ratio else f"h/{1.0 / w_ratio:,.0f}"
        lines.append(f"  {story:<20}{seismic:>10}{inelastic:>10}  {str(s_combo or '-')[:42]:<44}"
                     f"{wind:>9}  {str(w_combo or '-')[:42]}")
    return lines


def typed_minimum_findings(seismic: pd.DataFrame, pattern_types: dict[str, int]
                           ) -> list[mc.Finding]:
    """The drift patterns whose forces are under the zone 4 minimum of Eq. 208-11.

    With Ca and Cv typed in, ETABS applies the minimum with Nv = 1
    (``model_check.typed_minimum_finding``), so the drift read on such a
    pattern is too small by the same share. One finding for each pattern.
    """
    out, seen = [], set()
    if seismic.empty or not {"Name", "Ca", "Cv", "I", "R", "CoeffUsed"} <= set(seismic.columns):
        return out
    for _, r in seismic.iterrows():
        parent = str(r["Name"]).split("(")[0]
        typed = "Z" not in r.index or pd.isna(r.get("Z"))
        if parent in seen or not typed or pattern_types.get(parent) != mc.SEISMIC_DRIFT:
            continue
        finding = mc.typed_minimum_finding(parent, mc._num(r["Ca"]), mc._num(r["Cv"]),
                                           mc._num(r["I"]), mc._num(r["R"]),
                                           mc._num(r["CoeffUsed"]))
        if finding is not None:
            seen.add(parent)
            finding.text += ". The drift of this pattern is too small by the same share"
            out.append(finding)
    return out


def level_findings(drifts: dict[str, tuple[float, str]], seismic: pd.DataFrame,
                   pattern_types: dict[str, int], wind_denominator: float,
                   drift_cases: dict[str, tuple[str, bool]] | None = None,
                   r_factor: float | None = None) -> list[mc.Finding]:
    """The NSCP seismic and the wind drift checks of one stiffness level.

    ``drift_cases`` is for combinations the user picked on a model without
    the DRIFT / WDRIFT names: combination -> (lateral case, is wind).
    ``r_factor`` is used when no UBC 97 pattern gives R.
    """
    d = mc.ModelData(pattern_types=dict(pattern_types), drifts=dict(drifts),
                     drift_from="combinations", wind_drift_denominator=wind_denominator,
                     tables={SEISMIC_TABLE: seismic}, drift_case_of=dict(drift_cases or {}),
                     r_factor=r_factor)
    return mc._check_drift(d, seismic) + mc.check_wind_drift(d)


# =============================================================================
# ETABS
# =============================================================================
@dataclass
class DriftReport:
    model_path: str = ""
    reference: str = CENTER
    seismic: str = BOTH
    wind_denominator: float = NSCP.wind.drift_limit_denominator
    levels: list[tuple[StiffnessLevel, list[mc.Finding], dict[str, float]]] = field(
        default_factory=list)
    restored: bool = False
    notes: list[str] = field(default_factory=list)
    picked: list[str] = field(default_factory=list)  # combinations the user picked, if any
    stories: dict[str, list[tuple]] = field(default_factory=dict)  # level name: story_table rows
    modifiers: list[mc.Finding] = field(default_factory=list)  # your modifiers, judged
    modelled: str = ""  # the effective I of your modifiers, as text

    def text(self) -> str:
        where = ("diaphragm centre of mass" if self.reference == CENTER
                 else "outer four column joints of each story")
        which = {STATIC: "static drift patterns (EQXSD, EQYSD)",
                 SPECTRUM: "spectrum drift cases (RSAXD, RSAYD)",
                 BOTH: "static drift patterns and spectrum drift cases"}[self.seismic]
        combinations = (f"{len(self.picked)} combinations you picked ("
                        + ", ".join(self.picked[:6]) + (", ..." if len(self.picked) > 6 else "")
                        + ")" if self.picked else
                        f"DRIFT combinations (203.3, rho 1.0) on the {which}; WDRIFT combinations")
        lines = ["", "=" * 96, "STORY DRIFT (NSCP 208.6.4, 208.6.5)", "=" * 96,
                 f"Model: {self.model_path}", f"Drift read at the {where}; {combinations}, "
                 f"wind limit h/{self.wind_denominator:g}"]
        if self.modifiers:
            lines += ["", "--- Your modifiers (frame x section, as ETABS uses them) ---"]
            lines += [f"[{f.status:>4}] {f.text}   ({f.ref})" for f in self.modifiers]
        for level, findings, periods in self.levels:
            stiffness = (self.modelled if level.as_modelled else
                         f"beams I {level.beam:.2f}, columns I {level.column:.2f}")
            lines += ["", f"--- {level.name}: {stiffness} ({level.reference}) ---"]
            if periods:
                lines.append("Drift pattern periods: " + ", ".join(
                    f"{case} {t:.3f} s" for case, t in sorted(periods.items())))
            lines += [f"[{f.status:>4}] {f.text}   ({f.ref})" for f in findings]
            lines += story_table_text(self.stories.get(level.name, []))
        lines += [""] + self.notes
        lines.append("Model restored to its strength modifiers and spectrum scale factors"
                     + (" and analysed again." if self.restored else "."))
        lines.append("=" * 96)
        return "\n".join(lines)


def _modelled_text(effective: dict[str, tuple], columns: set[str]) -> str:
    """The range of the effective I33 of the beams and the columns."""
    def span(values: list[float]) -> str:
        if not values:
            return "-"
        low, high = min(values), max(values)
        return f"{low:.2f}" if abs(high - low) < 1e-3 else f"{low:.2f} to {high:.2f}"

    beams = [v[5] for n, v in effective.items() if n not in columns]
    cols = [v[5] for n, v in effective.items() if n in columns]
    return f"beams I {span(beams)}, columns I {span(cols)}"


def _ok(result) -> bool:
    return return_code(result) == 0


def _frames(connector) -> tuple[list[str], set[str], dict[str, str]]:
    """Every beam and column, the columns, and each frame's section."""
    beams = connector._read_database_table("Beam Object Connectivity")
    columns = connector._read_database_table("Column Object Connectivity")
    sections = connector._read_database_table("Frame Assignments - Section Properties")
    names = [str(n) for n in beams.get("UniqueName", [])] + \
        [str(n) for n in columns.get("UniqueName", [])]
    return (names, {str(n) for n in columns.get("UniqueName", [])},
            dict(zip(sections["UniqueName"].astype(str), sections["SectProp"].astype(str))))


def _spectrum_loads(model) -> dict[str, tuple]:
    api = model.LoadCases.ResponseSpectrum
    names = [str(n) for n in as_list(model.LoadCases.GetNameList()[1])]
    out = {}
    for name in names:
        try:
            loads = api.GetLoads(name)
        except Exception:
            continue
        if return_code(loads) == 0 and int(loads[0]) > 0:
            out[name] = loads
    return out


def _restore_spectrum(model, saved: dict[str, tuple]) -> None:
    api = model.LoadCases.ResponseSpectrum
    for name, (count, directions, functions, scales, systems, angles, _) in saved.items():
        api.SetLoads(name, count, as_list(directions), as_list(functions), as_list(scales),
                     as_list(systems), as_list(angles))


def select_combinations(names: list[str], seismic: str = BOTH) -> list[str]:
    """The drift combinations to check: every WDRIFT one, and the DRIFT ones on the
    static drift patterns (``STATIC``), the spectrum drift cases (``SPECTRUM``) or both."""
    from etabs_api.workflows.load_combinations import (
        DRIFT_SET,
        DRIFT_SPECTRUM_CASES,
        WIND_DRIFT_SET,
    )

    out = []
    for name in names:
        if name.startswith(WIND_DRIFT_SET + " "):
            out.append(name)
        elif name.startswith(DRIFT_SET + " "):
            spectral = name.split()[-1] in DRIFT_SPECTRUM_CASES
            if seismic == BOTH or (seismic == SPECTRUM) == spectral:
                out.append(name)
    return out


def _drift_combinations(model, seismic: str = BOTH) -> list[str]:
    return select_combinations([str(n) for n in as_list(model.RespCombo.GetNameList()[1])],
                               seismic)


def _read(connector, name: str, combos: list[str], cases: list[str] | None = None
          ) -> pd.DataFrame:
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases or []))
    tables.SetLoadCombinationsSelectedForDisplay(list(combos))
    return connector._read_database_table(name)


def drift_load_cases(connector) -> dict[str, tuple[str, bool]]:
    """The load cases a drift can be read on directly: {case: (case, is wind)}.

    The static seismic drift patterns (EQXSD, EQYSD: the forces of the period
    without its cap, NSCP 208.6.5.2) and the wind patterns, where each is a
    linear static load case of the model.
    """
    model = connector.sap_model
    patterns = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    cases = {str(n) for n in as_list(model.LoadCases.GetNameList(0, [])[1])}
    out = {}
    for name in patterns:
        kind = int(model.LoadPatterns.GetLoadType(name)[0])
        if name in cases and kind in (mc.SEISMIC_DRIFT, mc.WIND):
            out[name] = (name, kind == mc.WIND)
    return out


def run_drift(connector, reference: str = CENTER,
              wind_denominator: float = NSCP.wind.drift_limit_denominator,
              levels: list[StiffnessLevel] | None = None, progress=None,
              seismic: str = BOTH, combos: list[str] | None = None,
              drift_cases: dict[str, tuple[str, bool]] | None = None,
              r_factor: float | None = None, cases: list[str] | None = None) -> DriftReport:
    """Drift at each stiffness level on the open model, then restore it (see the module).

    ``seismic`` picks the seismic drift combinations: on the static drift
    patterns, on the spectrum drift cases, or both. Without spectrum ones, the
    spectrum is not scaled (one analysis less per level).

    ``combos`` with ``drift_cases`` are the combinations the user picked on a
    model that has no DRIFT / WDRIFT combinations (``resolve_drift_combinations``).
    ``cases`` are load cases to read the drift on directly, in place of
    combinations (``drift_load_cases``).
    """
    from etabs_api.workflows.model_analysis import scale_spectrum_to_static

    model = connector.sap_model
    say = progress or (lambda text: None)
    from etabs_api.core.connection import model_file

    report = DriftReport(model_path=model_file(model), reference=reference,
                         wind_denominator=wind_denominator, seismic=seismic)
    picked = bool(combos)
    cases = list(cases or [])
    combos = list(combos) if combos else ([] if cases else _drift_combinations(model, seismic))
    if cases:
        report.picked = list(cases)
        report.notes.append(
            "The drift is read on the lateral load cases themselves (" + ", ".join(cases) + "): "
            "the seismic ones are the drift patterns, on the forces of the period without its "
            "cap (NSCP 208.6.5.2). No gravity load acts with them, so the P-delta effect of the "
            "combinations of 208.6.4.1 is not in these values.")
    elif not combos:
        raise RuntimeError("The model has no DRIFT / WDRIFT combinations and none was picked: "
                           "run sdt drift to pick them, or sdt setup to add the standard ones.")
    if picked and not cases:
        report.picked = list(combos)
        report.notes.append(
            "The drift is on combinations you picked: their factors and load cases are yours "
            "to confirm (NSCP 208.6.4.1 uses the 203.3 combinations with rho = 1.0). Where a "
            "seismic case in them uses the capped period of the strength design (NSCP "
            "208.5.2.2), the drift is on larger forces than 208.6.5.2 requires, which is on "
            "the safe side.")
    had_results = mc._has_results(connector)
    names, columns, section_of = _frames(connector)
    original = {n: list(model.FrameObj.GetModifiers(n, [])[0]) for n in names}
    section_mods = {}
    for prop in set(section_of.values()):
        section_mods[prop] = list(model.PropFrame.GetModifiers(prop, [])[0])
    effective = {n: tuple(a * b for a, b in zip(
        original[n], section_mods.get(section_of.get(n, ""), [1.0] * 8))) for n in names}
    report.modifiers = mc.modifier_findings(effective, columns, group="Modifiers")
    report.modelled = _modelled_text(effective, columns)
    spectrum = _spectrum_loads(model)
    patterns = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    pattern_types = {n: int(model.LoadPatterns.GetLoadType(n)[0]) for n in patterns}
    corners = None
    if reference == CORNERS:
        corners = outer_corners(connector._read_database_table("Point Object Connectivity"),
                                connector._read_database_table("Column Object Connectivity"))
    base_elevation = float(as_list(model.Story.GetStories()[2])[0])
    story_order = [str(n) for n in as_list(model.Story.GetStories()[1])][::-1]   # the top first

    def run():
        connector.analysis.run()

    try:
        for level in levels or stiffness_levels():
            model.SetModelIsLocked(False)
            _restore_spectrum(model, spectrum)  # every level scales from the original factors
            if level.as_modelled:
                say(f"{level.name}: your modifiers")
                for name, values in original.items():
                    model.FrameObj.SetModifiers(name, values)
            else:
                say(f"{level.name}: setting beams I {level.beam:.2f}, columns I "
                    f"{level.column:.2f}")
            for name in ([] if level.as_modelled else names):
                target = level.column if name in columns else level.beam
                section = section_mods.get(section_of.get(name, ""), [1.0] * 8)
                values = list(original[name])
                for index in (I22, I33):
                    wanted = target
                    if level.at_least_modelled:   # not softer than it was modelled
                        wanted = max(target, min(effective[name][index], NSCP.analysis.max_inertia))
                    values[index] = wanted / section[index] if section[index] else wanted
                if not _ok(model.FrameObj.SetModifiers(name, values)):
                    report.notes.append(f"Could not set the modifiers of {name}.")
            say(f"{level.name}: running the analysis")
            run()
            if seismic != STATIC:
                say(f"{level.name}: scaling the response spectrum cases")
                scale_spectrum_to_static(connector, run)
            say(f"{level.name}: reading the drift")
            if reference == CORNERS:
                moved = _read(connector, "Joint Drifts", combos, cases)
                drifts = corner_drifts(moved, corners)
                by_story = corner_story_drifts(moved, corners)
            else:
                moved = _read(connector, "Diaphragm Center Of Mass Displacements", combos,
                              cases)
                drifts = center_drifts(moved, base_elevation)
                by_story = center_story_drifts(moved, base_elevation)
            # the pattern definitions read only with the patterns selected for display
            model.DatabaseTables.SetLoadPatternsSelectedForDisplay(patterns)
            try:
                pattern_table = connector._read_database_table(SEISMIC_TABLE)
            except Exception:  # no UBC 97 seismic pattern in this model
                pattern_table = pd.DataFrame()
            if pattern_table.empty or "Name" not in pattern_table.columns:
                pattern_table = pd.DataFrame({"Name": pd.Series(dtype=str)})
            periods = {}
            for _, r in pattern_table.iterrows():
                parent = str(r["Name"]).split("(")[0]
                if pattern_types.get(parent) == mc.SEISMIC_DRIFT:
                    period = mc._num(r.get("TUsed"))
                    if period == period:
                        periods[parent] = max(period, periods.get(parent, 0.0))
            report.levels.append((level, level_findings(
                drifts, pattern_table, pattern_types, wind_denominator, drift_cases, r_factor)
                + typed_minimum_findings(pattern_table, pattern_types), periods))
            used_r = r_factor
            if not used_r and "R" in pattern_table.columns:
                values = pd.to_numeric(pattern_table["R"], errors="coerce").dropna()
                used_r = float(values.iloc[0]) if len(values) else None

            def is_wind(combo: str) -> bool:
                if drift_cases and combo in drift_cases:
                    return bool(drift_cases[combo][1])
                return str(combo).upper().startswith("WDRIFT")

            report.stories[level.name] = story_table(by_story, story_order, is_wind, used_r)
    finally:
        say("Restoring the strength modifiers and spectrum scale factors")
        model.SetModelIsLocked(False)
        for name, values in original.items():
            model.FrameObj.SetModifiers(name, values)
        _restore_spectrum(model, spectrum)
        if had_results:
            say("Analysing the restored model")
            run()
            report.restored = True
        else:
            model.File.Save()
    return report


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
@dataclass
class DriftOptions:
    reference: str = CENTER
    seismic: str = BOTH
    wind_denominator: float = NSCP.wind.drift_limit_denominator
    # set by resolve_drift_combinations on a model without the DRIFT / WDRIFT names
    combos: list[str] | None = None
    drift_cases: dict[str, tuple[str, bool]] | None = None
    r_factor: float | None = None
    cases: list[str] | None = None        # load cases to read the drift on, in place of combinations
    service_factor: float | None = None   # service stiffness over strength stiffness (1.4 if None)


def resolve_drift_combinations(connector, model_path: str, title: str,
                               options: DriftOptions) -> DriftOptions | None:
    """Fill ``options`` with the drift combinations to use.

    A model with the DRIFT / WDRIFT combinations of ``sdt setup`` needs
    nothing. On any other model the user picks the combinations from the
    model's own; R comes from the UBC 97 patterns or is asked. None when a
    dialog is closed.
    """
    from etabs_api.workflows import model_inputs as mi

    if _drift_combinations(connector.sap_model, options.seismic):
        return options
    direct = drift_load_cases(connector)
    on_cases = False
    if any(not wind for _, wind in direct.values()):
        from utilities._gui_helpers import select_option

        on_patterns = "On the drift load cases: " + ", ".join(direct)
        chosen = select_option(
            title, "This model has no DRIFT / WDRIFT combinations. What should the drift be "
            "checked on?\n\nThe drift load cases use the seismic forces of the period without "
            "its cap, which is what NSCP 208.6.5.2 allows for drift. Combinations of your own "
            "usually carry the strength forces, with the capped period, and give a larger "
            "drift than the code asks for.",
            [on_patterns, "On combinations I pick"])
        if chosen is None:
            return None
        on_cases = chosen == on_patterns
    if on_cases:
        options.cases = list(direct)
        options.drift_cases = dict(direct)
        options.seismic = STATIC
    else:
        combinations = mi.read_combinations(connector)
        picked = mi.ask_drift_combinations(connector, model_path, title, combinations)
        if picked is None:
            return None
        options.combos = picked
        options.drift_cases = mi.drift_cases(combinations, picked)
        # the spectrum is scaled only when a picked combination has a spectrum case
        spectral = any(combinations.terms[name].spectral for name in picked)
        options.seismic = BOTH if spectral else STATIC
    model_values = mi.read_seismic(connector)
    answer = mi.ask_seismic_values(model_path, title, model_values,
                                   mi.load(model_path).get("seismic", {}), ("r_factor",))
    if answer is None:
        return None
    options.r_factor = answer[0]["r_factor"]
    return options


def has_standard_combinations(connector) -> bool:
    """Whether the model has the DRIFT / WDRIFT combinations of ``sdt setup``."""
    return bool(_drift_combinations(connector.sap_model, BOTH))


def ask_drift_options(title: str, standard: bool = True) -> DriftOptions | None:
    """Where to read the drift, which seismic combinations and the wind limit. None if cancelled.

    ``standard`` is False for a model without the DRIFT / WDRIFT combinations:
    the static / spectrum question is then left out, since the user picks the
    combinations (``resolve_drift_combinations``).
    """
    from utilities._gui_helpers import enter_values, select_option, show_warning

    where = select_option(title, "Where should the drift be read?",
                          ["Diaphragm centre of mass",
                           "Outer four corners (extreme column joints of each story)"])
    if where is None:
        return None
    which = "Both"
    if standard:
        which = select_option(title, "Seismic drift combinations to check (NSCP 208.6.4.1):",
                              ["Static: EQXSD / EQYSD (period not capped)",
                               "Response spectrum: RSAXD / RSAYD (scaled to the drift patterns)",
                               "Both"])
    if which is None:
        return None
    label = "Wind drift limit: h /"
    stiffer = "Service level stiffness, times the strength level (ACI 6.6.3.2.2: 1.4)"
    code_factor = NSCP.analysis.service_stiffness_factor
    while True:
        typed = enter_values(title, "Story drift limit under the wind combinations (NSCP 207 "
                             "sets none; seismic drift follows NSCP 208.6.5), and the stiffness "
                             "of the service level.", [label, stiffer],
                             {label: f"{NSCP.wind.drift_limit_denominator:g}",
                              stiffer: f"{code_factor:g}"})
        if typed is None:
            return None
        try:
            denominator = float(typed[label])
            factor = float(typed.get(stiffer, code_factor))
            if denominator <= 0 or not 1.0 <= factor <= 3.0:
                raise ValueError
            break
        except ValueError:
            show_warning("The wind drift limit must be a positive number and the service "
                         "stiffness factor from 1 to 3.", title=title)
    return DriftOptions(
        CENTER if where.startswith("Diaphragm") else CORNERS,
        BOTH if which == "Both" else (STATIC if which.startswith("Static") else SPECTRUM),
        denominator, service_factor=None if factor == code_factor else factor)


def failures(report: DriftReport) -> list[tuple[str, mc.Finding]]:
    """(level, finding) of every failing drift check."""
    return [(level.name, f) for level, findings, _ in report.levels for f in findings
            if f.status == mc.FAIL]


def save_report(report: DriftReport, path: str) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(report.text().lstrip("\n") + "\n")
    return path


def run_drift_check() -> DriftReport | None:
    """Entry point: ask the options, run the drift levels on the open model, print the
    report and save it beside the model (``<model> - Drift.txt``)."""
    from design.concrete_workflow import attach_etabs
    from utilities._gui_helpers import LoadingWindow, select_option, show_warning

    title = "Story Drift"
    connector = attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    path = os.path.splitext(str(model.GetModelFilename()))[0] + ".EDB"
    if not os.path.isfile(path):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    options = ask_drift_options(title, has_standard_combinations(connector))
    if options is None:
        return None
    options = resolve_drift_combinations(connector, path, title, options)
    if options is None:
        return None
    levels = stiffness_levels(options.service_factor)
    text = "\n".join(f"  {lv.name}: " + ("your modifiers" if lv.as_modelled else
                                         f"beams {lv.beam:.2f}, columns {lv.column:.2f}")
                     for lv in levels)
    go = select_option(
        title, "sdt drift works on this model (no copy). It unlocks it, sets the drift stiffness "
        f"of every beam and column and analyses it for each level:\n\n{text}\n\nThen it puts "
        "your modifiers and response spectrum scale factors back and analyses it again, so it "
        "is left as it was for the strength design. Continue?", ["Continue", "Cancel"])
    if go != "Continue":
        return None
    try:
        with LoadingWindow("Story drift") as window:
            report = run_drift(connector, options.reference, options.wind_denominator, levels,
                               window.update, options.seismic, options.combos,
                               options.drift_cases, options.r_factor, options.cases)
    except RuntimeError as error:
        show_warning(str(error), title=title)
        return None
    print(report.text())
    saved = save_report(report, os.path.splitext(path)[0] + " - Drift.txt")
    print(f"Saved: {saved}")
    drift_summary(report, path, saved).show(popup=True, echo=False)
    return report


def drift_summary(report: DriftReport, model_path: str | None = None, saved: str | None = None):
    """The closing summary of a drift check: the levels run and every failing check."""
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt drift", model_path)
    summary.add("Drift read at", "diaphragm centre of mass" if report.reference == CENTER
                else "outer four corners")
    summary.add("Seismic combinations", f"{len(report.picked)} combinations picked by you"
                if report.picked else report.seismic)
    summary.add("Wind limit", f"h / {report.wind_denominator:g}")
    for level, findings, _ in report.levels:
        failing = sum(1 for f in findings if f.status == mc.FAIL)
        summary.add(level.name, f"{len(findings)} checks, {failing} failing")
    summary.add("Model restored", "yes, and analysed again" if report.restored
                else "yes (it had no results before)")
    for level, finding in failures(report):
        summary.fail(f"{level}: {finding.text}")
    for note in report.notes:
        summary.note(note)
    summary.file("Report", saved)
    return summary


if __name__ == "__main__":
    run_drift_check()
