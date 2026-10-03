"""NSCP 2015 load combinations for the office ETABS models.

``build_combinations`` works on plain data and needs no ETABS. Names have the
form ``<set> <number> <expression>``, for example
``ULS 107 (1.2 + Ev) DL + f LL + 1.0 EQ3``:

* ULS   strength combinations; these are the concrete design combinations
* SLS   service combinations
* SSLC  special seismic combinations with the amplified force Em

Seismic combinations exist twice, once on the static cases (``EQ1`` to
``EQ8``) and once on the response spectrum cases (``RSA1`` to ``RSA8``). The
number is the directional combination: 100 % in one direction with 30 % in
the other. The vertical effect Ev = 0.5 Ca I D is put into the dead load factor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEAD_TYPES = ("Dead", "Super Dead")
LIVE_TYPES = ("Live", "Reducible Live")
ROOF_LIVE_TYPES = ("Roof Live",)

# (100 % case, factor, 30 % case, factor) for the static seismic cases.
_STATIC_DIRECTIONS = (
    ("EQXPE", 1.0, "EQYPE", 0.3), ("EQXPE", 1.0, "EQYNE", -0.3),
    ("EQXNE", -1.0, "EQYPE", 0.3), ("EQXNE", -1.0, "EQYNE", -0.3),
    ("EQYPE", 1.0, "EQXPE", 0.3), ("EQYPE", 1.0, "EQXNE", -0.3),
    ("EQYNE", -1.0, "EQXPE", 0.3), ("EQYNE", -1.0, "EQXNE", -0.3),
)
_SPECTRUM_DIRECTIONS = (
    ("RSAX", 1.0, "RSAY", 0.3), ("RSAX", 1.0, "RSAY", -0.3),
    ("RSAX", -1.0, "RSAY", 0.3), ("RSAX", -1.0, "RSAY", -0.3),
    ("RSAY", 1.0, "RSAX", 0.3), ("RSAY", 1.0, "RSAX", -0.3),
    ("RSAY", -1.0, "RSAX", 0.3), ("RSAY", -1.0, "RSAX", -0.3),
)
SERVICE_SEISMIC = 1.0 / 1.4  # E / 1.4 in the service combinations


@dataclass
class Combination:
    """One load combination: its cases and combinations with their factors."""

    name: str
    envelope: bool = False
    cases: list[tuple[str, float]] = field(default_factory=list)
    combos: list[tuple[str, float]] = field(default_factory=list)
    design: bool = False  # used for concrete design (strength)


def build_combinations(
    patterns: dict[str, str],
    ca: float,
    importance: float = 1.0,
    rho: float = 1.0,
    omega0: float = 2.8,
    live_factor: float = 0.5,
) -> list[Combination]:
    """All combinations, in the order they must be created.

    ``patterns`` maps each load pattern to its ETABS type. Dead and super dead
    patterns form DL, live and reducible live form LL, roof live forms Lr, and
    the wind patterns are ``WX`` and ``WY``. ``live_factor`` is f on LL.
    """
    dead = [name for name, kind in patterns.items() if kind in DEAD_TYPES]
    live = [name for name, kind in patterns.items() if kind in LIVE_TYPES]
    roof = [name for name, kind in patterns.items() if kind in ROOF_LIVE_TYPES]
    ev = 0.5 * ca * importance
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
    # ---- strength ----
    for name in (
        add("ULS 100 1.4 DL", gravity(1.4), design=True),
        add("ULS 101 1.2 DL + 1.6 LL + 0.5 Lr", gravity(1.2, 1.6, 0.5), design=True),
        add("ULS 102 1.2 DL + 1.6 Lr + f LL", gravity(1.2, f, 1.6), design=True),
    ):
        group("ULS_GRAVITY", name)
    for number, (text, case) in zip((103, 104), w):
        group("ULS_WIND", add(f"ULS {number} 1.2 DL + 1.6 Lr + 0.5 {text}",
                              gravity(1.2, 0.0, 1.6) + [(case, 0.5)], design=True))
    for number, (text, case) in zip((105, 106), w):
        group("ULS_WIND", add(f"ULS {number} 1.2 DL + f LL + 0.5 Lr + 1.0 {text}",
                              gravity(1.2, f, 0.5) + [(case, 1.0)], design=True))
    seismic("ULS", 107, "(1.2 + Ev) DL + f LL + 1.0", gravity(1.2 + ev, f), rho, "ULS", True)
    for number, (text, case) in zip((108, 109), w):
        group("ULS_WIND", add(f"ULS {number} 0.9 DL + 1.0 {text}",
                              gravity(0.9) + [(case, 1.0)], design=True))
    seismic("ULS", 110, "(0.9 - Ev) DL + 1.0", gravity(0.9 - ev), rho, "ULS", True)

    # ---- service ----
    e = SERVICE_SEISMIC
    group("SLS_GRAVITY", add("SLS 100 1.0 DL + 1.0 LL + 1.0 Lr", gravity(1.0, 1.0, 1.0)))
    group("SLS_GRAVITY", add("SLS 101 1.0 DL + 0.75 LL + 0.75 Lr", gravity(1.0, 0.75, 0.75)))
    for number, (text, case) in zip((102, 103), w):
        group("SLS_WIND", add(f"SLS {number} 1.0 DL + 0.6 {text}", gravity(1.0) + [(case, 0.6)]))
    seismic("SLS", 104, "(1.0 + 0.714 Ev) DL + 0.714", gravity(1.0 + e * ev), e * rho, "SLS")
    for number, (text, case) in zip((105, 106), w):
        group("SLS_WIND", add(f"SLS {number} 1.0 DL + 0.75 LL + 0.75 Lr + 0.45 {text}",
                              gravity(1.0, 0.75, 0.75) + [(case, 0.45)]))
    seismic("SLS", 107, "(1.0 + 0.536 Ev) DL + 0.75 LL + 0.536",
            gravity(1.0 + 0.75 * e * ev, 0.75), 0.75 * e * rho, "SLS")
    for number, (text, case) in zip((108, 109), w):
        group("SLS_WIND", add(f"SLS {number} 0.6 DL + 0.6 {text}", gravity(0.6) + [(case, 0.6)]))
    seismic("SLS", 110, "(0.6 - 0.714 Ev) DL + 0.714", gravity(0.6 - e * ev), e * rho, "SLS")
    for number, (text, case) in zip((111, 112), w):
        group("SLS_WIND", add(f"SLS {number} 1.0 DL + 1.0 LL + 0.6 {text}",
                              gravity(1.0, 1.0) + [(case, 0.6)]))
    seismic("SLS", 113, "(1.0 + 0.714 Ev) DL + 1.0 LL + 0.714",
            gravity(1.0 + e * ev, 1.0), e * rho, "SLS")

    # ---- special seismic: Em = omega0 Eh on the envelope of the directions ----
    for label in ("EQ", "RSA"):
        for number, expression, cases, sign in (
            (100, "1.2 DL + f LL + 1.0 Em", gravity(1.2, f), 1.0),
            (101, "0.9 DL + 1.0 Em", gravity(0.9), 1.0),
            (102, "0.9 DL - 1.0 Em", gravity(0.9), -1.0),
        ):
            group(f"SSLC_{label}", add(f"SSLC {number} {expression} {label}", cases,
                                       [(f"ENVE_{label}", sign * omega0)]))

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
    return out
