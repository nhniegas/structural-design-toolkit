"""
The windows of the toolkit: the dialogs that ask, the progress window, the
closing summary and the warnings.

They share one look, that of the program's icon: a graphite window, light
text, and amber for the mark, the choice made and the button that confirms.
File and folder pickers are those of Windows.
"""

import ctypes
import subprocess
import sys
import time
import os
import tkinter as tk
from tkinter import filedialog

from utilities import answers

# Fix blurry text on High-DPI displays for all Tkinter popups
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# =============================================================================
# THEME
# =============================================================================
# The graphite and amber of the icon (tools/make_icon.py), and the greys between.
BG = "#1C1E24"            # the window
PANEL = "#23262E"         # the header band and the footer
FIELD = "#2C3039"         # text boxes, lists, the row of the choice made
LINE = "#3B404B"          # borders
TEXT = "#E9EAEE"
MUTED = "#A3A8B3"         # explanations, labels of little weight
AMBER = "#FFAA2C"
AMBER_LIGHT = "#FFC062"   # the confirm button under the pointer
ON_AMBER = "#1C1E24"      # text on amber
AMBER_DIM = "#7A5A22"     # selected text in a box: the text stays light, with or without focus
RED = "#FF7366"
GREEN = "#78D27C"

HEADING_FONT = "Bahnschrift"      # the font of the icon's letters; Windows has it
BODY_FONT = "Segoe UI"
MONO_FONT = "Consolas"
MARK = "SDT"


def icon_path() -> str:
    """The program's icon file, in the source tree or in the packaged program."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "sdt.ico")


def _px(widget, points: float) -> int:
    """A length given for a 96 dpi screen, in the pixels of this screen."""
    try:
        return max(int(round(points * widget.winfo_fpixels("1i") / 96.0)), 1)
    except tk.TclError:
        return int(points)


def _dress(root) -> None:
    """Give a window the icon, the graphite background and a dark title bar."""
    root.configure(bg=BG)
    try:
        root.iconbitmap(icon_path())
    except tk.TclError:
        pass                      # no icon file: the window still works
    try:
        root.update_idletasks()
        handle = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE, and before 20H1
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    handle, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - not Windows, or an older one: a light title bar
        pass


def _window(title: str, topmost: bool = True) -> tk.Tk:
    """A new themed window, hidden until it is placed with ``_place``."""
    root = tk.Tk()
    root.withdraw()
    root.title(title)
    root.resizable(False, False)
    if topmost:
        root.attributes("-topmost", True)
    _dress(root)
    return root


def _hidden_root() -> tk.Tk:
    """The unseen window a Windows file dialog belongs to; it gives it the icon."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        root.iconbitmap(icon_path())
    except tk.TclError:
        pass
    return root


