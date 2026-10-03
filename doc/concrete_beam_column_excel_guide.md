# Concrete Beam and Column Excel Workflow

How `spreadsheets/beam_column_designer_aci318.xlsm` works with ETABS and the Python design modules. Design code: ACI 318M-14.

## How the pieces fit

- `design/beam_designer_aci318.py`: ETABS extraction, beam design, beam schedule DXF.
- `design/column_designer_aci318.py`: column design, SMRF checks, column schedule DXF.
- `design/aci318_config.py`: every ACI constant used by both designers, each with its clause. The designers read their factors and limits from here.
- `etabs_api/core/`: the ETABS connection (`connection.py`) and the other thin wrappers around the ETABS API.
- `etabs_api/workflows/`: table extraction to Excel (`exporter.py`), automatic member tagging (`frame_tagger.py`) and the other office workflows.
- `utilities/_gui_helpers.py`: pickers and the loading window.
- `utilities/_calc_report.py`: the PDF calculation report layout used by both designers.

The workbook macros call the design modules directly. There is no intermediate `main.py` layer. Defining the materials, sections, loads and combinations of the ETABS model is a separate step, done before this workflow: see [ETABS model setup](etabs_model_setup.md).

## Buttons and what they run

Run them in this order.

| Step | Macro | Python function |
|---|---|---|
| Optional: tag members | `TriggerAutoTagFrames` | `etabs_api.workflows.frame_tagger.auto_tag_frames` |
| 1. Extract from ETABS | `CallExtractForcesPropertiesCustom` | `beam_designer_aci318.extract_forces_properties_from_etabs` |
| 2. Prepare beam data | `ExtractBeamDesignDataMacro` | `beam_designer_aci318.extract_beam_design_data` |
| 3. Design beams | `TriggerBeamDesign` | `beam_designer_aci318.run_beam_design_from_excel` |
| 4. Export beam DXF | `TriggerExportCADDrawings` | `beam_designer_aci318.export_cad_drawings` |
| 5. Design columns | `TriggerColumnDesign` | `column_designer_aci318.run_column_design_from_excel` |
| 6. Export column DXF | `TriggerExportColumnCAD` | `column_designer_aci318.export_column_cad_drawings` |
| Beam calculations (PDF) | `TriggerExportBeamCalcs` | `beam_designer_aci318.export_beam_calculations` |
| Column calculations (PDF) | `TriggerExportColumnCalcs` | `column_designer_aci318.export_column_calculations` |

Design beams before columns: the SMRF joint checks use the designed beam bars.

## Sheets

| Sheet | Written by | Contents |
|---|---|---|
| `OVERWRITES` | you, and step 1 | Design options and bar sizes; selected combinations (from `B6`) and members (from `C6`) |
| `FACTORED LOADS` | step 1 | Design forces per member, combination, permutation and station (kN, kN-m) |
| `FRAME DATA` | step 1 | Section, dimensions and material strengths per member |
| `CONNECTIVITY` | step 1 | End joints of every beam and column |
| `BEAM DESIGN` | steps 2 and 3 | Beam inputs, then the beam design results, from `B8` |
| `COLUMN DESIGN` | step 5 | Consolidated column report, headers on rows 8 and 9 |

Inputs on `OVERWRITES`:

| Cell | Input |
|---|---|
| `F3` | SMRF (seismic) design on or off. One tick covers beams and columns |
| `F4` | Factored gravity combination used for beam seismic shear |
| `I4` to `I8` | Beam main bar, stirrup bar, web bar diameters (mm), web bar fyw (MPa), cover (mm) |
| `I10` to `I12` | Column main bar, tie bar diameters (mm), cover (mm) |

The sheet also lists the steps of use under `INSTRUCTIONS OF USE`.

## Optional: automatic tagging

`AUTO TAG FRAMES` on `OVERWRITES` (or `python main.py tag` in a terminal) gives every beam and column in the open ETABS model a unique name: beams as `<level><type>-<number><letter>`, for example `2GX-10B`, and columns as `<level>-<type><number><letter>`, for example `3-C5C`. The extraction only reads named members, so tag the model before step 1.

The model is first saved as `<model name> - TAGGED.EDB` in its own folder and the names are changed in that copy. The original file is not changed. ETABS has the tagged copy open afterwards.

