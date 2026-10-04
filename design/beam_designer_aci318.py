"""Beam design for ACI 318M-14: flexure, shear, torsion, SMRF, detailing and schedules.

The terminal workflow (``sdt beams``) is in ``design/concrete_workflow.py``;
this module designs from tables (``design_beams``) and writes the results
file, the DXF schedules and the calculation report.
"""

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List

import ezdxf
import pandas as pd

from design.code_config import CODE, AciCode
from design.beam_deflection import (
    DEFLECTION_COLUMNS,
    LIMIT_DAMAGED,
    LIMIT_NOT_DAMAGED,
    add_deflection_columns,
)
from utilities._calc_report import (
    is_blank,
    MemberReport,
    ReportTable,
    Tex,
    build_calc_report,
    number,
)


GRAVITY_BEAM_STATUS = "Beam-Framed / Floating"  # neither end is on a column or wall


def is_gravity_beam(support_status: object) -> bool:
    """True for a beam that frames into other beams only.

    A gravity beam is not part of the moment frame, so the seismic (SMRF)
    provisions are not applied to it even when seismic design is switched on.
    """
    return str(support_status).strip() == GRAVITY_BEAM_STATUS


def identify_cantilever_beams(
    frame_df: pd.DataFrame,
    conn_df: pd.DataFrame,
) -> pd.DataFrame:
    """Classify beam supports from column and wall connectivity.

    `frame_df` remains in the signature for compatibility with existing
    callers. Support joints are identified from `conn_df` only.
    """
    del frame_df

    conn = conn_df.copy()
    conn.columns = [str(column).strip() for column in conn.columns]

    required = {"DesignType", "UniqueName", "UniquePtI", "UniquePtJ"}
    missing = required.difference(conn.columns)
    if missing:
        raise ValueError(
            "Connectivity data is missing required columns: "
            + ", ".join(sorted(missing))
        )

    support_rows = conn.loc[conn["DesignType"].isin(["Column", "Wall"])]
    point_columns = [
        column
        for column in (
            "UniquePtI",
            "UniquePtJ",
            "UniquePt1",
            "UniquePt2",
            "UniquePt3",
            "UniquePt4",
        )
        if column in support_rows.columns
    ]
    support_joints = {
        point
        for point in support_rows[point_columns].to_numpy().ravel()
        if pd.notna(point)
    }

    beams = conn.loc[conn["DesignType"].eq("Beam")].copy()
    has_support_i = beams["UniquePtI"].isin(support_joints)
    has_support_j = beams["UniquePtJ"].isin(support_joints)

    beams["SupportStatus"] = GRAVITY_BEAM_STATUS
    beams.loc[has_support_i & has_support_j, "SupportStatus"] = "Supported Both Ends"
    beams.loc[has_support_i & ~has_support_j, "SupportStatus"] = (
        "Cantilever (Free at PtJ)"
    )
    beams.loc[~has_support_i & has_support_j, "SupportStatus"] = (
        "Cantilever (Free at PtI)"
    )

    return beams[["UniqueName", "SupportStatus"]].reset_index(drop=True)


# =============================================================================
# DATA STRUCTURES FOR MULTI-LOCATION DEMANDS
# =============================================================================
@dataclass
class FlexureLocationDemand:
    """Design moments for one beam location, with moments expressed in kN-m."""

    location_name: str
    Mu_neg: float  # kN-m
    Mu_pos: float  # kN-m


