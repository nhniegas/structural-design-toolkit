# XLWings Structural Design Toolkit

Structural design calculations driven from Excel workbooks. Each workbook button runs a Python design module through `xlwings`; the module reads the inputs from the sheet, does the calculation, and writes the results back. Reports can be exported as PDF and reinforcement schedules as DXF.

## What is in it

| Workbook (`spreadsheets/`) | Design module (`design/`) | What it does |
|---|---|---|
| `beam_column_designer_aci318.xlsm` | `beam_designer_aci318.py` | ACI 318M-14 beam flexure, shear, torsion and SMRF checks from ETABS forces; beam schedule DXF |
| `beam_column_designer_aci318.xlsm` | `column_designer_aci318.py` | ACI 318M-14 column P-M, shear, confinement, strong-column and joint-shear checks; column schedule DXF |
| `composite_column_designer_aiscDG06.xlsm` | `composite_column_designer_aiscDG06.py` | AISC Design Guide 6 rectangular filled composite column; PDF report |
| `wind_load_calculator_asce7.xlsm` | `wind_calculator_directional_asce7.py` | ASCE 7 directional procedure (MWFRS) wind pressures; PDF report |
| none yet | `general_steel_section_designer_aisc360.py` | AISC 360-22 wide-flange capacity checks; PDF report |

Supporting code:

- `design/aci318_config.py`: every ACI 318M-14 constant used by the beam and column designers, with its clause.
- `etabs_api/`: ETABS connection, table extraction and Excel export.
- `utilities/_gui_helpers.py`: file pickers, list pickers and the loading window.
- `geotech/logspiral_passive.py`: standalone log-spiral passive earth pressure calculator (run in a terminal).
- `main.qmd`: Quarto template for written reports.
- `main.py`: a marker file only. No workbook calls it.

## Requirements

- Windows with Microsoft Excel desktop and the xlwings add-in
- Python 3.10 or newer (developed on 3.14)
- ETABS, for the beam and column workbook (developed on ETABS 22)
- A LaTeX distribution such as MiKTeX, for PDF reports
- Quarto, only to render `main.qmd`

## Setup

```powershell
git clone https://github.com/nhniegas/xlwings_spreadsheet_structural.git
cd xlwings_spreadsheet_structural
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
xlwings addin install
```

In Excel, open the xlwings ribbon and set **Interpreter** to `.venv\Scripts\python.exe` in this folder.

Keep the folder layout as it is. The macros find the Python code relative to the workbook (`spreadsheets\..`), so the project folder can be moved or renamed, but the workbooks must stay in `spreadsheets/` next to `design/`.

If ETABS is not installed at `C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe`, set the `ETABS_PROGRAM_PATH` environment variable to your `ETABS.exe`. It is only used when no ETABS session is already open.

## Using the workbooks

Each guide in `doc/` covers one workbook: buttons, input cells, what is written where, and the limits of the calculation.

- [Concrete beam and column workflow](doc/concrete_beam_column_excel_guide.md)
- [Steel and composite modules](doc/steel_design_documentation.md)
- [Wind load calculator](doc/wind_calculator_asce7_documentation.md)

## Tests

```powershell
python -m pip install -r .github/requirements-ci.txt
python -m pytest tests
```

The tests need neither Excel nor ETABS. GitHub Actions runs them on every push to `main` (`.github/workflows/ci.yml`). Pushing a tag such as `v1.0.0` builds a release package (`.github/workflows/release.yml`).

## Engineering use

These tools automate calculations; they do not replace engineering judgement. Check the results independently before using them for design, and read the limitations section of each guide.
