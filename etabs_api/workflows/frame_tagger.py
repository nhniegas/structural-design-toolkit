"""Automatic unique-name tagging of beams and columns in an ETABS model.

Beam names have the form ``<level><type>-<number><letter>``, for example
``2GX-10B``. Column names have the form ``<level>-<type><number><letter>``, for
example ``3-C5C`` or ``PD2-PC2A``:

* level   short form of the story (``2F`` -> ``2``; other stories are asked for)
* type    ``GX``/``GY`` girders, ``BX``/``BY`` beams, ``C`` columns,
          ``PC`` planted columns (a stack that does not reach a support)
* number  the line (beams) or the row (columns)
* letter  position along the line: none, A, B, ... (I and O are not used)

Reading order in plan: X lines are numbered top to bottom with letters left to
right; Y lines are numbered left to right with letters bottom to top. Columns
are numbered once over the combined plan of all levels, so a column keeps its
number and letter on every level.

Run it on the model that is open in ETABS, from the workbook button or with:

    python main.py tag
    python etabs_api/workflows/frame_tagger.py

The planning functions work on plain data and need neither ETABS nor Excel.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass

if __package__ in (None, ""):
    # Run as a script: make the project folder (two levels up) importable.
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etabs_api.core.helpers import as_list, return_code  # noqa: E402

MAX_BEND_DEGREES = 45.0  # a line continues through a joint up to this bend
GAP_OFFSET = 250.0  # mm; sideways tolerance when a line continues across a gap
ROW_OFFSET = 150.0  # mm; a column this close to an X girder line is in its row
AXIS_LIMIT_DEGREES = 45.0  # within this angle of global X a beam runs along X
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O are left out
TAGGED_SUFFIX = " - TAGGED"
_NUMBERED_STORY = re.compile(r"^\s*(\d+)\s*F\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Frame:
    """A beam or column: its unique name, story and the two end joints."""

    name: str
    story: str
    joint_i: str
    joint_j: str


def default_story_prefix(story: str) -> str | None:
    """Prefix of a numbered story (``2F`` -> ``2``); None when it must be asked for."""
    match = _NUMBERED_STORY.match(str(story))
    return match.group(1) if match else None


def position_letter(index: int) -> str:
    """Letter of the ``index``-th member of a line: '', A ... Z, AA, AB, ..."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, len(LETTERS))
        letters = LETTERS[remainder] + letters
    return letters


@dataclass
class _Member:
    """A beam laid out in plan, pointing in its reading direction."""

    frame: Frame
    start: str
    end: str
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    direction: tuple[float, float]
    axis: str  # "X" or "Y"
    is_girder: bool


