"""The tables of a column design: cleaning, names, joints, local axes and the permutations of a combination."""

import math
import re
import numpy as np
import pandas as pd
from design.criteria.code_config import CODE, AciCode
from utilities._calc_report import number


def _clean_table(frame: pd.DataFrame | None) -> pd.DataFrame:
    """A copy of a table with stripped column names (empty when None)."""
    if frame is None:
        return pd.DataFrame()
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _normalize_object_name(value) -> str:
    """Normalize Excel/ETABS object identifiers, including numeric IDs like 502.0."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        integer_text = text[:-2]
        if integer_text.isdigit() and (
            integer_text == "0" or not integer_text.startswith("0")
        ):
            return integer_text
    return text


def _normalize_names(names: pd.Series) -> pd.Series:
    """``_normalize_object_name`` of every value of a column, worked out once for each
    different name: the force table repeats a few thousand names millions of times."""
    codes, different = pd.factorize(names, use_na_sentinel=True)     # a missing name: -1
    cleaned = np.empty(len(different) + 1, dtype=object)
    cleaned[:-1] = [_normalize_object_name(name) for name in different]
    cleaned[-1] = _normalize_object_name(None)
    return pd.Series(cleaned[codes], index=names.index, name=names.name)


class IncompleteJointDataError(ValueError):
    """Signal that SMRF joint checks lack a framing member's design/load data."""


def _deduplicate_frame_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse repeated frame rows after verifying each member has one property set."""
    keys = ["UniqueName"]
    property_columns = [
        column
        for column in ("DesignType", "SectProp", "f'c", "Width", "Depth", "Diameter", "fy", "fys")
        if column in frame.columns
    ]
    unique_properties = frame.groupby(keys, dropna=False)[property_columns].nunique(
        dropna=False
    )
    inconsistent = unique_properties.index[(unique_properties > 1).any(axis=1)]
    if len(inconsistent):
        names = ", ".join(map(str, inconsistent[:10]))
        raise ValueError(
            "FRAME DATA contains conflicting rows for the same member: "
            f"{names}. Refresh the source table before designing."
        )
    return frame.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)


def _require_columns(
    frame: pd.DataFrame, required: set[str], source_name: str
) -> None:
    """Raise a source-specific error when an input table is missing required fields."""
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"{source_name} is missing required columns: {', '.join(sorted(missing))}. "
            f"Available columns: {', '.join(map(str, frame.columns))}"
        )


def _numeric(value, field_name: str, object_name: str) -> float:
    """Convert a required table value to a finite number with a useful error."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} for {object_name} must be numeric; received {value!r}."
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            f"{field_name} for {object_name} must be finite; received {value!r}."
        )
    return number


def _as_etabs_dataframe(result, table_name: str) -> pd.DataFrame:
    """Validate and normalize a DataFrame returned by the ETABS table API."""
    if isinstance(result, dict):
        raise RuntimeError(
            f"ETABS failed to retrieve '{table_name}': "
            f"{result.get('error', result)}"
        )
    if not isinstance(result, pd.DataFrame):
        raise RuntimeError(
            f"ETABS returned no tabular data for '{table_name}' "
            f"({type(result).__name__})."
        )
    result = result.copy()
    result.columns = [str(column).strip() for column in result.columns]
    if result.empty:
        raise RuntimeError(f"ETABS returned an empty '{table_name}' table.")
    return result


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...], source: str) -> str:
    """Find a case-insensitive field alias or report the received schema."""
    lookup = {str(column).strip().casefold(): column for column in frame.columns}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    raise ValueError(
        f"Could not identify {source} in table columns "
        f"{', '.join(map(str, frame.columns))}."
    )


def _extract_point_coordinates(point_table: pd.DataFrame) -> dict[str, np.ndarray]:
    """Build point coordinates from ETABS' Point Object Connectivity table.

    Returns:
        A map from normalized ETABS point unique names to global XYZ coordinates in
        the model's current length units.
    """
    point_name = _find_column(
        point_table, ("Point", "PointName", "UniqueName", "Name", "Label"), "point name"
    )
    x_column = _find_column(point_table, ("X", "X-coordinate", "Global X"), "X coordinate")
    y_column = _find_column(point_table, ("Y", "Y-coordinate", "Global Y"), "Y coordinate")
    z_column = _find_column(point_table, ("Z", "Z-coordinate", "Global Z"), "Z coordinate")

    coordinates: dict[str, np.ndarray] = {}
    for _, row in point_table.iterrows():
        name = _normalize_object_name(row[point_name])
        coordinates[name] = np.array(
            [
                _numeric(row[x_column], str(x_column), name),
                _numeric(row[y_column], str(y_column), name),
                _numeric(row[z_column], str(z_column), name),
            ],
            dtype=float,
        )
    return coordinates


