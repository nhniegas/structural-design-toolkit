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
from design.code_config import CODE, override  # noqa: E402

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
        """NSCP 425.7.2.2: 10 mm ties up to 32 mm bars, 12 mm ties above and for bundles."""
        assert col._minimum_tie_diameter(build_rect_column(dmain=25.0)) == 10.0
        assert col._minimum_tie_diameter(build_rect_column(dmain=36.0)) == 12.0
        bundled = [(62.5, 62.5, 2), (437.5, 62.5, 2), (62.5, 437.5, 2), (437.5, 437.5, 2)]
        assert col._minimum_tie_diameter(build_rect_column(dmain=25.0), bundled) == 12.0

    def test_confinement_uses_the_core_to_the_outside_of_the_hoops(self):
        """HAND CALC (ACI 18.7.5.4): 500 x 500, cover 40, fc' 28, fyt 415, Pu low.
        bc = 500 - 2 x 40 = 420 mm, Ach = 420^2.
        (a) 0.3 (250000/176400 - 1) 28/415 = 0.0844;  (b) 0.09 x 28/415 = 0.00607.
        """
        engine = build_rect_column(width=500, height=500, fc=28.0, fy=415.0, is_smrf=True)
        result = col._smrf_transverse_design(engine, 12, 1.0e5, 100.0)
        assert result["Core_Dimension_mm"] == "420.0 x 420.0"
        assert result["Confinement_18_7_5_a"] == pytest.approx(
            0.3 * (250000 / 420**2 - 1) * 28 / 415, rel=1e-6)

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

    def test_ordinary_column_uses_the_analysis_shear(self):
        """Capacity design (Mpr / lu) is for special moment frames only (ACI 18.7.6.1)."""
        rows, _ = self._run(is_smrf=False)
        assert all(r["Capacity_Based_Ve_kN"] == 0.0 for r in rows)
        assert all(r["Design_Shear_kN"] == pytest.approx(r["Analysis_Shear_kN"]) for r in rows)

    def test_axial_tension_lowers_vc(self):
        """HAND CALC (ACI 22.5.7.1): Nu = -500 kN: Vc = 0.17 (1 - 500e3/(3.5 x 250000))
        sqrt(28) x 500 x 437.5 = 84.4 kN."""
        forces = force_table(combos={"T": (-500.0, 40.0, 0.0, 0.0, 50.0)})
        rows, _ = self._run(is_smrf=False, forces=forces)
        expected = 0.17 * (1 - 500e3 / (3.5 * 250000)) * math.sqrt(28) * 500 * 437.5 / 1000
        assert rows[0]["Vc_kN"] == pytest.approx(expected, rel=1e-6)

    def test_shear_beyond_the_vs_limit_fails_instead_of_adding_legs(self):
        """Vs <= 0.66 sqrt(fc') b d (ACI 22.5.1.2): 0.75 x 0.66 x sqrt(28) x 500 x 437.5
        = 573 kN of steel at most, plus Vc. 2000 kN cannot be carried."""
        forces = force_table(combos={"BIG": (1500.0, 2000.0, 0.0, 0.0, 120.0)})
        rows, _ = self._run(is_smrf=False, forces=forces)
        v2 = [r for r in rows if r["Shear_Direction"] == "V2"]
        assert all(r["Shear_Check"].startswith("FAIL") for r in v2)
        assert all(r["Shear_Spacing_Limit_mm"] == pytest.approx(437.5 / 4) for r in v2)

    def test_smrf_ve_is_capped_by_the_beams(self):
        """ACI 18.7.6.1.1: Ve need not exceed the shear from the beams' Mpr at the joints."""
        engine = build_rect_column(is_smrf=True)
        layout = col._enumerate_column_bar_layouts(engine, 40)[3]
        n_bars = sum(c for *_, c in layout)
        limits = {("V2", "I"): 100.0, ("V2", "J"): 100.0}
        rows, _ = col._column_shear_checks(
            frame_row(), force_table(), engine, n_bars, 100.0, 2, True, bundle_layout=layout,
            clear_height=2500.0, beam_moment_limits=limits)
        v2 = [r for r in rows if r["Shear_Direction"] == "V2"]
        assert v2[0]["Capacity_Based_Ve_kN"] == pytest.approx(200.0 / 2.5)
        assert v2[0]["Clear_Height_mm"] == 2500.0

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
def _tie_bars_for(width=600.0, depth=600.0, n_bars=24, dmain=25.0, dties=10.0, cover=40.0):
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


# --------------------------------------------------------------------------
# LOAD-COMBINATION PERMUTATIONS
# --------------------------------------------------------------------------
def test_each_permutation_becomes_its_own_internal_combo():
    """BEHAVIOUR: ETABS permutations are checked separately but named plainly."""
    forces = pd.DataFrame(
        {
            "UniqueName": ["C1"] * 3,
            "Combo": ["ULS 1", "ULS 1", "ULS 2"],
            "Permutation": [1.0, 2.0, 1.0],
            "P": [100.0, 200.0, 300.0],
        }
    )
    expanded, display = col._expand_combo_permutations(forces)
    assert expanded["Combo"].tolist() == ["ULS 1-1", "ULS 1-2", "ULS 2-1"]
    assert display == {"ULS 1-1": "ULS 1", "ULS 1-2": "ULS 1", "ULS 2-1": "ULS 2"}
    assert forces["Combo"].tolist() == ["ULS 1", "ULS 1", "ULS 2"]  # input untouched


