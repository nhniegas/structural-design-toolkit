"""
sdt compatibility: members not designated as part of the seismic-force-resisting
system (NSCP 2015 208.7.2.3 and 418.14; ACI 18.14).

The members the engineer designates are those of the ETABS group ``GRAVITY ONLY``.
When the group is missing or empty, the command proposes candidates (girders
with a clear span under 4d, cantilevers, planted columns, columns with no girder
in a direction, columns that take a minor share of the story shear) and can
create the group with them, for the engineer to review in ETABS.

The check:

1. The design displacement δu, story by story and in each direction: the
   story drift of the drift load case (EQXSD, EQYSD) with the designated
   members pinned at both ends (their end moments released), so that the
   frames and walls carry the earthquake alone, as NSCP 208.7.2.3 asks
   ("the stiffening effect of those elements ... shall be neglected"),
   amplified to ΔM = 0.7 R ΔS (208.6.4.2), and at least 0.0025 of the story
   height. The releases are put back afterwards.
2. The forces in each designated member at δu, taken in the model as built,
   with its real end fixity: the forces of the drift load case, scaled by
   (δu / the story drift of that case in the model as built). The analysis
   is linear, so this is the model pushed to δu.
3. Those induced forces with 1.2D + f L (f = 0.5 or 1.0, 418.14.2.1) and with
   0.9D, against the design strength φMn and φVn of the member as designed
   (the stored design of sdt beams / sdt columns / sdt design):
   within them, 418.14.3.2 applies; over them, 418.14.3.3 (the special moment
   frame rules).
4. The detailing each clause asks, checked where the design holds it.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.criteria.code_config import CODE, NSCP  # noqa: E402
from etabs_api.core.helpers import as_list, return_code  # noqa: E402

GROUP = "GRAVITY ONLY"
MIN_DRIFT_RATIO = 0.0025           # NSCP 208.7.2.3: at least 0.0025 times the story height
AMPLIFICATION = NSCP.seismic.drift_amplification   # ΔM = 0.7 R ΔS (208.6.4.2)
PHI_FLEXURE, PHI_SHEAR = 0.90, 0.75
MINOR_SHARE = 0.5                  # a column under half the story's average share is proposed
CLAUSE_WITHIN = "NSCP 418.14.3.2 (ACI 18.14.3.2)"
CLAUSE_OVER = "NSCP 418.14.3.3 (ACI 18.14.3.3)"
DEAD_TYPES, LIVE_TYPES = (1, 2), (3, 4)   # ETABS pattern types: dead, super dead; live, reducible
# the tie spacing the column schedule shows over the middle of a column
NSCP_GENERAL_TIE_SPACING = float(CODE.drawing.column_general_tie_spacing)


# =============================================================================
# STRENGTHS OF THE MEMBERS AS DESIGNED
# =============================================================================
def beam_strength(rows: pd.DataFrame) -> dict:
    """φMn (top, bottom) and φVn at the supports of a beam, from its designed bars
    (the two rows of the stored beam results, TOP and BOTTOM). Singly reinforced
    flexure on the tension bars, Vc + Vs with the stirrups of the support zone."""
    first = rows.iloc[0]
    b, h = float(first["Width"]), float(first["Depth"])
    fc, fy = float(first["f'c"]), float(first["fy"])
    fyt = float(first.get("fyw") or fy)
    dm, ds = float(first["dm"]), float(first["ds"])
    out = {"b": b, "h": h, "fc": fc}
    for face in ("TOP", "BOTTOM"):
        row = rows[rows["Face"].astype(str).str.upper() == face]
        if row.empty:
            out[f"phi_Mn_{face.lower()}"] = 0.0
            continue
        r = row.iloc[0]
        cover = float(r["TopCover"] if face == "TOP" else r["BotCover"])
        d = h - cover
        count = max(sum(int(r.get(f"n_{end}_L{n}", 0) or 0) for n in (1, 2, 3))
                    for end in ("left", "right"))
        area = count * math.pi * dm ** 2 / 4.0
        a = area * fy / (0.85 * fc * b)
        out[f"phi_Mn_{face.lower()}"] = PHI_FLEXURE * area * fy * (d - a / 2.0) / 1e6
        out[f"d_{face.lower()}"] = d
    d = min(out.get("d_top", h), out.get("d_bottom", h))
    out["d"] = d
    legs = int(first.get("Stirrup_Legs") or 2)
    spacing = float(first.get("Spacing_2H") or first.get("Spacing_Mid") or 0.0)
    root = math.sqrt(fc)
    vc = 0.0 if bool(first.get("Vc_zero_left")) or bool(first.get("Vc_zero_right")) \
        else 0.17 * root * b * d
    vs = legs * math.pi * ds ** 2 / 4.0 * fyt * d / spacing if spacing > 0 else 0.0
    vs = min(vs, 0.66 * root * b * d)
    out["phi_Vn"] = PHI_SHEAR * (vc + vs) / 1000.0
    out["stirrup_spacing_mid"] = float(first.get("Spacing_Mid") or spacing or 0.0)
    out["stirrup_spacing_end"] = spacing
    return out


def column_strength(rows: pd.DataFrame, dmain: float) -> dict:
    """The smallest φMn and φVn of the column's design rows, its Ag, Ast and Po."""
    first = rows.iloc[0]
    diameter = float(first.get("Diameter_mm") or 0.0)
    if diameter > 0:
        ag = math.pi * diameter ** 2 / 4.0
    else:
        ag = float(first["Width_mm"]) * float(first["Depth_mm"])
    bars = pd.to_numeric(rows.get("Longitudinal_Bars"), errors="coerce").max()
    ast = (0.0 if bars != bars else float(bars)) * math.pi * dmain ** 2 / 4.0
    fc = float(pd.to_numeric(rows.get("f'c (MPa)", rows.get("fc_MPa")), errors="coerce").max()) \
        if ("f'c (MPa)" in rows or "fc_MPa" in rows) else math.nan
    return {
        "phi_Mn": float(pd.to_numeric(rows["phi_Mn_kNm"], errors="coerce").min()),
        "phi_Vn": float(pd.to_numeric(rows["phi_Vn_kN"], errors="coerce").min()),
        "ag": ag, "ast": ast, "fc": fc,
        "spacing": float(pd.to_numeric(rows.get("Transverse_Spacing_mm"),
                                       errors="coerce").min()),
    }


