"""Where the commands remember their last answers: one folder in the user's home.

The folder is ``~/.structural_design_toolkit``. Answers saved by earlier
versions in ``~/.xlwings_structural`` are copied over the first time it is used.
"""

from __future__ import annotations

import os
import shutil

FOLDER = ".structural_design_toolkit"
OLD_FOLDER = ".xlwings_structural"  # before the project was renamed


def settings_path(filename: str) -> str:
    """Full path of a settings file in the user's settings folder.

    The folder itself is not created here; writers create it when they save.
    """
    home = os.path.expanduser("~")
    folder = os.path.join(home, FOLDER)
    old = os.path.join(home, OLD_FOLDER)
    if not os.path.isdir(folder) and os.path.isdir(old):
        try:
            shutil.copytree(old, folder)
        except OSError:
            return os.path.join(old, filename)
    return os.path.join(folder, filename)
