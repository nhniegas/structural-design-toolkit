"""Test runs of the commands in ETABS, with no one at the screen.

    python tools/etabs_run.py SCENARIO.json                  run, and compare with the reference
    python tools/etabs_run.py SCENARIO.json --save-reference  run, and keep the results as the reference

A scenario names a model and the commands to run on it, each with the
answers to its dialogs (utilities/answers.py)::

    {
      "model": "t34.EDB",
      "steps": [
        {"command": "analyze", "unanswered": "default", "answers": []},
        {"command": "beams", "folder": "out", "unanswered": "default",
         "answers": [{"ask": "Seismic combinations to design for", "answer": "Both"}]}
      ]
    }

What a run does, and why the results can be trusted:

* **A copy of the model.** The model (with its ``.setup.json``) is copied to
  a work folder beside the scenario, emptied first. The model itself is
  never opened.
* **Its own ETABS.** A new ETABS is started for the run and closed at the
  end; the commands are tied to that process (``SDT_ETABS_PID``), so an ETABS
  you have open is not touched.
* **The commands themselves.** Each step is ``sdt <command> --answers``: the
  same code as with the dialogs, given the same values the dialogs return.
* **The program's own defaults.** The remembered answers of this machine are
  not read (``SDT_SETTINGS_DIR`` is an empty folder of the run), so a run
  gives the same on any machine, and yours are left as they are.
* **Every value compared.** What each command returns (the extracted
  tables, the design results, the findings, the reports) is saved and
  compared with the reference. Text, counts and whole numbers must be the
  same: a bar count, a spacing, a status, a name. Other numbers must agree
  within ``--tolerance``, 1e-9 of their size unless another is given: ETABS
  does not give the last digits of a force the same at every analysis (about
  1e-14 of its size was measured). A difference fails the run and is listed
  with its table, column and row; the report also gives the largest
  difference that was accepted, so that nothing is hidden by the tolerance.
  ``--tolerance 0`` accepts none.
* **What ETABS names by itself is left out.** The ``GUID`` and ``PointBay``
  of the joints: ETABS makes the joints of its mesh again at each analysis
  and gives them new ones.

The speed comes from what is no longer done, not from a shortcut in the
calculation: no dialogs or progress windows, one ETABS and one Python for all
the steps, and no one to wait for.

    --keep-open        leave the ETABS of the run open (its process number is printed)
    --pid N            use that ETABS instead of starting one
    --only a,b         run only these steps (by name or number)
    --units kN-m       set the model's present units before the steps, as a model
                       made in those units has them
    --tolerance X      relative difference of a number that is accepted (default 1e-9;
                       0 accepts none)
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import math
import os
import pickle
import shutil
import subprocess
import sys
import time
import traceback

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

# Not part of a result: where the run happened and when.
SKIPPED_KEYS = {"model_path", "model_saved", "path", "log_path", "working", "folder"}
# Columns ETABS fills with names of its own that change at each analysis.
SKIPPED_COLUMNS = {"GUID", "PointBay"}
TOLERANCE = 1e-9
MAX_LISTED = 12


@dataclasses.dataclass
class Accepted:
    """The numbers that differ within the tolerance: how many, and the largest
    difference as a share of the size of its column (or of the number)."""

    count: int = 0
    largest: float = 0.0
    where: str = ""

    def add(self, count: int, share: float, where: str) -> None:
        self.count += count
        if share > self.largest:
            self.largest, self.where = share, where


# =============================================================================
# RESULTS AS PLAIN DATA
# =============================================================================
def plain(value, depth: int = 0):
    """What a command returned as tables, lists, texts and numbers, so that two
    runs can be compared. Paths and times are left out (``SKIPPED_KEYS``)."""
    import numpy as np
    import pandas as pd

    if depth > 12:
        return str(type(value).__name__)
    if isinstance(value, pd.DataFrame):
        table = value.reset_index(drop=True)
        table.attrs = {}      # working data of the command (objects), not a result
        return table
    if isinstance(value, pd.Series):
        return value.reset_index(drop=True).to_frame()
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [plain(item, depth + 1) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(key): plain(item, depth + 1) for key, item in value.items()
                if str(key) not in SKIPPED_KEYS}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [plain(item, depth + 1) for item in items]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name), depth + 1)
                for field in dataclasses.fields(value) if field.name not in SKIPPED_KEYS}
    if hasattr(value, "__dict__"):
        return {key: plain(item, depth + 1) for key, item in vars(value).items()
                if key not in SKIPPED_KEYS and not key.startswith("_") and not callable(item)}
    return str(value)


def _same_number(a: float, b: float, tolerance: float, where: str = "",
                 accepted: Accepted | None = None) -> bool:
    if a != a and b != b:          # both not a number
        return True
    if a == b:
        return True
    if tolerance <= 0 or a != a or b != b or math.isinf(a) or math.isinf(b):
        return False
    whole = float(a).is_integer() and float(b).is_integer() and max(abs(a), abs(b)) < 1e9
    if whole:                      # a count: 4 bars are not 5 bars
        return False
    size = max(abs(a), abs(b), 1.0)
    if abs(a - b) > tolerance * size:
        return False
    if accepted is not None:
        accepted.add(1, abs(a - b) / size, where)
    return True


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def _same_cell(a, b, tolerance: float, where: str = "", accepted: Accepted | None = None) -> bool:
    if _blank(a) and _blank(b):
        return True
    if _is_number(a) and _is_number(b):
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return _same_number(float(a), float(b), tolerance, where, accepted)
    if type(a) is not type(b) and not (_is_number(a) or _is_number(b)):
        return str(a) == str(b)
    return a == b


def compare_tables(now, reference, where: str, tolerance: float = TOLERANCE,
                   accepted: Accepted | None = None) -> list[str]:
    """The differences between two tables: columns, number of rows, then every cell.

    A column of decimal numbers is compared within ``tolerance`` of the
    largest number of the column, so that a force that is zero but for the
    noise of the analysis (1e-15 against 2e-15 kN) is not a difference. A
    column of whole numbers, and every text, must be the same.
    """
    import numpy as np

    out = []
    now = now[[c for c in now.columns if c not in SKIPPED_COLUMNS]]
    reference = reference[[c for c in reference.columns if c not in SKIPPED_COLUMNS]]
    if list(now.columns) != list(reference.columns):
        added = [c for c in now.columns if c not in set(reference.columns)]
        gone = [c for c in reference.columns if c not in set(now.columns)]
        if added or gone:
            out.append(f"{where}: columns differ"
                       + (f"; new: {', '.join(map(str, added))}" if added else "")
                       + (f"; missing: {', '.join(map(str, gone))}" if gone else ""))
        else:
            out.append(f"{where}: the columns are in another order")
    if len(now) != len(reference):
        out.append(f"{where}: {len(now)} rows, the reference has {len(reference)}")
        return out
    for column in [c for c in reference.columns if c in set(now.columns)]:
        a, b = now[column], reference[column]
        if a.dtype.kind in "fiub" and b.dtype.kind in "fiub":
            x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
            same = (x == y) | (np.isnan(x) & np.isnan(y))
            decimal = a.dtype.kind == "f" and b.dtype.kind == "f"
            if tolerance > 0 and decimal and not same.all():
                finite = np.isfinite(x) & np.isfinite(y)
                size = max(float(np.nanmax(np.abs(np.where(finite, x, 0.0)))),
                           float(np.nanmax(np.abs(np.where(finite, y, 0.0)))), 1.0)
                with np.errstate(invalid="ignore"):
                    gap = np.where(finite, np.abs(x - y), np.inf)
                close = ~same & (gap <= tolerance * size)
                if accepted is not None and close.any():
                    accepted.add(int(close.sum()), float(gap[close].max()) / size,
                                 f"{where}[{column}]")
                same |= close
            rows = np.flatnonzero(~same)
        else:
            rows = [i for i, (p, q) in enumerate(zip(a.tolist(), b.tolist()))
                    if not _same_cell(p, q, tolerance, f"{where}[{column}]", accepted)]
        if len(rows):
            first = int(rows[0])
            out.append(f"{where}[{column}]: {len(rows)} of {len(a)} values differ; row {first}: "
                       f"{a.iloc[first]!r}, the reference has {b.iloc[first]!r}")
    return out


def compare(now, reference, where: str = "result", tolerance: float = TOLERANCE,
            accepted: Accepted | None = None) -> list[str]:
    """Every difference between what a step returned and its reference.

    ``accepted`` collects the numbers that differ within the tolerance.
    """
    import pandas as pd

    if isinstance(reference, pd.DataFrame) or isinstance(now, pd.DataFrame):
        if not (isinstance(reference, pd.DataFrame) and isinstance(now, pd.DataFrame)):
            return [f"{where}: a table on one side only"]
        return compare_tables(now, reference, where, tolerance, accepted)
    if isinstance(reference, dict) or isinstance(now, dict):
        if not (isinstance(reference, dict) and isinstance(now, dict)):
            return [f"{where}: {type(now).__name__}, the reference has {type(reference).__name__}"]
        out = []
        for key in list(reference) + [k for k in now if k not in reference]:
            if key not in now:
                out.append(f"{where}.{key}: missing")
            elif key not in reference:
                out.append(f"{where}.{key}: not in the reference")
            else:
                out += compare(now[key], reference[key], f"{where}.{key}", tolerance, accepted)
        return out
    if isinstance(reference, list) or isinstance(now, list):
        if not (isinstance(reference, list) and isinstance(now, list)):
            return [f"{where}: {type(now).__name__}, the reference has {type(reference).__name__}"]
        if len(now) != len(reference):
            return [f"{where}: {len(now)} items, the reference has {len(reference)}"]
        out = []
        for index, (a, b) in enumerate(zip(now, reference)):
            out += compare(a, b, f"{where}[{index}]", tolerance, accepted)
        return out
    if not _same_cell(now, reference, tolerance, where, accepted):
        return [f"{where}: {now!r}, the reference has {reference!r}"]
    return []


# =============================================================================
# THE SCENARIO
# =============================================================================
@dataclasses.dataclass
class StepResult:
    name: str
    command: str
    status: str = "not run"          # ok, stopped, error
    seconds: float = 0.0
    dialogs: int = 0
    defaults: int = 0
    unused: int = 0
    comparison: str = ""
    compared: bool = False
    differences: list[str] = dataclasses.field(default_factory=list)
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "ok" and not self.differences


def read_scenario(path: str) -> dict:
    """The scenario of ``path`` with its paths made absolute and its steps named."""
    with open(path, encoding="utf-8") as handle:
        scenario = json.load(handle)
    base = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]

    def absolute(value: str) -> str:
        return os.path.normpath(value if os.path.isabs(value) else os.path.join(base, value))

    if not scenario.get("model") or not scenario.get("steps"):
        raise ValueError("A scenario needs a \"model\" and its \"steps\".")
    scenario["model"] = absolute(scenario["model"])
    scenario["work"] = absolute(scenario.get("work") or f"{stem} run")
    scenario["reference"] = absolute(scenario.get("reference") or f"{stem} reference")
    seen: dict[str, int] = {}
    for number, step in enumerate(scenario["steps"], start=1):
        if "command" not in step:
            raise ValueError(f"Step {number} needs a \"command\".")
        name = str(step.get("name") or step["command"])
        seen[name] = seen.get(name, 0) + 1
        step["name"] = name if seen[name] == 1 else f"{name} {seen[name]}"
        step["number"] = number
    return scenario


def chosen_steps(steps: list[dict], only: str | None) -> list[dict]:
    """The steps to run: all, or those named (or numbered) in ``only``."""
    if not only:
        return steps
    wanted = {part.strip().casefold() for part in only.split(",") if part.strip()}
    picked = [step for step in steps
              if step["name"].casefold() in wanted or str(step["number"]) in wanted]
    if not picked:
        raise ValueError(f"No step is named or numbered {only!r}.")
    return picked


def prepare_work_folder(scenario: dict) -> str:
    """An empty work folder with a copy of the model; returns the path of the copy."""
    model, work = scenario["model"], scenario["work"]
    if not os.path.isfile(model):
        raise FileNotFoundError(f"The model of the scenario does not exist: {model}")
    if os.path.normcase(os.path.dirname(model)) == os.path.normcase(work):
        raise ValueError("The work folder must not be the folder of the model.")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(os.path.join(work, "settings"))
    copy = os.path.join(work, os.path.basename(model))
    shutil.copy2(model, copy)
    setup = os.path.splitext(model)[0] + ".setup.json"
    if os.path.isfile(setup):
        shutil.copy2(setup, os.path.splitext(copy)[0] + ".setup.json")
    return copy


class _Tee(io.TextIOBase):
    """Writes to the terminal and keeps a copy."""

    def __init__(self, stream):
        self.stream, self.kept = stream, io.StringIO()

    def write(self, text: str) -> int:
        self.kept.write(text)
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            self.stream.write(text.encode("ascii", "replace").decode("ascii"))
        return len(text)

    def flush(self) -> None:
        self.stream.flush()


def run_step(step: dict, scenario: dict, save_reference: bool, tolerance: float) -> StepResult:
    """Run one command from its answers, then save and compare what it returned."""
    import main
    from utilities.answers import AnswersError

    work = scenario["work"]
    label = f"{step['number']:02d} {step['name']}"
    result = StepResult(step["name"], step["command"])
    answers_file = os.path.join(work, f"{label} answers.json")
    with open(answers_file, "w", encoding="utf-8") as handle:
        json.dump({key: step[key] for key in ("folder", "unanswered", "answers") if key in step},
                  handle, indent=2, ensure_ascii=False)
    if step.get("open"):      # a step that needs another model than the one left open
        reopen(step["open"], scenario)
    returned = None
    tee = _Tee(sys.stdout)
    start = time.time()
    try:
        with contextlib.redirect_stdout(tee):
            print(f"\n===== step {label}: sdt {step['command']} =====", flush=True)
            returned = main.run_and_restore(step["command"], answers_file)
        result.status = "ok" if returned is not None or step.get("returns_nothing") else "stopped"
        if result.status == "stopped":
            result.detail = "the command ended without a result (a dialog closed, or a warning)"
    except AnswersError as error:
        result.status, result.detail = "stopped", str(error)
    except Exception as error:  # noqa: BLE001 - whatever a command raises fails its step
        result.status, result.detail = "error", f"{type(error).__name__}: {error}"
        with contextlib.redirect_stdout(tee):
            traceback.print_exc(file=sys.stdout)
    result.seconds = time.time() - start
    text = tee.kept.getvalue()
    with open(os.path.join(work, f"{label} output.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    lines = [line for line in text.splitlines() if line.startswith("[answers] ")]
    result.dialogs = sum(1 for line in lines if " -> " in line)
    result.defaults = sum(1 for line in lines if line.endswith("(default)"))
    result.compared = False
    result.unused = sum(1 for line in lines if "were not used" in line)
    if result.status != "ok":
        print(f"step {label}: {result.status} - {result.detail}", flush=True)
        return result

    snapshot = plain(returned)
    os.makedirs(os.path.join(work, "results"), exist_ok=True)
    with open(os.path.join(work, "results", f"{label}.pkl"), "wb") as handle:
        pickle.dump(snapshot, handle, protocol=pickle.HIGHEST_PROTOCOL)
    reference_file = os.path.join(scenario["reference"], f"{label}.pkl")
    if save_reference:
        os.makedirs(scenario["reference"], exist_ok=True)
        shutil.copy2(os.path.join(work, "results", f"{label}.pkl"), reference_file)
        result.comparison = "reference saved"
    elif os.path.isfile(reference_file):
        result.compared = True
        from design.concrete.workflow import StoreReader

        with open(reference_file, "rb") as handle:
            reference = StoreReader(handle).load()   # also a reference of before the folders
        accepted = Accepted()
        result.differences = compare(snapshot, reference, step["name"], tolerance, accepted)
        if result.differences:
            result.comparison = f"{len(result.differences)} differences from the reference"
        elif accepted.count:
            result.comparison = (
                f"same as the reference: text and counts identical, {accepted.count:,} numbers "
                f"within {tolerance:g} (largest difference {accepted.largest:.1e} of its size, "
                f"in {accepted.where})")
        else:
            result.comparison = "identical to the reference, to the last digit"
    else:
        result.comparison = "no reference to compare with"
    return result


def reopen(which: str, scenario: dict) -> None:
    """Open a model of the work folder before a step: "model" is the copy of the
    scenario's model, anything else a file name in the work folder."""
    from etabs_api.core.connection import etabs_helper, ETABS_OBJECT

    name = os.path.basename(scenario["model"]) if which == "model" else which
    path = os.path.join(scenario["work"], name)
    etabs = etabs_helper().GetObjectProcess(ETABS_OBJECT, int(os.environ["SDT_ETABS_PID"]))
    outcome = etabs.SapModel.File.OpenFile(path)
    if (outcome[-1] if isinstance(outcome, (list, tuple)) else outcome) != 0:
        raise RuntimeError(f"ETABS could not open {path}.")


