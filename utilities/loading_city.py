"""The animation of the progress window: a city built along the loading line.

A row of buildings stands on a horizontal line, the loading line. A tower crane
runs back and forth along it; the buildings its hook passes go up floor by
floor, drawn as structures (columns and floor beams in the blue of ETABS, the
floors that are done filled in light blue). There are several kinds of
building: moment frames, braced frames, setback towers, high-rises with a spire,
houses with a pitched roof, warehouses with a roof truss and domed buildings.
On each pass some buildings are given another height, and now and then one is
taken down and another kind built in its place, so the city keeps changing.
A blue stretch of the line under the crane shows the work moving, as the bar
of a progress window does.

It runs in the progress window's own process (``run_loading_window``), on a Tk
canvas, at about 30 frames a second.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

FRAME_MS = 33               # about 30 frames a second
SWEEP_SECONDS = 3.2         # the crane from one end to the other
BUILD_RATE = 0.55           # floors added (or taken off) per frame under the hook
CHANGE_CHANCE = 0.45        # the share of buildings changed on each pass
REPLACE_CHANCE = 0.35       # of those, the share taken down to build another kind

# the kinds of building, and the slots (by width, in px at 96 dpi) each fits
FRAME, BRACED, SETBACK, TOWER, HOUSE, WAREHOUSE, DOME = (
    "frame", "braced", "setback", "tower", "house", "warehouse", "dome")
# landmarks, drawn as simplified silhouettes in the same structural style
BURJ, TAIPEI, PETRONAS, EIFFEL, OPERA = (
    "burj khalifa", "taipei 101", "petronas towers", "eiffel tower", "sydney opera house")
EMPIRE, PISA, CN, ARCH, COLOSSEUM = (
    "empire state building", "leaning tower of pisa", "cn tower", "gateway arch", "colosseum")
LANDMARKS = (BURJ, TAIPEI, PETRONAS, EIFFEL, OPERA, EMPIRE, PISA, CN, ARCH, COLOSSEUM)
KINDS_BY_WIDTH = (
    (20, (FRAME, BRACED, TOWER, HOUSE, BURJ, TAIPEI, PISA, CN)),
    (27, (FRAME, BRACED, SETBACK, HOUSE, DOME, BURJ, TAIPEI, EIFFEL, EMPIRE, PISA, CN)),
    (33, (FRAME, BRACED, SETBACK, WAREHOUSE, DOME, PETRONAS, EIFFEL, EMPIRE, ARCH)),
    (99, (FRAME, BRACED, SETBACK, WAREHOUSE, DOME, PETRONAS, OPERA, ARCH, COLOSSEUM)),
)
# what stands on a finished landmark (spire, antenna), in floors
CROWN = {BURJ: 2.6, TAIPEI: 1.4, PETRONAS: 1.3, EIFFEL: 1.0, OPERA: 0.0, EMPIRE: 1.6,
         PISA: 0.0, CN: 2.4, ARCH: 0.0, COLOSSEUM: 0.0}
# the landmarks drawn by a function of their own; the others are stacks of levels
DRAWN_APART = (EIFFEL, OPERA, PISA, CN, ARCH, COLOSSEUM)


@dataclass
class Building:
    x0: float
    x1: float
    kind: str
    target: int             # the floors it is being built to (0: being taken down)
    built: float = 0.0      # the floors built so far (a part of the top one in progress)
    columns: int = 2        # inner columns, besides the two at the ends


class CityAnimation:
    """The animation on ``canvas`` (``width`` x ``height`` pixels)."""

    def __init__(self, canvas, width: int, height: int, colours: dict, scale: float = 1.0):
        self.canvas = canvas
        self.width, self.height = width, height
        self.c = colours                    # accent, select, line, muted
        self.s = scale
        self.floor = max(int(round(6 * scale)), 4)
        self.ground = height - int(round(10 * scale))
        self.max_floors = max((self.ground - int(round(34 * scale))) // self.floor, 4)
        self.random = random.Random(11)
        self.buildings = self._lay_out()
        self.x = 0.0
        self.direction = 1
        self.speed = width / (SWEEP_SECONDS * 1000.0 / FRAME_MS)
        self._job = None
        self.frame = 0             # frames drawn: the swing of the damper ball

    # ---- the city ----
    def _lay_out(self) -> list[Building]:
        """Buildings of different widths along the line, with gaps between."""
        out, x = [], 6 * self.s
        while True:
            w = self.random.choice((16, 20, 24, 28, 32, 36)) * self.s
            if x + w > self.width - 6 * self.s:
                break
            building = Building(x, x + w, FRAME, 0)
            self._new_kind(building)
            out.append(building)
            x += w + self.random.choice((5, 7, 9)) * self.s
        return out

    def _kinds_for(self, building: Building) -> tuple[str, ...]:
        width = (building.x1 - building.x0) / self.s
        return next(kinds for limit, kinds in KINDS_BY_WIDTH if width <= limit)

    def _new_kind(self, building: Building) -> None:
        """Give a slot a kind of building that fits it, and the height to build."""
        building.kind = self.random.choice(self._kinds_for(building))
        width = (building.x1 - building.x0) / self.s
        building.columns = {TOWER: 1, WAREHOUSE: 1, HOUSE: 1}.get(
            building.kind, max(int(width // 9), 1))
        building.target = self._height(building.kind)

    def _height(self, kind: str) -> int:
        """Floors for a kind of building."""
        top = self.max_floors
        low, high = {HOUSE: (1, 2), WAREHOUSE: (1, 2), DOME: (2, 4), TOWER: (top - 3, top),
                     SETBACK: (4, top), BRACED: (2, top - 1),
                     BURJ: (top, top), TAIPEI: (top, top), PETRONAS: (top - 1, top),
                     EIFFEL: (top - 1, top), OPERA: (2, 2), EMPIRE: (top - 1, top),
                     PISA: (4, 5), CN: (top, top), ARCH: (top - 2, top - 1),
                     COLOSSEUM: (3, 3)}.get(kind, (2, top))
        return self.random.randint(max(low, 1), max(high, low, 1))

    def _floor_height(self, b: Building) -> float:
        return self.floor * (1.7 if b.kind == WAREHOUSE else 1.0)

    def _top(self, b: Building) -> float:
        """The height of the roof of a building as built, in px above the ground."""
        roof = 0.0
        if b.target and b.built >= b.target:
            roof = {HOUSE: 0.9, WAREHOUSE: 0.5, DOME: 0.4, TOWER: 1.8, **CROWN}.get(b.kind, 0.0)
            roof *= self.floor if b.kind != DOME else (b.x1 - b.x0)
        if b.kind == OPERA:                       # the shells rise above their podium
            return 3.0 * self.floor * b.built / max(b.target, 1)
        return b.built * self._floor_height(b) + roof

    # ---- running ----
    def start(self) -> None:
        self._tick()

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.canvas.after_cancel(self._job)
            except Exception:  # noqa: BLE001 - the window is gone
                pass
            self._job = None

    def _tick(self) -> None:
        self.frame += 1
        try:
            self.step()
            self.draw()
        except Exception:  # noqa: BLE001 - a frame that fails is skipped, the city goes on
            pass
        self._job = self.canvas.after(FRAME_MS, self._tick)

    @staticmethod
    def _size(b: Building) -> int:
        """The floors a building's shape is drawn for: its target, or, while it comes
        down (target 0), the floors still standing."""
        return max(b.target, math.ceil(b.built), 1)

    def step(self) -> None:
        """Move the crane one frame and build under its hook."""
        self.x += self.direction * self.speed
        if self.x >= self.width or self.x <= 0:
            self.x = min(max(self.x, 0.0), float(self.width))
            self.direction *= -1
            for building in self.buildings:          # the next pass: some change
                if self.random.random() < CHANGE_CHANCE:
                    if self.random.random() < REPLACE_CHANCE:
                        building.target = 0          # taken down, then another kind
                    else:
                        building.target = self._height(building.kind)
        hook = self.x + self.direction * 26 * self.s * 0.8     # where the hook hangs
        for building in self.buildings:
            if building.x0 - 10 * self.s <= hook <= building.x1 + 10 * self.s:
                if building.built < building.target:
                    building.built = min(building.built + BUILD_RATE, building.target)
                elif building.built > building.target:
                    building.built = max(building.built - BUILD_RATE, building.target)
                if building.built <= 0 and building.target == 0:
                    self._new_kind(building)          # the next kind on the cleared slot

    # ---- drawing ----
    def draw(self) -> None:
        c, s, cv = self.c, self.s, self.canvas
        cv.delete("all")
        ground = self.ground
        for building in self.buildings:
            self._draw_building(building)
        # the loading line, with the stretch under the crane in blue
        cv.create_line(0, ground, self.width, ground, fill=c["line"], width=max(2 * s, 1))
        half = 34 * s
        cv.create_line(max(self.x - half, 0), ground, min(self.x + half, self.width), ground,
                       fill=c["accent"], width=max(3 * s, 2), capstyle="round")
        self._draw_crane()

    def _span(self, b: Building, level: int) -> tuple[float, float]:
        """The left and right of a floor: a setback tower steps in as it rises."""
        if b.kind != SETBACK:
            return b.x0, b.x1
        width = b.x1 - b.x0
        inset = 0.0
        if level >= math.ceil(0.5 * self._size(b)):
            inset = 0.17 * width
        if level >= math.ceil(0.8 * self._size(b)):
            inset = 0.30 * width
        return b.x0 + inset, b.x1 - inset

    def _draw_building(self, b: Building) -> None:
        c, s, cv = self.c, self.s, self.canvas
        if b.built <= 0.01:
            return
        if b.kind in LANDMARKS:
            self._draw_landmark(b)
            return
        floors, part = int(b.built), b.built - int(b.built)
        thin = max(int(s), 1)
        height = self._floor_height(b)
        for level in range(floors + (1 if part > 0.02 else 0)):
            left, right = self._span(b, level)
            xs = [left + (right - left) * i / (b.columns + 1) for i in range(b.columns + 2)]
            y1 = self.ground - level * height
            done = level < floors
            y0 = y1 - height * (1.0 if done else part)
            if done:
                cv.create_rectangle(left, y0, right, y1, fill=c["select"], outline="")
                cv.create_line(left, y0, right, y0, fill=c["accent"], width=thin)   # the beams
                if b.kind == BRACED:                     # an X in every bay
                    for xa, xb in zip(xs, xs[1:]):
                        cv.create_line(xa, y1, xb, y0, fill=c["accent"], width=thin)
                        cv.create_line(xa, y0, xb, y1, fill=c["accent"], width=thin)
            for x in xs:                                 # the columns (the top floor: going up)
                cv.create_line(x, y1, x, y0, fill=c["accent"], width=thin)
        if b.target and b.built >= b.target:
            self._draw_roof(b, self.ground - floors * height)

    def _draw_roof(self, b: Building, top: float) -> None:
        """What crowns a finished building of each kind."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        left, right = self._span(b, b.target - 1)
        middle = (left + right) / 2
        if b.kind == HOUSE:                              # a pitched roof
            cv.create_polygon(left - 2 * s, top, middle, top - 0.9 * self.floor, right + 2 * s,
                              top, fill=c["select"], outline=c["accent"], width=thin)
        elif b.kind == WAREHOUSE:                        # a gable truss
            apex = top - 0.5 * self.floor
            cv.create_line(left, top, middle, apex, right, top, fill=c["accent"], width=thin)
            for k in (1, 2, 3):
                x = left + (right - left) * k / 4
                y = top - (apex - top) * -(1 - abs(x - middle) / ((right - left) / 2))
                cv.create_line(x, top, x, y, fill=c["accent"], width=thin)
        elif b.kind == DOME:                             # a dome on the roof slab
            inset = 0.12 * (right - left)
            rise = 0.4 * (right - left)
            cv.create_arc(left + inset, top - rise, right - inset, top + rise, start=0, extent=180,
                          style="chord", fill=c["select"], outline=c["accent"], width=thin)
        elif b.kind == TOWER:                            # a spire
            cv.create_line(middle, top, middle, top - 1.8 * self.floor, fill=c["accent"],
                           width=thin)
            cv.create_line(middle - 2 * s, top - 0.6 * self.floor, middle + 2 * s,
                           top - 0.6 * self.floor, fill=c["accent"], width=thin)

    # ---- the landmarks ----
    def _draw_landmark(self, b: Building) -> None:
        """A landmark built to ``b.built`` floors of ``b.target``: its levels from the
        ground up (the level going up shows its columns only), then its crown."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        done = b.target and b.built >= b.target
        middle = (b.x0 + b.x1) / 2
        width = b.x1 - b.x0
        if b.kind in DRAWN_APART:
            {OPERA: self._draw_opera, EIFFEL: self._draw_eiffel, PISA: self._draw_pisa,
             CN: self._draw_cn, ARCH: self._draw_arch, COLOSSEUM: self._draw_colosseum}[b.kind](b)
            return
        floors, part = int(b.built), b.built - int(b.built)
        for level in range(floors + (1 if part > 0.02 else 0)):
            full = level < floors
            y1 = g - level * f
            y0 = y1 - f * (1.0 if full else part)
            for left, right, flare in self._landmark_level(b, level):
                if not full:                                  # the level going up
                    for x in (left, middle if b.kind != PETRONAS else (left + right) / 2, right):
                        cv.create_line(x, y1, x, y0, fill=c["accent"], width=thin)
                    continue
                grow = flare * width / 2                      # Taipei 101: wider at the top
                cv.create_polygon(left, y1, right, y1, right + grow, y0, left - grow, y0,
                                  fill=c["select"], outline=c["accent"], width=thin)
        if b.kind == PETRONAS and b.built >= 0.55 * b.target:   # the skybridge
            y = g - round(0.5 * b.target) * f
            (al, ar, _), (bl, br, _) = self._landmark_level(b, 0)
            cv.create_line(ar, y, bl, y, fill=c["accent"], width=max(2 * s, 2))
            cv.create_line(ar, y + f, (ar + bl) / 2, y, bl, y + f, fill=c["accent"], width=thin)
        if b.kind == TAIPEI and b.built >= b.target - 1:         # the tuned mass damper
            top_module = g - (b.target - 2) * f
            swing = 1.6 * s * math.sin(self.frame * 0.12)
            hang = top_module + 0.45 * f
            cv.create_line(middle, top_module, middle + swing, hang, fill=c["muted"],
                           width=thin)
            r = 0.16 * width
            cv.create_oval(middle + swing - r, hang, middle + swing + r, hang + 2 * r,
                           fill=c["accent"], outline="")
        if done:                                                # the spires
            top = g - b.target * f
            for x in ([(l + r) / 2 for l, r, _ in self._landmark_level(b, b.target - 1)]
                      if b.kind == PETRONAS else [middle]):
                cv.create_line(x, top, x, top - CROWN[b.kind] * f, fill=c["accent"],
                               width=thin)

    def _landmark_level(self, b: Building, level: int) -> list[tuple[float, float, float]]:
        """The (left, right, flare) of each body of a landmark at a level."""
        width, middle = b.x1 - b.x0, (b.x0 + b.x1) / 2
        n = self._size(b)
        if b.kind == BURJ:                         # tiers stepping in all the way up
            half = width / 2 * (1.0 - 0.8 * level / n)
            return [(middle - half, middle + half, 0.0)]
        if b.kind == EMPIRE:                       # Art Deco setbacks in four steps
            share = next(w for limit, w in ((0.35, 1.0), (0.62, 0.78), (0.86, 0.56), (float("inf"), 0.34))
                         if level / n < limit)
            return [(middle - share * width / 2, middle + share * width / 2, 0.0)]
        if b.kind == TAIPEI:
            if level == 0:                         # the podium
                return [(b.x0 + 0.05 * width, b.x1 - 0.05 * width, 0.0)]
            if level >= n - 1:                     # the top, above the modules
                return [(middle - 0.17 * width, middle + 0.17 * width, 0.0)]
            return [(middle - 0.26 * width, middle + 0.26 * width, 0.11)]   # a flared module
        # Petronas: two towers, narrower in their top two floors
        shrink = 0.04 * width * max(0, level - (n - 3))
        return [(b.x0 + 0.06 * width + shrink, b.x0 + 0.40 * width - shrink, 0.0),
                (b.x0 + 0.60 * width + shrink, b.x0 + 0.94 * width - shrink, 0.0)]

    def _draw_eiffel(self, b: Building) -> None:
        """A lattice on curved legs, built from the ground up to ``b.built`` floors."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        width, middle = b.x1 - b.x0, (b.x0 + b.x1) / 2
        total = self._size(b) * f
        height = b.built * f

        def half(h):        # the half width at a height: the curve of the legs
            return width / 2 * (0.06 + 0.94 * (1 - h / total) ** 2.2)

        steps = max(int(height / (2 * s)), 1)
        for side in (-1, 1):                       # the two legs
            points = []
            for k in range(steps + 1):
                h = height * k / steps
                points += [middle + side * half(h), g - h]
            if len(points) >= 4:
                cv.create_line(*points, fill=c["accent"], width=max(2 * s, 1))
        level = 0.0
        while level + f <= height:                 # the lattice: an X in each storey
            y0, y1 = g - level, g - level - f
            a0, a1 = half(level), half(level + f)
            cv.create_line(middle - a0, y0, middle + a1, y1, fill=c["accent"], width=thin)
            cv.create_line(middle + a0, y0, middle - a1, y1, fill=c["accent"], width=thin)
            level += f
        for share in (0.25, 0.55):                 # the platforms
            h = share * total
            if height >= h:
                cv.create_line(middle - half(h) - 1.5 * s, g - h, middle + half(h) + 1.5 * s,
                               g - h, fill=c["accent"], width=max(2 * s, 1))
        if height >= 0.25 * total:                 # the arch between the legs
            a = half(0.12 * total) * 0.75
            cv.create_arc(middle - a, g - 0.2 * total, middle + a, g + 0.04 * total, start=0,
                          extent=180, style="arc", outline=c["accent"], width=thin)
        if b.target and b.built >= b.target:       # the antenna
            cv.create_line(middle, g - total, middle, g - total - CROWN[EIFFEL] * f,
                           fill=c["accent"], width=thin)

    def _draw_pisa(self, b: Building) -> None:
        """Arcaded tiers, each leaning a little more, and the belfry on top."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        width, middle = b.x1 - b.x0, (b.x0 + b.x1) / 2
        lean = 0.9 * s                                   # px of lean per storey
        floors, part = int(b.built), b.built - int(b.built)
        for level in range(floors + (1 if part > 0.02 else 0)):
            full = level < floors
            y1 = g - level * f
            y0 = y1 - f * (1.0 if full else part)
            half = 0.30 * width
            low = middle + level * lean
            high = middle + (level + (1.0 if full else part)) * lean
            if full:
                cv.create_polygon(low - half, y1, low + half, y1, high + half, y0, high - half, y0,
                                  fill=c["select"], outline=c["accent"], width=thin)
            for k in range(-2, 3):                       # the arcade's columns
                x = k * half / 2.5
                cv.create_line(low + x, y1, high + x, y0, fill=c["accent"], width=thin)
        if b.target and b.built >= b.target:             # the belfry
            top = g - b.target * f
            x = middle + b.target * lean
            cv.create_rectangle(x - 0.18 * width, top - 0.7 * f, x + 0.18 * width, top,
                                fill=c["select"], outline=c["accent"], width=thin)

    def _draw_cn(self, b: Building) -> None:
        """A tapering concrete shaft, the observation pod, the upper shaft, the antenna."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        width, middle = b.x1 - b.x0, (b.x0 + b.x1) / 2
        total = self._size(b) * f
        height = b.built * f
        pod = 0.72 * total

        def half(h):
            return width / 2 * (0.42 - 0.30 * min(h / pod, 1.0)) if h <= pod else width * 0.05

        h = min(height, total)
        cv.create_polygon(middle - half(0), g, middle + half(0), g, middle + half(h), g - h,
                          middle - half(h), g - h, fill=c["select"], outline=c["accent"],
                          width=thin)
        if height >= pod:                                # the pod
            r = 0.42 * width
            cv.create_oval(middle - r, g - pod - 0.55 * f, middle + r, g - pod + 0.45 * f,
                           fill=c["select"], outline=c["accent"], width=thin)
            cv.create_line(middle - r, g - pod, middle + r, g - pod, fill=c["accent"],
                           width=thin)
        if b.target and b.built >= b.target:             # the antenna
            cv.create_line(middle, g - total, middle, g - total - CROWN[CN] * f,
                           fill=c["accent"], width=thin)

    def _draw_arch(self, b: Building) -> None:
        """The two legs rise along the curve and meet at the top."""
        c, s, cv = self.c, self.s, self.canvas
        f, g = self.floor, self.ground
        width, middle = b.x1 - b.x0, (b.x0 + b.x1) / 2
        total = self._size(b) * f
        height = min(b.built * f, total)
        steps = 24
        for side in (-1, 1):
            points = []
            for k in range(steps + 1):
                u = k / steps                            # from the foot to the top
                x = middle + side * (width / 2) * (1 - u)
                h = total * (1 - (1 - u) ** 2)           # a parabola: steep at the foot
                if h > height + 0.5:
                    break
                points += [x, g - h]
            if len(points) >= 4:
                cv.create_line(*points, fill=c["accent"], width=max(3 * s, 2),
                               capstyle="round", smooth=True)

    def _draw_colosseum(self, b: Building) -> None:
        """Tiers of arches; the top tier broken away on one side, as it stands today."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        width = b.x1 - b.x0
        bay = 4.5 * s
        floors, part = int(b.built), b.built - int(b.built)
        for level in range(floors + (1 if part > 0.02 else 0)):
            full = level < floors
            y1 = g - level * f
            y0 = y1 - f * (1.0 if full else part)
            right = b.x1
            if b.target and b.built >= b.target and level == b.target - 1:
                right = b.x0 + 0.62 * width              # the top tier, broken off
            if full:
                cv.create_rectangle(b.x0, y0, right, y1, fill=c["select"], outline=c["accent"],
                                    width=thin)
                x = b.x0 + bay / 2
                while x + bay / 2 <= right:              # the arches of the tier
                    cv.create_arc(x - bay * 0.32, y0 + 0.25 * f, x + bay * 0.32,
                                  y1 + 0.35 * f, start=0, extent=180, style="arc",
                                  outline=c["accent"], width=thin)
                    x += bay
            else:
                x = b.x0
                while x <= right:
                    cv.create_line(x, y1, x, y0, fill=c["accent"], width=thin)
                    x += bay

    def _draw_opera(self, b: Building) -> None:
        """A podium and the sail shells, which open up as it is built."""
        c, s, cv = self.c, self.s, self.canvas
        thin = max(int(s), 1)
        f, g = self.floor, self.ground
        width = b.x1 - b.x0
        p = min(b.built / max(b.target, 1), 1.0)
        podium = 0.6 * f * min(p * 2, 1.0)
        cv.create_rectangle(b.x0, g - podium, b.x1, g, fill=c["select"], outline=c["accent"],
                            width=thin)
        if p <= 0.5:
            return
        rise = (p - 0.5) * 2                       # the shells, after the podium
        base = g - podium
        for start, span, tall in ((0.02, 0.44, 2.4), (0.30, 0.40, 2.0), (0.58, 0.40, 1.6)):
            x0 = b.x0 + start * width
            x1 = x0 + span * width
            peak = base - tall * f * rise
            cv.create_polygon(x0, base, x0 + 0.15 * span * width, peak, x1, base,
                              smooth=True, fill=c["select"], outline=c["accent"], width=thin)
            cv.create_line(x0 + 0.15 * span * width, peak, x0 + 0.5 * span * width, base,
                           fill=c["accent"], width=thin)

    def _draw_crane(self) -> None:
        c, s, cv = self.c, self.s, self.canvas
        x, ground = self.x, self.ground
        mast_top = int(round(8 * s))
        jib = 26 * s
        reach = x + self.direction * jib * 0.8              # the hook ahead of the crane
        line = max(int(round(1.5 * s)), 1)
        cv.create_line(x, ground, x, mast_top, fill=c["accent"], width=max(2 * s, 2))
        cv.create_line(x - jib * 0.5 * self.direction, mast_top + 3 * s, x + jib * self.direction,
                       mast_top + 3 * s, fill=c["accent"], width=line)
        cv.create_line(x, mast_top - 3 * s, x + jib * self.direction, mast_top + 3 * s,
                       fill=c["accent"], width=max(int(s), 1))
        cv.create_rectangle(x - jib * 0.5 * self.direction - 3 * s, mast_top + 2 * s,
                            x - jib * 0.5 * self.direction + 3 * s, mast_top + 7 * s,
                            fill=c["accent"], outline="")            # the counterweight
        # the hook comes down to the roof of the building under it
        roof = ground - 4 * s
        for b in self.buildings:
            if b.x0 <= reach <= b.x1:
                roof = ground - self._top(b) - 3 * s
        roof = max(roof, mast_top + 10 * s)
        cv.create_line(reach, mast_top + 3 * s, reach, roof, fill=c["muted"], width=max(int(s), 1))
        cv.create_rectangle(reach - 2 * s, roof, reach + 2 * s, roof + 3 * s,
                            fill=c["accent"], outline="")
