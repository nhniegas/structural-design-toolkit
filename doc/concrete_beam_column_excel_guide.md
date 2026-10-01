# Concrete Beam and Column Design Excel Integration Guide

This guide combines the reinforced-concrete beam and column design workflows,
including their calculation scope, workbook inputs and outputs, Excel/xlwings
integration, DXF export callbacks, validation requirements, and troubleshooting.

> **Engineering review required:** This document describes the current program
> behavior; it does not certify code compliance. A qualified structural engineer
> must verify the adopted ACI edition, local amendments, units, load combinations,
> force signs, reinforcement detailing, ETABS mapping, and all design results
> before design use or construction.

## 1. Workflow overview

The workbook uses dedicated design modules rather than duplicating engineering
logic in `main.py`:

- [`beam_designer_aci318.py`](../design/beam_designer_aci318.py)
  contains beam extraction, design, reporting, and beam DXF export.
- [`column_designer_aci318.py`](../design/column_designer_aci318.py) contains
  column design, SMRF/joint checks, column reporting, and the stacked column DXF
  schedule export.
- [`main.py`](../main.py) remains a compatibility facade for existing VBA calls
  and re-exports the public beam and column callbacks. New VBA buttons may call
  the dedicated design modules directly.

The normal sequence is:

1. Extract ETABS combinations, member properties, forces, and connectivity.
2. Extract and verify beam design inputs.
3. Run beam design and confirm TOP/BOTTOM results.
4. Run column design; SMRF checks use the current beam results.
5. Review Excel output tables and export beam or column DXF schedules.

## 2. Beam design workflow

The consolidated script contains:

- Beam support-condition classification from ETABS connectivity.
- ETABS load-combination and beam-member selection.
- Excel input extraction and result writing.
- Flexural, shear, torsion, seismic, and reinforcement-detailing calculations.
- Optional DXF beam-schedule export.

Wind-design and composite-column functions are not included. The script imports ETABS and GUI helpers from the project's `utilities` package, and uses `xlwings`, `pandas`, and `ezdxf`.

The module can be imported by a workbook button without starting a workflow. When executed directly as a Python script, the `__main__` block calls `extract_forces_properties_from_etabs()`.

### 2.1 ETABS extraction button

Call `extract_forces_properties_from_etabs()` to:

1. Connect through `ETABSConnector`.
2. Retrieve available load combinations and show a selection dialog.
3. Apply the selected combinations and run concrete design.
4. Retrieve available members and show a member-selection dialog.
5. Export selected inputs, factored loads, frame data, and connectivity through `ETABSDataExporter`.

The helper makes the ETABS calls; this guide does not alter or prescribe behavior inside those APIs.

The dedicated `etabs_api` package also exposes reusable ETABS service clients.
Use `ETABSConnector` as the entry point and access its `analysis`, `database`,
`loads`, `results`, `selection`, `properties`, `assignments`, `geometry`, and
`stories_grids` attributes for model automation outside the workbook callbacks.
The former `utilities/_etabs_api.py` and
`utilities/_etabs_data_extraction.py` compatibility files were removed; external
scripts should import `ETABSConnector` and `ETABSDataExporter` from `etabs_api`.

### 2.2 Beam input data button

Call `extract_beam_design_data()` to read frame and connectivity tables, determine beam support status, join that status to beam properties, add reinforcement overwrite values, sort and deduplicate beams, and write the input table to the `BEAM DESIGN` sheet.

Default worksheet/cell locations:

| Purpose | Default |
|---|---|
| Frame data | `FRAME DATA!B2` |
| Connectivity data | `CONNECTIVITY!B2` |
| Reinforcement overrides | `OVERWRITES` |
| Output/input beam table | `BEAM DESIGN!B8` |
| Main-bar diameter | `OVERWRITES!I4` |
| Stirrup diameter | `OVERWRITES!I5` |
| Side-bar diameter | `OVERWRITES!I6` |
| Transverse steel yield strength | `OVERWRITES!I7` |
| Concrete cover | `OVERWRITES!I8` |

The connectivity table must contain `DesignType`, `UniqueName`, `UniquePtI`, and `UniquePtJ`. Support points are collected from column and wall connectivity point columns (when present). For a beam, support at both ends gives `Supported Both Ends`; support at one end gives a cantilever status; otherwise it is classified as `Beam-Framed / Floating`.

### 2.3 Beam design button

Call `run_beam_design_from_excel()` to read the inputs, call `execute_beam_design()`, sort results, display readable column headers, and write/format the result table beginning at `BEAM DESIGN!B8`.

It reads:

- Seismic enable flag: `OVERWRITES!F3`.
- Gravity combo name: `OVERWRITES!F4`.
- Frame-force table: `FACTORED LOADS!B2`.
- Beam-property table: `BEAM DESIGN!B8`.

The design function uses a default axial load of 50 kN when `Pu_axial_load` is not passed directly.

### 2.4 CAD export button

Call `export_cad_drawings()` to read the design table, restore internal column keys from display labels, group rows by story, and create a DXF schedule for each story. The DXF helper expects both TOP and BOTTOM rows for each beam and uses the reinforcement layer fields to write the schedule.

### 2.5 VBA macro calls

