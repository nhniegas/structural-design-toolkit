"""
Rectangular Filled Composite (CFT) Member -- calculation + report helper
========================================================================
AISC Design Guide 6 (2nd Ed.) Sec. 2.5 and AISC 360 Ch. I (LRFD).

No Excel, no xlwings, no PyLaTeX. This module:
  1. does the calculation (SI in, SI out; US-customary inside because the
     AISC equations are unit-specific), and
  2. builds a detailed step-by-step Markdown/LaTeX report string that a
     Quarto document renders straight to PDF.

Typical use from a .qmd cell:

    from helpers.rect_filled_composite import RectangularFilledComposite
    col = RectangularFilledComposite(b_mm=635, h_mm=635, t_mm=12.7, ...)
    col.show()

Known limitations (same as before, kept explicit):
  * Noncompact/slender flexure (Spec. Eq. I3-5b) not implemented -> reported N/A.
  * Internal rebar (Asr) is included in axial strength only, not in flexure.
  * The DG6 bare-steel floor on phi*Pn (p.32) is NOT applied (the old code
    had a comment for it but never implemented it).
  * Axial tension demand is not part of the interaction check.
"""

from __future__ import annotations

import math
from datetime import date

# --- unit conversions (SI -> US customary) --------------------------------
MM_IN = 25.4
MPA_KSI = 0.145038
M_IN = 39.3701
KN_KIP = 0.224809
KNM_KIPIN = 8.8507
MM2_IN2 = 645.16


def kip_to_kN(x):
    return x / KN_KIP


def kipin_to_kNm(x):
    return x / KNM_KIPIN


def _f(x, n=1):
    """Format a number for the report."""
    return f"{x:,.{n}f}"


def _sci(x):
    m, e = f"{x:.3e}".split("e")
    return f"{m}\\times 10^{{{int(e)}}}"


def _status(ratio):
    return "OK" if ratio <= 1.0 else "NOT OK"


