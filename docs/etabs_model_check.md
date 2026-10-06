# Model check (`sdt check`)

`sdt check` (or `python main.py check`) reads the model open in ETABS and prints a list of checks in the terminal. A separate window then shows the summary: the count of each status and every FAIL. It doesn't change the model.

```
[FAIL] Story range should be GF to RD: EQXPE 3F-RD   (NSCP 208.5.2.3)
```

Each line has a status:

| Status | Meaning |
|---|---|
| OK | The check passes. |
| INFO | A value for you to confirm, such as the importance factor or the support type. |
| WARN | Probably missing or unusual. |
| FAIL | Wrong against the code. |
| N/A | Needs analysis results. Run `sdt analyze` first. |

## Basis: NSCP 2015 through UBC 97

ETABS has no NSCP seismic code, so the model uses the UBC 97 auto-seismic patterns and the UBC 97 response spectrum function. NSCP 2015 Section 208 is UBC 97 with the same equations. The figure and table numbers in this section are the NSCP ones:

- Figure 208-3 is the response spectrum.
- Tables 208-4 to 208-8 hold Na, Nv, Ca and Cv. For Z 0.2 and 0.4 these are the same values as the UBC tables.
- Table 208-11A gives R for concrete systems.

`sdt check` reads the UBC 97 inputs and holds them to the NSCP tables. It checks every pattern, including Ca, Cv, the period or coefficient typed in by hand. With results, it recomputes the NSCP base shear coefficient from the period and weight ETABS used, so an edited static pattern is checked too.

Every limit and table value comes from `design/code_config.py`: `NSCP` for chapter 2, and `CODE` (ACI 318M-14 = NSCP chapter 4) for the member limits. To change a value, change it there.

**Drift.** NSCP 208.6.4.1 computes drift with the 203.3 combinations, so the check reads the story drifts of the `DRIFT` and `WDRIFT` combinations that `sdt setup` creates. For each drift case it reports the worst combination:

- **Seismic:** `EQXSD` and `EQYSD`, the drift patterns with the period not capped; and `RSAXD` and `RSAYD`, the spectrum drift cases that `sdt analyze` scales to those patterns. Each is amplified to ΔM = 0.7 R ΔS and checked against 0.025h or 0.020h.
- **Wind:** `WX` and `WY` are checked against the limit typed in the dialog (h/400 by default). NSCP sets no wind drift limit.

Every story is included, those below the ground level too. A model set up before the drift combinations existed is checked on the cases alone, with a warning to run `sdt setup`.

## What is checked

| Group | Check | NSCP 2015 |
|---|---|---|
| Model | Every frame tagged and on a setup section (warnings only: the design runs on untagged members and reads other sections from ETABS); floors with a diaphragm and a slab section; supports at the column bases; beams with a free end | 208.5.1.3 |
| Loads | Gravity patterns with no loads; roof live assigned; self weight counted once; reducible live above 4.8 kPa | 205, 205.4, 205.5 |
| Seismic | Story range: from the ground level (the story at elevation 0 when there is a footing level) or below it, up to the highest level with structure. A base below the ground level, such as the bottom of the foundation, is your choice: it is reported for you to confirm, not failed. Patterns that start above the ground level fail. A top story with no structure fails: the height hn, and with it the period cap, come out too long and the base shear too small | 208.5.2.3, 208.5.2.2 |
| | Accidental eccentricity 0.05 | 208.5.1.3 |
| | Z is 0.20 or 0.40 (only zones 2 and 4) | Table 208-3 |
| | Ca and Cv against soil, zone, source type and distance; Na above 1.1; within 2 km of a fault | Tables 208-5 to 208-8, 208.4.4.3 |
| | I is 1.0, 1.25 or 1.5 | Table 208-1 |
| | R is a concrete system and is permitted in zone 4 (IMRF 5.5 and OMRF 3.5 are not) | Table 208-11A |
| | Ct is 0.035, 0.030 or 0.020 in ft units (0.0853, 0.0731, 0.0488 in m) | 208.5.2.2 Eq. 208-12 |
| | Spectrum function Ca and Cv match the patterns | 208.5.3.2, Fig. 208-3 |
| | Spectrum cases have accidental eccentricity | 208.5.3.5.6 |
| | Height hn (75 m or more needs the dynamic procedure) | 208.4.8 |
| Seismic, with results | V/W of every static pattern = Cv I/(R T), at most 2.5 Ca I/R, at least 0.11 Ca I and, in zone 4, 0.8 Z Nv I/R; drift patterns without the lower limits | 208.5.2.1, 208.6.5.2 |
| | Period T at most 1.3 T_A in zone 4 (1.4 T_A in zone 2), T_A = Ct hn^(3/4). hn is the height between the bottom and top stories of each pattern, as ETABS takes it; the message gives the height and the stories it runs between. The weight W is checked over the same stories | 208.5.2.2, 208.6.1 |
| | Spectrum base shear: OK at 100 % of static, WARN from 90 % (only for regular structures), FAIL below 90 % | 208.5.3.5.4 |
| | Modal participating mass at least 90 % | 208.5.3.5.2 |
| | Drift ΔM = 0.7 R ΔS at most 0.025 h (T < 0.7 s) or 0.020 h, from the drift patterns and the spectrum drift cases (names ending in D) | 208.6.4.2, 208.6.5.1 |
| Wind | Story range; speed, exposure, Kzt, G and Kd for you to confirm | 207 |
| Combinations | Strength combinations exist (a model whose combinations are not named `ULS` gets a warning, not a failure: the design commands ask which ones to design for, see [existing models](existing_models.md)); every combination refers to existing cases; seismic strength combinations carry Ev = 0.5 Ca I D on the dead load, (1.2 + Ev) D and (0.9 − Ev) D | 203.3.1, 208.6.1 |
| Analysis | Beams with the full torsional stiffness (J modifier near 1), which attract large compatibility torsion (ACI 22.7.3.2); Linear static cases on preset P-Delta; the P-delta method | 208.6.3 |
| | Mass source without double self weight | 208.6.1 |
| | Effective cracked-section I, the member modifier times the section modifier as ETABS applies them, of 0.35 (beams) and 0.70 (columns). A modifier assigned to both the members and the sections is applied twice: 0.12 / 0.49 | 208.6.2, 406.6.3.1.1 |
| | Mass and weight modifiers equal to 1 (any other value reduces W) | 208.6.1 |
| | Rigid end zone factor | 406.6.2.3 |
| SMRF (R 8.5) | Girder width at least the smaller of 0.3h and 250 mm; clear span at least 4d (a warning: `sdt beams` and `sdt design` can design the shorter girders without the SMRF rules, see [the design guide](concrete_beam_column_design.md#girders-with-a-clear-span-under-4d)) | 418.6.2.1 |
| | Column side at least 300 mm; side ratio at least 0.4 | 418.7.2.1 |
| | Concrete at least 21 MPa; rebar at most 420 MPa | 418.2.5.1, 418.2.6.1, 420.2.2.5 |

## Not checked yet

- Irregularities: the vertical ones (Table 208-9) and the plan ones (Table 208-10, including torsion).
- The redundancy factor ρ (208.6.1).
- Minimum live loads against Table 205-1, and partition loads (204.3).
- Wind speed against the site map (Figure 207A.5).
