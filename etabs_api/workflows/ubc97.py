"""UBC 97 seismic coefficients (also NSCP 2015 Section 208).

Ca and Cv come from the seismic zone factor, the soil profile type and, in
zone 4, the near-source factors Na and Nv, which depend on the seismic source
type and the closest distance to the source. The tables are in
``design/code_config.py`` (``NSCP.seismic``).
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import NSCP  # noqa: E402

_SEISMIC = NSCP.seismic
ZONE_FACTORS = _SEISMIC.ubc_zone_factors
SOIL_TYPES = tuple(_SEISMIC.ca)
SOURCE_TYPES = tuple(_SEISMIC.na)
GRAVITY = _SEISMIC.gravity  # mm/s2


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
    return (_interpolate(_SEISMIC.na[source], distance_km),
            _interpolate(_SEISMIC.nv[source], distance_km))


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
    ca, cv = _SEISMIC.ca[soil][index], _SEISMIC.cv[soil][index]
    if abs(ZONE_FACTORS[index] - _SEISMIC.zone4_factor) < 1e-9:
        na, nv = near_source_factors(source_type, distance_km)
        ca, cv = ca * na, cv * nv
    return ca, cv


def vertical_effect_factor(ca: float, importance: float) -> float:
    """Ev as a fraction of the dead load: Ev = 0.5 Ca I D."""
    return NSCP.load_factors.vertical_effect * ca * importance


def response_spectrum_scale(importance: float, r_factor: float) -> float:
    """Scale factor of a response spectrum case in N-mm units: g I / R."""
    return GRAVITY * importance / r_factor
