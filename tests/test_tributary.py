"""
tests/test_tributary.py
=======================
Checks for etabs_api/workflows/tributary.py: geometric tributary areas of
columns and beams. No ETABS is needed. Lengths in mm, areas in m2.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows.tributary import (  # noqa: E402
    Floor,
    Frame,
    beam_tributary,
    column_tributary,
)

Z = 4000.0


def column(name, x, y, z0=0.0, z1=Z, bottom=None, top=None):
    return Frame(name, bottom or f"{name}b", top or f"{name}t", (x, y, z0), (x, y, z1))


def slab(name, x0, y0, x1, y1, z=Z, kpa=2.4):
    return Floor(name, z, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], kpa)


def test_four_columns_share_a_square_bay_in_quarters():
    columns = [column(f"C{i}", x, y) for i, (x, y) in
               enumerate([(0, 0), (8000, 0), (0, 8000), (8000, 8000)])]
    areas = column_tributary([slab("F1", 0, 0, 8000, 8000)], columns, spacing=100)
    for area in areas.values():
        assert area.area_m2 == pytest.approx(16.0, rel=0.01)
        assert area.levels == 1


def test_an_interior_column_takes_the_whole_middle_bay():
    columns = [column(f"C{x}-{y}", x, y) for x in (0, 8000, 16000) for y in (0, 8000, 16000)]
    areas = column_tributary([slab("F1", 0, 0, 16000, 16000)], columns, spacing=100)
    assert areas["C8000-8000"].area_m2 == pytest.approx(64.0, rel=0.01)
    assert areas["C0-0"].area_m2 == pytest.approx(16.0, rel=0.01)


def test_a_column_adds_the_area_of_the_column_above():
    lower = column("L", 0, 0, 0, Z, bottom="j0", top="j1")
    upper = column("U", 0, 0, Z, 2 * Z, bottom="j1", top="j2")
    floors = [slab("F1", -4000, -4000, 4000, 4000, Z),
              slab("F2", -4000, -4000, 4000, 4000, 2 * Z)]
    areas = column_tributary(floors, [lower, upper], spacing=100)
    assert areas["U"].area_m2 == pytest.approx(64.0, rel=0.01)
    assert areas["L"].area_m2 == pytest.approx(128.0, rel=0.01)
    assert (areas["U"].levels, areas["L"].levels) == (1, 2)


def test_a_level_without_a_floor_adds_no_level():
    lower = column("L", 0, 0, 0, Z, bottom="j0", top="j1")
    upper = column("U", 0, 0, Z, 2 * Z, bottom="j1", top="j2")
    areas = column_tributary([slab("F2", -4000, -4000, 4000, 4000, 2 * Z)], [lower, upper],
                             spacing=100)
    assert areas["L"].area_m2 == pytest.approx(64.0, rel=0.01)
    assert areas["L"].levels == 1


def test_heavy_floor_area_and_intensity():
    columns = [column("C1", 0, 0), column("C2", 8000, 0)]
    floors = [slab("OFFICE", 0, 0, 4000, 4000, kpa=2.4),
              slab("EE", 4000, 0, 8000, 4000, kpa=7.2)]
    areas = column_tributary(floors, columns, spacing=100)
    assert areas["C1"].heavy_m2 == pytest.approx(0.0)
    assert areas["C2"].heavy_m2 == pytest.approx(16.0, rel=0.01)
    assert areas["C2"].sources == {"EE"}
    assert areas["C1"].reducible_kpa == pytest.approx(2.4, rel=0.01)


def beam(name, x0, y0, x1, y1, i=None, j=None, depth=500.0):
    return Frame(name, i or f"{name}i", j or f"{name}j", (x0, y0, Z), (x1, y1, Z), depth)


def test_parallel_beams_take_strips_to_the_halfway_line():
    beams = [beam("B0", 0, 0, 8000, 0), beam("B1", 0, 3000, 8000, 3000),
             beam("B2", 0, 6000, 8000, 6000)]
    areas = beam_tributary([slab("F", 0, 0, 8000, 6000)], beams, set(), spacing=100)
    assert areas["B1"].area_m2 == pytest.approx(24.0, rel=0.02)  # 8 m x 3 m
    assert areas["B0"].area_m2 == pytest.approx(12.0, rel=0.02)


def test_a_girder_picks_up_half_of_a_beam_framing_into_it():
    # A girder along y = 0 in two segments meeting at joint "m"; a beam along
    # x = 4000 from the girder (joint "m") to a column at (4000, 6000).
    g1 = beam("G1", 0, 0, 4000, 0, i="c1", j="m", depth=600)
    g2 = beam("G2", 4000, 0, 8000, 0, i="m", j="c2", depth=600)
    b = beam("B", 4000, 0, 4000, 6000, i="m", j="c3", depth=400)
    areas = beam_tributary([slab("F", 0, 0, 8000, 6000)], [g1, g2, b], {"c1", "c2", "c3"},
                           spacing=100)
    own_g1, own_b = areas["G1"].own_m2, areas["B"].own_m2
    assert areas["B"].area_m2 == pytest.approx(own_b)
    # half of the beam goes to the joint, shared by the two girder segments
    assert areas["G1"].area_m2 == pytest.approx(own_g1 + own_b / 4)
