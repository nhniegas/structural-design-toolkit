"""Compatibility tests for the consolidated composite-column designer."""

from design.composite_column_designer_aiscDG06 import RectangularFilledComposite


def test_standalone_constructor_names_are_supported():
    """The former rev02 constructor names remain available."""
    column = RectangularFilledComposite(
        b_mm=400,
        h_mm=500,
        t_mm=10,
        fc_mpa=30,
        fy_mpa=345,
        Lb_m=3,
        Lh_m=3,
        Mb_kNm=20,
        Mh_kNm=10,
        label="C1",
    )
    assert column.Lx > 0
    assert column.Mux > 0
    assert column.label == "C1"


def test_standalone_method_aliases_and_report():
    """The consolidated class exposes the former standalone method names."""
    column = RectangularFilledComposite(400, 500, 10, 30, 345, 3, 3)
    assert "cls" in column.axial_classification()
    assert "phiPn" in column.axial_compression()
    assert "phiMn" in column.flexure("b")
    assert "Rectangular Filled Composite Column" in column.report()
