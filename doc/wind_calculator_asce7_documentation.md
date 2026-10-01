# ASCE 7 Directional Wind Load Calculator

This document describes the current wind-load calculator in `design/wind_calculator_directional_asce7.py` and its place within the broader workbook-based structural design project.

## Scope

The module computes MWFRS wind pressures for a gable-roofed building using the ASCE 7 directional procedure. It uses hardcoded reference tables that were previously embedded in the Excel workbook, and it can be called from the workbook through `main.py` as part of the active spreadsheet workflow.

## Repository context

This is not a standalone one-off script; it is integrated with the same engineering toolkit used for the composite-column and concrete-design modules. The public workbook callback layer in `main.py` exposes the wind API to Excel while keeping the engineering implementation in the `design/` package.

## Current implementation notes

- The calculator reads project inputs as constructor arguments or Excel values passed by the workbook callback.
- It computes velocity pressure, wall coefficients, roof coefficients, and net design pressures.
- It can generate a PDF report using `PyLaTeX` when the environment has a working LaTeX installation.
- It is self-contained: the ASCE 7 coefficient tables are stored in the module instead of being read live from worksheet cells.

## Typical use

- Workbook callback: `calculate_wind_loads()` and `export_pdf_wind_loads()` in `main.py`
- Direct Python use: instantiate `WindLoadCalculatorDirectionalASCE7` and call `calculate()`

## Dependencies

The module depends on:

- `numpy`
- `pandas`
- `pylatex`
- `xlwings` when used through the workbook callback layer

## Limitations

This module covers the MWFRS directional procedure and the hardcoded reference tables used by the project. It does not replace a complete project-specific code review or the full design package for all building elements.
