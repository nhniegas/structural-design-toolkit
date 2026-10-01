---
title: "Steel & Composite Column Design Engines — Technical Documentation"
subtitle: "AISC 360-22 Wide-Flange Capacity Module & AISC Design Guide 6 Rectangular Filled Composite Module"
author: "NHN"
date: today
---

# 0. Scope of this document

This documents two design engines from the codebase:

| Module | File | Reference |
|---|---|---|
| Wide-flange / I-shape capacity checks | `general_steel_section_designer_aisc360.py` | AISC 360-22, Ch. B, D, E, F, G, H |
| Rectangular filled composite column | `composite_column_designer_aiscDG06.py` | AISC Design Guide 6 (2nd Ed.) §2.5, AISC 360 Ch. I |

For each module you get: purpose/scope, class & method reference, unit conventions, and a **fully worked manual computation** that reproduces the numbers the code itself would produce, so the module can be spot-checked by hand (or against a PE's calc pad) during QA.

> This file is plain Markdown/Pandoc-YAML front matter, so it renders as-is with `quarto render steel_composite_design_documentation.md` if you want a PDF/HTML calc-package output — no changes needed to use it as a `.qmd` either.

---

# 1. General Steel Section Designer — `WideFlangeCapacity`

## 1.1 Purpose & scope

`WideFlangeCapacity` performs **AISC 360-22 capacity checks for doubly-symmetric wide-flange / I-shaped members** (rolled W/M/S/HP from the `steelpy` database, or a welded built-up shape via plate dimensions). Scope, per the module docstring:

- **Ch. B** — Table B4.1a (axial compactness) / B4.1b (flexural compactness) classification
- **Ch. D** — D1–D3 tension (shear-lag `U`, no block shear, no pin-connected/eyebar provisions)
- **Ch. E** — E3/E4 flexural & torsional buckling, E7 slender-element effective area
- **Ch. F** — F2–F6, F13.1/F13.2 flexure (compact → slender web, all governing provisions)
- **Ch. G** — G2.1–G2.4 major-axis (web) shear incl. tension-field action & stiffener design, G6 minor-axis (flange) shear
- **Ch. H** — H1.1–H1.3 combined axial+flexure, H2 stress-interaction alternative, H3.3 torsion, H4 flange rupture at bolt holes

**Not covered:** second-order (P-Δ/P-δ) amplification of `Pr`/`Mr` — that must be applied by the caller (Chapter C) before passing demands in; block shear (a connection check, not a member check).

## 1.2 Unit convention

| Quantity | Input/working unit |
|---|---|
| Section dimensions, lengths | mm |
| Areas / section moduli | mm², mm³, mm⁴, mm⁶ |
| Stresses (`Fy`, `Fu`, `E`, `G`) | MPa |
| Forces (`Pr`, `Vr`) | kN |
| Moments (`Mr`, `Tr`) | kN·m |

`steelpy` returns inch-based AISC tables; `_load_steelpy()` converts with `IN = 25.4`.

## 1.3 Class & data model reference

```
SectionProps      geometry + section properties (d, bf, tf, tw, h, A, Ix, Iy, Zx, Zy, Sx, Sy, J, Cw, Qw, Wno)
                  + derived properties: ho, rx, ry, rts
Stiffener         transverse stiffener plate data (b, t, Fy, pair, c_end) for G2.4
CheckResult       one capacity check's full output: design_capacity, dcr, limit_states{}, classification, notes, warnings
WideFlangeCapacity  the engine — one instance per member per load case
```

`WideFlangeCapacity` is built two ways:

```python
# 1) rolled shape from the steelpy AISC database
member = WideFlangeCapacity("W14X90", Fy=345, Fu=450, Lx=3600, Ly=3600, Lb=3600)

# 2) welded built-up shape from plate sizes (mm) — used throughout §1.4 below
member = WideFlangeCapacity.from_plates(d=600, bf=300, tf=20, tw=16, name="BUILT-UP",
                                         Fy=345, Fu=450, Lx=4000, Ly=4000, Lb=4000)
```

### Method reference by chapter

| Method | Clause(s) | Returns |
|---|---|---|
| `classify_compression()` | Table B4.1a | dict: flange/web `lam`, `lam_r`, `cls`, overall `slender` flag |
| `classify_flexure(axis)` | Table B4.1b | dict: flange/web `lam`, `lam_p`, `lam_r`, `cls`, `section` |
| `tensile_capacity(Pr, An, U, L)` | D2(a)/(b) | `CheckResult`, governs on min(yield, rupture) |
| `compressive_capacity(Pr, Lx..Kz, xa, ya)` | E3/E4 (+E7 if slender) | `CheckResult`, governs on min(x, y, torsional) |
| `flexural_capacity(Mr, axis, Lb, Cb, moments, bolt_holes)` | F2–F6, F13.1 | `CheckResult` (major or minor axis) |
| `shear_capacity(Vr, axis, a, stiffener, tension_field, panel, Mr)` | G2.1–G2.4 / G6 | `CheckResult` |
| `combined_forces_torsion_capacity(...)` | H1/H2/H3.3/H4 | `CheckResult`, single governing DCR |
| `proportion_check(a)` | F13.2 | slender-web proportioning limits |
| `calc_Cb(Mmax, MA, MB, MC)` *(staticmethod)* | Eq. F1-1 | `Cb` |
| `export_report(filename, **demands)` | — | one-page PDF via `WideFlangeReport` |

`_avail()` implements the LRFD/ASD switch everywhere (`phi*Rn` vs `Rn/omega`); `alpha` = 1.0 (LRFD) or 1.6 (ASD) drives the H1 interaction equation split.

## 1.4 Sample manual computation

**Member:** welded built-up I-girder, `Fy = 345 MPa`, `Fu = 450 MPa`, `E = 200 000 MPa`, `G = 77 200 MPa`, LRFD.

| Plate | Dimension |
|---|---|
| Depth, `d` | 600 mm |
| Flange width, `bf` | 300 mm |
| Flange thickness, `tf` | 20 mm |
| Web thickness, `tw` | 16 mm |

Unbraced lengths: `Lx = Ly = Lz = Lb = 4000 mm`, `Kx = Ky = Kz = 1.0`, `Cb = 1.0`.

### Step 1 — Section properties (`from_plates`)

```
h  = d − 2tf              = 560.0 mm
ho = d − tf                = 580.0 mm
A  = 2·bf·tf + h·tw        = 2(300)(20) + 560(16)          = 20 960 mm²
Ix = bf·d³/12 − (bf−tw)·h³/12                                = 1.2438 × 10⁹ mm⁴
Iy = 2·tf·bf³/12 + h·tw³/12                                  = 90.19 × 10⁶ mm⁴
Zx = bf·tf·ho + tw·h²/4                                       = 4.7344 × 10⁶ mm³
Sx = Ix /(d/2)                                                = 4.1458 × 10⁶ mm³
Sy = Iy /(bf/2)                                                = 0.6013 × 10⁶ mm³
J  = (2·bf·tf³ + h·tw³)/3                                      = 2.3646 × 10⁶ mm⁴
Cw = Iy·ho²/4                                                  = 7.585 × 10¹² mm⁶
rx = √(Ix/A) = 243.6 mm    ry = √(Iy/A) = 65.6 mm    rts = √(√(Iy·Cw)/Sx) = 79.4 mm
```

### Step 2 — Classification (Ch. B)

**Axial (Table B4.1a):**
```
lambda_flange = bf/(2tf) = 300/40 = 7.50   vs  lam_r = 0.56√(E/Fy) = 13.48  ->  nonslender
lambda_web    = h/tw     = 560/16 = 35.00  vs  lam_r = 1.49√(E/Fy) = 35.87  ->  nonslender
```
Whole section: **nonslender** → E3/E4 uses `Pn = Fn·Ag` (no E7 reduction).

**Flexural, x-axis (Table B4.1b):**
```
flange: lambda = 7.50  vs  lam_p = 0.38√(E/Fy) = 9.15  ->  compact
web:    lambda = 35.00 vs  lam_p = 3.76√(E/Fy) = 90.53 ->  compact
```
Section is **compact** → governs by **F2** (yielding + LTB only, no local buckling terms).

### Step 3 — Tension (D2), `An = Ag` (no holes), `U = 1.0`

```
Pn(yield)    = Fy·Ag       = 345(20 960)/1000              = 7 231.2 kN  -> phi=0.90 -> phiPn = 6 508.1 kN
Pn(rupture)  = Fu·Ae       = 450(20 960)/1000               = 9 432.0 kN  -> phi=0.75 -> phiPn = 7 074.0 kN
Governing:  phiPn(t) = 6 508.1 kN  (tensile yielding on the gross section)
```

### Step 4 — Compression (E3/E4), `KL = 4000 mm` all axes

```
Fex = pi^2 E /(KLx/rx)^2 = pi^2(200 000)/(4000/243.6)^2      = 7 320.7 MPa
Fey = pi^2 E /(KLy/ry)^2 = pi^2(200 000)/(4000/65.6)^2       =   530.9 MPa   <- governs (smallest Fe)
Fez = (pi^2 E Cw/Lz^2 + G J)/(Ix+Iy)                          =   838.4 MPa
```
For the governing y-axis mode, `Fy/Fe = 345/530.9 = 0.650 ≤ 2.25` → inelastic buckling (Eq. E3-2):
```
Fn = 0.658^(Fy/Fe) · Fy = 0.658^0.650 (345)                  = 262.8 MPa
Pn = Fn·Ag = 262.8(20 960)/1000                                = 5 509.1 kN
phiPn = 0.90 Pn                                                 = 4 958.2 kN     <- y-axis flexural buckling governs
```

### Step 5 — Flexure, major axis, F2 (compact section)

```
Mp = Fy·Zx = 345(4.7344e6)/1e6                                 = 1 633.4 kN-m
Lp = 1.76 ry √(E/Fy) = 1.76(65.6)(24.08)                        = 2 780 mm
Jc = J/(Sx·ho) = 2.3646e6/(4.1458e6 × 580)                       = 9.83e-4
Lr = 1.95 rts (E/0.7Fy)√(Jc+√(Jc²+6.76(0.7Fy/E)²))                = 8 385 mm
```
`Lp = 2780 mm < Lb = 4000 mm ≤ Lr = 8385 mm` → **inelastic LTB, Eq. F2-2**:
```
Mn = Cb[Mp − (Mp − 0.7Fy Sx)(Lb−Lp)/(Lr−Lp)] ≤ Mp
   = 1.0[1633.4 − (1633.4 − 0.7(345)(4.1458e6)/1e6)(4000−2780)/(8385−2780)]
   = 1 495.7 kN-m
phiMnx = 0.90(1495.7)                                            = 1 346.2 kN-m
```

### Step 6 — Shear, major axis, unstiffened web, G2.1(b) (welded, so `h/tw` compared against 2.24√(E/Fy) test is bypassed for rolled-only; here `kv = 5.34`)

```
Aw = d·tw = 600(16)                                              = 9 600 mm²
h/tw = 35.00  vs  1.10√(kv E/Fy) = 1.10√(5.34·200000/345) = 61.2   ->  Cv1 = 1.0
Vn  = 0.6 Fy Aw Cv1 = 0.6(345)(9600)(1.0)/1000                    = 1 987.2 kN
phiVn = 0.90(1987.2)                                               = 1 788.5 kN
```

### Step 7 — Combined forces, H1 (`Pr = 1500 kN` compression, `Mrx = 550 kN-m`, `Mry = 0`)

```
Pc = phiPn = 4 958.2 kN        Mcx = phiMnx = 1 346.2 kN-m
Pr/Pc = 1500/4958.2 = 0.303  >=  0.2   ->  Eq. H1-1a governs

Ratio = Pr/Pc + (8/9)(Mrx/Mcx + Mry/Mcy)
      = 0.303 + (8/9)(550/1346.2 + 0)
      = 0.303 + 0.889(0.4086)
      = 0.666   <=  1.0   ->  OK  (DCR = 0.666)
```

**Summary table** (all values above, as `WideFlangeCapacity` would report them):

| Check | Governing clause | Capacity | Demand | DCR |
|---|---|---|---|---|
| Tension | D2(a) yielding | φPn = 6508.1 kN | — | — |
| Compression | E3, y-axis flexural buckling | φPn = 4958.2 kN | 1500 kN | 0.303 |
| Flexure (x) | F2.2 inelastic LTB | φMn = 1346.2 kN·m | 550 kN·m | 0.409 |
| Shear (x) | G2.1(b) | φVn = 1788.5 kN | — | — |
| Combined | H1-1a | — | — | **0.666 (OK)** |

## 1.5 Notes / gotchas for QA

- `classify_compression()` and `classify_flexure()` use **different** web/flange limits (B4.1a vs B4.1b) — a section can be simultaneously *slender for axial* and *compact for flexure* (this is common for deep, thin-webbed plate girders); always confirm which classification a given capacity call actually used.
- `compressive_capacity()` raises on `xa and ya` both non-zero (E4 doesn't cover biaxial bracing offset — needs an analysis-based approach).
- `shear_capacity(..., tension_field=True, panel="end")` silently no-ops (falls back to G2.1 only) if you don't also pass a `Stiffener` — check `res.warnings`.
- H1.3 (single-axis alternative) only activates automatically inside `combined_forces_torsion_capacity` if the section is **rolled**, compact, and `Mry/Mcy < 0.05` — a built-up shape (as above) never qualifies (`self.rolled` is `False`).

---

# 2. Composite Column Designer — `RectangularFilledComposite`

## 2.1 Purpose & scope

`RectangularFilledComposite` checks **rectangular/square concrete-filled steel HSS or welded box columns (CFT)** per **AISC Design Guide 6, 2nd Ed., §2.5** and **AISC 360 Chapter I**:

- Axial compression (`Pno`, `Pn`, `φPn`) including EI-effective column buckling and compact/noncompact/slender wall classification
- Axial tension (`φTn`)
- Flexural strength about either axis (plastic stress distribution, DG6 Fig. 2-13) — **compact sections only**
- Shear strength (Spec. Eq. I4-1) with optional `Kc` interpolation from shear-span-to-depth ratio
- Combined axial + biaxial flexure interaction (DG6 §2.5.6), both the standard linear form and the DG6-recommended `α = 1.5` form
- AISC 341-16 Table D1.1 seismic wall ductility classification

**Documented limitation (see module docstring):** noncompact/slender flexural strength (Spec. Eq. I3-5b) is **not implemented** — `flexural_strength()` returns a descriptive string instead of a number when the section isn't flexurally compact, rather than guessing at a first-yield moment `My` with no closed-form DG6 equation for rectangular shapes.

## 2.2 Unit convention

Constructor arguments are **metric** (mm, MPa, m, kN, kN·mm) — the `__init__` immediately converts everything to **US customary (in, ksi, kip, kip-in)** because the DG6 equations as coded are calibrated in that system:

```python
self.b = b_mm / 25.4        # in
self.fc = fc_mpa * 0.145038 # ksi
self.Pu = Pu_kN * 0.224809  # kip
self.Mux = Mbx_kNmm * 0.0088507  # kip-in
```

All public results (`interaction_check()`, `shear_strength()`, etc.) are returned **in kips / kip-in**; the Excel/PDF entry points (`_kips_to_kN`, `_kipin_to_kNm`) convert back to metric for display.

## 2.3 Class & method reference

```python
RectangularFilledComposite(
    b_mm, h_mm, t_mm, fc_mpa, fy_mpa, Lx_m, Ly_m,
    Pu_kN=0, Mbx_kNmm=0, Mhy_kNmm=0, Vbx_kN=0, Vhy_kN=0,
    Asr_mm2=0.0, Fysr_mpa=414.0,   # optional internal rebar
    ri_mm=0.0,                     # corner radius (0 = sharp/welded box)
    shear_span_to_depth=None,      # enables Kc interpolation
)
```

| Method | Clause(s) | Notes |
|---|---|---|
| `check_compactness_axial()` | Table 2-5 (walls, axial) | one limit set for both `b/t` and `h/t` |
| `check_compactness_flexure(axis)` | Table 2-5 (walls, flexure) | **different** flange/web limits than axial; `axis="b"` or `"h"` |
| `check_min_steel_ratio()` | Eq. I2.2a(a) | `As/Ag ≥ 1%` |
| `axial_compressive_strength()` | Eq. I2-9/I2-10 + column buckling | stores `self.Pno`, `self.Pn`; returns `φPn` |
| `axial_tensile_strength()` | Eq. I2-14 | `φTn = φt(As·Fy + Asr·Fysr)` |
| `flexural_strength(axis)` | DG6 Fig. 2-13 plastic stress dist. | compact only; includes corner-radius correction |
| `_compute_Kc()` / `shear_strength(axis)` | Eq. I4-1 + commentary | `Kc` = 1 (default), up to 10 for stocky, flexure-compact members |
| `interaction_check()` | DG6 §2.5.6 | returns both standard and `α=1.5` ratios, plus all classifications |
| `check_seismic_compactness(Ry)` | AISC 341-16 Table D1.1 | Highly / Moderately Ductile / Not Seismically Compact |

Excel/PDF layer (`calculate_capacity`, `export_calcs`, `export_standalone_pdf`) is a thin I/O wrapper around `interaction_check()` + `shear_strength()` — no additional engineering logic lives there.

## 2.4 Sample manual computation — reproduces `__main__` (DG6 Example 2.5)

**Input (metric, as passed to the constructor):**

| Parameter | Value |
|---|---|
| `b_mm`, `h_mm` | 635 mm × 635 mm |
| `t_mm` | 12.7 mm |
| `f'c` | 41.37 MPa |
| `Fy` (steel tube) | 344.74 MPa |
| `Lx = Ly` | 9.144 m |
| `Pu` | 6672.3 kN |
| `Mbx` | 2 440 472 kN·mm (= 2440.5 kN·m) |
| `Vbx` | 400.3 kN |

### Step 1 — Unit conversion (`__init__`)

```
b = h = 635/25.4        = 25.000 in
t      = 12.7/25.4       =  0.500 in
fc     = 41.37(0.145038) =  6.000 ksi
Fy     = 344.74(0.145038)=  50.000 ksi
Lx=Ly  = 9.144(39.3701)  = 360.00 in
Pu     = 6672.3(0.224809)= 1 500.0 kip
Mux    = 2 440 472(0.0088507) = 21 604.1 kip-in  (= 1800.3 kip-ft)
```

### Step 2 — Section properties

```
bi = hi = b − 2t = 25 − 1 = 24.000 in
Ac = bi·hi − 0.858 ri² = 24×24 − 0            = 576.00 in²
As = Ag − Ac = 625 − 576                       =  49.00 in²    (As/Ag = 7.84% ≥ 1% ✓)
Icx = Icy = bi·hi³/12 = 24(24)³/12             = 27 648 in⁴
Isx = Isy = b·h³/12 − Icx = 25(25)³/12 − 27648 =  4 904.1 in⁴
```

### Step 3 — Compactness (Table 2-5, axial)

```
lambda = max(bi/t, hi/t) = 24/0.5           = 48.00
lambda_p = 2.26√(Es/Fy) = 2.26√(29000/50)    = 54.43
lambda_r = 3.00√(Es/Fy)                       = 72.25
48.00 <= 54.43  ->  Compact
```
Flexural compactness (b-axis) uses the same flange limits here (square, `ri=0`) → also **Compact**.

### Step 4 — Axial compression (DG6 §2.5.2)

```
Pp = Fy·As + 0.85 fc (Ac + Asr·Es/Ec)  = 50(49) + 0.85(6)(576)   = 5 387.7 kip   (compact -> Pno = Pp)
C3 = min(0.45 + 3(As/Ag), 0.9) = min(0.45+3(0.0784), 0.9)          = 0.6852
Ec = wc^1.5 √fc = 145^1.5 √6                                        = 4 273 ksi
EIeff = Es·Isx + C3·Ec·Icx = 29000(4904.1) + 0.6852(4273)(27648)     = 2.245e8 kip-in²
Pe = pi² EIeff / Lx² = pi²(2.245e8)/360²                              = 17 000.9 kip
Pno/Pe = 5387.7/17000.9 = 0.317 <= 2.25  ->  inelastic column buckling
Pn = Pno(0.658^(Pno/Pe)) = 5387.7(0.658^0.317)                        = 4 718.5 kip
phi_c Pn = 0.75(4718.5)                                                = 3 538.8 kip  = 15 741.6 kN
```

### Step 5 — Flexural strength, b-axis (DG6 Fig. 2-13 plastic stress distribution)

```
Zs = b·h²/4 − bi·hi²/4 = 25(25)²/4 − 24(24)²/4                       =   450.25 in³
Zc = bi·hi²/4 (ri=0)                                                   = 3 456.00 in³
MD = Fy·Zs + 0.85 fc Zc/2 = 50(450.25) + 0.85(6)(3456)/2               = 31 325.8 kip-in

hn = 0.85 fc Ac / [2(0.85 fc bi + 4 Fy t)]
   = 0.85(6)(576) / [2(0.85(6)(24) + 4(50)(0.5))]                      =    6.604 in
Zsn = 2t·hn² = 2(0.5)(6.604)²                                           =   43.62 in³
Zcn = bi·hn²  = 24(6.604)²                                               = 1 046.8 in³

MB = MD − Fy·Zsn − 0.85 fc Zcn/2
   = 31 325.8 − 50(43.62) − 0.85(6)(1046.8)/2                            = 26 475.4 kip-in
phi_b Mnx = 0.90(26 475.4) = 23 827.8 kip-in                              = 2 692.2 kN-m
```

### Step 6 — Shear strength, b-axis (Eq. I4-1, `Kc = 1` — no `shear_span_to_depth` supplied)

```
Av = 2h·t = 2(25)(0.5)                                                    = 25.00 in²
Vn = 0.6 Fy Av + 0.06 Kc Ac √fc = 0.6(50)(25) + 0.06(1)(576)√6            =   834.7 kip
phi_v Vbx = 0.90(834.7) = 751.2 kip                                        = 3 341.5 kN
D/C = Vb,demand/phiVbx = 400.3/3341.5                                       = 0.120  ->  OK
```

### Step 7 — Combined interaction (DG6 §2.5.6)

```
chi = Pn/Pno = 4718.5/5387.7                                               = 0.8758
Pc_cross = 0.85 fc Ac = 0.85(6)(576)                                        = 2 937.7 kip
Pcc = phi_c · chi · Pc_cross = 0.75(0.8758)(2937.7)                          = 1 929.6 kip  = 8 583.2 kN

Pu = 1500 kip (6672.3 kN)  <  Pcc = 1929.6 kip (8583.2 kN)
->  axial term is OMITTED; interaction reduces to flexure only.

ratio_x = Mux/phiMnx = 2440.5/2692.2                                          = 0.9065

Standard:      Ratio = ratio_x                                                = 0.9065  (OK, <1.0)
Alpha = 1.5:   Ratio = (ratio_x^1.5)^(1/1.5) = ratio_x  (single-axis, alpha cancels)  = 0.9065  (OK)
```

**Summary table:**

| Check | Result |
|---|---|
| `As/Ag` | 7.84% ≥ 1% — OK |
| Axial compactness | Compact |
| Flexural compactness (b, h) | Compact / Compact |
| φPn | 3538.8 kip = 15 741.6 kN |
| φMnx | 23 827.8 kip-in = 2692.2 kN·m |
| φVbx | 751.2 kip = 3341.5 kN, D/C = 0.120 |
| Pcc | 1929.6 kip = 8583.2 kN (Pu < Pcc → flexure-only branch) |
| **Interaction (Standard / α=1.5)** | **0.907 / 0.907 — OK** |

These numbers match the order of magnitude reported in DG6 Example 2.5 (φPn ≈ 3540 kip, φMn ≈ 23 800 kip-in), confirming the port from the textbook example is internally consistent.

## 2.5 Notes / gotchas for QA

- Because `Pu < Pcc` in the worked example, the **bilinear axial term drops out entirely** — both the "Standard" and "α = 1.5" interaction ratios collapse to the same single-axis moment ratio. This is easy to misread as a bug; it's the correct DG6 §2.5.6 behavior. It only differs once `Pu ≥ Pcc`.
- `flexural_strength()` returns a **string**, not a float, whenever the section is noncompact/slender for flexure — always guard with `isinstance(result, str)` before arithmetic (the module's own `interaction_check()` already does this).
- `shear_strength()` defaults `Kc = 1.0` (conservative) unless `shear_span_to_depth` is supplied at construction — for stocky members this under-predicts shear strength by up to 10×; supply the ratio when `(Mr/Vr)/d < 0.7` is expected.
- `check_compactness_axial` and `check_compactness_flexure` again use **different limits** (same pattern as the steel-section module) — a wall can be compact for axial load and noncompact/slender for flexure, in which case `interaction_check()` reports `"N/A - Noncompact/Slender Flexure"` for both ratios rather than a number.

---

# 3. Cross-reference — Excel / xlwings integration

Both modules expose the same pattern for use from an Excel front-end:

```python
INPUT_CELLS = {...}          # named range -> constructor kwarg map
def calculate_capacity():    # "Calculate" button: read cells -> run checks -> write results block
def export_calcs():          # "Export PDF" button: same, + PyLaTeX report via generate_pdf()
```

`main.py` wires these through `xw.Book.caller()` so the same functions work from a VBA `RunPython` macro or interactively from VS Code against the currently-active workbook. When unit-testing either engine outside Excel, bypass the `xw.Book.caller()` calls entirely and instantiate `WideFlangeCapacity` / `RectangularFilledComposite` directly, as done throughout §1.4/§2.4 above — that keeps the pure engineering logic testable without an open workbook.