def _place(root, width: int | None = None, high: bool = False) -> None:
    """Size a window to its content, centre it on the screen and show it."""
    root.update_idletasks()
    width = width or root.winfo_reqwidth()
    height = root.winfo_reqheight()
    x = max((root.winfo_screenwidth() - width) // 2, 0)
    y = max((root.winfo_screenheight() - height) // (3 if high else 2), 0)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.deiconify()
    _dress(root)           # the title bar takes its colour once the window is shown


def _close(root) -> None:
    """Tear a window down without raising Tcl errors."""
    try:
        root.quit()
        root.destroy()
    except tk.TclError:
        pass


def _header(root, title: str) -> tk.Frame:
    """The band at the top of a window: the mark in amber, then what is asked."""
    band = tk.Frame(root, bg=PANEL)
    band.pack(side=tk.TOP, fill=tk.X)
    inner = tk.Frame(band, bg=PANEL)
    inner.pack(fill=tk.X, padx=_px(root, 20), pady=_px(root, 12))
    tk.Label(inner, text=MARK, bg=PANEL, fg=AMBER, font=(HEADING_FONT, 13, "bold")).pack(
        side=tk.LEFT)
    tk.Label(inner, text=title, bg=PANEL, fg=TEXT, font=(HEADING_FONT, 12)).pack(
        side=tk.LEFT, padx=(_px(root, 12), 0))
    tk.Frame(root, bg=AMBER, height=_px(root, 2)).pack(side=tk.TOP, fill=tk.X)
    return band


def _button(parent, text: str, command, primary: bool = False, width: int = 0) -> tk.Button:
    """A flat button: amber for the one that confirms, grey for the others."""
    normal, over = (AMBER, AMBER_LIGHT) if primary else (FIELD, LINE)
    button = tk.Button(
        parent, text=text, command=command, bg=normal, fg=ON_AMBER if primary else TEXT,
        activebackground=over, activeforeground=ON_AMBER if primary else TEXT,
        font=(BODY_FONT, 10, "bold" if primary else "normal"), relief=tk.FLAT, bd=0,
        padx=_px(parent, 18), pady=_px(parent, 6), cursor="hand2", highlightthickness=0)
    if width:
        button.configure(width=width)
    button.bind("<Enter>", lambda _e: button.configure(bg=over))
    button.bind("<Leave>", lambda _e: button.configure(bg=normal))
    return button


def _footer(root, confirm, cancel=None, confirm_text: str = "Confirm",
            cancel_text: str = "Cancel") -> tk.Frame:
    """The band at the bottom: the confirm button at the right, cancel before it."""
    tk.Frame(root, bg=LINE, height=1).pack(side=tk.BOTTOM, fill=tk.X)
    band = tk.Frame(root, bg=PANEL)
    band.pack(side=tk.BOTTOM, fill=tk.X, before=root.pack_slaves()[-1])
    inner = tk.Frame(band, bg=PANEL)
    inner.pack(fill=tk.X, padx=_px(root, 20), pady=_px(root, 12))
    _button(inner, confirm_text, confirm, primary=True).pack(side=tk.RIGHT)
    if cancel is not None:
        _button(inner, cancel_text, cancel).pack(side=tk.RIGHT, padx=(0, _px(root, 10)))
    return inner


def _prompt(parent, prompt: str, wrap: int) -> None:
    """The question in bright text, and what explains it under it in grey."""
    question, _, rest = prompt.strip().partition("\n\n")
    tk.Label(parent, text=question, bg=BG, fg=TEXT, font=(BODY_FONT, 11, "bold"),
             wraplength=wrap, justify="left", anchor="w").pack(fill=tk.X, anchor="w")
    if rest.strip():
        tk.Label(parent, text=rest.strip(), bg=BG, fg=MUTED, font=(BODY_FONT, 10),
                 wraplength=wrap, justify="left", anchor="w").pack(
            fill=tk.X, anchor="w", pady=(_px(parent, 8), 0))


def _scrollbar_style(root) -> str:
    """A thin dark scrollbar; returns the name of its style."""
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        return "Vertical.TScrollbar"
    for name, side in (("SDT.Vertical.TScrollbar", "ns"), ("SDT.Horizontal.TScrollbar", "ew")):
        kind = name.split(".", 1)[1]
        style.layout(name, [(f"{kind.split('.')[0]}.Scrollbar.trough", {
            "sticky": "nsew" if side == "ns" else "ew",
            "children": [(f"{kind.split('.')[0]}.Scrollbar.thumb", {"expand": "1",
                                                                     "sticky": "nsew"})]})])
        style.configure(name, troughcolor=FIELD, background=LINE, bordercolor=FIELD,
                        lightcolor=LINE, darkcolor=LINE, relief=tk.FLAT, gripcount=0)
        style.map(name, background=[("active", MUTED)])
    style.configure("SDT.Horizontal.TProgressbar", troughcolor=FIELD, background=AMBER,
                    bordercolor=FIELD, lightcolor=AMBER, darkcolor=AMBER)
    return "SDT.Vertical.TScrollbar"


def _scroll_when_needed(bar, show, hide):
    """The ``set`` of a scrollbar that is there only while something is out of view."""
    def update(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            hide()
        else:
            show()
        bar.set(first, last)
    return update


def select_output_directory(title: str = "Select the output folder") -> str:
    """Opens a native Windows directory dialog to select an output folder."""
    folder_path = answers.folder(title)   # from the answers file, when there is one
    if folder_path is answers.ASK:
        root = _hidden_root()
        folder_path = filedialog.askdirectory(title=title)
        root.destroy()
        answers.keep(title, "", os.path.normpath(folder_path) if folder_path else None)
    if not folder_path:
        return ""
    # A folder typed in the dialog, or removed since it was last used, does not
    # exist yet: make it now, not after the whole design has run.
    folder_path = os.path.normpath(folder_path)
    try:
        os.makedirs(folder_path, exist_ok=True)
    except OSError as error:
        show_warning(f"The folder {folder_path} cannot be used: {error}", title=title)
        return ""
    return folder_path


def select_save_file(default_name="Composite_Column_Report") -> str:
    """Opens a native Windows Save As dialog to name the PDF."""
    title = "Save PDF Report As"
    scripted = answers.save_path(title, default_name, ".pdf")
    if scripted is not answers.ASK:
        return scripted
    root = _hidden_root()

    file_path = filedialog.asksaveasfilename(
        title=title,
        initialfile=default_name,
        defaultextension=".pdf",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
    )

    root.destroy()
    answers.keep(title, "", file_path or None)
    return file_path


def select_save_path(
    title: str, default_name: str, extension: str, description: str
) -> str:
    """Opens a native Windows Save As dialog for a file of the given extension."""
    scripted = answers.save_path(title, default_name, extension)
    if scripted is not answers.ASK:
        return scripted
    root = _hidden_root()
    file_path = filedialog.asksaveasfilename(
        title=title,
        initialfile=default_name,
        defaultextension=extension,
        filetypes=[(description, f"*{extension}"), ("All files", "*.*")],
    )
    root.destroy()
    answers.keep(title, "", file_path or None)
    return file_path


def select_open_path(title: str, extension: str, description: str) -> str:
    """Opens a native Windows file dialog for one file of the given extension."""
    scripted = answers.open_path(title)
    if scripted is not answers.ASK:
        return scripted
    root = _hidden_root()
    file_path = filedialog.askopenfilename(
        title=title, filetypes=[(description, f"*{extension}"), ("All files", "*.*")]
    )
    root.destroy()
    answers.keep(title, "", file_path or None)
    return file_path


def select_etabs_file() -> str:
    """Opens a native Windows file dialog to select an ETABS .edb file."""
    title = "Select ETABS Model File"
    scripted = answers.open_path(title)
    if scripted is not answers.ASK:
        return scripted
    root = _hidden_root()
    file_path = filedialog.askopenfilename(
        title=title,
        filetypes=[("ETABS Database Files", "*.edb"), ("All Files", "*.*")],
    )
    root.destroy()
    answers.keep(title, "", file_path or None)
    return file_path


def select_option(
    title: str, prompt: str, options: list[str], default_index: int = 0
) -> str | None:
    """Show a screen-centered dialog with one choice per option.

    Returns the chosen option, or ``None`` when the window is closed without
    confirming. Enter confirms, Escape cancels, the arrow keys move the choice,
    and a double click on an option confirms it.
    """
    if not options:
        raise ValueError("select_option needs at least one option.")
    scripted = answers.option(title, prompt, options, default_index)
    if scripted is not answers.ASK:   # answered from the answers file
        return scripted
    result: dict[str, str | None] = {"value": None}

    root = _window(title)
    longest = max(len(line) for line in (prompt + "\n" + "\n".join(options)).split("\n"))
    width = _px(root, 480 if longest < 70 else 640)
    wrap = width - _px(root, 48)
    chosen = {"index": min(max(default_index, 0), len(options) - 1)}

    def close():
        _close(root)

    def confirm():
        result["value"] = options[chosen["index"]]
        close()

    root.protocol("WM_DELETE_WINDOW", close)
    _header(root, title)
    body = tk.Frame(root, bg=BG)
    body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24), pady=(_px(root, 18), _px(root, 16)))
    _prompt(body, prompt, wrap)

    rows: list[tuple[tk.Frame, tk.Frame, tk.Canvas, tk.Label]] = []
    dot = _px(root, 16)

    def paint():
        for index, (row, edge, mark, label) in enumerate(rows):
            on = index == chosen["index"]
            colour = FIELD if on else BG
            for widget in (row, mark, label):
                widget.configure(bg=colour)
            edge.configure(bg=AMBER if on else BG)
            label.configure(fg=TEXT if on else MUTED,
                            font=(BODY_FONT, 10, "bold" if on else "normal"))
            mark.delete("all")
            mark.create_oval(2, 2, dot - 2, dot - 2, outline=AMBER if on else LINE, width=2)
            if on:
                mark.create_oval(dot * 0.32, dot * 0.32, dot * 0.68, dot * 0.68, fill=AMBER,
                                 outline=AMBER)

    def choose(index: int):
        chosen["index"] = index % len(options)
        paint()

    listed = tk.Frame(body, bg=BG)
    listed.pack(fill=tk.X, pady=(_px(root, 16), 0))
    for index, option in enumerate(options):
        row = tk.Frame(listed, bg=BG, cursor="hand2")
        row.pack(fill=tk.X, pady=1)
        edge = tk.Frame(row, bg=BG, width=_px(root, 3))
        edge.pack(side=tk.LEFT, fill=tk.Y)
        mark = tk.Canvas(row, width=dot, height=dot, bg=BG, highlightthickness=0)
        mark.pack(side=tk.LEFT, padx=(_px(root, 10), _px(root, 10)), pady=_px(root, 8))
        label = tk.Label(row, text=option, bg=BG, fg=MUTED, font=(BODY_FONT, 10), anchor="w",
                         justify="left", wraplength=wrap - _px(root, 60))
        label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=_px(root, 6))
        for widget in (row, mark, label):
            widget.bind("<Button-1>", lambda _e, i=index: choose(i))
            widget.bind("<Double-Button-1>", lambda _e, i=index: (choose(i), confirm()))
        rows.append((row, edge, mark, label))
    paint()

    _footer(root, confirm, close)
    root.bind("<Return>", lambda _event: confirm())   # Enter confirms the selection
    root.bind("<Escape>", lambda _event: close())
    root.bind("<Down>", lambda _event: choose(chosen["index"] + 1))
    root.bind("<Up>", lambda _event: choose(chosen["index"] - 1))
    _place(root, width)

    try:
        root.lift()
        root.focus_force()
        root.mainloop()
    except tk.TclError:
        pass
    answers.keep(title, prompt, result["value"])
    return result["value"]


