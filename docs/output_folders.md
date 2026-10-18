# Output folders

Every command sorts the files it writes into subfolders by kind. A command that asks for an output folder writes into subfolders of that folder; a command that writes beside the model (drift, irregularity, quantities, the foundation reactions, the plan changes of `sdt grids`, the log of `sdt design`) writes into subfolders of the model's folder.

| Subfolder | Holds | Examples |
|---|---|---|
| `Results` | Workbooks (`.xlsx`) | `<model> - Beam Design.xlsx`, `<model> - Column Design.xlsx`, `<model> - Deflection.xlsx`, `<model> - Concrete Quantities.xlsx`, `<model> - FOUNDATION - Reactions.xlsx` |
| `Calculations` | Calculation reports (`.pdf`) | `<model> - Beam Calculations.pdf`, `<model> - Column Calculations.pdf`, `<model> - Project Report.pdf` |
| `Schedules` | Beam and column schedules (`.dxf`) | `<Story>_Girder_Schedule.dxf`, `<Story>_Beam_Schedule.dxf`, `Column_Schedule.dxf` |
| `Drawings` | Framing and foundation plans (`.dxf`), the 3D model (`.step`) | `<model> - Framing Plans.dxf`, `<model> - 3D Model.step` |
| `Summaries` | Summaries, logs and text reports (`.txt`) | `<model> - Drift.txt`, `<model> - Irregularity.txt`, `<model> - DESIGN log.txt`, `<model> - Beam Design summary.txt`, `<model> - plan changes.txt` |

Files that later commands read back stay where they always were: `<model> - design data.pkl` at the top of the output folder, and the inputs saved beside the model (`<model>.setup.json`, `<model> - FOUNDATION.plan.json`). Model copies made by a command (`- TAGGED.EDB`, `- DESIGN.EDB`, `- FOUNDATION.EDB`) are also saved beside the model.

The standalone checks (`sdt pilecap`, `sdt composite`, `sdt steel`, `sdt wind`) ask where to save their report, and save it there.

The subfolders are made when a command first writes into them. Files written by versions before 0.7.0 stay where they are.

The code is in `utilities/output_folders.py`, tested in `tests/test_output_folders.py`.
