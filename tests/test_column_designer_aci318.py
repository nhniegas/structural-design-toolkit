"""Unit tests for design/column_designer_aci318.py  (ACI 318M-14).

HOW TO RUN (from the project root, the folder that contains main.py):
    python -m pytest tests/test_column_designer_aci318.py -v

HOW TO READ THIS FILE
    Each test is a short story:  "given this column ... the code should give ..."
    Expected numbers come from:
      * PUBLISHED - a worked example (US units converted to SI in this file).
      * HAND CALC - a calculation written out step by step in the test.
      * BEHAVIOUR - a rule that must always hold.

    Published example used for the interaction diagram:
      StructurePoint, "Interaction Diagram - Tied Reinforced Concrete Column
      (ACI 318-14)", after Wight & MacGregor, Reinforced Concrete Mechanics and
      Design, 6th ed., Example 11-1.
      16 in x 16 in column, fc' = 5 ksi, fy = 60 ksi, 4 - #9 top and 4 - #9 bottom,
      2.5 in from the face to the bar centres.
"""

import math
import sys
import types
from pathlib import Path

import numpy as np
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

import ezdxf  # noqa: E402

from design import column_designer_aci318 as col  # noqa: E402
from design.aci318_config import CODE, override  # noqa: E402

# --------------------------------------------------------------------------
# UNIT CONVERSIONS
# --------------------------------------------------------------------------
IN = 25.4  # mm per inch
KIP = 4.44822  # kN per kip
KIP_FT = 1.355818  # kN-m per kip-ft
KSI = 6.894757  # MPa per ksi

# In this code base, bending "angle 0" means bending about the X axis
# (compression on the top or bottom face, bars arranged in rows).
BENDING_ABOUT_X = 0.0


def close(actual: float, expected: float, tolerance: float = 0.02) -> bool:
    """True when ``actual`` is within ``tolerance`` (2 % default) of ``expected``."""
    return abs(actual - expected) <= tolerance * abs(expected)


# ==========================================================================
# HELPERS THAT BUILD TEST COLUMNS
# ==========================================================================
def build_published_column():
    """The 16x16 in published column with 4 top + 4 bottom bars (SI units)."""
    width = height = 16 * IN
    bar_diameter = 1.128 * IN  # #9
    tie_diameter = 0.375 * IN  # #3
    centre_offset = 2.5 * IN  # face to bar centre
    cover = centre_offset - tie_diameter - bar_diameter / 2  # cover to the tie

    engine = col.ColumnFlexureDesign(
        width, height, 0.0, 5 * KSI, 60 * KSI, 60 * KSI,
        bar_diameter, tie_diameter, cover,
    )
    concrete, steel = engine.define_materials()
    section = engine.define_section(height, width, 0.0, concrete)

    x_positions = [centre_offset + i * (width - 2 * centre_offset) / 3 for i in range(4)]
    layout = [(x, y, 1) for y in (centre_offset, height - centre_offset) for x in x_positions]
    section = engine.add_reinf(section, bar_diameter, steel, bundle_layout=layout)
    return engine, section


def build_rect_column(width=500.0, height=500.0, fc=28.0, fy=415.0, dmain=25.0, dties=10.0,
                      cover=40.0, is_smrf=False):
    """A plain rectangular column engine (no bars yet)."""
    return col.ColumnFlexureDesign(
        width, height, 0.0, fc, fy, fy, dmain, dties, cover,
        shape="rectangular", is_smrf=is_smrf,
    )


def frame_row(name="C1", width=500.0, depth=500.0, diameter=0.0, fc=28.0, fy=415.0, fys=415.0):
    """A row shaped like ETABS FRAME DATA for one column."""
    return pd.Series(
        {"UniqueName": name, "Story": "L1", "SectProp": "C500", "DesignType": "Column",
         "Width": width, "Depth": depth, "Diameter": diameter,
         "f'c": fc, "fy": fy, "fys": fys}
    )


def force_table(name="C1", combos=None, length=3000.0):
    """Mock ETABS column forces: I-end at Station 0, J-end at Station = length.

    ``combos`` maps a combo name to (P, V2, V3, M2, M3) with kN and kN-m.
    """
    combos = combos or {"COMBO1": (1500.0, 40.0, 0.0, 0.0, 120.0)}
    rows = []
    for combo, (p, v2, v3, m2, m3) in combos.items():
        for station in (0.0, length):
            rows.append(
                {"UniqueName": name, "Combo": combo, "Station": station,
                 "P": p, "V2": v2, "V3": v3, "M2": m2, "M3": m3}
            )
    return pd.DataFrame(rows)


