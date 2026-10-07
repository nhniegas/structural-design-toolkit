"""
tests/test_beam_deflection.py
=============================
Checks for design/beam_deflection.py against closed-form results.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design import beam_deflection as bd  # noqa: E402

L = 6000.0
X = np.linspace(0, L, 41)


def section(top=(2, 0, 0), bottom=(0, 0, 3), b=300.0, h=600.0, fc=28.0):
    zones = ("left", "mid", "right")
    return bd.BeamSection(b, h, fc, 40.0, 10.0, 20.0,
                          {z: list(top) for z in zones}, {z: list(bottom) for z in zones})


def test_cracked_inertia_of_a_singly_reinforced_section():
    b, d, area, n = 300.0, 450.0, 1500.0, 8.0
    c = (-n * area + math.sqrt((n * area) ** 2 + 2 * b * n * area * d)) / b
    expected = b * c**3 / 3 + n * area * (d - c) ** 2
    assert bd.cracked_inertia(b, d, area, 50.0, 0.0, n) == pytest.approx(expected)


def test_effective_inertia_is_gross_below_cracking():
    assert bd.effective_inertia(10.0, 20.0, 5e9, 1e9) == 5e9
    assert 1e9 < bd.effective_inertia(40.0, 20.0, 5e9, 1e9) < 5e9


def test_simple_span_uniform_load_matches_5wl4_over_384ei():
    w = 0.01  # kN/mm -> M in kN-mm; convert to kN-m below
    m = w * X * (L - X) / 2 / 1000.0
    ei = np.full_like(X, 3e13 / 1e6)
    delta = bd.deflection_profile(X, m, ei)
    expected = 5 * (w * 1000) * L**4 / (384 * 3e13)  # w in N/mm
    assert delta.max() == pytest.approx(expected, rel=2e-3)
    assert delta[0] == pytest.approx(0) and delta[-1] == pytest.approx(0)


@pytest.mark.parametrize("root", ["start", "end"])
def test_cantilever_tip_matches_wl4_over_8ei(root):
    w = 0.01
    distance = (L - X) if root == "start" else X
    m = -w * distance**2 / 2 / 1000.0
    ei = np.full_like(X, 3e13 / 1e6)
    delta = bd.deflection_profile(X, m, ei, root)
    tip = delta[-1] if root == "start" else delta[0]
    assert tip == pytest.approx((w * 1000) * L**4 / (8 * 3e13), rel=2e-3)


def test_uncracked_beam_uses_ig_and_checks_against_limits():
    # small moments: no cracking, so Ie = Ig everywhere
    w_dead, w_live = 0.002, 0.001
    shape = X * (L - X) / 2 / 1000.0
    moments = {bd.COMBO_DEAD: w_dead * shape, bd.COMBO_FULL: (w_dead + w_live) * shape,
               bd.COMBO_SUSTAINED: (w_dead + 0.25 * w_live) * shape}
    result = bd.beam_deflection(section(), X, moments)
    sec = section()
    ig = sec.width * sec.depth**3 / 12
    ec = 4700 * math.sqrt(sec.fc)
    assert result.ie["mid"] == pytest.approx(ig)
    live = 5 * (w_live * 1000) * L**4 / (384 * ec * ig)
    assert result.live == pytest.approx(live, rel=5e-3)
    assert result.live_limit == pytest.approx(L / 360)
    assert result.long_limit == pytest.approx(L / 480)
    assert result.roof is None
    assert result.passed


def test_compression_steel_lowers_the_long_term_factor():
    moments = {c: np.zeros_like(X) for c in (bd.COMBO_DEAD, bd.COMBO_FULL, bd.COMBO_SUSTAINED)}
    without = bd.beam_deflection(section(top=(0, 0, 0)), X, moments)
    with_top = bd.beam_deflection(section(top=(4, 0, 0)), X, moments)
    assert without.lam == pytest.approx(2.0)
    assert with_top.lam < 2.0


def test_cracked_heavy_load_fails():
    shape = X * (L - X) / 2 / 1000.0
    moments = {bd.COMBO_DEAD: 0.03 * shape, bd.COMBO_FULL: 0.05 * shape,
               bd.COMBO_SUSTAINED: 0.035 * shape}
    result = bd.beam_deflection(section(bottom=(0, 0, 2), h=400.0), X, moments)
    assert result.ie["mid"] < 300 * 400**3 / 12
    assert not result.passed


def connectivity(rows):
    return pd.DataFrame(rows, columns=["UniqueName", "DesignType", "UniquePtI", "UniquePtJ",
                                       "Length"])


def test_segments_of_a_girder_line_join_into_one_span():
    # columns at a and d; the girder 2GX-1 runs a-b-c-d, beams frame in at b and c
    conn = connectivity([
        ("C1", "Column", "a0", "a", 3000), ("C2", "Column", "d0", "d", 3000),
        ("2GX-1", "Beam", "a", "b", 3000), ("2GX-1A", "Beam", "c", "b", 3000),  # reversed
        ("2GX-1B", "Beam", "c", "d", 3000),
        ("2BY-1", "Beam", "b", "e", 4000), ("2BY-2", "Beam", "c", "f", 4000),
    ])
    spans = {tuple(s.members): s for s in bd.beam_spans(conn, ["2GX-1", "2GX-1A", "2GX-1B"])}
    assert set(spans) == {("2GX-1", "2GX-1A", "2GX-1B")} or set(spans) == {
        ("2GX-1B", "2GX-1A", "2GX-1")}
    span = next(iter(spans.values()))
    assert span.start_supported and span.end_supported
    assert span.offsets == [0.0, 3000.0, 6000.0]
    middle = span.members.index("2GX-1A")
    assert span.reversed[middle] != span.reversed[0]


def test_a_free_end_makes_a_cantilever_span():
    conn = connectivity([("C1", "Column", "a0", "a", 3000), ("2GY-9", "Beam", "a", "tip", 2000)])
    span = bd.beam_spans(conn, ["2GY-9"])[0]
    assert span.start_supported and not span.end_supported


def test_a_split_span_deflects_like_the_whole_span():
    w = 0.002
    whole = np.linspace(0, L, 41)
    shape = whole * (L - whole) / 2 / 1000.0
    moments = {bd.COMBO_DEAD: w * shape, bd.COMBO_FULL: 2 * w * shape,
               bd.COMBO_SUSTAINED: 1.25 * w * shape}
    one = bd.span_deflection([bd.SpanPart("A", section(), whole, moments)])["A"]
    halves = []
    for name, keep in (("L", whole <= L / 2), ("R", whole >= L / 2)):
        halves.append(bd.SpanPart(name, section(), whole[keep],
                                  {c: m[keep] for c, m in moments.items()}))
    split = bd.span_deflection(halves)
    assert max(split["L"].live, split["R"].live) == pytest.approx(one.live, rel=1e-6)
    assert split["L"].span == pytest.approx(L)


def test_a_line_that_ends_on_an_unsplit_girder_is_supported_there_not_a_cantilever():
    """Two pieces of a beam line from a column to a girder that ETABS has not
    split where the line meets it: no member of the girder ends at that joint."""
    rows = [("C1", "Column", "c0", "a"), ("C2", "Column", "h1", "g1"), ("C3", "Column", "h2", "g2"),
            ("2BY-3", "Beam", "a", "m"), ("2BY-3A", "Beam", "m", "e"),
            ("2BX-9", "Beam", "m", "s"),                   # a beam framing in at mid length
            ("2GX-1", "Beam", "g1", "g2")]                 # the girder under the end e
    table = pd.DataFrame(rows, columns=["UniqueName", "DesignType", "UniquePtI", "UniquePtJ"])
    table["Length"] = [3000.0, 3000.0, 3000.0, 3000.0, 3000.0, 2000.0, 8000.0]
    points = pd.DataFrame({
        "UniqueName": ["c0", "a", "m", "e", "s", "g1", "g2", "h1", "h2"],
        "X": [0.0, 0.0, 0.0, 0.0, 2000.0, -4000.0, 4000.0, -4000.0, 4000.0],
        "Y": [0.0, 0.0, 3000.0, 6000.0, 3000.0, 6000.0, 6000.0, 6000.0, 6000.0],
        "Z": [0.0, 3000.0, 3000.0, 3000.0, 3000.0, 3000.0, 3000.0, 0.0, 0.0]})
    line = ["2BY-3", "2BY-3A"]

    def ends(coordinates=None):
        span = next(s for s in bd.beam_spans(table, line, coordinates)
                    if sorted(s.members) == line)
        return sorted([span.start_supported, span.end_supported])

    assert ends() == [False, True]             # counting the members at the joint: a cantilever
    assert ends(points) == [True, True]        # with the coordinates: supported at both ends

    # a line that truly ends in the air stays a cantilever
    free = points.copy()
    free.loc[free["UniqueName"].isin(["g1", "g2"]), "Y"] = 9000.0
    assert ends(free) == [False, True]


def test_the_part_of_a_cantilever_deflection_that_its_support_gives_is_told_apart():
    """A rigid rotation of the support moves the tip whatever the section: that part of the
    ratio is what no size takes away (the design loop asks it before growing a beam)."""
    w_dead, w_live = 0.004, 0.002
    reach = X                                              # fixed at the start
    shape = -(L - X) ** 2 / 2 / 1000.0
    moments = {bd.COMBO_DEAD: w_dead * shape, bd.COMBO_FULL: (w_dead + w_live) * shape,
               bd.COMBO_SUSTAINED: (w_dead + 0.25 * w_live) * shape}
    alone = bd.beam_deflection(section(), X, moments, cantilever_root="start")
    assert alone.support == 0.0                            # the support does not rotate
    tip = {bd.COMBO_DEAD: 20.0, bd.COMBO_FULL: 32.0, bd.COMBO_SUSTAINED: 23.0}   # mm at the tip
    turned = bd.beam_deflection(section(), X, moments, cantilever_root="start",
                                root_rotation=tip)
    live_part = (32.0 - 20.0) / (L / 360)
    long_part = (turned.lam * 23.0 + 32.0 - 23.0) / (L / bd.LIMIT_DAMAGED)
    assert turned.support == pytest.approx(max(live_part, long_part), rel=1e-6)
    assert turned.ratio > turned.support > 0               # the bending comes on top of it
    assert turned.ratio - turned.support == pytest.approx(alone.ratio, rel=0.05)
    assert reach[-1] == L
    spans = bd.beam_deflection(section(), X, {k: -v for k, v in moments.items()})
    assert spans.support == 0.0                            # a span between supports has none
