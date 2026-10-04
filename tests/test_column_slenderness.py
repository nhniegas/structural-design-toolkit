"""Checks for design/column_slenderness.py (ACI 318M-14 6.2.5, 6.6.4.5).

HAND CALC values are worked out in the test with the code equations. None of
these tests needs ETABS.
"""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design import column_slenderness as cs  # noqa: E402

SECTION = cs.ColumnSection(width=400.0, depth=400.0, circular=False, fc=28.0)
LONG = cs.AxisGeometry(lu=6000.0, k=1.0)
SHORT = cs.AxisGeometry(lu=3000.0, k=1.0)


# --------------------------------------------------------------------------
# FORMULAS
# --------------------------------------------------------------------------
def test_radius_of_gyration_is_030_h_or_025_d():
    assert cs.radius_of_gyration(400.0, False) == pytest.approx(120.0)
    assert cs.radius_of_gyration(400.0, True) == pytest.approx(100.0)


@pytest.mark.parametrize("ratio, limit", [
    (-1.0, 22.0),   # single curvature, equal end moments: 34 - 12
    (0.0, 34.0),    # one end pinned
    (0.5, 40.0),    # double curvature: 34 + 6 = 40
    (1.0, 40.0),    # 46, capped at 40
])
def test_the_limit_is_34_plus_12_m1_over_m2_at_most_40(ratio, limit):
    assert cs.slenderness_limit(ratio) == pytest.approx(limit)


def test_cm_is_one_in_single_curvature_and_small_in_double_curvature():
    assert cs.cm_factor(-1.0) == pytest.approx(1.0)
    assert cs.cm_factor(1.0) == pytest.approx(0.2)
    assert cs.cm_factor(0.0) == pytest.approx(0.6)


@pytest.mark.parametrize("psi_a, psi_b, k", [
    (1.0, 1.0, 0.80),            # 0.7 + 0.05 * 2 = 0.80 (< 0.85 + 0.05)
    (0.0, 0.0, 0.70),            # both ends fixed
    (1.0, 10.0, 0.90),           # 0.85 + 0.05 * 1 = 0.90 governs over 0.7 + 0.55
    (10.0, 10.0, 1.00),          # never above 1.0 for a braced column
    (1.0, math.inf, 0.90),       # a hinge at one end of a braced column: 0.85 + 0.05
    (math.inf, math.inf, 1.00),  # hinged at both ends
])
def test_braced_k_follows_the_alignment_chart_equations(psi_a, psi_b, k):
    assert cs.braced_k(psi_a, psi_b) == pytest.approx(k)


def test_minimum_moment_is_pu_times_15_plus_003_h():
    # 1000 kN on a 400 mm column: e = 15 + 12 = 27 mm -> 27 kN-m
    assert cs.minimum_moment(1000.0, 400.0) == pytest.approx(27.0)
    assert cs.minimum_moment(-200.0, 400.0) == 0.0  # tension: none


# --------------------------------------------------------------------------
# ONE AXIS
# --------------------------------------------------------------------------
def test_a_stocky_column_is_not_slender_and_keeps_its_moment():
    # k lu / r = 3000 / 120 = 25; double curvature limit 40
    result = cs.axis_result(100.0, -60.0, 800.0, SHORT, SECTION, "3", 0.6)
    assert result.slenderness == pytest.approx(25.0)
    assert not result.slender and result.delta == 1.0
    assert result.status == cs.NOT_SLENDER
    assert result.design == pytest.approx(100.0)


def test_the_same_column_is_slender_in_single_curvature():
    # equal end moments of the same sign: limit 22 < 25
    result = cs.axis_result(100.0, 100.0, 800.0, SHORT, SECTION, "3", 0.6)
    assert result.limit == pytest.approx(22.0)
    assert result.slender


def test_moment_magnification_hand_calc():
    """400 x 400, fc' 28, lu 6000, k 1.0, Pu 600 kN, single curvature 100 kN-m."""
    ec = 4700.0 * math.sqrt(28.0)
    ig = 400.0**4 / 12.0
    pc = math.pi**2 * 0.4 * ec * ig / (1.0 + 0.6) / 6000.0**2 / 1000.0   # kN
    delta = 1.0 / (1.0 - 600.0 / (0.75 * pc))
    result = cs.axis_result(100.0, 100.0, 600.0, LONG, SECTION, "3", 0.6)
    assert result.slenderness == pytest.approx(50.0)
    assert result.cm == pytest.approx(1.0)
    assert result.pc == pytest.approx(pc)
    assert result.delta == pytest.approx(delta)
    assert 1.2 < delta < 1.4
    assert result.design == pytest.approx(delta * 100.0)
    assert result.minimum == pytest.approx(600.0 * 27.0 / 1000.0)
    assert result.status == "Slender: moment magnified"


