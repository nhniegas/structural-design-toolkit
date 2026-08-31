"""Reinforced Concrete Beam Structural Design Engine per ACI 318M-14.

Includes automated flexural design, shear design, torsion design,
code check evaluations, live Excel overwrite integration, and a
direct compliance resolver that updates geometry and rebar without demand inflation.
"""

import math
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xlwings as xw
from typing import Any, Dict, List, Set, Tuple

from concreteproperties import add_bar
from concreteproperties.concrete_section import ConcreteSection
from concreteproperties.material import Concrete, SteelBar
import concreteproperties.stress_strain_profile as ssp
import sectionproperties.pre.library.primitive_sections as sp_ps


def identify_cantilever_beams(
    frame_df: pd.DataFrame, conn_df: pd.DataFrame
) -> pd.DataFrame:
    """Identifies beam support conditions using Column and Wall joint connectivity."""
    conn_df.columns = [str(c).strip() for c in conn_df.columns]

    support_rows = conn_df[conn_df["DesignType"].isin(["Column", "Wall"])]

    pt_cols = [
        c
        for c in [
            "UniquePtI",
            "UniquePtJ",
            "UniquePt1",
            "UniquePt2",
            "UniquePt3",
            "UniquePt4",
        ]
        if c in support_rows.columns
    ]

    support_joints = set()
    for col in pt_cols:
        support_joints.update(support_rows[col].dropna().tolist())

    beam_conn = conn_df[conn_df["DesignType"] == "Beam"].copy()

    beam_conn["Has_Support_PtI"] = beam_conn["UniquePtI"].isin(support_joints)
    beam_conn["Has_Support_PtJ"] = beam_conn["UniquePtJ"].isin(support_joints)

    def get_status(row):
        sup_count = sum([row["Has_Support_PtI"], row["Has_Support_PtJ"]])
        if sup_count == 2:
            return "Supported Both Ends"
        elif sup_count == 1:
            cant_pt = "PtI" if not row["Has_Support_PtI"] else "PtJ"
            return f"Cantilever (Free at {cant_pt})"
        else:
            return "Beam-Framed / Floating"

    beam_conn["SupportStatus"] = beam_conn.apply(get_status, axis=1)

    return beam_conn[["UniqueName", "SupportStatus"]].reset_index(drop=True)


# =========================================================================
# 1. CORE SECTION & MATERIAL ENGINE
# =========================================================================
class BeamSectionGeometry:
    """Manages concrete geometry, rebar materials, and ultimate flexural capacity calculations."""

    def __init__(
        self,
        width: float,
        depth: float,
        fc: float = 34.48,
        fy: float = 413.69,
        concrete_name: str = "C35",
        rebar_name: str = "G60",
    ):
        self.width = width
        self.depth = depth
        self.fc = fc
        self.fy = fy
        self.concrete_name = concrete_name
        self.rebar_name = rebar_name

        self.concrete_ft = 0.62 * math.sqrt(self.fc)
        self.reinforced_concrete_beam = self.create_concrete_section()
        self.reinforced_concrete_beam_rebar_config = None

    @staticmethod
    def calculate_gamma(compressive_strength: float) -> float:
        if compressive_strength <= 28:
            return 0.85
        elif 28 < compressive_strength < 55:
            return 0.85 - 0.05 * (compressive_strength - 28) / 7
        return 0.65

    @staticmethod
    def calculate_ec(compressive_strength: float) -> float:
        return 4700 * math.sqrt(compressive_strength)

    def get_materials(self) -> tuple[Concrete, SteelBar]:
        ec = self.calculate_ec(self.fc)
        gamma_val = self.calculate_gamma(self.fc)

        concrete = Concrete(
            name=self.concrete_name,
            density=2.4e-6,
            stress_strain_profile=ssp.ConcreteLinearNoTension(
                elastic_modulus=ec,
                ultimate_strain=0.003,
                compressive_strength=self.fc,
            ),
            ultimate_stress_strain_profile=ssp.RectangularStressBlock(
                compressive_strength=self.fc,
                alpha=0.85,
                gamma=gamma_val,
                ultimate_strain=0.003,
            ),
            flexural_tensile_strength=self.concrete_ft,
            colour="lightgrey",
        )

        steel = SteelBar(
            name=self.rebar_name,
            density=7.85e-6,
            stress_strain_profile=ssp.SteelElasticPlastic(
                yield_strength=self.fy,
                elastic_modulus=200000.0,
                fracture_strain=0.05,
            ),
            colour="grey",
        )
        return concrete, steel

    def create_concrete_section(self):
        concrete_mat, _ = self.get_materials()
        return sp_ps.rectangular_section(
            d=self.depth, b=self.width, material=concrete_mat
        )

    def add_bars(self, rebar_area: float, x_coor: float, y_coor: float):
        _, rebar_mat = self.get_materials()

        self.reinforced_concrete_beam_rebar_config = add_bar(
            geometry=self.reinforced_concrete_beam_rebar_config
            or self.reinforced_concrete_beam,
            material=rebar_mat,
            area=rebar_area,
            x=x_coor,
            y=y_coor,
            n=40,
        )

    def calculate_ultimate_bending_capacity(self):
        if self.reinforced_concrete_beam_rebar_config is None:
            raise ValueError("No reinforcement added.")
        conc_sec = ConcreteSection(self.reinforced_concrete_beam_rebar_config)
        sag_res = conc_sec.ultimate_bending_capacity()
        hog_res = conc_sec.ultimate_bending_capacity(theta=np.pi)
        weak_res = conc_sec.ultimate_bending_capacity(theta=np.pi / 2)
        return sag_res, hog_res, weak_res


# =========================================================================
# 2. FLEXURAL DESIGN MODULE
# =========================================================================
class BeamFlexuralDesign:
    """Handles flexural sizing, multi-layer coordinates, capacity verification, and shear synchronization."""

    def __init__(
        self,
        section: BeamSectionGeometry,
        db: float = 20.0,
        d_stirrup: float = 10.0,
        clear_cover: float = 40.0,
        d_agg: float = 20.0,
        phi_flexure: float = 0.9,
        target_override_bars: dict = None,
    ):
        self.section = section
        self.db = db
        self.d_stirrup = d_stirrup
        self.clear_cover = clear_cover
        self.d_agg = d_agg
        self.phi = phi_flexure
        self.target_override_bars = target_override_bars or {}

        self.s_clear_min = max(25.0, self.db, (4.0 / 3.0) * self.d_agg)
        self.s_center_min = self.s_clear_min + self.db
        self.s_vert_min = max(25.0, self.db)

        self.x_left = self.clear_cover + self.d_stirrup + (self.db / 2.0)
        self.x_right = (
            self.section.width - (self.clear_cover + self.d_stirrup) - (self.db / 2.0)
        )
        self.w_avail = self.x_right - self.x_left

        self.max_bars_per_layer = int(math.floor(self.w_avail / self.s_center_min)) + 1
        if self.max_bars_per_layer < 2:
            raise ValueError("Beam width is too narrow to accommodate 2 rebars.")
        self.max_3_layer_bars = 3 * self.max_bars_per_layer

    def _sanitize_bar_count(self, n_bars: int) -> int:
        if n_bars <= 2:
            return max(2, n_bars)
        M = self.max_bars_per_layer
        if M < n_bars < M + 2:
            return M + 2
        if 2 * M < n_bars < 2 * M + 2:
            return 2 * M + 2
        return n_bars

    def calculate_as_min(self, d_eff: float) -> float:
        eq_a = (0.25 * math.sqrt(self.section.fc) / self.section.fy) * (
            self.section.width * d_eff
        )
        eq_b = (1.4 / self.section.fy) * (self.section.width * d_eff)
        return max(eq_a, eq_b)

    def get_min_required_bars(self, d_eff: float, evaluator=None) -> int:
        as_min = self.calculate_as_min(d_eff)
        area_single = (math.pi / 4.0) * (self.db**2)
        n_as_min = int(math.ceil(as_min / area_single))

        fs_service = (2.0 / 3.0) * self.section.fy
        if evaluator is not None:
            s_max_limit = evaluator.s_max(clear_cover=self.clear_cover, fs=fs_service)
        else:
            s1 = 380.0 * (280.0 / fs_service) - 2.5 * self.clear_cover
            s2 = 300.0 * (280.0 / fs_service)
            s_max_limit = min(s1, s2)

        n_smax = (
            int(math.ceil(self.w_avail / s_max_limit)) + 1 if s_max_limit > 0 else 2
        )
        return self._sanitize_bar_count(max(2, n_as_min, n_smax))

    def generate_layer_coordinates(
        self, total_bars: int, is_top: bool
    ) -> list[tuple[float, float]]:
        coords = []
        total_bars = min(self._sanitize_bar_count(total_bars), self.max_3_layer_bars)
        l1_bars = min(total_bars, self.max_bars_per_layer)

        x_l1 = [
            self.x_left + i * (self.w_avail / (l1_bars - 1)) for i in range(l1_bars)
        ]
        y_l1 = (
            self.section.depth - self.clear_cover - self.d_stirrup - (self.db / 2.0)
            if is_top
            else self.clear_cover + self.d_stirrup + (self.db / 2.0)
        )
        y_step = -(self.s_vert_min + self.db) if is_top else (self.s_vert_min + self.db)

        bars_placed = 0
        for x in x_l1:
            if bars_placed < total_bars:
                coords.append((x, y_l1))
                bars_placed += 1

        layer_idx = 1
        while bars_placed < total_bars and layer_idx < 3:
            y_layer = y_l1 + (layer_idx * y_step)
            remaining = total_bars - bars_placed
            bars_this_layer = min(remaining, l1_bars)

            priority_indices = self._get_priority_indices(l1_bars, bars_this_layer)
            for idx in priority_indices:
                coords.append((x_l1[idx], y_layer))
                bars_placed += 1

            layer_idx += 1

        return coords

    @staticmethod
    def _get_priority_indices(l1_bars: int, count: int) -> list[int]:
        if count >= l1_bars:
            return list(range(l1_bars))

        order = []
        left, right = 0, l1_bars - 1
        toggle = True

        while left <= right:
            if toggle:
                order.append(left)
                left += 1
            else:
                order.append(right)
                right -= 1
            toggle = not toggle

        return order[:count]

    def design_flexure_location(
        self,
        mu_top: float,
        mu_bot: float,
        location_name: str = "Left",
        mu_top_orig: float = None,
        mu_bot_orig: float = None,
        evaluator=None,
    ) -> dict:
        mu_top, mu_bot = abs(mu_top), abs(mu_bot)
        mu_top_orig = mu_top if mu_top_orig is None else abs(mu_top_orig)
        mu_bot_orig = mu_bot if mu_bot_orig is None else abs(mu_bot_orig)

        d_approx = (
            self.section.depth - self.clear_cover - self.d_stirrup - (self.db / 2.0)
        )
        min_bars = self.get_min_required_bars(d_approx, evaluator=evaluator)

        override_top = self.target_override_bars.get(location_name, {}).get("top", 0)
        override_bot = self.target_override_bars.get(location_name, {}).get("bot", 0)

        n_top = self._sanitize_bar_count(max(min_bars, override_top))
        n_bot = self._sanitize_bar_count(max(min_bars, override_bot))

        area_single = (math.pi / 4.0) * (self.db**2)
        top_passed, bot_passed = False, False
        top_max_reached, bot_max_reached = False, False

        while not (top_passed and bot_passed):
            self.section.reinforced_concrete_beam_rebar_config = (
                self.section.create_concrete_section()
            )
            top_coords = self.generate_layer_coordinates(n_top, is_top=True)
            bot_coords = self.generate_layer_coordinates(n_bot, is_top=False)

            for x, y in top_coords + bot_coords:
                self.section.add_bars(area_single, x, y)

            sag_res, hog_res, _ = self.section.calculate_ultimate_bending_capacity()
            phi_mn_top = self.phi * abs(hog_res.m_x) / 1e6
            phi_mn_bot = self.phi * abs(sag_res.m_x) / 1e6

            if phi_mn_top >= mu_top:
                top_passed = True
            elif not top_passed:
                next_top = self._sanitize_bar_count(n_top + 1)
                if next_top > self.max_3_layer_bars:
                    top_max_reached = top_passed = True
                else:
                    n_top = next_top

            if phi_mn_bot >= mu_bot:
                bot_passed = True
            elif not bot_passed:
                next_bot = self._sanitize_bar_count(n_bot + 1)
                if next_bot > self.max_3_layer_bars:
                    bot_max_reached = bot_passed = True
                else:
                    n_bot = next_bot

        return {
            "n_top_bars": "Max Reached" if top_max_reached else n_top,
            "n_bot_bars": "Max Reached" if bot_max_reached else n_bot,
            "as_top_mm2": "Max Reached" if top_max_reached else n_top * area_single,
            "as_bot_mm2": "Max Reached" if bot_max_reached else n_bot * area_single,
            "phi_Mn_top_kNm": "Max Reached" if top_max_reached else phi_mn_top,
            "phi_Mn_bot_kNm": "Max Reached" if bot_max_reached else phi_mn_bot,
            "Mu_top_kNm": mu_top,
            "Mu_bot_kNm": mu_bot,
            "Mu_top_original_kNm": mu_top_orig,
            "Mu_bot_original_kNm": mu_bot_orig,
            "top_coords": top_coords,
            "bot_coords": bot_coords,
        }

    def design_beam_flexure(
        self, demands: dict, original_demands: dict = None, evaluator=None
    ) -> dict:
        original_demands = original_demands or demands
        return {
            loc: self.design_flexure_location(
                demands.get(loc, {}).get("top", 0.0),
                demands.get(loc, {}).get("bot", 0.0),
                location_name=loc,
                mu_top_orig=original_demands.get(loc, {}).get("top", 0.0),
                mu_bot_orig=original_demands.get(loc, {}).get("bot", 0.0),
                evaluator=evaluator,
            )
            for loc in ["Left", "Mid", "Right"]
        }

    def synchronize_with_shear_design(
        self, flexural_results: dict, shear_results: dict
    ) -> dict:
        """Synchronizes flexural bar counts across Left, Mid, and Right with uniform shear leg profile."""
        updated_results = flexural_results.copy()
        area_single = (math.pi / 4.0) * (self.db**2)

        governing_legs = shear_results.get("Governing_Uniform_Legs", 2)
        target_legs = min(governing_legs, self.max_bars_per_layer)

        for loc in ["Left", "Mid", "Right"]:
            if loc not in updated_results:
                continue

            curr_n_top = updated_results[loc]["n_top_bars"]
            curr_n_bot = updated_results[loc]["n_bot_bars"]

            new_n_top = (
                max(curr_n_top, target_legs)
                if isinstance(curr_n_top, int)
                else curr_n_top
            )
            new_n_bot = (
                max(curr_n_bot, target_legs)
                if isinstance(curr_n_bot, int)
                else curr_n_bot
            )

            if new_n_top != curr_n_top or new_n_bot != curr_n_bot:
                self.section.reinforced_concrete_beam_rebar_config = (
                    self.section.create_concrete_section()
                )
                top_coords = self.generate_layer_coordinates(new_n_top, is_top=True)
                bot_coords = self.generate_layer_coordinates(new_n_bot, is_top=False)

                for x, y in top_coords + bot_coords:
                    self.section.add_bars(area_single, x, y)

                sag_res, hog_res, _ = self.section.calculate_ultimate_bending_capacity()

                updated_results[loc].update(
                    {
                        "n_top_bars": new_n_top,
                        "n_bot_bars": new_n_bot,
                        "as_top_mm2": new_n_top * area_single,
                        "as_bot_mm2": new_n_bot * area_single,
                        "phi_Mn_top_kNm": self.phi * abs(hog_res.m_x) / 1e6,
                        "phi_Mn_bot_kNm": self.phi * abs(sag_res.m_x) / 1e6,
                        "top_coords": top_coords,
                        "bot_coords": bot_coords,
                        "shear_leg_sync_note": (
                            f"Longitudinal bars updated to at least {target_legs} bars "
                            f"to engage uniform shear leg profile ({governing_legs} legs)."
                        ),
                    }
                )

        return updated_results


