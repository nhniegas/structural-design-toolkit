"""
Log-Spiral Passive Earth Pressure (c-phi soil, wall adhesion)
-------------------------------------------------------------
CE 264 Geotechnical Engineering, Activity Guide 3
Formulas follow Lecture 4 (Terzaghi, Peck & Mesri, Art. 32):

    P_p = P_PI (frictional part) + P_PII (cohesion + adhesion part)

The critical wedge angle theta1 is the value that MINIMISES the total P_p
and is found by successive parabolic interpolation (Lecture slides 8-9).

Pure Python (standard library only). Run:   python logspiral_passive.py
"""

import math
import sys

# ------------------------- numerical settings --------------------------
TOL_THETA = 1e-6   # stop when |theta3 - theta1| <= this (deg)
TOL_F = 1e-8       # ... and |f3 - f1| <= this (kN/m)
MAX_ITER = 200
SIMPSON_N = 2000   # even number of intervals for A1 and Q1
# -----------------------------------------------------------------------


# ============================== THEORY =================================
def simpson(f, a, b, n=SIMPSON_N):
    """Composite Simpson's rule (n must be even)."""
    h = (b - a) / n
    s = f(a) + f(b)
    for i in range(1, n):
        s += f(a + i * h) * (4 if i % 2 else 2)
    return s * h / 3


def passive_force(theta1_deg, H, gamma, phi_deg, delta_deg, c, ca):
    """Return dict with P_p and every intermediate value for a trial theta1."""
    phi = math.radians(phi_deg)
    delta = math.radians(delta_deg)
    th1 = math.radians(theta1_deg)
    tan_phi = math.tan(phi)

    phi_A = math.pi / 4 - phi / 2
    phi_P = math.pi / 4 + phi / 2
    eta1 = math.pi - phi_P
    eta2 = phi_P - th1
    Kp = math.tan(phi_P) ** 2          # Rankine passive coefficient

    # ---- lengths
    r0 = math.sin(eta1) / math.sin(th1) * H
    r1 = r0 * math.exp(th1 * tan_phi)
    r2 = math.sin(eta2) / math.sin(th1) * H
    r3 = r1 - r2
    Hd, Ld = r3 * math.sin(phi_A), r3 * math.cos(phi_A)
    H1, L1 = r2 * math.sin(phi_A), r2 * math.cos(phi_A)

    # ---- areas (spiral sector by numerical integration)
    A1 = simpson(lambda t: 0.5 * (r0 * math.exp(t * tan_phi)) ** 2, 0.0, th1)
    A2 = 0.5 * L1 * H
    A3 = 0.5 * Ld * Hd

    # ---- moment integral and centroids
    Q1 = simpson(
        lambda t: (r0 * math.exp(t * tan_phi)) ** 3 * math.sin(eta2 + t) / 3.0,
        0.0, th1,
    )
    x1, x2, x3 = Q1 / A1, 2.0 * L1 / 3.0, L1 + 2.0 * Ld / 3.0

    # ---- frictional passive force P_PI
    l1 = math.cos(delta) * (2.0 * H / 3.0 + H1) - math.sin(delta) * L1
    l2 = (A1 * x1 + A3 * x3 - A2 * x2) / (A1 + A3 - A2)
    l3 = 2.0 * Hd / 3.0 + H1
    PdI = 0.5 * gamma * Kp * Hd ** 2
    W = gamma * (A1 + A3 - A2)
    PPI = (W * l2 + PdI * l3) / l1

    # ---- cohesive passive force P_PII
    PdII = 2.0 * c * Hd * math.sqrt(Kp)
    if tan_phi > 1e-12:
        Mc = c / (2.0 * tan_phi) * (r1 ** 2 - r0 ** 2)
    else:                               # phi = 0 limit: r is constant
        Mc = c * r0 ** 2 * th1
    l1c = math.cos(delta) * (H / 2.0 + H1) - math.sin(delta) * L1
    l3c = Hd / 2.0 + H1
    Ca = H * ca
    l4 = L1
    PPII = (Mc + PdII * l3c - Ca * l4) / l1c

    Pp = PPI + PPII
    z = (H / 3.0 * PPI + H / 2.0 * PPII) / Pp

    return dict(
        theta1=theta1_deg, eta2=math.degrees(eta2),
        phi_A=math.degrees(phi_A), phi_P=math.degrees(phi_P),
        eta1=math.degrees(eta1), Kp_rankine=Kp,
        r0=r0, r1=r1, r2=r2, r3=r3,
        Hd=Hd, Ld=Ld, H1=H1, L1=L1,
        A1=A1, A2=A2, A3=A3, Q1=Q1,
        x1=x1, x2=x2, x3=x3,
        l1=l1, l2=l2, l3=l3, PdI=PdI, W=W, PPI=PPI,
        PdII=PdII, Mc=Mc, l1c=l1c, l3c=l3c, Ca=Ca, l4=l4, PPII=PPII,
        Pp=Pp, z=z, Kp_eff=Pp / (0.5 * gamma * H ** 2),
    )