def test_tables_without_a_permutation_column_are_left_as_they_are():
    """BEHAVIOUR: workbooks extracted before the Permutation column still work."""
    forces = pd.DataFrame({"UniqueName": ["C1"], "Combo": ["ULS 1"], "P": [1.0]})
    expanded, display = col._expand_combo_permutations(forces)
    assert expanded["Combo"].tolist() == ["ULS 1"]
    assert display == {"ULS 1": "ULS 1"}


def test_report_keeps_the_governing_permutation_under_the_plain_combo_name():
    """BEHAVIOUR: a failing permutation governs; otherwise the highest utilization."""
    display = {"ULS 1-1": "ULS 1", "ULS 1-2": "ULS 1", "ULS 1-3": "ULS 1"}
    checks = pd.DataFrame(
        {
            "UniqueName": ["C1"] * 5,
            "Combo": ["ULS 1-1", "ULS 1-2", "ULS 1-3", "ULS 1-1", "ULS 1-2"],
            "End": ["I", "I", "I", "J", "J"],
            "Pu_kN": [10.0, 20.0, 30.0, 40.0, 50.0],
            "Flexure_Utilization": [0.9, 0.4, 1.3, 0.2, 0.6],
            "Strength_Check": ["PASS", "PASS", "FAIL", "PASS", "PASS"],
        }
    )
    shear = pd.DataFrame({"UniqueName": ["C1"], "Combo": ["ULS 1-2"], "End": ["I"]})
    joints = pd.DataFrame({"Load_Combo": ["ULS 1-3"], "Column_Beam_Ratio": [1.5]})

    load, shear, joints = col._collapse_combo_permutations(checks, shear, joints, display)

    assert load[["Combo", "End", "Pu_kN"]].values.tolist() == [
        ["ULS 1", "I", 30.0],  # the failing permutation
        ["ULS 1", "J", 50.0],  # the highest utilization
    ]
    assert shear["Combo"].tolist() == ["ULS 1"]
    assert joints["Load_Combo"].tolist() == ["ULS 1"]


# --------------------------------------------------------------------------
# CIRCULAR COLUMNS IN THE SCHEDULE, SECTION REUSE, CONFIG VALUES
# --------------------------------------------------------------------------
def test_circular_column_is_drawn_with_a_circular_concrete_outline(tmp_path):
    """BEHAVIOUR: a circular column must not be drawn as a square."""
    engine = col.ColumnFlexureDesign(
        width=0.0, height=0.0, diameter=600.0, dmain=25.0, dties=10.0, cc=40.0,
        shape="circular", is_smrf=True,
    )
    layout = col._enumerate_column_bar_layouts(engine, 60)[0]
    row = {
        "Unique Name": "PD1C9", "Column Label": "C9", "Story": "L1",
        "f′c (MPa)": 28.0, "Width (mm)": np.nan, "Depth (mm)": np.nan,
        "Diameter (mm)": 600.0, "Longitudinal Bars": sum(c for *_, c in layout),
        "Bundle Layout": col._column_layout_summary(layout),
        "Tie Bar Diameter (mm)": 10.0, "Tie / Spiral Spacing (mm)": 75.0,
    }
    target = tmp_path / "circular.dxf"
    col.generate_dxf_column_schedule(pd.DataFrame([row]), str(target), 25.0, 40.0, True)

    concrete = [e for e in ezdxf.readfile(target).modelspace() if e.dxf.layer == "CONCRETE"]
    assert [entity.dxftype() for entity in concrete] == ["CIRCLE"]


def test_analysis_section_is_built_once_and_reused():
    """BEHAVIOUR: repeated capacity checks of one section do not re-mesh it."""
    engine = build_rect_column(400.0, 400.0)
    concrete, steel = engine.define_materials()
    geometry = engine.add_reinf(
        engine.define_section(400.0, 400.0, 0.0, concrete), engine.dmain, steel, 8
    )
    first = col._concrete_section_for(geometry)
    assert col._concrete_section_for(geometry) is first
    assert col._concrete_section_for(first) is first

    capacity_low = engine.solve_moment_capacity(geometry, axial_load=200e3, bending_angle=0.0)
    capacity_high = engine.solve_moment_capacity(geometry, axial_load=800e3, bending_angle=0.0)
    assert col._concrete_section_for(geometry) is first
    assert capacity_low[0] != capacity_high[0]  # the axial load still matters


def test_material_models_use_megapascals_throughout():
    """HAND CALC: Ec = 4700*sqrt(28) = 24 870 MPa, the same unit as Es = 200 000 MPa."""
    concrete, steel = build_rect_column(400.0, 400.0).define_materials()
    assert concrete.stress_strain_profile.elastic_modulus == pytest.approx(
        4700.0 * math.sqrt(28.0)
    )
    assert steel.stress_strain_profile.elastic_modulus == 200_000.0


# ==========================================================================
# 12. BUNDLED BARS IN THE SCHEDULE DRAWING
# ==========================================================================
def _site(count, corner=False, shaft_side=-1.0):
    """A bottom-face position (or the bottom-left corner) holding ``count`` bars."""
    if corner:
        return col.BarSite(0.0, 0.0, count, (1.0, 0.0), (0.0, 1.0), is_corner=True)
    return col.BarSite(0.0, 0.0, count, (1.0, 0.0), (0.0, 1.0), shaft_side=shaft_side)


def _offsets(site, diameter=20.0):
    return [(round(x, 3), round(y, 3)) for x, y in col._bundle_bar_offsets(site, diameter)]


def test_two_bars_on_a_face_are_stacked_toward_the_core():
    assert _offsets(_site(2)) == [(0.0, 0.0), (0.0, 20.0)]