# =========================================================================
# 3. SHEAR DESIGN MODULE
# =========================================================================
class BeamShearDesign:
    """Calculates concrete/steel shear capacities and stirrup/hoop spacing limits per ACI 318M-14."""

    def __init__(
        self,
        section: BeamSectionGeometry,
        flexural_designer: BeamFlexuralDesign = None,
        n_legs: int = 2,
        d_stirrup: float = 10.0,
        fyt: float = 413.69,
        phi_shear: float = 0.75,
    ):
        self.section = section
        self.flexural_designer = flexural_designer
        self.n_legs = n_legs
        self.d_stirrup = d_stirrup
        self.fyt = fyt
        self.phi = phi_shear

        self.a_stirrup_single = (math.pi / 4.0) * (self.d_stirrup**2)
        self.av = self.n_legs * self.a_stirrup_single

    def calculate_vc(self, d_eff: float, lambda_factor: float = 1.0) -> float:
        fc_term = math.sqrt(self.section.fc)
        vc_n = 0.17 * lambda_factor * fc_term * self.section.width * d_eff
        return vc_n / 1000.0

    def design_stirrup_spacing(
        self,
        vu_analysis: float,
        d_eff: float,
        ve_seismic: float = 0.0,
        current_long_bars: int = 2,
        is_plastic_hinge: bool = False,
        db_main_min: float = 20.0,
        vc_override: float = None,
        lambda_factor: float = 1.0,
        s_min_practical: float = 100.0,
    ) -> dict:
        vu_design = max(vu_analysis, ve_seismic)
        governing_type = (
            "Seismic Probable Shear (Ve)"
            if ve_seismic >= vu_analysis
            else "Analysis Factored Shear (Vu)"
        )

        max_legs = (
            self.flexural_designer.max_bars_per_layer if self.flexural_designer else 4
        )
        vc = (
            vc_override
            if vc_override is not None
            else self.calculate_vc(d_eff, lambda_factor=lambda_factor)
        )

        vn_required = vu_design / self.phi
        vs_required = max(0.0, vn_required - vc)

        vs_max_limit = (
            0.66 * math.sqrt(self.section.fc) * self.section.width * d_eff / 1000.0
        )
        if vs_required > vs_max_limit:
            return {
                "Status": "SECTION_TOO_SMALL",
                "Error": "Required Vs exceeds maximum allowable limit.",
            }

        current_legs = self.n_legs
        while current_legs <= max_legs:
            av_current = current_legs * self.a_stirrup_single
            s_strength = (
                (av_current * self.fyt * d_eff) / (vs_required * 1000.0)
                if vs_required > 0
                else 9999.0
            )

            s_min_1 = (av_current * self.fyt) / (
                0.062 * math.sqrt(self.section.fc) * self.section.width
            )
            s_min_2 = (av_current * self.fyt) / (0.35 * self.section.width)
            s_min_reinf = min(s_min_1, s_min_2)

            if is_plastic_hinge:
                s_max_code = min(d_eff / 4.0, 6.0 * db_main_min, 150.0)
                zone_desc = "Seismic Plastic Hinge (Within 2H - Clause: 18.6.4.4)"
            else:
                vs_threshold = (
                    0.33
                    * math.sqrt(self.section.fc)
                    * self.section.width
                    * d_eff
                    / 1000.0
                )
                s_max_code = (
                    min(d_eff / 4.0, 300.0)
                    if vs_required > vs_threshold
                    else min(d_eff / 2.0, 600.0)
                )
                zone_desc = "Standard Non-Hinge Zone (Clause: 9.7.6.2.2)"

            s_governing = min(s_strength, s_min_reinf, s_max_code)
            if s_governing >= s_min_practical or vs_required == 0:
                break
            current_legs += 1

        current_legs = min(current_legs, max_legs)

        required_long_bars_for_legs = current_legs
        additional_long_bars_needed = max(0, current_legs - current_long_bars)

        s_provided = max(50.0, math.floor(s_governing / 25.0) * 25.0)
        av_final = current_legs * self.a_stirrup_single
        vs_provided = (av_final * self.fyt * d_eff) / (s_provided * 1000.0)
        phi_vn_provided = self.phi * (vc + vs_provided)

        return {
            "Zone": zone_desc,
            "Demand_Selection": {
                "Vu_analysis_kN": round(vu_analysis, 2),
                "Ve_seismic_kN": round(ve_seismic, 2),
                "Vu_governing_kN": round(vu_design, 2),
                "Governing_Demand": governing_type,
            },
            "n_legs": current_legs,
            "max_allowable_legs": max_legs,
            "s_provided_mm": int(s_provided),
            "Av_mm2": av_final,
            "Vc_kN": round(vc, 2),
            "Vs_provided_kN": round(vs_provided, 2),
            "phi_Vn_kN": round(phi_vn_provided, 2),
            "Vu_design_kN": round(vu_design, 2),
            "DCR_Shear": round(vu_design / phi_vn_provided, 3),
            "Pass": phi_vn_provided >= vu_design,
            "current_long_bars": current_long_bars,
            "required_long_bars": required_long_bars_for_legs,
            "additional_long_bars_needed": additional_long_bars_needed,
        }

    def design_beam_shear(
        self,
        shear_demands: dict,
        ve_seismic: float,
        d_eff: float,
        long_bars_map: dict,
        db_main_min: float = 20.0,
        vc_override: float = None,
        s_min_practical: float = 100.0,
    ) -> dict:
        zones_config = [
            (
                "Left",
                "At_Column_Face",
                shear_demands["Left"]["v_face"],
                ve_seismic,
                True,
            ),
            (
                "Left",
                "Plastic_Hinge_at_d",
                shear_demands["Left"]["v_d"],
                ve_seismic,
                True,
            ),
            ("Left", "Beyond_Hinge_at_2H", shear_demands["Left"]["v_2h"], 0.0, False),
            (
                "Right",
                "At_Column_Face",
                shear_demands["Right"]["v_face"],
                ve_seismic,
                True,
            ),
            (
                "Right",
                "Plastic_Hinge_at_d",
                shear_demands["Right"]["v_d"],
                ve_seismic,
                True,
            ),
            ("Right", "Beyond_Hinge_at_2H", shear_demands["Right"]["v_2h"], 0.0, False),
        ]

        # Trial Pass
        trial_legs = []
        for side, _, vu, ve, is_hinge in zones_config:
            bars = long_bars_map.get(side, 2)
            res = self.design_stirrup_spacing(
                vu,
                d_eff,
                ve,
                bars,
                is_hinge,
                db_main_min,
                vc_override,
                1.0,
                s_min_practical,
            )
            trial_legs.append(res["n_legs"])

        # Lock uniform governing legs
        global_max_legs = max(trial_legs)
        self.n_legs = global_max_legs

        # Final Pass
        results = {"Governing_Uniform_Legs": global_max_legs, "Left": {}, "Right": {}}
        for side, zone_name, vu, ve, is_hinge in zones_config:
            bars = long_bars_map.get(side, 2)
            results[side][zone_name] = self.design_stirrup_spacing(
                vu,
                d_eff,
                ve,
                bars,
                is_hinge,
                db_main_min,
                vc_override,
                1.0,
                s_min_practical,
            )

        return results


# =========================================================================
# 4. BEAM TORSION DESIGN MODULE
# =========================================================================
class BeamTorsionDesign:
    """Handles torsional strength, threshold/cracking evaluations, combined shear-torsion
    section checks, transverse At/s and longitudinal Al requirements per ACI 318M-14.
    """

    def __init__(
        self,
        section: BeamSectionGeometry,
        clear_cover: float = 40.0,
        d_stirrup: float = 10.0,
        fyt: float = 413.69,
        fy_long: float = 413.69,
        phi_torsion: float = 0.75,
        lambda_factor: float = 1.0,
    ):
        self.section = section
        self.clear_cover = clear_cover
        self.d_stirrup = d_stirrup
        self.fyt = fyt
        self.fy_long = fy_long
        self.phi = phi_torsion
        self.lambda_factor = lambda_factor

        self.b_w = section.width
        self.h = section.depth
        self.A_cp = self.b_w * self.h
        self.p_cp = 2.0 * (self.b_w + self.h)

        self.x_1 = self.b_w - 2.0 * self.clear_cover - self.d_stirrup
        self.y_1 = self.h - 2.0 * self.clear_cover - self.d_stirrup
        self.A_oh = max(0.0, self.x_1 * self.y_1)
        self.p_h = 2.0 * (self.x_1 + self.y_1)
        self.A_o = 0.85 * self.A_oh

    def calculate_t_th(self) -> float:
        t_th_n = (
            0.083
            * self.lambda_factor
            * math.sqrt(self.section.fc)
            * ((self.A_cp**2) / self.p_cp)
        )
        return t_th_n / 1e6

    def calculate_t_cr(self) -> float:
        t_cr_n = (
            0.33
            * self.lambda_factor
            * math.sqrt(self.section.fc)
            * ((self.A_cp**2) / self.p_cp)
        )
        return t_cr_n / 1e6

    def evaluate_torsion_demand(
        self, tu_input: float, reduce_to_phi_tcr: bool = True
    ) -> dict:
        t_th = self.calculate_t_th()
        t_cr = self.calculate_t_cr()
        phi_t_th = self.phi * t_th
        phi_t_cr = self.phi * t_cr

        requires_torsion = tu_input > phi_t_th
        tu_design = tu_input
        is_capped = False

        if requires_torsion and tu_input > phi_t_cr and reduce_to_phi_tcr:
            tu_design = phi_t_cr
            is_capped = True

        return {
            "Tu_input_kNm": round(tu_input, 2),
            "T_th_kNm": round(t_th, 2),
            "T_th_Equation": "Tth = 0.083 * lambda * sqrt(f'c) * (Acp^2 / pcp) (Clause: 22.7.4.1)",
            "phi_T_th_kNm": round(phi_t_th, 2),
            "T_cr_kNm": round(t_cr, 2),
            "T_cr_Equation": "Tcr = 0.33 * lambda * sqrt(f'c) * (Acp^2 / pcp) (Clause: 22.7.5.1)",
            "phi_T_cr_kNm": round(phi_t_cr, 2),
            "Requires_Torsion_Reinf": requires_torsion,
            "Reduce_to_phi_Tcr_Flag": reduce_to_phi_tcr,
            "Tu_is_Capped": is_capped,
            "Tu_design_kNm": round(tu_design, 2),
            "Tu_Reduction_Clause": "Clause: 22.7.3.2",
        }

    def check_combined_shear_torsion_dimensions(
        self, tu_design: float, vu_demand: float, d_eff: float
    ) -> dict:
        vu_n = vu_demand * 1000.0
        tu_n = tu_design * 1e6
        vc_n = 0.17 * self.lambda_factor * math.sqrt(self.section.fc) * self.b_w * d_eff

        shear_stress_sq = (vu_n / (self.b_w * d_eff)) ** 2
        torsion_stress_sq = ((tu_n * self.p_h) / (1.7 * (self.A_oh**2))) ** 2
        lhs_stress = math.sqrt(shear_stress_sq + torsion_stress_sq)

        rhs_stress = self.phi * (
            (vc_n / (self.b_w * d_eff)) + 0.66 * math.sqrt(self.section.fc)
        )

        return {
            "LHS_Combined_Stress_MPa": round(lhs_stress, 3),
            "LHS_Equation": "sqrt((Vu / (bw * d))^2 + ((Tu * ph) / (1.7 * Aoh^2))^2) (Clause: 22.7.7.1)",
            "RHS_Allowable_Limit_MPa": round(rhs_stress, 3),
            "RHS_Equation": "phi * ((Vc / (bw * d)) + 0.66 * sqrt(f'c)) (Clause: 22.7.7.1)",
            "Section_Adequate": lhs_stress <= rhs_stress,
            "Clause": "Clause: 22.7.7.1",
        }

    def design_torsion_reinforcement(
        self, tu_design: float, vu_demand: float, d_eff: float, theta_deg: float = 45.0
    ) -> dict:
        eval_demand = self.evaluate_torsion_demand(tu_design, True)
        if not eval_demand["Requires_Torsion_Reinf"]:
            return {
                "Torsion_Required": False,
                "Tu_design_kNm": eval_demand["Tu_design_kNm"],
                "At_over_s_mm2_per_mm": 0.0,
                "Al_req_mm2": 0.0,
                "Al_min_mm2": 0.0,
                "Al_governing_mm2": 0.0,
                "Combined_Transverse_Minimum": "N/A",
                "Perimeter_Spacing_Pass": True,
            }

        tu_n = eval_demand["Tu_design_kNm"] * 1e6
        theta_rad = math.radians(theta_deg)

        at_over_s_req = tu_n / (
            2.0 * self.phi * self.A_o * self.fyt * (1.0 / math.tan(theta_rad))
        )

        al_calculated = (
            at_over_s_req
            * self.p_h
            * (self.fyt / self.fy_long)
            * ((1.0 / math.tan(theta_rad)) ** 2)
        )

        at_s_min_clause = (0.175 * self.b_w) / self.fyt
        at_s_for_al_min = max(at_over_s_req, at_s_min_clause)

        al_min = ((0.42 * math.sqrt(self.section.fc) * self.A_cp) / self.fy_long) - (
            at_s_for_al_min * self.p_h * (self.fyt / self.fy_long)
        )
        al_min = max(0.0, al_min)
        al_governing = max(al_calculated, al_min)

        dim_check = self.check_combined_shear_torsion_dimensions(
            eval_demand["Tu_design_kNm"], vu_demand, d_eff
        )

        combined_min_1 = (0.062 * math.sqrt(self.section.fc) * self.b_w) / self.fyt
        combined_min_2 = (0.35 * self.b_w) / self.fyt
        combined_transverse_min = max(combined_min_1, combined_min_2)

        s_max_torsion = min(self.p_h / 8.0, 300.0)
        max_perimeter_spacing_limit = 300.0

        return {
            "Torsion_Required": True,
            "Demand_Evaluation": eval_demand,
            "Dimension_Check": dim_check,
            "At_over_s_mm2_per_mm": round(at_over_s_req, 4),
            "At_over_s_Equation": "At / s = Tu / (2 * phi * Ao * fyt * cot(theta)) (Clause: 22.7.6.1)",
            "Al_calculated_mm2": round(al_calculated, 2),
            "Al_calculated_Equation": "Al = (At / s) * ph * (fyt / fy) * cot^2(theta) (Clause: 22.7.6.1)",
            "Al_min_mm2": round(al_min, 2),
            "Al_min_Equation": "Al,min = (0.42 * sqrt(f'c) * Acp / fy) - (At / s) * ph * (fyt / fy) (Clause: 9.6.4.3 & 22.7.6.2.5)",
            "Al_governing_mm2": round(al_governing, 2),
            "Combined_Transverse_Min_req_mm2_per_mm": round(combined_transverse_min, 4),
            "Combined_Transverse_Min_Equation": "((Av + 2*At) / s)_min = max(0.062 * sqrt(f'c) * bw / fyt, 0.35 * bw / fyt) (Clause: 9.6.4.2)",
            "Stirrup_Max_Spacing_Torsion_mm": round(s_max_torsion, 2),
            "Stirrup_Max_Spacing_Equation": "s_max = min(ph / 8, 300 mm) (Clause: 9.7.6.3.3)",
            "Perimeter_Long_Spacing_Limit_mm": max_perimeter_spacing_limit,
            "Perimeter_Long_Spacing_Clause": "Clause: 9.7.6.3.4",
            "Note_Longitudinal_Reinf": f"Required longitudinal torsion reinf = {round(al_governing, 2)} mm²",
        }


