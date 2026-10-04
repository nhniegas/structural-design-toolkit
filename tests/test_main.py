"""
tests/test_main.py
==================
Checks for main.py, the terminal entry point of the ETABS workflows and design
checks. The workflows themselves are replaced, so nothing opens a dialog or ETABS.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


@pytest.mark.parametrize("command", ["setup", "grids", "tag", "check", "analyze", "drift", "beams", "deflection", "columns", "design", "composite", "steel", "wind", "doctor"])
def test_each_command_runs_its_workflow(command, monkeypatch):
    ran = []
    for name in main.COMMANDS:
        monkeypatch.setitem(main.COMMANDS, name, (lambda n=name: ran.append(n), "text"))
    assert main.main([command]) == 0
    assert ran == [command]


def test_no_command_prints_the_list(capsys):
    assert main.main([]) == 1
    out = capsys.readouterr().out
    assert all(name in out for name in main.COMMANDS)


def test_unknown_command_is_refused():
    with pytest.raises(SystemExit):
        main.main(["not-a-command"])


def test_tagger_loads_as_a_script():
    """`python etabs_api/workflows/frame_tagger.py` must find its own package."""
    import runpy

    script = Path(__file__).resolve().parents[1] / "etabs_api" / "workflows" / "frame_tagger.py"
    module = runpy.run_path(str(script), run_name="not_main")
    assert callable(module["auto_tag_frames"])


@pytest.mark.parametrize("script, prog", [("sdt", "sdt"), ("xs", "xs"), ("main.py", "python main.py")])
def test_help_names_the_command_that_was_typed(script, prog, monkeypatch):
    monkeypatch.setattr(sys, "argv", [script])
    assert main.build_parser().prog == prog


def test_saved_answers_move_from_the_old_settings_folder(tmp_path, monkeypatch):
    from utilities import user_settings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    old = tmp_path / user_settings.OLD_FOLDER
    old.mkdir()
    (old / "concrete_design.json").write_text('{"smrf": true}', encoding="utf-8")
    path = Path(user_settings.settings_path("concrete_design.json"))
    assert path.parent.name == user_settings.FOLDER
    assert path.read_text(encoding="utf-8") == '{"smrf": true}'


# ----------------------------------------------------------------- the menu and the packaged program
def test_version_matches_pyproject():
    import tomllib

    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert main.VERSION == tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]


def test_menu_choice_by_number_name_or_start_of_name():
    names = list(main.COMMANDS)
    assert main.menu_choice("1") == names[0]
    assert main.menu_choice(str(len(names))) == names[-1]
    assert main.menu_choice(" Beams ") == "beams"
    assert main.menu_choice("col") == "columns"
    assert main.menu_choice("de") is None  # deflection or design
    assert main.menu_choice("0") is None and main.menu_choice("99") is None
    assert main.menu_choice("") is None


def test_menu_runs_what_is_typed_until_quit(monkeypatch, capsys):
    ran = []
    for name in main.COMMANDS:
        monkeypatch.setitem(main.COMMANDS, name, (lambda n=name: ran.append(n), "text"))
    typed = iter(["beams", "nonsense", "2", "q"])
    assert main.menu(read=lambda prompt: next(typed)) == 0
    assert ran == ["beams", list(main.COMMANDS)[1]]
    assert "is not one of the commands" in capsys.readouterr().out


def test_an_error_in_a_command_returns_to_the_menu(monkeypatch, capsys):
    def broken():
        raise RuntimeError("ETABS went away")

    monkeypatch.setitem(main.COMMANDS, "beams", (broken, "text"))
    typed = iter(["beams", "q"])
    assert main.menu(read=lambda prompt: next(typed)) == 0
    assert "ETABS went away" in capsys.readouterr().err


def test_no_command_in_a_terminal_opens_the_menu(monkeypatch):
    class Terminal:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(main, "menu", lambda: 7)
    assert main.main([]) == 7


def test_the_hidden_argument_opens_the_progress_window(monkeypatch):
    from utilities import _gui_helpers

    shown = []
    monkeypatch.setattr(_gui_helpers, "run_loading_window", shown.append)
    assert main.main([main.LOADING_WINDOW_FLAG, "Reading the model's \"tables\""]) == 0
    assert shown == ["Reading the model's \"tables\""]
    assert main.LOADING_WINDOW_FLAG == _gui_helpers.LOADING_WINDOW_FLAG


def test_the_packaged_program_starts_itself_for_the_progress_window(monkeypatch):
    from utilities import _gui_helpers

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Tools\sdt\sdt.exe")
    assert _gui_helpers.loading_window_command("Working") == [
        r"C:\Tools\sdt\sdt.exe", "--loading-window", "Working"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = _gui_helpers.loading_window_command("Working")
    assert command[1] == "-c" and command[-1] == "Working"


def test_doctor_reports_every_check_and_counts_the_problems(monkeypatch, capsys):
    from utilities import doctor

    monkeypatch.setattr(doctor, "check_dialogs", lambda: (doctor.OK, "Dialogs", "fine"))
    monkeypatch.setattr(doctor, "check_modules", lambda: (doctor.OK, "Commands", "fine"))
    monkeypatch.setattr(doctor, "check_loading_window",
                        lambda: (doctor.OK, "Progress window", "fine"))
    monkeypatch.setattr(doctor, "check_etabs_program",
                        lambda: (doctor.WARN, "ETABS program", "not found"))
    monkeypatch.setattr(doctor, "check_etabs_api", lambda: (doctor.FAIL, "ETABS API", "no"))
    monkeypatch.setattr(doctor, "check_running_etabs",
                        lambda: pytest.fail("not checked when the API did not load"))
    monkeypatch.setattr(doctor, "check_latex", lambda: (doctor.OK, "LaTeX", "found"))
    checks = doctor.run_doctor("0.1.0")
    out = capsys.readouterr().out
    assert [status for status, _, _ in checks].count(doctor.FAIL) == 1
    assert "[FAIL] ETABS API: no" in out and "1 problem(s), 1 warning(s)" in out
    assert "version 0.1.0" in out


def test_the_menu_opens_with_the_author_and_the_terms(capsys):
    assert main.menu(read=lambda prompt: "q") == 0
    out = capsys.readouterr().out
    assert out.index(main.AUTHOR) < out.index("Commands:")
    for text in (main.VERSION, main.PROFILE, main.REPOSITORY, "MIT", "engineering"):
        assert text in out


def test_doctor_loads_the_module_of_every_command():
    from utilities import doctor

    status, _, found = doctor.check_modules()
    assert status == doctor.OK, found
