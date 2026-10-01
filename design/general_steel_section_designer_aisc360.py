"""
general_steel_section_designer_aisc360.py   -   ONE self-contained script
=======================================================
AISC 360-22 capacity checks for wide-flange / I-shaped members  +  one-page PDF calculation
report (pylatex) driven from Excel through xlwings.

    Section database : steelpy (AISC Shapes DB: W, M, S, HP)
    PDF report       : pylatex + a LaTeX install with pdflatex (extarticle, amsmath, booktabs, multicol)
    Excel button     : xlwings  ->  RunPython "from design import general_steel_section_designer_aisc360 as steel; steel.export_calcs()"

Install once:   pip install steelpy pylatex xlwings

UNITS  (inputs / outputs)
    Excel:  lengths of members in m,  forces in kN,  moments / torque in kN-m,  stresses in MPa
    Class:  section dims / lengths in mm, area mm^2, I mm^4, S,Z mm^3, Cw mm^6, stresses MPa,
            forces kN, moments kN.m

SCOPE (doubly symmetric I-shapes only)
    Ch. B  Table B4.1a / B4.1b classification          Ch. F  F2-F6, F13.1, F13.2 flexure
    Ch. D  D1-D3 tension (U = 1.0, D5/D6 ignored)      Ch. G  G2.1-G2.4, G6 shear (+ transverse stiffeners)
    Ch. E  E3, E4, E7 compression                      Ch. H  H1.1-H1.3, H2, H3.3, H4 combined + torsion

NOTES
  * steelpy values are rounded to ~3 significant figures; use WideFlangeCapacity.from_plates() for
    exact built-up / custom I-shapes.
  * Table B4.1b web limits (Case 15: 3.76 / 5.70 sqrt(E/Fy)) were not on the supplied page extract;
    the standard AISC 360-22 values are used.
  * Required strengths (Pr, Mr, Vr, Tr) come from your analysis (Chapter C second-order effects
    included where required).  H3.3 warping functions (Design Guide 9) are NOT solved here.

CODE MAP (search for the banner comments)
    [1] SectionProps / Stiffener / CheckResult       [4] LatexRenderer   (pylatex, mimics DG6 report layout)
    [2] WideFlangeCapacity  (all AISC checks)        [5] WideFlangeReport (build -> render -> compile -> fit)
    [3] Demands / content model / ReportBuilder      [6] Excel (xlwings) entry point  export_calcs()
"""

from __future__ import annotations

import datetime
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

from pylatex import Document, Itemize, Package, Section as LxSection, Tabular
from pylatex.utils import NoEscape, escape_latex
from steelpy import aisc

try:  # xlwings is only needed for export_calcs()
    import xlwings as xw
except ImportError:  # script still works standalone (see __main__)
    xw = None


# =========================================================================== #
# [1]-[2]  SECTION DATA + AISC 360-22 CAPACITY CHECKS
# =========================================================================== #
IN = 25.4  # mm per inch  (steelpy database is in inches)
_PROFILES = ("W_shapes", "M_shapes", "S_shapes", "HP_shapes")


# --------------------------------------------------------------------------- #
#  Helper data classes
# --------------------------------------------------------------------------- #
@dataclass
class SectionProps:
    """Section properties in mm-based units."""

    name: str
    kind: str  # "rolled" or "built-up"
    d: float
    bf: float
    tf: float
    tw: float
    h: float  # web clear height used for h/tw  (rolled: d-2k)
    A: float
    Ix: float
    Iy: float
    Zx: float
    Zy: float
    Sx: float
    Sy: float
    J: float
    Cw: float
    Qw: float  # first moment of half-section at neutral axis (major axis)
    Wno: float  # normalized warping function at flange tip

    @property
    def ho(self) -> float:
        return self.d - self.tf

    @property
    def rx(self) -> float:
        return math.sqrt(self.Ix / self.A)

    @property
    def ry(self) -> float:
        return math.sqrt(self.Iy / self.A)

    @property
    def rts(self) -> float:  # Eq. F2-7
        return math.sqrt(math.sqrt(self.Iy * self.Cw) / self.Sx)


@dataclass
class Stiffener:
    """Transverse stiffener data (mm).  b = width (outstand) of ONE plate, t = thickness."""

    b: float
    t: float
    Fy: Optional[float] = None  # defaults to web Fy
    pair: bool = True  # True = pair (both sides), False = single-sided
    c_end: Optional[float] = (
        None  # end panel only: distance stiffener inside face -> beam end
    )


@dataclass
class CheckResult:
    check: str
    code_ref: str
    method: str
    design_capacity: Optional[float]  # governing available strength (kN or kN.m)
    unit: str
    nominal: Optional[float]
    governing: str
    demand: Optional[float] = None
    dcr: Optional[float] = None
    limit_states: Dict[str, dict] = field(default_factory=dict)
    classification: dict = field(default_factory=dict)
    details: dict = field(
        default_factory=dict
    )  # intermediate values (used by the report exporter)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> Optional[bool]:
        return None if self.dcr is None else self.dcr <= 1.0

    def __str__(self) -> str:
        L = [f"{self.check}  [{self.code_ref}]  ({self.method})"]
        for k, v in self.limit_states.items():
            if "ratio" in v:
                L.append(f"   {k:<52s} ratio = {v['ratio']:.3f}")
            else:
                L.append(
                    f"   {k:<52s} Rn = {v['Rn']:>11.2f}   avail = {v['available']:>11.2f} {self.unit}"
                )
        if self.design_capacity is not None:
            L.append(
                f"   >> Governing: {self.governing}  ->  available = {self.design_capacity:.2f} {self.unit}"
            )
        if self.dcr is not None:
            L.append(
                f"   >> Demand = {self.demand}  DCR = {self.dcr:.3f}  {'OK' if self.ok else 'NOT OK'}"
            )
        for w in self.warnings:
            L.append(f"   !! {w}")
        return "\n".join(L)


# --------------------------------------------------------------------------- #
#  Generic geometry helper: plastic moment of stacked rectangles
# --------------------------------------------------------------------------- #
def _plastic_moment(rects: List[Tuple[float, float, float]], Fy: float) -> float:
    """rects = [(width, y0, y1), ...]  -> Mp (N.mm) about the equal-area axis."""
    A = sum(w * (y1 - y0) for w, y0, y1 in rects)

    def below(y):
        return sum(w * max(0.0, min(y, y1) - y0) for w, y0, y1 in rects)

    lo, hi = min(r[1] for r in rects), max(r[2] for r in rects)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if below(mid) < A / 2 else (lo, mid)
    yp = 0.5 * (lo + hi)
    M = 0.0
    for w, y0, y1 in rects:
        if y0 < yp:
            b1 = min(y1, yp)
            M += w * (b1 - y0) * (yp - 0.5 * (y0 + b1))
        if y1 > yp:
            a0 = max(y0, yp)
            M += w * (y1 - a0) * (0.5 * (a0 + y1) - yp)
    return Fy * M


