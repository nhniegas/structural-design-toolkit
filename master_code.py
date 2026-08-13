"""
Main orchestration module for the STRUCTURAL ANALYSIS TOOL.
Handles Excel interfacing via xlwings, data processing with pandas,
and PDF report generation via PyLaTeX.
"""
import re
import subprocess
import tkinter as tk
from tkinter import filedialog
import xlwings as xw
import pandas as pd
import numpy as np
from pylatex import (
    Command,
    Document,
    Itemize,
    Section,
    Subsection,
    Table,
    Tabular,
    Center,
    FlushLeft,
    UnsafeCommand,
)
from pylatex.utils import NoEscape


class WindLoadCalculatorDirectionalASCE7:
    """Calculates wind loads on structures per ASCE 7 provisions."""
    def __init__(
        self,
        target_sheet,
        table_vel_pres_coef,
        wall_press_coeff_data,
        table_int_pres_coef,
        table_roof_over_10,
        table_roof_under_10,
        building_class,
        basic_wind_speed,
        enclosure_class,
        exposure_category,
        wind_dir_factor,
        topographic_factor,
        ground_elevation_factor,
        gust_effect_factor,
        velocity_pressure,
        internal_pressure_coefficient_pos,
        internal_pressure_coefficient_neg,
        l_input,
        b_input,
        ridge_direction_input,
        raw_heights,
        eave_height,
        apex_height,
    ):
        """
        Initializes the calculator and binds it to the active Excel sheet.
        """
        self.sheet = target_sheet
        self.table_vel_pres_coef = self.excel_to_dataframe(
            self.sheet, table_vel_pres_coef
        )
        self.wall_press_coeff_data = self.excel_to_dataframe(
            self.sheet, wall_press_coeff_data
        )
        self.table_int_pres_coef = self.excel_to_dataframe(
            self.sheet, table_int_pres_coef
        )
        self.table_roof_over_10 = self.excel_to_dataframe(
            self.sheet, table_roof_over_10
        )
        self.table_roof_under_10 = self.excel_to_dataframe(
            self.sheet, table_roof_under_10
        )

        self.building_class = self.sheet.range(building_class).value
        self.basic_wind_speed = self.sheet.range(basic_wind_speed).value
        self.enclosure_class = self.sheet.range(enclosure_class).value
        self.exposure_category = self.sheet.range(exposure_category).value
        self.wind_dir_factor = self.sheet.range(wind_dir_factor).value
        self.topographic_factor = self.sheet.range(topographic_factor).value
        self.ground_elevation_factor = self.sheet.range(ground_elevation_factor).value
        self.gust_effect_factor = self.sheet.range(gust_effect_factor).value
        self.velocity_pressure = self.sheet.range(velocity_pressure).value
        self.internal_pressure_coefficient_pos = self.sheet.range(
            internal_pressure_coefficient_pos
        ).value
        self.internal_pressure_coefficient_neg = self.sheet.range(
            internal_pressure_coefficient_neg
        ).value

        self.l_input = self.sheet.range(l_input).value
        self.b_input = self.sheet.range(b_input).value
        self.ridge_direction_input = self.sheet.range(ridge_direction_input).value

        self.raw_heights = self.sheet.range(raw_heights).value
        self.eave_height = self.sheet.range(eave_height).value
        self.apex_height = self.sheet.range(apex_height).value
        self.mean_roof_height = (self.eave_height + self.apex_height) / 2

        self.heights_list = []
        self.exposure_input = None
        self.vel_pres = None

        self.gcpi_pos: float
        self.gcpi_neg: float

    # --- HELPER FUNCTION ---
    def excel_to_dataframe(self, sheet_name, start_cell):
        """Grabs a table from Excel and converts it to a Pandas DataFrame."""
        wb = xw.Book.caller()
        sheet = wb.sheets[sheet_name]
        df = sheet.range(start_cell).expand("table").options(pd.DataFrame).value
        return df

    # --- HELPER FUNCTION ---
    def paste_dataframe_with_border(
        self, sheet_obj, dataframe_to_paste, target_cell_address, table_title=None
    ):
        """
        Pastes a table title and DataFrame to Excel.
        Applies outside borders and vertical column dividers only.
        """
        # 1. Parse the column letter and row number from the address
        match = re.match(r"([A-Za-z]+)([0-9]+)", target_cell_address)
        if not match:
            raise ValueError(f"Invalid cell address: {target_cell_address}")

        col_letter = match.group(1).upper()
        current_row = int(match.group(2))

        # 2. Paste and format the title (if one was provided)
        if table_title:
            title_cell = f"{col_letter}{current_row}"
            sheet_obj.range(title_cell).value = table_title
            sheet_obj.range(title_cell).api.Font.Bold = True
            current_row += 1

        # The target address for the DataFrame
        df_target_address = f"{col_letter}{current_row}"

        # 3. Paste the dataframe
        sheet_obj.range(df_target_address).options(
            index=False
        ).value = dataframe_to_paste

        # 4. Calculate table dimensions
        num_rows = len(dataframe_to_paste.index) + 1
        num_cols = len(dataframe_to_paste.columns)

        # 5. Grab the pasted range
        pasted_range = sheet_obj.range(df_target_address).resize(
            row_size=num_rows, column_size=num_cols
        )

        # --- BORDER FORMATTING ---
        # First, clear any weird formatting that might be lingering in those cells
        pasted_range.api.Borders.LineStyle = (
            -4142
        )  # -4142 is Excel's internal code for "No Border"

        # Apply Outside Borders (7, 8, 9, 10) and Inside Vertical (11). We explicitly skip 12.
        for border_id in [7, 8, 9, 10, 11]:
            pasted_range.api.Borders(border_id).Weight = 2  # 2 is xlThin

        # Optional but recommended: Add a single horizontal line under the Header Row
        header_range = sheet_obj.range(df_target_address).resize(
            row_size=1, column_size=num_cols
        )
        header_range.api.Borders(9).Weight = 2  # 9 is the Bottom Edge
        # -------------------------

        # 6. Calculate the next starting cell
        next_start_row = current_row + num_rows + 1

        return f"{col_letter}{next_start_row}"

        # --- HELPER FUNCTION ---

    # --- HELPER FUNCTION ---

    # --- HELPER FUNCTION ---
    def clear_output_zones(self, sheet_obj, start_cell_address, last_col="K"):
        """
        Completely wipes data, borders, and formatting from the starting cell
        downwards to prepare a clean canvas for dynamic table pasting.
        """
        # 1. Parse the starting column and row (e.g., "B24" -> "B" and 24)
        match = re.match(r"([A-Za-z]+)([0-9]+)", start_cell_address)
        if not match:
            raise ValueError(f"Invalid cell address: {start_cell_address}")

        start_col = match.group(1).upper()
        start_row = match.group(2)

        # 2. Define the block to wipe clean (e.g., "B24:K1000")
        # Adjust 'last_col' if your tables extend wider than column K!
        clear_range = f"{start_col}{start_row}:{last_col}1000"

        # 3. .clear() wipes EVERYTHING (data, borders, colors).
        # (Note: If you ever want to keep colors/borders and ONLY delete text,
        # you would use .clear_contents() instead).
        sheet_obj.range(clear_range).clear()

        print(f"Output canvas cleared from {clear_range}.")

    # --- HELPER FUNCTION ---
    def interpolate_table_value(
        self, reference_table, target_column_name, lookup_input_value
    ):
        """
        Safely handles exact match or linear interpolation for a given input
        against a reference DataFrame's index and selected data column.
        """
        try:
            # 1. Clean and check column constraints
            col_name = str(target_column_name).strip()
            if col_name not in reference_table.columns:
                raise KeyError(
                    f"Column '{col_name}' does not exist in the provided reference table."
                )

            # 2. Extract arrays from the reference dataframe
            # x_pts = table index (e.g. standard heights: 0, 4.6, 6.1...)
            # y_pts = data column values corresponding to those indexes
            x_pts = np.array(reference_table.index, dtype=float)
            y_pts = np.array(reference_table[col_name], dtype=float)

            # 3. Use numpy's 1D linear interpolation engine
            target_val = float(lookup_input_value)
            interpolated_result = float(np.interp(target_val, x_pts, y_pts))

            return interpolated_result

        except Exception as e:
            print(f"Interpolation Error: {str(e)}")
            return None

    def generate_velocity_pressure_profile(
        self,
        table_vel_pres_coef,
        heights_list,
        eave_height,
        mean_roof_height,
        apex_height,  
        exposure_input,
        vel_pres,
    ):
        """
        Extracts Kz values for the selected exposure category, handles height consolidation
        including eave, mean roof, and apex heights, interpolates intermediate values,
        computes qz (Pa), and returns the final DataFrame profile.
        """
        try:
            # 1. EXTRACT VELOCITY PRESSURE COEFFICIENTS FOR THE SELECTED EXPOSURE CATEGORY
            target_column = str(exposure_input).strip().upper()
            if target_column not in table_vel_pres_coef.columns:
                raise ValueError(
                    f"Exposure Category '{exposure_input}' column not found in table."
                )

            # 2. Gather all distinct heights to consider and sort them ascending
            all_heights = set(heights_list)
            if eave_height is not None:
                all_heights.add(float(eave_height))
            if mean_roof_height is not None:
                all_heights.add(float(mean_roof_height))
            if apex_height is not None:  # <-- 2. ADDED TO THE PROFILE HEAP
                all_heights.add(float(apex_height))
            sorted_heights = sorted(list(all_heights))

            # 3. Interpolate Kz values line-by-line
            kz_results = []
            for h in sorted_heights:
                kz_val = self.interpolate_table_value(
                    reference_table=table_vel_pres_coef,
                    target_column_name=target_column,
                    lookup_input_value=h,
                )

                if kz_val is None:
                    continue

                # --- 3. CONTEXT LABELS ADJUSTMENT ---
                label = "User Input"
                if h == float(mean_roof_height):
                    label = "Mean Roof Height"
                elif h == float(eave_height):
                    label = "Eave Height"
                elif h == float(apex_height):  # <-- Added dynamic label matching
                    label = "Apex Height"

                kz_results.append(
                    {
                        "Height (m)": h,
                        "Type": label,
                        f"Kz ({target_column})": round(kz_val, 3),
                    }
                )

            # 4. APPEND VELOCITY PRESSURE (qz) COLUMN TO THE RESULTS TABLE
            table_vel_pressure = pd.DataFrame(kz_results)

            if table_vel_pressure.empty:
                return table_vel_pressure

            # Compute the final pressure product column matching your exact formatting equation
            table_vel_pressure["qz (Pa)"] = round(
                table_vel_pressure[f"Kz ({target_column})"] * float(vel_pres), 3
            )

            return table_vel_pressure

        except Exception as e:
            print(f"Error compiling velocity pressure profile: {str(e)}")
            return pd.DataFrame()

    def generate_wall_cp_table(self, table_wall_cp, l_value, b_value, ridge_direction):
        """
        Generates a structured wall Cp table by pulling raw numeric sequences out
        by physical row position, completely bypassing string matching bugs.
        """
        try:
            # 1. Clean dimension inputs
            l_value = float(l_value)
            b_value = float(b_value)
            ridge_dir = str(ridge_direction).strip().upper()

            # 2. Calculate structural L/B aspect ratios
            if ridge_dir == "L":
                lb_normal = b_value / l_value
                lb_parallel = l_value / b_value
            else:
                lb_normal = l_value / b_value
                lb_parallel = b_value / l_value

            lb_thresholds = []
            cp_coefficients = []

            # Target the explicit rows (1 to 4) where Leeward properties sit
            for row_idx in [1, 2, 3, 4]:
                row_data = table_wall_cp.iloc[row_idx]

                # Extract all float values from this row
                row_floats = []
                for cell in row_data:
                    try:
                        row_floats.append(float(cell))
                    except (ValueError, TypeError):
                        continue

                # Based on the grid layout, the 1st float is L/B, the 2nd float is Cp
                if len(row_floats) >= 2:
                    lb_thresholds.append(row_floats[0])
                    cp_coefficients.append(row_floats[1])

            # 4. Build clean temporary lookup frame for our helper function
            leeward_lookup = pd.DataFrame(
                data={"Cp": cp_coefficients}, index=lb_thresholds
            )

            # 5. Compute interpolation values using our core standalone engine helper
            cp_normal = self.interpolate_table_value(
                reference_table=leeward_lookup,
                target_column_name="Cp",
                lookup_input_value=lb_normal,
            )

            cp_parallel = self.interpolate_table_value(
                reference_table=leeward_lookup,
                target_column_name="Cp",
                lookup_input_value=lb_parallel,
            )

            # 6. Construct final 4-row output table layout matching image 3
            wall_data = [
                {
                    "Surface": "Windward wall",
                    "Wind direction": "All",
                    "L/B": "All",
                    "Cp": 0.80,
                },
                {
                    "Surface": "Leeward wall",
                    "Wind direction": "Normal to ridge",
                    "L/B": round(lb_normal, 2),
                    "Cp": round(cp_normal, 2) if cp_normal is not None else -0.50,
                },
                {
                    "Surface": "",
                    "Wind direction": "Parallel to ridge",
                    "L/B": round(lb_parallel, 2),
                    "Cp": round(cp_parallel, 2) if cp_parallel is not None else -0.45,
                },
                {
                    "Surface": "Side wall",
                    "Wind direction": "All",
                    "L/B": "All",
                    "Cp": -0.70,
                },
            ]

            return pd.DataFrame(wall_data)

        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Error compiling Wall Cp Matrix: {str(e)}")
            return pd.DataFrame()

    def generate_roof_cp(
        self,
        table_roof_under_10,
        table_roof_over_10,
        eave_height,
        apex_height,
        b_value,
        l_value,
        ridge_direction,
        wind_direction,
    ):
        """
        Unified engine structured to branch explicitly by wind direction, then by
        roof pitch threshold under normal wind. Integrates an internal helper function
        to process stepped distance zones cleanly.
        """

        # =========================================================================
        # REUSABLE INTERNAL HELPER: STEPPED DISTANCE ZONES
        # =========================================================================
        def _process_stepped_zones(h_over_l, current_wind):
            """Helper function to calculate and structure low-slope or parallel stepped zones."""
            x_hl = [0.5, 1.0]
            col_hl_label = f"h/L = {h_over_l:.2f}"

            # --- Condition 1: Purely Low Aspect Ratio (<= 0.5) ---
            if h_over_l <= 0.5:
                cp_z1_c1, cp_z1_c2 = (
                    float(table_roof_under_10.iloc[0, 0]),
                    float(table_roof_under_10.iloc[0, 1]),
                )
                cp_z2_c1, cp_z2_c2 = (
                    float(table_roof_under_10.iloc[1, 0]),
                    float(table_roof_under_10.iloc[1, 1]),
                )
                cp_z3_c1, cp_z3_c2 = (
                    float(table_roof_under_10.iloc[2, 0]),
                    float(table_roof_under_10.iloc[2, 1]),
                )
                cp_z4_c1, cp_z4_c2 = (
                    float(table_roof_under_10.iloc[3, 0]),
                    float(table_roof_under_10.iloc[3, 1]),
                )

                display_rows = [
                    {
                        "Surface": f"Roof ({current_wind})",
                        "Horizontal Distance from Windward Edge": "0 to h/2",
                        col_hl_label: f"{round(cp_z1_c1, 2)}, {round(cp_z1_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": "h/2 to h",
                        col_hl_label: f"{round(cp_z2_c1, 2)}, {round(cp_z2_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": "h to 2h",
                        col_hl_label: f"{round(cp_z3_c1, 2)}, {round(cp_z3_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": ">2h",
                        col_hl_label: f"{round(cp_z4_c1, 2)}, {round(cp_z4_c2, 2)}*",
                    },
                ]
                df_profile_payload = pd.DataFrame(
                    [
                        {
                            "Surface": "Roof (0 to h/2)",
                            "C_p_1": round(cp_z1_c1, 2),
                            "C_p_2": round(cp_z1_c2, 2),
                        },
                        {
                            "Surface": "Roof (h/2 to h)",
                            "C_p_1": round(cp_z2_c1, 2),
                            "C_p_2": round(cp_z2_c2, 2),
                        },
                        {
                            "Surface": "Roof (h to 2h)",
                            "C_p_1": round(cp_z3_c1, 2),
                            "C_p_2": round(cp_z3_c2, 2),
                        },
                        {
                            "Surface": "Roof (>2h)",
                            "C_p_1": round(cp_z4_c1, 2),
                            "C_p_2": round(cp_z4_c2, 2),
                        },
                    ]
                )

            # --- Condition 2: Purely High Aspect Ratio (>= 1.0) ---
            elif h_over_l >= 1.0:
                cp_z1_c1, cp_z1_c2 = (
                    float(table_roof_under_10.iloc[4, 0]),
                    float(table_roof_under_10.iloc[4, 1]),
                )
                cp_z4_c1, cp_z4_c2 = (
                    float(table_roof_under_10.iloc[5, 0]),
                    float(table_roof_under_10.iloc[5, 1]),
                )

                display_rows = [
                    {
                        "Surface": f"Roof ({current_wind})",
                        "Horizontal Distance from Windward Edge": "0 to h/2",
                        col_hl_label: f"{round(cp_z1_c1, 2)}, {round(cp_z1_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": ">h/2",
                        col_hl_label: f"{round(cp_z4_c1, 2)}, {round(cp_z4_c2, 2)}*",
                    },
                ]
                df_profile_payload = pd.DataFrame(
                    [
                        {
                            "Surface": "Roof (0 to h/2)",
                            "C_p_1": round(cp_z1_c1, 2),
                            "C_p_2": round(cp_z1_c2, 2),
                        },
                        {
                            "Surface": "Roof (>h/2)",
                            "C_p_1": round(cp_z4_c1, 2),
                            "C_p_2": round(cp_z4_c2, 2),
                        },
                    ]
                )

            # --- Condition 3: In-Between Aspect Ratio (0.5 < h/L < 1.0) ---
            else:
                df_z1_c1 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[0, 0]),
                            float(table_roof_under_10.iloc[4, 0]),
                        ]
                    },
                    index=x_hl,
                )
                df_z1_c2 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[0, 1]),
                            float(table_roof_under_10.iloc[4, 1]),
                        ]
                    },
                    index=x_hl,
                )
                df_z2_c1 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[1, 0]),
                            float(table_roof_under_10.iloc[5, 0]),
                        ]
                    },
                    index=x_hl,
                )
                df_z2_c2 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[1, 1]),
                            float(table_roof_under_10.iloc[5, 1]),
                        ]
                    },
                    index=x_hl,
                )
                df_z3_c1 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[2, 0]),
                            float(table_roof_under_10.iloc[5, 0]),
                        ]
                    },
                    index=x_hl,
                )
                df_z3_c2 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[2, 1]),
                            float(table_roof_under_10.iloc[5, 1]),
                        ]
                    },
                    index=x_hl,
                )
                df_z4_c1 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[3, 0]),
                            float(table_roof_under_10.iloc[5, 0]),
                        ]
                    },
                    index=x_hl,
                )
                df_z4_c2 = pd.DataFrame(
                    data={
                        "Val": [
                            float(table_roof_under_10.iloc[3, 1]),
                            float(table_roof_under_10.iloc[5, 1]),
                        ]
                    },
                    index=x_hl,
                )

                cp_z1_c1 = self.interpolate_table_value(df_z1_c1, "Val", h_over_l)
                cp_z1_c2 = self.interpolate_table_value(df_z1_c2, "Val", h_over_l)
                cp_z2_c1 = self.interpolate_table_value(df_z2_c1, "Val", h_over_l)
                cp_z2_c2 = self.interpolate_table_value(df_z2_c2, "Val", h_over_l)
                cp_z3_c1 = self.interpolate_table_value(df_z3_c1, "Val", h_over_l)
                cp_z3_c2 = self.interpolate_table_value(df_z3_c2, "Val", h_over_l)
                cp_z4_c1 = self.interpolate_table_value(df_z4_c1, "Val", h_over_l)
                cp_z4_c2 = self.interpolate_table_value(df_z4_c2, "Val", h_over_l)

                display_rows = [
                    {
                        "Surface": f"Roof ({current_wind})",
                        "Horizontal Distance from Windward Edge": "0 to h/2",
                        col_hl_label: f"{round(cp_z1_c1, 2)}, {round(cp_z1_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": "h/2 to h",
                        col_hl_label: f"{round(cp_z2_c1, 2)}, {round(cp_z2_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": "h to 2h",
                        col_hl_label: f"{round(cp_z3_c1, 2)}, {round(cp_z3_c2, 2)}*",
                    },
                    {
                        "Surface": "",
                        "Horizontal Distance from Windward Edge": ">2h",
                        col_hl_label: f"{round(cp_z4_c1, 2)}, {round(cp_z4_c2, 2)}*",
                    },
                ]

                df_profile_payload = pd.DataFrame(
                    [
                        {
                            "Surface": "Roof (0 to h/2)",
                            "C_p_1": round(cp_z1_c1, 2),
                            "C_p_2": round(cp_z1_c2, 2),
                        },
                        {
                            "Surface": "Roof (h/2 to h)",
                            "C_p_1": round(cp_z2_c1, 2),
                            "C_p_2": round(cp_z2_c2, 2),
                        },
                        {
                            "Surface": "Roof (h to 2h)",
                            "C_p_1": round(cp_z3_c1, 2),
                            "C_p_2": round(cp_z3_c2, 2),
                        },
                        {
                            "Surface": "Roof (>2h)",
                            "C_p_1": round(cp_z4_c1, 2),
                            "C_p_2": round(cp_z4_c2, 2),
                        },
                    ]
                )

            return pd.DataFrame(display_rows), df_profile_payload, theta, h_over_l

        # =========================================================================
        # PRIMARY EXECUTION ENTRY
        # =========================================================================
        try:
            eave, apex, b, l = (
                float(eave_height),
                float(apex_height),
                float(b_value),
                float(l_value),
            )
            h = (eave + apex) / 2.0
            current_wind = str(wind_direction).strip().capitalize()

            # ---------------------------------------------------------------------
            # BRANCH 1: WIND IS NORMAL TO THE RIDGE
            # ---------------------------------------------------------------------
            if current_wind == "Normal":
                run_dist = (
                    (b / 2.0)
                    if str(ridge_direction).strip().upper() == "L"
                    else (l / 2.0)
                )
                l_wind = b if str(ridge_direction).strip().upper() == "L" else l
                theta = np.degrees(np.arctan((apex - eave) / run_dist))
                h_over_l = h / l_wind

                print("\n--- Wind Normal to Ridge Engine ---")
                print(f"h: {round(h, 2)}")
                print(f"L_wind: {round(l_wind, 2)}")
                print(f"Computed h/L ratio: {round(h_over_l, 2)}")
                print(f"Computed roof slope angle (theta): {round(theta, 2)}°")

                # Sub-Branch A: Flat Roofs (< 10 degrees) -> Diverts to Helper Function
                if theta < 10.0:
                    print("Slope < 10°: Invoking Stepped Zone Helper...")
                    return _process_stepped_zones(h_over_l, current_wind)

                # Sub-Branch B: Pitched Roofs (>= 10 degrees) -> Performs Bilinear 2D Interpolation
                else:
                    print("Slope >= 10°: Running 2D Bilinear Matrix Interpolation...")

                    table_roof_over_10 = table_roof_over_10.reset_index(
                        drop=True
                    )  # Reset index to ensure clean row access

                    all_headers = [float(col) for col in table_roof_over_10.columns]
                    angle_array_windward = np.array(all_headers[:8])
                    angle_array_leeward = np.array(all_headers[8:])

                    lower_angle = max(
                        [a for a in angle_array_windward if a <= theta],
                        default=angle_array_windward.min(),
                    )
                    upper_angle = min(
                        [a for a in angle_array_windward if a >= theta],
                        default=angle_array_windward.max(),
                    )

                    y_hl_coordinates = [0.25, 0.5, 1.0]
                    w1_low, w1_act, w1_high = [], [], []
                    w2_low, w2_act, w2_high = [], [], []
                    l1_low, l1_act, l1_high = [], [], []

                    for tier_idx in range(3):
                        r_c1 = tier_idx * 2
                        r_c2 = (tier_idx * 2) + 1

                        df_w1 = pd.DataFrame(
                            data={
                                "Val": table_roof_over_10.iloc[r_c1, :8]
                                .fillna(0.0)
                                .values
                            },
                            index=angle_array_windward,
                        )
                        df_w2 = pd.DataFrame(
                            data={
                                "Val": table_roof_over_10.iloc[r_c2, :8]
                                .fillna(0.0)
                                .values
                            },
                            index=angle_array_windward,
                        )
                        df_l1 = pd.DataFrame(
                            data={
                                "Val": table_roof_over_10.iloc[r_c2, 8:]
                                .fillna(0.0)
                                .values
                            },
                            index=angle_array_leeward,
                        )

                        w1_low.append(
                            self.interpolate_table_value(df_w1, "Val", lower_angle)
                        )
                        w1_act.append(self.interpolate_table_value(df_w1, "Val", theta))
                        w1_high.append(
                            self.interpolate_table_value(df_w1, "Val", upper_angle)
                        )

                        w2_low.append(
                            self.interpolate_table_value(df_w2, "Val", lower_angle)
                        )
                        w2_act.append(self.interpolate_table_value(df_w2, "Val", theta))
                        w2_high.append(
                            self.interpolate_table_value(df_w2, "Val", upper_angle)
                        )

                        l1_low.append(
                            self.interpolate_table_value(df_l1, "Val", lower_angle)
                        )
                        l1_act.append(self.interpolate_table_value(df_l1, "Val", theta))
                        l1_high.append(
                            self.interpolate_table_value(df_l1, "Val", upper_angle)
                        )

                    df_final_w1_low = pd.DataFrame(
                        data={"Val": w1_low}, index=y_hl_coordinates
                    )
                    df_final_w1_act = pd.DataFrame(
                        data={"Val": w1_act}, index=y_hl_coordinates
                    )
                    df_final_w1_high = pd.DataFrame(
                        data={"Val": w1_high}, index=y_hl_coordinates
                    )
                    df_final_w2_low = pd.DataFrame(
                        data={"Val": w2_low}, index=y_hl_coordinates
                    )
                    df_final_w2_act = pd.DataFrame(
                        data={"Val": w2_act}, index=y_hl_coordinates
                    )
                    df_final_w2_high = pd.DataFrame(
                        data={"Val": w2_high}, index=y_hl_coordinates
                    )
                    df_final_l1_low = pd.DataFrame(
                        data={"Val": l1_low}, index=y_hl_coordinates
                    )
                    df_final_l1_act = pd.DataFrame(
                        data={"Val": l1_act}, index=y_hl_coordinates
                    )
                    df_final_l1_high = pd.DataFrame(
                        data={"Val": l1_high}, index=y_hl_coordinates
                    )

                    cp_w1_low = self.interpolate_table_value(
                        df_final_w1_low, "Val", h_over_l
                    )
                    cp_w1_act = self.interpolate_table_value(
                        df_final_w1_act, "Val", h_over_l
                    )
                    cp_w1_high = self.interpolate_table_value(
                        df_final_w1_high, "Val", h_over_l
                    )
                    cp_w2_low = self.interpolate_table_value(
                        df_final_w2_low, "Val", h_over_l
                    )
                    cp_w2_act = self.interpolate_table_value(
                        df_final_w2_act, "Val", h_over_l
                    )
                    cp_w2_high = self.interpolate_table_value(
                        df_final_w2_high, "Val", h_over_l
                    )
                    cp_l1_low = self.interpolate_table_value(
                        df_final_l1_low, "Val", h_over_l
                    )
                    cp_l1_act = self.interpolate_table_value(
                        df_final_l1_act, "Val", h_over_l
                    )
                    cp_l1_high = self.interpolate_table_value(
                        df_final_l1_high, "Val", h_over_l
                    )

                    col_low = f"{lower_angle:g}°"
                    col_act = f"{theta:.1f}°"
                    col_high = f"{upper_angle:g}°"

                    display_rows = [
                        {
                            "Surface": "Windward roof",
                            col_low: round(cp_w1_low, 2),
                            col_act: round(cp_w1_act, 2),
                            col_high: round(cp_w1_high, 2),
                        },
                        {
                            "Surface": "",
                            col_low: round(cp_w2_low, 2),
                            col_act: round(cp_w2_act, 2),
                            col_high: round(cp_w2_high, 2),
                        },
                        {
                            "Surface": "Leeward roof",
                            col_low: round(cp_l1_low, 2),
                            col_act: round(cp_l1_act, 2),
                            col_high: round(cp_l1_high, 2),
                        },
                    ]
                    df_profile_payload = pd.DataFrame(
                        [
                            {
                                "Surface": "Windward roof",
                                "C_p_1": round(cp_w1_act, 2),
                                "C_p_2": round(cp_w2_act, 2),
                            },
                            {
                                "Surface": "Leeward roof",
                                "C_p_1": round(cp_l1_act, 2),
                                "C_p_2": round(cp_l1_act, 2),
                            },
                        ]
                    )
                    return (
                        pd.DataFrame(display_rows),
                        df_profile_payload,
                        theta,
                        h_over_l,
                    )

            # ---------------------------------------------------------------------
            # BRANCH 2: WIND IS PARALLEL TO THE RIDGE (All Pitches & Slopes)
            # ---------------------------------------------------------------------
            else:
                run_dist = (
                    (l / 2.0)
                    if str(ridge_direction).strip().upper() == "L"
                    else (b / 2.0)
                )
                l_wind = l if str(ridge_direction).strip().upper() == "L" else b
                theta = np.degrees(np.arctan((apex - eave) / run_dist))
                h_over_l = h / l_wind

                print("\n--- Wind Parallel to Ridge Engine ---")
                print(
                    "Invoking Stepped Zone Helper (Governs for all values of theta)..."
                )
                print(f"h: {round(h, 2)}")
                print(f"l_wind: {round(l_wind, 2)}")
                print(f"Computed h/L ratio: {round(h_over_l, 2)}")
                print(f"Computed roof slope angle (theta): {round(theta, 2)}°")
                return _process_stepped_zones(h_over_l, current_wind)

        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"Structural Pipeline Error: {str(e)}")
            return pd.DataFrame(), pd.DataFrame(), 0.0

    def generate_mwfrs_normal_to_ridge_table(
        self,
        df_wall_cp,
        df_roof_payload,
        df_velocity_profile,
        gust_effect_factor=0.85,
        gcpi_pos=0.55,
        gcpi_neg=-0.55,
    ):
        """
        Generates a dedicated MWFRS pressure summary table exclusively for the
        'Wind Normal to Ridge' direction case.

        Dynamically handles:
        1. Pitched roofs (>= 10 deg) with dual windward load cases.
        2. Flat roofs (< 10 deg) with stepped distance zones (0 to h/2, etc.).
        """
        # Cast variables to floats to prevent string math errors
        g_pos = float(gcpi_pos)
        g_neg = float(gcpi_neg)

        # 1. Isolate the reference q_h value at Mean Roof Height ('Mean' entry in profile)
        try:
            q_h = float(
                df_velocity_profile.loc[
                    df_velocity_profile["Type"].str.contains("Mean", na=False),
                    "qz (Pa)",
                ].values[0]
            )
        except (IndexError, KeyError, ValueError):
            # Fallback to the top of the building if 'Mean' isn't explicitly defined
            q_h = float(df_velocity_profile["qz (Pa)"].iloc[-1])

        summary_rows = []

        # =========================================================================
        # PART 1: WINDWARD WALL PROFILE (Step-by-step heights)
        # =========================================================================
        cp_ww = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Windward wall", "Cp"].values[0]
        )

        # Filter out the roof-specific heights so the wall profile stops at the eave
        df_wall_profile = df_velocity_profile[
            ~df_velocity_profile["Type"].str.contains("Mean|Apex", case=False, na=False)
        ].reset_index(drop=True)

        for idx, row in df_wall_profile.iterrows():
            z_val = float(row["Height (m)"])
            q_z = float(row["qz (Pa)"])

            # Net Pressure: p = q*G*Cp - qi*GCpi
            p_pos = (q_z * gust_effect_factor * cp_ww) - (q_h * g_pos)
            p_neg = (q_z * gust_effect_factor * cp_ww) - (q_h * g_neg)

            summary_rows.append(
                {
                    "Surface": "Windward wall" if idx == 0 else "",
                    "z (m)": f"{z_val:g}",
                    "q (Pa)": round(q_z, 2),
                    "G": gust_effect_factor,
                    "C_p": cp_ww,
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # =========================================================================
        # PART 2: LEEWARD & SIDE WALLS (Uniform at q_h)
        # =========================================================================
        cp_lw = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Leeward wall", "Cp"].iloc[0]
        )
        cp_sw = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Side wall", "Cp"].values[0]
        )

        for label, cp_val in [("Leeward wall", cp_lw), ("Side walls", cp_sw)]:
            p_pos = (q_h * gust_effect_factor * cp_val) - (q_h * g_pos)
            p_neg = (q_h * gust_effect_factor * cp_val) - (q_h * g_neg)

            summary_rows.append(
                {
                    "Surface": label,
                    "z (m)": "All",
                    "q (Pa)": round(q_h, 2),
                    "G": gust_effect_factor,
                    "C_p": cp_val,
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # =========================================================================
        # PART 3: ROOF ENVELOPE SCRIPTING (Handles BOTH Pitched and Flat Roofs)
        # =========================================================================
        for _, row in df_roof_payload.iterrows():
            zone_label = str(row["Surface"]).strip()

            # --- SCENARIO A: PITCHED ROOF (>= 10 degrees) ---
            if "windward" in zone_label.lower():
                cp_1 = float(row["C_p_1"])
                p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
                p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)

                summary_rows.append(
                    {
                        "Surface": "Windward roof*",
                        "z (m)": "—",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_1,
                        "Net (+GCpi)": round(p_pos_1, 2),
                        "Net (-GCpi)": round(p_neg_1, 2),
                    }
                )

                # Add the second load case sub-row for Windward Roof if it exists
                if (
                    "C_p_2" in row
                    and not np.isnan(row["C_p_2"])
                    and row["C_p_2"] != cp_1
                ):
                    cp_2 = float(row["C_p_2"])
                    p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                    p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)

                    summary_rows.append(
                        {
                            "Surface": "",
                            "z (m)": "—",
                            "q (Pa)": round(q_h, 2),
                            "G": gust_effect_factor,
                            "C_p": cp_2,
                            "Net (+GCpi)": round(p_pos_2, 2),
                            "Net (-GCpi)": round(p_neg_2, 2),
                        }
                    )

            elif "leeward" in zone_label.lower():
                cp_l = float(row["C_p_1"])
                p_pos_l = (q_h * gust_effect_factor * cp_l) - (q_h * g_pos)
                p_neg_l = (q_h * gust_effect_factor * cp_l) - (q_h * g_neg)

                summary_rows.append(
                    {
                        "Surface": "Leeward roof",
                        "z (m)": "—",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_l,
                        "Net (+GCpi)": round(p_pos_l, 2),
                        "Net (-GCpi)": round(p_neg_l, 2),
                    }
                )

            # --- SCENARIO B: FLAT ROOF (< 10 degrees) ---
            # Catches the stepped distance zones (e.g., "Roof (0 to h/2)")
            else:
                # Safely strip any asterisks from the primary coefficient before math
                raw_cp1 = str(row["C_p_1"]).replace("*", "").strip()
                cp_1 = float(raw_cp1)

                p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
                p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)

                summary_rows.append(
                    {
                        "Surface": zone_label,
                        "z (m)": "—",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_1,  # Uses the clean float for math
                        "Net (+GCpi)": round(p_pos_1, 2),
                        "Net (-GCpi)": round(p_neg_1, 2),
                    }
                )

                # Safely strip any asterisks from the secondary coefficient
                if (
                    "C_p_2" in row
                    and pd.notna(row["C_p_2"])
                    and str(row["C_p_2"]).strip() != ""
                ):
                    raw_cp2 = str(row["C_p_2"]).replace("*", "").strip()
                    cp_2 = float(raw_cp2)

                    if cp_2 != cp_1:
                        p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                        p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)

                        summary_rows.append(
                            {
                                "Surface": "",
                                "z (m)": "—",
                                "q (Pa)": round(q_h, 2),
                                "G": gust_effect_factor,
                                "C_p": cp_2,  # Uses the clean float
                                "Net (+GCpi)": round(p_pos_2, 2),
                                "Net (-GCpi)": round(p_neg_2, 2),
                            }
                        )

        return pd.DataFrame(summary_rows)

    def generate_mwfrs_parallel_to_ridge_table(
        self,
        df_wall_cp,
        df_roof_payload,
        df_velocity_profile,
        gust_effect_factor=0.85,
        gcpi_pos=0.55,
        gcpi_neg=-0.55,
    ):
        """
        Generates a dedicated MWFRS pressure summary table exclusively for the
        'Wind Parallel to Ridge' direction case.

        Treats the entire roof as a single stepped-distance surface.
        Windward wall extends to the Apex (Gable end).
        Rounded to 2 decimal places.
        """
        g_pos = float(gcpi_pos)
        g_neg = float(gcpi_neg)

        # 1. Isolate the reference q_h value at Mean Roof Height
        try:
            q_h = float(
                df_velocity_profile.loc[
                    df_velocity_profile["Type"].str.contains("Mean", na=False),
                    "qz (Pa)",
                ].values[0]
            )
        except (IndexError, KeyError, ValueError):
            q_h = float(df_velocity_profile["qz (Pa)"].iloc[-1])

        summary_rows = []

        # =========================================================================
        # PART 1: WINDWARD WALL PROFILE (Gable End - Uses ALL heights)
        # =========================================================================
        cp_ww = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Windward wall", "Cp"].values[0]
        )

        # For wind parallel to the ridge, the wall extends to the apex.
        # We iterate through the entire velocity profile without filtering.
        for idx, row in df_velocity_profile.iterrows():
            z_val = float(row["Height (m)"])
            q_z = float(row["qz (Pa)"])

            p_pos = (q_z * gust_effect_factor * cp_ww) - (q_h * g_pos)
            p_neg = (q_z * gust_effect_factor * cp_ww) - (q_h * g_neg)

            summary_rows.append(
                {
                    "Surface": "Windward wall" if idx == 0 else "",
                    "z (m)": f"{z_val:g}",
                    "q (Pa)": round(q_z, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_ww, 2),
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # =========================================================================
        # PART 2: LEEWARD & SIDE WALLS (Parallel Specific)
        # =========================================================================
        # 1. Forward-fill empty Surface cells to fix blank Excel rows
        df_wall_cp["Surface"] = (
            df_wall_cp["Surface"].replace(r"^\s*$", np.nan, regex=True).ffill()
        )

        # 2. Extract Side Wall
        cp_sw = float(
            df_wall_cp.loc[
                df_wall_cp["Surface"].str.contains("Side wall", case=False), "Cp"
            ].values[0]
        )

        # 3. Explicitly target the Leeward Wall row where Wind Direction contains "Parallel"
        try:
            parallel_leeward_mask = (
                df_wall_cp["Surface"].str.contains("Leeward", case=False)
            ) & (
                df_wall_cp["Wind direction"].str.contains(
                    "Parallel", case=False, na=False
                )
            )
            cp_lw = float(df_wall_cp.loc[parallel_leeward_mask, "Cp"].values[0])
        except (IndexError, KeyError, ValueError):
            cp_lw = float(
                df_wall_cp.loc[
                    df_wall_cp["Surface"].str.contains("Leeward", case=False), "Cp"
                ].iloc[-1]
            )
            
        # 4. Run the uniform load calculations using q_h
        for label, cp_val in [("Leeward wall", cp_lw), ("Side walls", cp_sw)]:
            p_pos = (q_h * gust_effect_factor * cp_val) - (q_h * g_pos)
            p_neg = (q_h * gust_effect_factor * cp_val) - (q_h * g_neg)

            summary_rows.append(
                {
                    "Surface": label,
                    "z (m)": "All",
                    "q (Pa)": round(q_h, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_val, 2),
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # =========================================================================
        # PART 3: ROOF ENVELOPE SCRIPTING (Stepped Distance Zones Only)
        # =========================================================================
        for idx, row in df_roof_payload.iterrows():
            zone_label = str(row["Surface"]).strip()

            # 1. Primary Coefficient (Asterisk Stripping)
            raw_cp1 = str(row["C_p_1"]).replace("*", "").strip()
            cp_1 = float(raw_cp1)
            p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
            p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)

            display_surface = f"Roof ({zone_label})" if idx == 0 else zone_label

            summary_rows.append(
                {
                    "Surface": display_surface,
                    "z (m)": "—",
                    "q (Pa)": round(q_h, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_1, 2),
                    "Net (+GCpi)": round(p_pos_1, 2),
                    "Net (-GCpi)": round(p_neg_1, 2),
                }
            )

            # 2. Secondary Minimum Coefficient (Dual-Row Logic)
            if (
                "C_p_2" in row
                and pd.notna(row["C_p_2"])
                and str(row["C_p_2"]).strip() != ""
            ):
                raw_cp2 = str(row["C_p_2"]).replace("*", "").strip()
                cp_2 = float(raw_cp2)

                if cp_2 != cp_1:
                    p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                    p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)

                    summary_rows.append(
                        {
                            "Surface": "",
                            "z (m)": "—",
                            "q (Pa)": round(q_h, 2),
                            "G": round(gust_effect_factor, 2),
                            "C_p": round(cp_2, 2),
                            "Net (+GCpi)": round(p_pos_2, 2),
                            "Net (-GCpi)": round(p_neg_2, 2),
                        }
                    )

        return pd.DataFrame(summary_rows)

    # --- WIND LOAD CALCULATION TRIGGER FUNCTION ---
    def calculate_wind_load(self):
        """This is the function the Excel button will actually trigger."""
        wb = xw.Book.caller()
        main_sheet = wb.sheets.active

        try:
            # EXTRACT ALL INPUT VALUES
            table_vel_pres_coef = self.table_vel_pres_coef
            table_wall_pres_coef = self.wall_press_coeff_data
            table_int_pres_coef = self.table_int_pres_coef
            table_roof_over_10 = self.table_roof_over_10
            table_roof_under_10 = self.table_roof_under_10
            l_input = self.l_input
            b_input = self.b_input
            ridge_direction_input = self.ridge_direction_input

            raw_heights = self.raw_heights
            eave_height = self.eave_height
            apex_height = self.apex_height
            mean_roof_height = self.mean_roof_height

            if raw_heights:
                # Split by comma, strip spaces off each item, and keep it only if it's not empty
                heights_list = [
                    float(h.strip()) for h in str(raw_heights).split(",") if h.strip()
                ]
                self.heights_list = heights_list
            else:
                heights_list = []

                # 1. Grab base variables from Excel (Example layout)
            enclosure_input = self.enclosure_class
            exposure_input = self.exposure_category

            self.exposure_input = exposure_input  # Store for later use in other methods

            v_input = self.basic_wind_speed
            kd_input = self.wind_dir_factor
            kzt_input = self.topographic_factor
            ke_input = self.ground_elevation_factor
            g_input = self.gust_effect_factor

            vel_pres = 0.613 * (v_input**2) * kd_input * kzt_input * ke_input

            self.vel_pres = vel_pres  # Store for later use in other methods

            main_sheet.range("C10").value = round(vel_pres, 3)

            print(table_vel_pres_coef)
            print(table_wall_pres_coef)
            print(table_roof_over_10)
            print(table_int_pres_coef)
            print(table_roof_under_10)

            print(f"Extracted heights: {heights_list}")
            print(f"Eave height: {eave_height}")
            print(f"Mean roof height: {mean_roof_height}")

            target = str(enclosure_input).strip()

            matched_row = None
            for idx_val in table_int_pres_coef.index:
                if str(idx_val).strip().lower() == target.lower():
                    matched_row = table_int_pres_coef.loc[idx_val]
                    break

            if matched_row is None:
                raise ValueError(
                    f"Enclosure Classification '{enclosure_input}' was not found."
                )

            # Extracted clean float variables by position index
            gcpi_pos = float(matched_row.iloc[1])
            gcpi_neg = float(matched_row.iloc[2])

            self.gcpi_pos = gcpi_pos  # Store for later use in other methods
            self.gcpi_neg = gcpi_neg  # Store for later use in other methods

            print(
                f"Matched row for Enclosure Classification '{enclosure_input}': {matched_row}"
            )
            print(
                f"Extracted GCpi values: Positive = {gcpi_pos}, Negative = {gcpi_neg}"
            )

            main_sheet.range("C11").value = gcpi_pos
            main_sheet.range("C12").value = gcpi_neg

            initial_anchor_cell = "B24"

            # 2. WIPE THE CANVAS CLEAN before pasting anything!
            # This guarantees no "ghost" borders or old data from previous runs are left behind
            self.clear_output_zones(
                sheet_obj=main_sheet, start_cell_address=initial_anchor_cell
            )

            # 3. Set the tracking variable to your starting point
            current_anchor_cell = initial_anchor_cell

            table_vel_pressure = self.generate_velocity_pressure_profile(
                table_vel_pres_coef=table_vel_pres_coef,
                heights_list=heights_list,
                eave_height=eave_height,
                mean_roof_height=mean_roof_height,
                apex_height=apex_height,  # <-- Remember to hook this up here!
                exposure_input=exposure_input,
                vel_pres=vel_pres,
            )

            print(table_vel_pressure)

            # Paste the results back to Excel
            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=table_vel_pressure,
                target_cell_address=current_anchor_cell,
                table_title="Velocity Pressure Profile (qz) Table",
            )

            table_wall_cp = self.generate_wall_cp_table(
                table_wall_pres_coef, l_input, b_input, ridge_direction_input
            )
            print(table_wall_cp)

            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=table_wall_cp,
                target_cell_address=current_anchor_cell,
                table_title="Wall Pressure Coefficient (Cp) Table",
            )

            (
                df_roof_display_normal,
                df_roof_payload_normal,
                computed_pitch,
                final_hl_ratio,
            ) = self.generate_roof_cp(
                table_roof_under_10=table_roof_under_10,  # Your parsed low-slope DataFrame
                table_roof_over_10=table_roof_over_10,  # Your parsed steep-slope DataFrame
                eave_height=eave_height,
                apex_height=apex_height,
                b_value=b_input,
                l_value=l_input,
                ridge_direction=ridge_direction_input,
                wind_direction="Normal",  # "Normal" or "Parallel"
            )

            print(df_roof_display_normal)

            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=df_roof_display_normal,
                target_cell_address=current_anchor_cell,
                table_title="Roof Pressure Coefficient (Cp) Table - Normal Wind Direction",
            )

            (
                df_roof_display_parallel,
                df_roof_payload_parallel,
                computed_pitch,
                final_hl_ratio,
            ) = self.generate_roof_cp(
                table_roof_under_10=table_roof_under_10,  # Your parsed low-slope DataFrame
                table_roof_over_10=table_roof_over_10,  # Your parsed steep-slope DataFrame
                eave_height=eave_height,
                apex_height=apex_height,
                b_value=b_input,
                l_value=l_input,
                ridge_direction=ridge_direction_input,
                wind_direction="Parallel",  # "Normal" or "Parallel"
            )

            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=df_roof_display_parallel,
                target_cell_address=current_anchor_cell,
                table_title="Roof Pressure Coefficient (Cp) Table - Parallel Wind Direction",
            )

            print(df_roof_payload_normal)
            print(df_roof_payload_parallel)

            df_normal_summary = self.generate_mwfrs_normal_to_ridge_table(
                df_wall_cp=table_wall_cp,
                df_roof_payload=df_roof_payload_normal,
                df_velocity_profile=table_vel_pressure,
                gust_effect_factor=g_input,
                gcpi_pos=gcpi_pos,
                gcpi_neg=gcpi_neg,
            )

            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=df_normal_summary,
                target_cell_address=current_anchor_cell,
                table_title="MWFRS Pressure Summary Table - Normal Wind Direction",
            )

            print(df_normal_summary)

            df_parallel_summary = self.generate_mwfrs_parallel_to_ridge_table(
                df_wall_cp=table_wall_cp,  # Your imported wall coefficients
                df_roof_payload=df_roof_payload_parallel,  # Your calculated stepped roof zones
                df_velocity_profile=table_vel_pressure,  # Your height tracking profile
                gust_effect_factor=g_input,
                gcpi_pos=gcpi_pos,
                gcpi_neg=gcpi_neg,
            )

            current_anchor_cell = self.paste_dataframe_with_border(
                sheet_obj=main_sheet,
                dataframe_to_paste=df_parallel_summary,
                target_cell_address=current_anchor_cell,
                table_title="MWFRS Pressure Summary Table - Parallel Wind Direction",
            )

            print(df_parallel_summary)

        except (ValueError, KeyError, TypeError) as e:
            main_sheet.range("A23").value = f"Error: {str(e)}"

        finally:
            main_sheet.range(
                "B23"
            ).value = "Table loaded successfully into Pandas memory. Check the console for details."

    # --- EXPORT PDF TRIGGER FUNCTION ---
    def generate_pdf_report(
        self, output_filename="ASCE_7_MWFRS_Directional_Procedure_Report"
    ):
        """Generates a formal engineering PDF report using pylatex directly from pandas."""

        # ==========================================
        # 0. Trigger "Save As" Dialog via tkinter
        # ==========================================
        root = tk.Tk()
        root.withdraw()  # Hides the default empty tkinter window
        root.attributes("-topmost", True)  # Forces dialog to appear IN FRONT of Excel

        save_path = filedialog.asksaveasfilename(
            title="Save ASCE 7 MWFRS Directional Procedure Report",
            initialfile=output_filename,
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
        )

        # Handle User Cancellation
        if not save_path:
            self.sheet.range("D23").value = "PDF Export Canceled."
            return  # Exits the method early so the PDF doesn't generate blindly

        # Clean up the filename for PyLaTeX (strips .pdf to prevent "Name.pdf.pdf")
        if save_path.endswith(".pdf"):
            save_path = save_path[:-4]

        # Overwrite the default output_filename with the user's chosen path
        output_filename = save_path
        # ==========================================

        # 1. Initialize Document
        doc = Document(
            output_filename,
            geometry_options={
                "top": "0.75in",
                "bottom": "0.75in",
                "left": "0.75in",  # Added slight side margins so it doesn't crowd the edge
                "right": "0.75in",
            },
        )

        doc.preamble.append(Command("usepackage", "booktabs"))
        doc.preamble.append(Command("usepackage", "amsmath"))
        doc.preamble.append(Command("usepackage", "float"))

        # --- NEW: Fix the huge gap above \maketitle ---
        doc.preamble.append(Command("usepackage", "titling"))
        doc.preamble.append(NoEscape(r"\setlength{\droptitle}{-0.75in}"))
        doc.preamble.append(
            UnsafeCommand("posttitle", r"\par\end{center}\vspace{-1.5em}")
        )
        doc.preamble.append(UnsafeCommand("pagestyle", "empty"))
        # ----------------------------------------------

        with doc.create(Center()):
            # Title
            doc.append(
                NoEscape(
                    r"{\Large \bfseries ASCE 7 Wind Load Calculations (Directional Procedure)}\\[0.5em]"
                )
            )
            # Date
            doc.append(NoEscape(r"{\small \today}"))
        # Small spacing before Section 1 begins
        doc.append(NoEscape(r"\vspace{1.5em}"))

        # Hide page number on Page 1 if desired
        doc.append(UnsafeCommand("thispagestyle", "empty"))

        # ==========================================
        # 1.0 DESIGN PARAMETERS
        # ==========================================

        with doc.create(Section("Design Parameters")):
            doc.append(
                "The following fundamental parameters and building dimensions were utilized for the ASCE 7 wind load calculations:"
            )

            with doc.create(Table(position="h!")) as input_table:
                # 'l r' means left-aligned first column, right-aligned second column
                with input_table.create(Tabular("l r", booktabs=True)) as tabular:
                    # Table Header
                    tabular.add_row(("Parameter", "Value"))
                    tabular.add_hline()

                    # Core Parameters
                    tabular.add_row(("Building Classification", self.building_class))
                    tabular.add_row(
                        ("Basic Wind Speed (m/s)", f"{self.basic_wind_speed:.2f}")
                    )
                    tabular.add_row(("Enclosure Classification", self.enclosure_class))
                    tabular.add_row(("Exposure Category", self.exposure_category))
                    tabular.add_row(
                        (
                            "Wind Directionality Factor (Kd)",
                            f"{self.wind_dir_factor:.2f}",
                        )
                    )
                    tabular.add_row(
                        ("Topographic Factor (Kzt)", f"{self.topographic_factor:.2f}")
                    )
                    tabular.add_row(
                        (
                            "Ground Elevation Factor (Ke)",
                            f"{self.ground_elevation_factor:.2f}",
                        )
                    )
                    tabular.add_row(
                        ("Gust Effect Factor", f"{self.gust_effect_factor:.2f}")
                    )
                    tabular.add_row(
                        (
                            "Tentative Velocity Pressure (Kz Pa)",
                            f"{self.velocity_pressure:.2f}",
                        )
                    )

                    # Handling the two GCpi values on one line for a cleaner look
                    gcpi_string = f"+{self.internal_pressure_coefficient_pos:.2f} / {self.internal_pressure_coefficient_neg:.2f}"
                    tabular.add_row(("GCpi", gcpi_string))

                    tabular.add_hline()  # Optional separator between params and geometry

                    # Geometry Parameters
                    tabular.add_row(("L (m)", f"{self.l_input:.2f}"))
                    tabular.add_row(("B (m)", f"{self.b_input:.2f}"))
                    tabular.add_row(("Direction of Ridge", self.ridge_direction_input))

                    # Convert the heights list to a clean comma-separated string
                    # e.g. [8, 10, 15] -> "8, 10, 15"
                    if isinstance(self.heights_list, list):
                        heights_str = ", ".join([str(h) for h in self.heights_list])
                    else:
                        heights_str = str(self.heights_list)

                    tabular.add_row(("Heights to Consider (m)", heights_str))
                    tabular.add_row(("Eave Height (m)", f"{self.eave_height:.2f}"))
                    tabular.add_row(("Apex Height (m)", f"{self.apex_height:.2f}"))

        # 2. Grab Data directly from the pandas DataFrame
        # NOTE: If this returns a DataFrame, make sure to add .values.tolist() at the end!
        velocity_pressure = self.generate_velocity_pressure_profile(
            table_vel_pres_coef=self.table_vel_pres_coef,
            heights_list=self.heights_list,
            eave_height=self.eave_height,
            mean_roof_height=self.mean_roof_height,
            apex_height=self.apex_height,  # <-- Remember to hook this up here!
            exposure_input=self.exposure_input,
            vel_pres=self.vel_pres,
        )

        wall_press_coeff_data = self.generate_wall_cp_table(
            self.wall_press_coeff_data, self.l_input, self.b_input, self.ridge_direction_input
        )

        roof_press_coeff_normal, roof_payload_normal, place_holder_1, place_holder_2 = (
            self.generate_roof_cp(
                table_roof_under_10=self.table_roof_under_10,  # Your parsed low-slope DataFrame
                table_roof_over_10=self.table_roof_over_10,  # Your parsed steep-slope DataFrame
                eave_height=self.eave_height,
                apex_height=self.apex_height,
                b_value=self.b_input,
                l_value=self.l_input,
                ridge_direction=self.ridge_direction_input,
                wind_direction="Normal",  # "Normal" or "Parallel"
            )
        )

        (
            roof_press_coeff_parallel,
            roof_payload_parallel,
            place_holder_1,
            place_holder_2,
        ) = self.generate_roof_cp(
            table_roof_under_10=self.table_roof_under_10,  # Your parsed low-slope DataFrame
            table_roof_over_10=self.table_roof_over_10,  # Your parsed steep-slope DataFrame
            eave_height=self.eave_height,
            apex_height=self.apex_height,
            b_value=self.b_input,
            l_value=self.l_input,
            ridge_direction=self.ridge_direction_input,
            wind_direction="Parallel",  # "Normal" or "Parallel"
        )

        mwfrs_normal_summary = self.generate_mwfrs_normal_to_ridge_table(
            df_wall_cp=wall_press_coeff_data,
            df_roof_payload=roof_payload_normal,
            df_velocity_profile=velocity_pressure,
            gust_effect_factor=self.gust_effect_factor,
            gcpi_pos=self.gcpi_pos,
            gcpi_neg=self.gcpi_neg,
        )

        mwfrs_parallel_summary = self.generate_mwfrs_parallel_to_ridge_table(
            df_wall_cp=wall_press_coeff_data,  # Your imported wall coefficients
            df_roof_payload=roof_payload_parallel,  # Your calculated stepped roof zones
            df_velocity_profile=velocity_pressure,  # Your height tracking profile
            gust_effect_factor=self.gust_effect_factor,
            gcpi_pos=self.gcpi_pos,
            gcpi_neg=self.gcpi_neg,
        )

        print(velocity_pressure)
        print(wall_press_coeff_data)
        print(roof_press_coeff_normal)
        print(roof_press_coeff_parallel)
        print(roof_payload_normal)
        print(roof_payload_parallel)
        print(mwfrs_normal_summary)
        print(mwfrs_parallel_summary)

        # ==========================================
        # 2.0 VELOCITY PRESSURE SECTION
        # ==========================================

        # 3. Build the Velocity Pressure Section
        with doc.create(Section("Velocity Pressure Profile (qz)")):
            doc.append(
                "The velocity pressure, evaluated at height z, is calculated using the following ASCE 7 formula:"
            )

            # ASCE 7 Formula (SI Units)
        doc.append(
            NoEscape(
                r"\begin{equation*} q_z = 0.613 K_z K_{zt} K_d K_e V^2 \end{equation*}"
            )
        )

        # Legend as a Bulleted Itemize list (matching the MWFRS layout)
        with doc.create(Itemize()) as itemize:
            itemize.add_item(
                NoEscape(r"$q_z$: Velocity pressure ($\text{N/m}^2$ or $\text{Pa}$)")
            )
            itemize.add_item(NoEscape(r"$V$: Basic wind speed ($\text{m/s}$)"))
            itemize.add_item(NoEscape(r"$K_d$: Wind directionality factor"))
            itemize.add_item(NoEscape(r"$K_e$: Ground elevation factor"))
            itemize.add_item(NoEscape(r"$K_{zt}$: Topographic factor"))
            itemize.add_item(NoEscape(r"$K_z$: Velocity pressure exposure coefficient"))

            # 4. Build the booktabs formatted table
            with doc.create(Subsection("Velocity Pressure Profile Table")):
                with doc.create(Tabular("r l r r", booktabs=True)) as table:
                    # Table Header
                    table.add_row(["Height (m)", "Type", "Kz", "qz (Pa)"])
                    table.add_hline()  # Adds the clean midrule line

                    vp_data_list = velocity_pressure.values.tolist()

                    # Populate Data Rows (Loop logic stays exactly the same!)
                    for row in vp_data_list:
                        formatted_row = [
                            row[0],
                            row[1],
                            f"{row[2]:.3f}"
                            if isinstance(row[2], (int, float))
                            else row[2],
                            f"{row[3]:.3f}"
                            if isinstance(row[3], (int, float))
                            else row[3],
                        ]
                        table.add_row(formatted_row)

        # ==========================================
        # 3.0 PRESSURE COEFFICIENTS SECTION
        # ==========================================
        with doc.create(Section("Pressure Coefficients (Cp)")):
            # --- 3.1 Wall Pressure Coefficients ---
            with doc.create(Subsection("Wall Pressure Coefficient (Cp) Table")):
                # Extract DataFrame if trapped inside a tuple
                df_wall = (
                    wall_press_coeff_data[0]
                    if isinstance(wall_press_coeff_data, tuple)
                    else wall_press_coeff_data
                )

                # Calculate dynamic column alignment (e.g., 4 cols -> 'l r r r')
                wall_cols = len(df_wall.columns)
                wall_align = "l " + " ".join(["r"] * (wall_cols - 1))

                with doc.create(Table(position="h!")) as table_env:
                    with table_env.create(
                        Tabular(wall_align, booktabs=True)
                    ) as tabular:
                        tabular.add_row(df_wall.columns.tolist())
                        tabular.add_hline()

                        wall_data = df_wall.fillna("").values.tolist()
                        for row in wall_data:
                            tabular.add_row(row)

            # --- 3.2 Roof Pressure Coefficients (Normal) ---
            with doc.create(
                Subsection(
                    "Roof Pressure Coefficient (Cp) Table - Normal Wind Direction"
                )
            ):
                # Extract DataFrame if trapped inside a tuple
                df_roof_norm = (
                    roof_press_coeff_normal[0]
                    if isinstance(roof_press_coeff_normal, tuple)
                    else roof_press_coeff_normal
                )

                # Calculate dynamic column alignment
                roof_norm_cols = len(df_roof_norm.columns)
                roof_norm_align = "l " + " ".join(["r"] * (roof_norm_cols - 1))

                with doc.create(Table(position="h!")) as table_env:
                    with table_env.create(
                        Tabular(roof_norm_align, booktabs=True)
                    ) as tabular:
                        tabular.add_row(df_roof_norm.columns.tolist())
                        tabular.add_hline()

                        roof_normal_data = df_roof_norm.fillna("").values.tolist()
                        for row in roof_normal_data:
                            tabular.add_row(row)

            # --- 3.3 Roof Pressure Coefficients (Parallel) ---
            with doc.create(
                Subsection(
                    "Roof Pressure Coefficient (Cp) Table - Parallel Wind Direction"
                )
            ):
                # Extract DataFrame if trapped inside a tuple
                df_roof_par = (
                    roof_press_coeff_parallel[0]
                    if isinstance(roof_press_coeff_parallel, tuple)
                    else roof_press_coeff_parallel
                )

                # Calculate dynamic column alignment
                roof_par_cols = len(df_roof_par.columns)
                roof_par_align = "l " + " ".join(["r"] * (roof_par_cols - 1))

                with doc.create(Table(position="h!")) as table_env:
                    with table_env.create(
                        Tabular(roof_par_align, booktabs=True)
                    ) as tabular:
                        tabular.add_row(df_roof_par.columns.tolist())
                        tabular.add_hline()

                        roof_parallel_data = df_roof_par.fillna("").values.tolist()
                        for row in roof_parallel_data:
                            tabular.add_row(row)

        # ==========================================
        # 4.0 MWFRS PRESSURE SUMMARY SECTION
        # ==========================================
        with doc.create(Section("MWFRS Pressure Summary")):
            # --- Equation Explanation ---
            doc.append(
                "Design wind pressures for the Main Wind-Force Resisting System (MWFRS) "
            )
            doc.append(
                "are determined in accordance with ASCE 7 using the following equation:"
            )

            # Corrected Equation
            doc.append(
                NoEscape(
                    r"\begin{equation*} p = q G C_p - q_i (GC_{pi}) \end{equation*}"
                )
            )

            # Bulleted Symbol List
            with doc.create(Itemize()) as itemize:
                itemize.add_item(NoEscape(r"$p$: Design wind pressure (Pa)"))
                itemize.add_item(
                    NoEscape(
                        r"$q$: Velocity pressure (Pa), evaluated at height $z$ for windward walls, or at height $h$ for leeward/side walls and roofs"
                    )
                )
                itemize.add_item(
                    NoEscape(
                        r"$q_i$: Internal velocity pressure (Pa), evaluated at mean roof height $h$"
                    )
                )
                itemize.add_item(NoEscape(r"$G$: Gust-effect factor"))
                itemize.add_item(NoEscape(r"$C_p$: External pressure coefficient"))
                itemize.add_item(NoEscape(r"$GC_{pi}$: Internal pressure coefficient"))

            doc.append(NoEscape(r"\vspace{0.5cm}"))

            # --- Table Helper for Dynamic Generation ---
            def build_summary_table(df_input, table_title):
                df = df_input[0] if isinstance(df_input, tuple) else df_input

                with doc.create(Subsection(table_title)):
                    doc.append(NoEscape(r"\vspace{2em}"))
                    num_cols = len(df.columns)
                    align_str = "l " + " ".join(["r"] * (num_cols - 1))

                    # Replace Center() with FlushLeft() to align flush to the left margin
                    with doc.create(FlushLeft()) as left_env:
                        with left_env.create(
                            Tabular(align_str, booktabs=True)
                        ) as tabular:
                            # Format column headers cleanly
                            clean_headers = []
                            for col in df.columns:
                                col_str = str(col)
                                col_str = col_str.replace("C_p", "$C_p$")
                                col_str = col_str.replace("(+GCpi)", r"(+$GC_{pi}$)")
                                col_str = col_str.replace("(-GCpi)", r"(-$GC_{pi}$)")

                                if "$" not in col_str:
                                    col_str = col_str.replace("_", r"\_")

                                clean_headers.append(NoEscape(col_str))

                            tabular.add_row(clean_headers)
                            tabular.add_hline()

                            # Clean NaNs and populate rows
                            clean_data = df.fillna("").values.tolist()
                            for row in clean_data:
                                tabular.add_row(row)

                    doc.append(NoEscape(r"\vspace{1.5em}"))

            # --- 4.1 Summary Table - Normal Wind Direction ---
            # Replace 'mwfrs_normal_df' with your actual DataFrame variable name
            build_summary_table(
                mwfrs_normal_summary,
                "MWFRS Pressure Summary Table - Normal Wind Direction",
            )

            # --- 4.2 Summary Table - Parallel Wind Direction ---
            # Replace 'mwfrs_parallel_df' with your actual DataFrame variable name
            build_summary_table(
                mwfrs_parallel_summary,
                "MWFRS Pressure Summary Table - Parallel Wind Direction",
            )

        # 5. Compile the PDF

        try:
            doc.generate_pdf(save_path, clean_tex=True, compiler="pdflatex")
            self.sheet.range("D23").value = "PDF Exported Successfully!"
            
        except subprocess.CalledProcessError as e:
            # This catches the specific error where LaTeX runs but the math/syntax is bad
            self.sheet.range("D23").value = "PDF EXPORT FAILED: LaTeX Syntax Error"
            print(f"LaTeX Compilation Failed: {e}")

        except FileNotFoundError as e:
            # This catches the error if MiKTeX isn't installed or added to PATH
            self.sheet.range("D23").value = "PDF EXPORT FAILED: Compiler Not Found"
            print(f"LaTeX Compiler missing: {e}")

