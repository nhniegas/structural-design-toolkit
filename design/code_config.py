"""Structural code constants: ACI 318M-14, NSCP 2015, ASCE 7-10 and UBC 97.

* ``CODE`` (``AciCode``): ACI 318M-14, used by the beam and column designers
  (NSCP 2015 chapter 4 follows it: ACI clause X is NSCP clause 4X).
* ``NSCP`` (``NscpCode``): NSCP 2015 chapter 2, the loads, load combinations,
  live load reduction (with the ASCE 7-10 alternate) and earthquake provisions
  (with the UBC 97 tables that ETABS uses, which NSCP 208 follows).

WHY A PYTHON MODULE (instead of JSON / YAML / Excel)?
    * Every value carries the code clause it comes from, right next to the number.
    * Values are typed and auto-completed in VS Code (``CODE.strength.phi_shear``).
    * Values are immutable (``frozen=True``): a design run cannot silently change
      a code factor half-way through.
    * A few values are *rules*, not numbers (beta1, phi versus strain). They live
      here as small methods so the beam and column scripts can never disagree.
    * A different code edition or a company standard is one ``override(...)`` away
      (see the bottom of this file) with no edits to the design scripts.

WHAT DOES NOT BELONG HERE
    Project inputs that change from job to job (bar sizes, cover, fc', fy, SMRF
    toggle, seismic zone, soil) are asked by the commands or come from the ETABS
    model. Office conventions (pattern names, mass fractions) stay in the
    workflow that uses them.

UNITS: N, mm, MPa unless a field name says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


# =============================================================================
# MATERIAL MODEL
# =============================================================================
@dataclass(frozen=True)
class MaterialConfig:
    """Stress-strain assumptions and material-model constants."""

    steel_elastic_modulus: float = 200_000.0  # MPa           ACI 20.2.2.2
    concrete_ultimate_strain: float = 0.003  #               ACI 22.2.2.1
    stress_block_alpha: float = 0.85  # 0.85 fc' block        ACI 22.2.2.4.1
    beta1_max: float = 0.85  #                                ACI Table 22.2.2.4.3
    beta1_min: float = 0.65  #                                ACI Table 22.2.2.4.3
    beta1_fc_start: float = 28.0  # MPa, beta1 starts to fall ACI Table 22.2.2.4.3
    beta1_drop_per_mpa: float = 0.05 / 7.0  #                 ACI Table 22.2.2.4.3
    concrete_modulus_coeff: float = 4700.0  # Ec = 4700*sqrt(fc') ACI 19.2.2.1(b)
    modulus_of_rupture_coeff: float = 0.62  # fr = 0.62*sqrt(fc') ACI 19.2.3.1
    concrete_density: float = 2400.0  # kg/m3, model input only
    steel_density: float = 7850.0  # kg/m3, model input only
    steel_fracture_strain: float = 0.05  # model input only (not a code value)
    max_fyt_shear: float = 420.0  # MPa, cap on fyt in shear design  ACI 20.2.2.4
    max_fyt_confinement: float = 700.0  # MPa, cap in confinement   ACI 20.2.2.4

    def beta1(self, fc: float) -> float:
        """Whitney block depth factor beta1 for a given fc' (MPa)."""
        raw = self.beta1_max - self.beta1_drop_per_mpa * max(0.0, fc - self.beta1_fc_start)
        return max(self.beta1_min, min(self.beta1_max, raw))


# =============================================================================
# STRENGTH REDUCTION FACTORS  (ACI Table 21.2.1 / 21.2.2)
# =============================================================================
@dataclass(frozen=True)
class StrengthReductionConfig:
    """Strength reduction factors (phi)."""

    tension_controlled: float = 0.90
    compression_tied: float = 0.65
    compression_spiral: float = 0.75
    shear: float = 0.75
    torsion: float = 0.75
    joint_shear: float = 0.85  # ACI 21.2.4.1
    tension_controlled_strain: float = 0.005  # ACI 21.2.2 (Grade 420 reinforcement)

    def phi_flexure(
        self, net_tensile_strain: float, fy: float, es: float, spiral: bool = False
    ) -> float:
        """phi for flexure / combined P-M from the extreme tension strain.

        Compression-controlled up to the yield strain, tension-controlled from
        0.005, straight-line transition in between (ACI Table 21.2.2).
        """
        low = self.compression_spiral if spiral else self.compression_tied
        yield_strain = fy / es
        if net_tensile_strain >= self.tension_controlled_strain:
            return self.tension_controlled
        if net_tensile_strain <= yield_strain:
            return low
        fraction = (net_tensile_strain - yield_strain) / (
            self.tension_controlled_strain - yield_strain
        )
        return low + (self.tension_controlled - low) * fraction


