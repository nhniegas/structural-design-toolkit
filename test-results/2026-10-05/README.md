# Results of the real ETABS run, 2026-10-05

Run on Nhel's PC by a Claude Code session, ETABS Ultimate 22.6.0, toolkit 0.1.1.
Answers the request in `../README.md`.

| File | What it is | Result |
|---|---|---|
| `01_doctor.txt` | `sdt doctor` | everything OK |
| `02_spectrum.txt` | spectrum scale factors, I, R and base shears before and after `sdt analyze`, kN-m and N-mm | **bug confirmed**: 1153.72 written in kN-m, RSA base shear 793 x the static |
| `03_analyze_kNm.txt` | `sdt analyze`, kN-m present units | no warning about the above |
| `04_analyze_Nmm.txt` | `sdt analyze`, N-mm (control) | correct: RSAX x 1.261, RSAY x 1.228 |
| `05_check_kNm.txt` | `sdt check`, kN-m | **confirmed**: hn = 0.0 m, period-cap lines FAIL |
| `05b_check_Nmm.txt` | `sdt check`, N-mm (control) | hn = 11.5 m, no FAIL |
| `06_drift_basement.txt` | `sdt drift`, kN-m, base at -1.0 m | suspected failure **not reproduced**; but drifts are 1.22 x the N-mm ones |
| `06b_drift_Nmm.txt` | `sdt drift`, N-mm (control) | all OK |
| `07_beams.txt`, `07_beams.xlsx` | `sdt beams`, defaults | 102 of 102 pass |
| `08_columns.txt`, `08_columns.xlsx` | `sdt columns`, defaults | 13 pass, 34 fail (12 column shear, 22 joint shear) |
| `09_notes.txt` | choices, timings, what looked wrong, what was not done | read this first |
| `10_errors.txt` | crashes | none |

Two limits to keep in mind (details in `02_spectrum.txt` and `09_notes.txt`):

- The model's database units are N-mm. "kN-m" here means the API's present units
  were set to kN-m before the command, not a model created in kN-m.
- The dialogs were answered by a script with their defaults; no one was at the
  screen.
