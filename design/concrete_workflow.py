"""Concrete beam and column design from the terminal (``python main.py beams`` / ``columns``).

``xs beams``
    Attaches to the model open in ETABS, asks the inputs, reads the forces
    from the analysis results (with the service loads for deflection), designs
    every beam, and saves the results (.xlsx), the calculation report (.pdf)
    and the beam schedules (.dxf) in the folder you choose.

``xs deflection``
    Checks only the deflection of the beams and girders: the bars of the
    last ``xs beams`` of the model with the service moments read again from
    ETABS, and a results file of the deflections.

``xs columns``
    Reads what ``xs beams`` stored for the model, asks the column inputs,
    designs every column (the SMRF joint checks use the beam bars), and saves
    the results, the calculation report and the column schedule.

Between the two steps everything is kept in a binary store next to the model,
``<model> - design data.pkl``: the extracted tables, the inputs and the
results. It is internal; the files you save are made from it.
"""

from __future__ import annotations

import json
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass, field

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STORE_SUFFIX = " - design data.pkl"
# Columns that hold names: kept as text (everything else that looks like a
# number becomes one, as the old workbook did).
TEXT_COLUMNS = {
    "UniqueName", "Label", "Story", "Combo", "SectProp", "Name", "GUID", "Element",
    "UniquePtI", "UniquePtJ", "UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4",
    "BeamBay", "ColumnBay", "WallBay", "PointBay", "Beam", "Column", "DesignType",
    "Shape", "Material", "Tributary method", "Governed by",
}


def numeric_like(table: pd.DataFrame) -> pd.DataFrame:
    """Number-like text as numbers, except in the name columns."""
    table = table.copy()
    for column in table.columns:
        if column in TEXT_COLUMNS or table[column].dtype != object:
            continue
        values = table[column].replace({"None": None, "": None})
        converted = pd.to_numeric(values, errors="coerce")
        if converted.notna().sum() == values.notna().sum():
            table[column] = converted
        else:
            table[column] = values
    return table


@dataclass
class DesignStore:
    """What the beam and column steps share for one model."""

    model_path: str
    model_saved: float                      # model file time when extracted
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    beam_results: pd.DataFrame | None = None
    column_report: pd.DataFrame | None = None
    column_groups: list | None = None
    joint_results: pd.DataFrame | None = None

    @staticmethod
    def path_for(model_path: str) -> str:
        return os.path.splitext(os.path.normpath(model_path))[0] + STORE_SUFFIX

    @property
    def path(self) -> str:
        return self.path_for(self.model_path)

    def save(self) -> str:
        with open(self.path, "wb") as handle:
            pickle.dump(self, handle, protocol=pickle.HIGHEST_PROTOCOL)
        return self.path

    @classmethod
    def load(cls, model_path: str) -> "DesignStore | None":
        try:
            with open(cls.path_for(model_path), "rb") as handle:
                return pickle.load(handle)
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
            return None

    def is_stale(self) -> bool:
        """True when the model file was saved after the extraction."""
        try:
            return os.path.getmtime(self.model_path) > self.model_saved + 1.0
        except OSError:
            return False


