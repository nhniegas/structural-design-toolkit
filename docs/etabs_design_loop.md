# Design Loop: Analysis, Design and Resizing

`etabs_api/workflows/design_loop.py` runs the ETABS analysis and the beam and column design again and again, resizing members until they pass. ETABS concrete design is not used, and no spreadsheet is needed.

## How to use it

1. Open the model in ETABS (saved).
2. From the project folder:

```powershell
sdt design
```

(`python main.py design` does the same). The questions, asked once:

| Question | Notes |
|---|---|
| What the model lacks | Only on a model not made with `sdt setup`: a dialog lists what was found and what is missing, then you pick the strength and deflection combinations and say what to do with untagged members. A second dialog lists the sections with other names, grouped as girders, beams, tie beams and columns. See [Working on a model the toolkit did not set up](existing_models.md) |
| Seismic combinations | Static (EQ), response spectrum (RSA) or both. Gravity and wind `ULS` combinations are always included. Not asked when you picked the combinations yourself |
| Live load reduction, tributary area method, pattern live load | As in the extraction (see the [concrete guide](concrete_beam_column_design.md#step-1-etabs-extraction)) |
| Design inputs | SMRF on or off, the gravity combination for the beam seismic shear, beam bars and cover, column bars and cover (as in `sdt beams` / `sdt columns`) |
| Column questions | Bottom-story cover and vertical bars carried down; asked once for every iteration |
| Cover against earth | The floor levels whose beams get 75 mm cover, as in `sdt beams` |
| Capacity-design checks by level | With SMRF on: whether BCC and joint shear are run at the topmost level, and BCC, joint shear and Ve at the foundation level, as in `sdt columns`. A level left out no longer drives the column sizes |
| Interior ties of the column schedule | Crossties or closed inner hoops, as in `sdt columns`; used for the `Column_Schedule.dxf` of the final design |
| Deflection limit | L/480 (partitions likely to be damaged) or L/240 |
| When the partitions are built | Not known, or the months and the dead load share before them, as in `sdt beams`. With them the long-term deflection before the partitions is deducted, so fewer beams are made deeper for deflection |
| Target ratios | The code limits, the targets saved for this model, or your own for girders, beams and columns, check by check. The loop then sizes the members to them: a member above its target is made larger, and "make smaller" is measured against the target. See the [concrete guide](concrete_beam_column_design.md#target-ratios) |
| Beam torsion | The analysis torsion, or at most φTcr (compatibility torsion), as in `sdt beams` |
| Beam bar spacing | The office rule of 150 mm clear, or the code only (crack control), as in `sdt beams` |
| Girders under 4d | With SMRF: apply the SMRF rules to girders with a clear span under 4d, or design them without, as in `sdt beams`. Without the rules their capacity shear no longer grows with the section, so the loop can size them |
| Depth of a beam that carries others | When yes, a carrier shallower than a beam it carries is made deeper, and no carrier is made shallower than the beams it carries |
| Loop limits | One dialog, remembered for next time. **Shared:** size increment past the setup ranges (50 mm), the downsizing threshold (0.7), the number of rounds (5). **Beams:** largest width and depth, iterations in a round (10), the beam line similarity (30 %), the largest bend between members of a beam line that share a size (15 degrees), the share of the governing moment and shear a member needs to take that size (75 %), the smallest beam width as a share of the depth (40 %). **Columns:** largest side, largest side ratio long / short (2), iterations in a round (10). The largest sizes are real limits: a setup range that goes beyond them is cut there |
| Drift of the final sizes | Where to read the drift, static and/or spectrum drift combinations, and the wind limit, as in [`sdt drift`](etabs_drift.md). Without `DRIFT` / `WDRIFT` combinations in the model, you pick the drift combinations |
| Output folder | For the final results, calculations and schedules |
| Size ranges | For section families that have none in the setup inputs (for example `FTB` tie beams). When the model has no setup inputs of its own (no `<model>.setup.json` beside it), every family is shown with a range to confirm or change, since the loop makes members smaller down to the first size of the range. The dialog lists the sizes the model has now, and the range shown always holds them: the default range widened where needed, or, for a family with no default (circular columns), from the smallest size in the model to two steps above the largest. Saved with the model's setup inputs |
| Seismic values | Z and Ct for the period cap are read from the model's UBC 97 seismic patterns. Asked only when the model has none, or when a value differs from the one saved before |

The model is saved as `<model> - DESIGN.EDB` and the loop works on that copy; the original is not changed. At the end the final design is written to the output folder (results, calculations, schedules, as `sdt beams` and `sdt columns` do) and stored in `<model> - DESIGN - design data.pkl`, so `sdt columns` can be run again on it. The log `<model> - DESIGN log.txt` lists every iteration: each analysis, the spectrum scaling and every section change with its reason. Each iteration ends with its time and how it divides: analysis, reading the results, beam design, column design, choosing the sizes and resizing. The terminal lists the same lines as it runs. When the loop finishes, a separate window shows its summary, also saved as `<model> - DESIGN summary.txt` in the output folder: the status and the number of iterations, how many beams and columns were designed and fail, the net size changes (first size to final size, members grouped; a member that returned to its first size is not listed), what still fails, the drift result and the files.

**Drift.** Drift depends on the final member sizes, so it is checked once at the end, with `sdt drift` on the final working copy, and saved as `<model> - DESIGN - Drift.txt` in the output folder. The sections are **not** resized for drift. If a check fails, the log and the terminal say so: reconfigure the model for drift (stiffer members or walls) and run `sdt design` again.

**Section names.** The loop reads the size of every beam and column section from its name when it follows the setup naming, and from ETABS otherwise. A new size is always created under the setup name, with the concrete, rebar material and cover of the section it replaces and no stiffness modifiers on the section. The log names the model's own section in each change.

## One iteration

1. Analysis, with the response spectrum scaled to 100 % of the static base shear. The scale factors start from the unscaled spectrum, g I / R from the model's seismic patterns, every time. So they follow the current stiffness and do not carry over a scaling from an earlier `sdt analyze`.
2. Extraction: forces from the analysis results, service loads for the deflection checks, frame data and connectivity (in memory).
3. Beam design, with the deflection checks.
4. In the column phase, column design (it uses the beam bars at the joints).
5. Resizing.

## Order

- **Beams and girders** first: iterations until no beam changes.
- **Columns**: iterations until no column changes. Beams are redesigned in each, since the columns change the forces.
- **Final check** of every member on the final analysis, without downsizing. If anything still has to change, a new round starts.

The loop stops when the final check changes nothing (converged) or after the number of rounds.

## Resizing rules

| Member | Failure | Change |
|---|---|---|
| Beam | Bars do not fit in 3 layers, girder steel ratio above 2.5 %, SMRF moment ratios, deflection | Deeper first; wider when deeper would break width / depth >= 0.3 |
| Beam | Stirrup spacing below the minimum (shear, torsion) | Wider first |
| Column | Joint shear or beam-column strength in one direction | The side the joint is measured along grows, one step: the depth for the beams along the depth (Y, local 2), the width for those along the width (X, local 3). The report gives these per column axis, so the rotation of the column does not matter |
| Column | Flexure, axial load, slenderness (δns above 1.4 or Pu at 0.75 Pc), no bar count within the 6 % limit, shear, SMRF dimension | Straight to the **first size that passes on the forces of the current analysis** (see below); the next analysis confirms it |

- **Sizing on the current forces.** A column that fails flexure, axial load, the steel limit or shear is not grown one size per analysis. The candidate sizes (each the next "square" size up, as many as 12) are designed on the forces of the analysis just run: bar layout search on the interaction surface with the slenderness of the trial size (its own r and Ig, on the lu and k of the last design), transverse detailing, SMRF dimensions and the shear steel limit. The column goes to the first size that passes (or to the largest when none does). A larger column attracts more force, so the next analysis checks it again; usually it passes, and a column that needed three sizes takes one or two analyses instead of three. Joint shear and beam-column strength failures depend on the beams and the other columns at the joint, so they still grow one side one size. `LoopSettings.size_on_forces = False` restores the old one-step behaviour.
- **One size along a beam line.** When a member has to grow, the other members of its line (`2GX-3`, `2GX-3A`, ...) take its size when all of these hold:
  - they are held the same way. The three ways are a cantilever, a span between supports, and a gravity beam on other beams only. So a deep cantilever does not make the span beside it deeper, and a span between columns does not make a cantilever deeper;
  - their spans differ by no more than the similarity limit (30 % of the longer one by default). The span is the one the deflection was checked on, between its supports, not the length of the ETABS member;
  - they bend from each other by no more than the largest bend (15 degrees by default). The tags put members in one line through a bend of up to 45 degrees, which is too loose for sharing a size on skewed framing. Set it to 45 to follow the tags, or to 0 to share a size only between members in a straight line;
  - their own largest moment and shear are at least a share of those of the member that governs (75 % by default; 0 switches this off). A much less loaded member keeps its own size.
- **The pieces of one span always take one size**, whatever their lengths and loads: a girder is not stepped between its supports.
- **Width against depth.** A beam that is resized keeps its width at least a share of its depth (40 % by default; 30 % is the least for a special moment frame, ACI 18.6.2.1(b)). A beam made deeper is made wider in the same step when the depth needs it. Existing beams that are not resized are left as they are.
- **A deflection that growing does not help.** A deflection that the beam's own stiffness governs falls by a quarter or more for each step of depth. One that hardly moves (less than 5 %) comes from elsewhere: the rotation of the support of a cantilever, or the movement of what carries the beam. Such a beam keeps its size, and the log gives its ratio before and after; the beams that only followed it along its line stay as they are too. Look at the framing, not the size.
- **A beam that growing does not help.** The capacity shear of a frame beam comes from its own probable moments, so a larger beam with more bars can need more shear, not less: a short span is the usual cause. A beam made larger 3 times for shear that still fails keeps its size, and the log says so; the beams that only followed it along its line stay as they are too. Look at its span and its bars, or at the [bar spacing rule](concrete_beam_column_design.md#beam-bar-spacing).
- With SMRF on, a column is not made smaller than 20 bar diameters of the beam main bar (ACI 18.8.2.3: 500 mm for 25 mm bars). Below that the joint fails its dimension rule, the column grows again and, having grown, can never be made smaller.
- The log starts with the size ranges and limits in use.
- Transverse detailing failures (tie spacing, confinement) do not resize a column: they are solved with ties, and are reported.
- **Downsizing**: a passing member whose ratios are all below the threshold goes one size smaller and the next analysis confirms it. Beams: tension steel ratio, shear and deflection ratio; columns: flexure, shear and joint shear utilization, steel ratio, beam-column strength ratio at least 1.2 / threshold. A member that grew in this run is never made smaller again, and beams never go below the ACI 318-14 Table 9.3.1.1 depth (L/16, cantilevers L/8).
- Sizes follow the setup ranges of the family, then grow by the increment up to the largest size given. Missing sections are created with the setup rebar data and no stiffness modifiers; the modifiers assigned to the frames stay with them when their section changes. Concrete and rebar never change.

## When the loop is done

Changing the size of one member shifts the forces in the others, so no member is judged on old forces:

- **Every iteration designs every member again** on the forces of the analysis just run, whether its own size changed or not.
- **An iteration that follows one with no section change does not analyse again.** The model is the one the last analysis and design were made on, so they are used as they are: the first column iteration after the beams settle, and the final check after the columns settle. The log says so. Nothing is carried over once a section has changed.
- **Every round ends with a final check of every member**, beams and columns together, with no member made smaller. The results that are written come from the last such pass, on the final sizes and their forces.
- **Converged** means that a final check changed nothing: nothing fails that a size can fix. If the final check still has to make a member larger, another round starts, up to the largest number of rounds. The log and the summary say whether the loop converged or stopped at that limit.

What "optimal" means here: within a round, a member whose ratios are all under the "make smaller" threshold goes one size down, and a member that had to grow is not made smaller again. So the loop ends where no member that was trimmed can lose another size without failing. It is not a least-weight or least-cost design: members are moved one at a time, with no objective for the whole frame, and a threshold close to 1.0 leaves little room between "can shrink" and "must grow", which takes more rounds to settle.

Members that no size fixes are listed apart in the log and the summary, by what stops them: at the largest size allowed, shear that grows with the section, or a deflection that the support governs.

## Time

Beam design reads each beam's forces once and cuts the zones of every combination from arrays. On a real model of 2,114 beams, the beam design with deflection and carrier depth takes about 100 s where it took 295 s, with every value of every row the same.

A round whose iterations settle needs fewer analyses than iterations: on the 4-story test model a round of six iterations (three for the beams, two for the columns, the final check) runs four analyses, since two of them follow an iteration that changed nothing.

Reading the results is done on numbers, not on the ETABS display tables: 78 s on that model where it took 224 s.

On a model of 200 beams or more, `sdt` shares the beam design between the cores of the machine: up to 8 processes, each designing a part of the beams, with the results joined in the order one process gives them. The values are the same; on that model the beam design takes 31 s where one process takes 61 s. `SDT_WORKERS=1` in the environment keeps one process, and another number sets how many. `sdt doctor` says whether the processes start on this machine. The column design stays in one process: sharing it was tried and was no faster, since the columns of a joint and of a stack depend on each other and the forces must be passed between the processes.

One full iteration on that model (analysis 84 s, reading 78 s, beam design 31 s, column design about 115 s the first time and 60 s after) is about 4 to 5 minutes, where it was 18 to 25.

Column design is vectorized: the interaction surface of a bar layout is computed in closed form (no polygon clipping per grid point) and every demand of a column is checked against it in one call. On a 48-column, 4-story SMRF test model with 20 combinations column design takes about 5 s (it was 16 s), and a non-SMRF set of 20 columns with 80 load sets about 2 s (it was 20 s). Most of an iteration is now the ETABS analysis and extraction; sizing on the current forces cuts the number of those.

## Not covered yet

- Resizing for drift (it is only checked and reported), and the irregularity and torsion checks.

## Tests

`tests/test_design_loop.py` (resizing decisions, combination choice) and `tests/test_sections.py` (section sizes). The loop was run on a copy of the test model.