Each Excel button should call its own public VBA macro. In the `RunPython` statement, import the consolidated module from the project package, rather than `main`:

```vb
Application.Run "xlwings.RunPython", _
    "import sys; sys.path.insert(0, r'" & ThisWorkbook.Path & "\..'); " & _
    "import design.beam_designer_aci318 as beam; " & _
    "beam.extract_forces_properties_from_etabs()"
```

Use the corresponding function name for each button:

| Button action | Python function |
|---|---|
| ETABS extraction | `extract_forces_properties_from_etabs` |
| Beam input extraction | `extract_beam_design_data` |
| Beam design | `run_beam_design_from_excel` |
| CAD schedule export | `export_cad_drawings` |

The example retains the prior path assumption that the workbook is one directory below the project root. If the workbook is stored elsewhere, adjust the Python search path. Ensure that the `design` folder is importable as a package in the Python environment used by xlwings.

### 2.6 Beam expected input data

#### Beam properties

The design pipeline reads the following fields (some have alternate names or defaults):

| Property | Preferred column | Fallback/default used by design |
|---|---|---|
| Beam identifier | `UniqueName` | Required |
| Width | `Width` | `b`, then 300 mm |
| Overall depth | `Depth` | `Height`, then 500 mm |
| Concrete strength | `f'c` | 28 MPa |
| Longitudinal bar yield strength | `fy` | 413.69 MPa |
| Stirrup yield strength | `fyw` | `fyt`, then 414 MPa |
| Main-bar diameter | `dm` | `d_main`, then 12 mm |
| Stirrup diameter | `ds` | `d_stirrup`, then 12 mm |
| Side-bar diameter | `dw` | `d_side`, then main-bar diameter |
| Concrete cover | `cc` | 40 mm |
| Support condition | `SupportStatus` | Used to identify cantilevers |

#### Frame forces

The frame-force table is expected to provide at least:

| Column | Meaning in this workflow |
|---|---|
| `UniqueName` | Beam identifier matching the property table |
| `Combo` | Load-combination name |
| `Station` | Position along the beam |
| `M3` | Flexural moment used for the top/bottom demand envelopes |
| `V2` | Shear demand |
| `T` | Torsion demand |

This workflow assumes dimensions and forces use compatible units: section dimensions and stations in mm, strength in MPa (N/mm²), moments in kN-m, and shear/axial force in kN. Check the ETABS export and workbook values; the design equations do not automatically discover or convert arbitrary unit systems.

### 2.7 Beam calculation workflow

#### Demand zoning and envelopes

For each beam and combo, the force stations are split into design zones:

- Flexural end zones cover the first and last 25% of the modeled span.
- The remaining central span is the flexural middle zone.
- Shear and torsion end zones extend `2h` from each modeled end.
- The region between those end zones is the interior `2h` zone.

Negative `M3` values are converted to positive magnitudes for TOP-face flexure. Positive `M3` values are used for BOTTOM-face flexure. Absolute peaks of `V2` and `T` are used for shear and torsion.

Within the design solver, moment demands are enveloped over the combinations in a beam group. The selected gravity combination is used to identify gravity end shears for the seismic calculation. When seismic design is enabled, probable-moment sway shear is combined with gravity shear and checked against the force envelope.

#### Flexure and reinforcement arrangement

`BeamFlexureDesign`:

1. Calculates the maximum number of longitudinal bars in one layer using available section width and a minimum clear-spacing rule.
2. Enforces a minimum bar count based on the 150 mm spacing rule.
3. Distributes bars over layers and calculates top/bottom steel centroids and effective depths.
4. Checks minimum flexural steel and crack-control spacing.
5. Increases top and bottom bar counts until moment capacity, minimum steel, crack spacing, and torsion-steel area requirements are met, or a three-layer congestion limit is reached.

The effective depth `d` is measured from the extreme compression face to the centroid of the tension reinforcement. The opposite-face centroid is `d'`.

#### Moment capacity and compression steel

`solve_moment_capacity()` solves the neutral-axis location `c` by force equilibrium. It uses:

- Equivalent concrete stress: `0.85 fc`.
- Equivalent compression-block depth: `a = beta1 c`.
- Linear strain compatibility with a concrete extreme compression strain of 0.003.
- Steel stress limited to `±fy`.
- Opposite-face steel force based on its compatible strain. If compression steel lies within the Whitney block, the steel force is reduced by the concrete stress displaced by the bar area.

The return value includes nominal `Mn`, `phi_Mn`, tensile strain, `phi`, provided tension/compression steel areas, compression-steel stress, `c`, and `a`. If tensile strain is at least 0.005, the current implementation uses `phi = 0.90`; otherwise it applies its implemented transition expression with a lower bound of 0.65.

#### Shear and stirrup spacing

`BeamShearDesign.solve_shear_capacity()` calculates concrete shear `Vc`, the maximum stirrup contribution, factored strength, required stirrup ratio `Av/s`, and a spacing ceiling. `Vc` is set to zero when the seismic check requires suppression.

The full beam workflow combines the required shear and torsion transverse-steel ratios, applies the most restrictive applicable shear/torsion spacing limit, and, where seismic rules apply, adds a more restrictive support-zone spacing limit. The recommended spacing is rounded down to a 25 mm increment.

#### Torsion and longitudinal steel

