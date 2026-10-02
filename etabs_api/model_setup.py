"""Automatic definition of the standard parameters of an ETABS concrete model.

Defines, in the model that is open in ETABS or in a new blank one:

* materials             concrete ``C05`` (strength in ksi) and rebar ``G60``
* frame sections        ``G_500X800_C06_G60`` and so on, from size ranges
* load patterns         the office standard set, UBC 97 seismic, ASCE 7-10 wind
* response spectrum     the UBC 97 function, with Ca and Cv from the seismic inputs
* load cases            modal and the two response spectrum cases
* load combinations     NSCP 2015, see ``load_combinations.py``

Run it with ``python main.py``. The inputs are asked for in dialogs and saved
beside the model, so they are not asked again for the same model.

The planning functions work on plain data and need no ETABS.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re

from .helpers import as_list, return_code
from .load_combinations import Combination, build_combinations
from .ubc97 import (
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
BEAM_MODIFIER = 0.35  # on I22 and I33
COLUMN_MODIFIER = 0.70
BEAM_COVER = 60.0  # mm, to the bar centre, top and bottom
COLUMN_COVER = 40.0  # mm
BEAM_MIN_WIDTH_TO_DEPTH = 0.3
COLUMN_MIN_SIDE_RATIO = 0.5
BEAM_PREFIXES = ("G", "B", "FTB")  # girders, beams, footing tie beams
OMEGA0 = 2.8
RHO = 1.0
LIVE_FACTOR = 0.5  # f on live load in the seismic and wind combinations

# The office standard load patterns: (name, ETABS type, self weight multiplier).
STANDARD_PATTERNS = (
    ("SELFWEIGHT", "Dead", 1.0),
    ("SIDL", "Super Dead", 0.0),
    ("CMUWALLS", "Super Dead", 0.0),
    ("EXTERIOR WALLS", "Super Dead", 0.0),
    ("FACADE", "Super Dead", 0.0),
    ("FLOORBEAMS", "Super Dead", 0.0),
    ("DLBALCONY", "Super Dead", 0.0),
    ("H2ODEAD", "Super Dead", 0.0),
    ("LIVERED", "Reducible Live", 0.0),
    ("LIVENRED", "Live", 0.0),
    ("LIVEMECH", "Live", 0.0),
    ("LIVEOTHER", "Live", 0.0),
    ("LLBALCONY", "Live", 0.0),
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
    "seismic": {
        "zone_factor": 0.4, "soil_type": "SD", "source_type": "A", "distance_km": 10.0,
        "importance": 1.0, "r_factor": 8.5, "ct": 0.03, "eccentricity": 0.05,
    },
    "wind": {"speed": 150.0, "exposure": "B", "kzt": 1.0, "gust": 0.85, "kd": 0.85},
    "extra_dead": [],
    "extra_live": [],
    "extra_reducible_live": [],
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
    return os.path.join(os.path.expanduser("~"), ".xlwings_structural", "model_setup.json")


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


def define_materials(model, settings: dict, log: SetupLog) -> None:
    api = model.PropMaterial
    for ksi in settings["concrete_ksi"]:
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


def define_sections(model, settings: dict, log: SetupLog, progress=None) -> None:
    api = model.PropFrame
    sections = section_definitions(settings)
    for index, section in enumerate(sections):
        name, material, rebar = section["name"], section["material"], section["rebar"]
        if progress is not None and index % 20 == 0:
            progress(f"Section {index + 1} of {len(sections)}\t{name}")
        if section["kind"] == "circle":
            created = api.SetCircle(name, material, section["depth"])
        else:  # (name, material, depth t3, width t2)
            created = api.SetRectangle(name, material, section["depth"], section["width"])
        if not log.check(created, f"section {name}"):
            continue
        log.done("frame sections")
        factor = BEAM_MODIFIER if section["kind"] == "beam" else COLUMN_MODIFIER
        # area, shear 2, shear 3, torsion, I22, I33, mass, weight
        log.check(api.SetModifiers(name, [1.0, 1.0, 1.0, 1.0, factor, factor, 1.0, 1.0]),
                  f"{name} stiffness modifiers")
        if section["kind"] == "beam":
            log.check(api.SetRebarBeam(name, rebar, rebar, BEAM_COVER, BEAM_COVER, 0, 0, 0, 0),
                      f"{name} reinforcement data")
        else:
            circular = section["kind"] == "circle"
            # pattern 1 rectangular / 2 circular; confinement 1 ties / 2 spiral; to be designed
            log.check(
                api.SetRebarColumn(name, rebar, rebar, 2 if circular else 1,
                                   2 if circular else 1, COLUMN_COVER, 8 if circular else 0,
                                   5, 3, "20", "10", 150.0, 3, 3, True),
                f"{name} reinforcement data",
            )


def remove_blank_model_defaults(model) -> None:
    """Delete the 'Dead' and 'Live' patterns ETABS puts in a new blank model.

    The default 'Dead' pattern carries self weight, which SELFWEIGHT already does.
    """
    names = {str(name) for name in as_list(model.LoadPatterns.GetNameList()[1])}
    for name in ("Dead", "Live"):
        if name in names:
            model.LoadCases.Delete(name)
            model.LoadPatterns.Delete(name)


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


def define_lateral_loads(model, settings: dict, log: SetupLog) -> None:
    """UBC 97 seismic and ASCE 7-10 wind parameters of the lateral patterns."""
    stories = [str(name) for name in as_list(model.Story.GetStories()[1])]
    base, top = stories[0], stories[-1]
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
    if _edit_table(model, "Load Pattern Definitions - Auto Wind - ASCE 7-10", rows, log):
        log.done("wind load patterns (ASCE 7-10)", len(rows))


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
    edited and opened, and the result is saved over the model again.
    """
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
        opened = log.check(model.File.OpenFile(edited), "opening the edited model text file")
        return opened and log.check(model.File.Save(path), "saving the per-code model")
    finally:
        if os.path.exists(edited):
            os.remove(edited)


