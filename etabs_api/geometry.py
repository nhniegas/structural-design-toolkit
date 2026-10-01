"""ETABS geometry inspection helpers."""

from .helpers import as_list, ensure_success


class Geometry:
    """Inspect and create basic point, frame, and area geometry."""

    def __init__(self, connector):
        self.connector = connector

    def all_points(self) -> list[str]:
        """Return all point object names."""
        result = self.connector.sap_model.PointObj.GetNameList(0, [])
        ensure_success(result, "PointObj.GetNameList")
        return as_list(result[2])

    def all_frames(self) -> list[str]:
        """Return all frame object names."""
        result = self.connector.sap_model.FrameObj.GetNameList(0, [])
        ensure_success(result, "FrameObj.GetNameList")
        return as_list(result[2])

    def all_areas(self) -> list[str]:
        """Return all area object names."""
        result = self.connector.sap_model.AreaObj.GetNameList(0, [])
        ensure_success(result, "AreaObj.GetNameList")
        return as_list(result[2])

    def add_point(self, x: float, y: float, z: float, user_name: str = ""):
        """Add a point object and return ETABS names."""
        result = self.connector.sap_model.PointObj.AddCartesian(x, y, z, "", user_name)
        ensure_success(result, "PointObj.AddCartesian")
        return result

    def add_frame(self, point_i: str, point_j: str, section: str, user_name: str = ""):
        """Add a frame object between two points."""
        result = self.connector.sap_model.FrameObj.AddByPoint(
            point_i, point_j, "", section, user_name
        )
        ensure_success(result, "FrameObj.AddByPoint")
        return result

