# ASCE 7 Directional Procedure Wind Load Calculator — Documentation

**File:** `wind_calculator_asce7.py`
**Purpose:** Computes MWFRS (Main Wind-Force Resisting System) wind pressures on an
enclosed/partially-enclosed/open, gable-roofed building per ASCE 7 (Directional
Procedure, SI units), and optionally exports a formatted PDF calculation report.

This is a standalone rewrite of the original `xlwings`-driven tool. It no longer
reads or writes an Excel workbook — every reference table that used to live on the
worksheet is now hardcoded in this file, and every project input is a plain Python
variable you edit directly in the script.

---

## 1. Requirements

```bash
pip install numpy pandas pylatex
```

PDF export additionally requires a working **LaTeX distribution** with `pdflatex`
on your system `PATH` (e.g. TeX Live, MiKTeX). If you only need the terminal
summary, LaTeX is not required — the import of `pylatex` at the top of the file
still needs to succeed, but you never have to call `generate_pdf_report()`.

`tkinter` (standard library) is used for the "Save As" file dialog. If no display
is available (headless server, SSH session with no X forwarding), the script
automatically falls back to a plain terminal prompt for the save path.

---

## 2. How to run it

1. Open `wind_calculator_asce7.py`.
2. Scroll to the bottom, section **`3. CONFIG`**, inside the
   `if __name__ == "__main__":` block.
3. Edit the constructor arguments to match your project (see the parameter table
   in Section 4 below).
4. Run:
   ```bash
   python wind_calculator_asce7.py
   ```
5. The script will:
   - Print every intermediate table (velocity pressure profile, wall Cp, roof Cp,
     MWFRS summaries) to the terminal.
   - Open a "Save As" dialog asking where to save the PDF report (or prompt in the
     terminal if no dialog can be shown).
   - Compile and save the PDF at the chosen location.

To skip the PDF entirely, comment out the last line of the `CONFIG` block
(`calculator.generate_pdf_report(...)`).

---

## 3. Using it as a module (no PDF, no prompts)

```python
from wind_calculator_asce7 import WindLoadCalculatorDirectionalASCE7

calc = WindLoadCalculatorDirectionalASCE7(
    building_class="Risk Category IV",
    basic_wind_speed=61.11,
    enclosure_class="Enclosed Buildings",
    exposure_category="D",
    wind_dir_factor=0.85,
    topographic_factor=1.0,
    ground_elevation_factor=1.0,
    gust_effect_factor=0.85,
    l_input=180,
    b_input=180,
    ridge_direction_input="B",
    raw_heights="10",
    eave_height=15,
    apex_height=20,
)
calc.calculate()                      # runs the full pipeline
calc.print_summary()                  # terminal output
calc.generate_pdf_report(
    save_path="my_project_report.pdf" # skips the file dialog entirely
)
```

All computed tables are also available as plain `pandas.DataFrame` attributes
after `calculate()` runs — see Section 6.

---

## 4. Input parameters (CONFIG block)

| Parameter | Type | Description |
|---|---|---|
| `building_class` | `str` | Risk category label, e.g. `"Risk Category IV"`. Informational only — does not affect the math (basic wind speed already reflects the risk category). |
| `basic_wind_speed` | `float` | Basic wind speed `V`, in **m/s**. |
| `enclosure_class` | `str` | One of `"Enclosed Buildings"`, `"Partially Enclosed Buildings"`, `"Partially Open Buildings"`, `"Open Buildings"`. Must match a `GCPI_TABLE` row exactly (case-insensitive). |
| `exposure_category` | `str` | `"B"`, `"C"`, or `"D"`. |
| `wind_dir_factor` | `float` | Wind directionality factor `Kd` (typically `0.85`). |
| `topographic_factor` | `float` | Topographic factor `Kzt` (`1.0` if no topographic effects). |
| `ground_elevation_factor` | `float` | Ground elevation factor `Ke` (`1.0` at sea level). |
| `gust_effect_factor` | `float` | Gust-effect factor `G` (`0.85` for rigid structures). |
| `l_input` | `float` | Building plan dimension `L`, in **m**. |
| `b_input` | `float` | Building plan dimension `B`, in **m**. |
| `ridge_direction_input` | `str` | `"L"` if the ridge runs along the `L` dimension, `"B"` if it runs along the `B` dimension. Controls which dimension is "along-ridge" vs "across-ridge" for each wind direction. |
| `raw_heights` | `str` | Comma-separated list of additional heights (m) to report `Kz`/`qz` at, e.g. `"10, 12.5, 15"`. Eave, mean roof, and apex heights are always added automatically — you don't need to repeat them here. |
| `eave_height` | `float` | Eave height, in **m**. |
| `apex_height` | `float` | Ridge/apex height, in **m**. |