# =============================================================================
# ETABS
# =============================================================================
def start_etabs() -> tuple[object, int]:
    """A new ETABS for the run: (its ETABS object, its process number)."""
    from etabs_api.core.connection import (
        DEFAULT_ETABS_PROGRAM_PATH,
        _etabs_process_ids,
        etabs_helper,
    )

    program = os.environ.get("ETABS_PROGRAM_PATH", DEFAULT_ETABS_PROGRAM_PATH)
    if not os.path.exists(program):
        raise FileNotFoundError(f"ETABS was not found at {program!r}. Set ETABS_PROGRAM_PATH.")
    before = set(_etabs_process_ids())
    etabs = etabs_helper().CreateObject(program)
    etabs.ApplicationStart()
    new = sorted(set(_etabs_process_ids()) - before)
    if len(new) != 1:
        raise RuntimeError("The ETABS started for the run could not be told from the others "
                           f"(new processes: {new}).")
    return etabs, new[0]


def attach_etabs(pid: int):
    from etabs_api.core.connection import ETABS_OBJECT, etabs_helper

    etabs = etabs_helper().GetObjectProcess(ETABS_OBJECT, int(pid))
    if etabs is None:
        raise RuntimeError(f"No ETABS answers as process {pid}.")
    return etabs


def close_etabs(etabs, pid: int) -> None:
    """Close the ETABS of the run, and end its process if it does not go."""
    from etabs_api.core.connection import _etabs_process_ids

    try:
        etabs.ApplicationExit(False)
    except Exception:  # noqa: BLE001 - it is ended below in any case
        pass
    for _ in range(40):
        if pid not in _etabs_process_ids():
            return
        time.sleep(0.5)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)


