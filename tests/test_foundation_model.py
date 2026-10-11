"""
tests/test_foundation_model.py
==============================
The foundation model (etabs_api/workflows/foundation_model.py): the parts
that need no ETABS. What goes on a support joint is the opposite of its
reaction; a wind case with several steps becomes one pattern and one
combination for each step; a response spectrum case is left out or signed;
and the loads of a pattern must add up to the base reaction of its case.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import foundation_model as fm  # noqa: E402
from etabs_api.workflows.foundation_model import (  # noqa: E402
    SPECTRUM,
    STATIC,
    STEP,
    Combo,
    Options,
    Pattern,
    Support,
)

SUPPORTS = [Support("1", 0.0, 0.0, -1000.0, [True, True, True, False, False, False], "column",
                    "GF-C1", "CR_600X600_C04_G60", 600.0, 600.0, live_factor=0.8),
            Support("3", 6000.0, 0.0, -1000.0, [True] * 6, "column", "GF-C2",
                    "CR_600X600_C04_G60", 600.0, 600.0)]


def reactions(rows) -> pd.DataFrame:
    """rows: (joint, case, kind, step, F1, F2, F3, M1, M2, M3)."""
    return pd.DataFrame(rows, columns=["Joint", "Case", "Kind", "Step", *fm.COMPONENTS])


def base(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["Case", "Step", "FX", "FY", "FZ"])


TABLE = reactions([
    ("1", "SELFWEIGHT", STATIC, 0, 10.0, 0.0, 300e3, 0.0, 0.0, 0.0),
    ("3", "SELFWEIGHT", STATIC, 0, -10.0, 0.0, 500e3, 0.0, 2e6, 0.0),
    ("1", "LIVERED", STATIC, 0, 0.0, 0.0, 50e3, 0.0, 0.0, 0.0),
    ("3", "LIVERED", STATIC, 0, 0.0, 0.0, 80e3, 0.0, 0.0, 0.0),
    ("1", "EQX", STATIC, 0, -30e3, 0.0, -90e3, 0.0, 0.0, 0.0),
    ("3", "EQX", STATIC, 0, -40e3, 0.0, 90e3, 0.0, 5e6, 0.0),
    ("1", "WX", STEP, 1, -5e3, 0.0, -2e3, 0.0, 0.0, 0.0),
    ("3", "WX", STEP, 1, -6e3, 0.0, 2e3, 0.0, 0.0, 0.0),
    ("1", "WX", STEP, 2, 0.0, -7e3, -1e3, 0.0, 0.0, 0.0),
    ("3", "WX", STEP, 2, 0.0, -8e3, 1e3, 0.0, 0.0, 0.0),
    ("1", "RSAX", SPECTRUM, 0, 33e3, 2e3, 95e3, 0.0, 0.0, 0.0),
    ("3", "RSAX", SPECTRUM, 0, 44e3, 1e3, 99e3, 0.0, 6e6, 0.0),
    ("99", "SELFWEIGHT", STATIC, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),   # not a support
])


def patterns(options=None, **kw):
    found, notes = fm.build_patterns(TABLE, SUPPORTS, options or Options(), **kw)
    return {p.name: p for p in found}, notes


# --------------------------------------------------------------------------
# LOADS
# --------------------------------------------------------------------------
def test_a_support_joint_gets_the_opposite_of_its_reaction():
    """HAND CALC: the support pushes the structure up with 300 kN, so the structure
    pushes the foundation down with 300 kN."""
    found, _ = patterns()
    assert found["SELFWEIGHT"].loads["1"] == [-10.0, 0.0, -300e3, 0.0, 0.0, 0.0]
    assert found["SELFWEIGHT"].loads["3"] == [10.0, 0.0, -500e3, 0.0, -2e6, 0.0]
    assert "99" not in found["SELFWEIGHT"].loads


def test_a_pinned_support_gives_no_moment_because_its_reaction_has_none():
    found, _ = patterns()
    assert found["EQX"].loads["1"][3:] == [0.0, 0.0, 0.0]        # pinned in the model
    assert found["EQX"].loads["3"][4] == -5e6                    # fixed: its moment goes over


def test_every_step_of_a_wind_case_is_its_own_pattern():
    found, _ = patterns()
    assert "WX" not in found
    assert found["WX-1"].loads["1"][:3] == [5e3, 0.0, 2e3]
    assert found["WX-2"].loads["3"][:3] == [0.0, 8e3, -1e3]
    assert (found["WX-2"].kind, found["WX-2"].case, found["WX-2"].step) == (STEP, "WX", 2)


def test_a_spectrum_case_is_left_out_unless_signed():
    found, notes = patterns()
    assert "RSAX" not in found
    assert any("RSAX" in note and "no sign" in note for note in notes)


def test_a_signed_spectrum_case_takes_the_signs_of_its_static_partner():
    """Peak values have no sign: each component takes the sign of the static case of
    the same direction at that joint, then the load is the opposite."""
    found, _ = patterns(Options(spectrum=fm.SPECTRUM_SIGNED), partners={"RSAX": "EQX"})
    # joint 1: EQX reaction (-30, 0, -90) kN -> RSAX reaction (-33, +2, -95) -> load (+33, -2, +95)
    assert found["RSAX"].loads["1"][:3] == [33e3, -2e3, 95e3]
    # joint 3: EQX (-40, 0, +90), moment +5 -> RSAX (-44, +1, +99), +6 -> load (+44, -1, -99), -6
    assert found["RSAX"].loads["3"] == [44e3, -1e3, -99e3, 0.0, -6e6, 0.0]
    assert found["RSAX"].partner == "EQX"


def test_a_spectrum_case_with_no_partner_is_left_out_and_said():
    found, notes = patterns(Options(spectrum=fm.SPECTRUM_SIGNED), partners={})
    assert "RSAX" not in found and any("no static seismic case" in n for n in notes)


def test_the_live_load_reduction_is_that_of_the_column_on_the_support():
    found, _ = patterns(Options(reduce_live=True), reducible=("LIVERED",))
    assert found["LIVERED"].loads["1"][2] == pytest.approx(-0.8 * 50e3)   # factor 0.8
    assert found["LIVERED"].loads["3"][2] == pytest.approx(-80e3)         # factor 1.0
    assert found["LIVERED"].reduced and not found["SELFWEIGHT"].reduced
    assert found["SELFWEIGHT"].loads["1"][2] == -300e3                    # only reducible live


# --------------------------------------------------------------------------
# EQUILIBRIUM
# --------------------------------------------------------------------------
BASE = base([("SELFWEIGHT", 0, 0.0, 0.0, 800e3), ("LIVERED", 0, 0.0, 0.0, 130e3),
             ("EQX", 0, -70e3, 0.0, 0.0), ("WX", 1, -11e3, 0.0, 0.0),
             ("WX", 2, 0.0, -15e3, 0.0), ("RSAX", 0, 77e3, 3e3, 0.0)])


def test_the_loads_of_a_pattern_add_up_to_the_base_reaction():
    found, _ = patterns()
    rows = {r["Pattern"]: r for r in fm.equilibrium_rows(list(found.values()), BASE)}
    assert all(rows[name]["Check"] == "OK" for name in ("SELFWEIGHT", "EQX", "WX-1", "WX-2"))
    assert rows["SELFWEIGHT"]["Sum FZ (kN)"] == pytest.approx(-800.0)
    assert rows["EQX"]["Sum FX (kN)"] == pytest.approx(70.0)


def test_a_load_that_is_missing_fails_the_check():
    found, _ = patterns()
    del found["SELFWEIGHT"].loads["3"]      # as if joint 3 were not read
    row = next(r for r in fm.equilibrium_rows(list(found.values()), BASE)
               if r["Pattern"] == "SELFWEIGHT")
    assert row["Check"].startswith("FAIL") and "500.000 kN" in row["Check"]


def test_a_reduced_live_pattern_and_a_spectrum_pattern_are_information_not_failures():
    found, _ = patterns(Options(spectrum=fm.SPECTRUM_SIGNED, reduce_live=True),
                        partners={"RSAX": "EQX"}, reducible=("LIVERED",))
    rows = {r["Pattern"]: r for r in fm.equilibrium_rows(list(found.values()), BASE)}
    assert rows["LIVERED"]["Check"].startswith("INFO: reduced live load: the loads are 92.3 %")
    assert rows["RSAX"]["Check"].startswith("INFO: peak values with the signs of EQX")


# --------------------------------------------------------------------------
# COMBINATIONS
# --------------------------------------------------------------------------
def definitions() -> pd.DataFrame:
    rows = [("ULS 1", "Linear Add", "SELFWEIGHT", 1.4),
            ("ULS 3", "Linear Add", "SELFWEIGHT", 1.2), (None, None, "WX", 0.5),
            ("RSA 1", "Linear Add", "RSAX", 1.0),
            ("ULS 7", "Linear Add", "SELFWEIGHT", 1.2), (None, None, "RSA 1", 1.0),
            ("ENVE", "Envelope", "ULS 1", 1.0), (None, None, "ULS 3", 1.0),
            (None, None, "ULS 7", 1.0)]
    return pd.DataFrame(rows, columns=["Name", "Type", "LoadName", "SF"])


CASES = {"SELFWEIGHT", "LIVERED", "EQX", "WX", "RSAX"}


def test_combinations_are_read_with_their_nested_combinations():
    combos = fm.read_combos(definitions())
    assert [c.name for c in combos] == ["ULS 1", "ULS 3", "RSA 1", "ULS 7", "ENVE"]
    assert combos[1].members == [("SELFWEIGHT", False, 1.2), ("WX", False, 0.5)]
    assert combos[3].members == [("SELFWEIGHT", False, 1.2), ("RSA 1", True, 1.0)]
    assert combos[4].kind == "Envelope"


def test_a_wind_combination_becomes_one_for_each_step_and_their_envelope():
    found, _ = patterns()
    plan, _ = fm.plan_combinations(fm.read_combos(definitions()), list(found.values()), CASES)
    by_name = {c.name: c for c in plan}
    assert by_name["ULS 3 [1]"].members == [("SELFWEIGHT", False, 1.2), ("WX-1", False, 0.5)]
    assert by_name["ULS 3 [2]"].members == [("SELFWEIGHT", False, 1.2), ("WX-2", False, 0.5)]
    assert by_name["ULS 3"] == Combo("ULS 3", "Envelope", [("ULS 3 [1]", True, 1.0),
                                                           ("ULS 3 [2]", True, 1.0)])
    names = [c.name for c in plan]     # a combination is made after what it uses
    assert names.index("ULS 3 [2]") < names.index("ULS 3") < names.index("ENVE")


def test_a_combination_that_uses_a_case_left_out_is_not_made_and_is_said():
    """Without one of its terms a linear combination would be another combination."""
    found, _ = patterns()      # the spectrum case is left out
    plan, notes = fm.plan_combinations(fm.read_combos(definitions()), list(found.values()), CASES)
    names = [c.name for c in plan]
    assert "RSA 1" not in names and "ULS 7" not in names
    assert any(n.startswith("RSA 1: not made") for n in notes)
    assert any(n.startswith("ULS 7: not made") for n in notes)
    # the envelope keeps what is left of it
    assert {m[0] for m in next(c for c in plan if c.name == "ENVE").members} == {"ULS 1", "ULS 3"}


def test_with_the_spectrum_case_signed_its_combinations_are_kept():
    found, _ = patterns(Options(spectrum=fm.SPECTRUM_SIGNED), partners={"RSAX": "EQX"})
    plan, notes = fm.plan_combinations(fm.read_combos(definitions()), list(found.values()), CASES)
    assert {"RSA 1", "ULS 7"} <= {c.name for c in plan} and notes == []


def test_an_envelope_of_a_stepped_case_takes_every_step():
    found, _ = patterns()
    combos = [Combo("ENVE WIND", "Envelope", [("WX", False, 1.0)])]
    plan, _ = fm.plan_combinations(combos, list(found.values()), CASES)
    assert plan == [Combo("ENVE WIND", "Envelope", [("WX-1", False, 1.0), ("WX-2", False, 1.0)])]


# --------------------------------------------------------------------------
# UPDATE
# --------------------------------------------------------------------------
def test_supports_are_matched_by_where_they_are_not_by_their_name():
    now = [Support("7", 0.0, 0.0, -1000.0), Support("3", 6000.4, 0.0, -1000.0),
           Support("12", 12000.0, 0.0, -1000.0)]
    stored = SUPPORTS + [Support("5", 0.0, 9000.0, -1000.0)]
    pairs, new, gone = fm.match_supports(stored, now)
    assert pairs == {"7": "1", "3": "3"}          # renumbered, and moved by less than 1 mm
    assert [s.joint for s in new] == ["12"] and [s.joint for s in gone] == ["5"]


def test_the_change_of_the_loads_is_reported_for_each_pattern():
    old = [{"name": "SIDL", "loads": {"1": [0, 0, -100e3, 0, 0, 0], "3": [0, 0, -200e3, 0, 0, 0]}},
           {"name": "GONE", "loads": {}},
           {"name": "SELFWEIGHT", "loads": {"1": [0, 0, -300e3, 0, 0, 0]}}]
    new = [Pattern("SIDL", "SIDL", STATIC, loads={"1": [0, 0, -100e3, 0, 0, 0],
                                                   "3": [0, 0, -298e3, 0, 4e6, 0]}),
           Pattern("SELFWEIGHT", "SELFWEIGHT", STATIC, loads={"1": [0, 0, -300.003e3, 0, 0, 0]}),
           Pattern("WX-1", "WX", STEP, 1)]
    rows = {r["Pattern"]: r for r in fm.load_changes(old, new)}
    assert rows["SIDL"]["Change"] == "changed"
    assert rows["SIDL"]["Sum FZ before (kN)"] == -300.0 and rows["SIDL"]["Sum FZ now (kN)"] == -398.0
    assert rows["SIDL"]["Largest change of a force (kN)"] == pytest.approx(98.0)
    assert rows["SIDL"]["Largest change of a moment (kN-m)"] == pytest.approx(4.0)
    assert rows["SELFWEIGHT"]["Change"] == "same"       # 3 N: what P-delta moves, not a change
    assert rows["WX-1"]["Change"] == "new pattern" and rows["GONE"]["Change"] == "no longer exported"


def test_the_files_of_a_foundation_model_are_named_after_it(tmp_path):
    model = str(tmp_path / "Tower.EDB")
    target = fm.foundation_path_for(model)
    assert target == str(tmp_path / "Tower - FOUNDATION.EDB")
    assert fm.load_manifest(target) is None
    fm.save_manifest(target, {"foundation": target, "supports": []})
    assert fm.manifest_path(target).endswith("Tower - FOUNDATION.foundation.json")
    assert fm.load_manifest(target)["foundation"] == target


def test_the_report_shows_the_check_and_what_changed():
    outcome = fm.Outcome("updated", "T - FOUNDATION.EDB")
    found, _ = patterns()
    outcome.equilibrium = fm.equilibrium_rows(list(found.values()), BASE)
    outcome.changes = [{"Pattern": "SIDL", "Sum FZ before (kN)": -300.0, "Sum FZ now (kN)": -398.0,
                        "Largest change of a force (kN)": 98.0,
                        "Largest change of a moment (kN-m)": 4.0, "Change": "changed"},
                       {"Pattern": "SELFWEIGHT", "Change": "same"}]
    text = fm.outcome_report(outcome).plain()
    assert "FOUNDATION MODEL - updated" in text and "SELFWEIGHT" in text and "-800.0" in text
    assert "SIDL" in text and "98.0" in text
    summary = fm.foundation_summary(outcome, "T.EDB").text()
    assert "the loads of every pattern add up to its base reaction" in summary
    assert "Patterns whose loads changed  1 of 2" in summary
