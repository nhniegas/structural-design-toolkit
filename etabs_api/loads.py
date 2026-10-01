"""ETABS load-pattern, load-case, combination, and assignment helpers."""

from .helpers import as_list, ensure_success


class Loads:
    """Manage common ETABS load definitions and assignments."""

    def __init__(self, connector):
        self.connector = connector

    @property
    def patterns(self):
        """Return the load-pattern COM interface."""
        self.connector.ensure_connected()
        return self.connector.sap_model.LoadPatterns

    def get_patterns(self) -> list[str]:
        """Return defined load-pattern names."""
        result = self.patterns.GetNameList(0, [])
        ensure_success(result, "LoadPatterns.GetNameList")
        return as_list(result[1])  # (count, names, status)

    def define_pattern(self, name: str, load_type: int, self_weight_multiplier: float = 0.0):
        """Define a load pattern."""
        result = self.patterns.Add(name, load_type, self_weight_multiplier, True)
        ensure_success(result, "LoadPatterns.Add")
        return name

    def define_combo(self, name: str, combo_type: int = 0):
        """Define a load combination."""
        result = self.connector.sap_model.RespCombo.Add(name, combo_type)
        ensure_success(result, "RespCombo.Add")
        return name

    def add_combo_case(self, combo_name: str, case_name: str, scale_factor: float = 1.0):
        """Add a load case or nested combination to a response combination."""
        result = self.connector.sap_model.RespCombo.SetCaseList(
            combo_name, 0, case_name, scale_factor
        )
        ensure_success(result, "RespCombo.SetCaseList")
        return combo_name

    def assign_point_load(self, name: str, load_pattern: str, values: list[float], replace=True):
        """Assign a six-component point load vector."""
        result = self.connector.sap_model.PointObj.SetLoadForce(
            name, load_pattern, values, replace
        )
        ensure_success(result, "PointObj.SetLoadForce")
        return name

    def assign_frame_load(self, name: str, load_pattern: str, direction: int, value: float, relative=True, replace=True):
        """Assign a uniform distributed frame load."""
        result = self.connector.sap_model.FrameObj.SetLoadDistributed(
            name, load_pattern, 1, direction, 0.0, 1.0, value, value, "Global", replace
        )
        ensure_success(result, "FrameObj.SetLoadDistributed")
        return name

