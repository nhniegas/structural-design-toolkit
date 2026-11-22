"""sdt slabs: Method 3, the one-way coefficients, the strip, the DDM, panels and marks."""

import math

import pytest

from design.concrete.slabs import ddm, method3, one_way, strip
from design.concrete.slabs.design import (
    CANTILEVER,
    ONE_WAY,
    TWO_WAY,
    SlabOptions,
    classify,
    design_floor,
)
from design.concrete.slabs.panels import SlabArea, Support, make_panels, row
from design.concrete.slabs.schedule import assign_marks, schedule_table


# ---- Method 3 ---------------------------------------------------------------
def test_method3_cases_by_continuous_edges():
    assert [method3.case_of(*k) for k in
            ((0, 0), (2, 2), (0, 2), (1, 1), (2, 0), (1, 0), (0, 1), (1, 2), (2, 1))] == \
        list(range(1, 10))


def test_method3_negative_coefficients_are_zero_where_the_strips_have_no_continuous_end():
    for m in method3.CA_NEG:
        for case in (1, 3, 7):
            assert method3.CA_NEG[m][case - 1] == 0
        for case in (1, 5, 6):
            assert method3.CB_NEG[m][case - 1] == 0


def test_method3_values_of_the_office_sheets():
    # a few cells of SLAB DESIGNER v1.0 (2way) and SLAB DESIGN.xls (INPUT)
    assert method3.CA_NEG[1.0] == (0, 0.045, 0, 0.05, 0.075, 0.071, 0, 0.033, 0.061)
    assert method3.CB_NEG[0.5][2] == 0.022
    assert method3.CA_DL[1.0][0] == 0.036
    assert method3.CA_LL[0.5][0] == 0.095
    # the four cells where SLAB DESIGN.xls has typing errors take the v1.0 values
    assert method3.CA_NEG[0.8][8] == 0.075
    assert method3.CA_DL[0.9][1] == 0.022
    assert method3.CA_DL[0.85][5] == 0.042
    assert method3.CB_LL[0.8][8] == 0.017


def test_method3_load_ratio_is_wa_with_the_short_direction_carrying_more():
    assert method3.WA[0.5] == (0.94, 0.94, 0.76, 0.94, 0.99, 0.97, 0.86, 0.89, 0.97)
    assert method3.WA[0.65] == (0.85, 0.85, 0.53, 0.85, 0.96, 0.93, 0.69, 0.74, 0.92)
    for m, values in method3.WA.items():
        for case in (1, 2, 4):           # symmetric cases: half each way when square
            if m == 1.0:
                assert values[case - 1] == 0.5


def test_method3_coefficients_grow_as_the_panel_gets_longer():
    for table in (method3.CA_NEG, method3.CA_DL, method3.CA_LL):
        for case in range(1, 10):
            column = [table[m][case - 1] for m in sorted(table, reverse=True)]
            assert column == sorted(column), (case, column)


def test_method3_interpolates_and_gives_the_moments():
    assert method3.coefficient(method3.CA_NEG, 0.775, 2) == pytest.approx((0.065 + 0.069) / 2)
    r = method3.panel_moments(4.0, 5.0, 6.0, 4.0, 2)
    assert r.m == 0.8
    assert r.ma_neg_cont == pytest.approx(0.065 * 10.0 * 16.0)
    assert r.ma_pos == pytest.approx((0.026 * 6.0 + 0.041 * 4.0) * 16.0)
    assert r.mb_neg_cont == pytest.approx(0.027 * 10.0 * 25.0)
    assert r.ma_neg_disc == pytest.approx(r.ma_pos / 3.0)
    assert r.va == pytest.approx(0.71 * 10.0 * 4.0 / 2.0)


# ---- one-way ----------------------------------------------------------------
def _spans(lengths, wd=6.0, wl=4.0):
    return [one_way.Span(ln, ln, wd, wl, 5.0, 2.5, 125.0) for ln in lengths]