class RectangularFilledComposite:
    """Rectangular/square filled composite member (LRFD)."""

    PHI_C, PHI_T, PHI_B, PHI_V = 0.75, 0.90, 0.90, 0.90

    def __init__(
        self,
        b_mm,
        h_mm,
        t_mm,
        fc_mpa,
        fy_mpa,
        Lb_m=0.0,
        Lh_m=0.0,
        Pu_kN=0.0,
        Mb_kNm=0.0,
        Mh_kNm=0.0,
        Vb_kN=0.0,
        Vh_kN=0.0,
        Asr_mm2=0.0,
        Fysr_mpa=414.0,
        ri_mm=0.0,
        shear_span_to_depth=None,
        label="",
    ):
        self.label = label
        self.inputs = dict(
            b=b_mm,
            h=h_mm,
            t=t_mm,
            fc=fc_mpa,
            fy=fy_mpa,
            Lb=Lb_m,
            Lh=Lh_m,
            Pu=Pu_kN,
            Mb=Mb_kNm,
            Mh=Mh_kNm,
            Vb=Vb_kN,
            Vh=Vh_kN,
            Asr=Asr_mm2,
            ri=ri_mm,
        )

        # US customary
        self.b, self.h, self.t = b_mm / MM_IN, h_mm / MM_IN, t_mm / MM_IN
        self.fc, self.Fy = fc_mpa * MPA_KSI, fy_mpa * MPA_KSI
        self.Lb, self.Lh = Lb_m * M_IN, Lh_m * M_IN
        self.ri = ri_mm / MM_IN
        self.Asr, self.Fysr = Asr_mm2 / MM2_IN2, Fysr_mpa * MPA_KSI

        self.Pu = Pu_kN * KN_KIP
        self.Mux = Mb_kNm * KNM_KIPIN  # kip-in, about b-axis bending
        self.Muy = Mh_kNm * KNM_KIPIN
        self.Vux, self.Vuy = Vb_kN * KN_KIP, Vh_kN * KN_KIP
        self.shear_span_to_depth = shear_span_to_depth

        # materials
        self.Es, self.wc = 29000.0, 145.0
        self.Ec = self.wc**1.5 * math.sqrt(self.fc)

        # section properties
        self.Ag = self.b * self.h
        self.bi, self.hi = self.b - 2 * self.t, self.h - 2 * self.t
        self.Ac = self.bi * self.hi - 0.858 * self.ri**2
        self.As = self.Ag - self.Ac
        self.Icx = self.bi * self.hi**3 / 12
        self.Icy = self.hi * self.bi**3 / 12
        self.Isx = self.b * self.h**3 / 12 - self.Icx
        self.Isy = self.h * self.b**3 / 12 - self.Icy

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def axial_classification(self):
        s = math.sqrt(self.Es / self.Fy)
        lam_b, lam_h = self.bi / self.t, self.hi / self.t
        lam = max(lam_b, lam_h)
        p, r, mx = 2.26 * s, 3.00 * s, 5.00 * s
        if lam <= p:
            c = "Compact"
        elif lam <= r:
            c = "Noncompact"
        elif lam <= mx:
            c = "Slender"
        else:
            c = "Not Permitted"
        return dict(
            lam_b=lam_b, lam_h=lam_h, lam=lam, lam_p=p, lam_r=r, lam_max=mx, cls=c
        )

    def flexure_classification(self, axis="b"):
        """Flange (parallel to bending axis) and web limits, DG6 Table 2-5."""
        s = math.sqrt(self.Es / self.Fy)
        if axis == "b":
            lf, lw = self.bi / self.t, self.hi / self.t
        else:
            lf, lw = self.hi / self.t, self.bi / self.t
        fp, fr, fm = 2.26 * s, 3.00 * s, 5.00 * s
        wp, wr, wm = 3.00 * s, 5.70 * s, 5.70 * s
        if lf > fm or lw > wm:
            c = "Not Permitted"
        elif lf <= fp and lw <= wp:
            c = "Compact"
        elif lf <= fr and lw <= wr:
            c = "Noncompact"
        else:
            c = "Slender"
        return dict(lam_f=lf, lam_w=lw, fp=fp, fr=fr, wp=wp, wr=wr, cls=c)

    def seismic_classification(self, Ry=1.3):
        """AISC 341-16 Table D1.1."""
        lam = max(self.bi, self.hi) / self.t
        hd = 1.48 * math.sqrt(self.Es / (Ry * self.Fy))
        md = 2.37 * math.sqrt(self.Es / (Ry * self.Fy))
        c = (
            "Highly Ductile"
            if lam <= hd
            else "Moderately Ductile" if lam <= md else "Not Seismically Compact"
        )
        return dict(Ry=Ry, lam=lam, hd=hd, md=md, cls=c)

    # ------------------------------------------------------------------
    # Axial
    # ------------------------------------------------------------------
    def axial_compression(self):
        d = self.axial_classification()
        d["phiPn"] = None
        if d["cls"] == "Not Permitted":
            return d

        Ecr = self.Es / self.Ec
        conc = self.Ac + self.Asr * Ecr
        Pp = self.Fy * self.As + 0.85 * self.fc * conc
        Py = self.Fy * self.As + 0.70 * self.fc * conc
        lam, lp, lr = d["lam"], d["lam_p"], d["lam_r"]

        if d["cls"] == "Compact":
            Pno = Pp
        elif d["cls"] == "Noncompact":
            Pno = Pp - (Pp - Py) / (lr - lp) ** 2 * (lam - lp) ** 2
        else:
            d["Fcr"] = 9 * self.Es / lam**2
            Pno = d["Fcr"] * self.As + 0.70 * self.fc * conc

        C3 = min(0.45 + 3 * (self.As + self.Asr) / self.Ag, 0.9)
        EIx = self.Es * self.Isx + C3 * self.Ec * self.Icx
        EIy = self.Es * self.Isy + C3 * self.Ec * self.Icy
        Pex = math.pi**2 * EIx / self.Lb**2 if self.Lb > 0 else math.inf
        Pey = math.pi**2 * EIy / self.Lh**2 if self.Lh > 0 else math.inf
        Pe = min(Pex, Pey)

        if math.isinf(Pe):
            Pn, branch = Pno, "no buckling (L = 0)"
        elif Pno / Pe <= 2.25:
            Pn, branch = Pno * 0.658 ** (Pno / Pe), "inelastic"
        else:
            Pn, branch = 0.877 * Pe, "elastic"

        d.update(
            Pp=Pp,
            Py=Py,
            Pno=Pno,
            C3=C3,
            EIx=EIx,
            EIy=EIy,
            Pex=Pex,
            Pey=Pey,
            Pe=Pe,
            Pn=Pn,
            branch=branch,
            Ecr=Ecr,
            phiPn=self.PHI_C * Pn,
        )
        return d

    def axial_tension(self):
        return self.PHI_T * (self.As * self.Fy + self.Asr * self.Fysr)

    # ------------------------------------------------------------------
    # Flexure (plastic stress distribution, DG6 Fig. 2-13)
    # ------------------------------------------------------------------
    def flexure(self, axis="b"):
        d = self.flexure_classification(axis)
        d["phiMn"] = None
        if d["cls"] != "Compact":
            return d

        if axis == "b":
            w, dp, wi, di = self.b, self.h, self.bi, self.hi
        else:
            w, dp, wi, di = self.h, self.b, self.hi, self.bi
        ri = self.ri

        Zs = w * dp**2 / 4 - wi * di**2 / 4
        Zc = wi * di**2 / 4 - 0.429 * ri**2 * di + 0.192 * ri**3
        MD = self.Fy * Zs + 0.85 * self.fc * Zc / 2

        hn_raw = (
            0.85
            * self.fc
            * self.Ac
            / (2 * (0.85 * self.fc * wi + 4 * self.Fy * self.t))
        )
        hn = min(hn_raw, di / 2)
        Zsn = 2 * self.t * hn**2
        Zcn = wi * hn**2
        MB = MD - self.Fy * Zsn - 0.85 * self.fc * Zcn / 2

        d.update(
            w=w,
            dp=dp,
            wi=wi,
            di=di,
            Zs=Zs,
            Zc=Zc,
            MD=MD,
            hn_raw=hn_raw,
            hn=hn,
            Zsn=Zsn,
            Zcn=Zcn,
            MB=MB,
            phiMn=self.PHI_B * MB,
        )
        return d

    # ------------------------------------------------------------------
    # Shear
    # ------------------------------------------------------------------
    def _Kc(self):
        r = self.shear_span_to_depth
        if (
            r is None
            or self.flexure_classification("b")["cls"] != "Compact"
            or r >= 0.7
        ):
            return 1.0
        if r < 0.5:
            return 10.0
        return 10.0 + (r - 0.5) * (1.0 - 10.0) / 0.2

    def shear(self, axis="b"):
        depth = self.h if axis == "b" else self.b
        demand = self.Vux if axis == "b" else self.Vuy
        Av = 2 * depth * self.t
        Kc = self._Kc()
        Vs = 0.6 * self.Fy * Av
        Vc = 0.06 * Kc * self.Ac * math.sqrt(self.fc)
        phiVn = self.PHI_V * (Vs + Vc)
        return dict(
            depth=depth,
            Av=Av,
            Kc=Kc,
            Vs=Vs,
            Vc=Vc,
            phiVn=phiVn,
            Vu=demand,
            ratio=demand / phiVn if phiVn > 0 else 0.0,
        )

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def interaction(self, alpha=1.5):
        ax, fx, fy = self.axial_compression(), self.flexure("b"), self.flexure("h")
        out = dict(ax=ax, fx=fx, fy=fy, alpha=alpha, std=None, alp=None)
        if ax["phiPn"] is None:
            return out

        chi = ax["Pn"] / ax["Pno"]
        Pcc = self.PHI_C * chi * 0.85 * self.fc * self.Ac
        out.update(chi=chi, Pcc=Pcc)
        if fx["phiMn"] is None or fy["phiMn"] is None:
            return out

        rx = abs(self.Mux) / fx["phiMn"]
        ry = abs(self.Muy) / fy["phiMn"]
        den = ax["phiPn"] - Pcc
        axial_term = 0.0 if self.Pu < Pcc else (self.Pu - Pcc) / den
        mom_std = rx + ry
        mom_alp = rx**alpha + ry**alpha
        out.update(
            rx=rx,
            ry=ry,
            axial_term=axial_term,
            above_Pcc=self.Pu >= Pcc,
            std=mom_std if self.Pu < Pcc else axial_term + mom_std,
            alp=mom_alp if self.Pu < Pcc else axial_term + mom_alp ** (1 / alpha),
        )
        return out

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def report(self, heading_level=1):
        """Return the full calculation report as Markdown + LaTeX."""
        i = self.inputs
        H = "#" * heading_level
        H2 = "#" * (heading_level + 1)
        o = []
        a = o.append

        it = self.interaction()
        ax, fb, fh = it["ax"], it["fx"], it["fy"]
        vb, vh = self.shear("b"), self.shear("h")
        se = self.seismic_classification()

        a(
            f"*Date: {date.today():%B %d, %Y}*"
            + (f" — *{self.label}*" if self.label else "")
            + "\n"
        )
        a(
            "*Intermediate quantities are shown in kip / in / ksi (the units of the "
            "AISC equations); results are converted to kN / kN·m.*\n"
        )

        # ---- 1. Inputs ----
        a(f"{H} Design Parameters\n")
        a("| Parameter | Value | Parameter | Value |")
        a("|:----------|--------:|:-----------|--------:|")
        a(f"| $b$ (mm) | {_f(i['b'])} | $P_u$ (kN) | {_f(i['Pu'])} |")
        a(f"| $h$ (mm) | {_f(i['h'])} | $M_{{b}}$ (kN·m) | {_f(i['Mb'])} |")
        a(f"| $t$ (mm) | {_f(i['t'],2)} | $M_{{h}}$ (kN·m) | {_f(i['Mh'])} |")
        a(f"| $f'_c$ (MPa) | {_f(i['fc'])} | $V_{{b}}$ (kN) | {_f(i['Vb'])} |")
        a(f"| $F_y$ (MPa) | {_f(i['fy'])} | $V_{{h}}$ (kN) | {_f(i['Vh'])} |")
        a(f"| $L_b$ (m) | {_f(i['Lb'],2)} | $A_{{sr}}$ (mm²) | {_f(i['Asr'])} |")
        a(f"| $L_h$ (m) | {_f(i['Lh'],2)} | $r_i$ (mm) | {_f(i['ri'])} |\n")

        # ---- 2. Section properties ----
        a(f"{H} Section Properties\n")
        a("$$\\begin{aligned}")
        a(
            f"b_i &= b - 2t = {_f(self.b,3)} - 2({_f(self.t,3)}) = {_f(self.bi,3)}\\ \\text{{in}}\\\\"
        )
        a(
            f"h_i &= h - 2t = {_f(self.h,3)} - 2({_f(self.t,3)}) = {_f(self.hi,3)}\\ \\text{{in}}\\\\"
        )
        a(f"A_g &= bh = {_f(self.Ag,2)}\\ \\text{{in}}^2\\\\")
        a(f"A_c &= b_i h_i - 0.858 r_i^2 = {_f(self.Ac,2)}\\ \\text{{in}}^2\\\\")
        a(f"A_s &= A_g - A_c = {_f(self.As,2)}\\ \\text{{in}}^2\\\\")
        a(
            f"E_c &= w_c^{{1.5}}\\sqrt{{f'_c}} = {self.wc:.0f}^{{1.5}}\\sqrt{{{_f(self.fc,3)}}} = {_f(self.Ec,0)}\\ \\text{{ksi}}"
        )
        a("\\end{aligned}$$\n")
        a("$$\\begin{aligned}")
        a(
            f"I_{{sx}} &= \\tfrac{{bh^3}}{{12}} - I_{{cx}} = {_f(self.Isx,0)}\\ \\text{{in}}^4, &\\quad I_{{cx}} &= \\tfrac{{b_i h_i^3}}{{12}} = {_f(self.Icx,0)}\\ \\text{{in}}^4\\\\"
        )
        a(
            f"I_{{sy}} &= \\tfrac{{hb^3}}{{12}} - I_{{cy}} = {_f(self.Isy,0)}\\ \\text{{in}}^4, &\\quad I_{{cy}} &= \\tfrac{{h_i b_i^3}}{{12}} = {_f(self.Icy,0)}\\ \\text{{in}}^4"
        )
        a("\\end{aligned}$$\n")
        rho = self.As / self.Ag
        rho_sym = "\\ge" if rho >= 0.01 else "<"
        rho_txt = "OK" if rho >= 0.01 else "NOT OK"
        a(
            f"Steel ratio (Spec. Eq. I2-2a): $A_s/A_g = {rho*100:.2f}\\% "
            f"{rho_sym} 1\\%$ $\\rightarrow$ **{rho_txt}**.\n"
        )

        # ---- 3. Classification ----
        a(f"{H} Local Buckling Classification\n")
        a(f"{H2} Axial compression (DG6 Table 2-5)\n")
        if ax["cls"] == "Not Permitted":
            a(
                f"$\\lambda = \\max(b_i/t,\\,h_i/t) = {_f(ax['lam'],1)}$ exceeds "
                f"$\\lambda_{{max}} = 5.00\\sqrt{{E/F_y}} = {_f(ax['lam_max'],1)}$ $\\rightarrow$ "
                "**Not permitted**. Section must be revised.\n"
            )
        else:
            a("$$\\begin{aligned}")
            a(f"\\lambda &= \\max(b_i/t,\\,h_i/t) = {_f(ax['lam'],1)}\\\\")
            a(
                f"\\lambda_p &= 2.26\\sqrt{{E/F_y}} = {_f(ax['lam_p'],1)},\\quad "
                f"\\lambda_r = 3.00\\sqrt{{E/F_y}} = {_f(ax['lam_r'],1)}"
            )
            a(
                f"\\end{{aligned}}$$\n\nSection is **{ax['cls']}** for axial compression.\n"
            )

        a(f"{H2} Flexure (DG6 Table 2-5)\n")
        a(
            "| Axis | Flange $\\lambda_f$ | $\\lambda_{pf}$ / $\\lambda_{rf}$ | Web $\\lambda_w$ | $\\lambda_{pw}$ / $\\lambda_{rw}$ | Class |"
        )
        a("|:---|-------:|-------------:|-------:|-------------:|:--------|")
        for nm, f_ in (("b", fb), ("h", fh)):
            a(
                f"| {nm} | {_f(f_['lam_f'],1)} | {_f(f_['fp'],1)} / {_f(f_['fr'],1)} | "
                f"{_f(f_['lam_w'],1)} | {_f(f_['wp'],1)} / {_f(f_['wr'],1)} | **{f_['cls']}** |"
            )
        a("")

        a(f"{H2} Seismic (AISC 341-16 Table D1.1, $R_y = {se['Ry']}$)\n")
        a(
            f"$\\lambda_{{hd}} = 1.48\\sqrt{{E/(R_yF_y)}} = {_f(se['hd'],1)}$, "
            f"$\\lambda_{{md}} = 2.37\\sqrt{{E/(R_yF_y)}} = {_f(se['md'],1)}$, "
            f"$\\lambda = {_f(se['lam'],1)}$ $\\rightarrow$ **{se['cls']}**.\n"
        )

        # ---- 4. Axial ----
        a(f"{H} Axial Compressive Strength\n")
        if ax["phiPn"] is None:
            a("Not available — slenderness exceeds the permitted maximum.\n")
        else:
            conc = f"A_c + A_{{sr}}\\tfrac{{E_s}}{{E_c}}"
            a("$$\\begin{aligned}")
            a(
                f"P_p &= F_y A_s + 0.85 f'_c\\left({conc}\\right) = {_f(ax['Pp'],0)}\\ \\text{{kips}}\\\\"
            )
            a(
                f"P_y &= F_y A_s + 0.70 f'_c\\left({conc}\\right) = {_f(ax['Py'],0)}\\ \\text{{kips}}"
            )
            a("\\end{aligned}$$\n")
            if ax["cls"] == "Compact":
                a(f"Compact: $P_{{no}} = P_p = {_f(ax['Pno'],0)}$ kips.\n")
            elif ax["cls"] == "Noncompact":
                a("Noncompact (Spec. Eq. I2-7):\n")
                a(
                    f"$$P_{{no}} = P_p - \\frac{{P_p - P_y}}{{(\\lambda_r-\\lambda_p)^2}}(\\lambda-\\lambda_p)^2 = {_f(ax['Pno'],0)}\\ \\text{{kips}}$$\n"
                )
            else:
                a("Slender (Spec. Eq. I2-9):\n")
                a(
                    f"$$F_{{cr}} = \\frac{{9E_s}}{{(b/t)^2}} = {_f(ax['Fcr'],1)}\\ \\text{{ksi}},\\quad "
                    f"P_{{no}} = F_{{cr}}A_s + 0.70 f'_c({conc}) = {_f(ax['Pno'],0)}\\ \\text{{kips}}$$\n"
                )
            a("Effective stiffness (Spec. Eq. I2-12, I2-13):\n")
            a("$$\\begin{aligned}")
            a(
                f"C_3 &= \\min\\left[0.45 + 3\\tfrac{{A_s + A_{{sr}}}}{{A_g}},\\,0.9\\right] = {ax['C3']:.3f}\\\\"
            )
            a(
                f"EI_{{eff,b}} &= E_s I_{{sx}} + C_3 E_c I_{{cx}} = {_sci(ax['EIx'])}\\ \\text{{kip-in}}^2\\\\"
            )
            a(
                f"EI_{{eff,h}} &= E_s I_{{sy}} + C_3 E_c I_{{cy}} = {_sci(ax['EIy'])}\\ \\text{{kip-in}}^2"
            )
            a("\\end{aligned}$$\n")
            pe = lambda v: "\\infty" if math.isinf(v) else _f(v, 0)
            a("$$\\begin{aligned}")
            a(
                f"P_{{e,b}} &= \\frac{{\\pi^2 EI_{{eff,b}}}}{{L_b^2}} = {pe(ax['Pex'])}\\ \\text{{kips}},\\quad "
                f"P_{{e,h}} = \\frac{{\\pi^2 EI_{{eff,h}}}}{{L_h^2}} = {pe(ax['Pey'])}\\ \\text{{kips}}\\\\"
            )
            a(f"P_e &= \\min(P_{{e,b}},P_{{e,h}}) = {pe(ax['Pe'])}\\ \\text{{kips}}")
            a("\\end{aligned}$$\n")
            if not math.isinf(ax["Pe"]):
                r = ax["Pno"] / ax["Pe"]
                if ax["branch"] == "inelastic":
                    a(
                        f"$P_{{no}}/P_e = {r:.3f} \\le 2.25$ $\\rightarrow$ $P_n = P_{{no}}\\left(0.658^{{P_{{no}}/P_e}}\\right) = {_f(ax['Pn'],0)}$ kips.\n"
                    )
                else:
                    a(
                        f"$P_{{no}}/P_e = {r:.3f} > 2.25$ $\\rightarrow$ $P_n = 0.877P_e = {_f(ax['Pn'],0)}$ kips.\n"
                    )
            a(
                f"$$\\phi P_n = 0.75({_f(ax['Pn'],0)}) = {_f(ax['phiPn'],0)}\\ \\text{{kips}} "
                f"= \\mathbf{{{_f(kip_to_kN(ax['phiPn']),0)}\\ kN}}$$\n"
            )

        # ---- 5. Flexure ----
        a(f"{H} Flexural Strength\n")
        for nm, f_ in (("b", fb), ("h", fh)):
            a(f"{H2} About the {nm}-axis bending\n")
            if f_["phiMn"] is None:
                a(
                    f"Section is **{f_['cls']}** for flexure. Noncompact/slender flexure "
                    "(Spec. Eq. I3-5b) is not implemented $\\rightarrow$ **N/A**.\n"
                )
                continue
            a("$$\\begin{aligned}")
            a(
                f"Z_s &= \\tfrac{{wD^2}}{{4}} - \\tfrac{{w_iD_i^2}}{{4}} = {_f(f_['Zs'],1)}\\ \\text{{in}}^3\\\\"
            )
            a(
                f"Z_c &= \\tfrac{{w_iD_i^2}}{{4}} - 0.429r_i^2D_i + 0.192r_i^3 = {_f(f_['Zc'],1)}\\ \\text{{in}}^3\\\\"
            )
            a(
                f"M_D &= F_yZ_s + 0.85f'_c\\tfrac{{Z_c}}{{2}} = {_f(f_['MD'],0)}\\ \\text{{kip-in}}\\\\"
            )
            a(
                f"h_n &= \\frac{{0.85f'_cA_c}}{{2(0.85f'_cw_i + 4F_yt)}} = {_f(f_['hn_raw'],2)}\\ \\text{{in}}"
                + (
                    ""
                    if f_["hn_raw"] <= f_["di"] / 2
                    else f"\\ \\rightarrow\\ {_f(f_['hn'],2)}\\ (\\text{{limited to }}D_i/2)"
                )
                + "\\\\"
            )
            a(
                f"Z_{{sn}} &= 2th_n^2 = {_f(f_['Zsn'],1)}\\ \\text{{in}}^3,\\quad Z_{{cn}} = w_ih_n^2 = {_f(f_['Zcn'],1)}\\ \\text{{in}}^3\\\\"
            )
            a(
                f"M_B &= M_D - F_yZ_{{sn}} - 0.85f'_c\\tfrac{{Z_{{cn}}}}{{2}} = {_f(f_['MB'],0)}\\ \\text{{kip-in}}"
            )
            a("\\end{aligned}$$\n")
            a(
                f"$$\\phi M_n = 0.90({_f(f_['MB'],0)}) = {_f(f_['phiMn'],0)}\\ \\text{{kip-in}} "
                f"= \\mathbf{{{_f(kipin_to_kNm(f_['phiMn']),1)}\\ kN\\cdot m}}$$\n"
            )

        # ---- 6. Shear ----
        a(f"{H} Shear Strength (Spec. Eq. I4-1)\n")
        a("$$V_n = 0.6F_yA_v + 0.06K_cA_c\\sqrt{f'_c}$$\n")
        a(
            "| Axis | $A_v = 2Dt$ (in²) | $K_c$ | $\\phi V_n$ (kN) | $V_u$ (kN) | D/C | Status |"
        )
        a("|:--|--------:|----:|--------:|-------:|-----:|:-----|")
        for nm, v in (("b", vb), ("h", vh)):
            a(
                f"| {nm} | {_f(v['Av'],2)} | {v['Kc']:.1f} | {_f(kip_to_kN(v['phiVn']))} | "
                f"{_f(kip_to_kN(v['Vu']))} | {v['ratio']:.3f} | **{_status(v['ratio'])}** |"
            )
        a("")

        # ---- 7. Interaction ----
        a(f"{H} Combined Axial–Flexure Interaction (DG6 Sec. 2.5.6)\n")
        if ax["phiPn"] is None:
            a("Not available — axial strength not permitted.\n")
        else:
            Pcc = it["Pcc"]
            cmp_sym = "$\\ge$" if self.Pu >= Pcc else "$<$"
            a(
                f"$P_{{cc}} = \\phi_c\\chi\\,0.85f'_cA_c$, with $\\chi = P_n/P_{{no}} = {it['chi']:.3f}$ $\\rightarrow$ "
                f"$P_{{cc}} = {_f(kip_to_kN(Pcc))}$ kN. "
                f"$P_u = {_f(i['Pu'])}$ kN "
                f"{cmp_sym} $P_{{cc}}$.\n"
            )
            if it["std"] is None:
                a(
                    "A flexural direction is not compact $\\rightarrow$ interaction ratio **N/A**.\n"
                )
            else:
                if self.Pu < Pcc:
                    a("Axial term omitted:\n")
                    a(
                        "$$R = \\frac{M_{ux}}{\\phi M_{nx}} + \\frac{M_{uy}}{\\phi M_{ny}},\\qquad "
                        "R_\\alpha = \\left(\\tfrac{M_{ux}}{\\phi M_{nx}}\\right)^{\\alpha} + "
                        "\\left(\\tfrac{M_{uy}}{\\phi M_{ny}}\\right)^{\\alpha}$$\n"
                    )
                else:
                    a("Bilinear axial term applied:\n")
                    a(
                        "$$R = \\frac{P_u-P_{cc}}{\\phi P_n-P_{cc}} + \\frac{M_{ux}}{\\phi M_{nx}} + \\frac{M_{uy}}{\\phi M_{ny}},\\qquad "
                        "R_\\alpha = \\frac{P_u-P_{cc}}{\\phi P_n-P_{cc}} + \\left[\\left(\\tfrac{M_{ux}}{\\phi M_{nx}}\\right)^{\\alpha} + "
                        "\\left(\\tfrac{M_{uy}}{\\phi M_{ny}}\\right)^{\\alpha}\\right]^{1/\\alpha}$$\n"
                    )
                a(
                    f"With $\\alpha = {it['alpha']}$: $M_{{ux}}/\\phi M_{{nx}} = {it['rx']:.3f}$, "
                    f"$M_{{uy}}/\\phi M_{{ny}} = {it['ry']:.3f}$"
                    + (
                        f", axial term $= {it['axial_term']:.3f}$"
                        if self.Pu >= Pcc
                        else ""
                    )
                    + ".\n"
                )
                a(
                    f"$$R = \\mathbf{{{it['std']:.3f}}}\\ ({_status(it['std'])}),\\qquad "
                    f"R_\\alpha = \\mathbf{{{it['alp']:.3f}}}\\ ({_status(it['alp'])})$$\n"
                )

        # ---- 8. Summary ----
        a(f"{H} Summary\n")
        a("| Check | Capacity | Demand | D/C | Status |")
        a("|:-------------------|---------:|---------:|-----:|:-----|")
        if ax["phiPn"] is not None:
            a(
                f"| Axial $\\phi P_n$ (kN) | {_f(kip_to_kN(ax['phiPn']))} | {_f(i['Pu'])} | "
                f"{i['Pu']/kip_to_kN(ax['phiPn']):.3f} | {_status(i['Pu']/kip_to_kN(ax['phiPn']))} |"
            )
        for nm, f_, dem in (("b", fb, i["Mb"]), ("h", fh, i["Mh"])):
            if f_["phiMn"] is not None:
                cap = kipin_to_kNm(f_["phiMn"])
                a(
                    f"| Flexure $\\phi M_n$, {nm} (kN·m) | {_f(cap)} | {_f(dem)} | {dem/cap:.3f} | {_status(dem/cap)} |"
                )
            else:
                a(f"| Flexure $\\phi M_n$, {nm} | N/A | {_f(dem)} | – | – |")
        for nm, v, dem in (("b", vb, i["Vb"]), ("h", vh, i["Vh"])):
            a(
                f"| Shear $\\phi V_n$, {nm} (kN) | {_f(kip_to_kN(v['phiVn']))} | {_f(dem)} | {v['ratio']:.3f} | {_status(v['ratio'])} |"
            )
        if it["std"] is not None:
            a(
                f"| Interaction, standard | – | – | {it['std']:.3f} | **{_status(it['std'])}** |"
            )
            a(
                f"| Interaction, $\\alpha = {it['alpha']}$ | – | – | {it['alp']:.3f} | **{_status(it['alp'])}** |"
            )
        a("")
        return "\n".join(o)

    def show(self, heading_level=1):
        """Render the report inside a Quarto/Jupyter cell."""
        try:
            from IPython.display import Markdown, display

            display(Markdown(self.report(heading_level)))
        except ImportError:
            print(self.report(heading_level))


if __name__ == "__main__":
    # DG6 Example 2.5 sanity check
    col = RectangularFilledComposite(
        b_mm=635,
        h_mm=635,
        t_mm=12.7,
        fc_mpa=41.37,
        fy_mpa=344.74,
        Lb_m=9.144,
        Lh_m=9.144,
        Pu_kN=6672.3,
        Mb_kNm=2440.5,
        Vb_kN=400.3,
    )
    print(col.report())
