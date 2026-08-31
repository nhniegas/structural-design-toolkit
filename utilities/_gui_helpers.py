"""
GUI Helper module providing interactive file picking and dual listbox selection dialogs.
"""

import ctypes
import multiprocessing
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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


class DualListboxSelector:
    """Creates a screen-centered dual listbox popup window."""

    def __init__(self, title: str, available_items: list[str]):
        self.selected_items: list[str] = []
        self.available_items = available_items

        self.root = tk.Tk()
        self.root.title(title)

        # Screen Centering Logic
        width, height = 540, 380
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

        # Left Listbox (Available Items)
        frame_left = tk.Frame(frame_top)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(frame_left, text="Available Items", font=("Arial", 9, "bold")).pack(
            pady=(0, 5)
        )

        self.lb_available = tk.Listbox(
            frame_left, selectmode=tk.MULTIPLE, exportselection=False
        )
        self.lb_available.pack(fill=tk.BOTH, expand=True)
        for item in self.available_items:
            self.lb_available.insert(tk.END, item)

        # Center Action Buttons (Vertically Centered)
        frame_mid = tk.Frame(frame_top)
        frame_mid.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        frame_mid_inner = tk.Frame(frame_mid)
        frame_mid_inner.pack(expand=True)  # Places inner frame in the vertical center

        btn_add = tk.Button(
            frame_mid_inner, text=">>>", width=8, command=self._add_items
        )
        btn_add.pack(pady=6)
        btn_remove = tk.Button(
            frame_mid_inner, text="<<<", width=8, command=self._remove_items
        )
        btn_remove.pack(pady=6)

        # Right Listbox (Selected Items)
        frame_right = tk.Frame(frame_top)
        frame_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(frame_right, text="Selected Items", font=("Arial", 9, "bold")).pack(
            pady=(0, 5)
        )

        self.lb_selected = tk.Listbox(
            frame_right, selectmode=tk.MULTIPLE, exportselection=False
        )
        self.lb_selected.pack(fill=tk.BOTH, expand=True)

        # Bottom Frame with Centered Confirm Button
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
        self.root.destroy()

    def show(self) -> list[str]:
        """Displays dialog and blocks until user confirms selection."""
        self.root.lift()
        self.root.focus_force()
        self.root.mainloop()
        return self.selected_items


class LoadingWindow:
    """Displays a process-isolated loading window centered on screen during heavy ETABS tasks."""

    def __init__(self, message: str = "Processing, please wait..."):
        self.message = message
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Starts the loading popup."""
        gui_script = f"""import ctypes
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

root.lift()
root.focus_force()
root.mainloop()
"""
        self.proc = subprocess.Popen([sys.executable, "-c", gui_script])
        time.sleep(0.5)

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
