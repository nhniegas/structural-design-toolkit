# XLWings Structural Design Toolkit

This repository is a Python-based structural engineering toolkit built around Excel workbooks and `xlwings`. It has evolved beyond a simple steel-beam helper into a multi-module design platform for spreadsheet-driven calculation workflows.

## Project scope

The active codebase contains separate engineering modules for:

- ASCE 7 directional wind-load calculations in `design/wind_calculator_directional_asce7.py`
- AISC Design Guide 6 rectangular filled composite-column design in `design/composite_column_designer_aiscDG06.py`
- ACI 318 reinforced-concrete beam design and ETABS extraction in `design/beam_designer_aci318.py`
- ACI 318 reinforced-concrete column design, detailing, and SMRF checks in `design/column_designer_aci318.py`
- AISC 360 steel-member capacity checks in `design/general_steel_section_designer_aisc360.py`

The workbook-facing entry point is `main.py`, which acts as a compatibility facade. It exposes workbook callback functions that delegate to the active engineering modules rather than duplicating design logic in one script.

## Current architecture

- `main.py` keeps the historical Excel callback names available and routes work into the design modules.
- `design/` contains the actual engineering logic, input parsing, result tables, and export functions.
- `etabs_api/` provides ETABS connection and model-access utilities used by the concrete design workflows.
- `utilities/` contains shared GUI and workbook helper functions.
- `spreadsheets/`, `pdf/`, and `doc/` hold workbook assets, generated reports, and project documentation.

## Typical workflow

1. The Excel workbook calls a Python callback through `xlwings`.
2. `main.py` or a module-level callback loads the active sheet and reads design inputs.
3. The relevant design module performs calculations and writes results back to Excel.
4. Optional outputs include PDF reports, DXF schedules, and structured result tables.

## Installation

Requirements are listed in `requirements.txt` and include spreadsheet, structural-analysis, and reporting libraries such as `xlwings`, `pandas`, `numpy`, `ezdxf`, `concreteproperties`, `sectionproperties`, `steelpy`, `matplotlib`, and `PyLaTeX`.

```powershell
python -m pip install -r requirements.txt
```

## Recommended setup

- Windows with Microsoft Excel Desktop
- Python 3.9+
- Git for repository management
- LaTeX distribution for PDF generation when using report exports

## Documentation

The documentation under `doc/` documents the current project modules and workflow, including:

- ASCE 7 wind-load design
- steel and composite member design
- concrete beam and column workbook integration

## Notes

This repository is intended to be a structural design workbook ecosystem, not a single-purpose beam calculator. The documentation and workbook entry points are organized around that broader current architecture.
