# Irregularity Check (`sdt irregularity`)

`sdt irregularity` reads the analysed model open in ETABS and reports each structural irregularity of UBC 97 Tables 16-L and 16-M (NSCP 2015 Tables 208-9 and 208-10) as regular, irregular, or left to you to judge, with the numbers behind it. It also gives the accidental torsion amplifier Ax and the P-delta stability coefficient. The model is not changed.

Code: `etabs_api/workflows/irregularity.py`.

## How to use it

1. Open the model in ETABS, saved and analysed (run the analysis, or `sdt analyze`).
2. For the weak story check, run `sdt beams` and `sdt columns` first: the story strength comes from the column design.
3. Run:

```powershell
sdt irregularity
```

4. Answer the dialog, shown when P-delta is on in the model:

| Question | Choices |
|---|---|
| P-delta stability coefficient | Analyse a copy of the model with P-delta off, for first-order drifts; or use the drifts of the model as analysed, which include P-delta |

The report is printed and saved beside the model as `<model> - Irregularity.txt`, and a window shows the summary.

## What is checked

The checks use the static seismic load cases of the model: its seismic patterns, each with its accidental eccentricity, or the drift patterns when the model has no other. Each case is checked in its own direction only: ETABS also lists an X case in Y, where it hardly moves the structure and a ratio of drifts means nothing. For each story and direction, the case that governs is shown.

### Vertical irregularities (Table 16-L)

| Type | Rule | From |
|---|---|---|
| 1. Stiffness (soft story) | Story stiffness under 70 % of the story above, or under 80 % of the average of the three stories above | ETABS story stiffness: story shear over story drift |
| 2. Weight (mass) | Story mass over 150 % of an adjacent story. A roof lighter than the floor below is not counted | ETABS mass by story |
| 3. Vertical geometric | Plan dimension of the lateral system over 130 % of an adjacent story | The distance between the outermost columns and walls of each story, along X and Y |
| 4. In-plane discontinuity | Left to you | The report lists the columns that start on a floor with no column under them |
| 5. Capacity (weak story) | Story strength under 80 % of the story above | The column design (see below) |

### Plan irregularities (Table 16-M)

| Type | Rule | From |
|---|---|---|
| 1. Torsional | Largest story drift at one end over 1.2 times the average of the two ends | ETABS maximum over average story drifts |
| 2. Re-entrant corners | Both projections beyond a re-entrant corner over 15 % of the plan dimension | The outline of the floor shells of each story |
| 3. Diaphragm discontinuity | Openings over 50 % of the enclosed area | The open area inside the floor outline. The change of diaphragm stiffness from story to story is left to you |
| 4. Out-of-plane offsets | Left to you | The columns that start on a floor |
| 5. Non-parallel systems | Left to you | |

### Accidental torsion amplifier Ax

For each level and direction, from the largest and the average displacement of the level (UBC 1630.7):

```
Ax = (dmax / (1.2 davg))^2,   1.0 <= Ax <= 3.0
```

Ax is reported beside the torsion check. It is **not applied** to the seismic patterns of the model: where a level is torsionally irregular, its accidental torsion is yours to amplify.

### Weak story: the story strength

The strength of a story is the sum over its columns of the shear at which the column yields in flexure at both ends, limited by its shear strength:

```
V(story) = sum of min( phi Vn,  (phi Mn top + phi Mn bottom) / lu )
```

`phi Vn`, `phi Mn` and the unsupported length `lu` are those of the last column design saved for the model, the lowest values of each column over its combinations. Every story is measured the same way, so the ratio between stories is what counts. Walls and braces are not in it.

With no column design saved, the check says to run `sdt columns` first. When the model file is newer than the design, the report says so. Under 65 % of the story above, it adds the height limit of UBC 1629.9.1.

### P-delta stability coefficient

For each story and direction (UBC 1630.1.3):

```
theta = Px D / (Vx h)
```

| Symbol | Value |
|---|---|
| `Px` | The dead and floor live load above the story, unfactored: the axial force of the story in the dead and live load cases. Roof live load is left out |
| `Vx` | The story shear of the seismic case |
| `D` | The story drift of that case at the design forces (the elastic drift, not multiplied by 0.7 R) |
| `h` | The story height |

P-delta may be neglected where theta is at most 0.10. The report also gives the second rule for seismic zones 3 and 4: the story drift ratio at most 0.02 / R.

**First-order drifts.** With P-delta on, the drifts of the model already include it. For the coefficient as the code defines it, the command copies the model file into a folder beside it (`sdt irregularity scratch`), switches P-delta off in the copy, analyses the seismic and gravity cases, reads the story shears and drifts, opens your model again and removes the folder. Your model is not saved or changed, and it has its results when it is opened again. On the test model this takes about a minute.

The other choice reads the drifts of the model as it is. They are a little larger, so theta is on the safe side, and the report says which was used.

## What follows from the result

The report ends with what the irregularities found ask for:

- whether the static force procedure is still allowed (an irregular structure of at most five stories or 20 m; UBC 1629.8.3) or the dynamic procedure is required (1629.8.4);
- the spectrum scaling that applies: 90 % of the static base shear for a regular structure, 100 % for an irregular one (UBC 1631.5.4). `sdt analyze` always scales to 100 %; this report does not change that.

## Limits

- **A level at or below the ground.** A level of tie beams only is a story in ETABS. It has little mass and a short, stiff story under it, so the story above can be reported as a mass irregularity. Judge whether that level is a story of the structure.
- **Re-entrant corners** are measured where the floor outline turns inward. A corner cut off at an angle is not one. A model with no floor shells has no outline, and the check is not made.
- **Penthouses.** A one-story penthouse need not be considered for the vertical geometric type; the command does not know which story is a penthouse.
- **Flexible diaphragms.** The torsion check and Ax are for diaphragms that are not flexible. The command does not check the diaphragm type.
- **Response spectrum cases** are not used: their results have no sign, and the tables are those of the static cases.
- The clause numbers are those of UBC 97. NSCP 2015 section 208 follows the same rules; check the NSCP clause before you cite one.
- The types left to you are reported as information, never as regular.

## Tests

`tests/test_irregularity.py`: each computed type at, under and over its limit; Ax and its bounds; the story strength from a column design; the plan shape of a rectangle, an L, a small notch, a cut corner and a turned floor of several shells; theta and its two rules; and the report. They need no ETABS.

In ETABS, on the test model (4 stories, P-delta on): regular in stiffness, geometry and torsion (largest max / avg drift 1.18, Ax 1.00), a mass irregularity at the second floor against the tie beam level, theta at most 0.033 on first-order drifts. On its designed copy: a weak story at the second floor, 75 % of the third, which is the taller story. After the command the model was locked with its results, and the scratch folder was gone.
