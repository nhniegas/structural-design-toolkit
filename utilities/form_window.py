"""One window for every question of a command (since v0.18.0).

A command asks its questions one after the other through the helpers of
``_gui_helpers`` (``select_option``, ``enter_values``, ``choose_for_each``,
``DualListboxSelector``, the folder and file pickers). When the command runs
in a form session, each of those calls becomes a section of one window instead
of a window of its own:

- **The form fills in by itself.** A question with an answer to offer (the one
  last chosen, the program's default, a folder used before) takes it at once and
  the command goes on to the next, so the whole form shows up together. Only a
  question with nothing to offer (an empty list, a folder never chosen) waits.
- **An answer changed asks again.** The command's questions are run again from
  the start, as an answers file replays them: an answer is kept for every
  question that is still asked, so a later question appears, disappears or
  offers other choices as the answers before it decide.
- **Warnings stay in the form.** A value the command does not accept shows its
  warning under the questions; the form waits until an answer is changed.
- **Run.** The command marks where its questions end and its work begins
  (``ready_to_run``). The form waits there for Run. After Run the window closes,
  and a question that only comes up during the work is asked in a window of its
  own, as before.

The questions before Run are asked again each time an answer changes, so a
command must not change the model or write a file before ``ready_to_run``.

The session works the same with any front end: the Tk window (``TkForm``), or a
scripted one in the tests. With an answers file (``--answers``) there is no form.
``SDT_DIALOGS=1`` in the environment keeps the dialogs one after the other.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

FOLDERS_FILE = "form_folders.json"   # the last folder chosen for each folder question


class Restart(BaseException):
    """An answer before the current question changed: ask again from the start.

    A BaseException, so that a command's ``except Exception`` does not catch it.
    """


class Cancelled(BaseException):
    """The window was closed: the command stops."""


@dataclass
class Question:
    """One question of a command, as the helper that asks it describes it."""

    kind: str                    # option, values, choices, items, folder, open, save
    title: str                   # the dialog title: the heading of its group
    prompt: str                  # the question, then (after a blank line) what explains it
    options: list = field(default_factory=list)   # option: options; items: available; choices: choices
    labels: list = field(default_factory=list)    # values and choices: one per box
    default: object = None       # option: index; values, choices: dict; folder: a path
    extra: dict = field(default_factory=dict)     # file questions: extension, description, name

    @property
    def key(self) -> tuple:
        """What makes it the same question when it is asked again."""
        question = self.prompt.strip().split("\n\n")[0].strip()
        return (self.kind, self.title, question, tuple(self.labels))

    @property
    def question(self) -> str:
        return self.prompt.strip().split("\n\n")[0].strip() or self.title

    def valid(self, answer) -> bool:
        """Whether ``answer`` is an answer this question accepts now."""
        if answer is None:
            return False
        if self.kind == "option":
            return answer in self.options
        if self.kind == "values":
            return isinstance(answer, dict) and set(answer) == set(self.labels)
        if self.kind == "choices":
            return (isinstance(answer, dict) and set(answer) == set(self.labels)
                    and all(v in self.options for v in answer.values()))
        if self.kind == "items":
            return (isinstance(answer, list) and len(answer) > 0
                    and all(item in self.options for item in answer))
        if self.kind == "open":
            return isinstance(answer, str) and os.path.isfile(answer)
        return isinstance(answer, str) and bool(answer.strip())

    def offered(self):
        """The answer offered before anyone answers, or None when there is none."""
        if self.kind == "option":
            if not self.options:
                return None
            index = self.default if isinstance(self.default, int) else 0
            return self.options[min(max(index, 0), len(self.options) - 1)]
        if self.kind == "values":
            defaults = self.default or {}
            values = {label: str(defaults.get(label, "")) for label in self.labels}
            # a box with nothing in it waits to be filled; except where the command
            # offers it empty on purpose (a level left blank is skipped)
            if all(values.values()) or self.extra.get("blank_allowed"):
                return values
            return None
        if self.kind == "choices":
            defaults = self.default or {}
            return {label: defaults.get(label, self.options[0] if self.options else "")
                    for label in self.labels}
        if self.kind == "items":
            return list(self.default) if self.default else None
        if self.kind == "folder":
            return self.default if self.default and os.path.isdir(str(self.default)) else None
        return None


class FormSession:
    """The answers of one command's questions, kept while they are asked again."""

    def __init__(self, frontend, name: str):
        self.frontend = frontend
        self.name = name
        self.remembered: dict[tuple, object] = {}
        self.asked: list[Question] = []
        self.warnings: list[str] = []
        self.running = False
        self.passes = 0

    # ---- one pass through the command's questions ----
    def begin_pass(self) -> None:
        self.asked = []
        self.warnings = []
        self.passes += 1
        self.frontend.begin_pass()

    def ask(self, question: Question):
        """The answer of ``question``: kept, offered, or waited for.

        A question asked again in the same pass (a command that asks until the
        values are valid) waits for its section to change; it is not repeated.
        """
        for earlier, asked in enumerate(self.asked):
            if asked.key == question.key:
                self.frontend.ready_again(earlier)
                return self._wait(earlier)
        index = len(self.asked)
        self.asked.append(question)
        answer = self.remembered.get(question.key)
        if not question.valid(answer):
            answer = question.offered()
        self.frontend.show(index, question, answer if question.valid(answer) else None)
        if question.valid(answer):
            self.remembered[question.key] = answer
            return answer
        return self._wait(index)

    def warn(self, message: str) -> None:
        """A warning of the command, shown in the form under its questions."""
        self.warnings.append(str(message))
        self.frontend.warn(str(message))

    def _take(self, index: int, value) -> Question:
        """Keep an answer typed in the section at ``index``."""
        question = (self.asked[index] if index < len(self.asked)
                    else self.frontend.question_at(index))
        if question is not None:
            self.remembered[question.key] = value
        return question

    def _wait(self, index: int | None):
        """Wait for the section at ``index`` (None: for Run). Returns its answer."""
        while True:
            event = self.frontend.wait(index)
            kind = event[0]
            if kind == "cancel":
                raise Cancelled
            if kind == "run" and index is None:
                return None
            if kind != "answer":
                continue
            changed, value = event[1], event[2]
            question = self._take(changed, value)
            if index is not None and changed == index and question is not None \
                    and question.valid(value):
                return value
            if changed < len(self.asked) and (index is None or changed < index):
                raise Restart         # an answer the command already used
            # a section after the one waited for: kept for when it is asked

    def ready(self) -> None:
        """The command's questions are done: wait for Run (or a change, or Cancel)."""
        self.frontend.trim(len(self.asked))
        self.frontend.ready()
        self._wait(None)
        self.running = True
        self._remember_folders()
        self.frontend.close()

    def stopped(self) -> None:
        """The command stopped before Run (a value it does not accept, a "Stop"
        chosen): the form stays, with what was said, until an answer changes."""
        self.frontend.trim(len(self.asked))
        self.frontend.stopped(self.warnings)
        while True:
            event = self.frontend.wait(None)
            if event[0] == "cancel":
                raise Cancelled
            if event[0] == "answer":
                self._take(event[1], event[2])
                raise Restart

    def final_answers(self) -> list[tuple[Question, object]]:
        return [(q, self.remembered.get(q.key)) for q in self.asked]

    def _remember_folders(self) -> None:
        folders = {q.title: self.remembered.get(q.key) for q in self.asked
                   if q.kind == "folder" and q.valid(self.remembered.get(q.key))}
        if folders:
            save_folders({**load_folders(), **folders})