**Not user-supplied:** `GCpi` (internal pressure coefficient) is *not* a direct
input — it's looked up automatically from `enclosure_class` against `GCPI_TABLE`
(Section 5). Mean roof height is also derived automatically as
`(eave_height + apex_height) / 2`.

---

## 5. Reference tables (hardcoded)

These replace the 5 Excel ranges the original tool read live off the worksheet.
They were extracted directly from your workbook (`_wind_load_calculator_asce7.xlsm`),
so they carry over any project-specific values or rounding your original sheet used.

| Constant | Replaces (Excel anchor) | Content |
|---|---|---|
| `KZ_TABLE` | `table_vel_pres_coef` (`L5`) | Velocity pressure exposure coefficient `Kz`/`Kh` vs. height, for Exposure B / C / D (Table 26.10-1). |
| `WALL_CP_RAW` | `wall_press_coeff_data` (`Q4`) | Windward wall (`Cp = 0.8`), leeward wall `Cp` vs. `L/B` (4 breakpoints), sidewall (`Cp = -0.7`). |
| `GCPI_TABLE` | `table_int_pres_coef` (`L31`) | `GCpi` (+/-) by enclosure classification. |
| `TABLE_ROOF_OVER_10` | `table_roof_over_10` (`R14`) | Roof `Cp`, wind normal to ridge, slope ≥ 10°: windward (8 angle breakpoints) × leeward (3 angle breakpoints) × 3 `h/L` tiers. |
| `TABLE_ROOF_UNDER_10` | `table_roof_under_10` (`S23`) | Roof `Cp`, stepped distance zones (`0–h/2`, `h/2–h`, `h–2h`, `>2h`), for `h/L ≤ 0.5` and `h/L ≥ 1.0`. |

If you ever need to update a coefficient (e.g. your office adopts a newer edition
of ASCE 7 with revised table values), edit these constants directly near the top
of the file — every downstream calculation reads from them.

---

## 6. Calculation pipeline (what `calculate()` does)

Calling `calculator.calculate()` runs these steps in order and stores each
result as an attribute on the object:

1. **Parse heights** — `raw_heights` string → `self.heights_list` (list of floats).
2. **Velocity pressure** —
   `vel_pres = 0.613 * V**2 * Kd * Kzt * Ke` → `self.vel_pres`.
3. **GCpi lookup** — match `enclosure_class` against `GCPI_TABLE` →
   `self.gcpi_pos`, `self.gcpi_neg`.
4. **Velocity pressure profile** — interpolates `Kz` (linear interpolation
   against `KZ_TABLE`) at every considered height (user heights + eave + mean
   roof + apex), then `qz = Kz * vel_pres` →
   `self.velocity_pressure_table`.
5. **Wall Cp table** — computes `L/B` (normal and parallel to ridge) and
   interpolates leeward-wall `Cp` against `WALL_CP_RAW` →
   `self.wall_cp_table`.
6. **Roof Cp — normal to ridge** — computes roof slope `θ` and `h/L`; if
   `θ < 10°` uses the stepped-zone table, otherwise performs a 2‑D
   (angle × `h/L`) bilinear interpolation against `TABLE_ROOF_OVER_10` →
   `self.roof_cp_display_normal` (display table) and
   `self.roof_cp_payload_normal` (values used downstream).
7. **Roof Cp — parallel to ridge** — always uses the stepped-zone table,
   regardless of pitch (per ASCE 7) →
   `self.roof_cp_display_parallel` / `self.roof_cp_payload_parallel`.
