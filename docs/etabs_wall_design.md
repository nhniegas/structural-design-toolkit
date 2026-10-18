# Wall design (`sdt walls`)

`sdt walls` designs the structural walls of the model open in ETABS: planar walls, L, T and U walls and cores, to NSCP 2015 Section 418.10 (special structural walls) or Section 411 (walls).

## Legs and walls

A wall is read as **legs**: each straight run of wall panels in one plane is a leg, with its own pier label (`W1A`, `W1B`, ...). Legs whose ends meet in plan are one **wall** (`W1`), and are designed together as one section: in an L wall the flange works with the web (NSCP 418.10.5.1). The labels are the same on every story where the leg continues, so the forces of each leg can be read story by story.

`sdt tag` gives the labels, with the beam and column names. When the walls are not labelled as the design reads them, `sdt walls` offers to label them itself.

Walls are numbered in plan order, as the columns are (top to bottom, then left to right); the legs of a wall are lettered by direction.

## What is asked

| Question | |
|---|---|
| Special or ordinary, wall by wall | Special: 418.10, for walls of the seismic-force-resisting system in zone 4. Ordinary: Section 411 |
| f'c, fy, the vertical and horizontal bar, cover, and the module the boundary element length is rounded up to | 28 MPa, 414 MPa, 16 mm, 12 mm, 40 mm, 50 mm |
| Which method decides special boundary elements when both apply | The displacement method (418.10.6.2) or the stress method (418.10.6.3); both results are given either way |
| Strength combinations | The `ULS` combinations, or those you pick when the model has none |

## How each story of a wall is designed

**Forces.** The pier forces of every leg at the base of the story are summed, by statics, to the centroid of the wall's section: the axial load, the moments about X and Y, and the shear of each leg in its own plane. ETABS gives each leg's forces in its pier axes; the sign convention of the sum was checked in ETABS against a pier of the whole L wall, on three load cases and three stories: the moments agree to 2 × 10⁻⁵.

**Shear, leg by leg.**

