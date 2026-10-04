"""What the toolkit knows about a model, for models it did not set up itself.

A model built by ``sdt setup`` follows conventions the commands rely on: the
``ULS`` / ``DEF`` / ``DRIFT`` combination names, section names such as
``G_300X500_C04_G60``, tagged members and the saved setup inputs. A model
built by hand, or by someone else, may have none of these.

Every command therefore checks what it needs before it starts. What the
model already holds is read from it; what it does not is asked, once, and
saved beside the model in ``<model>.setup.json`` under ``"model"``. Nothing
falls back to a hidden default and nothing is skipped without a word.

Saved under ``"model"``:

* ``strength``    the combinations to design for
* ``deflection``  the combination of each deflection role
* ``drift``       the combinations of the drift check
* ``families``    section family (G, B, FTB, CR, C) of each frame section
* ``seismic``     Z, I, R and Ct confirmed for this model

The functions that only work on plain data need no ETABS and no dialogs.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from design.beam_deflection import (
    COMBO_DEAD,
    COMBO_FULL,
    COMBO_ROOF,
    COMBO_SUSTAINED,
    DEFLECTION_COMBOS,
)

MODEL_KEY = "model"
# ETABS load pattern types (eLoadPatternType)
DEAD_TYPES, LIVE_TYPES, ROOF_TYPES = (1, 2), (3, 4), (11,)
SEISMIC_TYPES, WIND_TYPES = (5, 61), (6,)
GRAVITY, SEISMIC, WIND, OTHER = "gravity", "seismic", "wind", "other"
ADD_IT = "Let the toolkit add it: {name}"
MAX_CHOICES = 24  # options one choice dialog can show

DEFLECTION_ROLES = {
    COMBO_DEAD: "dead load only",
    COMBO_FULL: "dead + live load",
    COMBO_SUSTAINED: "dead + sustained (25 %) live load",
    COMBO_ROOF: "dead + roof live load",
}


# =============================================================================
# SAVED WITH THE MODEL
# =============================================================================
def edb_path(model_filename: str) -> str:
    """The .EDB path of a model (ETABS can report its text file as the model file)."""
    return os.path.splitext(os.path.normpath(str(model_filename)))[0] + ".EDB"


def load(model_path: str) -> dict:
    """What was saved for this model; empty when there is nothing."""
    from etabs_api.workflows.model_setup import load_settings, settings_path

    return dict((load_settings(settings_path(model_path)) or {}).get(MODEL_KEY) or {})


def save(model_path: str, **values) -> None:
    """Add ``values`` to what is saved for this model; the setup inputs are kept."""
    from etabs_api.workflows.model_setup import load_settings, save_settings, settings_path

    path = settings_path(model_path)
    data = load_settings(path) or {}
    data.setdefault(MODEL_KEY, {}).update(values)
    save_settings(data, path)


def has_setup_inputs(model_path: str) -> bool:
    """Whether the model has setup inputs of its own (from ``sdt setup``), not
    only the answers saved here."""
    from etabs_api.workflows.model_setup import load_settings, settings_path

    return "sections" in (load_settings(settings_path(model_path)) or {})


# =============================================================================
# COMBINATIONS (plain data)
# =============================================================================
@dataclass
class Combinations:
    """The load combinations of a model and what each one holds.

    ``terms`` has the linear-add combinations, each as its load cases and
    factors with nested combinations expanded (``analysis_forces.ComboTerms``).
    An envelope is not one set of forces, so it is in ``names`` but not there.
    """

    names: list[str] = field(default_factory=list)
    terms: dict = field(default_factory=dict)
    pattern_types: dict[str, int] = field(default_factory=dict)

    @property
    def linear(self) -> list[str]:
        """The combinations a member can be designed for, in model order."""
        return [name for name in self.names if name in self.terms]

    @property
    def envelopes(self) -> list[str]:
        return [name for name in self.names if name not in self.terms]

    def kind(self, name: str) -> str:
        """gravity, seismic or wind, from the load cases of the combination."""
        terms = self.terms.get(name)
        if terms is None:
            return OTHER
        if terms.spectral:
            return SEISMIC
        types = {self.pattern_types.get(case) for case in terms.static}
        if types & set(SEISMIC_TYPES):
            return SEISMIC
        if types & set(WIND_TYPES):
            return WIND
        return GRAVITY if types <= set(DEAD_TYPES + LIVE_TYPES + ROOF_TYPES) else OTHER

    def lateral_case(self, name: str) -> str | None:
        """The seismic or wind case with the largest factor in a combination."""
        terms = self.terms.get(name)
        if terms is None:
            return None
        lateral = {case: abs(factor) for case, factor in terms.cases.items()
                   if case in terms.spectral
                   or self.pattern_types.get(case) in SEISMIC_TYPES + WIND_TYPES}
        return max(lateral, key=lateral.get) if lateral else None

    def factors(self, name: str) -> dict[str, float]:
        """Sum of the factors of the dead, live and roof live cases of a combination."""
        out = {"dead": 0.0, "live": 0.0, "roof": 0.0, "other": 0.0}
        terms = self.terms.get(name)
        if terms is None:
            return out
        counts = {"dead": 0, "live": 0, "roof": 0}
        for case, factor in terms.cases.items():
            kind = self.pattern_types.get(case)
            key = ("dead" if kind in DEAD_TYPES else "live" if kind in LIVE_TYPES
                   else "roof" if kind in ROOF_TYPES else "other")
            if key == "other":
                out["other"] += abs(factor)
            else:
                out[key] += factor
                counts[key] += 1
        for key, count in counts.items():  # the factor on each pattern of the kind
            if count:
                out[key] /= count
        return out


def standard_strength(names: list[str], seismic: str = "Both") -> list[str]:
    """The combinations named as ``sdt setup`` names them (``ULS ...``)."""
    out = []
    for name in names:
        if not str(name).startswith("ULS"):
            continue
        is_eq = re.search(r"\bEQ\d", name) is not None
        is_rsa = re.search(r"\bRSA\d", name) is not None
        if (not is_eq and not is_rsa) or (is_eq and seismic in ("EQ", "Both")) \
                or (is_rsa and seismic in ("RSA", "Both")):
            out.append(name)
    return out


def gravity_options(combinations: Combinations, chosen: list[str]) -> list[str]:
    """The chosen combinations with no seismic or wind term, for the beam seismic
    shear; every chosen one when none can be told apart."""
    plain = [name for name in chosen if combinations.kind(name) == GRAVITY]
    if plain:
        return plain
    by_name = [c for c in chosen if not re.search(r"\b(EQ|RSA)\d|\bW[XY]\b", c)]
    return by_name or list(chosen)


def suggest_deflection(combinations: Combinations) -> dict[str, list[str]]:
    """Existing combinations that could serve each deflection role, best first.

    A candidate holds only gravity cases. It is ranked by how close its
    factors are to the role (1.0 D; 1.0 D + 1.0 L; 1.0 D + 0.25 L; 1.0 D + 1.0 Lr).
    """
    wanted = {
        COMBO_DEAD: (1.0, 0.0, 0.0), COMBO_FULL: (1.0, 1.0, 0.0),
        COMBO_SUSTAINED: (1.0, 0.25, 0.0), COMBO_ROOF: (1.0, 0.0, 1.0),
    }
    gravity = [name for name in combinations.linear if combinations.kind(name) == GRAVITY]
    out = {}
    for role, (dead, live, roof) in wanted.items():
        scored = []
        for name in gravity:
            f = combinations.factors(name)
            distance = (abs(f["dead"] - dead) + abs(f["live"] - live) + abs(f["roof"] - roof))
            scored.append((distance, name))
        out[role] = [name for _, name in sorted(scored, key=lambda item: item[0])]
    return out


def deflection_roles(names: list[str], saved: dict | None = None) -> dict[str, str | None]:
    """Combination of each deflection role: the standard name when the model has
    it, else the saved choice when it still exists, else None (to be asked).

    A saved choice equal to the standard name means "let the toolkit add it".
    """
    present, saved = set(names), saved or {}
    out: dict[str, str | None] = {}
    for role in DEFLECTION_COMBOS:
        if role in present:
            out[role] = role
        elif saved.get(role) == role or saved.get(role) in present:
            out[role] = saved[role]
        else:
            out[role] = None
    return out


def drift_cases(combinations: Combinations, chosen: list[str]) -> dict[str, tuple[str, bool]]:
    """For each chosen drift combination: (its lateral load case, whether it is wind)."""
    out = {}
    for name in chosen:
        case = combinations.lateral_case(name)
        if case is None:
            continue
        wind = (case not in combinations.terms[name].spectral
                and combinations.pattern_types.get(case) in WIND_TYPES)
        out[name] = (case, wind)
    return out


# =============================================================================
# MEMBERS AND SECTIONS (plain data)
# =============================================================================
def unnamed_members(names: list[str]) -> list[str]:
    """Members that still have the number ETABS gave them."""
    return [str(name) for name in names if str(name).isnumeric()]


def grade_tag(prefix: str, strength_mpa: float, per_ksi: float) -> str:
    """``C04`` for a 27.6 MPa concrete, ``G60`` for a 414 MPa bar: the setup tags."""
    if not strength_mpa or not math.isfinite(float(strength_mpa)):
        return f"{prefix}00"
    return f"{prefix}{int(round(float(strength_mpa) / per_ksi)):02d}"


def beam_family(frames_into_column: bool, on_bottom_level: bool) -> str:
    """Family of a beam whose section name does not say it: a tie beam on the
    bottom-most level, a girder when it frames into a column, else a beam."""
    if on_bottom_level:
        return "FTB"
    return "G" if frames_into_column else "B"


def geometric_lines(connectivity: pd.DataFrame, points: pd.DataFrame | None) -> dict[str, str]:
    """Beam line of every beam, from the geometry: beams in line with each other
    that meet at a joint with no column belong to one line.

    ETABS splits a beam where another member frames into it. Tagged members
    carry their line in the name (``2GX-1``, ``2GX-1A``); members with other
    names are joined here instead. Returns {beam: name of the first beam of
    its line}; a beam alone is its own line.
    """
    if connectivity is None or connectivity.empty or points is None or points.empty:
        return {}
    conn = connectivity.copy()
    conn["UniqueName"] = conn["UniqueName"].astype(str)
    kind = conn["DesignType"].astype(str)
    xyz = {str(n): (float(x), float(y), float(z)) for n, x, y, z in zip(
        points["UniqueName"], pd.to_numeric(points["X"], errors="coerce"),
        pd.to_numeric(points["Y"], errors="coerce"), pd.to_numeric(points["Z"], errors="coerce"))}
    columns = conn[kind.eq("Column")]
    supports = set(columns["UniquePtI"].astype(str)) | set(columns["UniquePtJ"].astype(str))
    beams = conn[kind.eq("Beam")].drop_duplicates("UniqueName")
    ends = {name: (str(i), str(j)) for name, i, j in zip(
        beams["UniqueName"], beams["UniquePtI"], beams["UniquePtJ"])}

    def direction(name: str, start: str):
        i, j = ends[name]
        far = j if i == start else i
        if start not in xyz or far not in xyz:
            return None
        dx, dy, dz = (xyz[far][k] - xyz[start][k] for k in range(3))
        size = math.sqrt(dx * dx + dy * dy + dz * dz)
        return (dx / size, dy / size, dz / size) if size > 1e-9 else None

    at_joint: dict[str, list[str]] = {}
    for name, (i, j) in ends.items():
        at_joint.setdefault(i, []).append(name)
        at_joint.setdefault(j, []).append(name)
    parent = {name: name for name in ends}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for joint, members in at_joint.items():
        if joint in supports or len(members) < 2:
            continue
        # two beams continue each other when they leave the joint in opposite directions
        for a_index, a in enumerate(members):
            da = direction(a, joint)
            if da is None:
                continue
            for b in members[a_index + 1:]:
                db = direction(b, joint)
                if db is not None and sum(p * q for p, q in zip(da, db)) < -0.966:  # 15 degrees
                    parent[find(a)] = find(b)
    order = {name: index for index, name in enumerate(ends)}
    first: dict[str, str] = {}
    for name in ends:
        root = find(name)
        if root not in first or order[name] < order[first[root]]:
            first[root] = name
    return {name: first[find(name)] for name in ends}


# =============================================================================
# SEISMIC VALUES
# =============================================================================
SEISMIC_FIELDS = {"zone_factor": "Z", "importance": "I", "r_factor": "R", "ct": "Ct"}


def seismic_from_table(table: pd.DataFrame | None) -> dict[str, float]:
    """Z, I, R and Ct of the UBC 97 seismic patterns of a model; empty when the
    model has none (seismic loads of another code, or user loads)."""
    if table is None or table.empty:
        return {}
    rows = table
    if "IsAuto" in rows.columns:
        rows = rows[rows["IsAuto"].astype(str) != "Yes"]
    out = {}
    for key, column in SEISMIC_FIELDS.items():
        if column in rows.columns:
            values = pd.to_numeric(rows[column], errors="coerce").dropna()
            if len(values):
                out[key] = float(values.iloc[0])
    return out


def seismic_differences(model: dict, saved: dict) -> dict[str, tuple[float, float]]:
    """Values that differ between the model and what was saved: key -> (model, saved)."""
    out = {}
    for key in SEISMIC_FIELDS:
        if key in model and key in saved:
            try:
                a, b = float(model[key]), float(saved[key])
            except (TypeError, ValueError):
                continue
            if abs(a - b) > 1e-6 * max(1.0, abs(a), abs(b)):
                out[key] = (a, b)
    return out


# =============================================================================
# READING THE MODEL
# =============================================================================
def read_combinations(connector) -> Combinations:
    """The combinations of the open model with their load cases."""
    from etabs_api.workflows import analysis_forces as af

    cases = af._read(connector, "Load Case Definitions - Summary")
    case_types = dict(zip(cases["Name"].astype(str), cases["Type"].astype(str)))
    definitions = af._read(connector, "Load Combination Definitions")
    names = list(dict.fromkeys(definitions["Name"].dropna().astype(str))) \
        if "Name" in definitions.columns else []
    terms = af.combination_terms(definitions, case_types) if names else {}
    return Combinations(names, terms, af.pattern_types(connector))


def read_seismic(connector) -> dict[str, float]:
    """Z, I, R and Ct from the UBC 97 seismic patterns of the open model."""
    from etabs_api.core.helpers import as_list

    model = connector.sap_model
    try:
        names = [str(n) for n in as_list(model.LoadPatterns.GetNameList(0, [])[1])]
        model.DatabaseTables.SetLoadPatternsSelectedForDisplay(names)
        table = connector._read_database_table("Load Pattern Definitions - Auto Seismic - UBC 97")
    except Exception:
        return {}
    return seismic_from_table(table)


def wall_count(connector) -> int:
    """Number of wall panels in the open model (they are not designed)."""
    try:
        table = connector.get_data("Wall Object Connectivity")
    except RuntimeError:
        return 0
    return 0 if table is None else len(table)


# =============================================================================
# WHERE EACH INPUT CAME FROM
# =============================================================================
@dataclass
class Sources:
    """Which inputs of a run were read from the model, answered or assumed."""

    model: list[str] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)

    def add_to(self, summary) -> None:
        """Write the three lists into a ``RunSummary``."""
        if self.model:
            summary.add("Read from the model", "; ".join(self.model))
        if self.answered:
            summary.add("Answered by you", "; ".join(self.answered))
        if self.assumed:
            summary.add("Assumed", "; ".join(self.assumed))


# =============================================================================
# DIALOGS
# =============================================================================
@dataclass
class Readiness:
    """What a command found in the model before it starts."""

    command: str
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # will be asked, or stops the command
    warnings: list[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        """The dialog is shown only when something is missing or to be warned of."""
        return bool(self.missing or self.warnings)

    def text(self) -> str:
        lines = [f"Before {self.command} starts on this model:"]
        if self.found:
            lines += ["", "Found:"] + [f"  - {item}" for item in self.found]
        if self.missing:
            lines += ["", "Missing (asked next, then saved with the model):"] + [
                f"  - {item}" for item in self.missing]
        if self.warnings:
            lines += ["", "Check:"] + [f"  - {item}" for item in self.warnings]
        return "\n".join(lines)

    def confirm(self, title: str) -> bool:
        """Show the report when it is needed. False when the user stops."""
        if not self.needed:
            return True
        from utilities._gui_helpers import select_option

        chosen = select_option(title, self.text(), ["Continue", "Stop"])
        return chosen == "Continue"


def ask_strength_combinations(connector, model_path: str, title: str,
                              combinations: Combinations | None = None
                              ) -> tuple[list[str], str] | None:
    """The combinations to design for when the model has no ``ULS`` names.

    The user picks from the model's own combinations (envelopes are left out:
    they are not one set of forces). The choice is saved with the model and
    offered again next time. Returns (combinations, where they came from), or
    None when a dialog is closed.
    """
    from utilities._gui_helpers import DualListboxSelector, select_option, show_warning

    combinations = combinations or read_combinations(connector)
    candidates = combinations.linear
    if not candidates:
        show_warning("The model has no load combination a member can be designed for "
                     "(linear-add combinations of load cases). Envelopes are not one set of "
                     "forces. Define the strength combinations, or run sdt setup.", title=title)
        return None
    saved = [name for name in load(model_path).get("strength", []) if name in candidates]
    if saved:
        chosen = select_option(
            title, f"This model has no ULS combinations. {len(saved)} combinations were chosen "
            "for it before:\n\n" + "\n".join(saved[:14])
            + (f"\n... and {len(saved) - 14} more" if len(saved) > 14 else ""),
            ["Use these", "Choose again"])
        if chosen is None:
            return None
        if chosen == "Use these":
            return saved, "saved with the model"
    left_out = len(combinations.envelopes)
    picked = DualListboxSelector(
        "Strength combinations to design for"
        + (f" ({left_out} envelopes left out)" if left_out else ""), candidates).show()
    picked = [name for name in candidates if name in set(picked)]
    if not picked:
        return None
    save(model_path, strength=picked)
    return picked, "picked by you"


def ask_deflection_roles(connector, model_path: str, title: str,
                         combinations: Combinations | None = None) -> dict[str, str] | None:
    """The combination of each deflection role; asked for the roles the model lacks.

    Returns {standard name: combination to read}. A role mapped to its own
    standard name that the model does not have is to be added by the toolkit.
    None when a dialog is closed.
    """
    from utilities._gui_helpers import select_option

    combinations = combinations or read_combinations(connector)
    roles = deflection_roles(combinations.names, load(model_path).get("deflection"))
    if all(roles.values()):
        return {role: str(name) for role, name in roles.items()}
    suggestions = suggest_deflection(combinations)
    for role, name in roles.items():
        if name is not None:
            continue
        add = ADD_IT.format(name=role)
        options = suggestions[role][:MAX_CHOICES - 1] + [add]
        chosen = select_option(
            title, f"Deflection check: which combination is {DEFLECTION_ROLES[role]}, "
            "unfactored? The model has none under the standard name. Existing gravity "
            "combinations are listed, the closest first.", options,
            default_index=0 if len(options) > 1 else len(options) - 1)
        if chosen is None:
            return None
        roles[role] = role if chosen == add else chosen
    save(model_path, deflection=roles)
    return {role: str(name) for role, name in roles.items()}


def ask_drift_combinations(connector, model_path: str, title: str,
                           combinations: Combinations | None = None) -> list[str] | None:
    """The drift combinations when the model has no ``DRIFT`` / ``WDRIFT`` names."""
    from utilities._gui_helpers import DualListboxSelector, select_option, show_warning

    combinations = combinations or read_combinations(connector)
    candidates = [name for name in combinations.linear
                  if combinations.kind(name) in (SEISMIC, WIND)]
    if not candidates:
        show_warning("The model has no linear-add combination with a seismic or wind case to "
                     "check the drift on. Define them, or run sdt setup.", title=title)
        return None
    saved = [name for name in load(model_path).get("drift", []) if name in candidates]
    if saved:
        chosen = select_option(
            title, f"This model has no DRIFT / WDRIFT combinations. {len(saved)} combinations "
            "were chosen for the drift before:\n\n" + "\n".join(saved[:14]),
            ["Use these", "Choose again"])
        if chosen is None:
            return None
        if chosen == "Use these":
            return saved
    picked = DualListboxSelector("Combinations to check the drift on (seismic and wind)",
                                 candidates).show()
    picked = [name for name in candidates if name in set(picked)]
    if not picked:
        return None
    save(model_path, drift=picked)
    return picked


def ask_seismic_values(model_path: str, title: str, model_values: dict, saved: dict,
                       wanted: tuple = ("zone_factor", "ct")) -> tuple[dict, Sources] | None:
    """Z, Ct (and the others in ``wanted``) for this run.

    Read from the model's UBC 97 patterns where they exist. A value saved
    before that differs from the model is asked; a value that is nowhere is
    asked too. Returns the values and where each came from.
    """
    from utilities._gui_helpers import enter_values, show_warning

    sources = Sources()
    values, to_ask = {}, {}
    differences = seismic_differences(model_values, saved)
    for key in wanted:
        symbol = SEISMIC_FIELDS[key]
        if key in differences:
            to_ask[key] = (f"{symbol}: the model has {differences[key][0]:g}, saved before "
                           f"{differences[key][1]:g}", f"{differences[key][0]:g}")
        elif key in model_values:
            values[key] = float(model_values[key])
            sources.model.append(f"{symbol} {values[key]:g}")
        elif key in saved:
            values[key] = float(saved[key])
            sources.answered.append(f"{symbol} {values[key]:g} (saved)")
        else:
            defaults = {"zone_factor": 0.4, "ct": 0.03, "importance": 1.0, "r_factor": 8.5}
            # user Ca, Cv patterns keep no zone; a model of another code has no pattern
            why = ("the model's UBC 97 patterns do not hold it, as with user defined Ca, Cv"
                   if model_values else "not in the model: no UBC 97 seismic pattern")
            to_ask[key] = (f"{symbol} ({why})", f"{defaults[key]:g}")
    while to_ask:
        labels = {label: key for key, (label, _) in to_ask.items()}
        typed = enter_values(title, "Seismic values for this model. They are saved with it.",
                             list(labels), {label: default for label, default in to_ask.values()})
        if typed is None:
            return None
        try:
            for label, key in labels.items():
                values[key] = float(typed[label])
                if values[key] <= 0:
                    raise ValueError
            break
        except ValueError:
            show_warning("Every value must be a positive number.", title=title)
    for key in to_ask:
        sources.answered.append(f"{SEISMIC_FIELDS[key]} {values[key]:g}")
    if to_ask:
        save(model_path, seismic={**saved, **values})
    return values, sources
