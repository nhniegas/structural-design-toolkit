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
| Folder | Where `<model> - Framing Plans.dxf` is saved |

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

## What it does not do

- Slabs: no slab outline, mark or span arrow.
- A beam whose section is not a concrete rectangle (a steel shape, a tee, a section designer section) is drawn as one dashed line; the summary lists those sections. A column that is not a rectangle or a circle is not drawn.
- The insertion point (cardinal point) and joint offsets of a member are ignored: every member is drawn on its joint-to-joint line.
- A beam that slopes is drawn by its plan projection.
- Braces are not drawn.
- The drawing has no title block and no paper space layout: it is model space geometry to bring into your sheet.

The plan was checked by reading the DXF back and rendering it. Open it in your CAD program and check the first one against the model before relying on it.