def test_one_way_coefficients_of_table_406_5_2():
    spans = _spans([4.0, 4.0, 4.0])
    r = one_way.design_strip(spans, one_way.SPANDREL, one_way.UNRESTRAINED)
    assert r.method == "NSCP Table 406.5.2"
    w = 10.0
    first, middle, last = r.spans
    assert first.m_left == pytest.approx(w * 16 / 24)
    assert first.m_pos == pytest.approx(w * 16 / 14)
    assert first.m_right == pytest.approx(w * 16 / 10)
    assert middle.m_pos == pytest.approx(w * 16 / 16)
    assert last.m_pos == pytest.approx(w * 16 / 11)
    assert last.m_right == 0.0
    assert first.v_right == pytest.approx(1.15 * w * 4 / 2)
    assert middle.v_left == pytest.approx(w * 4 / 2)


def test_one_way_two_spans_and_short_spans():
    two = one_way.design_strip(_spans([4.0, 4.0]), one_way.SPANDREL, one_way.SPANDREL)
    assert two.spans[0].m_right == pytest.approx(10.0 * 16 / 9)
    short = one_way.design_strip(_spans([2.5, 2.5, 2.5]), one_way.SPANDREL, one_way.SPANDREL)
    assert short.spans[1].m_left == pytest.approx(10.0 * 2.5 ** 2 / 12)
    assert short.spans[0].m_left == pytest.approx(10.0 * 2.5 ** 2 / 12)


def test_one_way_falls_to_a_strip_analysis_when_406_5_1_is_not_met():
    r = one_way.design_strip(_spans([3.0, 5.0]), one_way.SPANDREL, one_way.SPANDREL)
    assert r.method.startswith("strip analysis")
    assert any("406.5.1(e)" in reason for reason in r.reasons)
    single = one_way.design_strip(_spans([4.0]), one_way.UNRESTRAINED, one_way.UNRESTRAINED)
    assert any("406.5.1(d)" in reason for reason in single.reasons)
    assert single.spans[0].m_pos == pytest.approx(10.0 * 16 / 8)


def test_strip_analysis_two_equal_spans_and_the_live_load_pattern():
    spans = [one_way.Span(4.0, 4.0, 0.0, 10.0, 1.0, 5.0, 125.0)] * 2
    result = one_way.strip_analysis(spans, one_way.UNRESTRAINED, one_way.UNRESTRAINED)
    # support: both spans loaded, w l^2 / 8; span: one span loaded, 0.0957 w l^2
    assert result[0].m_right == pytest.approx(10.0 * 16 / 8)
    assert result[0].m_pos == pytest.approx(0.0957 * 10.0 * 16, rel=2e-3)


def test_cantilever():
    r = one_way.cantilever(one_way.Span(1.5, 1.5, 6.0, 4.0, 5.0, 2.5, 125.0))
    assert r.m_left == pytest.approx(10.0 * 1.5 ** 2 / 2)
    assert r.v_left == pytest.approx(15.0)


# ---- the strip ----------------------------------------------------------------
def test_steel_for_a_moment_by_hand():
    steel, strain = strip.steel_for_moment(10.0, 100.0, 28.0, 414.0)
    rn = 10e6 / (0.9 * 1000 * 100 ** 2)
    rho = 0.85 * 28 / 414 * (1 - math.sqrt(1 - 2 * rn / (0.85 * 28)))
    assert steel == pytest.approx(rho * 1000 * 100)
    assert strain > 0.005


def test_minimum_steel_and_spacing():
    assert strip.as_min_ratio(414.0) == 0.0020
    assert strip.as_min_ratio(420.0) == pytest.approx(0.0018)
    assert strip.as_min_ratio(600.0) == pytest.approx(max(0.0018 * 420 / 600, 0.0014))
    assert strip.spacing_for(300.0, 12.0, 300.0) == 375.0 - 75.0  # 377 -> limit 300
    assert strip.spacing_for(500.0, 12.0, 450.0) == 225.0
    b = strip.design_bars("x", 0.0, 99.0, 125.0, 28.0, 414.0, 12.0, 250.0)
    assert b.as_design == pytest.approx(0.0020 * 1000 * 125)
    assert b.spacing == 250.0


