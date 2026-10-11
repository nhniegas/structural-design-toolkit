"""Axial load and biaxial bending of a bar layout: every demand at both ends."""

import math
import numpy as np
import pandas as pd
from design.criteria import dcr_targets
from design.concrete.columns.tables import (
    _numeric,
    _section_bending_angle,
)
from design.concrete.columns.layouts import (
    _END_FORCE_INDEX,
    _build_column_section,
)


def _clear_end_force_index() -> None:
    _END_FORCE_INDEX.clear()
    _DEMAND_INDEX.clear()


def _end_force_rows(forces: pd.DataFrame) -> dict:
    entry = _END_FORCE_INDEX.get(id(forces))
    if entry is not None and entry[0] is forces:
        return entry[1]
    stations = pd.to_numeric(forces["Station"], errors="coerce")
    table = forces.copy()
    table["Station"] = stations
    names = table["Combo"].astype(str)
    index: dict = {combo: None for combo in names.unique()}
    valid = table[stations.notna()]
    if not valid.empty:
        grouped = valid["Station"].groupby(names[valid.index], sort=False)
        lowest, highest = grouped.idxmin(), grouped.idxmax()
        # the two end rows of each combination as plain records: they are read some
        # hundreds of thousands of times, and a table row is slow to read from. The
        # rows are taken in two reads, not one per combination: a table is indexed
        # again for every size and bar layout that is tried.
        first = valid.loc[lowest.to_numpy()].to_dict("records")
        last = valid.loc[highest.to_numpy()].to_dict("records")
        for combo, low_row, high_row in zip(lowest.index, first, last):
            index[combo] = (low_row, high_row)
    _END_FORCE_INDEX[id(forces)] = (forces, index)
    return index


def _column_force_at_end(
    forces: pd.DataFrame, combo: str, at_i_end: bool
) -> dict:
    """Select the first/last station for a member and load combination.

    ETABS frame stations are treated as increasing from connectivity I to J.
    The member's table is indexed by combination the first time it is used.
    The record returned is shared: read it, do not change it.
    """
    index = _end_force_rows(forces)
    if str(combo) not in index:
        raise ValueError(f"No column force stations were found for combo {combo!r}.")
    rows = index[str(combo)]
    if rows is None:
        raise ValueError(f"Column force stations for combo {combo!r} are not numeric.")
    return rows[0] if at_i_end else rows[1]


def _evaluate_column_candidate(
    frame_row: pd.Series,
    force_rows: pd.DataFrame,
    bar_layout: list[tuple[float, float, int]],
    dmain: float,
    dties: float,
    cover: float,
    is_smrf: bool,
    stop_at_first_failure: bool = False,
    progress=None,
) -> tuple[bool, list[dict], dict]:
    """Check one bundled longitudinal layout against every combo/end force pair.

    The resultant of M2/M3 is compared with the uniaxial section capacity at
    that moment direction; this radial check is not a full biaxial interaction
    surface.
    Returns:
        Overall pass status, detailed end/combination checks, and steel ratio data.
    """
    member = str(frame_row["UniqueName"])
    n_bars = sum(count for _, _, count in bar_layout)
    engine, section = _build_column_section(
        frame_row,
        n_bars,
        dmain,
        dties,
        cover,
        is_smrf,
        bundle_layout=bar_layout,
    )
    strength_limits = engine.check_reinforcement_limits(n_bars)
    if strength_limits["status"] != "Pass":
        return False, [], strength_limits

    from design.concrete.columns.interaction import surface_for

    surface = surface_for(engine, section, bar_layout) if USE_INTERACTION_SURFACE else None
    if surface is not None:
        return _evaluate_on_surface(member, force_rows, surface, engine, n_bars, dmain,
                                    stop_at_first_failure, progress, strength_limits)

    checks: list[dict] = []
    passed = True
    for combo in sorted(force_rows["Combo"].dropna().astype(str).unique()):
        for endpoint, at_i in (("I", True), ("J", False)):
            if progress is not None:
                progress(member, endpoint, "Flexure and axial (P-M)", combo)
            force = _column_force_at_end(force_rows, combo, at_i)
            axial_kN = _numeric(force["P"], "P", member)
            m2_kNm = _numeric(force["M2"], "M2", member)
            m3_kNm = _numeric(force["M3"], "M3", member)
            moment_demand = math.hypot(m2_kNm, m3_kNm)
            theta = _section_bending_angle(m2_kNm, m3_kNm)
            try:
                nominal, design, _, design_p, phi = engine.solve_moment_capacity(
                    section,
                    axial_load=axial_kN * 1000.0,
                    bending_angle=theta,
                )
            except ValueError as exc:
                checks.append(
                    {
                        "UniqueName": member,
                        "Combo": combo,
                        "End": endpoint,
                        "Pu_kN": axial_kN,
                        "Mu2_kNm": m2_kNm,
                        "Mu3_kNm": m3_kNm,
                        "Mu_resultant_kNm": moment_demand,
                        "Mn_kNm": np.nan,
                        "phi_Mn_kNm": np.nan,
                        "Phi": np.nan,
                        "phi_Pn_max_kN": np.nan,
                        "Axial_Check": f"ERROR: {exc}",
                        "Flexure_Check": f"ERROR: {exc}",
                        "Strength_Check": f"ERROR: {exc}",
                    }
                )
                passed = False
                if stop_at_first_failure:
                    return False, checks, strength_limits
                continue

            moment_capacity_kNm = float(design) / 1e6
            capacity_ratio = (
                moment_demand / moment_capacity_kNm
                if moment_capacity_kNm > 0
                else math.inf
            )
            moment_pass = moment_demand <= moment_capacity_kNm * dcr_targets.limit(
                dcr_targets.COLUMN, dcr_targets.FLEXURE)
            tension_capacity = (
                engine.code.strength.tension_controlled
                * n_bars
                * math.pi
                * dmain**2
                / 4.0
                * engine.fy
                / 1000.0
            )
            axial_pass = (
                axial_kN * 1000.0 <= float(design_p)
                if axial_kN >= 0
                else abs(axial_kN) <= tension_capacity
            )
            checks.append(
                {
                    "UniqueName": member,
                    "Combo": combo,
                    "End": endpoint,
                    "Pu_kN": axial_kN,
                    "Mu2_kNm": m2_kNm,
                    "Mu3_kNm": m3_kNm,
                    "Mu_resultant_kNm": moment_demand,
                    "Mn_kNm": float(nominal) / 1e6,
                    "phi_Mn_kNm": moment_capacity_kNm,
                    "Phi": float(phi),
                    "phi_Pn_max_kN": float(design_p) / 1000.0,
                    "phi_Pn_tension_kN": tension_capacity,
                    "Flexure_Utilization": capacity_ratio,
                    "Axial_Check": "PASS" if axial_pass else "FAIL",
                    "Flexure_Check": "PASS" if moment_pass else "FAIL",
                    "Strength_Check": "PASS" if moment_pass and axial_pass else "FAIL",
                }
            )
            passed = passed and moment_pass and axial_pass
            if stop_at_first_failure and not passed:
                return False, checks, strength_limits
    return passed, checks, strength_limits


