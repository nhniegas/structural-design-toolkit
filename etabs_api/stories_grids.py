"""ETABS story and grid inspection helpers."""

from .helpers import as_list, ensure_success


class StoriesGrids:
    """Read story and grid definitions."""

    def __init__(self, connector):
        self.connector = connector

    def stories(self) -> list[dict]:
        """Return story names, elevations, and heights."""
        result = self.connector.sap_model.Story.GetStories(0, [], [], [], [])
        ensure_success(result, "Story.GetStories")
        names = as_list(result[2])
        elevations = as_list(result[3])
        heights = as_list(result[4])
        return [
            {
                "name": name,
                "elevation": elevations[i] if i < len(elevations) else None,
                "height": heights[i] if i < len(heights) else None,
            }
            for i, name in enumerate(names)
        ]

    def grids(self) -> list[str]:
        """Return defined grid-system names."""
        result = self.connector.sap_model.GridSys.GetNameList(0, [])
        ensure_success(result, "GridSys.GetNameList")
        return as_list(result[2])

