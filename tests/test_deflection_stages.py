"""The long-term part, the total and the stages of the beam deflection (ACI 24.2)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_beam_deflection as base  # noqa: E402

bd = base.bd
L, X = base.L, base.X


def moments(w_dead=0.03, w_live=0.01):
    shape = X * (L - X) / 2 / 1000.0
    return {bd.COMBO_DEAD: w_dead * shape, bd.COMBO_FULL: (w_dead + w_live) * shape,
            bd.COMBO_SUSTAINED: (w_dead + 0.25 * w_live) * shape}


@pytest.mark.parametrize("months, xi", [(0, 0.0), (1.5, 0.5), (3, 1.0), (6, 1.2), (9, 1.3),
                                        (12, 1.4), (36, 1.7), (60, 2.0), (240, 2.0)])
def test_the_time_factor_follows_the_table_of_the_code(months, xi):
    assert bd.time_factor(months) == pytest.approx(xi)


def test_the_after_attachment_deflection_is_the_long_term_part_plus_the_live_load_not_sustained():
    result = bd.beam_deflection(base.section(top=(0, 0, 0)), X, moments())
    dead = result.total - result.creep - result.live          # D(DL), all on one Ie
    sustained = result.creep / result.lam                     # D(DL + 0.25 LL)
    assert result.lam == pytest.approx(2.0)
    assert sustained == pytest.approx(dead + 0.25 * result.live, rel=1e-6)
    assert result.long_term == pytest.approx(result.creep + 0.75 * result.live, rel=1e-6)
    # the total is the after-attachment deflection plus the immediate one of the sustained load
    assert result.total == pytest.approx(result.long_term + sustained, rel=1e-6)
    assert result.deducted == 0 and result.at_first_load is None and result.at_partitions is None


def test_without_a_time_nothing_is_deducted():
    plain = bd.beam_deflection(base.section(), X, moments())
    for stages in (None, bd.DeflectionStages(), bd.DeflectionStages(0, 0.8),
                   bd.DeflectionStages(6, 0.0)):
        again = bd.beam_deflection(base.section(), X, moments(), stages=stages)
        assert again.long_term == pytest.approx(plain.long_term)
        assert again.deducted == 0


def test_the_long_term_deflection_before_the_partitions_is_deducted():
    plain = bd.beam_deflection(base.section(top=(0, 0, 0)), X, moments())
    dead = plain.total - plain.creep - plain.live
    staged = bd.beam_deflection(base.section(top=(0, 0, 0)), X, moments(),
                                stages=bd.DeflectionStages(3, 0.8))
    # 3 months: xi = 1.0, of the 2.0 at 5 years; 80 % of the dead load is there by then
    assert staged.at_first_load == pytest.approx(0.8 * dead, rel=1e-6)
    assert staged.deducted == pytest.approx(1.0 * 0.8 * dead, rel=1e-6)
    assert staged.at_partitions == pytest.approx(staged.at_first_load + staged.deducted, rel=1e-6)
    assert staged.long_term == pytest.approx(plain.long_term - staged.deducted, rel=1e-6)
    assert staged.long_term < plain.long_term
    # what is not a limit does not change: live load, the long-term part and the total
    assert (staged.live, staged.creep, staged.total) == pytest.approx(
        (plain.live, plain.creep, plain.total))


def test_a_later_start_and_a_larger_share_deduct_more_but_never_all_of_it():
    values = [bd.beam_deflection(base.section(), X, moments(), stages=bd.DeflectionStages(m, s))
              .long_term for m, s in ((1, 0.5), (3, 0.5), (3, 1.0), (12, 1.0), (600, 1.0))]
    assert values == sorted(values, reverse=True)
    assert values[-1] > 0      # the sustained live load and the rest of the live load remain


def test_compression_steel_lowers_the_deduction_with_the_long_term_factor():
    with_top = bd.beam_deflection(base.section(top=(4, 0, 0)), X, moments(),
                                  stages=bd.DeflectionStages(3, 1.0))
    dead = with_top.total - with_top.creep - with_top.live
    assert with_top.lam < 2.0
    assert with_top.deducted == pytest.approx(with_top.lam / 2.0 * 1.0 * dead, rel=1e-6)


def test_the_stages_are_saved_and_read_and_a_wrong_record_is_the_safe_side():
    stages = bd.DeflectionStages(3, 0.8)
    assert bd.DeflectionStages.from_saved(stages.to_saved()) == stages
    assert "3 months" in stages.describe() and "80 %" in stages.describe()
    for wrong in (None, {}, {"months_before_partitions": "x", "dead_share_before": 1},
                  {"months_before_partitions": -1, "dead_share_before": 1},
                  {"months_before_partitions": 3, "dead_share_before": 1.5}):
        assert not bd.DeflectionStages.from_saved(wrong).active
    assert "safe side" in bd.DeflectionStages().describe()


def results_and_service():
    rows = []
    for face, top_bars, bottom_bars in (("TOP", 2, 0), ("BOTTOM", 0, 3)):
        row = {"UniqueName": "B1", "Story": "2F", "Face": face, "SupportStatus": "Supported Both Ends",
               "Width": 300.0, "Depth": 600.0, "f'c": 28.0, "cc": 40.0, "ds": 10.0, "dm": 20.0,
               "Design_Status": "OK"}
        for zone in ("left", "mid", "right"):
            for layer in (1, 2, 3):
                row[f"n_{zone}_L{layer}"] = 0
            row[f"n_{zone}_L1"] = top_bars
            row[f"n_{zone}_L3"] = bottom_bars
        rows.append(row)
    service = pd.concat([pd.DataFrame({"UniqueName": "B1", "Combo": combo, "Station": X, "M3": m})
                         for combo, m in moments().items()], ignore_index=True)
    return pd.DataFrame(rows), service


def test_the_results_show_the_long_term_part_and_the_total_and_the_stages_only_when_given():
    results, service = results_and_service()
    plain = bd.add_deflection_columns(results, service)
    assert {"Defl_creep_mm", "Defl_total_mm"} <= set(plain.columns)
    assert not set(bd.STAGE_COLUMNS) & set(plain.columns)
    row = plain.iloc[0]
    assert row["Defl_total_mm"] > row["Defl_long_mm"] > row["Defl_creep_mm"] > row["Defl_live_mm"]

    staged = bd.add_deflection_columns(results, service, stages=bd.DeflectionStages(3, 0.8))
    assert set(bd.STAGE_COLUMNS) <= set(staged.columns)
    again = staged.iloc[0]
    assert again["Defl_long_mm"] == pytest.approx(row["Defl_long_mm"] - again["Defl_deducted_mm"],
                                                  abs=0.02)
    assert again["Defl_ratio"] < row["Defl_ratio"]
    assert "3 months" in again["Defl_stages"]


def test_the_question_keeps_the_safe_side_unless_the_engineer_gives_the_time(monkeypatch, tmp_path):
    from design import beam_designer_aci318 as beam
    from utilities import _gui_helpers as gui

    monkeypatch.setattr(beam, "_deflection_settings_path", lambda: str(tmp_path / "d.json"))
    monkeypatch.setattr(gui, "select_option",
                        lambda title, prompt, options, default_index=0: options[default_index])
    assert not beam.ask_deflection_stages().active            # the default: not known

    monkeypatch.setattr(gui, "select_option", lambda *a, **k: beam.STAGES_ENTER)
    monkeypatch.setattr(gui, "enter_values", lambda title, prompt, labels, defaults: {
        beam.STAGE_MONTHS: "6", beam.STAGE_SHARE: "75"})
    assert beam.ask_deflection_stages() == bd.DeflectionStages(6.0, 0.75)

    # remembered: the next run offers "enter" first, with the same numbers
    seen = {}

    def first(title, prompt, options, default_index=0):
        seen["default"] = options[default_index]
        return options[default_index]

    def keep(title, prompt, labels, defaults):
        seen["defaults"] = dict(defaults)
        return defaults

    monkeypatch.setattr(gui, "select_option", first)
    monkeypatch.setattr(gui, "enter_values", keep)
    assert beam.ask_deflection_stages() == bd.DeflectionStages(6.0, 0.75)
    assert seen["default"] == beam.STAGES_ENTER
    assert seen["defaults"] == {beam.STAGE_MONTHS: "6", beam.STAGE_SHARE: "75"}

    # the limit question does not erase the stages it shares its file with
    beam._save_deflection_settings({"long_limit_divisor": 240})
    assert bd.DeflectionStages.from_saved(beam._deflection_settings()["stages"]).active

    monkeypatch.setattr(gui, "enter_values", lambda *a, **k: {beam.STAGE_MONTHS: "-1",
                                                              beam.STAGE_SHARE: "80"})
    monkeypatch.setattr(gui, "show_warning", lambda *a, **k: None)
    assert beam.ask_deflection_stages() is None                # a wrong entry is refused
    monkeypatch.setattr(gui, "select_option", lambda *a, **k: None)
    assert beam.ask_deflection_stages() is None                # closed


def test_the_window_names_the_number_of_combinations_not_the_first_one():
    import test_beam_designer_aci318 as beam_tests

    props, forces = beam_tests._mock_beam_properties(), beam_tests._mock_force_table()
    second = forces.copy()
    second["Combo"] = "WIND"
    lines = []
    beam_tests.beam.execute_beam_design(props, pd.concat([forces, second], ignore_index=True),
                                        False, "GRAV", progress=lines.append)
    demands = [line for line in lines if "Demands" in line]
    assert len(demands) == 1 and "all 2 combinations" in demands[0]
