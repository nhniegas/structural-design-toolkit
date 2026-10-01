"""ETABS property-inspection helpers."""

from .helpers import as_list, ensure_success


class Properties:
    """Read material, frame-section, area-section, and rebar property names."""

    def __init__(self, connector):
        self.connector = connector

    def _names(self, interface, operation: str) -> list[str]:
        """Read a property-name list from an ETABS property interface."""
        result = interface.GetNameList(0, [])
        ensure_success(result, operation)
        return as_list(result[1])  # (count, names, status)

    def materials(self) -> list[str]:
        """Return material property names."""
        return self._names(self.connector.sap_model.PropMaterial, "PropMaterial.GetNameList")

    def frame_sections(self) -> list[str]:
        """Return frame-section property names."""
        return self._names(self.connector.sap_model.PropFrame, "PropFrame.GetNameList")

    def area_sections(self) -> list[str]:
        """Return area-section property names."""
        return self._names(self.connector.sap_model.PropArea, "PropArea.GetNameList")

    def rebar(self) -> list[str]:
        """Return reinforcing-bar property names."""
        return self._names(self.connector.sap_model.PropRebar, "PropRebar.GetNameList")

