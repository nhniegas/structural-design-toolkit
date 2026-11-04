"""T- and L-beams in the deflection check: flange width (NSCP Table 406.3.2.1), section
properties against hand values, the slab found beside the beams, and the option."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_beam_deflection as base  # noqa: E402
import test_deflection_stages as stages  # noqa: E402

from design.concrete.beams import flanges as fl  # noqa: E402

bd = base.bd


@pytest.mark.parametrize("flange, ln, expected", [
    (fl.Flange(150, 150, 2000, 2000), 6000, (750, 750)),       # ln/8
    (fl.Flange(150, 150, 2000, 2000), 60000, (1000, 1000)),    # sw/2
    (fl.Flange(100, 100, 9000, 9000), 60000, (800, 800)),      # 8h
    (fl.Flange(150, 0, 2000), 6000, (500, 0)),                 # one side: ln/12
    (fl.Flange(150, 0, 9000), 60000, (900, 0)),                # one side: 6h
    (fl.Flange(0, 0), 6000, (0, 0)),
])
def test_the_overhangs_are_the_least_of_the_limits_of_the_code(flange, ln, expected):
    assert flange.overhangs(ln) == pytest.approx(expected)


def test_gross_properties_of_a_t_section_match_a_hand_calculation():
    # bw 300, h 600, hf 150, 600 mm overhang each side: bf 1500
    section = fl.t_section(300.0, 600.0, fl.Flange(150, 150, 1200, 1200), 4800.0)
    assert section.width == pytest.approx(1500.0)
    assert section.kind() == "T-beam"
    assert section.centroid == pytest.approx(187.5)
    hand = (300 * 600**3 / 12 + 180000 * 112.5**2 + 1200 * 150**3 / 12 + 180000 * 112.5**2)
    assert section.ig == pytest.approx(hand)
    assert "bf 1,500 mm, hf 150 mm" in section.describe()


def test_a_rectangle_gives_the_rectangular_values():
    section = fl.t_section(300.0, 600.0, None, 6000.0)
    assert section.ig == pytest.approx(300 * 600**3 / 12)
    assert section.centroid == pytest.approx(300.0) and section.kind() == "Rectangular"
    assert fl.cracked_inertia_flanged([(300.0, 600.0)], 540.0, 1500.0, 60.0, 600.0, 8.0) == \
        pytest.approx(bd.cracked_inertia(300.0, 540.0, 1500.0, 60.0, 600.0, 8.0), rel=1e-9)


def test_icr_with_the_neutral_axis_in_the_flange_is_that_of_a_rectangle_bf_wide():
    section = fl.t_section(300.0, 600.0, fl.Flange(150, 150, 1200, 1200), 4800.0)
    area = 3 * math.pi * 20**2 / 4
    flanged = fl.cracked_inertia_flanged(section.parts, 540.0, area, 60.0, 0.0, 8.0)
    assert flanged == pytest.approx(bd.cracked_inertia(1500.0, 540.0, area, 60.0, 0.0, 8.0),
                                    rel=1e-9)


def test_icr_with_the_neutral_axis_in_the_web():
    # a thin flange and much steel: the compression zone runs into the web
    parts = [(300.0, 600.0), (300.0, 50.0), (300.0, 50.0)]
    area, d, n = 6000.0, 540.0, 8.0
    c = fl.cracked_inertia_flanged(parts, d, area, 60.0, 0.0, n)
    # by hand: 900 x 50 flange block plus the web below it, solved for c
    a, b, cc = 150.0, 600 * 50 + n * area, -(600 * 50 * 25 + n * area * d)
    # first moments about the top: 300 c^2/2 + 600*50*(c - 25) = n As (d - c)
    root = (-b + math.sqrt(b * b - 4 * a * cc)) / (2 * a)
    assert root > 50.0
    hand = (300 * root**3 / 3 + 600 * 50**3 / 12 + 600 * 50 * (root - 25) ** 2
            + n * area * (d - root) ** 2)
    assert c == pytest.approx(hand, rel=1e-6)


def test_the_slab_beside_the_beams_is_found_with_the_clear_distance_to_the_next_web():
    points = {"1": (0, 0, 3000), "2": (6000, 0, 3000), "3": (0, 3000, 3000),
              "4": (6000, 3000, 3000), "5": (0, 0, 0), "6": (0, 3000, 0)}
    beams = [("B1", "1", "2"), ("B2", "3", "4"), ("C1", "5", "1")]
    widths = {"B1": 300.0, "B2": 300.0}
    slab = fl.FloorArea([(0, -2000), (6000, -2000), (6000, 3000), (0, 3000)], 3000, 150.0)
    found = fl.slab_flanges(points, beams, widths, [slab])
    assert "C1" not in found                                    # a column is not a beam
    b1, b2 = found["B1"], found["B2"]
    assert (b1.hf_1, b1.hf_2) == (150.0, 150.0) and b1.sides == 2
    assert b1.sw_1 == pytest.approx(2700.0) and math.isinf(b1.sw_2)
    assert (b2.hf_1, b2.hf_2) == (0.0, 150.0) and b2.sides == 1   # an edge beam: L-beam
    assert b2.sw_2 == pytest.approx(2700.0)

    hole = fl.FloorArea([(0, -2000), (6000, -2000), (6000, -10), (0, -10)], 3000, opening=True)
    with_hole = fl.slab_flanges(points, beams, widths, [slab, hole])
    assert with_hole["B1"].hf_2 == 0.0 and with_hole["B1"].hf_1 == 150.0

    rows = fl.flanges_to_rows(found)
    import pandas as pd

    assert fl.flanges_from_table(pd.DataFrame(rows)) == found


def test_the_deflection_of_a_t_beam_is_smaller_and_no_flange_changes_nothing():
    results, service = stages.results_and_service()
    plain = bd.add_deflection_columns(results, service)
    assert "Defl_section" not in plain.columns
    none = bd.add_deflection_columns(results, service, flanges={})
    assert none.iloc[0]["Defl_section"] == "Rectangular"
    for column in ("Defl_Ie_mid", "Defl_live_mm", "Defl_long_mm", "Defl_ratio"):
        assert none.iloc[0][column] == plain.iloc[0][column]
    flanged = bd.add_deflection_columns(results, service,
                                        flanges={"B1": fl.Flange(150, 150, 2700, 2700)})
    row = flanged.iloc[0]
    assert row["Defl_section"].startswith("T-beam: bf 1,800 mm")   # 300 + 2 x 6000/8
    assert row["Defl_Ie_mid"] > plain.iloc[0]["Defl_Ie_mid"]
    assert row["Defl_live_mm"] < plain.iloc[0]["Defl_live_mm"]
    assert row["Defl_ratio"] < plain.iloc[0]["Defl_ratio"]


def test_the_question_defaults_to_the_rectangle_and_is_remembered(monkeypatch, tmp_path):
    from design.concrete.beams import options as beam_options
    from utilities import _gui_helpers as gui

    monkeypatch.setattr(beam_options, "_deflection_settings_path", lambda: str(tmp_path / "d.json"))
    seen = {}

    def first(title, prompt, options, default_index=0):
        seen["default"] = options[default_index]
        return options[default_index]

    monkeypatch.setattr(gui, "select_option", first)
    assert beam_options.ask_deflection_section() is False
    assert seen["default"] == beam_options.SECTION_RECTANGULAR
    monkeypatch.setattr(gui, "select_option", lambda *a, **k: beam_options.SECTION_FLANGED)
    assert beam_options.ask_deflection_section() is True
    monkeypatch.setattr(gui, "select_option", first)
    assert beam_options.ask_deflection_section() is True
    assert seen["default"] == beam_options.SECTION_FLANGED
    monkeypatch.setattr(gui, "select_option", lambda *a, **k: None)
    assert beam_options.ask_deflection_section() is None
