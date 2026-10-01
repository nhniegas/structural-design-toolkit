"""
Rectangular Filled Composite Column Design Module

Axial, flexural, shear, and combined interaction strength of
rectangular/square concrete-filled steel composite members (LRFD),
per AISC Design Guide 6 (2nd Ed.) Section 2.5 and AISC 360 Chapter I.

Known limitation: noncompact/slender flexural strength (Spec. Eq. I3-5b)
is not implemented - it needs a first-yield moment My with no closed-form
equation given in DG6 for rectangular sections, so it's intentionally
left as a clear error rather than a guessed formula.
"""

import math
import sys
import os
from datetime import datetime
import xlwings as xw
from pylatex import (
    Document,
    Section,
    Tabular,
    Package,
    NoEscape,
    Itemize,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utilities._gui_helpers import select_save_file


class RectangularFilledComposite:
    """
    Rectangular/square filled composite member per AISC Design Guide 6
    Section 2.5 and AISC 360 Chapter I.
    """

    def __init__(
        self,
        b_mm,
        h_mm,
        t_mm,
        fc_mpa,
        fy_mpa,
        Lx_m=None,
        Ly_m=None,
        Pu_kN=0,
        Mbx_kNmm=0,
        Mhy_kNmm=0,
        Vbx_kN=0,
        Vhy_kN=0,
        Asr_mm2=0.0,  # internal reinforcement area
        Fysr_mpa=414.0,  # rebar yield (414 MPa ~= 60 ksi)
        ri_mm=0.0,  # corner radius; 0 = sharp/welded box (DG6 Ex 2.5)
        shear_span_to_depth=None,  # (Mr/Vr)/d, enables Kc interpolation
        *,
        Lb_m=None,
        Lh_m=None,
        Mb_kNm=None,
        Mh_kNm=None,
        label="",
    ):
        # Moments are accepted in kN-mm (Mbx_kNmm / Mhy_kNmm) or, through the
        # keyword-only aliases, in kN-m (Mb_kNm / Mh_kNm). Lb_m / Lh_m are aliases
        # of Lx_m / Ly_m.

        # 1. Metric to US Customary Conversions
        self.label = label
        Lx_m = Lx_m if Lx_m is not None else (Lb_m or 0.0)
        Ly_m = Ly_m if Ly_m is not None else (Lh_m or 0.0)
        Mbx_kNmm = Mbx_kNmm if Mbx_kNmm else (Mb_kNm or 0.0) * 1000.0
        Mhy_kNmm = Mhy_kNmm if Mhy_kNmm else (Mh_kNm or 0.0) * 1000.0

        self.b = b_mm / 25.4  # inches
        self.h = h_mm / 25.4  # inches
        self.t = t_mm / 25.4  # inches
        self.fc = fc_mpa * 0.145038  # ksi
        self.Fy = fy_mpa * 0.145038  # ksi
        self.Lx = Lx_m * 39.3701  # inches
        self.Ly = Ly_m * 39.3701  # inches
        self.ri = ri_mm / 25.4  # inches

        self.Asr = Asr_mm2 / 645.16  # in^2
        self.Fysr = Fysr_mpa * 0.145038  # ksi

        # Force/moment conversions
        self.Pu = Pu_kN * 0.224809  # kips
        self.Mux = Mbx_kNmm * 0.0088507  # kip-in  (kN-mm -> kip-in)
        self.Muy = Mhy_kNmm * 0.0088507  # kip-in
        self.Vux = Vbx_kN * 0.224809  # kips
        self.Vuy = Vhy_kN * 0.224809  # kips

        self.shear_span_to_depth = shear_span_to_depth

        # Fixed Materials
        self.Es = 29000.0  # ksi
        self.wc = 145.0  # pcf (Normal weight concrete)
        self.Ec = (self.wc**1.5) * math.sqrt(self.fc)  # ksi

        # Section Properties
        self.Ag = self.b * self.h
        self.bi = self.b - 2 * self.t
        self.hi = self.h - 2 * self.t

        # Ac and Zc include the corner-radius correction from DG6 Fig. 2-13
        # Point A / Point D. With ri = 0 these reduce exactly to the
        # original sharp-corner formulas.
        self.Ac = self.bi * self.hi - 0.858 * self.ri**2
        self.As = self.Ag - self.Ac

        self.Icx = (self.bi * self.hi**3) / 12.0
        self.Icy = (self.hi * self.bi**3) / 12.0
        self.Isx = (self.b * self.h**3) / 12.0 - self.Icx
        self.Isy = (self.h * self.b**3) / 12.0 - self.Icy

        # Resistance Factors (LRFD)
        self.phi_c = 0.75
        self.phi_t = 0.90
        self.phi_b = 0.90
        self.phi_v = 0.90

        self.Pn = None
        self.Pno = None

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def check_compactness_axial(self):
        """
        Classifies the section for local buckling under AXIAL LOAD per
        DG6 Table 2-5, row "Walls of rectangular HSS and box sections in
        members subjected to axial compression": lambda_p = 2.26,
        lambda_r = 3.00, lambda_max = 5.00 (all x sqrt(E/Fy)), applied
        identically to both b/t and h/t (DG6 p.32, AISC Spec Table I1.1a).
        """
        lam_b = self.bi / self.t
        lam_h = self.hi / self.t
        lam = max(lam_b, lam_h)

        lam_p = 2.26 * math.sqrt(self.Es / self.Fy)
        lam_r = 3.00 * math.sqrt(self.Es / self.Fy)
        lam_max = 5.00 * math.sqrt(self.Es / self.Fy)

        if lam <= lam_p:
            return "Compact"
        elif lam <= lam_r:
            return "Noncompact"
        elif lam <= lam_max:
            return "Slender"
        else:
            return "Not Permitted (Exceeds Maximum Limitation)"

    # Backward-compatible alias for the original method name.
    check_compactness = check_compactness_axial

    def check_compactness_flexure(self, axis="b"):
        """
        Classifies the section for local buckling under FLEXURE per
        DG6 Table 2-5, which uses DIFFERENT limits for the flange
        (parallel to the bending axis, width b) than for the web
        (parallel to the depth, height h):
            Flange: lambda_p=2.26, lambda_r=3.00, lambda_max=5.00 (xsqrt(E/Fy))
            Web:    lambda_p=3.00, lambda_r=5.70, lambda_max=5.70 (xsqrt(E/Fy))
        The governing (larger normalized) ratio determines classification.
        axis='b' -> bending such that b is the flange width, h is the depth.
        axis='h' -> bending such that h is the flange width, b is the depth.
        """
        if axis.lower() == "b":
            lam_flange = self.bi / self.t
            lam_web = self.hi / self.t
        elif axis.lower() == "h":
            lam_flange = self.hi / self.t
            lam_web = self.bi / self.t
        else:
            raise ValueError("Axis parameter must be 'b' or 'h'")

        sqrtEFy = math.sqrt(self.Es / self.Fy)
        flange_p, flange_r, flange_max = 2.26 * sqrtEFy, 3.00 * sqrtEFy, 5.00 * sqrtEFy
        web_p, web_r, web_max = 3.00 * sqrtEFy, 5.70 * sqrtEFy, 5.70 * sqrtEFy

        if lam_flange > flange_max or lam_web > web_max:
            return "Not Permitted (Exceeds Maximum Limitation)"

        if lam_flange <= flange_p and lam_web <= web_p:
            return "Compact"
        elif lam_flange <= flange_r and lam_web <= web_r:
            return "Noncompact"
        else:
            return "Slender"

    def check_min_steel_ratio(self):
        """
        Spec. Eq. I2.2a(a): As/Ag must be >= 1%. Returns (ok: bool, ratio: float).
        """
        ratio = self.As / self.Ag
        return ratio >= 0.01, ratio

    # ------------------------------------------------------------------
    # Axial
    # ------------------------------------------------------------------
    def axial_compressive_strength(self):
        """
        LRFD axial compressive strength, phi_Pn (kips), using AXIAL
        classification (correct per DG6 - Table 2-5's single row for
        walls in axial compression applies regardless of direction).

        Not applied: DG6 p.32 allows the design strength to be taken as not
        less than that of the bare steel section. That floor is not calculated
        here, so the result is the composite strength only (conservative).
        """
        compactness = self.check_compactness_axial()
        if "Not Permitted" in compactness:
            return "N/A - Exceeds Maximum Slenderness"

        lam_b = self.bi / self.t
        lam_h = self.hi / self.t
        lam = max(lam_b, lam_h)

        lam_p = 2.26 * math.sqrt(self.Es / self.Fy)
        lam_r = 3.00 * math.sqrt(self.Es / self.Fy)

        Asr = self.Asr

        Pp = (self.Fy * self.As) + 0.85 * self.fc * (
            self.Ac + Asr * (self.Es / self.Ec)
        )
        Py = (self.Fy * self.As) + 0.7 * self.fc * (self.Ac + Asr * (self.Es / self.Ec))

        if compactness == "Compact":
            self.Pno = Pp
        elif compactness == "Noncompact":
            self.Pno = Pp - ((Pp - Py) / (lam_r - lam_p) ** 2) * (lam - lam_p) ** 2
        elif compactness == "Slender":
            Fn = (9 * self.Es) / (lam**2)
            self.Pno = (Fn * self.As) + 0.7 * self.fc * (
                self.Ac + Asr * (self.Es / self.Ec)
            )

        C3 = min(0.45 + 3 * ((self.As + Asr) / self.Ag), 0.9)
        EI_eff_x = (self.Es * self.Isx) + (C3 * self.Ec * self.Icx)
        EI_eff_y = (self.Es * self.Isy) + (C3 * self.Ec * self.Icy)

        Pe_x = (math.pi**2 * EI_eff_x) / (self.Lx**2) if self.Lx > 0 else float("inf")
        Pe_y = (math.pi**2 * EI_eff_y) / (self.Ly**2) if self.Ly > 0 else float("inf")
        Pe = min(Pe_x, Pe_y)

        if Pe == float("inf"):
            self.Pn = self.Pno
        elif (self.Pno / Pe) <= 2.25:
            self.Pn = self.Pno * (0.658 ** (self.Pno / Pe))
        else:
            self.Pn = 0.877 * Pe

        return self.phi_c * self.Pn

    def axial_tensile_strength(self):
        """LRFD axial tensile strength, phi_Tn (kips). Spec. Eq. I2-14."""
        Tn = (self.As * self.Fy) + (self.Asr * self.Fysr)
        return self.phi_t * Tn

    # ------------------------------------------------------------------
    # Flexure
    # ------------------------------------------------------------------
    def flexural_strength(self, axis="b"):
        """
        LRFD flexural strength (kip-in), compact sections only
        (gated on check_compactness_flexure(), not the axial check).
        Plastic Stress Distribution per DG6 Fig. 2-13 (AISC Manual Table 6-3).
        Corner radius ri included (0 by default -> reduces to sharp-corner case).
        """
        compactness = self.check_compactness_flexure(axis=axis)
        if compactness != "Compact":
            return (
                f"N/A - Section is {compactness} for flexure about the "
                f"'{axis}' axis. Noncompact/slender flexural strength "
                "(Spec. Eq. I3-5b) requires a first-yield moment My that "
                "is not implemented here - see module docstring."
            )

        if axis.lower() == "b":
            width, depth = self.b, self.h
            width_i, depth_i = self.bi, self.hi
        elif axis.lower() == "h":
            width, depth = self.h, self.b
            width_i, depth_i = self.hi, self.bi
        else:
            raise ValueError("Axis parameter must be 'b' or 'h'")

        ri = self.ri

        Zs = (width * depth**2) / 4.0 - (width_i * depth_i**2) / 4.0
        Zc = (width_i * depth_i**2) / 4.0 - 0.429 * ri**2 * depth_i + 0.192 * ri**3

        MD = (self.Fy * Zs) + (0.85 * self.fc * Zc / 2.0)

        hn = (0.85 * self.fc * self.Ac) / (
            2 * (0.85 * self.fc * width_i + 4 * self.Fy * self.t)
        )
        hn = min(hn, depth_i / 2.0)

        Zsn = 2 * self.t * (hn**2)
        Zcn = width_i * (hn**2)

        MB = MD - (self.Fy * Zsn) - 0.85 * self.fc * (Zcn / 2.0)

        return self.phi_b * MB

    # ------------------------------------------------------------------
    # Shear
    # ------------------------------------------------------------------
    def _compute_Kc(self):
        """
        Kc per DG6 p.34 (Spec. Eq. I4-1 commentary).
        Kc = 1 if section is not flexure-compact, or shear-span-to-depth
        ratio (Mr/Vr)/d >= 0.7.
        Kc = 10 if flexure-compact and ratio < 0.5.
        Linear interpolation for 0.5 <= ratio < 0.7.
        Requires self.shear_span_to_depth to be supplied; defaults to
        Kc = 1 (conservative) if not provided.
        """
        if self.shear_span_to_depth is None:
            return 1.0

        # Uses 'b' axis flexural classification as the compactness check;
        # pass axis explicitly if you need the 'h' axis instead.
        compact = self.check_compactness_flexure(axis="b") == "Compact"
        ratio = self.shear_span_to_depth

        if not compact or ratio >= 0.7:
            return 1.0
        elif ratio < 0.5:
            return 10.0
        else:
            # linear interpolation between (0.5 -> 10) and (0.7 -> 1)
            return 10.0 + (ratio - 0.5) * (1.0 - 10.0) / (0.7 - 0.5)

    def shear_strength(self, axis="b"):
        """LRFD shear strength (kips). Spec. Eq. I4-1."""
        if axis.lower() == "b":
            depth = self.h
        elif axis.lower() == "h":
            depth = self.b
        else:
            raise ValueError("Axis parameter must be 'b' or 'h'")

        Av = 2 * depth * self.t
        Kc = self._compute_Kc()

        Vn = 0.6 * self.Fy * Av + 0.06 * Kc * self.Ac * math.sqrt(self.fc)

        return self.phi_v * Vn

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def interaction_check(self):
        """Combined axial + flexural interaction ratio, DG6 Section 2.5.6."""
        phi_Pn = self.axial_compressive_strength()
        phi_Mnx = self.flexural_strength(axis="b")
        phi_Mny = self.flexural_strength(axis="h")

        if isinstance(phi_Pn, str):
            return {"Error": phi_Pn}

        chi = self.Pn / self.Pno
        Pc_cross = 0.85 * self.fc * self.Ac  # PC, Spec Eq. C-I5-1 commentary
        Pcc = self.phi_c * chi * Pc_cross

        if isinstance(phi_Mnx, str) or isinstance(phi_Mny, str):
            ratio_no_alpha = "N/A - Noncompact/Slender Flexure"
            ratio_with_alpha = "N/A - Noncompact/Slender Flexure"
        else:
            ratio_x = abs(self.Mux / phi_Mnx) if phi_Mnx > 0 else 0
            ratio_y = abs(self.Muy / phi_Mny) if phi_Mny > 0 else 0

            moment_term_standard = ratio_x + ratio_y
            if self.Pu < Pcc:
                ratio_no_alpha = moment_term_standard
            else:
                ratio_no_alpha = (
                    (self.Pu - Pcc) / (phi_Pn - Pcc)
                ) + moment_term_standard

            alpha = 1.5  # DG6 p.11 - recommended for rectangular filled members
            moment_term_alpha = (ratio_x**alpha) + (ratio_y**alpha)
            if self.Pu < Pcc:
                ratio_with_alpha = moment_term_alpha
            else:
                ratio_with_alpha = ((self.Pu - Pcc) / (phi_Pn - Pcc)) + (
                    moment_term_alpha ** (1.0 / alpha)
                )

        min_steel_ok, steel_ratio = self.check_min_steel_ratio()

        return {
            "As/Ag Check (>=1%)": f"{steel_ratio*100:.2f}% {'OK' if min_steel_ok else 'NOT OK'}",
            "Axial Compactness": self.check_compactness_axial(),
            "Flexural Compactness (b-axis)": self.check_compactness_flexure(axis="b"),
            "Flexural Compactness (h-axis)": self.check_compactness_flexure(axis="h"),
            "Pno (kips)": round(self.Pno, 1),
            "Pn (kips)": round(self.Pn, 1),
            "phi_Pn (kips)": round(phi_Pn, 1),
            "phi_Mnx (kip-in)": (
                round(phi_Mnx, 1) if not isinstance(phi_Mnx, str) else phi_Mnx
            ),
            "phi_Mny (kip-in)": (
                round(phi_Mny, 1) if not isinstance(phi_Mny, str) else phi_Mny
            ),
            "Pcc (kips)": round(Pcc, 1),
            "Interaction Ratio (Standard)": (
                round(ratio_no_alpha, 3)
                if not isinstance(ratio_no_alpha, str)
                else ratio_no_alpha
            ),
            "Interaction Ratio (Alpha=1.5)": (
                round(ratio_with_alpha, 3)
                if not isinstance(ratio_with_alpha, str)
                else ratio_with_alpha
            ),
        }

    def check_seismic_compactness(self, Ry=1.3):
        """
        Seismic classification for walls of rectangular filled composite members
        per AISC 341-16 Table D1.1.
        Ry defaults to 1.3 for ASTM A36 Grade 36 plates, strips, and sheets.
        """
        lam_b = self.bi / self.t
        lam_h = self.hi / self.t
        lam = max(lam_b, lam_h)

        # Limits per AISC 341-16 Table D1.1
        lam_hd = 1.48 * math.sqrt(self.Es / (Ry * self.Fy))
        lam_md = 2.37 * math.sqrt(self.Es / (Ry * self.Fy))

        if lam <= lam_hd:
            return "Highly Ductile"
        elif lam <= lam_md:
            return "Moderately Ductile"
        else:
            return "Not Seismically Compact"

    def axial_classification(self):
        """Return axial compactness using the standalone API naming."""
        label = self.check_compactness_axial()
        scale = math.sqrt(self.Es / self.Fy)
        return {
            "lam_b": self.bi / self.t,
            "lam_h": self.hi / self.t,
            "lam": max(self.bi / self.t, self.hi / self.t),
            "lam_p": 2.26 * scale,
            "lam_r": 3.00 * scale,
            "lam_max": 5.00 * scale,
            "cls": label,
        }

    def flexure_classification(self, axis="b"):
        """Return flexural compactness using the standalone API naming."""
        label = self.check_compactness_flexure(axis)
        scale = math.sqrt(self.Es / self.Fy)
        flange = self.bi / self.t if axis == "b" else self.hi / self.t
        web = self.hi / self.t if axis == "b" else self.bi / self.t
        return {
            "lf": flange,
            "lw": web,
            "cls": label,
            "lam_fp": 2.26 * scale,
            "lam_fr": 3.00 * scale,
            "lam_wp": 3.00 * scale,
            "lam_wr": 5.70 * scale,
        }

    def seismic_classification(self, Ry=1.3):
        """Return seismic compactness using the standalone API naming."""
        return {"cls": self.check_seismic_compactness(Ry), "Ry": Ry}

    def axial_compression(self):
        """Return axial compression results using the standalone API naming."""
        result = self.axial_compressive_strength()
        return {"phiPn": result, "Pn": self.Pn, "Pno": self.Pno}

    def axial_tension(self):
        """Return axial tension strength using the standalone API naming."""
        return self.axial_tensile_strength()

    def flexure(self, axis="b"):
        """Return flexural strength using the standalone API naming."""
        return {"phiMn": self.flexural_strength(axis), "axis": axis}

    def shear(self, axis="b"):
        """Return shear strength and demand using the standalone API naming."""
        capacity = self.shear_strength(axis)
        demand = self.Vux if axis == "b" else self.Vuy
        return {
            "phiVn": capacity,
            "Vu": demand,
            "ratio": demand / capacity if capacity else 0.0,
            "axis": axis,
        }

    def interaction(self, alpha=1.5):
        """Return interaction results using the standalone API naming."""
        result = self.interaction_check()
        result["alpha"] = alpha
        return result

    def report(self, heading_level=1):
        """Return a concise Markdown report for standalone callers."""
        heading = "#" * heading_level
        interaction = self.interaction(alpha=1.5)
        axial = self.axial_compression()
        return "\n".join(
            [
                f"{heading} Rectangular Filled Composite Column",
                f"**Label:** {self.label or 'Unlabeled'}",
                "",
                "| Check | Result |",
                "|---|---:|",
                f"| Axial design strength, phiPn (kips) | {axial['phiPn']} |",
                f"| Axial interaction ratio | {interaction.get('Interaction Ratio (Standard)', 'N/A')} |",
                f"| Alpha interaction ratio | {interaction.get('Interaction Ratio (Alpha=1.5)', 'N/A')} |",
                f"| Seismic compactness | {self.check_seismic_compactness()} |",
            ]
        )

    def show(self, heading_level=1):
        """Print the standalone Markdown report and return it."""
        report = self.report(heading_level)
        print(report)
        return report


# ==========================================================================
# EXCEL ENTRY POINTS (workbook buttons "Calculate Capacity" and "Export Calcs")
# ==========================================================================
#
# Cell map of the active sheet:
#   C3  Pu   (kN)        C9  b   (mm)
#   C4  Mb   (kN-m)      C10 h   (mm)
#   C5  Mh   (kN-m)      C11 t   (mm)
#   C6  Vb   (kN)        C12 f'c (MPa)
#   C7  Vh   (kN)        C13 fy  (MPa)
#                        C14 Lb  (m)  <- unbraced length for b-axis bending
#                        C15 Lh  (m)  <- unbraced length for h-axis bending
#
# Lb/Lh map onto the class's Lx_m/Ly_m parameters: Lb pairs with the b-axis
# bending stiffness (Icx = bi*hi^3/12, behind phi_Mnx/Mb) and Lh with the
# h-axis bending stiffness (Icy = hi*bi^3/12, behind phi_Mny/Mh).
#
#   B17 "SOLUTION" header (already on the sheet)
#   B19 results are written from here down, cleared and rewritten on each run
#
# The workbook macros call these functions directly through xlwings:
#   RunPython "from design import composite_column_designer_aiscDG06 as m; m.calculate_capacity()"
#   RunPython "from design import composite_column_designer_aiscDG06 as m; m.export_calcs()"
# ==========================================================================

INPUT_CELLS = {
    "Pu": "C3",
    "Mb": "C4",
    "Mh": "C5",
    "Vb": "C6",
    "Vh": "C7",
    "b": "C9",
    "h": "C10",
    "t": "C11",
    "fc": "C12",
    "fy": "C13",
    "Lb": "C14",
    "Lh": "C15",
}

SOLUTION_START_ROW = 19
SOLUTION_LABEL_COL = "B"
SOLUTION_VALUE_COL = "C"
SOLUTION_CLEAR_ROWS = 30  # rows below SOLUTION_START_ROW to clear before writing


def calculate_capacity():
    """
    Reads demand forces and section properties from the active sheet,
    runs the DG6 capacity check, and writes a results block starting at
    B19 under the "SOLUTION" header. Bound to the "Calculate Capacity"
    button via the VBA RunPython wrapper.
    """
    book = xw.Book.caller()
    sht = book.sheets.active

    col, missing = _column_from_sheet(sht)
    if missing:
        sht.range(f"{SOLUTION_LABEL_COL}{SOLUTION_START_ROW}").value = (
            f"ERROR: missing input(s): {', '.join(missing)}"
        )
        return
    shear_demand = {"b": col.Vux / 0.224809, "h": col.Vuy / 0.224809}  # kN

    results = col.interaction_check()
    phi_Vbx = col.shear_strength(axis="b")
    phi_Vhy = col.shear_strength(axis="h")

    # --- Clear previous solution block dynamically -------------------------
    start_cell = sht.range(f"{SOLUTION_LABEL_COL}{SOLUTION_START_ROW}")

    # Clear the rows below the header so no old data or colors are left behind
    clear_range = sht.range(
        f"{SOLUTION_LABEL_COL}{SOLUTION_START_ROW}:"
        f"{SOLUTION_VALUE_COL}{SOLUTION_START_ROW + SOLUTION_CLEAR_ROWS}"
    )
    clear_range.clear_contents()
    clear_range.color = None
    clear_range.font.bold = False

    # --- Build Results Array -----------------------------------------------
    rows = []

    if "Error" in results:
        rows.append(("ERROR", results["Error"]))
    else:
        rows.append(("As/Ag Check (>=1%)", results["As/Ag Check (>=1%)"]))
        rows.append(("Axial Compactness", results["Axial Compactness"]))
        rows.append(
            ("Flexural Compactness (b-axis)", results["Flexural Compactness (b-axis)"])
        )
        rows.append(
            ("Flexural Compactness (h-axis)", results["Flexural Compactness (h-axis)"])
        )
        rows.append(
            ("Seismic Compactness (Ry=1.3)", col.check_seismic_compactness(Ry=1.3))
        )
        rows.append(("", ""))
        rows.append(("φPn (kN)", _kips_to_kN(results["phi_Pn (kips)"])))
        rows.append(("φMnx (kN-m)", _kipin_to_kNm(results["phi_Mnx (kip-in)"])))
        rows.append(("φMny (kN-m)", _kipin_to_kNm(results["phi_Mny (kip-in)"])))
        rows.append(("φVbx (kN)", _kips_to_kN(phi_Vbx)))
        rows.append(("φVhy (kN)", _kips_to_kN(phi_Vhy)))
        rows.append(("", ""))

        phi_Vbx_kN = _kips_to_kN(phi_Vbx)
        if phi_Vbx_kN > 0:
            shear_ratio_b = shear_demand["b"] / phi_Vbx_kN
            rows.append(("Shear Ratio (b-axis)", round(shear_ratio_b, 3)))
            rows.append(
                (
                    "STATUS (Shear-b)",
                    "OK" if shear_ratio_b <= 1.0 else "NOT OK - OVERSTRESSED",
                )
            )
        else:
            rows.append(("Shear Ratio (b-axis)", "N/A"))

        phi_Vhy_kN = _kips_to_kN(phi_Vhy)
        if phi_Vhy_kN > 0:
            shear_ratio_h = shear_demand["h"] / phi_Vhy_kN
            rows.append(("Shear Ratio (h-axis)", round(shear_ratio_h, 3)))
            rows.append(
                (
                    "STATUS (Shear-h)",
                    "OK" if shear_ratio_h <= 1.0 else "NOT OK - OVERSTRESSED",
                )
            )

        rows.append(("", ""))

        std_ratio = results["Interaction Ratio (Standard)"]
        rows.append(("Interaction Ratio (Standard)", std_ratio))
        if isinstance(std_ratio, (int, float)):
            rows.append(
                (
                    "STATUS (Standard)",
                    "OK" if std_ratio <= 1.0 else "NOT OK - OVERSTRESSED",
                )
            )

        alpha_ratio = results["Interaction Ratio (Alpha=1.5)"]
        rows.append(("Interaction Ratio (Alpha=1.5)", alpha_ratio))
        if isinstance(alpha_ratio, (int, float)):
            rows.append(
                (
                    "STATUS (Alpha=1.5)",
                    "OK" if alpha_ratio <= 1.0 else "NOT OK - OVERSTRESSED",
                )
            )

    # --- Batch Write & Format ----------------------------------------------
    start_cell.value = rows

    # Format the labels column to be bold
    label_range = sht.range(
        (SOLUTION_START_ROW, start_cell.column),
        (SOLUTION_START_ROW + len(rows) - 1, start_cell.column),
    )
    label_range.font.bold = True

    # Format the STATUS rows with colors based on utilization
    for i, (label, val) in enumerate(rows):
        if "STATUS" in str(label):
            row_idx = SOLUTION_START_ROW + i
            status_cells = sht.range(
                f"{SOLUTION_LABEL_COL}{row_idx}:{SOLUTION_VALUE_COL}{row_idx}"
            )
            status_cells.font.bold = True

            if "NOT OK" in str(val):
                status_cells.color = (255, 199, 206)  # Light red background
                status_cells.font.color = (156, 0, 6)  # Dark red text
            elif "OK" in str(val):
                status_cells.color = (198, 239, 206)  # Light green background
                status_cells.font.color = (0, 97, 0)  # Dark green text

    # Autofit the entire columns so input labels and solution labels both fit
    sht.range(f"{SOLUTION_LABEL_COL}:{SOLUTION_VALUE_COL}").columns.autofit()


def _column_from_sheet(sht):
    """Build the member from the input cells; returns ``(column, missing_inputs)``."""
    vals = {k: sht.range(addr).value for k, addr in INPUT_CELLS.items()}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return None, missing
    column = RectangularFilledComposite(
        b_mm=vals["b"],
        h_mm=vals["h"],
        t_mm=vals["t"],
        fc_mpa=vals["fc"],
        fy_mpa=vals["fy"],
        Lx_m=vals["Lb"],
        Ly_m=vals["Lh"],
        Pu_kN=vals["Pu"],
        Mbx_kNmm=vals["Mb"] * 1000,
        Mhy_kNmm=vals["Mh"] * 1000,
        Vbx_kN=vals["Vb"],
        Vhy_kN=vals["Vh"],
    )
    return column, []


def export_calcs():
    """Excel button: export the A4 PDF calculation report for the active sheet."""
    book = xw.Book.caller()
    sht = book.sheets.active
    status = sht.range(f"{SOLUTION_LABEL_COL}{SOLUTION_START_ROW}")

    col, missing = _column_from_sheet(sht)
    if missing:
        status.value = f"ERROR: missing input(s) for export: {', '.join(missing)}"
        return

    filepath_with_ext = select_save_file(default_name="Composite_Capacity_Report")
    if not filepath_with_ext:
        return
    if not filepath_with_ext.lower().endswith(".pdf"):
        filepath_with_ext += ".pdf"

    saved_path = export_standalone_pdf(col, filepath_with_ext)
    status.value = (
        f"Success: Saved to {saved_path}"
        if saved_path
        else "PDF Error: the report could not be compiled. Check the LaTeX installation."
    )


def _kips_to_kN(kips):
    if not isinstance(kips, (int, float)):
        return kips
    return round(kips / 0.224809, 1)


def _kipin_to_kNm(kipin):
    if not isinstance(kipin, (int, float)):
        return kipin
    return round(kipin / 8.8507, 3)


def export_standalone_pdf(col: RectangularFilledComposite, filepath_with_ext: str):
    """Generates the A4 PDF report from a class instance, independent of Excel."""
    if filepath_with_ext.lower().endswith(".pdf"):
        filepath = filepath_with_ext[:-4]
    else:
        filepath = filepath_with_ext

    # Reverse-calculate metric values for the report table
    vals = {
        "b": col.b * 25.4,
        "h": col.h * 25.4,
        "t": col.t * 25.4,
        "fc": col.fc / 0.145038,
        "fy": col.Fy / 0.145038,
        "Lb": col.Lx / 39.3701,
        "Lh": col.Ly / 39.3701,
        "Pu": col.Pu / 0.224809,
        "Mb": col.Mux / 0.0088507 / 1000,
        "Mh": col.Muy / 0.0088507 / 1000,
        "Vb": col.Vux / 0.224809,
        "Vh": col.Vuy / 0.224809,
    }

    results = col.interaction_check()
    phi_Vbx = col.shear_strength(axis="b")
    phi_Vhy = col.shear_strength(axis="h")

    # Quantities for Shear Checks
    phi_Vbx_kN = _kips_to_kN(phi_Vbx)
    phi_Vhy_kN = _kips_to_kN(phi_Vhy)
    shear_ratio_b = vals["Vb"] / phi_Vbx_kN if phi_Vbx_kN > 0 else 0
    shear_ratio_h = vals["Vh"] / phi_Vhy_kN if phi_Vhy_kN > 0 else 0

    lam_b, lam_h = col.bi / col.t, col.hi / col.t
    lam = max(lam_b, lam_h)
    lam_p_ax = 2.26 * math.sqrt(col.Es / col.Fy)
    lam_hd = 1.48 * math.sqrt(col.Es / (1.3 * col.Fy))
    lam_relation = r"\le" if lam <= lam_p_ax else ">"
    axial_class = col.check_compactness_axial()

    Pu_kN = vals["Pu"]
    Pcc_kN = _kips_to_kN(results.get("Pcc (kips)", 0))

    # --- Build PDF Document ---
    doc = Document(geometry_options={"a4paper": True, "margin": "0.5in"})
    doc.packages.append(Package("booktabs"))
    doc.packages.append(Package("amsmath"))
    doc.packages.append(Package("multicol"))
    doc.preamble.append(NoEscape(r"\pagestyle{empty}"))

    doc.append(NoEscape(r"\begin{center}"))
    doc.append(
        NoEscape(
            r"{\LARGE \textbf{Rectangular Filled Composite Member Capacity (AISC DG6)}}\\[0.35cm]"
        )
    )
    doc.append(
        NoEscape(r"{\normalsize " + datetime.today().strftime("%B %d, %Y") + r"}")
    )
    doc.append(NoEscape(r"\end{center}"))
    doc.append(NoEscape(r"\vspace{0.4cm}"))

    doc.append(NoEscape(r"\begin{multicols}{2}"))

    # Section 1: Design Parameters
    with doc.create(Section("Design Parameters")):
        doc.append("Fundamental parameters utilized for capacity calculations:")
        doc.append(NoEscape(r"\vspace{0.25cm}\newline\noindent"))
        with doc.create(Tabular("lr")) as table:
            table.append(NoEscape(r"\toprule"))
            table.add_row(("Parameter", "Value"))
            table.append(NoEscape(r"\midrule"))
            table.add_row((NoEscape(r"Width, $b$ (mm)"), f"{vals['b']:.1f}"))
            table.add_row((NoEscape(r"Height, $h$ (mm)"), f"{vals['h']:.1f}"))
            table.add_row((NoEscape(r"Thickness, $t$ (mm)"), f"{vals['t']:.1f}"))
            table.add_row(
                (NoEscape(r"Comp. Strength, $f^\prime_c$ (MPa)"), f"{vals['fc']:.1f}")
            )
            table.add_row(
                (NoEscape(r"Yield Strength, $f_y$ (MPa)"), f"{vals['fy']:.1f}")
            )
            table.add_row(
                (NoEscape(r"Unbraced Length, $L_b$ (m)"), f"{vals['Lb']:.2f}")
            )
            table.add_row(
                (NoEscape(r"Unbraced Length, $L_h$ (m)"), f"{vals['Lh']:.2f}")
            )
            table.append(NoEscape(r"\midrule"))
            table.add_row((NoEscape(r"Axial Demand, $P_u$ (kN)"), f"{vals['Pu']:.1f}"))
            table.add_row(
                (NoEscape(r"Moment Demand, $M_{bx}$ (kN-m)"), f"{vals['Mb']:.1f}")
            )
            table.add_row(
                (NoEscape(r"Moment Demand, $M_{hy}$ (kN-m)"), f"{vals['Mh']:.1f}")
            )
            table.add_row(
                (NoEscape(r"Shear Demand, $V_{bx}$ (kN)"), f"{vals['Vb']:.1f}")
            )
            table.add_row(
                (NoEscape(r"Shear Demand, $V_{hy}$ (kN)"), f"{vals['Vh']:.1f}")
            )
            table.append(NoEscape(r"\bottomrule"))

    # Section 2: Section Properties
    with doc.create(Section("Classification & Logic")):
        doc.append(NoEscape(r"\textbf{Steel Reinforcement Ratio ($A_s/A_g$):}\newline"))
        steel_ok, steel_ratio = col.check_min_steel_ratio()
        steel_verdict = "Satisfied" if steel_ok else "NOT satisfied"
        doc.append(
            NoEscape(
                rf"AISC requires $\rho_s \ge 1\%$. Calculated $\rho_s = {steel_ratio*100:.2f}\%$. Condition is \textbf{{{steel_verdict}}}.\vspace{{0.3cm}}\newline"
            )
        )

        doc.append(NoEscape(r"\textbf{Axial Compactness:}\newline"))
        doc.append(
            NoEscape(
                rf"The governing slenderness $\lambda = \max(b/t, h/t) = {lam:.1f}$. The limit for compact sections is $\lambda_p = 2.26\sqrt{{E/F_y}} = {lam_p_ax:.1f}$. Since $\lambda {lam_relation} \lambda_p$, the section is \textbf{{{axial_class}}}.\vspace{{0.3cm}}\newline"
            )
        )

        doc.append(NoEscape(r"\textbf{Flexural Compactness:}\newline"))
        doc.append(
            NoEscape(
                rf"Evaluating web and flange slenderness limits yields a \textbf{{{results.get('Flexural Compactness (b-axis)', '')}}} section for b-axis bending, and a \textbf{{{results.get('Flexural Compactness (h-axis)', '')}}} section for h-axis bending.\vspace{{0.3cm}}\newline"
            )
        )

        doc.append(NoEscape(r"\textbf{Seismic Compactness ($R_y=1.3$):}\newline"))
        doc.append(
            NoEscape(
                rf"The highly ductile limit is $\lambda_{{hd}} = 1.48\sqrt{{E/(R_yF_y)}} = {lam_hd:.1f}$. Condition yields a \textbf{{{col.check_seismic_compactness(Ry=1.3)}}} section."
            )
        )

    # Section 3: Design Capacities & Shear Checks
    with doc.create(Section("Governing Capacities")):
        doc.append(NoEscape(r"\textbf{1. Axial Compression ($\phi P_n$)}\newline"))
        doc.append(
            NoEscape(
                r"{\small \[ P_{no} = F_y A_s + 0.85 f^\prime_c \left( A_c + A_{sr} \frac{E_s}{E_c} \right) \]}"
            )
        )
        doc.append(
            NoEscape(
                rf"Available axial strength is \textbf{{$\phi P_n = {_kips_to_kN(results.get('phi_Pn (kips)', 0)):.1f}$ kN}}.\vspace{{0.3cm}}\newline"
            )
        )

        doc.append(NoEscape(r"\textbf{2. Flexural Strength ($\phi M_n$)}\newline"))
        doc.append(
            NoEscape(
                r"{\small \[ M_B = M_D - F_y Z_{sn} - \frac{0.85 f^\prime_c Z_{cn}}{2} \]}"
            )
        )
        doc.append(
            NoEscape(
                rf"Resulting capacities are \textbf{{$\phi M_{{nx}} = {_kipin_to_kNm(results.get('phi_Mnx (kip-in)', 0)):.1f}$ kN-m}} and \textbf{{$\phi M_{{ny}} = {_kipin_to_kNm(results.get('phi_Mny (kip-in)', 0)):.1f}$ kN-m}}.\vspace{{0.3cm}}\newline"
            )
        )

        doc.append(NoEscape(r"\textbf{3. Shear Strength \& Demand Checks}\newline"))
        doc.append(
            NoEscape(
                r"{\small \[ V_n = 0.6 F_y A_v + 0.06 K_c A_c \sqrt{f^\prime_c} \]}"
            )
        )
        status_vbx = "OK" if shear_ratio_b <= 1.0 else "OVERSTRESSED"
        status_vhy = "OK" if shear_ratio_h <= 1.0 else "OVERSTRESSED"
        doc.append(
            NoEscape(
                rf"$\phi V_{{bx}} = {phi_Vbx_kN:.1f}\text{{ kN}} \rightarrow D/C = {shear_ratio_b:.3f}\text{{ ({status_vbx})}}$\newline"
            )
        )
        doc.append(
            NoEscape(
                rf"$\phi V_{{hy}} = {phi_Vhy_kN:.1f}\text{{ kN}} \rightarrow D/C = {shear_ratio_h:.3f}\text{{ ({status_vhy})}}$"
            )
        )

    # Section 4: Interaction Check
    with doc.create(Section("Interaction Equations (DG6)")):
        if Pu_kN < Pcc_kN:
            doc.append(
                NoEscape(
                    rf"Since $P_u$ ({Pu_kN:.1f} kN) $< P_{{cc}}$ ({Pcc_kN:.1f} kN), axial forces are omitted from the check.\vspace{{0.15cm}}\newline"
                )
            )
            eq_std = r"{\small \[ \text{Ratio} = \frac{M_{ux}}{\phi M_{nx}} + \frac{M_{uy}}{\phi M_{ny}} \le 1.0 \]}"
            eq_alpha = r"{\small \[ \text{Ratio}_{\alpha} = \left(\frac{M_{ux}}{\phi M_{nx}}\right)^{1.5} + \left(\frac{M_{uy}}{\phi M_{ny}}\right)^{1.5} \le 1.0 \]}"
        else:
            doc.append(
                NoEscape(
                    rf"Since $P_u$ ({Pu_kN:.1f} kN) $\ge P_{{cc}}$ ({Pcc_kN:.1f} kN), the bilinear axial term is applied.\vspace{{0.15cm}}\newline"
                )
            )
            eq_std = r"{\small \[ \text{Ratio} = \frac{P_u - P_{cc}}{\phi P_n - P_{cc}} + \frac{M_{ux}}{\phi M_{nx}} + \frac{M_{uy}}{\phi M_{ny}} \le 1.0 \]}"
            eq_alpha = r"{\small \[ \text{Ratio}_{\alpha} = \frac{P_u - P_{cc}}{\phi P_n - P_{cc}} + \left[ \left(\frac{M_{ux}}{\phi M_{nx}}\right)^{1.5} + \left(\frac{M_{uy}}{\phi M_{ny}}\right)^{1.5} \right]^{0.67} \le 1.0 \]}"

        doc.append(NoEscape(r"\textbf{1. Standard Interaction}\newline"))
        doc.append(NoEscape(eq_std))
        std_ratio = results.get("Interaction Ratio (Standard)", "N/A")
        if isinstance(std_ratio, (float, int)):
            status_std = "OK" if std_ratio <= 1.0 else "OVERSTRESSED"
            doc.append(
                NoEscape(
                    rf"\textbf{{Result: {std_ratio:.3f} ({status_std})}}\vspace{{0.3cm}}\newline"
                )
            )

        doc.append(NoEscape(r"\textbf{2. Modified Interaction ($\alpha=1.5$)}\newline"))
        doc.append(NoEscape(eq_alpha))
        alpha_ratio = results.get("Interaction Ratio (Alpha=1.5)", "N/A")
        if isinstance(alpha_ratio, (float, int)):
            status_alpha = "OK" if alpha_ratio <= 1.0 else "OVERSTRESSED"
            doc.append(
                NoEscape(rf"\textbf{{Result: {alpha_ratio:.3f} ({status_alpha})}}")
            )

    doc.append(NoEscape(r"\end{multicols}"))
    doc.append(NoEscape(r"\vspace{0.3cm}"))
    doc.append(NoEscape(r"\hrule"))
    doc.append(NoEscape(r"\vspace{0.4cm}"))

    # Section 5: Executive Summary
    with doc.create(Section("Executive Summary of Results")):
        with doc.create(Itemize()) as itemize:
            itemize.add_item(
                NoEscape(
                    rf"\textbf{{Governing Axial Capacity ($\phi P_n$):}} {_kips_to_kN(results.get('phi_Pn (kips)', 0)):.1f} kN"
                )
            )
            itemize.add_item(
                NoEscape(
                    rf"\textbf{{Governing Flexural Capacities:}} $\phi M_{{nx}} = $ {_kipin_to_kNm(results.get('phi_Mnx (kip-in)', 0)):.1f} kN-m, $\phi M_{{ny}} = $ {_kipin_to_kNm(results.get('phi_Mny (kip-in)', 0)):.1f} kN-m"
                )
            )
            itemize.add_item(
                NoEscape(
                    rf"\textbf{{Shear Checks:}} b-axis D/C = {shear_ratio_b:.3f} ({status_vbx}), h-axis D/C = {shear_ratio_h:.3f} ({status_vhy})"
                )
            )

            if isinstance(std_ratio, (float, int)) and isinstance(
                alpha_ratio, (float, int)
            ):
                stat_std = "PASS (OK)" if std_ratio <= 1.0 else "FAIL (OVERSTRESSED)"
                stat_alpha = (
                    "PASS (OK)" if alpha_ratio <= 1.0 else "FAIL (OVERSTRESSED)"
                )
                itemize.add_item(
                    NoEscape(
                        rf"\textbf{{Standard Interaction Status:}} {std_ratio:.3f} $\rightarrow$ {stat_std}"
                    )
                )
                itemize.add_item(
                    NoEscape(
                        rf"\textbf{{Modified Interaction Status ($\alpha=1.5$):}} {alpha_ratio:.3f} $\rightarrow$ {stat_alpha}"
                    )
                )
            else:
                itemize.add_item(
                    NoEscape(
                        r"\textbf{Final Interaction Status:} Check Failed/Not Permitted (See Compactness)"
                    )
                )

    # --- Compile PDF ---
    try:
        doc.generate_pdf(filepath, clean_tex=True, compiler="pdflatex")
        return filepath + ".pdf"
    except Exception as e:
        print(f"PDF Error: {str(e)}")
        return None


# =========================================================================== #
if __name__ == "__main__":
    print("-" * 60)
    print("Standalone Demo: AISC DG6 Example 2.5")
    print("-" * 60)

    # ---------------------------------------------------------
    # 1. DEFINE THE COMPOSITE MEMBER & ADVANCED PARAMETERS
    # ---------------------------------------------------------
    col = RectangularFilledComposite(
        # Geometry & Materials
        b_mm=635.0,  # 25 inches
        h_mm=635.0,  # 25 inches
        t_mm=12.7,  # 0.5 inches
        fc_mpa=41.37,  # ~6 ksi
        fy_mpa=344.74,  # ~50 ksi
        Lx_m=9.144,  # 30 ft
        Ly_m=9.144,  # 30 ft
        # Advanced Parameters
        Asr_mm2=0.0,
        Fysr_mpa=414.0,
        ri_mm=0.0,
        shear_span_to_depth=None,
        # Demands
        Pu_kN=6672.3,  # ~1500 kips
        Mbx_kNmm=2440472.0,  # ~1800 kip-ft
        Mhy_kNmm=0,
        Vbx_kN=400.3,  # ~90 kips
        Vhy_kN=0,
    )

    print("Running capacity checks...")
    results = col.interaction_check()
    for key, val in results.items():
        print(f"  {key:<32}: {val}")

    print("-" * 60)
    print("Waiting for save location in popup dialog...")

    # Use the existing GUI dialog function
    dest_file = select_save_file(default_name="Composite_Capacity_Report")

    if dest_file:
        print("\nGenerating PDF report...")

        # ---------------------------------------------------------
        # 2. GENERATE PDF DIRECTLY FROM PYTHON (NO EXCEL)
        # ---------------------------------------------------------
        out_path = export_standalone_pdf(col, dest_file)

        if out_path:
            print(f"Export complete: {out_path}")
        else:
            print("Export failed. Check LaTeX installation.")
    else:
        print("Export cancelled by user.")
