"""The full project report of a design loop (``sdt design``, "Write the full report").

One PDF for the whole project, in two parts as a design office files them:

* **Part A, Analysis**: the building and its 3D view, the framing plan of every
  floor with the names of its members, materials and sections, load patterns
  and cases, the seismic and wind criteria, the load combinations, the periods
  and base shears, the story drifts, the irregularities with the P-delta
  stability coefficient, and every check of the model.
* **Part B, Design**: the design basis, every beam and every column with its
  bars and ratios, and the concrete quantities.
* **Part C, Member calculations**: the calculation tables of each beam and each
  column, those of the beam and column reports.

The report is put together from what the loop already has (its last analysis,
its drift check, its final designs) and from the commands that read the
finished model (``sdt check``, ``sdt irregularity``, ``sdt quantities``,
``sdt plans``, ``sdt 3d``). The loads, cases and combinations come from the text
copy of the model that ETABS keeps beside it (``<model>.$et``).

``parse_model_text`` and ``build_report`` need no ETABS.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from utilities.output_folders import CALCULATIONS, output_path

QUESTION = "Write the full project report?"
YES, NO = "Yes, write the full report", "No"
FIELDS = {"Project": "project", "Location": "location", "Client": "client",
          "Prepared by": "engineer", "Slab on grade thickness (mm), 0 for none": "grade"}
COMBINATION_GROUPS = (
    ("ULS", "Strength (ULS)"), ("SLS", "Service (SLS)"), ("SSLC", "Special seismic (SSLC)"),
    ("DRIFT", "Seismic drift"), ("WDRIFT", "Wind drift"), ("DEF", "Deflection"),
    ("EQ_COMBO", "Seismic directions (100 % + 30 %)"),
    ("RSA_COMBO", "Spectrum directions (100 % + 30 %)"))
OTHER_GROUP = "Other combinations"


@dataclass
class ProjectInfo:
    """What the user types for the front page; every line may be left empty."""

    project: str = ""
    location: str = ""
    client: str = ""
    engineer: str = ""
    grade: float = 0.0          # mm, the slab on grade of the concrete quantities


@dataclass
class ModelText:
    """The definitions of a model, read from its text file."""

    stories: list[tuple[str, float | None, float]] = field(default_factory=list)   # top first
    patterns: list[tuple[str, str, str]] = field(default_factory=list)
    seismic: list[dict] = field(default_factory=list)
    wind: list[dict] = field(default_factory=list)
    cases: dict[str, str] = field(default_factory=dict)
    combos: dict[str, dict] = field(default_factory=dict)
    mass: list[tuple[str, str]] = field(default_factory=list)
    pdelta: list[tuple[str, str]] = field(default_factory=list)
    area_loads: list[tuple[str, str, float]] = field(default_factory=list)   # story, pattern, kPa

    @property
    def elevations(self) -> dict[str, float]:
        return {name: elevation for name, _, elevation in self.stories}


@dataclass
class ReportData:
    """Everything a report is written from. A part that is None is left out."""

    model_name: str
    info: ProjectInfo = field(default_factory=ProjectInfo)
    text: ModelText = field(default_factory=ModelText)
    findings: list = field(default_factory=list)
    analysis: object = None
    drift: object = None
    irregularity: object = None
    quantities: object = None
    beams: object = None             # the beam results (DataFrame)
    columns: object = None           # the column report (DataFrame)
    frames: object = None            # FRAME DATA of the model
    inputs: dict = field(default_factory=dict)
    smrf: bool = True
    plans: list[tuple[str, float, int, int, str]] = field(default_factory=list)
    view: str = ""                   # the picture of the 3D view
    beam_members: list = field(default_factory=list)       # MemberReport, for Part C
    column_members: list = field(default_factory=list)
    files: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _get(item, name, default=None):
    """An attribute of an object, or the key of a stored copy of it."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


# =============================================================================
# THE TEXT FILE OF THE MODEL
# =============================================================================
def model_text_path(model_path: str) -> str:
    return os.path.splitext(model_path)[0] + ".$et"


def _field(line: str, key: str, default: str = "") -> str:
    found = (re.search(r"(?<![A-Za-z])" + key + r'\s+"([^"]*)"', line)
             or re.search(r"(?<![A-Za-z])" + key + r"\s+(\S+)", line))
    return found.group(1) if found else default


def _section(text: str, title: str) -> list[str]:
    start = text.find("$ " + title)
    if start < 0:
        return []
    end = text.find("\n$ ", start + 2)
    block = text[start:end if end > 0 else len(text)]
    return [line.strip() for line in block.split("\n")[1:] if line.strip()]


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_model_text(text: str) -> ModelText:
    """The stories, load patterns, cases, combinations, mass source, P-delta loads
    and floor loads of a model, from its ETABS text file (lengths in mm, forces
    in N: the units the toolkit saves a model in)."""
    out = ModelText()
    rows = [(_field(l, "STORY"), _field(l, "HEIGHT"), _field(l, "ELEV"))
            for l in _section(text, "STORIES - IN SEQUENCE FROM TOP")]
    elevation = _number(rows[-1][2]) if rows else 0.0
    levels = {}
    for name, height, _ in reversed(rows):
        if height:
            elevation += _number(height)
        levels[name] = elevation
    out.stories = [(name, _number(height) if height else None, levels[name])
                   for name, height, _ in rows]
    for line in _section(text, "LOAD PATTERNS"):
        if line.startswith("LOADPATTERN"):
            out.patterns.append((_field(line, "LOADPATTERN"), _field(line, "TYPE"),
                                 _field(line, "SELFWEIGHT")))
        elif line.startswith("SEISMIC "):
            quoted = re.findall(r'"([^"]*)"', line)
            out.seismic.append({
                "pattern": quoted[0], "code": quoted[1] if len(quoted) > 1 else "",
                "direction": _field(line, "DIR"), "eccentricity": _field(line, "ECC"),
                "top": _field(line, "TOPSTORY"), "bottom": _field(line, "BOTTOMSTORY"),
                "period": _field(line, "PERIODTYPE"), "ct": _field(line, "CT"),
                "ca": _field(line, "Ca"), "cv": _field(line, "Cv"), "i": _field(line, "I"),
                "r": _field(line, "R"), "source": _field(line, "SOURCETYPE"),
                "distance": _field(line, "SOURCEDIST"), "soil": _field(line, "SOILTYPE"),
                "zone": _field(line, "Z")})
        elif line.startswith("WIND "):
            quoted = re.findall(r'"([^"]*)"', line)
            out.wind.append({
                "pattern": quoted[0], "code": quoted[1] if len(quoted) > 1 else "",
                "speed": _field(line, "SPEED"), "exposure": _field(line, "EXPOSURE"),
                "kzt": _field(line, "KZT"), "gust": _field(line, "GUSTFACTOR"),
                "kd": _field(line, "KD"), "top": _field(line, "TOPSTORY"),
                "bottom": _field(line, "BOTTOMSTORY")})
    for line in _section(text, "LOAD CASES"):
        if re.match(r'LOADCASE\s+"[^"]*"\s+TYPE', line):
            out.cases[_field(line, "LOADCASE")] = _field(line, "TYPE")
    for line in _section(text, "LOAD COMBINATIONS"):
        name = _field(line, "COMBO")
        combo = out.combos.setdefault(name, {"type": "", "terms": []})
        if re.match(r'COMBO\s+"[^"]*"\s+(?:DESIGN\s+"[^"]*"\s+(?:COMBOTYPE\s+"[^"]*"\s+)?)?TYPE',
                    line) or (" TYPE " in line and not combo["type"]):
            combo["type"] = _field(line, "TYPE")
        for key in ("LOADCASE", "LOADCOMBO"):
            if key + " " in line:
                combo["terms"].append((_number(_field(line, "SF"), 1.0), _field(line, key)))
    for line in _section(text, "MASS SOURCE"):
        if line.startswith("MASSSOURCELOAD"):
            out.mass.append((re.findall(r'"([^"]*)"', line)[1], line.split()[-1]))
    for line in _section(text, "ANALYSIS OPTIONS"):
        if line.startswith("PDELTA") and " LOAD " in line:
            out.pdelta.append((_field(line, "LOAD"), _field(line, "FACTOR")))
    seen: dict[tuple[str, str], float] = {}
    for line in _section(text, "SHELL OBJECT LOADS"):
        if 'TYPE "UNIFF"' in line:
            quoted = re.findall(r'"([^"]*)"', line)
            key = (quoted[1], _field(line, "LC"))
            seen[key] = max(seen.get(key, 0.0), _number(_field(line, "FVAL")) * 1000.0)
    out.area_loads = [(story, pattern, value) for (story, pattern), value in seen.items()]
    return out


