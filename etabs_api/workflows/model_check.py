"""Check the open ETABS model for missing or inconsistent inputs (``python main.py check``).

Read only: nothing in the model is changed. Each check prints one line,

    [FAIL] Seismic: Story range should be GF to RD: EQXPE 3F-RD   (NSCP 208.5.2.3)

with OK, INFO (a fact to confirm), WARN (likely missing or unusual), FAIL
(wrong) or N/A (needs analysis results; run ``sdt analyze``).

The basis is NSCP 2015 (7th edition): loads 205, combinations 203, wind 207,
earthquake 208 and the special moment frame limits of 418 (ACI 318M-14
chapter 18). ETABS has no NSCP seismic code, so the model uses the UBC 97
auto-seismic patterns and response spectrum function. NSCP 208 is UBC 97
with the same equations; the checks hold the UBC 97 inputs (Z, Na, Nv, Ca,
Cv, I, R, Ct) to the NSCP tables, and with results recompute the NSCP base
shear coefficient from the period and weight ETABS used, so a pattern edited
by hand is checked too.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import CODE, NSCP  # noqa: E402
from etabs_api.core.helpers import as_list  # noqa: E402

OK, INFO, WARN, FAIL, NA = "OK", "INFO", "WARN", "FAIL", "N/A"
DEAD, SUPER_DEAD, LIVE, REDUCIBLE, SEISMIC, WIND, OTHER, ROOF = 1, 2, 3, 4, 5, 6, 8, 11
SEISMIC_DRIFT = 61

# Code values: design/code_config.py
SEIS = NSCP.seismic
LOADS = NSCP.load_factors
LLR = NSCP.live_load_reduction
MODEL = NSCP.analysis
SMRF_BEAM, SMRF_COLUMN, SMRF_MATERIAL = CODE.beam_seismic, CODE.column_seismic, CODE.seismic_material
COEFF_TOLERANCE = 0.005  # accepted difference of Ca, Cv, V/W (project rule)
CAP_TOLERANCE = 0.005    # relative, on the period cap (project rule)
SHEAR_OK = 0.99          # spectrum base shear counted as 100 % from here (project rule)
COVER_TO_BAR = 60.0      # mm, h - d of a girder for the 4d check (office cover)
WEIGHT_TOLERANCE = 0.01  # W used against the story weights (project rule)
FT = 304.8  # mm


@dataclass
class Finding:
    group: str
    status: str
    text: str
    ref: str = ""


@dataclass
class ModelData:
    """What the checks read; tables by their ETABS names."""

    path: str = ""
    analysed: bool = False
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    pattern_types: dict[str, int] = field(default_factory=dict)
    self_weight: dict[str, float] = field(default_factory=dict)
    stories: list[tuple[str, float]] = field(default_factory=list)  # base first: (name, elevation)
    base_shear: dict[str, tuple[float, float]] = field(default_factory=dict)  # case: (FX, FY), N
    modal: pd.DataFrame = field(default_factory=pd.DataFrame)
    drifts: dict[str, tuple[float, str]] = field(default_factory=dict)  # case: (ratio, story)
    story_weights: dict[str, float] = field(default_factory=dict)  # story: seismic weight, N
    drift_from: str = ""  # "combinations" (DRIFT, WDRIFT) or "cases" (older models)
    wind_drift_denominator: float = NSCP.wind.drift_limit_denominator  # h / this
    # section property modifiers: section -> (A, As2, As3, J, I22, I33, mass, weight)
    section_modifiers: dict[str, tuple] = field(default_factory=dict)
    # drift combinations the user picked on a model without the DRIFT / WDRIFT
    # names: combination -> (its lateral load case, whether it is wind)
    drift_case_of: dict[str, tuple[str, bool]] = field(default_factory=dict)
    r_factor: float | None = None  # R for the drift when no UBC 97 pattern gives it

    def table(self, name: str) -> pd.DataFrame:
        table = self.tables.get(name)
        return table if isinstance(table, pd.DataFrame) else pd.DataFrame()


def _num(value, default=math.nan) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# =============================================================================
# CHECKS (no ETABS)
# =============================================================================
def check_model(d: ModelData) -> list[Finding]:
    from etabs_api.workflows.sections import parse_section

    out = []
    sections = d.table("Frame Assignments - Section Properties")
    if sections.empty:
        return [Finding("Model", FAIL, "The model has no frames.")]
    names = sections["UniqueName"].astype(str)
    untagged = names[names.str.isnumeric()].tolist()
    out.append(Finding("Model", WARN if untagged else OK,
                       f"{len(untagged)} beams/columns without a tag (numeric names; the design "
                       f"offers to tag them or to design them as they are)"
                       f"{': ' + ', '.join(untagged[:10]) if untagged else ''}"
                       if untagged else "Every beam and column is tagged.", "sdt tag"))
    odd = sorted({str(p) for p in sections["SectProp"] if parse_section(str(p)) is None})
    out.append(Finding("Model", WARN if odd else OK,
                       f"Sections outside the setup naming (sdt design reads their size from "
                       f"ETABS and creates new sizes under the setup names): "
                       f"{', '.join(odd[:8])}" if odd else
                       "Every frame uses a setup section (G_, B_, FTB_, CR_, C_)."))
    floors = d.table("Floor Object Connectivity")
    floors = floors[floors["Story"].notna()] if "Story" in floors else floors
    areas = d.table("Area Assignments - Summary")
    if not floors.empty:
        floor_names = set(floors["UniqueName"].astype(str))
        info = areas[areas["UniqueName"].astype(str).isin(floor_names)] if not areas.empty else areas
        no_diaphragm = info[info["Diaphragm"].astype(str).isin(["None", "", "nan"])] \
            if "Diaphragm" in info else info
        no_section = info[info["SectProp"].astype(str).isin(["None", "", "nan"])] \
            if "SectProp" in info else info.iloc[0:0]
        out.append(Finding("Model", WARN if len(no_diaphragm) else OK,
                           f"{len(no_diaphragm)} floors without a diaphragm" if len(no_diaphragm)
                           else f"All {len(floor_names)} floors have a diaphragm.",
                           "NSCP 208.5.1.3"))
        out.append(Finding("Model", WARN if len(no_section) else OK,
                           f"{len(no_section)} floors without a slab section" if len(no_section)
                           else "All floors have a slab section."))
    else:
        out.append(Finding("Model", WARN, "The model has no floor objects: no slabs, no floor "
                           "loads and no rigid diaphragms from slabs."))
    # supports at the base of the columns
    points, columns = d.table("Point Object Connectivity"), d.table("Column Object Connectivity")
    restraints = d.table("Joint Assignments - Restraints")
    if not points.empty and not columns.empty:
        z = dict(zip(points["UniqueName"].astype(str), pd.to_numeric(points["Z"], errors="coerce")))
        bottoms = [str(p) for p in columns["UniquePtI"]]
        lowest = min(z.get(p, math.inf) for p in bottoms)
        base_joints = {p for p in bottoms if abs(z.get(p, math.inf) - lowest) < 1}
        fixed = pinned = 0
        restrained = {}
        if not restraints.empty:
            for _, r in restraints.iterrows():
                restrained[str(r["UniqueName"])] = (r.get("UX"), r.get("RX"))
        for joint in base_joints:
            ux, rx = restrained.get(joint, ("No", "No"))
            if ux == "Yes" and rx == "Yes":
                fixed += 1
            elif ux == "Yes":
                pinned += 1
        free = len(base_joints) - fixed - pinned
        out.append(Finding("Model", FAIL if free else INFO,
                           f"Column bases at z = {lowest:g}: {fixed} fixed, {pinned} pinned, "
                           f"{free} without supports" + ("" if free else
                           " - confirm the support assumption matches the footing design")))
    beams = d.table("Beam Object Connectivity")
    if not beams.empty and not points.empty:
        free = free_beam_ends(points, beams, columns)
        out.append(Finding("Model", INFO if free else OK,
                           f"{len(free)} beams with a free end (cantilevers; check they are meant "
                           f"to be): {', '.join(free[:10])}" if free else "No beam has a free end."))
    return out


def free_beam_ends(points: pd.DataFrame, beams: pd.DataFrame, columns: pd.DataFrame,
                   tolerance: float = 5.0) -> list[str]:
    """Beams with an end that touches no other member.

    An end is supported when another beam or column shares its joint, or when
    the joint lies on another member (ETABS connects a beam framing into a
    girder between the girder's joints by meshing, without a shared point).
    """
    xyz = {str(n): (float(x), float(y), float(z)) for n, x, y, z in zip(
        points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
        pd.to_numeric(points["Y"], errors="coerce"), pd.to_numeric(points["Z"], errors="coerce"))}
    frames = []
    for table in (beams, columns):
        if table.empty:
            continue
        for name, i, j in zip(table["UniqueName"].astype(str), table["UniquePtI"].astype(str),
                              table["UniquePtJ"].astype(str)):
            if i in xyz and j in xyz:
                frames.append((name, i, j, xyz[i], xyz[j]))
    users: dict[str, set[str]] = {}
    for name, i, j, _, _ in frames:
        users.setdefault(i, set()).add(name)
        users.setdefault(j, set()).add(name)

    def on_other(point, own: str) -> bool:
        px, py, pz = point
        for name, _, _, a, b in frames:
            if name == own:
                continue
            ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            length2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
            if length2 == 0:
                continue
            t = ((px - a[0]) * ab[0] + (py - a[1]) * ab[1] + (pz - a[2]) * ab[2]) / length2
            if not 0.0 <= t <= 1.0:
                continue
            dx = a[0] + t * ab[0] - px
            dy = a[1] + t * ab[1] - py
            dz = a[2] + t * ab[2] - pz
            if dx * dx + dy * dy + dz * dz <= tolerance * tolerance:
                return True
        return False

    free = []
    for name, i, j, a, b in frames[:len(beams)]:
        for joint, point in ((i, a), (j, b)):
            if len(users.get(joint, ())) <= 1 and not on_other(point, name):
                free.append(name)
                break
    return free


def _loaded_patterns(d: ModelData) -> set[str]:
    loaded = set()
    area = d.table("Area Load Assignments - Uniform")
    if "LoadPattern" in area:
        loaded |= set(area["LoadPattern"].astype(str))
    sets = d.table("Shell Uniform Load Sets")
    assigned = d.table("Area Load Assignments - Uniform Load Sets")
    if "LoadPattern" in sets and "LoadSet" in assigned:
        names = sets["Name"].replace({None: pd.NA, "": pd.NA}).ffill().astype(str)
        used = set(assigned["LoadSet"].astype(str))
        loaded |= {p for n, p in zip(names, sets["LoadPattern"].astype(str)) if n in used}
    for name in ("Frame Loads Assignments - Distributed", "Frame Loads Assignments - Point",
                 "Joint Loads Assignments - Force"):
        table = d.table(name)
        if "LoadPattern" in table:
            loaded |= set(table["LoadPattern"].astype(str))
    loaded |= {p for p, sw in d.self_weight.items() if sw}
    return loaded


def check_loads(d: ModelData) -> list[Finding]:
    out = []
    loaded = _loaded_patterns(d)
    kinds = {DEAD: "dead", SUPER_DEAD: "super dead", LIVE: "live", REDUCIBLE: "reducible live",
             ROOF: "roof live"}
    empty = [f"{p} ({kinds[t]})" for p, t in d.pattern_types.items()
             if t in kinds and p not in loaded and not p.startswith("~")]
    out.append(Finding("Loads", INFO if empty else OK,
                       "Gravity patterns with no loads: " + ", ".join(empty) +
                       " - confirm intended" if empty else
                       "Every gravity load pattern has loads.", "NSCP 205"))
    roof = [p for p, t in d.pattern_types.items() if t == ROOF]
    if not roof or not any(p in loaded for p in roof):
        out.append(Finding("Loads", INFO, "No roof live load is assigned (the Lr combinations "
                           "and the roof deflection check see none) - confirm intended",
                           "NSCP 205.4 Table 205-3"))
    weight = [p for p, sw in d.self_weight.items() if sw]
    out.append(Finding("Loads", FAIL if len(weight) > 1 else (OK if weight else WARN),
                       f"Self weight on {', '.join(weight)} (counted {len(weight)} times)"
                       if len(weight) > 1 else (f"Self weight only on {weight[0]}." if weight else
                                                "No pattern carries the self weight.")))
    area = d.table("Area Load Assignments - Uniform")
    reducible = [p for p, t in d.pattern_types.items() if t == REDUCIBLE]
    heavy = []
    if "LoadPattern" in area:
        for p, value in zip(area["LoadPattern"].astype(str), area["Load"]):
            if p in reducible and abs(_num(value, 0)) * 1e3 > LLR.heavy_kpa:
                heavy.append(f"{p} {abs(_num(value)) * 1e3:.1f} kPa")
    out.append(Finding("Loads", WARN if heavy else OK,
                       f"Reducible live load above {LLR.heavy_kpa:g} kPa (NSCP allows no "
                       "reduction; should it be non-reducible?): " + ", ".join(sorted(set(heavy)))
                       if heavy else f"No reducible live load above {LLR.heavy_kpa:g} kPa.",
                       "NSCP 205.5"))
    return out


def nscp_coefficient(ca: float, cv: float, importance: float, r: float, period: float,
                     zone_factor: float, nv: float, drift: bool = False) -> float:
    """NSCP 208.5.2.1: the base shear V / W.

    V = Cv I / (R T) W (208-8), at most 2.5 Ca I / R W (208-9), at least
    0.11 Ca I W (208-10) and, in zone 4, 0.8 Z Nv I / R W (208-11). For drift
    (``drift``) the two lower limits do not apply (208.6.5.2).
    """
    value = min(cv * importance / (r * period), SEIS.plateau * ca * importance / r)
    if not drift:
        value = max(value, SEIS.minimum * ca * importance)
        if zone_factor >= SEIS.zone4_factor:
            value = max(value, SEIS.zone4_minimum * zone_factor * nv * importance / r)
    return value


def method_a_period(ct_ft: float, height_mm: float) -> float:
    """NSCP Eq. 208-12, T_A = Ct hn^(3/4), with Ct in ft units as ETABS has it."""
    return ct_ft * (height_mm / FT) ** SEIS.period_exponent


def drift_limit(period: float) -> float:
    """NSCP 208.6.5.1: 0.025 of the storey height when T < 0.7 s, otherwise 0.020."""
    return SEIS.drift_limit_short if period < SEIS.drift_period else SEIS.drift_limit_long


def check_seismic(d: ModelData, settings: dict | None = None) -> list[Finding]:
    from etabs_api.workflows.model_setup import lateral_story_range
    from etabs_api.workflows.ubc97 import near_source_factors, seismic_coefficients

    out = []
    names = [n for n, _ in d.stories]
    elevations = [z for _, z in d.stories]
    bottom, top = lateral_story_range(names, elevations) if names else ("", "")
    seismic = d.table("Load Pattern Definitions - Auto Seismic - UBC 97")
    if seismic.empty:
        return [Finding("Seismic", FAIL, "No UBC 97 seismic load patterns (ETABS has no NSCP "
                        "code; NSCP 208 is UBC 97).", "NSCP 208.5.2")]
    auto = seismic["IsAuto"].astype(str) == "Yes" if "IsAuto" in seismic else \
        pd.Series(False, index=seismic.index)
    rows = seismic[~auto]
    wrong = [f"{r['Name']} {r['BotStory']}-{r['TopStory']}" for _, r in rows.iterrows()
             if (str(r["BotStory"]), str(r["TopStory"])) != (bottom, top)]
    out.append(Finding("Seismic", FAIL if wrong else OK,
                       f"Story range should be {bottom} to {top}: " + ", ".join(wrong)
                       if wrong else f"Every seismic pattern covers {bottom} to {top}.",
                       "NSCP 208.5.2.3"))
    ecc = [str(r["Name"]) for _, r in rows.iterrows()
           if abs(_num(r.get("EccRatio"), 0) - SEIS.eccentricity) > 1e-6
           and any(str(r.get(k)) == "Yes" for k in ("XDirPlusE", "XDirMinusE", "YDirPlusE",
                                                    "YDirMinusE"))]
    out.append(Finding("Seismic", WARN if ecc else OK,
                       f"Accidental eccentricity not {SEIS.eccentricity:g}: " + ", ".join(ecc)
                       if ecc else f"Accidental eccentricity {SEIS.eccentricity:g} on every "
                       "pattern.", "NSCP 208.5.1.3"))
    strength = rows[rows["Name"].map(lambda n: d.pattern_types.get(str(n)) == SEISMIC)]
    expected = None
    if not strength.empty:
        r = strength.iloc[0]
        # user-defined Ca, Cv: ETABS writes no zone, soil or source columns at all
        has_site = "Z" in r.index and pd.notna(r.get("Z"))
        z = _num(r.get("Z")) if has_site else math.nan
        soil, source = str(r.get("SoilType", "")), str(r.get("SourceType", ""))
        distance = _num(r.get("SourceDist"))
        zone4 = z >= SEIS.zone4_factor if has_site else True
        if has_site:
            nscp_zone = any(abs(z - x) < 1e-6 for x in SEIS.nscp_zone_factors)
            out.append(Finding("Seismic", OK if nscp_zone else FAIL, f"Z = {z:g}" + (
                "" if nscp_zone else " (NSCP has only Z = " +
                " and ".join(f"{x:.2f}" for x in SEIS.nscp_zone_factors) + ")"),
                "NSCP Table 208-3"))
        else:
            out.append(Finding("Seismic", INFO, "Ca, Cv are user defined: ETABS keeps no zone, "
                               "soil or source with them, so zone 4 is assumed for the period "
                               "cap and the 0.8 Z Nv I / R minimum is not checked",
                               "NSCP Table 208-3"))
        typed = zone4 and not str(r.get("NearSrcOpt", "Per Code")).startswith("Per Code")
        try:
            if not has_site:
                raise ValueError("no site data")
            if typed:
                # Na, Nv typed in: ETABS uses them, not the source distance
                na, nv = _num(r.get("Na"), 1.0), _num(r.get("Nv"), 1.0)
                base = seismic_coefficients(z, soil, "C", 15.0)  # Na = Nv = 1
                expected = (base[0] * na, base[1] * nv)
                by_distance = near_source_factors(source, distance)
                if abs(by_distance[0] - na) > 1e-3 or abs(by_distance[1] - nv) > 1e-3:
                    match = [km / 10 for km in range(0, 201)
                             if abs(near_source_factors(source, km / 10)[1] - nv) < 5e-3]
                    out.append(Finding("Seismic", INFO, f"Na {na:g}, Nv {nv:g} are typed in "
                                       f"(user defined), so the source distance ({distance:g} km, "
                                       f"which would give Na {by_distance[0]:g}, Nv "
                                       f"{by_distance[1]:g}) is not used" + (
                                           f"; Nv {nv:g} is source {source} at about "
                                           f"{match[0]:g} km" if match else "") +
                                       " - confirm the distance to the fault",
                                       "NSCP Tables 208-5, 208-6"))
            else:
                expected = seismic_coefficients(z, soil, source, distance)
                na, nv = near_source_factors(source, distance) if zone4 else (1.0, 1.0)
        except Exception:
            expected, na, nv = None, 1.0, 1.0
        # Ca, Cv, Na, Nv of every strength pattern, whether per code or typed in.
        # "Per Code" patterns show placeholder Ca, Cv until ETABS analyses the model.
        per_code = str(r.get("CoeffOpt", "")).startswith("Per Code")
        if expected is not None and per_code and not d.analysed:
            out.append(Finding("Seismic", INFO,
                               f"Ca {expected[0]:.3f}, Cv {expected[1]:.3f} per code (soil {soil}, "
                               f"Z {z:g}, source {source} at {distance:g} km, Na {na:.2f}, Nv "
                               f"{nv:.2f}); ETABS works them out when it analyses the model "
                               "(the table shows placeholders until then)",
                               "NSCP Tables 208-5 to 208-8"))
        elif str(r.get("CoeffOpt", "")).startswith("User"):
            # typed Ca, Cv: ETABS ignores the soil and source fields, so the values
            # must agree with the response spectrum function (static = dynamic)
            function = d.table("Functions - Response Spectrum - UBC 97")
            spectrum = function.dropna(subset=["Ca"]).iloc[0] if "Ca" in function and                 function["Ca"].notna().any() else None
            typed = sorted({(round(_num(x["Ca"]), 3), round(_num(x["Cv"]), 3))
                            for _, x in strength.iterrows()})
            same = spectrum is not None and all(
                abs(ca - _num(spectrum["Ca"])) <= COEFF_TOLERANCE
                and abs(cv - _num(spectrum["Cv"])) <= COEFF_TOLERANCE for ca, cv in typed)
            out.append(Finding("Seismic", OK if same else WARN,
                               f"Ca, Cv user defined {typed}" +
                               (f", equal to the spectrum {spectrum['Name']}" if same else
                                (f", not equal to the spectrum {spectrum['Name']} (Ca "
                                 f"{_num(spectrum['Ca']):g}, Cv {_num(spectrum['Cv']):g})"
                                 if spectrum is not None else "")),
                               "NSCP Tables 208-7, 208-8"))
            if expected is not None and any(
                    abs(ca - expected[0]) > COEFF_TOLERANCE or abs(cv - expected[1]) >
                    COEFF_TOLERANCE for ca, cv in typed):
                out.append(Finding("Seismic", INFO,
                                   f"The soil and source fields (soil {soil}, Z {z:g}, source "
                                   f"{source} at {distance:g} km) would give Ca "
                                   f"{expected[0]:.3f}, Cv {expected[1]:.3f}; ETABS uses the "
                                   "typed values - confirm the site", "NSCP Tables 208-5 to 208-8"))
        elif expected is not None:
            bad = sorted({f"{x['Name']} Ca {_num(x['Ca']):g} Cv {_num(x['Cv']):g}"
                          for _, x in strength.iterrows()
                          if abs(_num(x["Ca"]) - expected[0]) > COEFF_TOLERANCE
                          or abs(_num(x["Cv"]) - expected[1]) > COEFF_TOLERANCE})
            out.append(Finding("Seismic", WARN if bad else OK,
                               f"Ca, Cv differ from NSCP {expected[0]:.3f}, {expected[1]:.3f} "
                               f"(soil {soil}, Z {z:g}, source {source} at {distance:g} km, "
                               f"Na {na:.2f}, Nv {nv:.2f}): " + ", ".join(bad) if bad else
                               f"Ca {expected[0]:.3f}, Cv {expected[1]:.3f} (soil {soil}, Z {z:g}, "
                               f"source {source} at {distance:g} km, Na {na:.2f}, Nv {nv:.2f}).",
                               "NSCP Tables 208-5 to 208-8"))
            if zone4 and na > SEIS.na_cap:
                out.append(Finding("Seismic", INFO, f"Na {na:.2f} > {SEIS.na_cap:g}: NSCP lets "
                                   f"Na be capped at {SEIS.na_cap:g} for soil SA to SD, rho 1.0, "
                                   "SMRF and no Type 1, 4, 5 vertical / Type 1, 4 plan "
                                   "irregularity", "NSCP 208.4.4.3"))
            if zone4 and distance <= SEIS.near_fault_km:
                out.append(Finding("Seismic", INFO, f"Within {SEIS.near_fault_km:g} km of a major "
                                   "fault: a site specific spectrum is recommended for high rise "
                                   "and essential facilities", "NSCP 208.4.4.3"))
        importance = {_num(x["I"]) for _, x in strength.iterrows()}
        odd_i = [i for i in importance
                 if not any(abs(i - x) < 1e-6 for x in SEIS.importance_factors)]
        out.append(Finding("Seismic", WARN if odd_i else INFO,
                           f"Importance factor I = {', '.join(f'{i:g}' for i in sorted(importance))}"
                           + (" (NSCP: " + ", ".join(f"{x:g}" for x in SEIS.importance_factors)
                              + ")" if odd_i else " - Table 208-1 by occupancy category"),
                           "NSCP Table 208-1"))
        factors = {_num(x["R"]) for _, x in strength.iterrows()}
        for value in sorted(factors):
            known = next((v for k, v in SEIS.concrete_r.items() if abs(k - value) < 1e-6), None)
            if known is None:
                out.append(Finding("Seismic", WARN, f"R = {value:g} is not a concrete system of "
                                   "Table 208-11A (steel and masonry: 208-11B, 208-11C)",
                                   "NSCP Table 208-11A"))
            elif zone4 and not known[1]:
                out.append(Finding("Seismic", FAIL, f"R = {value:g} ({known[0]}) is not "
                                   "permitted in zone 4", "NSCP Table 208-11A"))
            else:
                smrf = value >= SEIS.smrf_r - 1e-6
                out.append(Finding("Seismic", OK if smrf else INFO,
                                   f"R = {value:g}: {known[0]} (Omega0 {LOADS.omega0:g})" +
                                   ("; the SMRF limits of 418 below apply" if smrf else ""),
                                   "NSCP Table 208-11A"))
        cts = {round(_num(x["Ct"]), 4) for _, x in strength.iterrows() if "Ct" in x}
        for ct in sorted(cts):
            known = SEIS.ct.get(ct)
            out.append(Finding("Seismic", INFO if known else WARN,
                               f"Ct = {ct:g} (ft units) = {known[1]} in m: {known[0]}" if known
                               else f"Ct = {ct:g} is not an NSCP value (" + ", ".join(
                                   f"{k:g} {v[0]}" for k, v in SEIS.ct.items()) + ", in ft units)",
                               "NSCP 208.5.2.2 Eq. 208-12"))
        kinds = {str(x["PeriodType"]) for _, x in strength.iterrows()}
        out.append(Finding("Seismic", INFO, "Period: " + ", ".join(sorted(kinds)) +
                           f" (Program Calculated = Method B, capped at {SEIS.period_cap_zone4:g}"
                           f" T_A in zone 4, {SEIS.period_cap_other:g} T_A in zone 2)",
                           "NSCP 208.5.2.2"))
    # response spectrum function and cases (UBC 97 function = NSCP Fig. 208-3)
    function = d.table("Functions - Response Spectrum - UBC 97")
    if not function.empty and expected is not None:
        f = function.dropna(subset=["Ca"]).iloc[0] if function["Ca"].notna().any() else None
        if f is not None:
            fca, fcv = _num(f["Ca"]), _num(f["Cv"])
            match = abs(fca - expected[0]) < COEFF_TOLERANCE and \
                abs(fcv - expected[1]) < COEFF_TOLERANCE
            out.append(Finding("Seismic", OK if match else WARN,
                               f"Spectrum {f['Name']}: Ca {fca:g}, Cv {fcv:g}" +
                               ("" if match else f" (patterns: {expected[0]:.3f}, {expected[1]:.3f})"),
                               "NSCP 208.5.3.2 Fig. 208-3"))
    spectrum = d.table("Load Case Definitions - Response Spectrum")
    if spectrum.empty:
        out.append(Finding("Seismic", WARN, "No response spectrum cases (dynamic analysis is "
                           "required for irregular or tall buildings)", "NSCP 208.4.8.3"))
    else:
        no_ecc = [str(r["Name"]) for _, r in spectrum.iterrows() if _num(r.get("EccenRatio"), 0) <= 0]
        out.append(Finding("Seismic", WARN if no_ecc else OK,
                           "Response spectrum cases without eccentricity: " + ", ".join(no_ecc)
                           if no_ecc else "Response spectrum cases have accidental eccentricity.",
                           "NSCP 208.5.3.5.6"))
    if names:
        ground = elevations[names.index(bottom)] if bottom in names else elevations[0]
        height = elevations[-1] - ground
        tall = height >= SEIS.dynamic_height_mm
        out.append(Finding("Seismic", WARN if tall else INFO,
                           f"Height above the ground hn = {height / 1000:.1f} m" +
                           (f" ({SEIS.dynamic_height_mm / 1000:g} m or more: dynamic procedure "
                            "required)" if tall else
                            " (static procedure allowed if regular; irregular over 5 stories or "
                            "20 m needs the dynamic procedure)"), "NSCP 208.4.8"))
    # results
    if not d.analysed:
        out.append(Finding("Seismic", NA, "Base shear coefficient, period cap, response "
                           "spectrum scaling, modal mass and drift: not analysed (run sdt analyze)."))
        return out
    out += _check_static_results(d, seismic, bottom)
    from etabs_api.workflows.model_setup import DRIFT_SUFFIX

    shears = d.base_shear
    statics = [p for p, t in d.pattern_types.items() if t == SEISMIC and p in shears]
    drift_statics = [p for p, t in d.pattern_types.items() if t == SEISMIC_DRIFT and p in shears]
    for case, values in shears.items():
        if case in d.pattern_types or spectrum.empty or case not in set(spectrum["Name"].astype(str)):
            continue
        # a drift spectrum case (RSAXD) is compared with the drift patterns
        own = drift_statics if case.upper().endswith(DRIFT_SUFFIX) and drift_statics else statics
        axis = 0 if abs(values[0]) >= abs(values[1]) else 1
        static = max((abs(shears[p][axis]) for p in own
                      if abs(shears[p][axis]) >= abs(shears[p][1 - axis])), default=0.0)
        if static <= 0:
            continue
        ratio = abs(values[axis]) / static
        regular, irregular = SEIS.scaling_regular, SEIS.scaling_irregular
        status = OK if ratio >= SHEAR_OK * irregular else (WARN if ratio >= regular else FAIL)
        out.append(Finding("Seismic", status,
                           f"{case}: base shear {abs(values[axis]) / 1e3:,.0f} kN = "
                           f"{ratio * 100:.0f} % of the static {static / 1e3:,.0f} kN" +
                           ("" if status == OK else f" ({regular * 100:g} % is enough only for a "
                            f"regular structure, {irregular * 100:g} % if irregular; sdt analyze "
                            f"scales it to {irregular * 100:g} %)" if status == WARN else
                            f" (below {regular * 100:g} %: scale it, sdt analyze)"),
                           "NSCP 208.5.3.5.4"))
    if not d.modal.empty:
        for axis in ("UX", "UY"):
            column = f"Sum{axis}"
            if column in d.modal:
                total = _num(d.modal[column].iloc[-1], 0)
                enough = total >= SEIS.modal_mass
                out.append(Finding("Seismic", OK if enough else FAIL,
                                   f"Modal mass {axis}: {total * 100:.1f} %"
                                   + ("" if enough else f" (below {SEIS.modal_mass * 100:g} %: add "
                                      "modes)"), "NSCP 208.5.3.5.2"))
    out += _check_drift(d, seismic)
    return out


def _check_static_results(d: ModelData, seismic: pd.DataFrame, bottom: str) -> list[Finding]:
    """The base shear coefficient and period ETABS used, against NSCP 208.5.2."""
    out = []
    if "CoeffUsed" not in seismic:
        return out
    names = [n for n, _ in d.stories]
    elevations = [z for _, z in d.stories]
    ground = elevations[names.index(bottom)] if bottom in names else (elevations or [0])[0]
    height = (elevations[-1] - ground) if elevations else 0.0
    # W: the seismic weight of the stories above the ground level (208.6.1)
    above = names[names.index(bottom) + 1:] if bottom in names else names[1:]
    total = sum(d.story_weights.get(n, 0.0) for n in above)
    checked_weight = False
    for _, r in seismic.iterrows():
        name = str(r["Name"])
        parent = name.split("(")[0]
        kind = d.pattern_types.get(parent)
        period, used = _num(r.get("TUsed")), _num(r.get("CoeffUsed"))
        if kind not in (SEISMIC, SEISMIC_DRIFT) or period != period or used != used:
            continue
        weight = _num(r.get("WeightUsed"))
        if total > 0 and weight == weight and not checked_weight:
            checked_weight = True
            good = abs(weight / total - 1.0) <= WEIGHT_TOLERANCE
            out.append(Finding("Seismic", OK if good else FAIL,
                               f"W used by the static patterns {weight / 1e3:,.0f} kN" +
                               (f" = the stories above {bottom}." if good else
                                f", but the stories above {bottom} weigh {total / 1e3:,.0f} kN "
                                f"(check the story range: {r['BotStory']} to {r['TopStory']})"),
                               "NSCP 208.5.2.1, 208.6.1"))
        drift = kind == SEISMIC_DRIFT
        z, nv = _num(r.get("Z")), _num(r.get("Nv"), 1.0)  # no Z when Ca, Cv are typed
        want = nscp_coefficient(_num(r["Ca"]), _num(r["Cv"]), _num(r["I"]), _num(r["R"]),
                                period, z, nv, drift)
        good = abs(used - want) <= COEFF_TOLERANCE * want
        out.append(Finding("Seismic", OK if good else FAIL,
                           f"{name}: V/W {used:.4f} at T {period:.3f} s" +
                           ("" if good else f", NSCP gives {want:.4f}") +
                           (" (drift: no lower limit)" if drift else ""),
                           "NSCP 208.6.5.2" if drift else "NSCP 208.5.2.1 Eq. 208-8 to 208-11"))
        ct = _num(r.get("Ct"))
        if not drift and height > 0 and ct == ct:
            t_a = method_a_period(ct, height)
            # zone 4 when the zone is unknown (user-defined Ca, Cv): the stricter cap
            cap = (SEIS.period_cap_zone4 if z >= SEIS.zone4_factor or z != z
                   else SEIS.period_cap_other) * t_a
            out.append(Finding("Seismic", OK if period <= cap * (1 + CAP_TOLERANCE) else FAIL,
                               f"{name}: T {period:.3f} s, T_A {t_a:.3f} s, cap {cap:.3f} s",
                               "NSCP 208.5.2.2"))
    return out


def _worst_drifts(d: ModelData, wind: bool) -> dict[str, tuple[float, str, str]]:
    """The largest drift of each load case (EQXSD, RSAXD, WX ...): (ratio, story, source).

    The source is the combination (``DRIFT ... EQXSD``, ``WDRIFT ... WX``), or
    the case itself for models without drift combinations.
    """
    from etabs_api.workflows.load_combinations import DRIFT_SET, WIND_DRIFT_SET

    prefix = (WIND_DRIFT_SET if wind else DRIFT_SET) + " "
    wind_cases = {p for p, t in d.pattern_types.items() if t == WIND}
    out: dict[str, tuple[float, str, str]] = {}
    for name, (ratio, story) in d.drifts.items():
        if name in d.drift_case_of:  # a combination the user picked
            case, is_wind = d.drift_case_of[name]
            if is_wind != wind:
                continue
        elif d.drift_from == "combinations":
            if not name.startswith(prefix):
                continue
            case = name.split()[-1]
        else:
            case = name
            if (case in wind_cases) != wind:
                continue
        if case not in out or ratio > out[case][0]:
            out[case] = (ratio, story, name)
    return out


def _check_drift(d: ModelData, seismic: pd.DataFrame) -> list[Finding]:
    """NSCP 208.6.4 and 208.6.5: Delta_M = 0.7 R Delta_S against 0.025 or 0.020 h,
    Delta_S from the 203.3 combinations with rho = 1.0 (values from NSCP.seismic)."""
    if not d.drifts:
        return [Finding("Seismic", NA, "Story drifts were not read.", "NSCP 208.6.5")]
    out = []
    if d.drift_from == "cases":
        out.append(Finding("Seismic", WARN, "No combination is named DRIFT: the drift here is "
                           "from the load cases alone. sdt drift asks which of the model's "
                           "combinations to check the drift on.", "NSCP 208.6.4.1"))
    known = not seismic.empty and "Name" in seismic.columns
    if not known:  # no UBC 97 seismic pattern: R is the user's, the period is not known
        seismic = pd.DataFrame({"Name": pd.Series(dtype=str)})
    parents = seismic["Name"].astype(str).str.split("(").str[0]
    drift_rows = seismic[parents.map(lambda n: d.pattern_types.get(n) == SEISMIC_DRIFT)]
    for case, (ratio, story, source) in sorted(_worst_drifts(d, wind=False).items()):
        # a static case has its own row; a response spectrum case takes the
        # period of the drift patterns (the uncapped one, 208.6.5.2)
        rows = seismic[parents == case]
        rows = rows if not rows.empty else (drift_rows if not drift_rows.empty else seismic)
        fallback = d.r_factor if d.r_factor else SEIS.smrf_r
        r_factor = _num(rows["R"].iloc[0], fallback) if known and len(rows) else fallback
        periods = pd.to_numeric(rows.get("TUsed"), errors="coerce").dropna() \
            if known else pd.Series(dtype=float)
        period = float(periods.max()) if len(periods) else math.nan
        limit = drift_limit(period) if period == period else SEIS.drift_limit_long
        inelastic = SEIS.drift_amplification * r_factor * ratio
        out.append(Finding("Seismic", OK if inelastic <= limit else FAIL,
                           f"{case}: drift {ratio:.5f} at {story}, Delta_M = "
                           f"{SEIS.drift_amplification:g} R x = {inelastic:.4f} (limit "
                           f"{limit:.3f}" + (f", T {period:.2f} s" if period == period else "")
                           + (f"; {source}" if source != case else "") + ")",
                           "NSCP 208.6.4.1, 208.6.5.1"))
    return out


def check_wind_drift(d: ModelData) -> list[Finding]:
    """Wind drift of the WDRIFT combinations against h / the typed limit."""
    out = []
    limit = 1.0 / d.wind_drift_denominator
    for case, (ratio, story, source) in sorted(_worst_drifts(d, wind=True).items()):
        out.append(Finding("Wind", OK if ratio <= limit else FAIL,
                           f"{case}: drift {ratio:.5f} = h/{1 / ratio:,.0f} at {story} (limit "
                           f"h/{d.wind_drift_denominator:g}"
                           + (f"; {source}" if source != case else "") + ")",
                           "NSCP 203.3 combinations; limit: project (NSCP 207 sets none)"))
    return out


def check_wind(d: ModelData) -> list[Finding]:
    from etabs_api.workflows.model_setup import lateral_story_range

    wind = d.table("Load Pattern Definitions - Auto Wind - ASCE 7-10")
    if wind.empty:
        return [Finding("Wind", WARN, "No ASCE 7-10 wind load patterns.", "NSCP 207")]
    names = [n for n, _ in d.stories]
    bottom, top = lateral_story_range(names, [z for _, z in d.stories]) if names else ("", "")
    rows = wind[wind["BotStory"].notna() & (wind["BotStory"].astype(str) != "None")]
    wrong = [f"{r['Name']} {r['BotStory']}-{r['TopStory']}" for _, r in rows.iterrows()
             if (str(r["BotStory"]), str(r["TopStory"])) != (bottom, top)
             and "(" not in str(r["Name"])]
    out = [Finding("Wind", FAIL if wrong else OK,
                   f"Story range should be {bottom} to {top}: " + ", ".join(wrong) if wrong
                   else f"Every wind pattern covers {bottom} to {top}.", "NSCP 207")]
    out += check_wind_drift(d)
    for _, r in rows[~rows["Name"].astype(str).str.contains(r"\(", regex=True)].iterrows():
        out.append(Finding("Wind", INFO, f"{r['Name']}: speed {r.get('WindSpeed')} (mph = "
                           f"{_num(r.get('WindSpeed'), 0) * 1.609:.0f} km/h), exposure "
                           f"{r.get('ExpType')}, Kzt {r.get('kzt')}, G {r.get('GustFact')}, Kd "
                           f"{r.get('Kd')} - confirm against the site's wind zone",
                           "NSCP 207.5 Fig. 207A.5-1"))
    return out


def check_combinations(d: ModelData) -> list[Finding]:
    combos = d.table("Load Combination Definitions")
    if combos.empty:
        return [Finding("Combinations", FAIL, "No load combinations.", "NSCP 203")]
    names = list(dict.fromkeys(combos["Name"].dropna().astype(str)))
    cases = set(d.table("Load Case Definitions - Summary").get("Name", pd.Series(dtype=str))
                .astype(str))
    out = []
    uls = [n for n in names if n.startswith("ULS")]
    if uls:
        out.append(Finding("Combinations", OK, f"{len(uls)} ULS strength combinations.",
                           "NSCP 203.3"))
    elif names:
        # a model with its own names: the design commands ask which to design for
        out.append(Finding("Combinations", WARN, f"None of the {len(set(names))} combinations "
                           "is named ULS: the design commands ask which ones to design for, and "
                           "their factors are then yours to confirm.", "NSCP 203.3"))
    else:
        out.append(Finding("Combinations", FAIL, "The model has no load combinations.",
                           "NSCP 203.3"))
    defl = [n for n in names if n.startswith("DEF")]
    out.append(Finding("Combinations", OK if len(defl) >= 3 else WARN,
                       f"{len(defl)} deflection combinations." if defl else
                       "No DEF deflection combinations (sdt beams asks which of the "
                       "model's combinations to use, or adds them)."))
    missing = sorted({str(load) for load in combos["LoadName"].dropna().astype(str)
                      if load not in cases and load not in set(names)})
    out.append(Finding("Combinations", FAIL if missing else OK,
                       "Combinations refer to missing cases: " + ", ".join(missing[:10])
                       if missing else "Every combination refers to existing cases."))
    out += _check_vertical_effect(d, combos, uls)
    return out


def _check_vertical_effect(d: ModelData, combos: pd.DataFrame, uls: list[str]) -> list[Finding]:
    """NSCP 208.6.1: E = rho Eh + Ev with Ev = 0.5 Ca I D, so the dead load of the
    seismic strength combinations is (1.2 + Ev) D (203-5) and (0.9 - Ev) D (203-7)."""
    if "SF" not in combos:
        return []
    seismic = d.table("Load Pattern Definitions - Auto Seismic - UBC 97")
    strength = seismic[seismic["Name"].astype(str).map(
        lambda n: d.pattern_types.get(n) == SEISMIC)] if "Name" in seismic else seismic
    if strength.empty:
        return []
    ca = _num(strength["Ca"].iloc[0])
    first = strength.iloc[0]
    if str(first.get("CoeffOpt", "")).startswith("Per Code") and not d.analysed:
        # the table holds placeholders until analysis: use the code value
        from etabs_api.workflows.ubc97 import seismic_coefficients

        try:
            ca = seismic_coefficients(_num(first.get("Z")), str(first.get("SoilType")),
                                      str(first.get("SourceType")),
                                      _num(first.get("SourceDist")))[0]
        except (ValueError, KeyError, TypeError):
            pass
    ev = LOADS.vertical_effect * ca * _num(strength["I"].iloc[0], 1.0)
    if ev != ev:
        return []
    spectrum = set(d.table("Load Case Definitions - Response Spectrum").get(
        "Name", pd.Series(dtype=str)).astype(str))
    quake = {p for p, t in d.pattern_types.items() if t == SEISMIC} | spectrum
    members: dict[str, list[tuple[str, float]]] = {}
    names = combos["Name"].replace({"": pd.NA}).ffill().astype(str)
    for name, load, sf in zip(names, combos["LoadName"].astype(str), combos["SF"]):
        members.setdefault(name, []).append((load, _num(sf, 0.0)))

    def has_quake(name: str, seen: frozenset = frozenset()) -> bool:
        if name in quake:
            return True
        if name in seen or name not in members:
            return False
        return any(has_quake(load, seen | {name}) for load, _ in members[name])

    dead = {p for p, t in d.pattern_types.items() if t in (DEAD, SUPER_DEAD)}
    wanted = (LOADS.dead + ev, LOADS.dead_minimum - ev)
    odd = []
    for name in uls:
        if not has_quake(name):
            continue
        factors = {round(sf, 3) for load, sf in members.get(name, []) if load in dead}
        if any(min(abs(f - w) for w in wanted) > COEFF_TOLERANCE for f in factors):
            odd.append(f"{name} ({', '.join(f'{f:g}' for f in sorted(factors))} D)")
    return [Finding("Combinations", WARN if odd else OK,
                    f"Seismic strength combinations without Ev = {LOADS.vertical_effect:g} Ca I D"
                    f" = {ev:.3f} D on the dead load (want {wanted[0]:.3f} or {wanted[1]:.3f}; "
                    f"{LOADS.dead:g} / {LOADS.dead_minimum:g} is right only for the Omega0 "
                    "combinations 203-19, 203-20): " + ", ".join(odd[:6])
                    if odd else f"Seismic strength combinations carry Ev: {wanted[0]:.3f} D and "
                    f"{wanted[1]:.3f} D.", "NSCP 208.6.1, 203.3.1")]


def check_analysis(d: ModelData) -> list[Finding]:
    out = []
    static = d.table("Load Case Definitions - Linear Static")
    if "StiffType" in static:
        other = sorted(set(static.loc[static["StiffType"].astype(str) != "P-Delta", "Name"]
                           .astype(str)))
        out.append(Finding("Analysis", WARN if other else OK,
                           "Load cases not on preset P-Delta: " + ", ".join(other[:10]) if other
                           else "Every linear static case uses preset P-Delta."))
    pdelta = d.table("P-Delta Option Definition")
    method = str(pdelta["AutoMethod"].dropna().iloc[0]) if "AutoMethod" in pdelta and \
        pdelta["AutoMethod"].notna().any() else "None"
    out.append(Finding("Analysis", OK if "Iterative" in method or "Non-iterative" in method
                       else WARN, f"P-delta: {method}", "NSCP 208.6.3"))
    mass = d.table("Mass Source Definition")
    if not mass.empty:
        first = mass.iloc[0]
        loads = set(mass["LoadPattern"].dropna().astype(str)) if "LoadPattern" in mass else set()
        own = str(first.get("SourceSelf")) == "Yes"
        double = own and any(d.self_weight.get(p) for p in loads)
        out.append(Finding("Analysis", FAIL if double else OK,
                           "Mass source counts the self weight twice (element self mass and "
                           "the self-weight pattern)" if double else
                           f"Mass source: {', '.join(sorted(loads)) or 'element mass'} (W: all "
                           f"dead load, {MODEL.storage_live_mass * 100:g} % of storage live, "
                           f"partitions at least {MODEL.partition_mass_kpa:g} kPa)",
                           "NSCP 208.6.1"))
    out += _check_modifiers(d)
    offsets = d.table("Frame Assignments - End Length Offsets")
    if "RigidFact" in offsets:
        factors = sorted({_num(x, 0) for x in offsets["RigidFact"]})
        out.append(Finding("Analysis", INFO, "Rigid end zone factor: " +
                           ", ".join(f"{x:g}" for x in factors) +
                           f" ({MODEL.rigid_zone_typical:g} is common for SMRF)",
                           "NSCP 406.6.2.3(b)"))
    return out


MODIFIER_FIELDS = ("AMod", "A2Mod", "A3Mod", "JMod", "I2Mod", "I3Mod", "MMod", "WMod")


def effective_modifiers(d: ModelData) -> dict[str, tuple]:
    """Each frame's modifiers as ETABS uses them: object x section, (A ... weight)."""
    sections = d.table("Frame Assignments - Section Properties")
    if sections.empty:
        return {}
    objects = {}
    table = d.table("Frame Assignments - Property Modifiers")
    if not table.empty and "I3Mod" in table:
        for _, r in table.iterrows():
            objects[str(r["UniqueName"])] = tuple(_num(r.get(f), 1.0) for f in MODIFIER_FIELDS)
    out = {}
    for member, prop in zip(sections["UniqueName"].astype(str), sections["SectProp"].astype(str)):
        own = objects.get(member, (1.0,) * 8)
        section = d.section_modifiers.get(prop, (1.0,) * 8)
        out[member] = tuple(a * b for a, b in zip(own, section))
    return out


def _check_modifiers(d: ModelData) -> list[Finding]:
    effective = effective_modifiers(d)
    columns = set(d.table("Column Object Connectivity").get("UniqueName", pd.Series(dtype=str))
                  .astype(str))
    return modifier_findings(effective, columns)


def modifier_findings(effective: dict[str, tuple], columns: set[str],
                      group: str = "Analysis") -> list[Finding]:
    """Cracked-section I (NSCP 208.6.2) and mass / weight modifiers, as ETABS combines
    them: the frame object modifier times the section modifier."""
    if not effective:
        return []
    odd, mass = {}, []
    for member, values in effective.items():
        want = MODEL.column_inertia if member in columns else MODEL.beam_inertia
        i3 = values[5]
        if abs(i3 - want) > MODEL.modifier_tolerance + 1e-3:
            odd.setdefault(("column" if member in columns else "beam", round(i3, 3)), []).append(
                member)
        if abs(values[6] - 1.0) > 1e-6 or abs(values[7] - 1.0) > 1e-6:
            mass.append(f"{member} (mass {values[6]:g}, weight {values[7]:g})")
    out = [Finding(group, WARN if odd else OK,
                   "Effective I (frame modifier x section modifier) other than "
                   f"{MODEL.beam_inertia:g} (beams) / {MODEL.column_inertia:g} (columns): " +
                   "; ".join(f"{len(m)} {k} at {v:g} ({', '.join(m[:3])})"
                             for (k, v), m in sorted(odd.items())) +
                   " (a modifier on both the frames and the sections is applied twice)"
                   if odd else f"Effective I: beams {MODEL.beam_inertia:g}, columns "
                   f"{MODEL.column_inertia:g}.", "NSCP 208.6.2 item 1, 406.6.3.1.1")]
    if mass:
        out.append(Finding(group, FAIL, f"{len(mass)} frames with a mass or weight "
                           f"modifier other than 1 (the seismic weight W is reduced): "
                           f"{', '.join(mass[:4])}", "NSCP 208.6.1"))
    return out


def check_smrf_members(d: ModelData) -> list[Finding]:
    from etabs_api.workflows.sections import parse_section

    sections = d.table("Frame Assignments - Section Properties")
    if sections.empty:
        return []
    columns = set(d.table("Column Object Connectivity").get("UniqueName", pd.Series(dtype=str))
                  .astype(str))
    lengths = dict(zip(d.table("Beam Object Connectivity").get("UniqueName", pd.Series(dtype=str))
                       .astype(str),
                       pd.to_numeric(d.table("Beam Object Connectivity").get("Length",
                                                                             pd.Series(dtype=float)),
                                     errors="coerce")))
    narrow, short, small_col, slender_col = [], [], [], []
    for member, prop in zip(sections["UniqueName"].astype(str), sections["SectProp"].astype(str)):
        s = parse_section(prop)
        if s is None:
            continue
        if member in columns:
            least = min(s.width, s.depth)
            if least < SMRF_COLUMN.min_dimension:
                small_col.append(member)
            if not s.circular and least / max(s.width, s.depth) < SMRF_COLUMN.min_aspect_ratio:
                slender_col.append(member)
        elif s.family in ("G",):
            if s.width < min(SMRF_BEAM.min_width_to_depth * s.depth, SMRF_BEAM.min_width) - 0.5:
                narrow.append(member)
            length = lengths.get(member, math.nan)
            if length == length and \
                    length < SMRF_BEAM.min_clear_span_to_depth * (s.depth - COVER_TO_BAR):
                short.append(member)
    ref, group = "NSCP ", "SMRF"
    span_ratio = SMRF_BEAM.min_clear_span_to_depth
    least_side, side_ratio = SMRF_COLUMN.min_dimension, SMRF_COLUMN.min_aspect_ratio
    out = [
        Finding(group, WARN if narrow else OK, f"Girders narrower than the smaller of "
                f"{SMRF_BEAM.min_width_to_depth:g}h and {SMRF_BEAM.min_width:g} mm: "
                f"{', '.join(narrow[:8])}" if narrow else f"Girder widths at least the smaller of "
                f"{SMRF_BEAM.min_width_to_depth:g}h and {SMRF_BEAM.min_width:g} mm.",
                ref + "418.6.2.1(b)"),
        Finding(group, WARN if short else OK, f"Girders with a span under "
                f"{span_ratio:g}d (deep beams): {', '.join(short[:8])}" if short else
                f"Girder spans at least {span_ratio:g}d.", ref + "418.6.2.1(a)"),
        Finding(group, WARN if small_col else OK, f"Columns with a side under {least_side:g} mm: "
                f"{', '.join(small_col[:8])}" if small_col else
                f"Column sides at least {least_side:g} mm.", ref + "418.7.2.1(a)"),
        Finding(group, WARN if slender_col else OK, f"Columns with a side ratio under "
                f"{side_ratio:g}: {', '.join(slender_col[:8])}" if slender_col else
                f"Column side ratio at least {side_ratio:g}.", ref + "418.7.2.1(b)"),
    ]
    min_fc, max_fy = SMRF_MATERIAL.min_fc, SMRF_MATERIAL.max_fy
    concrete = d.table("Material Properties - Concrete Data")
    used = {s.concrete for s in (parse_section(p) for p in sections["SectProp"].astype(str)) if s}
    if "Fc" in concrete:
        weak = [f"{m} {(_num(f)):.1f} MPa" for m, f in zip(concrete["Material"].astype(str),
                                                         concrete["Fc"])
                if m in used and _num(f, 99) < min_fc - 0.05]
        out.append(Finding(group, FAIL if weak else OK, f"Concrete below {min_fc:g} MPa: " +
                           ", ".join(weak) if weak else
                           f"Concrete of the frames at least {min_fc:g} MPa.",
                           ref + "418.2.5.1, Table 419.2.1.1"))
    rebar = d.table("Material Properties - Rebar Data")
    used_rebar = {s.rebar for s in (parse_section(p) for p in sections["SectProp"].astype(str)) if s}
    if "Fy" in rebar:
        strong = [f"{m} {_num(f):.0f} MPa" for m, f in zip(rebar["Material"].astype(str), rebar["Fy"])
                  if m in used_rebar and _num(f, 0) > max_fy + 1]
        out.append(Finding(group, FAIL if strong else OK, f"Longitudinal rebar above {max_fy:g} "
                           "MPa: " + ", ".join(strong) if strong else
                           f"Rebar of the frames at most {max_fy:g} MPa (A706, or A615 with the "
                           "420.2.2.5 supplements).", ref + "418.2.6.1, 420.2.2.5"))
    return out


def run_checks(d: ModelData) -> list[Finding]:
    out = []
    for check in (check_model, check_loads, check_seismic, check_wind, check_combinations,
                  check_analysis, check_smrf_members):
        try:
            out += check(d)
        except Exception as error:  # one broken check must not hide the others
            out.append(Finding(check.__name__.replace("check_", "").title(), WARN,
                               f"Check could not run: {error}"))
    return out


def report_text(d: ModelData, findings: list[Finding]) -> str:
    lines = ["", "=" * 96, "MODEL CHECK", "=" * 96, f"Model: {d.path}",
             f"Analysis results: {'yes' if d.analysed else 'no (results checks are N/A)'}"]
    group = None
    for f in findings:
        if f.group != group:
            group = f.group
            lines += ["", f"--- {group} ---"]
        ref = f"   ({f.ref})" if f.ref else ""
        lines.append(f"[{f.status:>4}] {f.text}{ref}")
    counts = {s: sum(f.status == s for f in findings) for s in (FAIL, WARN, INFO, OK, NA)}
    lines += ["", "Summary: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n), "=" * 96]
    return "\n".join(lines)


# =============================================================================
# FROM ETABS (read only)
# =============================================================================
TABLES = (
    "Frame Assignments - Section Properties", "Frame Assignments - Property Modifiers",
    "Frame Assignments - End Length Offsets", "Floor Object Connectivity",
    "Area Assignments - Summary", "Point Object Connectivity", "Column Object Connectivity",
    "Beam Object Connectivity", "Joint Assignments - Restraints",
    "Area Load Assignments - Uniform", "Shell Uniform Load Sets",
    "Area Load Assignments - Uniform Load Sets", "Frame Loads Assignments - Distributed",
    "Frame Loads Assignments - Point", "Joint Loads Assignments - Force",
    "Load Pattern Definitions - Auto Seismic - UBC 97",
    "Load Pattern Definitions - Auto Wind - ASCE 7-10",
    "Functions - Response Spectrum - UBC 97", "Load Case Definitions - Response Spectrum",
    "Load Case Definitions - Linear Static", "Load Case Definitions - Summary",
    "Load Combination Definitions", "P-Delta Option Definition", "Mass Source Definition",
    "Material Properties - Concrete Data", "Material Properties - Rebar Data",
)


def _has_results(connector) -> bool:
    """True when any load case has finished results (a saved model can have results
    without being locked)."""
    model = connector.sap_model
    if model.GetModelIsLocked():
        return True
    try:
        return any(row["status"] == "Finished" for row in connector.analysis.status())
    except Exception:
        return False


def read_model_data(connector, progress=None) -> ModelData:
    """Every table the checks need, without changing the model."""
    say = progress or (lambda text: None)
    model = connector.sap_model
    say("Reading the load patterns and stories")
    tables = model.DatabaseTables
    available = {str(t) for t in as_list(tables.GetAvailableTables()[1])}
    patterns = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    d = ModelData(path=str(model.GetModelFilename()), analysed=_has_results(connector))
    d.pattern_types = {n: int(model.LoadPatterns.GetLoadType(n)[0]) for n in patterns}
    d.self_weight = {n: float(model.LoadPatterns.GetSelfWTMultiplier(n)[0]) for n in patterns}
    stories = model.Story.GetStories()
    d.stories = list(zip([str(n) for n in as_list(stories[1])],
                         [float(z) for z in as_list(stories[2])]))
    tables.SetLoadPatternsSelectedForDisplay(patterns)
    tables.SetLoadCasesSelectedForDisplay([])
    tables.SetLoadCombinationsSelectedForDisplay([])
    for number, name in enumerate(TABLES, start=1):
        if name in available:
            say(f"Reading table {number} of {len(TABLES)}\n{name}")
            try:
                d.tables[name] = connector._read_database_table(name)
            except Exception:
                pass
    used = d.table("Frame Assignments - Section Properties").get("SectProp", pd.Series(dtype=str))
    say("Reading the section property modifiers")
    for prop in sorted({str(p) for p in used}):
        try:
            d.section_modifiers[prop] = tuple(float(v) for v in
                                              model.PropFrame.GetModifiers(prop, [])[0])
        except Exception:
            pass
    if d.analysed:
        from etabs_api.workflows.model_analysis import base_shears, modal_periods

        cases = [str(n) for n in d.table("Load Case Definitions - Summary").get("Name", [])]
        say("Analysis results: base shears")
        try:
            shears = base_shears(connector, cases)
            d.base_shear = {str(c): (float(r["FX"]), float(r["FY"])) for c, r in shears.iterrows()}
        except Exception:
            pass
        say("Analysis results: modal periods and mass")
        try:
            d.modal = modal_periods(connector)
        except Exception:
            pass
        say("Analysis results: story weights")
        try:
            from etabs_api.workflows.model_analysis import story_weights

            weights = story_weights(connector)
            d.story_weights = {str(s): float(w) * 1e3 for s, w in
                               zip(weights["Story"], weights["Weight (kN)"])}
        except Exception:
            pass
        say("Analysis results: story drifts")
        try:
            combos = drift_combinations(d)
            if combos:
                d.drifts, d.drift_from = story_drifts(connector, [], combos), "combinations"
            else:
                d.drifts, d.drift_from = story_drifts(connector, drift_cases(d), []), "cases"
        except Exception:
            pass
    return d


def drift_combinations(d: ModelData) -> list[str]:
    """The seismic (DRIFT) and wind (WDRIFT) drift combinations of the model."""
    from etabs_api.workflows.load_combinations import DRIFT_SET, WIND_DRIFT_SET

    names = d.table("Load Combination Definitions").get("Name", pd.Series(dtype=str))
    return list(dict.fromkeys(str(n) for n in names.dropna()
                              if str(n).startswith((DRIFT_SET + " ", WIND_DRIFT_SET + " "))))


def drift_cases(d: ModelData) -> list[str]:
    """Without drift combinations: the drift patterns (period not capped,
    208.6.5.2), the response spectrum drift cases (RSA..D) and the wind
    patterns, or, without drift patterns, the strength patterns."""
    from etabs_api.workflows.model_setup import DRIFT_SUFFIX

    drift = [p for p, t in d.pattern_types.items() if t == SEISMIC_DRIFT]
    if not drift:
        drift = [p for p, t in d.pattern_types.items() if t == SEISMIC]
    spectrum = d.table("Load Case Definitions - Response Spectrum").get("Name", pd.Series(dtype=str))
    drift += [str(n) for n in spectrum if str(n).upper().endswith(DRIFT_SUFFIX)]
    drift += [p for p, t in d.pattern_types.items() if t == WIND]
    return drift


def story_drifts(connector, cases: list[str], combos: list[str]) -> dict[str, tuple[float, str]]:
    """The largest story drift ratio of each case or combination and its story.

    Every story is included, those below the ground level too.
    """
    if not cases and not combos:
        return {}
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases))
    tables.SetLoadCombinationsSelectedForDisplay(list(combos))
    table = connector._read_database_table("Story Drifts")
    table = table[table["OutputCase"].astype(str).isin(set(cases) | set(combos))]
    out = {}
    for case, rows in table.groupby("OutputCase"):
        drift = pd.to_numeric(rows["Drift"], errors="coerce").abs()
        if drift.notna().any():
            at = drift.idxmax()
            out[str(case)] = (float(drift[at]), str(rows.loc[at, "Story"]))
    return out


