# Pile Cap Design (`sdt pilecap`)

`sdt pilecap` designs a reinforced concrete pile cap under one column: the pile reactions, the bottom and top bars and their anchorage, the shear checks, a strut-and-tie model for deep caps, and the least thickness that passes. It needs no ETABS: the inputs are typed in one window that draws the cap and its piles as you type, and the column loads are typed or read from an Excel or CSV file. The results appear in the same window, which stays open for the next run; they can be printed in the terminal or written as a PDF calculation report.

Code: `design/concrete/pile_cap.py` (the calculation), `pile_cap_window.py` (the window) and `pile_cap_report.py` (the PDF).

## Codes

NSCP 2015 governs. Where it leaves a point open, the program follows the documents below, and the report says which one each check comes from.

| Document | What it gives |
|---|---|
| NSCP 2015, Volume 1 | Section 413.4.2 (pile caps), 407, 409, 421, 422 (shear), 423 (strut-and-tie), 424, 425 (development), 418.13 (foundations in Zone 4), 306.6 and 308.4.1 (piles) |
| ACI 318M-14 with commentary | The basis of NSCP Chapter 4; cited for its commentary: R13.2.6.3 (strut-and-tie for pile caps), R13.4.2.4, R13.4.2.5 (piles close to the column) |
| CRSI, Design Guide for Pile Caps, 1st ed., 2015 | Written to ACI 318-14. The rigid cap with pinned piles, the pile placement tolerance, the minimum steel, uniform short bars, the shear checks near the column and around the piles, the recommended spacing and edge distance |
| FHWA GEC 12 (FHWA-NHI-16-009), 2016 | Pile head fixity (7.3.7.1) and, quoting AASHTO LRFD 10.7.1.2, the minimum pile spacing, edge distance and embedment (7.10.1) |

The size effect factor λs of ACI 318-19 is not used: NSCP 2015 has none.

## How to use it

```powershell
sdt pilecap
```

One window opens, in three parts:

| Part | What it does |
|---|---|
| Inputs (left) | Every input in one form that scrolls, in four sections: column and piles, pile capacity, cap and reinforcement, column loads. Choices are drop-down lists; the loads file has a Browse button. What you typed last time is offered again. Only the inputs that apply to your choices are shown (see below) |
| Plan (middle) | The cap, the column and the piles, numbered, redrawn a moment after each change, with the cap size and the least pile spacing. After a run it also shows under each pile its largest factored reaction and any uplift (red), colours each pile by its service load over its compression capacity (green up to 0.9, amber to 1.0, red above), and draws the two-way section at d/2 and the one-way sections at d |
| Results (right) | The result, the bars, the checks with the highest ratios first, the recommendations not met, and the notes |

The buttons: **Run design** (or Ctrl+Enter) designs the cap with the inputs as they are; change a value and run again. **Print in the terminal** prints the full results, every check with its clause. **Save PDF report** writes the calculation report. **Close** ends the command; the terminal then shows a short summary.

**Inputs that follow your choices.** The form shows only what applies:

| Choice | Shown |
|---|---|
| Pile layout: Standard | Number of piles, spacing, edge distance |
| Pile layout: Coordinates | The pile coordinates (`x,y; x,y; ...` in mm from the column centre) and the cap length along X and Y |
| Column shape: Rectangular | The column size along Y (a circular column has its diameter only) |
| Pile heads: Fixed | The ground (firm or soft) and the depth to fixity |
| Column loads from: Typed / File | The box of typed loads, or the loads file |

A box that may be left blank says in its label what a blank means: a bar spacing left blank is designed, a column bar left blank is not checked, a depth to fixity left blank is 1.5 m in firm ground and 3.0 m in soft.

With an answers file (`sdt pilecap --answers file.json`) the window is not opened: the inputs are asked in four dialogs, one per section, which the file answers.

