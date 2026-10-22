"""Tests for the framing of the floors (etabs_api/workflows/framing.py) and the plain parts
of sdt model (etabs_api/workflows/model_framing.py). None needs ETABS."""

import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import framing as fr  # noqa: E402
from etabs_api.workflows import grid_column_model as gc  # noqa: E402
from etabs_api.workflows import model_framing as mf  # noqa: E402
from etabs_api.workflows import model_setup as ms  # noqa: E402

XS, YS = (0, 6000, 12000, 18000), (0, 8000, 16000)


def grid_floor(story="2F", outline=None, **extra):
    supports = [fr.Support(x, y, 500, 500, f"C{x}-{y}") for x in XS for y in YS]
    outline = outline if outline is not None else [[(0, 0), (18000, 0), (18000, 16000),
                                                    (0, 16000)]]
    return fr.Floor(story, supports, outline=outline, **extra)


def kinds(members):
    out = {}
    for m in members:
        out[m.kind] = out.get(m.kind, 0) + 1
    return out


# --------------------------------------------------------------------------
# THE CODE RULES
# --------------------------------------------------------------------------
def test_beam_depth_and_slab_thickness_follow_the_span_ratios():
    # NSCP Table 409.3.1.1 and 407.3.1.1 at fy 420 (factor 1.0)
    assert fr.beam_depth(6300, 2, 420) == pytest.approx(300.0)
    assert fr.beam_depth(6300, 0, 420) == pytest.approx(6300 / 16)
    assert fr.one_way_thickness(2800, 2, 420) == pytest.approx(100.0)
    assert fr.one_way_thickness(2400, 1, 420) == pytest.approx(100.0)
    # 409.3.1.1.1: fy other than 420 MPa
    assert fr.fy_factor(280) == pytest.approx(0.8)


def test_two_way_thickness_by_alpha_fm():
    ln = 7500.0
    stiff = fr.two_way_thickness(ln, 1.25, 3.0, 420)
    assert stiff == pytest.approx(max(ln * (0.8 + 420 / 1400) / (36 + 9 * 1.25), 90))
    middle = fr.two_way_thickness(ln, 1.25, 1.0, 420)
    assert middle == pytest.approx(max(ln * 1.1 / (36 + 5 * 1.25 * 0.8), 125))
    # alpha_fm at most 0.2: Table 408.3.1.1, an interior panel at fy 420 is ln / 33
    assert fr.two_way_thickness(ln, 1.0, 0.1, 420) == pytest.approx(max(ln / 33, 125))


def test_rule_section_rounds_up_and_keeps_b_at_least_0_3_h():
    assert fr.rule_section(560, [500, 600, 700], [200, 300]) == (200.0, 600.0)
    assert fr.rule_section(690, [500, 600, 700], [200, 300]) == (300.0, 700.0)
    assert fr.rule_section(900, [500, 600], [300]) is None


# --------------------------------------------------------------------------
# GIRDERS AND BAYS
# --------------------------------------------------------------------------
def test_girders_join_neighbouring_columns_both_ways():
    lines = fr.girders(grid_floor(), 300)
    assert len(lines) == 3 * 3 + 4 * 2
    assert all(m.length in (6000, 8000) for m in lines)


def test_a_column_off_its_line_within_the_tolerance_still_frames():
    floor = grid_floor()
    floor.supports[1] = fr.Support(200, 8000, 500, 500)     # 200 mm off line A
    assert len(fr.girders(floor, 300)) == 17
    assert len(fr.girders(floor, 100)) < 17


def test_a_girder_stops_at_a_wall_and_none_runs_along_one():
    supports = [fr.Support(0, 0), fr.Support(12000, 0)]
    wall = fr.Wall(6000, -3000, 6000, 3000, 250)
    lines = fr.girders(fr.Floor("2F", supports, [wall]), 300)
    ends = sorted(round(m.x2 if m.x1 == 0 else m.x1) for m in lines if m.along_x)
    assert 6000 in ends and 12000 not in [round(m.length) for m in lines]
    along = fr.Floor("2F", [fr.Support(6000, -3000), fr.Support(6000, 3000)], [wall])
    assert not [m for m in fr.girders(along, 300) if not m.along_x]