| Part | Rule |
|---|---|
| Level | A story named as a number and `F` drops the `F` (`2F` gives `2`). For every other story a dialog asks for the prefix; a level left blank is skipped |
| Type | `G` if at least one end is on a column, otherwise `B`. `X` if the member is within 45 degrees of the global X axis, otherwise `Y`. Columns are `C`; planted columns are `PC` |
| Beam line | Members that share a joint continue one line while the bend is 45 degrees or less; the straightest pair is joined. A line keeps its number across an opening when the next member is straight ahead of it |
| Beam number | Counted per level and per type. X lines top to bottom, Y lines left to right, by the start of the line |
| Letter | Along the line, left to right for X and bottom to top for Y: none, `A`, `B`, ... without `I` and `O`, then `AA`, `AB`, ... |
| Column number and letter | Counted once over the combined plan of all levels, so a column keeps them on every level. A row is the columns along one X girder line; rows run top to bottom and letters left to right. A column takes the level of its upper joint |

A planted column is a stack that does not reach a supported joint. Planted columns are tagged `PC` by the same rule as `C`, with their own numbers starting at 1 (`PD2-PC1`, `PD2-PC1A`). In the column schedule the mark drops the level, so `2-C5A` is `C5A` and `PD2-PC1A` is `PC1A`. A stack with no column on a tagged level is not counted. A new name already used by a member that is not being tagged is skipped and counted in the closing message.

## Step 1: ETABS extraction

The step attaches to the running ETABS model, or asks for a model file and opens it. You pick the load combinations and the members in two list dialogs. ETABS concrete design is run for the selected combinations so the design-force tables exist.

What is read, and how often:

- **Design forces** (`Design Forces - Beams`, `Design Forces - Columns`) are read one combination at a time through `ETABSConnector.get_design_forces`. ETABS does not return these tables reliably when several combinations are selected at once. Each combination is read only once per extraction.
- **Everything else** (frame assignments, section definitions, reinforcing, material properties, connectivity) does not depend on load combinations and is read once.

Members with a purely numeric ETABS name are skipped; only named members are designed.

Units: every table is read in N and mm, whatever units the model was created in and whatever the ETABS window displays. If the model's API units differ, the extraction switches them to N-mm for each read and restores them afterwards. Forces are then written to Excel in kN and kN-m, dimensions in mm, strengths in MPa.

### Load-combination permutations

ETABS writes several row sets for one combination when it contains a response-spectrum or multi-direction case, naming them `ULS 107 ...-1`, `ULS 107 ...-2`, and so on. Each is a different P, M2, M3 set.

`FACTORED LOADS` stores the plain combination name in `Combo` and the suffix in `Permutation`.

- **Beams** take the envelope of all rows of a combination, so every permutation is covered.
- **Columns** are checked against every permutation separately. The report and the loading window show only the plain combination name. For flexure and axial load each report row shows the governing permutation (a failing one if there is one, otherwise the highest utilization). Shear and joint values are the worst across the permutations.

A workbook extracted before the `Permutation` column existed still runs, but columns are then checked against one row set per combination. Extract again to get the full check.

## Steps 2 to 4: beams

Step 2 filters `FRAME DATA` to beams, classifies each as supported both ends, cantilever, or beam-framed from `CONNECTIVITY`, adds the bar sizes from `OVERWRITES`, and writes the table to `BEAM DESIGN!B8`.

Step 3 designs each beam at the left support, midspan and right support:

- flexure by strain compatibility, including compression steel, minimum steel and crack-control spacing
- shear and torsion, with combined transverse steel and longitudinal torsion steel
- SMRF checks when `F3` is on: reinforcement ratio, moment-strength ratios, probable-moment design shear, hoop spacing
- bar layering (up to three layers), stirrup legs and anchorage of the legs

With `F3` on, the moment-strength ratios of ACI 18.6.3.2 are enforced, not only checked: bars are added until the positive strength at each support face is at least half the negative strength there, and every section has at least a quarter of the largest support strength, top and bottom. The result is in `SMRF moment strength ratio check`; `SMRF steel ratio check` reports the 2.5 % limit. Cantilevers are left out of this step.

The seismic design shear is reported at each end as `Vₑ, left` and `Vₑ, right`: the gravity shear from the `F4` combination plus the sway shear from the probable moments (`Vₛway,max`).

A gravity beam, one with neither end on a column or wall (`Beam-Framed / Floating` in `SupportStatus`), is not part of the moment frame. It is designed for gravity only even when `F3` is on: no probable-moment shear, no seismic hoop spacing, no strength-ratio or 2.5 % checks.

