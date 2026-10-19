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
     Method A, TA = Ct hn^(3/4) (Ct in ft units, hn in ft, measured from the
     ground level: elevation 0 when the base is below it), and its cap of
     1.3 TA in zone 4 or 1.4 TA in zones 1 to 3;
   * modal participating mass: a warning when the sum is below 90 % in X or Y;
   * weight: the seismic weight from the mass source against the base reaction
     of the same loads (they must agree within 1 %), and the weight per story.

Drift is checked by ``sdt drift``; the irregularities, the torsion amplifier Ax
and the P-delta stability coefficient by ``sdt irregularity``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    # Run as a script: make the project folder (two levels up) importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.criteria.code_config import NSCP  # noqa: E402
from etabs_api.core.connection import save_model  # noqa: E402
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
    """NSCP Eq. 208-12 (UBC 97 Eq. 30-8): TA = Ct hn^(3/4), Ct in ft units."""
    return ct * (height_mm / FT) ** NSCP.seismic.period_exponent


def period_cap(zone_factor: float) -> float:
    """NSCP 208.5.2.2: Method B may not exceed 1.3 TA in zone 4, 1.4 TA otherwise."""
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
    target: float = SCALE_TARGET  # share of the static base shear: 1.0, or 0.9 when regular


@dataclass
class MinimumScaling:
    """A drift load case raised to the zone 4 minimum of Eq. 208-11."""

    case: str
    coefficient: float           # V/W of the pattern as ETABS computed it
    minimum: float               # 0.8 Z Nv I / R
    factor: float                # scale factor of the pattern in its load case (1 = not needed)
    soil: str
    nv: float


