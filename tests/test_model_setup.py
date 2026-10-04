"""
tests/test_model_setup.py
=========================
Checks for the ETABS model setup: etabs_api/workflows/ubc97.py, load_combinations.py and
the planning functions of model_setup.py. None of them needs ETABS.

The UBC 97 values are checked against the two office models, whose ETABS
"per code" coefficients are known.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import load_combinations as lc  # noqa: E402
from etabs_api.workflows import model_setup as ms  # noqa: E402
from etabs_api.workflows import ubc97  # noqa: E402


# --------------------------------------------------------------------------
# UBC 97 COEFFICIENTS
# --------------------------------------------------------------------------
def test_near_source_factors_are_interpolated_on_distance():
    """Type A at 8.9 km: Na between 1.2 (5 km) and 1.0 (10 km), Nv between 1.6 and 1.2."""
    na, nv = ubc97.near_source_factors("A", 8.9)
    assert na == pytest.approx(1.044)
    assert nv == pytest.approx(1.288)


def test_near_source_factors_are_constant_outside_the_table():
    assert ubc97.near_source_factors("A", 0.5) == (1.5, 2.0)
    assert ubc97.near_source_factors("A", 40.0) == (1.0, 1.0)
    assert ubc97.near_source_factors("C", 1.0) == (1.0, 1.0)


def test_coefficients_match_the_test_model():
    """ETABS, zone 4, SE, source A at 8.9 km: Ca = 0.37584, Cv = 1.23648."""
    ca, cv = ubc97.seismic_coefficients(0.4, "SE", "A", 8.9)
    assert ca == pytest.approx(0.37584)
    assert cv == pytest.approx(1.23648)


def test_coefficients_of_soil_sd_at_8_km():
    """HAND CALC: Ca = 0.44 Na = 0.44 x 1.08, Cv = 0.64 Nv = 0.64 x 1.36."""
    ca, cv = ubc97.seismic_coefficients(0.4, "SD", "A", 8.0)
    assert ca == pytest.approx(0.4752)
    assert cv == pytest.approx(0.8704)


def test_near_source_does_not_apply_below_zone_4():
    assert ubc97.seismic_coefficients(0.2, "SD", "A", 1.0) == (0.28, 0.40)


@pytest.mark.parametrize("args", [(0.35, "SD"), (0.4, "SF"), (0.4, "SD", "D")])
def test_invalid_inputs_are_refused(args):
    with pytest.raises(ValueError):
        ubc97.seismic_coefficients(*args)


def test_vertical_effect_and_spectrum_scale():
    assert ubc97.vertical_effect_factor(0.37584, 1.0) == pytest.approx(0.18792)
    assert ubc97.response_spectrum_scale(1.0, 8.5) == pytest.approx(1153.72, abs=0.01)


# --------------------------------------------------------------------------
# LOAD COMBINATIONS
# --------------------------------------------------------------------------
PATTERNS = {
    "SELFWEIGHT": "Dead", "SIDL": "Super Dead", "LIVERED": "Reducible Live",
    "LIVENRED": "Live", "LIVEROOF": "Roof Live", "WX": "Wind", "WY": "Wind",
    "EQXPE": "Seismic", "EQXSD": "Seismic (Drift)",
}


def _combos(ca=0.4):
    return {combo.name: combo for combo in lc.build_combinations(PATTERNS, ca)}


def test_vertical_effect_goes_into_the_dead_load_factor():
    """Ev = 0.5 x 0.4 = 0.20: added in the 1.2 D case, subtracted in the 0.9 D case."""
    combos = _combos()
    heavy = dict(combos["ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ1"].cases)
    light = dict(combos["ULS 110 (0.9 - Ev) DL + 1.0 RSA4"].cases)
    assert heavy["SELFWEIGHT"] == pytest.approx(1.4) and heavy["SIDL"] == pytest.approx(1.4)
    assert light["SELFWEIGHT"] == pytest.approx(0.7)


def test_live_factor_and_roof_live_in_the_seismic_combination():
    cases = dict(_combos()["ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ1"].cases)
    assert cases["LIVERED"] == 0.5 and cases["LIVENRED"] == 0.5
    assert "LIVEROOF" not in cases and "WX" not in cases and "EQXPE" not in cases


def test_roof_live_combination_contains_the_roof_live_load():
    cases = dict(_combos()["ULS 102 1.2 DL + 1.6 Lr + f LL"].cases)
    assert cases["LIVEROOF"] == 1.6 and cases["LIVERED"] == 0.5


def test_seismic_combinations_point_at_their_directional_combination():
    combos = _combos()
    assert combos["ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3"].combos == [("EQ_COMBO_03", 1.0)]
    assert combos["ULS 107 (1.2 + Ev) DL + f LL + 1.0 RSA3"].combos == [("RSA_COMBO_03", 1.0)]
    assert combos["EQ_COMBO_03"].cases == [("EQXNE", -1.0), ("EQYPE", 0.3)]
    assert combos["RSA_COMBO_05"].cases == [("RSAY", 1.0), ("RSAX", 0.3)]


def test_static_and_spectrum_sets_share_one_number_and_one_format():
    names = list(_combos())
    for number in (107, 110):
        assert sum(n.startswith(f"ULS {number} ") and " EQ" in n for n in names) == 8
        assert sum(n.startswith(f"ULS {number} ") and " RSA" in n for n in names) == 8
    assert not any(" - " in n and not ("(0.9 - Ev)" in n or "(0.6 - " in n or "- 1.0 Em" in n
                                       or n.startswith("DRIFT "))
                   for n in names)


def test_only_strength_combinations_are_design_combinations():
    combos = _combos()
    assert all(c.design == name.startswith("ULS ") for name, c in combos.items())


def test_special_combinations_amplify_the_envelope():
    combos = _combos()
    assert combos["SSLC 100 1.2 DL + f LL + 1.0 Em EQ"].combos == [("ENVE_EQ", 2.8)]
    assert combos["SSLC 102 0.9 DL - 1.0 Em RSA"].combos == [("ENVE_RSA", -2.8)]
    assert combos["ENVE_EQ"].envelope and len(combos["ENVE_EQ"].combos) == 8


def test_combinations_only_use_what_was_created_before_them():
    """ETABS needs a combination to exist before another one can use it."""
    seen = set()
    for combo in lc.build_combinations(PATTERNS, 0.4):
        assert all(name in seen for name, _ in combo.combos), combo.name
        seen.add(combo.name)
    assert len(seen) == 168


def test_drift_combinations_follow_203_with_rho_one():
    """NSCP 208.6.4.1: drift from the 203.3 combinations, E from the drift cases, rho 1.0."""
    combos = _combos()
    up = combos["DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 EQXSD"]
    assert dict(up.cases)["SELFWEIGHT"] == pytest.approx(1.2 + 0.5 * 0.4 * 1.0)
    assert dict(up.cases)["EQXSD"] == 1.0 and not up.design
    assert dict(combos["DRIFT 103 (0.9 - Ev) DL - 1.0 EQXSD"].cases)["EQXSD"] == -1.0
    assert "DRIFT 100 (1.2 + Ev) DL + f LL + 1.0 RSAXD" in combos
    assert not any(n.startswith("DRIFT 101") and "RSA" in n for n in combos)  # no sign on RSA
    wind = combos["WDRIFT 101 1.2 DL + f LL + 0.5 Lr + 1.0 WX"]
    assert dict(wind.cases)["WX"] == 1.0
    assert combos["ENVE_DRIFT"].envelope and combos["ENVE_WDRIFT"].envelope


def test_deflection_combinations_are_unfactored_and_not_designed():
    combos = _combos()
    sustained = combos["DEF 102 1.0 DL + 0.25 LL"]
    assert dict(sustained.cases)["SELFWEIGHT"] == 1.0
    assert all(f == 0.25 for name, f in sustained.cases if name.startswith("LIVE"))
    assert not any(c.design for name, c in combos.items() if name.startswith("DEF"))


def test_service_seismic_factor_is_one_over_1_4():
    combos = _combos()
    combo = combos["SLS 104 (1.0 + 0.714 Ev) DL + 0.714 EQ1"]
    assert combo.combos[0][1] == pytest.approx(1 / 1.4)
    assert dict(combo.cases)["SELFWEIGHT"] == pytest.approx(1.0 + 0.2 / 1.4)


# --------------------------------------------------------------------------
# MATERIALS, SECTIONS, SETTINGS
# --------------------------------------------------------------------------
def test_concrete_grade_matches_the_office_models():
    """C05 in the models: f'c = 34.4828 MPa, E = 27599.35 MPa."""
    grade = ms.concrete_properties(5)
    assert grade["name"] == "C05"
    assert grade["fc"] == pytest.approx(34.482758, abs=1e-5)
    assert grade["E"] == pytest.approx(27599.35, abs=0.01)


