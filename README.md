# Structural Design Toolkit

Structural design calculations and ETABS automation, run from the terminal with input dialogs. No spreadsheet is needed: results are saved as Excel files, calculation reports as PDF and reinforcement schedules as DXF.

## What is in it

| Run from | Design module (`design/`) | What it does |
|---|---|---|
| `sdt beams` | `beam_designer_aci318.py` | ACI 318M-14 beam flexure, shear, torsion and SMRF checks from ETABS forces; girder and beam schedule DXF; PDF calculation report |
| `sdt columns` | `column_designer_aci318.py` | ACI 318M-14 column P-M, shear, confinement, strong-column and joint-shear checks; column schedule DXF; PDF calculation report |
| `python main.py composite` | `composite_column_designer_aiscDG06.py` | AISC Design Guide 6 rectangular filled composite column; printout and/or PDF report |
| `python main.py steel` | `general_steel_section_designer_aisc360.py` | AISC 360-22 wide-flange capacity checks; printout and/or PDF report |
| `python main.py wind` | `wind_calculator_directional_asce7.py` | ASCE 7 directional procedure (MWFRS) wind pressures; printout and/or PDF report |

Supporting code:

- `design/code_config.py`: every structural code value, each with its clause. `CODE` holds ACI 318M-14 (NSCP chapter 4), used by the beam and column designers. `NSCP` holds NSCP 2015 chapter 2: load factors, live load reduction (with the ASCE 7 alternate), and the earthquake values with the UBC 97 tables that ETABS uses. NSCP 2015 governs; the other codes are guides.
- `etabs_api/core/`: the ETABS connection and thin wrappers for tables, geometry, assignments, loads, properties, results and stories.
- `etabs_api/workflows/`: automation built on the office conventions, listed below.
- `design/concrete_workflow.py`: the `sdt beams`, `sdt deflection` and `sdt columns` commands and the design data stored between them.
- `etabs_api/workflows/exporter.py`: the ETABS tables the beam and column design need.
- `etabs_api/workflows/frame_tagger.py`: automatic unique names for beams and columns (`sdt tag`).
- `etabs_api/workflows/grid_column_model.py`: stories, grids, columns and walls of an ETABS model from a DXF of framing plans.
- `etabs_api/workflows/analysis_forces.py`: factored forces from the analysis results (combinations, spectrum and wind permutations, NSCP live load reduction, pattern live load).
- `etabs_api/workflows/design_loop.py`, `sections.py`: the analysis and design loop with member resizing.
- `etabs_api/workflows/tributary.py`: geometric tributary areas for the live load reduction.
- `design/beam_deflection.py`: beam deflection checks (ACI 318M-14 24.2).
- `design/column_interaction.py`: the biaxial P-Mx-My design interaction surface of a column layout, built once and cached. It also provides the demand convex hull and the 3D figure for the calculation report.
- `etabs_api/workflows/model_analysis.py`: analysis run, response spectrum scaling, period, modal mass and weight checks.
- `etabs_api/workflows/model_setup.py`, `ubc97.py`, `load_combinations.py`: definition of materials, sections, loads and NSCP 2015 combinations in an ETABS model.
- `utilities/design_cli.py`: the terminal workflow shared by the composite, steel and wind checks (input dialog, then printout, PDF or both).
- `utilities/_gui_helpers.py`: file pickers, list pickers, choice and text-entry dialogs, and the loading window.
- `utilities/_calc_report.py`: layout of the beam and column PDF calculation reports.
- `main.qmd`: Quarto template for written reports.
- `main.py`: terminal entry point of every command (`sdt --help` lists them).

## Requirements

- Windows (ETABS and its COM API)
- Python 3.10 or newer (developed on 3.14)
- ETABS, for the ETABS workflows and the beam and column design (developed on ETABS 22)
- A LaTeX distribution such as MiKTeX, for PDF reports
- Quarto, only to render `main.qmd`

## Setup

```powershell
git clone https://github.com/nhniegas/structural-design-toolkit.git
cd structural-design-toolkit
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
```

The last line installs the `sdt` command (Structural Design Toolkit; see below).

If ETABS is not installed at `C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe`, set the `ETABS_PROGRAM_PATH` environment variable to your `ETABS.exe`. It is only used when no ETABS session is already open.

## Guides

Each guide in `docs/` covers one tool: how to run it, its inputs, what it writes, and the limits of the calculation.

- [Concrete beam and column workflow](docs/concrete_beam_column_design.md)
- [Steel and composite modules](docs/steel_design_documentation.md)
- [Wind load calculator](docs/wind_calculator_asce7_documentation.md)

## Running from the terminal

