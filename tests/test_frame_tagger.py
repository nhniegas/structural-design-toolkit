"""
tests/test_frame_tagger.py
==========================
Checks for etabs_api/frame_tagger.py, the automatic beam and column tagging.

The planning functions take plain joints and frames, so the tests build small
framing plans by hand and need neither ETABS nor Excel.

Plan used by most tests (mm), one level at z = 3000 on columns from z = 0:

    y = 6000   C ---- C ---- C        row 1 of columns, X girder line 1
               |      |      |
    y = 3000   +------+------+        an intermediate beam along X
               |      |      |
    y = 0      C ---- C ---- C        row 2 of columns, X girder line 2
             x=0    5000   10000
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etabs_api import frame_tagger as ft  # noqa: E402

XS = (0.0, 5000.0, 10000.0)
YS = (0.0, 6000.0)


def grid_model(levels=(3000.0,)):
    """Joints, beams and columns of the plan above, repeated on each level."""
    points, beams, columns = {}, [], []
    elevations = (0.0,) + tuple(levels)
    for k, z in enumerate(elevations):
        for i, x in enumerate(XS):
            for j, y in enumerate(YS):
                points[f"P{k}-{i}-{j}"] = (x, y, z)
            points[f"M{k}-{i}"] = (x, 3000.0, z)  # mid-height of the Y girders
    for k in range(1, len(elevations)):
        story = f"{k + 1}F"
        for i in range(len(XS)):
            for j in range(len(YS)):
                columns.append(ft.Frame(f"col{k}{i}{j}", story, f"P{k - 1}-{i}-{j}", f"P{k}-{i}-{j}"))
            # Y girders, split at mid-height where the intermediate beam frames in
            beams.append(ft.Frame(f"gy{k}{i}a", story, f"P{k}-{i}-0", f"M{k}-{i}"))
            beams.append(ft.Frame(f"gy{k}{i}b", story, f"M{k}-{i}", f"P{k}-{i}-1"))
        for i in range(len(XS) - 1):
            for j in range(len(YS)):
                beams.append(ft.Frame(f"gx{k}{i}{j}", story, f"P{k}-{i}-{j}", f"P{k}-{i + 1}-{j}"))
            beams.append(ft.Frame(f"bx{k}{i}", story, f"M{k}-{i}", f"M{k}-{i + 1}"))
    return points, beams, columns


def tags_for(levels=(3000.0,), prefixes=None):
    points, beams, columns = grid_model(levels)
    prefixes = prefixes or {f"{k + 2}F": str(k + 2) for k in range(len(levels))}
    return ft.plan_frame_tags(points, beams, columns, prefixes)


# --------------------------------------------------------------------------
# NAME PARTS
# --------------------------------------------------------------------------
def test_numbered_story_drops_the_f():
    assert ft.default_story_prefix("2F") == "2"
    assert ft.default_story_prefix("12F") == "12"


@pytest.mark.parametrize("story", ["UG", "PD1", "2F - FT", "LOB -TOP"])
def test_other_stories_must_be_asked_for(story):
    assert ft.default_story_prefix(story) is None


def test_letters_skip_i_and_o_and_continue_after_z():
    letters = [ft.position_letter(index) for index in range(27)]
    assert letters[:4] == ["", "A", "B", "C"]
    assert "I" not in letters and "O" not in letters
    assert letters[8:10] == ["H", "J"]
    assert letters[24:27] == ["Z", "AA", "AB"]


# --------------------------------------------------------------------------
# BEAMS
# --------------------------------------------------------------------------
def test_x_girders_are_numbered_top_to_bottom_and_lettered_left_to_right():
    tags = tags_for()
    assert tags["gx101"] == "2GX-1"      # top line (y = 6000), left member
    assert tags["gx111"] == "2GX-1A"
    assert tags["gx100"] == "2GX-2"      # bottom line (y = 0)
    assert tags["gx110"] == "2GX-2A"


def test_y_girders_are_numbered_left_to_right_and_lettered_bottom_to_top():
    tags = tags_for()
    assert (tags["gy10a"], tags["gy10b"]) == ("2GY-1", "2GY-1A")
    assert (tags["gy12a"], tags["gy12b"]) == ("2GY-3", "2GY-3A")


def test_a_beam_with_no_end_on_a_column_is_a_b():
    tags = tags_for()
    assert (tags["bx10"], tags["bx11"]) == ("2BX-1", "2BX-1A")


def test_drawing_direction_does_not_change_the_tag():
    """A member drawn right to left is still read left to right."""
    points, beams, columns = grid_model()
    flipped = [
        ft.Frame(b.name, b.story, b.joint_j, b.joint_i) if b.name == "gx111" else b for b in beams
    ]
    assert ft.plan_frame_tags(points, flipped, columns, {"2F": "2"})["gx111"] == "2GX-1A"


def test_line_continues_through_a_bend_of_45_degrees_or_less():
    points = {"a": (0, 0, 0), "b": (5000, 0, 0), "c": (9000, 3000, 0)}  # bend of 36.9 degrees
    beams = [ft.Frame("one", "2F", "a", "b"), ft.Frame("two", "2F", "b", "c")]
    tags = ft.plan_frame_tags(points, beams, [], {"2F": "2"})
    assert (tags["one"], tags["two"]) == ("2BX-1", "2BX-1A")


def test_a_larger_bend_starts_a_new_line():
    points = {"a": (0, 0, 0), "b": (5000, 0, 0), "c": (9000, -3900, 0), "d": (9000, 3900, 0)}
    # Both branches leave joint b at 44.3 degrees, 88.6 degrees apart: one continues.
    beams = [ft.Frame("one", "2F", "a", "b"), ft.Frame("up", "2F", "b", "d"),
             ft.Frame("down", "2F", "b", "c")]
    tags = ft.plan_frame_tags(points, beams, [], {"2F": "2"})
    numbers = {name: tag.split("-")[1] for name, tag in tags.items()}
    assert len({numbers["up"].rstrip("A"), numbers["down"].rstrip("A")}) == 2
    assert sorted(tag[-1] for tag in tags.values()).count("A") == 1   # only one continues


def test_line_keeps_its_number_across_an_opening():
    points = {"a": (0, 0, 0), "b": (4000, 0, 0), "c": (9000, 0, 0), "d": (13000, 0, 0)}
    beams = [ft.Frame("left", "2F", "a", "b"), ft.Frame("right", "2F", "c", "d")]
    tags = ft.plan_frame_tags(points, beams, [], {"2F": "2"})
    assert (tags["left"], tags["right"]) == ("2BX-1", "2BX-1A")


def test_frames_on_a_story_without_a_prefix_keep_their_name():
    tags = tags_for(levels=(3000.0, 6000.0), prefixes={"2F": "2"})
    assert "gx201" not in tags and "col200" not in tags
    assert tags["gx101"] == "2GX-1"


# --------------------------------------------------------------------------
# COLUMNS
# --------------------------------------------------------------------------
def test_columns_are_numbered_by_row_top_to_bottom_and_lettered_left_to_right():
    tags = tags_for()
    assert [tags[f"col1{i}1"] for i in range(3)] == ["2-C1", "2-C1A", "2-C1B"]
    assert [tags[f"col1{i}0"] for i in range(3)] == ["2-C2", "2-C2A", "2-C2B"]


def test_a_stack_keeps_its_number_and_letter_on_every_level():
    tags = tags_for(levels=(3000.0, 6000.0))
    assert (tags["col111"], tags["col211"]) == ("2-C1A", "3-C1A")
    assert (tags["col120"], tags["col220"]) == ("2-C2B", "3-C2B")


def test_column_missing_on_an_upper_level_does_not_shift_the_others():
    """Numbering is over the combined plan, so a shorter stack leaves a gap above."""
    points, beams, columns = grid_model(levels=(3000.0, 6000.0))
    columns = [c for c in columns if c.name != "col211"]
    tags = ft.plan_frame_tags(points, beams, columns, {"2F": "2", "3F": "3"})
    assert tags["col111"] == "2-C1A"
    assert tags["col221"] == "3-C1B"          # still B, although 3-C1A does not exist


def test_planted_column_is_tagged_pc_with_its_own_numbers():
    """A stack that does not reach a supported joint is a planted column."""
    points, beams, columns = grid_model()
    points["top"] = (2500.0, 6000.0, 6000.0)
    points["seat"] = (2500.0, 6000.0, 3000.0)
    columns.append(ft.Frame("planted", "3F", "seat", "top"))
    supports = {f"P0-{i}-{j}" for i in range(3) for j in range(2)}
    tags = ft.plan_frame_tags(points, beams, columns, {"2F": "2", "3F": "3"}, supports)
    assert tags["planted"] == "3-PC1"
    assert tags["col101"] == "2-C1"          # the supported columns are not renumbered


def test_planted_stack_keeps_its_tag_on_every_level_and_rows_read_like_columns():
    points, beams, columns = grid_model()
    for name, x, y in (("a", 2500.0, 6000.0), ("b", 7500.0, 6000.0), ("c", 2500.0, 0.0)):
        points[f"{name}0"] = (x, y, 3000.0)
        points[f"{name}1"] = (x, y, 6000.0)
        points[f"{name}2"] = (x, y, 9000.0)
        columns.append(ft.Frame(f"{name}-low", "3F", f"{name}0", f"{name}1"))
        columns.append(ft.Frame(f"{name}-high", "4F", f"{name}1", f"{name}2"))
    beams.append(ft.Frame("link", "3F", "a1", "b1"))      # an X girder joining a and b
    supports = {f"P0-{i}-{j}" for i in range(3) for j in range(2)}
    tags = ft.plan_frame_tags(points, beams, columns, {"2F": "2", "3F": "3", "4F": "4"}, supports)
    assert (tags["a-low"], tags["b-low"], tags["c-low"]) == ("3-PC1", "3-PC1A", "3-PC2")
    assert (tags["a-high"], tags["b-high"]) == ("4-PC1", "4-PC1A")


def test_stack_on_a_skipped_level_only_leaves_no_gap_in_the_numbers():
    points, beams, columns = grid_model(levels=(3000.0, 6000.0))
    # the top-left stack exists on the skipped upper level only
    columns = [c for c in columns if c.name != "col101"]
    tags = ft.plan_frame_tags(points, beams, columns, {"2F": "2"})
    assert [tags[f"col1{i}1"] for i in (1, 2)] == ["2-C1", "2-C1A"]


def test_every_new_name_is_unique():
    tags = tags_for(levels=(3000.0, 6000.0))
    assert len(set(tags.values())) == len(tags)


# --------------------------------------------------------------------------
# CLASHES AND THE TAGGED COPY
# --------------------------------------------------------------------------
def test_name_used_by_an_untouched_frame_is_reported():
    tags = {"12": "2GX-1", "13": "2GX-1A"}
    assert ft.conflicting_tags(tags, ["12", "13", "brace"]) == []
    # A name freed by a frame that is itself renamed is not a clash.
    assert ft.conflicting_tags({"12": "13", "13": "2GX-1"}, ["12", "13"]) == []
    assert ft.conflicting_tags({"12": "2GX-1"}, ["12", "2GX-1"]) == ["2GX-1"]


def test_tagged_copy_never_replaces_an_existing_file(tmp_path):
    model = tmp_path / "MODEL.EDB"
    model.write_text("x")
    first = ft.tagged_model_path(str(model))
    assert Path(first).name == "MODEL - TAGGED.EDB"
    Path(first).write_text("x")
    assert Path(ft.tagged_model_path(str(model))).name == "MODEL - TAGGED (2).EDB"


def test_tagged_copy_is_an_edb_even_when_etabs_reports_its_text_file(tmp_path):
    assert Path(ft.tagged_model_path(str(tmp_path / "MODEL.$et"))).name == "MODEL - TAGGED.EDB"


class _FakeFrames:
    """Stands in for SapModel.FrameObj: renaming onto a name in use fails."""

    def __init__(self, names):
        self.names = set(names)

    def ChangeName(self, old, new):
        if old not in self.names or new in self.names:
            return 1
        self.names.remove(old)
        self.names.add(new)
        return 0


class _FakeConnector:
    def __init__(self, names):
        self.sap_model = type("Model", (), {"FrameObj": _FakeFrames(names)})()


def test_names_can_be_swapped_between_two_frames():
    """Renaming goes through temporary names, so A -> B and B -> A both succeed."""
    connector = _FakeConnector(["A", "B", "C"])
    failed = ft.apply_frame_tags(connector, {"A": "B", "B": "A"})
    assert failed == []
    assert connector.sap_model.FrameObj.names == {"A", "B", "C"}


def test_failed_rename_gets_its_old_name_back():
    connector = _FakeConnector(["A", "C"])
    failed = ft.apply_frame_tags(connector, {"A": "C"})     # C belongs to an untouched frame
    assert failed == ["A"]
    assert connector.sap_model.FrameObj.names == {"A", "C"}
