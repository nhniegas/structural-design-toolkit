# Concrete Quantities (`sdt quantities`)

`sdt quantities` reads the model open in ETABS and gives the volume of concrete of each story and of the building, the floor area, and the **concrete ratio in m3/m2**: the total volume over the total floor area. The model is not changed, and no analysis is needed.

Code: `etabs_api/workflows/concrete_quantities.py`.

## How to use it

1. Open the model in ETABS (saved).
2. Run:

```powershell
sdt quantities
```

3. Answer the dialogs:

| Question | Choices |
|---|---|
| Slab on grade | Its thickness in mm (100 is offered), or 0 to leave it out |
| Foundation | Yes or no. Asked only when a plan of the foundation model of this model was saved with `sdt foundation` |

The report is printed and saved beside the model as `<model> - Concrete Quantities.txt` and `.xlsx`, and a window shows the summary.

## What is measured

Each kind of member is measured so that no concrete is counted twice where members meet.

| Member | Volume |
|---|---|
| Columns | Section by length, joint to joint: floor to floor |
| Beams and girders | Width, by the depth **below the slab**, by the clear length between the faces of the supports. The part of a beam inside the slab is counted with the slab. A beam with no slab over it, or under an opening, is measured at its full depth |
| Slabs | The area of each floor shell, less its openings and less the part of each column section that lies in it, by the thickness of its slab property |
| Walls | Thickness by height by length |
| Footings | The outline of each footing by its thickness, from the saved foundation plan |
| Tie beams | Width by depth by the length between the column faces, from the saved foundation plan |
| Slab on grade | The footprint of the building by the thickness you type |

**The slab on grade** is not in an ETABS model as a rule, so it is not read from it. Its area is the **footprint** of the building: the outline of all the floor shells of the model seen in plan, with no opening taken out. On a model with no floor shell, it is the outline around the columns. The slab on grade is in these quantities only; it is not in the 3D model of `sdt 3d`.

**The foundation** is in another model. Save its plan first: open the foundation model, run `sdt foundation` and choose **Save the foundation plan** (see [Foundation model](etabs_foundation_model.md#the-foundation-plan)).

## The floor area and the ratio

| Item | Value |
|---|---|
| Floor area of a story | The area of its floor shells, less their openings |
| A level with no floor shell | No floor area. Its columns, beams and walls are counted. A level of grade beams is the usual case: its floor is the slab on grade |
| Ground floor | The footprint, when a slab on grade is counted |
| Total floor area | The stories and the ground floor |
| Concrete ratio | Total volume over total floor area, in m3/m2 |

The table by story also gives the ratio of each story, and the ratio of the stories alone, without the slab on grade and the foundation.

On the test model, three floors of 180, 180 and 165 m2 on a footprint of 180 m2:

```
stories 192.72 + slab on grade 18.00 + footings 29.52 + tie beams 3.58 = 243.82 m3
floors 525.00 + ground floor 180.00 = 705.00 m2
ratio = 243.82 / 705.00 = 0.346 m3/m2
```

## Limits

- **Concrete rectangles and circles only.** A frame with another section (a steel shape, a tee, a section designer section) and a floor that is not a slab (a deck) are not counted; the report says how many.
- **Walls and beams are not cut from each other**, and openings in walls are not taken out.
- **The slab is as modelled.** Where the floor shells stop at the column centre lines, the strip of slab outside them is not in the area or the volume.
- **A story above the ground with no floor shell** adds no floor area, which raises the ratio. Model its slab, or allow for it.
- **Stairs, parapets, ramps and sloped slabs** are not in the model as slabs, and are not counted.
- The insertion point and joint offsets of a member are ignored, as on the framing plans.
- This is a quantity for comparing schemes and checking a design against what is usual. It is not a bill of quantities.

## Tests

`tests/test_concrete_quantities.py`: a story measured by hand (columns, beams below the slab, the slab less its columns), an opening, a beam under an opening, a wall, a turned and a round column in a slab, the footprint, the slab on grade, the foundation, and the ratio. They need no ETABS.

In ETABS, on the test model with its saved foundation plan and a slab on grade of 100 mm: the values above, in 3 seconds. The second-floor slab, 180 m2 at 125 mm less its twelve columns, came to 22.20 m3; the eleven footings to 29.52 m3, which is their sizes by hand.