def test_minimum_thickness():
    assert strip.one_way_min_thickness(3000, "simple", 420) == pytest.approx(150.0)
    assert strip.one_way_min_thickness(3000, "simple", 414) == pytest.approx(150 * (0.4 + 414 / 700))
    assert strip.one_way_min_thickness(1500, "cantilever", 420) == pytest.approx(150.0)
    h = strip.two_way_min_thickness(5000, 1.25, 2.5, 420, False)
    assert h == pytest.approx(5000 * 1.1 / (36 + 9 * 1.25))
    assert strip.two_way_min_thickness(3000, 1.0, 2.5, 420, False) == 90.0
    mid = strip.two_way_min_thickness(6000, 1.0, 1.0, 420, True)
    assert mid == pytest.approx(max(1.1 * 6000 * 1.1 / (36 + 5 * 0.8), 125.0))
    assert strip.two_way_min_thickness(6000, 1.0, 0.1, 420, False, exterior=True) == \
        pytest.approx(6000 / 33)
    assert strip.flat_divisor(350, 0) == pytest.approx(1 / ((1 / 33 + 1 / 30) / 2))


def test_two_way_bars_need_a_strain_of_0_004():
    b = strip.design_bars("x", 40.0, 80.0, 100.0, 21.0, 414.0, 12.0, 200.0, 0.004)
    assert not b.passed


# ---- DDM ------------------------------------------------------------------------
def test_ddm_column_strip_shares_at_the_table_points():
    assert ddm.cs_interior_negative(0.0, 1.0) == 0.75
    assert ddm.cs_interior_negative(1.0, 0.5) == 0.90
    assert ddm.cs_interior_negative(2.0, 2.0) == 0.45
    assert ddm.cs_interior_negative(0.5, 2.0) == pytest.approx(0.60)
    assert ddm.cs_positive(0.0, 2.0) == 0.60
    assert ddm.cs_positive(1.0, 0.5) == 0.90
    assert ddm.cs_exterior_negative(1.0, 0.0, 1.0) == 1.0
    assert ddm.cs_exterior_negative(1.0, 2.5, 2.0) == pytest.approx(0.45)
    assert ddm.beam_share(1.0) == 0.85 and ddm.beam_share(0.5) == pytest.approx(0.425)


def test_ddm_moments_of_an_interior_span():
    r = ddm.direction_moments(6.0, 5.0, 5.6, 10.0, False, "beams", 0.0, 0.0)
    assert r.mo == pytest.approx(10.0 * 5.0 * 5.6 ** 2 / 8)
    assert r.neg_interior == pytest.approx(0.65 * r.mo)
    assert r.positive == pytest.approx(0.35 * r.mo)
    end = ddm.direction_moments(6.0, 5.0, 5.6, 10.0, True, "beams", 0.0, 0.0)
    assert (end.neg_interior, end.positive, end.neg_exterior) == pytest.approx(
        (0.70 * end.mo, 0.57 * end.mo, 0.16 * end.mo))


def test_flanged_beam_inertia_and_alpha_f():
    # a 300 x 600 beam under a 150 slab: flanges of 450 each side, at most 4h = 600
    assert ddm.flange_width(300, 600, 150, 2) == 1200
    assert ddm.flange_width(300, 1000, 150, 1) == 900
    i = ddm.flanged_inertia(300, 600, 150, 0)
    assert i == pytest.approx(300 * 600 ** 3 / 12)
    a = ddm.alpha_f(300, 600, 150, 2, 6000)
    assert a == pytest.approx(ddm.flanged_inertia(300, 600, 150, 2) / (6000 * 150 ** 3 / 12))
    assert ddm.alpha_f(300, 150, 150, 2, 6000) == 0.0