def run_model_check() -> list[Finding] | None:
    """Entry point: check the model open in ETABS and print the results."""
    from design.concrete_workflow import attach_etabs
    from utilities._gui_helpers import LoadingWindow

    from utilities._gui_helpers import enter_values, show_warning

    connector = attach_etabs()
    if connector is None:
        return None
    label = "Wind drift limit: h /"
    typed = enter_values("Model Check", "Story drift limit under the wind combinations (NSCP "
                         "207 sets none; seismic drift follows NSCP 208.6.5).", [label],
                         {label: f"{NSCP.wind.drift_limit_denominator:g}"})
    if typed is None:
        return None
    try:
        denominator = float(typed[label])
        if denominator <= 0:
            raise ValueError
    except ValueError:
        show_warning("The wind drift limit must be a positive number.", title="Model Check")
        return None
    with LoadingWindow("Checking the model") as window:
        window.update("Reading the model (nothing is changed)")
        data = read_model_data(connector, progress=window.update)
        window.update("Running the checks")
        data.wind_drift_denominator = denominator
        findings = run_checks(data)
    print(report_text(data, findings))
    check_summary(findings, str(connector.sap_model.GetModelFilename())).show(popup=True, echo=False)
    return findings


def check_summary(findings: list[Finding], model_path: str | None = None):
    """The closing summary of a model check: the counts and every FAIL."""
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt check", model_path)
    for status in (OK, INFO, WARN, FAIL):
        summary.add(status, sum(1 for f in findings if f.status == status))
    not_run = sum(1 for f in findings if f.status not in (OK, INFO, WARN, FAIL))
    if not_run:
        summary.add("N/A (needs analysis results)", not_run)
    for finding in findings:
        if finding.status == FAIL:
            summary.fail(f"{finding.group}: {finding.text}")
    warnings = sum(1 for f in findings if f.status == WARN)
    if warnings:
        summary.note(f"{warnings} warnings are listed above with their clauses.")
    summary.note("The model was not changed.")
    return summary


if __name__ == "__main__":
    run_model_check()
