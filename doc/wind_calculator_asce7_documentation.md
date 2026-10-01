# ASCE 7 Directional Wind Load Calculator

`design/wind_calculator_directional_asce7.py` computes MWFRS wind pressures for a rectangular building with a symmetrical gable roof, using the ASCE 7 directional procedure in SI units.

## Workbook

`spreadsheets/wind_load_calculator_asce7.xlsm`. The buttons act on the active sheet.

| Button macro | Python function | Result |
|---|---|---|
| `RunDataLoad` | `calculate_wind_loads()` | Calculates and writes the results to the sheet |
| `ExportCalcs` | `export_pdf_wind_loads()` | Calculates, refreshes the sheet, and saves a PDF report |

Input cells:

| Cell | Input |
|---|---|
| `C2` | Building classification (text, reported only) |
| `C3` | Basic wind speed V (m/s) |
| `C4` | Enclosure classification, e.g. `Enclosed Buildings` |
| `C5` | Exposure category: `B`, `C` or `D` |
| `C6` to `C9` | Kd, Kzt, Ke, gust-effect factor G |
| `C13`, `C14` | Plan dimensions L and B (m) |
| `C15` | Direction of ridge: `L` or `B` |
| `C16` | Extra heights to report (m), comma separated |
| `C18`, `C19` | Eave height and apex height (m) |

Output cells:

| Cell | Output |
|---|---|
| `C10` | Velocity pressure without Kz, `0.613 Kd Kzt Ke V²` (Pa) |
| `C11`, `C12` | Internal pressure coefficients +GCpi and -GCpi |
| `B23` | Status: time of the last calculation, or the error message |
| `D23` | PDF export status |
| `B24` downward | Six result tables; `B24:K1000` is cleared on every run |

The six tables are the velocity pressure profile, wall Cp, roof Cp for wind normal to the ridge, roof Cp for wind parallel to the ridge, and the MWFRS pressure summary for each wind direction.

## Calculation

1. Velocity pressure `qz = 0.613 Kz Kzt Kd Ke V²`, with Kz interpolated from Table 26.10-1 at the listed heights, the eave, the mean roof height and the apex.
2. Wall Cp from Fig. 27.3-1: windward 0.8, side walls -0.7, leeward interpolated on L/B for each wind direction.
3. Roof Cp from Fig. 27.3-1:
   - wind normal to the ridge, slope of 10 degrees or more: interpolated on slope and h/L. For 60 degrees and steeper the windward value is `0.01 θ`.
   - wind normal to the ridge below 10 degrees, and wind parallel to the ridge at any slope: distance zones from the windward edge.
4. Net pressure `p = q G Cp - qh (GCpi)` for both signs of internal pressure. Windward walls use qz at each height; all other surfaces use qh at the mean roof height.

The reference tables are constants in the module, not read from the workbook.

## Python use

```python
from design.wind_calculator_directional_asce7 import WindLoadCalculatorDirectionalASCE7

calculator = WindLoadCalculatorDirectionalASCE7(
    building_class="Risk Category II", basic_wind_speed=60.0,
    enclosure_class="Enclosed Buildings", exposure_category="C",
    wind_dir_factor=0.85, topographic_factor=1.0, ground_elevation_factor=1.0,
    gust_effect_factor=0.85, l_input=20.0, b_input=10.0, ridge_direction_input="L",
    raw_heights="3, 4.5", eave_height=6.0, apex_height=8.0,
).calculate()

print(calculator.mwfrs_normal_summary)
calculator.generate_pdf_report(save_path="wind_report.pdf")
```

Running the file directly uses the inputs in the CONFIG block at the bottom, prints every table, and asks where to save the PDF. `xlwings` is needed only for the two workbook functions.

## Errors

Invalid inputs raise a `ValueError` that names the input: an exposure category other than B, C or D, a ridge direction other than L or B, non-positive dimensions, an apex below the eave, or an unknown enclosure classification. From the workbook the message is written to `B23`.

## Limitations

- Symmetrical gable roof on a rectangular plan only. The `OVERWRITES` cells for angle and mean roof height on the sheet are not read by the code.
- MWFRS only; components and cladding are not covered.
- Exposure category, Kzt and the gust-effect factor are inputs, not calculated.
- Roof Cp at 35 degrees should be checked against Fig. 27.3-1 of your ASCE 7 edition before use. The tabulated values at that slope are the same as at 30 degrees, which may be a transcription error from the original workbook.

## Tests

`tests/test_wind_calculator_asce7.py` checks the velocity pressure equation, Kz interpolation, the steep-roof coefficient, the zone method, wall coefficients, the net pressure equation and the input checks.
