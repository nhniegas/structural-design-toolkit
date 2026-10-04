"""Tests for the model check (``xs check``) on synthetic model data, no ETABS."""

import pandas as pd
import pytest

from etabs_api.workflows import model_check as mc

STORIES = [("Base", 0.0), ("2F", 4000.0), ("3F", 7500.0), ("RD", 11000.0)]


def seismic_row(name, **values):
    row = {"Name": name, "IsAuto": "No", "XDirPlusE": "Yes", "XDirMinusE": "No",
           "YDirPlusE": "No", "YDirMinusE": "No", "EccRatio": 0.05, "TopStory": "RD",
           "BotStory": "Base", "PeriodType": "Program Calculated", "Ct": 0.03,
           "CoeffOpt": "Per Code", "SoilType": "SD", "Z": 0.4, "Ca": 0.44, "Cv": 0.768,
           "SourceType": "A", "SourceDist": 10, "Na": 1.0, "Nv": 1.2, "I": 1.0, "R": 8.5}
    row.update(values)
    return row


def model(rows, analysed=False, **tables):
    data = mc.ModelData(analysed=analysed, stories=list(STORIES),
                        pattern_types={"EQX": mc.SEISMIC, "EQXSD": mc.SEISMIC_DRIFT,
                                       "DEAD": mc.DEAD},
                        tables={"Load Pattern Definitions - Auto Seismic - UBC 97":
                                pd.DataFrame(rows), **tables})
    return data


def by_ref(findings, ref):
    return [f for f in findings if ref in f.ref]


# --------------------------------------------------------------------------- #
# NSCP 208 formulas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("period, drift, expected", [
    (0.243, False, 2.5 * 0.44 / 8.5),           # 208-9 governs
    (1.518, True, 0.768 / (8.5 * 1.518)),       # 208-8, drift: no lower limit
    (5.0, False, 0.11 * 0.44),                  # 208-10 governs (0.0484 > 208-11 0.0452)
    (5.0, True, 0.768 / (8.5 * 5.0)),
])
def test_nscp_coefficient(period, drift, expected):
    assert mc.nscp_coefficient(0.44, 0.768, 1.0, 8.5, period, 0.4, 1.2, drift) == \
        pytest.approx(expected)


def test_zone_4_minimum_208_11():
    # soil SA, near source: 0.8 Z Nv I / R above 0.11 Ca I
    value = mc.nscp_coefficient(0.32, 0.64, 1.0, 4.5, 10.0, 0.4, 2.0)
    assert value == pytest.approx(0.8 * 0.4 * 2.0 / 4.5)


@pytest.mark.parametrize("period, limit", [(0.5, 0.025), (0.7, 0.020), (1.5, 0.020)])
def test_drift_limit(period, limit):
    assert mc.drift_limit(period) == limit


def test_method_a_period_metric_and_ft_agree():
    height = 20000.0
    assert mc.method_a_period(0.03, height) == pytest.approx(0.0731 * 20.0 ** 0.75, rel=2e-3)


# --------------------------------------------------------------------------- #
# seismic inputs against the NSCP tables
# --------------------------------------------------------------------------- #
def test_good_inputs_pass():
    findings = mc.check_seismic(model([seismic_row("EQX")], analysed=True))
    assert not [f for f in findings if f.status == mc.FAIL]
    assert by_ref(findings, "208-5 to 208-8")[0].status == mc.OK


def test_zone_factor_outside_nscp_fails():
    findings = mc.check_seismic(model([seismic_row("EQX", Z=0.3)]))
    assert by_ref(findings, "Table 208-3")[0].status == mc.FAIL


def test_hand_typed_ca_cv_are_checked():
    findings = mc.check_seismic(model([seismic_row("EQX", Ca=0.40, Cv=0.64)], analysed=True))
    assert by_ref(findings, "208-5 to 208-8")[0].status == mc.WARN


def test_typed_near_source_factors_are_used_instead_of_the_distance():
    # Nv 1.2 typed in with the distance left at 15 km: ETABS uses Cv = 0.64 x 1.2
    row = seismic_row("EQX", SourceDist=15, NearSrcOpt="User Defined", Na=1.0, Nv=1.2)
    findings = mc.check_seismic(model([row], analysed=True))
    assert by_ref(findings, "208-5 to 208-8")[0].status == mc.OK
    note = by_ref(findings, "Tables 208-5, 208-6")[0]
    assert note.status == mc.INFO and "about 10 km" in note.text