# =============================================================================
# BEAM: FLEXURE, SHEAR, TORSION  (ACI chapters 9, 22, 24)
# =============================================================================
@dataclass(frozen=True)
class BeamFlexureConfig:
    """Minimum steel and crack-control limits for beams."""

    as_min_coeff_sqrt_fc: float = 0.25  # As,min = 0.25*sqrt(fc')/fy*bw*d   ACI 9.6.1.2(a)
    as_min_coeff_fy: float = 1.4  # As,min = 1.4/fy*bw*d                   ACI 9.6.1.2(b)
    crack_service_stress_ratio: float = 2.0 / 3.0  # fs = 2/3 fy             ACI 24.3.2.1
    crack_spacing_coeff_1: float = 380.0  # s = 380(280/fs) - 2.5cc          ACI Table 24.3.2
    crack_spacing_coeff_2: float = 300.0  # s <= 300(280/fs)                 ACI Table 24.3.2
    crack_reference_stress: float = 280.0  # MPa                             ACI Table 24.3.2
    crack_cover_multiplier: float = 2.5  #                                   ACI Table 24.3.2
    skin_reinforcement_depth: float = 900.0  # mm, h above which skin bars   ACI 9.7.2.3
    skin_max_spacing: float = 300.0  # mm, upper cap for skin bar spacing   (practice)


@dataclass(frozen=True)
class BeamShearConfig:
    """Shear strength and stirrup spacing limits for beams."""

    vc_coeff: float = 0.17  # Vc = 0.17*sqrt(fc')*bw*d                        ACI 22.5.5.1
    vs_max_coeff: float = 0.66  # Vs <= 0.66*sqrt(fc')*bw*d                   ACI 22.5.1.2
    vs_spacing_threshold_coeff: float = 0.33  # Vs vs 0.33*sqrt(fc')*bw*d      ACI 9.7.6.2.2
    s_max_d_fraction_low: float = 0.5  # s <= d/2 when Vs is small             ACI 9.7.6.2.2
    s_max_abs_low: float = 600.0  # mm                                         ACI 9.7.6.2.2
    s_max_d_fraction_high: float = 0.25  # s <= d/4 when Vs is large           ACI 9.7.6.2.2
    s_max_abs_high: float = 300.0  # mm                                        ACI 9.7.6.2.2
    av_min_coeff_sqrt_fc: float = 0.062  # Av,min/s = 0.062*sqrt(fc')*bw/fyt   ACI 9.6.3.3
    av_min_coeff_fyt: float = 0.35  # Av,min/s = 0.35*bw/fyt                   ACI 9.6.3.3


@dataclass(frozen=True)
class BeamTorsionConfig:
    """Torsion thresholds, capacity and detailing limits (ACI 22.7, 9.6.4, 9.7.5)."""

    threshold_coeff: float = 0.083  # Tth = 0.083*sqrt(fc')*Acp^2/pcp          ACI Table 22.7.4.1(a)
    cracking_coeff: float = 0.33  # Tcr = 0.33*sqrt(fc')*Acp^2/pcp             ACI Table 22.7.5.1(a)
    ao_over_aoh: float = 0.85  # Ao = 0.85*Aoh                                 ACI 22.7.6.1.1
    stress_limit_denominator: float = 1.7  # Tu*ph/(1.7*Aoh^2)                 ACI 22.7.7.1
    truss_angle_degrees: float = 45.0  # theta for non-prestressed members     ACI 22.7.6.1.2
    al_min_coeff: float = 0.42  # Al,min = 0.42*sqrt(fc')*Acp/fy - ...         ACI 9.6.4.3
    at_s_min_coeff: float = 0.175  # At/s >= 0.175*bw/fyt                      ACI 9.6.4.3
    s_max_ph_divisor: float = 8.0  # s <= ph/8                                 ACI 9.7.6.3.3
    s_max_abs: float = 300.0  # mm                                             ACI 9.7.6.3.3
    # ACI 22.7.3.2 lets Tu drop to phi*Tcr only for compatibility torsion in
    # statically indeterminate members. Equilibrium torsion may NOT be reduced,
    # so the safe default is False. Switch on only if you know it is compatibility.
    allow_redistribution: bool = False


@dataclass(frozen=True)
class BeamDetailingConfig:
    """Bar spacing and layout rules used when placing beam reinforcement."""

    min_clear_spacing: float = 25.0  # mm                                      ACI 25.2.1
    aggregate_spacing_factor: float = 4.0 / 3.0  # 4/3 * aggregate size        ACI 25.2.1
    default_aggregate_size: float = 25.0  # mm, project assumption
    max_clear_spacing_target: float = 150.0  # mm, project rule for bar count
    layer_clear_spacing: float = 25.0  # mm, between layers                    ACI 25.2.2
    max_layers: int = 3  # project rule before flagging congestion
    earth_contact_cover: float = 75.0  # mm, cast against and exposed to earth  ACI Table 20.6.1.3.1
    min_bars_per_face: int = 2  # two continuous bars                          ACI 9.7.2 / 18.6.3.1
    min_stirrup_legs: int = 2
    stirrup_spacing_step: float = 25.0  # mm, spacing rounded down to this
    min_practical_spacing: float = 100.0  # mm, add legs below this
    min_acceptable_spacing: float = 75.0  # mm, flag design below this
    max_spacing_default: float = 600.0  # mm, starting cap before code limits
    first_stirrup_offset: float = 50.0  # mm from support face                  ACI 18.6.4.4
    moment_zone_fraction: float = 0.25  # end zones of the span used for support moments (project rule)