def unit_code(name: str | None) -> int | None:
    from etabs_api.core.connection import UNIT_NAMES

    if not name:
        return None
    for code, text in UNIT_NAMES.items():
        if text.casefold() == str(name).casefold():
            return code
    raise ValueError(f"Units {name!r} are not known; use one of: {', '.join(UNIT_NAMES.values())}.")


# =============================================================================
# THE RUN
# =============================================================================
def report_text(scenario_path: str, results: list[StepResult], times: dict[str, float]) -> str:
    lines = ["", "=" * 78, f"ETABS TEST RUN - {os.path.basename(scenario_path)}", "=" * 78]
    lines += [f"{label}: {seconds:.0f} s" for label, seconds in times.items()]
    lines.append("")
    lines.append(f"{'step':<22}{'result':<10}{'time':>7}  {'dialogs':>7}  {'defaults':>8}  comparison")
    for r in results:
        lines.append(f"{r.name:<22}{r.status:<10}{r.seconds:>6.0f}s  {r.dialogs:>7}  "
                     f"{r.defaults:>8}  {r.comparison}")
        if r.detail:
            lines.append(f"    {r.detail}")
        lines += [f"    {text}" for text in r.differences[:MAX_LISTED]]
        if len(r.differences) > MAX_LISTED:
            lines.append(f"    ... and {len(r.differences) - MAX_LISTED} more")
        if r.unused:
            lines.append("    some answers were not used (see the output of the step)")
    if not results or not all(r.passed for r in results):
        verdict = "FAILED: see the steps above."
    elif all(r.compared for r in results):
        verdict = "PASSED: every step ran and its results are those of the reference."
    elif all(r.comparison == "reference saved" for r in results):
        verdict = "REFERENCE SAVED: every step ran, and its results are now the reference."
    else:
        verdict = ("RAN: every step ran. Not every step has a reference to compare with "
                   "(--save-reference keeps the results of a run as the reference).")
    lines += ["", verdict, "=" * 78]
    return "\n".join(lines)