@dataclass
class AnalysisReport:
    """What ``analyze_model`` found."""

    model_path: str = ""
    scaling: list[ShearScaling] = field(default_factory=list)
    minimum_scaling: list[MinimumScaling] = field(default_factory=list)
    periods: pd.DataFrame = field(default_factory=pd.DataFrame)
    governing_period: dict[str, float] = field(default_factory=dict)
    method_a: float | None = None
    cap: float | None = None
    height: float | None = None                # mm, hn above the ground level
    ground_elevation: float = 0.0              # mm
    height_from: str = ""                      # the stories hn runs between, when the patterns say
    mass_sum: dict[str, float] = field(default_factory=dict)
    seismic_weight: float | None = None        # N, from the mass source, every level
    # N: the weight above the base of the static seismic patterns, the W of the
    # base shear V = Cs W (sdt check compares the patterns with it)
    base_shear_weight: float | None = None
    reaction_weight: float | None = None       # N, base reaction of the mass-source loads
    story_weights: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # checks that do not apply to this model
    scale_target: float = SCALE_TARGET          # 0.9 when the structure is regular
    scale_reason: str = ""
    ax: object = None                           # seismic_factors.AxResult
    rho: dict = field(default_factory=dict)     # direction -> seismic_factors.Rho
    rho_combinations: int = 0

    def seismic_lines(self) -> list[str]:
        """Ax, rho and the scaling share, as lines."""
        lines = []
        if self.scale_reason:
            lines.append(self.scale_reason)
        ax = self.ax
        if ax is not None:
            if ax.applied:
                worst = max(ax.ax.items(), key=lambda item: item[1])
                lines.append(f"Ax (NSCP 208.5.1.4): applied after {ax.rounds} analysis round(s); "
                             f"largest {worst[1]:.2f} at {worst[0][0]} {worst[0][1]}. The added "
                             f"torsion is in the patterns {', '.join(ax.patterns)}; the spectrum "
                             f"eccentricity is {ax.spectrum_eccentricity:.3f}.")
                lines += [f"  {story} {direction}: Ax {value:.2f}"
                          for (story, direction), value in sorted(ax.ax.items())
                          if value > 1.0 + 1e-9]
            lines += ax.notes
        for direction, r in sorted(self.rho.items()):
            lines.append(f"rho {direction} = {r.rho:.3f} (NSCP 208.6.1: 2 - 6.1 / (rmax sqrt AB) "
                         f"= 2 - 6.1 / ({r.r_max:.3f} x sqrt {r.area:.0f}) = {r.computed:.3f}; "
                         f"rmax at {r.story or '-'}, {r.element or '-'})")
            lines += [f"  {note}" for note in r.notes]
        if self.rho:
            lines.append(f"rho put in {self.rho_combinations} strength and service combinations "
                         "(EQ_RHO and RSA_RHO); the Em and drift combinations keep rho 1.0."
                         if self.rho_combinations else
                         "rho not put in the combinations: the model has none of the setup's "
                         "(EQ_COMBO_nn); use it in your own.")
        return lines

    def report(self):
        """The report for the terminal: tables, and the equations with their numbers."""
        from utilities.terminal import Report

        out = Report("ETABS ANALYSIS", [f"Model: {self.model_path}"] if self.model_path else [])
        out.heading(f"Response spectrum scaled to {self.scale_target * 100:g} % of the static "
                    "base shear (drift cases: 100 % of the drift patterns)")
        out.table(["Direction", "Static case", "V static (kN)", "Spectrum case",
                   "V before (kN)", "V after (kN)", "Scale factor x"],
                  [[s.direction, s.static_case, f"{s.static_shear / 1e3:,.1f}", s.spectrum_case,
                    f"{s.spectrum_before / 1e3:,.1f}", f"{s.spectrum_after / 1e3:,.1f}",
                    f"{s.factor:.4f}"] for s in self.scaling])
        out.equations([f"{s.spectrum_case}: factor = Vstatic / Vspectrum = "
                       f"{s.static_shear / 1e3:,.1f} / {s.spectrum_before / 1e3:,.1f} = "
                       f"{s.factor:.4f}" for s in self.scaling if s.factor != 1.0])
        if not self.scaling:
            out.lines(["No static and spectrum cases were found to compare."])
        raised = [m for m in self.minimum_scaling if m.factor > 1.0]
        if raised:
            out.heading("Drift load cases raised to the zone 4 minimum (Eq. 208-11)")
            out.table(["Case", "V/W of ETABS", "0.8 Z Nᵥ I / R", "Soil", "Nᵥ", "Scale factor x"],
                      [[m.case, f"{m.coefficient:.4f}", f"{m.minimum:.4f}", m.soil,
                        f"{m.nv:g}", f"{m.factor:.3f}"] for m in raised])
            out.equations([f"{m.case}: factor = (0.8 Z Nᵥ I / R) / (V/W) = {m.minimum:.4f} / "
                           f"{m.coefficient:.4f} = {m.factor:.3f}" for m in raised])
            out.lines(["Ca and Cv are typed in, and ETABS then takes Nv = 1 in the minimum."])
        out.heading("Periods")
        out.table(["Direction", "Governing modal period (s)"],
                  [[d, f"{t:.3f}"] for d, t in self.governing_period.items()])
        if self.method_a is not None and self.height is not None:
            where = (f"{self.height_from}, the stories of the static seismic patterns"
                     if self.height_from else
                     f"above the ground at elevation {self.ground_elevation / 1e3:g} m")
            out.equations([
                f"hₙ = {self.height / 1e3:.2f} m = {self.height / FT:.2f} ft  ({where})",
                f"TA = Cₜ hₙ³⁄⁴ = {self.method_a / (self.height / FT) ** 0.75:g} × "
                f"{self.height / FT:.2f}⁰·⁷⁵ = {self.method_a:.3f} s   (NSCP Eq. 208-12)",
                f"T ≤ {self.cap:.1f} TA = {self.cap * self.method_a:.3f} s   (Method B cap, "
                "NSCP 208.5.2.2)"])
        if not self.periods.empty:
            head = self.periods.head(12)
            out.table([str(c) for c in head.columns], head.values.tolist(), "Modes")
        out.heading("Modal participating mass")
        out.table(["Direction", "Sum (%)", "Needed (%)"],
                  [[d, f"{total * 100:.1f}", f"{NSCP.seismic.modal_mass * 100:g}"]
                   for d, total in self.mass_sum.items()])
        out.heading("Weight")
        rows = []
        if self.seismic_weight is not None:
            rows.append(["Weight from the mass source, every level",
                         f"{self.seismic_weight / 1e3:,.1f}"])
        if self.base_shear_weight is not None:
            rows.append([f"Seismic weight W, above {self.height_from.split(' to ')[0]} "
                         "(the base of the seismic patterns)",
                         f"{self.base_shear_weight / 1e3:,.1f}"])
        if self.reaction_weight is not None:
            rows.append(["Base reaction of the same loads", f"{self.reaction_weight / 1e3:,.1f}"])
        out.table(["", "kN"], rows)
        if not self.story_weights.empty:
            out.table([str(c) for c in self.story_weights.columns],
                      self.story_weights.values.tolist(), "Weight of each story")
        seismic = self.seismic_lines()
        if seismic:
            out.heading("Torsion amplification, redundancy and scaling")
            out.lines(seismic)
        out.heading("Warnings")
        out.lines(self.warnings or ["None."], "yellow" if self.warnings else "green")
        if self.notes:
            out.heading("Not applicable to this model")
            out.lines(self.notes)
        return out

    def text(self) -> str:
        """The report as terminal text."""
        lines = ["", "=" * 72, "ETABS ANALYSIS", "=" * 72]
        if self.model_path:
            lines.append(f"Model: {self.model_path}")
        lines += ["", f"--- Response spectrum scaled to {self.scale_target * 100:g} % of the "
                  "static base shear (drift cases: 100 % of the drift patterns) ---"]
        for s in self.scaling:
            lines.append(
                f"{s.direction}: static {s.static_case} {s.static_shear / 1e3:,.1f} kN | "
                f"{s.spectrum_case} {s.spectrum_before / 1e3:,.1f} kN -> "
                f"{s.spectrum_after / 1e3:,.1f} kN (scale factor x {s.factor:.4f})"
            )
        if not self.scaling:
            lines.append("No static and spectrum cases were found to compare.")
        raised = [m for m in self.minimum_scaling if m.factor > 1.0]
        if raised:
            lines += ["", "--- Drift load cases raised to the zone 4 minimum 0.8 Z Nv I / R "
                      "(Eq. 208-11) ---"]
            lines += [f"{m.case}: V/W {m.coefficient:.4f} -> {m.minimum:.4f} (soil {m.soil}, Nv "
                      f"{m.nv:g}): the pattern is scaled x {m.factor:.3f} in its load case"
                      for m in raised]
            lines.append("Ca and Cv are typed in, and ETABS then takes Nv = 1 in the minimum.")
        lines += ["", "--- Periods ---"]
        for direction, period in self.governing_period.items():
            lines.append(f"Governing modal period {direction}: {period:.3f} s")
        if self.height is not None:
            lines.append(f"Height hn = {self.height / 1e3:.2f} m ({self.height_from}, the "
                         "stories of the static seismic patterns)" if self.height_from else
                         f"Height above the ground hn = {self.height / 1e3:.2f} m "
                         f"(ground level at elevation {self.ground_elevation / 1e3:g} m)")
        if self.method_a is not None:
            lines.append(f"Method A, TA = Cₜ hₙ³⁄⁴: {self.method_a:.3f} s; "
                         f"Method B cap {self.cap:.1f} TA = {self.cap * self.method_a:.3f} s")
        if not self.periods.empty:
            lines.append(self.periods.head(12).to_string(index=False))
        lines += ["", "--- Modal participating mass ---"]
        for direction, total in self.mass_sum.items():
            lines.append(f"Sum {direction}: {total * 100:.1f} %")
        lines += ["", "--- Weight ---"]
        if self.seismic_weight is not None:
            lines.append("Weight from the mass source, every level: "
                         f"{self.seismic_weight / 1e3:,.1f} kN")
        if self.base_shear_weight is not None:
            lines.append(f"Seismic weight W, above {self.height_from.split(' to ')[0]} (the base "
                         f"of the seismic patterns): {self.base_shear_weight / 1e3:,.1f} kN")
        if self.reaction_weight is not None:
            lines.append(f"Base reaction of the same loads:     {self.reaction_weight / 1e3:,.1f} kN")
        if not self.story_weights.empty:
            lines.append(self.story_weights.to_string(index=False))
        seismic = self.seismic_lines()
        if seismic:
            lines += ["", "--- Torsion amplification, redundancy and scaling ---"] + seismic
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
    """Multiply every scale factor of a response spectrum case.

    The model is unlocked first. ETABS accepts a new scale factor on a locked
    model and the next run gives the new base shear, but it keeps the
    accidental torsion of the run before, at the old scale: the story torsion
    of the spectrum cases is then too small. Unlocked, the next run is
    complete.
    """
    model = connector.sap_model
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    api = model.LoadCases.ResponseSpectrum
    count, directions, functions, scales, systems, angles, status = api.GetLoads(case)
    if status != 0:
        raise RuntimeError(f"Could not read the loads of {case}.")
    scales = [float(s) * factor for s in as_list(scales)]
    result = api.SetLoads(case, count, as_list(directions), as_list(functions), scales,
                          as_list(systems), as_list(angles))
    if (result[-1] if isinstance(result, (list, tuple)) else result) != 0:
        raise RuntimeError(f"Could not set the scale factor of {case}.")