@dataclass(frozen=True)
class BeamSeismicConfig:
    """Special moment frame beam requirements (ACI 18.6)."""

    max_reinforcement_ratio: float = 0.025  #                                 ACI 18.6.3.1
    positive_to_negative_at_face: float = 0.50  #                             ACI 18.6.3.2
    min_moment_at_any_section: float = 0.25  #                                ACI 18.6.3.2
    probable_stress_factor: float = 1.25  # fs = 1.25 fy for Mpr               ACI 18.6.5.1
    hoop_zone_depth_factor: float = 2.0  # hoops within 2h of the support      ACI 18.6.4.1
    hoop_spacing_d_fraction: float = 0.25  # s <= d/4                          ACI 18.6.4.4
    hoop_spacing_bar_multiple: float = 6.0  # s <= 6*db of smallest main bar   ACI 18.6.4.4
    hoop_spacing_abs: float = 150.0  # mm                                      ACI 18.6.4.4
    vc_zero_shear_fraction: float = 0.5  # earthquake shear >= 1/2 max         ACI 18.6.5.2
    vc_zero_axial_divisor: float = 20.0  # Pu < Ag*fc'/20                      ACI 18.6.5.2
    min_width: float = 250.0  # mm, bw >= smaller of 0.3h and 250 mm           ACI 18.6.2.1(b)
    min_width_to_depth: float = 0.3  #                                         ACI 18.6.2.1(b)
    min_clear_span_to_depth: float = 4.0  # ln >= 4d                           ACI 18.6.2.1(a)


@dataclass(frozen=True)
class SeismicMaterialConfig:
    """Materials of special moment frames and special structural walls."""

    min_fc: float = 21.0  # MPa                                     ACI 18.2.5.1, Table 19.2.1.1
    max_fy: float = 420.0  # MPa, longitudinal bars                ACI 18.2.6.1, 20.2.2.5


# =============================================================================
# COLUMN  (ACI chapters 10, 18, 22, 25)
# =============================================================================
@dataclass(frozen=True)
class ColumnStrengthConfig:
    """Axial-flexure limits and reinforcement ratios for columns."""

    earth_contact_cover: float = 75.0  # mm, cast against earth            ACI 20.6.1.3.1

    rho_min: float = 0.01  #                                                  ACI 10.6.1.1
    rho_max: float = 0.08  #                                                  ACI 10.6.1.1
    rho_max_smrf: float = 0.06  #                                             ACI 18.7.4.1
    pn_max_factor_tied: float = 0.80  #                                       ACI Table 22.4.2.1
    pn_max_factor_spiral: float = 0.85  #                                     ACI Table 22.4.2.1
    axial_compression_steel_stress: float = 0.85  # Po = 0.85fc'(Ag-Ast)+fy*Ast  ACI 22.4.2.2
    min_bars_tied: int = 4  #                                                 ACI 10.7.3.1
    min_bars_circular: int = 6  # spiral / SMRF circular                      ACI 10.7.3.1
    max_bundle_size: int = 4  #                                               ACI 25.6.1.1
    default_aggregate_size: float = 20.0  # mm, project assumption
    max_longitudinal_spacing: float = 150.0  # mm, project rule (ACI 18.7.5.2 allows hx<=350)
    # Clear spacing of longitudinal column bars: at least 40 mm, 1.5 db and 4/3 of the
    # aggregate (a bundle counts as one bar of the same area)   NSCP 425.2.3, ACI 25.2.3
    min_clear_spacing: float = 40.0  # mm
    clear_spacing_bar_multiple: float = 1.5


@dataclass(frozen=True)
class ColumnTransverseConfig:
    """Tie, hoop and spiral limits (ACI 25.7.2, 25.7.3, 18.7.5)."""

    tie_spacing_bar_multiple: float = 16.0  # s <= 16 db                         ACI 25.7.2.1
    tie_spacing_tie_multiple: float = 48.0  # s <= 48 dtie                       ACI 25.7.2.1
    # NSCP 425.7.2.2 states the metric sizes: 10 mm ties for 32 mm bars or smaller,
    # 12 mm for 36 mm bars or larger and for bundled bars (ACI: No. 10 and No. 13)
    tie_diameter_small_bars: float = 10.0  # mm                                  NSCP 425.7.2.2
    tie_diameter_large_bars: float = 12.0  # mm, bars > 32 mm or bundled         NSCP 425.7.2.2
    large_bar_threshold: float = 32.0  # mm                                      NSCP 425.7.2.2
    spiral_diameter_min: float = 10.0  # mm                                      NSCP 425.7.3.2
    spiral_clear_spacing_min: float = 25.0  # mm                                 ACI 25.7.3.1
    spiral_clear_spacing_max: float = 75.0  # mm                                 ACI 25.7.3.1
    tie_spacing_abs_max: float = 300.0  # mm, upper cap on reported tie spacing (project rule)


