"""
tests/test_sections.py
======================
Checks for etabs_api/workflows/sections.py: section names and the next size
up or down. No ETABS is needed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows.sections import (  # noqa: E402
    Limits,
    Section,
    depth_along_x,
    grow_beam,
    grow_column,
    has_range,
    parse_section,
    shrink_beam,
    shrink_column,
)

RANGES = {
    "G": {"width": [300, 600, 100], "depth": [500, 1000, 100]},
    "B": {"width": [200, 400, 100], "depth": [400, 800, 100]},
    "FTB": {"width": [], "depth": []},
    "CR": {"size": [400, 1000, 100]},
    "C": {"diameter": [500, 900, 100]},
}
LIMITS = Limits(increment=50, beam_max_width=800, beam_max_depth=1200, column_max=1200)


def g(width, depth):
    return Section("G", width, depth, "C05", "G60")


def cr(width, depth):
    return Section("CR", width, depth, "C05", "G60")


@pytest.mark.parametrize("name, expected", [
    ("G_300X500_C05_G60", Section("G", 300, 500, "C05", "G60")),
    ("FTB_400X600_C04_G60", Section("FTB", 400, 600, "C04", "G60")),
    ("CR_400X500_C05_G60", Section("CR", 400, 500, "C05", "G60")),
    ("C_800X600_C05_G60", Section("CR", 800, 600, "C05", "G60")),  # legacy rectangular
    ("C_600_C05_G60", Section("C", 600, 600, "C05", "G60", circular=True)),
    ("W14X90", None),
])
def test_parse_section(name, expected):
    assert parse_section(name) == expected
    if expected:
        assert parse_section(expected.name) == expected


def test_beam_grows_in_depth_first_then_width():
    assert grow_beam(g(300, 500), "depth", RANGES, LIMITS) == g(300, 600)
    assert grow_beam(g(300, 500), "width", RANGES, LIMITS) == g(400, 500)


def test_beam_past_the_range_grows_by_the_increment_to_the_maximum():
    assert grow_beam(g(400, 1000), "depth", RANGES, LIMITS) == g(400, 1050)
    assert grow_beam(g(800, 1200), "depth", RANGES, LIMITS) is None


def test_beam_widens_when_deepening_would_break_the_ratio():
    # 300 x 1050 is below b/h = 0.3: the beam widens first, then deepens next time
    assert grow_beam(g(300, 1000), "depth", RANGES, LIMITS) == g(400, 1000)


def test_beam_widening_deepens_when_width_would_pass_depth():
    assert grow_beam(g(500, 500), "width", RANGES, LIMITS) == g(600, 600)


def test_beam_shrinks_depth_first_and_respects_the_minimum_depth():
    assert shrink_beam(g(300, 700), RANGES) == g(300, 600)
    assert shrink_beam(g(300, 700), RANGES, min_depth=650) is None  # 600 is under 650
    assert shrink_beam(g(300, 500), RANGES) is None


def test_column_grows_square_or_along_one_side():
    assert grow_column(cr(400, 400), "square", RANGES, LIMITS) == cr(500, 500)
    assert grow_column(cr(400, 600), "square", RANGES, LIMITS) == cr(500, 700)
    assert grow_column(cr(400, 400), "side", RANGES, LIMITS, along_depth=True) == cr(400, 500)
    assert grow_column(cr(400, 400), "side", RANGES, LIMITS, along_depth=False) == cr(500, 400)
    # 400 x 900 would break the side ratio: the width grows with it
    grown = grow_column(cr(400, 800), "side", RANGES, LIMITS, along_depth=True)
    assert grown.depth == 900 and grown.width >= 450


def test_column_shrinks_to_the_largest_smaller_size():
    assert shrink_column(cr(500, 500), RANGES) == cr(400, 500) or \
        shrink_column(cr(500, 500), RANGES) == cr(500, 400)
    assert shrink_column(cr(400, 400), RANGES) is None


def test_circular_column_grows_by_diameter():
    c = Section("C", 600, 600, "C05", "G60", circular=True)
    assert grow_column(c, "square", RANGES, LIMITS).depth == 700


@pytest.mark.parametrize("angle, along", [(0, True), (180, True), (90, False), (270, False),
                                          (45, None)])
def test_depth_direction_from_the_local_axis_angle(angle, along):
    assert depth_along_x(angle) is along


def test_family_without_a_range_is_reported():
    assert not has_range(RANGES, "FTB")
    assert has_range(RANGES, "G") and has_range(RANGES, "CR")
