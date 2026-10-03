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
        # Returns (count, keys, names, import types, is-empty flags, status).
        result = self.interface.GetAllTables(0, [], [], [])
        ensure_success(result, "DatabaseTables.GetAllTables")
        return [
            {"key": key, "name": name}
            for key, name in zip(as_list(result[1]), as_list(result[2]))
        ]

    def get_table(self, table_key: str, group_name: str = "") -> pd.DataFrame:
        """Read one ETABS table into a DataFrame."""
        # Returns (field key list, table version, fields included, record count,
        # flattened values, status).
        result = self.interface.GetTableForDisplayArray(
            table_key, [], group_name, 0, [], 0, []
        )
        ensure_success(result, f"DatabaseTables.GetTableForDisplayArray({table_key!r})")
        fields = as_list(result[2])
        record_count = int(result[3])
        values = as_list(result[4])
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
        # Returns (fatal errors, errors, warnings, info messages, log, status).
        result = self.interface.ApplyEditedTables(fill_import_log, 0, 0, 0, 0, "")
        ensure_success(result, "DatabaseTables.ApplyEditedTables")
        return {
            "fatal_errors": int(result[0]),
            "errors": int(result[1]),
            "warnings": int(result[2]),
            "info_messages": int(result[3]),
            "log_file": result[4],
        }

