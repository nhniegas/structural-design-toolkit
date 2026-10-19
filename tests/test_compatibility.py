"""
tests/test_compatibility.py
===========================
sdt compatibility (etabs_api/workflows/compatibility.py): members not part of the
seismic-force-resisting system, NSCP 2015 208.7.2.3 and 418.14, checked against
hand calculations. No ETABS.
"""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import compatibility as cp  # noqa: E402


def test_design_displacement_is_0_7_r_of_the_frames_alone_at_least_0_0025():
    pinned = {("2F", "X"): 0.003, ("3F", "X"): 0.0001}
    real = {("2F", "X"): 0.002, ("3F", "X"): 0.0001}
    out = cp.design_displacement(real, pinned, 8.5)
    assert out[("2F", "X")][0] == pytest.approx(0.7 * 8.5 * 0.003)
    assert out[("2F", "X")][2] == pytest.approx(0.7 * 8.5 * 0.003 / 0.002)
    assert out[("3F", "X")][0] == 0.0025          # 208.7.2.3: at least 0.0025 h
    assert out[("3F", "X")][2] == pytest.approx(25.0)


def beam_rows(top=3, bottom=3, spacing_mid=200, spacing_end=100):
    common = {"UniqueName": "B1", "Width": 300.0, "Depth": 500.0, "f'c": 28.0, "fy": 414.0,
              "fyw": 414.0, "dm": 20.0, "ds": 10.0, "TopCover": 60.0, "BotCover": 60.0,
              "Stirrup_Legs": 2, "Spacing_2H": spacing_end, "Spacing_Mid": spacing_mid,
              "Vc_zero_left": False, "Vc_zero_right": False, "SupportStatus": "Supported",
              "SectProp": "B_300X500_C04_G60", "ClearSpan_Ln": 6000.0}
    return pd.DataFrame([{**common, "Face": "TOP", "n_left_L1": top, "n_right_L1": top},
                         {**common, "Face": "BOTTOM", "n_left_L1": bottom,
                          "n_right_L1": bottom}])


def test_beam_strength_by_hand():
    s = cp.beam_strength(beam_rows())
    area = 3 * math.pi * 20 ** 2 / 4                     # 942.5 mm2
    a = area * 414 / (0.85 * 28 * 300)                   # 54.7 mm
    assert s["phi_Mn_top"] == pytest.approx(0.9 * area * 414 * (440 - a / 2) / 1e6)
    vc = 0.17 * math.sqrt(28) * 300 * 440
    vs = 2 * math.pi * 10 ** 2 / 4 * 414 * 440 / 100
    assert s["phi_Vn"] == pytest.approx(0.75 * (vc + vs) / 1000)


def test_a_beam_within_its_strength_needs_stirrups_at_d_over_2():
    strength = cp.beam_strength(beam_rows(spacing_mid=200))
    induced = {"Mu_kNm": 50.0, "Vu_kN": 100.0, "Pu_kN": 0.0}
    check = cp.check_beam("B1", "2F", induced, strength, smrf_design=True)
    assert check.clause.startswith("NSCP 418.14.3.2") and check.passed   # 200 <= 220
    loose = cp.check_beam("B1", "2F", induced, cp.beam_strength(beam_rows(spacing_mid=250)),
                          smrf_design=True)
    assert not loose.passed                                              # 250 > 220


def test_a_beam_over_its_strength_needs_the_smrf_rules():
    strength = cp.beam_strength(beam_rows())
    induced = {"Mu_kNm": 500.0, "Vu_kN": 100.0, "Pu_kN": 0.0}
    assert cp.check_beam("B1", "2F", induced, strength, True).clause.startswith(
        "NSCP 418.14.3.3")
    assert cp.check_beam("B1", "2F", induced, strength, True).passed
    assert not cp.check_beam("B1", "2F", induced, strength, False).passed