def test_rebar_grade_60():
    grade = ms.rebar_properties(60)
    assert grade["name"] == "G60" and grade["fy"] == pytest.approx(413.685, abs=0.001)


def _sections(ranges):
    settings = ms.merge_settings({"sections": ranges, "section_concrete_ksi": [6]})
    return ms.section_definitions(settings)


EMPTY = {"G": {"width": [], "depth": []}, "B": {"width": [], "depth": []},
         "CR": {"size": []}, "C": {"diameter": []}}


def test_beam_sections_keep_width_to_depth_at_least_0_3():
    ranges = {**EMPTY, "G": {"width": [200, 600, 200], "depth": [400, 800, 400]}}
    names = [s["name"] for s in _sections(ranges)]
    # 200X800 is 0.25, below the limit; 600X400 is wider than deep
    assert names == ["G_200X400_C06_G60", "G_400X400_C06_G60", "G_400X800_C06_G60",
                     "G_600X800_C06_G60"]


def test_rectangular_columns_keep_the_short_side_at_least_half_the_long_one():
    ranges = {**EMPTY, "CR": {"size": [400, 1000, 300]}}
    sizes = {(s["width"], s["depth"]) for s in _sections(ranges)}
    assert (400, 700) in sizes and (700, 400) in sizes       # both orientations
    assert (400, 1000) not in sizes                           # 0.4
    assert all(s["kind"] == "column" for s in _sections(ranges))


