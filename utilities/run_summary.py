"""The short summary every command prints when it finishes.

One layout for all commands: what was run and on which model, the counts,
what failed, and the files written. The detail stays in the result files
and logs. The terminal shows the detailed results of a command; its summary
opens in a separate window that stays until it is closed.
"""

from __future__ import annotations

import os

WIDTH = 72
MAX_LISTED = 12


class RunSummary:
    """Collects the lines of a command's summary, then prints or saves them."""

    def __init__(self, command: str, model: str | None = None):
        self.command = command
        self.model = model
        self.items: list[tuple[str, str]] = []
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.files: list[tuple[str, str]] = []

    def add(self, label: str, value) -> "RunSummary":
        self.items.append((str(label), str(value)))
        return self

    def fail(self, text: str) -> "RunSummary":
        self.failures.append(str(text))
        return self

    def note(self, text: str) -> "RunSummary":
        self.notes.append(str(text))
        return self

    def file(self, label: str, path) -> "RunSummary":
        if path:
            self.files.append((str(label), str(path)))
        return self

    @property
    def passed(self) -> bool:
        return not self.failures

    def text(self) -> str:
        lines = ["", "=" * WIDTH, f"SUMMARY - {self.command}", "=" * WIDTH]
        if self.model:
            lines.append(f"Model: {os.path.basename(str(self.model))}")
        width = max((len(label) for label, _ in self.items), default=0)
        lines += [f"{label:<{width}}  {value}" for label, value in self.items]
        if self.failures:
            lines += ["", f"Needs attention ({len(self.failures)}):"]
            lines += [f"  - {text}" for text in self.failures[:MAX_LISTED]]
            if len(self.failures) > MAX_LISTED:
                lines.append(f"  ... and {len(self.failures) - MAX_LISTED} more "
                             "(see the result files)")
        else:
            lines += ["", "Nothing needs attention."]
        if self.notes:
            lines += [""] + [f"Note: {text}" for text in self.notes]
        if self.files:
            lines += ["", "Files:"]
            width = max(len(label) for label, _ in self.files)
            lines += [f"  {label:<{width}}  {path}" for label, path in self.files]
        lines.append("=" * WIDTH)
        return "\n".join(lines)

    def show(self, save_as: str | None = None, popup: bool = False, echo: bool = True) -> str:
        """Show the summary; with ``save_as`` also write it to that text file.

        ``popup`` opens it in a window that stays until it is closed, as every
        command does at its end. ``echo`` prints it in the terminal: commands
        that already printed their detailed results there pass False, so the
        terminal holds the detail and the window the summary. A window that
        cannot open falls back to the terminal.
        """
        text = self.text()
        if save_as:
            try:
                with open(save_as, "w", encoding="utf-8") as handle:
                    handle.write(text.lstrip("\n") + "\n")
            except OSError:
                save_as = None
        if echo:
            self._print(text, save_as)
        elif save_as:
            print(f"Summary saved: {save_as}")
        if popup:
            from utilities import _gui_helpers

            shown = text + (f"\nSummary saved: {save_as}" if save_as else "")
            try:
                _gui_helpers.show_summary(shown, f"Summary - {self.command}")
            except Exception:  # no display: the terminal gets it instead
                if not echo:
                    self._print(text, None)
        return text

    @staticmethod
    def _print(text: str, save_as: str | None) -> None:
        try:
            print(text)
        except UnicodeEncodeError:  # a terminal that cannot show a symbol in a name
            print(text.encode("ascii", "replace").decode("ascii"))
        if save_as:
            print(f"Summary saved: {save_as}")


def listed(names, limit: int = MAX_LISTED) -> str:
    """Names joined for one summary line, cut after ``limit``."""
    names = [str(name) for name in names]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", ... ({len(names)} in all)"
