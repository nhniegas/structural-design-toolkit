"""
tests/test_concrete_workflow.py
===============================
Checks for design/concrete_workflow.py (the store between the beam and
column steps) and the column results file. No ETABS is needed.
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design import column_designer_aci318 as col  # noqa: E402
from design.concrete_workflow import (  # noqa: E402
    DesignStore,
    _gravity_options,
    deflection_table,
    numeric_like,
    write_deflection_xlsx,
)


def test_number_like_text_becomes_numbers_but_names_stay_text():
    table = pd.DataFrame({"UniqueName": ["17", "2GX-1"], "Station": ["0", "500.5"],
                          "Cover": ["None", "40"], "Note": ["a", "1"]})
    out = numeric_like(table)
    assert out["UniqueName"].tolist() == ["17", "2GX-1"]
    assert out["Station"].tolist() == [0.0, 500.5]
    assert pd.isna(out["Cover"][0]) and out["Cover"][1] == 40.0
    assert out["Note"].tolist() == ["a", "1"]


def test_the_store_is_saved_next_to_the_model_and_found_again(tmp_path):
    model = tmp_path / "TOWER.EDB"
    model.write_bytes(b"model")
    store = DesignStore(str(model), os.path.getmtime(model),
                        {"FRAME DATA": pd.DataFrame({"A": [1]})}, {"smrf": True})
    store.beam_results = pd.DataFrame({"UniqueName": ["B1"]})
    path = store.save()
    assert path == str(tmp_path / "TOWER - design data.pkl")
    again = DesignStore.load(str(model))
    assert again.inputs == {"smrf": True}
    assert again.beam_results["UniqueName"].tolist() == ["B1"]
    assert not again.is_stale()
    later = time.time() + 10
    os.utime(model, (later, later))
    assert again.is_stale()
    assert DesignStore.load(str(tmp_path / "OTHER.EDB")) is None


def test_gravity_combinations_leave_out_seismic_and_wind():
    combos = ["ULS 100 1.4 DL", "ULS 105 1.2 DL + f LL + 0.5 Lr + 1.0 WX",
              "ULS 107 (1.2 + Ev) DL + f LL + 1.0 RSA1", "ULS 101 1.2 DL + 1.6 LL + 0.5 Lr"]
    assert _gravity_options(combos) == ["ULS 100 1.4 DL", "ULS 101 1.2 DL + 1.6 LL + 0.5 Lr"]


def test_column_results_are_saved_with_group_headers(tmp_path):
    from openpyxl import load_workbook

    report = pd.DataFrame({
        "Column_Label": ["C1", "C1"], "UniqueName": ["2-C1", "2-C1"], "Story": ["2F", "2F"],
        "End": ["I", "J"], "Combo": ["ULS 100", "ULS 100"],
        "Flexure_Check": ["PASS", "FAIL"], "Column_Design_Status": ["PASS", "FAIL"],
    })
    groups = [("COLUMN LABEL / LEVEL", ["Column_Label", "UniqueName", "Story", "End", "Combo"]),
              ("FLEXURE / AXIAL", ["Flexure_Check"]),
              ("DESIGN STATUS", ["Column_Design_Status"])]
    path = col.write_column_results_xlsx(report, groups, str(tmp_path / "columns.xlsx"))
    sheet = load_workbook(path).active
    assert sheet["A2"].value == "COLUMN LABEL / LEVEL"
    assert sheet["B3"].value == col.COLUMN_REPORT_LABELS.get("UniqueName", "UniqueName")
    assert sheet["D4"].value == "Bottom (I)" and sheet["D5"].value == "Top (J)"
    assert sheet["F5"].font.bold  # FAIL in bold red


def test_the_deflection_table_has_one_row_per_beam(tmp_path):
    results = pd.DataFrame({
        "Story": ["2F", "2F"], "UniqueName": ["2GX-1", "2GX-1"], "Face": ["TOP", "BOTTOM"],
        "Width": [300, 300], "Depth": [500, 500], "Defl_live_mm": [12.0, 12.0],
        "Defl_ratio": [1.2, 1.2], "Deflection_Check": ["FAIL", "FAIL"],
    })
    table = deflection_table(results)
    assert len(table) == 1
    assert {"Δ live (mm)", "Deflection check"} <= set(table.columns)
    from openpyxl import load_workbook

    path = write_deflection_xlsx(table, str(tmp_path / "d.xlsx"))
    sheet = load_workbook(path).active
    assert sheet.cell(row=2, column=table.columns.get_loc("Deflection check") + 1).font.bold
