# Story Drift (`sdt drift`)

`sdt drift` (or `python main.py drift`) checks the story drift of the model open in ETABS using the drift stiffness. It works on the model itself, not a copy, and puts the model back as it was when it finishes.

## Why a separate run

ETABS keeps stiffness modifiers per member, not per load case. Drift with a different cracked-section stiffness than the strength design therefore needs its own analysis. `sdt check` reports the drift of the model as it is, with its strength modifiers; `sdt drift` reruns the analysis with each drift stiffness.

## What it does

1. Asks:
   - where to read the drift: the diaphragm centre of mass, or the four outer corners;
   - which seismic drift combinations to check: static (`EQXSD`, `EQYSD`), response spectrum (`RSAXD`, `RSAYD`), or both. With static only, the spectrum is not scaled, which saves one analysis per level. The wind drift combinations are always checked;
   - the wind drift limit, h / 400 by default;
   - a confirmation, since it changes the model while it runs;
   - on a model with no `DRIFT` / `WDRIFT` combinations: which of the model's seismic and wind combinations to check the drift on, and R when the model has no UBC 97 seismic pattern to read it from. The choice is saved with the model. The static / response spectrum question above is then not asked. Where a seismic case in the picked combinations has the capped period of the strength design (NSCP 208.5.2.2), the drift is on larger forces than 208.6.5.2 requires, which is on the safe side; the report says so.
2. Remembers the frame modifiers, the response spectrum scale factors, and whether the model had results.
3. Judges your modifiers: effective I (frame × section) of 0.35 for beams and 0.70 for columns, and mass and weight at 1.
4. For each stiffness level:
   - sets the cracked-section I22 and I33 of every beam and column;
   - runs the analysis, and scales the spectrum cases (`RSAXD` and `RSAYD` to the drift patterns `EQXSD` and `EQYSD`, as `sdt analyze` does);
   - reads the drift of the `DRIFT` and `WDRIFT` combinations.

   | Level | Beams | Columns | Basis |
   |---|---|---|---|
   | As modelled | yours | yours | the modifiers you assigned, unchanged |
   | Strength | 0.35 | 0.70 | ACI 6.6.3.1.1 (NSCP 406.6.3.1.1) |
   | Service | 0.49 | 0.98 | 1.4 times, at most the gross section, ACI 6.6.3.2.2 |

5. Puts the modifiers and scale factors back and, if the model had results, analyses it again. It is left ready for the strength design.
6. Prints the report in the terminal and saves it beside the model as `<model> - Drift.txt`. A separate window then shows the summary: the checks and the failures of each stiffness level.

`sdt design` runs the same check once at the end, on the final sizes (see [design loop](etabs_design_loop.md)).

`sdt setup` gives the frame sections no modifiers; you assign them to the frames in ETABS. The "As modelled" level shows the drift with them, and the report says whether they match the code values. For the strength and service levels, the stiffness set is the **effective** one: the member modifier is set to the target ÷ section modifier. So the result is exact even if a section carries a modifier from an older setup.

## Where the drift is read

| Reference | How |
|---|---|
| Diaphragm centre of mass | `Diaphragm Center Of Mass Displacements`: the change of the centre-of-mass displacement from the story below (the base below the lowest story), over the story height |
| Outer four corners | At each story, the column tops farthest along the two diagonals (largest x + y, x − y, −x + y, −x − y). Re-entrant corners of L or T plans are not picked. The drift is ETABS's `Joint Drifts` at those joints |

For the spectrum combinations, ETABS gives Max and Min envelopes. The centre-of-mass drift uses the difference of the envelope displacements, a close approximation; the joint drifts come from ETABS directly. Every story is included, those below the ground level too.

## Checks (per level)

**A model with no DRIFT combinations.** `sdt drift` then asks what to check the drift on:

- **The drift load cases** (`EQXSD`, `EQYSD` and the wind patterns), when the model has them. The seismic ones use the forces of the period without its cap, which is what NSCP 208.6.5.2 allows for drift. No gravity load acts with them, so the P-delta effect of the 208.6.4.1 combinations is not in the values; the report says so.
- **Combinations you pick.** Strength combinations usually carry the forces of the capped period, and give a larger drift than the code asks for.

**The service level stiffness** is asked with the wind limit: the factor on the strength level, 1.4 by ACI 6.6.3.2.2. Give another value when your office uses one; it is capped at the gross stiffness, and the report names it as yours. At the service level a member that was modelled stiffer than the level keeps its own stiffness: a beam modelled at 0.5 Ig is not set to 0.49.

**Every story is listed.** Under the checks of each stiffness level the report has a table with one row per story, the top first: the largest seismic drift ratio of the combinations checked, ΔM = 0.7 R times it, the combination it comes from, and the same for wind as h/x. The checks above the table give only the worst story of each load case, and one very flexible level, such as a small deck at the top, would otherwise hide that the storeys below it are over the limit too.

- **Seismic** (`DRIFT` combinations: 203-5 and 203-7, E from `EQXSD`, `EQYSD`, `RSAXD` and `RSAYD`, ρ = 1.0): ΔM = 0.7 R ΔS, at most 0.025h when T < 0.7 s, otherwise 0.020h (NSCP 208.6.5.1). T is the drift pattern period from the same run. The worst combination of each case is reported.
- **Wind** (`WDRIFT` combinations: 203-3, 203-4 and 203-6 on `WX` and `WY`): at most h / the typed limit. NSCP 207 sets no wind drift limit.

The drift combinations come from `sdt setup`; a model without them stops with a message to run `sdt setup` first. Every value (stiffness, factor 1.4, limits) is in `design/code_config.py`.

## Tests

`tests/test_drift_check.py` covers the stiffness levels, the corner search (rectangle and L plan), the centre-of-mass and corner drifts, the level checks, and the effective (member × section) modifiers. The full run, with both references and the restore, was tested on a copy of the test model.
