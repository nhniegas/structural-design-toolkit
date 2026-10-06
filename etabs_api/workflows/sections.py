"""Frame section sizes for the design loop: the next size up or down.

Section names follow model setup: ``<family>_<width>X<depth>_<concrete>_<rebar>``
(G, B and FTB beams, CR rectangular columns) and ``C_<diameter>_...``
(circular columns). Width is t2 and depth t3; a column's depth runs along
its local 2 axis.

Sizes come from the setup ranges ``[minimum, maximum, step]`` of each family.
Past the range a size grows by ``increment`` up to the maximum the user
gives; a range that goes beyond that maximum is cut at it. Beams keep
depth >= width and width / depth >= 0.3; rectangular columns keep the longer
side at most ``column_max_ratio`` times the shorter one (2 by default). The
concrete and rebar of a section never change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

BEAM_FAMILIES = ("G", "B", "FTB")
BEAM_MIN_RATIO = 0.3
COLUMN_MIN_RATIO = 0.5
_RECT = re.compile(r"^(G|B|FTB|CR|C)_(\d+)X(\d+)_(\w+?)_(\w+)$", re.IGNORECASE)
_CIRCLE = re.compile(r"^(C)_(\d+)_(\w+?)_(\w+)$", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    """A section of the setup naming."""

    family: str
    width: int
    depth: int
    concrete: str
    rebar: str
    circular: bool = False

    @property
    def name(self) -> str:
        if self.circular:
            return f"C_{self.depth}_{self.concrete}_{self.rebar}"
        family = "CR" if self.family == "C" else self.family  # legacy rectangular C_
        return f"{family}_{self.width}X{self.depth}_{self.concrete}_{self.rebar}"

    @property
    def is_beam(self) -> bool:
        return self.family in BEAM_FAMILIES

    @property
    def area(self) -> float:
        return math.pi * self.depth**2 / 4 if self.circular else self.width * self.depth


def parse_section(name: str) -> Section | None:
    """The section of a setup name, or None for any other name."""
    text = str(name).strip()
    match = _RECT.match(text)
    if match:
        family, width, depth, concrete, rebar = match.groups()
        family = family.upper()
        return Section("CR" if family == "C" else family, int(width), int(depth),
                       concrete.upper(), rebar.upper())
    match = _CIRCLE.match(text)
    if match:
        _, diameter, concrete, rebar = match.groups()
        d = int(diameter)
        return Section("C", d, d, concrete.upper(), rebar.upper(), circular=True)
    return None


@dataclass
class Limits:
    """How far sizes may grow past the setup ranges."""

    increment: int = 50
    beam_max_width: int = 800
    beam_max_depth: int = 1200
    column_max: int = 1200
    column_max_ratio: float = 1.0 / COLUMN_MIN_RATIO  # longer side / shorter side
    beam_min_ratio: float = BEAM_MIN_RATIO  # beam width / depth when a beam is resized


def _sizes(span) -> list[int]:
    if not span:
        return []
    low, high, step = (int(round(float(v))) for v in span)
    return list(range(low, high + 1, step)) if step > 0 and high >= low else []


def _extended(sizes: list[int], increment: int, maximum: int, keep: tuple = ()) -> list[int]:
    """The range up to ``maximum``, then on by ``increment`` up to ``maximum``.

    ``maximum`` is the user's limit: sizes of the range above it are left out.
    ``keep`` are the sizes the member has now; they stay even above the limit,
    so a member that is already larger is not forced to another size.
    """
    out = sorted({size for size in sizes if size <= maximum})
    last = out[-1] if out else 0
    while out and last + increment <= maximum:
        last += increment
        out.append(last)
    return sorted(set(out) | set(keep))


def has_range(ranges: dict, family: str) -> bool:
    spans = ranges.get(family, {})
    if family in BEAM_FAMILIES:
        return bool(spans.get("width")) and bool(spans.get("depth"))
    if family == "CR":
        return bool(spans.get("size"))
    return bool(spans.get("diameter"))


def _beam_ok(width: int, depth: int, min_ratio: float = BEAM_MIN_RATIO) -> bool:
    return depth >= width and width / depth >= max(min_ratio, BEAM_MIN_RATIO) - 1e-9


def _column_ok(width: int, depth: int, max_ratio: float | None = None) -> bool:
    """Whether the side ratio is allowed: longer / shorter at most ``max_ratio``."""
    limit = 1.0 / COLUMN_MIN_RATIO if max_ratio is None else max(float(max_ratio), 1.0)
    return max(width, depth) / min(width, depth) <= limit + 1e-9


# =============================================================================
# BEAMS
# =============================================================================
def grow_beam(section: Section, mode: str, ranges: dict, limits: Limits) -> Section | None:
    """The next beam size: ``mode`` "depth" (flexure) or "width" (shear, torsion).

    When one way is used up the other is tried. None when no size is left.
    """
    spans = ranges.get(section.family, {})
    widths = _extended(_sizes(spans.get("width")) or [section.width], limits.increment,
                       limits.beam_max_width, (section.width,))
    depths = _extended(_sizes(spans.get("depth")) or [section.depth], limits.increment,
                       limits.beam_max_depth, (section.depth,))
    w, d = section.width, section.depth
    ratio = limits.beam_min_ratio

    def deeper():
        # the next depth that keeps this width; where the width is then too small
        # for the depth, the smallest width that is not
        for depth in (x for x in depths if x > d):
            if _beam_ok(w, depth, ratio):
                return replace(section, depth=depth)
            for width in (x for x in widths if x > w):
                if _beam_ok(width, depth, ratio):
                    return replace(section, width=width, depth=depth)
        return None

    def wider():
        for width in (x for x in widths if x > w):
            if _beam_ok(width, d, ratio):
                return replace(section, width=width)
            for depth in (x for x in depths if x > d):  # deepen to keep depth >= width
                if _beam_ok(width, depth, ratio):
                    return replace(section, width=width, depth=depth)
        return None

    first, second = (deeper, wider) if mode == "depth" else (wider, deeper)
    return first() or second()


def shrink_beam(section: Section, ranges: dict, min_depth: float = 0.0,
                min_ratio: float = BEAM_MIN_RATIO) -> Section | None:
    """One size smaller: the next smaller depth, else the next smaller width.

    Never below the smallest size of the range nor below ``min_depth``, and
    the width stays at least ``min_ratio`` of the depth.
    """
    spans = ranges.get(section.family, {})
    widths = sorted(set(_sizes(spans.get("width")) + [section.width]))
    depths = sorted(set(_sizes(spans.get("depth")) + [section.depth]))
    w, d = section.width, section.depth
    for depth in sorted((x for x in depths if x < d), reverse=True):
        if depth >= min_depth and _beam_ok(w, depth, min_ratio):
            return replace(section, depth=depth)
    for width in sorted((x for x in widths if x < w), reverse=True):
        if _beam_ok(width, d, min_ratio):
            return replace(section, width=width)
    return None


# =============================================================================
# COLUMNS
# =============================================================================
def _column_sides(ranges: dict, section: Section, limits: Limits) -> list[int]:
    key = "diameter" if section.circular else "size"
    family = "C" if section.circular else "CR"
    sides = _sizes(ranges.get(family, {}).get(key)) or [section.depth]
    return _extended(sides, limits.increment, limits.column_max,
                     (section.width, section.depth))


def grow_column(section: Section, mode: str, ranges: dict, limits: Limits,
                along_depth: bool | None = None) -> Section | None:
    """The next column size.

    ``mode`` "square": both sides one size up (400 x 400 -> 500 x 500,
    400 x 600 -> 500 x 700). "side": grow the side that is ``along_depth``
    (True: depth, False: width), keeping the side ratio. When neither fits,
    the smallest larger size with both sides at least the current ones.
    No side goes above ``limits.column_max``.
    """
    sides = _column_sides(ranges, section, limits)
    ratio = limits.column_max_ratio

    def _column_ok(width: int, depth: int) -> bool:  # with the user's side ratio
        return globals()["_column_ok"](width, depth, ratio)

    if section.circular:
        bigger = [s for s in sides if s > section.depth]
        return replace(section, width=bigger[0], depth=bigger[0]) if bigger else None
    w, d = section.width, section.depth
    if mode == "side" and along_depth is not None:
        for side in (s for s in sides if s > (d if along_depth else w)):
            width, depth = (w, side) if along_depth else (side, d)
            if _column_ok(width, depth):
                return replace(section, width=width, depth=depth)
            # the other side must grow too to keep the ratio
            for other in (s for s in sides if s > (w if along_depth else d)):
                width, depth = (other, side) if along_depth else (side, other)
                if _column_ok(width, depth):
                    return replace(section, width=width, depth=depth)
    if mode == "square":
        next_w = next((s for s in sides if s > w), None)
        next_d = next((s for s in sides if s > d), None)
        if next_w and next_d and _column_ok(next_w, next_d):
            return replace(section, width=next_w, depth=next_d)
    options = [(a, b) for a in sides for b in sides
               if a >= w and b >= d and (a, b) != (w, d) and _column_ok(a, b)]
    if not options:
        return None
    width, depth = min(options, key=lambda s: (s[0] * s[1], abs(s[0] - s[1])))
    return replace(section, width=width, depth=depth)


def shrink_column(section: Section, ranges: dict, min_side: float = 0.0,
                  max_ratio: float | None = None) -> Section | None:
    """One size smaller: the largest smaller size with both sides at most the current ones.

    No side goes below ``min_side`` (what the joints need, see the design
    loop) nor below the smallest size of the range.
    """
    family = "C" if section.circular else "CR"
    key = "diameter" if section.circular else "size"
    sides = sorted(set(_sizes(ranges.get(family, {}).get(key)) + [section.width, section.depth]))
    sides = [s for s in sides if s >= min_side]
    if section.circular:
        smaller = [s for s in sides if s < section.depth]
        return replace(section, width=smaller[-1], depth=smaller[-1]) if smaller else None
    w, d = section.width, section.depth
    options = [(a, b) for a in sides for b in sides
               if a <= w and b <= d and (a, b) != (w, d) and _column_ok(a, b, max_ratio)]
    if not options:
        return None
    width, depth = max(options, key=lambda s: (s[0] * s[1], -abs(s[0] - s[1])))
    return replace(section, width=width, depth=depth)


def depth_along_x(angle_degrees: float) -> bool | None:
    """Whether a column's depth (local 2) runs along global X (True) or Y (False).

    None when the column is not within 25 degrees of either axis.
    """
    c = abs(math.cos(math.radians(angle_degrees)))
    if c >= math.cos(math.radians(25)):
        return True
    if c <= math.sin(math.radians(25)):
        return False
    return None


# =============================================================================
# ETABS
# =============================================================================
def ensure_section(model, section: Section, existing: set[str],
                   materials: dict[str, str] | None = None, cover: float | None = None) -> bool:
    """Create the section in ETABS when it is missing (no stiffness modifiers,
    setup rebar data).

    ``materials`` maps the grade tags of the name (``C04``, ``G60``) to the
    model's own material names, for a model whose materials are named
    otherwise; ``cover`` is the cover of the section it replaces.
    """
    if section.name in existing:
        return True
    from etabs_api.workflows.model_setup import SetupLog, create_section

    materials = materials or {}
    kind = "circle" if section.circular else ("beam" if section.is_beam else "column")
    created = create_section(model.PropFrame, {
        "name": section.name, "kind": kind, "width": section.width, "depth": section.depth,
        "material": materials.get(section.concrete, section.concrete),
        "rebar": materials.get(section.rebar, section.rebar), "cover": cover}, SetupLog())
    if created:
        existing.add(section.name)
    return created


def assign_section(model, member: str, section: Section) -> bool:
    result = model.FrameObj.SetSection(member, section.name, 0)
    return (result[-1] if isinstance(result, (list, tuple)) else result) == 0
