"""Offline tests for the ETABS service wrappers using fake COM interfaces.

The fakes return values in the layout ETABS really uses through comtypes
(checked against ETABS 22): output arguments first, status code LAST, e.g.
``GetNameList`` gives ``(count, names, 0)``.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

from etabs_api.analysis import Analysis
from etabs_api.connection import ETABSConnector
from etabs_api.database import DatabaseTables
from etabs_api.exporter import ETABSDataExporter
from etabs_api.geometry import Geometry
from etabs_api.helpers import ensure_success, return_code
from etabs_api.loads import Loads
from etabs_api.results import Results
from etabs_api.selection import Selection
from etabs_api.stories_grids import StoriesGrids


class FakeInterface:
    """Small recording fake for the ETABS COM methods used by the tests."""

    def __init__(self):
        self.calls = []

    def GetAllTables(self, *_):
        return (2, ("k1", "k2"), ("Table 1", "Table 2"), (0, 2), (False, False), 0)

    def GetTableForDisplayArray(self, *_):
        return ((), 1, ("A", "B"), 2, ("1", "2", "3", "4"), 0)

    def SetLoadCombinationsSelectedForDisplay(self, combinations):
        self.calls.append(("SetLoadCombinationsSelectedForDisplay", combinations))
        return 0

    def SetLoadCasesSelectedForDisplay(self, cases):
        return 0

    def SetLoadPatternsSelectedForDisplay(self, patterns):
        return 0

    def RunAnalysis(self):
        self.calls.append(("RunAnalysis",))
        return 0

    def GetCaseStatus(self, *_):
        return (2, ("Case 1", "Case 2"), (4, 1), 0)

    def SetActiveDOF(self, dof):
        self.calls.append(("SetActiveDOF", dof))
        return 0

    def SetSelected(self, name, value):
        self.calls.append(("SetSelected", name, value))
        return 0

    def GetSelected(self, *_):
        return (2, (2, 1), ("F1", "P1"), 0)

    def ClearSelection(self):
        return 0

    def Add(self, *args):
        self.calls.append(("Add", args))
        return 0

    def SetCaseList(self, *args):
        self.calls.append(("SetCaseList", args))
        return 0

    def SetLoadForce(self, *args):
        self.calls.append(("SetLoadForce", args))
        return 0

    def SetLoadDistributed(self, *args):
        self.calls.append(("SetLoadDistributed", args))
        return 0

    def GetNameList(self, *_):
        return (1, ("P1",), 0)

    def SetSection(self, *args):
        return 0

    def GetStories(self, *_):
        return (
            2,
            ("BASE", "GF"),
            (0.0, 4500.0),
            (0.0, 4500.0),
            (False, True),
            (None, None),
            (False, False),
            (0.0, 0.0),
            0,
        )

    def BaseReact(self, *_):
        return (
            1, ("DEAD",), ("Single Value",), (0.0,),
            (1.0,), (2.0,), (3.0,), (4.0,), (5.0,), (6.0,),
            0.0, 0.0, 0.0, 0,
        )


def fake_connector():
    """Build a connector-shaped fake model tree."""
    model = SimpleNamespace(
        DatabaseTables=FakeInterface(),
        Analyze=FakeInterface(),
        SelectObj=FakeInterface(),
        LoadPatterns=FakeInterface(),
        RespCombo=FakeInterface(),
        PointObj=FakeInterface(),
        FrameObj=FakeInterface(),
        AreaObj=FakeInterface(),
        LinkObj=FakeInterface(),
        PropMaterial=FakeInterface(),
        PropFrame=FakeInterface(),
        PropArea=FakeInterface(),
        PropRebar=FakeInterface(),
        Story=FakeInterface(),
        GridSys=FakeInterface(),
        Results=FakeInterface(),
    )
    return SimpleNamespace(sap_model=model, ensure_connected=lambda: None)


def real_connector(database: FakeInterface) -> ETABSConnector:
    """An ``ETABSConnector`` wired to a fake database interface (no ETABS needed)."""
    api = ETABSConnector()
    api.sap_model = SimpleNamespace(DatabaseTables=database)
    api.is_connected = True
    return api


# --------------------------------------------------------------------------
# RETURN CODES
# --------------------------------------------------------------------------
def test_status_code_is_the_last_item_of_an_etabs_result():
    """ETABS returns its outputs first and the status code last."""
    assert return_code((3, ("a", "b", "c"), 0)) == 0
    assert return_code((0, (), 1)) == 1
    assert return_code(0) == 0


def test_a_nonzero_status_code_raises_with_the_operation_name():
    with pytest.raises(RuntimeError, match="FrameObj.GetNameList"):
        ensure_success((0, (), 1), "FrameObj.GetNameList")


# --------------------------------------------------------------------------
# DATABASE TABLES AND DESIGN FORCES
# --------------------------------------------------------------------------
def test_database_table_read_returns_dataframe():
    """Database tables are converted from flattened COM values."""
    table = DatabaseTables(fake_connector()).get_table("k1")
    assert isinstance(table, pd.DataFrame)
    assert table.to_dict("records") == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_available_tables_pairs_keys_with_names():
    tables = DatabaseTables(fake_connector()).available_tables()
    assert tables == [{"key": "k1", "name": "Table 1"}, {"key": "k2", "name": "Table 2"}]


def test_design_force_read_selects_one_combination_and_normalizes_member_name():
    """Design-force reads are combination-scoped and expose UniqueName."""
    database = FakeInterface()
    database.GetTableForDisplayArray = lambda *_: (
        (), 1, ("Beam", "Station", "M3"), 1, ("B1", "0.0", "12.5"), 0,
    )
    result = real_connector(database).get_design_forces("Design Forces - Beams", "ULS 1")

    assert result.to_dict("records") == [
        {"Combo": "ULS 1", "UniqueName": "B1", "Station": "0.0", "M3": "12.5"}
    ]
    assert database.calls == [("SetLoadCombinationsSelectedForDisplay", ["ULS 1"])]


def test_a_failed_table_read_raises_instead_of_returning_an_error_object():
    database = FakeInterface()
    database.GetTableForDisplayArray = lambda *_: ((), 0, (), 0, (), 1)
    with pytest.raises(RuntimeError, match="Material Properties"):
        real_connector(database).get_data("Material Properties - Concrete Data")


# --------------------------------------------------------------------------
# EXPORTER: what is read per combination and what is read once
# --------------------------------------------------------------------------
class RecordingEtabs:
    """Stands in for ``ETABSConnector`` and records every table request."""

    FORCES = {
        "Design Forces - Beams": ("Beam", "B1", "GX-1"),
        "Design Forces - Columns": ("Column", "C1", "2F-C1"),
    }

    def __init__(self):
        self.force_reads = []
        self.table_reads = []

    def get_design_forces(self, table_name, combo):
        self.force_reads.append((table_name, combo))
        label_column, label, unique_name = self.FORCES[table_name]
        rows = [
            {
                "Story": "2F", label_column: label, "UniqueName": unique_name,
                "Combo": f"{combo}-{permutation}", "Station": "0",
                "P": "1000", "V2": "2000", "V3": "0", "T": "0",
                "M2": "0", "M3": "3000000",
            }
            for permutation in (1, 2)
        ]
        return pd.DataFrame(rows)

    def get_data(self, table_name, combos=None):
        self.table_reads.append((table_name, combos))
        tables = {
            "Frame Assignments - Section Properties": pd.DataFrame(
                {"Story": ["2F"], "UniqueName": ["GX-1"], "SectProp": ["B300x500"]}
            ),
            "Frame Section Property Definitions - Concrete Rectangular": pd.DataFrame(
                {"Name": ["B300x500"], "Material": ["C28"], "t2": ["300"], "t3": ["500"],
                 "DesignType": ["Beam"]}
            ),
            "Frame Section Property Definitions - Concrete Circle": pd.DataFrame(
                columns=["Name", "Material", "t3", "DesignType"]
            ),
            "Frame Section Property Definitions - Concrete Beam Reinforcing": pd.DataFrame(
                {"Name": ["B300x500"], "RebarMatL": ["G60"], "RebarMatC": ["G40"]}
            ),
            "Frame Section Property Definitions - Concrete Column Reinforcing": pd.DataFrame(
                columns=["Name", "RebarMatL", "RebarMatC"]
            ),
            "Material Properties - Concrete Data": pd.DataFrame(
                {"Material": ["C28"], "Fc": ["28"]}
            ),
            "Material Properties - Rebar Data": pd.DataFrame(
                {"Material": ["G60", "G40"], "Fy": ["414", "276"]}
            ),
        }
        return tables[table_name]


@pytest.fixture
def exporter(monkeypatch):
    """An exporter whose Excel writes are captured instead of sent to Excel."""
    written = {}
    monkeypatch.setattr(
        ETABSDataExporter,
        "_write_dataframe_to_excel",
        classmethod(lambda cls, df, sheet_name, **_: written.__setitem__(sheet_name, df)),
    )
    instance = ETABSDataExporter(RecordingEtabs())
    instance.written = written
    return instance


def test_frame_data_tables_are_read_once_not_once_per_combination(exporter):
    """BEHAVIOUR: section and material tables do not depend on load combinations."""
    frame_data = exporter.display_frame_data(
        members_selected=["GX-1"], load_combos_selected=["ULS 1", "ULS 2", "ULS 3"]
    )
    assert len(exporter.etabs.table_reads) == 7
    assert all(combos is None for _, combos in exporter.etabs.table_reads)
    assert len(frame_data) == 1  # one row per member, no duplicates
    assert frame_data.loc[0, "f'c"] == 28.0
    assert frame_data.loc[0, "fy"] == 414.0


def test_design_forces_are_read_one_combination_at_a_time(exporter):
    exporter.display_factored_loads(load_combos_selected=["ULS 1", "ULS 2"])
    assert exporter.etabs.force_reads == [
        ("Design Forces - Beams", "ULS 1"),
        ("Design Forces - Beams", "ULS 2"),
        ("Design Forces - Columns", "ULS 1"),
        ("Design Forces - Columns", "ULS 2"),
    ]


def test_each_combination_is_read_from_etabs_only_once_per_extraction(exporter):
    """The member list and the force table share the same ETABS reads."""
    members = exporter.get_available_members(["ULS 1", "ULS 2"])
    exporter.display_factored_loads(load_combos_selected=["ULS 1", "ULS 2"])
    assert members == ["GX-1", "2F-C1"]
    assert len(exporter.etabs.force_reads) == 4


def test_factored_loads_split_the_combo_name_from_its_permutation(exporter):
    """ETABS names rows 'ULS 1-1', 'ULS 1-2': plain name plus a Permutation column."""
    forces = exporter.display_factored_loads(load_combos_selected=["ULS 1"])
    assert forces["Combo"].unique().tolist() == ["ULS 1"]
    assert sorted(forces["Permutation"].unique()) == [1, 2]
    assert list(forces.columns[:5]) == ["Story", "Label", "UniqueName", "Combo", "Permutation"]
    assert sorted(forces["Label"].unique()) == ["B1", "C1"]  # beams and columns keep labels
    # N -> kN and N-mm -> kN-m
    assert forces["P"].iloc[0] == 1.0
    assert forces["M3"].iloc[0] == 3.0


# --------------------------------------------------------------------------
# SERVICE WRAPPERS
# --------------------------------------------------------------------------
def test_analysis_selection_and_geometry_services():
    """Analysis, selection, and geometry wrappers read the right result items."""
    connector = fake_connector()
    analysis = Analysis(connector)
    assert analysis.status() == [
        {"case": "Case 1", "status_code": 4, "status": "Finished"},
        {"case": "Case 2", "status_code": 1, "status": "Not Run"},
    ]
    assert analysis.set_active_dof()[0] is True
    assert Selection(connector).get_selected() == [
        {"type": "Frame", "name": "F1"},
        {"type": "Point", "name": "P1"},
    ]
    assert Geometry(connector).all_points() == ["P1"]


def test_selection_uses_the_selectobj_interface():
    """ETABS exposes selection as SapModel.SelectObj (there is no SapModel.Select)."""
    connector = fake_connector()
    assert Selection(connector).interface is connector.sap_model.SelectObj


def test_stories_and_grids_are_read_from_the_right_items():
    connector = fake_connector()
    service = StoriesGrids(connector)
    assert service.stories() == [
        {"name": "BASE", "elevation": 0.0, "height": 0.0},
        {"name": "GF", "elevation": 4500.0, "height": 4500.0},
    ]
    assert service.grids() == ["P1"]


def test_results_are_returned_as_a_labelled_dataframe():
    connector = fake_connector()
    reactions = Results(connector).base_reactions()
    assert reactions.to_dict("records") == [
        {"OutputCase": "DEAD", "StepType": "Single Value", "StepNum": 0.0,
         "FX": 1.0, "FY": 2.0, "FZ": 3.0, "MX": 4.0, "MY": 5.0, "MZ": 6.0}
    ]


def test_load_service_defines_and_assigns():
    """Load helpers validate return codes and preserve caller values."""
    connector = fake_connector()
    loads = Loads(connector)
    assert loads.get_patterns() == ["P1"]
    assert loads.define_pattern("DEAD", 1, 1.0) == "DEAD"
    assert loads.assign_point_load("P1", "DEAD", [1, 2, 3, 4, 5, 6]) == "P1"
