"""
sdt walls: the structural walls of the open model, NSCP 2015 418.10 and 411.

1. The walls are read as legs and walls (``wall_piers``); every leg gets its pier
   label (as ``sdt tag`` gives it) when the model does not have them.
2. The engineer says which walls are special (418.10) and which ordinary (411),
   and gives the bars.
3. The pier forces of every leg, at the base of every story, for the strength
   combinations, are summed by statics to the centroid of the wall's assembled
   section (the sign convention of ETABS piers was checked against a pier of the
   whole wall: the sums agree to 2e-5).
4. The design displacement at the top of each wall, for the boundary elements
   by 418.10.6.2: 0.7 R times the largest displacement of the wall's top under
   the drift load cases (EQXSD, EQYSD).
5. ``design.concrete.walls.design_story`` designs each story; the results go to
   ``Results/<model> - Wall Design.xlsx`` and a summary.
"""

from __future__ import annotations

import math
import os
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.concrete.walls import (  # noqa: E402
    LegShape,
    StoryForces,
    WallInputs,
    design_story,
)
from design.criteria.code_config import NSCP  # noqa: E402
from etabs_api.core.helpers import as_list  # noqa: E402
from etabs_api.workflows import wall_piers as wp  # noqa: E402

SPECIAL, ORDINARY = "Special (418.10)", "Ordinary (411)"


def assemble(rows: pd.DataFrame, props: pd.DataFrame, legs: list[wp.Leg],
             story: str) -> list[StoryForces]:
    """The forces of one story of a wall, at the base of the story, summed from its
    legs to the centroid of the assembled section (kN, kN-m; P tension positive).

    Each leg's pier forces (P, V2, V3, M2, M3 in the pier axes, at the AxisAngle
    of ETABS) are turned to global and moved to the centroid. The convention that
    agrees with an ETABS pier of the whole wall: Mx = Σ (M2 ux − M3 nx − (y − yc) P),
    My = Σ (M2 uy − M3 ny + (x − xc) P), with u along the leg and n across it.
    """
    labels = {leg.label for leg in legs}
    own = rows[(rows["Story"].astype(str) == story) & (rows["Pier"].astype(str).isin(labels))
               & (rows["Location"].astype(str) == "Bottom")]
    geometry = {}
    for _, r in props[(props["Story"].astype(str) == story)
                      & (props["Pier"].astype(str).isin(labels))].iterrows():
        geometry[str(r["Pier"])] = (float(r["AxisAngle"]), float(r["CGBotX"]),
                                    float(r["CGBotY"]), float(r["WidthBot"]) * float(r["ThickBot"]))
    if not geometry:
        return []
    area = sum(g[3] for g in geometry.values())
    xc = sum(g[1] * g[3] for g in geometry.values()) / area
    yc = sum(g[2] * g[3] for g in geometry.values()) / area
    out = []
    keys = [own["OutputCase"].astype(str), own["StepType"].astype(str).fillna("")]
    for (combo, step), rows_c in own.groupby(keys, sort=False):
        p = mx = my = 0.0
        shears = {}
        for _, r in rows_c.iterrows():
            pier = str(r["Pier"])
            if pier not in geometry:
                continue
            angle, x, y, _ = geometry[pier]
            t = math.radians(angle)
            u, n = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
            f = {k: float(r[k]) for k in ("P", "V2", "V3", "M2", "M3")}
            p += f["P"]
            mx += f["M2"] * u[0] - f["M3"] * n[0] - (y - yc) * f["P"]
            my += f["M2"] * u[1] - f["M3"] * n[1] + (x - xc) * f["P"]
            shears[pier] = f["V2"] / 1e3
        label = combo if step in ("", "nan", "None") else f"{combo} {step}"
        out.append(StoryForces(label, p / 1e3, mx / 1e6, my / 1e6, shears))
    return out


def leg_shapes(legs: list[wp.Leg]) -> list[LegShape]:
    return [LegShape(leg.label, leg.x1, leg.y1, leg.x2, leg.y2, leg.thickness or 200.0)
            for leg in legs]


def wall_height(wall: wp.Wall, elevations: dict[str, float], below: dict[str, str]) -> float:
    stories = list(wall.legs)
    top = max(elevations[s] for s in stories)
    bottom = min(elevations[below[s]] for s in stories if below.get(s) in elevations)
    return top - bottom