# --------------------------------------------------------------------------- #
#  Main class
# --------------------------------------------------------------------------- #
class WideFlangeCapacity:
    """
    AISC 360-22 capacity checks for a wide-flange / I-shaped member.

    Parameters
    ----------
    designation : steelpy shape name, e.g. "W14X90" (case-insensitive)
    Fy, Fu      : MPa        (A992: 345 / 450)
    E, G        : MPa
    design_method : "LRFD" | "ASD"
    Lx, Ly, Lz  : unbraced lengths for buckling about x, y and twist (mm).  Lz defaults to Ly.
    Kx, Ky, Kz  : effective length factors
    Lb          : unbraced length for lateral-torsional buckling (mm).  Defaults to Ly.
    Cb          : LTB modification factor (default 1.0; see `calc_Cb`)
    """

    def __init__(
        self,
        designation: Optional[str] = None,
        Fy: float = 345.0,
        Fu: float = 450.0,
        E: float = 200_000.0,
        G: float = 77_200.0,
        design_method: str = "LRFD",
        Lx: float = 0.0,
        Ly: float = 0.0,
        Lz: Optional[float] = None,
        Kx: float = 1.0,
        Ky: float = 1.0,
        Kz: float = 1.0,
        Lb: Optional[float] = None,
        Cb: float = 1.0,
        section: Optional[SectionProps] = None,
    ):
        self.method = design_method.upper()
        if self.method not in ("LRFD", "ASD"):
            raise ValueError("design_method must be 'LRFD' or 'ASD'")
        self.Fy, self.Fu, self.E, self.G = Fy, Fu, E, G
        self.Lx, self.Ly = Lx, Ly
        self.Lz = Ly if Lz is None else Lz
        self.Kx, self.Ky, self.Kz = Kx, Ky, Kz
        self.Lb = Ly if Lb is None else Lb
        self.Cb = Cb
        if section is not None:
            self.s = section
        elif designation:
            self.s = self._load_steelpy(designation)
        else:
            raise ValueError("give a steelpy designation or a SectionProps")
        self.rolled = self.s.kind == "rolled"
        self.alpha = 1.0 if self.method == "LRFD" else 1.6  # H1-2 / G2-12 alpha

    # ------------------------------------------------------------------ #
    #  Section loading
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_steelpy(designation: str) -> SectionProps:
        key = designation.upper().replace(" ", "")
        for prof in _PROFILES:
            sec = getattr(aisc, prof).sections.get(key)
            if sec is not None:
                p = {k: v for k, v in sec.properties.items()}
                f = lambda n: float(p[n])
                d, bf, tf, tw, k = (
                    f("d") * IN,
                    f("bf") * IN,
                    f("tf") * IN,
                    f("tw") * IN,
                    f("k") * IN,
                )
                return SectionProps(
                    name=key,
                    kind="rolled",
                    d=d,
                    bf=bf,
                    tf=tf,
                    tw=tw,
                    h=d - 2 * k,
                    A=f("area") * IN**2,
                    Ix=f("Ix") * IN**4,
                    Iy=f("Iy") * IN**4,
                    Zx=f("Zx") * IN**3,
                    Zy=f("Zy") * IN**3,
                    Sx=f("Sx") * IN**3,
                    Sy=f("Sy") * IN**3,
                    J=f("J") * IN**4,
                    Cw=f("Cw") * IN**6,
                    Qw=f("Qw") * IN**3,
                    Wno=f("Wno") * IN**2,
                )
        raise ValueError(f"Shape '{designation}' not found in steelpy W/M/S/HP tables")

    @classmethod
    def from_plates(
        cls, d: float, bf: float, tf: float, tw: float, name: str = "BUILT-UP", **kw
    ):
        """Welded doubly symmetric I-shape from plate sizes (mm)."""
        h = d - 2 * tf
        ho = d - tf
        A = 2 * bf * tf + h * tw
        Ix = bf * d**3 / 12 - (bf - tw) * h**3 / 12
        Iy = 2 * tf * bf**3 / 12 + h * tw**3 / 12
        Zx = bf * tf * ho + tw * h**2 / 4
        Zy = tf * bf**2 / 2 + h * tw**2 / 4
        sec = SectionProps(
            name=name,
            kind="built-up",
            d=d,
            bf=bf,
            tf=tf,
            tw=tw,
            h=h,
            A=A,
            Ix=Ix,
            Iy=Iy,
            Zx=Zx,
            Zy=Zy,
            Sx=Ix / (d / 2),
            Sy=Iy / (bf / 2),
            J=(2 * bf * tf**3 + h * tw**3) / 3,
            Cw=Iy * ho**2 / 4,
            Qw=bf * tf * ho / 2 + tw * (h / 2) ** 2 / 2,
            Wno=ho * bf / 4,
        )
        return cls(section=sec, **kw)

    # ------------------------------------------------------------------ #
    #  Small utilities
    # ------------------------------------------------------------------ #
    @property
    def _rt_EF(self) -> float:
        return math.sqrt(self.E / self.Fy)

    def _avail(self, Rn: float, phi: float, omega: float) -> float:
        return phi * Rn if self.method == "LRFD" else Rn / omega

    def _ls(
        self, Rn_N: float, phi: float, omega: float, clause: str, scale: float, **extra
    ) -> dict:
        """limit-state record.  Rn_N in N or N.mm ; scale converts to kN / kN.m."""
        Rn = Rn_N / scale
        d = dict(
            Rn=Rn,
            phi=phi,
            omega=omega,
            available=self._avail(Rn, phi, omega),
            clause=clause,
        )
        d.update(extra)
        return d

    def _kc(self) -> float:
        return min(max(4.0 / math.sqrt(self.s.h / self.s.tw), 0.35), 0.76)

    def _Fn(self, Fe: float) -> float:  # Eq. E3-2 / E3-3
        if Fe == float("inf"):
            return self.Fy
        return (
            (0.658 ** (self.Fy / Fe)) * self.Fy if self.Fy / Fe <= 2.25 else 0.877 * Fe
        )

    @staticmethod
    def calc_Cb(Mmax: float, MA: float, MB: float, MC: float) -> float:
        """Eq. F1-1.  Absolute moments at max, 1/4, 1/2, 3/4 points of the unbraced segment."""
        return 12.5 * Mmax / (2.5 * Mmax + 3 * MA + 4 * MB + 3 * MC)

    def _finish(self, res: CheckResult, demand: Optional[float]) -> CheckResult:
        if demand is not None and res.design_capacity:
            res.demand = demand
            res.dcr = abs(demand) / res.design_capacity
        return res

    # ------------------------------------------------------------------ #
    #  Table B4.1a / B4.1b  classification
    # ------------------------------------------------------------------ #
    def classify_compression(self) -> dict:
        """Table B4.1a - members subject to axial compression (nonslender / slender)."""
        s, rEF = self.s, self._rt_EF
        lam_f = s.bf / 2 / s.tf
        if self.rolled:
            lr_f, case_f = 0.56 * rEF, "Case 1"
        else:
            lr_f, case_f = 0.64 * math.sqrt(self._kc() * self.E / self.Fy), "Case 2"
        lam_w, lr_w = s.h / s.tw, 1.49 * rEF
        out = dict(
            flange=dict(
                case=case_f,
                lam=lam_f,
                lam_r=lr_f,
                cls="slender" if lam_f > lr_f else "nonslender",
            ),
            web=dict(
                case="Case 5",
                lam=lam_w,
                lam_r=lr_w,
                cls="slender" if lam_w > lr_w else "nonslender",
            ),
        )
        out["slender"] = (
            out["flange"]["cls"] == "slender" or out["web"]["cls"] == "slender"
        )
        return out

    def classify_flexure(self, axis: str = "x") -> dict:
        """Table B4.1b - members subject to flexure (compact / noncompact / slender)."""
        s, rEF = self.s, self._rt_EF
        lam_f = s.bf / 2 / s.tf

        def cl(l, lp, lr):
            return "compact" if l <= lp else ("noncompact" if l <= lr else "slender")

        if axis == "y" or self.rolled:
            lp_f, lr_f, case_f = (
                0.38 * rEF,
                1.0 * rEF,
                "Case 13" if axis == "y" else "Case 10",
            )
        else:
            FL = 0.7 * self.Fy  # doubly symmetric, Sxt/Sxc = 1
            lp_f, lr_f, case_f = (
                0.38 * rEF,
                0.95 * math.sqrt(self._kc() * self.E / FL),
                "Case 11",
            )
        out = dict(
            axis=axis,
            flange=dict(
                case=case_f,
                lam=lam_f,
                lam_p=lp_f,
                lam_r=lr_f,
                cls=cl(lam_f, lp_f, lr_f),
            ),
        )
        if axis == "x":
            lam_w, lp_w, lr_w = s.h / s.tw, 3.76 * rEF, 5.70 * rEF
            out["web"] = dict(
                case="Case 15",
                lam=lam_w,
                lam_p=lp_w,
                lam_r=lr_w,
                cls=cl(lam_w, lp_w, lr_w),
            )
            out["section"] = (
                "compact"
                if out["flange"]["cls"] == out["web"]["cls"] == "compact"
                else "noncompact/slender"
            )
        else:
            out["web"] = None
            out["section"] = out["flange"]["cls"]
        return out

    # ------------------------------------------------------------------ #
    #  CHAPTER D - TENSION
    # ------------------------------------------------------------------ #
    def tensile_capacity(
        self,
        Pr: Optional[float] = None,
        An: Optional[float] = None,
        U: float = 1.0,
        L: Optional[float] = None,
    ) -> CheckResult:
        """
        AISC 360-22 D2.  Yielding on Ag (D2-1) and rupture on Ae = An*U (D2-2, D3-1).
        An : net area (mm2); defaults to Ag (no holes).    U : shear lag factor (default 1.0, Table D3.1 Case 1).
        Pr : required tensile strength (kN).   L : member length for D1 slenderness advisory (mm).
        """
        s = self.s
        An = s.A if An is None else An
        Ae = An * U
        ls = {
            "Tensile yielding, gross section (D2-1)": self._ls(
                self.Fy * s.A, 0.90, 1.67, "D2(a)", 1e3
            ),
            "Tensile rupture, net section (D2-2)": self._ls(
                self.Fu * Ae, 0.75, 2.00, "D2(b)", 1e3, Ae=Ae
            ),
        }
        gov = min(ls, key=lambda k: ls[k]["available"])
        res = CheckResult(
            "Tension",
            "AISC 360-22 Ch. D",
            self.method,
            ls[gov]["available"],
            "kN",
            ls[gov]["Rn"],
            gov,
            limit_states=ls,
        )
        res.notes += [
            f"U = {U} (Table D3.1 Case 1 assumed); An = {An:.0f} mm2",
            "Pin-connected members (D5) and eyebars (D6) ignored; block shear (J4.3) is a connection check.",
        ]
        L = L or max(self.Lx, self.Ly, self.Lz)
        if L:
            r_min = min(s.rx, s.ry)
            res.notes.append(
                f"D1 slenderness L/r = {L / r_min:.0f} (preferably <= 300)"
            )
            if L / r_min > 300:
                res.warnings.append(
                    "L/r exceeds the preferred limit of 300 (D1 User Note)"
                )
        return self._finish(res, Pr)

    # ------------------------------------------------------------------ #
    #  CHAPTER E - COMPRESSION
    # ------------------------------------------------------------------ #
    def _effective_area(self, Fn: float) -> Tuple[float, dict]:
        """E7.1 effective area for slender I-shape elements."""
        s, cls = self.s, self.classify_compression()
        info, reduction = {}, 0.0
        elems = [
            (
                "flange",
                s.bf / 2,
                s.tf,
                4,
                0.22,
                1.49,
            ),  # 4 outstand half-flanges  (Table E7.1 case c)
            ("web", s.h, s.tw, 1, 0.18, 1.31),
        ]  # stiffened element        (Table E7.1 case a)
        for name, b, t, n, c1, c2 in elems:
            lam, lr = cls[name]["lam"], cls[name]["lam_r"]
            if lam <= lr * math.sqrt(self.Fy / Fn):
                be = b
            else:
                Fel = (c2 * lr / lam) ** 2 * self.Fy  # E7-5
                q = math.sqrt(Fel / Fn)
                be = b * (1 - c1 * q) * q  # E7-3
            info[name] = dict(b=b, be=be)
            reduction += n * (b - be) * t
        return s.A - reduction, info

    def compressive_capacity(
        self,
        Pr: Optional[float] = None,
        Lx=None,
        Ly=None,
        Lz=None,
        Kx=None,
        Ky=None,
        Kz=None,
        xa: float = 0.0,
        ya: float = 0.0,
    ) -> CheckResult:
        """
        AISC 360-22 Ch. E.  Governing of flexural buckling about x and y (E3) and torsional
        buckling (E4); slender flanges/webs handled by E7 (Table B4.1a classification).
        xa, ya : lateral-bracing offset from shear center (E4(d)/(e)); one of them only.
        """
        s = self.s
        Lcx = (Kx or self.Kx) * (self.Lx if Lx is None else Lx)
        Lcy = (Ky or self.Ky) * (self.Ly if Ly is None else Ly)
        Lcz = (Kz or self.Kz) * (self.Lz if Lz is None else Lz)
        cls = self.classify_compression()
        slender = cls["slender"]
        pi2E = math.pi**2 * self.E

        Fex = pi2E / (Lcx / s.rx) ** 2 if Lcx > 0 else float("inf")  # E3-4 / E4-5
        Fey = pi2E / (Lcy / s.ry) ** 2 if Lcy > 0 else float("inf")  # E4-6
        if xa and ya:
            raise ValueError(
                "Bracing offset in both directions is not covered by E4 (analysis required)"
            )
        tb_clause = "E4(d)" if ya else ("E4(e)" if xa else "E4(a)")  # always defined
        if Lcz <= 0:
            Fez = float("inf")
        elif xa == 0 and ya == 0:  # E4-2
            Fez = (pi2E * s.Cw / Lcz**2 + self.G * s.J) / (s.Ix + s.Iy)
        else:
            ro2 = s.rx**2 + s.ry**2 + ya**2 + xa**2  # E4-11
            if ya:  # E4-10
                Fez = (
                    pi2E * s.Iy / Lcz**2 * (s.ho**2 / 4 + ya**2) + self.G * s.J
                ) / (s.A * ro2)
            else:  # E4-12
                Fez = (
                    pi2E * s.Iy / Lcz**2 * (s.ho**2 / 4 + s.Ix / s.Iy * xa**2)
                    + self.G * s.J
                ) / (s.A * ro2)

        ls = {}
        for name, Fe, cl in (
            ("Flexural buckling about x-axis", Fex, "E3"),
            ("Flexural buckling about y-axis", Fey, "E3"),
            ("Torsional buckling", Fez, tb_clause),
        ):
            Fn = self._Fn(Fe)
            if slender:
                Ae, det = self._effective_area(Fn)
                clause = f"{cl} + E7"
            else:
                Ae, det, clause = s.A, None, cl
            ls[name] = self._ls(
                Fn * Ae, 0.90, 1.67, clause, 1e3, Fe=Fe, Fn=Fn, Ae=Ae, eff_widths=det
            )
        gov = min(ls, key=lambda k: ls[k]["available"])
        res = CheckResult(
            "Compression",
            "AISC 360-22 Ch. E",
            self.method,
            ls[gov]["available"],
            "kN",
            ls[gov]["Rn"],
            gov,
            limit_states=ls,
            classification=cls,
        )
        res.details = dict(Lcx=Lcx, Lcy=Lcy, Lcz=Lcz)
        res.notes.append(
            "Section is "
            + (
                "SLENDER -> E7 effective area used"
                if slender
                else "NONSLENDER -> Pn = Fn*Ag (E3/E4)"
            )
        )
        for lab, Lc, r in (("KLx/rx", Lcx, s.rx), ("KLy/ry", Lcy, s.ry)):
            if Lc / r > 200:
                res.warnings.append(
                    f"{lab} = {Lc / r:.0f} exceeds preferred limit of 200 (E2 User Note)"
                )
        return self._finish(res, Pr)

    # ------------------------------------------------------------------ #
    #  CHAPTER F - FLEXURE
    # ------------------------------------------------------------------ #
    def _F2_ltb(self, Lb, Cb, Mp):
        s, E, Fy = self.s, self.E, self.Fy
        Jc = s.J / (s.Sx * s.ho)  # c = 1 (doubly symmetric)
        Lp = 1.76 * s.ry * math.sqrt(E / Fy)  # F2-5
        Lr = (
            1.95
            * s.rts
            * E
            / (0.7 * Fy)
            * math.sqrt(Jc + math.sqrt(Jc**2 + 6.76 * (0.7 * Fy / E) ** 2))
        )  # F2-6
        if Lb <= Lp:
            return Mp, Lp, Lr, "Lb <= Lp: LTB does not apply", None
        if Lb <= Lr:
            Mn = min(
                Cb * (Mp - (Mp - 0.7 * Fy * s.Sx) * (Lb - Lp) / (Lr - Lp)), Mp
            )  # F2-2
            return Mn, Lp, Lr, "Lp < Lb <= Lr: inelastic LTB (F2-2)", None
        Fcr = (
            Cb
            * math.pi**2
            * E
            / (Lb / s.rts) ** 2
            * math.sqrt(1 + 0.078 * Jc * (Lb / s.rts) ** 2)
        )  # F2-4
        return min(Fcr * s.Sx, Mp), Lp, Lr, "Lb > Lr: elastic LTB (F2-3)", Fcr

    def _flex_major(self, Lb, Cb, cls):
        """Returns (limit-state dict [N.mm], info dict)."""
        s, E, Fy = self.s, self.E, self.Fy
        fl, wb = cls["flange"], cls["web"]
        lam = fl["lam"]
        kc = self._kc()
        st, info = {}, {}
        # ---------------- F2 : compact web & compact flange
        if wb["cls"] == "compact" and fl["cls"] == "compact":
            Mp = Fy * s.Zx
            st["Yielding (F2-1)"] = (Mp, "F2.1")
            Mn, Lp, Lr, zone, Fcr = self._F2_ltb(Lb, Cb, Mp)
            st["Lateral-torsional buckling (F2.2)"] = (Mn, "F2.2")
            info.update(section="F2", Lp=Lp, Lr=Lr, ltb_zone=zone, Fcr=Fcr, Mp=Mp)
        # ---------------- F3 : compact web, noncompact/slender flange
        elif wb["cls"] == "compact":
            Mp = Fy * s.Zx
            Mn, Lp, Lr, zone, Fcr = self._F2_ltb(Lb, Cb, Mp)
            st["Lateral-torsional buckling (F3.1 -> F2.2)"] = (Mn, "F3.1")
            if fl["cls"] == "noncompact":
                Mn_f = Mp - (Mp - 0.7 * Fy * s.Sx) * (lam - fl["lam_p"]) / (
                    fl["lam_r"] - fl["lam_p"]
                )  # F3-1
            else:
                Mn_f = 0.9 * E * kc * s.Sx / lam**2  # F3-2
            st["Compression flange local buckling (F3.2)"] = (Mn_f, "F3.2")
            info.update(section="F3", Lp=Lp, Lr=Lr, ltb_zone=zone, Fcr=Fcr, Mp=Mp)
        # ---------------- F4 / F5 common terms
        else:
            hc = s.h
            aw = hc * s.tw / (s.bf * s.tf)
            rt = s.bf / math.sqrt(12 * (1 + aw / 6))  # F4-11
            Myc = Fy * s.Sx
            Mp = min(Fy * s.Zx, 1.6 * Fy * s.Sx)
            lam_w, lpw, lrw = wb["lam"], wb["lam_p"], wb["lam_r"]
            Lp = 1.1 * rt * math.sqrt(E / Fy)  # F4-7
            if wb["cls"] == "noncompact":
                # ------------ F4  (doubly symmetric, Iyc/Iy = 0.5 > 0.23)
                Rpc = (
                    Mp / Myc
                    if lam_w <= lpw
                    else min(
                        Mp / Myc - (Mp / Myc - 1) * (lam_w - lpw) / (lrw - lpw),
                        Mp / Myc,
                    )
                )  # F4-9
                FL = 0.7 * Fy
                Jt = s.J / (s.Sx * s.ho)
                Lr = (
                    1.95
                    * rt
                    * E
                    / FL
                    * math.sqrt(Jt + math.sqrt(Jt**2 + 6.76 * (FL / E) ** 2))
                )  # F4-8
                st["Compression flange yielding (F4.1)"] = (Rpc * Myc, "F4.1")
                if Lb <= Lp:
                    Mn, zone, Fcr = Rpc * Myc, "Lb <= Lp: LTB does not apply", None
                elif Lb <= Lr:
                    Mn = min(
                        Cb
                        * (Rpc * Myc - (Rpc * Myc - FL * s.Sx) * (Lb - Lp) / (Lr - Lp)),
                        Rpc * Myc,
                    )  # F4-2
                    zone, Fcr = "inelastic LTB (F4-2)", None
                else:
                    Fcr = (
                        Cb
                        * math.pi**2
                        * E
                        / (Lb / rt) ** 2
                        * math.sqrt(1 + 0.078 * Jt * (Lb / rt) ** 2)
                    )  # F4-5
                    Mn, zone = min(Fcr * s.Sx, Rpc * Myc), "elastic LTB (F4-3)"
                st["Lateral-torsional buckling (F4.2)"] = (Mn, "F4.2")
                if fl["cls"] == "noncompact":
                    st["Compression flange local buckling (F4.3)"] = (
                        Rpc * Myc
                        - (Rpc * Myc - FL * s.Sx)
                        * (lam - fl["lam_p"])
                        / (fl["lam_r"] - fl["lam_p"]),
                        "F4.3",
                    )  # F4-13
                elif fl["cls"] == "slender":
                    st["Compression flange local buckling (F4.3)"] = (
                        0.9 * E * kc * s.Sx / lam**2,
                        "F4.3",
                    )  # F4-14
                info.update(
                    section="F4",
                    Lp=Lp,
                    Lr=Lr,
                    ltb_zone=zone,
                    Fcr=Fcr,
                    Rpc=Rpc,
                    rt=rt,
                    Mp=Mp,
                )
            else:
                # ------------ F5  slender web
                aw5 = min(aw, 10.0)
                Rpg = min(
                    1
                    - aw5 / (1200 + 300 * aw5) * (hc / s.tw - 5.7 * math.sqrt(E / Fy)),
                    1.0,
                )  # F5-6
                Lr = math.pi * rt * math.sqrt(E / (0.7 * Fy))  # F5-5
                st["Compression flange yielding (F5.1)"] = (Rpg * Fy * s.Sx, "F5.1")
                if Lb <= Lp:
                    Fcr, zone = Fy, "Lb <= Lp: LTB does not apply"
                elif Lb <= Lr:
                    Fcr = min(Cb * (Fy - 0.3 * Fy * (Lb - Lp) / (Lr - Lp)), Fy)  # F5-3
                    zone = "inelastic LTB (F5-3)"
                else:
                    Fcr = min(Cb * math.pi**2 * E / (Lb / rt) ** 2, Fy)  # F5-4
                    zone = "elastic LTB (F5-4)"
                st["Lateral-torsional buckling (F5.2)"] = (Rpg * Fcr * s.Sx, "F5.2")
                if fl["cls"] == "noncompact":
                    Fcr_f = Fy - 0.3 * Fy * (lam - fl["lam_p"]) / (
                        fl["lam_r"] - fl["lam_p"]
                    )  # F5-8
                    st["Compression flange local buckling (F5.3)"] = (
                        Rpg * Fcr_f * s.Sx,
                        "F5.3",
                    )
                elif fl["cls"] == "slender":
                    Fcr_f = 0.9 * E * kc / lam**2  # F5-9
                    st["Compression flange local buckling (F5.3)"] = (
                        Rpg * Fcr_f * s.Sx,
                        "F5.3",
                    )
                info.update(
                    section="F5",
                    Lp=Lp,
                    Lr=Lr,
                    ltb_zone=zone,
                    Fcr=Fcr,
                    Rpg=Rpg,
                    rt=rt,
                    aw=aw,
                )
        return st, info

    def _flex_minor(self, cls):
        """F6 - yielding and flange local buckling about the minor axis."""
        s, Fy, E = self.s, self.Fy, self.E
        fl = cls["flange"]
        Mp = min(Fy * s.Zy, 1.6 * Fy * s.Sy)  # F6-1
        st = {"Yielding (F6.1)": (Mp, "F6.1")}
        lam = fl["lam"]
        if fl["cls"] == "noncompact":
            st["Flange local buckling (F6.2)"] = (
                Mp
                - (Mp - 0.7 * Fy * s.Sy)
                * (lam - fl["lam_p"])
                / (fl["lam_r"] - fl["lam_p"]),
                "F6.2",
            )  # F6-2
        elif fl["cls"] == "slender":
            st["Flange local buckling (F6.2)"] = (
                0.70 * E / lam**2 * s.Sy,
                "F6.2",
            )  # F6-3/4
        return st, dict(section="F6", Mp=Mp)

    def flexural_capacity(
        self,
        Mr: Optional[float] = None,
        axis: str = "x",
        Lb: Optional[float] = None,
        Cb: Optional[float] = None,
        moments: Optional[Tuple[float, float, float, float]] = None,
        Cb_factor: float = 1.0,
        bolt_holes: Optional[dict] = None,
    ) -> CheckResult:
        """
        AISC 360-22 Ch. F.
        axis   : "x" major (F2/F3/F4/F5 chosen from Table B4.1b)  |  "y" minor (F6)
        Lb, Cb : unbraced length (mm) and LTB factor (default from instance).
                 moments=(Mmax, MA, MB, MC) computes Cb with Eq. F1-1.
        Cb_factor : multiplier on Cb (H1.2 tension increase, sqrt(1 + alpha*Pr/Pey))
        bolt_holes: {"Afn": net tension-flange area (mm2)} -> F13.1 tension-flange rupture (major axis)
        Mr     : required moment (kN.m).
        """
        s = self.s
        Lb = self.Lb if Lb is None else Lb
        if moments is not None:
            Cb = self.calc_Cb(*moments)
        Cb = (self.Cb if Cb is None else Cb) * Cb_factor
        cls = self.classify_flexure(axis)
        if axis == "x":
            st, info = self._flex_major(Lb, Cb, cls)
        elif axis == "y":
            st, info = self._flex_minor(cls)
        else:
            raise ValueError("axis must be 'x' or 'y'")
        ls = {k: self._ls(v[0], 0.90, 1.67, v[1], 1e6) for k, v in st.items()}
        res = CheckResult(
            f"Flexure ({'major' if axis == 'x' else 'minor'} axis)",
            "AISC 360-22 Ch. F",
            self.method,
            None,
            "kN.m",
            None,
            "",
            limit_states=ls,
            classification=cls,
        )

        if bolt_holes and axis == "x":  # F13.1
            Afg = s.bf * s.tf
            Afn = bolt_holes["Afn"]
            Yt = 1.0 if self.Fy / self.Fu <= 0.8 else 1.1
            if self.Fu * Afn < Yt * self.Fy * Afg:
                ls["Tension flange rupture at holes (F13-1)"] = self._ls(
                    self.Fu * Afn / Afg * s.Sx, 0.90, 1.67, "F13.1", 1e6
                )
            else:
                res.notes.append(
                    "F13.1: Fu*Afn >= Yt*Fy*Afg -> tensile rupture of flange does not apply"
                )
        gov = min(ls, key=lambda k: ls[k]["available"])
        res.governing, res.design_capacity, res.nominal = (
            gov,
            ls[gov]["available"],
            ls[gov]["Rn"],
        )
        res.notes.append(f"Cb used = {Cb:.3f}, Lb = {Lb:.0f} mm")
        for k in ("Lp", "Lr", "Fcr", "Rpc", "Rpg", "rt"):
            if info.get(k) is not None:
                res.notes.append(f"{k} = {info[k]:.2f}")
        res.notes.append(
            f"Applicable section: {info['section']}"
            + (f" - {info['ltb_zone']}" if "ltb_zone" in info else "")
        )
        if axis == "x":
            res.notes.append(
                "Table B4.1b web limits (Case 15) taken as 3.76/5.70 sqrt(E/Fy)."
            )
        # F13.2 proportioning advisory for slender webs
        if axis == "x" and cls["web"]["cls"] == "slender":
            res.warnings.append(
                "Slender web: check F13.2 proportioning limits with proportion_check()"
            )
        res.details = dict(info, Cb=Cb, Lb=Lb)
        return self._finish(res, Mr)

    def proportion_check(self, a: Optional[float] = None) -> dict:
        """F13.2 limits for slender-web I-shapes.  a = clear distance between transverse stiffeners (mm)."""
        s, rEF = self.s, self._rt_EF
        htw = s.h / s.tw
        aw = s.h * s.tw / (s.bf * s.tf)
        if a is None:
            lim, why = 260.0, "unstiffened girder: h/tw <= 260"
        elif a / s.h <= 1.5:
            lim, why = 12.0 * rEF, "a/h <= 1.5 : (h/tw)max = 12.0 sqrt(E/Fy)  (F13-3)"
        else:
            lim, why = (
                0.40 * self.E / self.Fy,
                "a/h > 1.5 : (h/tw)max = 0.40 E/Fy  (F13-4)",
            )
        return dict(
            h_tw=htw, limit=lim, rule=why, h_tw_ok=htw <= lim, aw=aw, aw_ok=aw <= 10.0
        )

    # ------------------------------------------------------------------ #
    #  CHAPTER G - SHEAR
    # ------------------------------------------------------------------ #
    def _Cv2(self, htw: float, kv: float) -> float:  # G2-9 .. G2-11
        r1 = 1.10 * math.sqrt(kv * self.E / self.Fy)
        r2 = 1.37 * math.sqrt(kv * self.E / self.Fy)
        if htw <= r1:
            return 1.0
        if htw <= r2:
            return r1 / htw
        return 1.51 * kv * self.E / (htw**2 * self.Fy)

    def _shear_major(
        self,
        Vr,
        a,
        stf: Optional[Stiffener],
        tension_field,
        panel,
        Mr,
        res: CheckResult,
    ):
        s, E, Fy = self.s, self.E, self.Fy
        h, tw = s.h, s.tw
        Aw = s.d * tw
        htw = h / tw
        rEF = self._rt_EF
        kv = (
            5.34 if a is None else (5 + 5 / (a / h) ** 2 if a / h <= 3.0 else 5.34)
        )  # G2-5
        ls = {}
        # ---- G2.1
        if self.rolled and htw <= 2.24 * rEF:
            Cv1, phi, om, cl = 1.0, 1.00, 1.50, "G2.1(a)"
        else:
            lim = 1.10 * math.sqrt(kv * E / Fy)
            Cv1 = 1.0 if htw <= lim else lim / htw
            phi, om, cl = 0.90, 1.67, "G2.1(b)"
        Vn1 = 0.6 * Fy * Aw * Cv1
        ls["Web shear, no tension field (G2-1)"] = self._ls(
            Vn1, phi, om, cl, 1e3, Cv1=Cv1, kv=kv
        )
        res.details = dict(h=h, tw=tw, Aw=Aw, htw=htw, kv=kv)
        res.notes.append(
            f"h/tw = {htw:.1f}, kv = {kv:.2f}, Cv1 = {Cv1:.3f}, Aw = d*tw = {Aw:.0f} mm2"
        )

        # ---- G2.2 / G2.3
        if tension_field:
            if a is None or a / h > 3.0:
                res.warnings.append(
                    "Tension-field action needs stiffener spacing a with a/h <= 3.0 - ignored"
                )
            elif panel == "interior":
                Cv2 = self._Cv2(htw, kv)
                if htw <= 1.10 * math.sqrt(kv * E / Fy):
                    Vn2 = 0.6 * Fy * Aw  # G2-6
                else:
                    Afc = Aft = s.bf * s.tf
                    ah = a / h
                    if 2 * Aw / (Afc + Aft) <= 2.5 and h / s.bf <= 6.0:
                        Vn2 = (
                            0.6
                            * Fy
                            * Aw
                            * (Cv2 + (1 - Cv2) / (1.15 * math.sqrt(1 + ah**2)))
                        )  # G2-7
                    else:
                        Vn2 = (
                            0.6
                            * Fy
                            * Aw
                            * (Cv2 + (1 - Cv2) / (1.15 * (ah + math.sqrt(1 + ah**2))))
                        )  # G2-8
                ls["Web shear, tension field - interior panel (G2.2)"] = self._ls(
                    Vn2, 0.90, 1.67, "G2.2", 1e3, Cv2=Cv2
                )
            else:  # end panel, G2.3
                if stf is None:
                    res.warnings.append(
                        "End-panel tension field (G2.3) needs a Stiffener - ignored"
                    )
                else:
                    Cv2 = self._Cv2(htw, kv)
                    if Cv2 >= 1.0:
                        Vn3 = 0.6 * Fy * Aw
                    else:
                        de = (
                            35 * tw * (0.8 - Cv2) ** 2 if Cv2 <= 0.8 else 0.0
                        )  # G2-14 / 15
                        Mpf = _plastic_moment(
                            [(s.bf, 0.0, s.tf), (tw, s.tf, s.tf + de)], Fy
                        )
                        cap = 0.84 * tw * rEF
                        c = max(
                            min(stf.c_end if stf.c_end is not None else cap, cap), stf.t
                        )
                        n = 2 if stf.pair else 1
                        Fyst = stf.Fy or Fy
                        Mpst = _plastic_moment(
                            [(tw, 0.0, c + de), (n * stf.b, c - stf.t, c)], Fyst
                        )
                        Mpm = min(Mpf, Mpst)
                        bv = min(
                            2.8
                            * (math.sqrt(Mpf + Mpm) + math.sqrt(Mpst + Mpm))
                            / (h * math.sqrt(Fy * tw * (1 - Cv2))),
                            1.0,
                        )  # G2-13
                        Vn3 = (
                            0.6
                            * Fy
                            * Aw
                            * (
                                Cv2
                                + bv * (1 - Cv2) / (1.15 * math.sqrt(1 + (a / h) ** 2))
                            )
                        )  # G2-12
                    ls["Web shear, tension field - end panel (G2.3)"] = self._ls(
                        Vn3, 0.90, 1.67, "G2.3", 1e3
                    )
                    if Mr is not None:
                        f = self.alpha * Mr * 1e6 / s.Sx
                        res.notes.append(
                            f"G2.3 tension-flange stress alpha*Mr/Sxt = {f:.1f} MPa vs 0.35Fy = {0.35 * Fy:.1f} MPa "
                            + (
                                "OK"
                                if f <= 0.35 * Fy
                                else "-> NOT OK, G2.3 not permitted"
                            )
                        )
                        if f > 0.35 * Fy:
                            ls.pop("Web shear, tension field - end panel (G2.3)")
                    else:
                        res.notes.append(
                            "G2.3: verify alpha*Mr/Sxt <= 0.35Fy in the end panel (supply Mr)."
                        )
        # permitted to take the LARGER available strength (G2.2 note)
        gov = max(ls, key=lambda k: ls[k]["available"])
        res.limit_states = ls
        res.governing = gov
        res.design_capacity, res.nominal = ls[gov]["available"], ls[gov]["Rn"]

        # ---- G2.4 transverse stiffener requirements
        if a is not None or stf is not None:
            self._stiffener_check(Vr, a, stf, Aw, htw, kv, ls, res)

    def _stiffener_check(self, Vr, a, stf, Aw, htw, kv, ls, res):
        s, E, Fy = self.s, self.E, self.Fy
        h, tw = s.h, s.tw
        rEF = self._rt_EF
        out = {}
        Vc1 = res.design_capacity
        # (a) are stiffeners required?
        lim_ns = 1.10 * math.sqrt(5.34 * E / Fy)
        Cv1_ns = 1.0 if htw <= lim_ns else lim_ns / htw
        if self.rolled and htw <= 2.24 * rEF:
            Vc_ns = self._avail(0.6 * Fy * Aw * 1.0, 1.0, 1.5) / 1e3
        else:
            Vc_ns = self._avail(0.6 * Fy * Aw * Cv1_ns, 0.9, 1.67) / 1e3
        req_free = htw <= 2.54 * rEF or (Vr is not None and Vc_ns >= Vr)
        out["G2.4(a)"] = "not required" if req_free else "required"
        if stf is None:
            res.classification["stiffener_check"] = out
            return
        Fyst = stf.Fy or Fy
        bt = stf.b / stf.t
        out["G2.4(d)"] = f"(b/t)st {bt:.2f} <= {0.56 * math.sqrt(E / Fyst):.2f} " + (
            "OK" if bt <= 0.56 * math.sqrt(E / Fyst) else "NOT OK"
        )
        if a is None:
            out["G2.4(e)"] = "spacing a needed for Ist check"
            res.classification["stiffener_check"] = out
            return
        Ist = (
            stf.t * (2 * stf.b + tw) ** 3 / 12 if stf.pair else stf.t * stf.b**3 / 3
        )  # about web centre / web face
        rho_st = max(Fy / Fyst, 1.0)
        Ist1 = h**4 * rho_st**1.3 / 40 * (Fy / E) ** 1.5  # G2-18
        bp = min(a, h)
        Ist2 = max((2.5 / (a / h) ** 2 - 2) * bp * tw**3, 0.5 * bp * tw**3)  # G2-19
        Cv2 = self._Cv2(htw, kv)
        phi, om = (0.9, 1.67)
        Vc2 = self._avail(0.6 * Fy * Aw * Cv2, phi, om) / 1e3
        if Vr is None or Vc1 <= Vc2:
            rho_w = 1.0 if Vr is None or Vr > Vc2 else 0.0
        else:
            rho_w = max((Vr - Vc2) / (Vc1 - Vc2), 0.0)
        Ist_req = Ist2 + (Ist1 - Ist2) * rho_w  # G2-17
        out["G2.4(e)"] = (
            f"Ist {Ist:.3e} >= {Ist_req:.3e} mm4 "
            + ("OK" if Ist >= Ist_req else "NOT OK")
            + f" [Ist1={Ist1:.3e}, Ist2={Ist2:.3e}, rho_w={rho_w:.2f}]"
        )
        out["G2.4(b,c) detailing"] = (
            "stop stiffener weld 4-6 tw from web-flange toe; bolts <= 300 mm o.c.; "
            "intermittent fillet clear spacing <= min(16 tw, 250 mm)"
        )
        res.classification["stiffener_check"] = out
        if Vr is None:
            res.notes.append(
                "Vr not given: rho_w = 1.0 used for Ist required (conservative)."
            )

    def _shear_minor(self, res: CheckResult):
        """G6 - minor-axis shear on the two flanges (kv = 1.2, h/tw = bf/(2 tf))."""
        s = self.s
        Cv2 = self._Cv2(s.bf / (2 * s.tf), 1.2)
        Vn = 2 * 0.6 * self.Fy * s.bf * s.tf * Cv2  # two flanges act as shear elements
        res.limit_states = {
            "Flange shear, 2 flanges (G6-1)": self._ls(
                Vn, 0.90, 1.67, "G6", 1e3, Cv2=Cv2
            )
        }
        k = next(iter(res.limit_states))
        res.governing, res.design_capacity, res.nominal = (
            k,
            res.limit_states[k]["available"],
            res.limit_states[k]["Rn"],
        )
        res.notes.append(
            f"Cv2 = {Cv2:.3f} (bf/2tf = {s.bf / 2 / s.tf:.2f}, kv = 1.2); Vn = 2 x 0.6 Fy bf tf Cv2"
        )

    def shear_capacity(
        self,
        Vr: Optional[float] = None,
        axis: str = "x",
        a: Optional[float] = None,
        stiffener: Optional[Stiffener] = None,
        tension_field: bool = False,
        panel: str = "interior",
        Mr: Optional[float] = None,
    ) -> CheckResult:
        """
        AISC 360-22 Ch. G.
        axis "x" : major-axis (web) shear, G2.  axis "y" : minor-axis (flange) shear, G6.
        a            : clear distance between transverse stiffeners (mm); None = unstiffened.
        stiffener    : Stiffener(b, t, Fy, pair, c_end) -> G2.4 checks (slenderness, Ist).
        tension_field: use G2.2 (interior panel) / G2.3 (end panel) in addition to G2.1;
                       larger available strength governs.
        panel        : "interior" | "end"
        Mr           : end-panel moment (kN.m) for the G2.3 0.35Fy tension-flange stress limit.
        """
        res = CheckResult(
            "Shear (major axis, web)" if axis == "x" else "Shear (minor axis, flanges)",
            "AISC 360-22 Ch. G",
            self.method,
            None,
            "kN",
            None,
            "",
        )
        if axis == "x":
            self._shear_major(Vr, a, stiffener, tension_field, panel, Mr, res)
        elif axis == "y":
            self._shear_minor(res)
        else:
            raise ValueError("axis must be 'x' or 'y'")
        return self._finish(res, Vr)

    # ------------------------------------------------------------------ #
    #  CHAPTER H - COMBINED FORCES AND TORSION
    # ------------------------------------------------------------------ #
    @staticmethod
    def _h1(Pr, Pc, Mrx, Mcx, Mry, Mcy):
        pr = Pr / Pc if Pc else 0.0
        m = (Mrx / Mcx if Mcx else 0.0) + (Mry / Mcy if Mcy else 0.0)
        return pr + 8.0 / 9.0 * m if pr >= 0.2 else pr / 2.0 + m  # H1-1a / H1-1b

    def combined_forces_torsion_capacity(
        self,
        Pr: float = 0.0,
        axial: str = "compression",
        Mrx: float = 0.0,
        Mry: float = 0.0,
        Vrx: float = 0.0,
        Vry: float = 0.0,
        Tr: float = 0.0,
        Lb: Optional[float] = None,
        Cb: Optional[float] = None,
        moments: Optional[Tuple[float, float, float, float]] = None,
        interaction: str = "H1",
        use_H1_3: bool = False,
        An: Optional[float] = None,
        U: float = 1.0,
        bolt_holes: Optional[dict] = None,
        T_sv: Optional[float] = None,
        Bimoment: float = 0.0,
        warping_shear_stress: float = 0.0,
        Fcr_normal: Optional[float] = None,
        Fcr_shear: Optional[float] = None,
        **length_overrides,
    ) -> CheckResult:
        """
        AISC 360-22 Ch. H (I-shaped members only).

        Demands (magnitudes unless noted):  Pr kN, axial = "compression"|"tension";  Mrx, Mry kN.m;
            Vrx, Vry kN;  Tr kN.m.
            SIGN (used by H2, H3.3, H4 corner checks):  Mrx > 0 -> compression in top flange,
            Mry > 0 -> compression on the +x flange tips.  H1 uses magnitudes.
        interaction : "H1" (H1.1/H1.2)  or  "H2" (stress form) as the governing axial-flexure check.
        use_H1_3    : use H1.3 single-axis alternative when it is permitted (rolled compact, Lcz <= Lcy, Mry/Mcy < 0.05)
        bolt_holes  : {"Afn": net flange area, "flanges": ("top","bottom")}  -> F13.1 & H4 check
        Torsion (H3.3): T_sv = St. Venant part of Tr (kN.m, default = Tr);
            Bimoment (kN.m^2) -> warping normal stress = B*Wno/Cw;  warping_shear_stress (MPa);
            Fcr_normal / Fcr_shear : buckling stresses (MPa) from analysis, if a buckling check is wanted.
        length_overrides : Lx, Ly, Lz, Kx, Ky, Kz forwarded to the compression check.
        """
        s, Fy = self.s, self.Fy
        if axial not in ("compression", "tension"):
            raise ValueError("axial must be 'compression' or 'tension'")
        Lb = self.Lb if Lb is None else Lb
        Cbv = self.Cb if Cb is None else Cb
        if moments is not None:
            Cbv = self.calc_Cb(*moments)
        res = CheckResult(
            "Combined forces & torsion",
            "AISC 360-22 Ch. H",
            self.method,
            None,
            "-",
            None,
            "",
        )
        ls: Dict[str, dict] = {}

        # ---------------- available strengths
        Pc = None
        Ten = self.tensile_capacity(An=An, U=U)
        Cmp = self.compressive_capacity(**length_overrides)
        if Pr > 0:
            Pc = (Cmp if axial == "compression" else Ten).design_capacity
        # Cb increase for axial tension (H1.2) - doubly symmetric only
        Cb_fac = 1.0
        if Pr > 0 and axial == "tension" and Lb > 0:
            Pey = math.pi**2 * self.E * s.Iy / Lb**2
            Cb_fac = math.sqrt(1 + self.alpha * Pr * 1e3 / Pey)
            res.notes.append(
                f"H1.2: Cb increased by sqrt(1+alpha*Pr/Pey) = {Cb_fac:.3f}"
            )
        Fx = self.flexural_capacity(
            axis="x", Lb=Lb, Cb=Cbv, Cb_factor=Cb_fac, bolt_holes=bolt_holes
        )
        Fy_ = self.flexural_capacity(axis="y")
        Mcx, Mcy = Fx.design_capacity, Fy_.design_capacity

        # ---------------- H1.1 / H1.2
        r_h1 = self._h1(Pr, Pc or 1.0, abs(Mrx), Mcx, abs(Mry), Mcy)
        ls["H1.1/H1.2 (Eq. H1-1a/b)"] = dict(
            ratio=r_h1,
            clause="H1.1" if axial == "compression" else "H1.2",
            Pr=Pr,
            Pc=Pc,
            Mcx=Mcx,
            Mcy=Mcy,
        )
        # ---------------- H1.3 (rolled, compact, compression, single-axis)
        h13_ok = (
            axial == "compression"
            and Pr > 0
            and self.rolled
            and Fx.classification["section"] == "compact"
            and Fy_.classification["flange"]["cls"] == "compact"
            and (
                self.Kz * self.Lz
                if "Lz" not in length_overrides
                else length_overrides["Lz"]
            )
            <= (
                self.Ky * self.Ly
                if "Ly" not in length_overrides
                else length_overrides["Ly"]
            )
            + 1e-9
            and (Mcy and abs(Mry) / Mcy < 0.05)
        )
        r_h13 = None
        if h13_ok:
            Fx1 = self.flexural_capacity(axis="x", Lb=Lb, Cb=1.0)
            ltb_key = next(k for k in Fx1.limit_states if "Lateral" in k)
            Mcx_ltb = Fx1.limit_states[ltb_key]["available"]
            Mcx_y = Fx.limit_states[next(k for k in Fx.limit_states if "Yield" in k)][
                "available"
            ]
            Pcx = Cmp.limit_states["Flexural buckling about x-axis"]["available"]
            Pcy = Cmp.limit_states["Flexural buckling about y-axis"]["available"]
            r_in = self._h1(Pr, Pcx, abs(Mrx), Mcx_y, abs(Mry), Mcy)  # in-plane
            r_out = (
                Pr / Pcy * (1.5 - 0.5 * Pr / Pcy) + (abs(Mrx) / (Cbv * Mcx_ltb)) ** 2
            )  # H1-3
            r_h13 = max(r_in, r_out)
            ls["H1.3 in-plane (H1-1 with Pcx, Mcx = phi*Mp)"] = dict(
                ratio=r_in, clause="H1.3(a)"
            )
            ls["H1.3 out-of-plane (H1-3)"] = dict(ratio=r_out, clause="H1.3(b)")
        elif use_H1_3:
            res.warnings.append(
                "H1.3 not permitted here (needs rolled compact I-shape, compression, Lcz <= Lcy, Mry/Mcy < 0.05)"
            )
        # ---------------- H2 stress form (any signed combination)
        fa = Pr * 1e3 / s.A
        Fca_c = Cmp.design_capacity * 1e3 / s.A
        Fca_t = Ten.design_capacity * 1e3 / s.A
        Fcbx, Fcby = Mcx * 1e6 / s.Sx, Mcy * 1e6 / s.Sy
        fbx, fby = Mrx * 1e6 / s.Sx, Mry * 1e6 / s.Sy
        h2_max, corner_stress = 0.0, []
        for sx in (1, -1):
            for sy in (1, -1):
                f_axial = fa if axial == "compression" else -fa  # +compression
                f_bx, f_by = sy * fbx, sx * fby  # +compression at the corner
                ratio = 0.0
                for f, Fc_pos, Fc_neg in (
                    (f_axial, Fca_c, Fca_t),
                    (f_bx, Fcbx, Fcbx),
                    (f_by, Fcby, Fcby),
                ):
                    ratio += (
                        f / (Fc_pos if f >= 0 else Fc_neg)
                        if (Fc_pos and Fc_neg)
                        else 0.0
                    )
                corner_stress.append(f_axial + f_bx + f_by)
                h2_max = max(h2_max, ratio)
        ls["H2 (Eq. H2-1, worst flange tip)"] = dict(ratio=h2_max, clause="H2")

        # ---------------- H4 flange rupture at bolt holes
        if bolt_holes:
            flg = bolt_holes.get("flanges", ("top", "bottom"))
            Pc4 = self._avail(self.Fu * (An if An else s.A) * U, 0.75, 2.00) / 1e3
            rup = next((k for k in Fx.limit_states if "rupture" in k), None)
            Mc4 = (
                Fx.limit_states[rup]["available"]
                if rup
                else self._avail(Fy * s.Zx, 0.90, 1.67) / 1e6
            )
            P_signed = Pr if axial == "tension" else -Pr  # + tension
            for name, sgn in (("top", -1), ("bottom", 1)):  # + Mrx => top compression
                if name in flg:
                    r4 = P_signed / Pc4 + sgn * Mrx / Mc4
                    ls[f"H4 flange rupture at holes - {name} flange (H4-1)"] = dict(
                        ratio=r4, clause="H4"
                    )
            res.notes.append(
                "H4: flange with bolt holes checked where net tension exists (ratio only meaningful if > 0)."
            )

        # ---------------- H3.3 torsion (non-HSS)
        torsion = Tr > 0 or Bimoment != 0 or warping_shear_stress != 0
        if torsion:
            phiT, omT = 0.90, 1.67
            aT = (lambda F: phiT * F) if self.method == "LRFD" else (lambda F: F / omT)
            fw = abs(Bimoment) * 1e9 * s.Wno / s.Cw  # kN.m2 -> N.mm2 ; MPa
            f_axial_c = fa if axial == "compression" else -fa  # +compression
            f_un = (
                max(
                    abs(f_axial_c + sy_ * fbx + sx_ * fby)
                    for sx_ in (1, -1)
                    for sy_ in (1, -1)
                )
                + fw
            )
            Tsv = (Tr if T_sv is None else T_sv) * 1e6
            t_sv_f, t_sv_w = Tsv * s.tf / s.J, Tsv * s.tw / s.J
            t_vx = Vrx * 1e3 * s.Qw / (s.Ix * s.tw)
            t_vy = 1.5 * Vry * 1e3 / (2 * s.bf * s.tf)
            f_uv_f = t_sv_f + warping_shear_stress + t_vy
            f_uv_w = t_sv_w + t_vx
            f_uv = max(f_uv_f, f_uv_w)
            ls["H3.3(a) normal stress yielding, f_un <= phi*Fy"] = dict(
                ratio=f_un / aT(Fy), clause="H3.3(a)", f_un=f_un
            )
            ls["H3.3(b) shear yielding, f_uv <= phi*0.6Fy"] = dict(
                ratio=f_uv / aT(0.6 * Fy),
                clause="H3.3(b)",
                f_uv=f_uv,
                flange=f_uv_f,
                web=f_uv_w,
            )
            if Fcr_normal:
                ls["H3.3(c) buckling, normal"] = dict(
                    ratio=f_un / aT(Fcr_normal), clause="H3.3(c)"
                )
            if Fcr_shear:
                ls["H3.3(c) buckling, shear"] = dict(
                    ratio=f_uv / aT(Fcr_shear), clause="H3.3(c)"
                )
            res.notes.append(
                "H3.3 uses elastic stresses. Warping effects only if Bimoment / warping_shear_stress supplied "
                "(Design Guide 9 analysis); otherwise Tr is treated as pure St. Venant torsion."
            )
            if Bimoment == 0 and warping_shear_stress == 0:
                res.warnings.append(
                    "No warping stresses supplied - open I-shapes under torsion carry most of Tr by warping; "
                    "this check may be unconservative for normal stress."
                )

        # ---------------- governing
        gov_pool = {}
        if interaction == "H2":
            gov_pool["H2 (Eq. H2-1, worst flange tip)"] = h2_max
        elif use_H1_3 and r_h13 is not None:
            gov_pool["H1.3 (governing of in-plane / out-of-plane)"] = r_h13
        else:
            gov_pool["H1.1/H1.2 (Eq. H1-1a/b)"] = r_h1
        for k, v in ls.items():
            if k.startswith(("H3.3", "H4")):
                gov_pool[k] = v["ratio"]
        gov = max(gov_pool, key=gov_pool.get)
        res.limit_states = ls
        res.governing, res.dcr, res.demand = gov, gov_pool[gov], 1.0
        res.classification = dict(
            flexure_x=Fx.classification,
            flexure_y=Fy_.classification,
            compression=Cmp.classification,
        )
        res.details = dict(
            Pr=Pr,
            Pc=Pc,
            Mcx=Mcx,
            Mcy=Mcy,
            Mrx=Mrx,
            Mry=Mry,
            h1=r_h1,
            h2=h2_max,
            h13=r_h13,
            axial=axial,
        )
        res.notes.append(
            f"Pc = {Pc if Pc else 0:.1f} kN, Mcx = {Mcx:.1f} kN.m, Mcy = {Mcy:.1f} kN.m (available)"
        )
        res.notes.append(
            "Pr, Mr must include second-order effects per Chapter C when axial compression is present."
        )
        return res

    # ------------------------------------------------------------------ #
    #  Convenience report / PDF export
    # ------------------------------------------------------------------ #
    def export_report(self, filename: str = "member_report", **demands) -> str:
        """One-page PDF calculation report (needs pylatex + a LaTeX install)."""
        return WideFlangeReport(self).export(filename, Demands(**demands))

    def summary(self) -> str:
        s = self.s
        c, fx, fy = (
            self.classify_compression(),
            self.classify_flexure("x"),
            self.classify_flexure("y"),
        )
        lines = [
            f"{s.name}  ({s.kind})   Fy={self.Fy} MPa  Fu={self.Fu} MPa   {self.method}",
            f"  d={s.d:.1f} bf={s.bf:.1f} tf={s.tf:.1f} tw={s.tw:.1f} h={s.h:.1f} mm   A={s.A:.0f} mm2",
            f"  Compression : flange {c['flange']['cls']} ({c['flange']['lam']:.2f}/{c['flange']['lam_r']:.2f}), "
            f"web {c['web']['cls']} ({c['web']['lam']:.2f}/{c['web']['lam_r']:.2f})",
            f"  Flexure-x   : flange {fx['flange']['cls']} ({fx['flange']['lam']:.2f}; p={fx['flange']['lam_p']:.2f} r={fx['flange']['lam_r']:.2f}), "
            f"web {fx['web']['cls']} ({fx['web']['lam']:.2f}; p={fx['web']['lam_p']:.2f} r={fx['web']['lam_r']:.2f})",
            f"  Flexure-y   : flange {fy['flange']['cls']}",
        ]
        return "\n".join(lines)