# ==========================================================================
# 1. INTERACTION DIAGRAM AGAINST THE PUBLISHED EXAMPLE
# ==========================================================================
class TestPublishedInteractionDiagram:
    """Each row: (label, Pn kip, Mn kip-ft, phi*Mn kip-ft, phi)  -- all published."""

    POINTS = [
        ("fs = 0 (Point 2)",          957, 261, 170, 0.65),
        ("fs = -0.5 fy (Point 3)",    649, 339, 220, 0.65),
        ("balanced (Point 4)",        417, 386, 251, 0.65),
        ("eps_t = 0.005 (Point 5)",   195, 320, 288, 0.90),
        ("pure bending (Point 6)",      0, 238, 214, 0.90),
    ]

    @pytest.mark.parametrize("label, pn, mn, phi_mn, phi", POINTS)
    def test_control_point(self, label, pn, mn, phi_mn, phi):
        engine, section = build_published_column()
        nominal_m, design_m, _, _, phi_used = engine.solve_moment_capacity(
            section, axial_load=pn * KIP * 1000, bending_angle=BENDING_ABOUT_X
        )
        assert close(nominal_m / 1e6, mn * KIP_FT, 0.02), label
        assert close(design_m / 1e6, phi_mn * KIP_FT, 0.02), label
        assert phi_used == pytest.approx(phi, abs=0.01), label

    def test_pure_compression_limits(self):
        """PUBLISHED: Po = 1530 kip, phi*Pn,max = 798 kip (tied: 0.80 * 0.65 * Po)."""
        engine, section = build_published_column()
        _, _, nominal_max, design_max, _ = engine.solve_moment_capacity(section, axial_load=0.0)
        assert close(nominal_max, 0.80 * 1530 * KIP * 1000, 0.01)
        assert close(design_max, 798 * KIP * 1000, 0.01)

    def test_axial_load_above_limit_is_rejected(self):
        """A load larger than the maximum nominal axial strength must raise an error."""
        engine, section = build_published_column()
        with pytest.raises(ValueError):
            engine.solve_moment_capacity(section, axial_load=5000 * KIP * 1000)

    def test_pure_tension_limit(self):
        """PUBLISHED: Pnt = fy*Ast = 480 kip.  Anything beyond must be rejected."""
        engine, section = build_published_column()
        engine.solve_moment_capacity(section, axial_load=-470 * KIP * 1000)
        with pytest.raises(ValueError):
            engine.solve_moment_capacity(section, axial_load=-500 * KIP * 1000)

    def test_capacity_drops_when_column_is_weaker_axis(self):
        """PUBLISHED note: bending about Y has 2 bars in 4 layers -> lower capacity."""
        engine, section = build_published_column()
        about_x = engine.solve_moment_capacity(section, 195 * KIP * 1000, BENDING_ABOUT_X)[0]
        about_y = engine.solve_moment_capacity(section, 195 * KIP * 1000, math.pi / 2)[0]
        assert about_y < about_x


# ==========================================================================
# 1b. AXIAL FORCE SIGN CONVENTION (ETABS: compression is NEGATIVE)
# ==========================================================================
class TestAxialSignConvention:
    def test_etabs_compression_is_flipped_to_positive(self):
        etabs = force_table(combos={"DL": (-1500.0, 0, 0, 0, 100.0), "WIND": (250.0, 0, 0, 0, 80.0)})
        converted = col._to_compression_positive(etabs)
        assert converted["P"].tolist()[:2] == [1500.0, 1500.0]  # compression -> positive
        assert converted["P"].tolist()[2:] == [-250.0, -250.0]  # tension -> negative

    def test_input_table_is_not_modified(self):
        etabs = force_table(combos={"DL": (-1500.0, 0, 0, 0, 100.0)})
        col._to_compression_positive(etabs)
        assert etabs["P"].iloc[0] == -1500.0

    def test_flip_can_be_switched_off_in_config(self):
        positive_compression = override(CODE, conventions__etabs_compression_is_negative=False)
        table = force_table(combos={"DL": (1500.0, 0, 0, 0, 100.0)})
        assert col._to_compression_positive(table, positive_compression)["P"].iloc[0] == 1500.0

    def test_unflipped_etabs_forces_would_be_judged_as_tension(self):
        """Why the flip matters: -1500 kN read as compression-positive is a 1500 kN PULL.

        A pull lowers the moment capacity (about 317 kN-m here) while the same
        load as compression raises it (about 632 kN-m).  A 400 kN-m moment
        therefore fails if the sign is wrong and passes if it is right.
        """
        layout = next(o for o in col._enumerate_column_bar_layouts(build_rect_column(), 60)
                      if sum(c for *_, c in o) == 16 and all(c == 1 for *_, c in o))
        etabs = force_table(combos={"DL": (-1500.0, 10.0, 0.0, 0.0, 400.0)})

        raw_ok, raw_checks, _ = col._evaluate_column_candidate(
            frame_row(), etabs, layout, 25.0, 10.0, 40.0, False)
        fixed_ok, fixed_checks, _ = col._evaluate_column_candidate(
            frame_row(), col._to_compression_positive(etabs), layout, 25.0, 10.0, 40.0, False)

        assert raw_ok is False  # read as a 1500 kN pull, the moment capacity is too low
        assert fixed_ok is True  # the same forces, correctly read as compression, pass
        assert fixed_checks[0]["Pu_kN"] == 1500.0


# ==========================================================================
# 2. CIRCULAR (SPIRAL) COLUMN
# ==========================================================================
class TestCircularColumn:
    def _circular(self):
        engine = col.ColumnFlexureDesign(
            0.0, 0.0, 500.0, 35.0, 420.0, 420.0, 25.0, 10.0, 40.0, shape="circular"
        )
        concrete, steel = engine.define_materials()
        section = engine.define_section(0, 0, 500.0, concrete)
        section = engine.add_reinf(section, 25.0, steel, initial_bars=8)
        return engine, section

    def test_spiral_axial_limit_hand_calc(self):
        """HAND CALC: spiral phi*Pn,max = 0.75 * 0.85 * [0.85 fc'(Ag-Ast) + fy Ast]."""
        engine, section = self._circular()
        _, _, _, design_max, _ = engine.solve_moment_capacity(section, axial_load=0.0)

        bars = engine._calculate_required_bars(8)
        ast = bars * math.pi * 25**2 / 4
        ag = math.pi * 500**2 / 4
        po = 0.85 * 35 * (ag - ast) + 420 * ast
        assert design_max == pytest.approx(0.75 * 0.85 * po, rel=0.005)

    def test_spiral_uses_higher_low_end_phi(self):
        """Spiral columns use 0.75 (not 0.65) when compression-controlled."""
        engine, section = self._circular()
        *_, phi = engine.solve_moment_capacity(section, axial_load=2500e3, bending_angle=0.0)
        assert phi == pytest.approx(0.75)

    def test_minimum_bars_in_circular_smrf(self):
        """ACI 10.7.3.1: at least 6 bars for a spiral / special column."""
        engine = col.ColumnFlexureDesign(0, 0, 400.0, 28, 415, 415, 16, 10, 40,
                                         shape="circular", is_smrf=True)
        assert engine._calculate_required_bars(2) >= 6