# ---- the session of the command that is running ----
_session: FormSession | None = None


def active() -> FormSession | None:
    """The form session asking questions now, or None (dialogs, answers file, after Run)."""
    if _session is not None and not _session.running:
        return _session
    return None


def wanted() -> bool:
    """Whether commands run with the form: not with ``SDT_DIALOGS=1``."""
    return os.environ.get("SDT_DIALOGS", "").strip() not in ("1", "yes", "true")


def run(name: str, command, frontend=None, description: str = ""):
    """Run ``command`` (no arguments) with its questions in one window.

    ``description`` is shown beside the name of the command in the window.
    Returns what the command returns, or None when the window is closed.
    """
    global _session
    if frontend is None:
        frontend = TkForm(name, description)
    session = FormSession(frontend, name)
    _session = session
    try:
        while True:
            session.begin_pass()
            try:
                result = command()
            except Restart:
                continue
            except Cancelled:
                return None
            if session.running:
                return result
            if not session.asked:
                # it stopped before asking anything (no ETABS, no saved model): what it
                # said is shown as a warning of its own, as without the form
                frontend.close()
                _session = None
                from utilities._gui_helpers import show_warning

                for message in session.warnings:
                    show_warning(message, title=f"sdt {name}")
                return result
            try:
                session.stopped()
            except Restart:
                continue
            except Cancelled:
                return None
    finally:
        _session = None
        frontend.close()


