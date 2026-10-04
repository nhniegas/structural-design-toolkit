"""``sdt doctor``: check that this machine can run the toolkit.

It changes nothing. It reports, one line per check, what the commands need:
ETABS and its API, the dialogs, the loading window, LaTeX for the PDF
reports, and where the toolkit keeps its files. Run it first on a new
machine, and when a command does not start.
"""

from __future__ import annotations

import os
import platform
import sys
import time

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Check = tuple[str, str, str]  # status, what was checked, what was found


def is_packaged() -> bool:
    """Whether the toolkit runs from the packaged program (sdt.exe)."""
    return bool(getattr(sys, "frozen", False))


def check_program(version: str) -> Check:
    how = "packaged program" if is_packaged() else f"Python {platform.python_version()}"
    return OK, "Toolkit", f"version {version}, {how}: {sys.executable}"


def check_settings_folder() -> Check:
    from utilities.user_settings import settings_path

    folder = os.path.dirname(settings_path("x"))
    probe = folder if os.path.isdir(folder) else os.path.dirname(folder)
    if os.access(probe, os.W_OK):
        return OK, "Saved answers", folder
    return FAIL, "Saved answers", f"{folder} cannot be written"


def check_dialogs() -> Check:
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
    except Exception as error:
        return FAIL, "Dialogs", f"tkinter could not open a window: {error}"
    return OK, "Dialogs", "tkinter opens a window"


def check_loading_window() -> Check:
    """Start the progress window as the commands do, and close it again."""
    from utilities._gui_helpers import LoadingWindow

    window = LoadingWindow("sdt doctor: testing the progress window")
    try:
        window.start()
        time.sleep(1.0)
        alive = window.proc is not None and window.proc.poll() is None
    except Exception as error:
        return FAIL, "Progress window", f"could not start: {error}"
    finally:
        window.stop()
    if alive:
        return OK, "Progress window", "starts and closes"
    return FAIL, "Progress window", "closed by itself right after starting"


COMMAND_MODULES = (
    "etabs_api.workflows.model_setup", "etabs_api.workflows.grid_column_model",
    "etabs_api.workflows.frame_tagger", "etabs_api.workflows.model_check",
    "etabs_api.workflows.model_analysis", "etabs_api.workflows.drift_check",
    "etabs_api.workflows.design_loop", "design.concrete_workflow",
    "design.beam_designer_aci318", "design.column_designer_aci318",
    "design.composite_column_designer_aiscDG06",
    "design.general_steel_section_designer_aisc360",
    "design.wind_calculator_directional_asce7", "utilities._calc_report",
)


def check_modules() -> Check:
    """Every command's code and the libraries it uses load (a packaged program
    can miss one)."""
    import importlib

    missing = []
    for name in COMMAND_MODULES:
        try:
            importlib.import_module(name)
        except Exception as error:
            missing.append(f"{name.rsplit('.', 1)[-1]} ({type(error).__name__}: {error})")
    if missing:
        return FAIL, "Commands", "could not load: " + "; ".join(missing)
    return OK, "Commands", f"all {len(COMMAND_MODULES)} modules load"


def check_etabs_program() -> Check:
    from etabs_api.core.connection import DEFAULT_ETABS_PROGRAM_PATH

    path = os.environ.get("ETABS_PROGRAM_PATH", DEFAULT_ETABS_PROGRAM_PATH)
    if os.path.isfile(path):
        return OK, "ETABS program", path
    return WARN, "ETABS program", (
        f"not found at {path}. The commands work on a model that is already open in ETABS; "
        "set ETABS_PROGRAM_PATH to your ETABS.exe to let them start ETABS")


def check_etabs_api() -> Check:
    """The ETABS API: its helper object, and the interface files comtypes writes."""
    try:
        import comtypes.client

        from etabs_api.core.connection import etabs_helper

        etabs_helper()
        folder = getattr(comtypes.client, "gen_dir", None) or "memory only"
    except Exception as error:
        return FAIL, "ETABS API", (
            f"the ETABS API could not be loaded ({error}). ETABS must be installed, and "
            "registered for the API (run ETABS once as administrator)")
    return OK, "ETABS API", f"loaded; interface files in {folder}"


def check_running_etabs() -> Check:
    from etabs_api.core.connection import _etabs_process_ids, etabs_helper, running_etabs

    try:
        running = _etabs_process_ids()
        if not running:
            return WARN, "Open ETABS model", "ETABS is not running (open your model first)"
        etabs = running_etabs(etabs_helper())
        if etabs is None:
            return FAIL, "Open ETABS model", (
                "ETABS is running but could not be reached. It may run as administrator "
                "while this program does not: start both the same way")
        name = str(etabs.SapModel.GetModelFilename()) or "a model that is not saved yet"
    except Exception as error:
        return FAIL, "Open ETABS model", f"could not be read: {error}"
    return OK, "Open ETABS model", name


def check_latex() -> Check:
    from utilities.latex_help import INSTALL_HINT, latex_path

    path = latex_path()
    if path:
        return OK, "LaTeX (PDF reports)", path
    return WARN, "LaTeX (PDF reports)", (
        "pdflatex was not found: results and schedules are still written, the PDF "
        "calculation reports are not. " + INSTALL_HINT)


def run_doctor(version: str = "") -> list[Check]:
    """Run every check, print one line each, and return them."""
    checks = [check_program(version), check_settings_folder(), check_modules(),
              check_dialogs(), check_loading_window(), check_etabs_program()]
    api = check_etabs_api()
    checks.append(api)
    if api[0] == OK:
        checks.append(check_running_etabs())
    checks.append(check_latex())
    print()
    for status, what, found in checks:
        print(f"[{status:>4}] {what}: {found}")
    failed = sum(1 for status, _, _ in checks if status == FAIL)
    warned = sum(1 for status, _, _ in checks if status == WARN)
    print()
    print("Everything the toolkit needs is in place." if not failed and not warned else
          f"{failed} problem(s), {warned} warning(s). A warning limits one feature; "
          "a problem stops the commands that need it.")
    return checks