def scale_spectrum_to_static(connector, run, progress=None, target: float = SCALE_TARGET
                             ) -> list[ShearScaling]:
    """Scale each spectrum case up to ``target`` times the static base shear of its
    direction (1.0; 0.9 for a regular structure, NSCP 208.5.3.5.4). The drift
    spectrum cases always go to 100 % of the drift patterns.

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
                                 abs(first.loc[case, component]),
                                 SCALE_TARGET if drift else target)
    say = progress or (lambda text: None)
    for attempt in range(1, MAX_SCALING_RUNS + 1):
        changed = False
        for item in out.values():
            if item.spectrum_after <= 0:
                continue
            ratio = item.target * item.static_shear / item.spectrum_after
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
        if all(abs(i.spectrum_after / (i.target * i.static_shear) - 1) <= SCALE_TOLERANCE
               or i.spectrum_after > i.target * i.static_shear for i in out.values()):
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


def seismic_height(connector) -> tuple[float, float, str] | None:
    """(base elevation, hn, "bottom to top") of the static seismic patterns, mm.

    hn runs between the stories the patterns themselves are given, which is
    the height ETABS uses for the period: from the ground level, or from the
    bottom of the foundation when the engineer took the base there, up to
    the top story of the patterns. None when the model has no such pattern.
    """
    try:
        with connector.extraction_units():
            stories = connector.sap_model.Story.GetStories()
        elevation = {str(n): float(z) for n, z in zip(as_list(stories[1]), as_list(stories[2]))}
        table = _read(connector, "Load Pattern Definitions - Auto Seismic - UBC 97")
        if "IsAuto" in table.columns:
            table = table[table["IsAuto"].astype(str) != "Yes"]
        for low, high in zip(table["BotStory"].astype(str), table["TopStory"].astype(str)):
            if low in elevation and high in elevation and elevation[high] > elevation[low]:
                return elevation[low], elevation[high] - elevation[low], f"{low} to {high}"
    except Exception:
        return None
    return None


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


def weight_above(weights: pd.DataFrame, bottom: str) -> float | None:
    """The weight, in N, of the levels above ``bottom``, the story the static
    seismic patterns start from. ``weights``: Story and Weight (kN), from the
    top down. None when the story is not in the table."""
    stories = [str(name) for name in weights["Story"]]
    if bottom not in stories:
        return None
    return float(weights["Weight (kN)"].iloc[:stories.index(bottom)].sum() * 1e3)


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
    from etabs_api.core.connection import use_working_units

    opened = (return_code(save_model(model, path)) == 0
              and return_code(model.File.OpenFile(path)) == 0)
    use_working_units(model)  # a file opens in its own units
    return opened


def static_case_scales(model, cases: list[str]) -> dict[str, float]:
    """The scale factor of each linear static case's own load pattern (1 when not read)."""
    out = {}
    api = model.LoadCases.StaticLinear
    for case in cases:
        try:
            loads = api.GetLoads(case)
            names = [str(n) for n in as_list(loads[2])]
            factors = [float(f) for f in as_list(loads[3])]
            out[case] = factors[names.index(case)] if case in names else 1.0
        except Exception:
            out[case] = 1.0
    return out