# =========================================================================== #
# [3]  REPORT INPUT, CONTENT MODEL AND BUILDER (all AISC report content lives here; no pylatex)
# =========================================================================== #
# 1. INPUT
# =========================================================================== #
@dataclass
class Demands:
    """Required strengths (kN, kN.m) and options forwarded to the capacity checks."""

    Pr: float = 0.0
    axial: str = "compression"  # "compression" | "tension"
    Mrx: float = 0.0
    Mry: float = 0.0
    Vrx: float = 0.0
    Vry: float = 0.0
    Tr: float = 0.0
    # shear / stiffener options (G2)
    a: Optional[float] = None
    stiffener: Optional[Stiffener] = None
    tension_field: bool = False
    panel: str = "interior"
    # combined-force options (H)
    interaction: str = "H1"
    use_H1_3: bool = False
    moments: Optional[Tuple[float, float, float, float]] = (
        None  # (Mmax, MA, MB, MC) -> Cb
    )
    An: Optional[float] = None
    U: float = 1.0
    bolt_holes: Optional[dict] = None
    T_sv: Optional[float] = None
    Bimoment: float = 0.0
    warping_shear_stress: float = 0.0


# =========================================================================== #
# 2. NEUTRAL CONTENT MODEL  (no pylatex here)
# =========================================================================== #
@dataclass
class Text:
    latex: str  # paragraph; may contain inline $math$


