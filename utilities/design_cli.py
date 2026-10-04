"""Run a design module from the terminal: input dialog, calculation, printout and PDF.

Every design module that runs this way offers the same four names:

* ``INPUTS``                  the input fields (``Field``), in dialog order
* ``calculate(values)``       the calculation, from a dict of the inputs
* ``summary_text(result)``    the results as plain text for the terminal
* ``export_pdf(result, path)``  the PDF calculation report; returns its path or None

``run_design`` turns those into the terminal workflow. The inputs typed last
time are offered again, so a second member only needs the values that change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

OUTPUT_OPTIONS = {
    "Print the results in the terminal": (True, False),
    "Export a PDF calculation report": (False, True),
    "Both": (True, True),
}


@dataclass(frozen=True)
class Field:
    """One input of a design module.

    ``kind`` is "float", "text" or "choice"; a choice must be one of
    ``choices``. An ``optional`` field may be left blank and is then None.
    """

    key: str
    label: str
    default: object
    kind: str = "float"
    choices: tuple = ()
    optional: bool = False


def parse_inputs(fields: list[Field], typed: dict[str, str]) -> dict:
    """Turn the typed text into values; ValueError names the first bad field."""
    values = {}
    for field in fields:
        text = str(typed.get(field.label, "")).strip()
        if not text:
            if field.optional:
                values[field.key] = None
                continue
            raise ValueError(f"{field.label} is required.")
        if field.kind == "float":
            try:
                values[field.key] = float(text)
            except ValueError:
                raise ValueError(f"{field.label} must be a number; you typed {text!r}.") from None
        elif field.kind == "choice":
            match = next((c for c in field.choices if c.lower() == text.lower()), None)
            if match is None:
                raise ValueError(f"{field.label} must be one of: {', '.join(field.choices)}.")
            values[field.key] = match
        else:
            values[field.key] = text
    return values


def dialog_label(field: Field) -> str:
    """The text beside a box; a choice lists what may be typed."""
    if field.kind == "choice":
        return f"{field.label} [{' / '.join(field.choices)}]"
    return field.label + (" (optional)" if field.optional else "")


def _saved_path(name: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".xlwings_structural", f"{name}.json")


def _load(name: str) -> dict:
    try:
        with open(_saved_path(name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save(name: str, values: dict) -> None:
    path = _saved_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(values, handle, indent=2)


def run_design(module, title: str, name: str, report_name: str):
    """The terminal workflow of one design module. Returns the result, or None if cancelled.

    ``name`` keys the saved inputs; ``report_name`` is the suggested PDF file name.
    """
    from utilities._gui_helpers import (
        LoadingWindow,
        enter_values,
        select_option,
        select_save_file,
        show_warning,
    )

    fields = module.INPUTS
    saved = _load(name)
    shown = {}
    for field in fields:
        value = saved.get(field.key, field.default)
        shown[dialog_label(field)] = "" if value is None else (
            f"{value:g}" if isinstance(value, float) else str(value))

    while True:  # ask again, with the same entries, until the inputs are valid
        typed = enter_values(title, "Inputs of the check. Leave an optional box blank.",
                             list(shown), shown)
        if typed is None:
            return None
        shown = typed
        try:
            values = parse_inputs(fields, {f.label: typed[dialog_label(f)] for f in fields})
            result = module.calculate(values)
            break
        except (ValueError, KeyError, ZeroDivisionError) as error:
            show_warning(f"The check could not run:\n\n{error}", title=title)
    _save(name, values)

    choice = select_option(title, "What should be done with the results?", list(OUTPUT_OPTIONS))
    if choice is None:
        return result
    show, export = OUTPUT_OPTIONS[choice]
    if show:
        print(module.summary_text(result))
    if export:
        path = select_save_file(default_name=report_name)
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            with LoadingWindow(f"{title}: writing the PDF report"):
                saved_pdf = module.export_pdf(result, path)
            if saved_pdf:
                print(f"PDF report saved: {saved_pdf}")
            else:
                show_warning("The PDF could not be written. Check that LaTeX (pdflatex) "
                             "is installed.", title=title)
    return result