def test_two_bars_at_a_corner_sit_on_the_diagonal():
    """BEHAVIOUR: the second bar is one bar diameter away along the 45-degree line."""
    first, second = _offsets(_site(2, corner=True))
    assert first == (0.0, 0.0)
    assert second == (round(20.0 / math.sqrt(2.0), 3),) * 2


def test_three_bars_form_an_l_shape():
    """Corner: one bar along each face. Face: two along the face, third behind the
    bar on the tie-shaft side."""
    assert _offsets(_site(3, corner=True)) == [(0.0, 0.0), (20.0, 0.0), (0.0, 20.0)]
    assert _offsets(_site(3, shaft_side=-1.0)) == [(-10.0, 0.0), (10.0, 0.0), (-10.0, 20.0)]
    assert _offsets(_site(3, shaft_side=1.0)) == [(10.0, 0.0), (-10.0, 0.0), (10.0, 20.0)]


def test_three_bars_on_a_circular_column_form_a_triangle():
    """BEHAVIOUR: with no tie shaft to clear, the third bar sits between the other two."""
    engine = col.ColumnFlexureDesign(0.0, 0.0, 700.0, dmain=25.0, dties=10.0, cc=40.0,
                                     shape="circular", is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 60) if l[0][2] == 3)
    for site in col._circular_bar_sites(layout):
        first, second, third = col._bundle_bar_offsets(site, 25.0)
        assert math.dist(first, second) == pytest.approx(25.0)
        assert math.dist(first, third) == pytest.approx(25.0)   # touches both outer bars
        assert math.dist(second, third) == pytest.approx(25.0)
        # the third bar is on the line from the position to the column centre
        assert third[0] * site.along[0] + third[1] * site.along[1] == pytest.approx(0.0, abs=1e-9)
        assert third[0] * site.inward[0] + third[1] * site.inward[1] > 0


def test_four_bars_form_a_square():
    assert sorted(_offsets(_site(4, corner=True))) == [(0.0, 0.0), (0.0, 20.0), (20.0, 0.0), (20.0, 20.0)]
    assert sorted(_offsets(_site(4))) == [(-10.0, 0.0), (-10.0, 20.0), (10.0, 0.0), (10.0, 20.0)]


def test_bars_of_a_bundle_touch_but_never_overlap():
    for count in (2, 3, 4):
        for site in (_site(count), _site(count, corner=True)):
            centres = col._bundle_bar_offsets(site, 20.0)
            gaps = [math.dist(a, b) for i, a in enumerate(centres) for b in centres[i + 1:]]
            assert min(gaps) == pytest.approx(20.0)


def test_bar_layout_text_round_trips():
    layout = [(62.5, 62.5, 3), (250.0, 62.5, 1), (437.5, 62.5, 2)]
    assert col._decode_bar_layout(col._encode_bar_layout(layout)) == layout
    assert col._decode_bar_layout(None) is None
    assert col._decode_bar_layout(float("nan")) is None


def test_drawing_uses_the_designed_layout_not_a_lookalike():
    """BEHAVIOUR: two layouts with the same bar total and bundle summary are told apart.

    A 500 x 800 column has layouts with more bars on the long faces and others
    with more on the short faces. The schedule must draw the one the design chose.
    """
    engine = build_rect_column(500.0, 800.0, is_smrf=True)
    layouts = col._enumerate_column_bar_layouts(engine, 200)
    by_summary = {}
    for layout in layouts:
        key = (sum(c for *_, c in layout), col._column_layout_summary(layout))
        by_summary.setdefault(key, []).append(layout)
    twins = next(group for group in by_summary.values() if len(group) > 1)
    designed = twins[-1]  # not the first match, which the old lookup would return

    row = pd.Series({
        "Unique Name": "PD1C1", "Width (mm)": 500.0, "Depth (mm)": 800.0,
        "Diameter (mm)": np.nan, "f′c (MPa)": 28.0,
        "Longitudinal Bars": sum(c for *_, c in designed),
        "Bundle Layout": col._column_layout_summary(designed),
        "Bar Layout Data (x, y, n)": col._encode_bar_layout(designed),
    })
    drawn = col._column_layout_from_report(row, 25.0, 10.0, 40.0, True)
    rounded = lambda layout: sorted((round(x, 2), round(y, 2), c) for x, y, c in layout)
    assert rounded(drawn) == rounded(designed)
    assert rounded(drawn) != rounded(twins[0])


def test_reports_without_layout_data_still_draw():
    """Older reports have only the bundle summary; the lookup falls back to it."""
    row = pd.Series(_report_row())
    assert "Bar Layout Data (x, y, n)" not in row
    layout = col._column_layout_from_report(row, 25.0, 10.0, 40.0, True)
    assert sum(c for *_, c in layout) == 16


@pytest.mark.parametrize("count", [3, 4])
def test_bundle_of_three_or_four_gets_a_flat_run_before_the_hook(count):
    """The tie turns sharply, runs flat across the two outer bars, then bends 45
    degrees toward the core, so the extension leaves the bundle diagonally."""
    points = col._crosstie_end_points(
        bar=(100.0, 50.0), count=count, outward=(0.0, -1.0), toward_tail=(1.0, 0.0),
        bar_diameter=20.0, bend_radius=15.0, tail_length=75.0,
    )
    corner, flat_end = points[0], points[1]
    assert corner == pytest.approx((75.0, 35.0))    # beside the first bar, at the outer face
    assert flat_end == pytest.approx((110.0, 35.0))  # past the second bar, same level: flat
    tip, before_tip = points[-1], points[-2]
    heading = math.degrees(math.atan2(tip[1] - before_tip[1], tip[0] - before_tip[0]))
    assert heading == pytest.approx(45.0)            # into the core (+y) and away from the shaft (+x)
    assert math.dist(tip, before_tip) == pytest.approx(75.0)