# ==========================================================================
# 3. REINFORCEMENT LIMITS AND SPACING
# ==========================================================================
class TestLimitsAndSpacing:
    def test_reinforcement_ratio_between_one_and_eight_percent(self):
        """ACI 10.6.1.1: 1 % <= rho <= 8 %.  500x500 column, 25 mm bars (491 mm2 each).

        4 bars  -> 0.79 %  (too little)      8 bars -> 1.57 %  (ok)
        40 bars -> 7.85 %  (ok)              41 bars -> 8.05 % (too much)
        """
        engine = build_rect_column()
        assert engine.check_reinforcement_limits(4)["status"] == "Fail"
        assert engine.check_reinforcement_limits(8)["status"] == "Pass"
        assert engine.check_reinforcement_limits(40)["status"] == "Pass"
        assert engine.check_reinforcement_limits(41)["status"] == "Fail"

    def test_smrf_ratio_limit_is_six_percent(self):
        """ACI 18.7.4.1: special moment frame columns limited to 6 %."""
        area = math.pi * 25**2 / 4
        bars_for_7_percent = math.ceil(0.07 * 500**2 / area)
        ordinary = build_rect_column(is_smrf=False).check_reinforcement_limits(bars_for_7_percent)
        special = build_rect_column(is_smrf=True).check_reinforcement_limits(bars_for_7_percent)
        assert ordinary["status"] == "Pass"
        assert special["status"] == "Fail"

    def test_tie_spacing_hand_calc(self):
        """HAND CALC (ACI 25.7.2.1): s <= min(16 db, 48 dtie, least dimension).

        25 mm bars, 10 mm ties, 500 mm column -> min(400, 480, 500) = 400 mm.
        """
        limits = build_rect_column().solve_max_spacing()
        assert limits["max_tie_spacing_mm"] == 400.0

    def test_spiral_pitch_limit(self):
        """ACI 25.7.3.1: clear spiral spacing 25..75 mm -> pitch limit = 75 + dtie."""
        engine = col.ColumnFlexureDesign(0, 0, 500.0, 28, 415, 415, 25, 10, 40, shape="circular")
        assert engine.solve_max_spacing()["max_tie_spacing_mm"] == 85.0

    def test_minimum_tie_diameter_depends_on_bar_size(self):
        """ACI 25.7.2.2: 9.5 mm ties up to 32 mm bars, 12.7 mm ties above."""
        assert col._minimum_tie_diameter(build_rect_column(dmain=25.0)) == 9.5
        assert col._minimum_tie_diameter(build_rect_column(dmain=36.0)) == 12.7

    def test_bar_count_respects_150mm_spacing(self):
        """Project rule: no gap between bars larger than 150 mm (centre to centre)."""
        engine = build_rect_column(width=600, height=600)
        n_bars = engine._calculate_required_bars(4)
        core = 600 - 2 * 40 - 2 * 10 - 25  # spacing span between bar centres
        per_side = n_bars // 4
        assert core / per_side <= 150.0
        assert n_bars % 4 == 0  # symmetric layout


# ==========================================================================
# 4. BAR LAYOUT ENUMERATION
# ==========================================================================
class TestBarLayouts:
    def test_perimeter_positions_have_no_duplicate_corners(self):
        """Regression: earlier code drew every corner bar twice."""
        engine = build_rect_column()
        positions = col._perimeter_bar_positions(engine, 3, 3)
        assert len(positions) == 12
        assert len({(round(x, 3), round(y, 3)) for x, y in positions}) == 12

    def test_layouts_are_symmetric(self):
        """Every bar must have a mirror partner about both axes of the column."""
        engine = build_rect_column()
        for layout in col._enumerate_column_bar_layouts(engine, max_bars=40)[:8]:
            points = {(round(x, 3), round(y, 3)) for x, y, _ in layout}
            for x, y in points:
                assert (round(500 - x, 3), y) in points
                assert (x, round(500 - y, 3)) in points

    def test_layouts_are_sorted_from_fewest_bars(self):
        engine = build_rect_column()
        totals = [sum(c for *_, c in layout) for layout in
                  col._enumerate_column_bar_layouts(engine, max_bars=60)]
        assert totals == sorted(totals)

    def test_bundle_size_never_exceeds_four(self):
        """ACI 25.6.1.1: bundles of at most 4 bars."""
        engine = build_rect_column()
        for layout in col._enumerate_column_bar_layouts(engine, max_bars=120):
            assert max(c for *_, c in layout) <= 4

    def test_no_layout_exceeds_bar_budget(self):
        engine = build_rect_column()
        for layout in col._enumerate_column_bar_layouts(engine, max_bars=30):
            assert sum(c for *_, c in layout) <= 30

    def test_layout_summary_text(self):
        summary = col._column_layout_summary([(0, 0, 1), (1, 1, 1), (2, 2, 2)])
        assert summary == "2 single bars; 1 bundles of 2 bars"
        assert col._bundle_histogram(summary) == {1: 2, 2: 1}


