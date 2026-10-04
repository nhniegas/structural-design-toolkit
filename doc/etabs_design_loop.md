# Design Loop: Analysis, Design and Resizing

`etabs_api/workflows/design_loop.py` runs the ETABS analysis and the beam and column design again and again, resizing members until they pass. ETABS concrete design is not used, and no spreadsheet is needed.

## How to use it

1. Open the model in ETABS (saved).
2. From the project folder:

```powershell
xs design
```

(or `python main.py design`). The questions, asked once:

| Question | Notes |
|---|---|
| Seismic combinations | Static (EQ), response spectrum (RSA) or both. Gravity and wind `ULS` combinations are always included |
| Live load reduction, tributary area method, pattern live load | As in the extraction (see the [concrete guide](concrete_beam_column_design.md#step-1-etabs-extraction)) |
| Design inputs | SMRF on or off, the gravity combination for the beam seismic shear, beam bars and cover, column bars and cover (as in `xs beams` / `xs columns`) |
| Column questions | Bottom-story cover and vertical bars carried down; asked once for every iteration |
| Deflection limit | L/480 (partitions likely to be damaged) or L/240 |
| Loop limits | Size increment past the setup ranges (50 mm), largest beam width and depth, largest column side, the downsizing threshold (0.7), the beam line similarity (30 %) and the number of rounds (5). Remembered for next time |
| Drift of the final sizes | Where to read the drift, static and/or spectrum drift combinations, and the wind limit, as in [`xs drift`](etabs_drift.md) |
| Output folder | For the final results, calculations and schedules |
| Size ranges | Only for section families that have none in the setup inputs (for example `FTB` tie beams); saved with the model's setup inputs |

The model is saved as `<model> - DESIGN.EDB` and the loop works on that copy; the original is not changed. At the end the final design is written to the output folder (results, calculations, schedules, as `xs beams` and `xs columns` do) and stored in `<model> - DESIGN - design data.pkl`, so `xs columns` can be run again on it. The log `<model> - DESIGN log.txt` lists every iteration.

**Drift.** Drift depends on the final member sizes, so it is checked once at the end, with `xs drift` on the final working copy, and saved as `<model> - DESIGN - Drift.txt` in the output folder. The sections are **not** resized for drift. If a check fails, the log and the terminal say so: reconfigure the model for drift (stiffer members or walls) and run `xs design` again.

## One iteration

1. Analysis, with the response spectrum scaled to 100 % of the static base shear. The scale factors start from the unscaled spectrum, g I / R from the model's seismic patterns, every time. So they follow the current stiffness and do not carry over a scaling from an earlier `xs analyze`.
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
| Column | Joint shear or beam-column strength in one direction | The side along that direction grows (from the column's local axes; a column more than 25 degrees off the axes grows square) |
| Column | Flexure, axial load, no bar count within the 6 % limit, shear, SMRF dimension | Straight to the **first size that passes on the forces of the current analysis** (see below); the next analysis confirms it |

- **Sizing on the current forces.** A column that fails flexure, axial load, the steel limit or shear is not grown one size per analysis. The candidate sizes (each the next "square" size up, as many as 12) are designed on the forces of the analysis just run: bar layout search on the interaction surface, transverse detailing, SMRF dimensions and the shear steel limit. The column goes to the first size that passes (or to the largest when none does). A larger column attracts more force, so the next analysis checks it again; usually it passes, and a column that needed three sizes takes one or two analyses instead of three. Joint shear and beam-column strength failures depend on the beams and the other columns at the joint, so they still grow one side one size. `LoopSettings.size_on_forces = False` restores the old one-step behaviour.
- Members of one beam line (`2GX-3`, `2GX-3A`, ...) take the same size unless their lengths differ by more than the similarity limit.
- A column is never smaller than the column above it.
- Transverse detailing failures (tie spacing, confinement) do not resize a column: they are solved with ties, and are reported.
- **Downsizing**: a passing member whose ratios are all below the threshold goes one size smaller and the next analysis confirms it. Beams: tension steel ratio, shear and deflection ratio; columns: flexure, shear and joint shear utilization, steel ratio, beam-column strength ratio at least 1.2 / threshold. A member that grew in this run is never made smaller again, and beams never go below the ACI 318-14 Table 9.3.1.1 depth (L/16, cantilevers L/8).
- Sizes follow the setup ranges of the family, then grow by the increment up to the largest size given. Missing sections are created with the setup rebar data and no stiffness modifiers; the modifiers assigned to the frames stay with them when their section changes. Concrete and rebar never change.

## Time

Column design is vectorized: the interaction surface of a bar layout is computed in closed form (no polygon clipping per grid point) and every demand of a column is checked against it in one call. On a 48-column, 4-story SMRF test model with 20 combinations column design takes about 5 s (it was 16 s), and a non-SMRF set of 20 columns with 80 load sets about 2 s (it was 20 s). Most of an iteration is now the ETABS analysis and extraction; sizing on the current forces cuts the number of those.

## Not covered yet

- Resizing for drift (it is only checked and reported), and the irregularity and torsion checks.

## Tests

`tests/test_design_loop.py` (resizing decisions, combination choice) and `tests/test_sections.py` (section sizes). The loop was run on a copy of the test model.
