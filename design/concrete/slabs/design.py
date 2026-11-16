"""Design of the slab panels of a floor: kind, method, moments, bars and checks.

Kinds:
* ONE WAY: supported on two opposite sides only, or on all four with the long clear span
  over twice the short; bars span the short direction (between the supports). The
  moments are those of its strip (``one_way``): the panels in a row through it across
  its continuous ends. Bars the other way are shrinkage and temperature bars.
* CANTILEVER: supported on one side only, designed as a one-way cantilever.
* TWO WAY: supported on three or four sides with the long clear span at most twice the
  short. By ACI Method 3 (``method3``) when the mean alpha_f of its beams is at least
  the limit given (walls count as stiff supports); else by the Direct Design Method
  (``ddm``) when the limits of NSCP 408.10.2 hold; else by Method 3 and flagged for a
  check in ETABS. A free (unsupported) side is taken as discontinuous and flagged.

Every panel is checked, not resized: thickness (NSCP Tables 407.3.1.1, 408.3.1.2),
flexure and the net tensile strain, spacing, and shear without stirrups. A panel that
fails is flagged with the reason.

Loads: wu = 1.2 (self weight + superimposed dead) + 1.6 live, at least 1.4 dead
(NSCP 203-1, 203-2), the self weight from h and the unit weight of the concrete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from design.concrete.slabs import ddm, method3, one_way, strip
from design.concrete.slabs.panels import Panel, row
from design.criteria.code_config import CODE, NSCP, AciCode

ONE_WAY = "ONE WAY"
TWO_WAY = "TWO WAY"
CANTILEVER = "CANTILEVER"
NOT_DESIGNED = "NOT DESIGNED"
WALL_ALPHA = 99.0      # a wall edge: a stiff support for the alpha_f checks

# the bar locations of a panel, as the schedule lists them
LOCATIONS = (
    ("short", "top", "continuous"), ("short", "top", "discontinuous"), ("short", "bottom", "midspan"),
    ("long", "top", "continuous"), ("long", "top", "discontinuous"), ("long", "bottom", "midspan"),
)


@dataclass(frozen=True)
class SlabOptions:
    """Inputs of the design (the window's values)."""

    fy: float = 414.0
    bar: float = 12.0                 # mm, both ways
    max_spacing: float = 300.0        # mm, office maximum
    alpha_limit: float = 2.0          # Method 3 when alpha_fm >= this, else the DDM
    unit_weight: float = 23.6         # kN/m3 of the concrete
    dead_factor: float = NSCP.load_factors.dead
    live_factor: float = NSCP.load_factors.live
    dead_only_factor: float = NSCP.load_factors.dead_only


@dataclass
class PanelDesign:
    panel: Panel
    kind: str = NOT_DESIGNED
    method: str = ""
    short_dir: str = "X"
    short_clear: float = 0.0          # mm
    long_clear: float = 0.0
    h_min: float = 0.0
    alpha_fm: float = 0.0
    self_weight: float = 0.0          # kPa
    wu: float = 0.0                   # kPa
    moments: dict = field(default_factory=dict)      # (dir, face, where) -> kN-m per m
    bars: dict = field(default_factory=dict)         # (dir, face, where) -> BarResult
    vu: float = 0.0                   # kN per m, largest
    phi_vc: float = 0.0
    flags: list[str] = field(default_factory=list)
    mark: str = ""

    @property
    def passed(self) -> bool:
        return (self.kind != NOT_DESIGNED and all(b.passed for b in self.bars.values())
                and self.vu <= self.phi_vc + 1e-9 and self.panel.h >= self.h_min - 1e-6)

    def spacing(self, key: tuple) -> float:
        b = self.bars.get(key)
        return b.spacing if b else 0.0


def _loads(panel: Panel, options: SlabOptions) -> tuple[float, float, float, float]:
    """(self weight, factored dead, factored live, wu) in kPa; wu at least 1.4 D."""
    sw = options.unit_weight * panel.h / 1000.0
    dead = sw + panel.dead
    wd = options.dead_factor * dead
    wl = options.live_factor * panel.live
    if options.dead_only_factor * dead > wd + wl:
        wd, wl = options.dead_only_factor * dead, 0.0
    return sw, wd, wl, wd + wl


def _effective_depths(h: float, bar: float, code: AciCode) -> tuple[float, float]:
    """d of the outer (short direction) and inner (long direction) layers."""
    cover = code.slab.cover
    return h - cover - bar / 2.0, h - cover - 1.5 * bar


def _edge_alpha(panel: Panel, side: str, panels: list[Panel], code: AciCode) -> float:
    """alpha_f of the beam along a side of the panel (0 for a free side)."""
    s = panel.sides[side]
    if not s.is_supported or s.support is None:
        return 0.0
    if s.support.kind == "wall":
        return WALL_ALPHA
    along_x = side in ("S", "N")
    width = (panel.ly if along_x else panel.lx) / 2.0
    sides = 1
    if s.is_continuous and s.neighbour is not None:
        other = panels[s.neighbour]
        width += (other.ly if along_x else other.lx) / 2.0
        sides = 2
    else:
        width += s.support.width / 2.0
    ratio = ddm.elastic_modulus_ratio(s.support.fc, panel.fc)
    return ddm.alpha_f(s.support.width, s.support.depth, panel.h, sides, width, ratio, 1.0, code)


def _end_kind(panel: Panel, side: str) -> str:
    s = panel.sides[side]
    if s.support is not None and s.support.kind == "wall":
        return one_way.SPANDREL         # a concrete wall built with the slab
    return one_way.SPANDREL if s.is_supported else one_way.UNRESTRAINED


def classify(panel: Panel) -> tuple[str, str]:
    """(kind, short direction "X"/"Y") of a panel from its supported sides and spans."""
    sup = {k: panel.sides[k].is_supported for k in panel.sides}
    count = sum(sup.values())
    cx, cy = panel.clear_x, panel.clear_y
    short = "X" if cx <= cy else "Y"
    if count == 0:
        return NOT_DESIGNED, short
    if count == 1:
        side = next(k for k, v in sup.items() if v)
        return CANTILEVER, ("X" if side in ("W", "E") else "Y")
    if count == 2:
        if sup["W"] and sup["E"]:
            return ONE_WAY, "X"
        if sup["S"] and sup["N"]:
            return ONE_WAY, "Y"
        return NOT_DESIGNED, short       # two adjacent sides: a corner support
    long_, short_ = max(cx, cy), min(cx, cy)
    if long_ > CODE.slab.one_way_aspect * short_:
        return ONE_WAY, short
    return TWO_WAY, short


def _spans_along(panel: Panel, direction: str) -> bool:
    """Whether a panel belongs to a one-way strip along ``direction`` (its axes): a
    one-way panel spanning that way. A two-way panel beyond takes its own negative
    moment at the shared edge (Method 3 or the DDM): the strip stops there, fixed."""
    kind, short = classify(panel)
    return kind == ONE_WAY and short == direction


def _cantilever_beyond(panels: list[Panel], panel: Panel, side: str) -> bool:
    s = panel.sides[side]
    return s.neighbour is not None and classify(panels[s.neighbour])[0] == CANTILEVER


def strip_row(panels: list[Panel], start: int, direction: str) -> list[int]:
    """The panels of the one-way strip through ``start`` along ``direction`` ("X"/"Y"):
    across continuous edges, while the next panel is square to it and spans that way."""
    back, ahead = ("W", "E") if direction == "X" else ("S", "N")
    out = [start]
    seen = {start}
    for side, add in ((back, lambda i: out.insert(0, i)), (ahead, out.append)):
        current = start
        while True:
            s = panels[current].sides[side]
            nxt = s.neighbour if s.is_continuous else None
            if (nxt is None or nxt in seen
                    or abs(panels[nxt].angle - panels[start].angle) > 1e-3
                    or not _spans_along(panels[nxt], direction)):
                break
            seen.add(nxt)
            add(nxt)
            current = nxt
    return out


def _strip_spans(panels: list[Panel], members: list[int], direction: str,
                 options: SlabOptions) -> list[one_way.Span]:
    spans = []
    for i in members:
        p = panels[i]
        _, wd, wl, _ = _loads(p, options)
        sw = options.unit_weight * p.h / 1000.0
        ln = (p.clear_x if direction == "X" else p.clear_y) / 1000.0
        l = (p.lx if direction == "X" else p.ly) / 1000.0
        spans.append(one_way.Span(ln, l, wd, wl, sw + p.dead, p.live, p.h))
    return spans


def _design_one_way(d: PanelDesign, panels: list[Panel], options: SlabOptions, code: AciCode):
    p = d.panel
    direction = d.short_dir
    back, ahead = ("W", "E") if direction == "X" else ("S", "N")
    members = strip_row(panels, p.index, direction)
    position = members.index(p.index)
    spans = _strip_spans(panels, members, direction, options)
    # a strip that stops at a continuous edge (the slab beyond does not span along it)
    # is taken as continuing in a mirror of its end span: a fixed end
    first, last = panels[members[0]], panels[members[-1]]
    if first.sides[back].is_continuous and not _cantilever_beyond(panels, first, back):
        spans.insert(0, spans[0])
        position += 1
    if last.sides[ahead].is_continuous and not _cantilever_beyond(panels, last, ahead):
        spans.append(spans[-1])
    left_end = _end_kind(first, back)
    right_end = _end_kind(last, ahead)
    result = one_way.design_strip(spans, left_end, right_end, code)
    span = result.spans[position]
    d.method = result.method
    if result.reasons:
        d.flags.append("COEFFICIENTS NOT APPLICABLE (" + "; ".join(result.reasons)
                       + "): STRIP ANALYSIS")
    cont = [p.sides[back].is_continuous, p.sides[ahead].is_continuous]
    tops_cont = [m for m, c in zip((span.m_left, span.m_right), cont) if c]
    tops_disc = [m for m, c in zip((span.m_left, span.m_right), cont) if not c]
    if tops_cont:
        d.moments[("short", "top", "continuous")] = max(tops_cont)
    if tops_disc:
        d.moments[("short", "top", "discontinuous")] = max(tops_disc)
    d.moments[("short", "bottom", "midspan")] = span.m_pos
    d.vu = max(span.v_left, span.v_right)
    n_cont = sum(cont)
    support = {0: "simple", 1: "one end", 2: "both ends"}[n_cont]
    d.h_min = strip.one_way_min_thickness(d.short_clear, support, options.fy, code)


def _design_cantilever(d: PanelDesign, options: SlabOptions, code: AciCode):
    p = d.panel
    ln = (p.clear_x if d.short_dir == "X" else p.clear_y) / 1000.0
    _, wd, wl, _ = _loads(p, options)
    sw = options.unit_weight * p.h / 1000.0
    r = one_way.cantilever(one_way.Span(ln, ln, wd, wl, sw + p.dead, p.live, p.h))
    d.method = "cantilever"
    d.moments[("short", "top", "continuous")] = r.m_left
    d.vu = r.v_left
    d.h_min = strip.one_way_min_thickness(ln * 1000.0, "cantilever", options.fy, code)
    d.flags.append("CANTILEVER: TOP BARS TO BE ANCHORED INTO THE BACKSPAN")


def _ddm_or_none(d: PanelDesign, panels: list[Panel], alphas: dict, options: SlabOptions,
                 code: AciCode) -> bool:
    """Design a two-way panel by the DDM when 408.10.2 allows it. False when it does not."""
    p = d.panel
    row_x = row(panels, p.index, "x")
    row_y = row(panels, p.index, "y")
    spans_x = [panels[i].lx / 1000.0 for i in row_x]
    spans_y = [panels[i].ly / 1000.0 for i in row_y]
    lx, ly = p.lx / 1000.0, p.ly / 1000.0
    alpha_x = (alphas["S"] + alphas["N"]) / 2.0     # beams along x
    alpha_y = (alphas["W"] + alphas["E"]) / 2.0
    offset = 0.0
    for members, lo, hi, span in ((row_x, 1, 3, lx), (row_y, 0, 2, ly)):
        for a, b in zip(members, members[1:]):
            ba, bb = panels[a].box, panels[b].box
            if abs(panels[a].angle - panels[b].angle) > 1e-3:
                offset = 1.0                  # panels not square to each other
                continue
            offset = max(offset, max(abs(ba[lo] - bb[lo]), abs(ba[hi] - bb[hi])) / 1000.0 / span)
    sw = options.unit_weight * p.h / 1000.0
    failing = ddm.limits(spans_x, spans_y, lx, ly, sw + p.dead, p.live,
                         alpha_x * ly ** 2, alpha_y * lx ** 2, offset, code)
    if failing:
        d.flags.append(f"BEAMS FLEXIBLE (ALPHA_FM {d.alpha_fm:.2f} UNDER {options.alpha_limit:g}), "
                       "DDM NOT APPLICABLE (" + "; ".join(failing) + "): METHOD 3 USED, "
                       "CHECK IN ETABS")
        return False
    _, _, _, wu = _loads(p, options)
    results = {}
    for direction, l1, l2, ln, ends, alpha1 in (
            ("X", lx, ly, p.clear_x / 1000.0, ("W", "E"), alpha_x),
            ("Y", ly, lx, p.clear_y / 1000.0, ("S", "N"), alpha_y)):
        disc = [k for k in ends if not p.sides[k].is_continuous]
        exterior, beta = "beams", 0.0
        if disc:
            s = p.sides[disc[0]]
            if s.support is not None and s.support.kind == "wall":
                exterior = "restrained"
            elif not s.is_supported:
                exterior = "unrestrained"
            else:
                beta = ddm.beta_t(s.support.width, s.support.depth, p.h, l2 * 1000.0,
                                  ddm.elastic_modulus_ratio(s.support.fc, p.fc), 1.0, code)
        results[direction] = ddm.direction_moments(l1, l2, ln, wu, bool(disc), exterior,
                                                   alpha1, beta, code)
    short, long_ = (results["X"], results["Y"]) if d.short_dir == "X" else \
        (results["Y"], results["X"])
    for key, r in (("short", short), ("long", long_)):
        d.moments[(key, "top", "continuous")] = r.slab_neg_interior
        if r.neg_exterior or r.slab_neg_exterior:
            d.moments[(key, "top", "discontinuous")] = r.slab_neg_exterior
        d.moments[(key, "bottom", "midspan")] = r.slab_positive
    d.method = "Direct Design Method (NSCP 408.10)"
    d.vu = wu * min(lx, ly) / 2.0
    return True


def _design_two_way(d: PanelDesign, panels: list[Panel], options: SlabOptions, code: AciCode):
    p = d.panel
    alphas = {k: _edge_alpha(p, k, panels, code) for k in p.sides}
    d.alpha_fm = sum(alphas.values()) / 4.0
    free = [k for k in p.sides if not p.sides[k].is_supported]
    if free:
        d.flags.append("FREE EDGE " + ",".join(free) + " TAKEN AS DISCONTINUOUS: CHECK IN ETABS")
    beta = max(d.short_clear, d.long_clear) / min(d.short_clear, d.long_clear)
    disc_no_edge_beam = any(not p.sides[k].is_continuous and alphas[k] < code.slab.edge_beam_alpha
                            for k in p.sides)
    exterior = any(not p.sides[k].is_continuous for k in p.sides)
    d.h_min = strip.two_way_min_thickness(d.long_clear, beta, d.alpha_fm, options.fy,
                                          disc_no_edge_beam, exterior, code)
    if d.alpha_fm < options.alpha_limit and not free:
        if _ddm_or_none(d, panels, alphas, options, code):
            return
    # Method 3: long edges run along the long direction and carry the short strips
    if d.short_dir == "X":
        long_edges, short_edges = ("W", "E"), ("S", "N")
    else:
        long_edges, short_edges = ("S", "N"), ("W", "E")
    n_long = sum(p.sides[k].is_continuous for k in long_edges)
    n_short = sum(p.sides[k].is_continuous for k in short_edges)
    case = method3.case_of(n_long, n_short)
    _, wd, wl, _ = _loads(p, options)
    r = method3.panel_moments(d.short_clear / 1000.0, d.long_clear / 1000.0, wd, wl, case)
    d.method = f"ACI Method 3, case {case} (NSCP 408.2.1)"
    if n_long:
        d.moments[("short", "top", "continuous")] = r.ma_neg_cont
    if n_long < 2:
        d.moments[("short", "top", "discontinuous")] = r.ma_neg_disc
    d.moments[("short", "bottom", "midspan")] = r.ma_pos
    if n_short:
        d.moments[("long", "top", "continuous")] = r.mb_neg_cont
    if n_short < 2:
        d.moments[("long", "top", "discontinuous")] = r.mb_neg_disc
    d.moments[("long", "bottom", "midspan")] = r.mb_pos
    d.vu = max(r.va, r.vb)


def design_panel(panel: Panel, panels: list[Panel], options: SlabOptions,
                 code: AciCode = CODE) -> PanelDesign:
    d = PanelDesign(panel)
    d.flags.extend(panel.flags)
    d.kind, d.short_dir = classify(panel)
    cx, cy = panel.clear_x, panel.clear_y
    d.short_clear, d.long_clear = (cx, cy) if d.short_dir == "X" else (cy, cx)
    d.self_weight, _, _, d.wu = _loads(panel, options)
    if panel.dead <= 0 and panel.live <= 0:
        d.flags.append("NO SUPERIMPOSED LOAD IN THE MODEL: SELF WEIGHT ONLY")
    if d.kind == NOT_DESIGNED:
        d.flags.append("NOT SUPPORTED ON OPPOSITE SIDES OR ON THREE SIDES: CHECK IN ETABS")
        return d
    if min(cx, cy) <= 0:
        d.kind = NOT_DESIGNED
        d.flags.append("NO CLEAR SPAN: CHECK THE MODEL")
        return d
    if d.kind == ONE_WAY:
        _design_one_way(d, panels, options, code)
    elif d.kind == CANTILEVER:
        _design_cantilever(d, options, code)
    else:
        _design_two_way(d, panels, options, code)
    _bars(d, options, code)
    return d


def _bars(d: PanelDesign, options: SlabOptions, code: AciCode):
    h = d.panel.h
    d_short, d_long = _effective_depths(h, options.bar, code)
    fc, fy = d.panel.fc, options.fy
    if d.kind == TWO_WAY:
        limit_main = limit_long = strip.two_way_spacing_limit(h, options.max_spacing, code)
        strain = code.slab.two_way_min_strain
    else:
        limit_main = strip.one_way_spacing_limit(h, fy, options.max_spacing, code)
        limit_long = strip.temperature_spacing_limit(h, options.max_spacing, code)
        strain = 0.0
    for key in LOCATIONS:
        direction, face, where = key
        label = f"{direction} {face} {where}"
        if d.kind != TWO_WAY and direction == "long":
            # shrinkage and temperature bars (NSCP 424.4.3.2, 407.7.2.4): at the bottom,
            # and at the top under the main top bars they hold in place
            if face == "bottom" or ("short", face, where) in d.moments:
                d.bars[key] = strip.design_bars(label + " (temperature)", 0.0, d_long, h, fc,
                                                fy, options.bar, limit_long, 0.0, code)
            continue
        if key not in d.moments:
            continue                       # the panel has no such edge
        mu = d.moments[key]
        depth = d_short if direction == "short" else d_long
        limit = limit_main if direction == "short" else limit_long
        d.bars[key] = strip.design_bars(label, mu, depth, h, fc, fy, options.bar, limit,
                                        strain, code)
        if not d.bars[key].passed:
            d.flags.append(f"{label.upper()}: {d.bars[key].note}")
    depth = d_short
    d.phi_vc = strip.phi_vc(depth, fc, code)
    if d.vu > d.phi_vc:
        d.flags.append(f"SHEAR {d.vu:.1f} OVER PHI VC {d.phi_vc:.1f} kN/m: THICKER SLAB")
    if h < d.h_min - 1e-6:
        d.flags.append(f"THICKNESS {h:.0f} UNDER THE MINIMUM {d.h_min:.0f} mm "
                       "(DEFLECTION NOT CALCULATED)")


def design_floor(panels: list[Panel], options: SlabOptions, code: AciCode = CODE
                 ) -> list[PanelDesign]:
    """Design every panel of a list of panels (one or more floors)."""
    return [design_panel(p, panels, options, code) for p in panels]

