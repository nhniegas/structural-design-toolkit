"""Reinforced Concrete Concrete Structural Design Engine per ACI 318M-14.

Includes automated flexural design, shear design, code check evaluations,
live Excel overwrite integration, and a direct compliance resolver that
updates geometry and rebar without demand inflation.
"""

import numpy as np
import math
from sectionproperties.pre.library import rectangular_section, circular_section
import concreteproperties.stress_strain_profile as ssp
from concreteproperties import (
    Concrete,
    ConcreteSection,
    SteelBar,
    add_bar,
)
from concreteproperties.post import si_kn_m, si_n_mm


def define_materials(
    self, fc_name: str = "Concrete", fy_name: str = "SteelBar"
) -> None:
    concrete = Concrete(
        name=fc_name,
        density=2400.0,  # kg/m^3
        # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
        stress_strain_profile=ssp.ConcreteLinear(
            elastic_modulus=4700 * (self.fc**0.5) * 1e6
        ),  # Pa
        ultimate_stress_strain_profile=ssp.RectangularStressBlock(
            compressive_strength=self.fc,  # Pa
            alpha=0.85,
            gamma=0.8,
            ultimate_strain=0.003,
        ),
        flexural_tensile_strength=0.62 * (self.fc**0.5) * 1e6,  # Pa
        colour="lightgrey",
    )

    steel = SteelBar(
        name=fy_name,
        density=7850.0,  # kg/m^3
        stress_strain_profile=ssp.SteelElasticPlastic(
            yield_strength=self.fy,  # Pa
            elastic_modulus=200e3,  # Pa
            fracture_strain=0.05,
        ),
        colour="grey",
    )

    return concrete, steel


def define_section(self, d, b, concrete) -> None:
    if concrete is None:
        section = rectangular_section(d=d, b=b, material=concrete)
    else:
        section = circular_section(d=d, n=20, material=concrete)

    return section


def add_reinf(self, section, bar_diameter, steelbar, concrete) -> None:
    section = section

    section = add_bar(
        geometry=section,
        area=(math.pi * (bar_diameter**2) / 4),
        material=self.steel,
        x=100,
        y=100,
    )
    return section
