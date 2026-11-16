"""Slab marks and the tables of the results workbook.

Marks, floor by floor: panels of the same kind, thickness, result and bars share a mark.
A failing panel only shares a mark with failing panels. Marks whose spacings are all
within a tolerance (25 mm by default) are merged; then, when a floor has more marks than
the limit given, the two marks of the same kind, thickness and result whose bars are
closest are merged, until the floor is within the limit (or no such pair is left). A
merged mark keeps the closer spacing at each location. Marks are named ``S-<n>``,
numbered on each floor (the schedule groups them by floor): two-way first, then one-way,
then cantilevers, thinner first, failing marks after passing ones.

The schedule gives, for the short and the long direction, the top bars at the
discontinuous and the continuous edges and the bottom bars at midspan, as
``12mm @ 250``, and ``-`` where the panel has no such edge.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from design.concrete.slabs.design import (
    CANTILEVER,
    LOCATIONS,
    NOT_DESIGNED,
    ONE_WAY,
    TWO_WAY,
    PanelDesign,
)

KIND_ORDER = {TWO_WAY: 0, ONE_WAY: 1, CANTILEVER: 2, NOT_DESIGNED: 3}
SCHEDULE_COLUMNS = (
    ("short", "top", "discontinuous"), ("short", "top", "continuous"), ("short", "bottom", "midspan"),
    ("long", "top", "discontinuous"), ("long", "top", "continuous"), ("long", "bottom", "midspan"),
)


@dataclass
class Mark:
    name: str
    story: str
    kind: str
    h: float
    bar: float
    spacings: dict                       # location -> spacing (mm), absent: no bars there
    designs: list[PanelDesign] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(d.passed for d in self.designs)


def _signature(d: PanelDesign) -> tuple:
    return tuple(d.bars[k].spacing if k in d.bars else None for k in LOCATIONS)


def _distance(a: dict, b: dict) -> float:
    total = 0.0
    for k in LOCATIONS:
        sa, sb = a.get(k), b.get(k)
        if (sa is None) != (sb is None):
            total += 1000.0
        elif sa is not None:
            total += abs(sa - sb)
    return total


def _merge(a: dict, b: dict) -> dict:
    out = {}
    for k in LOCATIONS:
        values = [s for s in (a.get(k), b.get(k)) if s is not None]
        if values:
            out[k] = min(values)
    return out


def _mergeable(a: dict, b: dict) -> bool:
    """Marks that may be merged: the same kind, thickness and result (a failing panel
    only shares a mark with failing panels)."""
    return a["kind"] == b["kind"] and a["h"] == b["h"] and a["passed"] == b["passed"]


def _within(a: dict, b: dict, tolerance: float) -> bool:
    """The same locations, every spacing within ``tolerance`` (mm) of the other's."""
    if set(a) != set(b):
        return False
    return all(abs(a[k] - b[k]) <= tolerance + 1e-9 for k in a)


def _merge_closest(pending: list[dict], allowed) -> bool:
    """Merge the closest pair that ``allowed(a, b)`` accepts; False when there is none."""
    best = None
    for i, a in enumerate(pending):
        for j in range(i + 1, len(pending)):
            b = pending[j]
            if not _mergeable(a, b) or not allowed(a, b):
                continue
            gap = _distance(a["spacings"], b["spacings"])
            if best is None or gap < best[0]:
                best = (gap, i, j)
    if best is None:
        return False
    _, i, j = best
    a, b = pending[i], pending[j]
    a["spacings"] = _merge(a["spacings"], b["spacings"])
    a["designs"].extend(b["designs"])
    del pending[j]
    return True


