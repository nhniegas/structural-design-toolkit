"""Analysis and design loop with automatic member resizing (``python main.py design``).

Each iteration:

1. analysis of the working copy, with the response spectrum scaled to 100 %
   of the static base shear (the scale factors start from their original
   values every time, so they follow the current stiffness);
2. extraction of the forces from the analysis, the service loads for
   deflection, the frame data and the connectivity (kept in memory);
3. beam design, with the deflection checks;
4. in the column phase, column design;
5. resizing of the members that fail, and of those that pass comfortably.

The beams and girders are settled first (their loop runs until no beam
changes), then the columns, because the column checks use the beam bars.
A final check of every member on the final analysis follows; if anything
fails there, another round starts. The loop stops when nothing changes or
after the iteration limits.

Rules (agreed with the office):

* Beams: bars that do not fit, girder steel ratio above 2.5 %, SMRF moment
  ratios and deflection grow the depth first; stirrup spacing below the
  minimum (shear, torsion) grows the width first. Members of one beam line
  share the size unless their lengths differ by more than the similarity
  limit.
* Columns: joint shear or beam-column strength failing in one direction grows
  the side along that direction; flexure, axial load, bar limits, shear and
  the SMRF dimension grow to the next square size. A column is never smaller
  than the column above it.
* Downsizing: a member whose ratios are all below the threshold goes one
  size smaller; the next analysis confirms it. A member that grew in this
  run is never made smaller again. Beams never go below the ACI 318-14
  Table 9.3.1.1 minimum depth (L/16, cantilevers L/8).
* Sizes follow the setup ranges, then grow by the increment up to the
  maximum the user gives. Concrete and rebar never change.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etabs_api.workflows.sections import (  # noqa: E402
    Limits,
    Section,
    depth_along_x,
    grow_beam,
    grow_column,
    parse_section,
    shrink_beam,
    shrink_column,
)

DEPTH_FAILURES = ("MAX BARS", "SMRF STEEL RATIO", "SMRF MOMENT", "DEFLECTION")
WIDTH_FAILURES = ("SHEAR SPACING",)
BCC_REQUIRED = 1.2
SMRF_RHO_LIMIT = 0.025
COLUMN_RHO_LIMIT = 0.06


@dataclass
class LoopSettings:
    """Everything the loop needs, asked once at the start."""

    combos: list[str]
    force_options: object = None
    gravity_combo: str | None = None
    smrf: bool = True
    beam_bars: dict = field(default_factory=lambda: {"dm": 28, "ds": 12, "dw": 12,
                                                     "fyw": 414, "cc": 40})
    column_bars: dict = field(default_factory=lambda: {"dmain": 25, "dties": 12,
                                                       "cover": 40})
    continuous_bars: bool = True
    bottom_cover: str = "none"
    long_limit: int = 480
    limits: Limits = field(default_factory=Limits)
    ranges: dict = field(default_factory=dict)
    downsize_ratio: float = 0.7
    span_similarity: float = 0.30
    max_rounds: int = 5
    max_inner: int = 10
    zone_factor: float = 0.4
    ct: float = 0.03


@dataclass
class Change:
    """One section change."""

    member: str
    old: str
    new: str
    reason: str


# =============================================================================
# BEAM DECISIONS (no ETABS)
# =============================================================================
def _line(name: str) -> str:
    from design.beam_deflection import _MARK

    match = _MARK.match(str(name))
    return (match.group(1) + match.group(2) + "-" + match.group(3)).upper() if match else name


def _beam_rho(row: pd.Series, zone: str) -> float:
    """Tension steel ratio of a face row in one zone."""
    bar = float(row.get("dm", 20) or 20)
    count = sum(float(row.get(f"n_{zone}_L{k}", 0) or 0) for k in (1, 2, 3))
    width = float(row["Width"])
    depth = float(row["Depth"]) - float(row.get("cc", 40) or 40) - float(row.get("ds", 10) or 10) \
        - bar / 2
    return count * math.pi * bar**2 / 4 / (width * depth) if depth > 0 else 0.0


def _tension_controlled_rho(fc: float, fy: float) -> float:
    """Steel ratio at a net tensile strain of 0.005 (ACI 21.2.2): 0.85 b1 fc/fy * 3/8."""
    beta1 = 0.85 if fc <= 28 else max(0.65, 0.85 - 0.05 * (fc - 28) / 7)
    return 0.85 * beta1 * fc / fy * 0.375


def beam_comfortable(rows: pd.DataFrame, ratio: float, seismic: bool) -> bool:
    """A passing beam whose steel, shear and deflection all stay below ``ratio`` of the limits."""
    top = rows.iloc[0]
    fc, fy = float(top["f'c"]), float(top["fy"])
    limit = _tension_controlled_rho(fc, fy)
    if seismic and not str(top.get("SupportStatus", "")).startswith("Beam-Framed"):
        limit = min(limit, SMRF_RHO_LIMIT)
    for _, row in rows.iterrows():
        if max(_beam_rho(row, z) for z in ("left", "mid", "right")) > ratio * limit:
            return False
    width = float(top["Width"])
    d = float(top["Depth"]) - float(top.get("cc", 40) or 40) - 30.0
    shear_limit = 0.75 * (0.17 + 0.66) * math.sqrt(fc) * width * d / 1e3  # kN, phi (Vc + Vs,max)
    shear = max(float(top.get(k, 0) or 0) for k in ("Vu_left", "Vu_right", "Vu_mid_2h"))
    if shear > ratio * shear_limit:
        return False
    deflection = top.get("Defl_ratio")
    if deflection is not None and pd.notna(deflection) and float(deflection) > ratio:
        return False
    return True


def beam_actions(results: pd.DataFrame, sections: dict[str, Section], lengths: dict[str, float],
                 grown: set[str], settings: LoopSettings, seismic: bool = True,
                 allow_shrink: bool = True) -> dict[str, tuple[Section, str]]:
    """New sizes for the beams: grow the failing ones, shrink the comfortable ones.

    ``results`` is the beam design table with internal column names (two rows
    per beam). Returns {member: (new section, reason)}.
    """
    wanted: dict[str, tuple[Section, str]] = {}
    shrinkable: dict[str, Section] = {}
    for name, rows in results.groupby(results["UniqueName"].astype(str)):
        section = sections.get(name)
        if section is None or not section.is_beam:
            continue
        statuses = " ".join(rows["Design_Status"].astype(str)).upper()
        failing = statuses.replace("OK", "").strip()
        if any(key in statuses for key in DEPTH_FAILURES):
            new = grow_beam(section, "depth", settings.ranges, settings.limits)
            reason = next(k for k in DEPTH_FAILURES if k in statuses).lower()
        elif any(key in statuses for key in WIDTH_FAILURES):
            new = grow_beam(section, "width", settings.ranges, settings.limits)
            reason = "shear spacing"
        elif failing and "FAIL" in failing:
            new, reason = grow_beam(section, "depth", settings.ranges, settings.limits), "failed"
        else:
            new, reason = None, ""
            if allow_shrink and name not in grown and beam_comfortable(
                    rows, settings.downsize_ratio, seismic):
                span = lengths.get(name, 0.0)
                cantilever = "Cantilever" in str(rows.iloc[0].get("SupportStatus", ""))
                min_depth = span / (8.0 if cantilever else 16.0)
                smaller = shrink_beam(section, settings.ranges, min_depth)
                if smaller is not None:
                    shrinkable[name] = smaller
            continue
        if new is None:
            wanted[name] = (section, f"{reason}: no larger size within the limits")
        else:
            wanted[name] = (new, f"grow ({reason})")

    # members of a beam line share the size, unless their lengths differ too much
    by_line: dict[str, list[str]] = {}
    for name in sections:
        if sections[name].is_beam:
            by_line.setdefault(_line(name), []).append(name)

    def similar(a: str, b: str) -> bool:
        la, lb = lengths.get(a, 0.0), lengths.get(b, 0.0)
        top = max(la, lb)
        return top <= 0 or abs(la - lb) / top <= settings.span_similarity

    out: dict[str, tuple[Section, str]] = {}
    for name, (new, reason) in wanted.items():
        if new == sections[name]:
            out[name] = (new, reason)  # reported, no change
            continue
        for other in by_line.get(_line(name), [name]):
            if other != name and not similar(name, other):
                continue
            current = out.get(other, (sections[other], ""))[0]
            if sections[other].family != new.family:
                continue
            bigger = Section(new.family, max(current.width, new.width),
                             max(current.depth, new.depth), current.concrete, current.rebar)
            if bigger != sections[other]:
                out[other] = (bigger, reason if other == name else f"same beam line as {name}")
    # shrink a group only when all its members can shrink to the same size
    for name, smaller in shrinkable.items():
        if name in out:
            continue
        group = [o for o in by_line.get(_line(name), [name]) if similar(name, o)]
        if all(o in shrinkable and shrinkable[o] == smaller for o in group):
            out[name] = (smaller, "shrink (all ratios below the threshold)")
    return out


# =============================================================================
# COLUMN DECISIONS (no ETABS)
# =============================================================================
def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def column_needs(rows: pd.DataFrame) -> set[str]:
    """What a column's report rows say it needs: "X", "Y" (a side) or "square"."""
    needs: set[str] = set()
    text = lambda column: " ".join(rows.get(column, pd.Series(dtype=str)).astype(str)).upper()
    for axis in ("X", "Y"):
        ratios = [_number(v) for v in rows.get(f"BCC_Ratio_{axis}", [])]
        joints = [_number(v) for v in rows.get(f"Joint_Shear_Utilization_{axis}", [])]
        if any(r is not None and r < BCC_REQUIRED for r in ratios) or \
                any(u is not None and u > 1.0 for u in joints):
            needs.add(axis)
    if "FAIL" in text("Flexure_Check") or "FAIL" in text("Axial_Check") \
            or "FAIL" in text("Shear_Check") or "FAIL" in text("SMRF_Dimension_Check") \
            or "NO PASSING BAR COUNT" in text("Design_Status_Reason"):
        needs.add("square")
    rho = [_number(v) for v in rows.get("Reinforcement_Ratio", [])]
    if any(r is not None and r > COLUMN_RHO_LIMIT for r in rho):
        needs.add("square")
    return needs


