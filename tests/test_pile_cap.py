"""
tests/test_pile_cap.py
======================
``sdt pilecap`` (design/concrete/pile_cap.py): the NSCP 2015 strengths against
hand calculations, the pile reactions of a rigid cap, the loads from text and
files, the least-thickness search, the deep-cap checks, and the workflow with
its four dialogs. No dialog opens and no LaTeX is run.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design.concrete import pile_cap as pc  # noqa: E402
from design.concrete import pile_cap_report as report  # noqa: E402
from utilities import _gui_helpers, design_cli  # noqa: E402
from utilities.design_cli import dialog_label  # noqa: E402

ROOT28 = math.sqrt(28.0)


def inputs(**changes) -> dict:
    values = {f.key: f.default for f in pc.INPUTS}
    values.update(changes)
    return values


def weightless(**changes) -> dict:
    return inputs(gamma_c=0.0, soil_h=0.0, surcharge=0.0, tolerance=0.0, **changes)


# --------------------------------------------------------------------------- #
# NSCP strengths (hand calculations)
# --------------------------------------------------------------------------- #
def test_two_way_vc_is_the_least_of_the_three_expressions():
    # (a) 0.33 sqrt(f'c) governs a compact section
    assert pc.two_way_vc(28, 1.0, 40, 700, 4000) == pytest.approx(0.33 * ROOT28)
    # (c) governs a long perimeter: 0.083 (2 + 40 x 700 / 20000) sqrt(f'c)
    assert pc.two_way_vc(28, 1.0, 40, 700, 20000) == pytest.approx(0.083 * 3.4 * ROOT28)
    # (b) governs an elongated column, beta = 4: 0.17 (1 + 2/4) sqrt(f'c)
    assert pc.two_way_vc(28, 4.0, 40, 700, 4000) == pytest.approx(0.17 * 1.5 * ROOT28)


def test_no_size_effect_factor_is_applied():
    # NSCP 2015 (ACI 318-14) has no lambda_s: MIDAS's 0.870 is ACI 318-19 only
    assert pc.two_way_vc(41.4, 1.0, 40, 410, 4430) == pytest.approx(0.33 * math.sqrt(41.4))


def test_alpha_s_for_interior_edge_and_corner():
    assert [pc.alpha_s(e) for e in (0, 1, 2, 3)] == [40, 30, 20, 20]


def test_square_root_of_fc_is_capped_at_8_3_mpa():
    assert pc.sqrt_fc(100.0) == 8.3


def test_development_lengths():
    ld, ldh = pc.development(25, 28, 414, top=False)
    assert ld == pytest.approx(414 / (1.7 * ROOT28) * 25)          # 425.4.2.2, 25 mm bars
    assert ldh == pytest.approx(0.24 * 414 * 0.7 / ROOT28 * 25)    # 425.4.3.1, psi_c = 0.7
    ld_top, _ = pc.development(20, 28, 414, top=True)
    assert ld_top == pytest.approx(1.3 * 414 / (2.1 * ROOT28) * 20)  # psi_t = 1.3, 2.1
    assert pc.development(10, 60, 414, top=False)[0] == 300.0         # at least 300 mm


def test_shrinkage_ratio_of_table_424_4_3_2():
    assert pc.shrinkage_ratio(414) == 0.0020      # Grade 60 bars are under 420 MPa
    assert pc.shrinkage_ratio(420) == pytest.approx(0.0018)
    assert pc.shrinkage_ratio(550) == pytest.approx(0.0014)


@pytest.mark.parametrize("As, expected", [
    (0.0, 1200.0),              # shrinkage steel 0.0020 b h
    (1000.0, 4 / 3 * 1000.0),   # 4/3 of what is needed
    (1500.0, 1.4 / 414 * 1000 * 500),   # the beam minimum caps the 4/3
    (2000.0, 2000.0),           # more than the beam minimum: what is needed
])
def test_minimum_steel_as_crsi_reads_nscp(As, expected):
    assert pc.minimum_steel(As, 1000, 500, 600, 28, 414) == pytest.approx(expected)


def test_flexural_steel_matches_the_rectangular_block():
    As, why = pc.flexural_steel(300e6, 1000, 500, 28, 414)
    a = As * 414 / (0.85 * 28 * 1000)
    assert not why and 0.9 * As * 414 * (500 - a / 2) == pytest.approx(300e6, rel=1e-9)


@pytest.mark.parametrize("outside, share", [(200, 1.0), (-200, 0.0), (0, 0.5), (100, 0.75)])
def test_pile_reaction_share_of_413_4_2_5(outside, share):
    assert pc.share_outside(outside, 400) == pytest.approx(share)


# --------------------------------------------------------------------------- #
# Layouts and reactions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9, 10, 12])
def test_standard_layouts_are_centred_with_the_typed_spacing(n):
    piles = pc.standard_layout(n, 1200)
    assert len(piles) == n
    assert sum(p[0] for p in piles) == pytest.approx(0, abs=1e-6)
    assert sum(p[1] for p in piles) == pytest.approx(0, abs=1e-6)
    nearest = min(math.dist(a, b) for i, a in enumerate(piles) for b in piles[i + 1:])
    assert nearest == pytest.approx(1200, rel=1e-6)


def test_rigid_cap_reactions_by_hand():
    cap = pc.calculate(weightless(n_piles=4, find_depth="No", loads="S1, S, 1000, 0, 100"))
    R = cap.reactions["S1 (cap weight x1)"]
    # R = P/n + My x / sum x^2 = 250 +- 100 x 0.6 / (4 x 0.36)
    assert sorted(R) == pytest.approx([250 - 41.667, 250 - 41.667, 250 + 41.667, 250 + 41.667],
                                      abs=1e-3)


def test_reactions_are_in_equilibrium_for_a_skewed_group():
    cap = pc.calculate(weightless(layout="Coordinates", coords="0,800; -700,-300; 900,-500",
                                  cap_lx=2800, cap_ly=2400, find_depth="No",
                                  loads="S1, S, 900, 120, -80, 30, -20"))
    R = cap.reactions["S1 (cap weight x1)"]
    lever = (cap.h - cap.values["embed"]) / 1000
    assert sum(R) == pytest.approx(900)
    assert sum(r * x for r, (x, y) in zip(R, cap.piles)) / 1000 == pytest.approx(-80 + 30 * lever)
    assert sum(r * y for r, (x, y) in zip(R, cap.piles)) / 1000 == pytest.approx(120 - 20 * lever)


def test_the_cap_weight_is_added_with_both_factors():
    cap = pc.calculate(inputs(n_piles=4, find_depth="No", loads="U1, F, 1000"))
    low, high = cap.reactions["U1 (cap weight x0.9)"], cap.reactions["U1 (cap weight x1.2)"]
    assert sum(high) > sum(low) > 1000


def test_piles_in_one_line_cannot_resist_a_moment_about_it():
    cap = pc.calculate(inputs(n_piles=2, find_depth="No", loads="U1, F, 1000, 50, 0"))
    group = [c for c in cap.checks if c.group == "Pile group"]
    assert group and not group[0].passed and not cap.passed


def test_one_third_increase_only_for_wind_and_earthquake():
    loads = "D+L, S, 2000; D+0.7E, S, 2000"
    plain = pc.calculate(inputs(n_piles=4, find_depth="No", loads=loads, q_comp=600))
    more = pc.calculate(inputs(n_piles=4, find_depth="No", loads=loads, q_comp=600,
                               capacity_method="Allowable + 1/3 for wind and earthquake"))
    comp = lambda cap: next(c for c in cap.checks if c.name.startswith("Compression"))  # noqa
    assert comp(plain).capacity == 600              # the gravity combination governs
    assert comp(more).capacity == 600               # D+L still has no increase
    assert pc.Combo("D+0.7E", False, 1).has_lateral()
    assert not pc.Combo("1.2D+1.6L", True, 1).has_lateral()


# --------------------------------------------------------------------------- #
# Loads
# --------------------------------------------------------------------------- #
def test_loads_typed():
    combos = pc.parse_loads("D+L, S, 1600, 10, 20, 3, 4; U, F, 2200")
    assert combos[0].factored is False and combos[0].My == 20 and combos[0].Vy == 4
    assert combos[1].factored is True and combos[1].Mx == 0


def test_loads_typed_refuses_an_unknown_kind():
    with pytest.raises(ValueError, match="S \\(service\\) or F"):
        pc.parse_loads("A, X, 100")


def test_loads_from_csv_and_excel(tmp_path):
    rows = [["Name", "Type", "P", "Mx", "My", "Vx", "Vy", "WE", "DF"],
            ["D+L", "S", 1600, 10, 20, 0, 0, "No", ""],
            ["0.9D+E", "F", 900, 0, 300, 50, 0, "Yes", 0.9]]
    csv_path = tmp_path / "loads.csv"
    csv_path.write_text("\n".join(",".join(str(c) for c in r) for r in rows), encoding="utf-8")
    from openpyxl import Workbook

    book = Workbook()
    for r in rows:
        book.active.append(r)
    xlsx_path = tmp_path / "loads.xlsx"
    book.save(xlsx_path)
    for path in (csv_path, xlsx_path):
        combos = pc.read_loads_file(str(path))
        assert [c.name for c in combos] == ["D+L", "0.9D+E"]
        assert combos[1].factored and combos[1].lateral is True and combos[1].df == 0.9
        assert [case.df for case in pc.weight_cases(combos[1])] == [0.9]


# --------------------------------------------------------------------------- #
# Design
# --------------------------------------------------------------------------- #
def test_least_thickness_passes_and_the_next_thinner_fails():
    cap = pc.calculate(inputs(n_piles=4, loads="D+L, S, 2400; U, F, 3400"))
    assert cap.passed and cap.searched[-1] == (cap.h, True, "")
    if len(cap.searched) > 1:
        assert cap.searched[-2][1] is False and cap.searched[-2][0] == cap.h - 50


def test_the_search_stops_when_only_the_pile_capacity_fails():
    # the cap is made deep enough for everything else, then the search stops:
    # no thickness changes the pile capacity
    cap = pc.calculate(inputs(n_piles=4, loads="D+L, S, 9000; U, F, 3400", q_comp=1000))
    assert {c.group for c in cap.failing()} == {"Pile capacity"}
    assert cap.searched[-1] == (cap.h, False, "Pile capacity")
    assert cap.h < pc.MAX_DEPTH


def test_a_tension_pile_gets_top_bars():
    cap = pc.calculate(inputs(column_shape="Circular", cx=1000, layout="Coordinates",
                              coords="0,700; -600,-350; 600,-350", cap_lx=2160, cap_ly=2010,
                              find_depth="No", h=550, loads="U, F, 470, -271, -363"))
    assert any(b.face == "Top" for b in cap.bars)


def test_deep_cap_uses_strut_and_tie_and_the_crsi_option_switches_it():
    loads = "D+L, S, 2400; U, F, 3400"
    stm = pc.calculate(inputs(n_piles=4, h=900, find_depth="No", loads=loads))
    assert stm.deep
    assert any(c.group == "Strut-and-tie" and c.governs for c in stm.checks)
    assert all(not c.governs for c in stm.checks if c.group in ("One-way shear", "Two-way shear"))
    crsi = pc.calculate(inputs(n_piles=4, h=900, find_depth="No", loads=loads,
                               deep_method="CRSI special checks"))
    assert any(c.group == "Deep cap (CRSI)" and c.governs for c in crsi.checks)
    assert all(not c.governs for c in crsi.checks if c.group == "Strut-and-tie")


def test_strut_and_tie_tie_force_by_hand():
    # one pile pair along X, column 600 square: the node is at c/4 = 150 mm from the centre
    cap = pc.calculate(weightless(n_piles=2, spacing=1200, h=900, find_depth="No",
                                  loads="U, F, 2000"))
    _, T, _ = cap.geometry["ties"]["X"]
    z_check = next(c for c in cap.checks if c.name.startswith("Least angle"))
    z = float(z_check.note.split("=")[1].split("mm")[0])
    assert T == pytest.approx(1000 * (600 - 150) / z, rel=2e-3)   # z is printed to 1 mm


def test_column_bearing_by_hand():
    # NSCP 422.8.3: column 600 square on a 2400 cap, A2/A1 > 4, so the factor is 2
    cap = pc.calculate(inputs(n_piles=4, h=900, find_depth="No", loads="U, F, 14000",
                              deep_method="CRSI special checks"))
    b = cap.geometry["bearing"]
    assert b["factor"] == 2.0
    assert b["phiBn"] == pytest.approx(0.65 * 2 * 0.85 * 28 * 600 * 600 / 1000)
    check = next(c for c in cap.checks if c.name == "Column on the cap")
    assert check.demand > check.capacity and not check.governs
    excess = check.demand - check.capacity
    assert b["As_req"] == pytest.approx(max(0.005 * 600 * 600, excess * 1000 / (0.65 * 414)))


# --------------------------------------------------------------------------- #
# CRSI Design Guide for Pile Caps (2015), Examples 1 and 2 (US units)
# --------------------------------------------------------------------------- #
IN, KIP, KFT, PSI = 25.4, 4.448222, 1.355818, 0.00689476


def crsi_example(piles, side_in, cap_in, h_in, dp_in, p_service_k):
    """CRSI loads the column so that each pile carries 1.6 P_service with 1.6 on
    the cap weight, and moves the piles 3 in toward the edges."""
    W = 0.150 * (cap_in / 12) ** 2 * h_in / 12
    n = len(piles)
    v = inputs(column_shape="Rectangular", cx=side_in * IN, cy=side_in * IN, layout="Coordinates",
               coords="; ".join(f"{x * IN},{y * IN}" for x, y in piles),
               cap_lx=cap_in * IN, cap_ly=cap_in * IN, dp=dp_in * IN, pile_shape="Round",
               embed=6 * IN, tolerance=3 * IN, capacity_method="Allowable",
               q_comp=p_service_k * KIP, h=h_in * IN, find_depth="No", fc=3000 * PSI,
               fy=60000 * PSI, bottom_bar=1.0 * IN, clear_above_pile=3 * IN,
               gamma_c=150 * 0.157087, soil_h=0.0, surcharge=0.0,
               flexure_section="c/4 from the column centre", deep_method="CRSI special checks")
    v["combos"] = [pc.Combo("1.6(D+L)", True, 1.6 * (n * p_service_k - W) * KIP, df=1.6, lf=0.0),
                   pc.Combo("D+L", False, (n * p_service_k - W) * KIP, df=1.0, lf=0.0)]
    v.pop("loads", None)
    return pc.calculate(v)


def check_named(cap, start):
    return next(c for c in cap.checks if c.name.startswith(start))


def test_crsi_example_1_sixteen_piles():
    piles = [(x, y) for y in (-54, -18, 18, 54) for x in (-54, -18, 18, 54)]
    cap = crsi_example(piles, 22, 138, 48, 8, 80)
    assert cap.geometry["Mu"]["sag"]["X"][0] / KFT == pytest.approx(2704, rel=2e-3)
    assert max(next(iter(cap.reactions.values()))) / KIP == pytest.approx(128.0)
    # the factored shears of CRSI: LS 1, 4 and 5
    assert check_named(cap, "Around the column").demand / KIP == pytest.approx(1433, rel=2e-3)
    assert check_named(cap, "Two-way shear at the column face").demand / KIP == \
        pytest.approx(1924.3, rel=2e-3)
    assert check_named(cap, "One-way shear at the column face along X").demand / KIP == \
        pytest.approx(970.6, rel=2e-3)
    # P3: b0 = pi (dp + d) / 4 + 2 E, with E = 15 in
    b0 = float(check_named(cap, "Two-way shear around pile 1 ").name.split("= ")[1].split()[0])
    assert b0 / IN == pytest.approx(66.1, abs=0.1)
    # the bars are developed from the column face, not from c/4: A/2 - c/2 - 3 in = 55 in
    # (the program keeps 75 mm at the side, CRSI 3 in)
    assert cap.bars[0].available / IN == pytest.approx(55.0, abs=0.1)


def test_crsi_example_2_five_piles():
    a = 0.7071 * 36
    cap = crsi_example([(-a, -a), (a, -a), (0, 0), (-a, a), (a, a)], 20, 93, 43, 10, 200)
    assert cap.geometry["Mu"]["sag"]["X"][0] / KFT == pytest.approx(1211, rel=2e-3)
    # LS 5: Vu 620 k
    assert check_named(cap, "One-way shear at the column face along X").demand / KIP == \
        pytest.approx(620, rel=2e-3)
    # P3: b0 = 75.8 in; NSCP 0.33 sqrt(f'c) is 0.7 % under ACI's 4 sqrt(f'c) psi
    p3 = check_named(cap, "Two-way shear around pile 1 ")
    assert p3.capacity / KIP == pytest.approx(466 * 0.75 / 0.85, rel=0.01)
    # P4: b = 95.4 in; fails as in CRSI (320 k > phi Vc)
    p4 = check_named(cap, "One-way shear across corner pile")
    assert float(p4.name.split("= ")[1].split()[0]) / IN == pytest.approx(95.4, abs=0.2)
    assert p4.demand / KIP == pytest.approx(320) and not p4.passed
    # the minimum steel of CRSI along Y (d = 32.5 in): 1.4/fy b d
    bar_y = next(b for b in cap.bars if b.direction == "Y")
    assert bar_y.As_req / IN ** 2 == pytest.approx(1.4 / (60000 * PSI) * 93 * 32.5, rel=2e-3)


def test_fixed_heads_give_the_pile_head_moment():
    cap = pc.calculate(inputs(n_piles=4, find_depth="No", head="Fixed",
                              loads="U, F, 2000, 0, 0, 80, 0"))
    note = next(n for n in cap.notes if n.startswith("Fixed pile heads"))
    assert "20.0 x 1.50 / 2 = 15.0 kN-m" in note and "308.4.1" in note


def test_recommendations_do_not_fail_the_design():
    cap = pc.calculate(inputs(n_piles=4, loads="D+L, S, 2400; U, F, 3400", embed=75))
    rec = [c for c in cap.checks if c.group == "Recommendation"]
    assert any(not c.passed for c in rec) and cap.passed


def test_summary_and_report_text():
    cap = pc.calculate(inputs(n_piles=3, loads="D+L, S, 1800; U, F, 2500"))
    text = pc.summary_text(cap)
    assert "PILE CAP DESIGN (NSCP 2015)" in text and "RESULT:" in text
    body = report.report_body(cap, "plan.png")
    assert r"\section{Pile Reactions}" in body and "NSCP 422.6.5.2" in body


def test_plan_figure_is_written(tmp_path):
    cap = pc.calculate(inputs(n_piles=5, loads="D+L, S, 3000; U, F, 4200"))
    path = report.draw_plan(cap, str(tmp_path / "plan.png"))
    assert Path(path).stat().st_size > 1000


# --------------------------------------------------------------------------- #
# The workflow: four dialogs, then the loads file
# --------------------------------------------------------------------------- #
def test_the_inputs_are_asked_in_four_dialogs(monkeypatch, tmp_path):
    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    titles = []

    def enter_values(title, prompt, labels, shown):
        titles.append(title)
        return {label: shown[label] for label in labels}

    monkeypatch.setattr(_gui_helpers, "enter_values", enter_values)
    monkeypatch.setattr(_gui_helpers, "select_option", lambda *a, **k: None)
    monkeypatch.setattr(_gui_helpers, "show_summary", lambda *a, **k: None)
    cap = pc.run(window=False)
    assert titles == [title for title, _ in pc.INPUT_GROUPS]
    assert isinstance(cap, pc.PileCap)


def test_a_loads_file_is_chosen_after_the_dialogs(monkeypatch, tmp_path):
    path = tmp_path / "loads.csv"
    path.write_text("Name,Type,P\nU,F,2500\nS,S,1800\n", encoding="utf-8")
    monkeypatch.setattr(_gui_helpers, "select_open_path", lambda *a, **k: str(path))
    values = pc.complete(inputs(load_source="File", loads_file=""))
    assert values["loads_file"] == str(path)
    assert pc.calculate(values).combos[0].name == "U"


def test_every_label_fits_its_dialog():
    labels = [dialog_label(f) for f in pc.INPUTS]
    assert len(labels) == len(set(labels))
    assert max(len(group) for _, group in pc.INPUT_GROUPS) <= 16


# --------------------------------------------------------------------------- #
# The window: the form, the live plan and the results
# --------------------------------------------------------------------------- #
def test_the_window_designs_and_redraws(monkeypatch, tmp_path):
    import tkinter as tk

    from design.concrete import pile_cap_window as pw

    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    try:
        window = pw.PileCapWindow(pc)
    except tk.TclError:
        pytest.skip("no display for Tk")
    try:
        window.root.withdraw()
        window.widgets["n_piles"].set("5")
        window.widgets["loads"].delete("1.0", "end")
        window.widgets["loads"].insert("1.0", "D+L, S, 2400;\nU, F, 3400")
        window.redraw()
        assert "5 piles" in window.legend.cget("text")
        window.run()
        assert window.result is not None and window.result.n == 5
        assert "RESULT:" in window.text.get("1.0", "end")
        assert "largest factored" in window.legend.cget("text")      # reactions drawn
        window.widgets["spacing"].set("1300")                        # geometry changed
        window.redraw()
        assert "run again" in window.legend.cget("text")
        window.widgets["spacing"].set("abc")
        window.redraw()                                               # no exception
    finally:
        window.close()


def test_inputs_that_do_not_apply_are_hidden(monkeypatch, tmp_path):
    import tkinter as tk

    from design.concrete import pile_cap_window as pw

    monkeypatch.setattr(design_cli, "_saved_path", lambda name: str(tmp_path / f"{name}.json"))
    try:
        window = pw.PileCapWindow(pc)
    except tk.TclError:
        pytest.skip("no display for Tk")
    try:
        window.root.withdraw()
        shown = lambda key: bool(window.rows[key][1].grid_info())  # noqa: E731
        window.widgets["layout"].set("Standard")
        assert shown("n_piles") and not shown("coords") and not shown("cap_lx")
        window.widgets["layout"].set("Coordinates")
        assert shown("coords") and shown("cap_ly") and not shown("spacing")
        window.widgets["head"].set("Pinned")
        assert not shown("lf")
        window.widgets["head"].set("Fixed")
        assert shown("lf") and shown("ground")
        window.widgets["column_shape"].set("Circular")
        assert not shown("cy")
    finally:
        window.close()


@pytest.mark.parametrize("module_name", ["design.concrete.pile_cap",
                                         "design.steel.wide_flange_aisc360"])
def test_every_blank_input_says_what_a_blank_means(module_name):
    import importlib

    module = importlib.import_module(module_name)
    conditional = set(getattr(module, "SHOWN_WHEN", {}))
    for field in module.INPUTS:
        if field.optional and field.key not in conditional and field.key != "loads":
            assert "blank" in field.label, field.label
