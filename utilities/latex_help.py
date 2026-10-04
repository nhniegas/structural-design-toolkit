"""What to tell the user when a PDF calculation report could not be written.

The reports are compiled by ``pdflatex``, which is not part of the toolkit:
it comes with a LaTeX distribution such as MiKTeX that each machine installs
once. Every command that writes a PDF uses the same words from here, so the
user always gets the command to type.
"""

from __future__ import annotations

import shutil

INSTALL_COMMAND = "winget install MiKTeX.MiKTeX"
INSTALL_HINT = (
    f"Install MiKTeX once, from a terminal: {INSTALL_COMMAND} (or from miktex.org). Then "
    "open a new terminal and run the command again. On the first report MiKTeX asks to "
    "install the packages it lacks: allow it."
)


def latex_path() -> str | None:
    """Where ``pdflatex`` is, or None when no LaTeX is installed."""
    return shutil.which("pdflatex")


def missing_pdf_reason() -> str:
    """Why a PDF report was not written, and what to do about it."""
    if latex_path() is None:
        return ("not written: LaTeX (pdflatex) is not installed. The results and schedules "
                "were written. " + INSTALL_HINT)
    return ("not written: LaTeX stopped with an error. The results and schedules were "
            "written. If MiKTeX asked to install a package, allow it and run the command "
            "again; sdt doctor shows which LaTeX is used.")
