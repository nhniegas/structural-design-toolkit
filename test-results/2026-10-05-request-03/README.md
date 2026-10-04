# Results of REQUEST_03, run 2026-10-05

| Item | File | Result |
|---|---|---|
| C | `C3_gf_c1_report.txt` | The PDF report does not show the Ve working: no Mpr, no axial load, no beam limit. Largest Ve of GF-C1 is 9,226 kN with Pu = 260 kN (compression), of GF-C2 2,596 kN with Pu = 607 kN. Uplift is not the cause; Ve barely changes with Pu. |
| F | `F2_beams_250_earth.txt` | **Crashes** with the expected `ValueError: SECTION DETAILING ERROR ... (250 mm) ...`; no output file is written. |

Read `notes.txt` for the detail and for a slip in the test procedure (answers remembered
between test runs) that was found and fixed here.
