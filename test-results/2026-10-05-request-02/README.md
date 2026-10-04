# Results of REQUEST_02, run 2026-10-05

Same PC, ETABS 22.6.0, toolkit 0.1.1. The folder is named `2026-10-05-request-02`
because `2026-10-05/` already holds the first run. Read `notes.txt` first.

| Item | File | Result |
|---|---|---|
| A1 | `A1_diaphragms.txt` | GF has no diaphragm; only 2F, 3F, RD (D1) |
| A2 | `A2_etabs_story_drifts.txt` | ETABS Story Drifts and centre of mass displacements, strength level |
| A3 | `A3_drift_corners_Nmm.txt` | corners 0.00278 / 0.00301, equal to ETABS |
| A | `A0_drift_centre_Nmm.txt` | centre 0.00265 / 0.00255 = 2F displacement / 5.5 m |
| B | `B1_wind_patterns.txt` | WX and WY are the same 'Create All' pattern at angle 0; plan 18 x 10 m |
| C1 | `C1_gf_columns.txt` | shear columns of GF-C1, GF-C2, GF-C2A; no Mpr columns exist |
| C2 | `C2_columns_no_foundation.txt` | 12 GF failures gone: 25 pass, 22 fail (joint shear) |
| D | `D1_joints.txt` | joint capacity = 0.85 x 1.2 x sqrt(f'c) x column area; 4D28 is not a strength need |
| E | `E1_beams_no_def.txt` | **the default of every deflection role is a factored ULS combination** |
| F | `F1_beams_earth_cover.txt` | no crash; the narrowest beam here is 300 mm, not 250 |
| G | `G1_untagged.txt` | readiness default Continue; untagged default 'Tag them now, in this model' |
