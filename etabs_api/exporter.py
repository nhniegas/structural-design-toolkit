"""ETABS data extraction and Excel export helpers."""

from __future__ import annotations

import pandas as pd
import xlwings as xw


class ETABSDataExporter:
    """Extract ETABS tables and write them into Excel worksheets."""

    def __init__(self, etabs_instance):
        self.etabs = etabs_instance

    @staticmethod
    def _get_sheet(sheet_name: str):
        """Return a workbook sheet from caller context or the active workbook."""
        try:
            return xw.Book.caller().sheets[sheet_name]
        except Exception:
            return xw.books.active.sheets[sheet_name]

    @classmethod
    def _write_dataframe_to_excel(
        cls,
        df: pd.DataFrame,
        sheet_name: str,
        start_cell: str = "B2",
        header_color: tuple = (189, 215, 238),
        clear_sheet: bool = True,
        autofit: bool = True,
    ):
        """Write a DataFrame to Excel and format the header row."""
        sheet = cls._get_sheet(sheet_name)
        if clear_sheet:
            sheet.clear()
        sheet.range(start_cell).options(index=False).value = df
        if not df.empty:
            if header_color:
                header_range = sheet.range(start_cell).expand("right")
                header_range.color = header_color
            if autofit:
                sheet.range(start_cell).expand().columns.autofit()

    def get_load_combinations(self, place_holder=None) -> list:
        """Return unique load combination names."""
        raw_combos = self.etabs.get_data("Load Combination Definitions", place_holder)[
            "Name"
        ].tolist()
        return list(dict.fromkeys(raw_combos))

    def get_available_members(self, load_combos: list) -> list:
        """Return all non-numeric member names participating in design forces."""
        raw_beam_forces = self.etabs.get_data("Design Forces - Beams", load_combos)
        raw_col_forces = self.etabs.get_data("Design Forces - Columns", load_combos)
        combined_names = pd.concat(
            [raw_beam_forces["UniqueName"], raw_col_forces["UniqueName"]]
        )
        return [m for m in combined_names.unique().tolist() if not str(m).isnumeric()]

    def display_selected_inputs(
        self,
        load_combos: list,
        members: list,
        sheet_name: str = "OVERWRITES",
        combo_cell: str = "B6",
        member_cell: str = "C6",
    ):
        """Write the selected load combos and members to Excel."""
        sheet = self._get_sheet(sheet_name)
        for cell_ref in [combo_cell, member_cell]:
            cell = sheet.range(cell_ref)
            if cell.value is not None:
                cell.expand("down").clear()
            else:
                cell.clear()
        if load_combos:
            sheet.range(combo_cell).options(transpose=True).value = load_combos
        if members:
            sheet.range(member_cell).options(transpose=True).value = members

    def display_factored_gravity_loads_menu(
        self,
        load_combos: list,
        sheet_name: str = "OVERWRITES",
        dropdown_cell: str = "F8",
    ):
        """Populate a dropdown list with the selected load combinations."""
        if not load_combos:
            return
        sheet = self._get_sheet(sheet_name)
        target = sheet.range(dropdown_cell)
        combo_list_str = ",".join(load_combos)
        try:
            target.api.Validation.Delete()
            target.api.Validation.Add(3, 1, 1, combo_list_str)
            target.value = load_combos[0]
        except Exception as exc:
            print(
                f"Warning: Could not update dropdown menu on {sheet_name}!{dropdown_cell}: {exc}"
            )

    def display_factored_loads(
        self,
        load_combos_selected: list = None,
        members_selected: list = None,
        sheet_name: str = "FACTORED LOADS",
        start_cell: str = "B2",
        header_color: tuple = (189, 215, 238),
    ) -> pd.DataFrame:
        """Extract and normalize design forces."""
        beam_forces = self.etabs.get_data("Design Forces - Beams", load_combos_selected)
        col_forces = self.etabs.get_data("Design Forces - Columns", load_combos_selected)
        if "Beam" in beam_forces.columns:
            beam_forces.rename(columns={"Beam": "Label"}, inplace=True)
        if "Column" in col_forces.columns:
            col_forces.rename(columns={"Column": "Label"}, inplace=True)
        design_forces = pd.concat([beam_forces, col_forces], ignore_index=True)
        if "Combo" in design_forces.columns:
            design_forces["Combo"] = design_forces["Combo"].str[:-2].str.strip()
        numeric_cols = ["Station", "P", "V2", "V3", "T", "M2", "M3"]
        for col in numeric_cols:
            if col in design_forces.columns:
                design_forces[col] = pd.to_numeric(design_forces[col], errors="coerce")
        force_cols = ["P", "V2", "V3"]
        moment_cols = ["T", "M2", "M3"]
        design_forces[force_cols] = design_forces[force_cols] / 1000
        design_forces[moment_cols] = design_forces[moment_cols] / 1000000
        design_forces = design_forces[~design_forces["UniqueName"].str.isnumeric()].copy()
        if members_selected:
            design_forces = design_forces[
                design_forces["UniqueName"].isin(members_selected)
            ].copy()
        if load_combos_selected:
            design_forces = design_forces[
                design_forces["Combo"].isin(load_combos_selected)
            ].copy()
        design_forces.drop_duplicates(inplace=True)
        if "Column" in design_forces.columns:
            design_forces.drop(columns=["Column"], inplace=True)
        self._write_dataframe_to_excel(
            df=design_forces,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )
        return design_forces

    def display_frame_data(
        self,
        members_selected: list = None,
        sheet_name: str = "FRAME DATA",
        start_cell: str = "B2",
        load_combos_selected: list = None,
        header_color: tuple = (189, 215, 238),
    ) -> pd.DataFrame:
        """Extract frame assignments and related section data."""
        frame_assignments = self.etabs.get_data(
            "Frame Assignments - Section Properties", load_combos_selected
        )
        frame_section_properties_rectangular = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Rectangular",
            load_combos_selected,
        )
        frame_section_properties_circular = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Circle", load_combos_selected
        )
        concrete_beam_reinforcing = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Beam Reinforcing",
            load_combos_selected,
        )
        concrete_column_reinforcing = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Column Reinforcing",
            load_combos_selected,
        )

        frame_assignments = frame_assignments[["Story", "UniqueName", "SectProp"]].copy()
        if members_selected:
            frame_assignments = frame_assignments[
                frame_assignments["UniqueName"].isin(members_selected)
            ].copy()

        frame_assignments["SectProp"] = frame_assignments["SectProp"].astype(str)
        rectangular_props = frame_section_properties_rectangular[
            ["Name", "Material", "t2", "t3", "DesignType"]
        ].copy()
        rectangular_props["t2"] = pd.to_numeric(
            rectangular_props["t2"], errors="coerce"
        )
        rectangular_props["t3"] = pd.to_numeric(
            rectangular_props["t3"], errors="coerce"
        )
        rectangular_props.rename(
            columns={"Name": "SectProp", "t2": "Width", "t3": "Depth"},
            inplace=True,
        )
        circular_props = frame_section_properties_circular[
            ["Name", "Material", "t3", "DesignType"]
        ].copy()
        circular_props["t3"] = pd.to_numeric(circular_props["t3"], errors="coerce")
        circular_props.rename(
            columns={"Name": "SectProp", "t3": "Diameter"}, inplace=True
        )
        reinforcing = pd.concat(
            [
                concrete_beam_reinforcing.rename(columns={"Name": "SectProp"}),
                concrete_column_reinforcing.rename(columns={"Name": "SectProp"}),
            ],
            ignore_index=True,
        )
        frame_data = frame_assignments.merge(
            pd.concat([rectangular_props, circular_props], ignore_index=True),
            on="SectProp",
            how="left",
        ).merge(reinforcing, on="SectProp", how="left")
        concrete_data = self.etabs.get_data(
            "Material Properties - Concrete Data", load_combos_selected
        )
        rebar_data = self.etabs.get_data(
            "Material Properties - Rebar Data", load_combos_selected
        )
        fc_map = dict(
            zip(
                concrete_data["Material"].astype(str).str.strip(),
                pd.to_numeric(concrete_data["Fc"], errors="coerce").round(2),
            )
        )
        fy_map = dict(
            zip(
                rebar_data["Material"].astype(str).str.strip(),
                pd.to_numeric(rebar_data["Fy"], errors="coerce").round(2),
            )
        )
        frame_data["Material"] = (
            frame_data["Material"].astype(str).str.strip().map(fc_map)
        )
        frame_data["RebarMatL"] = (
            frame_data["RebarMatL"].astype(str).str.strip().map(fy_map)
        )
        frame_data["RebarMatC"] = (
            frame_data["RebarMatC"].astype(str).str.strip().map(fy_map)
        )
        frame_data.rename(
            columns={"Material": "f'c", "RebarMatL": "fy", "RebarMatC": "fys"},
            inplace=True,
        )
        self._write_dataframe_to_excel(
            df=frame_data,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )
        return frame_data

    def display_connectivity_data(
        self,
        sheet_name: str = "CONNECTIVITY",
        start_cell: str = "B2",
        load_combos_selected: list = None,
        header_color: tuple = (189, 215, 238),
    ) -> pd.DataFrame:
        """Extract and export connectivity data."""
        connectivity_frames = []
        for table_name, design_type in (
            ("Beam Object Connectivity", "Beam"),
            ("Column Object Connectivity", "Column"),
        ):
            table = self.etabs.get_data(table_name, None)
            if isinstance(table, pd.DataFrame) and not table.empty:
                table = table.copy()
                table["DesignType"] = design_type
                connectivity_frames.append(table)
        connectivity = (
            pd.concat(connectivity_frames, ignore_index=True)
            if connectivity_frames
            else pd.DataFrame()
        )
        self._write_dataframe_to_excel(
            df=connectivity,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )
        return connectivity