def _set_static_case_scale(model, case: str, factor: float) -> bool:
    api = model.LoadCases.StaticLinear
    loads = api.GetLoads(case)
    kinds, names = [str(k) for k in as_list(loads[1])], [str(n) for n in as_list(loads[2])]
    factors = [float(f) for f in as_list(loads[3])]
    if case not in names:
        return False
    factors[names.index(case)] = float(factor)
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    result = api.SetLoads(case, len(names), kinds, names, factors)
    return (result[-1] if isinstance(result, (list, tuple)) else result) == 0


def minimum_scalings(patterns: pd.DataFrame, cases: list[str]) -> list[MinimumScaling]:
    """What each drift case needs to meet Eq. 208-11, from the seismic pattern table.

    Only the cases whose pattern has Ca and Cv typed in, when the two values
    belong to one zone 4 site (``ubc97.typed_zone4_minimum``): ETABS applies
    the minimum itself, with the right Nv, to a pattern defined by its site.
    """
    from etabs_api.workflows.ubc97 import typed_zone4_minimum

    out = []
    needed = {"Name", "Ca", "Cv", "I", "R", "CoeffUsed"}
    if patterns is None or patterns.empty or not needed <= set(patterns.columns):
        return out
    parents = patterns["Name"].astype(str).str.split("(").str[0]
    for case in cases:
        rows = patterns[parents == case]
        if rows.empty:
            continue
        r = rows.iloc[0]
        if "Z" in rows.columns and pd.notna(r.get("Z")):
            continue                                    # defined by its site: ETABS has the Nv
        try:
            ca, cv, importance, r_factor = (float(r[k]) for k in ("Ca", "Cv", "I", "R"))
            used = float(pd.to_numeric(rows["CoeffUsed"], errors="coerce").max())
        except (TypeError, ValueError):
            continue
        found = typed_zone4_minimum(ca, cv, importance, r_factor)
        if found is None or not used > 0:
            continue
        minimum, soil, nv = found
        factor = minimum / used if minimum > used * (1.0 + SCALE_TOLERANCE / 10) else 1.0
        out.append(MinimumScaling(case, used, minimum, factor, soil, nv))
    return out


