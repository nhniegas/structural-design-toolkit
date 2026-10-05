# User test report: Structural Design Toolkit (`sdt`) v0.1.1

Governing code assumed for every user: **NSCP 2015** (loads Ch. 2, seismic 208
UBC 97 style, concrete Ch. 4 = ACI 318M-14).
Dates: 2026-10-04 (simulation and code review) and 2026-10-05 (real ETABS runs).

---

## 1. How it was tested

| Part | Method | Status |
|---|---|---|
| `beams`, `columns` | Real toolkit code driven by a fake ETABS with 12 synthetic models × 4 scripted user types (96 runs) | **Executed** |
| `wind`, `steel`, `composite` | 57 input cases (normal, edge, invalid) through `calculate()` and the summary text | **Executed** |
| Menu, `doctor` | 27 typed menu inputs; `doctor` on a machine without ETABS | **Executed** |
| Beam flexure accuracy | Independent hand φMn of all 92 beams of the office model compared with the bars given | **Executed** |
| `setup`, `grids`, `tag`, `check`, `analyze`, `drift`, `design` | Code-path review (they need ETABS) | **Read only** |
| Real ETABS confirmation | Nhel's PC, ETABS Ultimate 22.6.0, toolkit 0.1.1, three rounds of requests (`test-results/`) | **Executed on real ETABS** |

Limits:
- `sdt.exe` cannot run on Linux; the Python code it is built from was run instead.
- In the simulation, ETABS extraction was replaced by synthetic design tables.
- Dialogs were answered by scripts, so screen layout was not checked (except `doctor` on the PC).
- On the PC, "kN-m" means the API present units were set to kN-m on an N-mm model, not a model created in kN-m.
- The PC test model is a 3-storey concrete SMRF built with `sdt setup` and `sdt grids` (base -1.0 m, GF 0, 2F 4.5 m, 3F 8.0 m, RD 11.5 m, 149 members).

### Simulated models
A tagged office · B hand-built untagged · C circular columns + cantilevers · D 10 storeys ·
E undersized members · F pinned base, tall GF · G f'c 42 / fy 550 · H f'c 17 / fy 230 ·
I envelope combinations only · J 1 bay, 1 storey · K no supports table · L 400×900 columns.

### Simulated user types
- **Expert:** sensible choices.
- **Novice:** accepts every default.
- **Fat-finger:** types bad values first.
- **Contrarian:** picks the last option every time.
- **Quitter:** closes a dialog midway.

---

## 2. Who uses it, and what they hit

| User | Typical stage | What they hit |
|---|---|---|
| Fresh graduate / junior | Designing a model built by someone else | Takes every default, which includes the risky ones: Ve combination 1.4D, factored deflection combinations, tag in place. Error messages don't say which box is wrong. |
| Senior design engineer | Whole workflow | 13–19 dialogs per beam run, no "repeat last run", "FAIL" with no reason. |
| Checker / peer reviewer | Existing analysed model from another firm | kN-m units (spectrum and `check` bugs). Untagged members. Wants read-only, but `setup`, `tag` and `analyze` change the model. |
| Modeller / BIM drafter | Blank or half-built model (`setup`, `grids`) | Story names differ from the DXF, so all columns are deleted. Rotated grids rewritten. Existing loads left out of combinations. |
| Engineer re-running after revisions | Late design | `design` overwrites its copy. Remembered answers from a previous model come back as defaults. |
| Steel / composite specialist | Standalone checks | No HSS, channels or metric names. Composite crashes on non-compact walls. |
| Firm with older ETABS (v19–21) | Any | Default path is ETABS 22. `setup` fails partway on old versions. |
| Student / academic | Standalone checks | NaN or 0 inputs give silent zero results. |
| Engineer on a deadline | Any | Cancelling is safe. A partial `<model>.setup.json` is left. A missing output folder loses all the work at the end. |

---

## 3. Critical: wrong numbers with no warning

### 3.1 Response spectrum scale 1000× too large in kN-m (`analyze`, `design`): **confirmed on real ETABS**
- **Code:** `reset_spectrum_scale` (`etabs_api/workflows/model_analysis.py:338-351`; same in `design_loop.py:670-685`) writes `9806.65·I/R` (mm/s²) without switching to N-mm.
- **Real run (kN-m):**
  - the scale factor written was **1153.72** instead of about 1.154;
  - RSA base shear was **541,024 kN** against 682 kN static (about 793×);
  - `analyze` gave no warning, and `check` marked it OK.
