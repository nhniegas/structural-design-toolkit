"""The size trial of the design loop: whether a column size passes on the forces of the current analysis."""

import math
import pandas as pd
from design.concrete.columns.slenderness import magnify_member
from design.concrete.columns.tables import (
    _clean_table,
    _expand_combo_permutations,
    _normalize_names,
    _normalize_object_name,
    _to_compression_positive,
)
from design.concrete.columns.layouts import (
    _build_column_section,
    _enumerate_column_bar_layouts,
)
from design.concrete.columns.flexure import (
    _clear_end_force_index,
    _evaluate_column_candidate,
)
from design.concrete.columns.transverse import (
    _column_transverse_candidate_passes,
    _smrf_transverse_design,
)
from design.concrete.columns.shear import (
    _column_shear_checks,
)
from design.concrete.columns.design import (
    _slenderness_section,
)


def column_size_passes(
    frame_row: pd.Series,
    forces: pd.DataFrame,
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    slenderness_geometry: dict | None = None,
    capacity_design: bool = True,
    clear_height: float | None = None,
    beam_moment_limits: dict | None = None,
) -> tuple[bool, str]:
    """Whether a column section can be reinforced for its forces, and why not.

    ``slenderness_geometry`` is the column's lu and k per axis from the last
    design (``report.attrs["slenderness_geometry"]``): the trial section is
    then checked with its own slenderness. ``capacity_design`` False designs the
    shear for the analysis shear (a foundation-level or topmost column the
    user chose not to check for Ve). ``clear_height`` and
    ``beam_moment_limits`` are those of the last design
    (``report.attrs["clear_heights"]``, ``["beam_moment_limits"]``): the
    capacity shear Ve of a trial section is then the one the design would
    use, over the clear height and no more than the beams can deliver (ACI
    18.7.6.1.1). Without them Ve comes from the column's own probable moments
    over its whole length, which grows faster than the section's shear
    strength: no size would ever pass.

    The same member checks as ``design_columns`` that depend on the section size
    alone: a bar layout within the steel limit that passes flexure and axial load
    on the interaction surface, its transverse detailing, the SMRF dimensions and
    the column shear (the steel limit Vs <= 0.66 sqrt(fc') b d). ``forces`` is the
    member's FACTORED LOADS rows as ETABS gives them (compression negative). The
    joint checks need the beams and the columns around the joint, so they are
    left to the full design.
    """
    forces, _ = _expand_combo_permutations(_to_compression_positive(_clean_table(forces)))
    forces["UniqueName"] = _normalize_names(forces["UniqueName"])
    row = frame_row.copy()
    row["UniqueName"] = _normalize_object_name(row["UniqueName"])
    engine, _ = _build_column_section(row, 4, dmain, dties, cover, is_smrf)
    if slenderness_geometry:
        magnified = magnify_member(forces, _slenderness_section(engine), slenderness_geometry)
        if magnified.failed:
            return False, "slenderness (ACI 6.2.6)"
        forces = magnified.forces
    if is_smrf:
        seismic = engine.code.column_seismic
        sides = ((engine.diameter, engine.diameter) if engine.shape == "circular"
                 else (min(engine.width, engine.height), max(engine.width, engine.height)))
        if sides[0] < seismic.min_dimension or sides[0] / sides[1] < seismic.min_aspect_ratio:
            return False, "SMRF dimensions (ACI 18.7.2.1)"
    strength = engine.code.column_strength
    ratio_limit = strength.rho_max_smrf if is_smrf else strength.rho_max
    gross = (engine.width * engine.height if engine.shape == "rectangular"
             else math.pi * engine.diameter**2 / 4.0)
    max_bars = int(ratio_limit * gross / (math.pi * dmain**2 / 4.0))
    try:
        layouts = _enumerate_column_bar_layouts(engine, max_bars=max_bars)
    except ValueError as exc:
        return False, str(exc)
    axial = pd.to_numeric(forces["P"], errors="coerce").dropna()
    max_compression = max(0.0, float(axial.max()) * 1000.0) if not axial.empty else 0.0
    reason = f"no bar layout within the {ratio_limit:.0%} limit passes flexure and axial load"
    try:
        for layout in layouts:
            passes, _, _ = _evaluate_column_candidate(
                row, forces, layout, dmain, dties, cover, is_smrf, stop_at_first_failure=True)
            if not passes:
                continue
            bars = sum(count for _, _, count in layout)
            layout_engine, _ = _build_column_section(
                row, bars, dmain, dties, cover, is_smrf, bundle_layout=layout)
            detailing, _, _ = _column_transverse_candidate_passes(
                row, forces, layout_engine, layout, max_compression, is_smrf)
            if not detailing:
                reason = "transverse detailing"
                continue
            spacing = layout_engine.solve_max_spacing()["max_tie_spacing_mm"]
            if is_smrf:
                spacing = min(spacing, _smrf_transverse_design(
                    layout_engine, len(layout), max_compression, spacing
                )["Transverse_Spacing_Provided_mm"])
            shear, _ = _column_shear_checks(
                row, forces, layout_engine, bars, spacing, 2, is_smrf, bundle_layout=layout,
                clear_height=clear_height, beam_moment_limits=beam_moment_limits,
                capacity_design=capacity_design)
            if all(check["Shear_Check"] == "PASS" for check in shear):
                return True, "passes"
            return False, "column shear (section too small for the shear steel)"
    finally:
        _clear_end_force_index()
    return False, reason
