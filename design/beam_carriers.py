"""Which beam carries which, and the rule that a carrier is at least as deep.

A beam that frames into another beam, and not into a column, is carried by
it. Where a girder is shallower than the beam it carries, the bottom bars of
the carried beam pass under the girder's bottom bars and cannot be supported
by it. The rule is a detailing choice of the engineer, not a code clause, so
it is applied only when asked (``sdt beams`` and ``sdt design`` ask).

The carrier of a beam end is found from the model geometry and the load path:

* a beam whose centre line passes through that end joint, whether ETABS has
  it as one member from column to column or as two pieces in line that meet
  at the joint;
* never a beam that only continues the carried beam in line;
* a beam that only ends at the joint when it is nearer to the supports, as
  the tip of a cantilever girder under an edge beam;
* never a beam further from the supports than the one that ends on it
  (``BeamNetwork``): an edge beam across the tips of cantilever girders
  rests on the girders, it does not carry them.
"""

from __future__ import annotations

import math

import pandas as pd

ON_LINE = 10.0                                   # mm
IN_LINE = math.cos(math.radians(15.0))           # beams within 15 degrees are in line
SAME_LEVEL = 50.0                                # mm
MOVE_SHARE = 0.5       # an end drops when it moves down by this share of its bending or more
MOVE_FLOOR = 0.1       # mm: less bending than this is not read
DOWN_I, DOWN_J = "Down at I (mm)", "Down at J (mm)"
TIP_I, TIP_J = "Tip from rotation at I (mm)", "Tip from rotation at J (mm)"
PUSH_SHARE = 0.05      # an end force above this share of the member's largest shear is read
PUSH_FLOOR = 0.5       # kN: smaller end forces are not read at all
CARRIER_FAILED = "FAILED: DEPTH BELOW THE BEAM IT CARRIES"
NOT_A_CARRIER = "N/A - carries no beam"


