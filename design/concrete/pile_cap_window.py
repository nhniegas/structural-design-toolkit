"""The window of ``sdt pilecap`` (the shared ``utilities/design_window.py``): every
input in one form, a plan of the cap and its piles that redraws as you type,
and the results beside them. What the plan and the results panel show is in
``design/concrete/pile_cap.py`` (``preview``, ``result_parts``)."""

from __future__ import annotations

from utilities.design_window import DesignWindow, open_window as _open

NAME = "pile_cap"
TITLE = "Pile Cap Design (NSCP 2015)"
REPORT = "Pile_Cap_Report"


class PileCapWindow(DesignWindow):
    def __init__(self, module):
        super().__init__(module, NAME, TITLE, REPORT)


def open_window(module):
    """Open the window; returns the last design (or None when nothing was run)."""
    return _open(module, NAME, TITLE, REPORT, "pilecap")
