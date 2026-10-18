# The Full Project Report (`sdt design`)

`sdt design` can end by writing one PDF for the whole project: the analysis, the design, and the calculation tables of every member. It is asked for before the loop starts and written when the loop ends, in the folder of the results, as `<model> - DESIGN - Project Report.pdf`.

Code: `etabs_api/workflows/project_report.py`. The look is that of the other reports (see [The calculation reports](calculation_reports.md)).

## How to ask for it

Among the questions of `sdt design`, after the folder of the results:

| Question | Choices |
|---|---|
| Write the full project report? | No, or yes |
| Front page (when yes) | Project, location, client, prepared by: each may be left empty. And the thickness of the slab on grade for the concrete quantities, 0 for none |

What you type is remembered for the next run. The loop then runs as usual; the report adds a few minutes at its end, most of them for the interaction surface of each column.

## What it holds

**Front page.** The title block with what you typed, the model, the date and the codes; the contents; and one table of results: model checks, story drift, irregularities, beams, columns.

**Part A, Analysis**

| Section | What it shows | From |
|---|---|---|
| A1 Building and model | The stories with their heights, and a 3D view of the structure | The model |
| A2 Framing plans | One plan for each floor: beams and girders at their width with their names, the columns with their marks | As `sdt plans` reads the model |
| A3 Materials and sections | The strengths, every frame section used and how many members have it, the stiffness modifiers | The design data, `sdt check` |
| A4 Load patterns and cases | The patterns with their types, the largest uniform load of each pattern on each story, the load cases, the mass source and the P-delta loads | The text file of the model |
| A5 Seismic criteria | Ca, Cv, I, R, Ct, the seismic source, the weight W, the Method A period and its cap; the static patterns; the base shear coefficients; the scaling of the response spectrum | The text file, the last analysis of the loop, `sdt check` |
| A6 Wind criteria | Code, speed, exposure, Kzt, G and Kd of each wind pattern | The text file |
| A7 Load combinations | Every combination with its terms, by group: strength, service, special seismic, drift, deflection, direction combinations | The text file |
| A8 Analysis results | Modal periods and mass, the weight of each level | The last analysis of the loop |
| A9 Story drift | Seismic and wind drift of each story, at each stiffness level | The drift check of the final sizes |
| A10 Irregularities and P-delta | Every type with its result, and the tables behind them; the stability coefficient | As `sdt irregularity` |
| A11 Model checks | Every check of the model with its clause | As `sdt check` |

**Part B, Design**

| Section | What it shows |
|---|---|
| B1 Design basis | Codes, seismic detailing, bar sizes, cover, design forces |
| B2 Beam design | Every beam: section, top and bottom bars at the left support / midspan / right support, stirrups, deflection ratio, result |
| B3 Column design | Every column: size, bars, ties, steel ratio, the largest flexure, shear and joint ratios, result |
| B4 Concrete quantities | Volume by story, floor area, and the concrete ratio in m3/m2 |
| B5 Files of the design | The other files the loop wrote |

**Part C, Member calculations.** The calculation tables of each beam and each column, those of the beam and column reports. Each column starts on a new page, with its interaction surface and its P-M diagrams in the XZ and YZ planes.

## Notes on what is listed

- **Envelope combinations are not listed.** An envelope has no factor of its own: it names other combinations, which are all in the list. The report says how many were left out.
- **The loads, cases and combinations come from `<model>.$et`**, the text copy of the model that ETABS writes beside it when it saves. If that file is not there, sections A4 to A7 are left out and the front page says so.
- **The floor loads** are the largest uniform load of each pattern on each story, in kPa. Line and point loads are not listed.
- **A part that cannot be read does not lose the report.** It is left out, and a line on the front page names it and why.

## Size

Part C makes the report long: about a page for each beam and two for each column. The test model, 102 beams and 47 columns, gives 247 pages in a file of 4 MB.

## Limits

- The framing plans of the report are pictures for the page, with no grid lines and no dimensions. The plans for the drawings are those of `sdt plans`.
- The 3D view shows the concrete of the model, drawn by the report itself; it is not a rendering from ETABS.
- The report is written at the end of `sdt design` only. It cannot yet be written for a model designed earlier.
- It needs LaTeX, like the other PDF reports. Without it the loop ends as usual and its log says the report was not written.

## Tests

`tests/test_project_report.py`: what is read from the text file of a model (stories, patterns, seismic and wind values, cases, combinations, floor loads), which combinations are listed and how they are grouped, the lines of the beam and column tables, a round column, and the LaTeX of the report with parts left out. They need no ETABS and no LaTeX.

In ETABS: `sdt design` was run on the test model with the report asked for (the `t34 design` scenario). The loop ended as usual and wrote the report, 246 pages, with no part left out.
