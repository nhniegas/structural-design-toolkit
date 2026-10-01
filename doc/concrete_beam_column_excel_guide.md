# Concrete Beam and Column Excel Workflow

This guide describes the concrete design workflows that are currently implemented in the repository and their relationship to the Excel-based workbook architecture.

## Current project architecture

The project is organized as a set of engineering modules under `design/` with a compatibility layer in `main.py`.

- `main.py` exposes the workbook-facing API used by Excel callbacks.
- `design/beam_designer_aci318.py` contains the reinforced-concrete beam design workflow.
- `design/column_designer_aci318.py` contains the reinforced-concrete column design workflow and related checks.
- `design/composite_column_designer_aiscDG06.py` and `design/wind_calculator_directional_asce7.py` are separate modules in the same toolkit.

This means the project is structured as a collection of reusable design engines rather than as a single monolithic workbook script.

## Beam design workflow

The beam module includes:

- ETABS data extraction and member selection
- beam property and force-table processing
- flexural, shear, and torsion checks
- reinforcement detailing and output tables
- DXF schedule export

The workbook callbacks for beam work are exposed through the compatibility layer in `main.py`, while the implementation remains in the design module.

## Column design workflow

The column module includes:

- section data and bar-layout checks
- P-M interaction evaluation
- transverse reinforcement checks
- shear checks and SMRF-related joint review notes
- Excel output tables

The same pattern applies here: the workbook invokes the public API, but the actual engineering logic resides in the dedicated module under `design/`.

## Excel and `xlwings`

`xlwings` remains the workbook interface used to read inputs, write result tables, and trigger design actions. The public functions remain available through the workbook entry point, but the design modules themselves are the authoritative implementation layer.

## Reporting and export paths

The project supports optional PDF and CAD/DXF outputs for the design workflows, depending on the module being used. These export functions are part of the current engineering package and should be documented as such instead of treating the project as a simple beam-only workbook.

## Important note for documentation

Any documentation should reflect the current state of the codebase: a multi-module structural engineering toolkit with spreadsheet automation, ETABS interactions, wind design, composite column checks, and concrete member design, all coordinated by the Excel callback facade in `main.py`.
