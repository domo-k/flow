"""Timer accuracy: Flow keeps real time, and the panel counts down exactly one second per second."""
import time

from _harness import Report, load_flow

m = load_flow()
report = Report("Timer")
app = m.App()
report.watchdog(app.root, 60)
R = app.root

app.state = "working"
app.reset_work()
app.handle("panel")
app.panel.win.unbind("<FocusOut>")  # keep it open while we watch
start, start_elapsed = time.monotonic(), app.work_elapsed
shown = []


def sample(i=0):
    shown.append(app.panel.clock.cget("text"))
    if i < 15:
        return R.after(1000, sample, i + 1)
    real = time.monotonic() - start
    counted = app.work_elapsed - start_elapsed
    report.check("counted time matches the real clock (within 1.2 s)", abs(counted - real) < 1.2,
                 f"real {real:.2f}s, counted {counted:.2f}s")
    secs = [int(t[:-3]) * 60 + int(t[-2:]) for t in shown]
    steps = [a - b for a, b in zip(secs, secs[1:])]
    report.check("panel counts down exactly 1 second per second", all(s == 1 for s in steps), f"steps {steps}")
    app.panel.close()
    report.finish(app)


R.after(1300, sample)
app.run()
raise SystemExit(report.code)
