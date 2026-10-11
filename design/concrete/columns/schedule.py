"""The column schedule drawing (DXF): sections to scale with bars, hoops and ties."""

import math
import os
import re
import pandas as pd
import ezdxf
from dataclasses import dataclass
from design.criteria.code_config import CODE
from shapely.geometry import LineString
from shapely.ops import unary_union
from design.concrete.columns.section import (
    ColumnFlexureDesign,
)
from design.concrete.columns.tables import (
    _clean_table,
    _column_story_sort_key,
    _common_column_mark,
    _story_order_from_stacks,
)
from design.concrete.columns.layouts import (
    _enumerate_column_bar_layouts,
)
from design.concrete.columns.transverse import (
    _decode_bar_layout,
)
from design.concrete.columns.report import (
    column_report_display,
)


def _bundle_histogram(summary: str) -> dict[int, int]:
    """Parse the report bundle summary into bundle-size to package-count pairs."""
    result: dict[int, int] = {}
    for part in summary.split(";"):
        single_match = re.fullmatch(r"\s*(\d+)\s+single bars\s*", part)
        bundle_match = re.fullmatch(
            r"\s*(\d+)\s+bundles of\s+(\d+)\s+bars\s*", part
        )
        if single_match:
            result[1] = int(single_match.group(1))
        elif bundle_match:
            result[int(bundle_match.group(2))] = int(bundle_match.group(1))
        elif part.strip():
            raise ValueError(f"Unrecognized column bundle summary: {summary!r}")
    return result


def _column_layout_from_report(
    row: pd.Series,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    cover: float,
    is_smrf: bool,
) -> list[tuple[float, float, int]]:
    """Return the bar layout the design selected for this report row.

    The report stores the exact coordinates in ``Bar Layout Data (x, y, n)``, so
    the drawing always shows the designed arrangement. Reports written before
    that column existed fall back to the first enumerated layout that matches
    the bar total and bundle summary.
    """
    stored_layout = _decode_bar_layout(row.get("Bar Layout Data (x, y, n)"))
    if stored_layout is not None:
        return stored_layout
    is_circular = (
        pd.notna(row.get("Diameter (mm)"))
        and float(row["Diameter (mm)"]) > 0
    )
    engine = ColumnFlexureDesign(
        width=0.0 if is_circular else float(row["Width (mm)"]),
        height=0.0 if is_circular else float(row["Depth (mm)"]),
        diameter=float(row["Diameter (mm)"]) if is_circular else 0.0,
        fc=float(row["f′c (MPa)"]),
        fy=415.0,
        fyt=415.0,
        dmain=main_bar_diameter,
        dties=tie_bar_diameter,
        cc=cover,
        shape="circular" if is_circular else "rectangular",
        is_smrf=is_smrf,
    )
    total_bars = int(float(row["Longitudinal Bars"]))
    ratio_limit = (
        engine.code.column_strength.rho_max_smrf
        if is_smrf
        else engine.code.column_strength.rho_max
    )
    gross_area = (
        math.pi * engine.diameter**2 / 4.0
        if is_circular
        else engine.width * engine.height
    )
    max_bars = int(
        ratio_limit * gross_area / (math.pi * main_bar_diameter**2 / 4.0)
    )
    layouts = _enumerate_column_bar_layouts(engine, max_bars=max_bars)
    expected_histogram = _bundle_histogram(str(row["Bundle Layout"]))
    for layout in layouts:
        histogram: dict[int, int] = {}
        for _, _, count in layout:
            histogram[count] = histogram.get(count, 0) + 1
        if (
            sum(count for _, _, count in layout) == total_bars
            and histogram == expected_histogram
        ):
            return layout
    raise ValueError(
        f"Could not reconstruct the selected bar coordinates for "
        f"{row['Unique Name']} from its report bundle summary."
    )


# ---------------------------------------------------------------------------
# DXF TIE DRAWING
#
# Every tie bar (hoop pieces and crossties) is described as a centreline path
# plus a STACKING LEVEL. A bar with a higher level lies on top of a bar with a
# lower level, so wherever two bars cross, the lower bar's outline is trimmed
# away underneath the upper bar. This is what makes the drawing read like real
# reinforcement instead of wire-frame lines passing through each other.
#
# Stacking order, bottom to top:
#   0        hoop leg that comes up the LEFT side (the end laid first)
#   1        hoop leg that comes along the TOP from the RIGHT (the end laid
#            last): it lies on top of the left leg wherever the two overlap
#   2 .. 3   interior ties parallel to Y (vertical), later ties on top of earlier
#   3 .. 4   interior ties parallel to X (horizontal), later ties on top of earlier
# Interior ties are crossties or closed inner hoops (see _crosstie_bars).
# ---------------------------------------------------------------------------
LEVEL_HOOP_LEFT_LEG = 0.0


LEVEL_HOOP_RIGHT_LEG = 1.0


LEVEL_CROSSTIE_Y = 2.0  # ties parallel to Y; x-ties (below) sit on top of them


LEVEL_CROSSTIE_X = 3.0


@dataclass(frozen=True)
class TieBar:
    """A tie bar centreline, its DXF layer, and where it sits in the stack."""

    path: list[tuple[float, float]]
    level: float
    layer: str


def _rotate_dxf_vector(
    vector: tuple[float, float], angle: float
) -> tuple[float, float]:
    """Rotate a two-dimensional DXF vector counter-clockwise."""
    cosine, sine = math.cos(angle), math.sin(angle)
    return (
        vector[0] * cosine - vector[1] * sine,
        vector[0] * sine + vector[1] * cosine,
    )


