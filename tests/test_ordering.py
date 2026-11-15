"""The order of marks and names in the results, schedules and reports (v0.16.1)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design.concrete.beams.results import beam_sort_key, sort_beam_rows  # noqa: E402
from utilities.ordering import (  # noqa: E402
    column_member_order,
    natural_key,
    natural_rank,
    story_elevations,
)


def test_column_marks_are_read_by_number():
    marks = ["C10", "C2", "C1A", "PC1", "C1", "C11", "C2A", "PC10", "PC2", "C20"]
    assert sorted(marks, key=natural_key) == [
        "C1", "C1A", "C2", "C2A", "C10", "C11", "C20", "PC1", "PC2", "PC10"]


def test_position_letters_go_by_length_first():
    """sdt tag gives A ... Z, then AA: C1Z comes before C1AA."""
    assert sorted(["C1AA", "C1B", "C1Z", "C1"], key=natural_key) == ["C1", "C1B", "C1Z", "C1AA"]


def test_untagged_etabs_names_are_read_by_number():
    assert sorted(["100", "10", "2", "1", "1001"], key=natural_key) == ["1", "2", "10", "100", "1001"]


def test_case_and_spaces_do_not_change_the_order():
    assert natural_key(" c2 ") == natural_key("C2")


def test_natural_rank_sorts_like_the_key_with_blanks_last():
    values = pd.Series(["C10", None, "C2", "C1", "C2"])
    assert natural_rank(values).tolist() == [2, 3, 1, 0, 1]


def test_column_member_order_by_mark_then_top_floor_first():
    report = pd.DataFrame({
        "UniqueName": ["2-C10", "3-C2", "2-C2", "2-C2", "3-PC1", "3-C10"],
        "Column_Label": ["C10", "C2", "C2", "C2", "PC1", "C10"],
        "Level_Elevation_m": [3.0, 6.0, 3.0, 3.0, 6.0, 6.0],
        "Story": ["2F", "3F", "2F", "2F", "3F", "3F"],
    })
    assert column_member_order(report) == ["3-C2", "2-C2", "3-C10", "2-C10", "3-PC1"]


def test_story_elevations_from_the_joints():
    points = pd.DataFrame({"Story": ["2F", "2F", "2F", "10F"], "Z": [3.0, 3.0, 2.5, 30.0]})
    assert story_elevations(points) == {"2F": 3.0, "10F": 30.0}
    assert story_elevations(pd.DataFrame({"UniqueName": ["1"]})) == {}
    assert story_elevations(None) == {}


def test_beam_numbers_and_floors_are_read_by_number():
    names = ["10GX-1", "2BX-10", "2BX-2", "2GX-1", "2BX-1A", "2BX-1"]
    assert sorted(names, key=beam_sort_key) == [
        "2BX-1", "2BX-1A", "2BX-2", "2BX-10", "2GX-1", "10GX-1"]


def test_untagged_beams_are_read_by_number():
    assert sorted(["100", "20", "3"], key=beam_sort_key) == ["3", "20", "100"]


def test_beam_rows_go_up_the_floors_by_elevation():
    """Floor names say nothing of the order (UG, RD, PD1): the elevation does."""
    rows = pd.DataFrame({
        "UniqueName": ["RDGX-1", "RDGX-1", "UGGX-2", "UGGX-2", "UGGX-10", "UGGX-10"],
        "Face": ["BOTTOM", "TOP", "TOP", "BOTTOM", "BOTTOM", "TOP"],
        "Story": ["RD", "RD", "UG", "UG", "UG", "UG"],
    })
    ordered = sort_beam_rows(rows, {"UG": 0.0, "RD": 9.0})
    assert list(zip(ordered["UniqueName"], ordered["Face"])) == [
        ("UGGX-2", "TOP"), ("UGGX-2", "BOTTOM"), ("UGGX-10", "TOP"), ("UGGX-10", "BOTTOM"),
        ("RDGX-1", "TOP"), ("RDGX-1", "BOTTOM")]


def test_beam_rows_without_elevations_go_by_the_floor_of_the_mark():
    rows = pd.DataFrame({"UniqueName": ["10GX-1", "2GX-1"], "Story": ["10F", "2F"]})
    assert sort_beam_rows(rows)["UniqueName"].tolist() == ["2GX-1", "10GX-1"]
