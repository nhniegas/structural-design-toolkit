"""
tests/test_output_folders.py
============================
The subfolders of an output folder (utilities/output_folders.py): each kind of
file goes to its own folder, made when first needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utilities import output_folders as of  # noqa: E402


def test_each_kind_of_file_goes_to_its_folder(tmp_path):
    folder = str(tmp_path)
    expected = {
        "M - Beam Design.xlsx": "Results",
        "M - Beam Calculations.pdf": "Calculations",
        "M - Column_Schedule.dxf": "Schedules",
        "M - 3D Model.step": "Drawings",
        "M - Drift.txt": "Summaries",
        "M - DESIGN log.txt": "Summaries",
    }
    for name, sub in expected.items():
        path = Path(of.output_path(folder, name))
        assert path == tmp_path / sub / name
        assert path.parent.is_dir()


def test_a_plan_is_a_drawing_and_design_data_stays_at_the_top(tmp_path):
    folder = str(tmp_path)
    assert Path(of.output_path(folder, "M - Framing Plans.dxf", of.DRAWINGS)) == \
        tmp_path / "Drawings" / "M - Framing Plans.dxf"
    assert Path(of.output_path(folder, "M - design data.pkl")) == tmp_path / "M - design data.pkl"


def test_files_beside_the_model_go_to_subfolders_of_its_folder(tmp_path):
    model = str(tmp_path / "BOTTOM BUILDING.EDB")
    assert Path(of.beside(model, " - Irregularity.txt")) == \
        tmp_path / "Summaries" / "BOTTOM BUILDING - Irregularity.txt"
    assert Path(of.beside(model, " - Reactions.xlsx")) == \
        tmp_path / "Results" / "BOTTOM BUILDING - Reactions.xlsx"


def test_subfolder_is_made_for_writers_that_name_their_files(tmp_path):
    target = Path(of.subfolder(str(tmp_path), of.SCHEDULES))
    assert target == tmp_path / "Schedules" and target.is_dir()

