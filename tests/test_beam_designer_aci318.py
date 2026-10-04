"""Unit tests for design/beam_designer_aci318.py  (ACI 318M-14).

HOW TO RUN (from the project root, the folder that contains main.py):
    python -m pytest tests/test_beam_designer_aci318.py -v

HOW TO READ THIS FILE
    Each test is a short story:  "given this beam ... the code should give ..."
    The expected numbers come from one of three places, named in each test:
      * PUBLISHED   - a worked example from StructurePoint / Wight & MacGregor.
                      The original US-unit values are converted to SI here.
      * HAND CALC   - a calculation written out step by step in the test.
      * BEHAVIOUR   - a rule that must always hold (more load -> not fewer bars).

    Published example used for shear + torsion + flexure:
      StructurePoint, "Equilibrium Torsion in Beams (ACI 318-14)"
      (after Wight, Reinforced Concrete Mechanics and Design, 7th ed., Example 7-2)
      Cantilever beam 14 in x 24 in, fc' = 4 ksi, fy = 60 ksi, #4 stirrups, #8 bars.
    Published example for compression steel:
      StructurePoint, "Doubly Reinforced Concrete Beam Design (ACI 318-14)"
      (after Wang et al., Reinforced Concrete Design, 8th ed., Example 3.11.1)
"""

import math
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