def _entry(parent, width: int) -> tuple[tk.Frame, tk.Entry]:
    """A text box of the theme, in its frame: the border turns amber while it is
    typed in. The frame is what is placed in the window."""
    border = tk.Frame(parent, bg=LINE)
    inside = tk.Frame(border, bg=FIELD)
    inside.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    entry = tk.Entry(inside, width=width, font=(BODY_FONT, 10), bg=FIELD, fg=TEXT,
                     insertbackground=AMBER, selectbackground=AMBER_DIM, selectforeground=TEXT,
                     relief=tk.FLAT, bd=0, highlightthickness=0)
    entry.pack(fill=tk.X, padx=_px(parent, 8), pady=_px(parent, 5))
    entry.bind("<FocusIn>", lambda _e: border.configure(bg=AMBER))
    entry.bind("<FocusOut>", lambda _e: border.configure(bg=LINE))
    inside.bind("<Button-1>", lambda _e: entry.focus_set())
    return border, entry


def enter_values(
    title: str, prompt: str, labels: list[str], defaults: dict[str, str] | None = None
) -> dict[str, str] | None:
    """Show a screen-centered dialog with one text box per label.

    Returns ``{label: typed text}``, or ``None`` when the window is closed
    without confirming. Enter confirms and Escape cancels.
    """
    if not labels:
        raise ValueError("enter_values needs at least one label.")
    scripted = answers.values(title, prompt, labels, defaults)
    if scripted is not answers.ASK:   # answered from the answers file
        return scripted
    result: dict[str, dict[str, str] | None] = {"value": None}

    root = _window(title)
    # Wide enough to show the longest value in full, and never a cramped box.
    box_width = max([24] + [len(str(value)) + 4 for value in (defaults or {}).values()])

    def close():
        _close(root)

    def confirm():
        result["value"] = {label: entry.get() for label, entry in entries.items()}
        close()

    root.protocol("WM_DELETE_WINDOW", close)
    _header(root, title)
    body = tk.Frame(root, bg=BG)
    body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24), pady=(_px(root, 18), _px(root, 18)))
    text = tk.Frame(body, bg=BG)
    text.pack(fill=tk.X)
    boxes = tk.Frame(body, bg=BG)
    boxes.pack(fill=tk.X, pady=(_px(root, 16), 0))
    boxes.columnconfigure(1, weight=1)
    entries: dict[str, tk.Entry] = {}
    for row, label in enumerate(labels):
        tk.Label(boxes, text=label, bg=BG, fg=TEXT, font=(BODY_FONT, 10), anchor="w").grid(
            row=row, column=0, sticky="w", pady=_px(root, 5), padx=(0, _px(root, 16)))
        box, entry = _entry(boxes, box_width)
        entry.insert(0, (defaults or {}).get(label, ""))
        box.grid(row=row, column=1, sticky="ew", pady=_px(root, 5))
        entries[label] = entry

    # The prompt wraps to the width of the labels and boxes, never under 400.
    root.update_idletasks()
    _prompt(text, prompt, max(boxes.winfo_reqwidth(), _px(root, 400)))
    _footer(root, confirm, close)
    root.bind("<Return>", lambda _event: confirm())  # Enter confirms the values
    root.bind("<Escape>", lambda _event: close())
    _place(root)

    try:
        root.lift()
        root.focus_force()
        first = entries[labels[0]]
        first.focus_set()
        first.icursor(tk.END)          # ready to edit what is offered
        root.mainloop()
    except tk.TclError:
        pass
    answers.keep(title, prompt, result["value"])
    return result["value"]