@dataclass
class Head:
    latex: str  # bold run-in heading, e.g. "Axial Compactness (Table B4.1a):"


@dataclass
class Eq:
    lines: List[str]  # display equation, one aligned row per entry ("lhs &= rhs")


@dataclass
class Tbl:
    header: List[str]
    rows: List[List[str]]
    rules_after: Sequence[int] = ()  # insert \midrule after these row indices
    spec: str = "lr"


@dataclass
class Bullets:
    items: List[str]


Item = Union[Text, Head, Eq, Tbl, Bullets]


@dataclass
class Sec:
    title: str
    items: List[Item] = field(default_factory=list)


@dataclass
class Content:
    title: str
    date: str
    columns: List[Sec]  # flow in two columns
    summary: Sec  # full width, after a rule


# --------------------------------------------------------------------------- #
def _f(x: float, d: int = 1) -> str:
    return f"{x:.{d}f}"


def _status(dcr: Optional[float]) -> str:
    return "n/a" if dcr is None else f"{dcr:.3f} ({'OK' if dcr <= 1.0 else 'NOT OK'})"


def _dc(dcr: Optional[float]) -> str:
    return "n/a" if dcr is None else f"{dcr:.3f}, {'OK' if dcr <= 1.0 else 'NOT OK'}"


def _aligned(lhs: str, rhs: Sequence[str]) -> str:
    """lhs &= rhs[0] \\ &= rhs[1] ...   (kept short: columns are ~8.5 cm wide)"""
    rows = [f"{lhs} &= {rhs[0]}"] + [f"&= {r}" for r in rhs[1:]]
    return r" \\ ".join(rows)


