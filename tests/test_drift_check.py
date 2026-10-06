"""Tests for the drift check (``sdt drift``) that need no ETABS."""

import pandas as pd
import pytest

from etabs_api.workflows import drift_check as dc
from etabs_api.workflows import model_check as mc


def test_levels_are_as_modelled_strength_and_service():
    modelled, strength, service = dc.stiffness_levels()
    assert modelled.as_modelled and modelled.beam is None
    assert (strength.beam, strength.column) == (0.35, 0.70)
    assert service.beam == pytest.approx(0.49) and service.column == pytest.approx(0.98)


def test_service_level_is_capped_at_the_gross_section(monkeypatch):
    from design.code_config import NSCP, override

    monkeypatch.setattr(dc, "NSCP", override(NSCP, analysis__column_inertia=0.8))
    assert dc.stiffness_levels()[2].column == 1.0


NAMES = ["DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 EQXSD", "DRIFT 102 (0.9 - Ev) DL + 1.0 EQYSD",
         "DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 RSAXD", "WDRIFT 102 0.9 DL + 1.0 WX",
         "ENVE_DRIFT", "ULS 100 1.4 DL"]


@pytest.mark.parametrize("seismic, expected", [
    (dc.STATIC, [NAMES[0], NAMES[1], NAMES[3]]),
    (dc.SPECTRUM, [NAMES[2], NAMES[3]]),
    (dc.BOTH, NAMES[:4]),
])
def test_static_or_spectrum_drift_combinations(seismic, expected):
    assert dc.select_combinations(NAMES, seismic) == expected


def plan(points):
    """Points and the columns ending at them, all on story 2F."""
    names = [f"P{i}" for i in range(len(points))]
    pts = pd.DataFrame({"UniqueName": names, "X": [p[0] for p in points],
                        "Y": [p[1] for p in points]})
    cols = pd.DataFrame({"UniqueName": [f"C{i}" for i in range(len(points))],
                         "Story": ["2F"] * len(points), "UniquePtJ": names})
    return pts, cols


def test_outer_corners_of_a_rectangle():
    grid = [(x, y) for x in (0, 6000, 12000) for y in (0, 5000, 10000)]
    corners = dc.outer_corners(*plan(grid))["2F"]
    xy = dict(zip([f"P{i}" for i in range(len(grid))], grid))
    assert sorted(xy[c] for c in corners) == [(0, 0), (0, 10000), (12000, 0), (12000, 10000)]


def test_outer_corners_of_an_l_shape():
    # the re-entrant corner (6000, 5000) is not picked; the far ends are
    grid = [(0, 0), (12000, 0), (12000, 5000), (6000, 5000), (6000, 10000), (0, 10000)]
    corners = dc.outer_corners(*plan(grid))["2F"]
    picked = {grid[int(c[1:])] for c in corners}
    assert (6000, 5000) not in picked
    assert {(0, 0), (12000, 0), (0, 10000)} <= picked


def test_corner_drifts_take_the_largest_of_x_and_y():
    table = pd.DataFrame({
        "Story": ["2F", "2F", "2F"], "UniqueName": ["P0", "P1", "P9"],
        "OutputCase": ["DRIFT 100 X"] * 3, "DriftX": [0.002, 0.001, 0.009],
        "DriftY": [0.0001, 0.003, 0.0]})
    out = dc.corner_drifts(table, {"2F": ["P0", "P1"]})  # P9 is not a corner
    assert out["DRIFT 100 X"][0] == pytest.approx(0.003)
    assert "P1" in out["DRIFT 100 X"][1]


def test_center_drifts_from_the_centre_of_mass_displacements():
    table = pd.DataFrame({
        "Story": ["2F", "RD"], "Diaphragm": ["D1", "D1"], "OutputCase": ["C", "C"],
        "StepType": ["", ""], "UX": [8.0, 20.0], "UY": [0.0, 0.0], "Z": [4000, 7000]})
    out = dc.center_drifts(table, base_elevation=0.0)
    # 2F: 8 / 4000 = 0.002; RD: 12 / 3000 = 0.004 governs
    assert out["C"][0] == pytest.approx(0.004) and out["C"][1].startswith("RD")


def test_center_drifts_start_from_a_footing_base():
    table = pd.DataFrame({"Story": ["GF"], "Diaphragm": ["D1"], "OutputCase": ["C"],
                          "StepType": [""], "UX": [3.0], "UY": [0.0], "Z": [0.0]})
    assert dc.center_drifts(table, base_elevation=-1500.0)["C"][0] == pytest.approx(0.002)


def test_level_findings_check_seismic_and_wind():
    seismic = pd.DataFrame({"Name": ["EQXSD", "EQXSD(1/2)"], "IsAuto": ["No", "Yes"],
                            "R": [8.5, 8.5], "TUsed": [None, 1.5]})
    drifts = {"DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 EQXSD": (0.003, "2F"),
              "WDRIFT 101 1.2 DL + f LL + 0.5 Lr + 1.0 WX": (0.002, "2F")}
    findings = dc.level_findings(drifts, seismic, {"EQXSD": mc.SEISMIC_DRIFT, "WX": mc.WIND},
                                 400.0)
    statuses = {f.text.split(":")[0]: f.status for f in findings}
    assert statuses == {"EQXSD": mc.OK, "WX": mc.OK}  # 0.7 x 8.5 x 0.003 = 0.0179 <= 0.020