@dataclass(frozen=True)
class ColumnSeismicConfig:
    """Special moment frame column and joint requirements (ACI 18.7, 18.8)."""

    min_dimension: float = 300.0  # mm                                           ACI 18.7.2.1(a)
    min_aspect_ratio: float = 0.40  #                                            ACI 18.7.2.1(b)
    strong_column_ratio: float = 1.2  # sum Mnc >= 6/5 sum Mnb                   ACI 18.7.3.2
    # Confinement expressions, Table 18.7.5.4
    rect_coeff_a: float = 0.3  # 0.3*(Ag/Ach-1)*fc'/fyt
    rect_coeff_b: float = 0.09  # 0.09*fc'/fyt
    rect_coeff_c: float = 0.2  # 0.2*kf*kn*Pu/(fyt*Ach)
    spiral_coeff_d: float = 0.45  # 0.45*(Ag/Ach-1)*fc'/fyt
    spiral_coeff_e: float = 0.12  # 0.12*fc'/fyt
    spiral_coeff_f: float = 0.35  # 0.35*kf*Pu/(fyt*Ach)
    kf_fc_divisor: float = 175.0  # kf = fc'/175 + 0.6 >= 1.0                    ACI 18.7.5.4
    kf_offset: float = 0.6
    high_axial_fraction: float = 0.3  # Pu > 0.3*Ag*fc'                          ACI Table 18.7.5.4
    high_strength_fc: float = 70.0  # MPa                                        ACI Table 18.7.5.4
    hx_max: float = 350.0  # mm crosstie spacing                                 ACI 18.7.5.2
    hx_max_high_axial: float = 200.0  # mm                                       ACI 18.7.5.2
    hx_assumed: float = 150.0  # mm, matches max_longitudinal_spacing
    so_min: float = 100.0  # mm                                                  ACI 18.7.5.3
    so_max: float = 150.0  # mm                                                  ACI 18.7.5.3
    so_hx_divisor: float = 3.0  # so = 100 + (350 - hx)/3                        ACI 18.7.5.3
    so_hx_reference: float = 350.0  # mm
    spacing_dimension_fraction: float = 0.25  # s <= 1/4 least dimension         ACI 18.7.5.3
    spacing_bar_multiple: float = 6.0  # s <= 6 db                               ACI 18.7.5.3
    spiral_pitch_offset: float = 75.0  # mm, pitch <= 75 + dtie
    probable_stress_factor: float = 1.25  #                                      ACI 18.7.6.1.1
    joint_shear_coeff_4_faces: float = 1.7  #                                    ACI Table 18.8.4.1
    joint_shear_coeff_3_faces: float = 1.2  #                                    ACI Table 18.8.4.1
    joint_shear_coeff_other: float = 1.0  #                                      ACI Table 18.8.4.1
    joint_beam_depth_limit: float = 2.0  # joint depth >= beam depth / 2              ACI 18.8.2.4
    joint_bar_diameter_multiple: float = 20.0  # column side >= 20 db of through beam bars  ACI 18.8.2.3
    bcc_exempt_axial_fraction: float = 0.1  # no 6/5 rule at a top joint, Pu < 0.1 Ag fc'  ACI 18.7.3.1
    column_offset_limit: float = 1.0 / 6.0  # 1:6 alignment (project check)
    transverse_beam_width_fraction: float = 0.75  # transverse beam >= 3/4 joint width  ACI 18.8.4.2


@dataclass(frozen=True)
class ColumnShearConfig:
    """Non-seismic column shear strength (ACI 22.5.6)."""

    vc_coeff: float = 0.17  #                                                    ACI 22.5.6.1
    axial_divisor: float = 14.0  # 1 + Nu/(14*Ag)                                ACI 22.5.6.1
    tension_axial_divisor: float = 3.5  # 1 + Nu/(3.5*Ag), Nu negative           ACI 22.5.7.1
    vc_upper_coeff: float = 0.29  # practical upper cap on Vc


