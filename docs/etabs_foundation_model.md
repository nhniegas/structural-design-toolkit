# Foundation Model (`sdt foundation`)

`sdt foundation` makes a second ETABS model for the foundation. It holds only the support joints of the superstructure, each loaded with what the superstructure delivers there. You model the footings and mats as shells in that model and design them in ETABS, apart from the superstructure, so neither analysis is slowed by the other.

When the superstructure changes, the same command updates the forces of the foundation model and leaves what you modelled there.

Code: `etabs_api/workflows/foundation_model.py`.

## How to use it

1. Open the superstructure in ETABS (saved). It is analysed first if any load case has no results.
2. Run:

```powershell
sdt foundation
```

3. Answer the dialogs:

| Question | Choices |
|---|---|
| What to do | **Create** the foundation model, or **update** the forces of the one this model already has |
| Seismic cases | The static seismic cases only, or also the response spectrum cases with signs (see [Response spectrum cases](#response-spectrum-cases)) |
| Live load | The full live load, or the reducible live load reduced by NSCP 2015 205.5 or ASCE 7-10 4.7 (see [Live load reduction](#live-load-reduction)) |
| On an update | The same choices as before, or choose again |

The foundation model is saved beside the superstructure as `<model> - FOUNDATION.EDB`, and ETABS has it open when the command ends. The file of the superstructure is not changed.

## What the foundation model holds

| Item | In the foundation model |
|---|---|
| Structure | Nothing but the support joints: every frame, shell and other joint is deleted |
| Support joints | At their coordinates, with **no restraint**: what you model there carries them. The model is unstable until the footings and their soil springs are in |
| Loads | On each support joint, in every load pattern, the opposite of its reaction in the superstructure |
| Moments | Follow the model exactly: a fixed support delivers its moments, a pinned support delivers none |
| Grids, stories, materials, sections | Kept |
| Gravity load patterns | Kept with their type. `SELFWEIGHT` keeps its self-weight multiplier, so the weight of the footings you model is added to the column loads |
| Seismic and wind patterns | Become plain patterns (type Other) that hold the exported loads. ETABS cannot generate them on a model with no structure and no mass |
| Wind case with several steps | One pattern for each step: `WX-1` to `WX-12`. A seismic drift pattern with both eccentricities has two: `EQXSD-1`, `EQXSD-2` |
| Combinations | Made again from those of the superstructure (see below) |
| P-delta | Off: the reactions already hold it |
| Modal and response spectrum cases | Removed; a spectrum case that is exported becomes a static case of the same name |

**The sign of a load.** A support pushes the structure up with its reaction, so the structure pushes the foundation down with the same force. A column reaction of 300 kN upward becomes a load of 300 kN downward on its joint.

## Combinations

- A combination of cases that are all in the foundation model is made as it was, with the same name and factors.
- **A wind combination becomes 12.** A case with several steps is 12 sets of forces, so a linear combination that uses it becomes one combination for each step, `ULS 103 ... 0.5 WX [1]` to `[12]`, and their envelope under the name of the combination. What used that name, such as an envelope of the strength combinations, still finds it.
- **A combination that uses a case left out is not made.** With the response spectrum cases left out, the `RSA` combinations are not in the foundation model; the notes list them. An envelope keeps what is left of it.

## Response spectrum cases

The reactions of a response spectrum case are peak values with no sign. They are not one set of forces in equilibrium: their vertical sum is not zero, and every support has its largest value at once.

- **Static cases only (the first choice).** The spectrum cases and their combinations are left out. The static seismic cases carry the earthquake.
- **With signs.** Each component of a spectrum reaction takes the sign of the static seismic case of the same direction at that joint: the one with the largest base shear in that direction, and a drift pattern for a drift spectrum case. The pattern then has the size of the spectrum and the sense of the static case. The check lists these patterns as information, since they do not add up to a base reaction.

## Live load reduction

The reactions of the live load cases are unreduced. With a reduction, the reducible live load at each support is multiplied by the factor of the column that stands on it, by the same rule and tributary areas as the column design (see the [concrete guide](concrete_beam_column_design.md#live-load-reduction)). Supports of walls are not reduced. The factor of each support is in the workbook.

## The equilibrium check

Every create and update ends with a table: for each pattern, the sum of the loads applied, against the base reaction of its case in the superstructure.

| Result | Meaning |
|---|---|
| `OK` | The loads add up to the base reaction, to one part in a million |
| `FAIL` | They do not: a reaction was not exported. The usual cause is a support that is a spring, not a restraint |
| `INFO` | A reduced live pattern (the share of the unreduced base reaction is given), or a signed spectrum pattern |

This is the check against forces lost in the export. Read it after every update.

## Updating the forces

After the superstructure changes: open it, and run `sdt foundation` again with **Update**. It is analysed if needed, then:

- the exported loads on the support joints are replaced, in every pattern;
- shells, springs, restraints and loads of your own in the foundation model are left;
- a support that is new gets a joint; a support that is gone has its joint removed, or left with no load when something is attached to it, and is reported;
- supports are matched by where they are (within 1 mm), so a joint that ETABS renumbered is still the same support;
- the combinations are made again only when those of the superstructure changed.

A table then says how the loads of each pattern moved: the vertical total before and now, and the largest change of a force and of a moment at any support. A load that moved by less than 0.01 kN counts as the same. Expect small changes in patterns you did not touch: a change of dead load changes the seismic weight and the P-delta stiffness, and with them the seismic and wind reactions.

Creating again on a model that has a foundation model replaces it, with everything modelled in it. The command says so and asks before it does.

## Files

| File | What it holds |
|---|---|
| `<model> - FOUNDATION.EDB` | The foundation model |
| `<model> - FOUNDATION.foundation.json` | The supports (joint, coordinates, restraint, the column or wall on it and its size), the choices, and the loads of every pattern. The update, the framing plans and the 3D model read it |
| `<model> - FOUNDATION - Reactions.xlsx` | Supports, the equilibrium check, the changes of the last update, the loads applied, the reactions of every load case and of every combination at every support (kN, kN-m) |

## Limits

- **Spring supports.** Only restrained joints are supports here. A reaction at a spring is not exported, and the equilibrium check fails for it.
- **The combinations of the foundation model are not flagged for design.** Choose the design combinations in ETABS.
- **Area and line loads on the ground.** Loads that reach the supports through the superstructure are in the reactions. A slab on grade that is not part of the superstructure model is not.
- **A combination that mixes two stepped cases** becomes one combination for each pair of steps, which can be many.
- The foundation is not designed by this command: it prepares the model.

## Tests

`tests/test_foundation_model.py`: the sign of a load, pinned and fixed supports, wind steps, the signed spectrum case, the live load reduction, the equilibrium check, the combinations, the matching of supports and the report of changes. They need no ETABS.

In ETABS, on the test model: create, then update with no change (no load moved), then update after 100 kN was added to one load pattern (its vertical total moved from 787.5 to 887.5 kN). The foundation model had the 12 support joints with no restraint, no frame or shell, 42 patterns, 428 combinations and P-delta off.
