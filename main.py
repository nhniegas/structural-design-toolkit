"""
Main orchestration module for the STRUCTURAL ANALYSIS TOOL.
Handles Excel interfacing via xlwings, data processing with pandas,
and PDF report generation via PyLaTeX.
"""

import xlwings as xw
import pandas as pd
from utilities._etabs_api import ETABSConnector
from utilities._etabs_data_extraction import ETABSDataExporter
from utilities._gui_helpers import (
    DualListboxSelector,
    LoadingWindow,
    show_warning,
)
from utilities._wind_calculator_directional_asce7 import (
    WindLoadCalculatorDirectionalASCE7,
)
from utilities._beam_designer_aci318 import identify_cantilever_beams


# FUNCTION TO TRIGGER WIND LOAD CALCULATION FROM EXCEL BUTTON
def calculate_wind_loads():
    """
    Hook for the Excel VBA Macro.
    Builds the class and runs the orchestrator.
    """
    with LoadingWindow("Calculating Wind Load..."):
        wb = xw.Book.caller()
        main_sheet = wb.sheets.active  # Ensure this matches your tab name

        # Initialize class and run
        wind_calculation_instance_for_excel_display = (
            WindLoadCalculatorDirectionalASCE7(
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
                gci_neg_output="C12",
            )
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
        gci_neg_output="C12",
    )
    with LoadingWindow("Exporting PDF Report..."):
        wind_calculation_instance_for_pdf_export.calculate_wind_load()

    # Call the class method directly
    wind_calculation_instance_for_pdf_export.generate_pdf_report()


# FUNCTION TO TRIGGER EXTRACT ETABS BEAM DATA TO EXCEL
def extract_forces_properties_from_etabs():
    """Triggers the full workflow to extract design forces and section properties
    from ETABS, process materials/dimensions, and export results to Excel.
    """
    etabs_instance = ETABSConnector()

    etabs_instance.connect()
    # tabs_instance.open_model(file_path)
    # etabs_instance.run_analysis()

    # Instantiate exporter passing the connected etabs_instance
    exporter = ETABSDataExporter(etabs_instance)

    # 1. Load Combination Selection
    try:
        load_combos_filtered = exporter.get_load_combinations()
        load_combos_selected = DualListboxSelector(
            "Select Load Combinations", load_combos_filtered
        ).show()
    except Exception:
        show_warning(title="Warning", message="File Not Found")
        return

    etabs_instance.clear_load_combinations(load_combos_filtered)
    etabs_instance.set_load_combinations(load_combos_selected)

    with LoadingWindow("Running Concrete Design..."):
        etabs_instance.run_concrete_design()

    # 2. Member Selection
    with LoadingWindow("Extracting Members..."):
        members = exporter.get_available_members()
    members_selected = DualListboxSelector("Select Members to Design", members).show()

    with LoadingWindow("Extracting Data..."):
        # 3. Export Operations
        exporter.display_selected_inputs(
            load_combos=load_combos_selected,
            members=members_selected,
            sheet_name="OVERWRITES",
            combo_cell="B6",
            member_cell="C6",
        )

        # Populate Factored Gravity Load dropdown menu
        exporter.display_factored_gravity_loads_menu(
            load_combos=load_combos_selected,
            sheet_name="OVERWRITES",
            dropdown_cell="F35",  # Cell coordinate containing '*default'
        )

        design_forces = exporter.display_factored_loads(
            load_combos_selected=load_combos_selected,
            members_selected=members_selected,
            sheet_name="FACTORED LOADS",
            start_cell="B2",
        )

        frame_assignments = exporter.display_frame_data(
            members_selected=members_selected,
            sheet_name="FRAME DATA",
            start_cell="B2",
        )

        connectivity_data = exporter.display_connectivity_data(
            sheet_name="CONNECTIVITY",
            start_cell="B2",
        )