# ============================ OPTIMISATION =============================
def parabolic_min(t1, t2, t3, f1, f2, f3):
    """Vertex of the parabola through three points (lecture slide 8)."""
    num = (t2 - t1) ** 2 * (f2 - f3) - (t2 - t3) ** 2 * (f2 - f1)
    den = (t2 - t1) * (f2 - f3) - (t2 - t3) * (f2 - f1)
    if den == 0:
        return t2
    return t2 - 0.5 * num / den


def find_bracket(f, lo, hi, n=120):
    """Scan [lo, hi] and return (t1,t2,t3) with f(t2) < f(t1), f(t3)."""
    step = (hi - lo) / n
    ts = [lo + i * step for i in range(n + 1)]
    fs = [f(t) for t in ts]
    k = min(range(len(fs)), key=fs.__getitem__)
    if k == n:
        raise RuntimeError("Minimum lies at the upper edge of the search "
                           "range; check the input data.")
    if k == 0:
        return None, None                # minimum at theta -> 0
    return (ts[k - 1], ts[k], ts[k + 1]), (fs[k - 1], fs[k], fs[k + 1])


def minimise(f, phi_P_deg, log):
    """Successive parabolic interpolation, lecture slides 8-9."""
    lo, hi = 0.01, phi_P_deg - 0.5
    bracket = find_bracket(f, lo, hi)
    if bracket[0] is None:
        # P_p increases monotonically with theta (typical when delta = 0):
        # the log-spiral degenerates into the plane Rankine wedge.
        log.append("Minimum at theta -> 0 (log-spiral degenerates to a "
                   "plane / Rankine wedge).")
        return lo, f(lo), 0
    (t1, t2, t3), (f1, f2, f3) = bracket
    log.append("Initial bracket (from coarse scan):")
    log.append(f"  theta = {t1:.4f}, {t2:.4f}, {t3:.4f} deg")
    log.append(f"  P_p   = {f1:.4f}, {f2:.4f}, {f3:.4f} kN/m")
    log.append("")
    log.append(f"{'it':>3} {'theta1':>10} {'theta2':>10} {'theta3':>10} "
               f"{'theta_min':>11} {'P_min':>13}")
    tm, fm = t2, f2
    for it in range(1, MAX_ITER + 1):
        tm = parabolic_min(t1, t2, t3, f1, f2, f3)
        if not (t1 < tm < t3):          # safety: stay inside bracket
            tm = 0.5 * (t1 + t3)
        fm = f(tm)
        log.append(f"{it:>3} {t1:10.5f} {t2:10.5f} {t3:10.5f} "
                   f"{tm:11.6f} {fm:13.6f}")
        # bracket update rules (lecture slide 9)
        if t2 <= tm:
            if f2 <= fm:
                t3, f3 = tm, fm
            else:
                t1, f1, t2, f2 = t2, f2, tm, fm
        else:
            if fm <= f2:
                t3, f3, t2, f2 = t2, f2, tm, fm
            else:
                t1, f1 = tm, fm
        if abs(t3 - t1) <= TOL_THETA and abs(f3 - f1) <= TOL_F:
            break
    return tm, fm, it


# ============================== REPORTING ==============================
def summary_block(H, gamma, phi, delta, c, ca, r):
    L = []
    bar = "=" * 72
    L.append(bar)

    def row(label, sym, val, unit=""):
        L.append(f"{label:<44}{sym:>6} = {val:>14.5f}{unit}")

    row("Height", "(H)", H, " m")
    row("Unit Weight", "(gamma)", gamma, " kN/m3")
    row("Angle of friction of backfill", "(phi)", phi, " deg")
    row("Angle of wall friction", "(delta)", delta, " deg")
    row("Cohesion of backfill", "(c)", c, " kPa")
    row("Adhesion of wall", "(ca)", ca, " kPa")
    row("Wedge angle", "(theta)", r["theta1"], " deg")
    row("Resultant of passive earth pressure", "(Pp)", r["Pp"], " kN/m")
    row("Height of application above base of wall", "(z)", r["z"], " m")
    row("Passive earth pressure coefficient", "(Kp)", r["Kp_eff"])
    L.append(bar)
    return L


def detail_block(r):
    L = []
    L.append("Intermediate values at the critical angle")
    groups = [
        ("Angles (deg)", [("phi_A", "phi_A"), ("phi_P", "phi_P"),
                          ("eta1", "eta1"), ("eta2", "eta2")]),
        ("Radii (m)", [("r0", "r0"), ("r1", "r1"), ("r2", "r2"), ("r3", "r3")]),
        ("Rankine wedge (m)", [("Hd", "Hd"), ("Ld", "Ld"),
                               ("H1", "H1"), ("L1", "L1")]),
        ("Areas (m2)", [("A1", "A1"), ("A2", "A2"), ("A3", "A3")]),
        ("Moment integral (m3)", [("Q1", "Q1")]),
        ("Centroids (m)", [("x1", "x1"), ("x2", "x2"), ("x3", "x3")]),
        ("FRICTIONAL part: lever arms (m)", [("l1", "l1"), ("l2", "l2"),
                                             ("l3", "l3")]),
        ("FRICTIONAL part: forces (kN/m)", [("PdI", "P_dI"), ("W", "W"),
                                            ("PPI", "P_PI")]),
        ("COHESIVE part: lever arms (m)", [("l1c", "l1"), ("l3c", "l3"),
                                           ("l4", "l4")]),
        ("COHESIVE part: moment / forces", [("Mc", "M_c (kN.m/m)"),
                                            ("PdII", "P_dII (kN/m)"),
                                            ("Ca", "C_a (kN/m)"),
                                            ("PPII", "P_PII (kN/m)")]),
        ("Rankine Kp = tan^2(45+phi/2)", [("Kp_rankine", "Kp")]),
    ]
    for title, items in groups:
        L.append(f"  {title}")
        for key, name in items:
            L.append(f"    {name:<16} = {r[key]:.4f}")
    return L