def test_the_bays_are_the_panels_between_the_girders():
    floor = grid_floor()
    panels = fr.bays(floor, fr.girders(floor, 300))
    assert len(panels) == 6 and all(b.panel for b in panels)
    corner = min(panels, key=lambda b: b.polygon.bounds[:2])
    assert corner.continuous(True) == 1 and corner.continuous(False) == 1


# --------------------------------------------------------------------------
# THE FRAMING OF A FLOOR
# --------------------------------------------------------------------------
def test_a_thin_slab_needs_beams_a_thick_one_does_not():
    settings = fr.FramingSettings()
    thin = fr.frame_floor(grid_floor(), settings, 100)
    thick = fr.frame_floor(grid_floor(), settings, 200)
    assert not thin.problems and not thick.problems
    assert kinds(thin.members).get(fr.BEAM, 0) > kinds(thick.members).get(fr.BEAM, 0)
    assert all(o.thickness <= 100 + 1e-6 for _, o in thin.choices)


def test_the_x_and_y_schemes_keep_their_beam_direction():
    settings = fr.FramingSettings()
    x = fr.frame_floor(grid_floor(), settings, 125, "X")
    y = fr.frame_floor(grid_floor(), settings, 125, "Y")
    assert {o.kind for _, o in x.choices} <= {fr.ONE_WAY_X, fr.TWO_WAY}
    assert {o.kind for _, o in y.choices} <= {fr.ONE_WAY_Y, fr.TWO_WAY}


def test_an_overhang_gets_cantilever_beams_and_an_edge_beam():
    outline = [[(0, -1500), (18000, -1500), (18000, 16000), (0, 16000)]]
    framing = fr.frame_floor(grid_floor(outline=outline), fr.FramingSettings(), 150)
    count = kinds(framing.members)
    assert count.get(fr.CANTILEVER, 0) >= 4
    # the edge beam runs tip to tip, one member per gap between cantilevers
    assert count.get(fr.EDGE) == count[fr.CANTILEVER] - 1
    assert max(m.depth for m in framing.members if m.kind == fr.EDGE) <= 500
    tips = [m for m in framing.members if m.kind == fr.CANTILEVER]
    assert all(min(m.y1, m.y2) == pytest.approx(-1500) for m in tips)
    assert {round(m.x1) for m in tips} >= set(XS)          # one at every column


def test_a_planted_column_gets_a_transfer_girder():
    floor = grid_floor(planted=[fr.Support(9000, 4000, 400, 400, "PC1")])
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    transfer = [m for m in framing.members if m.kind == fr.TRANSFER]
    assert len(transfer) == 1 and transfer[0].length == pytest.approx(6000)
    assert any("PC1" in note for note in framing.notes)
    # a column standing on a girder makes that girder the transfer girder
    on_line = grid_floor(planted=[fr.Support(3000, 8000, 400, 400, "PC2")])
    framing = fr.frame_floor(on_line, fr.FramingSettings(), 150)
    assert [m.length for m in framing.members if m.kind == fr.TRANSFER] == [6000]


def test_only_large_openings_get_trimmer_beams():
    large = grid_floor(openings=[[(13000, 9000), (15000, 9000), (15000, 11000),
                                  (13000, 11000)]])
    small = grid_floor(openings=[[(13000, 9000), (13600, 9000), (13600, 9600),
                                  (13000, 9600)]])
    settings = fr.FramingSettings(trimmer_from=1000)
    assert kinds(fr.frame_floor(large, settings, 150).members).get(fr.TRIMMER) == 4
    assert fr.TRIMMER not in kinds(fr.frame_floor(small, settings, 150).members)
    # the opening is not slab
    framing = fr.frame_floor(large, settings, 150)
    assert framing.slab_area == pytest.approx(18 * 16 - 4, abs=0.01)


def test_the_architect_depth_limit_is_a_problem():
    settings = fr.FramingSettings(max_depth={"2F": 400})
    framing = fr.frame_floor(grid_floor(), settings, 150)
    assert any("architect" in p for p in framing.problems)