`BeamTorsionDesign.solve_torsion_capacity()` calculates torsion thresholds and a combined shear/torsion stress check. Where the script determines torsion reinforcement is required, it calculates transverse torsion steel `At/s` and longitudinal torsion steel `Al`.

`distribute_longitudinal_torsion_and_skin()` distributes `Al` over the four faces:

- `Al/4` to the top face.
- `Al/4` to the bottom face.
- `Al/4` to each of the two web faces.

It also determines web-bar counts for torsion/skin reinforcement. The flexural solver includes the required top/bottom torsion steel area when it evaluates its reinforcement requirements.

#### Seismic and detailing checks

`BeamSeismicDesign` provides:

- Top and bottom reinforcement-ratio checks.
- Positive/negative moment capacity-ratio checks for the special moment frame checks represented in this script.
- Probable moment calculations using the implemented `1.25 fy` steel stress multiplier.
- Sway-shear and concrete-shear suppression checks using axial load and demand comparisons.

Additional helpers determine a common stirrup-leg arrangement, check whether stirrup legs are anchored by the outer longitudinal bars, and add bars where needed. For cantilevers, the engine shares the governing top and bottom reinforcement counts across the beam zones.

### 2.8 Beam output table and header labels

The result DataFrame contains one row for TOP reinforcement and one for BOTTOM reinforcement per beam. Rows include the beam properties, force envelopes, longitudinal bar counts by location/layer, side reinforcement, stirrup legs and spacing, seismic checks, anchorage checks, and `Design_Status`.

Selected internal labels are converted for Excel display. Examples:

| Internal dataframe key | Excel display label |
|---|---|
| `n_left_L1` | `n₍left, 1₎` |
| `n_mid_L2` | `n₍mid, 2₎` |
| `Mu_left` | `Mᵤ, left` |
| `Vu_mid_2h` | `Vᵤ, mid (2h)` |
| `Tu_right` | `Tᵤ, right` |
| `ClearSpan_Ln` | `Lₙ` |
| `Spacing_2H` | `s₂H (mm)` |
| `n_side_per_face_gov` | `Side bars / face (governing)` |
| `Design_Status` | `Design status` |

Labels not in the display mapping retain their internal names. The subscript-like labels use Unicode characters; their exact appearance depends on the Excel font. The CAD exporter converts the mapped display labels back to internal keys before processing reinforcement columns.

The Excel design workflow bolds and borders the header row, colors column groups, shades alternating beam row pairs, adds section dividers, and calls `autofit()`.

### 2.9 Beam worked example: one beam, left support zone

This example uses the script's actual calculation methods with a single beam and a single design zone. It demonstrates a negative-moment TOP-face check, a positive-moment BOTTOM-face check, and non-seismic shear/torsion detailing at the left support. It is not a full ETABS load-envelope example and does not activate seismic spacing or seismic shear suppression.

#### Given data

| Input | Value |
|---|---:|
| Width, `b` | 300 mm |
| Overall depth, `h` | 500 mm |
| Clear span, `Ln` | 6,000 mm |
| Concrete strength, `fc` | 30 MPa |
| Longitudinal steel yield, `fy` | 415 MPa |
| Stirrup steel yield, `fyt` | 415 MPa |
| Main-bar diameter, `db` | 16 mm |
| Stirrup diameter, `ds` | 10 mm |
| Side-bar diameter, `dw` | 12 mm |
| Clear cover, `cc` | 40 mm |
| Assumed maximum aggregate size | 20 mm |
| Negative flexural demand, `Mu_neg` | 150 kN-m |
| Positive flexural demand, `Mu_pos` | 90 kN-m |
| Shear demand at this zone, `Vu` | 120 kN |
| Torsion demand at this zone, `Tu` | 8 kN-m |
| Seismic design for this example | Disabled |

For the initial torsion calculation, the code estimates effective depth as:

```text
d_eff_guess = h - cc - ds - db/2
            = 500 - 40 - 10 - 8
            = 442 mm
```

#### Fit and minimum bar count

The available width between stirrup legs is:

```text
clear_width = b - 2(cc + ds)
            = 300 - 2(40 + 10)
            = 200 mm
```

The governing minimum clear spacing is:

```text
s_clear,min = max(25, db, 4/3 * aggregate size)
            = max(25, 16, 26.67)
            = 26.67 mm
```

Thus the computed maximum bars in one layer is:

```text
n_max = floor((clear_width + s_clear,min) / (db + s_clear,min))
      = floor((200 + 26.67) / (16 + 26.67))
      = 5 bars/layer
```

The spacing-based minimum bar count is 3 for this geometry. The final trial arrangement will satisfy this minimum.

#### Torsion reinforcement demand and distribution

For `b = 300 mm`, `h = 500 mm`, `cc = 40 mm`, and `ds = 10 mm`, the script calculates the stirrup-centerline torsion geometry:

```text
x1 = b - 2cc - ds = 210 mm
y1 = h - 2cc - ds = 410 mm
Aoh = x1*y1      = 86,100 mm^2
Ao  = 0.85*Aoh   = 73,185 mm^2
ph  = 2(x1+y1)   = 1,240 mm
Acp = b*h        = 150,000 mm^2
Pcp = 2(b+h)     = 1,600 mm
```

