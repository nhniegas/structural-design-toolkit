"""What the commands work out about a model that sdt setup did not build."""

import json

import pandas as pd
import pytest

from design.beam_deflection import COMBO_DEAD, COMBO_FULL, COMBO_ROOF, COMBO_SUSTAINED
from etabs_api.workflows import model_inputs as mi
from etabs_api.workflows.analysis_forces import ComboTerms

# D, SD dead; L live; Lr roof live; EX seismic; WX wind
TYPES = {"D": 1, "SD": 2, "L": 3, "Lr": 11, "EX": 5, "WX": 6}


def combinations() -> mi.Combinations:
    terms = {
        "1.4D": ComboTerms("1.4D", {"D": 1.4, "SD": 1.4}),
        "1.2D+1.6L": ComboTerms("1.2D+1.6L", {"D": 1.2, "SD": 1.2, "L": 1.6}),
        "SVC D": ComboTerms("SVC D", {"D": 1.0, "SD": 1.0}),
        "SVC D+L": ComboTerms("SVC D+L", {"D": 1.0, "SD": 1.0, "L": 1.0}),
        "SVC D+Lr": ComboTerms("SVC D+Lr", {"D": 1.0, "SD": 1.0, "Lr": 1.0}),
        "1.2D+L+EX": ComboTerms("1.2D+L+EX", {"D": 1.2, "SD": 1.2, "L": 1.0, "EX": 1.0}),
        "1.2D+L+SPECX": ComboTerms("1.2D+L+SPECX", {"D": 1.2, "L": 1.0}, {"SPECX": 1.0}),
        "0.9D+WX": ComboTerms("0.9D+WX", {"D": 0.9, "SD": 0.9, "WX": 1.0}),
    }
    return mi.Combinations(list(terms) + ["ENVELOPE"], terms, TYPES)


# ----------------------------------------------------------------- combinations
def test_an_envelope_is_listed_apart_and_cannot_be_designed_for():
    c = combinations()
    assert c.envelopes == ["ENVELOPE"]
    assert "ENVELOPE" not in c.linear
    assert c.kind("ENVELOPE") == mi.OTHER


def test_the_kind_of_a_combination_comes_from_its_load_cases():
    c = combinations()
    assert c.kind("1.4D") == mi.GRAVITY
    assert c.kind("1.2D+L+EX") == mi.SEISMIC
    assert c.kind("1.2D+L+SPECX") == mi.SEISMIC  # a response spectrum case
    assert c.kind("0.9D+WX") == mi.WIND


def test_standard_strength_names_and_the_seismic_choice():
    names = ["ULS 101", "ULS 201 EQ1", "ULS 301 RSA1", "DEF 100", "COMB1"]
    assert mi.standard_strength(names) == names[:3]
    assert mi.standard_strength(names, "EQ") == ["ULS 101", "ULS 201 EQ1"]
    assert mi.standard_strength(names, "RSA") == ["ULS 101", "ULS 301 RSA1"]
    assert mi.standard_strength(["COMB1", "COMB2"]) == []


def test_gravity_options_are_the_chosen_combinations_without_lateral_load():
    c = combinations()
    chosen = ["1.4D", "1.2D+1.6L", "1.2D+L+EX", "0.9D+WX"]
    assert mi.gravity_options(c, chosen) == ["1.4D", "1.2D+1.6L"]
    # nothing to tell apart: every chosen one is offered
    assert mi.gravity_options(c, ["1.2D+L+EX"]) == ["1.2D+L+EX"]


def test_deflection_suggestions_put_the_closest_combination_first():
    suggested = mi.suggest_deflection(combinations())
    assert suggested[COMBO_DEAD][0] == "SVC D"
    assert suggested[COMBO_FULL][0] == "SVC D+L"
    assert suggested[COMBO_ROOF][0] == "SVC D+Lr"
    # no D + 0.25 L in the model: the closest is still a gravity combination
    assert suggested[COMBO_SUSTAINED][0] in ("SVC D", "SVC D+L")
    assert "1.2D+L+EX" not in suggested[COMBO_DEAD]