@dataclass(frozen=True)
class ColumnSlendernessConfig:
    """Slenderness of columns along their length (ACI 6.2.5, 6.6.4.5).

    The sway (P-Delta) effects come from the second-order analysis (ACI 6.7);
    these values are for the member (P-delta) magnification that is added to it.
    """

    radius_factor_rectangular: float = 0.30  # r = 0.30 h                        ACI 6.2.5.1(b)
    radius_factor_circular: float = 0.25  # r = 0.25 D                           ACI 6.2.5.1(c)
    limit_base: float = 34.0  # k lu / r <= 34 + 12 (M1/M2)                      ACI 6.2.5(b)
    limit_moment_coeff: float = 12.0  #                                          ACI 6.2.5(b)
    limit_max: float = 40.0  #                                                   ACI 6.2.5(c)
    stiffness_factor: float = 0.40  # (EI)eff = 0.4 Ec Ig / (1 + beta_dns)       ACI 6.6.4.4.4(a)
    stiffness_reduction: float = 0.75  # delta = Cm / (1 - Pu / (0.75 Pc))       ACI 6.6.4.5.2
    cm_base: float = 0.6  # Cm = 0.6 - 0.4 (M1/M2)                               ACI 6.6.4.5.3
    cm_moment_coeff: float = 0.4  #                                              ACI 6.6.4.5.3
    min_eccentricity: float = 15.0  # mm, M2,min = Pu (15 + 0.03 h)              ACI 6.6.4.5.4
    min_eccentricity_depth_factor: float = 0.03  #                               ACI 6.6.4.5.4
    max_magnifier: float = 1.4  # second-order moment <= 1.4 first-order         ACI 6.2.6
    default_sustained_ratio: float = 0.6  # beta_dns when the dead share is not in the data
    # Effective length factor k of a braced column (ACI R6.2.5): the smaller of
    # 0.7 + 0.05 (psiA + psiB) and 0.85 + 0.05 psi_min, at most 1.0.
    k_sum_base: float = 0.70
    k_sum_coeff: float = 0.05
    k_min_base: float = 0.85
    k_min_coeff: float = 0.05
    k_max: float = 1.0
    psi_fixed_base: float = 1.0  # a footing built to resist moment              ACI R6.2.5
    psi_pinned_base: float = 10.0  #                                             ACI R6.2.5
    column_inertia_factor: float = 0.70  # cracked I for psi                     ACI Table 6.6.3.1.1(a)
    beam_inertia_factor: float = 0.35  #                                         ACI Table 6.6.3.1.1(a)
    brace_alignment: float = 0.25  # cos^2 of the beam to the direction it braces (60 degrees)


@dataclass(frozen=True)
class DetailingDrawingConfig:
    """Constants that only affect the DXF drawings, not the design."""

    hook_angle_degrees: float = 135.0  # seismic hook                            ACI 25.3.2
    hook_tail_bar_multiple: float = 6.0  # 6 db                                  ACI 25.3.2
    hook_tail_min: float = 75.0  # mm                                            ACI 25.3.2
    arc_steps: int = 12  # segments per bend arc
    first_hoop_offset: float = 50.0  # mm, first hoop from support face
    column_joint_tie_spacing: float = 100.0  # mm, "JOINT REIN." row
    column_confinement_tie_spacing: float = 100.0  # mm, "CONFINMT" row
    column_schedule_base_label: str = "FDN"  # level below the bottom-most story
    column_general_tie_spacing: float = 150.0  # mm, "TIES" row outside confinement


# =============================================================================
# INPUT CONVENTIONS
# =============================================================================
@dataclass(frozen=True)
class InputConventionConfig:
    """Sign conventions of the data coming from ETABS.

    Inside the designers, axial force is ALWAYS compression-positive.
    ETABS reports compression as NEGATIVE, so the column designer flips the
    sign once, where the FACTORED LOADS table is read.
    """

    etabs_compression_is_negative: bool = True


# =============================================================================
# ONE OBJECT TO IMPORT
# =============================================================================
@dataclass(frozen=True)
class AciCode:
    """All ACI 318M-14 constants used by the designers, grouped by topic."""

    name: str = "ACI 318M-14"
    material: MaterialConfig = field(default_factory=MaterialConfig)
    strength: StrengthReductionConfig = field(default_factory=StrengthReductionConfig)
    beam_flexure: BeamFlexureConfig = field(default_factory=BeamFlexureConfig)
    beam_shear: BeamShearConfig = field(default_factory=BeamShearConfig)
    beam_torsion: BeamTorsionConfig = field(default_factory=BeamTorsionConfig)
    beam_detailing: BeamDetailingConfig = field(default_factory=BeamDetailingConfig)
    beam_seismic: BeamSeismicConfig = field(default_factory=BeamSeismicConfig)
    seismic_material: SeismicMaterialConfig = field(default_factory=SeismicMaterialConfig)
    column_strength: ColumnStrengthConfig = field(default_factory=ColumnStrengthConfig)
    column_transverse: ColumnTransverseConfig = field(default_factory=ColumnTransverseConfig)
    column_seismic: ColumnSeismicConfig = field(default_factory=ColumnSeismicConfig)
    column_shear: ColumnShearConfig = field(default_factory=ColumnShearConfig)
    column_slenderness: ColumnSlendernessConfig = field(
        default_factory=ColumnSlendernessConfig)
    drawing: DetailingDrawingConfig = field(default_factory=DetailingDrawingConfig)
    conventions: InputConventionConfig = field(default_factory=InputConventionConfig)

    # ---- helpers shared by beam and column scripts -------------------------
    def beta1(self, fc: float) -> float:
        """Whitney block factor for concrete strength ``fc`` (MPa)."""
        return self.material.beta1(fc)

    def phi_flexure(self, net_tensile_strain: float, fy: float, spiral: bool = False) -> float:
        """Flexure / axial-flexure phi factor from the extreme tension strain."""
        return self.strength.phi_flexure(
            net_tensile_strain, fy, self.material.steel_elastic_modulus, spiral
        )

    def crack_control_spacing(self, fy: float, clear_cover: float) -> float:
        """Maximum centre-to-centre bar spacing for crack control (ACI 24.3.2).

        ``clear_cover`` is the clear distance from the tension face to the
        surface of the flexural bar (i.e. cover + stirrup diameter).
        """
        cfg = self.beam_flexure
        fs = cfg.crack_service_stress_ratio * fy
        ratio = cfg.crack_reference_stress / fs
        return min(
            cfg.crack_spacing_coeff_1 * ratio - cfg.crack_cover_multiplier * clear_cover,
            cfg.crack_spacing_coeff_2 * ratio,
        )