# =========================================================================== #
# 3. BUILDER  - AISC knowledge lives here
# =========================================================================== #
class ReportBuilder:
    """Runs every capacity check once and turns the results into `Content`."""

    def __init__(self, member: WideFlangeCapacity):
        self.m = member
        self.s = member.s
        self.lrfd = member.method == "LRFD"

    # ---- symbol for "available strength" ------------------------------------
    def _av(self, sym: str, sub: str) -> str:
        return rf"\phi_{{{sub}}}{sym}" if self.lrfd else rf"{sym}/\Omega_{{{sub}}}"

    # ---- run all checks ---------------------------------------------------
    def run(self, d: Demands) -> dict:
        m = self.m
        R = {}
        R["ten"] = m.tensile_capacity(
            Pr=d.Pr if d.axial == "tension" and d.Pr else None, An=d.An, U=d.U
        )
        R["cmp"] = m.compressive_capacity(
            Pr=d.Pr if d.axial == "compression" and d.Pr else None
        )
        Cb = m.calc_Cb(*d.moments) if d.moments else m.Cb
        R["Cb"] = Cb
        R["fx"] = m.flexural_capacity(
            Mr=d.Mrx or None, axis="x", Cb=Cb, bolt_holes=d.bolt_holes
        )
        R["fy"] = m.flexural_capacity(Mr=d.Mry or None, axis="y")
        R["vx"] = m.shear_capacity(
            Vr=d.Vrx or None,
            axis="x",
            a=d.a,
            stiffener=d.stiffener,
            tension_field=d.tension_field,
            panel=d.panel,
        )
        R["vy"] = m.shear_capacity(Vr=d.Vry or None, axis="y")
        R["h"] = m.combined_forces_torsion_capacity(
            Pr=d.Pr,
            axial=d.axial,
            Mrx=d.Mrx,
            Mry=d.Mry,
            Vrx=d.Vrx,
            Vry=d.Vry,
            Tr=d.Tr,
            Cb=Cb,
            interaction=d.interaction,
            use_H1_3=d.use_H1_3,
            An=d.An,
            U=d.U,
            bolt_holes=d.bolt_holes,
            T_sv=d.T_sv,
            Bimoment=d.Bimoment,
            warping_shear_stress=d.warping_shear_stress,
        )
        return R

    # ---- public ------------------------------------------------------------
    def build(
        self, d: Demands, title: Optional[str] = None, date: Optional[str] = None
    ) -> Content:
        R = self.run(d)
        date = date or datetime.date.today().strftime("%B %d, %Y").replace(" 0", " ")
        return Content(
            title=title
            or f"{escape_latex(self.s.name)} I-Shaped Member Capacity (AISC 360-22)",
            date=date,
            columns=[
                self._parameters(d, R),
                self._classification(d, R),
                self._capacities(d, R),
                self._interaction(d, R),
            ],
            summary=self._summary(d, R),
        )

    # ---- 1 Design Parameters -------------------------------------------------
    def _parameters(self, d: Demands, R) -> Sec:
        m, s = self.m, self.s
        rows = [
            [
                "Section",
                escape_latex(s.name) + (" (rolled)" if m.rolled else " (built-up)"),
            ],
            [r"$d \times b_f$ (mm)", f"{_f(s.d)} $\\times$ {_f(s.bf)}"],
            [r"$t_f \times t_w$ (mm)", f"{_f(s.tf)} $\\times$ {_f(s.tw)}"],
            [r"$h$ (mm), $A_g$ (mm$^2$)", f"{_f(s.h)}, {_f(s.A, 0)}"],
            [r"$F_y$, $F_u$ (MPa)", f"{_f(m.Fy, 0)}, {_f(m.Fu, 0)}"],
            ["Design method", m.method],
            [
                r"$L_x, L_y, L_z$ (m)",
                f"{m.Lx / 1e3:.2f}, {m.Ly / 1e3:.2f}, {m.Lz / 1e3:.2f}",
            ],
            [r"$K_x, K_y, K_z$", f"{m.Kx:g}, {m.Ky:g}, {m.Kz:g}"],
            [r"$L_b$ (m), $C_b$", f"{m.Lb / 1e3:.2f}, {R['Cb']:.2f}"],
        ]
        n0 = len(rows)
        rows += [
            [
                r"Axial Demand, $P_r$ (kN)",
                f"{_f(d.Pr)} ({'C' if d.axial == 'compression' else 'T'})",
            ],
            [r"Moment Demand, $M_{rx}$ (kN-m)", _f(d.Mrx)],
            [r"Moment Demand, $M_{ry}$ (kN-m)", _f(d.Mry)],
            [r"Shear Demand, $V_{rx}$ (kN)", _f(d.Vrx)],
            [r"Shear Demand, $V_{ry}$ (kN)", _f(d.Vry)],
        ]
        if d.Tr:
            rows.append([r"Torsion Demand, $T_r$ (kN-m)", _f(d.Tr)])
        return Sec(
            "Design Parameters",
            [
                Text("Fundamental parameters utilized for capacity calculations:"),
                Tbl(["Parameter", "Value"], rows, rules_after=[n0 - 1]),
            ],
        )

    # ---- 2 Classification -----------------------------------------------------
    def _classification(self, d: Demands, R) -> Sec:
        m, s = self.m, self.s
        c = m.classify_compression()
        fx, fy = R["fx"].classification, R["fy"].classification
        items: List[Item] = []

        cf, cw = c["flange"], c["web"]
        items += [
            Head("Axial Compactness (Table B4.1a):"),
            Text(
                rf"Flange $\lambda=b_f/2t_f={cf['lam']:.2f}$ vs. $\lambda_r={cf['lam_r']:.2f}$ "
                rf"({cf['case']}): \textbf{{{cf['cls'].capitalize()}}}. "
                rf"Web $\lambda=h/t_w={cw['lam']:.2f}$ vs. $\lambda_r=1.49\sqrt{{E/F_y}}={cw['lam_r']:.2f}$: "
                rf"\textbf{{{cw['cls'].capitalize()}}}. "
                + (
                    "Slender element: $P_n=F_nA_e$ (E7)."
                    if c["slender"]
                    else "Hence $P_n=F_nA_g$ (E3/E4)."
                )
            ),
        ]

        ff, fw = fx["flange"], fx["web"]
        info = R["fx"].details
        items += [
            Head("Flexural Compactness (Table B4.1b):"),
            Text(
                rf"Major axis: flange $\lambda={ff['lam']:.2f}$ ($\lambda_p={ff['lam_p']:.2f}$, "
                rf"$\lambda_r={ff['lam_r']:.2f}$) \textbf{{{ff['cls'].capitalize()}}}; web $\lambda={fw['lam']:.2f}$ "
                rf"($\lambda_p={fw['lam_p']:.2f}$, $\lambda_r={fw['lam_r']:.2f}$) \textbf{{{fw['cls'].capitalize()}}}. "
                rf"Governing provision: \textbf{{Sect. {info['section']}}}. "
                rf"Minor axis: flange \textbf{{{fy['flange']['cls'].capitalize()}}} (F6)."
            ),
        ]

        h_tw, rEF = s.h / s.tw, m._rt_EF
        vd = R["vx"].details
        g21a = m.rolled and h_tw <= 2.24 * rEF
        items += [
            Head("Shear (Chapter G):"),
            Text(
                rf"$h/t_w={h_tw:.1f}$ "
                + (
                    rf"$\le 2.24\sqrt{{E/F_y}}={2.24 * rEF:.1f}$: G2.1(a), $\phi_v=1.00$, $C_{{v1}}=1.0$. "
                    if g21a
                    else rf"$k_v={vd['kv']:.2f}$: G2.1(b). "
                )
                + r"Minor axis: G6, $k_v=1.2$, $h/t_w=b_f/2t_f$."
            ),
        ]
        items += [
            Head("Tension (Chapter D):"),
            Text(
                r"Shear-lag factor $U=1.0$ (Table D3.1 Case 1), $A_e=UA_n$; pin-connected (D5) and eyebar (D6) provisions ignored."
            ),
        ]
        return Sec("Classification \\& Logic", items)

    # ---- 3 Governing capacities ---------------------------------------------
    def _capacities(self, d: Demands, R) -> Sec:
        return Sec(
            "Governing Capacities",
            [
                *self._tension(R["ten"]),
                *self._compression(R["cmp"]),
                *self._flexure(R["fx"], "x"),
                *self._flexure(R["fy"], "y"),
                *self._shear(R),
            ],
        )

    def _limit_line(self, res: CheckResult, fmt=".1f") -> str:
        """Compact list of every limit state: name (clause): available."""
        return "; ".join(
            f"{escape_latex(k.split(' (')[0])} {v['available']:{fmt}}"
            for k, v in res.limit_states.items()
        )

    def _tension(self, r: CheckResult) -> List[Item]:
        s, m = self.s, self.m
        y, u = list(r.limit_states.values())
        Ae = u["Ae"]
        eq = Eq(
            [
                _aligned(
                    "P_n",
                    [
                        rf"F_yA_g=({_f(m.Fy, 0)})({_f(s.A, 0)})\times10^{{-3}}",
                        rf"{_f(y['Rn'])}~\text{{kN}}",
                    ],
                ),
                _aligned(
                    "P_n",
                    [
                        rf"F_uA_e=({_f(m.Fu, 0)})({_f(Ae, 0)})\times10^{{-3}}",
                        rf"{_f(u['Rn'])}~\text{{kN}}",
                    ],
                ),
            ]
        )
        return [
            Head(rf"1. Tension (${self._av('P_n', 't')}$)"),
            eq,
            Text(
                rf"Available tensile strength is ${self._av('P_n', 't')}={_f(r.design_capacity)}$ \textbf{{kN}} "
                rf"({escape_latex(r.governing.split(' (')[0].lower())})."
            ),
        ]

    def _compression(self, r: CheckResult) -> List[Item]:
        s, m = self.s, self.m
        g = r.limit_states[r.governing]
        Fe, Fn, Ae = g["Fe"], g["Fn"], g["Ae"]
        if "x-axis" in r.governing:
            Lc, rr, tag = r.details["Lcx"], s.rx, "x"
        elif "y-axis" in r.governing:
            Lc, rr, tag = r.details["Lcy"], s.ry, "y"
        else:
            Lc, rr, tag = r.details["Lcz"], None, "z"
        lines = []
        if math.isinf(Fe):
            lines.append(_aligned("F_n", [rf"F_y={_f(Fn)}~\text{{MPa}}~(L_c=0)"]))
        else:
            if rr:
                lines.append(
                    _aligned(
                        r"F_e",
                        [
                            rf"\frac{{\pi^2E}}{{(L_{{c{tag}}}/r_{tag})^2}}",
                            rf"\frac{{\pi^2({_f(m.E, 0)})}}{{({_f(Lc / rr)})^2}}={_f(Fe)}~\text{{MPa}}",
                        ],
                    )
                )
            else:
                lines.append(rf"F_e = {_f(Fe)}~\text{{MPa (Eq. E4-2)}}")
            br = r"0.658^{F_y/F_e}F_y" if m.Fy / Fe <= 2.25 else r"0.877F_e"
            lines.append(
                _aligned(
                    "F_n",
                    [rf"{br}\;(F_y/F_e={m.Fy / Fe:.2f})", rf"{_f(Fn)}~\text{{MPa}}"],
                )
            )
        lines.append(
            _aligned(
                "P_n",
                [
                    rf"F_nA_{{{'e' if Ae < s.A else 'g'}}}=({_f(Fn)})({_f(Ae, 0)})\times10^{{-3}}",
                    rf"{_f(g['Rn'])}~\text{{kN}}",
                ],
            )
        )
        return [
            Head(rf"2. Compression (${self._av('P_n', 'c')}$)"),
            Eq(lines),
            Text(
                rf"Available axial strength is ${self._av('P_n', 'c')}={_f(r.design_capacity)}$ \textbf{{kN}} "
                rf"({escape_latex(r.governing.lower())}); all: {self._limit_line(r)} kN."
            ),
        ]

    def _flexure(self, r: CheckResult, axis: str) -> List[Item]:
        s, m = self.s, self.m
        info, cls = r.details, r.classification
        g = r.limit_states[r.governing]
        clause = g["clause"]
        Z, S = (s.Zx, s.Sx) if axis == "x" else (s.Zy, s.Sy)
        z, ss = ("Z_x", "S_x") if axis == "x" else ("Z_y", "S_y")
        Mp = info.get("Mp", m.Fy * Z) / 1e6
        Mn = g["Rn"]
        lines: List[str] = []
        pre: Optional[str] = None
        fl = cls["flange"]

        if clause in ("F2.1", "F6.1"):
            lines = [
                _aligned(
                    "M_n",
                    [
                        rf"M_p=F_y{z}=({_f(m.Fy, 0)})({_f(Z, 0)})\times10^{{-6}}",
                        rf"{_f(Mn)}~\text{{kN-m}}",
                    ],
                )
            ]
        elif clause in ("F2.2", "F3.1"):
            Lp, Lr, Lb = info["Lp"], info["Lr"], info["Lb"]
            pre = rf"L_p={_f(Lp, 0)},\;L_r={_f(Lr, 0)},\;L_b={_f(Lb, 0)}~\text{{mm}}"
            if Lb <= Lp:
                lines = [_aligned("M_n", [rf"M_p={_f(Mp)}~\text{{kN-m}}~(L_b\le L_p)"])]
            elif Lb <= Lr:
                lines = [
                    _aligned(
                        "M_n",
                        [
                            rf"C_b\left[M_p-(M_p-0.7F_y{ss})\frac{{L_b-L_p}}{{L_r-L_p}}\right]",
                            rf"{_f(Mn)}~\text{{kN-m}}",
                        ],
                    )
                ]
            else:
                lines = [
                    _aligned(
                        "M_n",
                        [
                            rf"F_{{cr}}{ss}=({_f(info['Fcr'])})({_f(S, 0)})\times10^{{-6}}",
                            rf"{_f(Mn)}~\text{{kN-m}}",
                        ],
                    )
                ]
        elif clause in ("F3.2", "F4.3", "F6.2", "F5.3"):
            lam, lp, lr = fl["lam"], fl["lam_p"], fl["lam_r"]
            if fl["cls"] == "noncompact":
                pre = rf"\lambda={lam:.2f},\;\lambda_p={lp:.2f},\;\lambda_r={lr:.2f}"
                lines = [
                    _aligned(
                        "M_n",
                        [
                            rf"M_p-(M_p-0.7F_y{ss})\frac{{\lambda-\lambda_p}}{{\lambda_r-\lambda_p}}",
                            rf"{_f(Mn)}~\text{{kN-m}}",
                        ],
                    )
                ]
            else:
                lines = [
                    _aligned(
                        "M_n", [rf"\text{{slender-flange LB}}={_f(Mn)}~\text{{kN-m}}"]
                    )
                ]
        else:  # F4 / F5 / F13.1 : report value
            if info.get("Lp"):
                extra = (
                    rf",\;R_{{pc}}={info['Rpc']:.3f}"
                    if "Rpc" in info
                    else rf",\;R_{{pg}}={info['Rpg']:.3f}" if "Rpg" in info else ""
                )
                pre = (
                    rf"L_p={_f(info['Lp'], 0)},\;L_r={_f(info['Lr'], 0)},\;L_b={_f(info['Lb'], 0)}~\text{{mm}}"
                    + extra
                )
            lines = [
                _aligned("M_n", [rf"{_f(Mn)}~\text{{kN-m}}~\text{{(Sect. {clause})}}"])
            ]

        name = "major" if axis == "x" else "minor"
        return [
            Head(
                rf"3{'a' if axis == 'x' else 'b'}. Flexure, {name} axis (${self._av('M_n', 'b')}$)"
            ),
            *([Eq([pre])] if pre else []),
            Eq(lines),
            Text(
                rf"${self._av('M_n', 'b')}={_f(r.design_capacity)}$ \textbf{{kN-m}} ({escape_latex(r.governing.split(' (')[0].lower())}); "
                rf"$M_p={_f(Mp)}$ kN-m."
            ),
        ]

    def _shear(self, R) -> List[Item]:
        s, m = self.s, self.m
        vx, vy = R["vx"], R["vy"]
        gx = vx.limit_states[vx.governing]
        vd = vx.details
        if gx["clause"].startswith("G2.1"):
            Cv = gx.get("Cv1", 1.0)
            eqx = _aligned(
                "V_n",
                [
                    rf"0.6F_yA_wC_{{v1}}=0.6({_f(m.Fy, 0)})({_f(vd['Aw'], 0)})({Cv:.2f})\times10^{{-3}}",
                    rf"{_f(gx['Rn'])}~\text{{kN}}",
                ],
            )
        else:
            eqx = _aligned(
                "V_n", [rf"{_f(gx['Rn'])}~\text{{kN (tension field, {gx['clause']})}}"]
            )
        gy = vy.limit_states[vy.governing]
        eqy = _aligned(
            "V_n",
            [
                rf"2(0.6F_yb_ft_fC_{{v2}})=2(0.6)({_f(m.Fy, 0)})({_f(s.bf)})({_f(s.tf)})({gy['Cv2']:.2f})\times10^{{-3}}",
                rf"{_f(gy['Rn'])}~\text{{kN}}",
            ],
        )
        phi = r"\phi_v" if self.lrfd else r"V_n/\Omega_v"
        txt = [
            Text(
                rf"${phi}V_{{n,x}}={_f(vx.design_capacity)}$ kN"
                + (
                    rf" $\to$ D/C $={vx.dcr:.3f}$ ({'OK' if vx.ok else 'NOT OK'})"
                    if vx.dcr is not None
                    else ""
                )
            ),
            Text(
                rf"${phi}V_{{n,y}}={_f(vy.design_capacity)}$ kN"
                + (
                    rf" $\to$ D/C $={vy.dcr:.3f}$ ({'OK' if vy.ok else 'NOT OK'})"
                    if vy.dcr is not None
                    else ""
                )
            ),
        ]
        out: List[Item] = [
            Head("4. Shear Strength \\& Demand Checks"),
            Eq([eqx, eqy]),
            *txt,
        ]
        stf = vx.classification.get("stiffener_check")
        if stf:

            def tex(s: str) -> str:  # plain text -> LaTeX with real symbols
                return (
                    escape_latex(s)
                    .replace("<=", r"$\le$")
                    .replace(">=", r"$\ge$")
                    .replace("mm4", r"mm$^4$")
                )

            out.append(
                Text(
                    "Stiffeners (G2.4): "
                    + "; ".join(
                        f"{k.replace('G2.4', '')} {tex(v.split(' [')[0])}"
                        for k, v in stf.items()
                        if "b,c" not in k
                    )
                )
            )
        return out

    # ---- 4 Interaction ----------------------------------------------------------
    def _interaction(self, d: Demands, R) -> Sec:
        h: CheckResult = R["h"]
        dt = h.details
        Pr, Pc, Mcx, Mcy = dt["Pr"], dt["Pc"], dt["Mcx"], dt["Mcy"]
        ratio_p = Pr / Pc if Pc else 0.0
        mom_s = r"\frac{M_{rx}}{M_{cx}}+\frac{M_{ry}}{M_{cy}}"
        mom_n = rf"\frac{{{_f(abs(d.Mrx))}}}{{{_f(Mcx)}}}+\frac{{{_f(abs(d.Mry))}}}{{{_f(Mcy)}}}"
        if not Pc:
            note = "No axial force: flexure terms only (Eq. H1-1b with $P_r=0$)."
            sym, num = mom_s, mom_n
        elif ratio_p >= 0.2:
            note = rf"Since $P_r/P_c={ratio_p:.3f}\ge0.2$, Eq. H1-1a applies."
            sym = rf"\frac{{P_r}}{{P_c}}+\frac{{8}}{{9}}\left({mom_s}\right)"
            num = rf"\frac{{{_f(Pr)}}}{{{_f(Pc)}}}+\frac{{8}}{{9}}\left({mom_n}\right)"
        else:
            note = rf"Since $P_r/P_c={ratio_p:.3f}<0.2$, Eq. H1-1b applies."
            sym = rf"\frac{{P_r}}{{2P_c}}+\left({mom_s}\right)"
            num = rf"\frac{{{_f(Pr)}}}{{2({_f(Pc)})}}+\left({mom_n}\right)"
        eq = _aligned(r"\text{Ratio}", [sym, num, rf"{dt['h1']:.3f}\le 1.0"])

        # Running section counter so numbering stays sequential even when a
        # block (H2, or H3.3/H4) is skipped because it doesn't apply to this run.
        n = 1
        items: List[Item] = [
            Text(
                note
                + (
                    " (tension: $C_b$ increased per H1.2.)"
                    if d.axial == "tension" and d.Pr
                    else ""
                )
            ),
            Head(f"{n}. Standard Interaction (H1)"),
            Eq([eq]),
            Text(
                rf"\textbf{{Result: {dt['h1']:.3f} ({'OK' if dt['h1'] <= 1 else 'NOT OK'})}}"
            ),
        ]
        n += 1

        # H2 (stress form) is an ALTERNATIVE to H1 for any shape - AISC 360-22
        # permits it "in lieu of" H1, it is not an additional required check for
        # doubly-symmetric wide-flange members. Only show it when the user
        # actually selected interaction="H2" as the governing method.
        if d.interaction == "H2":
            items += [
                Head(f"{n}. Stress Form (H2)"),
                Text(
                    rf"$f_{{ra}}/F_{{ca}}+f_{{rbw}}/F_{{cbw}}+f_{{rbz}}/F_{{cbz}}$ at worst flange tip: "
                    rf"\textbf{{{dt['h2']:.3f} ({'OK' if dt['h2'] <= 1 else 'NOT OK'})}}"
                ),
            ]
            n += 1

        if dt["h13"] is not None:
            items.append(
                Text(
                    rf"H1.3 (rolled compact, single-axis) permitted: \textbf{{{dt['h13']:.3f}}}"
                    + (" (governs)" if d.use_H1_3 else " (information)")
                )
            )

        # H3.3 (torsion) and H4 (flange rupture at bolt holes) are independent
        # limit states that only exist if the user actually supplied torsion
        # demand / warping data, or bolt-hole data, respectively (see
        # combined_forces_torsion_capacity). Label the block with only the
        # clause(s) that actually produced a ratio, so an H3.3-only run isn't
        # mislabeled as also including an H4 check.
        extra = [
            (k, v) for k, v in h.limit_states.items() if k.startswith(("H3.3", "H4"))
        ]
        if extra:
            has_33 = any(k.startswith("H3.3") for k, _ in extra)
            has_h4 = any(k.startswith("H4") for k, _ in extra)
            clause_label = " \\& ".join(
                filter(None, ["H3.3" if has_33 else "", "H4" if has_h4 else ""])
            )
            if has_33 and has_h4:
                title = "Torsion / Flange Rupture"
            elif has_33:
                title = "Torsion"
            else:
                title = "Flange Rupture at Bolt Holes"
            items.append(Head(f"{n}. {title} ({clause_label})"))
            items.append(
                Bullets(
                    [
                        rf"{escape_latex(k.split(',')[0].split(' (')[0])}: \textbf{{{v['ratio']:.3f}}}"
                        for k, v in extra
                    ]
                )
            )
            n += 1
        for w in h.warnings:
            items.append(Text(rf"\textit{{Note: {escape_latex(w)}}}"))
        return Sec("Interaction Equations (Ch. H)", items)

    # ---- 5 Summary ----------------------------------------------------------------
    def _summary(self, d: Demands, R) -> Sec:
        ten, cmp_, fx, fy, vx, vy, h = (
            R[k] for k in ("ten", "cmp", "fx", "fy", "vx", "vy", "h")
        )
        av = self._av
        b = [
            rf"\textbf{{Governing Axial Capacities (${av('P_n', 't')}$ / ${av('P_n', 'c')}$):}} "
            rf"{_f(ten.design_capacity)} kN (tension) / {_f(cmp_.design_capacity)} kN (compression) "
            + (
                rf"$\to$ D/C = {_status(ten.dcr if d.axial == 'tension' else cmp_.dcr)}"
                if d.Pr
                else ""
            ),
            rf"\textbf{{Governing Flexural Capacities (${av('M_n', 'b')}$):}} "
            rf"$M_{{nx}}$ = {_f(fx.design_capacity)} kN-m (D/C {_dc(fx.dcr)}), "
            rf"$M_{{ny}}$ = {_f(fy.design_capacity)} kN-m (D/C {_dc(fy.dcr)})",
            rf"\textbf{{Shear Checks:}} major axis D/C = {_status(vx.dcr)}, minor axis D/C = {_status(vy.dcr)}",
            rf"\textbf{{Combined Forces (H):}} {escape_latex(h.governing.split(',')[0].split(' (')[0])} $\to$ {h.dcr:.3f} "
            rf"{'PASS' if h.dcr <= 1 else 'FAIL'}",
        ]
        pool = [
            x
            for x in (
                ten.dcr if d.axial == "tension" else cmp_.dcr,
                fx.dcr,
                fy.dcr,
                vx.dcr,
                vy.dcr,
                h.dcr,
            )
            if x is not None
        ]
        worst = max(pool)
        b.append(
            rf"\textbf{{Overall Status:}} maximum D/C = {worst:.3f} $\to$ "
            rf"\textbf{{{'PASS (OK)' if worst <= 1 else 'FAIL (NOT OK)'}}}"
        )
        return Sec("Executive Summary of Results", [Bullets(b)])


