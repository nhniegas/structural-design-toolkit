"""
The subfolders of an output folder, by kind of file.

Every command writes its files into one of these, inside the output folder it
was given (or, for the commands that write beside the model, inside the
model's folder):

    Results        workbooks (.xlsx)
    Calculations   calculation reports (.pdf)
    Schedules      beam and column schedules (.dxf)
    Drawings       framing and foundation plans (.dxf) and the 3D model (.step)
    Summaries      summaries, logs and text reports (.txt)

The design data file (" - design data.pkl") stays at the top of the output
folder: later commands read it back from there.
"""

import os

RESULTS = "Results"
CALCULATIONS = "Calculations"
SCHEDULES = "Schedules"
DRAWINGS = "Drawings"
SUMMARIES = "Summaries"

BY_EXTENSION = {
    ".xlsx": RESULTS,
    ".pdf": CALCULATIONS,
    ".dxf": SCHEDULES,
    ".step": DRAWINGS,
    ".stp": DRAWINGS,
    ".txt": SUMMARIES,
    ".log": SUMMARIES,
}


def output_path(folder: str, filename: str, kind: str | None = None) -> str:
    """The path of ``filename`` in its subfolder of ``folder``, which is made
    if needed. ``kind`` names the subfolder when the extension does not
    decide it (a plan is a .dxf but goes to Drawings). A file of another kind
    stays at the top of the folder."""
    sub = kind or BY_EXTENSION.get(os.path.splitext(filename)[1].lower())
    target = os.path.join(folder, sub) if sub else folder
    os.makedirs(target, exist_ok=True)
    return os.path.join(target, filename)


def subfolder(folder: str, kind: str) -> str:
    """The subfolder ``kind`` of ``folder``, made if needed, for writers that
    take a folder and name their own files (the schedules)."""
    target = os.path.join(folder, kind)
    os.makedirs(target, exist_ok=True)
    return target


def beside(model_path: str, suffix: str, kind: str | None = None) -> str:
    """The path of a file named after the model (``MODEL - Drift.txt`` for
    the suffix ``" - Drift.txt"``), in its subfolder of the model's folder."""
    folder = os.path.dirname(os.path.abspath(model_path))
    stem = os.path.splitext(os.path.basename(model_path))[0]
    return output_path(folder, stem + suffix, kind)


def relative(kind: str, filename: str) -> str:
    """How a file is named from the top of the output folder, for lists of the
    files written (``Results/MODEL - Beam Design.xlsx``)."""
    return f"{kind}/{filename}"