def ready_to_run() -> None:
    """Mark the end of a command's questions: the form waits for Run here.

    Nothing happens without a form (dialogs one after the other, an answers file).
    """
    session = active()
    if session is None:
        return
    session.ready()
    from utilities import answers

    if answers.recording():
        for question, answer in session.final_answers():
            answers.keep(question.title, question.prompt if question.kind != "folder" else "",
                         answer)


def load_folders() -> dict:
    from utilities.user_settings import settings_path

    try:
        with open(settings_path(FOLDERS_FILE), encoding="utf-8") as handle:
            return dict(json.load(handle))
    except (OSError, ValueError, TypeError):
        return {}


def save_folders(folders: dict) -> None:
    from utilities.user_settings import settings_path

    path = settings_path(FOLDERS_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(folders, handle, indent=2)
    except OSError:
        pass


# =============================================================================
# THE WINDOW
# =============================================================================
class TkForm:
    """The form window: a page of sections that scrolls, a status line, Cancel and Run."""

    WIDTH = 860
    RADIO_LIMIT = 10        # more options than this: a drop-down list

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.root = None
        self.sections: list[_Section] = []
        self._event = None
        self._waiting: int | None = None
        self._state = "asking"
        self._closed = False
        self._view = None          # where the page was when an answer was changed
        self._wheel = 0            # mouse wheel turns not scrolled yet
        self._wheel_job = None

    # ---- building ----
    def _open(self) -> None:
        if self.root is not None:
            return
        import tkinter as tk
        from tkinter import ttk

        from utilities import _gui_helpers as gui

        root = gui._window(f"sdt {self.name}", topmost=False)
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", lambda: self._post(("cancel",)))
        self.root = root
        self._var = tk.IntVar(root, 0)
        bar_style = gui._scrollbar_style(root)
        gui._header(root, f"sdt {self.name}" + (f"  -  {self.description}"
                                                 if self.description else ""))
        body = tk.Frame(root, bg=gui.BG)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, bg=gui.BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview, style=bar_style)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        page = tk.Frame(canvas, bg=gui.BG)
        window = canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        # the wheel moves the page in steps of a few lines; the turns that come in while
        # the page is redrawn are added up and scrolled at once, so it does not lag behind
        canvas.configure(yscrollincrement=gui._px(root, 20))
        root.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas, self.page = canvas, page
        self.list_frame = tk.Frame(page, bg=gui.BG)
        self.list_frame.pack(fill=tk.X, padx=gui._px(root, 20), pady=(gui._px(root, 8), 0))
        self.notes = tk.Frame(page, bg=gui.BG)
        self.notes.pack(fill=tk.X, padx=gui._px(root, 20), pady=gui._px(root, 12))

        # the footer: what the form waits for, Cancel and Run
        tk.Frame(root, bg=gui.LINE, height=1).pack(side=tk.BOTTOM, fill=tk.X)
        band = tk.Frame(root, bg=gui.PANEL)
        band.pack(side=tk.BOTTOM, fill=tk.X, before=body)
        inner = tk.Frame(band, bg=gui.PANEL)
        inner.pack(fill=tk.X, padx=gui._px(root, 20), pady=gui._px(root, 10))
        self.run_button = gui._button(inner, "Run", lambda: self._post(("run",)), primary=True)
        self.run_button.pack(side=tk.RIGHT)
        gui._button(inner, "Cancel", lambda: self._post(("cancel",))).pack(
            side=tk.RIGHT, padx=(0, gui._px(root, 10)))
        self.status = tk.Label(inner, text="Reading the model...", bg=gui.PANEL, fg=gui.MUTED,
                               font=(gui.BODY_FONT, 9), anchor="w", justify="left",
                               wraplength=gui._px(root, self.WIDTH - 260))
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._set_run(False)
        height = min(gui._px(root, 900), int(root.winfo_screenheight() * 0.85))
        width = gui._px(root, self.WIDTH)
        x = max((root.winfo_screenwidth() - width) // 2, 0)
        y = max((root.winfo_screenheight() - height) // 3, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.deiconify()
        gui._dress(root)
        root.lift()
        root.focus_force()

    def _on_wheel(self, event) -> None:
        self._wheel += event.delta
        if self._wheel_job is None:
            self._wheel_job = self.root.after(15, self._scroll_wheel)

    def _scroll_wheel(self) -> None:
        self._wheel_job = None
        steps = -round(self._wheel / 40)        # one notch of 120: three steps of 20 px
        self._wheel = 0
        if steps:
            try:
                self.canvas.yview_scroll(steps, "units")
            except Exception:  # noqa: BLE001 - the window is going away
                pass

    def _set_run(self, on: bool) -> None:
        from utilities import _gui_helpers as gui

        button = self.run_button
        if on:
            button.configure(state="normal", bg=gui.ACCENT, fg=gui.ON_ACCENT, cursor="hand2")
            button.bind("<Enter>", lambda _e: button.configure(bg=gui.ACCENT_LIGHT))
            button.bind("<Leave>", lambda _e: button.configure(bg=gui.ACCENT))
        else:
            button.configure(state="disabled", bg=gui.BUTTON, disabledforeground=gui.MUTED,
                             cursor="arrow")
            button.unbind("<Enter>")
            button.unbind("<Leave>")

    def _say(self, text: str, colour: str | None = None) -> None:
        from utilities import _gui_helpers as gui

        self.status.configure(text=text, fg=colour or gui.MUTED)

    def _post(self, event) -> None:
        if event[0] == "run" and self._state != "ready":
            return
        self._event = event
        try:
            self._var.set(self._var.get() + 1)
        except Exception:  # noqa: BLE001 - the window is going away
            pass

    # ---- the session's calls ----
    def begin_pass(self) -> None:
        self._state = "asking"
        if self.root is None:      # opened by the first question (none for a command
            return                 # that asks nothing)
        self._set_run(False)
        for child in self.notes.winfo_children():
            child.destroy()
        self._say("Reading the model and the answers...")
        self.root.update_idletasks()

    def question_at(self, index: int):
        return self.sections[index].question if index < len(self.sections) else None

    def show(self, index: int, question: Question, answer) -> None:
        self._open()
        if index < len(self.sections) and self.sections[index].question.key == question.key \
                and self.sections[index].fits(question):
            self.sections[index].question = question
            self.sections[index].set(answer)
        else:
            for section in self.sections[index:]:
                section.destroy()
            del self.sections[index:]
            before = self.sections[-1].question.title if self.sections else None
            self.sections.append(_Section(self, index, question, answer,
                                          heading=question.title != before))
        self.root.update_idletasks()

    def trim(self, count: int) -> None:
        for section in self.sections[count:]:
            section.destroy()
        del self.sections[count:]

    def warn(self, message: str) -> None:
        import tkinter as tk

        from utilities import _gui_helpers as gui

        self._open()
        row = tk.Frame(self.notes, bg=gui.BG)
        row.pack(fill=tk.X, pady=(gui._px(self.root, 4), 0))
        tk.Label(row, text="!", bg=gui.RED, fg="#FFFFFF", font=(gui.BODY_FONT, 9, "bold"),
                 width=2).pack(side=tk.LEFT, anchor="n")
        tk.Label(row, text=message, bg=gui.BG, fg=gui.RED, font=(gui.BODY_FONT, 9),
                 justify="left", anchor="w",
                 wraplength=gui._px(self.root, self.WIDTH - 90)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(gui._px(self.root, 8), 0))

    def ready_again(self, index: int) -> None:
        """The command did not accept the section at ``index``: it waits for a change."""
        from utilities import _gui_helpers as gui

        if index < len(self.sections):
            self.sections[index]._last = None     # the same values, typed again, count
        self._say("Change the values marked, then press Enter.", gui.RED)

    def ready(self) -> None:
        self._state = "ready"
        self._set_run(True)
        self._say("Check the inputs above, then Run. Changing an answer asks the questions "
                  "after it again.")
        self.root.bind("<Return>", lambda _e: self._post(("run",)))

    def stopped(self, warnings: list[str]) -> None:
        from utilities import _gui_helpers as gui

        self._state = "stopped"
        self._set_run(False)
        self._say("The command cannot go on with these answers"
                  + (" (see the message above)" if warnings else "")
                  + ". Change an answer to go on, or Cancel.", gui.RED)

    def wait(self, index: int | None):
        """Run the window until something happens; returns that event."""
        from utilities import _gui_helpers as gui

        self._open()
        if self._closed:
            return ("cancel",)
        self._waiting = index
        for section in self.sections:
            section.mark(section.index == index)
        if index is not None and index < len(self.sections):
            section = self.sections[index]
            self._say(f"Waiting for: {section.question.question}", gui.ACCENT)
            if self._view is None:     # shown when first waited for, not after each change
                self.root.after(50, lambda: self._scroll_to(section))
        if self._view is not None:     # after a change: the page stays where it was
            view, self._view = self._view, None
            self.root.update_idletasks()
            self.canvas.yview_moveto(view)
        self._event = None
        while self._event is None and not self._closed:
            try:
                self.root.wait_variable(self._var)
            except Exception:  # noqa: BLE001 - the window was destroyed
                return ("cancel",)
        if self._closed:
            return ("cancel",)
        event, self._event = self._event, None
        if event[0] == "answer":
            self._view = self.canvas.yview()[0]
            self._say("Asking again with the new answer...")
            self.root.update_idletasks()
        return event

    def _scroll_to(self, section) -> None:
        try:
            self.root.update_idletasks()
            total = max(self.page.winfo_height(), 1)
            self.canvas.yview_moveto(max(section.frame.winfo_y() - 20, 0) / total)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self.root is None or self._closed:
            return
        self._closed = True
        try:
            self.root.unbind_all("<MouseWheel>")
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def _first_sentence(text: str) -> tuple[str, str]:
    """A prompt written in one paragraph, split for the form: the question (its
    first sentence, in bold) and what explains it (the rest, in grey).

    A short prompt, or one with no sentence end before its last words, stays whole.
    """
    text = text.strip()
    if len(text) <= 90:
        return text, ""
    for index, char in enumerate(text):
        if char in ".?:" and index + 2 < len(text) and text[index + 1] == " " \
                and 8 <= index <= len(text) - 20:
            return text[:index + 1], text[index + 2:]
    return text, ""


class _Section:
    """One question in the form: its heading, its text and its input."""

    def __init__(self, form: TkForm, index: int, question: Question, answer, heading: bool):
        import tkinter as tk

        from utilities import _gui_helpers as gui

        self.form, self.index, self.question = form, index, question
        root = form.root
        self.frame = tk.Frame(form.list_frame, bg=gui.BG)
        self.frame.pack(fill=tk.X, pady=(gui._px(root, 10 if heading else 4), 0))
        if heading:
            band = tk.Frame(self.frame, bg=gui.PANEL)
            band.pack(fill=tk.X, pady=(0, gui._px(root, 6)))
            tk.Frame(band, bg=gui.ACCENT, width=gui._px(root, 3)).pack(side=tk.LEFT, fill=tk.Y)
            tk.Label(band, text=question.title, bg=gui.PANEL, fg=gui.TEXT,
                     font=(gui.HEADING_FONT, 10, "bold"), anchor="w").pack(
                side=tk.LEFT, padx=gui._px(root, 8), pady=gui._px(root, 4))
        self.box = tk.Frame(self.frame, bg=gui.BG, highlightthickness=1,
                            highlightbackground=gui.BG, highlightcolor=gui.BG)
        self.box.pack(fill=tk.X)
        inner = tk.Frame(self.box, bg=gui.BG)
        inner.pack(fill=tk.X, padx=gui._px(root, 8), pady=gui._px(root, 4))
        wrap = gui._px(root, form.WIDTH - 110)
        text, _, rest = question.prompt.strip().partition("\n\n")
        if not rest.strip():
            text, rest = _first_sentence(text)
        if text or not heading:     # a list with no prompt: its title is the heading
            tk.Label(inner, text=text or question.title, bg=gui.BG, fg=gui.TEXT,
                     font=(gui.BODY_FONT, 9, "bold"), anchor="w", justify="left",
                     wraplength=wrap).pack(fill=tk.X)
        if rest.strip():
            tk.Label(inner, text=rest.strip(), bg=gui.BG, fg=gui.MUTED, font=(gui.BODY_FONT, 9),
                     anchor="w", justify="left", wraplength=wrap).pack(
                fill=tk.X, pady=(gui._px(root, 2), 0))
        self.input = tk.Frame(inner, bg=gui.BG)
        self.input.pack(fill=tk.X, pady=(gui._px(root, 4), 0))
        self._build(answer)

    # ---- the input of each kind ----
    def fits(self, question: Question) -> bool:
        """Whether this section's input still fits the question (same choices)."""
        return list(question.options) == list(self.question.options)

    def _build(self, answer) -> None:
        kind = self.question.kind
        getattr(self, f"_build_{kind}")(answer)

    def _send(self, value) -> None:
        self.form._post(("answer", self.index, value))

    def _build_option(self, answer) -> None:
        import tkinter as tk
        from tkinter import ttk

        from utilities import _gui_helpers as gui

        options = list(self.question.options)
        self.var = tk.StringVar(self.form.root, value=answer or "")
        if len(options) > TkForm.RADIO_LIMIT:
            combo = ttk.Combobox(self.input, textvariable=self.var, values=options,
                                 state="readonly", font=(gui.BODY_FONT, 9),
                                 width=min(max(len(o) for o in options) + 2, 90))
            combo.pack(anchor="w")
            combo.bind("<<ComboboxSelected>>", lambda _e: self._send(self.var.get()))
            return
        for option in options:
            tk.Radiobutton(self.input, text=option, value=option, variable=self.var,
                           command=lambda: self._send(self.var.get()), bg=gui.BG, fg=gui.TEXT,
                           activebackground=gui.SELECT, selectcolor=gui.FIELD,
                           font=(gui.BODY_FONT, 9), anchor="w", justify="left",
                           wraplength=gui._px(self.form.root, self.form.WIDTH - 150),
                           highlightthickness=0, bd=0).pack(fill=tk.X, anchor="w")

    def _entries(self, labels, values, width=30):
        import tkinter as tk

        from utilities import _gui_helpers as gui

        grid = tk.Frame(self.input, bg=gui.BG)
        grid.pack(fill=tk.X)
        self.entries = {}
        for row, label in enumerate(labels):
            tk.Label(grid, text=label, bg=gui.BG, fg=gui.TEXT, font=(gui.BODY_FONT, 9),
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=(0, gui._px(self.form.root, 12)), pady=1)
            entry = tk.Entry(grid, width=width, font=(gui.BODY_FONT, 9), bg=gui.FIELD,
                             fg=gui.TEXT, relief=tk.SOLID, bd=1, highlightthickness=1,
                             highlightbackground=gui.LINE, highlightcolor=gui.ACCENT_LIGHT,
                             selectbackground=gui.SELECT, selectforeground=gui.TEXT)
            entry.insert(0, str((values or {}).get(label, "")))
            entry.grid(row=row, column=1, sticky="w", pady=1)
            entry.bind("<Return>", lambda _e: self._commit_values())
            entry.bind("<FocusOut>", lambda _e: self._commit_values())
            self.entries[label] = entry
        return grid

    def _build_values(self, answer) -> None:
        width = max([18] + [len(str(v)) + 4 for v in (answer or self.question.default
                                                         or {}).values()])
        self._entries(self.question.labels, answer or self.question.default or {}, min(width, 60))
        self._last = dict(answer) if answer else None

    def _commit_values(self) -> None:
        value = {label: entry.get() for label, entry in self.entries.items()}
        if value != self._last:
            self._last = value
            self._send(value)

    def _build_choices(self, answer) -> None:
        import tkinter as tk
        from tkinter import ttk

        from utilities import _gui_helpers as gui

        grid = tk.Frame(self.input, bg=gui.BG)
        grid.pack(fill=tk.X)
        self.vars = {}
        values = answer or self.question.offered() or {}
        width = min(max(len(c) for c in self.question.options) + 2, 60)
        for row, label in enumerate(self.question.labels):
            tk.Label(grid, text=label, bg=gui.BG, fg=gui.TEXT, font=(gui.BODY_FONT, 9),
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=(0, gui._px(self.form.root, 12)), pady=1)
            var = tk.StringVar(self.form.root, value=values.get(label, ""))
            combo = ttk.Combobox(grid, textvariable=var, values=list(self.question.options),
                                 state="readonly", width=width, font=(gui.BODY_FONT, 9))
            combo.grid(row=row, column=1, sticky="w", pady=1)
            combo.bind("<<ComboboxSelected>>",
                       lambda _e: self._send({k: v.get() for k, v in self.vars.items()}))
            self.vars[label] = var

    def _build_items(self, answer) -> None:
        import tkinter as tk

        from utilities import _gui_helpers as gui

        root = self.form.root
        chosen = list(answer or [])
        row = tk.Frame(self.input, bg=gui.BG)
        row.pack(fill=tk.X)

        def listbox(parent, heading):
            column = tk.Frame(parent, bg=gui.BG)
            column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            label = tk.Label(column, text=heading, bg=gui.BG, fg=gui.MUTED,
                             font=(gui.BODY_FONT, 8, "bold"), anchor="w")
            label.pack(fill=tk.X)
            box = tk.Listbox(column, selectmode=tk.EXTENDED, exportselection=False, height=8,
                             bg=gui.FIELD, fg=gui.TEXT, font=(gui.BODY_FONT, 9),
                             selectbackground=gui.SELECT, selectforeground=gui.TEXT,
                             relief=tk.SOLID, bd=1, highlightthickness=0, activestyle="none")
            box.pack(fill=tk.BOTH, expand=True)
            return box, label

        self.available, self._available_label = listbox(row, "AVAILABLE")
        middle = tk.Frame(row, bg=gui.BG)
        middle.pack(side=tk.LEFT, padx=gui._px(root, 10))
        self.selected, self._selected_label = listbox(row, "SELECTED")
        for item in self.question.options:
            (self.selected if item in chosen else self.available).insert(tk.END, item)
        gui._button(middle, "Add  ›", self._add, width=9).pack(pady=gui._px(root, 3))
        gui._button(middle, "‹  Remove", self._remove, width=9).pack(pady=gui._px(root, 3))
        gui._button(middle, "All", self._all, width=9).pack(pady=gui._px(root, 3))
        self.apply = gui._button(middle, "Apply", self._apply_items, primary=True, width=9)
        self.apply.pack(pady=(gui._px(root, 10), 0))
        self.available.bind("<Double-Button-1>", lambda _e: self._add())
        self.selected.bind("<Double-Button-1>", lambda _e: self._remove())
        self._count()

    def _count(self) -> None:
        self._available_label.configure(text=f"AVAILABLE  ({self.available.size()})")
        self._selected_label.configure(text=f"SELECTED  ({self.selected.size()})")

    def _move(self, source, target, indices) -> None:
        import tkinter as tk

        for i in indices:
            target.insert(tk.END, source.get(i))
        for i in reversed(indices):
            source.delete(i)
        self._count()

    def _add(self) -> None:
        self._move(self.available, self.selected, list(self.available.curselection()))

    def _remove(self) -> None:
        self._move(self.selected, self.available, list(self.selected.curselection()))

    def _all(self) -> None:
        self._move(self.available, self.selected, list(range(self.available.size())))

    def _apply_items(self) -> None:
        import tkinter as tk

        items = list(self.selected.get(0, tk.END))
        if items:
            self._send(items)

    def _build_path(self, answer, browse) -> None:
        import tkinter as tk

        from utilities import _gui_helpers as gui

        row = tk.Frame(self.input, bg=gui.BG)
        row.pack(fill=tk.X)
        self.path = tk.Entry(row, font=(gui.BODY_FONT, 9), bg=gui.FIELD, fg=gui.TEXT,
                             relief=tk.SOLID, bd=1, highlightthickness=1,
                             highlightbackground=gui.LINE, highlightcolor=gui.ACCENT_LIGHT)
        self.path.insert(0, answer or "")
        self.path.pack(side=tk.LEFT, fill=tk.X, expand=True)
        gui._button(row, "Browse...", browse).pack(side=tk.LEFT, padx=(gui._px(self.form.root, 8), 0))
        self.path.bind("<Return>", lambda _e: self._commit_path())
        self.path.bind("<FocusOut>", lambda _e: self._commit_path())
        self._last = answer or ""

    def _commit_path(self) -> None:
        value = os.path.normpath(self.path.get().strip()) if self.path.get().strip() else ""
        if value and value != self._last:
            self._last = value
            self._send(value)

    def _set_path(self, value: str) -> None:
        import tkinter as tk

        if value:
            self.path.delete(0, tk.END)
            self.path.insert(0, os.path.normpath(value))
            self._commit_path()

    def _build_folder(self, answer) -> None:
        from tkinter import filedialog

        self._build_path(answer, lambda: self._set_path(filedialog.askdirectory(
            parent=self.form.root, title=self.question.title)))

    def _build_open(self, answer) -> None:
        from tkinter import filedialog

        extension = self.question.extra.get("extension", "")
        description = self.question.extra.get("description", "Files")
        self._build_path(answer, lambda: self._set_path(filedialog.askopenfilename(
            parent=self.form.root, title=self.question.title,
            filetypes=[(description, f"*{extension}"), ("All files", "*.*")])))

    def _build_save(self, answer) -> None:
        from tkinter import filedialog

        extension = self.question.extra.get("extension", "")
        description = self.question.extra.get("description", "Files")
        self._build_path(answer, lambda: self._set_path(filedialog.asksaveasfilename(
            parent=self.form.root, title=self.question.title,
            initialfile=self.question.extra.get("name", ""), defaultextension=extension,
            filetypes=[(description, f"*{extension}"), ("All files", "*.*")])))

    # ---- state ----
    def set(self, answer) -> None:
        """Show ``answer`` (asked again with the same question)."""
        import tkinter as tk

        kind = self.question.kind
        if answer is None:
            return
        if kind == "option":
            self.var.set(answer)
        elif kind == "values":
            for label, entry in self.entries.items():
                if entry.get() != str(answer.get(label, "")):
                    entry.delete(0, tk.END)
                    entry.insert(0, str(answer.get(label, "")))
            self._last = dict(answer)
        elif kind == "choices":
            for label, var in self.vars.items():
                var.set(answer.get(label, var.get()))
        elif kind in ("folder", "open", "save"):
            if self.path.get() != answer:
                self.path.delete(0, tk.END)
                self.path.insert(0, answer)
            self._last = answer

    def mark(self, waiting: bool) -> None:
        """Outline the section the form waits for."""
        from utilities import _gui_helpers as gui

        colour = gui.ACCENT_LIGHT if waiting else gui.BG
        self.box.configure(highlightbackground=colour, highlightcolor=colour)

    def destroy(self) -> None:
        try:
            self.frame.destroy()
        except Exception:  # noqa: BLE001
            pass