def combination_group(name: str) -> str:
    for prefix, label in sorted(COMBINATION_GROUPS, key=lambda g: -len(g[0])):
        if str(name).startswith(prefix):
            return label
    return OTHER_GROUP


def combination_terms(combo: dict) -> str:
    out = []
    for factor, item in combo["terms"]:
        out.append(f"{'-' if factor < 0 else '+'} {abs(factor):g} {item}")
    text = " ".join(out)
    return text[2:] if text.startswith("+ ") else text


def listed_combinations(combos: dict[str, dict]) -> dict[str, list[str]]:
    """The combinations a report lists, by group. Envelopes are left out: they
    hold no factor of their own, only the names of the combinations above."""
    groups: dict[str, list[str]] = {}
    for name, combo in combos.items():
        if str(combo.get("type", "")).lower().startswith("envelope"):
            continue
        groups.setdefault(combination_group(name), []).append(name)
    return groups


# =============================================================================
# PICTURES
# =============================================================================
INK, AMBER_DARK = "#1C1E24", "#B36B00"


def draw_plan(floor, path: str) -> str:
    """A framing plan as a picture: beams at their width with their names, the
    columns below the floor with their marks. ``floor``: a ``framing_plans.FloorPlan``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from etabs_api.workflows import framing_plans as fp

    fig, ax = plt.subplots(figsize=(11.0, 6.6))
    trims = fp.beam_trims(floor)
    for wall in floor.walls:
        outline = fp.wall_outline(wall)
        if outline:
            ax.fill([p[0] for p in outline], [p[1] for p in outline], color="#5B6068")
    for beam in floor.beams:
        ends = fp.trimmed_ends(beam, trims.get(beam.name, (0.0, 0.0)))
        if ends is None:
            continue
        (x1, y1), (x2, y2) = ends
        length = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        width = beam.width if beam.width > 0 else 60.0
        nx, ny = -uy * width / 2.0, ux * width / 2.0
        ax.fill([x1 + nx, x2 + nx, x2 - nx, x1 - nx], [y1 + ny, y2 + ny, y2 - ny, y1 - ny],
                facecolor="#ECEEF1" if beam.girder else "#F7F8F9", edgecolor=INK,
                linewidth=0.7 if beam.girder else 0.45)
        side = 1.0 if (ux > 1e-9 or (abs(ux) <= 1e-9 and uy > 0)) else -1.0
        offset = width / 2.0 + 170.0
        ax.text((x1 + x2) / 2.0 - uy * offset * side, (y1 + y2) / 2.0 + ux * offset * side,
                beam.name, rotation=fp.readable_angle(ux, uy), rotation_mode="anchor",
                ha="center", va="center", fontsize=5.6, color=INK)
    for column in floor.columns:
        if column.circular:
            ax.add_patch(plt.Circle((column.x, column.y), column.depth / 2.0, color=INK))
        else:
            outline = fp.column_outline(column)
            ax.fill([p[0] for p in outline], [p[1] for p in outline], color=INK)
        ax.text(column.x + column.width / 2.0 + 130, column.y + column.depth / 2.0 + 130,
                fp.column_mark(column.name), fontsize=6.4, fontweight="bold", color=AMBER_DARK,
                ha="left", va="bottom")
    ax.set_aspect("equal")
    ax.margins(0.04)
    ax.axis("off")
    fig.savefig(path, dpi=190, bbox_inches="tight", pad_inches=0.04, facecolor="white")
    plt.close(fig)
    return path


def _box_faces(bar) -> list[list[tuple[float, float, float]]]:
    """The six faces of a member solid (a round one as a square of its diameter)."""
    from etabs_api.workflows.model_3d import _cross, _unit, square_up

    axis = _unit(tuple(b - a for a, b in zip(bar.start, bar.end)))
    if axis is None:
        return []
    up = square_up(axis, _unit(bar.up) or (0.0, 0.0, 1.0))
    side = _cross(up, axis)
    corners = []
    for point in (bar.start, bar.end):
        ring = []
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            ring.append(tuple(p + a * bar.width / 2.0 * s + b * bar.depth / 2.0 * u
                              for p, s, u in zip(point, side, up)))
        corners.append(ring)
    low, high = corners
    faces = [low, high]
    for i in range(4):
        j = (i + 1) % 4
        faces.append([low[i], low[j], high[j], high[i]])
    return faces


def draw_view(model, path: str) -> str:
    """A 3D view of the model as a picture. ``model``: a ``model_3d.Model3D``
    (concrete only; its bars and stirrups, if any, are not drawn)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    from etabs_api.workflows.model_3d import BEAM, COLUMN, FOOTING, REBAR, SLAB, TIE_BEAM, WALL

    colours = {COLUMN: "#AEB3BC", BEAM: "#D9DCE1", WALL: "#C2C5CB", TIE_BEAM: "#D0C7BB",
               SLAB: "#EEF0F2", FOOTING: "#CFC3B4"}
    fig = plt.figure(figsize=(11.0, 7.6))
    ax = fig.add_subplot(projection="3d")
    points = []
    for bar in model.bars:
        if bar.kind == REBAR:
            continue
        faces = _box_faces(bar)
        if faces:
            ax.add_collection3d(Poly3DCollection(faces, facecolor=colours.get(bar.kind, "#D9DCE1"),
                                                 edgecolor=INK, linewidths=0.3))
            points += faces[0] + faces[1]
    for plate in model.plates:
        top = [(x, y, plate.top) for x, y in plate.outline]
        bottom = [(x, y, plate.top - plate.thickness) for x, y in plate.outline]
        faces = [top, bottom] + [[top[i], top[(i + 1) % len(top)], bottom[(i + 1) % len(top)],
                                  bottom[i]] for i in range(len(top))]
        ax.add_collection3d(Poly3DCollection(
            faces, facecolor=colours.get(plate.kind, "#EEF0F2"), edgecolor=INK, linewidths=0.25,
            alpha=0.55 if plate.kind == SLAB else 1.0))
        points += top + bottom
    if points:
        xs, ys, zs = zip(*points)
        ax.set_xlim(min(xs), max(xs))
        ax.set_ylim(min(ys), max(ys))
        ax.set_zlim(min(zs), max(zs))
        ax.set_box_aspect((max(xs) - min(xs) or 1.0, max(ys) - min(ys) or 1.0,
                           max(zs) - min(zs) or 1.0))
    ax.view_init(elev=24, azim=-58)
    ax.set_axis_off()
    fig.savefig(path, dpi=170, bbox_inches="tight", pad_inches=0.0, facecolor="white")
    plt.close(fig)
    return path