# Default configuration imported by the design scripts.
CODE = AciCode()


# =============================================================================
# NSCP 2015 CHAPTER 2: LOAD COMBINATIONS  (Section 203)
# =============================================================================
@dataclass(frozen=True)
class LoadFactorConfig:
    """Load factors of the strength (203.3) and allowable stress (203.4) combinations."""

    dead_only: float = 1.4  # 1.4 D                                    NSCP 203-1
    dead: float = 1.2  # 1.2 D                                         NSCP 203-2 to 203-5
    live: float = 1.6  # 1.6 L                                         NSCP 203-2
    roof_live: float = 1.6  # 1.6 Lr                                   NSCP 203-3
    roof_live_companion: float = 0.5  # 0.5 Lr                         NSCP 203-2, 203-4
    live_companion: float = 0.5  # f1 for other live loads             NSCP 203.3.1
    live_companion_heavy: float = 1.0  # f1: assembly, > 4.8 kPa, garages  NSCP 203.3.1
    wind_with_roof: float = 0.5  # 0.5 W                               NSCP 203-3
    wind: float = 1.0  # 1.0 W                                         NSCP 203-4, 203-6
    seismic: float = 1.0  # 1.0 E, 1.0 Em                              NSCP 203-5, 203-7, 203-19
    dead_minimum: float = 0.9  # 0.9 D                                 NSCP 203-6, 203-7, 203-20
    service: float = 1.0  # D, L, Lr                                   NSCP 203-8 to 203-18
    service_companion: float = 0.75  # 0.75 [L + Lr + ...]             NSCP 203-11, 203-13
    service_wind: float = 0.6  # 0.6 W                                 NSCP 203-12 to 203-17
    service_seismic_divisor: float = 1.4  # E / 1.4                    NSCP 203-12 to 203-18
    service_dead_minimum: float = 0.6  # 0.6 D                         NSCP 203-14, 203-15
    sustained_live: float = 0.25  # D + 0.25 L for long-term deflection (practice)
    vertical_effect: float = 0.5  # Ev = 0.5 Ca I D                    NSCP 208.6.1
    orthogonal: float = 0.3  # 100 % one direction + 30 % the other    NSCP 208.7.1
    omega0: float = 2.8  # Em = Omega0 Eh, concrete systems            NSCP Table 208-11A
    rho: float = 1.0  # redundancy (1.0 <= rho <= 1.25 for SMRF)        NSCP 208.6.1
    drift_rho: float = 1.0  # rho when calculating drift              NSCP 208.6.1


# =============================================================================
# NSCP 2015 SECTION 205 / ASCE 7-10 4.7: LIVE LOAD REDUCTION
# =============================================================================
@dataclass(frozen=True)
class LiveLoadReductionConfig:
    """Live load reduction by NSCP 205.5 or by ASCE 7-10 4.7 (NSCP 205.6, 405.2.3).

    NSCP 205.5 prints R = r (A - 15), r = 0.08: the UBC 97 rate per ft2. Its
    metric form, used here, is r = 0.86 % per m2 above 14 m2.
    """

    nscp_rate: float = 0.86  # % per m2                                NSCP 205.5 Eq. 205-1 (metric)
    nscp_area_offset: float = 14.0  # m2                               NSCP 205.5 Eq. 205-1 (metric)
    max_one_level: float = 40.0  # %                                   NSCP 205.5
    max_other: float = 60.0  # %                                       NSCP 205.5
    dead_live_coeff: float = 23.1  # R <= 23.1 (1 + D/L)              NSCP 205.5 Eq. 205-2
    heavy_kpa: float = 4.8  # no reduction above                       NSCP 205.5, ASCE 4.7.3
    heavy_multi_floor: float = 20.0  # % for members on two or more floors  NSCP 205.5, ASCE 4.7.3
    # ASCE 4.7: the same in 7-10 and 7-16 (KLL: Table 4-2 in 7-10, Table 4.7-1 in 7-16)
    heavy_tolerance_m2: float = 0.5  # floor above 4.8 kPa a member may carry (project rule)
    asce_min_influence: float = 37.16  # m2, KLL AT >= 400 ft2         ASCE 4.7.2
    asce_constant: float = 0.25  # L = Lo (0.25 + 4.57 / sqrt(KLL AT)) ASCE Eq. 4.7-1
    asce_coeff: float = 4.57  #                                        ASCE Eq. 4.7-1
    asce_min_one_level: float = 0.50  # L >= 0.50 Lo                   ASCE 4.7.2
    asce_min_other: float = 0.40  # L >= 0.40 Lo                       ASCE 4.7.2
    kll_column: float = 4.0  # interior / exterior without cantilever  ASCE Table 4-2
    kll_beam: float = 2.0  # interior / edge without cantilever       ASCE Table 4-2


