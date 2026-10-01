"""Database-table operations for ETABS."""

from __future__ import annotations

import pandas as pd

from .helpers import as_list, ensure_success


class DatabaseTables:
    """Read and edit ETABS database tables through a connector."""

    def __init__(self, connector):
        self.connector = connector

    @property
    def interface(self):
        """Return the ETABS DatabaseTables COM interface."""
        self.connector.ensure_connected()
        return self.connector.sap_model.DatabaseTables

    def available_tables(self) -> list[dict]:
        """Return available table names and internal keys."""
        result = self.interface.GetAllTables(0, [], [], [])
        ensure_success(result, "DatabaseTables.GetAllTables")
        return [
            {"key": key, "name": name}
            for key, name in zip(as_list(result[2]), as_list(result[3]))
        ]

    def get_table(self, table_key: str, group_name: str = "") -> pd.DataFrame:
        """Read one ETABS table into a DataFrame."""
        result = self.interface.GetTableForDisplayArray(
            table_key, [], group_name, 0, [], 0, []
        )
        ensure_success(result, "DatabaseTables.GetTableForDisplayArray")
        fields = as_list(result[4])
        record_count = int(result[5])
        values = as_list(result[6])
        if not fields or not record_count:
            return pd.DataFrame(columns=fields)
        width = len(fields)
        rows = [values[i : i + width] for i in range(0, record_count * width, width)]
        return pd.DataFrame(rows, columns=fields)

    def stage_table_data(
        self, table_key: str, fields: list[str], rows: list[list[str]]
    ):
        """Stage rows for a database table edit without applying them."""
        if any(len(row) != len(fields) for row in rows):
            raise ValueError("Every table row must match the fields length.")
        flat_values = [value for row in rows for value in row]
        return self.interface.SetTableForEditingArray(
            table_key, 0, fields, len(rows), flat_values
        )

    def apply_edits(self, fill_import_log: bool = False):
        """Apply staged database table edits and return ETABS diagnostics."""
        result = self.interface.ApplyEditedTables(fill_import_log, 0, 0, 0, "")
        ensure_success(result, "DatabaseTables.ApplyEditedTables")
        return {
            "fatal_errors": int(result[1]),
            "errors": int(result[2]),
            "warnings": int(result[3]),
            "info_messages": int(result[4]),
            "log_file": result[5],
        }