def test_the_volume_is_the_slab_and_the_members_below_it():
    framing = fr.frame_floor(grid_floor(), fr.FramingSettings(), 200)
    below = sum(m.width * (m.depth - 200) * m.length for m in framing.members) / 1e9
    assert framing.volume == pytest.approx(18 * 16 * 0.2 + below)


# --------------------------------------------------------------------------
# FRAME ACTION AND THE RANKING
# --------------------------------------------------------------------------
def test_frame_action_finds_a_column_with_no_girder_one_way():
    floor = grid_floor()
    floor.supports.append(fr.Support(24000, 3000, 500, 500, "LONE"))
    floor.outline = [[(0, 0), (24500, 0), (24500, 16000), (0, 16000)]]
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    check = fr.frame_action(floor, framing)
    assert not check.passed
    assert any("LONE" in f for f in check.findings)


def test_a_regular_grid_meets_the_frame_rules():
    floor = grid_floor()
    check = fr.frame_action(floor, fr.frame_floor(floor, fr.FramingSettings(), 150))
    assert check.passed, check.findings


def test_typical_floors_are_framed_once_and_counted_for_each():
    floors = [grid_floor("2F"), grid_floor("3F"), grid_floor("RF", planted=[])]
    floors[2].supports = floors[2].supports[:-1]       # the roof differs
    groups = fr.typical_groups(floors)
    assert [len(g) for g in groups] == [2, 1]
    ranked = fr.candidates(floors, fr.FramingSettings(thicknesses=[150]))
    best = ranked[0]
    assert [f.copies for f in best.floors] == [2, 1]
    assert best.volume == pytest.approx(2 * best.floors[0].volume + best.floors[1].volume)
    assert best.floors[0].label == "2F to 3F"


def test_candidates_are_ranked_valid_then_by_volume():
    ranked = fr.candidates([grid_floor()], fr.FramingSettings())
    valid = [c for c in ranked if c.valid]
    assert valid and ranked[:len(valid)] == valid
    assert [c.volume for c in valid] == sorted(c.volume for c in valid)


def test_holding_a_thickness_or_a_spacing():
    held = fr.candidates([grid_floor()], fr.FramingSettings(hold="thickness", held=125))
    assert {c.thickness for c in held} == {125}
    spaced = fr.candidates([grid_floor()], fr.FramingSettings(hold="spacing", held=2500))
    for c in spaced:
        for _, option in c.floors[0].choices:
            assert option.kind == fr.TWO_WAY or option.spacing <= 2500 + 1e-6