# =============================================================================
# THE REPORT (LaTeX)
# =============================================================================
def _tex(value) -> str:
    from utilities._calc_report import latex_text

    return latex_text(value)


def _table(headers, rows, spec, size="small", long=False) -> str:
    from utilities._calc_report import status_cell

    head = " & ".join(r"\bfseries " + _tex(h) for h in headers) + r" \\"
    body = [" & ".join(status_cell(c) for c in row) + r" \\ \hline" for row in rows]
    if not body:
        return r"{\small\sdtsoft{None.}}\par\vspace{2mm}"
    if long:
        return "\n".join([
            r"{\%s\setlength{\tabcolsep}{4pt}\begin{longtable}{@{}%s@{}}" % (size, spec),
            r"\rowcolor{sdtband}", head, r"\endhead", *body, r"\end{longtable}}"])
    return "\n".join([
        r"\noindent{\%s\setlength{\tabcolsep}{5pt}\begin{tabular}{@{}%s@{}}" % (size, spec),
        r"\rowcolor{sdtband}", head, *body, r"\end{tabular}}\par\vspace{2mm}"])


def _figure(name: str, width: str, caption: str) -> str:
    return "\n".join([
        r"\begin{center}", r"\includegraphics[width=%s\textwidth]{%s}" % (width, name),
        r"\par\vspace{1mm}{\footnotesize\sdtsoft{" + _tex(caption) + r"}}", r"\end{center}"])


def _part(letter: str, title: str, text: str) -> str:
    return "\n".join([
        r"\clearpage\vspace*{52mm}",
        r"{\color{sdtamber}\rule{24mm}{2.4pt}}\par\vspace{5mm}",
        r"{\fontsize{15}{18}\selectfont\sdtsoft{PART " + letter + r"}}\par\vspace{2mm}",
        r"{\fontsize{30}{34}\selectfont\bfseries " + _tex(title) + r"}\par\vspace{6mm}",
        r"\parbox{120mm}{\raggedright\large\sdtsoft{" + _tex(text) + r"}}", r"\clearpage"])


def _sec(number: str, title: str) -> str:
    return r"\sdtband{" + number + r"\quad " + _tex(title) + "}"


def _sub(title: str) -> str:
    return r"\sdtsub{" + _tex(title) + "}"


def _para(text: str) -> str:
    return r"{\small " + _tex(text) + r"}\par\vspace{1.5mm}"


def _status(value: str):
    """A status of a check for a table: tags for results, grey for information."""
    from utilities._calc_report import Tex

    text = str(value)
    if text == "WARN":
        return Tex(r"\sdtwarn{CHECK}")
    if text in ("INFO", "N/A"):
        return Tex(r"\sdtsoft{" + text + "}")
    return text


def column_results(columns) -> tuple[list[list], dict[str, str]]:
    """One line for each column of a column report, and the result of each."""
    import pandas as pd

    rows, status = [], {}
    for name, group in columns.groupby("UniqueName", sort=False):
        first = group.iloc[0]
        failed = group["Column_Design_Status"].astype(str).str.upper().str.startswith("FAIL").any()
        status[str(name)] = "FAIL" if failed else "PASS"
        round_one = str(first.get("Shape")) == "Circular"
        size = (f"{_number(first.get('Diameter_mm')):.0f} dia." if round_one else
                f"{_number(first.get('Width_mm')):.0f} x {_number(first.get('Depth_mm')):.0f}")

        def most(*names):
            values = pd.to_numeric(pd.concat([group[n] for n in names]), errors="coerce")
            return f"{values.max():.2f}" if values.notna().any() else "-"

        spiral = "spiral" in str(first.get("Transverse_Type", "")).lower()
        rows.append([
            first.get("Column_Label"), name, first.get("Story"), size,
            int(_number(first.get("Longitudinal_Bars"))),
            f"{_number(first.get('Tie_Bar_mm')):.0f} @ {_number(first.get('Transverse_Spacing_mm')):.0f}"
            + (" spiral" if spiral else ""),
            f"{_number(first.get('Reinforcement_Ratio')) * 100:.2f} %",
            most("Flexure_Utilization"), most("Shear_Utilization"),
            most("Joint_Shear_Utilization_X", "Joint_Shear_Utilization_Y"), status[str(name)]])
    return rows, status


def beam_results(beams) -> list[list]:
    """One line for each beam of the beam results."""
    import pandas as pd

    rows = []
    for name, pair in beams.groupby("UniqueName", sort=False):
        tops, bottoms = pair[pair["Face"] == "TOP"], pair[pair["Face"] == "BOTTOM"]
        if tops.empty or bottoms.empty:
            continue
        top, bottom = tops.iloc[0], bottoms.iloc[0]

        def bars(row):
            return "/".join(str(int(sum(_number(row.get(f"n_{zone}_L{k}")) for k in (1, 2, 3))))
                            for zone in ("left", "mid", "right"))

        ratio = pd.to_numeric(pd.Series([top.get("Defl_ratio")]), errors="coerce").iloc[0]
        rows.append([
            name, top.get("Story"), f"{_number(top.get('Width')):.0f} x {_number(top.get('Depth')):.0f}",
            bars(top), bars(bottom),
            f"{int(_number(top.get('Stirrup_Legs'), 2))}-{_number(top.get('ds')):.0f} @ "
            f"{int(_number(top.get('Spacing_2H')))}/{int(_number(top.get('Spacing_Mid')))}",
            f"{ratio:.2f}" if ratio == ratio else "-",
            "PASS" if str(top.get("Design_Status")) == "OK" else "FAIL"])
    return rows