# ==========================================================================
# 5. FLEXURE + AXIAL CHECK WITH MOCK ETABS FORCES
# ==========================================================================
class TestCandidateCheck:
    def _layout(self, n_bars_per_side=3, width=500.0, cover=40.0, dties=10.0, dmain=25.0):
        engine = build_rect_column(dmain=dmain, dties=dties, cover=cover)
        options = col._enumerate_column_bar_layouts(engine, max_bars=60)
        return next(o for o in options if sum(c for *_, c in o) == 4 * (n_bars_per_side - 1)
                    and all(c == 1 for *_, c in o))

    def test_light_load_passes(self):
        layout = self._layout(4)
        ok, checks, limits = col._evaluate_column_candidate(
            frame_row(), force_table(combos={"L": (800.0, 10.0, 0.0, 0.0, 60.0)}),
            layout, 25.0, 10.0, 40.0, False,
        )
        assert ok is True
        assert all(c["Strength_Check"] == "PASS" for c in checks)
        assert len(checks) == 2  # one combo x two ends (I and J)

    def test_huge_moment_fails(self):
        layout = self._layout(4)
        ok, checks, _ = col._evaluate_column_candidate(
            frame_row(), force_table(combos={"L": (800.0, 10.0, 0.0, 0.0, 900.0)}),
            layout, 25.0, 10.0, 40.0, False,
        )
        assert ok is False
        assert checks[0]["Flexure_Check"] == "FAIL"

    def test_resultant_of_two_moments_is_used(self):
        """HAND CALC: demand is sqrt(M2^2 + M3^2)."""
        layout = self._layout(4)
        _, checks, _ = col._evaluate_column_candidate(
            frame_row(), force_table(combos={"L": (800.0, 0.0, 0.0, 30.0, 40.0)}),
            layout, 25.0, 10.0, 40.0, False,
        )
        assert checks[0]["Mu_resultant_kNm"] == pytest.approx(50.0)

    def test_more_bars_never_reduce_capacity(self):
        """BEHAVIOUR: phi*Mn is non-decreasing as bars are added (same axial load)."""
        forces = force_table(combos={"L": (1000.0, 0.0, 0.0, 0.0, 50.0)})
        capacities = []
        for per_side in (4, 5, 6):
            layout = self._layout(per_side)
            _, checks, _ = col._evaluate_column_candidate(
                frame_row(), forces, layout, 25.0, 10.0, 40.0, False)
            capacities.append(checks[0]["phi_Mn_kNm"])
        assert capacities == sorted(capacities)

    def test_tension_column_is_checked_against_bar_yield(self):
        """HAND CALC: phi*Pnt = 0.9 * fy * Ast."""
        layout = self._layout(4)
        _, checks, _ = col._evaluate_column_candidate(
            frame_row(), force_table(combos={"T": (-500.0, 0.0, 0.0, 0.0, 0.0)}),
            layout, 25.0, 10.0, 40.0, False)
        n_bars = sum(c for *_, c in layout)
        expected = 0.9 * n_bars * math.pi * 25**2 / 4 * 415 / 1000
        assert checks[0]["phi_Pn_tension_kN"] == pytest.approx(expected, rel=1e-6)


# ==========================================================================
# 6. SMRF TRANSVERSE (CONFINEMENT) DESIGN - ACI 18.7.5.4
# ==========================================================================
class TestSmrfConfinement:
    def _numbers(self, width=500.0, pu_n=0.0, fc=28.0, fyt=415.0):
        engine = build_rect_column(width=width, height=width, fc=fc, is_smrf=True)
        engine.fyt = fyt
        ag = width**2
        ach = (width - 2 * 40.0) ** 2  # outside-to-outside of the hoop
        return engine, ag, ach

    def test_required_ratio_hand_calc_low_axial_load(self):
        """HAND CALC: Ash/(s bc) >= max( 0.3(Ag/Ach - 1) fc'/fyt , 0.09 fc'/fyt )."""
        engine, ag, ach = self._numbers()
        result = col._smrf_transverse_design(engine, 12, max_compression=1000e3,
                                             spacing_limit=100.0)
        expected_a = 0.3 * (ag / ach - 1) * 28 / 415
        expected_b = 0.09 * 28 / 415
        assert result["Confinement_18_7_5_a"] == pytest.approx(expected_a)
        assert result["Confinement_18_7_5_b"] == pytest.approx(expected_b)
        assert result["Required_Ash_s_Ratio_X"] == pytest.approx(max(expected_a, expected_b))
        assert np.isnan(result["Confinement_18_7_5_c"])  # low axial load: (c) not needed

    def test_high_axial_load_activates_expression_c(self):
        """ACI Table 18.7.5.4: (c) applies when Pu > 0.3 Ag fc'.

        HAND CALC: 0.2 * kf * kn * Pu / (fyt * Ach), with kf = fc'/175 + 0.6 (>= 1)
        and kn = nl/(nl - 2).
        """
        engine, ag, ach = self._numbers()
        pu = 0.4 * ag * 28  # above the 0.3 Ag fc' trigger, in N
        n_bars = 12
        result = col._smrf_transverse_design(engine, n_bars, pu, spacing_limit=100.0)
        kf = max(1.0, 28 / 175 + 0.6)
        kn = n_bars / (n_bars - 2)
        assert result["Confinement_18_7_5_c"] == pytest.approx(0.2 * kf * kn * pu / (415 * ach))
        assert result["High_Axial_or_High_fc_Check"] == "APPLIED"

    def test_spacing_obeys_all_limits(self):
        """ACI 18.7.5.3: s <= min(1/4 least dimension, 6 db, so), so = 100..150 mm."""
        engine, _, _ = self._numbers()
        result = col._smrf_transverse_design(engine, 12, 1000e3, spacing_limit=400.0)
        so = 100 + (350 - 150) / 3  # hx = 150 mm  -> 166.7, capped at 150
        limit = min(0.25 * 500, 6 * 25, min(150.0, so))
        assert result["Transverse_Spacing_Provided_mm"] == pytest.approx(limit)
        assert result["Transverse_Spacing_Check"] == "PASS"

    def test_more_legs_needed_for_wider_column(self):
        """BEHAVIOUR: a wider section needs at least as many hoop legs."""
        narrow, _, _ = self._numbers(width=400.0)
        wide, _, _ = self._numbers(width=800.0)
        narrow_legs = col._smrf_transverse_design(narrow, 12, 1000e3, 100.0)["Transverse_Legs_Per_Direction"]
        wide_legs = col._smrf_transverse_design(wide, 20, 1000e3, 100.0)["Transverse_Legs_Per_Direction"]
        assert wide_legs >= narrow_legs

    def test_spiral_ratio_hand_calc(self):
        """HAND CALC (spiral): rho_s >= max(0.45(Ag/Ach - 1) fc'/fyt, 0.12 fc'/fyt)."""
        engine = col.ColumnFlexureDesign(0, 0, 600.0, 28, 415, 415, 25, 12, 40,
                                         shape="circular", is_smrf=True)
        result = col._smrf_transverse_design(engine, 12, 1000e3, spacing_limit=80.0)
        ag = math.pi * 600**2 / 4
        ach = math.pi * (600 - 2 * 40) ** 2 / 4
        expected = max(0.45 * (ag / ach - 1) * 28 / 415, 0.12 * 28 / 415)
        assert result["Required_Confinement_Ratio"] == pytest.approx(expected)

    def test_alternate_bars_get_lateral_support(self):
        """Every second bar on a face needs a hook/leg: 5 bars per face -> 4 legs."""
        engine = build_rect_column(width=600, height=600, is_smrf=True)
        layout = [layout for layout in col._enumerate_column_bar_layouts(engine, 40)
                  if sum(c for *_, c in layout) == 16 and all(c == 1 for *_, c in layout)][0]
        legs, added, message = col._post_check_alternating_support(engine, layout, 2)
        assert legs >= 3 and added == legs - 2
        assert message.startswith("PASS")


