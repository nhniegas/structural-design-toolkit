"""
Main orchestration module for the STRUCTURAL ANALYSIS TOOL.
Handles Excel interfacing via xlwings, data processing with pandas,
and PDF report generation via PyLaTeX.
"""

import math
import os
import xlwings as xw
import ezdxf
import pandas as pd
from utilities._etabs_api import ETABSConnector
from utilities._etabs_data_extraction import ETABSDataExporter
from utilities._gui_helpers import (
    DualListboxSelector,
    LoadingWindow,
    show_warning,
    select_output_directory,
)
from utilities._wind_calculator_directional_asce7 import (
    WindLoadCalculatorDirectionalASCE7,
)
from utilities._beam_designer_aci318 import identify_cantilever_beams
from utilities._beam_designer_aci318_emp import execute_beam_design


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
    dl_cell: str = "I4",  # Main Bar Ø (mm)
    ds_cell: str = "I5",  # Stirrups Bar Diameter Ø (mm)
    dw_cell: str = "I6",  # Web Bar Diameter Ø (mm)
    fyw_cell: str = "I7",  # Web Bar, Fyw (Mpa)
    cc_cell: str = "I8",  # Concrete Cover (mm)
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
        beam_df["fyw"] = overwrites_sheet.range(fyw_cell).value
        beam_df["dm"] = overwrites_sheet.range(dl_cell).value
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