def test_ddm_limits():
    ok = ddm.limits([6, 6, 6], [5, 5, 5], 6, 5, 5.0, 4.0, 1.0, 1.0, 0.0)
    assert ok == []
    bad = ddm.limits([6, 6], [5, 3, 5], 6, 2.5, 5.0, 12.0, 1.0, 10.0, 0.2)
    text = " ".join(bad)
    for clause in ("408.10.2.1", "408.10.2.2", "408.10.2.3", "408.10.2.4", "408.10.2.6",
                   "408.10.2.7"):
        assert clause in text


# ---- panels, design and marks ---------------------------------------------------
def _floor(beam_depth=500.0):
    xs, ys = [0, 4000, 8000, 12000], [0, 5000, 10000]
    areas, supports = [], []
    for i in range(3):
        for j in range(2):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            # each bay drawn in two halves, joined into one panel
            xm = (x0 + x1) / 2
            for k, (a, b) in enumerate(((x0, xm), (xm, x1))):
                areas.append(SlabArea(f"A{i}{j}{k}", "2F", 3000,
                                      [(a, y0), (b, y0), (b, y1), (a, y1)], 125, "S125", 28,
                                      2.5, 2.4))
    for x in xs:
        supports.append(Support(f"BX{x}", "2F", 3000, (x, 0), (x, 10000), 250, beam_depth))
    for y in ys:
        supports.append(Support(f"BY{y}", "2F", 3000, (0, y), (12000, y), 250, beam_depth))
    for i in range(3):
        areas.append(SlabArea(f"C{i}", "2F", 3000, [(xs[i], 10000), (xs[i + 1], 10000),
                                                    (xs[i + 1], 12000), (xs[i], 12000)],
                              125, "S125", 28, 2.5, 2.4))
    for x in xs:
        supports.append(Support(f"CX{x}", "2F", 3000, (x, 10000), (x, 12000), 250, beam_depth))
    supports.append(Support("CN", "2F", 3000, (0, 12000), (12000, 12000), 250, beam_depth))
    # a cantilever 1.2 m out from the south edge of the first bay
    areas.append(SlabArea("K", "2F", 3000, [(0, -1200), (4000, -1200), (4000, 0), (0, 0)],
                          125, "S125", 28, 2.5, 2.4))
    return areas, supports


def test_panels_join_the_halves_and_describe_their_sides():
    areas, supports = _floor()
    panels = make_panels(areas, supports)
    assert len(panels) == 6 + 3 + 1
    first = next(p for p in panels if "A000" in p.areas)
    assert first.areas == ["A000", "A001"]
    assert first.box == (0, 0, 4000, 5000)
    assert all(first.sides[k].is_supported for k in "WESN")
    assert not first.sides["W"].is_continuous and first.sides["E"].is_continuous
    assert first.sides["S"].is_continuous      # the cantilever beyond
    assert first.clear_x == 4000 - 250
    assert row(panels, first.index, "x") == [first.index] + [
        p.index for p in panels if p.areas in (["A100", "A101"], ["A200", "A201"])]


def test_kinds_methods_and_marks():
    areas, supports = _floor()
    panels = make_panels(areas, supports)
    designs = design_floor(panels, SlabOptions())
    kinds = {" ".join(d.panel.areas): d for d in designs}
    assert kinds["A000 A001"].kind == TWO_WAY
    assert kinds["A000 A001"].method.startswith("ACI Method 3, case 8")   # S continuous (cantilever)
    assert kinds["A100 A101"].method.startswith("ACI Method 3, case 9")
    assert kinds["C0"].kind == ONE_WAY and kinds["C0"].short_dir == "Y"
    assert kinds["K"].kind == CANTILEVER
    assert kinds["K"].moments[("short", "top", "continuous")] > 0
    for d in designs:
        assert d.passed, (d.panel.areas, d.flags)
    marks = assign_marks(designs, 12.0, 15)
    assert all(d.mark for d in designs)
    assert marks[0].name == "S-1" and marks[0].kind == TWO_WAY
    table = schedule_table(marks)
    assert list(table.columns[:3]) == ["Mark", "Story", "Thickness (mm)"]
    assert "Short top disco" in table.columns
    few = assign_marks(designs, 12.0, 2)
    assert len({m.kind for m in few}) <= len(few) <= 3


