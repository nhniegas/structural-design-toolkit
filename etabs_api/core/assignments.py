"""ETABS object-assignment helpers."""

from .helpers import ensure_success


class Assignments:
    """Assign sections, releases, diaphragms, and wall labels."""

    def __init__(self, connector):
        self.connector = connector

    def frame_section(self, name: str, section: str):
        """Assign a frame section property."""
        result = self.connector.sap_model.FrameObj.SetSection(name, section, True)
        ensure_success(result, "FrameObj.SetSection")
        return name

    def frame_releases(self, name: str, start: list[bool], end: list[bool]):
        """Assign frame end releases."""
        result = self.connector.sap_model.FrameObj.SetReleases(name, start, end)
        ensure_success(result, "FrameObj.SetReleases")
        return name

    def area_section(self, name: str, section: str):
        """Assign an area section property."""
        result = self.connector.sap_model.AreaObj.SetProperty(name, section)
        ensure_success(result, "AreaObj.SetProperty")
        return name

    def diaphragm(self, name: str, diaphragm_name: str):
        """Assign a diaphragm to an area object."""
        result = self.connector.sap_model.AreaObj.SetDiaphragm(name, diaphragm_name)
        ensure_success(result, "AreaObj.SetDiaphragm")
        return name

    def pier_label(self, name: str, label: str):
        """Assign a pier label to an area object."""
        result = self.connector.sap_model.AreaObj.SetPier(name, label)
        ensure_success(result, "AreaObj.SetPier")
        return name

    def spandrel_label(self, name: str, label: str):
        """Assign a spandrel label to an area object."""
        result = self.connector.sap_model.AreaObj.SetSpandrel(name, label)
        ensure_success(result, "AreaObj.SetSpandrel")
        return name

