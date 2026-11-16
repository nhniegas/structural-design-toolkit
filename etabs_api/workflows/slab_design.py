"""sdt slabs: the solid slabs of the open model, designed panel by panel.

Read from the model (N-mm):
* the floor area objects with a slab property (decks, openings and walls left out), with
  the thickness and concrete of their property;
* the beams at each level, with the width and depth of their rectangular concrete
  section (a beam of another section is taken as a stiff support), and the concrete walls
  through or ending at each level;
* the uniform loads on the floors, from load sets and direct assignments: dead and
  superimposed dead patterns as superimposed dead, live, reducible live and roof live
  patterns as live (the slab weight is added from its thickness).

The panels and their design are in design/concrete/slabs. The command writes the results
workbook (schedule per floor, every panel, the flags), the slab schedule (DXF), the
calculation report (PDF), and keeps the panels and marks beside the model
(`` - slab design.json``) for the slab marks of ``sdt plans``.
"""

from __future__ import annotations

import json
import math
from collections import Counter

import pandas as pd

from design.concrete.slabs.design import SlabOptions, design_floor
from design.concrete.slabs.panels import SlabArea, Support, make_panels
from design.concrete.slabs.schedule import (
    assign_marks,
    flags_table,
    panel_table,
    schedule_table,
)
from etabs_api.core.helpers import as_list, return_code

TITLE = "Slab Design"
DEAD_TYPES = (1, 2)         # Dead, SuperDead
LIVE_TYPES = (3, 4, 11)     # Live, ReduceLive, RoofLive
LEVEL = 1.0                 # mm
SLAB_FIELDS = {
    "fy of the slab bars (MPa)": ("fy", "414"),
    "Bar diameter, both ways (mm)": ("bar", "12"),
    "Largest bar spacing (mm)": ("max_spacing", "300"),
    "Method 3 when alpha_fm is at least": ("alpha_limit", "2.0"),
    "Most marks per floor": ("max_marks", "15"),
    "Merge marks with spacings within (mm)": ("tolerance", "25"),
    "Unit weight of the concrete (kN/m3)": ("unit_weight", "23.6"),
}


def _rows(model, key: str) -> list[dict]:
    from etabs_api.workflows.grid_column_model import _table

    return _table(model, key)


def _num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def read_concrete(model) -> tuple[dict[str, float], dict[str, float]]:
    """f'c (MPa) and unit weight (kN/m3) of the concrete materials of the model."""
    fc = {str(r.get("Material")): _num(r.get("Fc"))
          for r in _rows(model, "Material Properties - Concrete Data")}
    weight = {}
    for r in _rows(model, "Material Properties - Basic Mechanical Properties"):
        w = _num(r.get("UnitWeight"))
        if w > 0:
            weight[str(r.get("Material"))] = w * 1e6       # N/mm3 -> kN/m3
    return fc, weight


def read_loads(connector) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """(superimposed dead kPa, live kPa, a note of the source) of every floor object."""
    from etabs_api.workflows.tributary import _table

    model = connector.sap_model
    names = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    types = {n: int(model.LoadPatterns.GetLoadType(n)[0]) for n in names}
    dead: dict[str, float] = {}
    live: dict[str, float] = {}
    note: dict[str, str] = {}

    def add(name: str, pattern: str, kpa: float, source: str):
        kind = types.get(pattern)
        if kind in DEAD_TYPES:
            dead[name] = dead.get(name, 0.0) + kpa
        elif kind in LIVE_TYPES:
            live[name] = live.get(name, 0.0) + kpa
        else:
            return
        note[name] = source

    sets = _table(connector, "Shell Uniform Load Sets", names)
    if not sets.empty:
        sets = sets.copy()
        sets["Name"] = sets["Name"].replace({None: pd.NA, "": pd.NA}).ffill()
        values: dict[str, list] = {}
        for set_name, pattern, value in zip(sets["Name"], sets["LoadPattern"],
                                            pd.to_numeric(sets["LoadValue"], errors="coerce")):
            if not pd.isna(value):
                values.setdefault(str(set_name), []).append((str(pattern), abs(float(value)) * 1e3))
        assigned = _table(connector, "Area Load Assignments - Uniform Load Sets", names)
        for name, load_set in zip(assigned.get("UniqueName", []), assigned.get("LoadSet", [])):
            for pattern, kpa in values.get(str(load_set), []):
                add(str(name), pattern, kpa, f"load set {load_set}")
    direct = _table(connector, "Area Load Assignments - Uniform", names)
    if not direct.empty:
        gravity = direct["Dir"].astype(str).str.contains("Gravity|Z", case=False, regex=True)
        for name, pattern, value in zip(direct["UniqueName"][gravity],
                                        direct["LoadPattern"][gravity],
                                        pd.to_numeric(direct["Load"][gravity], errors="coerce")):
            if not pd.isna(value):
                add(str(name), str(pattern), abs(float(value)) * 1e3, "uniform loads")
    return dead, live, note


