"""Tests for Ax, rho and the regularity record (etabs_api/workflows/seismic_factors.py).
None needs ETABS."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design.criteria.code_config import seismic_system  # noqa: E402
from etabs_api.workflows import model_setup as ms  # noqa: E402
from etabs_api.workflows import seismic_factors as sf  # noqa: E402


# --------------------------------------------------------------------------
# AX
# --------------------------------------------------------------------------
def test_ax_by_eq_208_7_and_its_limits():
    assert sf.ax_of(1.2, 1.0) == pytest.approx(1.0)
    assert sf.ax_of(1.5, 1.0) == pytest.approx((1.5 / 1.2) ** 2)
    assert sf.ax_of(3.0, 1.0) == pytest.approx(3.0)          # capped
    assert sf.ax_of(1.0, 1.0) == pytest.approx(1.0)          # not below 1.0


def test_level_ax_takes_the_largest_case_of_each_direction():
    rows = [("2F", "EQXPE", "X", 1.5, 1.0), ("2F", "EQXNE", "X", 1.3, 1.0),
            ("2F", "EQYPE", "Y", 1.1, 1.0)]
    ax = sf.level_ax(rows)
    assert ax[("2F", "X")] == pytest.approx((1.5 / 1.2) ** 2)
    assert ax[("2F", "Y")] == pytest.approx(1.0)


def test_accidental_torques_from_the_story_torques():
    # story torque adds the levels above: RF alone, then RF + 3F
    torques = {("RF", "EQXPE"): 3411.8, ("RF", "EQXNE"): 2678.1,
               ("3F", "EQXPE"): 5879.4, ("3F", "EQXNE"): 4618.0}
    out = sf.accidental_torques(torques, ["RF", "3F"], "EQXPE", "EQXNE")
    assert out["RF"] == pytest.approx((3411.8 - 2678.1) / 2)
    assert out["3F"] == pytest.approx((5879.4 - 4618.0) / 2 - (3411.8 - 2678.1) / 2)


def test_added_torque_is_ax_minus_one_times_the_accidental_with_the_sign():
    ax = {("RF", "X"): 1.5, ("3F", "X"): 1.0}
    accidental = {"RF": 100.0, "3F": 80.0}
    assert sf.added_torques(ax, accidental, "X", 1.0) == {"RF": pytest.approx(50.0)}
    assert sf.added_torques(ax, accidental, "X", -1.0) == {"RF": pytest.approx(-50.0)}


# --------------------------------------------------------------------------
# RHO
# --------------------------------------------------------------------------
def test_rho_by_eq_208_20_and_its_limits():
    smrf = seismic_system("C1")
    rho, computed, _, over = sf.rho_value(0.4, 400.0, smrf)
    assert computed == pytest.approx(2 - 6.1 / (0.4 * 20))       # 1.2375
    assert rho == pytest.approx(computed) and not over
    rho, _, _, _ = sf.rho_value(0.05, 400.0, smrf)               # a low ratio
    assert rho == pytest.approx(1.0)
    rho, computed, notes, over = sf.rho_value(0.5, 400.0, smrf)  # over 1.25: more bays
    assert computed == pytest.approx(2 - 6.1 / 10) and over
    assert rho == pytest.approx(min(computed, 1.5)) and any("bays" in n for n in notes)
    dual = seismic_system("D1")
    rho, computed, _, over = sf.rho_value(0.5, 400.0, dual)
    assert rho == pytest.approx(max(min(0.8 * computed, 1.5), 1.0)) and not over
    assert sf.rho_value(0.5, 100.0, smrf, zone_factor=0.2)[0] == 1.0   # zone 2


def test_frame_bays_and_the_element_story_shear_ratio():
    columns = {"C1": (0, 0), "C2": (6000, 0), "C3": (12000, 0), "C4": (0, 8000)}
    beams = [("C1", "C2"), ("C2", "C3"), ("C1", "C4")]
    along_x = sf.frame_bays(columns, beams, True)
    assert along_x == [("C1", "C2"), ("C2", "C3")]
    shears = {"C1": 100.0, "C2": 200.0, "C3": 100.0}
    # C2 has bays on both sides: 70 % of its shear
    ratio, where = sf.story_ratio(shears, along_x, 1000.0)
    assert ratio == pytest.approx((100 + 0.7 * 200) / 1000)
    assert where in ("C1 and C2", "C2 and C3")


def test_wall_ratio_and_the_lower_two_thirds():
    assert sf.wall_ratio(300.0, 6.0, 1000.0) == pytest.approx(300 * 3 / 6 / 1000)
    stories = sf.lower_two_thirds({"2F": 4000, "3F": 7500, "4F": 11000, "RF": 14500}, 0.0)
    # 2/3 of 14.5 m is 9.67 m: 4F starts at 7.5 m, below it; RF starts at 11 m, above
    assert stories == ["2F", "3F", "4F"]
    # a one-story building: its only story
    assert sf.lower_two_thirds({"2F": 8700}, 1500) == ["2F"]


def test_case_direction():
    assert [sf.case_direction(c) for c in ("EQXPE", "RSAX", "AX EQYNE", "EQYPE")] == [
        "X", "X", "Y", "Y"]


# --------------------------------------------------------------------------
# REGULARITY RECORD
# --------------------------------------------------------------------------
def test_regularity_record(tmp_path):
    model = tmp_path / "m.EDB"
    model.write_text("x")
    sf.save_regularity(str(model), ["1. Torsional irregularity"], ["5. Non-parallel systems"])
    record = sf.load_regularity(str(model))
    assert record["irregular"] == ["1. Torsional irregularity"]
    assert sf.load_regularity(str(tmp_path / "other.EDB")) is None


def test_the_ax_patterns_are_not_asked_a_role_in_setup():
    assert ms.etabs_generated("AX EQXPE") and not ms.etabs_generated("EQXPE")