def _hook_tail_length_dxf(tie_bar_diameter: float) -> float:
    """Return the ACI 25.3.2 hook extension in millimetres (6 db, at least 75 mm)."""
    cfg = CODE.drawing
    return max(cfg.hook_tail_bar_multiple * tie_bar_diameter, cfg.hook_tail_min)


def _hook_points_dxf(
    end: tuple[float, float],
    direction: tuple[float, float],
    turn_degrees: float,
    side: int,
    bend_radius: float,
    tail_length: float,
) -> list[tuple[float, float]]:
    """Build explicit bend-arc and straight-tail points for a tie hook."""
    steps = CODE.drawing.arc_steps
    turn = math.radians(turn_degrees) * side
    normal = (-direction[1], direction[0])
    center = (
        end[0] + side * bend_radius * normal[0],
        end[1] + side * bend_radius * normal[1],
    )
    radius_vector = (end[0] - center[0], end[1] - center[1])
    points = []
    for step in range(1, steps + 1):
        dx, dy = _rotate_dxf_vector(radius_vector, turn * step / steps)
        points.append((center[0] + dx, center[1] + dy))
    tail_x, tail_y = _rotate_dxf_vector(direction, turn)
    arc_end = points[-1]
    points.append(
        (
            arc_end[0] + tail_length * tail_x,
            arc_end[1] + tail_length * tail_y,
        )
    )
    return points


