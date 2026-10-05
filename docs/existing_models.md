# Working on a Model the Toolkit Did Not Set Up

A model made with `sdt setup` and `sdt tag` follows conventions the commands rely on: combinations named `ULS`, `DEF`, `DRIFT` and `WDRIFT`, sections named like `G_300X500_C04_G60`, members tagged like `2GX-1`, and the setup inputs saved beside the model in `<model>.setup.json`.

A model built by hand, or by someone else, may have none of these. Every command still runs on it. What the model already holds is read from it. What it does not hold is asked, once, and saved beside the model. Nothing falls back to a hidden default.

## The readiness dialog

Before its own questions, a design command looks at the model and, if anything is missing, shows one dialog:

- **Found:** what the command can use as it is.
- **Missing:** what it will ask next.
- **Check:** things to know before going on, for example wall panels (not designed) or P-delta switched off.

Choose **Continue** or **Stop**. On a model that has everything, the dialog is not shown and the command asks what it always asked.

## What is asked when it is missing

| Missing | What the command does |
|---|---|
| Strength combinations named `ULS` | You pick the combinations to design for from the model's own. Envelope combinations are left out of the list, because an envelope is not one set of forces |
| Deflection combinations named `DEF` | For each of the four cases (dead; dead + live; dead + 25 % live; dead + roof live) the first choice is a combination of the model with exactly those unfactored factors, or, when there is none, **Let the toolkit add it**. The other gravity combinations follow with their factors shown (for example `1.4 D`): a factored combination gives deflections that are too large, so it is never the choice made for you. A combination added by the toolkit is built from the load pattern types of the model |
| Drift combinations named `DRIFT` / `WDRIFT` | In `sdt drift` and at the end of `sdt design`, you pick the seismic and wind combinations to check the drift on |
| Member names | Members that still have their ETABS number: tag them now, design them with their numbers, or leave them out (see below) |
| Section size ranges | `sdt design` shows every section family with a range to confirm or change. The dialog lists the sizes the model has now, and the range shown always holds them |
| Seismic values Z, Ct, R | Read from the model's UBC 97 seismic patterns. Asked only when the model does not hold the value, or when the model's value differs from one saved earlier. Patterns with user defined Ca and Cv keep no zone factor in ETABS, so Z is asked once for such a model and saved |

Your answers are saved in `<model>.setup.json` under `"model"`, and offered again the next time (**Use these** or **Choose again**).

## Members without tags

`sdt tag` gives each member its level, type and number (`2GX-1`, `3-C5`), and the schedules use those names. When members still have their ETABS numbers, the design command offers three choices:

- **Design them as they are** (the first choice: it changes nothing in the model). The results and schedules then list the ETABS numbers. ETABS splits a girder where a secondary beam frames into it; without tags, the pieces of one beam line are found from the geometry (beams in line that meet at a joint with no column), so deflection and resizing still treat them as one beam.
- **Tag them now**, in this model. Tagging renames the members of the open model, unlocks it and drops its analysis results; in `sdt design` it happens before the working copy is made, so it is the one choice that changes your original model.
- **Leave the unnamed members out** (when only some are unnamed).

## Sections with other names (`sdt design`)

The loop needs the size, materials and family of every section it may resize.

- A section named as `sdt setup` names it is read from its name.
- Any other rectangular or circular concrete section is read from ETABS: its dimensions, its concrete and rebar materials and its cover.
- The family of a beam section comes from where the member is: a **girder** when it frames into a column, a **beam** when it sits on other beams, a **tie beam** on the bottom-most level. Columns are rectangular or circular by shape.
- Sections of another shape (steel, tees, section designer) are not resized; the dialog lists them.

A new size is created under the setup name (for example `G_300X550_C04_G60`), with the concrete, rebar material and cover of the section it replaces, and **no stiffness modifiers on the section** (modifiers assigned to the frame objects stay as they are). If a section of the model carries modifiers of its own, the dialog says so, since a member that gets a new size then loses them.

## Seismic loads of another kind

The checks of the response spectrum scaling, the period cap and the base shear coefficient read the UBC 97 seismic patterns. When the model's seismic loads are of another code, or user loads:

- `sdt analyze` reports those checks as not applicable, and scales the spectrum only where a static seismic case exists.
- `sdt drift` asks for R, and uses it for ΔM = 0.7 R ΔS.
- The design still runs on the combinations you picked.

Drift on combinations you picked uses their load cases as they are. Where a seismic case in them has the capped period of the strength design (NSCP 208.5.2.2), the drift is on larger forces than NSCP 208.6.5.2 requires, which is on the safe side; the report says so. The static / response spectrum question of `sdt drift` is not asked: the spectrum is scaled when a picked combination has a response spectrum case.

## What the summary tells you

Every summary ends with where the inputs came from:

- **Read from the model:** for example the strength combinations under `ULS` names, Z and Ct.
- **Answered by you:** for example the combinations you picked, the size ranges.
- **Assumed:** for example deflection combinations added by the toolkit.

## Not covered

- **Shear walls** are not designed. The readiness dialog and the summaries say how many wall panels the model has.
- **Envelope combinations** cannot be designed for.
- **Steel and composite frames** in the ETABS model are not designed by `sdt beams` / `sdt columns` (see the [steel and composite modules](steel_design_documentation.md) for the standalone checks).
