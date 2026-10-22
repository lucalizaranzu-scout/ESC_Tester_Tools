#!/usr/bin/env python3
"""Vertiq ESC PWM Configurator - one-click switch of a Vertiq module to/from PWM control."""

import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from serial.tools import list_ports

import iquart

APP_DIR = Path(__file__).resolve().parent
PROFILE_PATH = APP_DIR / "profile.json"
BACKUP_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "vertiq-pwm-configurator" / "backups"

DEFAULT_PROFILE = {"port": "/dev/ttyUSB0", "baud": 115200, "pwm": {}, "can": {}}


def load_profile():
    try:
        with open(PROFILE_PATH) as f:
            return {**DEFAULT_PROFILE, **json.load(f)}
    except FileNotFoundError:
        return dict(DEFAULT_PROFILE)


def save_profile(profile):
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")


def resolve(values):
    """{"client.param": value} -> [(Param, value)], rejecting unknown keys."""
    out = []
    for key, value in values.items():
        if key.startswith("_"):
            continue
        if key not in iquart.PARAMS_BY_KEY:
            raise ValueError(f"Unknown parameter in profile: {key}")
        out.append((iquart.PARAMS_BY_KEY[key], value))
    return out


class App:
    def __init__(self, root):
        self.root = root
        self.profile = load_profile()
        self.module = None
        self.uid = None
        self.busy = False
        self.events = queue.Queue()

        root.title("Vertiq ESC PWM Configurator")
        root.minsize(680, 420)
        self._build_ui()
        self._refresh_buttons()
        root.after(50, self._poll_events)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _build_ui(self):
        menubar = tk.Menu(self.root)
        adv = tk.Menu(menubar, tearoff=0)
        adv.add_command(label="Capture PWM profile from connected motor…",
                        command=lambda: self._capture("pwm"))
        adv.add_command(label="Capture CAN profile from connected motor…",
                        command=lambda: self._capture("can"))
        adv.add_separator()
        adv.add_command(label="Read all parameters to log", command=self._dump_all)
        adv.add_command(label="Show profile / backup locations", command=self._show_paths)
        menubar.add_cascade(label="Advanced", menu=adv)
        self.root.config(menu=menubar)
        self.adv_menu = adv

        pad = {"padx": 10, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Port:").pack(side="left")
        ports = sorted({p.device for p in list_ports.comports()} | {self.profile["port"]})
        self.port_var = tk.StringVar(value=self.profile["port"])
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, values=ports, width=18)
        self.port_box.pack(side="left", padx=(4, 12))
        ttk.Label(top, text=f"{self.profile['baud']} baud").pack(side="left")

        style = ttk.Style()
        style.configure("Big.TButton", font=("TkDefaultFont", 12, "bold"), padding=10)

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.connect_btn = ttk.Button(btns, text="Connect", style="Big.TButton", command=self._toggle_connect)
        self.set_btn = ttk.Button(btns, text="Set PWM Parameters", style="Big.TButton", command=self._set_pwm)
        self.revert_btn = ttk.Button(btns, text="Revert to CAN", style="Big.TButton", command=self._revert)
        self.restart_btn = ttk.Button(btns, text="Restart Motor", style="Big.TButton", command=self._restart)
        for i, b in enumerate((self.connect_btn, self.set_btn, self.revert_btn, self.restart_btn)):
            b.grid(row=0, column=i, sticky="ew", padx=4)
            btns.columnconfigure(i, weight=1)

        self.status_var = tk.StringVar(value="Not connected")
        self.status_lbl = tk.Label(self.root, textvariable=self.status_var, font=("TkDefaultFont", 11, "bold"),
                                   anchor="w", fg="#555")
        self.status_lbl.pack(fill="x", padx=14)
        self.state_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.state_var, anchor="w").pack(fill="x", padx=14)

        logf = ttk.Frame(self.root)
        logf.pack(fill="both", expand=True, **pad)
        self.log_txt = tk.Text(logf, height=14, wrap="word", state="disabled", font=("TkFixedFont", 9))
        sb = ttk.Scrollbar(logf, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        if not self.profile["pwm"]:
            self.log("PWM profile is empty. Configure one motor with IQ Control Center, connect it here and use "
                     "Advanced → Capture PWM profile.")

    def log(self, msg):
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", time.strftime("%H:%M:%S  ") + msg + "\n")
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    def _set_status(self, text, color="#555"):
        self.status_var.set(text)
        self.status_lbl.configure(fg=color)

    def _refresh_buttons(self):
        connected = self.module is not None
        idle = not self.busy
        self.connect_btn.configure(text="Disconnect" if connected else "Connect",
                                   state="normal" if idle else "disabled")
        for b in (self.set_btn, self.revert_btn, self.restart_btn):
            b.configure(state="normal" if connected and idle else "disabled")
        self.port_box.configure(state="disabled" if connected or not idle else "normal")
        for i in (0, 1, 3):
            self.adv_menu.entryconfigure(i, state="normal" if connected and idle else "disabled")

    # ---------- background work ----------
    def _run(self, label, fn):
        """Run fn() in a worker thread; fn may call self.emit(...) to log."""
        self.busy = True
        self._refresh_buttons()
        self._set_status(label + "…", "#555")

        def worker():
            try:
                result = fn()
                self.events.put(("done", result))
            except Exception as e:  # report every failure in the UI
                self.events.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

    def emit(self, msg):
        self.events.put(("log", msg))

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self.busy = False
                    if callable(payload):
                        payload()
                    self._refresh_buttons()
                elif kind == "error":
                    self.busy = False
                    self.log(f"ERROR: {payload}")
                    self._set_status(f"Error: {payload}", "#b00020")
                    if isinstance(payload, OSError) and self.module:
                        self._drop_connection()
                    self._refresh_buttons()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_events)

    # ---------- actions ----------
    def _toggle_connect(self):
        if self.module:
            self._drop_connection()
            self.log("Disconnected.")
            self._set_status("Not connected")
            self.state_var.set("")
            self._refresh_buttons()
            return

        port = self.port_var.get().strip()

        def work():
            self.emit(f"Connecting to {port} @ {self.profile['baud']}…")
            mod = iquart.find_module(port, self.profile["baud"])
            uid = mod.read_uid()
            fw = mod.get(iquart.FIRMWARE_PARAM)
            if uid is None:
                mod.close()
                raise iquart.CommError("Module answered but UID could not be read.")
            state = self._describe_state(mod, uid)

            def finish():
                self.module, self.uid = mod, uid
                if port != self.profile["port"]:
                    self.profile["port"] = port
                    save_profile(self.profile)
                fw_txt = f"0x{fw:08X}" if fw is not None else "?"
                self.log(f"Connected: module id {mod.obj}, UID {uid}, firmware {fw_txt}")
                self._set_status(f"Connected  (UID {uid})", "#1b7f3b")
                self.state_var.set(state)
            return finish

        self._run("Connecting", work)

    def _drop_connection(self):
        if self.module:
            self.module.close()
        self.module = None
        self.uid = None

    def _matches(self, mod, which):
        targets = resolve(self.profile[which])
        return bool(targets) and all(iquart.values_equal(p, mod.get(p), v) for p, v in targets)

    def _describe_state(self, mod, uid):
        if self._matches(mod, "pwm"):
            txt = "Current mode: PWM (ESC)"
        elif self._matches(mod, "can"):
            txt = "Current mode: CAN"
        else:
            txt = "Current mode: matches neither the PWM nor the CAN profile"
        if not self.profile["can"]:
            txt += "   ·   no CAN profile captured yet"
        return txt

    @staticmethod
    def _backup_path(uid):
        return BACKUP_DIR / f"{uid}.json"

    def _write_and_verify(self, mod, items):
        """Write [(Param, value)] with save, then read back. Returns list of failures."""
        for p, v in items:
            self.emit(f"  set {p.key} = {v}")
            mod.set(p, v)
        time.sleep(0.1)
        failures = []
        for p, v in items:
            got = mod.get(p)
            if not iquart.values_equal(p, got, v):
                failures.append(f"{p.key}: wanted {v}, read back {got}")
        return failures

    def _set_pwm(self):
        try:
            targets = resolve(self.profile["pwm"])
        except ValueError as e:
            messagebox.showerror("Profile error", str(e))
            return
        if not targets:
            messagebox.showwarning("No PWM profile",
                                   "The PWM profile is empty.\n\nConfigure one motor with IQ Control Center, "
                                   "connect it here, then use Advanced → Capture PWM profile.")
            return
        mod, uid = self.module, self.uid

        def work():
            self.emit("Reading current parameters…")
            current = {p.key: mod.get(p) for p, _ in targets}
            missing = [k for k, v in current.items() if v is None]
            if missing:
                raise iquart.CommError("No reply for: " + ", ".join(missing))
            to_write = [(p, v) for p, v in targets if not iquart.values_equal(p, current[p.key], v)]
            if not to_write:
                self.emit("Already configured for PWM - nothing to change.")
            else:
                backup = self._backup_path(uid)
                if not backup.exists():
                    full = {p.key: mod.get(p) for p in iquart.MANAGED_PARAMS}
                    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    with open(backup, "w") as f:
                        json.dump({"uid": uid, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "values": {k: v for k, v in full.items() if v is not None}}, f, indent=2)
                    self.emit(f"Backed up original values to {backup}")
                else:
                    self.emit(f"Keeping existing backup {backup}")
                self.emit(f"Writing {len(to_write)} parameter(s)…")
                failures = self._write_and_verify(mod, to_write)
                if failures:
                    raise iquart.CommError("Verification failed: " + "; ".join(failures))
                self._restart_module(mod, uid)
            state = self._describe_state(mod, uid)

            def finish():
                self.log("PWM parameters set, saved and applied.")
                self._set_status("PWM parameters set ✔", "#1b7f3b")
                self.state_var.set(state)
            return finish

        self._run("Setting PWM parameters", work)

    def _revert(self):
        mod, uid = self.module, self.uid
        backup = self._backup_path(uid)
        if self.profile["can"]:
            values = self.profile["can"]
            source = "CAN profile"
        elif backup.exists():
            with open(backup) as f:
                data = json.load(f)
            values = data["values"]
            source = f"backup taken {data.get('saved', '?')} (no CAN profile captured)"
        else:
            messagebox.showwarning("No CAN profile",
                                   "No CAN profile has been captured and there is no backup for this motor.\n\n"
                                   "Connect a motor that is set up for CAN and use Advanced → Capture CAN profile.")
            return
        try:
            items = resolve(values)
        except ValueError as e:
            messagebox.showerror("Revert error", str(e))
            return
        if not messagebox.askyesno("Revert to CAN", f"Write {len(items)} parameter(s) from the {source}?"):
            return

        def work():
            self.emit(f"Reverting to CAN from {source}…")
            failures = self._write_and_verify(mod, items)
            if failures:
                raise iquart.CommError("Verification failed: " + "; ".join(failures))
            if backup.exists():
                backup.rename(backup.with_suffix(".reverted.json"))
            self._restart_module(mod, uid)
            state = self._describe_state(mod, uid)

            def finish():
                self.log("CAN parameters set, saved and applied.")
                self._set_status("Reverted to CAN ✔", "#1b7f3b")
                self.state_var.set(state)
            return finish

        self._run("Reverting parameters", work)

    def _capture(self, which):
        label = which.upper()
        hint = f"Make sure this motor is fully configured for {label} in IQ Control Center."
        if not messagebox.askyesno(f"Capture {label} profile",
                                   f"{hint}\n\nThis reads every managed parameter from the connected motor and "
                                   f"overwrites the '{which}' section of profile.json. Continue?"):
            return
        mod = self.module

        def work():
            values, skipped = {}, []
            for p in iquart.MANAGED_PARAMS:
                v = mod.get(p)
                if v is None:
                    skipped.append(p.key)
                else:
                    values[p.key] = round(v, 6) if isinstance(v, float) else v

            def finish():
                self.profile[which] = values
                save_profile(self.profile)
                self.log(f"Captured {len(values)} parameters into the {label} profile ({PROFILE_PATH}).")
                if skipped:
                    self.log("Not supported by this firmware (skipped): " + ", ".join(skipped))
                self._set_status(f"{label} profile captured ✔", "#1b7f3b")
            return finish

        self._run(f"Capturing {label} profile", work)

    def _restart_module(self, mod, uid):
        """Reboot the motor so new settings take effect (instead of a power cycle)."""
        self.emit("Restarting motor…")
        new_uid = mod.reboot()
        if new_uid is None:
            raise iquart.CommError("Motor did not answer after restart - power-cycle it and reconnect.")
        if new_uid != uid:
            raise iquart.CommError(f"A different motor answered after restart ({new_uid}).")
        self.emit("Motor restarted and is responding.")

    def _restart(self):
        mod, uid = self.module, self.uid

        def work():
            self._restart_module(mod, uid)
            state = self._describe_state(mod, uid)

            def finish():
                self._set_status("Motor restarted ✔", "#1b7f3b")
                self.state_var.set(state)
            return finish

        self._run("Restarting motor", work)

    def _dump_all(self):
        mod = self.module

        def work():
            for p in iquart.MANAGED_PARAMS:
                v = mod.get(p)
                self.emit(f"  {p.key:60} {'(no reply)' if v is None else v} {p.unit if v is not None else ''}")
            return lambda: self._set_status("Read complete", "#1b7f3b")

        self._run("Reading parameters", work)

    def _show_paths(self):
        messagebox.showinfo("Locations", f"Profile:\n{PROFILE_PATH}\n\nPer-motor backups:\n{BACKUP_DIR}")

    def _on_close(self):
        self._drop_connection()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
