# Slab design (`sdt slabs`)

`sdt slabs` (since v0.19.0) designs the solid slabs of the model open in ETABS, panel by panel, to NSCP 2015 Sections 406, 407 and 408. It reads the model and does not change it.

## How to use it

1. Open the model in ETABS. No analysis is needed: the slabs are designed from their loads, not from ETABS shell forces.
2. Run `sdt slabs` and answer the window.
3. Read the summary, then the workbook's **Flags** sheet.

## What is asked

| Question | Default |
|---|---|
| fy of the slab bars (MPa) | 414 |
| Bar diameter, both ways (mm) | 12 |
| Largest bar spacing (mm) | 300 (the office maximum; the code limits apply too) |
| Method 3 when alpha_fm is at least | 2.0: two-way panels on beams with a mean alpha_f under it go to the Direct Design Method |
| Most marks per floor | 15 |
| Merge marks with spacings within (mm) | 25: marks whose spacings all differ by no more than this are merged; 0 keeps every different spacing a mark of its own |
| Unit weight of the concrete (kN/m3) | The unit weight of the slab concrete in the model, else 23.6 |

## What is read from the model

| Item | From |
|---|---|
| Slabs | The floor area objects with a slab property: the thickness and the concrete (f'c) of the property. Decks and openings are left out, and so is a floor object off a story level |
| Beams | The beams at the level, with the width and depth of their concrete rectangular section. A beam of another section (steel, a tee) is a support taken as stiff |
| Walls | Every wall panel is a support at each story level from its foot to its top |
| Loads | The uniform loads on each floor object, from load sets and direct assignments: dead and superimposed dead patterns as superimposed dead, live, reducible live and roof live patterns as live. The weight of the slab is added from its thickness |

## Panels

A panel is bounded by beam or wall lines (NSCP 408.4.1.7).

- **Cut.** Each floor object is cut along the beam and wall lines that cross it, as models often have one slab object over many bays. The pieces are named after their object: `12:3` is the third piece of object `12`, counted from the south-west.
- **Joined.** Pieces and objects of the same thickness are joined across every edge that no beam or wall runs along.
- **Axes.** Each panel has its own axes: those of the least rectangle around it nearest to the global X and Y. So the bays of a skewed grid are rectangles in their axes, and the workbook gives each panel's centre, the angle of its axes and its spans in them.
- **Sides.** A panel has four sides, W, E, S and N in its axes. A side is supported when at least half of it is on a beam or wall, and continuous when at least half of it has another panel beyond. The clear spans are taken between the faces of the supporting beams.
- **Odd shapes.** A panel that fills less than 90 percent of its rectangle (a trapezoid, an L) is designed on the rectangle and flagged.

## Kinds and methods

| Kind | When | Method |
|---|---|---|
| ONE WAY | Supported on two opposite sides only, or on all four with the long clear span over twice the short | NSCP Table 406.5.2 (wu ln²/14, /11, /16, /24, /16, /9, /10, /11; /12 for spans of 3 m and under), and the shears of Table 406.5.4, when its strip meets NSCP 406.5.1 (a) to (e). Otherwise a strip analysis: a continuous beam on knife-edge supports at the centre-to-centre spans, with the live load on the span and alternate spans, and on the two spans beside a support (NSCP 406.4.2). Bars the other way are shrinkage and temperature bars |
| CANTILEVER | Supported on one side only | wu ln²/2 and wu ln at the support; top bars anchored into the backspan (flagged) |
| TWO WAY | Supported on three or four sides, the long clear span at most twice the short | ACI Method 3 (the coefficient method of ACI 318-63, cases 1 to 9 of the office slab sheets), under NSCP 408.2.1, which allows any procedure that satisfies equilibrium and compatibility. On beams with a mean alpha_f under the limit given: the Direct Design Method (NSCP 408.10) when its limits hold (408.10.2.1 to 408.10.2.7), else Method 3 and a flag to check the panel in ETABS |

**The strip of a one-way panel** is the row of one-way panels through it that span the same way, across its continuous edges. It stops at a panel that does not span that way, such as a two-way panel, a one-way panel turned the other way, or a panel at another skew. That panel takes its own negative moment at the shared edge, and the strip takes the edge as fixed: it is analysed as continuing in a mirror of its end span. Over a beam shared by two panels, use the closer of their top bar spacings.

**Method 3.** The case follows from how many long and short edges are continuous. Edges along the long span carry the short-span strips.

| Case | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| Long edges continuous | 0 | 2 | 0 | 1 | 2 | 1 | 0 | 1 | 2 |
| Short edges continuous | 0 | 2 | 2 | 1 | 0 | 0 | 1 | 2 | 1 |

- **Moments.** Ma,neg = Ca,neg wu la², Ma,pos = Ca,dl wu,d la² + Ca,ll wu,l la², and the same in b with lb. At a discontinuous edge the negative moment is one third of the positive.
- **Shear.** Va = Wa wu la / 2 and Vb = (1 − Wa) wu lb / 2.
- **Ratios.** Values between the m of the tables are interpolated.
- **Source of the tables.** They come from the office sheet SLAB DESIGNER v1.0.xls, checked cell by cell against SLAB DESIGN.xls. The two disagree in four cells, where SLAB DESIGN.xls breaks the smooth run of its own column, and v1.0 is followed. The load ratio table of v1.0 has its short and long labels swapped; the values used are the short-direction share Wa, as published copies of the table give it.

**Direct Design Method.**
- **Static moment.** Mo = qu l2 ln²/8, with ln at least 0.65 l1. It is shared 0.65 / 0.35 in an interior span, and by Table 408.10.4.2 (slab with beams between all supports, or an exterior edge on a wall taken as fully restrained) in an end span.
- **Column strips.** They take the shares of Tables 408.10.5.1, 408.10.5.2 and 408.10.5.5. The beam takes its share of Table 408.10.5.7.1, and the rest goes to the middle strips.
- **Stiffness terms.** alpha_f and beta_t come from the beam sections, as T- and L-beams per NSCP 408.4.1.8.
- **Design moment.** The slab is designed for the larger moment per metre of its column strip (less the beam's share) and its middle strip.

## Checks

| Check | Rule |
|---|---|
| Loads | wu = 1.2 (self weight + superimposed dead) + 1.6 live, at least 1.4 dead (NSCP 203-1, 203-2) |
| Flexure | Rectangular stress block, φ 0.90, 1 m strips (NSCP 422.2, Table 421.2.2). Short bars in the outer layer, d = h − 20 − db/2; long bars inside them |
| Net tensile strain | Tension-controlled; two-way slabs at least 0.004 (NSCP 408.3.3.1) |
| Minimum steel | NSCP Tables 407.6.1.1 and 408.6.1.1: 0.0020 Ag for fy under 420 MPa, else the greater of 0.0018 × 420/fy Ag and 0.0014 Ag. Shrinkage and temperature bars by Table 424.4.3.2 |
| Spacing | One-way main bars 3h and 450 mm (407.7.2.3) and crack control (407.7.2.2, 424.3.2); temperature bars 5h and 450 mm (407.7.2.4); two-way bars 2h and 450 mm (408.7.2.2); the largest spacing you give. Spacings are rounded down to 25 mm; under 75 mm is a failure |
| Shear | φVc = 0.75 × 0.17 √f'c b d (NSCP 422.5.5.1), no stirrups |
| Thickness | One-way: NSCP Table 407.3.1.1 with 407.3.1.1.1 on the clear span. Two-way: Table 408.3.1.2 with the mean alpha_f, plus 10 percent at a discontinuous edge without an edge beam of alpha_f 0.8 (408.3.1.2.1); under alpha_fm 0.2, Table 408.3.1.1. Deflection is not calculated: a slab under the minimum is flagged |
| Cover | 20 mm, slabs not exposed to weather (NSCP Table 420.6.1.3.1) |

A panel that fails is **flagged, not resized**: its row is red in the workbook and its flags give the reason (a thicker slab, a larger bar).

## Marks

- **Sharing.** Panels of the same floor, kind, thickness, result and bars share a mark: `S-1`, `S-2`, ..., numbered on each floor (the schedule and the workbook group them by floor). Two-way marks come first, then one-way, then cantilevers; thinner first; failing marks after passing ones.
- **Failing panels.** A panel that fails only shares a mark with other failing panels, never with passing ones, so its remarks (FAILED: SEE FLAGS) point to it alone.
- **Within a tolerance.** Marks of the same kind, thickness and result, with bars at the same locations and every spacing within the tolerance (25 mm by default), are merged, the closest first.
- **Over the limit.** When a floor still has more marks than the limit, the two closest marks of the same kind, thickness and result are merged, until it is within the limit (or no such pair is left).
- **A merged mark** keeps the closer spacing at each location, so it is never weaker than a panel it covers. The Panels sheet keeps each panel's own spacings.

## What it writes

| File | Folder | Holds |
|---|---|---|
| `<model> - Slab Design.xlsx` | `Results` | A schedule sheet per floor; **Panels**: every panel with its axes, spans, kind, method, loads, moments, steel, spacings, shear and flags (failing panels red); **Flags** |
| `<model> - Slab Schedule.dxf` | `Schedules` | A table per floor: for each mark, TOP bars at the discontinuous (DISCO) and continuous (CONT) edges and BOTTOM bars at midspan, in the short and the long direction, the thickness and the remarks (ONE WAY, TWO WAY, CANTILEVER) |
| `<model> - Slab Calculations.pdf` | `Calculations` | The design basis, a summary, then each panel: geometry and loads, method, the bars at each location, shear and thickness, flags |
| `<model> - slab design.json` | beside the model | The panels and their marks, for the slab marks of `sdt plans` |

## Tested

- **pytest.** The tests (`tests/test_slabs.py`) cover:
  - the Method 3 tables against the office sheets and their smooth runs;
  - the coefficients of Table 406.5.2;
  - a two-span strip against w l²/8 and 0.0957 w l²;
  - the steel of a strip by hand;
  - the thickness tables;
  - the DDM shares at the table points;
  - a slab object over six bays cut into the same six panels as six objects;
  - a regular floor on flexible beams designed by the DDM;
  - the marks.
- **ETABS.** Real ETABS runs (see [ETABS test runs](etabs_test_runs.md)):
  - `t34 slabs.json`: `sdt slabs` then `sdt plans` with the slab marks, against a saved reference. 53 panels.
  - `bottom slabs.json` on the BOTTOM model: 259 floor objects cut into 1,233 panels on a grid skewed about 55 degrees. Of these:
    - 860 are one-way and 369 two-way;
    - 401 are flagged as failing, mostly the 125 mm slab under the NSCP minimum thickness.

## Not covered yet

- Flat plates and flat slabs (punching shear), post-tensioned, ribbed and waffle slabs, and tank slabs.
- Deflection by calculation: the thickness is checked against the NSCP minimum.
- Point and line loads on a slab (a wall on it, for instance): only the uniform loads of the floor objects are read.
- Bar cut-off points and laps.
