# ETABS Analysis Checks

`etabs_api/workflows/model_analysis.py` runs the analysis of the model open in ETABS, scales the response spectrum cases to the static base shear, and checks the periods, the modal mass and the weight.

## How to use it

With the model open and saved in ETABS, from the project folder:

```powershell
sdt analyze
```

(`python main.py analyze` does the same). Two or three dialogs:

1. **Which model:** this model, or a copy saved beside it as `<model> - ANALYSIS.EDB`. The scaling changes the response spectrum cases, so choose the copy to keep the original as it is.
2. **Seismic zone factor Z and Ct** (ft units, 0.030 for concrete frames), for the Method A period. They are filled in from the model's own UBC 97 seismic patterns; when a value differs from the one saved with the model (`<model>.setup.json`), the dialog says so. On a model with no UBC 97 seismic pattern they are filled in from what was saved, or typed, and then saved with the model.

3. **90 % or 100 %,** only when the last `sdt irregularity` of this model found the structure regular in every type it checks. You are asked whether the three types it cannot check are also absent: in-plane discontinuity, out-of-plane offsets and non-parallel systems. Yes scales the spectrum to 90 % of the static base shear; No, or no such result, keeps 100 %.

On a model whose seismic loads are not UBC 97 patterns, the report lists what does not apply: the reset of the spectrum to g I / R, and the scaling when there is no static seismic case to scale to.

The report is printed in the terminal and the model is saved. A separate window then shows the summary: the scaling, the periods, the modal mass, the weight and every warning. If ETABS cannot run the analysis, a message says so and nothing is saved.

## What it does

1. Runs every load case. The command works in N-mm, whatever the units of the model, and puts the model's units back at the end; the spectrum scale g I / R is a length per second squared, so it depends on the units.
2. Puts every response spectrum case back to its unscaled factor, g I / R from the seismic patterns, so a second run does not keep the first run's scaling. For each response spectrum case (`RSAX` along U1, `RSAY` along U2), compares its base shear with the largest static seismic case of the same direction (the load patterns of type Seismic). The drift cases `RSAXD` and `RSAYD` (names ending in D) are compared with the drift patterns (type Seismic (Drift): `EQXSD`, `EQYSD`), whose period is not capped (NSCP 208.6.5.2). So the drift cases are not inflated to the capped-period strength shear, and the drift check in `sdt check` uses them. When the spectrum base shear is lower, the scale factor of the spectrum case is multiplied by static / spectrum and the analysis runs again. The analysis is linear, so one scaling is exact; it is repeated (at most three times) until the two agree within 1 %. A spectrum base shear above the static one is left as it is.
   Before that comparison, the drift patterns are held to the zone 4 minimum 0.8 Z Nv I / R (Eq. 208-11) when their Ca and Cv are typed in. ETABS keeps no Nv with typed coefficients and applies the minimum with Nv = 1, which is too low near a source for a long-period building. The site the two values belong to is found from Tables 208-5 to 208-8, and the scale factor of the pattern in its own load case is set to the minimum over the V/W ETABS computed, or to 1 when the minimum does not govern. The model is analysed again when a factor changed, and the report lists each case raised. Patterns defined by their site (zone, soil, source, distance) are left alone: ETABS has the Nv.
3. **Torsional irregularity: Ax** (NSCP 208.5.1.4, Eq. 208-7). Where the largest displacement of a level is over 1.2 times its average under a static seismic case (EQXPE, EQXNE, EQYPE, EQYNE), its accidental torsion is multiplied by Ax = (δmax / 1.2 δavg)², between 1.0 and 3.0.
   - ETABS has no per-level eccentricity for UBC 97 patterns. So the part added, (Ax − 1) times the level's accidental torque, is a load pattern of its own beside each static pattern (`AX EQXPE` and so on). It is a moment on a joint of the level's rigid diaphragm.
   - The accidental torque of a level is half the difference of the story torques of the + and − eccentricity patterns. It is added in every combination that has its pattern, so the directional, strength, service and Em combinations all carry it.
   - The spectrum cases take 5 % times the largest Ax as their eccentricity.
   - Ax is read once, from the static cases with their 5 % eccentricity, and applied; the model is then analysed once more so its results include the added torsion. It is not read again after the torsion is added: the added torsion would raise the ratio again, and Ax would climb with each round.
   - The `AX` patterns are load cases of their own. Running `sdt analyze` again reads the same Ax from the plain patterns, and replaces the torsion loads rather than adding to them.
   - The drift patterns are not amplified.
