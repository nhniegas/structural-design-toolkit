# Steel and Composite Design Modules

Two modules: the rectangular filled composite column designer (`python main.py composite`) and the general steel section designer (`python main.py steel`). Both run from the terminal; neither needs Excel.

Every design module run from the terminal offers the same names:

| Name | What it is |
|---|---|
| `INPUTS` | the input fields, in dialog order |
| `calculate(values)` | runs the checks from a dict of the inputs, keyed as in `INPUTS` |
| `summary_text(result)` | the results as plain text |
| `export_pdf(result, path)` | writes the PDF report; returns its path, or `None` if LaTeX fails |
| `run()` | the terminal workflow (`python main.py ...`) |


## Composite column: `design/composite_column_designer_aiscDG06.py`

Rectangular or square concrete-filled steel box members, LRFD, per AISC Design Guide 6 (2nd edition) Section 2.5 and AISC 360 Chapter I.

### From the terminal

From the project folder, with the environment active:

```powershell
python main.py composite
```

1. A dialog asks for the inputs. Choices are listed in brackets beside the box (type one of them); boxes marked optional may be left blank. The values typed last time are filled in again.
2. If a value is not valid, a message names it and the dialog opens again with your entries.
3. A second dialog asks what to do with the results: **Print the results in the terminal**, **Export a PDF calculation report**, or **Both**. Exporting asks where to save the PDF.

Closing either dialog stops without printing or saving anything. Running the file directly (`python design/composite_column_designer_aiscDG06.py`) does the same.

Inputs: Pu (kN); Mb, Mh (kN-m); Vb, Vh (kN); b, h, t (mm); f'c, Fy (MPa); Lb, Lh (m), the unbraced lengths. The printout lists the compactness checks, phi Pn, phi Mn and phi Vn about each axis, the shear ratios and the interaction ratios with OK / NOT OK.

### Checks

- minimum steel ratio (As/Ag at least 1%)
- wall compactness for axial load, for flexure about each axis, and seismic compactness (AISC 341 Table D1.1, Ry = 1.3)
- axial compression with flexural buckling, and axial tension
- flexural strength by plastic stress distribution, compact sections only
- shear strength (Spec. Eq. I4-1)
- axial-flexure interaction, in the standard form and with exponent 1.5

Inputs are metric. The engine converts to US customary units internally and converts the results back.

### Python use

```python
from design import composite_column_designer_aiscDG06 as composite

column = composite.calculate(dict(
    Pu=6672.3, Mb=2440.5, Mh=0, Vb=400.3, Vh=0,
    b=635, h=635, t=12.7, fc=41.37, fy=344.74, Lb=9.144, Lh=9.144,
))
print(composite.summary_text(column))
composite.export_pdf(column, "column_report.pdf")
```

`calculate` returns a `RectangularFilledComposite`; its methods (`interaction_check()`, `report()`, ...) can also be called directly. The terminal and `export_pdf` produce the same report.

### Limitations

- Noncompact and slender sections in flexure are reported as not available; that strength needs a first-yield moment that is not implemented.
- The bare-steel floor of DG6 p.32 (design strength not less than the bare steel section's) is not applied. The result is the composite strength only, which is conservative.
- Normal-weight concrete (145 pcf) and E = 29,000 ksi are fixed.

## General steel section: `design/general_steel_section_designer_aisc360.py`

AISC 360-22 capacity checks for doubly symmetric I-shapes, LRFD or ASD, with a one-page PDF report.

### From the terminal

From the project folder, with the environment active:

```powershell
python main.py steel
```

1. A dialog asks for the inputs. Choices are listed in brackets beside the box (type one of them); boxes marked optional may be left blank. The values typed last time are filled in again.
2. If a value is not valid, a message names it and the dialog opens again with your entries.
3. A second dialog asks what to do with the results: **Print the results in the terminal**, **Export a PDF calculation report**, or **Both**. Exporting asks where to save the PDF.

Closing either dialog stops without printing or saving anything. Running the file directly (`python design/general_steel_section_designer_aisc360.py`) does the same.

Inputs: the section name (e.g. `W14X90`), Fy and Fu (MPa), LRFD or ASD, unbraced lengths Lx, Ly, Lz and Lb (m; a blank length takes Ly), K factors, Cb, compression or tension, the demands Pr (kN), Mrx, Mry (kN-m), Vrx, Vry (kN), Tr (kN-m), and optionally the stiffener spacing and plate size (mm) and tension-field action. The printout lists the section properties, every capacity table and the summary with the governing ratio, PASS or FAIL.

### Python use

- **Sections**: rolled W, M, S and HP shapes from the `steelpy` database, or built-up shapes from plate sizes with `WideFlangeCapacity.from_plates`.
- **Checks**: classification (Table B4.1), tension (Chapter D), compression including slender elements (E3, E4, E7), flexure about both axes (F2 to F6, F13), shear including tension-field action and stiffeners (G2, G6), combined forces and torsion (H1 to H4).

```python
from design import general_steel_section_designer_aisc360 as steel

check = steel.calculate({"section": "W14X90", "Fy": 345, "Fu": 450, "Ly": 4.0,
                         "Pr": 800, "Mrx": 150, "Mry": 20, "Vrx": 100, "Vry": 10})
print(steel.summary_text(check))
steel.export_pdf(check, "W14X90_report.pdf")
```

`calculate` takes lengths in m. The class can also be used directly, with lengths in mm:

```python
from design.general_steel_section_designer_aisc360 import WideFlangeCapacity

member = WideFlangeCapacity("W14X90", Fy=345, Fu=450, Lx=4000, Ly=4000, Lb=4000)
print(member.summary())
print(member.combined_forces_torsion_capacity(Pr=800, Mrx=150, Mry=20))
member.export_report("W14X90_report", Pr=800, Mrx=150, Mry=20, Vrx=100, Vry=10)
```

With the class, lengths are in mm; forces are in kN, moments in kN-m, stresses in MPa.

### Status

- `tests/test_design_cli.py` checks the terminal workflow and the uniform names; the capacity equations themselves have no automated tests yet.
- Required strengths must already include second-order effects (Chapter C). Warping torsion is checked only if the bimoment and warping shear stress are supplied.
