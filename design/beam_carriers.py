"""Which beam carries which, and the rule that a carrier is at least as deep.

A beam that frames into another beam, and not into a column, is carried by
it. Where a girder is shallower than the beam it carries, the bottom bars of
the carried beam pass under the girder's bottom bars and cannot be supported
by it. The rule is a detailing choice of the engineer, not a code clause, so
it is applied only when asked (``sdt beams`` and ``sdt design`` ask).

The carrier of a beam end is found from the model geometry:

* a beam whose centre line passes through that end joint, whether ETABS has
  it as one member from column to column or as two pieces in line that meet
  at the joint;
* never a beam that only continues the carried beam in line.
"""

from __future__ import annotations

import math

import pandas as pd

ON_LINE = 10.0                                   # mm
IN_LINE = math.cos(math.radians(15.0))           # beams within 15 degrees are in line
SAME_LEVEL = 50.0                                # mm
CARRIER_FAILED = "FAILED: DEPTH BELOW THE BEAM IT CARRIES"
NOT_A_CARRIER = "N/A - carries no beam"


def _name(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def carried_beams(connectivity: pd.DataFrame, points: pd.DataFrame | None
                  ) -> dict[str, list[str]]:
    """{carrier beam: the beams it carries}, from the frame connectivity and the
    joint coordinates. Empty without coordinates: the direction of a beam is
    needed to tell a carrier from a continuation."""
    if connectivity is None or connectivity.empty or points is None or points.empty:
        return {}
    xyz = {_name(n): (float(x), float(y), float(z)) for n, x, y, z in zip(
        points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
        pd.to_numeric(points["Y"], errors="coerce"), pd.to_numeric(points["Z"], errors="coerce"))}
    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    column_joints = set()
    for end in ("UniquePtI", "UniquePtJ"):
        column_joints |= {_name(v) for v in connectivity.loc[kind.eq("column"), end]}
    beams = {}
    for row in connectivity.loc[kind.eq("beam")].drop_duplicates("UniqueName").to_dict("records"):
        i, j = _name(row["UniquePtI"]), _name(row["UniquePtJ"])
        if i in xyz and j in xyz:
            beams[_name(row["UniqueName"])] = (i, j)

    def unit(name: str) -> tuple[float, float] | None:
        (x1, y1, _), (x2, y2, _) = (xyz[p] for p in beams[name])
        length = math.hypot(x2 - x1, y2 - y1)
        return ((x2 - x1) / length, (y2 - y1) / length) if length > ON_LINE else None

    def meets(other: str, joint: str) -> str:
        """"through", "end" or "" for the centre line of ``other`` at ``joint``."""
        (x1, y1, z1), (x2, y2, z2) = (xyz[p] for p in beams[other])
        x, y, z = xyz[joint]
        if abs(z - (z1 + z2) / 2.0) > SAME_LEVEL + abs(z2 - z1) / 2.0:
            return ""
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= ON_LINE:
            return ""
        along = ((x - x1) * dx + (y - y1) * dy) / length
        off = abs((x - x1) * dy - (y - y1) * dx) / length
        if off > ON_LINE or along < -ON_LINE or along > length + ON_LINE:
            return ""
        return "through" if ON_LINE < along < length - ON_LINE else "end"

    directions = {name: unit(name) for name in beams}
    out: dict[str, list[str]] = {}
    for name, ends in beams.items():
        own = directions[name]
        if own is None:
            continue
        for joint in ends:
            if joint in column_joints:
                continue
            meeting = [(other, meets(other, joint)) for other in beams if other != name]
            meeting = [(other, how) for other, how in meeting
                       if how and directions[other] is not None]
            for other, how in meeting:
                d = directions[other]
                if abs(own[0] * d[0] + own[1] * d[1]) >= IN_LINE:
                    continue               # it continues this beam
                if how == "end":           # a girder in two pieces: its partner is in line
                    partner = any(abs(d[0] * directions[third][0] + d[1] * directions[third][1])
                                  >= IN_LINE for third, _ in meeting if third != other)
                    if not partner:
                        continue
                carried = out.setdefault(other, [])
                if name not in carried:
                    carried.append(name)
    return out


def add_carrier_depth_check(results: pd.DataFrame, connectivity: pd.DataFrame,
                            points: pd.DataFrame | None) -> pd.DataFrame:
    """Add ``Carried_Beam_Depth`` and ``Carrier_Depth_Check`` to the beam results.

    A beam shallower than a beam it carries fails; a passing beam then gets
    the status ``CARRIER_FAILED`` (the design loop makes it deeper).
    """
    out = results.copy()
    names = out["UniqueName"].astype(str)
    depth_of = dict(zip(names, pd.to_numeric(out["Depth"], errors="coerce")))
    carried = carried_beams(connectivity, points)
    out["Carried_Beam_Depth"] = float("nan")
    out["Carrier_Depth_Check"] = NOT_A_CARRIER
    for carrier, beams in carried.items():
        rows = names.eq(carrier)
        depths = {beam: depth_of[beam] for beam in beams
                  if beam in depth_of and depth_of[beam] == depth_of[beam]}
        if not rows.any() or not depths:
            continue
        deepest = max(depths, key=depths.get)
        out.loc[rows, "Carried_Beam_Depth"] = float(depths[deepest])
        own = depth_of.get(carrier)
        if own is not None and own + 1e-6 >= depths[deepest]:
            out.loc[rows, "Carrier_Depth_Check"] = "PASS"
            continue
        out.loc[rows, "Carrier_Depth_Check"] = (
            f"FAIL: {own:.0f} deep, carries {deepest} ({depths[deepest]:.0f} deep)")
        passing = rows & out["Design_Status"].astype(str).eq("OK")
        out.loc[passing, "Design_Status"] = CARRIER_FAILED
    order = [c for c in out.columns if c != "Design_Status"] + ["Design_Status"]
    return out[order]
