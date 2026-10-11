"""The reinforcing bars of the 3D model (``sdt 3d``), from the last beam and
column designs saved for the model.

What the designs give is drawn, and nothing more:

* **Columns**: every longitudinal bar at its designed position, from the bottom
  joint of the column to its top joint, and the ties (or the rings of a spiral)
  at the designed spacing, with their inner legs.
* **Beams**: the top and bottom bars of each layer, the web bars, and the
  stirrups at their two spacings (within 2h of each support, and between).

The designs do not give laps, hooks, bends, anchorage or cut-off points, so the
bars are straight and stop at the ends of the member. Where a beam has more
bars at a support or at midspan than along its whole length, the extra bars
are drawn over a fixed share of the clear span (``SUPPORT_SHARE``,
``MIDSPAN_SHARE``): drawn lengths, not designed ones.

Everything here works on plain data: no ETABS and no build123d.
"""

from __future__ import annotations

import math

from etabs_api.workflows.model_3d import REBAR, TIE, Bar, Hoop, Model3D, _cross, _unit

NO_BARS, MAIN_BARS, ALL_BARS = "none", "main", "all"
SUPPORT_SHARE = 0.30       # extra bars at a support: this share of the clear span from its face
MIDSPAN_SHARE = 0.50       # extra bars at midspan: this share of the clear span, centred
LAYER_GAP = 25.0           # mm clear between two layers of beam bars (not less than the bar)
FIRST_TIE = 50.0           # mm from the end of the member to its first stirrup or tie
MIN_PIECE = 50.0           # mm: no bar shorter than this is drawn


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class Frame:
    """The local axes of a member solid: ``s`` along it from its start, ``y``
    to its side and ``z`` along its ``up`` (both from its centre line)."""

    def __init__(self, member: Bar):
        self.member = member
        self.axis = _unit(tuple(b - a for a, b in zip(member.start, member.end)))
        self.up = _unit(member.up) or (0.0, 0.0, 1.0)
        self.side = _unit(_cross(self.up, self.axis))
        self.length = member.length

    def point(self, s: float, y: float, z: float) -> tuple[float, float, float]:
        return tuple(p + s * a + y * b + z * c for p, a, b, c in zip(
            self.member.start, self.axis, self.side, self.up))

    def bar(self, s0: float, s1: float, y: float, z: float, diameter: float) -> Bar | None:
        if s1 - s0 < MIN_PIECE or diameter <= 0:
            return None
        return Bar(REBAR, self.member.name, self.member.group, self.point(s0, y, z),
                   self.point(s1, y, z), diameter, diameter, self.up, circular=True)

    def hoop(self, s: float, width: float, depth: float, diameter: float, inner_up: int = 0,
             inner_side: int = 0, circular: bool = False) -> Hoop:
        return Hoop(TIE, self.member.name, self.member.group, self.point(s, 0.0, 0.0), self.axis,
                    self.up, width, depth, diameter, max(inner_up, 0), max(inner_side, 0),
                    circular)


def spread(count: int, half_width: float) -> list[float]:
    """``count`` positions across a width, the two outer ones at its ends."""
    if count <= 0:
        return []
    if count == 1:
        return [0.0]
    return [-half_width + 2.0 * half_width * i / (count - 1) for i in range(count)]


def outer_first(count: int) -> list[int]:
    """The order in which the positions of a layer are filled: the two outer
    bars, then inward, so that the bars that run the whole length are the outer
    ones."""
    order, low, high = [], 0, count - 1
    while low <= high:
        order.append(low)
        if high != low:
            order.append(high)
        low, high = low + 1, high - 1
    return order


def layer_pieces(left: int, middle: int, right: int, length: float
                 ) -> list[tuple[int, int, float, float]]:
    """The bars of one layer as (position index, positions in the layer, from,
    to) along the member. A bar present at both supports and at midspan runs the
    whole length; the others run over the zone they are needed in."""
    most = max(left, middle, right)
    zones = {"left": (0.0, SUPPORT_SHARE * length),
             "middle": ((0.5 - MIDSPAN_SHARE / 2.0) * length, (0.5 + MIDSPAN_SHARE / 2.0) * length),
             "right": ((1.0 - SUPPORT_SHARE) * length, length)}
    counts = {"left": left, "middle": middle, "right": right}
    out = []
    for rank, index in enumerate(outer_first(most)):
        needed = [name for name in ("left", "middle", "right") if counts[name] > rank]
        if len(needed) == 3:
            out.append((index, most, 0.0, length))
            continue
        spans = [zones[name] for name in needed]
        merged = []
        for start, end in spans:              # zones that overlap are one bar
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        out += [(index, most, start, end) for start, end in merged]
    return out


