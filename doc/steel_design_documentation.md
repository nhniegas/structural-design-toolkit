# Steel and Composite Design Modules

Two modules: the rectangular filled composite column designer, which has a workbook, and the general steel section designer, which currently runs from Python only.

## Composite column: `design/composite_column_designer_aiscDG06.py`

Rectangular or square concrete-filled steel box members, LRFD, per AISC Design Guide 6 (2nd edition) Section 2.5 and AISC 360 Chapter I.

### Workbook

`spreadsheets/composite_column_designer_aiscDG06.xlsm`, sheet `CONCRETE-FILLED`.

| Button macro | Python function | Result |
|---|---|---|
| `CalculateCapacity` | `calculate_capacity()` | Writes the results block from `B19` |
| `ExportCalcs` | `export_calcs()` | Asks where to save, writes a one-page PDF report |

Input cells:

| Cell | Input | Cell | Input |
|---|---|---|---|
| `C3` | Pu (kN) | `C9` | b (mm) |
| `C4` | Mb (kN-m) | `C10` | h (mm) |
| `C5` | Mh (kN-m) | `C11` | t (mm) |
| `C6` | Vb (kN) | `C12` | f'c (MPa) |
| `C7` | Vh (kN) | `C13` | fy (MPa) |
| | | `C14`, `C15` | Lb, Lh (m), unbraced lengths |

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
from design.composite_column_designer_aiscDG06 import RectangularFilledComposite, export_standalone_pdf

column = RectangularFilledComposite(
    b_mm=635, h_mm=635, t_mm=12.7, fc_mpa=41.37, fy_mpa=344.74,
    Lb_m=9.144, Lh_m=9.144, Pu_kN=6672.3, Mb_kNm=2440.5, Vbx_kN=400.3,
)
print(column.interaction_check())
export_standalone_pdf(column, "column_report.pdf")
```

The workbook export and `export_standalone_pdf` produce the same report.

### Limitations

- Noncompact and slender sections in flexure are reported as not available; that strength needs a first-yield moment that is not implemented.
- The bare-steel floor of DG6 p.32 (design strength not less than the bare steel section's) is not applied. The result is the composite strength only, which is conservative.
- Normal-weight concrete (145 pcf) and E = 29,000 ksi are fixed.

## General steel section: `design/general_steel_section_designer_aisc360.py`

AISC 360-22 capacity checks for doubly symmetric I-shapes, LRFD or ASD, with a one-page PDF report.

- **Sections**: rolled W, M, S and HP shapes from the `steelpy` database, or built-up shapes from plate sizes with `WideFlangeCapacity.from_plates`.
- **Checks**: classification (Table B4.1), tension (Chapter D), compression including slender elements (E3, E4, E7), flexure about both axes (F2 to F6, F13), shear including tension-field action and stiffeners (G2, G6), combined forces and torsion (H1 to H4).

```python
from design.general_steel_section_designer_aisc360 import WideFlangeCapacity

member = WideFlangeCapacity("W14X90", Fy=345, Fu=450, Lx=4000, Ly=4000, Lb=4000)
print(member.summary())
print(member.combined_forces_torsion_capacity(Pr=800, Mrx=150, Mry=20))
member.export_report("W14X90_report", Pr=800, Mrx=150, Mry=20, Vrx=100, Vry=10)
```

Lengths are in mm, forces in kN, moments in kN-m, stresses in MPa.

### Status

- No workbook is wired to this module yet. `export_calcs()` and its `INPUT_CELLS` map are ready for one.
- The module has no automated tests yet.
- Required strengths must already include second-order effects (Chapter C). Warping torsion is checked only if the bimoment and warping shear stress are supplied.