def test_deflection_roles_use_standard_names_then_saved_choices():
    names = [COMBO_DEAD, "SVC D+L", "SVC D+Lr"]
    saved = {COMBO_FULL: "SVC D+L", COMBO_SUSTAINED: COMBO_SUSTAINED, COMBO_ROOF: "gone"}
    roles = mi.deflection_roles(names, saved)
    assert roles[COMBO_DEAD] == COMBO_DEAD            # in the model
    assert roles[COMBO_FULL] == "SVC D+L"             # saved and still there
    assert roles[COMBO_SUSTAINED] == COMBO_SUSTAINED  # saved as "let the toolkit add it"
    assert roles[COMBO_ROOF] is None                  # saved choice no longer exists: ask


def test_drift_cases_name_the_lateral_case_and_whether_it_is_wind():
    c = combinations()
    cases = mi.drift_cases(c, ["1.2D+L+EX", "1.2D+L+SPECX", "0.9D+WX", "1.4D"])
    assert cases == {"1.2D+L+EX": ("EX", False), "1.2D+L+SPECX": ("SPECX", False),
                     "0.9D+WX": ("WX", True)}


# ----------------------------------------------------------------- members
def test_unnamed_members_are_those_with_an_etabs_number():
    assert mi.unnamed_members(["2GX-1", "17", "3-C5", "204"]) == ["17", "204"]


@pytest.mark.parametrize("strength, per_ksi, prefix, tag", [
    (27.579, 6.894757, "C", "C04"), (20.684, 6.894757, "C", "C03"),
    (413.685, 6.894757, "G", "G60"), (275.79, 6.894757, "G", "G40"), (0, 6.894757, "C", "C00"),
])
def test_grade_tag(strength, per_ksi, prefix, tag):
    assert mi.grade_tag(prefix, strength, per_ksi) == tag


def test_beam_family_from_where_the_beam_is():
    assert mi.beam_family(frames_into_column=True, on_bottom_level=False) == "G"
    assert mi.beam_family(frames_into_column=False, on_bottom_level=False) == "B"
    assert mi.beam_family(frames_into_column=True, on_bottom_level=True) == "FTB"


def test_geometric_lines_join_beams_in_line_between_columns():
    # columns at joints 1 and 4; a girder split at 2 and 3 by secondary beams
    points = pd.DataFrame({
        "UniqueName": ["1", "2", "3", "4", "5", "6", "1b", "4b"],
        "X": [0, 2000, 4000, 6000, 2000, 4000, 0, 6000],
        "Y": [0, 0, 0, 0, 3000, 3000, 0, 0],
        "Z": [3000, 3000, 3000, 3000, 3000, 3000, 0, 0],
    })
    connectivity = pd.DataFrame({
        "UniqueName": ["11", "12", "13", "21", "22", "C1", "C2"],
        "DesignType": ["Beam"] * 5 + ["Column"] * 2,
        "UniquePtI": ["1", "2", "3", "2", "3", "1b", "4b"],
        "UniquePtJ": ["2", "3", "4", "5", "6", "1", "4"],
    })
    lines = mi.geometric_lines(connectivity, points)
    assert lines["11"] == lines["12"] == lines["13"] == "11"
    assert lines["21"] == "21" and lines["22"] == "22"  # perpendicular: lines of their own


def test_geometric_lines_stop_at_a_column():
    points = pd.DataFrame({"UniqueName": ["1", "2", "3", "2b"], "X": [0, 3000, 6000, 3000],
                           "Y": [0, 0, 0, 0], "Z": [3000, 3000, 3000, 0]})
    connectivity = pd.DataFrame({
        "UniqueName": ["11", "12", "C1"], "DesignType": ["Beam", "Beam", "Column"],
        "UniquePtI": ["1", "2", "2b"], "UniquePtJ": ["2", "3", "2"]})
    lines = mi.geometric_lines(connectivity, points)
    assert lines["11"] != lines["12"]


