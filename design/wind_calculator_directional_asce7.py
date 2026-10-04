"""
ASCE 7 Directional Procedure Wind Load Calculator (MWFRS).

All reference tables (Kz, wall Cp, roof Cp, GCpi) are module-level constants,
so the calculator class needs no workbook: give it the inputs and call
``calculate()``.

Run it from the terminal with ``python main.py wind``: a dialog asks for the
inputs, then whether to print the result tables, export the PDF report, or
both. In code, the same steps are ``calculate(values)``,
``summary_text(calculator)`` and ``export_pdf(calculator, path)``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from pylatex import Document, Itemize, Package, Section, Subsection, Tabular
from pylatex.utils import NoEscape

if __package__ in (None, ""):
    # Run as a script: make the project folder importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.design_cli import Field, run_design  # noqa: E402

# =============================================================================
# 1. REFERENCE TABLES  (ASCE 7 - SI units, extracted from the source workbook)
# =============================================================================
# These replace the 5 ranges that used to be read live off the Excel sheet
# (table_vel_pres_coef @ L5, wall_press_coeff_data @ Q4, table_int_pres_coef
# @ L31, table_roof_over_10 @ R14, table_roof_under_10 @ S23).

# --- Table 26.10-1: Velocity pressure exposure coefficient Kz / Kh ----------
# Index = height above ground (m); columns = Exposure Category B / C / D
KZ_TABLE = pd.DataFrame(
    {
        "B": [
            0.57,
            0.57,
            0.62,
            0.66,
            0.70,
            0.76,
            0.81,
            0.85,
            0.89,
            0.93,
            0.96,
            0.99,
            1.04,
            1.09,
            1.13,
            1.17,
            1.20,
            1.28,
            1.35,
            1.41,
            1.47,
            1.52,
            1.56,
        ],
        "C": [
            0.85,
            0.85,
            0.90,
            0.94,
            0.98,
            1.04,
            1.09,
            1.13,
            1.17,
            1.21,
            1.24,
            1.26,
            1.31,
            1.36,
            1.39,
            1.43,
            1.46,
            1.53,
            1.59,
            1.64,
            1.69,
            1.73,
            1.77,
        ],
        "D": [
            1.03,
            1.03,
            1.08,
            1.12,
            1.16,
            1.22,
            1.27,
            1.31,
            1.34,
            1.38,
            1.40,
            1.43,
            1.48,
            1.52,
            1.55,
            1.58,
            1.61,
            1.68,
            1.73,
            1.78,
            1.82,
            1.86,
            1.89,
        ],
    },
    index=[
        0,
        4.6,
        6.1,
        7.6,
        9.1,
        12.2,
        15.2,
        18.3,
        21.3,
        24.4,
        27.4,
        30.5,
        36.6,
        42.7,
        48.8,
        54.9,
        61,
        76.2,
        91.4,
        106.7,
        121.9,
        137.2,
        152.4,
    ],
)

# --- Fig. 27.3-1: Wall pressure coefficients (raw layout, positional) ------
# Columns: Surface | L/B | Cp | Use With  (rows 1-4 = Leeward Wall, by L/B)
WALL_CP_RAW = pd.DataFrame(
    [
        ["Windward wall", "All values", 0.8, "qz"],
        ["Leeward Wall", 0, -0.5, "qh"],
        ["Leeward Wall", 1, -0.5, "qh"],
        ["Leeward Wall", 2, -0.3, "qh"],
        ["Leeward Wall", 4, -0.2, "qh"],
        ["Sidewall", "All values", -0.7, "qh"],
    ],
    columns=["Surface", "L/B", "Cp", "Use With"],
)

# --- Table 26.13-1: Internal pressure coefficient GCpi, by enclosure -------
GCPI_TABLE = pd.DataFrame(
    {
        "Internal Pressure": ["Moderate", "High", "Moderate", "Negligible"],
        "GCpi (+)": [0.18, 0.55, 0.18, 0.0],
        "GCpi (-)": [-0.18, -0.55, -0.18, 0.0],
    },
    index=[
        "Enclosed Buildings",
        "Partially Enclosed Buildings",
        "Partially Open Buildings",
        "Open Buildings",
    ],
)

# --- Fig. 27.3-1: Roof Cp, normal to ridge, slope >= 10 degrees ------------
# 9 windward angle columns (10-80 deg) + 3 leeward angle columns (10-20 deg).
# 6 data rows = 3 h/L tiers (<0.25, 0.5, >1) x 2 sub-rows (windward w1, w2);
# the leeward value is read from the same "w2" (second) sub-row of each tier.
# For slopes of 60 degrees and steeper the code gives Cp = 0.01 * theta for both
# windward cases; the 60 and 80 degree columns (0.6, 0.8) reproduce that line
# exactly when interpolated.
_ROOF_OVER_10_WINDWARD_ANGLES = [10, 15, 20, 25, 30, 35, 45, 60, 80]
_ROOF_OVER_10_LEEWARD_ANGLES = [10, 15, 20]
_N_WINDWARD = len(_ROOF_OVER_10_WINDWARD_ANGLES)
TABLE_ROOF_OVER_10 = pd.DataFrame(
    [
        # windward (9 cols)                                    leeward (3 cols)
        # 35 deg: (0.0, 0.4), (-0.2, 0.3), (-0.2, 0.2); 45 deg at h/L <= 0.25 is the
        # single value 0.4 (both cases). The 35 deg column used to repeat 30 deg.
        [-0.7, -0.5, -0.3, -0.2, -0.2, 0.0, 0.4, 0.6, 0.8, 0.0, 0.0, 0.0],  # h/L<0.25, w1
        [-0.18, 0.0, 0.2, 0.3, 0.3, 0.4, 0.4, 0.6, 0.8, -0.3, -0.5, -0.6],  # h/L<0.25, w2
        [-0.9, -0.7, -0.4, -0.3, -0.2, -0.2, 0.0, 0.6, 0.8, 0.0, 0.0, 0.0],  # h/L=0.5, w1
        [-0.18, -0.18, 0.0, 0.2, 0.2, 0.3, 0.4, 0.6, 0.8, -0.5, -0.5, -0.6],  # h/L=0.5, w2
        [-1.3, -1.0, -0.7, -0.5, -0.3, -0.2, 0.0, 0.6, 0.8, 0.0, 0.0, 0.0],  # h/L>1, w1
        [-0.18, -0.18, -0.18, 0.0, 0.2, 0.2, 0.3, 0.6, 0.8, -0.7, -0.6, -0.6],  # h/L>1, w2
    ],
    columns=[*_ROOF_OVER_10_WINDWARD_ANGLES, *_ROOF_OVER_10_LEEWARD_ANGLES],
)

# --- Fig. 27.3-2: Roof Cp, normal/parallel to ridge, slope < 10 degrees ----
# Rows 0-3: h/L <= 0.5 stepped zones (0-h/2, h/2-h, h-2h, >2h)
# Rows 4-5: h/L >= 1.0 stepped zones (0-h/2, >h/2)
# Columns: [Cp (primary), Cp (with GCpi note, secondary)]
TABLE_ROOF_UNDER_10 = pd.DataFrame(
    [
        [-0.9, -0.18],
        [-0.9, -0.18],
        [-0.5, -0.18],
        [-0.3, -0.18],
        [-1.3, -0.18],
        [-0.7, -0.18],
    ],
    columns=["Cp_1", "Cp_2"],
)


# =============================================================================
# 2. CALCULATOR CLASS
# =============================================================================
@dataclass
class WindLoadCalculatorDirectionalASCE7:
    """Calculates MWFRS wind loads on structures per ASCE 7 (Directional Procedure)."""

    building_class: str
    basic_wind_speed: float
    enclosure_class: str
    exposure_category: str
    wind_dir_factor: float
    topographic_factor: float
    ground_elevation_factor: float
    gust_effect_factor: float
    l_input: float
    b_input: float
    ridge_direction_input: str
    raw_heights: str
    eave_height: float
    apex_height: float

    # Populated during calculate()
    heights_list: list = field(default_factory=list, init=False)
    mean_roof_height: float = field(init=False)
    vel_pres: float = field(default=None, init=False)
    gcpi_pos: float = field(default=None, init=False)
    gcpi_neg: float = field(default=None, init=False)

    def __post_init__(self):
        self.mean_roof_height = (self.eave_height + self.apex_height) / 2

    # -------------------------------------------------------------------
    # HELPER: linear interpolation against a reference DataFrame
    # -------------------------------------------------------------------
    @staticmethod
    def interpolate_table_value(
        reference_table, target_column_name, lookup_input_value
    ):
        """Linear interpolation of a column against the table's index."""
        col_name = str(target_column_name).strip()
        if col_name not in reference_table.columns:
            raise KeyError(f"Column '{col_name}' does not exist in the reference table.")
        x_pts = np.array(reference_table.index, dtype=float)
        y_pts = np.array(reference_table[col_name], dtype=float)
        return float(np.interp(float(lookup_input_value), x_pts, y_pts))

    # -------------------------------------------------------------------
    # STEP 1: Velocity pressure profile (qz)
    # -------------------------------------------------------------------
    def generate_velocity_pressure_profile(
        self, heights_list, exposure_input, vel_pres
    ):
        """Interpolates Kz at every considered height and computes qz = Kz * vel_pres."""
        try:
            target_column = str(exposure_input).strip().upper()
            if target_column not in KZ_TABLE.columns:
                raise ValueError(
                    f"Exposure Category '{exposure_input}' column not found."
                )

            all_heights = set(heights_list)
            all_heights.add(float(self.eave_height))
            all_heights.add(float(self.mean_roof_height))
            all_heights.add(float(self.apex_height))
            sorted_heights = sorted(all_heights)

            kz_results = []
            for h in sorted_heights:
                kz_val = self.interpolate_table_value(KZ_TABLE, target_column, h)
                if kz_val is None:
                    continue
                if h == float(self.mean_roof_height):
                    label = "Mean Roof Height"
                elif h == float(self.eave_height):
                    label = "Eave Height"
                elif h == float(self.apex_height):
                    label = "Apex Height"
                else:
                    label = "User Input"
                kz_results.append(
                    {
                        "Height (m)": h,
                        "Type": label,
                        f"Kz ({target_column})": round(kz_val, 3),
                    }
                )

            table_vel_pressure = pd.DataFrame(kz_results)
            if table_vel_pressure.empty:
                return table_vel_pressure

            table_vel_pressure["qz (Pa)"] = round(
                table_vel_pressure[f"Kz ({target_column})"] * float(vel_pres), 3
            )
            return table_vel_pressure

        except Exception as e:
            raise ValueError(f"Velocity pressure profile failed: {e}") from e

    # -------------------------------------------------------------------
    # STEP 2: Wall Cp table
    # -------------------------------------------------------------------
    def generate_wall_cp_table(self, l_value, b_value, ridge_direction):
        """Builds the 4-row wall Cp table (windward / leeward-normal / leeward-parallel / side)."""
        try:
            l_value, b_value = float(l_value), float(b_value)
            ridge_dir = str(ridge_direction).strip().upper()

            if ridge_dir == "L":
                lb_normal, lb_parallel = b_value / l_value, l_value / b_value
            else:
                lb_normal, lb_parallel = l_value / b_value, b_value / l_value

            leeward_lookup = pd.DataFrame(
                data={"Cp": WALL_CP_RAW.loc[1:4, "Cp"].astype(float).tolist()},
                index=WALL_CP_RAW.loc[1:4, "L/B"].astype(float).tolist(),
            )

            cp_normal = self.interpolate_table_value(leeward_lookup, "Cp", lb_normal)
            cp_parallel = self.interpolate_table_value(
                leeward_lookup, "Cp", lb_parallel
            )

            wall_data = [
                {
                    "Surface": "Windward wall",
                    "Wind direction": "All",
                    "L/B": "All",
                    "Cp": 0.80,
                },
                {
                    "Surface": "Leeward wall",
                    "Wind direction": "Normal to ridge",
                    "L/B": round(lb_normal, 2),
                    "Cp": round(cp_normal, 2),
                },
                {
                    "Surface": "",
                    "Wind direction": "Parallel to ridge",
                    "L/B": round(lb_parallel, 2),
                    "Cp": round(cp_parallel, 2),
                },
                {
                    "Surface": "Side wall",
                    "Wind direction": "All",
                    "L/B": "All",
                    "Cp": -0.70,
                },
            ]
            return pd.DataFrame(wall_data)

        except Exception as e:
            raise ValueError(f"Wall Cp table failed: {e}") from e

    # -------------------------------------------------------------------
    # STEP 3: Roof Cp (normal or parallel to ridge)
    # -------------------------------------------------------------------
    def generate_roof_cp(self, b_value, l_value, ridge_direction, wind_direction):
        """
        Branches by wind direction, then by roof slope threshold (10 deg) for
        wind normal to the ridge; wind parallel to the ridge always uses the
        low-slope stepped-zone method regardless of pitch.
        """

        def _process_stepped_zones(h_over_l, current_wind, theta):
            """Low-slope (<10 deg normal) / parallel stepped-distance-zone lookup."""
            x_hl = [0.5, 1.0]
            col_hl_label = f"h/L = {h_over_l:.2f}"

            if h_over_l <= 0.5:
                rows = [TABLE_ROOF_UNDER_10.iloc[i] for i in range(4)]
                zone_names = ["0 to h/2", "h/2 to h", "h to 2h", ">2h"]
                surfaces = [
                    "Roof (0 to h/2)",
                    "Roof (h/2 to h)",
                    "Roof (h to 2h)",
                    "Roof (>2h)",
                ]
            elif h_over_l >= 1.0:
                rows = [TABLE_ROOF_UNDER_10.iloc[4], TABLE_ROOF_UNDER_10.iloc[5]]
                zone_names = ["0 to h/2", ">h/2"]
                surfaces = ["Roof (0 to h/2)", "Roof (>h/2)"]
            else:
                # Interpolate between the h/L=0.5 and h/L=1.0 rows, zone by zone
                pairs = [(0, 4), (1, 5), (2, 5), (3, 5)]
                rows = []
                for lo_i, hi_i in pairs:
                    c1 = self.interpolate_table_value(
                        pd.DataFrame(
                            {
                                "Val": [
                                    TABLE_ROOF_UNDER_10.iloc[lo_i, 0],
                                    TABLE_ROOF_UNDER_10.iloc[hi_i, 0],
                                ]
                            },
                            index=x_hl,
                        ),
                        "Val",
                        h_over_l,
                    )
                    c2 = self.interpolate_table_value(
                        pd.DataFrame(
                            {
                                "Val": [
                                    TABLE_ROOF_UNDER_10.iloc[lo_i, 1],
                                    TABLE_ROOF_UNDER_10.iloc[hi_i, 1],
                                ]
                            },
                            index=x_hl,
                        ),
                        "Val",
                        h_over_l,
                    )
                    rows.append({"Cp_1": c1, "Cp_2": c2})
                zone_names = ["0 to h/2", "h/2 to h", "h to 2h", ">2h"]
                surfaces = [
                    "Roof (0 to h/2)",
                    "Roof (h/2 to h)",
                    "Roof (h to 2h)",
                    "Roof (>2h)",
                ]

            display_rows, payload_rows = [], []
            for i, (row, zone, surf) in enumerate(zip(rows, zone_names, surfaces)):
                c1, c2 = round(float(row["Cp_1"]), 2), round(float(row["Cp_2"]), 2)
                display_rows.append(
                    {
                        "Surface": f"Roof ({current_wind})" if i == 0 else "",
                        "Distance from Windward Edge": zone,
                        col_hl_label: f"{c1}, {c2}*",
                    }
                )
                payload_rows.append({"Surface": surf, "C_p_1": c1, "C_p_2": c2})

            return (
                pd.DataFrame(display_rows),
                pd.DataFrame(payload_rows),
                theta,
                h_over_l,
            )

        try:
            eave, apex = float(self.eave_height), float(self.apex_height)
            b, l = float(b_value), float(l_value)
            h = (eave + apex) / 2.0
            current_wind = str(wind_direction).strip().capitalize()
            ridge_upper = str(ridge_direction).strip().upper()

            if current_wind == "Normal":
                run_dist = (b / 2.0) if ridge_upper == "L" else (l / 2.0)
                l_wind = b if ridge_upper == "L" else l
                theta = np.degrees(np.arctan((apex - eave) / run_dist))
                h_over_l = h / l_wind

                print("\n--- Wind Normal to Ridge Engine ---")
                print(
                    f"h: {round(h, 2)}  L_wind: {round(l_wind, 2)}  "
                    f"h/L: {round(h_over_l, 2)}  theta: {round(theta, 2)} deg"
                )

                if theta < 10.0:
                    print("Slope < 10 deg: using stepped-zone table.")
                    return _process_stepped_zones(h_over_l, current_wind, theta)

                print("Slope >= 10 deg: bilinear (angle x h/L) interpolation.")
                angle_w = np.array(_ROOF_OVER_10_WINDWARD_ANGLES, dtype=float)
                angle_l = np.array(_ROOF_OVER_10_LEEWARD_ANGLES, dtype=float)
                lower_angle = max(
                    [a for a in angle_w if a <= theta], default=angle_w.min()
                )
                upper_angle = min(
                    [a for a in angle_w if a >= theta], default=angle_w.max()
                )
                y_hl = [0.25, 0.5, 1.0]

                w1_low, w1_act, w1_high = [], [], []
                w2_low, w2_act, w2_high = [], [], []
                l1_low, l1_act, l1_high = [], [], []

                for tier in range(3):
                    r1, r2 = tier * 2, tier * 2 + 1
                    df_w1 = pd.DataFrame(
                        {"Val": TABLE_ROOF_OVER_10.iloc[r1, :_N_WINDWARD].values},
                        index=angle_w,
                    )
                    df_w2 = pd.DataFrame(
                        {"Val": TABLE_ROOF_OVER_10.iloc[r2, :_N_WINDWARD].values},
                        index=angle_w,
                    )
                    df_l1 = pd.DataFrame(
                        {"Val": TABLE_ROOF_OVER_10.iloc[r2, _N_WINDWARD:].values},
                        index=angle_l,
                    )

                    for target, low_l, act_l, high_l in (
                        (df_w1, w1_low, w1_act, w1_high),
                        (df_w2, w2_low, w2_act, w2_high),
                        (df_l1, l1_low, l1_act, l1_high),
                    ):
                        low_l.append(
                            self.interpolate_table_value(target, "Val", lower_angle)
                        )
                        act_l.append(self.interpolate_table_value(target, "Val", theta))
                        high_l.append(
                            self.interpolate_table_value(target, "Val", upper_angle)
                        )

                def _interp_hl(series):
                    return self.interpolate_table_value(
                        pd.DataFrame({"Val": series}, index=y_hl), "Val", h_over_l
                    )

                cp_w1_low, cp_w1_act, cp_w1_high = (
                    _interp_hl(w1_low),
                    _interp_hl(w1_act),
                    _interp_hl(w1_high),
                )
                cp_w2_low, cp_w2_act, cp_w2_high = (
                    _interp_hl(w2_low),
                    _interp_hl(w2_act),
                    _interp_hl(w2_high),
                )
                cp_l1_low, cp_l1_act, cp_l1_high = (
                    _interp_hl(l1_low),
                    _interp_hl(l1_act),
                    _interp_hl(l1_high),
                )

                col_low, col_act, col_high = (
                    f"{lower_angle:g}\u00b0",
                    f"{theta:.1f}\u00b0",
                    f"{upper_angle:g}\u00b0",
                )
                display_rows = [
                    {
                        "Surface": "Windward roof",
                        col_low: round(cp_w1_low, 2),
                        col_act: round(cp_w1_act, 2),
                        col_high: round(cp_w1_high, 2),
                    },
                    {
                        "Surface": "",
                        col_low: round(cp_w2_low, 2),
                        col_act: round(cp_w2_act, 2),
                        col_high: round(cp_w2_high, 2),
                    },
                    {
                        "Surface": "Leeward roof",
                        col_low: round(cp_l1_low, 2),
                        col_act: round(cp_l1_act, 2),
                        col_high: round(cp_l1_high, 2),
                    },
                ]
                df_profile_payload = pd.DataFrame(
                    [
                        {
                            "Surface": "Windward roof",
                            "C_p_1": round(cp_w1_act, 2),
                            "C_p_2": round(cp_w2_act, 2),
                        },
                        {
                            "Surface": "Leeward roof",
                            "C_p_1": round(cp_l1_act, 2),
                            "C_p_2": round(cp_l1_act, 2),
                        },
                    ]
                )
                return pd.DataFrame(display_rows), df_profile_payload, theta, h_over_l

            # Wind PARALLEL to ridge: always stepped-zone method, any pitch
            run_dist = (l / 2.0) if ridge_upper == "L" else (b / 2.0)
            l_wind = l if ridge_upper == "L" else b
            theta = np.degrees(np.arctan((apex - eave) / run_dist))
            h_over_l = h / l_wind

            print("\n--- Wind Parallel to Ridge Engine ---")
            print(
                f"h: {round(h, 2)}  l_wind: {round(l_wind, 2)}  "
                f"h/L: {round(h_over_l, 2)}  theta: {round(theta, 2)} deg"
            )
            return _process_stepped_zones(h_over_l, current_wind, theta)

        except Exception as e:
            raise ValueError(f"Roof Cp table ({wind_direction}) failed: {e}") from e

    # -------------------------------------------------------------------
    # STEP 4a: MWFRS summary - wind normal to ridge
    # -------------------------------------------------------------------
    def generate_mwfrs_normal_to_ridge_table(
        self,
        df_wall_cp,
        df_roof_payload,
        df_velocity_profile,
        gust_effect_factor,
        gcpi_pos,
        gcpi_neg,
    ):
        """MWFRS pressure summary, wind normal to ridge (windward wall stops at eave)."""
        g_pos, g_neg = float(gcpi_pos), float(gcpi_neg)

        try:
            q_h = float(
                df_velocity_profile.loc[
                    df_velocity_profile["Type"].str.contains("Mean", na=False),
                    "qz (Pa)",
                ].values[0]
            )
        except (IndexError, KeyError, ValueError):
            q_h = float(df_velocity_profile["qz (Pa)"].iloc[-1])

        summary_rows = []

        # Windward wall (step-by-step heights up to eave)
        cp_ww = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Windward wall", "Cp"].values[0]
        )
        df_wall_profile = df_velocity_profile[
            ~df_velocity_profile["Type"].str.contains("Mean|Apex", case=False, na=False)
        ].reset_index(drop=True)

        for idx, row in df_wall_profile.iterrows():
            z_val, q_z = float(row["Height (m)"]), float(row["qz (Pa)"])
            p_pos = (q_z * gust_effect_factor * cp_ww) - (q_h * g_pos)
            p_neg = (q_z * gust_effect_factor * cp_ww) - (q_h * g_neg)
            summary_rows.append(
                {
                    "Surface": "Windward wall" if idx == 0 else "",
                    "z (m)": f"{z_val:g}",
                    "q (Pa)": round(q_z, 2),
                    "G": gust_effect_factor,
                    "C_p": cp_ww,
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # Leeward & side walls (uniform, at q_h)
        cp_lw = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Leeward wall", "Cp"].iloc[0]
        )
        cp_sw = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Side wall", "Cp"].values[0]
        )
        for label, cp_val in [("Leeward wall", cp_lw), ("Side walls", cp_sw)]:
            p_pos = (q_h * gust_effect_factor * cp_val) - (q_h * g_pos)
            p_neg = (q_h * gust_effect_factor * cp_val) - (q_h * g_neg)
            summary_rows.append(
                {
                    "Surface": label,
                    "z (m)": "All",
                    "q (Pa)": round(q_h, 2),
                    "G": gust_effect_factor,
                    "C_p": cp_val,
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # Roof (pitched windward/leeward, or flat stepped zones)
        for _, row in df_roof_payload.iterrows():
            zone_label = str(row["Surface"]).strip()

            if "windward" in zone_label.lower():
                cp_1 = float(row["C_p_1"])
                p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
                p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)
                summary_rows.append(
                    {
                        "Surface": "Windward roof*",
                        "z (m)": "\u2014",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_1,
                        "Net (+GCpi)": round(p_pos_1, 2),
                        "Net (-GCpi)": round(p_neg_1, 2),
                    }
                )
                if (
                    "C_p_2" in row
                    and not np.isnan(row["C_p_2"])
                    and row["C_p_2"] != cp_1
                ):
                    cp_2 = float(row["C_p_2"])
                    p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                    p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)
                    summary_rows.append(
                        {
                            "Surface": "",
                            "z (m)": "\u2014",
                            "q (Pa)": round(q_h, 2),
                            "G": gust_effect_factor,
                            "C_p": cp_2,
                            "Net (+GCpi)": round(p_pos_2, 2),
                            "Net (-GCpi)": round(p_neg_2, 2),
                        }
                    )

            elif "leeward" in zone_label.lower():
                cp_l = float(row["C_p_1"])
                p_pos_l = (q_h * gust_effect_factor * cp_l) - (q_h * g_pos)
                p_neg_l = (q_h * gust_effect_factor * cp_l) - (q_h * g_neg)
                summary_rows.append(
                    {
                        "Surface": "Leeward roof",
                        "z (m)": "\u2014",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_l,
                        "Net (+GCpi)": round(p_pos_l, 2),
                        "Net (-GCpi)": round(p_neg_l, 2),
                    }
                )

            else:  # flat-roof stepped zones
                cp_1 = float(str(row["C_p_1"]).replace("*", "").strip())
                p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
                p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)
                summary_rows.append(
                    {
                        "Surface": zone_label,
                        "z (m)": "\u2014",
                        "q (Pa)": round(q_h, 2),
                        "G": gust_effect_factor,
                        "C_p": cp_1,
                        "Net (+GCpi)": round(p_pos_1, 2),
                        "Net (-GCpi)": round(p_neg_1, 2),
                    }
                )
                if (
                    "C_p_2" in row
                    and pd.notna(row["C_p_2"])
                    and str(row["C_p_2"]).strip() != ""
                ):
                    cp_2 = float(str(row["C_p_2"]).replace("*", "").strip())
                    if cp_2 != cp_1:
                        p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                        p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)
                        summary_rows.append(
                            {
                                "Surface": "",
                                "z (m)": "\u2014",
                                "q (Pa)": round(q_h, 2),
                                "G": gust_effect_factor,
                                "C_p": cp_2,
                                "Net (+GCpi)": round(p_pos_2, 2),
                                "Net (-GCpi)": round(p_neg_2, 2),
                            }
                        )

        return pd.DataFrame(summary_rows)

    # -------------------------------------------------------------------
    # STEP 4b: MWFRS summary - wind parallel to ridge
    # -------------------------------------------------------------------
    def generate_mwfrs_parallel_to_ridge_table(
        self,
        df_wall_cp,
        df_roof_payload,
        df_velocity_profile,
        gust_effect_factor,
        gcpi_pos,
        gcpi_neg,
    ):
        """MWFRS pressure summary, wind parallel to ridge (windward wall extends to apex)."""
        g_pos, g_neg = float(gcpi_pos), float(gcpi_neg)

        try:
            q_h = float(
                df_velocity_profile.loc[
                    df_velocity_profile["Type"].str.contains("Mean", na=False),
                    "qz (Pa)",
                ].values[0]
            )
        except (IndexError, KeyError, ValueError):
            q_h = float(df_velocity_profile["qz (Pa)"].iloc[-1])

        summary_rows = []

        # Windward wall (gable end - full height profile, up to apex)
        cp_ww = float(
            df_wall_cp.loc[df_wall_cp["Surface"] == "Windward wall", "Cp"].values[0]
        )
        for idx, row in df_velocity_profile.iterrows():
            z_val, q_z = float(row["Height (m)"]), float(row["qz (Pa)"])
            p_pos = (q_z * gust_effect_factor * cp_ww) - (q_h * g_pos)
            p_neg = (q_z * gust_effect_factor * cp_ww) - (q_h * g_neg)
            summary_rows.append(
                {
                    "Surface": "Windward wall" if idx == 0 else "",
                    "z (m)": f"{z_val:g}",
                    "q (Pa)": round(q_z, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_ww, 2),
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # Leeward & side walls (parallel-specific lookup)
        df_wall_cp = df_wall_cp.copy()
        df_wall_cp["Surface"] = (
            df_wall_cp["Surface"].replace(r"^\s*$", np.nan, regex=True).ffill()
        )

        cp_sw = float(
            df_wall_cp.loc[
                df_wall_cp["Surface"].str.contains("Side wall", case=False), "Cp"
            ].values[0]
        )
        try:
            mask = df_wall_cp["Surface"].str.contains(
                "Leeward", case=False
            ) & df_wall_cp["Wind direction"].str.contains(
                "Parallel", case=False, na=False
            )
            cp_lw = float(df_wall_cp.loc[mask, "Cp"].values[0])
        except (IndexError, KeyError, ValueError):
            cp_lw = float(
                df_wall_cp.loc[
                    df_wall_cp["Surface"].str.contains("Leeward", case=False), "Cp"
                ].iloc[-1]
            )

        for label, cp_val in [("Leeward wall", cp_lw), ("Side walls", cp_sw)]:
            p_pos = (q_h * gust_effect_factor * cp_val) - (q_h * g_pos)
            p_neg = (q_h * gust_effect_factor * cp_val) - (q_h * g_neg)
            summary_rows.append(
                {
                    "Surface": label,
                    "z (m)": "All",
                    "q (Pa)": round(q_h, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_val, 2),
                    "Net (+GCpi)": round(p_pos, 2),
                    "Net (-GCpi)": round(p_neg, 2),
                }
            )

        # Roof (always stepped-distance zones for parallel wind)
        for idx, row in df_roof_payload.iterrows():
            zone_label = str(row["Surface"]).strip()
            cp_1 = float(str(row["C_p_1"]).replace("*", "").strip())
            p_pos_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_pos)
            p_neg_1 = (q_h * gust_effect_factor * cp_1) - (q_h * g_neg)
            # zone_label already carries the "Roof (...)" prefix from the payload builder
            display_surface = zone_label
            summary_rows.append(
                {
                    "Surface": display_surface,
                    "z (m)": "\u2014",
                    "q (Pa)": round(q_h, 2),
                    "G": round(gust_effect_factor, 2),
                    "C_p": round(cp_1, 2),
                    "Net (+GCpi)": round(p_pos_1, 2),
                    "Net (-GCpi)": round(p_neg_1, 2),
                }
            )
            if (
                "C_p_2" in row
                and pd.notna(row["C_p_2"])
                and str(row["C_p_2"]).strip() != ""
            ):
                cp_2 = float(str(row["C_p_2"]).replace("*", "").strip())
                if cp_2 != cp_1:
                    p_pos_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_pos)
                    p_neg_2 = (q_h * gust_effect_factor * cp_2) - (q_h * g_neg)
                    summary_rows.append(
                        {
                            "Surface": "",
                            "z (m)": "\u2014",
                            "q (Pa)": round(q_h, 2),
                            "G": round(gust_effect_factor, 2),
                            "C_p": round(cp_2, 2),
                            "Net (+GCpi)": round(p_pos_2, 2),
                            "Net (-GCpi)": round(p_neg_2, 2),
                        }
                    )

        return pd.DataFrame(summary_rows)

    def _validate_inputs(self):
        """Reject inputs the tables cannot handle, with a message naming the input."""
        exposure = str(self.exposure_category).strip().upper()
        if exposure not in KZ_TABLE.columns:
            raise ValueError(
                f"Exposure Category must be B, C or D; received {self.exposure_category!r}."
            )
        ridge = str(self.ridge_direction_input).strip().upper()
        if ridge not in ("L", "B"):
            raise ValueError(
                f"Direction of Ridge must be 'L' or 'B'; received "
                f"{self.ridge_direction_input!r}."
            )
        if float(self.l_input) <= 0 or float(self.b_input) <= 0:
            raise ValueError("Building dimensions L and B must be greater than zero.")
        if float(self.eave_height) <= 0:
            raise ValueError("Eave height must be greater than zero.")
        if float(self.apex_height) < float(self.eave_height):
            raise ValueError("Apex height must not be lower than the eave height.")

    # -------------------------------------------------------------------
    # ORCHESTRATOR: run the full calculation pipeline
    # -------------------------------------------------------------------
    def calculate(self):
        """Runs the full ASCE 7 directional-procedure pipeline and stores all results."""
        self._validate_inputs()
        if self.raw_heights:
            self.heights_list = [
                float(h.strip()) for h in str(self.raw_heights).split(",") if h.strip()
            ]
        else:
            self.heights_list = []

        self.vel_pres = (
            0.613
            * (self.basic_wind_speed**2)
            * self.wind_dir_factor
            * self.topographic_factor
            * self.ground_elevation_factor
        )

        target = str(self.enclosure_class).strip()
        matched_row = None
        for idx_val in GCPI_TABLE.index:
            if str(idx_val).strip().lower() == target.lower():
                matched_row = GCPI_TABLE.loc[idx_val]
                break
        if matched_row is None:
            raise ValueError(
                f"Enclosure Classification '{self.enclosure_class}' was not found."
            )
        self.gcpi_pos = float(matched_row["GCpi (+)"])
        self.gcpi_neg = float(matched_row["GCpi (-)"])

        self.velocity_pressure_table = self.generate_velocity_pressure_profile(
            heights_list=self.heights_list,
            exposure_input=self.exposure_category,
            vel_pres=self.vel_pres,
        )

        self.wall_cp_table = self.generate_wall_cp_table(
            self.l_input, self.b_input, self.ridge_direction_input
        )

        self.roof_cp_display_normal, self.roof_cp_payload_normal, _, _ = (
            self.generate_roof_cp(
                b_value=self.b_input,
                l_value=self.l_input,
                ridge_direction=self.ridge_direction_input,
                wind_direction="Normal",
            )
        )
        self.roof_cp_display_parallel, self.roof_cp_payload_parallel, _, _ = (
            self.generate_roof_cp(
                b_value=self.b_input,
                l_value=self.l_input,
                ridge_direction=self.ridge_direction_input,
                wind_direction="Parallel",
            )
        )

        self.mwfrs_normal_summary = self.generate_mwfrs_normal_to_ridge_table(
            df_wall_cp=self.wall_cp_table,
            df_roof_payload=self.roof_cp_payload_normal,
            df_velocity_profile=self.velocity_pressure_table,
            gust_effect_factor=self.gust_effect_factor,
            gcpi_pos=self.gcpi_pos,
            gcpi_neg=self.gcpi_neg,
        )
        self.mwfrs_parallel_summary = self.generate_mwfrs_parallel_to_ridge_table(
            df_wall_cp=self.wall_cp_table,
            df_roof_payload=self.roof_cp_payload_parallel,
            df_velocity_profile=self.velocity_pressure_table,
            gust_effect_factor=self.gust_effect_factor,
            gcpi_pos=self.gcpi_pos,
            gcpi_neg=self.gcpi_neg,
        )
        return self

    # -------------------------------------------------------------------
    # TERMINAL SUMMARY
    # -------------------------------------------------------------------
    def summary_text(self) -> str:
        """Every intermediate and final table as terminal text."""
        lines = []
        print = lines.append  # the lines below were written as print calls

        print("\n" + "=" * 78)
        print("ASCE 7 WIND LOAD CALCULATOR - DIRECTIONAL PROCEDURE (MWFRS)")
        print("=" * 78)
        print(f"Building Classification : {self.building_class}")
        print(f"Basic Wind Speed        : {self.basic_wind_speed:.3f} m/s")
        print(f"Enclosure Classification: {self.enclosure_class}")
        print(f"Exposure Category       : {self.exposure_category}")
        print(
            f"Kd / Kzt / Ke / G       : {self.wind_dir_factor} / {self.topographic_factor} / "
            f"{self.ground_elevation_factor} / {self.gust_effect_factor}"
        )
        print(f"Velocity Pressure (base): {self.vel_pres:.3f} Pa")
        print(f"GCpi (+/-)              : {self.gcpi_pos} / {self.gcpi_neg}")
        print(
            f"L x B                   : {self.l_input} m x {self.b_input} m "
            f"(ridge direction: {self.ridge_direction_input})"
        )
        print(
            f"Eave / Mean / Apex      : {self.eave_height} / {self.mean_roof_height} / "
            f"{self.apex_height} m"
        )

        print("\n--- Velocity Pressure Profile (qz) ---")
        print(self.velocity_pressure_table.to_string(index=False))

        print("\n--- Wall Pressure Coefficient (Cp) Table ---")
        print(self.wall_cp_table.to_string(index=False))

        print("\n--- Roof Pressure Coefficient (Cp) Table - Normal Wind Direction ---")
        print(self.roof_cp_display_normal.to_string(index=False))

        print(
            "\n--- Roof Pressure Coefficient (Cp) Table - Parallel Wind Direction ---"
        )
        print(self.roof_cp_display_parallel.to_string(index=False))

        print("\n--- MWFRS Pressure Summary - Normal Wind Direction ---")
        print(self.mwfrs_normal_summary.to_string(index=False))

        print("\n--- MWFRS Pressure Summary - Parallel Wind Direction ---")
        print(self.mwfrs_parallel_summary.to_string(index=False))
        print("=" * 78 + "\n")
        return "\n".join(lines)

    def print_summary(self):
        """Prints every intermediate and final table to the terminal."""
        print(self.summary_text())

    # -------------------------------------------------------------------
    # PDF REPORT
    # -------------------------------------------------------------------
    def generate_pdf_report(
        self,
        output_filename="ASCE_7_MWFRS_Directional_Procedure_Report",
        save_path=None,
    ):
        """
        Builds the formal PyLaTeX engineering report. If save_path is None, prompts
        for one via a tkinter "Save As" dialog (falling back to a text prompt if no
        display/tkinter is available).
        """
        if save_path is None:
            save_path = self._prompt_for_save_path(output_filename)
            if not save_path:
                print("PDF export canceled.")
                return None

        if save_path.endswith(".pdf"):
            save_path = save_path[:-4]

        def _rule_table(colspec, header_row, data_rows, hline_after_header=True):
            """Builds a plain (non-floating) Tabular with manual \\toprule/\\midrule/\\bottomrule,
            matching the house style: no float wrapper (floats don't play well inside multicols).
            """
            tabular = Tabular(colspec)
            tabular.append(NoEscape(r"\toprule"))
            tabular.add_row(header_row)
            if hline_after_header:
                tabular.append(NoEscape(r"\midrule"))
            for row in data_rows:
                tabular.add_row(row)
            tabular.append(NoEscape(r"\bottomrule"))
            return tabular

        doc = Document(
            save_path,
            lmodern=False,  # avoids a hard dependency on the lmodern font package
            geometry_options={"a4paper": True, "margin": "0.5in"},
        )
        doc.packages.append(Package("booktabs"))
        doc.packages.append(Package("amsmath"))
        doc.packages.append(Package("multicol"))
        doc.preamble.append(NoEscape(r"\pagestyle{empty}"))

        doc.append(NoEscape(r"\begin{center}"))
        doc.append(
            NoEscape(
                r"{\LARGE \textbf{ASCE 7 Wind Load Calculations (Directional Procedure)}}\\[0.35cm]"
            )
        )
        doc.append(
            NoEscape(r"{\normalsize " + datetime.today().strftime("%B %d, %Y") + r"}")
        )
        doc.append(NoEscape(r"\end{center}"))
        doc.append(NoEscape(r"\vspace{0.4cm}"))

        doc.append(NoEscape(r"\begin{multicols}{2}"))

        # --- 1.0 Design Parameters --------------------------------------
        with doc.create(Section("Design Parameters")):
            doc.append(
                "Fundamental parameters and building dimensions utilized for the "
                "ASCE 7 wind load calculations:"
            )
            doc.append(NoEscape(r"\vspace{0.25cm}\newline\noindent"))
            heights_str = (
                ", ".join(str(h) for h in self.heights_list)
                if self.heights_list
                else "\u2014"
            )
            doc.append(
                _rule_table(
                    "lr",
                    ("Parameter", "Value"),
                    [
                        ("Building Classification", self.building_class),
                        ("Basic Wind Speed (m/s)", f"{self.basic_wind_speed:.2f}"),
                        ("Enclosure Classification", self.enclosure_class),
                        ("Exposure Category", self.exposure_category),
                        (
                            NoEscape("Wind Directionality Factor, $K_d$"),
                            f"{self.wind_dir_factor:.2f}",
                        ),
                        (
                            NoEscape("Topographic Factor, $K_{zt}$"),
                            f"{self.topographic_factor:.2f}",
                        ),
                        (
                            NoEscape("Ground Elevation Factor, $K_e$"),
                            f"{self.ground_elevation_factor:.2f}",
                        ),
                        ("Gust Effect Factor, G", f"{self.gust_effect_factor:.2f}"),
                        ("Velocity Pressure (Pa)", f"{self.vel_pres:.2f}"),
                        (
                            NoEscape("$GC_{pi}$"),
                            f"+{self.gcpi_pos:.2f} / {self.gcpi_neg:.2f}",
                        ),
                        ("L (m)", f"{self.l_input:.2f}"),
                        ("B (m)", f"{self.b_input:.2f}"),
                        ("Direction of Ridge", self.ridge_direction_input),
                        ("Heights to Consider (m)", heights_str),
                        ("Eave Height (m)", f"{self.eave_height:.2f}"),
                        ("Apex Height (m)", f"{self.apex_height:.2f}"),
                    ],
                )
            )

        # --- 2.0 Velocity Pressure Section ------------------------------
        with doc.create(Section("Velocity Pressure Profile")):
            doc.append(
                "The velocity pressure, evaluated at height z, is calculated as:"
            )
            doc.append(NoEscape(r"{\small \[ q_z = 0.613\, K_z K_{zt} K_d K_e V^2 \]}"))
            with doc.create(Itemize()) as itemize:
                itemize.add_item(NoEscape(r"$q_z$: Velocity pressure (Pa)"))
                itemize.add_item(NoEscape(r"$V$: Basic wind speed (m/s)"))
                itemize.add_item(NoEscape(r"$K_d$: Wind directionality factor"))
                itemize.add_item(NoEscape(r"$K_e$: Ground elevation factor"))
                itemize.add_item(NoEscape(r"$K_{zt}$: Topographic factor"))
                itemize.add_item(
                    NoEscape(r"$K_z$: Velocity pressure exposure coefficient")
                )

            with doc.create(Subsection("Velocity Pressure Profile Table")):
                rows = [
                    [row[0], row[1], f"{row[2]:.3f}", f"{row[3]:.3f}"]
                    for row in self.velocity_pressure_table.values.tolist()
                ]
                doc.append(
                    _rule_table(
                        "r l r r", ("Height (m)", "Type", "Kz", "qz (Pa)"), rows
                    )
                )

        # --- 3.0 Pressure Coefficients Section --------------------------
        def _table_section(subsection_title, df):
            with doc.create(Subsection(subsection_title)):
                align = "l " + " ".join(["r"] * (len(df.columns) - 1))
                rows = df.fillna("").values.tolist()
                doc.append(NoEscape(r"{\footnotesize"))
                doc.append(_rule_table(align, df.columns.tolist(), rows))
                doc.append(NoEscape(r"}"))

        with doc.create(Section("Pressure Coefficients (Cp)")):
            _table_section("Wall Pressure Coefficient (Cp) Table", self.wall_cp_table)
            _table_section(
                "Roof Cp Table - Normal Wind Direction", self.roof_cp_display_normal
            )
            _table_section(
                "Roof Cp Table - Parallel Wind Direction", self.roof_cp_display_parallel
            )

        doc.append(NoEscape(r"\end{multicols}"))
        doc.append(NoEscape(r"\vspace{0.3cm}"))
        doc.append(NoEscape(r"\hrule"))
        doc.append(NoEscape(r"\vspace{0.4cm}"))

        # --- 4.0 MWFRS Pressure Summary Section (kept full-width, single column) ---
        with doc.create(Section("MWFRS Pressure Summary")):
            doc.append(
                "Design wind pressures for the Main Wind-Force Resisting System (MWFRS) "
            )
            doc.append(
                "are determined in accordance with ASCE 7 using the following equation:"
            )
            doc.append(NoEscape(r"\[ p = q\, G\, C_p - q_i (GC_{pi}) \]"))
            with doc.create(Itemize()) as itemize:
                itemize.add_item(NoEscape(r"$p$: Design wind pressure (Pa)"))
                itemize.add_item(
                    NoEscape(
                        r"$q$: Velocity pressure (Pa), evaluated at height $z$ for windward walls, "
                        r"or at height $h$ for leeward/side walls and roofs"
                    )
                )
                itemize.add_item(
                    NoEscape(
                        r"$q_i$: Internal velocity pressure (Pa), evaluated at mean roof height $h$"
                    )
                )
                itemize.add_item(NoEscape(r"$G$: Gust-effect factor"))
                itemize.add_item(NoEscape(r"$C_p$: External pressure coefficient"))
                itemize.add_item(NoEscape(r"$GC_{pi}$: Internal pressure coefficient"))
            doc.append(NoEscape(r"\vspace{0.4cm}"))

            def build_summary_table(df, table_title):
                with doc.create(Subsection(table_title)):
                    align_str = "l " + " ".join(["r"] * (len(df.columns) - 1))
                    clean_headers = []
                    for col in df.columns:
                        col_str = str(col).replace("C_p", "$C_p$")
                        col_str = col_str.replace("(+GCpi)", r"(+$GC_{pi}$)")
                        col_str = col_str.replace("(-GCpi)", r"(-$GC_{pi}$)")
                        if "$" not in col_str:
                            col_str = col_str.replace("_", r"\_")
                        clean_headers.append(NoEscape(col_str))
                    rows = df.fillna("").values.tolist()
                    doc.append(NoEscape(r"{\small"))
                    doc.append(_rule_table(align_str, clean_headers, rows))
                    doc.append(NoEscape(r"}"))
                    doc.append(NoEscape(r"\vspace{0.6cm}"))

            build_summary_table(
                self.mwfrs_normal_summary,
                "MWFRS Pressure Summary Table - Normal Wind Direction",
            )
            build_summary_table(
                self.mwfrs_parallel_summary,
                "MWFRS Pressure Summary Table - Parallel Wind Direction",
            )

        # --- Compile -----------------------------------------------------
        try:
            doc.generate_pdf(save_path, clean_tex=True, compiler="pdflatex")
            print(f"PDF exported successfully: {save_path}.pdf")
            return f"{save_path}.pdf"
        except subprocess.CalledProcessError as e:
            print(f"PDF EXPORT FAILED - LaTeX syntax error: {e}")
        except FileNotFoundError as e:
            from utilities.latex_help import INSTALL_HINT

            print(f"PDF EXPORT FAILED - LaTeX compiler not found: {e}\n{INSTALL_HINT}")
        return None

    @staticmethod
    def _prompt_for_save_path(output_filename):
        """Tries a tkinter 'Save As' dialog; falls back to a plain terminal prompt."""
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            save_path = filedialog.asksaveasfilename(
                title="Save ASCE 7 MWFRS Directional Procedure Report",
                initialfile=output_filename,
                defaultextension=".pdf",
                filetypes=[("PDF Files", "*.pdf")],
            )
            root.destroy()
            return save_path
        except Exception:  # pylint: disable=broad-exception-caught
            # No display / tkinter unavailable (e.g. headless server, SSH session)
            typed = input(
                f"Enter path to save the PDF report [default: ./{output_filename}.pdf]: "
            ).strip()
            return typed or f"./{output_filename}.pdf"


