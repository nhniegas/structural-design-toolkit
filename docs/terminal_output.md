# Results in the Terminal

When a command ends, the terminal shows its results as tables, with the equations of the checks and the status of each check in colour. This guide says what is shown, and what to expect when the output is not a terminal.

## What you see

| Part | How it is shown |
|---|---|
| Tables | Bordered, with the numbers of a column aligned to the right and the same number of decimals down the column |
| Status | `OK` and `PASS` in green, `WARN` in yellow, `FAIL` in red, `N/A` dimmed |
| Equations | In a box of their own, written with the symbols a terminal has: `φMₙ ≥ Mᵤ`, `√f′c`, `Δ_M = 0.7 R Δ_S` |
| What needs attention | In red, in the closing summary |

A terminal cannot draw LaTeX as a PDF does. The equations use Greek letters, the root sign and small letters above and below the line. Where no such letter exists (there is no small `b`, `c`, `d`, `f`, `g`, `w` or `z` below the line, and no capital), the subscript is written beside its symbol, as engineers type it: `Lb`, `Ag`, `bf`, `φc`, `ΔM`. An exponent is raised (`hₙ³⁄⁴`); one that is itself an expression keeps its mark, `0.658^(Fy/Fe)`. No command prints LaTeX source, an underscore before a subscript, or a caret before a plain exponent; a test checks the equations of every command for this.

## What each command shows

| Command | Tables | Equations |
|---|---|---|
| `sdt check` | One table for each group of checks: status, check, clause | With the numbers of the model: the base shear `V/W = Cᵥ I / (R T)` and its limits, the Method A period and its cap, the drift `Δ_M = 0.7 R Δ_S`, the wind drift, the vertical effect `Eᵥ = 0.5 Cₐ I D`, the spectrum scaling |
| `sdt analyze` | Spectrum scaling, periods, modes, modal mass, weight of each story | The scale factor of each spectrum case, `hₙ`, `T_A = Cₜ hₙ^(3/4)` and the cap, with their numbers |
| `sdt drift` | The checks of each stiffness level, and the drift of every story | The drift of each case with its numbers |
| `sdt beams` | One row for each beam: section, top and bottom bars at the left support, midspan and right support, stirrups, deflection ratio, status | Flexure, shear, torsion, the SMRF shear and bar rules, deflection |
| `sdt deflection` | One row for each beam: span checked, the two deflections with their limits, ratio, check | Effective inertia, long-term factor, the two checks |
| `sdt columns` | One row for each column: section, bars, steel ratio, the largest utilizations, the lowest strong column ratio, status | Axial load and bending, slenderness, shear, strong column - weak beam, joint shear, confinement |
| `sdt design` | The final beams and the final columns, as above | As above |
| `sdt steel` | The tables of the calculation report | Every equation of the calculation report, with its numbers |
| `sdt composite` | Section checks, strengths, shear, interaction | Axial strength, shear and the interaction |
| `sdt wind` | Inputs, velocity pressure profile, Cp tables, pressures | The velocity pressure with its numbers, and the pressure equation |
| `sdt pilecap` | The bars, every check with its clause, the pile reactions | Pile reactions, flexure, one-way and two-way shear, strut-and-tie |
| `sdt doctor`, the menu, every closing summary | Tables | |

**One-member checks and model checks print their equations with numbers.** For `sdt beams`, `sdt columns` and `sdt design` the equations are printed once, as the key to the table. The calculation of each member, with its numbers, is in the PDF calculation report.

**Bars in the beam table.** `4+2 | 2 | 4` is the left support, the midspan and the right support; `4+2` is four bars in the first layer and two in the second.

**A large model.** A model with more than 40 beams or columns lists the members that fail, and says how many pass; every member is in the results workbook.

## When the output is not a terminal

Sent to a file or a pipe, or run from an [answers file](answers_file.md) in a test, the same content is written as plain text: no borders, no colour, the columns still aligned. The saved reports (`<model> - Drift.txt`, the summary `.txt` files, the design loop log) are plain text as before.

`SDT_PLAIN=1` in the environment gives plain text in a terminal too:

```powershell
$env:SDT_PLAIN = "1"
sdt check
```

A symbol that the output cannot hold (an old console code page) is replaced by `?`; the command does not stop for it.

## Speed

The design takes the same time. The tables are built once, when the command ends, from results that are already there. The package that draws them (`rich`) is loaded only when something is printed, in about a tenth of a second, and nothing is printed for each member while a design runs.

## Code

- `utilities/terminal.py`: the report (headings, tables, equations, lines), its two forms, and `math`, which turns the LaTeX of a calculation report into terminal symbols.
- `design/concrete/results_tables.py`: the beam, deflection and column tables and their equations.
- Each command builds its own report: `findings_report` in `model_check.py`, `AnalysisReport.report`, `DriftReport.report`, `RunSummary.report`, and `terminal_report` in the steel, composite, wind and pile cap modules.

## Tests

`tests/test_terminal.py`: the symbols, the alignment of the tables, the plain and drawn forms, and the report of each command.