# =============================================================================
# THE DESIGN DISPLACEMENT AND THE INDUCED FORCES
# =============================================================================
def design_displacement(real: dict[tuple[str, str], float],
                        pinned: dict[tuple[str, str], float],
                        r_factor: float) -> dict[tuple[str, str], tuple[float, float, float]]:
    """(story, direction) -> (δu drift ratio, the drift ratio as built, the factor).

    δu is ΔM = 0.7 R ΔS of the frames alone (``pinned``), at least 0.0025 h
    (208.7.2.3). The factor scales the forces of the model as built to δu.
    """
    out = {}
    for key, ratio in pinned.items():
        delta_u = max(AMPLIFICATION * r_factor * ratio, MIN_DRIFT_RATIO)
        built = real.get(key)
        if built and built > 0:
            out[key] = (delta_u, built, delta_u / built)
    return out


def induced_forces(forces: pd.DataFrame, cases: dict[str, str], factors: dict,
                   dead: list[str], live: list[str], live_factor: float,
                   story_of: dict[str, str]) -> pd.DataFrame:
    """For every member: the largest moment and shear at δu with the gravity load.

    ``forces`` are the frame forces of the load cases (frame_force_arrays),
    ``cases`` maps each drift load case to its direction (X or Y), ``factors``
    is ``design_displacement``. Two gravity combinations (418.14.2.1):
    1.2D + f L and 0.9D; at every station the gravity force and the induced one
    are added with the same sign (the worst), in each direction apart.
    """
    rows = []
    gravity_sets = (("1.2D + {f:g}L".format(f=live_factor), 1.2, live_factor), ("0.9D", 0.9, 0.0))
    for name, own in forces.groupby(forces["UniqueName"].astype(str)):
        story = story_of.get(name, str(own["Story"].iloc[0]))
        # one value per station (a member split into elements repeats the shared one)
        by_case = {case: grp.groupby("Station")[["P", "V2", "V3", "M2", "M3"]].first()
                   for case, grp in own.groupby(own["OutputCase"].astype(str))}
        best = None
        for case, direction in cases.items():
            if case not in by_case or (story, direction) not in factors:
                continue
            delta_u, built, k = factors[(story, direction)]
            quake = by_case[case]
            for label, df, fl in gravity_sets:
                grav = quake * 0.0
                for c, f in [(c, df) for c in dead] + [(c, fl) for c in live]:
                    if c in by_case and f:
                        grav = grav + f * by_case[c].reindex(quake.index).fillna(0.0)
                moment = max(float((grav[m].abs() + k * quake[m].abs()).max())
                             for m in ("M2", "M3")) / 1e6
                shear = max(float((grav[v].abs() + k * quake[v].abs()).max())
                            for v in ("V2", "V3")) / 1e3
                axial = float((-grav["P"]).max()) / 1e3        # compression positive
                item = {"UniqueName": name, "Story": story, "Direction": direction,
                        "Case": case, "Gravity": label, "delta_u": delta_u,
                        "Drift as built": built, "Factor": k, "Mu_kNm": moment,
                        "Vu_kN": shear, "Pu_kN": axial}
                if best is None or (moment, shear) > (best["Mu_kNm"], best["Vu_kN"]):
                    best = item
        if best is not None:
            rows.append(best)
    return pd.DataFrame(rows)


