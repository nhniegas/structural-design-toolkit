"""Target demand / capacity ratios, and the depth of a beam that carries others."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_beam_designer_aci318 as beam_tests  # noqa: E402

from design import beam_carriers as bc  # noqa: E402
from design import dcr_targets as dt  # noqa: E402

beam = beam_tests.beam


# ----------------------------------------------------------------- the targets
def test_without_targets_every_check_is_at_the_code_limit():
    assert dt.limit(dt.GIRDER, dt.FLEXURE) == 1.0
    assert dt.limit(dt.COLUMN, dt.STRONG_COLUMN) == 1.2
    assert not dt.active().changed and dt.note((dt.GIRDER, dt.COLUMN)) == ""


def test_targets_are_by_member_type_and_check_and_only_inside_the_block():
    targets = dt.Targets()
    targets.set(dt.GIRDER, dt.FLEXURE, 0.85)
    targets.set(dt.COLUMN, dt.STRONG_COLUMN, 1.3)
    with dt.use(targets):
        assert dt.limit(dt.GIRDER, dt.FLEXURE) == 0.85
        assert dt.limit(dt.BEAM, dt.FLEXURE) == 1.0          # another type keeps the code limit
        assert dt.limit(dt.GIRDER, dt.SHEAR) == 1.0          # and another check
        assert dt.limit(dt.COLUMN, dt.STRONG_COLUMN) == 1.3
        assert "Girders: flexure 0.85" in dt.note((dt.GIRDER,))
    assert dt.limit(dt.GIRDER, dt.FLEXURE) == 1.0


@pytest.mark.parametrize("member, check, ratio", [
    (dt.GIRDER, dt.FLEXURE, 1.05),        # above the code limit
    (dt.BEAM, dt.SHEAR, 0.1),             # a typing slip
    (dt.COLUMN, dt.STRONG_COLUMN, 1.0),   # below the 1.2 of the code
])
def test_a_target_looser_than_the_code_is_refused(member, check, ratio):
    with pytest.raises(ValueError):
        dt.Targets().set(member, check, ratio)


def test_a_target_at_the_code_limit_is_not_kept_and_targets_are_saved_and_read():
    targets = dt.Targets()
    targets.set(dt.BEAM, dt.DEFLECTION, 0.9)
    targets.set(dt.BEAM, dt.FLEXURE, 1.0)
    assert targets.to_saved() == {"beam.deflection": 0.9}
    again = dt.Targets.from_saved({"beam.deflection": 0.9, "nonsense.check": 0.5,
                                   "column.flexure": "x"})
    assert again.values == {(dt.BEAM, dt.DEFLECTION): 0.9}


def test_a_girder_and_a_gravity_beam_are_told_apart_by_their_supports():
    assert dt.beam_type("Supported Both Ends") == dt.GIRDER
    assert dt.beam_type(beam.GRAVITY_BEAM_STATUS) == dt.BEAM


# ----------------------------------------------------------------- beams designed to a target
def bottom_bars(results) -> int:
    row = results[results["Face"] == "BOTTOM"].iloc[0]
    return int(sum(row[f"n_mid_L{k}"] for k in (1, 2, 3)))


def test_a_flexure_target_gives_more_bars_and_the_real_moment_is_reported():
    props, forces = beam_tests._mock_beam_properties(), beam_tests._mock_force_table()
    code = beam.execute_beam_design(props, forces, False, "GRAV")
    targets = dt.Targets()
    targets.set(dt.GIRDER, dt.FLEXURE, 0.6)
    with dt.use(targets):
        tight = beam.execute_beam_design(props, forces, False, "GRAV")
    assert bottom_bars(tight) > bottom_bars(code)
    assert tight["Mu_mid"].max() == pytest.approx(code["Mu_mid"].max())     # not the amplified one
    assert tight["Target_DCR_Flexure"].iloc[0] == 0.6
    assert "Target_DCR_Flexure" not in code.columns                         # only when set


def test_a_target_of_another_member_type_changes_nothing():
    props, forces = beam_tests._mock_beam_properties(), beam_tests._mock_force_table()
    code = beam.execute_beam_design(props, forces, False, "GRAV")
    targets = dt.Targets()
    targets.set(dt.BEAM, dt.FLEXURE, 0.6)            # the mock is a girder
    with dt.use(targets):
        other = beam.execute_beam_design(props, forces, False, "GRAV")
    assert bottom_bars(other) == bottom_bars(code)


def test_a_shear_target_gives_more_stirrup_steel():
    props, forces = beam_tests._mock_beam_properties(), beam_tests._mock_force_table(wu=110.0)
    code = beam.execute_beam_design(props, forces, False, "GRAV")
    targets = dt.Targets()
    targets.set(dt.GIRDER, dt.SHEAR, 0.6)
    with dt.use(targets):
        tight = beam.execute_beam_design(props, forces, False, "GRAV")
    def steel(results):   # stirrup legs per mm of beam: more legs, or closer stirrups
        return results["Stirrup_Legs"].iloc[0] / results["Spacing_2H"].iloc[0]

    assert steel(tight) > steel(code)
    assert tight["Vu_left"].max() == pytest.approx(code["Vu_left"].max())


# ----------------------------------------------------------------- carriers
def framing(split: bool = False, with_points: bool = True):
    """Columns at (0,0) and (6000,0); a girder between them; a beam along Y that
    lands on the girder at mid span. ``split``: ETABS has the girder in two pieces."""
    points = pd.DataFrame({
        "UniqueName": ["a0", "a", "b0", "b", "m", "n"],
        "X": [0, 0, 6000, 6000, 3000, 3000], "Y": [0, 0, 0, 0, 0, 5000],
        "Z": [0, 3000, 0, 3000, 3000, 3000]})
    girders = ([("G1", "a", "m"), ("G1A", "m", "b")] if split else [("G1", "a", "b")])
    rows = [("C1", "Column", "a0", "a"), ("C2", "Column", "b0", "b")] \
        + [(name, "Beam", i, j) for name, i, j in girders] + [("B1", "Beam", "m", "n")]
    connectivity = pd.DataFrame(rows, columns=["UniqueName", "DesignType", "UniquePtI",
                                               "UniquePtJ"])
    return connectivity, (points if with_points else None)


@pytest.mark.parametrize("split, carriers", [(False, {"G1"}), (True, {"G1", "G1A"})])
def test_the_girder_under_a_beam_end_is_its_carrier_whole_or_in_two_pieces(split, carriers):
    carried = bc.carried_beams(*framing(split))
    assert set(carried) == carriers
    assert all(beams == ["B1"] for beams in carried.values())
    assert "B1" not in carried                      # the carried beam carries nothing


def test_beams_that_continue_each_other_do_not_carry_each_other():
    points = pd.DataFrame({"UniqueName": ["p", "q", "r"], "X": [0, 3000, 6000],
                           "Y": [0, 0, 0], "Z": [3000] * 3})
    connectivity = pd.DataFrame({"UniqueName": ["B1", "B2"], "DesignType": ["Beam"] * 2,
                                 "UniquePtI": ["p", "q"], "UniquePtJ": ["q", "r"]})
    assert bc.carried_beams(connectivity, points) == {}


def test_without_joint_coordinates_no_carrier_is_claimed():
    assert bc.carried_beams(*framing(with_points=False)) == {}


def results(girder_depth: float, beam_depth: float) -> pd.DataFrame:
    rows = []
    for name, depth in (("G1", girder_depth), ("B1", beam_depth)):
        for face in ("TOP", "BOTTOM"):
            rows.append({"UniqueName": name, "Face": face, "Depth": depth, "Design_Status": "OK"})
    return pd.DataFrame(rows)


def test_a_girder_shallower_than_the_beam_it_carries_fails():
    out = bc.add_carrier_depth_check(results(500.0, 600.0), *framing())
    girder = out[out["UniqueName"] == "G1"].iloc[0]
    assert girder["Carried_Beam_Depth"] == 600.0
    assert girder["Carrier_Depth_Check"] == "FAIL: 500 deep, carries B1 (600 deep)"
    assert girder["Design_Status"] == bc.CARRIER_FAILED
    carried = out[out["UniqueName"] == "B1"].iloc[0]
    assert carried["Carrier_Depth_Check"] == bc.NOT_A_CARRIER and carried["Design_Status"] == "OK"
    assert out.columns[-1] == "Design_Status"


@pytest.mark.parametrize("girder_depth", [600.0, 700.0])
def test_a_girder_as_deep_as_the_beam_or_deeper_passes(girder_depth):
    out = bc.add_carrier_depth_check(results(girder_depth, 600.0), *framing())
    girder = out[out["UniqueName"] == "G1"].iloc[0]
    assert girder["Carrier_Depth_Check"] == "PASS" and girder["Design_Status"] == "OK"


def test_the_loop_makes_a_shallow_carrier_deeper_and_does_not_shrink_one_below_its_beams():
    from etabs_api.workflows import design_loop as dl

    assert any(key in bc.CARRIER_FAILED for key in dl.DEPTH_FAILURES)


# ----------------------------------------------------------------- asking
def test_the_targets_dialogs_keep_the_code_limits_unless_the_engineer_sets_some(monkeypatch,
                                                                                tmp_path):
    from design import concrete_workflow as cw
    from etabs_api.workflows import model_inputs as mi
    from utilities import _gui_helpers as gui

    model_path = str(tmp_path / "m.EDB")
    shown = []

    def first(title, prompt, options, default_index=0):
        shown.append(list(options))
        return options[default_index]

    monkeypatch.setattr(gui, "select_option", first)
    targets = cw.ask_dcr_targets(model_path, "t", (dt.GIRDER, dt.BEAM))
    assert not targets.changed and shown[0] == [cw.CODE_LIMITS, cw.SET_TARGETS]

    monkeypatch.setattr(gui, "select_option", lambda *a, **k: cw.SET_TARGETS)
    typed = {dt.GIRDER: {"Flexure": "0.85"}, dt.BEAM: {"Deflection": "0.9"}}

    def enter(title, prompt, labels, defaults):
        member = dt.GIRDER if prompt.startswith("Girders") else dt.BEAM
        return {label: next((value for key, value in typed[member].items()
                             if label.startswith(key)), defaults[label]) for label in labels}

    monkeypatch.setattr(gui, "enter_values", enter)
    targets = cw.ask_dcr_targets(model_path, "t", (dt.GIRDER, dt.BEAM))
    assert targets.values == {(dt.GIRDER, dt.FLEXURE): 0.85, (dt.BEAM, dt.DEFLECTION): 0.9}
    assert mi.load(model_path)["dcr"] == {"girder.flexure": 0.85, "beam.deflection": 0.9}

    # the next run offers what was saved, and it is the choice made by pressing Enter
    monkeypatch.setattr(gui, "select_option", first)
    again = cw.ask_dcr_targets(model_path, "t", (dt.GIRDER, dt.BEAM))
    assert again.values == targets.values and len(shown[-1]) == 3


def test_the_carrier_depth_question_defaults_to_no(monkeypatch):
    from design import concrete_workflow as cw
    from utilities import _gui_helpers as gui

    monkeypatch.setattr(gui, "select_option",
                        lambda title, prompt, options, default_index=0: options[default_index])
    assert cw.ask_carrier_depth("t") is False
    assert cw.ask_carrier_depth("t", {"carrier_depth": True}) is True


# ----------------------------------------------------------------- what holds a beam end
def test_an_end_on_an_unsplit_girder_is_carried_not_free():
    held = bc.end_conditions(*framing())                 # B1 from the girder's mid span
    assert held[("B1", "m")] == bc.BEAM_END
    assert held[("B1", "n")] == bc.FREE_END              # nothing at its far end
    assert held[("G1", "a")] == held[("G1", "b")] == bc.COLUMN_END


def chain(tip_held: bool):
    """A column at a; beam pieces a-b and b-c in line; c free, or on a girder."""
    points = pd.DataFrame({"UniqueName": ["a0", "a", "b", "c", "g1", "g2"],
                           "X": [0, 0, 2000, 4000, 4000, 4000], "Y": [0, 0, 0, 0, -3000, 3000],
                           "Z": [0, 3000, 3000, 3000, 3000, 3000]})
    rows = [("C1", "Column", "a0", "a"), ("R", "Beam", "a", "b"), ("T", "Beam", "b", "c")]
    if tip_held:
        rows.append(("G", "Beam", "g1", "g2"))            # a girder across the tip, not split
    return pd.DataFrame(rows, columns=["UniqueName", "DesignType", "UniquePtI", "UniquePtJ"]), points


def test_a_cantilever_in_two_pieces_is_still_a_cantilever():
    connectivity, points = chain(tip_held=False)
    held = bc.end_conditions(connectivity, points)
    assert held[("T", "c")] == bc.FREE_END and held[("R", "b")] == bc.FREE_END
    status = beam.identify_cantilever_beams(None, connectivity, points).set_index("UniqueName")
    assert status.loc["R", "SupportStatus"] == "Cantilever (Free at PtJ)"


def test_a_beam_from_a_column_to_a_girder_is_not_a_cantilever():
    connectivity, points = chain(tip_held=True)
    held = bc.end_conditions(connectivity, points)
    assert held[("T", "c")] == bc.BEAM_END and held[("R", "b")] == bc.BEAM_END
    status = beam.identify_cantilever_beams(None, connectivity, points).set_index("UniqueName")
    assert status.loc["R", "SupportStatus"] == "Supported Both Ends"
    assert status.loc["T", "SupportStatus"] == beam.GRAVITY_BEAM_STATUS


def test_without_coordinates_the_supports_are_classified_as_before():
    connectivity, _ = chain(tip_held=True)
    status = beam.identify_cantilever_beams(None, connectivity).set_index("UniqueName")
    assert status.loc["R", "SupportStatus"] == "Cantilever (Free at PtJ)"


# ----------------------------------------------------------------- bar spacing rule and runaway growth
def test_without_the_office_rule_a_wide_beam_gets_fewer_bars_for_spacing():
    """A 1000 mm wide beam: 150 mm clear needs 6 bars of 25 on a face; crack
    control (about 250 mm centre to centre) needs 5."""
    from design.code_config import CODE, override

    office = beam.BeamFlexureDesign(1000, 800, 28, 414, 414, 25, 10, 40)
    code_only = beam.BeamFlexureDesign(
        1000, 800, 28, 414, 414, 25, 10, 40,
        code=override(CODE, beam_detailing__limit_clear_spacing=False))
    assert office.get_min_bars_for_150mm_spacing() == 6
    assert code_only.get_min_bars_for_150mm_spacing() < 6
    assert code_only.get_min_bars_for_150mm_spacing() >= 2


def test_the_office_rule_stays_the_default_of_the_design():
    props, forces = beam_tests._mock_beam_properties(), beam_tests._mock_force_table(wu=5.0)
    props["Width"] = 900.0
    tables = {"FRAME DATA": None}
    default = beam.execute_beam_design(props, forces, False, "GRAV")
    from design.code_config import CODE, override

    code_only = beam.execute_beam_design(
        props, forces, False, "GRAV",
        code=override(CODE, beam_detailing__limit_clear_spacing=False))
    count = lambda r: int(r[r["Face"] == "TOP"].iloc[0]["n_mid_L1"])
    assert count(code_only) < count(default)
    assert tables


def test_the_loop_stops_enlarging_a_beam_that_keeps_failing_in_shear():
    from etabs_api.workflows import design_loop as dl
    from etabs_api.workflows.sections import Section

    small, big = Section("G", 500, 800, "C06", "G60"), Section("G", 600, 800, "C06", "G60")
    sections = {"2GX-10": small, "2GX-10A": small, "2GX-3": small}
    counts: dict[str, int] = {}
    for _ in range(dl.MAX_SHEAR_GROWTHS):
        actions = {"2GX-10": (big, "grow (shear spacing)"),
                   "2GX-10A": (big, "same beam line as 2GX-10"),
                   "2GX-3": (big, "grow (deflection)")}
        assert dl.stop_runaway_growth(actions, sections, counts) == []
        assert actions["2GX-10"][0] == big
    actions = {"2GX-10": (big, "grow (shear spacing)"),
               "2GX-10A": (big, "same beam line as 2GX-10"),
               "2GX-3": (big, "grow (deflection)")}
    assert dl.stop_runaway_growth(actions, sections, counts) == ["2GX-10"]
    assert actions["2GX-10"][0] == small and "does not help" in actions["2GX-10"][1]
    assert "2GX-10A" not in actions                 # it only followed the stopped beam
    assert actions["2GX-3"][0] == big               # growth for another reason goes on