def test_circular_columns_are_named_by_diameter():
    ranges = {**EMPTY, "C": {"diameter": [600, 700, 100]}}
    assert [s["name"] for s in _sections(ranges)] == ["C_600_C06_G60", "C_700_C06_G60"]


def test_extra_patterns_are_added_once_and_soil_is_not_standard():
    settings = ms.merge_settings({"extra_dead": ["elevator dead", "SIDL"], "extra_live": ["Stage"]})
    patterns = {name: kind for name, kind, _ in ms.load_patterns(settings)}
    assert patterns["ELEVATOR DEAD"] == "Super Dead" and patterns["STAGE"] == "Live"
    assert "SOIL" not in patterns
    assert len(patterns) == len(ms.STANDARD_PATTERNS) + 2


def test_only_self_weight_carries_the_self_weight_multiplier():
    assert [name for name, _, sw in ms.STANDARD_PATTERNS if sw] == ["SELFWEIGHT"]


def test_modes_are_three_per_story_with_a_floor():
    assert ms.number_of_modes(10) == 30
    assert ms.number_of_modes(1) == 12


def test_saved_settings_keep_new_defaults(tmp_path):
    path = tmp_path / "model.setup.json"
    ms.save_settings({"seismic": {"soil_type": "SE"}}, str(path))
    merged = ms.merge_settings(ms.load_settings(str(path)))
    assert merged["seismic"]["soil_type"] == "SE"
    assert merged["seismic"]["r_factor"] == 8.5          # from the defaults
    assert ms.settings_path(r"C:\x\MODEL.EDB") == r"C:\x\MODEL.setup.json"