def tie_stations(length: float, near: float, between: float, zone: float) -> list[float]:
    """Where the stirrups or ties are along a member: from ``FIRST_TIE`` at each
    end, at the spacing ``near`` within ``zone`` of each end and ``between``
    in the rest."""
    near = near if near > 0 else between
    between = between if between > 0 else near
    if near <= 0 or length <= 2.0 * FIRST_TIE:
        return []
    end = length - FIRST_TIE
    if length <= 2.0 * zone or abs(near - between) < 1e-6:
        count = max(int(math.floor((end - FIRST_TIE) / near + 1e-6)), 0)
        return [FIRST_TIE + i * near for i in range(count + 1)]
    left, s = [], FIRST_TIE
    while s <= zone + 1e-6:
        left.append(s)
        s += near
    right = [length - x for x in left][::-1]
    # between the two zones: equal spaces, none wider than the spacing
    gap = right[0] - left[-1]
    spaces = max(int(math.ceil(gap / between - 1e-6)), 1)
    middle = [left[-1] + gap * i / spaces for i in range(1, spaces)]
    return left + middle + right


# =============================================================================
# BEAMS
# =============================================================================
def beam_reinforcement(member: Bar, top: dict, bottom: dict, ties: bool
                       ) -> tuple[list[Bar], list[Hoop]]:
    """The bars and stirrups of one beam, from the two rows (top face, bottom
    face) of the beam design results.

    The layers of a face are numbered from the top of the section down: layer
    1 is the outer layer of the top bars and layer 3 the outer layer of the
    bottom bars.
    """
    frame = Frame(member)
    width, depth, length = member.width, member.depth, frame.length
    main, stirrup = _number(top.get("dm")), _number(top.get("ds"))
    cover = _number(top.get("cc"))
    edge = cover + stirrup + main / 2.0
    step = main + max(LAYER_GAP, main)
    bars: list[Bar] = []
    for face, row in (("top", top), ("bottom", bottom)):
        for layer in (1, 2, 3):
            counts = [int(_number(row.get(f"n_{zone}_L{layer}"))) for zone in
                      ("left", "mid", "right")]
            if not any(counts):
                continue
            inward = (layer - 1) if face == "top" else (3 - layer)
            z = (depth / 2.0 - edge - inward * step) * (1.0 if face == "top" else -1.0)
            for index, most, start, end in layer_pieces(*counts, length):
                y = spread(most, width / 2.0 - edge)[index]
                piece = frame.bar(start, end, y, z, main)
                if piece is not None:
                    bars.append(piece)
    web = _number(top.get("dw"))
    per_face = int(max(_number(top.get("n_side_per_face_gov")),
                       _number(bottom.get("n_side_per_face_gov"))))
    if per_face > 0 and web > 0:
        high = depth / 2.0 - edge
        for k in range(1, per_face + 1):
            z = -high + 2.0 * high * k / (per_face + 1)
            for y in (-(width / 2.0 - cover - stirrup - web / 2.0),
                      width / 2.0 - cover - stirrup - web / 2.0):
                piece = frame.bar(0.0, length, y, z, web)
                if piece is not None:
                    bars.append(piece)
    hoops: list[Hoop] = []
    if ties and stirrup > 0:
        legs = int(_number(top.get("Stirrup_Legs"), 2.0))
        for s in tie_stations(length, _number(top.get("Spacing_2H")),
                              _number(top.get("Spacing_Mid")), 2.0 * depth):
            hoops.append(frame.hoop(s, width - 2.0 * cover - stirrup,
                                    depth - 2.0 * cover - stirrup, stirrup, legs - 2))
    return bars, hoops


# =============================================================================
# COLUMNS
# =============================================================================
def decode_layout(text) -> list[tuple[float, float, int]]:
    """``x,y,n;x,y,n`` of the column report as (x, y, bars at that position)."""
    out = []
    for part in str(text or "").split(";"):
        values = part.split(",")
        if len(values) == 3:
            try:
                out.append((float(values[0]), float(values[1]), int(float(values[2]))))
            except ValueError:
                continue
    return out