def test_intermediate_frame_not_permitted_in_zone_4():
    findings = mc.check_seismic(model([seismic_row("EQX", R=5.5)]))
    assert by_ref(findings, "Table 208-11A")[0].status == mc.FAIL


def test_unknown_ct_warns():
    findings = mc.check_seismic(model([seismic_row("EQX", Ct=0.025)]))
    assert by_ref(findings, "208-12")[0].status == mc.WARN


def test_story_range_from_the_ground_level():
    findings = mc.check_seismic(model([seismic_row("EQX", BotStory="2F")]))
    assert by_ref(findings, "208.5.2.3")[0].status == mc.FAIL


# --------------------------------------------------------------------------- #
# with results
# --------------------------------------------------------------------------- #
def analysed(**values):
    row = seismic_row("EQX", **{"TUsed": 0.243, "CoeffUsed": 2.5 * 0.44 / 8.5, **values})
    drift = seismic_row("EQXSD(1/2)", IsAuto="Yes", TUsed=1.518, CoeffUsed=0.768 / (8.5 * 1.518))
    return model([row, drift], analysed=True)


def test_static_coefficient_and_period_cap_pass():
    findings = mc.check_seismic(analysed())
    coefficient = by_ref(findings, "Eq. 208-8 to 208-11")
    assert [f.status for f in coefficient] == [mc.OK]
    assert by_ref(findings, "208.6.5.2")[0].status == mc.OK
    assert [f.status for f in findings if f.ref == "NSCP 208.5.2.2" and "s, cap" in f.text] == [mc.OK]


def test_edited_static_coefficient_fails():
    findings = mc.check_seismic(analysed(CoeffUsed=0.10))
    assert by_ref(findings, "Eq. 208-8 to 208-11")[0].status == mc.FAIL


def test_period_above_the_cap_fails():
    # T_A = 0.03 (11000 / 304.8)^0.75 = 0.441 s; cap 1.3 T_A = 0.573 s
    findings = mc.check_seismic(analysed(TUsed=0.70, CoeffUsed=0.768 / (8.5 * 0.70)))
    cap = [f for f in findings if f.ref == "NSCP 208.5.2.2" and "s, cap" in f.text]
    assert cap[0].status == mc.FAIL


def test_drift_uses_0_7_r_and_the_period_limit():
    d = analysed()
    d.drifts = {"EQXSD": (0.0030, "2F"), "RSAXD": (0.0040, "3F")}
    drift = by_ref(mc.check_seismic(d), "208.6.5.1")
    # 0.7 x 8.5 x 0.003 = 0.01785 <= 0.020; 0.7 x 8.5 x 0.004 = 0.0238 > 0.020 (T 1.52 s)
    assert [(f.text.split(":")[0], f.status) for f in drift] == [("EQXSD", mc.OK),
                                                                  ("RSAXD", mc.FAIL)]


def test_drift_from_combinations_takes_the_worst_per_case():
    d = analysed()
    d.pattern_types["WX"] = mc.WIND
    d.drift_from = "combinations"
    d.drifts = {
        "DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 EQXSD": (0.0020, "2F"),
        "DRIFT 102 (0.9 - Ev) DL + 1.0 EQXSD": (0.0030, "2F"),   # governs: 0.01785
        "DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 RSAXD": (0.0040, "3F"),
        "WDRIFT 101 1.2 DL + f LL + 0.5 Lr + 1.0 WX": (0.0030, "3F"),
        "WDRIFT 102 0.9 DL + 1.0 WX": (0.0020, "3F"),
    }
    seismic = {f.text.split(":")[0]: f for f in by_ref(mc.check_seismic(d), "208.6.5.1")}
    assert seismic["EQXSD"].status == mc.OK and "DRIFT 102" in seismic["EQXSD"].text
    assert seismic["RSAXD"].status == mc.FAIL
    d.tables["Load Pattern Definitions - Auto Wind - ASCE 7-10"] = pd.DataFrame(
        {"Name": ["WX"], "BotStory": ["Base"], "TopStory": ["RD"], "WindSpeed": [150]})
    wind = [f for f in mc.check_wind(d) if "drift" in f.text]
    assert wind[0].status == mc.FAIL and "h/333" in wind[0].text  # 0.003 > 1/400
    d.wind_drift_denominator = 300.0
    assert [f for f in mc.check_wind(d) if "drift" in f.text][0].status == mc.OK