@dataclass
class TransverseLocationDemand:
    """Shear and torsion demands for one beam location, in kN and kN-m."""

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
        d_agg: float | None = None,
        code: AciCode = CODE,
    ):
        """Initialize the rectangular beam section and reinforcement design inputs (mm, MPa, kN-m).

        ``d_agg`` is the maximum aggregate size; ``None`` uses the configured default.
        ``code`` holds every ACI constant (see code_config.py).
        """
        self.code = code
        detailing = code.beam_detailing
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
        self.d_agg = detailing.default_aggregate_size if d_agg is None else d_agg

        # ACI beta1 block factor decreases with fc, bounded to the code-prescribed range.
        self.bheta = code.beta1(self.fc)
        self.max_bar_per_layer = self.calculate_max_bars_per_layer()

        n_min_150 = self.get_min_bars_for_150mm_spacing()
        self.n_top = n_min_150
        self.n_bot = n_min_150

    def calculate_max_bars_per_layer(self) -> int:
        """Return the number of longitudinal bars that fit in one layer under minimum-clear-spacing rules."""
        detailing = self.code.beam_detailing

        # Governing clear gap is the largest of code minimum, bar diameter, and aggregate-based limits.
        min_spacing = max(
            detailing.min_clear_spacing,
            self.dmain,
            detailing.aggregate_spacing_factor * self.d_agg,
        )

        # Width available between stirrup legs for longitudinal bars.
        clear_width = self.width - 2 * (self.cc + self.dstirrup)

        if clear_width < self.dmain:
            return 1

        return int((clear_width + min_spacing) // (self.dmain + min_spacing))

    def get_min_bars_for_150mm_spacing(self) -> int:
        """Return the minimum bar count needed to limit longitudinal bar spacing to 150 mm."""
        clear_center_width = self.width - 2 * (
            self.cc + self.dstirrup + (self.dmain / 2.0)
        )
        detailing = self.code.beam_detailing
        # Convert the clear-spacing target (150 mm) to a center-to-center spacing limit.
        max_center_spacing = detailing.max_clear_spacing_target + self.dmain
        min_spaces = math.ceil(clear_center_width / max_center_spacing)
        return max(detailing.min_bars_per_face, min_spaces + 1)

    def calculate_layer_distribution(self, n_bars: int) -> list:
        """Distribute a total bar count into consecutive layers within the section fit limit."""
        layers = []
        remaining = n_bars
        while remaining > 0:
            count = min(remaining, self.max_bar_per_layer)
            layers.append(count)
            remaining -= count
        return layers

    def compute_effective_depths(self, is_negative_moment: bool = True):
        """Calculate tension- and compression-steel centroid depths for the selected moment direction."""
        vert_clear_spacing = max(self.code.beam_detailing.layer_clear_spacing, self.dmain)
        center_spacing = self.dmain + vert_clear_spacing

        top_layers = self.calculate_layer_distribution(self.n_top)
        # Distance from the top concrete face to the center of the first top layer.
        top_d1 = self.cc + self.dstirrup + (self.dmain / 2.0)
        # Weight each layer's coordinate by bar count to locate the top-steel centroid.
        top_moments = sum(
            count * (top_d1 + i * center_spacing) for i, count in enumerate(top_layers)
        )
        d_top_centroid = top_moments / self.n_top if self.n_top > 0 else top_d1

        bot_layers = self.calculate_layer_distribution(self.n_bot)
        bot_d1_from_bottom = self.cc + self.dstirrup + (self.dmain / 2.0)
        # Use coordinates measured from the top so the bottom-steel centroid is comparable.
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
        """Compare actual tension-layer bar spacing with the crack-control spacing limit."""
        # ACI 24.3.2: cc is the clear cover to the *bar surface*, so the stirrup
        # diameter is added to the cover given to the stirrup.
        s_max_crack = self.code.crack_control_spacing(self.fy, self.cc + self.dstirrup)

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
        """Calculate phi*Mn including reinforcement on both beam faces.

        Strain compatibility determines steel stress and force equilibrium
        determines the neutral-axis depth. Compression steel within the
        Whitney block is corrected for the concrete it displaces. Forces are
        in N; moments are returned in kN-m.
        """
        d_tens, d_prime = self.compute_effective_depths(is_negative_moment)
        bar_area = math.pi * self.dmain**2 / 4.0

        n_tens = self.n_top if is_negative_moment else self.n_bot
        n_comp = self.n_bot if is_negative_moment else self.n_top
        # Tension-face steel area controls the tensile resultant in force equilibrium.
        As_tens = n_tens * bar_area
        # Opposite-face steel is included separately as compression/tension steel per strain compatibility.
        As_comp = n_comp * bar_area

        if self.width <= 0 or self.height <= 0 or self.fc <= 0 or self.fy <= 0:
            raise ValueError(
                "Section dimensions and material strengths must be positive."
            )
        if d_tens <= 0 or not 0 < self.bheta <= 1:
            raise ValueError("Effective depth and beta1 must be positive and valid.")

        Es = self.code.material.steel_elastic_modulus  # MPa
        eps_cu = self.code.material.concrete_ultimate_strain
        # Whitney-block concrete stress used with the equivalent block depth a = beta1*c.
        concrete_stress = self.code.material.stress_block_alpha * self.fc

        def section_force_residual(c: float) -> tuple[float, float, float, float]:
            """Return force imbalance and section state for trial neutral axis c."""
            a = self.bheta * c
            eps_t = eps_cu * (d_tens - c) / c
            fs_t = max(-self.fy, min(self.fy, Es * eps_t))
            eps_comp = eps_cu * (c - d_prime) / c
            fs_comp = max(-self.fy, min(self.fy, Es * eps_comp))

            concrete_force = concrete_stress * self.width * a
            compression_steel_force = As_comp * fs_comp
            if d_prime <= a and fs_comp > 0:
                compression_steel_force -= As_comp * concrete_stress

            # Root occurs where compression resultants balance the tension-steel resultant.
            residual = concrete_force + compression_steel_force - As_tens * fs_t
            return residual, a, eps_t, fs_comp

        # Bracket a neutral axis within the physical section before bisection.
        lower = 1e-6
        upper = min(self.height, d_tens)
        f_lower = section_force_residual(lower)[0]
        f_upper = section_force_residual(upper)[0]

        if f_lower == 0:
            c = lower
        elif f_upper == 0:
            c = upper
        elif f_lower * f_upper > 0:
            raise ValueError(
                "Could not bracket the flexural neutral axis within the section. "
                "Check the section dimensions and reinforcement."
            )
        else:
            for _ in range(80):
                c = (lower + upper) / 2.0
                f_mid = section_force_residual(c)[0]
                if abs(f_mid) <= 1e-8:
                    break
                if f_lower * f_mid <= 0:
                    upper = c
                else:
                    lower = c
                    f_lower = f_mid
            else:
                c = (lower + upper) / 2.0

        _, a, eps_t, fs_comp = section_force_residual(c)
        concrete_force = concrete_stress * self.width * a
        compression_steel_force = As_comp * fs_comp
        if d_prime <= a and fs_comp > 0:
            compression_steel_force -= As_comp * concrete_stress

        # Sum internal force times lever arm about the tension-steel centroid for nominal Mn.
        Mn = (
            concrete_force * (d_tens - a / 2.0)
            + compression_steel_force * (d_tens - d_prime)
        ) / 1e6
        # Strength reduction factor follows the tensile strain-based transition
        # (ACI Table 21.2.2). Uses the real yield strain, exactly like the column designer.
        phi = self.code.phi_flexure(eps_t, self.fy)

        return {
            "phi_Mn": phi * Mn,
            "Mn": Mn,
            "et": eps_t,
            "phi": phi,
            "failure_mode": (
                "Tension-Controlled"
                if eps_t >= self.code.strength.tension_controlled_strain
                else "Compression-Controlled"
            ),
            "As_provided": As_tens,
            "As_compression": As_comp,
            "fs_compression": fs_comp,
            "c": c,
            "a": a,
        }

    def check_reinforcement_limits(self, is_negative_moment: bool = True):
        """Check minimum longitudinal steel and crack-control requirements for one flexural direction."""
        d_tens, _ = self.compute_effective_depths(is_negative_moment)
        As_provided = (self.n_top if is_negative_moment else self.n_bot) * (
            (math.pi / 4) * self.dmain**2
        )

        # Two code minimum-steel expressions are evaluated; the larger one governs.
        flexure_cfg = self.code.beam_flexure
        As_min_1 = (
            (flexure_cfg.as_min_coeff_sqrt_fc * math.sqrt(self.fc) / self.fy)
            * self.width
            * d_tens
        )
        As_min_2 = (flexure_cfg.as_min_coeff_fy / self.fy) * self.width * d_tens
        As_min_code = max(As_min_1, As_min_2)

        n_min_150 = self.get_min_bars_for_150mm_spacing()
        # Minimum area implied by the independent 150 mm bar-spacing requirement.
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
        """Increase top and bottom reinforcement until strength, minimum-steel, spacing, and detailing checks pass or capacity is exhausted."""
        n_min_150 = self.get_min_bars_for_150mm_spacing()
        self.n_top = max(self.n_top, n_min_150)
        self.n_bot = max(self.n_bot, n_min_150)

        # This solver limits detailing to three bar layers before reporting congestion.
        max_layers = self.code.beam_detailing.max_layers
        max_allowed_bars = max_layers * self.max_bar_per_layer
        self.rebar_congestion_exceeded = False

        while True:
            top_layers = self.calculate_layer_distribution(self.n_top)
            bot_layers = self.calculate_layer_distribution(self.n_bot)

            # --- STRICT MINIMUM OF 2 BARS PER LAYER (ADD ONLY) ---
            # If the calculation results in exactly 1 bar in the last layer (e.g., [6, 1]),
            # instantly add another bar to the total count (becoming [6, 2]) and re-loop.
            if self.max_bar_per_layer >= 2:
                needs_increment = False

                if top_layers and top_layers[-1] == 1 and self.n_top < max_allowed_bars:
                    self.n_top += 1
                    needs_increment = True

                if bot_layers and bot_layers[-1] == 1 and self.n_bot < max_allowed_bars:
                    self.n_bot += 1
                    needs_increment = True

                if needs_increment:
                    continue
            # -----------------------------------------------------

            # Cap layer depth at 3 layers and flag congestion if exceeded
            if len(top_layers) > max_layers or len(bot_layers) > max_layers:
                self.rebar_congestion_exceeded = True
                if len(top_layers) > max_layers:
                    self.n_top = max_allowed_bars
                if len(bot_layers) > max_layers:
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

    def clean_single_bars(self):
        """Post-process check to ensure no manual overrides result in exactly 1 bar in the last layer."""
        if self.max_bar_per_layer >= 2:
            top_layers = self.calculate_layer_distribution(self.n_top)
            if top_layers and top_layers[-1] == 1:
                self.n_top += 1

            bot_layers = self.calculate_layer_distribution(self.n_bot)
            if bot_layers and bot_layers[-1] == 1:
                self.n_bot += 1


# =============================================================================
# SHEAR & TORSION DESIGN CLASSES
# =============================================================================
class BeamShearDesign:
    """Calculate beam shear strength and required transverse reinforcement."""

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
        code: AciCode = CODE,
    ):
        """Store shear-section dimensions, material strengths, stirrup legs, and Vc override."""
        self.code = code
        self.width = width
        self.height = height
        self.d_tens = d_tens
        self.fc = fc
        self.fyt = fyt
        self.dstirrup = dstirrup
        self.n_legs = max(code.beam_detailing.min_stirrup_legs, n_legs)
        self.suppress_Vc = suppress_Vc

    def solve_shear_capacity(self, Vu: float) -> dict:
        """Calculate concrete and stirrup shear contributions, required stirrup area, and spacing limit."""
        cfg = self.code.beam_shear
        phi_v = self.code.strength.shear
        sqrt_fc_bd = math.sqrt(self.fc) * self.width * self.d_tens / 1000.0  # kN per unit coefficient
        # Concrete shear contribution is intentionally zero when seismic criteria require it.
        Vc = 0.0 if self.suppress_Vc else cfg.vc_coeff * sqrt_fc_bd
        # Upper stirrup shear contribution used to cap the section shear strength.
        Vs_max = cfg.vs_max_coeff * sqrt_fc_bd
        # ACI 20.2.2.4 caps fyt used in shear design.
        fyt_design = min(self.fyt, self.code.material.max_fyt_shear)

        max_allowable_phi_Vn = phi_v * (Vc + Vs_max)
        shear_failed = Vu > max_allowable_phi_Vn

        # Calculate required Vs (clamped if section fails)
        # Required stirrup contribution after removing concrete resistance from factored demand.
        Vs_req = max(0.0, (Vu / phi_v) - Vc)
        # Required Av/s in mm^2 per mm, derived from Vs = Av*fyt*d/s.
        Av_s_demand = (
            (Vs_req * 1000.0) / (fyt_design * self.d_tens) if Vs_req > 0 else 0.0
        )

        if Vs_req <= cfg.vs_spacing_threshold_coeff * sqrt_fc_bd:
            s_max = min(self.d_tens * cfg.s_max_d_fraction_low, cfg.s_max_abs_low)
        else:
            s_max = min(self.d_tens * cfg.s_max_d_fraction_high, cfg.s_max_abs_high)

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
    """Calculate beam torsion strength and required transverse/longitudinal reinforcement."""

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
        code: AciCode = CODE,
    ):
        """Store section/material inputs and derive the closed stirrup-centerline torsion geometry."""
        self.code = code
        self.width = width
        self.height = height
        self.d_tens = d_tens
        self.fc = fc
        self.fy = fy
        self.fyt = fyt
        self.dstirrup = dstirrup
        self.cc = cc

        # Gross concrete area and perimeter are used by the torsion threshold equations.
        self.Acp = self.width * self.height
        self.Pcp = 2 * (self.width + self.height)
        self.x1 = self.width - 2 * self.cc - self.dstirrup
        self.y1 = self.height - 2 * self.cc - self.dstirrup
        self.Aoh = self.x1 * self.y1
        # Effective closed shear-flow area is taken as 85% of the stirrup-centerline area.
        self.A_o = code.beam_torsion.ao_over_aoh * self.Aoh
        self.p_h = 2 * (self.x1 + self.y1)

    def solve_torsion_capacity(self, Tu: float, Vu: float) -> dict:
        """Check torsion thresholds and combined stress, then calculate transverse and longitudinal torsion steel."""
        cfg = self.code.beam_torsion
        phi_t = self.code.strength.torsion
        sqrt_fc = math.sqrt(self.fc)
        # Section property Acp^2/pcp used by both torsion limits (N-mm units -> kN-m at the end).
        acp2_over_pcp = self.Acp**2 / self.Pcp

        # Threshold torsion (ACI 22.7.4.1): torsion is ignored when Tu < phi*Tth.
        # Here Tth already includes phi, matching the way it is compared with Tu.
        Tth = phi_t * cfg.threshold_coeff * sqrt_fc * acp2_over_pcp / 1e6
        # Cracking torsion WITHOUT phi (ACI 22.7.5.1); phi_Tcr is the design value.
        Tcr = cfg.cracking_coeff * sqrt_fc * acp2_over_pcp / 1e6
        phi_Tcr = phi_t * Tcr

        torsion_required = Tu > Tth
        # ACI 22.7.3.2: Tu may drop to phi*Tcr ONLY for compatibility torsion.
        Tu_design = (
            min(Tu, phi_Tcr) if (torsion_required and cfg.allow_redistribution) else Tu
        )

        # Section-size check, ACI 22.7.7.1(a).
        shear_stress = (Vu * 1000.0) / (self.width * self.d_tens)
        torsion_stress = (Tu_design * 1e6 * self.p_h) / (
            cfg.stress_limit_denominator * (self.Aoh**2)
        )
        combined_stress = math.sqrt(shear_stress**2 + torsion_stress**2)
        shear_cfg = self.code.beam_shear
        allowable_stress = phi_t * (
            shear_cfg.vc_coeff * sqrt_fc + shear_cfg.vs_max_coeff * sqrt_fc
        )

        torsion_failed = combined_stress > allowable_stress

        # cot(theta) squared; theta = 45 deg gives 1.0 (ACI 22.7.6.1.2).
        cot_theta = 1.0 / math.tan(math.radians(cfg.truss_angle_degrees))
        # ACI 20.2.2.4 (NSCP 420.2.2.4): fy and fyt are at most 420 MPa for torsion.
        fyt_design = min(self.fyt, self.code.material.max_fyt_shear)
        fy_design = min(self.fy, self.code.material.max_fyt_shear)
        if torsion_required:
            Tn_req = (Tu_design * 1e6) / phi_t
            # Closed stirrup requirement from the torsional shear-flow equilibrium (Eq. 22.7.6.1a).
            At_s_demand = Tn_req / (2.0 * self.A_o * fyt_design * cot_theta)
            # Longitudinal torsion steel (Eq. 22.7.6.1b).
            Al_demand = At_s_demand * self.p_h * (fyt_design / fy_design) * cot_theta**2

            # ACI 9.6.4.3: the minimum uses fy (NOT fyt) in the first term.
            term_1 = cfg.al_min_coeff * sqrt_fc * self.Acp / fy_design
            at_s_floor = cfg.at_s_min_coeff * self.width / fyt_design
            Al_min_a = term_1 - At_s_demand * self.p_h * (fyt_design / fy_design)
            Al_min_b = term_1 - at_s_floor * self.p_h * (fyt_design / fy_design)
            Al_design = max(Al_demand, max(0.0, min(Al_min_a, Al_min_b)))
        else:
            At_s_demand = 0.0
            Al_design = 0.0

        shear_cfg = self.code.beam_shear
        Av_2At_s_min = max(
            (shear_cfg.av_min_coeff_sqrt_fc * sqrt_fc * self.width) / fyt_design,
            (shear_cfg.av_min_coeff_fyt * self.width) / fyt_design,
        )
        s_max_torsion = min(self.p_h / cfg.s_max_ph_divisor, cfg.s_max_abs)

        return {
            "Tth": Tth,
            "Tcr": Tcr,
            "phi_Tcr": phi_Tcr,
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
        code: AciCode = CODE,
    ):
        """Store location engines, span, axial load, and the seismic-check enable flag."""
        self.code = code
        self.flexure_engines = flexure_engines
        self.clear_span = clear_span
        self.Pu_axial = Pu_axial
        self.enable_seismic = enable_seismic

    def check_reinforcement_ratio_limits(self) -> dict:
        """Check top and bottom longitudinal reinforcement ratios at every beam design location."""
        results = {}
        all_passed = True

        for loc_name, eng in self.flexure_engines.items():
            d_neg, _ = eng.compute_effective_depths(is_negative_moment=True)
            d_pos, _ = eng.compute_effective_depths(is_negative_moment=False)

            As_top = eng.n_top * ((math.pi / 4) * eng.dmain**2)
            As_bot = eng.n_bot * ((math.pi / 4) * eng.dmain**2)

            # Reinforcement ratio uses the effective depth for top-face tension.
            rho_top = As_top / (eng.width * d_neg)
            # Reinforcement ratio uses the effective depth for bottom-face tension.
            rho_bot = As_bot / (eng.width * d_pos)

            rho_limit = self.code.beam_seismic.max_reinforcement_ratio
            top_pass = rho_top <= rho_limit
            bot_pass = rho_bot <= rho_limit

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
        """Check the SMF support and span minimum flexural-capacity ratios."""
        left_eng = self.flexure_engines["Left Support Face"]
        right_eng = self.flexure_engines["Right Support Face"]

        res_left = left_eng.solve_moment_capacity(is_negative_moment=True)
        res_right = right_eng.solve_moment_capacity(is_negative_moment=True)

        Mn_neg_left = res_left["phi_Mn"] / res_left["phi"]
        Mn_neg_right = res_right["phi_Mn"] / res_right["phi"]

        max_M_neg_support = max(Mn_neg_left, Mn_neg_right)

        # SMF support positive capacity target: half the larger support negative capacity.
        seismic = self.code.beam_seismic
        req_half_cap = seismic.positive_to_negative_at_face * max_M_neg_support
        # SMF span minimum capacity target: one-quarter of governing support negative capacity.
        req_quarter_cap = seismic.min_moment_at_any_section * max_M_neg_support

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

    def enforce_flexural_capacity_ratios(self, max_rounds: int = 60) -> bool:
        """Add bars until the ACI 18.6.3.2 strength ratios are met.

        At a support face the positive moment strength must be at least half the
        negative strength, and every section must have at least a quarter of the
        largest support strength, top and bottom. Bars are added one at a time
        where a rule is not met. Returns False when the bar limit is reached first.
        """
        max_layers = self.code.beam_detailing.max_layers
        for _ in range(max_rounds):
            check = self.check_flexural_capacity_ratios()
            if check["all_passed"]:
                return True
            changed = False
            for loc_name, result in check["locations"].items():
                eng = self.flexure_engines[loc_name]
                limit = max_layers * eng.max_bar_per_layer
                needs_bottom = (
                    not result["pass_half"]
                    or result["Mn_pos"] < check["req_quarter_cap"]
                )
                needs_top = result["Mn_neg"] < check["req_quarter_cap"]
                if needs_bottom and eng.n_bot < limit:
                    eng.n_bot += 1
                    changed = True
                if needs_top and eng.n_top < limit:
                    eng.n_top += 1
                    changed = True
            if not changed:
                return False
        return self.check_flexural_capacity_ratios()["all_passed"]

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

        # Probable-moment calculation applies the expected steel stress multiplier.
        f_seismic = self.code.beam_seismic.probable_stress_factor * eng.fy
        a_pr = (As_total_tens * f_seismic) / (
            self.code.material.stress_block_alpha * eng.fc * eng.width
        )
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
        """Combine probable-moment sway shear with gravity demand and determine when concrete shear is suppressed."""
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
        # Sway shear for one moment-reversal sense; moments are divided by clear span.
        V_sway_cw = (M_pr_top_left + M_pr_bot_right) / L_n_m
        # Opposite reversal sense; the larger sense governs seismic shear.
        V_sway_ccw = (M_pr_bot_left + M_pr_top_right) / L_n_m
        V_sway_max = max(V_sway_cw, V_sway_ccw)

        # 3. Seismic Combo Demand (Gravity + Sway)
        # Combine the gravity end shear envelope with capacity-design sway shear.
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
        shear_fraction = self.code.beam_seismic.vc_zero_shear_fraction
        cond1_left = (
            (V_sway_max / Vu_total_max_left) >= shear_fraction
            if Vu_total_max_left > 0
            else False
        )
        cond1_right = (
            (V_sway_max / Vu_total_max_right) >= shear_fraction
            if Vu_total_max_right > 0
            else False
        )

        Ag = left_eng.width * left_eng.height
        # Axial-load threshold for the Vc=0 condition, converted from N to kN.
        Pu_limit = (
            left_eng.fc * Ag / self.code.beam_seismic.vc_zero_axial_divisor
        ) / 1000.0
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
    code: AciCode = CODE,
) -> dict:
    """
    Distributes longitudinal torsion steel (Al) across 4 faces:
    - Top Face: Al/4
    - Bottom Face: Al/4
    - Side Web Faces: Al/2 combined (Al/4 per face)
    Enforces side spacing and skin reinforcement ONLY if Al > 0 or h > 900mm.
    """
    # Split total longitudinal torsion steel equally across four beam faces.
    Al_face = Al_design / 4.0
    Al_top_req = Al_face
    Al_bot_req = Al_face
    Al_sides_req = 2 * Al_face

    # Clear side-face height available for distributed skin/torsion reinforcement.
    web_height = height - 2 * (cc + dstirrup + db_main / 2.0)

    # 1. Check if Torsion or Deep Beam Skin Reinforcement is Active
    torsion_active = Al_design > 0.0
    deep_beam_active = height > code.beam_flexure.skin_reinforcement_depth

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
    s_max_side = code.beam_flexure.skin_max_spacing
    if deep_beam_active:
        # ACI 9.7.2.3: skin bars follow the 24.3.2 spacing, measured from the side face.
        s_skin_code = code.crack_control_spacing(fy, cc + dstirrup)
        s_max_side = min(s_max_side, s_skin_code)

    # 3. Calculate Required Side Bars (Strength vs Spacing)
    # Number of vertical spacing intervals needed to meet the side-bar spacing limit.
    min_spaces = math.ceil(web_height / s_max_side)
    n_side_spacing_per_face = max(0, min_spaces - 1)

    area_single_side_bar = (math.pi / 4) * (db_side**2)
    # Bars per side face required by torsional steel area, separate from spacing minimum.
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
    """Add longitudinal bars when needed to anchor the required number of stirrup legs."""
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
    """Return the stirrup-leg count needed to laterally support alternating longitudinal bars."""
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
    """Build transverse demand records using governing interior-web shear and torsion."""
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
    """Select the engine for a beam zone and return its smaller tension effective depth."""
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
    code: AciCode = CODE,
    progress=None,
) -> pd.DataFrame:
    """Executes the full beam design pipeline and returns the results DataFrame.

    ``progress`` (optional) receives one status text at a time: the beam, its
    level, the load combination and the check being performed.
    """

    def report(member, story, combo, check) -> None:
        if progress is not None:
            progress(f"Beam {member}  |  Level {story}\nCombo: {combo}\nCheck: {check}")

    detailing = code.beam_detailing
    seismic_cfg = code.beam_seismic

    # =============================================================================
    # 2. BUILD EXTRACTION DEMANDS DATAFRAME PER COMBO
    # =============================================================================
    design_rows = []

    for _, prop_row in df_beam_props.iterrows():
        u_name = prop_row["UniqueName"]
        h = prop_row.get("Depth", prop_row.get("Height", 500))
        two_h = seismic_cfg.hoop_zone_depth_factor * h

        df_forces_beam = df_frame_forces[df_frame_forces["UniqueName"] == u_name]

        if not df_forces_beam.empty:
            min_st = df_forces_beam["Station"].min()
            max_st = df_forces_beam["Station"].max()
            span_length = max_st - min_st
            # Clear span ln between the support faces, for the probable-moment
            # shear Ve = (Mpr1 + Mpr2) / ln (ACI 18.6.5.1): the joint-to-joint
            # length less half of each supporting column.
            support_half = sum(
                0.5 * float(prop_row.get(key) or 0.0)
                for key in ("SupportWidth_I", "SupportWidth_J")
                if pd.notna(prop_row.get(key))
            )
            clear_span = span_length - support_half if span_length > support_half else span_length

            # Moment design zones use the end quarter of the modeled span.
            m_left_boundary = min_st + detailing.moment_zone_fraction * span_length
            # Mirror the left end zone so the central flexural region excludes both end quarters.
            m_right_boundary = max_st - detailing.moment_zone_fraction * span_length

            # Shear/torsion end zones extend 2h from each beam end.
            v_left_boundary = min_st + two_h
            v_right_boundary = max_st - two_h

            combos = df_forces_beam["Combo"].unique()

            for combo in combos:
                report(u_name, prop_row.get("Story", "-"), combo,
                       "Demands: Mu, Vu and Tu at the supports and midspan")
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

                # Convert negative M3 values into positive design-demand magnitudes for the top face.
                Mneg_left = (
                    abs(min(0.0, df_left_m["M3"].min())) if not df_left_m.empty else 0.0
                )
                # Positive M3 envelope is assigned to bottom-face flexural design.
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

                # Absolute peak shear in each end zone is used as the transverse design demand.
                Vd_left = df_left_vt["V2"].abs().max() if not df_left_vt.empty else 0.0
                V2h = df_mid_vt["V2"].abs().max() if not df_mid_vt.empty else 0.0
                Vd_right = (
                    df_right_vt["V2"].abs().max() if not df_right_vt.empty else 0.0
                )

                # Absolute peak torsion is paired with the shear demand for each zone.
                Td_left = df_left_vt["T"].abs().max() if not df_left_vt.empty else 0.0
                T2h = df_mid_vt["T"].abs().max() if not df_mid_vt.empty else 0.0
                Td_right = (
                    df_right_vt["T"].abs().max() if not df_right_vt.empty else 0.0
                )

                # Top Row
                top_row = prop_row.to_dict()
                top_row["Combo"] = combo
                top_row["Face"] = "TOP"
                top_row["ClearSpan_Ln"] = clear_span
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
                bot_row["ClearSpan_Ln"] = clear_span
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

        # Seismic provisions apply to the frame members only, not to gravity beams.
        seismic_here = enable_seismic_design and not is_gravity_beam(
            b_row.get("SupportStatus", "")
        )

        df_grav = df_b[df_b["Combo"] == gravity_combo_name]
        Vu_grav_left = df_grav["Vu_left"].max() if not df_grav.empty else 0.0
        Vu_grav_right = df_grav["Vu_right"].max() if not df_grav.empty else 0.0
        Vu_grav_mid = df_grav["Vu_mid_2h"].max() if not df_grav.empty else 0.0

        prev_state = None
        smrf_flexure_check = "N/A"
        smrf_rho_check = "N/A"

        story = b_row.get("Story", "-")
        moments = df_b[["Mu_left", "Mu_mid", "Mu_right"]].max(axis=1)
        governing = df_b.loc[moments.idxmax(), "Combo"] if len(df_b) else "-"
        while True:
            # --- STEP 3A: Flexure Design ---
            report(unique_name, story,
                   f"envelope of {df_b['Combo'].nunique()} (largest moment: {governing})",
                   "Flexure at the left support, midspan and right support")
            flex_eng_left, flex_eng_mid, flex_eng_right = (
                BeamFlexureDesign(
                    b_width, b_height, fc_val, fy_val, fyt_val, d_m, d_s, c_cover,
                    code=code,
                )
                for _ in range(3)
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

            # Initial effective-depth estimate for torsion steel distribution before flexure layers are known.
            d_eff_guess = b_height - c_cover - d_s - (d_m / 2.0)
            torsion_eng_left, torsion_eng_mid, torsion_eng_right = (
                BeamTorsionDesign(
                    b_width, b_height, d_eff_guess, fc_val, fy_val, fyt_val, d_s, c_cover,
                    code=code,
                )
                for _ in range(3)
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
                code,
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
                code,
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
                code,
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

                # --- ADD CLEANUP HERE ---
                flex_eng_left.clean_single_bars()
                flex_eng_mid.clean_single_bars()
                flex_eng_right.clean_single_bars()

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
            if seismic_here:
                report(unique_name, story, gravity_combo_name or "-",
                       "SMRF moment strength ratios and probable-moment shear")
            seismic_checker = BeamSeismicDesign(
                flex_engines, span_ln, Pu_axial_load, seismic_here, code
            )

            # ACI 18.6.3: strength ratios along the span and the 2.5 % steel limit.
            # A cantilever is not part of the moment frame, so it is left out.
            if seismic_here and not is_cantilever:
                ratios_met = seismic_checker.enforce_flexural_capacity_ratios()
                for eng in flex_engines.values():
                    eng.clean_single_bars()
                smrf_flexure_check = "PASS" if ratios_met else "FAIL"
                smrf_rho_check = (
                    "PASS"
                    if seismic_checker.check_reinforcement_ratio_limits()["all_passed"]
                    else "FAIL"
                )

            if is_cantilever and seismic_here:
                # "Free at PtI" means the support is at the J end (right engine).
                supported_eng = (
                    flex_eng_right
                    if "Free at PtI" in str(b_row.get("SupportStatus", ""))
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
            elif is_cantilever:
                # Without seismic design, retain gravity demand and concrete shear strength.
                seismic_res = {
                    "V_sway_max": 0.0,
                    "Vu_seismic_left": abs(Vu_grav_left),
                    "Vu_seismic_right": abs(Vu_grav_right),
                    "Vc_zero_left": False,
                    "Vc_zero_right": False,
                }
            else:
                seismic_res = seismic_checker.evaluate_seismic_shear_demands(
                    Vu_grav_left,
                    Vu_grav_right,
                    side_distributions,
                    Vu_envelope_max_left=max_Vu_left,
                    Vu_envelope_max_right=max_Vu_right,
                )

            min_legs = detailing.min_stirrup_legs
            gov_legs_left = gov_legs_mid = gov_legs_right = min_legs
            min_s_left = min_s_mid = min_s_right = detailing.max_spacing_default
            section_failures: list[str] = []

            for combo_name in df_b["Combo"].unique():
                df_c_top = df_b[
                    (df_b["Combo"] == combo_name) & (df_b["Face"] == "TOP")
                ].iloc[0]

                Vu_L = (
                    max(df_c_top["Vu_left"], seismic_res["Vu_seismic_left"])
                    if seismic_here
                    else df_c_top["Vu_left"]
                )
                # ACI 18.6.5.1: the probable-moment shear acts along the whole span,
                # so beyond 2h it is the sway shear plus the gravity shear there.
                Vu_M = (
                    max(df_c_top["Vu_mid_2h"],
                        seismic_res["V_sway_max"] + abs(Vu_grav_mid))
                    if seismic_here and not is_cantilever
                    else df_c_top["Vu_mid_2h"]
                )
                Vu_R = (
                    max(df_c_top["Vu_right"], seismic_res["Vu_seismic_right"])
                    if seismic_here
                    else df_c_top["Vu_right"]
                )

                for zone_name, Vu_val, Tu_val, leg_var in [
                    ("Left Support (d_eff)", Vu_L, df_c_top["Tu_left"], "L"),
                    ("Interior Web (Gov. 2h)", Vu_M, df_c_top["Tu_mid_2h"], "M"),
                    ("Right Support (d_eff)", Vu_R, df_c_top["Tu_right"], "R"),
                ]:
                    report(unique_name, story, combo_name,
                           f"Shear and torsion, {zone_name.split(' (')[0].lower()}")
                    # Use the shallower face-specific effective depth as the conservative transverse-design depth.
                    d_eff_z = get_location_effective_depth(zone_name, flex_engines)
                    n_legs = min_legs
                    while True:
                        shear = BeamShearDesign(
                            b_width,
                            b_height,
                            d_eff_z,
                            fc_val,
                            fyt_val,
                            d_s,
                            n_legs,
                            code=code,
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
                            code=code,
                        )

                        s_r = shear.solve_shear_capacity(Vu_val)
                        t_r = torsion.solve_torsion_capacity(Tu_val, Vu_val)
                        if s_r["shear_failed"] or t_r["torsion_failed"]:
                            failure = (
                                "SHEAR STRENGTH: Vu > phi(Vc + 0.66 sqrt(fc') bw d), "
                                "ACI 22.5.1.2" if s_r["shear_failed"]
                                else "SHEAR STRENGTH: shear + torsion stress above "
                                "ACI 22.7.7.1"
                            )
                            if failure not in section_failures:
                                section_failures.append(failure)

                        # Required transverse steel ratio must satisfy shear-plus-torsion and torsion minimums.
                        des_ratio = max(
                            s_r["Av_s_demand"] + 2 * t_r["At_s_demand"],
                            t_r["Av_2At_s_min"],
                        )
                        s_max = min(s_r["s_max_code"], t_r["s_max_torsion"])

                        # SEISMIC SPACING OVERRIDE
                        if seismic_here and leg_var in ["L", "R"]:
                            s_max_seismic = min(
                                d_eff_z * seismic_cfg.hoop_spacing_d_fraction,
                                seismic_cfg.hoop_spacing_bar_multiple * d_m,
                                seismic_cfg.hoop_spacing_abs,
                            )
                            s_max = min(s_max, s_max_seismic)

                        Av_prov = n_legs * ((math.pi / 4) * d_s**2)
                        s_raw = (
                            min(Av_prov / des_ratio, s_max) if des_ratio > 0 else s_max
                        )
                        # Round spacing down to the nearest step (25 mm) so rounding cannot exceed the limit.
                        step = detailing.stirrup_spacing_step
                        s_rec = int((s_raw // step) * step)

                        if (
                            s_rec < detailing.min_practical_spacing
                            and n_legs < flex_eng_mid.max_bar_per_layer
                        ):
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

            # One common stirrup-leg arrangement must satisfy the most demanding beam zone.
            governing_beam_legs = max(gov_legs_left, gov_legs_mid, gov_legs_right)
            # Support-zone spacing is governed by the tighter of the two beam ends.
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

                    # --- ADD CLEANUP HERE ---
                    eng.clean_single_bars()

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

                # --- ADD CLEANUP HERE ---
                flex_eng_left.clean_single_bars()
                flex_eng_mid.clean_single_bars()
                flex_eng_right.clean_single_bars()

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
            # Design shear at each end: gravity shear plus the sway shear above.
            summary["Ve_left_kN"] = seismic_res["Vu_seismic_left"]
            summary["Ve_right_kN"] = seismic_res["Vu_seismic_right"]
            summary["Vc_zero_left"] = seismic_res["Vc_zero_left"]
            summary["Vc_zero_right"] = seismic_res["Vc_zero_right"]
            summary["Anchorage_Check"] = (
                "PASSED" if anchorage_passed_all else "ADJUSTED"
            )
            summary["Alternating_Tie_Check"] = "PASSED"
            summary["SMRF_Flexure_Ratio_Check"] = smrf_flexure_check
            summary["SMRF_Rho_Check"] = smrf_rho_check

            # --- NEW: Catch Congestion Warnings ---
            if (
                flex_eng_left.rebar_congestion_exceeded
                or flex_eng_mid.rebar_congestion_exceeded
                or flex_eng_right.rebar_congestion_exceeded
            ):
                summary["Design_Status"] = "FAILED: MAX BARS EXCEEDED (>3 LAYERS)"
            elif smrf_flexure_check == "FAIL":
                summary["Design_Status"] = (
                    "FAILED: SMRF MOMENT STRENGTH RATIOS (ACI 18.6.3.2)"
                )
            elif smrf_rho_check == "FAIL":
                summary["Design_Status"] = (
                    "FAILED: SMRF STEEL RATIO ABOVE 2.5% (ACI 18.6.3.1)"
                )
            elif section_failures:  # the section, not the stirrups, is too small
                summary["Design_Status"] = "FAILED: " + "; ".join(section_failures)
            elif (
                s_2h < detailing.min_acceptable_spacing
                or s_mid < detailing.min_acceptable_spacing
            ):  # Flag if shear spacing is unrealistically tight
                summary["Design_Status"] = (
                    f"FAILED: SHEAR SPACING < {detailing.min_acceptable_spacing:g}mm"
                )
            else:
                summary["Design_Status"] = "OK"

            results_list.append(summary)

    df_beam_design_results = pd.DataFrame(results_list).round(2)
    return df_beam_design_results


_BEAM_MARK_PATTERN = re.compile(r"^(.*?)(BX|BY|GX|GY)-(\d+)(.*)$", re.IGNORECASE)
_BEAM_TYPE_PRIORITY = {"BX": 1, "BY": 2, "GX": 3, "GY": 4}


def beam_sort_key(name: object, face: object = "") -> tuple:
    """Order a beam mark by floor, type, numeric mark, suffix, and face."""
    name_text = str(name).upper()
    face_priority = 0 if "TOP" in str(face).upper() else 1
    match = _BEAM_MARK_PATTERN.match(name_text)
    if not match:
        return name_text, 5, 0, "", face_priority

    floor, beam_type, number, suffix = match.groups()
    return (
        floor,
        _BEAM_TYPE_PRIORITY[beam_type.upper()],
        int(number),
        suffix,
        face_priority,
    )


def sort_beam_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return rows sorted by beam mark and, when present, TOP before BOTTOM."""
    if "UniqueName" not in df.columns:
        return df.reset_index(drop=True)

    result = df.copy()
    if "Face" in result.columns:
        result["_sort_key"] = [
            beam_sort_key(name, face)
            for name, face in zip(result["UniqueName"], result["Face"])
        ]
    else:
        result["_sort_key"] = result["UniqueName"].map(beam_sort_key)

    return (
        result.sort_values("_sort_key", kind="stable")
        .drop(columns="_sort_key")
        .reset_index(drop=True)
    )


_BEAM_RESULT_LABELS = {
    **{
        f"n_{location}_L{layer}": f"n₍{location}, {layer}₎"
        for location in ("left", "mid", "right")
        for layer in (1, 2, 3)
    },
    "Mu_left": "Mᵤ, left",
    "Mu_mid": "Mᵤ, mid",
    "Mu_right": "Mᵤ, right",
    "Vu_left": "Vᵤ, left",
    "Vu_mid_2h": "Vᵤ, mid (2h)",
    "Vu_right": "Vᵤ, right",
    "Tu_left": "Tᵤ, left",
    "Tu_mid_2h": "Tᵤ, mid (2h)",
    "Tu_right": "Tᵤ, right",
    "ClearSpan_Ln": "Lₙ",
    "Spacing_2H": "s₂H (mm)",
    "Spacing_Mid": "sₘᵢd (mm)",
    "V_sway_max_kN": "Vₛway,max (kN)",
    "Ve_left_kN": "Vₑ, left (kN)",
    "Ve_right_kN": "Vₑ, right (kN)",
    "SMRF_Flexure_Ratio_Check": "SMRF moment strength ratio check",
    "SMRF_Rho_Check": "SMRF steel ratio check",
    "n_side_per_face_gov": "Side bars / face (governing)",
    "Stirrup_Legs": "Stirrup legs",
    "Vc_zero_left": "Concrete shear suppressed (left)",
    "Vc_zero_right": "Concrete shear suppressed (right)",
    "Anchorage_Check": "Stirrup anchorage check",
    "Alternating_Tie_Check": "Alternating tie check",
    **DEFLECTION_COLUMNS,
    "Design_Status": "Design status",
}


def display_beam_result_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with readable Excel headers while retaining internal keys."""
    mapping = {
        source: display
        for source, display in _BEAM_RESULT_LABELS.items()
        if source in df.columns
    }
    return df.rename(columns=mapping)


def restore_beam_result_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Restore internal column keys from display labels read back from Excel."""
    inverse = {display: source for source, display in _BEAM_RESULT_LABELS.items()}
    mapping = {display: source for display, source in inverse.items() if display in df}
    return df.rename(columns=mapping)


def _beam_sort_key(name) -> tuple:
    """Floor, type (BX, BY, GX, GY), number and suffix of a beam mark."""
    name_str = str(name).upper()
    priority = next((i for i, t in enumerate(("BX", "BY", "GX", "GY"), start=1)
                     if t in name_str), 5)
    match = re.search(r"^(.*?)(BX|BY|GX|GY)-(\d+)(.*)$", name_str)
    if match:
        return (match.group(1), priority, int(match.group(3)), match.group(4))
    return (name_str, priority, 0, "")


BEAM_BAR_INPUTS = ("dm", "ds", "dw", "fyw", "cc")  # main, stirrup, web bar, web fy, cover


def _support_widths(frame_df: pd.DataFrame, conn_df: pd.DataFrame) -> pd.DataFrame:
    """The largest side of the column at each end of every beam (0 when none), mm.

    The beam direction is not used, so the larger column side is taken: the
    clear span, and with it the probable-moment shear, errs on the safe side.
    """
    frame = frame_df.copy()
    frame.columns = [str(c).strip() for c in frame.columns]
    conn = conn_df.copy()
    conn.columns = [str(c).strip() for c in conn.columns]
    if not {"UniqueName", "DesignType", "UniquePtI", "UniquePtJ"} <= set(conn.columns):
        return pd.DataFrame(columns=["UniqueName", "SupportWidth_I", "SupportWidth_J"])
    sizes = {}
    for row in frame.to_dict("records"):
        values = [pd.to_numeric(row.get(key), errors="coerce")
                  for key in ("Width", "Depth", "Diameter")]
        values = [float(v) for v in values if pd.notna(v) and float(v) > 0]
        if values:
            sizes[str(row.get("UniqueName"))] = max(values)
    width_at: dict[str, float] = {}
    columns = conn[conn["DesignType"].astype(str).eq("Column")]
    for row in columns.to_dict("records"):
        side = sizes.get(str(row["UniqueName"]), 0.0)
        for point in (row["UniquePtI"], row["UniquePtJ"]):
            width_at[point] = max(width_at.get(point, 0.0), side)
    beams = conn[conn["DesignType"].astype(str).eq("Beam")]
    return pd.DataFrame({
        "UniqueName": beams["UniqueName"].to_numpy(),
        "SupportWidth_I": [width_at.get(p, 0.0) for p in beams["UniquePtI"]],
        "SupportWidth_J": [width_at.get(p, 0.0) for p in beams["UniquePtJ"]],
    })


def prepare_beam_table(frame_df: pd.DataFrame, conn_df: pd.DataFrame,
                       bars: dict, earth_cover_stories=()) -> pd.DataFrame:
    """The beams of FRAME DATA with their support status and the bar inputs.

    ``bars`` has dm (main bar), ds (stirrup), dw (web bar) in mm, fyw (web bar
    yield strength, MPa) and cc (cover, mm). The beams of the stories in
    ``earth_cover_stories`` (cast against or exposed to earth: footing tie
    beams, ground beams) get the earth-contact cover instead, 75 mm. One row
    per beam, sorted by floor, type and mark.
    """
    frame_df = frame_df.copy()
    conn_df = conn_df.copy()
    frame_df.columns = [str(c).strip() for c in frame_df.columns]
    conn_df.columns = [str(c).strip() for c in conn_df.columns]
    support_df = identify_cantilever_beams(frame_df, conn_df)
    support_df = support_df.merge(_support_widths(frame_df, conn_df), on="UniqueName",
                                  how="left")
    beam_df = frame_df.merge(support_df, on="UniqueName", how="left")
    if "DesignType" in beam_df.columns:
        beam_df = beam_df[beam_df["DesignType"] == "Beam"].copy()
    beam_df = beam_df.drop_duplicates(subset=["UniqueName"], keep="first")
    cols = [c for c in beam_df.columns if c != "SupportStatus"]
    if "SectProp" in cols:
        cols.insert(cols.index("SectProp") + 1, "SupportStatus")
    beam_df = beam_df[cols]
    for key in BEAM_BAR_INPUTS:
        beam_df[key] = float(bars[key])
    stories = {str(story) for story in earth_cover_stories or ()}
    if stories and "Story" in beam_df.columns:
        on_earth = beam_df["Story"].astype(str).isin(stories)
        beam_df.loc[on_earth, "cc"] = max(float(bars["cc"]),
                                          CODE.beam_detailing.earth_contact_cover)
    beam_df["_sort_key"] = beam_df["UniqueName"].apply(_beam_sort_key)
    return beam_df.sort_values("_sort_key").drop(columns="_sort_key").reset_index(drop=True)


DEFLECTION_LIMIT_OPTIONS = {
    f"Yes: L/{LIMIT_DAMAGED} after partitions are installed": LIMIT_DAMAGED,
    f"No: L/{LIMIT_NOT_DAMAGED}": LIMIT_NOT_DAMAGED,
}


def _deflection_settings_path() -> str:
    from utilities.user_settings import settings_path as user_settings_path

    return user_settings_path("beam_deflection.json")


def ask_earth_cover_stories(stories: list[str], normal_cover: float,
                            last: list[str] | None = None) -> list[str] | None:
    """Ask which floor levels have beams that need the 75 mm earth-contact cover.

    ``stories`` are the levels that have beams. Returns the chosen levels (an
    empty list for none), or None when a dialog is closed. Nothing is asked
    when the typed cover is already 75 mm or more.
    """
    from utilities._gui_helpers import DualListboxSelector, select_option

    earth = CODE.beam_detailing.earth_contact_cover
    if not stories or float(normal_cover) >= earth:
        return []
    last = [story for story in (last or []) if story in stories]
    remembered = f" (last time: {', '.join(last)})" if last else ""
    chosen = select_option(
        "Beam Design - Cover against Earth",
        f"Do the beams of any floor level need {earth:g} mm cover (cast against or exposed "
        f"to earth, such as footing tie beams and ground beams)? The other beams keep "
        f"{float(normal_cover):g} mm.{remembered}",
        [f"No - {float(normal_cover):g} mm cover on every level",
         f"Yes - choose the levels with {earth:g} mm cover"]
        + ([f"Yes - the same levels as last time: {', '.join(last)}"] if last else []),
        default_index=2 if last else 0)
    if chosen is None:
        return None
    if chosen.startswith("No"):
        return []
    if "same levels" in chosen:
        return last
    picked = DualListboxSelector(f"Levels whose beams get {earth:g} mm cover", stories).show()
    return [story for story in stories if story in set(picked)] or None


def ask_deflection_limit() -> int | None:
    """Ask the long-term deflection limit (ACI Table 24.2.2); remembered for next time."""
    import json

    from utilities._gui_helpers import select_option

    path = _deflection_settings_path()
    try:
        with open(path, encoding="utf-8") as handle:
            last = int(json.load(handle).get("long_limit_divisor", LIMIT_DAMAGED))
    except (OSError, ValueError):
        last = LIMIT_DAMAGED
    chosen = select_option(
        "Beam Design - Deflection",
        "Do the beams support partitions or finishes likely to be damaged by deflection?",
        list(DEFLECTION_LIMIT_OPTIONS),
        default_index=0 if last == LIMIT_DAMAGED else 1,
    )
    if chosen is None:
        return None
    divisor = DEFLECTION_LIMIT_OPTIONS[chosen]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"long_limit_divisor": divisor}, handle)
    return divisor


def design_beams(tables: dict, smrf: bool, gravity_combo: str | None, bars: dict,
                 long_limit_divisor: int = LIMIT_DAMAGED, progress=None,
                 earth_cover_stories=()) -> pd.DataFrame:
    """Design every beam from the extracted tables; deflection when service loads exist.

    ``tables`` holds FACTORED LOADS, FRAME DATA, CONNECTIVITY and (optional)
    SERVICE LOADS. ``earth_cover_stories`` are the levels whose beams get the
    75 mm earth-contact cover. Returns the results, two rows (TOP, BOTTOM) per beam.
    """
    beam_props = prepare_beam_table(tables["FRAME DATA"], tables["CONNECTIVITY"], bars,
                                    earth_cover_stories)
    results = execute_beam_design(
        df_beam_props=beam_props,
        df_frame_forces=tables["FACTORED LOADS"],
        enable_seismic_design=smrf,
        gravity_combo_name=gravity_combo,
        progress=progress,
    )
    service = tables.get("SERVICE LOADS")
    if service is not None and len(service) and not results.empty:
        results = add_deflection_columns(results, service, long_limit_divisor,
                                         tables["CONNECTIVITY"], progress=progress)
    return sort_beam_rows(results)


_BEAM_GROUP_FILLS = ("BDD7EE", "E2EFDA", "FFF2CC", "FCE4D6", "DEEBF7")


_ETABS_COLUMN_ONLY_FIELDS = (
    "Diameter", "ReinfConfig", "IsSpiral", "IsDesigned", "Cover", "NumBars3Dir", "NumBars2Dir",
    "NumBarsCirc", "BarSizeLong", "BarSizeCorn", "BarSizeConf", "SpacingConf", "NumCBars3",
    "NumCBars2",
)


def beam_blank_reason(name: str, row: dict) -> str:
    """Why a cell of the beam results has no value."""
    if name in ("Defl_roof_mm", "Defl_roof_limit_mm"):
        return "N/A - not a roof beam"
    if name.startswith("Defl_"):
        check = str(row.get("Deflection_Check", "")).strip()
        return f"N/A - {check}" if check and check not in ("PASS", "FAIL") else "N/A"
    if name in ("Ve_left_kN", "Ve_right_kN", "V_sway_max_kN") or name.startswith("SMRF_"):
        return "N/A - no seismic design for this beam"
    return "N/A"


def write_beam_results_xlsx(results: pd.DataFrame, path: str) -> str:
    """The beam design results as a formatted Excel file (no Excel needed)."""
    from openpyxl import Workbook
    from openpyxl.styles import Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from utilities._xlsx_values import cell_value, is_blank

    # ETABS column-section fields that come with FRAME DATA: never filled for a beam.
    unused = [c for c in _ETABS_COLUMN_ONLY_FIELDS
              if c in results.columns and results[c].map(is_blank).all()]
    results = results.drop(columns=unused)
    table = display_beam_result_labels(results)
    cols = table.columns.tolist()
    starts = [0]
    for label in ("n₍left, 1₎", "Side bars / face (governing)", "Stirrup legs",
                  "Stirrup anchorage check"):
        starts.append(cols.index(label) if label in cols else len(cols))
    book = Workbook()
    sheet = book.active
    sheet.title = "BEAM DESIGN"
    sheet.append(cols)
    internal = results.columns.tolist()
    for record, row in zip(results.to_dict("records"), table.astype(object).values.tolist()):
        sheet.append([
            cell_value(value, 3 if name in ("Defl_lambda", "Defl_ratio") else 2,
                       beam_blank_reason(name, record) if is_blank(value) else "")
            for name, value in zip(internal, row)
        ])
    thin, thick = Side(style="thin"), Side(style="medium")
    for index, name in enumerate(cols, start=1):
        group = max(g for g, start in enumerate(starts) if index - 1 >= start)
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=_BEAM_GROUP_FILLS[min(group, 4)])
        cell.border = Border(top=thick, bottom=thick)
        width = max(len(str(name)), *(len(str(v)) for v in table.iloc[:, index - 1]
                                      .head(200).tolist())) if len(table) else len(str(name))
        sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 8), 45)
    grey = PatternFill("solid", fgColor="F2F2F2")
    for first in range(2, len(table) + 2, 2):  # two rows per beam: TOP and BOTTOM
        for row in (first, first + 1):
            if row > len(table) + 1:
                continue
            for column in range(1, len(cols) + 1):
                cell = sheet.cell(row=row, column=column)
                if (first // 2) % 2 == 1:
                    cell.fill = grey
                if row == first + 1:
                    cell.border = Border(bottom=thin)
    sheet.freeze_panes = "C2"
    book.save(path)
    return path


def export_beam_dxf(results: pd.DataFrame, output_dir: str) -> list[str]:
    """One girder and one gravity beam schedule DXF per story."""
    written = []
    if results.empty:
        return written
    if "SupportStatus" in results.columns:
        gravity = results["SupportStatus"].map(is_gravity_beam)
    else:
        gravity = pd.Series(False, index=results.index)
    for story in results["Story"].unique():
        on_story = results["Story"] == story
        for label, rows in (
            ("Girder", results[on_story & ~gravity]),
            ("Beam", results[on_story & gravity]),
        ):
            if rows.empty:
                continue
            path = os.path.join(output_dir, f"{story}_{label}_Schedule.dxf")
            generate_dxf_beam_schedule(story_name=f"{story} {label.upper()}S",
                                       df_story=rows, output_filepath=path)
            written.append(path)
    return written


def generate_dxf_beam_schedule(
    story_name: str,
    df_story: pd.DataFrame,
    output_filepath: str,
    code: AciCode = CODE,
):
    """Generates a structured CAD Beam Schedule DXF table for a single story using ezdxf."""
    seismic_cfg = code.beam_seismic
    detailing_cfg = code.beam_detailing
    # 1. Initialize DXF Document with SIMPLEX Text Style
    doc = ezdxf.new(dxfversion="R2018")
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})

    msp = doc.modelspace()

    # 2. Define Table Geometry (Column Widths & Row Heights in mm based on markup)
    col_widths = [
        15.0,  # 0: Beam Mark
        15.0,  # 1: Section B
        15.0,  # 2: Section H
        6.0,  # 3: LAYER
        15.0,  # 4: Left Support Top (Half of 30)
        15.0,  # 5: Left Support Bottom
        15.0,  # 6: Mid Support Top (Half of 30)
        15.0,  # 7: Mid Support Bottom
        15.0,  # 8: Right Support Top (Half of 30)
        15.0,  # 9: Right Support Bottom
        12.0,  # 10: Web Bars (UPDATED TO 12mm)
        40.0,  # 11: Stirrups
        15.0,  # 12: Remarks
    ]

    col_x = [0.0]
    for w in col_widths:
        col_x.append(col_x[-1] + w)

    header_h = 6.0  # Height per header row (2 rows = 12mm total)
    subrow_h = 4.5  # Height per layer sub-row (3 sub-rows = 13.5mm total per beam)

    def add_cell_text(
        text,
        x_left,
        y_top,
        width,
        height,
        font_size=2.0,
        width_factor=0.75,
        style="SIMPLEX",
        rotation=0,
    ):
        """Custom text adder that safely splits \n into stacked CAD text entities."""
        lines = str(text).split("\n")
        num_lines = len(lines)

        # Calculate vertical shift to keep the multi-line block centered
        line_spacing = font_size * 1.5
        total_text_height = (num_lines - 1) * line_spacing

        cx = x_left + width / 2.0
        start_cy = (y_top - height / 2.0) + (total_text_height / 2.0)

        for idx, line_text in enumerate(lines):
            cy = start_cy - (idx * line_spacing)
            text_ent = msp.add_text(
                line_text,
                dxfattribs={
                    "style": style,
                    "height": font_size,
                    "width": width_factor,  # Applies the 0.75 width factor
                    "color": 7,
                    "rotation": rotation,
                },
            )
            # If text is rotated (like the LAYER column), swap middle-center alignment handling
            text_ent.set_placement(
                (cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER
            )

    def draw_box(x1, y1, x2, y2, lineweight=25):
        """Draw a closed rectangular polyline around a schedule cell or row."""
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        msp.add_lwpolyline(pts, dxfattribs={"color": 7, "lineweight": lineweight})

    # 3. Draw Main Table Headers
    y_curr = 0.0

    # Header Outer Box
    draw_box(col_x[0], y_curr, col_x[-1], y_curr - 2 * header_h, lineweight=35)

    # Header Column Titles & Dividers
    add_cell_text("Beam\nMark", col_x[0], y_curr, col_widths[0], 2 * header_h)
    msp.add_line(
        (col_x[1], y_curr), (col_x[1], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Section", col_x[1], y_curr, col_widths[1] + col_widths[2], header_h)
    add_cell_text("B (mm)", col_x[1], y_curr - header_h, col_widths[1], header_h)
    add_cell_text("H (mm)", col_x[2], y_curr - header_h, col_widths[2], header_h)
    msp.add_line(
        (col_x[1], y_curr - header_h),
        (col_x[3], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[2], y_curr - header_h),
        (col_x[2], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[3], y_curr), (col_x[3], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    # Rotated the LAYER text 90 degrees to fit the 6mm column
    add_cell_text("LAYER", col_x[3], y_curr, col_widths[3], 2 * header_h, rotation=90)

    msp.add_line(
        (col_x[4], y_curr), (col_x[4], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Left Support", col_x[4], y_curr, col_widths[4] + col_widths[5], header_h
    )
    add_cell_text("Top", col_x[4], y_curr - header_h, col_widths[4], header_h)
    add_cell_text("Bottom", col_x[5], y_curr - header_h, col_widths[5], header_h)
    msp.add_line(
        (col_x[4], y_curr - header_h),
        (col_x[6], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[5], y_curr - header_h),
        (col_x[5], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[6], y_curr), (col_x[6], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Mid Support", col_x[6], y_curr, col_widths[6] + col_widths[7], header_h
    )
    add_cell_text("Top", col_x[6], y_curr - header_h, col_widths[6], header_h)
    add_cell_text("Bottom", col_x[7], y_curr - header_h, col_widths[7], header_h)
    msp.add_line(
        (col_x[6], y_curr - header_h),
        (col_x[8], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[7], y_curr - header_h),
        (col_x[7], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[8], y_curr), (col_x[8], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Right Support", col_x[8], y_curr, col_widths[8] + col_widths[9], header_h
    )
    add_cell_text("Top", col_x[8], y_curr - header_h, col_widths[8], header_h)
    add_cell_text("Bottom", col_x[9], y_curr - header_h, col_widths[9], header_h)
    msp.add_line(
        (col_x[8], y_curr - header_h),
        (col_x[10], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[9], y_curr - header_h),
        (col_x[9], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[10], y_curr), (col_x[10], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Web\nBars", col_x[10], y_curr, col_widths[10], 2 * header_h)
    msp.add_line(
        (col_x[11], y_curr), (col_x[11], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Stirrups", col_x[11], y_curr, col_widths[11], 2 * header_h)
    msp.add_line(
        (col_x[12], y_curr), (col_x[12], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Remarks", col_x[12], y_curr, col_widths[12], 2 * header_h)

    y_curr -= 2 * header_h

    # 4. Populate Data Rows per Beam
    if not df_story.empty:
        unique_beams = df_story["UniqueName"].unique()

        for u_name in unique_beams:
            df_beam = df_story[df_story["UniqueName"] == u_name]
            top_row = df_beam[df_beam["Face"] == "TOP"].iloc[0]
            bot_row = df_beam[df_beam["Face"] == "BOTTOM"].iloc[0]

            beam_h = 3 * subrow_h
            y_beam_start = y_curr

            add_cell_text(u_name, col_x[0], y_beam_start, col_widths[0], beam_h)

            b_val = int(top_row.get("Width", top_row.get("b", 300)))
            h_val = int(top_row.get("Depth", top_row.get("Height", 500)))
            add_cell_text(b_val, col_x[1], y_beam_start, col_widths[1], beam_h)
            add_cell_text(h_val, col_x[2], y_beam_start, col_widths[2], beam_h)

            n_side = int(top_row.get("n_side_per_face_gov", 0))
            dw = int(top_row.get("dw", top_row.get("dm", 12)))
            web_bars_str = f"{2 * n_side} - {dw}Ø" if n_side > 0 else "-"
            add_cell_text(web_bars_str, col_x[10], y_beam_start, col_widths[10], beam_h)

            # Stirrups Detailing
            ds = int(top_row.get("ds", top_row.get("d_stirrup", 10)))
            sp_2h = float(top_row.get("Spacing_2H", 100))
            sp_mid = float(top_row.get("Spacing_Mid", 200))
            first_offset = detailing_cfg.first_stirrup_offset
            two_h = seismic_cfg.hoop_zone_depth_factor * h_val
            n_2h = int(two_h / sp_2h) if sp_2h > 0 else 0

            # Use \n to split the long string onto two lines
            stirrups_str = (
                f"{ds}Ø - 1@{first_offset:g}mm, {n_2h}@{int(sp_2h)}mm;"
                f"\nREST @{int(sp_mid)}mm o.c."

            )
            add_cell_text(stirrups_str, col_x[11], y_beam_start, col_widths[11], beam_h)

            legs = int(top_row.get("Stirrup_Legs", 2))
            type_num = max(1, legs - 1)
            remarks_str = f"TYPE - {type_num}"
            add_cell_text(remarks_str, col_x[12], y_beam_start, col_widths[12], beam_h)

            dm = int(top_row.get("dm", top_row.get("d_main", 25)))

            for layer_idx in [1, 2, 3]:
                y_sub_top = y_beam_start - (layer_idx - 1) * subrow_h

                add_cell_text(layer_idx, col_x[3], y_sub_top, col_widths[3], subrow_h)

                n_top_left = int(top_row.get(f"n_left_L{layer_idx}", 0))
                n_bot_left = int(bot_row.get(f"n_left_L{layer_idx}", 0))

                n_top_mid = int(top_row.get(f"n_mid_L{layer_idx}", 0))
                n_bot_mid = int(bot_row.get(f"n_mid_L{layer_idx}", 0))

                n_top_right = int(top_row.get(f"n_right_L{layer_idx}", 0))
                n_bot_right = int(bot_row.get(f"n_right_L{layer_idx}", 0))

                str_top_left = f"{n_top_left} - {dm}Ø" if n_top_left > 0 else "-"
                str_bot_left = f"{n_bot_left} - {dm}Ø" if n_bot_left > 0 else "-"

                str_top_mid = f"{n_top_mid} - {dm}Ø" if n_top_mid > 0 else "-"
                str_bot_mid = f"{n_bot_mid} - {dm}Ø" if n_bot_mid > 0 else "-"

                str_top_right = f"{n_top_right} - {dm}Ø" if n_top_right > 0 else "-"
                str_bot_right = f"{n_bot_right} - {dm}Ø" if n_bot_right > 0 else "-"

                add_cell_text(
                    str_top_left, col_x[4], y_sub_top, col_widths[4], subrow_h
                )
                add_cell_text(
                    str_bot_left, col_x[5], y_sub_top, col_widths[5], subrow_h
                )
                add_cell_text(str_top_mid, col_x[6], y_sub_top, col_widths[6], subrow_h)
                add_cell_text(str_bot_mid, col_x[7], y_sub_top, col_widths[7], subrow_h)
                add_cell_text(
                    str_top_right, col_x[8], y_sub_top, col_widths[8], subrow_h
                )
                add_cell_text(
                    str_bot_right, col_x[9], y_sub_top, col_widths[9], subrow_h
                )

                if layer_idx < 3:
                    y_line = y_sub_top - subrow_h
                    msp.add_line(
                        (col_x[3], y_line),
                        (col_x[10], y_line),
                        dxfattribs={"color": 7},
                    )

            y_beam_end = y_beam_start - beam_h
            draw_box(col_x[0], y_beam_start, col_x[-1], y_beam_end, lineweight=25)

            for idx in range(1, len(col_x) - 1):
                msp.add_line(
                    (col_x[idx], y_beam_start),
                    (col_x[idx], y_beam_end),
                    dxfattribs={"color": 7},
                )

            y_curr = y_beam_end

    doc.saveas(output_filepath)


# =============================================================================
# CALCULATION REPORT (PDF)
# =============================================================================
_BEAM_LOCATIONS = (("left", "Left support"), ("mid", "Midspan"), ("right", "Right support"))


def _beam_deflection_table(top: pd.Series) -> ReportTable | None:
    """The deflection checks of one beam (ACI 318M-14 24.2), from its result row.

    None when the results were made without the deflection check. A beam with
    no deflection data gets a table that says why.
    """
    if "Deflection_Check" not in top.index:
        return None
    check = str(top.get("Deflection_Check"))
    if is_blank(top.get("Defl_ratio")):
        return ReportTable(
            "Deflection (ACI 24.2)", ["Parameter", "Value"],
            [["Deflection check", check if check not in ("None", "nan", "") else
              "Not checked - no service moments for this beam"]], "lr")

    def pair(value, limit, missing: str) -> list:
        if is_blank(value):
            return [missing, "--", "--"]
        ratio = (float(value) / float(limit)) if not is_blank(limit) and float(limit) > 0 else None
        return [number(value), number(limit), number(ratio) if ratio is not None else "--"]

    body = [
        ["Immediate, live load (L/360)",
         *pair(top.get("Defl_live_mm"), top.get("Defl_live_limit_mm"), "N/A")],
        ["Immediate, roof live load (L/180)",
         *pair(top.get("Defl_roof_mm"), top.get("Defl_roof_limit_mm"), "N/A - not a roof beam")],
        ["After partitions are installed (long term)",
         *pair(top.get("Defl_long_mm"), top.get("Defl_long_limit_mm"), "N/A")],
    ]
    return ReportTable(
        "Deflection (ACI 24.2)",
        ["Check", Tex(r"$\Delta$ (mm)"), "Limit (mm)", Tex(r"$\Delta$ / limit")],
        body, "lrrr",
        "Effective moment of inertia Ie (mm4): left "
        f"{number(top.get('Defl_Ie_left'), 0)}, midspan {number(top.get('Defl_Ie_mid'), 0)}, "
        f"right {number(top.get('Defl_Ie_right'), 0)}. Long-term factor "
        f"{number(top.get('Defl_lambda'), 3)}. Governing ratio "
        f"{number(top.get('Defl_ratio'), 3)}. The segments of one beam line between supports "
        f"are checked as one span. Deflection check: {check}.",
    )


def _beam_calc_member(
    top: pd.Series, bottom: pd.Series, seismic: bool, code: AciCode = CODE
) -> MemberReport:
    """Build the report tables of one beam from its TOP and BOTTOM result rows.

    The bar counts, stirrup legs and spacings are the designed ones. Depths,
    strengths and ratios are worked out again from them with the design engines.
    """
    name = str(top["UniqueName"])
    width, height = float(top["Width"]), float(top["Depth"])
    fc, fy = float(top["f'c"]), float(top["fy"])
    fyt = float(top.get("fyw", fy))
    d_main, d_stirrup, cover = float(top["dm"]), float(top["ds"]), float(top["cc"])
    span = float(top["ClearSpan_Ln"])
    is_cantilever = "Cantilever" in str(top.get("SupportStatus", ""))
    seismic = seismic and not is_gravity_beam(top.get("SupportStatus", ""))

    def bars(row: pd.Series, location: str) -> list[int]:
        return [int(float(row.get(f"n_{location}_L{layer}") or 0)) for layer in (1, 2, 3)]

    engines = {}
    for location, _ in _BEAM_LOCATIONS:
        engine = BeamFlexureDesign(
            width, height, fc, fy, fyt, d_main, d_stirrup, cover, code=code
        )
        engine.n_top = sum(bars(top, location))
        engine.n_bot = sum(bars(bottom, location))
        engines[location] = engine

    section = ReportTable(
        "Section and materials",
        ["Parameter", "Value", "Parameter", "Value"],
        [
            ["Section", top.get("SectProp"), "Support condition", top.get("SupportStatus")],
            [Tex("Width, $b$ (mm)"), number(width, 0), Tex("Depth, $h$ (mm)"), number(height, 0)],
            [Tex("Clear span, $L_n$ (mm)"), number(span, 0), Tex("Cover (mm)"), number(cover, 0)],
            [Tex(r"$f'_c$ (MPa)"), number(fc), Tex("$f_y$ (MPa)"), number(fy)],
            [Tex("$f_{yt}$ (MPa)"), number(fyt), Tex("Main bar (mm)"), number(d_main, 0)],
            ["Stirrup bar (mm)", number(d_stirrup, 0), "Web bar (mm)", number(top.get("dw"), 0)],
        ],
        "llll",
    )

    flexure_rows = []
    flexure_passes = True
    for location, label in _BEAM_LOCATIONS:
        for face, row, negative in (("Top", top, True), ("Bottom", bottom, False)):
            layers = bars(row, location)
            # Layer 1 is the topmost; a bottom face fills from layer 3 upward.
            layout = "+".join(str(n) for n in (layers if negative else layers[::-1]) if n)
            demand = float(row.get(f"Mu_{location}") or 0.0)
            engine = engines[location]
            try:
                result = engine.solve_moment_capacity(is_negative_moment=negative)
                depth = engine.compute_effective_depths(negative)[0]
                ratio = demand / result["phi_Mn"] if result["phi_Mn"] > 0 else float("nan")
                passed = demand <= result["phi_Mn"] + 1e-6
                flexure_passes = flexure_passes and passed
                flexure_rows.append([
                    label, face, number(demand), layout or "0",
                    number(result["As_provided"], 0), number(depth, 1),
                    number(result["a"], 1), number(result["et"], 4),
                    number(result["phi"], 2), number(result["Mn"]),
                    number(result["phi_Mn"]), number(ratio), "PASS" if passed else "FAIL",
                ])
            except (ValueError, ZeroDivisionError):
                flexure_rows.append(
                    [label, face, number(demand), layout or "0"] + ["--"] * 8
                    + ["PASS" if demand <= 1e-6 else "FAIL"]
                )
    flexure = ReportTable(
        "Flexure",
        ["Location", "Face", Tex("$M_u$"), "Bars", Tex("$A_s$"), Tex("$d$"), Tex("$a$"),
         Tex(r"$\varepsilon_t$"), Tex(r"$\phi$"), Tex("$M_n$"), Tex(r"$\phi M_n$"),
         Tex(r"$M_u/\phi M_n$"), "Check"],
        flexure_rows,
        "ll" + "r" * 10 + "l",
        "Moments in kN-m, areas in mm2, depths in mm. Bars are listed per layer, from the face inward.",
    )

    legs = int(float(top.get("Stirrup_Legs") or 0))
    stirrup_area = legs * math.pi * d_stirrup**2 / 4.0
    fyt_shear = min(fyt, code.material.max_fyt_shear)
    phi_shear = code.strength.shear
    shear_rows = []
    for location, label, v_key, t_key, s_key in (
        ("left", "Left support", "Vu_left", "Tu_left", "Spacing_2H"),
        ("mid", "Beyond 2h", "Vu_mid_2h", "Tu_mid_2h", "Spacing_Mid"),
        ("right", "Right support", "Vu_right", "Tu_right", "Spacing_2H"),
    ):
        engine = engines[location]
        depth = min(
            engine.compute_effective_depths(True)[0],
            engine.compute_effective_depths(False)[0],
        )
        analysis_shear = float(top.get(v_key) or 0.0)
        capacity_shear = (
            float(top.get(f"Ve_{location}_kN") or 0.0)
            if seismic and location != "mid"
            else float("nan")
        )
        design_shear = (
            analysis_shear if math.isnan(capacity_shear)
            else max(analysis_shear, capacity_shear)
        )
        torsion_demand = float(top.get(t_key) or 0.0)
        suppressed = location != "mid" and top.get(f"Vc_zero_{location}") in (True, "True", 1)
        spacing = float(top.get(s_key) or 0.0)
        shear = BeamShearDesign(
            width, height, depth, fc, fyt, d_stirrup, legs, suppress_Vc=suppressed, code=code
        )
        shear_result = shear.solve_shear_capacity(design_shear)
        torsion_result = BeamTorsionDesign(
            width, height, depth, fc, fy, fyt, d_stirrup, cover, code=code
        ).solve_torsion_capacity(torsion_demand, design_shear)
        steel_shear = (
            stirrup_area * fyt_shear * depth / spacing / 1000.0 if spacing > 0 else 0.0
        )
        steel_shear_limit = (
            shear_result["max_allowable_phi_Vn"] / phi_shear - shear_result["Vc"]
        )
        capacity = phi_shear * (shear_result["Vc"] + min(steel_shear, steel_shear_limit))
        ratio = design_shear / capacity if capacity > 0 else float("nan")
        shear_rows.append([
            label, number(analysis_shear), number(capacity_shear), number(design_shear),
            number(torsion_demand), number(depth, 1), number(shear_result["Vc"]),
            legs, number(spacing, 0), number(steel_shear), number(capacity), number(ratio),
            number(torsion_result["At_s_demand"], 3), number(torsion_result["Al_design"], 0),
            "PASS" if design_shear <= capacity + 1e-6 else "FAIL",
        ])
    shear = ReportTable(
        "Shear and torsion",
        ["Zone", Tex("$V_u$"), Tex("$V_e$"), Tex("Design $V_u$"), Tex("$T_u$"), Tex("$d$"),
         Tex("$V_c$"), "Legs", Tex("$s$"), Tex("$V_s$"), Tex(r"$\phi V_n$"),
         Tex(r"$V_u/\phi V_n$"), Tex("$A_t/s$"), Tex("$A_l$"), "Check"],
        shear_rows,
        "l" + "r" * 13 + "l",
        "Forces in kN, torsion in kN-m, lengths in mm, At/s in mm2/mm, Al in mm2. "
        "Vu and Tu are the envelopes of all combinations. Vc is taken as zero where the "
        "seismic rule requires it. The stirrups also carry the torsion steel At/s.",
    )

    tables = [section, flexure, shear]
    if seismic and not is_cantilever:
        try:
            checker = BeamSeismicDesign(
                {"Left Support Face": engines["left"], "Midspan Zone": engines["mid"],
                 "Right Support Face": engines["right"]},
                span, code=code,
            )
            strengths = checker.check_flexural_capacity_ratios()
            seismic_rows = [
                [location, number(values["Mn_neg"]), number(values["Mn_pos"]),
                 number(values["Mn_pos"] / values["Mn_neg"]) if values["Mn_neg"] else "--"]
                for location, values in strengths["locations"].items()
            ]
            tables.append(ReportTable(
                "Seismic moment strengths (SMRF)",
                ["Location", Tex("$M_n^-$ (kN-m)"), Tex("$M_n^+$ (kN-m)"), Tex("$M_n^+/M_n^-$")],
                seismic_rows,
                "lrrr",
            ))
        except (ValueError, KeyError, ZeroDivisionError):
            pass
    if seismic:
        tables.append(ReportTable(
            "Seismic design shear (SMRF)",
            ["Parameter", "Value"],
            [
                [Tex("Sway shear from probable moments, $V_{sway}$ (kN)"), number(top.get("V_sway_max_kN"))],
                [Tex("Design shear, left, $V_e$ (kN)"), number(top.get("Ve_left_kN"))],
                [Tex("Design shear, right, $V_e$ (kN)"), number(top.get("Ve_right_kN"))],
                ["Concrete shear taken as zero, left", top.get("Vc_zero_left") in (True, "True", 1)],
                ["Concrete shear taken as zero, right", top.get("Vc_zero_right") in (True, "True", 1)],
                ["Moment strength ratio check (ACI 18.6.3.2)", top.get("SMRF_Flexure_Ratio_Check")],
                ["Steel ratio check (ACI 18.6.3.1)", top.get("SMRF_Rho_Check")],
            ],
            "lr",
        ))

    deflection = _beam_deflection_table(top)
    if deflection is not None:
        tables.append(deflection)

    status = str(top.get("Design_Status"))
    tables.append(ReportTable(
        "Detailing and status",
        ["Parameter", "Value"],
        [
            ["Stirrup legs", legs],
            ["Stirrup spacing within 2h of supports (mm)", number(top.get("Spacing_2H"), 0)],
            ["Stirrup spacing elsewhere (mm)", number(top.get("Spacing_Mid"), 0)],
            ["Side bars per face", number(top.get("n_side_per_face_gov"), 0)],
            ["Stirrup anchorage check", top.get("Anchorage_Check")],
            ["Alternating tie check", top.get("Alternating_Tie_Check")],
            ["Design status", status],
        ],
        "lr",
    ))

    return MemberReport(
        heading=f"Beam {name} ({top.get('Story')})",
        summary=[name, top.get("Story"), top.get("SectProp"),
                 "PASS" if flexure_passes else "FAIL", status],
        tables=tables,
    )


def build_beam_calc_report(
    df_results: pd.DataFrame, filepath: str, seismic: bool, gravity_combo: object = None
) -> str | None:
    """Write the beam calculation PDF from the BEAM DESIGN result table."""
    members = []
    for _, rows in df_results.groupby("UniqueName", sort=False):
        top = rows[rows["Face"] == "TOP"]
        bottom = rows[rows["Face"] == "BOTTOM"]
        if top.empty or bottom.empty:
            continue
        members.append(_beam_calc_member(top.iloc[0], bottom.iloc[0], seismic))
    if not members:
        raise ValueError("BEAM DESIGN contains no designed beams to report.")
    information = [
        ("Design code", "ACI 318M-14"),
        ("Seismic design (SMRF)", "Yes" if seismic else "No"),
        ("Factored gravity combination", gravity_combo if seismic else "Not used"),
        ("Design forces", "Envelope of all extracted load combinations"),
        ("Beams reported", len(members)),
    ]
    return build_calc_report(
        "Concrete Beam Design Calculations (ACI 318M-14)",
        information,
        ["Beam", "Story", "Section", "Flexure", "Design status"],
        members,
        filepath,
        "p{3.2cm}p{1.3cm}p{5.2cm}p{1.6cm}p{5.5cm}",
    )


def export_beam_pdf(results: pd.DataFrame, path: str, seismic: bool,
                    gravity_combo: str | None) -> str | None:
    """The beam calculation report (PDF); None when LaTeX fails."""
    return build_beam_calc_report(results, path, seismic=seismic, gravity_combo=gravity_combo)


if __name__ == "__main__":
    from design.concrete_workflow import run_beams

    run_beams()