# =============================================================================
# THE CLAUSE THAT APPLIES, AND ITS DETAILING
# =============================================================================
@dataclass
class MemberCheck:
    name: str
    kind: str                  # "beam" or "column"
    story: str
    mu: float
    vu: float
    pu: float
    phi_mn: float
    phi_vn: float
    clause: str = ""
    detailing: list[tuple[str, str, bool | None]] = field(default_factory=list)  # (rule, value, ok)

    @property
    def within(self) -> bool:
        return self.mu <= self.phi_mn + 1e-9 and self.vu <= self.phi_vn + 1e-9

    @property
    def passed(self) -> bool:
        return all(ok is not False for _, _, ok in self.detailing)


def check_beam(name: str, story: str, induced: dict, strength: dict, smrf_design: bool
               ) -> MemberCheck:
    """418.14.3.2(a) when within its strength, else 418.14.3.3(b)."""
    phi_mn = min(strength["phi_Mn_top"], strength["phi_Mn_bottom"])
    check = MemberCheck(name, "beam", story, induced["Mu_kNm"], induced["Vu_kN"],
                        induced["Pu_kN"], phi_mn, strength["phi_Vn"])
    d = strength["d"]
    if check.within:
        check.clause = CLAUSE_WITHIN
        largest = max(strength["stirrup_spacing_mid"], strength["stirrup_spacing_end"])
        check.detailing.append((
            "Stirrups over the whole length at most d/2 (418.14.3.2(a))",
            f"{largest:.0f} mm against d/2 = {d / 2:.0f} mm", largest <= d / 2 + 1e-9))
        if induced["Pu_kN"] * 1e3 > strength["b"] * strength["h"] * strength["fc"] / 10.0:
            check.detailing.append((
                "Pu over Ag f'c / 10: hoops at so (418.14.3.2(a), 418.7.5.2)",
                f"Pu {induced['Pu_kN']:.0f} kN", None))
    else:
        check.clause = CLAUSE_OVER
        check.detailing.append((
            "Special moment frame beam rules, 418.6.5 and 418.14.3.2(a) (418.14.3.3(b))",
            "designed with the SMRF rules" if smrf_design else
            "designed WITHOUT the SMRF rules: design it as a special moment frame beam",
            smrf_design))
    return check


