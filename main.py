"""Terminal entry point for the ETABS workflows and the design checks.

Every tool is a command of ``sdt`` (installed by ``pip install -e .``, see
pyproject.toml); ``python main.py <command>`` from the project folder runs
the same commands.

    sdt setup       define materials, frame sections, load patterns, the UBC 97
                    response spectrum, load cases, mass source, P-delta and
                    load combinations       (etabs_api/workflows/model_setup.py)
    sdt grids       build or update the stories, grids, columns and walls from
                    a DXF of framing plans  (etabs_api/workflows/grid_column_model.py)
    sdt tag         give every beam and column of the open model its unique
                    name, in a tagged copy  (etabs_api/workflows/frame_tagger.py)
    sdt check       check the open model for missing or inconsistent inputs
                    (read only; etabs_api/workflows/model_check.py)
    sdt analyze     run the analysis, scale the response spectrum to the static
                    base shear, check periods, modal mass and weight
                    (etabs_api/workflows/model_analysis.py)
    sdt drift       story drift of the DRIFT / WDRIFT combinations with the drift
                    stiffness, strength and service level; the model is restored
                    (etabs_api/workflows/drift_check.py)
    sdt beams       extract the forces and design the beams; save the results,
                    calculations and schedules (design/concrete_workflow.py)
    sdt deflection  check only the deflection of the beams (bars of the last
                    beam design, service moments read again from ETABS)
    sdt columns     design the columns from the stored beam step, with
                    slenderness; save the results, calculations and schedule
    sdt design      analysis and beam/column design loop that resizes the
                    members until they pass (etabs_api/workflows/design_loop.py)
    sdt plans       framing plans of every floor as one DXF: beams at their
                    width, columns at their size, marks and grids
                    (etabs_api/workflows/framing_plans.py)
    sdt composite   rectangular filled composite column, AISC DG6
                    (design/composite_column_designer_aiscDG06.py)
    sdt steel       wide-flange member, AISC 360-22
                    (design/general_steel_section_designer_aisc360.py)
    sdt wind        MWFRS wind pressures, ASCE 7 directional procedure
                    (design/wind_calculator_directional_asce7.py)
    sdt doctor      check that this machine can run the toolkit: ETABS and its
                    API, the dialogs, LaTeX     (utilities/doctor.py)
    sdt --help      list the commands
    sdt --version   the version

``sdt`` alone, and the packaged ``sdt.exe`` when it is double-clicked, shows
a menu of the commands: type a number or a name, and the menu comes back when
the command ends.

Each command asks for its inputs in dialogs. The standalone checks
(composite, steel, wind) then ask whether to print the results in the
terminal, export a PDF report, or both. The terminal shows the detailed
results; when a command finishes, a separate window shows its summary: what
was run, the counts, what needs attention and the files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

VERSION = "0.3.2"  # the same as in pyproject.toml (tests/test_main.py checks it)
# Hidden first argument: this process is the progress window of another one
# (utilities/_gui_helpers.LoadingWindow). It is how the packaged program,
# which has no separate Python to start, opens that window.
LOADING_WINDOW_FLAG = "--loading-window"

AUTHOR = "Nhel Harold Niegas"
PROFILE = "https://github.com/nhniegas"
REPOSITORY = "https://github.com/nhniegas/structural-design-toolkit"


def banner() -> str:
    """What the menu shows first: the program, its author and the terms of use."""
    rule = "=" * 72
    return "\n".join([
        rule,
        f"Structural Design Toolkit {VERSION}",
        "ETABS automation and structural design checks (NSCP 2015, ACI 318M-14)",
        "",
        f"Author:   {AUTHOR}",
        f"Profile:  {PROFILE}",
        f"Source:   {REPOSITORY}",
        f"License:  MIT (c) 2026 {AUTHOR}. Provided as is, without warranty.",
        "",
        "These tools automate calculations; they do not replace engineering",
        "judgement. Check the results independently before using them for design.",
        rule,
    ])


def _setup():
    from etabs_api.workflows.model_setup import run_model_setup

    return run_model_setup()


def _grids():
    from etabs_api.workflows.grid_column_model import run_grid_column_model

    return run_grid_column_model()


def _tag():
    from etabs_api.workflows.frame_tagger import auto_tag_frames

    return auto_tag_frames()


def _check():
    from etabs_api.workflows.model_check import run_model_check

    return run_model_check()


def _analyze():
    from etabs_api.workflows.model_analysis import run_model_analysis

    return run_model_analysis()


def _drift():
    from etabs_api.workflows.drift_check import run_drift_check

    return run_drift_check()


def _beams():
    from design.concrete_workflow import run_beams

    return run_beams()


def _deflection():
    from design.concrete_workflow import run_deflection

    return run_deflection()


def _columns():
    from design.concrete_workflow import run_columns

    return run_columns()


def _design():
    from etabs_api.workflows.design_loop import run_design_cli

    return run_design_cli()


def _plans():
    from etabs_api.workflows.framing_plans import run_framing_plans

    return run_framing_plans()


def _composite():
    from design.composite_column_designer_aiscDG06 import run

    return run()


def _steel():
    from design.general_steel_section_designer_aisc360 import run

    return run()


def _wind():
    from design.wind_calculator_directional_asce7 import run

    return run()


def _doctor():
    from utilities.doctor import run_doctor

    return run_doctor(VERSION)


COMMANDS = {
    "setup": (_setup, "define materials, sections, loads, spectrum, cases and combinations"),
    "grids": (_grids, "build or update stories, grids, columns and walls from a DXF"),
    "tag": (_tag, "give every beam and column of the open model its unique name"),
    "check": (_check, "check the open model for missing or inconsistent inputs (read only)"),
    "analyze": (_analyze, "run, scale the response spectrum, check periods, mass and weight"),
    "drift": (_drift, "story drift with the drift stiffness (strength, service), then restore"),
    "beams": (_beams, "extract the forces and design the beams (ACI 318M-14)"),
    "deflection": (_deflection, "check only the beam deflections (bars of the last beam design)"),
    "columns": (_columns, "design the columns (with slenderness) from the stored beam step"),
    "design": (_design, "analysis and design loop that resizes beams and columns"),
    "plans": (_plans, "framing plans of every floor as one DXF (beams, columns, marks, grids)"),
    "composite": (_composite, "check a rectangular filled composite column (AISC DG6)"),
    "steel": (_steel, "check a wide-flange steel member (AISC 360-22)"),
    "wind": (_wind, "MWFRS wind pressures by the ASCE 7 directional procedure"),
    "doctor": (_doctor, "check that this machine can run the toolkit (ETABS, dialogs, LaTeX)"),
}


COMMAND_NAMES = {"sdt": "sdt", "xs": "xs"}  # installed scripts (pyproject.toml)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=COMMAND_NAMES.get(Path(sys.argv[0]).stem, "python main.py"),
        description="ETABS workflows and design checks. Each one asks for its inputs in dialogs.",
    )
    parser.add_argument("--version", action="version",
                        version=f"Structural Design Toolkit {VERSION}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    for name, (_, text) in COMMANDS.items():
        commands.add_parser(name, help=text, description=text)
    return parser


def run_and_restore(name: str):
    """Run a command, then put back the units of the ETABS model it worked on
    (the commands work in N-mm, whatever units the model is in)."""
    try:
        return COMMANDS[name][0]()
    finally:
        try:
            from etabs_api.core.connection import restore_units

            restore_units()
        except Exception:  # noqa: BLE001 - never hide the command's own error
            pass


def run_command(name: str) -> bool:
    """Run one command for the menu. An error is printed, not raised, so the
    window stays open and the menu comes back. True when it ran to its end."""
    import traceback

    try:
        run_and_restore(name)
    except KeyboardInterrupt:
        print(f"\n{name} was stopped (Ctrl+C).")
        return False
    except Exception:  # noqa: BLE001 - whatever a command raises is shown to the user
        traceback.print_exc()
        print(f"\n{name} stopped with the error above. Run 'doctor' to check this machine.")
        return False
    return True


def menu_choice(typed: str) -> str | None:
    """The command for what was typed at the menu: its number or its name
    (or the start of its name, when only one command starts that way)."""
    typed = typed.strip().lower()
    names = list(COMMANDS)
    if typed.isdigit():
        return names[int(typed) - 1] if 1 <= int(typed) <= len(names) else None
    if typed in COMMANDS:
        return typed
    starts = [name for name in names if typed and name.startswith(typed)]
    return starts[0] if len(starts) == 1 else None


def menu(read=input) -> int:
    """List the commands and run the ones typed until the user quits."""
    print(banner())
    while True:
        print("\nCommands:\n")
        for number, (name, (_, text)) in enumerate(COMMANDS.items(), start=1):
            print(f"  {number:>2}  {name:<11} {text}")
        try:
            typed = read("\nType a number or a name (q to quit): ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if typed.strip().lower() in ("q", "quit", "exit"):
            return 0
        name = menu_choice(typed)
        if name is None:
            print(f"'{typed.strip()}' is not one of the commands.")
            continue
        print(f"\n--- {name} ---")
        run_command(name)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == [LOADING_WINDOW_FLAG]:
        from utilities._gui_helpers import run_loading_window

        run_loading_window(argv[1] if len(argv) > 1 else "")
        return 0
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        if sys.stdin is not None and sys.stdin.isatty():
            return menu()  # typed alone in a terminal, or the program double-clicked
        parser.print_help()
        return 1
    run_and_restore(arguments.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