def choose_for_each(
    title: str, prompt: str, labels: list[str], choices: list[str],
    defaults: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """A dialog with one drop-down list per label, every list offering ``choices``.

    Returns ``{label: choice}``, or ``None`` when the window is closed without
    confirming. From an answers file it is answered like ``enter_values``,
    each answer matched to one of the choices.
    """
    if not labels:
        raise ValueError("choose_for_each needs at least one label.")
    offered = {label: (defaults or {}).get(label, choices[0]) for label in labels}
    scripted = answers.values(title, prompt, labels, offered)
    if scripted is not answers.ASK:   # answered from the answers file
        if scripted is None:
            return None
        out = {}
        for label, text in scripted.items():
            match = answers._match_option(text, choices)
            if match is None:
                raise answers.AnswersError(f"{label!r}: {text!r} is not one of " +
                                           " | ".join(choices))
            out[label] = match
        return out
    result: dict[str, dict[str, str] | None] = {"value": None}

    root = _window(title)

    def close():
        _close(root)

    def confirm():
        result["value"] = {label: var.get() for label, var in chosen.items()}
        close()

    root.protocol("WM_DELETE_WINDOW", close)
    _header(root, title)
    body = tk.Frame(root, bg=BG)
    body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24), pady=(_px(root, 18), _px(root, 18)))
    text = tk.Frame(body, bg=BG)
    text.pack(fill=tk.X)
    rows = tk.Frame(body, bg=BG)
    rows.pack(fill=tk.X, pady=(_px(root, 16), 0))
    rows.columnconfigure(1, weight=1)
    chosen: dict[str, tk.StringVar] = {}
    width = max(len(c) for c in choices) + 2
    for row, label in enumerate(labels):
        tk.Label(rows, text=label, bg=BG, fg=TEXT, font=(BODY_FONT, 10), anchor="w").grid(
            row=row, column=0, sticky="w", pady=_px(root, 4), padx=(0, _px(root, 16)))
        var = tk.StringVar(root, value=offered[label])
        menu = tk.OptionMenu(rows, var, *choices)
        menu.configure(bg=FIELD, fg=TEXT, activebackground=AMBER_DIM, activeforeground=TEXT,
                       highlightthickness=1, highlightbackground=LINE, relief=tk.FLAT,
                       font=(BODY_FONT, 10), anchor="w", width=width, bd=0)
        menu["menu"].configure(bg=FIELD, fg=TEXT, activebackground=AMBER,
                               activeforeground=BG, font=(BODY_FONT, 10), bd=0)
        menu.grid(row=row, column=1, sticky="ew", pady=_px(root, 4))
        chosen[label] = var

    root.update_idletasks()
    _prompt(text, prompt, max(rows.winfo_reqwidth(), _px(root, 400)))
    _footer(root, confirm, close)
    root.bind("<Return>", lambda _event: confirm())
    root.bind("<Escape>", lambda _event: close())
    _place(root)
    try:
        root.lift()
        root.focus_force()
        root.mainloop()
    except tk.TclError:
        pass
    answers.keep(title, prompt, result["value"])
    return result["value"]


