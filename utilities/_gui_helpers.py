"""
GUI Helper module providing interactive file picking and dual listbox selection dialogs.
"""

import ctypes
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

# Fix blurry text on High-DPI displays for all Tkinter popups
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def select_output_directory() -> str:
    """Opens a native Windows directory dialog to select an output folder."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder_path = filedialog.askdirectory(
        title="Select Output Folder for DXF Schedules"
    )
    root.destroy()
    return folder_path


def select_save_file(default_name="Composite_Column_Report") -> str:
    """Opens a native Windows Save As dialog to name the PDF."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # Forces dialog over Excel

    file_path = filedialog.asksaveasfilename(
        title="Save PDF Report As",
        initialfile=default_name,
        defaultextension=".pdf",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
    )

    root.destroy()
    return file_path


def select_save_path(
    title: str, default_name: str, extension: str, description: str
) -> str:
    """Opens a native Windows Save As dialog for a file of the given extension."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.asksaveasfilename(
        title=title,
        initialfile=default_name,
        defaultextension=extension,
        filetypes=[(description, f"*{extension}"), ("All files", "*.*")],
    )
    root.destroy()
    return file_path


def select_open_path(title: str, extension: str, description: str) -> str:
    """Opens a native Windows file dialog for one file of the given extension."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.askopenfilename(
        title=title, filetypes=[(description, f"*{extension}"), ("All files", "*.*")]
    )
    root.destroy()
    return file_path


