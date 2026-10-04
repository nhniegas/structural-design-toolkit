"""ETABS data extraction for the beam and column design.

Every table is returned as a DataFrame and kept in ``exporter.tables`` under
its name (``FACTORED LOADS``, ``SERVICE LOADS``, ``LIVE LOAD REDUCTION``,
``FRAME DATA``, ``CONNECTIVITY``, ``LOCAL AXES``, ``POINTS``), which the
design store saves for the beam and column steps.
"""

from __future__ import annotations

import pandas as pd

from etabs_api.workflows.analysis_forces import ForceOptions, factored_forces


class ETABSDataExporter:
    """Extract the ETABS tables the beam and column design need."""

    def __init__(self, etabs_instance):
        self.etabs = etabs_instance
        self.last_forces = None  # FactoredForces of the last display_factored_loads
        self.tables: dict[str, pd.DataFrame] = {}
        # {standard deflection combination: the model's combination for that role};
        # None: the standard names (model_inputs.ask_deflection_roles)
        self.deflection_roles: dict[str, str] | None = None

    def _write_dataframe_to_excel(self, df: pd.DataFrame, sheet_name: str, **_) -> None:
        """Keep a table under its name (the name of its old workbook sheet)."""
        self.tables[sheet_name] = df

    def get_load_combinations(self, place_holder=None) -> list:
        """Return unique load combination names."""
        raw_combos = self.etabs.get_data("Load Combination Definitions", place_holder)[
            "Name"
        ].tolist()
        return list(dict.fromkeys(raw_combos))

    def get_available_members(self, load_combos: list | None = None) -> list:
        """Return every named (non-numeric) beam and column of the model.

        ``load_combos`` is accepted only for caller compatibility: the members
        no longer depend on a design run.
        """
        names = []
        for table_name in ("Beam Object Connectivity", "Column Object Connectivity"):
            table = self.etabs.get_data(table_name)
            if isinstance(table, pd.DataFrame) and "UniqueName" in table.columns:
                names += table["UniqueName"].astype(str).tolist()
        if not names:
            raise ValueError("The ETABS model has no beams or columns.")
        from etabs_api.workflows.analysis_forces import designed_names

        return designed_names(self.etabs, dict.fromkeys(names))

    def display_factored_loads(
        self,
        load_combos_selected: list = None,
        members_selected: list = None,
        sheet_name: str = "FACTORED LOADS",
        start_cell: str = "B2",
        header_color: tuple = (189, 215, 238),
        options: ForceOptions | None = None,
    ) -> pd.DataFrame:
        """Factored forces from the analysis results, written in kN and kN-m.

        No ETABS design is run: the forces are combined from the load case
        results (see ``etabs_api.workflows.analysis_forces``), with the live
        load reduction and pattern live load chosen in ``options``.
        """
        result = factored_forces(
            self.etabs, list(load_combos_selected or []), members_selected, options
        )
        self.last_forces = result
        forces = result.table.copy()
        if forces.empty:
            raise ValueError("No member forces were found for the chosen combinations.")
        forces[["P", "V2", "V3"]] = forces[["P", "V2", "V3"]] / 1000
        if "P_sustained" in forces.columns:
            forces["P_sustained"] = forces["P_sustained"] / 1000
        forces[["T", "M2", "M3"]] = forces[["T", "M2", "M3"]] / 1000000
        self._write_dataframe_to_excel(
            df=forces,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )
        if result.reductions:
            self.display_live_load_reduction(result.reductions)
        return forces

    def display_service_loads(
        self, members_selected: list | None = None, sheet_name: str = "SERVICE LOADS",
        start_cell: str = "B2",
    ) -> pd.DataFrame | None:
        """Service moments of the beams for the deflection checks, in kN-m.

        Uses the deflection combinations (``DEF 100`` to ``DEF 103``) that are in
        the model; returns None, writing nothing, when there are none. Each row
        also has, for the beam's combination, the downward tip deflection (mm)
        from the rotation of its I end and of its J end, used when the beam is a
        cantilever supported at that end.
        """
        from design.beam_deflection import DEFLECTION_COMBOS

        from etabs_api.workflows.load_combinations import ensure_deflection_combinations

        # Each role reads the model's own combination where the user picked one;
        # a role mapped to its standard name is added when the model lacks it.
        roles = self.deflection_roles or {name: name for name in DEFLECTION_COMBOS}
        added = ensure_deflection_combinations(
            self.etabs.sap_model, only={role for role, name in roles.items() if role == name})
        if added:
            print("Deflection combinations added to the model: " + ", ".join(added))
        combos = self.get_load_combinations()
        roles = {role: name for role, name in roles.items() if name in combos}
        present = list(dict.fromkeys(roles.values()))
        if not present:
            return None
        beams = self.etabs.get_data("Beam Object Connectivity")
        beam_names = set(beams["UniqueName"].astype(str))
        if members_selected:
            beam_names &= set(map(str, members_selected))
        result = factored_forces(self.etabs, present, sorted(beam_names))
        table = result.table
        table = table[table["UniqueName"].astype(str).isin(beam_names)].copy()
        if table.empty:
            return None
        table["M3"] = table["M3"] / 1e6
        table["V2"] = table["V2"] / 1e3
        table = table[["Story", "UniqueName", "Combo", "Station", "M3", "V2"]]
        # The deflection check knows the roles by their standard names: the rows
        # of the model's own combination are relabelled with the role they serve.
        table["Model combination"] = table["Combo"].astype(str)
        relabelled = [table[table["Combo"].astype(str).eq(name)].assign(Combo=role)
                      for role, name in roles.items()]
        table = pd.concat(relabelled, ignore_index=True)
        if table.empty:
            return None
        actual_of = {role: name for role, name in roles.items()}

        # downward tip deflection from a rigid rotation of the support joint:
        # -(R x r)_z = -L (Rx dy - Ry dx), r from the support to the tip
        points = self.etabs.get_data("Point Object Connectivity")
        xyz = {str(n): (float(x), float(y)) for n, x, y in zip(
            points["UniqueName"], pd.to_numeric(points["X"]), pd.to_numeric(points["Y"]))}
        tables = self.etabs.sap_model.DatabaseTables
        tables.SetLoadCasesSelectedForDisplay([])
        tables.SetLoadCombinationsSelectedForDisplay(present)
        moves = self.etabs._read_database_table("Joint Displacements")
        moves = moves[moves["OutputCase"].astype(str).isin(present)]
        by_actual = {(str(j), str(c)): (float(rx), float(ry)) for j, c, rx, ry in zip(
            moves["UniqueName"], moves["OutputCase"], pd.to_numeric(moves["Rx"]),
            pd.to_numeric(moves["Ry"]))}
        joints = {joint for joint, _ in by_actual}
        rotation = {(joint, role): by_actual[(joint, actual)]
                    for role, actual in actual_of.items() for joint in joints
                    if (joint, actual) in by_actual}
        ends = {str(n): (str(i), str(j)) for n, i, j in zip(
            beams["UniqueName"], beams["UniquePtI"], beams["UniquePtJ"])}

        def tip(member: str, combo: str, support_at_i: bool) -> float:
            joint_i, joint_j = ends.get(member, ("", ""))
            if joint_i not in xyz or joint_j not in xyz:
                return 0.0
            root, free = (joint_i, joint_j) if support_at_i else (joint_j, joint_i)
            dx, dy = xyz[free][0] - xyz[root][0], xyz[free][1] - xyz[root][1]
            rx, ry = rotation.get((root, combo), (0.0, 0.0))
            return -(rx * dy - ry * dx)

        keys = table[["UniqueName", "Combo"]].drop_duplicates()
        tips = {(m, c): (tip(m, c, True), tip(m, c, False))
                for m, c in zip(keys["UniqueName"].astype(str), keys["Combo"].astype(str))}
        table["Tip from rotation at I (mm)"] = [
            tips[(str(m), str(c))][0] for m, c in zip(table["UniqueName"], table["Combo"])]
        table["Tip from rotation at J (mm)"] = [
            tips[(str(m), str(c))][1] for m, c in zip(table["UniqueName"], table["Combo"])]

        stories = self.etabs.get_data("Story Definitions")["Story"].astype(str).tolist()
        order = {name: index for index, name in enumerate(stories)}  # listed top first
        top_story = min(table["Story"].astype(str), key=lambda s: order.get(s, len(order)))
        table["Roof level"] = table["Story"].astype(str).eq(top_story)

        self._write_dataframe_to_excel(df=table, sheet_name=sheet_name)
        return table

    def display_live_load_reduction(
        self, reductions: dict, sheet_name: str = "LIVE LOAD REDUCTION", start_cell: str = "B2"
    ) -> pd.DataFrame:
        """Write the live load reduction of every member to its own sheet."""
        table = pd.DataFrame(
            [
                {
                    "UniqueName": member,
                    "Code": getattr(r, "code", "NSCP"),
                    "Tributary method": r.method,
                    "Tributary area (m2)": round(r.area_m2, 2),
                    "Reducible live (kPa)": round(r.reducible_kpa, 2),
                    "Area above 4.8 kPa (m2)": round(r.heavy_m2, 2),
                    "Levels": r.levels,
                    "Reduction (%)": round(r.percent, 2),
                    "Governed by": r.limit,
                    "Factor on reducible live": round(r.factor, 4),
                }
                for member, r in reductions.items()
            ]
        )
        self._write_dataframe_to_excel(df=table, sheet_name=sheet_name)
        return table

    def display_frame_data(
        self,
        members_selected: list = None,
        sheet_name: str = "FRAME DATA",
        start_cell: str = "B2",
        load_combos_selected: list = None,
        header_color: tuple = (189, 215, 238),
    ) -> pd.DataFrame:
        """Extract frame assignments and related section data.

        These tables do not depend on load combinations, so each is read once.
        ``load_combos_selected`` is accepted only for caller compatibility.
        """
        frame_assignments = self.etabs.get_data(
            "Frame Assignments - Section Properties"
        )
        frame_section_properties_rectangular = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Rectangular"
        )
        frame_section_properties_circular = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Circle"
        )
        concrete_beam_reinforcing = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Beam Reinforcing"
        )
        concrete_column_reinforcing = self.etabs.get_data(
            "Frame Section Property Definitions - Concrete Column Reinforcing"
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
            "Material Properties - Concrete Data"
        )
        rebar_data = self.etabs.get_data(
            "Material Properties - Rebar Data"
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
        connectivity = self._with_beam_lines(connectivity)
        self._write_dataframe_to_excel(
            df=connectivity,
            sheet_name=sheet_name,
            start_cell=start_cell,
            header_color=header_color,
        )
        return connectivity

    def _with_beam_lines(self, connectivity: pd.DataFrame) -> pd.DataFrame:
        """Add ``Line``: the beam line of every beam.

        A tagged beam carries its line in the name (``2GX-1``, ``2GX-1A``).
        For any other name the line is found from the geometry: beams in line
        that meet at a joint with no column. The deflection check and the
        design loop treat the beams of one line as one.
        """
        if connectivity.empty or "UniqueName" not in connectivity.columns:
            return connectivity
        from design.beam_deflection import _MARK
        from etabs_api.workflows.model_inputs import geometric_lines

        try:
            points = self.etabs.get_data("Point Object Connectivity")
        except RuntimeError:
            points = None
        geometric = geometric_lines(connectivity, points)
        lines = []
        for name, kind in zip(connectivity["UniqueName"].astype(str),
                              connectivity["DesignType"].astype(str)):
            match = _MARK.match(name)
            if kind != "Beam":
                lines.append(None)
            elif match:
                lines.append((match.group(1) + match.group(2) + "-" + match.group(3)).upper())
            else:
                lines.append(geometric.get(name, name))
        connectivity = connectivity.copy()
        connectivity["Line"] = lines
        return connectivity

    def display_orientation_data(self) -> None:
        """Column local axes and joint coordinates (the SMRF joint checks need them)."""
        self._write_dataframe_to_excel(
            df=self.etabs.get_data("Frame Assignments - Local Axes"), sheet_name="LOCAL AXES")
        self._write_dataframe_to_excel(
            df=self.etabs.get_data("Point Object Connectivity"), sheet_name="POINTS")
        # The supports of the model: the column slenderness takes a footing as
        # fixed or pinned from them. A model with no restraint has no table.
        try:
            supports = self.etabs.get_data("Joint Assignments - Restraints")
        except RuntimeError:
            supports = None
        if supports is None or "UniqueName" not in getattr(supports, "columns", ()):
            supports = pd.DataFrame(columns=["UniqueName", "UX", "UY", "UZ", "RX", "RY", "RZ"])
        self._write_dataframe_to_excel(df=supports, sheet_name="SUPPORTS")

    def extract_all(self, load_combos: list, members: list | None,
                    options: ForceOptions | None) -> list[str]:
        """Every table of the beam and column design. Returns notes worth printing."""
        self.display_factored_loads(load_combos, members, options=options)
        notes = list(self.last_forces.notes)
        try:
            self.display_service_loads(members)
        except (ValueError, KeyError, RuntimeError) as error:
            notes.append(f"Service loads for the deflection checks were not read: {error}")
        self.display_frame_data(members_selected=members)
        self.display_connectivity_data()
        self.display_orientation_data()
        return notes