# ----------------------------------------------------------------- seismic values
def test_seismic_values_from_the_ubc97_table():
    table = pd.DataFrame({"Name": ["EQX", "EQX(1/3)"], "IsAuto": ["No", "Yes"],
                          "Z": ["0.4", "0.4"], "I": ["1", "1"], "R": ["8.5", "8.5"],
                          "Ct": ["0.03", "0.03"]})
    assert mi.seismic_from_table(table) == {"zone_factor": 0.4, "importance": 1.0,
                                            "r_factor": 8.5, "ct": 0.03}
    assert mi.seismic_from_table(pd.DataFrame()) == {}
    assert mi.seismic_from_table(None) == {}


def test_seismic_differences_between_the_model_and_what_was_saved():
    model = {"zone_factor": 0.4, "ct": 0.03, "r_factor": 8.5}
    saved = {"zone_factor": 0.2, "ct": 0.03}
    assert mi.seismic_differences(model, saved) == {"zone_factor": (0.4, 0.2)}


def ask_with(monkeypatch, typed=None):
    """Run ask_seismic_values with the dialog answered by ``typed`` (None: fail if asked)."""
    from utilities import _gui_helpers as gui

    shown = []

    def enter_values(title, prompt, labels, defaults=None):
        shown.append(labels)
        if typed is None:
            raise AssertionError("nothing should be asked")
        return {label: typed.get(label.split(":")[0].split()[0], defaults[label])
                for label in labels}

    monkeypatch.setattr(gui, "enter_values", enter_values)
    return shown


def test_seismic_values_in_the_model_are_not_asked(monkeypatch, tmp_path):
    ask_with(monkeypatch)
    values, sources = mi.ask_seismic_values(str(tmp_path / "m.EDB"), "t",
                                            {"zone_factor": 0.4, "ct": 0.03}, {})
    assert values == {"zone_factor": 0.4, "ct": 0.03}
    assert sources.model == ["Z 0.4", "Ct 0.03"] and not sources.answered


def test_seismic_values_missing_from_the_model_are_asked_and_saved(monkeypatch, tmp_path):
    shown = ask_with(monkeypatch, {"Z": "0.2"})
    model_path = str(tmp_path / "m.EDB")
    values, sources = mi.ask_seismic_values(model_path, "t", {}, {})
    assert len(shown) == 1 and len(shown[0]) == 2
    assert values == {"zone_factor": 0.2, "ct": 0.03}
    assert sources.answered == ["Z 0.2", "Ct 0.03"]
    assert mi.load(model_path)["seismic"] == {"zone_factor": 0.2, "ct": 0.03}
    # the next run finds them saved and does not ask again
    ask_with(monkeypatch)
    again, _ = mi.ask_seismic_values(model_path, "t", {}, mi.load(model_path)["seismic"])
    assert again == values


def test_a_seismic_value_that_differs_from_the_saved_one_is_asked(monkeypatch, tmp_path):
    shown = ask_with(monkeypatch, {})
    values, sources = mi.ask_seismic_values(str(tmp_path / "m.EDB"), "t",
                                            {"zone_factor": 0.4, "ct": 0.03},
                                            {"zone_factor": 0.2, "ct": 0.03})
    assert len(shown[0]) == 1 and "model has 0.4" in shown[0][0]
    assert values["zone_factor"] == 0.4  # the model's value is the default answer


# ----------------------------------------------------------------- saved with the model
def test_answers_are_saved_beside_the_model_and_keep_the_setup_inputs(tmp_path):
    model_path = str(tmp_path / "tower.EDB")
    setup_file = tmp_path / "tower.setup.json"
    assert mi.load(model_path) == {} and not mi.has_setup_inputs(model_path)
    mi.save(model_path, strength=["COMB1"])
    assert not mi.has_setup_inputs(model_path)  # answers alone are not setup inputs
    setup_file.write_text(json.dumps({"sections": {"G": {}}, "model": {"strength": ["COMB1"]}}))
    mi.save(model_path, drift=["DR1"])
    data = json.loads(setup_file.read_text())
    assert data["sections"] == {"G": {}}
    assert data["model"] == {"strength": ["COMB1"], "drift": ["DR1"]}
    assert mi.has_setup_inputs(model_path)


