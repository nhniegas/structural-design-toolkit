"""Where a beam line is supported, read from its shear (design/beam_deflection.py)."""
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
B, H, FC = 300.0, 900.0, 28.0      # deep enough to stay uncracked under these loads
EI = 4700.0 * math.sqrt(FC) * B * H**3 / 12.0      # N-mm2, uncracked
W_DEAD, W_LIVE = 0.002, 0.001                       # kN/mm (2 and 1 kN/m): the beam stays uncracked


def rows(*names):
    out = []
    for name in names:
        for face, top, bottom in (("TOP", 2, 0), ("BOTTOM", 0, 3)):
            row = {"UniqueName": name, "Story": "2F", "Face": face,
                   "SupportStatus": "Supported Both Ends", "Width": B, "Depth": H, "f'c": FC,
                   "cc": 40.0, "ds": 10.0, "dm": 20.0, "Design_Status": "OK"}
            for zone in ("left", "mid", "right"):
                for layer in (1, 2, 3):
                    row[f"n_{zone}_L{layer}"] = 0
                row[f"n_{zone}_L1"] = top
                row[f"n_{zone}_L3"] = bottom
            out.append(row)
    return pd.DataFrame(out)


def service(members, shear_sign=1.0, with_shear=True):
    """``members``: {name: f(x, w) -> (M in kN-mm, dM/dx in kN)}; the three DEF combinations."""
    loads = {bd.COMBO_DEAD: (W_DEAD, 0.0), bd.COMBO_FULL: (W_DEAD, 1.0),
             bd.COMBO_SUSTAINED: (W_DEAD, 0.25)}
    frames = []
    for name, shape in members.items():
        for combo, (dead, live_share) in loads.items():
            moment_d, slope_d = shape(X, dead)
            moment_l, slope_l = shape(X, W_LIVE)
            frame = pd.DataFrame({"UniqueName": name, "Combo": combo, "Station": X,
                                  "M3": (moment_d + live_share * moment_l) / 1000.0})
            if with_shear:
                frame["V2"] = shear_sign * (slope_d + live_share * slope_l)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def connectivity(beams, columns=()):
    data = [(name, "Beam", i, j, L) for name, i, j in beams]
    data += [(name, "Column", i, j, 3000.0) for name, i, j in columns]
    return pd.DataFrame(data, columns=["UniqueName", "DesignType", "UniquePtI", "UniquePtJ", "Length"])


def top(out, name):
    return out[(out["UniqueName"] == name) & (out["Face"] == "TOP")].iloc[0]


# ------------------------------------------------------------------ the sign of the shear
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_the_shear_is_read_with_the_sign_of_the_moment_slope_whatever_the_analysis_uses(sign):
    moment = W_DEAD * X * (L - X) / 2.0                 # a simple span: reactions push up
    slope = W_DEAD * (L / 2.0 - X)
    (q,) = bd.statics_shear([(X, moment, sign * slope)])
    assert q[0] > 0 > q[-1]
    assert bd.support_cuts([X], [q]) == [(0, 0), (1, 0)]    # supported at both ends
    assert bd.statics_shear([(X, np.zeros_like(X), np.zeros_like(X))]) is None


# ------------------------------------------------------------------ a line over a support
def two_spans():
    """Two equal spans over a middle support, uniform load: R at the outer ends = 3 w L / 8."""
    def first(x, w):
        return 3 * w * L / 8 * x - w * x**2 / 2, 3 * w * L / 8 - w * x

    def second(x, w):        # the mirror of the first, running away from the middle support
        moment, slope = first(L - x, w)
        return moment, -slope

    return {"2BY-1": first, "2BY-1A": second}


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_a_line_over_a_girder_is_two_spans_not_one(sign):
    """The pieces meet at a joint with no column: a girder not split there holds the line."""
    table = connectivity([("2BY-1", "a", "m"), ("2BY-1A", "m", "b")],
                         [("C1", "a0", "a"), ("C2", "b0", "b")])
    results = rows("2BY-1", "2BY-1A")
    out = bd.add_deflection_columns(results, service(two_spans(), sign), connectivity=table)
    row = top(out, "2BY-1")
    assert row["Defl_live_limit_mm"] == pytest.approx(L / 360, abs=0.01)       # its own span
    expected = W_LIVE * 1000 * L**4 / (185.0 * EI)                             # w L^4 / 185 EI
    assert row["Defl_live_mm"] == pytest.approx(expected, rel=0.03, abs=0.01)
    assert top(out, "2BY-1A")["Defl_live_mm"] == row["Defl_live_mm"]

    # without the shears the pieces are joined, and the limit is that of twice the span
    joined = bd.add_deflection_columns(results, service(two_spans(), with_shear=False),
                                       connectivity=table)
    assert top(joined, "2BY-1")["Defl_live_limit_mm"] == pytest.approx(2 * L / 360, abs=0.01)


# ------------------------------------------------------------------ a column that is a load
P_DEAD, P_LIVE = 4.0, 2.0     # kN, from a planted column at the middle of a 12 m span