def test_single_bar_keeps_the_normal_hook():
    points = col._crosstie_end_points(
        bar=(100.0, 50.0), count=1, outward=(0.0, -1.0), toward_tail=(1.0, 0.0),
        bar_diameter=20.0, bend_radius=15.0, tail_length=75.0,
    )
    assert points[0] == pytest.approx((85.0, 50.0))  # shaft beside the bar, no flat run
    assert all(math.dist(point, (100.0, 50.0)) == pytest.approx(15.0) for point in points[:-1])


def _section_parts(layout, width, depth, dmain, dties, cover, style):
    """Tie bodies and bar circles of one drawn section, at 1:1."""
    from shapely.geometry import LineString, Point

    sites = col._rect_bar_sites(layout, style)
    hook_corner = min((s for s in sites if s.is_corner), key=lambda s: (s.x, -s.y))
    ties = col._hoop_tie_bars(
        0.0, 0.0, width, depth, cover, dmain, dties, 1.0, hook_corner_count=hook_corner.count
    ) + col._crosstie_bars(layout, 0.0, 0.0, 1.0, dmain, dties, style)
    bodies = [
        LineString(tie.path).buffer(dties / 2.0, cap_style="flat", join_style="mitre")
        for tie in ties
    ]
    bars = [
        Point(site.x + dx, site.y + dy).buffer(dmain / 2.0, 32)
        for site in sites
        for dx, dy in col._bundle_bar_offsets(site, dmain)
    ]
    return ties, bodies, bars


@pytest.mark.parametrize("style", col.INNER_TIE_STYLES)
@pytest.mark.parametrize(
    "width, depth, dmain, dties",
    [(700.0, 700.0, 25.0, 10.0), (500.0, 800.0, 28.0, 12.0), (400.0, 400.0, 20.0, 10.0)],
)
def test_no_tie_is_drawn_through_a_bar(width, depth, dmain, dties, style):
    """BEHAVIOUR: ties only touch the bars, for every layout the design can select
    whose bar positions are at least 110 mm apart.

    Closer than that, a 75 mm hook extension leaving a corner bundle diagonally
    can reach the bundle at the next position; that is a real congestion problem
    the drawing shows rather than hides.
    """
    engine = build_rect_column(width, depth, dmain=dmain, dties=dties, is_smrf=True)
    bar_area = math.pi * dmain**2 / 4.0
    max_bars = int(CODE.column_strength.rho_max_smrf * width * depth / bar_area)
    for layout in col._enumerate_column_bar_layouts(engine, max_bars):
        xs = sorted({round(x, 1) for x, _, _ in layout})
        ys = sorted({round(y, 1) for _, y, _ in layout})
        if min(xs[1] - xs[0], ys[1] - ys[0]) < 110.0:
            continue
        _, bodies, bars = _section_parts(layout, width, depth, dmain, dties, 40.0, style)
        worst = max(body.intersection(bar).area for body in bodies for bar in bars)
        assert worst <= 0.05 * bar_area, col._column_layout_summary(layout)


def test_inner_hoops_replace_pairs_of_crossties():
    """BEHAVIOUR: each closed inner hoop encloses two neighbouring bar positions."""
    engine = build_rect_column(700.0, 700.0, is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 200)
                  if len(l) == 20 and all(c == 1 for *_, c in l))  # 4 interior bars per face
    crossties = col._crosstie_bars(layout, 0.0, 0.0, 1.0, 25.0, 10.0, "crossties")
    hoops = col._crosstie_bars(layout, 0.0, 0.0, 1.0, 25.0, 10.0, "hoops")
    assert len(crossties) == 8          # 4 vertical + 4 horizontal single ties
    assert len(hoops) == 8              # 2 + 2 closed hoops, each drawn as two legs
    xs = [x for x, _ in hoops[1].path]  # right leg of the first vertical hoop
    assert max(xs) - min(xs) > 100.0    # it spans two bar positions, a crosstie does not


def test_unknown_inner_tie_style_is_rejected():
    engine = build_rect_column(500.0, 500.0, is_smrf=True)
    layout = col._enumerate_column_bar_layouts(engine, 60)[0]
    with pytest.raises(ValueError, match="inner_tie_style"):
        col._rect_bar_sites(layout, "spirals")


@pytest.mark.parametrize("style", col.INNER_TIE_STYLES)
def test_schedule_draws_every_bar_of_a_bundled_layout(tmp_path, style):
    engine = build_rect_column(700.0, 700.0, is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 200)
                  if max(c for *_, c in l) == 3)
    row = _report_row(width=700.0, depth=700.0)
    row.update({
        "Longitudinal Bars": sum(c for *_, c in layout),
        "Bundle Layout": col._column_layout_summary(layout),
        "Bar Layout Data (x, y, n)": col._encode_bar_layout(layout),
    })
    target = tmp_path / f"bundled_{style}.dxf"
    col.generate_dxf_column_schedule(pd.DataFrame([row]), str(target), 25.0, 40.0, True, style)
    document = ezdxf.readfile(target)
    circles = [e for e in document.modelspace()
               if e.dxftype() == "CIRCLE" and e.dxf.layer == "LONGITUDINAL"]
    assert len(circles) == sum(c for *_, c in layout)
    assert len(document.audit().errors) == 0