# --------------------------------------------------------------------------
# TEST SET-UP: make the project importable and replace ETABS/GUI-only modules
# with empty stand-ins, so the tests run on any computer without ETABS/Excel.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _install_stand_ins_if_needed() -> None:
    """Fake the ETABS API and GUI helpers when they are not installed."""
    try:
        import etabs_api  # noqa: F401
        import utilities._gui_helpers  # noqa: F401
        return
    except ImportError:
        pass
    for name in ("etabs_api", "utilities", "utilities._gui_helpers"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["etabs_api"].ETABSConnector = object
    sys.modules["etabs_api"].ETABSDataExporter = object
    for name in ("DualListboxSelector", "LoadingWindow", "select_output_directory", "show_warning"):
        setattr(sys.modules["utilities._gui_helpers"], name, object)


_install_stand_ins_if_needed()

from design import beam_designer_aci318 as beam  # noqa: E402
from design.code_config import CODE, override  # noqa: E402

# --------------------------------------------------------------------------
# UNIT CONVERSIONS (published examples are in US units, the code works in SI)
# --------------------------------------------------------------------------
IN = 25.4  # mm per inch
KIP = 4.44822  # kN per kip
KIP_FT = 1.355818  # kN-m per kip-ft
KSI = 6.894757  # MPa per ksi
IN2 = IN**2  # mm2 per in2


def close(actual: float, expected: float, tolerance: float = 0.03) -> bool:
    """True when ``actual`` is within ``tolerance`` (3 % by default) of ``expected``.

    3 % absorbs unit-conversion rounding and the fact that the metric ACI
    coefficients (0.17, 0.083 ...) are rounded versions of the US ones (2, 1 ...).
    """
    return abs(actual - expected) <= tolerance * abs(expected)


# --------------------------------------------------------------------------
# THE PUBLISHED CANTILEVER BEAM (StructurePoint equilibrium torsion example)
# --------------------------------------------------------------------------
@pytest.fixture
def torsion_beam():
    """Section and materials of the published cantilever beam, converted to SI."""
    return dict(
        width=14 * IN,
        height=24 * IN,
        d=21.5 * IN,  # effective depth used in the example
        fc=4 * KSI,
        fy=60 * KSI,
        stirrup=0.5 * IN,  # #4 stirrup
        cover=1.5 * IN,  # clear cover to stirrup
        main_bar=1.0 * IN,  # #8 bar
    )


# ==========================================================================
# 1. CONFIG FILE
# ==========================================================================
class TestConfig:
    def test_beta1_matches_aci_table(self):
        """ACI Table 22.2.2.4.3: 0.85 up to 28 MPa, falling to 0.65 at 55+ MPa."""
        assert CODE.beta1(20) == pytest.approx(0.85)
        assert CODE.beta1(28) == pytest.approx(0.85)
        assert CODE.beta1(35) == pytest.approx(0.80)  # 0.85 - 0.05*(35-28)/7
        assert CODE.beta1(80) == pytest.approx(0.65)

    def test_phi_transition_uses_real_yield_strain(self):
        """phi = 0.65 at yield strain, 0.90 at 0.005, straight line in between."""
        fy = 415.0
        yield_strain = fy / 200_000
        assert CODE.phi_flexure(yield_strain, fy) == pytest.approx(0.65)
        assert CODE.phi_flexure(0.005, fy) == pytest.approx(0.90)
        middle = (yield_strain + 0.005) / 2
        assert CODE.phi_flexure(middle, fy) == pytest.approx(0.775)

    def test_override_changes_a_copy_only(self):
        """override() must not touch the shared default configuration."""
        stricter = override(CODE, strength__shear=0.70)
        assert stricter.strength.shear == 0.70
        assert CODE.strength.shear == 0.75


# ==========================================================================
# 2. FLEXURE
# ==========================================================================
class TestFlexure:
    def test_singly_reinforced_capacity_matches_hand_calc(self, torsion_beam):
        """HAND CALC: rectangular block, tension steel only, phi*Mn.

        4 - #8 bars, no bottom steel.  a = As*fy/(0.85*fc*b),  Mn = As*fy*(d - a/2)
        """
        b = torsion_beam
        engine = beam.BeamFlexureDesign(
            b["width"], b["height"], b["fc"], b["fy"], b["fy"],
            b["main_bar"], b["stirrup"], b["cover"],
        )
        engine.n_top, engine.n_bot = 4, 0  # top steel only = singly reinforced

        result = engine.solve_moment_capacity(is_negative_moment=True)

        area_steel = 4 * math.pi * b["main_bar"] ** 2 / 4
        a = area_steel * b["fy"] / (0.85 * b["fc"] * b["width"])
        expected_mn = area_steel * b["fy"] * (b["d"] - a / 2) / 1e6  # kN-m

        assert close(result["Mn"], expected_mn, 0.005)
        assert result["failure_mode"] == "Tension-Controlled"
        assert result["phi"] == pytest.approx(0.90)

    def test_compression_steel_increases_capacity(self, torsion_beam):
        """BEHAVIOUR: adding bars to the compression face must raise phi*Mn.

        (This guards the earlier bug where compression steel was ignored.)
        """
        b = torsion_beam

        def capacity(n_bottom: int) -> float:
            engine = beam.BeamFlexureDesign(
                b["width"], b["height"], b["fc"], b["fy"], b["fy"],
                b["main_bar"], b["stirrup"], b["cover"],
            )
            engine.n_top, engine.n_bot = 6, n_bottom
            return engine.solve_moment_capacity(is_negative_moment=True)["phi_Mn"]

        assert capacity(3) > capacity(0)

    def test_doubly_reinforced_beam_matches_published_example(self):
        """PUBLISHED: Doubly Reinforced Beam, 14x29 in, fc'=5 ksi, fy=60 ksi.

        Published: As = 9.42 in2, As' = 1.81 in2 -> phi*Mn = 943 kip-ft.
        Bars must be equal size in this class, so 10 bars top (9.42 in2) and
        2 bars bottom (1.88 in2, slightly above the 1.81 published) are used;
        the result must land within 2 % ABOVE the published value.
        """
        bar_area = 9.42 * IN2 / 10
        bar_diameter = math.sqrt(4 * bar_area / math.pi)
        engine = beam.BeamFlexureDesign(
            14 * IN, 29 * IN, 5 * KSI, 60 * KSI, 60 * KSI,
            bar_diameter, 0.5 * IN, 2.0 * IN,
        )
        engine.n_top, engine.n_bot = 2, 10  # positive moment: tension at bottom
        # The example fixes the steel centroid 3 in from each face; use it directly.
        engine.compute_effective_depths = lambda is_negative_moment=True: (26 * IN, 3 * IN)

        result = engine.solve_moment_capacity(is_negative_moment=False)

        published_phi_mn = 943 * KIP_FT
        assert published_phi_mn * 0.99 <= result["phi_Mn"] <= published_phi_mn * 1.03
        assert result["failure_mode"] == "Tension-Controlled"

    def test_minimum_steel_formula(self, torsion_beam):
        """PUBLISHED: As,min = 1.003 in2 (ACI 9.6.1.2(b) governs, 200*b*d/fy)."""
        b = torsion_beam
        engine = beam.BeamFlexureDesign(
            b["width"], b["height"], b["fc"], b["fy"], b["fy"],
            b["main_bar"], b["stirrup"], b["cover"],
        )
        engine.n_top = 4
        limits = engine.check_reinforcement_limits(is_negative_moment=True)
        # Code governing value can be larger (150 mm spacing rule), never smaller.
        assert limits["As_min_governing"] >= 1.003 * IN2 * 0.97

    def test_design_beam_selects_four_bars_for_published_moment(self, torsion_beam):
        """PUBLISHED: Mu = 228.25 kip-ft needs As = 2.55 in2 -> 4 - #8 bars."""
        b = torsion_beam
        engine = beam.BeamFlexureDesign(
            b["width"], b["height"], b["fc"], b["fy"], b["fy"],
            b["main_bar"], b["stirrup"], b["cover"],
            Mu_neg=228.25 * KIP_FT, Mu_pos=0.0,
        )
        outcome = engine.design_beam()
        assert outcome["n_top"] == 4
        assert outcome["status"] == "PASSED"

    def test_crack_control_uses_cover_to_bar_surface(self):
        """HAND CALC (ACI 24.3.2): cc is measured to the BAR, not to the stirrup.

        fy = 415 MPa -> fs = 2/3 fy = 276.7 MPa.
        s = 380*(280/fs) - 2.5*cc  with cc = 40 (cover) + 10 (stirrup) = 50 mm.
        """
        engine = beam.BeamFlexureDesign(300, 500, 28, 415, 415, 20, 10, 40)
        fs = 2 / 3 * 415
        expected = min(380 * (280 / fs) - 2.5 * 50, 300 * (280 / fs))
        assert engine.check_crack_control_spacing()["s_max_crack"] == pytest.approx(expected)

    def test_more_moment_never_needs_fewer_bars(self):
        """BEHAVIOUR: bar count is non-decreasing with the design moment."""
        counts = []
        for moment in (50, 150, 250, 350):
            engine = beam.BeamFlexureDesign(350, 600, 28, 415, 415, 25, 10, 40, Mu_neg=moment)
            counts.append(engine.design_beam()["n_top"])
        assert counts == sorted(counts)


# ==========================================================================
# 3. SHEAR
# ==========================================================================
class TestShear:
    def test_published_stirrup_demand(self, torsion_beam):
        """PUBLISHED: Vu = 57.14 kip -> Av/s = 0.0295 in2/in.

        Small difference is expected: the metric Vc uses 0.17 where the US
        formula uses 2*sqrt(fc') = 0.166 (about 2 % apart).
        """
        b = torsion_beam
        shear = beam.BeamShearDesign(
            b["width"], b["height"], b["d"], b["fc"], b["fy"], b["stirrup"], n_legs=2
        )
        result = shear.solve_shear_capacity(57.14 * KIP)
        assert close(result["Av_s_demand"], 0.0295 * IN, 0.03)  # mm2 per mm
        assert result["shear_failed"] is False

    def test_concrete_shear_strength_hand_calc(self):
        """HAND CALC (ACI 22.5.5.1): Vc = 0.17*sqrt(fc')*bw*d."""
        shear = beam.BeamShearDesign(300, 600, 540, 28, 415, 10, 2)
        result = shear.solve_shear_capacity(50.0)
        assert result["Vc"] == pytest.approx(0.17 * math.sqrt(28) * 300 * 540 / 1000)

    def test_small_shear_needs_no_stirrup_steel_from_strength(self):
        """BEHAVIOUR: if Vu is below phi*Vc the strength-based demand is zero."""
        shear = beam.BeamShearDesign(300, 600, 540, 28, 415, 10, 2)
        assert shear.solve_shear_capacity(20.0)["Av_s_demand"] == 0.0

    def test_stirrup_spacing_limit_tightens_for_large_shear(self):
        """ACI 9.7.6.2.2: d/2 (max 600) normally, d/4 (max 300) for large Vs."""
        shear = beam.BeamShearDesign(300, 600, 540, 28, 415, 10, 2)
        low = shear.solve_shear_capacity(100.0)["s_max_code"]
        high = shear.solve_shear_capacity(450.0)["s_max_code"]
        assert low == pytest.approx(270.0)  # d/2
        assert high == pytest.approx(135.0)  # d/4

    def test_section_too_small_is_reported(self):
        """ACI 22.5.1.2: Vs cannot exceed 0.66*sqrt(fc')*bw*d."""
        shear = beam.BeamShearDesign(200, 400, 340, 28, 415, 10, 2)
        assert shear.solve_shear_capacity(2000.0)["shear_failed"] is True

    def test_seismic_zero_vc_option(self):
        """ACI 18.6.5.2: Vc can be switched off, stirrups must carry everything."""
        with_vc = beam.BeamShearDesign(300, 600, 540, 28, 415, 10, 2)
        no_vc = beam.BeamShearDesign(300, 600, 540, 28, 415, 10, 2, suppress_Vc=True)
        assert no_vc.solve_shear_capacity(200)["Vc"] == 0.0
        assert (
            no_vc.solve_shear_capacity(200)["Av_s_demand"]
            > with_vc.solve_shear_capacity(200)["Av_s_demand"]
        )


# ==========================================================================
# 4. TORSION
# ==========================================================================
class TestTorsion:
    def _engine(self, b, fyt=None):
        return beam.BeamTorsionDesign(
            b["width"], b["height"], b["d"], b["fc"], b["fy"],
            fyt or b["fy"], b["stirrup"], b["cover"],
        )

    def test_published_section_properties(self, torsion_beam):
        """PUBLISHED: Aoh = 215.25 in2, ph = 62.00 in."""
        engine = self._engine(torsion_beam)
        assert engine.Aoh == pytest.approx(215.25 * IN2, rel=1e-6)
        assert engine.p_h == pytest.approx(62.0 * IN, rel=1e-6)

    def test_published_torsion_results(self, torsion_beam):
        """PUBLISHED: Tu = 28 kip-ft, Vu = 57.14 kip.

        Published: phi*Tth = 5.87 kip-ft, At/s = 0.0204 in2/in per leg,
        Al = 1.265 in2, section adequate (325.6 psi < 474.3 psi).
        """
        engine = self._engine(torsion_beam)
        result = engine.solve_torsion_capacity(Tu=28 * KIP_FT, Vu=57.14 * KIP)

        assert close(result["Tth"], 5.87 * KIP_FT, 0.02)
        assert close(result["At_s_demand"], 0.0204 * IN, 0.02)
        assert close(result["Al_design"], 1.265 * IN2, 0.02)
        assert close(result["combined_stress"], 325.55 * 0.006895, 0.02)  # psi -> MPa
        assert result["torsion_failed"] is False

    def test_cracking_torsion_has_phi_applied_only_once(self, torsion_beam):
        """PUBLISHED: phi*Tcr = 23.49 kip-ft.  (Bug fixed: phi was applied twice.)"""
        engine = self._engine(torsion_beam)
        result = engine.solve_torsion_capacity(Tu=28 * KIP_FT, Vu=57.14 * KIP)
        assert close(result["phi_Tcr"], 23.49 * KIP_FT, 0.02)
        assert result["phi_Tcr"] == pytest.approx(0.75 * result["Tcr"])

    def test_equilibrium_torsion_is_not_reduced_by_default(self, torsion_beam):
        """ACI 22.7.3.2: only compatibility torsion may drop to phi*Tcr.

        The published beam has equilibrium torsion, so full Tu = 28 kip-ft is
        designed (At/s = 0.0204).  Turning redistribution ON must give LESS steel.
        """
        engine = self._engine(torsion_beam)
        default = engine.solve_torsion_capacity(28 * KIP_FT, 57.14 * KIP)

        relaxed_engine = self._engine(torsion_beam)
        relaxed_engine.code = override(CODE, beam_torsion__allow_redistribution=True)
        relaxed = relaxed_engine.solve_torsion_capacity(28 * KIP_FT, 57.14 * KIP)

        assert relaxed["At_s_demand"] < default["At_s_demand"]

    def test_small_torsion_is_ignored(self, torsion_beam):
        """ACI 22.7.1.1: below phi*Tth no torsion steel is needed."""
        engine = self._engine(torsion_beam)
        result = engine.solve_torsion_capacity(Tu=3 * KIP_FT, Vu=20 * KIP)
        assert result["At_s_demand"] == 0.0
        assert result["Al_design"] == 0.0

    def test_minimum_longitudinal_torsion_steel(self, torsion_beam):
        """ACI 9.6.4.3 uses fy (not fyt) in Al,min = 0.42*sqrt(fc')*Acp/fy - ...

        With fyt different from fy the result must follow the fy-based formula.
        Al,min is only decisive for small torsion, so a small Tu just above the
        threshold is used.
        """
        b = torsion_beam
        fyt = 275.0  # deliberately different from fy = 414 MPa
        engine = self._engine(b, fyt=fyt)
        tu = 1.05 * engine.solve_torsion_capacity(0.0, 0.0)["Tth"]  # just above threshold
        result = engine.solve_torsion_capacity(Tu=tu, Vu=0.0)

        acp = b["width"] * b["height"]
        term_1 = 0.42 * math.sqrt(b["fc"]) * acp / b["fy"]
        at_s_floor = 0.175 * b["width"] / fyt
        at_s = max(result["At_s_demand"], 0.0)
        expected_min = max(
            0.0,
            min(
                term_1 - at_s * engine.p_h * fyt / b["fy"],
                term_1 - at_s_floor * engine.p_h * fyt / b["fy"],
            ),
        )
        # Torsion is barely above the threshold, so the minimum-steel rule decides.
        assert result["Al_design"] == pytest.approx(expected_min, rel=1e-6)

    def test_torsion_stirrup_spacing_limit(self, torsion_beam):
        """PUBLISHED: s <= ph/8 = 7.75 in."""
        result = self._engine(torsion_beam).solve_torsion_capacity(28 * KIP_FT, 57.14 * KIP)
        assert result["s_max_torsion"] == pytest.approx(7.75 * IN, rel=0.001)

    def test_combined_shear_torsion_stirrup_ratio(self, torsion_beam):
        """PUBLISHED: (Av + 2At)/s = 0.0704 in2/in."""
        b = torsion_beam
        shear = beam.BeamShearDesign(
            b["width"], b["height"], b["d"], b["fc"], b["fy"], b["stirrup"], 2
        )
        s = shear.solve_shear_capacity(57.14 * KIP)
        t = self._engine(b).solve_torsion_capacity(28 * KIP_FT, 57.14 * KIP)
        total = s["Av_s_demand"] + 2 * t["At_s_demand"]
        assert close(total, 0.0704 * IN, 0.03)


# ==========================================================================
# 5. SEISMIC (SPECIAL MOMENT FRAME) BEAM CHECKS
# ==========================================================================
def _seismic_beam(n_top=4, n_bot=3, width=350, height=600):
    """Three identical location engines with chosen bar counts."""
    engines = {}
    for name in ("Left Support Face", "Midspan Zone", "Right Support Face"):
        engine = beam.BeamFlexureDesign(width, height, 28, 415, 415, 25, 10, 40)
        engine.n_top, engine.n_bot = n_top, n_bot
        engines[name] = engine
    return engines


class TestSeismic:
    def test_probable_moment_hand_calc(self):
        """HAND CALC (ACI 18.6.5.1): Mpr uses fs = 1.25 fy and phi = 1.0.

        4 - 25 mm bars at the top.  a = As*1.25fy/(0.85 fc b),  Mpr = As*1.25fy*(d - a/2)
        """
        engines = _seismic_beam()
        checker = beam.BeamSeismicDesign(engines, clear_span=6000)
        engine = engines["Left Support Face"]
        d, _ = engine.compute_effective_depths(is_negative_moment=True)

        area = 4 * math.pi * 25**2 / 4
        fs = 1.25 * 415
        a = area * fs / (0.85 * 28 * 350)
        expected = area * fs * (d - a / 2) / 1e6

        assert checker.compute_probable_moment(engine, True) == pytest.approx(expected)

    def test_reinforcement_ratio_limit(self):
        """ACI 18.6.3.1: rho <= 0.025.  Light steel passes, a very heavy layer fails."""
        light = beam.BeamSeismicDesign(_seismic_beam(4, 3), 6000).check_reinforcement_ratio_limits()
        heavy = beam.BeamSeismicDesign(_seismic_beam(14, 3), 6000).check_reinforcement_ratio_limits()
        assert light["all_passed"] is True
        assert heavy["all_passed"] is False

    def test_positive_moment_at_face_must_reach_half_of_negative(self):
        """ACI 18.6.3.2: bottom capacity at the support >= 1/2 of top capacity."""
        weak_bottom = beam.BeamSeismicDesign(
            _seismic_beam(n_top=8, n_bot=3), 6000
        ).check_flexural_capacity_ratios()
        strong_bottom = beam.BeamSeismicDesign(
            _seismic_beam(n_top=4, n_bot=4), 6000
        ).check_flexural_capacity_ratios()
        assert weak_bottom["all_passed"] is False
        assert strong_bottom["all_passed"] is True

    def test_sway_shear_hand_calc_and_vc_suppression(self):
        """HAND CALC (ACI 18.6.5.1): Ve = (Mpr_top,left + Mpr_bot,right)/ln + gravity.

        Symmetric beam: sway shear = (Mpr_top + Mpr_bot)/ln.  With a small
        gravity shear the earthquake part is >= 50 % so Vc must be set to zero.
        """
        engines = _seismic_beam()
        checker = beam.BeamSeismicDesign(engines, clear_span=6000, Pu_axial=0.0)
        zero_torsion = {"Al_top_req": 0.0}
        sides = {"Left Support (d_eff)": zero_torsion, "Right Support (d_eff)": zero_torsion}

        result = checker.evaluate_seismic_shear_demands(30.0, 30.0, sides)

        engine = engines["Left Support Face"]
        expected_sway = (
            checker.compute_probable_moment(engine, True)
            + checker.compute_probable_moment(engine, False)
        ) / 6.0
        assert result["V_sway_max"] == pytest.approx(expected_sway)
        assert result["Vu_seismic_left"] == pytest.approx(30.0 + expected_sway)
        assert result["Vc_zero_left"] is True

    def test_vc_kept_when_gravity_shear_dominates(self):
        """Large gravity shear -> earthquake share < 50 % -> Vc stays."""
        engines = _seismic_beam()
        checker = beam.BeamSeismicDesign(engines, clear_span=6000)
        zero = {"Al_top_req": 0.0}
        sides = {"Left Support (d_eff)": zero, "Right Support (d_eff)": zero}
        result = checker.evaluate_seismic_shear_demands(2000.0, 2000.0, sides)
        assert result["Vc_zero_left"] is False

    def test_seismic_off_returns_gravity_only(self):
        """With seismic design switched off, no sway shear is added."""
        checker = beam.BeamSeismicDesign(_seismic_beam(), 6000, enable_seismic=False)
        result = checker.evaluate_seismic_shear_demands(80.0, 90.0, {})
        assert result["V_sway_max"] == 0.0
        assert result["Vu_seismic_left"] == 80.0


# ==========================================================================
# 6. DETAILING HELPERS
# ==========================================================================
class TestDetailing:
    def test_no_skin_or_torsion_steel_for_ordinary_beam(self):
        """No torsion and h <= 900 mm -> no side bars."""
        result = beam.distribute_longitudinal_torsion_and_skin(
            0.0, 25, 12, 600, 300, 540, 415, 40, 10
        )
        assert result["n_side_bars_total"] == 0

    def test_torsion_steel_is_split_into_four_faces(self):
        """Al is shared equally: Al/4 on top, Al/4 on bottom, Al/2 on the sides."""
        result = beam.distribute_longitudinal_torsion_and_skin(
            1200.0, 25, 16, 600, 300, 540, 415, 40, 10
        )
        assert result["Al_top_req"] == pytest.approx(300.0)
        assert result["Al_bot_req"] == pytest.approx(300.0)
        assert result["Al_sides_req"] == pytest.approx(600.0)
        assert result["n_side_per_face"] >= 1

    def test_deep_beam_gets_skin_bars(self):
        """ACI 9.7.2.3: h > 900 mm requires skin reinforcement."""
        result = beam.distribute_longitudinal_torsion_and_skin(
            0.0, 25, 16, 1000, 300, 940, 415, 40, 10
        )
        assert result["n_side_per_face"] >= 1

    def test_last_layer_never_holds_a_single_bar(self):
        """Project rule: a layer with 1 bar is bumped to 2."""
        engine = beam.BeamFlexureDesign(300, 500, 28, 415, 415, 20, 10, 40)
        per_layer = engine.max_bar_per_layer
        engine.n_top = per_layer + 1  # would leave exactly one bar on layer 2
        engine.n_bot = 2
        engine.clean_single_bars()
        assert engine.calculate_layer_distribution(engine.n_top)[-1] >= 2

    def test_stirrup_legs_grow_with_bars_per_layer(self):
        """Alternate bars need lateral support: legs = bars/2 + 1 (rounded down)."""
        assert beam.check_alternating_tie_legs(6, 6, 2, 6) == 4
        assert beam.check_alternating_tie_legs(2, 2, 2, 6) == 2

    def test_anchorage_adds_bars_when_legs_exceed_first_layer(self):
        """Every stirrup leg needs a corner/anchor bar in the first layer."""
        result = beam.check_stirrup_leg_anchorage(
            n_top=2, n_bot=2, n_legs=3, max_bar_per_layer=5,
            dmain=20, dstirrup=10, width=300, cc=40,
        )
        assert result["final_n_top"] == 3
        assert result["anchorage_passed"] is False


# ==========================================================================
# 7. SUPPORT CLASSIFICATION (mock ETABS connectivity)
# ==========================================================================
class TestSupportStatus:
    def test_beam_supports_from_connectivity(self):
        """Mock ETABS connectivity: column at J of B1, columns at both ends of B2."""
        connectivity = pd.DataFrame(
            {
                "UniqueName": ["C1", "C2", "C3", "B1", "B2"],
                "DesignType": ["Column", "Column", "Column", "Beam", "Beam"],
                "UniquePtI": ["10", "11", "12", "1", "2"],
                "UniquePtJ": ["20", "21", "22", "12", "11"],
            }
        )
        # B1 runs 1 -> 12, B2 runs 2 -> 11. Point 12 and 11 have columns; 1 and 2 do not.
        # Give point 2 a column too so B2 is supported at both ends.
        connectivity.loc[connectivity["UniqueName"] == "C1", "UniquePtI"] = "2"
        result = beam.identify_cantilever_beams(pd.DataFrame(), connectivity).set_index("UniqueName")

        assert result.loc["B1", "SupportStatus"] == "Cantilever (Free at PtI)"
        assert result.loc["B2", "SupportStatus"] == "Supported Both Ends"


# ==========================================================================
# 8. FULL PIPELINE WITH MOCK ETABS RESULTS
# ==========================================================================
def _mock_beam_properties(name="B1", support="Supported Both Ends"):
    """One row that looks like what extract_beam_design_data() writes to Excel."""
    return pd.DataFrame(
        [{
            "UniqueName": name, "Story": "L2", "SectProp": "B300x600",
            "SupportStatus": support, "Width": 300.0, "Depth": 600.0,
            "f'c": 28.0, "fy": 414.0, "fyw": 414.0,
            "dm": 25.0, "ds": 10.0, "dw": 16.0, "cc": 40.0,
        }]
    )


def _mock_force_table(name="B1", span=6000.0, wu=60.0):
    """Force stations for a simply supported beam under uniform load wu (kN/m).

    One load combination called 'GRAV'. Moment is positive (sagging) and
    follows M = wu*x*(L - x)/2; shear follows V = wu*(L/2 - x).
    """
    rows = []
    length = span / 1000.0
    for i in range(13):
        x = length * i / 12
        rows.append(
            {
                "UniqueName": name,
                "Combo": "GRAV",
                "Station": x * 1000.0,
                "V2": wu * (length / 2 - x),
                "M3": wu * x * (length - x) / 2,
                "T": 0.0,
            }
        )
    return pd.DataFrame(rows)


class TestPipelineWithMockEtabs:
    def test_simply_supported_beam_matches_hand_moment(self):
        """HAND CALC: wu = 60 kN/m, L = 6 m -> Mmid = wu L^2 / 8 = 270 kN-m."""
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        bottom = results[results["Face"] == "BOTTOM"].iloc[0]
        assert bottom["Mu_mid"] == pytest.approx(60 * 6**2 / 8, rel=0.001)
        assert bottom["Design_Status"] == "OK"

    def test_designed_bars_are_strong_enough(self):
        """Take the bars the pipeline chose and re-check phi*Mn >= Mu independently."""
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        bottom = results[results["Face"] == "BOTTOM"].iloc[0]
        n_bars = int(bottom["n_mid_L1"] + bottom["n_mid_L2"] + bottom["n_mid_L3"])

        check = beam.BeamFlexureDesign(300, 600, 28, 414, 414, 25, 10, 40)
        check.n_bot = n_bars
        check.n_top = int(
            results[results["Face"] == "TOP"].iloc[0][["n_mid_L1", "n_mid_L2", "n_mid_L3"]].sum()
        )
        capacity = check.solve_moment_capacity(is_negative_moment=False)["phi_Mn"]
        assert capacity >= bottom["Mu_mid"]

    def test_heavier_load_gives_at_least_as_many_bars(self):
        """BEHAVIOUR: doubling the load cannot reduce the bottom bar count."""
        def bottom_bars(load):
            out = beam.execute_beam_design(
                _mock_beam_properties(), _mock_force_table(wu=load), False, "GRAV"
            )
            row = out[out["Face"] == "BOTTOM"].iloc[0]
            return row["n_mid_L1"] + row["n_mid_L2"] + row["n_mid_L3"]

        assert bottom_bars(120) >= bottom_bars(40)

    def test_output_has_top_and_bottom_row_per_beam(self):
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        assert list(results["Face"]) == ["TOP", "BOTTOM"]

    def test_stirrup_spacing_is_multiple_of_25mm(self):
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        row = results.iloc[0]
        assert row["Spacing_2H"] % 25 == 0
        assert row["Spacing_Mid"] % 25 == 0

    def test_seismic_zone_stirrups_are_at_least_as_tight(self):
        """ACI 18.6.4.4: with seismic design on, end-zone hoops obey d/4, 6db, 150 mm."""
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), True, "GRAV"
        )
        d = 600 - 40 - 10 - 12.5
        limit = min(d / 4, 6 * 25, 150)
        assert results.iloc[0]["Spacing_2H"] <= limit

    def test_cantilever_sway_shear_hand_calc(self):
        """HAND CALC: cantilever sway shear = Mpr(top bars at the support) / clear span.

        Also guards the support lookup, which used to search for the text 'Pt1'
        although the connectivity helper writes 'Free at PtI'.
        """
        props = _mock_beam_properties(support="Cantilever (Free at PtI)")
        results = beam.execute_beam_design(props, _mock_force_table(), True, "GRAV")
        top = results[results["Face"] == "TOP"].iloc[0]

        engine = beam.BeamFlexureDesign(300, 600, 28, 414, 414, 25, 10, 40)
        engine.n_top = int(top["n_right_L1"] + top["n_right_L2"] + top["n_right_L3"])
        engine.n_bot = 2
        checker = beam.BeamSeismicDesign({}, 6000)
        expected = checker.compute_probable_moment(engine, True) / 6.0
        assert top["V_sway_max_kN"] == pytest.approx(expected, rel=0.01)

    def test_display_labels_round_trip(self):
        """Excel headers use subscripts; restoring them must give the original keys."""
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        shown = beam.display_beam_result_labels(results)
        assert "n₍left, 1₎" in shown.columns
        restored = beam.restore_beam_result_labels(shown)
        assert list(restored.columns) == list(results.columns)

    def test_beam_names_sort_by_floor_type_then_number(self):
        names = ["L2BY-3", "L2BX-44", "L2BX-7", "L1GX-1"]
        ordered = sorted(names, key=beam.beam_sort_key)
        assert ordered == ["L1GX-1", "L2BX-7", "L2BX-44", "L2BY-3"]


