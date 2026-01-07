#!/usr/bin/env python3
"""
3mf_single_plate_launcher.py

Thread-safe Tkinter launcher (macOS-safe):
- Calls scripts via subprocess (does NOT import them)
- Streams stdout/stderr to UI using a queue + .after polling
- Never touches Tk widgets from a background thread

Assumes these scripts are next to this UI file:
- convert_3mf_to_single_plate.py
- batch_convert_3mf_to_single_plate.py
"""

from __future__ import annotations

import sys
import threading
import subprocess
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("3MF Single-Plate Launcher (Orca/Bambu)")
        self.geometry("860x580")
        self.minsize(860, 580)

        self.base_dir = Path(__file__).resolve().parent
        self.convert_script = self.base_dir / "convert_3mf_to_single_plate.py"
        self.batch_script = self.base_dir / "batch_convert_3mf_to_single_plate.py"

        self.mode = tk.StringVar(value="file")  # file | dir
        self.source = tk.StringVar(value="")
        self.dest = tk.StringVar(value="")
        self.recursive = tk.BooleanVar(value=True)
        self.dry_run = tk.BooleanVar(value=False)

        # Queue for thread -> UI messages
        self.ui_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._running = False

        self._build()
        self._refresh_script_status()
        self._on_mode_change()

        # Start queue polling loop on main thread
        self.after(50, self._poll_queue)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Mode:").pack(side="left")
        ttk.Radiobutton(top, text="Single File", value="file", variable=self.mode, command=self._on_mode_change).pack(side="left", padx=8)
        ttk.Radiobutton(top, text="Batch Directory", value="dir", variable=self.mode, command=self._on_mode_change).pack(side="left", padx=8)

        status = ttk.LabelFrame(self, text="Script Status (must be in the same folder as this UI)")
        status.pack(fill="x", **pad)
        self.status_lbl = ttk.Label(status, text="")
        self.status_lbl.pack(anchor="w", padx=10, pady=6)

        io = ttk.LabelFrame(self, text="Input / Output")
        io.pack(fill="x", **pad)

        self._path_row(io, "Source", self.source, self._browse_source)
        self._path_row(io, "Destination (Output Folder)", self.dest, self._browse_dest)

        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)

        self.rec_chk = ttk.Checkbutton(opts, text="Recursive (Batch mode)", variable=self.recursive)
        self.rec_chk.pack(side="left", padx=10, pady=6)

        self.dry_chk = ttk.Checkbutton(opts, text="Dry run (Batch mode)", variable=self.dry_run)
        self.dry_chk.pack(side="left", padx=10, pady=6)

        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)

        self.run_btn = ttk.Button(actions, text="Run", command=self._run_clicked)
        self.run_btn.pack(side="left")

        ttk.Button(actions, text="Clear Log", command=self._clear_log).pack(side="left", padx=10)

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log = tk.Text(log_frame, wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def _path_row(self, parent, label: str, var: tk.StringVar, browse_cmd):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=6)
        ttk.Label(row, text=label, width=28).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="Browse…", command=browse_cmd).pack(side="left")

    def _refresh_script_status(self) -> None:
        missing = []
        if not self.convert_script.exists():
            missing.append(self.convert_script.name)
        if not self.batch_script.exists():
            missing.append(self.batch_script.name)

        if missing:
            self.status_lbl.configure(text=f"Missing script(s): {', '.join(missing)}\nFolder: {self.base_dir}")
        else:
            self.status_lbl.configure(text=f"OK: Found both scripts.\nFolder: {self.base_dir}")

    def _on_mode_change(self) -> None:
        if self.mode.get() == "file":
            self.rec_chk.state(["disabled"])
            self.dry_chk.state(["disabled"])
        else:
            self.rec_chk.state(["!disabled"])
            self.dry_chk.state(["!disabled"])

    def _browse_source(self) -> None:
        if self.mode.get() == "file":
            p = filedialog.askopenfilename(
                title="Select .gcode.3mf file",
                filetypes=[("GCODE.3MF", "*.gcode.3mf"), ("All files", "*.*")],
            )
        else:
            p = filedialog.askdirectory(title="Select source directory")
        if p:
            self.source.set(p)

    def _browse_dest(self) -> None:
        p = filedialog.askdirectory(title="Select destination (output) directory")
        if p:
            self.dest.set(p)

    def _clear_log(self) -> None:
        self.log.delete("1.0", "end")

    def _log_mainthread(self, s: str) -> None:
        """Only call this from main thread."""
        self.log.insert("end", s)
        self.log.see("end")

    def _validate(self) -> tuple[bool, str]:
        self._refresh_script_status()
        if not self.convert_script.exists():
            return False, f"Missing {self.convert_script.name} next to the UI."
        if not self.batch_script.exists():
            return False, f"Missing {self.batch_script.name} next to the UI."

        src = Path(self.source.get().strip())
        dst = Path(self.dest.get().strip())

        if not src.as_posix():
            return False, "Please select a Source."
        if not dst.as_posix():
            return False, "Please select a Destination folder."

        if self.mode.get() == "file":
            if not src.is_file():
                return False, "Source must be a file in Single File mode."
        else:
            if not src.is_dir():
                return False, "Source must be a directory in Batch Directory mode."

        try:
            dst.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return False, f"Cannot create destination folder: {e}"

        return True, ""

    def _build_command(self) -> list[str]:
        src = self.source.get().strip()
        dst = self.dest.get().strip()

        if self.mode.get() == "file":
            return [sys.executable, str(self.convert_script), src, "-o", dst]

        cmd = [sys.executable, str(self.batch_script), src, "-o", dst]
        if self.recursive.get():
            cmd.append("--recursive")
        if self.dry_run.get():
            cmd.append("--dry-run")
        return cmd

    def _run_clicked(self) -> None:
        if self._running:
            return

        ok, msg = self._validate()
        if not ok:
            messagebox.showerror("Validation Error", msg)
            return

        cmd = self._build_command()

        # UI setup (main thread)
        self._running = True
        self.run_btn.state(["disabled"])
        self._log_mainthread("\n=== RUN START ===\n")
        self._log_mainthread("Command:\n  " + " ".join(cmd) + "\n\n")

        # Start worker thread
        t = threading.Thread(target=self._worker_run_subprocess, args=(cmd,), daemon=True)
        t.start()

    def _worker_run_subprocess(self, cmd: list[str]) -> None:
        """Background thread: run subprocess, push output lines to queue."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            assert proc.stdout is not None
            assert proc.stderr is not None

            for line in proc.stdout:
                self.ui_queue.put(("log", line))
            for line in proc.stderr:
                self.ui_queue.put(("log", line))

            rc = proc.wait()
            self.ui_queue.put(("exit", rc))

        except Exception as e:
            self.ui_queue.put(("error", str(e)))

    def _poll_queue(self) -> None:
        """Main thread: process queued UI updates."""
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()

                if kind == "log":
                    self._log_mainthread(str(payload))

                elif kind == "error":
                    self._log_mainthread(f"\nERROR: {payload}\n")
                    messagebox.showerror("Error", str(payload))
                    self._finish_run()

                elif kind == "exit":
                    rc = int(payload)
                    self._log_mainthread(f"\nExit code: {rc}\n")
                    if rc == 0:
                        messagebox.showinfo("Done", "Completed successfully. See log for output filenames.")
                    else:
                        messagebox.showerror("Failed", f"Process exited with code {rc}. See log for details.")
                    self._finish_run()

        except queue.Empty:
            pass
        finally:
            self.after(50, self._poll_queue)

    def _finish_run(self) -> None:
        """Main thread cleanup after a run."""
        if not self._running:
            return
        self._log_mainthread("=== RUN END ===\n")
        self.run_btn.state(["!disabled"])
        self._running = False


if __name__ == "__main__":
    Launcher().mainloop()