- **N-mm control:** correct (RSAX × 1.261, RSAY × 1.228).
- Scaling only goes up, so the error is never corrected. Every RSA force, combination and design result is wrong.

### 3.2 `check` reads story elevations in model units: **confirmed on real ETABS**
- In kN-m: hn = 0.0 m, T_A = 0.003 s, four period-cap lines FAIL.
- In N-mm: hn = 11.5 m, no FAIL.
- Code: `model_check.py:1039-1041`, `:500-509`.

### 3.3 Beams over-reinforced by a 150 mm bar-spacing rule: **traced, effect seen on real ETABS**
- **Code:** `BeamFlexureDesign.get_min_bars_for_150mm_spacing` (`design/beam_designer_aci318.py:198-207`) uses `max_clear_spacing_target = 150 mm` (`code_config.py:156`, labelled "project rule"). It sets the bar count of every beam face so the clear spacing is 150 mm or less.
- **Why it matters:** NSCP/ACI limit beam bar spacing only for crack control (ACI 24.3.2, about 280–300 mm c/c here). The 150 mm figure belongs to lateral support of column bars (25.7.2.3). The rule is not shown to the user or asked as an option.
- **Real run:**
  - 500×500 girders got **4D28 top and 4D28 bottom**, where strength needs 3D28 top at 2F, 2D28 elsewhere, and 2D28 bottom;
  - the simulation gave the same 4D28 at Mu = 79 kN·m.
- **Consequence:** joint shear demand is proportional to beam steel, and **22 columns fail joint shear** on the real model. Most of these failures likely come from this rule.

### 3.4 Deflection combinations default to factored ULS combinations: **confirmed on real ETABS**
- On a model without DEF combinations, each of the four deflection-role dialogs defaults to a factored combination (1.4 DL, 1.2 DL + 1.6 LL).
- "Let the toolkit add it" is the last option.
- Pressing Enter four times gives deflections about 1.4× too large, and the run still reports 0 failing.

### 3.5 Seismic beam shear Ve uses 1.4D by default: **confirmed on real ETABS**
- The gravity combination for Ve is simply the first in the list ("ULS 100 1.4 DL").
- NSCP 203 / ACI 18.6.5.1 calls for 1.2D + f1L. Leaving out live load can make Ve unconservative.
- On hand-built models no option is 1.2D + f1L at all.

### 3.6 `drift`, centre-of-mass method: **confirmed on real ETABS**
- **Code:** `center_drifts` (`drift_check.py:123-148`) measures each diaphragm story from the diaphragm below, or from the base for the lowest one.
- **Real model:** GF has no diaphragm, so 2F is divided by 5.5 m (from the base at -1.0 m).

| 2F drift | EQXSD | EQYSD | Wind |
|---|---|---|---|
| sdt, centre of mass (N-mm) | 0.00265 | 0.00255 | 0.00124 at 2F |
| sdt, outer corners | 0.00278 | 0.00301 | 0.00130 at **GF** |
| ETABS Story Drifts (max) | 0.002785 | 0.003012 | 0.001304 at **GF** |

- On this model the 5.5 m value is close to the true centre drift only by coincidence.
- **Missed:**
  - the GF story itself is never reported (it governs wind);
  - torsion: the corner and ETABS values are 18% higher in Y.
- **Units:** in kN-m the same run gives drifts 1.22× larger, because the base elevation is read in model units (`drift_check.py:348`) but compared with Z in mm.
- **Method:** the RSA centre method takes differences of envelope displacements, which is not a CQC story drift.
- **Recommendation:** default to the ETABS Story Drifts table or the corners method.

### 3.7 `setup` on an existing model (code review)
- Combinations and mass are built only from setup's own pattern names (`model_setup.py:258-283, 339-344`). Existing DL / SDL / LL / "Live" / EQX are left out of every ULS combination.
- A "Dead" pattern with self weight 1.0 stays next to the new SELFWEIGHT, so self weight is counted twice (`check` flags it, `model_check.py:270-274`).
- The mass source (`:600-608`) and the P-delta table (`:611-621`) are replaced.
- Modifiers are reset on setup-named sections (`:376-377`).
- The model is unlocked and its results dropped without warning (`:976-977`).

