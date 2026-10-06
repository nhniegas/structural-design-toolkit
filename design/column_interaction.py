"""Biaxial P-Mx-My interaction surface of a column section, built once per layout.

The surface is the ACI 318M-14 design surface (phi Pn, phi Mnx, phi Mny):

* strain compatibility, concrete ultimate strain 0.003, Whitney rectangular
  stress block (22.2.2), elastic-plastic bars (the same model as the
  column designer's exact solver, ``_fast_rectangular_block_capacity``);
* phi from the extreme tension strain of each point (Table 21.2.2), tied or
  spiral;
* phi Pn capped at phi 0.80 Po (tied) or phi 0.85 Po (spiral) (Table 22.4.2.1).

It is computed on a grid of neutral-axis angles x neutral-axis depths, all at
once with NumPy (closed-form clipping of the convex rings), and cached by layout (shape,
size, bars, cover, materials), so every column, combination and iteration with
the same layout reuses it.

A demand (Pu, Mux, Muy) is checked by cutting the surface at phi Pn = Pu (the
load contour) and taking the capacity along the demand's moment direction:
utilization = |Mu| / capacity. The surface is convex, so a set of demands is
inside it exactly when the vertices of their convex hull are: the bar layout
search checks only those (``hull_vertices``).

Moments follow the designer's convention: a neutral-axis angle theta gives the
moment vector (Mx, My) ~ (cos theta, -sin theta); ETABS M3 maps to Mx and M2 to
-My (``demand_moments``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FIGURE_REPORT_DPI = 110     # the surface figure in the calculation report
FIGURE_JPEG_QUALITY = 80
N_ANGLES = 72        # neutral-axis angles over 360 degrees (5 degrees)
N_DEPTHS = 120       # neutral-axis depths per angle (chords of a convex surface: conservative, ~0.1 %)
_CACHE: dict[tuple, "InteractionSurface | None"] = {}


@dataclass
class InteractionSurface:
    """Design surface on an (angle x depth) grid; compression positive, N and N-mm."""

    p: np.ndarray        # phi Pn            (n_angles, n_depths)
    mx: np.ndarray       # phi Mnx
    my: np.ndarray       # phi Mny
    phi: np.ndarray      # phi of each point
    p_cap: float         # phi Pn,max (0.80 / 0.85 Po)
    p_tension: float     # phi Tn (all bars yielding in tension), positive number

    def _rows(self, nominal: bool):
        """(P rows made non-decreasing along the depth, Mx, My, phi), cached."""
        cache = self.__dict__.setdefault("_row_cache", {})
        if nominal not in cache:
            if nominal:
                p, x, y = self.p / self.phi, self.mx / self.phi, self.my / self.phi
                phi = np.ones_like(p)
            else:
                p, x, y, phi = self.p, self.mx, self.my, self.phi
            cache[nominal] = (np.maximum.accumulate(p, axis=1), x, y, phi)
        return cache[nominal]

    def _contours(self, levels: np.ndarray, nominal: bool = False):
        """Load contours at the axial ``levels`` (one row each, one point per angle):
        Mx, My, phi of shape (n_levels, n_angles) and the levels inside the range."""
        p_rows, x, y, phi = self._rows(nominal)
        levels = np.asarray(levels, dtype=float)
        n = p_rows.shape[0]
        cx = np.empty((len(levels), n))
        cy = np.empty_like(cx)
        cphi = np.empty_like(cx)
        for k in range(n):
            cx[:, k] = np.interp(levels, p_rows[k], x[k])
            cy[:, k] = np.interp(levels, p_rows[k], y[k])
            cphi[:, k] = np.interp(levels, p_rows[k], phi[k])
        valid = (levels >= p_rows[:, 0].max()) & (levels <= p_rows[:, -1].min())
        if not nominal:
            valid &= (levels <= self.p_cap) & (levels >= -self.p_tension)
        return cx, cy, cphi, valid

    def capacities(self, pu, mx, my) -> tuple[np.ndarray, np.ndarray]:
        """``capacity`` of many demands at once (arrays of Pu, Mx, My)."""
        pu, mx, my = (np.atleast_1d(np.asarray(a, dtype=float)) for a in (pu, mx, my))
        cx, cy, cphi, valid = self._contours(pu)
        direction = np.where((mx != 0) | (my != 0), np.arctan2(my, mx), 0.0)
        capacity, phi = _ray_capacities(cx, cy, cphi, direction)
        return np.where(valid, capacity, 0.0), np.where(valid, phi, np.nan)

    def contour(self, pu: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Mx, My and phi of the load contour at phi Pn = pu (one point per angle);
        None when pu is outside the axial range of the section."""
        cx, cy, cphi, valid = self._contours(np.array([pu]))
        return (cx[0], cy[0], cphi[0]) if valid[0] else None

    def capacity(self, pu: float, mx: float, my: float) -> tuple[float, float]:
        """(phi Mn, phi) along the direction of (mx, my) at phi Pn = pu; (0, nan)
        when pu is outside the axial range."""
        capacity, phi = self.capacities(pu, mx, my)
        return float(capacity[0]), float(phi[0])

    def nominal_capacities(self, pn, mx, my) -> np.ndarray:
        """``nominal_capacity`` of many axial loads and directions at once."""
        pn, mx, my = (np.atleast_1d(np.asarray(a, dtype=float)) for a in (pn, mx, my))
        cx, cy, cphi, valid = self._contours(pn, nominal=True)
        direction = np.where((mx != 0) | (my != 0), np.arctan2(my, mx), 0.0)
        capacity, _ = _ray_capacities(cx, cy, cphi, direction)
        return np.where(valid, capacity, 0.0)

    def nominal_capacity(self, pn: float, mx: float, my: float) -> float:
        """Nominal Mn (phi = 1) along the direction of (mx, my) at the axial load pn,
        as the strong column - weak beam check uses it (ACI 18.7.3.2); 0 when pn is
        outside the nominal axial range."""
        return float(self.nominal_capacities(pn, mx, my)[0])

    def nominal_curve(self, mx: float, my: float, levels: int = 600):
        """(P, Mn) table of the nominal strength along one moment direction, built once
        per direction; a lookup is then a 1D interpolation."""
        cache = self.__dict__.setdefault("_curves", {})
        direction = round(math.degrees(math.atan2(my, mx)) if (mx or my) else 0.0, 2)
        if direction not in cache:
            p_rows = self._rows(True)[0]
            axial = np.linspace(float(p_rows[:, 0].max()), float(p_rows[:, -1].min()), levels)
            radians = math.radians(direction)
            moments = self.nominal_capacities(axial, np.full(levels, math.cos(radians)),
                                              np.full(levels, math.sin(radians)))
            cache[direction] = (axial, moments)
        return cache[direction]

    def nominal_capacity_fast(self, pn: float, mx: float, my: float) -> float:
        """``nominal_capacity`` from the direction's (P, Mn) table."""
        axial, moments = self.nominal_curve(mx, my)
        if pn < axial[0] or pn > axial[-1]:
            return self.nominal_capacity(pn, mx, my)
        return float(np.interp(pn, axial, moments))

    def utilizations(self, pu, mx, my) -> np.ndarray:
        """|Mu| / phi Mn of many demands at once (``utilization``)."""
        pu, mx, my = (np.atleast_1d(np.asarray(a, dtype=float)) for a in (pu, mx, my))
        demand = np.hypot(mx, my)
        capacity, _ = self.capacities(pu, mx, my)
        inside = (pu >= -self.p_tension) & (pu <= self.p_cap)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(capacity > 0, demand / capacity, math.inf)
        return np.where(demand <= 1e-9, np.where(inside, 0.0, math.inf), ratio)

    def utilization(self, pu: float, mx: float, my: float) -> float:
        return float(self.utilizations(pu, mx, my)[0])


