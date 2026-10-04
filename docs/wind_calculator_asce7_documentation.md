# ASCE 7 Directional Wind Load Calculator

`design/wind_calculator_directional_asce7.py` computes MWFRS wind pressures for a rectangular building with a symmetrical gable roof, using the ASCE 7 directional procedure in SI units.

## From the terminal

From the project folder, with the environment active:

```powershell
sdt wind
```

1. A dialog asks for the inputs. Choices are listed in brackets beside the box (type one of them); boxes marked optional may be left blank. The values typed last time are filled in again.
2. If a value is not valid, a message names it and the dialog opens again with your entries.
3. A second dialog asks what to do with the results: **Print the results in the terminal**, **Export a PDF calculation report**, or **Both**. Exporting asks where to save the PDF.

Closing either dialog stops without printing or saving anything. Running the file directly (`python design/wind_calculator_directional_asce7.py`) does the same.

Inputs:

| Input | Notes |
|---|---|
| Building classification | text, reported only |
| Basic wind speed V (m/s) | |
| Enclosure | `Enclosed Buildings`, `Partially Enclosed Buildings`, `Partially Open Buildings` or `Open Buildings` |
| Exposure category | `B`, `C` or `D` |
| Kd, Kzt, Ke, G | directionality, topographic, ground elevation and gust-effect factors |
| L, B (m) | plan dimensions |
| Ridge runs along | `L` or `B` |
| Heights for the qz profile (m) | extra heights to report, comma separated; optional |
| Eave height, apex height (m) | |

The printout and the PDF give the velocity pressure, GCpi, and six tables: the velocity pressure profile, wall Cp, roof Cp for wind normal to the ridge, roof Cp for wind parallel to the ridge, and the MWFRS pressure summary for each wind direction.

Every design module run from the terminal offers the same names:

| Name | What it is |
|---|---|
| `INPUTS` | the input fields, in dialog order |
| `calculate(values)` | runs the checks from a dict of the inputs, keyed as in `INPUTS` |
| `summary_text(result)` | the results as plain text |
| `export_pdf(result, path)` | writes the PDF report; returns its path, or `None` if LaTeX fails |
| `run()` | the terminal workflow (`sdt wind`) |

## Calculation

1. Velocity pressure `qz = 0.613 Kz Kzt Kd Ke V²`, with Kz interpolated from Table 26.10-1 at the listed heights, the eave, the mean roof height and the apex.
2. Wall Cp from Fig. 27.3-1: windward 0.8, side walls -0.7, leeward interpolated on L/B for each wind direction.
3. Roof Cp from Fig. 27.3-1:
   - wind normal to the ridge, slope of 10 degrees or more: interpolated on slope and h/L. For 60 degrees and steeper the windward value is `0.01 θ`.
   - wind normal to the ridge below 10 degrees, and wind parallel to the ridge at any slope: distance zones from the windward edge.
4. Net pressure `p = q G Cp - qh (GCpi)` for both signs of internal pressure. Windward walls use qz at each height; all other surfaces use qh at the mean roof height.

The reference tables are constants in the module.

## Python use

```python
from design import wind_calculator_directional_asce7 as wind

calculator = wind.calculate(dict(
    building_class="Risk Category II", basic_wind_speed=60.0,
    enclosure_class="Enclosed Buildings", exposure_category="C",
    wind_dir_factor=0.85, topographic_factor=1.0, ground_elevation_factor=1.0,
    gust_effect_factor=0.85, l_input=20.0, b_input=10.0, ridge_direction_input="L",
    raw_heights="3, 4.5", eave_height=6.0, apex_height=8.0,
))
print(wind.summary_text(calculator))
print(calculator.mwfrs_normal_summary)   # each table is also a DataFrame
wind.export_pdf(calculator, "wind_report.pdf")
```

## Errors

Invalid inputs raise a `ValueError` that names the input: an exposure category other than B, C or D, a ridge direction other than L or B, non-positive dimensions, an apex below the eave, or an unknown enclosure classification. From the terminal the message is shown and the input dialog opens again.

## Limitations

- Symmetrical gable roof on a rectangular plan only; the roof angle and mean roof height are worked out from the eave and apex heights.
- MWFRS only; components and cladding are not covered.
- Exposure category, Kzt and the gust-effect factor are inputs, not calculated.
- Roof Cp at 35 degrees should be checked against Fig. 27.3-1 of your ASCE 7 edition before use. The tabulated values at that slope are the same as at 30 degrees, which may be a transcription error from the original workbook.

## Tests

`tests/test_wind_calculator_asce7.py` checks the velocity pressure equation, Kz interpolation, the steep-roof coefficient, the zone method, wall coefficients, the net pressure equation and the input checks. `tests/test_design_cli.py` checks the terminal workflow.
