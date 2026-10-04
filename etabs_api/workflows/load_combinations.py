"""NSCP 2015 load combinations for the office ETABS models.

``build_combinations`` works on plain data and needs no ETABS. Names have the
form ``<set> <number> <expression>``, for example
``ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3``:

* ULS   strength combinations; these are the concrete design combinations
* SLS   service combinations
* SSLC  special seismic combinations with the amplified force Em
* DEF   deflection: DL, DL + LL, DL + 0.25 LL (sustained) and DL + Lr

Seismic combinations exist twice, once on the static cases (``EQ1`` to
``EQ8``) and once on the response spectrum cases (``RSA1`` to ``RSA8``). The
number is the directional combination: 100 % in one direction with 30 % in
the other. The vertical effect Ev = 0.5 Ca I D is put into the dead load factor.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from design.code_config import NSCP  # noqa: E402

DEAD_TYPES = ("Dead", "Super Dead")
LIVE_TYPES = ("Live", "Reducible Live")
ROOF_LIVE_TYPES = ("Roof Live",)
_FACTORS = NSCP.load_factors


def _directions(x_plus: str, x_minus: str, y_plus: str, y_minus: str) -> tuple:
    """(100 % case, factor, 30 % case, factor) of the eight directional combinations."""
    o = _FACTORS.orthogonal
    out = []
    for main, sign, other_plus, other_minus in ((x_plus, 1.0, y_plus, y_minus),
                                                (x_minus, -1.0, y_plus, y_minus),
                                                (y_plus, 1.0, x_plus, x_minus),
                                                (y_minus, -1.0, x_plus, x_minus)):
        out += [(main, sign, other_plus, o), (main, sign, other_minus, -o)]
    return tuple(out)


_STATIC_DIRECTIONS = _directions("EQXPE", "EQXNE", "EQYPE", "EQYNE")
_SPECTRUM_DIRECTIONS = _directions("RSAX", "RSAX", "RSAY", "RSAY")
SERVICE_SEISMIC = 1.0 / _FACTORS.service_seismic_divisor  # E / 1.4 in the service combinations
# Drift (NSCP 208.6.4.1: the 203.3 combinations; rho = 1.0 by 208.6.1). E is a
# drift case: a drift pattern (period not capped) or a spectrum drift case.
DRIFT_SET = "DRIFT"         # seismic drift combinations, 203-5 and 203-7
WIND_DRIFT_SET = "WDRIFT"   # wind drift combinations, 203-3, 203-4 and 203-6
DRIFT_SPECTRUM_CASES = ("RSAXD", "RSAYD")


@dataclass
class Combination:
    """One load combination: its cases and combinations with their factors."""

    name: str
    envelope: bool = False
    cases: list[tuple[str, float]] = field(default_factory=list)
    combos: list[tuple[str, float]] = field(default_factory=list)
    design: bool = False  # used for concrete design (strength)


def _g(value: float) -> str:
    """A factor as it reads in a combination name: 1.0, 1.2, 0.45, 0.714."""
    value = round(value, 3)
    return f"{value:.1f}" if abs(value * 10 - round(value * 10)) < 1e-9 else f"{value:g}"


def build_combinations(
    patterns: dict[str, str],
    ca: float,
    importance: float = 1.0,
    rho: float = _FACTORS.rho,
    omega0: float = _FACTORS.omega0,
    live_factor: float = _FACTORS.live_companion,
) -> list[Combination]:
    """All combinations, in the order they must be created.

    ``patterns`` maps each load pattern to its ETABS type. Dead and super dead
    patterns form DL, live and reducible live form LL, roof live forms Lr, and
    the wind patterns are ``WX`` and ``WY``. ``live_factor`` is f on LL. The
    factors are those of ``NSCP.load_factors`` (design/code_config.py).
    """
    k = _FACTORS
    dead = [name for name, kind in patterns.items() if kind in DEAD_TYPES]
    live = [name for name, kind in patterns.items() if kind in LIVE_TYPES]
    roof = [name for name, kind in patterns.items() if kind in ROOF_LIVE_TYPES]
    ev = k.vertical_effect * ca * importance
    out: list[Combination] = []

    def gravity(dl: float, ll: float = 0.0, lr: float = 0.0) -> list[tuple[str, float]]:
        items = [(name, dl) for name in dead]
        items += [(name, ll) for name in live if ll]
        items += [(name, lr) for name in roof if lr]
        return items

    def add(name, cases, combos=(), design=False, envelope=False) -> str:
        out.append(Combination(name, envelope, list(cases), list(combos), design))
        return name

    # ---- directional seismic combinations and their envelopes ----
    for label, directions in (("EQ", _STATIC_DIRECTIONS), ("RSA", _SPECTRUM_DIRECTIONS)):
        for number, (main, main_sf, other, other_sf) in enumerate(directions, start=1):
            add(f"{label}_COMBO_{number:02d}", [(main, main_sf), (other, other_sf)])
        add(f"ENVE_{label}", [], [(f"{label}_COMBO_{n:02d}", 1.0) for n in range(1, 9)],
            envelope=True)

    groups: dict[str, list[str]] = {}

    def group(key: str, name: str) -> None:
        groups.setdefault(key, []).append(name)

    def seismic(set_name, number, expression, cases, factor, key, design=False):
        """One combination per direction, for the static and the spectrum cases."""
        for label in ("EQ", "RSA"):
            for n in range(1, 9):
                name = add(
                    f"{set_name} {number} {expression} {label}{n}", cases,
                    [(f"{label}_COMBO_{n:02d}", factor)], design=design,
                )
                group(f"{key}_{label}", name)

    f = live_factor
    w = (("WX", "WX"), ("WY", "WY"))
    d, d_min, s = k.dead, k.dead_minimum, k.service
    # ---- strength (NSCP 203.3.1) ----
    for name in (
        add(f"ULS 100 {_g(k.dead_only)} DL", gravity(k.dead_only), design=True),
        add(f"ULS 101 {_g(d)} DL + {_g(k.live)} LL + {_g(k.roof_live_companion)} Lr",
            gravity(d, k.live, k.roof_live_companion), design=True),
        add(f"ULS 102 {_g(d)} DL + {_g(k.roof_live)} Lr + f LL",
            gravity(d, f, k.roof_live), design=True),
    ):
        group("ULS_GRAVITY", name)
    for number, (text, case) in zip((103, 104), w):
        group("ULS_WIND", add(
            f"ULS {number} {_g(d)} DL + {_g(k.roof_live)} Lr + {_g(k.wind_with_roof)} {text}",
            gravity(d, 0.0, k.roof_live) + [(case, k.wind_with_roof)], design=True))
    for number, (text, case) in zip((105, 106), w):
        group("ULS_WIND", add(
            f"ULS {number} {_g(d)} DL + f LL + {_g(k.roof_live_companion)} Lr + "
            f"{_g(k.wind)} {text}",
            gravity(d, f, k.roof_live_companion) + [(case, k.wind)], design=True))
    seismic("ULS", 107, f"({_g(d)} + Ev) DL + f LL + {_g(k.seismic)}", gravity(d + ev, f),
            k.seismic * rho, "ULS", True)
    for number, (text, case) in zip((108, 109), w):
        group("ULS_WIND", add(f"ULS {number} {_g(d_min)} DL + {_g(k.wind)} {text}",
                              gravity(d_min) + [(case, k.wind)], design=True))
    seismic("ULS", 110, f"({_g(d_min)} - Ev) DL + {_g(k.seismic)}", gravity(d_min - ev),
            k.seismic * rho, "ULS", True)

    # ---- service (NSCP 203.4.1) ----
    e, c, sw = SERVICE_SEISMIC, k.service_companion, k.service_wind
    s_min = k.service_dead_minimum
    group("SLS_GRAVITY", add(f"SLS 100 {_g(s)} DL + {_g(s)} LL + {_g(s)} Lr", gravity(s, s, s)))
    group("SLS_GRAVITY", add(f"SLS 101 {_g(s)} DL + {_g(c)} LL + {_g(c)} Lr", gravity(s, c, c)))
    for number, (text, case) in zip((102, 103), w):
        group("SLS_WIND", add(f"SLS {number} {_g(s)} DL + {_g(sw)} {text}",
                              gravity(s) + [(case, sw)]))
    seismic("SLS", 104, f"({_g(s)} + {_g(e)} Ev) DL + {_g(e)}", gravity(s + e * ev), e * rho, "SLS")
    for number, (text, case) in zip((105, 106), w):
        group("SLS_WIND", add(f"SLS {number} {_g(s)} DL + {_g(c)} LL + {_g(c)} Lr + "
                              f"{_g(c * sw)} {text}",
                              gravity(s, c, c) + [(case, c * sw)]))
    seismic("SLS", 107, f"({_g(s)} + {_g(c * e)} Ev) DL + {_g(c)} LL + {_g(c * e)}",
            gravity(s + c * e * ev, c), c * e * rho, "SLS")
    for number, (text, case) in zip((108, 109), w):
        group("SLS_WIND", add(f"SLS {number} {_g(s_min)} DL + {_g(sw)} {text}",
                              gravity(s_min) + [(case, sw)]))
    seismic("SLS", 110, f"({_g(s_min)} - {_g(e)} Ev) DL + {_g(e)}", gravity(s_min - e * ev),
            e * rho, "SLS")
    for number, (text, case) in zip((111, 112), w):
        group("SLS_WIND", add(f"SLS {number} {_g(s)} DL + {_g(s)} LL + {_g(sw)} {text}",
                              gravity(s, s) + [(case, sw)]))
    seismic("SLS", 113, f"({_g(s)} + {_g(e)} Ev) DL + {_g(s)} LL + {_g(e)}",
            gravity(s + e * ev, s), e * rho, "SLS")

    # ---- drift: seismic (203-5, 203-7) and wind (203-3, 203-4, 203-6) ----
    e_drift = k.seismic * k.drift_rho
    drift_static = [name for name, kind in patterns.items() if kind == "Seismic (Drift)"]
    for case, signs in [(c, (1.0, -1.0)) for c in drift_static] + \
            [(c, (1.0,)) for c in DRIFT_SPECTRUM_CASES]:
        for number, dl, ll, text in ((100, d + ev, f, f"({_g(d)} + Ev) DL + f LL"),
                                     (102, d_min - ev, 0.0, f"({_g(d_min)} - Ev) DL")):
            for offset, sign in enumerate(signs):
                operator = "+" if sign > 0 else "-"
                group("DRIFT", add(f"{DRIFT_SET} {number + offset} {text} {operator} "
                                   f"{_g(e_drift)} {case}",
                                   gravity(dl, ll) + [(case, sign * e_drift)]))
    for text, case in w:
        for number, expression, cases in (
            (100, f"{_g(d)} DL + {_g(k.roof_live)} Lr + {_g(k.wind_with_roof)}",
             gravity(d, 0.0, k.roof_live) + [(case, k.wind_with_roof)]),
            (101, f"{_g(d)} DL + f LL + {_g(k.roof_live_companion)} Lr + {_g(k.wind)}",
             gravity(d, f, k.roof_live_companion) + [(case, k.wind)]),
            (102, f"{_g(d_min)} DL + {_g(k.wind)}", gravity(d_min) + [(case, k.wind)]),
        ):
            group("WDRIFT", add(f"{WIND_DRIFT_SET} {number} {expression} {text}", cases))

    # ---- deflection (unfactored, no live load reduction) ----
    out.extend(deflection_set(dead, live, roof))

    # ---- special seismic: Em = omega0 Eh on the envelope of the directions ----
    for label in ("EQ", "RSA"):
        for number, expression, cases, sign in (
            (100, f"{_g(d)} DL + f LL + {_g(k.seismic)} Em", gravity(d, f), 1.0),
            (101, f"{_g(d_min)} DL + {_g(k.seismic)} Em", gravity(d_min), 1.0),
            (102, f"{_g(d_min)} DL - {_g(k.seismic)} Em", gravity(d_min), -1.0),
        ):
            group(f"SSLC_{label}", add(f"SSLC {number} {expression} {label}", cases,
                                       [(f"ENVE_{label}", sign * k.seismic * omega0)]))

    # ---- envelopes ----
    def envelope(name: str, *keys: str) -> None:
        members = [member for key in keys for member in groups.get(key, [])]
        if members:
            add(name, [], [(member, 1.0) for member in members], envelope=True)

    envelope("ENVE_ULS_GRAVITY", "ULS_GRAVITY")
    envelope("ENVE_ULS_EQ", "ULS_GRAVITY", "ULS_WIND", "ULS_EQ")
    envelope("ENVE_ULS_RSA", "ULS_GRAVITY", "ULS_WIND", "ULS_RSA")
    envelope("ENVE_ULS_BOTH (EQ & RSA)", "ULS_GRAVITY", "ULS_WIND", "ULS_EQ", "ULS_RSA")
    envelope("ENVE_SLS_EQ", "SLS_GRAVITY", "SLS_WIND", "SLS_EQ")
    envelope("ENVE_SLS_RSA", "SLS_GRAVITY", "SLS_WIND", "SLS_RSA")
    envelope("ENVE_SLS_BOTH (EQ & RSA)", "SLS_GRAVITY", "SLS_WIND", "SLS_EQ", "SLS_RSA")
    envelope("ENVE_SSLC_EQ", "SSLC_EQ")
    envelope("ENVE_SSLC_RSA", "SSLC_RSA")
    envelope("ENVE_DRIFT", "DRIFT")
    envelope("ENVE_WDRIFT", "WDRIFT")
    return out


def deflection_set(dead: list[str], live: list[str], roof: list[str]) -> list[Combination]:
    """DL, DL + LL, DL + sustained LL and DL + Lr, unfactored."""
    s, sustained = _FACTORS.service, _FACTORS.sustained_live

    def items(ll: float = 0.0, lr: float = 0.0) -> list[tuple[str, float]]:
        out = [(p, s) for p in dead]
        out += [(p, ll) for p in live if ll]
        out += [(p, lr) for p in roof if lr]
        return out

    return [
        Combination(f"DEF 100 {_g(s)} DL", cases=items()),
        Combination(f"DEF 101 {_g(s)} DL + {_g(s)} LL", cases=items(s)),
        Combination(f"DEF 102 {_g(s)} DL + {_g(sustained)} LL", cases=items(sustained)),
        Combination(f"DEF 103 {_g(s)} DL + {_g(s)} Lr", cases=items(lr=s)),
    ]


# ETABS load pattern types (eLoadPatternType) of DL, LL and Lr
_DEAD_PATTERN_TYPES = (1, 2)
_LIVE_PATTERN_TYPES = (3, 4)
_ROOF_PATTERN_TYPES = (11,)


def deflection_combinations(pattern_types: dict[str, int]) -> list[Combination]:
    """The four deflection combinations from the pattern types of a model."""
    return deflection_set(
        [p for p, t in pattern_types.items() if t in _DEAD_PATTERN_TYPES],
        [p for p, t in pattern_types.items() if t in _LIVE_PATTERN_TYPES],
        [p for p, t in pattern_types.items() if t in _ROOF_PATTERN_TYPES],
    )


def ensure_deflection_combinations(model) -> list[str]:
    """Add the deflection combinations a model does not have yet. Returns those added.

    Adding combinations keeps the analysis results.
    """
    patterns = model.LoadPatterns
    names = [str(n) for n in patterns.GetNameList(0, [])[1]]
    types = {n: int(patterns.GetLoadType(n)[0]) for n in names}
    cases = {str(n) for n in model.LoadCases.GetNameList(0, [])[1]}
    existing = {str(n) for n in model.RespCombo.GetNameList(0, [])[1]}
    added = []
    for combo in deflection_combinations(types):
        if combo.name in existing:
            continue
        model.RespCombo.Add(combo.name, 0)
        for case, factor in combo.cases:
            if case in cases:
                model.RespCombo.SetCaseList(combo.name, 0, case, factor)
        added.append(combo.name)
    return added
