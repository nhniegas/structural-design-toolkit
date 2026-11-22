"""The ECC drafting standard for the DXF drawings sdt writes.

ECCRUZ Corporation's standard, documented with its template, plot styles and tools in
``Desktop\\ECC DRAFTING STANDARDS`` (its CLAUDE.md): **colour = pen**. Every object is
ByLayer; the layer carries the colour, and the plot style ``ECC_FULL for Paper.ctb`` turns the
colour into the plotted lineweight (5 = 0.53, 6/7 = 0.45, 4 = 0.40, 2 = 0.30, 1 = 0.18,
3 = 0.15, 52 = 0.13, 251/252 = 0.09 grey). No lineweight is set on objects or layers.

Text is SIMPLEX (simplex.shx) at width 0.85. Drawings are in mm at full size: plans and
sections in model space at 1:1, schedules drawn at paper size (to be shown in a 1:1 viewport).

The drawings stay readable without the plot style: AutoCAD and viewers show the layer
colours, and a PDF plotted with the CTB gets the lineweights.
"""

from __future__ import annotations

import os

# role -> (layer, ACI colour, linetype, plotted)
LAYERS = {
    "text": ("S-ANNO-TEXT", 52, "Continuous", True),          # notes, labels, table text
    "dims": ("S-ANNO-DIMS", 1, "Continuous", True),           # dimensions
    "symbols": ("S-ANNO-SYMB", 52, "Continuous", True),       # leaders, thin table lines
    "beam_marks": ("S-ANNO-BEAM", 52, "Continuous", True),
    "column_marks": ("S-ANNO-COLS", 52, "Continuous", True),
    "slab_marks": ("S-ANNO-SLAB", 52, "Continuous", True),
    "titles": ("S-REFR-TITL-1", 2, "Continuous", True),       # drawing and sheet titles
    "symbol_lines": ("S-SYMB-MISC", 1, "Continuous", True),   # symbol linework, zones
    "grid": ("S-GRID", 251, "CENTER", True),                  # grid lines (bubbles: symbols)
    "columns": ("S-COLS", 5, "Continuous", True),             # columns cut in plan
    "girders": ("S-GIRDER", 4, "HIDDEN", True),               # girders under the slab
    "beams": ("S-BEAM", 3, "HIDDEN", True),                   # beams under the slab
    "walls": ("S-WALL", 4, "Continuous", True),
    "footings": ("S-FOOTING", 6, "HIDDEN", True),
    "tie_beams": ("S-TIEBEAM", 4, "HIDDEN", True),
    "concrete": ("S-DETAIL", 2, "Continuous", True),          # concrete outline in sections
    "bars": ("S-REBAR", 5, "Continuous", True),               # bars, bar dots
    "ties": ("S-DETL-RBAR", 5, "Continuous", True),           # ties, stirrups, hoops
    "schedule": ("S-SCHEDULE", 2, "Continuous", True),        # schedule outlines and headers
    "hatch": ("S-HATCH", 8, "Continuous", True),
    "no_plot": ("MV", 251, "Continuous", False),              # aids, bar size labels
}
TEXT_STYLE = "SIMPLEX"
TEXT_FONT = "simplex.shx"
TEXT_WIDTH = 0.85
STANDARD_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "ECC DRAFTING STANDARDS")
TEMPLATE_DXF = os.path.join(STANDARD_FOLDER, "template", "ECC STANDARD.dxf")


TEXT_TYPES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")
LINE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "HATCH", "SOLID")


def to_standard(doc, layer_roles: dict | None = None, heavy: int = 30) -> None:
    """Put a drawing made with its own layers on the ECC standard, before it is saved.

    ``layer_roles`` maps an old layer name to an ECC role, or to (role for lines, role for
    text). Text not mapped goes to ``text``; lines not mapped go to ``schedule`` (S-SCHEDULE,
    pen 0.30) when they were drawn with a lineweight of ``heavy`` (0.30 mm) or more, else to
    ``symbols`` (S-ANNO-SYMB, pen 0.13). Explicit colours and lineweights are removed, so
    every object plots by its layer colour; text takes the SIMPLEX style, keeping its width.
    Old layers left empty are removed."""
    layer_roles = layer_roles or {}
    setup(doc)
    old_layers = set()
    for entity in doc.modelspace():
        kind = entity.dxftype()
        old = entity.dxf.get("layer", "0")
        mapped = layer_roles.get(old)
        is_text = kind in TEXT_TYPES
        if isinstance(mapped, tuple):
            role = mapped[1] if is_text else mapped[0]
        elif mapped:
            role = mapped
        elif is_text:
            role = "text"
        elif entity.dxf.get("lineweight", -1) >= heavy:
            role = "schedule"
        else:
            role = "symbols"
        if old not in ("0", "Defpoints"):
            old_layers.add(old)
        entity.dxf.layer = layer(role)
        if entity.dxf.is_supported("color"):
            entity.dxf.color = 256
        if entity.dxf.is_supported("lineweight"):
            entity.dxf.lineweight = -1
        if is_text and entity.dxf.is_supported("style"):
            entity.dxf.style = TEXT_STYLE
    standard = {name for name, *_ in LAYERS.values()}
    for name in old_layers - standard:
        if name in doc.layers:
            try:
                doc.layers.remove(name)
            except Exception:  # noqa: BLE001 - still used by a block
                pass


def layer(role: str) -> str:
    """The ECC layer name of a role (``"girders"`` -> ``"S-GIRDER"``)."""
    return LAYERS[role][0]


def setup(doc, roles=None) -> None:
    """Add the ECC layers (all, or those of ``roles``) and the SIMPLEX 0.85 text style to an
    ezdxf document. Layers are ByLayer colour, Default lineweight; linetypes must exist in
    the document (``ezdxf.new(setup=True)`` loads them), else Continuous is used."""
    for role, (name, colour, linetype, plot) in LAYERS.items():
        if roles is not None and role not in roles:
            continue
        if linetype not in doc.linetypes:
            linetype = "Continuous"
        if name in doc.layers:
            entry = doc.layers.get(name)
            entry.dxf.color = colour
            entry.dxf.linetype = linetype
        else:
            entry = doc.layers.add(name, color=colour, linetype=linetype)
        entry.dxf.plot = 1 if plot else 0
    if TEXT_STYLE in doc.styles:
        style = doc.styles.get(TEXT_STYLE)
        style.dxf.font = TEXT_FONT
    else:
        style = doc.styles.new(TEXT_STYLE, dxfattribs={"font": TEXT_FONT})
    style.dxf.width = TEXT_WIDTH
    doc.header["$TEXTSTYLE"] = TEXT_STYLE
    doc.header["$INSUNITS"] = 4
    doc.header["$LTSCALE"] = 1.0
    doc.header["$PSLTSCALE"] = 1
