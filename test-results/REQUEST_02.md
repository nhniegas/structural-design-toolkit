# Request 02: follow-up to the 2026-10-05 run

Thank you for `2026-10-05/`. The spectrum and `check` bugs are confirmed. The
items below narrow down what that run raised. Same rules as before: scratch
copies only, defaults unless a step says otherwise, and the dialog log at the
top of each file. Save under `test-results/2026-10-06/` (or the date you run
it), with a short `README.md` and `notes.txt` as last time.

## A. Drift: which story height is right (highest priority)

`center_drifts` (etabs_api/workflows/drift_check.py:123-148) measures each
diaphragm story from the diaphragm story below, or from the base elevation for
the lowest one. If GF has no diaphragm, 2F is measured over 5.5 m (from the
base at -1.0 m) instead of 4.5 m. That gives drift 18% too low in N-mm. The
kN-m result looked right only because of the units bug.

On the N-mm copy, after one `sdt drift` run with the defaults (centre of mass):
1. `A1_diaphragms.txt`: which stories have a diaphragm assigned (Base, GF,
   2F, 3F, RD), and the Joint/Diaphragm assignment at GF.
2. `A2_etabs_story_drifts.txt`: the ETABS "Story Drifts" table for EQXSD,
   EQYSD, WX and WY (the drift combinations sdt used). Use the analysis of the
   first stiffness level (strength level, 0.35/0.70). If that's not easy, use
   the model as it is and say which.
3. `A3_drift_corners_Nmm.txt`: `sdt drift` again on a fresh N-mm copy, choosing
   **"Outer four corners"** instead of the centre of mass.
4. In notes: the 2F drift from each of the three sources (sdt centre, sdt
   corners, ETABS Story Drifts) side by side.

## B. Wind X and Wind Y give identical drift

WX and WY gave 0.00152 / 0.00152 (and h/767 / h/767 in `check`).
1. `B1_wind_patterns.txt`: the definition of the WX and WY load patterns
   (Define > Load Patterns > Modify Lateral Load, or the "Load Pattern
   Definitions - Auto Wind - ASCE 7-10" table): direction angles, exposure
   widths, Cp windward/leeward, and "Create All" or not.
2. Base reactions FX, FY for WX and WY.
3. The plan dimensions of the building (is it square?).

## C. Short footing-level columns, and GF-C1 three times worse

In `08_columns.xlsx` all 12 GF columns fail shear on the capacity shear Ve.
GF-C1 has ratio 10.7, the others about 3. Ve may be capped by the beams'
Mpr at the top joint (ACI 18.7.6.1.1, `beam_moment_limits`) but not at the
footing.
1. `C1_gf_columns.txt`: for GF-C1, GF-C2 and GF-C2A, copy from
   `08_columns.xlsx` every column about capacity shear: Capacity_Based_Ve,
   probable moments or limits per end if present, clear height lu, P, and the
   beams framing in at GF (names, sizes, bars).
2. Re-run `sdt columns` on a fresh copy, answering **"check the foundation
   level: No"** (or whatever the option is that designs these for the analysis
   shear). Save as `C2_columns_no_foundation.txt` and say whether the 12 GF
   failures go away and what the summary says.

## D. Joint shear (3-C1A, 2-C2)

The demand doubling looks consistent with ACI (T plus C from two beams, and the
column shear doubles too). To check the capacity side, `D1_joints.txt`:
1. For 3-C1A and 2-C2: f'c, column size, the beam widths and depths framing in
   each direction, and from `08_columns.xlsx` the joint columns (effective
   width / Aj, coefficient or number of confined faces if shown, phi Vn).
2. From `07_beams.xlsx`, for the beams framing into those two joints: Mu at
   that end and the bars given (4D28 top and bottom?). Is 4D28 needed for
   strength there, or set by a minimum or the SMRF rules?

## E. Default deflection combinations (needs a model without DEF combinations)

On a fresh copy, **delete the four DEF combinations** that `sdt setup` made
(and any service/deflection combinations), save, then run `sdt beams` with the
defaults. Save as `E1_beams_no_def.txt`. The dialog log should show the
deflection-role dialog: list each option and which one is the default.

## F. Earth cover on narrow beams (expected crash)

On a fresh copy, run `sdt beams` answering **Earth cover: Yes**, and pick the
level with the narrowest beams (GF if it has tie beams). Everything else stays
default. Save as `F1_beams_earth_cover.txt`. The simulation crashed the whole
run with
`ValueError: SECTION DETAILING ERROR: Beam width (250 mm) fits max 1 main bars per layer, but requires 2 stirrup legs!`
Report whether it crashes, and if not, what the narrowest beam got.

## G. Untagged members default (optional, only if simple)

On a fresh copy, rename three beams to plain numbers (e.g. "901", "902",
"903") and run `sdt beams`. Record the readiness dialog: its options and which
one is the default. **Answer "Stop"** at that dialog (don't tag). Save as
`G1_untagged.txt`.
