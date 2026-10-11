"""Beam section design: flexure, shear, torsion and the SMRF checks of one beam (ACI 318M-14)."""

import math
from dataclasses import dataclass
from typing import Dict, List
from design.criteria.code_config import CODE, AciCode


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
        ``code`` holds every ACI constant (see criteria/code_config.py).
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
        """The fewest bars on a face that keep the bar spacing within its limit.

        With the office rule (``limit_clear_spacing``) the clear spacing is at
        most 150 mm; without it the limit is the crack-control spacing of ACI
        24.3.2, centre to centre.
        """
        clear_center_width = self.width - 2 * (
            self.cc + self.dstirrup + (self.dmain / 2.0)
        )
        detailing = self.code.beam_detailing
        if detailing.limit_clear_spacing:
            # Convert the clear-spacing target (150 mm) to a center-to-center spacing limit.
            max_center_spacing = detailing.max_clear_spacing_target + self.dmain
        else:
            max_center_spacing = self.code.crack_control_spacing(
                self.fy, self.cc + self.dstirrup)
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