Results replace the table at `BEAM DESIGN!B8`. The sheet is cleared from `B8` to the end of its used range first, so rows from an earlier, longer run cannot remain below the new table. `Design status` reads `OK` or states the failure.

Step 4 writes two files per story to the folder you choose: `<Story>_Girder_Schedule.dxf` for the members on columns (including cantilevers) and `<Story>_Beam_Schedule.dxf` for the gravity beams. A file is left out when a story has no member of that kind.

## Step 5: columns

For each column the designer:

1. lists the bar layouts that fit the section, including bundled bars, in increasing order of steel. A square column only gets layouts with the same bars on all four faces
2. picks the first layout that passes axial and flexure for every load set and both ends, and the transverse detailing rules
3. when `F3` is on, checks strong column-weak beam and joint shear at each joint, and moves a column to a heavier layout if the 6/5 ratio is not met
4. checks column shear in both directions, using the larger of the analysis shear and the capacity-design shear from probable moments

### Cover on the bottom-most story

The button first asks whether the columns of the bottom-most story get 75 mm concrete cover (`earth_contact_cover` in the configuration). The bottom-most story is read from how the columns stand on each other in `CONNECTIVITY`. If yes, a second dialog asks how:

| Choice | Result |
|---|---|
| Enlarge the section | Every face moves out by the extra cover (75 minus the `I12` cover), so each dimension grows by twice that: 800 x 600 with 40 mm cover becomes 870 x 670. The bars keep their position |
| Keep the section size | The bars move inward to the 75 mm cover |

The cover used is reported in `Concrete Cover (mm)` and drawn in the schedule. The ETABS forces are not changed by an enlarged section.

### Vertical bars from level to level

The button then asks how to treat the vertical bars between levels:

| Choice | Result |
|---|---|
| No - design every level on its own | Every column gets the lightest layout that passes |
| Yes - lower levels use at least the bars of the level above | Working from the top level down, a column gets at least as many vertical bars as the column standing on it. A heavier level higher up therefore sets the minimum for every level below it |

Columns are stacked by their shared joints in `CONNECTIVITY`. A column that is changed gets the first passing layout with at least the required number of bars, and every check is run again on it. The `Vertical Bar Continuity` column of the report states what was changed. Closing the dialog cancels the design.

SMRF design reads column local axes and joint coordinates from ETABS, so ETABS must be open with the model.

The report on `COLUMN DESIGN` has one row per column, end and combination, with the top end (`Top (J)`) listed before the bottom end (`Bottom (I)`), grouped as identification, section and bars, flexure and axial, shear, beam-column capacity, joint shear, transverse detailing and overall status. Statuses are `PASS`, `FAIL`, `ERROR` or `BLOCKED` (a check that could not run because data for a framing member is missing); the reason is in the last column.

Values that depend on direction are reported for each section axis, X along the width and Y along the depth:

- `Bars on X Edge` and `Bars on Y Edge`: bars on one face, corner bars counted on both
- `Tie Legs along X Edge` and `Tie Legs along Y Edge`, each with its own alternate-bar support check. Shear along the width is resisted by the legs counted along the Y edge, and the reverse
- strong column-weak beam: beam bars, column strength, beam strength and ratio for the beams framing in along X and along Y. Each axis shows the case with the lowest ratio
- joint shear: demand, capacity and utilization along X and along Y. Each axis shows the case with the highest utilization

A cell without a value states the reason, for example that no beam frames in along that axis.

The loading window shows the column mark, level, end, load combination and current check while the design runs.

Units inside the column designer are N, mm and MPa. ETABS reports compression as negative; the designer converts to compression-positive once, when the forces are read.

## Step 6: column schedule

Asks for the output folder and for the interior tie style, then writes `Column_Schedule.dxf` with one cell per column mark and story: the section drawn to scale with bars, hoops and interior ties, and rows for size, vertical bars, joint ties, confinement ties and general ties. Bars are written as `18-25mmØ` and ties as `12mmØ @ 100mm`. Joint and confinement ties are shown at 100 mm and general ties at 150 mm (set in the configuration); the confinement row shows the designed spacing instead when the design needs less than 100 mm. Circular columns are drawn with a circular outline and spiral.

The drawing uses the exact bar layout the design selected. The report stores it in the `Bar Layout Data (x, y, n)` column as `x,y,count` per bar position (mm from the bottom-left corner of the section; from the centre for circular columns). A report written before that column existed is drawn from the bundle summary instead, which cannot always tell two similar layouts apart, so run the column design again before exporting.