# ==========================================================================
# 9. DXF BEAM SCHEDULE
# ==========================================================================
class TestBeamScheduleDxf:
    def test_schedule_file_is_created(self, tmp_path):
        results = beam.execute_beam_design(
            _mock_beam_properties(), _mock_force_table(), False, "GRAV"
        )
        target = tmp_path / "L2_Beam_Schedule.dxf"
        beam.generate_dxf_beam_schedule("L2", results, str(target))
        assert target.exists() and target.stat().st_size > 1000


# --------------------------------------------------------------------------
# CLEARING OLD RESULTS FROM THE SHEET
# --------------------------------------------------------------------------
class _FakeRange:
    def __init__(self, sheet, first, last=None):
        self.sheet, self.first, self.last = sheet, first, last or first
        self.row, self.column = first

    def clear(self):
        self.sheet.cleared.append((self.first, self.last))


# --------------------------------------------------------------------------
# SMRF MOMENT STRENGTH RATIOS ARE ENFORCED, DESIGN SHEAR IS REPORTED
# --------------------------------------------------------------------------
def _three_zone_engines(top=6, bottom=2):
    engines = {}
    for name in ("Left Support Face", "Midspan Zone", "Right Support Face"):
        engine = beam.BeamFlexureDesign(400.0, 600.0, 28.0, 415.0, 415.0, 25.0, 10.0, 40.0)
        engine.n_top, engine.n_bot = (top, bottom) if "Support" in name else (2, 2)
        engines[name] = engine
    return engines


