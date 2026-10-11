"""The questions of the column design: bottom-story cover, vertical bars, levels left out of the capacity checks."""

import numpy as np
import pandas as pd
from design.criteria.code_config import CODE, AciCode
from design.concrete.columns.tables import (
    _story_order_from_stacks,
)


BOTTOM_COVER_MODES = ("none", "enlarge", "bars")


BOTTOM_COVER_QUESTION = {
    "Yes - use {cover:g} mm cover on the bottom-most story": True,
    "No - keep {normal:g} mm cover on every story": False,
}


BOTTOM_COVER_MODE_OPTIONS = {
    "Enlarge the section - add {extra:g} mm on every face, the bars stay where they are": "enlarge",
    "Keep the section size - move the vertical bars inward": "bars",
}


def _apply_bottom_story_cover(
    frame_data: pd.DataFrame,
    connectivity: pd.DataFrame,
    cover: float,
    mode: str,
    code: AciCode = CODE,
) -> tuple[pd.DataFrame, str | None]:
    """Give the columns of the bottom-most story the earth-contact cover.

    ``mode`` is ``"enlarge"`` (each face moves out by the extra cover, so every
    dimension grows by twice that and the bars keep their position) or
    ``"bars"`` (the section is kept and the bars move inward). The bottom-most
    story is read from how the columns stand on each other.

    Returns the frame data and the name of that story (None when nothing changed).
    """
    if mode not in BOTTOM_COVER_MODES:
        raise ValueError(f"bottom_story_cover must be one of {BOTTOM_COVER_MODES}.")
    bottom_cover = code.column_strength.earth_contact_cover
    extra = bottom_cover - cover
    if mode == "none" or extra <= 0:
        return frame_data, None
    is_column = frame_data["DesignType"].astype(str).str.strip().str.casefold().eq("column")
    story_of = dict(zip(
        frame_data.loc[is_column, "UniqueName"].astype(str),
        frame_data.loc[is_column, "Story"].astype(str),
    ))
    order = _story_order_from_stacks(connectivity, story_of)
    if not order:
        return frame_data, None
    frame_data = frame_data.copy()
    rows = is_column & frame_data["Story"].astype(str).eq(order[0])
    if "DesignCover" not in frame_data.columns:
        frame_data["DesignCover"] = np.nan
    frame_data.loc[rows, "DesignCover"] = bottom_cover
    if mode == "enlarge":
        for name in ("Width", "Depth", "Diameter"):
            if name not in frame_data.columns:
                continue
            values = pd.to_numeric(frame_data[name], errors="coerce")
            grow = rows & values.gt(0)
            frame_data[name] = frame_data[name].astype(object)
            frame_data.loc[grow, name] = values[grow] + 2.0 * extra
    return frame_data, order[0]


VERTICAL_BAR_OPTIONS = {
    "Yes - lower levels use at least the bars of the level above": True,
    "No - design every level on its own": False,
}


def ask_column_design_options(
    continuous_vertical_bars: bool | None = None,
    bottom_story_cover: str | None = None,
    normal_cover: float = 40.0,
) -> tuple[bool, str] | None:
    """Ask the column design questions that are not answered yet.

    Returns ``(continuous_vertical_bars, bottom_story_cover)``, or ``None`` when
    a dialog is closed. An automatic resize loop asks once and passes the
    answers to every run.
    """
    from utilities._gui_helpers import select_option

    if bottom_story_cover is None:
        normal = float(normal_cover)
        bottom = CODE.column_strength.earth_contact_cover
        bottom_story_cover = "none"
        if bottom > normal:
            questions = {
                text.format(cover=bottom, normal=normal): value
                for text, value in BOTTOM_COVER_QUESTION.items()
            }
            chosen = select_option(
                "Column Design - Bottom Story Cover",
                f"Use {bottom:g} mm concrete cover for the columns of the bottom-most story?",
                list(questions),
                default_index=1,
            )
            if chosen is None:
                return None
            if questions[chosen]:
                modes = {
                    text.format(extra=bottom - normal): value
                    for text, value in BOTTOM_COVER_MODE_OPTIONS.items()
                }
                chosen = select_option(
                    "Column Design - Bottom Story Cover",
                    f"How should the {bottom:g} mm cover be provided?",
                    list(modes),
                )
                if chosen is None:
                    return None
                bottom_story_cover = modes[chosen]

    if continuous_vertical_bars is None:
        chosen = select_option(
            "Column Design - Vertical Bars",
            "Carry the larger number of vertical bars of an upper level down to the "
            "levels below it?",
            list(VERTICAL_BAR_OPTIONS),
        )
        if chosen is None:
            return None
        continuous_vertical_bars = VERTICAL_BAR_OPTIONS[chosen]
    return continuous_vertical_bars, bottom_story_cover


TOP_LEVEL_OPTIONS = {
    "Yes - check them (ACI 18.7.3, 18.8)": True,
    "No - leave them out; design the topmost columns for the analysis shear": False,
}


FOUNDATION_LEVEL_OPTIONS = {
    "Yes - check them (ACI 18.7.3, 18.7.6, 18.8)": True,
    "No - leave them out; design these columns for the analysis shear": False,
}


def ask_capacity_check_levels(is_smrf: bool, last: dict | None = None
                              ) -> tuple[bool, bool] | None:
    """Ask whether the capacity-design checks are run at the two levels where
    they usually fail for reasons of geometry.

    Returns ``(check_top_level, check_foundation_level)``, or None when a
    dialog is closed. Without seismic design there is nothing to ask.
    """
    if not is_smrf:
        return True, True
    from utilities._gui_helpers import select_option

    last = last or {}
    chosen = select_option(
        "Column Design - Topmost Level",
        "Run the strong column - weak beam (BCC), joint shear and probable-moment shear "
        "(Ve) checks at the topmost level (the joints on top of the columns with no column "
        "above, and those columns)? One column alone must there be 1.2 times stronger than "
        "the beams, and the check often fails.",
        list(TOP_LEVEL_OPTIONS),
        default_index=0 if last.get("check_top_level", True) else 1)
    if chosen is None:
        return None
    top = TOP_LEVEL_OPTIONS[chosen]
    chosen = select_option(
        "Column Design - Foundation Level",
        "Run the BCC, joint shear and probable-moment shear (Ve) checks on the columns of the "
        "bottom-most story (foundation to ground level)? These columns are short, so "
        "Ve = sum of Mpr / clear height becomes very large and a larger section does not "
        "help: the probable moment grows with the section.",
        list(FOUNDATION_LEVEL_OPTIONS),
        default_index=0 if last.get("check_foundation_level", True) else 1)
    if chosen is None:
        return None
    return top, FOUNDATION_LEVEL_OPTIONS[chosen]