def check_column(name: str, story: str, induced: dict, strength: dict, dmain: float,
                 fy: float, smrf_design: bool) -> MemberCheck:
    """418.14.3.2(b) and (c) when within its strength, else 418.14.3.3(c)."""
    check = MemberCheck(name, "column", story, induced["Mu_kNm"], induced["Vu_kN"],
                        induced["Pu_kN"], strength["phi_Mn"], strength["phi_Vn"])
    so = min(6.0 * dmain, 150.0)
    general = NSCP_GENERAL_TIE_SPACING
    if check.within:
        check.clause = CLAUSE_WITHIN
        largest = max(general, strength["spacing"]) if strength["spacing"] == strength["spacing"] \
            else general
        check.detailing.append((
            "Hoops over the full height at so = min(6 db, 150 mm) (418.14.3.2(b))",
            f"{largest:.0f} mm against so = {so:.0f} mm", largest <= so + 1e-9))
        fc = strength["fc"]
        if fc == fc:
            po = 0.85 * fc * (strength["ag"] - strength["ast"]) + fy * strength["ast"]
            if induced["Pu_kN"] * 1e3 > 0.35 * po:
                check.detailing.append((
                    "Pu over 0.35 Po: half the confinement of 418.7.5.4 and 418.7.5.7, at so "
                    "(418.14.3.2(c))", f"Pu {induced['Pu_kN']:.0f} kN > 0.35 Po "
                    f"{0.35 * po / 1e3:.0f} kN", smrf_design or None))
    else:
        check.clause = CLAUSE_OVER
        check.detailing.append((
            "Special moment frame column rules 418.7.4, 418.7.5, 418.7.6 and the joint "
            "418.8.3.1 (418.14.3.3(c), (d))",
            "designed with the SMRF rules" if smrf_design else
            "designed WITHOUT the SMRF rules: design it as a special moment frame column",
            smrf_design))
    return check




# =============================================================================
# CANDIDATES FOR THE GROUP
# =============================================================================
def beam_candidates(beams: pd.DataFrame) -> dict[str, str]:
    """Cantilevers and girders with a clear span under 4d (418.6.2.1(a))."""
    out = {}
    for name, rows in beams.groupby(beams["UniqueName"].astype(str)):
        first = rows.iloc[0]
        status = str(first.get("SupportStatus", ""))
        if "cantilever" in status.lower():
            out[name] = "cantilever"
            continue
        d = float(first["Depth"]) - float(first.get("BotCover") or 60.0)
        span = pd.to_numeric(first.get("ClearSpan_Ln"), errors="coerce")
        if str(first.get("SectProp", "")).upper().startswith("G_") and span == span \
                and span < 4.0 * d:
            out[name] = f"clear span {span:.0f} mm under 4d = {4 * d:.0f} mm (418.6.2.1(a))"
    return out


def frame_beams(beams: pd.DataFrame) -> set[str]:
    """The beams that frame into columns (girders and grade tie beams): every beam
    the design did not find to be a gravity beam resting on other beams."""
    from design.concrete.beams.supports import is_gravity_beam

    first = beams.drop_duplicates("UniqueName")
    return {str(n) for n, status in zip(first["UniqueName"], first["SupportStatus"])
            if not is_gravity_beam(status)}


