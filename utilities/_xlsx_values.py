"""Cell values of the result workbooks: rounded numbers and stated blanks.

A result file should not show a long unrounded number, and an empty cell
should not be mistaken for a missing calculation: a value that does not
apply is written as ``N/A`` with the reason.
"""

from __future__ import annotations

import math
import numbers

import pandas as pd

DEFAULT_DECIMALS = 2
NOT_APPLICABLE = "N/A"


def is_blank(value) -> bool:
    """None, NaN, NaT or an empty text."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(value != value)  # NaN and NaT are not equal to themselves
    except (TypeError, ValueError):
        return value is pd.NA


def cell_value(value, decimals: int = DEFAULT_DECIMALS, blank: str = NOT_APPLICABLE):
    """The value to write in a result cell.

    Whole numbers stay whole, other numbers are rounded to ``decimals``, a
    blank becomes ``blank`` and text is written as it is.
    """
    if is_blank(value):
        return blank
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    number = float(value)
    if math.isinf(number):
        return "Infinite" if number > 0 else "-Infinite"
    rounded = round(number, decimals)
    return int(rounded) if rounded == int(rounded) and abs(rounded) >= 1 else rounded