def raise_drift_cases_to_minimum(connector, run, progress=None) -> list[MinimumScaling]:
    """Scale the drift load cases up to the zone 4 minimum 0.8 Z Nv I / R (Eq. 208-11).

    With Ca and Cv typed in, ETABS keeps no Nv and applies the minimum with
    Nv = 1, so a drift pattern of a long-period building near a source gets
    too little force. The pattern keeps its definition; its scale factor in
    its own load case becomes minimum / (V/W ETABS computed), or 1 when the
    minimum does not govern, and ``run`` analyses again if a factor changed.
    Called after every analysis, since the factor follows the period.
    """
    model = connector.sap_model
    cases = seismic_static_cases(connector, SEISMIC_DRIFT_PATTERN_TYPE)
    if not cases:
        return []
    names = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    model.DatabaseTables.SetLoadPatternsSelectedForDisplay(names)
    try:
        patterns = connector._read_database_table(
            "Load Pattern Definitions - Auto Seismic - UBC 97")
    except Exception:  # no UBC 97 pattern in this model
        return []
    wanted = minimum_scalings(patterns, cases)
    now = static_case_scales(model, [m.case for m in wanted])
    changed = [m for m in wanted if abs(now[m.case] - m.factor) > 1e-4 * m.factor]
    for m in changed:
        if not _set_static_case_scale(model, m.case, m.factor):
            raise RuntimeError(f"Could not set the scale factor of {m.case}.")
    if changed:
        (progress or (lambda text: None))(
            "Running the analysis again with the drift cases at the zone 4 minimum\n"
            + ", ".join(f"{m.case} x {m.factor:.3f}" for m in changed))
        run()
    return wanted


