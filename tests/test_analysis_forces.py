"""
tests/test_analysis_forces.py
=============================
Checks for etabs_api/workflows/analysis_forces.py: factored forces built from
analysis results (combinations, spectrum and step permutations, pattern live
load, NSCP live load reduction). No ETABS is needed.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import analysis_forces as af  # noqa: E402

CASE_TYPES = {"DEAD": "Linear Static", "LIVE": "Linear Static", "LRED": "Linear Static",
              "EQX": "Linear Static", "RSAX": "Response Spectrum", "RSAY": "Response Spectrum",
              "WX": "Linear Static"}


def definitions(rows):
    """A "Load Combination Definitions" table: (name, type, load, factor) rows."""
    out = []
    for name, kind, load, factor in rows:
        out.append({"Name": name, "Type": kind, "LoadName": load, "SF": factor})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# combination_terms
# --------------------------------------------------------------------------- #
def test_nested_combinations_are_expanded_with_their_factor():
    table = definitions([
        ("RSA1", "Linear Add", "RSAX", 1.0), ("RSA1", None, "RSAY", 0.3),
        ("ULS", "Linear Add", "DEAD", 1.2), ("ULS", None, "LIVE", 0.5), ("ULS", None, "RSA1", 1.0),
        ("ENV", "Envelope", "ULS", 1.0),
        ("ON_ENV", "Linear Add", "ENV", 1.0),
    ])
    terms = af.combination_terms(table, CASE_TYPES)
    assert terms["ULS"].static == {"DEAD": 1.2, "LIVE": 0.5}
    assert terms["ULS"].spectral == {"RSAX": 1.0, "RSAY": pytest.approx(0.3)}
    assert "ENV" not in terms and "ON_ENV" not in terms


# --------------------------------------------------------------------------- #
# a small model: one beam (11 stations) and one column (3 stations)
# --------------------------------------------------------------------------- #
L = 6000.0
X = np.linspace(0, L, 11)


def beam_case(w: float, mi: float, mj: float) -> np.ndarray:
    """Forces of a uniformly loaded span with end moments mi, mj (sagging +, V2 = -dM/dx)."""
    t = X / L
    m = w * X * (L - X) / 2 + mi * (1 - t) + mj * t
    v = -(w * (L / 2 - X) + (mj - mi) / L)
    out = np.zeros((len(X), 6))
    out[:, 1], out[:, 5] = v, m
    return out


def element_table(cases: dict[str, np.ndarray], member="B1", label="Beam",
                  stations=X, step=None) -> pd.DataFrame:
    rows = []
    for case, values in cases.items():
        name, number = (case.split("#") + [None])[:2]
        for station, forces in zip(stations, values):
            row = {"Story": "2F", label: "B1", "UniqueName": member, "OutputCase": name,
                   "CaseType": "LinRespSpec" if name.startswith("RSA") else "LinStatic",
                   "StepType": "Max" if name.startswith("RSA") else ("Step By Step" if number else None),
                   "StepNumber": number, "Station": station, "Element": member,
                   "ElemStation": station}
            row.update(dict(zip(af.FORCES, forces)))
            rows.append(row)
    return pd.DataFrame(rows)


def test_static_combination_is_the_factored_sum():
    dead, live = beam_case(0.02, -50e6, -60e6), beam_case(0.01, -25e6, -30e6)
    forces = af.case_forces(element_table({"DEAD": dead, "LIVE": live}), "Beam")
    out = af.combine(forces, [af.ComboTerms("U", {"DEAD": 1.2, "LIVE": 1.6})], "beam")
    assert out["Permutation"].unique().tolist() == [1]
    np.testing.assert_allclose(out["M3"], 1.2 * dead[:, 5] + 1.6 * live[:, 5])
    assert list(out.columns) == ["Story", "Label", "UniqueName", "Combo", "Permutation",
                                 "Station", *af.FORCES]


def test_column_spectrum_gives_eight_sign_permutations():
    stations = np.array([0.0, 1500.0, 3000.0])
    dead = np.tile([-1e6, 1e3, 2e3, 0.0, 5e6, 8e6], (3, 1))
    rsa = np.tile([2e5, 4e3, 3e3, 1e3, 9e6, 7e6], (3, 1))
    table = element_table({"DEAD": dead, "RSAX": rsa}, "C1", "Column", stations)
    forces = af.case_forces(table, "Column")
    out = af.combine(forces, [af.ComboTerms("U", {"DEAD": 1.0}, {"RSAX": 1.0})], "column")
    assert out["Permutation"].nunique() == 8
    sets = out.groupby("Permutation").first()
    assert {tuple(np.sign(r[["P", "M2", "M3"]] - dead[0, [0, 4, 5]])) for _, r in sets.iterrows()} \
        == {(a, b, c) for a in (1, -1) for b in (1, -1) for c in (1, -1)}
    # the shear follows the sign of the moment it belongs to
    assert all(np.sign(r["V2"] - 1e3) == np.sign(r["M3"] - 8e6) for _, r in sets.iterrows())


def test_beam_is_reduced_to_the_envelope():
    dead, rsa = beam_case(0.02, -50e6, -60e6), np.abs(beam_case(0.002, 30e6, 30e6))
    forces = af.case_forces(element_table({"DEAD": dead, "RSAX": rsa}), "Beam")
    out = af.combine(forces, [af.ComboTerms("U", {"DEAD": 1.0}, {"RSAX": 1.0})], "beam")
    top = out[out.Permutation == 1]["M3"].to_numpy()
    bottom = out[out.Permutation == 2]["M3"].to_numpy()
    np.testing.assert_allclose(top, dead[:, 5] + rsa[:, 5])
    np.testing.assert_allclose(bottom, dead[:, 5] - rsa[:, 5])


def test_each_wind_step_is_a_permutation_for_columns():
    stations = np.array([0.0, 3000.0])
    dead = np.tile([-1e6, 0, 0, 0, 0, 0], (2, 1))
    steps = {f"WX#{k}": np.tile([k * 1e3, 0, 0, 0, 0, k * 1e6], (2, 1)) for k in (1, 2, 3)}
    forces = af.case_forces(element_table({"DEAD": dead, **steps}, "C1", "Column", stations),
                            "Column")
    assert forces.steps == {"WX": ["WX#1", "WX#2", "WX#3"]}
    out = af.combine(forces, [af.ComboTerms("U", {"DEAD": 0.9, "WX": 1.0})], "column")
    assert sorted(out.groupby("Permutation")["M3"].first()) == [1e6, 2e6, 3e6]


# --------------------------------------------------------------------------- #
# pattern live load
# --------------------------------------------------------------------------- #
def test_pattern_gives_simple_span_and_fixed_end_moments():
    w = 0.01
    live = beam_case(w, -20e6, -35e6)
    out = af.pattern_variants(X, live)
    np.testing.assert_allclose(out["pinned"][5, 5], w * L**2 / 8, rtol=1e-9)
    assert out["pinned"][0, 5] == pytest.approx(0, abs=1e-6)
    np.testing.assert_allclose(out["fixed"][[0, -1], 5], [-w * L**2 / 12] * 2, rtol=2e-3)
    # V2 = -dM/dx still holds: the pinned span has symmetric end shears
    assert out["pinned"][0, 1] == pytest.approx(-w * L / 2)


def test_pattern_keeps_a_released_end_pinned():
    w = 0.01
    out = af.pattern_variants(X, beam_case(w, -20e6, 0.0), released_i=False, released_j=True)
    assert out["fixed"][-1, 5] == pytest.approx(0, abs=1e-6)
    assert out["fixed"][0, 5] == pytest.approx(-w * L**2 / 8, rel=5e-3)  # propped cantilever


def test_pattern_leaves_a_cantilever_alone():
    x = X
    m = -0.01 * (L - x) ** 2 / 2
    forces = np.zeros((len(x), 6))
    forces[:, 5], forces[:, 1] = m, -np.gradient(m, x)
    forces[-1, 1] = 0.0
    out = af.pattern_variants(x, forces)
    np.testing.assert_array_equal(out["pinned"], forces)


def test_pattern_permutations_cover_the_analysis():
    dead, live = beam_case(0.02, -50e6, -60e6), beam_case(0.01, -60e6, -60e6)
    forces = af.case_forces(element_table({"DEAD": dead, "LIVE": live}), "Beam")
    combo = af.ComboTerms("U", {"DEAD": 1.2, "LIVE": 1.6})
    plain = af.combine(forces, [combo], "beam")
    patterned = af.combine(forces, [combo], "beam", live_cases=["LIVE"], pattern_factor=0.75)
    assert patterned["Permutation"].nunique() == 2
    most = patterned.groupby("Station")["M3"].max().to_numpy()
    assert (most >= plain["M3"].to_numpy() - 1e-6).all()
    assert most[5] > plain["M3"].to_numpy()[5]  # midspan sagging grows


# --------------------------------------------------------------------------- #
# live load reduction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("area, kpa, ratio, one_level, percent, limit", [
    (50.0, 0.0, 1.0, True, 0.0, "no reducible live"),
    (10.0, 4.8, 1.0, True, 0.0, "A <= 14 m2"),
    (30.0, 4.8, 1.0, True, 0.86 * 16, "r (A - 14)"),
    (200.0, 4.8, 3.0, True, 40.0, "40 % (one level)"),
    (200.0, 4.8, 3.0, False, 60.0, "60 %"),
    (200.0, 4.8, 0.5, False, 34.65, "23.1 (1 + D/L)"),
    (200.0, 7.2, 3.0, False, 20.0, "L > 4.8 kPa"),
    (200.0, 7.2, 3.0, True, 0.0, "L > 4.8 kPa"),
])
def test_nscp_reduction_limits(area, kpa, ratio, one_level, percent, limit):
    result = af.nscp_reduction(area, kpa, ratio, one_level)
    assert result.percent == pytest.approx(percent)
    assert result.limit == limit


@pytest.mark.parametrize("area, kpa, kind, one_level, percent, limit", [
    (50.0, 0.0, "beam", True, 0.0, "no reducible live"),
    (18.0, 2.4, "beam", True, 0.0, "KLL AT < 37.16 m2"),        # 2 x 18 = 36 m2
    (30.0, 2.4, "beam", True, (1 - (0.25 + 4.57 / 60 ** 0.5)) * 100, "0.25 + 4.57 / sqrt(KLL AT)"),
    (30.0, 2.4, "column", False, (1 - (0.25 + 4.57 / 120 ** 0.5)) * 100,
     "0.25 + 4.57 / sqrt(KLL AT)"),
    (500.0, 2.4, "beam", True, 50.0, "50 % (one level)"),       # L >= 0.50 Lo
    (500.0, 2.4, "column", False, 60.0, "60 %"),                # L >= 0.40 Lo
    (200.0, 7.2, "column", False, 20.0, "L > 4.8 kPa"),
    (8.0, 7.2, "column", False, 0.0, "L > 4.8 kPa"),            # 4 x 8 < 37.16: not below 4.7.2
    (200.0, 7.2, "beam", True, 0.0, "L > 4.8 kPa"),
])
def test_asce_reduction_limits(area, kpa, kind, one_level, percent, limit):
    result = af.asce_reduction(area, kpa, kind, one_level)
    assert result.percent == pytest.approx(percent, abs=1e-3)
    assert result.limit == limit
    assert result.code == "ASCE"


def test_live_load_reduction_uses_the_chosen_code():
    width = 5000.0
    forces = af.case_forces(element_table({
        "LRED": beam_case(2.4e-3 * width, 0.0, 0.0), "DEAD": beam_case(6e-3 * width, 0.0, 0.0),
    }), "Beam")
    areas = {"B1": af.Tributary(area_m2=30.0, load_kn=30.0 * 2.4)}
    nscp = af.live_load_reduction(forces, "beam", areas, ["DEAD"], ["LRED"])["B1"]
    asce = af.live_load_reduction(forces, "beam", areas, ["DEAD"], ["LRED"], code="ASCE")["B1"]
    assert nscp.percent == pytest.approx(0.86 * 16)
    assert asce.percent == pytest.approx((1 - (0.25 + 4.57 / 60 ** 0.5)) * 100, abs=1e-3)


def test_load_share_area_comes_from_the_unit_load():
    width = 5000.0  # tributary width: 1 kPa = 0.001 N/mm2 over 5 m
    unit = beam_case(1e-3 * width, 0.0, 0.0)
    live = beam_case(4.8e-3 * width, 0.0, 0.0)
    dead = beam_case(6e-3 * width, 0.0, 0.0)
    forces = af.case_forces(element_table({"UNIT": unit, "LRED": live, "DEAD": dead}), "Beam")
    geometric = {"B1": af.Tributary(area_m2=28.0, load_kn=28.0 * 4.8)}
    areas = af.load_share_tributary(forces, "beam", "UNIT", 1.0, geometric)
    assert areas["B1"].area_m2 == pytest.approx(30.0)  # 6 m x 5 m, from the analysis
    assert areas["B1"].reducible_kpa == pytest.approx(4.8)  # intensity from the floors
    result = af.live_load_reduction(forces, "beam", areas, ["DEAD"], ["LRED"],
                                    "load share")["B1"]
    assert result.percent == pytest.approx(0.86 * 16)
    assert result.method == "load share"
    combo = af.ComboTerms("U", {"DEAD": 1.2, "LRED": 1.6})
    out = af.combine(forces, [combo], "beam", ["LRED"], ["LRED"], {"B1": result})
    np.testing.assert_allclose(out["M3"], 1.2 * dead[:, 5] + 1.6 * result.factor * live[:, 5])


def column_case(p_top: float, p_bottom: float) -> np.ndarray:
    out = np.zeros((2, 6))
    out[:, 0] = [-p_bottom, -p_top]
    return out


@pytest.mark.parametrize("own_floor, above, levels", [
    (0.0, 200e3, 1),     # no slab at its own level: one level (the floor above)
    (100e3, 0.0, 1),     # top column: its own floor only
    (100e3, 200e3, 2),   # its floor and the floors above
])
def test_load_share_counts_the_levels_that_load_a_column(own_floor, above, levels):
    stations = np.array([0.0, 3000.0])
    total = own_floor + above
    tables = []
    for name, unit_load in (("C1", total), ("C2", above)):
        if unit_load == 0:
            continue
        tables.append(element_table({"UNIT": column_case(unit_load, unit_load)}, name,
                                    "Column", stations))
    forces = af.case_forces(pd.concat(tables, ignore_index=True), "Column")
    areas = af.load_share_tributary(forces, "column", "UNIT", 1.0, None,
                                    {"C1": "C2"} if above else {})
    assert areas["C1"].area_m2 == pytest.approx(total / 1000)
    assert areas["C1"].levels == levels


@pytest.mark.parametrize("levels, heavy, percent, limit", [
    (1, 0.0, 40.0, "40 % (one level)"),
    (2, 0.0, 60.0, "60 %"),
    (2, 3.0, 20.0, "L > 4.8 kPa (F9)"),
    (1, 3.0, 0.0, "L > 4.8 kPa (F9)"),
    (2, 0.3, 60.0, "60 %"),  # under the 0.5 m2 tolerance
])
def test_column_reduction_uses_levels_and_heavy_area(levels, heavy, percent, limit):
    stations = np.array([0.0, 3000.0])
    table = element_table({"DEAD": column_case(900e3, 900e3),
                           "LRED": column_case(400e3, 400e3)}, "C1", "Column", stations)
    forces = af.case_forces(table, "Column")
    area = af.Tributary(area_m2=200.0, load_kn=200.0 * 2.4, heavy_m2=heavy, levels=levels,
                        sources={"F9"} if heavy else set())
    result = af.live_load_reduction(forces, "column", {"C1": area}, ["DEAD"], ["LRED"])["C1"]
    assert result.percent == pytest.approx(percent)
    assert result.limit == limit