def test_model_text_is_rewritten_to_per_code_seismic_inputs():
    line = ('  SEISMIC "EQXPE"  "UBC 97"    DIR "X+ECC"  ECC 0.05  TOPSTORY "RD"    '
            'BOTTOMSTORY "Base"   PERIODTYPE "PROGCALC"   CT 0.02  Ca 0.37584  Cv 1.23648  '
            'SOURCETYPE "B"    SOURCEDIST 15  I 1  R 8.5')
    other = '  SEISMIC "OLD"  "UBC 97"    DIR "X"  CT 0.03  Ca 0.4  Cv 0.56  I 1  R 8.5'
    settings = ms.merge_settings({"seismic": {"soil_type": "SE", "distance_km": 8.9}})
    text, changed = ms.per_code_seismic_text("\n".join(["$ LOAD PATTERNS", line, other]), settings)
    assert changed == 1
    lines = text.splitlines()
    assert lines[1].endswith('CT 0.02  SOIL "SE"  Z 0.4  SOURCETYPE "A"    SOURCEDIST 8.9  I 1  R 8.5')
    assert "Ca " not in lines[1] and lines[2] == other       # other patterns untouched


# --------------------------------------------------------------------------
# WITHOUT DIALOGS
# --------------------------------------------------------------------------
def test_planned_setup_touches_no_etabs_and_reports_the_definitions():
    result = ms.setup_model(
        {"seismic": {"soil_type": "SE", "distance_km": 8.9}, "extra_dead": ["ELEVATOR DEAD"]},
        apply=False,
    )
    assert result.log is None and result.path is None
    report = result.report(heading_level=2)
    assert "Planned only" in report
    assert "## Seismic parameters (UBC 97)" in report
    assert "| Ca | 0.3758 |" in report and "| Cv | 1.2365 |" in report
    assert "| ELEVATOR DEAD | Super Dead | 0 |" in report
    assert "| ULS 107 (1.2 + Ev) DL + f LL + 1.0 E | 1.39 | Yes |" in report
    assert report.count("ULS 107") == 1          # one row for the sixteen directions


def test_setup_refuses_invalid_inputs_before_etabs():
    with pytest.raises(ValueError):
        ms.setup_model({"seismic": {"soil_type": "SX"}}, apply=False)
    with pytest.raises(ValueError):
        ms.setup_model({}, target="new")          # no path given


def test_module_runs_as_a_script_without_the_package():
    """`python etabs_api/workflows/model_setup.py` must find its own package."""
    import subprocess

    script = Path(ms.__file__)
    code = (
        "import runpy, sys; sys.argv = ['x']; "
        f"m = runpy.run_path(r'{script}', run_name='not_main'); "
        "print(m['setup_model']({}, apply=False).settings['seismic']['r_factor'])"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=str(script.parent))
    assert done.stdout.strip() == "8.5", done.stderr


# --------------------------------------------------------------------------
# MASS SOURCE
# --------------------------------------------------------------------------
def test_mass_takes_every_dead_load_and_the_non_reducible_live_load():
    settings = ms.merge_settings({"extra_dead": ["ELEVATOR DEAD"], "extra_live": ["STAGE"]})
    mass = dict(ms.mass_source_loads(settings))
    assert mass["SELFWEIGHT"] == 1.0 and mass["SIDL"] == 1.0 and mass["ELEVATOR DEAD"] == 1.0
    assert mass["LIVENRED"] == 1.0
    for name in ("LIVERED", "LIVEROOF", "LIVEMECH", "STAGE", "WX", "EQXPE"):
        assert name not in mass


def test_reducible_live_load_joins_the_mass_at_20_percent_when_chosen():
    settings = ms.merge_settings({"mass": {"include_reducible_live": True},
                                  "extra_reducible_live": ["ELEVATOR LIVERED"]})
    mass = dict(ms.mass_source_loads(settings))
    assert mass["LIVERED"] == 0.2 and mass["ELEVATOR LIVERED"] == 0.2
    assert mass["LIVENRED"] == 1.0