def _arc_points_dxf(
    center: tuple[float, float],
    radius: float,
    start_degrees: float,
    end_degrees: float,
) -> list[tuple[float, float]]:
    """Return sampled points along a circular DXF arc."""
    steps = CODE.drawing.arc_steps
    points = []
    for index in range(steps + 1):
        angle = math.radians(
            start_degrees + (end_degrees - start_degrees) * index / steps
        )
        points.append(
            (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
        )
    return points


# ---------------------------------------------------------------------------
# BUNDLED BARS IN THE DRAWING
#
# A layout position holds 1 to 4 bars. The position itself is the bar that sits
# against the tie; the other bars of the bundle are placed like this:
#
#   bars  corner position                      face position
#   2     second bar on the diagonal, inward   second bar directly behind the first
#   3     L-shape: one bar along each face     two along the face, third behind the
#                                              bar on the tie-shaft side
#   4     2 x 2 square                         2 x 2 square
#
# On a circular column there is no tie shaft to make room for, so the third bar
# of a 3-bar bundle sits centred behind the other two (a triangle).
#
# A tie cannot be bent around a bundle with a normal bend when another bar of
# the bundle lies in the way (bundles of 3 or 4, or a second bar stacked behind
# the bar a hoop closes on). There the tie turns with a sharp corner, runs flat
# along two bars, and then bends 45 degrees toward the core. The hook extension
# leaves the bundle diagonally into the core, 135 degrees from the leg the tie
# arrived on, and clears every bar of the bundle.
# ---------------------------------------------------------------------------
BUNDLE_FLAT_HOOK_MIN = 3  # bars in a bundle from which the flat-run hook is used


INNER_TIE_STYLES = ("crossties", "hoops")


@dataclass(frozen=True)
class BarSite:
    """One layout position and the directions its bundle is arranged along.

    For a face position ``along`` runs along the face and ``inward`` points to
    the column core. For a corner position both are the inward face directions.
    ``shaft_side`` is +1 or -1 along ``along``: the side of the position on
    which the tie shaft (or inner-hoop leg) passes. ``centred_third`` places the
    third bar of a 3-bar bundle between the other two instead of behind one.
    """

    x: float
    y: float
    count: int
    along: tuple[float, float]
    inward: tuple[float, float]
    is_corner: bool = False
    shaft_side: float = -1.0
    centred_third: bool = False


def _bundle_bar_offsets(site: BarSite, bar_diameter: float) -> list[tuple[float, float]]:
    """Offsets of every bar of a bundle from its layout position (bars touch)."""
    d = bar_diameter
    (tx, ty), (nx, ny) = site.along, site.inward
    if site.count == 1:
        return [(0.0, 0.0)]
    if site.count > 4:
        raise ValueError(f"Bundles of {site.count} bars cannot be drawn (maximum is 4).")
    if site.is_corner:
        diagonal = d / math.sqrt(2.0)
        if site.count == 2:
            return [(0.0, 0.0), (diagonal * (tx + nx), diagonal * (ty + ny))]
        offsets = [(0.0, 0.0), (d * tx, d * ty), (d * nx, d * ny)]
        if site.count == 4:
            offsets.append((d * (tx + nx), d * (ty + ny)))
        return offsets
    if site.count == 2:
        return [(0.0, 0.0), (d * nx, d * ny)]
    half = site.shaft_side * d / 2.0
    first = (half * tx, half * ty)  # bar on the tie-shaft side
    second = (-half * tx, -half * ty)
    if site.count == 3 and site.centred_third:
        depth = d * math.sqrt(3.0) / 2.0  # equilateral triangle: all three bars touch
        return [first, second, (depth * nx, depth * ny)]
    offsets = [first, second, (first[0] + d * nx, first[1] + d * ny)]
    if site.count == 4:
        offsets.append((second[0] + d * nx, second[1] + d * ny))
    return offsets


def _interior_tie_coordinates(
    points: list[tuple[float, float]],
) -> tuple[list[float], list[float]]:
    """Coordinates of interior bars present on both opposite faces (x list, y list)."""
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs = sorted(
        x
        for x in {x for x, y in points if y == y_min} & {x for x, y in points if y == y_max}
        if x_min < x < x_max
    )
    horizontal_ys = sorted(
        y
        for y in {y for x, y in points if x == x_min} & {y for x, y in points if x == x_max}
        if y_min < y < y_max
    )
    return vertical_xs, horizontal_ys


def _shaft_sides(coordinates: list[float], inner_tie_style: str) -> dict[float, float]:
    """Side on which each interior tie passes its bar position (+1 or -1).

    Crossties alternate, so neighbouring hooks point away from each other.
    Inner hoops enclose positions in pairs, so each leg runs on the outside of
    its pair; an unpaired last position gets a single crosstie. The last tie
    always has its shaft on the far side, so its hook extension points away from
    the corner bundle next to it.
    """
    sides = {}
    for index, coordinate in enumerate(coordinates):
        if inner_tie_style == "hoops":
            paired_right = index % 2 == 1
            sides[coordinate] = 1.0 if paired_right else -1.0
        else:
            sides[coordinate] = -1.0 if index % 2 == 0 else 1.0
    if len(coordinates) > 1:
        sides[coordinates[-1]] = 1.0
    return sides


def _rect_bar_sites(
    layout: list[tuple[float, float, int]], inner_tie_style: str = "crossties"
) -> list[BarSite]:
    """Describe every position of a rectangular layout for drawing bars and ties."""
    if inner_tie_style not in INNER_TIE_STYLES:
        raise ValueError(f"inner_tie_style must be one of {INNER_TIE_STYLES}.")
    points = [(round(x, 4), round(y, 4)) for x, y, _ in layout]
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs, horizontal_ys = _interior_tie_coordinates(points)
    x_sides = _shaft_sides(vertical_xs, inner_tie_style)
    y_sides = _shaft_sides(horizontal_ys, inner_tie_style)

    sites = []
    for (x, y), (_, _, count) in zip(points, layout):
        on_x_face, on_y_face = x in (x_min, x_max), y in (y_min, y_max)
        inward_x = (1.0, 0.0) if x == x_min else (-1.0, 0.0)
        inward_y = (0.0, 1.0) if y == y_min else (0.0, -1.0)
        if on_x_face and on_y_face:
            sites.append(BarSite(x, y, count, inward_x, inward_y, is_corner=True))
        elif on_y_face:  # bottom or top face: ties run vertically
            sites.append(
                BarSite(x, y, count, (1.0, 0.0), inward_y, shaft_side=x_sides.get(x, -1.0))
            )
        else:  # left or right face: ties run horizontally
            sites.append(
                BarSite(x, y, count, (0.0, 1.0), inward_x, shaft_side=y_sides.get(y, -1.0))
            )
    return sites


def _circular_bar_sites(layout: list[tuple[float, float, int]]) -> list[BarSite]:
    """Describe every position of a circular layout (bundles stack toward the centre)."""
    sites = []
    for x, y, count in layout:
        radius = math.hypot(x, y)
        inward = (-x / radius, -y / radius) if radius > 0 else (0.0, -1.0)
        along = (-inward[1], inward[0])
        sites.append(BarSite(x, y, count, along, inward, centred_third=True))
    return sites


def _rect_hoop_bars(
    c_left: float,
    c_bottom: float,
    c_right: float,
    c_top: float,
    radius: float,
    tail_length: float,
    bar_pitch: float,
    neighbours: tuple[bool, bool, bool],
    left_level: float,
    right_level: float,
    layer: str,
) -> list[TieBar]:
    """Describe a closed rectangular hoop as two stacked legs closing at the top-left.

    ``c_*`` are the centres of the four corner bars; the hoop centreline runs one
    bend radius outside them. Both hook ends are at the top-left corner. The leg
    that arrives from the RIGHT (along the top, then around the hoop) is laid
    last, so it lies on top of the leg that comes up the LEFT side.

    ``neighbours`` tells which other bars of the bundle touch the top-left corner
    bar: ``(beside it along the top, below it along the left side, diagonal)``,
    each one ``bar_pitch`` away. A hook end whose normal bend would run into a
    neighbour instead turns the corner sharply, runs flat past two bars, and then
    bends 45 degrees into the core (see the notes above ``BUNDLE_FLAT_HOOK_MIN``).
    """
    x0, y0 = c_left - radius, c_bottom - radius
    x1, y1 = c_right + radius, c_top + radius
    hook_angle = CODE.drawing.hook_angle_degrees
    beside, below, _ = neighbours
    flat_turn = hook_angle - 90.0  # the sharp corner already turned 90 degrees
    corner = (x0, y1)  # sharp corner used by the flat-run ends

    if below:
        # Leg from the right: turns down the left side past two bars, then bends.
        down_end = (x0, c_top - bar_pitch)
        right_hook = _hook_points_dxf(
            down_end, (0.0, -1.0), flat_turn, 1, radius, tail_length
        )
        right_head = [*reversed(right_hook), down_end, corner]
    else:
        # Leg from the right: along the top edge, hooks back around the corner bar.
        right_start = (c_left, y1)
        right_hook = _hook_points_dxf(
            right_start, (-1.0, 0.0), hook_angle, 1, radius, tail_length
        )
        right_head = [*reversed(right_hook), right_start]

    if beside:
        # Leg from the left: turns along the top past two bars, then bends.
        across_end = (c_left + bar_pitch, y1)
        left_hook = _hook_points_dxf(
            across_end, (1.0, 0.0), flat_turn, -1, radius, tail_length
        )
        left_tail = [corner, across_end, *left_hook]
    else:
        # Leg from the left: up the left side, hooks around the corner bar.
        left_end = (x0, c_top)
        left_hook = _hook_points_dxf(
            left_end, (0.0, 1.0), hook_angle, -1, radius, tail_length
        )
        left_tail = [left_end, *left_hook]

    split = (x0, c_bottom)  # where the two legs meet at the lower-left
    right_leg = [
        *right_head,
        (c_right, y1),
        *_arc_points_dxf((c_right, c_top), radius, 90.0, 0.0),
        (x1, c_bottom),
        *_arc_points_dxf((c_right, c_bottom), radius, 0.0, -90.0),
        (c_left, y0),
        *_arc_points_dxf((c_left, c_bottom), radius, -90.0, -180.0),
    ]
    left_leg = [split, *left_tail]
    return [
        TieBar(left_leg, left_level, layer),
        TieBar(right_leg, right_level, layer),
    ]


def _hook_corner_neighbours(site: BarSite, hoop_is_vertical: bool) -> tuple[bool, bool, bool]:
    """Which bundle bars touch the bar a hoop closes around (see ``_rect_hoop_bars``).

    ``site`` is the bar position at the hoop's top-left corner. ``hoop_is_vertical``
    is True for the perimeter hoop and for inner hoops spanning bottom to top.
    """
    if site.count >= 4:
        return True, True, True
    if site.count == 3:
        return True, True, False  # L-shape
    if site.count == 2 and not site.is_corner:
        # Second bar stacked toward the core: below a top-face bar, beside a
        # left-face bar.
        return (False, True, False) if hoop_is_vertical else (True, False, False)
    return False, False, False  # single bar, or a corner pair on the diagonal


def _hoop_tie_bars(
    left: float,
    bottom: float,
    right: float,
    top: float,
    cover: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    scale: float,
    hook_corner_count: int = 1,
) -> list[TieBar]:
    """Describe the perimeter hoop as two stacked legs meeting at the top-left corner.

    Both 135-degree hook ends wrap the same corner bar:
        * left leg  - up the left face and around the corner   (level 0)
        * right leg - top edge, right side, bottom, up to the left face
                      and its hook at the corner                (level 1)

    ``hook_corner_count`` is the number of bars bundled at that corner. With
    three or more the hook ends use the flat run described in ``_rect_hoop_bars``.
    """
    if hook_corner_count >= 4:
        neighbours = (True, True, True)
    elif hook_corner_count == 3:
        neighbours = (True, True, False)
    else:
        neighbours = (False, False, False)
    thickness = tie_bar_diameter * scale
    radius = (main_bar_diameter + tie_bar_diameter) / 2.0 * scale
    tail_length = _hook_tail_length_dxf(tie_bar_diameter) * scale
    inset = cover * scale + thickness / 2.0
    return _rect_hoop_bars(
        left + inset + radius,
        bottom + inset + radius,
        right - inset - radius,
        top - inset - radius,
        radius,
        tail_length,
        main_bar_diameter * scale,
        neighbours,
        LEVEL_HOOP_LEFT_LEG,
        LEVEL_HOOP_RIGHT_LEG,
        "TIES",
    )


def _crosstie_end_points(
    bar: tuple[float, float],
    count: int,
    outward: tuple[float, float],
    toward_tail: tuple[float, float],
    bar_diameter: float,
    bend_radius: float,
    tail_length: float,
) -> list[tuple[float, float]]:
    """Centreline of one crosstie end, from the shaft to the tip of the hook.

    ``outward`` points from the core to the face; ``toward_tail`` points along
    the face to the side the hook curls to (the shaft is on the other side).
    A single bar, or two bars stacked inward, get the normal hook around the bar
    against the face. A bundle of three or four gets a sharp corner, a flat run
    across its two outer bars, then a 45-degree bend toward the core, so the
    extension leaves the bundle diagonally, away from the shaft.
    """
    hook_angle = CODE.drawing.hook_angle_degrees
    if count < BUNDLE_FLAT_HOOK_MIN:
        end = (
            bar[0] - bend_radius * toward_tail[0],
            bar[1] - bend_radius * toward_tail[1],
        )
        normal = (-outward[1], outward[0])
        side = 1 if toward_tail[0] * normal[0] + toward_tail[1] * normal[1] > 0 else -1
        return [
            end,
            *_hook_points_dxf(end, outward, hook_angle, side, bend_radius, tail_length),
        ]

    half = bar_diameter / 2.0
    first = (bar[0] - half * toward_tail[0], bar[1] - half * toward_tail[1])
    second = (bar[0] + half * toward_tail[0], bar[1] + half * toward_tail[1])
    corner = (
        first[0] - bend_radius * toward_tail[0] + bend_radius * outward[0],
        first[1] - bend_radius * toward_tail[1] + bend_radius * outward[1],
    )
    flat_end = (
        second[0] + bend_radius * outward[0],
        second[1] + bend_radius * outward[1],
    )
    normal = (-toward_tail[1], toward_tail[0])
    side = 1 if -(outward[0] * normal[0] + outward[1] * normal[1]) > 0 else -1
    return [
        corner,
        flat_end,
        *_hook_points_dxf(
            flat_end, toward_tail, hook_angle - 90.0, side, bend_radius, tail_length
        ),
    ]


def _crosstie_bars(
    layout: list[tuple[float, float, int]],
    left: float,
    bottom: float,
    scale: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    inner_tie_style: str = "crossties",
) -> list[TieBar]:
    """Describe the interior ties, each with its own stacking level.

    ``inner_tie_style`` is ``"crossties"`` (one tie with a hook at each end per
    pair of opposite bars) or ``"hoops"`` (closed hoops, each enclosing two
    neighbouring bar positions on opposite faces; an unpaired position keeps a
    crosstie). Ties parallel to Y lie below ties parallel to X, and within one
    direction each later tie sits slightly higher than the earlier one.
    """
    sites = {(site.x, site.y): site for site in _rect_bar_sites(layout, inner_tie_style)}
    points = list(sites)
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    vertical_xs, horizontal_ys = _interior_tie_coordinates(points)
    bar_diameter = main_bar_diameter * scale
    bend_radius = (main_bar_diameter + tie_bar_diameter) / 2.0 * scale
    tail_length = _hook_tail_length_dxf(tie_bar_diameter) * scale

    def to_drawing(x: float, y: float) -> tuple[float, float]:
        """Convert ideal section coordinates into drawing coordinates."""
        return left + x * scale, bottom + y * scale

    def outer_shift(site: BarSite) -> float:
        """Distance from a position to the centre of its bar at the hoop corner."""
        return bar_diameter / 2.0 if site.count >= BUNDLE_FLAT_HOOK_MIN else 0.0

    def crosstie(first: BarSite, last: BarSite, axis: tuple[float, float]) -> list:
        """Path of one crosstie from the ``first`` face position to the ``last``."""
        ends = []
        for site, outward in ((first, (-axis[0], -axis[1])), (last, axis)):
            toward_tail = (
                -site.shaft_side * site.along[0],
                -site.shaft_side * site.along[1],
            )
            ends.append(
                _crosstie_end_points(
                    to_drawing(site.x, site.y),
                    site.count,
                    outward,
                    toward_tail,
                    bar_diameter,
                    bend_radius,
                    tail_length,
                )
            )
        return [*reversed(ends[0]), *ends[1]]

    def groups(coordinates: list[float]) -> list[tuple[float, ...]]:
        """Pair neighbouring positions for inner hoops; singles stay crossties."""
        if inner_tie_style != "hoops":
            return [(coordinate,) for coordinate in coordinates]
        paired = [
            tuple(coordinates[index : index + 2]) for index in range(0, len(coordinates), 2)
        ]
        return paired

    bars: list[TieBar] = []
    for base_level, coordinates, vertical in (
        (LEVEL_CROSSTIE_Y, vertical_xs, True),
        (LEVEL_CROSSTIE_X, horizontal_ys, False),
    ):
        tie_groups = groups(coordinates)
        for index, group in enumerate(tie_groups):
            level = base_level + (index + 1) / (len(tie_groups) + 1)
            if len(group) == 1:
                if vertical:
                    first, last = sites[(group[0], y_min)], sites[(group[0], y_max)]
                    path = crosstie(first, last, (0.0, 1.0))
                else:
                    first, last = sites[(x_min, group[0])], sites[(x_max, group[0])]
                    path = crosstie(first, last, (1.0, 0.0))
                bars.append(TieBar(path, level, "CROSSTIES"))
                continue

            low, high = group
            if vertical:
                hook_site = sites[(low, y_max)]
                c_left = to_drawing(low, y_min)[0] - outer_shift(sites[(low, y_min)])
                c_right = to_drawing(high, y_min)[0] + outer_shift(sites[(high, y_min)])
                c_bottom, c_top = to_drawing(low, y_min)[1], to_drawing(low, y_max)[1]
            else:
                hook_site = sites[(x_min, high)]
                c_bottom = to_drawing(x_min, low)[1] - outer_shift(sites[(x_min, low)])
                c_top = to_drawing(x_min, high)[1] + outer_shift(sites[(x_min, high)])
                c_left, c_right = to_drawing(x_min, low)[0], to_drawing(x_max, low)[0]
            bars.extend(
                _rect_hoop_bars(
                    c_left,
                    c_bottom,
                    c_right,
                    c_top,
                    bend_radius,
                    tail_length,
                    bar_diameter,
                    _hook_corner_neighbours(hook_site, vertical),
                    level,
                    level + 0.4 / (len(tie_groups) + 1),
                    "CROSSTIES",
                )
            )
    return bars


def _visible_tie_shapes(
    bars: list[TieBar], bar_thickness: float
) -> list[tuple[TieBar, object]]:
    """Return each bar's outline shape with the parts hidden under higher bars removed."""
    full_shapes = [
        LineString(bar.path).buffer(
            bar_thickness / 2.0, cap_style="flat", join_style="round"
        )
        for bar in bars
    ]
    # Shrink the cutting shapes a hair so bars that merely touch end-to-end
    # (e.g. hoop body and its last-laid end) are not sliced by rounding noise.
    tolerance = bar_thickness * 1e-3
    visible = []
    for bar, shape in zip(bars, full_shapes):
        higher = [
            other.buffer(-tolerance)
            for other_bar, other in zip(bars, full_shapes)
            if other_bar.level > bar.level
        ]
        visible.append((bar, shape.difference(unary_union(higher)) if higher else shape))
    return visible


DXF_OUTLINE_TOLERANCE = 0.05   # mm: a tie outline is drawn within this of its true shape


def _draw_tie_bars(modelspace, bars: list[TieBar], bar_thickness: float) -> None:
    """Draw the visible outline of every tie bar as closed polylines."""
    for bar, shape in _visible_tie_shapes(bars, bar_thickness):
        for polygon in getattr(shape, "geoms", [shape]):
            if polygon.geom_type != "Polygon" or polygon.is_empty:
                continue
            # The outline of a bent bar has hundreds of points, most of them on straight
            # runs or closer together than a plotter can show: points that move the line
            # by less than DXF_OUTLINE_TOLERANCE are left out, and the rest are written
            # to a hundredth of a millimetre. A schedule of 500 columns was 44 MB.
            polygon = polygon.simplify(DXF_OUTLINE_TOLERANCE, preserve_topology=True)
            if polygon.is_empty or polygon.geom_type != "Polygon":
                continue
            for ring in (polygon.exterior, *polygon.interiors):
                modelspace.add_lwpolyline(
                    [(round(x, 2), round(y, 2)) for x, y in list(ring.coords)[:-1]],
                    close=True,
                    dxfattribs={"layer": bar.layer, "lineweight": 18},
                )


def _draw_column_section(
    modelspace,
    row: pd.Series,
    x_center: float,
    y_center: float,
    drawing_width: float,
    drawing_height: float,
    main_bar_diameter: float,
    tie_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str = "crossties",
) -> None:
    """Draw a scaled column section, bar bundles, hoops, inner ties, and hooks."""
    diameter = (
        float(row["Diameter (mm)"])
        if pd.notna(row["Diameter (mm)"])
        else 0.0
    )
    is_circular = diameter > 0
    width = diameter if is_circular else float(row["Width (mm)"])
    height = diameter if is_circular else float(row["Depth (mm)"])
    scale = min(drawing_width / width, drawing_height / height)
    scaled_width, scaled_height = width * scale, height * scale
    left, bottom = x_center - scaled_width / 2.0, y_center - scaled_height / 2.0
    right, top = left + scaled_width, bottom + scaled_height
    tie_offset = cover * scale
    tie_thickness = tie_bar_diameter * scale
    bar_radius = main_bar_diameter * scale / 2.0

    if is_circular:
        modelspace.add_circle(
            (x_center, y_center),
            diameter * scale / 2.0,
            dxfattribs={"layer": "CONCRETE", "lineweight": 25},
        )
    else:
        modelspace.add_lwpolyline(
            [(left, bottom), (right, bottom), (right, top), (left, top)],
            close=True,
            dxfattribs={"layer": "CONCRETE", "lineweight": 25},
        )
    layout = _column_layout_from_report(
        row, main_bar_diameter, tie_bar_diameter, cover, is_smrf
    )

    if is_circular:
        center = (x_center, y_center)
        modelspace.add_circle(
            center,
            diameter * scale / 2.0 - tie_offset,
            dxfattribs={"layer": "TIES", "lineweight": 18},
        )
        modelspace.add_circle(
            center,
            diameter * scale / 2.0 - tie_offset - tie_thickness,
            dxfattribs={"layer": "TIES", "lineweight": 18},
        )
        sites = _circular_bar_sites(layout)
        origin = center
    else:
        sites = _rect_bar_sites(layout, inner_tie_style)
        origin = (left, bottom)
        hook_corner = min(
            (site for site in sites if site.is_corner),
            key=lambda site: (site.x, -site.y),  # top-left corner, where the hoop closes
        )
        # Hoop and inner ties are drawn together so the stacking order
        # (inner ties over hoop, x-ties over y-ties, last hoop end on top)
        # is resolved across ALL bars at once.
        tie_bars = _hoop_tie_bars(
            left,
            bottom,
            right,
            top,
            cover,
            main_bar_diameter,
            tie_bar_diameter,
            scale,
            hook_corner_count=hook_corner.count,
        ) + _crosstie_bars(
            layout,
            left,
            bottom,
            scale,
            main_bar_diameter,
            tie_bar_diameter,
            inner_tie_style,
        )
        _draw_tie_bars(modelspace, tie_bars, tie_thickness)

    for site in sites:
        for offset_x, offset_y in _bundle_bar_offsets(site, main_bar_diameter):
            modelspace.add_circle(
                (
                    origin[0] + (site.x + offset_x) * scale,
                    origin[1] + (site.y + offset_y) * scale,
                ),
                bar_radius,
                dxfattribs={"layer": "LONGITUDINAL", "lineweight": 18},
            )


def generate_dxf_column_schedule(
    report: pd.DataFrame,
    output_filepath: str,
    main_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str = "crossties",
    story_order: list[str] | None = None,
) -> None:
    """Create one grouped column schedule DXF for the supplied floor report.

    ``inner_tie_style`` is ``"crossties"`` or ``"hoops"`` (closed inner hoops).
    ``story_order`` lists the stories from the bottom up; without it the order
    is guessed from the story names.
    """
    if report.empty:
        raise ValueError("Column design report has no rows to export.")

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    for layer_name, color in (
        ("TABLE", 7),
        ("CONCRETE", 7),
        ("TIES", 7),
        ("CROSSTIES", 7),
        ("HOOKS", 7),
        ("LONGITUDINAL", 7),
    ):
        if layer_name not in doc.layers:
            doc.layers.new(layer_name, dxfattribs={"color": color})
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})
    modelspace = doc.modelspace()

    # Each ETABS column label is a vertical group. Its members are stacked from
    # the highest available floor to the lowest, rather than regrouping by floor.
    grouped_labels = []
    report = report.copy()
    report["_DXF Mark"] = report["Unique Name"].map(_common_column_mark)
    known = [str(story) for story in (story_order or [])]
    story_names = report["Story"].dropna().astype(str).unique()
    full_order = known + sorted(
        (story for story in story_names if story not in known), key=_column_story_sort_key
    )
    story_rank = {story: index for index, story in enumerate(full_order)}
    for label, members in report.groupby("_DXF Mark", sort=False):
        elevation_column = next(
            (
                column
                for column in ("Level Elevation (m)", "Level_Elevation_m")
                if column in members.columns
            ),
            None,
        )
        if elevation_column is None:
            members = members.assign(
                _elevation=members["Story"].map(lambda value: story_rank.get(str(value), -1))
            )
        else:
            members = members.assign(
                _elevation=pd.to_numeric(
                    members[elevation_column], errors="coerce"
                )
            )
        grouped_labels.append(
            (
                label,
                members.sort_values(
                    ["_elevation", "Story", "Unique Name"],
                    ascending=[False, False, True],
                    kind="stable",
                ).drop_duplicates("Unique Name"),
            )
        )
    # Proportions of the office column schedule: F'c, floor level and mark
    # columns, then one detail column per mark.
    left_widths = [30.0, 50.0, 59.0]
    detail_width = 90.0
    header_height = 8.0
    drawing_height = 88.0
    data_height = 7.0
    text_height = 3.5  # headers, row names and values
    level_text_height = 4.5  # F'c and floor level
    diameter_sign = "%%C"  # drawn as the diameter symbol in CAD
    field_names = (
        "SIZE",
        "VERTICAL BARS",
        "JOINT REIN.",
        "CONFINMT",
        "TIES",
    )

    def add_text(text: object, x: float, y: float, height: float, width: float) -> None:
        """Place centered single-line text in a DXF schedule cell."""
        entity = modelspace.add_text(
            str(text),
            dxfattribs={
                "style": "SIMPLEX",
                "height": height,
                "width": 1.0,
                "layer": "TABLE",
            },
        )
        entity.set_placement(
            (x + width / 2.0, y),
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )

    def add_cell(x1: float, y1: float, x2: float, y2: float) -> None:
        """Draw a single schedule-cell boundary."""
        modelspace.add_lwpolyline(
            [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
            close=True,
            dxfattribs={"layer": "TABLE", "lineweight": 18},
        )

    # Arrange the schedule by story so the level properties are written once
    # and shared by every column mark on that level.
    story_values = sorted(story_names, key=story_rank.get, reverse=True)
    labels = list(dict.fromkeys(report["_DXF Mark"].astype(str)))
    story_content_height = drawing_height + len(field_names) * data_height
    story_x_positions = [0.0]
    for width in left_widths:
        story_x_positions.append(story_x_positions[-1] + width)
    for _ in labels:
        story_x_positions.append(story_x_positions[-1] + detail_width)

    y_top = 0.0
    for story_index, story in enumerate(story_values):
        has_header = story_index == 0
        story_header_height = header_height if has_header else 0.0
        story_block_height = story_header_height + story_content_height
        story_rows = report.loc[report["Story"].astype(str).eq(story)]
        by_label = {
            str(row["_DXF Mark"]): row
            for _, row in story_rows.drop_duplicates("_DXF Mark").iterrows()
        }
        block_bottom = y_top - story_block_height
        fc_values = pd.to_numeric(story_rows["f′c (MPa)"], errors="coerce").dropna().unique()
        fc_value = f"{float(fc_values[0]):g}" if len(fc_values) else "-"

        for column_index, title in enumerate(("F'c (MPa)", "FLOOR LEVEL")):
            add_cell(
                story_x_positions[column_index],
                y_top,
                story_x_positions[column_index + 1],
                y_top - story_header_height,
            )
            add_cell(
                story_x_positions[column_index],
                y_top - story_header_height,
                story_x_positions[column_index + 1],
                block_bottom,
            )
            if has_header:
                add_text(
                    title,
                    story_x_positions[column_index],
                    y_top - story_header_height / 2.0,
                    text_height,
                    left_widths[column_index],
                )
        add_cell(
            story_x_positions[2],
            y_top,
            story_x_positions[3],
            y_top - story_header_height,
        )
        add_cell(
            story_x_positions[2],
            y_top - story_header_height,
            story_x_positions[3],
            block_bottom,
        )
        if has_header:
            add_text(
                "MARK",
                story_x_positions[2],
                y_top - story_header_height / 2.0,
                text_height,
                left_widths[2],
            )
        level_y = y_top - story_header_height - story_content_height / 2.0
        add_text(fc_value, story_x_positions[0], level_y, level_text_height, left_widths[0])
        # A column runs from the level below to its own story: "3F TO PD1".
        rank = story_rank[story]
        below = full_order[rank - 1] if rank > 0 else CODE.drawing.column_schedule_base_label
        level_range = f"{below} TO {story}"
        add_text(
            level_range,
            story_x_positions[1],
            level_y,
            # long story names are written smaller so they stay inside the cell
            min(level_text_height, left_widths[1] / (0.95 * len(level_range))),
            left_widths[1],
        )

        for field_index, field_name in enumerate(field_names):
            row_top = (
                y_top
                - story_header_height
                - drawing_height
                - field_index * data_height
            )
            row_bottom = row_top - data_height
            add_cell(story_x_positions[2], row_top, story_x_positions[3], row_bottom)
            add_text(
                field_name,
                story_x_positions[2],
                row_top - data_height / 2.0,
                text_height,
                left_widths[2],
            )

        for label_index, label in enumerate(labels):
            row = by_label.get(label)
            x_left = story_x_positions[3 + label_index]
            x_right = story_x_positions[4 + label_index]
            add_cell(x_left, y_top, x_right, y_top - story_header_height)
            if has_header:
                add_text(
                    label,
                    x_left,
                    y_top - story_header_height / 2.0,
                    text_height,
                    detail_width,
                )
            add_cell(
                x_left,
                y_top - story_header_height,
                x_right,
                y_top - story_header_height - drawing_height,
            )
            if row is not None:
                _draw_column_section(
                    modelspace,
                    row,
                    (x_left + x_right) / 2.0,
                    y_top - story_header_height - drawing_height / 2.0,
                    detail_width * 0.78,
                    drawing_height * 0.82,
                    main_bar_diameter,
                    float(row["Tie Bar Diameter (mm)"]),
                    # a column designed with its own cover is drawn with it
                    float(row["Concrete Cover (mm)"])
                    if pd.notna(row.get("Concrete Cover (mm)"))
                    else cover,
                    is_smrf,
                    inner_tie_style,
                )
            else:
                add_text(
                    "-",
                    x_left,
                    y_top - story_header_height - drawing_height / 2.0,
                    level_text_height,
                    detail_width,
                )

            for field_index, field_name in enumerate(field_names):
                row_top = (
                    y_top
                    - story_header_height
                    - drawing_height
                    - field_index * data_height
                )
                row_bottom = row_top - data_height
                add_cell(x_left, row_top, x_right, row_bottom)
                if row is None:
                    value = "-"
                else:
                    tie = f"{float(row['Tie Bar Diameter (mm)']):g}mm{diameter_sign}"
                    if field_name == "SIZE":
                        diameter = float(row["Diameter (mm)"] or 0.0)
                        value = (
                            f"{diameter:.0f}mm{diameter_sign}"
                            if diameter > 0
                            else f"{float(row['Width (mm)']):.0f}X{float(row['Depth (mm)']):.0f}"
                        )
                    elif field_name == "VERTICAL BARS":
                        value = (
                            f"{int(float(row['Longitudinal Bars']))}-"
                            f"{main_bar_diameter:g}mm{diameter_sign}"
                        )
                    elif field_name == "JOINT REIN.":
                        value = f"{tie} @ {CODE.drawing.column_joint_tie_spacing:g}mm"
                    elif field_name == "CONFINMT":
                        # The standard spacing, unless the design needs a closer one.
                        designed = pd.to_numeric(
                            row.get("Tie / Spiral Spacing (mm)"), errors="coerce"
                        )
                        spacing = CODE.drawing.column_confinement_tie_spacing
                        if pd.notna(designed) and designed < spacing:
                            spacing = float(designed)
                        value = f"{tie} @ {spacing:g}mm"
                    else:
                        value = f"{tie} @ {CODE.drawing.column_general_tie_spacing:g}mm"
                add_text(value, x_left, row_top - data_height / 2.0, text_height, detail_width)
        y_top = block_bottom

    doc.saveas(output_filepath)
    return


INNER_TIE_STYLE_LABELS = {
    "Crossties (one tie with a hook at each end)": "crossties",
    "Closed inner hoops (each enclosing two bar positions)": "hoops",
}


def ask_inner_tie_style() -> str | None:
    """Ask whether interior ties are drawn as crossties or closed inner hoops."""
    from utilities._gui_helpers import select_option

    chosen = select_option(
        "Column Schedule - Interior Ties",
        "How should the interior ties be drawn?",
        list(INNER_TIE_STYLE_LABELS),
    )
    return None if chosen is None else INNER_TIE_STYLE_LABELS[chosen]


def export_column_cad_drawings(
    report: pd.DataFrame,
    output_directory: str,
    main_bar_diameter: float,
    cover: float,
    is_smrf: bool,
    inner_tie_style: str,
    connectivity: pd.DataFrame,
) -> list[str]:
    """Export one stacked all-story column schedule DXF from the design report."""
    destination = output_directory
    if not os.path.isdir(destination):
        raise NotADirectoryError(f"DXF output directory does not exist: {destination}")
    if inner_tie_style not in INNER_TIE_STYLES:
        raise ValueError(f"inner_tie_style must be one of {INNER_TIE_STYLES}.")

    report = column_report_display(report)
    if not report.empty:
        report = report.drop_duplicates(subset=["Unique Name"], keep="first")
    if report.empty:
        raise ValueError("There are no designed columns to export.")
    os.makedirs(destination, exist_ok=True)
    output_path = os.path.join(destination, "Column_Schedule.dxf")
    generate_dxf_column_schedule(
        report,
        output_path,
        main_bar_diameter,
        cover,
        is_smrf,
        inner_tie_style,
        story_order=_story_order_from_stacks(
            _clean_table(connectivity),
            dict(zip(report["Unique Name"].astype(str), report["Story"].astype(str))),
        ),
    )
    return [output_path]
