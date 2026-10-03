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


@pytest.mark.parametrize("command", ["setup", "grids", "tag", "analyze", "beams", "deflection", "columns", "design", "composite", "steel", "wind"])
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
