"""Shared setup for Flow's tests.

Loads ../flow.pyw with throwaway settings in a temp folder, so the tests never touch your real
settings, stats, startup entry or tray pin. Keyboard/mouse idle time is faked through `m.FAKE_IDLE`.
"""
import ctypes
import importlib.util
import os
import tempfile
import time
import tkinter as tk
import traceback

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_flow(**defaults):
    spec = importlib.util.spec_from_file_location("flow", os.path.join(ROOT, "flow.pyw"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tmp = tempfile.mkdtemp(prefix="flow-test-")
    m.CONFIG_PATH = os.path.join(tmp, "config.json")
    m.STATS_PATH = os.path.join(tmp, "stats.json")
    # Nothing outside the temp folder: no registry, no tray pinning, no sound, no network.
    m.migrate_startup = lambda: None
    m.pin_tray_icon = lambda: None
    m.set_startup = lambda on: None
    m.get_startup = lambda: False
    m.chime = lambda v: None
    m.sent = []
    m.send_phone = lambda *a, **k: m.sent.append(a) or [True]
    m.FAKE_IDLE = [1.0]  # seconds since the last keyboard/mouse input
    m.idle_seconds = lambda: m.FAKE_IDLE[0]
    m.DEFAULTS.update(welcome_popup=False, **defaults)
    return m


class Report:
    """Collects checks, prints a summary, and exits with 0 (all passed) or 1."""

    def __init__(self, title):
        self.title, self.results, self.errors = title, [], []
        self.code = 1  # stays a failure unless finish() runs and everything passed
        tk.Tk.report_callback_exception = self._on_error

    def _on_error(self, *exc):  # Tk passes (type, value, traceback)
        self.errors.append(exc)
        print("ERROR", "".join(traceback.format_exception(*exc)), flush=True)

    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))

    def crashed(self, name):
        self.results.append((f"{name} crashed", False, traceback.format_exc(limit=4)))

    def finish(self, app):
        passed = sum(ok for _, ok, _ in self.results)
        print(f"\n== {self.title} ==")
        for name, ok, detail in self.results:
            print(("PASS " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        print(f"{passed}/{len(self.results)} passed, {len(self.errors)} unexpected errors", flush=True)
        self.code = 0 if passed == len(self.results) and not self.errors else 1
        app.handle("quit")

    def watchdog(self, root, seconds):
        def stuck():
            print(f"TIMEOUT: {self.title} got stuck", flush=True)
            os._exit(2)
        root.after(seconds * 1000, stuck)


def run_steps(app, report, steps, gap_ms=300):
    """Run step functions one after another inside Tk's event loop."""
    def run(i=0):
        if i >= len(steps):
            return report.finish(app)
        try:
            wait = steps[i]()
        except Exception:
            report.crashed(steps[i].__name__)
            wait = None
        app.root.after(wait or gap_ms, run, i + 1)
    app.root.after(500, run)


def tick(app, n=1, gap=1.0):
    """Advance Flow's once-a-second timer by `gap` seconds, `n` times."""
    for _ in range(n):
        app.last_tick = time.monotonic() - gap
        app._tick()
        app.root.update()