# --------------------------------------------------------------------------
# FROM THE DRAWING
# --------------------------------------------------------------------------
def _write(path):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    def text(value, x, y, layer):
        msp.add_text(value, dxfattribs={"layer": layer, "height": 300}).set_placement(
            (x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    for index, (story, planted) in enumerate((("2F", False), ("3F", True))):
        ox, oy = 100000.0 + index * 60000.0, 50000.0
        msp.add_lwpolyline([(ox - 8000, oy - 8000), (ox + 30000, oy - 8000),
                            (ox + 30000, oy + 25000), (ox - 8000, oy + 25000)], close=True,
                           dxfattribs={"layer": "S-STORY"})
        text(f"STORY {story}   HEIGHT 3500", ox + 6000, oy - 7000, "S-STORY")
        msp.add_point((ox, oy), dxfattribs={"layer": "S-ORIGIN"})
        for x in XS:
            for y in YS:
                msp.add_lwpolyline([(ox + x - 250, oy + y - 250), (ox + x + 250, oy + y - 250),
                                    (ox + x + 250, oy + y + 250), (ox + x - 250, oy + y + 250)],
                                   close=True, dxfattribs={"layer": "S-COLUMN"})
        if planted:
            msp.add_lwpolyline([(ox + 8800, oy + 3800), (ox + 9200, oy + 3800),
                                (ox + 9200, oy + 4200), (ox + 8800, oy + 4200)], close=True,
                               dxfattribs={"layer": "S-COLUMN"})
        msp.add_lwpolyline([(ox, oy), (ox + 18000, oy), (ox + 18000, oy + 16000),
                            (ox, oy + 16000)], close=True, dxfattribs={"layer": "S-SLAB"})
        msp.add_lwpolyline([(ox + 13000, oy + 9000), (ox + 15000, oy + 9000),
                            (ox + 15000, oy + 11000), (ox + 13000, oy + 11000)], close=True,
                           dxfattribs={"layer": "S-OPENING"})
        msp.add_lwpolyline([(ox, oy), (ox + 6000, oy), (ox + 6000, oy + 16000),
                            (ox, oy + 16000)], close=True, dxfattribs={"layer": "S-LOAD"})
        text("STORAGE", ox + 3000, oy + 8000, "S-LOAD")
    doc.saveas(path)
    return str(path)


def test_the_slab_openings_and_load_zones_are_read_from_the_drawing(tmp_path):
    building = gc.read_framing_plans(_write(tmp_path / "plans.dxf"))
    shapes = building.floors["2F"]
    assert len(shapes["slab"]) == 1 and len(shapes["opening"]) == 1
    assert shapes["load"][0][0] == "STORAGE"
    assert shapes["slab"][0][0] == pytest.approx((0, 0))
    floors = fr.floors_from_building(building)
    assert [f.story for f in floors] == ["2F", "3F"]
    # the 3F plan has a column with none under it: planted on the 2F floor
    assert [(p.x, p.y) for p in floors[0].planted] == [(9000, 4000)]
    assert not floors[1].planted


def test_floors_follow_renamed_stories(tmp_path):
    building = gc.read_framing_plans(_write(tmp_path / "plans.dxf"))
    building = gc.rename_stories(building, [("2F", "L2"), ("3F", "L3")])
    assert set(building.floors) == {"L2", "L3"}


# --------------------------------------------------------------------------
# SDT MODEL (plain parts)
# --------------------------------------------------------------------------
def test_framing_settings_come_from_the_setup():
    setup = ms.merge_settings({})
    settings = mf.framing_settings(setup, "thickness", 150, {"2F": 700})
    assert settings.girder_depths[0] == 500 and settings.girder_depths[-1] == 1000
    assert settings.beam_widths == [200, 300, 400]
    assert settings.thicknesses == [100, 125, 150, 200]
    assert settings.fy == pytest.approx(60 * ms.KSI_TO_MPA)
    member = fr.Member(fr.GIRDER, 0, 0, 6000, 0, 300, 600)
    assert mf.section_name(member, setup) == "G_300X600_C05_G60"
    assert mf.section_name(fr.Member(fr.BEAM, 0, 0, 1, 0, 200, 400), setup).startswith("B_")
    assert mf.slab_name(150, setup, True) == "S_150_C05_1W"


def test_slab_pieces_cover_the_slab_without_the_openings_and_split_by_zone():
    floor = grid_floor(openings=[[(13000, 9000), (15000, 9000), (15000, 11000),
                                  (13000, 11000)]],
                       zones=[("STORAGE", [(0, 0), (6000, 0), (6000, 16000), (0, 16000)])])
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    pieces = mf.slab_pieces(floor, framing)
    from shapely.geometry import Polygon

    area = sum(Polygon(points).area for points, _, _ in pieces) / 1e6
    assert area == pytest.approx(18 * 16 - 4, abs=0.05)
    storage = sum(Polygon(p).area for p, _, zone in pieces if zone == "STORAGE") / 1e6
    assert storage == pytest.approx(6 * 16, abs=0.05)
    assert {zone for _, _, zone in pieces} == {"STORAGE", mf.DEFAULT_ZONE}


def test_the_options_drawing_and_table(tmp_path):
    floors = [grid_floor()]
    ranked = fr.candidates(floors, fr.FramingSettings(thicknesses=[125, 150]))[:2]
    path = mf.write_candidates_dxf(ranked, floors, str(tmp_path / "options.dxf"),
                                   analysed={0: "ETABS: drift 0.80 of the limit"})
    doc = ezdxf.readfile(path)
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert any(t.startswith("OPTION 1") for t in texts)
    assert any("ETABS: drift" in t for t in texts)
    table = mf.candidates_table(ranked, {0: "ETABS: drift 0.80 of the limit"})
    assert table[1].lstrip().startswith("1") and "ETABS" in table[2]


def test_screening_text_marks_a_marginal_option():
    assert "MARGINAL" in mf.screening_text({"drift": 1.05, "story": "3F", "direction": "X"})
    assert "passes" in mf.screening_text({"drift": 0.9, "story": "3F", "direction": "X"})
    assert [mf.screening_rank(r) for r in ({"drift": 0.9}, {"drift": 1.05}, {"drift": 1.3},
                                           None)] == [0, 1, 2, 3]


# --------------------------------------------------------------------------
# ANGLED GRIDS, EDGES, VOIDS AND TIE BEAMS (2026-10-22)
# --------------------------------------------------------------------------
def turned_floor(angle=30.0, outline=True, **extra):
    """The 3 x 2 bay grid of ``grid_floor`` turned by ``angle`` about the origin, with its
    grid lines."""
    def turn(x, y):
        return fr._rotate(x, y, angle)

    supports = [fr.Support(*turn(x, y), 500, 500, f"C{x}-{y}") for x in XS for y in YS]
    grids = [(*turn(x, -2000), *turn(x, 18000)) for x in XS]
    grids += [(*turn(-2000, y), *turn(20000, y)) for y in YS]
    shape = [[turn(0, 0), turn(18000, 0), turn(18000, 16000), turn(0, 16000)]] if outline else []
    return fr.Floor("2F", supports, outline=shape, grid_lines=grids, **extra)


def test_girders_follow_grid_lines_at_an_angle():
    lines = fr.girders(turned_floor(30.0), 300)
    assert len(lines) == 17
    assert all(fr.parallel(m.angle, 30.0) or fr.parallel(m.angle, 120.0) for m in lines)
    assert sorted(round(m.length) for m in lines) == sorted(
        round(m.length) for m in fr.girders(grid_floor(), 300))


def test_a_turned_floor_frames_like_the_straight_one():
    settings = fr.FramingSettings()
    straight = fr.frame_floor(grid_floor(), settings, 125, "X")
    turned = fr.frame_floor(turned_floor(30.0), settings, 125, "X")
    assert not turned.problems
    assert kinds(turned.members) == kinds(straight.members)
    beams = [m for m in turned.members if m.kind == fr.BEAM]
    assert beams and all(fr.parallel(m.angle, 30.0) or fr.parallel(m.angle, 120.0) for m in beams)
    assert turned.volume == pytest.approx(straight.volume, rel=1e-6)
    assert fr.frame_action(turned_floor(30.0), turned).passed


def test_the_lowest_floor_without_a_slab_gets_tie_beams_only():
    floor = grid_floor(outline=[])
    floor.ties_only = True
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    assert set(kinds(framing.members)) == {fr.TIE} and framing.slab_area == 0
    assert fr.frame_action(floor, framing).passed
    setup = ms.merge_settings({})
    tie = framing.members[0]
    assert mf.section_name(tie, setup).startswith("FTB_")


def test_a_void_across_a_girder_gets_edge_beams_not_the_girder():
    void = [(4000, 6000), (8000, 6000), (8000, 10000), (4000, 10000)]   # across x = 6000
    floor = grid_floor(openings=[void])
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    from shapely.geometry import LineString, Polygon

    core = Polygon(void).buffer(-100)
    girders = [m for m in framing.members if m.kind == fr.GIRDER]
    assert not any(LineString(m.ends()).intersects(core) for m in girders)
    edges = [m for m in framing.members if m.kind == fr.EDGE]
    assert edges and all(Polygon(void).exterior.distance(LineString(m.ends()).centroid) < 1
                         for m in edges)
    assert any("voids cross" in n for n in framing.notes)


def test_an_opening_inside_one_bay_keeps_its_trimmers_on_a_turned_floor():
    hole = [fr._rotate(x, y, 30.0) for x, y in ((13000, 9000), (15000, 9000), (15000, 11000),
                                                 (13000, 11000))]
    framing = fr.frame_floor(turned_floor(30.0, openings=[hole]), fr.FramingSettings(), 150)
    trimmers = [m for m in framing.members if m.kind == fr.TRIMMER]
    assert len(trimmers) == 4
    assert all(fr.parallel(m.angle, 30.0) or fr.parallel(m.angle, 120.0) for m in trimmers)


def test_a_slab_edge_far_beyond_the_columns_gets_cantilevers_from_them():
    # the slab runs 5 m past the last column line, which has no girder along the edge side
    outline = [[(0, 0), (18000, 0), (18000, 21000), (0, 21000)]]
    settings = fr.FramingSettings(edge_span=4000)
    framing = fr.frame_floor(grid_floor(outline=outline), settings, 150)
    tips = [m for m in framing.members if m.kind == fr.CANTILEVER
            and max(m.y1, m.y2) == pytest.approx(21000)]
    assert {round(min(m.x1, m.x2)) for m in tips} >= {6000, 12000}   # the inner columns
    assert all(m.length <= settings.long_cantilever + 1 for m in tips)
    assert any(m.kind == fr.EDGE and m.y1 == pytest.approx(21000) for m in framing.members)


def test_an_edge_within_a_metre_of_its_girder_is_a_plain_overhang():
    outline = [[(0, -600), (18000, -600), (18000, 16000), (0, 16000)]]
    framing = fr.frame_floor(grid_floor(outline=outline), fr.FramingSettings(), 150)
    assert fr.EDGE not in kinds(framing.members)
    assert fr.CANTILEVER not in kinds(framing.members)
    assert framing.slab_area == pytest.approx(18 * 16.6, abs=0.05)


def test_slab_holes_are_the_openings_inside_a_piece():
    small = [(2000, 2000), (2600, 2000), (2600, 2600), (2000, 2600)]
    floor = grid_floor(openings=[small])
    framing = fr.frame_floor(floor, fr.FramingSettings(), 150)
    holes = mf.slab_holes(floor, framing)
    assert len(holes) == 1 and len(holes[0]) == 4


def test_the_reader_takes_polyline_grids_drops_twin_columns_and_reads_arcs(tmp_path):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(-1000, -1000), (20000, -1000), (20000, 20000), (-1000, 20000)],
                       close=True, dxfattribs={"layer": "S-STORY"})
    msp.add_text("STORY 2F   HEIGHT 3500", dxfattribs={"layer": "S-STORY", "height": 300}
                 ).set_placement((0, -500))
    msp.add_point((0, 0), dxfattribs={"layer": "S-ORIGIN"})
    msp.add_lwpolyline([(0, 0), (0, 15000)], dxfattribs={"layer": "S-GRID"})
    msp.add_text("1", dxfattribs={"layer": "S-GRID", "height": 300}).set_placement((0, 15500))
    for _ in range(2):        # drawn twice
        msp.add_lwpolyline([(-250, -250), (250, -250), (250, 250), (-250, 250)], close=True,
                           dxfattribs={"layer": "S-COLUMN"})
    msp.add_line((5000, 0), (5500, 0), dxfattribs={"layer": "S-COLUMN"})
    msp.add_lwpolyline([(0, 0, 0, 0, 0), (10000, 0, 0, 0, 1.0), (10000, 10000, 0, 0, 0),
                        (0, 10000, 0, 0, 0)], format="xyseb", close=True,
                       dxfattribs={"layer": "S-SLAB"})
    path = tmp_path / "plans.dxf"
    doc.saveas(path)
    building = gc.read_framing_plans(str(path))
    assert [g.label for g in building.grids] == ["1"]
    assert len(building.columns) == 1
    assert any("drawn twice" in w for w in building.warnings)
    assert any("plain line on the column layer" in w for w in building.warnings)
    slab = building.floors["2F"]["slab"][0]
    assert len(slab) > 6                       # the half circle as pieces
    assert max(x for x, _ in slab) == pytest.approx(15000, abs=1)   # bulge 1: a half circle