def report_body(data: ReportData) -> str:
    """The LaTeX of the report, between its header and its end."""
    import pandas as pd

    from utilities import report_style as style
    from utilities._calc_report import _Unwrapped, _figure_latex, _table_latex

    text, info = data.text, data.info
    body: list[str] = []
    add = body.append
    date = datetime.today().strftime("%d %B %Y").lstrip("0")
    seismic = text.seismic[0] if text.seismic else {}
    top_story = text.stories[0] if text.stories else ("", None, 0.0)
    base = text.stories[-1][2] if text.stories else 0.0

    # ---------------------------------------------------------------- front
    add(style.title_block("Structural Analysis and Design Report", info.project or data.model_name, [
        ("Project", info.project), ("Location", info.location), ("Client", info.client),
        ("Prepared by", info.engineer), ("Model", data.model_name), ("Date", date),
        ("Design codes", "NSCP 2015, ACI 318M-14"),
        ("Prepared with", f"sdt design {style.program_version()}".strip()),
        ("Stories", f"{max(len(text.stories) - 1, 0)} above the base, "
                    f"{(top_story[2] - base) / 1000:.2f} m high" if text.stories else ""),
        ("Structural system", ("Special moment-resisting frame" if data.smrf else "Moment frame")
         + (f", R = {seismic.get('r')}" if seismic.get("r") else ""))]))
    add(r"\vspace{3mm}" + _sub("Contents"))
    contents = [("PART A", "ANALYSIS"), ("A1", "Building and model"), ("A2", "Framing plans"),
                ("A3", "Materials and sections"), ("A4", "Load patterns and load cases"),
                ("A5", "Seismic criteria"), ("A6", "Wind criteria"), ("A7", "Load combinations"),
                ("A8", "Analysis results"), ("A9", "Story drift"),
                ("A10", "Irregularities and P-delta"), ("A11", "Model checks"),
                ("PART B", "DESIGN"), ("B1", "Design basis"), ("B2", "Beam design"),
                ("B3", "Column design"), ("B4", "Concrete quantities"),
                ("B5", "Files of the design"),
                ("PART C", "MEMBER CALCULATIONS"), ("C1", "Beams"), ("C2", "Columns")]
    rows = []
    for number, title in contents:
        rows.append(r"\multicolumn{2}{@{}l}{\rule{0pt}{5mm}\bfseries " + number + r"\quad " + title
                    + r"} \\" if number.startswith("PART") else
                    r"\sdtsoft{" + number + r"} & " + title + r" \\")
    add(r"\noindent{\small\begin{tabular}{@{}p{12mm}l@{}}" + "\n".join(rows) + r"\end{tabular}}\par")

    add(r"\vspace{4mm}" + _sub("Result"))
    results = []
    if data.findings:
        failed = sum(_get(f, "status") == "FAIL" for f in data.findings)
        results.append([
            "Model checks (NSCP 2015)", "PASS" if not failed else "FAIL",
            f"{sum(_get(f, 'status') == 'OK' for f in data.findings)} passed, "
            f"{sum(_get(f, 'status') == 'WARN' for f in data.findings)} warnings, "
            f"{sum(_get(f, 'status') == 'INFO' for f in data.findings)} for information, "
            f"{failed} failed"])
    if data.drift is not None:
        failing = [f for _, findings, *_ in _get(data.drift, "levels", [])
                   for f in findings if _get(f, "status") == "FAIL"]
        results.append(["Story drift", "PASS" if not failing else "FAIL",
                        "seismic and wind, at each stiffness level" if not failing else
                        f"{len(failing)} checks fail: the members were not resized for drift"])
    if data.irregularity is not None:
        found = [_get(i, "name") for i in _get(data.irregularity, "items", [])
                 if _get(i, "status") == "WARN"]
        results.append(["Irregularities", "PASS" if not found else _status("WARN"),
                        "regular in the types computed" if not found else
                        "irregular: " + "; ".join(found)])
    column_rows, column_status = column_results(data.columns) if data.columns is not None and len(
        data.columns) else ([], {})
    beam_rows = beam_results(data.beams) if data.beams is not None and len(data.beams) else []
    if beam_rows:
        failing = sum(row[-1] == "FAIL" for row in beam_rows)
        results.append(["Beams", "PASS" if not failing else "FAIL",
                        f"{len(beam_rows)} designed, {failing} not passing"])
    if column_rows:
        failing = sum(value == "FAIL" for value in column_status.values())
        results.append(["Columns", "PASS" if not failing else "FAIL",
                        f"{len(column_rows)} designed, {failing} not passing"])
    add(_table(["Item", "Result", "Remarks"],
               [[a, b, _Unwrapped(c)] for a, b, c in results],
               r"p{48mm}p{16mm}>{\raggedright\arraybackslash}p{100mm}"))
    for note in data.notes:
        add(_para(note))

    # ---------------------------------------------------------------- PART A
    add(_part("A", "Analysis", "The model, its loads and their combinations, the seismic and "
              "wind criteria, and what the analysis gave: periods, base shears, drifts and "
              "irregularities."))
    add(_sec("A1", "Building and model"))
    add(_para("The structure is modelled in ETABS as a three-dimensional frame. Beams and columns "
              "are frame members with cracked section properties; floors are shells that carry "
              "the gravity loads to the beams."))
    frames = data.frames
    counts = {}
    if frames is not None and len(frames):
        for kind in ("Column", "Beam"):
            counts[kind] = frames[frames["DesignType"] == kind].groupby("Story")["UniqueName"].count()
    add(_table(["Story", "Height (m)", "Elevation (m)", "Columns", "Beams and girders"], [
        [name, "-" if height is None else f"{height / 1000:.2f}", f"{elevation / 1000:+.2f}",
         int(counts.get("Column", {}).get(name, 0)) or "-",
         int(counts.get("Beam", {}).get(name, 0)) or "-"]
        for name, height, elevation in text.stories], "lrrrr"))
    if data.view:
        add(_figure(data.view, "0.92", "Three-dimensional view of the structure."))
    add(r"\clearpage" + _sec("A2", "Framing plans"))
    add(_para("Each plan shows the beams and girders of a floor at their width with their names, "
              "and the columns below it with their marks. Girders, which frame into a column, "
              "are drawn darker than the beams they carry."))
    for story, elevation, beams, columns, picture in data.plans:
        add(r"\noindent\begin{minipage}{\textwidth}"
            + _sub(f"{story} framing plan, elevation {elevation / 1000:+.2f} m")
            + _figure(picture, "0.93", f"{beams} beams and girders, {columns} columns.")
            + r"\end{minipage}\par")

    add(r"\clearpage" + _sec("A3", "Materials and sections"))
    if frames is not None and len(frames):
        concrete = "f'c"
        add(_table(["Material", "Property", "Value"], [
            ["Concrete", "Compressive strength f'c",
             ", ".join(f"{v:.1f}" for v in sorted(set(pd.to_numeric(frames[concrete], errors="coerce").dropna()))) + " MPa"],
            ["Reinforcing bars", "Yield strength fy",
             ", ".join(f"{v:.0f}" for v in sorted(set(pd.to_numeric(frames["fy"], errors="coerce").dropna()))) + " MPa"],
            ["Stirrups and ties", "Yield strength fyt",
             ", ".join(f"{v:.0f}" for v in sorted(set(pd.to_numeric(frames["fys"], errors="coerce").dropna()))) + " MPa"],
        ], "llr"))
        add(_sub("Frame sections in the model"))
        used = frames.groupby(["DesignType", "SectProp"], sort=True).agg(
            count=("UniqueName", "count"), width=("Width", "first"), depth=("Depth", "first"),
            diameter=("Diameter", "first")).reset_index()
        add(_table(["Member", "Section", "Size (mm)", "Members"], [
            [row["DesignType"], row["SectProp"],
             f"{row['diameter']:.0f} dia." if pd.notna(row["diameter"]) and row["diameter"] > 0
             else f"{_number(row['width']):.0f} x {_number(row['depth']):.0f}", int(row["count"])]
            for _, row in used.iterrows()], "lllr", long=len(used) > 30))
    modifiers = [_get(f, "text") for f in data.findings
                 if "Effective I" in str(_get(f, "text")) or "Rigid end zone" in str(_get(f, "text"))]
    for line in modifiers:
        add(_para(line))

    add(_sec("A4", "Load patterns and load cases"))
    add(_table(["Load pattern", "Type", "Self weight multiplier"],
               [[n, t, s] for n, t, s in text.patterns], "llr"))
    if text.area_loads:
        add(_sub("Uniform loads on the floors"))
        add(_table(["Story", "Pattern", "Largest load (kPa)"],
                   [[s, p, f"{v:.2f}"] for s, p, v in text.area_loads], "llr",
                   long=len(text.area_loads) > 30))
    add(_sub("Load cases"))
    add(_table(["Load case", "Type"], [[n, t] for n, t in text.cases.items()], "ll"))
    if text.mass or text.pdelta:
        add(_sub("Mass source and P-delta loads"))
        add(_table(["Pattern", "Mass factor", "P-delta factor"], [
            [name, dict(text.mass).get(name, "-"), dict(text.pdelta).get(name, "-")]
            for name in dict.fromkeys([n for n, _ in text.mass] + [n for n, _ in text.pdelta])],
            "lrr"))

    analysis = data.analysis
    add(_sec("A5", "Seismic criteria"))
    if seismic:
        rows = [["Code of the static patterns", seismic.get("code") or "-", "NSCP 208.5.2"]]
        for label, key, clause in (
                ("Seismic zone factor Z", "zone", "NSCP Table 208-3"),
                ("Soil profile type", "soil", "NSCP Table 208-2"),
                ("Seismic coefficient Ca", "ca", "NSCP Table 208-7"),
                ("Seismic coefficient Cv", "cv", "NSCP Table 208-8"),
                ("Importance factor I", "i", "NSCP Table 208-1"),
                ("Response modification R", "r", "NSCP Table 208-11A"),
                ("Period coefficient Ct (ft units)", "ct", "NSCP Eq. 208-12"),
                ("Accidental eccentricity", "eccentricity", "NSCP 208.5.1.3")):
            if seismic.get(key):
                rows.append([label, seismic[key], clause])
        if seismic.get("source"):
            rows.append(["Seismic source type", f"{seismic['source']}, {seismic.get('distance', '-')} km",
                         "NSCP Table 208-4"])
        if analysis is not None and _get(analysis, "method_a"):
            cap = _number(_get(analysis, "cap"), 1.0)
            rows.append(["Method A period TA", f"{_get(analysis, 'method_a'):.3f} s", "NSCP 208.5.2.2"])
            rows.append(["Period cap of Method B", f"{cap:.1f} TA = {cap * _get(analysis, 'method_a'):.3f} s",
                         "NSCP 208.5.2.2"])
        weight = _get(analysis, "base_shear_weight") if analysis is not None else None
        if weight:
            rows.append([f"Seismic weight W, above {seismic.get('bottom') or 'the base'}",
                         f"{weight / 1000:,.0f} kN", "NSCP 208.6.1"])
        add(_table(["Parameter", "Value", "Reference"], rows, "llr"))
        add(_sub("Static seismic patterns"))
        add(_table(["Pattern", "Direction", "Stories", "Period"], [
            [s["pattern"], s["direction"], f"{s['bottom']} to {s['top']}",
             "program calculated" if s["period"] == "PROGCALC" else s["period"].lower()]
            for s in text.seismic], "llll"))
    shears = [f for f in data.findings if _get(f, "group") == "Seismic" and "V/W" in str(_get(f, "text"))]
    if shears:
        add(_sub("Base shear coefficients"))
        add(_table(["Status", "Check", "Clause"], [
            [_status(_get(f, "status")), _Unwrapped(_get(f, "text")), _Unwrapped(_get(f, "ref"))]
            for f in shears],
            r"p{12mm}>{\raggedright\arraybackslash}p{104mm}>{\raggedright\arraybackslash}p{46mm}",
            "footnotesize"))
    scaling = _get(analysis, "scaling", []) if analysis is not None else []
    if scaling:
        add(_sub("Response spectrum scaled to the static base shear"))
        add(_table(["Direction", "Static case", "V static (kN)", "Spectrum case", "V before (kN)",
                    "V after (kN)", "Scale factor"], [
            [_get(s, "direction"), _get(s, "static_case"), f"{_get(s, 'static_shear') / 1000:,.1f}",
             _get(s, "spectrum_case"), f"{_get(s, 'spectrum_before') / 1000:,.1f}",
             f"{_get(s, 'spectrum_after') / 1000:,.1f}", f"{_get(s, 'factor'):.4f}"]
            for s in scaling], "llrlrrr"))

    add(_sec("A6", "Wind criteria"))
    add(_table(["Pattern", "Code", "Speed", "Exposure", "Kzt", "Gust factor G", "Kd", "Stories"], [
        [w["pattern"], w["code"], w["speed"], w["exposure"], w["kzt"], w["gust"], w["kd"],
         f"{w['bottom']} to {w['top']}"] for w in text.wind], "llllrrrl"))
    for finding in data.findings:
        if _get(finding, "group") == "Wind" and "speed" in str(_get(finding, "text")):
            add(_para(_get(finding, "text")))
            break

    add(_sec("A7", "Load combinations"))
    groups = listed_combinations(text.combos)
    envelopes = sum(str(c.get("type", "")).lower().startswith("envelope") for c in text.combos.values())
    add(_para(f"The model has {len(text.combos)} combinations. {envelopes} of them are envelopes "
              "of the others and are not listed. Ev is the vertical seismic effect, 0.5 Ca I D; "
              "f is the live load factor of the seismic combinations."))
    add(_table(["Group", "Combinations"], [[g, len(n)] for g, n in groups.items()], "lr"))
    for group, names in groups.items():
        add(_sub(f"{group} ({len(names)})"))
        add(_table(["Combination", "Terms"], [
            [_Unwrapped(n), _Unwrapped(combination_terms(text.combos[n]))] for n in names],
            r">{\raggedright\arraybackslash}p{60mm}>{\raggedright\arraybackslash}p{106mm}",
            "scriptsize", long=True))

    add(_sec("A8", "Analysis results"))
    if analysis is not None:
        periods = _get(analysis, "periods")
        if periods is not None and len(periods):
            head = periods.head(12)
            add(_sub("Modal periods and mass"))
            add(_table([str(c) for c in head.columns], [
                [f"{v:.0f}" if str(c) == "Mode" else f"{v:.3f}" if isinstance(v, float) else v
                 for c, v in zip(head.columns, row)] for row in head.values.tolist()],
                "l" + "r" * (len(head.columns) - 1), "footnotesize"))
        governing = _get(analysis, "governing_period", {}) or {}
        mass_sum = _get(analysis, "mass_sum", {}) or {}
        if governing:
            add(_table(["Direction", "Governing period (s)", "Modal mass (%)", "Needed (%)"], [
                [d, f"{governing[d]:.3f}", f"{mass_sum.get(d, 0) * 100:.1f}", "90"]
                for d in governing], "lrrr"))
        for warning in _get(analysis, "warnings", []) or []:
            add(_para(warning))
        weights = _get(analysis, "story_weights")
        if weights is not None and len(weights):
            add(_sub("Weight of each level, from the mass source"))
            add(_table([str(c) for c in weights.columns], [
                [f"{v:,.1f}" if isinstance(v, float) else v for v in row]
                for row in weights.values.tolist()], "l" + "r" * (len(weights.columns) - 1)))
    else:
        add(_para("The analysis results were not available when the report was written."))

    add(_sec("A9", "Story drift"))
    if data.drift is not None:
        add(_para("Seismic drift: the inelastic drift 0.7 R times the elastic drift, against the "
                  "limit of NSCP 208.6.5 (0.025 h for a period under 0.7 s, 0.020 h above). Wind "
                  f"drift against h / {_number(_get(data.drift, 'wind_denominator'), 400):g}."))
        for level, rows in (_get(data.drift, "stories", {}) or {}).items():
            add(_sub(str(level)))
            add(_table(["Story", "Seismic drift / h", "0.7 R x drift / h", "Combination",
                        "Wind drift / h", "Combination"], [
                [r[0], "-" if r[1] is None else f"{r[1]:.5f}", "-" if r[2] is None else f"{r[2]:.4f}",
                 _Unwrapped(r[3] or "-"), "-" if r[4] is None else f"{r[4]:.5f}",
                 _Unwrapped(r[5] or "-")] for r in rows],
                r"lrr>{\raggedright\arraybackslash}p{50mm}r>{\raggedright\arraybackslash}p{50mm}",
                "scriptsize", long=len(rows) > 30))
        for note in _get(data.drift, "notes", []) or []:
            add(_para(note))
    else:
        add(_para("No drift check was made."))

    add(_sec("A10", "Irregularities and P-delta"))
    if data.irregularity is not None:
        items = _get(data.irregularity, "items", [])
        add(_table(["Status", "Check", "Result", "Clause"], [
            [_status(_get(i, "status")), _get(i, "name"), _Unwrapped(_get(i, "verdict")),
             _Unwrapped(_get(i, "ref"))] for i in items],
            r"p{13mm}>{\raggedright\arraybackslash}p{50mm}>{\raggedright\arraybackslash}p{66mm}"
            r">{\raggedright\arraybackslash}p{32mm}", "footnotesize"))
        for item in items:
            rows = _get(item, "rows", [])
            if rows:
                headers = _get(item, "headers", [])
                add(_sub(_get(item, "name")))
                add(_table(headers, rows, "lll" + "r" * max(len(headers) - 4, 0) + "l"
                           if len(headers) > 5 else "l" + "r" * max(len(headers) - 2, 0) + "l",
                           "footnotesize", long=len(rows) > 30))
            for note in _get(item, "notes", []) or []:
                add(r"{\footnotesize\sdtsoft{" + _tex(note) + r"}}\par\vspace{1mm}")
    else:
        add(_para("The irregularity check was not made."))

    add(_sec("A11", "Model checks"))
    add(_para("Every check of the model against NSCP 2015, as sdt check reports it. INFO lines "
              "state what the model holds, for the engineer to confirm."))
    by_group: dict[str, list] = {}
    for finding in data.findings:
        by_group.setdefault(str(_get(finding, "group")), []).append(finding)
    for group, items in by_group.items():
        add(_sub(group))
        add(_table(["Status", "Check", "Clause"], [
            [_status(_get(f, "status")), _Unwrapped(_get(f, "text")), _Unwrapped(_get(f, "ref"))]
            for f in items],
            r"p{12mm}>{\raggedright\arraybackslash}p{106mm}>{\raggedright\arraybackslash}p{44mm}",
            "scriptsize", long=True))

    # ---------------------------------------------------------------- PART B
    add(_part("B", "Design", "The design basis, the result of every beam and column against "
              "NSCP 2015 chapter 4 (ACI 318M-14), and the concrete quantities."))
    add(_sec("B1", "Design basis"))
    bars, column_bars = data.inputs.get("beam_bars") or {}, data.inputs.get("column_bars") or {}
    seismic_beams = "Special moment frame (NSCP 418.6; ACI 18.6)" if data.smrf else "Not a special moment frame"
    seismic_columns = "Special moment frame (NSCP 418.7, 418.8; ACI 18.7, 18.8)" if data.smrf else \
        "Not a special moment frame"
    add(_table(["Item", "Beams and girders", "Columns"], [
        ["Design code", "NSCP 2015 chapter 4 (ACI 318M-14)", "NSCP 2015 chapter 4 (ACI 318M-14)"],
        ["Seismic detailing", seismic_beams, seismic_columns],
        ["Main bars", f"{_number(bars.get('dm')):.0f} mm", f"{_number(column_bars.get('dmain')):.0f} mm"],
        ["Stirrups and ties", f"{_number(bars.get('ds')):.0f} mm",
         f"{_number(column_bars.get('dties')):.0f} mm"],
        ["Clear cover", f"{_number(bars.get('cc')):.0f} mm", f"{_number(column_bars.get('cover')):.0f} mm"],
        ["Design forces", "Envelope of the strength combinations",
         "Every strength combination, at both ends"],
        ["Slenderness", "-", _Unwrapped("Sway effect from the P-delta analysis, member effect by "
                                        "moment magnification (NSCP 406.6.4; ACI 6.6.4)")],
        ["Combinations", f"{len(data.inputs.get('combos') or [])} strength combinations", "the same"],
    ], r"p{34mm}p{62mm}>{\raggedright\arraybackslash}p{66mm}"))

    add(_sec("B2", "Beam design"))
    add(_para(f"{len(beam_rows)} beams and girders. Bars are counted at the left support / "
              "midspan / right support. Stirrups: legs, bar size, and the spacing within 2h of a "
              "support / between. The deflection is the governing deflection over its limit."))
    add(_table(["Beam", "Story", "Section", "Top bars", "Bottom bars", "Stirrups", "Deflection",
                "Result"], beam_rows, "llllllrl", "scriptsize", long=True))

    add(_sec("B3", "Column design"))
    add(_para(f"{len(column_rows)} columns. The ratios are the largest over every combination "
              "and both ends."))
    add(_table(["Mark", "Column", "Story", "Size (mm)", "Bars", "Ties", "Steel", "Flexure", "Shear",
                "Joint", "Result"], column_rows, "lllllllrrrl", "scriptsize", long=True))

    add(_sec("B4", "Concrete quantities"))
    q = data.quantities
    if q is not None:
        stories = _get(q, "stories", [])
        rows = []
        for s in stories:
            volume = sum(_number(_get(s, k)) for k in ("columns", "beams", "walls", "slabs"))
            rows.append([_get(s, "story"), *(f"{_number(_get(s, k)):.2f}" for k in
                                             ("columns", "beams", "walls", "slabs")),
                         f"{volume:.2f}", f"{_number(_get(s, 'area')):.2f}"])
        add(_table(["Story", "Columns (m3)", "Beams (m3)", "Walls (m3)", "Slabs (m3)",
                    "Concrete (m3)", "Floor area (m2)"], rows, "lrrrrrr"))
        volume = sum(sum(_number(_get(s, k)) for k in ("columns", "beams", "walls", "slabs"))
                     for s in stories)
        area = sum(_number(_get(s, "area")) for s in stories)
        thickness, footprint = _number(_get(q, "grade_thickness")), _number(_get(q, "grade_area"))
        grade = footprint * thickness / 1000.0
        foundation = _number(_get(q, "footings")) + _number(_get(q, "tie_beams"))
        parts = [["Stories", f"{volume:.2f}", f"{area:.2f}"]]
        if thickness > 0:
            parts.append([f"Slab on grade, {thickness:g} mm on the footprint", f"{grade:.2f}",
                          f"{footprint:.2f}"])
        if _get(q, "foundation_included"):
            parts.append(["Footings and tie beams", f"{foundation:.2f}", "-"])
        total_area = area + (footprint if thickness > 0 else 0.0)
        total = volume + grade + (foundation if _get(q, "foundation_included") else 0.0)
        parts.append(["Total", f"{total:.2f}", f"{total_area:.2f}"])
        add(_table(["Part", "Concrete (m3)", "Floor area (m2)"], parts, "lrr"))
        if total_area > 0:
            add(r"\noindent{\bfseries Concrete ratio:\ \ " + f"{total / total_area:.3f}"
                + r" m\textsuperscript{3}/m\textsuperscript{2}}\par\vspace{2mm}")
        for note in _get(q, "notes", []) or []:
            add(_para(note))
    else:
        add(_para("The concrete quantities were not measured."))

    add(_sec("B5", "Files of the design"))
    add(_table(["File", "What it holds"], [[_Unwrapped(a), _Unwrapped(b)] for a, b in data.files],
               r">{\raggedright\arraybackslash}p{72mm}>{\raggedright\arraybackslash}p{94mm}"))

    # ---------------------------------------------------------------- PART C
    if data.beam_members or data.column_members:
        add(_part("C", "Member calculations", "The calculation tables of each beam and each "
                  "column: the values behind the results of Part B."))
        for number, title, members, pages in (("C1", "Beams", data.beam_members, False),
                                              ("C2", "Columns", data.column_members, True)):
            if not members:
                continue
            add(_sec(number, title))
            for index, member in enumerate(members):
                if pages and index:
                    add(r"\clearpage")
                add(r"\sdtsub{" + _tex(member.heading) + "}")
                figures = list(member.figures)
                for item in member.tables:
                    if figures and member.beside_figure and item.title == member.beside_figure:
                        from utilities._calc_report import _beside_latex

                        add(_beside_latex(item, *figures.pop(0)))
                    else:
                        add(_table_latex(item))
                for image, caption in figures:
                    add(_figure_latex(image, caption))
            add(r"\clearpage")
    return "\n\n".join(body)


