# The model from a DXF, with its framing (`sdt model`)

`sdt model` reads the framing plans of one DXF file and builds the model in two steps:

1. **The stories, grids, columns and walls**, as described in [Grids, columns and walls from a DXF](etabs_grid_column_model.md). Run again on a revised drawing, this step updates the model.
2. **The framing of every floor:** the girders, beams and slabs that use the least concrete while keeping frame action. The best few framings are drawn side by side and ranked; you pick one, and it is built with its loads.

`sdt grids` is the old name of the same command. When it asks whether to frame the floors, answer No to get only the first step.

Run `sdt setup` first. The framing takes its member sizes, slab thicknesses and load patterns from the setup.

Code: `etabs_api/workflows/model_framing.py` (the command and ETABS) and `etabs_api/workflows/framing.py` (the framing itself, with no ETABS).

## What to draw

Draw the layers of [`sdt grids`](etabs_grid_column_model.md#drawing-rules). Each plan can also have:

| Layer | What to draw |
|---|---|
| `S-SLAB` | The outline of the floor slab, as a closed polyline. Arcs are read as straight pieces of about 2 m |
| `S-OPENING` | Each opening in the slab, as a closed polyline (arcs as for the slab) |
| `S-LOAD` | Each occupancy zone, as a closed polyline with its name inside it as a text (`OFFICE`, `STORAGE`). Several zones can share a name; the loads of each name are typed once |

A plan with no `S-SLAB` outline is framed between its outer girders, with no overhang. When the lowest plan has none while the plans above do, `sdt model` asks whether it is a slab on grade: then that floor gets **tie beams** only (`FTB` sections of the setup, along the girder lines) and no slab.

## How a floor is framed

All dimensions are in millimetres. The code minimums are those for deflection without calculation; `sdt design` checks the sizes and resizes them.

- **Girders.** A girder joins each column (or wall end) to the nearest one along each of its framing directions. With grid lines in the drawing these are the directions of the grid lines through the column (and the square to it where only one passes; the nearest grid line's where none does), so girders run along the grids at any angle: a plan with wings at 26° and 36° frames along its own grids. Without grid lines they are X and Y. A column that is off the line by up to the tolerance you give (300 mm offered) still frames. A girder that meets a wall stops at it. No girder is made along a wall, with its middle off the slab, across a large void, or longer than the deepest setup girder can span (ℓ/16). Where a column is still held only one way, a girder is added to the nearest member that way (not under 1.5 m).
- **Transfer girders.** A column of the story above with no column under it gets a transfer girder. If it stands on a girder, that girder becomes the transfer girder. Otherwise a new one crosses its bay the short way. Each one is named in the notes.
- **Openings.** An opening at least as large as the size you give (1000 mm offered) that lies inside one bay gets four trimmer beams. Two run girder to girder along its sides, across the short span of its bay, and two headers run between them. A larger one that crosses girders (an atrium) is a void: the girders stop at it and its edge is framed like the slab edge (below). A smaller opening is only cut out of the slab; in ETABS it is drawn as an opening over the floor.
- **Each bay** between girders and walls is framed one of two ways:
  - as a **two-way slab**, when its long clear span is at most twice the short one, with the thickness of NSCP Table 408.3.1.2 (ACI Table 8.3.1.2). Here αfm is the mean αf of its four girders (NSCP 408.10.2.7; ACI 8.10.2.7), each girder taken as a plain rectangle; its flange is left out, which gives a lower αf. A panel at the slab edge whose edge girder has αf under 0.8 is made 10 % thicker (NSCP 408.3.1.2.1; ACI 8.3.1.2.1). Where αfm is 0.2 or less, Table 408.3.1.1 applies;
  - as a **one-way slab** on beams along the bay's own X or Y (the directions of its girders) at equal spacing, or on its girders alone, with the thickness of NSCP Table 407.3.1.1 (ACI Table 7.3.1.1): ℓ/24 for a span with one end continuous, ℓ/28 for both ends. Beams no closer than 1.2 m are tried.
- **Slab edges and voids.** An edge beam runs along every straight piece of the slab outline and of each void, split where a girder, a beam or a column reaches it. Where the edge is beyond the column line, a cantilever beam runs from the column out to it along the column's framing direction, up to the cantilever length you give (3 m offered). An edge with no support over more than 12 m gets more: cantilever beams square to the edge from each column within 8 m of it, then, where a stretch is still over 12 m, one about every 6 m from the first member met inside, at most 8 m long. A slab edge within 1 m of a girder in line with it is a plain slab overhang, with no beams. Bays the edge beams close are framed like any other bay. Edge stretches that nothing within 8 m can carry are listed as problems: they need columns or walls that the drawing does not show.
- **Member sizes** are rule sizes: the depth of NSCP Table 409.3.1.1 (ACI Table 9.3.1.1). That is ℓ/16, ℓ/18.5 or ℓ/21 by the ends that continue into a member in line, and ℓ/8 for a cantilever. The depth is rounded up to the setup sizes (girders and transfer girders from the G range, the others from the B range). The width is the least setup width with b ≥ 0.3 h (NSCP 418.6.2.1(b); ACI 18.6.2.1(b)). For fy other than 420 MPa, the span ratios are multiplied by (0.4 + fy/700) (NSCP 407.3.1.1.1 and 409.3.1.1.1; ACI 7.3.1.1.1 and 9.3.1.1.1).
- **The architect's limit.** You can give the deepest member allowed at each floor. A framing with a deeper member is listed with that problem.

## The options and their ranking

What you hold decides which framings are tried:

- **Least concrete:** every slab thickness of the setup is tried, and the volume decides.
- **Hold a slab thickness:** only that thickness. The beam spacing follows from it.
- **Hold a beam spacing:** the beams are no farther apart than it. The slab thickness follows from it.

For each slab thickness there are three schemes:
- **least concrete bay by bay:** each bay takes the option of least concrete. Neighbouring one-way bays keep one beam direction where the other direction costs no more than 5 % more;
- **beams along X;**
- **beams along Y.**

A floor holds one slab thickness. Floors with the same columns, walls, slab, openings, zones and planted columns are typical floors. They are framed once, and each copy counts in the volume.

The volume is the slab plus the members below it. The ranking puts:

1. framings without problems first (a bay the slab cannot span, a member deeper than the setup or the architect allows);
2. then those that meet the frame action rules:
   - every column held by a girder along X and along Y;
   - no girder line of a single bay;
   - no girder with a clear span under 4d (NSCP 418.6.2.1(a); ACI 18.6.2.1(a));
   - the centre of rigidity of the columns and walls within 10 % of the plan size of the centre of the slab. The 10 % is a rule of the toolkit, not of the code;
3. then by volume.

The table also gives the formwork area and the number of members.

**In ETABS.** You choose how many of the best framings to build and analyse (2 offered, 0 for none). Each one is built in a copy of the model, `<model> - OPTION n.EDB`, and analysed once. The copy reports:
- its drift on the static drift patterns, with the strength-level stiffness (NSCP 406.6.3.1.1; ACI 6.6.3.1.1), as `sdt drift` reads it;
- the periods of the drift patterns;
- the largest ratio of the maximum to the average story drift.

A framing that passes drift ranks first. One over its limit by no more than 10 % is marked **marginal**, not dropped, since `sdt design` can resize it.

## The run

```powershell
sdt model
```

It asks for the following:

1. **The drawing and the model**, as `sdt grids` does, plus the slab, opening and load layers. Then it applies the stories, grids, columns and walls.
2. **Whether to frame the floors now.**
3. **What is held**, the tolerance for columns off a line, the size from which openings get trimmer beams, how many options to draw and to analyse, and the deepest member allowed at each floor.
4. **The loads of each zone:** the superimposed dead load and the live load in kPa, and the live load pattern (reducible, not reducible or roof). `(no zone)` is the slab outside every zone.
5. **Which option to build.** The list is in the dialog, in `<model> - framing options.txt`, and drawn in `<model> - framing options.dxf`. That file has one row per option and one plan per typical floor, with the slab of each bay marked. Choose None to keep the model without framing.

The option you pick is built in the model:
- the girders (`G_` sections) and the beams (`B_` sections), created when the setup has not made that size;
- the floors between them, each a `S_<t>_<concrete>` slab; the one-way `_1W` slab where the bay is one-way, when the setup made it;
- the loads of each zone on the patterns `SIDL` and the live pattern chosen;
- a rigid diaphragm `D1`.

If a floor already has beams or slabs, it asks before replacing them.

## Limits

- An edge piece that runs from a column to a free corner is made an edge beam (sized ℓ/16 to ℓ/21), not a cantilever (ℓ/8): `sdt design` checks it.
- The frame action screening counts a girder line of one bay as a finding; on plans at several angles there are many. They do not stop the framing.

- The framing works on bays that are close to rectangles. A skewed bay is framed on the rectangle around it.
- The girders run along X and Y. A plan at an angle frames, but its girders follow the columns, not a rotated grid.
- Walls are supports for the slabs and beams. They are not designed here (`sdt walls` designs them).
- The frame action rules are a screening. The analysis of the options, and then `sdt design` and `sdt drift`, decide.

## Tests

`tests/test_framing.py` covers the following, with no ETABS:
- the code ratios;
- the girders, bays, overhangs, transfer girders and trimmers;
- the schemes, typical floors and ranking;
- the reading of the slab, opening and load layers;
- the slab pieces and the options drawing.
