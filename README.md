# Structural Design Toolkit

Structural design calculations and ETABS automation, run from the terminal with input dialogs. No spreadsheet is needed: results are saved as Excel files, calculation reports as PDF and reinforcement schedules as DXF.

## What is in it

| Run from | Design module (`design/`) | What it does |
|---|---|---|
| `sdt beams` | `concrete/beams/` | ACI 318M-14 beam flexure, shear, torsion, deflection and SMRF checks from ETABS forces; girder and beam schedule DXF; PDF calculation report |
| `sdt columns` | `concrete/columns/` | ACI 318M-14 column P-M with slenderness, shear, confinement, strong-column and joint-shear checks; column schedule DXF; PDF calculation report |
| `sdt composite` | `steel/composite_column_aiscDG06.py` | AISC Design Guide 6 rectangular filled composite column; printout and/or PDF report |
| `sdt steel` | `steel/wide_flange_aisc360.py` | AISC 360-22 wide-flange capacity checks; printout and/or PDF report |
| `sdt wind` | `loads/wind_directional_asce7.py` | ASCE 7 directional procedure (MWFRS) wind pressures; printout and/or PDF report |

Where the code is:

```
design/
  criteria/     the code values (code_config.py) and the target ratios (dcr_targets.py)
  concrete/     workflow.py: the sdt beams, sdt deflection and sdt columns commands
    beams/      design, sections, supports, deflection, carriers, results, schedule, calculations, options
    columns/    design, sizing, section, layouts, flexure, interaction, slenderness, transverse,
                shear, joints, tables, report, schedule, calculations, options
  steel/        wide-flange members (AISC 360-22) and filled composite columns (AISC DG6)
  loads/        the wind calculator (ASCE 7)
  parallel.py   the processes that share the beam design of a large model
etabs_api/      core: the ETABS connection; workflows: the ETABS commands
utilities/      dialogs, answers file, summaries, report layout, doctor
tools/          the ETABS test runner and the icon
```

Supporting code:

