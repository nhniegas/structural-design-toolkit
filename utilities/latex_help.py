"""What to tell the user when a PDF calculation report could not be written.

The reports are compiled by ``pdflatex``, which is not part of the toolkit:
it comes with a LaTeX distribution such as MiKTeX that each machine installs
once. Every command that writes a PDF uses the same words from here, so the
user always gets the command to type.
"""

from __future__ import annotations

import os
import shutil
import subprocess

INSTALL_COMMAND = "winget install MiKTeX.MiKTeX"
INSTALL_HINT = (
    f"Install MiKTeX once, from a terminal: {INSTALL_COMMAND} (or from miktex.org). Then "
    "open a new terminal and run the command again. Then run sdt doctor: it installs the "
    "LaTeX packages the reports use, so MiKTeX does not ask for each one."
)

# The files the reports load, and the package of the distribution each comes in.
REPORT_PACKAGES = {
    "geometry.sty": "geometry", "xcolor.sty": "xcolor", "booktabs.sty": "booktabs",
    "amsmath.sty": "amsmath", "longtable.sty": "tools", "multicol.sty": "tools",
    "graphicx.sty": "graphics", "extarticle.cls": "extsizes",
    "fancyhdr.sty": "fancyhdr", "colortbl.sty": "colortbl",
    "tgheros.sty": "tex-gyre", "sansmath.sty": "sansmath",
}
# Without these a report is written plainer; without one of the others it is not written.
OPTIONAL_PACKAGES = ("fancyhdr", "colortbl", "tex-gyre", "sansmath")


def latex_path() -> str | None:
    """Where ``pdflatex`` is, or None when no LaTeX is installed."""
    return shutil.which("pdflatex")


def is_miktex() -> bool:
    """Whether the LaTeX found is MiKTeX, whose packages the toolkit can install."""
    path = latex_path()
    return bool(path) and shutil.which("mpm", path=os.path.dirname(path)) is not None


def missing_packages() -> list[str] | None:
    """The packages of ``REPORT_PACKAGES`` this LaTeX lacks, or None when that
    cannot be told (no LaTeX, or no ``kpsewhich``). MiKTeX is asked not to
    install anything: looking for a file must not open its window."""
    path = latex_path()
    finder = shutil.which("kpsewhich", path=os.path.dirname(path)) if path else None
    if finder is None:
        return None
    command = [finder] + (["--miktex-disable-installer"] if is_miktex() else [])
    try:
        found = subprocess.run(command + list(REPORT_PACKAGES), capture_output=True, text=True,
                               timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    present = {os.path.basename(line.strip()).lower() for line in found.splitlines()}
    return sorted({package for file, package in REPORT_PACKAGES.items()
                   if file.lower() not in present})


def install_packages(packages: list[str]) -> list[str]:
    """Install MiKTeX packages for this user, with no window. Returns those that
    could not be installed."""
    path = latex_path()
    folder = os.path.dirname(path) if path else None
    miktex, mpm = shutil.which("miktex", path=folder), shutil.which("mpm", path=folder)
    failed = []
    for package in packages:
        commands = ([[miktex, "packages", "install", package]] if miktex else [])             + ([[mpm, f"--install={package}"]] if mpm else [])
        for command in commands:
            try:
                if subprocess.run(command, capture_output=True, timeout=600).returncode == 0:
                    break
            except (OSError, subprocess.SubprocessError):
                continue
        else:
            failed.append(package)
    return failed


def compiler_args() -> list[str]:
    """Options for ``pdflatex`` so that a missing package cannot stop a report.

    MiKTeX installs a missing package when a report asks for it, and stops the
    report when that fails or is refused, even for a package the report can do
    without. So when only such packages are missing, MiKTeX is told to install
    nothing: the report is written plainer. When a package it cannot do without
    is missing, MiKTeX is left to install it.
    """
    if not is_miktex():
        return []
    missing = missing_packages()
    if missing is None or set(missing) - set(OPTIONAL_PACKAGES):
        return []
    return ["--disable-installer"]


def missing_pdf_reason() -> str:
    """Why a PDF report was not written, and what to do about it."""
    if latex_path() is None:
        return ("not written: LaTeX (pdflatex) is not installed. The results and schedules "
                "were written. " + INSTALL_HINT)
    return ("not written: LaTeX stopped with an error. The results and schedules were "
            "written. Run sdt doctor: it lists the LaTeX packages the reports use and "
            "installs those that are missing. Then run the command again.")
