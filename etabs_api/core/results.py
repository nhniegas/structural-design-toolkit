"""Analysis-result extraction helpers."""

from __future__ import annotations

import pandas as pd

from .helpers import as_list, ensure_success


class Results:
    """Extract selected ETABS analysis results as DataFrames.

    Every ETABS results call returns ``(count, array, array, ..., status)``.
    Without a name the object queries return every object: ETABS takes no
    empty name, so the group "All" is asked for instead.
    """

    @staticmethod
    def _target(name: str) -> tuple[str, int]:
        """(name, item type) of a query: one object (0), or the group "All" (2)."""
        return (name, 0) if name else ("All", 2)

    def __init__(self, connector):
        self.connector = connector

    @property
    def interface(self):
        """Return the ETABS Results COM interface."""
        self.connector.ensure_connected()
        return self.connector.sap_model.Results

    def setup_cases(self, cases: list[str] | None = None, combos: list[str] | None = None):
        """Select the load cases and combinations used for result extraction."""
        setup = self.interface.Setup
        if cases is None and combos is None:
            return
        ensure_success(
            setup.DeselectAllCasesAndCombosForOutput(),
            "Results.Setup.DeselectAllCasesAndCombosForOutput",
        )
        for case in cases or []:
            ensure_success(
                setup.SetCaseSelectedForOutput(case),
                "Results.Setup.SetCaseSelectedForOutput",
            )
        for combo in combos or []:
            ensure_success(
                setup.SetComboSelectedForOutput(combo),
                "Results.Setup.SetComboSelectedForOutput",
            )

    def joint_displacements(self, name: str = "") -> pd.DataFrame:
        """Extract joint displacement results for one joint, or for every joint."""
        result = self.interface.JointDispl(
            *self._target(name), 0, [], [], [], [], [], [], [], [], [], [], []
        )
        return self._result_dataframe(
            result,
            ["Joint", "Element", "OutputCase", "StepType", "StepNum",
             "U1", "U2", "U3", "R1", "R2", "R3"],
        )

    def frame_forces(self, name: str = "") -> pd.DataFrame:
        """Extract frame-force results for one frame, or for every frame."""
        result = self.interface.FrameForce(
            *self._target(name), 0, [], [], [], [], [], [], [], [], [], [], [], [], []
        )
        return self._result_dataframe(
            result,
            ["Frame", "Station", "Element", "ElementStation", "OutputCase", "StepType",
             "StepNum", "P", "V2", "V3", "T", "M2", "M3"],
        )

    def joint_reactions(self, name: str = "") -> pd.DataFrame:
        """Extract joint-reaction results for one joint, or for every joint."""
        result = self.interface.JointReact(
            *self._target(name), 0, [], [], [], [], [], [], [], [], [], [], []
        )
        return self._result_dataframe(
            result,
            ["Joint", "Element", "OutputCase", "StepType", "StepNum",
             "F1", "F2", "F3", "M1", "M2", "M3"],
        )

    def base_reactions(self) -> pd.DataFrame:
        """Extract base-reaction results."""
        result = self.interface.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        return self._result_dataframe(
            result,
            ["OutputCase", "StepType", "StepNum", "FX", "FY", "FZ", "MX", "MY", "MZ"],
        )

    @staticmethod
    def _result_dataframe(result, columns: list[str]) -> pd.DataFrame:
        """Convert an ETABS ``(count, arrays..., status)`` tuple to a DataFrame."""
        ensure_success(result, "ETABS results query")
        count = int(result[0])
        arrays = [as_list(values) for values in result[1 : 1 + len(columns)]]
        rows = [
            [values[index] if index < len(values) else None for values in arrays]
            for index in range(count)
        ]
        return pd.DataFrame(rows, columns=columns)