The calculated torsion threshold is approximately `Tth = 4.795 kN-m`. Since `Tu = 8 kN-m` exceeds this value, the torsion steel branch is active. The engine reports:

| Torsion result | Value |
|---|---:|
| `Tth` | 4.795 kN-m |
| `Tcr` | 19.063 kN-m |
| Combined shear/torsion stress | 1.199 MPa |
| Allowable stress used in check | 3.410 MPa |
| Transverse torsion steel, `At/s` | 0.17560 mm²/mm |
| Longitudinal torsion steel, `Al_design` | 613.736 mm² |

The combined-stress check passes for these inputs. The resulting longitudinal allocation is:

```text
Al per face = 613.736 / 4 = 153.434 mm^2
```

The code therefore requires 153.434 mm² on each of the top and bottom faces and 153.434 mm² on each side face. With 12 mm side bars, it selects 2 bars per side face for this section and demand.

#### Required flexural reinforcement

Area of one 16 mm bar:

```text
Ab = pi*db^2/4 = pi*16^2/4 = 201.062 mm^2
```

The design iteration uses the moment demands and includes 153.434 mm² torsion steel requirements at the top and bottom faces. It returns:

| Face | Selected bars | Steel area | Demand |
|---|---:|---:|---:|
| Top (negative-moment tension face) | 5 × 16 mm | 1,005.310 mm² | 150 kN-m |
| Bottom (positive-moment tension face) | 4 × 16 mm | 804.248 mm² | 90 kN-m |

Both faces fit in one layer because the calculated limit is 5 bars per layer.

#### Negative-moment capacity including compression steel

For negative moment, the top bars are in tension and the bottom bars are the opposite-face reinforcement. The computed centroids are:

```text
d  = 500 - (40 + 10 + 8) = 442 mm
d' = 40 + 10 + 8        = 58 mm
```

The concrete block parameter used by the script is:

```text
beta1 = 0.85 - 0.05*(fc - 28)/7
      = 0.835714
```

Areas and force equilibrium:

```text
As_tension = 5*201.062 = 1,005.310 mm^2
As_opposite = 4*201.062 = 804.248 mm^2
```

The script solves the force-equilibrium residual for `c` and obtains:

| Section result | Value |
|---|---:|
| Neutral axis, `c` | 61.251 mm |
| Equivalent block depth, `a = beta1*c` | 51.188 mm |
| Tension steel strain, `et` | 0.018649 |
| Opposite-face steel stress | +31.846 MPa (compression) |
| Concrete compression resultant, `Cc` | 391.591 kN |
| Opposite-face steel compression resultant | 25.612 kN |
| Tension steel resultant at `fy` | 417.203 kN |

For this arrangement, `d' = 58 mm` is greater than `a = 51.188 mm`, so the opposite-face steel lies outside the Whitney block and the concrete-displacement correction is not applied. Its positive calculated steel stress means this steel is in compression.

The internal force balance is approximately:

```text
Cc + Csteel = 391.591 + 25.612 = 417.203 kN
Tsteel       = 1,005.310*415/1000 = 417.203 kN
```

Nominal moment is evaluated about the tension-steel centroid:

```text
Mn = [Cc*(d - a/2) + Csteel*(d - d')]/1,000,000
   = 172.896 kN-m
```

The tensile strain exceeds 0.005, so the script applies `phi = 0.90`:

```text
phi*Mn = 0.90*172.896 = 155.606 kN-m
```

Therefore, for this script calculation, `phi*Mn = 155.606 kN-m` exceeds `Mu_neg = 150 kN-m`. The compression-steel force is explicitly part of force equilibrium and moment resistance.

#### Positive-moment capacity

For positive moment, the bottom bars become tension reinforcement and the top bars are the opposite-face steel. The engine reports:

```text
phi*Mn_positive = 126.487 kN-m
Mu_positive     = 90.000 kN-m
```

The positive-moment strength check passes for this example.

#### Minimum reinforcement and crack-spacing check

The two code-style minimum-steel expressions evaluated by the script are:

```text
As_min,1 = (0.25*sqrt(fc)/fy)*b*d = 437.518 mm^2
As_min,2 = (1.4/fy)*b*d          = 447.325 mm^2
```

The script also calculates the area corresponding to the 3-bar spacing minimum:

```text
As_150 = 3*201.062 = 603.186 mm^2
```

Thus, the governing minimum area returned by the current implementation is 603.186 mm². The top area (1,005.310 mm²) and bottom area (804.248 mm²) exceed that value; the area checks also include the face torsion-steel requirements during design iteration.

For the 5-bar top layer, the actual center-to-center spacing is 46.0 mm. The crack-control spacing limit is approximately 284.578 mm, so the calculated spacing check passes.

#### Shear and transverse torsion reinforcement

Using `d = 442 mm`, the concrete shear resistance is:

```text
Vc = 0.17*sqrt(fc)*b*d/1000
   = 123.468 kN
```

For `Vu = 120 kN` and `phi_v = 0.75`:

```text
Vs_required = max(0, Vu/phi_v - Vc)
            = max(0, 120/0.75 - 123.468)
            = 36.532 kN

Av/s (shear) = Vs_required*1000/(fyt*d)
             = 0.19916 mm^2/mm
```