| | Special wall (418.10.4) | Ordinary wall (411.5.4) |
|---|---|---|
| Strength | Vn = Acv (αc λ √f'c + ρt fy); αc 0.25 for hw/lw ≤ 1.5, 0.17 from 2.0, linear between | Vc = 0.17 λ √f'c h d, d = 0.8 lw; Vs = Av fy d / s |
| Upper limit | Vn of a leg at most 0.83 Acw √f'c (418.10.4.4) | Vn at most 0.83 √f'c h d (411.5.4.3) |
| φ | 0.75, or 0.60 where Vn is under the shear at the nominal moment strength (421.2.4.1): the steel is taken for whichever needs less | 0.75 |

**Minimum steel and curtains.** Special walls: ρl and ρt at least 0.0025 (418.10.2.1), or the values of 411.6 when Vu ≤ 0.083 Acv λ √f'c; ρl at least ρt when hw/lw ≤ 2 (418.10.4.3); two curtains when Vu > 0.17 Acv λ √f'c or hw/lw ≥ 2 (418.10.2.2); bars at most 450 mm apart. Ordinary walls: Table 411.6.1 when Vu ≤ 0.5 φVc, else 411.6.2; spacing at most 3h and 450 mm (and lw/3 where shear steel is needed); two layers in walls over 250 mm thick (411.7.2.3).

**Axial load and bending.** The section is the union of the legs, with the vertical bars of every leg in its curtains. Its strength is found by strain compatibility (NSCP 422.2: ε = 0.003, the rectangular block, φ from the net tensile strain, 421.2.2; φPn,max 0.80 φPo, 422.4.2.1), on concrete fibres 25 mm along each leg and 1/20 of its thickness across, about each principal axis of the section, checked apart. The vertical bars start at the spacing of the minimum steel and come closer, 25 mm at a time, until every combination is inside the strength about both axes.

**Special boundary elements (special walls).** For each principal axis and each end:

- **Displacement method (418.10.6.2),** for walls with hw/lw ≥ 2: needed when the neutral axis depth c at the nominal strength under the factored axial load is at least lw / (600 δu/hw), δu/hw not less than 0.005. δu is 0.7 R times the largest displacement of the top of the wall under the drift load cases (`EQXSD`, `EQYSD`).
- **Stress method (418.10.6.3):** needed where P/A + M c / I on the gross section exceeds 0.2 f'c.
- The length is the larger of c − 0.1 lw and c/2 (418.10.6.4(a)), rounded up to your module.

## Coupling beams and the degree of coupling

A beam that joins two separate walls (not two legs of one wall) is a coupling beam. Its clear span ln is the part of the beam outside both walls. Each is designed by NSCP 418.10.7:

| | Rule |
|---|---|
| ln/h ≥ 4 | A beam of a special moment frame, the wall boundary as its column (418.10.7.1): design it with `sdt beams`, SMRF on |
| ln/h < 2 and Vu > 0.33 λ √f'c Acw | Two groups of diagonal bars (418.10.7.2): Avd = Vu / (φ 2 fy sin α), φ = 0.85 (421.2.4.3), at least 4 bars a group (418.10.7.4(b)); α from the diagonals' centroids 100 mm from the faces |
| Otherwise | Diagonal bars or the beam rules of 418.6.3 to 418.6.5 (418.10.7.3) |
| Any | Vn at most 0.83 √f'c Acw (Eq. 418.10.7.4) |

For every pair of walls joined by coupling beams, the **degree of coupling** under each drift load case is the share of the overturning moment carried by the axial couple of the walls: T L / (T L + M1 + M2), with T the sum of the coupling beam shears over the height, L the distance between the walls' centroids, and M1, M2 the walls' own base moments about the axis across the line between them. Under the limit you give (20 % offered), the walls act as separate cantilevers; the summary and the workbook say which.

## What it writes

Beside the model:

- `Results/<model> - Wall Design.xlsx`: the sheet `Walls`, one row per leg and story with the vertical bars and ρl, the P-M ratio and the combination and axis that govern, the horizontal bars, ρt and φ for shear, the boundary elements of the story, and the result; the sheets `Coupling beams` and `Degree of coupling`.
- `Calculations/<model> - Wall Calculations.pdf`, in the format of the column report: for every wall and story, the shear of each leg, the governing combination about each principal axis, the minimum steel, both boundary element checks with their numbers and clauses, and the P-M curves about the principal axes with every demand.
- `Schedules/<model> - Wall Schedule.dxf`: one section detail per wall and story, the walls whose legs meet (an L, a U, a core) drawn as one section, at the scale you give (1 : 50 offered), with the vertical bars, the special boundary element zones, and rows for the thickness, the vertical and horizontal bars and the boundary elements.

A summary lists what fails, where boundary elements are needed and the degree of coupling of each pair.

Boundary elements are checked for bending in the plane of the wall: an axis about which the whole section is no deeper than twice its thickness (bending out of the plane of every leg) is left out.

## Tested

`tests/test_walls.py`: the axial strength against the code cap, in-plane bending against Cárdenas (1973) within 3 %, the weak axis, an L section, αc, the shear steel by hand (both φ), the minimum steel, both boundary element checks by hand, a story designed until it passes, and the statics of the leg forces. `tests/test_wall_piers.py`: legs, an L and a planar wall labelled on every story, and a core.

In ETABS, on the test model with an L wall (6 m and 5 m legs, 250 mm) and a 5 m planar wall over four stories: the labels were given and kept from story to story, the leg forces summed to the L agree with ETABS's own pier of the whole L, and every story was designed (minimum steel governs on this four-story model; P-M ratios 0.12 to 0.55; no boundary element needed).

## Not covered yet

- Resizing the wall thickness in `sdt design` (your decision: each leg its own thickness).
- Openings in a leg, and walls that change in plan from story to story beyond the overlap of their legs.