# =============================================================================
# 3. TERMINAL WORKFLOW  (python main.py wind)
# =============================================================================
INPUTS = [
    Field("building_class", "Building classification", "Risk Category IV", kind="text"),
    Field("basic_wind_speed", "Basic wind speed V (m/s)", 61.111),
    Field("enclosure_class", "Enclosure", "Enclosed Buildings", kind="choice",
          choices=tuple(GCPI_TABLE.index)),
    Field("exposure_category", "Exposure category", "D", kind="choice", choices=("B", "C", "D")),
    Field("wind_dir_factor", "Wind directionality factor Kd", 0.85),
    Field("topographic_factor", "Topographic factor Kzt", 1.0),
    Field("ground_elevation_factor", "Ground elevation factor Ke", 1.0),
    Field("gust_effect_factor", "Gust effect factor G", 0.85),
    Field("l_input", "Building length L (m)", 180.0),
    Field("b_input", "Building width B (m)", 180.0),
    Field("ridge_direction_input", "Ridge runs along", "B", kind="choice", choices=("L", "B")),
    Field("raw_heights", "Heights for the qz profile (m, comma separated)", "10", kind="text",
          optional=True),
    Field("eave_height", "Eave height (m)", 15.0),
    Field("apex_height", "Apex height (m)", 20.0),
]


def calculate(values: dict) -> WindLoadCalculatorDirectionalASCE7:
    """The calculator from a dict of the inputs (keys of ``INPUTS``), with every table worked out."""
    inputs = {field.key: values.get(field.key) for field in INPUTS}
    inputs["raw_heights"] = inputs["raw_heights"] or ""
    return WindLoadCalculatorDirectionalASCE7(**inputs).calculate()


def summary_text(calculator: WindLoadCalculatorDirectionalASCE7) -> str:
    """Every intermediate and final table as terminal text."""
    return calculator.summary_text()


def export_pdf(calculator: WindLoadCalculatorDirectionalASCE7, path: str) -> str | None:
    """Write the PDF calculation report; returns its path, or None when LaTeX fails."""
    return calculator.generate_pdf_report(save_path=path)


def run():
    """Terminal workflow: input dialog, then printout, PDF report or both."""
    return run_design(sys.modules[__name__], "Wind Loads (ASCE 7, Directional)",
                      "wind_loads", "ASCE_7_MWFRS_Directional_Procedure_Report")


if __name__ == "__main__":
    run()
