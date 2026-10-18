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
              "Boundary element length rounded up to (mm)",
              "Coupling beam diagonal bar (mm)", "Degree of coupling: coupled from (%)",
              "Wall schedule: sections at 1 :"]
    typed = enter_values(title, "Materials and bars of the walls and coupling beams. Walls "
                         "whose degree of coupling is under the limit act as separate "
                         "cantilevers.", labels,
                         {labels[0]: "28", labels[1]: "414", labels[2]: "16", labels[3]: "12",
                          labels[4]: "40", labels[5]: "50", labels[6]: "20", labels[7]: "20",
                          labels[8]: "50"})
    if typed is None:
        return None
    try:
        fc, fy, bar_v, bar_h, cover, module, bar_d, coupled_from, scale = (
            float(typed[k]) for k in labels)
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
        inputs_by: dict[int, WallInputs] = {}
        for wall, label in zip(walls, names):
            special = kinds[label] == SPECIAL
            hw = wall_height(wall, elevations, below)
            top_story = max(wall.legs, key=lambda s: elevations[s])
            corners = {p.name for leg in wall.legs[top_story] for p in leg.panels}
            delta_u = _top_displacement(moved, model, corners, cases, r_factor)
            inputs = WallInputs(special, fc, fy, fy, cover, bar_v, bar_h, module,
                                "displacement" if decides.startswith("Displacement") else "stress")
            inputs_by[wall.number] = inputs
            for story in sorted(wall.legs, key=lambda s: elevations[s]):
                window.update(f"Wall W{wall.number}, {story}")
                legs = wall.legs[story]
                story_forces = assemble(forces, props, legs, story)
                if not story_forces:
                    continue
                result = design_story(story, leg_shapes(legs), story_forces, hw, delta_u,
                                      inputs)
                designs.append((wall, special, legs, result))
        window.update("Coupling beams")
        beams, pairs = coupling(connector, model, walls, strength, cases, fc, fy, bar_d,
                                coupled_from / 100.0)
    table = results_table(designs, bar_v, bar_h)
    workbook = beside(path, " - Wall Design.xlsx")
    with pd.ExcelWriter(workbook) as writer:
        table.to_excel(writer, sheet_name="Walls", index=False)
        coupling_table(beams).to_excel(writer, sheet_name="Coupling beams", index=False)
        pd.DataFrame(pairs).to_excel(writer, sheet_name="Degree of coupling", index=False)
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
    schedule = write_schedule(path, designs, fc, fy, cover, bar_v, bar_h, scale)
    from design.concrete.wall_report import export_wall_pdf

    with LoadingWindow(title) as window:
        window.update("The calculation report (.pdf, LaTeX)")
        pdf = export_wall_pdf(designs, lambda w: inputs_by[w.number],
                              beside(path, " - Wall Calculations.pdf"))
    if beams:
        summary.add("Coupling beams", len(beams))
        for beam in beams:
            if not beam.passed:
                summary.fail(f"Coupling beam {beam.name} ({beam.story}): {beam.note}")
    for pair in pairs:
        summary.add(f"Degree of coupling {pair['Walls']} ({pair['Case']})",
                    f"{pair['Degree of coupling']:.0%}: {pair['Acts as']}")
    summary.file("Workbook", workbook)
    summary.file("Wall schedule", schedule)
    if pdf:
        summary.file("Calculation report", pdf)
    else:
        summary.fail("The calculation report was not written (LaTeX): run sdt doctor")
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