# ==========================================================================
# 13. SQUARE COLUMNS, LEGS PER EDGE, COLUMN STACKS, REPORT PER AXIS
# ==========================================================================
def _positions_per_edge(layout):
    xs = {round(x, 3) for x, _, _ in layout}
    ys = {round(y, 3) for _, y, _ in layout}
    return len(xs), len(ys)


def test_square_column_gets_the_same_bars_on_every_face():
    """BEHAVIOUR: a square column is never offered an unequal bar arrangement."""
    layouts = col._enumerate_column_bar_layouts(build_rect_column(500.0, 500.0), 80)
    assert layouts
    assert all(nx == ny for nx, ny in map(_positions_per_edge, layouts))


def test_rectangular_column_may_still_have_unequal_faces():
    layouts = col._enumerate_column_bar_layouts(build_rect_column(500.0, 800.0), 80)
    assert any(nx != ny for nx, ny in map(_positions_per_edge, layouts))


def test_support_legs_and_bar_counts_are_given_per_edge():
    """HAND CALC: 4 positions on the X edge need 2 + ceil(2/2) = 3 legs; 6 on the
    Y edge need 2 + ceil(4/2) = 4 legs."""
    engine = build_rect_column(500.0, 800.0, is_smrf=True)
    layout = next(l for l in col._enumerate_column_bar_layouts(engine, 80)
                  if _positions_per_edge(l) == (4, 6) and all(c == 1 for *_, c in l))
    assert col._alternating_support_legs(engine, layout) == (3, 4)
    assert col._bars_per_edge(engine, layout) == (4, 6)


def test_circular_column_has_no_edges():
    engine = col.ColumnFlexureDesign(0.0, 0.0, 600.0, shape="circular")
    layout = col._enumerate_column_bar_layouts(engine, 40)[0]
    assert col._alternating_support_legs(engine, layout) == (1, 1)
    assert col._bars_per_edge(engine, layout) == (None, None)


def test_shear_legs_are_sized_per_direction():
    """BEHAVIOUR: V2 (along the depth) uses the legs counted along the X edge and
    V3 (along the width) the legs counted along the Y edge."""
    engine = build_rect_column(is_smrf=False)
    layout = col._enumerate_column_bar_layouts(engine, 40)[1]
    rows, most = col._column_shear_checks(
        frame_row(), force_table(), engine, sum(c for *_, c in layout), 100.0,
        (9, 7), False, bundle_layout=layout)
    by_direction = {row["Shear_Direction"]: row["Provided_Transverse_Legs"] for row in rows}
    assert by_direction == {"V2": 9, "V3": 7}
    assert most == 9


def test_column_stacks_are_listed_from_the_top_level_down():
    """Columns stand on each other when one's bottom joint is the other's top joint."""
    ends = {
        "GF-C1": ("P0", "P1"), "2F-C1": ("P1", "P2"), "3F-C1": ("P2", "P3"),
        "GF-C2": ("Q0", "Q1"), "2F-C2": ("Q1", "Q2"),
        "GF-C3": ("R0", "R1"),
    }
    stacks = col._column_stacks(ends)
    assert sorted(stacks) == [
        ["2F-C2", "GF-C2"], ["3F-C1", "2F-C1", "GF-C1"], ["GF-C3"],
    ]


def _column_result(name="C1"):
    return {"UniqueName": name, "Story": "2F", "Width_mm": 500.0, "Depth_mm": 500.0,
            "Longitudinal_Bars": 12,
            "Longitudinal_Bar_Layout": "12 single bars", "Design_Status": "PASS"}


def _load_check(end, combo="ULS 1"):
    return {"UniqueName": "C1", "Combo": combo, "End": end, "Pu_kN": 100.0,
            "Mu2_kNm": 1.0, "Mu3_kNm": 2.0, "phi_Mn_kNm": 50.0,
            "Flexure_Utilization": 0.1, "Axial_Check": "PASS", "Flexure_Check": "PASS",
            "Strength_Check": "PASS"}


def _joint(axis, ratio, utilization, sway):
    return {"Load_Combo": "ULS 1", "Column_End_Members": f"C1:J:{axis}",
            "Sway_Direction": sway, "Framing_Beam_Reinforcement": f"beams {axis}",
            "Column_Reinforcement_At_Joint": "C1 (J): 12 bars",
            "Sum_Column_Mn_kNm": 100.0 * ratio, "Sum_Beam_Mn_kNm": 100.0,
            "Column_Beam_Ratio": ratio, "Strong_Column_Check": "PASS" if ratio >= 1.2 else "FAIL",
            "Joint_Shear_Demand_kN": 500.0 * utilization, "phi_Vn_kN": 500.0,
            "Joint_Shear_Utilization": utilization, "Joint_Shear_Check": "PASS"}


def _report(joints):
    report, _ = col._build_consolidated_column_report(
        pd.DataFrame([_column_result()]),
        pd.DataFrame([_load_check("I"), _load_check("J")]),
        pd.DataFrame(),
        pd.DataFrame(joints),
        joint_na_reason="N/A - no beam frames into this end",
    )
    return report


def test_report_lists_the_top_of_the_column_before_the_bottom():
    report = _report([_joint("X", 1.5, 0.4, "Positive")])
    assert report["End"].tolist() == ["J", "I"]