def test_double_curvature_can_leave_the_magnifier_at_one():
    # Cm = 0.6 - 0.4 * 1 = 0.2, so Cm / (1 - Pu / 0.75 Pc) < 1 and delta = 1
    geometry = cs.AxisGeometry(lu=6000.0, k=1.0)
    result = cs.axis_result(100.0, -100.0, 600.0, geometry, SECTION, "3", 0.6)
    assert result.slender                    # 50 > 40
    assert result.delta == pytest.approx(1.0)
    assert result.design == pytest.approx(100.0)


def test_the_minimum_moment_governs_a_small_end_moment():
    result = cs.axis_result(5.0, 3.0, 600.0, LONG, SECTION, "3", 0.6)
    assert result.minimum == pytest.approx(16.2)
    assert result.cm == pytest.approx(1.0)            # ACI 6.6.4.5.3 with M2,min
    assert result.design == pytest.approx(result.delta * 16.2)


def test_a_magnifier_above_14_fails():
    result = cs.axis_result(100.0, 100.0, 1500.0, LONG, SECTION, "3", 0.6)
    assert result.delta > 1.4
    assert result.status.startswith("FAIL") and "6.2.6" in result.status
    assert result.design == pytest.approx(1.4 * 100.0)   # kept finite for the report


def test_a_load_at_the_buckling_load_fails_as_unstable():
    result = cs.axis_result(100.0, 100.0, 5000.0, LONG, SECTION, "3", 0.6)
    assert math.isinf(result.delta)
    assert "0.75 Pc" in result.status


def test_response_spectrum_moments_are_taken_in_single_curvature():
    signed = cs.axis_result(100.0, -100.0, 600.0, SHORT, SECTION, "3", 0.6)
    unsigned = cs.axis_result(100.0, -100.0, 600.0, SHORT, SECTION, "3", 0.6,
                              unknown_curvature=True)
    assert signed.limit == pytest.approx(40.0) and not signed.slender
    assert unsigned.limit == pytest.approx(22.0) and unsigned.slender
    assert unsigned.cm == pytest.approx(1.0)


def test_tension_is_not_magnified():
    result = cs.axis_result(100.0, 100.0, -200.0, LONG, SECTION, "3", 0.6)
    assert not result.slender and result.status == cs.NO_COMPRESSION


def test_more_sustained_load_lowers_the_buckling_load():
    low = cs.axis_result(100.0, 100.0, 600.0, LONG, SECTION, "3", 0.2)
    high = cs.axis_result(100.0, 100.0, 600.0, LONG, SECTION, "3", 1.0)
    assert high.pc < low.pc and high.delta > low.delta


def test_a_rectangular_section_uses_the_dimension_in_the_bending_plane():
    section = cs.ColumnSection(width=400.0, depth=800.0, circular=False, fc=28.0)
    assert section.dimension("3") == 800.0 and section.dimension("2") == 400.0
    assert section.inertia("3") == pytest.approx(400.0 * 800.0**3 / 12.0)
    assert section.inertia("2") == pytest.approx(800.0 * 400.0**3 / 12.0)


# --------------------------------------------------------------------------
# A MEMBER'S FORCE TABLE
# --------------------------------------------------------------------------
def forces(combos: dict, length=6000.0, **extra) -> pd.DataFrame:
    """Column forces, compression positive: combo -> ((P, M2, M3) at I, (P, M2, M3) at J)."""
    rows = []
    for combo, ends in combos.items():
        for station, (p, m2, m3) in zip((0.0, length / 2, length),
                                        (ends[0], ends[0], ends[1])):
            rows.append({"UniqueName": "C1", "Combo": combo, "Station": station, "P": p,
                         "V2": 0.0, "V3": 0.0, "M2": m2, "M3": m3, **extra})
    return pd.DataFrame(rows)