The torsion routine gives `At/s = 0.17560 mm²/mm`, so the combined transverse-steel demand used in the design is:

```text
max(Av/s + 2*At/s, torsion minimum)
= max(0.19916 + 2*0.17560, 0.25301)
= 0.55037 mm^2/mm
```

The controlling spacing cap is 155 mm from the torsion limit (the shear spacing cap is 221 mm). A two-legged 10 mm stirrup provides:

```text
Av = 2*pi*10^2/4 = 157.080 mm^2
Av/(required Av/s) = 157.080/0.55037 = 285.4 mm
```

The script applies the 155 mm code limit and rounds down to a 25 mm increment:

```text
recommended spacing = floor(155/25)*25 = 150 mm
```

The factored shear capacity check passes, and the script's non-seismic spacing result for this zone is two-legged 10 mm stirrups at 150 mm spacing. The complete beam pipeline may select a tighter common arrangement after it evaluates all zones and combos.

#### Worked-example summary

For the stated left-zone input and non-seismic assumptions:

| Check/item | Result |
|---|---|
| Top longitudinal reinforcement | 5 × 16 mm |
| Bottom longitudinal reinforcement | 4 × 16 mm |
| Calculated negative `phi*Mn` | 155.606 kN-m > 150 kN-m |
| Calculated positive `phi*Mn` | 126.487 kN-m > 90 kN-m |
| Crack-spacing check | Pass |
| Torsion combined-stress check | Pass |
| Shear capacity check | Pass |
| Side reinforcement from torsion | 2 × 12 mm bars per side face |
| Transverse reinforcement for this zone | 2-legged 10 mm at 150 mm |

This is a focused demonstration of a single zone. It does not replace running `execute_beam_design()` with all actual ETABS combinations, both support zones, the midspan zone, the correct support status, and the project's seismic settings.

### 2.10 Beam validation checklist

Before relying on an Excel result:

1. Confirm the workbook units match the assumptions in Section 3.
2. Confirm force stations cover the full beam and the expected `2h` and quarter-span zones.
3. Confirm `UniqueName` values match across beam properties and frame forces.
4. Confirm the `OVERWRITES!F4` gravity combination is selected correctly and exists in the force table.
5. Confirm the seismic checkbox in `OVERWRITES!F3` reflects the intended design.
6. Review every row marked with a failed or adjusted status and independently check congestion, anchorage, and stirrup-spacing results.
7. Review the final reinforcement schedule against the drawing/detailing requirements and verify all calculation inputs and code provisions with the engineer of record.

### 2.11 Beam implementation notes and limitations

- The module-level description identifies ACI 318M-14, while some class documentation/comments refer to ACI 318-19. Confirm and reconcile the intended governing edition before engineering use.
- The worked example reports values from the current script methods; it does not independently validate those methods against a design standard.
- The Excel display-label helper only renames keys explicitly listed in its mapping. Other output columns remain unchanged.
- `extract_beam_design_data()` and `run_beam_design_from_excel()` use xlwings and require a suitable open workbook when called from Excel.
- `export_cad_drawings()` requires an output directory selection and usable TOP/BOTTOM rows for each beam.
- The computational example in this guide was evaluated from the actual `BeamFlexureDesign`, `BeamShearDesign`, `BeamTorsionDesign`, and longitudinal torsion distribution methods in the consolidated script. ETABS, Excel COM, workbook formatting, and DXF export were not exercised as part of that calculation.

---

## 3. Column design workflow

The script currently provides:

- Rectangular and circular reinforced-concrete section modeling.
- Candidate longitudinal bar-count checks at factored-force stations for columns listed in `FRAME DATA`.
- Axial-flexural capacity calculations using `concreteproperties`.
- Basic transverse reinforcement spacing, SMRF confinement, and capacity-based shear calculations.
- Optional SMRF strong-column/weak-beam, joint shear, and column-continuity checks.
- Excel table reading and output to `COLUMN DESIGN` and `JOINT CHECKS`.

For SMRF checks, the script queries the live ETABS `Frame Assignments - Local Axes` and `Point Object Connectivity` tables. It does not run ETABS analysis. The current local-axis table used by this workflow contains column-axis angles only; beam beta angles are assumed to be 0 degrees and are identified in the joint-check output.

The SMRF implementation is **provisional**. In particular, the current live workbook was found to have different beam identifiers in `FRAME DATA` and `BEAM DESIGN` (73 unique beam IDs versus 171 unique beam IDs, with no matching names in the inspected snapshot). SMRF joint evaluation needs matching designed reinforcement for each beam framing into a joint and will stop with an error when a required beam is absent. Resolve the workbook member mapping before treating joint results as usable.

### 3.1 Column units and sign convention

The script expects:

| Quantity | Workbook/interface unit |
|---|---|
| Length, cover, bar diameter, spacing | mm |
| Concrete and steel strength | MPa |
| Axial force and shear | kN |
| Moment and torque | kN-m |
| Internal section force/moment calculations | N and N-mm |

`FACTORED LOADS` is converted to kN and kN-m by the existing ETABS exporter. The column script uses the axial force sign in that table as-is and treats positive `P` as compression. Confirm that this matches the ETABS result convention and the current exporter for the selected model before use; the script does not infer or auto-correct the sign.

