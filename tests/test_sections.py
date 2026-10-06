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
    # 300 x 1050 is below b/h = 0.3: the beam is made deeper and as wide as that depth needs
    assert grow_beam(g(300, 1000), "depth", RANGES, LIMITS) == g(400, 1050)


def test_the_width_share_of_the_engineer_is_kept_when_a_beam_is_resized():
    from dataclasses import replace

    wide = replace(LIMITS, beam_min_ratio=0.4)
    assert grow_beam(g(300, 700), "depth", RANGES, LIMITS) == g(300, 800)      # 0.375: fine at 0.3
    assert grow_beam(g(300, 700), "depth", RANGES, wide) == g(400, 800)        # 0.4 needs 320 mm
    assert grow_beam(g(400, 1000), "depth", RANGES, wide) == g(500, 1050)
    assert shrink_beam(g(400, 900), RANGES, min_ratio=0.4) == g(400, 800)      # depth first
    assert shrink_beam(g(400, 1000), {"G": {"width": [300, 600, 100], "depth": [1000, 1000, 100]}},
                       min_ratio=0.4) is None                                  # 300 / 1000 is below 0.4
    assert replace(LIMITS, beam_min_ratio=0.1).beam_min_ratio == 0.1           # never below the code:
    assert grow_beam(g(300, 1000), "depth", RANGES, replace(LIMITS, beam_min_ratio=0.1))         == g(400, 1050)                                                        # 0.3 still holds


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


def test_the_largest_column_side_also_cuts_the_setup_range():
    """A setup range that goes to 1000 must not offer more than the 800 the user typed."""
    limits = Limits(increment=100, beam_max_width=500, beam_max_depth=800, column_max=800)
    size = cr(600, 600)
    grown = []
    while True:
        size = grow_column(size, "side", RANGES, limits, along_depth=False)
        if size is None:
            break
        grown.append(size)
    assert max(max(s.width, s.depth) for s in grown) == 800
    assert grow_column(cr(800, 800), "square", RANGES, limits) is None
    # a column that is already larger keeps its size on offer and is not forced down
    assert grow_column(cr(900, 900), "square", RANGES, limits) is None


def test_the_largest_beam_size_also_cuts_the_setup_range():
    limits = Limits(increment=100, beam_max_width=500, beam_max_depth=800, column_max=800)
    size = g(300, 500)
    sizes = []
    while size is not None:
        sizes.append(size)
        size = grow_beam(size, "depth", RANGES, limits)
    assert max(s.depth for s in sizes) == 800 and max(s.width for s in sizes) <= 500


def test_the_column_side_ratio_is_the_users():
    square = Limits(increment=100, column_max=1200, column_max_ratio=1.0)
    assert grow_column(cr(500, 500), "side", RANGES, square, along_depth=True) == cr(600, 600)
    slim = Limits(increment=100, column_max=1200, column_max_ratio=3.0)
    assert grow_column(cr(400, 1000), "side", RANGES, slim, along_depth=True) == cr(400, 1100)
    default = Limits(increment=100, column_max=1200)
    grown = grow_column(cr(400, 800), "side", RANGES, default, along_depth=True)
    assert grown.depth == 900 and grown.width >= 450       # 2.0: the width follows


def test_a_column_does_not_shrink_below_the_smallest_side_asked():
    assert shrink_column(cr(600, 600), RANGES, min_side=500) in (cr(500, 600), cr(600, 500))
    assert shrink_column(cr(500, 500), RANGES, min_side=500) is None
    circle = Section("C", 600, 600, "C05", "G60", circular=True)
    assert shrink_column(circle, RANGES, min_side=600) is None
    assert shrink_column(circle, RANGES, min_side=500).depth == 500


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
