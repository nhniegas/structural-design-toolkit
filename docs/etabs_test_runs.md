# ETABS Test Runs

`tools/etabs_run.py` runs the commands in ETABS from a file, with no one at the screen, and compares what they give with a reference. It is for checking the program after a change. It is run from the source code; it is not part of `sdt.exe`.

## How to use it

```powershell
python tools/etabs_run.py scenario.json --save-reference   # run, and keep the results as the reference
python tools/etabs_run.py scenario.json                    # run, and compare with the reference
```

The first makes the reference, with code you trust. The second, after a change, says whether anything moved.

| Option | Effect |
|---|---|
| `--save-reference` | Keep the results of this run as the reference |
| `--keep-open` | Leave the ETABS of the run open; its process number is printed |
| `--pid N` | Use that ETABS again. Starting ETABS takes about two minutes; a run on an open one starts in seconds |
| `--only a,b` | Run only these steps, by name or number |
| `--units kN-m` | Set the present units of the model before the steps, as a model made in those units has them |
| `--tolerance X` | Accepted relative difference of a number: 1e-9 by default, 0 for none |

## The scenario

```json
{
  "model": "t34.EDB",
  "steps": [
    {"command": "beams", "folder": "out", "unanswered": "stop", "answers": [
      {"ask": "Seismic combinations to design for", "answer": "Both"},
      {"ask": "SMRF (seismic) design?", "answer": "Yes"}
    ]},
    {"command": "columns", "folder": "out", "unanswered": "stop", "answers": []}
  ]
}
```

Each step is one command with the answers to its dialogs, written as in an [answers file](answers_file.md). `model` is relative to the scenario file. Beside the scenario the run makes `<scenario> run` (the copy of the model, the outputs, the output of each step, `run report.txt`) and `<scenario> reference` (the reference). A step can have a `name`; `work` and `reference` name other folders.

To write a scenario for a new model, give the steps `"unanswered": "default"` and no answers, run it once, and copy the answers from the lines the run prints. A run with `"unanswered": "stop"` and every answer given is the one to keep.

## What keeps the results trustworthy

- **The commands themselves.** Each step is `sdt <command> --answers`: the same code as with the dialogs, given the same values the dialogs return.
- **A copy of the model.** The model, with its `.setup.json`, is copied to the work folder, which is emptied first. The model itself is never opened.
- **Its own ETABS.** A new ETABS is started for the run and closed at the end, also when a step fails. The commands are tied to that process, so an ETABS you have open is not touched.
- **The program's own defaults.** The answers remembered on this machine are not read and not changed: the run has an empty settings folder of its own.
- **Every value compared.** What each command returns is saved and compared with the reference: the extracted tables, the design results, the findings, the reports.

## How the comparison works

| Kind of value | Rule |
|---|---|
| Text: a name, a status, a check, a section | Must be the same |
| Whole numbers: bar counts, stirrup legs, spacings, sizes | Must be the same, whatever the tolerance |
| Other numbers | Within the tolerance: 1e-9 of the largest number of their column |
| Columns and number of rows | Must be the same |
| `GUID` and `PointBay` of the joints | Left out: ETABS makes the joints of its mesh again at each analysis and gives them new ones |
| Paths and times | Left out: they are where and when the run was, not a result |

The tolerance is there for one reason. ETABS does not give the last digits of a force the same at every analysis: on the test model the forces of two analyses of the same file differ by about 1e-12 of their size. The design results are rounded, and came out the same to the last digit.

Nothing is hidden by it: the report says how many numbers differed within the tolerance and how large the largest difference was. A difference beyond it fails the run and is listed with its table, column and row:

```
columns   ok   57s   8   0   3 differences from the reference
    columns.column_report[Pu_kN]: 102 of 3854 values differ; row 290: 417.31, the reference has 402.36
```

A whole number in a column of decimals is held to the same rule as a count: 4.0 against 5.0 is a difference at any tolerance.

## The report

```
step        result    time  dialogs  defaults  comparison
beams       ok         28s       18         0  same as the reference: text and counts identical, 540,198 numbers within 1e-09 (largest difference 2.0e-12 of its size, in beams.tables.FACTORED LOADS[T])
columns     ok         57s        8         0  same as the reference: ...
deflection  ok          3s        3         0  identical to the reference, to the last digit

PASSED: every step ran and its results are those of the reference.
```

`defaults` counts the dialogs that took what they offered; it is 0 when every answer is given. The run ends with `PASSED` only when every step ran and was compared; `RAN` when a step had no reference; `FAILED` otherwise, and the command then returns 1.

## What was measured

On the 4-story test model (102 beams, 47 columns, 41 combinations), `beams`, `columns`, `deflection`, `plans`, `check`, `analyze` and `drift` run in about 3 minutes on an open ETABS, with nothing to click. The design loop runs in under 6 minutes.

The first runs showed three things:

- The beam design from an answers file gave the results of the earlier run made with dialogs: all 204 rows and 92 columns the same, to the last digit.
- A run with the model in kN-m gave the results of the reference.
- The column report was not the same from one analysis to the next at pinned column bases, which was corrected (see the [concrete guide](concrete_beam_column_design.md#column-strength-the-biaxial-interaction-surface)).

For v0.7.0 a second scenario, `t34 own patterns.json`, runs `setup`, `check`, `analyze` and `drift` with every dialog at its default on a copy of the test model that was given load patterns of its own (`Dead` with a self weight of 1.0, `Live`, `EQX`), as a model from another office has. It checks that setup puts `Dead` and `Live` in the combinations and the mass, sets the second self weight to 0, and does not list the patterns ETABS makes by itself (`WX(1/12)`, `~LLRF`). The copy is made by a short script beside the scenario, since ETABS must add the patterns.

## Limits

- The models are not part of the repository, so the scenarios and references stay with the models on your machine.
- A reference is only as right as the code that made it: the comparison says that nothing changed, not that the results are correct. The checks against hand calculations are in `tests/`.
- A dialog that ETABS itself shows stops the run until it is closed. One is closed by the runner: the "Error initializing shader" box that ETABS can show after an analysis, which is about its own drawing. The runner presses OK on it and says so at the end of the run.
- Run it with its output in a file or a terminal of its own. An ETABS left open with `--keep-open` keeps a pipe of the command that started it open.

## Tests

`tests/test_etabs_run.py`: the comparison rules, the results as plain data, the scenario and the work folder. They need no ETABS.