def test_flexible_beams_go_to_the_ddm_or_are_flagged():
    areas, supports = _floor(beam_depth=200.0)
    designs = design_floor(make_panels(areas, supports), SlabOptions())
    two_way = [d for d in designs if d.kind == TWO_WAY]
    assert all(d.alpha_fm < 2.0 for d in two_way)
    # two spans in y only: 408.10.2.1 fails, Method 3 is kept and flagged
    assert all(d.method.startswith("ACI Method 3") for d in two_way)
    assert all(any("DDM NOT APPLICABLE" in f for f in d.flags) for d in two_way)


def test_classify_by_supported_sides():
    areas, supports = _floor()
    panels = make_panels(areas, supports)
    k = next(p for p in panels if p.areas == ["K"])
    assert classify(k) == (CANTILEVER, "Y")


def test_a_regular_floor_on_flexible_beams_is_designed_by_the_ddm():
    grid = [0, 6000, 12000, 18000]
    areas = [SlabArea(f"P{i}{j}", "3F", 6000, [(grid[i], grid[j]), (grid[i + 1], grid[j]),
                                               (grid[i + 1], grid[j + 1]), (grid[i], grid[j + 1])],
                      150, "S150", 28, 3.0, 2.4) for i in range(3) for j in range(3)]
    supports = [Support(f"X{x}", "3F", 6000, (x, 0), (x, 18000), 300, 300) for x in grid]
    supports += [Support(f"Y{y}", "3F", 6000, (0, y), (18000, y), 300, 300) for y in grid]
    designs = design_floor(make_panels(areas, supports), SlabOptions())
    assert all(d.method == "Direct Design Method (NSCP 408.10)" for d in designs), \
        [(d.method, d.flags) for d in designs]
    middle = next(d for d in designs if d.panel.areas == ["P11"])
    corner = next(d for d in designs if d.panel.areas == ["P00"])
    assert ("short", "top", "discontinuous") not in middle.moments
    assert corner.moments[("short", "top", "discontinuous")] > 0
    assert corner.moments[("short", "bottom", "midspan")] > middle.moments[("short", "bottom", "midspan")]


def test_one_slab_object_over_several_bays_is_cut_at_the_beams():
    xs, ys = [0, 4000, 8000, 12000], [0, 5000, 10000]
    whole = [SlabArea("F1", "2F", 3000, [(0, 0), (12000, 0), (12000, 10000), (0, 10000)],
                      125, "S125", 28, 2.5, 2.4)]
    bays = [SlabArea(f"B{i}{j}", "2F", 3000, [(xs[i], ys[j]), (xs[i + 1], ys[j]),
                                               (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])],
                     125, "S125", 28, 2.5, 2.4) for i in range(3) for j in range(2)]
    supports = [Support(f"X{x}", "2F", 3000, (x, 0), (x, 10000), 250, 500) for x in xs]
    supports += [Support(f"Y{y}", "2F", 3000, (0, y), (12000, y), 250, 500) for y in ys]
    cut = make_panels(whole, supports)
    assert len(cut) == 6
    assert sorted(p.box for p in cut) == sorted(p.box for p in make_panels(bays, supports))
    assert {a for p in cut for a in p.areas} == {f"F1:{n}" for n in range(1, 7)}
    one = design_floor(cut, SlabOptions())
    other = design_floor(make_panels(bays, supports), SlabOptions())
    by_box = {d.panel.box: d for d in other}
    for d in one:
        assert d.method == by_box[d.panel.box].method
        assert d.moments == pytest.approx(by_box[d.panel.box].moments)