8. **MWFRS summary — normal to ridge** — combines wall + roof `Cp` with
   `q`, `G`, and `±GCpi` into net design pressures
   (`p = q·G·Cp − qᵢ·GCpi`) → `self.mwfrs_normal_summary`.
9. **MWFRS summary — parallel to ridge** — same, but the windward wall
   profile runs the full height (eave → apex, gable end) instead of
   stopping at the eave → `self.mwfrs_parallel_summary`.

### Governing equations

```
qz   = 0.613 · Kz · Kzt · Kd · Ke · V²      (velocity pressure, Pa)
p    = q · G · Cp − qᵢ · GCpi               (net design pressure, Pa)
```

Where `q = qz` (evaluated at height `z`) for windward walls, and `q = qh`
(evaluated at mean roof height) for leeward walls, side walls, and roof surfaces.
`qᵢ = qh` always, per ASCE 7 Directional Procedure.

---

## 7. Output attributes (after `calculate()`)

| Attribute | Type | Description |
|---|---|---|
| `velocity_pressure_table` | `DataFrame` | Height, type label, `Kz`, `qz` at every considered height. |
| `wall_cp_table` | `DataFrame` | Windward / leeward (normal & parallel) / side wall `Cp` and `L/B`. |
| `roof_cp_display_normal` / `roof_cp_display_parallel` | `DataFrame` | Human-readable roof `Cp` tables (with the low-`Cp`/high-`Cp` `*` notation preserved). |
| `roof_cp_payload_normal` / `roof_cp_payload_parallel` | `DataFrame` | Machine-readable roof `Cp` values feeding the MWFRS summary. |
| `mwfrs_normal_summary` | `DataFrame` | Final net design pressures, wind normal to ridge. |
| `mwfrs_parallel_summary` | `DataFrame` | Final net design pressures, wind parallel to ridge. |
| `vel_pres` | `float` | Base velocity pressure coefficient product (before `Kz`). |
| `gcpi_pos` / `gcpi_neg` | `float` | Resolved internal pressure coefficients. |
| `mean_roof_height` | `float` | `(eave_height + apex_height) / 2`. |

Every table follows the same column convention as the original Excel output:
`Net (+GCpi)` and `Net (-GCpi)` give the two internal-pressure load cases per
ASCE 7 (evaluate both; the controlling one governs for each element).

---

## 8. PDF report

`generate_pdf_report(output_filename=..., save_path=None)` builds a 3-section
PyLaTeX report:

1. **Design Parameters** — all inputs plus derived `GCpi` and base velocity
   pressure, in a summary table.
2. **Velocity Pressure Profile** — governing formula + full `Kz`/`qz` table.
3. **Pressure Coefficients** — wall Cp table, roof Cp tables (normal &
   parallel).
4. **MWFRS Pressure Summary** — governing formula + both final summary
   tables (normal & parallel to ridge), with `±GCpi` load cases.

