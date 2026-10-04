# Grids, Columns and Walls from a DXF

`sdt grids` (or `python main.py grids`) reads framing plans from one DXF file and builds the stories, grid lines, columns and walls of an ETABS model. Beams are drawn by hand in ETABS afterwards. When the plans are revised, running it again updates the model.

Code: `etabs_api/workflows/grid_column_model.py`. Sample drawings: `edb/plan dxf/`.

## Drawing rules

All framing plans go in one file, side by side, in millimetres.

| Layer | What to draw |
|---|---|
| `S-STORY` | One closed rectangle around each plan and, inside it, one text: `STORY 2F   HEIGHT 4500` |
| `S-ORIGIN` | One point per plan at the same building point, so the plans stack correctly |
| `S-GRID` | One line per grid, at any angle and of any length, with its label as a text at one end. The bubble is optional |
| `S-COLUMN` | Each column as its outline: a closed rectangle at any rotation, or a circle. It need not be on a grid |
| `S-WALL` | Walls and core walls, drawn either way below |

Walls:

- **Centre lines with a width.** A polyline along the wall centre lines, with its width set to the wall thickness. One polyline can trace a whole core; each straight piece becomes a wall.
- **Outline.** A closed rectangle around one wall. The short side is the thickness.

Other rules:

- Plans go left to right, lowest level first.
- `HEIGHT` is the height of that story, from the level below up to it.
- The columns and walls in a plan are the ones below that level, the ones that support it.
- The layer names can be different; the script asks for them.
- A grid shown on several plans is taken from the lowest plan that shows it.

## Running it

```powershell
.venv\Scripts\Activate.ps1
sdt grids
```

`python etabs_api/workflows/grid_column_model.py` does the same.

1. Pick the DXF file.
2. Confirm the layer names and type the concrete and rebar grade (ksi) of the columns and walls.
3. Choose whether to add a footing level (see below); if so, type the embedment depth (mm) and the name of the ground level (`GF` by default). Both are remembered.
4. Choose the model: the one open in ETABS, or a new blank model. For the open model, choose whether the changes go **into this model** or **into a copy**: the copy is saved where you choose (`<name> - REV.EDB` is offered) with the model's saved setup inputs, and the model it came from is left as it is on disk. ETABS has the copy open afterwards.
5. A dialog lists what will change. Choose **Apply these changes** or **Cancel**.
6. The closing summary, shown in a window and printed in the terminal, lists what was done. The full list of changes is saved beside the model as `<name> - plan changes.txt`.

Closing any dialog cancels the run without changing the model.

## What is created

| Item | Rule |
|---|---|
| Seismic and wind patterns | Their bottom and top story follow the stories (bottom = the ground level). "Per Code" seismic patterns become "User Defined" with the same Ca and Cv, because ETABS resets their source distance when that table is written |
| Stories | Names and heights from the plan titles. On a model with members, the base elevation and every story height are updated in place and ETABS moves the levels above with their members; stories can only be added or removed while the model has no members |
| Grid lines | General grid lines in the first grid system. The drawing replaces them on every run |
| Column sections | `CR_<width>X<depth>_<concrete>_<rebar>` or `C_<diameter>_...`, created if missing, with the same rebar data as the model setup (no stiffness modifiers) |
| Columns | From the level below up to the story of the plan. Depth is along the ETABS local 2 axis; the rotation is kept between 0 and 90 degrees |
| Wall sections | `SW_<thickness>_<concrete>_<rebar>`, thin shell, created if missing |
| Walls | One panel per straight wall, over the story height |

## Footing level

Without it, the base of the model is the ground: the lowest plan's `HEIGHT` is measured from elevation 0.

With a footing level of embedment depth D:

- the base goes down to -D;
- a ground story (`GF`, height D) is added below the lowest plan and ends at elevation 0, so the ground level is the zero reference;
- the drawn stories keep their heights above it (the sample's `2F` stays 4500 above `GF`);
- the columns and walls of the lowest plan are repeated in the ground story, from the footings up to the ground level.

The ground level name must not be one of the drawing's stories. Use the same choice on every run of a revision, so the ground story columns are matched as unchanged. A changed depth on a later run moves the base and stretches the ground story columns; the ground level stays at 0.

## Revisions

Run the script again with the revised drawing on the model that is open. Story by story, each drawn column is paired with a column in the model:

1. **Same place:** unchanged, or updated in place when its size or rotation differs.
2. **Same grid intersection:** moved. The model's column is found by the grid labels it sat on before, so a grid, or a whole wing, can shift or turn and its columns are still recognised.
3. **Off the grids:** the nearest remaining pair within 2.5 m is treated as moved.
4. Anything left is added or removed.

A moved column is moved by its joints, so the beams framing into it follow and its assignments are kept. If one of its joints is shared with a column that stays put, it is replaced instead.

Walls are simpler: a wall with the same two ends is unchanged, or updated when its thickness differs. A wall that moved or changed length is removed and drawn again.

Check the list in the confirmation dialog before applying, particularly for a large revision: a column that could not be paired shows as one `REMOVE` and one `ADD`.

## Limitations

- Stories are not changed once the model has members. If the drawing's stories differ, the run reports it and leaves them.
- Beams are not created or reconnected. After a column is removed or replaced, reconnect the beams by hand.
- Loads and assignments on a wall that is removed and drawn again are lost.
- Only straight walls and rectangular or circular columns are read. Other outlines are skipped and listed as warnings in the report file.
- Wall openings, pier labels and meshing are not set.

## From your own script

```python
from etabs_api.workflows.grid_column_model import build_grid_column_model

building, changes, log, path = build_grid_column_model(
    r"C:\...\FRAMING PLANS.dxf", concrete_ksi=5, rebar_ksi=60, target="open",
    footing_depth=1500, ground_story="GF",  # footing level optional
    copy_path=r"C:\...\MODEL - REV.EDB")   # optional: change a copy, keep the open model
```

## Tests

`tests/test_grid_column_model.py` draws small plans with `ezdxf` and checks the reading (stories, origin, grid labels, column size and rotation, walls) and the comparison (moved grids, resized, added and removed columns, wall changes). It runs without ETABS.
