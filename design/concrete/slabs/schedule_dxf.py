"""The slab schedule (DXF): one table per floor, in millimetres on paper, as the office
schedule: for each mark two rows, TOP (the bars at the discontinuous and the continuous
edges) and BOTTOM (the bars at midspan), in the short and the long direction, with the
thickness and the remarks (ONE WAY, TWO WAY, CANTILEVER). Tables are placed side by
side. Text simplex; table lines 0.18 mm, the outline and titles 0.35 mm.
"""

from __future__ import annotations

import ezdxf

from design.concrete.slabs.design import TWO_WAY
from design.concrete.slabs.schedule import Mark, bar_text

TEXT = 2.5
ROW = 7.0
HEAD = 7.0
GAP = 40.0
# column widths: mark, thickness, row, short disco, short cont, long disco, long cont, remarks
WIDTHS = (28.0, 20.0, 18.0, 28.0, 28.0, 28.0, 28.0, 46.0)


def write_slab_schedule(path: str, marks: list[Mark]) -> str:
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    for layer, colour, weight in (("TABLE", 7, 18), ("OUTLINE", 7, 35), ("TEXT", 7, 18),
                                  ("TITLE", 7, 35)):
        doc.layers.new(layer, dxfattribs={"color": colour, "lineweight": weight})
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})
    msp = doc.modelspace()
    middle = ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER

    def text(value, x, y, height=TEXT, layer="TEXT", align=middle):
        msp.add_text(str(value), dxfattribs={"height": height, "style": "SIMPLEX",
                                             "layer": layer}).set_placement((x, y), align=align)

    def line(x1, y1, x2, y2, layer="TABLE"):
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})

    def rect(x1, y1, x2, y2, layer="TABLE"):
        msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True,
                           dxfattribs={"layer": layer})

    total = sum(WIDTHS)
    floors: dict[str, list[Mark]] = {}
    for m in marks:
        floors.setdefault(m.story, []).append(m)
    x0 = 0.0
    for story, floor in floors.items():
        xs = [x0]
        for w in WIDTHS:
            xs.append(xs[-1] + w)
        top = 0.0
        text(f"{story} SLAB SCHEDULE", x0, top + 8.0, TEXT * 1.6, "TITLE",
             ezdxf.enums.TextEntityAlignment.LEFT)
        # two header rows
        y1, y2, y3 = top, top - HEAD, top - 2 * HEAD
        rect(xs[0], y1, xs[-1], y3)
        for i in (1, 2, 3, 7):
            line(xs[i], y1, xs[i], y3)
        line(xs[5], y1, xs[5], y3)
        line(xs[3], y2, xs[7], y2)
        line(xs[4], y2, xs[4], y3)
        line(xs[6], y2, xs[6], y3)
        text("MARK", (xs[0] + xs[1]) / 2, (y1 + y3) / 2)
        text("THK.", (xs[1] + xs[2]) / 2, (y1 + y3) / 2)
        text("", (xs[2] + xs[3]) / 2, (y1 + y3) / 2)
        text("SHORT DIRECTION", (xs[3] + xs[5]) / 2, (y1 + y2) / 2)
        text("LONG DIRECTION", (xs[5] + xs[7]) / 2, (y1 + y2) / 2)
        for i, label in ((3, "DISCO"), (4, "CONT"), (5, "DISCO"), (6, "CONT")):
            text(label, (xs[i] + xs[i + 1]) / 2, (y2 + y3) / 2)
        text("REMARKS", (xs[7] + xs[8]) / 2, (y1 + y3) / 2)
        y = y3
        for m in floor:
            a, b, c = y, y - ROW, y - 2 * ROW
            rect(xs[0], a, xs[-1], c)
            for i in (1, 2, 3, 5, 7):
                line(xs[i], a, xs[i], c)
            line(xs[2], b, xs[7], b)
            line(xs[4], a, xs[4], b)
            line(xs[6], a, xs[6], b)
            text(m.name, (xs[0] + xs[1]) / 2, b, min(TEXT, WIDTHS[0] / (0.75 * len(m.name))))
            text(f"{m.h:g}", (xs[1] + xs[2]) / 2, b)
            text("TOP", (xs[2] + xs[3]) / 2, (a + b) / 2)
            text("BOTTOM", (xs[2] + xs[3]) / 2, (b + c) / 2)
            s = m.spacings
            for i, key in ((3, ("short", "top", "discontinuous")), (4, ("short", "top", "continuous")),
                           (5, ("long", "top", "discontinuous")), (6, ("long", "top", "continuous"))):
                text(bar_text(m.bar, s.get(key)), (xs[i] + xs[i + 1]) / 2, (a + b) / 2)
            text(bar_text(m.bar, s.get(("short", "bottom", "midspan"))), (xs[3] + xs[5]) / 2,
                 (b + c) / 2)
            text(bar_text(m.bar, s.get(("long", "bottom", "midspan"))), (xs[5] + xs[7]) / 2,
                 (b + c) / 2)
            if m.kind == TWO_WAY:
                text(m.kind, (xs[7] + xs[8]) / 2, b)
            else:   # the long direction of one-way slabs and cantilevers: temperature bars
                text(m.kind, (xs[7] + xs[8]) / 2, (a + b) / 2)
                text("LONG: TEMP. BARS", (xs[7] + xs[8]) / 2, (b + c) / 2, TEXT * 0.85)
            y = c
        rect(xs[0], top, xs[-1], y, "OUTLINE")
        x0 += total + GAP
    doc.saveas(path)
    return path