If `save_path` is omitted, a native "Save As" dialog opens (title: *"Save ASCE 7
MWFRS Directional Procedure Report"*); if no display is available, you'll be
prompted for a path in the terminal instead. Pass `save_path` explicitly to skip
both and automate report generation (e.g. batch-processing multiple load cases).

The `.pdf` extension is stripped/re-added automatically — you can pass either
`"report"` or `"report.pdf"`.

---

## 9. Differences from the original Excel-based tool

| Item | Original | This version |
|---|---|---|
| Data source | Live `xlwings` reads from the workbook | Hardcoded constants, extracted from the same workbook |
| Inputs | Excel cells | Python constructor arguments (`CONFIG` block) |
| Output | Pasted back into Excel sheet | Printed to terminal (`print_summary()`) |
| PDF trigger | Excel button (VBA/xlwings) | Direct method call / script execution |
| "Roof (Roof (0 to h/2))" label bug (parallel-wind summary) | Present (cosmetic double-prefix) | **Fixed** — displays as `"Roof (0 to h/2)"` |
| Numerical results | — | **Identical** — validated against the workbook's worked example (`L=100, B=100, Exposure B, eave=20, apex=25`) to the last decimal place across every table |

No other behavioral changes were made. All interpolation logic (linear `Kz`
lookup, leeward-wall `L/B` interpolation, roof `Cp` bilinear interpolation, and
stepped-zone `h/L` interpolation) is a direct, line-for-line port of the
original algorithm.

---

## 10. Known limitations (carried over from the original tool)

- Assumes a **symmetrical gable roof**. Monoslope and mansard roofs are not
  supported.
- Exposure category and gust-effect factor are **manual inputs**, not
  auto-computed from upwind terrain or building period.
- Components & Cladding (C&C) pressures are **not included** — this tool
  covers MWFRS (Directional Procedure) only.
- `ridge_direction_input` only accepts `"L"` or `"B"` (case-insensitive);
  anything else is treated as `"B"` by the underlying `.upper() == "L"` check.

---

## 11. Sample hand calculation (step-by-step)

This walks through the same example used to validate the script against the
original workbook (Section 9), computed by hand, so you can verify — or
reproduce on a calculator — exactly how each number in the printed tables is
derived. Every intermediate value below matches `print_summary()`'s output
for this input set exactly.

### 11.1 Given

| Input | Value |
|---|---|
| Basic wind speed, `V` | 61.111 m/s |
| `Kd`, `Kzt`, `Ke` | 0.85, 1.0, 1.0 |
| Gust-effect factor, `G` | 0.85 |
| Exposure Category | B |
| Enclosure Classification | Enclosed Buildings |
| `L`, `B` | 100 m, 100 m |
| Ridge direction | `"B"` |
| Eave height | 20 m |
| Apex height | 25 m |
| Additional height to report | 15 m |

### 11.2 Step 1 — Base velocity pressure coefficient product

$$q_z = 0.613 \cdot K_z \cdot K_{zt} \cdot K_d \cdot K_e \cdot V^2$$

Everything except `Kz` is height-independent, so compute that product once:

```
0.613 × Kzt × Kd × Ke × V²
= 0.613 × 1.0 × 0.85 × 1.0 × (61.111)²
= 0.613 × 0.85 × 3734.568
= 1945.897   (call this "base_q")
```

`qz` at any height is then just `Kz(height) × 1945.897`.

### 11.3 Step 2 — Interpolate Kz at each height (Exposure B)

The mean roof height is always added automatically:
`h = (eave + apex) / 2 = (20 + 25) / 2 = 22.5 m`.

So four heights need `Kz`: the one you asked for (15 m), eave (20 m), mean
roof (22.5 m), and apex (25 m). `Kz` is linearly interpolated against the two
bracketing rows of `KZ_TABLE` (Exposure B column):

| Height (m) | Bracketing table rows (height → Kz) | Interpolation | Kz (rounded, 3 dp) |
|---|---|---|---|
| 15.0 | 12.2 → 0.76, 15.2 → 0.81 | 0.76 + (15.0−12.2)/(15.2−12.2) × (0.81−0.76) = 0.76 + 0.933×0.05 | **0.807** |
| 20.0 | 18.0 → 0.85, 21.3 → 0.89 | 0.85 + (20.0−18.0)/(21.3−18.0) × (0.89−0.85) = 0.85 + 0.606×0.04 | **0.874** |
| 22.5 | 21.3 → 0.89, 24.4 → 0.93 | 0.89 + (22.5−21.3)/(24.4−21.3) × (0.93−0.89) = 0.89 + 0.387×0.04 | **0.905** |
| 25.0 | 24.4 → 0.93, 27.4 → 0.96 | 0.93 + (25.0−24.4)/(27.4−24.4) × (0.96−0.93) = 0.93 + 0.200×0.03 | **0.936** |

General interpolation formula used throughout the script (and reused for
`L/B` and roof-angle lookups — same math, different axis):

$$y = y_1 + \frac{x - x_1}{x_2 - x_1}\,(y_2 - y_1)$$

### 11.4 Step 3 — Velocity pressure `qz` at each height

`qz = Kz(rounded) × base_q`, then rounded to 3 decimals:

| Height | Type | Kz | qz (Pa) |
|---|---|---|---|
| 15.0 m | User Input | 0.807 | 0.807 × 1945.897 = **1570.339** |
| 20.0 m | Eave Height | 0.874 | 0.874 × 1945.897 = **1700.714** |
| 22.5 m | Mean Roof Height | 0.905 | 0.905 × 1945.897 = **1761.036** ← this is `qh`, used for all leeward/side/roof pressures |
| 25.0 m | Apex Height | 0.936 | 0.936 × 1945.897 = **1821.359** |

### 11.5 Step 4 — Wall pressure coefficients

`L/B` (normal to ridge, since `ridge_direction = "B"` → `L/B = L/B = 100/100 = 1.0`):

- This lands **exactly** on a breakpoint in `WALL_CP_RAW` (`L/B = 1.0 → Cp = −0.5`), so no interpolation is needed here.
- Windward wall: `Cp = 0.8` (constant, always).
- Sidewall: `Cp = −0.7` (constant, always).
- Because the building is square (`L = B`), the parallel-to-ridge `L/B` is also `1.0`, so the parallel leeward `Cp` is also `−0.5`.

*(If your `L/B` falls between two breakpoints — e.g. `1.6` — interpolate linearly between the nearest two rows of the leeward table, `{0: −0.5, 1: −0.5, 2: −0.3, 4: −0.2}`, using the same formula as Step 2.)*

### 11.6 Step 5 — Roof geometry and Cp zone selection

```
run_dist = L / 2 = 50 m         (ridge_direction "B" → run is along L)
θ = atan[(apex − eave) / run_dist] = atan[(25 − 20) / 50] = atan(0.10) = 5.71°
h/L = 22.5 / 100 = 0.225
```

Since `θ = 5.71° < 10°`, the roof is treated as **low-slope** → use the
stepped-distance-zone table (`TABLE_ROOF_UNDER_10`), not the angle-based
interpolation table. Because `h/L = 0.225 ≤ 0.5`, use the four `h/L ≤ 0.5`
rows directly (no interpolation needed — they already cover this range):

| Zone (distance from windward edge) | Cp₁ | Cp₂ |
|---|---|---|
| 0 to h/2 | −0.9 | −0.18 |
| h/2 to h | −0.9 | −0.18 |
| h to 2h | −0.5 | −0.18 |
| >2h | −0.3 | −0.18 |

*(Two `Cp` values per zone because ASCE 7 requires both be checked; `Cp₂ =
−0.18` represents the "or −0.18" alternate case noted in Fig. 27.3-2's
footnote for low-slope roofs — both feed into the `±GCpi` load cases below.)*

### 11.7 Step 6 — Internal pressure coefficient

`enclosure_class = "Enclosed Buildings"` → from `GCPI_TABLE`:
`GCpi(+) = +0.18`, `GCpi(−) = −0.18`.

### 11.8 Step 7 — Net design pressures (MWFRS, wind normal to ridge)

$$p = q \cdot G \cdot C_p \;-\; q_h \cdot GC_{pi}$$

`q = qz` at that surface's height for the windward wall; `q = qh = 1761.036`
Pa for every leeward wall, sidewall, and roof zone. Two pressures are always
computed — one per `GCpi` sign — because ASCE 7 requires checking both:

**Windward wall, z = 15 m:**
```
q·G·Cp     = 1570.339 × 0.85 × 0.8   = 1067.83
qh·GCpi(+) = 1761.036 × 0.18         =  316.99
qh·GCpi(−) = 1761.036 × (−0.18)      = −316.99

p(+GCpi) = 1067.83 − 316.99  = 750.84 Pa
p(−GCpi) = 1067.83 − (−316.99) = 1384.82 Pa
```

**Windward wall, z = 20 m:** same formula with `qz = 1700.714`:
```
p(+GCpi) = (1700.714 × 0.85 × 0.8) − 316.99 = 839.50 Pa
p(−GCpi) = (1700.714 × 0.85 × 0.8) + 316.99 = 1473.47 Pa
```

**Leeward wall** (`q = qh`, `Cp = −0.5`):
```
p(+GCpi) = (1761.036 × 0.85 × −0.5) − 316.99 = −748.44 − 316.99 = −1065.43 Pa
p(−GCpi) = −748.44 + 316.99 = −431.45 Pa
```

**Side walls** (`q = qh`, `Cp = −0.7`):
```
p(+GCpi) = (1761.036 × 0.85 × −0.7) − 316.99 = −1047.82 − 316.99 = −1364.80 Pa
p(−GCpi) = −1047.82 + 316.99 = −730.83 Pa
```

**Roof, zone "0 to h/2"** (`q = qh`; two `Cp` values checked per zone):
```
Cp = −0.9:
  p(+GCpi) = (1761.036 × 0.85 × −0.9) − 316.99 = −1347.19 − 316.99 = −1664.18 Pa
  p(−GCpi) = −1347.19 + 316.99 = −1030.21 Pa

Cp = −0.18:
  p(+GCpi) = (1761.036 × 0.85 × −0.18) − 316.99 = −269.44 − 316.99 = −586.42 Pa
  p(−GCpi) = −269.44 + 316.99 = 47.55 Pa
```

The remaining roof zones (`h/2 to h`, `h to 2h`, `>2h`) follow the identical
procedure, substituting each zone's `Cp₁`/`Cp₂` from the Step 5 table — giving
the full `mwfrs_normal_summary` table reproduced below for cross-check:

| Surface | z (m) | q (Pa) | G | Cp | Net (+GCpi) | Net (−GCpi) |
|---|---|---|---|---|---|---|
| Windward wall | 15 | 1570.34 | 0.85 | 0.80 | 750.84 | 1384.82 |
| | 20 | 1700.71 | 0.85 | 0.80 | 839.50 | 1473.47 |
| Leeward wall | All | 1761.04 | 0.85 | −0.50 | −1065.43 | −431.45 |
| Side walls | All | 1761.04 | 0.85 | −0.70 | −1364.80 | −730.83 |
| Roof (0 to h/2) | — | 1761.04 | 0.85 | −0.90 | −1664.18 | −1030.21 |
| | — | 1761.04 | 0.85 | −0.18 | −586.42 | 47.55 |
| Roof (h/2 to h) | — | 1761.04 | 0.85 | −0.90 | −1664.18 | −1030.21 |
| | — | 1761.04 | 0.85 | −0.18 | −586.42 | 47.55 |
| Roof (h to 2h) | — | 1761.04 | 0.85 | −0.50 | −1065.43 | −431.45 |
| | — | 1761.04 | 0.85 | −0.18 | −586.42 | 47.55 |
| Roof (>2h) | — | 1761.04 | 0.85 | −0.30 | −766.05 | −132.08 |
| | — | 1761.04 | 0.85 | −0.18 | −586.42 | 47.55 |

The **wind-parallel-to-ridge** case (Section 8, `mwfrs_parallel_summary`) uses
the exact same `p = q·G·Cp − qh·GCpi` formula and the exact same roof zones —
the only difference is the windward wall is evaluated at *every* profile
height up to the apex (gable end), rather than stopping at the eave, since
the full triangular gable face is exposed to the wind in that direction.

### 11.9 Governing design pressures

For each element, the **larger-magnitude** of `Net (+GCpi)` and
`Net (−GCpi)` at each `Cp` typically governs strength design, but both signs
must be checked against the applicable load combinations — a positive
(inward) pressure and a negative (outward/suction) pressure can each govern
different failure modes (e.g. cladding attachment vs. uplift), so neither
value should be discarded.

---

## 12. Quick reference — file layout

```
wind_calculator_asce7.py
├── 1. REFERENCE TABLES        (KZ_TABLE, WALL_CP_RAW, GCPI_TABLE,
│                                TABLE_ROOF_OVER_10, TABLE_ROOF_UNDER_10)
├── 2. WindLoadCalculatorDirectionalASCE7  (the calculator class)
│   ├── interpolate_table_value()
│   ├── generate_velocity_pressure_profile()
│   ├── generate_wall_cp_table()
│   ├── generate_roof_cp()
│   ├── generate_mwfrs_normal_to_ridge_table()
│   ├── generate_mwfrs_parallel_to_ridge_table()
│   ├── calculate()             <- orchestrator, call this first
│   ├── print_summary()
│   └── generate_pdf_report()
└── 3. CONFIG                  (if __name__ == "__main__": block — edit here)
```