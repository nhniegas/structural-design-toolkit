import os
import sys
import comtypes
import comtypes.client
import pandas as pd


class ETABSConnector:
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
        self.program_path = (
            r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"
        )

        # Create the ETABS API Helper Object
        try:
            helper = comtypes.client.CreateObject("ETABSv1.Helper")
            helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)

            # Create a new instance of ETABS
            self.etabs_object = helper.CreateObject(self.program_path)

            # Create SapModel Object
            self.sap_model = self.etabs_object.SapModel
            self.sap_model.InitializeNewModel

            # Set connection to true
            self.is_connected = True

        except Exception as e:
            print(f"Error connecting to ETABS: {e}")
            self.is_connected = False
            return False

    def open_model(self, model_path: str):
        # Verify model path exists
        self.model_path = model_path
        if not os.path.exists(self.model_path):
            print(f"Model file not found: {self.model_path}")
            return False

        self.etabs_object.ApplicationStart()

        try:
            # Open the model
            self.sap_model.SetPresentUnits(6)  # Set units to kN, m, C
            self.sap_model.File.OpenFile(self.model_path)
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model opened successfully: {self.model_path}")
            return True

        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error opening model: {e}")
            return False

    def run_analysis(self):
        try:
            # Run the analysis
            run_info = self.sap_model.Analyze.RunAnalysis()
            if run_info == 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Analysis completed successfully")
                return True
            else:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Analysis failed with code: {run_info}")
                return False

        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running analysis: {e}")
            return False

    def clear_load_combinations(self, load_combos):
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
        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error clearing load combinations for design: {e}")

    def set_load_combinations(self, load_combos):
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

        except Exception as e:
            print(f"Error setting load combinations for design: {e}")

    def run_concrete_design(self):
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

        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error running concrete design: {e}")
            return False

    def get_data(self, table_name):
        try:
            # Get all raw data
            data = self.sap_model.DatabaseTables.GetTableForDisplayArray(
                table_name, [], "", 0
            )

            # Check if retrieval was successful
            if data[5] == 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Data Retrieved Successfully")

                # Store data in a pandas dataframe
                headers = data[2]
                table_data = data[4]
                num_columns = len(headers)
                row_list = []
                for i in range(0, len(table_data), num_columns):
                    row = table_data[i : i + num_columns]
                    row_list.append(row)
                dataframe = pd.DataFrame(row_list, columns=headers)
                return dataframe

            else:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Failed to retrieve data. Error code: {data[6]}")

        except Exception as e:
            return {"error": str(e)}

    def get_unique_name(self):
        try:
            ret = self.sap_model.SelectObj.GetSelected()

            if ret[0] > 0:
                func_name = sys._getframe().f_code.co_name
                print(f"[{func_name}] Unique name retrieved successfully: {ret[2][0]}")
                return ret[2][0]
            return None

        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error occurred while retrieving unique name: {e}")
            pass

    def change_unique_name(
        self, extracted_unique_name, cmb_tag_name, cmb_tag_number, cmb_tag_letter
    ):
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

        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(
                f"[{func_name}] Failed to rename {extracted_unique_name}. Error code: {e}"
            )

    def clear_selection(self):
        if self.sap_model is None:
            return

        try:
            self.sap_model.SelectObj.ClearSelection()
        except:
            pass

    def refresh_view(self):
        if self.sap_model is None:
            return

        try:
            self.sap_model.View.RefreshView()
        except:
            pass

    def close_model(self):
        try:
            self.sap_model.File.Save()
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Model saved successfully.")
            self.etabs_object.ApplicationExit(False)
            print(f"[{func_name}] ETABS application closed successfully.")

            self.sap_model = None
            self.etabs_object = None
        except Exception as e:
            func_name = sys._getframe().f_code.co_name
            print(f"[{func_name}] Error closing model: {e}")
            return False


if __name__ == "__main__":
    test_etabs = ETABSConnector()
