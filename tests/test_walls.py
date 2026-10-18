"""
tests/test_walls.py
===================
Structural walls (design/concrete/walls.py, etabs_api/workflows/wall_design.py):
the assembled section, its strength against hand values, shear, minimum steel,
boundary elements, and the statics of the leg forces. No ETABS.
"""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design.concrete import walls as w  # noqa: E402

FC, FY = 28.0, 414.0


def planar(length=3000.0, thickness=250.0, spacing=200.0):
    return w.WallSection([w.LegShape("W1A", 0, 0, length, 0, thickness)], FC, FY, 40.0, 16.0,
                         spacing, 2)


def test_pure_axial_strength_is_the_code_cap():
    s = planar()
    po = 0.85 * FC * (s.area - s.steel) + FY * s.steel
    assert min(p for p, _ in s.interaction(90.0)) == pytest.approx(-0.80 * 0.65 * po / 1e3)


def test_in_plane_bending_agrees_with_cardenas():
    """Cardenas (1973) for walls with uniformly distributed steel, no axial load:
    Mn = 0.5 As fy lw (1 - c/lw), c/lw = ω / (2ω + 0.85 β1). It leaves out the
    compression steel, so the strain compatibility value is a little higher."""
    s = planar()
    omega = s.steel * FY / (3000 * 250 * FC)
    c_lw = omega / (2 * omega + 0.85 * 0.85)
    hand = 0.9 * 0.5 * s.steel * FY * 3000 * (1 - c_lw) / 1e6
    got = s.capacity_at(90.0, 0.0, 1.0)
    assert hand <= got <= 1.03 * hand


def test_the_weak_axis_reaches_tension_too():
    s = planar(thickness=250.0)
    ps = [p for p, _ in s.interaction(0.0)]
    assert max(ps) > 0 and s.capacity_at(0.0, -500.0, 1.0) > 0


def test_an_l_section_has_its_centroid_and_principal_axes():
    s = w.WallSection([w.LegShape("A", 0, 0, 6000, 0, 250), w.LegShape("B", 0, 0, 0, 5000, 250)],
                      FC, FY, 40.0, 16.0, 200.0, 2)
    # the corner is counted once: area = 6000 x 250 + (5000 - 125) x 250 + its half overlap
    assert s.area == pytest.approx(250 * (6000 + 5000) - 125 * 250, rel=0.01)
    assert 1000 < s.xc < 2500 and 500 < s.yc < 1500
    assert not math.isclose(s.principal_angle() % 90.0, 0.0, abs_tol=1.0)   # skewed axes


def test_alpha_c_follows_hw_over_lw():
    assert w.alpha_c(1.0) == 0.25 and w.alpha_c(3.0) == 0.17
    assert w.alpha_c(1.75) == pytest.approx(0.21)


def test_special_shear_steel_by_hand():
    # 5000 x 250, Vu 2500 kN, hw/lw 2.3: Vn = Vu / 0.75 = 3333 kN
    need = w.special_shear_steel(2500.0, 5000.0, 250.0, FC, FY, 2.3)
    acv = 5000 * 250
    expected = (2500e3 / 0.75 / acv - 0.17 * math.sqrt(FC)) / FY
    assert need["rho_t"] == pytest.approx(expected) and need["phi"] == 0.75 and need["fits"]
    # 421.2.4.1: with the shear at Mn high, φ = 0.60 can need less steel
    low = w.special_shear_steel(2500.0, 5000.0, 250.0, FC, FY, 2.3, v_at_mn=6000.0)
    assert low["phi"] == 0.60 and low["Vn_needed"] == pytest.approx(2500 / 0.6)


def test_minimum_ratios():
    # a lightly loaded special wall falls back to Table 411.6.1
    assert w.minimum_ratios(True, 10.0, 5000, 250, FC, FY, 16, 2.5) == (0.0015, 0.0025)
    assert w.minimum_ratios(True, 2000.0, 5000, 250, FC, FY, 16, 2.5) == (0.0025, 0.0025)


def test_boundary_element_checks_by_hand():
    disp = w.boundary_displacement(c=900.0, lw=5000.0, hw=11500.0, delta_u=120.0)
    assert disp["limit"] == pytest.approx(5000 / (600 * 120 / 11500)) and disp["needed"]
    assert disp["length"] == pytest.approx(max(900 - 500, 450))
    stress = w.boundary_stress(-2000.0, 8000.0, 1.25e6, 250 * 5000 ** 3 / 12, 2500.0, FC)
    assert stress["stress"] == pytest.approx(2000e3 / 1.25e6 + 8e9 * 2500 / (250 * 5000 ** 3 / 12))
    assert stress["needed"]
    assert w.round_up(437.0, 50.0) == 450.0


def test_a_story_is_designed_until_it_passes():
    legs = [w.LegShape("W1A", 0, 0, 5000, 0, 250)]
    forces = [w.StoryForces("U1", -2000, 0, 8000, {"W1A": 900}),
              w.StoryForces("U2", -1000, 0, -8000, {"W1A": 900})]
    d = w.design_story("GF", legs, forces, 11500, {"X": 120, "Y": 120},
                       w.WallInputs(True, FC, FY, FY, 40, 16, 12))
    assert d.passed and 0.9 < d.pm_ratio <= 1.0 and d.vertical_spacing < 450
    assert d.horizontal["W1A"][0] == 0.0025


def test_leg_forces_are_summed_to_the_wall_centroid():
    """The statics of wall_design.assemble: two legs along X and Y, each with P only;
    the moments at the centroid come from their offsets."""
    from etabs_api.workflows import wall_design as wd
    from etabs_api.workflows.wall_piers import Leg

    rows = pd.DataFrame([
        {"Story": "GF", "Pier": "A", "OutputCase": "U", "StepType": "", "Location": "Bottom",
         "P": -100e3, "V2": 10e3, "V3": 0.0, "M2": 0.0, "M3": 0.0},
        {"Story": "GF", "Pier": "B", "OutputCase": "U", "StepType": "", "Location": "Bottom",
         "P": -100e3, "V2": 20e3, "V3": 0.0, "M2": 0.0, "M3": 0.0}])
    props = pd.DataFrame([
        {"Story": "GF", "Pier": "A", "AxisAngle": 0, "CGBotX": 3000, "CGBotY": 0,
         "WidthBot": 6000, "ThickBot": 250},
        {"Story": "GF", "Pier": "B", "AxisAngle": 90, "CGBotX": 0, "CGBotY": 2500,
         "WidthBot": 5000, "ThickBot": 250}])
    legs = [Leg("GF", [], 0, 0, 6000, 0, 250, "A"), Leg("GF", [], 0, 0, 0, 5000, 250, "B")]
    f = wd.assemble(rows, props, legs, "GF")[0]
    xc = (3000 * 6000) / 11000
    yc = (2500 * 5000) / 11000
    assert f.p == pytest.approx(-200.0)
    # Mx = -Σ (y - yc) P, My = Σ (x - xc) P (the convention checked against ETABS)
    assert f.mx == pytest.approx(-((0 - yc) * -100e3 + (2500 - yc) * -100e3) / 1e6)
    assert f.my == pytest.approx(((3000 - xc) * -100e3 + (0 - xc) * -100e3) / 1e6)
    assert f.leg_shear == {"A": 10.0, "B": 20.0}
