# Request: sample output from a real ETABS run

A user test of the toolkit was run in a cloud session against a simulated
ETABS model (no ETABS there). Some findings can only be confirmed on a machine
with ETABS. This folder collects that output.

**Who runs this:** you, or a Claude Code session on your own PC, with ETABS
installed and this branch checked out:

```powershell
git fetch origin
git checkout claude/amazing-mayer-rud2d9
.venv\Scripts\activate
pip install -e .
```

**Work on copies of models only.** Several commands change the model
(`analyze` rescales the spectrum, `drift` edits stiffness modifiers, `setup`
replaces the mass source). Copy the `.EDB` to a scratch folder first.

Save each result under `test-results/<date>/` (for example
`test-results/2026-10-05/`) using the file names below. Then commit and push
to this branch.

---

## 1. Machine check (`doctor`)

```powershell
sdt doctor > test-results/<date>/01_doctor.txt 2>&1
```

Also write down the ETABS version (Help > About) at the top of that file.

## 2. Units and spectrum scale: the main thing to confirm

Suspected bug: `sdt analyze` and `sdt design` reset the response spectrum
scale to `9806.65 * I / R`, a value in mm/s², without first switching the
model to N-mm. In a kN-m model that is about 1000x too large, and the
scaling step only scales up, so it is never corrected.

Use a copy of a model that is in **kN-m** units (bottom-right of the ETABS
window) and has a UBC 97 seismic pattern and a response spectrum case.

1. Before running anything, record in `02_spectrum.txt`:
   - the model units (bottom right),
   - for each response spectrum case: Define > Load Cases > the case >
     Modify, then the **Scale Factor** of each U1/U2 load,
   - I and R of the seismic load pattern.
2. Run `sdt analyze` on that copy. Save the terminal output and the summary
   window text as `03_analyze_kNm.txt`.
3. After it finishes, record the spectrum Scale Factors again in
   `02_spectrum.txt`, together with the RSA and static base shears
   (Display > Tables > Base Reactions).

Expected if the bug is real: scale factor about `9806.65*I/R` (≈ 1154 for
I = 1.0, R = 8.5), not about `9.81*I/R` (≈ 1.154). Base shear from RSA far
above the static base shear.

If you can, repeat steps 1–3 on the same model saved in **N-mm** units
(`04_analyze_Nmm.txt`) as a control.

## 3. Model check (`check`)

On the kN-m copy:

```powershell
sdt check > test-results/<date>/05_check_kNm.txt 2>&1
```

Look for "hn = 0.0 m" or period-cap lines that all FAIL (suspected units
issue in story elevations).

## 4. Drift on a model with a basement (optional)

If you have a model with the base below 0 (a basement), in kN-m units:

```powershell
sdt drift > test-results/<date>/06_drift_basement.txt 2>&1
```

Suspected bug: the first story above the base reads as about 3 mm tall and
FAILs.

## 5. Beam and column design on a real model

On an analysed copy (tagged or not; note which):

1. `sdt beams`. Save the terminal output and the summary window text as
   `07_beams.txt`. Copy the results `.xlsx` as `07_beams.xlsx`.
2. `sdt columns`. Save the output as `08_columns.txt` and the `.xlsx` as
   `08_columns.xlsx`.
3. Write down in `09_notes.txt`:
   - which choices you made in each dialog, and the defaults you left;
   - how long each command took;
   - anything confusing, slow or wrong-looking.

Things to note in particular:
- Seismic gravity combination for beam shear: which one was the default?
- Deflection combinations: which ones were suggested first?
- Columns failing only "Joint shear" while the shear ratio is low (this is
  the joint dimension rule, 20 db).

## 6. Anything that crashed

Paste the full traceback into `10_errors.txt`, with the command and the
dialog answers just before it.

---

Model files (`.EDB`) are not needed. Text output and the `.xlsx` results are
enough. Remove client names or project details if the models are
confidential.

---

## Follow-up requests

- `REQUEST_02.md`: drift story height, identical wind drift, short
  footing-level columns, joint shear inputs, default deflection combinations,
  earth cover crash, untagged default.
- `REQUEST_03.md`: the capacity shear of the GF corner column, and the earth
  cover crash on a 250 mm beam.