def assign_marks(designs: list[PanelDesign], bar: float, max_marks: int = 15,
                 tolerance: float = 25.0) -> list[Mark]:
    """Give every designed panel a mark (``PanelDesign.mark``) and return the marks.

    On each floor: panels of the same kind, thickness, result (pass or fail) and bars
    share a mark; marks whose spacings are all within ``tolerance`` mm of each other are
    merged, closest first; then, while the floor has more than ``max_marks``, the
    closest two of the same kind, thickness and result. A merged mark keeps the closer
    spacing at each location."""
    by_story: dict[str, list[PanelDesign]] = defaultdict(list)
    for d in designs:
        by_story[d.panel.story].append(d)
    marks: list[Mark] = []
    for story, floor in by_story.items():
        groups: dict[tuple, list[PanelDesign]] = defaultdict(list)
        for d in floor:
            if d.kind == NOT_DESIGNED:
                continue
            groups[(d.kind, round(d.panel.h), d.passed, _signature(d))].append(d)
        pending = [dict(kind=k, h=h, passed=ok,
                        spacings={loc: s for loc, s in zip(LOCATIONS, sig) if s is not None},
                        designs=list(ds))
                   for (k, h, ok, sig), ds in groups.items()]
        while tolerance > 0 and _merge_closest(
                pending, lambda a, b: _within(a["spacings"], b["spacings"], tolerance)):
            pass
        while len(pending) > max_marks and _merge_closest(pending, lambda a, b: True):
            pass

        def order(m: dict):
            first = min(m["designs"], key=lambda d: (round(d.panel.centre[1]),
                                                     round(d.panel.centre[0]))).panel
            return (KIND_ORDER[m["kind"]], m["h"], not m["passed"], round(first.centre[1]),
                    round(first.centre[0]))

        for n, m in enumerate(sorted(pending, key=order), start=1):
            mark = Mark(f"S-{n}", story, m["kind"], m["h"], bar, m["spacings"],
                        m["designs"])
            for d in mark.designs:
                d.mark = mark.name
            marks.append(mark)
    return marks


WHERE = {"discontinuous": "disco", "continuous": "cont", "midspan": "midspan"}


def column_label(key: tuple) -> str:
    """"Short top disco", "Long bottom midspan", ... as the office schedule names them."""
    direction, face, where = key
    return f"{direction.capitalize()} {face} {WHERE[where]}"


def bar_text(bar: float, spacing: float | None) -> str:
    return "-" if spacing is None else f"{bar:g}mm @ {spacing:g}"


def schedule_table(marks: list[Mark]) -> pd.DataFrame:
    """The slab schedule: one row per mark."""
    rows = []
    for m in marks:
        row = {"Mark": m.name, "Story": m.story, "Thickness (mm)": m.h}
        for key in SCHEDULE_COLUMNS:
            row[column_label(key)] = bar_text(m.bar, m.spacings.get(key))
        remarks = [m.kind]
        if m.kind != TWO_WAY:
            remarks.append("LONG DIRECTION: TEMPERATURE BARS")
        if not m.passed:
            remarks.append("FAILED: SEE FLAGS")
        row["Remarks"] = ", ".join(remarks)
        row["Panels"] = len(m.designs)
        rows.append(row)
    return pd.DataFrame(rows)


def panel_table(designs: list[PanelDesign]) -> pd.DataFrame:
    """Every panel with its design: one row per panel."""
    rows = []
    for d in designs:
        p = d.panel
        row = {
            "Mark": d.mark or "-", "Story": p.story, "Area objects": " ".join(p.areas),
            "Centre x (m)": p.centre[0] / 1000, "Centre y (m)": p.centre[1] / 1000,
            "Axes (deg)": round(math.degrees(p.angle), 1),
            "Span x (m)": p.lx / 1000, "Span y (m)": p.ly / 1000,
            "Kind": d.kind, "Method": d.method, "Short direction": d.short_dir,
            "Short clear span (m)": d.short_clear / 1000, "Long clear span (m)": d.long_clear / 1000,
            "h (mm)": p.h, "h min (mm)": round(d.h_min, 1), "f'c (MPa)": p.fc,
            "alpha_fm": round(d.alpha_fm, 2) if d.kind == TWO_WAY else "N/A",
            "Self weight (kPa)": d.self_weight, "SDL (kPa)": p.dead, "Live (kPa)": p.live,
            "wu (kPa)": d.wu, "Load source": p.load_note,
        }
        for k in LOCATIONS:
            name = " ".join(k)
            b = d.bars.get(k)
            row[f"Mu {name} (kN-m/m)"] = d.moments.get(k, "N/A") if b else "N/A"
            row[f"As req {name} (mm2/m)"] = round(b.as_design, 1) if b else "N/A"
            row[f"Spacing {name} (mm)"] = b.spacing if b else "N/A"
        row["Vu (kN/m)"] = d.vu
        row["phi Vc (kN/m)"] = d.phi_vc
        row["Result"] = "PASS" if d.passed else "FAILED"
        row["Flags"] = "; ".join(d.flags)
        rows.append(row)
    return pd.DataFrame(rows)


def flags_table(designs: list[PanelDesign]) -> pd.DataFrame:
    rows = [{"Mark": d.mark or "-", "Story": d.panel.story, "Area objects": " ".join(d.panel.areas),
             "Flag": flag} for d in designs for flag in d.flags]
    return pd.DataFrame(rows, columns=["Mark", "Story", "Area objects", "Flag"])