def test_drift_from_cases_warns_for_older_models():
    d = analysed()
    d.drift_from = "cases"
    d.drifts = {"EQXSD": (0.001, "2F")}
    assert by_ref(mc.check_seismic(d), "208.6.4.1")[0].status == mc.WARN


def test_response_spectrum_scaling_bands():
    d = analysed()
    d.tables["Load Case Definitions - Response Spectrum"] = pd.DataFrame(
        {"Name": ["RSAX", "RSAY", "RSAZ"], "EccenRatio": [0.05] * 3})
    d.base_shear = {"EQX": (-100e3, 0.0), "RSAX": (100e3, 0.0), "RSAY": (95e3, 0.0),
                    "RSAZ": (80e3, 0.0)}
    status = {f.text.split(":")[0]: f.status for f in by_ref(mc.check_seismic(d), "208.5.3.5.4")}
    assert status == {"RSAX": mc.OK, "RSAY": mc.WARN, "RSAZ": mc.FAIL}


@pytest.mark.parametrize("weight_used, status", [(2500e3, mc.OK), (500e3, mc.FAIL)])
def test_static_weight_against_the_stories_above_the_ground(weight_used, status):
    # a story range starting too high leaves the lower floors out of W
    d = analysed(WeightUsed=weight_used)
    d.story_weights = {"Base": 50e3, "2F": 1000e3, "3F": 1000e3, "RD": 500e3}
    finding = by_ref(mc.check_seismic(d), "208.6.1")[0]
    assert finding.status == status


def test_drift_spectrum_case_is_compared_with_the_drift_pattern():
    d = analysed()
    d.tables["Load Case Definitions - Response Spectrum"] = pd.DataFrame(
        {"Name": ["RSAXD"], "EccenRatio": [0.05]})
    d.base_shear = {"EQX": (100e3, 0.0), "EQXSD": (40e3, 0.0), "RSAXD": (40e3, 0.0)}
    finding = by_ref(mc.check_seismic(d), "208.5.3.5.4")[0]
    assert finding.status == mc.OK and "of the static 40 kN" in finding.text


# --------------------------------------------------------------------------- #
# combinations: Ev
# --------------------------------------------------------------------------- #
def combos(dead_factor):
    return pd.DataFrame({
        "Name": ["EQ_COMBO_01", "ULS 107", None, "ULS 100"],
        "LoadName": ["EQX", "DEAD", "EQ_COMBO_01", "DEAD"],
        "SF": [1.0, dead_factor, 1.0, 1.4],
    })


@pytest.mark.parametrize("dead_factor, status", [(1.2 + 0.22, mc.OK), (1.2, mc.WARN)])
def test_seismic_combinations_carry_ev(dead_factor, status):
    d = model([seismic_row("EQX")])
    d.tables["Load Combination Definitions"] = combos(dead_factor)
    findings = by_ref(mc.check_combinations(d), "208.6.1")
    assert findings[0].status == status


# --------------------------------------------------------------------------- #
# special moment frame members (NSCP 418)
# --------------------------------------------------------------------------- #
def test_girder_width_is_the_smaller_of_0_3h_and_250():
    d = mc.ModelData(tables={
        "Frame Assignments - Section Properties": pd.DataFrame({
            "UniqueName": ["G1", "G2", "G3"],
            "SectProp": ["G_250X1000_C28_R414", "G_200X600_C28_R414", "G_200X500_C28_R414"]}),
        "Beam Object Connectivity": pd.DataFrame({"UniqueName": ["G1", "G2", "G3"],
                                                  "Length": [8000, 6000, 6000]}),
    })
    width = by_ref(mc.check_smrf_members(d), "418.6.2.1(b)")[0]
    # 250 x 1000: min(300, 250) = 250 OK; 200 x 600: min(180, 250) = 180 OK; 200 x 500 OK
    assert width.status == mc.OK
    d.tables["Frame Assignments - Section Properties"].loc[1, "SectProp"] = "G_200X900_C28_R414"
    width = by_ref(mc.check_smrf_members(d), "418.6.2.1(b)")[0]
    assert width.status == mc.WARN and "G2" in width.text


