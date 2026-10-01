"""ETABS COM connection and database access helpers."""

from __future__ import annotations

import os
import sys

import comtypes
import comtypes.client
import pandas as pd

from utilities._gui_helpers import LoadingWindow, select_etabs_file
from .analysis import Analysis
from .assignments import Assignments
from .database import DatabaseTables
from .geometry import Geometry
from .loads import Loads
from .properties import Properties
from .results import Results
from .selection import Selection
from .stories_grids import StoriesGrids


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
        self.program_path = r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"
        try:
            with LoadingWindow("Connecting to Model.."):
                helper = comtypes.client.CreateObject("ETABSv1.Helper")
                helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
                if attach_to_existing:
                    self.etabs_object = helper.GetObject("CSI.ETABS.API.ETABSObject")
                    self.sap_model = self.etabs_object.SapModel
                    self.is_connected = True
                    return True
        except Exception:
            pass

        model_path = select_etabs_file()
        if not model_path:
            return False
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
        self.model_path = model_path
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

            all_chunks = []
            func_name = sys._getframe().f_code.co_name
            if load_combinations_for_display:
                for combo in load_combinations_for_display:
                    self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay(
                        [combo]
                    )
                    data = self.sap_model.DatabaseTables.GetTableForDisplayArray(
                        table_name, [], "", 0
                    )
                    if data[5] == 0:
                        headers = data[2]
                        table_data = data[4]
                        num_columns = len(headers)
                        if num_columns > 0 and table_data:
                            row_list = [
                                table_data[i : i + num_columns]
                                for i in range(0, len(table_data), num_columns)
                            ]
                            all_chunks.append(pd.DataFrame(row_list, columns=headers))
                    else:
                        print(
                            f"[{func_name}] Failed to retrieve data for combo: {combo}. Error: {data[6]}"
                        )
                if all_chunks:
                    print(f"[{func_name}] All chunked data retrieved successfully")
                    return pd.concat(all_chunks, ignore_index=True)
                return pd.DataFrame()

            data = self.sap_model.DatabaseTables.GetTableForDisplayArray(
                table_name, [], "", 0
            )
            if data[5] == 0:
                headers = data[2]
                table_data = data[4]
                num_columns = len(headers)
                if num_columns > 0 and table_data:
                    row_list = [
                        table_data[i : i + num_columns]
                        for i in range(0, len(table_data), num_columns)
                    ]
                    print(f"[{func_name}] Data retrieved successfully")
                    return pd.DataFrame(row_list, columns=headers)
                return pd.DataFrame()
            print(f"[{func_name}] Failed to retrieve data. Error: {data[6]}")
            return pd.DataFrame()
        except Exception as exc:
            return {"error": str(exc)}

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