### 3.8 Seismic system factors (code review; verify against NSCP Table 208-11A)
- `concrete_r` is keyed by R alone (`code_config.py:504-514`):
  - 5.5 maps to "IMRF / wall-frame, not zone 4", so a correct zone-4 concrete shear-wall building gets FAIL "not permitted" (`model_check.py:465-468`);
  - 5.6 looks like the concrete braced frame;
  - 5.0 "special walls" returns OK.
- Ω0 is fixed at 2.8 for every system (`code_config.py:437`).
- ρ is fixed at 1.0 and never computed (208.5.1.1).
- R and Ct default to SMRF values (8.5, 0.03) whatever the system.

### 3.9 `grids` (code review)
- `plan_changes` takes the union of model and DXF stories (`grid_column_model.py:420`). If story names differ ("2F" vs "L2"), **all existing columns are removed** and every add fails with "no such story" (`:532-539`, `:653-655`).
- `replace_all=True` (`:620-623`) rewrites every grid system into the first one, breaking rotated, offset and wing grids.
- Old grids are read only from "General" lines (`:510-513`), so Cartesian grid matching never works. Columns moved more than 2.5 m become remove + add and lose their beam connectivity.
- Walls need exact end points (`:362-366`). Meshed or split walls are all redrawn.
- A copy option ("Into a copy") does exist (`:952-966`).

### 3.10 GF corner column capacity shear about 2.6× too large: **seen on real ETABS, cause not traced**
- GF-C1 (corner, 600×600, 16D25): Ve = 9,226 kN, ratio 10.7.
- GF-C2 (edge, same section): Ve = 2,596 kN. Both have lu = 500 mm.
- Computed directly with the toolkit's own column engine, 16D25 gives Ve ≈ 3,500 kN uncapped and ≈ 2,050 kN capped by the beams, at Pu = 260 kN.
- Ve barely changes with Pu, and uplift is ruled out.
- **Hypothesis:** Ve is taken from a heavier candidate tried during the bar iteration, or the clear height is wrong for the corner.
- The PDF report doesn't show the Mpr, P or limit used, so it can't be checked by hand.

---

## 4. Crashes (user sees a traceback; work lost)

| # | Where | Trigger | Evidence |
|---|---|---|---|
| 4.1 | `beams`: `check_stirrup_leg_anchorage` (`beam_designer_aci318.py:1094`) | One beam fits fewer bars per layer than stirrup legs (250 mm beam with 75 mm earth cover) | 10 of 12 simulated models (contrarian); **confirmed on real ETABS**. The whole run stops and **no output files** are written. It should mark that beam FAIL and continue. |
| 4.2 | `composite`: `result_rows` (`composite_column_designer_aiscDG06.py:599`; PDF `:807`) | Non-compact or slender wall for flexure (common: 635×635×6.35, 400×800×12.7, Fy 600) | Executed. `TypeError` escapes the input retry loop. AISC I3.4b flexure for non-compact walls isn't implemented. |
| 4.3 | `beams` save step | Output folder doesn't exist | Real ETABS: `FileNotFoundError` after all design work is done. |
| 4.4 | `setup` (`model_setup.py:1200, 1263`) | Blank or non-numeric entry | Code review |
| 4.5 | `setup` (`:465-472`) | ETABS without `eLoadPatternType_QuakeDrift` | Code review; fails midway |
| 4.6 | `analyze` (`model_analysis.py:226`) | An RSA case not run | `KeyError` (only RuntimeError/COMError caught) |
| 4.7 | `grids` / `tag` with no ETABS open | `connect()` opens the file and **runs the analysis**, changing the solver option (`connection.py:158-174`) | Code review |

---

## 5. Model changes the user may not expect

- **Untagged members:** the default is "Tag them now, in this model (sdt tag)", **confirmed on real ETABS**.
  - It renames members in the open model, unlocks it and drops results, before any copy exists (`concrete_workflow.py:442-447`).
  - No old-to-new name map is saved.
- **`design`:**
  - It says the original is not changed, but the tag step above does change it.
  - `<model> - DESIGN.EDB` and its log are overwritten silently on a re-run (`design_loop.py:1255-1259`).
  - Save return codes aren't checked.
- **`drift`:**
  - It edits the open model (0.35 / 0.70 modifiers, `:353-414`). If ETABS crashes partway, the EDB keeps them.
  - The modifiers are applied to steel members too. Walls and slabs are never cracked.