def test_pdelta_always_takes_half_the_reducible_live_load():
    """Same dead and non-reducible live loads as the mass, whatever the mass choice was."""
    for include in (False, True):
        settings = ms.merge_settings({"mass": {"include_reducible_live": include},
                                      "extra_reducible_live": ["ELEVATOR LIVERED"]})
        pdelta = dict(ms.pdelta_loads(settings))
        assert pdelta["LIVERED"] == 0.5 and pdelta["ELEVATOR LIVERED"] == 0.5
        assert pdelta["SELFWEIGHT"] == 1.0 and pdelta["LIVENRED"] == 1.0
        assert "LIVEROOF" not in pdelta and "WX" not in pdelta


def test_old_saved_inputs_cannot_bring_back_the_quarter_live_mass():
    """Inputs saved before LIVENRED became 1.0 still hold 0.25; it must be ignored."""
    settings = ms.merge_settings({"mass": {"non_reducible_live_factor": 0.25}})
    assert dict(ms.mass_source_loads(settings))["LIVENRED"] == 1.0
    assert dict(ms.pdelta_loads(settings))["LIVENRED"] == 1.0


def test_deflection_combinations_come_from_the_pattern_types():
    types = {"SELFWEIGHT": 1, "SIDL": 2, "LIVENRED": 3, "LIVERED": 4, "LIVEROOF": 11, "WX": 6}
    combos = {c.name: dict(c.cases) for c in lc.deflection_combinations(types)}
    assert combos["DEF 100 1.0 DL"] == {"SELFWEIGHT": 1.0, "SIDL": 1.0}
    assert combos["DEF 102 1.0 DL + 0.25 LL"]["LIVERED"] == 0.25
    assert combos["DEF 103 1.0 DL + 1.0 Lr"]["LIVEROOF"] == 1.0
    assert "LIVENRED" not in combos["DEF 103 1.0 DL + 1.0 Lr"]
    assert all("WX" not in c for c in combos.values())


def test_slab_and_wall_sections_have_their_own_concrete():
    settings = ms.merge_settings({
        "section_concrete_ksi": [5], "section_rebar_ksi": 60,
        "slabs": {"thickness": [150, 100], "type": "Membrane", "concrete_ksi": 4},
        "walls": {"thickness": [200], "concrete_ksi": 6}})
    areas = ms.area_section_definitions(settings)
    assert [a["name"] for a in areas] == ["S_100_C04", "S_150_C04", "SW_200_C06_G60"]
    assert [a["material"] for a in areas] == ["C04", "C04", "C06"]
    assert [a["shell"] for a in areas] == ["Membrane", "Membrane", "Shell-Thin"]
    grades = ms.concrete_grades(settings)
    assert 4.0 in grades and 6.0 in grades  # both are always defined


def test_slab_and_wall_concrete_default_to_the_frame_grade():
    settings = ms.merge_settings({"section_concrete_ksi": [5]})
    assert ms.area_concrete_ksi(settings, "slabs") == 5.0
    assert ms.area_concrete_ksi(settings, "walls") == 5.0
    assert ms.area_section_definitions(settings)[0]["name"] == "S_100_C05"


def test_an_older_single_slab_and_wall_concrete_still_applies():
    settings = ms.merge_settings({"section_concrete_ksi": [5], "area_concrete_ksi": 4})
    assert ms.area_concrete_ksi(settings, "slabs") == 4.0
    assert ms.area_concrete_ksi(settings, "walls") == 4.0


def test_the_wall_name_matches_xs_grids():
    from etabs_api.workflows.grid_column_model import wall_section_name

    settings = ms.merge_settings({"section_concrete_ksi": [5], "walls": {"thickness": [250]},
                                  "slabs": {"thickness": []}})
    assert ms.area_section_definitions(settings)[0]["name"] == wall_section_name(250, 5, 60)


