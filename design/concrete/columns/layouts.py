"""Bar layouts of a column section, with bundled bars, that fit its faces."""

import math
import numpy as np
import pandas as pd
from design.criteria.code_config import CODE, AciCode
from design.concrete.columns.section import (
    ColumnFlexureDesign,
    LazyColumnSection,
)
from design.concrete.columns.tables import (
    _numeric,
)


def _build_column_section(
    row: pd.Series,
    n_bars: int,
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    bundle_layout: list[tuple[float, float, int]] | None = None,
) -> tuple[ColumnFlexureDesign, object]:
    """Create a column engine and reinforced geometry for a frame-data row.

    A row with a ``DesignCover`` value uses it in place of ``cover``.
    """
    member = str(row["UniqueName"])
    own_cover = row.get("DesignCover")
    if own_cover is not None and pd.notna(own_cover):
        cover = float(own_cover)
    diameter = row.get("Diameter")
    is_circular = pd.notna(diameter) and float(diameter) > 0
    width = 0.0 if is_circular else _numeric(row["Width"], "Width", member)
    height = 0.0 if is_circular else _numeric(row["Depth"], "Depth", member)
    diameter_value = _numeric(diameter, "Diameter", member) if is_circular else 0.0

    # The section depends only on its size, materials, bars and cover: build each
    # one once (meshing is slow) and share it between members, joints and checks.
    key = (width, height, diameter_value, _numeric(row["f'c"], "f'c", member),
           _numeric(row["fy"], "fy", member), _numeric(row["fys"], "fys", member),
           float(dmain), float(dties), float(cover), bool(is_smrf), int(n_bars),
           tuple(tuple(float(x) for x in item) for item in (bundle_layout or ())))
    if key not in _SECTION_CACHE:
        _SECTION_CACHE[key] = _new_column_section(
            width, height, diameter_value, is_circular, row, member, n_bars, dmain, dties,
            cover, is_smrf, bundle_layout)
    return _SECTION_CACHE[key]


_SECTION_CACHE: dict[tuple, tuple] = {}


def _new_column_section(width, height, diameter_value, is_circular, row, member, n_bars,
                        dmain, dties, cover, is_smrf, bundle_layout):
    engine = ColumnFlexureDesign(
        width=width,
        height=height,
        diameter=diameter_value,
        fc=_numeric(row["f'c"], "f'c", member),
        fy=_numeric(row["fy"], "fy", member),
        fyt=_numeric(row["fys"], "fys", member),
        dmain=dmain,
        dties=dties,
        cc=cover,
        shape="circular" if is_circular else "rectangular",
        is_smrf=is_smrf,
    )
    return engine, LazyColumnSection(engine, n_bars, bundle_layout)


def _perimeter_bar_positions(
    engine: ColumnFlexureDesign, nx: int, ny: int
) -> list[tuple[float, float]]:
    """Return unique, symmetric rectangular perimeter bar-center positions."""
    offset = engine.cc + engine.dties + engine.dmain / 2.0
    span_x = engine.width - 2.0 * offset
    span_y = engine.height - 2.0 * offset
    if min(span_x, span_y) <= 0:
        raise ValueError("Column core dimensions must be positive for bar placement.")

    positions = []
    # Include each corner once. The previous four independent loops repeated
    # every corner, which made the DXF appear to double-count corner bars.
    for index in range(nx + 1):
        positions.append((offset + index * span_x / nx, offset))
    for index in range(1, ny + 1):
        positions.append((offset + span_x, offset + index * span_y / ny))
    for index in range(nx - 1, -1, -1):
        positions.append((offset + index * span_x / nx, offset + span_y))
    for index in range(ny - 1, 0, -1):
        positions.append((offset, offset + index * span_y / ny))
    return positions


