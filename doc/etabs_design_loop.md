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
| Output folder | For the final results, calculations and schedules |
| Size ranges | Only for section families that have none in the setup inputs (for example `FTB` tie beams); saved with the model's setup inputs |

The model is saved as `<model> - DESIGN.EDB` and the loop works on that copy; the original is not changed. At the end the final design is written to the output folder (results, calculations, schedules, as `xs beams` and `xs columns` do) and stored in `<model> - DESIGN - design data.pkl`, so `xs columns` can be run again on it. The log `<model> - DESIGN log.txt` lists every iteration.

## One iteration

1. Analysis, with the response spectrum scaled to 100 % of the static base shear. The scale factors start from their original values every time, so they follow the current stiffness.
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
| Column | Flexure, axial load, no bar count within the 6 % limit, shear, SMRF dimension | Next size with both sides at least the current ones, the most square of the smallest |

- Members of one beam line (`2GX-3`, `2GX-3A`, ...) take the same size unless their lengths differ by more than the similarity limit.
- A column is never smaller than the column above it.
- Transverse detailing failures (tie spacing, confinement) do not resize a column: they are solved with ties, and are reported.
- **Downsizing**: a passing member whose ratios are all below the threshold goes one size smaller and the next analysis confirms it. Beams: tension steel ratio, shear and deflection ratio; columns: flexure, shear and joint shear utilization, steel ratio, beam-column strength ratio at least 1.2 / threshold. A member that grew in this run is never made smaller again, and beams never go below the ACI 318-14 Table 9.3.1.1 depth (L/16, cantilevers L/8).
- Sizes follow the setup ranges of the family, then grow by the increment up to the largest size given. Missing sections are created with the setup stiffness modifiers and rebar data. Concrete and rebar never change.

## Time

The column design takes most of the time (about 10 minutes for the 44 columns of the test model). A round with several column iterations takes accordingly long.

## Not covered yet

- Drift, irregularity and torsion checks (see [analysis checks](etabs_analysis.md)).
- Redesigning only the members that changed between column iterations.

## Tests

`tests/test_design_loop.py` (resizing decisions, combination choice) and `tests/test_sections.py` (section sizes). The loop was run on a copy of the test model.