# =========================================================================
# 5. CODE CHECK & OVERWRITE EVALUATOR SERVICE
# =========================================================================
class CodeCheckEvaluator:
    """Evaluates individual ACI 318M-14 code provisions, dimensional limits,
    flexural/shear/torsional DCRs, Mpr capacities, and Ve seismic shear demands.
    """

    def __init__(self, section: BeamSectionGeometry):
        self.section = section

    def s_max(self, clear_cover: float = 40.0, fs: float = None) -> float:
        if fs is None or fs <= 0:
            fs = (2.0 / 3.0) * self.section.fy
        s1 = 380.0 * (280.0 / fs) - 2.5 * clear_cover
        s2 = 300.0 * (280.0 / fs)
        return min(s1, s2)

    def calculate_s_clear_min(self, db: float, d_agg: float = 20.0) -> float:
        return max(25.0, db, (4.0 / 3.0) * d_agg)

    def get_max_bars_per_layer(
        self,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
        d_stirrup: float = 10.0,
        d_agg: float = 20.0,
    ) -> int:
        s_clear_min = self.calculate_s_clear_min(db_min, d_agg)
        s_center_min = s_clear_min + db_min
        w_avail = self.section.width - 2.0 * (clear_cover + d_stirrup) - db_min
        return int(math.floor(w_avail / s_center_min)) + 1

    def check_crack_control_spacing_all(
        self,
        flex_results: dict,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
        d_stirrup: float = 10.0,
    ) -> dict:
        fs_service = (2.0 / 3.0) * self.section.fy
        s_max_limit = self.s_max(clear_cover=clear_cover, fs=fs_service)

        max_per_layer = self.get_max_bars_per_layer(db_min, clear_cover, d_stirrup)
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min

        min_bars_req_crack = (
            int(math.ceil(w_avail / s_max_limit)) + 1 if s_max_limit > 0 else 2
        )
        min_bars_req_crack = max(2, min_bars_req_crack)

        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_as_layer_1_req = min_bars_req_crack * area_single_bar

        audits = {}
        all_pass = True
        overall_min_as_layer_1_req = min_as_layer_1_req

        targets = [("Left", "top"), ("Mid", "bot"), ("Right", "top")]

        for loc, face in targets:
            n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
            if not isinstance(n_bars, int):
                n_bars = 2

            l1_bars = min(n_bars, max_per_layer)
            s_provided_cc = w_avail / (l1_bars - 1) if l1_bars > 1 else w_avail

            pass_check = s_provided_cc <= s_max_limit
            if not pass_check:
                all_pass = False

            if loc not in audits:
                audits[loc] = {}

            audits[loc][face] = {
                "n_bars_tension_layer_1": l1_bars,
                "s_provided_center_to_center_mm": round(s_provided_cc, 2),
                "s_max_limit_mm": round(s_max_limit, 2),
                "s_max_Equation": "s_max = min(380 * (280 / fs) - 2.5 * cc, 300 * (280 / fs)) (Clause: 24.3.2)",
                "min_required_bars_layer_1": min_bars_req_crack,
                "min_As_layer_1_req_mm2": round(min_as_layer_1_req, 2),
                "Pass": pass_check,
            }

        return {
            "Overall_Pass": all_pass,
            "min_required_bars_layer_1": min_bars_req_crack,
            "min_As_layer_1_req_mm2": round(overall_min_as_layer_1_req, 2),
            "Tension_Face_Locations": audits,
            "Clause": "Clause: 9.7.2.2 & 24.3",
        }

    def check_compression_lateral_support(
        self,
        flex_results: dict,
        shear_results: dict,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
        d_stirrup: float = 10.0,
    ) -> dict:
        audits = {}
        all_pass = True
        overall_min_required_legs = 2

        max_per_layer = self.get_max_bars_per_layer(db_min, clear_cover, d_stirrup)
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min
        area_stirrup_single = (math.pi / 4.0) * (d_stirrup**2)

        provided_legs = 2
        if shear_results and "Governing_Uniform_Legs" in shear_results:
            provided_legs = shear_results["Governing_Uniform_Legs"]

        targets = [("Left", "bot"), ("Mid", "top"), ("Right", "bot")]

        for loc, face in targets:
            n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
            if not isinstance(n_bars, int):
                n_bars = 2

            l1_bars = min(n_bars, max_per_layer)

            if l1_bars <= 2:
                total_legs_req_alternating = 2
                clear_dist_unanchored = 0.0
                pass_150mm = True
                pass_legs = provided_legs >= 2
                min_legs_loc = 2
                recommendation = "Complies: 2 corner legs satisfy perimeter support."
            else:
                s_cc_adj = w_avail / (l1_bars - 1)
                clear_dist_adj = s_cc_adj - db_min

                interior_legs_req = (l1_bars - 2) // 2
                total_legs_req_alternating = 2 + interior_legs_req

                pass_legs = provided_legs >= total_legs_req_alternating

                if clear_dist_adj <= 150.0:
                    pass_150mm = True
                    min_legs_loc = total_legs_req_alternating
                else:
                    pass_150mm = False
                    min_legs_loc = l1_bars

                if provided_legs >= l1_bars:
                    clear_dist_unanchored = clear_dist_adj
                    recommendation = "Complies: All main bars are laterally anchored by stirrup legs."
                else:
                    clear_dist_unanchored = clear_dist_adj

                    if clear_dist_unanchored <= 150.0:
                        if pass_legs:
                            recommendation = "Complies: Alternating ties satisfied and clear distance <= 150mm."
                        else:
                            recommendation = (
                                f"Non-compliant: Provide at least {total_legs_req_alternating} legs "
                                f"to satisfy alternating bar support (Clause: 9.7.6.4.2)."
                            )
                    else:
                        recommendation = (
                            f"Non-compliant: Clear distance ({round(clear_dist_unanchored, 1)} mm) exceeds 150 mm limit. "
                            f"Suggest providing stirrup/crosstie legs to ALL {l1_bars} main bars in Layer 1 (Clause: 9.7.6.4.3)."
                        )

            overall_min_required_legs = max(overall_min_required_legs, min_legs_loc)
            min_av_req_loc = min_legs_loc * area_stirrup_single

            pass_loc = pass_legs and pass_150mm
            if not pass_loc:
                all_pass = False

            if loc not in audits:
                audits[loc] = {}

            audits[loc][face] = {
                "n_compression_bars_layer_1": l1_bars,
                "legs_provided": provided_legs,
                "alternating_legs_required": total_legs_req_alternating,
                "alternating_legs_Pass": pass_legs,
                "clear_dist_unanchored_to_anchored_bar_mm": round(
                    clear_dist_unanchored, 2
                ),
                "max_clear_limit_without_ties_mm": 150.0,
                "clear_distance_150mm_Pass": pass_150mm,
                "min_required_legs": min_legs_loc,
                "min_Av_req_mm2": round(min_av_req_loc, 2),
                "Pass": pass_loc,
                "Recommendation_Note": recommendation,
                "Clause": "Clause: 9.7.6.4, 9.7.6.4.2 & 9.7.6.4.3",
            }

        overall_min_av_req = overall_min_required_legs * area_stirrup_single

        return {
            "Overall_Pass": all_pass,
            "min_required_legs": overall_min_required_legs,
            "min_Av_req_mm2": round(overall_min_av_req, 2),
            "Compression_Zone_Locations": audits,
            "Clause": "Clause: 9.7.6.4",
        }

    def check_seismic_supported_bar_spacing_all(
        self,
        flex_results: dict,
        shear_results: dict,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
        d_stirrup: float = 10.0,
    ) -> dict:
        audits = {}
        all_pass = True
        overall_min_required_legs = 2

        max_per_layer = self.get_max_bars_per_layer(db_min, clear_cover, d_stirrup)
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min
        area_stirrup_single = (math.pi / 4.0) * (d_stirrup**2)

        provided_legs = 2
        if shear_results and "Governing_Uniform_Legs" in shear_results:
            provided_legs = shear_results["Governing_Uniform_Legs"]

        targets = [
            ("Left", "top"),
            ("Left", "bot"),
            ("Mid", "top"),
            ("Mid", "bot"),
            ("Right", "top"),
            ("Right", "bot"),
        ]

        def distribute_legs_uniformly(n_bars: int, num_legs: int) -> list[int]:
            if n_bars <= 2 or num_legs <= 2:
                return [0, n_bars - 1] if n_bars >= 2 else [0]
            if num_legs >= n_bars:
                return list(range(n_bars))

            supported = {0, n_bars - 1}
            interior_legs_to_place = min(num_legs - 2, n_bars - 2)

            indices = np.linspace(0, n_bars - 1, interior_legs_to_place + 2)
            for idx in indices:
                supported.add(int(round(idx)))

            return sorted(list(supported))

        def evaluate_layout(l1_bars: int, legs_to_test: int):
            if l1_bars <= 1:
                return True, True, 0.0, 0.0, [0], ""

            s_cc_adj = w_avail / (l1_bars - 1)
            clear_dist_adj = s_cc_adj - db_min

            supported_indices = distribute_legs_uniformly(l1_bars, legs_to_test)

            has_unanchored_bars = len(supported_indices) < l1_bars
            if has_unanchored_bars:
                max_unanchored_clear = clear_dist_adj
                pass_150 = max_unanchored_clear <= 150.0
            else:
                max_unanchored_clear = 0.0
                pass_150 = True

            max_supported_cc = 0.0
            for k in range(len(supported_indices) - 1):
                i_a = supported_indices[k]
                i_b = supported_indices[k + 1]
                gap_cc = (i_b - i_a) * s_cc_adj
                if gap_cc > max_supported_cc:
                    max_supported_cc = gap_cc

            pass_350 = max_supported_cc <= 350.0

            msg_parts = []
            if not pass_150:
                msg_parts.append(
                    f"Clear distance to unanchored bar ({round(max_unanchored_clear, 1)} mm) > 150 mm (Clause: 9.7.6.4.3)."
                )
            if not pass_350:
                msg_parts.append(
                    f"Supported bar C/C spacing ({round(max_supported_cc, 1)} mm) > 350 mm (Clause: 18.6.4.2)."
                )

            status_msg = (
                " ".join(msg_parts)
                if msg_parts
                else "Complies with ACI spacing limits."
            )
            return (
                pass_150,
                pass_350,
                max_unanchored_clear,
                max_supported_cc,
                supported_indices,
                status_msg,
            )

        for loc, face in targets:
            n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
            if not isinstance(n_bars, int):
                n_bars = 2

            l1_bars = min(n_bars, max_per_layer)

            p150, p350, max_clear_unsupp, max_cc_supp, supp_indices, status_note = (
                evaluate_layout(l1_bars, provided_legs)
            )

            suggested_legs = provided_legs
            retested_pass = p150 and p350

            if not retested_pass:
                while suggested_legs < l1_bars:
                    suggested_legs += 1
                    sp150, sp350, _, _, _, _ = evaluate_layout(l1_bars, suggested_legs)
                    if sp150 and sp350:
                        retested_pass = True
                        break

                if retested_pass:
                    recommendation = (
                        f"Non-compliant with {provided_legs} legs. {status_note} "
                        f"SUGGESTION: Increase to {suggested_legs} legs (uniformly distributed at bar indices {distribute_legs_uniformly(l1_bars, suggested_legs)}). "
                        f"Re-evaluation with {suggested_legs} legs satisfies all 150mm clear & 350mm C/C limits."
                    )
                else:
                    suggested_legs = l1_bars
                    recommendation = (
                        f"Non-compliant. {status_note} "
                        f"SUGGESTION: Provide crosstie legs to ALL {l1_bars} main bars in Layer 1."
                    )
            else:
                recommendation = "Complies: Provided uniform leg distribution satisfies 150mm clear & 350mm C/C limits."

            min_legs_loc = suggested_legs if not (p150 and p350) else provided_legs
            overall_min_required_legs = max(overall_min_required_legs, min_legs_loc)
            min_av_req_loc = min_legs_loc * area_stirrup_single

            pass_loc = p150 and p350
            if not pass_loc:
                all_pass = False

            if loc not in audits:
                audits[loc] = {}

            audits[loc][face] = {
                "n_bars_layer_1": l1_bars,
                "legs_provided": provided_legs,
                "anchored_bar_indices": supp_indices,
                "max_clear_dist_unanchored_bar_mm": round(max_clear_unsupp, 2),
                "clear_limit_150mm_Pass": p150,
                "max_supported_bar_cc_spacing_mm": round(max_cc_supp, 2),
                "code_limit_350mm_Pass": p350,
                "min_required_legs": min_legs_loc,
                "min_Av_req_mm2": round(min_av_req_loc, 2),
                "Spacing_Equation": "s_supported_cc <= 350 mm (Clause: 18.6.4.2) & s_clear_unanchored <= 150 mm (Clause: 9.7.6.4.3)",
                "Pass": pass_loc,
                "Recommendation_Note": recommendation,
                "Clause": "Clause: 18.6.4.2, 25.7.2.4 & 9.7.6.4.3",
            }

        overall_min_av_req = overall_min_required_legs * area_stirrup_single

        return {
            "Overall_Pass": all_pass,
            "min_required_legs": overall_min_required_legs,
            "min_Av_req_mm2": round(overall_min_av_req, 2),
            "Locations": audits,
            "Clause": "Clause: 18.6.4.2 & 25.7.2.4",
        }

    def calculate_as_min(self, d_eff: float) -> float:
        eq_a = (0.25 * math.sqrt(self.section.fc) / self.section.fy) * (
            self.section.width * d_eff
        )
        eq_b = (1.4 / self.section.fy) * (self.section.width * d_eff)
        return max(eq_a, eq_b)

    def check_min_flexural_reinforcement(
        self, as_map: dict, d_eff: float, db_min: float = 20.0
    ) -> dict:
        as_min = self.calculate_as_min(d_eff)
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_bars_req = max(2, int(math.ceil(as_min / area_single_bar)))

        audits = {}
        all_pass = True

        for loc, data in as_map.items():
            audits[loc] = {}
            for face in ["top", "bot"]:
                as_prov = data.get(f"as_{face}_mm2", 0.0)
                if not isinstance(as_prov, (int, float)):
                    as_prov = 0.0
                pass_check = as_prov >= as_min
                if not pass_check:
                    all_pass = False
                audits[loc][face] = {
                    "As_Provided_mm2": round(as_prov, 2),
                    "As_Min_mm2": round(as_min, 2),
                    "As_Min_Equation": "As,min = max(0.25 * sqrt(f'c) / fy * bw * d, 1.4 / fy * bw * d) (Clause: 9.6.1.2)",
                    "min_bars_required": min_bars_req,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "as_min_mm2": round(as_min, 2),
            "min_bars_required": min_bars_req,
            "Locations": audits,
            "Clause": "Clause: 9.6.1",
        }

    def check_min_bar_spacing_all(
        self, flex_results: dict, db_min: float = 20.0, d_agg: float = 20.0
    ) -> dict:
        audits = {}
        all_pass = True

        clear_cover = 40.0
        d_stirrup = 10.0
        w_avail = self.section.width - 2.0 * (clear_cover + d_stirrup) - db_min
        s_clear_min = self.calculate_s_clear_min(db_min, d_agg)

        s_center_min = s_clear_min + db_min
        max_bars_per_layer = (
            int(math.floor(w_avail / s_center_min)) + 1 if s_center_min > 0 else 2
        )

        area_single_bar = (math.pi / 4.0) * (db_min**2)
        max_as_capacity_layer_1 = max_bars_per_layer * area_single_bar

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                n_bars_key = f"n_{face}_bars"
                n_bars = flex_results.get(loc, {}).get(n_bars_key, 2)
                if not isinstance(n_bars, int):
                    n_bars = 2

                l1_bars = min(n_bars, max_bars_per_layer)

                if l1_bars > 1:
                    center_spacing = w_avail / (l1_bars - 1)
                    s_provided = center_spacing - db_min
                else:
                    s_provided = w_avail

                pass_check = s_provided >= s_clear_min
                if not pass_check:
                    all_pass = False

                audits[loc][face] = {
                    "n_bars_total": n_bars,
                    "n_bars_layer_1": l1_bars,
                    "db_mm": db_min,
                    "s_provided_clear_mm": round(s_provided, 2),
                    "s_clear_min_req_mm": round(s_clear_min, 2),
                    "s_clear_min_Equation": "s_clear_min = max(25 mm, db, 4/3 * d_agg) (Clause: 25.2.1)",
                    "max_bars_per_layer": max_bars_per_layer,
                    "max_As_capacity_layer_1_mm2": round(max_as_capacity_layer_1, 2),
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "max_bars_per_layer": max_bars_per_layer,
            "max_As_capacity_layer_1_mm2": round(max_as_capacity_layer_1, 2),
            "Locations": audits,
            "Clause": "Clause: 9.7.2.1 & 25.2",
        }

    def check_max_transverse_spacing_all(
        self, shear_results: dict, d_eff: float
    ) -> dict:
        if not shear_results or "Left" not in shear_results:
            return {
                "Overall_Pass": True,
                "Locations": "No Shear Results Available",
                "Clause": "Clause: 9.7.6.2.2 & Table 9.7.6.2.2",
            }

        vs_threshold = (
            0.33 * math.sqrt(self.section.fc) * self.section.width * d_eff / 1000.0
        )

        audits = {}
        all_pass = True

        zones_to_check = [
            ("Plastic_Hinge_at_d", "Plastic Hinge (at d)"),
            ("Beyond_Hinge_at_2H", "Beyond Hinge (at 2H)"),
        ]

        for loc in ["Left", "Right"]:
            audits[loc] = {}
            for zone_key, zone_label in zones_to_check:
                zone_data = shear_results.get(loc, {}).get(zone_key, {})
                s_provided = zone_data.get("s_provided_mm", 0)
                vs_provided = zone_data.get("Vs_provided_kN", 0.0)

                if vs_provided > vs_threshold:
                    s_max_code = min(d_eff / 4.0, 300.0)
                    threshold_note = "Vs > 0.33*sqrt(f'c)*bw*d"
                else:
                    s_max_code = min(d_eff / 2.0, 600.0)
                    threshold_note = "Vs <= 0.33*sqrt(f'c)*bw*d"

                pass_check = s_provided <= s_max_code
                if not pass_check:
                    all_pass = False

                audits[loc][zone_label] = {
                    "s_provided_mm": s_provided,
                    "s_max_code_limit_mm": round(s_max_code, 2),
                    "s_max_Equation": (
                        "s_max = min(d/4, 300) if Vs > Vs_th else min(d/2, 600) "
                        "(Clause: 9.7.6.2.2)"
                    ),
                    "Vs_provided_kN": round(vs_provided, 2),
                    "Vs_threshold_kN": round(vs_threshold, 2),
                    "Vs_Condition": threshold_note,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "Clause: 9.7.6.2.2 & Table 9.7.6.2.2",
        }

    def check_flexural_dcrs_all(
        self,
        flex_results: dict,
        dcr_limit: float = 1.0,
        db_min: float = 20.0,
    ) -> dict:
        audits = {}
        all_pass = True
        overall_min_bars_required = 2
        overall_min_as_required = 0.0
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        phi_flexure = 0.9

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                mu_orig = flex_results.get(loc, {}).get(
                    f"Mu_{face}_original_kNm",
                    flex_results.get(loc, {}).get(f"Mu_{face}_kNm", 0.0),
                )
                phi_mn_val = flex_results.get(loc, {}).get(f"phi_Mn_{face}_kNm", 1.0)

                if not isinstance(phi_mn_val, (int, float)) or phi_mn_val <= 0:
                    dcr = 0.0
                else:
                    dcr = mu_orig / phi_mn_val

                pass_check = dcr <= dcr_limit
                if not pass_check:
                    all_pass = False

                mu_target_kNm = (
                    abs(mu_orig) / dcr_limit if dcr_limit > 0 else abs(mu_orig)
                )

                if mu_target_kNm > 0:
                    is_top_face = face == "top"
                    test_bars = 2
                    capacity_reached = False

                    while test_bars <= 20:
                        trial_sec = BeamSectionGeometry(
                            width=self.section.width,
                            depth=self.section.depth,
                            fc=self.section.fc,
                            fy=self.section.fy,
                            concrete_name=self.section.concrete_name,
                            rebar_name=self.section.rebar_name,
                        )

                        y_pos = (
                            self.section.depth - 40.0 - 10.0 - (db_min / 2.0)
                            if is_top_face
                            else 40.0 + 10.0 + (db_min / 2.0)
                        )
                        x_left = 40.0 + 10.0 + (db_min / 2.0)
                        x_right = self.section.width - (40.0 + 10.0) - (db_min / 2.0)
                        w_avail = x_right - x_left

                        for i in range(test_bars):
                            x_pos = (
                                x_left + i * (w_avail / (test_bars - 1))
                                if test_bars > 1
                                else x_left
                            )
                            trial_sec.add_bars(area_single_bar, x_pos, y_pos)

                        sag_res, hog_res, _ = (
                            trial_sec.calculate_ultimate_bending_capacity()
                        )
                        phi_mn_test = (
                            phi_flexure * abs(hog_res.m_x) / 1e6
                            if is_top_face
                            else phi_flexure * abs(sag_res.m_x) / 1e6
                        )

                        if phi_mn_test >= mu_target_kNm:
                            capacity_reached = True
                            break
                        test_bars += 1

                    min_bars_loc = test_bars if capacity_reached else 999
                else:
                    min_bars_loc = 2

                min_as_req_loc = min_bars_loc * area_single_bar

                overall_min_bars_required = max(overall_min_bars_required, min_bars_loc)
                overall_min_as_required = max(overall_min_as_required, min_as_req_loc)

                audits[loc][face] = {
                    "Mu_original_demand_kNm": round(mu_orig, 2),
                    "phi_Mn_capacity_kNm": (
                        round(phi_mn_val, 2)
                        if isinstance(phi_mn_val, (int, float))
                        else str(phi_mn_val)
                    ),
                    "DCR": round(dcr, 3),
                    "DCR_Equation": "DCR_flexure = Mu_original / phi_Mn (Clause: 9.5.1)",
                    "DCR_Limit": dcr_limit,
                    "min_bars_required": min_bars_loc,
                    "min_As_req_mm2": round(min_as_req_loc, 2),
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "min_bars_required": overall_min_bars_required,
            "min_As_req_mm2": round(overall_min_as_required, 2),
            "Locations": audits,
            "Clause": "Flexural DCR Limit",
        }

    def check_shear_dcrs_all(
        self,
        shear_results: dict,
        dcr_limit: float = 1.0,
        d_eff: float = 435.0,
        fyt: float = 413.69,
        phi_shear: float = 0.75,
        shear_designer: BeamShearDesign = None,
    ) -> dict:
        audits = {}
        all_pass = True
        overall_min_av_over_s = 0.0

        if not shear_results or "Left" not in shear_results:
            return {
                "Overall_Pass": True,
                "min_Av_over_s_req_mm2_per_mm": 0.0,
                "Locations": "No Shear Results Available",
                "Clause": "Shear DCR Limit",
            }

        if shear_designer is None:
            shear_designer = BeamShearDesign(
                section=self.section,
                fyt=fyt,
                phi_shear=phi_shear,
            )

        for loc in ["Left", "Right"]:
            audits[loc] = {}
            for zone_key, zone_data in shear_results[loc].items():
                if not isinstance(zone_data, dict):
                    continue

                demand_sel = zone_data.get("Demand_Selection", {})
                vu_gov = demand_sel.get("Vu_governing_kN", 0.0)

                gov_type = demand_sel.get(
                    "Governing_Type",
                    demand_sel.get("Governing_Demand_Type", "Factored Demand (Vu)"),
                )

                phi_vn = zone_data.get("phi_Vn_kN", 1.0)
                vc_kN = zone_data.get("Vc_kN", 0.0)

                dcr = zone_data.get("DCR_Shear", 0.0)
                pass_check = dcr <= dcr_limit
                if not pass_check:
                    all_pass = False

                target_vu_demand = vu_gov / dcr_limit if dcr_limit > 0 else vu_gov

                vn_required = target_vu_demand / shear_designer.phi
                vc_val = vc_kN if vc_kN > 0 else shear_designer.calculate_vc(d_eff)
                vs_required = max(0.0, vn_required - vc_val)

                if vs_required > 0 and d_eff > 0:
                    min_av_s_zone = (vs_required * 1000.0) / (
                        shear_designer.fyt * d_eff
                    )
                else:
                    min_av_s_zone = 0.0

                overall_min_av_over_s = max(overall_min_av_over_s, min_av_s_zone)

                audits[loc][zone_key] = {
                    "Vu_governing_kN": vu_gov,
                    "Governing_Demand_Type": gov_type,
                    "phi_Vn_capacity_kN": phi_vn,
                    "DCR": dcr,
                    "DCR_Equation": "DCR_shear = Vu / phi_Vn (Clause: 9.5.1)",
                    "DCR_Limit": dcr_limit,
                    "min_Av_over_s_req_mm2_per_mm": round(min_av_s_zone, 4),
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "min_Av_over_s_req_mm2_per_mm": round(overall_min_av_over_s, 4),
            "Locations": audits,
            "Clause": "Shear DCR Limit",
        }

    def check_torsional_dcr(
        self,
        tu: float = 0.0,
        torsion_res: dict = None,
        dcr_limit: float = 1.0,
        vu_demand: float = 0.0,
        d_eff: float = 435.0,
        torsion_designer: BeamTorsionDesign = None,
    ) -> dict:
        if not torsion_res or not torsion_res.get("Torsion_Required", False):
            return {
                "Tu_demand_kNm": tu,
                "DCR": 0.0,
                "DCR_Limit": dcr_limit,
                "min_bw_times_d_req_mm2": 0.0,
                "Pass": True,
                "Clause": "Torsion Exempt (Tu <= phi*Tth) (Clause: 22.7.4.1)",
            }

        dim_check = torsion_res.get("Dimension_Check", {})
        lhs = dim_check.get("LHS_Combined_Stress_MPa", 0.0)
        rhs = dim_check.get("RHS_Allowable_Limit_MPa", 1.0)
        dcr = lhs / rhs if rhs > 0 else 0.0
        pass_check = dcr <= dcr_limit

        if torsion_designer is None:
            torsion_designer = BeamTorsionDesign(
                section=self.section,
                phi_torsion=0.75,
            )

        eval_demand = torsion_designer.evaluate_torsion_demand(tu, True)
        tu_design = eval_demand["Tu_design_kNm"]

        if rhs > 0 and lhs > 0:
            target_dim_check = torsion_designer.check_combined_shear_torsion_dimensions(
                tu_design=tu_design,
                vu_demand=vu_demand,
                d_eff=d_eff,
            )
            lhs_calc = target_dim_check["LHS_Combined_Stress_MPa"]
            rhs_calc = target_dim_check["RHS_Allowable_Limit_MPa"]

            target_rhs_limit = rhs_calc * dcr_limit
            scale_factor = lhs_calc / target_rhs_limit if target_rhs_limit > 0 else 1.0

            current_bw_d = self.section.width * d_eff
            min_bw_d_req = current_bw_d * scale_factor
        else:
            min_bw_d_req = self.section.width * d_eff

        return {
            "Tu_demand_kNm": tu,
            "Combined_Stress_LHS_MPa": lhs,
            "Allowable_Limit_RHS_MPa": rhs,
            "DCR": round(dcr, 3),
            "DCR_Equation": "DCR_torsion = LHS_stress / RHS_limit (Clause: 22.7.7.1)",
            "DCR_Limit": dcr_limit,
            "min_bw_times_d_req_mm2": round(min_bw_d_req, 2),
            "Pass": pass_check,
            "Clause": "Clause: 22.7.7.1",
        }

    def calculate_mpr_capacities(
        self, flex_results: dict, db_min: float = 20.0
    ) -> tuple[float, float, float]:
        orig_fy = self.section.fy
        self.section.fy = 1.25 * orig_fy
        mpr_dict = {}

        try:
            for loc in ["Left", "Right"]:
                data = flex_results[loc]
                self.section.reinforced_concrete_beam_rebar_config = (
                    self.section.create_concrete_section()
                )
                area_single = (math.pi / 4.0) * (db_min**2)

                for x, y in data.get("top_coords", []) + data.get("bot_coords", []):
                    self.section.add_bars(area_single, x, y)

                sag_res, hog_res, _ = self.section.calculate_ultimate_bending_capacity()
                mpr_dict[loc] = max(abs(sag_res.m_xy) / 1e6, abs(hog_res.m_xy) / 1e6)
        finally:
            self.section.fy = orig_fy

        mpr_left = mpr_dict.get("Left", 0.0)
        mpr_right = mpr_dict.get("Right", 0.0)
        mpr_max = max(mpr_left, mpr_right)

        return mpr_left, mpr_right, mpr_max

    def calculate_seismic_shear_demand(
        self, mpr_left: float, mpr_right: float, clear_span: float, vg: float
    ) -> float:
        if clear_span <= 0:
            raise ValueError("Clear span Ln must be greater than zero.")
        return ((mpr_left + mpr_right) / clear_span) + abs(vg)

    def check_seismic_vc_zero(self, veq: float, vu: float, pu: float) -> dict:
        ag = self.section.width * self.section.depth
        low_axial = abs(pu) < (0.05 * self.section.fc * ag / 1000.0)
        high_shear = veq >= (0.5 * vu) if vu > 0 else False
        vc_is_zero = low_axial and high_shear

        return {
            "Low_Axial": low_axial,
            "Low_Axial_Equation": "Pu < 0.05 * f'c * Ag (Clause: 18.6.5.2a)",
            "High_Seismic_Shear": high_shear,
            "High_Seismic_Shear_Equation": "Veq >= 0.5 * Vu (Clause: 18.6.5.2b)",
            "Vc_Zero": vc_is_zero,
            "Clause": "Clause: 18.6.5.2",
        }

    def check_pu_limit(self, pu: float) -> dict:
        ag = self.section.width * self.section.depth
        pu_limit = 0.10 * self.section.fc * ag / 1000.0
        return {
            "Pu_kN": pu,
            "Limit_kN": round(pu_limit, 2),
            "Pu_Limit_Equation": "Pu_limit = 0.10 * f'c * Ag (Clause: 9.5.2)",
            "Pass": abs(pu) < pu_limit,
            "Clause": "Clause: 9.5.2",
        }

    def check_skin_reinforcement(
        self, clear_cover: float = 40.0, fs: float = None
    ) -> dict:
        required = self.section.depth > 900.0
        s_max_val = self.s_max(clear_cover, fs) if required else "N/A"
        return {
            "Depth_mm": self.section.depth,
            "Required": required,
            "Depth_Requirement_Equation": "h > 900 mm (Clause: 9.7.2.3)",
            "Max_Spacing_mm": s_max_val,
            "Clause": "Clause: 9.7.2.3",
        }

    def check_pos_reinf_support_extensions(
        self,
        as_bot_left: float,
        as_bot_mid: float,
        as_bot_right: float,
        fraction: str = "1/3",
        db_min: float = 20.0,
    ) -> dict:
        req_ratio = 1 / 3 if fraction == "1/3" else 1 / 4
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_2_bars_area = 2.0 * area_single_bar

        as_req_calculated = req_ratio * as_bot_mid
        as_req_governing = max(as_req_calculated, min_2_bars_area)
        clause_num = "9.7.3.8.1" if fraction == "1/3" else "9.7.3.8.2"

        pass_left = as_bot_left >= as_req_governing
        pass_right = as_bot_right >= as_req_governing

        return {
            "Ref_As_Pos_Max_Mid_mm2": round(as_bot_mid, 2),
            "As_Req_Calculated_mm2": round(as_req_calculated, 2),
            "As_Req_Equation": f"As_req = max({fraction} * As_pos_mid, 2_bars_area) (Clause: {clause_num})",
            "Min_2_Bars_Area_mm2": round(min_2_bars_area, 2),
            "As_Req_Governing_mm2": round(as_req_governing, 2),
            "Left_Support": {
                "As_Provided_mm2": round(as_bot_left, 2),
                "Pass": pass_left,
            },
            "Right_Support": {
                "As_Provided_mm2": round(as_bot_right, 2),
                "Pass": pass_right,
            },
            "Overall_Pass": pass_left and pass_right,
            "Clause": f"Clause: {clause_num}",
        }

    def check_integrity_reinforcement(
        self,
        as_bot_left: float,
        as_bot_mid: float,
        as_bot_right: float,
        as_top_left: float,
        as_top_mid: float,
        as_top_right: float,
        db_min: float = 20.0,
    ) -> dict:
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_2_bars_area = 2.0 * area_single_bar

        req_bot_1_4_calc = 0.25 * as_bot_mid
        req_bot_governing = max(req_bot_1_4_calc, min_2_bars_area)

        pass_bot_left = as_bot_left >= req_bot_governing
        pass_bot_right = as_bot_right >= req_bot_governing

        ref_as_top_support = max(as_top_left, as_top_right)
        req_top_1_6_calc = (1.0 / 6.0) * ref_as_top_support
        req_top_governing = max(req_top_1_6_calc, min_2_bars_area)

        pass_top_left = as_top_left >= req_top_governing
        pass_top_mid = as_top_mid >= req_top_governing
        pass_top_right = as_top_right >= req_top_governing

        all_integrity_pass = (
            pass_bot_left
            and pass_bot_right
            and pass_top_left
            and pass_top_mid
            and pass_top_right
        )

        return {
            "Overall_Pass": all_integrity_pass,
            "Item_3a_Bottom_Integrity_1_4": {
                "Ref_As_Bot_Mid_mm2": round(as_bot_mid, 2),
                "Req_1_4_Calculated_mm2": round(req_bot_1_4_calc, 2),
                "Req_Equation": "As_bot_integrity = max(0.25 * As_bot_mid, 2_bars_area) (Clause: 9.7.7.1a)",
                "Min_2_Bars_Area_mm2": round(min_2_bars_area, 2),
                "Governing_Req_mm2": round(req_bot_governing, 2),
                "Left_Pass": pass_bot_left,
                "Right_Pass": pass_bot_right,
            },
            "Item_3b_Top_Integrity_1_6": {
                "Ref_Max_As_Top_Support_mm2": round(ref_as_top_support, 2),
                "Req_1_6_Calculated_mm2": round(req_top_1_6_calc, 2),
                "Req_Equation": "As_top_integrity = max(1/6 * As_top_support, 2_bars_area) (Clause: 9.7.7.1b)",
                "Min_2_Bars_Area_mm2": round(min_2_bars_area, 2),
                "Governing_Req_mm2": round(req_top_governing, 2),
                "Left_Pass": pass_top_left,
                "Mid_Pass": pass_top_mid,
                "Right_Pass": pass_top_right,
            },
            "Item_3c_Enclosed_Stirrups": True,
            "Clause": "Clause: 9.7.7.1",
        }

    def check_av_min_exception(
        self,
        vu: float,
        vc: float,
        phi: float = 0.75,
        fyt: float = 413.69,
    ) -> dict:
        h_total = self.section.depth
        bw = self.section.width
        fc = self.section.fc

        is_exempt_by_height = h_total < 250.0
        threshold = 0.5 * phi * vc
        req_by_shear = vu > threshold
        av_min_required = req_by_shear and not is_exempt_by_height

        if av_min_required:
            min_eq1 = (0.062 * math.sqrt(fc) * bw) / fyt
            min_eq2 = (0.35 * bw) / fyt
            min_av_over_s = max(min_eq1, min_eq2)
        else:
            min_av_over_s = 0.0

        return {
            "Total_Height_h_mm": h_total,
            "Height_Exempt_h_lt_250": is_exempt_by_height,
            "Exemption_Equation": "h < 250 mm (Clause: 9.6.3.1)",
            "Vu_kN": round(vu, 2),
            "Threshold_0.5_phi_Vc_kN": round(threshold, 2),
            "Threshold_Equation": "Threshold = 0.5 * phi * Vc (Clause: 9.6.3)",
            "Av_min_Required": av_min_required,
            "min_Av_over_s_req_mm2_per_mm": round(min_av_over_s, 4),
            "Clause": "Clause: 9.6.3 / 9.6.3.1 & 9.6.3.2",
        }

    def check_min_stirrup_bar_size_by_main_bar(
        self, d_stirrup: float, main_bar_db: float = 20.0
    ) -> dict:
        req_d_stirrup = 13.0 if main_bar_db >= 36.0 else 10.0
        pass_size = d_stirrup >= req_d_stirrup

        return {
            "Main_Bar_db_mm": main_bar_db,
            "Provided_d_stirrup_mm": d_stirrup,
            "Required_d_stirrup_mm": req_d_stirrup,
            "Requirement_Equation": "d_stirrup >= 13mm for db >= 36mm else 10mm (Clause: 9.7.6.4.2)",
            "Pass": pass_size,
            "Clause": "Clause: 9.7.6.4.2",
        }

    def check_concrete_cover(self, clear_cover: float, req_cover: float = 40.0) -> dict:
        return {
            "Provided_Cover_mm": clear_cover,
            "Required_Cover_mm": req_cover,
            "Cover_Requirement_Equation": "clear_cover >= 40 mm (Clause: 20.6.1.3.1)",
            "Pass": clear_cover >= req_cover,
            "Clause": "Clause: 9.7.1.1 & 20.5.13",
        }

    def check_seismic_dimensional_limits(self, clear_span: float) -> dict:
        c1 = clear_span >= (4.0 * (self.section.depth - 65.0) / 1000.0)
        c2 = self.section.width >= 250.0
        c3 = self.section.width >= (0.30 * self.section.depth)
        return {
            "Ln >= 4d": c1,
            "Ln_Equation": "Ln >= 4 * d (Clause: 18.6.2.1a)",
            "bw >= 250mm": c2,
            "Width_Equation": "bw >= 250 mm (Clause: 18.6.2.1b)",
            "bw >= 0.30h": c3,
            "Ratio_Equation": "bw >= 0.30 * h (Clause: 18.6.2.1c)",
            "Pass": c1 and c2 and c3,
            "Clause": "Clause: 18.6.2.1",
        }

    def check_seismic_max_rho_all(
        self, as_map: dict, d_eff: float, limit: float = 0.025
    ) -> dict:
        audits = {}
        all_pass = True

        as_max_limit = limit * self.section.width * d_eff

        for loc, data in as_map.items():
            audits[loc] = {}
            for face in ["top", "bot"]:
                as_prov = data.get(f"as_{face}_mm2", 0.0)
                if not isinstance(as_prov, (int, float)):
                    as_prov = 0.0
                rho = as_prov / (self.section.width * d_eff)
                pass_check = rho <= limit
                if not pass_check:
                    all_pass = False
                audits[loc][face] = {
                    "As_mm2": round(as_prov, 2),
                    "Rho": round(rho, 4),
                    "Rho_Equation": "rho = As / (bw * d) <= 0.025 (Clause: 18.6.3.1)",
                    "as_max_limit_mm2": round(as_max_limit, 2),
                    "Limit": limit,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "as_max_limit_mm2": round(as_max_limit, 2),
            "Locations": audits,
            "Clause": "Clause: 18.6.3.1",
        }

    def check_seismic_face_moment_ratios(
        self, results: dict, d_eff: float = 435.0, db_min: float = 20.0
    ) -> dict:
        mn_neg_left = abs(results["Left"].get("phi_Mn_top_kNm", 0.0))
        mn_neg_right = abs(results["Right"].get("phi_Mn_top_kNm", 0.0))
        max_mn_neg_face = max(mn_neg_left, mn_neg_right)

        audits = {}
        all_pass = True
        overall_min_as_req = 0.0
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        phi_flexure = 0.9

        for loc in ["Left", "Right"]:
            mn_pos = abs(results[loc].get("phi_Mn_bot_kNm", 0.0))
            req_mn_pos = 0.50 * max_mn_neg_face
            pass_check = mn_pos >= req_mn_pos if req_mn_pos > 0 else True
            if not pass_check:
                all_pass = False

            if req_mn_pos > 0:
                test_bars = 2
                capacity_reached = False

                while test_bars <= 20:
                    trial_sec = BeamSectionGeometry(
                        width=self.section.width,
                        depth=self.section.depth,
                        fc=self.section.fc,
                        fy=self.section.fy,
                        concrete_name=self.section.concrete_name,
                        rebar_name=self.section.rebar_name,
                    )

                    y_pos = 40.0 + 10.0 + (db_min / 2.0)
                    x_left = 40.0 + 10.0 + (db_min / 2.0)
                    x_right = self.section.width - (40.0 + 10.0) - (db_min / 2.0)
                    w_avail = x_right - x_left

                    for i in range(test_bars):
                        x_pos = (
                            x_left + i * (w_avail / (test_bars - 1))
                            if test_bars > 1
                            else x_left
                        )
                        trial_sec.add_bars(area_single_bar, x_pos, y_pos)

                    sag_res, _, _ = trial_sec.calculate_ultimate_bending_capacity()
                    phi_mn_test = phi_flexure * abs(sag_res.m_x) / 1e6

                    if phi_mn_test >= req_mn_pos:
                        capacity_reached = True
                        break
                    test_bars += 1

                min_as_loc = test_bars * area_single_bar if capacity_reached else 0.0
            else:
                min_as_loc = 0.0

            overall_min_as_req = max(overall_min_as_req, min_as_loc)

            audits[loc] = {
                "Mn_Pos_Provided_kNm": round(mn_pos, 2),
                "Max_Mn_Neg_Face_Ref_kNm": round(max_mn_neg_face, 2),
                "Req_Mn_Pos_1_2_kNm": round(req_mn_pos, 2),
                "Ratio_Equation": "Mn(+) >= 0.50 * Mn(-) at joint face (Clause: 18.6.3.2)",
                "min_As_req_mm2": round(min_as_loc, 2),
                "Pass": pass_check,
            }

        return {
            "Overall_Pass": all_pass,
            "min_As_req_mm2": round(overall_min_as_req, 2),
            "Faces": audits,
            "Clause": "Clause: 18.6.3.2",
        }

    def check_seismic_any_moment_ratios(
        self, results: dict, d_eff: float = 435.0, db_min: float = 20.0
    ) -> dict:
        all_capacities = []
        for loc in ["Left", "Mid", "Right"]:
            for face in ["top", "bot"]:
                val = abs(results[loc].get(f"phi_Mn_{face}_kNm", 0.0))
                if isinstance(val, (int, float)):
                    all_capacities.append(val)

        max_mn_global = max(all_capacities) if all_capacities else 0.0
        req_mn_any = 0.25 * max_mn_global

        audits = {}
        all_pass = True
        overall_min_as_req = 0.0
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        phi_flexure = 0.9

        if req_mn_any > 0:
            test_bars = 2
            capacity_reached = False

            while test_bars <= 20:
                trial_sec = BeamSectionGeometry(
                    width=self.section.width,
                    depth=self.section.depth,
                    fc=self.section.fc,
                    fy=self.section.fy,
                    concrete_name=self.section.concrete_name,
                    rebar_name=self.section.rebar_name,
                )

                y_pos = 40.0 + 10.0 + (db_min / 2.0)
                x_left = 40.0 + 10.0 + (db_min / 2.0)
                x_right = self.section.width - (40.0 + 10.0) - (db_min / 2.0)
                w_avail = x_right - x_left

                for i in range(test_bars):
                    x_pos = (
                        x_left + i * (w_avail / (test_bars - 1))
                        if test_bars > 1
                        else x_left
                    )
                    trial_sec.add_bars(area_single_bar, x_pos, y_pos)

                sag_res, _, _ = trial_sec.calculate_ultimate_bending_capacity()
                phi_mn_test = phi_flexure * abs(sag_res.m_x) / 1e6

                if phi_mn_test >= req_mn_any:
                    capacity_reached = True
                    break
                test_bars += 1

            min_as_req_calc = test_bars * area_single_bar if capacity_reached else 0.0
        else:
            min_as_req_calc = 0.0

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                val = abs(results[loc].get(f"phi_Mn_{face}_kNm", 0.0))
                if not isinstance(val, (int, float)):
                    val = 0.0
                pass_check = val >= req_mn_any if req_mn_any > 0 else True
                if not pass_check:
                    all_pass = False

                if req_mn_any > 0:
                    overall_min_as_req = max(overall_min_as_req, min_as_req_calc)

                audits[loc][face] = {
                    "Mn_Provided_kNm": round(val, 2),
                    "Max_Mn_Global_Ref_kNm": round(max_mn_global, 2),
                    "Req_Mn_1_4_kNm": round(req_mn_any, 2),
                    "Ratio_Equation": "Mn_any >= 0.25 * Mn_max (Clause: 18.6.3.2)",
                    "min_As_req_mm2": round(min_as_req_calc, 2),
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "min_As_req_mm2": round(overall_min_as_req, 2),
            "Locations": audits,
            "Clause": "Clause: 18.6.3.2",
        }

    def check_seismic_hoop_region_extent(self) -> dict:
        return {
            "Extent_Length_mm": 2.0 * self.section.depth,
            "Extent_Equation": "Length = 2 * h (Clause: 18.6.4.1a)",
            "Clause": "Clause: 18.6.4.1a",
        }

    def check_seismic_hoop_spacing(
        self, d_eff: float, db_min_flexural: float, shear_results: dict = None
    ) -> dict:
        s_max_limit = min(d_eff / 4.0, 6.0 * db_min_flexural, 150.0)

        s_provided = 100.0
        if shear_results and "Left" in shear_results:
            s_provided = shear_results["Left"]["Plastic_Hinge_at_d"].get(
                "s_provided_mm", 100.0
            )

        pass_check = s_provided <= s_max_limit

        return {
            "First_Hoop_Dist_mm": 50.0,
            "Provided_Spacing_s_mm": s_provided,
            "Max_Spacing_Limit_mm": round(s_max_limit, 2),
            "Max_Spacing_Equation": "s_max = min(d / 4, 6 * db_main, 150 mm) (Clause: 18.6.4.4)",
            "Pass": pass_check,
            "Clause": "Clause: 18.6.4.4",
        }

    def check_seismic_non_hoop_spacing(
        self, d_eff: float, shear_results: dict = None
    ) -> dict:
        s_max_limit = d_eff / 2.0

        s_provided = 175.0
        if shear_results and "Left" in shear_results:
            s_provided = shear_results["Left"]["Beyond_Hinge_at_2H"].get(
                "s_provided_mm", 175.0
            )

        pass_check = s_provided <= s_max_limit

        return {
            "Provided_Spacing_s_mm": s_provided,
            "Max_Spacing_Limit_mm": round(s_max_limit, 2),
            "Max_Spacing_Equation": "s_max = d / 2 (Clause: 18.6.4.6)",
            "Pass": pass_check,
            "Clause": "Clause: 18.6.4.6",
        }

    def evaluate(
        self,
        results: dict,
        options: dict = None,
        demands: dict = None,
        shear_demands: dict = None,
        shear_results: dict = None,
        torsion_results: dict = None,
    ) -> dict:
        flags = {
            "check_pu_limit": True,
            "check_min_flexural_reinf": True,
            "check_min_bar_spacing": True,
            "check_crack_control_spacing": True,
            "check_skin_reinforcement": True,
            "check_pos_1_3_simple": True,
            "check_pos_1_4_continuous": True,
            "check_compression_lateral_support": True,
            "check_integrity_reinforcement": True,
            "check_av_min": True,
            "check_max_transverse_spacing": True,
            "check_min_stirrup_size": True,
            "check_concrete_cover": True,
            "set_flexural_dcr": 1.0,
            "set_shear_dcr": 1.0,
            "set_torsional_dcr": 1.0,
            "reduce_tu_to_phi_tcr": True,
            "check_seismic_dimensional_limits": True,
            "check_seismic_max_rho": True,
            "check_seismic_face_moment": True,
            "check_seismic_any_moment": True,
            "check_seismic_hoop_extent": True,
            "check_seismic_supported_bar_spacing": True,
            "check_seismic_hoop_spacing": True,
            "check_seismic_non_hoop_spacing": True,
            "check_seismic_probable_shear": True,
            "check_seismic_vc_zero": True,
            "factored_gravity_load": "DConS21",
        }
        if options:
            flags.update(options)
        demands = demands or {}
        shear_demands = shear_demands or {}

        pu = demands.get("pu", 0.0)
        tu = demands.get("tu", 0.0)
        d_eff = demands.get("d_eff", self.section.depth - 65.0)
        db_min = demands.get("db_min", 20.0)
        d_stirrup = demands.get("d_stirrup", 10.0)
        clear_span = demands.get("clear_span", 5.5)

        vc_calc = (
            self.section.width * d_eff * 0.17 * math.sqrt(self.section.fc) / 1000.0
        )

        if "Left" in shear_demands and "Right" in shear_demands:
            vu_max = max(
                shear_demands["Left"]["v_face"],
                shear_demands["Left"]["v_d"],
                shear_demands["Left"]["v_2h"],
                shear_demands["Right"]["v_face"],
                shear_demands["Right"]["v_d"],
                shear_demands["Right"]["v_2h"],
            )
        else:
            vu_max = demands.get("vu", 0.0)

        evaluations = {"GENERAL_OVERWRITES": {}, "SEISMIC_OVERWRITES": {}}

        def get_as(loc, key):
            val = results[loc][key]
            return val if isinstance(val, (int, float)) else 0.0

        as_top_left = get_as("Left", "as_top_mm2")
        as_bot_left = get_as("Left", "as_bot_mm2")
        as_top_mid = get_as("Mid", "as_top_mm2")
        as_bot_mid = get_as("Mid", "as_bot_mm2")
        as_top_right = get_as("Right", "as_top_mm2")
        as_bot_right = get_as("Right", "as_bot_mm2")

        if flags["check_pu_limit"]:
            evaluations["GENERAL_OVERWRITES"]["Pu < 0.10 f'c Ag (9.5.2)"] = (
                self.check_pu_limit(pu)
            )

        if flags["check_min_flexural_reinf"]:
            evaluations["GENERAL_OVERWRITES"]["Min Flexural Reinf (9.6.1)"] = (
                self.check_min_flexural_reinforcement(results, d_eff)
            )

        if flags["check_min_bar_spacing"]:
            evaluations["GENERAL_OVERWRITES"]["Min Bar Spacing (9.7.2.1 & 25.2)"] = (
                self.check_min_bar_spacing_all(results, db_min)
            )

        if flags["check_crack_control_spacing"]:
            evaluations["GENERAL_OVERWRITES"][
                "Crack Control Spacing (9.7.2.2 & 24.3)"
            ] = self.check_crack_control_spacing_all(
                results, db_min, d_stirrup=d_stirrup
            )

        if flags["check_skin_reinforcement"]:
            evaluations["GENERAL_OVERWRITES"]["Skin Reinforcement (9.7.2.3)"] = (
                self.check_skin_reinforcement(40.0)
            )

        if flags["check_pos_1_3_simple"]:
            evaluations["GENERAL_OVERWRITES"][
                "1/3 Pos. Reinf Simple Support (9.7.3.8.1)"
            ] = self.check_pos_reinf_support_extensions(
                as_bot_left, as_bot_mid, as_bot_right, "1/3", db_min
            )

        if flags["check_pos_1_4_continuous"]:
            evaluations["GENERAL_OVERWRITES"][
                "1/4 Pos. Reinf Cont. Support (9.7.3.8.2)"
            ] = self.check_pos_reinf_support_extensions(
                as_bot_left, as_bot_mid, as_bot_right, "1/4", db_min
            )

        if flags["check_compression_lateral_support"]:
            evaluations["GENERAL_OVERWRITES"][
                "Compression Bar Lateral Support (9.7.6.4)"
            ] = self.check_compression_lateral_support(
                results, shear_results, db_min, d_stirrup=d_stirrup
            )

        if flags["check_integrity_reinforcement"]:
            evaluations["GENERAL_OVERWRITES"]["Integrity Reinforcement (9.7.7.1)"] = (
                self.check_integrity_reinforcement(
                    as_bot_left,
                    as_bot_mid,
                    as_bot_right,
                    as_top_left,
                    as_top_mid,
                    as_top_right,
                    db_min,
                )
            )

        if flags["check_av_min"]:
            evaluations["GENERAL_OVERWRITES"]["Min Shear Reinforcement (9.6.3)"] = (
                self.check_av_min_exception(vu_max, vc_calc)
            )

        if flags["check_max_transverse_spacing"]:
            evaluations["GENERAL_OVERWRITES"]["Max Transverse Spacing (9.7.6.2.2)"] = (
                self.check_max_transverse_spacing_all(shear_results, d_eff)
            )

        if flags["check_min_stirrup_size"]:
            evaluations["GENERAL_OVERWRITES"]["Min Stirrup Bar Size (9.7.6.4.2)"] = (
                self.check_min_stirrup_bar_size_by_main_bar(d_stirrup, db_min)
            )

        if flags["check_concrete_cover"]:
            evaluations["GENERAL_OVERWRITES"]["Concrete Cover (9.7.1.1 & 20.5.13)"] = (
                self.check_concrete_cover(40.0)
            )

        evaluations["GENERAL_OVERWRITES"]["Flexural_DCR_Audit"] = (
            self.check_flexural_dcrs_all(results, flags["set_flexural_dcr"])
        )
        evaluations["GENERAL_OVERWRITES"]["Shear_DCR_Audit"] = (
            self.check_shear_dcrs_all(shear_results, flags["set_shear_dcr"])
        )
        evaluations["GENERAL_OVERWRITES"]["Torsional_DCR_Audit"] = (
            self.check_torsional_dcr(tu, torsion_results, flags["set_torsional_dcr"])
        )

        if flags["check_seismic_dimensional_limits"]:
            evaluations["SEISMIC_OVERWRITES"]["Dimensional Limits (18.6.2.1)"] = (
                self.check_seismic_dimensional_limits(clear_span)
            )

        if flags["check_seismic_max_rho"]:
            evaluations["SEISMIC_OVERWRITES"]["Max Rho <= 0.025 (18.6.3.1)"] = (
                self.check_seismic_max_rho_all(results, d_eff)
            )

        if flags["check_seismic_face_moment"]:
            evaluations["SEISMIC_OVERWRITES"]["1/2 Face Moment Ratio (18.6.3.2)"] = (
                self.check_seismic_face_moment_ratios(results)
            )

        if flags["check_seismic_any_moment"]:
            evaluations["SEISMIC_OVERWRITES"][
                "1/4 Any Section Moment Ratio (18.6.3.2)"
            ] = self.check_seismic_any_moment_ratios(results)

        if flags["check_seismic_hoop_extent"]:
            evaluations["SEISMIC_OVERWRITES"][
                "Hoops Over 2h Extent (18.6.4.1a)"
            ] = self.check_seismic_hoop_region_extent()

        if flags["check_seismic_supported_bar_spacing"]:
            evaluations["SEISMIC_OVERWRITES"][
                "Supported Bar Spacing <= 350mm (18.6.4.2 & 25.7.2.4)"
            ] = self.check_seismic_supported_bar_spacing_all(
                results, shear_results, db_min, d_stirrup=d_stirrup
            )

        if flags["check_seismic_hoop_spacing"]:
            evaluations["SEISMIC_OVERWRITES"]["Hoop Spacing Within 2h (18.6.4.4)"] = (
                self.check_seismic_hoop_spacing(d_eff, db_min, shear_results)
            )

        if flags["check_seismic_non_hoop_spacing"]:
            evaluations["SEISMIC_OVERWRITES"][
                "Stirrup Spacing Beyond 2h (18.6.4.6)"
            ] = self.check_seismic_non_hoop_spacing(d_eff, shear_results)

        if flags["check_seismic_probable_shear"]:
            mpr_left, mpr_right, mpr_max = self.calculate_mpr_capacities(
                results, db_min
            )
            v_eq_calculated = (
                (mpr_left + mpr_right) / clear_span if clear_span > 0 else 0.0
            )
            v_g_gravity = abs(demands.get("vg", 0.0))
            v_e_governing = v_eq_calculated + v_g_gravity

            evaluations["SEISMIC_OVERWRITES"]["Probable Shear (18.6.5.1)"] = {
                "Mpr_max_kNm": round(mpr_max, 2),
                "Mpr_Left_kNm": round(mpr_left, 2),
                "Mpr_Right_kNm": round(mpr_right, 2),
                "Veq_Sway_Shear_kN": round(v_eq_calculated, 2),
                "Vg_Gravity_Shear_kN": round(v_g_gravity, 2),
                "Governing_Vpr_Ve_Design_Shear_kN": round(v_e_governing, 2),
                "Ve_Equation": "Ve = (Mpr_left + Mpr_right) / Ln + |Vg| (Clause: 18.6.5.1)",
                "Gravity_Combo": flags["factored_gravity_load"],
                "Clause": "Clause: 18.6.5.1",
            }
        else:
            v_eq_calculated = 0.0

        if flags["check_seismic_vc_zero"]:
            vc_res = self.check_seismic_vc_zero(v_eq_calculated, vu_max, pu)
            vc_res["Gravity_Combo"] = flags["factored_gravity_load"]
            vc_res["Veq_calculated_kN"] = round(v_eq_calculated, 2)
            vc_res["Vu_max_evaluated_kN"] = round(vu_max, 2)
            evaluations["SEISMIC_OVERWRITES"]["Vc = 0 Evaluation (18.6.5.2)"] = vc_res

        return evaluations


