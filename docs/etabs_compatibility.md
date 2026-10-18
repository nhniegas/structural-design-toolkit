# Deformation compatibility (`sdt compatibility`)

`sdt compatibility` checks the members that are not part of the seismic-force-resisting system: NSCP 2015 208.7.2.3 and 418.14 (ACI 18.14). Such a member does not resist the earthquake, but it moves with the building, and it must keep carrying its gravity load at the displacement the building reaches.

It runs on the model open in ETABS, after `sdt beams` and `sdt columns` (or `sdt design`): it compares the forces it works out with the strength of each member as designed.

## Which members

- **The members of the ETABS group `GRAVITY ONLY`.** You designate them in ETABS (select them, Assign > Assign Objects to Group). The group is kept in the model.
- **Every gravity beam,** that is, every beam the design found to rest on other beams only. Such a beam is never part of a frame, so it is always checked.

When the model has no such group, the command proposes members for it:

| Proposed | Why |
|---|---|
| Girders with a clear span under 4d | They do not qualify as beams of a special moment frame (NSCP 418.6.2.1(a)) |
| Cantilevers | They take no part in the frame action |
| Planted columns | No column below and no support: they sit on a beam |
| Columns with no girder along X or Y at their top | No frame in that direction |
| Columns under half the average share of their story's shear | Under the drift load cases; the share of each is given |

It then offers to create the group with them, so you can review it in ETABS and run the command again, or to check the gravity beams alone.

## How the check works

**1. The design displacement δu.** The building is analysed once more with the designated members and the gravity beams pinned at both ends (their M2 and M3 releases set), so that the frames and walls carry the earthquake alone. NSCP 208.7.2.3 asks for this: "the stiffening effect of those elements not part of the lateral-force-resisting system shall be neglected". The story drift of each drift load case (`EQXSD`, `EQYSD`) is read at the outer corners and amplified to ΔM = 0.7 R ΔS (208.6.4.2), and taken as at least 0.0025 of the story height. The releases are put back and the model is analysed again, so it is as it was.

**2. The forces at δu, in the model as built.** The members keep their real end fixity: a gravity column that is fixed at its ends does take moment when the building sways, and that is what 418.14 checks. The forces of the drift load case in the model as built are scaled by δu over the drift that case gives the model as built, story by story and in each direction. The analysis is linear, so this is the model pushed to δu without a displacement load case.

For example: the third floor drifts 10 mm under `EQXSD` in the model as built, and column C5 takes 20 kN-m. With the frames alone the drift is larger, and δu (0.7 R times it) is 60 mm. C5 then takes 20 × 60 / 10 = 120 kN-m at δu.

**3. With the gravity load (NSCP 418.14.2.1).** The forces at δu are added to 1.2D + f L and to 0.9D, station by station, with the worst sign. f is asked: 0.5, allowed except for garages, places of public assembly and live load over 4.8 kPa, where it is 1.0.

**4. The clause and its detailing.**

| The forces at δu | Clause | Checked |
|---|---|---|
| Within φMn and φVn of the member as designed | NSCP 418.14.3.2 | Beams: stirrups over the whole length at most d/2 (a). Columns: hoops over the full height at so = min(6 db, 150 mm) (b); with Pu over 0.35 Po, half the confinement of 418.7.5.4 (c) |
| Over φMn or φVn | NSCP 418.14.3.3 | The special moment frame rules: met when the member was designed with them (SMRF on, and not one of the girders under 4d designed without them) |

The strengths are those of the design: for a beam, φMn of its tension bars at the supports (singly reinforced) and φVn = φ(Vc + Vs) with the stirrups of the support zone; for a column, the smallest φMn and φVn of its design.

## What it writes

A workbook `Results/<model> - Compatibility.xlsx` (one row per member: δu, the factor, Mu, φMn, Vu, φVn, Pu, the clause, the detailing and the result) and a text report `Summaries/<model> - Compatibility.txt` with δu of every story, both beside the model, and the closing summary.

## Tested

`tests/test_compatibility.py` checks δu (0.7 R ΔS, at least 0.0025 h), the beam strengths against hand calculations, the clause and detailing of beams and columns, the induced forces with the gravity load, and the candidates. In ETABS, on the designed test model: with only the gravity beams pinned, δu over the drift as built is 0.7 R = 5.95 on every story, as it must be (secondary beams add no lateral stiffness). With two ground floor columns also pinned, the factor at that story rose to 6.2 while the other stories stayed at 5.95. The 37 members checked were within their strength (418.14.3.2).

## Not covered

- Slab-column connections of flat slabs (418.14.5) and wall piers (418.14.6).
- Precast members (418.14.4).
- Drift on the response spectrum drift cases: the static drift patterns are used.