def build_report(data: ReportData, filepath: str) -> str | None:
    """Write the report as a PDF; returns its path, or None when LaTeX fails.
    Pictures are named relative to the folder of the PDF."""
    from pylatex.utils import NoEscape

    from utilities import report_style as style

    if filepath.lower().endswith(".pdf"):
        filepath = filepath[:-4]
    doc = style.new_document("Structural Analysis and Design Report", "NSCP 2015, ACI 318M-14",
                             f"Model: {data.model_name}", "sdt design",
                             os.path.basename(filepath) + ".pdf")
    doc.preamble.append(NoEscape(
        r"\setlength{\LTleft}{0pt}\setlength{\LTpre}{0pt}\setlength{\LTpost}{2mm}"))
    doc.append(NoEscape(report_body(data)))
    here = os.getcwd()
    try:
        os.chdir(os.path.dirname(os.path.abspath(filepath)))
        # twice: "Page x of y" and the long tables settle on the second pass
        from utilities.latex_help import compiler_args

        latex_args = compiler_args()
        doc.generate_pdf(filepath, clean=False, clean_tex=False, compiler="pdflatex",
                         compiler_args=latex_args)
        doc.generate_pdf(filepath, clean_tex=True, compiler="pdflatex",
                         compiler_args=latex_args)
        return filepath + ".pdf"
    except Exception as error:  # noqa: BLE001 - LaTeX missing or a compile error
        print(f"PDF Error: {error}")
        return None
    finally:
        os.chdir(here)


