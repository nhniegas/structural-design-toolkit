"""Excel callback facade for the structural design workbook.

The engineering workflows live in their dedicated modules.  This module keeps
the historical ``main`` import path available for existing VBA callbacks while
avoiding duplicate beam, column, and DXF implementations.
"""

import xlwings as xw

from design import composite_column_designer_aiscDG06 as _composite
from design.column_designer_aci318 import (
    export_column_cad_drawings,
    run_column_design_from_excel,
)
from design.beam_designer_aci318 import (
    export_cad_drawings,
    extract_beam_design_data,
    extract_forces_properties_from_etabs,
    generate_dxf_beam_schedule,
    run_beam_design_from_excel,
)
from design.wind_calculator_directional_asce7 import (
    WindLoadCalculatorDirectionalASCE7,
)
from utilities._gui_helpers import LoadingWindow


def calculate_wind_loads() -> None:
    """Calculate wind loads using inputs read from the active Excel sheet."""
    workbook = xw.Book.caller()
    active_sheet = workbook.sheets.active
    calculator = _build_wind_calculator(active_sheet)
    with LoadingWindow("Calculating Wind Load..."):
        calculator.calculate()


def export_pdf_wind_loads() -> None:
    """Calculate wind loads and export the resulting PDF report."""
    workbook = xw.Book.caller()
    active_sheet = workbook.sheets.active
    calculator = _build_wind_calculator(active_sheet)
    with LoadingWindow("Exporting PDF Report..."):
        calculator.calculate()
        calculator.generate_pdf_report()


def _build_wind_calculator(active_sheet):
    """Create the wind calculator with the workbook's configured cell map."""
    return WindLoadCalculatorDirectionalASCE7(
        building_class=active_sheet.range("C2").value,
        basic_wind_speed=_numeric_wind_input(active_sheet, "C3"),
        enclosure_class=active_sheet.range("C4").value,
        exposure_category=active_sheet.range("C5").value,
        wind_dir_factor=_numeric_wind_input(active_sheet, "C6"),
        topographic_factor=_numeric_wind_input(active_sheet, "C7"),
        ground_elevation_factor=_numeric_wind_input(active_sheet, "C8"),
        gust_effect_factor=_numeric_wind_input(active_sheet, "C9"),
        l_input=_numeric_wind_input(active_sheet, "C13"),
        b_input=_numeric_wind_input(active_sheet, "C14"),
        ridge_direction_input=active_sheet.range("C15").value,
        raw_heights=active_sheet.range("C16").value,
        eave_height=_numeric_wind_input(active_sheet, "C18"),
        apex_height=_numeric_wind_input(active_sheet, "C19"),
    )


def _numeric_wind_input(active_sheet, cell_address: str) -> float:
    """Read and validate one numeric wind-design input from Excel."""
    value = active_sheet.range(cell_address).value
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Wind-design input {cell_address} must be numeric; received {value!r}."
        ) from error


def calculate_composite_capacity() -> None:
    """Run the composite-column capacity callback."""
    _composite.calculate_capacity()


def export_composite_calcs() -> None:
    """Export the composite-column calculations from the active workbook."""
    _composite.export_calcs()


__all__ = [
    "calculate_composite_capacity",
    "calculate_wind_loads",
    "export_cad_drawings",
    "export_column_cad_drawings",
    "export_composite_calcs",
    "export_pdf_wind_loads",
    "extract_beam_design_data",
    "extract_forces_properties_from_etabs",
    "generate_dxf_beam_schedule",
    "run_beam_design_from_excel",
    "run_column_design_from_excel",
]