def run_walls_cli():
    from design.concrete.workflow import attach_etabs
    from etabs_api.core.connection import model_file, save_model
    from etabs_api.workflows.compatibility import drift_cases
    from etabs_api.workflows.analysis_forces import ensure_analysis
    from utilities._gui_helpers import (
        DualListboxSelector,
        LoadingWindow,
        choose_for_each,
        enter_values,
        select_option,
        show_warning,
    )
    from utilities.output_folders import beside
    from utilities.run_summary import RunSummary

    title = "Wall Design"
    connector = attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    path = model_file(model)
    with LoadingWindow(title) as window:
        window.update("Reading the walls")
        panels, stories = wp.read_wall_panels(connector)
    if not panels:
        show_warning("The model has no walls.", title=title)
        return None
    walls = wp.plan_walls(panels, stories)
    have = {p.name: str(model.AreaObj.GetPier(p.name, "")[0]) for p in panels}
    planned = {p.name: leg.label for w in walls for legs in w.legs.values() for leg in legs
               for p in leg.panels}
    if any(have.get(n) != planned[n] for n in planned):
        answer = select_option(title, "The walls are not labelled as the design reads them: one "
                               "pier label per straight leg (W1A, W1B, ...), the legs that meet "
                               "making one wall. Label them now (the model is changed)?",
                               ["Label the piers now", "Stop"])
        if answer != "Label the piers now":
            return None
        if model.GetModelIsLocked():
            model.SetModelIsLocked(False)
        wp.apply_pier_labels(model, walls)
        save_model(model)
    # ---- inputs ----
    names = [f"W{w.number} ({w.shape}, {len(next(iter(w.legs.values())))} legs)" for w in walls]
    kinds = choose_for_each(title, "Special structural walls follow 418.10 (zone 4 seismic "
                            "systems); ordinary walls 411.", names, [SPECIAL, ORDINARY],
                            {n: SPECIAL for n in names})
    if kinds is None:
        return None
    labels = ["f'c (MPa)", "fy (MPa)", "Vertical bar (mm)", "Horizontal bar (mm)", "Cover (mm)",
              "Boundary element length rounded up to (mm)"]
    typed = enter_values(title, "Materials and bars of the walls.", labels,
                         {labels[0]: "28", labels[1]: "414", labels[2]: "16", labels[3]: "12",
                          labels[4]: "40", labels[5]: "50"})
    if typed is None:
        return None
    try:
        fc, fy, bar_v, bar_h, cover, module = (float(typed[k]) for k in labels)
    except ValueError:
        show_warning("Every value must be a number.", title=title)
        return None
    decides = select_option(title, "Special boundary elements: when the displacement method "
                            "(418.10.6.2) and the stress method (418.10.6.3) both apply, which "
                            "decides? Both results are shown either way.",
                            ["Displacement method (418.10.6.2)", "Stress method (418.10.6.3)"])
    if decides is None:
        return None
    combos = [str(n) for n in as_list(model.RespCombo.GetNameList(0, [])[1])]
    strength = [n for n in combos if n.upper().startswith("ULS")]
    if not strength:
        picked = DualListboxSelector("Strength combinations for the wall design",
                                     combos).show()
        if not picked:
            return None
        strength = picked
    # ---- forces ----
    cases = drift_cases(connector)
    r_factor = NSCP.seismic.smrf_r
    with LoadingWindow(title) as window:
        window.update("Analysis and the pier forces")
        ensure_analysis(connector, list(cases) or [])
        tables = model.DatabaseTables
        tables.SetLoadCasesSelectedForDisplay([])
        tables.SetLoadCombinationsSelectedForDisplay(strength)
        forces = connector._read_database_table("Pier Forces")
        props = connector._read_database_table("Pier Section Properties")
        try:
            seismic = connector._read_database_table(
                "Load Pattern Definitions - Auto Seismic - UBC 97")
            r_values = pd.to_numeric(seismic["R"], errors="coerce").dropna()
            r_factor = float(r_values.iloc[0]) if len(r_values) else r_factor
        except Exception:
            pass
        tables.SetLoadCasesSelectedForDisplay(list(cases))
        tables.SetLoadCombinationsSelectedForDisplay([])
        moved = connector._read_database_table("Joint Displacements")
        levels = model.Story.GetStories()
        names_s = [str(n) for n in as_list(levels[1])]
        elevations = dict(zip(names_s, (float(z) for z in as_list(levels[2]))))
        below = {names_s[i]: names_s[i - 1] for i in range(1, len(names_s))}
        designs = []
        for wall, label in zip(walls, names):
            special = kinds[label] == SPECIAL
            hw = wall_height(wall, elevations, below)
            top_story = max(wall.legs, key=lambda s: elevations[s])
            corners = {p.name for leg in wall.legs[top_story] for p in leg.panels}
            delta_u = _top_displacement(moved, model, corners, cases, r_factor)
            inputs = WallInputs(special, fc, fy, fy, cover, bar_v, bar_h, module,
                                "displacement" if decides.startswith("Displacement") else "stress")
            for story in sorted(wall.legs, key=lambda s: elevations[s]):
                window.update(f"Wall W{wall.number}, {story}")
                legs = wall.legs[story]
                story_forces = assemble(forces, props, legs, story)
                if not story_forces:
                    continue
                result = design_story(story, leg_shapes(legs), story_forces, hw, delta_u,
                                      inputs)
                designs.append((wall, special, legs, result))
    table = results_table(designs, bar_v, bar_h)
    workbook = beside(path, " - Wall Design.xlsx")
    table.to_excel(workbook, index=False)
    summary = RunSummary("sdt walls", path)
    summary.add("Walls", f"{len(walls)} ({sum(kinds[n] == SPECIAL for n in names)} special)")
    summary.add("Wall stories designed", len(designs))
    failing = [f"W{w.number} {d.story}" for w, _, _, d in designs if not d.passed]
    if failing:
        summary.fail("Failing: " + ", ".join(failing))
    for w, _, _, d in designs:
        for problem in d.problems:
            summary.fail(f"W{w.number} {d.story}: {problem}")
    elements = [f"W{w.number} {d.story}" for w, _, _, d in designs
                if any(b["needed"] for b in d.boundary)]
    if elements:
        summary.add("Special boundary elements", ", ".join(elements))
    summary.file("Workbook", workbook)
    summary.show(popup=True)
    return designs