def column_candidates(connectivity: pd.DataFrame, points: pd.DataFrame,
                      supports: pd.DataFrame | None, girders: set[str],
                      shears: dict[str, float]) -> dict[str, str]:
    """Planted columns, columns with no girder in a direction, and columns under
    half the average share of their story's shear (``shears``: column -> the
    largest shear under the drift load cases)."""
    conn = connectivity.copy()
    conn["UniqueName"] = conn["UniqueName"].astype(str)
    columns = conn[conn["ColumnBay"].notna() & (conn["ColumnBay"].astype(str) != "nan")] \
        if "ColumnBay" in conn else conn.iloc[0:0]
    beams = conn[conn["UniqueName"].isin(girders)]
    xy = {str(r["UniqueName"]): (float(r["X"]), float(r["Y"])) for _, r in points.iterrows()}
    restrained = set(supports["UniqueName"].astype(str)) if supports is not None and \
        not supports.empty and "UniqueName" in supports else set()
    tops = set(columns["UniquePtJ"].astype(str))
    out: dict[str, str] = {}
    for _, c in columns.iterrows():
        name, bottom, top = str(c["UniqueName"]), str(c["UniquePtI"]), str(c["UniquePtJ"])
        if bottom not in tops and bottom not in restrained:
            out[name] = "planted: no column below and no support"
            continue
        ways = set()
        for _, b in beams[(beams["UniquePtI"].astype(str) == top)
                          | (beams["UniquePtJ"].astype(str) == top)].iterrows():
            i, j = xy.get(str(b["UniquePtI"])), xy.get(str(b["UniquePtJ"]))
            if i and j:
                ways.add("X" if abs(j[0] - i[0]) >= abs(j[1] - i[1]) else "Y")
        missing = {"X", "Y"} - ways
        if missing:
            out[name] = "no girder along " + " or ".join(sorted(missing)) + " at its top"
    story_of = dict(zip(columns["UniqueName"], columns["Story"].astype(str)))
    by_story: dict[str, list[tuple[str, float]]] = {}
    for name, shear in shears.items():
        if name in story_of:
            by_story.setdefault(story_of[name], []).append((name, abs(shear)))
    for story, items in by_story.items():
        total = sum(v for _, v in items)
        if total <= 0:
            continue
        average = 1.0 / len(items)
        for name, value in items:
            share = value / total
            if share < MINOR_SHARE * average and name not in out:
                out[name] = (f"{share:.1%} of the story shear, under half the average "
                             f"{average:.1%}")
    return out


# =============================================================================
# ETABS
# =============================================================================
def group_members(model) -> list[str]:
    """The frame objects of the group GRAVITY ONLY; empty when there is no group."""
    try:
        got = model.GroupDef.GetAssignments(GROUP, 0, [], [])
    except Exception:
        return []
    if return_code(got) != 0:
        return []
    types, names = as_list(got[1]), as_list(got[2])
    return [str(n) for t, n in zip(types, names) if int(t) == 2]   # 2: frame objects


def create_group(model, names: list[str]) -> int:
    """Create (or fill) the group GRAVITY ONLY with these frame objects."""
    model.GroupDef.SetGroup(GROUP)
    done = 0
    for name in names:
        if return_code(model.FrameObj.SetGroupAssign(name, GROUP, False, 0)) == 0:
            done += 1
    return done


def release_ends(model, names: list[str]) -> dict[str, tuple]:
    """Release M2 and M3 at both ends of each member; returns what they had."""
    before = {}
    for name in names:
        got = model.FrameObj.GetReleases(name, [], [], [], [])
        ii, jj, si, sj = (list(as_list(x)) for x in got[:4])
        before[name] = (ii, jj, si, sj)
        new_i, new_j = list(ii), list(jj)
        for index in (4, 5):        # M2, M3
            new_i[index] = new_j[index] = True
        model.FrameObj.SetReleases(name, new_i, new_j, list(si), list(sj))
    return before


def restore_releases(model, before: dict[str, tuple]) -> None:
    for name, (ii, jj, si, sj) in before.items():
        model.FrameObj.SetReleases(name, ii, jj, si, sj)


def story_drifts(connector, cases: dict[str, str], corners) -> dict[tuple[str, str], float]:
    """(story, direction) -> the largest drift ratio at the outer corners under the
    drift load case of that direction."""
    from etabs_api.workflows.drift_check import _read

    table = _read(connector, "Joint Drifts", [], list(cases))
    out: dict[tuple[str, str], float] = {}
    if table.empty:
        return out
    wanted = {(story, joint) for story, joints in corners.items() for joint in joints}
    for _, r in table.iterrows():
        case, story = str(r["OutputCase"]), str(r["Story"])
        if case not in cases or (story, str(r["UniqueName"])) not in wanted:
            continue
        direction = cases[case]
        value = abs(float(pd.to_numeric(r["DriftX" if direction == "X" else "DriftY"],
                                        errors="coerce")))
        if value == value:
            key = (story, direction)
            out[key] = max(out.get(key, 0.0), value)
    return out


