"""``sdt doctor``: check that this machine can run the toolkit.

It reports, one line per check, what the commands need: ETABS and its API,
the dialogs, the loading window, LaTeX for the PDF reports and its packages,
and where the toolkit keeps its files. It changes nothing in a model. The one
thing it can install, when you agree, is the LaTeX packages the reports use
that MiKTeX lacks. Run it first on a new machine, and when a command does not
start.
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
    "etabs_api.workflows.design_loop", "etabs_api.workflows.framing_plans",
    "etabs_api.workflows.foundation_model", "etabs_api.workflows.foundation_plan",
    "etabs_api.workflows.model_3d", "etabs_api.workflows.irregularity",
    "etabs_api.workflows.concrete_quantities", "etabs_api.workflows.compatibility",
    "etabs_api.workflows.wall_design", "etabs_api.workflows.wall_piers", "design.concrete.walls",
    "design.concrete.workflow",
    "design.concrete.beams.design", "design.concrete.beams.schedule",
    "design.concrete.beams.calculations", "design.concrete.columns.design",
    "design.concrete.columns.schedule", "design.concrete.columns.calculations",
    "design.steel.composite_column_aiscDG06",
    "design.steel.wide_flange_aisc360",
    "design.loads.wind_directional_asce7", "design.concrete.pile_cap",
    "design.concrete.pile_cap_report", "design.concrete.pile_cap_window",
    "utilities.design_window",
    "utilities._calc_report",
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


LATEX_PACKAGES = "LaTeX packages"


def check_latex_packages() -> Check | None:
    """The LaTeX packages of the reports. None when there is no LaTeX to ask."""
    from utilities.latex_help import REPORT_PACKAGES, is_miktex, latex_path, missing_packages

    if latex_path() is None:
        return None
    missing = missing_packages()
    if missing is None:
        return WARN, LATEX_PACKAGES, "could not be listed (kpsewhich was not found)"
    if not missing:
        return OK, LATEX_PACKAGES, (
            f"all {len(set(REPORT_PACKAGES.values()))} packages of the reports are installed")
    how = ("sdt doctor can install them" if is_miktex() else
           "install them with: tlmgr install " + " ".join(missing))
    return WARN, LATEX_PACKAGES, f"missing: {', '.join(missing)}. {how}"


def offer_latex_packages(check: Check) -> Check:
    """Ask to install the missing MiKTeX packages, install them, and check again."""
    from utilities import latex_help
    from utilities._gui_helpers import select_option

    missing = latex_help.missing_packages()
    if not missing or not latex_help.is_miktex():
        return check
    yes = "Yes - install them now"
    choice = select_option(
        "sdt doctor", "The PDF reports use LaTeX packages that MiKTeX lacks on this machine: "
        + ", ".join(missing) + ". Install them now? It needs the internet and takes about a "
        "minute. Without them MiKTeX asks for each one when a report is written.",
        [yes, "No - leave them"])
    if choice != yes:
        return check
    print(f"Installing the LaTeX packages: {', '.join(missing)} ...", flush=True)
    failed = latex_help.install_packages(missing)
    after = check_latex_packages() or check
    if failed and after[0] != OK:
        return WARN, LATEX_PACKAGES, (
            f"could not be installed: {', '.join(failed)}. Check the internet connection, "
            "or install them in the MiKTeX Console (Packages)")
    return after


def check_3d_kernel() -> Check:
    """Whether build123d, which makes the solids of ``sdt 3d``, can be loaded."""
    try:
        import build123d
    except Exception as error:  # noqa: BLE001 - not installed, or one of its parts is missing
        return WARN, "3D model (build123d)", (
            f"build123d could not be loaded ({error}): sdt 3d cannot write its STEP file. "
            "Every other command works. Install it with: pip install build123d")
    return OK, "3D model (build123d)", f"build123d {build123d.__version__}"


def check_design_processes() -> Check:
    """Whether the member designs of a large model can be shared between the cores."""
    from design import parallel

    cores = os.cpu_count() or 1
    if cores < 3:
        return OK, "Design processes", f"{cores} core(s): the designs run in one process"
    if parallel.self_test() == 2:
        return OK, "Design processes", (
            f"{cores} cores; the beam design of a large model uses up to "
            f"{min(parallel.MAX_WORKERS, cores - 1)} processes (SDT_WORKERS=1 switches it off)")
    return WARN, "Design processes", (
        "the extra processes did not start: the designs run in one process, which is "
        "slower on a large model and gives the same results")


def run_doctor(version: str = "") -> list[Check]:
    """Run every check, print one line each, and return them."""
    checks = [check_program(version), check_settings_folder(), check_modules(),
              check_dialogs(), check_loading_window(), check_etabs_program()]
    api = check_etabs_api()
    checks.append(api)
    if api[0] == OK:
        checks.append(check_running_etabs())
    checks.append(check_latex())
    packages = check_latex_packages()
    if packages is not None:
        checks.append(offer_latex_packages(packages) if packages[0] == WARN else packages)
    checks.append(check_3d_kernel())
    checks.append(check_design_processes())
    failed = sum(1 for status, _, _ in checks if status == FAIL)
    warned = sum(1 for status, _, _ in checks if status == WARN)
    verdict = ("Everything the toolkit needs is in place." if not failed and not warned else
               f"{failed} problem(s), {warned} warning(s). A warning limits one feature; "
               "a problem stops the commands that need it.")
    from utilities import terminal

    if terminal.drawn():
        report = terminal.Report("sdt doctor" + (f" {version}" if version else ""))
        report.table(["Status", "Check", "Found"], [list(check) for check in checks])
        report.lines([verdict], "green" if not failed and not warned else "yellow")
        report.show()
        return checks
    print()
    for status, what, found in checks:
        print(f"[{status:>4}] {what}: {found}")
    print()
    print(verdict)
    return checks