def _top_displacement(moved: pd.DataFrame, model, panels: set[str], cases: dict[str, str],
                      r_factor: float) -> dict[str, float]:
    """δu at the top of the wall in X and Y: 0.7 R times the largest displacement of
    the top joints of its top panels under the drift load cases (mm)."""
    joints = set()
    for name in panels:
        got = model.AreaObj.GetPoints(name, 0, [])
        joints |= {str(j) for j in as_list(got[1])}
    out = {"X": 0.0, "Y": 0.0}
    if moved.empty:
        return out
    rows = moved[moved["UniqueName"].astype(str).isin(joints)]
    for case, direction in cases.items():
        mine = rows[rows["OutputCase"].astype(str) == case]
        if mine.empty:
            continue
        column = "Ux" if "Ux" in mine else "UX"
        if direction == "Y":
            column = "Uy" if "Uy" in mine else "UY"
        value = float(pd.to_numeric(mine[column], errors="coerce").abs().max())
        out[direction] = max(out[direction], NSCP.seismic.drift_amplification * r_factor * value)
    return out


def results_table(designs, bar_v: float, bar_h: float) -> pd.DataFrame:
    rows = []
    for wall, special, legs, d in designs:
        base = {"Wall": f"W{wall.number}", "Shape": wall.shape, "Story": d.story,
                "Kind": "special" if special else "ordinary",
                "Vertical bars": f"{d.curtains} x {bar_v:g} mm @ {d.vertical_spacing:g}",
                "ρl": round(d.rho_l, 5), "P-M ratio": round(d.pm_ratio, 3),
                "Governs": d.governing}
        for leg in legs:
            rho_t, s, phi = d.horizontal.get(leg.label, (0.0, 0.0, 0.0))
            ends = [b for b in d.boundary if b["needed"]]
            rows.append({**base, "Leg": leg.label, "Length (mm)": round(leg.length),
                         "Thickness (mm)": round(leg.thickness),
                         "Horizontal bars": f"{d.curtains} x {bar_h:g} mm @ {s:g}",
                         "ρt": round(rho_t, 5), "φ shear": phi,
                         "Boundary elements": "; ".join(
                             f"axis {b['axis']:.0f}° end {b['end']}: {b['length']:.0f} mm "
                             f"({b['method']})" for b in ends) or "not needed",
                         "Result": "PASS" if d.passed else "FAIL",
                         "Problems": "; ".join(d.problems)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    run_walls_cli()
