"""Run the ETABS analysis and check its basic results (``python main.py analyze``).

1. Run every load case.
2. Scale the response spectrum cases so their base shear is 100 % of the
   static base shear in the same direction (only up, never down), and run
   again. The analysis is linear, so one scaling is exact; it is repeated
   until the two agree within 1 %. The strength cases (RSAX, RSAY) follow the
   seismic patterns; the drift cases (RSAXD, RSAYD) follow the drift patterns
   (EQXSD, EQYSD), whose period is not capped (NSCP 208.6.5.2).
3. Report:

   * periods: the governing modal period in each direction against UBC 97
     Method A, T_A = Ct hn^(3/4) (Ct in ft units, hn in ft, measured from the
     ground level: elevation 0 when the base is below it), and its cap of
     1.3 T_A in zone 4 or 1.4 T_A in zones 1 to 3;
   * modal participating mass: a warning when the sum is below 90 % in X or Y;
   * weight: the seismic weight from the mass source against the base reaction
     of the same loads (they must agree within 1 %), and the weight per story.

Drift, irregularity and torsion checks are not done yet.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    # Run as a script: make the project folder (two levels up) importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import NSCP  # noqa: E402
from etabs_api.core.helpers import as_list, return_code  # noqa: E402

G = NSCP.seismic.gravity  # mm/s2
FT = 304.8   # mm
SCALE_TOLERANCE = 0.01
SCALE_TARGET = NSCP.seismic.scaling_irregular  # 100 % of the static base shear
MAX_SCALING_RUNS = 3
SEISMIC_PATTERN_TYPE = 5
SEISMIC_DRIFT_PATTERN_TYPE = 61
DRIFT_SUFFIX = "D"  # RSAXD, RSAYD: the drift spectrum cases (model_setup.DRIFT_SUFFIX)


def method_a_period(ct: float, height_mm: float) -> float:
    """NSCP Eq. 208-12 (UBC 97 Eq. 30-8): T_A = Ct hn^(3/4), Ct in ft units."""
    return ct * (height_mm / FT) ** NSCP.seismic.period_exponent


def period_cap(zone_factor: float) -> float:
    """NSCP 208.5.2.2: Method B may not exceed 1.3 T_A in zone 4, 1.4 T_A otherwise."""
    k = NSCP.seismic
    return k.period_cap_zone4 if zone_factor >= k.zone4_factor else k.period_cap_other


@dataclass
class ShearScaling:
    """Base shear of one direction before and after scaling the spectrum case."""

    direction: str
    static_case: str
    static_shear: float          # N
    spectrum_case: str
    spectrum_before: float       # N
    factor: float                # applied to the spectrum scale factor (1 = unchanged)
    spectrum_after: float        # N


@dataclass
class AnalysisReport:
    """What ``analyze_model`` found."""

    model_path: str = ""
    scaling: list[ShearScaling] = field(default_factory=list)
    periods: pd.DataFrame = field(default_factory=pd.DataFrame)
    governing_period: dict[str, float] = field(default_factory=dict)
    method_a: float | None = None
    cap: float | None = None
    height: float | None = None                # mm, hn above the ground level
    ground_elevation: float = 0.0              # mm
    mass_sum: dict[str, float] = field(default_factory=dict)
    seismic_weight: float | None = None        # N, from the mass source
    reaction_weight: float | None = None       # N, base reaction of the mass-source loads
    story_weights: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # checks that do not apply to this model

    def text(self) -> str:
        """The report as terminal text."""
        lines = ["", "=" * 72, "ETABS ANALYSIS", "=" * 72]
        if self.model_path:
            lines.append(f"Model: {self.model_path}")
        lines += ["", f"--- Response spectrum scaled to {SCALE_TARGET * 100:g} % of the static "
                  "base shear (drift cases: of the drift patterns) ---"]
        for s in self.scaling:
            lines.append(
                f"{s.direction}: static {s.static_case} {s.static_shear / 1e3:,.1f} kN | "
                f"{s.spectrum_case} {s.spectrum_before / 1e3:,.1f} kN -> "
                f"{s.spectrum_after / 1e3:,.1f} kN (scale factor x {s.factor:.4f})"
            )
        if not self.scaling:
            lines.append("No static and spectrum cases were found to compare.")
        lines += ["", "--- Periods ---"]
        for direction, period in self.governing_period.items():
            lines.append(f"Governing modal period {direction}: {period:.3f} s")
        if self.height is not None:
            lines.append(f"Height above the ground hn = {self.height / 1e3:.2f} m "
                         f"(ground level at elevation {self.ground_elevation / 1e3:g} m)")
        if self.method_a is not None:
            lines.append(f"Method A, T_A = Ct hn^(3/4): {self.method_a:.3f} s; "
                         f"Method B cap {self.cap:.1f} T_A = {self.cap * self.method_a:.3f} s")
        if not self.periods.empty:
            lines.append(self.periods.head(12).to_string(index=False))
        lines += ["", "--- Modal participating mass ---"]
        for direction, total in self.mass_sum.items():
            lines.append(f"Sum {direction}: {total * 100:.1f} %")
        lines += ["", "--- Weight ---"]
        if self.seismic_weight is not None:
            lines.append(f"Seismic weight from the mass source: {self.seismic_weight / 1e3:,.1f} kN")
        if self.reaction_weight is not None:
            lines.append(f"Base reaction of the same loads:     {self.reaction_weight / 1e3:,.1f} kN")
        if not self.story_weights.empty:
            lines.append(self.story_weights.to_string(index=False))
        lines += ["", "--- Warnings ---"] + (self.warnings or ["None."])
        if self.notes:
            lines += ["", "--- Not applicable to this model ---"] + self.notes
        lines.append("=" * 72)
        return "\n".join(lines)


# =============================================================================
# READING
# =============================================================================
def _read(connector, name: str, cases: list[str] | None = None) -> pd.DataFrame:
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases or []))
    tables.SetLoadCombinationsSelectedForDisplay([])
    data = connector._read_database_table(name)
    for column in data.columns:
        if column not in ("OutputCase", "CaseType", "StepType", "Case", "Story", "Name", "Type"):
            converted = pd.to_numeric(data[column], errors="coerce")
            if converted.notna().any():
                data[column] = converted
    return data


def _case_names(connector) -> dict[str, str]:
    table = _read(connector, "Load Case Definitions - Summary")
    return dict(zip(table["Name"].astype(str), table["Type"].astype(str)))


def seismic_static_cases(connector, pattern_type: int = SEISMIC_PATTERN_TYPE) -> list[str]:
    """Load cases of the seismic load patterns (type Quake, or ``pattern_type``)
    with a linear static case."""
    patterns = connector.sap_model.LoadPatterns
    names = [str(n) for n in as_list(patterns.GetNameList(0, [])[1])]
    seismic = [n for n in names if int(patterns.GetLoadType(n)[0]) == pattern_type]
    cases = _case_names(connector)
    return [n for n in seismic if cases.get(n) == "Linear Static"]


def base_shears(connector, cases: list[str]) -> pd.DataFrame:
    """Base reaction FX, FY, FZ of each case (largest magnitude of its steps)."""
    table = _read(connector, "Base Reactions", cases)
    table = table[table["OutputCase"].astype(str).isin(cases)]
    return table.groupby("OutputCase")[["FX", "FY", "FZ"]].agg(
        lambda s: s.loc[s.abs().idxmax()] if len(s) else 0.0)


# =============================================================================
# SCALING
# =============================================================================
def spectrum_cases(connector) -> dict[str, str]:
    """Each response spectrum case and its direction ("X" for U1, "Y" for U2)."""
    out = {}
    api = connector.sap_model.LoadCases.ResponseSpectrum
    for name, kind in _case_names(connector).items():
        if kind != "Response Spectrum":
            continue
        loads = api.GetLoads(name)
        directions = [str(d) for d in as_list(loads[1])]
        if "U1" in directions:
            out[name] = "X"
        elif "U2" in directions:
            out[name] = "Y"
    return out


def multiply_spectrum_scale(connector, case: str, factor: float) -> None:
    """Multiply every scale factor of a response spectrum case."""
    api = connector.sap_model.LoadCases.ResponseSpectrum
    count, directions, functions, scales, systems, angles, status = api.GetLoads(case)
    if status != 0:
        raise RuntimeError(f"Could not read the loads of {case}.")
    scales = [float(s) * factor for s in as_list(scales)]
    result = api.SetLoads(case, count, as_list(directions), as_list(functions), scales,
                          as_list(systems), as_list(angles))
    if (result[-1] if isinstance(result, (list, tuple)) else result) != 0:
        raise RuntimeError(f"Could not set the scale factor of {case}.")


def scale_spectrum_to_static(connector, run, progress=None) -> list[ShearScaling]:
    """Scale each spectrum case up to the static base shear of its direction.

    ``run`` runs the analysis. Returns the scaling of each direction.
    """
    statics = seismic_static_cases(connector)
    drifts = seismic_static_cases(connector, SEISMIC_DRIFT_PATTERN_TYPE)
    spectra = spectrum_cases(connector)
    if not (statics or drifts) or not spectra:
        return []
    first = base_shears(connector, statics + drifts + list(spectra))
    out = {}
    for case, direction in spectra.items():
        component = "FX" if direction == "X" else "FY"
        drift = case.upper().endswith(DRIFT_SUFFIX) and bool(drifts)
        own = [c for c in (drifts if drift else statics) if c in first.index
               and abs(first.loc[c, component]) >= abs(first.loc[c, "FY" if component == "FX" else "FX"])]
        if not own:
            continue
        static_case = max(own, key=lambda c: abs(first.loc[c, component]))
        out[case] = ShearScaling(direction, static_case, abs(first.loc[static_case, component]),
                                 case, abs(first.loc[case, component]), 1.0,
                                 abs(first.loc[case, component]))
    say = progress or (lambda text: None)
    for attempt in range(1, MAX_SCALING_RUNS + 1):
        changed = False
        for item in out.values():
            if item.spectrum_after <= 0:
                continue
            ratio = SCALE_TARGET * item.static_shear / item.spectrum_after
            if ratio > 1.0 + SCALE_TOLERANCE / 10:  # scale up only
                multiply_spectrum_scale(connector, item.spectrum_case, ratio)
                item.factor *= ratio
                changed = True
        if not changed:
            break
        say("Running the analysis again with the scaled spectrum\n" + ", ".join(
            f"{i.spectrum_case} x {i.factor:.3f}" for i in out.values())
            + (f"\n(pass {attempt})" if attempt > 1 else ""))
        run()
        again = base_shears(connector, list(out))
        for case, item in out.items():
            component = "FX" if item.direction == "X" else "FY"
            item.spectrum_after = abs(again.loc[case, component])
        if all(abs(i.spectrum_after / (SCALE_TARGET * i.static_shear) - 1) <= SCALE_TOLERANCE
               or i.spectrum_after > SCALE_TARGET * i.static_shear for i in out.values()):
            break
    return sorted(out.values(), key=lambda s: s.direction)


# =============================================================================
# CHECKS
# =============================================================================
def modal_periods(connector) -> pd.DataFrame:
    table = _read(connector, "Modal Participating Mass Ratios", ["Modal"])
    keep = [c for c in ("Mode", "Period", "UX", "UY", "RZ", "SumUX", "SumUY", "SumRZ")
            if c in table.columns]
    return table[keep].reset_index(drop=True)


def ground_elevation(elevations: list[float]) -> float:
    """Elevation hn is measured from: 0 when the base is below it (a footing
    level or a basement), otherwise the base. ``elevations`` start at the base."""
    base = float(elevations[0])
    return 0.0 if base < -0.5 and float(elevations[-1]) > 0.5 else base


def height_above_ground(elevations: list[float]) -> float:
    """hn: from the ground level to the top level."""
    return float(elevations[-1]) - ground_elevation(elevations)


def story_elevations(connector) -> list[float]:
    """Elevation of the base and of every story above it, mm."""
    with connector.extraction_units():
        stories = connector.sap_model.Story.GetStories()
    return [float(z) for z in as_list(stories[2])]


def building_height(connector) -> float:
    """hn, mm: the height of the top level above the ground level."""
    return height_above_ground(story_elevations(connector))


def story_weights(connector) -> pd.DataFrame:
    mass = _read(connector, "Mass Summary by Story")
    out = mass[["Story", "UX"]].copy()
    out["Weight (kN)"] = (out.pop("UX") * G / 1e3).round(1)  # N-s2/mm * mm/s2 = N
    return out


def mass_source_loads(connector) -> list[tuple[str, float]]:
    """(load pattern, multiplier) of the default mass source, from its table."""
    table = _read(connector, "Mass Source Definition")
    if table.empty or "LoadPattern" not in table.columns:
        return []
    if "IsDefault" in table.columns:
        table = table.copy()
        table["IsDefault"] = table["IsDefault"].ffill()
        table = table[table["IsDefault"].astype(str).eq("Yes")]
    rows = table.dropna(subset=["LoadPattern"])
    return [(str(p), float(m)) for p, m in zip(rows["LoadPattern"], rows["Multiplier"])
            if str(p) not in ("", "None")]


def reaction_weight(connector, loads: list[tuple[str, float]]) -> float | None:
    """Vertical base reaction of the mass-source loads with their multipliers, N."""
    cases = _case_names(connector)
    loads = [(p, m) for p, m in loads if p in cases]
    if not loads:
        return None
    reactions = base_shears(connector, [p for p, _ in loads])
    return float(sum(m * abs(reactions.loc[p, "FZ"]) for p, m in loads if p in reactions.index))


def base_spectrum_scale(connector) -> float | None:
    """g I / R from the model's UBC 97 seismic patterns: the unscaled spectrum factor."""
    from etabs_api.workflows.ubc97 import response_spectrum_scale

    model = connector.sap_model
    try:
        names = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
        model.DatabaseTables.SetLoadPatternsSelectedForDisplay(names)
        table = connector._read_database_table("Load Pattern Definitions - Auto Seismic - UBC 97")
        if "IsAuto" in table:
            table = table[table["IsAuto"].astype(str) != "Yes"]
        importance = float(pd.to_numeric(table["I"], errors="coerce").dropna().iloc[0])
        r_factor = float(pd.to_numeric(table["R"], errors="coerce").dropna().iloc[0])
        return response_spectrum_scale(importance, r_factor)
    except Exception:
        return None