| Section | Inputs |
|---|---|
| Column and piles | Column rectangular or circular and its size; the column bar diameter (to check its development into the cap); the pile layout: **Standard** (number of piles, spacing, edge distance) or **Coordinates** (x,y of each pile from the column centre, and the cap size); pile diameter and shape; embedment; placement tolerance (75 mm by default, CRSI 5.2) |
| Pile capacity | The method (below); the capacity in compression, tension and lateral load; the pile heads pinned or fixed; firm or soft ground; the depth to fixity |
| Cap and reinforcement | Thickness, and whether to find the least thickness that passes; f'c, fy; bottom and top bar diameters, and their spacing if you want it checked as given; clear cover above the piles and to the top bars; unit weights, soil over the cap, surcharge; the critical section for moment; the method for deep caps (below) |
| Column loads | Typed, or from a file: Browse for it, or leave it blank and it is asked when you run |

### Standard layouts

2, 3, 4, 5, 6, 7, 8, 9, 10 or 12 piles, centred on the column, at the spacing you type: a line of two, a triangle, a square, a square with a centre pile, grids of 2 x 3, 3 x 3 and 3 x 4, a hexagon with a centre pile, and rows of 3-2-3 and 3-4-3. The cap is a rectangle reaching the edge distance beyond the outer piles. For any other group, type the coordinates and the cap size.

### Loads

One combination per row, at the base of the column, in kN and kN-m:

| Column | Meaning |
|---|---|
| Name | The name of the combination |
| Type | `S` for a service (allowable stress) combination, `F` for a factored one |
| P | Axial load, compression positive |
| Mx, My | Moments: Mx increases the reaction of the piles on +Y, My those on +X |
| Vx, Vy | Shears at the top of the cap |
| WE (optional) | `Yes` when the combination has wind or earthquake (for the one-third increase). Left out, it is read from the name: a name with E, EQ, RSA, W, WX, WY and the like has it |
| DF, LF (optional) | The factors on the cap and soil weight, and on the surcharge. Left out, a factored combination is checked with 1.2 and with 0.9 (1.6 and 0 on the surcharge), a service one with 1.0 and with 0.6 |

Typed in the dialog, the rows are separated by `;`: `D+L, S, 1600, 0, 0, 0, 0; 1.2D+1.6L, F, 2200, 0, 0, 0, 0`. In a file (`.xlsx` or `.csv`) the first row holds the column names.

## What it calculates

### Pile reactions

The cap is rigid and the pile heads pinned (CRSI 3.3, 6.2): each pile takes R = (P + W)/n + My' x / Σx² + Mx' y / Σy², with x and y from the centroid of the piles, W the weight of the cap, the soil over it and the surcharge, and My' = My + Vx (h − embedment). A skewed group uses the full rigid-cap equations. Piles in one line cannot resist a moment about that line; the program then fails the check and asks for tie beams or a wider group. The lateral load is shared equally (CRSI 3.3).

### Pile capacity

| Method | Checked |
|---|---|
| Allowable | Service reactions against the allowable capacities (NSCP 413.4.1.1, 203.4.1) |
| Allowable + 1/3 for wind and earthquake | The same, with the capacities increased by one third for combinations with wind or earthquake (NSCP 203.4.2) |
| Factored resistance | Factored reactions against the factored resistances you type |

### Pile heads

**Pinned** (the default): the piles take axial load and shear. **Fixed**: each pile head also takes M = H Lf / 2, with H its share of the lateral load and Lf the depth to fixity (NSCP 306.6: 1.5 m in firm ground, 3 m in soft, or the value you type); the cap is designed for those moments too. FHWA GEC 12 (7.3.7.1) says a fixed head needs an embedment of two to three pile diameters or a designed connection; the program reports it as a recommendation, and gives the uplift and moment the anchorage must develop, with at least 25 % of the pile's tension strength (NSCP 308.4.1).

### Flexure and bars

The moment is taken at the face of the column (NSCP 413.2.7.1; a circular column as a square of equal area, 413.2.7.3), from every force on one side (413.2.6.4), with the pile reactions at the pile centres (413.4.2.2), each pile moved 75 mm farther from the column (CRSI 5.2). The CRSI section at c/4 from the column centre can be chosen instead.