# =============================================================================
# FROM THE FINISHED MODEL
# =============================================================================
def member_reports(beams, columns, smrf: bool, folder: str, figure_options: dict | None,
                   progress=None) -> tuple[list, list]:
    """The calculation tables of every beam and column (Part C). The interaction
    surface of each column is drawn into ``folder``/report figures."""
    from design.concrete.beams.calculations import _beam_calc_member
    from design.concrete.columns.calculations import (
        BESIDE_FIGURE,
        _bcc_caption,
        _column_calc_member,
        column_interaction_figure,
    )

    say = progress or (lambda text: None)
    beam_members, column_members = [], []
    if beams is not None and len(beams):
        for _, rows in beams.groupby("UniqueName", sort=False):
            top, bottom = rows[rows["Face"] == "TOP"], rows[rows["Face"] == "BOTTOM"]
            if not top.empty and not bottom.empty:
                beam_members.append(_beam_calc_member(top.iloc[0], bottom.iloc[0], smrf))
    if columns is not None and len(columns):
        os.makedirs(os.path.join(folder, FIGURES), exist_ok=True)
        groups = list(columns.groupby("UniqueName", sort=False))
        for index, (name, rows) in enumerate(groups, start=1):
            member = _column_calc_member(rows)
            if figure_options:
                say(f"Report: interaction surface of column {index} of {len(groups)}")
                relative = f"{FIGURES}/{re.sub(r'[^A-Za-z0-9_-]', '_', str(name))}.jpg"
                if column_interaction_figure(rows, os.path.join(folder, relative), **figure_options):
                    member.figures.append((relative, "Design interaction surface of the final "
                                           "bar layout with every combination at both ends."
                                           + _bcc_caption(rows)))
                    member.beside_figure = BESIDE_FIGURE
            column_members.append(member)
    return beam_members, column_members


