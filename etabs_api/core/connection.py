"""ETABS COM connection and database access helpers."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

import comtypes
import comtypes.client
import pandas as pd

from utilities._gui_helpers import LoadingWindow, select_etabs_file
from .analysis import Analysis
from .assignments import Assignments
from .database import DatabaseTables
from .geometry import Geometry
from .helpers import ensure_success
from .loads import Loads
from .properties import Properties
from .results import Results
from .selection import Selection
from .stories_grids import StoriesGrids


# Used only when no ETABS session is running and one has to be started.
# Set the ETABS_PROGRAM_PATH environment variable to use another installation.
DEFAULT_ETABS_PROGRAM_PATH = (
    r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"
)


# ETABS unit-system codes (eUnits). The API returns table values in the model's
# "present units", which are independent of the display units shown in the ETABS
# window. Extraction always reads in N-mm so the conversions to kN and kN-m in
# exporter.py are valid whatever the model was created in.
UNIT_NAMES = {
    1: "lb-in", 2: "lb-ft", 3: "kip-in", 4: "kip-ft",
    5: "kN-mm", 6: "kN-m", 7: "kgf-mm", 8: "kgf-m",
    9: "N-mm", 10: "N-m", 11: "tonf-mm", 12: "tonf-m",
    13: "kN-cm", 14: "kgf-cm", 15: "N-cm", 16: "tonf-cm",
}
EXTRACTION_UNITS = 9  # N, mm, C


class ETABSConnector:
    """Manage a CSI ETABS COM connection and expose common ETABS operations."""

    def __init__(self):
        self.etabs_object = None
        self.sap_model = None
        self.program_path = ""
        self.model_path = ""
        self.is_connected = False
        self.load_combinations = []
        self.concrete_design_code = "ACI 318-14"
        self.analysis = Analysis(self)
        self.database = DatabaseTables(self)
        self.loads = Loads(self)
        self.results = Results(self)
        self.selection = Selection(self)
        self.properties = Properties(self)
        self.assignments = Assignments(self)
        self.geometry = Geometry(self)
        self.stories_grids = StoriesGrids(self)

    def ensure_connected(self):
        """Raise when no active ETABS model is available."""
        if not self.is_connected or self.sap_model is None:
            raise ConnectionError("ETABS is not connected. Call connect() first.")

    def connect(self, attach_to_existing: bool = True):
        """Attach to an active ETABS session or open a model file."""
        self.program_path = os.environ.get(
            "ETABS_PROGRAM_PATH", DEFAULT_ETABS_PROGRAM_PATH
        )
        try:
            with LoadingWindow("Connecting to Model.."):
                helper = comtypes.client.CreateObject("ETABSv1.Helper")
                helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
                if attach_to_existing:
                    self.etabs_object = helper.GetObject("CSI.ETABS.API.ETABSObject")
                    self.sap_model = self.etabs_object.SapModel
                    self.is_connected = True
                    return True
        except Exception as exc:
            # No running ETABS session to attach to: fall through and open a model.
            print(f"[connect] Could not attach to a running ETABS session: {exc}")

        model_path = select_etabs_file()
        if not model_path:
            return False
        if not os.path.exists(self.program_path):
            raise FileNotFoundError(
                f"ETABS was not found at {self.program_path!r}. Set the "
                "ETABS_PROGRAM_PATH environment variable to your ETABS.exe."
            )
        with LoadingWindow("Opening Etabs Model..."):
            helper = comtypes.client.CreateObject("ETABSv1.Helper")
            helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
            self.etabs_object = helper.CreateObject(self.program_path)
            self.sap_model = self.etabs_object.SapModel
            self.is_connected = True
            self.open_model(model_path)
            self.run_analysis()
        return True

    def open_model(self, model_path: str):
        """Open an ETABS model file."""
        # ETABS misreads a path with forward slashes as relative to its own folder.
        self.model_path = os.path.normpath(model_path)
        if not os.path.exists(self.model_path):
            print(f"Model file not found: {self.model_path}")
            return False
        self.etabs_object.ApplicationStart()
        try:
            self.sap_model.File.OpenFile(self.model_path)
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model opened successfully: {self.model_path}")
            return True
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error opening model: {exc}")
            return False

    def get_status(self) -> dict:
        """Return a lightweight connection/status snapshot."""
        return {
            "connected": bool(self.is_connected and self.sap_model is not None),
            "model_path": self.model_path,
            "program_path": self.program_path,
            "has_model": self.sap_model is not None,
        }

    def save_model(self):
        """Save the active ETABS model."""
        if self.sap_model is None:
            return False
        try:
            self.sap_model.File.Save()
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model saved successfully.")
            return True
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error saving model: {exc}")
            return False

    def run_analysis(self):
        """Run structural analysis in the active ETABS model."""
        try:
            with LoadingWindow("Running Analysis Model..."):
                self.sap_model.Analyze.SetSolverOption_3(2, 0, 0, 0, 0)
                run_info = self.sap_model.Analyze.RunAnalysis()
                if run_info == 0:
                    func_name = sys._getframe().f_code.co_name
                    print(f"[{func_name}] Analysis completed successfully")
                    return True
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Analysis failed with code: {run_info}")
                return False
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running analysis: {exc}")
            return False

    def clear_load_combinations(self, load_combos):
        """Clear strength combinations before applying a filtered set."""
        try:
            for combo in load_combos:
                ret = self.sap_model.DesignConcrete.SetComboStrength(combo, False)
                if ret != 0:
                    print(
                        f"[clear_load_combinations] Failed to clear load combination {combo} for design. Error code: {ret}"
                    )
        except Exception as exc:
            print(f"[clear_load_combinations] Error clearing load combinations: {exc}")

    def set_load_combinations(self, load_combos):
        """Activate a set of strength combinations for concrete design."""
        try:
            prev_combo = ""
            for combo in load_combos:
                if combo != prev_combo:
                    ret = self.sap_model.DesignConcrete.SetComboStrength(combo, True)
                    if ret != 0:
                        print(
                            f"Failed to set load combination {combo} for design. Error code: {ret}"
                        )
                prev_combo = combo
        except Exception as exc:
            print(f"Error setting load combinations for design: {exc}")

    def run_concrete_design(self):
        """Run the ETABS concrete design engine."""
        try:
            self.sap_model.DesignConcrete.SetCode(self.concrete_design_code)
            run_info = self.sap_model.DesignConcrete.StartDesign()
            if run_info == 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Concrete design completed successfully")
                return True
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Concrete design failed with code: {run_info}")
            return False
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running concrete design: {exc}")
            return False

    def get_data(self, table_name, load_combinations_for_display=None):
        """Read an ETABS database table into a DataFrame."""
        try:
            self.sap_model.DatabaseTables.SetLoadCasesSelectedForDisplay([])
            self.sap_model.DatabaseTables.SetLoadPatternsSelectedForDisplay([])
            self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay([])

            if load_combinations_for_display:
                chunks = [
                    self.get_design_forces(table_name, combo)
                    if table_name in {"Design Forces - Beams", "Design Forces - Columns"}
                    else self._get_table_for_selected_combination(table_name, combo)
                    for combo in load_combinations_for_display
                ]
                chunks = [chunk for chunk in chunks if not chunk.empty]
                return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()

            return self._read_database_table(table_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to read ETABS table {table_name!r}.") from exc

    def get_units(self) -> int:
        """Return the model's present (API) unit-system code; see ``UNIT_NAMES``."""
        return int(self.sap_model.GetPresentUnits())

    @contextmanager
    def extraction_units(self):
        """Read in N-mm for the duration of the block, then restore the model's units.

        Does nothing when the model is already in N-mm. The display units in the
        ETABS window are not affected.
        """
        original = self.get_units()
        if original == EXTRACTION_UNITS:
            yield
            return
        ensure_success(
            self.sap_model.SetPresentUnits(EXTRACTION_UNITS),
            f"SetPresentUnits({UNIT_NAMES[EXTRACTION_UNITS]})",
        )
        try:
            yield
        finally:
            ensure_success(
                self.sap_model.SetPresentUnits(original),
                f"SetPresentUnits({UNIT_NAMES.get(original, original)})",
            )

    def _read_database_table(self, table_name: str) -> pd.DataFrame:
        """Read one ETABS database table, in N-mm, with the current display selection."""
        with self.extraction_units():
            return self.database.get_table(table_name)

    def _get_table_for_selected_combination(
        self, table_name: str, load_combination: str
    ) -> pd.DataFrame:
        """Read a non-design table after selecting one display combination."""
        self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay(
            [load_combination]
        )
        return self._read_database_table(table_name)

    def get_design_forces(
        self, table_name: str, load_combination: str
    ) -> pd.DataFrame:
        """Read one design-force table for exactly one load combination.

        ETABS does not reliably return frame design-force tables when multiple
        combinations are selected in one display request. Selecting one
        combination and reading it immediately avoids mixed schemas and keeps
        the combination associated with every returned row.
        """
        if table_name not in {"Design Forces - Beams", "Design Forces - Columns"}:
            raise ValueError(
                "table_name must be 'Design Forces - Beams' or "
                "'Design Forces - Columns'."
            )
        if not load_combination:
            raise ValueError("load_combination must not be empty.")

        self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay(
            [load_combination]
        )
        data = self._read_database_table(table_name)
        if data.empty:
            return data

        identifier = next(
            (
                column
                for column in ("UniqueName", "Beam", "Column", "Label", "Name")
                if column in data.columns
            ),
            None,
        )
        if identifier is None:
            raise RuntimeError(
                f"{table_name} for combination {load_combination!r} has no member "
                "identifier column."
            )
        if identifier != "UniqueName":
            data = data.rename(columns={identifier: "UniqueName"})
        if "Combo" not in data.columns:
            data.insert(0, "Combo", load_combination)
        return data

    def get_unique_name(self):
        """Return the first selected ETABS object unique name."""
        try:
            ret = self.sap_model.SelectObj.GetSelected()
            if ret[0] > 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Unique name retrieved successfully: {ret[2][0]}")
                return ret[2][0]
            return None
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error occurred while retrieving unique name: {exc}")

    def change_unique_name(
        self, extracted_unique_name, cmb_tag_name, cmb_tag_number, cmb_tag_letter
    ):
        """Rename an ETABS frame object."""
        try:
            if cmb_tag_letter == "-":
                new_unique_name = f"{cmb_tag_name}-{cmb_tag_number}"
            else:
                new_unique_name = f"{cmb_tag_name}-{cmb_tag_number}{cmb_tag_letter}"
            ret = self.sap_model.FrameObj.ChangeName(extracted_unique_name, new_unique_name)
            if ret == 0:
                func_name = sys._getframe().f_code.co_name
                print(
                    f"[{func_name}] Renamed {extracted_unique_name} to {new_unique_name} successfully."
                )
                return ret
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Failed to rename {extracted_unique_name}. Error code: {exc}")

    def clear_selection(self):
        """Clear selected objects in ETABS."""
        if self.sap_model is None:
            return
        try:
            self.sap_model.SelectObj.ClearSelection()
        except Exception:
            pass

    def refresh_view(self):
        """Refresh the ETABS viewport."""
        if self.sap_model is None:
            return
        try:
            self.sap_model.View.RefreshView()
        except Exception:
            pass

    def call_api(self, interface_path: str, method_name: str, *args, **kwargs):
        """Call any ETABS COM method by interface path and method name.

        This is a low-level escape hatch for ETABS methods that are not wrapped
        by the typed helpers in this package.
        """
        if self.sap_model is None:
            raise RuntimeError("ETABS is not connected.")
        target = self.sap_model
        for part in str(interface_path).split("."):
            target = getattr(target, part)
        method = getattr(target, method_name)
        return method(*args, **kwargs)

    def close_model(self):
        """Save and close the ETABS application."""
        try:
            self.sap_model.File.Save()
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model saved successfully.")
            self.etabs_object.ApplicationExit(False)
            print(f"[{func_name}] ETABS application closed successfully.")
            self.sap_model = None
            self.etabs_object = None
        except Exception as exc:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error closing model: {exc}")
            return False
