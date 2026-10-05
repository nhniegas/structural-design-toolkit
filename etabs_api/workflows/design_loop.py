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
  the SMRF dimension go straight to the first larger square size that passes
  on the forces of the current analysis (``column_sizer``), and the next
  analysis confirms it. A column is never smaller than the column above it.
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
    grow_beam,
    grow_column,
    parse_section,
    shrink_beam,
    shrink_column,
)

DEPTH_FAILURES = ("MAX BARS", "SMRF STEEL RATIO", "SMRF MOMENT", "DEFLECTION",
                  "DEPTH BELOW THE BEAM IT CARRIES")
WIDTH_FAILURES = ("SHEAR SPACING", "SHEAR STRENGTH")
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
    max_inner: int = 10          # beam iterations within a round
    max_inner_columns: int = 10  # column iterations within a round
    zone_factor: float = 0.4
    ct: float = 0.03
    size_on_forces: bool = True  # failing columns jump to the first passing size
    beam_earth_cover_stories: tuple = ()  # levels whose beams get the 75 mm earth cover
    check_top_level: bool = True  # BCC and joint shear at the topmost joints
    check_foundation_level: bool = True  # BCC, joint shear and Ve at the bottom-most story
    inner_tie_style: str = "crossties"  # how the column schedule draws the interior ties
    # {standard deflection combination: the model's combination for that role}
    deflection_roles: dict | None = None
    sources: object = None  # model_inputs.Sources: where the inputs of the run came from
    targets: object = None  # dcr_targets.Targets: target ratios by member type and check
    carrier_depth: bool = False  # a beam is at least as deep as the beams it carries
    compatibility_torsion: bool = False  # beam torsion at most phi Tcr (ACI 22.7.3.2)


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
def _line(name: str, lines: dict[str, str] | None = None) -> str:
    """Beam line of a member: from ``lines`` (the CONNECTIVITY ``Line`` column,
    which covers members with any name) or from the tag in its name."""
    if lines and str(name) in lines:
        return lines[str(name)]
    from design.beam_deflection import _MARK

    match = _MARK.match(str(name))
    return (match.group(1) + match.group(2) + "-" + match.group(3)).upper() if match else name


# =============================================================================
# SECTIONS OF THE MODEL (any naming)
# =============================================================================
@dataclass
class ModelSections:
    """The section of every beam and column, whatever the model calls it.

    A section named as ``sdt setup`` names it is read from its name. Any other
    rectangular or circular concrete section is read from ETABS: its size
    and materials, and its family from where the member is. New sizes are
    always created under the setup names.
    """

    sections: dict[str, Section] = field(default_factory=dict)   # member -> size
    actual: dict[str, str] = field(default_factory=dict)         # member -> the model's name
    materials: dict[str, str] = field(default_factory=dict)      # grade tag -> model material
    covers: dict[str, float] = field(default_factory=dict)       # member -> cover of its section
    foreign: list[str] = field(default_factory=list)             # names outside the setup naming
    skipped: dict[str, str] = field(default_factory=dict)        # section name -> why not resized
    modified: list[str] = field(default_factory=list)            # sections with stiffness modifiers

    def families(self) -> dict[str, int]:
        """Number of members of each family (G, B, FTB, CR, C)."""
        out: dict[str, int] = {}
        for section in self.sections.values():
            family = "C" if section.circular else section.family
            out[family] = out.get(family, 0) + 1
        return out