# =========================================================================== #
# [4]  RENDERER  (pylatex) - mimics the layout of the DG6 composite-member report
#      A4, 0.5in margins, numbered sections, 2-column multicols body, booktabs left-aligned
#      table, {\small \[ ... \]} equations, hrule, full-width executive summary.
# =========================================================================== #
def _has_sty(name: str) -> bool:
    """True if a LaTeX package file is installed (lets the exporter run on minimal TeX installs)."""
    if not shutil.which("kpsewhich"):
        return False
    return bool(
        subprocess.run(
            ["kpsewhich", name], capture_output=True, text=True
        ).stdout.strip()
    )


class LatexRenderer:
    def __init__(self, base_pt: int = 10, gap_cm: float = 0.30, margin: str = "0.5in"):
        self.pt, self.gap, self.margin = base_pt, gap_cm, margin

    # ---- one item -> LaTeX --------------------------------------------------
    def _emit(self, box, it: Item, nxt: Optional[Item] = None) -> None:
        if isinstance(it, Text):
            box.append(NoEscape(it.latex + r"\par"))
        elif isinstance(it, Head):
            # heading is the last line of the paragraph leading into what follows -> TeX keeps them together
            tail = {Eq: "", Text: r"\newline"}.get(type(nxt), r"\par\nopagebreak")
            box.append(NoEscape(rf"\textbf{{{it.latex}}}{tail}"))
        elif isinstance(it, Eq):
            body = r" \\[3pt] ".join(it.lines)
            # FIX: Changed \[ \begin{aligned} to \begin{align*} so equations can break across columns
            box.append(NoEscape(r"{\small \begin{align*} " + body + r" \end{align*} }"))
        elif isinstance(it, Tbl):  # left-aligned booktabs table
            box.append(NoEscape(r"\par\vspace{0.25cm}\noindent"))
            t = Tabular(it.spec)
            t.append(NoEscape(r"\toprule"))
            t.add_row([NoEscape(h) for h in it.header])
            t.append(NoEscape(r"\midrule"))
            for i, row in enumerate(it.rows):
                t.add_row([NoEscape(c) for c in row])
                if i in it.rules_after:
                    t.append(NoEscape(r"\midrule"))
            t.append(NoEscape(r"\bottomrule"))
            box.append(t)
            # FIX: Add vertical space after the table to prevent the next heading from crashing into it
            box.append(NoEscape(rf"\par\vspace{{{self.gap}cm}}"))
        elif isinstance(it, Bullets):
            lst = Itemize()
            for b in it.items:
                lst.add_item(NoEscape(b))
            box.append(lst)

    def _section(self, container, sec: Sec) -> None:
        s = LxSection(NoEscape(sec.title))
        for i, it in enumerate(sec.items):
            if (
                isinstance(it, Head) and i > 0
            ):  # gap between blocks (like \vspace{0.3cm})
                s.append(NoEscape(rf"\par\vspace{{{self.gap}cm}}"))
            self._emit(s, it, sec.items[i + 1] if i + 1 < len(sec.items) else None)
        container.append(s)

    # ---- Content -> Document --------------------------------------------------
    def document(self, c: Content) -> Document:
        doc = Document(
            documentclass="extarticle",
            document_options=[f"{self.pt}pt"],
            geometry_options={"a4paper": True, "margin": self.margin},
            lmodern=_has_sty("lmodern.sty"),
        )
        doc.packages.append(Package("booktabs"))
        doc.packages.append(Package("amsmath"))
        doc.packages.append(Package("multicol"))
        doc.preamble.append(NoEscape(r"\pagestyle{empty}"))  # no page numbers
        # FIX: Added \raggedcolumns to stop the multicol package from stretching spaces
        doc.preamble.append(
            NoEscape(
                r"\setlength{\parindent}{0pt}\setlength{\parskip}{2pt}"
                r"\clubpenalty=10000 \widowpenalty=10000 \sloppy \raggedbottom \raggedcolumns \allowdisplaybreaks"
            )
        )
        # header
        doc.append(NoEscape(r"\begin{center}"))
        doc.append(NoEscape(r"{\LARGE \textbf{" + c.title + r"}}\\[0.35cm]"))
        doc.append(NoEscape(r"{\normalsize " + c.date + r"}"))
        doc.append(NoEscape(r"\end{center}"))
        doc.append(NoEscape(rf"\vspace{{{self.gap * 4 / 3:.2f}cm}}"))
        # 2-column body
        doc.append(NoEscape(r"\begin{multicols}{2}"))
        for sec in c.columns:
            self._section(doc, sec)
        doc.append(NoEscape(r"\end{multicols}"))
        doc.append(NoEscape(rf"\vspace{{{self.gap}cm}}"))
        doc.append(NoEscape(r"\hrule"))
        doc.append(NoEscape(rf"\vspace{{{self.gap * 4 / 3:.2f}cm}}"))
        self._section(doc, c.summary)
        return doc