### 3.2 Column calculation workflow

#### Input selection and column candidates

1. Read the four workbook tables and overwrite values.
2. Normalize object names so a numeric point identifier read from Excel as `502.0` matches the ETABS identifier `"502"`.
3. Deduplicate repeated `FRAME DATA` rows after checking that each member has a consistent property set.
4. Select `DesignType == "Column"` members from `FRAME DATA`.
5. For each column, build its section and select an initial bar count that meets the 150 mm longitudinal-bar spacing target.
6. Increase candidate bar counts in steps of two, checking every available force combination and the first/last force station for axial-flexural strength and longitudinal steel ratio.
7. Apply transverse detailing and shear checks, then run the SMRF joint checks when the overwrite flag is enabled.

#### Axial-flexural capacity

For gross area `Ag`, total longitudinal steel area `Ast`, concrete strength `fc`, and longitudinal steel yield strength `fy`, the nominal concentric axial strength implemented is:

```text
Po = 0.85 fc (Ag - Ast) + fy Ast
```

The script then applies a maximum nominal axial cap:

```text
Pn,max = 0.80 Po   tied rectangular section
Pn,max = 0.85 Po   circular section treated as spirally reinforced
```

At a factored force state, it evaluates a moment capacity for the applied `Pu`, using the angle of the resultant `M2`/`M3`, and compares the resultant moment with the capacity in that direction. This is a **radial directional capacity check**, not a generated full biaxial `P-M2-M3` interaction surface. Do not interpret it as a substitute for a complete biaxial interaction check unless that method has been separately accepted and verified.

The strength-reduction factor for moment is calculated from the strain in the extreme longitudinal reinforcement, with tied/spiral branches and a transition between compression- and tension-controlled strain limits. The separate maximum axial output uses 0.65 for tied members and 0.75 for spiral members.

#### Basic tie/spiral spacing and reinforcement ratio

For tied non-SMRF columns, the maximum spacing is calculated as the least of `16 db` for longitudinal bars, `48 db` for ties, and the smallest gross section dimension. The bar-layout helper places bars around all four perimeter edges and chooses the count required by the 150 mm spacing target.

For circular sections, the output represents a spiral. The implemented pitch and clear-spacing checks are reported separately; the program does not provide a fabrication drawing.

The longitudinal steel ratio is:

```text
rho = Ast / Ag
```

The script uses a 1% minimum, an 8% general maximum, and a 6% maximum when the SMRF flag is enabled. SMRF dimension and transverse reinforcement checks are additional program checks, but the current implementation is not a substitute for a complete Chapter 18 detailing review.

#### Shear and SMRF checks

- Column shear demand is compared with the maximum of analysis shear and a capacity-based value from probable end moments divided by the clear force-station length.
- Non-SMRF `Vc` uses the implemented axial-compression expression and a cap.
- SMRF column `Vc` is set to zero in the shear calculation.
- Transverse leg demand is calculated from the selected shear demand, spacing, effective depth, transverse-bar area, and transverse steel strength.
- SMRF joint checks use designed TOP/BOTTOM beam bar counts, a 1.25 multiplier for probable beam forces, combination-specific column axial force, a 1.2 column/beam ratio threshold, joint area, and a joint shear expression.
- Slab reinforcement is excluded from the beam strength calculation.
- Column continuity at a joint is reported using the lateral offset divided by vertical run, with a 1:6 limit.
- Rectangular SMRF crosstie anchorage and alternating hook placement are not generated; the output explicitly reports `NOT VERIFIED` and changes the overall column status to `REVIEW REQUIRED`.

These descriptions state what the current program does. The detailed application of ACI 318M-14 Chapter 18—including the actual confinement geometry, crosstie support and anchorage, joint shear classification, joint effective area, beam-bar development, and load-direction equilibrium—requires independent engineering review.

### 3.3 Column variable definitions

| Variable | Meaning | Units |
|---|---|---|
| `Ag` | Gross column section area | mm2 |
| `Ast` | Total column longitudinal bar area | mm2 |
| `Ach` | Core area bounded by transverse reinforcement | mm2 |
| `Po` | Nominal concentric axial capacity before the axial cap | N |
| `Pn,max` | Code-capped nominal axial capacity | N |
| `Pu` | Factored column axial force at the selected end/station | N internally; kN in Excel |
| `Mn` | Nominal moment capacity at the specified axial load and bending direction | N-mm internally; kN-m in Excel |
| `phi` | Strength-reduction factor based on extreme reinforcement strain and section classification | dimensionless |
| `Ve` | Capacity-based shear demand derived from probable end moments and clear length | kN |
| `hx` | Assumed maximum spacing between laterally supported longitudinal bars for SMRF confinement | mm |
| `rho` | Longitudinal reinforcement ratio `Ast/Ag` | dimensionless |
| `Aj` | Effective joint area used by the joint shear check | mm2 |
| `UniqueName` | ETABS object identifier used to join properties, forces, and connectivity | text |
| `UniquePtI`, `UniquePtJ` | ETABS connectivity endpoint identifiers | text / numeric IDs normalized to text |

### 3.4 Worked example: 400 x 400 mm tied column

This worked example demonstrates the current section routine with a square tied column. It is a calculation illustration, not a design approval.

