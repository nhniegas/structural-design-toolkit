"""``sdt model``: the stories, grids, columns and walls from the DXF (as ``sdt grids``), then
the framing of every floor: girders, beams, slabs and their loads.

The framings are worked out by ``framing`` (no ETABS): for each slab thickness of
the setup (or the one held) and each scheme, the girders between the columns, the
beams and the slab of every bay, at rule sizes. They are ranked by the concrete
they use; those that meet the frame action rules come first. The best few are
drawn side by side in one DXF and, when asked, each is built in a copy of the
model and analysed once (drift, periods, torsion). The one picked is built in the
model: its girders and beams, its floors (one-way where the bay is one-way), the
loads of each occupancy zone and a rigid diaphragm.

``sdt setup`` comes first: the section ranges, the slab thicknesses and the load
patterns are the setup's.
"""

from __future__ import annotations

import os

from etabs_api.core.connection import save_model
from etabs_api.workflows import framing as fr
from etabs_api.workflows import framing_plans as fp
from etabs_api.workflows import model_setup as ms

GIRDER_KINDS = (fr.GIRDER, fr.TRANSFER, fr.TIE)
DIAPHRAGM = "D1"
LIVE_KINDS = {"Reducible live (LIVERED)": "LIVERED", "Live, not reducible (LIVENRED)": "LIVENRED",
              "Roof live (LIVEROOF)": "LIVEROOF"}
SIDL_PATTERN = "SIDL"
DEFAULT_ZONE = "(no zone)"     # the slab outside every drawn zone
MARGINAL = 1.10                # a framing over its drift limit by no more than this is marginal


# =============================================================================
# SETTINGS (no ETABS)
# =============================================================================
def framing_settings(setup: dict, hold: str = "volume", held: float = 0.0,
                     max_depth: dict[str, float] | None = None, off_line: float = 300.0,
                     trimmer_from: float = 1000.0, max_cantilever: float = 3000.0,
                     long_cantilever: float = 8000.0) -> fr.FramingSettings:
    """The framing inputs from the setup settings (``model_setup.merge_settings``)."""
    sections = setup["sections"]

    def sizes(prefix: str, part: str) -> list[float]:
        return [float(v) for v in ms._sizes(sections.get(prefix, {}).get(part, []))]

    girder_widths, girder_depths = sizes("G", "width"), sizes("G", "depth")
    beam_widths, beam_depths = sizes("B", "width"), sizes("B", "depth")
    tie_widths, tie_depths = sizes("FTB", "width"), sizes("FTB", "depth")
    if not girder_depths or not girder_widths:
        raise ValueError("The setup has no girder sizes (G): run sdt setup with a G range.")
    return fr.FramingSettings(
        fy=ms.rebar_properties(setup["section_rebar_ksi"])["fy"],
        girder_widths=girder_widths, girder_depths=girder_depths,
        beam_widths=beam_widths or girder_widths, beam_depths=beam_depths or girder_depths,
        tie_widths=tie_widths or girder_widths, tie_depths=tie_depths or girder_depths,
        thicknesses=[float(t) for t in setup.get("slabs", {}).get("thickness", [])] or [150.0],
        hold=hold, held=float(held), max_depth=dict(max_depth or {}), off_line=float(off_line),
        trimmer_from=float(trimmer_from), max_cantilever=float(max_cantilever),
        long_cantilever=float(long_cantilever))


def section_name(member: fr.Member, setup: dict) -> str:
    """The setup name of a member's section: G_ for girders, FTB_ for tie beams, B_ for
    the others."""
    concrete = ms.grade_name("C", setup["section_concrete_ksi"][0])
    rebar = ms.grade_name("G", setup["section_rebar_ksi"])
    prefix = "FTB" if member.kind == fr.TIE else "G" if member.kind in GIRDER_KINDS else "B"
    return f"{prefix}_{member.width:.0f}X{member.depth:.0f}_{concrete}_{rebar}"


def slab_name(thickness: float, setup: dict, one_way: bool) -> str:
    concrete = ms.grade_name("C", ms.area_concrete_ksi(setup, "slabs"))
    name = f"S_{thickness:g}_{concrete}"
    return name + ms.ONE_WAY_SUFFIX if one_way else name


