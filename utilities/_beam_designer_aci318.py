"""Reinforced Concrete Beam Structural Design Engine per ACI 318-14.

Provides automated flexural design, shear design, code check evaluations,
and live Excel overwrite integration.
"""

import math
import matplotlib.pyplot as plt
import numpy as np
import xlwings as xw

from concreteproperties import add_bar
from concreteproperties.concrete_section import ConcreteSection
from concreteproperties.material import Concrete, SteelBar
import concreteproperties.stress_strain_profile as ssp
import sectionproperties.pre.library.primitive_sections as sp_ps


def identify_cantilever_beams(
    frame_df: pd.DataFrame, conn_df: pd.DataFrame
) -> pd.DataFrame:
    """Identifies beam support conditions using both Column and Wall joint connectivity."""
    conn_df.columns = [str(c).strip() for c in conn_df.columns]

    # 1. Extract support joints across all Column and Wall members
    support_rows = conn_df[conn_df["DesignType"].isin(["Column", "Wall"])]

    # Collect all possible point joint column names across columns and walls
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

    # 2. Filter connectivity data for Beams only
    beam_conn = conn_df[conn_df["DesignType"] == "Beam"].copy()

    # 3. Check connectivity at both end joints against vertical support joints (Column/Wall)
    beam_conn["Has_Support_PtI"] = beam_conn["UniquePtI"].isin(support_joints)
    beam_conn["Has_Support_PtJ"] = beam_conn["UniquePtJ"].isin(support_joints)

    # 4. Determine Support Status
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
        """Initializes section geometry and material strength parameters."""
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
        """Calculates rectangular stress block depth factor (beta_1 / gamma) per ACI 318."""
        if compressive_strength <= 28:
            return 0.85
        elif 28 < compressive_strength < 55:
            return 0.85 - 0.05 * (compressive_strength - 28) / 7
        return 0.65

    @staticmethod
    def calculate_ec(compressive_strength: float) -> float:
        """Calculates concrete elastic modulus Ec (MPa) per ACI 318 (4700 * sqrt(f'c))."""
        return 4700 * math.sqrt(compressive_strength)

    def get_materials(self) -> tuple[Concrete, SteelBar]:
        """Instantiates concrete and steel material instances for concreteproperties."""
        ec = self.calculate_ec(self.fc)
        gamma_val = self.calculate_gamma(self.fc)

        # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
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
        """Generates rectangular concrete section geometry using sectionproperties."""
        concrete_mat, _ = self.get_materials()
        return sp_ps.rectangular_section(
            d=self.depth, b=self.width, material=concrete_mat
        )

    def add_bars(self, rebar_area: float, x_coor: float, y_coor: float):
        """Adds a single rebar to the concrete section geometry."""
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
        """Solves ultimate flexural capacities via strain compatibility."""
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
    ):
        """Initializes flexural solver preferences and spacing constraints."""
        self.section = section
        self.db = db
        self.d_stirrup = d_stirrup
        self.clear_cover = clear_cover
        self.d_agg = d_agg
        self.phi = phi_flexure

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
        """Enforces a strict minimum of 2 rebars per populated horizontal layer."""
        if n_bars <= 2:
            return max(2, n_bars)
        M = self.max_bars_per_layer
        if M < n_bars < M + 2:
            return M + 2
        if 2 * M < n_bars < 2 * M + 2:
            return 2 * M + 2
        return n_bars

    def calculate_as_min(self, d_eff: float) -> float:
        """Calculates minimum flexural steel area As_min per ACI 318-14 Sec 9.6.1.2."""
        eq_a = (0.25 * math.sqrt(self.section.fc) / self.section.fy) * (
            self.section.width * d_eff
        )
        eq_b = (1.4 / self.section.fy) * (self.section.width * d_eff)
        return max(eq_a, eq_b)

    def get_min_required_bars(self, d_eff: float, evaluator=None) -> int:
        """Determines initial minimum bar count based on As_min and crack control spacing limits."""
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
        """Generates (x, y) centroid coordinates for all bars across up to 3 layers."""
        coords = []
        total_bars = min(self._sanitize_bar_count(total_bars), self.max_3_layer_bars)
        l1_bars = min(total_bars, self.max_bars_per_layer)

        # Master horizontal grid coordinates defined by Layer 1
        x_l1 = [
            self.x_left + i * (self.w_avail / (l1_bars - 1)) for i in range(l1_bars)
        ]
        y_l1 = (
            self.section.depth - self.clear_cover - self.d_stirrup - (self.db / 2.0)
            if is_top
            else self.clear_cover + self.d_stirrup + (self.db / 2.0)
        )
        y_step = -(self.s_vert_min + self.db) if is_top else (self.s_vert_min + self.db)

        # Fill Layer 1
        bars_placed = 0
        for x in x_l1:
            if bars_placed < total_bars:
                coords.append((x, y_l1))
                bars_placed += 1

        # Fill Layer 2 and Layer 3 (Inward alternating placement)
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
        """Returns x-indices for secondary layers filling from outer edges inward alternating."""
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
        self, mu_top: float, mu_bot: float, evaluator=None
    ) -> dict:
        """Iteratively sizes top and bottom reinforcement to satisfy moment demands at a location."""
        mu_top, mu_bot = abs(mu_top), abs(mu_bot)
        d_approx = (
            self.section.depth - self.clear_cover - self.d_stirrup - (self.db / 2.0)
        )
        min_bars = self.get_min_required_bars(d_approx, evaluator=evaluator)

        n_top, n_bot = min_bars, min_bars
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
            "top_coords": top_coords,
            "bot_coords": bot_coords,
        }

    def design_beam_flexure(self, demands: dict, evaluator=None) -> dict:
        """Executes flexural sizing across Left, Mid, and Right design locations."""
        return {
            loc: self.design_flexure_location(
                demands.get(loc, {}).get("top", 0.0),
                demands.get(loc, {}).get("bot", 0.0),
                evaluator=evaluator,
            )
            for loc in ["Left", "Mid", "Right"]
        }

    def synchronize_with_shear_design(
        self, flexural_results: dict, shear_results: dict
    ) -> dict:
        """Synchronizes outer layer flexural rebar count with shear stirrup leg requirements."""
        updated_results = flexural_results.copy()
        area_single = (math.pi / 4.0) * (self.db**2)

        for loc in ["Left", "Right"]:
            if loc not in updated_results or loc not in shear_results:
                continue

            req_bars = shear_results[loc]["Plastic_Hinge_at_d"].get(
                "required_long_bars", 2
            )
            curr_n_top = updated_results[loc]["n_top_bars"]
            curr_n_bot = updated_results[loc]["n_bot_bars"]
            target_outer_bars = min(req_bars, self.max_bars_per_layer)

            new_n_top = (
                max(curr_n_top, target_outer_bars)
                if isinstance(curr_n_top, int)
                else curr_n_top
            )
            new_n_bot = (
                max(curr_n_bot, target_outer_bars)
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
                        "shear_leg_sync_note": f"Longitudinal bars updated to {req_bars} bars to engage stirrup legs.",
                    }
                )

        return updated_results