def run(scenario_path: str, save_reference: bool = False, keep_open: bool = False,
        pid: int | None = None, only: str | None = None, units: str | None = None,
        tolerance: float = TOLERANCE) -> int:
    """Run a scenario. Returns 0 when every step ran and nothing differs."""
    scenario = read_scenario(scenario_path)
    steps = chosen_steps(scenario["steps"], only)
    units = unit_code(units or scenario.get("units"))
    times: dict[str, float] = {}
    started = time.time()
    results: list[StepResult] = []
    own = pid is None
    etabs = None
    if not own:
        # an ETABS used again still holds the model of its last run: it lets go
        # of those files only when another model takes its place
        etabs = attach_etabs(pid)
        etabs.SapModel.InitializeNewModel()
        etabs.SapModel.File.NewBlank()
    copy = prepare_work_folder(scenario)
    os.environ["SDT_SETTINGS_DIR"] = os.path.join(scenario["work"], "settings")
    try:
        if own:
            print("Starting an ETABS for the run", flush=True)
            etabs, pid = start_etabs()
        os.environ["SDT_ETABS_PID"] = str(pid)
        times["ETABS started"] = time.time() - started
        print(f"ETABS process {pid}; opening {copy}", flush=True)
        opened = time.time()
        model = etabs.SapModel
        outcome = model.File.OpenFile(copy)
        if (outcome[-1] if isinstance(outcome, (list, tuple)) else outcome) != 0:
            raise RuntimeError(f"ETABS could not open {copy}.")
        if units is not None:
            model.SetPresentUnits(units)
        times["model opened"] = time.time() - opened
        for step in steps:
            results.append(run_step(step, scenario, save_reference, tolerance))
            if results[-1].status != "ok" and not step.get("continue_after_failure"):
                break
    finally:
        closing = time.time()
        if etabs is not None and pid is not None:
            if keep_open or not own:
                print(f"ETABS process {pid} is left open (--pid {pid} uses it again).", flush=True)
            else:
                close_etabs(etabs, pid)
                times["ETABS closed"] = time.time() - closing
        os.environ.pop("SDT_ETABS_PID", None)
        os.environ.pop("SDT_SETTINGS_DIR", None)
    times["whole run"] = time.time() - started
    text = report_text(scenario_path, results, times)
    print(text, flush=True)
    with open(os.path.join(scenario["work"], "run report.txt"), "w", encoding="utf-8") as handle:
        handle.write(text.lstrip("\n") + "\n")
    return 0 if results and len(results) == len(steps) and all(r.passed for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run sdt commands in an ETABS of their own, from a scenario file, and "
                    "compare what they return with a reference.")
    parser.add_argument("scenario")
    parser.add_argument("--save-reference", action="store_true",
                        help="keep the results of this run as the reference")
    parser.add_argument("--keep-open", action="store_true", help="leave the ETABS of the run open")
    parser.add_argument("--pid", type=int, help="use this ETABS process instead of starting one")
    parser.add_argument("--only", help="run only these steps: names or numbers, with commas")
    parser.add_argument("--units", help="present units of the model for the run, such as kN-m")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE,
                        help="relative difference of a number that is accepted (default "
                             f"{TOLERANCE:g}; 0 accepts none)")
    arguments = parser.parse_args(argv)
    from design import parallel

    parallel.enable()   # as the sdt program does
    return run(arguments.scenario, arguments.save_reference, arguments.keep_open, arguments.pid,
               arguments.only, arguments.units, arguments.tolerance)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
