# ETABS Model Setup

`sdt setup` (or `python main.py setup`) defines the standard parameters of an ETABS concrete model. The frame geometry is still built by hand; this step covers the definitions under the ETABS *Define* menu.

Code: `etabs_api/workflows/model_setup.py`, `etabs_api/workflows/ubc97.py`, `etabs_api/workflows/load_combinations.py`.

## How to use it

### Before you start

- The project is installed as in the README (`.venv` with the requirements).
- To set up a model you already have, open it in ETABS and save it. To start a new model, ETABS can be open or closed; it is started if needed.

### 1. Start the script

Open PowerShell (or the VS Code terminal), go to the project folder, and run:

```powershell
cd "C:\path\to\xlwings_spreadsheet_structural"
.venv\Scripts\Activate.ps1
sdt setup
```

The second line switches to the project's Python environment. It is needed once per terminal session; `(.venv)` at the start of the prompt shows it is active.

If PowerShell refuses to run `Activate.ps1` because scripts are disabled, skip that line and call the environment's Python directly:

```powershell
.venv\Scripts\python.exe main.py setup
```

(or `.venv\Scripts\sdt.exe setup`).

`python main.py setup` and `python etabs_api/workflows/model_setup.py` do the same. `sdt --help` lists the other commands.

Everything after this happens in dialogs. Closing any dialog cancels the run and changes nothing.

### 2. Choose the model

| Choice | What happens |
|---|---|
| The model that is open in ETABS | A second dialog asks whether the definitions go **into this model** or **into a copy** saved beside it as `<name> - SETUP.EDB` |
| A new blank model | You are asked where to save it. A blank model in N-mm is created there |

### 3. Type the inputs

Seven dialogs follow (eight with membrane slabs). Each box already holds the value you used last time (or the default on the first run). Lists are separated by commas. Press Enter or **OK / Confirm** to go on.

| Dialog | Boxes | Example |
|---|---|---|
| Materials | Concrete strengths (ksi); rebar grades (ksi) | `4, 5, 6` and `60` give C04, C05, C06 and G60 |
| Frame Sections | Concrete and rebar of the sections; then for each kind of section a range as `min, max, step` in mm | Girders G: width `300, 600, 100`, depth `500, 1000, 100`. Leave a range blank to skip that kind |
| Slab and Wall Sections | Slab thicknesses (mm); slab type (Membrane, Shell-Thin or Shell-Thick); concrete of the slabs (ksi); wall thicknesses (mm); concrete of the walls (ksi). When the type is Membrane, a second dialog asks whether to add a one-way counterpart of each slab | `100, 125, 150, 200`, `Membrane`, `4`, `150, 200, 250, 300`, `5`. Leave a list blank to skip it |
| Seismic (UBC 97) | Zone factor Z, soil profile type, source type, distance (km), I, R, Ct, eccentricity ratio | `0.4`, `SD`, `A`, `8`, `1`, `8.5`, `0.03`, `0.05` |
| Wind (ASCE 7-10) | Wind speed (as typed in ETABS, mph), exposure type, Kzt, gust factor, Kd | `150`, `B`, `1`, `0.85`, `0.85` |
| Load Patterns | Extra super dead, live and reducible live patterns | `ELEVATOR DEAD, CONCRETE PAD` |
| Mass Source | A choice: include 20 % of the reducible live load in the seismic mass, or not | |

Notes on the section ranges:

- Girders `G`, beams `B` and footing tie beams `FTB` each take a width range and a depth range. Every width is combined with every depth that keeps the depth at least the width and the width at least 0.3 of the depth.
- Rectangular columns `CR` take one range for the side. Every pair of sides is made whose shorter side is at least half the longer one, in both orientations (`CR_800X600` and `CR_600X800`).
- Circular columns `C` take a diameter range.
- Each section is made once per concrete strength listed in "Concrete of the sections", so a long list multiplies the number of sections.
- Slabs and walls each have their own concrete ("Concrete of the slabs", "Concrete of the walls"), the first frame section concrete by default. Both are always defined as materials, even when they are not in the Materials list.

### 4. Wait for the closing message

A loading window shows the progress. A blank model takes about one to two minutes. The closing summary, shown in a window and printed in the terminal, lists how many of each item were defined, the Ca, Cv and Ev used, the response spectrum scale factor, where the model was saved, and anything that failed.

### 5. Continue in ETABS