USE_INTERACTION_SURFACE = True  # False: the per-demand solver (Mn at Pn = Pu, then phi)


# A moment below this is no moment: what the analysis leaves at a pinned end, about
# 1e-13 kN-m, in a direction that changes from one analysis to the next. Such a
# demand is checked as axial load alone, with its strength read about the section's
# first axis, so that the result is the same at every run.
NO_MOMENT_KNM = 1e-6


def column_demands(member: str, force_rows: pd.DataFrame) -> list[tuple[str, str, float, float, float]]:
    """(combo, end, Pu kN, Mu2 kN-m, Mu3 kN-m) of every combination at both ends."""
    entry = _DEMAND_INDEX.get(id(force_rows))
    if entry is not None and entry[0] is force_rows:
        return entry[1]
    index = _end_force_rows(force_rows)
    out = []
    for combo in sorted(force_rows["Combo"].dropna().astype(str).unique()):
        rows = index.get(combo)
        if rows is None:
            raise ValueError(f"Column force stations for combo {combo!r} are not numeric.")
        for endpoint, row in (("I", rows[0]), ("J", rows[1])):
            out.append((combo, endpoint, _numeric(row["P"], "P", member),
                        _numeric(row["M2"], "M2", member), _numeric(row["M3"], "M3", member)))
    _DEMAND_INDEX[id(force_rows)] = (force_rows, out)
    return out


# Each member's demands, read once from its force table (cleared with the end-force index).
_DEMAND_INDEX: dict[int, tuple[pd.DataFrame, list]] = {}