# ============================= INTERACTIVE =============================
def ask_float(prompt, default, lo=None, hi=None, lo_incl=True, hi_incl=True):
    while True:
        raw = input(f"  {prompt} [{default}]: ").strip()
        if raw == "":
            val = float(default)
        else:
            try:
                val = float(raw.replace(",", "."))
            except ValueError:
                print("    ! Please enter a number.")
                continue
        if lo is not None and (val < lo if lo_incl else val <= lo):
            print(f"    ! Value must be {'>=' if lo_incl else '>'} {lo}.")
            continue
        if hi is not None and (val > hi if hi_incl else val >= hi):
            print(f"    ! Value must be {'<=' if hi_incl else '<'} {hi}.")
            continue
        return val


def ask_yes_no(prompt, default="n"):
    raw = input(f"  {prompt} (y/n) [{default}]: ").strip().lower()
    if raw == "":
        raw = default
    return raw.startswith("y")


def run_once(defaults):
    print("\n  Enter soil / wall data (press ENTER to accept the [default]).\n")
    H = ask_float("Wall height H (m)", defaults["H"], 0.0, None, False)
    gamma = ask_float("Unit weight gamma (kN/m3)", defaults["gamma"], 0.0, None, False)
    phi = ask_float("Friction angle phi' (deg, 0 < phi < 90)",
                    defaults["phi"], 0.0, 90.0, False, False)
    delta = ask_float("Wall friction delta (deg, 0 <= delta <= phi)",
                      defaults["delta"], 0.0, phi)
    c = ask_float("Cohesion c (kPa)", defaults["c"], 0.0)
    ca = ask_float("Wall adhesion ca (kPa)", defaults["ca"], 0.0)
    defaults.update(H=H, gamma=gamma, phi=phi, delta=delta, c=c, ca=ca)

    show_iter = ask_yes_no("Show iteration table?", "n")
    show_detail = ask_yes_no("Show intermediate values?", "y")

    phi_P = 45.0 + phi / 2.0

    def f(theta):
        return passive_force(theta, H, gamma, phi, delta, c, ca)["Pp"]

    log = []
    try:
        tm, fm, iters = minimise(f, phi_P, log)
    except Exception as exc:                       # noqa: BLE001
        print(f"\n  ERROR: {exc}")
        return
    r = passive_force(tm, H, gamma, phi, delta, c, ca)

    out = []
    out.append("")
    if show_iter:
        out += log + [""]
    if iters == 0:
        out.append("NOTE: P_p is smallest as theta -> 0 (typical for delta = 0).")
        out.append("      The critical surface becomes the plane Rankine wedge,")
        out.append("      so the result below is the Rankine passive value.")
    else:
        out.append(f"Converged in {iters} iterations.")
    out += summary_block(H, gamma, phi, delta, c, ca, r)
    out.append(f"  P_PI  (frictional) = {r['PPI']:.5f} kN/m")
    out.append(f"  P_PII (cohesive)   = {r['PPII']:.5f} kN/m")
    # independent check of the numerical integral for A1
    A1_exact = (r["r1"] ** 2 - r["r0"] ** 2) / (4 * math.tan(math.radians(phi)))
    out.append(f"  Check: A1 numerical = {r['A1']:.6f}  | closed form = "
               f"{A1_exact:.6f} m2")
    if show_detail:
        out.append("")
        out += detail_block(r)
    text = "\n".join(out)
    print(text)

    if ask_yes_no("\nSave this result to a text file?", "n"):
        name = input("  File name [logspiral_result.txt]: ").strip() or "logspiral_result.txt"
        try:
            with open(name, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            print(f"  Saved to {name}")
        except OSError as exc:
            print(f"  Could not save file: {exc}")


def main():
    print("=" * 72)
    print("  LOG-SPIRAL PASSIVE EARTH PRESSURE  (c-phi soil, wall adhesion)")
    print("  CE 264 Geotechnical Engineering - Terzaghi, Peck & Mesri Art. 32")
    print("=" * 72)
    defaults = dict(H=6.1, gamma=17.9, phi=36.0, delta=20.0, c=24.0, ca=24.0)
    while True:
        run_once(defaults)
        if not ask_yes_no("\nRun another case?", "n"):
            break
    print("\nDone. Press ENTER to close.")
    try:
        input()
    except EOFError:
        pass


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(0)
