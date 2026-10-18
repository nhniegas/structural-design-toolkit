# Framing Plans as DXF (`sdt plans`)

`sdt plans` (or `python main.py plans`) writes the framing plans of the model open in ETABS as one DXF file. It reads the model and changes nothing in it.

## How to use it

1. Open the model in ETABS.
2. Run:

```powershell
sdt plans
```

3. Answer the dialogs:

| Question | Choices |
|---|---|
| Grid lines | Yes; yes with the dimensions between the grids; no |
| Column marks | On every floor, or only where each column starts (the bottom-most level) |
| Plot scale | 1 : 100, 1 : 50 or 1 : 200. It sets the text height (2.5 mm on paper, so 250 mm at 1 : 100) and the length of the dashes. The plan itself is always drawn at 1 : 1 in mm |
| Foundation plan | Yes or no. Asked only when a plan of the foundation model of this model was saved (see [The foundation plan](#the-foundation-plan)) |
| Folder | The output folder: `<model> - Framing Plans.dxf` is saved in its `Drawings` subfolder |

A window then shows the summary: the floors, the counts of what was drawn and the file.

## What is drawn

The floors are drawn side by side in one row, from the lowest, each with its title (`2F FRAMING PLAN`) and elevation. A story with no beam, column or wall is left out.

| Item | How | Layer |
|---|---|---|
| Girders (beams that frame into a column) | Dashed multiline at the true width of the section | `S-GIRDER` |
| Beams (carried by other beams) | Dashed multiline at the true width | `S-BEAM` |
| Columns | Solid, at the true size and rotation; circular columns as circles | `S-COLUMN` |
| Walls | Solid, at the true thickness | `S-WALL` |
| Beam names and column marks, titles | Text | `S-TEXT` |
| Grid lines and bubbles | Centre lines | `S-GRID` |
| Grid dimensions | Between the grids, and overall | `S-DIM` |

**Beams are multilines** (the AutoCAD `MLINE` object, style `SDT_BEAM`). A multiline is one object with two parallel lines: when you drag an end point, both lines follow and the width is kept. The width is the scale of each multiline, so it can be changed in its properties. `EXPLODE` turns a multiline into two plain lines.

**Where a beam stops.** At a column, at the face of the column. Elsewhere, at the face of the girder that carries it: any beam whose centre line passes through that end, whether ETABS has it as one member from column to column or as pieces split at that joint. Beams that only continue each other in line are not cut where they meet.

**Skewed frames.** None of this assumes the frames run along X and Y. A beam that meets a girder or a column face at an angle is cut along that face, so both of its lines end on the face; a rotated column is cut along its own rotated face. A circular column, and a face nearly in line with the beam (under 20 degrees), get a square end. When you drag the end of such a beam in AutoCAD, the program redraws that end square.

**Columns of a floor.** A floor shows the columns below it, which are the columns of that ETABS story. A column that starts on a floor (with none below) is not drawn there, but the beams still stop at its face.

**Marks.** A beam has its ETABS name beside it, along the beam. A column has its mark without the level: `GF-C1` and `2-C1A` are marked `C1` and `C1A`. On a model that is not tagged, the ETABS numbers are used. With "only where each column starts", a column that runs from the footing to the roof is marked once, on the lowest plan, and a column planted on an upper floor is marked on the first plan that shows it.

**Grids.** Every visible grid line of every grid system, with the origin and rotation of its system. Each line runs across the plan with a bubble at its left or bottom end. Dimensions are drawn between the grids that run along X or along Y; skewed grids get no dimension.

## The foundation plan

The footings are in another model, the foundation model that `sdt foundation` makes, and ETABS has one model open at a time. So the foundation is saved first, then drawn:

1. Open the foundation model (`<model> - FOUNDATION.EDB`), with its footings and tie beams modelled.
2. Run `sdt foundation` and choose **Save the foundation plan**. A table lists the footings and tie beams found, and they are saved beside the model in `<model> - FOUNDATION.plan.json`.
3. Open the superstructure and run `sdt plans`. It finds that file, says when it was saved and what it holds, and asks whether to include it.

The foundation plan is drawn first, to the left of the floors, with the title `FOUNDATION PLAN` and its elevation.

| Item | How | Layer |
|---|---|---|
| Footings | The outline of each footing, with its mark at the lower left corner | `S-FOOTING` |
| Tie beams | Dashed multiline at the true width, stopped at the column faces, with its name | `S-TIEBEAM` |
| Columns | Solid, at the size and rotation of the column on each support, each with its mark | `S-COLUMN` |
| Footing sizes | One line for each mark under the title: `F1: 2,000 x 2,000 x 500 thk (9 footings)` | `S-TEXT` |

**What is one footing.** Each horizontal shell of the foundation model is a footing. Shells that touch are one footing, so a mat or a footing meshed into several shells has one outline and one mark.

**Footing marks.** They follow how the footings are modelled:

- A footing under one column is an isolated footing. Isolated footings of the same length, width and thickness share a mark: `F1`, `F2`, ...
- A footing under two columns or more is a combined footing and has a mark of its own: `CF1`, `CF2`, ... Its line under the title names its columns: `CF1: 7,000 x 2,000 x 600 thk, combined footing of C3, C2`.
- Marks are given in reading order, from the top of the plan down, then left to right. Sizes that agree to 10 mm are the same size. The length and width are the sides of the footing itself, also when it is turned.

**Tie beam names.** A tie beam you named in ETABS keeps its name. One that still has its ETABS number is named `FTB-1`, `FTB-2`, ... in the same reading order.

**Columns.** They are the columns that stand on the support joints, from the file of the foundation model, at the joints as they now are in that model. Every column of the foundation plan has its mark, whatever was chosen for the floors.

The saved plan is what the foundation model held when it was saved. After the foundation changes, save the plan again. When `sdt foundation` saves a plan, it reports the columns that have no footing under them.

## What it does not do

- Slabs: no slab outline, mark or span arrow.
- The foundation plan shows the footings as modelled: no bars, no dimensions to the grids, no footing schedule table. A wall footing is drawn by its outline, with no wall on it.
- A beam whose section is not a concrete rectangle (a steel shape, a tee, a section designer section) is drawn as one dashed line; the summary lists those sections. A column that is not a rectangle or a circle is not drawn.
- The insertion point (cardinal point) and joint offsets of a member are ignored: every member is drawn on its joint-to-joint line.
- A beam that slopes is drawn by its plan projection.
- Braces are not drawn.
- The drawing has no title block and no paper space layout: it is model space geometry to bring into your sheet.

The plan was checked by reading the DXF back and rendering it. Open it in your CAD program and check the first one against the model before relying on it.