class DualListboxSelector:
    """A screen-centered window with two lists: what is available, and what is
    chosen. Items move with the buttons between them or with a double click."""

    def __init__(self, title: str, available_items: list[str]):
        self.selected_items: list[str] = []
        self.available_items = available_items
        self.title = title
        # with an answers file the list is answered from it and no window is made
        self.scripted = answers.items(title, list(available_items))
        if self.scripted is not answers.ASK:
            return

        self.root = _window(title)
        # Handle early window close ('X' button) safely
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        _place(self.root)

    def _list(self, parent, heading: str) -> tuple[tk.Listbox, tk.Label]:
        from tkinter import ttk

        root = self.root
        title = tk.Label(parent, text=heading, bg=BG, fg=MUTED, font=(BODY_FONT, 9, "bold"),
                         anchor="w")
        title.pack(fill=tk.X, pady=(0, _px(root, 6)))
        frame = tk.Frame(parent, bg=LINE)
        frame.pack(fill=tk.BOTH, expand=True)
        inside = tk.Frame(frame, bg=FIELD)
        inside.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        # tk.EXTENDED enables Shift+Click range select and Ctrl+Click toggle select
        box = tk.Listbox(
            inside, selectmode=tk.EXTENDED, exportselection=False, width=34, height=14,
            bg=FIELD, fg=TEXT, font=(BODY_FONT, 10), selectbackground=AMBER,
            selectforeground=ON_AMBER, relief=tk.FLAT, bd=0, highlightthickness=0,
            activestyle="none")
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(_px(root, 8), 0),
                 pady=_px(root, 6))
        bar = ttk.Scrollbar(inside, orient=tk.VERTICAL, command=box.yview, style=self._bar)
        box.configure(yscrollcommand=_scroll_when_needed(
            bar, lambda: bar.pack(side=tk.RIGHT, fill=tk.Y), bar.pack_forget))
        return box, title

    def _build_ui(self):
        root = self.root
        self._bar = _scrollbar_style(root)
        _header(root, self.title)
        body = tk.Frame(root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24),
                  pady=(_px(root, 18), _px(root, 8)))

        left = tk.Frame(body, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lb_available, self._available_title = self._list(left, "AVAILABLE")
        for item in self.available_items:
            self.lb_available.insert(tk.END, item)
        buttons = tk.Frame(left, bg=BG)
        buttons.pack(fill=tk.X, pady=(_px(root, 8), 0))
        _button(buttons, "Select all", self._select_all_available).pack(side=tk.LEFT)
        _button(buttons, "Clear", self._unselect_all_available).pack(
            side=tk.LEFT, padx=(_px(root, 6), 0))

        middle = tk.Frame(body, bg=BG)
        middle.pack(side=tk.LEFT, fill=tk.Y, padx=_px(root, 14))
        inner = tk.Frame(middle, bg=BG)
        inner.pack(expand=True)   # the two buttons sit at mid-height
        _button(inner, "Add  ›", self._add_items, primary=True, width=9).pack(
            pady=_px(root, 5))
        _button(inner, "‹  Remove", self._remove_items, width=9).pack(pady=_px(root, 5))

        right = tk.Frame(body, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lb_selected, self._selected_title = self._list(right, "SELECTED")
        buttons = tk.Frame(right, bg=BG)
        buttons.pack(fill=tk.X, pady=(_px(root, 8), 0))
        _button(buttons, "Select all", self._select_all_selected).pack(side=tk.LEFT)
        _button(buttons, "Clear", self._unselect_all_selected).pack(
            side=tk.LEFT, padx=(_px(root, 6), 0))

        self._message = tk.Label(root, text="Shift or Ctrl with a click selects several. "
                                 "A double click moves an item.", bg=BG, fg=MUTED,
                                 font=(BODY_FONT, 9), anchor="w")
        self._message.pack(fill=tk.X, padx=_px(root, 24), pady=(0, _px(root, 12)))
        _footer(root, self._confirm, self._on_close)
        self.lb_available.bind("<Double-Button-1>", lambda _e: self._add_items())
        self.lb_selected.bind("<Double-Button-1>", lambda _e: self._remove_items())
        root.bind("<Return>", lambda _e: self._confirm())
        root.bind("<Escape>", lambda _e: self._on_close())
        self._count()

    def _count(self):
        self._available_title.configure(text=f"AVAILABLE  ({self.lb_available.size()})")
        self._selected_title.configure(text=f"SELECTED  ({self.lb_selected.size()})")

    # --- Available Listbox Helpers ---
    def _select_all_available(self):
        self.lb_available.selection_set(0, tk.END)

    def _unselect_all_available(self):
        self.lb_available.selection_clear(0, tk.END)

    # --- Selected Listbox Helpers ---
    def _select_all_selected(self):
        self.lb_selected.selection_set(0, tk.END)

    def _unselect_all_selected(self):
        self.lb_selected.selection_clear(0, tk.END)

    # --- Item Transfer Logic ---
    def _add_items(self):
        selected_indices = list(self.lb_available.curselection())
        for idx in selected_indices:           # in the order they are listed
            self.lb_selected.insert(tk.END, self.lb_available.get(idx))
        for idx in reversed(selected_indices):
            self.lb_available.delete(idx)
        self._count()

    def _remove_items(self):
        selected_indices = list(self.lb_selected.curselection())
        for idx in selected_indices:
            self.lb_available.insert(tk.END, self.lb_selected.get(idx))
        for idx in reversed(selected_indices):
            self.lb_selected.delete(idx)
        self._count()

    def _confirm(self):
        self.selected_items = list(self.lb_selected.get(0, tk.END))
        if not self.selected_items:
            self._message.configure(text="Select at least one item: add it to the list on "
                                    "the right.", fg=RED)
            return
        self._on_close()

    def _on_close(self):
        """Safely tears down the Tkinter loop without causing Tcl errors."""
        _close(self.root)

    def show(self) -> list[str]:
        """Displays dialog safely and blocks until user confirms or closes."""
        if self.scripted is not answers.ASK:
            self.selected_items = list(self.scripted)
            return self.selected_items
        try:
            if self.root.winfo_exists():
                self.root.lift()
                self.root.focus_force()
                self.root.mainloop()
        except tk.TclError:
            pass

        answers.keep(self.title, "", list(self.selected_items) or None)
        return self.selected_items


def run_loading_window(message: str) -> None:
    """The progress window itself. It runs in a process of its own (see
    ``LoadingWindow``) and shows the lines it reads from its standard input."""
    import queue
    import threading
    from tkinter import ttk

    root = _window("Working")
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    _scrollbar_style(root)
    _header(root, message)
    width = _px(root, 440)
    wrap = width - _px(root, 48)
    body = tk.Frame(root, bg=BG)
    body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24), pady=_px(root, 20))
    progress = ttk.Progressbar(body, mode="indeterminate", style="SDT.Horizontal.TProgressbar")
    progress.pack(fill=tk.X)
    progress.start(12)

    # Progress lines sent by LoadingWindow.update() arrive on stdin, one per line.
    detail = tk.Label(body, text="Please wait.", bg=BG, fg=MUTED, font=(BODY_FONT, 10),
                      wraplength=wrap, justify="left", anchor="w")
    detail.pack(fill=tk.X, pady=(_px(root, 14), 0))
    updates: queue.Queue = queue.Queue()

    def read_updates():
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            for line in sys.stdin:
                updates.put(line.rstrip("\n").replace("\t", "\n"))
        except Exception:
            pass

    def show_updates():
        text = None
        while not updates.empty():
            text = updates.get_nowait()
        if text is not None:
            detail.config(text=text)
            root.update_idletasks()
            needed = root.winfo_reqheight()
            if needed > root.winfo_height():       # a longer line: the window grows down
                root.geometry(f"{width}x{needed}")
        root.after(100, show_updates)

    threading.Thread(target=read_updates, daemon=True).start()
    root.after(100, show_updates)
    _place(root, width)
    root.lift()
    root.focus_force()
    root.mainloop()


