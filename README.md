# XLWings Structural Design Toolkit

Structural design calculations. The ACI concrete beam and column design runs from an Excel workbook through `xlwings`: each button runs a Python design module that reads the inputs from the sheet and writes the results back. The composite column, steel section and wind checks, and the ETABS workflows, run from the terminal with input dialogs. Reports can be exported as PDF and reinforcement schedules as DXF.

## What is in it

| Run from | Design module (`design/`) | What it does |
|---|---|---|
| `spreadsheets/beam_column_designer_aci318.xlsm` | `beam_designer_aci318.py` | ACI 318M-14 beam flexure, shear, torsion and SMRF checks from ETABS forces; girder and beam schedule DXF; PDF calculation report |
| `spreadsheets/beam_column_designer_aci318.xlsm` | `column_designer_aci318.py` | ACI 318M-14 column P-M, shear, confinement, strong-column and joint-shear checks; column schedule DXF; PDF calculation report |
| `python main.py composite` | `composite_column_designer_aiscDG06.py` | AISC Design Guide 6 rectangular filled composite column; printout and/or PDF report |
| `python main.py steel` | `general_steel_section_designer_aisc360.py` | AISC 360-22 wide-flange capacity checks; printout and/or PDF report |
| `python main.py wind` | `wind_calculator_directional_asce7.py` | ASCE 7 directional procedure (MWFRS) wind pressures; printout and/or PDF report |

Supporting code:

- `design/aci318_config.py`: every ACI 318M-14 constant used by the beam and column designers, with its clause.
- `etabs_api/core/`: the ETABS connection and thin wrappers for tables, geometry, assignments, loads, properties, results and stories.
- `etabs_api/workflows/`: automation built on the office conventions, listed below.
- `etabs_api/workflows/exporter.py`: ETABS tables to the beam and column workbook.
- `etabs_api/workflows/frame_tagger.py`: automatic unique names for beams and columns (`AUTO TAG FRAMES` button).
- `etabs_api/workflows/grid_column_model.py`: stories, grids, columns and walls of an ETABS model from a DXF of framing plans.
- `etabs_api/workflows/model_setup.py`, `ubc97.py`, `load_combinations.py`: definition of materials, sections, loads and NSCP 2015 combinations in an ETABS model.
- `utilities/design_cli.py`: the terminal workflow shared by the composite, steel and wind checks (input dialog, then printout, PDF or both).
- `utilities/_gui_helpers.py`: file pickers, list pickers, choice and text-entry dialogs, and the loading window.
- `utilities/_calc_report.py`: layout of the beam and column PDF calculation reports.
- `geotech/logspiral_passive.py`: standalone log-spiral passive earth pressure calculator (run in a terminal).
- `main.qmd`: Quarto template for written reports.
- `main.py`: terminal entry point for the ETABS workflows (`setup`, `grids`, `tag`) and the design checks (`composite`, `steel`, `wind`). No workbook calls it.

## Requirements

- Windows; Microsoft Excel desktop and the xlwings add-in for the concrete beam and column workbook
- Python 3.10 or newer (developed on 3.14)
- ETABS, for the beam and column workbook and the model setup (developed on ETABS 22)
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

## Guides

Each guide in `doc/` covers one tool: how to run it, its inputs, what it writes, and the limits of the calculation.

- [Concrete beam and column workflow](doc/concrete_beam_column_excel_guide.md)
- [Steel and composite modules](doc/steel_design_documentation.md)
- [Wind load calculator](doc/wind_calculator_asce7_documentation.md)

## Running from the terminal

From the project folder, with the environment active:

```powershell
.venv\Scripts\Activate.ps1
python main.py setup     # materials, sections, loads, spectrum, cases and combinations
python main.py grids     # stories, grids, columns and walls from a DXF of framing plans
python main.py tag       # unique names for every beam and column, in a tagged copy
python main.py composite # rectangular filled composite column, AISC DG6
python main.py steel     # wide-flange steel member, AISC 360-22
python main.py wind      # MWFRS wind pressures, ASCE 7 directional procedure
python main.py --help    # list the commands
```

Each command asks for its inputs in dialogs. The design checks then ask whether to print the results in the terminal, export a PDF calculation report, or both.

### Shorter: the `xs` command

Install the project once into the environment (editable, so code changes apply without reinstalling):

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Then, from any folder while the environment is active, `python main.py` becomes `xs`:

```powershell
xs wind
xs setup
xs --help
```

To use `xs` without activating the environment, add this to your PowerShell profile (`notepad $PROFILE`), with the path to your copy of the project:

```powershell
function xs { & "C:\path\to\xlwings_spreadsheet_structural\.venv\Scripts\xs.exe" @args }
```

## Setting up an ETABS model


This defines the materials, frame sections, load patterns, UBC 97 response spectrum, load cases and NSCP 2015 load combinations of an ETABS model from a few dialogs. See [ETABS model setup](doc/etabs_model_setup.md).

## Building grids, columns and walls from a DXF

`python main.py grids`. It reads framing plans drawn in one DXF file and creates the stories, grid lines, columns and walls in ETABS; run again on a revised drawing, it updates the model. See [Grids, columns and walls from a DXF](doc/etabs_grid_column_model.md).

## Tests

```powershell
python -m pip install -r .github/requirements-ci.txt
python -m pytest tests
```

The tests need neither Excel nor ETABS. GitHub Actions runs them on every push to `main` (`.github/workflows/ci.yml`). Pushing a tag such as `v1.0.0` builds a release package (`.github/workflows/release.yml`).

## Engineering use

These tools automate calculations; they do not replace engineering judgement. Check the results independently before using them for design, and read the limitations section of each guide.