def select_etabs_file() -> str:
    """Opens a native Windows file dialog to select an ETABS .edb file."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_path = filedialog.askopenfilename(
        title="Select ETABS Model File",
        filetypes=[("ETABS Database Files", "*.edb"), ("All Files", "*.*")],
    )
    root.destroy()
    return file_path


def select_option(
    title: str, prompt: str, options: list[str], default_index: int = 0
) -> str | None:
    """Show a screen-centered dialog with one choice per option.

    Returns the chosen option, or ``None`` when the window is closed without
    confirming.
    """
    if not options:
        raise ValueError("select_option needs at least one option.")
    result: dict[str, str | None] = {"value": None}

    root = tk.Tk()
    root.title(title)
    root.withdraw()  # shown once it has been sized to its content
    width = 460 if max(len(line) for line in prompt.split("\n")) < 80 else 620
    root.resizable(False, False)
    root.attributes("-topmost", True)

    def close():
        """Tear the window down without raising Tcl errors."""
        try:
            root.quit()
            root.destroy()
        except tk.TclError:
            pass

    def confirm():
        result["value"] = options[choice.get()]
        close()

    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Return>", lambda _event: confirm())  # Enter confirms the selection

    tk.Label(
        root, text=prompt, font=("Arial", 9, "bold"), wraplength=width - 40,
        justify="left" if "\n" in prompt else "center",
    ).pack(pady=(18, 8), padx=20)
    choice = tk.IntVar(root, value=min(max(default_index, 0), len(options) - 1))
    frame_options = tk.Frame(root)
    frame_options.pack(fill=tk.X, padx=30)
    for index, option in enumerate(options):
        tk.Radiobutton(
            frame_options,
            text=option,
            variable=choice,
            value=index,
            font=("Arial", 9),
            anchor="w",
            justify="left",
            wraplength=width - 80,
        ).pack(fill=tk.X, anchor="w")

    tk.Button(
        root,
        text="OK / Confirm",
        width=16,
        height=1,
        bg="#007ACC",
        fg="white",
        font=("Arial", 9, "bold"),
        command=confirm,
    ).pack(side=tk.BOTTOM, pady=(12, 15))

    # The height follows the prompt, so a long list of lines still fits.
    root.update_idletasks()
    height = root.winfo_reqheight()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = max((root.winfo_screenheight() // 2) - (height // 2), 0)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.deiconify()

    try:
        root.lift()
        root.focus_force()
        root.mainloop()
    except tk.TclError:
        pass
    return result["value"]


def enter_values(
    title: str, prompt: str, labels: list[str], defaults: dict[str, str] | None = None
) -> dict[str, str] | None:
    """Show a screen-centered dialog with one text box per label.

    Returns ``{label: typed text}``, or ``None`` when the window is closed
    without confirming.
    """
    if not labels:
        raise ValueError("enter_values needs at least one label.")
    result: dict[str, dict[str, str] | None] = {"value": None}

    root = tk.Tk()
    root.title(title)
    root.withdraw()  # shown once it has been sized to its content
    root.resizable(False, False)
    root.attributes("-topmost", True)
    # Wide enough to show the longest value in full, and never a cramped box.
    box_width = max([28] + [len(str(value)) + 4 for value in (defaults or {}).values()])

    def close():
        """Tear the window down without raising Tcl errors."""
        try:
            root.quit()
            root.destroy()
        except tk.TclError:
            pass

    def confirm():
        result["value"] = {label: entry.get() for label, entry in entries.items()}
        close()

    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Return>", lambda _event: confirm())  # Enter confirms the values

    prompt_label = tk.Label(root, text=prompt, font=("Arial", 9, "bold"), justify="center")
    prompt_label.pack(pady=(18, 8), padx=30)
    frame_entries = tk.Frame(root)
    frame_entries.pack(fill=tk.X, padx=30)
    frame_entries.columnconfigure(1, weight=1)
    entries: dict[str, tk.Entry] = {}
    for row, label in enumerate(labels):
        tk.Label(frame_entries, text=label, font=("Arial", 9), anchor="w").grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 12)
        )
        entry = tk.Entry(frame_entries, font=("Arial", 10), width=box_width)
        entry.insert(0, (defaults or {}).get(label, ""))
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        entries[label] = entry

    tk.Button(
        root,
        text="OK / Confirm",
        width=16,
        height=1,
        bg="#007ACC",
        fg="white",
        font=("Arial", 9, "bold"),
        command=confirm,
    ).pack(side=tk.BOTTOM, pady=(18, 15))

    # Size the window to the labels and boxes, wrap the prompt to that width,
    # then centre it on the screen.
    root.update_idletasks()
    prompt_label.config(wraplength=max(frame_entries.winfo_reqwidth(), 360))
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.deiconify()

    try:
        root.lift()
        root.focus_force()
        entries[labels[0]].focus_set()
        root.mainloop()
    except tk.TclError:
        pass
    return result["value"]


class DualListboxSelector:
    """Creates a screen-centered dual listbox popup window with extended selection and select all capabilities."""

    def __init__(self, title: str, available_items: list[str]):
        self.selected_items: list[str] = []
        self.available_items = available_items

        self.root = tk.Tk()
        self.root.title(title)

        # Handle early window close ('X' button) safely
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Screen Centering Logic
        width, height = 620, 420
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w // 2) - (width // 2)
        y = (screen_h // 2) - (height // 2)

        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._build_ui()

    def _build_ui(self):
        # Top Frame containing both listboxes and center action buttons
        frame_top = tk.Frame(self.root)
        frame_top.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=15, pady=10)

        # ------------------- Left Listbox (Available Items) -------------------
        frame_left = tk.Frame(frame_top)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(frame_left, text="Available Items", font=("Arial", 9, "bold")).pack(
            pady=(0, 5)
        )

        # tk.EXTENDED enables Shift+Click range select and Ctrl+Click toggle select
        self.lb_available = tk.Listbox(
            frame_left, selectmode=tk.EXTENDED, exportselection=False
        )
        self.lb_available.pack(fill=tk.BOTH, expand=True)
        for item in self.available_items:
            self.lb_available.insert(tk.END, item)

        # Left Action Buttons (Select All / Unselect All)
        frame_left_btns = tk.Frame(frame_left)
        frame_left_btns.pack(fill=tk.X, pady=(5, 0))
        btn_sel_all_avail = tk.Button(
            frame_left_btns, text="Select All", command=self._select_all_available
        )
        btn_sel_all_avail.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        btn_unsel_all_avail = tk.Button(
            frame_left_btns, text="Unselect All", command=self._unselect_all_available
        )
        btn_unsel_all_avail.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # ------------------- Center Action Buttons (>>> / <<<) -------------------
        frame_mid = tk.Frame(frame_top)
        frame_mid.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        frame_mid_inner = tk.Frame(frame_mid)
        frame_mid_inner.pack(expand=True)  # Vertically centers transfer buttons

        btn_add = tk.Button(
            frame_mid_inner, text=">>>", width=8, command=self._add_items
        )
        btn_add.pack(pady=6)
        btn_remove = tk.Button(
            frame_mid_inner, text="<<<", width=8, command=self._remove_items
        )
        btn_remove.pack(pady=6)

        # ------------------- Right Listbox (Selected Items) -------------------
        frame_right = tk.Frame(frame_top)
        frame_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(frame_right, text="Selected Items", font=("Arial", 9, "bold")).pack(
            pady=(0, 5)
        )

        # tk.EXTENDED enables Shift+Click range select and Ctrl+Click toggle select
        self.lb_selected = tk.Listbox(
            frame_right, selectmode=tk.EXTENDED, exportselection=False
        )
        self.lb_selected.pack(fill=tk.BOTH, expand=True)

        # Right Action Buttons (Select All / Unselect All)
        frame_right_btns = tk.Frame(frame_right)
        frame_right_btns.pack(fill=tk.X, pady=(5, 0))
        btn_sel_all_sel = tk.Button(
            frame_right_btns, text="Select All", command=self._select_all_selected
        )
        btn_sel_all_sel.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        btn_unsel_all_sel = tk.Button(
            frame_right_btns, text="Unselect All", command=self._unselect_all_selected
        )
        btn_unsel_all_sel.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # ------------------- Bottom Frame with Confirm Button -------------------
        frame_bottom = tk.Frame(self.root)
        frame_bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 15))

        btn_confirm = tk.Button(
            frame_bottom,
            text="OK / Confirm",
            width=16,
            height=1,
            bg="#007ACC",
            fg="white",
            font=("Arial", 9, "bold"),
            command=self._confirm,
        )
        btn_confirm.pack(anchor=tk.CENTER)

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
        for idx in reversed(selected_indices):
            item = self.lb_available.get(idx)
            self.lb_selected.insert(tk.END, item)
            self.lb_available.delete(idx)

    def _remove_items(self):
        selected_indices = list(self.lb_selected.curselection())
        for idx in reversed(selected_indices):
            item = self.lb_selected.get(idx)
            self.lb_available.insert(tk.END, item)
            self.lb_selected.delete(idx)

    def _confirm(self):
        self.selected_items = list(self.lb_selected.get(0, tk.END))
        if not self.selected_items:
            messagebox.showwarning("Warning", "Please select at least one item.")
            return
        self._on_close()

    def _on_close(self):
        """Safely tears down the Tkinter loop without causing Tcl errors."""
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass

    def show(self) -> list[str]:
        """Displays dialog safely and blocks until user confirms or closes."""
        try:
            if self.root.winfo_exists():
                self.root.lift()
                self.root.focus_force()
                self.root.mainloop()
        except tk.TclError:
            pass

        return self.selected_items


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
        """Starts the loading popup."""
        gui_script = f"""import ctypes
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