def reset_spectrum_scale(connector) -> float | None:
    """Put every spectrum case back to g I / R, so the scaling starts from the
    unscaled spectrum and not from an earlier run's factors. Returns the factor."""
    base = base_spectrum_scale(connector)
    if base is None:
        return None
    model = connector.sap_model
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    api = model.LoadCases.ResponseSpectrum
    for case in spectrum_cases(connector):
        count, directions, functions, scales, systems, angles, _ = api.GetLoads(case)
        api.SetLoads(case, count, as_list(directions), as_list(functions),
                     [base] * len(as_list(scales)), as_list(systems), as_list(angles))
    return base


def reopen_model(connector) -> bool:
    """Save the model and open it again from its EDB. True when that worked."""
    model = connector.sap_model
    path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    if not os.path.isfile(path):
        return False
    return return_code(model.File.Save(path)) == 0 and return_code(model.File.OpenFile(path)) == 0


def analyze_model(connector, zone_factor: float | None = None, ct: float | None = None,
                  scale: bool = True, progress=None) -> AnalysisReport:
    """Reset the spectrum to g I / R, run, scale it to the static base shear, run
    again, and check periods, mass and weight. ``progress`` gets each step."""
    say = progress or (lambda text: None)
    report = AnalysisReport(model_path=str(connector.sap_model.GetModelFilename()))
    if scale:
        say("Putting the response spectrum cases back to g I / R")
        if reset_spectrum_scale(connector) is None and spectrum_cases(connector):
            report.notes.append(
                "Reset of the spectrum scale to g I / R: not applicable (the model has no UBC 97 "
                "seismic pattern to read I and R from). The spectrum cases were scaled up from "
                "the factors they had.")

    def run():
        try:
            connector.analysis.run()
        except RuntimeError:
            # ETABS returns 1, with no log, for a model it holds from a text
            # file: open it from its EDB and try once more.
            if not reopen_model(connector):
                raise
            connector.analysis.run()

    say("Running every load case")
    run()
    if scale:
        say("Comparing the spectrum and static base shears")
        report.scaling = scale_spectrum_to_static(connector, run, say)
        if not report.scaling:
            report.notes.append(
                "Scaling of the response spectrum to the static base shear: not applicable (it "
                "needs a static seismic case and a response spectrum case in each direction).")
    for item in report.scaling:
        if item.spectrum_after < SCALE_TARGET * item.static_shear * (1 - SCALE_TOLERANCE):
            report.warnings.append(f"{item.spectrum_case} base shear is below the static base "
                                   "shear after scaling.")

    say("Reading the modal periods and participating mass")
    periods = modal_periods(connector)
    report.periods = periods
    if not periods.empty:
        for direction, column in (("X", "UX"), ("Y", "UY")):
            if column in periods.columns:
                row = periods.loc[periods[column].idxmax()]
                report.governing_period[direction] = float(row["Period"])
        for direction, column in (("X", "SumUX"), ("Y", "SumUY")):
            if column in periods.columns:
                total = float(periods[column].iloc[-1])
                report.mass_sum[direction] = total
                if total < NSCP.seismic.modal_mass:
                    report.warnings.append(
                        f"Modal participating mass in {direction} is {total * 100:.1f} % "
                        f"(below {NSCP.seismic.modal_mass * 100:g} %): add modes to the modal "
                        "case.")
    if ct and zone_factor:
        elevations = story_elevations(connector)
        report.ground_elevation = ground_elevation(elevations)
        report.height = height_above_ground(elevations)
        report.method_a = method_a_period(ct, report.height)
        report.cap = period_cap(zone_factor)
        limit = report.cap * report.method_a
        for direction, period in report.governing_period.items():
            if period > limit:
                report.warnings.append(
                    f"Modal period {direction} {period:.3f} s is above {report.cap:.1f} T_A = "
                    f"{limit:.3f} s: the static base shear uses the capped period.")

    say("Reading the seismic weight and the base reactions")
    weights = story_weights(connector)
    report.story_weights = weights
    report.seismic_weight = float(weights["Weight (kN)"].sum() * 1e3)
    report.reaction_weight = reaction_weight(connector, mass_source_loads(connector))
    if report.reaction_weight:
        difference = report.seismic_weight / report.reaction_weight - 1
        if abs(difference) > 0.01:
            report.warnings.append(
                f"The seismic weight differs from the base reaction of the mass-source loads "
                f"by {difference * 100:+.1f} %: check the mass source (element self mass, "
                "added mass, or loads on the base).")
    return report


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
def run_model_analysis() -> AnalysisReport | None:
    """Entry point: pick the model, run, scale the spectrum, and print the checks."""
    from comtypes import COMError

    from etabs_api.core.connection import attach_running_etabs
    from etabs_api.workflows.model_setup import load_settings, settings_path
    from utilities._gui_helpers import LoadingWindow, enter_values, select_option, show_warning

    title = "Analysis"
    connector = attach_running_etabs(title)
    if connector is None:
        return None
    model = connector.sap_model
    path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    if not os.path.isfile(path):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    where = select_option(
        title, "The response spectrum cases are scaled in the model. Which model?",
        ["This model", "A copy saved beside it"])
    if where is None:
        return None
    if where.startswith("A copy"):
        stem, extension = os.path.splitext(path)
        path, counter = f"{stem} - ANALYSIS{extension}", 2
        while os.path.exists(path):
            path, counter = f"{stem} - ANALYSIS ({counter}){extension}", counter + 1
        model.File.Save(path)

    from etabs_api.workflows import model_inputs as mi

    saved = load_settings(settings_path(path)) or load_settings(
        settings_path(str(model.GetModelFilename()))) or {}
    # The values of the model's own UBC 97 patterns come first; what was saved
    # with the model (setup inputs, or an earlier answer) fills what they lack.
    in_model = mi.read_seismic(connector)
    seismic = {**saved.get("seismic", {}), **(saved.get(mi.MODEL_KEY, {}).get("seismic", {}))}
    differ = mi.seismic_differences(in_model, seismic)
    seismic = {**seismic, **in_model}
    prompt = "For the Method A period check (UBC 97)."
    if not in_model:
        prompt += (" This model has no UBC 97 seismic pattern, so Z and Ct could not be read "
                   "from it, and the spectrum is scaled only where static seismic cases exist.")
    elif any(key in differ for key in ("zone_factor", "ct")):
        prompt += " The model's values are shown; they differ from what was saved: " + ", ".join(
            f"{mi.SEISMIC_FIELDS[key]} was {old:g}" for key, (_, old) in differ.items()
            if key in ("zone_factor", "ct")) + "."
    labels = {"Seismic zone factor Z": seismic.get("zone_factor", 0.4),
              "Ct (ft units, 0.030 for concrete frames)": seismic.get("ct", 0.03)}
    typed = enter_values(title, prompt, list(labels),
                         {k: f"{v:g}" for k, v in labels.items()})
    if typed is None:
        return None
    try:
        zone, ct = (float(typed[k]) for k in labels)
    except ValueError:
        show_warning("The zone factor and Ct must be numbers.", title=title)
        return None
    if not in_model:  # nowhere else to read them next time
        mi.save(path, seismic={**saved.get(mi.MODEL_KEY, {}).get("seismic", {}),
                               "zone_factor": zone, "ct": ct})

    try:
        with LoadingWindow("Analysis") as window:
            report = analyze_model(connector, zone, ct, progress=window.update)
    except (RuntimeError, COMError) as error:  # COMError: ETABS stopped answering
        show_warning(
            f"ETABS could not finish the analysis:\n\n{error}\n\n"
            "Run the analysis once in ETABS (Analyze > Run Analysis) to see its own message. "
            "A model with no supports, or with members that are not connected, cannot be "
            "analysed. If ETABS closed, open the model again.", title=title)
        return None
    # Save() keeps the results; Save(path) drops them even for the same file
    model.File.Save()
    print(report.text())
    print(f"Saved: {path}")
    analysis_summary(report, path).show(popup=True, echo=False)
    return report


def analysis_summary(report: AnalysisReport, model_path: str | None = None):
    """The closing summary of an analysis run: scaling, periods, mass, weight, warnings."""
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt analyze", model_path)
    for item in report.scaling:
        summary.add(f"{item.spectrum_case} to {item.static_case}",
                    f"{item.spectrum_after / 1e3:,.0f} kN (x {item.factor:.3f})")
    for direction, period in report.governing_period.items():
        summary.add(f"Period {direction}", f"{period:.3f} s")
    if report.method_a is not None and report.height is not None:
        summary.add("Method A", f"T_A {report.method_a:.3f} s, hn {report.height / 1e3:.2f} m "
                                f"above the ground; cap {report.cap * report.method_a:.3f} s")
    for direction, total in report.mass_sum.items():
        summary.add(f"Modal mass {direction}", f"{total * 100:.1f} %")
    if report.seismic_weight is not None:
        summary.add("Seismic weight", f"{report.seismic_weight / 1e3:,.0f} kN")
    for warning in report.warnings:
        summary.fail(warning)
    for note in report.notes:
        summary.note(note)
    summary.file("Model (saved with its results)", model_path)
    return summary


if __name__ == "__main__":
    run_model_analysis()
