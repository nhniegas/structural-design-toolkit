"""Girder bars through the columns: support bars matched to the next span.

An option of ``sdt beams`` and ``sdt design`` (off by default). At an interior
column, the support bars of two girders that meet in line are made the same,
layer by layer, so they run through the column instead of anchoring in it:

- Girders only (members on columns); a gravity beam keeps its own bars.
- In line: the two girders meet at the column with their axes within 1 in 6
  of a straight line, in plan. A column with more girders pairs each girder
  with the one most in line with it.
- Layer by layer: each layer of each face takes the larger count of the two
  spans (A 4 + 2 and B 5 + 0 give both 5 + 2), so every bar has its partner in
  the same place.
- A layer holds what fits in the width of its girder: the narrower girder
  takes the most bars that fit, and the extra bars of the wider one anchor in
  the column as before.
- Bottom bars only when the two soffits are within 1 in 6 of the column: the
  bars bend by the depth difference over the smaller side of the column (a
  600 mm column takes up to 100 mm). Otherwise the bottom bars anchor as before
  and only the top bars are matched (the tops are flush).

The matching changes the bars, so the girders are designed again with these
counts as a minimum (``BeamFlexureDesign.set_layer_floor``): more bars raise
the probable moments and the capacity shear, and the SMRF strength ratios may
add bottom bars. The pairs are matched again until no count changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from design.concrete.beams.supports import is_gravity_beam

IN_LINE_SLOPE = 1.0 / 6.0      # in plan, between the two girder axes
BEND_SLOPE = 1.0 / 6.0         # bottom bars bent over the column for a depth step
MAX_ROUNDS = 10                # matching and designing again, at most


def _name(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


@dataclass(frozen=True)
class GirderPair:
    """Two girders in line at one column: the end of each at that column."""

    joint: str
    first: str
    first_end: str      # "left" (the I end) or "right" (the J end)
    second: str
    second_end: str
    column_side: float  # the smaller side of the column, mm (0 when unknown)

    def ends(self):
        return ((self.first, self.first_end), (self.second, self.second_end))

    def other(self, member: str) -> str:
        return self.second if member == self.first else self.first


def _column_sides(frame_data: pd.DataFrame | None, connectivity: pd.DataFrame) -> dict:
    """The smaller side (or the diameter) of the columns at each joint, mm."""
    if frame_data is None or frame_data.empty:
        return {}
    sides = {}
    for row in frame_data.to_dict("records"):
        values = [pd.to_numeric(row.get(key), errors="coerce")
                  for key in ("Width", "Depth", "Diameter")]
        values = [float(v) for v in values if pd.notna(v) and float(v) > 0]
        if values:
            sides[_name(row.get("UniqueName"))] = min(values)
    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    at: dict[str, float] = {}
    for row in connectivity.loc[kind.eq("column")].to_dict("records"):
        side = sides.get(_name(row["UniqueName"]))
        if side is None:
            continue
        for end in ("UniquePtI", "UniquePtJ"):
            joint = _name(row[end])
            at[joint] = min(at.get(joint, side), side)
    return at


def girder_pairs(results: pd.DataFrame, connectivity: pd.DataFrame | None,
                 points: pd.DataFrame | None,
                 frame_data: pd.DataFrame | None = None) -> list[GirderPair]:
    """The girders that meet in line at a column, in pairs.

    Empty without the joint coordinates. A girder end is in at most one pair.
    """
    if (results is None or results.empty or connectivity is None or connectivity.empty
            or points is None or points.empty):
        return []
    needed = {"UniqueName", "UniquePtI", "UniquePtJ", "DesignType"}
    if not needed <= set(connectivity.columns) or not {"X", "Y"} <= set(points.columns):
        return []
    xy = {_name(n): (float(x), float(y)) for n, x, y in zip(
        points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
        pd.to_numeric(points["Y"], errors="coerce")) if x == x and y == y}
    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    column_joints: set[str] = set()
    for end in ("UniquePtI", "UniquePtJ"):
        column_joints |= {_name(v) for v in connectivity.loc[kind.eq("column"), end]}
    first_rows = results.drop_duplicates("UniqueName")
    statuses = (first_rows["SupportStatus"] if "SupportStatus" in first_rows.columns
                else [""] * len(first_rows))
    girders = {_name(n) for n, status in zip(first_rows["UniqueName"], statuses)
               if not is_gravity_beam(status)}
    ends_at: dict[str, list[tuple[str, str, tuple[float, float]]]] = {}
    for row in connectivity.loc[kind.eq("beam")].drop_duplicates("UniqueName").to_dict("records"):
        name = _name(row["UniqueName"])
        i, j = _name(row["UniquePtI"]), _name(row["UniquePtJ"])
        if name not in girders or i not in xy or j not in xy:
            continue
        dx, dy = xy[j][0] - xy[i][0], xy[j][1] - xy[i][1]
        length = math.hypot(dx, dy)
        if length <= 0:        # vertical or zero length in plan
            continue
        along = (dx / length, dy / length)
        for joint, end, away in ((i, "left", along), (j, "right", (-along[0], -along[1]))):
            if joint in column_joints:
                ends_at.setdefault(joint, []).append((name, end, away))
    sides = _column_sides(frame_data, connectivity)
    pairs = []
    for joint in sorted(ends_at):
        candidates = []
        members = ends_at[joint]
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                (name_a, _, u), (name_b, _, v) = members[a], members[b]
                if name_a == name_b:
                    continue
                # in line: the two point away from the joint in opposite directions
                dot = -(u[0] * v[0] + u[1] * v[1])
                cross = abs(u[0] * v[1] - u[1] * v[0])
                if dot > 0 and cross <= IN_LINE_SLOPE * dot:
                    candidates.append((cross / dot, a, b))
        used: set[int] = set()
        for _, a, b in sorted(candidates):
            if a in used or b in used:
                continue
            used |= {a, b}
            pairs.append(GirderPair(joint, members[a][0], members[a][1],
                                    members[b][0], members[b][1], sides.get(joint, 0.0)))
    return pairs


def outer_first(row, end: str, top: bool) -> list[int]:
    """The bars of each layer at one end of one face, outer layer first.

    The results give the layers from the top down (L1 is the highest), so for
    the bottom face the outer layer is L3.
    """
    counts = [int(pd.to_numeric(row.get(f"n_{end}_L{k}"), errors="coerce") or 0)
              for k in (1, 2, 3)]
    layers = counts if top else counts[::-1]
    while layers and layers[-1] == 0:
        layers.pop()
    return layers


def _layerwise_max(a: list[int], b: list[int]) -> list[int]:
    size = max(len(a), len(b))
    a, b = a + [0] * (size - len(a)), b + [0] * (size - len(b))
    return [max(x, y) for x, y in zip(a, b)]


def bottom_bars_bend(depth_a: float, depth_b: float, column_side: float) -> bool:
    """Whether bottom bars can bend from one soffit to the other within the column."""
    step = abs(float(depth_a) - float(depth_b))
    if step <= 0.5:
        return True
    return column_side > 0 and step <= BEND_SLOPE * column_side


def match_girder_bars(results: pd.DataFrame, pairs: list[GirderPair],
                      floors: dict | None = None) -> dict:
    """The fewest bars of each layer at the paired girder ends.

    Returns beam name -> {(end, face): [bars per layer, outer first]}, face
    ``top`` or ``bot``; it keeps the larger of ``floors`` (the last round) and
    the larger count of the two spans, layer by layer.
    """
    out: dict = {name: dict(value) for name, value in (floors or {}).items()}
    rows = {(_name(r["UniqueName"]), str(r["Face"]).upper()): r
            for r in results.to_dict("records")}
    for pair in pairs:
        a, b = pair.first, pair.second
        if (a, "TOP") not in rows or (b, "TOP") not in rows:
            continue
        faces = [("top", "TOP", True)]
        depth_a = pd.to_numeric(rows[(a, "TOP")].get("Depth"), errors="coerce")
        depth_b = pd.to_numeric(rows[(b, "TOP")].get("Depth"), errors="coerce")
        if (pd.notna(depth_a) and pd.notna(depth_b)
                and bottom_bars_bend(depth_a, depth_b, pair.column_side)):
            faces.append(("bot", "BOTTOM", False))
        for face, label, top in faces:
            if (a, label) not in rows or (b, label) not in rows:
                continue
            layers = _layerwise_max(outer_first(rows[(a, label)], pair.first_end, top),
                                    outer_first(rows[(b, label)], pair.second_end, top))
            for name, end in pair.ends():
                key = (end, face)
                current = out.setdefault(name, {}).get(key, [])
                out[name][key] = _layerwise_max(current, layers)
    return out


def describe_continuity(results: pd.DataFrame, pairs: list[GirderPair]) -> pd.Series:
    """For each girder row, what its support bars do at each paired column.

    For example ``right (to 2GX-2): top 5+2 and bottom 3 through`` or, when a
    layer of this girder holds more than the other: ``2 top bars anchor``.
    """
    rows = {(_name(r["UniqueName"]), str(r["Face"]).upper()): r
            for r in results.to_dict("records")}
    notes: dict[str, list[str]] = {}
    for pair in pairs:
        if (pair.first, "TOP") not in rows or (pair.second, "TOP") not in rows:
            continue
        depth_a = pd.to_numeric(rows[(pair.first, "TOP")].get("Depth"), errors="coerce")
        depth_b = pd.to_numeric(rows[(pair.second, "TOP")].get("Depth"), errors="coerce")
        bottom = (pd.notna(depth_a) and pd.notna(depth_b)
                  and bottom_bars_bend(depth_a, depth_b, pair.column_side))
        for name, end in pair.ends():
            other = pair.other(name)
            other_end = pair.second_end if name == pair.first else pair.first_end
            parts = []
            for face, label, top in (("top", "TOP", True), ("bottom", "BOTTOM", False)):
                if (name, label) not in rows or (other, label) not in rows:
                    continue
                own = outer_first(rows[(name, label)], end, top)
                theirs = outer_first(rows[(other, label)], other_end, top)
                if face == "bottom" and not bottom:
                    parts.append("bottom anchored (depth step over 1:6 of the column)")
                    continue
                through = [min(x, y) for x, y in zip(own, theirs)]
                extra = sum(own) - sum(through)
                text = f"{face} {'+'.join(str(n) for n in through) or '0'} through"
                if extra > 0:
                    text += f", {extra} anchored"
                parts.append(text)
            notes.setdefault(name, []).append(f"{end} (to {other}): " + "; ".join(parts))
    return results["UniqueName"].map(lambda n: " | ".join(notes.get(_name(n), [])) or "-")