# =============================================================================
# NSCP 2015 SECTION 208 (UBC 97): EARTHQUAKE
# =============================================================================
@dataclass(frozen=True)
class SeismicConfig:
    """Earthquake loads: NSCP 208, with the UBC 97 tables ETABS uses."""

    # zone factors: NSCP has zones 2 and 4 only; ETABS (UBC 97) lists all five
    nscp_zone_factors: tuple = (0.2, 0.4)  #                          NSCP Table 208-3
    ubc_zone_factors: tuple = (0.075, 0.15, 0.2, 0.3, 0.4)  #          UBC Table 16-I
    zone4_factor: float = 0.4
    # Ca and Cv per soil, one value per UBC zone factor; zone 4 times Na / Nv
    ca: dict = field(default_factory=lambda: {  #                      NSCP Table 208-7, UBC 16-Q
        "SA": (0.06, 0.12, 0.16, 0.24, 0.32), "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
        "SC": (0.09, 0.18, 0.24, 0.33, 0.40), "SD": (0.12, 0.22, 0.28, 0.36, 0.44),
        "SE": (0.19, 0.30, 0.34, 0.36, 0.36)})
    cv: dict = field(default_factory=lambda: {  #                      NSCP Table 208-8, UBC 16-R
        "SA": (0.06, 0.12, 0.16, 0.24, 0.32), "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
        "SC": (0.13, 0.25, 0.32, 0.45, 0.56), "SD": (0.18, 0.32, 0.40, 0.54, 0.64),
        "SE": (0.26, 0.50, 0.64, 0.84, 0.96)})
    # (distance km, factor); linear in between, constant outside
    na: dict = field(default_factory=lambda: {  #                      NSCP Table 208-5
        "A": ((2.0, 1.5), (5.0, 1.2), (10.0, 1.0)),
        "B": ((2.0, 1.3), (5.0, 1.0), (10.0, 1.0)),
        "C": ((2.0, 1.0), (5.0, 1.0), (10.0, 1.0))})
    nv: dict = field(default_factory=lambda: {  #                      NSCP Table 208-6
        "A": ((2.0, 2.0), (5.0, 1.6), (10.0, 1.2), (15.0, 1.0)),
        "B": ((2.0, 1.6), (5.0, 1.2), (10.0, 1.0), (15.0, 1.0)),
        "C": ((2.0, 1.0), (5.0, 1.0), (10.0, 1.0), (15.0, 1.0))})
    na_cap: float = 1.1  # Na may be capped when regular, SMRF, rho 1.0  NSCP 208.4.4.3
    near_fault_km: float = 2.0  # site-specific spectrum recommended   NSCP 208.4.4.3
    importance_factors: tuple = (1.0, 1.25, 1.5)  #                    NSCP Table 208-1
    # R -> (system, permitted in zone 4), concrete                     NSCP Table 208-11A
    concrete_r: dict = field(default_factory=lambda: {
        8.5: ("special moment frame (or dual system with special walls)", True),
        6.5: ("dual system: ordinary walls, or special walls with IMRF", True),
        5.6: ("building frame: ordinary walls", False),
        5.5: ("intermediate moment frame / shear wall-frame interaction", False),
        5.0: ("building frame: special walls", True),
        4.5: ("bearing wall: special walls", True),
        4.2: ("shear wall-frame interactive with ordinary frames", False),
        3.5: ("ordinary moment frame", False),
        2.2: ("cantilevered column", True),
    })
    smrf_r: float = 8.5  #                                             NSCP Table 208-11A
    # Method A, Ct in ft units (ETABS) -> (system, Ct in m units)      NSCP 208.5.2.2 Eq. 208-12
    ct: dict = field(default_factory=lambda: {
        0.035: ("steel moment frames", 0.0853), 0.03: ("concrete moment frames", 0.0731),
        0.02: ("all other buildings", 0.0488)})
    period_exponent: float = 0.75  # T = Ct hn^(3/4)                   NSCP Eq. 208-12
    period_cap_zone4: float = 1.3  # Method B <= 1.3 T_A               NSCP 208.5.2.2
    period_cap_other: float = 1.4  #                                   NSCP 208.5.2.2
    # base shear V / W                                                 NSCP 208.5.2.1
    plateau: float = 2.5  # V <= 2.5 Ca I / R W                        NSCP Eq. 208-9
    minimum: float = 0.11  # V >= 0.11 Ca I W                          NSCP Eq. 208-10
    zone4_minimum: float = 0.8  # V >= 0.8 Z Nv I / R W                NSCP Eq. 208-11
    eccentricity: float = 0.05  # accidental                           NSCP 208.5.1.3
    # dynamic analysis
    scaling_regular: float = 0.90  # of the static base shear          NSCP 208.5.3.5.4
    scaling_irregular: float = 1.00  #                                 NSCP 208.5.3.5.4
    modal_mass: float = 0.90  #                                        NSCP 208.5.3.5.2
    dynamic_height_mm: float = 75_000.0  # dynamic procedure required  NSCP 208.4.8.3
    damping: float = 0.05  # spectrum damping ratio                    NSCP 208.5.3.2
    gravity: float = 9806.65  # mm/s2 as ETABS (NSCP 208.5.3.2 prints 9.815 m/s2)
    # drift                                                            NSCP 208.6.4, 208.6.5
    drift_amplification: float = 0.7  # Delta_M = 0.7 R Delta_S        NSCP Eq. 208-21
    drift_limit_short: float = 0.025  # T < 0.7 s                      NSCP 208.6.5.1
    drift_limit_long: float = 0.020  #                                 NSCP 208.6.5.1
    drift_period: float = 0.7  # s                                     NSCP 208.6.5.1


