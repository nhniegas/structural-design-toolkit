# Concrete Beam and Column Excel Workflow

How `spreadsheets/beam_column_designer_aci318.xlsm` works with ETABS and the Python design modules. Design code: ACI 318M-14.

## How the pieces fit

- `design/beam_designer_aci318.py`: ETABS extraction, beam design, beam schedule DXF.
- `design/column_designer_aci318.py`: column design, SMRF checks, column schedule DXF.
- `design/aci318_config.py`: every ACI constant used by both designers, each with its clause. The designers read their factors and limits from here.
- `etabs_api/`: ETABS connection (`connection.py`) and table extraction to Excel (`exporter.py`).
- `utilities/_gui_helpers.py`: pickers and the loading window.

The workbook macros call the design modules directly. There is no intermediate `main.py` layer.

## Buttons and what they run

Run them in this order.

| Step | Macro | Python function |
|---|---|---|
| 1. Extract from ETABS | `CallExtractForcesPropertiesCustom` | `beam_designer_aci318.extract_forces_properties_from_etabs` |
| 2. Prepare beam data | `ExtractBeamDesignDataMacro` | `beam_designer_aci318.extract_beam_design_data` |
| 3. Design beams | `TriggerBeamDesign` | `beam_designer_aci318.run_beam_design_from_excel` |
| 4. Export beam DXF | `TriggerExportCADDrawings` | `beam_designer_aci318.export_cad_drawings` |
| 5. Design columns | `TriggerColumnDesign` | `column_designer_aci318.run_column_design_from_excel` |
| 6. Export column DXF | `TriggerExportColumnCAD` | `column_designer_aci318.export_column_cad_drawings` |

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
| `F3` | SMRF (seismic) design on or off, for beams and columns |
| `F4` | Factored gravity combination used for beam seismic shear |
| `I4` to `I8` | Beam main bar, stirrup bar, web bar diameters (mm), web bar fyw (MPa), cover (mm) |
| `I10` to `I12` | Column main bar, tie bar diameters (mm), cover (mm) |

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
- SMRF checks when `F3` is on: reinforcement ratio, moment-capacity ratios, probable-moment sway shear, hoop spacing
- bar layering (up to three layers), stirrup legs and anchorage of the legs

Results replace the table at `BEAM DESIGN!B8`. The sheet is cleared from `B8` to the end of its used range first, so rows from an earlier, longer run cannot remain below the new table. `Design status` reads `OK` or states the failure.

Step 4 writes one `<Story>_Beam_Schedule.dxf` per story to the folder you choose.

## Step 5: columns

For each column the designer:

1. lists the bar layouts that fit the section, including bundled bars, in increasing order of steel
2. picks the first layout that passes axial and flexure for every load set and both ends, and the transverse detailing rules
3. when `F3` is on, checks strong column-weak beam and joint shear at each joint, and moves a column to a heavier layout if the 6/5 ratio is not met
4. checks column shear in both directions, using the larger of the analysis shear and the capacity-design shear from probable moments

SMRF design reads column local axes and joint coordinates from ETABS, so ETABS must be open with the model.

The report on `COLUMN DESIGN` has one row per column, end (I or J) and combination, grouped as identification, section and bars, flexure and axial, shear, beam-column capacity, joint shear, transverse detailing and overall status. Statuses are `PASS`, `FAIL`, `ERROR` or `BLOCKED` (a check that could not run because data for a framing member is missing); the reason is in the last column.

The loading window shows the column mark, level, end, load combination and current check while the design runs.

Units inside the column designer are N, mm and MPa. ETABS reports compression as negative; the designer converts to compression-positive once, when the forces are read.

## Step 6: column schedule

Asks for the output folder and for the interior tie style, then writes `Column_Schedule.dxf` with one cell per column mark and story: the section drawn to scale with bars, hoops and interior ties, and rows for size, vertical bars, joint ties, confinement ties and general ties. Circular columns are drawn with a circular outline and spiral.

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

## Limitations

- The biaxial check compares the resultant of M2 and M3 with the section capacity at that moment direction. It is not a full interaction surface.
- Slenderness (second-order moment magnification) is not calculated; the ETABS forces must already include second-order effects.
- Joint checks ignore slab reinforcement. Beams use local-axis angle 0 unless ETABS supplies one.
- Crosstie spacing uses an assumed `hx` from the configuration, not the drawn layout.
- Beam design assumes rectangular sections and uses one bar diameter per beam.
- SMRF confinement, joint classification and anchorage details need an independent check against ACI 318M-14 Chapter 18 before use.

## Tests

`tests/test_beam_designer_aci318.py`, `tests/test_column_designer_aci318.py` and `tests/test_etabs_api_services.py` cover the design engines and the extraction logic against published examples, hand calculations and behaviour rules. They run without Excel or ETABS.