# =========================================================================
# 3. SHEAR DESIGN MODULE
# =========================================================================
class BeamShearDesign:
    """Calculates concrete/steel shear capacities and stirrup/hoop spacing limits per ACI 318-14."""

    def __init__(
        self,
        section: BeamSectionGeometry,
        flexural_designer: BeamFlexuralDesign = None,
        n_legs: int = 2,
        d_stirrup: float = 10.0,
        fyt: float = 413.69,
        phi_shear: float = 0.75,
    ):
        """Initializes shear design solver."""
        self.section = section
        self.flexural_designer = flexural_designer
        self.n_legs = n_legs
        self.d_stirrup = d_stirrup
        self.fyt = fyt
        self.phi = phi_shear

        self.a_stirrup_single = (math.pi / 4.0) * (self.d_stirrup**2)
        self.av = self.n_legs * self.a_stirrup_single

    def calculate_vc(self, d_eff: float, lambda_factor: float = 1.0) -> float:
        """Calculates concrete shear capacity Vc (kN) per ACI 318-14 Eq 22.5.5.1."""
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
        """Computes stirrup spacing and required legs based on governing shear demand."""
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
                zone_desc = "Seismic Plastic Hinge (Within 2H - Clause 18.6.4.4)"
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
                zone_desc = "Standard Non-Hinge Zone (Clause 9.7.6.2.2)"

            s_governing = min(s_strength, s_min_reinf, s_max_code)
            if s_governing >= s_min_practical or vs_required == 0:
                break
            current_legs += 1

        current_legs = min(current_legs, max_legs)
        required_long_bars = max(current_long_bars, current_legs)
        additional_long_bars_needed = max(0, required_long_bars - current_long_bars)

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
            "required_long_bars": required_long_bars,
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
        """Executes a two-pass shear design for Left and Right sections at Column Face, d, and 2H."""
        left_bars = long_bars_map.get("Left", 2)
        right_bars = long_bars_map.get("Right", 2)

        # PASS 1: Trial Discovery across ALL critical locations (Face, d, 2H)
        trial_left_face = self.design_stirrup_spacing(
            shear_demands["Left"]["v_face"],
            d_eff,
            ve_seismic,
            left_bars,
            True,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )
        trial_left_d = self.design_stirrup_spacing(
            shear_demands["Left"]["v_d"],
            d_eff,
            ve_seismic,
            left_bars,
            True,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )
        trial_left_2h = self.design_stirrup_spacing(
            shear_demands["Left"]["v_2h"],
            d_eff,
            0.0,
            left_bars,
            False,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )

        trial_right_face = self.design_stirrup_spacing(
            shear_demands["Right"]["v_face"],
            d_eff,
            ve_seismic,
            right_bars,
            True,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )
        trial_right_d = self.design_stirrup_spacing(
            shear_demands["Right"]["v_d"],
            d_eff,
            ve_seismic,
            right_bars,
            True,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )
        trial_right_2h = self.design_stirrup_spacing(
            shear_demands["Right"]["v_2h"],
            d_eff,
            0.0,
            right_bars,
            False,
            db_main_min,
            vc_override,
            1.0,
            s_min_practical,
        )

        global_max_legs = max(
            trial_left_face["n_legs"],
            trial_left_d["n_legs"],
            trial_left_2h["n_legs"],
            trial_right_face["n_legs"],
            trial_right_d["n_legs"],
            trial_right_2h["n_legs"],
        )

        # PASS 2: Uniform Leg Count Enforcement
        self.n_legs = global_max_legs
        return {
            "Governing_Uniform_Legs": global_max_legs,
            "Left": {
                "At_Column_Face": self.design_stirrup_spacing(
                    shear_demands["Left"]["v_face"],
                    d_eff,
                    ve_seismic,
                    left_bars,
                    True,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
                "Plastic_Hinge_at_d": self.design_stirrup_spacing(
                    shear_demands["Left"]["v_d"],
                    d_eff,
                    ve_seismic,
                    left_bars,
                    True,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
                "Beyond_Hinge_at_2H": self.design_stirrup_spacing(
                    shear_demands["Left"]["v_2h"],
                    d_eff,
                    0.0,
                    left_bars,
                    False,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
            },
            "Right": {
                "At_Column_Face": self.design_stirrup_spacing(
                    shear_demands["Right"]["v_face"],
                    d_eff,
                    ve_seismic,
                    right_bars,
                    True,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
                "Plastic_Hinge_at_d": self.design_stirrup_spacing(
                    shear_demands["Right"]["v_d"],
                    d_eff,
                    ve_seismic,
                    right_bars,
                    True,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
                "Beyond_Hinge_at_2H": self.design_stirrup_spacing(
                    shear_demands["Right"]["v_2h"],
                    d_eff,
                    0.0,
                    right_bars,
                    False,
                    db_main_min,
                    vc_override,
                    1.0,
                    s_min_practical,
                ),
            },
        }


# =========================================================================
# 4. CODE CHECK & OVERWRITE EVALUATOR SERVICE
# =========================================================================
class CodeCheckEvaluator:
    """Evaluates individual ACI 318-14 code provisions, dimensional limits,

    flexural/shear/torsional DCRs, Mpr capacities, and Ve seismic shear demands.
    """

    def __init__(self, section: BeamSectionGeometry):
        """Initializes code evaluator."""
        self.section = section

    # ---------------------------------------------------------------------
    # CRACK CONTROL SPACING & CLEAR SPACING LIMITS
    # ---------------------------------------------------------------------
    def s_max(self, clear_cover: float = 40.0, fs: float = None) -> float:
        """Calculates maximum flexural rebar spacing for crack control per ACI 318-14 Sec 24.3.2."""
        if fs is None or fs <= 0:
            fs = (2.0 / 3.0) * self.section.fy
        s1 = 380.0 * (280.0 / fs) - 2.5 * clear_cover
        s2 = 300.0 * (280.0 / fs)
        return min(s1, s2)

    def calculate_s_clear_min(self, db: float, d_agg: float = 20.0) -> float:
        """Calculates minimum horizontal clear spacing between main bars per ACI 318-14 Sec 25.2.1."""
        return max(25.0, db, (4.0 / 3.0) * d_agg)

    # ---------------------------------------------------------------------
    # CHECK 1: CRACK CONTROL SPACING (s_max per Sec 9.7.2.2 & 24.3)
    # ---------------------------------------------------------------------
    def check_crack_control_spacing_all(
        self, flex_results: dict, db_min: float = 20.0, clear_cover: float = 40.0
    ) -> dict:
        """ACI 318-14 Sec 9.7.2.2 & 24.3: Audits provided spacing against s_max for bars closest to tension face."""
        fs_service = (2.0 / 3.0) * self.section.fy
        s_max_limit = self.s_max(clear_cover=clear_cover, fs=fs_service)

        d_stirrup = 10.0
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min

        audits = {}
        all_pass = True

        targets = [
            ("Left", "top"),
            ("Mid", "bot"),
            ("Right", "top"),
        ]

        for loc, face in targets:
            n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
            if not isinstance(n_bars, int):
                n_bars = 2

            l1_bars = min(n_bars, 5)
            if l1_bars > 1:
                s_provided_cc = w_avail / (l1_bars - 1)
            else:
                s_provided_cc = w_avail

            pass_check = s_provided_cc <= s_max_limit
            if not pass_check:
                all_pass = False

            if loc not in audits:
                audits[loc] = {}

            audits[loc][face] = {
                "n_bars_tension_layer_1": l1_bars,
                "s_provided_center_to_center_mm": round(s_provided_cc, 2),
                "s_max_limit_mm": round(s_max_limit, 2),
                "Pass": pass_check,
            }

        return {
            "Overall_Pass": all_pass,
            "Tension_Face_Locations": audits,
            "Clause": "9.7.2.2 & 24.3",
        }

    # ---------------------------------------------------------------------
    # CHECK 2: COMPRESSION BAR LATERAL SUPPORT (Sec 9.7.6.4)
    # ---------------------------------------------------------------------
    def check_compression_lateral_support(
        self,
        flex_results: dict,
        shear_results: dict,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
    ) -> dict:
        """ACI 318-14 Sec 9.7.6.4: Checks lateral support of compression bars.

        Evaluates 2 outer corner legs + floor((N-1)/2) interior legs for alternating support,
        and verifies clear distance between adjacent bars <= 150 mm (Sec 9.7.6.4.3).
        """
        audits = {}
        all_pass = True

        d_stirrup = 10.0
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min

        targets = [
            ("Left", "bot"),
            ("Mid", "top"),
            ("Right", "bot"),
        ]

        for loc, face in targets:
            n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
            if not isinstance(n_bars, int):
                n_bars = 2

            l1_bars = min(n_bars, 5)

            if l1_bars > 1:
                cc_spacing = w_avail / (l1_bars - 1)
                clear_spacing_adj = cc_spacing - db_min
            else:
                clear_spacing_adj = 0.0

            # Alternating tie leg calculation: 2 outer corner legs + interior legs for every second bar
            outer_legs = 2 if l1_bars >= 2 else 1
            interior_legs = (l1_bars - 1) // 2 if l1_bars > 2 else 0
            total_legs_req = outer_legs + interior_legs

            provided_legs = 2
            if shear_results and loc in shear_results:
                zone_key = (
                    "Plastic_Hinge_at_d"
                    if loc in ["Left", "Right"]
                    else "Beyond_Hinge_at_2H"
                )
                provided_legs = shear_results[loc].get(zone_key, {}).get("n_legs", 2)

            pass_legs = provided_legs >= total_legs_req
            pass_150mm = clear_spacing_adj <= 150.0

            pass_loc = pass_legs and pass_150mm
            if not pass_loc:
                all_pass = False

            if loc not in audits:
                audits[loc] = {}

            audits[loc][face] = {
                "n_compression_bars_layer_1": l1_bars,
                "outer_corner_legs_req": outer_legs,
                "interior_legs_req": interior_legs,
                "total_legs_req_alternating": total_legs_req,
                "legs_provided": provided_legs,
                "legs_pass": pass_legs,
                "clear_spacing_adjacent_bars_mm": round(clear_spacing_adj, 2),
                "max_clear_distance_limit_mm": 150.0,
                "clear_distance_150mm_pass": pass_150mm,
                "Pass": pass_loc,
            }

        return {
            "Overall_Pass": all_pass,
            "Compression_Zone_Locations": audits,
            "Clause": "9.7.6.4",
        }

    # ---------------------------------------------------------------------
    # CHECK 3: SEISMIC LATERALLY SUPPORTED BAR SPACING <= 350 MM (Sec 18.6.4.2 & 25.7.2.4)
    # ---------------------------------------------------------------------
    def check_seismic_supported_bar_spacing_all(
        self,
        flex_results: dict,
        shear_results: dict,
        db_min: float = 20.0,
        clear_cover: float = 40.0,
    ) -> dict:
        """ACI 318-14 Sec 18.6.4.2 & 25.7.2.4: Verifies center-to-center spacing of laterally supported bars <= 350 mm."""
        audits = {}
        all_pass = True

        d_stirrup = 10.0
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                n_bars = flex_results.get(loc, {}).get(f"n_{face}_bars", 2)
                if not isinstance(n_bars, int):
                    n_bars = 2

                l1_bars = min(n_bars, 5)

                provided_legs = 2
                if shear_results and loc in shear_results:
                    zone_key = (
                        "Plastic_Hinge_at_d"
                        if loc in ["Left", "Right"]
                        else "Beyond_Hinge_at_2H"
                    )
                    provided_legs = (
                        shear_results[loc].get(zone_key, {}).get("n_legs", 2)
                    )

                # Center-to-center spacing of adjacent bars in Layer 1
                if l1_bars > 1:
                    cc_spacing_adj = w_avail / (l1_bars - 1)
                else:
                    cc_spacing_adj = w_avail

                # Determine spacing between laterally supported bars based on provided legs
                # If provided_legs >= l1_bars, every bar is supported -> spacing = cc_spacing_adj
                # If 2 legs (perimeter hoop only), outer corner bars are supported -> spacing = w_avail
                if provided_legs >= l1_bars:
                    supported_cc_spacing = cc_spacing_adj
                elif provided_legs > 2:
                    # Alternating support or interior ties reduce the max supported spacing
                    supported_bars_count = min(l1_bars, provided_legs)
                    supported_cc_spacing = w_avail / (supported_bars_count - 1)
                else:
                    supported_cc_spacing = w_avail

                pass_check = supported_cc_spacing <= 350.0
                if not pass_check:
                    all_pass = False

                audits[loc][face] = {
                    "n_bars_layer_1": l1_bars,
                    "legs_provided": provided_legs,
                    "max_supported_bar_cc_spacing_mm": round(supported_cc_spacing, 2),
                    "code_limit_mm": 350.0,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "18.6.4.2 & 25.7.2.4",
        }

    # ---------------------------------------------------------------------
    # MINIMUM FLEXURAL REINFORCEMENT (ALL LOCATIONS)
    # ---------------------------------------------------------------------
    def calculate_as_min(self, d_eff: float) -> float:
        """Calculates minimum flexural reinforcement area per ACI 318-14 Sec 9.6.1.2."""
        eq_a = (0.25 * math.sqrt(self.section.fc) / self.section.fy) * (
            self.section.width * d_eff
        )
        eq_b = (1.4 / self.section.fy) * (self.section.width * d_eff)
        return max(eq_a, eq_b)

    def check_min_flexural_reinforcement(self, as_map: dict, d_eff: float) -> dict:
        """ACI 318-14 Sec 9.6.1: Audits As >= As_min across Left, Mid, and Right (Top and Bottom)."""
        as_min = self.calculate_as_min(d_eff)
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
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "9.6.1",
        }

    # ---------------------------------------------------------------------
    # MINIMUM BAR SPACING (ALL LOCATIONS)
    # ---------------------------------------------------------------------
    def check_min_bar_spacing_all(
        self, flex_results: dict, db_min: float = 20.0, d_agg: float = 20.0
    ) -> dict:
        """ACI 318-14 Sec 9.7.2.1 & 25.2: Calculates actual provided clear spacing vs minimum required across all zones."""
        audits = {}
        all_pass = True

        clear_cover = 40.0
        d_stirrup = 10.0
        w_avail = self.section.width - 2 * (clear_cover + d_stirrup) - db_min

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                n_bars_key = f"n_{face}_bars"
                n_bars = flex_results.get(loc, {}).get(n_bars_key, 2)
                if not isinstance(n_bars, int):
                    n_bars = 2

                s_clear_min = self.calculate_s_clear_min(db_min, d_agg)

                if n_bars > 1:
                    center_spacing = (
                        w_avail / (min(n_bars, 5) - 1)
                        if min(n_bars, 5) > 1
                        else w_avail
                    )
                    s_provided = center_spacing - db_min
                else:
                    s_provided = w_avail

                pass_check = s_provided >= s_clear_min
                if not pass_check:
                    all_pass = False

                audits[loc][face] = {
                    "n_bars": n_bars,
                    "db_mm": db_min,
                    "s_provided_clear_mm": round(s_provided, 2),
                    "s_clear_min_req_mm": round(s_clear_min, 2),
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "9.7.2.1 & 25.2",
        }

    # ---------------------------------------------------------------------
    # FLEXURAL, SHEAR, AND TORSIONAL DCR AUDITS AGAINST MAPPED LIMITS
    # ---------------------------------------------------------------------
    def check_flexural_dcrs_all(
        self, flex_results: dict, dcr_limit: float = 1.0
    ) -> dict:
        """Audits flexural DCR = Mu / phi_Mn at Left, Mid, Right (Top & Bot) against flexural DCR limit."""
        audits = {}
        all_pass = True

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                mu_val = flex_results.get(loc, {}).get(f"Mu_{face}_kNm", 0.0)
                phi_mn_val = flex_results.get(loc, {}).get(f"phi_Mn_{face}_kNm", 1.0)

                if not isinstance(phi_mn_val, (int, float)) or phi_mn_val <= 0:
                    dcr = 0.0
                else:
                    dcr = mu_val / phi_mn_val

                pass_check = dcr <= dcr_limit
                if not pass_check:
                    all_pass = False

                audits[loc][face] = {
                    "Mu_demand_kNm": round(mu_val, 2),
                    "phi_Mn_capacity_kNm": (
                        round(phi_mn_val, 2)
                        if isinstance(phi_mn_val, (int, float))
                        else str(phi_mn_val)
                    ),
                    "DCR": round(dcr, 3),
                    "DCR_Limit": dcr_limit,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "Flexural DCR Limit",
        }

    def check_shear_dcrs_all(self, shear_results: dict, dcr_limit: float = 1.0) -> dict:
        """Audits shear DCR = Vu / phi_Vn at Face, d, and 2h for Left and Right supports against shear DCR limit."""
        audits = {}
        all_pass = True

        if not shear_results or "Left" not in shear_results:
            return {
                "Overall_Pass": True,
                "Locations": "No Shear Results Available",
                "Clause": "Shear DCR Limit",
            }

        for loc in ["Left", "Right"]:
            audits[loc] = {}
            for zone_key, zone_data in shear_results[loc].items():
                dcr = zone_data.get("DCR_Shear", 0.0)
                pass_check = dcr <= dcr_limit
                if not pass_check:
                    all_pass = False

                audits[loc][zone_key] = {
                    "Vu_governing_kN": zone_data["Demand_Selection"]["Vu_governing_kN"],
                    "phi_Vn_capacity_kN": zone_data["phi_Vn_kN"],
                    "DCR": dcr,
                    "DCR_Limit": dcr_limit,
                    "Pass": pass_check,
                }

        return {
            "Overall_Pass": all_pass,
            "Locations": audits,
            "Clause": "Shear DCR Limit",
        }

    def check_torsional_dcr(
        self, tu: float = 0.0, tn: float = 1.0, dcr_limit: float = 1.0
    ) -> dict:
        """Default placeholder torsional DCR check using Tu / (0.75 * Tn) against torsional DCR limit."""
        phi_tn = 0.75 * tn if tn > 0 else 1.0
        dcr = tu / phi_tn if phi_tn > 0 else 0.0
        pass_check = dcr <= dcr_limit

        return {
            "Tu_demand_kNm": tu,
            "phi_Tn_capacity_kNm": round(phi_tn, 2),
            "DCR": round(dcr, 3),
            "DCR_Limit": dcr_limit,
            "Pass": pass_check,
            "Clause": "Torsional DCR Limit (Placeholder)",
        }

    # ---------------------------------------------------------------------
    # SEISMIC PROBABLE MOMENTS & SHEAR DEMANDS (ACI 18.6.5.1 & 18.6.5.2)
    # ---------------------------------------------------------------------
    def calculate_mpr_capacities(
        self, flex_results: dict, db_min: float = 20.0
    ) -> tuple[float, float, float]:
        """Calculates Mpr (probable flexural strength using 1.25 * fy) for Left and Right supports."""
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
        """Calculates design shear force Ve = (Mpr_L + Mpr_R) / Ln + |Vg| per ACI 318-14 Sec 18.6.5.1."""
        if clear_span <= 0:
            raise ValueError("Clear span Ln must be greater than zero.")
        return ((mpr_left + mpr_right) / clear_span) + abs(vg)

    def check_seismic_vc_zero(self, veq: float, vu: float, pu: float) -> dict:
        """Evaluates whether Vc = 0 condition is triggered per ACI 318-14 Sec 18.6.5.2."""
        ag = self.section.width * self.section.depth
        low_axial = abs(pu) < (0.05 * self.section.fc * ag / 1000.0)
        high_shear = veq >= (0.5 * vu) if vu > 0 else False
        vc_is_zero = low_axial and high_shear

        return {
            "Low_Axial": low_axial,
            "High_Seismic_Shear": high_shear,
            "Vc_Zero": vc_is_zero,
            "Clause": "18.6.5.2",
        }

    # ---------------------------------------------------------------------
    # CHAPTER 9 & 25: GENERAL BEAM PROVISIONS
    # ---------------------------------------------------------------------
    def check_pu_limit(self, pu: float) -> dict:
        """Evaluates Pu < 0.10 * f'c * Ag beam action limit per Sec 9.5.2."""
        ag = self.section.width * self.section.depth
        pu_limit = 0.10 * self.section.fc * ag / 1000.0
        return {
            "Pu_kN": pu,
            "Limit_kN": round(pu_limit, 2),
            "Pass": abs(pu) < pu_limit,
            "Clause": "9.5.2",
        }

    def check_skin_reinforcement(
        self, clear_cover: float = 40.0, fs: float = None
    ) -> dict:
        """Evaluates skin reinforcement requirement for h > 900 mm per Sec 9.7.2.3."""
        required = self.section.depth > 900.0
        s_max_val = self.s_max(clear_cover, fs) if required else "N/A"
        return {
            "Depth_mm": self.section.depth,
            "Required": required,
            "Max_Spacing_mm": s_max_val,
            "Clause": "9.7.2.3",
        }

    def check_pos_reinf_support_extensions(
        self,
        as_bot_left: float,
        as_bot_mid: float,
        as_bot_right: float,
        fraction: str = "1/3",
        db_min: float = 20.0,
    ) -> dict:
        """ACI 318-14 Sec 9.7.3.8.1 / 9.7.3.8.2: Uses as_bot_mid as ref max positive steel (checking >= fraction OR >= 2 bars)."""
        req_ratio = 1 / 3 if fraction == "1/3" else 1 / 4
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_2_bars_area = 2.0 * area_single_bar

        as_req_calculated = req_ratio * as_bot_mid
        as_req_governing = max(as_req_calculated, min_2_bars_area)
        clause = "9.7.3.8.1" if fraction == "1/3" else "9.7.3.8.2"

        pass_left = as_bot_left >= as_req_governing
        pass_right = as_bot_right >= as_req_governing

        return {
            "Ref_As_Pos_Max_Mid_mm2": round(as_bot_mid, 2),
            "As_Req_Calculated_mm2": round(as_req_calculated, 2),
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
            "Clause": clause,
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
        """ACI 318-14 Sec 9.7.7.1: Integrity steel checks for 1/4 bot (ref as_bot_mid) and 1/6 top (ref max support as_top)."""
        area_single_bar = (math.pi / 4.0) * (db_min**2)
        min_2_bars_area = 2.0 * area_single_bar

        # 3.a: Bottom Integrity (1/4 of max As(+) mid, not less than 2 bars)
        req_bot_1_4_calc = 0.25 * as_bot_mid
        req_bot_governing = max(req_bot_1_4_calc, min_2_bars_area)

        pass_bot_left = as_bot_left >= req_bot_governing
        pass_bot_right = as_bot_right >= req_bot_governing

        # 3.b: Top Integrity (1/6 of As(-) at support, not less than 2 bars; checked across Left, Mid, Right)
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
                "Min_2_Bars_Area_mm2": round(min_2_bars_area, 2),
                "Governing_Req_mm2": round(req_bot_governing, 2),
                "Left_Pass": pass_bot_left,
                "Right_Pass": pass_bot_right,
            },
            "Item_3b_Top_Integrity_1_6": {
                "Ref_Max_As_Top_Support_mm2": round(ref_as_top_support, 2),
                "Req_1_6_Calculated_mm2": round(req_top_1_6_calc, 2),
                "Min_2_Bars_Area_mm2": round(min_2_bars_area, 2),
                "Governing_Req_mm2": round(req_top_governing, 2),
                "Left_Pass": pass_top_left,
                "Mid_Pass": pass_top_mid,
                "Right_Pass": pass_top_right,
            },
            "Item_3c_Enclosed_Stirrups": True,
            "Clause": "9.7.7.1",
        }

    def check_av_min_exception(self, vu: float, vc: float, phi: float = 0.75) -> dict:
        """ACI 318-14 Sec 9.6.3 / 9.6.3.1: Checks if h < 250 mm to qualify for Av_min exception."""
        h_total = self.section.depth
        is_exempt_by_height = h_total < 250.0
        threshold = 0.5 * phi * vc
        req_by_shear = vu > threshold

        return {
            "Total_Height_h_mm": h_total,
            "Height_Exempt_h_lt_250": is_exempt_by_height,
            "Vu_kN": round(vu, 2),
            "Threshold_0.5_phi_Vc_kN": round(threshold, 2),
            "Av_min_Required": req_by_shear and not is_exempt_by_height,
            "Clause": "9.6.3 / 9.6.3.1",
        }

    def check_min_stirrup_bar_size_by_main_bar(
        self, d_stirrup: float, main_bar_db: float = 20.0
    ) -> dict:
        """ACI 318-14 Sec 9.7.6.4.2: Stirrup diameter check based on main bar diameter."""
        if main_bar_db >= 36.0:
            req_d_stirrup = 13.0
        elif main_bar_db >= 32.0:
            req_d_stirrup = 10.0
        else:
            req_d_stirrup = 10.0

        pass_size = d_stirrup >= req_d_stirrup

        return {
            "Main_Bar_db_mm": main_bar_db,
            "Provided_d_stirrup_mm": d_stirrup,
            "Required_d_stirrup_mm": req_d_stirrup,
            "Pass": pass_size,
            "Clause": "9.7.6.4.2",
        }

    def check_concrete_cover(self, clear_cover: float, req_cover: float = 40.0) -> dict:
        """Evaluates concrete clear cover per Sec 9.7.1.1 & 20.5.13."""
        return {
            "Provided_Cover_mm": clear_cover,
            "Required_Cover_mm": req_cover,
            "Pass": clear_cover >= req_cover,
            "Clause": "9.7.1.1 & 20.5.13",
        }

    # ---------------------------------------------------------------------
    # CHAPTER 18: SEISMIC BEAM PROVISIONS
    # ---------------------------------------------------------------------
    def check_seismic_dimensional_limits(self, clear_span: float) -> dict:
        """Evaluates beam geometry dimensional limits for special moment frames per Sec 18.6.2.1."""
        c1 = clear_span >= (4.0 * (self.section.depth - 65.0) / 1000.0)
        c2 = self.section.width >= 250.0
        c3 = self.section.width >= (0.30 * self.section.depth)
        return {
            "Ln >= 4d": c1,
            "bw >= 250mm": c2,
            "bw >= 0.30h": c3,
            "Pass": c1 and c2 and c3,
            "Clause": "18.6.2.1",
        }

    def check_seismic_max_rho_all(
        self, as_map: dict, d_eff: float, limit: float = 0.025
    ) -> dict:
        """ACI 318-14 Sec 18.6.3.1: Audits rho <= 0.025 across Left, Mid, Right (Top & Bot)."""
        audits = {}
        all_pass = True

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
                    "Limit": limit,
                    "Pass": pass_check,
                }

        return {"Overall_Pass": all_pass, "Locations": audits, "Clause": "18.6.3.1"}

    def check_seismic_face_moment_ratios(self, results: dict) -> dict:
        """ACI 318-14 Sec 18.6.3.2: Audits Mn(+) >= 1/2 * Mn(-) at joint faces using max negative face strength."""
        mn_neg_left = abs(results["Left"].get("phi_Mn_top_kNm", 0.0))
        mn_neg_right = abs(results["Right"].get("phi_Mn_top_kNm", 0.0))
        max_mn_neg_face = max(mn_neg_left, mn_neg_right)

        audits = {}
        all_pass = True

        for loc in ["Left", "Right"]:
            mn_pos = abs(results[loc].get("phi_Mn_bot_kNm", 0.0))
            req_mn_pos = 0.50 * max_mn_neg_face
            pass_check = mn_pos >= req_mn_pos if req_mn_pos > 0 else True
            if not pass_check:
                all_pass = False

            audits[loc] = {
                "Mn_Pos_Provided_kNm": round(mn_pos, 2),
                "Max_Mn_Neg_Face_Ref_kNm": round(max_mn_neg_face, 2),
                "Req_Mn_Pos_1_2_kNm": round(req_mn_pos, 2),
                "Pass": pass_check,
            }

        return {"Overall_Pass": all_pass, "Faces": audits, "Clause": "18.6.3.2"}

    def check_seismic_any_moment_ratios(self, results: dict) -> dict:
        """ACI 318-14 Sec 18.6.3.2: Audits Mn >= 1/4 * Mn_max across all zones (Left, Mid, Right for Top & Bot)."""
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

        for loc in ["Left", "Mid", "Right"]:
            audits[loc] = {}
            for face in ["top", "bot"]:
                val = abs(results[loc].get(f"phi_Mn_{face}_kNm", 0.0))
                if not isinstance(val, (int, float)):
                    val = 0.0
                pass_check = val >= req_mn_any if req_mn_any > 0 else True
                if not pass_check:
                    all_pass = False

                audits[loc][face] = {
                    "Mn_Provided_kNm": round(val, 2),
                    "Max_Mn_Global_Ref_kNm": round(max_mn_global, 2),
                    "Req_Mn_1_4_kNm": round(req_mn_any, 2),
                    "Pass": pass_check,
                }

        return {"Overall_Pass": all_pass, "Locations": audits, "Clause": "18.6.3.2"}

    def check_seismic_hoop_region_extent(self) -> dict:
        """Evaluates 2h hoop region length per Sec 18.6.4.1a."""
        return {"Extent_Length_mm": 2.0 * self.section.depth, "Clause": "18.6.4.1a"}

    def check_seismic_hoop_spacing(
        self, d_eff: float, db_min_flexural: float, shear_results: dict = None
    ) -> dict:
        """ACI 318-14 Sec 18.6.4.4: Hoop spacing limit within 2h zone, reporting provided spacing and Pass status."""
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
            "Pass": pass_check,
            "Clause": "18.6.4.4",
        }

    def check_seismic_non_hoop_spacing(
        self, d_eff: float, shear_results: dict = None
    ) -> dict:
        """ACI 318-14 Sec 18.6.4.6: Stirrup spacing limit beyond 2h zone (<= d/2), reporting provided spacing and Pass status."""
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
            "Pass": pass_check,
            "Clause": "18.6.4.6",
        }

    # ---------------------------------------------------------------------
    # MAIN EVALUATION DISPATCHER
    # ---------------------------------------------------------------------
    def evaluate(
        self,
        results: dict,
        options: dict = None,
        demands: dict = None,
        shear_demands: dict = None,
        shear_results: dict = None,
    ) -> dict:
        """Dispatches active code checks based on options dictionary."""
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
        vc = demands.get("vc", 0.0)
        tu = demands.get("tu", 0.0)
        tn = demands.get("tn", 1.0)
        d_eff = demands.get("d_eff", self.section.depth - 65.0)
        db_min = demands.get("db_min", 20.0)
        clear_span = demands.get("clear_span", 5.5)

        # Extract peak shear demand across ALL locations (Left & Right; face, d, 2h)
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

        # Extract all top and bottom steel areas across Left, Mid, and Right locations
        as_top_left = get_as("Left", "as_top_mm2")
        as_bot_left = get_as("Left", "as_bot_mm2")
        as_top_mid = get_as("Mid", "as_top_mm2")
        as_bot_mid = get_as("Mid", "as_bot_mm2")
        as_top_right = get_as("Right", "as_top_mm2")
        as_bot_right = get_as("Right", "as_bot_mm2")

        # 1. General Overwrites Evaluation
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
            ] = self.check_crack_control_spacing_all(results, db_min)

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
            ] = self.check_compression_lateral_support(results, shear_results, db_min)

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
                self.check_av_min_exception(vu_max, vc)
            )

        if flags["check_min_stirrup_size"]:
            evaluations["GENERAL_OVERWRITES"]["Min Stirrup Bar Size (9.7.6.4.2)"] = (
                self.check_min_stirrup_bar_size_by_main_bar(10.0, db_min)
            )

        if flags["check_concrete_cover"]:
            evaluations["GENERAL_OVERWRITES"]["Concrete Cover (9.7.1.1 & 20.5.13)"] = (
                self.check_concrete_cover(40.0)
            )

        # Mapped DCR Limit Audits across all flexural and shear zones
        evaluations["GENERAL_OVERWRITES"]["Flexural_DCR_Audit"] = (
            self.check_flexural_dcrs_all(results, flags["set_flexural_dcr"])
        )
        evaluations["GENERAL_OVERWRITES"]["Shear_DCR_Audit"] = (
            self.check_shear_dcrs_all(shear_results, flags["set_shear_dcr"])
        )
        evaluations["GENERAL_OVERWRITES"]["Torsional_DCR_Audit"] = (
            self.check_torsional_dcr(tu, tn, flags["set_torsional_dcr"])
        )

        # 2. Seismic Overwrites Evaluation
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

        # REVISION 3: Transversely Supported Flexural Bar Spacing <= 350 mm Check (Sec 18.6.4.2 & 25.7.2.4)
        if flags["check_seismic_supported_bar_spacing"]:
            evaluations["SEISMIC_OVERWRITES"][
                "Supported Bar Spacing <= 350mm (18.6.4.2 & 25.7.2.4)"
            ] = self.check_seismic_supported_bar_spacing_all(
                results, shear_results, db_min
            )

        if flags["check_seismic_hoop_spacing"]:
            evaluations["SEISMIC_OVERWRITES"]["Hoop Spacing Within 2h (18.6.4.4)"] = (
                self.check_seismic_hoop_spacing(d_eff, db_min, shear_results)
            )

        if flags["check_seismic_non_hoop_spacing"]:
            evaluations["SEISMIC_OVERWRITES"][
                "Stirrup Spacing Beyond 2h (18.6.4.6)"
            ] = self.check_seismic_non_hoop_spacing(d_eff, shear_results)

        # Probable Shear Mpr Evaluation & V_eq Dynamic Calculation
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
                "Gravity_Combo": flags["factored_gravity_load"],
                "Clause": "18.6.5.1",
            }
        else:
            v_eq_calculated = 0.0

        # Vc = 0 Evaluation using dynamic V_eq and vu_max
        if flags["check_seismic_vc_zero"]:
            vc_res = self.check_seismic_vc_zero(v_eq_calculated, vu_max, pu)
            vc_res["Gravity_Combo"] = flags["factored_gravity_load"]
            vc_res["Veq_calculated_kN"] = round(v_eq_calculated, 2)
            vc_res["Vu_max_evaluated_kN"] = round(vu_max, 2)
            evaluations["SEISMIC_OVERWRITES"]["Vc = 0 Evaluation (18.6.5.2)"] = vc_res

        return evaluations