def test_the_larger_end_moment_is_replaced_by_the_magnified_one():
    table = forces({"U1": ((600.0, 0.0, 80.0), (600.0, 0.0, 100.0))})
    out = cs.magnify_member(table, SECTION, {"2": LONG, "3": LONG})
    delta = cs.axis_result(80.0, 100.0, 600.0, LONG, SECTION, "3", 0.6).delta
    ends = out.forces.sort_values("Station")
    assert ends["M3"].iloc[-1] == pytest.approx(delta * 100.0)   # the J end governs
    assert ends["M3"].iloc[0] == pytest.approx(80.0)             # the smaller end is kept
    assert table["M3"].tolist() == [80.0, 80.0, 100.0]           # the input is not changed
    record = out.records[("U1", "J")]
    assert record["Mu3_analysis_kNm"] == pytest.approx(100.0)
    assert record["Slender_delta3"] == pytest.approx(delta)
    assert record["Slenderness_Check"] == "PASS - slender, moments magnified"
    assert out.slender and not out.failed


def test_the_sign_of_the_moment_is_kept():
    table = forces({"U1": ((600.0, 0.0, -100.0), (600.0, 0.0, -80.0))})
    out = cs.magnify_member(table, SECTION, {"2": LONG, "3": LONG})
    assert out.forces.sort_values("Station")["M3"].iloc[0] < -100.0


def test_a_stocky_member_is_returned_unchanged_with_a_stated_reason():
    table = forces({"U1": ((600.0, 20.0, 100.0), (600.0, -20.0, -90.0))}, length=3000.0)
    out = cs.magnify_member(table, SECTION, {"2": SHORT, "3": SHORT})
    pd.testing.assert_frame_equal(out.forces, table)
    record = out.records[("U1", "I")]
    assert record["Slender_Cm3"] == cs.NOT_SLENDER and record["Slender_delta3"] == 1.0
    assert record["Slenderness_Check"] == "PASS - not slender"
    assert not out.slender


def test_a_failing_combination_marks_the_member():
    table = forces({"U1": ((1500.0, 0.0, 100.0), (1500.0, 0.0, 100.0))})
    out = cs.magnify_member(table, SECTION, {"2": LONG, "3": LONG})
    assert out.failed
    assert out.records[("U1", "I")]["Slenderness_Check"].startswith("FAIL: SLENDERNESS")


def test_beta_dns_is_the_dead_load_share_when_the_table_has_it():
    table = forces({"U1": ((600.0, 0.0, 100.0), (600.0, 0.0, 100.0))}, P_sustained=450.0)
    out = cs.magnify_member(table, SECTION, {"2": LONG, "3": LONG})
    record = out.records[("U1", "I")]
    assert record["Slender_beta_dns"] == pytest.approx(0.75)
    assert record["Slender_beta_basis"] == "dead load share of Pu"
    assumed = cs.magnify_member(forces({"U1": ((600.0, 0.0, 100.0), (600.0, 0.0, 100.0))}),
                                SECTION, {"2": LONG, "3": LONG})
    assert assumed.records[("U1", "I")]["Slender_beta_dns"] == pytest.approx(0.6)
    assert assumed.records[("U1", "I")]["Slender_beta_basis"] == "assumed"


def test_spectral_combinations_are_flagged_by_the_table_or_by_their_eight_permutations():
    flagged = forces({"U1": ((600.0, 0.0, 100.0), (600.0, 0.0, -100.0))}, length=3000.0,
                     Spectral=True)
    out = cs.magnify_member(flagged, SECTION, {"2": SHORT, "3": SHORT})
    assert out.records[("U1", "I")]["Slender_limit3"] == pytest.approx(22.0)
    eight = pd.concat([
        forces({f"RS-{n}": ((600.0, 0.0, 100.0), (600.0, 0.0, -100.0))}, length=3000.0,
               Permutation=n) for n in range(1, 9)], ignore_index=True)
    out = cs.magnify_member(eight, SECTION, {"2": SHORT, "3": SHORT})
    assert out.records[("RS-3", "I")]["Slender_limit3"] == pytest.approx(22.0)


