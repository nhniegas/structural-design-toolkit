"""v0.16.0: the fixes found on the RP Antipolo runs (2026-11-04 to 2026-11-14)."""

import os

import pandas as pd

from design.concrete.beams.design import split_too_shallow
from design.concrete.columns import slenderness as cs
from design.criteria.code_config import NSCP, seismic_system, systems_with_r
from etabs_api.workflows.load_combinations import build_combinations
from etabs_api.workflows.model_setup import F1_FULL, F1_OTHER, f1_defaults
from utilities.latex_help import tex_path

PATTERNS = {"SELFWEIGHT": "Dead", "LIVENRED": "Live", "LIVERED": "Reducible Live",
            "EQXPE": "Seismic", "WX": "Wind"}


def _cases(combos, start):
    combo = next(c for c in combos if c.name.startswith(start))
    return dict(combo.cases)


def test_f1_is_per_live_pattern():
    """NSCP 203.3.1: 1.0 for loads above 4.8 kPa (and assembly, garage), 0.5 otherwise."""
    combos = build_combinations(PATTERNS, 0.44, full_live=["LIVENRED"])
    seismic = _cases(combos, "ULS 107")
    assert seismic["LIVENRED"] == NSCP.load_factors.live_companion_heavy == 1.0
    assert seismic["LIVERED"] == NSCP.load_factors.live_companion == 0.5
    wind = _cases(combos, "ULS 105")
    assert wind["LIVENRED"] == 1.0 and wind["LIVERED"] == 0.5
    full = _cases(combos, "ULS 101")            # 1.6 L is not a companion load
    assert full["LIVENRED"] == full["LIVERED"] == 1.6


def test_without_full_live_every_pattern_keeps_05():
    seismic = _cases(build_combinations(PATTERNS, 0.44), "ULS 107")
    assert seismic["LIVENRED"] == seismic["LIVERED"] == 0.5


def test_f1_offered_from_the_model_loads_and_from_before():
    settings = {"extra_live": [], "extra_reducible_live": [], "extra_dead": [],
                "extra_roof_live": [], "full_live": []}
    offered = f1_defaults(settings, {"LIVENRED": 7.2, "LIVERED": 2.4})
    assert offered["LIVENRED"] == F1_FULL and offered["LIVERED"] == F1_OTHER
    settings["full_live"] = ["LIVERED"]
    assert f1_defaults(settings, {})["LIVERED"] == F1_FULL


def test_members_too_shallow_for_two_layers_are_left_out():
    props = pd.DataFrame({"UniqueName": ["B1", "B413"], "Width": [300, 400],
                          "Depth": [600, 100], "cc": [40.0, 40.0], "ds": [10.0, 10.0],
                          "dm": [20.0, 20.0]})
    kept, out = split_too_shallow(props)
    assert list(kept["UniqueName"]) == ["B1"]
    assert out == ["B413 (400 x 100)"]


def test_a_rigid_link_ends_the_unbraced_length():
    """A column tied to a wall by a link (no beam) is supported there: lu is one
    story, not several (RP Antipolo T5: 14.5 m)."""
    section = cs.ColumnSection(width=600.0, depth=600.0, circular=False, fc=28.0)
    columns = {
        "C1": cs.FrameColumn("C1", "p0", "p1", 4000.0, section, (1.0, 0.0)),
        "C2": cs.FrameColumn("C2", "p1", "p2", 4000.0, section, (1.0, 0.0)),
    }
    alone = cs.FrameModel(columns, {})
    linked = cs.FrameModel(columns, {}, links={"L1": ("p1", "w1", (1.0, 0.0)),
                                              "L2": ("p1", "w2", (0.0, 1.0))})
    assert alone.axis_geometry("C2", "3").lu > 7000.0
    geometry = linked.axis_geometry("C2", "3")
    assert geometry.lu == 4000.0
    assert geometry.k <= 1.0


def test_steel_systems_of_table_208_11b():
    scbf = seismic_system("SB3")
    assert (scbf.r, scbf.omega0, scbf.zone4) == (6.0, 2.2, 30.0)
    assert "SB3" in [s.key for s in systems_with_r(6.0)]
    assert seismic_system("C1").r == 8.5                     # the concrete table is unchanged


def test_a_path_without_a_tilde_is_left_as_it_is(tmp_path):
    path = str(tmp_path / "report")
    assert tex_path(path) == os.path.abspath(path)