def test_per_code_placeholders_before_analysis_are_not_judged():
    # before analysis ETABS shows Ca 0.4 / Cv 0.56 for "Per Code" patterns
    d = model([seismic_row("EQX", Ca=0.4, Cv=0.56, SourceDist=10)])
    findings = mc.check_seismic(d)
    coefficient = by_ref(findings, "208-5 to 208-8")[0]
    assert coefficient.status == mc.INFO and "0.768" in coefficient.text
    d.tables["Load Combination Definitions"] = combos(1.2 + 0.22)
    assert by_ref(mc.check_combinations(d), "208.6.1")[0].status == mc.OK



@pytest.mark.parametrize("cv, status", [(0.768, mc.OK), (0.64, mc.WARN)])
def test_user_defined_coefficients_must_match_the_spectrum(cv, status):
    row = seismic_row("EQX", CoeffOpt="User Defined", Ca=0.44, Cv=cv, SourceDist=15)
    d = model([row], **{"Functions - Response Spectrum - UBC 97": pd.DataFrame(
        {"Name": ["RSUBC97"], "Ca": [0.44], "Cv": [0.768]})})
    findings = mc.check_seismic(d)
    assert by_ref(findings, "Tables 208-7, 208-8")[0].status == status
    # the 15 km field would give Cv 0.64: noted, since ETABS uses the typed values
    notes = [f for f in findings if "typed values" in f.text]
    assert bool(notes) == (cv == 0.768)


def user_defined_table():
    """The UBC 97 table as ETABS writes it for user-defined Ca, Cv (no zone, soil, source)."""
    base = {"IsAuto": "No", "XDir": "No", "XDirPlusE": "Yes", "XDirMinusE": "No", "YDir": "No",
            "YDirPlusE": "No", "YDirMinusE": "No", "EccRatio": 0.05, "TopStory": "RD",
            "BotStory": "Base", "PeriodType": "Program Calculated", "Ct": 0.03,
            "CoeffOpt": "User Defined", "Ca": 0.44, "Cv": 0.768, "Na": 1, "Nv": 1, "I": 1,
            "R": 8.5}
    rows = [dict(base, Name="EQX", TUsed=0.594, CoeffUsed=2.5 * 0.44 / 8.5),
            dict(base, Name="EQXSD", TUsed=None, CoeffUsed=None),
            dict(base, Name="EQXSD(1/2)", IsAuto="Yes", TUsed=1.518,
                 CoeffUsed=0.768 / (8.5 * 1.518))]
    return pd.DataFrame(rows)


def test_user_defined_table_without_site_columns():
    d = mc.ModelData(analysed=True, stories=list(STORIES),
                     pattern_types={"EQX": mc.SEISMIC, "EQXSD": mc.SEISMIC_DRIFT},
                     tables={"Load Pattern Definitions - Auto Seismic - UBC 97": user_defined_table(),
                             "Functions - Response Spectrum - UBC 97": pd.DataFrame(
                                 {"Name": ["RSUBC97"], "Ca": [0.44], "Cv": [0.768]})})
    d.drifts = {"EQXSD": (0.003, "2F")}
    findings = mc.check_seismic(d)
    assert not [f for f in findings if "could not run" in f.text]
    assert by_ref(findings, "Tables 208-7, 208-8")[0].status == mc.OK      # equal to the spectrum
    assert by_ref(findings, "Table 208-3")[0].status == mc.INFO            # zone 4 assumed
    assert [f.status for f in by_ref(findings, "Eq. 208-8 to 208-11")] == [mc.OK]
    drift = by_ref(findings, "208.6.5.1")[0]
    assert "T 1.52 s" in drift.text and drift.status == mc.OK