def column_reinforcement(member: Bar, spec: dict, ties: bool) -> tuple[list[Bar], list[Hoop]]:
    """The bars and ties of one column. ``spec``: its ``layout`` (x across the
    width and y along the depth from a corner; from the centre for a circular
    column), ``main`` and ``tie`` diameters, ``cover``, ``spacing`` of the ties
    and their ``legs_x`` and ``legs_y``."""
    frame = Frame(member)
    width, depth, length = member.width, member.depth, frame.length
    main, tie, cover = spec["main"], spec["tie"], spec["cover"]
    bars: list[Bar] = []
    for x, y, bundle in spec["layout"]:
        across, along = (x, y) if member.circular else (x - width / 2.0, y - depth / 2.0)
        reach = math.hypot(across, along)
        for k in range(max(bundle, 1)):       # the bars of a bundle, one behind the other
            pull = 1.0 - k * main / reach if reach > main * (k + 1) else 1.0
            piece = frame.bar(0.0, length, across * pull, along * pull, main)
            if piece is not None:
                bars.append(piece)
    hoops: list[Hoop] = []
    if ties and tie > 0 and spec.get("spacing", 0.0) > 0:
        ring = width - 2.0 * cover - tie
        for s in tie_stations(length, spec["spacing"], spec["spacing"], length):
            if member.circular:
                hoops.append(frame.hoop(s, ring, ring, tie, circular=True))
            else:
                hoops.append(frame.hoop(s, ring, depth - 2.0 * cover - tie, tie,
                                        int(spec.get("legs_y", 2)) - 2,
                                        int(spec.get("legs_x", 2)) - 2))
    return bars, hoops


def column_specs(report, main: float) -> dict[str, dict]:
    """What each column of the design report has: its bar layout, its ties at
    the closest spacing of its two ends, and the most legs of the two."""
    import pandas as pd

    out = {}
    for name, rows in report.groupby(report["UniqueName"].astype(str)):
        first = rows.iloc[0]
        layout = decode_layout(first.get("Bar_Layout_Data"))
        if not layout:
            continue
        spacing = pd.to_numeric(rows["Transverse_Spacing_mm"], errors="coerce").min()
        out[name] = {
            "layout": layout, "main": main, "tie": _number(first.get("Tie_Bar_mm")),
            "cover": _number(first.get("Cover_mm")),
            "spacing": float(spacing) if spacing == spacing else 0.0,
            "legs_x": int(_number(pd.to_numeric(rows["Transverse_Legs_X"],
                                                errors="coerce").max(), 2.0)),
            "legs_y": int(_number(pd.to_numeric(rows["Transverse_Legs_Y"],
                                                errors="coerce").max(), 2.0))}
    return out


# =============================================================================
# THE MODEL
# =============================================================================
def add_reinforcement(model: Model3D, beam_results, column_report, column_main: float,
                      level: str) -> dict[str, int]:
    """Add the bars of the designed beams and columns to ``model``. ``level``:
    ``MAIN_BARS`` for the longitudinal bars, ``ALL_BARS`` with stirrups and
    ties. Returns the counts: members with bars, and members with no design."""
    from etabs_api.workflows.model_3d import BEAM, COLUMN

    counts = {"beams": 0, "columns": 0, "beams_without": 0, "columns_without": 0}
    if level not in (MAIN_BARS, ALL_BARS):
        return counts
    ties = level == ALL_BARS
    rows_of: dict[str, dict[str, dict]] = {}
    if beam_results is not None and len(beam_results):
        for _, row in beam_results.iterrows():
            rows_of.setdefault(str(row["UniqueName"]), {})[str(row["Face"]).upper()] = row.to_dict()
    specs = column_specs(column_report, column_main) if column_report is not None and \
        len(column_report) else {}
    bars: list[Bar] = []
    hoops: list[Hoop] = []
    for member in list(model.bars):
        if member.kind == BEAM:
            rows = rows_of.get(member.name, {})
            if "TOP" not in rows or "BOTTOM" not in rows:
                counts["beams_without"] += 1
                continue
            found = beam_reinforcement(member, rows["TOP"], rows["BOTTOM"], ties)
            counts["beams"] += 1
        elif member.kind == COLUMN:
            if member.name not in specs:
                counts["columns_without"] += 1
                continue
            found = column_reinforcement(member, specs[member.name], ties)
            counts["columns"] += 1
        else:
            continue
        bars += found[0]
        hoops += found[1]
    model.bars += bars
    model.hoops += hoops
    return counts
