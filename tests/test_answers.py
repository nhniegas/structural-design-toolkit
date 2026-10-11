"""
tests/test_answers.py
=====================
The answers file (utilities/answers.py): a command run with no dialog gets
from the file exactly what the dialog would have returned. No window is
opened here: a dialog that is not answered raises, it never waits.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utilities import _gui_helpers, answers  # noqa: E402
from utilities.answers import AnswersError  # noqa: E402


@pytest.fixture(autouse=True)
def no_session():
    answers.stop()
    yield
    answers.stop()


def test_without_a_file_the_dialogs_are_asked():
    assert answers.option("T", "Q?", ["Yes", "No"]) is answers.ASK
    assert answers.values("T", "Q", ["a"]) is answers.ASK
    assert answers.items("T", ["a"]) is answers.ASK
    assert answers.folder("T") is answers.ASK
    assert not answers.warning("m", "t") and not answers.summary("s", "t")


@pytest.mark.parametrize("answer, expected", [
    ("No", "No"), ("no", "No"), (1, "No"),
    ("Yes - a beam", "Yes - a beam is at least as deep"),   # the start of the option
    ("as deep", "Yes - a beam is at least as deep"),        # a piece only one option has
])
def test_a_choice_is_the_option_its_start_or_its_number(answer, expected):
    answers.use([{"ask": "carrier", "answer": answer}])
    options = ["No", "Yes - a beam is at least as deep"]
    assert _gui_helpers.select_option("Beam Design", "Carrier depth?", options) == expected


def test_a_choice_that_fits_two_options_is_refused():
    answers.use([{"ask": "Q", "answer": "Yes"}])
    with pytest.raises(AnswersError, match="not one of its choices"):
        _gui_helpers.select_option("T", "Q", ["Yes - one", "Yes - two"])


def test_a_closed_dialog_is_null():
    answers.use([{"ask": "Q", "answer": None}, {"ask": "boxes", "answer": None}])
    assert _gui_helpers.select_option("T", "Q", ["Yes", "No"]) is None
    assert _gui_helpers.enter_values("T", "boxes", ["a"], {"a": "1"}) is None


def test_boxes_keep_what_the_dialog_offers_unless_answered():
    answers.use([{"ask": "bars and cover", "answer": {"Beam main bar (mm)": 25, "cover": "50"}}])
    labels = ["Beam main bar (mm)", "Beam stirrup (mm)", "Beam cover (mm)"]
    typed = _gui_helpers.enter_values("Beam Design", "Beam bars and cover.", labels,
                                      {"Beam main bar (mm)": "28", "Beam stirrup (mm)": "12",
                                       "Beam cover (mm)": "40"})
    # exactly what the dialog returns: every box, as text
    assert typed == {"Beam main bar (mm)": "25", "Beam stirrup (mm)": "12",
                     "Beam cover (mm)": "50"}


def test_a_box_that_does_not_exist_is_refused():
    answers.use([{"ask": "Q", "answer": {"width": "300"}}])
    with pytest.raises(AnswersError, match="has no box"):
        _gui_helpers.enter_values("T", "Q", ["depth"], {"depth": "500"})


def test_a_list_takes_items_or_all():
    answers.use([{"ask": "Levels", "answer": ["GF"]}, {"ask": "Combinations", "answer": "all"}])
    assert _gui_helpers.DualListboxSelector("Levels whose beams", ["GF", "2F"]).show() == ["GF"]
    assert _gui_helpers.DualListboxSelector("Combinations", ["a", "b"]).show() == ["a", "b"]


def test_an_item_that_is_not_listed_is_refused():
    answers.use([{"ask": "Levels", "answer": ["9F"]}])
    with pytest.raises(AnswersError, match="does not list: 9F"):
        _gui_helpers.DualListboxSelector("Levels", ["GF", "2F"])


def test_answers_are_used_once_and_in_order():
    answers.use([{"ask": "Target", "answer": "Girders"}, {"ask": "Target", "answer": "Beams"}])
    assert _gui_helpers.select_option("T", "Target ratios", ["Girders", "Beams"]) == "Girders"
    assert _gui_helpers.select_option("T", "Target ratios", ["Girders", "Beams"]) == "Beams"
    with pytest.raises(AnswersError, match="No answer"):
        _gui_helpers.select_option("T", "Target ratios", ["Girders", "Beams"])


def test_a_repeating_answer_serves_every_dialog_it_fits():
    answers.use([{"ask": "Continue", "answer": "Continue", "repeat": True}])
    for _ in range(3):
        assert _gui_helpers.select_option("T", "Continue?", ["Continue", "Stop"]) == "Continue"
    assert answers.unused() == []


def test_a_dialog_without_an_answer_stops_by_default_and_names_its_choices():
    answers.use([])
    with pytest.raises(AnswersError) as error:
        _gui_helpers.select_option("Beam Design", "SMRF (seismic) design?", ["Yes", "No"])
    assert "Beam Design: SMRF (seismic) design?" in str(error.value)
    assert "Yes | No" in str(error.value)


def test_unanswered_default_takes_what_the_dialog_offers_and_says_so(capsys):
    answers.use({"unanswered": "default", "answers": []})
    assert _gui_helpers.select_option("T", "Q", ["a", "b"], default_index=1) == "b"
    assert _gui_helpers.enter_values("T", "boxes", ["x"], {"x": "7"}) == {"x": "7"}
    assert _gui_helpers.DualListboxSelector("List", ["p", "q"]).show() == ["p", "q"]
    out = capsys.readouterr().out
    assert out.count("(default)") == 3
    assert all("(default)" in line for line in answers.transcript())


def test_an_answer_that_is_never_accepted_ends_the_run():
    answers.use({"unanswered": "default", "answers": []})
    with pytest.raises(AnswersError, match="was asked"):
        for _ in range(answers.MAX_REPEATS + 1):
            _gui_helpers.enter_values("T", "A number.", ["x"], {"x": "not a number"})


def test_folders_and_files_start_at_the_folder_of_the_answers_file(tmp_path):
    path = tmp_path / "run.json"
    (tmp_path / "plan.dxf").write_text("0", encoding="utf-8")
    path.write_text(json.dumps({"folder": "out", "answers": [
        {"ask": "framing plan DXF", "answer": "plan.dxf"},
        {"ask": "Save the new ETABS model", "answer": "models/NEW"}]}), encoding="utf-8")
    answers.load(str(path))
    assert _gui_helpers.select_output_directory("Folder for the results") == str(tmp_path / "out")
    assert (tmp_path / "out").is_dir()
    assert _gui_helpers.select_open_path("Select the framing plan DXF", ".dxf", "DXF") == str(
        tmp_path / "plan.dxf")
    assert _gui_helpers.select_save_path("Save the new ETABS model as", "New Model", ".EDB",
                                         "ETABS model") == str(tmp_path / "models" / "NEW.EDB")
    # a file to save with no answer goes to the folder, under the name the dialog offers
    assert _gui_helpers.select_save_file("Wind Report") == str(tmp_path / "out" / "Wind Report.pdf")


def test_a_folder_question_without_folder_or_answer_stops():
    answers.use([])
    with pytest.raises(AnswersError, match="folder"):
        _gui_helpers.select_output_directory("Folder for the results")


def test_a_file_to_open_that_is_missing_is_refused(tmp_path):
    answers.use([{"ask": "DXF", "answer": "missing.dxf"}], str(tmp_path))
    with pytest.raises(AnswersError, match="does not exist"):
        _gui_helpers.select_open_path("Select the DXF", ".dxf", "DXF")


def test_warnings_and_the_summary_are_printed_not_shown(capsys):
    answers.use([])
    _gui_helpers.show_warning("Save the model first.", title="Beam Design")
    _gui_helpers.show_summary("SUMMARY - sdt beams\nBeams designed  12", "Summary")
    out = capsys.readouterr().out
    assert "warning - Beam Design: Save the model first." in out
    assert "Beams designed  12" in out
    assert answers.warnings() == ["Beam Design: Save the model first."]
    assert answers.summaries() == ["SUMMARY - sdt beams\nBeams designed  12"]


def test_no_loading_window_is_started():
    answers.use([])
    with _gui_helpers.LoadingWindow("Beam design") as window:
        window.update("a line")
        assert window.proc is None


def test_unused_answers_are_reported():
    answers.use([{"ask": "one", "answer": "a"}, {"ask": "two", "answer": "b"}])
    _gui_helpers.select_option("T", "one", ["a", "b"])
    assert answers.stop() == ["two"]
    assert not answers.replaying()


@pytest.mark.parametrize("data, message", [
    ({"answers": [{"ask": "x"}]}, "needs"),
    ({"unanswered": "guess", "answers": []}, "unanswered"),
    ("text", "holds an object"),
])
def test_a_file_that_is_not_an_answers_file_is_refused(data, message):
    with pytest.raises(AnswersError, match=message):
        answers.use(data)


def test_a_file_that_is_missing_or_not_json_is_refused(tmp_path):
    with pytest.raises(AnswersError, match="cannot be read"):
        answers.load(str(tmp_path / "none.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(AnswersError, match="not valid JSON"):
        answers.load(str(bad))


def test_a_recording_plays_back_the_same_answers(tmp_path, monkeypatch):
    """What --record saves is what --answers gives back, dialog by dialog."""
    path = tmp_path / "recorded.json"
    answers.record(str(path))
    asked = [("option", "Beam Design", "SMRF (seismic) design?\n\nMore text.", "Yes"),
             ("values", "Beam Design", "Beam bars and cover.", {"Beam main bar (mm)": "25"}),
             ("option", "Beam Design", "Carrier depth?", None)]
    for kind, title, prompt, answer in asked:
        answers.keep(title, prompt, answer)       # what a dialog does when it closes
    answers.keep("Levels whose beams get 75 mm cover", "", ["GF"])
    answers.stop()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["unanswered"] == "stop" and len(saved["answers"]) == 4

    answers.load(str(path))
    assert _gui_helpers.select_option("Beam Design", "SMRF (seismic) design?\n\nMore text.",
                                      ["Yes", "No"]) == "Yes"
    assert _gui_helpers.enter_values("Beam Design", "Beam bars and cover.",
                                     ["Beam main bar (mm)"], {"Beam main bar (mm)": "28"}
                                     ) == {"Beam main bar (mm)": "25"}
    assert _gui_helpers.select_option("Beam Design", "Carrier depth?", ["No", "Yes"]) is None
    assert _gui_helpers.DualListboxSelector("Levels whose beams get 75 mm cover",
                                            ["GF", "2F"]).show() == ["GF"]
    assert answers.stop() == []


def test_sdt_runs_a_command_from_an_answers_file(tmp_path, monkeypatch, capsys):
    """sdt wind --answers: the inputs, the output choice and the summary, with no window."""
    import main
    from utilities import design_cli

    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    path = tmp_path / "wind.json"
    path.write_text(json.dumps({"unanswered": "default", "answers": [
        {"ask": "What should be done with the results", "answer": "Print the results"}]}),
        encoding="utf-8")
    assert main.main(["wind", "--answers", str(path)]) == 0
    out = capsys.readouterr().out
    assert "[answers]" in out and "SUMMARY - sdt wind" in out
    assert not answers.replaying()     # the command ended: back to the dialogs


def test_sdt_stops_with_a_message_when_an_answer_is_missing(tmp_path, monkeypatch, capsys):
    import main
    from utilities import design_cli

    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    path = tmp_path / "empty.json"
    path.write_text('{"answers": []}', encoding="utf-8")
    assert main.main(["wind", "--answers", str(path)]) == 2
    assert "wind stopped: No answer in empty.json" in capsys.readouterr().out
    assert not answers.replaying()


def test_the_etabs_of_a_test_run_is_the_only_one_attached(monkeypatch):
    """SDT_ETABS_PID: a command never attaches to another ETABS that is open."""
    from etabs_api.core import connection

    calls = []

    class Helper:
        def GetObject(self, name):
            calls.append("any")
            return "another ETABS"

        def GetObjectProcess(self, name, pid):
            calls.append(pid)
            return f"ETABS {pid}"

    monkeypatch.setenv("SDT_ETABS_PID", "4321")
    assert connection.running_etabs(Helper()) == "ETABS 4321"
    assert calls == [4321]
    monkeypatch.delenv("SDT_ETABS_PID")
    assert connection.running_etabs(Helper()) == "another ETABS"
    assert os.environ.get("SDT_ETABS_PID") is None