def test_joint_checks_are_reported_for_each_column_axis():
    """BEHAVIOUR: each axis shows its own governing case, with matching values."""
    report = _report([
        _joint("X", 1.5, 0.4, "Positive"), _joint("X", 1.3, 0.6, "Negative"),
        _joint("Y", 2.0, 0.2, "Positive"),
    ])
    top = report.loc[report["End"] == "J"].iloc[0]
    assert top["BCC_Ratio_X"] == pytest.approx(1.3)           # lowest ratio along X
    assert top["Sum_Column_Mn_X_kNm"] == pytest.approx(130.0)  # from the same case
    assert top["BCC_Ratio_Y"] == pytest.approx(2.0)
    assert top["Joint_Shear_Utilization_X"] == pytest.approx(0.6)   # highest along X
    assert top["Joint_Shear_Demand_X_kN"] == pytest.approx(300.0)   # from the same case
    assert top["Joint_Shear_Capacity_X_kN"] == pytest.approx(500.0)
    assert top["Beam_Reinforcement_Y"] == "beams Y"
    assert top["BCC_Status"] == "PASS"


def test_empty_joint_cells_say_why():
    report = _report([_joint("X", 1.5, 0.4, "Positive")])
    top = report.loc[report["End"] == "J"].iloc[0]
    bottom = report.loc[report["End"] == "I"].iloc[0]
    assert top["BCC_Ratio_Y"] == "N/A - no beam frames in along Y"
    assert bottom["BCC_Ratio_X"] == "N/A - no beam frames into this end"
    assert bottom["Joint_Shear_Status"] == "N/A - no beam frames into this end"


# ==========================================================================
# 14. ETABS LOCAL AXES: DEPTH (t3) ALONG LOCAL 2, WIDTH (t2) ALONG LOCAL 3
# ==========================================================================
def _wide_column(width=800.0, depth=600.0):
    """A column wider than it is deep, with the same bar positions on each face."""
    row = frame_row(width=width, depth=depth)
    engine = build_rect_column(width, depth)
    layout = next(
        l for l in col._enumerate_column_bar_layouts(engine, 40)
        if _positions_per_edge(l)[0] == _positions_per_edge(l)[1]
    )
    bars = sum(count for *_, count in layout)
    engine, section = col._build_column_section(
        row, bars, 25.0, 10.0, 40.0, False, bundle_layout=layout)
    return row, engine, section, layout, bars


def _capacity(engine, section, m2, m3):
    angle = col._section_bending_angle(m2, m3)
    return abs(float(engine.solve_moment_capacity(section, 0.0, angle)[0]))


def test_m3_bends_the_section_over_its_depth():
    """ETABS: I33 = t2 * t3^3 / 12, so M3 uses the depth as its lever and M2 the width.

    The same bars sit on every face, so the capacity must be larger over the
    longer dimension: M2 (over the 800 width) above M3 (over the 600 depth).
    """
    _, engine, section, _, _ = _wide_column(800.0, 600.0)
    assert _capacity(engine, section, 1.0, 0.0) > 1.2 * _capacity(engine, section, 0.0, 1.0)


def test_m3_capacity_matches_the_turned_section():
    """An 800 x 600 column under M3 is a 600 x 800 column under M2."""
    _, engine, section, layout, bars = _wide_column(800.0, 600.0)
    turned_layout = [(y, x, count) for x, y, count in layout]
    turned_engine, turned_section = col._build_column_section(
        frame_row(width=600.0, depth=800.0), bars, 25.0, 10.0, 40.0, False,
        bundle_layout=turned_layout)
    assert _capacity(engine, section, 0.0, 1.0) == pytest.approx(
        _capacity(turned_engine, turned_section, 1.0, 0.0), rel=1e-3)


def test_v2_shear_uses_the_depth_as_its_effective_depth():
    """HAND CALC: V2 acts along local 2 (the depth): d = 600 - 40 - 10 - 12.5 = 537.5 mm.
    V3 acts along the width: d = 800 - 62.5 = 737.5 mm."""
    row, engine, _, layout, bars = _wide_column(800.0, 600.0)
    rows, _ = col._column_shear_checks(
        row, force_table(), engine, bars, 100.0, 4, False, bundle_layout=layout)
    vc = {r["Shear_Direction"]: r["Vc_kN"] for r in rows}  # Vc is proportional to b * d
    assert vc["V2"] / vc["V3"] == pytest.approx((800 * 537.5) / (600 * 737.5))


# ==========================================================================
# 15. CALCULATION REPORT (PDF) CONTENT
# ==========================================================================
def _tables(member):
    return {table.title: table for table in member.tables}


def test_column_report_shows_the_governing_combination_per_end():
    """BEHAVIOUR: per end, the combination with the highest utilization is reported."""
    checks = pd.DataFrame([
        {**_load_check("J", "ULS 1"), "Flexure_Utilization": 0.30},
        {**_load_check("J", "ULS 2"), "Flexure_Utilization": 0.80},
        {**_load_check("I", "ULS 1"), "Flexure_Utilization": 0.55},
        {**_load_check("I", "ULS 2"), "Flexure_Utilization": 0.10},
    ])
    report, _ = col._build_consolidated_column_report(
        pd.DataFrame([_column_result()]), checks, pd.DataFrame(),
        pd.DataFrame([_joint("X", 1.5, 0.4, "Positive"), _joint("X", 1.3, 0.6, "Negative")]),
    )
    member = col._column_calc_member(report)
    flexure = _tables(member)["Axial load and flexure - governing combination at each end"]
    assert [(row[0], row[1], row[6]) for row in flexure.rows] == [
        ("Top (J)", "ULS 2", "0.80"), ("Bottom (I)", "ULS 1", "0.55"),
    ]
    assert member.heading == "Column C1 (2F)"
    assert member.summary[-1] == "PASS"