# ==========================================================================
# 7. COLUMN SHEAR
# ==========================================================================
class TestColumnShear:
    def _run(self, is_smrf, forces=None, spacing=150.0, legs=2):
        engine = build_rect_column(is_smrf=is_smrf)
        layout = col._enumerate_column_bar_layouts(engine, 40)[3]
        n_bars = sum(c for *_, c in layout)
        return col._column_shear_checks(
            frame_row(), forces if forces is not None else force_table(),
            engine, n_bars, spacing, legs, is_smrf, bundle_layout=layout)

    def test_ordinary_column_concrete_shear_hand_calc(self):
        """HAND CALC (ACI 22.5.6.1): Vc = 0.17 (1 + Nu/14Ag) sqrt(fc') b d.

        Nu = 1500 kN, Ag = 250000 mm2, d = 500 - 40 - 10 - 12.5 = 437.5 mm.
        """
        rows, _ = self._run(is_smrf=False)
        d = 500 - 40 - 10 - 25 / 2
        expected = 0.17 * (1 + 1500e3 / (14 * 500**2)) * math.sqrt(28) * 500 * d / 1000
        assert rows[0]["Vc_kN"] == pytest.approx(expected, rel=1e-6)

    def test_smrf_ignores_concrete_shear(self):
        rows, _ = self._run(is_smrf=True)
        assert all(r["Vc_kN"] == 0.0 for r in rows)
        assert all(r["Concrete_Shear_Strength_Neglected"] for r in rows)

    def test_smrf_design_shear_is_capacity_based(self):
        """ACI 18.7.6.1: design shear is at least (Mpr,top + Mpr,bottom)/ln."""
        rows, _ = self._run(is_smrf=True)
        for row in rows:
            assert row["Design_Shear_kN"] >= row["Analysis_Shear_kN"]
            assert row["Design_Shear_kN"] == pytest.approx(
                max(row["Analysis_Shear_kN"], row["Capacity_Based_Ve_kN"]))
        assert rows[0]["Capacity_Based_Ve_kN"] > 40.0  # much larger than the analysis shear

    def test_more_legs_never_lower_shear_capacity(self):
        few, _ = self._run(is_smrf=False, legs=2)
        many, _ = self._run(is_smrf=False, legs=6)
        assert many[0]["phi_Vn_kN"] >= few[0]["phi_Vn_kN"]

    def test_huge_shear_fails_and_asks_for_more_legs(self):
        forces = force_table(combos={"BIG": (1500.0, 1200.0, 0.0, 0.0, 120.0)})
        rows, required_legs = self._run(is_smrf=False, forces=forces)
        assert required_legs > 2


