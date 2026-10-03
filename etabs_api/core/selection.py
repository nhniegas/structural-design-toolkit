"""ETABS object selection and grouping operations."""

from .helpers import as_list, ensure_success


class Selection:
    """Select objects and manage ETABS groups."""

    def __init__(self, connector):
        self.connector = connector

    @property
    def interface(self):
        """Return the ETABS selection interface (``SapModel.SelectObj``)."""
        self.connector.ensure_connected()
        return self.connector.sap_model.SelectObj

    def select(self, name: str, object_type: str = "Frame", clear_previous=False):
        """Select one Point, Frame, Area, or Link object."""
        if clear_previous:
            self.clear()
        objects = {
            "point": self.connector.sap_model.PointObj,
            "frame": self.connector.sap_model.FrameObj,
            "area": self.connector.sap_model.AreaObj,
            "link": self.connector.sap_model.LinkObj,
        }
        key = object_type.casefold()
        if key not in objects:
            raise ValueError(f"Unsupported ETABS object type: {object_type}")
        result = objects[key].SetSelected(name, True)
        ensure_success(result, f"{object_type}.SetSelected")
        return name

    def get_selected(self) -> list[dict]:
        """Return selected ETABS object names and object types."""
        result = self.interface.GetSelected(0, [], [])
        ensure_success(result, "Select.GetSelected")
        type_names = {1: "Point", 2: "Frame", 3: "Cable", 4: "Tendon", 5: "Area", 6: "Solid", 7: "Link"}
        # Returns (count, object types, object names, status).
        types = as_list(result[1])
        names = as_list(result[2])
        return [
            {"type": type_names.get(types[i], "Unknown"), "name": name}
            for i, name in enumerate(names)
        ]

    def clear(self):
        """Clear all active ETABS selections."""
        result = self.interface.ClearSelection()
        ensure_success(result, "Select.ClearSelection")
        return result

    def define_group(self, group_name: str, color: int = -1):
        """Define or update an ETABS group."""
        result = self.connector.sap_model.GroupDef.SetGroup(
            group_name, color, True, True, True, True, True, True, True
        )
        ensure_success(result, "GroupDef.SetGroup")
        return group_name

    def add_to_group(self, name: str, group_name: str, object_type: str = "Frame", remove=False):
        """Add or remove an object from an ETABS group."""
        objects = {
            "point": self.connector.sap_model.PointObj,
            "frame": self.connector.sap_model.FrameObj,
            "area": self.connector.sap_model.AreaObj,
            "link": self.connector.sap_model.LinkObj,
        }
        key = object_type.casefold()
        if key not in objects:
            raise ValueError(f"Unsupported ETABS object type: {object_type}")
        result = objects[key].SetGroupAssign(name, group_name, remove)
        ensure_success(result, f"{object_type}.SetGroupAssign")
        return {"name": name, "group": group_name, "removed": remove}