def test_column_report_keeps_the_reason_where_a_joint_check_is_missing():
    report = _report([_joint("X", 1.5, 0.4, "Positive")])
    member = col._column_calc_member(report)
    capacity = _tables(member)["Strong column - weak beam"]
    by_end_axis = {(row[0], row[1]): row for row in capacity.rows}
    assert by_end_axis[("Top (J)", "X")][5] == "1.50"
    assert by_end_axis[("Top (J)", "Y")][2] == "N/A - no beam frames in along Y"


def test_report_sheet_headers_map_back_to_field_names():
    """Every worksheet header must be unique, or the sheet cannot be read back."""
    labels = list(col.COLUMN_REPORT_LABELS.values())
    assert len(labels) == len(set(labels))


def test_column_report_joint_shear_names_no_combination():
    """Joint shear does not depend on the load combination, so none is shown."""
    member = col._column_calc_member(_report([_joint("X", 1.5, 0.4, "Positive")]))
    joint = _tables(member)["Joint shear"]
    assert "Combination" not in joint.header
    by_end_axis = {(row[0], row[1]): row for row in joint.rows}
    assert by_end_axis[("Top (J)", "X")][2:] == ["200.00", "500.00", "0.40", "PASS"]
    assert by_end_axis[("Top (J)", "Y")][-1] == "N/A - no beam frames in along Y"


@pytest.mark.parametrize("name, mark", [
    ("2F - C1", "C1"), ("2F-C7A", "C7A"), ("2GC-5A", "C5A"), ("2-C5A", "C5A"), ("PD1-C12", "C12"), ("PD1GC-12", "C12"),
    ("PD2PC-1A", "PC1A"), ("PD2-PC2A", "PC2A"), ("PC-1", "PC1"), ("TOPGC-3", "C3"), ("1234", "1234"),
])
def test_column_mark_from_unique_name(name, mark):
    """The level is dropped; a planted column keeps its P."""
    assert col._common_column_mark(name) == mark


def _stack_connectivity():
    """Three columns of one stack (UG, 2F, PD1) and one more on 2F and PD1."""
    return pd.DataFrame([
        {"UniqueName": "UG-C1", "UniquePtI": "a0", "UniquePtJ": "a1"},
        {"UniqueName": "2-C1", "UniquePtI": "a1", "UniquePtJ": "a2"},
        {"UniqueName": "PD1-C1", "UniquePtI": "a2", "UniquePtJ": "a3"},
        {"UniqueName": "2-C2", "UniquePtI": "b1", "UniquePtJ": "b2"},
        {"UniqueName": "PD1-C2", "UniquePtI": "b2", "UniquePtJ": "b3"},
    ])


def test_story_order_comes_from_the_stacks_not_the_names():
    """By name, PD1 (the number 1) would sort below 2F. The stack says otherwise."""
    story_of = {"UG-C1": "UG", "2-C1": "2F", "PD1-C1": "PD1", "2-C2": "2F", "PD1-C2": "PD1"}
    assert col._story_order_from_stacks(_stack_connectivity(), story_of) == ["UG", "2F", "PD1"]


def test_story_order_falls_back_to_the_names_without_connectivity():
    story_of = {"a": "3F", "b": "2F"}
    assert col._story_order_from_stacks(pd.DataFrame(), story_of) == ["2F", "3F"]


# ==========================================================================
# 16. BOTTOM-STORY COVER AND THE SCHEDULE TEXT
# ==========================================================================
def _two_story_frame():
    frame = pd.DataFrame([
        {**frame_row("GF-C1", 800.0, 600.0).to_dict(), "Story": "GF"},
        {**frame_row("2-C1", 800.0, 600.0).to_dict(), "Story": "2F"},
        {"UniqueName": "2GX-1", "Story": "2F", "DesignType": "Beam", "Width": 300.0, "Depth": 600.0},
    ])
    connectivity = pd.DataFrame([
        {"UniqueName": "GF-C1", "UniquePtI": "p0", "UniquePtJ": "p1"},
        {"UniqueName": "2-C1", "UniquePtI": "p1", "UniquePtJ": "p2"},
    ])
    return frame, connectivity


def test_bottom_story_cover_can_move_the_bars_inward():
    frame, connectivity = _two_story_frame()
    out, story = col._apply_bottom_story_cover(frame, connectivity, 40.0, "bars")
    by_name = out.set_index("UniqueName")
    assert story == "GF"
    assert by_name.loc["GF-C1", "DesignCover"] == 75.0
    assert pd.isna(by_name.loc["2-C1", "DesignCover"])           # upper story untouched
    assert (by_name.loc["GF-C1", "Width"], by_name.loc["GF-C1", "Depth"]) == (800.0, 600.0)


def test_bottom_story_cover_can_enlarge_the_section_instead():
    """HAND CALC: 75 - 40 = 35 mm more on every face, so 70 mm on each dimension."""
    frame, connectivity = _two_story_frame()
    out, _ = col._apply_bottom_story_cover(frame, connectivity, 40.0, "enlarge")
    by_name = out.set_index("UniqueName")
    assert (by_name.loc["GF-C1", "Width"], by_name.loc["GF-C1", "Depth"]) == (870.0, 670.0)
    assert (by_name.loc["2-C1", "Width"], by_name.loc["2GX-1", "Width"]) == (800.0, 300.0)


def test_no_bottom_story_cover_changes_nothing():
    frame, connectivity = _two_story_frame()
    out, story = col._apply_bottom_story_cover(frame, connectivity, 40.0, "none")
    assert story is None and out is frame