Interior tie style:

| Choice | What is drawn |
|---|---|
| Crossties | One tie per pair of opposite face bars, with a 135-degree hook at each end |
| Closed inner hoops | Closed hoops, each enclosing two neighbouring bar positions on opposite faces. An odd position left over keeps a crosstie |

### Bundled bars

A layout position holds one to four bars. The position is the bar that sits against the tie; the others are placed so the bars touch:

| Bars | At a corner | On a face |
|---|---|---|
| 2 | Second bar on the diagonal, toward the core | Second bar directly behind the first, toward the core |
| 3 | L-shape: one bar along each face | Two along the face, the third behind the bar on the tie's shaft side |
| 4 | 2 x 2 square | 2 x 2 square |

On a circular column, bundles follow the face rules with "toward the core" meaning toward the centre, except that the third bar of a 3-bar bundle sits centred behind the other two, forming a triangle.

Hooks at bundles:

- **One bar, or a corner pair on the diagonal:** the normal 135-degree bend around the bar.
- **Bundle of three or four, or a second bar stacked behind the bar a hoop closes on:** no bend around the first bar. The tie turns with a sharp corner, runs flat past two bars, then bends 45 degrees toward the core. The hook extension leaves the bundle diagonally into the core, 135 degrees from the leg the tie arrived on, and clears every bar of the bundle.

Where bar positions are closer than about 110 mm, a hook extension leaving a corner bundle can reach the bundle at the next position. The drawing shows that overlap as it is; check such sections for congestion.

The design calculation places each bundle as one equivalent bar at the layout position. The drawn bars are offset from that point by up to one bar diameter, which changes the capacity only slightly and is not fed back into the design.

## Calculation reports (PDF)

`EXPORT CALC` on `BEAM DESIGN` and on `COLUMN DESIGN` asks where to save, then writes an A4 PDF from the results on that sheet. Run the design first. The report is one column wide and starts with the design basis and a summary table of all members; each member then has its own section of value tables. Equations are not written out.

Beam report, per beam:

- section and materials
- flexure at the left support, midspan and right support, top and bottom: Mu, bars per layer, As, d, a, strain, phi, Mn, phi Mn and Mu / phi Mn
- shear and torsion per zone: Vu, Ve, design Vu, Tu, d, Vc, legs, spacing, Vs, phi Vn, Vu / phi Vn, At/s and Al
- with `F3` on: nominal moment strengths at each location and the seismic design shear
- detailing and design status

The sheet stores the bars, legs and spacings; depths, strengths and ratios in the report are worked out again from them with the same design classes.

Column report, per column: section and vertical bars, then the governing combination at each end for axial load and flexure, column shear, strong column-weak beam (per axis), then joint shear per axis (it does not depend on the load combination, so none is named), the transverse reinforcement values and the design status.

The reports need a LaTeX install with `pdflatex` (MiKTeX or TeX Live), like the wind and composite reports.

## Column local axes

ETABS places the section depth (`t3`, `Depth` in `FRAME DATA`) along local axis 2 and the width (`t2`, `Width`) along local axis 3. The column designer follows that:

| ETABS force | Acts on the section as |
|---|---|
| M3 | bending with the depth as the lever |
| M2 | bending with the width as the lever |
| V2 | shear along the depth, resisted by the tie legs counted along the X edge |
| V3 | shear along the width, resisted by the tie legs counted along the Y edge |

In the report, X is the width direction (local 3) and Y the depth direction (local 2).

## Limitations

- The biaxial check compares the resultant of M2 and M3 with the section capacity at that moment direction. It is not a full interaction surface.
- Slenderness (second-order moment magnification) is not calculated; the ETABS forces must already include second-order effects.
- Joint checks ignore slab reinforcement. Beams use local-axis angle 0 unless ETABS supplies one.
- Crosstie spacing uses an assumed `hx` from the configuration, not the drawn layout.
- Beam design assumes rectangular sections and uses one bar diameter per beam.
- SMRF confinement, joint classification and anchorage details need an independent check against ACI 318M-14 Chapter 18 before use.

## Tests

`tests/test_beam_designer_aci318.py`, `tests/test_column_designer_aci318.py`, `tests/test_etabs_api_services.py`, `tests/test_frame_tagger.py` and `tests/test_calc_report.py` cover the design engines, the extraction logic, the tagging rules and the report text against published examples, hand calculations and behaviour rules. They run without Excel or ETABS.