def column_comfortable(rows: pd.DataFrame, ratio: float) -> bool:
    for column in ("Flexure_Utilization", "Shear_Utilization", "Joint_Shear_Utilization_X",
                   "Joint_Shear_Utilization_Y"):
        values = [_number(v) for v in rows.get(column, [])]
        if any(v is not None and v > ratio for v in values):
            return False
    rho = [_number(v) for v in rows.get("Reinforcement_Ratio", [])]
    if any(r is not None and r > ratio * COLUMN_RHO_LIMIT for r in rho):
        return False
    for axis in ("X", "Y"):
        values = [_number(v) for v in rows.get(f"BCC_Ratio_{axis}", [])]
        if any(v is not None and v < BCC_REQUIRED / ratio for v in values):
            return False
    status = " ".join(rows.get("Column_Design_Status", pd.Series(dtype=str)).astype(str)).upper()
    return "FAIL" not in status


def column_actions(report: pd.DataFrame, sections: dict[str, Section], angles: dict[str, float],
                   above: dict[str, str], grown: set[str], settings: LoopSettings,
                   allow_shrink: bool = True) -> dict[str, tuple[Section, str]]:
    """New sizes for the columns, then lower columns at least the size of the one above."""
    out: dict[str, tuple[Section, str]] = {}
    for name, rows in report.groupby(report["UniqueName"].astype(str)):
        section = sections.get(name)
        if section is None or section.is_beam:
            continue
        needs = column_needs(rows)
        if needs:
            if "square" in needs or needs == {"X", "Y"}:
                new = grow_column(section, "square", settings.ranges, settings.limits)
                reason = "grow (" + ", ".join(sorted(needs)) + ")"
            else:
                axis = next(iter(needs))
                depth_x = depth_along_x(angles.get(name, 0.0))
                along_depth = None if depth_x is None else (depth_x == (axis == "X"))
                mode = "side" if along_depth is not None else "square"
                new = grow_column(section, mode, settings.ranges, settings.limits, along_depth)
                reason = f"grow (joint / beam-column strength in {axis})"
            out[name] = (new or section,
                         reason if new else reason + ": no larger size within the limits")
        elif allow_shrink and name not in grown and column_comfortable(rows,
                                                                       settings.downsize_ratio):
            smaller = shrink_column(section, settings.ranges)
            if smaller is not None:
                out[name] = (smaller, "shrink (all ratios below the threshold)")

    # A column is never smaller than the one above it. ``above`` maps each
    # column to the column standing on it; walk down from every top column.
    lower_of = {upper: lower for lower, upper in above.items()}
    for start in [c for c in sections if not sections[c].is_beam and c not in above]:
        current = start
        while current in lower_of:
            lower = lower_of[current]
            upper_size = out.get(current, (sections[current], ""))[0]
            lower_size = out.get(lower, (sections[lower], ""))[0]
            if upper_size.circular != lower_size.circular:
                current = lower
                continue
            width = max(upper_size.width, lower_size.width)
            depth = max(upper_size.depth, lower_size.depth)
            if (width, depth) != (lower_size.width, lower_size.depth):
                bigger = Section(lower_size.family, width, depth, lower_size.concrete,
                                 lower_size.rebar, lower_size.circular)
                own = out.get(lower, (None, ""))[1]
                reason = f"not smaller than {current} above"
                out[lower] = (bigger, f"{own}; {reason}" if own else reason)
            current = lower
    return {name: action for name, action in out.items() if action[0] != sections[name]
            or "no larger size" in action[1]}


