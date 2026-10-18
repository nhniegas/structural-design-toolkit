"""
tests/test_irregularity.py
==========================
The irregularity checks (etabs_api/workflows/irregularity.py): each type of
UBC 97 Tables 16-L and 16-M that is computed, the torsion amplifier Ax and the
P-delta stability coefficient, on plain numbers. No ETABS is needed.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import irregularity as ir  # noqa: E402

ORDER = ["RF", "4F", "3F", "2F"]          # from the top down


# --------------------------------------------------------------------------
# TORSION AND Ax
# --------------------------------------------------------------------------
def test_a_story_is_torsionally_irregular_when_its_largest_drift_is_over_1_2_of_the_average():
    drifts = [("RF", "EQXPE", "X", 11.0, 10.0), ("RF", "EQXNE", "X", 10.5, 10.0),
              ("4F", "EQXPE", "X", 13.0, 10.0), ("4F", "EQXNE", "X", 11.0, 10.0)]
    moved = [("RF", "EQXPE", "X", 44.0, 40.0), ("4F", "EQXPE", "X", 39.0, 30.0)]
    item = ir.torsion_item(drifts, moved, ORDER)
    assert item.status == ir.WARN and "1.300" in item.verdict
    rows = {r[0]: r for r in item.rows}
    assert rows["RF"][2] == "EQXPE" and rows["RF"][-1] == ir.REGULAR      # 1.10, the larger case
    assert rows["4F"][5] == "1.300" and rows["4F"][-1] == ir.IRREGULAR
    # Ax = (39 / (1.2 x 30))^2 = 1.17 at 4F; never under 1.0 (44 / 48 at RF)
    assert rows["4F"][6] == "1.17" and rows["RF"][6] == "1.00"
    assert any("Ax" in note for note in item.notes)


def test_ax_is_never_over_3():
    item = ir.torsion_item([("RF", "E", "X", 30.0, 10.0)], [("RF", "E", "X", 30.0, 10.0)], ORDER)
    assert item.rows[0][6] == "3.00"      # (30 / 12)^2 = 6.25


def test_at_exactly_1_2_the_story_is_regular():
    item = ir.torsion_item([("RF", "E", "X", 12.0, 10.0)], [], ORDER)
    assert item.status == ir.OK and item.rows[0][-1] == ir.REGULAR


def test_no_seismic_case_gives_no_torsion_check():
    assert ir.torsion_item([], [], ORDER).status == ir.NA


# --------------------------------------------------------------------------
# VERTICAL TYPES
# --------------------------------------------------------------------------
def stiff(values: dict[str, float], case: str = "EQX", direction: str = "X"):
    return [(story, case, direction, value) for story, value in values.items()]


def test_a_story_under_70_percent_of_the_stiffness_above_is_soft():
    item = ir.soft_story_item(stiff({"RF": 100e3, "4F": 100e3, "3F": 69e3, "2F": 69e3}), ORDER)
    rows = {r[0]: r for r in item.rows}
    assert rows["3F"][-1] == ir.IRREGULAR and rows["3F"][4] == "0.69"
    assert rows["4F"][-1] == ir.REGULAR and "RF" not in rows         # nothing above the roof
    assert item.status == ir.WARN and "3F" in item.verdict


def test_a_story_under_80_percent_of_the_average_of_the_three_above_is_soft():
    # 2F is 75 % of the story above (not soft by that) and 0.75 / ((1 + 1.2 + 1.4) / 3) = 62.5 %
    item = ir.soft_story_item(stiff({"RF": 140e3, "4F": 120e3, "3F": 100e3, "2F": 75e3}), ORDER)
    rows = {r[0]: r for r in item.rows}
    assert rows["2F"][4] == "0.75" and rows["2F"][5] == "0.62" and rows["2F"][-1] == ir.IRREGULAR
    assert rows["3F"][5] == "-"            # only two stories above it


def test_the_direction_and_case_with_the_lowest_ratio_are_shown():
    rows = stiff({"RF": 100e3, "4F": 90e3}, "EQXPE") + stiff({"RF": 100e3, "4F": 60e3}, "EQXNE") \
        + stiff({"RF": 100e3, "4F": 95e3}, "EQY", "Y")
    item = ir.soft_story_item(rows, ORDER)
    shown = {(r[0], r[1]): r for r in item.rows}
    assert shown[("4F", "X")][2] == "EQXNE" and shown[("4F", "X")][-1] == ir.IRREGULAR
    assert shown[("4F", "Y")][-1] == ir.REGULAR


def test_a_story_over_150_percent_of_the_mass_of_an_adjacent_story_is_irregular():
    item = ir.mass_item([("RF", 100.0), ("4F", 200.0), ("3F", 310.0), ("2F", 200.0)])
    rows = {r[0]: r[-1] for r in item.rows}
    # 4F is twice the roof, which is not counted; 3F is 1.55 times 4F and 2F
    assert rows == {"RF": ir.REGULAR, "4F": ir.REGULAR, "3F": ir.IRREGULAR, "2F": ir.REGULAR}
    assert item.status == ir.WARN


def test_equal_masses_are_regular_and_one_story_is_not_checked():
    assert ir.mass_item([("RF", 90.0), ("4F", 100.0), ("3F", 100.0)]).status == ir.OK
    assert ir.mass_item([("RF", 90.0), ("Base", 0.0)]).status == ir.NA


def test_a_setback_over_130_percent_is_a_vertical_geometric_irregularity():
    item = ir.geometry_item([("RF", 12000.0, 10000.0), ("4F", 12000.0, 10000.0),
                             ("3F", 18000.0, 10000.0), ("2F", 18000.0, 10000.0)])
    rows = {r[0]: r for r in item.rows}
    assert rows["3F"][3] == "1.50" and rows["3F"][-1] == ir.IRREGULAR
    assert rows["4F"][-1] == ir.REGULAR and rows["2F"][-1] == ir.REGULAR
    assert ir.geometry_item([("RF", 18000.0, 10000.0), ("4F", 18000.0, 10000.0)]).status == ir.OK


def test_a_story_under_80_percent_of_the_strength_above_is_weak():
    item = ir.weak_story_item([("RF", 1000.0, 8), ("4F", 1200.0, 8), ("3F", 900.0, 8),
                               ("2F", 500.0, 8)])
    rows = {r[0]: r for r in item.rows}
    assert rows["3F"][3] == "0.75" and rows["3F"][-1] == ir.IRREGULAR
    assert rows["2F"][3] == "0.56" and rows["4F"][-1] == ir.REGULAR
    assert item.status == ir.WARN and any("65 %" in note and "2F" in note for note in item.notes)


def test_the_weak_story_asks_for_the_column_design_when_there_is_none():
    item = ir.weak_story_item(None)
    assert item.status == ir.NA and "sdt columns" in item.verdict


def test_the_story_strength_is_the_flexural_shear_of_each_column_limited_by_its_shear_strength():
    report = pd.DataFrame([
        # C1: phi Mn 300 at each end over lu 3 m gives 200 kN, under its phi Vn of 400
        ("2F", "C1", "I", 400.0, 300.0, 3000.0, 3000.0),
        ("2F", "C1", "I", 450.0, 350.0, 3000.0, 3000.0),      # another combination: not the lowest
        ("2F", "C1", "J", 400.0, 300.0, 3000.0, 3000.0),
        # C2: flexure gives 2 x 600 / 3 = 400 kN, its phi Vn of 250 governs
        ("2F", "C2", "I", 250.0, 600.0, 3000.0, 3500.0),
        ("2F", "C2", "J", 250.0, 600.0, 3000.0, 3500.0),
        ("3F", "C1", "I", 400.0, "N/A", 3000.0, 3000.0),       # no moment strength: left out
    ], columns=["Story", "UniqueName", "End", "phi_Vn_kN", "phi_Mn_kNm", "Slender_lu3_mm",
                "Slender_lu2_mm"])
    found = {story: (strength, count) for story, strength, count in
             ir.story_strengths(report, ["3F", "2F"])}
    assert found["2F"] == (pytest.approx(450.0), 2)
    assert found["3F"] == (0.0, 0)


# --------------------------------------------------------------------------
# PLAN SHAPE
# --------------------------------------------------------------------------
RECTANGLE = [(0, 0), (18000, 0), (18000, 10000), (0, 10000)]
L_SHAPE = [(0, 0), (18000, 0), (18000, 5000), (9000, 5000), (9000, 10000), (0, 10000)]


def test_a_rectangle_has_no_re_entrant_corner():
    lx, ly, projections, opening = ir.plan_shape([RECTANGLE])
    assert (lx, ly) == pytest.approx((18000.0, 10000.0))
    assert projections == (0.0, 0.0) and opening == pytest.approx(0.0, abs=1e-6)


def test_an_l_shaped_floor_gives_the_two_projections_of_its_corner():
    lx, ly, (px, py), _ = ir.plan_shape([L_SHAPE])
    assert (px, py) == pytest.approx((0.5, 0.5), abs=0.01)
    corners, _ = ir.plan_items([("2F", ir.plan_shape([L_SHAPE]))])
    assert corners.status == ir.WARN and corners.rows[0][-1] == ir.IRREGULAR


def test_a_small_notch_is_not_an_irregularity():
    notch = [(0, 0), (18000, 0), (18000, 9000), (16000, 9000), (16000, 10000), (0, 10000)]
    corners, _ = ir.plan_items([("2F", ir.plan_shape([notch]))])      # 11 % by 10 %
    assert corners.status == ir.OK


def test_a_corner_cut_off_at_an_angle_is_not_a_re_entrant_corner():
    cut = [(0, 0), (12000, 0), (18000, 5000), (18000, 10000), (0, 10000)]
    assert ir.plan_shape([cut])[2] == (0.0, 0.0)


def test_a_floor_drawn_as_several_shells_and_turned_on_plan_is_measured_the_same():
    import math

    c, s = math.cos(math.radians(25.0)), math.sin(math.radians(25.0))
    halves = [[(0, 0), (9000, 0), (9000, 10000), (0, 10000)],
              [(9000, 0), (18000, 0), (18000, 5000), (9000, 5000)]]
    turned = [[(x * c - y * s, x * s + y * c) for x, y in half] for half in halves]
    lx, ly, (px, py), _ = ir.plan_shape(turned)
    assert (lx, ly) == pytest.approx((18000.0, 10000.0), abs=5.0)
    assert (px, py) == pytest.approx((0.5, 0.5), abs=0.01)


def test_openings_over_half_of_the_enclosed_area_are_a_diaphragm_discontinuity():
    ring = [[(0, 0), (10000, 0), (10000, 1000), (0, 1000)],
            [(0, 9000), (10000, 9000), (10000, 10000), (0, 10000)],
            [(0, 1000), (1000, 1000), (1000, 9000), (0, 9000)],
            [(9000, 1000), (10000, 1000), (10000, 9000), (9000, 9000)]]
    share = ir.plan_shape(ring)[3]
    assert share == pytest.approx(0.64, abs=0.01)
    _, opening = ir.plan_items([("2F", ir.plan_shape(ring)), ("3F", ir.plan_shape([RECTANGLE]))])
    assert opening.status == ir.WARN and [r[-1] for r in opening.rows] == [ir.IRREGULAR,
                                                                         ir.REGULAR]


def test_a_model_with_no_floor_shell_has_no_plan_shape_check():
    corners, opening = ir.plan_items([])
    assert corners.status == ir.NA and "no floor shells" in opening.verdict


# --------------------------------------------------------------------------
# P-DELTA STABILITY
# --------------------------------------------------------------------------
def test_theta_is_p_delta_over_v_h():
    rows = [("2F", "EQX", "X", 6_000_000.0, 600_000.0, 15.0, 3000.0),      # 0.05
            ("2F", "EQXNE", "X", 6_000_000.0, 600_000.0, 12.0, 3000.0),    # 0.04, not shown
            ("2F", "EQY", "Y", 6_000_000.0, 600_000.0, 9.0, 3000.0)]       # 0.03
    item = ir.stability_item(rows, pdelta_on=True, r_factor=8.5)
    shown = {r[1]: r for r in item.rows}
    assert shown["X"][2] == "EQX" and shown["X"][7] == "0.0500" and shown["Y"][7] == "0.0300"
    assert item.status == ir.OK and "may be neglected" in item.verdict
    assert any("0.02 / R = 0.00235" in note and "over it" in note for note in item.notes)


def test_theta_over_0_10_needs_p_delta_and_warns_only_when_it_is_off():
    rows = [("2F", "EQX", "X", 9_000_000.0, 300_000.0, 12.0, 3000.0)]      # 0.12
    on = ir.stability_item(rows, pdelta_on=True)
    off = ir.stability_item(rows, pdelta_on=False)
    assert on.status == ir.OK and "must be considered" in on.verdict
    assert off.status == ir.WARN and "off in the model" in off.verdict
    assert on.rows[0][-1] == "P-delta to consider"


def test_the_note_says_which_drifts_the_coefficient_is_on():
    rows = [("2F", "EQX", "X", 1e6, 1e5, 5.0, 3000.0)]
    assert "P-delta off" in ir.stability_item(rows, True, first_order=True).notes[0]
    assert "as analysed" in ir.stability_item(rows, True, first_order=False).notes[0]
    assert ir.stability_item([], True).status == ir.NA


# --------------------------------------------------------------------------
# WHAT FOLLOWS, AND THE REPORT
# --------------------------------------------------------------------------
def test_a_regular_structure_may_use_90_percent_and_an_irregular_one_100():
    regular = [ir.mass_item([("RF", 90.0), ("4F", 100.0)])]
    assert any("90 %" in line for line in ir.consequences(regular))
    irregular = [ir.soft_story_item(stiff({"RF": 100e3, "4F": 50e3}), ORDER)]
    lines = ir.consequences(irregular)
    assert "vertical type 1" in lines[0] and any("100 %" in line for line in lines)
    assert any("dynamic procedure" in line for line in lines)


def test_the_types_left_to_the_engineer_say_what_the_model_shows():
    offset, plane, parallel = ir.judged_items(["3-C7", "3-C8"], 4)
    assert offset.status == ir.INFO and "2 columns start on a floor" in offset.verdict
    assert "1630.8.2" in plane.notes[0] and not parallel.notes
    assert "every column runs down" in ir.judged_items([], 4)[0].verdict


def test_the_report_lists_every_check_with_its_table_and_equations():
    report = ir.IrregularityReport("m.EDB", cases=["EQX"], items=[
        ir.soft_story_item(stiff({"RF": 100e3, "4F": 50e3}), ORDER), ir.weak_story_item(None),
        ir.torsion_item([("RF", "EQX", "X", 13.0, 10.0)], [("RF", "EQX", "X", 13.0, 10.0)], ORDER),
        ir.stability_item([("2F", "EQX", "X", 6e6, 6e5, 15.0, 3000.0)], True)])
    text = report.text()
    for expected in ("IRREGULARITY CHECK", "Static seismic cases: EQX", "Vertical irregularities",
                     "Plan irregularities", "P-delta", "soft story at 4F", "run sdt columns first",
                     "What follows", "theta 0.0500"):
        assert expected in text
    summary = ir.irregularity_summary(report, "m - Irregularity.txt").text()
    assert "IRREGULAR: soft story at 4F" in summary and "The model was not changed." in summary


# --------------------------------------------------------------------------
# A CASE IN ITS OWN DIRECTION, AND THE EQUATIONS IN THE TERMINAL
# --------------------------------------------------------------------------
def test_a_seismic_case_is_checked_in_its_own_direction_only():
    """ETABS lists an X case in Y too, where it hardly moves the structure: 9.3
    over 1.8 there is not a torsional irregularity."""
    rows = [("LG", "EQXNE", "X", 8.2, 7.9), ("LG", "EQXNE", "Y", 9.288, 1.785),
            ("UG", "EQXNE", "X", 12.1, 11.8), ("UG", "EQXNE", "Y", 0.9, 0.2),
            ("LG", "EQYPE", "Y", 9.0, 8.1), ("LG", "EQYPE", "X", 1.1, 0.2)]
    kept = ir.own_direction(rows)
    assert {(r[1], r[2]) for r in kept} == {("EQXNE", "X"), ("EQYPE", "Y")}
    item = ir.torsion_item(kept, kept, ["UG", "LG"])
    assert item.status == ir.OK and all(r[-1] == ir.REGULAR for r in item.rows)
    assert {(r[1], r[2]) for r in item.rows} == {("X", "EQXNE"), ("Y", "EQYPE")}


def test_the_direction_of_a_case_can_be_told_by_another_column():
    rows = [("2F", "EQX", "X", 50e3, 600e3), ("2F", "EQX", "Y", 90e3, 2.0)]   # stiffness, shear
    assert [r[2] for r in ir.own_direction(rows)] == ["X"]             # by the shear, the last
    assert [r[2] for r in ir.own_direction(rows, weight=3)] == ["Y"]


def test_the_equations_are_printed_with_symbols_not_as_latex():
    report = ir.IrregularityReport("m.EDB", cases=["EQX"], items=[
        ir.soft_story_item(stiff({"RF": 100e3, "4F": 50e3}), ORDER),
        ir.mass_item([("RF", 100.0), ("4F", 200.0), ("3F", 310.0)]),
        ir.weak_story_item([("RF", 1000.0, 8), ("4F", 700.0, 8)]),
        ir.torsion_item([("RF", "EQX", "X", 13.0, 10.0)], [("RF", "EQX", "X", 13.0, 10.0)], ORDER),
        ir.stability_item([("2F", "EQX", "X", 6e6, 6e5, 15.0, 3000.0)], True)])
    text = report.text()
    assert "\\" not in text and "_{" not in text and "_(" not in text
    for expected in ("δₘₐₓ / δₐᵥₑ > 1.2 ⇒ irregular", "Aₓ = (δₘₐₓ / (1.2 δₐᵥₑ))²",
                     "θ = Pₓ Δ / (Vₓ h) ≤ 0.10", "Kᵢ < 0.70 Kᵢ₊₁", "Vᵢ < 0.80 Vᵢ₊₁ ⇒ weak story"):
        assert expected in text


# --------------------------------------------------------------------------
# WHICH LEVELS ARE STORIES
# --------------------------------------------------------------------------
def test_a_minor_level_and_the_levels_at_the_base_are_not_compared_as_stories():
    """The masses of a real model: a footing tie level of 62 t among floors of
    15,000 t gave a mass ratio of 270."""
    masses = [("PD2", 7333.6), ("PD1", 19228.9), ("3F", 15157.3), ("2F", 15260.7),
              ("UG", 16684.0), ("UG - FT", 61.9), ("LG", 2742.7), ("LG - FT", 378.0)]
    stories, below, minor = ir.compared_stories(masses, "LG")
    assert stories == ["PD2", "PD1", "3F", "2F", "UG"]
    assert below == ["LG", "LG - FT"] and minor == ["UG - FT"]
    note = ir.levels_note(below, minor)
    assert "LG, LG - FT (at or below the base" in note and "UG - FT (under 10 %" in note
    item = ir.mass_item([(s, m) for s, m in masses if s in stories])
    assert item.status == ir.OK      # PD1 is 2.6 times the roof above it, which is not counted


def test_with_no_base_named_every_level_with_enough_mass_is_a_story():
    stories, below, minor = ir.compared_stories([("RD", 156.0), ("3F", 184.0), ("GF", 12.0)], None)
    assert (stories, below, minor) == (["RD", "3F"], [], ["GF"])
    assert ir.levels_note([], []) == ""


def test_ax_is_not_given_where_etabs_has_no_displacement_of_the_level():
    drifts = [("2F", "EQX", "X", 13.0, 10.0), ("LG", "EQX", "X", 17.3, 10.0)]
    item = ir.torsion_item(drifts, [("2F", "EQX", "X", 26.0, 20.0)], ["2F", "LG"])
    rows = {r[0]: r for r in item.rows}
    assert rows["2F"][6] == "1.17" and rows["LG"][6] == "-" and rows["LG"][-1] == ir.IRREGULAR
    assert any("no diaphragm" in note for note in item.notes)


def test_diaphragm_stories_from_the_assignments():
    rows = [{"Story": "L3", "Diaphragm": "D1"}, {"Story": "L2", "Diaphragm": "D1"},
            {"Story": "TIE", "Diaphragm": "None"}, {"Story": "", "Diaphragm": "D1"},
            {"Story": "GF", "Diaphragm": "From Shell Object"}]
    assert ir.diaphragm_stories(rows) == {"L3", "L2"}


def test_stories_without_a_diaphragm_are_listed_and_left_out():
    order = ["ROOF", "L3", "L2", "TIE", "BASE"]
    missing = ir.without_diaphragm(order, {"ROOF", "L3", "L2"})
    assert missing == ["TIE", "BASE"]
    # a model with no diaphragm at all is compared as before
    assert ir.without_diaphragm(order, set()) == []
    note = ir.levels_note([], [], ["TIE"])
    assert "TIE (no diaphragm" in note


def test_a_story_without_a_diaphragm_is_not_in_the_mass_comparison():
    masses = [("ROOF", 300.0), ("L2", 400.0), ("TIE", 900.0)]
    no_diaphragm = ir.without_diaphragm(["ROOF", "L2", "TIE"], {"ROOF", "L2"})
    stories, _, _ = ir.compared_stories(masses, None)
    stories = [story for story in stories if story not in no_diaphragm]
    item = ir.mass_item([m for m in masses if m[0] in stories])
    assert stories == ["ROOF", "L2"]
    assert not item.irregular