def test_bottom_bars_are_added_until_positive_strength_is_half_the_negative():
    """ACI 18.6.3.2: at the joint face Mn+ >= 0.5 Mn-, and every section >= 0.25 max."""
    engines = _three_zone_engines(top=6, bottom=2)
    checker = beam.BeamSeismicDesign(engines, 6000.0)
    assert not checker.check_flexural_capacity_ratios()["all_passed"]

    assert checker.enforce_flexural_capacity_ratios() is True

    check = checker.check_flexural_capacity_ratios()
    assert check["all_passed"]
    assert engines["Left Support Face"].n_bot > 2          # bars were added at the support
    assert engines["Left Support Face"].n_top == 6          # the governing top steel is untouched
    left = check["locations"]["Left Support Face"]
    assert left["Mn_pos"] >= 0.5 * check["max_M_neg_support"]


def test_design_reports_the_design_shear_and_the_smrf_checks():
    results = beam.execute_beam_design(
        df_beam_props=_mock_beam_properties(), df_frame_forces=_mock_force_table(),
        enable_seismic_design=True, gravity_combo_name="GRAV",
    )
    row = results.iloc[0]
    assert row["Ve_left_kN"] >= row["V_sway_max_kN"] > 0    # sway shear plus gravity shear
    assert row["SMRF_Flexure_Ratio_Check"] in ("PASS", "FAIL")
    assert row["SMRF_Rho_Check"] in ("PASS", "FAIL")