- The bars are designed tension controlled, φ = 0.90 (NSCP 421.2.2).
- The minimum steel is read as CRSI 4.3 reads the code: the beam minimum of NSCP 409.6.1.2, or 4/3 of the steel needed (409.6.1.3), but not less than the shrinkage steel of Table 424.4.3.2 (0.0020 for bars under 420 MPa, as Grade 60 is).
- The bars along the longer side are the lower layer. In a rectangular cap the short bars are multiplied by 2β/(β + 1) so they can be spaced uniformly (NSCP 413.3.3.3, CRSI 4.2).
- The spacing is rounded down to 25 mm, at most min(3h, 450 mm) (NSCP 407.7.2.3).
- Each bar is developed from the column face to its end (NSCP 413.2.8.3), also when the moment is taken at c/4: straight if ℓd fits, else hooked (425.4.3.1, ψc = 0.7 for the side cover of a cap); the report says which. ℓd is the lesser of Table 425.4.2.2 and Eq. 425.4.2.3a with Ktr = 0, cb the least of the cover to the bar centre, the side cover to the bar centre and half the spacing, and (cb + Ktr)/db at most 2.5.
- **Top bars** are designed wherever a section has tension on top, from a pile in tension or the cap's own weight, in any factored combination (NSCP 414.1.5: no plain concrete pile caps; 418.13.2.4).

### Shear

| Check | Clause |
|---|---|
| One-way shear at d from the column face, across the whole cap, both directions | NSCP 413.4.2.3(a), 422.5.5.1: φVc = 0.75 × 0.17 λ √f'c b d |
| Two-way shear at d/2 around the column | NSCP 413.4.2.3(b), Table 422.6.5.2 |
| Two-way shear around each pile, around piles whose sections overlap, around corner piles to the cap edges; one-way shear across a corner pile | NSCP 422.6.5.2, 422.5.5.1; CRSI 5.4 (P1 to P4) |

A pile reaction loads a section in full when its centre is half a pile diameter or more outside it, not at all half a diameter inside, and in proportion between (NSCP 413.4.2.5).

Around piles, the program checks each pile alone, and each pair of piles closer than dp + d (their sections at d/2 overlap) around both together. A section that reaches a cap edge stops there: the perimeter is the part inside the cap. For a corner pile it is also allowed to run straight to the two edges, b0 = π(dp + d)/4 + ex + ey (CRSI P3), whichever is less.

### Deep caps

A cap is deep when a pile centre is closer to the column face than d (or d/2 for two-way shear): the sections at d then miss its reaction (ACI R13.4.2.5, CRSI 5.3). For such a cap the one-way and two-way shear checks above are shown for information, and the shear is governed by one of two methods, chosen in the dialog:

- **Strut-and-tie** (the default; NSCP 413.2.6.3 and Section 423). A strut from the column to each pile, ties along X and Y at the bottom bars. The node under the column is at the quarter point of the column toward the pile and its depth holds the horizontal strut; the lever arm follows from it. Struts use βs = 0.60 (NSCP 413.4.2.4); the pile nodes are CTT (βn = 0.60) or CCT (0.80), the column node CCC (1.0). The tie force is added to the bottom bars, which are developed past the outer pile centre (NSCP 423.8.3; CRSI 5.3).
- **CRSI special checks** (CRSI 5.3 and 5.4, limit states 4 to 6): shear at the column face with the higher strength of a deep member, converted to MPa.

**A limit of the strut-and-tie method.** With βs = 0.60, the strut at the column node takes a column stress of at most 0.75 × 0.85 × 0.60 f'c of the cap, about 0.38 f'c, whatever the thickness. When the column is stressed more, no thickness passes: the program says so, under Bearing, and names the ways out: a stronger cap concrete, a larger column, or the CRSI special checks. This is why the CRSI guide does not use the strut-and-tie method for its tabulated caps.

### Column bearing

The column bears on the cap (NSCP 422.8.3, Table 422.8.3.2):

φBn = 0.65 × min(√(A2/A1), 2) × 0.85 f'c A1

with φ = 0.65 for bearing (Table 421.2.1(d)), A1 the column area, and A2 the base of the largest frustum under the column that is sloped 1 vertical to 2 horizontal and lies wholly within the cap. The program takes A2 as the column grown on every side by the least of 2h and the distances from the column to the cap edges.

A column load over φBn is carried by reinforcement across the interface (NSCP 416.3.1.2): the column bars extended into the cap, or dowels. The program reports the area needed, the excess over 0.65 fy and at least 0.005 Ag (416.3.4.1), and writes a note. This does not fail the cap, since the bars belong to the column design; the column's own bearing strength (416.3.3.4) is not checked either, because the column's f'c is not an input.

