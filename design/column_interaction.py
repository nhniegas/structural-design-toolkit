"""Biaxial P-Mx-My interaction surface of a column section, built once per layout.

The surface is the ACI 318M-14 design surface (phi Pn, phi Mnx, phi Mny):

* strain compatibility, concrete ultimate strain 0.003, Whitney rectangular
  stress block (22.2.2), elastic-plastic bars (the same model as the
  column designer's exact solver, ``_fast_rectangular_block_capacity``);
* phi from the extreme tension strain of each point (Table 21.2.2), tied or
  spiral;
* phi Pn capped at phi 0.80 Po (tied) or phi 0.85 Po (spiral) (Table 22.4.2.1).

It is computed on a grid of neutral-axis angles x neutral-axis depths, all at
once with NumPy and vectorized shapely clipping, and cached by layout (shape,
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
import shapely

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

    def contour(self, pu: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Mx, My and phi of the load contour at phi Pn = pu (one point per angle);
        None when pu is outside the axial range of the section."""
        if pu > self.p_cap or pu < -self.p_tension:
            return None
        mx = np.empty(self.p.shape[0])
        my = np.empty_like(mx)
        phi = np.empty_like(mx)
        for k in range(self.p.shape[0]):
            p_row = np.maximum.accumulate(self.p[k])  # phi Pn grows with the depth
            if pu < p_row[0] or pu > p_row[-1]:
                return None
            mx[k] = np.interp(pu, p_row, self.mx[k])
            my[k] = np.interp(pu, p_row, self.my[k])
            phi[k] = np.interp(pu, p_row, self.phi[k])
        return mx, my, phi

    def capacity(self, pu: float, mx: float, my: float) -> tuple[float, float]:
        """(phi Mn, phi) along the direction of (mx, my) at phi Pn = pu; (0, nan)
        when pu is outside the axial range."""
        contour = self.contour(pu)
        if contour is None:
            return 0.0, math.nan
        cx, cy, cphi = contour
        direction = math.atan2(my, mx) if (mx or my) else 0.0
        return _ray_capacity(cx, cy, cphi, direction)

    def nominal_capacity(self, pn: float, mx: float, my: float) -> float:
        """Nominal Mn (phi = 1) along the direction of (mx, my) at the axial load pn,
        as the strong column - weak beam check uses it (ACI 18.7.3.2); 0 when pn is
        outside the nominal axial range."""
        p_nom, x_nom, y_nom = self.p / self.phi, self.mx / self.phi, self.my / self.phi
        cx = np.empty(p_nom.shape[0])
        cy = np.empty_like(cx)
        for k in range(p_nom.shape[0]):
            row = np.maximum.accumulate(p_nom[k])
            if pn < row[0] or pn > row[-1]:
                return 0.0
            cx[k] = np.interp(pn, row, x_nom[k])
            cy[k] = np.interp(pn, row, y_nom[k])
        direction = math.atan2(my, mx) if (mx or my) else 0.0
        capacity, _ = _ray_capacity(cx, cy, np.ones_like(cx), direction)
        return capacity

    def nominal_curve(self, mx: float, my: float, levels: int = 600):
        """(P, Mn) table of the nominal strength along one moment direction, built once
        per direction; a lookup is then a 1D interpolation."""
        cache = self.__dict__.setdefault("_curves", {})
        direction = round(math.degrees(math.atan2(my, mx)) if (mx or my) else 0.0, 2)
        if direction not in cache:
            p_nom = self.p / self.phi
            lowest = float(np.max(p_nom[:, 0]))       # every angle reaches it
            highest = float(np.min(p_nom.max(axis=1)))
            axial = np.linspace(lowest, highest, levels)
            radians = math.radians(direction)
            moments = np.array([self.nominal_capacity(p, math.cos(radians), math.sin(radians))
                                for p in axial])
            cache[direction] = (axial, moments)
        return cache[direction]

    def nominal_capacity_fast(self, pn: float, mx: float, my: float) -> float:
        """``nominal_capacity`` from the direction's (P, Mn) table."""
        axial, moments = self.nominal_curve(mx, my)
        if pn < axial[0] or pn > axial[-1]:
            return self.nominal_capacity(pn, mx, my)
        return float(np.interp(pn, axial, moments))

    def utilization(self, pu: float, mx: float, my: float) -> float:
        demand = math.hypot(mx, my)
        capacity, _ = self.capacity(pu, mx, my)
        if demand <= 1e-9:
            return 0.0 if -self.p_tension <= pu <= self.p_cap else math.inf
        return demand / capacity if capacity > 0 else math.inf