From the project folder, with the environment active:

```powershell
.venv\Scripts\Activate.ps1
python main.py setup     # materials, sections, loads, spectrum, cases and combinations
python main.py grids     # stories, grids, columns and walls from a DXF of framing plans
python main.py tag       # unique names for every beam and column (a tagged copy or the model itself)
python main.py analyze   # run, scale the response spectrum, check periods, modal mass and weight
python main.py beams     # extract the forces and design the beams; results, calcs and schedules
python main.py deflection  # only the beam deflections (bars of the last beam design)
python main.py columns   # design the columns from the stored beam step
python main.py design    # analysis and beam/column design loop that resizes members until they pass
python main.py composite # rectangular filled composite column, AISC DG6
python main.py steel     # wide-flange steel member, AISC 360-22
python main.py wind      # MWFRS wind pressures, ASCE 7 directional procedure
python main.py --help    # list the commands
```

Each command asks for its inputs in dialogs. The design checks then ask whether to print the results in the terminal, export a PDF calculation report, or both.

### Shorter: the `sdt` command

Install the project once into the environment (editable, so code changes apply without reinstalling):

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Then, from any folder while the environment is active, `python main.py` becomes `sdt`:

```powershell
sdt wind
sdt setup
sdt --help
```

To use `sdt` without activating the environment, add this to your PowerShell profile (`notepad $PROFILE`), with the path to your copy of the project:

```powershell
function sdt { & "C:\path\to\structural-design-toolkit\.venv\Scripts\sdt.exe" @args }
```

`xs`, the command's earlier name, still works as an alias. Answers the commands remember are kept in `~/.structural_design_toolkit` (those in the old `~/.xlwings_structural` are copied over on first use).

## Setting up an ETABS model

This defines the materials, frame sections, load patterns, UBC 97 response spectrum, load cases and NSCP 2015 load combinations of an ETABS model from a few dialogs. See [ETABS model setup](docs/etabs_model_setup.md).

## Building grids, columns and walls from a DXF

`python main.py grids`. It reads framing plans drawn in one DXF file and creates the stories, grid lines, columns and walls in ETABS; run again on a revised drawing, it updates the model. See [Grids, columns and walls from a DXF](docs/etabs_grid_column_model.md).

## Running and checking the analysis

`sdt analyze` (or `python main.py analyze`) runs the analysis, scales the response spectrum cases to 100 % of the static base shear, and checks the periods against UBC 97 Method A, the modal participating mass and the seismic weight. See [ETABS analysis checks](docs/etabs_analysis.md).

`sdt check` (or `python main.py check`) checks the open model against NSCP 2015 without changing it, and prints one line per check in the terminal. It covers missing loads, supports and diaphragms; the seismic inputs against the Section 208 tables (Z, Na, Nv, Ca, Cv, I, R, Ct); the story ranges and the Ev term in the combinations; P-delta, the mass source and the cracked-section modifiers; and the Section 418 member limits. After `sdt analyze` it also recomputes the base shear coefficient and period cap, and checks the spectrum scaling, modal mass and the drift ΔM = 0.7RΔS. See [Model check](docs/etabs_model_check.md).

`sdt drift` (or `python main.py drift`) checks the story drift of the `DRIFT` and `WDRIFT` combinations using the drift stiffness: strength level (0.35 / 0.70) and service level (1.4 times). It works on the open model: it sets the modifiers, analyses, reports and restores the model. The drift is read at the diaphragm centre of mass or at the four outer column joints of each story. See [Story drift](docs/etabs_drift.md).

`sdt design` runs the analysis and the beam and column design again and again on a copy of the model, resizing the members until they pass (deflection included). See [Design loop](docs/etabs_design_loop.md).

## Beam and column design

`sdt beams`, then `sdt columns` (and `sdt deflection` for the deflections alone). The design forces come from the analysis results, not from ETABS concrete design. Each command saves its results (`.xlsx`), calculation report (`.pdf`) and schedules (`.dxf`) in the folder you choose. See [Concrete beam and column design](docs/concrete_beam_column_design.md).

## Tests

```powershell
python -m pip install -r .github/requirements-ci.txt
python -m pytest tests
```

The tests need no ETABS. GitHub Actions runs them on every push to `main` (`.github/workflows/ci.yml`). Pushing a tag such as `v1.0.0` builds a release package (`.github/workflows/release.yml`).

## Geotechnical tools

The log-spiral passive earth pressure calculator moved to its own repository, [geotech-toolkit](https://github.com/nhniegas/geotech-toolkit).

## Engineering use

These tools automate calculations; they do not replace engineering judgement. Check the results independently before using them for design, and read the limitations section of each guide.