# =============================================================================
# SLAB PIECES (no ETABS)
# =============================================================================
def slab_pieces(floor: fr.Floor, framing: fr.FloorFraming
                ) -> list[tuple[list[tuple[float, float]], bool, str]]:
    """The floor objects of a framing: each piece of slab between the members, walls
    and slab edge, without the openings, cut by the load zones. (outline, one-way,
    zone name or DEFAULT_ZONE). An opening inside a piece is in ``slab_holes``."""
    return [(points, one_way, zone) for points, one_way, zone, _ in _pieces(floor, framing)]


def slab_holes(floor: fr.Floor, framing: fr.FloorFraming) -> list[list[tuple[float, float]]]:
    """The openings that fall inside a slab piece (not on its edge): each is drawn in
    ETABS as an opening over the floor."""
    return [hole for *_, holes in _pieces(floor, framing) for hole in holes]


def _pieces(floor: fr.Floor, framing: fr.FloorFraming):
    """(outline, one-way, zone, holes) of each slab piece."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union

    lines = [LineString(m.ends()) for m in framing.members] + fr._wall_lines(floor.walls)
    slab = fr.floor_polygon(floor)
    if slab is not None:
        lines.append(slab.boundary)
    holes = unary_union([Polygon(p).buffer(0) for p in floor.openings if len(p) >= 3])
    zones = [(name, Polygon(p).buffer(0)) for name, p in floor.zones if len(p) >= 3]
    one_way = [(bay.polygon, option.kind in (fr.ONE_WAY_X, fr.ONE_WAY_Y, fr.OVERHANG))
               for bay, option in framing.choices]
    out = []
    for face in polygonize(unary_union(lines)):
        if face.area < 1e4:
            continue
        inside = face.representative_point()
        if slab is not None and not slab.buffer(fr.NEAR).contains(inside):
            continue
        owner = next((way for shape, way in one_way if shape.buffer(fr.NEAR).contains(inside)),
                     None)
        if owner is None:
            continue          # not a bay of the framing (a bay it could not frame)
        piece = face.difference(holes) if not holes.is_empty else face
        parts = []
        rest = piece
        for name, zone in zones:
            part = rest.intersection(zone)
            if part.area > 1e4:
                parts.append((part, name))
                rest = rest.difference(zone)
        if rest.area > 1e4:
            parts.append((rest, DEFAULT_ZONE))
        for shape, name in parts:
            for polygon in getattr(shape, "geoms", [shape]):
                if polygon.geom_type != "Polygon" or polygon.area < 1e4:
                    continue
                points = list(polygon.simplify(1.0).exterior.coords)[:-1]
                inner = [list(ring.coords)[:-1] for ring in polygon.interiors]
                if len(points) >= 3:
                    out.append((points, owner, name, inner))
    return out


# =============================================================================
# DRAWING (no ETABS)
# =============================================================================
def _joint(x: float, y: float) -> str:
    return f"{round(x)},{round(y)}"


def plan_of(floor: fr.Floor, framing: fr.FloorFraming, elevation: float = 0.0,
            title: str = "") -> fp.FloorPlan:
    """A FloorPlan of ``framing_plans`` to draw a framing with."""
    beams = []
    for index, m in enumerate(framing.members, start=1):
        beams.append(fp.PlanBeam(f"{m.kind}{index} {m.width:.0f}x{m.depth:.0f}", m.x1, m.y1,
                                 m.x2, m.y2, m.width, _joint(m.x1, m.y1), _joint(m.x2, m.y2),
                                 girder=m.kind in GIRDER_KINDS))
    columns = [fp.PlanColumn(f"C{i}", s.x, s.y, s.size_y, s.size_x, top=_joint(s.x, s.y))
               for i, s in enumerate(floor.supports, start=1)]
    above = [fp.PlanColumn(f"P{i}", s.x, s.y, s.size_y, s.size_x, bottom=_joint(s.x, s.y))
             for i, s in enumerate(floor.planted, start=1)]
    walls = [fp.PlanWall(f"W{i}", w.x1, w.y1, w.x2, w.y2, w.thickness)
             for i, w in enumerate(floor.walls, start=1)]
    return fp.FloorPlan(framing.label, elevation, beams, columns, walls, above, title=title)


def write_candidates_dxf(ranked: list[fr.Candidate], floors: list[fr.Floor], path: str,
                         grids=None, scale: float = 100.0, analysed: dict | None = None) -> str:
    """Every candidate in a row of plans, one plan per typical floor, best first.
    Under each plan: the candidate, its slab, its volume and its problems."""
    options = fp.PlanOptions(grids=bool(grids), scale=scale)
    doc = fp._new_document(options)
    msp = doc.modelspace()
    height = options.text_height
    by_story = {f.story: f for f in floors}
    plans = [[plan_of(by_story[x.story], x) for x in c.floors] for c in ranked]
    box = fp.extent([p for row in plans for p in row])
    room = 16.0 * height if grids else 4.0 * height
    pitch_x = (box[2] - box[0]) + 2.0 * room + 10.0 * height
    pitch_y = (box[3] - box[1]) + 2.0 * room + 22.0 * height
    for row, (candidate, row_plans) in enumerate(zip(ranked, plans)):
        for column, (framing, plan) in enumerate(zip(candidate.floors, row_plans)):
            offset = (column * pitch_x, -row * pitch_y)
            if grids:
                fp.draw_grids(msp, grids, box, offset, options)
            fp.draw_floor(msp, plan, offset, options, set())
            for bay, option in framing.choices:
                point = bay.polygon.representative_point()
                fp._text(msp, option.kind.upper(), (point.x + offset[0], point.y + offset[1]),
                         0.8 * height, layer=fp.LAYER_TEXT)
            middle = (box[0] + box[2]) / 2.0 + offset[0]
            base = box[1] - room - 4.0 * height + offset[1]
            fp._text(msp, f"OPTION {row + 1} - {framing.label} FRAMING PLAN", (middle, base),
                     1.6 * height)
            lines = [f"{candidate.name}; {framing.thickness:.0f} mm slab",
                     f"floor {framing.volume:.1f} m3 x {framing.copies}; whole option "
                     f"{candidate.volume:.1f} m3, formwork {candidate.formwork:.0f} m2, "
                     f"{candidate.count} members"]
            if analysed and row in analysed:
                lines.append(analysed[row])
            lines += [f"problem: {p}" for p in framing.problems[:3]]
            for index, text in enumerate(lines):
                fp._text(msp, text, (box[0] + offset[0], base - (2.6 + 1.6 * index) * height),
                         0.9 * height, align="MIDDLE_LEFT")
    doc.saveas(path)
    return path


def candidates_table(ranked: list[fr.Candidate], analysed: dict | None = None) -> list[str]:
    """The ranking as text lines."""
    out = [f"{'#':>2}  {'Framing':<46} {'Volume m3':>10} {'Formwork m2':>12} {'Members':>8}  "
           "Frame action / problems"]
    for index, c in enumerate(ranked, start=1):
        state = ("problems: " + "; ".join(c.problems[:2])) if not c.valid else (
            "meets the rules" if c.frames else "rules: " + "; ".join(c.frame_findings[:2]))
        out.append(f"{index:>2}  {c.name:<46} {c.volume:>10.1f} {c.formwork:>12.0f} "
                   f"{c.count:>8}  {state}")
        if analysed and index - 1 in analysed:
            out.append(f"{'':>4}{analysed[index - 1]}")
    return out


# =============================================================================
# ETABS
# =============================================================================
def story_tops(model) -> dict[str, float]:
    from etabs_api.core.helpers import as_list

    got = model.Story.GetStories()
    return {str(n): float(z) for n, z in zip(as_list(got[1]), as_list(got[2]))}


def remove_framing(model, stories: set[str], log: ms.SetupLog) -> None:
    """Delete the beams and floors of ``stories`` (a framing built again)."""
    from etabs_api.workflows.grid_column_model import _table

    for row in _table(model, "Beam Object Connectivity"):
        if str(row.get("Story")) in stories and log.check(
                model.FrameObj.Delete(str(row["UniqueName"])), "removing a beam"):
            log.done("beams removed")
    for row in _table(model, "Floor Object Connectivity"):
        if str(row.get("Story")) in stories and log.check(
                model.AreaObj.Delete(str(row["UniqueName"])), "removing a floor"):
            log.done("floors removed")


def existing_framing(model) -> set[str]:
    """The stories that already have beams or floors."""
    from etabs_api.workflows.grid_column_model import _table

    return {str(r.get("Story")) for key in ("Beam Object Connectivity", "Floor Object Connectivity")
            for r in _table(model, key) if r.get("Story") not in (None, "")}


def build_framing(model, candidate: fr.Candidate, floors: list[fr.Floor], setup: dict,
                  loads: dict[str, tuple[float, float, str]], log: ms.SetupLog,
                  progress=None) -> None:
    """Build a candidate in the open model (N-mm): the members of every floor of each
    typical group, the floors with their sections, loads and diaphragm. ``loads``:
    zone name -> (SIDL kPa, live kPa, live pattern)."""
    from etabs_api.workflows.grid_column_model import _table

    say = progress or (lambda text: None)
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    units = int(model.GetPresentUnits())
    model.SetPresentUnits(ms.UNITS_N_MM)
    try:
        tops = story_tops(model)
        have = {str(r["Name"]) for r in _table(model, "Frame Section Property Definitions - Summary")}
        slabs = {str(r["Name"]) for r in _table(model, ms.SLAB_TABLE)}
        by_story = {f.story: f for f in floors}
        rebar = ms.grade_name("G", setup["section_rebar_ksi"])
        concrete = ms.grade_name("C", setup["section_concrete_ksi"][0])
        slab_concrete = ms.grade_name("C", ms.area_concrete_ksi(setup, "slabs"))
        shell = ms.SLAB_SHELL_TYPES.get(setup.get("slabs", {}).get("type", "Membrane"), 3)
        model.Diaphragm.SetDiaphragm(DIAPHRAGM, False)
        for framing in candidate.floors:
            floor = by_story[framing.story]
            pieces = slab_pieces(floor, framing)
            holes = slab_holes(floor, framing)
            for story in framing.stories:
                if story not in tops:
                    log.problems.append(f"story {story}: not in the model")
                    continue
                z = tops[story]
                say(f"Framing {story}")
                for member in framing.members:
                    name = section_name(member, setup)
                    if name not in have and ms.create_section(model.PropFrame, {
                            "name": name, "kind": "beam", "width": member.width,
                            "depth": member.depth, "material": concrete, "rebar": rebar}, log):
                        have.add(name)
                        log.done("frame sections")
                    made = model.FrameObj.AddByCoord(member.x1, member.y1, z, member.x2,
                                                     member.y2, z, "", name, "", "Global")
                    if log.check(made, f"{fr.KIND_NAMES[member.kind]} on {story}"):
                        log.done(f"{fr.KIND_NAMES[member.kind]}s")
                for points, one_way, zone in pieces:
                    section = slab_name(framing.thickness, setup, one_way)
                    if section not in slabs:
                        if one_way and slab_name(framing.thickness, setup, False) in slabs:
                            section = slab_name(framing.thickness, setup, False)
                        elif log.check(model.PropArea.SetSlab(
                                section, 0, shell, slab_concrete, float(framing.thickness)),
                                f"slab section {section}"):
                            slabs.add(section)
                            if one_way:
                                ms.set_one_way_slabs(model, {section: True}, log)
                    xs, ys = [p[0] for p in points], [p[1] for p in points]
                    made = model.AreaObj.AddByCoord(len(points), xs, ys, [z] * len(points), "",
                                                    section, "", "Global")
                    if not log.check(made, f"floor on {story}"):
                        continue
                    name = str(made[3]) if len(made) > 3 else ""
                    log.done("floors")
                    log.check(model.AreaObj.SetDiaphragm(name, DIAPHRAGM), f"diaphragm {name}")
                    sidl, live, pattern = loads.get(zone) or loads.get(DEFAULT_ZONE) or (0, 0, "")
                    for value, pat in ((sidl, SIDL_PATTERN), (live, pattern)):
                        if value and pat:
                            # kPa to N/mm2; direction 10 is gravity, the value downward
                            log.check(model.AreaObj.SetLoadUniform(
                                name, pat, value / 1000.0, 10, True, "Global", 0),
                                f"{pat} on {name}")
                for points in holes:          # an opening inside a slab piece
                    xs, ys = [p[0] for p in points], [p[1] for p in points]
                    made = model.AreaObj.AddByCoord(len(points), xs, ys, [z] * len(points), "",
                                                    "Default", "", "Global")
                    if log.check(made, f"opening on {story}"):
                        name = str(made[3]) if len(made) > 3 else ""
                        if log.check(model.AreaObj.SetOpening(name, True), f"opening {name}"):
                            log.done("openings")
    finally:
        model.SetPresentUnits(units)



# =============================================================================
# THE LATERAL SCREENING (ETABS)
# =============================================================================
def option_path(model_path: str, number: int) -> str:
    return f"{os.path.splitext(model_path)[0]} - OPTION {number}.EDB"


def screen_option(connector, model_path: str, number: int, candidate: fr.Candidate,
                  floors: list[fr.Floor], setup: dict, loads: dict, progress=None) -> dict:
    """Build one candidate in a copy of the model (``<model> - OPTION n.EDB``), analyse
    it once and read its drift (strength-level stiffness, NSCP 406.6.3.1.1; the
    static drift patterns), the periods of the drift patterns and the largest
    ratio of the maximum to the average story drift. The model is opened again."""
    from etabs_api.workflows.analysis_forces import ensure_analysis
    from etabs_api.workflows.drift_check import (
        CORNERS,
        STATIC,
        drift_load_cases,
        drift_ratios,
        run_drift,
        stiffness_levels,
    )
    from etabs_api.workflows.irregularity import _results, load_cases

    say = progress or (lambda text: None)
    model = connector.sap_model
    copy = option_path(model_path, number)
    out = {"path": copy}
    try:
        model.File.OpenFile(model_path)
        save_model(model, copy)
        log = ms.SetupLog()
        remove_framing(model, {s for f in candidate.floors for s in f.stories}, log)
        say(f"Option {number}: building it in a copy of the model")
        build_framing(model, candidate, floors, setup, loads, log, say)
        save_model(model)
        out["problems"] = list(log.problems)
        say(f"Option {number}: analysis and drift")
        cases = drift_load_cases(connector)
        report = run_drift(connector, CORNERS, levels=[stiffness_levels()[1]], seismic=STATIC,
                           drift_cases=cases or None,
                           progress=lambda text: say(f"Option {number}: {text}"))
        ratios = drift_ratios(report, cases or None)
        if ratios:
            (story, direction), (over, combo, _) = max(ratios.items(), key=lambda i: i[1][0])
            out.update(drift=over, story=story, direction=direction, combo=combo)
        out["periods"] = {case: period for level in report.level_periods.values()
                          for case, period in level.items()}
        seismic, _, _ = load_cases(model)
        if seismic:
            ensure_analysis(connector, seismic)
            table = _results(connector, "Story Max Over Avg Drifts", seismic)
            if len(table):
                ratio = (table["Max Drift"].astype(float) / table["Avg Drift"].astype(float)
                         .where(lambda v: v > 0))
                out["torsion"] = float(ratio.max())
        save_model(model)
    except Exception as error:  # noqa: BLE001 - the option is listed without results
        out["error"] = str(error)
    finally:
        try:
            model.File.OpenFile(model_path)
        except Exception:  # noqa: BLE001
            pass
    return out


def screening_text(result: dict) -> str:
    """One line for the table and the drawing."""
    if "error" in result:
        return f"ETABS: not analysed ({result['error'][:80]})"
    parts = []
    if "drift" in result:
        over = result["drift"]
        state = ("passes" if over <= 1.0 + 1e-9 else
                 "MARGINAL" if over <= MARGINAL else "FAILS")
        parts.append(f"drift {over:.2f} of the limit at {result['story']} "
                     f"{result.get('direction') or ''} ({state})".replace("  ", " "))
    if result.get("periods"):
        parts.append("T " + ", ".join(f"{c} {t:.2f} s" for c, t in sorted(result["periods"].items())))
    if "torsion" in result:
        parts.append(f"max / avg drift {result['torsion']:.2f}")
    return "ETABS: " + "; ".join(parts) if parts else "ETABS: no drift results"


def screening_rank(result: dict | None) -> int:
    """0 passes drift, 1 marginal, 2 fails, 3 not analysed."""
    if not result or "drift" not in result:
        return 3
    over = result["drift"]
    return 0 if over <= 1.0 + 1e-9 else 1 if over <= MARGINAL else 2


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
CANTILEVER_FIELD = "Longest cantilever beam from a column out to a slab edge (mm)"
LONG_CANTILEVER_FIELD = "Longest cantilever beam where an edge has no support over 12 m (mm)"
TIES_ONLY = "Tie beams only (slab on grade): FTB sections along the girder lines"
SLAB_TO_GIRDERS = "A slab up to its outer girders, like any floor"
HOLD_VOLUME = "Least concrete: try every slab thickness of the setup"
HOLD_THICKNESS = "Hold a slab thickness: the beam spacing follows from it"
HOLD_SPACING = "Hold a beam spacing: the slab thickness follows from it"


def _defaults_path() -> str:
    from utilities.user_settings import settings_path as user_settings_path

    return user_settings_path("model_framing.json")


def ask_framing(title: str, floors: list[fr.Floor], setup: dict) -> dict | None:
    """How to frame: what is held, the architect's depth limits, the tolerances, the
    loads of each zone and how many options to draw and analyse."""
    from utilities._gui_helpers import choose_for_each, enter_values, select_option, show_warning

    saved = ms.load_settings(_defaults_path()) or {}
    hold = select_option(title, "Slab thickness and beam spacing depend on each other. "
                         "Which is held?", [HOLD_VOLUME, HOLD_THICKNESS, HOLD_SPACING],
                         default_index={"volume": 0, "thickness": 1, "spacing": 2}.get(
                             saved.get("hold", "volume"), 0))
    if hold is None:
        return None
    mode = "volume" if hold == HOLD_VOLUME else "thickness" if hold == HOLD_THICKNESS else "spacing"
    fields = {}
    if mode == "thickness":
        fields["Slab thickness held (mm)"] = str(saved.get("thickness", 150))
    if mode == "spacing":
        fields["Largest beam spacing (mm)"] = str(saved.get("spacing", 3000))
    fields["A column this far off a line still frames into it (mm)"] = str(
        saved.get("off_line", 300))
    fields["Openings this large or larger get beams around them (mm)"] = str(
        saved.get("trimmer_from", 1000))
    fields[CANTILEVER_FIELD] = str(saved.get("max_cantilever", 3000))
    fields[LONG_CANTILEVER_FIELD] = str(saved.get("long_cantilever", 8000))
    fields["Options to draw"] = str(saved.get("draw", 3))
    fields["Options to build and analyse in ETABS (0 for none)"] = str(saved.get("analyse", 2))
    for floor in floors:
        fields[f"Deepest member allowed at {floor.story} (mm, blank: no limit)"] = str(
            saved.get("depths", {}).get(floor.story, ""))
    typed = enter_values(title, "Framing of the floors. The depth limit is the architect's, "
                         "one per floor.", list(fields), fields)
    if typed is None:
        return None
    try:
        held = (float(typed["Slab thickness held (mm)"]) if mode == "thickness" else
                float(typed["Largest beam spacing (mm)"]) if mode == "spacing" else 0.0)
        off_line = float(typed["A column this far off a line still frames into it (mm)"])
        trimmer_from = float(typed["Openings this large or larger get beams around them (mm)"])
        max_cantilever = float(typed[CANTILEVER_FIELD])
        long_cantilever = float(typed[LONG_CANTILEVER_FIELD])
        draw = max(1, int(float(typed["Options to draw"])))
        analyse = max(0, int(float(typed["Options to build and analyse in ETABS (0 for none)"])))
        depths = {}
        for floor in floors:
            text = typed[f"Deepest member allowed at {floor.story} (mm, blank: no limit)"].strip()
            if text:
                depths[floor.story] = float(text)
    except ValueError:
        show_warning("Every value must be a number.", title=title)
        return None
    zones = sorted({name for f in floors for name, _ in f.zones}) + [DEFAULT_ZONE]
    loads_saved = saved.get("loads", {})
    load_fields = {}
    for zone in zones:
        sidl, live, _ = loads_saved.get(zone, (2.0, 2.0, "LIVERED"))
        load_fields[f"{zone}: superimposed dead load (kPa)"] = f"{sidl:g}"
        load_fields[f"{zone}: live load (kPa)"] = f"{live:g}"
    typed = enter_values(title, "Loads of each occupancy zone (S-LOAD), typed once; "
                         f"{DEFAULT_ZONE} is the slab outside every zone.",
                         list(load_fields), load_fields)
    if typed is None:
        return None
    kinds = choose_for_each(title, "Live load pattern of each zone.", zones, list(LIVE_KINDS),
                            {z: next((k for k, v in LIVE_KINDS.items()
                                      if v == loads_saved.get(z, (0, 0, "LIVERED"))[2]),
                                     list(LIVE_KINDS)[0]) for z in zones})
    if kinds is None:
        return None
    try:
        loads = {z: (float(typed[f"{z}: superimposed dead load (kPa)"]),
                     float(typed[f"{z}: live load (kPa)"]), LIVE_KINDS[kinds[z]]) for z in zones}
    except ValueError:
        show_warning("Every load must be a number.", title=title)
        return None
    ms.save_settings({"hold": mode, "thickness": held if mode == "thickness" else
                      saved.get("thickness", 150),
                      "spacing": held if mode == "spacing" else saved.get("spacing", 3000),
                      "off_line": off_line, "trimmer_from": trimmer_from, "draw": draw,
                      "max_cantilever": max_cantilever, "long_cantilever": long_cantilever,
                      "analyse": analyse, "depths": {**saved.get("depths", {}), **depths},
                      "loads": {**loads_saved, **{z: list(v) for z, v in loads.items()}}},
                     _defaults_path())
    return {"settings": framing_settings(setup, mode, held, depths, off_line, trimmer_from,
                                         max_cantilever, long_cantilever),
            "draw": draw, "analyse": analyse, "loads": loads}


def run_model() -> str | None:
    """Entry point of ``sdt model``: the grids, columns and walls (as ``sdt grids``),
    then the framing options, drawn and ranked; the one picked is built."""
    from etabs_api.workflows import grid_column_model as gcm

    title = "Model from DXF"
    inputs = gcm.ask_grid_inputs(title, floor_layers=True)
    if inputs is None:
        return None
    applied = gcm.apply_grid_inputs(title, inputs)
    if applied is None:
        return None
    building, changes, log, path = applied
    summary = gcm.grids_summary("sdt model", inputs["dxf_path"], building, changes, log, path)
    floors = fr.floors_from_building(building)
    if floors:
        _frame(title, path, building, floors, summary)
    else:
        summary.note("The drawing has no floors to frame.")
    summary.show(popup=True)
    return path


def _frame(title: str, path: str, building, floors: list[fr.Floor], summary) -> bool | None:
    """Ask, rank, draw, screen and build the framing; notes go to ``summary``."""
    from design.concrete.workflow import attach_etabs
    from utilities._gui_helpers import LoadingWindow, select_option, show_warning
    from utilities.output_folders import DRAWINGS, beside

    go = select_option(title, "The stories, grids, columns and walls are in the model. Frame "
                       "the floors now (girders, beams, slabs and their loads)?",
                       ["Yes, work out the framing options", "No, only the grids and columns"])
    if go is None or not go.startswith("Yes"):
        return None
    stored = ms.load_settings(ms.settings_path(path))
    if stored is None:
        show_warning("The model has no setup (<model>.setup.json). Run sdt setup first: the "
                     "framing takes its sizes, slab thicknesses and load patterns from it.",
                     title=title)
        summary.note("Framing not done: run sdt setup first, then sdt model again.")
        return None
    setup = ms.merge_settings(stored)
    if floors and not floors[0].outline and any(f.outline for f in floors[1:]):
        ties = select_option(title, f"The lowest floor, {floors[0].story}, has no slab outline "
                             "while the floors above have one. How is it framed?",
                             [TIES_ONLY, SLAB_TO_GIRDERS])
        if ties is None:
            return None
        if ties == TIES_ONLY:
            floors = fr.floors_from_building(building, ground_ties=True)
    asked = ask_framing(title, floors, setup)
    if asked is None:
        return None
    settings = asked["settings"]
    with LoadingWindow("Framing options") as window:
        ranked = fr.candidates(floors, settings, window.update)
    if not ranked:
        show_warning("No framing could be worked out.", title=title)
        return None
    shown = ranked[:asked["draw"]]
    results: dict[int, dict] = {}
    connector = attach_etabs() if asked["analyse"] else None
    if connector is not None:
        with LoadingWindow("Framing options in ETABS") as window:
            for index, candidate in enumerate(shown[:asked["analyse"]]):
                if not candidate.valid:
                    continue
                results[index] = screen_option(connector, path, index + 1, candidate, floors,
                                               setup, asked["loads"], window.update)
        # among those analysed: passing drift first, then marginal, then by volume
        order = sorted(range(len(shown)), key=lambda i: (
            not shown[i].valid, not shown[i].frames,
            screening_rank(results.get(i)) if results else 0, shown[i].volume))
        shown = [shown[i] for i in order]
        results = {new: results[old] for new, old in enumerate(order) if old in results}
    texts = {i: screening_text(r) for i, r in results.items()}
    drawing = beside(path, " - framing options.dxf", DRAWINGS)
    grids = [fp.PlanGrid(g.label, g.x1, g.y1, g.x2, g.y2) for g in building.grids]
    write_candidates_dxf(shown, floors, drawing, grids, analysed=texts)
    table = candidates_table(shown, texts)
    report = beside(path, " - framing options.txt")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write("\n".join(["FRAMING OPTIONS (sdt model)", f"Model: {path}", ""] + table
                               + [""] + [n for c in shown[:1] for f in c.floors
                                         for n in f.notes]) + "\n")
    summary.file("Framing options (drawing)", drawing)
    summary.file("Framing options (table)", report)
    labels = [f"{i + 1}. {c.name} - {c.volume:.1f} m3" + (" (problems)" if not c.valid else "")
              for i, c in enumerate(shown)]
    picked = select_option(title, "Framing options, best first (see the drawing and the "
                           "table beside the model):\n\n" + "\n".join(table)
                           + "\n\nWhich one is built in the model?",
                           labels + ["None: leave the model without framing"])
    if picked is None or picked.startswith("None"):
        summary.note("No framing built; the options are in the drawing.")
        return None
    candidate = shown[labels.index(picked)]
    connector = connector or attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    model.File.OpenFile(path)
    stories = {s for f in candidate.floors for s in f.stories}
    framed_now = existing_framing(model) & stories
    log = ms.SetupLog()
    if framed_now:
        answer = select_option(title, "These stories already have beams or floors: "
                               + ", ".join(sorted(framed_now)) + ". Replace them?",
                               ["Replace them", "Cancel"])
        if answer is None or not answer.startswith("Replace"):
            return None
        remove_framing(model, framed_now, log)
    with LoadingWindow("Building the framing") as window:
        build_framing(model, candidate, floors, setup, asked["loads"], log, window.update)
        save_model(model)
    summary.add("Framing built", candidate.name)
    summary.add("Concrete of the framing", f"{candidate.volume:.1f} m3 (formwork "
                                           f"{candidate.formwork:.0f} m2, {candidate.count} "
                                           "members)")
    for what, count in log.counts.items():
        summary.add(what[:1].upper() + what[1:], count)
    for problem in log.problems:
        summary.fail(problem)
    for problem in candidate.problems:
        summary.fail(problem)
    for finding in candidate.frame_findings:
        summary.note(f"Frame action: {finding}")
    for framing in candidate.floors:
        for note in framing.notes:
            summary.note(f"{framing.label}: {note}")
    summary.note("The sizes are rule sizes (NSCP Table 409.3.1.1; ACI Table 9.3.1.1): run "
                 "sdt design to check and resize them.")
    return True
