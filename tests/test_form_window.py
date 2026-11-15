"""The one-window form of the commands (v0.18.0): the session, with a scripted front end."""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utilities import _gui_helpers as gui  # noqa: E402
from utilities import form_window  # noqa: E402


class Scripted:
    """A front end that plays the user: each wait takes the next scripted event.

    An event is ("answer", <question text>, value), ("run",) or ("cancel",). It
    records what the form showed.
    """

    def __init__(self, events):
        self.events = list(events)
        self.shown = []          # (index, question text, answer) of the last pass
        self.passes = 0
        self.warnings = []
        self.states = []
        self.closed = False
        self.sections = []

    def begin_pass(self):
        self.passes += 1
        self.shown = []
        self.warnings = []

    def show(self, index, question, answer):
        del self.sections[index:]
        self.sections.append(question)
        self.shown.append((index, question.question, answer))

    def question_at(self, index):
        return self.sections[index] if index < len(self.sections) else None

    def trim(self, count):
        del self.sections[count:]

    def warn(self, message):
        self.warnings.append(message)

    def ready(self):
        self.states.append("ready")

    def ready_again(self, index):
        self.states.append(f"again {index}")

    def stopped(self, warnings):
        self.states.append("stopped")

    def wait(self, index):
        if not self.events:
            return ("cancel",)
        event = self.events.pop(0)
        if event[0] == "answer":
            text = event[1]
            where = next(i for i, q in enumerate(self.sections) if q.question == text)
            return ("answer", where, event[2])
        return event

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_saved_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("SDT_SETTINGS_DIR", str(tmp_path))


def command_with_a_dependent_question(log):
    """SMRF? then, only with SMRF, the girders under 4d; then a folder; then Run."""
    smrf = gui.select_option("Beam Design", "SMRF (seismic) design?", ["Yes", "No"])
    if smrf is None:
        return None
    short = None
    if smrf == "Yes":
        short = gui.select_option("Beam Design", "Girders with a clear span under 4d.",
                                  ["Apply the SMRF rules", "Design them without"])
    bars = gui.enter_values("Beam Design", "Beam bars and cover.", ["Main bar (mm)", "Cover"],
                            {"Main bar (mm)": "25", "Cover": "40"})
    folder = gui.select_output_directory("Folder for the results")
    if not folder:
        return None
    form_window.ready_to_run()
    log.append("work")
    return smrf, short, bars, folder


def test_defaults_fill_the_form_and_only_the_folder_waits(tmp_path):
    log = []
    front = Scripted([("answer", "Folder for the results", str(tmp_path)), ("run",)])
    result = form_window.run("beams", lambda: command_with_a_dependent_question(log), front)
    assert result == ("Yes", "Apply the SMRF rules",
                      {"Main bar (mm)": "25", "Cover": "40"}, str(tmp_path))
    assert log == ["work"]
    assert front.passes == 1            # nothing was asked again
    assert front.states == ["ready"]
    assert front.closed


def test_a_changed_answer_asks_again_and_the_dependent_question_goes(tmp_path):
    log = []
    front = Scripted([("answer", "Folder for the results", str(tmp_path)),
                      ("answer", "SMRF (seismic) design?", "No"),
                      ("run",)])
    result = form_window.run("beams", lambda: command_with_a_dependent_question(log), front)
    assert result[0] == "No" and result[1] is None
    assert result[3] == str(tmp_path)          # kept from the first pass
    assert front.passes == 2
    assert [text for _, text, _ in front.shown] == [
        "SMRF (seismic) design?", "Beam bars and cover.", "Folder for the results"]
    assert log == ["work"]                     # the work ran once, after Run


def test_values_typed_are_kept_when_an_earlier_answer_changes(tmp_path):
    log = []
    front = Scripted([("answer", "Folder for the results", str(tmp_path)),
                      ("answer", "Beam bars and cover.", {"Main bar (mm)": "20", "Cover": "50"}),
                      ("answer", "Girders with a clear span under 4d.", "Design them without"),
                      ("run",)])
    result = form_window.run("beams", lambda: command_with_a_dependent_question(log), front)
    assert result[1] == "Design them without"
    assert result[2] == {"Main bar (mm)": "20", "Cover": "50"}


def test_cancel_stops_before_any_work(tmp_path):
    log = []
    front = Scripted([("answer", "Folder for the results", str(tmp_path)), ("cancel",)])
    assert form_window.run("beams", lambda: command_with_a_dependent_question(log), front) is None
    assert log == []
    assert front.closed