# ----------------------------------------------------------------- readiness and sources
def test_readiness_is_shown_only_when_something_is_missing(monkeypatch):
    from utilities import _gui_helpers as gui

    shown = []
    monkeypatch.setattr(gui, "select_option",
                        lambda title, prompt, options, default_index=0:
                        shown.append(prompt) or "Stop")
    ready = mi.Readiness("sdt beams", found=["12 strength combinations named ULS"])
    assert ready.confirm("t") and not shown
    ready.missing.append("deflection combinations")
    assert not ready.confirm("t")  # the user stopped
    assert "Missing" in shown[0] and "Found" in shown[0]


def test_sources_go_into_the_summary():
    from utilities.run_summary import RunSummary

    summary = RunSummary("sdt beams", None)
    mi.Sources(model=["Z 0.4"], answered=["12 strength combinations (picked by you)"],
               assumed=["deflection combinations added"]).add_to(summary)
    labels = [label for label, _ in summary.items]
    assert labels == ["Read from the model", "Answered by you", "Assumed"]


# ----------------------------------------------------------------- drift on picked combinations
def test_drift_findings_on_picked_combinations_without_ubc97_patterns():
    from etabs_api.workflows.drift_check import level_findings

    drifts = {"DR1": (0.002, "2F"), "WD1": (0.001, "3F")}
    findings = level_findings(drifts, pd.DataFrame(), {"EX": 5, "WX": 6}, 400,
                              {"DR1": ("EX", False), "WD1": ("WX", True)}, r_factor=8.5)
    text = " ".join(f.text for f in findings)
    assert "EX" in text and "WX" in text
    # 0.7 R x 0.002 = 0.0119 < 0.02
    seismic = [f for f in findings if "EX" in f.text]
    assert seismic and all(f.status != "FAIL" for f in seismic)


# ----------------------------------------------------------------- sections with other names
class FakeTables:
    def SetLoadCasesSelectedForDisplay(self, names):
        return 0

    def SetLoadCombinationsSelectedForDisplay(self, names):
        return 0


class FakeModel:
    """The few calls read_model_sections makes on the ETABS model."""

    DatabaseTables = FakeTables()

    class Story:
        @staticmethod
        def GetStories():
            return (3, ["Base", "GF", "2F", "RD"], [], [], [], [], [], [], 0)

    class PropFrame:
        @staticmethod
        def GetModifiers(name, values):
            cracked = [1, 1, 1, 1, 0.35, 0.35, 1, 1]
            return (cracked if name == "RB 300x500" else [1.0] * 8, 0)


class FakeConnector:
    sap_model = FakeModel()

    def __init__(self, tables, include_numeric=False):
        self.tables = tables
        self.include_numeric_members = include_numeric

    def _read_database_table(self, name):
        if name not in self.tables:
            raise RuntimeError(name)
        return self.tables[name]