Where a strut-and-tie model governs, NSCP 422.8.1.2 says this section does not apply: the node under the column is checked instead (CCC, βn = 1.0).

### Least thickness

With "find the least thickness" on, the program starts from the thinnest cap with d of 300 mm (NSCP 413.4.2.1) and adds 50 mm until every check passes, up to 4 m. It stops early when only checks a thicker cap does not change still fail (pile capacity, a moment across a line of piles, bearing). The report lists every thickness tried and what failed.

### Detailing and recommendations

Code limits: d of at least 300 mm (NSCP 413.4.2.1); 75 mm cover against earth (Table 420.6.1.3.1); the column bars developed into the cap with hooks resting on the bottom bars (418.13.2.1, 418.13.2.2).

Recommendations, shown but never failing the design since NSCP sets no limit for them:

| Item | Recommended |
|---|---|
| Pile spacing | At least 2.5 dp and 760 mm (AASHTO LRFD 10.7.1.2, from FHWA GEC 12); CRSI: 3 dp, dp + 600 mm and 900 mm |
| Cap edge to the side of a pile | At least 230 mm (AASHTO LRFD 10.7.1.2) |
| Edge distance to the pile centre | CRSI 4.3, by the pile load: 381 mm up to 60 tons, 533 mm to 120, 686 mm to 200, 762 mm to 280, 914 mm above |
| Pile embedment | 300 mm, or 150 mm where the pile is anchored with bars (AASHTO LRFD 10.7.1.2); CRSI: 100 mm for concrete piles, 150 mm for steel |

## Worked examples

The two examples are Design Examples #1 and #2 of the CRSI *Design Guide for Pile Caps* (2015), worked again to NSCP 2015 in SI units. CRSI's own values are given beside each step. Both examples are in `tests/test_pile_cap.py`, so a change that moves these numbers fails the tests.

**Why some numbers differ from CRSI.** CRSI uses φ = 0.85 for shear with its load factor of 1.6 on all loads. NSCP 2015 uses φ = 0.75 (Table 421.2.1(b)), so a CRSI shear capacity × 0.75/0.85 is the NSCP value. CRSI takes one d = Dcap − 10 in for both directions; the program takes d of each bar layer. NSCP's 0.33√f'c (MPa) is 0.7 % under ACI's 4√f'c (psi), and 0.17√f'c is 2.3 % over 2√f'c. The demands (reactions, moments, shears) carry none of these differences and match exactly.

### Example 1: 16 piles (CRSI Design Example #1)

**Data**

| Item | US (CRSI) | SI |
|---|---|---|
| Piles | 16 piles of 8 in, 4 × 4 at 36 in | 203 mm at 914 mm |
| Pile service load | 80 k (40 tons) | 355.9 kN |
| Cap | 11'-6" × 11'-6" × 48 in | 3505 × 3505 × 1219 mm |
| Column | 22 in square | 559 mm |
| Materials | f'c = 3,000 psi, fy = 60,000 psi | 20.7 MPa, 413.7 MPa |
| Piles embedded / clear cover above them | 6 in / 3 in | 152 / 76 mm |
| Bars | #11 | 35.8 mm |
| Placement tolerance, piles moved away from the column | 3 in | 76 mm |
| Edge distance to the pile centre, E | 15 in | 381 mm |

Inputs: Coordinates layout, the CRSI section at c/4, deep-cap method "CRSI special checks", combinations 1.6(D+L) factored and D+L service (both as in CRSI, with 1.6 on the cap weight).

**1. Loads and reactions** (CRSI 3.3; rigid cap, pinned piles)

- Cap weight W = 23.56 kN/m³ × 3.505² × 1.219 = 353.0 kN (CRSI: 150 lb/ft³).
- Column load: Pu = 1.6 × 16 × 355.9 − 1.6 × 353.0 = 8,545 kN, the most the piles allow (CRSI: 1,921 k).
- Each pile, factored: (8,545 + 1.6 × 353.0) / 16 = **569.4 kN** (CRSI: 1.6 × 80 = **128 k** ✓).

**2. Effective depth**