def _ray_capacities(cx: np.ndarray, cy: np.ndarray, cphi: np.ndarray,
                    direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance from the origin to each contour polygon (rows of cx, cy) along its
    ``direction``, and phi interpolated at that point."""
    angles = np.arctan2(cy, cx)
    order = np.argsort(angles, axis=1)
    angles, cx, cy, cphi = (np.take_along_axis(a, order, axis=1)
                            for a in (angles, cx, cy, cphi))
    # close each polygon across +-pi
    angles = np.concatenate([angles[:, -1:] - 2 * math.pi, angles,
                             angles[:, :1] + 2 * math.pi], axis=1)
    cx, cy, cphi = (np.concatenate([a[:, -1:], a, a[:, :1]], axis=1) for a in (cx, cy, cphi))
    j = (angles < direction[:, None]).sum(axis=1)          # searchsorted, row by row
    j = np.clip(j, 1, angles.shape[1] - 1)
    rows = np.arange(len(j))
    ax, ay, bx, by = cx[rows, j - 1], cy[rows, j - 1], cx[rows, j], cy[rows, j]
    ux, uy = np.cos(direction), np.sin(direction)
    # A + s (B - A) = t u  ->  solve for t (the capacity) and s (for phi)
    det = (bx - ax) * (-uy) - (by - ay) * (-ux)
    flat = np.abs(det) < 1e-12
    safe = np.where(flat, 1.0, det)
    s = np.where(flat, 0.0, ((-ax) * (-uy) - (-ay) * (-ux)) / safe)
    t = np.where(flat, np.hypot(ax, ay), ((bx - ax) * (-ay) - (by - ay) * (-ax)) / safe)
    s = np.clip(s, 0.0, 1.0)
    phi = cphi[rows, j - 1] + s * (cphi[rows, j] - cphi[rows, j - 1])
    return np.maximum(t, 0.0), phi


def _ray_capacity(cx: np.ndarray, cy: np.ndarray, cphi: np.ndarray,
                  direction: float) -> tuple[float, float]:
    """Distance from the origin to the contour polygon along ``direction``."""
    t, phi = _ray_capacities(cx[None, :], cy[None, :], cphi[None, :], np.array([direction]))
    return float(t[0]), float(phi[0])


def demand_moments(m2: float, m3: float) -> tuple[float, float]:
    """Section moments (Mx, My) of ETABS local M2, M3 (see the module docstring)."""
    return m3, -m2


def hull_vertices(points: np.ndarray) -> np.ndarray:
    """Indices of the convex hull vertices of (P, Mx, My) demand points; all
    indices when the hull cannot be formed (too few or coplanar points)."""
    n = len(points)
    if n <= 4:
        return np.arange(n)
    from scipy.spatial import ConvexHull, QhullError

    span = np.ptp(points, axis=0)
    span[span <= 0] = 1.0
    try:
        return np.unique(ConvexHull(points / span).vertices)
    except (QhullError, ValueError):
        pass
    # coplanar (for example no M2 at all): the 2D hull in the varying axes
    varying = [i for i in range(points.shape[1]) if np.ptp(points[:, i]) > 0]
    if len(varying) >= 2:
        try:
            return np.unique(ConvexHull((points / span)[:, varying]).vertices)
        except (QhullError, ValueError):
            pass
    return np.arange(n)


# =============================================================================
# BUILD
# =============================================================================
def section_rings(geom) -> list[tuple[np.ndarray, float]]:
    """Counter-clockwise vertex arrays of a concrete polygon: the outline with sign +1
    and each hole (the bars cut out of the concrete) with sign -1. Every ring of a
    column section is convex (rectangle, regular polygon, bar polygons)."""
    rings = [(geom.exterior, 1.0)] + [(ring, -1.0) for ring in geom.interiors]
    out = []
    for ring, sign in rings:
        points = np.asarray(ring.coords, dtype=float)[:-1]
        x, y = points[:, 0], points[:, 1]
        if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:  # clockwise: reverse
            points = points[::-1]
        out.append((points, sign))
    return out


def clip_moments(rings, nx, ny, boundary):
    """Area and first moments (A, Sx = sum x dA, Sy = sum y dA) of the part of the
    section with nx*x + ny*y >= boundary, for arrays of half-planes at once.

    ``nx``, ``ny`` and ``boundary`` broadcast to one shape; the results have that
    shape. Each convex ring is clipped in closed form (Green's theorem over the
    kept edges and the cut), which gives the same values as polygon clipping
    without building a polygon per half-plane.
    """
    nx, ny, boundary = np.broadcast_arrays(*(np.asarray(a, dtype=float)
                                             for a in (nx, ny, boundary)))
    area = np.zeros(nx.shape)
    sx = np.zeros(nx.shape)
    sy = np.zeros(nx.shape)
    for points, sign in rings:
        x0, y0 = points[:, 0], points[:, 1]
        x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
        d0 = nx[..., None] * x0 + ny[..., None] * y0 - boundary[..., None]
        d1 = np.roll(d0, -1, axis=-1)
        in0, in1 = d0 >= 0.0, d1 >= 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(in0 != in1, d0 / (d0 - d1), 0.0)
        cx, cy = x0 + t * (x1 - x0), y0 + t * (y1 - y0)   # crossing of each edge
        ax, ay = np.where(in0, x0, cx), np.where(in0, y0, cy)
        bx, by = np.where(in1, x1, cx), np.where(in1, y1, cy)
        keep = in0 | in1
        # the cut closes the kept chain: from the exit crossing to the entry crossing
        exit_, entry = in0 & ~in1, ~in0 & in1
        ex, ey = (cx * exit_).sum(-1), (cy * exit_).sum(-1)
        nx_, ny_ = (cx * entry).sum(-1), (cy * entry).sum(-1)
        cross = np.where(keep, ax * by - bx * ay, 0.0)
        cut = ex * ny_ - nx_ * ey
        area += sign * (cross.sum(-1) + cut) / 2.0
        sx += sign * (((ax + bx) * cross).sum(-1) + (ex + nx_) * cut) / 6.0
        sy += sign * (((ay + by) * cross).sum(-1) + (ey + ny_) * cut) / 6.0
    return area, sx, sy


def _phi(strain: np.ndarray, fy: float, es: float, spiral: bool, strength) -> np.ndarray:
    """Vector form of AciCode.phi_flexure (Table 21.2.2)."""
    low = strength.compression_spiral if spiral else strength.compression_tied
    yield_strain = fy / es
    fraction = (strain - yield_strain) / (strength.tension_controlled_strain - yield_strain)
    return np.clip(low + (strength.tension_controlled - low) * fraction, low,
                   strength.tension_controlled)


def _section_data(section) -> dict | None:
    """Rings, bars and material constants of a concreteproperties section (rectangular
    block, lumped elastic-plastic bars), or None for any other section."""
    from concreteproperties.stress_strain_profile import (
        RectangularStressBlock,
        SteelElasticPlastic,
    )

    from design.column_designer_aci318 import _concrete_section_for

    section = _concrete_section_for(section)
    if (len(section.meshed_geometries) != 1 or section.reinf_geometries_meshed
            or section.strand_geometries or not section.reinf_geometries_lumped):
        return None
    concrete = section.meshed_geometries[0]
    profile = getattr(concrete.material, "ultimate_stress_strain_profile", None)
    if not isinstance(profile, RectangularStressBlock):
        return None
    bars = section.reinf_geometries_lumped
    if any(not isinstance(b.material.stress_strain_profile, SteelElasticPlastic) for b in bars):
        return None
    steel = bars[0].material.stress_strain_profile
    eps_cu = float(profile.ultimate_strain)
    return {
        "rings": section_rings(concrete.geom),
        "vertices": np.asarray(section.compound_geometry.points, dtype=float),
        "bar_xy": np.asarray([b.calculate_centroid() for b in bars], dtype=float),
        "bar_area": np.asarray([b.calculate_area() for b in bars], dtype=float),
        "steel_strains": np.asarray(steel.strains, dtype=float),
        "steel_stresses": np.asarray(steel.stresses, dtype=float),
        "stress_block": float(profile.stresses[2]),
        "eps_cu": eps_cu,
        "block_ratio": 1.0 - float(profile.strains[1]) / eps_cu,   # a = beta1 c
        "centroid": tuple(map(float, section.moment_centroid)),
    }


def _bar_hole(area: float, n: int = 4) -> np.ndarray:
    """The polygon concreteproperties cuts out for a bar of ``area`` (``add_bar``: a
    regular ``n``-gon of that area, first vertex on the +x axis), centred at 0."""
    side = 2.0 * math.sqrt(area / n) * math.sqrt(math.tan(math.pi / n))
    radius = math.hypot(side / (2.0 * math.tan(math.pi / n)), side / 2.0)
    angles = 2.0 * math.pi * np.arange(n) / n
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])


def layout_section_data(engine, bar_layout) -> dict:
    """The same data as ``_section_data``, straight from the engine and the bar layout
    ``[(x, y, bars), ...]``, without building (meshing) a concreteproperties section.
    The concrete and the bars are those of ``ColumnFlexureDesign.define_materials``,
    ``define_section`` and ``add_reinf``."""
    code = engine.code
    material = code.material
    if engine.shape == "circular":
        angles = 2.0 * math.pi * np.arange(20) / 20     # sectionproperties circular_section
        outline = np.column_stack([engine.diameter / 2 * np.cos(angles),
                                   engine.diameter / 2 * np.sin(angles)])
        centroid = (0.0, 0.0)
    else:
        outline = np.array([(0.0, 0.0), (engine.width, 0.0), (engine.width, engine.height),
                            (0.0, engine.height)])
        centroid = (engine.width / 2.0, engine.height / 2.0)
    bar = math.pi * engine.dmain ** 2 / 4.0
    bar_xy = np.array([(x, y) for x, y, _ in bar_layout], dtype=float)
    bar_area = np.array([bar * count for _, _, count in bar_layout], dtype=float)
    rings = [(outline, 1.0)] + [(_bar_hole(a) + xy, -1.0) for xy, a in zip(bar_xy, bar_area)]
    fy, es = engine.fy, material.steel_elastic_modulus
    fracture = material.steel_fracture_strain
    return {
        "rings": rings,
        "vertices": np.vstack([ring for ring, _ in rings]),
        "bar_xy": bar_xy,
        "bar_area": bar_area,
        "steel_strains": np.array([-fracture, -fy / es, 0.0, fy / es, fracture]),
        "steel_stresses": np.array([-fy, -fy, 0.0, fy, fy]),
        "stress_block": material.stress_block_alpha * engine.fc,
        "eps_cu": material.concrete_ultimate_strain,
        "block_ratio": code.beta1(engine.fc),
        "centroid": centroid,
    }


def build_surface(section, engine, n_angles: int = N_ANGLES,
                  n_depths: int = N_DEPTHS, data: dict | None = None
                  ) -> InteractionSurface | None:
    """The design surface of a concreteproperties section (rectangular block, lumped
    elastic-plastic bars), or None for other sections (the exact solver is used).
    ``data`` (``layout_section_data``) replaces the section when given."""
    if data is None:
        data = _section_data(section)
        if data is None:
            return None
    code = engine.code
    vertices, bar_xy, bar_area = data["vertices"], data["bar_xy"], data["bar_area"]
    steel_strains, steel_stresses = data["steel_strains"], data["steel_stresses"]
    stress_block, eps_cu, block_ratio = data["stress_block"], data["eps_cu"], data["block_ratio"]
    xc, yc = data["centroid"]

    theta = np.linspace(-math.pi, math.pi, n_angles, endpoint=False)
    normal = np.stack([-np.sin(theta), np.cos(theta)], axis=1)        # (n_angles, 2)
    v = vertices @ normal.T                                           # (n_vertices, n_angles)
    v_max, v_min = v.max(axis=0), v.min(axis=0)
    depth = v_max - v_min
    # neutral-axis depths: from deep tension to beyond full compression
    # dense where the interaction curve bends (around the balance point up to full
    # compression), coarse in deep tension and beyond the section depth
    n_tension = n_depths // 4
    n_tail = n_depths // 8
    fractions = np.concatenate([np.geomspace(0.01, 0.25, n_tension, endpoint=False),
                                np.linspace(0.25, 1.4, n_depths - n_tension - n_tail,
                                            endpoint=False),
                                np.geomspace(1.4, 4.0, n_tail)])
    c = depth[:, None] * fractions[None, :]                           # (n_angles, n_depths)

    # concrete: the section clipped by the compression half-plane of every grid point
    boundary = v_max[:, None] - block_ratio * c
    area, s_x, s_y = clip_moments(data["rings"], normal[:, 0, None],
                                  normal[:, 1, None], boundary)
    pn = area * stress_block
    mx = stress_block * (s_y - area * yc)
    my = stress_block * (s_x - area * xc)

    # bars: strain compatibility
    bar_v = bar_xy @ normal.T                                         # (n_bars, n_angles)
    na_v = v_max[:, None] - c                                         # (n_angles, n_depths)
    strains = eps_cu * (bar_v.T[:, None, :] - na_v[:, :, None]) / c[:, :, None]
    stresses = np.interp(strains, steel_strains, steel_stresses)
    low, high = strains < steel_strains[0], strains > steel_strains[-1]
    slope_low = (steel_stresses[1] - steel_stresses[0]) / (steel_strains[1] - steel_strains[0])
    slope_high = (steel_stresses[-1] - steel_stresses[-2]) / (steel_strains[-1] - steel_strains[-2])
    stresses = np.where(low, steel_stresses[0] + (strains - steel_strains[0]) * slope_low, stresses)
    stresses = np.where(high, steel_stresses[-1] + (strains - steel_strains[-1]) * slope_high,
                        stresses)
    forces = stresses * bar_area
    pn += forces.sum(axis=2)
    mx += (forces * (bar_xy[:, 1] - yc)).sum(axis=2)
    my += (forces * (bar_xy[:, 0] - xc)).sum(axis=2)

    # phi from the extreme tension strain, and the axial limits
    tension_strain = np.maximum(0.0, -strains.min(axis=2))
    spiral = engine.shape == "circular"
    phi = _phi(tension_strain, engine.fy, code.material.steel_elastic_modulus, spiral,
               code.strength)
    gross = engine.width * engine.height if engine.shape == "rectangular" \
        else math.pi * engine.diameter ** 2 / 4
    steel_area = float(bar_area.sum())
    po = code.material.stress_block_alpha * engine.fc * (gross - steel_area) \
        + engine.fy * steel_area
    cap_factor = code.column_strength.pn_max_factor_spiral if spiral \
        else code.column_strength.pn_max_factor_tied
    phi_axial = code.strength.compression_spiral if spiral else code.strength.compression_tied
    p_cap = phi_axial * cap_factor * po
    p_tension = code.strength.tension_controlled * engine.fy * steel_area
    return InteractionSurface(phi * pn, phi * mx, phi * my, phi, p_cap, p_tension)


def layout_key(engine, bar_layout) -> tuple:
    """What the surface depends on: shape, size, cover, bars and materials."""
    return (engine.shape, round(engine.width, 3), round(engine.height, 3),
            round(engine.diameter, 3), round(engine.cc, 3), round(engine.dmain, 3),
            round(engine.dties, 3), round(engine.fc, 4), round(engine.fy, 4),
            tuple(tuple(round(float(x), 3) for x in item) for item in (bar_layout or ())))


def surface_for(engine, section, bar_layout) -> InteractionSurface | None:
    """The cached surface of a layout (built on first use)."""
    key = layout_key(engine, bar_layout)
    if key not in _CACHE:
        built = getattr(section, "is_built", True)
        if bar_layout and not built:   # a lazy section: no need to mesh it for this
            _CACHE[key] = build_surface(None, engine,
                                        data=layout_section_data(engine, bar_layout))
        else:
            _CACHE[key] = build_surface(section, engine)
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()


# =============================================================================
# 3D PLOT (calculation report)
# =============================================================================
def plot_surface(surface: InteractionSurface, demands: np.ndarray, path: str, title: str = "",
                 hull: np.ndarray | None = None, governing: int | None = None) -> str:
    """Save a 3D view of the design surface with the demands to ``path`` (PNG).

    ``demands`` holds (Pu, Mx, My) rows in N and N-mm; ``hull`` the indices of
    the hull vertices and ``governing`` the governing demand. Axes in kN, kN-m.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = np.minimum(surface.p, surface.p_cap) / 1e3
    mx, my = surface.mx / 1e6, surface.my / 1e6
    closed = np.append(np.arange(p.shape[0]), 0)  # wrap the angles around
    figure = plt.figure(figsize=(6.4, 5.0), dpi=150)
    axes = figure.add_subplot(projection="3d")
    axes.plot_surface(mx[closed], my[closed], p[closed], alpha=0.25, color="tab:blue",
                      linewidth=0.2, edgecolor="tab:blue", rstride=2, cstride=3)
    if len(demands):
        d = np.asarray(demands, dtype=float)
        axes.scatter(d[:, 1] / 1e6, d[:, 2] / 1e6, d[:, 0] / 1e3, s=4, color="0.45",
                     label="Demands")
        if hull is not None and len(hull):
            h = d[np.asarray(hull, dtype=int)]
            axes.scatter(h[:, 1] / 1e6, h[:, 2] / 1e6, h[:, 0] / 1e3, s=14, color="tab:orange",
                         label="Hull vertices (checked)")
        if governing is not None:
            g = d[int(governing)]
            axes.scatter([g[1] / 1e6], [g[2] / 1e6], [g[0] / 1e3], s=60, color="tab:red",
                         marker="*", label="Governing")
    axes.set_xlabel(r"$\phi M_{nx}$ (kN-m)")
    axes.set_ylabel(r"$\phi M_{ny}$ (kN-m)")
    axes.set_zlabel(r"$\phi P_n$ (kN)")
    axes.view_init(elev=18, azim=-55)
    if title:
        axes.set_title(title, fontsize=9)
    axes.legend(loc="upper left", fontsize=7)
    figure.tight_layout()
    if str(path).lower().endswith((".jpg", ".jpeg")):
        # a report of hundreds of columns holds one of these each: a JPEG at this
        # size is about a quarter of the PNG, with the text still sharp on the page
        figure.savefig(path, dpi=FIGURE_REPORT_DPI, pil_kwargs={"quality": FIGURE_JPEG_QUALITY})
    else:
        figure.savefig(path)
    plt.close(figure)
    return path