@dataclass(frozen=True)
class WindConfig:
    """Wind serviceability (NSCP 207 sets no drift limit: an office default)."""

    drift_limit_denominator: float = 400.0  # storey drift <= h/400 under the 203.3 wind combinations (practice)


@dataclass(frozen=True)
class AnalysisModelConfig:
    """Modelling requirements of the analysis."""

    beam_inertia: float = 0.35  # cracked I of beams                  NSCP 208.6.2, 406.6.3.1.1
    column_inertia: float = 0.70  # cracked I of columns              NSCP 208.6.2, 406.6.3.1.1
    service_stiffness_factor: float = 1.4  # service-level I = 1.4 x strength-level  ACI 6.6.3.2.2
    max_inertia: float = 1.0  # the service-level I is at most the gross section
    modifier_tolerance: float = 0.05  # accepted difference (project rule)
    rigid_zone_typical: float = 0.5  # rigid end zone factor (practice; NSCP 406.6.2.3(b))
    storage_live_mass: float = 0.25  # of storage live in W            NSCP 208.6.1
    partition_mass_kpa: float = 0.5  # minimum partition load in W     NSCP 208.6.1


@dataclass(frozen=True)
class NscpCode:
    """NSCP 2015 chapter 2 constants, grouped by topic."""

    name: str = "NSCP 2015"
    load_factors: LoadFactorConfig = field(default_factory=LoadFactorConfig)
    live_load_reduction: LiveLoadReductionConfig = field(default_factory=LiveLoadReductionConfig)
    seismic: SeismicConfig = field(default_factory=SeismicConfig)
    wind: WindConfig = field(default_factory=WindConfig)
    analysis: AnalysisModelConfig = field(default_factory=AnalysisModelConfig)


NSCP = NscpCode()


def override(code: Any, **changes: Any) -> Any:
    """Return a copy of ``code`` with selected fields changed.

    Use dotted names with double underscores, e.g.::

        stricter = override(CODE, strength__shear=0.70, beam_seismic__max_reinforcement_ratio=0.02)

    ``code`` itself is never modified.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for dotted, value in changes.items():
        group, _, attribute = dotted.partition("__")
        if not attribute:
            grouped.setdefault("", {})[group] = value
        else:
            grouped.setdefault(group, {})[attribute] = value
    top_level = grouped.pop("", {})
    updated_groups = {
        group: replace(getattr(code, group), **fields) for group, fields in grouped.items()
    }
    return replace(code, **top_level, **updated_groups)


if __name__ == "__main__":
    # Quick self-check: python design/code_config.py
    print(CODE.name, "beta1(28)=", CODE.beta1(28.0), "beta1(60)=", CODE.beta1(60.0))
    print("phi at eps_t=0.0035, fy=415:", round(CODE.phi_flexure(0.0035, 415.0), 3))
    print("crack spacing, fy=415, clear cover 50:", round(CODE.crack_control_spacing(415.0, 50.0), 1))
    print("overridden phi_shear:", override(CODE, strength__shear=0.70).strength.shear)