@pytest.mark.parametrize("names, elevations, expected", [
    (["Base", "2F", "3F", "RD"], [0, 4500, 8000, 11500], ("Base", "RD")),
    (["Base", "GF", "2F", "RD"], [-1500, 0, 4500, 8000], ("GF", "RD")),   # footing level
    (["Base", "B1", "GF", "RD"], [-6000, -3000, 0, 4000], ("GF", "RD")),  # basement
])
def test_lateral_loads_start_at_the_ground_level(names, elevations, expected):
    assert ms.lateral_story_range(names, elevations) == expected



def test_per_code_text_reload_is_refused_on_a_model_with_frames():
    """Reloading from the .$et text renames every object: never on a tagged model."""
    class Frames:
        @staticmethod
        def Count():
            return 150

    class Model:
        FrameObj = Frames()

    log = ms.SetupLog()
    assert ms.make_seismic_per_code(Model(), "x.EDB", ms.merge_settings({}), log) is False
    assert "150 frames" in log.problems[0]


def test_membrane_slabs_get_a_one_way_counterpart_when_chosen():
    settings = ms.merge_settings({
        "section_concrete_ksi": [4], "walls": {"thickness": []},
        "slabs": {"thickness": [100, 150], "type": "Membrane", "one_way": True}})
    slabs = ms.area_section_definitions(settings)
    assert [s["name"] for s in slabs] == ["S_100_C04", "S_100_C04_1W", "S_150_C04", "S_150_C04_1W"]
    assert [bool(s.get("one_way")) for s in slabs] == [False, True, False, True]
    assert {s["shell"] for s in slabs} == {"Membrane"}


def test_only_membrane_slabs_have_a_one_way_counterpart():
    settings = ms.merge_settings({
        "section_concrete_ksi": [4], "walls": {"thickness": []},
        "slabs": {"thickness": [150], "type": "Shell-Thin", "one_way": True}})
    assert [s["name"] for s in ms.area_section_definitions(settings)] == ["S_150_C04"]
    assert ms.merge_settings({})["slabs"]["one_way"] is False  # off unless chosen


class _SlabTables:
    """Stands in for DatabaseTables with the slab table of a blank model."""

    FIELDS = ("Name", "ModelType", "OneWayLoad", "Thickness")

    def __init__(self):
        self.rows = [["S_150_C04", "Membrane", "No", "150"],
                     ["S_150_C04_1W", "Membrane", "No", "150"],
                     ["Slab1", "Shell-Thin", None, "200"]]
        self.applied = 0

    def GetTableForEditingArray(self, key, *_):
        assert key == ms.SLAB_TABLE
        return (0, self.FIELDS, len(self.rows), tuple(v for row in self.rows for v in row), 0)

    def SetTableForEditingArray(self, key, version, fields, count, values):
        width = len(fields)
        self.rows = [list(values[i:i + width]) for i in range(0, len(values), width)]
        return 0

    def ApplyEditedTables(self, *_):
        self.applied += 1
        return (0, 0, 0, 0, "", 0)


def test_one_way_load_is_set_in_the_slab_table_and_other_rows_are_kept():
    from types import SimpleNamespace

    tables = _SlabTables()
    log = ms.SetupLog()
    ms.set_one_way_slabs(SimpleNamespace(DatabaseTables=tables),
                         {"S_150_C04": False, "S_150_C04_1W": True}, log)
    assert [row[2] for row in tables.rows] == ["No", "Yes", ""]
    assert log.counts == {"one-way slab sections": 1} and not log.problems
    ms.set_one_way_slabs(SimpleNamespace(DatabaseTables=tables),
                         {"S_150_C04": False, "S_150_C04_1W": True}, log)
    assert tables.applied == 1  # nothing to change the second time


