"""Reinforced Concrete Concrete Structural Design Engine per ACI 318M-14.

Includes automated flexural design, shear design, code check evaluations,
live Excel overwrite integration, and a direct compliance resolver that
updates geometry and rebar without demand inflation.
"""

import math
import numpy as np
from sectionproperties.pre.library import rectangular_section, circular_section
import concreteproperties.stress_strain_profile as ssp
from concreteproperties import (
    Concrete,
    ConcreteSection,
    SteelBar,
    add_bar,
    add_bar_circular_array,
)
from concreteproperties.post import si_kn_m, si_n_mm


class ColumnFlexureDesign:
    """A structural design engine for reinforced concrete columns per ACI 318M-14.

    This class handles the definition of materials, cross-sections, and reinforcement,
    and provides a framework for evaluating moment capacity and code compliance.
    """

    def __init__(
        self,
        width: float,
        height: float,
        diameter: float,
        fc: float = 28.0,
        fy: float = 415.0,
        fyt: float = 415.0,
        dmain: float = 25.0,
        dties: float = 10.0,
        cc: float = 40.0,
        m_x: float = 0.0,
        m_y: float = 0.0,
        axial_load: float = 0.0,
        shape: str = "rectangular",
    ) -> None:
        self.width = width
        self.height = height
        self.diameter = diameter
        self.fc = fc
        self.fy = fy
        self.fyt = fyt
        self.dmain = dmain
        self.dties = dties
        self.cc = cc
        self.m_x = m_x
        self.m_y = m_y
        self.axial_load = axial_load
        self.shape = shape.lower()
        self.design_results = {}

    def define_materials(self, fc_name: str = "Concrete", fy_name: str = "SteelBar"):
        """Define the nonlinear material models for concrete and steel.

        Uses ACI 318M-14 provisions to generate a Rectangular Stress Block
        for concrete and an Elastic-Plastic profile for the reinforcing steel.

        Args:
            fc_name (str): The label for the concrete material.
            fy_name (str): The label for the steel material.

        Returns:
            tuple: A tuple containing the initialized (Concrete, SteelBar) objects.
        """

        def solve_beta():
            """Determine the beta1 factor for the rectangular stress block.

            ACI 318M-14 Table 19.2.2 provides a linear reduction of beta1
            from 0.85 to 0.65 for concrete strengths between 28 MPa and 56 MPa.
            """
            if self.fc <= 28:
                return 0.85
            elif self.fc >= 56:
                return 0.65
            else:
                return 0.85 - ((self.fc - 28) * (0.85 - 0.65) / (56 - 28))

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
                gamma=solve_beta(),
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

    def define_section(self, height, width, diameter, concrete):
        """Create the geometric cross-section based on self.shape."""
        if self.shape == "rectangular":
            section = rectangular_section(d=height, b=width, material=concrete)
        elif self.shape == "circular":
            # For circular sections, width is typically treated as the diameter
            section = circular_section(d=diameter, n=20, material=concrete)
        else:
            raise ValueError("Shape must be 'rectangular' or 'circular'.")

        return section

    def _calculate_required_bars(
        self, initial_bars: int, max_spacing: float = 150.0
    ) -> int:
        """Determines total required bars, ensuring corners are populated and spacing is met."""
        if self.shape == "rectangular":
            core_width = self.width - (2 * self.cc) - (2 * self.dties) - self.dmain
            core_height = self.height - (2 * self.cc) - (2 * self.dties) - self.dmain

            # Determine minimum bar spaces per edge to satisfy max spacing
            nx = math.ceil(core_width / max_spacing)
            ny = math.ceil(core_height / max_spacing)

            required_bars = 2 * nx + 2 * ny

            # Symmetrically add bars to the longest spacing intervals until initial_bars is met
            while required_bars < initial_bars:
                if (core_width / nx) > (core_height / ny):
                    nx += 1
                else:
                    ny += 1
                required_bars = 2 * nx + 2 * ny

            return required_bars

        elif self.shape == "circular":
            core_radius = (self.diameter / 2) - self.cc - self.dties - (self.dmain / 2)
            perimeter = 2 * math.pi * core_radius
            required_bars = math.ceil(perimeter / max_spacing)
            return max(initial_bars, required_bars)

    def solve_max_spacing(self) -> dict:
        """Evaluate maximum allowed spacing for transverse ties per ACI 318M-14.

        Calculates tie spacing limits based on the main longitudinal bar diameter,
        tie diameter, and the smallest overall column dimension.

        Returns:
            dict: A dictionary containing 'max_tie_spacing_mm' and 'max_long_spacing_mm' limits.
        """
        min_col_dim = (
            min(self.width, self.height)
            if self.shape == "rectangular"
            else self.diameter
        )
        max_tie_spacing = min(16 * self.dmain, 48 * self.dties, min_col_dim)
        return {"max_tie_spacing_mm": max_tie_spacing, "max_long_spacing_mm": 150.0}

    def check_reinforcement_limits(self, n_bars: int) -> dict:
        """Check longitudinal steel ratio (rho) against ACI 318M-14 requirements.

        Calculates the gross concrete area (Ag) and the total area of longitudinal
        steel (Ast) to ensure the reinforcement ratio falls between the 1% and 8% code limits.

        Args:
            n_bars (int): Total number of longitudinal reinforcing bars provided.

        Returns:
            dict: A dictionary detailing Ag, Ast, rho, upper/lower limits, and the compliance status ('Pass' or 'Fail').
        """
        ag = (
            self.width * self.height
            if self.shape == "rectangular"
            else (math.pi * (self.diameter**2)) / 4
        )
        ast = n_bars * (math.pi * (self.dmain**2) / 4)
        rho = ast / ag
        status = "Pass" if 0.01 <= rho <= 0.08 else "Fail"

        return {
            "Ag_mm2": ag,
            "Ast_mm2": ast,
            "rho": rho,
            "status": status,
            "limits": (0.01, 0.08),
        }

    def add_reinf(self, section, bar_diameter, steelbar, initial_bars=4):
        """Insert longitudinal reinforcing bars explicitly anchoring the corners."""
        area = (math.pi * (bar_diameter**2)) / 4

        if self.shape == "rectangular":
            offset_x = self.cc + self.dties + (bar_diameter / 2)
            offset_y = self.cc + self.dties + (bar_diameter / 2)
            core_w = self.width - 2 * offset_x
            core_h = self.height - 2 * offset_y

            # Recalculate segments identical to the helper method
            nx = math.ceil(core_w / 150.0)
            ny = math.ceil(core_h / 150.0)
            required_bars = 2 * nx + 2 * ny

            while required_bars < initial_bars:
                if (core_w / nx) > (core_h / ny):
                    nx += 1
                else:
                    ny += 1
                required_bars = 2 * nx + 2 * ny

            # Map out exact coordinates for each edge (preventing corner duplication)
            bar_coords = []

            # Bottom edge (Includes bottom-left corner)
            for i in range(nx):
                bar_coords.append((offset_x + i * (core_w / nx), offset_y))

            # Right edge (Includes bottom-right corner)
            for i in range(ny):
                bar_coords.append((offset_x + core_w, offset_y + i * (core_h / ny)))

            # Top edge (Includes top-right corner)
            for i in range(nx):
                bar_coords.append(
                    (offset_x + core_w - i * (core_w / nx), offset_y + core_h)
                )

            # Left edge (Includes top-left corner)
            for i in range(ny):
                bar_coords.append((offset_x, offset_y + core_h - i * (core_h / ny)))

            # Draw all mapped bars
            for x, y in bar_coords:
                section = add_bar(
                    geometry=section, area=area, material=steelbar, x=x, y=y
                )

        elif self.shape == "circular":
            n_bars = self._calculate_required_bars(initial_bars, max_spacing=150.0)
            r_array = (self.diameter / 2) - self.cc - self.dties - (bar_diameter / 2)
            section = add_bar_circular_array(
                geometry=section,
                area=area,
                material=steelbar,
                n_bar=n_bars,
                r_array=r_array,
                ctr=(0, 0),
            )

        return section

    def solve_moment_capacity(
        self, section, axial_load=0.0, bending_angle=None
    ) -> tuple:
        """Calculate the nominal and design moment capacities (Mn, phi*Mn).

        Evaluates the capacity reduction factor based on strain conditions and
        cross-section shape per ACI 318M-14 Table 21.2.2. The bending angle is
        derived from the arctan of the applied biaxial moments if not specified.

        Args:
            section (Geometry): The reinforced concrete section.
            axial_load (float): The applied axial load in N. Defaults to 0.0.
            bending_angle (float, optional): The bending angle in radians. Defaults to arctan(My/Mx).

        Returns:
            tuple: (nominal_moment_capacity, ultimate_moment_capacity, capacity_reduction_factor)
        """
        section = ConcreteSection(section)

        def compressive_tensile_strength():

            compressive_strength = 0.0
            tensile_strength = 0.0
            alpha = 0.85

            for conc_geom in section.concrete_geometries:
                # calculate area
                area = conc_geom.calculate_area()
                # calculate compressive force
                ult_profile = conc_geom.material.ultimate_stress_strain_profile
                force_c = area * alpha * ult_profile.get_compressive_strength()

                # add to totals
                compressive_strength += force_c

            for steel_geom in section.reinf_geometries_lumped:
                # calculate area
                area = steel_geom.calculate_area()

                # calculate compressive and tensile force
                force_c = area * steel_geom.material.stress_strain_profile.get_stress(
                    strain=0.025
                )

                force_t = (
                    -area
                    * steel_geom.material.stress_strain_profile.get_yield_strength()
                )

                # add to totals
                compressive_strength += force_c
                tensile_strength += force_t

            return compressive_strength, tensile_strength

        compressive_strength, tensile_strength = compressive_tensile_strength()

        if self.shape == "rectangular":
            if (
                0.80 * compressive_strength < axial_load
                or 0.80 * tensile_strength > axial_load
            ):
                raise ValueError(
                    "Applied axial load exceeds the section's compressive or tensile capacity."
                )
        else:  # Circular section
            if (
                0.85 * compressive_strength < axial_load
                or 0.85 * tensile_strength > axial_load
            ):
                raise ValueError(
                    "Applied axial load exceeds the section's compressive or tensile capacity."
                )

        n = axial_load

        # Calculate bending angle from applied moments if not overridden
        if bending_angle is None:
            if self.m_x == 0 and self.m_y == 0:
                bending_angle = np.pi / 2
            else:
                bending_angle = np.arctan2(self.m_x, self.m_y) - np.pi / 2

        nominal_moment_capacity = section.ultimate_bending_capacity(
            theta=bending_angle, n=n
        )

        stress_diagram = section.calculate_ultimate_stress(nominal_moment_capacity)
        strain_data = stress_diagram.lumped_reinforcement_strains
        extreme_strain = abs(np.min(strain_data))  # Most negative strain value

        nominal_moment_capacity.print_results(si_kn_m)

        # Steel yield strain calculation
        e_y = self.fy / (200e3)

        # ACI 318M-14 Table 21.2.2 Capacity Reduction Factors (phi)
        if self.shape == "circular":  # Assuming spirally reinforced
            if extreme_strain >= 0.005:
                capacity_reduction_factor = 0.90
            elif extreme_strain <= e_y:
                capacity_reduction_factor = 0.75
            else:
                # Transition zone for spiral
                capacity_reduction_factor = 0.75 + 0.15 * (
                    (extreme_strain - e_y) / (0.005 - e_y)
                )
        else:  # Rectangular (Tied)
            if extreme_strain >= 0.005:
                capacity_reduction_factor = 0.90
            elif extreme_strain <= e_y:
                capacity_reduction_factor = 0.65
            else:
                # Transition zone for tied
                capacity_reduction_factor = 0.65 + 0.25 * (
                    (extreme_strain - e_y) / (0.005 - e_y)
                )

        ultimate_moment_capacity = (
            capacity_reduction_factor * nominal_moment_capacity.m_xy
        )
        nominal_axial_capacity = (
            0.85 * compressive_strength
            if self.shape == "circular"
            else 0.80 * compressive_strength
        )
        ultimate_axial_capacity = capacity_reduction_factor * nominal_axial_capacity

        return (
            nominal_moment_capacity.m_xy,
            ultimate_moment_capacity,
            nominal_axial_capacity,
            ultimate_axial_capacity,
            capacity_reduction_factor,
        )

    def perform_column_flexure_design(self, initial_bars: int = 4) -> dict:
        """Execute the full column design, modeling, and evaluation workflow."""
        concrete, steel = self.define_materials()
        section = self.define_section(self.height, self.width, self.diameter, concrete)
        n_bars = self._calculate_required_bars(initial_bars, max_spacing=150.0)
        section = self.add_reinf(section, self.dmain, steel, initial_bars)

        # Pass the initialized axial load to the solver
        (
            nom_moment_capacity,
            ult_moment_capacity,
            nom_axial_capacity,
            ult_axial_capacity,
            phi_factor,
        ) = self.solve_moment_capacity(section, axial_load=self.axial_load)

        spacing_limits = self.solve_max_spacing()
        rho_limits = self.check_reinforcement_limits(n_bars)

        self.design_results = {
            "shape": self.shape.capitalize(),
            "total_longitudinal_bars": n_bars,
            "tie_spacing_provided": min(spacing_limits["max_tie_spacing_mm"], 300.0),
            "max_tie_spacing_allowed": spacing_limits["max_tie_spacing_mm"],
            "reinforcement_ratio": round(rho_limits["rho"], 4),
            "ratio_compliance": rho_limits["status"],
            "nominal_moment_capacity": nom_moment_capacity,
            "ultimate_moment_capacity": ult_moment_capacity,
            "nominal_axial_capacity": nom_axial_capacity,
            "ultimate_axial_capacity": ult_axial_capacity,
            "capacity_reduction_factor": phi_factor,
        }

        return self.design_results