#### Inputs

```text
Section                 400 mm x 400 mm
Concrete strength, fc   28 MPa
Longitudinal steel, fy  415 MPa
Tie steel, fyt           415 MPa
Longitudinal bars        8-D25
Tie diameter             10 mm
Cover                    40 mm
Factored axial load, Pu  500 kN compression
Factored moment          M2 = 180 kN-m, M3 = 0
```

The section routine placed two bars per perimeter face, for eight bars total. The bar area is:

```text
Ab  = pi (25 mm)^2 / 4 = 490.874 mm2
Ast = 8 Ab             = 3,926.991 mm2
Ag  = 400 x 400         = 160,000 mm2
rho = Ast / Ag          = 0.024544 = 2.454%
```

#### Nominal and capped axial capacity

```text
Po = 0.85 (28) (160,000 - 3,926.991) + 415 (3,926.991)
   = 5,344,239 N
   = 5,344.239 kN

Pn,max = 0.80 Po
       = 4,275.391 kN

phi Pn,max = 0.65 Pn,max
            = 2,779.004 kN
```

The `Pu = 500 kN` example is below the reported capped design axial capacity. In a combined axial-flexure check, the section routine also computes moment capacity at the applied `Pu`.

#### Moment capacity from the script

The focused run of `ColumnFlexureDesign.solve_moment_capacity()` using the inputs above and `Pu = 500,000 N` produced:

```text
Mn      = 291.520 kN-m
phi     = 0.89527
phi Mn  = 260.990 kN-m
```

The example moment demand is:

```text
Mu = sqrt(M2^2 + M3^2)
   = sqrt(180^2 + 0^2)
   = 180.000 kN-m

Utilization = Mu / phi Mn
            = 180.000 / 260.990
            = 0.690
```

The directional moment comparison passes in this illustration, and the provided steel ratio is within the script's general 1%-8% bounds. The reported `phi Mn` is a runtime result from the selected section-analysis library; the nominal axial hand calculation above is independent arithmetic. Before relying on the moment result, independently verify the section strain profile, bar placement, force sign, moment-angle mapping, and biaxial design method.

### 3.5 Column Excel result tables

`COLUMN DESIGN` includes one summary row per designed column. Important fields include:

- `Longitudinal_Bars`, `Reinforcement_Ratio`, and bar spacing target.
- `Transverse_Type`, `Transverse_Spacing_Provided_mm`, and `Transverse_Legs_Per_Direction`.
- `Confinement_Check`, `Tie_Diameter_Check`, and `Alternating_Anchor_Check` (the rectangular SMRF anchor/layout check is deliberately marked `NOT VERIFIED`).
- `SMRF_Dimension_Check` and `Flexure_Axial_Check`.

Below the summary table, the sheet writes force checks and shear-check details. `JOINT CHECKS` contains per-joint/per-combination directional rows, including column/beam ratio, joint shear demand/capacity, continuity, and the `Beam_Local_Axis_Assumption` field. That field reports the beta=0 assumption when no beam angle was returned from the column-only ETABS local-axis table.

### 3.6 Column limitations and validation checklist

Do not use results for construction until all items below are resolved and signed off:

1. Confirm the exporter’s `P` sign convention equals the program's positive-compression assumption.
2. Match all required `FRAME DATA`, `CONNECTIVITY`, `FACTORED LOADS`, and `BEAM DESIGN` object names. The inspected workbook currently has a beam-name mismatch that prevents validated SMRF joint checks.
3. Verify station direction and I/J mapping against ETABS results for representative columns.
4. Verify the reconstructed local axes and moment direction against ETABS for rotated and vertical members.
5. Validate the moment interaction algorithm against a trusted uniaxial and biaxial interaction calculation.
6. Independently check SMRF confinement, crosstie anchorage/alternating hooks, joint shear, joint face classification, and capacity-based shear calculations against the adopted ACI 318M-14 provisions and CRSI examples.
7. Check every output warning/status, and verify a failed run did not leave stale output tables being mistaken for new results.
8. Have a licensed structural engineer review the code, model assumptions, and calculations.

References used as project context include ACI 318M-14 and the project CRSI ACI 318 Design Guide. This guide does not reproduce or replace either reference.

---

> **Important:** The current workbook snapshot has an unresolved mismatch between beam IDs in `FRAME DATA` and `BEAM DESIGN`. Do not run or rely on SMRF joint checks until the beam results represent the same ETABS members used by the frame/connectivity tables. A successful column-only calculation does not establish that the joint checks are valid.

### 3.7 Column Excel integration

The routine reads these locations:

| Sheet and location | Required purpose |
|---|---|
| `FRAME DATA!B2` | Frame properties; the routine selects rows where `DesignType` is `Column`. |
| `CONNECTIVITY!B2` | ETABS frame endpoint identifiers and `DesignType`. |
| `FACTORED LOADS!B2` | Force combinations and stations for columns; includes `P`, `V2`, `V3`, `M2`, and `M3`. |
| `BEAM DESIGN!B8` | TOP/BOTTOM beam reinforcement results for beams framing into SMRF joints. Run beam design first. |
| `OVERWRITES!F3` | Boolean SMRF/seismic detailing switch. |
| `OVERWRITES!I10` | Column longitudinal bar diameter in mm. |
| `OVERWRITES!I11` | Column tie/spiral diameter in mm. |
| `OVERWRITES!I12` | Column cover in mm. |