- Lower layer (X): d = 1219 − 152 − 76 − 35.8/2 = **972.7 mm** (38.3 in); upper layer (Y): 936.9 mm (36.9 in); average 954.8 mm. CRSI takes d = 38 in for both.

**3. Moment** at c/4 = 139.7 mm from the column centre (CRSI 5.2), the piles moved 76 mm outward:

- Piles: 4 × 569.4 × (457.2 + 76.2 − 139.7) + 4 × 569.4 × (1371.6 + 76.2 − 139.7) = 896.7 + 2,979.3 = 3,876.0 kN·m
- Cap weight beyond the section: 1.6 × 353.0 × (1752.6 − 139.7)/3505.2 = 259.9 kN, at 806 mm: −209.6 kN·m
- **Mu = 3,666 kN·m** (CRSI: **2,704 k-ft** = 3,666 kN·m ✓)

**4. Bars** along X (NSCP 409.6.1.2, 409.6.1.3, Table 424.4.3.2; CRSI 4.3)

- For flexure: Rn = Mu/(φ b d²) = 3,666 × 10⁶ / (0.90 × 3505 × 972.7²) = 1.228 MPa → As = 10,504 mm².
- Minimum: ρ = max(0.25√f'c/fy, 1.4/fy) = 0.00338, so ρbd = 0.00338 × 3505 × 972.7 = 11,538 mm². 4/3 As = 14,005 mm². The shrinkage steel is 0.0020 × 3505 × 1219 = 8,547 mm².
- Since As < ρbd ≤ 4/3 As, CRSI's case 2 applies: **As = 11,538 mm²** (17.9 in²; CRSI 17.5 in² with 200/fy and d = 38 in).
- Bars: 11,538 / 1006 = 11.5, so 12 bars. The width for the bars is 3505 − 2 × 75 − 35.8 = 3,319 mm, giving s = 3,319/11 = 302 mm, rounded down to **300 mm**: 12 × 35.8 mm @ 300, As = 12,089 mm². (CRSI: 11 #11 @ 13.1 in, 17.2 in².)

**5. Development** (NSCP 413.2.8.3, Eq. 425.4.2.3a)

- cb = least of 246 mm (cover to the bar centre), 93 mm (side cover 75 + db/2) and 150 mm (s/2) = 93 mm; (cb + Ktr)/db = 93/35.8 = 2.59, so 2.5 is used.
- ℓd = fy ψt ψe ψs / (1.1 λ √f'c × 2.5) × db = 413.7 / (1.1 × 4.548 × 2.5) × 35.8 = **1,185 mm** (CRSI: 46.3 in = 1,176 mm).
- Available from the column face: 1752.6 − 279.4 − 75 = **1,398 mm** (CRSI: 55 in). Straight bars ✓.

**6. Is the cap deep?** w, the distance from the column face to the nearest pile centre with the tolerance, is 914/2 − 559/2 + 76 = 254 mm (CRSI: 10 in). This is less than d/2 = 477 mm, so the CRSI special checks govern (CRSI 5.3). The ordinary checks of steps 7 and 8 are shown for information.

**7. Two-way shear at d/2 around the column** (CRSI LS 1, for information; NSCP 422.6.5.2, 413.4.2.5)

- The section is at 279.4 + 477.4 = 757 mm from the centre. The inner piles, at 533 mm with the tolerance, are more than dp/2 inside it and count zero; the 12 outer piles count in full.
- Vu = 12 × 569.4 − 1.6 × 23.56 × 1.219 × (3.505² − 1.514²) = 6,833 − 459 = **6,373 kN** (CRSI: **1,433 k** = 6,374 kN ✓).
- b0 = 4(559 + 955) = 6,054 mm; vc = least of 0.33√f'c, 0.17(1 + 2/1)√f'c and 0.083(2 + 40 × 955/6054)√f'c, so 0.33 × 4.548 = 1.501 MPa.
- φVc = 0.75 × 1.501 × 6,054 × 954.8 = **6,507 kN** (CRSI 1,698 k × 0.75/0.85 = 1,498 k = 6,664 kN, with d = 38 in).

**8. One-way shear at d** (CRSI LS 2, for information): Vu = 2,197 kN (CRSI 494 k ✓). φVc = 0.75 × 0.17√f'c b d = 1,977 kN, so it would fail. CRSI also finds it N.G. (494 k > 488 k) and passes the cap on LS 5, as here.

**9. Two-way shear at the column face** (CRSI LS 4)

- Every pile is outside the column: Vu = 16 × 569.4 − 1.6 × 353.0 × (1 − 559²/3505²) = 9,110 − 550 = **8,560 kN** (CRSI: **1,924 k** ✓).
- vc = (d/w)(1 + d/c) 0.166√f'c = (954.8/254)(1 + 954.8/558.8) × 0.166 × 4.548 = 7.69 MPa ≤ 2.66√f'c = 12.10 MPa (CRSI: 1,135 psi = 7.83 MPa with d = 38 in).
- φVc = 0.75 × 7.69 × (4 × 558.8) × 954.8 = **12,304 kN** (CRSI 3,226 k × 0.75/0.85 = 2,846 k = 12,661 kN). Ratio 0.70 ✓.

**10. One-way shear at the column face** (CRSI LS 5 and 6)

- The 8 piles beyond the face: Vu = 8 × 569.4 − 1.6 × 353.0 × (1752.6 − 279.4)/3505.2 = 4,555 − 237 = **4,318 kN** (CRSI: **971 k** ✓).
- vc from CRSI's deep-beam expression is 705 psi, over the limit of 10√f'c, so vc = 0.83√f'c = 3.775 MPa.
- φVc = 0.75 × 3.775 × 3505 × 972.7 = **9,653 kN** (CRSI 2,440 k × 0.75/0.85 = 2,153 k = 9,577 kN, d = 38 in). Ratio 0.45 ✓.

**11. Around the piles** (CRSI P1 to P4; no reduction for the cap weight)

| Check | Program | CRSI |
|---|---|---|
| P3, corner pile: b0 = π(dp + d)/4 + 2E = π(203 + 955)/4 + 2 × 381 | b0 = 1,671 mm, φVc = 0.75 × 1.501 × 1671 × 954.8 = 1,796 kN; Vu = 569 kN | b0 = 66.1 in, φVc = 468 k (413 k at φ 0.75); Vu = 128 k |
| P2, two adjacent piles | b0 = 2,655 mm (the part inside the cap), φVc = 2,854 kN; Vu = 1,139 kN | b0 = 216 in = 5,486 mm, with no cap edge counted |
| P4, one-way across the corner pile: b = 2(E√2 + dp/2 + d), d ≤ 330 mm in b only | b = 1,941 mm, φVc = 0.75 × 0.17√f'c b d = 1,075 kN; Vu = 569 kN | b = 76.7 in = 1,948 mm, 271 k (239 k = 1,063 kN at φ 0.75) |

For P2, CRSI draws the perimeter around both piles as if the cap went on past the edge. The program counts only the part of the perimeter inside the cap, which is lower and on the safe side. Both pass by a wide margin.

**12. Column bearing** (NSCP 422.8.3; CRSI does not check it)

- A1 = 558.8² = 312,257 mm². The frustum spreads by min(2h, distance to the cap edge) = min(2,438, 1,473) = 1,473 mm, so A2 = 3,505² = 12.29 × 10⁶ mm²; √(A2/A1) = 6.3, so 2 is used.
- φBn = 0.65 × 2 × 0.85 × 20.68 × 312,257 = **7,137 kN** (1,604 k), less than Pu = 8,545 kN.
- The excess, 1,408 kN, needs 1,408 × 10³ / (0.65 × 413.7) = **5,237 mm²** of column bars or dowels into the cap (NSCP 416.3.1.2); the minimum is 0.005 × 312,257 = 1,561 mm². Eight #11 (35.8 mm) column bars carried into the cap, 8,050 mm², would do it.

**Result.** The demands match CRSI exactly: reactions, Mu, and Vu for LS 1, 2, 4 and 5. The capacities match once CRSI's φ = 0.85 is changed to 0.75 and its d = 38 in to the d of each layer. The cap passes, as in CRSI, and the program adds the bearing note of step 12.

### Example 2: 5 piles (CRSI Design Example #2)

5 piles of 10 in (254 mm), four at the corners of a 36 in (914 mm) diagonal pattern and one at the centre. Pile service load 200 k (889.6 kN). Cap 7'-9" (2362 mm) square, 43 in (1092 mm) thick; column 20 in (508 mm) square; #8 bars; the rest as in Example 1.

| Step | Program (NSCP 2015) | CRSI | Why they differ |
|---|---|---|---|
| Pile reaction, factored | 1,423 kN (320 k) | 320 k | – |
| Mu at c/4 | 1,642 kN·m (1,211 k-ft) | 1,211 k-ft | – |
| As along Y, d = 825.5 mm (32.5 in) | 6,599 mm² (10.23 in²), 1.4/fy b d | 10.22 in² | 1.4/fy against 200/fy |
| LS 1, two-way at d/2 | Vu = 3,805 kN (855 k); φVc = 5,081 kN (1,142 k) | Vu = 1,245 k; φVc = 1,303 k | the corner piles' centres are 50 mm outside the section, within dp/2, so NSCP 413.4.2.5 counts each at 70 %. CRSI counts them in full |
| LS 4, two-way at the column face | not checked: w = 469 mm > d/2 = 419 mm | Vu 1,231 k > φVc 1,160 k, accepted as 6 % over | CRSI's own rule (5.4) applies LS 4 only when w ≤ d/2. The example checks it anyway. LS 1 covers this case |
| LS 5, one-way at the column face | Vu = 2,757 kN (620 k); φVc = 4,571 kN (1,028 k) | Vu = 620 k; φVc = 1,111 k (980 k at φ 0.75) | φ, and d = 33.5 in for the X layer |
| P3, corner pile | b0 = 1,927 mm (75.9 in); φVc = 1,818 kN (409 k) | b0 = 75.8 in; 466 k (411 k at φ 0.75) | φ |
| P2, corner and centre pile | b0 = 4,811 mm (189 in); φVc = 4,539 kN | b0 = 207 in; 1,272 k | the part of the perimeter outside the cap is not counted |
| P4, one-way across the corner pile | b = 2,426 mm (95.5 in); φVc = 1,179 kN (265 k) < Vu = 1,423 kN: **fails** | b = 95.4 in; 293 k < 320 k: N.G. | both fail. CRSI accepts the 9 % overstress or makes the cap 46 in |
| Column bearing | φBn = 5,898 kN < Pu = 6,887 kN; 3,678 mm² of column bars or dowels | not checked | – |

**Result.** The program fails this cap on P4, as CRSI's own numbers do. CRSI then accepts the overstress on judgment; the program reports the failure and leaves that decision to the engineer. With "find the least thickness" on, P4 passes at 1,300 mm (51 in; CRSI's 46 in is at φ = 0.85). But the piles of this example already carry their full 200 k, so the heavier cap overloads them, and the search stops on the pile capacity: the column load has to come down, or the cap needs more piles.

## The report

The PDF report (format of [the calculation reports](calculation_reports.md)) holds: the result and the bars; the design basis; a plan of the cap with the piles numbered and the critical sections; the reactions of every pile in every combination; each group of checks with its equation, its numbers, its clause and its result; the thicknesses tried; notes; the references.

## Limits

- One column, at the centre of the cap. A cap under two columns or a wall is not designed.
- The cap is rectangular; clipped corners are not drawn or counted.
- Piles are vertical; batter piles (NSCP 418.13.4.6) are not designed.
- The piles themselves (their reinforcement, buckling, lateral capacity from the soil) are not designed; their capacities are inputs.
- The strut-and-tie model is the usual simplified one: one strut per pile, nodes at the column quarter points and at the pile tops.

## Tests

`tests/test_pile_cap.py`: CRSI Design Examples #1 and #2 (the worked examples above: reactions, moments, shears, perimeters, the development length available, the minimum steel); the NSCP strengths against hand calculations (two-way shear, development, shrinkage and minimum steel, flexure, column bearing), the reactions of a rigid cap and their equilibrium, the loads from text, CSV and Excel, the least-thickness search, the deep-cap methods, the strut-and-tie tie force by hand, fixed pile heads, the four dialogs of an answers-file run, and the window (the form, the plan redrawn, the results, a run after a change).

Run in full from an answers file (5 piles, a loads CSV with six combinations, PDF written): two-way and one-way shear checked by hand against the printout.