def _bend(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Angle in degrees between two unit directions."""
    return math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))))


def _distance_to_segment(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Plan distance from a point to the segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared < 1e-9:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_squared
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def _layout_beam(
    frame: Frame, points: dict[str, tuple[float, float, float]], column_joints: set[str]
) -> _Member | None:
    """Orient a beam left to right (X) or bottom to top (Y); None for a vertical one."""
    xi, yi, _ = points[frame.joint_i]
    xj, yj, _ = points[frame.joint_j]
    length = math.hypot(xj - xi, yj - yi)
    if length < 1e-6:
        return None
    angle = math.degrees(math.atan2(yj - yi, xj - xi)) % 180.0
    axis = "X" if min(angle, 180.0 - angle) <= AXIS_LIMIT_DEGREES else "Y"
    forward = (xj, yj) >= (xi, yi) if axis == "X" else (yj, xj) >= (yi, xi)
    start, end = (frame.joint_i, frame.joint_j) if forward else (frame.joint_j, frame.joint_i)
    sx, sy, _ = points[start]
    ex, ey, _ = points[end]
    return _Member(
        frame, start, end, (sx, sy), (ex, ey),
        ((ex - sx) / length, (ey - sy) / length), axis,
        frame.joint_i in column_joints or frame.joint_j in column_joints,
    )


def _chain_lines(members: list[_Member]) -> list[list[_Member]]:
    """Join members into lines, in reading order, and return the lines in order.

    Members that share a joint continue one line when the bend is at most
    ``MAX_BEND_DEGREES``; the straightest pair is joined first. A line then
    continues across a gap onto the nearest line straight ahead of it.
    """
    if not members:
        return []
    following: dict[int, int] = {}
    preceding: dict[int, int] = {}

    def join(candidates: list[tuple[float, int, int]]) -> None:
        for _, first, second in sorted(candidates):
            if first not in following and second not in preceding:
                following[first] = second
                preceding[second] = first

    starts_at: dict[str, list[int]] = {}
    for index, member in enumerate(members):
        starts_at.setdefault(member.start, []).append(index)
    join([
        (bend, first, second)
        for first, member in enumerate(members)
        for second in starts_at.get(member.end, [])
        if second != first
        and (bend := _bend(member.direction, members[second].direction)) <= MAX_BEND_DEGREES
    ])

    # Across a gap: from the end of a line to the head of another one ahead of it.
    tails = [index for index in range(len(members)) if index not in following]
    heads = [index for index in range(len(members)) if index not in preceding]
    gaps = []
    for tail in tails:
        last = members[tail]
        for head in heads:
            if head == tail:
                continue
            first = members[head]
            dx = first.start_xy[0] - last.end_xy[0]
            dy = first.start_xy[1] - last.end_xy[1]
            ahead = dx * last.direction[0] + dy * last.direction[1]
            sideways = abs(dx * last.direction[1] - dy * last.direction[0])
            if (
                ahead > 1e-6
                and sideways <= GAP_OFFSET
                and _bend(last.direction, first.direction) <= MAX_BEND_DEGREES
            ):
                gaps.append((ahead, tail, head))
    # A gap join must not close a loop back onto the same line.
    for _, tail, head in sorted(gaps):
        if tail in following or head in preceding:
            continue
        walker, loops = head, False
        while walker in following:
            walker = following[walker]
            if walker == tail:
                loops = True
                break
        if not loops and walker != tail:
            following[tail] = head
            preceding[head] = tail

    lines = []
    for index in range(len(members)):
        if index in preceding:
            continue
        line, walker = [], index
        while walker is not None and len(line) <= len(members):
            line.append(members[walker])
            walker = following.get(walker)
        lines.append(line)

    def reading_order(line: list[_Member]) -> tuple:
        x, y = line[0].start_xy
        # X lines: top to bottom. Y lines: left to right.
        return (-round(y, 1), round(x, 1)) if line[0].axis == "X" else (round(x, 1), round(y, 1))

    return sorted(lines, key=reading_order)


def _beam_lines(
    beams: list[Frame], points: dict, column_joints: set[str]
) -> dict[tuple[str, str], list[list[_Member]]]:
    """Lines of every story and type: ``{(story, "GX"): [line, ...]}``."""
    groups: dict[tuple[str, str], list[_Member]] = {}
    for frame in beams:
        member = _layout_beam(frame, points, column_joints)
        if member is None:
            continue
        kind = ("G" if member.is_girder else "B") + member.axis
        groups.setdefault((frame.story, kind), []).append(member)
    return {key: _chain_lines(members) for key, members in groups.items()}


def _column_stacks(columns: list[Frame], points: dict) -> list[list[Frame]]:
    """Columns that stand on each other, each stack listed from the bottom up."""
    def bottom_top(frame: Frame) -> tuple[str, str]:
        if points[frame.joint_i][2] <= points[frame.joint_j][2]:
            return frame.joint_i, frame.joint_j
        return frame.joint_j, frame.joint_i

    by_bottom = {bottom_top(frame)[0]: frame for frame in columns}
    tops = {bottom_top(frame)[1] for frame in columns}
    stacks = []
    for frame in columns:
        if bottom_top(frame)[0] in tops:
            continue  # a column below carries this one
        stack, current = [], frame
        while current is not None and current not in stack:
            stack.append(current)
            current = by_bottom.get(bottom_top(current)[1])
        stacks.append(stack)
    return stacks


def plan_frame_tags(
    points: dict[str, tuple[float, float, float]],
    beams: list[Frame],
    columns: list[Frame],
    prefixes: dict[str, str],
    restrained_joints: set[str] | None = None,
) -> dict[str, str]:
    """Work out the new unique name of every beam and column.

    ``prefixes`` maps a story to its level prefix; frames on a story without a
    prefix keep their name. ``restrained_joints`` are the supported joints: a
    column stack that does not reach one is a planted column. Planted columns
    are tagged ``PC`` with their own numbering, by the same rule as ``C``.
    With no restraint data every stack is tagged ``C``.

    Returns ``{current name: new name}`` for the frames that get a tag.
    """
    prefixes = {story: prefix for story, prefix in prefixes.items() if prefix}
    column_joints = {joint for frame in columns for joint in (frame.joint_i, frame.joint_j)}
    lines = _beam_lines(beams, points, column_joints)
    tags: dict[str, str] = {}

    for (story, kind), story_lines in lines.items():
        if story not in prefixes:
            continue
        for number, line in enumerate(story_lines, start=1):
            for index, member in enumerate(line):
                tags[member.frame.name] = (
                    f"{prefixes[story]}{kind}-{number}{position_letter(index)}"
                )

    # ---- columns: one row and letter per stack, over the plan of all levels ----
    def bottom_joint(stack: list[Frame]) -> str:
        first = stack[0]
        lower_i = points[first.joint_i][2] <= points[first.joint_j][2]
        return first.joint_i if lower_i else first.joint_j

    all_stacks = _column_stacks(columns, points)
    supported = [
        stack for stack in all_stacks
        if not restrained_joints or bottom_joint(stack) in restrained_joints
    ]
    planted = [stack for stack in all_stacks if stack not in supported]
    for kind, stacks in (("C", supported), ("PC", planted)):
        tags.update(_column_tags(kind, stacks, lines, points, beams, prefixes))
    return tags


def _column_tags(
    kind: str,
    stacks: list[list[Frame]],
    lines: dict[tuple[str, str], list[list[_Member]]],
    points: dict,
    beams: list[Frame],
    prefixes: dict[str, str],
) -> dict[str, str]:
    """Row number and letter of each column stack.

    Supported columns are named ``<level>-C<row><letter>`` and planted columns
    ``<level>-PC<row><letter>``.

    A stack with no column on a tagged level is not counted, so skipped levels
    leave no gaps in the numbers.
    """
    tags: dict[str, str] = {}
    stacks = [stack for stack in stacks if any(frame.story in prefixes for frame in stack)]
    stack_of_joint = {
        joint: index
        for index, stack in enumerate(stacks)
        for frame in stack
        for joint in (frame.joint_i, frame.joint_j)
    }
    position = {}
    for index, stack in enumerate(stacks):
        x, y, _ = min((points[stack[0].joint_i], points[stack[0].joint_j]), key=lambda p: p[2])
        position[index] = (x, y)

    # A row is the columns along one X girder line. Longer lines claim their
    # columns first; a line that meets an existing row extends it.
    story_level = {}
    for frame in beams:
        story_level.setdefault(frame.story, points[frame.joint_i][2])
    girder_lines = []
    for (story, beam_kind), story_lines in lines.items():
        if beam_kind != "GX":
            continue
        for order, line in enumerate(story_lines):
            # Columns jointed to the line, and columns the line passes over
            # (a column of another level, or one the girder is not split at).
            path = [line[0].start_xy]
            for member in line:
                path += [member.start_xy, member.end_xy]
            on_line = list(dict.fromkeys(
                [
                    stack_of_joint[joint]
                    for member in line
                    for joint in (member.start, member.end)
                    if joint in stack_of_joint
                ]
                + [
                    stack
                    for stack, xy in position.items()
                    if any(
                        _distance_to_segment(xy, a, b) <= ROW_OFFSET
                        for a, b in zip(path, path[1:])
                    )
                ]
            ))
            if on_line:
                girder_lines.append((-len(on_line), story_level.get(story, 0.0), order, on_line))
    row_of: dict[int, int] = {}
    rows: list[list[int]] = []
    for _, _, _, on_line in sorted(girder_lines):
        placed = [row_of[stack] for stack in on_line if stack in row_of]
        new = [stack for stack in on_line if stack not in row_of]
        if not new:
            continue
        if placed:
            row = max(set(placed), key=placed.count)
        else:
            row = len(rows)
            rows.append([])
        rows[row].extend(new)
        row_of.update({stack: row for stack in new})
    for stack in range(len(stacks)):  # columns on no X girder line: a row each
        if stack not in row_of:
            row_of[stack] = len(rows)
            rows.append([stack])

    for row in rows:
        row.sort(key=lambda stack: (round(position[stack][0], 1), -round(position[stack][1], 1)))
    rows.sort(key=lambda row: (-round(position[row[0]][1], 1), round(position[row[0]][0], 1)))
    for number, row in enumerate(rows, start=1):
        for index, stack in enumerate(row):
            for frame in stacks[stack]:
                if frame.story in prefixes:
                    tags[frame.name] = (
                        f"{prefixes[frame.story]}-{kind}{number}{position_letter(index)}"
                    )
    return tags


def conflicting_tags(tags: dict[str, str], all_frame_names: list[str]) -> list[str]:
    """New names that are already used by a frame that is not being renamed."""
    untouched = set(all_frame_names) - set(tags)
    return sorted(new for new in tags.values() if new in untouched)


# =============================================================================
# ETABS
# =============================================================================
def read_model_frames(connector) -> tuple[dict, list[Frame], list[Frame], set[str], list[str]]:
    """Read joints, beams, columns, supported joints and the story order (bottom up)."""
    def table(name: str):
        try:
            return connector.get_data(name)
        except RuntimeError:
            return None

    joints = connector.get_data("Point Object Connectivity")
    points = {
        str(row.UniqueName): (float(row.X), float(row.Y), float(row.Z))
        for row in joints.itertuples()
    }

    def frames(name: str) -> list[Frame]:
        data = table(name)
        if data is None or data.empty:
            return []
        return [
            Frame(str(row.UniqueName), str(row.Story), str(row.UniquePtI), str(row.UniquePtJ))
            for row in data.itertuples()
            if str(row.UniquePtI) in points and str(row.UniquePtJ) in points
        ]

    restraints = table("Joint Assignments - Restraints")
    restrained = (
        set() if restraints is None or restraints.empty
        else set(restraints["UniqueName"].astype(str))
    )
    # Story.GetStories returns (count, names, elevations, ..., status), base first.
    stories = [str(name) for name in as_list(connector.sap_model.Story.GetStories()[1])]
    return (
        points, frames("Beam Object Connectivity"), frames("Column Object Connectivity"),
        restrained, stories,
    )


def tagged_model_path(model_path: str) -> str:
    """Path of the tagged copy, next to the model; never an existing file."""
    folder, filename = os.path.split(model_path)
    stem = os.path.splitext(filename)[0]
    extension = ".EDB"  # ETABS can report its text file (.$et) as the model file
    candidate = os.path.join(folder, f"{stem}{TAGGED_SUFFIX}{extension}")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}{TAGGED_SUFFIX} ({counter}){extension}")
        counter += 1
    return candidate


def apply_frame_tags(connector, tags: dict[str, str], progress=None) -> list[str]:
    """Rename the frames in the open model. Returns the names that failed.

    Every frame first gets a temporary name, so a new name that is still in
    use by another frame about to be renamed cannot clash.
    """
    frame_api = connector.sap_model.FrameObj
    changes = [(old, new) for old, new in tags.items() if old != new]
    failed = []
    temporary = {}
    for index, (old, _) in enumerate(changes):
        name = f"~TAG{index}~"
        if return_code(frame_api.ChangeName(old, name)) == 0:
            temporary[old] = name
        else:
            failed.append(old)
        if progress is not None and index % 50 == 0:
            progress(f"Preparing {index + 1} of {len(changes)}")
    for index, (old, new) in enumerate(changes):
        if old not in temporary:
            continue
        if return_code(frame_api.ChangeName(temporary[old], new)) != 0:
            frame_api.ChangeName(temporary[old], old)  # put the old name back
            failed.append(old)
        if progress is not None and index % 50 == 0:
            progress(f"Tagging {index + 1} of {len(changes)}\t{new}")
    return failed


TAG_TARGETS = {
    "Into a copy saved beside it (<model> - TAGGED.EDB)": False,
    "Into this model (overwrite it)": True,
}


def auto_tag_frames(prefixes: dict[str, str] | None = None,
                    in_place: bool | None = None, connector=None) -> str | None:
    """Tag every beam and column of the open ETABS model (``sdt tag``).

    ``in_place`` False (the default answer) saves the model as a tagged copy
    in its own folder first, so the original file is not changed; True tags
    and saves the model itself. It is asked when not given. Returns the path
    of the tagged model.
    """
    from utilities._gui_helpers import LoadingWindow, enter_values, select_option, show_warning

    from etabs_api.core.connection import ETABSConnector

    if connector is None:  # a command that offers the tagging passes its own
        connector = ETABSConnector()
        if not connector.connect():
            return None
    model = connector.sap_model
    model_path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
    if not os.path.isfile(model_path):
        show_warning("Save the ETABS model first: it has no file yet.", title="Auto Tagging")
        return None

    with LoadingWindow("Reading the ETABS model..."):
        points, beams, columns, restrained, stories = read_model_frames(connector)
    if not beams and not columns:
        show_warning("The model has no beams or columns to tag.", title="Auto Tagging")
        return None

    if prefixes is None:
        used = {frame.story for frame in beams + columns}
        ordered = [story for story in reversed(stories) if story in used]
        prefixes = {story: default_story_prefix(story) for story in ordered}
        to_ask = [story for story in ordered if prefixes[story] is None]
        if to_ask:
            answers = enter_values(
                "Auto Tagging - Level Prefixes",
                "Type the prefix of each level, as it should appear in the names "
                "(for example UG or PD1). Leave a level blank to skip it.",
                to_ask,
                {story: story if story.isalnum() else "" for story in to_ask},
            )
            if answers is None:
                return None  # dialog closed without confirming
            prefixes.update({story: answer.strip().upper() for story, answer in answers.items()})
    prefixes = {story: prefix for story, prefix in prefixes.items() if prefix}
    repeated = sorted({p for p in prefixes.values() if list(prefixes.values()).count(p) > 1})
    if repeated:
        show_warning(
            "Two levels cannot share a prefix: " + ", ".join(repeated), title="Auto Tagging"
        )
        return None

    if in_place is None:
        chosen = select_option("Auto Tagging", "Where should the tags go?", list(TAG_TARGETS))
        if chosen is None:
            return None  # dialog closed without confirming
        in_place = TAG_TARGETS[chosen]

    tags = plan_frame_tags(points, beams, columns, prefixes, restrained)
    all_names = [str(name) for name in as_list(model.FrameObj.GetNameList()[1])]
    clashes = set(conflicting_tags(tags, all_names))
    tags = {old: new for old, new in tags.items() if new not in clashes}

    new_path = model_path if in_place else tagged_model_path(model_path)
    with LoadingWindow("Tagging beams and columns...") as window:
        if return_code(model.File.Save(new_path)) != 0:
            show_warning(f"ETABS could not save the model:\n{new_path}", title="Auto Tagging")
            return None
        if model.GetModelIsLocked():
            model.SetModelIsLocked(False)  # names cannot change in a locked model
        failed = apply_frame_tags(connector, tags, progress=window.update)
        model.File.Save(new_path)
        connector.refresh_view()

    from utilities.run_summary import RunSummary, listed

    summary = RunSummary("sdt tag", new_path)
    summary.add("Beams", len(beams))
    summary.add("Columns", len(columns))
    summary.add("Tagged", len(tags) - len(failed))
    summary.add("Target", "the model itself" if in_place else
                "a tagged copy (the original model was not changed)")
    if clashes:
        summary.fail(f"{len(clashes)} names were already used by members that are not tagged; "
                     "those were left as they are: " + listed(sorted(clashes)))
    if failed:
        summary.fail("Could not be renamed: " + listed(failed))
    if not in_place:
        summary.note("ETABS now has the tagged copy open.")
    summary.file("Model", new_path)
    summary.show(popup=True)
    return new_path


if __name__ == "__main__":
    auto_tag_frames()
