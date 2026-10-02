"""UBC 97 seismic coefficients (also NSCP 2015 Section 208).

Ca and Cv come from the seismic zone factor, the soil profile type and, in
zone 4, the near-source factors Na and Nv, which depend on the seismic source
type and the closest distance to the source.
"""

from __future__ import annotations

ZONE_FACTORS = (0.075, 0.15, 0.2, 0.3, 0.4)
SOIL_TYPES = ("SA", "SB", "SC", "SD", "SE")
SOURCE_TYPES = ("A", "B", "C")

# Tables 16-Q and 16-R: one value per zone factor. In zone 4 (Z = 0.4) the
# value is multiplied by Na (for Ca) or Nv (for Cv).
_CA = {
    "SA": (0.06, 0.12, 0.16, 0.24, 0.32),
    "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
    "SC": (0.09, 0.18, 0.24, 0.33, 0.40),
    "SD": (0.12, 0.22, 0.28, 0.36, 0.44),
    "SE": (0.19, 0.30, 0.34, 0.36, 0.36),
}
_CV = {
    "SA": (0.06, 0.12, 0.16, 0.24, 0.32),
    "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
    "SC": (0.13, 0.25, 0.32, 0.45, 0.56),
    "SD": (0.18, 0.32, 0.40, 0.54, 0.64),
    "SE": (0.26, 0.50, 0.64, 0.84, 0.96),
}
# Tables 16-S and 16-T: (distance in km, factor); linear in between, constant outside.
_NA = {
    "A": ((2.0, 1.5), (5.0, 1.2), (10.0, 1.0)),
    "B": ((2.0, 1.3), (5.0, 1.0), (10.0, 1.0)),
    "C": ((2.0, 1.0), (5.0, 1.0), (10.0, 1.0)),
}
_NV = {
    "A": ((2.0, 2.0), (5.0, 1.6), (10.0, 1.2), (15.0, 1.0)),
    "B": ((2.0, 1.6), (5.0, 1.2), (10.0, 1.0), (15.0, 1.0)),
    "C": ((2.0, 1.0), (5.0, 1.0), (10.0, 1.0), (15.0, 1.0)),
}
GRAVITY = 9806.65  # mm/s2


def _interpolate(points: tuple, x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def near_source_factors(source_type: str, distance_km: float) -> tuple[float, float]:
    """Na and Nv for a seismic source type (A, B, C) and its distance in km."""
    source = str(source_type).strip().upper()
    if source not in SOURCE_TYPES:
        raise ValueError(f"Seismic source type must be one of {SOURCE_TYPES}.")
    if distance_km < 0:
        raise ValueError("Distance to the seismic source cannot be negative.")
    return _interpolate(_NA[source], distance_km), _interpolate(_NV[source], distance_km)


def seismic_coefficients(
    zone_factor: float, soil_type: str, source_type: str = "A", distance_km: float = 15.0
) -> tuple[float, float]:
    """Ca and Cv. The near-source factors only apply in zone 4 (Z = 0.4)."""
    soil = str(soil_type).strip().upper()
    if soil not in SOIL_TYPES:
        raise ValueError(f"Soil profile type must be one of {SOIL_TYPES}.")
    matches = [i for i, z in enumerate(ZONE_FACTORS) if abs(z - float(zone_factor)) < 1e-9]
    if not matches:
        raise ValueError(f"Seismic zone factor must be one of {ZONE_FACTORS}.")
    index = matches[0]
    ca, cv = _CA[soil][index], _CV[soil][index]
    if index == len(ZONE_FACTORS) - 1:
        na, nv = near_source_factors(source_type, distance_km)
        ca, cv = ca * na, cv * nv
    return ca, cv


def vertical_effect_factor(ca: float, importance: float) -> float:
    """Ev as a fraction of the dead load: Ev = 0.5 Ca I D."""
    return 0.5 * ca * importance


def response_spectrum_scale(importance: float, r_factor: float) -> float:
    """Scale factor of a response spectrum case in N-mm units: g I / R."""
    return GRAVITY * importance / r_factor