root = tk.Tk()
root.title("Background Processing")

width, height = 380, 130
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()
x = (screen_w // 2) - (width // 2)
y = (screen_h // 2) - (height // 2)

root.geometry(f"{{width}}x{{height}}+{{x}}+{{y}}")
root.resizable(False, False)
root.attributes("-topmost", True)
root.protocol("WM_DELETE_WINDOW", lambda: None)

label = tk.Label(root, text="{self.message}", font=("Arial", 9, "bold"), wraplength=340)
label.pack(pady=(22, 10))

progress = ttk.Progressbar(root, mode="indeterminate", length=300)
progress.pack(pady=5)
progress.start(10)

# Progress lines sent by LoadingWindow.update() arrive on stdin, one per line.
detail = tk.Label(root, text="", font=("Arial", 9), wraplength=340, justify="center")
updates = queue.Queue()

def read_updates():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        for line in sys.stdin:
            updates.put(line.rstrip("\\n").replace("\\t", "\\n"))
    except Exception:
        pass

def show_updates():
    text = None
    while not updates.empty():
        text = updates.get_nowait()
    if text is not None:
        if not detail.winfo_ismapped():
            detail.pack(pady=(6, 0))
        detail.config(text=text)
        root.update_idletasks()
        needed = height + detail.winfo_reqheight() + 16
        if needed > root.winfo_height():
            root.geometry(f"{{width}}x{{needed}}+{{x}}+{{y}}")
    root.after(100, show_updates)

threading.Thread(target=read_updates, daemon=True).start()
root.after(100, show_updates)

root.lift()
root.focus_force()
root.mainloop()
"""
        self.proc = subprocess.Popen(
            [sys.executable, "-c", gui_script],
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
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


def show_warning(message: str, title: str = "Warning", topmost: bool = True) -> None:
    """Displays a GUI warning popup box with a custom message and title."""
    root = tk.Tk()
    root.withdraw()  # Hide the main Tkinter root window

    if topmost:
        root.attributes("-topmost", True)  # Forces dialog over Excel/ETABS

    messagebox.showwarning(title=title, message=message, parent=root)
    root.destroy()