- `design/criteria/code_config.py`: every structural code value, each with its clause. `CODE` holds ACI 318M-14 (NSCP chapter 4), used by the beam and column designers. `NSCP` holds NSCP 2015 chapter 2: load factors, live load reduction (with the ASCE 7 alternate), and the earthquake values with the UBC 97 tables that ETABS uses. NSCP 2015 governs; the other codes are guides.
- `etabs_api/core/`: the ETABS connection and thin wrappers for tables, geometry, assignments, loads, properties, results and stories.
- `etabs_api/workflows/`: automation built on the office conventions, listed below.
- `design/concrete/workflow.py`: the `sdt beams`, `sdt deflection` and `sdt columns` commands and the design data stored between them.
- `etabs_api/workflows/exporter.py`: the ETABS tables the beam and column design need.
- `etabs_api/workflows/frame_tagger.py`: automatic unique names for beams and columns (`sdt tag`).
- `etabs_api/workflows/grid_column_model.py`: stories, grids, columns and walls of an ETABS model from a DXF of framing plans.
- `etabs_api/workflows/analysis_forces.py`: factored forces from the analysis results (combinations, spectrum and wind permutations, NSCP live load reduction, pattern live load).
- `etabs_api/workflows/design_loop.py`, `sections.py`: the analysis and design loop with member resizing.
- `etabs_api/workflows/tributary.py`: geometric tributary areas for the live load reduction.
- `etabs_api/workflows/framing_plans.py`: framing plans of the model as a DXF (`sdt plans`).
- `etabs_api/workflows/foundation_model.py`: a second model for the foundation, from the support reactions (`sdt foundation`).
- `etabs_api/workflows/foundation_plan.py`: the footings, tie beams and columns of the foundation model, with their marks, saved for the framing plans.
- `etabs_api/workflows/model_3d.py`: the concrete of the model as 3D solids in a STEP file (`sdt 3d`).
- `design/concrete/beams/`: the beam design in files by job (design, sections, supports, results, schedule, calculations, options).
- `design/concrete/columns/`: the column design in files by job (design, sizing, section, layouts, flexure, transverse, shear, joints, tables, report, schedule, calculations, options).
- `design/concrete/beams/deflection.py`: beam deflection checks (ACI 318M-14 24.2).
- `design/criteria/dcr_targets.py`: target demand / capacity ratios by member type and check.
- `design/concrete/beams/carriers.py`: which beam carries which, and the rule that a carrier is at least as deep.
- `design/concrete/columns/slenderness.py`: slenderness of columns along their length (ACI 6.2.5, 6.6.4.5): unbraced lengths and k from the frame, the moment magnifier and the minimum moment. The sway effects come from the ETABS P-delta analysis.
- `design/concrete/columns/interaction.py`: the biaxial P-Mx-My design interaction surface of a column layout, built once and cached. It also provides the demand convex hull and the 3D figure for the calculation report.
- `etabs_api/workflows/model_analysis.py`: analysis run, response spectrum scaling, period, modal mass and weight checks.
- `etabs_api/workflows/model_setup.py`, `ubc97.py`, `load_combinations.py`: definition of materials, sections, loads and NSCP 2015 combinations in an ETABS model.
- `utilities/design_cli.py`: the terminal workflow shared by the composite, steel and wind checks (input dialog, then printout, PDF or both).
- `utilities/_gui_helpers.py`: file pickers, list pickers, choice and text-entry dialogs, and the loading window.
- `utilities/_calc_report.py`: layout of the beam and column PDF calculation reports.
- `utilities/terminal.py`: tables, colour and equations in the terminal; `design/concrete/results_tables.py`: the beam and column tables.
- `utilities/run_summary.py`: the summary every command shows in a window when it finishes (the terminal keeps the detailed results).
- `utilities/doctor.py`: `sdt doctor`, the check that a machine can run the toolkit.
- `utilities/answers.py`: the answers file, which runs a command with no dialog (`--answers`, `--record`).
- `tools/etabs_run.py`: test runs of the commands in an ETABS of their own, compared with a reference.
- `assets/sdt.ico`: the icon of `sdt.exe`; `tools/make_icon.py` draws it (run it again after a change of the design).
- `design/parallel.py`: the processes that share the beam design of a large model between the cores (`SDT_WORKERS=1` keeps one).
- `build_exe.ps1`: builds the packaged program `sdt.exe`.
- `utilities/_xlsx_values.py`: rounded numbers and stated blanks of the result workbooks.
- `main.qmd`: Quarto template for written reports.
- `main.py`: terminal entry point of every command (`sdt --help` lists them).

## Download (no Python needed)