def read_slab_model(connector) -> tuple[list[SlabArea], list[Support], dict]:
    """The slabs, the beam and wall lines of every level, and what was left out."""
    from etabs_api.workflows.foundation_plan import read_shells

    model = connector.sap_model
    xyz = {str(r["UniqueName"]): (_num(r["X"]), _num(r["Y"]), _num(r["Z"]))
           for r in _rows(model, "Point Object Connectivity")}
    got = model.Story.GetStories()
    stories = sorted(zip([str(n) for n in as_list(got[1])], [float(z) for z in as_list(got[2])]),
                     key=lambda s: s[1])

    def story_at(z: float) -> str | None:
        for name, level in stories:
            if abs(level - z) <= LEVEL:
                return name
        return None

    fc_of, weight_of = read_concrete(model)
    dead, live, note = read_loads(connector)
    info: dict = {"decks or other": 0, "off a story level": 0, "unit weights": Counter()}
    material: dict[str, str] = {}
    areas = []
    for shell in read_shells(model, xyz):
        if shell.thickness <= 0:
            info["decks or other"] += 1
            continue
        story = story_at(shell.z)
        if story is None:
            info["off a story level"] += 1
            continue
        if shell.section not in material:
            slab = model.PropArea.GetSlab(shell.section)
            material[shell.section] = str(slab[2]) if return_code(slab) == 0 else ""
        mat = material[shell.section]
        info["unit weights"][round(weight_of.get(mat, 0.0), 2)] += 1
        areas.append(SlabArea(shell.name, story, shell.z, list(shell.points), shell.thickness,
                              shell.section, fc_of.get(mat, 28.0) or 28.0,
                              dead.get(shell.name, 0.0), live.get(shell.name, 0.0),
                              note.get(shell.name, "")))
    supports = _read_beams(model, xyz, stories, fc_of)
    supports += _read_walls(model, xyz, stories, fc_of)
    return areas, supports, info


def _read_beams(model, xyz, stories, fc_of) -> list[Support]:
    sections = {str(r["UniqueName"]): str(r["SectProp"])
                for r in _rows(model, "Frame Assignments - Section Properties")}
    rect = {str(r["Name"]): (_num(r.get("t2")), _num(r.get("t3")), str(r.get("Material")))
            for r in _rows(model, "Frame Section Property Definitions - Concrete Rectangular")}
    out = []
    for r in _rows(model, "Beam Object Connectivity"):
        a, b = str(r["UniquePtI"]), str(r["UniquePtJ"])
        if a not in xyz or b not in xyz or abs(xyz[a][2] - xyz[b][2]) > LEVEL:
            continue
        name = str(r["UniqueName"])
        width, depth, mat = rect.get(sections.get(name, ""), (0.0, 0.0, ""))
        kind = "beam" if width > 0 and depth > 0 else "other"
        out.append(Support(name, str(r.get("Story", "")), xyz[a][2], xyz[a][:2], xyz[b][:2],
                           width, depth, kind, fc_of.get(mat, 28.0) or 28.0))
    return out


