"""Target demand / capacity ratios, by member type and check.

The code passes a check at a ratio of 1.0. A target lower than that is a
margin the engineer chooses: with a flexure target of 0.85 on girders, every
girder gets the bars that keep Mu / phi Mn at or below 0.85, and is reported
as failing above it. The strong column - weak beam check is a minimum, so its
target is a ratio at least the 1.2 of the code.

The designers read the targets that are active (``use``); without any, every
target is the code limit and nothing changes.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

from design.code_config import CODE

GIRDER, BEAM, COLUMN = "girder", "beam", "column"
FLEXURE, SHEAR, DEFLECTION = "flexure", "shear", "deflection"
JOINT_SHEAR, STRONG_COLUMN = "joint_shear", "strong_column"

# member type -> its checks, with the words shown to the user
CHECKS = {
    GIRDER: {FLEXURE: "Flexure, Mu / phi Mn", SHEAR: "Shear and torsion, Vu / phi Vn",
             DEFLECTION: "Deflection, deflection / limit"},
    BEAM: {FLEXURE: "Flexure, Mu / phi Mn", SHEAR: "Shear and torsion, Vu / phi Vn",
           DEFLECTION: "Deflection, deflection / limit"},
    COLUMN: {FLEXURE: "Axial load and bending (P-M), Mu / phi Mn",
             SHEAR: "Shear, Vu / phi Vn", JOINT_SHEAR: "Joint shear, Vj / phi Vn",
             STRONG_COLUMN: "Strong column - weak beam, sum Mnc / sum Mnb at least"},
}
TYPE_NAMES = {GIRDER: "Girders (frame into a column)", BEAM: "Beams (carried by other beams)",
              COLUMN: "Columns"}
LOWEST = 0.30   # a target below this is taken as a typing slip


def _short(member: str, check: str) -> str:
    """The words of a check without its formula, in lower case except P-M."""
    words = CHECKS[member][check].split(",")[0]
    return words[0].lower() + words[1:]


def code_limit(check: str) -> float:
    """What the code requires: 1.0, or the strong column ratio."""
    return CODE.column_seismic.strong_column_ratio if check == STRONG_COLUMN else 1.0


@dataclass
class Targets:
    """The targets that differ from the code: {(member type, check): ratio}."""

    values: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, member: str, check: str) -> float:
        return float(self.values.get((member, check), code_limit(check)))

    def set(self, member: str, check: str, ratio: float) -> None:
        """Keep a target; a value at the code limit is dropped."""
        ratio, limit = float(ratio), code_limit(check)
        if check == STRONG_COLUMN:
            if ratio < limit - 1e-9:
                raise ValueError(f"The strong column ratio is at least {limit:g}.")
        elif not LOWEST <= ratio <= 1.0 + 1e-9:
            raise ValueError(f"A target ratio is between {LOWEST:g} and 1.")
        if abs(ratio - limit) < 1e-9:
            self.values.pop((member, check), None)
        else:
            self.values[(member, check)] = ratio

    @property
    def changed(self) -> bool:
        return bool(self.values)

    def lines(self, members: tuple[str, ...] = (GIRDER, BEAM, COLUMN)) -> list[str]:
        """The targets that differ from the code, one text per member type."""
        out = []
        for member in members:
            parts = [f"{_short(member, check)} {ratio:g}"
                     for (kind, check), ratio in sorted(self.values.items()) if kind == member]
            if parts:
                out.append(f"{TYPE_NAMES[member].split(' (')[0]}: " + ", ".join(parts))
        return out

    def to_saved(self) -> dict:
        return {f"{member}.{check}": ratio for (member, check), ratio in self.values.items()}

    @classmethod
    def from_saved(cls, saved: dict | None) -> "Targets":
        targets = cls()
        for key, ratio in (saved or {}).items():
            member, _, check = str(key).partition(".")
            if member in CHECKS and check in CHECKS[member]:
                try:
                    targets.set(member, check, float(ratio))
                except (TypeError, ValueError):
                    continue
        return targets


_ACTIVE = Targets()


def active() -> Targets:
    return _ACTIVE


def limit(member: str, check: str) -> float:
    """The ratio the active targets allow for a check of a member type."""
    return _ACTIVE.get(member, check)


@contextmanager
def use(targets: Targets | None):
    """Design with ``targets`` for the duration of the block."""
    global _ACTIVE
    before, _ACTIVE = _ACTIVE, targets or Targets()
    try:
        yield _ACTIVE
    finally:
        _ACTIVE = before


def beam_type(support_status: object) -> str:
    """Girder or beam, from the support status of the beam design table."""
    from design.beam_designer_aci318 import is_gravity_beam

    return BEAM if is_gravity_beam(support_status) else GIRDER


def note(members: tuple[str, ...]) -> str:
    """One sentence on the active targets of these member types, for a report;
    empty when they are the code limits."""
    lines = _ACTIVE.lines(members)
    if not lines:
        return ""
    return ("Target ratios set by the engineer, stricter than the code limit: "
            + "; ".join(lines) + ". A check passes at or below its target.")