# ==========================================================================
# 8. SMRF JOINT: STRONG-COLUMN / WEAK-BEAM AND JOINT SHEAR (mock ETABS model)
# ==========================================================================
def build_joint_model(beam_top_bars=4, beam_bottom_bars=3, column_bars_layout_index=3):
    """One interior joint: columns above and below, beams left and right.

    Geometry (mm):  joint P1 at (0, 0, 3000);  columns run to z = 0 and z = 6000;
    beams run to x = -6000 and x = +6000.  Everything is on the X axis.
    """
    points = {
        "P0": np.array([0.0, 0.0, 0.0]), "P1": np.array([0.0, 0.0, 3000.0]),
        "P2": np.array([0.0, 0.0, 6000.0]), "P3": np.array([6000.0, 0.0, 3000.0]),
        "P4": np.array([-6000.0, 0.0, 3000.0]),
    }
    connectivity = pd.DataFrame(
        [("C1", "Column", "P0", "P1"), ("C2", "Column", "P1", "P2"),
         ("B1", "Beam", "P1", "P3"), ("B2", "Beam", "P4", "P1")],
        columns=["UniqueName", "DesignType", "UniquePtI", "UniquePtJ"],
    )
    frame_data = pd.DataFrame(
        [frame_row("C1").to_dict(), frame_row("C2").to_dict(),
         {"UniqueName": "B1", "DesignType": "Beam", "Width": 300.0, "Depth": 600.0,
          "f'c": 28.0, "fy": 415.0, "fys": 415.0, "Diameter": np.nan},
         {"UniqueName": "B2", "DesignType": "Beam", "Width": 300.0, "Depth": 600.0,
          "f'c": 28.0, "fy": 415.0, "fys": 415.0, "Diameter": np.nan}]
    )
    # Forces: columns 3000 mm long carry 1500 kN axial; beams only need a Station range.
    combos = {"COMBO1": (1500.0, 40.0, 0.0, 0.0, 120.0)}
    loads = pd.concat(
        [force_table("C1", combos), force_table("C2", combos),
         force_table("B1", {"COMBO1": (0.0, 50.0, 0.0, 0.0, 100.0)}, 6000.0),
         force_table("B2", {"COMBO1": (0.0, 50.0, 0.0, 0.0, 100.0)}, 6000.0)],
        ignore_index=True,
    )
    beam_rows = []
    for name in ("B1", "B2"):
        for face, n_bars in (("TOP", beam_top_bars), ("BOTTOM", beam_bottom_bars)):
            beam_rows.append(
                {"UniqueName": name, "Face": face, "Width": 300.0, "Depth": 600.0,
                 "f'c": 28.0, "fy": 415.0, "dm": 25.0, "ds": 10.0, "cc": 40.0,
                 "n_left_L1": n_bars, "n_left_L2": 0, "n_left_L3": 0,
                 "n_right_L1": n_bars, "n_right_L2": 0, "n_right_L3": 0}
            )
    engine = build_rect_column(is_smrf=True)
    layouts = col._enumerate_column_bar_layouts(engine, 40)
    layout = layouts[column_bars_layout_index]
    n_bars = sum(c for *_, c in layout)
    column_results = pd.DataFrame(
        [{"UniqueName": name, "Longitudinal_Bars": n_bars, "Main_Bar_mm": 25.0,
          "Longitudinal_Bar_Layout": col._column_layout_summary(layout)}
         for name in ("C1", "C2")]
    )
    return dict(
        column_results=column_results, connectivity=connectivity, frame_data=frame_data,
        factored_loads=loads, beam_design=pd.DataFrame(beam_rows),
        point_coordinates=points, frame_angles={"C1": 0.0, "C2": 0.0},
        is_smrf=True, dmain=25.0, dties=10.0, cover=40.0,
        column_layouts={"C1": layout, "C2": layout},
    )