- **`analyze`, `setup`, `tag`:** they unlock the model and drop results without warning. "This model" is the first choice.

---

## 6. Accuracy and completeness gaps (NSCP 2015)

- **Wind (`setup`):**
  - WX and WY are the **same** "Create All" pattern at 0° (real ETABS: plan 18 × 10 m, identical drift). Every wind combination runs twice.
  - There are no −W combinations or 180°/270° cases.
  - The sub-patterns aren't in any combination.
- **`setup` input units:** wind speed is asked in mph while the NSCP map is in kph. Default exposure is B. Z accepts UBC-only values (0.075 / 0.15 / 0.3).
- **Mass source:** 100% of LIVENRED. NSCP 208.6.1 asks only 25% of storage live, so this is conservative but adds mass.
- **RSA scaling:** the target is always 100%; there is no 90% option for regular buildings.
- **Not checked anywhere:** accidental torsion amplification Ax, irregularities, and Ft (left to ETABS).
- **`analyze`:** hn starts from elevation 0 only when the base is below -0.5 mm (`model_analysis.py:265-269`). It is wrong when the base is at 0 and ground is above it.
- **Footing-level columns:**
  - With the default "check the foundation level: Yes", all 12 columns of a 1.0 m footing story (lu 500 mm) fail shear on Ve = ΣMpr/lu. No section can fix this (real ETABS).
  - With "No", the failures go away.
  - The dialog warns, but the default leads the user into unfixable failures.
- **Joint shear (real ETABS):**
  - The demand is consistent with ACI: T + C, with the column shear doubling for two beams.
  - Capacity is 0.85 × 1.2 × √f'c × Aj.
  - Aj at the top of a column uses the smaller column above (500×500); that is conservative, but should be stated.
  - The in-plane faces get γ = 1.2 without the ¾-width check that the transverse beams get.
  - The real failures are capacity, not the 20 db rule; on the simulated models the 20 db rule failed 32/48 columns.
- **`wind` standalone:**
  - V = 0 or negative, G = 0 and Kd = NaN are accepted (zero or NaN pressures in the report).
  - "Building classification" is free text that changes nothing.
- **Number boxes everywhere:** `nan`, `inf` and full-width digits are accepted. `1,234` and `25mm` are rejected.
- **`steel`:** only W/M/S/HP shapes. No HSS or channels, and metric names (W360X134) fail.
- **Earlier open items:**
  - beam Pu fixed at 50 kN for the Vc = 0 check;
  - slab steel ignored in the strong-column/weak-beam check.

---

## 7. User experience

- **Dialog count:**
  - `beams`: 13 dialogs on a tagged model, 19 on a hand-built one, 17–23 with typing errors;
  - `columns`: 8–11.
  
  Live load reduction, tributary method and pattern factor are asked every run, in technical wording. Suggestion: one "Use last settings for this model? [Yes / Review]".
- **Remembered answers:** they come back as defaults, even on another model (real ETABS: earth cover levels, "foundation level: No"). Show which model they came from.
- **Error messages:**
  - They don't name the field ("Every value must be a positive number.").
  - The retry shows the bad values again as the defaults.
- **Joint shear FAIL text:** the Design Status Reason is just "FAIL" (real ETABS), while column shear reads "FAIL: column shear strength".
  - The summary calls the 20 db dimension failure "Joint shear" even when the shear ratio is about 0.48.
  - No fix is suggested.
- **The column summary says the sway effect comes from "ETABS P-delta"** even when P-delta was off. Its "Combinations" count comes from the beam step.
- **Beam workbook:** L1 is always the topmost layer, so the main bottom bars appear in `n_*_L3`. Label them by face.
- **Column workbook:** "Beam Bars at Joint, X" lists GY girders and "Y" lists GX girders. It reads as swapped (real ETABS).
- **Sorting:** ETABS member numbers sort as text (11, 14, 2, 20). Use natural sort.
- **Summaries:** `beams` / `columns` print their summary in the terminal and then show the same text in the window; `analyze` / `check` / `drift` show only the window.
- **Model name:** after an analysis the report shows "Model: name.$et" (the ETABS temp file).
- **Menu:**
  - `d`, `co` and `s` match several commands but reply only "not one of the commands"; list the matches.
  - Typing `sdt beams` at the menu fails; ignore a leading `sdt` / `xs`.
