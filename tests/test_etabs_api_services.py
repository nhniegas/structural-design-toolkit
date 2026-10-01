"""Offline tests for the ETABS service wrappers using fake COM interfaces."""

from types import SimpleNamespace

import pandas as pd

from etabs_api.analysis import Analysis
from etabs_api.database import DatabaseTables
from etabs_api.geometry import Geometry
from etabs_api.loads import Loads
from etabs_api.selection import Selection


class FakeInterface:
    """Small recording fake for ETABS COM methods used by the tests."""

    def __init__(self):
        self.calls = []

    def GetAllTables(self, *_):
        return (0, 2, ["k1", "k2"], ["Table 1", "Table 2"])

    def GetTableForDisplayArray(self, *_):
        return (0, 0, 0, 0, ["A", "B"], 2, ["1", "2", "3", "4"])

    def RunAnalysis(self):
        self.calls.append(("RunAnalysis",))
        return 0

    def GetCaseStatus(self, *_):
        return (0, 2, ["Case 1", "Case 2"], [4, 1])

    def SetActiveDOF(self, dof):
        self.calls.append(("SetActiveDOF", dof))
        return 0

    def SetSelected(self, name, value):
        self.calls.append(("SetSelected", name, value))
        return 0

    def GetSelected(self, *_):
        return (0, 2, [2, 1], ["F1", "P1"])

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
        return (0, 1, ["P1"])

    def SetSection(self, *args):
        return 0


def fake_connector():
    """Build a connector-shaped fake model tree."""
    database = FakeInterface()
    analyze = FakeInterface()
    select = FakeInterface()
    load_patterns = FakeInterface()
    resp_combo = FakeInterface()
    point = FakeInterface()
    frame = FakeInterface()
    model = SimpleNamespace(
        DatabaseTables=database,
        Analyze=analyze,
        Select=select,
        LoadPatterns=load_patterns,
        RespCombo=resp_combo,
        PointObj=point,
        FrameObj=frame,
        AreaObj=FakeInterface(),
        LinkObj=FakeInterface(),
        PropMaterial=FakeInterface(),
        PropFrame=FakeInterface(),
        PropArea=FakeInterface(),
        PropRebar=FakeInterface(),
    )
    return SimpleNamespace(
        sap_model=model,
        ensure_connected=lambda: None,
    )


def test_database_table_read_returns_dataframe():
    """Database tables are converted from flattened COM values."""
    connector = fake_connector()
    table = DatabaseTables(connector).get_table("k1")
    assert isinstance(table, pd.DataFrame)
    assert table.to_dict("records") == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_analysis_selection_and_geometry_services():
    """Analysis, selection, and geometry wrappers call the expected COM methods."""
    connector = fake_connector()
    analysis = Analysis(connector)
    assert analysis.status()[0]["status"] == "Finished"
    assert analysis.set_active_dof()[0] is True
    assert Selection(connector).get_selected()[0] == {"type": "Frame", "name": "F1"}
    assert Geometry(connector).all_points() == ["P1"]


def test_load_service_defines_and_assigns():
    """Load helpers validate return codes and preserve caller values."""
    connector = fake_connector()
    loads = Loads(connector)
    assert loads.define_pattern("DEAD", 1, 1.0) == "DEAD"
    assert loads.assign_point_load("P1", "DEAD", [1, 2, 3, 4, 5, 6]) == "P1"