class TestSmrfJoint:
    def test_joint_rows_are_produced_for_both_sway_directions(self):
        result = col._evaluate_smrf_joints(**build_joint_model())
        assert set(result["Sway_Direction"]) == {"Positive", "Negative"}
        assert set(result["Joint_Point"]) == {"P1"}

    def test_ratio_equals_column_over_beam_capacity(self):
        result = col._evaluate_smrf_joints(**build_joint_model())
        for _, row in result.iterrows():
            assert row["Column_Beam_Ratio"] == pytest.approx(
                row["Sum_Column_Mn_kNm"] / row["Sum_Beam_Mn_kNm"])

    def test_strong_columns_weak_beams_passes(self):
        result = col._evaluate_smrf_joints(**build_joint_model(beam_top_bars=3, beam_bottom_bars=2,
                                                               column_bars_layout_index=8))
        assert (result["Strong_Column_Check"] == "PASS").all()

    def test_heavy_beams_fail_the_ratio(self):
        result = col._evaluate_smrf_joints(**build_joint_model(beam_top_bars=9, beam_bottom_bars=9,
                                                               column_bars_layout_index=0))
        assert (result["Strong_Column_Check"] == "FAIL").any()

    def test_more_beam_steel_lowers_the_ratio(self):
        """BEHAVIOUR: stronger beams -> smaller column/beam ratio."""
        light = col._evaluate_smrf_joints(**build_joint_model(beam_top_bars=3, beam_bottom_bars=3))
        heavy = col._evaluate_smrf_joints(**build_joint_model(beam_top_bars=8, beam_bottom_bars=8))
        assert heavy["Column_Beam_Ratio"].min() < light["Column_Beam_Ratio"].min()

    def test_joint_shear_capacity_hand_calc(self):
        """HAND CALC (ACI 18.8.4.1): phi*Vn = 0.85 * coeff * sqrt(fc') * Aj.

        Joint confined on two opposite faces by the beams -> coefficient 1.2.
        Joint depth = 500 mm, effective width = min(500, 300 + 500) = 500 mm.
        """
        result = col._evaluate_smrf_joints(**build_joint_model())
        row = result.iloc[0]
        aj = 500 * 500
        expected = 0.85 * row["Nominal_Joint_Shear_Coefficient"] * math.sqrt(28) * aj / 1000
        assert row["Joint_Area_mm2"] == pytest.approx(aj)
        assert row["phi_Vn_kN"] == pytest.approx(expected)
        assert row["Nominal_Joint_Shear_Coefficient"] == pytest.approx(1.2)

    def test_column_shear_comes_from_beam_probable_moments(self):
        """HAND CALC (ACI 18.8.2.1, R18.8.2): joint column shear from the BEAMS.

        Sway one way: left beam hogs (4 top bars), right beam sags (3 bottom bars).
            Mpr = As * 1.25 fy * (d - a/2),   a = As * 1.25 fy / (0.85 fc' b)
            Vcol = (Mpr_left + Mpr_right) / (half column heights) = sum Mpr / 3.0 m
            Vu,joint = T_left + C_right - Vcol,  with T = C = As * 1.25 fy
        (The hand calculation ignores compression steel, so 4 % tolerance.)
        """
        def mpr_and_force(n_bars):
            area = n_bars * math.pi * 25**2 / 4
            fs = 1.25 * 415
            a = area * fs / (0.85 * 28 * 300)
            d = 600 - 40 - 10 - 25 / 2
            return area * fs * (d - a / 2) / 1e6, area * fs / 1000

        mpr_top, force_top = mpr_and_force(4)
        mpr_bottom, force_bottom = mpr_and_force(3)
        expected_vcol = (mpr_top + mpr_bottom) * 1000 / (0.5 * 3000 + 0.5 * 3000)
        expected_demand = abs(force_top + force_bottom - expected_vcol)

        row = col._evaluate_smrf_joints(**build_joint_model()).iloc[0]
        assert close(row["Capacity_Based_Column_Shear_kN"], expected_vcol, 0.04)
        assert close(row["Beam_Tension_Force_kN"], force_top + force_bottom, 0.001)
        assert close(row["Joint_Shear_Demand_kN"], expected_demand, 0.04)

    def test_joint_shear_demand_does_not_depend_on_column_steel(self):
        """BEHAVIOUR: stronger columns must not reduce the joint shear demand.

        (Bug fixed: the column shear used to come from the COLUMN capacity, so
        more column bars wrongly lowered the joint shear.)
        """
        few_bars = col._evaluate_smrf_joints(**build_joint_model(column_bars_layout_index=3))
        many_bars = col._evaluate_smrf_joints(**build_joint_model(column_bars_layout_index=8))
        assert few_bars["Joint_Shear_Demand_kN"].tolist() == pytest.approx(
            many_bars["Joint_Shear_Demand_kN"].tolist())

    def test_missing_beam_design_is_reported_not_crashed(self):
        model = build_joint_model()
        model["beam_design"] = model["beam_design"][model["beam_design"]["UniqueName"] != "B2"]
        result = col._evaluate_smrf_joints(**model)
        assert result["Strong_Column_Check"].str.startswith("BLOCKED").all()

    def test_non_smrf_frames_skip_joint_checks(self):
        model = build_joint_model()
        model["is_smrf"] = False
        assert col._evaluate_smrf_joints(**model).empty


# ==========================================================================
# 9. LOCAL AXES AND ETABS TABLE HELPERS
# ==========================================================================
class TestGeometryHelpers:
    def test_local_axes_of_a_vertical_column(self):
        """ETABS: local 1 = I to J (up).  Local axes stay perpendicular unit vectors."""
        one, two, three = col._frame_local_axes(
            np.array([0.0, 0, 0]), np.array([0.0, 0, 3000]), 0.0)
        assert np.allclose(one, [0, 0, 1])
        assert abs(np.dot(one, two)) < 1e-9 and abs(np.dot(two, three)) < 1e-9
        assert np.allclose([np.linalg.norm(two), np.linalg.norm(three)], 1.0)

    def test_beta_angle_rotates_axes_2_and_3(self):
        _, two, _ = col._frame_local_axes(np.array([0.0, 0, 0]), np.array([0.0, 0, 3000]), 0.0)
        _, rotated, _ = col._frame_local_axes(np.array([0.0, 0, 0]), np.array([0.0, 0, 3000]), 90.0)
        assert abs(np.dot(two, rotated)) < 1e-9

    def test_same_start_and_end_point_is_rejected(self):
        with pytest.raises(ValueError):
            col._frame_local_axes(np.zeros(3), np.zeros(3), 0.0)

    def test_numeric_ids_from_excel_are_cleaned(self):
        """Excel turns object id 502 into 502.0; both must map to '502'."""
        assert col._normalize_object_name(502.0) == "502"
        assert col._normalize_object_name(" C12 ") == "C12"
        assert col._normalize_object_name(float("nan")) == ""

    def test_common_column_mark(self):
        assert col._common_column_mark("PD2C14A") == "C14A"

    def test_conflicting_frame_rows_are_detected(self):
        frame = pd.DataFrame([frame_row("C1"), frame_row("C1", width=600.0)])
        with pytest.raises(ValueError):
            col._deduplicate_frame_data(frame)

    def test_missing_columns_are_named_in_the_error(self):
        with pytest.raises(ValueError, match="P"):
            col._require_columns(pd.DataFrame({"M2": [1]}), {"P", "M2"}, "FACTORED LOADS")

    def test_story_ordering(self):
        stories = ["L3", "GF", "B1", "L1"]
        assert sorted(stories, key=col._column_story_sort_key) == ["B1", "GF", "L1", "L3"]


# ==========================================================================
# 10. DXF TIE DRAWING - THE OVERLAP RULES
# ==========================================================================
def _tie_bars_for(width=500.0, depth=500.0, n_bars=24, dmain=25.0, dties=10.0, cover=40.0):
    """Hoop + crossties drawn at 1:1 for a rectangular column."""
    engine = build_rect_column(width, depth, dmain=dmain, dties=dties, cover=cover, is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 200)
                  if sum(c for *_, c in l) == n_bars and all(c == 1 for *_, c in l))
    bars = col._hoop_tie_bars(0.0, 0.0, width, depth, cover, dmain, dties, 1.0)
    bars += col._crosstie_bars(layout, 0.0, 0.0, 1.0, dmain, dties)
    return bars, dties