# =========================================================================
# 6. EXCEL OVERWRITE READER SERVICE
# =========================================================================
class ExcelOverwriteReader:
    """Connects to active Excel instance via xlwings to read overwrite flags."""

    LABEL_MAP = {
        "Check Pu < 0.10 fcAg": "check_pu_limit",
        "Minimum flexural reinforcement area in nonprestressed": "check_min_flexural_reinf",
        "Longitudinal bar spacing satisfies the minimum spacing": "check_min_bar_spacing",
        "Bars closest to the tension face sat sify Section 9.7.2.2 and 24.3": "check_crack_control_spacing",
        "side bars, skin reinforcement is provided": "check_skin_reinforcement",
        "At simple supports, at least 1/3 of As": "check_pos_1_3_simple",
        "At continuous supports, at least 1/4 of As": "check_pos_1_4_continuous",
        "Compression bars are laterally supported": "check_compression_lateral_support",
        "Integrity Reinforcements must satisy": "check_integrity_reinforcement",
        "Minimum shear reinforcement satisfies Section 9.6.3": "check_av_min",
        "Maximum spacing of shear reinforcement along length": "check_max_transverse_spacing",
        "Minimum Shear reinforcement bar size satisfies Section 9.7.6.4.2": "check_min_stirrup_size",
        "Concrete reinforcement cover satisfies Sections 9.7.1.1": "check_concrete_cover",
        "Reduce Torsional Moment Tu to": "reduce_tu_to_phi_tcr",
        "SET FLEXURAL DCR LIMIT": "set_flexural_dcr",
        "SET SHEAR DCR LIMIT": "set_shear_dcr",
        "SET TORSIONAL DCR LIMIT": "set_torsional_dcr",
        "Beam satisifes the minimum dimensional requirements": "check_seismic_dimensional_limits",
        "Maximum longitudinal reinforcement ratio satisfies Section 18.6.3.1": "check_seismic_max_rho",
        "Mn (+) at joint face >= 1/2 Mn (-)": "check_seismic_face_moment",
        "at any section along member length >= [1/4 ,Mn (-)]": "check_seismic_any_moment",
        "Hoops provided over 2h from the face": "check_seismic_hoop_extent",
        "Laterally supported longitudinal bars must not be placed more than 350 mm": "check_seismic_supported_bar_spacing",
        "Place first hoop 50 mm from face": "check_seismic_hoop_spacing",
        "In the remaning regions, place stirrups with seismic hooks": "check_seismic_non_hoop_spacing",
        "Design shear force Ve calculated from Mpr": "check_seismic_probable_shear",
        "Gravity Load": "factored_gravity_load",
        "Transverse reinforcement shall be designed at Vc = 0": "check_seismic_vc_zero",
    }

    TRUE_VALUES = {True, 1, 1.0, "TRUE", "True", "true", "x", "X"}

    def __init__(self, cell_range: str = "A1:G60", sheet_name: str = "OVERWRITES"):
        self.cell_range = cell_range
        self.sheet_name = sheet_name

    def read_overwrites(self) -> dict:
        options = {
            "set_flexural_dcr": 1.0,
            "set_shear_dcr": 1.0,
            "set_torsional_dcr": 1.0,
            "reduce_tu_to_phi_tcr": True,
            "factored_gravity_load": "DConS21",
        }
        try:
            wb = xw.books.active
            sheet = (
                wb.sheets[self.sheet_name]
                if self.sheet_name in [s.name for s in wb.sheets]
                else wb.sheets.active
            )
            raw_matrix = sheet.range(self.cell_range).value

            if not raw_matrix:
                return options

            if not isinstance(raw_matrix[0], list):
                raw_matrix = [raw_matrix]

            for row in raw_matrix:
                for col_idx, cell in enumerate(row):
                    if cell is None:
                        continue

                    cell_str = str(cell).strip()

                    for label, key in self.LABEL_MAP.items():
                        if label.lower() in cell_str.lower():
                            val = next(
                                (
                                    c
                                    for c in row[col_idx + 1 :]
                                    if c is not None and str(c).strip() != ""
                                ),
                                None,
                            )

                            if key in [
                                "set_flexural_dcr",
                                "set_shear_dcr",
                                "set_torsional_dcr",
                            ]:
                                if val is not None:
                                    try:
                                        options[key] = float(val)
                                    except (ValueError, TypeError):
                                        pass
                            elif key == "factored_gravity_load":
                                options[key] = str(val).strip() if val else "DConS21"
                            else:
                                options[key] = val in self.TRUE_VALUES

                            break

            print("\n Successfully extracted live overwrites from Excel:")
            print(f"    • Flexural DCR Limit   : {options['set_flexural_dcr']}")
            print(f"    • Shear DCR Limit      : {options['set_shear_dcr']}")
            print(f"    • Torsional DCR Limit  : {options['set_torsional_dcr']}")
            print(f"    • Reduce Tu to phi*Tcr : {options['reduce_tu_to_phi_tcr']}")
            return options

        except Exception as e:
            print(
                f"\n Could not connect to active Excel instance ({e}). Using default options."
            )
            return options