def extract_beam_design_data(
    frame_sheet_name: str = "FRAME DATA",
    frame_cell_ref: str = "B2",
    conn_sheet_name: str = "CONNECTIVITY",
    conn_cell_ref: str = "B2",
    overwrites_sheet_name: str = "OVERWRITES",
    dl_cell: str = "J4",  # Main Bar Ø (mm)
    ds_cell: str = "J5",  # Stirrups Bar Diameter Ø (mm)
    dw_cell: str = "J6",  # Web Bar Diameter Ø (mm)
    fyw_cell: str = "J7",  # Web Bar, Fyw (Mpa)
    cc_cell: str = "J8",  # Concrete Cover (mm)
    output_sheet_name: str = "BEAM DESIGN",
    output_cell_ref: str = "B8",
    clear_start_cell: str = "B8",
    header_color: tuple = (189, 215, 238),
) -> pd.DataFrame:
    """Processes full frame and connectivity data to identify support conditions,

    filters the final table to display ONLY 'Beam' members, and exports to Excel.
    """
    with LoadingWindow("Extracting Beam Design Data..."):
        try:
            wb = xw.Book.caller()
        except Exception:
            wb = xw.books.active

        # 1. Read input DataFrames from Excel
        frame_sheet = wb.sheets[frame_sheet_name]
        conn_sheet = wb.sheets[conn_sheet_name]
        overwrites_sheet = wb.sheets[overwrites_sheet_name]

        frame_df = (
            frame_sheet.range(frame_cell_ref)
            .options(pd.DataFrame, expand="table", index=False)
            .value
        )
        conn_df = (
            conn_sheet.range(conn_cell_ref)
            .options(pd.DataFrame, expand="table", index=False)
            .value
        )

        # Clean column headers
        frame_df.columns = [str(c).strip() for c in frame_df.columns]
        conn_df.columns = [str(c).strip() for c in conn_df.columns]

        # 2. Get support status using ALL frame and connectivity data
        support_df = identify_cantilever_beams(frame_df, conn_df)

        # 3. Merge SupportStatus onto full frame_df
        beam_df = frame_df.merge(support_df, on="UniqueName", how="left")

        # 4. Filter to display ONLY 'Beam' DesignType members in the output
        if "DesignType" in beam_df.columns:
            beam_df = beam_df[beam_df["DesignType"] == "Beam"].copy()

        # 5. Reorder SupportStatus right after SectProp
        cols = list(beam_df.columns)
        if "SupportStatus" in cols:
            cols.remove("SupportStatus")
        if "SectProp" in cols:
            sect_idx = cols.index("SectProp")
            cols.insert(sect_idx + 1, "SupportStatus")
        beam_df = beam_df[cols]

        # 6. Append reinforcement overwrite values from OVERWRITES sheet
        beam_df["Fyw"] = overwrites_sheet.range(fyw_cell).value
        beam_df["dl"] = overwrites_sheet.range(dl_cell).value
        beam_df["ds"] = overwrites_sheet.range(ds_cell).value
        beam_df["dw"] = overwrites_sheet.range(dw_cell).value
        beam_df["cc"] = overwrites_sheet.range(cc_cell).value

        # 7. Clear target area starting from clear_start_cell
        beam_sheet = wb.sheets[output_sheet_name]
        try:
            beam_sheet.range(clear_start_cell).expand().clear()
        except Exception:
            beam_sheet.range(clear_start_cell).clear()

        # 8. Write filtered DataFrame at output_cell_ref and apply header fill
        beam_sheet.range(output_cell_ref).options(index=False).value = beam_df

        header_range = beam_sheet.range(output_cell_ref).expand("right")
        header_range.color = header_color

        print(
            f"Beam design data successfully exported to {output_sheet_name}!{output_cell_ref}"
        )
        return beam_df


# RUN CONDITIONS WHEN SCRIPT IS EXECUTED DIRECTLY (FOR TESTING PURPOSES)
if __name__ == "__main__":
    # Safety catch for IDE testing
    extract_forces_properties_from_etabs()