def _extract_frame_angles(frame_table: pd.DataFrame) -> dict[str, float]:
    """Read local-axis rotation angles, in degrees, keyed by ETABS UniqueName.

    Returns:
        A map from normalized frame unique names to their ETABS beta angles.
    """
    name_column = _find_column(
        frame_table, ("UniqueName", "Frame", "Name", "Label"), "frame name"
    )
    angle_column = _find_column(
        frame_table,
        ("Angle", "Local Axis Angle", "Angle (deg)", "AngleDeg"),
        "local-axis angle",
    )
    angles: dict[str, float] = {}
    for _, row in frame_table.iterrows():
        name = _normalize_object_name(row[name_column])
        angle = _numeric(row[angle_column], str(angle_column), name)
        angles[name] = angle
    return angles


def _extract_frame_labels(frame_table: pd.DataFrame) -> dict[str, str]:
    """Read ETABS display labels, keyed by normalized frame UniqueName."""
    name_column = _find_column(
        frame_table, ("UniqueName", "Frame", "Name"), "frame unique name"
    )
    label_column = _find_column(
        frame_table, ("Label", "Frame Label"), "ETABS frame label"
    )
    labels = {}
    for _, row in frame_table.iterrows():
        name = _normalize_object_name(row[name_column])
        label = str(row[label_column]).strip()
        if name and label:
            labels[name] = label
    return labels


def _common_column_mark(unique_name: object) -> str:
    """Extract the shared column mark from a floor-qualified unique name.

    ``2F - C1`` and ``2GC-1`` both give ``C1``; a planted column ``PD2PC-1A``
    gives ``PC1A``.
    """
    text = _normalize_object_name(unique_name)
    match = re.search(r"(?:GC|(P)C|C)-?(\d+[A-Z]*)\s*$", text, flags=re.IGNORECASE)
    if not match:
        return text
    return f"{'P' if match.group(1) else ''}C{match.group(2)}".upper()


def _story_order_from_stacks(
    connectivity: pd.DataFrame, story_of: dict[str, str]
) -> list[str]:
    """Stories from the bottom up, read from how the columns stand on each other.

    ETABS draws a column from its bottom joint (I) to its top joint (J), so a
    column whose bottom joint is another column's top joint is one story higher.
    Story names are free text (``UG``, ``PD1``, ``2F``) and cannot be ordered
    reliably by name; stories the stacks do not relate fall back to the name.
    """
    stories = list(dict.fromkeys(str(story) for story in story_of.values()))
    if connectivity.empty or not {"UniqueName", "UniquePtI", "UniquePtJ"} <= set(
        connectivity.columns
    ):
        return sorted(stories, key=_column_story_sort_key)
    ends = {
        _normalize_object_name(row.UniqueName): (
            _normalize_object_name(row.UniquePtI), _normalize_object_name(row.UniquePtJ)
        )
        for row in connectivity.itertuples()
    }
    story_at_top = {
        ends[member][1]: str(story) for member, story in story_of.items() if member in ends
    }
    above: dict[str, set[str]] = {story: set() for story in stories}
    for member, story in story_of.items():
        below = story_at_top.get(ends.get(member, ("", ""))[0])
        if below is not None and below != str(story):
            above[below].add(str(story))
    waiting = {story: 0 for story in stories}
    for higher in above.values():
        for story in higher:
            waiting[story] += 1
    ordered: list[str] = []
    while len(ordered) < len(stories):
        ready = [s for s in stories if s not in ordered and waiting[s] == 0]
        if not ready:  # stacks contradict each other: finish by name
            ready = [s for s in stories if s not in ordered]
            ordered.extend(sorted(ready, key=_column_story_sort_key))
            break
        lowest = min(ready, key=_column_story_sort_key)
        ordered.append(lowest)
        for story in above[lowest]:
            waiting[story] -= 1
    return ordered


def _unit_vector(vector: np.ndarray, description: str) -> np.ndarray:
    """Return a normalized 3D vector, rejecting degenerate frame geometry."""
    length = float(np.linalg.norm(vector))
    if length <= 1e-9:
        raise ValueError(f"Cannot determine {description} from coincident endpoints.")
    return vector / length