# =========================================================================
# 7. HIGH-LEVEL PIPELINE MANAGER
# =========================================================================
class BeamDesignPipeline:
    """Orchestrates flexure, shear, torsion, code check evaluations, Excel overwrites, and synchronization."""

    def __init__(
        self,
        width: float = 300.0,
        depth: float = 500.0,
        fc: float = 34.48,
        fy: float = 413.69,
        overwrite_range: str = "A1:G60",
        overwrite_sheet_name: str = "OVERWRITES",
    ):
        self.width = width
        self.depth = depth
        self.fc = fc
        self.fy = fy
        self.section = BeamSectionGeometry(width=width, depth=depth, fc=fc, fy=fy)
        self.flex_designer = BeamFlexuralDesign(section=self.section)
        self.shear_designer = BeamShearDesign(
            section=self.section, flexural_designer=self.flex_designer
        )
        self.torsion_designer = BeamTorsionDesign(
            section=self.section, fyt=self.shear_designer.fyt, fy_long=fy
        )
        self.evaluator = CodeCheckEvaluator(section=self.section)
        self.excel_reader = ExcelOverwriteReader(
            cell_range=overwrite_range, sheet_name=overwrite_sheet_name
        )

    def run_pipeline(
        self,
        moment_demands: dict,
        shear_demands: dict,
        analysis_demands: dict,
        original_moment_demands: dict = None,
        read_excel_overwrites: bool = True,
        manual_overwrites: dict = None,
        target_override_bars: dict = None,
    ) -> dict:
        overwrites = {}
        if read_excel_overwrites:
            overwrites = self.excel_reader.read_overwrites()
        if manual_overwrites:
            overwrites.update(manual_overwrites)

        original_moment_demands = original_moment_demands or moment_demands

        if target_override_bars:
            self.flex_designer.target_override_bars = target_override_bars

        # 1. Flexural Design
        flex_results = self.flex_designer.design_beam_flexure(
            moment_demands,
            original_demands=original_moment_demands,
            evaluator=self.evaluator,
        )

        # 2. Preliminary Code Checks
        evals_prelim = self.evaluator.evaluate(
            results=flex_results,
            options=overwrites,
            demands=analysis_demands,
            shear_demands=shear_demands,
        )

        # 3. Seismic Shear Calculation & Shear Demand Updates
        mpr_left = evals_prelim["SEISMIC_OVERWRITES"]["Probable Shear (18.6.5.1)"][
            "Mpr_Left_kNm"
        ]
        mpr_right = evals_prelim["SEISMIC_OVERWRITES"]["Probable Shear (18.6.5.1)"][
            "Mpr_Right_kNm"
        ]

        ve_seismic = self.evaluator.calculate_seismic_shear_demand(
            mpr_left,
            mpr_right,
            analysis_demands["clear_span"],
            analysis_demands["vg"],
        )

        for loc in ["Left", "Right"]:
            if loc in shear_demands:
                shear_demands[loc]["v_seismic"] = ve_seismic

        vc_override = (
            0.0
            if evals_prelim["SEISMIC_OVERWRITES"]["Vc = 0 Evaluation (18.6.5.2)"][
                "Vc_Zero"
            ]
            else None
        )

        # 4. Shear Design
        long_bars_map = {
            "Left": max(
                flex_results["Left"]["n_top_bars"],
                flex_results["Left"]["n_bot_bars"],
            ),
            "Right": max(
                flex_results["Right"]["n_top_bars"],
                flex_results["Right"]["n_bot_bars"],
            ),
        }

        shear_results = self.shear_designer.design_beam_shear(
            shear_demands=shear_demands,
            ve_seismic=ve_seismic,
            d_eff=analysis_demands["d_eff"],
            long_bars_map=long_bars_map,
            db_main_min=analysis_demands["db_min"],
            vc_override=vc_override,
        )

        # 5. Torsion Design
        vu_peak = max(
            shear_demands["Left"]["v_face"],
            shear_demands["Right"]["v_face"],
        )
        self.torsion_designer.phi_torsion = (
            overwrites.get("set_torsional_dcr", 1.0) * 0.75
        )

        torsion_results = self.torsion_designer.design_torsion_reinforcement(
            tu_design=analysis_demands.get("tu", 0.0),
            vu_demand=vu_peak,
            d_eff=analysis_demands["d_eff"],
        )

        # 6. Synchronize Flexure Layout across Left, Mid, Right with Shear Legs
        final_flex_results = self.flex_designer.synchronize_with_shear_design(
            flex_results, shear_results
        )

        # 7. Final Code Checks
        final_evals = self.evaluator.evaluate(
            results=final_flex_results,
            options=overwrites,
            demands=analysis_demands,
            shear_demands=shear_demands,
            shear_results=shear_results,
            torsion_results=torsion_results,
        )

        return {
            "Flexural_Results": final_flex_results,
            "Shear_Results": shear_results,
            "Torsion_Results": torsion_results,
            "Code_Evaluations": final_evals,
            "Applied_Overwrites": overwrites,
        }