FIGURES = "report figures"


def gather(connector, model_path: str, folder: str, summary: dict, settings, info: ProjectInfo,
           analysis=None, drift=None, tables: dict | None = None, wind_denominator: float = 400.0,
           progress=None) -> ReportData:
    """Read the finished model and put together what the report is written from.
    A part that cannot be read is left out, with a note on the front page."""
    from etabs_api.workflows import framing_plans as fp
    from etabs_api.workflows import model_check as mc

    say = progress or (lambda text: None)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    data = ReportData(stem, info, analysis=analysis, drift=drift,
                      beams=summary.get("beams"), columns=summary.get("columns"),
                      smrf=bool(getattr(settings, "smrf", True)))
    data.frames = (tables or {}).get("FRAME DATA")
    data.inputs = {"beam_bars": getattr(settings, "beam_bars", {}),
                   "column_bars": getattr(settings, "column_bars", {}),
                   "combos": getattr(settings, "combos", [])}
    pictures = os.path.join(folder, FIGURES)
    os.makedirs(pictures, exist_ok=True)

    def part(what: str, read):
        try:
            return read()
        except Exception as error:  # noqa: BLE001 - one part must not lose the whole report
            data.notes.append(f"{what} could not be read ({error}): it is left out of this report.")
            return None

    say("Report: loads, cases and combinations of the model")
    path = model_text_path(model_path)
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            data.text = part("The text file of the model", lambda: parse_model_text(handle.read())) \
                or ModelText()
    else:
        data.notes.append(f"{os.path.basename(path)} was not found beside the model: the loads, "
                          "load cases and combinations are left out of this report.")

    say("Report: checks of the model")

    def checks():
        found = mc.read_model_data(connector)
        found.wind_drift_denominator = wind_denominator
        return mc.run_checks(found)

    data.findings = part("The model checks", checks) or []

    say("Report: irregularities and the P-delta stability coefficient")

    def irregular():
        from etabs_api.workflows.irregularity import read_irregularity

        return read_irregularity(connector, model_path, True, say)

    data.irregularity = part("The irregularity check", irregular)

    say("Report: concrete quantities")

    def measured():
        from etabs_api.workflows.concrete_quantities import read_quantities
        from etabs_api.workflows.framing_plans import saved_foundation_plan

        return read_quantities(connector, model_path, info.grade,
                               saved_foundation_plan(model_path), say)

    data.quantities = part("The concrete quantities", measured)

    say("Report: framing plans")

    def plans():
        floors, _, _ = fp.read_floor_plans(connector.sap_model)
        out = []
        for floor in floors:
            name = f"{FIGURES}/plan {re.sub(r'[^A-Za-z0-9_-]', '_', floor.story)}.png"
            draw_plan(floor, os.path.join(folder, name))
            out.append((floor.story, floor.elevation, len(floor.beams), len(floor.columns), name))
        return out

    data.plans = part("The framing plans", plans) or []

    say("Report: 3D view")

    def view():
        from etabs_api.workflows.model_3d import read_model

        name = f"{FIGURES}/view 3d.png"
        draw_view(read_model(connector.sap_model, True), os.path.join(folder, name))
        return name

    data.view = part("The 3D view", view) or ""

    bars = getattr(settings, "column_bars", {}) or {}
    figure_options = ({"dmain": bars["dmain"], "dties": bars["dties"], "cover": bars["cover"],
                       "is_smrf": data.smrf} if {"dmain", "dties", "cover"} <= set(bars) else None)
    found = part("The member calculations", lambda: member_reports(
        data.beams, data.columns, data.smrf, folder, figure_options, say))
    data.beam_members, data.column_members = found or ([], [])
    data.files = [
        (f"Results/{stem} - Beam Design.xlsx", "Every value of the beam design"),
        (f"Calculations/{stem} - Beam Calculations.pdf", "The calculation tables of each beam"),
        (f"Results/{stem} - Column Design.xlsx", "Every value of the column design"),
        (f"Calculations/{stem} - Column Calculations.pdf", "The calculation tables and interaction surface of "
                                              "each column"),
        ("Schedules: beam, girder and column schedules (.dxf)", "The reinforcement schedules, by floor"),
        (f"Summaries/{stem} - Drift.txt", "The story drift of the final sizes"),
        (f"Summaries/{stem} summary.txt", "The summary of the design loop"),
    ]
    return data


