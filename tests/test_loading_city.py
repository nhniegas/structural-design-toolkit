"""The city animation of the progress window (v0.18.1): it must never stop."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utilities import loading_city as lc  # noqa: E402


class Canvas:
    """A stand-in for the Tk canvas: takes every drawing call, schedules nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def city(width=392, height=92):
    return lc.CityAnimation(Canvas(), width, height,
                            {"accent": "#1F4E9A", "select": "#CCE4F7", "line": "#ABADB3",
                             "muted": "#5F6368"})


def test_thousands_of_frames_draw_without_an_error():
    """Every kind is built, changed, taken down and replaced along the way."""
    animation = city()
    seen = set()
    for _ in range(6000):
        animation.frame += 1
        animation.step()
        animation.draw()
        seen |= {b.kind for b in animation.buildings}
    assert len(seen) >= 12              # most kinds came up


@pytest.mark.parametrize("kind", [lc.FRAME, lc.BRACED, lc.SETBACK, lc.TOWER, lc.HOUSE,
                                  lc.WAREHOUSE, lc.DOME, *lc.LANDMARKS])
@pytest.mark.parametrize("built", [0.3, 1.0, 2.5, 5.0, 9.0])
def test_every_kind_draws_while_built_and_while_taken_down(kind, built):
    """While coming down its target is 0 (v0.18.1: the Empire State setbacks failed then,
    and the whole animation stopped)."""
    animation = city()
    b = animation.buildings[0]
    b.kind = kind
    for target in (animation._height(kind), 0):
        b.target, b.built = target, built
        animation._draw_building(b)
        animation._top(b)


def test_an_error_in_a_frame_does_not_stop_the_animation(monkeypatch):
    animation = city()
    scheduled = []
    animation.canvas = type("C", (), {"after": lambda self, ms, f: scheduled.append(f),
                                      "delete": lambda self, *a: None})()
    monkeypatch.setattr(animation, "draw", lambda: 1 / 0)
    animation._tick()
    assert scheduled                    # the next frame is still asked for
