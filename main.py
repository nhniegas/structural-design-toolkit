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
    sdt composite   rectangular filled composite column, AISC DG6
                    (design/composite_column_designer_aiscDG06.py)
    sdt steel       wide-flange member, AISC 360-22
                    (design/general_steel_section_designer_aisc360.py)
    sdt wind        MWFRS wind pressures, ASCE 7 directional procedure
                    (design/wind_calculator_directional_asce7.py)
    sdt --help      list the commands

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


def _composite():
    from design.composite_column_designer_aiscDG06 import run

    return run()


def _steel():
    from design.general_steel_section_designer_aisc360 import run

    return run()


def _wind():
    from design.wind_calculator_directional_asce7 import run

    return run()


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
    "composite": (_composite, "check a rectangular filled composite column (AISC DG6)"),
    "steel": (_steel, "check a wide-flange steel member (AISC 360-22)"),
    "wind": (_wind, "MWFRS wind pressures by the ASCE 7 directional procedure"),
}


COMMAND_NAMES = {"sdt": "sdt", "xs": "xs"}  # installed scripts (pyproject.toml)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=COMMAND_NAMES.get(Path(sys.argv[0]).stem, "python main.py"),
        description="ETABS workflows and design checks. Each one asks for its inputs in dialogs.",
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    for name, (_, text) in COMMANDS.items():
        commands.add_parser(name, help=text, description=text)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 1
    COMMANDS[arguments.command][0]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