def _frame_local_axes(
    point_i: np.ndarray, point_j: np.ndarray, angle_degrees: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Construct ETABS local 1/2/3 axes from endpoints and beta angle.

    ETABS local 1 follows I-to-J. The unrotated 2/3 basis is chosen using global
    vertical as the reference; ETABS' beta rotation then rotates axes 2 and 3.
    """
    local_1 = _unit_vector(point_j - point_i, "frame local axis 1")
    vertical = np.array([0.0, 0.0, 1.0])
    local_2 = vertical - np.dot(vertical, local_1) * local_1
    if np.linalg.norm(local_2) <= 1e-8:
        reference = np.array([1.0, 0.0, 0.0])
        local_2 = reference - np.dot(reference, local_1) * local_1
    local_2 = _unit_vector(local_2, "frame local axis 2")
    local_3 = _unit_vector(np.cross(local_1, local_2), "frame local axis 3")

    beta = math.radians(angle_degrees)
    rotated_2 = math.cos(beta) * local_2 + math.sin(beta) * local_3
    rotated_3 = -math.sin(beta) * local_2 + math.cos(beta) * local_3
    return local_1, rotated_2, rotated_3


def _section_bending_angle(moment_2: float, moment_3: float) -> float:
    """Bending angle of the section for moments about ETABS local 2 and local 3.

    ETABS puts the section depth (t3, ``Depth``) along local 2 and the width
    (t2, ``Width``) along local 3. M3 therefore bends the section over its
    depth, which is angle 0 here, and M2 bends it over its width, angle pi/2.
    """
    return math.atan2(moment_2, moment_3)


def _to_compression_positive(
    factored_loads: pd.DataFrame, code: AciCode = CODE
) -> pd.DataFrame:
    """Return a copy of the ETABS force table with axial force compression-positive.

    ETABS reports compression as negative. Every check in this module treats
    compression as positive, so the sign of ``P`` is flipped here, once.
    The input table is not modified.
    """
    converted = factored_loads.copy()
    if code.conventions.etabs_compression_is_negative:
        converted["P"] = -pd.to_numeric(converted["P"], errors="coerce")
        if "P_sustained" in converted.columns:  # the dead-load share, for beta_dns
            converted["P_sustained"] = -pd.to_numeric(converted["P_sustained"], errors="coerce")
    return converted


def _expand_combo_permutations(
    factored_loads: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Give every ETABS permutation of a load combination its own combo label.

    One combination with a response-spectrum or multi-direction case produces
    several permutations, each a different P-M2-M3 set. A column must be checked
    against every set, so each permutation becomes a separate internal combo
    (``"<combo>-<permutation>"``).

    Returns:
        The force table with internal combo labels, and a map from each internal
        label back to the plain combination name shown to the user.
    """
    converted = factored_loads.copy()
    names = converted["Combo"].astype(str).str.strip()
    if "Permutation" not in converted.columns:
        converted["Combo"] = names
        return converted, {name: name for name in names.unique()}
    permutation = pd.to_numeric(converted["Permutation"], errors="coerce")
    internal = names.where(
        permutation.isna(),
        names + "-" + permutation.fillna(0).astype(int).astype(str),
    )
    converted["Combo"] = internal
    return converted, dict(zip(internal, names))


def _collapse_combo_permutations(
    load_checks: pd.DataFrame,
    shear_checks: pd.DataFrame,
    joint_results: pd.DataFrame,
    combo_display: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Relabel permutation-level results with the plain combination name.

    Flexure/axial keeps one row per column, combination and end: the governing
    permutation, i.e. a failing one if any, otherwise the highest utilization.
    Permutations with the same utilization (to ``RATIO_TOLERANCE``; at a pinned
    end every one is zero) are told apart by the larger axial compression, so
    that the row shown does not depend on the last digits of the analysis.
    Shear and joint rows are only relabelled; the report builder already takes
    the worst value of every row that shares a column, combination and end.
    """
    def plain(series: pd.Series) -> pd.Series:
        text = series.astype(str)
        return text.map(combo_display).fillna(text)

    if not load_checks.empty:
        load_checks = load_checks.copy()
        load_checks["Combo"] = plain(load_checks["Combo"])
        utilization = pd.to_numeric(
            load_checks.get("Flexure_Utilization"), errors="coerce"
        )
        load_checks["_not_pass"] = load_checks["Strength_Check"].astype(str).ne("PASS")
        load_checks["_utilization"] = utilization.fillna(math.inf).round(
            GOVERNING_DECIMALS)
        load_checks["_axial"] = pd.to_numeric(
            load_checks.get("Pu_kN"), errors="coerce").fillna(-math.inf).round(
            GOVERNING_DECIMALS)
        load_checks = (
            load_checks.sort_values(
                ["_not_pass", "_utilization", "_axial"], ascending=False, kind="stable"
            )
            .drop_duplicates(subset=["UniqueName", "Combo", "End"], keep="first")
            .sort_index()
            .drop(columns=["_not_pass", "_utilization", "_axial"])
            .reset_index(drop=True)
        )
    if not shear_checks.empty:
        shear_checks = shear_checks.copy()
        shear_checks["Combo"] = plain(shear_checks["Combo"])
    if not joint_results.empty and "Load_Combo" in joint_results.columns:
        joint_results = joint_results.copy()
        joint_results["Load_Combo"] = plain(joint_results["Load_Combo"])
    return load_checks, shear_checks, joint_results


GOVERNING_DECIMALS = 9   # utilizations and axial loads equal to this many decimals are a tie


def _column_story_sort_key(story: object) -> tuple[int, str]:
    """Return a practical bottom-to-top order for common ETABS story labels."""
    text = str(story).strip().upper()
    if text in {"GF", "GROUND", "GROUND FLOOR"}:
        return 0, text
    basement = re.search(r"(?:B|BASEMENT)\s*(\d+)", text)
    if basement:
        return -int(basement.group(1)), text
    floor = re.search(r"(?:P|PENTHOUSE)\s*(\d+)", text)
    if floor:
        return 10000 + int(floor.group(1)), text
    number = re.search(r"-?\d+", text)
    if number:
        return int(number.group()), text
    return 0, text
