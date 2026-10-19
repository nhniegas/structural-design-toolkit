"""One window for a standalone design check: every input in a form that
scrolls, a drawing that follows the inputs as they are typed, and the results
beside them.

Used by ``sdt pilecap``, ``sdt composite`` and ``sdt steel``. The window stays
open after a run, so a value can be changed and the check run again at once;
the results are printed in the terminal or written as the PDF report from its
buttons. With an answers file the window is not opened: the module runs
through the dialogs of ``utilities/design_cli.py``, which the file answers.

A module shown here offers, beside the names of ``design_cli``:

* ``preview(figure, values, result) -> str``  draws on the matplotlib figure;
  ``result`` is the last result while the inputs that matter to it are
  unchanged, else None. Returns the text shown under the drawing.
* ``PREVIEW_KEYS`` (optional)  the inputs a result belongs to; when one of them
  changes, the drawing stops showing the result. Default: every input.
* ``result_parts(result) -> [(text, tag)]``  the results panel; tags "pass",
  "fail", "head", "muted" or None.
* ``window_summary(result) -> (line, [failures])`` (optional)  for the summary
  printed when the window is closed.
* ``MULTILINE`` (optional)  keys of text inputs shown as a box of several lines.
* ``SHOWN_WHEN`` (optional)  {key: (choice key, (values, ...))}: an input shown only
  while that choice has one of the values (others are kept, and hidden).
* ``FILE_INPUTS`` (optional)  {key: (dialog title, patterns, (choice key, value))}:
  a text input with a Browse button, which also sets the choice to the value.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import filedialog, ttk

from utilities import _gui_helpers as gui
from utilities.design_cli import _load, _save, parse_inputs

REDRAW_DELAY_MS = 350
LABEL_WIDTH = 300           # px at 96 dpi: labels wrap to this

# colours of the drawings, those of the window
PLOT = dict(bg=gui.PANEL, field=gui.FIELD, line=gui.LINE, text=gui.TEXT, muted=gui.MUTED,
            amber=gui.AMBER, red=gui.RED, green=gui.GREEN, dark=gui.BG, on_amber=gui.ON_AMBER)


def style_axes(ax, ticks: bool = True) -> None:
    """Dress a matplotlib axes in the colours of the window."""
    ax.set_facecolor(PLOT["bg"])
    for side in ax.spines.values():
        side.set_color(PLOT["line"])
    ax.tick_params(colors=PLOT["muted"], labelsize=7)
    if not ticks:
        ax.set_xticks([])
        ax.set_yticks([])
    ax.xaxis.label.set_color(PLOT["muted"])
    ax.yaxis.label.set_color(PLOT["muted"])
    ax.title.set_color(PLOT["text"])


def ratio_colour(ratio: float | None) -> str:
    if ratio is None:
        return PLOT["muted"]
    return PLOT["green"] if ratio <= 0.9 else PLOT["amber"] if ratio <= 1.0 else PLOT["red"]


class DesignWindow:
    """The form at the left, the drawing in the middle, the results at the right."""

    def __init__(self, module, name: str, title: str, report_name: str):
        self.module = module
        self.name = name
        self.title = title
        self.report_name = report_name
        self.fields = module.INPUTS
        self.groups = getattr(module, "INPUT_GROUPS", None) or [("Inputs", self.fields)]
        self.multiline = set(getattr(module, "MULTILINE", ()))
        self.file_inputs = dict(getattr(module, "FILE_INPUTS", {}))
        self.preview_keys = getattr(module, "PREVIEW_KEYS", None)
        self.shown_when = dict(getattr(module, "SHOWN_WHEN", {}))
        self.rows: dict[str, tuple] = {}
        self.result = None
        self.result_values = None
        self.saved_pdf = None
        self.widgets: dict[str, object] = {}
        self._pending = None
        saved = _load(name)
        self.start = {}
        for field in self.fields:
            value = saved.get(field.key, field.default)
            self.start[field.key] = "" if value is None else (
                f"{value:g}" if isinstance(value, float) else str(value))
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        root = self.root = gui._window(self.title, topmost=False)
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        gui._header(root, self.title)
        style_name = gui._scrollbar_style(root)
        self._combo_style(root)

        body = tk.Frame(root, bg=gui.BG)
        body.pack(fill=tk.BOTH, expand=True, padx=gui._px(root, 14), pady=gui._px(root, 12))
        body.columnconfigure(1, weight=3)
        body.columnconfigure(2, weight=2)
        body.rowconfigure(0, weight=1)

        holder = tk.Frame(body, bg=gui.BG)
        holder.grid(row=0, column=0, sticky="nsew")
        canvas = tk.Canvas(holder, bg=gui.BG, highlightthickness=0, bd=0,
                           width=gui._px(root, 560))
        bar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview, style=style_name)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        form = tk.Frame(canvas, bg=gui.BG)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        root.bind_all("<MouseWheel>", lambda e: self._wheel(canvas, e))
        self._form(form)

        drawing = tk.Frame(body, bg=gui.PANEL)
        drawing.grid(row=0, column=1, sticky="nsew",
                     padx=(gui._px(root, 14), gui._px(root, 14)))
        self._drawing(drawing)

        results = tk.Frame(body, bg=gui.PANEL)
        results.grid(row=0, column=2, sticky="nsew")
        tk.Label(results, text="RESULTS", bg=gui.PANEL, fg=gui.AMBER,
                 font=(gui.HEADING_FONT, 11, "bold"), anchor="w").pack(
            fill=tk.X, padx=gui._px(root, 12), pady=(gui._px(root, 10), gui._px(root, 4)))
        self.text = tk.Text(results, bg=gui.PANEL, fg=gui.TEXT, font=(gui.MONO_FONT, 9),
                            relief=tk.FLAT, bd=0, wrap="word", highlightthickness=0,
                            width=46, padx=gui._px(root, 12), pady=gui._px(root, 4))
        self.text.pack(fill=tk.BOTH, expand=True)
        for tag, colour in (("pass", gui.GREEN), ("fail", gui.RED), ("head", gui.AMBER),
                            ("muted", gui.MUTED)):
            self.text.tag_configure(tag, foreground=colour)
        self.text.tag_configure("head", font=(gui.HEADING_FONT, 10, "bold"))
        self.show_text([("Type or change the inputs; the drawing follows them.\n\n", "muted"),
                        ("Run: the check runs and the results appear here.\n", "muted")])

        tk.Frame(root, bg=gui.LINE, height=1).pack(side=tk.BOTTOM, fill=tk.X)
        band = tk.Frame(root, bg=gui.PANEL)
        band.pack(side=tk.BOTTOM, fill=tk.X, before=body)
        inner = tk.Frame(band, bg=gui.PANEL)
        inner.pack(fill=tk.X, padx=gui._px(root, 20), pady=gui._px(root, 10))
        self.status = tk.Label(inner, text="", bg=gui.PANEL, fg=gui.MUTED,
                               font=(gui.BODY_FONT, 9), anchor="w")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        gui._button(inner, "Run", self.run, primary=True).pack(side=tk.RIGHT)
        gui._button(inner, "Save PDF report", self.save_pdf).pack(
            side=tk.RIGHT, padx=(0, gui._px(root, 10)))
        gui._button(inner, "Print in the terminal", self.print_results).pack(
            side=tk.RIGHT, padx=(0, gui._px(root, 10)))
        gui._button(inner, "Close", self.close).pack(side=tk.RIGHT, padx=(0, gui._px(root, 10)))
        root.bind("<Control-Return>", lambda _e: self.run())

        # clear of the taskbar: the work area is the screen less about 60 px at the bottom
        width = min(gui._px(root, 1560), root.winfo_screenwidth() - 60)
        height = min(gui._px(root, 900), root.winfo_screenheight() - gui._px(root, 140))
        root.update_idletasks()
        x = max((root.winfo_screenwidth() - width) // 2, 0)
        y = gui._px(root, 20)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.deiconify()
        gui._dress(root)
        self.redraw()

    def _combo_style(self, root) -> None:
        style = ttk.Style(root)
        style.configure("SDT.TCombobox", fieldbackground=gui.FIELD, background=gui.LINE,
                        foreground=gui.TEXT, arrowcolor=gui.AMBER, bordercolor=gui.LINE,
                        lightcolor=gui.FIELD, darkcolor=gui.FIELD, selectbackground=gui.FIELD,
                        selectforeground=gui.TEXT, padding=gui._px(root, 4))
        style.map("SDT.TCombobox", fieldbackground=[("readonly", gui.FIELD)],
                  foreground=[("readonly", gui.TEXT)], bordercolor=[("focus", gui.AMBER)])
        root.option_add("*TCombobox*Listbox.background", gui.FIELD)
        root.option_add("*TCombobox*Listbox.foreground", gui.TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", gui.AMBER)
        root.option_add("*TCombobox*Listbox.selectForeground", gui.ON_AMBER)

    def _wheel(self, canvas, event) -> None:
        widget = event.widget
        while widget is not None:          # scroll the form only when the pointer is over it
            if widget is canvas:
                canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            widget = getattr(widget, "master", None)

    def _form(self, form) -> None:
        root = self.root
        wrap = gui._px(root, LABEL_WIDTH)
        row = 0
        for title, group in self.groups:
            heading = title.split(":", 1)[-1].strip().upper()
            tk.Label(form, text=heading, bg=gui.BG, fg=gui.AMBER,
                     font=(gui.HEADING_FONT, 10, "bold"), anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="w",
                pady=(gui._px(root, 14 if row else 2), gui._px(root, 4)))
            row += 1
            for field in group:
                # a box that may be left blank says in its label what a blank means
                label = tk.Label(form, text=field.label, bg=gui.BG, fg=gui.TEXT,
                                 font=(gui.BODY_FONT, 9), wraplength=wrap, justify="left",
                                 anchor="w")
                label.grid(row=row, column=0, sticky="w", pady=gui._px(root, 3),
                           padx=(0, gui._px(root, 12)))
                widget = self._widget(form, field)
                widget.grid(row=row, column=1, sticky="ew", pady=gui._px(root, 3),
                            padx=(0, gui._px(root, 16)))      # clear of the scrollbar
                self.rows[field.key] = (label, widget)
                row += 1
        form.columnconfigure(1, weight=1)
        for key, (choice, _) in self.shown_when.items():
            if choice in self.widgets:
                self.widgets[choice].trace_add("write", lambda *_: self._show_rows())
        self._show_rows()

    def _show_rows(self) -> None:
        """Show only the inputs that apply to the choices made (``SHOWN_WHEN``)."""
        for key, (choice, wanted) in self.shown_when.items():
            if key not in self.rows or choice not in self.widgets:
                continue
            visible = self.widgets[choice].get() in wanted
            for part in self.rows[key]:
                if visible:
                    part.grid()
                else:
                    part.grid_remove()

    def _widget(self, form, field):
        root = self.root
        value = self.start.get(field.key, "")
        if field.kind == "choice":
            var = tk.StringVar(value=value if value in field.choices else field.choices[0])
            box = ttk.Combobox(form, textvariable=var, values=list(field.choices),
                               state="readonly", style="SDT.TCombobox", font=(gui.BODY_FONT, 10))
            var.trace_add("write", lambda *_: self.schedule())
            self.widgets[field.key] = var
            return box
        if field.key in self.multiline:
            frame = tk.Frame(form, bg=gui.LINE)
            text = tk.Text(frame, height=5, width=30, bg=gui.FIELD, fg=gui.TEXT,
                           insertbackground=gui.AMBER, font=(gui.MONO_FONT, 9), relief=tk.FLAT,
                           bd=0, wrap="word", padx=gui._px(root, 6), pady=gui._px(root, 4))
            text.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            text.insert("1.0", value.replace("; ", ";\n"))
            text.bind("<KeyRelease>", lambda _e: self.schedule())
            self.widgets[field.key] = text
            return frame
        frame = tk.Frame(form, bg=gui.BG)
        border, entry = gui._entry(frame, 14)
        border.pack(side=tk.LEFT, fill=tk.X, expand=True)
        var = tk.StringVar(value=value)
        entry.configure(textvariable=var)
        var.trace_add("write", lambda *_: self.schedule())
        self.widgets[field.key] = var
        if field.key in self.file_inputs:
            gui._button(frame, "Browse", lambda: self.browse(field.key)).pack(
                side=tk.LEFT, padx=(gui._px(root, 6), 0))
        return frame

    def browse(self, key: str) -> None:
        title, patterns, choice = self.file_inputs[key]
        path = filedialog.askopenfilename(parent=self.root, title=title,
                                          filetypes=[(title, patterns), ("All files", "*.*")])
        if path:
            self.widgets[key].set(path)
            if choice:
                self.widgets[choice[0]].set(choice[1])

    # ------------------------------------------------------------------ values
    def typed(self) -> dict[str, str]:
        out = {}
        for field in self.fields:
            widget = self.widgets[field.key]
            if isinstance(widget, tk.Text):
                out[field.label] = " ".join(widget.get("1.0", "end").split("\n")).strip()
            else:
                out[field.label] = widget.get()
        return out

    def values(self) -> dict:
        return parse_inputs(self.fields, self.typed())

    def _key(self, values: dict) -> tuple:
        keys = self.preview_keys or [f.key for f in self.fields]
        return tuple(values.get(k) for k in keys)

    # ------------------------------------------------------------------ drawing
    def _drawing(self, frame) -> None:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        root = self.root
        tk.Label(frame, text=getattr(self.module, "PREVIEW_TITLE", "DRAWING"), bg=gui.PANEL,
                 fg=gui.AMBER, font=(gui.HEADING_FONT, 11, "bold"), anchor="w").pack(
            fill=tk.X, padx=gui._px(root, 12), pady=(gui._px(root, 10), gui._px(root, 4)))
        self.figure = Figure(figsize=(6, 6), dpi=100, facecolor=gui.PANEL)
        self.plot = FigureCanvasTkAgg(self.figure, master=frame)
        self.plot.get_tk_widget().configure(bg=gui.PANEL, highlightthickness=0)
        self.plot.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=gui._px(root, 6),
                                       pady=(0, gui._px(root, 6)))
        self.legend = tk.Label(frame, text="", bg=gui.PANEL, fg=gui.MUTED,
                               font=(gui.BODY_FONT, 9), justify="left", anchor="w",
                               wraplength=gui._px(root, 560))
        self.legend.pack(fill=tk.X, padx=gui._px(root, 12), pady=(0, gui._px(root, 10)))

    def schedule(self) -> None:
        """Redraw a moment after the last key, not at every key."""
        if self._pending is not None:
            self.root.after_cancel(self._pending)
        self._pending = self.root.after(REDRAW_DELAY_MS, self.redraw)

    def redraw(self) -> None:
        self._pending = None
        self.figure.clear()
        try:
            values = self.values()
            current = self.result if (self.result is not None and
                                      self.result_values == self._key(values)) else None
            text = self.module.preview(self.figure, values, current)
            if self.result is not None and current is None:
                text = (text + "\n" if text else "") + \
                    "The inputs changed since the last run: run again to see the results drawn."
        except Exception as error:  # noqa: BLE001 - half-typed input: say what is missing
            self.figure.clear()
            ax = self.figure.add_axes([0, 0, 1, 1])
            style_axes(ax, ticks=False)
            ax.text(0.5, 0.5, f"The drawing waits for valid inputs:\n{error}", color=gui.MUTED,
                    ha="center", va="center", wrap=True, transform=ax.transAxes, fontsize=9)
            text = ""
        self.legend.configure(text=text)
        self.plot.draw_idle()

    # ------------------------------------------------------------------ actions
    def run(self) -> None:
        try:
            values = self.values()
            for key, (_, _, choice) in self.file_inputs.items():
                if choice and values.get(choice[0]) == choice[1] and not values.get(key):
                    self.browse(key)
                    values = self.values()
            self._busy("Running the check...")
            result = self.module.calculate(values)
        except (ValueError, KeyError, ZeroDivisionError) as error:
            self._busy("")
            gui.show_warning(f"The check could not run:\n\n{error}", title=self.title)
            return
        self._busy("")
        _save(self.name, {k: v for k, v in values.items() if k in {f.key for f in self.fields}})
        self.result = result
        self.result_values = self._key(values)
        self.show_text(self.module.result_parts(result))
        self.redraw()
        line, failures = self._summary(result)
        self.status.configure(text=line)

    def _summary(self, result) -> tuple[str, list[str]]:
        if hasattr(self.module, "window_summary"):
            return self.module.window_summary(result)
        return "Checked.", []

    def _busy(self, text: str) -> None:
        self.status.configure(text=text)
        self.root.configure(cursor="watch" if text else "")
        self.root.update_idletasks()

    def show_text(self, parts) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for text, tag in parts:
            self.text.insert("end", text, tag)
        self.text.configure(state="disabled")

    def print_results(self) -> None:
        if self.result is None:
            self.status.configure(text="Run the check first.")
            return
        from utilities import terminal

        if terminal.drawn() and hasattr(self.module, "terminal_report"):
            self.module.terminal_report(self.result).show()
        else:
            terminal.write(self.module.summary_text(self.result))
        self.status.configure(text="Printed in the terminal.")

    def save_pdf(self) -> None:
        if self.result is None:
            self.status.configure(text="Run the check first.")
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save the PDF report as",
                                            initialfile=self.report_name, defaultextension=".pdf",
                                            filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self._busy("Writing the PDF report (LaTeX)...")
        saved = self.module.export_pdf(self.result, path)
        self._busy("")
        if saved:
            self.saved_pdf = saved
            self.status.configure(text=f"PDF report saved: {saved}")
            print(f"PDF report saved: {saved}")
        else:
            from utilities.latex_help import missing_pdf_reason

            gui.show_warning("The PDF report was " + missing_pdf_reason(), title=self.title)

    def close(self) -> None:
        gui._close(self.root)

    def show(self):
        self.root.mainloop()
        return self.result


def open_window(module, name: str, title: str, report_name: str, command: str):
    """Open the window; returns the last result (None when nothing was run). A short
    summary is printed when it is closed."""
    window = DesignWindow(module, name, title, report_name)
    result = window.show()
    from utilities.run_summary import RunSummary

    summary = RunSummary(f"sdt {command} - {title}")
    if result is None:
        summary.add("Results", "nothing was run")
    else:
        line, failures = window._summary(result)
        summary.add("Last run", line)
        for failure in failures:
            summary.fail(failure)
        if window.saved_pdf:
            summary.file("PDF report", window.saved_pdf)
    summary.show(popup=False, echo=True)
    return result


__all__ = ["DesignWindow", "open_window", "style_axes", "ratio_colour", "PLOT", "math"]