def test_a_column_within_its_strength_needs_hoops_at_so():
    strength = {"phi_Mn": 300.0, "phi_Vn": 400.0, "ag": 500 * 500, "ast": 0.0, "fc": 28.0,
                "spacing": 100.0}
    induced = {"Mu_kNm": 100.0, "Vu_kN": 50.0, "Pu_kN": 500.0}
    # so = min(6 x 25, 150) = 150: the 150 mm ties of the schedule meet it
    assert cp.check_column("C1", "2F", induced, strength, 25.0, 414.0, True).passed
    # so = min(6 x 20, 150) = 120: they do not
    assert not cp.check_column("C1", "2F", induced, strength, 20.0, 414.0, True).passed


def test_induced_forces_add_the_scaled_quake_to_the_gravity_load():
    rows = []
    for case, (p, m) in {"DEAD": (-100e3, 20e6), "LIVE": (-50e3, 10e6),
                         "EQXSD": (0.0, 5e6)}.items():
        rows.append({"UniqueName": "C1", "Story": "2F", "OutputCase": case, "Station": 0.0,
                     "P": p, "V2": 0.0, "V3": 0.0, "M2": 0.0, "M3": m})
    factors = {("2F", "X"): (0.0178, 0.002, 8.9)}
    out = cp.induced_forces(pd.DataFrame(rows), {"EQXSD": "X"}, factors, ["DEAD"], ["LIVE"],
                            0.5, {"C1": "2F"}).iloc[0]
    # 1.2 x 20 + 0.5 x 10 + 8.9 x 5 = 73.5 kN-m (it governs over 0.9D: 18 + 44.5)
    assert out["Mu_kNm"] == pytest.approx(1.2 * 20 + 0.5 * 10 + 8.9 * 5)
    assert out["Pu_kN"] == pytest.approx(1.2 * 100 + 0.5 * 50)
    assert out["Gravity"] == "1.2D + 0.5L"


def test_candidates_cantilevers_short_girders_planted_and_minor_columns():
    beams = pd.concat([
        beam_rows().assign(UniqueName="CANT", SupportStatus="Cantilever"),
        beam_rows().assign(UniqueName="SHORT", SectProp="G_300X500_C04_G60",
                           ClearSpan_Ln=1500.0),       # 4d = 1760
        beam_rows().assign(UniqueName="LONG", SectProp="G_300X500_C04_G60")])
    found = cp.beam_candidates(beams)
    assert set(found) == {"CANT", "SHORT"}
    points = pd.DataFrame([{"UniqueName": n, "X": x, "Y": y} for n, x, y in (
        ("1", 0, 0), ("2", 0, 0), ("3", 6000, 0), ("4", 6000, 0), ("5", 0, 6000),
        ("6", 0, 6000), ("7", 9000, 9000))])
    conn = pd.DataFrame([
        {"UniqueName": "C1", "Story": "2F", "ColumnBay": "C1", "UniquePtI": "1", "UniquePtJ": "2"},
        {"UniqueName": "C2", "Story": "2F", "ColumnBay": "C2", "UniquePtI": "3", "UniquePtJ": "4"},
        {"UniqueName": "C3", "Story": "2F", "ColumnBay": "C3", "UniquePtI": "5", "UniquePtJ": "6"},
        {"UniqueName": "PL", "Story": "3F", "ColumnBay": "PL", "UniquePtI": "7", "UniquePtJ": "8"},
        {"UniqueName": "GX", "Story": "2F", "ColumnBay": None, "UniquePtI": "2", "UniquePtJ": "4"},
        {"UniqueName": "GY", "Story": "2F", "ColumnBay": None, "UniquePtI": "2", "UniquePtJ": "6"},
    ])
    supports = pd.DataFrame({"UniqueName": ["1", "3", "5"]})
    shears = {"C1": 100.0, "C2": 100.0, "C3": 10.0}
    found = cp.column_candidates(conn, points, supports, {"GX", "GY"}, shears)
    assert "C1" not in found                                  # girders both ways, its share
    assert found["PL"].startswith("planted")
    assert "no girder along Y" in found["C2"]                 # only GX reaches its top
    assert "no girder along X" in found["C3"]
