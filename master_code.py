"""
Main orchestration module for the STRUCTURAL ANALYSIS TOOL.
Handles Excel interfacing via xlwings, data processing with pandas,
and PDF report generation via PyLaTeX.
"""

import xlwings as xw
from utilities.etabs_api import ETABSConnector
from utilities.wind_calculator_directional_asce7 import WindLoadCalculatorDirectionalASCE7

# FUNCTION TO TRIGGER WIND LOAD CALCULATION FROM EXCEL BUTTON
def calculate_wind_loads():
    """
    Hook for the Excel VBA Macro.
    Builds the class and runs the orchestrator.
    """
    wb = xw.Book.caller()
    main_sheet = wb.sheets.active  # Ensure this matches your tab name

    # Initialize class and run
    wind_calculation_instance_for_excel_display = WindLoadCalculatorDirectionalASCE7(
        active_sheet=main_sheet,
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
        velocity_pressure_output="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
        gcpi_pos_output="C11",
        gci_neg_output="C12"
    )
    wind_calculation_instance_for_excel_display.calculate_wind_load()

# FUNCTION TO TRIGGER PDF EXPORT FROM EXCEL BUTTON
def export_pdf_wind_loads():
    """Hook for the Excel 'Export Calcs' VBA Macro."""
    wb = xw.Book.caller()
    main_sheet = wb.sheets.active

    # Initialize the class and run calculations
    wind_calculation_instance_for_pdf_export = WindLoadCalculatorDirectionalASCE7(
        active_sheet=main_sheet,
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
        velocity_pressure_output="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
        gcpi_pos_output="C11",
        gci_neg_output="C12"
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
        velocity_pressure_output="C10",
        internal_pressure_coefficient_pos="C11",
        internal_pressure_coefficient_neg="C12",
        l_input="C13",
        b_input="C14",
        ridge_direction_input="C15",
        raw_heights="C16",
        eave_height="C18",
        apex_height="C19",
        gcpi_pos_output="C11",
        gci_neg_output="C12"
    )

    wind_calculation_instance_for_debugging.calculate_wind_load()

    # 3. Call the method you want to test
    wind_calculation_instance_for_debugging.generate_pdf_report()