# =============================================================================
# THE LOOP (ETABS and the design tables)
# =============================================================================
class Workbench:
    """The working copy in ETABS and its design tables, with the steps of one iteration."""

    def __init__(self, connector, settings: LoopSettings, log_path: str, progress=None):
        self.connector, self.settings = connector, settings
        self.tables: dict[str, pd.DataFrame] = {}
        self.model = connector.sap_model
        self.log_path = log_path
        self.progress = progress or (lambda text: None)
        self.existing_sections: set[str] | None = None
        self.original_spectrum = self._spectrum_loads()
        self.stage = ""      # round, iteration and phase, shown in the loading window
        self.last = ""       # results of the last iteration

    def show(self, step: str, detail: str = "") -> None:
        """Update the loading window: where the loop is, the step and its detail."""
        lines = [self.stage, step] + ([detail] if detail else []) + (
            [self.last] if self.last else [])
        self.progress("\n".join(line for line in lines if line))

    # ---- log ----
    def log(self, text: str = "") -> None:
        print(text, flush=True)
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    # ---- model data ----
    def _table(self, name: str) -> pd.DataFrame:
        tables = self.model.DatabaseTables
        tables.SetLoadCasesSelectedForDisplay([])
        tables.SetLoadCombinationsSelectedForDisplay([])
        return self.connector._read_database_table(name)

    def members(self) -> tuple[list[str], list[str]]:
        beams = self._table("Beam Object Connectivity")["UniqueName"].astype(str)
        columns = self._table("Column Object Connectivity")["UniqueName"].astype(str)
        return ([n for n in beams if not n.isnumeric()],
                [n for n in columns if not n.isnumeric()])

    def sections(self) -> dict[str, Section]:
        table = self._table("Frame Assignments - Section Properties")
        out = {}
        for name, prop in zip(table["UniqueName"].astype(str), table["SectProp"].astype(str)):
            section = parse_section(prop)
            if section is not None and not name.isnumeric():
                out[name] = section
        return out

    def lengths(self) -> dict[str, float]:
        table = self._table("Beam Object Connectivity")
        return dict(zip(table["UniqueName"].astype(str), pd.to_numeric(table["Length"])))

    def angles(self, columns: list[str]) -> dict[str, float]:
        return {name: float(self.model.FrameObj.GetLocalAxes(name, 0.0, False)[0])
                for name in columns}

    def above(self) -> dict[str, str]:
        from etabs_api.workflows.analysis_forces import columns_above

        return columns_above(self.connector)

    # ---- response spectrum ----
    def _spectrum_loads(self) -> dict[str, tuple]:
        from etabs_api.workflows.model_analysis import spectrum_cases

        api = self.model.LoadCases.ResponseSpectrum
        return {case: tuple(api.GetLoads(case)[:6]) for case in spectrum_cases(self.connector)}

    def _restore_spectrum(self) -> None:
        from etabs_api.core.helpers import as_list

        api = self.model.LoadCases.ResponseSpectrum
        for case, loads in self.original_spectrum.items():
            count, directions, functions, scales, systems, angles = loads
            api.SetLoads(case, count, as_list(directions), as_list(functions), as_list(scales),
                         as_list(systems), as_list(angles))

    # ---- steps ----
    def analyze(self):
        from etabs_api.workflows.model_analysis import analyze_model

        self.show("Analysis and response spectrum scaling")
        if self.model.GetModelIsLocked():
            self.model.SetModelIsLocked(False)
        self._restore_spectrum()
        report = analyze_model(self.connector, self.settings.zone_factor, self.settings.ct)
        for item in report.scaling:
            self.log(f"  {item.direction}: static {item.static_shear / 1e3:,.0f} kN, "
                     f"{item.spectrum_case} x {item.factor:.3f}")
        if report.governing_period:
            self.log("  periods: " + ", ".join(
                f"{d} {t:.3f} s" for d, t in report.governing_period.items()))
        return report

    def extract(self) -> None:
        from design.concrete_workflow import extract

        self.show("Extraction", "forces, service loads, frame data, connectivity")
        beams, columns = self.members()
        self.tables, notes = extract(self.connector, self.settings.combos,
                                     self.settings.force_options, beams + columns)
        for note in notes:
            self.log("  " + note)

    def design_beams(self) -> pd.DataFrame:
        from design.beam_designer_aci318 import design_beams

        def beam_progress(detail: str) -> None:
            self.show("Beam design", str(detail))

        beam_progress("starting")
        return design_beams(self.tables, self.settings.smrf, self.settings.gravity_combo,
                            self.settings.beam_bars, self.settings.long_limit,
                            progress=beam_progress)

    def design_columns(self, beams: pd.DataFrame) -> pd.DataFrame:
        from design.column_designer_aci318 import design_columns

        def column_progress(detail: str) -> None:
            self.show("Column design", str(detail))

        column_progress("starting")
        bars = self.settings.column_bars
        report, self.column_groups, _ = design_columns(
            self.tables, beams, self.settings.smrf, bars["dmain"], bars["dties"],
            bars["cover"], progress=column_progress,
            continuous_vertical_bars=self.settings.continuous_bars,
            bottom_story_cover=self.settings.bottom_cover)
        return report

    def apply(self, actions: dict[str, tuple[Section, str]], current: dict[str, Section],
              grown: set[str]) -> list[Change]:
        from etabs_api.workflows.sections import assign_section, ensure_section

        if self.existing_sections is None:
            table = self._table("Frame Section Property Definitions - Summary")
            self.existing_sections = set(table["Name"].astype(str))
        self.show("Resizing", f"{len(actions)} members to change")
        if self.model.GetModelIsLocked():
            self.model.SetModelIsLocked(False)
        changes = []
        for name, (section, reason) in sorted(actions.items()):
            old = current[name]
            if section == old:
                self.log(f"  {name}: {old.name} - {reason}")
                continue
            if not ensure_section(self.model, section, self.existing_sections):
                self.log(f"  {name}: could not create {section.name}")
                continue
            if assign_section(self.model, name, section):
                changes.append(Change(name, old.name, section.name, reason))
                if section.area > old.area:
                    grown.add(name)
                self.log(f"  {name}: {old.name} -> {section.name}  ({reason})")
        self.model.File.Save()
        return changes