def extract(connector, combos: list[str], options, members: list[str] | None = None,
            progress=None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Every table the beam and column design need, from the open model."""
    from etabs_api.workflows.exporter import ETABSDataExporter

    exporter = ETABSDataExporter(connector)
    if members is None:
        members = exporter.get_available_members()
    if progress:
        progress("Reading the analysis results and building the factored forces")
    notes = exporter.extract_all(combos, members, options)
    return {name: numeric_like(table) for name, table in exporter.tables.items()}, notes


# =============================================================================
# DIALOGS
# =============================================================================
BEAM_FIELDS = {
    "Beam main bar (mm)": ("dm", 28),
    "Beam stirrup (mm)": ("ds", 12),
    "Beam web (side) bar (mm)": ("dw", 12),
    "Web bar fy (MPa)": ("fyw", 414),
    "Beam cover (mm)": ("cc", 40),
}
COLUMN_FIELDS = {
    "Column main bar (mm)": ("dmain", 25),
    "Column tie (mm)": ("dties", 12),
    "Column cover (mm)": ("cover", 40),
}


def _settings_file() -> str:
    return os.path.join(os.path.expanduser("~"), ".xlwings_structural", "concrete_design.json")


def _last() -> dict:
    try:
        with open(_settings_file(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _remember(values: dict) -> None:
    saved = _last()
    saved.update(values)
    os.makedirs(os.path.dirname(_settings_file()), exist_ok=True)
    with open(_settings_file(), "w", encoding="utf-8") as handle:
        json.dump(saved, handle, indent=2)


def _ask_numbers(title: str, prompt: str, fields: dict, last: dict) -> dict | None:
    from utilities._gui_helpers import enter_values, show_warning

    defaults = {label: f"{float(last.get(key, value)):g}" for label, (key, value) in fields.items()}
    while True:
        typed = enter_values(title, prompt, list(fields), defaults)
        if typed is None:
            return None
        try:
            values = {key: float(typed[label]) for label, (key, _) in fields.items()}
            if min(values.values()) <= 0:
                raise ValueError
            return values
        except ValueError:
            show_warning("Every value must be a positive number.", title=title)
            defaults = typed


def attach_etabs():
    """The ETABS session that is running, as a connector; None with a message."""
    import comtypes.client

    from etabs_api.core.connection import ETABSConnector
    from utilities._gui_helpers import show_warning

    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
    try:
        etabs = helper.GetObject("CSI.ETABS.API.ETABSObject")
    except Exception:
        show_warning("ETABS is not running. Open the model in ETABS first.", title="Design")
        return None
    connector = ETABSConnector()
    connector.etabs_object, connector.sap_model, connector.is_connected = (
        etabs, etabs.SapModel, True)
    return connector


def model_path_of(connector) -> str | None:
    path = os.path.splitext(os.path.normpath(str(connector.sap_model.GetModelFilename())))[0]
    path += ".EDB"
    return path if os.path.isfile(path) else None


def _gravity_options(combos: list[str]) -> list[str]:
    """Combinations offered for the beam seismic shear: no seismic or wind term."""
    plain = [c for c in combos if not re.search(r"\b(EQ|RSA)\d|\bW[XY]\b", c)]
    return plain or combos


# =============================================================================
# BEAMS
# =============================================================================
def run_beams() -> DesignStore | None:
    """Extract, design the beams and save the results, the calculations and the schedules."""
    from design.beam_designer_aci318 import (
        ask_deflection_limit,
        design_beams,
        export_beam_dxf,
        export_beam_pdf,
        write_beam_results_xlsx,
    )
    from etabs_api.workflows.analysis_forces import ask_force_options
    from etabs_api.workflows.design_loop import uls_combinations
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_output_directory,
        show_warning,
    )

    title = "Beam Design"
    connector = attach_etabs()
    if connector is None:
        return None
    model_path = model_path_of(connector)
    if model_path is None:
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    last = _last()

    seismic = select_option(title, "Seismic combinations to design for (gravity and wind ULS "
                            "combinations are always included):",
                            ["Static (EQ)", "Response spectrum (RSA)", "Both"],
                            default_index=["EQ", "RSA", "Both"].index(last.get("seismic", "RSA")))
    if seismic is None:
        return None
    seismic_key = {"Static (EQ)": "EQ", "Response spectrum (RSA)": "RSA", "Both": "Both"}[seismic]
    names = list(dict.fromkeys(
        connector.get_data("Load Combination Definitions")["Name"].dropna().astype(str)))
    combos = uls_combinations(names, seismic_key)
    if not combos:
        show_warning("The model has no ULS combinations.", title=title)
        return None
    options = ask_force_options()
    if options is None:
        return None
    smrf = select_option(title, "SMRF (seismic) design? Gravity beams (on other beams only) "
                         "are designed for gravity in any case.", ["Yes", "No"],
                         default_index=0 if last.get("smrf", True) else 1)
    if smrf is None:
        return None
    smrf = smrf == "Yes"
    gravity = None
    if smrf:
        choices = _gravity_options(combos)
        previous = last.get("gravity_combo")
        gravity = select_option(title, "Gravity combination for the beam seismic shear:",
                                choices, default_index=choices.index(previous)
                                if previous in choices else 0)
        if gravity is None:
            return None
    bars = _ask_numbers(title, "Beam bars and cover.", BEAM_FIELDS, last)
    if bars is None:
        return None
    divisor = ask_deflection_limit()
    if divisor is None:
        return None
    folder = select_output_directory("Folder for the beam results, calculations and schedules")
    if not folder:
        return None
    _remember({"seismic": seismic_key, "smrf": smrf, "gravity_combo": gravity, **bars})

    stem = os.path.splitext(os.path.basename(model_path))[0]
    with LoadingWindow("Beam design") as window:
        start = time.time()
        tables, notes = extract(connector, combos, options, progress=window.update)
        store = DesignStore(model_path, os.path.getmtime(model_path), tables, {
            "combos": combos, "seismic": seismic_key, "smrf": smrf, "gravity_combo": gravity,
            "beam_bars": bars, "long_limit": divisor,
        })
        results = design_beams(tables, smrf, gravity, bars, divisor, progress=window.update)
        store.beam_results = results
        store.save()
        window.update("Saving 1 of 3: the results workbook (.xlsx)")
        xlsx = write_beam_results_xlsx(results, os.path.join(folder, f"{stem} - Beam Design.xlsx"))
        window.update("Saving 2 of 3: the beam schedules (.dxf)")
        dxf = export_beam_dxf(results, folder)
        window.update("Saving 3 of 3: the calculation report (.pdf, LaTeX)")
        pdf = export_beam_pdf(results, os.path.join(folder, f"{stem} - Beam Calculations.pdf"),
                              smrf, gravity)
    for note in notes:
        print(note)
    status = results.groupby("UniqueName")["Design_Status"].apply(
        lambda s: "OK" if (s.astype(str) == "OK").all() else "FAILED")
    print(f"Beams: {len(status)} designed, {(status == 'FAILED').sum()} failing "
          f"({time.time() - start:.0f} s)")
    print(f"Results: {xlsx}")
    print("Schedules: " + ", ".join(os.path.basename(p) for p in dxf))
    print(f"Calculations: {pdf}" if pdf else "Calculations: not written (check LaTeX)")
    print(f"Design data for the column step: {store.path}")
    return store


# =============================================================================
# DEFLECTION ONLY
# =============================================================================
DEFLECTION_REPORT_COLUMNS = ["Story", "UniqueName", "SectProp", "SupportStatus", "Width",
                             "Depth"]


def deflection_table(results: pd.DataFrame) -> pd.DataFrame:
    """One row per beam: the section and its deflection results."""
    from design.beam_deflection import DEFLECTION_COLUMNS

    top = results[results["Face"].astype(str).eq("TOP")] if "Face" in results.columns \
        else results.drop_duplicates("UniqueName")
    keep = [c for c in DEFLECTION_REPORT_COLUMNS if c in top.columns]
    keep += [c for c in DEFLECTION_COLUMNS if c in top.columns]
    table = top[keep].reset_index(drop=True)
    return table.rename(columns=DEFLECTION_COLUMNS)


def write_deflection_xlsx(table: pd.DataFrame, path: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "DEFLECTION"
    sheet.append(table.columns.tolist())
    for row in table.astype(object).where(pd.notna(table), None).itertuples(index=False):
        sheet.append(list(row))
    for index, name in enumerate(table.columns, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DEEBF7")
        sheet.column_dimensions[get_column_letter(index)].width = min(max(len(str(name)) + 2,
                                                                         10), 32)
    check = table.columns.get_loc("Deflection check") + 1 if "Deflection check" in table else 0
    for row in range(2, len(table) + 2):
        if check and str(sheet.cell(row=row, column=check).value) == "FAIL":
            sheet.cell(row=row, column=check).font = Font(color="C00000", bold=True)
    sheet.freeze_panes = "C2"
    book.save(path)
    return path


def run_deflection() -> pd.DataFrame | None:
    """Deflection checks only, with the bars of the last beam design of the model."""
    from design.beam_deflection import DEFLECTION_COLUMNS, add_deflection_columns
    from design.beam_designer_aci318 import ask_deflection_limit
    from etabs_api.workflows.exporter import ETABSDataExporter
    from utilities._gui_helpers import LoadingWindow, select_output_directory, show_warning

    title = "Deflection Check"
    connector = attach_etabs()
    if connector is None:
        return None
    model_path = model_path_of(connector)
    if model_path is None:
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    store = DesignStore.load(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    if store is None or store.beam_results is None or store.beam_results.empty:
        show_warning(f"No beam design for {stem}: the deflection needs the beam bars. "
                     "Run xs beams first.", title=title)
        return None
    divisor = ask_deflection_limit()
    if divisor is None:
        return None
    folder = select_output_directory("Folder for the deflection results")
    if not folder:
        return None
    with LoadingWindow("Deflection check") as window:
        window.update("Reading the service moments (DEF 100 to DEF 103) from ETABS")
        exporter = ETABSDataExporter(connector)
        beams = list(store.beam_results["UniqueName"].astype(str).unique())
        service = exporter.display_service_loads(beams)
        exporter.display_connectivity_data()
        if service is None:
            show_warning("The model has no deflection combinations or no beam results.",
                         title=title)
            return None
        service = numeric_like(service)
        connectivity = numeric_like(exporter.tables["CONNECTIVITY"])
        results = store.beam_results.drop(columns=[c for c in DEFLECTION_COLUMNS
                                                   if c in store.beam_results.columns])
        checked = add_deflection_columns(results, service, divisor, connectivity,
                                         progress=window.update)
        table = deflection_table(checked)
        window.update("Saving the deflection workbook (.xlsx)")
        path = write_deflection_xlsx(table, os.path.join(folder, f"{stem} - Deflection.xlsx"))
    status = table["Deflection check"].astype(str)
    print(f"Deflection: {len(table)} beams, {int((status == 'FAIL').sum())} failing "
          f"(limit L/360 live, L/{divisor} after partitions)")
    print(f"Results: {path}")
    return table


# =============================================================================
# COLUMNS
# =============================================================================
def run_columns() -> DesignStore | None:
    """Design the columns from the stored beam step and save the outputs."""
    from design.column_designer_aci318 import (
        ask_column_design_options,
        ask_inner_tie_style,
        design_columns,
        export_column_cad_drawings,
        export_column_pdf,
        write_column_results_xlsx,
    )
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_output_directory,
        show_warning,
    )

    title = "Column Design"
    connector = attach_etabs()
    if connector is None:
        return None
    model_path = model_path_of(connector)
    if model_path is None:
        show_warning("Save the ETABS model first: it has no file yet.", title=title)
        return None
    store = DesignStore.load(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    if store is None or store.beam_results is None or store.beam_results.empty:
        show_warning(f"No beam design for {stem}. Run xs beams first.", title=title)
        return None
    if store.is_stale():
        go_on = select_option(title, f"The model was saved after the beam design of {stem}. "
                              "The stored forces may be out of date.",
                              ["Stop (run xs beams again)", "Continue with the stored data"])
        if go_on is None or go_on.startswith("Stop"):
            return None
    last = _last()
    bars = _ask_numbers(title, "Column bars and cover.", COLUMN_FIELDS, last)
    if bars is None:
        return None
    answers = ask_column_design_options(normal_cover=bars["cover"])
    if answers is None:
        return None
    tie_style = ask_inner_tie_style()
    if tie_style is None:
        return None
    folder = select_output_directory("Folder for the column results, calculations and schedule")
    if not folder:
        return None
    _remember(bars)
    smrf = bool(store.inputs.get("smrf", True))

    with LoadingWindow("Column design") as window:
        start = time.time()
        report, groups, joints = design_columns(
            store.tables, store.beam_results, smrf, bars["dmain"], bars["dties"], bars["cover"],
            progress=window.update, continuous_vertical_bars=answers[0],
            bottom_story_cover=answers[1])
        store.column_report, store.column_groups, store.joint_results = report, groups, joints
        store.inputs.update({"column_bars": bars, "continuous_bars": answers[0],
                             "bottom_cover": answers[1]})
        store.save()
        window.update("Saving 1 of 3: the results workbook (.xlsx)")
        xlsx = write_column_results_xlsx(report, groups,
                                         os.path.join(folder, f"{stem} - Column Design.xlsx"))
        window.update("Saving 2 of 3: the column schedule (.dxf)")
        dxf = export_column_cad_drawings(report, folder, bars["dmain"], bars["cover"], smrf,
                                         tie_style, store.tables["CONNECTIVITY"])
        window.update("Saving 3 of 3: the calculation report (.pdf, LaTeX)")
        pdf = export_column_pdf(report, os.path.join(folder, f"{stem} - Column Calculations.pdf"),
                                smrf, bars["dmain"], bars["dties"], bars["cover"])
    failing = report.groupby("UniqueName")["Column_Design_Status"].apply(
        lambda s: (s.astype(str) == "FAIL").any())
    print(f"Columns: {len(failing)} designed, {int(failing.sum())} failing "
          f"({time.time() - start:.0f} s)")
    print(f"Results: {xlsx}")
    print("Schedule: " + ", ".join(os.path.basename(p) for p in dxf))
    print(f"Calculations: {pdf}" if pdf else "Calculations: not written (check LaTeX)")
    return store


if __name__ == "__main__":
    run_beams()