ETABS has the set-up model open. Build or continue the frame, assign the sections and loads, then run the analysis. Still to do by hand: scaling the response spectrum cases to the static base shear, and the concrete design preferences.

### From your own script

`setup_model` runs the same setup without dialogs:

```python
from etabs_api.workflows.model_setup import setup_model

result = setup_model(
    {"seismic": {"soil_type": "SE", "distance_km": 8.9}},   # only what differs from the defaults
    target="open",        # or "new" with path=r"C:\...\MODEL.EDB"
    copy_model=True,      # work on "<name> - SETUP.EDB"
)
print(result.report())    # what was defined, as Markdown
```

`apply=False` only describes what would be defined and does not touch ETABS.

### Running it again

The inputs are saved beside the model as `<name>.setup.json`. When you run the script on that model again, it asks whether to **use the saved inputs** or **review and change them**. Run it again after adding stories, so the number of modes and the top story of the lateral loads are updated.

At the end the model is saved and opened again from its `.EDB`, and the wind patterns are only rewritten when their values changed. Writing the wind pattern table makes ETABS drop the wind patterns it generated in an earlier analysis (`WX(1/12)` and so on); if it then analyses in the same session, its save fails with "Error cleaning Wind Loads Arrays" / "Index was outside the bounds of the array" and the `.EDB` is deleted. Opening the saved file clears that. `sdt grids` saves and reopens the same way.

Anything with the same name as a definition the setup makes is overwritten. Everything else in the model is left alone. Deleting the `.setup.json` file makes the script ask everything from the last used values.

## What is defined

| Item | Rule |
|---|---|
| Concrete | `C05` for 5 ksi: f'c = ksi / 0.145 MPa, E = 4700 sqrt(f'c), 23.56 kN/m3 |
| Rebar | `G60` for grade 60: fy = 413.69 MPa, fu = 1.5 fy |
| Beam sections | `G_`, `B_`, `FTB_` + `<width>X<depth>_<concrete>_<rebar>` from the size ranges. Depth is at least the width, and width / depth at least 0.3. Cover 60 mm. No stiffness modifiers (1.0): assign the cracked-section modifiers to the frames in ETABS; `sdt check` and `sdt drift` check them |
| Rectangular columns | `CR_<width>X<depth>_...`, shorter side at least half the longer one, both orientations. Cover 40 mm, to be designed. No stiffness modifiers (1.0) |
| Circular columns | `C_<diameter>_...`, no stiffness modifiers |
| Slabs | `S_<thickness>_<concrete>`, for example `S_150_C04`, of the type chosen (Membrane by default). Stiffness modifiers 1.0 |
| One-way slabs | Only for membrane slabs, when chosen: `S_<thickness>_<concrete>_1W`, for example `S_150_C04_1W`, the same slab with one-way load distribution. The two-way slab is always made too |
| Walls | `SW_<thickness>_<concrete>_<rebar>`, thin shell, the name `sdt grids` gives its walls. Stiffness modifiers 1.0 |
| Load patterns | The office standard set, plus extra super dead, live and reducible live patterns you type. Each gets its linear static load case. Only `SELFWEIGHT` carries self weight |
| Seismic patterns | `EQXPE`, `EQXNE`, `EQXSD`, `EQYPE`, `EQYNE`, `EQYSD` as UBC 97, program-calculated period |
| Wind patterns | `WX`, `WY` as ASCE 7-10 on the diaphragms |
| Response spectrum | Function `RSUBC97`, 5 % damping |
| Load cases | `Modal` (eigen, 3 modes per story, at least 12), `RSAX` (U1) and `RSAY` (U2): CQC, SRSS, 5 % eccentricity, scale factor g I / R. Also `RSAXD` and `RSAYD`, the same cases for the drift check: `sdt analyze` scales them to the drift patterns `EQXSD` / `EQYSD` (period not capped, NSCP 208.6.5.2). They are in no combination: NSCP checks drift with E alone, ΔM = 0.7 R ΔS |
| Mass source | From the load patterns only: every dead and super dead pattern at 1.0, `LIVENRED` at 1.0, and reducible live patterns at 0.20 when chosen. Element self mass and added mass are off, since `SELFWEIGHT` already carries the weight |
| P-delta | Iterative, based on loads, tolerance 0.0001: every dead and super dead pattern at 1.0, `LIVENRED` at 1.0, and reducible live patterns at 0.50 (always) |
| P-delta in the load cases | Every linear static case uses **Use Preset P-Delta Settings**. The cases ETABS makes with the load patterns start as "Use Nonlinear Case" (None) and are switched |
| Load combinations | See below |

