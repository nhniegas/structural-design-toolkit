"""
tests/test_project_report.py
============================
The full project report (etabs_api/workflows/project_report.py): what is read
from the text file of a model, which combinations are listed, the lines of the
beam and column tables, and the LaTeX of the report. No ETABS and no LaTeX is
needed.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import project_report as pr  # noqa: E402

MODEL_TEXT = '''$ File C:\\models\\t.$et
$ STORIES - IN SEQUENCE FROM TOP
  STORY "RD"  HEIGHT 3500
  STORY "2F"  HEIGHT 4500
  STORY "Base"  ELEV -1000

$ LOAD PATTERNS
  LOADPATTERN "SELFWEIGHT"  TYPE  "Dead"  SELFWEIGHT  1
  LOADPATTERN "LIVERED"  TYPE  "Reducible Live"  SELFWEIGHT  0
  LOADPATTERN "EQXPE"  TYPE  "Seismic"  SELFWEIGHT  0
  LOADPATTERN "WX"  TYPE  "Wind"  SELFWEIGHT  0
  SEISMIC "EQXPE"  "UBC 97"    DIR "X+ECC"  ECC 0.05  TOPSTORY "RD"    BOTTOMSTORY "2F"   PERIODTYPE "PROGCALC"   CT 0.03  Ca 0.44  Cv 0.768  SOURCETYPE "B"    SOURCEDIST 15  I 1  R 8.5
  WIND "WX"  "ASCE 7-10"  EXPOSUREFROM "DIAPHRAGMS"  TOPSTORY "RD"  BOTTOMSTORY "2F"  SPEED 150  EXPOSURE "B"    KZT 1  GUSTFACTOR 0.85  KD 0.85

$ SHELL OBJECT LOADS
  AREALOAD  "F1"  "2F"  TYPE "UNIFF"  DIR "GRAV"  LC "LIVERED"  FVAL 0.0024
  AREALOAD  "F2"  "2F"  TYPE "UNIFF"  DIR "GRAV"  LC "LIVERED"  FVAL 0.0048

$ ANALYSIS OPTIONS
  PDELTA  METHOD "ITERATIVE"  TOL 0.0001
  PDELTA  LOAD "SELFWEIGHT"  FACTOR 1
  PDELTA  LOAD "LIVERED"  FACTOR 0.5

$ MASS SOURCE
  MASSSOURCE  "MsSrc1"    INCLUDEELEMENTS "No"
  MASSSOURCELOAD  "MsSrc1"  "SELFWEIGHT"  1

$ LOAD CASES
  LOADCASE "Modal"  TYPE  "Modal - Eigen"  INITCOND  "PRESET"
  LOADCASE "SELFWEIGHT"  TYPE  "Linear Static"  INITCOND  "PRESET"
  LOADCASE "SELFWEIGHT"  LOADPAT  "SELFWEIGHT"  SF  1
  LOADCASE "RSAX"  TYPE  "Response Spectrum"  MODALCASE  "Modal"
  LOADCASE "RSAX"  MODALDAMPTYPE  "Constant"  CONSTDAMP  0.05

$ LOAD COMBINATIONS
  COMBO "EQ_COMBO_01"  TYPE "Linear Add"
  COMBO "EQ_COMBO_01"  LOADCASE "EQXPE"  SF 1
  COMBO "ULS 100 1.4 DL"  DESIGN "Concrete"  COMBOTYPE "Strength"  TYPE "Linear Add"
  COMBO "ULS 100 1.4 DL"  LOADCASE "SELFWEIGHT"  SF 1.4
  COMBO "ULS 107 EQ"  TYPE "Linear Add"
  COMBO "ULS 107 EQ"  LOADCASE "SELFWEIGHT"  SF 1.42
  COMBO "ULS 107 EQ"  LOADCOMBO "EQ_COMBO_01"  SF -1
  COMBO "WDRIFT 100"  TYPE "Linear Add"
  COMBO "WDRIFT 100"  LOADCASE "WX"  SF 0.5
  COMBO "DRIFT 100"  TYPE "Linear Add"
  COMBO "DRIFT 100"  LOADCASE "EQXPE"  SF 1
  COMBO "ENVE_ULS"  TYPE "Envelope"
  COMBO "ENVE_ULS"  LOADCOMBO "ULS 100 1.4 DL"  SF 1
  COMBO "MY OWN"  TYPE "Linear Add"
  COMBO "MY OWN"  LOADCASE "WX"  SF 1

$ END OF MODEL FILE
'''


def test_the_stories_get_their_elevations_from_the_base_up():
    text = pr.parse_model_text(MODEL_TEXT)
    assert text.stories == [("RD", 3500.0, 7000.0), ("2F", 4500.0, 3500.0), ("Base", None, -1000.0)]
    assert text.elevations["RD"] == 7000.0


def test_the_patterns_and_their_seismic_and_wind_values_are_read():
    text = pr.parse_model_text(MODEL_TEXT)
    assert text.patterns[0] == ("SELFWEIGHT", "Dead", "1") and len(text.patterns) == 4
    seismic = text.seismic[0]
    # R and I are read as themselves: "DIR" also ends in R, "CT" is not "T"
    assert (seismic["pattern"], seismic["r"], seismic["i"], seismic["ca"], seismic["cv"],
            seismic["ct"], seismic["direction"], seismic["code"]) == (
        "EQXPE", "8.5", "1", "0.44", "0.768", "0.03", "X+ECC", "UBC 97")
    wind = text.wind[0]
    assert (wind["speed"], wind["exposure"], wind["kd"], wind["code"]) == (
        "150", "B", "0.85", "ASCE 7-10")


def test_a_case_has_its_own_type_not_that_of_its_damping():
    cases = pr.parse_model_text(MODEL_TEXT).cases
    assert cases == {"Modal": "Modal - Eigen", "SELFWEIGHT": "Linear Static",
                     "RSAX": "Response Spectrum"}


def test_the_floor_loads_are_in_kpa_the_largest_of_a_story_and_pattern():
    text = pr.parse_model_text(MODEL_TEXT)
    assert text.area_loads == [("2F", "LIVERED", 4.8)]
    assert text.mass == [("SELFWEIGHT", "1")]
    assert text.pdelta == [("SELFWEIGHT", "1"), ("LIVERED", "0.5")]


def test_the_combinations_are_read_with_their_terms_and_grouped():
    text = pr.parse_model_text(MODEL_TEXT)
    assert text.combos["ULS 100 1.4 DL"] == {"type": "Linear Add", "terms": [(1.4, "SELFWEIGHT")]}
    assert pr.combination_terms(text.combos["ULS 107 EQ"]) == "1.42 SELFWEIGHT - 1 EQ_COMBO_01"
    groups = pr.listed_combinations(text.combos)
    assert groups["Strength (ULS)"] == ["ULS 100 1.4 DL", "ULS 107 EQ"]
    # WDRIFT is not DRIFT, and an envelope is not listed
    assert groups["Wind drift"] == ["WDRIFT 100"] and groups["Seismic drift"] == ["DRIFT 100"]
    assert groups[pr.OTHER_GROUP] == ["MY OWN"]
    assert not any("ENVE_ULS" in names for names in groups.values())


def test_a_model_file_with_a_part_missing_gives_what_it_has():
    text = pr.parse_model_text("$ LOAD PATTERNS\n  LOADPATTERN \"D\"  TYPE  \"Dead\"  SELFWEIGHT  1\n")
    assert text.patterns == [("D", "Dead", "1")] and text.stories == [] and text.combos == {}


def columns_report() -> pd.DataFrame:
    base = {"Column_Label": "C1", "Story": "2F", "Shape": "Rectangular", "Width_mm": 500.0,
            "Depth_mm": 600.0, "Diameter_mm": float("nan"), "Longitudinal_Bars": 12,
            "Tie_Bar_mm": 10.0, "Transverse_Spacing_mm": 100.0, "Transverse_Type": "Hoops",
            "Reinforcement_Ratio": 0.02, "Flexure_Utilization": 0.5, "Shear_Utilization": 0.3,
            "Joint_Shear_Utilization_X": "N/A", "Joint_Shear_Utilization_Y": 0.62,
            "Column_Design_Status": "PASS"}
    return pd.DataFrame([
        dict(base, UniqueName="2-C1"), dict(base, UniqueName="2-C1", Flexure_Utilization=0.81),
        dict(base, UniqueName="2-C2", Shape="Circular", Width_mm=float("nan"),
             Depth_mm=float("nan"), Diameter_mm=600.0, Transverse_Type="Spiral",
             Transverse_Spacing_mm=75.0, Column_Design_Status="FAIL: column shear strength")])


def test_a_column_line_has_its_largest_ratios_and_a_round_column_its_diameter():
    rows, status = pr.column_results(columns_report())
    first, second = rows
    assert first[:4] == ["C1", "2-C1", "2F", "500 x 600"] and first[7:] == [
        "0.81", "0.30", "0.62", "PASS"]
    assert second[3] == "600 dia." and second[5] == "10 @ 75 spiral" and second[-1] == "FAIL"
    assert status == {"2-C1": "PASS", "2-C2": "FAIL"}


def test_a_beam_line_counts_its_bars_over_the_layers():
    row = {"UniqueName": "2GX-1", "Story": "2F", "Width": 300.0, "Depth": 500.0, "ds": 10.0,
           "Stirrup_Legs": 2, "Spacing_2H": 100, "Spacing_Mid": 150, "Defl_ratio": 0.748,
           "Design_Status": "OK"}
    layers = {f"n_{zone}_L{k}": 0 for zone in ("left", "mid", "right") for k in (1, 2, 3)}
    beams = pd.DataFrame([
        {**row, **layers, "Face": "TOP", "n_left_L1": 3, "n_left_L2": 2, "n_mid_L1": 2,
         "n_right_L1": 3},
        {**row, **layers, "Face": "BOTTOM", "n_left_L3": 2, "n_mid_L3": 3, "n_right_L3": 2}])
    assert pr.beam_results(beams) == [["2GX-1", "2F", "300 x 500", "5/2/3", "2/3/2",
                                      "2-10 @ 100/150", "0.75", "PASS"]]


def test_the_report_has_its_three_parts_and_leaves_out_what_it_was_not_given():
    data = pr.ReportData("tower", pr.ProjectInfo("Office tower", "Makati", "", "N. H."),
                         text=pr.parse_model_text(MODEL_TEXT),
                         findings=[{"group": "Seismic", "status": "OK",
                                    "text": "EQXPE: V/W 0.1294 at T 0.594 s", "ref": "NSCP 208.5.2.1"},
                                   {"group": "Model", "status": "WARN", "text": "3 beams untagged",
                                    "ref": ""}],
                         columns=columns_report(), files=[("tower - Drift.txt", "The drift")])
    body = pr.report_body(data)
    for expected in ("Structural Analysis and Design Report", "Office tower", "Makati",
                     "PART A", "PART B", r"A7\quad Load combinations", "ULS 107 EQ",
                     "1.42 SELFWEIGHT - 1 EQ\\_COMBO\\_01", r"\sdtwarn{CHECK}", "600 dia.",
                     "No drift check was made.", "The irregularity check was not made.",
                     "The concrete quantities were not measured.", "tower - Drift.txt"):
        assert expected in body, expected
    assert "Client" not in body          # an empty line of the front page is left out
    assert "ENVE\\_ULS" not in body and "PART C" not in body.split("Contents")[1].split("Result")[1]


def test_results_stored_as_plain_copies_are_read_like_the_objects():
    class Thing:
        status = "OK"

    assert pr._get(Thing(), "status") == "OK" and pr._get({"status": "FAIL"}, "status") == "FAIL"
    assert pr._get({}, "missing", 3) == 3