def test_non_seismic_design_marks_the_smrf_checks_not_applicable():
    results = beam.execute_beam_design(
        df_beam_props=_mock_beam_properties(), df_frame_forces=_mock_force_table(),
        enable_seismic_design=False, gravity_combo_name="GRAV",
    )
    assert set(results["SMRF_Flexure_Ratio_Check"]) == {"N/A"}
    assert set(results["SMRF_Rho_Check"]) == {"N/A"}


# --------------------------------------------------------------------------
# CALCULATION REPORT (PDF) CONTENT
# --------------------------------------------------------------------------
def _beam_report_member(seismic=False):
    results = beam.execute_beam_design(
        _mock_beam_properties(), _mock_force_table(), seismic, "GRAV"
    )
    top = results[results["Face"] == "TOP"].iloc[0]
    bottom = results[results["Face"] == "BOTTOM"].iloc[0]
    return beam._beam_calc_member(top, bottom, seismic), bottom


def test_beam_report_recomputes_strength_from_the_designed_bars():
    """HAND CHECK: the midspan bottom row carries Mu = 270 kN-m and phi*Mn above it."""
    member, bottom = _beam_report_member()
    tables = {table.title: table for table in member.tables}
    midspan = next(row for row in tables["Flexure"].rows
                   if row[0] == "Midspan" and row[1] == "Bottom")
    assert float(midspan[2]) == pytest.approx(270.0, abs=0.01)   # Mu
    assert float(midspan[10]) >= 270.0                            # phi*Mn
    assert midspan[-1] == "PASS"
    assert len(tables["Flexure"].rows) == 6       # three locations, two faces
    assert len(tables["Shear and torsion"].rows) == 3
    assert member.summary[3] == "PASS"
    assert member.summary[4] == bottom["Design_Status"]