def test_a_new_per_code_model_is_opened_again_from_its_edb(tmp_path):
    """ETABS does not analyse a model it still holds from the text file."""
    path = str(tmp_path / "M.EDB")
    settings = ms.merge_settings({})
    lines = [f'  SEISMIC "{name}"  "UBC 97"    DIR "X"  CT 0.03  COEFFTYPE "USER"  CA 0.44  '
             f'CV 0.64  I 1  R 8.5' for name in ms.SEISMIC_DIRECTIONS]
    calls = []

    class File:
        @staticmethod
        def Save(target):
            calls.append(("Save", target))
            with open(str(tmp_path / "M.$et"), "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return 0

        @staticmethod
        def OpenFile(target):
            calls.append(("OpenFile", target))
            return 0

    class Model:
        FrameObj = type("Frames", (), {"Count": staticmethod(lambda: 0)})()

    Model.File = File()
    log = ms.SetupLog()
    assert ms.make_seismic_per_code(Model(), path, settings, log) is True, log.problems
    assert [c[0] for c in calls] == ["Save", "OpenFile", "Save", "OpenFile"]
    assert calls[-1] == ("OpenFile", path)


def test_per_code_patterns_keep_their_coefficients_through_a_table_edit():
    """ETABS resets the source distance to 15 km when the seismic table is written."""
    fields = ["Name", "CoeffOpt", "SoilType", "Z", "Ca", "Cv", "SourceType", "SourceDist"]
    rows = [["EQXPE", "Per Code", "SD", "0.4", "0.4", "0.56", "A", "10"],   # placeholders
            ["EQYPE", "User Defined", "", "", "0.44", "0.768", "", ""]]
    assert ms.keep_per_code_coefficients(fields, rows) == 1
    ca, cv = ubc97.seismic_coefficients(0.4, "SD", "A", 10)
    assert rows[0][1] == "User Defined"
    assert (float(rows[0][4]), float(rows[0][5])) == pytest.approx((ca, cv))
    assert cv == pytest.approx(0.768)                   # 0.64 at the 15 km ETABS falls back to
    assert rows[1] == ["EQYPE", "User Defined", "", "", "0.44", "0.768", "", ""]


def test_the_model_is_opened_again_from_its_edb_after_it_is_saved():
    """Writing the wind table on an analysed model breaks the next save unless
    ETABS opens the saved file again."""
    calls = []

    class File:
        @staticmethod
        def Save(path):
            calls.append(("Save", path))
            return 0

        @staticmethod
        def OpenFile(path):
            calls.append(("OpenFile", path))
            return 0

    class Model:
        pass

    Model.File = File()
    log = ms.SetupLog()
    assert ms.save_and_reopen(Model(), "M.EDB", log) is True
    assert calls == [("Save", "M.EDB"), ("OpenFile", "M.EDB")] and not log.problems


def test_the_wind_table_is_not_written_when_it_already_holds_the_values():
    """Any write of the wind table makes ETABS drop its generated wind patterns."""
    from types import SimpleNamespace

    fields = ("Name", "IsAuto", "WindSpeed", "ExpType", "Angle")
    held = ("WX", "No", "150", "B", "0",
            "WX", "No", None, None, None,              # continuation row of the same pattern
            "WX(1/12)", "Yes", "150", "B", "0",
            "WY", "No", "150", "B", "90")
    tables = SimpleNamespace(GetTableForEditingArray=lambda *_: (0, fields, 4, held, 0))
    model = SimpleNamespace(DatabaseTables=tables)
    wanted = [{"Name": "WX", "IsAuto": "No", "WindSpeed": 150.0, "ExpType": "B", "Angle": 0.0},
              {"Name": "WY", "IsAuto": "No", "WindSpeed": 150.0, "ExpType": "B", "Angle": 90.0}]
    assert ms.table_holds(model, ms.WIND_TABLE, wanted) is True
    wanted[1]["WindSpeed"] = 160.0
    assert ms.table_holds(model, ms.WIND_TABLE, wanted) is False
    assert ms.table_holds(model, ms.WIND_TABLE, [{"Name": "WZ", "IsAuto": "No"}]) is False
    blank = [{"Name": "WX", "Angle": ""}, {"Name": "WY", "Angle": 0.0}]
    assert ms.table_holds(model, ms.WIND_TABLE, blank) is False
    assert ms.table_holds(model, ms.WIND_TABLE, blank, ignore=("Angle",)) is True