def _visible_map(bars, thickness):
    return [(bar, shape) for bar, shape in col._visible_tie_shapes(bars, thickness)]


@pytest.mark.parametrize("n_bars", [16, 24])
def test_no_two_visible_tie_bars_overlap(n_bars):
    """After stacking, no drawn bar may run through another (no crossing lines)."""
    bars, thickness = _tie_bars_for(n_bars=n_bars)
    visible = _visible_map(bars, thickness)
    tolerance = (0.02 * thickness) * thickness  # a sliver 2 % of the bar width is noise
    for i in range(len(visible)):
        for j in range(i + 1, len(visible)):
            overlap = visible[i][1].intersection(visible[j][1]).area
            assert overlap <= tolerance, f"bars {i} and {j} overlap by {overlap:.3f} mm2"


def test_crossties_sit_on_top_of_hoop():
    """The hoop is hidden wherever a crosstie crosses it, never the other way round."""
    bars, thickness = _tie_bars_for()
    visible = _visible_map(bars, thickness)
    from shapely.geometry import LineString
    hoop_pieces = [(bar, shape) for bar, shape in visible if bar.layer == "TIES"]
    for bar, shape in hoop_pieces:
        full = LineString(bar.path).buffer(thickness / 2, cap_style="flat", join_style="round")
        assert shape.area < full.area  # part of every hoop leg is hidden under something
    for bar, shape in visible:
        if bar.layer == "CROSSTIES":
            full = LineString(bar.path).buffer(thickness / 2, cap_style="flat", join_style="round")
            assert shape.area >= 0.7 * full.area  # crossties keep most of their body


def test_horizontal_crossties_sit_on_top_of_vertical_crossties():
    bars, _ = _tie_bars_for()
    vertical = [b.level for b in bars if b.layer == "CROSSTIES" and col.LEVEL_CROSSTIE_Y < b.level < col.LEVEL_CROSSTIE_X]
    horizontal = [b.level for b in bars if b.layer == "CROSSTIES" and b.level > col.LEVEL_CROSSTIE_X]
    assert vertical and horizontal
    assert min(horizontal) > max(vertical)


def test_hoop_leg_from_the_right_sits_on_top_of_leg_from_the_left():
    bars, _ = _tie_bars_for()
    hoop_pieces = [b for b in bars if b.layer == "TIES"]
    assert len(hoop_pieces) == 2
    left_leg, right_leg = hoop_pieces  # left leg = up the left side, right leg = from the top edge
    assert right_leg.level > left_leg.level


def test_crowded_hook_extensions_are_stacked_in_order():
    """In a crowded section neighbouring crossties get different levels, so one
    hook extension lies on top of the next instead of crossing it."""
    bars, _ = _tie_bars_for(n_bars=24)
    horizontal_levels = sorted(b.level for b in bars if b.level > col.LEVEL_CROSSTIE_X)
    assert len(horizontal_levels) >= 2
    assert len(set(horizontal_levels)) == len(horizontal_levels)


def test_hook_tail_length_rule():
    """ACI 25.3.2: hook extension is 6 db but not less than 75 mm."""
    assert col._hook_tail_length_dxf(10.0) == 75.0
    assert col._hook_tail_length_dxf(16.0) == 96.0


# ==========================================================================
# 11. DXF COLUMN SCHEDULE FILE
# ==========================================================================
def _report_row(name="PD1C1", story="L1", width=500.0, depth=500.0, bars=16):
    engine = build_rect_column(width, depth, is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 200)
                  if sum(c for *_, c in l) == bars and all(c == 1 for *_, c in l))
    return {
        "Unique Name": name, "Column Label": "C1", "Story": story,
        "f′c (MPa)": 28.0, "Width (mm)": width, "Depth (mm)": depth,
        "Diameter (mm)": np.nan, "Longitudinal Bars": bars,
        "Bundle Layout": col._column_layout_summary(layout),
        "Tie Bar Diameter (mm)": 10.0, "Tie / Spiral Spacing (mm)": 100.0,
        "Hoop Legs X": 3, "Hoop Legs Y": 3,
    }


def test_column_schedule_dxf_is_written_and_readable(tmp_path):
    report = pd.DataFrame([_report_row(story="L1"), _report_row("PD2C1", "L2")])
    target = tmp_path / "Column_Schedule.dxf"
    col.generate_dxf_column_schedule(report, str(target), 25.0, 40.0, True)

    document = ezdxf.readfile(target)
    layers = {entity.dxf.layer for entity in document.modelspace()}
    assert {"TABLE", "CONCRETE", "TIES", "CROSSTIES", "LONGITUDINAL"} <= layers


def test_dxf_draws_one_circle_per_longitudinal_bar(tmp_path):
    report = pd.DataFrame([_report_row(bars=16)])
    target = tmp_path / "one.dxf"
    col.generate_dxf_column_schedule(report, str(target), 25.0, 40.0, True)
    circles = [e for e in ezdxf.readfile(target).modelspace()
               if e.dxftype() == "CIRCLE" and e.dxf.layer == "LONGITUDINAL"]
    assert len(circles) == 16  # corners are no longer drawn twice


def test_empty_report_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        col.generate_dxf_column_schedule(pd.DataFrame(), str(tmp_path / "x.dxf"), 25.0, 40.0, True)