def test_beam_report_adds_the_seismic_tables_only_for_smrf():
    plain, _ = _beam_report_member(seismic=False)
    seismic, _ = _beam_report_member(seismic=True)
    assert not any("SMRF" in table.title for table in plain.tables)
    assert {"Seismic moment strengths (SMRF)", "Seismic design shear (SMRF)"} <= {
        table.title for table in seismic.tables
    }


# --------------------------------------------------------------------------
# GRAVITY BEAMS ARE LEFT OUT OF THE SEISMIC PROVISIONS
# --------------------------------------------------------------------------
def _design(support, seismic):
    return beam.execute_beam_design(
        _mock_beam_properties(support=support), _mock_force_table(), seismic, "GRAV"
    )


def test_gravity_beam_gets_the_same_design_with_seismic_on_or_off():
    """BEHAVIOUR: a beam on no column is designed for gravity only."""
    on = _design(beam.GRAVITY_BEAM_STATUS, True)
    off = _design(beam.GRAVITY_BEAM_STATUS, False)
    pd.testing.assert_frame_equal(on, off)
    assert set(on["SMRF_Flexure_Ratio_Check"]) == {"N/A"}
    assert (on["V_sway_max_kN"] == 0).all()


def test_frame_beam_still_gets_the_seismic_provisions():
    on = _design("Supported Both Ends", True)
    assert set(on["SMRF_Flexure_Ratio_Check"]) != {"N/A"}
    assert (on["V_sway_max_kN"] > 0).all()