def _symmetric_position_groups(
    positions: list[tuple[float, float]], width: float, height: float
) -> list[list[int]]:
    """Group positions into mirror-symmetric tiers, corners before interior bars."""
    scale = 6
    index_by_point = {
        (round(x, scale), round(y, scale)): index
        for index, (x, y) in enumerate(positions)
    }
    unseen = set(range(len(positions)))
    groups = []
    while unseen:
        index = min(unseen)
        x, y = positions[index]
        orbit = {
            index_by_point[(round(xv, scale), round(yv, scale))]
            for xv in (x, width - x)
            for yv in (y, height - y)
            if (round(xv, scale), round(yv, scale)) in index_by_point
        }
        unseen.difference_update(orbit)
        groups.append(sorted(orbit))
    two_position_groups = [group for group in groups if len(group) == 2]
    groups = [group for group in groups if len(group) != 2]
    for index in range(0, len(two_position_groups), 2):
        paired_groups = two_position_groups[index : index + 2]
        groups.append(sorted(position for group in paired_groups for position in group))
    return sorted(
        groups,
        key=lambda group: (
            min(
                min(positions[i][0], width - positions[i][0])
                + min(positions[i][1], height - positions[i][1])
                for i in group
            ),
            min(group),
        ),
    )


def _bundle_spacing_is_valid(
    positions: list[tuple[float, float]],
    bundle_counts: list[int],
    bar_diameter: float,
    aggregate_size: float | None = None,
) -> bool:
    """Check clear distances between equivalent circular bar-bundle envelopes.

    Column bars need a clear spacing of at least 40 mm, 1.5 db and 4/3 of the
    aggregate (NSCP 425.2.3 / ACI 25.2.3); a bundle counts as one bar of the
    same area (NSCP 425.6.1.6; ACI 25.6.1.6).
    """
    if aggregate_size is None:
        aggregate_size = CODE.column_strength.default_aggregate_size
    equivalent_diameters = np.array([bar_diameter * math.sqrt(count) for count in bundle_counts])
    points = np.asarray(positions, dtype=float).reshape(-1, 2)
    if len(points) < 2:
        return True
    distance = np.hypot(points[:, None, 0] - points[None, :, 0],
                        points[:, None, 1] - points[None, :, 1])
    mean_diameter = (equivalent_diameters[:, None] + equivalent_diameters[None, :]) / 2.0
    minimum_clear = _column_clear_spacing(mean_diameter, aggregate_size)
    upper = np.triu_indices(len(points), 1)
    return bool(np.all(distance[upper] + 1e-8 >= (mean_diameter + minimum_clear)[upper]))


def _column_clear_spacing(bar_diameter, aggregate_size: float, code: AciCode = CODE):
    """Minimum clear spacing of longitudinal column bars (NSCP 425.2.3 / ACI 25.2.3)."""
    strength = code.column_strength
    return np.maximum(
        np.maximum(strength.min_clear_spacing, strength.clear_spacing_bar_multiple * bar_diameter),
        code.beam_detailing.aggregate_spacing_factor * aggregate_size,
    )