def coupling(connector, model, walls, strength: list[str], cases: dict[str, str], fc: float,
             fy: float, bar: float, coupled_from: float):
    """The coupling beams (designed by 418.10.7) and the degree of coupling of every
    pair of walls they join, under the drift load cases."""
    from design.concrete.coupling import degree_of_coupling, design_coupling_beam
    from etabs_api.workflows.sections import parse_section

    conn = connector._read_database_table("Beam Object Connectivity")
    points = connector._read_database_table("Point Object Connectivity")
    xy = {str(r["UniqueName"]): (float(r["X"]), float(r["Y"])) for _, r in points.iterrows()}
    lines = []
    for _, r in conn.iterrows():
        i, j = xy.get(str(r["UniquePtI"])), xy.get(str(r["UniquePtJ"]))
        if i and j:
            lines.append(wp.BeamLine(str(r["UniqueName"]), str(r["Story"]), *i, *j))
    found = wp.coupling_beams(lines, walls)
    if not found:
        return [], []
    tables = model.DatabaseTables

    def beam_shears(selection_cases, selection_combos):
        tables.SetLoadCasesSelectedForDisplay(selection_cases)
        tables.SetLoadCombinationsSelectedForDisplay(selection_combos)
        forces = connector._read_database_table("Element Forces - Beams")
        wanted = {beam.name for beam, *_ in found}
        mine = forces[forces["UniqueName"].astype(str).isin(wanted)]
        return mine

    strength_forces = beam_shears([], strength)
    results = []
    for beam, a, b, ln in found:
        section = str(model.FrameObj.GetSection(beam.name, "", "")[0])
        parsed = parse_section(section)
        if parsed is not None:
            width, depth = float(parsed.width), float(parsed.depth)
        else:
            got = model.PropFrame.GetRectangle(section, "", "", 0.0, 0.0, 0, "", "")
            depth, width = float(got[2]), float(got[3])
        own = strength_forces[strength_forces["UniqueName"].astype(str) == beam.name]
        vu = float(pd.to_numeric(own["V2"], errors="coerce").abs().max()) / 1e3 if len(own) else 0.0
        results.append(design_coupling_beam(beam.name, (f"W{a.number}", f"W{b.number}"),
                                            beam.story, ln, depth, width, vu, fc, fy, bar))
    # the degree of coupling, by pair and drift case
    pairs = []
    case_forces = beam_shears(list(cases), [])
    tables.SetLoadCasesSelectedForDisplay(list(cases))
    tables.SetLoadCombinationsSelectedForDisplay([])
    piers = connector._read_database_table("Pier Forces")
    props = connector._read_database_table("Pier Section Properties")
    by_pair: dict[tuple[int, int], list] = {}
    for beam, a, b, _ in found:
        key = tuple(sorted((a.number, b.number)))
        by_pair.setdefault(key, []).append(beam.name)
    lookup = {w.number: w for w in walls}
    for (na, nb), names in by_pair.items():
        wa, wb = lookup[na], lookup[nb]
        base = min(set(wa.legs) & set(wb.legs), key=lambda s: list(wa.legs).index(s)
                   if s in wa.legs else 0, default=None)
        if base is None:
            continue
        ca, cb = _centroid(props, wa.legs[base], base), _centroid(props, wb.legs[base], base)
        if ca is None or cb is None:
            continue
        dx, dy = cb[0] - ca[0], cb[1] - ca[1]
        distance = math.hypot(dx, dy)
        across = (-dy / distance, dx / distance)
        for case in cases:
            shear = case_forces[(case_forces["UniqueName"].astype(str).isin(names))
                                & (case_forces["OutputCase"].astype(str) == case)]
            per_beam = shear.groupby(shear["UniqueName"].astype(str))["V2"].apply(
                lambda v: float(pd.to_numeric(v, errors="coerce").abs().max()) / 1e3)
            moments = []
            for wall in (wa, wb):
                forces = [f for f in assemble(piers, props, wall.legs[base], base)
                          if f.combo.startswith(case)]
                moments.append(max((abs(f.mx * across[0] + f.my * across[1]) for f in forces),
                                   default=0.0))
            doc = degree_of_coupling(list(per_beam.values), distance / 1e3, *moments)
            pairs.append({"Walls": f"W{na} - W{nb}", "Case": case, "Coupling beams": len(names),
                          "Distance (m)": round(distance / 1e3, 3),
                          "Sum of beam shears (kN)": round(float(per_beam.sum()), 1),
                          "Wall base moments (kN-m)": f"{moments[0]:.0f}, {moments[1]:.0f}",
                          "Degree of coupling": round(doc, 4),
                          "Acts as": "coupled walls" if doc >= coupled_from else
                          "separate cantilevers (the coupling is weak)"})
    return results, pairs


def _centroid(props: pd.DataFrame, legs, story: str):
    rows = props[(props["Story"].astype(str) == story)
                 & (props["Pier"].astype(str).isin({leg.label for leg in legs}))]
    if rows.empty:
        return None
    area = (pd.to_numeric(rows["WidthBot"]) * pd.to_numeric(rows["ThickBot"]))
    return (float((pd.to_numeric(rows["CGBotX"]) * area).sum() / area.sum()),
            float((pd.to_numeric(rows["CGBotY"]) * area).sum() / area.sum()))


def write_schedule(path: str, designs, fc: float, fy: float, cover: float, bar_v: float,
                   bar_h: float, scale: float) -> str:
    """The DXF wall schedule: one section detail per wall and story (the walls whose
    legs meet as one section)."""
    from design.concrete.wall_schedule import write_wall_schedule
    from design.concrete.walls import WallSection
    from utilities.output_folders import SCHEDULES, beside

    by_wall: dict[int, dict] = {}
    for wall, _, legs, d in designs:
        shapes = leg_shapes(legs)
        section = WallSection(shapes, fc, fy, cover, bar_v, d.vertical_spacing, d.curtains)
        thick = sorted({round(leg.thickness) for leg in legs})
        horizontal = sorted({(s, d.curtains) for _, s, _ in d.horizontal.values()})
        elements = [b for b in d.boundary if b["needed"]]
        entry = by_wall.setdefault(wall.number, {"name": f"W{wall.number}", "stories": []})
        entry["stories"].append({
            "story": d.story, "legs": shapes, "section": section, "boundary": d.boundary,
            "thickness": ", ".join(f"{t}" for t in thick) + " mm",
            "vertical": f"{d.curtains} x {bar_v:g}mm @ {d.vertical_spacing:g}",
            "horizontal": "; ".join(f"{c} x {bar_h:g}mm @ {s:g}" for s, c in horizontal),
            "elements": (", ".join(f"{b['length']:.0f} mm" for b in elements)
                         if elements else "none")})
    walls = [by_wall[n] for n in sorted(by_wall)]
    for wall in walls:
        wall["stories"].reverse()          # the top story first
    return write_wall_schedule(beside(path, " - Wall Schedule.dxf", SCHEDULES), walls, scale)


def coupling_table(beams) -> pd.DataFrame:
    return pd.DataFrame([{
        "Beam": b.name, "Walls": " - ".join(b.walls), "Story": b.story,
        "ln (mm)": round(b.ln), "h (mm)": round(b.h), "b (mm)": round(b.b),
        "ln / h": round(b.ln / b.h, 2) if b.h else None, "Vu (kN)": round(b.vu, 1),
        "Rule": b.rule, "Clause": b.clause, "Avd per group (mm2)": round(b.avd),
        "0.83 sqrt(f'c) Acw (kN)": round(b.vn_max, 1),
        "Result": "PASS" if b.passed else "FAIL", "Note": b.note} for b in beams])


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