def test_section_uses_the_cover_of_its_own_row():
    """With 75 mm cover the first bar sits at 75 + 10 + 12.5 = 97.5 mm."""
    row = frame_row()
    row["DesignCover"] = 75.0
    engine, _ = col._build_column_section(row, 8, 25.0, 10.0, 40.0, False)
    layout = col._enumerate_column_bar_layouts(engine, 20)[0]
    assert engine.cc == 75.0
    assert min(x for x, _, _ in layout) == pytest.approx(97.5)


def test_schedule_text_follows_the_office_convention(tmp_path):
    """Sizes as 32mm(dia), spacing as @ 100mm; confinement shows the standard 100 mm."""
    report = pd.DataFrame([_report_row()])
    report["Tie / Spiral Spacing (mm)"] = 150.0
    target = tmp_path / "schedule.dxf"
    col.generate_dxf_column_schedule(report, str(target), 25.0, 40.0, True)
    texts = {entity.dxf.text for entity in ezdxf.readfile(target).modelspace().query("TEXT")}
    tie = f"{float(report.iloc[0]['Tie Bar Diameter (mm)']):g}mm%%C"
    assert {"500X500", "16-25mm%%C", f"{tie} @ 100mm", f"{tie} @ 150mm"} <= texts
    assert sum(text == f"{tie} @ 100mm" for text in (
        entity.dxf.text for entity in ezdxf.readfile(target).modelspace().query("TEXT")
    )) == 2      # joint and confinement rows


def test_schedule_floor_level_is_the_range_the_column_spans(tmp_path):
    report = pd.DataFrame([
        _report_row("GF-C1", "GF"), _report_row("2-C1", "2F"), _report_row("PD1-C1", "PD1"),
    ])
    target = tmp_path / "schedule.dxf"
    col.generate_dxf_column_schedule(
        report, str(target), 25.0, 40.0, True, story_order=["GF", "2F", "PD1"])
    texts = {entity.dxf.text for entity in ezdxf.readfile(target).modelspace().query("TEXT")}
    assert {"FDN TO GF", "GF TO 2F", "2F TO PD1"} <= texts


def test_circular_smrf_spiral_detailing_passes():
    """A spiral supports every bar: its 'N/A for continuous spiral' bar-support result
    must not fail the detailing (it made every circular SMRF column fail)."""
    engine = col.ColumnFlexureDesign(0, 0, 700.0, 27.6, 414, 414, 25, 12, 40,
                                     shape="circular", is_smrf=True)
    layout = next(lay for lay in col._enumerate_column_bar_layouts(engine, 24)
                  if sum(c for _, _, c in lay) == 12)
    passes, _, _ = col._column_transverse_candidate_passes(
        pd.Series({"UniqueName": "C1C"}), pd.DataFrame(), engine, layout, 2.0e6, True)
    assert passes is True


def test_column_bars_keep_40mm_and_1_5db_clear():
    """NSCP 425.2.3: clear spacing >= max(40 mm, 1.5 db, 4/3 aggregate).

    HAND CALC: 500 x 500, cover 40, ties 10, bars 25: bar centres span
    500 - 2 x 62.5 = 375 mm. 7 bars a face (6 gaps) are 62.5 mm apart: 37.5 mm
    clear < 40 mm, not allowed. 6 bars a face (5 gaps): 75 - 25 = 50 mm, allowed.
    """
    engine = build_rect_column(500.0, 500.0, dmain=25.0, dties=10.0, cover=40.0)
    single = [sum(c for *_, c in lay) for lay in col._enumerate_column_bar_layouts(engine, 200)
              if all(c == 1 for *_, c in lay)]
    assert 20 in single and 24 not in single
    # 36 mm bars: 1.5 db = 54 mm governs over 40 mm
    assert float(col._column_clear_spacing(36.0, 20.0)) == pytest.approx(54.0)
    assert float(col._column_clear_spacing(20.0, 20.0)) == pytest.approx(40.0)


# ==========================================================================
# JOINT DIMENSIONS (ACI 18.8.2.3 / 18.8.2.4) AND THE TOP-JOINT EXEMPTION (18.7.3.1)
# ==========================================================================
def test_beam_bars_through_a_joint_need_a_column_of_20_db():
    """500 mm column: 25 mm beam bars need 500 mm (passes), 28 mm bars 560 mm (fails)."""
    model = build_joint_model()
    ok = col._evaluate_smrf_joints(**model)
    assert (ok["Joint_Dimension_Check"] == "PASS").all()
    model["beam_design"] = model["beam_design"].assign(dm=28.0)
    big = col._evaluate_smrf_joints(**model)
    assert big["Joint_Shear_Check"].str.contains("18.8.2.3").all()
    assert (big["Joint_Shear_Utilization"] >= 560 / 500 - 1e-9).all()


def _top_joint_model(axial_kN):
    """The joint of build_joint_model with the column above removed."""
    model = build_joint_model()
    model["connectivity"] = model["connectivity"][model["connectivity"]["UniqueName"] != "C2"]
    loads = model["factored_loads"]
    loads = loads[loads["UniqueName"] != "C2"].copy()
    loads.loc[loads["UniqueName"] == "C1", "P"] = axial_kN
    model["factored_loads"] = loads
    return model


def test_strong_column_rule_is_waived_at_a_lightly_loaded_top_joint():
    """0.1 Ag fc' = 0.1 x 500 x 500 x 28 = 700 kN."""
    light = col._evaluate_smrf_joints(**_top_joint_model(400.0))
    assert light["Strong_Column_Check"].str.contains("18.7.3.1").all()
    assert light["Column_Beam_Ratio"].isna().all()
    heavy = col._evaluate_smrf_joints(**_top_joint_model(900.0))
    assert heavy["Strong_Column_Check"].isin(["PASS", "FAIL"]).all()