# =========================================================================
# 8. CODE PROVISION RESOLVER CLASS
# =========================================================================
class CodeProvisionResolver:
    """Extracts failed ACI 318M-14 code provisions from CodeCheckEvaluator evaluation results
    and resolves their governing geometric/reinforcement requirements along with ALL failing critical locations.
    """

    @staticmethod
    def get_failed_provisions(evaluations: dict) -> list[dict]:
        failed = []

        for category, provisions in evaluations.items():
            if not isinstance(provisions, dict):
                continue

            for check_name, check_data in provisions.items():
                if not isinstance(check_data, dict):
                    continue

                is_pass = check_data.get("Overall_Pass", check_data.get("Pass", True))

                if not is_pass:
                    failed.append(
                        {
                            "category": category,
                            "check_name": check_name,
                            "clause": check_data.get("Clause", "N/A"),
                            "details": check_data,
                        }
                    )

        return failed

    @staticmethod
    def resolve_requirements(evaluations: dict) -> dict:
        failed_provisions = CodeProvisionResolver.get_failed_provisions(evaluations)

        resolved_reqs = {
            "Has_Failures": len(failed_provisions) > 0,
            "Failed_Checks_Count": len(failed_provisions),
            "Failed_Checks_Summary": [],
            "Governing_Requirements": {
                "min_As_flexure_mm2": 0.0,
                "min_As_layer_1_crack_control_mm2": 0.0,
                "min_As_seismic_ratios_mm2": 0.0,
                "max_As_capacity_layer_1_mm2": float("inf"),
                "max_As_seismic_limit_mm2": float("inf"),
                "min_Av_shear_support_mm2": 0.0,
                "min_Av_over_s_req_mm2_per_mm": 0.0,
                "min_bw_times_d_req_mm2": 0.0,
                "min_required_legs": 2,
            },
            "Governing_Locations": {
                "min_As_flexure_location": [],
                "min_As_layer_1_crack_control_location": [],
                "min_As_seismic_ratios_location": [],
                "max_As_capacity_layer_1_location": [],
                "max_As_seismic_limit_location": [],
                "min_Av_shear_support_location": [],
                "min_Av_over_s_req_location": [],
                "min_bw_times_d_req_location": [],
                "min_required_legs_location": [],
            },
        }

        gov = resolved_reqs["Governing_Requirements"]
        locs = resolved_reqs["Governing_Locations"]

        for item in failed_provisions:
            check_name = item["check_name"]
            data = item["details"]
            resolved_reqs["Failed_Checks_Summary"].append(
                {
                    "check": check_name,
                    "clause": item["clause"],
                }
            )

            # 1. Flexural DCR Failure (Appends ALL failing faces)
            if "Flexural_DCR_Audit" in check_name:
                locations = data.get("Locations", {})
                for loc_key, loc_data in locations.items():
                    if isinstance(loc_data, dict):
                        for face, face_data in loc_data.items():
                            is_face_pass = face_data.get("Pass", True)
                            val = face_data.get("min_As_req_mm2", 0.0)

                            if not is_face_pass:
                                loc_str = f"{loc_key} {face}"
                                if loc_str not in locs["min_As_flexure_location"]:
                                    locs["min_As_flexure_location"].append(loc_str)

                                if val > gov["min_As_flexure_mm2"]:
                                    gov["min_As_flexure_mm2"] = val

            # 2. Crack Control Spacing Failure
            elif "Crack Control" in check_name:
                locations = data.get("Tension_Face_Locations", {})
                for loc_key, loc_data in locations.items():
                    if isinstance(loc_data, dict):
                        for face, face_data in loc_data.items():
                            if not face_data.get("Pass", True):
                                loc_str = f"{loc_key} {face}"
                                if (
                                    loc_str
                                    not in locs["min_As_layer_1_crack_control_location"]
                                ):
                                    locs[
                                        "min_As_layer_1_crack_control_location"
                                    ].append(loc_str)
                                val = face_data.get("min_As_layer_1_req_mm2", 0.0)
                                if val > gov["min_As_layer_1_crack_control_mm2"]:
                                    gov["min_As_layer_1_crack_control_mm2"] = val

            # 3. Minimum Bar Spacing / Capacity Limit Failure
            elif "Min Bar Spacing" in check_name:
                locations = data.get("Locations", {})
                for loc_key, loc_data in locations.items():
                    if isinstance(loc_data, dict):
                        for face, face_data in loc_data.items():
                            if not face_data.get("Pass", True):
                                loc_str = f"{loc_key} {face}"
                                if (
                                    loc_str
                                    not in locs["max_As_capacity_layer_1_location"]
                                ):
                                    locs["max_As_capacity_layer_1_location"].append(
                                        loc_str
                                    )
                                cap = face_data.get(
                                    "max_As_capacity_layer_1_mm2", float("inf")
                                )
                                if cap < gov["max_As_capacity_layer_1_mm2"]:
                                    gov["max_As_capacity_layer_1_mm2"] = cap

            # 4. Seismic Maximum Reinforcement Limit Failure
            elif "Max Rho" in check_name:
                locations = data.get("Locations", {})
                for loc_key, loc_data in locations.items():
                    if isinstance(loc_data, dict):
                        for face, face_data in loc_data.items():
                            if not face_data.get("Pass", True):
                                loc_str = f"{loc_key} {face}"
                                if loc_str not in locs["max_As_seismic_limit_location"]:
                                    locs["max_As_seismic_limit_location"].append(
                                        loc_str
                                    )
                                limit = face_data.get("as_max_limit_mm2", float("inf"))
                                if limit < gov["max_As_seismic_limit_mm2"]:
                                    gov["max_As_seismic_limit_mm2"] = limit

            # 5. Seismic Face or Global Moment Ratio Failures
            elif "Moment Ratio" in check_name:
                faces = data.get("Faces", data.get("Locations", {}))
                for loc_key, loc_data in faces.items():
                    if isinstance(loc_data, dict):
                        if "min_As_req_mm2" in loc_data:
                            if not loc_data.get("Pass", True):
                                if (
                                    loc_key
                                    not in locs["min_As_seismic_ratios_location"]
                                ):
                                    locs["min_As_seismic_ratios_location"].append(
                                        loc_key
                                    )
                                val = loc_data.get("min_As_req_mm2", 0.0)
                                if val > gov["min_As_seismic_ratios_mm2"]:
                                    gov["min_As_seismic_ratios_mm2"] = val
                        else:
                            for face, face_data in loc_data.items():
                                if not face_data.get("Pass", True):
                                    loc_str = f"{loc_key} {face}"
                                    if (
                                        loc_str
                                        not in locs["min_As_seismic_ratios_location"]
                                    ):
                                        locs["min_As_seismic_ratios_location"].append(
                                            loc_str
                                        )
                                    val = face_data.get("min_As_req_mm2", 0.0)
                                    if val > gov["min_As_seismic_ratios_mm2"]:
                                        gov["min_As_seismic_ratios_mm2"] = val

            # 6. Compression Lateral Support & Seismic Transverse Ties Failure
            elif (
                "Compression Bar Lateral Support" in check_name
                or "Supported Bar Spacing" in check_name
            ):
                locations = data.get(
                    "Compression_Zone_Locations", data.get("Locations", {})
                )
                for loc_key, loc_data in locations.items():
                    if isinstance(loc_data, dict):
                        for face, face_data in loc_data.items():
                            if not face_data.get("Pass", True):
                                loc_str = f"{loc_key} {face}"
                                if loc_str not in locs["min_required_legs_location"]:
                                    locs["min_required_legs_location"].append(loc_str)
                                if loc_str not in locs["min_Av_shear_support_location"]:
                                    locs["min_Av_shear_support_location"].append(
                                        loc_str
                                    )
                                legs = face_data.get("min_required_legs", 2)
                                av_val = face_data.get("min_Av_req_mm2", 0.0)
                                if legs > gov["min_required_legs"]:
                                    gov["min_required_legs"] = legs
                                if av_val > gov["min_Av_shear_support_mm2"]:
                                    gov["min_Av_shear_support_mm2"] = av_val

            # 7. Shear DCR or Minimum Shear Reinforcement Failure
            elif (
                "Shear_DCR_Audit" in check_name
                or "Min Shear Reinforcement" in check_name
            ):
                locations = data.get("Locations", {})
                if isinstance(locations, dict):
                    for loc_key, loc_data in locations.items():
                        if isinstance(loc_data, dict):
                            for zone_key, zone_data in loc_data.items():
                                if not zone_data.get("Pass", True):
                                    loc_str = f"{loc_key} {zone_key}"
                                    if (
                                        loc_str
                                        not in locs["min_Av_over_s_req_location"]
                                    ):
                                        locs["min_Av_over_s_req_location"].append(
                                            loc_str
                                        )
                                    val = zone_data.get(
                                        "min_Av_over_s_req_mm2_per_mm", 0.0
                                    )
                                    if val > gov["min_Av_over_s_req_mm2_per_mm"]:
                                        gov["min_Av_over_s_req_mm2_per_mm"] = val

            # 8. Torsional DCR Dimension Check Failure
            elif "Torsional_DCR_Audit" in check_name:
                if not data.get("Pass", True):
                    locs["min_bw_times_d_req_location"] = ["Critical Torsion Section"]
                    val = data.get("min_bw_times_d_req_mm2", 0.0)
                    if val > gov["min_bw_times_d_req_mm2"]:
                        gov["min_bw_times_d_req_mm2"] = val

        if gov["max_As_capacity_layer_1_mm2"] == float("inf"):
            gov["max_As_capacity_layer_1_mm2"] = None
        if gov["max_As_seismic_limit_mm2"] == float("inf"):
            gov["max_As_seismic_limit_mm2"] = None

        formatted_locs = {}
        for key, loc_list in locs.items():
            formatted_locs[key] = ", ".join(loc_list) if loc_list else "N/A"

        resolved_reqs["Governing_Locations"] = formatted_locs
        return resolved_reqs