def planted():
    span = 2 * L

    def left(x, w):
        p = P_DEAD if w == W_DEAD else P_LIVE
        return (w * span / 2 + p / 2) * x - w * x**2 / 2, w * span / 2 + p / 2 - w * x

    def right(x, w):
        moment, slope = left(L - x, w)
        return moment, -slope

    return {"2GX-4": left, "2GX-4A": right}


def test_a_planted_column_is_a_load_and_the_span_runs_on_through_it():
    table = connectivity([("2GX-4", "a", "m"), ("2GX-4A", "m", "b")],
                         [("C1", "a0", "a"), ("C2", "b0", "b"), ("PC1", "m", "m_up")])
    out = bd.add_deflection_columns(rows("2GX-4", "2GX-4A"), service(planted()), connectivity=table)
    row = top(out, "2GX-4")
    span = 2 * L
    assert row["Defl_live_limit_mm"] == pytest.approx(span / 360, abs=0.01)
    expected = (5 * W_LIVE * 1000 * span**4 / 384 + P_LIVE * 1000 * span**3 / 48) / EI
    assert row["Defl_live_mm"] == pytest.approx(expected, rel=0.03)


# ------------------------------------------------------------------ a tip that carries a beam
def test_a_cantilever_that_carries_a_beam_at_its_tip_is_still_a_cantilever():
    """Another beam ends at the tip: counting the members there calls the tip supported."""
    def cantilever(x, w):                      # a point load at the tip only
        p = 10.0 if w == W_DEAD else 5.0
        return -p * (L - x), p + 0.0 * x

    table = connectivity([("2GX-7", "a", "t"), ("2BX-9", "t", "u")], [("C1", "a0", "a")])
    results = rows("2GX-7")
    out = bd.add_deflection_columns(results, service({"2GX-7": cantilever}), connectivity=table)
    row = top(out, "2GX-7")
    expected = 5.0 * 1000 * L**3 / (3 * EI)                    # P L^3 / 3 EI
    assert row["Defl_live_mm"] == pytest.approx(expected, rel=0.03)

    # the beam at the tip reaches no support of its own: the same without the shears
    again = bd.add_deflection_columns(results, service({"2GX-7": cantilever}, with_shear=False),
                                      connectivity=table)
    assert top(again, "2GX-7")["Defl_live_mm"] == row["Defl_live_mm"]

    # a beam that also ends at the tip, on a column of its own: two cantilevers at a corner
    corner = connectivity([("2GX-7", "a", "t"), ("2GY-9", "t", "u")],
                          [("C1", "a0", "a"), ("C2", "u0", "u")])
    free = bd.add_deflection_columns(results, service({"2GX-7": cantilever}), connectivity=corner)
    assert top(free, "2GX-7")["Defl_live_mm"] == row["Defl_live_mm"]

    # a girder that runs through the tip between its own columns holds it: a span
    propped = connectivity([("2GX-7", "a", "t"), ("2GY-9", "s", "t"), ("2GY-9A", "t", "u")],
                           [("C1", "a0", "a"), ("C2", "s0", "s"), ("C3", "u0", "u")])
    held = bd.add_deflection_columns(results, service({"2GX-7": cantilever}), connectivity=propped)
    assert top(held, "2GX-7")["Defl_live_mm"] < 0.1 * expected


def test_an_edge_beam_on_the_tips_of_cantilevers_does_not_hold_them_and_rests_on_them():
    """Two cantilever girders from their columns, an edge beam across their tips."""
    from design.beam_carriers import BeamNetwork

    table = connectivity([("2GX-1", "a", "t1"), ("2GX-2", "b", "t2"), ("2BY-8", "t1", "t2")],
                         [("C1", "a0", "a"), ("C2", "b0", "b")])
    network = BeamNetwork(table)
    assert network.rank_of(["2GX-1"]) == 0 and network.rank_of(["2BY-8"]) == 1
    assert not network.held_at("t1", ["2GX-1"])          # the tip is free
    assert network.held_at("t1", ["2BY-8"]) and network.held_at("t2", ["2BY-8"])


def test_a_member_supported_inside_its_length_is_cut_there():
    """One ETABS member over a support in the middle: the analysis gives two shears there."""
    x = np.concatenate([np.linspace(0, L, 21), np.linspace(L, 2 * L, 21)])
    w = 0.002
    r_end = 3 * w * L / 8
    moment = np.where(np.arange(len(x)) < 21, r_end * x - w * x**2 / 2,
                      r_end * (2 * L - x) - w * (2 * L - x) ** 2 / 2)
    q = np.where(np.arange(len(x)) < 21, r_end - w * x, -(r_end - w * (2 * L - x)))
    assert q[21] - q[20] > 0                              # the reaction of the middle support
    assert bd.support_cuts([x], [q]) == [(0, 0), (0, 21), (1, 0)]
    assert moment[20] == pytest.approx(moment[21])