def analyze_model(connector, zone_factor: float | None = None, ct: float | None = None,
                  scale: bool = True, progress=None, target: float = SCALE_TARGET,
                  amplify_torsion: bool = False, redundancy: bool = False,
                  setup: dict | None = None) -> AnalysisReport:
    """Reset the spectrum to g I / R, run, scale it to ``target`` times the static
    base shear, run again, and check periods, mass and weight. ``progress`` gets
    each step. ``amplify_torsion`` applies Ax before the scaling and ``redundancy``
    puts rho in the combinations after it (``seismic_factors``)."""
    say = progress or (lambda text: None)
    from etabs_api.core.connection import model_file

    report = AnalysisReport(model_path=model_file(connector.sap_model), scale_target=target)
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
        say("Checking the drift patterns against the zone 4 minimum")
        report.minimum_scaling = raise_drift_cases_to_minimum(connector, run, say)
    if amplify_torsion:
        from etabs_api.workflows.seismic_factors import apply_ax

        say("Torsional irregularity: amplifying the accidental torsion (Ax)")
        report.ax = apply_ax(connector, run, say)
    if scale:
        say("Comparing the spectrum and static base shears")
        report.scaling = scale_spectrum_to_static(connector, run, say, target)
        if not report.scaling:
            report.notes.append(
                "Scaling of the response spectrum to the static base shear: not applicable (it "
                "needs a static seismic case and a response spectrum case in each direction).")
    if redundancy:
        from etabs_api.workflows.seismic_factors import compute_rho, rho_combinations

        say("Redundancy factor rho")
        report.rho = compute_rho(connector, setup, say)
        if report.rho:
            report.rho_combinations = rho_combinations(
                connector.sap_model, {d: r.rho for d, r in report.rho.items()} | {
                    d: 1.0 for d in ("X", "Y") if d not in report.rho})
            if report.rho_combinations:
                # editing the combinations unlocks the model and clears its results
                say("Running the analysis again with rho in the combinations")
                run()
        for r in report.rho.values():
            if r.over_smrf:
                report.warnings.append(f"rho {r.direction} {r.rho:.3f} is over 1.25 for special "
                                       "moment frames: add bays (NSCP 208.6.1).")
    for item in report.scaling:
        if item.spectrum_after < item.target * item.static_shear * (1 - SCALE_TOLERANCE):
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
        own = seismic_height(connector)
        if own is not None:   # the height the static patterns use, so the cap is theirs
            report.ground_elevation, report.height, report.height_from = own
        report.method_a = method_a_period(ct, report.height)
        report.cap = period_cap(zone_factor)
        limit = report.cap * report.method_a
        for direction, period in report.governing_period.items():
            if period > limit:
                report.warnings.append(
                    f"Modal period {direction} {period:.3f} s is above {report.cap:.1f} TA = "
                    f"{limit:.3f} s: the static base shear uses the capped period.")

    say("Reading the seismic weight and the base reactions")
    weights = story_weights(connector)
    report.story_weights = weights
    report.seismic_weight = float(weights["Weight (kN)"].sum() * 1e3)
    report.base_shear_weight = weight_above(weights, report.height_from.split(" to ")[0]) \
        if report.height_from else None
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
    original = path
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
        save_model(model, path)

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

    from etabs_api.workflows import seismic_factors as sf

    target, reason = sf.IRREGULAR_TARGET, ""
    record = sf.load_regularity(path) or sf.load_regularity(original)
    if record is None:
        reason = ("Spectrum at 100 % of the static base shear: no sdt irregularity result for "
                  "this model. For 90 % on a regular structure, run sdt irregularity, then sdt "
                  "analyze again (NSCP 208.5.3.5.4).")
    elif record.get("irregular"):
        reason = ("Spectrum at 100 % of the static base shear: irregular ("
                  + ", ".join(record["irregular"]) + "), NSCP 208.5.3.5.4 item 3.")
    else:
        answer = select_option(
            title, "sdt irregularity found the structure regular in every type it checks. "
            "NSCP 208.5.3.5.4 lets the response spectrum go down to 90 % of the static base "
            "shear for a regular structure.\n\nAre these types, which it cannot check, also "
            "absent?\n\n  - in-plane discontinuity in vertical elements (vertical type 4)\n"
            "  - out-of-plane offsets (plan type 4)\n  - non-parallel systems (plan type 5)",
            ["Yes, the structure is regular: scale to 90 %",
             "No, or not sure: scale to 100 %"])
        if answer is None:
            return None
        if answer.startswith("Yes"):
            target = sf.REGULAR_TARGET
            reason = ("Spectrum at 90 % of the static base shear: regular by sdt irregularity, "
                      "and the types it cannot check confirmed absent (NSCP 208.5.3.5.4 "
                      "item 1).")
        else:
            reason = ("Spectrum at 100 % of the static base shear: the types sdt irregularity "
                      "cannot check were not confirmed absent.")
    try:
        with LoadingWindow("Analysis") as window:
            report = analyze_model(connector, zone, ct, progress=window.update, target=target,
                                   amplify_torsion=True, redundancy=True, setup=saved)
            report.scale_reason = reason
    except (RuntimeError, COMError) as error:  # COMError: ETABS stopped answering
        show_warning(
            f"ETABS could not finish the analysis:\n\n{error}\n\n"
            "Run the analysis once in ETABS (Analyze > Run Analysis) to see its own message. "
            "A model with no supports, or with members that are not connected, cannot be "
            "analysed. If ETABS closed, open the model again.", title=title)
        return None
    # Save() keeps the results; Save(path) drops them even for the same file
    save_model(model)
    from utilities import terminal

    if terminal.drawn():
        report.report().show()
    else:
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
    for m in report.minimum_scaling:
        if m.factor > 1.0:
            summary.add(f"{m.case} to Eq. 208-11", f"V/W {m.minimum:.4f} with Nv {m.nv:g} "
                                                  f"(x {m.factor:.3f})")
    for direction, period in report.governing_period.items():
        summary.add(f"Period {direction}", f"{period:.3f} s")
    if report.method_a is not None and report.height is not None:
        summary.add("Method A", f"TA {report.method_a:.3f} s, hn {report.height / 1e3:.2f} m "
                                f"above the ground; cap {report.cap * report.method_a:.3f} s")
    for direction, total in report.mass_sum.items():
        summary.add(f"Modal mass {direction}", f"{total * 100:.1f} %")
    if report.base_shear_weight is not None:
        summary.add("Seismic weight W", f"{report.base_shear_weight / 1e3:,.0f} kN above "
                    f"{report.height_from.split(' to ')[0]}")
    if report.seismic_weight is not None:
        summary.add("Weight of every level", f"{report.seismic_weight / 1e3:,.0f} kN")
    if report.scaling:
        summary.add("Spectrum scaled to", f"{report.scale_target * 100:g} % of the static base "
                                          "shear")
    if report.ax is not None:
        largest = max(report.ax.ax.values(), default=1.0)
        summary.add("Ax (NSCP 208.5.1.4)", f"largest {largest:.2f}, applied" if report.ax.applied
                    else "1.00 everywhere (no torsional irregularity)")
    for direction, r in sorted(report.rho.items()):
        summary.add(f"rho {direction} (NSCP 208.6.1)", f"{r.rho:.3f} (rmax {r.r_max:.3f} at "
                                                       f"{r.story or '-'})")
    for warning in report.warnings:
        summary.fail(warning)
    for line in report.seismic_lines():
        if not line.startswith("  "):
            summary.note(line)
    for note in report.notes:
        summary.note(note)
    summary.file("Model (saved with its results)", model_path)
    return summary


if __name__ == "__main__":
    run_model_analysis()