LOADING_WINDOW_FLAG = "--loading-window"  # main.py runs run_loading_window for it


def loading_window_command(message: str) -> list[str]:
    """The command that opens the progress window in a process of its own.

    The packaged program has no separate Python, so it starts itself with a
    hidden first argument that main.py turns into ``run_loading_window``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, LOADING_WINDOW_FLAG, message]
    return [sys.executable, "-c",
            "import sys; from utilities._gui_helpers import run_loading_window; "
            "run_loading_window(sys.argv[1])", message]


class LoadingWindow:
    """Displays a process-isolated loading window centered on screen during heavy ETABS tasks."""

    def __init__(self, message: str = "Processing, please wait..."):
        self.message = message
        self.proc: subprocess.Popen | None = None
        self._last_detail = ""
        self._last_update = 0.0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Starts the loading popup (none with an answers file: no one is watching)."""
        if answers.replaying():
            return
        # the window's process must find this package wherever the command was typed
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [project] + [p for p in environment.get("PYTHONPATH", "").split(os.pathsep) if p])
        self.proc = subprocess.Popen(
            loading_window_command(self.message),
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        time.sleep(0.5)

    def update(self, detail: str) -> None:
        """Show a progress line under the main message while the task runs.

        Calls that arrive faster than the window can show them are dropped.
        """
        if not self.proc or self.proc.stdin is None or detail == self._last_detail:
            return
        now = time.monotonic()
        if now - self._last_update < 0.1:
            return
        self._last_detail = detail
        self._last_update = now
        try:
            self.proc.stdin.write(str(detail).replace("\n", "\t") + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def stop(self):
        """Stops the loading popup."""
        if self.proc:
            self.proc.terminate()
            self.proc = None


def show_summary(text: str, title: str = "Summary") -> None:
    """Show a command's closing summary in a window, until it is closed.

    The text is shown in a fixed-width font so its columns line up, and can be
    selected and copied. Enter, Escape or OK closes it; Copy puts the whole
    text on the clipboard. With an answers file it is printed instead, and
    nothing waits to be closed.
    """
    if answers.summary(text, title):
        return
    from tkinter import ttk

    lines = text.strip("\n").split("\n")
    root = _window(title)
    root.resizable(True, True)

    def close(_event=None):
        _close(root)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(text.strip("\n") + "\n")
        copied.configure(text="Copied")
        root.after(1500, lambda: copied.configure(text=""))

    root.protocol("WM_DELETE_WINDOW", close)
    bar = _scrollbar_style(root)
    _header(root, title)
    frame = tk.Frame(root, bg=BG)
    frame.pack(fill=tk.BOTH, expand=True, padx=_px(root, 20), pady=_px(root, 16))
    columns = min(max(len(line) for line in lines) + 2, 150)
    rows = min(len(lines) + 1, 36)
    box = tk.Text(frame, width=columns, height=rows, wrap=tk.NONE, font=(MONO_FONT, 10),
                  relief=tk.FLAT, bd=0, bg=BG, fg=TEXT, insertbackground=BG,
                  selectbackground=AMBER, selectforeground=ON_AMBER, highlightthickness=0,
                  padx=_px(root, 4), pady=_px(root, 4))
    vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=box.yview, style=bar)
    horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=box.xview,
                               style="SDT.Horizontal.TScrollbar")
    box.configure(
        yscrollcommand=_scroll_when_needed(
            vertical, lambda: vertical.grid(row=0, column=1, sticky="ns"), vertical.grid_remove),
        xscrollcommand=_scroll_when_needed(
            horizontal, lambda: horizontal.grid(row=1, column=0, sticky="ew"),
            horizontal.grid_remove))
    box.tag_configure("rule", foreground=LINE)
    box.tag_configure("heading", foreground=AMBER, font=(MONO_FONT, 10, "bold"))
    box.tag_configure("attention", foreground=RED, font=(MONO_FONT, 10, "bold"))
    box.tag_configure("good", foreground=GREEN)
    box.tag_configure("note", foreground=MUTED)
    attention = False
    for line in lines:
        if line.startswith("Needs attention"):
            attention = True
        elif not line.strip():
            attention = False
        if attention:
            tag = "attention"
        elif line.strip() and set(line.strip()) <= {"=", "-"}:
            tag = "rule"
        elif line.startswith("SUMMARY") or line.rstrip() == "Files:":
            tag = "heading"
        elif line.startswith("Nothing needs attention"):
            tag = "good"
        elif line.startswith("Note:"):
            tag = "note"
        else:
            tag = ()
        box.insert(tk.END, line + "\n", tag)
    box.configure(state=tk.DISABLED)  # read only; the text can still be selected and copied
    box.grid(row=0, column=0, sticky="nsew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    footer = _footer(root, close, copy, confirm_text="OK", cancel_text="Copy")
    copied = tk.Label(footer, text="", bg=PANEL, fg=MUTED, font=(BODY_FONT, 9))
    copied.pack(side=tk.RIGHT, padx=(0, _px(root, 12)))
    root.bind("<Return>", close)
    root.bind("<Escape>", close)
    _place(root, high=True)
    root.lift()
    root.focus_force()
    root.mainloop()


def show_warning(message: str, title: str = "Warning", topmost: bool = True) -> None:
    """Show a warning in a window, until it is closed (Enter, Escape or OK).

    With an answers file it is printed instead.
    """
    if answers.warning(message, title):
        return
    root = _window(title, topmost)   # topmost: over Excel and ETABS

    def close(_event=None):
        _close(root)

    root.protocol("WM_DELETE_WINDOW", close)
    _header(root, title)
    longest = max(len(line) for line in str(message).split("\n"))
    width = _px(root, 460 if longest < 70 and len(str(message)) < 260 else 620)
    body = tk.Frame(root, bg=BG)
    body.pack(fill=tk.BOTH, expand=True, padx=_px(root, 24), pady=_px(root, 20))
    size = _px(root, 30)
    sign = tk.Canvas(body, width=size, height=size, bg=BG, highlightthickness=0)
    sign.create_polygon(size / 2, 2, size - 1, size - 3, 1, size - 3, fill=AMBER, outline=AMBER)
    sign.create_text(size / 2, size * 0.60, text="!", fill=ON_AMBER,
                     font=(BODY_FONT, 12, "bold"))
    sign.pack(side=tk.LEFT, anchor="n", padx=(0, _px(root, 16)))
    tk.Label(body, text=str(message), bg=BG, fg=TEXT, font=(BODY_FONT, 10), justify="left",
             anchor="w", wraplength=width - _px(root, 48) - size - _px(root, 16)).pack(
        side=tk.LEFT, fill=tk.X, expand=True)
    _footer(root, close, confirm_text="OK")
    root.bind("<Return>", close)
    root.bind("<Escape>", close)
    _place(root, width)
    try:
        root.lift()
        root.focus_force()
        root.mainloop()
    except tk.TclError:
        pass