def foreign_tables() -> dict:
    return {
        "Frame Assignments - Section Properties": pd.DataFrame({
            "UniqueName": ["1", "2", "3", "4", "5", "6"],
            "SectProp": ["RB 300x500", "RB 300x500", "RB 300x500", "COL 500", "COL D600",
                         "W14X90"]}),
        "Frame Section Property Definitions - Concrete Rectangular": pd.DataFrame({
            "Name": ["RB 300x500", "COL 500"], "Material": ["CONC28", "CONC28"],
            "t3": ["500", "500"], "t2": ["300", "500"]}),
        "Frame Section Property Definitions - Concrete Circle": pd.DataFrame({
            "Name": ["COL D600"], "Material": ["CONC28"], "t3": ["600"]}),
        "Frame Section Property Definitions - Concrete Beam Reinforcing": pd.DataFrame({
            "Name": ["RB 300x500"], "RebarMatL": ["STEEL414"], "TopCover": ["62.5"]}),
        "Frame Section Property Definitions - Concrete Column Reinforcing": pd.DataFrame({
            "Name": ["COL 500", "COL D600"], "RebarMatL": ["STEEL414", "STEEL414"],
            "Cover": ["40", "40"]}),
        "Material Properties - Concrete Data": pd.DataFrame({
            "Material": ["CONC28"], "Fc": ["27.579"]}),
        "Material Properties - Rebar Data": pd.DataFrame({
            "Material": ["STEEL414"], "Fy": ["413.685"]}),
        "Beam Object Connectivity": pd.DataFrame({
            "UniqueName": ["1", "2", "3", "6"], "Story": ["2F", "2F", "GF", "2F"],
            "UniquePtI": ["a", "m", "g", "x"], "UniquePtJ": ["b", "n", "h", "y"]}),
        "Column Object Connectivity": pd.DataFrame({
            "UniqueName": ["4", "5"], "Story": ["2F", "2F"],
            "UniquePtI": ["a0", "b0"], "UniquePtJ": ["a", "b"]}),
    }


def test_sections_with_other_names_are_read_from_etabs():
    from etabs_api.workflows.design_loop import read_model_sections

    found = read_model_sections(FakeConnector(foreign_tables(), include_numeric=True))
    section = found.sections
    # beam 1 frames into the columns, beam 2 sits on other beams, beam 3 is on the bottom level
    assert (section["1"].family, section["2"].family, section["3"].family) == ("G", "B", "FTB")
    assert (section["1"].width, section["1"].depth) == (300, 500)
    assert section["1"].concrete == "C04" and section["1"].rebar == "G60"
    assert section["4"].family == "CR" and not section["4"].circular
    assert section["5"].circular and section["5"].width == 600
    assert found.actual["1"] == "RB 300x500"
    assert found.materials == {"C04": "CONC28", "G60": "STEEL414"}
    assert found.covers["1"] == 62.5 and found.covers["4"] == 40
    assert found.skipped == {"W14X90": "not a rectangular or circular concrete section"}
    assert found.modified == ["RB 300x500"]  # it carries modifiers of its own
    assert found.families() == {"G": 1, "B": 1, "FTB": 1, "CR": 1, "C": 1}


def test_untagged_members_are_left_out_unless_the_user_chose_to_design_them():
    from etabs_api.workflows.design_loop import read_model_sections

    assert read_model_sections(FakeConnector(foreign_tables())).sections == {}


def test_sections_named_as_setup_names_them_need_no_reading():
    from etabs_api.workflows.design_loop import read_model_sections

    tables = {"Frame Assignments - Section Properties": pd.DataFrame({
        "UniqueName": ["2GX-1", "2-C1"],
        "SectProp": ["G_300X500_C04_G60", "CR_500X500_C04_G60"]})}
    found = read_model_sections(FakeConnector(tables))
    assert not found.foreign and found.families() == {"G": 1, "CR": 1}
    assert found.actual["2GX-1"] == "G_300X500_C04_G60"


# ----------------------------------------------------------------- prepare_model
class Dialogs:
    """Answers the dialogs of prepare_model and records what was shown."""

    def __init__(self, monkeypatch, answers=None, picks=None):
        from utilities import _gui_helpers as gui

        self.options, self.answers, self.picks = [], answers or {}, picks
        monkeypatch.setattr(gui, "select_option", self.select_option)
        monkeypatch.setattr(gui, "DualListboxSelector", self.selector)
        monkeypatch.setattr(gui, "show_warning", lambda *a, **k: None)

    def select_option(self, title, prompt, options, default_index=0):
        self.options.append((prompt, list(options)))
        for key, answer in self.answers.items():
            match = [o for o in options if o.startswith(answer)]
            if key in prompt and match:
                return match[0]
        return "Continue" if "Continue" in options else options[default_index]

    def selector(self, title, items):
        picks = self.picks

        class Selector:
            def show(self):
                return [i for i in items if i in picks]

        return Selector()


