"""Ties, hoops and spirals: confinement and the transverse detailing rules (ACI 18.7.5, 25.7)."""

import math
import numpy as np
import pandas as pd
from design.criteria.code_config import AciCode
from design.concrete.columns.section import (
    ColumnFlexureDesign,
)


RATIO_TOLERANCE = 1e-9


def _meets(provided: float, required: float) -> bool:
    """Whether a provided ratio meets the required one.

    The tie spacing is often the very spacing at which the provided
    confinement equals the required one, so the two numbers differ only in
    the last digit of the arithmetic: a plain >= then passes or fails by
    chance. A relative tolerance of 1e-9 settles it.
    """
    return provided >= required * (1.0 - RATIO_TOLERANCE)


def _smrf_so_limit(code: AciCode, hx: float | None = None) -> float:
    """Hoop spacing limit 'so' of ACI 18.7.5.3: 100 + (350 - hx)/3, kept within 100..150 mm."""
    cfg = code.column_seismic
    hx = cfg.hx_assumed if hx is None else hx
    so = cfg.so_min + (cfg.so_hx_reference - hx) / cfg.so_hx_divisor
    return min(cfg.so_max, max(cfg.so_min, so))


def _minimum_tie_diameter(engine: ColumnFlexureDesign, bar_layout=None) -> float:
    """Smallest permitted transverse bar diameter (NSCP 425.7.2.2 / 425.7.3.2), in mm.

    Ties are 12 mm for bars larger than 32 mm and for BUNDLED bars, 10 mm otherwise.
    """
    cfg = engine.code.column_transverse
    if engine.shape == "circular":
        return cfg.spiral_diameter_min
    bundled = bar_layout is not None and any(count > 1 for _, _, count in bar_layout)
    if engine.dmain > cfg.large_bar_threshold or bundled:
        return cfg.tie_diameter_large_bars
    return cfg.tie_diameter_small_bars


def _confined_core(engine: ColumnFlexureDesign) -> tuple[float, float]:
    """bc along the width and along the depth: the core to the OUTSIDE edges of the
    hoops (ACI 18.7.5.4 notation), in mm."""
    return engine.width - 2.0 * engine.cc, engine.height - 2.0 * engine.cc


def _column_transverse_candidate_passes(
    frame_row: pd.Series,
    force_rows: pd.DataFrame,
    engine: ColumnFlexureDesign,
    bar_layout: list[tuple[float, float, int]],
    max_compression: float,
    is_smrf: bool,
) -> tuple[bool, list[dict] | None, int]:
    """Check whether a longitudinal layout can satisfy its transverse detailing."""
    short_dimension = (
        min(engine.width, engine.height)
        if engine.shape == "rectangular"
        else engine.diameter
    )
    long_dimension = (
        max(engine.width, engine.height)
        if engine.shape == "rectangular"
        else engine.diameter
    )
    if not is_smrf:
        spacing_limit = engine.solve_max_spacing()["max_tie_spacing_mm"]
        transverse = {
            "Transverse_Legs_Per_Direction": 2,
            "Transverse_Spacing_Provided_mm": spacing_limit,
        }
    elif engine.shape == "rectangular":
        seismic = engine.code.column_seismic
        spacing_limit = min(
            engine.solve_max_spacing()["max_tie_spacing_mm"],
            seismic.spacing_dimension_fraction * short_dimension,
            seismic.spacing_bar_multiple * engine.dmain,
            _smrf_so_limit(engine.code),
        )
    else:
        seismic = engine.code.column_seismic
        spacing_limit = min(
            engine.solve_max_spacing()["max_tie_spacing_mm"],
            seismic.spacing_dimension_fraction * short_dimension,
            seismic.spacing_bar_multiple * engine.dmain,
            seismic.spiral_pitch_offset + engine.dties,
        )
    if is_smrf:
        transverse = _smrf_transverse_design(
            engine,
            len(bar_layout),
            max_compression,
            spacing_limit,
        )
        provided_legs, _, support_check = _post_check_alternating_support(
            engine,
            bar_layout,
            transverse["Transverse_Legs_Per_Direction"],
        )
    else:
        provided_legs = 2
        support_check = "PASS"
    if is_smrf and engine.shape == "rectangular":
        core_width, core_height = _confined_core(engine)
        tie_area = math.pi * engine.dties**2 / 4.0
        provided_x = provided_legs * tie_area / (
            transverse["Transverse_Spacing_Provided_mm"] * core_width
        )
        provided_y = provided_legs * tie_area / (
            transverse["Transverse_Spacing_Provided_mm"] * core_height
        )
        confinement_passes = (
            _meets(provided_x, transverse["Required_Confinement_Ratio_X"])
            and _meets(provided_y, transverse["Required_Confinement_Ratio_Y"])
        )
    elif is_smrf:
        confinement_passes = (
            transverse["Confinement_Check"] == "PASS"
        )
    else:
        confinement_passes = True

    minimum_tie_diameter = _minimum_tie_diameter(engine, bar_layout)
    seismic_cfg = engine.code.column_seismic
    detailing_passes = (
        not is_smrf
        or (
            short_dimension >= seismic_cfg.min_dimension
            and short_dimension / long_dimension >= seismic_cfg.min_aspect_ratio
            and confinement_passes
            and transverse["Transverse_Spacing_Check"] == "PASS"
            # "N/A for continuous spiral": a spiral supports every bar
            and support_check.startswith(("PASS", "N/A"))
            and engine.dties >= minimum_tie_diameter
        )
    )
    return detailing_passes, None, 0


