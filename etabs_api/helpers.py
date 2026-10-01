"""Shared ETABS COM return-code and array conversion helpers."""

from __future__ import annotations

from collections.abc import Iterable


def return_code(value) -> int:
    """Extract an ETABS return code from an integer or COM tuple.

    Through comtypes, ETABS returns its output arguments first and the status
    code as the LAST item, e.g. ``(count, names, 0)``.
    """
    if isinstance(value, (tuple, list)):
        return int(value[-1])
    return int(value)


def ensure_success(value, operation: str) -> None:
    """Raise a descriptive error when an ETABS operation returns nonzero."""
    code = return_code(value)
    if code != 0:
        raise RuntimeError(f"{operation} failed with ETABS return code {code}.")


def as_list(value) -> list:
    """Convert COM SAFEARRAY-like values to a regular Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def result_payload(value) -> tuple[int, list]:
    """Return an ETABS return code and the output values that precede it."""
    if isinstance(value, (tuple, list)):
        return int(value[-1]), list(value[:-1])
    return int(value), []

