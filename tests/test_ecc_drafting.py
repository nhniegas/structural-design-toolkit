"""The ECC drafting standard in the DXF drawings sdt writes (utilities/ecc_drafting.py):
colour = pen, objects ByLayer, ECC S- layers, SIMPLEX 0.85."""

import ezdxf
import pytest

from utilities import ecc_drafting as ecc

ECC_NAMES = {name for name, *_ in ecc.LAYERS.values()}


def _check_standard(path, allowed_extra=("0", "Defpoints")):
    doc = ezdxf.readfile(path)
    for entity in doc.modelspace():
        assert entity.dxf.layer in ECC_NAMES or entity.dxf.layer in allowed_extra, entity.dxf.layer
        assert entity.dxf.layer in doc.layers, f"layer {entity.dxf.layer} used but not defined"
        if entity.dxf.is_supported("color"):
            assert entity.dxf.get("color", 256) == 256, (entity.dxftype(), entity.dxf.layer)
        if entity.dxf.is_supported("lineweight"):
            assert entity.dxf.get("lineweight", -1) == -1, (entity.dxftype(), entity.dxf.layer)
        if entity.dxftype() in ("TEXT", "MTEXT"):
            assert entity.dxf.style == ecc.TEXT_STYLE
    for layer in doc.layers:
        assert layer.dxf.get("lineweight", -3) in (-3, -1), layer.dxf.name
    style = doc.styles.get(ecc.TEXT_STYLE)
    assert style.dxf.font == "simplex.shx" and style.dxf.width == pytest.approx(0.85)
    return doc


def test_layer_table_follows_the_pen_scheme():
    # colour = pen of ECC_FULL for Paper.ctb: bars 5 (0.53), girders 4 (0.40), concrete 2 (0.30),
    # dimensions 1 (0.18), beams 3 (0.15), text 52 (0.13), grid 251 (0.09 grey)
    colours = {role: colour for role, (_, colour, _, _) in ecc.LAYERS.items()}
    assert colours["bars"] == 5 and colours["girders"] == 4 and colours["concrete"] == 2
    assert colours["dims"] == 1 and colours["beams"] == 3 and colours["text"] == 52
    assert colours["grid"] == 251
    assert ecc.LAYERS["girders"][2] == "HIDDEN" and ecc.LAYERS["beams"][2] == "HIDDEN"
    assert ecc.LAYERS["no_plot"][3] is False


def test_to_standard_maps_layers_and_strips_colours_and_lineweights(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("TABLE")
    doc.layers.add("CONCRETE")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "TABLE", "color": 7, "lineweight": 18})
    msp.add_text("A", dxfattribs={"layer": "TABLE", "color": 3, "width": 0.75})
    msp.add_lwpolyline([(0, 0), (5, 5)], dxfattribs={"layer": "CONCRETE", "lineweight": 35})
    msp.add_line((0, 0), (0, 10), dxfattribs={"lineweight": 35})          # heavy, layer 0
    msp.add_line((0, 0), (0, 5), dxfattribs={"lineweight": 13})           # thin, layer 0
    ecc.to_standard(doc, {"TABLE": ("symbols", "text"), "CONCRETE": "concrete"})
    path = str(tmp_path / "x.dxf")
    doc.saveas(path)
    out = _check_standard(path)
    layers = [e.dxf.layer for e in out.modelspace()]
    assert layers == ["S-ANNO-SYMB", "S-ANNO-TEXT", "S-DETAIL", "S-SCHEDULE", "S-ANNO-SYMB"]
    text = out.modelspace().query("TEXT")[0]
    assert text.dxf.width == pytest.approx(0.75)               # a table keeps its condensed text
    assert "TABLE" not in out.layers and "CONCRETE" not in out.layers


def test_framing_plans_are_on_ecc_layers(tmp_path):
    from etabs_api.workflows.framing_plans import (
        FloorPlan,
        PlanBeam,
        PlanColumn,
        write_framing_plans,
    )

    floor = FloorPlan("2F", 3000.0)
    floor.beams.append(PlanBeam("G1", 0, 0, 6000, 0, 300, girder=True))
    floor.beams.append(PlanBeam("B1", 0, 3000, 6000, 3000, 250))
    floor.columns.append(PlanColumn("C1", 0, 0, 500, 500))
    path = str(tmp_path / "plans.dxf")
    write_framing_plans([floor], [], path)
    doc = _check_standard(path)
    used = {e.dxf.layer for e in doc.modelspace()}
    assert {"S-GIRDER", "S-BEAM", "S-COLS", "S-ANNO-TEXT"} <= used
    assert doc.layers.get("S-GIRDER").dxf.color == 4 and doc.layers.get("S-BEAM").dxf.color == 3


def test_slab_schedule_is_on_ecc_layers(tmp_path):
    from design.concrete.slabs.design import SlabOptions, design_floor
    from design.concrete.slabs.panels import SlabArea, Support, make_panels
    from design.concrete.slabs.schedule import assign_marks
    from design.concrete.slabs.schedule_dxf import write_slab_schedule

    areas = [SlabArea("A", "2F", 0, [(0, 0), (4000, 0), (4000, 5000), (0, 5000)], 125)]
    supports = [Support(f"S{i}", "2F", 0, a, b, 250, 500) for i, (a, b) in enumerate(
        [((0, 0), (4000, 0)), ((4000, 0), (4000, 5000)), ((4000, 5000), (0, 5000)),
         ((0, 5000), (0, 0))])]
    marks = assign_marks(design_floor(make_panels(areas, supports), SlabOptions()), 12.0)
    path = str(tmp_path / "slabs.dxf")
    write_slab_schedule(path, marks)
    doc = _check_standard(path)
    used = {e.dxf.layer for e in doc.modelspace()}
    assert {"S-ANNO-TEXT", "S-ANNO-SYMB", "S-SCHEDULE", "S-REFR-TITL-1"} <= used