def _enumerate_column_bar_layouts(
    engine: ColumnFlexureDesign,
    max_bars: int,
    max_spacing: float | None = None,
    max_bundle_size: int | None = None,
    aggregate_size: float | None = None,
) -> list[list[tuple[float, float, int]]]:
    """Enumerate symmetric perimeter layouts, then bundle symmetric bar tiers.

    Bar bundles are limited to four bars, consistent with the usual ACI bundle
    limit. Rectangular tiers begin at the corners; matching reflected positions
    are incremented together, producing symmetric 4- or 8-bar additions where
    the section geometry permits. Circular layouts bundle uniformly around the
    circumference.
    """
    strength_cfg = engine.code.column_strength
    max_spacing = strength_cfg.max_longitudinal_spacing if max_spacing is None else max_spacing
    max_bundle_size = strength_cfg.max_bundle_size if max_bundle_size is None else max_bundle_size
    aggregate_size = (
        strength_cfg.default_aggregate_size if aggregate_size is None else aggregate_size
    )
    bar_diameter = engine.dmain
    layouts: dict[tuple, list[tuple[float, float, int]]] = {}

    def add_layout(positions: list[tuple[float, float]], counts: list[int]) -> None:
        """Store a candidate only when its bar count and bundle clearances pass."""
        total_bars = sum(counts)
        if total_bars > max_bars or not _bundle_spacing_is_valid(
            positions, counts, bar_diameter, aggregate_size
        ):
            return
        signature = tuple(
            sorted(
                (
                    round(x, 4),
                    round(y, 4),
                    count,
                )
                for (x, y), count in zip(positions, counts)
            )
        )
        layouts.setdefault(
            signature,
            [
                (float(x), float(y), int(count))
                for (x, y), count in zip(positions, counts)
            ],
        )

    if engine.shape == "circular":
        radius = engine.diameter / 2.0 - engine.cc - engine.dties - bar_diameter / 2.0
        if radius <= 0:
            raise ValueError("Circular column core radius must be positive.")
        circumference = 2.0 * math.pi * radius
        minimum_positions = max(
            strength_cfg.min_bars_circular if engine.is_smrf else strength_cfg.min_bars_tied,
            math.ceil(circumference / max_spacing),
        )
        for count in range(minimum_positions, max_bars + 1):
            positions = [
                (
                    radius * math.cos(2.0 * math.pi * index / count),
                    radius * math.sin(2.0 * math.pi * index / count),
                )
                for index in range(count)
            ]
            if not _bundle_spacing_is_valid(
                positions, [1] * count, bar_diameter, aggregate_size
            ):
                break
            add_layout(positions, [1] * count)
        for bundle_size in range(2, max_bundle_size + 1):
            for count in range(minimum_positions, max_bars // bundle_size + 1):
                positions = [
                    (
                        radius * math.cos(2.0 * math.pi * index / count),
                        radius * math.sin(2.0 * math.pi * index / count),
                    )
                    for index in range(count)
                ]
                add_layout(positions, [bundle_size] * count)
    else:
        offset = engine.cc + engine.dties + bar_diameter / 2.0
        span_x = engine.width - 2.0 * offset
        span_y = engine.height - 2.0 * offset
        if min(span_x, span_y) <= 0:
            raise ValueError("Column core dimensions must be positive.")
        min_nx = max(1, math.ceil(span_x / max_spacing))
        min_ny = max(1, math.ceil(span_y / max_spacing))
        min_pitch = float(_column_clear_spacing(bar_diameter, aggregate_size, engine.code)) \
            + bar_diameter
        max_nx = max(min_nx, int(span_x // min_pitch))
        max_ny = max(min_ny, int(span_y // min_pitch))
        # A square column gets the same bars on all four faces: nothing on site
        # tells its faces apart, so an unequal cage could be placed turned 90 degrees.
        is_square = abs(engine.width - engine.height) <= 1e-6

        for nx in range(min_nx, max_nx + 1):
            if span_x / nx > max_spacing + 1e-8 or span_x / nx < min_pitch - 1e-8:
                continue
            for ny in range(min_ny, max_ny + 1):
                if is_square and ny != nx:
                    continue
                if span_y / ny > max_spacing + 1e-8 or span_y / ny < min_pitch - 1e-8:
                    continue
                positions = _perimeter_bar_positions(engine, nx, ny)
                counts = [1] * len(positions)
                add_layout(positions, counts)
                tiers = _symmetric_position_groups(
                    positions, engine.width, engine.height
                )
                for bundle_size in range(2, max_bundle_size + 1):
                    stalled = False
                    for tier in tiers:
                        if len(tier) not in (4, 8):
                            stalled = True
                            break
                        updated_counts = counts.copy()
                        if any(count != bundle_size - 1 for count in (counts[i] for i in tier)):
                            continue
                        for index in tier:
                            updated_counts[index] = bundle_size
                        if not _bundle_spacing_is_valid(
                            positions,
                            updated_counts,
                            bar_diameter,
                            aggregate_size,
                        ):
                            stalled = True
                            break
                        counts = updated_counts
                        add_layout(positions, counts)
                    if stalled:
                        break

    return sorted(
        layouts.values(),
        key=lambda layout: (
            sum(count for _, _, count in layout),
            max(count for _, _, count in layout),
            len(layout),
        ),
    )


# Each member's force table, indexed once by combination: (table, {combo: (I row, J row)}).
# The table itself is kept so its id stays unique while the entry exists.
_END_FORCE_INDEX: dict[int, tuple[pd.DataFrame, dict]] = {}