def _encode_bar_layout(layout: list[tuple[float, float, int]]) -> str:
    """Write a bar layout as text, ``"x,y,count;x,y,count;..."`` (mm, bars per position)."""
    return ";".join(f"{x:.2f},{y:.2f},{int(count)}" for x, y, count in layout)


def _decode_bar_layout(text: object) -> list[tuple[float, float, int]] | None:
    """Read a layout written by ``_encode_bar_layout``; ``None`` if the cell holds none."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None
    entries = [entry for entry in str(text).split(";") if entry.strip()]
    if not entries:
        return None
    layout = []
    for entry in entries:
        parts = entry.split(",")
        if len(parts) != 3:
            raise ValueError(f"Unrecognized bar layout data: {text!r}")
        layout.append((float(parts[0]), float(parts[1]), int(float(parts[2]))))
    return layout


def _column_layout_summary(layout: list[tuple[float, float, int]]) -> str:
    """Format the bundle count summary used in column-design report cells."""
    return "; ".join(
        (
            f"{sum(count == size for _, _, count in layout)} single bars"
            if size == 1
            else (
                f"{sum(count == size for _, _, count in layout)} "
                f"bundles of {size} bars"
            )
        )
        for size in sorted({count for _, _, count in layout})
    )


def _smrf_transverse_design(
    engine: ColumnFlexureDesign,
    n_bars: int,  # bar positions (a bundle counts once), for kn
    max_compression: float,
    spacing_limit: float,
) -> dict:
    """Estimate SMRF confinement demand and propose transverse reinforcement.

    ``max_compression`` is in N; all section dimensions, bar sizes, and spacing
    are in mm. The returned leg counts are a calculated minimum, not a bar-layout
    drawing or a complete seismic detailing audit.
    """
    # Ag is gross area; Ach is the core bounded by the transverse reinforcement.
    ag = (
        engine.width * engine.height
        if engine.shape == "rectangular"
        else math.pi * engine.diameter**2 / 4.0
    )
    tie_area = math.pi * engine.dties**2 / 4.0
    seismic = engine.code.column_seismic
    fyt = min(engine.fyt, engine.code.material.max_fyt_confinement)
    kf = max(1.0, engine.fc / seismic.kf_fc_divisor + seismic.kf_offset)
    # Trigger the additional confinement term for high axial load or high-strength
    # concrete. The code threshold is 0.3 Ag fc (all in N).
    high_axial_or_high_strength = (
        max_compression > seismic.high_axial_fraction * ag * engine.fc
        or engine.fc > seismic.high_strength_fc
    )

    if engine.shape == "circular":
        # Dc and Ach are measured to the OUTSIDE edge of the spiral (ACI 25.7.3.3,
        # 18.7.5.4): rho_s = 4 Asp / (Dc s), the ratio of spiral to core volume.
        core_diameter = engine.diameter - 2.0 * engine.cc
        if core_diameter <= 0:
            raise ValueError("Circular column core diameter must be positive.")
        ach = math.pi * core_diameter ** 2 / 4.0
        confinement_expressions = {
            "18.7.5(d)": seismic.spiral_coeff_d * (ag / ach - 1.0) * engine.fc / fyt,
            "18.7.5(e)": seismic.spiral_coeff_e * engine.fc / fyt,
        }
        if high_axial_or_high_strength:
            confinement_expressions["18.7.5(f)"] = (
                seismic.spiral_coeff_f * kf * max_compression / (fyt * ach)
            )
        controlling_expression, required_ratio = max(
            confinement_expressions.items(), key=lambda item: item[1]
        )
        pitch_for_confinement = (
            4.0 * tie_area / (required_ratio * core_diameter)
            if required_ratio > 0
            else math.inf
        )
        spacing = min(spacing_limit, pitch_for_confinement)
        provided_ratio = 4.0 * tie_area / (core_diameter * spacing)
        clear_spacing = spacing - engine.dties
        check = (
            "PASS"
            if _meets(provided_ratio, required_ratio)
            and engine.code.column_transverse.spiral_clear_spacing_min
            <= clear_spacing
            <= engine.code.column_transverse.spiral_clear_spacing_max
            else "FAIL"
        )
        return {
            "Transverse_Type": "Spiral",
            "Core_Dimension_mm": core_diameter,
            "Confinement_18_7_5_d": confinement_expressions["18.7.5(d)"],
            "Confinement_18_7_5_e": confinement_expressions["18.7.5(e)"],
            "Confinement_18_7_5_f": confinement_expressions.get(
                "18.7.5(f)", np.nan
            ),
            "Kf": kf,
            "Kn": np.nan,
            "Confinement_Criteria_Governing": controlling_expression,
            "Required_Ash_s_Ratio_X": required_ratio,
            "Required_Ash_s_Ratio_Y": required_ratio,
            "Provided_Ash_s_Ratio_X": provided_ratio,
            "Provided_Ash_s_Ratio_Y": provided_ratio,
            "Required_Confinement_Ratio": required_ratio,
            "Provided_Confinement_Ratio": provided_ratio,
            "Confinement_Check": check,
            "Transverse_Spacing_Provided_mm": spacing,
            "Transverse_Spacing_Check": (
                "PASS" if spacing <= spacing_limit + 1e-8 else "FAIL"
            ),
            "High_Axial_or_High_fc_Check": (
                "APPLIED" if high_axial_or_high_strength else "NOT APPLIED"
            ),
            "Spiral_Clear_Spacing_mm": clear_spacing,
            "Transverse_Legs_Per_Direction": 1,
            "Required_Legs_X": 1,
            "Required_Legs_Y": 1,
            "Alternating_Support_Check": "N/A for continuous spiral",
        }

    # bc and Ach are measured to the OUTSIDE edges of the hoops (ACI 18.7.5.4,
    # NSCP 418.7.5.4 notation): Ash >= ratio * s * bc.
    core_width, core_height = _confined_core(engine)
    if min(core_width, core_height) <= 0:
        raise ValueError("Rectangular column core dimensions must be positive.")
    ach = core_width * core_height

    # kn = nl / (nl - 2): nl counts the bars or BUNDLES laterally supported around
    # the perimeter (ACI 18.7.5.4), so a bundle is one; callers pass positions.
    nl = max(n_bars, 4)
    kn = nl / (nl - 2.0)
    base_a = seismic.rect_coeff_a * (ag / ach - 1.0) * engine.fc / fyt
    base_b = seismic.rect_coeff_b * engine.fc / fyt
    confinement_expressions = {
        "18.7.5(a)": base_a,
        "18.7.5(b)": base_b,
    }
    if high_axial_or_high_strength:
        base_c = seismic.rect_coeff_c * kf * kn * max_compression / (fyt * ach)
        confinement_expressions["18.7.5(c)"] = base_c
    controlling_expression, required_ratio = max(
        confinement_expressions.items(), key=lambda item: item[1]
    )

    short_dimension = min(engine.width, engine.height)
    hx_limit = (
        seismic.hx_max_high_axial if high_axial_or_high_strength else seismic.hx_max
    )
    hx = min(seismic.hx_assumed, hx_limit)
    so = _smrf_so_limit(engine.code, hx)
    spacing = min(
        spacing_limit,
        seismic.spacing_dimension_fraction * short_dimension,
        seismic.spacing_bar_multiple * engine.dmain,
        so,
    )

    # The perimeter layout uses interior support legs in addition to the closed hoop.
    max_leg_spacing = engine.code.column_strength.max_longitudinal_spacing
    nx = max(1, math.ceil(core_width / max_leg_spacing))
    ny = max(1, math.ceil(core_height / max_leg_spacing))
    minimum_legs = max(2, nx, ny)
    required_legs_x = max(
        minimum_legs,
        math.ceil(required_ratio * spacing * core_width / tie_area),
    )
    required_legs_y = max(
        minimum_legs,
        math.ceil(required_ratio * spacing * core_height / tie_area),
    )
    provided_ratio_x = required_legs_x * tie_area / (spacing * core_width)
    provided_ratio_y = required_legs_y * tie_area / (spacing * core_height)
    confinement_pass = (
        _meets(provided_ratio_x, required_ratio)
        and _meets(provided_ratio_y, required_ratio)
    )

    return {
        "Transverse_Type": "Rectilinear hoops and crossties",
        "Core_Dimension_mm": f"{core_width:.1f} x {core_height:.1f}",
        "Confinement_18_7_5_a": base_a,
        "Confinement_18_7_5_b": base_b,
        "Confinement_18_7_5_c": confinement_expressions.get(
            "18.7.5(c)", np.nan
        ),
        "Kf": kf,
        "Kn": kn,
        "Confinement_Criteria_Governing": controlling_expression,
        "Required_Ash_s_Ratio_X": required_ratio,
        "Required_Ash_s_Ratio_Y": required_ratio,
        "Provided_Ash_s_Ratio_X": provided_ratio_x,
        "Provided_Ash_s_Ratio_Y": provided_ratio_y,
        "Required_Confinement_Ratio_X": required_ratio,
        "Required_Confinement_Ratio_Y": required_ratio,
        "Provided_Confinement_Ratio_X": provided_ratio_x,
        "Provided_Confinement_Ratio_Y": provided_ratio_y,
        "Required_Confinement_Ratio": required_ratio,
        "Provided_Confinement_Ratio": min(provided_ratio_x, provided_ratio_y),
        "Confinement_Check": "PASS" if confinement_pass else "FAIL",
        "Transverse_Spacing_Provided_mm": spacing,
        "Transverse_Spacing_Check": (
            "PASS"
            if spacing <= spacing_limit + 1e-8
            and spacing <= seismic.spacing_dimension_fraction * short_dimension + 1e-8
            and spacing <= seismic.spacing_bar_multiple * engine.dmain + 1e-8
            and spacing <= so + 1e-8
            else "FAIL"
        ),
        "High_Axial_or_High_fc_Check": (
            "APPLIED" if high_axial_or_high_strength else "NOT APPLIED"
        ),
        "Hx_Limit_mm": hx_limit,
        "Hx_Assumed_mm": hx,
        "Transverse_Legs_Per_Direction": max(required_legs_x, required_legs_y),
        # Legs counted along the X edge run parallel to Y and confine the core
        # width; legs counted along the Y edge confine the core depth.
        "Required_Legs_X": required_legs_x,
        "Required_Legs_Y": required_legs_y,
        "Alternating_Support_Check": "Pending post-check",
    }


def _alternating_support_legs(
    engine: ColumnFlexureDesign, bundle_layout: list[tuple[float, float, int]]
) -> tuple[int, int]:
    """Tie legs needed along the X edge and along the Y edge to support alternate bars.

    The two hoop legs hold the corner bars; every second bar position between
    them needs a crosstie leg. A circular column has a continuous spiral instead.
    """
    if engine.shape == "circular":
        return 1, 1
    positions = [(x, y) for x, y, _ in bundle_layout]
    min_x, max_x = min(x for x, _ in positions), max(x for x, _ in positions)
    min_y, max_y = min(y for _, y in positions), max(y for _, y in positions)
    tolerance = 1e-6
    bars_along_x_edge = max(
        len({round(x, 5) for x, y in positions if abs(y - face) <= tolerance})
        for face in (min_y, max_y)
    )
    bars_along_y_edge = max(
        len({round(y, 5) for x, y in positions if abs(x - face) <= tolerance})
        for face in (min_x, max_x)
    )
    return (
        max(2, 2 + math.ceil(max(0, bars_along_x_edge - 2) / 2)),
        max(2, 2 + math.ceil(max(0, bars_along_y_edge - 2) / 2)),
    )


def _bars_per_edge(
    engine: ColumnFlexureDesign, bundle_layout: list[tuple[float, float, int]]
) -> tuple[int | None, int | None]:
    """Number of bars on one X edge and on one Y edge (corner bars count on both)."""
    if engine.shape == "circular":
        return None, None
    min_x = min(x for x, _, _ in bundle_layout)
    min_y = min(y for _, y, _ in bundle_layout)
    return (
        sum(count for x, y, count in bundle_layout if abs(y - min_y) <= 1e-6),
        sum(count for x, y, count in bundle_layout if abs(x - min_x) <= 1e-6),
    )


def _post_check_alternating_support(
    engine: ColumnFlexureDesign,
    bundle_layout: list[tuple[float, float, int]],
    provided_legs: int,
) -> tuple[int, int, str]:
    """Add transverse legs as needed to support alternate perimeter bar groups."""
    if engine.shape == "circular":
        return provided_legs, 0, "N/A for continuous spiral"

    required_legs = max(_alternating_support_legs(engine, bundle_layout))
    final_legs = max(provided_legs, required_legs)
    added_legs = final_legs - provided_legs
    status = (
        f"PASS - {final_legs} legs support alternating perimeter bar groups"
        if added_legs == 0
        else (
            f"PASS - increased transverse legs by {added_legs} "
            f"to {final_legs} for alternating support"
        )
    )
    return final_legs, added_legs, status