def test_schedules_are_written_per_story_for_girders_and_for_gravity_beams(tmp_path, monkeypatch):
    girder = _design("Supported Both Ends", False)
    gravity = _design(beam.GRAVITY_BEAM_STATUS, False).assign(UniqueName="B2")
    results = pd.concat([girder, gravity], ignore_index=True)
    written = []
    monkeypatch.setattr(
        beam, "generate_dxf_beam_schedule",
        lambda story_name, df_story, output_filepath: written.append(
            (Path(output_filepath).name, sorted(df_story["UniqueName"].unique()))
        ),
    )
    paths = beam.export_beam_dxf(results, str(tmp_path))
    assert len(paths) == 2
    assert written == [("L2_Girder_Schedule.dxf", ["B1"]), ("L2_Beam_Schedule.dxf", ["B2"])]


def test_the_beam_table_gets_support_status_and_bar_inputs():
    frame = pd.DataFrame({
        "Story": ["2F", "2F", "2F"], "UniqueName": ["2GX-2", "2GX-1", "2-C1"],
        "SectProp": ["G", "G", "C"], "DesignType": ["Beam", "Beam", "Column"],
        "Width": [300, 300, 400], "Depth": [500, 500, 400],
    })
    conn = pd.DataFrame({
        "UniqueName": ["2GX-2", "2GX-1", "2-C1"], "DesignType": ["Beam", "Beam", "Column"],
        "UniquePtI": ["a", "b", "z"], "UniquePtJ": ["b", "c", "a"],
    })
    bars = {"dm": 20, "ds": 10, "dw": 12, "fyw": 275, "cc": 40}
    table = beam.prepare_beam_table(frame, conn, bars)
    assert table["UniqueName"].tolist() == ["2GX-1", "2GX-2"]  # beams only, sorted
    assert table.loc[table.UniqueName == "2GX-2", "SupportStatus"].item().startswith(
        "Cantilever")
    assert table["cc"].tolist() == [40.0, 40.0]
    assert table.columns.tolist().index("SupportStatus") == \
        table.columns.tolist().index("SectProp") + 1


def test_beam_results_are_saved_as_a_formatted_workbook(tmp_path):
    from openpyxl import load_workbook

    results = _design("Supported Both Ends", False)
    path = beam.write_beam_results_xlsx(results, str(tmp_path / "beams.xlsx"))
    sheet = load_workbook(path).active
    assert sheet.title == "BEAM DESIGN"
    assert sheet.cell(row=1, column=1).font.bold
    assert sheet.max_row == len(results) + 1
