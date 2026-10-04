"""Tests for the biaxial interaction surface and the demand hull (design/column_interaction.py)."""

import math

import numpy as np
import pandas as pd
import pytest

from design import column_designer_aci318 as cd
from design import column_interaction as ci


def section(width=600, depth=800, diameter=np.nan, bars=16):
    row = pd.Series({"UniqueName": "C1", "Width": width, "Depth": depth, "Diameter": diameter,
                     "f'c": 27.6, "fy": 414, "fys": 414})
    return cd._build_column_section(row, bars, 25, 12, 40, True)


@pytest.fixture(scope="module")
def rectangle():
    engine, sec = section()
    return engine, sec, ci.build_surface(sec, engine)


def test_surface_matches_the_exact_solver(rectangle):
    engine, sec, surface = rectangle
    concrete = cd._concrete_section_for(sec)
    angles = np.linspace(-math.pi, math.pi, ci.N_ANGLES, endpoint=False)
    for k in (0, 18, 30, 54):
        for j in (15, 45, 70):
            pn = surface.p[k, j] / surface.phi[k, j]
            exact, _ = cd._fast_rectangular_block_capacity(concrete, angles[k], pn)
            mx = surface.mx[k, j] / surface.phi[k, j]
            my = surface.my[k, j] / surface.phi[k, j]
            assert exact.m_x == pytest.approx(mx, rel=1e-4, abs=1e3)
            assert exact.m_y == pytest.approx(my, rel=1e-4, abs=1e3)


def test_axial_limits(rectangle):
    engine, sec, surface = rectangle
    steel = sum(b.calculate_area() for b in cd._concrete_section_for(sec).reinf_geometries_lumped)
    po = 0.85 * 27.6 * (600 * 800 - steel) + 414 * steel
    assert surface.p_cap == pytest.approx(0.65 * 0.80 * po, rel=1e-3)
    _, _, _, old_cap, _ = engine.solve_moment_capacity(sec, axial_load=0.0, bending_angle=0.0)
    assert surface.p_cap == pytest.approx(old_cap)
    assert surface.p_tension == pytest.approx(0.90 * 414 * steel, rel=1e-3)
    assert surface.capacity(surface.p_cap * 1.01, 1e8, 0)[0] == 0.0


def test_capacity_is_symmetric_for_a_symmetric_section(rectangle):
    _, _, surface = rectangle
    for pu in (0.0, 2e6, 5e6):
        plus = surface.capacity(pu, 1e8, 0)[0]
        minus = surface.capacity(pu, -1e8, 0)[0]
        assert plus == pytest.approx(minus, rel=5e-3)


def test_design_point_uses_phi_pn_equal_to_pu(rectangle):
    """In the compression-controlled range the capacity at Pu is found where
    phi Pn = Pu, so it is lower than phi times Mn at Pn = Pu."""
    engine, sec, surface = rectangle
    pu = 0.6 * surface.p_cap
    on_surface, phi = surface.capacity(pu, 1e8, 0)
    _, old_design, _, _, _ = engine.solve_moment_capacity(sec, axial_load=pu, bending_angle=0.0)
    assert phi == pytest.approx(0.65, abs=1e-6)
    assert on_surface < old_design


def test_hull_decides_the_worst_utilization(rectangle):
    _, _, surface = rectangle
    rng = np.random.default_rng(1)
    points = np.column_stack([rng.uniform(0, 4e6, 200), rng.normal(0, 2e8, 200),
                              rng.normal(0, 1e8, 200)])
    hull = ci.hull_vertices(points)
    assert len(hull) < len(points)
    worst_all = max(surface.utilization(*p) for p in points)
    worst_hull = max(surface.utilization(*points[i]) for i in hull)
    assert worst_hull == pytest.approx(worst_all)


def test_hull_of_coplanar_demands_uses_the_varying_axes():
    points = np.column_stack([np.linspace(0, 1e6, 30), np.sin(np.arange(30)) * 1e8, np.zeros(30)])
    hull = ci.hull_vertices(points)
    assert 3 <= len(hull) < 30


def test_circular_section_and_cache():
    engine, sec = section(width=0, depth=0, diameter=700, bars=12)
    ci.clear_cache()
    first = ci.surface_for(engine, sec, None)
    assert ci.surface_for(engine, sec, None) is first
    assert first.capacity(1e6, 0, 2e8)[0] == pytest.approx(first.capacity(1e6, 2e8, 0)[0], rel=2e-2)


@pytest.mark.parametrize("axial, m2, m3", [(2e6, 0.0, 1.0), (2e6, 1.0, 0.0), (0.0, 0.0, 1.0),
                                           (5e6, 1.0, 0.0)])
def test_nominal_capacity_matches_the_exact_solver(rectangle, axial, m2, m3):
    engine, sec, surface = rectangle
    theta = cd._section_bending_angle(m2, m3)
    old, _, _, _, _ = engine.solve_moment_capacity(sec, axial_load=axial, bending_angle=theta)
    new = surface.nominal_capacity(axial, *ci.demand_moments(m2, m3))
    # the grid's chords lie inside the convex surface: never above, at most 0.3 % below
    assert old * (1 - 3e-3) <= new <= old * (1 + 1e-6)


@pytest.mark.parametrize("axial", [-1e6, 0.0, 2e6, 4e6, 6e6])
def test_fast_nominal_curve_matches_the_full_lookup(rectangle, axial):
    _, _, surface = rectangle
    for direction in ((1e6, 0.0), (0.0, 1e6), (7e5, 7e5)):
        full = surface.nominal_capacity(axial, *direction)
        assert surface.nominal_capacity_fast(axial, *direction) == pytest.approx(full, rel=2e-3)