def read_model_sections(connector) -> ModelSections:
    """The sections of the open model's beams and columns (see ``ModelSections``)."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows.analysis_forces import designed_names
    from etabs_api.workflows.model_inputs import beam_family, grade_tag
    from etabs_api.workflows.model_setup import KSI_TO_MPA, KSI_TO_MPA_CONCRETE

    model = connector.sap_model

    def table(name: str) -> pd.DataFrame:
        tables = model.DatabaseTables
        tables.SetLoadCasesSelectedForDisplay([])
        tables.SetLoadCombinationsSelectedForDisplay([])
        try:
            return connector._read_database_table(name)
        except Exception:
            return pd.DataFrame()

    out = ModelSections()
    assigned = table("Frame Assignments - Section Properties")
    if assigned.empty:
        return out
    members = designed_names(connector, assigned["UniqueName"].astype(str))
    wanted = set(members)
    prop_of = {str(n): str(p) for n, p in zip(assigned["UniqueName"], assigned["SectProp"])
               if str(n) in wanted}
    foreign = sorted({prop for prop in prop_of.values() if parse_section(prop) is None})
    out.foreign = foreign
    for name, prop in prop_of.items():
        section = parse_section(prop)
        if section is not None:
            out.sections[name], out.actual[name] = section, prop
    if not foreign:
        return out

    def by_name(frame: pd.DataFrame) -> dict[str, dict]:
        if frame.empty or "Name" not in frame.columns:
            return {}
        return {str(row["Name"]): row for row in frame.to_dict("records")}

    def number(value) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    rectangles = by_name(table("Frame Section Property Definitions - Concrete Rectangular"))
    circles = by_name(table("Frame Section Property Definitions - Concrete Circle"))
    beam_rebar = by_name(table("Frame Section Property Definitions - Concrete Beam Reinforcing"))
    column_rebar = by_name(
        table("Frame Section Property Definitions - Concrete Column Reinforcing"))
    concrete = table("Material Properties - Concrete Data")
    rebar = table("Material Properties - Rebar Data")
    fc_of = dict(zip(concrete.get("Material", pd.Series(dtype=str)).astype(str),
                     pd.to_numeric(concrete.get("Fc"), errors="coerce"))) if not concrete.empty else {}
    fy_of = dict(zip(rebar.get("Material", pd.Series(dtype=str)).astype(str),
                     pd.to_numeric(rebar.get("Fy"), errors="coerce"))) if not rebar.empty else {}
    default_rebar = next(iter(fy_of), "")

    beams = table("Beam Object Connectivity")
    columns = table("Column Object Connectivity")
    column_names = set(columns.get("UniqueName", pd.Series(dtype=str)).astype(str))
    column_joints = set()
    for end in ("UniquePtI", "UniquePtJ"):
        if end in columns.columns:
            column_joints |= set(columns[end].astype(str))
    stories = [str(n) for n in as_list(model.Story.GetStories()[1])]
    bottom_level = stories[1] if len(stories) > 1 else ""
    beam_info = {}
    if not beams.empty:
        for row in beams.to_dict("records"):
            ends = {str(row.get("UniquePtI")), str(row.get("UniquePtJ"))}
            beam_info[str(row["UniqueName"])] = (bool(ends & column_joints),
                                                 str(row.get("Story")) == bottom_level)
    try:
        for prop in foreign:
            modifiers = model.PropFrame.GetModifiers(prop, [])
            values = [float(v) for v in as_list(modifiers[0])]
            if any(abs(v - 1.0) > 1e-6 for v in values):
                out.modified.append(prop)
    except Exception:
        pass

    for name, prop in prop_of.items():
        if name in out.sections:
            continue
        is_column = name in column_names
        if prop in rectangles:
            data = rectangles[prop]
            width, depth = number(data.get("t2")), number(data.get("t3"))
            circular = False
        elif prop in circles:
            data = circles[prop]
            width = depth = number(circles[prop].get("t3"))
            circular = True
        else:
            out.skipped[prop] = "not a rectangular or circular concrete section"
            continue
        if width <= 0 or depth <= 0:
            out.skipped[prop] = "its size could not be read"
            continue
        if circular and not is_column:
            out.skipped[prop] = "a circular section on a beam"
            continue
        material = str(data.get("Material"))
        reinforcing = (column_rebar if is_column else beam_rebar).get(prop, {})
        bar_material = str(reinforcing.get("RebarMatL") or default_rebar)
        concrete_tag = grade_tag("C", number(fc_of.get(material)), KSI_TO_MPA_CONCRETE)
        rebar_tag = grade_tag("G", number(fy_of.get(bar_material)), KSI_TO_MPA)
        out.materials.setdefault(concrete_tag, material)
        if bar_material:
            out.materials.setdefault(rebar_tag, bar_material)
        if is_column:
            family = "C" if circular else "CR"
            cover = number(reinforcing.get("Cover"))
        else:
            family = beam_family(*beam_info.get(name, (True, False)))
            cover = number(reinforcing.get("TopCover"))
        out.sections[name] = Section(family, int(round(width)), int(round(depth)),
                                     concrete_tag, rebar_tag, circular)
        out.actual[name] = prop
        if cover > 0:
            out.covers[name] = cover
    return out


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
    from design.code_config import CODE

    ecu = CODE.material.concrete_ultimate_strain
    depth_ratio = ecu / (ecu + CODE.strength.tension_controlled_strain)  # c/d = 3/8
    return CODE.material.stress_block_alpha * CODE.beta1(fc) * fc / fy * depth_ratio


def beam_comfortable(rows: pd.DataFrame, ratio: float, seismic: bool) -> bool:
    """A passing beam whose steel, shear and deflection all stay below ``ratio`` of the
    limits. With target ratios, below ``ratio`` of the targets."""
    from design import dcr_targets

    top = rows.iloc[0]
    kind = dcr_targets.beam_type(top.get("SupportStatus", ""))
    flexure = ratio * dcr_targets.limit(kind, dcr_targets.FLEXURE)
    shear_ratio = ratio * dcr_targets.limit(kind, dcr_targets.SHEAR)
    deflection_ratio = ratio * dcr_targets.limit(kind, dcr_targets.DEFLECTION)
    fc, fy = float(top["f'c"]), float(top["fy"])
    limit = _tension_controlled_rho(fc, fy)
    if seismic and not str(top.get("SupportStatus", "")).startswith("Beam-Framed"):
        limit = min(limit, SMRF_RHO_LIMIT)
    for _, row in rows.iterrows():
        if max(_beam_rho(row, z) for z in ("left", "mid", "right")) > flexure * limit:
            return False
    width = float(top["Width"])
    d = float(top["Depth"]) - float(top.get("cc", 40) or 40) - 30.0
    shear_limit = 0.75 * (0.17 + 0.66) * math.sqrt(fc) * width * d / 1e3  # kN, phi (Vc + Vs,max)
    shear = max(float(top.get(k, 0) or 0) for k in ("Vu_left", "Vu_right", "Vu_mid_2h"))
    if shear > shear_ratio * shear_limit:
        return False
    deflection = top.get("Defl_ratio")
    if deflection is not None and pd.notna(deflection) and float(deflection) > deflection_ratio:
        return False
    return True


def beam_actions(results: pd.DataFrame, sections: dict[str, Section], lengths: dict[str, float],
                 grown: set[str], settings: LoopSettings, seismic: bool = True,
                 allow_shrink: bool = True, lines: dict[str, str] | None = None
                 ) -> dict[str, tuple[Section, str]]:
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
                # a carrier is not made shallower than the beams it carries
                carried = _number(rows.iloc[0].get("Carried_Beam_Depth"))
                if settings.carrier_depth and carried:
                    min_depth = max(min_depth, carried)
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
            by_line.setdefault(_line(name, lines), []).append(name)

    def similar(a: str, b: str) -> bool:
        la, lb = lengths.get(a, 0.0), lengths.get(b, 0.0)
        top = max(la, lb)
        return top <= 0 or abs(la - lb) / top <= settings.span_similarity

    out: dict[str, tuple[Section, str]] = {}
    for name, (new, reason) in wanted.items():
        if new == sections[name]:
            out[name] = (new, reason)  # reported, no change
            continue
        for other in by_line.get(_line(name, lines), [name]):
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
        group = [o for o in by_line.get(_line(name, lines), [name]) if similar(name, o)]
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
    from design import dcr_targets

    needs: set[str] = set()
    text = lambda column: " ".join(rows.get(column, pd.Series(dtype=str)).astype(str)).upper()
    strong_column = max(BCC_REQUIRED, dcr_targets.limit(dcr_targets.COLUMN,
                                                        dcr_targets.STRONG_COLUMN))
    joint_target = dcr_targets.limit(dcr_targets.COLUMN, dcr_targets.JOINT_SHEAR)
    for axis in ("X", "Y"):
        ratios = [_number(v) for v in rows.get(f"BCC_Ratio_{axis}", [])]
        joints = [_number(v) for v in rows.get(f"Joint_Shear_Utilization_{axis}", [])]
        if any(r is not None and r < strong_column for r in ratios) or \
                any(u is not None and u > joint_target + 1e-9 for u in joints):
            needs.add(axis)
    if "FAIL" in text("Flexure_Check") or "FAIL" in text("Axial_Check") \
            or "FAIL" in text("Slenderness_Check") \
            or "FAIL" in text("Shear_Check") or "FAIL" in text("SMRF_Dimension_Check") \
            or "NO PASSING BAR COUNT" in text("Design_Status_Reason"):
        needs.add("square")
    rho = [_number(v) for v in rows.get("Reinforcement_Ratio", [])]
    if any(r is not None and r > COLUMN_RHO_LIMIT for r in rho):
        needs.add("square")
    return needs


def column_comfortable(rows: pd.DataFrame, ratio: float) -> bool:
    """A passing column whose ratios all stay below ``ratio`` of the limits, or of
    the target ratios when the engineer set some."""
    from design import dcr_targets

    checks = {"Flexure_Utilization": dcr_targets.FLEXURE, "Shear_Utilization": dcr_targets.SHEAR,
              "Joint_Shear_Utilization_X": dcr_targets.JOINT_SHEAR,
              "Joint_Shear_Utilization_Y": dcr_targets.JOINT_SHEAR}
    for column, check in checks.items():
        values = [_number(v) for v in rows.get(column, [])]
        allowed = ratio * dcr_targets.limit(dcr_targets.COLUMN, check)
        if any(v is not None and v > allowed for v in values):
            return False
    rho = [_number(v) for v in rows.get("Reinforcement_Ratio", [])]
    if any(r is not None and r > ratio * COLUMN_RHO_LIMIT for r in rho):
        return False
    for axis in ("X", "Y"):
        values = [_number(v) for v in rows.get(f"BCC_Ratio_{axis}", [])]
        required = max(BCC_REQUIRED, dcr_targets.limit(dcr_targets.COLUMN,
                                                       dcr_targets.STRONG_COLUMN))
        if any(v is not None and v < required / ratio for v in values):
            return False
    status = " ".join(rows.get("Column_Design_Status", pd.Series(dtype=str)).astype(str)).upper()
    return "FAIL" not in status


MAX_SIZE_JUMP = 12  # sizes tried above the current one when sizing on the current forces


def growth_sizes(section: Section, settings: LoopSettings, count: int = MAX_SIZE_JUMP
                 ) -> list[Section]:
    """The next ``count`` "square" sizes above ``section``, smallest first."""
    out: list[Section] = []
    current = section
    while len(out) < count:
        current = grow_column(current, "square", settings.ranges, settings.limits)
        if current is None:
            break
        out.append(current)
    return out


def confirm_sections(found: ModelSections, title: str) -> bool:
    """Show what the loop found for the sections it does not know by name.

    Nothing is shown for a model whose sections all carry the setup names.
    False when the user stops.
    """
    if not found.foreign:
        return True
    from utilities._gui_helpers import select_option

    labels = {"G": "girders (frame into a column)", "B": "beams (on other beams)",
              "FTB": "tie beams (bottom-most level)", "CR": "rectangular columns",
              "C": "circular columns"}
    lines = [f"{len(found.foreign)} sections are not named as sdt setup names them. Their size "
             "and materials are read from ETABS. The members are grouped as:", ""]
    lines += [f"  {count} {labels.get(family, family)}"
              for family, count in sorted(found.families().items())]
    lines += ["", "A new size is created under the setup name (for example "
              "G_300X500_C04_G60), with the materials and cover of the section it replaces and "
              "no stiffness modifiers."]
    if found.skipped:
        lines += ["", "Not resized:"] + [f"  {name}: {why}"
                                         for name, why in sorted(found.skipped.items())[:8]]
    if found.modified:
        lines += ["", "These sections carry stiffness modifiers; a new size has none, so "
                  "resizing such a member changes its stiffness: "
                  + ", ".join(found.modified[:8])]
    return select_option(title, "\n".join(lines), ["Continue", "Stop"]) == "Continue"


def joint_min_side(settings: LoopSettings) -> float:
    """The smallest column side the beam bars through a joint allow: 20 bar
    diameters (ACI 18.8.2.3). Zero without seismic design."""
    from design.code_config import CODE

    if not settings.smrf:
        return 0.0
    return CODE.column_seismic.joint_bar_diameter_multiple * float(
        settings.beam_bars.get("dm", 0.0))


def column_actions(report: pd.DataFrame, sections: dict[str, Section], angles: dict[str, float],
                   above: dict[str, str], grown: set[str], settings: LoopSettings,
                   allow_shrink: bool = True, sizer=None) -> dict[str, tuple[Section, str]]:
    """New sizes for the columns, then lower columns at least the size of the one above.

    The joint results are given per column axis: "X" for the beams along the
    column width (local 3) and "Y" for those along its depth (local 2), as the
    column report has them. A joint or beam-column strength failure in Y
    therefore grows the depth, the side the joint is measured along, and in X
    the width, whatever the rotation of the column (``angles`` is not needed).

    A column is not made smaller than 20 bar diameters of the beam bars (ACI
    18.8.2.3): below that the joint fails its dimension rule, the column grows
    again and, having grown, is never made smaller.

    ``sizer(member, sizes)`` (optional) returns the index of the first size of
    ``sizes`` that passes the member checks on the forces of the current analysis,
    or None. With it, a column that fails flexure, axial load, the steel limit or
    shear goes straight to that size in one iteration instead of one size per
    analysis; the next analysis then only confirms it.
    """
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
                if sizer is not None and "square" in needs and new is not None:
                    sizes = growth_sizes(section, settings)
                    index = sizer(name, sizes)
                    if index is not None:
                        new = sizes[index]
                        reason += f"; {new.width}x{new.depth} is the first size that passes " \
                                  "on the current forces"
                    elif sizes:
                        new = sizes[-1]
                        reason += "; no size within the limits passes on the current forces"
            else:
                axis = next(iter(needs))
                along_depth = axis == "Y"  # Y: the beams run along the depth (local 2)
                new = grow_column(section, "side", settings.ranges, settings.limits, along_depth)
                side = "depth" if along_depth else "width"
                reason = f"grow the {side} (joint / beam-column strength in {axis})"
            out[name] = (new or section,
                         reason if new else reason + ": no larger size within the limits")
        elif allow_shrink and name not in grown and column_comfortable(rows,
                                                                       settings.downsize_ratio):
            smaller = shrink_column(section, settings.ranges, joint_min_side(settings),
                                    settings.limits.column_max_ratio)
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
        self.column_geometry: dict = {}      # lu and k per column, from the last column design
        self.foundation_columns: set[str] = set()
        self.verbose = True                  # the log lines are also printed in the terminal
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
        """Write a line to the log file and, with ``verbose``, to the terminal."""
        if getattr(self, "verbose", True):
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
        from etabs_api.workflows.analysis_forces import designed_names

        return designed_names(self.connector, beams), designed_names(self.connector, columns)

    def sections(self) -> dict[str, Section]:
        """The section of every member; read from ETABS where the name does not say it."""
        found = read_model_sections(self.connector)
        self.model_sections = found
        # the materials and covers of the sections being replaced stay known
        # after their members have moved to setup-named sections
        self.materials = {**found.materials, **getattr(self, "materials", {})}
        self.covers = {**getattr(self, "covers", {}), **found.covers}
        return found.sections

    def lines(self) -> dict[str, str]:
        """Beam line of every beam, from the extracted connectivity."""
        table = self.tables.get("CONNECTIVITY")
        if table is None or "Line" not in getattr(table, "columns", ()):
            return {}
        return {str(name): str(line) for name, line in zip(table["UniqueName"], table["Line"])
                if line is not None and str(line) != "nan"}

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
        """The spectrum loads, with every scale factor at its base value g I / R (from the
        model's seismic patterns), so each iteration scales from the unscaled spectrum
        and not from factors an earlier sdt analyze left in the model."""
        from etabs_api.core.helpers import as_list
        from etabs_api.workflows.model_analysis import spectrum_cases

        api = self.model.LoadCases.ResponseSpectrum
        base = self._base_scale()
        out = {}
        for case in spectrum_cases(self.connector):
            count, directions, functions, scales, systems, angles = api.GetLoads(case)[:6]
            if base is not None:
                scales = [base] * len(as_list(scales))
            out[case] = (count, directions, functions, scales, systems, angles)
        return out

    def _base_scale(self) -> float | None:
        """g I / R of the strength seismic patterns, or None when they cannot be read."""
        from etabs_api.workflows.model_analysis import base_spectrum_scale

        return base_spectrum_scale(self.connector)

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
        report = analyze_model(self.connector, self.settings.zone_factor, self.settings.ct,
                               progress=lambda text: self.show("Analysis", text))
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
                                     self.settings.force_options, beams + columns,
                                     deflection_roles=self.settings.deflection_roles)
        for note in notes:
            self.log("  " + note)

    def design_beams(self) -> pd.DataFrame:
        from design.beam_designer_aci318 import design_beams

        def beam_progress(detail: str) -> None:
            self.show("Beam design", str(detail))

        beam_progress("starting")
        return design_beams(self.tables, self.settings.smrf, self.settings.gravity_combo,
                            self.settings.beam_bars, self.settings.long_limit,
                            progress=beam_progress,
                            earth_cover_stories=self.settings.beam_earth_cover_stories,
                            carrier_depth=self.settings.carrier_depth,
                            compatibility_torsion=self.settings.compatibility_torsion)

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
            bottom_story_cover=self.settings.bottom_cover,
            check_top_level=self.settings.check_top_level,
            check_foundation_level=self.settings.check_foundation_level)
        self.column_geometry = report.attrs.get("slenderness_geometry", {})
        self.foundation_columns = set(report.attrs.get("foundation_columns", []))
        return report

    def column_sizer(self):
        """``sizer`` for column_actions: the member checks of each trial size on the
        forces of the last analysis (column_size_passes), without ETABS."""
        from design.column_designer_aci318 import column_size_passes

        frame = self.tables.get("FRAME DATA")
        loads = self.tables.get("FACTORED LOADS")
        if frame is None or loads is None or frame.empty or loads.empty:
            return None
        rows = {str(name): row for name, row in zip(frame["UniqueName"].astype(str),
                                                     (r for _, r in frame.iterrows()))}
        forces = {str(name): group for name, group in loads.groupby(
            loads["UniqueName"].astype(str))}
        bars = self.settings.column_bars

        def sizer(member: str, sizes: list[Section]) -> int | None:
            if member not in rows or member not in forces:
                return None
            for index, size in enumerate(sizes):
                row = rows[member].copy()
                if size.circular:
                    row["Diameter"] = float(size.depth)
                else:
                    row["Width"], row["Depth"] = float(size.width), float(size.depth)
                self.show("Column sizing on the current forces", f"{member}: {size.name}")
                passes, _ = column_size_passes(
                    row, forces[member], bars["dmain"], bars["dties"], bars["cover"],
                    self.settings.smrf, getattr(self, "column_geometry", {}).get(member),
                    capacity_design=member not in getattr(self, "foundation_columns", ()))
                if passes:
                    return index
            return None

        return sizer

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
            if not ensure_section(self.model, section, self.existing_sections,
                                  getattr(self, "materials", {}),
                                  getattr(self, "covers", {}).get(name)):
                self.log(f"  {name}: could not create {section.name}")
                continue
            if assign_section(self.model, name, section):
                found = getattr(self, "model_sections", None)
                was = found.actual.get(name, old.name) if found is not None else old.name
                changes.append(Change(name, was, section.name, reason))
                if section.area > old.area:
                    grown.add(name)
                self.log(f"  {name}: {was} -> {section.name}  ({reason})")
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
                               allow_shrink, bench.lines())
        if columns:
            _, column_names = bench.members()
            sizer = bench.column_sizer() if settings.size_on_forces else None
            actions.update(column_actions(column_report, sections, bench.angles(column_names),
                                          bench.above(), grown, settings, allow_shrink, sizer))
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
        for _ in range(settings.max_inner_columns):  # columns
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
    "Beam iterations in a round at most": ("max_inner", 10),
    "Beam line shares one size when lengths differ by at most (%)": ("span_similarity", 30),
    "Largest column side (mm)": ("column_max", 1200),
    "Largest column side ratio (long side / short side)": ("column_max_ratio", 2.0),
    "Column iterations in a round at most": ("max_inner_columns", 10),
    "Make smaller when every ratio is below": ("downsize_ratio", 0.7),
    "Rounds (beams, columns, final check) at most": ("max_rounds", 5),
}


def _saved(name: str) -> dict:
    import json

    from utilities.user_settings import settings_path as user_settings_path

    path = user_settings_path(f"{name}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save(name: str, values: dict) -> None:
    import json

    from utilities.user_settings import settings_path as user_settings_path

    path = user_settings_path(f"{name}.json")
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


def sizes_in_model(found: ModelSections | None) -> dict[str, dict[str, list[int]]]:
    """Sizes the model has now, per family: {family: {width / depth / diameter / size: [..]}}."""
    out: dict[str, dict[str, set[int]]] = {}
    for section in (found.sections.values() if found is not None else ()):
        if section.circular:
            out.setdefault("C", {}).setdefault("diameter", set()).add(section.width)
        elif section.family == "CR":
            out.setdefault("CR", {}).setdefault("size", set()).update(
                (section.width, section.depth))
        else:
            sizes = out.setdefault(section.family, {})
            sizes.setdefault("width", set()).add(section.width)
            sizes.setdefault("depth", set()).add(section.depth)
    return {family: {key: sorted(values) for key, values in sizes.items()}
            for family, sizes in out.items()}


def suggested_range(current, present: list[int], step: float = 50.0) -> list[float]:
    """The range to show for one dimension: the range there is, widened to hold
    the sizes the model has now; without a range, from the smallest size in
    the model to two steps above the largest."""
    current = [float(v) for v in current or []]
    if len(current) == 3:
        if not present:
            return current
        return [min(current[0], min(present)), max(current[1], max(present)), current[2]]
    if not present:
        return []
    return [float(min(present)), float(max(present)) + 2 * step, step]


def _ask_ranges(families: set[str], settings: dict, title: str,
                model_has_inputs: bool = True, found: ModelSections | None = None) -> bool:
    """Ask the size range of each family; False when cancelled.

    A family that has a range is shown with it, to confirm or change. With
    ``model_has_inputs`` False the model has no setup inputs of its own and
    the ranges shown are the defaults. ``found`` is what the model has now:
    the ranges shown always hold those sizes, and the dialog lists them.
    """
    from etabs_api.workflows.sections import BEAM_FAMILIES
    from utilities._gui_helpers import enter_values, show_warning

    in_model = sizes_in_model(found)
    for family in sorted(families):
        if family in BEAM_FAMILIES:
            labels = {f"{family} widths: from, to, step (mm)": "width",
                      f"{family} depths: from, to, step (mm)": "depth"}
        elif family == "C":
            labels = {f"{family} diameters: from, to, step (mm)": "diameter"}
        else:
            labels = {f"{family} sides: from, to, step (mm)": "size"}
        current = settings["sections"].get(family, {})
        present = in_model.get(family, {})
        shown = {label: ", ".join(f"{float(v):g}" for v in suggested_range(
            current.get(key), present.get(key, []), 100.0 if family == "C" else 50.0))
                 for label, key in labels.items()}
        prompt = (f"{family} sections have no size range in the setup inputs. Sizes to "
                  "iterate on:" if model_has_inputs else
                  f"This model has no setup inputs of its own. Sizes of the {family} sections "
                  "to iterate on (the loop makes members smaller down to the first size and "
                  "larger up to the last):")
        if present:
            prompt += "\n\nIn the model now: " + "; ".join(
                f"{key} " + ", ".join(str(v) for v in values)
                for key, values in present.items()) + " mm. The range shown holds these sizes."
        while True:
            typed = enter_values(title, prompt, list(labels), shown)
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


def final_drift_check(bench: Workbench, options, folder: str, stem: str) -> dict:
    """Drift of the final sizes (sdt drift), saved to ``<stem> - Drift.txt``.

    The sections are not resized for drift: a failure is reported, and the
    model is to be reconfigured (stiffer members, walls) and designed again.
    """
    from etabs_api.workflows.drift_check import failures, run_drift, save_report

    bench.stage = "Final drift check (final sizes; no resizing for drift)"
    report = run_drift(bench.connector, options.reference, options.wind_denominator,
                       progress=lambda text: bench.show(text), seismic=options.seismic,
                       combos=options.combos, drift_cases=options.drift_cases,
                       r_factor=options.r_factor)
    path = save_report(report, os.path.join(folder, f"{stem} - Drift.txt"))
    failed = failures(report)
    bench.log("")
    bench.log("##### Drift of the final sizes #####")
    bench.log(report.text())
    if failed:
        bench.log(f"DRIFT FAILS ({len(failed)} checks). The sections were not resized for drift: "
                  "reconfigure the model (larger columns or beams, walls) and run sdt design "
                  "again.")
        for level, finding in failed:
            bench.log(f"  {level}: {finding.text}")
    else:
        bench.log("Drift: every check passes.")
    bench.log(f"Drift report: {path}")
    return {"report": report, "failed": failed, "path": path}


def run_design_cli() -> dict | None:
    """Entry point: ask everything once, then run the loop on a working copy."""
    from design.beam_designer_aci318 import ask_deflection_limit, ask_earth_cover_stories
    from design.column_designer_aci318 import (
        ask_capacity_check_levels,
        ask_column_design_options,
        ask_inner_tie_style,
    )
    from etabs_api.core.connection import attach_running_etabs
    from etabs_api.workflows.analysis_forces import ask_force_options
    from etabs_api.workflows.model_setup import load_settings, merge_settings, save_settings
    from etabs_api.workflows.model_setup import settings_path
    from etabs_api.workflows.sections import has_range
    from utilities._gui_helpers import LoadingWindow, enter_values, select_option, show_warning

    title = "Design Loop"
    connector = attach_running_etabs(title)
    if connector is None:
        return None
    model = connector.sap_model
    original = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    if not os.path.isfile(original):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None

    # combinations, forces and the design inputs (as in sdt beams and sdt columns)
    from design.concrete_workflow import (
        confirm_pdelta,
        BEAM_FIELDS,
        COLUMN_FIELDS,
        _ask_numbers,
        _last,
        _remember,
    )
    from utilities._gui_helpers import select_output_directory

    if not confirm_pdelta(connector, title):
        return None
    last = _last()
    from design.concrete_workflow import prepare_model
    from etabs_api.workflows import model_inputs as mi

    ready = prepare_model(connector, original, title, "sdt design", last)
    if ready is None:
        return None
    combos, sources = ready.combos, ready.sources
    seismic = ready.seismic_key or "picked"
    # sections the loop will resize, whatever the model calls them
    found = read_model_sections(connector)
    if not confirm_sections(found, title):
        return None
    if found.foreign:
        sources.model.append(f"sizes of {len(found.foreign)} sections read from ETABS")
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
        choices = ready.gravity_choices[:24]  # what one choice dialog can show
        from design.concrete_workflow import VE_GRAVITY_PROMPT

        gravity = select_option(title, VE_GRAVITY_PROMPT, choices,
                                default_index=choices.index(ready.gravity_default)
                                if ready.gravity_default in choices else 0)
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
    from design.concrete_workflow import beam_stories

    earth_stories = ask_earth_cover_stories(beam_stories(connector), beam_bars["cc"],
                                            last.get("beam_earth_cover_stories"))
    if earth_stories is None:
        return None
    levels = ask_capacity_check_levels(smrf, last)
    if levels is None:
        return None
    tie_style = ask_inner_tie_style()  # for the column schedule of the final design
    if tie_style is None:
        return None
    long_limit = ask_deflection_limit()
    if long_limit is None:
        return None
    from design import dcr_targets
    from design.concrete_workflow import ask_carrier_depth, ask_dcr_targets

    all_types = (dcr_targets.GIRDER, dcr_targets.BEAM, dcr_targets.COLUMN)
    targets = ask_dcr_targets(original, title, all_types)
    if targets is None:
        return None
    carrier_depth = ask_carrier_depth(title, last)
    if carrier_depth is None:
        return None
    from design.concrete_workflow import ask_compatibility_torsion

    compatibility_torsion = ask_compatibility_torsion(title, last)
    if compatibility_torsion is None:
        return None
    from etabs_api.workflows.drift_check import ask_drift_options, has_standard_combinations

    drift_options = ask_drift_options(title + ": drift of the final sizes",
                                      has_standard_combinations(connector))
    if drift_options is None:
        return None
    from etabs_api.workflows.drift_check import resolve_drift_combinations

    drift_options = resolve_drift_combinations(connector, original,
                                               title + ": drift of the final sizes",
                                               drift_options)
    if drift_options is None:
        return None
    folder = select_output_directory("Folder for the final results, calculations and schedules")
    if not folder:
        return None
    _remember({"smrf": smrf, "gravity_combo": gravity, **beam_bars, **column_bars,
               "beam_earth_cover_stories": earth_stories, "check_top_level": levels[0],
               "check_foundation_level": levels[1]})

    # loop limits
    last = _saved("design_loop")
    defaults = {label: f"{last.get(key, value):g}" for label, (key, value) in LOOP_FIELDS.items()}
    typed = enter_values(title, "How the members are resized.", list(LOOP_FIELDS), defaults)
    if typed is None:
        return None
    try:
        values = {key: float(typed[label]) for label, (key, _) in LOOP_FIELDS.items()}
        if values["column_max_ratio"] < 1.0 or min(
                values["max_inner"], values["max_inner_columns"], values["max_rounds"]) < 1:
            raise ValueError
    except ValueError:
        show_warning("Every loop setting must be a number; the side ratio at least 1 and the "
                     "iterations and rounds at least 1.", title=title)
        return None
    _save("design_loop", values)

    # setup inputs: ranges and seismic values
    settings_file = settings_path(original)
    saved_setup = load_settings(settings_file)
    setup = merge_settings(saved_setup)
    has_inputs = "sections" in (saved_setup or {})  # setup inputs, not only saved answers
    families = set(found.families())
    missing = {f for f in families if not has_range(setup["sections"], f)}
    if not has_inputs:
        # No setup inputs beside this model: the default ranges would be used
        # without the user seeing them, so every family is shown to confirm.
        missing = set(families)
    if missing:
        if not _ask_ranges(missing, setup, title, model_has_inputs=has_inputs, found=found):
            return None
        # a model without setup inputs keeps only what was asked: the ranges
        save_settings(setup if has_inputs else
                      {**(saved_setup or {}), "sections": setup["sections"]}, settings_file)
        sources.answered.append("size ranges of " + ", ".join(sorted(missing)))
    # Z and Ct: from the model's UBC 97 patterns; asked when they differ from
    # what was saved, or when the model has no such pattern
    # (an answer given on this model before stands over the setup inputs)
    answer = mi.ask_seismic_values(
        original, title, mi.read_seismic(connector),
        {**((saved_setup or {}).get("seismic") or {}), **mi.load(original).get("seismic", {})})
    if answer is None:
        return None
    seismic_values, seismic_sources = answer
    for kind in ("model", "answered", "assumed"):
        getattr(sources, kind).extend(getattr(seismic_sources, kind))

    settings = LoopSettings(
        combos=combos, force_options=force_options, gravity_combo=gravity, smrf=smrf,
        beam_bars=beam_bars, column_bars=column_bars,
        continuous_bars=column_answers[0], bottom_cover=column_answers[1],
        long_limit=long_limit,
        limits=Limits(int(values["increment"]), int(values["beam_max_width"]),
                      int(values["beam_max_depth"]), int(values["column_max"]),
                      float(values["column_max_ratio"])),
        ranges=setup["sections"], downsize_ratio=values["downsize_ratio"],
        span_similarity=values["span_similarity"] / 100.0,
        max_rounds=int(values["max_rounds"]), max_inner=int(values["max_inner"]),
        max_inner_columns=int(values["max_inner_columns"]),
        zone_factor=seismic_values["zone_factor"], ct=seismic_values["ct"],
        beam_earth_cover_stories=tuple(earth_stories), check_top_level=levels[0],
        check_foundation_level=levels[1], inner_tie_style=tie_style,
        deflection_roles=ready.deflection_roles, sources=sources,
        targets=targets, carrier_depth=carrier_depth,
        compatibility_torsion=compatibility_torsion,
    )

    # the working copy: the original model is not changed
    stem = os.path.splitext(original)[0]
    working = f"{stem} - DESIGN.EDB"
    model.File.Save(working)
    log_path = f"{stem} - DESIGN log.txt"
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(f"Design loop of {original}\nWorking copy: {working}\n"
                     f"Started {time.strftime('%Y-%m-%d %H:%M')}\n")
    with dcr_targets.use(targets), \
            LoadingWindow("Design loop: analysis, design and resizing") as window:
        bench = Workbench(connector, settings, log_path, window.update)
        for line in targets.lines(all_types):
            bench.log(f"Target ratio: {line}")
        if carrier_depth:
            bench.log("A beam is at least as deep as the beams it carries.")
        if compatibility_torsion:
            bench.log("Beam torsion at most phi Tcr (compatibility torsion, ACI 22.7.3.2).")
        bench.log(f"Combinations: {len(combos)} ULS ({seismic})")
        bench.log("Size ranges (from, to, step): " + "; ".join(
            f"{family} " + " x ".join(
                "-".join(f"{float(v):g}" for v in span) for span in spans.values() if span)
            for family, spans in sorted(setup["sections"].items()) if family in families))
        bench.log(f"Limits: beams {settings.limits.beam_max_width} x "
                  f"{settings.limits.beam_max_depth}, column side {settings.limits.column_max}, "
                  f"side ratio {settings.limits.column_max_ratio:g}, smallest column side "
                  f"{joint_min_side(settings):g} (20 beam bar diameters, ACI 18.8.2.3)")
        summary = run_design_loop(bench)
        window.update("Saving the final results, calculations and schedules")
        model.File.Save()  # the working copy; Save(path) would drop the analysis results
        save_final_design(bench, summary, working, folder)
        summary["drift"] = final_drift_check(bench, drift_options, folder,
                                             os.path.splitext(os.path.basename(working))[0])
    print(f"Working copy: {working}\nLog: {log_path}\nOutputs: {folder}")
    if summary["drift"]["failed"]:
        print("DRIFT FAILS: the sections were not resized for drift. Reconfigure the model "
              f"for drift and run sdt design again. See {summary['drift']['path']}")
    loop_summary(summary, settings, original, working, log_path, folder).show(
        os.path.join(folder, f"{os.path.splitext(os.path.basename(working))[0]} summary.txt"),
        popup=True, echo=False)
    return summary


def loop_summary(summary: dict, settings: LoopSettings, original: str, working: str,
                 log_path: str, folder: str):
    """The closing summary of the design loop, for its window. Every iteration is
    in the terminal and in the log file."""
    from utilities.run_summary import RunSummary, listed

    out = RunSummary("sdt design", original)
    changes = summary.get("changes", [])
    final: dict[str, tuple[str, str]] = {}
    for change in changes:  # first old size and last new size of every member
        first = final.get(change.member, (change.old, change.new))[0]
        final[change.member] = (first, change.new)
    resized = {member: sizes for member, sizes in final.items() if sizes[0] != sizes[1]}
    out.add("Status", f"{summary.get('status', '')} after {summary.get('iterations', 0)} "
                      "iterations")
    out.add("Combinations", len(settings.combos))
    beams, columns = summary.get("beams"), summary.get("columns")
    if beams is not None and len(beams):
        failing = beams.groupby("UniqueName")["Design_Status"].apply(
            lambda s: any(v != "OK" for v in s.astype(str))).sum()
        out.add("Beams", f"{beams['UniqueName'].nunique()} designed, {int(failing)} failing")
    if columns is not None and len(columns):
        failing = columns.groupby("UniqueName")["Column_Design_Status"].apply(
            lambda s: (s.astype(str) == "FAIL").any()).sum()
        out.add("Columns", f"{columns['UniqueName'].nunique()} designed, {int(failing)} failing")
    out.add("Section changes", f"{len(changes)} in all; {len(resized)} members end with "
                               "another size")
    by_size: dict[tuple[str, str], list[str]] = {}
    for member, sizes in resized.items():
        by_size.setdefault(sizes, []).append(member)
    for (old, new), members in sorted(by_size.items()):
        out.add(f"  {old} -> {new}", listed(sorted(members), 8))
    drift = summary.get("drift") or {}
    if drift:
        out.add("Drift of the final sizes",
                f"{len(drift.get('failed', []))} checks failing" if drift.get("failed")
                else "every check passes")
    if summary.get("failing"):
        out.fail("Members still failing: " + listed(summary["failing"]))
    if drift.get("failed"):
        out.fail("Drift fails: the sections are not resized for drift. Reconfigure the model "
                 "(stiffer members, walls) and run sdt design again.")
    if settings.targets is not None:
        from design import dcr_targets
        from design.concrete_workflow import add_targets_to

        add_targets_to(out, settings.targets,
                       (dcr_targets.GIRDER, dcr_targets.BEAM, dcr_targets.COLUMN))
    if settings.carrier_depth:
        out.add("Carrier depth", "a beam is at least as deep as the beams it carries")
    if settings.compatibility_torsion:
        from design.concrete_workflow import TORSION_NOTE

        out.add("Beam torsion", "at most phi Tcr (compatibility torsion, ACI 22.7.3.2)")
        out.note(TORSION_NOTE)
    if settings.sources is not None:
        settings.sources.add_to(out)
    if settings.beam_earth_cover_stories:
        out.note("75 mm beam cover on: " + ", ".join(settings.beam_earth_cover_stories))
    if not settings.check_top_level:
        out.note("BCC and joint shear were not checked at the topmost level (your choice).")
    if not settings.check_foundation_level:
        out.note("BCC, joint shear and Ve were not checked at the foundation level "
                 "(your choice).")
    out.file("Working copy", working)
    out.file("Log (every iteration)", log_path)
    out.file("Outputs", folder)
    if drift.get("path"):
        out.file("Drift report", drift["path"])
    return out


def save_final_design(bench: Workbench, summary: dict, working: str, folder: str) -> None:
    """Store the final design for sdt columns and write the result files."""
    say = bench.progress
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
    from utilities.latex_help import missing_pdf_reason

    settings = bench.settings
    stem = os.path.splitext(os.path.basename(working))[0]
    beams, columns = summary.get("beams"), summary.get("columns")
    store = DesignStore(working, os.path.getmtime(working), bench.tables, {
        "combos": settings.combos, "smrf": settings.smrf, "gravity_combo": settings.gravity_combo,
        "beam_bars": settings.beam_bars, "column_bars": settings.column_bars,
        "long_limit": settings.long_limit,
        "beam_earth_cover_stories": list(settings.beam_earth_cover_stories),
        "check_top_level": settings.check_top_level,
        "check_foundation_level": settings.check_foundation_level,
        "inner_tie_style": settings.inner_tie_style,
    })
    store.beam_results, store.column_report = beams, columns
    store.column_groups = getattr(bench, "column_groups", None)
    say("Saving the design data for sdt columns")
    store.save()
    if beams is not None and len(beams):
        say("Saving the beams 1 of 3: results workbook (.xlsx)")
        write_beam_results_xlsx(beams, os.path.join(folder, f"{stem} - Beam Design.xlsx"))
        say("Saving the beams 2 of 3: schedules (.dxf)")
        export_beam_dxf(beams, folder)
        say("Saving the beams 3 of 3: calculation report (.pdf, LaTeX)")
        if not export_beam_pdf(beams, os.path.join(folder, f"{stem} - Beam Calculations.pdf"),
                               settings.smrf, settings.gravity_combo):
            bench.log("Beam calculation report " + missing_pdf_reason())
    if columns is not None and len(columns) and store.column_groups:
        bars = settings.column_bars
        say("Saving the columns 1 of 3: results workbook (.xlsx)")
        write_column_results_xlsx(columns, store.column_groups,
                                  os.path.join(folder, f"{stem} - Column Design.xlsx"))
        say("Saving the columns 2 of 3: schedule (.dxf)")
        export_column_cad_drawings(columns, folder, bars["dmain"], bars["cover"], settings.smrf,
                                   settings.inner_tie_style, bench.tables["CONNECTIVITY"])
        say("Saving the columns 3 of 3: calculation report (.pdf, LaTeX)")
        if not export_column_pdf(
                columns, os.path.join(folder, f"{stem} - Column Calculations.pdf"),
                settings.smrf, bars["dmain"], bars["dties"], bars["cover"]):
            bench.log("Column calculation report " + missing_pdf_reason())


if __name__ == "__main__":
    run_design_cli()