def _read_walls(model, xyz, stories, fc_of) -> list[Support]:
    """Every wall panel as a support at each story level from its foot to its top."""
    sections = {str(r["UniqueName"]): str(r["SectProp"])
                for r in _rows(model, "Area Assignments - Section Properties")}
    props: dict[str, tuple[float, str]] = {}
    out = []
    for r in _rows(model, "Wall Object Connectivity"):
        corners = [xyz[str(r[f"UniquePt{i}"])] for i in range(1, 5)
                   if str(r.get(f"UniquePt{i}")) in xyz]
        plan = sorted({(round(c[0], 1), round(c[1], 1)) for c in corners})
        if len(plan) != 2:
            continue
        name = str(r["UniqueName"])
        section = sections.get(name, "")
        if section not in props:
            try:
                wall = model.PropArea.GetWall(section)
                props[section] = ((float(wall[3]), str(wall[2])) if return_code(wall) == 0
                                  else (0.0, ""))
            except Exception:  # noqa: BLE001 - not a wall property
                props[section] = (0.0, "")
        thickness, mat = props[section]
        low, high = min(c[2] for c in corners), max(c[2] for c in corners)
        for story, level in stories:
            if low - LEVEL <= level <= high + LEVEL:
                out.append(Support(name, story, level, plan[0], plan[1], thickness, 0.0, "wall",
                                   fc_of.get(mat, 28.0) or 28.0))
    return out


def ask_slab_inputs(title: str, unit_weight: float | None
                    ) -> tuple[SlabOptions, int, float] | None:
    from utilities._gui_helpers import enter_values, show_warning

    defaults = {label: default for label, (_, default) in SLAB_FIELDS.items()}
    if unit_weight:
        defaults["Unit weight of the concrete (kN/m3)"] = f"{unit_weight:g}"
    typed = enter_values(title, "The slab bars and limits. Panels on beams with a mean alpha_f "
                         "under the limit are designed by the Direct Design Method (NSCP "
                         "408.10) when its limits hold. The unit weight is offered from the "
                         "slab concrete of the model.", list(SLAB_FIELDS), defaults)
    if typed is None:
        return None
    try:
        values = {key: float(typed[label]) for label, (key, _) in SLAB_FIELDS.items()}
    except ValueError:
        show_warning("Every value must be a number.", title=title)
        return None
    options = SlabOptions(fy=values["fy"], bar=values["bar"], max_spacing=values["max_spacing"],
                          alpha_limit=values["alpha_limit"], unit_weight=values["unit_weight"])
    return options, max(1, int(values["max_marks"])), max(0.0, values["tolerance"])


def write_workbook(path: str, designs, marks) -> list[str]:
    """The results workbook; returns the panels that fail."""
    from utilities._xlsx_values import cell_value, mark_failures

    schedule = schedule_table(marks)
    panels = panel_table(designs)
    failing: list[str] = []
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for story in dict.fromkeys(m.story for m in marks):
            part = schedule[schedule["Story"] == story].drop(columns=["Story"])
            sheet = f"Schedule {story}"[:31]
            part.to_excel(writer, sheet_name=sheet, index=False)
        clean = panels.map(cell_value) if hasattr(panels, "map") else panels.applymap(cell_value)
        clean.to_excel(writer, sheet_name="Panels", index=False)
        flags_table(designs).to_excel(writer, sheet_name="Flags", index=False)
        sheet = writer.sheets["Panels"]
        failing = mark_failures(sheet, 2, 3)
        for ws in writer.sheets.values():
            for column in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in column)
                ws.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 60)
    return failing


def free_end(design) -> int:
    """For a cantilever, +1 when its free edge is at the far end of its span (E or N in
    the panel's axes), -1 at the near end (W or S); 0 for other panels."""
    if design.kind != "CANTILEVER":
        return 0
    near = "W" if design.short_dir == "X" else "S"
    return 1 if design.panel.sides[near].is_supported else -1