def _ray_capacity(cx: np.ndarray, cy: np.ndarray, cphi: np.ndarray,
                  direction: float) -> tuple[float, float]:
    """Distance from the origin to the contour polygon along ``direction``."""
    angles = np.arctan2(cy, cx)
    radius = np.hypot(cx, cy)
    order = np.argsort(angles)
    angles, radius, cx, cy, cphi = (a[order] for a in (angles, radius, cx, cy, cphi))
    # close the polygon across +-pi
    angles = np.concatenate([angles[-1:] - 2 * math.pi, angles, angles[:1] + 2 * math.pi])
    cx = np.concatenate([cx[-1:], cx, cx[:1]])
    cy = np.concatenate([cy[-1:], cy, cy[:1]])
    cphi = np.concatenate([cphi[-1:], cphi, cphi[:1]])
    j = int(np.searchsorted(angles, direction))
    j = min(max(j, 1), len(angles) - 1)
    ax, ay, bx, by = cx[j - 1], cy[j - 1], cx[j], cy[j]
    ux, uy = math.cos(direction), math.sin(direction)
    # A + s (B - A) = t u  ->  solve for t (the capacity) and s (for phi)
    det = (bx - ax) * (-uy) - (by - ay) * (-ux)
    if abs(det) < 1e-12:
        t = float(np.hypot(ax, ay))
        s = 0.0
    else:
        s = ((-ax) * (-uy) - (-ay) * (-ux)) / det
        t = ((bx - ax) * (-ay) - (by - ay) * (-ax)) / det
    s = min(max(s, 0.0), 1.0)
    return max(float(t), 0.0), float(cphi[j - 1] + s * (cphi[j] - cphi[j - 1]))


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
def _phi(strain: np.ndarray, fy: float, es: float, spiral: bool, strength) -> np.ndarray:
    """Vector form of AciCode.phi_flexure (Table 21.2.2)."""
    low = strength.compression_spiral if spiral else strength.compression_tied
    yield_strain = fy / es
    fraction = (strain - yield_strain) / (strength.tension_controlled_strain - yield_strain)
    return np.clip(low + (strength.tension_controlled - low) * fraction, low,
                   strength.tension_controlled)


def build_surface(section, engine, n_angles: int = N_ANGLES,
                  n_depths: int = N_DEPTHS) -> InteractionSurface | None:
    """The design surface of a concreteproperties section (rectangular block, lumped
    elastic-plastic bars), or None for other sections (the exact solver is used)."""
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

    code = engine.code
    geom = concrete.geom
    vertices = np.asarray(section.compound_geometry.points, dtype=float)
    bar_xy = np.asarray([b.calculate_centroid() for b in bars], dtype=float)
    bar_area = np.asarray([b.calculate_area() for b in bars], dtype=float)
    steel = bars[0].material.stress_strain_profile
    steel_strains = np.asarray(steel.strains, dtype=float)
    steel_stresses = np.asarray(steel.stresses, dtype=float)
    stress_block = float(profile.stresses[2])
    eps_cu = float(profile.ultimate_strain)
    block_ratio = 1.0 - float(profile.strains[1]) / eps_cu     # a = beta1 c
    xc, yc = map(float, section.moment_centroid)

    theta = np.linspace(-math.pi, math.pi, n_angles, endpoint=False)
    normal = np.stack([-np.sin(theta), np.cos(theta)], axis=1)        # (n_angles, 2)
    axis = np.stack([np.cos(theta), np.sin(theta)], axis=1)
    v = vertices @ normal.T                                           # (n_vertices, n_angles)
    u = vertices @ axis.T
    v_max, v_min = v.max(axis=0), v.min(axis=0)
    depth = v_max - v_min
    margin = np.maximum(depth, u.max(axis=0) - u.min(axis=0)) + 1.0
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

    # concrete: clip the section by the compression half-plane of every grid point
    boundary = v_max[:, None] - block_ratio * c
    big_u0 = (u.min(axis=0) - margin)[:, None] * np.ones_like(c)
    big_u1 = (u.max(axis=0) + margin)[:, None] * np.ones_like(c)
    top = (v_max + margin)[:, None] * np.ones_like(c)
    cos, sin = np.cos(theta)[:, None], np.sin(theta)[:, None]

    def to_xy(uu, vv):
        return uu * cos - vv * sin, uu * sin + vv * cos

    corners = [to_xy(big_u0, boundary), to_xy(big_u1, boundary),
               to_xy(big_u1, top), to_xy(big_u0, top)]
    ring = np.stack([np.stack(pt, axis=-1) for pt in corners + corners[:1]], axis=-2)
    halves = shapely.polygons(ring.reshape(-1, 5, 2))
    clipped = shapely.intersection(geom, halves)
    area = shapely.area(clipped).reshape(c.shape)
    centroid = shapely.centroid(clipped)
    with np.errstate(invalid="ignore"):
        cx = np.where(area > 0, shapely.get_x(centroid).reshape(c.shape), xc)
        cy = np.where(area > 0, shapely.get_y(centroid).reshape(c.shape), yc)
    force_c = area * stress_block
    pn = force_c.copy()
    mx = force_c * (cy - yc)
    my = force_c * (cx - xc)

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
    figure.savefig(path)
    plt.close(figure)
    return path