def drift_cases(connector) -> dict[str, str]:
    """The seismic drift load patterns (EQXSD, EQYSD) and their directions."""
    from etabs_api.workflows.analysis_forces import pattern_types
    from etabs_api.workflows.drift_check import combo_direction
    from etabs_api.workflows.model_check import SEISMIC_DRIFT

    out = {}
    for name, kind in pattern_types(connector).items():
        if kind == SEISMIC_DRIFT and "(" not in name:
            direction = combo_direction(name)
            if direction:
                out[name] = direction
    return out


# =============================================================================
# THE COMMAND
# =============================================================================
def checks_table(checks: list[MemberCheck], induced: pd.DataFrame) -> pd.DataFrame:
    rows = []
    by_name = {str(r["UniqueName"]): r for _, r in induced.iterrows()}
    for c in checks:
        r = by_name.get(c.name, {})
        rows.append({
            "Member": c.name, "Kind": c.kind, "Story": c.story,
            "Direction": r.get("Direction"), "Gravity": r.get("Gravity"),
            "δu drift ratio": r.get("delta_u"), "Drift as built": r.get("Drift as built"),
            "Factor to δu": r.get("Factor"),
            "Mu (kN-m)": c.mu, "φMn (kN-m)": c.phi_mn, "Vu (kN)": c.vu, "φVn (kN)": c.phi_vn,
            "Pu (kN)": c.pu, "Within strength": "Yes" if c.within else "No",
            "Clause": c.clause,
            "Detailing": "; ".join(f"{rule}: {value}" for rule, value, _ in c.detailing),
            "Result": "PASS" if c.passed else "FAIL",
        })
    return pd.DataFrame(rows)


def evaluate(store, members: list[str], induced: pd.DataFrame) -> list[MemberCheck]:
    """The 418.14 check of every designated member that the stored design holds."""
    smrf = bool(store.inputs.get("smrf", True))
    exempt = bool(store.inputs.get("exempt_short_spans", False))
    bars = store.inputs.get("column_bars") or {}
    dmain = float(bars.get("dmain", 25.0))
    beams = store.beam_results if store.beam_results is not None else pd.DataFrame()
    columns = store.column_report if store.column_report is not None else pd.DataFrame()
    beam_rows = {n: g for n, g in beams.groupby(beams["UniqueName"].astype(str))} \
        if not beams.empty else {}
    column_rows = {n: g for n, g in columns.groupby(columns["UniqueName"].astype(str))} \
        if not columns.empty else {}
    frame = (store.tables or {}).get("FRAME DATA")
    fy_of = dict(zip(frame["UniqueName"].astype(str), pd.to_numeric(frame["fy"],
                                                                     errors="coerce"))) \
        if frame is not None else {}
    fc_of = dict(zip(frame["UniqueName"].astype(str), pd.to_numeric(frame["f'c"],
                                                                     errors="coerce"))) \
        if frame is not None else {}
    out = []
    for _, item in induced.iterrows():
        name = str(item["UniqueName"])
        if name not in members:
            continue
        if name in beam_rows:
            rows = beam_rows[name]
            seismic_rules = smrf and not (exempt and "4d" in str(rows.iloc[0].get(
                "SupportStatus", "")))
            out.append(check_beam(name, str(item["Story"]), item, beam_strength(rows),
                                  seismic_rules))
        elif name in column_rows:
            strength = column_strength(column_rows[name], dmain)
            if strength["fc"] != strength["fc"]:
                strength["fc"] = float(fc_of.get(name, math.nan))
            out.append(check_column(name, str(item["Story"]), item, strength, dmain,
                                    float(fy_of.get(name, 414.0)), smrf))
    return out