def test_slab_marks_on_the_framing_plans(tmp_path):
    import ezdxf

    from etabs_api.workflows.framing_plans import (
        FloorPlan,
        add_slab_marks,
        write_framing_plans,
    )

    floors = [FloorPlan("2F", 3000.0), FloorPlan("3F", 6000.0)]
    data = {"panels": [
        {"story": "2F", "box": [0, 0, 4000, 5000], "mark": "S-1", "kind": "TWO WAY", "short": "X"},
        {"story": "2F", "box": [0, 5000, 4000, 7000], "mark": "S-2", "kind": "ONE WAY", "short": "Y"},
        {"story": "2F", "box": [0, -1200, 4000, 0], "mark": "S-3", "kind": "CANTILEVER",
         "short": "Y", "free": -1},
    ]}
    assert add_slab_marks(floors, data) == 3
    assert floors[1].slabs == []
    path = str(tmp_path / "plans.dxf")
    totals = write_framing_plans(floors, [], path)
    assert totals["slabs"] == 3
    msp = ezdxf.readfile(path).modelspace()
    texts = {e.dxf.text for e in msp.query("TEXT[layer=='S-ANNO-SLAB']")}
    assert texts == {"S-1", "S-2", "S-3"}
    # span lines with a half arrowhead at each end: 2 x 3 for the two-way, 3 for the
    # one-way; the cantilever's at its free end only (2)
    lines = msp.query("LINE[layer=='S-ANNO-SLAB']")
    assert len(lines) == 6 + 3 + 2
    # the cantilever's half arrowhead is at its free (south) end, y = -600 - half
    cantilever = [e for e in lines if max(e.dxf.start.y, e.dxf.end.y) < 0]
    assert min(min(e.dxf.start.y, e.dxf.end.y) for e in cantilever) < -600


def _two_way_designs():
    import dataclasses

    areas, supports = _floor()
    designs = [d for d in design_floor(make_panels(areas, supports), SlabOptions())
               if d.kind == TWO_WAY][:3]
    for d in designs:      # the same edges on each, so only the spacings differ
        d.bars = {k: dataclasses.replace(b) for k, b in designs[0].bars.items()}
        d.flags = []
    return designs


def _set_spacing(d, spacing):
    import dataclasses

    d.bars = {k: dataclasses.replace(b, spacing=spacing) for k, b in d.bars.items()}


def test_marks_within_25_mm_are_merged_with_the_closer_spacing():
    designs = _two_way_designs()
    for d, s in zip(designs, (250.0, 275.0, 325.0)):
        _set_spacing(d, s)
    marks = assign_marks(designs, 12.0, 15, 25.0)
    assert sorted(len(m.designs) for m in marks) == [1, 2]
    merged = next(m for m in marks if len(m.designs) == 2)
    assert set(merged.spacings.values()) == {250.0}
    assert len(assign_marks(designs, 12.0, 15, 0.0)) == 3      # no tolerance: three marks
    assert len(assign_marks(designs, 12.0, 15, 50.0)) == 2     # 250 + 275, then 325 is 75 away


def test_a_failing_panel_keeps_a_mark_of_its_own():
    designs = _two_way_designs()
    for d in designs:
        _set_spacing(d, 250.0)
    designs[2].h_min = designs[2].panel.h + 50.0          # fails on thickness only
    assert not designs[2].passed
    marks = assign_marks(designs, 12.0, 1, 25.0)           # even when over the limit
    assert len(marks) == 2
    failing = next(m for m in marks if not m.passed)
    assert failing.designs == [designs[2]] and failing.name == "S-2"
