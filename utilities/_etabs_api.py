"""
ETABS API Integration Module.
Provides wrapper classes to manage COM client connections, program execution,
and structural model data exchange for CSI ETABS.
"""

import os
import sys
import comtypes
import comtypes.client
import pandas as pd
from utilities._gui_helpers import LoadingWindow

from utilities._gui_helpers import select_etabs_file


class ETABSConnector:
    """Manages API connections and interface methods for CSI ETABS."""

    def __init__(self):
        # Initialize ETABS API connection variables
        self.etabs_object = None
        self.sap_model = None
        self.program_path = ""
        self.model_path = ""
        self.is_connected = False

        # Design parameters for concrete design
        self.load_combinations = []
        self.concrete_design_code = "ACI 318-14"

    def connect(self):
        """Attaches to or launches a new ETABS application instance via COM API."""

        self.program_path = (
            r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"
        )

        # Create the ETABS API Helper Object
        try:
            with LoadingWindow("Connecting to Model.."):
                helper = comtypes.client.CreateObject("ETABSv1.Helper")
                helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
                self.etabs_object = helper.GetObject("CSI.ETABS.API.ETABSObject")
                self.sap_model = self.etabs_object.SapModel
                self.is_connected = True
                return True

        except Exception:  # pylint: disable=broad-exception-caught
            model_path = select_etabs_file()
            if not model_path:
                return

            with LoadingWindow("Opening Etabs Model..."):
                helper = comtypes.client.CreateObject("ETABSv1.Helper")
                helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)

                # Create a new instance of ETABS
                self.etabs_object = helper.CreateObject(self.program_path)

                # Create SapModel Object
                self.sap_model = self.etabs_object.SapModel

                # Set connection to true
                self.is_connected = True

                self.open_model(model_path)
                self.run_analysis()
            return True

    def open_model(self, model_path: str):
        """Opens an ETABS model file at the specified path and sets units to kN-m."""
        # Verify model path exists
        self.model_path = model_path
        if not os.path.exists(self.model_path):
            print(f"Model file not found: {self.model_path}")
            return False

        self.etabs_object.ApplicationStart()

        try:
            # Open the model
            self.sap_model.File.OpenFile(self.model_path)
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model opened successfully: {self.model_path}")
            return True

        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error opening model: {e}")
            return False

    def run_analysis(self):
        """Executes structural analysis in the active ETABS model."""
        try:
            with LoadingWindow("Running Analysis Model..."):
                # Run the analysis
                self.sap_model.Analyze.SetSolverOption_3(2, 0, 0, 0, 0)
                run_info = self.sap_model.Analyze.RunAnalysis()
                if run_info == 0:
                    func_name = sys._getframe().f_code.co_name
                    print(f"[{func_name}] Analysis completed successfully")
                    return True
                else:
                    func_name = sys._getframe().f_code.co_name
                    print(f"[{func_name}] Analysis failed with code: {run_info}")
                    return False

        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running analysis: {e}")
            return False

    def clear_load_combinations(self, load_combos):
        """Clears strength load combinations for concrete design."""
        try:
            for combo in load_combos:
                ret = self.sap_model.DesignConcrete.SetComboStrength(combo, False)

                if ret != 0:
                    func_name = sys._getframe().f_code.co_name
                    print(
                        f"[{func_name}] Failed to clear load combination {combo} for design. Error code: {ret}"
                    )
                else:
                    func_name = sys._getframe().f_code.co_name
                    print(
                        f"[{func_name}] Load combination {combo} cleared for design successfully."
                    )
        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error clearing load combinations for design: {e}")

    def set_load_combinations(self, load_combos):
        """Sets active strength load combinations for concrete design."""
        try:
            prev_combo = ""
            for combo in load_combos:
                current_combo = combo
                if current_combo != prev_combo:
                    ret = self.sap_model.DesignConcrete.SetComboStrength(combo, True)

                    if ret != 0:
                        print(
                            f"Failed to set load combination {combo} for design. Error code: {ret}"
                        )
                    else:
                        print(f"Load combination {combo} set for design successfully.")
                prev_combo = current_combo

        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Error setting load combinations for design: {e}")

    def run_concrete_design(self):
        """Sets the concrete design code and executes design in ETABS."""
        try:
            self.sap_model.DesignConcrete.SetCode(self.concrete_design_code)

            run_info = self.sap_model.DesignConcrete.StartDesign()
            if run_info == 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Concrete design completed successfully")
                return True
            else:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Concrete design failed with code: {run_info}")
                return False

        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running concrete design: {e}")
            return False

    def get_data(self, table_name, load_combinations_for_display=None):
        """Retrieves raw database display table from ETABS model in safe chunks or as a whole."""
        try:
            # 1. Clear all previously selected cases, patterns, and combinations using empty/null parameters
            self.sap_model.DatabaseTables.SetLoadCasesSelectedForDisplay([])
            self.sap_model.DatabaseTables.SetLoadPatternsSelectedForDisplay([])
            self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay([])

            all_chunks = []
            func_name = sys._getframe().f_code.co_name

            # 2. Check if a list of combinations was provided
            if load_combinations_for_display:
                # Loop through the list of load combos to extract data safely
                for combo in load_combinations_for_display:

                    # Set ONLY the current combination for extraction
                    self.sap_model.DatabaseTables.SetLoadCombinationsSelectedForDisplay(
                        [combo]
                    )

                    # NOW request the data (API only pulls this specific combo)
                    data = self.sap_model.DatabaseTables.GetTableForDisplayArray(
                        table_name, [], "", 0
                    )

                    # Process the data chunk if successful
                    if data[5] == 0:
                        headers = data[2]
                        table_data = data[4]
                        num_columns = len(headers)

                        if num_columns > 0 and table_data:
                            # Fast list comprehension to group flat array into rows
                            row_list = [
                                table_data[i : i + num_columns]
                                for i in range(0, len(table_data), num_columns)
                            ]
                            df_chunk = pd.DataFrame(row_list, columns=headers)
                            all_chunks.append(df_chunk)
                    else:
                        print(
                            f"[{func_name}] Failed to retrieve data for combo: {combo}. Error: {data[6]}"
                        )

                # Stitch all chunks into one massive final DataFrame
                if all_chunks:
                    final_dataframe = pd.concat(all_chunks, ignore_index=True)
                    print(f"[{func_name}] All chunked data retrieved successfully")
                    return final_dataframe
                else:
                    return pd.DataFrame()

            # 3. If NO combinations were provided, extract the table normally in one piece
            else:
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
                        final_dataframe = pd.DataFrame(row_list, columns=headers)
                        print(f"[{func_name}] Data retrieved successfully")
                        return final_dataframe
                    else:
                        return pd.DataFrame()
                else:
                    print(f"[{func_name}] Failed to retrieve data. Error: {data[6]}")
                    return pd.DataFrame()

        except Exception as e:
            return {"error": str(e)}

    def get_unique_name(self):
        """Retrieves unique names of currently selected ETABS objects."""
        try:
            ret = self.sap_model.SelectObj.GetSelected()

            if ret[0] > 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Unique name retrieved successfully: {ret[2][0]}")
                return ret[2][0]
            return None

        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error occurred while retrieving unique name: {e}")
            pass

    def change_unique_name(
        self, extracted_unique_name, cmb_tag_name, cmb_tag_number, cmb_tag_letter
    ):
        """Formats and updates element unique name tags."""
        try:
            current_unique_name = extracted_unique_name

            if cmb_tag_letter == "-":
                new_unique_name = f"{cmb_tag_name}-{cmb_tag_number}"
            else:
                new_unique_name = f"{cmb_tag_name}-{cmb_tag_number}{cmb_tag_letter}"

            ret = self.sap_model.FrameObj.ChangeName(
                current_unique_name, new_unique_name
            )
            if ret == 0:
                func_name = sys._getframe().f_code.co_name
                print(
                    f"[{func_name}] Renamed {extracted_unique_name} to {new_unique_name} successfully."
                )
                return ret

        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(
                f"[{func_name}] Failed to rename {extracted_unique_name}. Error code: {e}"
            )

    def clear_selection(self):
        """Clears all active element selections in the ETABS viewport."""
        if self.sap_model is None:
            return

        try:
            self.sap_model.SelectObj.ClearSelection()
        except:  # pylint: disable=broad-exception-caught
            pass

    def refresh_view(self):
        """Refreshes all active display views in ETABS."""
        if self.sap_model is None:
            return

        try:
            self.sap_model.View.RefreshView()
        except:  # pylint: disable=broad-exception-caught
            pass

    def close_model(self):
        """Saves structural model and terminates ETABS application."""
        try:
            self.sap_model.File.Save()
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model saved successfully.")
            self.etabs_object.ApplicationExit(False)
            print(f"[{func_name}] ETABS application closed successfully.")

            self.sap_model = None
            self.etabs_object = None
        except Exception as e:  # pylint: disable=broad-exception-caught
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error closing model: {e}")
            return False


if __name__ == "__main__":
    test_etabs = ETABSConnector()