# =========================================================================== #
# [5]  ORCHESTRATION   build -> render -> compile -> shrink until exactly ONE page
# =========================================================================== #
class WideFlangeReport:
    #        (font pt, block gap cm)   tried in order until the PDF is one page
    FIT_STEPS = ((10, 0.30), (9, 0.22), (9, 0.12), (8, 0.10), (8, 0.05))

    def __init__(self, member: WideFlangeCapacity):
        self.builder = ReportBuilder(member)

    @staticmethod
    def _pages(log_path: str) -> Optional[int]:
        try:
            with open(log_path, errors="ignore") as fh:
                m = re.search(r"Output written on .*?\((\d+) pages?", fh.read())
            return int(m.group(1)) if m else None
        except OSError:
            return None

    def export(
        self,
        filename: str,
        demands: Optional[Demands] = None,
        title: Optional[str] = None,
        date: Optional[str] = None,
        keep_tex: bool = False,
    ) -> str:
        """Writes <filename>.pdf (and .tex if keep_tex).  Returns the PDF path."""
        base = os.path.splitext(filename)[0]
        content = self.builder.build(demands or Demands(), title, date)
        pages = None
        for pt, gap in self.FIT_STEPS:
            doc = LatexRenderer(pt, gap).document(content)
            doc.generate_pdf(
                base,
                clean=False,
                clean_tex=False,
                compiler="pdflatex",
                compiler_args=["-interaction=nonstopmode"],
            )
            pages = self._pages(base + ".log")
            if pages == 1:
                break
        for ext in (".aux", ".log", ".out", ".fls", ".fdb_latexmk") + (
            () if keep_tex else (".tex",)
        ):
            if os.path.exists(base + ext):
                os.remove(base + ext)
        if pages != 1:
            print(f"WARNING: report is {pages} pages even at the smallest fit step")
        return base + ".pdf"