- **Leftover files:**
  - a `.tex` file is left when LaTeX is missing;
  - a partial `<model>.setup.json` is left after a cancel.
- **`doctor`:**
  - no ETABS version or API match, model units, locked state, or multiple-instance check;
  - the default path is ETABS 22 only.
- **Tests:** one test fails on non-Windows (`run_summary.py:52`, basename of a Windows path).

### Run time

| Command | Simulation (synthetic, Linux) | Real ETABS 22.6 |
|---|---|---|
| `beams` | 92 beams 9–10 s; 430 beams 45 s | 102 beams 21–24 s (with PDF) |
| `columns` | 48 cols 38 s; 200 cols **245 s** | 47 cols 42–54 s (23 s is the 9.4 MB PDF) |
| `analyze` | n/a | 11–17 s |
| `drift` | n/a | 25–36 s |
| `check` | n/a | 3 s |

`design` has no time estimate and runs up to 4 analyses per iteration.

---

## 8. What worked well

- **Cancelling** at any dialog was clean in all 96 simulated runs: no crash and no partial results.
- **The readiness dialog** is clear about unnamed members and missing combinations.
- **Clear messages:**
  - an envelope-only model gets a clear "no combination can be designed" message;
  - `columns` run before `beams` gives a clear "Run sdt beams first".
- **Odd models ran fine:** no supports table, 1 bay / 1 storey, circular columns, cantilevers, high- and low-strength materials.
- **Beam accuracy:** the hand-calculated φMn for 92 beams agreed with the program (worst Mu/φMn 0.68).
- **On real ETABS** the beams ran 102/102 with no crash, and N-mm analysis scaling was correct.
- **`check` is read-only.**
- **Confirmed against NSCP 2015:**
  - Ca/Cv/Na/Nv, the 0.11CaI and 0.8ZNvI/R minimums, the 2.5CaI/R plateau;
  - the 1.3/1.4 T_A caps and Ct;
  - drift 0.7RΔs at 0.025/0.020;
  - 203.3/203.4 including Ev = 0.5CaID;
  - 100/30 directions;
  - 5% accidental eccentricity.
- **`doctor`** output is clear, with steps to fix (MiKTeX, ETABS registration). On the real PC everything was OK.

---

## 9. Suggested fix order

1. **Units:**
   - spectrum reset in `analyze` / `design` (3.1);
   - `check` elevations (3.2);
   - `drift` base elevation (3.6).
2. **Beam bars:** make the 150 mm spacing rule an option (default to crack control), then re-check joint shear (3.3).
3. **Safe defaults:**
   - Ve gravity combination 1.2D + f1L (3.5);
   - service-only deflection combinations, with "let the toolkit add it" first (3.4);
   - "Design as they are" before "Tag in place" (5);
   - foundation level "No", or a clearer explanation (6).
4. **Crashes:**
   - a beam detailing error becomes a FAIL row (4.1);
   - composite non-compact walls (4.2);
   - check the output folder before designing (4.3).
5. **Drift:** use ETABS Story Drifts or the corners method by default, and report stories without a diaphragm (3.6).
6. **`setup`:**
   - respect existing patterns or refuse;
   - fix WX = WY and add ± wind (3.7, 6).
7. **`grids`:** check story names before deleting; keep grid systems (3.9).
8. **GF corner column Ve:** trace it and show Mpr, P and lu in the report (3.10).
9. **Seismic factors:** R / Ω0 / ρ per Table 208-11A; a 90% RSA option (3.8, 6).
10. **UX:**
    - "use last settings";
    - field-specific errors and FAIL reasons;
    - natural sort;
    - menu matches;
    - input checks;
    - HSS and metric steel;
    - a `doctor` version check.

---

## 10. Evidence

- Real ETABS outputs, on branch `claude/amazing-mayer-rud2d9`:
  - `test-results/2026-10-05/`: doctor, spectrum, analyze, check, drift, beams, columns;
  - `test-results/2026-10-05-request-02/`: drift heights, wind patterns, GF columns, joints, deflection defaults, earth cover, untagged;
  - `test-results/2026-10-05-request-03/`: GF-C1 capacity shear, 250 mm earth cover crash.
- The requests themselves: `test-results/README.md`, `REQUEST_02.md`, `REQUEST_03.md`.
- The simulation scripts (fake ETABS, personas, model matrix) were run in the cloud session and are not in the repository.
