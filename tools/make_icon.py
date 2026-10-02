"""Regenerate flow.ico from the logo drawing code in flow.pyw (Sunset theme, counter-clockwise ring).

    python tools/make_icon.py
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("flow", os.path.join(ROOT, "flow.pyw"))
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)
flow.apply_theme(flow.DEFAULTS)

sizes = [256, 128, 64, 48, 32, 24, 16]
images = [flow.app_logo(s) for s in sizes]  # each size drawn on its own, so small ones stay crisp
out = os.path.join(ROOT, "flow.ico")
images[0].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
print(f"wrote {out} ({os.path.getsize(out):,} bytes)")
