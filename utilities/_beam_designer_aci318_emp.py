"""Reinforced Concrete Beam Structural Design Engine per ACI 318M-14.

Includes automated flexural design, shear design, torsion design,
code check evaluations, live Excel overwrite integration, and a
direct compliance resolver that updates geometry and rebar without demand inflation.
"""


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


import math
from dataclasses import dataclass
from typing import Dict, List
import pandas as pd


# =============================================================================
# DATA STRUCTURES FOR MULTI-LOCATION DEMANDS
# =============================================================================
@dataclass
class FlexureLocationDemand:
    location_name: str
    Mu_neg: float  # kN-m
    Mu_pos: float  # kN-m


@dataclass
class TransverseLocationDemand:
    location_name: str
    Vu: float  # kN
    Tu: float  # kN-m


# =============================================================================
# FLEXURE DESIGN CLASS (ACI 318-19)
# =============================================================================
class BeamFlexureDesign:
    """
    Performs ACI 318 flexural design including:
    - Automatic determination of minimum bar counts for <= 150mm longitudinal clear spacing.
    - Explicit ACI 318-19 §24.3.2 crack control spacing verification (s_max_crack).
    - Layering & effective depth calculations (up to 2 layers).
    - Moment capacity solving (phi*Mn) & failure mode classification.
    - Longitudinal torsion reinforcement additions (Al/4) for top and bottom faces.
    """

    def __init__(
        self,
        width: float,
        height: float,
        fc: float = 28.0,
        fy: float = 415.0,
        fyt: float = 415.0,
        dmain: float = 25.0,
        dstirrup: float = 10.0,
        cc: float = 40.0,
        Mu_neg: float = 0.0,
        Mu_pos: float = 0.0,
    ):
        self.width = width
        self.height = height
        self.fc = fc
        self.fy = fy
        self.fyt = fyt
        self.dmain = dmain
        self.dstirrup = dstirrup
        self.cc = cc
        self.Mu_neg = Mu_neg
        self.Mu_pos = Mu_pos

        self.bheta = max(0.65, min(0.85, 0.85 - 0.05 * (self.fc - 28.0) / 7.0))
        self.max_bar_per_layer = self.calculate_max_bars_per_layer()

        n_min_150 = self.get_min_bars_for_150mm_spacing()
        self.n_top = n_min_150
        self.n_bot = n_min_150

    def calculate_max_bars_per_layer(self) -> int:
        clear_width = self.width - 2 * (self.cc + self.dstirrup)
        min_spacing = max(25.0, self.dmain)
        if clear_width < self.dmain:
            return 1
        return int((clear_width + min_spacing) // (self.dmain + min_spacing))

    def get_min_bars_for_150mm_spacing(self) -> int:
        clear_center_width = self.width - 2 * (
            self.cc + self.dstirrup + (self.dmain / 2.0)
        )
        max_center_spacing = 150.0 + self.dmain
        min_spaces = math.ceil(clear_center_width / max_center_spacing)
        return max(2, min_spaces + 1)

    def calculate_layer_distribution(self, n_bars: int) -> list:
        layers = []
        remaining = n_bars
        while remaining > 0:
            count = min(remaining, self.max_bar_per_layer)
            layers.append(count)
            remaining -= count
        return layers

    def compute_effective_depths(self, is_negative_moment: bool = True):
        vert_clear_spacing = max(25.0, self.dmain)
        center_spacing = self.dmain + vert_clear_spacing

        top_layers = self.calculate_layer_distribution(self.n_top)
        top_d1 = self.cc + self.dstirrup + (self.dmain / 2.0)
        top_moments = sum(
            count * (top_d1 + i * center_spacing) for i, count in enumerate(top_layers)
        )
        d_top_centroid = top_moments / self.n_top if self.n_top > 0 else top_d1

        bot_layers = self.calculate_layer_distribution(self.n_bot)
        bot_d1_from_bottom = self.cc + self.dstirrup + (self.dmain / 2.0)
        bot_moments_from_top = sum(
            count * (self.height - (bot_d1_from_bottom + i * center_spacing))
            for i, count in enumerate(bot_layers)
        )
        d_bot_centroid = (
            bot_moments_from_top / self.n_bot
            if self.n_bot > 0
            else (self.height - bot_d1_from_bottom)
        )

        if is_negative_moment:
            self.d_tens = self.height - d_top_centroid
            self.d_prime = self.height - d_bot_centroid
        else:
            self.d_tens = d_bot_centroid
            self.d_prime = d_top_centroid

        return self.d_tens, self.d_prime

    def check_crack_control_spacing(self, is_negative_moment: bool = True) -> dict:
        fs = (2.0 / 3.0) * self.fy
        s_crack_1 = 380.0 * (280.0 / fs) - 2.5 * self.cc
        s_crack_2 = 300.0 * (280.0 / fs)
        s_max_crack = min(s_crack_1, s_crack_2)

        n_layer_1 = min(
            self.n_top if is_negative_moment else self.n_bot,
            self.max_bar_per_layer,
        )

        if n_layer_1 > 1:
            clear_center_width = self.width - 2 * (
                self.cc + self.dstirrup + (self.dmain / 2.0)
            )
            s_actual_c2c = clear_center_width / (n_layer_1 - 1)
        else:
            s_actual_c2c = 0.0

        return {
            "s_max_crack": s_max_crack,
            "s_actual_c2c": s_actual_c2c,
            "crack_control_pass": s_actual_c2c <= s_max_crack,
        }

    def solve_moment_capacity(self, is_negative_moment: bool = True) -> dict:
        d_tens, d_prime = self.compute_effective_depths(is_negative_moment)
        As_tens = (self.n_top if is_negative_moment else self.n_bot) * (
            (math.pi / 4) * self.dmain**2
        )

        c = (As_tens * self.fy) / (0.85 * self.fc * self.bheta * self.width)
        a = self.bheta * c

        et = 0.003 * (d_tens - c) / c if c > 0 else 0.005
        phi = 0.90 if et >= 0.005 else max(0.65, 0.65 + (et - 0.002) * (0.25 / 0.003))

        Mn = As_tens * self.fy * (d_tens - a / 2.0) / 1e6
        phi_Mn = phi * Mn

        return {
            "phi_Mn": phi_Mn,
            "et": et,
            "phi": phi,
            "failure_mode": (
                "Tension-Controlled" if et >= 0.005 else "Compression-Controlled"
            ),
            "As_provided": As_tens,
        }

    def check_reinforcement_limits(self, is_negative_moment: bool = True):
        d_tens, _ = self.compute_effective_depths(is_negative_moment)
        As_provided = (self.n_top if is_negative_moment else self.n_bot) * (
            (math.pi / 4) * self.dmain**2
        )

        As_min_1 = (0.25 * math.sqrt(self.fc) / self.fy) * self.width * d_tens
        As_min_2 = (1.4 / self.fy) * self.width * d_tens
        As_min_code = max(As_min_1, As_min_2)

        n_min_150 = self.get_min_bars_for_150mm_spacing()
        As_min_150mm_spacing = n_min_150 * ((math.pi / 4) * self.dmain**2)
        As_min_governing = max(As_min_code, As_min_150mm_spacing)

        crack_check = self.check_crack_control_spacing(is_negative_moment)

        return {
            "As_min_governing": As_min_governing,
            "min_pass": As_provided >= As_min_governing,
            "crack_pass": (
                crack_check["crack_pass"]
                if "crack_pass" in crack_check
                else crack_check["crack_control_pass"]
            ),
        }

    def design_beam(self, Al_top_req: float = 0.0, Al_bot_req: float = 0.0) -> dict:
        n_min_150 = self.get_min_bars_for_150mm_spacing()
        self.n_top = max(self.n_top, n_min_150)
        self.n_bot = max(self.n_bot, n_min_150)

        max_allowed_bars = 3 * self.max_bar_per_layer
        self.rebar_congestion_exceeded = False

        while True:
            top_layers = self.calculate_layer_distribution(self.n_top)
            bot_layers = self.calculate_layer_distribution(self.n_bot)

            # Cap layer depth at 3 layers and flag congestion if exceeded
            if len(top_layers) > 3 or len(bot_layers) > 3:
                self.rebar_congestion_exceeded = True
                if len(top_layers) > 3:
                    self.n_top = max_allowed_bars
                if len(bot_layers) > 3:
                    self.n_bot = max_allowed_bars
                break

            res_neg = self.solve_moment_capacity(is_negative_moment=True)
            res_pos = self.solve_moment_capacity(is_negative_moment=False)

            limits_neg = self.check_reinforcement_limits(is_negative_moment=True)
            limits_pos = self.check_reinforcement_limits(is_negative_moment=False)

            As_top_provided = self.n_top * ((math.pi / 4) * self.dmain**2)
            As_bot_provided = self.n_bot * ((math.pi / 4) * self.dmain**2)

            neg_satisfied = (
                (res_neg["phi_Mn"] >= self.Mu_neg)
                and limits_neg["min_pass"]
                and limits_neg["crack_pass"]
                and (As_top_provided >= (limits_neg["As_min_governing"] + Al_top_req))
            )

            pos_satisfied = (
                (res_pos["phi_Mn"] >= self.Mu_pos)
                and limits_pos["min_pass"]
                and limits_pos["crack_pass"]
                and (As_bot_provided >= (limits_pos["As_min_governing"] + Al_bot_req))
            )

            if neg_satisfied and pos_satisfied:
                break

            if not neg_satisfied:
                if self.n_top < max_allowed_bars:
                    self.n_top += 1
                else:
                    self.rebar_congestion_exceeded = True

            if not pos_satisfied:
                if self.n_bot < max_allowed_bars:
                    self.n_bot += 1
                else:
                    self.rebar_congestion_exceeded = True

            if (self.n_top >= max_allowed_bars and not neg_satisfied) or (
                self.n_bot >= max_allowed_bars and not pos_satisfied
            ):
                break

        status_msg = (
            "PASSED"
            if not self.rebar_congestion_exceeded
            else "MAX REINF REACHED (>3 LAYERS - INCREASE SECTION)"
        )
        return {
            "n_top": self.n_top,
            "n_bot": self.n_bot,
            "rebar_congestion_exceeded": self.rebar_congestion_exceeded,
            "status": status_msg,
        }


# =============================================================================
# SHEAR & TORSION DESIGN CLASSES
# =============================================================================
class BeamShearDesign:
    def __init__(
        self,
        width: float,
        height: float,
        d_tens: float,
        fc: float,
        fyt: float,
        dstirrup: float,
        n_legs: int,
        suppress_Vc: bool = False,
    ):
        self.width = width
        self.height = height
        self.d_tens = d_tens
        self.fc = fc
        self.fyt = fyt
        self.dstirrup = dstirrup
        self.n_legs = max(2, n_legs)
        self.suppress_Vc = suppress_Vc

    def solve_shear_capacity(self, Vu: float) -> dict:
        phi_v = 0.75
        Vc = (
            0.0
            if self.suppress_Vc
            else (0.17 * math.sqrt(self.fc) * self.width * self.d_tens / 1000.0)
        )
        Vs_max = 0.66 * math.sqrt(self.fc) * self.width * self.d_tens / 1000.0

        max_allowable_phi_Vn = phi_v * (Vc + Vs_max)
        shear_failed = Vu > max_allowable_phi_Vn

        # Calculate required Vs (clamped if section fails)
        Vs_req = max(0.0, (Vu / phi_v) - Vc)
        Av_s_demand = (
            (Vs_req * 1000.0) / (self.fyt * self.d_tens) if Vs_req > 0 else 0.0
        )

        if Vs_req <= 0.33 * math.sqrt(self.fc) * self.width * self.d_tens / 1000.0:
            s_max = min(self.d_tens / 2.0, 600.0)
        else:
            s_max = min(self.d_tens / 4.0, 300.0)

        return {
            "Vu": Vu,
            "Vc": Vc,
            "Vs_req": Vs_req,
            "Av_s_demand": Av_s_demand,
            "s_max_code": s_max,
            "shear_failed": shear_failed,
            "max_allowable_phi_Vn": max_allowable_phi_Vn,
            "status_msg": (
                "PASSED"
                if not shear_failed
                else f"FAILED (Vu = {Vu:.1f} kN > phi*Vn,max = {max_allowable_phi_Vn:.1f} kN)"
            ),
        }


class BeamTorsionDesign:
    def __init__(
        self,
        width: float,
        height: float,
        d_tens: float,
        fc: float,
        fy: float,
        fyt: float,
        dstirrup: float,
        cc: float,
    ):
        self.width = width
        self.height = height
        self.d_tens = d_tens
        self.fc = fc
        self.fy = fy
        self.fyt = fyt
        self.dstirrup = dstirrup
        self.cc = cc

        self.Acp = self.width * self.height
        self.Pcp = 2 * (self.width + self.height)
        self.x1 = self.width - 2 * self.cc - self.dstirrup
        self.y1 = self.height - 2 * self.cc - self.dstirrup
        self.Aoh = self.x1 * self.y1
        self.A_o = 0.85 * self.Aoh
        self.p_h = 2 * (self.x1 + self.y1)

    def solve_torsion_capacity(self, Tu: float, Vu: float) -> dict:
        phi_t = 0.75
        Tth = (0.083 * phi_t * math.sqrt(self.fc) * (self.Acp**2 / self.Pcp)) / 1e6
        Tcr = (0.33 * phi_t * math.sqrt(self.fc) * (self.Acp**2 / self.Pcp)) / 1e6

        torsion_required = Tu > Tth
        Tu_design = min(Tu, phi_t * Tcr) if torsion_required else Tu

        shear_stress = (Vu * 1000.0) / (self.width * self.d_tens)
        torsion_stress = (Tu_design * 1e6 * self.p_h) / (1.7 * (self.Aoh**2))
        combined_stress = math.sqrt(shear_stress**2 + torsion_stress**2)
        allowable_stress = phi_t * (
            0.17 * math.sqrt(self.fc) + 0.66 * math.sqrt(self.fc)
        )

        torsion_failed = combined_stress > allowable_stress

        if torsion_required:
            Tn_req = (Tu_design * 1e6) / phi_t
            At_s_demand = Tn_req / (2.0 * self.A_o * self.fyt)
            Al_demand = At_s_demand * self.p_h * (self.fyt / self.fy)

            term_1 = 0.42 * math.sqrt(self.fc) * (self.Acp / self.fyt)
            Al_min_a = term_1 - At_s_demand * self.p_h * (self.fyt / self.fy)
            Al_min_b = term_1 - (0.175 * self.width / self.fyt) * self.p_h * (
                self.fyt / self.fy
            )
            Al_design = max(Al_demand, max(0.0, min(Al_min_a, Al_min_b)))
        else:
            At_s_demand = 0.0
            Al_design = 0.0

        Av_2At_s_min = max(
            (0.062 * math.sqrt(self.fc) * self.width) / self.fyt,
            (0.35 * self.width) / self.fyt,
        )
        s_max_torsion = min(self.p_h / 8.0, 300.0)

        return {
            "Tth": Tth,
            "Tcr": Tcr,
            "combined_stress": combined_stress,
            "allowable_stress": allowable_stress,
            "At_s_demand": At_s_demand,
            "Av_2At_s_min": Av_2At_s_min,
            "Al_design": Al_design,
            "s_max_torsion": s_max_torsion,
            "torsion_failed": torsion_failed,
            "status_msg": (
                "PASSED"
                if not torsion_failed
                else f"FAILED (v_uv = {combined_stress:.2f} MPa > v_allow = {allowable_stress:.2f} MPa)"
            ),
        }


# =============================================================================
# SEISMIC DESIGN CLASS (ACI 318 SMF CHAPTER 18)
# =============================================================================
class BeamSeismicDesign:
    """
    Performs ACI 318 Special Moment Frame (SMF) Seismic Checks (Chapter 18):
    A. Maximum tensile reinforcement ratio check (rho <= 0.025) top and bottom.
    B. Joint face positive moment capacity check: M_pos >= 0.50 * max(M_neg_left, M_neg_right).
    C. Full span minimum moment capacity check: M_pos/neg >= 0.25 * max(M_neg_left, M_neg_right).
    D. Probable shear capacity (V_pr) calculation considering f_s = 1.25 * f_y.
    E. V_c = 0 concrete shear capacity suppression check based on gravity vs seismic ratio and axial load.
    """

    def __init__(
        self,
        flexure_engines: Dict[str, object],
        clear_span: float,
        Pu_axial: float = 0.0,
        enable_seismic: bool = True,
    ):
        self.flexure_engines = flexure_engines
        self.clear_span = clear_span
        self.Pu_axial = Pu_axial
        self.enable_seismic = enable_seismic

    def check_reinforcement_ratio_limits(self) -> dict:
        results = {}
        all_passed = True

        for loc_name, eng in self.flexure_engines.items():
            d_neg, _ = eng.compute_effective_depths(is_negative_moment=True)
            d_pos, _ = eng.compute_effective_depths(is_negative_moment=False)

            As_top = eng.n_top * ((math.pi / 4) * eng.dmain**2)
            As_bot = eng.n_bot * ((math.pi / 4) * eng.dmain**2)

            rho_top = As_top / (eng.width * d_neg)
            rho_bot = As_bot / (eng.width * d_pos)

            top_pass = rho_top <= 0.025
            bot_pass = rho_bot <= 0.025

            if not (top_pass and bot_pass):
                all_passed = False

            results[loc_name] = {
                "rho_top": rho_top,
                "rho_bot": rho_bot,
                "top_pass": top_pass,
                "bot_pass": bot_pass,
            }

        return {"all_passed": all_passed, "locations": results}

    def check_flexural_capacity_ratios(self) -> dict:
        left_eng = self.flexure_engines["Left Support Face"]
        right_eng = self.flexure_engines["Right Support Face"]

        res_left = left_eng.solve_moment_capacity(is_negative_moment=True)
        res_right = right_eng.solve_moment_capacity(is_negative_moment=True)

        Mn_neg_left = res_left["phi_Mn"] / res_left["phi"]
        Mn_neg_right = res_right["phi_Mn"] / res_right["phi"]

        max_M_neg_support = max(Mn_neg_left, Mn_neg_right)

        req_half_cap = 0.50 * max_M_neg_support
        req_quarter_cap = 0.25 * max_M_neg_support

        results = {}
        all_passed = True

        for loc_name, eng in self.flexure_engines.items():
            r_neg = eng.solve_moment_capacity(is_negative_moment=True)
            r_pos = eng.solve_moment_capacity(is_negative_moment=False)

            Mn_neg = r_neg["phi_Mn"] / r_neg["phi"]
            Mn_pos = r_pos["phi_Mn"] / r_pos["phi"]

            pass_quarter = (Mn_neg >= req_quarter_cap) and (Mn_pos >= req_quarter_cap)
            pass_half = True

            if "Support" in loc_name:
                pass_half = Mn_pos >= req_half_cap

            if not (pass_quarter and pass_half):
                all_passed = False

            results[loc_name] = {
                "Mn_neg": Mn_neg,
                "Mn_pos": Mn_pos,
                "pass_half": pass_half,
                "pass_quarter": pass_quarter,
            }

        return {
            "all_passed": all_passed,
            "max_M_neg_support": max_M_neg_support,
            "req_half_cap": req_half_cap,
            "req_quarter_cap": req_quarter_cap,
            "locations": results,
        }

    def compute_probable_moment(
        self,
        eng: object,
        is_negative_moment: bool,
        Al_addition: float = 0.0,
    ) -> float:
        """
        Computes probable flexural strength M_pr considering stress f_s = 1.25 * f_y.
        Includes additional longitudinal torsion steel (Al/4) allocated to the face.
        """
        d_tens, _ = eng.compute_effective_depths(is_negative_moment)

        # Base flexural steel + Al/4 torsion addition
        n_bars = eng.n_top if is_negative_moment else eng.n_bot
        As_flexure = n_bars * ((math.pi / 4) * eng.dmain**2)
        As_total_tens = As_flexure + Al_addition

        f_seismic = 1.25 * eng.fy
        a_pr = (As_total_tens * f_seismic) / (0.85 * eng.fc * eng.width)
        M_pr = As_total_tens * f_seismic * (d_tens - a_pr / 2.0) / 1e6  # kN-m
        return M_pr

    def evaluate_seismic_shear_demands(
        self,
        Vu_gravity_left: float,
        Vu_gravity_right: float,
        side_distributions: dict,
        Vu_envelope_max_left: float = None,  # Max shear from all load combos
        Vu_envelope_max_right: float = None,  # Max shear from all load combos
    ) -> dict:
        if not self.enable_seismic:
            return {
                "seismic_active": False,
                "Vc_zero_left": False,
                "Vc_zero_right": False,
                "Vu_seismic_left": abs(Vu_gravity_left),
                "Vu_seismic_right": abs(Vu_gravity_right),
                "V_sway_max": 0.0,
            }

        left_eng = self.flexure_engines["Left Support Face"]
        right_eng = self.flexure_engines["Right Support Face"]

        Al_left = side_distributions["Left Support (d_eff)"]["Al_top_req"]
        Al_right = side_distributions["Right Support (d_eff)"]["Al_top_req"]

        # 1. Probable Moments (fs = 1.25 * fy)
        M_pr_top_left = self.compute_probable_moment(
            left_eng, is_negative_moment=True, Al_addition=Al_left
        )
        M_pr_bot_left = self.compute_probable_moment(
            left_eng, is_negative_moment=False, Al_addition=Al_left
        )
        M_pr_top_right = self.compute_probable_moment(
            right_eng, is_negative_moment=True, Al_addition=Al_right
        )
        M_pr_bot_right = self.compute_probable_moment(
            right_eng, is_negative_moment=False, Al_addition=Al_right
        )

        # 2. Sway Shear
        L_n_m = self.clear_span / 1000.0
        V_sway_cw = (M_pr_top_left + M_pr_bot_right) / L_n_m
        V_sway_ccw = (M_pr_bot_left + M_pr_top_right) / L_n_m
        V_sway_max = max(V_sway_cw, V_sway_ccw)

        # 3. Seismic Combo Demand (Gravity + Sway)
        Vu_seismic_left = abs(Vu_gravity_left) + V_sway_max
        Vu_seismic_right = abs(Vu_gravity_right) + V_sway_max

        # 4. Total Governing Envelope Demand (Use Seismic Combo if Envelope not provided)
        Vu_total_max_left = (
            Vu_envelope_max_left
            if Vu_envelope_max_left is not None
            else Vu_seismic_left
        )
        Vu_total_max_right = (
            Vu_envelope_max_right
            if Vu_envelope_max_right is not None
            else Vu_seismic_right
        )

        # 5. Check E: Vc = 0 Suppression against True Max Design Shear
        cond1_left = (
            (V_sway_max / Vu_total_max_left) >= 0.50 if Vu_total_max_left > 0 else False
        )
        cond1_right = (
            (V_sway_max / Vu_total_max_right) >= 0.50
            if Vu_total_max_right > 0
            else False
        )

        Ag = left_eng.width * left_eng.height
        Pu_limit = (left_eng.fc * Ag / 20.0) / 1000.0
        cond2 = self.Pu_axial < Pu_limit

        Vc_zero_left = cond1_left and cond2
        Vc_zero_right = cond1_right and cond2

        return {
            "seismic_active": True,
            "M_pr_top_left": M_pr_top_left,
            "M_pr_bot_left": M_pr_bot_left,
            "M_pr_top_right": M_pr_top_right,
            "M_pr_bot_right": M_pr_bot_right,
            "V_sway_max": V_sway_max,
            "Vu_seismic_left": Vu_seismic_left,
            "Vu_seismic_right": Vu_seismic_right,
            "Vc_zero_left": Vc_zero_left,
            "Vc_zero_right": Vc_zero_right,
            "Pu_limit": Pu_limit,
        }


# =============================================================================
# HELPER & POST-CHECK FUNCTIONS
# =============================================================================
def distribute_longitudinal_torsion_and_skin(
    Al_design: float,
    db_main: float,
    db_side: float,
    height: float,
    width: float,
    d_tens: float,
    fy: float,
    cc: float,
    dstirrup: float,
) -> dict:
    """
    Distributes longitudinal torsion steel (Al) across 4 faces:
    - Top Face: Al/4
    - Bottom Face: Al/4
    - Side Web Faces: Al/2 combined (Al/4 per face)
    Enforces side spacing and skin reinforcement ONLY if Al > 0 or h > 900mm.
    """
    Al_face = Al_design / 4.0
    Al_top_req = Al_face
    Al_bot_req = Al_face
    Al_sides_req = 2 * Al_face

    web_height = height - 2 * (cc + dstirrup + db_main / 2.0)

    # 1. Check if Torsion or Deep Beam Skin Reinforcement is Active
    torsion_active = Al_design > 0.0
    deep_beam_active = height > 900.0

    if not torsion_active and not deep_beam_active:
        # No web skin steel required!
        return {
            "Al_top_req": 0.0,
            "Al_bot_req": 0.0,
            "Al_sides_req": 0.0,
            "n_side_bars_total": 0,
            "n_side_per_face": 0,
            "side_bar_spacing": 0.0,
            "s_max_side": 0.0,
        }

    # 2. Determine Spacing Limits when Active
    s_max_side = 300.0
    if deep_beam_active:
        s_skin_code = min(d_tens / 6.0, 300.0, (1000.0 * (375.0 / fy)) - 2.5 * cc)
        s_max_side = min(s_max_side, s_skin_code)

    # 3. Calculate Required Side Bars (Strength vs Spacing)
    min_spaces = math.ceil(web_height / s_max_side)
    n_side_spacing_per_face = max(0, min_spaces - 1)

    area_single_side_bar = (math.pi / 4) * (db_side**2)
    n_side_strength_per_face = (
        math.ceil((Al_sides_req / 2.0) / area_single_side_bar)
        if Al_sides_req > 0
        else 0
    )

    n_side_per_face = max(n_side_strength_per_face, n_side_spacing_per_face)
    n_side_total = n_side_per_face * 2
    s_actual_side = (
        web_height / (n_side_per_face + 1) if n_side_per_face > 0 else web_height
    )

    return {
        "Al_top_req": Al_top_req,
        "Al_bot_req": Al_bot_req,
        "Al_sides_req": Al_sides_req,
        "n_side_bars_total": n_side_total,
        "n_side_per_face": n_side_per_face,
        "side_bar_spacing": s_actual_side,
        "s_max_side": s_max_side,
    }


def check_stirrup_leg_anchorage(
    n_top: int,
    n_bot: int,
    n_legs: int,
    max_bar_per_layer: int,
    dmain: float,
    dstirrup: float,
    width: float,
    cc: float,
) -> dict:
    top_layer_1 = min(n_top, max_bar_per_layer)
    bot_layer_1 = min(n_bot, max_bar_per_layer)

    if n_legs > max_bar_per_layer:
        raise ValueError(
            f"SECTION DETAILING ERROR: Beam width ({width:.0f} mm) fits max "
            f"{max_bar_per_layer} main bars per layer, but requires {n_legs} stirrup legs!"
        )

    top_deficit = max(0, n_legs - top_layer_1)
    bot_deficit = max(0, n_legs - bot_layer_1)

    final_n_top = n_top + top_deficit
    final_n_bot = n_bot + bot_deficit

    anchorage_passed = (top_deficit == 0) and (bot_deficit == 0)

    return {
        "anchorage_passed": anchorage_passed,
        "final_n_top": final_n_top,
        "final_n_bot": final_n_bot,
        "top_bars_added": top_deficit,
        "bot_bars_added": bot_deficit,
        "top_layer_1_count": min(final_n_top, max_bar_per_layer),
        "bot_layer_1_count": min(final_n_bot, max_bar_per_layer),
    }


def check_alternating_tie_legs(
    n_top: int, n_bot: int, current_n_legs: int, max_bar_per_layer: int
) -> int:
    top_layer_1 = min(n_top, max_bar_per_layer)
    bot_layer_1 = min(n_bot, max_bar_per_layer)

    req_top = (top_layer_1 // 2) + 1
    req_bot = (bot_layer_1 // 2) + 1

    return max(current_n_legs, req_top, req_bot)


def build_governing_transverse_demands(
    Vu_deff_left: float,
    Tu_deff_left: float,
    Vu_2h_left: float,
    Tu_2h_left: float,
    Vu_2h_right: float,
    Tu_2h_right: float,
    Vu_deff_right: float,
    Tu_deff_right: float,
) -> List[TransverseLocationDemand]:
    Vu_2h_gov = max(Vu_2h_left, Vu_2h_right)
    Tu_2h_gov = max(Tu_2h_left, Tu_2h_right)

    return [
        TransverseLocationDemand("Left Support (d_eff)", Vu_deff_left, Tu_deff_left),
        TransverseLocationDemand("Interior Web (Gov. 2h)", Vu_2h_gov, Tu_2h_gov),
        TransverseLocationDemand("Right Support (d_eff)", Vu_deff_right, Tu_deff_right),
    ]


def get_location_effective_depth(
    loc_name: str, flexure_engines: Dict[str, BeamFlexureDesign]
) -> float:
    if "Left" in loc_name:
        engine = flexure_engines["Left Support Face"]
    elif "Right" in loc_name:
        engine = flexure_engines["Right Support Face"]
    else:
        engine = flexure_engines["Midspan Zone"]

    d_neg, _ = engine.compute_effective_depths(is_negative_moment=True)
    d_pos, _ = engine.compute_effective_depths(is_negative_moment=False)
    return min(d_neg, d_pos)


def get_layer_columns(engine, is_top: bool) -> tuple:
    """Returns (L1, L2, L3) physical elevation counts (Topmost to Bottommost)."""
    n_bars = engine.n_top if is_top else engine.n_bot
    layers = engine.calculate_layer_distribution(n_bars)

    if is_top:
        l1 = layers[0] if len(layers) > 0 else 0
        l2 = layers[1] if len(layers) > 1 else 0
        l3 = layers[2] if len(layers) > 2 else 0
        return l1, l2, l3
    else:
        l3 = layers[0] if len(layers) > 0 else 0
        l2 = layers[1] if len(layers) > 1 else 0
        l1 = layers[2] if len(layers) > 2 else 0
        return l1, l2, l3


def execute_beam_design(
    df_beam_props: pd.DataFrame,
    df_frame_forces: pd.DataFrame,
    enable_seismic_design: bool,
    gravity_combo_name: str,
    Pu_axial_load: float = 50.0,
) -> pd.DataFrame:
    """Executes the full beam design pipeline and returns the results DataFrame."""

    # =============================================================================
    # 2. BUILD EXTRACTION DEMANDS DATAFRAME PER COMBO
    # =============================================================================
    design_rows = []

    for _, prop_row in df_beam_props.iterrows():
        u_name = prop_row["UniqueName"]
        h = prop_row.get("Depth", prop_row.get("Height", 500))
        two_h = 2.0 * h

        df_forces_beam = df_frame_forces[df_frame_forces["UniqueName"] == u_name]

        if not df_forces_beam.empty:
            min_st = df_forces_beam["Station"].min()
            max_st = df_forces_beam["Station"].max()
            span_length = max_st - min_st

            m_left_boundary = min_st + 0.25 * span_length
            m_right_boundary = max_st - 0.25 * span_length

            v_left_boundary = min_st + two_h
            v_right_boundary = max_st - two_h

            combos = df_forces_beam["Combo"].unique()

            for combo in combos:
                df_combo = df_forces_beam[df_forces_beam["Combo"] == combo]

                df_left_m = df_combo[df_combo["Station"] <= m_left_boundary]
                df_mid_m = df_combo[
                    (df_combo["Station"] > m_left_boundary)
                    & (df_combo["Station"] < m_right_boundary)
                ]
                df_right_m = df_combo[df_combo["Station"] >= m_right_boundary]

                df_left_vt = df_combo[df_combo["Station"] <= v_left_boundary]
                df_mid_vt = df_combo[
                    (df_combo["Station"] >= v_left_boundary)
                    & (df_combo["Station"] <= v_right_boundary)
                ]
                df_right_vt = df_combo[df_combo["Station"] >= v_right_boundary]

                Mneg_left = (
                    abs(min(0.0, df_left_m["M3"].min())) if not df_left_m.empty else 0.0
                )
                Mpos_left = (
                    max(0.0, df_left_m["M3"].max()) if not df_left_m.empty else 0.0
                )

                Mneg_mid = (
                    abs(min(0.0, df_mid_m["M3"].min())) if not df_mid_m.empty else 0.0
                )
                Mpos_mid = max(0.0, df_mid_m["M3"].max()) if not df_mid_m.empty else 0.0

                Mneg_right = (
                    abs(min(0.0, df_right_m["M3"].min()))
                    if not df_right_m.empty
                    else 0.0
                )
                Mpos_right = (
                    max(0.0, df_right_m["M3"].max()) if not df_right_m.empty else 0.0
                )

                Vd_left = df_left_vt["V2"].abs().max() if not df_left_vt.empty else 0.0
                V2h = df_mid_vt["V2"].abs().max() if not df_mid_vt.empty else 0.0
                Vd_right = (
                    df_right_vt["V2"].abs().max() if not df_right_vt.empty else 0.0
                )

                Td_left = df_left_vt["T"].abs().max() if not df_left_vt.empty else 0.0
                T2h = df_mid_vt["T"].abs().max() if not df_mid_vt.empty else 0.0
                Td_right = (
                    df_right_vt["T"].abs().max() if not df_right_vt.empty else 0.0
                )

                # Top Row
                top_row = prop_row.to_dict()
                top_row["Combo"] = combo
                top_row["Face"] = "TOP"
                top_row["ClearSpan_Ln"] = span_length
                top_row["Mu_left"] = Mneg_left
                top_row["Mu_mid"] = Mneg_mid
                top_row["Mu_right"] = Mneg_right
                top_row["Vu_left"] = Vd_left
                top_row["Vu_mid_2h"] = V2h
                top_row["Vu_right"] = Vd_right
                top_row["Tu_left"] = Td_left
                top_row["Tu_mid_2h"] = T2h
                top_row["Tu_right"] = Td_right
                design_rows.append(top_row)

                # Bottom Row
                bot_row = prop_row.to_dict()
                bot_row["Combo"] = combo
                bot_row["Face"] = "BOTTOM"
                bot_row["ClearSpan_Ln"] = span_length
                bot_row["Mu_left"] = Mpos_left
                bot_row["Mu_mid"] = Mpos_mid
                bot_row["Mu_right"] = Mpos_right
                bot_row["Vu_left"] = Vd_left
                bot_row["Vu_mid_2h"] = V2h
                bot_row["Vu_right"] = Vd_right
                bot_row["Tu_left"] = Td_left
                bot_row["Tu_mid_2h"] = T2h
                bot_row["Tu_right"] = Td_right
                design_rows.append(bot_row)

    df_beam_design_demands = pd.DataFrame(design_rows)
    if df_beam_design_demands.empty:
        return pd.DataFrame()

    # =============================================================================
    # 3. CONVERGED MULTI-COMBO REINFORCEMENT & POST-CHECK SOLVER
    # =============================================================================
    results_list = []

    for unique_name, df_b in df_beam_design_demands.groupby("UniqueName"):
        b_row = df_b.iloc[0]
        b_width = b_row.get("Width", b_row.get("b", 300))
        b_height = b_row.get("Depth", b_row.get("Height", 500))
        fc_val = b_row.get("f'c", 28)
        fy_val = b_row.get("fy", 413.69)
        fyt_val = b_row.get("fyw", b_row.get("fyt", 414))
        d_m = b_row.get("dm", b_row.get("d_main", 12))
        d_s = b_row.get("ds", b_row.get("d_stirrup", 12))
        d_side = b_row.get("dw", b_row.get("d_side", d_m))
        c_cover = b_row.get("cc", 40)
        span_ln = b_row.get("ClearSpan_Ln", 6000)

        df_grav = df_b[df_b["Combo"] == gravity_combo_name]
        Vu_grav_left = df_grav["Vu_left"].max() if not df_grav.empty else 0.0
        Vu_grav_right = df_grav["Vu_right"].max() if not df_grav.empty else 0.0

        prev_state = None

        while True:
            # --- STEP 3A: Flexure Design ---
            flex_eng_left = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )
            flex_eng_mid = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )
            flex_eng_right = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )

            flex_eng_left.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_left"].max()
            flex_eng_left.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_left"].max()

            flex_eng_mid.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_mid"].max()
            flex_eng_mid.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_mid"].max()

            flex_eng_right.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_right"].max()
            flex_eng_right.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_right"].max()

            max_Tu_left, max_Tu_mid, max_Tu_right = (
                df_b["Tu_left"].max(),
                df_b["Tu_mid_2h"].max(),
                df_b["Tu_right"].max(),
            )
            max_Vu_left, max_Vu_mid, max_Vu_right = (
                df_b["Vu_left"].max(),
                df_b["Vu_mid_2h"].max(),
                df_b["Vu_right"].max(),
            )

            d_eff_guess = b_height - c_cover - d_s - (d_m / 2.0)
            torsion_eng_left = BeamTorsionDesign(
                b_width, b_height, d_eff_guess, fc_val, fy_val, fyt_val, d_s, c_cover
            )
            torsion_eng_mid = BeamTorsionDesign(
                b_width, b_height, d_eff_guess, fc_val, fy_val, fyt_val, d_s, c_cover
            )
            torsion_eng_right = BeamTorsionDesign(
                b_width, b_height, d_eff_guess, fc_val, fy_val, fyt_val, d_s, c_cover
            )

            t_res_left = torsion_eng_left.solve_torsion_capacity(
                max_Tu_left, max_Vu_left
            )
            t_res_mid = torsion_eng_mid.solve_torsion_capacity(max_Tu_mid, max_Vu_mid)
            t_res_right = torsion_eng_right.solve_torsion_capacity(
                max_Tu_right, max_Vu_right
            )

            side_dist_left = distribute_longitudinal_torsion_and_skin(
                t_res_left["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )
            side_dist_mid = distribute_longitudinal_torsion_and_skin(
                t_res_mid["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )
            side_dist_right = distribute_longitudinal_torsion_and_skin(
                t_res_right["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )

            flex_eng_left.design_beam(
                side_dist_left["Al_top_req"], side_dist_left["Al_bot_req"]
            )
            flex_eng_mid.design_beam(
                side_dist_mid["Al_top_req"], side_dist_mid["Al_bot_req"]
            )
            flex_eng_right.design_beam(
                side_dist_right["Al_top_req"], side_dist_right["Al_bot_req"]
            )

            is_cantilever = "Cantilever" in str(b_row.get("SupportStatus", ""))
            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top, flex_eng_mid.n_top, flex_eng_right.n_top
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot, flex_eng_mid.n_bot, flex_eng_right.n_bot
                )
                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

            flex_engines = {
                "Left Support Face": flex_eng_left,
                "Midspan Zone": flex_eng_mid,
                "Right Support Face": flex_eng_right,
            }
            side_distributions = {
                "Left Support (d_eff)": side_dist_left,
                "Interior Web (Gov. 2h)": side_dist_mid,
                "Right Support (d_eff)": side_dist_right,
            }

            # --- STEP 3B: Seismic & Shear Design ---
            seismic_checker = BeamSeismicDesign(
                flex_engines, span_ln, Pu_axial_load, enable_seismic_design
            )

            if is_cantilever:
                supported_eng = (
                    flex_eng_right
                    if "Pt1" in str(b_row.get("SupportStatus", ""))
                    else flex_eng_left
                )
                Mpr_sup = seismic_checker.compute_probable_moment(
                    eng=supported_eng, is_negative_moment=True
                )
                V_sway_max = Mpr_sup / (span_ln / 1000.0) if span_ln > 0 else 0.0
                seismic_res = {
                    "V_sway_max": V_sway_max,
                    "Vu_seismic_left": Vu_grav_left + V_sway_max,
                    "Vu_seismic_right": Vu_grav_right + V_sway_max,
                    "Vc_zero_left": True,
                    "Vc_zero_right": True,
                }
            else:
                seismic_res = seismic_checker.evaluate_seismic_shear_demands(
                    Vu_grav_left,
                    Vu_grav_right,
                    side_distributions,
                    Vu_envelope_max_left=max_Vu_left,
                    Vu_envelope_max_right=max_Vu_right,
                )

            gov_legs_left, gov_legs_mid, gov_legs_right = 2, 2, 2
            min_s_left, min_s_mid, min_s_right = 600, 600, 600

            for combo_name in df_b["Combo"].unique():
                df_c_top = df_b[
                    (df_b["Combo"] == combo_name) & (df_b["Face"] == "TOP")
                ].iloc[0]

                Vu_L = (
                    max(df_c_top["Vu_left"], seismic_res["Vu_seismic_left"])
                    if enable_seismic_design
                    else df_c_top["Vu_left"]
                )
                Vu_M = df_c_top["Vu_mid_2h"]
                Vu_R = (
                    max(df_c_top["Vu_right"], seismic_res["Vu_seismic_right"])
                    if enable_seismic_design
                    else df_c_top["Vu_right"]
                )

                for zone_name, Vu_val, Tu_val, leg_var in [
                    ("Left Support (d_eff)", Vu_L, df_c_top["Tu_left"], "L"),
                    ("Interior Web (Gov. 2h)", Vu_M, df_c_top["Tu_mid_2h"], "M"),
                    ("Right Support (d_eff)", Vu_R, df_c_top["Tu_right"], "R"),
                ]:
                    d_eff_z = get_location_effective_depth(zone_name, flex_engines)
                    n_legs = 2
                    while True:
                        shear = BeamShearDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fyt_val,
                            d_s,
                            n_legs,
                            suppress_Vc=(
                                seismic_res["Vc_zero_left"]
                                if "Left" in zone_name
                                else (
                                    seismic_res["Vc_zero_right"]
                                    if "Right" in zone_name
                                    else False
                                )
                            ),
                        )
                        torsion = BeamTorsionDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fy_val,
                            fyt_val,
                            d_s,
                            c_cover,
                        )

                        s_r = shear.solve_shear_capacity(Vu_val)
                        t_r = torsion.solve_torsion_capacity(Tu_val, Vu_val)

                        des_ratio = max(
                            s_r["Av_s_demand"] + 2 * t_r["At_s_demand"],
                            t_r["Av_2At_s_min"],
                        )
                        s_max = min(s_r["s_max_code"], t_r["s_max_torsion"])

                        # SEISMIC SPACING OVERRIDE
                        if enable_seismic_design and leg_var in ["L", "R"]:
                            s_max_seismic = min(d_eff_z / 4.0, 6.0 * d_m, 150.0)
                            s_max = min(s_max, s_max_seismic)

                        Av_prov = n_legs * ((math.pi / 4) * d_s**2)
                        s_raw = (
                            min(Av_prov / des_ratio, s_max) if des_ratio > 0 else s_max
                        )
                        s_rec = int((s_raw // 25) * 25)

                        if s_rec < 100 and n_legs < flex_eng_mid.max_bar_per_layer:
                            n_legs += 1
                        else:
                            break

                    if leg_var == "L":
                        gov_legs_left = max(gov_legs_left, n_legs)
                        min_s_left = min(min_s_left, s_rec)
                    elif leg_var == "M":
                        gov_legs_mid = max(gov_legs_mid, n_legs)
                        min_s_mid = min(min_s_mid, s_rec)
                    elif leg_var == "R":
                        gov_legs_right = max(gov_legs_right, n_legs)
                        min_s_right = min(min_s_right, s_rec)

            governing_beam_legs = max(gov_legs_left, gov_legs_mid, gov_legs_right)
            s_2h = min(min_s_left, min_s_right)
            s_mid = min_s_mid

            # --- STEP 3C: Post-Checks ---
            for eng in flex_engines.values():
                alt_legs = check_alternating_tie_legs(
                    eng.n_top, eng.n_bot, governing_beam_legs, eng.max_bar_per_layer
                )
                governing_beam_legs = max(governing_beam_legs, alt_legs)

            anchorage_passed_all = True
            for loc_name, eng in flex_engines.items():
                anch_check = check_stirrup_leg_anchorage(
                    eng.n_top,
                    eng.n_bot,
                    governing_beam_legs,
                    eng.max_bar_per_layer,
                    d_m,
                    d_s,
                    b_width,
                    c_cover,
                )
                if not anch_check["anchorage_passed"]:
                    eng.n_top = anch_check["final_n_top"]
                    eng.n_bot = anch_check["final_n_bot"]
                    anchorage_passed_all = False

            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top, flex_eng_mid.n_top, flex_eng_right.n_top
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot, flex_eng_mid.n_bot, flex_eng_right.n_bot
                )
                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

            current_state = (
                [(eng.n_top, eng.n_bot) for eng in flex_engines.values()],
                governing_beam_legs,
            )
            if current_state == prev_state:
                break
            else:
                prev_state = current_state

        # =============================================================================
        # 4. CONSOLIDATE RESULTS (TOP & BOTTOM)
        # =============================================================================
        for is_top_face in [True, False]:
            face_str = "TOP" if is_top_face else "BOTTOM"
            summary = b_row.to_dict()
            summary["Combo"] = "ENVELOPE (ALL COMBOS)"
            summary["Face"] = face_str
            summary["Mu_left"] = df_b[df_b["Face"] == face_str]["Mu_left"].max()
            summary["Mu_mid"] = df_b[df_b["Face"] == face_str]["Mu_mid"].max()
            summary["Mu_right"] = df_b[df_b["Face"] == face_str]["Mu_right"].max()

            # Shear/Torsion envelope is identical for top and bottom rows
            summary["Vu_left"] = df_b["Vu_left"].max()
            summary["Vu_mid_2h"] = df_b["Vu_mid_2h"].max()
            summary["Vu_right"] = df_b["Vu_right"].max()
            summary["Tu_left"] = df_b["Tu_left"].max()
            summary["Tu_mid_2h"] = df_b["Tu_mid_2h"].max()
            summary["Tu_right"] = df_b["Tu_right"].max()

            summary["n_left_L1"], summary["n_left_L2"], summary["n_left_L3"] = (
                get_layer_columns(flex_eng_left, is_top=is_top_face)
            )
            summary["n_mid_L1"], summary["n_mid_L2"], summary["n_mid_L3"] = (
                get_layer_columns(flex_eng_mid, is_top=is_top_face)
            )
            summary["n_right_L1"], summary["n_right_L2"], summary["n_right_L3"] = (
                get_layer_columns(flex_eng_right, is_top=is_top_face)
            )

            # Web Reinforcement
            summary["n_side_per_face_gov"] = max(
                side_dist_left["n_side_per_face"],
                side_dist_mid["n_side_per_face"],
                side_dist_right["n_side_per_face"],
            )

            # Stirrups & Spacing
            summary["Stirrup_Legs"] = governing_beam_legs
            summary["Spacing_2H"] = s_2h
            summary["Spacing_Mid"] = s_mid

            # Post-Checks
            summary["V_sway_max_kN"] = seismic_res["V_sway_max"]
            summary["Vc_zero_left"] = seismic_res["Vc_zero_left"]
            summary["Vc_zero_right"] = seismic_res["Vc_zero_right"]
            summary["Anchorage_Check"] = (
                "PASSED" if anchorage_passed_all else "ADJUSTED"
            )
            summary["Alternating_Tie_Check"] = "PASSED"

            results_list.append(summary)

    df_beam_design_results = pd.DataFrame(results_list).round(2)
    return df_beam_design_results


# =============================================================================
# MAIN EXECUTION PIPELINE FOR FULL LENGTH BEAM
# =============================================================================
if __name__ == "__main__":
    import math
    from typing import Dict, List
    import pandas as pd

    # --- HELPER FUNCTION FOR PHYSICAL ELEVATION LAYERS ---
    def get_layer_columns(engine, is_top: bool) -> tuple:
        """Returns (L1, L2, L3) physical elevation counts (Topmost to Bottommost)."""
        n_bars = engine.n_top if is_top else engine.n_bot
        layers = engine.calculate_layer_distribution(n_bars)

        if is_top:
            l1 = layers[0] if len(layers) > 0 else 0
            l2 = layers[1] if len(layers) > 1 else 0
            l3 = layers[2] if len(layers) > 2 else 0
            return l1, l2, l3
        else:
            l3 = layers[0] if len(layers) > 0 else 0
            l2 = layers[1] if len(layers) > 1 else 0
            l1 = layers[2] if len(layers) > 2 else 0
            return l1, l2, l3

    # =============================================================================
    # 1. INPUT DATAFRAMES (BEAM PROPS & FRAME FORCES)
    # =============================================================================
    data_beam_props = {
        "Story": ["2F", "2F"],
        "UniqueName": ["CANTILEVER_1", "2GY-1"],
        "SectProp": ["G_300X500_C05_G60", "G_500X600_C05_G60"],
        "SupportStatus": ["Cantilever (Free at Pt1)", "Supported Both Ends"],
        "f'c": [34.48, 34.48],
        "Width": [300, 500],
        "Depth": [500, 600],
        "Diameter": [None, None],
        "DesignType": ["Beam", "Beam"],
        "fy": [413.69, 413.69],
        "fys": [413.69, 413.69],
        "fyw": [414, 414],
        "dm": [12, 12],
        "ds": [12, 12],
        "dw": [12, 12],
        "cc": [40, 40],
    }
    df_beam_props = pd.DataFrame(data_beam_props)

    stations_b9 = [0, 440, 880, 1320, 1760, 2200] * 3
    stations_b14 = [
        300,
        762.5,
        1225,
        1687.5,
        2150,
        2612.5,
        3075,
        3537.5,
        4000,
        4000,
        4462.5,
        4925,
        5387.5,
        5850,
        6312.5,
        6775,
        7237.5,
        7700,
    ] * 2

    data_frame_forces = {
        "Story": ["2F"] * 54,
        "Label": ["B9"] * 18 + ["B14"] * 36,
        "UniqueName": ["CANTILEVER_1"] * 18 + ["2GY-1"] * 36,
        "Combo": (
            ["TGL"] * 6
            + ["ULS 101 - 1.2 DL + 1.6 LL + 0.5 Lr"] * 12
            + ["TGL"] * 18
            + ["ULS 101 - 1.2 DL + 1.6 LL + 0.5 Lr"] * 18
        ),
        "Station": stations_b9 + stations_b14,
        "P": [-0.97339] * 6
        + [-1.27601] * 12
        + [-10.22803] * 9
        + [-8.82364] * 9
        + [-13.24588] * 9
        + [-11.52197] * 9,
        "V2": [
            52.89788,
            54.45305,
            56.00822,
            57.56338,
            59.11855,
            60.67371,
            71.32525,
            73.19145,
            75.05765,
            76.92385,
            78.79005,
            80.65625,
            39.93409,
            41.80029,
            43.66649,
            45.53269,
            47.39889,
            49.26509,
            -186.02045,
            -174.05575,
            -162.09105,
            -150.12635,
            -138.16165,
            -126.19696,
            -114.23226,
            -102.26756,
            -90.30286,
            97.64827,
            109.61296,
            121.57766,
            133.54236,
            145.50706,
            157.47176,
            169.43646,
            181.40115,
            193.36585,
            -243.0205,
            -228.13006,
            -213.23962,
            -198.34919,
            -183.45875,
            -168.56831,
            -153.67787,
            -138.78744,
            -123.897,
            133.2111,
            148.10153,
            162.99197,
            177.88241,
            192.77285,
            207.66329,
            222.55372,
            237.44416,
            252.3346,
        ],
        "V3": [-0.02756] * 6
        + [-0.03603] * 12
        + [-0.09878] * 9
        + [0.09278] * 9
        + [-0.12813] * 9
        + [0.12511] * 9,
        "T": [1.42122177] * 6
        + [1.91314566] * 12
        + [3.50327356] * 9
        + [-3.49075299] * 9
        + [4.81362807] * 9
        + [-4.78547112] * 9,
        "M2": [
            -0.00941428,
            0.00271032,
            0.01483493,
            0.02695953,
            0.03908414,
            0.05120874,
            -0.0125101,
            0.00334114,
            0.01919238,
            0.03504361,
            0.05089485,
            0.06674609,
            -0.0125101,
            0.00334114,
            0.01919238,
            0.03504361,
            0.05089485,
            0.06674609,
            -0.16387893,
            -0.11819503,
            -0.07251113,
            -0.02682723,
            0.01885667,
            0.06454057,
            0.11022447,
            0.15590837,
            0.20159227,
            0.16079767,
            0.11788478,
            0.07497188,
            0.03205898,
            -0.01085392,
            -0.05376681,
            -0.09667971,
            -0.13959261,
            -0.18250551,
            -0.20974186,
            -0.15048315,
            -0.09122443,
            -0.03196571,
            0.02729301,
            0.08655173,
            0.14581045,
            0.20506916,
            0.26432788,
            0.21475453,
            0.15689073,
            0.09902693,
            0.04116313,
            -0.01670068,
            -0.07456448,
            -0.13242828,
            -0.19029209,
            -0.24815589,
        ],
        "M3": [
            -0.37287454,
            -23.99008039,
            -48.29155929,
            -73.27731124,
            -98.94733624,
            -125.301634,
            -0.50474931,
            -32.29842402,
            -64.91322639,
            -98.34915643,
            -132.606214,
            -167.684399,
            -0.27554986,
            -18.2571138,
            -37.05980541,
            -56.68362468,
            -77.12857161,
            -98.3946462,
            -259.492687,
            -176.225067,
            -98.49112048,
            -26.29084663,
            40.37575437,
            101.5086825,
            157.1079378,
            207.1735203,
            251.7054298,
            251.7114025,
            203.7822428,
            150.3194102,
            91.3229047,
            26.79272639,
            -43.27112478,
            -118.868649,
            -199.999846,
            -286.664715,
            -342.873723,
            -233.920155,
            -131.853415,
            -36.67350245,
            51.69195826,
            133.0258407,
            207.5452712,
            275.1778742,
            335.9236499,
            335.9311582,
            270.877612,
            198.9372383,
            120.1100373,
            34.39600881,
            -58.20484708,
            -157.69253,
            -264.067041,
            -377.328379,
        ],
    }
    df_frame_forces = pd.DataFrame(data_frame_forces)

    # Global Parameters
    enable_seismic_design = True
    Pu_axial_load = 50.0  # kN
    gravity_combo_name = "TGL"

    # =============================================================================
    # 2. BUILD EXTRACTION DEMANDS DATAFRAME PER COMBO
    # =============================================================================
    design_rows = []

    for _, prop_row in df_beam_props.iterrows():
        u_name = prop_row["UniqueName"]
        h = prop_row["Depth"]
        two_h = 2.0 * h

        df_forces_beam = df_frame_forces[df_frame_forces["UniqueName"] == u_name]

        if not df_forces_beam.empty:
            min_st = df_forces_beam["Station"].min()
            max_st = df_forces_beam["Station"].max()
            span_length = max_st - min_st

            m_left_boundary = min_st + 0.25 * span_length
            m_right_boundary = max_st - 0.25 * span_length

            v_left_boundary = min_st + two_h
            v_right_boundary = max_st - two_h

            combos = df_forces_beam["Combo"].unique()

            for combo in combos:
                df_combo = df_forces_beam[df_forces_beam["Combo"] == combo]

                df_left_m = df_combo[df_combo["Station"] <= m_left_boundary]
                df_mid_m = df_combo[
                    (df_combo["Station"] > m_left_boundary)
                    & (df_combo["Station"] < m_right_boundary)
                ]
                df_right_m = df_combo[df_combo["Station"] >= m_right_boundary]

                df_left_vt = df_combo[df_combo["Station"] <= v_left_boundary]
                df_mid_vt = df_combo[
                    (df_combo["Station"] >= v_left_boundary)
                    & (df_combo["Station"] <= v_right_boundary)
                ]
                df_right_vt = df_combo[df_combo["Station"] >= v_right_boundary]

                Mneg_left = (
                    abs(min(0.0, df_left_m["M3"].min())) if not df_left_m.empty else 0.0
                )
                Mpos_left = (
                    max(0.0, df_left_m["M3"].max()) if not df_left_m.empty else 0.0
                )

                Mneg_mid = (
                    abs(min(0.0, df_mid_m["M3"].min())) if not df_mid_m.empty else 0.0
                )
                Mpos_mid = max(0.0, df_mid_m["M3"].max()) if not df_mid_m.empty else 0.0

                Mneg_right = (
                    abs(min(0.0, df_right_m["M3"].min()))
                    if not df_right_m.empty
                    else 0.0
                )
                Mpos_right = (
                    max(0.0, df_right_m["M3"].max()) if not df_right_m.empty else 0.0
                )

                Vd_left = df_left_vt["V2"].abs().max() if not df_left_vt.empty else 0.0
                V2h = df_mid_vt["V2"].abs().max() if not df_mid_vt.empty else 0.0
                Vd_right = (
                    df_right_vt["V2"].abs().max() if not df_right_vt.empty else 0.0
                )

                Td_left = df_left_vt["T"].abs().max() if not df_left_vt.empty else 0.0
                T2h = df_mid_vt["T"].abs().max() if not df_mid_vt.empty else 0.0
                Td_right = (
                    df_right_vt["T"].abs().max() if not df_right_vt.empty else 0.0
                )

                # Top Row
                top_row = prop_row.to_dict()
                top_row["Combo"] = combo
                top_row["Face"] = "TOP"
                top_row["ClearSpan_Ln"] = span_length
                top_row["Mu_left"] = Mneg_left
                top_row["Mu_mid"] = Mneg_mid
                top_row["Mu_right"] = Mneg_right
                top_row["Vu_left"] = Vd_left
                top_row["Vu_mid_2h"] = V2h
                top_row["Vu_right"] = Vd_right
                top_row["Tu_left"] = Td_left
                top_row["Tu_mid_2h"] = T2h
                top_row["Tu_right"] = Td_right
                design_rows.append(top_row)

                # Bottom Row
                bot_row = prop_row.to_dict()
                bot_row["Combo"] = combo
                bot_row["Face"] = "BOTTOM"
                bot_row["ClearSpan_Ln"] = span_length
                bot_row["Mu_left"] = Mpos_left
                bot_row["Mu_mid"] = Mpos_mid
                bot_row["Mu_right"] = Mpos_right
                bot_row["Vu_left"] = Vd_left
                bot_row["Vu_mid_2h"] = V2h
                bot_row["Vu_right"] = Vd_right
                bot_row["Tu_left"] = Td_left
                bot_row["Tu_mid_2h"] = T2h
                bot_row["Tu_right"] = Td_right
                design_rows.append(bot_row)

    # Create demands DataFrame from populated design rows
    df_beam_design_demands = pd.DataFrame(design_rows)

    # =============================================================================
    # 3. CONVERGED MULTI-COMBO REINFORCEMENT & POST-CHECK SOLVER
    # =============================================================================
    results_list = []

    for unique_name, df_b in df_beam_design_demands.groupby("UniqueName"):

        # Extract member geometric & material properties using key fallbacks
        b_row = df_b.iloc[0]
        b_width = b_row.get("Width", b_row.get("b", 300))
        b_height = b_row.get("Depth", b_row.get("Height", 500))
        fc_val = b_row.get("f'c", 28)
        fy_val = b_row.get("fy", 413.69)
        fyt_val = b_row.get("fyw", b_row.get("fyt", 414))
        d_m = b_row.get("dm", b_row.get("d_main", 12))
        d_s = b_row.get("ds", b_row.get("d_stirrup", 12))
        d_side = b_row.get("dw", b_row.get("d_side", d_m))
        c_cover = b_row.get("cc", 40)
        span_ln = b_row.get("ClearSpan_Ln", 6000)

        Vu_grav_left = df_b[df_b["Combo"] == gravity_combo_name]["Vu_left"].max()
        Vu_grav_right = df_b[df_b["Combo"] == gravity_combo_name]["Vu_right"].max()

        prev_state = None

        while True:
            # --- STEP 1: Multi-Combo Flexure Design & Max Bar Count Extraction ---
            flex_eng_left = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )
            flex_eng_mid = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )
            flex_eng_right = BeamFlexureDesign(
                b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover
            )

            # Assign maximum moments across envelope
            flex_eng_left.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_left"].max()
            flex_eng_left.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_left"].max()

            flex_eng_mid.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_mid"].max()
            flex_eng_mid.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_mid"].max()

            flex_eng_right.Mu_neg = df_b[df_b["Face"] == "TOP"]["Mu_right"].max()
            flex_eng_right.Mu_pos = df_b[df_b["Face"] == "BOTTOM"]["Mu_right"].max()

            # Compute Torsion Side Face Steel
            max_Tu_left, max_Tu_mid, max_Tu_right = (
                df_b["Tu_left"].max(),
                df_b["Tu_mid_2h"].max(),
                df_b["Tu_right"].max(),
            )
            max_Vu_left, max_Vu_mid, max_Vu_right = (
                df_b["Vu_left"].max(),
                df_b["Vu_mid_2h"].max(),
                df_b["Vu_right"].max(),
            )

            d_eff_guess = b_height - c_cover - d_s - (d_m / 2.0)
            torsion_eng_left = BeamTorsionDesign(
                b_width,
                b_height,
                d_eff_guess,
                fc_val,
                fy_val,
                fyt_val,
                d_s,
                c_cover,
            )
            torsion_eng_mid = BeamTorsionDesign(
                b_width,
                b_height,
                d_eff_guess,
                fc_val,
                fy_val,
                fyt_val,
                d_s,
                c_cover,
            )
            torsion_eng_right = BeamTorsionDesign(
                b_width,
                b_height,
                d_eff_guess,
                fc_val,
                fy_val,
                fyt_val,
                d_s,
                c_cover,
            )

            t_res_left = torsion_eng_left.solve_torsion_capacity(
                max_Tu_left, max_Vu_left
            )
            t_res_mid = torsion_eng_mid.solve_torsion_capacity(max_Tu_mid, max_Vu_mid)
            t_res_right = torsion_eng_right.solve_torsion_capacity(
                max_Tu_right, max_Vu_right
            )

            side_dist_left = distribute_longitudinal_torsion_and_skin(
                t_res_left["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )
            side_dist_mid = distribute_longitudinal_torsion_and_skin(
                t_res_mid["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )
            side_dist_right = distribute_longitudinal_torsion_and_skin(
                t_res_right["Al_design"],
                d_m,
                d_side,
                b_height,
                b_width,
                d_eff_guess,
                fy_val,
                c_cover,
                d_s,
            )

            flex_eng_left.design_beam(
                side_dist_left["Al_top_req"], side_dist_left["Al_bot_req"]
            )
            flex_eng_mid.design_beam(
                side_dist_mid["Al_top_req"], side_dist_mid["Al_bot_req"]
            )
            flex_eng_right.design_beam(
                side_dist_right["Al_top_req"], side_dist_right["Al_bot_req"]
            )

            # Enforce uniform Longitudinal Rebar across all 3 stations for Cantilevers
            is_cantilever = "Cantilever" in b_row.get("SupportStatus", "")
            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top,
                    flex_eng_mid.n_top,
                    flex_eng_right.n_top,
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot,
                    flex_eng_mid.n_bot,
                    flex_eng_right.n_bot,
                )

                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

            flex_engines = {
                "Left Support Face": flex_eng_left,
                "Midspan Zone": flex_eng_mid,
                "Right Support Face": flex_eng_right,
            }

            side_distributions = {
                "Left Support (d_eff)": side_dist_left,
                "Interior Web (Gov. 2h)": side_dist_mid,
                "Right Support (d_eff)": side_dist_right,
            }

            # --- STEP 2: Seismic V_sway & Multi-Combo Shear/Torsion Design ---
            seismic_checker = BeamSeismicDesign(
                flex_engines, span_ln, Pu_axial_load, enable_seismic_design
            )

            if is_cantilever:
                # Positional argument pass to resolve Mpr call
                supported_eng = (
                    flex_eng_right
                    if "Pt1" in b_row.get("SupportStatus", "")
                    else flex_eng_left
                )

                # FIXED: Call compute_probable_moment via the seismic_checker instance
                # It returns a float directly, so no need for ["Mpr"] indexing
                Mpr_sup = seismic_checker.compute_probable_moment(
                    eng=supported_eng, is_negative_moment=True
                )

                V_sway_max = Mpr_sup / (span_ln / 1000.0) if span_ln > 0 else 0.0

                seismic_res = {
                    "V_sway_max": V_sway_max,
                    "Vu_seismic_left": Vu_grav_left + V_sway_max,
                    "Vu_seismic_right": Vu_grav_right + V_sway_max,
                    "Vc_zero_left": True,
                    "Vc_zero_right": True,
                }
            else:
                seismic_res = seismic_checker.evaluate_seismic_shear_demands(
                    Vu_grav_left,
                    Vu_grav_right,
                    side_distributions,
                    Vu_envelope_max_left=max_Vu_left,
                    Vu_envelope_max_right=max_Vu_right,
                )

            gov_legs_left, gov_legs_mid, gov_legs_right = 2, 2, 2

            for combo_name in df_b["Combo"].unique():
                df_c_top = df_b[
                    (df_b["Combo"] == combo_name) & (df_b["Face"] == "TOP")
                ].iloc[0]

                Vu_L = (
                    max(df_c_top["Vu_left"], seismic_res["Vu_seismic_left"])
                    if enable_seismic_design
                    else df_c_top["Vu_left"]
                )
                Vu_M = df_c_top["Vu_mid_2h"]
                Vu_R = (
                    max(df_c_top["Vu_right"], seismic_res["Vu_seismic_right"])
                    if enable_seismic_design
                    else df_c_top["Vu_right"]
                )

                for zone_name, Vu_val, Tu_val, leg_var in [
                    ("Left Support (d_eff)", Vu_L, df_c_top["Tu_left"], "L"),
                    ("Interior Web (Gov. 2h)", Vu_M, df_c_top["Tu_mid_2h"], "M"),
                    ("Right Support (d_eff)", Vu_R, df_c_top["Tu_right"], "R"),
                ]:
                    d_eff_z = get_location_effective_depth(zone_name, flex_engines)
                    n_legs = 2
                    while True:
                        shear = BeamShearDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fyt_val,
                            d_s,
                            n_legs,
                            suppress_Vc=(
                                seismic_res["Vc_zero_left"]
                                if "Left" in zone_name
                                else (
                                    seismic_res["Vc_zero_right"]
                                    if "Right" in zone_name
                                    else False
                                )
                            ),
                        )
                        torsion = BeamTorsionDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fy_val,
                            fyt_val,
                            d_s,
                            c_cover,
                        )
                        s_r = shear.solve_shear_capacity(Vu_val)
                        t_r = torsion.solve_torsion_capacity(Tu_val, Vu_val)

                        des_ratio = max(
                            s_r["Av_s_demand"] + 2 * t_r["At_s_demand"],
                            t_r["Av_2At_s_min"],
                        )
                        s_max = min(s_r["s_max_code"], t_r["s_max_torsion"])
                        Av_prov = n_legs * ((math.pi / 4) * d_s**2)
                        s_raw = (
                            min(Av_prov / des_ratio, s_max) if des_ratio > 0 else s_max
                        )
                        s_rec = int((s_raw // 25) * 25)

                        if s_rec < 100 and n_legs < flex_eng_mid.max_bar_per_layer:
                            n_legs += 1
                        else:
                            break

                    if leg_var == "L":
                        gov_legs_left = max(gov_legs_left, n_legs)
                    elif leg_var == "M":
                        gov_legs_mid = max(gov_legs_mid, n_legs)
                    elif leg_var == "R":
                        gov_legs_right = max(gov_legs_right, n_legs)

            governing_beam_legs = max(gov_legs_left, gov_legs_mid, gov_legs_right)

            # --- STEP 3: Post-Checks (Alternating Ties & Anchorage) ---
            for eng in flex_engines.values():
                alt_legs = check_alternating_tie_legs(
                    eng.n_top,
                    eng.n_bot,
                    governing_beam_legs,
                    eng.max_bar_per_layer,
                )
                governing_beam_legs = max(governing_beam_legs, alt_legs)

            anchorage_passed_all = True
            for loc_name, eng in flex_engines.items():
                anch_check = check_stirrup_leg_anchorage(
                    eng.n_top,
                    eng.n_bot,
                    governing_beam_legs,
                    eng.max_bar_per_layer,
                    d_m,
                    d_s,
                    b_width,
                    c_cover,
                )
                if not anch_check["anchorage_passed"]:
                    eng.n_top = anch_check["final_n_top"]
                    eng.n_bot = anch_check["final_n_bot"]
                    anchorage_passed_all = False

            # Re-enforce uniform rebar for cantilevers after post-checks
            if is_cantilever:
                cant_n_top = max(
                    flex_eng_left.n_top,
                    flex_eng_mid.n_top,
                    flex_eng_right.n_top,
                )
                cant_n_bot = max(
                    flex_eng_left.n_bot,
                    flex_eng_mid.n_bot,
                    flex_eng_right.n_bot,
                )
                flex_eng_left.n_top = flex_eng_mid.n_top = flex_eng_right.n_top = (
                    cant_n_top
                )
                flex_eng_left.n_bot = flex_eng_mid.n_bot = flex_eng_right.n_bot = (
                    cant_n_bot
                )

            current_state = (
                [(eng.n_top, eng.n_bot) for eng in flex_engines.values()],
                governing_beam_legs,
            )
            if current_state == prev_state:
                break
            else:
                prev_state = current_state

        # --- STEP 4: Populate Single Governing Summary Row per Beam ---
        top_L1_left, top_L2_left, top_L3_left = get_layer_columns(
            flex_eng_left, is_top=True
        )
        top_L1_mid, top_L2_mid, top_L3_mid = get_layer_columns(
            flex_eng_mid, is_top=True
        )
        top_L1_right, top_L2_right, top_L3_right = get_layer_columns(
            flex_eng_right, is_top=True
        )

        bot_L1_left, bot_L2_left, bot_L3_left = get_layer_columns(
            flex_eng_left, is_top=False
        )
        bot_L1_mid, bot_L2_mid, bot_L3_mid = get_layer_columns(
            flex_eng_mid, is_top=False
        )
        bot_L1_right, bot_L2_right, bot_L3_right = get_layer_columns(
            flex_eng_right, is_top=False
        )

        # TOP FACE GOVERNING ROW
        top_summary = b_row.to_dict()
        top_summary["Combo"] = "ENVELOPE (ALL COMBOS)"
        top_summary["Face"] = "TOP"
        top_summary["Mu_left"] = df_b[df_b["Face"] == "TOP"]["Mu_left"].max()
        top_summary["Mu_mid"] = df_b[df_b["Face"] == "TOP"]["Mu_mid"].max()
        top_summary["Mu_right"] = df_b[df_b["Face"] == "TOP"]["Mu_right"].max()
        top_summary["Vu_left"] = df_b["Vu_left"].max()
        top_summary["Vu_mid_2h"] = df_b["Vu_mid_2h"].max()
        top_summary["Vu_right"] = df_b["Vu_right"].max()
        top_summary["Tu_left"] = df_b["Tu_left"].max()
        top_summary["Tu_mid_2h"] = df_b["Tu_mid_2h"].max()
        top_summary["Tu_right"] = df_b["Tu_right"].max()

        (
            top_summary["n_left_L1"],
            top_summary["n_left_L2"],
            top_summary["n_left_L3"],
        ) = (top_L1_left, top_L2_left, top_L3_left)
        top_summary["n_mid_L1"], top_summary["n_mid_L2"], top_summary["n_mid_L3"] = (
            top_L1_mid,
            top_L2_mid,
            top_L3_mid,
        )
        (
            top_summary["n_right_L1"],
            top_summary["n_right_L2"],
            top_summary["n_right_L3"],
        ) = (top_L1_right, top_L2_right, top_L3_right)

        top_summary["Stirrup_Legs"] = governing_beam_legs
        top_summary["V_sway_max_kN"] = seismic_res["V_sway_max"]
        top_summary["Vc_zero_left"] = seismic_res["Vc_zero_left"]
        top_summary["Vc_zero_right"] = seismic_res["Vc_zero_right"]
        top_summary["Anchorage_Check"] = (
            "PASSED" if anchorage_passed_all else "ADJUSTED"
        )
        top_summary["Alternating_Tie_Check"] = "PASSED"
        results_list.append(top_summary)

        # BOTTOM FACE GOVERNING ROW
        bot_summary = b_row.to_dict()
        bot_summary["Combo"] = "ENVELOPE (ALL COMBOS)"
        bot_summary["Face"] = "BOTTOM"
        bot_summary["Mu_left"] = df_b[df_b["Face"] == "BOTTOM"]["Mu_left"].max()
        bot_summary["Mu_mid"] = df_b[df_b["Face"] == "BOTTOM"]["Mu_mid"].max()
        bot_summary["Mu_right"] = df_b[df_b["Face"] == "BOTTOM"]["Mu_right"].max()
        bot_summary["Vu_left"] = df_b["Vu_left"].max()
        bot_summary["Vu_mid_2h"] = df_b["Vu_mid_2h"].max()
        bot_summary["Vu_right"] = df_b["Vu_right"].max()
        bot_summary["Tu_left"] = df_b["Tu_left"].max()
        bot_summary["Tu_mid_2h"] = df_b["Tu_mid_2h"].max()
        bot_summary["Tu_right"] = df_b["Tu_right"].max()

        (
            bot_summary["n_left_L1"],
            bot_summary["n_left_L2"],
            bot_summary["n_left_L3"],
        ) = (bot_L1_left, bot_L2_left, bot_L3_left)
        bot_summary["n_mid_L1"], bot_summary["n_mid_L2"], bot_summary["n_mid_L3"] = (
            bot_L1_mid,
            bot_L2_mid,
            bot_L3_mid,
        )
        (
            bot_summary["n_right_L1"],
            bot_summary["n_right_L2"],
            bot_summary["n_right_L3"],
        ) = (bot_L1_right, bot_L2_right, bot_L3_right)

        bot_summary["Stirrup_Legs"] = governing_beam_legs
        bot_summary["V_sway_max_kN"] = seismic_res["V_sway_max"]
        bot_summary["Vc_zero_left"] = seismic_res["Vc_zero_left"]
        bot_summary["Vc_zero_right"] = seismic_res["Vc_zero_right"]
        bot_summary["Anchorage_Check"] = (
            "PASSED" if anchorage_passed_all else "ADJUSTED"
        )
        bot_summary["Alternating_Tie_Check"] = "PASSED"
        results_list.append(bot_summary)

    # =============================================================================
    # 4. VIEW CONSOLIDATED RESULTS DATAFRAME
    # =============================================================================
    df_beam_design_results = pd.DataFrame(results_list).round(2)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)

    print("\nFINAL CONVERGED BEAM DESIGN RESULTS DATAFRAME:")
    print("=" * 120)
    print(
        df_beam_design_results[
            [
                "UniqueName",
                "Face",
                "n_left_L1",
                "n_left_L2",
                "n_left_L3",
                "n_mid_L1",
                "n_mid_L2",
                "n_mid_L3",
                "n_right_L1",
                "n_right_L2",
                "n_right_L3",
                "Stirrup_Legs",
                "V_sway_max_kN",
                "Anchorage_Check",
            ]
        ]
    )