def write_project_report(connector, model_path: str, folder: str, summary: dict, settings,
                         info: ProjectInfo, analysis=None, drift=None, tables: dict | None = None,
                         wind_denominator: float = 400.0, progress=None) -> str | None:
    """Gather the finished model and write ``<model> - Project Report.pdf`` in ``folder``."""
    say = progress or (lambda text: None)
    data = gather(connector, model_path, folder, summary, settings, info, analysis, drift, tables,
                  wind_denominator, say)
    say("Report: writing the PDF (LaTeX)")
    stem = os.path.splitext(os.path.basename(model_path))[0]
    return build_report(data, output_path(folder, f"{stem} - Project Report", CALCULATIONS))


# =============================================================================
# THE QUESTION, BEFORE THE LOOP
# =============================================================================
def ask_project_report(title: str) -> ProjectInfo | bool | None:
    """Ask, before the loop starts, whether to write the full report, and the
    lines of its front page. Returns the ``ProjectInfo``, False for no report,
    or None when the dialog was closed."""
    from etabs_api.workflows.design_loop import _save, _saved
    from utilities._gui_helpers import enter_values, select_option, show_warning

    chosen = select_option(
        title, QUESTION + "\n\nOne PDF for the whole project, written when the loop ends: the "
        "analysis (3D view, framing plans, loads, combinations, seismic and wind criteria, "
        "periods, drifts, irregularities, model checks), the design of every beam and column, "
        "and the calculation tables of each member. It adds a few minutes at the end of the "
        "loop.", [NO, YES])
    if chosen is None:
        return None
    if chosen != YES:
        return False
    last = _saved("project_report")
    defaults = {label: str(last.get(key, "100" if key == "grade" else "")) for label, key in
                FIELDS.items()}
    typed = enter_values(title, "Front page of the report\n\nEvery line may be left empty. The "
                         "slab on grade is for the concrete quantities: its area is the "
                         "footprint of the building.", list(FIELDS), defaults)
    if typed is None:
        return None
    try:
        grade = float(typed[list(FIELDS)[-1]] or 0)
        if grade < 0:
            raise ValueError
    except ValueError:
        show_warning("The slab on grade thickness must be a number, 0 or more.", title=title)
        return None
    values = {key: typed[label].strip() for label, key in FIELDS.items()}
    _save("project_report", values)
    return ProjectInfo(values["project"], values["location"], values["client"],
                       values["engineer"], grade)
