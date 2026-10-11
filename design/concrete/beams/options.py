"""The questions of the beam design: earth cover, deflection limit and when the partitions are built."""

import os
from design.criteria.code_config import CODE
from design.concrete.beams.deflection import LIMIT_DAMAGED, LIMIT_NOT_DAMAGED, DeflectionStages


DEFLECTION_LIMIT_OPTIONS = {
    f"Yes: L/{LIMIT_DAMAGED} after partitions are installed": LIMIT_DAMAGED,
    f"No: L/{LIMIT_NOT_DAMAGED}": LIMIT_NOT_DAMAGED,
}


def _deflection_settings_path() -> str:
    from utilities.user_settings import settings_path as user_settings_path

    return user_settings_path("beam_deflection.json")


def ask_earth_cover_stories(stories: list[str], normal_cover: float,
                            last: list[str] | None = None) -> list[str] | None:
    """Ask which floor levels have beams that need the 75 mm earth-contact cover.

    ``stories`` are the levels that have beams. Returns the chosen levels (an
    empty list for none), or None when a dialog is closed. Nothing is asked
    when the typed cover is already 75 mm or more.
    """
    from utilities._gui_helpers import DualListboxSelector, select_option

    earth = CODE.beam_detailing.earth_contact_cover
    if not stories or float(normal_cover) >= earth:
        return []
    last = [story for story in (last or []) if story in stories]
    remembered = f" (last time: {', '.join(last)})" if last else ""
    chosen = select_option(
        "Beam Design - Cover against Earth",
        f"Do the beams of any floor level need {earth:g} mm cover (cast against or exposed "
        f"to earth, such as footing tie beams and ground beams)? The other beams keep "
        f"{float(normal_cover):g} mm.{remembered}",
        [f"No - {float(normal_cover):g} mm cover on every level",
         f"Yes - choose the levels with {earth:g} mm cover"]
        + ([f"Yes - the same levels as last time: {', '.join(last)}"] if last else []),
        default_index=2 if last else 0)
    if chosen is None:
        return None
    if chosen.startswith("No"):
        return []
    if "same levels" in chosen:
        return last
    picked = DualListboxSelector(f"Levels whose beams get {earth:g} mm cover", stories).show()
    return [story for story in stories if story in set(picked)] or None


def ask_deflection_limit() -> int | None:
    """Ask the long-term deflection limit (ACI Table 24.2.2); remembered for next time."""
    import json

    from utilities._gui_helpers import select_option

    path = _deflection_settings_path()
    try:
        with open(path, encoding="utf-8") as handle:
            last = int(json.load(handle).get("long_limit_divisor", LIMIT_DAMAGED))
    except (OSError, ValueError):
        last = LIMIT_DAMAGED
    chosen = select_option(
        "Beam Design - Deflection",
        "Do the beams support partitions or finishes likely to be damaged by deflection?",
        list(DEFLECTION_LIMIT_OPTIONS),
        default_index=0 if last == LIMIT_DAMAGED else 1,
    )
    if chosen is None:
        return None
    divisor = DEFLECTION_LIMIT_OPTIONS[chosen]
    _save_deflection_settings({"long_limit_divisor": divisor})
    return divisor


def _deflection_settings() -> dict:
    import json

    try:
        with open(_deflection_settings_path(), encoding="utf-8") as handle:
            saved = json.load(handle)
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_deflection_settings(values: dict) -> None:
    import json

    path = _deflection_settings_path()
    saved = {**_deflection_settings(), **values}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(saved, handle)


STAGES_UNKNOWN = "Not known: count all the long-term deflection after them (the safe side)"


STAGES_ENTER = "Enter when they are built"


STAGE_MONTHS = "Months from when the beams first carry their dead load to the partitions"


STAGE_SHARE = "Dead load in place before the partitions (%)"


def ask_deflection_stages(title: str = "Beam Design - Deflection") -> DeflectionStages | None:
    """Ask when the partitions are built, for the after-attachment deflection.

    Returns the stages (inactive when the time is not known, which counts all
    the long-term deflection after the partitions), or None when closed. The
    answer is remembered for next time.
    """
    from utilities._gui_helpers import enter_values, select_option, show_warning

    last = DeflectionStages.from_saved(_deflection_settings().get("stages"))
    chosen = select_option(
        title, "When are the partitions built?\n\nThe deflection that can damage partitions "
        "is the long-term deflection after they are built, plus the live load. The code lets "
        "you leave out the long-term deflection that happens before (ACI Table 24.2.2). With "
        "the time of construction the results also show the deflection at each stage.",
        [STAGES_UNKNOWN, STAGES_ENTER], default_index=1 if last.active else 0)
    if chosen is None:
        return None
    if chosen == STAGES_UNKNOWN:
        _save_deflection_settings({"stages": DeflectionStages().to_saved()})
        return DeflectionStages()
    typed = enter_values(
        title, "The beams start to creep when the forms are removed. The share is the part "
        "of the dead load on the beam before the partitions: self weight, slab and what else "
        "is there by then, not the partitions and what follows them.",
        [STAGE_MONTHS, STAGE_SHARE],
        {STAGE_MONTHS: f"{last.months_before_partitions or 3:g}",
         STAGE_SHARE: f"{(last.dead_share_before if last.active else 0.8) * 100:g}"})
    if typed is None:
        return None
    try:
        months, share = float(typed[STAGE_MONTHS]), float(typed[STAGE_SHARE]) / 100.0
        if months < 0 or not 0 <= share <= 1:
            raise ValueError
    except ValueError:
        show_warning("The months must be 0 or more and the share from 0 to 100 %.", title=title)
        return None
    stages = DeflectionStages(months, share)
    _save_deflection_settings({"stages": stages.to_saved()})
    return stages
