"""
tests/test_wall_piers.py
========================
The walls as legs and walls (etabs_api/workflows/wall_piers.py): planar, L and
core walls, labels kept from story to story. No ETABS.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api.workflows import wall_piers as wp  # noqa: E402
from etabs_api.workflows.wall_piers import Panel, legs_of_story, plan_walls  # noqa: E402

STORIES = ["GF", "2F", "3F"]


def panels_for(story, segments, start=0):
    return [Panel(f"{story}-{start + i}", story, *seg, thickness=250.0)
            for i, seg in enumerate(segments)]


def test_panels_in_one_plane_touching_make_one_leg():
    legs = legs_of_story(panels_for("GF", [(0, 0, 3000, 0), (3000, 0, 6000, 0),
                                           (9000, 0, 12000, 0)]))
    lengths = sorted(round(leg.length) for leg in legs)
    assert lengths == [3000, 6000]          # the third panel is apart: its own leg


def test_an_l_wall_and_a_planar_wall_are_labelled_on_every_story():
    panels = []
    for story in STORIES:
        panels += panels_for(story, [(0, 0, 6000, 0), (0, 0, 0, 5000),
                                     (18000, 5000, 18000, 10000)],
                             start=10 * STORIES.index(story))
    walls = plan_walls(panels, STORIES)
    assert len(walls) == 2
    planar = next(w for w in walls if len(w.legs["GF"]) == 1)
    corner = next(w for w in walls if len(w.legs["GF"]) == 2)
    assert corner.shape == "L or T" and planar.shape == "planar"
    # one label per leg, the same on every story
    for wall in walls:
        for story in STORIES:
            labels = sorted(leg.label for leg in wall.legs[story])
            assert labels == sorted(leg.label for leg in wall.legs["GF"])
    # the planar wall is higher in plan (y 5 to 10 m): it is wall 1
    assert planar.number == 1 and [leg.label for leg in planar.legs["2F"]] == ["W1A"]
    assert sorted(leg.label for leg in corner.legs["3F"]) == ["W2A", "W2B"]


def test_a_core_is_one_wall_of_four_legs():
    box = [(0, 0, 4000, 0), (4000, 0, 4000, 3000), (0, 3000, 4000, 3000), (0, 0, 0, 3000)]
    walls = plan_walls(panels_for("GF", box), ["GF"])
    assert len(walls) == 1 and len(walls[0].legs["GF"]) == 4 and walls[0].shape == "core"


def test_a_beam_joining_two_separate_walls_is_a_coupling_beam():
    from etabs_api.workflows.wall_piers import BeamLine, coupling_beams

    panels = panels_for("GF", [(0, 0, 3000, 0), (4500, 0, 8000, 0), (0, 0, 0, 3000)])
    walls = plan_walls(panels, ["GF"])
    beams = [BeamLine("CB1", "GF", 2000, 0, 6000, 0),     # runs into both walls
             BeamLine("B2", "GF", 0, 3000, 0, 6000),      # one end only on a wall
             BeamLine("B3", "GF", 1000, 0, 2500, 0)]      # both ends on the same wall
    found = coupling_beams(beams, walls)
    assert [beam.name for beam, *_ in found] == ["CB1"]
    assert found[0][3] == pytest.approx(1500.0, abs=15.0)   # 3000 to 4500 between the walls


def test_a_model_with_no_walls_has_no_panels():
    import pandas as pd

    class Stories:
        def GetStories(self):
            return (0, ["Base", "2F"], [0.0, 3000.0])

    class Model:
        Story = Stories()

    class Connector:
        sap_model = Model()

        def _read_database_table(self, name):
            if name == "Wall Object Connectivity":     # ETABS has no such table then
                raise RuntimeError("return code 1")
            return pd.DataFrame({"UniqueName": ["1"], "X": [0.0], "Y": [0.0], "Z": [0.0]})

    panels, stories = wp.read_wall_panels(Connector())
    assert panels == [] and stories == ["2F"]
