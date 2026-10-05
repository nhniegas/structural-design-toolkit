"""Automatic definition of the standard parameters of an ETABS concrete model.

Defines, in the model that is open in ETABS or in a new blank one:

* materials             concrete ``C05`` (strength in ksi) and rebar ``G60``
* frame sections        ``G_500X800_C06_G60`` and so on, from size ranges
* load patterns         the office standard set, UBC 97 seismic, ASCE 7-10 wind
* response spectrum     the UBC 97 function, with Ca and Cv from the seismic inputs
* load cases            modal and the two response spectrum cases
* mass source           dead loads, non-reducible live and, optionally, reducible live
* P-delta               iterative, on dead loads, non-reducible live and half the reducible live
* load combinations     NSCP 2015, see ``load_combinations.py``

Run this file to use it:

    python etabs_api/workflows/model_setup.py

The inputs are asked for in dialogs and saved beside the model, so they are
not asked again for the same model. ``setup_model`` does the same without
dialogs, from a settings dictionary, for use in your own scripts.

The planning functions work on plain data and need no ETABS.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from dataclasses import dataclass

if __package__ in (None, ""):
    # Run as a script: make the project folder (two levels up) importable.
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import CODE, NSCP
from etabs_api.core.helpers import as_list, return_code
from etabs_api.workflows.load_combinations import DRIFT_SPECTRUM_CASES as lc_drift_cases
from etabs_api.workflows.load_combinations import Combination, build_combinations
from etabs_api.workflows.ubc97 import (
    response_spectrum_scale,
    seismic_coefficients,
    vertical_effect_factor,
)

UNITS_N_MM = 9
KSI_TO_MPA_CONCRETE = 1.0 / 0.145  # the office models use f'c = ksi / 0.145
KSI_TO_MPA = 6.894757
CONCRETE_UNIT_WEIGHT = 2.3563121e-05  # N/mm3 (23.56 kN/m3)
REBAR_UNIT_WEIGHT = 7.6972865e-05  # N/mm3
REBAR_MODULUS = 199947.98  # MPa
SPECTRUM_FUNCTION = "RSUBC97"
MODAL_CASE = "Modal"
MODES_PER_STORY = 3
MINIMUM_MODES = 12  # a blank model has one story; three modes would be too few
# Frame sections carry no stiffness modifiers (all 1.0): the cracked-section
# modifiers are assigned to the frames in ETABS; sdt check and sdt drift judge them.
SECTION_MODIFIERS = [1.0] * 8  # area, shear 2, shear 3, torsion, I22, I33, mass, weight
BEAM_COVER = 60.0  # mm, to the bar centre, top and bottom
COLUMN_COVER = 40.0  # mm
BEAM_MIN_WIDTH_TO_DEPTH = CODE.beam_seismic.min_width_to_depth
COLUMN_MIN_SIDE_RATIO = 0.5  # office rule, stricter than ACI 18.7.2.1(b)
BEAM_PREFIXES = ("G", "B", "FTB")  # girders, beams, footing tie beams
OMEGA0 = NSCP.load_factors.omega0
RHO = NSCP.load_factors.rho
LIVE_FACTOR = NSCP.load_factors.live_companion  # f on live load in the seismic and wind combinations
# Response spectrum cases: (name, direction). The strength cases are scaled to
# the static base shear; the drift cases (suffix DRIFT_SUFFIX) to the drift
# patterns, whose period is not capped (NSCP 208.6.5.2).
SPECTRUM_CASES = (("RSAX", "U1"), ("RSAY", "U2"))
DRIFT_SUFFIX = "D"
DRIFT_SPECTRUM_CASES = tuple((name + DRIFT_SUFFIX, direction) for name, direction in SPECTRUM_CASES)
assert tuple(n for n, _ in DRIFT_SPECTRUM_CASES) == lc_drift_cases  # the drift combinations use them
NON_REDUCIBLE_LIVE = "LIVENRED"  # always part of the seismic mass and the P-delta load
NON_REDUCIBLE_LIVE_FACTOR = 1.0
REDUCIBLE_LIVE_MASS_FACTOR = 0.20  # used when reducible live is included in the mass
REDUCIBLE_LIVE_PDELTA_FACTOR = 0.50  # reducible live is always part of the P-delta load
PDELTA_TOLERANCE = 0.0001

# The office standard load patterns: (name, ETABS type, self weight multiplier).
STANDARD_PATTERNS = (
    ("SELFWEIGHT", "Dead", 1.0),
    ("SIDL", "Super Dead", 0.0),
    ("EXTERIOR WALLS", "Super Dead", 0.0),
    ("LIVERED", "Reducible Live", 0.0),
    ("LIVENRED", "Live", 0.0),
    ("LIVEROOF", "Roof Live", 0.0),
    ("WX", "Wind", 0.0),
    ("WY", "Wind", 0.0),
    ("EQXPE", "Seismic", 0.0),
    ("EQXNE", "Seismic", 0.0),
    ("EQXSD", "Seismic (Drift)", 0.0),
    ("EQYPE", "Seismic", 0.0),
    ("EQYNE", "Seismic", 0.0),
    ("EQYSD", "Seismic (Drift)", 0.0),
)
# Direction flags of each seismic pattern in the UBC 97 table.
SEISMIC_DIRECTIONS = {
    "EQXPE": ("XDirPlusE",), "EQXNE": ("XDirMinusE",), "EQXSD": ("XDirPlusE", "XDirMinusE"),
    "EQYPE": ("YDirPlusE",), "EQYNE": ("YDirMinusE",), "EQYSD": ("YDirPlusE", "YDirMinusE"),
}
WIND_ANGLES = {"WX": 0.0, "WY": 90.0}
_PATTERN_ENUM = {
    "Dead": ("Dead",), "Super Dead": ("SuperDead",), "Live": ("Live",),
    "Reducible Live": ("ReduceLive", "ReducibleLive"), "Roof Live": ("Rooflive", "RoofLive"),
    "Wind": ("Wind",), "Seismic": ("Quake",), "Seismic (Drift)": ("QuakeDrift",),
}

DEFAULT_SETTINGS = {
    "concrete_ksi": [4, 5, 6],
    "rebar_ksi": [60],
    "section_concrete_ksi": [5],
    "section_rebar_ksi": 60,
    # [minimum, maximum, step] in mm; an empty list skips that kind of section
    "sections": {
        "G": {"width": [300, 600, 100], "depth": [500, 1000, 100]},
        "B": {"width": [200, 400, 100], "depth": [400, 800, 100]},
        "FTB": {"width": [], "depth": []},
        "CR": {"size": [400, 1000, 100]},
        "C": {"diameter": []},
    },
    # Slab and wall thicknesses in mm (lists) and their concrete (ksi); a
    # concrete of None is the first frame section concrete. "one_way" adds a
    # one-way counterpart of every membrane slab.
    "slabs": {"thickness": [100, 125, 150, 200], "type": "Membrane", "concrete_ksi": None,
              "one_way": False},
    "walls": {"thickness": [150, 200, 250, 300], "concrete_ksi": None},
    "seismic": {
        "zone_factor": 0.4, "soil_type": "SD", "source_type": "A", "distance_km": 10.0,
        "importance": 1.0, "r_factor": NSCP.seismic.smrf_r, "ct": 0.03,
        "eccentricity": NSCP.seismic.eccentricity,
    },
    "wind": {"speed": 150.0, "exposure": "B", "kzt": 1.0, "gust": 0.85, "kd": 0.85},
    "extra_dead": [],
    "extra_live": [],
    "extra_reducible_live": [],
    # Seismic mass: every dead pattern at 1.0, plus these fractions of live load.
    "mass": {"include_reducible_live": False},
}


# =============================================================================
# PLANNING (no ETABS)
# =============================================================================
def grade_name(prefix: str, ksi: float) -> str:
    """``C05`` for 5 ksi concrete, ``G60`` for grade 60 rebar."""
    return f"{prefix}{int(round(float(ksi))):02d}"


def concrete_properties(ksi: float) -> dict:
    """Strength and modulus (MPa) of a concrete grade; E = 4700 sqrt(f'c)."""
    fc = float(ksi) * KSI_TO_MPA_CONCRETE
    return {"name": grade_name("C", ksi), "fc": fc, "E": 4700.0 * math.sqrt(fc)}


def rebar_properties(ksi: float) -> dict:
    fy = float(ksi) * KSI_TO_MPA
    return {"name": grade_name("G", ksi), "fy": fy, "fu": 1.5 * fy}


def _sizes(span: list) -> list[int]:
    """Every size from ``[minimum, maximum, step]``; empty when the range is blank."""
    if not span:
        return []
    low, high, step = (int(round(float(value))) for value in span)
    if step <= 0 or high < low:
        raise ValueError(f"Size range {span} must be minimum, maximum, step.")
    return list(range(low, high + 1, step))


def section_definitions(settings: dict) -> list[dict]:
    """Frame sections to create: name, kind, width (t2), depth (t3) and material.

    Beams keep ``depth >= width`` and ``width / depth >= 0.3``. Rectangular
    columns keep the shorter side at least half the longer one. Names are
    ``<prefix>_<width>X<depth>_<concrete>_<rebar>``; circular columns are
    ``C_<diameter>_<concrete>_<rebar>``.
    """
    rebar = grade_name("G", settings["section_rebar_ksi"])
    ranges = settings["sections"]
    out = []
    for ksi in settings["section_concrete_ksi"]:
        concrete = grade_name("C", ksi)
        tail = f"{concrete}_{rebar}"
        for prefix in BEAM_PREFIXES:
            spans = ranges.get(prefix, {})
            for width in _sizes(spans.get("width", [])):
                for depth in _sizes(spans.get("depth", [])):
                    if depth >= width and width / depth >= BEAM_MIN_WIDTH_TO_DEPTH:
                        out.append({"name": f"{prefix}_{width}X{depth}_{tail}", "kind": "beam",
                                    "width": width, "depth": depth, "material": concrete,
                                    "rebar": rebar})
        sides = _sizes(ranges.get("CR", {}).get("size", []))
        for width in sides:
            for depth in sides:
                if min(width, depth) / max(width, depth) >= COLUMN_MIN_SIDE_RATIO:
                    out.append({"name": f"CR_{width}X{depth}_{tail}", "kind": "column",
                                "width": width, "depth": depth, "material": concrete,
                                "rebar": rebar})
        for diameter in _sizes(ranges.get("C", {}).get("diameter", [])):
            out.append({"name": f"C_{diameter}_{tail}", "kind": "circle", "width": diameter,
                        "depth": diameter, "material": concrete, "rebar": rebar})
    return out


SLAB_SHELL_TYPES = {"Shell-Thin": 1, "Shell-Thick": 2, "Membrane": 3}  # eShellType
ONE_WAY_SUFFIX = "_1W"  # S_150_C04_1W: the one-way counterpart of S_150_C04
SLAB_TABLE = "Slab Property Definitions"


def area_concrete_ksi(settings: dict, kind: str) -> float:
    """Concrete of the slabs (``kind`` "slabs") or the walls ("walls"), in ksi.

    Falls back to an older single slab-and-wall setting, then to the first
    frame section concrete.
    """
    for value in (settings.get(kind, {}).get("concrete_ksi"), settings.get("area_concrete_ksi")):
        if value not in (None, ""):
            return float(value)
    return float(settings["section_concrete_ksi"][0])


def area_section_definitions(settings: dict) -> list[dict]:
    """Slab and wall sections: ``S_<t>_<concrete>`` and ``SW_<t>_<concrete>_<rebar>``.

    With ``slabs["one_way"]`` every membrane slab also gets a counterpart that
    spans one way (``S_<t>_<concrete>_1W``); only a membrane has that option
    in ETABS. The wall name is the one ``sdt grids`` gives its walls, so they
    share it.
    """
    slab_concrete = grade_name("C", area_concrete_ksi(settings, "slabs"))
    wall_concrete = grade_name("C", area_concrete_ksi(settings, "walls"))
    rebar = grade_name("G", settings["section_rebar_ksi"])
    slab_type = settings.get("slabs", {}).get("type", "Membrane")
    if slab_type not in SLAB_SHELL_TYPES:
        raise ValueError(f"Slab type must be one of {', '.join(SLAB_SHELL_TYPES)}.")
    one_way = bool(settings.get("slabs", {}).get("one_way")) and slab_type == "Membrane"
    out = []
    for t in sorted({float(x) for x in settings.get("slabs", {}).get("thickness", [])}):
        out.append({"name": f"S_{t:g}_{slab_concrete}", "kind": "slab", "thickness": t,
                    "material": slab_concrete, "shell": slab_type})
        if one_way:
            out.append({**out[-1], "name": out[-1]["name"] + ONE_WAY_SUFFIX, "one_way": True})
    for t in sorted({float(x) for x in settings.get("walls", {}).get("thickness", [])}):
        out.append({"name": f"SW_{t:g}_{wall_concrete}_{rebar}", "kind": "wall", "thickness": t,
                    "material": wall_concrete, "shell": "Shell-Thin"})
    return out


def load_patterns(settings: dict) -> list[tuple[str, str, float]]:
    """The standard patterns plus the extra ones of the project."""
    patterns = list(STANDARD_PATTERNS)
    taken = {name for name, _, _ in patterns}
    for key, kind in (("extra_dead", "Super Dead"), ("extra_live", "Live"),
                      ("extra_reducible_live", "Reducible Live")):
        for name in settings.get(key, []):
            name = str(name).strip().upper()
            if name and name not in taken:
                patterns.append((name, kind, 0.0))
                taken.add(name)
    return patterns


def mass_source_loads(settings: dict) -> list[tuple[str, float]]:
    """Load patterns of the seismic mass, with their factors.

    Every dead and super dead pattern counts in full. Non-reducible live load
    (``LIVENRED``) is always included. Reducible live load is included at 20 %
    when that option is chosen.
    """
    mass = settings["mass"]
    loads = []
    for name, kind, _ in load_patterns(settings):
        if kind in ("Dead", "Super Dead"):
            loads.append((name, 1.0))
        elif name == NON_REDUCIBLE_LIVE:
            loads.append((name, NON_REDUCIBLE_LIVE_FACTOR))
        elif kind == "Reducible Live" and mass["include_reducible_live"]:
            loads.append((name, REDUCIBLE_LIVE_MASS_FACTOR))
    return loads


def pdelta_loads(settings: dict) -> list[tuple[str, float]]:
    """Load patterns of the P-delta load, with their factors.

    The dead and non-reducible live loads count as in the mass source.
    Reducible live load is always included, at 50 %.
    """
    loads = []
    for name, kind, _ in load_patterns(settings):
        if kind in ("Dead", "Super Dead"):
            loads.append((name, 1.0))
        elif name == NON_REDUCIBLE_LIVE:
            loads.append((name, NON_REDUCIBLE_LIVE_FACTOR))
        elif kind == "Reducible Live":
            loads.append((name, REDUCIBLE_LIVE_PDELTA_FACTOR))
    return loads


def seismic_values(settings: dict) -> dict:
    """Ca, Cv, the vertical effect factor and the response spectrum scale factor."""
    s = settings["seismic"]
    ca, cv = seismic_coefficients(
        s["zone_factor"], s["soil_type"], s["source_type"], s["distance_km"]
    )
    return {
        "ca": ca, "cv": cv,
        "ev": vertical_effect_factor(ca, s["importance"]),
        "scale": response_spectrum_scale(s["importance"], s["r_factor"]),
    }


def combinations(settings: dict) -> list[Combination]:
    values = seismic_values(settings)
    return build_combinations(
        {name: kind for name, kind, _ in load_patterns(settings)},
        values["ca"], settings["seismic"]["importance"], RHO, OMEGA0, LIVE_FACTOR,
    )


def number_of_modes(story_count: int) -> int:
    return max(MODES_PER_STORY * int(story_count), MINIMUM_MODES)


def merge_settings(saved: dict | None) -> dict:
    """Saved settings laid over the defaults, so new keys always exist."""
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for key, value in (saved or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            for inner, inner_value in value.items():
                if isinstance(inner_value, dict) and isinstance(merged[key].get(inner), dict):
                    merged[key][inner].update(inner_value)
                else:
                    merged[key][inner] = inner_value
        else:
            merged[key] = value
    return merged


def settings_path(model_path: str) -> str:
    return os.path.splitext(model_path)[0] + ".setup.json"


def defaults_path() -> str:
    """Last used settings, offered as the starting point of a new model."""
    from utilities.user_settings import settings_path as user_settings_path

    return user_settings_path("model_setup.json")


def load_settings(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_settings(settings: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)


# =============================================================================
# ETABS
# =============================================================================
class SetupLog:
    """Counts what was defined and keeps the problems for the closing message."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.problems: list[str] = []

    def done(self, what: str, count: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + count

    def check(self, result, what: str) -> bool:
        if return_code(result) == 0:
            return True
        self.problems.append(what)
        return False


def _edit_table(model, key: str, rows: list[dict], log: SetupLog, replace_all: bool = False):
    """Put rows into an ETABS database table, replacing the rows of the same name.

    Rows ETABS generated itself (``IsAuto`` = Yes) are left out; ETABS makes
    them again. Returns True when ETABS accepted the table.
    """
    tables = model.DatabaseTables
    current = tables.GetTableForEditingArray(key, "", 0, [], 0, [])
    fields = [str(name) for name in as_list(current[1])]
    values = as_list(current[3])
    width = len(fields)
    name_field = fields[0]
    new_names = {str(row[name_field]) for row in rows}
    kept = []
    if not replace_all and width:
        auto = fields.index("IsAuto") if "IsAuto" in fields else None
        for start in range(0, len(values), width):
            row = ["" if value is None else str(value) for value in values[start:start + width]]
            if row[0] in new_names or (auto is not None and row[auto] == "Yes"):
                continue
            kept.append(row)
    table = kept + [[str(row.get(name, "")) for name in fields] for row in rows]
    flat = [value for row in table for value in row]
    staged = tables.SetTableForEditingArray(key, 0, fields, len(table), flat)
    if not log.check(staged, f"table '{key}' could not be staged"):
        return False
    applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    fatal, errors = int(applied[0]), int(applied[1])
    if return_code(applied) != 0 or fatal or errors:
        log.problems.append(f"table '{key}': {str(applied[4]).strip()[:300]}")
        return False
    return True


def _pattern_type(kind: str) -> int:
    import comtypes.gen.ETABSv1 as etabs

    for name in _PATTERN_ENUM[kind]:
        value = getattr(etabs, f"eLoadPatternType_{name}", None)
        if value is not None:
            return int(value)
    raise ValueError(f"ETABS has no load pattern type for '{kind}'.")


def concrete_grades(settings: dict) -> list[float]:
    """The concrete materials to define: the listed ones plus the slab and wall grades."""
    grades = [float(k) for k in settings["concrete_ksi"]]
    for kind in ("slabs", "walls"):
        if settings.get(kind, {}).get("thickness"):
            grades.append(area_concrete_ksi(settings, kind))
    return sorted(set(grades))


def define_materials(model, settings: dict, log: SetupLog) -> None:
    api = model.PropMaterial
    for ksi in concrete_grades(settings):
        p = concrete_properties(ksi)
        name = p["name"]
        ok = log.check(api.SetMaterial(name, 2), f"concrete {name}")  # eMatType_Concrete
        ok = ok and log.check(api.SetMPIsotropic(name, p["E"], 0.2, 9.9e-06), f"{name} modulus")
        ok = ok and log.check(api.SetWeightAndMass(name, 1, CONCRETE_UNIT_WEIGHT), f"{name} weight")
        # (name, fc, lightweight, factor, Mander curve, concrete hysteresis, strains, slope)
        ok = ok and log.check(
            api.SetOConcrete_1(name, p["fc"], False, 0.0, 2, 4, 0.0022191422, 0.005, -0.1, 0.0, 0.0),
            f"{name} strength",
        )
        if ok:
            log.done("materials")
    for ksi in settings["rebar_ksi"]:
        p = rebar_properties(ksi)
        name = p["name"]
        ok = log.check(api.SetMaterial(name, 6), f"rebar {name}")  # eMatType_Rebar
        ok = ok and log.check(api.SetMPUniaxial(name, REBAR_MODULUS, 1.17e-05), f"{name} modulus")
        ok = ok and log.check(api.SetWeightAndMass(name, 1, REBAR_UNIT_WEIGHT), f"{name} weight")
        ok = ok and log.check(
            api.SetORebar_1(name, p["fy"], p["fu"], 1.1 * p["fy"], 1.1 * p["fu"], 1, 1,
                            0.01, 0.09, -0.1, False),
            f"{name} strength",
        )
        if ok:
            log.done("materials")


def create_section(api, section: dict, log: SetupLog) -> bool:
    """Create one concrete frame section (modifiers 1.0) with its rebar data.

    ``section`` has name, kind ("beam", "column" or "circle"), width (t2),
    depth (t3), material and rebar, and optionally ``cover`` (mm; the setup
    covers otherwise).
    """
    name, material, rebar = section["name"], section["material"], section["rebar"]
    cover = section.get("cover")
    beam_cover = float(cover) if cover else BEAM_COVER
    column_cover = float(cover) if cover else COLUMN_COVER
    if section["kind"] == "circle":
        created = api.SetCircle(name, material, section["depth"])
    else:  # (name, material, depth t3, width t2)
        created = api.SetRectangle(name, material, section["depth"], section["width"])
    if not log.check(created, f"section {name}"):
        return False
    # 1.0: also clears modifiers an earlier setup put on an existing section
    log.check(api.SetModifiers(name, list(SECTION_MODIFIERS)), f"{name} stiffness modifiers")
    if section["kind"] == "beam":
        log.check(api.SetRebarBeam(name, rebar, rebar, beam_cover, beam_cover, 0, 0, 0, 0),
                  f"{name} reinforcement data")
    else:
        circular = section["kind"] == "circle"
        # pattern 1 rectangular / 2 circular; confinement 1 ties / 2 spiral; to be designed
        log.check(
            api.SetRebarColumn(name, rebar, rebar, 2 if circular else 1,
                               2 if circular else 1, column_cover, 8 if circular else 0,
                               5, 3, "20", "10", 150.0, 3, 3, True),
            f"{name} reinforcement data",
        )
    return True


def define_sections(model, settings: dict, log: SetupLog, progress=None) -> None:
    sections = section_definitions(settings)
    for index, section in enumerate(sections):
        if progress is not None and index % 20 == 0:
            progress(f"Section {index + 1} of {len(sections)}\t{section['name']}")
        if create_section(model.PropFrame, section, log):
            log.done("frame sections")


def remove_blank_model_defaults(model) -> None:
    """Delete the 'Dead' and 'Live' patterns ETABS puts in a new blank model.

    The default 'Dead' pattern carries self weight, which SELFWEIGHT already does.
    """
    names = {str(name) for name in as_list(model.LoadPatterns.GetNameList()[1])}
    for name in ("Dead", "Live"):
        if name in names:
            model.LoadCases.Delete(name)
            model.LoadPatterns.Delete(name)


def define_area_sections(model, settings: dict, log: SetupLog) -> None:
    """Slab and wall sections (stiffness modifiers stay at 1.0)."""
    api = model.PropArea
    for section in area_section_definitions(settings):
        shell = SLAB_SHELL_TYPES[section["shell"]]
        if section["kind"] == "slab":  # (name, slab type: slab, shell type, material, t)
            done = api.SetSlab(section["name"], 0, shell, section["material"],
                               section["thickness"])
        else:  # (name, specified wall, thin shell, material, t)
            done = api.SetWall(section["name"], 1, shell, section["material"],
                               section["thickness"])
        if log.check(done, f"{section['kind']} section {section['name']}"):
            log.done(f"{section['kind']} sections")
    set_one_way_slabs(model, {s["name"]: bool(s.get("one_way"))
                              for s in area_section_definitions(settings)
                              if s["kind"] == "slab" and s["shell"] == "Membrane"}, log)


def set_one_way_slabs(model, one_way: dict[str, bool], log: SetupLog) -> None:
    """Set the load distribution of membrane slabs: one way (True) or two way.

    The API has no call for it, so the slab table is edited. Rows of other
    slabs are written back as they were read.
    """
    if not one_way:
        return
    tables = model.DatabaseTables
    current = tables.GetTableForEditingArray(SLAB_TABLE, "", 0, [], 0, [])
    fields = [str(name) for name in as_list(current[1])]
    if "OneWayLoad" not in fields:
        log.problems.append("one-way slabs: ETABS has no one-way field in the slab table")
        return
    width, flag = len(fields), fields.index("OneWayLoad")
    values = ["" if v is None else str(v) for v in as_list(current[3])]
    rows = [values[i:i + width] for i in range(0, len(values), width)]
    changed = 0
    for row in rows:
        wanted = one_way.get(row[0])
        if wanted is not None and row[flag] != ("Yes" if wanted else "No"):
            row[flag] = "Yes" if wanted else "No"
            changed += 1
    if not changed:
        return
    flat = [value for row in rows for value in row]
    if not log.check(tables.SetTableForEditingArray(SLAB_TABLE, 0, fields, len(rows), flat),
                     "one-way slabs could not be staged"):
        return
    applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    if return_code(applied) != 0 or int(applied[0]) or int(applied[1]):
        log.problems.append(f"one-way slabs: {str(applied[4]).strip()[:300]}")
        return
    count = sum(1 for wanted in one_way.values() if wanted)
    if count:
        log.done("one-way slab sections", count)


def define_load_patterns(model, settings: dict, log: SetupLog) -> None:
    api = model.LoadPatterns
    existing = {str(name) for name in as_list(api.GetNameList()[1])}
    for name, kind, self_weight in load_patterns(settings):
        code = _pattern_type(kind)
        if name in existing:
            ok = log.check(api.SetLoadType(name, code), f"load pattern {name}")
            ok = ok and log.check(api.SetSelfWTMultiplier(name, self_weight), f"{name} self weight")
        else:  # the last argument also creates the linear static load case
            ok = log.check(api.Add(name, code, self_weight, True), f"load pattern {name}")
        if ok:
            log.done("load patterns")


def use_preset_pdelta(model, log: SetupLog) -> None:
    """Every linear static case uses the preset P-delta settings, not a nonlinear case.

    The cases ETABS makes with the load patterns start as "Use Nonlinear Case"
    (None); they are switched to "Use Preset P-Delta Settings".
    """
    key = "Load Case Definitions - Linear Static"
    tables = model.DatabaseTables
    current = tables.GetTableForEditingArray(key, "", 0, [], 0, [])
    fields = [str(name) for name in as_list(current[1])]
    if "StiffType" not in fields:
        return
    width = len(fields)
    values = ["" if v is None else str(v) for v in as_list(current[3])]
    rows = [values[i:i + width] for i in range(0, len(values), width)]
    stiff, nonlinear = fields.index("StiffType"), fields.index("NonlinCase")
    changed = 0
    for row in rows:
        if row[stiff] == "Nonlinear Case":
            row[stiff], row[nonlinear] = "P-Delta", ""
            changed += 1
    if not changed:
        return
    flat = [value for row in rows for value in row]
    if not log.check(tables.SetTableForEditingArray(key, 0, fields, len(rows), flat),
                     "linear static cases could not be staged"):
        return
    applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    if return_code(applied) != 0 or int(applied[0]) or int(applied[1]):
        log.problems.append(f"preset P-delta in the load cases: {str(applied[4]).strip()[:300]}")
        return
    log.done("load cases on preset P-delta", changed)


def define_mass_source(model, settings: dict, log: SetupLog) -> None:
    """Seismic mass from the load patterns only (no element self mass, no added mass)."""
    loads = mass_source_loads(settings)
    names = [name for name, _ in loads]
    factors = [factor for _, factor in loads]
    # (from elements, from added mass, from loads, number of loads, patterns, factors)
    result = model.PropMaterial.SetMassSource_1(False, False, True, len(loads), names, factors)
    if log.check(result, "mass source"):
        log.done("mass source loads", len(loads))


def define_pdelta(model, settings: dict, log: SetupLog) -> None:
    """Iterative P-delta based on loads; the API has no call for it, so the table is used."""
    rows = [
        {"LoadPattern": name, "ScaleFactor": factor,
         "RelConTol": PDELTA_TOLERANCE if index == 0 else "",
         "AutoMethod": "Iterative Based on Loads" if index == 0 else ""}
        for index, (name, factor) in enumerate(pdelta_loads(settings))
    ]
    if _edit_table(model, "P-Delta Option Definition", rows, log, replace_all=True):
        log.done("P-delta loads", len(rows))


SEISMIC_TABLE = "Load Pattern Definitions - Auto Seismic - UBC 97"
WIND_TABLE = "Load Pattern Definitions - Auto Wind - ASCE 7-10"


def lateral_story_range(names: list[str], elevations: list[float]) -> tuple[str, str]:
    """(bottom, top) story of the seismic and wind loads.

    ``names`` and ``elevations`` are as ETABS lists them, the base first. The
    bottom is the ground level: the story at elevation 0 when the base is
    below it (a footing level), otherwise the base.
    """
    bottom = names[0]
    if elevations and elevations[0] < -0.5:
        ground = [n for n, z in zip(names[1:], elevations[1:]) if abs(z) <= 0.5]
        if ground:
            bottom = ground[0]
    return bottom, names[-1]


def model_story_range(model) -> tuple[str, str]:
    stories = model.Story.GetStories()
    return lateral_story_range([str(n) for n in as_list(stories[1])],
                               [float(z) for z in as_list(stories[2])])


def keep_per_code_coefficients(fields: list[str], rows: list[list[str]]) -> int:
    """Switch "Per Code" UBC 97 rows to "User Defined" with the Ca and Cv of their inputs.

    Writing the seismic table makes ETABS put the source distance of every
    "Per Code" pattern back to 15 km, which lowers Cv near a source. The
    rows are as read before the edit, so their distance is still the right
    one; the coefficients worked out from it are written instead. Returns
    the number of rows switched.
    """
    needed = ("CoeffOpt", "SoilType", "Z", "SourceType", "SourceDist", "Ca", "Cv")
    if any(name not in fields for name in needed):
        return 0
    at = {name: fields.index(name) for name in needed}
    switched = 0
    for row in rows:
        if row[at["CoeffOpt"]] != "Per Code":
            continue
        try:
            ca, cv = seismic_coefficients(float(row[at["Z"]]), row[at["SoilType"]],
                                          row[at["SourceType"]], float(row[at["SourceDist"]]))
        except ValueError:
            continue
        row[at["CoeffOpt"]] = "User Defined"
        row[at["Ca"]], row[at["Cv"]] = f"{ca:.6g}", f"{cv:.6g}"
        switched += 1
    return switched


def set_lateral_story_range(model, log: SetupLog) -> bool:
    """Put the bottom and top story of every seismic and wind pattern on the model's range.

    Run after the stories change, so the lateral loads always cover every
    level above the ground. Returns True when something was changed.
    """
    bottom, top = model_story_range(model)
    changed = False
    tables = model.DatabaseTables
    for key in (SEISMIC_TABLE, WIND_TABLE):
        current = tables.GetTableForEditingArray(key, "", 0, [], 0, [])
        fields = [str(name) for name in as_list(current[1])]
        if "BotStory" not in fields:
            continue
        width = len(fields)
        values = ["" if v is None else str(v) for v in as_list(current[3])]
        rows = [values[i:i + width] for i in range(0, len(values), width)]
        auto = fields.index("IsAuto") if "IsAuto" in fields else None
        rows = [r for r in rows if auto is None or r[auto] != "Yes"]  # ETABS makes these again
        bot, topi = fields.index("BotStory"), fields.index("TopStory")
        edits = 0
        for row in rows:
            if row[bot] and (row[bot], row[topi]) != (bottom, top):
                row[bot], row[topi] = bottom, top
                edits += 1
        if not edits:
            continue
        kept = keep_per_code_coefficients(fields, rows) if key == SEISMIC_TABLE else 0
        flat = [value for row in rows for value in row]
        if not log.check(tables.SetTableForEditingArray(key, 0, fields, len(rows), flat),
                         f"table '{key}' could not be staged"):
            continue
        applied = tables.ApplyEditedTables(True, 0, 0, 0, 0, "")
        if return_code(applied) != 0 or int(applied[0]) or int(applied[1]):
            log.problems.append(f"table '{key}': {str(applied[4]).strip()[:300]}")
            continue
        log.done("lateral patterns on the story range", edits)
        if kept:
            log.done("seismic patterns now on user-defined Ca and Cv (same values)", kept)
        changed = True
    return changed


def save_and_reopen(model, path: str, log: SetupLog) -> bool:
    """Save the model and open it again from its EDB.

    Needed after the wind pattern table is written on a model that was
    analysed before: ETABS keeps the wind loads it generated for the old
    patterns, and its next save fails ("Error cleaning Wind Loads Arrays",
    "Index was outside the bounds of the array") and deletes the EDB. The
    first save after the edit still works, and opening that file clears
    what ETABS kept.
    """
    if not log.check(model.File.Save(path), f"saving the model: {path}"):
        return False
    opened = log.check(model.File.OpenFile(path), "opening the saved model again")
    from etabs_api.core.connection import use_working_units

    use_working_units(model)  # a file opens in its own units
    return opened


def define_lateral_loads(model, settings: dict, log: SetupLog) -> None:
    """UBC 97 seismic and ASCE 7-10 wind parameters of the lateral patterns."""
    base, top = model_story_range(model)
    s, values = settings["seismic"], seismic_values(settings)
    rows = []
    for name, directions in SEISMIC_DIRECTIONS.items():
        row = {
            "Name": name, "IsAuto": "No", "EccRatio": s["eccentricity"],
            "TopStory": top, "BotStory": base, "PeriodType": "Program Calculated",
            # ETABS does not read the zone, soil and source reliably from this
            # table, so the coefficients worked out here are given directly.
            "Ct": s["ct"], "CoeffOpt": "User Defined",
            "Ca": values["ca"], "Cv": values["cv"],
            "I": s["importance"], "R": s["r_factor"],
        }
        for flag in ("XDir", "XDirPlusE", "XDirMinusE", "YDir", "YDirPlusE", "YDirMinusE"):
            row[flag] = "Yes" if flag in directions else "No"
        rows.append(row)
    if _edit_table(model, "Load Pattern Definitions - Auto Seismic - UBC 97", rows, log):
        log.done("seismic load patterns (UBC 97)", len(rows))

    w = settings["wind"]
    rows = [
        {
            "Name": name, "IsAuto": "No", "Exposure": "Diaphragms", "TopStory": top,
            "BotStory": base, "Parapet": "No", "UserCp": "No", "ASCECase": "Create All",
            "e1": 0.15, "e2": 0.15, "WindSpeed": w["speed"], "ExpType": w["exposure"],
            "kzt": w["kzt"], "GustFact": w["gust"], "Kd": w["kd"], "Angle": angle,
        }
        for name, angle in WIND_ANGLES.items()
    ]
    # Not written when it already holds these values: any write of this table
    # makes ETABS drop the wind patterns it generated (see save_and_reopen).
    # With "Create All" ETABS keeps no angle on the pattern itself (it reads back blank).
    if (table_holds(model, WIND_TABLE, rows, ignore=("Angle",))
            or _edit_table(model, WIND_TABLE, rows, log)):
        log.done("wind load patterns (ASCE 7-10)", len(rows))


def table_holds(model, key: str, rows: list[dict], ignore: tuple = ()) -> bool:
    """True when every row is in the table already, with the same values.

    A row is found by its first field (the name); rows ETABS generated
    itself (``IsAuto`` = Yes) are not looked at. Numbers are compared as
    numbers, so 150 matches "150.0". Fields in ``ignore`` are not compared.
    """
    current = model.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    fields = [str(name) for name in as_list(current[1])]
    values = as_list(current[3])
    width = len(fields)
    if not width:
        return False
    auto = fields.index("IsAuto") if "IsAuto" in fields else None
    have: dict[str, list] = {}
    for start in range(0, len(values), width):
        row = ["" if value is None else str(value) for value in values[start:start + width]]
        if auto is None or row[auto] != "Yes":
            have.setdefault(row[0], row)  # the first row of a name holds its values

    def same(a: str, b) -> bool:
        try:
            return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
        except (TypeError, ValueError):
            return str(a).strip() == str(b).strip()

    for row in rows:
        held = have.get(str(row[fields[0]]))
        if held is None or any(name not in fields or not same(held[fields.index(name)], value)
                               for name, value in row.items() if name not in ignore):
            return False
    return True


_SEISMIC_LINE = re.compile(
    r'^(\s*SEISMIC\s+"(?P<name>[^"]+)"\s+"UBC 97".*?\bCT\s+[-\d.eE]+)\s+.*?'
    r'(?P<tail>\s+I\s+[-\d.eE]+\s+R\s+[-\d.eE]+\s*)$'
)


def per_code_seismic_text(text: str, settings: dict) -> tuple[str, int]:
    """Rewrite the UBC 97 lines of an ETABS model text file to "per code" inputs.

    The coefficient part of each seismic line becomes the soil type, zone
    factor, source type and distance, from which ETABS works out Ca and Cv
    itself. Returns the new text and the number of lines changed.
    """
    s = settings["seismic"]
    inputs = (
        f'  SOIL "{s["soil_type"]}"  Z {s["zone_factor"]:g}  '
        f'SOURCETYPE "{s["source_type"]}"    SOURCEDIST {s["distance_km"]:g}'
    )
    changed = 0
    lines = []
    for line in text.splitlines():
        match = _SEISMIC_LINE.match(line)
        if match and match["name"] in SEISMIC_DIRECTIONS:
            line = match[1] + inputs + match["tail"].rstrip()
            changed += 1
        lines.append(line)
    return "\n".join(lines) + "\n", changed


def make_seismic_per_code(model, path: str, settings: dict, log: SetupLog) -> bool:
    """Switch the seismic patterns to "per code" by reloading the model from its text file.

    ETABS only accepts the source distance through the model text file
    (``.$et``), which it writes on every save. The model is saved, the text is
    edited and opened, and the result is saved over the model again. The
    saved model is then opened: ETABS does not analyse a model it still holds
    from a text file (RunAnalysis returns 1 and writes no log).

    Reloading from text gives every object a new unique name, so it is only
    done on a model without frames (a new blank model): on any other model the
    frame tags would be lost. Such models keep the user-defined Ca and Cv,
    which give ETABS the same coefficients.
    """
    frames = int(model.FrameObj.Count())
    if frames:
        log.problems.append(f"per-code seismic skipped: the model has {frames} frames and a "
                            "reload from text would rename them (Ca and Cv stay user defined)")
        return False
    if not log.check(model.File.Save(path), "saving before the per-code seismic step"):
        return False
    text_path = os.path.splitext(path)[0] + ".$et"
    try:
        with open(text_path, encoding="utf-8", errors="surrogateescape") as handle:
            text, changed = per_code_seismic_text(handle.read(), settings)
    except OSError:
        log.problems.append("per-code seismic: ETABS wrote no model text file")
        return False
    if changed != len(SEISMIC_DIRECTIONS):
        log.problems.append(f"per-code seismic: only {changed} seismic lines were found")
        return False
    edited = os.path.splitext(path)[0] + " - per code.$et"
    with open(edited, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(text)
    try:
        from etabs_api.core.connection import use_working_units

        opened = log.check(model.File.OpenFile(edited), "opening the edited model text file")
        done = (opened and log.check(model.File.Save(path), "saving the per-code model")
                and log.check(model.File.OpenFile(path), "opening the saved per-code model"))
        use_working_units(model)  # a file opens in its own units
        return done
    finally:
        if os.path.exists(edited):
            os.remove(edited)


def define_spectrum_and_cases(model, settings: dict, log: SetupLog) -> None:
    values = seismic_values(settings)
    function = [{"Name": SPECTRUM_FUNCTION, "Ca": values["ca"], "Cv": values["cv"],
                 "DampRatio": NSCP.seismic.damping}]
    if _edit_table(model, "Functions - Response Spectrum - UBC 97", function, log):
        log.done("response spectrum function")

    modal = model.LoadCases.ModalEigen
    stories = len(as_list(model.Story.GetStories()[1])) - 1  # the first name is the base
    if MODAL_CASE not in {str(n) for n in as_list(model.LoadCases.GetNameList()[1])}:
        log.check(modal.SetCase(MODAL_CASE), "modal case")
    if log.check(modal.SetNumberModes(MODAL_CASE, number_of_modes(stories), 1), "number of modes"):
        log.done("modal case")

    spectrum = model.LoadCases.ResponseSpectrum
    for name, direction in SPECTRUM_CASES + DRIFT_SPECTRUM_CASES:
        ok = log.check(spectrum.SetCase(name), f"response spectrum case {name}")
        ok = ok and log.check(
            spectrum.SetLoads(name, 1, [direction], [SPECTRUM_FUNCTION], [values["scale"]],
                              ["Global"], [0.0]),
            f"{name} loads",
        )
        ok = ok and log.check(spectrum.SetModalCase(name, MODAL_CASE), f"{name} modal case")
        ok = ok and log.check(
            spectrum.SetEccentricity(name, settings["seismic"]["eccentricity"]),
            f"{name} eccentricity",
        )
        if ok:
            log.done("response spectrum cases")


def define_combinations(model, settings: dict, log: SetupLog, progress=None) -> None:
    api = model.RespCombo
    planned = combinations(settings)
    existing = {str(name) for name in as_list(api.GetNameList()[1])}
    # Overwrite: remove the old ones first, last created first, because a
    # combination that another one still uses cannot be deleted.
    for combo in reversed(planned):
        if combo.name in existing and return_code(api.Delete(combo.name)) != 0:
            members = api.GetCaseList(combo.name)
            for kind, member in zip(as_list(members[1]), as_list(members[2])):
                api.DeleteCase(combo.name, int(kind), str(member))
    existing = {str(name) for name in as_list(api.GetNameList()[1])}
    for index, combo in enumerate(planned):
        if progress is not None and index % 20 == 0:
            progress(f"Combination {index + 1} of {len(planned)}\t{combo.name}")
        if combo.name not in existing and not log.check(
            api.Add(combo.name, 1 if combo.envelope else 0), f"combination {combo.name}"
        ):
            continue
        ok = True
        for case, factor in combo.cases:  # 0 = load case, 1 = load combination
            ok = log.check(api.SetCaseList(combo.name, 0, case, factor),
                           f"{combo.name}: case {case}") and ok
        for other, factor in combo.combos:
            ok = log.check(api.SetCaseList(combo.name, 1, other, factor),
                           f"{combo.name}: combination {other}") and ok
        model.DesignConcrete.SetComboStrength(combo.name, bool(combo.design))
        if ok:
            log.done("load combinations")


def apply_model_setup(model, settings: dict, progress=None) -> SetupLog:
    """Define everything in the open model. Returns what was done and what failed."""
    log = SetupLog()
    if model.GetModelIsLocked():
        model.SetModelIsLocked(False)
    original_units = int(model.GetPresentUnits())
    model.SetPresentUnits(UNITS_N_MM)
    try:
        for label, step in (
            ("Materials", lambda: define_materials(model, settings, log)),
            ("Frame sections", lambda: define_sections(model, settings, log, progress)),
            ("Slab and wall sections", lambda: define_area_sections(model, settings, log)),
            ("Load patterns", lambda: define_load_patterns(model, settings, log)),
            ("Mass source", lambda: define_mass_source(model, settings, log)),
            ("P-delta", lambda: define_pdelta(model, settings, log)),
            ("Seismic and wind", lambda: define_lateral_loads(model, settings, log)),
            ("Response spectrum", lambda: define_spectrum_and_cases(model, settings, log)),
            ("Preset P-delta in the load cases", lambda: use_preset_pdelta(model, log)),
            ("Load combinations", lambda: define_combinations(model, settings, log, progress)),
        ):
            if progress is not None:
                progress(label)
            step()
    finally:
        model.SetPresentUnits(original_units)
    return log


# =============================================================================
# WITHOUT DIALOGS (scripts, Quarto)
# =============================================================================
@dataclass
class SetupResult:
    """What a setup run defined, or would define when it was only planned."""

    settings: dict
    path: str | None = None
    log: SetupLog | None = None  # None: planned only, ETABS was not touched

    def report(self, heading_level: int = 2) -> str:
        """The inputs and the definitions as Markdown."""
        h = "#" * heading_level
        settings, s, w = self.settings, self.settings["seismic"], self.settings["wind"]
        values = seismic_values(settings)
        sections = section_definitions(settings)
        patterns = load_patterns(settings)
        combos = combinations(settings)

        def table(header: list, rows: list) -> list[str]:
            lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            return lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows] + [""]

        out = []
        if self.log is None:
            out += ["*Planned only: nothing was changed in ETABS.*", ""]
        else:
            out += [f"Model: `{os.path.basename(self.path)}`", ""]
            out += table(["Defined", "Count"], list(self.log.counts.items()))
            if self.log.problems:
                out += [f"**{len(self.log.problems)} items failed:**", ""]
                out += [f"- {problem}" for problem in self.log.problems] + [""]

        out += [f"{h} Seismic parameters (UBC 97)", ""]
        out += table(["Parameter", "Value"], [
            ["Seismic zone factor, Z", f"{s['zone_factor']:g}"],
            ["Soil profile type", s["soil_type"]],
            ["Seismic source type", s["source_type"]],
            ["Distance to the source (km)", f"{s['distance_km']:g}"],
            ["Importance factor, I", f"{s['importance']:g}"],
            ["R", f"{s['r_factor']:g}"],
            ["Ct", f"{s['ct']:g}"],
            ["Ca", f"{values['ca']:.4f}"],
            ["Cv", f"{values['cv']:.4f}"],
            ["Vertical effect, Ev = 0.5 Ca I D", f"{values['ev']:.3f} D"],
            ["Response spectrum scale factor, g I / R (mm/s2)", f"{values['scale']:.1f}"],
        ])
        out += [f"{h} Wind parameters (ASCE 7-10)", ""]
        out += table(["Parameter", "Value"], [
            ["Wind speed (mph)", f"{w['speed']:g}"], ["Exposure type", w["exposure"]],
            ["Kzt", f"{w['kzt']:g}"], ["Gust factor", f"{w['gust']:g}"], ["Kd", f"{w['kd']:g}"],
        ])
        out += [f"{h} Materials", ""]
        out += table(
            ["Material", "Type", "Strength (MPa)", "E (MPa)"],
            [[p["name"], "Concrete", f"{p['fc']:.2f}", f"{p['E']:.0f}"]
             for p in map(concrete_properties, concrete_grades(settings))]
            + [[p["name"], "Rebar", f"{p['fy']:.2f}", f"{REBAR_MODULUS:.0f}"]
               for p in map(rebar_properties, settings["rebar_ksi"])],
        )
        out += [f"{h} Frame sections", ""]
        rows = []
        for prefix, label in (("G", "Girders"), ("B", "Beams"), ("FTB", "Footing tie beams"),
                              ("CR", "Rectangular columns"), ("C", "Circular columns")):
            group = [x for x in sections if x["name"].startswith(prefix + "_")]
            if group:
                widths = sorted({x["width"] for x in group})
                depths = sorted({x["depth"] for x in group})
                rows.append([label, f"`{prefix}_`", len(group), f"{widths[0]} to {widths[-1]}",
                             f"{depths[0]} to {depths[-1]}"])
        out += table(["Kind", "Prefix", "Sections", "Width (mm)", "Depth (mm)"], rows)
        out += ["", "The sections carry no stiffness modifiers: assign the cracked-section "
                "modifiers to the frames in ETABS (sdt check and sdt drift check them).", ""]
        areas = area_section_definitions(settings)
        if areas:
            out += [f"{h} Slab and wall sections", ""]
            out += table(["Section", "Kind", "Thickness (mm)", "Type"],
                         [[a["name"], a["kind"], f"{a['thickness']:g}",
                           a["shell"] + (", one way" if a.get("one_way") else "")]
                          for a in areas])
        out += [f"{h} Load patterns", ""]
        out += table(["Pattern", "Type", "Self weight"],
                     [[name, kind, f"{sw:g}"] for name, kind, sw in patterns])
        mass, pdelta = dict(mass_source_loads(settings)), dict(pdelta_loads(settings))
        out += [f"{h} Mass source and P-delta loads", ""]
        out += table(
            ["Load pattern", "Mass source", "P-delta"],
            [[name, f"{mass[name]:g}" if name in mass else "-",
              f"{pdelta[name]:g}" if name in pdelta else "-"]
             for name, _, _ in patterns if name in mass or name in pdelta],
        )
        out += [f"{h} Load combinations (NSCP 2015)", ""]
        out += [f"{len(combos)} combinations; the ULS ones are the concrete design "
                "combinations. Each seismic combination below exists eight times for the "
                "static cases (EQ1 to EQ8) and eight times for the response spectrum cases "
                "(RSA1 to RSA8).", ""]
        seen, rows = set(), []
        for combo in combos:
            if not combo.name.startswith(("ULS", "SLS", "SSLC")):
                continue
            name = re.sub(r" (EQ|RSA)\d*$", " E", combo.name)
            if name not in seen:
                seen.add(name)
                dead = next((f"{factor:.3g}" for case, factor in combo.cases
                             if case == "SELFWEIGHT"), "-")
                rows.append([name, dead, "Yes" if combo.design else "No"])
        out += table(["Combination", "Dead load factor", "Design"], rows)
        return "\n".join(out)


def _attach_or_start(start: bool):
    """The running ETABS, or a new one when ``start`` is set and none is running."""
    from etabs_api.core.connection import (
        DEFAULT_ETABS_PROGRAM_PATH,
        NOT_RUNNING,
        etabs_helper,
        running_etabs,
    )

    helper = etabs_helper()
    etabs = running_etabs(helper)
    if etabs is not None:
        return etabs.SapModel
    if not start:
        raise RuntimeError(NOT_RUNNING)
    etabs = helper.CreateObject(os.environ.get("ETABS_PROGRAM_PATH", DEFAULT_ETABS_PROGRAM_PATH))
    etabs.ApplicationStart()
    return etabs.SapModel


def setup_model(
    settings: dict | None = None,
    target: str = "open",
    path: str | None = None,
    copy_model: bool = False,
    apply: bool = True,
) -> SetupResult:
    """Set up a model without dialogs.

    ``settings`` holds only what differs from ``DEFAULT_SETTINGS``. ``target``
    is ``"open"`` (the model open in ETABS; ``copy_model`` saves it beside
    itself as ``<name> - SETUP.EDB`` first) or ``"new"`` (a blank model saved
    at ``path``). With ``apply=False`` nothing is sent to ETABS and the result
    only describes what would be defined.
    """
    settings = merge_settings(settings)
    seismic_values(settings)  # refuse invalid seismic inputs before touching ETABS
    if not apply:
        return SetupResult(settings)
    if target not in ("open", "new"):
        raise ValueError('target must be "open" or "new".')
    new_model = target == "new"
    if new_model and not path:
        raise ValueError('A new model needs the path to save it at.')
    model = _attach_or_start(start=new_model)
    if new_model:
        path = os.path.splitext(os.path.abspath(path))[0] + ".EDB"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        model.InitializeNewModel(UNITS_N_MM)
        model.File.NewBlank()
    else:
        path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
        if not os.path.isfile(path):
            raise RuntimeError("Save the ETABS model first: it has no file yet.")
        if copy_model:
            stem, extension = os.path.splitext(path)
            path, counter = f"{stem} - SETUP{extension}", 2
            while os.path.exists(path):
                path, counter = f"{stem} - SETUP ({counter}){extension}", counter + 1
    if return_code(model.File.Save(path)) != 0:
        raise RuntimeError(f"ETABS could not save the model: {path}")
    log = apply_model_setup(model, settings)
    if new_model:
        remove_blank_model_defaults(model)
        make_seismic_per_code(model, path, settings, log)
    save_and_reopen(model, path, log)
    save_settings(settings, settings_path(path))
    return SetupResult(settings, path, log)


# =============================================================================
# DIALOGS
# =============================================================================
def _numbers(text: str) -> list[float]:
    return [float(part) for part in str(text).replace(";", ",").split(",") if part.strip()]


def _names(text: str) -> list[str]:
    return [part.strip().upper() for part in str(text).split(",") if part.strip()]


def _join(values) -> str:
    return ", ".join(f"{value:g}" if isinstance(value, (int, float)) else str(value)
                     for value in values)


def ask_settings(settings: dict) -> dict | None:
    """Ask for every input, starting from ``settings``. None when a dialog is closed."""
    from utilities._gui_helpers import enter_values, select_option

    settings = copy.deepcopy(settings)

    def ask(title: str, prompt: str, fields: dict) -> dict | None:
        answers = enter_values(f"Model Setup - {title}", prompt, list(fields),
                               {label: str(value) for label, value in fields.items()})
        return answers

    # ---- materials ----
    labels = {
        "Concrete strengths (ksi)": _join(settings["concrete_ksi"]),
        "Rebar grades (ksi)": _join(settings["rebar_ksi"]),
    }
    answers = ask("Materials", "Materials to define, separated by commas. 5 ksi concrete is "
                  "named C05 and grade 60 rebar G60.", labels)
    if answers is None:
        return None
    settings["concrete_ksi"] = _numbers(answers["Concrete strengths (ksi)"])
    settings["rebar_ksi"] = _numbers(answers["Rebar grades (ksi)"])

    # ---- sections ----
    ranges = settings["sections"]
    fields = {
        "Concrete of the sections (ksi)": _join(settings["section_concrete_ksi"]),
        "Rebar of the sections (ksi)": f"{settings['section_rebar_ksi']:g}",
    }
    keys = {}
    for prefix, text in (("G", "Girders G"), ("B", "Beams B"), ("FTB", "Footing tie beams FTB")):
        for part in ("width", "depth"):
            label = f"{text}: {part} (min, max, step)"
            fields[label] = _join(ranges.get(prefix, {}).get(part, []))
            keys[label] = (prefix, part)
    for prefix, part, label in (
        ("CR", "size", "Rectangular columns CR: side (min, max, step)"),
        ("C", "diameter", "Circular columns C: diameter (min, max, step)"),
    ):
        fields[label] = _join(ranges.get(prefix, {}).get(part, []))
        keys[label] = (prefix, part)
    answers = ask("Frame Sections", "Size ranges in mm. Leave a range blank to skip that "
                  "kind of section.", fields)
    if answers is None:
        return None
    settings["section_concrete_ksi"] = _numbers(answers["Concrete of the sections (ksi)"])
    settings["section_rebar_ksi"] = _numbers(answers["Rebar of the sections (ksi)"])[0]
    for label, (prefix, part) in keys.items():
        ranges.setdefault(prefix, {})[part] = _numbers(answers[label])

    # ---- slabs and walls ----
    slab_type_label = f"Slab type ({', '.join(SLAB_SHELL_TYPES)})"
    fields = {
        "Slab thicknesses (mm)": _join(settings["slabs"].get("thickness", [])),
        slab_type_label: settings["slabs"].get("type", "Membrane"),
        "Concrete of the slabs (ksi)": f"{area_concrete_ksi(settings, 'slabs'):g}",
        "Wall thicknesses (mm)": _join(settings["walls"].get("thickness", [])),
        "Concrete of the walls (ksi)": f"{area_concrete_ksi(settings, 'walls'):g}",
    }
    answers = ask("Slab and Wall Sections", "Thicknesses separated by commas. Leave a list "
                  "blank to skip those sections. Walls are thin shells.", fields)
    if answers is None:
        return None
    slab_type = next((t for t in SLAB_SHELL_TYPES
                      if t.lower() == answers[slab_type_label].strip().lower()), "Membrane")
    one_way = False
    slab_thicknesses = _numbers(answers["Slab thicknesses (mm)"])
    if slab_type == "Membrane" and slab_thicknesses:
        options = {
            "Yes - add a one-way slab for each thickness "
            f"(S_<t>_<concrete>{ONE_WAY_SUFFIX})": True,
            "No - two-way slabs only": False,
        }
        chosen = select_option(
            "Model Setup - Slab and Wall Sections",
            "The slabs are membranes, which spread their load two ways. Also add a one-way "
            "counterpart of each slab?", list(options),
            default_index=0 if settings["slabs"].get("one_way") else 1)
        if chosen is None:
            return None
        one_way = options[chosen]
    settings["slabs"] = {"thickness": slab_thicknesses,
                         "type": slab_type,
                         "concrete_ksi": _numbers(answers["Concrete of the slabs (ksi)"])[0],
                         "one_way": one_way}
    settings["walls"] = {"thickness": _numbers(answers["Wall thicknesses (mm)"]),
                         "concrete_ksi": _numbers(answers["Concrete of the walls (ksi)"])[0]}
    settings.pop("area_concrete_ksi", None)  # replaced by the two inputs above

    # ---- seismic ----
    s = settings["seismic"]
    fields = {
        "Seismic zone factor Z": s["zone_factor"], "Soil profile type (SA to SE)": s["soil_type"],
        "Seismic source type (A, B, C)": s["source_type"],
        "Distance to the source (km)": s["distance_km"], "Importance factor I": s["importance"],
        "R": s["r_factor"], "Ct (as typed in ETABS)": s["ct"],
        "Accidental eccentricity ratio": s["eccentricity"],
    }
    answers = ask("Seismic (UBC 97)", "One set of seismic inputs for the whole project. Ca and "
                  "Cv are worked out from them.", fields)
    if answers is None:
        return None
    for key, label in zip(
        ("zone_factor", "soil_type", "source_type", "distance_km", "importance", "r_factor",
         "ct", "eccentricity"), fields,
    ):
        text = answers[label].strip()
        s[key] = text.upper() if key in ("soil_type", "source_type") else float(text)
    seismic_values(settings)  # raises a clear error on an invalid zone, soil or source

    # ---- wind ----
    w = settings["wind"]
    fields = {
        "Wind speed (as typed in ETABS, mph)": w["speed"], "Exposure type (B, C, D)": w["exposure"],
        "Kzt": w["kzt"], "Gust factor": w["gust"], "Kd": w["kd"],
    }
    answers = ask("Wind (ASCE 7-10)", "Wind parameters of WX and WY.", fields)
    if answers is None:
        return None
    for key, label in zip(("speed", "exposure", "kzt", "gust", "kd"), fields):
        text = answers[label].strip()
        w[key] = text.upper() if key == "exposure" else float(text)

    # ---- extra load patterns ----
    fields = {
        "Extra super dead patterns": ", ".join(settings["extra_dead"]),
        "Extra live patterns": ", ".join(settings["extra_live"]),
        "Extra reducible live patterns": ", ".join(settings["extra_reducible_live"]),
    }
    standard = ", ".join(name for name, _, _ in STANDARD_PATTERNS)
    answers = ask("Load Patterns", f"The standard patterns are always created: {standard}. "
                  "Type any extra ones, separated by commas.", fields)
    if answers is None:
        return None
    settings["extra_dead"] = _names(answers["Extra super dead patterns"])
    settings["extra_live"] = _names(answers["Extra live patterns"])
    settings["extra_reducible_live"] = _names(answers["Extra reducible live patterns"])

    # ---- mass source ----
    options = {
        f"Yes - include {REDUCIBLE_LIVE_MASS_FACTOR:.0%} of the reducible live load": True,
        "No - dead loads and non-reducible live load only": False,
    }
    chosen = select_option(
        "Model Setup - Mass Source",
        "The seismic mass takes every dead load and the non-reducible live load. "
        "Include reducible live load (LIVERED) as well?",
        list(options),
        default_index=0 if settings["mass"]["include_reducible_live"] else 1,
    )
    if chosen is None:
        return None
    settings["mass"]["include_reducible_live"] = options[chosen]
    return settings


def run_model_setup() -> str | None:
    """Entry point: connect to ETABS or make a blank model, ask the inputs, define everything."""
    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_save_path,
        show_warning,
    )

    from etabs_api.core.connection import (
        DEFAULT_ETABS_PROGRAM_PATH,
        NOT_RUNNING,
        etabs_helper,
        running_etabs,
    )

    title = "Model Setup"
    source = select_option(title, "Which model should be set up?", [
        "The model that is open in ETABS", "A new blank model",
    ])
    if source is None:
        return None
    helper = etabs_helper()
    new_model = source.startswith("A new")
    etabs = running_etabs(helper)
    if etabs is not None:
        model = etabs.SapModel
    else:
        if not new_model:
            show_warning(NOT_RUNNING, title=title)
            return None
        with LoadingWindow("Starting ETABS..."):
            etabs = helper.CreateObject(
                os.environ.get("ETABS_PROGRAM_PATH", DEFAULT_ETABS_PROGRAM_PATH)
            )
            etabs.ApplicationStart()
            model = etabs.SapModel

    if new_model:
        path = select_save_path("Save the new ETABS model as", "New Model", ".EDB", "ETABS model")
        if not path:
            return None
        # The dialog returns forward slashes, which ETABS reads as a path
        # inside its own program folder.
        path = os.path.splitext(os.path.normpath(path))[0] + ".EDB"
    else:
        path = os.path.splitext(os.path.normpath(str(model.GetModelFilename())))[0] + ".EDB"
        if not os.path.isfile(path):
            show_warning("Save the ETABS model first: it has no file yet.", title=title)
            return None
        where = select_option(title, "Where should the definitions go?", [
            "Into this model", "Into a copy saved beside it",
        ])
        if where is None:
            return None
        if where.startswith("Into a copy"):
            stem, extension = os.path.splitext(path)
            path, counter = f"{stem} - SETUP{extension}", 2
            while os.path.exists(path):
                path, counter = f"{stem} - SETUP ({counter}){extension}", counter + 1

    # Inputs: saved for this model, else asked, starting from the last ones used.
    saved = load_settings(settings_path(path))
    if saved is not None:
        again = select_option(title, "This model has saved setup inputs.", [
            "Use the saved inputs", "Review and change them",
        ])
        if again is None:
            return None
        settings = merge_settings(saved)
        if again.startswith("Review"):
            settings = ask_settings(settings)
    else:
        settings = ask_settings(merge_settings(load_settings(defaults_path())))
    if settings is None:
        return None

    with LoadingWindow("Defining the model parameters...") as window:
        if new_model:
            # Only now, after every dialog was confirmed, so a cancelled run
            # does not replace the model that is open in ETABS.
            model.InitializeNewModel(UNITS_N_MM)
            model.File.NewBlank()
        if return_code(model.File.Save(path)) != 0:
            show_warning(f"ETABS could not save the model:\n{path}", title=title)
            return None
        log = apply_model_setup(model, settings, progress=window.update)
        if new_model:
            remove_blank_model_defaults(model)
            # A new model holds nothing else yet, so reloading it from text is safe.
            make_seismic_per_code(model, path, settings, log)
        window.update("Saving and opening the model again")
        save_and_reopen(model, path, log)
    save_settings(settings, settings_path(path))
    save_settings(settings, defaults_path())

    setup_summary(log, settings, path, new_model).show(popup=True)
    return path


def setup_summary(log: SetupLog, settings: dict, path: str, new_model: bool = False):
    """The closing summary of a model setup."""
    from utilities.run_summary import RunSummary

    values = seismic_values(settings)
    summary = RunSummary("sdt setup", path)
    summary.add("Model", "new blank model" if new_model else "the open model")
    for what, count in log.counts.items():
        summary.add(what[:1].upper() + what[1:], count)
    summary.add("Seismic coefficients", f"Ca {values['ca']:.4f}, Cv {values['cv']:.4f}, "
                                        f"Ev {values['ev']:.3f} D")
    summary.add("Spectrum scale factor", f"{values['scale']:.1f} (g I / R; sdt analyze scales it "
                                         "to the static base shear)")
    for problem in log.problems:
        summary.fail(problem)
    summary.file("Model", path)
    summary.file("Setup inputs", settings_path(path))
    return summary


if __name__ == "__main__":
    run_model_setup()
