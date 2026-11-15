"""The order of member marks and names in the results, schedules and reports.

Marks are read by number, not as text: ``C2`` comes before ``C10``, ``2GX-1``
before ``10GX-1``, and an untagged ETABS name ``2`` before ``10``. The letters
that follow a number (the position letters of ``sdt tag``: A ... Z, AA, AB) are
ordered by length first, so ``C1Z`` comes before ``C1AA``. Leading letters are
ordered as text, so ``C`` marks come before ``PC`` (planted) marks.
"""

import re

import pandas as pd

_DIGITS = re.compile(r"(\d+)")


def natural_key(text: object) -> tuple:
    """Sort key of a mark or name, read by its numbers (``C2`` before ``C10``).

    Letters are compared without case. Every element of the key is a tuple of
    the same shape, so the keys of any two names can be compared.
    """
    parts = _DIGITS.split(str(text).strip().upper())
    key = []
    for index, part in enumerate(parts):
        if index % 2:  # a run of digits
            key.append((1, int(part), 0, ""))
        elif part:  # letters: those after a number by length first (Z before AA)
            key.append((0, 0, len(part) if index else 0, part))
    return tuple(key)


def natural_rank(values: pd.Series) -> pd.Series:
    """The place of each value in the order of ``natural_key``, as integers.

    For ``sort_values`` on several columns: a rank sorts like the key and
    mixes with other columns. Equal texts share a rank; blanks come last.
    """
    text = values.astype(str).where(values.notna(), None)
    ordered = sorted({t for t in text if t is not None}, key=natural_key)
    rank = {t: index for index, t in enumerate(ordered)}
    return text.map(lambda t: rank.get(t, len(rank)) if t is not None else len(rank)).astype(int)


def column_member_order(report: pd.DataFrame) -> list[str]:
    """The columns of a column report in the order of the report and schedule.

    By mark (``natural_key``: C2 before C10, C before PC), then from the top
    floor down (``Level_Elevation_m``, else the story name), then by name. A
    report stored by an older version is put in this order as well.
    """
    if report.empty or "UniqueName" not in report.columns:
        return []
    names = report["UniqueName"].astype(str)
    first = report.assign(_name=names).drop_duplicates("_name")
    label = first["Column_Label"] if "Column_Label" in first.columns else first["_name"]
    if "Level_Elevation_m" in first.columns:
        elevation = pd.to_numeric(first["Level_Elevation_m"], errors="coerce").fillna(float("-inf"))
    else:
        elevation = pd.Series(0.0, index=first.index)
    story = first["Story"] if "Story" in first.columns else first["_name"]
    keys = {
        name: (natural_key(mark), -level, natural_key(floor), natural_key(name))
        for name, mark, level, floor in zip(first["_name"], label, elevation, story)
    }
    return sorted(keys, key=keys.get)


def story_elevations(points: pd.DataFrame | None) -> dict[str, float]:
    """The elevation of each story, from the joints of the ``POINTS`` table.

    The median Z of the joints of a story: a few joints off the floor (a
    sloped member, a step) do not move it. Empty when the table has no
    ``Story`` or ``Z`` column.
    """
    if points is None or getattr(points, "empty", True):
        return {}
    if not {"Story", "Z"} <= set(points.columns):
        return {}
    z = pd.to_numeric(points["Z"], errors="coerce")
    table = pd.DataFrame({"Story": points["Story"].astype(str), "Z": z}).dropna()
    return {str(story): float(value) for story, value in table.groupby("Story")["Z"].median().items()}