def run_compatibility_cli():
    """Entry point: the 418.14 check of the members of the group GRAVITY ONLY."""
    from design.concrete.workflow import DesignStore, attach_etabs
    from etabs_api.core.connection import model_file, save_model
    from etabs_api.workflows.analysis_forces import (
        ensure_analysis,
        frame_force_arrays,
        pattern_types,
    )
    from etabs_api.workflows.drift_check import outer_corners
    from utilities._gui_helpers import LoadingWindow, select_option, show_warning
    from utilities.output_folders import beside
    from utilities.run_summary import RunSummary

    title = "Deformation Compatibility"
    connector = attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    path = model_file(model)
    store = DesignStore.load(path)
    if store is None or store.beam_results is None:
        stem = os.path.splitext(path)[0]
        for alternative in (stem + " - DESIGN.EDB",):
            store = DesignStore.load(alternative)
            if store is not None:
                break
    if store is None or store.beam_results is None:
        show_warning("No stored design for this model: run sdt beams and sdt columns (or sdt "
                     "design) first. The check compares the induced forces with the strength "
                     "of the members as designed.", title=title)
        return None
    cases = drift_cases(connector)
    if not cases:
        show_warning("The model has no seismic drift load patterns (EQXSD, EQYSD): sdt setup "
                     "defines them.", title=title)
        return None
    members = group_members(model)
    types = pattern_types(connector)
    dead = [n for n, k in types.items() if k in DEAD_TYPES]
    live = [n for n, k in types.items() if k in LIVE_TYPES]
    with LoadingWindow(title) as window:
        window.update("Analysis and the forces of the drift and gravity load cases")
        ensure_analysis(connector, list(cases) + dead + live)
        forces = frame_force_arrays(connector, list(cases) + dead + live, {})
    if forces is None:
        show_warning("ETABS did not give the frame forces through the API.", title=title)
        return None
    if not members:
        shears = {}
        column_forces = forces["column"]
        quake = column_forces[column_forces["OutputCase"].astype(str).isin(cases)]
        for name, rows in quake.groupby(quake["UniqueName"].astype(str)):
            shears[name] = float(np.maximum(rows["V2"].abs(), rows["V3"].abs()).max())
        tables = store.tables or {}
        girders = frame_beams(store.beam_results)
        proposed = {**beam_candidates(store.beam_results),
                    **column_candidates(tables["CONNECTIVITY"], tables["POINTS"],
                                        tables.get("SUPPORTS"), girders, shears)}
        lines = [f"  {name}: {why}" for name, why in sorted(proposed.items())]
        text = (f"The model has no group {GROUP}: no member is designated as outside the "
                "seismic-force-resisting system.\n\nProposed (" + str(len(proposed)) + "):\n"
                + ("\n".join(lines[:60]) or "  none") + ("\n  ..." if len(lines) > 60 else ""))
        print(text)
        options = ([f"Create the group {GROUP} with these"] if proposed else []) + [
            "Check the gravity beams only (no other member designated)", "Stop"]
        chosen = select_option(title, text + "\n\nThe gravity beams (on other beams) are "
                               "checked in any case. Create the group with the proposed "
                               "members, review it in ETABS, then run sdt compatibility again?",
                               options)
        if chosen is None or chosen == "Stop":
            return None
        if chosen.startswith("Create"):
            if model.GetModelIsLocked():
                model.SetModelIsLocked(False)
            created = create_group(model, sorted(proposed))
            save_model(model)
            summary = RunSummary("sdt compatibility", path)
            summary.add("Group created", f"{GROUP}: {created} members")
            summary.note("Review the group in ETABS (add or remove members), then run sdt "
                         "compatibility again.")
            summary.show(popup=True)
            return None
    # the gravity beams are never part of the frames: 418.14 applies to them always
    every_beam = set(store.beam_results["UniqueName"].astype(str))
    gravity_beams = sorted(every_beam - frame_beams(store.beam_results))
    designated = list(members)
    members = sorted(set(members) | set(gravity_beams))
    factor = select_option(
        title, "Live load factor with the design displacement (NSCP 418.14.2.1)?",
        ["0.5 L (allowed, except garages, assembly areas and L over 4.8 kPa)",
         "1.0 L (garages, assembly areas, L over 4.8 kPa)"])
    if factor is None:
        return None
    live_factor = 0.5 if factor.startswith("0.5") else 1.0
    r_factor = _r_factor(connector)
    with LoadingWindow(title) as window:
        corners = outer_corners(connector._read_database_table("Point Object Connectivity"),
                                connector._read_database_table("Column Object Connectivity"))
        window.update("Story drifts of the model as built")
        real = story_drifts(connector, cases, corners)
        window.update("The frames alone: the designated members pinned")
        if model.GetModelIsLocked():
            model.SetModelIsLocked(False)
        before = release_ends(model, members)
        try:
            connector.analysis.run()
            pinned = story_drifts(connector, cases, corners)
        finally:
            window.update("Putting the end fixity back and analysing again")
            model.SetModelIsLocked(False)
            restore_releases(model, before)
            connector.analysis.run()
        factors = design_displacement(real, pinned, r_factor)
        story_of = {}
        for kind in ("beam", "column"):
            story_of.update(dict(zip(forces[kind]["UniqueName"].astype(str),
                                     forces[kind]["Story"].astype(str))))
        all_forces = pd.concat([forces["beam"], forces["column"]], ignore_index=True)
        induced = induced_forces(all_forces[all_forces["UniqueName"].astype(str).isin(members)],
                                 cases, factors, dead, live, live_factor, story_of)
        checks = evaluate(store, members, induced)
    table = checks_table(checks, induced)
    workbook = beside(path, " - Compatibility.xlsx")
    table.to_excel(workbook, index=False)
    lines = [f"{c.name:<14}{c.story:<8}Mu {c.mu:8.1f} / φMn {c.phi_mn:8.1f}   Vu {c.vu:7.1f} / "
             f"φVn {c.phi_vn:7.1f}   {c.clause.split(' (')[0]}   "
             f"{'PASS' if c.passed else 'FAIL'}" for c in checks]
    report = "\n".join(["Deformation compatibility (NSCP 208.7.2.3, 418.14)", "",
                        "δu by story (frames alone, ΔM = 0.7 R ΔS, at least 0.0025 h):"]
                       + [f"  {s} {d}: δu {v[0]:.5f}, as built {v[1]:.5f}, forces x {v[2]:.2f}"
                          for (s, d), v in sorted(factors.items())] + [""] + lines)
    print(report)
    text_path = beside(path, " - Compatibility.txt")
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")
    summary = RunSummary("sdt compatibility", path)
    summary.add("Members of " + GROUP, len(designated))
    summary.add("Gravity beams (always checked)", len(gravity_beams))
    summary.add("Checked", len(checks))
    summary.add("Within their strength (418.14.3.2)", sum(c.within for c in checks))
    summary.add("Over their strength (418.14.3.3)", sum(not c.within for c in checks))
    summary.add("Live load factor", f"{live_factor:g}")
    failing = [c.name for c in checks if not c.passed]
    if failing:
        summary.fail("Detailing not met: " + ", ".join(failing[:30]))
    missing = sorted(set(members) - {c.name for c in checks})
    if missing:
        summary.note("Not in the stored design, so not checked: " + ", ".join(missing[:30]))
    summary.file("Workbook", workbook)
    summary.file("Report", text_path)
    summary.show(popup=True)
    return checks


def _r_factor(connector) -> float:
    """R of the seismic patterns (UBC 97), else that of a special moment frame."""
    try:
        table = connector._read_database_table("Load Pattern Definitions - Auto Seismic - UBC 97")
        values = pd.to_numeric(table["R"], errors="coerce").dropna()
        if len(values):
            return float(values.iloc[0])
    except Exception:
        pass
    return NSCP.seismic.smrf_r


if __name__ == "__main__":
    run_compatibility_cli()