The overwrite diameters and cover must be positive numeric values. The `FRAME DATA` table must include `UniqueName`, `DesignType`, `f'c`, `fy`, `fys`, and the section geometry needed by the selected shape (`Width` and `Depth`, or `Diameter`). The connectivity table needs `UniqueName`, `DesignType`, `UniquePtI`, and `UniquePtJ`. The forces table needs `UniqueName`, `Combo`, `Station`, `P`, `V2`, `V3`, `M2`, and `M3`.

Beam joint checks also need matching `BEAM DESIGN` rows with `UniqueName`, `Face`, `Width`, `Depth`, `f'c`, `fy`, `dm`, and the first three layer-count fields at the beam end (`n_left_L1..L3` or `n_right_L1..L3`, restored internally from the workbook's formatted headers).

#### Recommended column button workflow

Run the existing project macros in this order:

1. Extract ETABS frame data, connectivity, and selected factored loads.
2. Extract/update beam design inputs.
3. Run beam design and confirm the output contains both TOP and BOTTOM results for every framing beam required at a column joint.
4. Confirm the ETABS model is open and connected if `OVERWRITES!F3` is TRUE.
5. Run the column-design button.
6. Review `COLUMN DESIGN`, its detailed check tables, and `JOINT CHECKS`; confirm that they are newly written for the current inputs.

When `OVERWRITES!F3` is FALSE, the routine does not retrieve the ETABS local-axis and point-coordinate tables and does not create SMRF joint rows. When TRUE, it connects to ETABS for those two tables, so keep the intended model active.

#### Column VBA macro

If the workbook is stored in the `spreadsheets` folder and `design` is a sibling folder under the project root, a button can call:

```vb
Sub TriggerColumnDesign()
    On Error GoTo ErrorHandler

    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual
    Application.DisplayAlerts = False

    Application.Run "xlwings.RunPython", _
        "import sys; " & _
        "sys.path.append(r'" & ThisWorkbook.Path & "\..'); " & _
        "from design.column_designer_aci318 import run_column_design_from_excel; " & _
        "run_column_design_from_excel()"

CleanUp:
    Application.Calculation = xlCalculationAutomatic
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True
    Exit Sub

ErrorHandler:
    MsgBox "An error occurred during column design: " & Err.Description, vbCritical
    Resume CleanUp
End Sub
```

Assign `TriggerColumnDesign` to a separate worksheet/form button. Confirm that `ThisWorkbook.Path & "\.."` resolves to the project root containing the `design` and `utilities` packages. If the workbook is stored elsewhere, change the appended project-root path accordingly.

#### Column output interpretation

On completion:

- `COLUMN DESIGN` contains the column summary at `B2`.
- The force-check table follows the summary, separated by blank rows.
- The shear-check table follows the force checks.
- `JOINT CHECKS` contains strong-column/weak-beam and joint shear results when the SMRF switch is on; otherwise it reports that no SMRF joint results were produced.

Inspect each result field rather than relying on one overall status. A `PASS` in the summary does not override an `ERROR` in a force detail or a missing beam/joint input. Rectangular SMRF columns currently report `NOT VERIFIED` for alternating crosstie anchorage/layout and receive a `REVIEW REQUIRED` overall status; this is not a passed detailing check. The joint output's `Beam_Local_Axis_Assumption` field indicates when beta=0 was assumed because the retrieved angle table has columns only.

#### Column pre-run data checks

Before clicking the button:

- Confirm that `UniqueName` values refer to the same model objects in all four tables.
- Confirm `UniquePtI` and `UniquePtJ` are populated and identify real endpoints.
- Confirm each designed column has numeric force rows at both ends for every intended load combination.
- Confirm the force `P` sign convention. The current calculation treats positive `P` as compression.
- Confirm beam design has TOP and BOTTOM records and that the left/right reinforcement fields correspond to ETABS I/J ends.
- For SMRF checks, confirm all columns above/below a joint are included and all beams framing into it have current beam design output.
- Confirm model units and concrete/reinforcement strengths match the script's mm-MPa-kN-kN-m interface.
- Keep a saved copy of the workbook before a design run.

#### Column troubleshooting

| Symptom | Checks |
|---|---|
| A required sheet/table or column is missing | Verify the sheet name, start cell, and headers listed above. |
| The routine reports a missing framing beam | Reconcile beam names between `CONNECTIVITY`, `FRAME DATA`, `FACTORED LOADS`, and `BEAM DESIGN`; rerun extraction/design. |
| The routine reports an undesigned adjacent column | Include the full column stack at the joint in `FRAME DATA` and provide force rows for each column. |
| ETABS connection fails | Open the intended ETABS model and confirm the API is available. |
| A run fails but old tables remain visible | Treat the old tables as stale; the routine writes outputs only after completing calculations. Check the error message and rerun only after correcting inputs. |
| Unexpected axial/shear/flexure results | Verify force signs, stations, axes, units, combinations, and section properties against ETABS and an independent calculation. |

The column designer is available through its dedicated module and is also re-exported by `main.py` for compatibility with existing workbook callbacks.