# =========================================================================
# 9. BEAM SECTION REDESIGNER CLASS
# =========================================================================
class BeamSectionRedesigner:
    """Iteratively redesigns an ACI 318M-14 reinforced concrete beam section
    by resolving failed code provisions, scaling geometry, adjusting main rebar layouts,
    and optimizing transverse shear/torsion reinforcement.
    """

    def __init__(self, pipeline: Any, max_iterations: int = 15):
        self.pipeline = pipeline
        self.max_iterations = max_iterations

    def resolve_section(
        self,
        moment_demands: Dict[str, Dict[str, float]],
        shear_demands: Dict[str, Dict[str, float]],
        analysis_demands: Dict[str, float],
        read_excel_overwrites: bool = False,
    ) -> Dict[str, Any]:
        iteration = 0

        pipeline_results = self.pipeline.run_pipeline(
            moment_demands=moment_demands,
            shear_demands=shear_demands,
            analysis_demands=analysis_demands,
            read_excel_overwrites=read_excel_overwrites,
        )

        code_evals = pipeline_results.get("Code_Evaluations", {})
        resolved_reqs = CodeProvisionResolver.resolve_requirements(code_evals)

        while resolved_reqs["Has_Failures"] and iteration < self.max_iterations:
            iteration += 1

            loc_reqs = self._extract_location_specific_requirements(code_evals)

            # Step A: Reconfigure Flexural Reinforcement (As)
            self._reconfigure_flexural_reinforcement(loc_reqs, analysis_demands)

            # Step B: Check Max Seismic Rho Limit (rho <= 0.025) & Expand Section Width
            while self._check_exceeds_max_rho_limit(resolved_reqs, analysis_demands):
                self.pipeline.width += 50.0
                self._rearrange_flexural_bars_for_new_width(analysis_demands)

            # Step C & D: Re-evaluate Section State
            pipeline_results = self.pipeline.run_pipeline(
                moment_demands=moment_demands,
                shear_demands=shear_demands,
                analysis_demands=analysis_demands,
                read_excel_overwrites=False,
            )
            code_evals = pipeline_results.get("Code_Evaluations", {})
            resolved_reqs = CodeProvisionResolver.resolve_requirements(code_evals)
            loc_reqs = self._extract_location_specific_requirements(code_evals)

            # Step E: Resolve Minimum Required Stirrup Legs & Synchronize Flexural Bars
            self._resolve_stirrup_legs_and_sync_bars(
                resolved_reqs, loc_reqs, analysis_demands
            )

            # Step F & G: Resolve Shear Area (Av) & Optimize Spacing
            self._optimize_transverse_spacing(resolved_reqs, loc_reqs, analysis_demands)

            # Step H: Resolve Torsional Section Dimensions (bw * d)
            min_bw_d_req = resolved_reqs["Governing_Requirements"].get(
                "min_bw_times_d_req_mm2", 0.0
            )
            if min_bw_d_req > 0:
                current_bw_d = self.pipeline.width * analysis_demands.get(
                    "d_eff", self.pipeline.depth - 65.0
                )
                while current_bw_d < min_bw_d_req:
                    self.pipeline.width += 50.0
                    current_bw_d = self.pipeline.width * analysis_demands.get(
                        "d_eff", self.pipeline.depth - 65.0
                    )
                    self._rearrange_flexural_bars_for_new_width(analysis_demands)

            # Step I: Final Pass & Re-evaluation Loop Pass
            pipeline_results = self.pipeline.run_pipeline(
                moment_demands=moment_demands,
                shear_demands=shear_demands,
                analysis_demands=analysis_demands,
                read_excel_overwrites=False,
            )
            code_evals = pipeline_results.get("Code_Evaluations", {})
            resolved_reqs = CodeProvisionResolver.resolve_requirements(code_evals)

        pipeline_results["Redesign_Summary"] = {
            "Total_Iterations": iteration,
            "Final_Width_mm": self.pipeline.width,
            "Final_Depth_mm": self.pipeline.depth,
            "Fully_Compliant": not resolved_reqs["Has_Failures"],
            "Resolved_Governing_Requirements": resolved_reqs,
        }

        return pipeline_results

    def _extract_location_specific_requirements(
        self, code_evaluations: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        location_reqs = {}

        def get_loc_entry(loc_name: str) -> Dict[str, Any]:
            if loc_name not in location_reqs:
                location_reqs[loc_name] = {
                    "min_As_flexure_req_mm2": 0.0,
                    "min_As_layer_1_crack_control_mm2": 0.0,
                    "min_As_seismic_ratio_mm2": 0.0,
                    "max_As_capacity_layer_1_mm2": None,
                    "max_As_seismic_limit_mm2": None,
                    "min_Av_shear_support_mm2": 0.0,
                    "min_Av_over_s_req_mm2_per_mm": 0.0,
                    "min_required_legs": 2,
                    "failed_clauses": set(),
                }
            return location_reqs[loc_name]

        for category, checks in code_evaluations.items():
            if not isinstance(checks, dict):
                continue

            for check_name, data in checks.items():
                if not isinstance(data, dict):
                    continue

                clause = data.get("Clause", "N/A")

                if "Flexural_DCR_Audit" in check_name:
                    for loc, loc_data in data.get("Locations", {}).items():
                        if isinstance(loc_data, dict):
                            for face, face_data in loc_data.items():
                                if not face_data.get("Pass", True):
                                    entry = get_loc_entry(f"{loc} {face}")
                                    entry["min_As_flexure_req_mm2"] = max(
                                        entry["min_As_flexure_req_mm2"],
                                        face_data.get("min_As_req_mm2", 0.0),
                                    )
                                    entry["failed_clauses"].add(clause)

                elif "Crack Control" in check_name:
                    for loc, loc_data in data.get("Tension_Face_Locations", {}).items():
                        if isinstance(loc_data, dict):
                            for face, face_data in loc_data.items():
                                if not face_data.get("Pass", True):
                                    entry = get_loc_entry(f"{loc} {face}")
                                    entry["min_As_layer_1_crack_control_mm2"] = max(
                                        entry["min_As_layer_1_crack_control_mm2"],
                                        face_data.get("min_As_layer_1_req_mm2", 0.0),
                                    )
                                    entry["failed_clauses"].add(clause)

                elif "Moment Ratio" in check_name:
                    faces = data.get("Faces", data.get("Locations", {}))
                    for loc, loc_data in faces.items():
                        if isinstance(loc_data, dict):
                            if "min_As_req_mm2" in loc_data:
                                if not loc_data.get("Pass", True):
                                    entry = get_loc_entry(loc)
                                    entry["min_As_seismic_ratio_mm2"] = max(
                                        entry["min_As_seismic_ratio_mm2"],
                                        loc_data.get("min_As_req_mm2", 0.0),
                                    )
                                    entry["failed_clauses"].add(clause)
                            else:
                                for face, face_data in loc_data.items():
                                    if not face_data.get("Pass", True):
                                        entry = get_loc_entry(f"{loc} {face}")
                                        entry["min_As_seismic_ratio_mm2"] = max(
                                            entry["min_As_seismic_ratio_mm2"],
                                            face_data.get("min_As_req_mm2", 0.0),
                                        )
                                        entry["failed_clauses"].add(clause)

                elif (
                    "Shear_DCR_Audit" in check_name
                    or "Min Shear Reinforcement" in check_name
                ):
                    for loc, loc_data in data.get("Locations", {}).items():
                        if isinstance(loc_data, dict):
                            for zone_key, zone_data in loc_data.items():
                                if isinstance(zone_data, dict) and not zone_data.get(
                                    "Pass", True
                                ):
                                    entry = get_loc_entry(f"{loc} {zone_key}")
                                    entry["min_Av_over_s_req_mm2_per_mm"] = max(
                                        entry["min_Av_over_s_req_mm2_per_mm"],
                                        zone_data.get(
                                            "min_Av_over_s_req_mm2_per_mm", 0.0
                                        ),
                                    )
                                    entry["failed_clauses"].add(clause)

                elif (
                    "Compression Bar Lateral Support" in check_name
                    or "Supported Bar Spacing" in check_name
                ):
                    locations = data.get(
                        "Compression_Zone_Locations", data.get("Locations", {})
                    )
                    for loc, loc_data in locations.items():
                        if isinstance(loc_data, dict):
                            for face, face_data in loc_data.items():
                                if not face_data.get("Pass", True):
                                    entry = get_loc_entry(f"{loc} {face}")
                                    entry["min_required_legs"] = max(
                                        entry["min_required_legs"],
                                        face_data.get("min_required_legs", 2),
                                    )
                                    entry["min_Av_shear_support_mm2"] = max(
                                        entry["min_Av_shear_support_mm2"],
                                        face_data.get("min_Av_req_mm2", 0.0),
                                    )
                                    entry["failed_clauses"].add(clause)

        return location_reqs

    def _reconfigure_flexural_reinforcement(
        self,
        location_reqs: Dict[str, Dict[str, Any]],
        analysis_demands: Dict[str, float],
    ):
        db_min = analysis_demands.get("db_min", 20.0)
        area_single_bar = (math.pi / 4.0) * (db_min**2)

        for loc in ["Left", "Mid", "Right"]:
            for face in ["top", "bot"]:
                loc_key = f"{loc} {face}"
                reqs = location_reqs.get(loc_key, {})

                as_flex = reqs.get("min_As_flexure_req_mm2", 0.0)
                as_crack = reqs.get("min_As_layer_1_crack_control_mm2", 0.0)
                as_seismic = reqs.get("min_As_seismic_ratio_mm2", 0.0)

                governing_as_target = max(as_flex, as_crack, as_seismic)

                if governing_as_target > 0:
                    current_as = getattr(self.pipeline, f"as_{face}_{loc.lower()}", 0.0)
                    if governing_as_target > current_as:
                        req_bars = int(math.ceil(governing_as_target / area_single_bar))
                        req_bars = max(2, req_bars)
                        new_as = req_bars * area_single_bar

                        setattr(self.pipeline, f"n_{face}_bars_{loc.lower()}", req_bars)
                        setattr(self.pipeline, f"as_{face}_{loc.lower()}", new_as)

    def _check_exceeds_max_rho_limit(
        self,
        resolved_reqs: Dict[str, Any],
        analysis_demands: Dict[str, float],
    ) -> bool:
        d_eff = analysis_demands.get("d_eff", self.pipeline.depth - 65.0)
        max_as_seismic_limit = 0.025 * self.pipeline.width * d_eff

        for loc in ["left", "mid", "right"]:
            for face in ["top", "bot"]:
                as_prov = getattr(self.pipeline, f"as_{face}_{loc}", 0.0)
                if as_prov > max_as_seismic_limit:
                    return True
        return False

    def _rearrange_flexural_bars_for_new_width(
        self, analysis_demands: Dict[str, float]
    ):
        db_min = analysis_demands.get("db_min", 20.0)
        clear_cover = 40.0
        d_stirrup = analysis_demands.get("d_stirrup", 10.0)
        d_agg = 20.0

        s_clear_min = max(25.0, db_min, (4.0 / 3.0) * d_agg)
        s_center_min = s_clear_min + db_min
        w_avail = self.pipeline.width - 2.0 * (clear_cover + d_stirrup) - db_min

        max_per_layer = (
            int(math.floor(w_avail / s_center_min)) + 1 if s_center_min > 0 else 2
        )
        setattr(self.pipeline, "max_bars_per_layer", max_per_layer)

    def _resolve_stirrup_legs_and_sync_bars(
        self,
        resolved_reqs: Dict[str, Any],
        location_reqs: Dict[str, Dict[str, Any]],
        analysis_demands: Dict[str, float],
    ):
        min_required_legs = resolved_reqs["Governing_Requirements"].get(
            "min_required_legs", 2
        )
        current_legs = getattr(self.pipeline, "provided_legs", 2)

        target_legs = max(current_legs, min_required_legs)
        setattr(self.pipeline, "provided_legs", target_legs)

        for loc in ["left", "mid", "right"]:
            for face in ["top", "bot"]:
                n_bars = getattr(self.pipeline, f"n_{face}_bars_{loc}", 2)
                if n_bars < target_legs:
                    db_min = analysis_demands.get("db_min", 20.0)
                    area_single_bar = (math.pi / 4.0) * (db_min**2)
                    new_n_bars = target_legs
                    new_as = new_n_bars * area_single_bar

                    setattr(self.pipeline, f"n_{face}_bars_{loc}", new_n_bars)
                    setattr(self.pipeline, f"as_{face}_{loc}", new_as)

    def _optimize_transverse_spacing(
        self,
        resolved_reqs: Dict[str, Any],
        location_reqs: Dict[str, Dict[str, Any]],
        analysis_demands: Dict[str, float],
    ):
        gov_av_over_s_req = resolved_reqs["Governing_Requirements"].get(
            "min_Av_over_s_req_mm2_per_mm", 0.0
        )

        if gov_av_over_s_req > 0:
            provided_legs = getattr(self.pipeline, "provided_legs", 2)
            d_stirrup = analysis_demands.get("d_stirrup", 10.0)
            area_stirrup_single = (math.pi / 4.0) * (d_stirrup**2)
            av_provided = provided_legs * area_stirrup_single

            s_req_target = (
                av_provided / gov_av_over_s_req if gov_av_over_s_req > 0 else 200.0
            )

            s_optimized = int(math.floor(s_req_target / 25.0)) * 25
            s_optimized = max(50, min(s_optimized, 300))

            setattr(self.pipeline, "stirrup_spacing_mm", s_optimized)


# =========================================================================
# HELPER FUNCTIONS FOR FORMATTED AUDIT OUTPUT
# =========================================================================
def print_nested_audit(data, indent: int = 0):
    spacing = " " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"{spacing}• {key}:")
                print_nested_audit(value, indent + 4)
            elif isinstance(value, list):
                print(f"{spacing}• {key}:")
                for item in value:
                    print_nested_audit(item, indent + 4)
            else:
                val_str = (
                    f"{value:.2f}"
                    if isinstance(value, (float, np.floating))
                    else str(value)
                )
                print(f"{spacing}  - {key}: {val_str}")
    else:
        val_str = f"{data:.2f}" if isinstance(data, (float, np.floating)) else str(data)
        print(f"{spacing}  - {val_str}")