def _evaluate_on_surface(member, force_rows, surface, engine, n_bars, dmain,
                         stop_at_first_failure, progress, strength_limits):
    """Flexure and axial checks on the layout's interaction surface (ACI design
    surface, phi Pn = Pu). Every demand is looked up at once (one vectorized call).
    While searching for a layout (``stop_at_first_failure``) the convex hull
    vertices of the demands are checked first: they are the demands most likely
    to fail, so a layout that cannot work is rejected before the others are
    looked up. The others are then confirmed, because the phi-scaled surface is
    not strictly convex where phi changes from 0.65 to 0.90."""
    from design.concrete.columns.interaction import hull_vertices

    demands = column_demands(member, force_rows)
    data = np.array([[p, m2, m3] for _, _, p, m2, m3 in demands], dtype=float).reshape(-1, 3)
    # Section moments: Mx = M3, My = -M2 (interaction.demand_moments), in N and N-mm.
    points = np.column_stack([data[:, 0] * 1e3, data[:, 2] * 1e6, -data[:, 1] * 1e6])
    no_moment = np.hypot(data[:, 1], data[:, 2]) < NO_MOMENT_KNM
    points[no_moment, 1:] = 0.0
    tension_capacity = (engine.code.strength.tension_controlled * n_bars * math.pi * dmain**2
                        / 4.0 * engine.fy / 1000.0)
    if progress is not None and demands:
        progress(member, "I and J", "Flexure and axial (P-M)", demands[0][0])
    if stop_at_first_failure and len(demands) > 4:
        hull = hull_vertices(points)
        groups = [hull, np.setdiff1d(np.arange(len(demands)), hull)]
    else:
        groups = [np.arange(len(demands))]
    checks, passed = [], True
    for indices in groups:
        indices = np.asarray(indices, dtype=int)
        if not len(indices):
            continue
        pu, mx, my = points[indices].T
        capacity, phi = surface.capacities(pu, mx, my)
        demand = np.where(no_moment[indices], 0.0,
                          np.hypot(data[indices, 1], data[indices, 2]))
        capacity_kNm = capacity / 1e6
        with np.errstate(divide="ignore", invalid="ignore"):
            utilization = np.where(
                capacity_kNm > 0, demand / capacity_kNm,
                np.where((demand <= 1e-9) & ~np.isnan(phi), 0.0, math.inf))
        moment_pass = utilization <= dcr_targets.limit(dcr_targets.COLUMN, dcr_targets.FLEXURE)
        axial = data[indices, 0]
        axial_pass = np.where(axial >= 0, pu <= surface.p_cap,
                              np.abs(axial) <= tension_capacity)
        for k, index in enumerate(indices):
            combo, endpoint, axial_kN, m2_kNm, m3_kNm = demands[int(index)]
            ok_m, ok_p = bool(moment_pass[k]), bool(axial_pass[k])
            f = float(phi[k])
            checks.append({
                "UniqueName": member, "Combo": combo, "End": endpoint, "Pu_kN": axial_kN,
                "Mu2_kNm": m2_kNm, "Mu3_kNm": m3_kNm, "Mu_resultant_kNm": float(demand[k]),
                "Mn_kNm": float(capacity_kNm[k]) / f if f == f and f > 0 else np.nan,
                "phi_Mn_kNm": float(capacity_kNm[k]), "Phi": f,
                "phi_Pn_max_kN": surface.p_cap / 1000.0, "phi_Pn_tension_kN": tension_capacity,
                "Flexure_Utilization": float(utilization[k]),
                "Axial_Check": "PASS" if ok_p else "FAIL",
                "Flexure_Check": "PASS" if ok_m else "FAIL",
                "Strength_Check": "PASS" if ok_m and ok_p else "FAIL",
            })
        passed = passed and bool(moment_pass.all() and axial_pass.all())
        if stop_at_first_failure and not passed:
            return False, checks, strength_limits
    if not stop_at_first_failure:
        order = {(c, e): i for i, (c, e, *_rest) in enumerate(demands)}
        checks.sort(key=lambda check: order[(check["Combo"], check["End"])])
    return passed, checks, strength_limits


def _strong_column_hull_combos(combos: list[str], columns: list[str], joint: str,
                               force_groups: dict, get_end_info) -> list[str]:
    """The combinations that can give a joint its lowest sum of column strengths.

    At a joint, for one frame direction, the sum of the column nominal strengths
    depends on the combination only through each column's axial load, and each
    column's Mn(P) along a fixed direction is concave (the nominal interaction
    surface is convex). The sum is therefore concave in (P_1, P_2) and its minimum
    over the combinations lies at a vertex of their convex hull: one or two
    columns give 2 or a few combinations instead of all of them. The beam strengths
    and the joint shear do not depend on the combination. Every combination is
    kept when an axial load cannot be read (the loop then reports what is missing).
    """
    from design.concrete.columns.interaction import hull_vertices

    if len(combos) <= 3 or not columns:
        return list(combos)
    points = []
    try:
        ends = {column: get_end_info(column, joint, False)[0] == "I" for column in columns}
        for combo in combos:
            points.append([
                _numeric(_column_force_at_end(force_groups[column], combo, ends[column])["P"],
                         "P", column)
                for column in columns])
    except (KeyError, ValueError, IndexError):
        return list(combos)
    points = np.asarray(points, dtype=float)
    if points.shape[1] == 1:  # one column: the smallest and largest axial load
        keep = {int(np.argmin(points[:, 0])), int(np.argmax(points[:, 0]))}
    else:
        keep = set(int(i) for i in hull_vertices(points))
    return [combo for index, combo in enumerate(combos) if index in keep]
