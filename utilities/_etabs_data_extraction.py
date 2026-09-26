"""ETABS Data Exporter Module.

Handles extracting, processing, and transferring structural model data—including
frame assignments, load combinations, connectivity, and design forces—from
the ETABS API directly into formatted Excel worksheets.
"""

import pandas as pd
import xlwings as xw


class ETABSDataExporter:
    """Handles ETABS data extraction, processing, and exporting to Excel worksheets."""

    def __init__(self, etabs_instance):
        self.etabs = etabs_instance

    @staticmethod
    def _get_sheet(sheet_name: str):
        """Helper to get target sheet via xlwings caller or active workbook fallback."""
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
        """Dedicated helper function to clear sheet, export DataFrame, apply header coloring, and auto-fit columns."""
        sheet = cls._get_sheet(sheet_name)

        if clear_sheet:
            sheet.clear()

        # Write DataFrame
        sheet.range(start_cell).options(index=False).value = df

        # Format if DataFrame is not empty
        if not df.empty:
            if header_color:
                header_range = sheet.range(start_cell).expand("right")
                header_range.color = header_color
            if autofit:
                sheet.range(start_cell).expand().columns.autofit()

    def get_load_combinations(self, place_holder=None) -> list:
        """Extracts unique load combination definitions from ETABS."""
        raw_combos = self.etabs.get_data("Load Combination Definitions", place_holder)[
            "Name"
        ].tolist()
        return list(dict.fromkeys(raw_combos))

    def get_available_members(self, load_combos: list) -> list:
        """Extracts unique non-numeric frame member names from ETABS."""
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
        """Clears previous selections and writes selected load combos & members to Excel."""
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
        """Populates an Excel Data Validation drop-down menu with the selected load combinations."""
        if not load_combos:
            return

        sheet = self._get_sheet(sheet_name)
        target = sheet.range(dropdown_cell)
        combo_list_str = ",".join(load_combos)

        try:
            target.api.Validation.Delete()
            target.api.Validation.Add(
                3,  # Type: xlValidateList
                1,  # AlertStyle: xlValidAlertStop
                1,  # Operator: xlBetween
                combo_list_str,
            )
            target.value = load_combos[0]
        except Exception as e:
            print(
                f"Warning: Could not update dropdown menu on {sheet_name}!{dropdown_cell}: {e}"
            )

    def display_factored_loads(
        self,
        load_combos_selected: list = None,
        members_selected: list = None,
        sheet_name: str = "FACTORED LOADS",
        start_cell: str = "B2",
        header_color: tuple = (189, 215, 238),
    ) -> pd.DataFrame:
        """Extracts, converts to numeric, deduplicates, and exports design forces."""
        beam_forces = self.etabs.get_data("Design Forces - Beams", load_combos_selected)
        col_forces = self.etabs.get_data(
            "Design Forces - Columns", load_combos_selected
        )

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

        design_forces = design_forces[
            ~design_forces["UniqueName"].str.isnumeric()
        ].copy()

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

        # Export using helper function
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
        """Extracts frame assignments, section properties, rebar, material strengths, processes them, and writes to Excel."""
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

        frame_assignments = frame_assignments[
            ["Story", "UniqueName", "SectProp"]
        ].copy()

        if members_selected:
            frame_assignments = frame_assignments[
                frame_assignments["UniqueName"].isin(members_selected)
            ].copy()

        # 1. Process Rectangular Sections
        rect_cols = [
            c
            for c in frame_section_properties_rectangular.columns
            if any(k in c for k in ["Name", "Material", "t2", "t3", "DesignType"])
        ]
        frames_rectangular = frame_section_properties_rectangular[rect_cols].copy()
        frames_rectangular["t2"] = pd.to_numeric(
            frames_rectangular["t2"], errors="coerce"
        ).astype("Int64")
        frames_rectangular["t3"] = pd.to_numeric(
            frames_rectangular["t3"], errors="coerce"
        ).astype("Int64")
        frames_rectangular.rename(columns={"t2": "Width", "t3": "Depth"}, inplace=True)

        # 2. Process Circular Sections
        circ_cols = [
            c
            for c in frame_section_properties_circular.columns
            if any(k in c for k in ["Name", "Material", "t3", "DesignType"])
        ]
        frames_circular = frame_section_properties_circular[circ_cols].copy()
        frames_circular["t3"] = pd.to_numeric(
            frames_circular["t3"], errors="coerce"
        ).astype("Int64")
        frames_circular.rename(columns={"t3": "Diameter"}, inplace=True)

        all_properties = pd.concat(
            [frames_rectangular, frames_circular], ignore_index=True
        )
        frame_assignments = frame_assignments.merge(
            all_properties, left_on="SectProp", right_on="Name", how="left"
        ).drop(columns=["Name"], errors="ignore")

        # 3. Process Reinforcing
        beam_cols = [
            c
            for c in concrete_beam_reinforcing.columns
            if any(k in c for k in ["Name", "RebarMatL", "RebarMatC"])
        ]
        col_cols = [
            c
            for c in concrete_column_reinforcing.columns
            if any(k in c for k in ["Name", "RebarMatL", "RebarMatC"])
        ]
        frames_reinf = pd.concat(
            [
                concrete_beam_reinforcing[beam_cols],
                concrete_column_reinforcing[col_cols],
            ],
            ignore_index=True,
        )

        frame_assignments = frame_assignments.merge(
            frames_reinf, left_on="SectProp", right_on="Name", how="left"
        ).drop(columns=["Name"], errors="ignore")

        # 4. Material Strengths
        concrete_data = self.etabs.get_data(
            "Material Properties - Concrete Data", load_combos_selected
        )
        rebar_data = self.etabs.get_data(
            "Material Properties - Rebar Data", load_combos_selected
        )

        concrete_data["Fc"] = pd.to_numeric(concrete_data["Fc"], errors="coerce").round(
            2
        )
        rebar_data["Fy"] = pd.to_numeric(rebar_data["Fy"], errors="coerce").round(2)

        fc_map = dict(
            zip(
                concrete_data["Material"].astype(str).str.strip(),
                concrete_data["Fc"],
            )
        )
        fy_map = dict(
            zip(
                rebar_data["Material"].astype(str).str.strip(),
                rebar_data["Fy"],
            )
        )

        frame_assignments["Material"] = (
            frame_assignments["Material"].astype(str).str.strip().map(fc_map)
        )
        frame_assignments["RebarMatL"] = (
            frame_assignments["RebarMatL"].astype(str).str.strip().map(fy_map)
        )
        frame_assignments["RebarMatC"] = (
            frame_assignments["RebarMatC"].astype(str).str.strip().map(fy_map)
        )

        frame_assignments.rename(
            columns={
                "Material": "f'c",
                "RebarMatL": "fy",
                "RebarMatC": "fys",
            },
            inplace=True,
        )

        # 5. Reorder Columns
        desired_order = [
            "Story",
            "UniqueName",
            "SectProp",
            "f'c",
            "Width",
            "Depth",
            "Diameter",
            "DesignType",
            "fy",
            "fys",
        ]
        final_cols = [c for c in desired_order if c in frame_assignments.columns]
        frame_assignments = frame_assignments[final_cols]

        # Export using helper function
        self._write_dataframe_to_excel(
            df=frame_assignments,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )

        return frame_assignments

    def display_connectivity_data(
        self,
        sheet_name: str = "CONNECTIVITY",
        start_cell: str = "B2",
        header_color: tuple = (189, 215, 238),
        load_combos_selected: list = None,
    ) -> pd.DataFrame:
        """Combines Beam, Column, and Wall connectivity tables and maps DesignType using section property definitions and frame assignments."""

        def _to_dataframe(obj) -> pd.DataFrame:
            if isinstance(obj, pd.DataFrame):
                return obj
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, pd.DataFrame):
                        return v
                try:
                    return pd.DataFrame(obj)
                except Exception:
                    pass
            return pd.DataFrame()

        # 1. Fetch Beam, Column, and Wall Connectivity tables
        beam_conn = _to_dataframe(
            self.etabs.get_data("Beam Object Connectivity", load_combos_selected)
        )
        if not beam_conn.empty:
            beam_conn["DesignType"] = "Beam"

        col_conn = _to_dataframe(
            self.etabs.get_data("Column Object Connectivity", load_combos_selected)
        )
        if not col_conn.empty:
            col_conn["DesignType"] = "Column"

        wall_conn = _to_dataframe(
            self.etabs.get_data("Wall Object Connectivity", load_combos_selected)
        )
        if not wall_conn.empty:
            wall_conn["DesignType"] = "Wall"

        conn_df = pd.concat([beam_conn, col_conn, wall_conn], ignore_index=True)
        if conn_df.empty:
            print("Warning: No connectivity data retrieved from ETABS.")
            return pd.DataFrame()

        conn_df.columns = [str(c).strip() for c in conn_df.columns]

        # 2. Fetch Section Property Definitions
        rect_props = _to_dataframe(
            self.etabs.get_data(
                "Frame Section Property Definitions - Concrete Rectangular",
                load_combos_selected,
            )
        )
        circ_props = _to_dataframe(
            self.etabs.get_data(
                "Frame Section Property Definitions - Concrete Circle",
                load_combos_selected,
            )
        )

        prop_design_map = {}
        for df, default_type in [(rect_props, "Beam"), (circ_props, "Column")]:
            if not df.empty:
                for _, row in df.iterrows():
                    name = row.get("Name") or row.get("SectionName")
                    d_type = (
                        row.get("DesignType") or row.get("Design Type") or default_type
                    )
                    if name:
                        prop_design_map[str(name).strip()] = str(d_type).strip()

        # 3. Fetch Frame Assignments to map UniqueName -> DesignType
        frame_assigns = _to_dataframe(
            self.etabs.get_data(
                "Frame Assignments - Section Properties", load_combos_selected
            )
        )

        if not frame_assigns.empty:
            frame_assigns.columns = [str(c).strip() for c in frame_assigns.columns]
            sect_col = "AnalSect"
            if "AnalSect" not in frame_assigns.columns:
                sect_col = (
                    "SectProp"
                    if "SectProp" in frame_assigns.columns
                    else "Section Property"
                )

            frame_assigns["DesignType_Frame"] = frame_assigns[sect_col].map(
                prop_design_map
            )

            conn_df = conn_df.merge(
                frame_assigns[["UniqueName", "DesignType_Frame"]],
                on="UniqueName",
                how="left",
            )
            conn_df["DesignType"] = (
                conn_df["DesignType_Frame"]
                .combine_first(conn_df["DesignType"])
                .fillna("Beam")
            )
            conn_df.drop(columns=["DesignType_Frame"], inplace=True, errors="ignore")
        else:
            conn_df["DesignType"] = conn_df["DesignType"].fillna("Beam")

        # Export using helper function
        self._write_dataframe_to_excel(
            df=conn_df,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )

        return conn_df
