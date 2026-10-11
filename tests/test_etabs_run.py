"""
tests/test_etabs_run.py
=======================
The parts of the ETABS test runner (tools/etabs_run.py) that need no ETABS:
how the results of a command are compared with a reference, and how a
scenario is read. The rule that is tested: text, counts and whole numbers
must be the same; other numbers within the tolerance; and nothing is accepted
without being counted.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import etabs_run as er  # noqa: E402


def table(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


# --------------------------------------------------------------------------
# COMPARISON
# --------------------------------------------------------------------------
def test_the_same_tables_have_no_difference_and_nothing_accepted():
    a = table(Name=["B1", "B2"], Bars=[4, 5], Mu=[120.5, 88.25], Check=["PASS", "FAIL"])
    accepted = er.Accepted()
    assert er.compare(a, a.copy(), "beams", accepted=accepted) == []
    assert accepted.count == 0


def test_the_last_digits_of_the_analysis_are_accepted_and_counted():
    """What ETABS gives differently at each analysis: about 1e-14 of a force."""
    a = table(M3=[-62.99289202417679, 0.0034312606781510])
    b = table(M3=[-62.99289202417681, 0.0034312606781505])
    accepted = er.Accepted()
    assert er.compare(a, b, "forces", accepted=accepted) == []
    assert accepted.count == 2 and 0 < accepted.largest < 1e-12
    assert accepted.where == "forces[M3]"
    # with no tolerance the same numbers are differences
    assert len(er.compare(a, b, "forces", tolerance=0.0)) == 1


def test_a_zero_that_is_only_noise_is_not_a_difference():
    """1e-15 against 2.5e-15 kN is twice as much, and nothing: the size of the
    column is the measure, not the size of the number."""
    a = table(P=[-1.27e-15, 417.31])
    b = table(P=[-2.55e-15, 417.31])
    assert er.compare(a, b, "forces") == []


def test_a_real_difference_of_a_number_is_reported_with_its_row():
    a = table(Name=["C1", "C2"], Pu=[417.313931, 596.6])
    b = table(Name=["C1", "C2"], Pu=[402.357278, 596.6])
    found = er.compare(a, b, "columns")
    assert len(found) == 1
    assert "columns[Pu]: 1 of 2 values differ; row 0" in found[0]


def test_a_count_is_never_within_a_tolerance():
    """4 bars are not 5 bars, in a column of whole numbers or of decimals."""
    assert er.compare(table(Bars=[4, 6]), table(Bars=[4, 5]), "t", tolerance=0.5)
    assert er.compare({"legs": 4.0}, {"legs": 5.0}, "t", tolerance=0.5)
    assert er.compare({"legs": 4}, {"legs": 5}, "t", tolerance=0.5)


def test_a_text_must_be_the_same():
    a = table(Status=["OK", "FAILED: DEFLECTION"])
    b = table(Status=["OK", "OK"])
    assert "t[Status]: 1 of 2 values differ" in er.compare(a, b, "t")[0]
    assert er.compare({"status": "converged"}, {"status": "not converged"}, "loop")


def test_blank_cells_are_the_same_whatever_they_are_written_as():
    a = table(Note=["x", None, np.nan], Value=[1.5, np.nan, 2.5])
    b = table(Note=["x", np.nan, None], Value=[1.5, np.nan, 2.5])
    assert er.compare(a, b, "t") == []


def test_columns_and_rows_that_differ_are_named():
    a = table(Name=["B1"], Depth=[500], New=[1])
    b = table(Name=["B1"], Depth=[500], Old=[1])
    found = er.compare(a, b, "t")
    assert "new: New" in found[0] and "missing: Old" in found[0]
    rows = er.compare(table(Name=["B1", "B2"]), table(Name=["B1"]), "t")
    assert rows == ["t: 2 rows, the reference has 1"]


def test_the_names_etabs_gives_by_itself_are_left_out():
    """ETABS makes the joints of its mesh again at each analysis, with a new GUID."""
    a = table(UniqueName=["1", "2"], X=[0.0, 6000.0], GUID=["aa", "bb"], PointBay=["33", "7"])
    b = table(UniqueName=["1", "2"], X=[0.0, 6000.0], GUID=["cc", "dd"], PointBay=["36", "7"])
    assert er.compare(a, b, "POINTS") == []


def test_nested_results_are_compared_through():
    now = {"status": "converged", "changes": [{"member": "2GX-1", "new": "G_300X600"}],
           "beams": table(Bars=[4])}
    ref = {"status": "converged", "changes": [{"member": "2GX-1", "new": "G_300X650"}],
           "beams": table(Bars=[4])}
    assert er.compare(now, ref, "design") == [
        "design.changes[0].new: 'G_300X600', the reference has 'G_300X650'"]
    assert er.compare({"a": 1}, {"a": 1, "b": 2}, "r") == ["r.b: missing"]
    assert er.compare({"a": 1, "c": 3}, {"a": 1}, "r") == ["r.c: not in the reference"]
    assert er.compare([1, 2], [1], "r") == ["r: 2 items, the reference has 1"]


# --------------------------------------------------------------------------
# WHAT A COMMAND RETURNED, AS PLAIN DATA
# --------------------------------------------------------------------------
@dataclass
class _Finding:
    group: str
    status: str
    text: str


@dataclass
class _Report:
    model_path: str
    findings: list
    periods: pd.DataFrame
    weight: float


def test_results_become_plain_data_without_paths():
    report = _Report(r"C:\run 1\model.EDB", [_Finding("Seismic", "OK", "Z = 0.4")],
                     table(Mode=[1], Period=[1.2]), np.float64(14567.5))
    data = er.plain(report)
    assert "model_path" not in data                    # where the run was is not a result
    assert data["findings"] == [{"group": "Seismic", "status": "OK", "text": "Z = 0.4"}]
    assert isinstance(data["weight"], float)
    assert isinstance(data["periods"], pd.DataFrame)
    other = er.plain(_Report(r"D:\run 2\model.EDB", [_Finding("Seismic", "OK", "Z = 0.4")],
                             table(Mode=[1], Period=[1.2]), 14567.5))
    assert er.compare(data, other) == []


def test_a_design_store_is_compared_table_by_table():
    from design.concrete.workflow import DesignStore

    def store(depth):
        s = DesignStore(r"C:\a\m.EDB", 1.0, {"FRAME DATA": table(Depth=[depth])}, {"smrf": True})
        s.beam_results = table(UniqueName=["2GX-1"], n_left_L1=[4])
        return er.plain(s)

    assert er.compare(store(500), store(500)) == []
    assert "tables.FRAME DATA[Depth]" in er.compare(store(500), store(550))[0]


# --------------------------------------------------------------------------
# THE SCENARIO
# --------------------------------------------------------------------------
def scenario_file(tmp_path, **changes) -> str:
    data = {"model": "models/t.EDB", "steps": [
        {"command": "beams", "answers": []}, {"command": "check", "answers": []},
        {"command": "beams", "answers": []}]}
    data.update(changes)
    path = tmp_path / "frame.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_a_scenario_gets_its_folders_beside_it_and_its_steps_named(tmp_path):
    scenario = er.read_scenario(scenario_file(tmp_path))
    assert scenario["model"] == str(tmp_path / "models" / "t.EDB")
    assert scenario["work"] == str(tmp_path / "frame run")
    assert scenario["reference"] == str(tmp_path / "frame reference")
    assert [s["name"] for s in scenario["steps"]] == ["beams", "check", "beams 2"]
    assert [s["number"] for s in scenario["steps"]] == [1, 2, 3]


def test_steps_are_picked_by_name_or_number(tmp_path):
    steps = er.read_scenario(scenario_file(tmp_path))["steps"]
    assert [s["name"] for s in er.chosen_steps(steps, "check, 3")] == ["check", "beams 2"]
    assert er.chosen_steps(steps, None) == steps
    with pytest.raises(ValueError, match="No step"):
        er.chosen_steps(steps, "drift")


def test_a_scenario_without_model_or_steps_is_refused(tmp_path):
    with pytest.raises(ValueError, match="model"):
        er.read_scenario(scenario_file(tmp_path, steps=[]))
    with pytest.raises(ValueError, match="command"):
        er.read_scenario(scenario_file(tmp_path, steps=[{"answers": []}]))


def test_the_run_works_on_a_copy_in_an_emptied_folder(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "t.EDB").write_bytes(b"model")
    (models / "t.setup.json").write_text("{}", encoding="utf-8")
    scenario = er.read_scenario(scenario_file(tmp_path))
    stale = Path(scenario["work"])
    stale.mkdir()
    (stale / "old result.txt").write_text("from the run before", encoding="utf-8")

    copy = er.prepare_work_folder(scenario)

    assert Path(copy) == stale / "t.EDB" and Path(copy).read_bytes() == b"model"
    assert (stale / "t.setup.json").is_file()          # the saved inputs go with the model
    assert not (stale / "old result.txt").exists()     # nothing is left of the run before
    assert (stale / "settings").is_dir()               # the empty settings folder of the run
    assert (models / "t.EDB").read_bytes() == b"model"  # the model itself is not touched


def test_the_work_folder_cannot_be_the_folder_of_the_model(tmp_path):
    (tmp_path / "t.EDB").write_bytes(b"model")
    scenario = er.read_scenario(scenario_file(tmp_path, model="t.EDB", work="."))
    with pytest.raises(ValueError, match="work folder"):
        er.prepare_work_folder(scenario)
    assert (tmp_path / "t.EDB").is_file()


def test_the_settings_folder_of_a_run_replaces_the_one_of_the_machine(tmp_path, monkeypatch):
    from utilities import user_settings

    monkeypatch.setenv("SDT_SETTINGS_DIR", str(tmp_path / "settings"))
    assert user_settings.settings_path("concrete_design.json") == str(
        tmp_path / "settings" / "concrete_design.json")


def test_units_are_named_as_etabs_names_them():
    assert er.unit_code("kN-m") == 6 and er.unit_code("N-mm") == 9 and er.unit_code(None) is None
    with pytest.raises(ValueError, match="not known"):
        er.unit_code("furlongs")


def test_the_report_says_passed_only_when_everything_was_compared():
    ok = er.StepResult("beams", "beams", "ok", comparison="identical", compared=True)
    saved = er.StepResult("beams", "beams", "ok", comparison="reference saved")
    differs = er.StepResult("columns", "columns", "ok", compared=True,
                            differences=["columns[Pu]: 1 of 2 values differ"])
    assert "PASSED" in er.report_text("s.json", [ok], {})
    assert "REFERENCE SAVED" in er.report_text("s.json", [saved], {})
    assert "RAN: every step ran" in er.report_text(
        "s.json", [ok, er.StepResult("check", "check", "ok", comparison="no reference")], {})
    text = er.report_text("s.json", [ok, differs], {})
    assert "FAILED" in text and "columns[Pu]" in text
    assert "FAILED" in er.report_text("s.json", [], {})
