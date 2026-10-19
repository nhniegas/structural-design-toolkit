"""
Wall piers: the walls of the model as straight legs, and the legs that meet as
one wall (planar, L, T, U or core), for the wall design.

* A **leg** is a straight run of wall panels in one plane, on one story,
  touching end to end. A leg keeps one pier label on every story where a leg
  of the same plan line overlaps it, so its forces can be read story by story.
* A **wall** is a set of legs whose ends meet in plan (within ``TOUCH``). Its
  legs are designed together as one assembled section (NSCP 418.10.5.1:
  flanges are effective), with the shear of each leg checked on its own.

Labels are ``W<number><letter>``: the wall number in plan order (top to
bottom, then left to right, as the columns of ``sdt tag``), and one letter per
leg (A, B, ...). A planar wall has one leg, ``W3A``.

The planning works on plain data; ``read_wall_panels`` and ``apply_pier_labels``
are the ETABS part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from etabs_api.core.helpers import as_list, return_code

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # I and O are left out, as in sdt tag
ALIGN = 50.0        # mm: panels this close to one line are in one plane
TOUCH = 400.0       # mm: legs whose ends are this close meet (a wall thickness or so)
OVERLAP = 0.5       # share of a leg's length another story's leg must overlap to share its label


@dataclass(frozen=True)
class Panel:
    """A wall panel: its unique name, story, the two ends of its base line in
    plan (mm), its thickness (mm) and its section."""

    name: str
    story: str
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float = 0.0
    section: str = ""


@dataclass
class Leg:
    story: str
    panels: list[Panel]
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float
    label: str = ""

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def angle(self) -> float:
        """Direction in plan, 0 to 180 degrees from global X."""
        return math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1)) % 180.0


@dataclass
class Wall:
    number: int
    legs: dict[str, list[Leg]] = field(default_factory=dict)   # story -> its legs

    @property
    def shape(self) -> str:
        count = max((len(legs) for legs in self.legs.values()), default=0)
        return {1: "planar", 2: "L or T", 3: "U, T or C"}.get(count, "core")


# =============================================================================
# PLANNING (no ETABS)
# =============================================================================
def _direction(p: Panel) -> tuple[float, float]:
    length = math.hypot(p.x2 - p.x1, p.y2 - p.y1) or 1.0
    return (p.x2 - p.x1) / length, (p.y2 - p.y1) / length


def _distance_to_line(x: float, y: float, a: tuple[float, float], u: tuple[float, float]) -> float:
    return abs((x - a[0]) * u[1] - (y - a[1]) * u[0])


def legs_of_story(panels: list[Panel]) -> list[Leg]:
    """The straight legs of one story: panels in one plane that touch end to end."""
    left = sorted(panels, key=lambda p: (round(p.y1), round(p.x1)))
    legs: list[Leg] = []
    while left:
        first = left.pop(0)
        u = _direction(first)
        group = [first]
        grown = True
        while grown:
            grown = False
            for p in list(left):
                if abs(abs(u[0] * _direction(p)[0] + u[1] * _direction(p)[1]) - 1.0) > 1e-3:
                    continue
                if max(_distance_to_line(p.x1, p.y1, (first.x1, first.y1), u),
                       _distance_to_line(p.x2, p.y2, (first.x1, first.y1), u)) > ALIGN:
                    continue
                ends = [(q.x1, q.y1) for q in group] + [(q.x2, q.y2) for q in group]
                if any(math.hypot(p.x1 - x, p.y1 - y) <= ALIGN or
                       math.hypot(p.x2 - x, p.y2 - y) <= ALIGN for x, y in ends):
                    group.append(p)
                    left.remove(p)
                    grown = True
        # the two extreme points along the line
        a = (first.x1, first.y1)
        points = [(q.x1, q.y1) for q in group] + [(q.x2, q.y2) for q in group]
        along = sorted(points, key=lambda pt: (pt[0] - a[0]) * u[0] + (pt[1] - a[1]) * u[1])
        (x1, y1), (x2, y2) = along[0], along[-1]
        if (x2, y2) < (x1, y1):
            (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
        legs.append(Leg(first.story, group, x1, y1, x2, y2,
                        max(q.thickness for q in group)))
    return legs


def _touch(a: Leg, b: Leg) -> bool:
    """Whether two legs meet: an end of one within TOUCH of the other leg."""
    def near(x, y, leg):
        u = ((leg.x2 - leg.x1) / (leg.length or 1.0), (leg.y2 - leg.y1) / (leg.length or 1.0))
        t = (x - leg.x1) * u[0] + (y - leg.y1) * u[1]
        if t < -TOUCH or t > leg.length + TOUCH:
            return False
        return _distance_to_line(x, y, (leg.x1, leg.y1), u) <= TOUCH
    return any(near(x, y, b) for x, y in ((a.x1, a.y1), (a.x2, a.y2))) or \
        any(near(x, y, a) for x, y in ((b.x1, b.y1), (b.x2, b.y2)))


def _overlap(a: Leg, b: Leg) -> float:
    """The share of the shorter of two parallel legs that overlaps the other in plan."""
    if abs(((a.angle - b.angle) + 90.0) % 180.0 - 90.0) > 1.0:
        return 0.0
    u = ((a.x2 - a.x1) / (a.length or 1.0), (a.y2 - a.y1) / (a.length or 1.0))
    if _distance_to_line(b.x1, b.y1, (a.x1, a.y1), u) > TOUCH:
        return 0.0
    ta = sorted([0.0, a.length])
    tb = sorted([(b.x1 - a.x1) * u[0] + (b.y1 - a.y1) * u[1],
                 (b.x2 - a.x1) * u[0] + (b.y2 - a.y1) * u[1]])
    common = min(ta[1], tb[1]) - max(ta[0], tb[0])
    return max(common, 0.0) / (min(a.length, b.length) or 1.0)


def plan_walls(panels: list[Panel], story_order: list[str]) -> list[Wall]:
    """Group the panels into legs and walls, and label every leg.

    ``story_order`` lists the stories from the bottom up. Legs of the same plan
    line on different stories (overlapping by at least OVERLAP) take one label.
    """
    by_story: dict[str, list[Leg]] = {}
    for story in story_order:
        own = [p for p in panels if p.story == story]
        if own:
            by_story[story] = legs_of_story(own)
    # a stack: the legs of one plan line on every story, found from any story
    stacks: list[list[Leg]] = []
    for story in story_order:
        for leg in by_story.get(story, []):
            home = next((s for s in stacks if any(_overlap(leg, o) >= OVERLAP for o in s)), None)
            if home is None:
                stacks.append([leg])
            else:
                home.append(leg)
    # walls: stacks whose legs meet on some story
    groups: list[list[int]] = []
    for i, stack in enumerate(stacks):
        joined = [g for g in groups if any(
            any(_touch(a, b) for a in stack for b in stacks[j] if a.story == b.story)
            for j in g)]
        merged = [i] + [j for g in joined for j in g]
        groups = [g for g in groups if g not in joined] + [merged]
    # plan order: top to bottom, then left to right, by the first leg's middle
    def key(group):
        legs = [leg for j in group for leg in stacks[j]]
        mx = sum((leg.x1 + leg.x2) / 2 for leg in legs) / len(legs)
        my = sum((leg.y1 + leg.y2) / 2 for leg in legs) / len(legs)
        return (-round(my, -2), round(mx, -2))

    walls = []
    for number, group in enumerate(sorted(groups, key=key), start=1):
        wall = Wall(number)
        ordered = sorted(group, key=lambda j: (round(stacks[j][0].angle), stacks[j][0].y1,
                                               stacks[j][0].x1))
        for letter, j in zip(LETTERS, ordered):
            for leg in stacks[j]:
                leg.label = f"W{number}{letter}"
                wall.legs.setdefault(leg.story, []).append(leg)
        walls.append(wall)
    return walls


# =============================================================================
# ETABS
# =============================================================================
def read_wall_panels(connector) -> tuple[list[Panel], list[str]]:
    """The wall panels of the open model (in N-mm) and the story names, bottom first."""
    import pandas as pd

    model = connector.sap_model
    try:
        walls = connector._read_database_table("Wall Object Connectivity")
    except RuntimeError:     # a model with no walls has no such table
        walls = pd.DataFrame()
    points = connector._read_database_table("Point Object Connectivity")
    xy = {str(r["UniqueName"]): (float(r["X"]), float(r["Y"]), float(r["Z"]))
          for _, r in points.iterrows()}
    panels = []
    for _, r in walls.iterrows():
        corners = [xy.get(str(r[c])) for c in ("UniquePt1", "UniquePt2", "UniquePt3",
                                                "UniquePt4") if c in r and str(r[c]) in xy]
        if len(corners) < 2:
            continue
        low = min(z for _, _, z in corners)
        base = [(x, y) for x, y, z in corners if abs(z - low) < 1.0]
        if len(base) < 2:
            continue
        (x1, y1), (x2, y2) = base[0], base[-1]
        name = str(r["UniqueName"])
        section = str(model.AreaObj.GetProperty(name, "")[0])
        thickness = _wall_thickness(model, section)
        panels.append(Panel(name, str(r["Story"]), x1, y1, x2, y2, thickness, section))
    stories = [str(n) for n in as_list(model.Story.GetStories()[1])][1:]
    return panels, stories


def _wall_thickness(model, section: str) -> float:
    try:
        got = model.PropArea.GetWall(section, 0, 0, "", 0.0, 0, "", "")
        return float(got[3])   # (type, shell, material, thickness, colour, notes, GUID, code)
    except Exception:
        return 0.0


def apply_pier_labels(model, walls: list[Wall]) -> int:
    """Give every panel the pier label of its leg. Returns the panels labelled."""
    done = 0
    for wall in walls:
        for legs in wall.legs.values():
            for leg in legs:
                model.PierLabel.SetPier(leg.label)
                for panel in leg.panels:
                    if return_code(model.AreaObj.SetPier(panel.name, leg.label)) == 0:
                        done += 1
    return done


# =============================================================================
# COUPLING BEAMS: beams joining two separate walls
# =============================================================================
@dataclass(frozen=True)
class BeamLine:
    name: str
    story: str
    xi: float
    yi: float
    xj: float
    yj: float


def _on_leg(x: float, y: float, leg: Leg, tolerance: float) -> bool:
    length = leg.length or 1.0
    u = ((leg.x2 - leg.x1) / length, (leg.y2 - leg.y1) / length)
    t = (x - leg.x1) * u[0] + (y - leg.y1) * u[1]
    across = abs(-(x - leg.x1) * u[1] + (y - leg.y1) * u[0])
    return -tolerance <= t <= length + tolerance and across <= leg.thickness / 2 + tolerance


def _wall_at(x: float, y: float, story: str, walls: list[Wall], tolerance: float) -> Wall | None:
    for wall in walls:
        if any(_on_leg(x, y, leg, tolerance) for leg in wall.legs.get(story, [])):
            return wall
    return None


def clear_span(beam: BeamLine, walls: tuple[Wall, Wall], samples: int = 400) -> float:
    """The length of the beam outside the two walls (its clear span ln, mm)."""
    length = math.hypot(beam.xj - beam.xi, beam.yj - beam.yi)
    outside = 0
    for k in range(samples):
        t = (k + 0.5) / samples
        x, y = beam.xi + t * (beam.xj - beam.xi), beam.yi + t * (beam.yj - beam.yi)
        if not any(_on_leg(x, y, leg, 0.0) for wall in walls
                   for leg in wall.legs.get(beam.story, [])):
            outside += 1
    return length * outside / samples


def coupling_beams(beams: list[BeamLine], walls: list[Wall], tolerance: float = 50.0
                   ) -> list[tuple[BeamLine, Wall, Wall, float]]:
    """The beams whose two ends are on two separate walls, with their clear span."""
    out = []
    for beam in beams:
        a = _wall_at(beam.xi, beam.yi, beam.story, walls, tolerance)
        b = _wall_at(beam.xj, beam.yj, beam.story, walls, tolerance)
        if a is not None and b is not None and a is not b:
            out.append((beam, a, b, clear_span(beam, (a, b))))
    return out
