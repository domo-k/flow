"""Run every Flow test and print one summary.

    python tests/run_all.py

Windows only. The tests open real (temporary) Flow windows on screen for a couple of minutes;
they use throwaway settings and never touch your real ones.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["test_behaviour.py", "test_timer.py", "test_windows.py"]

failed = []
for name in TESTS:
    started = time.monotonic()
    code = subprocess.call([sys.executable, "-u", os.path.join(HERE, name)], cwd=HERE)
    print(f"-> {name}: {'ok' if code == 0 else 'FAILED'} ({time.monotonic() - started:.0f}s)\n", flush=True)
    if code != 0:
        failed.append(name)

print("All tests passed." if not failed else f"Failed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