# ARBITRARY/DUMMY FUNCTION TO TEST MAIN SCRIPT FROM EXCEL BUTTON
def calculate_wind_loads():
    """
    Hook for the Excel VBA Macro.
    Builds the class and runs the orchestrator.
    """
    wb = xw.Book.caller()
    main_sheet = wb.sheets.active  # Ensure this matches your tab name

    # Initialize class and run
    wind_calculation_instance_for_excel_display = WindLoadCalculatorDirectionalASCE7(
        target_sheet=main_sheet,
        table_vel_pres_coef="L5",
        wall_press_coeff_data="Q4",
        table_int_pres_coef="L31",
        table_roof_over_10="R14",
        table_roof_under_10="S23",
        building_class="C2",
        basic_wind_speed="C3",
        enclosure_class="C4",
        exposure_category="C5",
        wind_dir_factor="C6",
        topographic_factor="C7",
        ground_elevation_factor="C8",
        gust_effect_factor="C9",
        velocity_pressure="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
    )
    wind_calculation_instance_for_excel_display.calculate_wind_load()


# ARBITRARY/DUMMY FUNCTION TO TEST PDF EXPORT FROM EXCEL BUTTON
def export_pdf_wind_loads():
    """Hook for the Excel 'Export Calcs' VBA Macro."""
    wb = xw.Book.caller()
    main_sheet = wb.sheets.active

    # Initialize the class and run calculations
    wind_calculation_instance_for_pdf_export = WindLoadCalculatorDirectionalASCE7(
        target_sheet=main_sheet,
        table_vel_pres_coef="L5",
        wall_press_coeff_data="Q4",
        table_int_pres_coef="L31",
        table_roof_over_10="R14",
        table_roof_under_10="S23",
        building_class="C2",
        basic_wind_speed="C3",
        enclosure_class="C4",
        exposure_category="C5",
        wind_dir_factor="C6",
        topographic_factor="C7",
        ground_elevation_factor="C8",
        gust_effect_factor="C9",
        velocity_pressure="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
    )
    wind_calculation_instance_for_pdf_export.calculate_wind_load()

    # Call the class method directly
    wind_calculation_instance_for_pdf_export.generate_pdf_report()


# RUN CONDITIONS WHEN SCRIPT IS EXECUTED DIRECTLY (FOR TESTING PURPOSES)
if __name__ == "__main__":
    # Safety catch for IDE testing
    xw.Book("wind_load_calculator_asce7.xlsm").set_mock_caller()

    # 2. Instantiate your calculator
    wind_calculation_instance_for_debugging = WindLoadCalculatorDirectionalASCE7(
        xw.Book.caller().sheets.active,
        table_vel_pres_coef="L5",
        wall_press_coeff_data="Q4",
        table_int_pres_coef="L31",
        table_roof_over_10="R14",
        table_roof_under_10="S23",
        building_class="C2",
        basic_wind_speed="C3",
        enclosure_class="C4",
        exposure_category="C5",
        wind_dir_factor="C6",
        topographic_factor="C7",
        ground_elevation_factor="C8",
        gust_effect_factor="C9",
        velocity_pressure="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
    )

    wind_calculation_instance_for_debugging.calculate_wind_load()

    # 3. Call the method you want to test
    wind_calculation_instance_for_debugging.generate_pdf_report()