def run_beam_design_from_excel():
    """Triggered by the DESIGN REINFORCEMENTS button in Excel or IDE."""

    # 1. Connect to Excel (Handles both VBA button clicks and IDE testing)
    try:
        wb = xw.Book.caller()
    except Exception:
        # Fallback for IDE testing: connects to the currently active Excel window
        wb = xw.books.active

        # Alternatively, you can hardcode the filename to be perfectly safe:
        # wb = xw.Book('Your_Excel_File_Name.xlsm')

    # 2. Assign Sheets
    sht_ow = wb.sheets["OVERWRITES"]
    sht_loads = wb.sheets["FACTORED LOADS"]
    sht_design = wb.sheets["BEAM DESIGN"]

    # 2. Assign Sheets
    sht_ow = wb.sheets["OVERWRITES"]
    sht_loads = wb.sheets["FACTORED LOADS"]
    sht_design = wb.sheets["BEAM DESIGN"]

    # 3. Read Parameters (Assuming linked checkbox is F4 and combo is F5)
    enable_seismic_design = sht_ow.range("F3").value is True
    gravity_combo_name = sht_ow.range("F4").value

    # 4. Extract DataFrames
    # expand='table' pulls the contiguous data block starting at A1
    df_frame_forces = (
        sht_loads.range("B2")
        .options(pd.DataFrame, header=1, index=False, expand="table")
        .value
    )
    df_beam_props = (
        sht_design.range("B8")
        .options(pd.DataFrame, header=1, index=False, expand="table")
        .value
    )

    # 5. Execute the Design Engine
    # Pass the extracted Excel data directly into your design function
    df_beam_design_results = execute_beam_design(
        df_beam_props=df_beam_props,
        df_frame_forces=df_frame_forces,
        enable_seismic_design=enable_seismic_design,
        gravity_combo_name=gravity_combo_name,
    )

    # 6. Paste Results Back to Excel (Starting at B8)
    sht_design.range("B8").options(index=False).value = df_beam_design_results

    # 7. Apply Advanced Visual Formatting (Shifted to B8)
    cols = df_beam_design_results.columns.tolist()
    num_rows = len(df_beam_design_results)
    num_cols = len(cols)

    if num_rows > 0:
        start_row = 8
        start_col = 2  # Column B

        # Dynamically find 0-based column offsets for all categories
        idx_main = cols.index("n_left_L1") if "n_left_L1" in cols else num_cols
        idx_web = (
            cols.index("n_side_per_face_gov")
            if "n_side_per_face_gov" in cols
            else num_cols
        )
        idx_stirrups = (
            cols.index("Stirrup_Legs") if "Stirrup_Legs" in cols else num_cols
        )
        idx_check = (
            cols.index("Anchorage_Check") if "Anchorage_Check" in cols else num_cols
        )

        rng_all = sht_design.range(
            (start_row, start_col), (start_row + num_rows, start_col + num_cols - 1)
        )

        # 1. CLEAR ALL BORDERS: -4142 is xlNone (Removes intermediate vertical/horizontal lines)
        rng_all.api.Borders.LineStyle = -4142

        # --- A. Format Headers (Row 8) ---
        header_rng = sht_design.range(
            (start_row, start_col), (start_row, start_col + num_cols - 1)
        )
        header_rng.font.bold = True

        # Bottom Border (xlEdgeBottom = 9)
        header_rng.api.Borders(9).LineStyle = 1
        header_rng.api.Borders(9).Weight = 3

        # Top Border (xlEdgeTop = 8)
        header_rng.api.Borders(8).LineStyle = 1
        header_rng.api.Borders(8).Weight = 3

        # Group 1: Properties & Forces (Light Blue)
        if idx_main > 0:
            sht_design.range(
                (start_row, start_col), (start_row, start_col + idx_main - 1)
            ).color = (189, 215, 238)
        # Group 2: Main Bars (Light Green)
        if idx_web > idx_main:
            sht_design.range(
                (start_row, start_col + idx_main), (start_row, start_col + idx_web - 1)
            ).color = (226, 239, 218)
        # Group 3: Web Reinforcement (Light Yellow)
        if idx_stirrups > idx_web:
            sht_design.range(
                (start_row, start_col + idx_web),
                (start_row, start_col + idx_stirrups - 1),
            ).color = (255, 242, 204)
        # Group 4: Stirrups / Shear (Light Orange)
        if idx_check > idx_stirrups:
            sht_design.range(
                (start_row, start_col + idx_stirrups),
                (start_row, start_col + idx_check - 1),
            ).color = (252, 228, 214)
        # Group 5: Post-Checks (Light Purple/Gray)
        if num_cols > idx_check:
            sht_design.range(
                (start_row, start_col + idx_check),
                (start_row, start_col + num_cols - 1),
            ).color = (222, 235, 247)

        # --- B. Alternate Row Colors & Horizontal Beam Separators ---
        # Step by 2 to grab both TOP and BOTTOM rows for a single beam at once
        for i in range(0, num_rows, 2):
            row_block = sht_design.range(
                (start_row + i + 1, start_col),
                (start_row + i + 2, start_col + num_cols - 1),
            )

            # Apply Alternating Fill
            if (i // 2) % 2 == 0:
                row_block.color = (242, 242, 242)  # Light Gray
            else:
                row_block.color = (255, 255, 255)  # White

            # Add border ONLY to the bottom of the 2-row block (Separates beams, ignores top/bot inside)
            row_block.api.Borders(9).LineStyle = 1  # xlEdgeBottom
            row_block.api.Borders(9).Weight = 2  # xlThin

        # --- C. Thick Vertical Section Grouping Borders ---
        def set_thick_right_border(col_offset):
            if 0 < col_offset < num_cols:
                target_col = start_col + col_offset - 1
                sht_design.range(
                    (start_row, target_col), (start_row + num_rows, target_col)
                ).api.Borders(
                    10
                ).LineStyle = 1  # 10 = xlEdgeRight
                sht_design.range(
                    (start_row, target_col), (start_row + num_rows, target_col)
                ).api.Borders(
                    10
                ).Weight = 3  # Medium/Thick Line

        # This keeps the thick vertical lines organizing the main categories,
        # while standard vertical gridlines remain hidden.
        set_thick_right_border(idx_main)
        set_thick_right_border(idx_web)
        set_thick_right_border(idx_stirrups)
        set_thick_right_border(idx_check)

    sht_design.autofit()


def generate_dxf_beam_schedule(
    story_name: str, df_story: pd.DataFrame, output_filepath: str
):
    """Generates a structured CAD Beam Schedule DXF table for a single story using ezdxf."""
    # 1. Initialize DXF Document with SIMPLEX Text Style
    doc = ezdxf.new(dxfversion="R2018")
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})

    msp = doc.modelspace()

    # 2. Define Table Geometry (Column Widths & Row Heights in mm)
    col_widths = [
        35.0,  # 0: Beam Mark
        20.0,  # 1: Section B
        20.0,  # 2: Section H
        18.0,  # 3: LAYER
        25.0,  # 4: Left Support Top
        25.0,  # 5: Left Support Bottom
        25.0,  # 6: Mid Support Top
        25.0,  # 7: Mid Support Bottom
        25.0,  # 8: Right Support Top
        25.0,  # 9: Right Support Bottom
        25.0,  # 10: Web Bars
        140.0,  # 11: Stirrups (Increased to 140.0 mm to prevent grid line overlap)
        30.0,  # 12: Remarks
    ]

    col_x = [0.0]
    for w in col_widths:
        col_x.append(col_x[-1] + w)

    header_h = 8.0  # Height per header row (2 rows = 16mm)
    subrow_h = 8.0  # Height per layer sub-row (3 sub-rows = 24mm per beam)

    def add_cell_text(
        text, x_left, y_top, width, height, font_size=2.5, style="SIMPLEX"
    ):
        cx = x_left + width / 2.0
        cy = y_top - height / 2.0
        text_ent = msp.add_text(
            str(text), dxfattribs={"style": style, "height": font_size, "color": 7}
        )
        text_ent.set_placement(
            (cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER
        )

    def draw_box(x1, y1, x2, y2, lineweight=25):
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        msp.add_lwpolyline(pts, dxfattribs={"color": 7, "lineweight": lineweight})

    # 3. Draw Main Table Headers
    y_curr = 0.0

    # Header Outer Box
    draw_box(col_x[0], y_curr, col_x[-1], y_curr - 2 * header_h, lineweight=35)

    # Header Column Titles & Dividers
    add_cell_text(
        "Beam Mark", col_x[0], y_curr, col_widths[0], 2 * header_h, font_size=3.0
    )
    msp.add_line(
        (col_x[1], y_curr),
        (col_x[1], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Section",
        col_x[1],
        y_curr,
        col_widths[1] + col_widths[2],
        header_h,
        font_size=3.0,
    )
    add_cell_text(
        "B (mm)",
        col_x[1],
        y_curr - header_h,
        col_widths[1],
        header_h,
        font_size=2.5,
    )
    add_cell_text(
        "H (mm)",
        col_x[2],
        y_curr - header_h,
        col_widths[2],
        header_h,
        font_size=2.5,
    )
    msp.add_line(
        (col_x[1], y_curr - header_h),
        (col_x[3], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[2], y_curr - header_h),
        (col_x[2], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[3], y_curr),
        (col_x[3], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text("LAYER", col_x[3], y_curr, col_widths[3], 2 * header_h, font_size=2.5)
    msp.add_line(
        (col_x[4], y_curr),
        (col_x[4], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Left Support",
        col_x[4],
        y_curr,
        col_widths[4] + col_widths[5],
        header_h,
        font_size=3.0,
    )
    add_cell_text(
        "Top", col_x[4], y_curr - header_h, col_widths[4], header_h, font_size=2.5
    )
    add_cell_text(
        "Bottom",
        col_x[5],
        y_curr - header_h,
        col_widths[5],
        header_h,
        font_size=2.5,
    )
    msp.add_line(
        (col_x[4], y_curr - header_h),
        (col_x[6], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[5], y_curr - header_h),
        (col_x[5], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[6], y_curr),
        (col_x[6], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Mid Support",
        col_x[6],
        y_curr,
        col_widths[6] + col_widths[7],
        header_h,
        font_size=3.0,
    )
    add_cell_text(
        "Top", col_x[6], y_curr - header_h, col_widths[6], header_h, font_size=2.5
    )
    add_cell_text(
        "Bottom",
        col_x[7],
        y_curr - header_h,
        col_widths[7],
        header_h,
        font_size=2.5,
    )
    msp.add_line(
        (col_x[6], y_curr - header_h),
        (col_x[8], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[7], y_curr - header_h),
        (col_x[7], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[8], y_curr),
        (col_x[8], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Right Support",
        col_x[8],
        y_curr,
        col_widths[8] + col_widths[9],
        header_h,
        font_size=3.0,
    )
    add_cell_text(
        "Top", col_x[8], y_curr - header_h, col_widths[8], header_h, font_size=2.5
    )
    add_cell_text(
        "Bottom",
        col_x[9],
        y_curr - header_h,
        col_widths[9],
        header_h,
        font_size=2.5,
    )
    msp.add_line(
        (col_x[8], y_curr - header_h),
        (col_x[10], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[9], y_curr - header_h),
        (col_x[9], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[10], y_curr),
        (col_x[10], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Web Bars", col_x[10], y_curr, col_widths[10], 2 * header_h, font_size=2.5
    )
    msp.add_line(
        (col_x[11], y_curr),
        (col_x[11], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Stirrups", col_x[11], y_curr, col_widths[11], 2 * header_h, font_size=3.0
    )
    msp.add_line(
        (col_x[12], y_curr),
        (col_x[12], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )

    add_cell_text(
        "Remarks", col_x[12], y_curr, col_widths[12], 2 * header_h, font_size=3.0
    )

    y_curr -= 2 * header_h

    # 4. Populate Data Rows per Beam
    unique_beams = df_story["UniqueName"].unique()

    for u_name in unique_beams:
        df_beam = df_story[df_story["UniqueName"] == u_name]
        top_row = df_beam[df_beam["Face"] == "TOP"].iloc[0]
        bot_row = df_beam[df_beam["Face"] == "BOTTOM"].iloc[0]

        beam_h = 3 * subrow_h
        y_beam_start = y_curr

        add_cell_text(
            u_name, col_x[0], y_beam_start, col_widths[0], beam_h, font_size=2.5
        )

        b_val = int(top_row.get("Width", top_row.get("b", 300)))
        h_val = int(top_row.get("Depth", top_row.get("Height", 500)))
        add_cell_text(
            b_val, col_x[1], y_beam_start, col_widths[1], beam_h, font_size=2.5
        )
        add_cell_text(
            h_val, col_x[2], y_beam_start, col_widths[2], beam_h, font_size=2.5
        )

        n_side = int(top_row.get("n_side_per_face_gov", 0))
        dw = int(top_row.get("dw", top_row.get("dm", 12)))
        web_bars_str = f"{2 * n_side} - {dw}Ø" if n_side > 0 else "-"
        add_cell_text(
            web_bars_str,
            col_x[10],
            y_beam_start,
            col_widths[10],
            beam_h,
            font_size=2.5,
        )

        # Stirrups Detailing (Font retained at 2.2, cell width widened to 140mm)
        ds = int(top_row.get("ds", top_row.get("d_stirrup", 10)))
        sp_2h = float(top_row.get("Spacing_2H", 100))
        sp_mid = float(top_row.get("Spacing_Mid", 200))
        two_h = 2.0 * h_val
        n_2h = int(two_h / sp_2h) if sp_2h > 0 else 0
        stirrups_str = (
            f"{ds}Ø - 1@50mm, {n_2h}@{int(sp_2h)}mm; REST @{int(sp_mid)}mm o.c."
        )
        add_cell_text(
            stirrups_str,
            col_x[11],
            y_beam_start,
            col_widths[11],
            beam_h,
            font_size=2.2,
        )

        legs = int(top_row.get("Stirrup_Legs", 2))
        type_num = max(1, legs - 1)
        remarks_str = f"TYPE - {type_num}"
        add_cell_text(
            remarks_str,
            col_x[12],
            y_beam_start,
            col_widths[12],
            beam_h,
            font_size=2.5,
        )

        dm = int(top_row.get("dm", top_row.get("d_main", 25)))

        for layer_idx in [1, 2, 3]:
            y_sub_top = y_beam_start - (layer_idx - 1) * subrow_h

            add_cell_text(
                layer_idx,
                col_x[3],
                y_sub_top,
                col_widths[3],
                subrow_h,
                font_size=2.5,
            )

            n_top_left = int(top_row.get(f"n_left_L{layer_idx}", 0))
            n_bot_left = int(bot_row.get(f"n_left_L{layer_idx}", 0))

            n_top_mid = int(top_row.get(f"n_mid_L{layer_idx}", 0))
            n_bot_mid = int(bot_row.get(f"n_mid_L{layer_idx}", 0))

            n_top_right = int(top_row.get(f"n_right_L{layer_idx}", 0))
            n_bot_right = int(bot_row.get(f"n_right_L{layer_idx}", 0))

            str_top_left = f"{n_top_left} - {dm}Ø" if n_top_left > 0 else "-"
            str_bot_left = f"{n_bot_left} - {dm}Ø" if n_bot_left > 0 else "-"

            str_top_mid = f"{n_top_mid} - {dm}Ø" if n_top_mid > 0 else "-"
            str_bot_mid = f"{n_bot_mid} - {dm}Ø" if n_bot_mid > 0 else "-"

            str_top_right = f"{n_top_right} - {dm}Ø" if n_top_right > 0 else "-"
            str_bot_right = f"{n_bot_right} - {dm}Ø" if n_bot_right > 0 else "-"

            add_cell_text(
                str_top_left,
                col_x[4],
                y_sub_top,
                col_widths[4],
                subrow_h,
                font_size=2.3,
            )
            add_cell_text(
                str_bot_left,
                col_x[5],
                y_sub_top,
                col_widths[5],
                subrow_h,
                font_size=2.3,
            )
            add_cell_text(
                str_top_mid,
                col_x[6],
                y_sub_top,
                col_widths[6],
                subrow_h,
                font_size=2.3,
            )
            add_cell_text(
                str_bot_mid,
                col_x[7],
                y_sub_top,
                col_widths[7],
                subrow_h,
                font_size=2.3,
            )
            add_cell_text(
                str_top_right,
                col_x[8],
                y_sub_top,
                col_widths[8],
                subrow_h,
                font_size=2.3,
            )
            add_cell_text(
                str_bot_right,
                col_x[9],
                y_sub_top,
                col_widths[9],
                subrow_h,
                font_size=2.3,
            )

            if layer_idx < 3:
                y_line = y_sub_top - subrow_h
                msp.add_line(
                    (col_x[3], y_line),
                    (col_x[10], y_line),
                    dxfattribs={"color": 7},
                )

        y_beam_end = y_beam_start - beam_h
        draw_box(col_x[0], y_beam_start, col_x[-1], y_beam_end, lineweight=25)

        for idx in range(1, len(col_x) - 1):
            msp.add_line(
                (col_x[idx], y_beam_start),
                (col_x[idx], y_beam_end),
                dxfattribs={"color": 7},
            )

        y_curr = y_beam_end

    doc.saveas(output_filepath)


def export_cad_drawings():
    """Triggered by the EXPORT CAD DRAWINGS button in Excel."""
    output_dir = select_output_directory()
    if not output_dir:
        return  # User canceled folder selection

    try:
        wb = xw.Book.caller()
    except Exception:
        wb = xw.books.active

    sht_design = wb.sheets["BEAM DESIGN"]

    df_results = (
        sht_design.range("B8")
        .options(pd.DataFrame, header=1, index=False, expand="table")
        .value
    )

    if df_results.empty:
        return

    stories = df_results["Story"].unique()
    for story in stories:
        df_story = df_results[df_results["Story"] == story]
        filename = f"{story}_Beam_Schedule.dxf"
        filepath = os.path.join(output_dir, filename)

        generate_dxf_beam_schedule(
            story_name=str(story),
            df_story=df_story,
            output_filepath=filepath,
        )


if __name__ == "__main__":
    export_cad_drawings()