def run_design_loop(bench: Workbench) -> dict:
    """The loop. Returns a summary: status, iterations, changes and what still fails."""
    settings = bench.settings
    grown: set[str] = set()
    all_changes: list[Change] = []
    iteration = 0
    status = "not converged"
    seismic = settings.smrf
    beams, report = pd.DataFrame(), None

    def step(label: str, columns: bool, allow_shrink: bool):
        nonlocal iteration
        iteration += 1
        bench.stage = f"Round {round_number} - Iteration {iteration}: {label}"
        bench.log("")
        bench.log(f"=== Iteration {iteration}: {label} ===")
        start = time.time()
        bench.analyze()
        bench.extract()
        beam_table = bench.design_beams()
        column_report = bench.design_columns(beam_table) if columns else None
        sections = bench.sections()
        actions = beam_actions(beam_table, sections, bench.lengths(), grown, settings, seismic,
                               allow_shrink)
        if columns:
            _, column_names = bench.members()
            actions.update(column_actions(column_report, sections, bench.angles(column_names),
                                          bench.above(), grown, settings, allow_shrink))
        failing = beam_table.groupby("UniqueName")["Design_Status"].apply(
            lambda s: any(v != "OK" for v in s.astype(str))).sum()
        summary = f"beams failing {failing} of {beam_table['UniqueName'].nunique()}"
        bench.log(f"  beams failing: {failing} of {beam_table['UniqueName'].nunique()}")
        if column_report is not None:
            failing = column_report.groupby("UniqueName")["Column_Design_Status"].apply(
                lambda s: (s.astype(str) == "FAIL").any()).sum()
            bench.log(f"  columns failing: {failing} of {column_report['UniqueName'].nunique()}")
            summary += f", columns failing {failing} of {column_report['UniqueName'].nunique()}"
        changes = bench.apply(actions, sections, grown)
        all_changes.extend(changes)
        bench.log(f"  {len(changes)} section changes, {time.time() - start:.0f} s")
        bench.last = f"Last iteration: {summary}; {len(changes)} section changes"
        return changes, beam_table, column_report

    round_number = 0
    for round_number in range(1, settings.max_rounds + 1):
        bench.log("")
        bench.log(f"##### Round {round_number} #####")
        for _ in range(settings.max_inner):  # beams and girders
            changes, _, _ = step("beams", False, allow_shrink=True)
            if not any(parse_section(c.new).is_beam for c in changes):
                break
        for _ in range(settings.max_inner):  # columns
            changes, _, _ = step("columns", True, allow_shrink=True)
            if not changes:
                break
        changes, beams, report = step("final check of every member", True, allow_shrink=False)
        if not changes:
            status = "converged"
            break
    still_failing = sorted(set(beams.loc[beams["Design_Status"].astype(str) != "OK",
                                         "UniqueName"].astype(str)))
    if report is not None:
        still_failing += sorted(set(report.loc[report["Column_Design_Status"].astype(str)
                                               == "FAIL", "UniqueName"].astype(str)))
    bench.log("")
    bench.log(f"Status: {status} after {iteration} iterations; "
              f"{len(all_changes)} section changes.")
    if still_failing:
        bench.log("Still failing: " + ", ".join(still_failing))
    return {"status": status, "iterations": iteration, "changes": all_changes,
            "failing": still_failing, "beams": beams, "columns": report}


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
LOOP_FIELDS = {
    "Size increment past the setup ranges (mm)": ("increment", 50),
    "Largest beam width (mm)": ("beam_max_width", 800),
    "Largest beam depth (mm)": ("beam_max_depth", 1200),
    "Largest column side (mm)": ("column_max", 1200),
    "Make smaller when every ratio is below": ("downsize_ratio", 0.7),
    "Beam line shares one size when lengths differ by at most (%)": ("span_similarity", 30),
    "Rounds (beams, columns, final check) at most": ("max_rounds", 5),
}
def _saved(name: str) -> dict:
    import json

    path = os.path.join(os.path.expanduser("~"), ".xlwings_structural", f"{name}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save(name: str, values: dict) -> None:
    import json

    path = os.path.join(os.path.expanduser("~"), ".xlwings_structural", f"{name}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(values, handle, indent=2)


def uls_combinations(names: list[str], seismic: str) -> list[str]:
    """The ULS combinations to design for: gravity and wind always, plus the
    static (EQ), response spectrum (RSA) or both seismic ones."""
    import re

    out = []
    for name in names:
        if not str(name).startswith("ULS"):
            continue
        is_eq = re.search(r"\bEQ\d", name) is not None
        is_rsa = re.search(r"\bRSA\d", name) is not None
        if (not is_eq and not is_rsa) or (is_eq and seismic in ("EQ", "Both")) \
                or (is_rsa and seismic in ("RSA", "Both")):
            out.append(name)
    return out


def _ask_ranges(families: set[str], settings: dict, title: str) -> bool:
    """Ask a size range for each family without one; False when cancelled."""
    from etabs_api.workflows.sections import BEAM_FAMILIES
    from utilities._gui_helpers import enter_values, show_warning

    for family in sorted(families):
        if family in BEAM_FAMILIES:
            labels = {f"{family} widths: from, to, step (mm)": "width",
                      f"{family} depths: from, to, step (mm)": "depth"}
        else:
            labels = {f"{family} sides: from, to, step (mm)": "size"}
        while True:
            typed = enter_values(title, f"{family} sections have no size range in the setup "
                                 "inputs. Sizes to iterate on:", list(labels),
                                 {label: "" for label in labels})
            if typed is None:
                return False
            try:
                spans = {}
                for label, key in labels.items():
                    values = [float(v) for v in typed[label].replace(";", ",").split(",")]
                    if len(values) != 3 or values[2] <= 0 or values[1] < values[0]:
                        raise ValueError
                    spans[key] = values
                break
            except ValueError:
                show_warning("Type three numbers: from, to, step.", title=title)
        settings["sections"].setdefault(family, {}).update(spans)
    return True


def run_design_cli() -> dict | None:
    """Entry point: ask everything once, then run the loop on a working copy."""
    import comtypes.client

    from design.beam_designer_aci318 import ask_deflection_limit
    from design.column_designer_aci318 import ask_column_design_options
    from etabs_api.core.connection import ETABSConnector
    from etabs_api.workflows.analysis_forces import ask_force_options
    from etabs_api.workflows.model_setup import load_settings, merge_settings, save_settings
    from etabs_api.workflows.model_setup import settings_path
    from etabs_api.workflows.sections import has_range
    from utilities._gui_helpers import LoadingWindow, enter_values, select_option, show_warning

    title = "Design Loop"
    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
    try:
        etabs = helper.GetObject("CSI.ETABS.API.ETABSObject")
    except Exception:
        show_warning("ETABS is not running. Open the model in ETABS first.", title=title)
        return None
    connector = ETABSConnector()
    connector.etabs_object, connector.sap_model, connector.is_connected = (
        etabs, etabs.SapModel, True)
    model = connector.sap_model
    original = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    if not os.path.isfile(original):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None

    # combinations, forces and the design inputs (as in xs beams and xs columns)
    from design.concrete_workflow import (
        BEAM_FIELDS,
        COLUMN_FIELDS,
        _ask_numbers,
        _gravity_options,
        _last,
        _remember,
    )
    from utilities._gui_helpers import select_output_directory

    last = _last()
    seismic = select_option(title, "Seismic combinations to design for "
                            "(gravity and wind ULS combinations are always included):",
                            ["Static (EQ)", "Response spectrum (RSA)", "Both"])
    if seismic is None:
        return None
    combos_table = connector.get_data("Load Combination Definitions")
    names = list(dict.fromkeys(combos_table["Name"].dropna().astype(str)))
    combos = uls_combinations(names, {"Static (EQ)": "EQ", "Response spectrum (RSA)": "RSA",
                                      "Both": "Both"}[seismic])
    if not combos:
        show_warning("The model has no ULS combinations.", title=title)
        return None
    force_options = ask_force_options()
    if force_options is None:
        return None
    smrf = select_option(title, "SMRF (seismic) design? Gravity beams are designed for "
                         "gravity in any case.", ["Yes", "No"],
                         default_index=0 if last.get("smrf", True) else 1)
    if smrf is None:
        return None
    smrf = smrf == "Yes"
    gravity = None
    if smrf:
        choices = _gravity_options(combos)
        gravity = select_option(title, "Gravity combination for the beam seismic shear:",
                                choices, default_index=choices.index(last["gravity_combo"])
                                if last.get("gravity_combo") in choices else 0)
        if gravity is None:
            return None
    beam_bars = _ask_numbers(title, "Beam bars and cover.", BEAM_FIELDS, last)
    if beam_bars is None:
        return None
    column_bars = _ask_numbers(title, "Column bars and cover.", COLUMN_FIELDS, last)
    if column_bars is None:
        return None
    column_answers = ask_column_design_options(normal_cover=column_bars["cover"])
    if column_answers is None:
        return None
    long_limit = ask_deflection_limit()
    if long_limit is None:
        return None
    folder = select_output_directory("Folder for the final results, calculations and schedules")
    if not folder:
        return None
    _remember({"smrf": smrf, "gravity_combo": gravity, **beam_bars, **column_bars})

    # loop limits
    last = _saved("design_loop")
    defaults = {label: f"{last.get(key, value):g}" for label, (key, value) in LOOP_FIELDS.items()}
    typed = enter_values(title, "How the members are resized.", list(LOOP_FIELDS), defaults)
    if typed is None:
        return None
    try:
        values = {key: float(typed[label]) for label, (key, _) in LOOP_FIELDS.items()}
    except ValueError:
        show_warning("Every loop setting must be a number.", title=title)
        return None
    _save("design_loop", values)

    # setup inputs: ranges and seismic values
    settings_file = settings_path(original)
    setup = merge_settings(load_settings(settings_file))
    families = set()
    for name, prop in zip(*[connector.get_data("Frame Assignments - Section Properties")[c]
                            .astype(str) for c in ("UniqueName", "SectProp")]):
        section = parse_section(prop)
        if section is not None and not name.isnumeric():
            families.add("C" if section.circular else section.family)
    missing = {f for f in families if not has_range(setup["sections"], f)}
    if missing:
        if not _ask_ranges(missing, setup, title):
            return None
        save_settings(setup, settings_file)

    settings = LoopSettings(
        combos=combos, force_options=force_options, gravity_combo=gravity, smrf=smrf,
        beam_bars=beam_bars, column_bars=column_bars,
        continuous_bars=column_answers[0], bottom_cover=column_answers[1],
        long_limit=long_limit,
        limits=Limits(int(values["increment"]), int(values["beam_max_width"]),
                      int(values["beam_max_depth"]), int(values["column_max"])),
        ranges=setup["sections"], downsize_ratio=values["downsize_ratio"],
        span_similarity=values["span_similarity"] / 100.0,
        max_rounds=int(values["max_rounds"]),
        zone_factor=setup["seismic"]["zone_factor"], ct=setup["seismic"]["ct"],
    )

    # the working copy: the original model is not changed
    stem = os.path.splitext(original)[0]
    working = f"{stem} - DESIGN.EDB"
    model.File.Save(working)
    log_path = f"{stem} - DESIGN log.txt"
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(f"Design loop of {original}\nWorking copy: {working}\n"
                     f"Started {time.strftime('%Y-%m-%d %H:%M')}\n")
    with LoadingWindow("Design loop: analysis, design and resizing") as window:
        bench = Workbench(connector, settings, log_path, window.update)
        bench.log(f"Combinations: {len(combos)} ULS ({seismic})")
        summary = run_design_loop(bench)
        window.update("Saving the final results, calculations and schedules")
        model.File.Save(working)
        save_final_design(bench, summary, working, folder)
    print(f"Working copy: {working}\nLog: {log_path}\nOutputs: {folder}")
    return summary


def save_final_design(bench: Workbench, summary: dict, working: str, folder: str) -> None:
    """Store the final design for xs columns and write the result files."""
    from design.beam_designer_aci318 import (
        export_beam_dxf,
        export_beam_pdf,
        write_beam_results_xlsx,
    )
    from design.column_designer_aci318 import (
        export_column_cad_drawings,
        export_column_pdf,
        write_column_results_xlsx,
    )
    from design.concrete_workflow import DesignStore

    settings = bench.settings
    stem = os.path.splitext(os.path.basename(working))[0]
    beams, columns = summary.get("beams"), summary.get("columns")
    store = DesignStore(working, os.path.getmtime(working), bench.tables, {
        "combos": settings.combos, "smrf": settings.smrf, "gravity_combo": settings.gravity_combo,
        "beam_bars": settings.beam_bars, "column_bars": settings.column_bars,
        "long_limit": settings.long_limit,
    })
    store.beam_results, store.column_report = beams, columns
    store.column_groups = getattr(bench, "column_groups", None)
    store.save()
    if beams is not None and len(beams):
        write_beam_results_xlsx(beams, os.path.join(folder, f"{stem} - Beam Design.xlsx"))
        export_beam_dxf(beams, folder)
        export_beam_pdf(beams, os.path.join(folder, f"{stem} - Beam Calculations.pdf"),
                        settings.smrf, settings.gravity_combo)
    if columns is not None and len(columns) and store.column_groups:
        bars = settings.column_bars
        write_column_results_xlsx(columns, store.column_groups,
                                  os.path.join(folder, f"{stem} - Column Design.xlsx"))
        export_column_cad_drawings(columns, folder, bars["dmain"], bars["cover"], settings.smrf,
                                   "crossties", bench.tables["CONNECTIVITY"])
        export_column_pdf(columns, os.path.join(folder, f"{stem} - Column Calculations.pdf"),
                          settings.smrf, bars["dmain"], bars["dties"], bars["cover"])


if __name__ == "__main__":
    run_design_cli()