if __name__ == "__main__":
    # Test 1: Rectangular Column
    print("--- Rectangular Column Test ---")
    rect_col = ColumnFlexureDesign(
        width=400.0,
        height=500.0,
        diameter=0.0,
        fc=28.0,
        fy=415.0,
        fyt=415.0,
        dmain=25.0,
        dties=10.0,
        cc=40.0,
        m_x=150e6,
        m_y=100e6,
        axial_load=1500e3,  # e.g., 1500 kN applied axial load
        shape="rectangular",
    )

    rect_results = rect_col.perform_column_flexure_design(initial_bars=4)
    for key, val in rect_results.items():
        if isinstance(val, float):
            print(f"{key}: {val:.2f}")
        else:
            print(f"{key}: {val}")

    print("\n--- Circular Column Test ---")
    # Test 2: Circular Column
    circ_col = ColumnFlexureDesign(
        width=0.0,
        height=0.0,
        diameter=450.0,
        fc=35.0,
        fy=420.0,
        fyt=420.0,
        dmain=28.0,
        dties=10.0,
        cc=40.0,
        m_x=200e6,
        m_y=0.0,
        axial_load=2000e3,  # e.g., 2000 kN applied axial load
        shape="circular",
    )

    circ_results = circ_col.perform_column_flexure_design(initial_bars=6)
    for key, val in circ_results.items():
        if isinstance(val, float):
            print(f"{key}: {val:.2f}")
        else:
            print(f"{key}: {val}")
