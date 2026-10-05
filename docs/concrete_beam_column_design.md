# Concrete Beam and Column Design

Beam and column design of an ETABS model from the terminal, ACI 318M-14. No spreadsheet is used: the commands ask their inputs in dialogs and save the results (`.xlsx`), the calculation reports (`.pdf`) and the schedules (`.dxf`) in the folder you choose.

## How to use it

Open the model in ETABS (saved, analysed or not), then from the project folder:

```powershell
sdt beams      # extract, design the beams, save the beam outputs
sdt deflection # only the deflections of the beams (bars of the last sdt beams)
sdt columns    # design the columns from the stored beam step, save the column outputs
```

(`python main.py beams` and so on does the same). Design the beams first: the SMRF joint checks of the columns use the designed beam bars.

**`sdt beams` asks:**

| Question | Notes |
|---|---|
| Seismic combinations | Static (EQ), response spectrum (RSA) or both. Gravity and wind `ULS` combinations are always included |
| Live load reduction, tributary method, pattern live load | See [Extraction](#extraction) |
| SMRF (seismic) design | One setting for the beams and the columns. Gravity beams (on other beams only) are designed for gravity in any case |
| Gravity combination for the beam seismic shear | Only with SMRF; the combinations without seismic or wind terms are offered. The shear Ve uses the factored gravity load 1.2 D + f1 L (ACI 318-14 18.6.5.1, NSCP 2015 203.3), so the combination closest to it is selected; a combination without live load, such as 1.4 D, leaves the live load out |
| Beam bars and cover | Main bar, stirrup and web bar (mm), web bar fy (MPa), cover (mm) |
| Cover against earth | Whether the beams of any floor level need 75 mm cover (cast against or exposed to earth: footing tie beams, ground beams). If yes, pick the levels; the other beams keep the typed cover. Not asked when the typed cover is 75 mm or more. The levels are remembered and offered again |
| Deflection limit | Partitions likely to be damaged (L/480) or not (L/240) |
| Target ratios | Use the code limits (every ratio 1.00), the targets saved for this model, or set them: one dialog for girders and one for beams, with a box for each check. See [Target ratios](#target-ratios) |
| Beam torsion | Design for the analysis torsion, or take it as at most φTcr (compatibility torsion, ACI 22.7.3.2). See [Beam torsion](#beam-torsion) |
| Beam bar spacing | The office rule (at most 150 mm clear between the bars of a face), or the code only (crack control, ACI 24.3.2). See [Beam bar spacing](#beam-bar-spacing) |
| Depth of a beam that carries others | Whether a girder or beam must be at least as deep as the beams that frame into it. See [Carrier depth](#carrier-depth) |
| Output folder | For the results, the calculations and the schedules |

**`sdt deflection`** checks only the deflections, apart from the strength design: it takes the bars of the last `sdt beams` of the model, reads the service moments again from ETABS (so a re-analysed model is checked with its current moments), asks the limit (L/480 or L/240) and the folder, and saves `<model> - Deflection.xlsx` with one row per beam: section, Ie, λΔ, the three deflections, their limits, the governing ratio and the check. It stops with a message when the model has no beam design yet (the cracked stiffness needs the bars).

**`sdt columns` asks:** column main bar, tie and cover; the bottom-story cover; the vertical bars carried down; with SMRF on, whether the capacity-design checks are run at the topmost level and at the foundation level (see [Levels where the capacity-design checks may be left out](#levels-where-the-capacity-design-checks-may-be-left-out)); how interior ties are drawn; the output folder.

Answers are remembered and offered again next time. Every named member (not a number) is designed.

**Files in the output folder:**

| File | From |
|---|---|
| `<model> - Beam Design.xlsx` | The beam results, two rows (top, bottom) per beam |
| `<model> - Beam Calculations.pdf` | Beam calculation report |
| `<model> - Deflection.xlsx` | `sdt deflection`: the deflection checks, one row per beam |
| `<Story>_Girder_Schedule.dxf`, `<Story>_Beam_Schedule.dxf` | Beam schedules |
| `<model> - Column Design.xlsx` | The column report, one row per column, end and combination |
| `<model> - Column Calculations.pdf` | Column calculation report |
| `Column_Schedule.dxf` | Column schedule with the reinforced sections |
| `<model> - Beam Design summary.txt`, `<model> - Column Design summary.txt` | The closing summary of the run, as shown in its window |

**Closing summary.** When a command finishes, a window opens with its summary and stays until you close it (OK, Enter or Escape): the model, the counts, what needs attention grouped by reason and shown in red (for example `Column shear: GF-C1, GF-C2`), any choice that left a check out, and the files written. The text can be selected and copied. The terminal is for the detailed results; `sdt beams` and `sdt columns`, whose detail is in their result files, print the same summary there.

**Numbers and blanks in the workbooks.** Values are rounded to two decimals; the small steel ratios (ρ, the 18.7.5 confinement ratios, Ash/(s bc)) keep four, since two would round them to 0.01. No cell is left empty: a value that does not apply is written as `N/A` with the reason, for example `N/A - rectangular hoops: 18.7.5(a) to (c) apply`, `N/A - circular column` or `N/A - not a roof beam`. The beam workbook leaves out the ETABS column-section fields that are never filled for a beam.

**Between the two steps** everything is kept next to the model in `<model> - design data.pkl`: the extracted tables (forces, service loads, frame data, connectivity, live load reduction, column orientation), the inputs and the results. It is internal (binary, for speed). `sdt columns` finds it from the model open in ETABS and stops with "No beam design for <model>. Run sdt beams first." when there is none. When the model file was saved after the beam step, it warns that the stored forces may be out of date and lets you stop or continue.

## How the pieces fit

- `design/concrete_workflow.py`: the commands, the dialogs and the store.
- `design/beam_designer_aci318.py`: beam design (`design_beams`), results file, beam schedule DXF and calculation report.
- `design/beam_deflection.py`: deflection checks.
- `design/column_designer_aci318.py`: column design and SMRF checks (`design_columns`), results file, column schedule DXF and calculation report.
- `design/code_config.py`: every code value, each with its clause: ACI 318M-14 (`CODE`, used by both designers) and NSCP 2015 chapter 2 (`NSCP`: loads, live load reduction, earthquake).
- `etabs_api/workflows/exporter.py`, `analysis_forces.py`, `tributary.py`: the extraction and the factored forces from the analysis results.
- `utilities/_gui_helpers.py`: dialogs and the loading window; `utilities/_calc_report.py`: the PDF layout.

Defining the materials, sections, loads and combinations of the ETABS model is a separate step, done before: see [ETABS model setup](etabs_model_setup.md). To size the members automatically, see the [design loop](etabs_design_loop.md).

## Optional: automatic tagging

`sdt tag` (or `python main.py tag`) gives every beam and column in the open ETABS model a unique name: beams as `<level><type>-<number><letter>`, for example `2GX-10B`, and columns as `<level>-<type><number><letter>`, for example `3-C5C`. The extraction only reads named members, so tag the model before `sdt beams`.

`sdt tag` asks where the tags go: into a copy saved beside the model as `<model name> - TAGGED.EDB` (the default; the original file is not changed and ETABS has the tagged copy open afterwards), or into the model itself, which is then saved with the new names.

| Part | Rule |
|---|---|
| Level | A story named as a number and `F` drops the `F` (`2F` gives `2`). For every other story a dialog asks for the prefix; a level left blank is skipped |
| Type | `G` if at least one end is on a column, otherwise `B`. `X` if the member is within 45 degrees of the global X axis, otherwise `Y`. Columns are `C`; planted columns are `PC` |
| Beam line | Members that share a joint continue one line while the bend is 45 degrees or less; the straightest pair is joined. A line keeps its number across an opening when the next member is straight ahead of it |
| Beam number | Counted per level and per type. X lines top to bottom, Y lines left to right, by the start of the line |
| Letter | Along the line, left to right for X and bottom to top for Y: none, `A`, `B`, ... without `I` and `O`, then `AA`, `AB`, ... |
| Column number and letter | Counted once over the combined plan of all levels, so a column keeps them on every level. A row is the columns along one X girder line; rows run top to bottom and letters left to right. A column takes the level of its upper joint |

A planted column is a stack that does not reach a supported joint. Planted columns are tagged `PC` by the same rule as `C`, with their own numbers starting at 1 (`PD2-PC1`, `PD2-PC1A`). In the column schedule the mark drops the level, so `2-C5A` is `C5A` and `PD2-PC1A` is `PC1A`. A stack with no column on a tagged level is not counted. A new name already used by a member that is not being tagged is skipped and counted in the closing message.

## Extraction

`sdt beams` attaches to the model open in ETABS and reads every table the design needs.

ETABS concrete design is **not** run. The design forces are built from the analysis results of each load case (`etabs_api/workflows/analysis_forces.py`), so every number can be traced. The analysis is run first if any case needed has no results.

The two questions:

| Question | Yes means |
|---|---|
| Reduce the reducible live load? | Each member's reducible live load (patterns of type Reducible Live) is multiplied by its own factor. Further questions choose the rule (NSCP 2015 205.5 or ASCE 7-10 4.7) and the tributary area method. See below |
| Include pattern live load on the beams (ACI 318-14 6.4.2)? | Each beam span is also designed with its live load as a simple span (the largest sagging moment) and with fixed ends (the largest hogging moment), times the factor you type (default 0.75). A released end stays pinned. Cantilevers are left as analysed. Columns are not patterned |

What is read:

- **Element forces** (`Element Forces - Beams`, `Element Forces - Columns`) for the load cases in the chosen combinations. The stations are those ETABS reports: they start and end at the faces of the end offsets.
- **Load Combination Definitions**: each combination is expanded to its load cases with their factors, including combinations used inside combinations. Envelope combinations cannot be picked: they are not one set of forces.
- **Everything else** (frame assignments, section definitions, reinforcing, material properties, connectivity) is read once.

The combination of the case results reproduces the ETABS combination results exactly; this was checked on the test model for gravity, wind, static seismic and response spectrum combinations.

Members with a purely numeric ETABS name are skipped by default; only named members are designed. When members are untagged, the command asks whether to tag them, design them with their ETABS numbers, or leave them out.

**A model with other names.** When no combination is named `ULS`, you pick the strength combinations from the model's own; when the `DEF` deflection combinations are missing, you pick an existing combination for each or let the toolkit add it. A dialog first lists what was found and what is missing. The summary ends with what was read from the model, what you answered and what was assumed. `sdt columns` uses the forces stored by `sdt beams`, so it carries the same list. See [Working on a model the toolkit did not set up](existing_models.md).

Units: every command works in N and mm, whatever units the model was created in. When a command attaches to ETABS it sets the model's units to N-mm, and it puts the model's own units back when it ends (also when it stops with an error). So a model in kN-m gives the same results as the same model in N-mm. These are the units of the API session: the units the ETABS window displays, and the model file, are not affected. Forces are then written to Excel in kN and kN-m, dimensions in mm, strengths in MPa.

### Load-combination permutations

One combination can stand for several sets of forces. `FACTORED LOADS` stores the combination name in `Combo` and the set number in `Permutation`.

| Source | Columns | Beams |
|---|---|---|
| Response spectrum case in the combination | 8 permutations: + and - of the spectrum part on P, M2 and M3 (V2 follows M3, V3 follows M2, T follows P) | 2: the maximum and the minimum of every force |
| Load case with several steps (the ASCE 7 wind cases: one step per wind load case) | One permutation per step (12 for `WX`) | 2: maximum and minimum |
| Pattern live load (when chosen) | none | 2: maximum and minimum over the analysed and patterned live load |
| Otherwise | 1 | 1 |

- **Beams** take the envelope of all rows of a combination, so the maximum and minimum rows carry everything.
- **Columns** are checked against every permutation separately. The report and the loading window show only the plain combination name. For flexure and axial load each report row shows the governing permutation (a failing one if there is one, otherwise the highest utilization). Shear and joint values are the worst across the permutations.

### Live load reduction

A dialog asks for the rule. NSCP 2015 governs; ASCE 7 is the alternate it allows (NSCP 205.6, and 405.2.3).

**NSCP 2015 205.5.** R = 0.86 (A - 14) percent, with A the tributary area in m2. NSCP prints R = 0.08 (A - 15), which is the UBC 97 rate per ft2; 0.86 per m2 above 14 m2 is its metric form. R is at most:

- 40 % for beams and for columns that receive load from one level only, 60 % for other columns;
- 23.1 (1 + D/L) %, with D and L the member's dead and live load from the analysis.

**ASCE 7-10 4.7** (the same in 7-16). L = Lo (0.25 + 4.57 / sqrt(K_LL A_T)), where K_LL is 4 for columns and 2 for beams (interior members, and edge members without cantilever slabs). It applies only when K_LL A_T is at least 37.16 m2. L is at least 0.50 Lo for beams and one-level columns, and 0.40 Lo for other columns.

**Both rules.** A member supporting reducible live load above 4.8 kPa (more than 0.5 m2 of such floor) gets no reduction. The exception is a column receiving load from more than one level, which gets 20 %; under ASCE, never more than its 4.7.2 reduction.

Only the patterns of type Reducible Live are reduced. The `LIVE LOAD REDUCTION` results list the rule used for each member. Every value is in `design/code_config.py` (`NSCP.live_load_reduction`).

**Tributary area.** When reduction is chosen, a third question asks how to find A:

| Method | What A is | Use |
|---|---|---|
| Geometric (default) | The floor closer to the member than to its neighbours (the halfway lines): for a column, the floor nearest it at each level, summed over the floors it supports; for a beam, the floor nearest it, plus half of every beam that frames into it away from a column. Sampled on a 200 mm grid from the floor objects | The NSCP definition, and the method of ETABS's own live load reduction. Depends on the plan only, so it does not change when members are resized |
| Analysis load share | The load the member takes from a 1 kPa load on every floor, in m2. The first extraction adds the load pattern `TRIBUTARY UNIT` (type Other: in no combination and not in the mass source) | Follows the real load path: continuity, frame action and stiffness. It differs from the geometric area, larger at some interior members and smaller, even near zero, at members that the frame unloads |

On the test model the two agree within about 10 % at most columns; the largest differences were an interior column at 98 m2 geometric against 66 m2 load share, and a corner column at 5.9 m2 geometric against 1.0 m2 load share (the neighbouring spans lift it).

**Floor loads.** The reducible live intensity of each floor is the sum of its uniform loads on Reducible Live patterns, from load sets (`Shell Uniform Load Sets`) and from loads assigned directly by pattern (`Area Load Assignments - Uniform`). The intensity listed for a member is the average over its tributary area. Reducible live load on a frame (a line load) is not part of any floor and is not seen.

**Warning.** When a load set or a floor puts more than 4.8 kPa on a Reducible Live pattern, the extraction prints it: NSCP allows no reduction above 4.8 kPa, so the load probably belongs on a non-reducible live pattern.

The table `LIVE LOAD REDUCTION` (in the store) lists per member the method, the tributary area, the reducible live intensity, the area above 4.8 kPa, the number of levels, the reduction, which limit governs and the factor applied.

## Beam design

The beams of `FRAME DATA` are classified as supported both ends, cantilever, or beam-framed from `CONNECTIVITY`, and get the bar sizes you typed.

Each beam is designed at the left support, midspan and right support:

- flexure by strain compatibility, including compression steel, minimum steel and crack-control spacing
- shear and torsion, with combined transverse steel and longitudinal torsion steel
- SMRF checks when SMRF is on: reinforcement ratio, moment-strength ratios, probable-moment design shear, hoop spacing
- bar layering (up to three layers), stirrup legs and anchorage of the legs

With SMRF on, the moment-strength ratios of ACI 18.6.3.2 are enforced, not only checked: bars are added until the positive strength at each support face is at least half the negative strength there, and every section has at least a quarter of the largest support strength, top and bottom. The result is in `SMRF moment strength ratio check`; `SMRF steel ratio check` reports the 2.5 % limit. Cantilevers are left out of this step.

The seismic design shear is reported at each end as `Vₑ, left` and `Vₑ, right`: the gravity shear from the gravity combination you chose plus the sway shear from the probable moments (`Vₛway,max`), over the clear span between the column faces (the joint-to-joint length less half of the larger side of the column at each end). The sway shear acts along the whole span, so the stirrups beyond 2h carry it too, with the gravity shear there and Vc.

A shear larger than phi (Vc + 0.66 √f'c bw d) (ACI 22.5.1.2), or a combined shear and torsion stress above ACI 22.7.7.1, fails the beam with `FAILED: SHEAR STRENGTH`: the section is too small, and more stirrup legs cannot fix it. Torsion steel uses fy and fyt of at most 420 MPa (ACI 20.2.2.4).

**Support status.** Each beam is classified by what holds its two ends:

- An end is on a support when a column or a wall is at its joint.
- An end that another beam carries is supported too: a beam across it at the joint, or a girder whose centre line passes through the joint, also when ETABS has not split that girder there.
- A beam that only continues in line does not hold an end by itself. The end is as held as the far end of that beam, so a cantilever that ETABS has in two pieces is still a cantilever.

`Supported Both Ends` is a member on a column or wall at one end at least and held at the other. `Cantilever (Free at PtI / PtJ)` is on a column or wall at one end and truly free at the other. Before version 0.3.1 a beam from a column to a girder was taken as a cantilever, with a short clear span and a capacity shear far too large.

A gravity beam, one with neither end on a column or wall (`Beam-Framed / Floating` in `SupportStatus`), is not part of the moment frame. It is designed for gravity only even when SMRF is on: no probable-moment shear, no seismic hoop spacing, no strength-ratio or 2.5 % checks.


### Deflection (ACI 318M-14 24.2)

The beam design also checks deflection; `sdt beams` asks once whether the beams support partitions or finishes likely to be damaged (L/480, or L/240 when not; remembered for next time).

`SERVICE LOADS` holds, for every beam, M3 and V2 at the stations of the deflection combinations `DEF 100 1.0 DL`, `DEF 101 1.0 DL + 1.0 LL`, `DEF 102 1.0 DL + 0.25 LL` and `DEF 103 1.0 DL + 1.0 Lr` (no live load reduction, no pattern live load). The extraction adds these combinations to a model that does not have them; adding combinations keeps the analysis results. Each row also has the downward tip deflection caused by the rotation of the beam's I end and of its J end, used for cantilevers, and whether the beam is on the roof level (the topmost story with beams).

For each beam, after its bars are chosen:

1. In each zone (left end, midspan, right end): Ec = 4700 √f'c, Ig = b h³/12, fr = 0.62 √f'c, Mcr = fr Ig / (h/2), and Icr of the cracked transformed section with the zone's bars; the tension face follows the sign of the moment.
2. Ie (Eq. 24.2.3.5a) at Ma = the largest `DEF 101` moment of the zone. The same Ie is used for every load: cracked under the full service load, the beam stays cracked.
3. The curvature M / (Ec Ie) is integrated twice along the span, with zero deflection at the supports.
4. λΔ = 2.0 / (1 + 50 ρ'), with ρ' the compression steel at midspan (at the support of a cantilever).

| Check | Deflection | Limit |
|---|---|---|
| Immediate live load | Δ(DL + LL) - Δ(DL) | L/360 |
| Immediate roof live load (roof level) | Δ(DL + Lr) - Δ(DL) | L/180 |
| After partitions are installed | λΔ Δ(DL + 0.25 LL) + Δ(DL + LL) - Δ(DL + 0.25 LL) | L/480 or L/240 |

**Spans, not segments.** ETABS splits a beam line wherever another member frames into it. The segments of one tagged line (`2GX-1`, `2GX-1A`, `2GX-1B`, ...) that meet at a joint without a column are checked together as one span, and L is the whole span. A span end is supported by a column, a wall or a member the line ends on; it is free (a cantilever tip) only when nothing else connects there. A cantilever span is fixed at its support, and the deflection from the rotation of that support (from the analysis) is added.

The results file has Ie of each zone, λΔ, the three deflections, their limits, the governing ratio and `Deflection check`. A beam that fails the deflection and nothing else gets `FAILED: DEFLECTION (ACI 24.2.2)`. The calculation report has the same values in a Deflection table for each beam.

### Target ratios

The code passes a check when demand / capacity is at most 1.00. A target ratio below that is a margin you choose, as engineering judgement, by member type and check:

| Member type | Checks with a target |
|---|---|
| Girders (frame into a column) | Flexure Mu / φMn; shear and torsion Vu / φVn; deflection / limit |
| Beams (carried by other beams) | Flexure; shear and torsion; deflection |
| Columns | Axial load and bending (P-M) Mu / φMn; shear Vu / φVn; joint shear Vj / φVn; strong column - weak beam, as a minimum ΣMnc / ΣMnb of at least 1.2 |

How a target is applied:

- **The members are designed to it.** With a flexure target of 0.85 the bars are chosen for Mu / 0.85, so Mu / φMn is at most 0.85; with a shear target the stirrups or ties are chosen the same way. The demands in the results are the real ones, not the divided ones.
- **A check passes at or below its target**, and fails above it even when it is below 1.00. The failure text says it is the target that is exceeded.
- A target is between 0.30 and 1.00. It cannot be looser than the code. The strong column ratio cannot be below 1.2.
- Detailing rules (bar spacing, minimum steel, bar fit, SMRF steel limits) have no ratio and are not changed.
- The targets are saved with the model, offered again the next time, and listed in the summary, in the result workbook (beams) and in the calculation reports.

A flexure target below 1 on girders adds beam bars. More beam steel raises the probable moments, so the column capacity shear Ve and the joint shear demand rise with it; the summary says so when such a target is set.

`sdt columns` asks the column targets; `sdt deflection` uses the targets of the beam design.

### Beam torsion

ETABS gives the torsion the elastic model attracts. With the full torsional stiffness of the beams (J modifier 1), beams that frame into each other pick up compatibility torsion several times their cracking torsion, and the section check of ACI 22.7.7.1 fails on beams that a real design would accept. There are two ways to deal with it:

- **In the model:** reduce the torsional stiffness of the beams (a J modifier such as 0.01, as `sdt setup` assigns). The analysis then redistributes the torsion into bending of the slab and the adjoining beams. `sdt check` warns when beams keep the full J.
- **In the design:** answer "At most φTcr" to the torsion question. ACI 22.7.3.2 allows it where the torsion can redistribute after cracking. The torsion in the results stays the analysis value; the "Torsion designed for" column and the calculation report say which was used. ACI 22.7.3.3 then requires the adjoining members to be designed for the redistributed moments and shears, which this option does not give you: only a model with reduced J does.

A cantilever keeps its analysis torsion with either choice, since nothing else can take it. The default is the analysis torsion.

### Beam bar spacing

A beam face needs enough bars to keep them close together. There are two rules for how close:

- **Office rule (the default):** at most 150 mm clear between the bars of a face. A wide beam then gets more bars than its strength needs.
- **Code only:** the crack control spacing of ACI 24.3.2, about 250 mm centre to centre for Grade 414 bars with 40 mm cover.

The choice matters for seismic design. The extra bars of the office rule raise the probable moments of the beam, and with them the capacity shear Ve of the beam and the joint shear and capacity shear of the columns. On a wide beam with a short span this can be the whole reason the shear check fails. The summary and the calculation report say which rule was used.

### Carrier depth

Where a girder is shallower than a beam it carries, the bottom bars of that beam pass below the girder's bottom bars and cannot rest on them. When you answer yes to "should a girder or beam be at least as deep as the beams that frame into it", every carrier is checked:

- The carrier of a beam end is the beam whose centre line passes through it, whether ETABS has that girder as one member from column to column or as pieces that meet at the joint. Beams that only continue each other are not carriers.
- A carrier shallower than a beam it carries gets `FAILED: DEPTH BELOW THE BEAM IT CARRIES`, with both depths in the "Depth against the beams it carries" column.
- It is a detailing rule of your own, not a code clause, so it is off unless you ask for it. It needs the joint coordinates, which the extraction provides.

### Cover against earth

Beams cast against or permanently exposed to earth need 75 mm cover (ACI Table 20.6.1.3.1; `earth_contact_cover` in `BeamDetailingConfig`). `sdt beams` asks which floor levels have such beams and designs them with 75 mm, the others with the typed cover. The cover of each beam is in its `cc` value and in the report. `sdt design` asks the same question once and uses the answer in every iteration.

The beam schedules are two files per story: `<Story>_Girder_Schedule.dxf` for the members on columns (including cantilevers) and `<Story>_Beam_Schedule.dxf` for the gravity beams. A file is left out when a story has no member of that kind.

## Column design

For each column the designer:

1. lists the bar layouts that fit the section, including bundled bars, in increasing order of steel. Bars keep a clear spacing of at least 40 mm, 1.5 db and 4/3 of the aggregate (NSCP 425.2.3; a bundle counts as one bar of the same area). A square column only gets layouts with the same bars on all four faces
2. picks the first layout that passes axial and flexure for every load set and both ends, and the transverse detailing rules. The moments are first magnified for slenderness along the column (see [Slenderness](#slenderness))
3. with SMRF on, checks strong column-weak beam and joint shear at each joint, and moves a column to a heavier layout if the 6/5 ratio is not met. The 6/5 rule is waived where the column stops at the joint and Pu < 0.1 Ag f'c (ACI 18.7.3.1). The joint also needs a column side of at least 20 db of the beam bars passing through it (18.8.2.3) and a depth of at least half the deepest beam (18.8.2.4); a violation fails the joint shear check and shows in its utilization
4. checks column shear in both directions. Ordinary columns use the analysis shear and Vc of ACI 22.5.6 / 22.5.7 with the axial load of each end (tension lowers it). SMRF columns use the larger of the analysis shear and the capacity-design shear Ve = (Mpr,top + Mpr,bottom) / lu, with lu the clear height: the joint-to-joint length less the deepest beam at the top joint (never more than the span of the ETABS force stations, which stop at the rigid end zones), and each Mpr no more than the beams' Mpr at that joint shared among its columns (ACI 18.7.6.1.1). Vs is limited to 0.66 √f'c b d: beyond it the column fails shear instead of getting more legs. Ties are at most d/2 apart (d/4 for a large Vs) once shear steel is needed, with at least Av,min

### Slenderness

Second-order effects have two parts, and both are covered:

| Effect | Where it comes from |
|---|---|
| Sway of the storeys (P-Δ) | The ETABS analysis. `sdt setup` defines iterative P-delta on the gravity loads and the frames carry cracked stiffness, which is an elastic second-order analysis (ACI 6.7). The forces read from ETABS already hold it, so no sway magnifier is added. `sdt check` reports when P-delta is off |
| Bow of the column between its ends (P-δ) | Added by `sdt columns`, by the moment magnification of ACI 6.6.4.5 (`design/column_slenderness.py`). ETABS does not capture it unless every column is subdivided |

For each column, each bending axis and each combination:

1. **k lu / r.** `lu` is the clear height in that direction: from brace to brace less the beam at the top. A joint braces a direction when a beam frames in along it; a joint with no beam that way is passed over, so a footing stub, a double-height space or a level with beams one way only gets the longer length. `r` is 0.30 h (0.25 D for a circular column). `k` comes from the alignment chart of a braced column in the form of ACI R6.2.5, the smaller of 0.7 + 0.05 (ψA + ψB) and 0.85 + 0.05 ψmin, at most 1.0, with ψ = Σ(EI/l) of the columns over Σ(EI/l) of the beams along that direction, on cracked stiffness (0.70 Ig columns, 0.35 Ig beams). A beam that ETABS split at a secondary beam counts as one span up to the next column. A footing takes the support of the ETABS model at that joint: ψ = 1.0 when the rotation in the bending direction is restrained (fixed base), ψ = 10 when it is not (pinned base, ACI R6.2.5), read for each axis from the joint restraints. A footing joint with no support in the model is taken as pinned. The results and the calculation report state the base of each column that stands on a support. Without joint coordinates, `lu` is the joint to joint length less the top beam and k = 1.0.
2. **Neglected** when k lu / r ≤ 34 + 12 (M1/M2), at most 40. M1/M2 is negative in single curvature; end moments of the same sign in the ETABS moment diagram are single curvature.
3. **Otherwise** Pc = π² (EI)eff / (k lu)², with (EI)eff = 0.4 Ec Ig / (1 + βdns), Cm = 0.6 − 0.4 (M1/M2), δns = Cm / (1 − Pu / 0.75 Pc) ≥ 1 and Mc = δns M2, with M2 at least Pu (15 + 0.03 h). The larger end moment of the combination is replaced by Mc before the layout is checked on the interaction surface.
4. **Fails** when Pu ≥ 0.75 Pc or δns > 1.4 (ACI 6.2.6): the column is too slender for its load, which bars cannot fix. `sdt design` then enlarges it.

- **βdns** is the dead-load share of Pu in each combination, taken from the load cases when the forces are extracted. Forces extracted before this was added use 0.6, and the report says `assumed`.
- **Response spectrum combinations** have end moments without sign, so single curvature is taken (Cm = 1.0, limit 22).
- **The minimum moment** is applied about both axes at once when both need it. ACI applies it about each axis separately, so this is on the safe side.

The results have a SLENDERNESS group per end: `lu`, `k`, `k lu/r`, the limit, Cm, Pc, δns and M2,min about each axis, βdns, the moments from the analysis, and the check. `Mu2 design` and `Mu3 design` in the flexure group are the magnified moments. A column that is not slender shows `Not slender` in the cells that do not apply and δns = 1. For a building with ordinary storey heights most columns are not slender and their design does not change.

### Levels where the capacity-design checks may be left out

With SMRF on, `sdt columns` asks two questions. Both default to running every check.

| Level | What may be left out | Why it tends to fail |
|---|---|---|
| Topmost level (joints with no column above) | Strong column-weak beam (BCC) and joint shear at those joints | One column alone must be 1.2 times stronger than the beams. The code already waives the 6/5 rule where the column stops at the joint and Pu < 0.1 Ag f'c (ACI 18.7.3.1); this choice also leaves out the joint shear there |
| Foundation level (the bottom-most story columns) | BCC and joint shear at their joints, and the probable-moment shear Ve of those columns, which are then designed for the analysis shear (Vc still taken as zero) | The columns are short. Ve = ΣMpr / lu, and with a clear height of a few hundred millimetres it is several times what the section can carry. A larger section does not help: Mpr grows with the section about as fast as the shear strength does, so the utilization stays the same |

What is left out is written in the cells (`Not checked - topmost level (user choice)`, `Not used - foundation level (user choice): analysis shear`), in the closing summary, and is never counted as a failure. Leaving a check out is an engineering decision: the short column between a footing and a tie beam is closer to a pedestal than to a flexural column, and its shear should then be judged on the forces it really takes. `sdt design` asks the same two questions and applies them in every iteration, so those levels stop driving the column sizes.

### Cover on the bottom-most story

`sdt columns` first asks whether the columns of the bottom-most story get 75 mm concrete cover (`earth_contact_cover` in the configuration). The bottom-most story is read from how the columns stand on each other in `CONNECTIVITY`. If yes, a second dialog asks how:

| Choice | Result |
|---|---|
| Enlarge the section | Every face moves out by the extra cover (75 minus the column cover), so each dimension grows by twice that: 800 x 600 with 40 mm cover becomes 870 x 670. The bars keep their position |
| Keep the section size | The bars move inward to the 75 mm cover |

The cover used is reported in `Concrete Cover (mm)` and drawn in the schedule. The ETABS forces are not changed by an enlarged section.

### Vertical bars from level to level

It then asks how to treat the vertical bars between levels:

| Choice | Result |
|---|---|
| No - design every level on its own | Every column gets the lightest layout that passes |
| Yes - lower levels use at least the bars of the level above | Working from the top level down, a column gets at least as many vertical bars as the column standing on it. A heavier level higher up therefore sets the minimum for every level below it |

Columns are stacked by their shared joints in `CONNECTIVITY`. A column that is changed gets the first passing layout with at least the required number of bars, and every check is run again on it. The `Vertical Bar Continuity` column of the report states what was changed. Closing the dialog cancels the design.

SMRF design reads column local axes and joint coordinates from ETABS, so ETABS must be open with the model.

The column results file has one row per column, end and combination, with the top end (`Top (J)`) listed before the bottom end (`Bottom (I)`), grouped as identification, section and bars, flexure and axial, slenderness, shear, beam-column capacity, joint shear, transverse detailing and overall status. Statuses are `PASS`, `FAIL`, `ERROR` or `BLOCKED` (a check that could not run because data for a framing member is missing); the reason is in the last column.

Values that depend on direction are reported for each section axis, X along the width and Y along the depth:

- `Bars on X Edge` and `Bars on Y Edge`: bars on one face, corner bars counted on both
- `Tie Legs along X Edge` and `Tie Legs along Y Edge`, each with its own alternate-bar support check. Shear along the width is resisted by the legs counted along the Y edge, and the reverse
- strong column-weak beam: beam bars, column strength, beam strength and ratio for the beams framing in along X and along Y. Each axis shows the case with the lowest ratio
- joint shear: demand, capacity and utilization along X and along Y. Each axis shows the case with the highest utilization

A cell without a value states the reason, for example that no beam frames in along that axis, that the joint is checked in the combinations that govern it (only those are evaluated, so the other combination rows of that end point to them), or that the check was left out at that level.

The loading window shows the column mark, level, end, load combination and current check while the design runs.

Units inside the column designer are N, mm and MPa. ETABS reports compression as negative; the designer converts to compression-positive once, when the forces are read.

## Column schedule

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

## Column strength: the biaxial interaction surface

Axial load and biaxial bending are checked on the ACI 318M-14 design interaction surface (phi Pn, phi Mnx, phi Mny) of each layout, in `design/column_interaction.py`:

- **The surface.** It is computed once per layout (shape, size, bars, cover, materials) and reused by every column, combination and iteration with that layout. The grid is 72 neutral-axis angles × 120 depths, using strain compatibility, the Whitney block and elastic-plastic bars (the same model as the exact solver it replaced). phi at each point comes from that point's steel strain, and the axial cap is phi 0.80 Po (tied) or phi 0.85 Po (spiral). The values match the exact solver; where they differ (at most about 0.1 %), the surface is on the safe side.
- **A demand (Pu, Mu2, Mu3).** The surface is cut where phi Pn = Pu, and the capacity is read along the demand's moment direction. Utilization is |Mu| / phi Mn. Before October 2026, phi was applied to Mn at Pn = Pu, which overstated the capacity above the balance point.
- **Bar search and the convex hull.** The demands of a column are checked first at the vertices of their convex hull, so a layout that cannot work is rejected after a few checks. The other demands are then confirmed, because the phi-scaled surface is not strictly convex where phi changes from 0.65 to 0.90. The final layout is reported for every combination at both ends.
- **Strong column - weak beam.** It uses the nominal surface (phi = 1) at the factored axial load. Each column's Mn(P) along a fixed direction is concave, so the lowest sum of column strengths at a joint occurs at a vertex of the convex hull of the columns' axial loads over the combinations. Only those combinations are evaluated; the result is the same. The beam strengths and the joint shear do not depend on the combination.
- **Capacity-design shear.** Mpr (1.25 fy) is read from the probable-strength surface along the principal axis, through a (P, Mn) table built once per direction.
- **Speed.** The compression zone of every grid point is the section clipped by a half-plane; it is computed in closed form for all 8,640 points at once (Green's theorem over the kept edges of the outline and of each bar hole), with the same result as polygon clipping. The surface is built from the bar layout directly, without meshing a concreteproperties section, and every demand of a column is looked up in one vectorized call. A surface takes about 40 ms instead of 0.65 s; a 48-column SMRF model with 20 combinations is designed in about 5 s.

SMRF confinement (ACI 18.7.5.4) uses bc and Ach measured to the outside of the hoops (Dc to the outside of the spiral, rho_s = 4 Asp / (Dc s)), and kn counts bar positions, a bundle once. Ties are at least 10 mm, and 12 mm for bars over 32 mm or bundled bars (NSCP 425.7.2.2).

## Calculation reports (PDF)

`sdt beams` and `sdt columns` write an A4 PDF from the design results to the output folder. The report is one column wide and starts with the design basis and a summary table of all members; each member then has its own section of value tables. Equations are not written out.

Beam report, per beam:

- section and materials
- flexure at the left support, midspan and right support, top and bottom: Mu, bars per layer, As, d, a, strain, phi, Mn, phi Mn and Mu / phi Mn
- shear and torsion per zone: Vu, Ve, design Vu, Tu, d, Vc, legs, spacing, Vs, phi Vn, Vu / phi Vn, At/s and Al
- with SMRF on: nominal moment strengths at each location and the seismic design shear
- deflection (ACI 24.2): the live, roof live and after-partitions deflections with their limits and ratios, Ie at the three zones, the long-term factor and the check
- detailing and design status

The design results hold the bars, legs and spacings; depths, strengths and ratios in the report are worked out again from them with the same design classes.

Column report, per column: section and vertical bars, then the governing combination at each end for axial load and flexure, slenderness about each axis (lu, k, k lu/r, the limit, Cm, Pc and the largest δns), column shear, strong column-weak beam (per axis), then joint shear per axis (it does not depend on the load combination, so none is named), the transverse reinforcement values and the design status. Each column ends with a 3D figure of its layout's design interaction surface, with every combination at both ends, the hull vertices and the governing demand. The strong column-weak beam check is not drawn there: it uses the nominal surface and belongs to the joint, not to one column. The caption under the figure gives its lowest ratio along X and Y and the result.

The reports need a LaTeX install with `pdflatex` (MiKTeX or TeX Live), like the wind and composite reports. It is not part of the toolkit or of the packaged program. When it is missing, the results and schedules are still written and the summary gives the command to install it (`winget install MiKTeX.MiKTeX`); `sdt doctor` shows whether it is found.

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

- Sections other than rectangles and circles with the rectangular stress block and lumped bars (L, T, walls) fall back to the per-demand exact solver.
- Slenderness: the member effect is calculated (ACI 6.6.4.5); the sway effect must be in the ETABS forces (the P-delta analysis that `sdt setup` defines). A footing is fixed (ψ = 1.0) or pinned (ψ = 10) as the support in the ETABS model has it; a spring support is not read and counts as pinned unless the rotation is also restrained. Design data stored by a version before 0.1.1 has no supports: the base is then assumed fixed and the report says so, until `sdt beams` is run again. A slab without beams is not counted as a brace or as a restraint, which is on the safe side. The minimum moment acts about both axes at once.
- Joint checks ignore slab reinforcement. Beams use local-axis angle 0 unless ETABS supplies one.
- Crosstie spacing uses an assumed `hx` from the configuration, not the drawn layout.
- Beam design assumes rectangular sections and uses one bar diameter per beam.
- SMRF confinement, joint classification and anchorage details need an independent check against ACI 318M-14 Chapter 18 before use.

## Tests

`tests/test_beam_designer_aci318.py`, `tests/test_column_designer_aci318.py`, `tests/test_column_slenderness.py` (hand calculations of the magnifier, the limits, k and the unbraced lengths), `tests/test_design_outputs.py` (workbook cells, the optional levels, the earth cover, the summaries), `tests/test_etabs_api_services.py`, `tests/test_frame_tagger.py` and `tests/test_calc_report.py` cover the design engines, the extraction logic, the tagging rules and the report text against published examples, hand calculations and behaviour rules. They run without Excel or ETABS.
