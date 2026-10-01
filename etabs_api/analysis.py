"""Analysis execution and status operations."""

from .helpers import as_list, ensure_success


class Analysis:
    """Control ETABS analysis execution."""

    def __init__(self, connector):
        self.connector = connector

    @property
    def interface(self):
        """Return the ETABS Analyze COM interface."""
        self.connector.ensure_connected()
        return self.connector.sap_model.Analyze

    def run(self):
        """Run all active load cases."""
        result = self.interface.RunAnalysis()
        ensure_success(result, "Analyze.RunAnalysis")
        return result

    def status(self) -> list[dict]:
        """Return analysis-case status records."""
        result = self.interface.GetCaseStatus(0, [], [])
        ensure_success(result, "Analyze.GetCaseStatus")
        names = as_list(result[2])
        statuses = as_list(result[3])
        labels = {1: "Not Run", 2: "Could Not Start", 3: "Not Finished", 4: "Finished"}
        return [
            {"case": name, "status_code": statuses[i], "status": labels.get(statuses[i], "Unknown")}
            for i, name in enumerate(names)
        ]

    def set_active_dof(self, u1=True, u2=True, u3=True, r1=True, r2=True, r3=True):
        """Set active translational and rotational degrees of freedom."""
        dof = [u1, u2, u3, r1, r2, r3]
        result = self.interface.SetActiveDOF(dof)
        ensure_success(result, "Analyze.SetActiveDOF")
        return dof

    def delete_results(self, case_name: str = ""):
        """Delete analysis results and unlock the model."""
        result = self.interface.DeleteResults(0, case_name)
        ensure_success(result, "Analyze.DeleteResults")
        return result