def _name(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def carried_beams(connectivity: pd.DataFrame, points: pd.DataFrame | None,
                  pushes: dict[tuple[str, str], int] | None = None) -> dict[str, list[str]]:
    """{carrier beam: the beams it carries}, from the frame connectivity, the
    joint coordinates and, with ``pushes`` (``end_pushes``), the shear and
    moment diagrams. Empty without coordinates: the direction of a beam is
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
    network = network_for(connectivity, points, pushes)
    # the beams near each level, so that a joint is compared with those and not with all
    by_level: dict[int, list[str]] = {}
    for other, (a, b) in beams.items():
        low, high = sorted((round(xyz[a][2] / SAME_LEVEL), round(xyz[b][2] / SAME_LEVEL)))
        for level in range(low, high + 1):
            by_level.setdefault(level, []).append(other)
    out: dict[str, list[str]] = {}
    for name, ends in beams.items():
        own = directions[name]
        if own is None:
            continue
        rank = network.rank_of([name])
        for joint in ends:
            if joint in column_joints:
                continue
            push = network.pushes.get((name, joint), 0)
            if push < 0:
                continue                   # a downward force on this end: nothing carries it here
            level = round(xyz[joint][2] / SAME_LEVEL)
            near = {o for k in range(level - 2, level + 3) for o in by_level.get(k, ())}
            meeting = [(other, meets(other, joint)) for other in beams
                       if other != name and other in near]
            meeting = [(other, how) for other, how in meeting
                       if how and directions[other] is not None]
            for other, how in meeting:
                d = directions[other]
                if abs(own[0] * d[0] + own[1] * d[1]) >= IN_LINE:
                    continue               # it continues this beam
                if how == "end":           # a girder in two pieces: its partner is in line
                    partner = any(abs(d[0] * directions[third][0] + d[1] * directions[third][1])
                                  >= IN_LINE for third, _ in meeting if third != other)
                    if not partner:        # it only ends here: a carrier when it pushes up
                        lifts = network.pushes.get((other, joint), 0)
                        if lifts > 0 or (lifts == 0 and network.rank_of([other]) >= rank):
                            continue
                if push == 0 and network.rank_of([other]) > rank:
                    continue               # no diagram to read, and it is further from the supports
                carried = out.setdefault(other, [])
                if name not in carried:
                    carried.append(name)
    return out


COLUMN_END, BEAM_END, FREE_END = "column", "beam", "free"


def end_conditions(connectivity: pd.DataFrame, points: pd.DataFrame | None,
                   pushes: dict[tuple[str, str], int] | None = None
                   ) -> dict[tuple[str, str], str]:
    """What holds each beam end: {(beam, joint): "column", "beam" or "free"}.

    * ``column``: a column or a wall is at the joint.
    * ``beam``: another beam carries the end: one that is nearer to the
      supports along the load path, or as near and running through the joint
      (``BeamNetwork.held_at``). It may end at the joint or pass through it
      (a girder ETABS has not split there).
    * ``free``: nothing holds it. A beam that only rests on this one does not:
      a cantilever with an edge beam on its tip is still free at the tip.

    An end that only meets a beam continuing in line is as held as the far end
    of that beam: a cantilever ETABS has in two pieces is still free at its
    root piece's far end, and a girder in pieces between columns is not.
    Needs the joint coordinates; empty without them.
    """
    if connectivity is None or connectivity.empty or points is None or points.empty:
        return {}
    xyz = {_name(n): (float(x), float(y), float(z)) for n, x, y, z in zip(
        points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
        pd.to_numeric(points["Y"], errors="coerce"), pd.to_numeric(points["Z"], errors="coerce"))}
    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    supports = set()
    rows = connectivity.loc[kind.isin(["column", "wall"])]
    for column in ("UniquePtI", "UniquePtJ", "UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4"):
        if column in rows.columns:
            supports |= {_name(v) for v in rows[column].dropna()}
    beams = {}
    for row in connectivity.loc[kind.eq("beam")].drop_duplicates("UniqueName").to_dict("records"):
        i, j = _name(row["UniquePtI"]), _name(row["UniquePtJ"])
        if i in xyz and j in xyz:
            beams[_name(row["UniqueName"])] = (i, j)

    def unit(name: str):
        (x1, y1, _), (x2, y2, _) = (xyz[q] for q in beams[name])
        length = math.hypot(x2 - x1, y2 - y1)
        return ((x2 - x1) / length, (y2 - y1) / length) if length > ON_LINE else None

    directions = {name: unit(name) for name in beams}
    at_joint: dict[str, list[str]] = {}
    by_level: dict[int, list[str]] = {}
    for name, ends in beams.items():
        for joint in ends:
            at_joint.setdefault(joint, []).append(name)
            by_level.setdefault(round(xyz[joint][2] / SAME_LEVEL), []).append(name)

    def passes_through(name: str, joint: str) -> bool:
        """Whether another beam's centre line runs through the joint, inside its length."""
        x, y, z = xyz[joint]
        level = round(z / SAME_LEVEL)
        for other in {o for k in (level - 1, level, level + 1) for o in by_level.get(k, [])}:
            if other == name or joint in beams[other]:
                continue
            (x1, y1, z1), (x2, y2, z2) = (xyz[q] for q in beams[other])
            if abs(z - (z1 + z2) / 2.0) > SAME_LEVEL + abs(z2 - z1) / 2.0:
                continue
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length <= ON_LINE:
                continue
            along = ((x - x1) * dx + (y - y1) * dy) / length
            off = abs((x - x1) * dy - (y - y1) * dx) / length
            if off <= ON_LINE and ON_LINE < along < length - ON_LINE:
                return True
        return False

    network = network_for(connectivity, points, pushes)
    raw: dict[tuple[str, str], str | list[str]] = {}
    for name, ends in beams.items():
        own = directions[name]
        for joint in ends:
            if joint in supports:
                raw[(name, joint)] = COLUMN_END
                continue
            others = [o for o in at_joint.get(joint, []) if o != name]
            in_line = [o for o in others if own is not None and directions[o] is not None
                       and abs(own[0] * directions[o][0] + own[1] * directions[o][1]) >= IN_LINE]
            if network.held_at(joint, [name] + in_line):
                raw[(name, joint)] = BEAM_END
            elif in_line:
                raw[(name, joint)] = in_line         # the line goes on: as held as its far end
            else:
                raw[(name, joint)] = FREE_END

    def resolve(name: str, joint: str, seen: frozenset) -> str:
        state = raw[(name, joint)]
        if isinstance(state, str):
            return state
        results = []
        for other in state:                # the line goes on: what holds its far end?
            if other in seen:
                continue
            far = beams[other][1] if beams[other][0] == joint else beams[other][0]
            results.append(resolve(other, far, seen | {other}))
        if COLUMN_END in results or BEAM_END in results:
            return BEAM_END                # held through the continuing beam
        return FREE_END

    return {key: resolve(key[0], key[1], frozenset({key[0]})) for key in raw}


def end_pushes(forces: pd.DataFrame | None, combo: str | None,
               connectivity: pd.DataFrame | None) -> dict[tuple[str, str], int]:
    """What the shear and moment diagrams say holds each beam end.

    {(beam, joint): +1, -1 or 0} for the beams of ``forces`` under the load
    combination ``combo`` (gravity). +1: the end is pushed up, so something
    carries it there. -1: a downward force acts on the end, so it is a free
    end that carries what sits on it. 0: too small to read. The sign of the
    shear is taken from the slope of the moment, whatever sign the analysis
    uses. ``forces`` has UniqueName, Combo, Station, M3 and V2.
    """
    needed = ("UniqueName", "Combo", "Station", "M3", "V2")
    if forces is None or connectivity is None or not combo or len(forces) == 0 \
            or any(c not in forces.columns for c in needed):
        return {}
    import numpy as np

    from design.beam_deflection import statics_shear

    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    ends = {_name(r["UniqueName"]): (_name(r["UniquePtI"]), _name(r["UniquePtJ"]))
            for r in connectivity.loc[kind.eq("beam")].to_dict("records")}
    table = forces.loc[forces["Combo"].astype(str).eq(str(combo)), list(needed)]
    out: dict[tuple[str, str], int] = {}
    for name, rows in table.groupby(table["UniqueName"].map(_name), sort=False):
        if name not in ends:
            continue
        rows = rows.sort_values("Station", kind="stable")
        x = pd.to_numeric(rows["Station"], errors="coerce").to_numpy(float)
        moment = pd.to_numeric(rows["M3"], errors="coerce").to_numpy(float)
        shear = pd.to_numeric(rows["V2"], errors="coerce").to_numpy(float)
        if len(x) < 2 or np.isnan(moment).any() or np.isnan(shear).any():
            continue
        found = statics_shear([(x, moment, shear)])
        if found is None:
            continue
        q = found[0]
        limit = max(PUSH_SHARE * float(np.max(np.abs(q))), PUSH_FLOOR)
        for joint, up in ((ends[name][0], float(q[0])), (ends[name][1], -float(q[-1]))):
            out[(name, joint)] = 1 if up > limit else (-1 if up < -limit else 0)
    return out


def end_moves(forces: pd.DataFrame | None, combo: str | None,
              connectivity: pd.DataFrame | None) -> dict[tuple[str, str], int]:
    """What the deflected shape of the analysis says about each beam end.

    {(beam, joint): -1 or +1}. -1: the end drops like the tip of a
    cantilever. +1: it stays, held where it is. The shear and moment
    diagrams cannot tell these apart when a downward force acts on the end:
    a free tip that carries a beam and an end that a girder or a column in
    tension holds down look the same. The movement does.

    For an end, measured from the other end of the member: ``delta`` is how
    far it moves down, and the part of it that is bending is ``delta`` less
    the movement from the rotation of the other end. With the other end held,
    a free tip moves down by its bending or more (the rotation adds to it);
    a held end does not move, the rotation of the far end taking up the
    bending. The end drops when delta is at least ``MOVE_SHARE`` of the
    bending. ``forces`` needs the joint movements and the tip movements from
    the end rotations (the service loads table has them); empty without.
    """
    needed = ("UniqueName", "Combo", DOWN_I, DOWN_J, TIP_I, TIP_J)
    if forces is None or connectivity is None or not combo or len(forces) == 0 \
            or any(c not in forces.columns for c in needed):
        return {}
    kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
    ends = {_name(r["UniqueName"]): (_name(r["UniquePtI"]), _name(r["UniquePtJ"]))
            for r in connectivity.loc[kind.eq("beam")].to_dict("records")}
    table = forces.loc[forces["Combo"].astype(str).eq(str(combo)), list(needed)]
    table = table.drop_duplicates("UniqueName")
    out: dict[tuple[str, str], int] = {}
    for name, down_i, down_j, tip_i, tip_j in zip(
            table["UniqueName"].map(_name), *(pd.to_numeric(table[c], errors="coerce")
                                              for c in (DOWN_I, DOWN_J, TIP_I, TIP_J))):
        if name not in ends or any(v != v for v in (down_i, down_j, tip_i, tip_j)):
            continue
        # (the end, how far it moves down from the other end, the part from that end's rotation)
        for joint, delta, rotation in ((ends[name][1], down_j - down_i, tip_i),
                                       (ends[name][0], down_i - down_j, tip_j)):
            bending = delta - rotation
            drops = bending > MOVE_FLOOR and delta >= MOVE_SHARE * bending
            out[(name, joint)] = -1 if drops else 1
    return out


class LoadPath:
    """What the analysis says holds each beam end: ``pushes`` from the shear and
    moment diagrams (``end_pushes``) and ``moves`` from the deflected shape
    (``end_moves``). Either may be empty."""

    def __init__(self, pushes: dict | None = None, moves: dict | None = None):
        self.pushes = pushes or {}
        self.moves = moves or {}

    def __bool__(self) -> bool:
        return bool(self.pushes or self.moves)


def read_load_path(forces: pd.DataFrame | None, combo: str | None,
                   connectivity: pd.DataFrame | None) -> LoadPath:
    """The load path of ``forces`` under ``combo``: the diagrams and, where the table
    has the joint movements, the deflected shape."""
    return LoadPath(end_pushes(forces, combo, connectivity),
                    end_moves(forces, combo, connectivity))


_NETWORKS: dict[tuple, "BeamNetwork"] = {}


def network_for(connectivity, points=None, pushes=None) -> "BeamNetwork":
    """The beam network of these tables, built once and kept while the same tables are
    in use: the support status, the carriers and the deflection all ask for it, and
    building it goes through every beam of the model."""
    framing = (id(connectivity), id(points),
               0 if connectivity is None else len(connectivity), 0 if points is None else len(points))
    key = framing + (id(pushes),)
    network = _NETWORKS.get(key)
    if network is None:
        if len(_NETWORKS) >= 8:
            _NETWORKS.clear()
        # what meets what and the ranks depend on the framing alone: found once, and
        # shared by the readings of the load path made on the same framing
        base = _NETWORKS.get(framing)
        if base is None:
            base = _NETWORKS[framing] = BeamNetwork(connectivity, points)
        network = _NETWORKS[key] = base.with_load_path(pushes)
    return network


def forget_networks() -> None:
    """Drop the networks kept by ``network_for`` (the tables may be changed next)."""
    _NETWORKS.clear()


class BeamNetwork:
    """Which beams meet, and which of them can reach a support.

    A support is a joint with a column below it or a wall. A column that
    only starts at a joint (a planted column) is a load on the beam there,
    not a support. Two beams meet when they share an end joint, or when the
    end of one lies on the centre line of the other (a girder ETABS has not
    split there; this needs the joint coordinates).

    ``held_at`` answers what the deflection check, the support status and
    the carrier rule need: is a beam end held up, or is it a free end that
    only carries what sits on it?

    The shear and moment diagrams of the analysis come first (``pushes``,
    from ``end_pushes``): an end that is pushed up is held, whatever the
    framing looks like. An edge beam that the girders ending on it push
    down, while their own ends are pushed up, carries those girders.

    A downward force on an end does not settle it: a free tip that carries
    a beam and an end that a girder or a column in tension holds down (the
    back span of a see-saw) have the same diagrams. The deflected shape of
    the analysis tells them apart (``moves``, from ``end_moves``): a free tip
    drops, a held end stays.

    Where neither is there, the framing decides. Every beam line has a rank: 0 when it rests
    on a support, 1 when it rests only on lines of rank 0, and so on (a line
    is the pieces of one tagged line, or pieces that continue each other in
    a straight line). An end is then held when another line there is nearer
    to the supports, or as near, running through the joint and supported at both its own
    ends: the member that holds an end down must itself be held.

    ``pushes`` is a ``LoadPath``, or the dictionary of ``end_pushes`` alone.
    """

    def __init__(self, connectivity: pd.DataFrame | None, points: pd.DataFrame | None = None,
                 pushes: dict[tuple[str, str], int] | None = None):
        self.moves = getattr(pushes, "moves", None) or {}
        self.pushes = (pushes.pushes if isinstance(pushes, LoadPath) else pushes) or {}
        self.beams: dict[str, tuple[str, str]] = {}
        self.supports: set[str] = set()
        self.at_joint: dict[str, list[str]] = {}
        self.passing: dict[str, list[str]] = {}
        self.neighbours: dict[str, set[str]] = {}
        self.grounded: set[str] = set()
        self.line: dict[str, str] = {}
        self.rank: dict[str, float] = {}
        self.line_ends: dict[str, set[str]] = {}
        if connectivity is None or len(connectivity) == 0:
            return
        xyz: dict[str, tuple[float, float, float]] = {}
        if points is not None and len(points):
            xyz = {_name(n): (float(x), float(y), float(z)) for n, x, y, z in zip(
                points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
                pd.to_numeric(points["Y"], errors="coerce"),
                pd.to_numeric(points["Z"], errors="coerce")) if x == x and y == y and z == z}
        kind = connectivity["DesignType"].astype(str).str.strip().str.casefold()
        known = [c for c in ("UniquePtI", "UniquePtJ", "UniquePt1", "UniquePt2", "UniquePt3",
                             "UniquePt4") if c in connectivity.columns]

        def joints(row) -> list[str]:
            return [_name(row[c]) for c in known
                    if row[c] is not None and str(row[c]) not in ("nan", "None", "")]

        for row in connectivity.loc[kind.eq("wall")].to_dict("records"):
            self.supports |= set(joints(row))
        for row in connectivity.loc[kind.eq("column")].to_dict("records"):
            i, j = _name(row["UniquePtI"]), _name(row["UniquePtJ"])
            if i in xyz and j in xyz:      # the top joint has the column below it
                self.supports.add(j if xyz[j][2] >= xyz[i][2] else i)
            else:
                self.supports.add(j)       # ETABS draws a column from its base up
        for row in connectivity.loc[kind.eq("beam")].drop_duplicates("UniqueName").to_dict(
                "records"):
            name = _name(row["UniqueName"])
            self.beams[name] = (_name(row["UniquePtI"]), _name(row["UniquePtJ"]))
        for name, ends in self.beams.items():
            self.neighbours.setdefault(name, set())
            for joint in ends:
                self.at_joint.setdefault(joint, []).append(name)
            if ends[0] in self.supports or ends[1] in self.supports:
                self.grounded.add(name)
        self._find_passing(xyz)
        for joint, names in self.at_joint.items():
            met = set(names) | set(self.passing.get(joint, []))
            for name in met:
                self.neighbours.setdefault(name, set()).update(met - {name})
        self._find_lines(connectivity, xyz)
        self._find_ranks()

    def _find_lines(self, connectivity: pd.DataFrame, xyz: dict) -> None:
        """Group the pieces into lines: the same tag line, or in a straight line at a joint."""
        import re

        parent = {name: name for name in self.beams}

        def root(name: str) -> str:
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def join(a: str, b: str) -> None:
            parent[root(a)] = root(b)

        tagged: dict[str, str] = {}
        if "Line" in connectivity.columns:
            for name, value in zip(connectivity["UniqueName"], connectivity["Line"]):
                if value is not None and str(value) not in ("nan", "None", ""):
                    tagged[_name(name)] = str(value)
        mark = re.compile(r"^(.*?)(BX|BY|GX|GY)-(\d+)([A-Z]*)$", re.IGNORECASE)
        first: dict[str, str] = {}
        for name in self.beams:
            match = mark.match(name)
            key = tagged.get(name) or (
                (match.group(1) + match.group(2) + "-" + match.group(3)).upper() if match else None)
            if key is None:
                continue
            if key in first:
                join(name, first[key])
            else:
                first[key] = name

        def direction(name: str):
            i, j = self.beams[name]
            if i not in xyz or j not in xyz:
                return None
            dx, dy = xyz[j][0] - xyz[i][0], xyz[j][1] - xyz[i][1]
            length = math.hypot(dx, dy)
            return (dx / length, dy / length) if length > ON_LINE else None

        for names in self.at_joint.values():
            for index, a in enumerate(names):
                da = direction(a)
                for b in names[index + 1:]:
                    db = direction(b)
                    if da and db and abs(da[0] * db[0] + da[1] * db[1]) >= IN_LINE:
                        join(a, b)
        self.line = {name: root(name) for name in self.beams}
        count: dict[tuple[str, str], int] = {}
        for name, ends in self.beams.items():
            for joint in ends:
                key = (self.line[name], joint)
                count[key] = count.get(key, 0) + 1
        for (line, joint), pieces in count.items():
            if pieces == 1:                      # one piece of the line ends there: a line end
                self.line_ends.setdefault(line, set()).add(joint)

    def _find_ranks(self) -> None:
        """Rank of every line: 0 on a support, otherwise one more than the nearest line it meets."""
        meets: dict[str, set[str]] = {}
        for name, others in self.neighbours.items():
            meets.setdefault(self.line[name], set()).update(
                self.line[o] for o in others if self.line[o] != self.line[name])
        level = {self.line[name] for name in self.grounded}
        rank = 0
        while level:
            for line in level:
                self.rank[line] = rank
            level = {o for line in level for o in meets.get(line, ()) if o not in self.rank}
            rank += 1

    def rank_of(self, members) -> float:
        """The rank of the line(s) of some pieces: the nearest to the supports. Infinite
        when they reach no support at all."""
        return min((self.rank.get(self.line.get(_name(m), ""), math.inf) for m in members),
                   default=math.inf)

    def _find_passing(self, xyz: dict) -> None:
        """The beams whose centre line runs through each beam end joint, inside their length."""
        located = {n: e for n, e in self.beams.items() if e[0] in xyz and e[1] in xyz}
        by_level: dict[int, list[str]] = {}
        for name, ends in located.items():
            for joint in ends:
                by_level.setdefault(round(xyz[joint][2] / SAME_LEVEL), []).append(name)
        for joint in self.at_joint:
            if joint not in xyz:
                continue
            x, y, z = xyz[joint]
            level = round(z / SAME_LEVEL)
            found = []
            for other in {o for k in (level - 1, level, level + 1) for o in by_level.get(k, [])}:
                if joint in located[other]:
                    continue
                (x1, y1, z1), (x2, y2, z2) = (xyz[q] for q in located[other])
                if abs(z - (z1 + z2) / 2.0) > SAME_LEVEL + abs(z2 - z1) / 2.0:
                    continue
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                if length <= ON_LINE:
                    continue
                along = ((x - x1) * dx + (y - y1) * dy) / length
                off = abs((x - x1) * dy - (y - y1) * dx) / length
                if off <= ON_LINE and ON_LINE < along < length - ON_LINE:
                    found.append(other)
            if found:
                self.passing[joint] = sorted(found)

    def others_at(self, joint, own) -> set[str]:
        """The beams that end at a joint or pass through it, apart from ``own``."""
        joint = _name(joint)
        return (set(self.at_joint.get(joint, [])) | set(self.passing.get(joint, []))) - set(own)

    def reaches_support(self, starts, without) -> bool:
        """Whether any of the beams ``starts`` reaches a support through the
        beams it meets, never passing through the beams ``without``."""
        without = set(without)
        seen = set(starts) - without
        queue = list(seen)
        while queue:
            name = queue.pop()
            if name in self.grounded:
                return True
            for other in self.neighbours.get(name, ()):
                if other not in seen and other not in without:
                    seen.add(other)
                    queue.append(other)
        return False

    def with_load_path(self, pushes) -> "BeamNetwork":
        """The same framing with another reading of the load path (nothing is found again)."""
        import copy

        other = copy.copy(self)
        other.moves = getattr(pushes, "moves", None) or {}
        other.pushes = (pushes.pushes if isinstance(pushes, LoadPath) else pushes) or {}
        return other

    def push_at(self, joint, own) -> int:
        """+1, -1 or 0 for the end of the line ``own`` at ``joint`` (see ``end_pushes``);
        0 as well where the line goes on through the joint, which is then not an end."""
        joint = _name(joint)
        enders = [m for m in own if joint in self.beams.get(_name(m), ())]
        if len(enders) != 1:
            return 0
        return self.pushes.get((_name(enders[0]), joint), 0)

    def move_at(self, joint, own) -> int:
        """-1 (drops), +1 (stays) or 0 (not known) for the end of the line ``own`` at ``joint``."""
        joint = _name(joint)
        enders = [m for m in own if joint in self.beams.get(_name(m), ())]
        if len(enders) != 1:
            return 0
        return self.moves.get((_name(enders[0]), joint), 0)

    def held_at(self, joint, own) -> bool:
        """Whether the end of the beams ``own`` at ``joint`` is held up (see the class)."""
        joint = _name(joint)
        if joint in self.supports:
            return True
        own = {_name(m) for m in own}
        if self.push_at(joint, own) > 0:
            return True                          # the diagrams show it pushed up: it is carried
        # A downward force on the end does not settle it: a free tip that carries a beam and
        # an end that is held down have the same diagrams. The deflected shape does.
        move = self.move_at(joint, own)
        if move:
            return move > 0
        # no movements to read: the framing decides
        own_lines = {self.line.get(m) for m in own}
        mine = self.rank_of(own)
        for other in self.others_at(joint, own):
            line = self.line.get(other)
            if line in own_lines:
                continue                         # the same line going on: not a support
            theirs = self.rank.get(line, math.inf)
            if theirs < mine:
                return True                      # nearer to the supports: it carries this end
            runs_through = joint not in self.line_ends.get(line, ()) \
                or other in self.passing.get(joint, ())
            if theirs == mine and theirs != math.inf and runs_through \
                    and self.supported_both_ends(line):
                return True
        return False

    def supported_both_ends(self, line: str) -> bool:
        """Whether a line is held at both its ends, by a support or pushed up there (from
        the diagrams). An end the diagrams say nothing about counts as held."""
        for joint in self.line_ends.get(line, ()):
            if joint in self.supports:
                continue
            enders = [m for m in self.at_joint.get(joint, ()) if self.line.get(m) == line]
            if any(self.pushes.get((m, joint), 1) <= 0 for m in enders):
                return False
        return True


def add_carrier_depth_check(results: pd.DataFrame, connectivity: pd.DataFrame,
                            points: pd.DataFrame | None,
                            pushes: dict[tuple[str, str], int] | None = None) -> pd.DataFrame:
    """Add ``Carried_Beam_Depth`` and ``Carrier_Depth_Check`` to the beam results.

    A beam shallower than a beam it carries fails; a passing beam then gets
    the status ``CARRIER_FAILED`` (the design loop makes it deeper).
    """
    out = results.copy()
    names = out["UniqueName"].astype(str)
    depth_of = dict(zip(names, pd.to_numeric(out["Depth"], errors="coerce")))
    carried = carried_beams(connectivity, points, pushes)
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