def save_for_plans(path: str, designs) -> str:
    """The panels and their marks, for the slab marks of sdt plans."""
    data = [{"story": d.panel.story, "z": d.panel.z, "centre": list(d.panel.centre),
             "span": [d.panel.lx, d.panel.ly], "angle": d.panel.angle, "mark": d.mark,
             "kind": d.kind, "short": d.short_dir, "free": free_end(d), "passed": d.passed}
            for d in designs]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "panels": data}, handle, indent=1)
    return path


def slab_data_path(model_path: str) -> str:
    import os

    stem = os.path.splitext(os.path.abspath(model_path))[0]
    return stem + " - slab design.json"


def run_slabs_cli():
    from design.concrete.workflow import attach_etabs
    from etabs_api.core.connection import model_file
    from utilities._gui_helpers import LoadingWindow, show_warning
    from utilities.output_folders import beside
    from utilities.run_summary import RunSummary

    connector = attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    path = model_file(model)
    with LoadingWindow(TITLE) as window:
        window.update("Reading the slabs, beams, walls and loads")
        areas, supports, info = read_slab_model(connector)
    if not areas:
        show_warning("The model has no floor objects with a slab property.", title=TITLE)
        return None
    weights = [w for w, _ in info["unit weights"].most_common() if w > 0]
    asked = ask_slab_inputs(TITLE, weights[0] if weights else None)
    if asked is None:
        return None
    from utilities.form_window import ready_to_run

    ready_to_run()   # the questions end here: the form waits for Run
    options, max_marks, tolerance = asked
    with LoadingWindow(TITLE) as window:
        window.update("Panels")
        panels = make_panels(areas, supports)
        window.update(f"Designing {len(panels)} panels")
        designs = design_floor(panels, options)
        marks = assign_marks(designs, options.bar, max_marks, tolerance)
    workbook = beside(path, " - Slab Design.xlsx")
    failing = write_workbook(workbook, designs, marks)
    from design.concrete.slabs.schedule_dxf import write_slab_schedule

    schedule = write_slab_schedule(beside(path, " - Slab Schedule.dxf"), marks)
    data = save_for_plans(slab_data_path(path), designs)
    from design.concrete.slabs.report import export_slab_pdf

    with LoadingWindow(TITLE) as window:
        window.update("The calculation report (.pdf, LaTeX)")
        pdf = export_slab_pdf(designs, options, beside(path, " - Slab Calculations.pdf"))
    summary = RunSummary("sdt slabs", path)
    kinds = Counter(d.kind for d in designs)
    summary.add("Floor objects", f"{len(areas)} in {len(panels)} panels")
    summary.add("Panels", ", ".join(f"{n} {k.lower()}" for k, n in kinds.items()))
    summary.add("Marks", len(marks))
    methods = Counter(d.method.split(",")[0].split(" (")[0] for d in designs if d.method)
    summary.add("Methods", ", ".join(f"{n} by {m}" for m, n in methods.items()))
    if info["decks or other"]:
        summary.add("Left out", f"{info['decks or other']} floor objects without a slab "
                    "property (decks)")
    if info["off a story level"]:
        summary.fail(f"{info['off a story level']} floor objects are not at a story level: "
                     "left out")
    if failing:
        summary.fail(f"{len(failing)} panels fail (red in the Panels sheet): see the Flags sheet")
    flagged = sum(1 for d in designs if d.flags)
    if flagged:
        summary.add("Panels with flags", flagged)
    summary.file("Workbook", workbook)
    summary.file("Slab schedule", schedule)
    if pdf:
        summary.file("Calculation report", pdf)
    else:
        summary.fail("The calculation report was not written (LaTeX): run sdt doctor")
    summary.file("Slab data for sdt plans", data)
    summary.show(popup=True)
    # what the command gives (compared by tools/etabs_run.py)
    return {"schedule": schedule_table(marks), "panels": panel_table(designs),
            "flags": flags_table(designs)}