# =========================================================================== #
# [6]  EXCEL (xlwings) ENTRY POINT
#      Edit INPUT_CELLS to match your sheet.  Blank OPTIONAL cells fall back to the defaults below.
# =========================================================================== #
INPUT_CELLS = {
    "section": "C4",  # steelpy name, e.g. W14X90
    "Fy": "C5",  # MPa
    "Fu": "C6",  # MPa
    "method": "C7",  # LRFD / ASD
    "Lx": "C9",  # m   unbraced length about x (buckling)
    "Ly": "C10",  # m   unbraced length about y (buckling)
    "Lz": "C11",  # m   torsional unbraced length (blank -> = Ly)
    "Kx": "C12",
    "Ky": "C13",
    "Kz": "C14",
    "Lb": "C15",  # m   LTB unbraced length (blank -> = Ly)
    "Cb": "C16",  # LTB factor (blank -> 1.0)
    "axial": "C18",  # C = compression, T = tension
    "Pr": "C19",  # kN
    "Mrx": "C20",  # kN-m
    "Mry": "C21",  # kN-m
    "Vrx": "C22",  # kN
    "Vry": "C23",  # kN
    "Tr": "C24",  # kN-m   (blank -> 0)
    "a": "C26",  # mm  stiffener spacing (blank -> unstiffened)
    "st_b": "C27",  # mm  stiffener plate width (one plate)
    "st_t": "C28",  # mm  stiffener plate thickness
    "tension_field": "C29",  # Y / N
}
REQUIRED = ("section", "Fy", "Fu", "Ly", "Pr", "Mrx", "Mry", "Vrx", "Vry")
SOLUTION_LABEL_COL = "F"  # status message goes here
SOLUTION_START_ROW = 4


def select_save_file(default_name: str = "WideFlange_Capacity_Report") -> Optional[str]:
    """Save-as dialog (tkinter). Falls back to the current folder if no GUI is available."""
    try:
        # Tell Windows this app is DPI-aware to prevent blurriness
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass  # Fails silently on non-Windows OS or older Windows versions

        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=default_name,
            title="Save calculation report",
        )
        root.destroy()
        return path or None
    except Exception:
        import os

        return os.path.join(os.getcwd(), default_name + ".pdf")


def _num(v, default=0.0) -> float:
    return default if v in (None, "") else float(v)


def export_from_values(vals: dict, filepath: str) -> str:
    """Excel-independent core: dict of sheet values -> PDF.  Lengths in m, forces kN, moments kN-m."""
    Ly = _num(vals["Ly"])
    member = WideFlangeCapacity(
        str(vals["section"]).strip(),
        Fy=float(vals["Fy"]),
        Fu=float(vals["Fu"]),
        design_method=str(vals.get("method") or "LRFD").strip().upper(),
        Lx=_num(vals.get("Lx"), Ly) * 1e3,
        Ly=Ly * 1e3,
        Lz=_num(vals.get("Lz"), Ly) * 1e3,
        Kx=_num(vals.get("Kx"), 1.0),
        Ky=_num(vals.get("Ky"), 1.0),
        Kz=_num(vals.get("Kz"), 1.0),
        Lb=_num(vals.get("Lb"), Ly) * 1e3,
        Cb=_num(vals.get("Cb"), 1.0),
    )
    axial = (
        "tension"
        if str(vals.get("axial") or "C").strip().upper().startswith("T")
        else "compression"
    )
    st_b, st_t = vals.get("st_b"), vals.get("st_t")
    demands = Demands(
        Pr=_num(vals["Pr"]),
        axial=axial,
        Mrx=_num(vals["Mrx"]),
        Mry=_num(vals["Mry"]),
        Vrx=_num(vals["Vrx"]),
        Vry=_num(vals["Vry"]),
        Tr=_num(vals.get("Tr")),
        a=(float(vals["a"]) if vals.get("a") else None),
        stiffener=(Stiffener(b=float(st_b), t=float(st_t)) if st_b and st_t else None),
        tension_field=str(vals.get("tension_field") or "N")
        .strip()
        .upper()
        .startswith("Y"),
    )
    return WideFlangeReport(member).export(filepath, demands)


def export_calcs():
    """Excel button macro:  RunPython "from design import general_steel_section_designer_aisc360 as steel; steel.export_calcs()" """
    if xw is None:
        raise RuntimeError("xlwings is not installed (pip install xlwings)")
    book = xw.Book.caller()
    sht = book.sheets.active
    status = sht.range(f"{SOLUTION_LABEL_COL}{SOLUTION_START_ROW}")

    # --- Read inputs
    vals = {k: sht.range(addr).value for k, addr in INPUT_CELLS.items()}
    missing = [k for k in REQUIRED if vals.get(k) is None]
    if missing:
        status.value = "ERROR: Missing inputs for export: " + ", ".join(missing)
        return

    # --- Prompt for save location & file name
    filepath_with_ext = select_save_file(default_name="WideFlange_Capacity_Report")
    if not filepath_with_ext:
        return
    filepath = (
        filepath_with_ext[:-4]
        if filepath_with_ext.lower().endswith(".pdf")
        else filepath_with_ext
    )

    # --- Calculate + compile PDF
    try:
        pdf = export_from_values(vals, filepath)
        status.value = f"Success: Saved to {pdf}"
    except Exception as e:
        status.value = f"PDF Error: {str(e)}"


# =========================================================================== #
if __name__ == "__main__":
    # ---------------------------------------------------------
    # 1. DEFINE THE MEMBER & STABILITY MODIFIERS
    # ---------------------------------------------------------
    # Standalone demo (no Excel needed)
    m = WideFlangeCapacity("W6X12", Fy=248, Fu=323, Lx=3400, Ly=3400, Lb=3400, Cb=1.0)
    print(m.summary())

    print("-" * 60)
    print("Waiting for save location in popup dialog...")

    # Use the existing GUI dialog function already defined in your script
    dest_file = select_save_file(default_name="W6X12_report")

    if dest_file:
        print("Generating PDF...")

        # ---------------------------------------------------------
        # 2. RUN CAPACITY CHECKS & EXPORT REPORT
        # ---------------------------------------------------------
        # BASIC DEMANDS:
        # Pr, Mrx, Mry, Vrx, Vry, Tr : Magnitudes of applied loads.
        # axial                      : "compression" or "tension".
        #
        # ADVANCED DETAILING & SHEAR PARAMETERS:
        # a             : Stiffener spacing (mm). Modifies web shear buckling (kv).
        # stiffener     : Transverse stiffener plate dimensions (Stiffener object). Triggers G2.4 checks.
        # tension_field : True/False. Enables post-buckling shear strength (boosts Vrx).
        # panel         : "interior" or "end". Tension field sets stricter limits on end panels.
        # An, U         : Net area (mm^2) and shear lag factor for tensile rupture (Chapter D).
        # bolt_holes    : Dict for tension-flange rupture (F13.1). e.g., {"Afn": 1800}
        # interaction   : "H1" (default) or "H2" (stress-based interaction form).
        # use_H1_3      : True/False. Permits alternative H1.3 single-axis interaction check.

        out_path = m.export_report(
            dest_file,
            # Basic Demands
            Pr=130,
            axial="compression",
            Mrx=1,
            Mry=1,
            Vrx=1,
            Vry=1,
            Tr=0.005,
            # Advanced Parameters (Examples - adjust or remove as needed)
            #a=1000,  # 1000 mm stiffener spacing
            #stiffener=Stiffener(b=50.0, t=5.0),  # 50x5 mm stiffener plates
            #tension_field=True,  # Set True to utilize tension field action
            # panel="interior",  # "interior" or "end" panel
            # An=2290,  # Net area for tension checks (mm^2)
            # U=1.0,  # Shear lag factor
            # bolt_holes=None,  # e.g., {"Afn": 1800}
            # interaction="H1",  # Override interaction equation ("H1" or "H2")
            # use_H1_3=False,  # Override to force H1.3 check if permitted
        )
        print(f"Export complete: {out_path}")
    else:
        print("Export cancelled by user.")