1. Open the [Releases](https://github.com/nhniegas/structural-design-toolkit/releases) page and download `sdt-<version>-windows.zip` of the latest release.
2. Unzip it anywhere and open the `sdt` folder. Keep the files of the folder together.
3. Double-click `sdt.exe`. A terminal opens with the menu of commands: type a number or a name, and the menu comes back when the command ends.
4. Run `doctor` first. It checks that ETABS, the dialogs, LaTeX and the 3D kernel are in place on your machine, and that the design processes start.

Windows may warn that the program is from an unknown publisher (it is not signed): choose **More info**, then **Run anyway**. ETABS must be installed for the ETABS commands.

LaTeX is not part of the download. The PDF calculation reports need it; without it the results and schedules are still written, and the command says how to get it. Install MiKTeX once, from a terminal:

```powershell
winget install MiKTeX.MiKTeX
```

Then open a new terminal. On the first report MiKTeX asks to install the packages it lacks: allow it.

From a terminal the program takes the same commands as below, for example `sdt.exe beams`.

## Requirements

For running from the source code:

- Windows (ETABS and its COM API)
- Python 3.10 or newer (developed on 3.14)
- ETABS, for the ETABS workflows and the beam and column design (developed on ETABS 22)
- A LaTeX distribution such as MiKTeX, for PDF reports (`winget install MiKTeX.MiKTeX`)
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
- [Working on a model the toolkit did not set up](docs/existing_models.md)
- [Framing plans as DXF](docs/etabs_framing_plans.md)
- [Foundation model](docs/etabs_foundation_model.md)
- [3D model as STEP](docs/etabs_3d_model.md)
- [Steel and composite modules](docs/steel_design_documentation.md)
- [Wind load calculator](docs/wind_calculator_asce7_documentation.md)
- [Results in the terminal](docs/terminal_output.md)
- [Running a command from an answers file](docs/answers_file.md)
- [ETABS test runs](docs/etabs_test_runs.md)

## Running from the terminal

Every tool is a command of `sdt`, the ETABS workflows and the standalone checks alike. With the environment active, from any folder:

```powershell
.venv\Scripts\Activate.ps1
sdt setup       # materials, sections, loads, spectrum, cases and combinations
sdt grids       # stories, grids, columns and walls from a DXF of framing plans
sdt tag         # unique names for every beam and column (a tagged copy or the model itself)
sdt check       # check the open model against NSCP 2015 (read only)
sdt analyze     # run, scale the response spectrum, check periods, modal mass and weight
sdt drift       # story drift with the drift stiffness, then restore the model
sdt beams       # extract the forces and design the beams; results, calcs and schedules
sdt deflection  # only the beam deflections (bars of the last beam design)
sdt columns     # design the columns from the stored beam step
sdt design      # analysis and beam/column design loop that resizes members until they pass
sdt foundation  # a second model for the foundation, from the support reactions; or update its forces
sdt plans       # framing plans of every floor as one DXF: beams, columns, marks, grids; the foundation plan too
sdt 3d          # the concrete of the model as 3D solids in a STEP file, for AutoCAD; the foundation too
sdt composite   # rectangular filled composite column, AISC DG6 (no ETABS)
sdt steel       # wide-flange steel member, AISC 360-22 (no ETABS)
sdt wind        # MWFRS wind pressures, ASCE 7 directional procedure (no ETABS)
sdt doctor      # check that this machine can run the toolkit: ETABS, dialogs, LaTeX
sdt             # the menu of commands (also what the packaged sdt.exe opens with)
sdt --help      # list the commands
sdt --version   # the version

sdt beams --record beams.json    # any command: save what you answer in its dialogs
sdt beams --answers beams.json   # any command: run it with no dialog, from that file
```

Each command asks for its inputs in dialogs. The standalone checks (`composite`, `steel`, `wind`) then ask whether to print the results in the terminal, export a PDF calculation report, or both. The terminal shows the detailed results of a command as tables, with the equations of the checks and each status in colour (see [Results in the terminal](docs/terminal_output.md)). When it finishes, a separate window shows its summary: what was run, the counts, what needs attention and the files written.

The commands also run on a model that was not made with `sdt setup`: one with its own combination names, section names and untagged members. A command reads what the model holds and asks for what is missing (which combinations to design for, the deflection and drift combinations, what to do with untagged members), saves the answers beside the model, and says in its summary what was read, answered or assumed. See [Working on a model the toolkit did not set up](docs/existing_models.md).

A command can also run with no dialog. `--record` saves what you answer; `--answers` runs the command again from that file, printing each question with the answer it got. A dialog with no answer stops the command unless the file says to take what the dialog offers. See [Running a command from an answers file](docs/answers_file.md).

`sdt` is installed by `python -m pip install -e .` in [Setup](#setup) (editable, so code changes apply without reinstalling). Without that step, `python main.py <command>` from the project folder runs the same commands:

```powershell
python main.py wind
python main.py --help
```

To use `sdt` without activating the environment, add this to your PowerShell profile (`notepad $PROFILE`), with the path to your copy of the project:

```powershell
function sdt { & "C:\path\to\structural-design-toolkit\.venv\Scripts\sdt.exe" @args }
```

`xs`, the command's earlier name, still works as an alias. Answers the commands remember are kept in `~/.structural_design_toolkit` (`SDT_SETTINGS_DIR` in the environment names another folder) (those in the old `~/.xlwings_structural` are copied over on first use).

## Setting up an ETABS model

This defines the materials, frame sections, load patterns, UBC 97 response spectrum, load cases and NSCP 2015 load combinations of an ETABS model from a few dialogs. See [ETABS model setup](docs/etabs_model_setup.md).

## Building grids, columns and walls from a DXF

`sdt grids`. It reads framing plans drawn in one DXF file and creates the stories, grid lines, columns and walls in ETABS; run again on a revised drawing, it updates the model. See [Grids, columns and walls from a DXF](docs/etabs_grid_column_model.md).

## Running and checking the analysis

`sdt analyze` runs the analysis, scales the response spectrum cases to 100 % of the static base shear, and checks the periods against UBC 97 Method A, the modal participating mass and the seismic weight. See [ETABS analysis checks](docs/etabs_analysis.md).

`sdt check` checks the open model against NSCP 2015 without changing it, and prints one line per check in the terminal. It covers missing loads, supports and diaphragms; the seismic inputs against the Section 208 tables (Z, Na, Nv, Ca, Cv, I, R, Ct); the story ranges and the Ev term in the combinations; P-delta, the mass source and the cracked-section modifiers; and the Section 418 member limits. After `sdt analyze` it also recomputes the base shear coefficient and period cap, and checks the spectrum scaling, modal mass and the drift ΔM = 0.7RΔS. See [Model check](docs/etabs_model_check.md).

`sdt drift` checks the story drift of the `DRIFT` and `WDRIFT` combinations (or, when the model has none under those names, of its drift load cases or of the combinations you pick) using the drift stiffness: strength level (0.35 / 0.70) and service level (1.4 times, or the factor you give). It works on the open model: it sets the modifiers, analyses, reports and restores the model. The drift is read at the diaphragm centre of mass or at the four outer column joints of each story. See [Story drift](docs/etabs_drift.md).

`sdt design` runs the analysis and the beam and column design again and again on a copy of the model, resizing the members until they pass (deflection included). See [Design loop](docs/etabs_design_loop.md).

## Foundation model

`sdt foundation` makes a second ETABS model that holds only the support joints of the superstructure, each loaded with the opposite of its reaction in every load pattern, so the foundation is modelled and designed apart from the superstructure. A wind case with several steps gets one pattern for each step, the combinations are made again, and a table checks that the loads of every pattern add up to the base reaction. Run again after the superstructure changes, it updates the forces and keeps what you modelled. Run with the foundation model open, it saves the footings, tie beams and columns for the framing plans. See [Foundation model](docs/etabs_foundation_model.md).

## Framing plans

`sdt plans` writes the framing plans of the open model as one DXF, the floors side by side at 1:1 in mm. Beams and girders are dashed multilines at their true width, which keep their width when an end is dragged in AutoCAD, and stop at the column faces. Columns are solid at their true size and rotation, walls at their thickness. Every beam has its name; the column marks are on every floor or only where each column starts; the grids, with or without dimensions, are optional. When the plan of the foundation model was saved with `sdt foundation`, the foundation plan can be included: footings with their marks (`F1` for isolated footings of one size, `CF1` for a combined footing), tie beams and columns. See [Framing plans](docs/etabs_framing_plans.md).

## 3D model

`sdt 3d` writes the concrete of the open model as 3D solids in one STEP file, in mm, which AutoCAD imports: columns, beams with their top at the floor and stopped at the column faces, walls and slabs, each named and grouped by story. When the plan of the foundation model was saved with `sdt foundation`, the footings and tie beams can be included. Members of the same size are one shape placed many times, so the file stays small. Concrete only: no steel members and no reinforcing bars. See [3D model](docs/etabs_3d_model.md).

## Beam and column design

`sdt beams`, then `sdt columns` (and `sdt deflection` for the deflections alone). The design forces come from the analysis results, not from ETABS concrete design. You can set a target ratio below the code limit of 1.00 for each check of girders, beams and columns, and require a girder to be at least as deep as the beams it carries. Girders with a clear span under 4d, which do not qualify as beams of a special moment frame, can be designed without the SMRF rules. The deflection results show the live load deflection, the deflection after attachment of the partitions, its long-term part and the total, and can leave out the long-term deflection that happens before the partitions are built. The columns include slenderness: the sway effect from the ETABS P-delta analysis and the member effect by moment magnification. Each command saves its results (`.xlsx`), calculation report (`.pdf`) and schedules (`.dxf`) in the folder you choose. See [Concrete beam and column design](docs/concrete_beam_column_design.md).

## Tests

```powershell
python -m pip install -r .github/requirements-ci.txt
python -m pytest tests
```

The tests need no ETABS. GitHub Actions runs them on every push to `main` (`.github/workflows/ci.yml`).

For the checks that do need ETABS, `python tools/etabs_run.py scenario.json` starts an ETABS of its own, runs the commands of the scenario on a copy of a model from their answers, and compares every result with a reference: text and counts exactly, numbers within 1e-9. See [ETABS test runs](docs/etabs_test_runs.md).

## Building and releasing the program

```powershell
.\build_exe.ps1 -Zip
```

This builds `dist\sdt\sdt.exe` with PyInstaller and packs `dist\sdt-<version>-windows.zip`. Run `dist\sdt\sdt.exe doctor` to check the build.

To release a version: set it in `pyproject.toml` and in `VERSION` of `main.py`, write `docs/releases/v<version>.md`, merge to `main`, then push a tag:

```powershell
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The tag starts `.github/workflows/release.yml`: it runs the tests, builds the program and publishes a GitHub Release with the program zip, the source zip and the release notes.

## Future development

Not in this version, and planned for later ones:

- **Shear wall design.** Walls are counted and reported as not designed.
- **Drift in the design loop.** Drift is checked on the final sizes and reported; members are not resized for it.
- **Steel and composite members from the ETABS model.** `sdt steel` and `sdt composite` are standalone checks with typed inputs.
- **Flanged beams.** The design loop resizes rectangular beams and rectangular or circular columns only.
- **A command for the irregularity checks** of UBC 97 and NSCP 2015: the torsional irregularity of each story (Table 16-M, NSCP Table 208-10) with the accidental torsion amplifier Ax (UBC 1630.7, NSCP 208.5.7), the other plan irregularities, and the vertical ones (Table 16-L, NSCP Table 208-9).
- **P-delta stability coefficient** in that command: the ratio θ of the secondary to the primary moment of each story (UBC 1630.1.3, NSCP 208.6.3), which says whether P-delta may be neglected. Today only whether P-delta is switched on is checked.
- **Redundancy factor ρ and the system factors.** ρ is a fixed value, and R and Ω0 are not checked against Table 208-11A by system.
- **Slabs on the framing plans.** `sdt plans` draws beams, columns, walls and grids; slab marks and span arrows are not drawn yet.
- **Reinforcing bars in the 3D model.** `sdt 3d` writes the concrete only; the bars of the beam and column designs are not in it yet.
- **IFC export.** The 3D model is a STEP file, which holds the solids and their names. An IFC file would also say what each solid is (a beam, a column, its story and material) for BIM programs such as Revit and for model checkers.
- **Seismic checks for other codes.** The period, base shear and spectrum scaling checks read UBC 97 seismic patterns; other patterns are designed for but those checks are reported as not applicable.
- **Further checks and features** not listed here are added as they are built; each release lists what it adds.

## Engineering use

These tools automate calculations; they do not replace engineering judgement. Check the results independently before using them for design, and read the limitations section of each guide.

## License

[MIT](LICENSE) © 2026 Nhel Harold Niegas. The software is provided as is, without warranty of any kind.
