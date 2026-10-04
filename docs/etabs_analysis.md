# ETABS Analysis Checks

`etabs_api/workflows/model_analysis.py` runs the analysis of the model open in ETABS, scales the response spectrum cases to the static base shear, and checks the periods, the modal mass and the weight.

## How to use it

With the model open and saved in ETABS, from the project folder:

```powershell
sdt analyze
```

(or `python main.py analyze`). Two dialogs:

1. **Which model:** this model, or a copy saved beside it as `<model> - ANALYSIS.EDB`. The scaling changes the response spectrum cases, so choose the copy to keep the original as it is.
2. **Seismic zone factor Z and Ct** (ft units, 0.030 for concrete frames), for the Method A period. They are filled in from the model's saved setup inputs (`<model>.setup.json`) when there are any.

The report is printed in the terminal and the model is saved.

## What it does

1. Runs every load case.
2. Puts every response spectrum case back to its unscaled factor, g I / R from the seismic patterns, so a second run does not keep the first run's scaling. For each response spectrum case (`RSAX` along U1, `RSAY` along U2), compares its base shear with the largest static seismic case of the same direction (the load patterns of type Seismic). The drift cases `RSAXD` and `RSAYD` (names ending in D) are compared with the drift patterns (type Seismic (Drift): `EQXSD`, `EQYSD`), whose period is not capped (NSCP 208.6.5.2). So the drift cases are not inflated to the capped-period strength shear, and the drift check in `sdt check` uses them. When the spectrum base shear is lower, the scale factor of the spectrum case is multiplied by static / spectrum and the analysis runs again. The analysis is linear, so one scaling is exact; it is repeated (at most three times) until the two agree within 1 %. A spectrum base shear above the static one is left as it is.
3. Reports:

| Check | Rule |
|---|---|
| Periods | The modal period with the largest mass in X and in Y, against NSCP 208.5.2.2 Method A, T_A = Ct hn^(3/4) (Ct and hn in ft units, as UBC 97 and ETABS; the height above the base), and the Method B cap: 1.3 T_A in zone 4, 1.4 T_A in zone 2. A longer modal period is reported: the static seismic cases use the capped period |
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

## Not covered yet

- The 90 % / 100 % scaling rule of NSCP for regular and irregular structures: the spectrum is always scaled to 100 % (`sdt check` reports 90 to 100 % as a warning).
- Irregularities (torsion and Ax, soft story, mass, weak story). Story drift is checked by `sdt check`.

The values (scaling target, period caps, modal mass) are in `design/code_config.py` (`NSCP.seismic`).
- A PDF or Excel report.

## Tests

`tests/test_model_analysis.py` checks the Method A period, the Method B cap and the report text. The scaling and the checks were run on a copy of the test model.