4. The spectrum scaling of step 2, to 90 % or 100 % (NSCP 208.5.3.5.4). The drift cases always go to 100 % of the drift patterns.
5. **The redundancy factor ρ** (NSCP 208.6.1, Eq. 208-20): ρ = 2 − 6.1 / (rmax √AB).
   - **AB** is the plan area of the lowest floor above the seismic base, overhangs in (m²).
   - **rmax** is the largest element-story shear ratio ri of the stories at or below two-thirds of the height (those that start below it, so a one-story building counts its story), under EQXPE (X) and EQYPE (Y):
     - for a moment frame, the shears along the direction of two adjacent columns joined by a beam, over the story shear, taking 70 % for a column with frame bays on both sides;
     - for a wall (a labelled pier running in that direction), its shear times 3 / lw (lw in m), over the story shear.
   - **The story shear** is the sum of the shears the story's columns and walls carry in that direction, or ETABS's story force when that is larger. Where walls carry the shear back (a podium or a backstay below grade), the story force ETABS gives is a small net value, and every column would look like the whole story.
   - **Limits:** ρ is between 1.0 and 1.5. For a dual system it need not exceed 80 % of the value. In Seismic Zone 2 it is 1.0. For special moment frames, a ρ over 1.25 is a warning to add bays; 1.25 is not used in its place.
   - **In the combinations:** ρx and ρy go into new directional combinations `EQ_RHO_01` to `08` and `RSA_RHO_01` to `08`, with ρx on the X cases and ρy on the Y cases. They take the place of `EQ_COMBO_nn` and `RSA_COMBO_nn` in every ULS and SLS seismic combination. The Em combinations (SSLC, Ω0 Eh) and the drift combinations keep ρ = 1.0. A model with no `EQ_COMBO_nn` combinations gets ρ in the report only. `sdt setup` writes the combinations back without ρ, so run `sdt analyze` again after it.
6. Reports:

| Check | Rule |
|---|---|
| Periods | The modal period with the largest mass in X and in Y, against NSCP 208.5.2.2 Method A, T_A = Ct hn^(3/4) (Ct and hn in ft units, as UBC 97 and ETABS). hn is the height between the bottom and top stories of the static seismic patterns, which is the height ETABS uses for their period; the report names the two stories. On a model with no such pattern it is the height of the top level above the ground level: elevation 0 when the base is below it (a footing level or a basement), otherwise the base. It is printed with the periods, and the Method B cap: 1.3 T_A in zone 4, 1.4 T_A in zone 2. A longer modal period is reported: the static seismic cases use the capped period |
| Modal mass | The sum of the modal participating mass in X and Y; a warning below 90 % (add modes) |
| Weight | The seismic weight from the mass source (Mass Summary by Story), against the vertical base reaction of the mass-source load patterns with their multipliers. A difference above 1 % is reported: element self mass or added mass in the mass source, or loads on the base |

The weight per story is listed too.

## Example (test model at its smallest sections)

```
X: static EQXNE 1,144.3 kN | RSAX 561.2 kN -> 1,144.3 kN (scale factor x 2.0392)
Y: static EQYNE 1,830.3 kN | RSAY 1,737.4 kN -> 1,830.3 kN (scale factor x 1.0535)
Governing modal period X: 2.322 s
Method A, T_A = Ct hn^(3/4): 0.859 s; Method B cap 1.3 T_A = 1.117 s
Sum X: 98.3 %   Sum Y: 98.6 %
Seismic weight from the mass source: 14,567.5 kN
Base reaction of the same loads:     14,567.5 kN
```

The model is unlocked before each scale factor is changed. ETABS accepts a new factor on a locked model and gives the new base shear, but keeps the accidental torsion of the run before; the story torsion of the spectrum cases would then be too small.

## Not covered yet

- The drift patterns do not get the Ax torsion.
- ρ is read on the analysis as it stands; after members change (`sdt design`), run `sdt analyze` again to read it on the new sizes.
- Story drift is checked by `sdt check` and `sdt drift`. The irregularities are checked by `sdt irregularity`, which also writes whether the structure is regular, for step 4.

The values (scaling target, period caps, modal mass) are in `design/criteria/code_config.py` (`NSCP.seismic`).
- A PDF or Excel report.

## Tests

`tests/test_model_analysis.py` checks the Method A period, the Method B cap and the report text. The scaling and the checks were run on a copy of the test model.