def define_spectrum_and_cases(model, settings: dict, log: SetupLog) -> None:
    values = seismic_values(settings)
    function = [{"Name": SPECTRUM_FUNCTION, "Ca": values["ca"], "Cv": values["cv"],
                 "DampRatio": 0.05}]
    if _edit_table(model, "Functions - Response Spectrum - UBC 97", function, log):
        log.done("response spectrum function")

    modal = model.LoadCases.ModalEigen
    stories = len(as_list(model.Story.GetStories()[1])) - 1  # the first name is the base
    if MODAL_CASE not in {str(n) for n in as_list(model.LoadCases.GetNameList()[1])}:
        log.check(modal.SetCase(MODAL_CASE), "modal case")
    if log.check(modal.SetNumberModes(MODAL_CASE, number_of_modes(stories), 1), "number of modes"):
        log.done("modal case")

    spectrum = model.LoadCases.ResponseSpectrum
    for name, direction in (("RSAX", "U1"), ("RSAY", "U2")):
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
            ("Load patterns", lambda: define_load_patterns(model, settings, log)),
            ("Seismic and wind", lambda: define_lateral_loads(model, settings, log)),
            ("Response spectrum", lambda: define_spectrum_and_cases(model, settings, log)),
            ("Load combinations", lambda: define_combinations(model, settings, log, progress)),
        ):
            if progress is not None:
                progress(label)
            step()
    finally:
        model.SetPresentUnits(original_units)
    return log


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
    from utilities._gui_helpers import enter_values

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
    return settings


def run_model_setup() -> str | None:
    """Entry point: connect to ETABS or make a blank model, ask the inputs, define everything."""
    import comtypes.client

    from utilities._gui_helpers import (
        LoadingWindow,
        select_option,
        select_save_path,
        show_warning,
    )

    from .connection import DEFAULT_ETABS_PROGRAM_PATH

    title = "Model Setup"
    source = select_option(title, "Which model should be set up?", [
        "The model that is open in ETABS", "A new blank model",
    ])
    if source is None:
        return None
    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
    new_model = source.startswith("A new")
    try:
        etabs = helper.GetObject("CSI.ETABS.API.ETABSObject")
        model = etabs.SapModel
    except Exception:
        etabs = None
    if etabs is None:
        if not new_model:
            show_warning("ETABS is not running. Open the model in ETABS first.", title=title)
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
        model.InitializeNewModel(UNITS_N_MM)
        model.File.NewBlank()
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
        if return_code(model.File.Save(path)) != 0:
            show_warning(f"ETABS could not save the model:\n{path}", title=title)
            return None
        log = apply_model_setup(model, settings, progress=window.update)
        if new_model:
            remove_blank_model_defaults(model)
            # A new model holds nothing else yet, so reloading it from text is safe.
            make_seismic_per_code(model, path, settings, log)
        model.File.Save(path)
    save_settings(settings, settings_path(path))
    save_settings(settings, defaults_path())

    values = seismic_values(settings)
    lines = [f"{count} {what}" for what, count in log.counts.items()]
    message = (
        "Defined:\n" + "\n".join(lines)
        + f"\n\nCa = {values['ca']:.4f}, Cv = {values['cv']:.4f}, Ev = {values['ev']:.3f} D"
        + f"\nResponse spectrum scale factor = {values['scale']:.1f} (g I / R, not yet scaled "
        "to the static base shear)"
        + f"\n\nSaved as:\n{path}"
    )
    if log.problems:
        message += f"\n\n{len(log.problems)} items failed:\n" + "\n".join(log.problems[:12])
    show_warning(message, title=title)
    return path
