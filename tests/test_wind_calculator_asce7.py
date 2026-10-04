"""Unit tests for design/wind_calculator_directional_asce7.py  (ASCE 7, MWFRS).

HOW TO RUN (from the project root):
    python -m pytest tests/test_wind_calculator_asce7.py -v

Expected numbers are HAND CALCS written out in each test, or BEHAVIOUR rules.
"""

import math

import pytest

from design.wind_calculator_directional_asce7 import (
    KZ_TABLE,
    WindLoadCalculatorDirectionalASCE7,
)


def make_calculator(**changes) -> WindLoadCalculatorDirectionalASCE7:
    """A 20 m x 10 m gable building, ridge along L, eave at 6 m."""
    inputs = dict(
        building_class="Risk Category II",
        basic_wind_speed=60.0,
        enclosure_class="Enclosed Buildings",
        exposure_category="C",
        wind_dir_factor=0.85,
        topographic_factor=1.0,
        ground_elevation_factor=1.0,
        gust_effect_factor=0.85,
        l_input=20.0,
        b_input=10.0,
        ridge_direction_input="L",
        raw_heights="3",
        eave_height=6.0,
        apex_height=8.0,
    )
    inputs.update(changes)
    return WindLoadCalculatorDirectionalASCE7(**inputs)


def apex_for_slope(degrees: float, eave: float = 6.0, half_width: float = 5.0) -> float:
    """Apex height that gives the roof slope for wind normal to the ridge."""
    return eave + half_width * math.tan(math.radians(degrees))


def windward_roof(calculator) -> dict:
    payload = calculator.roof_cp_payload_normal
    return payload.loc[payload["Surface"] == "Windward roof"].iloc[0].to_dict()


# --------------------------------------------------------------------------
# VELOCITY PRESSURE
# --------------------------------------------------------------------------
def test_velocity_pressure_follows_the_asce_equation():
    """HAND CALC: q = 0.613 * Kd * Kzt * Ke * V^2 = 0.613 * 0.85 * 60^2 = 1875.78 Pa."""
    calculator = make_calculator().calculate()
    assert calculator.vel_pres == pytest.approx(0.613 * 0.85 * 60.0**2)
    assert calculator.vel_pres == pytest.approx(1875.78, abs=0.01)


def test_kz_is_interpolated_between_tabulated_heights():
    """HAND CALC: Exposure C, 5.35 m is midway between 4.6 m (0.85) and 6.1 m (0.90)."""
    kz = WindLoadCalculatorDirectionalASCE7.interpolate_table_value(KZ_TABLE, "C", 5.35)
    assert kz == pytest.approx(0.875)


def test_kz_table_uses_18_3_m_for_the_60_ft_row():
    assert 18.3 in KZ_TABLE.index
    assert KZ_TABLE.loc[18.3, "C"] == 1.13


def test_internal_pressure_coefficient_follows_the_enclosure_class():
    enclosed = make_calculator().calculate()
    partially = make_calculator(enclosure_class="Partially Enclosed Buildings").calculate()
    assert (enclosed.gcpi_pos, enclosed.gcpi_neg) == (0.18, -0.18)
    assert (partially.gcpi_pos, partially.gcpi_neg) == (0.55, -0.55)


# --------------------------------------------------------------------------
# ROOF PRESSURE COEFFICIENTS
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slope, expected", [(60.0, 0.6), (70.0, 0.7), (80.0, 0.8)])
def test_steep_windward_roofs_use_cp_equal_to_0_01_theta(slope, expected):
    """ASCE 7 Fig. 27.3-1: for slopes of 60 degrees or more, Cp = 0.01 * theta."""
    roof = windward_roof(make_calculator(apex_height=apex_for_slope(slope)).calculate())
    assert roof["C_p_1"] == pytest.approx(expected, abs=0.005)
    assert roof["C_p_2"] == pytest.approx(expected, abs=0.005)


def test_low_slope_roof_uses_the_stepped_distance_zones():
    """BEHAVIOUR: below 10 degrees the roof is split into distance zones from the edge."""
    calculator = make_calculator(apex_height=apex_for_slope(5.0)).calculate()
    surfaces = calculator.roof_cp_payload_normal["Surface"].tolist()
    assert surfaces[0] == "Roof (0 to h/2)"
    assert "Windward roof" not in surfaces


def test_leeward_wall_cp_follows_the_plan_ratio():
    """HAND CALC: L/B = 2 gives Cp = -0.3 and L/B = 0.5 gives Cp = -0.5 (Fig. 27.3-1)."""
    walls = make_calculator().calculate().wall_cp_table
    leeward = walls.loc[walls["Wind direction"].isin(["Normal to ridge", "Parallel to ridge"])]
    assert sorted(leeward["Cp"].tolist()) == [-0.5, -0.3]


# --------------------------------------------------------------------------
# NET PRESSURES
# --------------------------------------------------------------------------
def test_windward_wall_pressure_is_q_g_cp_minus_internal_pressure():
    """HAND CALC: p = qz*G*Cp - qh*GCpi for the first windward-wall row."""
    calculator = make_calculator().calculate()
    row = calculator.mwfrs_normal_summary.iloc[0]
    profile = calculator.velocity_pressure_table
    q_h = float(profile.loc[profile["Type"] == "Mean Roof Height", "qz (Pa)"].iloc[0])
    expected = row["q (Pa)"] * 0.85 * 0.8 - q_h * 0.18
    assert row["Surface"] == "Windward wall"
    assert row["Net (+GCpi)"] == pytest.approx(expected, abs=0.02)


# --------------------------------------------------------------------------
# INPUT CHECKS
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "changes, message",
    [
        ({"exposure_category": "X"}, "Exposure Category"),
        ({"ridge_direction_input": "Q"}, "Direction of Ridge"),
        ({"apex_height": 1.0}, "Apex height"),
        ({"l_input": 0.0}, "L and B"),
        ({"enclosure_class": "Tent"}, "Enclosure Classification"),
    ],
)
def test_invalid_inputs_are_rejected_with_a_message_naming_the_input(changes, message):
    with pytest.raises(ValueError, match=message):
        make_calculator(**changes).calculate()


def test_roof_cp_table_35_and_45_degrees_follow_asce7_fig_27_4_1():
    """ASCE 7-10 Fig. 27.4-1, windward, theta = 35 deg: h/L <= 0.25: 0.0 and 0.4;
    h/L = 0.5: -0.2 and 0.3; h/L >= 1.0: -0.2 and 0.2. At 45 deg, h/L <= 0.25: 0.4."""
    from design.wind_calculator_directional_asce7 import TABLE_ROOF_OVER_10 as table

    assert table[35].tolist() == [0.0, 0.4, -0.2, 0.3, -0.2, 0.2]
    assert table[45].tolist()[:2] == [0.4, 0.4]