def test_a_value_the_command_refuses_keeps_the_form_open(tmp_path):
    """The command warns and stops: the form shows it and waits for a change."""
    def command():
        typed = gui.enter_values("Check", "Drift limit.", ["H /"], {"H /": "abc"})
        try:
            float(typed["H /"])
        except ValueError:
            gui.show_warning("The drift limit must be a number.")
            return None
        form_window.ready_to_run()
        return float(typed["H /"])

    front = Scripted([("answer", "Drift limit.", {"H /": "400"}), ("run",)])
    assert form_window.run("check", command, front) == 400.0
    assert front.states == ["stopped", "ready"]


def test_a_question_asked_again_until_valid_waits_on_its_own_section():
    def command():
        while True:
            typed = gui.enter_values("Wind", "Inputs of the check.", ["V (m/s)"], {"V (m/s)": "x"})
            try:
                value = float(typed["V (m/s)"])
                break
            except ValueError:
                gui.show_warning("The check could not run.")
        form_window.ready_to_run()
        return value

    front = Scripted([("answer", "Inputs of the check.", {"V (m/s)": "55"}), ("run",)])
    assert form_window.run("wind", command, front) == 55.0
    assert front.states == ["again 0", "ready"]
    assert len(front.sections) == 1


def test_a_list_waits_until_items_are_chosen():
    def command():
        chosen = gui.DualListboxSelector("Select the combinations", ["ULS1", "ULS2"]).show()
        form_window.ready_to_run()
        return chosen

    front = Scripted([("answer", "Select the combinations", ["ULS2"]), ("run",)])
    assert form_window.run("drift", command, front) == ["ULS2"]


def test_a_warning_before_any_question_is_a_window_of_its_own(monkeypatch):
    real = gui.show_warning
    windows = []

    def show_warning(message, title="Warning", topmost=True):
        if form_window.active() is not None:
            return real(message, title, topmost)       # to the form
        windows.append(message)                        # a window of its own

    monkeypatch.setattr(gui, "show_warning", show_warning)

    def command():
        gui.show_warning("ETABS is not running.")
        return None

    front = Scripted([])
    form_window.run("beams", command, front)
    assert front.warnings == ["ETABS is not running."]    # said in the form first
    assert windows == ["ETABS is not running."]           # then shown on its own


def test_without_a_form_the_run_point_does_nothing():
    assert form_window.active() is None
    form_window.ready_to_run()               # no session: returns at once


def test_folders_are_remembered_for_the_next_run(tmp_path):
    log = []
    front = Scripted([("answer", "Folder for the results", str(tmp_path)), ("run",)])
    form_window.run("beams", lambda: command_with_a_dependent_question(log), front)
    again = Scripted([("run",)])            # nothing to wait for now
    result = form_window.run("beams", lambda: command_with_a_dependent_question(log), again)
    assert result[3] == str(tmp_path)


def test_sdt_dialogs_keeps_one_dialog_after_the_other(monkeypatch):
    monkeypatch.setenv("SDT_DIALOGS", "1")
    assert not form_window.wanted()
    monkeypatch.delenv("SDT_DIALOGS")
    assert form_window.wanted()


# every command marks where its questions end
RUN_POINTS = {
    "etabs_api/workflows/model_setup.py": "run_model_setup",
    "etabs_api/workflows/model_framing.py": "run_model",
    "etabs_api/workflows/frame_tagger.py": "auto_tag_frames",
    "etabs_api/workflows/model_check.py": "run_model_check",
    "etabs_api/workflows/model_analysis.py": "run_model_analysis",
    "etabs_api/workflows/drift_check.py": "run_drift_check",
    "design/concrete/workflow.py": ("run_beams", "run_schedules", "run_deflection", "run_columns"),
    "etabs_api/workflows/wall_design.py": "run_walls_cli",
    "etabs_api/workflows/compatibility.py": "run_compatibility_cli",
    "etabs_api/workflows/design_loop.py": "run_design_cli",
    "etabs_api/workflows/irregularity.py": "run_irregularity",
    "etabs_api/workflows/foundation_model.py": "run_foundation",
    "etabs_api/workflows/framing_plans.py": "run_framing_plans",
    "etabs_api/workflows/model_3d.py": "run_model_3d",
    "etabs_api/workflows/concrete_quantities.py": "run_quantities",
    "utilities/design_cli.py": "run_design",
}


@pytest.mark.parametrize("path,names", RUN_POINTS.items())
def test_every_command_has_its_run_point(path, names):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in (names,) if isinstance(names, str) else names:
        calls = [n for n in ast.walk(functions[name]) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", "")) == "ready_to_run"]
        assert calls, f"{path}:{name} has no ready_to_run()"