def prepared(monkeypatch, tmp_path, names_of_members, combos, dialogs_kwargs=None):
    from design import concrete_workflow as cw

    monkeypatch.setattr(mi, "read_combinations", lambda connector: combos)
    monkeypatch.setattr(mi, "wall_count", lambda connector: 0)
    monkeypatch.setattr(cw, "member_names", lambda connector: names_of_members)
    monkeypatch.setattr(cw, "pdelta_method", lambda connector: "Iterative Based on Loads")
    dialogs = Dialogs(monkeypatch, **(dialogs_kwargs or {}))

    class Connector:
        include_numeric_members = False

    connector = Connector()
    ready = cw.prepare_model(connector, str(tmp_path / "m.EDB"), "t", "sdt beams", {})
    return ready, dialogs, connector


def test_a_setup_model_is_only_asked_the_seismic_combinations(monkeypatch, tmp_path):
    names = ["ULS 100", "ULS 107 EQ1", "ULS 107 RSA1"] + list(mi.DEFLECTION_ROLES)
    terms = {name: ComboTerms(name, {"D": 1.0}) for name in names}
    ready, dialogs, _ = prepared(monkeypatch, tmp_path, ["2GX-1", "2-C1"],
                                 mi.Combinations(names, terms, TYPES))
    assert len(dialogs.options) == 1 and "Seismic combinations" in dialogs.options[0][0]
    assert ready.combos == ["ULS 100", "ULS 107 RSA1"] and ready.seismic_key == "RSA"
    assert ready.deflection_roles == {role: role for role in mi.DEFLECTION_ROLES}
    assert not ready.sources.answered and len(ready.sources.model) == 2


def test_a_model_with_other_names_is_asked_what_is_missing(monkeypatch, tmp_path):
    ready, dialogs, connector = prepared(
        monkeypatch, tmp_path, ["11", "12", "C1"], combinations(),
        {"answers": {"still have the number": "Design them as they are",
                     "dead + sustained": "Let the toolkit add it"},
         "picks": ["1.4D", "1.2D+1.6L", "1.2D+L+EX", "ENVELOPE"]})
    readiness = dialogs.options[0][0]
    assert "none is named ULS" in readiness and "deflection combinations" in readiness
    assert "2 of 3 beams and columns" in readiness
    assert connector.include_numeric_members is True
    assert ready.combos == ["1.4D", "1.2D+1.6L", "1.2D+L+EX"]  # the envelope was not offered
    assert ready.seismic_key is None
    assert ready.gravity_choices == ["1.4D", "1.2D+1.6L"]
    assert ready.deflection_roles[COMBO_DEAD] == "SVC D"
    assert ready.deflection_roles[COMBO_SUSTAINED] == COMBO_SUSTAINED  # to be added
    assert any("picked by you" in text for text in ready.sources.answered)
    assert any(COMBO_SUSTAINED in text for text in ready.sources.assumed)
    saved = mi.load(str(tmp_path / "m.EDB"))
    assert saved["strength"] == ready.combos and saved["deflection"][COMBO_FULL] == "SVC D+L"


def test_stopping_at_the_readiness_dialog_stops_the_command(monkeypatch, tmp_path):
    ready, dialogs, _ = prepared(monkeypatch, tmp_path, ["11"], combinations(),
                                 {"answers": {"Before sdt beams starts": "Stop"}})
    assert ready is None and len(dialogs.options) == 1


def test_the_check_does_not_fail_a_model_for_its_combination_names():
    from etabs_api.workflows import model_check as mc

    data = mc.ModelData(tables={
        "Load Combination Definitions": pd.DataFrame({
            "Name": ["COMB1", "COMB2"], "Type": ["Linear Add"] * 2,
            "LoadName": ["D", "L"], "SF": [1.4, 1.6]}),
        "Load Case Definitions - Summary": pd.DataFrame({"Name": ["D", "L"]}),
    })
    findings = [f for f in mc.check_combinations(data) if "ULS" in f.text]
    assert findings and findings[0].status == mc.WARN
    assert "ask which ones to design for" in findings[0].text
