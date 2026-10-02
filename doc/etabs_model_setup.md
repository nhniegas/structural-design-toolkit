# ETABS Model Setup

`python main.py` defines the standard parameters of an ETABS concrete model. The frame geometry is still built by hand; this step covers the definitions under the ETABS *Define* menu.

Code: `etabs_api/model_setup.py`, `etabs_api/ubc97.py`, `etabs_api/load_combinations.py`.

## Running it

1. Choose the model: the one open in ETABS, or a new blank model (you are asked where to save it).
2. For an open model, choose whether the definitions go into it or into a copy saved beside it as `<name> - SETUP.EDB`.
3. Answer the input dialogs. They start from the inputs you used last time.
4. The inputs are saved beside the model as `<name>.setup.json`. Running the setup again on that model offers to use them without asking again.

Anything with the same name as a definition the setup makes is overwritten. Everything else in the model is left alone.

## What is defined

| Item | Rule |
|---|---|
| Concrete | `C05` for 5 ksi: f'c = ksi / 0.145 MPa, E = 4700 sqrt(f'c), 23.56 kN/m3 |
| Rebar | `G60` for grade 60: fy = 413.69 MPa, fu = 1.5 fy |
| Beam sections | `G_`, `B_`, `FTB_` + `<width>X<depth>_<concrete>_<rebar>` from the size ranges. Depth is at least the width, and width / depth at least 0.3. Cover 60 mm, I22 and I33 x 0.35 |
| Rectangular columns | `CR_<width>X<depth>_...`, shorter side at least half the longer one, both orientations. Cover 40 mm, I22 and I33 x 0.70, to be designed |
| Circular columns | `C_<diameter>_...`, same modifiers |
| Load patterns | The office standard set, plus extra super dead, live and reducible live patterns you type. Each gets its linear static load case. Only `SELFWEIGHT` carries self weight |
| Seismic patterns | `EQXPE`, `EQXNE`, `EQXSD`, `EQYPE`, `EQYNE`, `EQYSD` as UBC 97, program-calculated period |
| Wind patterns | `WX`, `WY` as ASCE 7-10 on the diaphragms |
| Response spectrum | Function `RSUBC97`, 5 % damping |
| Load cases | `Modal` (eigen, 3 modes per story, at least 12), `RSAX` (U1) and `RSAY` (U2): CQC, SRSS, 5 % eccentricity, scale factor g I / R |
| Load combinations | See below |

A new blank model also has the default `Dead` and `Live` patterns of ETABS removed.

## Seismic coefficients

One set of seismic inputs is typed per project: zone factor, soil profile type, source type, distance, I, R and Ct. Ca and Cv are worked out from them with the UBC 97 tables (`ubc97.py`) and used for the six seismic patterns, the response spectrum function and the vertical effect, so they cannot disagree.

How the seismic patterns hold these inputs depends on the model:

- **New blank model:** "Per Code" in ETABS, with the soil type, zone factor, source type and distance. ETABS only accepts the source distance through its model text file, so the new model is saved, its text file is edited and reopened, and the result is saved again.
- **Model that was already open:** "User Defined", with the Ca and Cv worked out here. The values are the same; the model is not reloaded from text.

The response spectrum scale factor is g I / R. Scaling to the static base shear is not done yet.

## Load combinations (NSCP 2015)

Names are `<set> <number> <expression>`, for example `ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3`.

| Set | Use |
|---|---|
| `ULS` | Strength. These are the concrete design combinations |
| `SLS` | Service checks |
| `SSLC` | Special seismic combinations with Em = 2.8 Eh |
| `EQ_COMBO_01` to `08`, `RSA_COMBO_01` to `08` | The eight directional combinations: 100 % in one direction with 30 % in the other |
| `ENVE_...` | Envelopes |

- DL is every dead and super dead pattern, LL every live and reducible live pattern, Lr the roof live pattern.
- f on live load is 0.5. The redundancy factor is 1.0.
- Each seismic combination exists for the static cases (`EQ1` to `EQ8`) and for the response spectrum cases (`RSA1` to `RSA8`), with the same number.
- The vertical effect Ev = 0.5 Ca I D is in the dead load factor: `(1.2 + Ev)` where gravity adds to the earthquake and `(0.9 - Ev)` where it resists it. In the service combinations it is scaled with the earthquake (E / 1.4).

## Not covered yet

Mass source, P-delta options, scaling of the response spectrum cases, and the concrete design preferences.