A new blank model also has the default `Dead` and `Live` patterns of ETABS removed.

## Seismic coefficients

One set of seismic inputs is typed per project: zone factor, soil profile type, source type, distance, I, R and Ct. Ca and Cv are worked out from them with the UBC 97 tables (`ubc97.py`) and used for the six seismic patterns, the response spectrum function and the vertical effect, so they cannot disagree.

How the seismic patterns hold these inputs depends on the model:

- **New blank model:** "Per Code" in ETABS, with the soil type, zone factor, source type and distance. ETABS only accepts the source distance through its model text file, so the new model is saved, its text file is edited and reopened, and the result is saved again. The saved model is then opened from its `.EDB`: ETABS does not analyse a model it still holds from a text file (the analysis stops at once, with no log).
- **After `sdt grids`:** "User Defined" again. ETABS puts the source distance of a "Per Code" pattern back to 15 km whenever the seismic table is written, and `sdt grids` writes it to set the bottom and top story. So it writes the Ca and Cv of the pattern's own inputs instead of leaving "Per Code" with the wrong distance.
- **Model that was already open:** "User Defined", with the Ca and Cv worked out here. The values are the same; the model is not reloaded from text.

The response spectrum scale factor is g I / R. Scaling to the static base shear is not done yet.

## Load combinations (NSCP 2015)

Names are `<set> <number> <expression>`, for example `ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3`.

| Set | Use |
|---|---|
| `ULS` | Strength. These are the concrete design combinations |
| `SLS` | Service checks |
| `SSLC` | Special seismic combinations with Em = 2.8 Eh |
| `DRIFT` | Seismic drift, NSCP 208.6.4.1, using the 203.3 combinations 203-5 and 203-7, `(1.2 + Ev) DL + f LL ± 1.0 E` and `(0.9 - Ev) DL ± 1.0 E`, with ρ = 1.0 (208.6.1). E is a drift case: `EQXSD` / `EQYSD` (period not capped), or `RSAXD` / `RSAYD` (no sign: the spectrum is an envelope). The scaling by `sdt analyze` changes the cases, so the combinations follow it with no change. Envelope `ENVE_DRIFT` |
| `WDRIFT` | Wind drift, 203-3, 203-4 and 203-6 on `WX` and `WY`. NSCP sets no limit; `sdt check` asks for one (h/400 by default). Envelope `ENVE_WDRIFT` |
| `DEF` | Deflection checks, unfactored: `DEF 100 1.0 DL`, `DEF 101 1.0 DL + 1.0 LL`, `DEF 102 1.0 DL + 0.25 LL` (sustained) and `DEF 103 1.0 DL + 1.0 Lr` |
| `EQ_COMBO_01` to `08`, `RSA_COMBO_01` to `08` | The eight directional combinations: 100 % in one direction with 30 % in the other |
| `ENVE_...` | Envelopes |

- DL is every dead and super dead pattern, LL every live and reducible live pattern, Lr the roof live pattern.
- f on live load is 0.5. The redundancy factor is 1.0.
- Each seismic combination exists for the static cases (`EQ1` to `EQ8`) and for the response spectrum cases (`RSA1` to `RSA8`), with the same number.
- The vertical effect Ev = 0.5 Ca I D is in the dead load factor: `(1.2 + Ev)` where gravity adds to the earthquake and `(0.9 - Ev)` where it resists it. In the service combinations it is scaled with the earthquake (E / 1.4), for example `(1.0 + 0.714 Ev)` and `(0.6 - 0.714 Ev)`.
- Wind is strength-level (ASCE 7-10): 1.0 W in the strength combinations and 0.6 W in the service ones.
- Only the `ULS` combinations are flagged for concrete design in ETABS.

## Not covered yet

- Scaling of the response spectrum cases to the static base shear.
- Concrete design preferences in ETABS (for example the overstrength factor there).
- "Per Code" seismic patterns in a model that was already open; see Seismic coefficients.
- Story and grid definition of a new model: it is created blank.

After a new model is set up, check the seismic patterns once the analysis has run: until then ETABS shows placeholder Ca and Cv for "Per Code" patterns.

## Tests

`tests/test_model_setup.py` checks the UBC 97 coefficients against the two office models, the load combinations (factors, names, order of creation, design flags) and the section, pattern and settings rules. It runs without ETABS.
