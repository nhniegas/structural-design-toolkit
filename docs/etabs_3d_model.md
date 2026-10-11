# 3D Model as STEP (`sdt 3d`)

`sdt 3d` (or `python main.py 3d`) writes the concrete of the model open in ETABS as 3D solids in one STEP file: columns, beams, walls and slabs at their true size, and the foundation when its plan was saved. AutoCAD and other CAD programs import the file. The command reads the model and changes nothing in it.

Code: `etabs_api/workflows/model_3d.py`. The solids are made with [build123d](https://build123d.readthedocs.io).

## How to use it

1. Open the model in ETABS.
2. Run:

```powershell
sdt 3d
```

3. Answer the dialogs:

| Question | Choices |
|---|---|
| Floor slabs | Yes or no. Columns, beams and walls are always included |
| Foundation | Yes or no. Asked only when a plan of the foundation model of this model was saved (see [The foundation](#the-foundation)) |
| Folder | Where `<model> - 3D Model.step` is saved |

A window then shows the summary: the count of each kind of member, the number of solids and of different shapes, the size of the file, and what was left out.

The 3D kernel takes some seconds to load, each time the command runs. No other command loads it.

If `sdt doctor` warns on its line `3D model (build123d)`, the kernel is missing on that machine: from the source, install it with `pip install build123d`.

## Opening the file in AutoCAD

Use `IMPORT` and choose the `.step` file. AutoCAD converts it in the background and shows a notice when the model is ready to place. The file is in mm.

## What is in the file

| Item | The solid | Group |
|---|---|---|
| Columns | From joint to joint, at the size and rotation of the section; circular columns as cylinders | The story of the column |
| Beams and girders | At the width and depth of the section, **the top on the line of the joints**, stopped at the faces of the columns and of the girders that carry them | The story of the beam |
| Walls | Each vertical panel at its thickness and its height | The story of the panel |
| Slabs | The outline of each floor shell, down from its joints by the thickness of its slab property | The story at its level |
| Footings | The outline of each footing, down from the level of its shells by its thickness | `FOUNDATION` |
| Tie beams | As the beams, stopped at the column faces | `FOUNDATION` |

**Groups and names.** The file is an assembly: the model, a group for each story and one for the foundation, and in each group its members. A member has its kind and its ETABS name: `COLUMN GF-C1`, `BEAM 2FGX-1`, `SLAB 12`, `FOOTING F1`, `TIE BEAM FTB-1`. Each kind has its own colour.

**One shape, many places.** Members of the same section and length are one shape in the file, placed once for each member. This keeps the file small: the test model, 168 solids, is 1.1 MB. In AutoCAD such members come in as references to one block.

**Beams follow the framing plans.** A beam stops where the framing plan stops it (see [Framing plans](etabs_framing_plans.md)), with a square end. A beam that slopes keeps its depth square to its own line.

## The foundation

The footings are in the foundation model, which is another file. Save its plan first: open the foundation model, run `sdt foundation` and choose **Save the foundation plan** (see [Foundation model](etabs_foundation_model.md#the-foundation-plan)). `sdt 3d`, on the superstructure, then finds the plan, says when it was saved and asks whether to include it.

The footings and tie beams are those of the foundation plan of `sdt plans`, with the same marks. After the foundation changes, save the plan again.

## What it does not do

- **Concrete only.** A frame whose section is not a concrete rectangle or circle (a steel shape, a tee, a section designer section) is left out. So is a floor that is not a slab, such as a deck. The summary counts them and lists the sections.
- **No reinforcing bars.**
- **Members overlap where they meet**: a slab and the beams under it, a column and the floor it passes through. Do not take concrete volumes from the file.
- The insertion point (cardinal point) and joint offsets of a member are ignored. A beam is drawn with its top on its joints and a column centred on its joints, which is how ETABS places them unless told otherwise.
- Openings in walls are not cut. A wall that is not a plain vertical panel, a ramp and a sloped slab are left out.
- A beam end that meets its support at an angle is cut square, not along the face.
- IFC is not written. See the future development list of the README.

The file was checked by reading it back: every solid valid, the count of solids, and the box around the model against the stories and the footings. Open the first one in your CAD program and check it against the model before relying on it.

## Tests

`tests/test_model_3d.py`: where a column, a beam, a sloped beam and a wall are; which members share a shape; the footings and tie beams from a saved foundation plan; and the STEP file, read back. They need no ETABS.

In ETABS, on the test model with its saved foundation plan: 47 columns, 102 beams, 3 slabs, 11 footings and 5 tie beams, 168 solids of 47 shapes, written in 9 seconds. Read back, the 168 solids were valid and their box ran from the underside of the combined footing (1,600 mm below the ground floor) to the roof.
