"""What holds a beam: support status, gravity beams, and the girders the SMRF rules apply to."""

import pandas as pd
from design.criteria.code_config import CODE, AciCode


GRAVITY_BEAM_STATUS = "Beam-Framed / Floating"  # neither end is on a column or wall


def is_gravity_beam(support_status: object) -> bool:
    """True for a beam that frames into other beams only.

    A gravity beam is not part of the moment frame, so the seismic (SMRF)
    provisions are not applied to it even when seismic design is switched on.
    """
    return str(support_status).strip() == GRAVITY_BEAM_STATUS


SMRF_APPLIED = "SMRF rules applied"


SMRF_NOT_APPLIED = "Not applied"


SMRF_GRAVITY = SMRF_NOT_APPLIED + ": gravity beam, not part of the moment frame"


# A clear span under 4d is also under 4h, so such a girder is a deep beam by ACI 9.9.1.1(a).
SMRF_SHORT_SPAN = (SMRF_NOT_APPLIED + ": clear span under 4d (NSCP 418.6.2.1(a); ACI 18.6.2.1(a)); a deep beam by "
                   "NSCP 409.9.1.1 (ACI 9.9.1.1), designed here as an ordinary beam")


def short_span_girder(clear_span: float, depth: float, cover: float, d_stirrup: float,
                      d_main: float, code: AciCode = CODE) -> bool:
    """Whether a girder is shorter than a beam of a special moment frame may be:
    clear span under 4d (NSCP 418.6.2.1(a); ACI 18.6.2.1(a)), d to the centre of one layer of bars."""
    d = depth - cover - d_stirrup - d_main / 2.0
    return clear_span < code.beam_seismic.min_clear_span_to_depth * d


def seismic_rules_of(support_status: object, clear_span: float, depth: float, cover: float,
                     d_stirrup: float, d_main: float, exempt_short_spans: bool,
                     code: AciCode = CODE) -> str:
    """Which seismic treatment a beam gets, as a text for the results.

    A gravity beam is never part of the moment frame. With
    ``exempt_short_spans`` a girder with a clear span under 4d is left out of
    the SMRF rules as well (the engineer's choice: such a member does not
    qualify as a beam of a special moment frame). A cantilever keeps its
    treatment. The text starts with ``SMRF_NOT_APPLIED`` when they do not apply.
    """
    if is_gravity_beam(support_status):
        return SMRF_GRAVITY
    if exempt_short_spans and "Cantilever" not in str(support_status) and short_span_girder(
            clear_span, depth, cover, d_stirrup, d_main, code):
        return SMRF_SHORT_SPAN
    return SMRF_APPLIED


def smrf_applies(row) -> bool:
    """Whether a results row was designed with the SMRF rules (when SMRF design is on)."""
    if is_gravity_beam(row.get("SupportStatus", "")):
        return False
    return not str(row.get("Seismic_Rules", "")).startswith(SMRF_NOT_APPLIED)


def identify_cantilever_beams(
    frame_df: pd.DataFrame,
    conn_df: pd.DataFrame,
    points: pd.DataFrame | None = None,
    pushes: dict | None = None,
) -> pd.DataFrame:
    """Classify the supports of every beam.

    An end is on a support when a column or a wall is at its joint. With
    ``points`` (the joint coordinates) an end that another beam carries is
    supported too: a beam across it at the joint, or a girder whose centre
    line passes through the joint and that ETABS has not split there. A beam
    that only continues in line does not hold an end by itself: the end is as
    held as the far end of that beam (``carriers.end_conditions``).

    * on a column or wall at one end at least, and held at the other:
      ``Supported Both Ends`` (a frame member, with the seismic rules)
    * on a column or wall at one end and free at the other:
      ``Cantilever (Free at PtI / PtJ)``
    * no column or wall at either end: a gravity beam

    Without ``points`` an end with no column or wall counts as free, as it
    did before the coordinates were used.

    `frame_df` remains in the signature for compatibility with existing callers.
    """
    del frame_df

    conn = conn_df.copy()
    conn.columns = [str(column).strip() for column in conn.columns]

    required = {"DesignType", "UniqueName", "UniquePtI", "UniquePtJ"}
    missing = required.difference(conn.columns)
    if missing:
        raise ValueError(
            "Connectivity data is missing required columns: "
            + ", ".join(sorted(missing))
        )

    support_rows = conn.loc[conn["DesignType"].isin(["Column", "Wall"])]
    point_columns = [
        column
        for column in (
            "UniquePtI",
            "UniquePtJ",
            "UniquePt1",
            "UniquePt2",
            "UniquePt3",
            "UniquePt4",
        )
        if column in support_rows.columns
    ]
    support_joints = {
        point
        for point in support_rows[point_columns].to_numpy().ravel()
        if pd.notna(point)
    }

    from design.concrete.beams.carriers import BEAM_END, _name, end_conditions

    beams = conn.loc[conn["DesignType"].eq("Beam")].copy()
    has_support_i = beams["UniquePtI"].isin(support_joints)
    has_support_j = beams["UniquePtJ"].isin(support_joints)
    held = end_conditions(conn, points, pushes)
    held_i = pd.Series([held.get((_name(n), _name(j))) == BEAM_END for n, j in
                        zip(beams["UniqueName"], beams["UniquePtI"])], index=beams.index)
    held_j = pd.Series([held.get((_name(n), _name(j))) == BEAM_END for n, j in
                        zip(beams["UniqueName"], beams["UniquePtJ"])], index=beams.index)
    free_i, free_j = ~has_support_i & ~held_i, ~has_support_j & ~held_j

    beams["SupportStatus"] = GRAVITY_BEAM_STATUS
    # on a column or wall at one end at least, and held at the other: a frame member
    beams.loc[(has_support_i | has_support_j) & ~free_i & ~free_j,
              "SupportStatus"] = "Supported Both Ends"
    beams.loc[has_support_i & free_j, "SupportStatus"] = "Cantilever (Free at PtJ)"
    beams.loc[free_i & has_support_j, "SupportStatus"] = "Cantilever (Free at PtI)"

    return beams[["UniqueName", "SupportStatus"]].reset_index(drop=True)
