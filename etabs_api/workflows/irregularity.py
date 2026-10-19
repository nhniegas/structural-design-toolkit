"""Structural irregularities and the P-delta stability coefficient (``sdt irregularity``).

The command reads the analysed model open in ETABS and reports each
irregularity of UBC 97 Tables 16-L and 16-M (NSCP 2015 Tables 208-9 and
208-10) as regular, irregular, or left to the engineer, with the numbers
behind it. It changes nothing in the model.

Computed from the analysis results, on the static seismic load cases:

* **Torsional irregularity** (plan type 1): the largest story drift at one
  end of the story over the average of the two ends, more than 1.2. With it
  the accidental torsion amplifier ``Ax = (dmax / 1.2 davg)^2`` of each level,
  between 1 and 3 (UBC 1630.7). Ax is reported, not applied to the model.
* **Soft story** (vertical type 1): a story stiffness under 70 % of the story
  above, or under 80 % of the average of the three stories above.
* **Mass** (vertical type 2): a story mass over 150 % of an adjacent story.
* **Vertical geometric** (type 3): the plan dimension of the columns and
  walls of a story over 130 % of that of an adjacent story.
* **Weak story** (type 5): a story strength under 80 % of the story above,
  from the column design (``sdt columns`` must have run).
* **Re-entrant corners** (plan type 2) and the openings of the diaphragm
  (plan type 3), from the outline of the floor shells.
* A story with no diaphragm (no floor or joint assigned one) is left out of
  the story-to-story checks above and listed: its mass, stiffness and drift
  are not those of a floor. A model with no diaphragm at all is compared as is.
* **P-delta stability coefficient** ``theta = Px D / (Vx h)`` of each story
  (UBC 1630.1.3), on first-order drifts: a copy of the model is analysed with
  P-delta off, and removed.

Left to the engineer, with what the model shows: in-plane discontinuity
(vertical type 4), out-of-plane offsets (plan type 4) and non-parallel
systems (plan type 5).

The functions that only work on plain data need no ETABS.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

OK, INFO, WARN, NA = "OK", "INFO", "WARN", "N/A"
REGULAR, IRREGULAR, JUDGE = "regular", "IRREGULAR", "for the engineer to judge"

TORSION_LIMIT = 1.2          # dmax / davg above this: torsional irregularity
AX_MAX = 3.0
SOFT_ABOVE = 0.70            # stiffness under this share of the story above
SOFT_THREE = 0.80            # ... or of the average of the three above
MASS_LIMIT = 1.50
GEOMETRY_LIMIT = 1.30
WEAK_LIMIT = 0.80
WEAK_SEVERE = 0.65           # under this, UBC 1629.9.1 limits the height
REENTRANT_LIMIT = 0.15
OPENING_LIMIT = 0.50
THETA_LIMIT = 0.10
MINOR_LEVEL = 0.10           # a level under this share of the largest story mass is not a story
NO_VALUE = 1e-9

VERTICAL, PLAN, STABILITY = "Vertical irregularities", "Plan irregularities", "P-delta"


@dataclass
class Item:
    """One irregularity type, or the stability check, with its numbers."""

    group: str
    name: str
    ref: str
    status: str = NA
    verdict: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def irregular(self) -> bool:
        return self.status == WARN


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _directions(rows, case_at: int = 1, direction_at: int = 2) -> list[str]:
    return list(dict.fromkeys(str(r[direction_at]) for r in rows))


# =============================================================================
# CHECKS (plain data)
# =============================================================================
def compared_stories(masses: list[tuple[str, float]], base: str | None
                     ) -> tuple[list[str], list[str], list[str]]:
    """Which levels are stories to compare with each other, from the top down:
    (stories, levels at or below the base, minor levels).

    ETABS has a story for every level of the model. Two kinds are not stories
    of the structure for the vertical irregularities:

    * the level the static seismic patterns start from (``base``) and those
      below it: they are at the base, where no story shear is carried over;
    * a minor level, with under ``MINOR_LEVEL`` of the mass of the largest
      story: a footing tie level, a landing, a small mezzanine.

    Compared with a full story, either gives a mass ratio in the hundreds and a
    false irregularity.
    """
    names = [story for story, _ in masses]
    below = names[names.index(base):] if base in names else []
    above = [(story, mass) for story, mass in masses if story not in below]
    largest = max((mass for _, mass in above), default=0.0)
    minor = [story for story, mass in above if mass < MINOR_LEVEL * largest]
    stories = [story for story, _ in above if story not in minor]
    return stories, below, minor


def diaphragm_stories(rows: list[dict]) -> set[str]:
    """The stories with a diaphragm, from rows of the area and joint diaphragm
    assignments (each with "Story" and "Diaphragm"). A joint "From Shell Object"
    has a diaphragm only through a floor, which the area rows give."""
    return {str(row.get("Story")) for row in rows
            if str(row.get("Diaphragm") or "").strip() not in ("", "None", "nan",
                                                                 "From Shell Object")
            and str(row.get("Story") or "").strip() not in ("", "None", "nan")}


def without_diaphragm(order: list[str], with_diaphragm: set[str]) -> list[str]:
    """The stories of ``order`` with no diaphragm. None are when the model has no
    diaphragm at all: then every story is compared as before."""
    if not with_diaphragm:
        return []
    return [story for story in order if story not in with_diaphragm]


def levels_note(below: list[str], minor: list[str], no_diaphragm: list[str] | None = None
                ) -> str:
    """What is said of the levels left out of the vertical comparisons."""
    parts = []
    if no_diaphragm:
        whose = "their" if len(no_diaphragm) > 1 else "its"
        parts.append(", ".join(no_diaphragm) + f" (no diaphragm: {whose} mass, stiffness and "
                     "drift are not those of a floor)")
    if below:
        parts.append(", ".join(below) + " (at or below the base of the seismic patterns)")
    if minor:
        parts.append(", ".join(minor) + f" (under {MINOR_LEVEL * 100:g} % of the mass of the "
                     "largest story: a minor level, not a story)")
    return "Levels not compared as stories: " + "; ".join(parts) + "." if parts else ""


def own_direction(rows: list[tuple], weight: int = -1) -> list[tuple]:
    """The rows of each load case in its own direction only.

    ETABS lists a seismic case in both directions. Across its own direction a
    case moves the structure very little, and a ratio of two such movements
    means nothing: an X case would be reported as torsionally irregular in Y.
    The direction of a case is the one where ``weight`` (a column of the rows,
    such as the average drift or the story shear) adds up to the most.
    ``rows``: (story, case, direction, ...).
    """
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        own = totals.setdefault(str(row[1]), {})
        own[str(row[2])] = own.get(str(row[2]), 0.0) + abs(float(row[weight]))
    main = {case: max(found, key=found.get) for case, found in totals.items()}
    return [row for row in rows if main[str(row[1])] == str(row[2])]


def torsion_item(drifts: list[tuple], displacements: list[tuple], order: list[str]) -> Item:
    """``drifts`` and ``displacements``: (story, case, direction, maximum, average)
    of each static seismic case. ``order``: the stories from the top down.

    For each story and direction, the case with the largest ratio is shown.
    """
    item = Item(PLAN, "1. Torsional irregularity", "UBC Table 16-M type 1, 1630.7")
    if not drifts:
        item.verdict = "no static seismic case with results"
        return item
    ax_of = {}
    for story, case, direction, most, average in displacements:
        if average > NO_VALUE:
            ax_of[(story, case, direction)] = min(max((most / (1.2 * average)) ** 2, 1.0), AX_MAX)
    worst: dict[tuple[str, str], tuple] = {}
    for story, case, direction, most, average in drifts:
        if average <= NO_VALUE:
            continue
        ratio = most / average
        key = (story, direction)
        if key not in worst or ratio > worst[key][0]:
            worst[key] = (ratio, case, most, average)
    item.headers = ["Story", "Dir.", "Case", "Max drift", "Avg drift", "Max / Avg", "Ax", "Result"]
    for story in order:
        for direction in sorted({d for _, d in worst}):
            if (story, direction) not in worst:
                continue
            ratio, case, most, average = worst[(story, direction)]
            known = [v for (s, _, d), v in ax_of.items() if s == story and d == direction]
            item.rows.append([story, direction, case, _fmt(most), _fmt(average), _fmt(ratio),
                              _fmt(max(known), 2) if known else "-",
                              IRREGULAR if ratio > TORSION_LIMIT else REGULAR])
    top = max((v[0] for v in worst.values()), default=0.0)
    ax_top = max(ax_of.values(), default=1.0)
    item.status = WARN if top > TORSION_LIMIT else OK
    item.verdict = (f"{IRREGULAR}: max / avg drift {top:.3f} > {TORSION_LIMIT:g}; largest Ax "
                    f"{ax_top:.2f}" if top > TORSION_LIMIT else
                    f"{REGULAR}: max / avg drift {top:.3f} <= {TORSION_LIMIT:g}")
    item.equations = [r"\delta_{max} / \delta_{ave} > 1.2 \Rightarrow irregular",
                      r"A_x = (\delta_{max} / (1.2 \delta_{ave}))^2, 1.0 \le A_x \le 3.0"]
    if item.irregular:
        item.notes.append(
            "The accidental torsion of each level is to be multiplied by its Ax (UBC 1630.7). "
            "Ax is reported here; it is not applied to the seismic patterns of the model.")
    if any(row[6] == "-" for row in item.rows):
        item.notes.append("Ax is from the displacements of a level; \"-\" where ETABS gives "
                          "none for it (a level with no diaphragm).")
    return item


def soft_story_item(stiffness: list[tuple], order: list[str]) -> Item:
    """``stiffness``: (story, case, direction, stiffness) of each static seismic
    case. For each story and direction the case with the lowest ratio is shown."""
    item = Item(VERTICAL, "1. Stiffness irregularity (soft story)", "UBC Table 16-L type 1")
    by_case: dict[tuple[str, str], dict[str, float]] = {}
    for story, case, direction, value in stiffness:
        if value > NO_VALUE:
            by_case.setdefault((case, direction), {})[story] = value
    if not by_case:
        item.verdict = "no story stiffness in the results"
        return item
    worst: dict[tuple[str, str], tuple] = {}
    for (case, direction), values in by_case.items():
        stories = [s for s in order if s in values]            # from the top down
        for index, story in enumerate(stories):
            above = [values[s] for s in stories[max(0, index - 3):index]][::-1]   # nearest first
            if not above:
                continue
            ratio_above = values[story] / above[0]
            ratio_three = values[story] / (sum(above) / 3.0) if len(above) == 3 else math.nan
            margin = min(ratio_above / SOFT_ABOVE,
                         ratio_three / SOFT_THREE if ratio_three == ratio_three else math.inf)
            key = (story, direction)
            if key not in worst or margin < worst[key][0]:
                worst[key] = (margin, case, values[story], ratio_above, ratio_three)
    item.headers = ["Story", "Dir.", "Case", "Stiffness (kN/mm)", "/ story above",
                    "/ avg of 3 above", "Result"]
    for story in order:
        for direction in sorted({d for _, d in worst}):
            if (story, direction) not in worst:
                continue
            margin, case, value, above, three = worst[(story, direction)]
            item.rows.append([story, direction, case, _fmt(value / 1000.0, 1), _fmt(above, 2),
                              _fmt(three, 2) if three == three else "-",
                              IRREGULAR if margin < 1.0 else REGULAR])
    soft = [r[0] for r in item.rows if r[-1] == IRREGULAR]
    item.status = WARN if soft else OK
    item.verdict = (f"{IRREGULAR}: soft story at " + ", ".join(dict.fromkeys(soft))
                    if soft else f"{REGULAR}: no story under 70 % of the story above or 80 % "
                    "of the average of the three above")
    item.equations = [r"K_i < 0.70 K_{i+1}  or  K_i < 0.80 (K_{i+1} + K_{i+2} + K_{i+3}) / 3 "
                      r"\Rightarrow soft story", r"K = V_{story} / \Delta_{story}"]
    return item


def mass_item(masses: list[tuple[str, float]]) -> Item:
    """``masses``: (story, mass) from the top down, the base left out."""
    item = Item(VERTICAL, "2. Weight (mass) irregularity", "UBC Table 16-L type 2")
    levels = [(s, m) for s, m in masses if m > NO_VALUE]
    if len(levels) < 2:
        item.verdict = "fewer than two stories with mass"
        return item
    item.headers = ["Story", "Mass (t)", "/ story above", "/ story below", "Result"]
    heavy = []
    for index, (story, mass) in enumerate(levels):
        above = mass / levels[index - 1][1] if index > 0 else math.nan
        below = mass / levels[index + 1][1] if index + 1 < len(levels) else math.nan
        # a roof lighter than the floor below need not be considered
        counted_above = above if index != 1 else math.nan
        flagged = any(r == r and r > MASS_LIMIT for r in (counted_above, below))
        if flagged:
            heavy.append(story)
        item.rows.append([story, _fmt(mass, 1), _fmt(above, 2) if above == above else "-",
                          _fmt(below, 2) if below == below else "-",
                          IRREGULAR if flagged else REGULAR])
    item.status = WARN if heavy else OK
    item.verdict = (f"{IRREGULAR}: " + ", ".join(heavy) + " over 150 % of an adjacent story"
                    if heavy else f"{REGULAR}: no story over 150 % of an adjacent story")
    item.equations = [r"m_i > 1.50 m_{next} \Rightarrow irregular"]
    item.notes.append("A roof lighter than the floor below is not counted.")
    return item


def geometry_item(extents: list[tuple[str, float, float]]) -> Item:
    """``extents``: (story, length along X, length along Y) of the columns and
    walls of each story, from the top down."""
    item = Item(VERTICAL, "3. Vertical geometric irregularity", "UBC Table 16-L type 3")
    levels = [e for e in extents if e[1] > NO_VALUE or e[2] > NO_VALUE]
    if len(levels) < 2:
        item.verdict = "fewer than two stories with columns or walls"
        return item
    item.headers = ["Story", "Lx (m)", "Ly (m)", "Lx / adjacent", "Ly / adjacent", "Result"]
    found = []
    for index, (story, lx, ly) in enumerate(levels):
        near = [levels[i] for i in (index - 1, index + 1) if 0 <= i < len(levels)]
        rx = max((lx / n[1] for n in near if n[1] > NO_VALUE), default=math.nan)
        ry = max((ly / n[2] for n in near if n[2] > NO_VALUE), default=math.nan)
        flagged = any(r == r and r > GEOMETRY_LIMIT for r in (rx, ry))
        if flagged:
            found.append(story)
        item.rows.append([story, _fmt(lx / 1000.0, 2), _fmt(ly / 1000.0, 2),
                          _fmt(rx, 2) if rx == rx else "-", _fmt(ry, 2) if ry == ry else "-",
                          IRREGULAR if flagged else REGULAR])
    item.status = WARN if found else OK
    item.verdict = (f"{IRREGULAR}: " + ", ".join(found) + " over 130 % of an adjacent story"
                    if found else f"{REGULAR}: no story over 130 % of an adjacent story")
    item.equations = [r"L_i > 1.30 L_{next} \Rightarrow irregular"]
    item.notes.append("L is the distance between the outermost columns and walls of a story. "
                      "A one-story penthouse need not be considered.")
    return item


def weak_story_item(strengths: list[tuple[str, float, int]] | None) -> Item:
    """``strengths``: (story, strength in kN, columns) from the top down; None
    when the model has no stored column design."""
    item = Item(VERTICAL, "5. Discontinuity in capacity (weak story)",
                "UBC Table 16-L type 5, 1629.9.1")
    if strengths is None:
        item.verdict = ("not checked: run sdt columns first (the story strength comes from "
                        "the column design)")
        return item
    levels = [s for s in strengths if s[1] > NO_VALUE]
    if len(levels) < 2:
        item.verdict = "not checked: fewer than two stories with designed columns"
        return item
    item.headers = ["Story", "Columns", "Strength (kN)", "/ story above", "Result"]
    weak, severe = [], []
    for index, (story, strength, count) in enumerate(levels):
        ratio = strength / levels[index - 1][1] if index > 0 else math.nan
        flagged = ratio == ratio and ratio < WEAK_LIMIT
        if flagged:
            weak.append(story)
        if ratio == ratio and ratio < WEAK_SEVERE:
            severe.append(story)
        item.rows.append([story, count, _fmt(strength, 0), _fmt(ratio, 2) if ratio == ratio
                          else "-", IRREGULAR if flagged else REGULAR])
    item.status = WARN if weak else OK
    item.verdict = (f"{IRREGULAR}: weak story at " + ", ".join(weak) if weak else
                    f"{REGULAR}: no story under 80 % of the strength of the story above")
    item.equations = [r"V_i < 0.80 V_{i+1} \Rightarrow weak story",
                      r"V_{story} = \sum min(\phi V_n, (\phi M_{n1} + \phi M_{n2}) / l_u)"]
    item.notes.append(
        "The story strength is the sum over its columns of the shear at which the column "
        "yields in flexure at both ends, limited by its shear strength, both from the last "
        "column design (the lowest values of each column). Walls and braces are not in it.")
    if severe:
        item.notes.append("Strength under 65 % of the story above at " + ", ".join(severe) +
                          ": UBC 1629.9.1 limits such a structure to two stories or 9 m, unless "
                          "the weak story resists the forces amplified by the overstrength.")
    return item


def story_strengths(report, order: list[str]) -> list[tuple[str, float, int]]:
    """The strength of each story from the column design report (a DataFrame with
    one row for each column end and combination), from the top down."""
    import pandas as pd

    def number(series):
        return pd.to_numeric(series, errors="coerce")

    frame = pd.DataFrame({
        "story": report["Story"].astype(str), "column": report["UniqueName"].astype(str),
        "end": report["End"].astype(str), "shear": number(report["phi_Vn_kN"]),
        "moment": number(report["phi_Mn_kNm"]),
        "lu": number(pd.concat([number(report["Slender_lu3_mm"]),
                                number(report["Slender_lu2_mm"])], axis=1).min(axis=1))})
    out = []
    for story in order:
        own = frame[frame["story"] == story]
        total, count = 0.0, 0
        for _, rows in own.groupby("column"):
            shear = rows["shear"].min()
            ends = rows.groupby("end")["moment"].min().dropna()
            lu = rows["lu"].min()
            if not (shear == shear) or not len(ends) or not (lu == lu) or lu <= 0:
                continue
            flexure = float(ends.sum()) * (2.0 / len(ends)) / (lu / 1000.0)   # both ends
            total += min(float(shear), flexure)
            count += 1
        out.append((story, total, count))
    return out


def plan_shape(points_by_shell: list[list[tuple[float, float]]]):
    """What the floor outline of one story shows: (Lx, Ly, the largest pair of
    re-entrant projections as shares of Lx and Ly, the share of the enclosed
    area that is open). None when the shells give no outline.

    The outline is turned so that its longest side runs along X. A notch of
    the outline, where it turns inward from its convex hull, measures the
    projections of the structure beyond the re-entrant corner. A corner that is
    cut off at an angle is not a notch.
    """
    from shapely import affinity
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polygons = []
    for points in points_by_shell:
        if len(points) >= 3:
            polygon = Polygon(points)
            polygon = polygon if polygon.is_valid else polygon.buffer(0)
            if polygon.area > 0:
                polygons.append(polygon.buffer(1.0))
    if not polygons:
        return None
    floor = unary_union(polygons).buffer(-1.0)
    if floor.geom_type != "Polygon":
        floor = max(getattr(floor, "geoms", [floor]), key=lambda g: g.area)
    corners = list(floor.minimum_rotated_rectangle.exterior.coords)
    edges = [(corners[i + 1][0] - corners[i][0], corners[i + 1][1] - corners[i][1])
             for i in range(2)]
    longest = max(edges, key=lambda e: math.hypot(*e))
    floor = affinity.rotate(floor, -math.degrees(math.atan2(longest[1], longest[0])),
                            origin="centroid")
    x0, y0, x1, y1 = floor.bounds
    lx, ly = x1 - x0, y1 - y0
    outer = Polygon(floor.exterior)
    notches = outer.convex_hull.difference(outer)
    worst = (0.0, 0.0)
    for notch in getattr(notches, "geoms", [notches]):
        if notch.is_empty or notch.area < 1e-4 * lx * ly:
            continue
        a, b, c, d = notch.bounds
        share = ((c - a) / lx, (d - b) / ly)
        if min(share) > min(worst):
            worst = share
    opening = 1.0 - floor.area / outer.area if outer.area > 0 else 0.0
    return lx, ly, worst, opening


def plan_items(shapes: list[tuple[str, tuple | None]]) -> tuple[Item, Item]:
    """The re-entrant corner and diaphragm opening items from ``plan_shape`` of
    each story, from the top down."""
    corners = Item(PLAN, "2. Re-entrant corners", "UBC Table 16-M type 2")
    opening = Item(PLAN, "3. Diaphragm discontinuity", "UBC Table 16-M type 3")
    known = [(story, shape) for story, shape in shapes if shape is not None]
    if not known:
        corners.verdict = opening.verdict = "not checked: the model has no floor shells"
        return corners, opening
    corners.headers = ["Story", "Lx (m)", "Ly (m)", "Projection / Lx", "Projection / Ly", "Result"]
    opening.headers = ["Story", "Open share of the enclosed area", "Result"]
    notched, open_stories = [], []
    for story, (lx, ly, (px, py), share) in known:
        flagged = px > REENTRANT_LIMIT and py > REENTRANT_LIMIT
        if flagged:
            notched.append(story)
        corners.rows.append([story, _fmt(lx / 1000.0, 2), _fmt(ly / 1000.0, 2), _fmt(px, 2),
                             _fmt(py, 2), IRREGULAR if flagged else REGULAR])
        if share > OPENING_LIMIT:
            open_stories.append(story)
        opening.rows.append([story, f"{share * 100:.0f} %",
                             IRREGULAR if share > OPENING_LIMIT else REGULAR])
    corners.status = WARN if notched else OK
    corners.verdict = (f"{IRREGULAR}: both projections over 15 % at " + ", ".join(notched)
                       if notched else f"{REGULAR}: no re-entrant corner with both projections "
                       "over 15 % of the plan dimension")
    corners.equations = [r"p_x > 0.15 L_x  and  p_y > 0.15 L_y \Rightarrow irregular"]
    opening.status = WARN if open_stories else INFO
    opening.verdict = (f"{IRREGULAR}: openings over 50 % of the enclosed area at "
                       + ", ".join(open_stories) if open_stories else
                       "openings under 50 % of the enclosed area; the change of diaphragm "
                       f"stiffness from story to story is {JUDGE}")
    opening.notes.append("Only the open area inside the floor outline is measured. A change of "
                         "more than 50 % in the effective diaphragm stiffness from one story to "
                         "the next is also this irregularity, and is not measured.")
    return corners, opening


def judged_items(planted: list[str], story_count: int) -> list[Item]:
    """The types left to the engineer, with what the model shows for them."""
    text = (f"{len(planted)} columns start on a floor, with no column under them ("
            + ", ".join(planted[:8]) + (", ..." if len(planted) > 8 else "") + ")"
            if planted else "every column runs down to a column or to the base")
    offset = Item(VERTICAL, "4. In-plane discontinuity in vertical elements",
                  "UBC Table 16-L type 4", INFO,
                  f"{JUDGE}: {text}")
    plane = Item(PLAN, "4. Out-of-plane offsets", "UBC Table 16-M type 4", INFO,
                 f"{JUDGE}: {text}")
    parallel = Item(PLAN, "5. Non-parallel systems", "UBC Table 16-M type 5", INFO,
                    f"{JUDGE}: whether the frames and walls run along two orthogonal axes")
    if planted:
        note = ("A column that starts on a floor interrupts the path of the lateral force: "
                "its supports are to be designed for the special seismic load combinations "
                "(UBC 1630.8.2).")
        offset.notes.append(note)
        plane.notes.append(note)
    return [offset, plane, parallel]


def stability_item(rows: list[tuple], pdelta_on: bool, r_factor: float | None = None,
                   first_order: bool = True) -> Item:
    """``rows``: (story, case, direction, P in N, V in N, drift in mm, height in mm),
    from the top down. For each story and direction the case with the largest
    coefficient is shown."""
    item = Item(STABILITY, "P-delta stability coefficient", "UBC 1630.1.3")
    worst: dict[tuple[str, str], tuple] = {}
    order = []
    for story, case, direction, axial, shear, drift, height in rows:
        if abs(shear) <= NO_VALUE or height <= NO_VALUE:
            continue
        theta = abs(axial) * abs(drift) / (abs(shear) * height)
        key = (story, direction)
        if story not in order:
            order.append(story)
        if key not in worst or theta > worst[key][0]:
            worst[key] = (theta, case, abs(axial), abs(shear), abs(drift), height)
    if not worst:
        item.verdict = "not checked: no story shear or drift in the results"
        return item
    item.headers = ["Story", "Dir.", "Case", "Px (kN)", "Vx (kN)", "Drift (mm)", "h (mm)",
                    "theta", "Drift / h", "Result"]
    for story in order:
        for direction in sorted({d for _, d in worst}):
            if (story, direction) not in worst:
                continue
            theta, case, axial, shear, drift, height = worst[(story, direction)]
            item.rows.append([story, direction, case, _fmt(axial / 1000.0, 0),
                              _fmt(shear / 1000.0, 1), _fmt(drift, 2), _fmt(height, 0),
                              _fmt(theta, 4), _fmt(drift / height, 5),
                              "P-delta to consider" if theta > THETA_LIMIT else "may be neglected"])
    top = max(v[0] for v in worst.values())
    over = top > THETA_LIMIT
    state = "on" if pdelta_on else "off"
    if over and not pdelta_on:
        item.status = WARN
        item.verdict = (f"theta {top:.4f} > {THETA_LIMIT:g}: P-delta must be considered, and "
                        "it is off in the model")
    elif over:
        item.status = OK
        item.verdict = (f"theta {top:.4f} > {THETA_LIMIT:g}: P-delta must be considered; it is "
                        "on in the model")
    else:
        item.status = OK
        item.verdict = (f"theta {top:.4f} <= {THETA_LIMIT:g}: P-delta may be neglected "
                        f"(it is {state} in the model)")
    item.equations = [r"\theta = P_x \Delta / (V_x h) \le 0.10"]
    item.notes.append(
        "Px is the dead and floor live load above the story, unfactored; Vx the story shear "
        "of the seismic case; Delta its story drift at the design forces, "
        + ("from an analysis of a copy of the model with P-delta off (first order)."
           if first_order else "from the model as analysed."))
    if r_factor and r_factor > 0:
        limit = 0.02 / r_factor
        ratio = max(v[4] / v[5] for v in worst.values())
        item.notes.append(
            f"In seismic zones 3 and 4, P-delta also need not be considered where the story "
            f"drift ratio is at most 0.02 / R = {limit:.5f} (R = {r_factor:g}): the largest "
            f"here is {ratio:.5f}, " + ("within it." if ratio <= limit else "over it."))
    return item


def consequences(items: list[Item]) -> list[str]:
    """What the irregularities found ask of the analysis and the design."""
    irregular = [i for i in items if i.irregular and i.group in (VERTICAL, PLAN)]
    if not irregular:
        return ["No irregularity was found among the types computed: the structure is regular "
                "for those. The types left to the engineer still decide.",
                "Spectrum scaling: a regular structure may be scaled to 90 % of the static "
                "base shear (UBC 1631.5.4); sdt analyze scales to 100 %."]
    names = ", ".join(f"{'vertical' if i.group == VERTICAL else 'plan'} type {i.name[0]}"
                      for i in irregular)
    out = [f"The structure is irregular ({names}).",
           "Analysis: the static force procedure is allowed for an irregular structure of at "
           "most five stories or 20 m; above that the dynamic procedure is required "
           "(UBC 1629.8.3, 1629.8.4).",
           "Spectrum scaling: an irregular structure is scaled to 100 % of the static base "
           "shear (UBC 1631.5.4), which is what sdt analyze does."]
    if any(i.group == VERTICAL and i.name[0] in "123" for i in irregular):
        out.append("Vertical types 1, 2 and 3: the distribution of the static forces over the "
                   "height does not apply; use the dynamic procedure (UBC 1629.8.4 item 2).")
    return out


# =============================================================================
# REPORT
# =============================================================================
@dataclass
class IrregularityReport:
    model_path: str
    items: list[Item] = field(default_factory=list)
    cases: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def report(self):
        from utilities.terminal import Report, math

        report = Report("IRREGULARITY CHECK", [
            f"Model: {self.model_path}",
            "Static seismic cases: " + (", ".join(self.cases) or "none")])
        for group in (VERTICAL, PLAN, STABILITY):
            own = [i for i in self.items if i.group == group]
            if not own:
                continue
            report.heading(group)
            report.table(["Status", "Check", "Result", "Clause"],
                         [[i.status, i.name, i.verdict, i.ref] for i in own])
            for item in own:
                if item.rows:
                    report.table(item.headers, item.rows, title=item.name)
                if item.equations and item.rows:
                    report.equations([math(equation) for equation in item.equations])
                if item.notes:
                    report.lines(item.notes)
        report.heading("What follows")
        report.lines(consequences(self.items) + self.notes)
        return report

    def text(self) -> str:
        return self.report().plain()


def irregularity_summary(report: IrregularityReport, saved: str | None = None):
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt irregularity", report.model_path)
    for item in report.items:
        summary.add(item.name, item.verdict)
    for item in report.items:
        if item.irregular and item.group == STABILITY:
            summary.fail(item.verdict)
    for line in consequences(report.items) + report.notes:
        summary.note(line)
    summary.note("The model was not changed.")
    summary.file("Report", saved)
    return summary


# =============================================================================
# ETABS
# =============================================================================
def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _results(connector, name: str, cases: list[str]):
    tables = connector.sap_model.DatabaseTables
    tables.SetLoadCasesSelectedForDisplay(list(cases))
    tables.SetLoadCombinationsSelectedForDisplay([])
    try:
        return connector._read_database_table(name)
    except Exception:  # noqa: BLE001 - a table the model does not have
        import pandas as pd

        return pd.DataFrame()


def load_cases(model) -> tuple[list[str], list[str], dict[str, int]]:
    """(static seismic cases, gravity cases for Px, the type of each pattern).
    The seismic cases are the strength patterns, or the drift patterns when the
    model has no other."""
    from etabs_api.core.helpers import as_list
    from etabs_api.workflows import model_check as mc

    patterns = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
    cases = {str(n) for n in as_list(model.LoadCases.GetNameList(0, [])[1])}
    types = {n: int(model.LoadPatterns.GetLoadType(n)[0]) for n in patterns}
    seismic = [n for n in patterns if types[n] == mc.SEISMIC and n in cases] or \
        [n for n in patterns if types[n] == mc.SEISMIC_DRIFT and n in cases]
    gravity = [n for n in patterns if n in cases and types[n] in (
        mc.DEAD, mc.SUPER_DEAD, mc.LIVE, mc.REDUCIBLE)]
    return seismic, gravity, types


def story_levels(model) -> list[tuple[str, float, float]]:
    """(story, elevation, height) from the top down, the base left out."""
    from etabs_api.core.helpers import as_list

    got = model.Story.GetStories()
    names = [str(n) for n in as_list(got[1])]
    elevations = [float(z) for z in as_list(got[2])]
    heights = [float(h) for h in as_list(got[3])]
    return list(zip(names[1:], elevations[1:], heights[1:]))[::-1]


def _stability_rows(connector, seismic: list[str], gravity: list[str],
                    levels: list[tuple[str, float, float]]) -> list[tuple]:
    """(story, case, direction, P, V, drift, height) from the results now in ETABS."""
    stiffness = _results(connector, "Story Stiffness", seismic)
    forces = _results(connector, "Story Forces", gravity)
    axial: dict[str, float] = {}
    if len(forces):
        bottom = forces[forces["Location"].astype(str) == "Bottom"]
        for story, value in zip(bottom["Story"].astype(str), bottom["P"]):
            axial[story] = axial.get(story, 0.0) + abs(_number(value))
    height = {story: h for story, _, h in levels}
    rows = []
    if len(stiffness):
        for _, row in stiffness.iterrows():
            story, case = str(row["Story"]), str(row["OutputCase"])
            for direction in ("X", "Y"):
                shear = _number(row.get(f"Shear{direction}"))
                if abs(shear) > NO_VALUE:
                    rows.append((story, case, direction, axial.get(story, 0.0), shear,
                                 _number(row.get(f"Drift{direction}")), height.get(story, 0.0)))
    rows = own_direction(rows, weight=4)        # by the story shear
    order = {story: index for index, (story, _, _) in enumerate(levels)}
    rows.sort(key=lambda r: order.get(r[0], 0))
    return rows


def first_order_stability(connector, model_path: str, seismic: list[str], gravity: list[str],
                          levels, progress=None) -> list[tuple]:
    """The stability rows from an analysis with P-delta off, on a copy of the
    model in a folder of its own. The model is opened again after, and the copy
    removed.

    The model is not saved first: ``File.Save`` through the API unlocks a model
    and discards its results (ETABS 22). A model with results is locked, so its
    file is what ETABS shows, and opening it again brings its results back.
    """
    import shutil

    from etabs_api.core.connection import use_working_units
    from etabs_api.core.helpers import return_code
    from etabs_api.workflows.foundation_model import _pdelta_off

    say = progress or (lambda text: None)
    model = connector.sap_model
    folder = os.path.join(os.path.dirname(model_path), "sdt irregularity scratch")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    scratch = os.path.join(folder, os.path.basename(model_path))
    shutil.copy2(model_path, scratch)
    try:
        say("Opening a copy of the model for the first-order analysis")
        if return_code(model.File.OpenFile(scratch)) != 0:
            raise RuntimeError("the copy of the model could not be opened")
        use_working_units(model)
        model.SetModelIsLocked(False)
        problems = _Problems()
        _pdelta_off(model, problems)
        if problems.problems:
            raise RuntimeError(problems.problems[0])
        model.Analyze.SetRunCaseFlag("", False, True)
        for case in seismic + gravity:
            model.Analyze.SetRunCaseFlag(case, True, False)
        say("Analysing the copy with P-delta off")
        if return_code(model.Analyze.RunAnalysis()) != 0:
            raise RuntimeError("the copy of the model could not be analysed")
        return _stability_rows(connector, seismic, gravity, levels)
    finally:
        say("Opening the model again")
        model.File.OpenFile(model_path)
        use_working_units(model)
        shutil.rmtree(folder, ignore_errors=True)


class _Problems:
    """Takes the problems ``foundation_model._pdelta_off`` reports."""

    def __init__(self):
        self.problems: list[str] = []


def read_irregularity(connector, model_path: str, first_order: bool = True,
                      progress=None, design_is_old: bool | None = None) -> IrregularityReport:
    """Run every check on the model open in ETABS (in N-mm, analysed).

    ``design_is_old``: whether the stored column design is older than the model
    file, when the caller looked before saving the model; None to look now.
    """
    from design.concrete.workflow import DesignStore, pdelta_method
    from etabs_api.workflows.foundation_plan import read_shells
    from etabs_api.workflows.grid_column_model import _table

    say = progress or (lambda text: None)
    model = connector.sap_model
    seismic, gravity, _ = load_cases(model)
    levels = story_levels(model)
    order = [story for story, _, _ in levels]
    report = IrregularityReport(model_path, cases=seismic)

    say("Reading the drifts, stiffness and mass of the stories")
    drifts = _results(connector, "Story Max Over Avg Drifts", seismic)
    moved = _results(connector, "Story Max Over Avg Displacements", seismic)
    stiff = _results(connector, "Story Stiffness", seismic)
    mass = _results(connector, "Mass Summary by Story", [])

    def pairs(table, most, average):
        return own_direction([
            (str(r["Story"]), str(r["OutputCase"]), str(r["Direction"]), _number(r[most]),
             _number(r[average])) for _, r in table.iterrows()]) if len(table) else []

    stiffness = []
    if len(stiff):
        for _, row in stiff.iterrows():
            for direction in ("X", "Y"):
                if abs(_number(row.get(f"Shear{direction}"))) > NO_VALUE:
                    stiffness.append((str(row["Story"]), str(row["OutputCase"]), direction,
                                      _number(row.get(f"Stiff{direction}")),
                                      _number(row.get(f"Shear{direction}"))))
    stiffness = [row[:4] for row in own_direction(stiffness)]      # by the story shear
    mass_of = {str(s): _number(m) for s, m in zip(mass["Story"], mass["UX"])} if len(mass) else {}

    say("Reading the columns, walls and floors")
    points = {str(r["UniqueName"]): (_number(r["X"]), _number(r["Y"]), _number(r["Z"]))
              for r in _table(model, "Point Object Connectivity")}
    spans: dict[str, list[tuple[float, float]]] = {}
    tops, bottoms = set(), {}
    for row in _table(model, "Column Object Connectivity"):
        a, b = str(row["UniquePtI"]), str(row["UniquePtJ"])
        if a not in points or b not in points:
            continue
        low, high = (a, b) if points[a][2] <= points[b][2] else (b, a)
        tops.add(high)
        bottoms[str(row["UniqueName"])] = low
        spans.setdefault(str(row["Story"]), []).append(points[high][:2])
    for row in _table(model, "Wall Object Connectivity"):
        for i in range(1, 5):
            joint = str(row.get(f"UniquePt{i}"))
            if joint in points:
                spans.setdefault(str(row["Story"]), []).append(points[joint][:2])
    extents = []
    for story in order:
        found = spans.get(story, [])
        extents.append((story, max(p[0] for p in found) - min(p[0] for p in found),
                        max(p[1] for p in found) - min(p[1] for p in found))
                       if found else (story, 0.0, 0.0))
    lowest = min((z for _, z, _ in levels), default=0.0) - max(
        (h for _, _, h in levels[-1:]), default=0.0)
    planted = sorted(name for name, low in bottoms.items()
                     if low not in tops and points[low][2] > lowest + 1.0
                     and not _restrained(model, low))
    shells_at: dict[str, list] = {}
    for shell in read_shells(model, points):
        story = min(levels, key=lambda level: abs(level[1] - shell.z))[0] if levels else ""
        shells_at.setdefault(story, []).append(shell.points)
    shapes = [(story, plan_shape(shells_at[story]) if story in shells_at else None)
              for story in order]

    store = DesignStore.load(model_path)
    column_report = getattr(store, "column_report", None)
    strengths = story_strengths(column_report, order) if column_report is not None and \
        len(column_report) else None

    corners, opening = plan_items([s for s in shapes if s[1] is not None])
    offset, plane, parallel = judged_items(planted, len(order))
    say("Reading the diaphragms")
    no_diaphragm = without_diaphragm(order, diaphragm_stories(
        _table(model, "Area Assignments - Diaphragms")
        + _table(model, "Joint Assignments - Diaphragms")))
    stories, below, minor = compared_stories(
        [(story, mass_of.get(story, 0.0)) for story in order if mass_of.get(story, 0.0) > NO_VALUE],
        _seismic_base(model))
    # a level at or below the base is left out for that already
    no_diaphragm = [story for story in no_diaphragm if story not in below]
    stories = [story for story in stories if story not in no_diaphragm]
    minor = [story for story in minor if story not in no_diaphragm]
    note = levels_note(below, minor, no_diaphragm)
    if note:
        report.notes.append(note)
    if strengths is not None:
        strengths = [row for row in strengths if row[0] in stories]
    report.items = [
        soft_story_item([row for row in stiffness if row[0] in stories], stories),
        mass_item([(story, mass_of.get(story, 0.0)) for story in stories]),
        geometry_item([row for row in extents if row[0] in stories]), offset,
        weak_story_item(strengths),
        torsion_item(pairs(drifts, "Max Drift", "Avg Drift"),
                     pairs(moved, "Maximum", "Average"),
                     [story for story in order if story not in no_diaphragm]),
        corners, opening, plane, parallel]
    if store is not None and column_report is not None and (
            store.is_stale() if design_is_old is None else design_is_old):
        report.notes.append("The column design used for the weak story is older than the model "
                            "file: run sdt columns again if the columns changed.")

    pdelta_on = pdelta_method(connector) not in ("", "None")
    rows = None
    if first_order and pdelta_on and seismic:
        try:
            rows = first_order_stability(connector, model_path, seismic, gravity, levels, say)
        except Exception as error:  # noqa: BLE001 - the model's own results are used instead
            report.notes.append(f"The first-order analysis did not run ({error}): the stability "
                                "coefficient is on the drifts of the model as analysed, which "
                                "include P-delta.")
    exact = rows is not None or not pdelta_on
    if rows is None:
        rows = _stability_rows(connector, seismic, gravity, levels)
    report.items.append(stability_item(rows, pdelta_on, _r_factor(model), exact))
    return report


def _restrained(model, joint: str) -> bool:
    try:
        return any(bool(v) for v in model.PointObj.GetRestraint(joint, [])[0])
    except Exception:  # noqa: BLE001
        return False


def _seismic_base(model) -> str | None:
    """The story the static seismic patterns start from, or None when the model
    does not say (patterns that are not UBC 97, or none)."""
    from etabs_api.workflows.grid_column_model import _table

    for row in _table(model, "Load Pattern Definitions - Auto Seismic - UBC 97"):
        for key in ("BotStory", "BottomStory", "Bottom Story"):
            if str(row.get(key) or "").strip() not in ("", "None"):
                return str(row[key]).strip()
    return None


def _r_factor(model) -> float | None:
    from etabs_api.workflows.grid_column_model import _table

    for row in _table(model, "Load Pattern Definitions - Auto Seismic - UBC 97"):
        value = _number(row.get("R"))
        if value > 0:
            return value
    return None


# =============================================================================
# TERMINAL WORKFLOW
# =============================================================================
EXACT = "Analyse a copy of the model with P-delta off (first-order drifts)"
AS_RUN = "Use the drifts of the model as analysed (they include P-delta)"


def run_irregularity() -> IrregularityReport | None:
    """Entry point: check the model open in ETABS, print the report and save it
    beside the model (``<model> - Irregularity.txt``)."""
    from design.concrete.workflow import attach_etabs, pdelta_method
    from etabs_api.core.connection import model_file
    from etabs_api.workflows import model_check as mc
    from utilities import terminal
    from utilities._gui_helpers import LoadingWindow, select_option, show_warning

    title = "Irregularity Check"
    connector = attach_etabs()
    if connector is None:
        return None
    model = connector.sap_model
    path = model_file(model)
    if not path or not os.path.isfile(path):
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    if not mc._has_results(connector):
        show_warning("The model has no analysis results. Run the analysis (or sdt analyze) "
                     "first: the irregularities are read from the story results.", title=title)
        return None
    from design.concrete.workflow import DesignStore

    store = DesignStore.load(path)
    design_is_old = store.is_stale() if store is not None else False
    first_order = False
    if pdelta_method(connector) not in ("", "None"):
        choice = select_option(
            title, "P-delta stability coefficient\n\nP-delta is on in this model, so its drifts "
            "already include it. For first-order drifts a copy of the model is analysed with "
            "P-delta off, in a folder beside it that is removed after. ETABS closes this model "
            "for that and opens it again, with its results. The model is not saved or changed.",
            [EXACT, AS_RUN])
        if choice is None:
            return None
        first_order = choice == EXACT
    with LoadingWindow("Irregularity check") as window:
        report = read_irregularity(connector, path, first_order, window.update, design_is_old)
    if terminal.drawn():
        report.report().show()
    else:
        print(report.text())
    from utilities.output_folders import beside

    saved = beside(path, " - Irregularity.txt")
    with open(saved, "w", encoding="utf-8") as handle:
        handle.write(report.text().lstrip("\n") + "\n")
    print(f"Saved: {saved}")
    from etabs_api.workflows.seismic_factors import save_regularity

    # sdt analyze reads it: 90 % spectrum scaling only for a regular structure
    save_regularity(path, [i.name for i in report.items
                           if i.irregular and i.group in (VERTICAL, PLAN)],
                    [i.name for i in report.items if i.verdict.startswith(JUDGE)])
    irregularity_summary(report, saved).show(popup=True, echo=False)
    return report


if __name__ == "__main__":
    run_irregularity()
