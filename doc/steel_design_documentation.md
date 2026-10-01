# Steel and Composite Design Modules

This document summarizes the live steel-related modules currently present in the repository and clarifies that the project is broader than a single concrete or beam design workflow.

## Modules in scope

The current codebase includes:

- `design/general_steel_section_designer_aisc360.py` — AISC 360 wide-flange / I-shape capacity checks
- `design/composite_column_designer_aiscDG06.py` — AISC Design Guide 6 rectangular filled composite column design
- `design/wind_calculator_directional_asce7.py` — ASCE 7 directional wind load calculations

## Project role

These modules are part of a structural engineering toolkit that is used from Excel via `xlwings`. The workbook facade in `main.py` re-exports the public callback functions and keeps the engineering logic separated in dedicated design files.

## Steel module focus

The general steel section designer covers AISC 360 checks for doubly symmetric I-sections, including axial, flexural, shear, and combined-force checks. The code supports rolled shapes from the steelpy database and custom built-up sections defined from plate geometry.

## Composite column module focus

The rectangular filled composite column module implements DG6-based checks for composite box members, including compactness checks, axial compression, flexural strength, shear checks, and interaction ratios. It is designed as a reusable engineering module rather than a workbook-only script.

## Workbook integration

The public workbook callbacks connect the Excel layer to these modules without embedding the engineering logic in `main.py`. This keeps the design engines maintainable and allows them to be used both directly in Python and through the workbook interface.

## Documentation status

The repository documentation should describe the project as a multi-module structural design toolbox rather than a single-purpose steel-beam tool. The modules above are the current implementation basis for that project narrative.