def print_shear_zone_report(zone_name: str, zone_data: dict):
    demands = zone_data["Demand_Selection"]
    ve_val = demands.get("Ve_seismic_kN", 0.0)

    print(f"\n  ► {zone_name.upper()} ZONE:")
    print(f"      - Governing Demand Type: {demands['Governing_Demand']}")
    print(f"      - Seismic Probable Shear (Ve): {ve_val:.2f} kN")
    print(f"      - Analysis Shear (Vu): {demands['Vu_analysis_kN']:.2f} kN")
    print(f"      - Governing Design Shear (Vu): {demands['Vu_governing_kN']:.2f} kN")
    print(f"      - Legs Provided: {zone_data['n_legs']} leg(s)")
    print(f"      - Stirrup Spacing: {zone_data['s_provided_mm']} mm c/c")
    print(
        f"      - Shear Capacity φVn: {zone_data['phi_Vn_kN']:.2f} kN (Vc:"
        f" {zone_data['Vc_kN']:.2f} kN, Vs: {zone_data['Vs_provided_kN']:.2f} kN)"
    )
    print(
        f"      - DCR Shear: {zone_data['DCR_Shear']:.3f} | Pass:"
        f" {zone_data['Pass']}"
    )


# =========================================================================
# MAIN EXECUTION BLOCK (Direct Pipeline & Redesign Run)
# =========================================================================
if __name__ == "__main__":
    beam_width = 300.0  # mm
    beam_depth = 500.0  # mm
    fc_concrete = 34.48  # MPa
    fy_rebar = 413.69  # MPa

    excel_sheet_name = "OVERWRITES"
    excel_cell_range = "A1:G60"
    enable_excel_reading = True

    moment_demands = {
        "Left": {"top": 180.0, "bot": 70.0},
        "Mid": {"top": 40.0, "bot": 150.0},
        "Right": {"top": 200.0, "bot": 80.0},
    }

    shear_demands = {
        "Left": {"v_face": 135.0, "v_d": 122.0, "v_2h": 110.0},
        "Right": {"v_face": 140.0, "v_d": 126.0, "v_2h": 112.0},
    }

    analysis_demands = {
        "pu": 15.0,
        "tu": 18.0,
        "d_eff": 435.0,
        "db_min": 20.0,
        "d_stirrup": 10.0,
        "clear_span": 5.5,
        "vg": 45.0,
    }

    # 1. Initialize Design Pipeline
    pipeline = BeamDesignPipeline(
        width=beam_width,
        depth=beam_depth,
        fc=fc_concrete,
        fy=fy_rebar,
        overwrite_range=excel_cell_range,
        overwrite_sheet_name=excel_sheet_name,
    )

    # 2. Run Direct Baseline Pipeline Pass
    initial_results = pipeline.run_pipeline(
        moment_demands=moment_demands,
        shear_demands=shear_demands,
        analysis_demands=analysis_demands,
        read_excel_overwrites=enable_excel_reading,
    )

    initial_code_evals = initial_results.get("Code_Evaluations", {})
    initial_requirements = CodeProvisionResolver.resolve_requirements(
        initial_code_evals
    )

    # Print Initial Audit
    print("\n" + "=" * 75)
    print(" INITIAL ACI 318M-14 CODE AUDIT (BEFORE REDESIGN)")
    print("=" * 75)
    print(f" Initial Section Width  : {pipeline.width} mm")
    print(f" Initial Section Depth  : {pipeline.depth} mm")
    print(f" Has Code Violations    : {initial_requirements['Has_Failures']}")
    print(f" Total Failed Checks    : {initial_requirements['Failed_Checks_Count']}")

    # 3. TRIGGER AUTOMATED BEAM SECTION REDESIGNER
    print("\n" + "=" * 75)
    print(" EXECUTING BEAM SECTION REDESIGNER RESOLUTION LOOP...")
    print("=" * 75)

    redesigner = BeamSectionRedesigner(pipeline=pipeline, max_iterations=15)
    final_results = redesigner.resolve_section(
        moment_demands=moment_demands,
        shear_demands=shear_demands,
        analysis_demands=analysis_demands,
        read_excel_overwrites=False,
    )

    final_code_evals = final_results.get("Code_Evaluations", {})
    final_requirements = CodeProvisionResolver.resolve_requirements(final_code_evals)
    redesign_summary = final_results.get("Redesign_Summary", {})

    # 4. Print Final Redesigned Section Summary
    print("\n" + "=" * 75)
    print(" FINAL REDESIGNED BEAM SECTION REPORT (AFTER RESOLUTION)")
    print("=" * 75)
    print(f" Total Redesign Iterations : {redesign_summary.get('Total_Iterations')}")
    print(f" Final Width (bw)          : {redesign_summary.get('Final_Width_mm')} mm")
    print(f" Final Depth (h)           : {redesign_summary.get('Final_Depth_mm')} mm")
    print(f" Provided Stirrup Legs     : {pipeline.shear_designer.n_legs} leg(s)")
    print(
        f" Provided Stirrup Spacing  : {pipeline.shear_designer.design_stirrup_spacing.__self__.av} mm² @ {pipeline.shear_designer.n_legs} legs"
    )
    print(f" Fully Code Compliant      : {redesign_summary.get('Fully_Compliant')}")
    print(f" Remaining Violations      : {final_requirements['Failed_Checks_Count']}\n")

    print(" Final Governing Targets & Locations:")
    gov_reqs = final_requirements["Governing_Requirements"]
    gov_locs = final_requirements["Governing_Locations"]

    target_loc_pairs = [
        ("min_As_flexure_mm2", "min_As_flexure_location"),
        ("min_As_layer_1_crack_control_mm2", "min_As_layer_1_crack_control_location"),
        ("min_As_seismic_ratios_mm2", "min_As_seismic_ratios_location"),
        ("max_As_capacity_layer_1_mm2", "max_As_capacity_layer_1_location"),
        ("max_As_seismic_limit_mm2", "max_As_seismic_limit_location"),
        ("min_Av_shear_support_mm2", "min_Av_shear_support_location"),
        ("min_Av_over_s_req_mm2_per_mm", "min_Av_over_s_req_location"),
        ("min_bw_times_d_req_mm2", "min_bw_times_d_req_location"),
        ("min_required_legs", "min_required_legs_location"),
    ]

    for req_key, loc_key in target_loc_pairs:
        val = gov_reqs.get(req_key)
        loc = gov_locs.get(loc_key, "N/A")
        val_str = f"{val}" if val is not None else "N/A"
        print(f"   • {req_key:<35}: {val_str:<12} | Location: {loc}")

    print("\n" + "=" * 75)