def test_effective_modifiers_multiply_object_and_section():
    d = mc.ModelData(tables={
        "Frame Assignments - Section Properties": pd.DataFrame(
            {"UniqueName": ["B1", "C1"], "SectProp": ["G_300X600_C28_R414", "CR_500X500_C28_R414"]}),
        "Frame Assignments - Property Modifiers": pd.DataFrame(
            {"UniqueName": ["B1", "C1"], "AMod": [1, 1], "A2Mod": [1, 1], "A3Mod": [1, 1],
             "JMod": [0.01, 1], "I2Mod": [0.35, 0.7], "I3Mod": [0.35, 0.7], "MMod": [1, 0.7],
             "WMod": [1, 1]}),
        "Column Object Connectivity": pd.DataFrame({"UniqueName": ["C1"]}),
    }, section_modifiers={"G_300X600_C28_R414": (1, 1, 1, 1, 0.35, 0.35, 1, 1),
                          "CR_500X500_C28_R414": (1, 1, 1, 1, 0.7, 0.7, 1, 1)})
    effective = mc.effective_modifiers(d)
    assert effective["B1"][5] == pytest.approx(0.1225)
    assert effective["C1"][5] == pytest.approx(0.49)
    findings = mc._check_modifiers(d)
    assert findings[0].status == mc.WARN and "applied twice" in findings[0].text
    assert findings[1].status == mc.FAIL and "C1" in findings[1].text  # mass 0.7


def test_modelled_text_and_your_modifiers_are_judged():
    effective = {"B1": (1, 1, 1, 0.01, 0.35, 0.35, 1, 1), "B2": (1, 1, 1, 0.01, 0.35, 0.35, 1, 1),
                 "C1": (1, 1, 1, 1, 0.7, 0.7, 0.7, 1)}
    assert dc._modelled_text(effective, {"C1"}) == "beams I 0.35, columns I 0.70"
    findings = mc.modifier_findings(effective, {"C1"}, group="Modifiers")
    assert [f.status for f in findings] == [mc.OK, mc.FAIL]  # I right, column mass 0.7


def test_the_drift_of_every_story_is_listed_not_only_the_worst():
    """A deck at the top with a very large drift must not hide the storeys below it."""
    table = pd.DataFrame({
        "Story": ["DECK", "DECK", "3F", "3F", "2F", "2F", "2F"],
        "UniqueName": ["1", "1", "2", "2", "3", "3", "3"],
        "OutputCase": ["DRIFT 1", "WDRIFT 1", "DRIFT 1", "WDRIFT 1", "DRIFT 1", "DRIFT 2",
                       "WDRIFT 1"],
        "DriftX": [0.0300, 0.0020, 0.0040, 0.0010, 0.0050, 0.0061, 0.0025],
        "DriftY": [0.0100, 0.0001, 0.0045, 0.0002, 0.0010, 0.0010, 0.0001]})
    corners = {"DECK": ["1"], "3F": ["2"], "2F": ["3"]}
    by_story = dc.corner_story_drifts(table, corners)
    assert by_story[("DRIFT 1", "3F")] == 0.0045 and by_story[("DRIFT 2", "2F")] == 0.0061
    rows = dc.story_table(by_story, ["DECK", "3F", "2F"],
                          lambda combo: combo.startswith("WDRIFT"), 8.5)
    assert [r[0] for r in rows] == ["DECK", "3F", "2F"]
    story, seismic, delta_m, combo, wind, wind_combo = rows[2]
    assert (seismic, combo, wind_combo) == (0.0061, "DRIFT 2", "WDRIFT 1")
    assert delta_m == pytest.approx(0.7 * 8.5 * 0.0061) and wind == 0.0025
    text = "\n".join(dc.story_table_text(rows))
    assert "h/400" in text and "0.0363" in text and "DECK" in text       # 2F: h/400, 0.0363
    assert dc.story_table({}, ["2F"], lambda c: False, 8.5) == []


def test_the_service_stiffness_factor_can_be_the_engineers():
    code = dc.stiffness_levels()[2]
    assert (code.beam, code.column) == (pytest.approx(0.49), pytest.approx(0.98))
    assert code.at_least_modelled and "ACI 6.6.3.2.2" in code.reference
    own = dc.stiffness_levels(1.6)[2]
    assert own.beam == pytest.approx(0.56) and own.column == 1.0        # never above gross
    assert "1.6" in own.name and "your factor" in own.reference
    assert not dc.stiffness_levels(1.6)[1].at_least_modelled             # the strength level is the code's


def test_a_drift_pattern_under_the_zone_4_minimum_is_reported():
    """Typed Ca, Cv: ETABS takes Nv = 1 in Eq. 208-11, and the drift is too small with it."""
    from etabs_api.workflows.ubc97 import seismic_coefficients

    ca, cv = seismic_coefficients(0.4, "SD", "A", 8.0)                    # Nv 1.36
    table = pd.DataFrame({"Name": ["EQXSD", "EQXSD(2/3)", "EQX"], "Ca": [ca] * 3, "Cv": [cv] * 3,
                          "I": [1.0] * 3, "R": [8.5] * 3,
                          "CoeffUsed": [0.8 * 0.4 / 8.5] * 3})
    types = {"EQXSD": mc.SEISMIC_DRIFT, "EQX": mc.SEISMIC}
    found = dc.typed_minimum_findings(table, types)
    assert len(found) == 1 and found[0].status == mc.FAIL
    assert found[0].text.startswith("EQXSD:") and "too small by the same share" in found[0].text
    assert dc.typed_minimum_findings(table, types, {"EQXSD": 1.37}) == []   # scaled in its case
    table["CoeffUsed"] = 0.8 * 0.4 * 1.36 / 8.5
    assert dc.typed_minimum_findings(table, types) == []
    assert dc.typed_minimum_findings(pd.DataFrame({"Name": []}), types) == []