# --------------------------------------------------------------------------
# THE FRAME: lu AND k
# --------------------------------------------------------------------------
def frame(beams_at_mid_level_along_y=True) -> cs.FrameModel:
    """Two columns on top of each other, 3 m each; 300 x 600 beams 6 m long.

    Beams along X at the mid and top levels; along Y at the top level, and at
    the mid level only when asked.
    """
    section = cs.ColumnSection(500.0, 500.0, False, 28.0)
    columns = {
        "C1": cs.FrameColumn("C1", "j0", "j1", 3000.0, section, (1.0, 0.0)),
        "C2": cs.FrameColumn("C2", "j1", "j2", 3000.0, section, (1.0, 0.0)),
    }

    def beam(name, joint, far, direction):
        return cs.FrameBeam(name, joint, far, 6000.0, 300.0, 600.0, 28.0, direction)

    beams = {"BX1": beam("BX1", "j1", "x1", (1.0, 0.0)), "BX2": beam("BX2", "j2", "x2", (1.0, 0.0)),
             "BY2": beam("BY2", "j2", "y2", (0.0, 1.0))}
    if beams_at_mid_level_along_y:
        beams["BY1"] = beam("BY1", "j1", "y1", (0.0, 1.0))
    return cs.FrameModel(columns, beams)


def test_clear_height_is_the_storey_less_the_beam_at_the_top():
    model = frame()
    geometry = model.axis_geometry("C1", "3")   # bending about 3: beams along local 2 (X)
    assert geometry.lu == pytest.approx(3000.0 - 600.0)
    assert geometry.k <= 1.0 and "alignment chart" in geometry.k_basis


def test_k_comes_from_the_stiffness_ratios_at_the_two_ends():
    model = frame()
    ec = 4700.0 * math.sqrt(28.0)
    column = ec * 0.70 * 500.0**4 / 12.0 / 3000.0
    beam = ec * 0.35 * 300.0 * 600.0**3 / 12.0 / 6000.0
    geometry = model.axis_geometry("C1", "3")
    assert geometry.psi_bottom == pytest.approx(1.0)              # footing: fixed base
    assert geometry.psi_top == pytest.approx(2.0 * column / beam)  # two columns, one beam
    assert geometry.k == pytest.approx(cs.braced_k(1.0, 2.0 * column / beam))


def test_a_joint_with_no_beam_in_a_direction_is_not_a_brace_in_that_direction():
    model = frame(beams_at_mid_level_along_y=False)
    about_3 = model.axis_geometry("C1", "3")    # X beams at the mid level: braced there
    about_2 = model.axis_geometry("C1", "2")    # no Y beam at the mid level
    assert about_3.lu == pytest.approx(2400.0)
    assert about_2.lu == pytest.approx(6000.0 - 600.0)   # runs on to the top level
    assert model.axis_geometry("C2", "2").lu == pytest.approx(6000.0 - 600.0)


def test_a_free_top_gets_k_one_and_says_so():
    section = cs.ColumnSection(500.0, 500.0, False, 28.0)
    model = cs.FrameModel(
        {"C1": cs.FrameColumn("C1", "j0", "j1", 4000.0, section, (1.0, 0.0))}, {})
    geometry = model.axis_geometry("C1", "3")
    assert geometry.k == 1.0 and "no beam" in geometry.k_basis
    assert geometry.lu == pytest.approx(4000.0)


def test_a_beam_split_by_a_secondary_beam_counts_as_one_span():
    section = cs.ColumnSection(500.0, 500.0, False, 28.0)
    columns = {"C1": cs.FrameColumn("C1", "j0", "j1", 3000.0, section, (1.0, 0.0)),
               "C9": cs.FrameColumn("C9", "k0", "k1", 3000.0, section, (1.0, 0.0))}
    beams = {"B1": cs.FrameBeam("B1", "j1", "m", 3000.0, 300.0, 600.0, 28.0, (1.0, 0.0)),
             "B1A": cs.FrameBeam("B1A", "m", "k1", 3000.0, 300.0, 600.0, 28.0, (1.0, 0.0))}
    model = cs.FrameModel(columns, beams)
    ec = 4700.0 * math.sqrt(28.0)
    one_span = ec * 0.35 * 300.0 * 600.0**3 / 12.0 / 6000.0   # 6 m, not the 3 m piece
    assert model.beam_stiffness("j1", (1.0, 0.0)) == pytest.approx(one_span)


def test_without_joint_coordinates_the_length_and_k_one_are_used():
    geometry = cs.simple_geometry(3500.0, 500.0)
    assert geometry["3"].lu == pytest.approx(3000.0) and geometry["2"].k == 1.0
    assert "not available" in geometry["3"].k_basis