# =========================================================================
# 5. EXCEL OVERWRITE READER SERVICE
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
        "a. At least 1/4 of maximum As": "check_integrity_reinforcement",
        "b. At least 1/6 of As": "check_integrity_reinforcement",
        "c. Longitudinal integrity reinforcement": "check_integrity_reinforcement",
        "Minimum shear reinforcement satisfies Section 9.6.3": "check_av_min",
        "Maximum spacing of shear reinforcement along length": "check_max_transverse_spacing",
        "Minimum Shear reinforcement bar size satisfies Section 9.7.6.4.2": "check_min_stirrup_size",
        "Concrete reinforcement cover satisfies Sections 9.7.1.1": "check_concrete_cover",
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

    # pylint: disable=duplicate-value
    TRUE_VALUES = {True, 1, 1.0, "TRUE", "True", "true", "x", "X"}

    def __init__(self, cell_range: str = "A1:G60", sheet_name: str = "OVERWRITES"):
        """Initializes Excel overwrite reader parameters."""
        self.cell_range = cell_range
        self.sheet_name = sheet_name

    def read_overwrites(self) -> dict:
        """Reads active Excel worksheet cell matrix and extracts overwrite parameters."""
        options = {
            "set_flexural_dcr": 1.0,
            "set_shear_dcr": 1.0,
            "set_torsional_dcr": 1.0,
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
            print(f"   • Flexural DCR Limit  : {options['set_flexural_dcr']}")
            print(f"   • Shear DCR Limit     : {options['set_shear_dcr']}")
            print(f"   • Torsional DCR Limit : {options['set_torsional_dcr']}")
            return options

        except Exception as e:
            print(
                f"\n Could not connect to active Excel instance ({e}). Using default options."
            )
            return options


# =========================================================================
# 6. HIGH-LEVEL PIPELINE MANAGER
# =========================================================================
class BeamDesignPipeline:
    """Orchestrates flexure, shear, code check evaluations, Excel overwrites, and synchronization."""

    def __init__(
        self,
        width: float = 300.0,
        depth: float = 500.0,
        fc: float = 34.48,
        fy: float = 413.69,
        overwrite_range: str = "A1:G60",
        overwrite_sheet_name: str = "OVERWRITES",
    ):
        """Initializes beam design pipeline components."""
        self.section = BeamSectionGeometry(width=width, depth=depth, fc=fc, fy=fy)
        self.flex_designer = BeamFlexuralDesign(section=self.section)
        self.shear_designer = BeamShearDesign(
            section=self.section, flexural_designer=self.flex_designer
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
        read_excel_overwrites: bool = True,
        manual_overwrites: dict = None,
    ) -> dict:
        """Executes full beam design workflow from flexure to shear and code check audits."""
        overwrites = {}
        if read_excel_overwrites:
            overwrites = self.excel_reader.read_overwrites()
        if manual_overwrites:
            overwrites.update(manual_overwrites)

        # 1. Flexural Design
        flex_results = self.flex_designer.design_beam_flexure(
            moment_demands, evaluator=self.evaluator
        )

        # 2. Preliminary Code Checks to obtain Mpr and Vc_zero status
        evals_prelim = self.evaluator.evaluate(
            results=flex_results,
            options=overwrites,
            demands=analysis_demands,
            shear_demands=shear_demands,
        )

        # 3. Seismic Shear Calculation
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

        vc_override = (
            0.0
            if evals_prelim["SEISMIC_OVERWRITES"]["Vc = 0 Evaluation (18.6.5.2)"][
                "Vc_Zero"
            ]
            else None
        )

        # 4. Shear Design (Evaluating Face, d, and 2H)
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

        # 5. Synchronize Flexure Layout with Shear Legs
        final_flex_results = self.flex_designer.synchronize_with_shear_design(
            flex_results, shear_results
        )

        # 6. Final Code Checks with fully synchronized flexure and shear results
        final_evals = self.evaluator.evaluate(
            results=final_flex_results,
            options=overwrites,
            demands=analysis_demands,
            shear_demands=shear_demands,
            shear_results=shear_results,
        )

        return {
            "Flexural_Results": final_flex_results,
            "Shear_Results": shear_results,
            "Code_Evaluations": final_evals,
            "Applied_Overwrites": overwrites,
        }


# =========================================================================
# HELPER FUNCTIONS FOR FORMATTED AUDIT OUTPUT
# =========================================================================
def print_nested_audit(data, indent: int = 0):
    """Recursively formats and prints nested dictionary audits with clear, indented lines."""
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
    """Formats shear design results into a structured, easily scannable section."""
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
# MAIN EXECUTION BLOCK
# =========================================================================
if __name__ == "__main__":
    # 1. BEAM GEOMETRY & MATERIAL PARAMETERS
    beam_width = 300.0  # mm
    beam_depth = 500.0  # mm
    fc_concrete = 34.48  # f'c = 34.48 MPa
    fy_rebar = 413.69  # fy = 413.69 MPa

    # 2. EXCEL OVERWRITE READER CELL TARGETS
    excel_sheet_name = "OVERWRITES"
    excel_cell_range = "A1:G60"
    enable_excel_reading = True

    # 3. DESIGN DEMANDS
    moment_demands = {
        "Left": {"top": 180.0, "bot": 70.0},  # kN·m
        "Mid": {"top": 40.0, "bot": 150.0},  # kN·m
        "Right": {"top": 200.0, "bot": 80.0},  # kN·m
    }

    shear_demands = {
        "Left": {"v_face": 135.0, "v_d": 122.0, "v_2h": 110.0},  # kN
        "Right": {"v_face": 140.0, "v_d": 126.0, "v_2h": 112.0},  # kN
    }

    analysis_demands = {
        "pu": 15.0,  # Factored axial force (kN)
        "vc": 90.0,  # Concrete shear capacity (kN)
        "vs": 40.0,  # Required steel shear capacity (kN)
        "tu": 12.0,  # Factored torsional moment (kN·m)
        "tn": 25.0,  # Nominal torsional capacity (kN·m)
        "d_eff": 435.0,  # Effective depth d (mm)
        "db_min": 20.0,  # Min flexural bar diameter (mm)
        "clear_span": 5.5,  # Clear span Ln (m)
        "vg": 45.0,  # Factored gravity shear Vg (kN)
    }

    # 4. INITIALIZE & RUN PIPELINE
    pipeline = BeamDesignPipeline(
        width=beam_width,
        depth=beam_depth,
        fc=fc_concrete,
        fy=fy_rebar,
        overwrite_range=excel_cell_range,
        overwrite_sheet_name=excel_sheet_name,
    )

    results = pipeline.run_pipeline(
        moment_demands=moment_demands,
        shear_demands=shear_demands,
        analysis_demands=analysis_demands,
        read_excel_overwrites=enable_excel_reading,
    )

    # 5. PRINT CLEANED & STRUCTURED RESULTS
    print("\n" + "=" * 75)
    print(" 1. FINAL FLEXURAL DESIGN RESULTS (POST-SHEAR SYNC)")
    print("=" * 75)
    for loc, data in results["Flexural_Results"].items():
        print(f"\n Location: {loc.upper()}")
        print(
            f"  • Top Bars: {data['n_top_bars']} bars | Area:"
            f" {data['as_top_mm2']:.2f} mm² | φMn Top: {data['phi_Mn_top_kNm']:.2f}"
            f" kN·m (Demand: {data['Mu_top_kNm']:.2f} kN·m)"
        )
        print(
            f"  • Bot Bars: {data['n_bot_bars']} bars | Area:"
            f" {data['as_bot_mm2']:.2f} mm² | φMn Bot: {data['phi_Mn_bot_kNm']:.2f}"
            f" kN·m (Demand: {data['Mu_bot_kNm']:.2f} kN·m)"
        )

    print("\n" + "=" * 75)
    print(" 2. SHEAR DESIGN RESULTS")
    print("=" * 75)
    print(
        "Governing Uniform Leg Count:"
        f" {results['Shear_Results']['Governing_Uniform_Legs']} Legs"
    )

    for loc in ["Left", "Right"]:
        print(f"\n--- {loc.upper()} SUPPORT ZONE ---")
        for zone_key, zone_data in results["Shear_Results"][loc].items():
            print_shear_zone_report(zone_key, zone_data)

    print("\n" + "=" * 75)
    print(" 3. ACI 318-14 CODE CHECK AUDIT REPORT")
    print("=" * 75)
    for category, checks in results["Code_Evaluations"].items():
        print(f"\n [ {category.replace('_', ' ')} ]")
        for check_name, data in checks.items():
            print(f"\n  • {check_name}:")
            print_nested_audit(data, indent=6)
    print("\n" + "=" * 75)
