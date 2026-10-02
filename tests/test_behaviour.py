"""Behaviour: sessions, breaks, nudges, pausing, idle handling, settings, themes and migration."""
import json
import os
import time

from _harness import Report, load_flow, run_steps, tick

m = load_flow()
report = Report("Behaviour")
check = report.check
app = m.App()
report.watchdog(app.root, 180)
R = app.root
steps = []


def step(fn):
    steps.append(fn)
    return fn


@step
def session_cycle():
    m.FAKE_IDLE[0] = 1.0
    app.state = "working"
    app.reset_work()
    work = app.minutes("work_minutes") * 60
    app.work_elapsed = work - app.minutes("warn_minutes_before") * 60 + 1
    tick(app)
    check("heads-up popup appears before the break", app.warned and app.toast is not None)
    app.work_elapsed = work + 1
    tick(app)
    check("break starts when the session ends", app.state == "break" and app.overlay is not None)
    tick(app)
    check("break pauses while you're at the computer", abs(app.break_remaining - app.break_total) < 0.01)
    m.FAKE_IDLE[0] = 30.0
    before = app.break_remaining
    tick(app, 3)
    check("break counts down while you're away", app.break_remaining < before)
    app.break_remaining = 0.5
    tick(app)
    check("after the break, Flow waits for you to come back", app.state == "returning")
    app.next_nudge = time.monotonic()
    tick(app)
    check("a nudge is sent if you don't come back", app.nudges_sent == 1)
    app.break_ended_at -= 10
    m.FAKE_IDLE[0] = 0.5
    tick(app)
    check("coming back starts the next session", app.state == "working" and app.work_elapsed < 5)
    day = app.stats[m.datetime.date.today().isoformat()]
    check("stats record the break and the return",
          day.get("breaks_taken", 0) >= 1 and day.get("on_time_returns", 0) + day.get("late_returns", 0) >= 1,
          str(day))


@step
def skip_phrase():
    app.start_break()
    app.overlay.entry.insert(0, "wrong words")
    app.overlay._check_phrase(None)
    check("wrong skip phrase does not end the break", app.state == "break")
    app.overlay.entry.insert(0, app.cfg["skip_phrase"].upper())
    app.overlay._check_phrase(None)
    check("correct skip phrase ends the break (any capitals)", app.state == "working")


@step
def pause_resume():
    m.FAKE_IDLE[0] = 1.0
    app.work_elapsed = 600
    app.handle("pause", 15)
    before = app.work_elapsed
    tick(app, 2)
    check("pause freezes the session timer", app.state == "paused" and app.work_elapsed == before)
    app.pause_until = time.monotonic()
    tick(app)
    check("timed pause resumes by itself", app.state == "working" and app.work_elapsed == before)
    app.handle("pause", None)
    tick(app)
    check("'until I resume' stays paused", app.state == "paused")
    app.handle("resume")
    check("resume continues the same session", app.state == "working" and app.work_elapsed == before)


@step
def meeting_mode():
    app.work_elapsed = app.minutes("work_minutes") * 60 + 10
    app.handle("snooze", 60)
    tick(app)
    check("meeting mode holds the break back", app.state == "working")
    check("meeting mode shows the real time to the break (not 00:00)", app.time_to_break() > 3500)
    app.snooze_until = 0
    tick(app)
    check("break arrives once meeting mode ends", app.state == "break")
    app.end_break(skipped=True)


@step
def idle_and_sleep():
    app.state = "working"
    app.work_elapsed = 900
    m.FAKE_IDLE[0] = app.minutes("idle_reset_minutes") * 60 + 5
    tick(app)
    check("a quiet spell keeps counting (no silent restart)", app.work_elapsed > 900)
    m.FAKE_IDLE[0] = 1.0
    tick(app)
    check("back after a quiet spell: Flow asks if it was a break", app.toast is not None)
    app.close_toast()
    app.work_elapsed = 600
    tick(app, gap=app.minutes("idle_reset_minutes") * 60 + 5)
    check("a sleeping laptop counts as a break", app.work_elapsed < 2)
    app.cfg["welcome_popup"] = True
    app.last_wall -= 600
    tick(app)
    check("waking from sleep shows the welcome screen", app.welcome_win is not None and app.state == "waiting")
    app.welcome_win.finish("go")
    check("'Let's go' starts a session", app.state == "working")
    app.cfg["welcome_popup"] = False


@step
def keep_running_mode():
    """'Watch keyboard & mouse' off: for studying away from the PC (e.g. on an iPad)."""
    app.cfg["use_activity"] = False
    app.cfg["welcome_popup"] = True
    app.state = "working"
    app.reset_work()
    app.work_elapsed = 600
    m.FAKE_IDLE[0] = 4 * 3600.0
    tick(app, 2)
    check("keep-running: PC idle for hours, timer keeps running", app.work_elapsed > 601 and app.state == "working")
    before = app.work_elapsed
    app.last_wall -= 900
    tick(app, gap=900)
    check("keep-running: runs through laptop sleep", app.work_elapsed >= before + 899)
    check("keep-running: no surprise welcome screen", app.welcome_win is None or not app.welcome_win.win.winfo_exists())
    app.work_elapsed = app.minutes("work_minutes") * 60 + 1
    tick(app)
    m.FAKE_IDLE[0] = 0.5  # someone at the PC: the break still counts down on the clock
    b0 = app.break_remaining
    tick(app, 2)
    check("keep-running: break counts down on the clock", app.state == "break" and app.break_remaining < b0)
    app.break_remaining = 0.5
    tick(app)
    check("keep-running: next session starts right after the break", app.state == "working" and app.work_elapsed < 3)
    app.close_toast()
    app.cfg.update(use_activity=True, welcome_popup=False)


@step
def tray_states():
    frames = {}
    for state in ("working", "paused", "waiting", "returning", "break"):
        app.state = state
        if state == "break":
            app.break_total = app.break_remaining = 60
        frames[state] = app.tray_frame()
        check(f"tray icon draws for '{state}'", app.render_tray(frames[state]).size == (64, 64))
    app.state = "working"
    check("tray: paused shows pause bars", frames["paused"][2] == "pause")


@step
def effects():
    ok = True
    for eff in m.EFFECTS:
        for speed in m.SPEEDS:
            mo = m.Motion(eff, speed)
            try:
                mo.color(), mo.ring_colors(), mo.loop_colors()
            except Exception as e:
                ok = False
                print("effect error", eff, speed, e)
    check("every effect and speed works", ok)
    for eff in m.EFFECTS:
        app.cfg.update(break_effect=eff)
        m.apply_theme(app.cfg)
        app.start_break()
        app.overlay.update(30, 60, True)
        app.overlay.fx()
        R.update()
        app.end_break(skipped=True)
    check("break screen works with every effect", True)


@step
def settings_everything():
    app.handle("settings")
    sw = app.settings_win
    R.update()
    pos = (sw.win.winfo_rootx(), sw.win.winfo_rooty())
    for tab in sw.TABS:
        sw.show(tab)
        R.update()
    sw.work.set(40)
    sw.brk.set(7)
    sw.goal.delete(0, "end")
    sw.goal.insert(0, "exam prep")
    sw.pick_theme("Violet")
    for prefix, name, _ in m.FX_ELEMENTS:
        sw.fx_tabs.select(name)
        R.update()
        sw.pending[f"{prefix}_effect"] = "Breathe"
    sw.fx_tabs.select("Panel border")
    sw.save()
    R.update()
    saved = json.load(open(m.CONFIG_PATH, encoding="utf-8"))
    check("Settings: Save writes every tab", saved["work_minutes"] == 40 and saved["break_minutes"] == 7
          and saved["goal"] == "exam prep" and saved["theme"] == "Violet")
    check("Settings: effects saved separately per element",
          all(saved[f"{p}_effect"] == "Breathe" for p, _, _ in m.FX_ELEMENTS))
    check("Settings: stays open, doesn't move, keeps your place on Save",
          sw.win.winfo_exists() and (sw.win.winfo_rootx(), sw.win.winfo_rooty()) == pos
          and sw.fx_name == "Panel border")
    check("theme applied everywhere", m.ACCENT == m.THEMES["Violet"][1])
    sw.open_picker()
    R.update()
    pk = sw.picker
    pk.hex.delete(0, "end")
    pk.hex.insert(0, "#12ab34")
    pk._typed_hex()
    check("colour picker: hex and RGB stay in sync", [e.get() for e in pk.rgb] == ["18", "171", "52"])
    pk._use()
    check("colour picker: choosing a colour selects Custom", sw.pending["theme"] == "Custom")
    sw.open_advanced()
    R.update()
    adv = sw.advanced
    adv.away.set(5)
    adv.activity.set(False)
    adv.save()
    check("Advanced: Save works", app.cfg["away_threshold_seconds"] == 5 and app.cfg["use_activity"] is False)
    adv._reset_button(True)
    adv.reset()
    R.update()
    check("Advanced: reset restores defaults but keeps your goal",
          app.cfg["work_minutes"] == m.DEFAULTS["work_minutes"] and app.cfg["goal"] == "exam prep"
          and app.cfg["use_activity"] is True)
    sw.win.destroy()


@step
def gradient_editor():
    check("Sunset is golden hour to violet twilight",
          m.THEMES["Sunset"] == ("#ffc56e", "#ff9248", "#e2552f", "#b5446e", "#5e3a8c"))
    app.handle("settings")
    sw = app.settings_win
    sw.nav.select("Appearance")
    sw.pick_theme("Ocean")
    R.update()
    base = list(m.THEMES["Ocean"])
    sw._set_stops(base + ["#ffffff"])                       # what "+" then a colour does
    check("gradient: + adds a colour", list(m.theme_stops(sw.pending)) == base + ["#ffffff"])
    stops = list(m.theme_stops(sw.pending))
    stops[0] = "#000000"
    sw._set_stops(stops)                                    # what clicking a chip then a colour does
    check("gradient: clicking a colour changes it", m.theme_stops(sw.pending)[0] == "#000000")
    n = m.px(28)
    sw._chip_click(type("E", (), {"x": n - 2, "y": 2})(), 1, n)  # the x in a chip's corner
    check("gradient: x removes a colour", len(m.theme_stops(sw.pending)) == 3)
    sw._set_stops(["#111111"] * 9)
    check("gradient: at most 6 colours", len(m.theme_stops(sw.pending)) == m.MAX_STOPS)
    sw._set_stops(["#000000", "#ff0000", "#ffffff"])
    sw.save()
    R.update()
    saved = json.load(open(m.CONFIG_PATH, encoding="utf-8"))
    check("gradient: saved and used by every effect",
          saved["theme_stops"]["Ocean"] == ["#000000", "#ff0000", "#ffffff"]
          and m.THEME.stops == ("#000000", "#ff0000", "#ffffff") and m.ACCENT == "#ff0000")
    check("gradient: other themes keep their presets", m.theme_stops(app.cfg, "Violet") == m.THEMES["Violet"])
    sw = app.settings_win
    sw._reset_gradient()
    sw.save()
    R.update()
    check("gradient: Reset brings the preset back", m.THEME.stops == m.THEMES["Ocean"])
    sw.win.destroy()


@step
def open_windows_follow_theme():
    app.handle("stats")
    R.update()
    check("Stats window opens", app.stats_win.win.winfo_exists())
    app.apply_settings(dict(app.cfg, theme="Ocean"))
    R.update()
    check("open Stats window redraws in a new theme", app.stats_win.win.winfo_exists())
    app.stats_win.win.destroy()
    app.panel_toggled = 0
    app.handle("panel")
    R.update()
    check("session panel opens", app.panel_open())
    app.apply_settings(dict(app.cfg, theme="Rose"))
    R.update()
    check("open panel redraws in a new theme", app.panel_open())
    app.panel.close()


@step
def stand_ups():
    """Stand-up reminders: a sitting clock separate from the session, with its own card."""
    for use_activity in (False, True):
        app.cfg.update(use_activity=use_activity, stand_reminders=True)
        tag = "keep-running" if not use_activity else "watching"
        app.close_stand()
        app.state = "working"
        app.reset_work()
        app.sit_elapsed = 0.0
        app.stand_snooze_until = app.snooze_until = 0.0
        m.FAKE_IDLE[0] = 4 * 3600.0 if not use_activity else 1.0
        every = app.minutes("stand_every_minutes") * 60
        app.sit_elapsed = every - 1.5
        tick(app)
        check(f"stand-up ({tag}): no card before it's due", not app.stand_open())
        tick(app, 2)
        check(f"stand-up ({tag}): card appears after sitting {every / 60:.0f} min",
              app.stand_open() and app.stand.phase == "ask")
        check(f"stand-up ({tag}): the focus session keeps running", app.state == "working")
        session = app.work_elapsed
        app.stand_next_nudge = time.monotonic()
        tick(app)
        check(f"stand-up ({tag}): ignored card asks again, firmer", app.stand.level == 1 and app.stand_nudges == 1)
        app.stand_up()
        check(f"stand-up ({tag}): 'I'm up' starts the on-your-feet countdown", app.stand.phase == "moving")
        sat = app.sit_elapsed
        tick(app, 3)
        check(f"stand-up ({tag}): sitting clock stops while you're moving", app.sit_elapsed == sat)
        check(f"stand-up ({tag}): session time still counts", app.work_elapsed > session)
        app.stand.ends = time.monotonic() - 0.1
        app.stand._run_clock()
        check(f"stand-up ({tag}): finishing resets the sitting clock", app.sit_elapsed == 0
              and app.stand.phase == "done")
    day = app.stats[m.datetime.date.today().isoformat()]
    check("stand-up: counted in stats", day.get("stand_ups", 0) >= 2, str(day))

    app.close_stand()
    app.sit_elapsed = every + 5
    app.stand_snooze(5)
    tick(app)
    check("stand-up: '5 more min' holds it back", not app.stand_open())
    app.stand_snooze_until = 0.0
    app.handle("snooze", 60)  # meeting mode
    tick(app)
    check("stand-up: meeting mode holds it back", not app.stand_open())
    app.snooze_until = 0.0
    app.work_elapsed = app.minutes("work_minutes") * 60 - 120  # break due in 2 min
    tick(app)
    check("stand-up: skipped when a screen break is about to start", not app.stand_open())
    app.work_elapsed = 0.0
    app.cfg["stand_reminders"] = False
    tick(app)
    check("stand-up: off means no card", not app.stand_open())
    app.cfg["stand_reminders"] = True
    tick(app)
    check("stand-up: back on, the card shows", app.stand_open())
    app.start_break()
    check("stand-up: a screen break closes the card and counts as standing",
          not app.stand_open() and app.sit_elapsed == 0)
    app.end_break(skipped=True)
    app.close_toast()
    m.FAKE_IDLE[0] = 1.0
    app.cfg["use_activity"] = True
    app.sit_elapsed = 600
    m.FAKE_IDLE[0] = app.minutes("stand_for_minutes") * 60 + 5
    tick(app)
    check("stand-up (watching): being away from the keyboard counts as standing", app.sit_elapsed == 0)
    m.FAKE_IDLE[0] = 1.0
    app.handle("stand_now")
    check("stand-up: 'Stand up now' starts the countdown straight away",
          app.stand_open() and app.stand.phase == "moving")
    app.close_stand()


@step
def panel_auto_hide():
    app.state = "working"
    if app.panel_open():
        app.panel.close()
    app.panel_toggled = 0.0
    app.handle("panel")
    app.panel.win.unbind("<FocusOut>")
    R.update()
    check("panel: stays open at first", app.panel_open())
    app.panel.last_seen -= 19
    app.panel._auto_hide()
    check("panel: still open just before 20 s", app.panel_open())
    app.panel.win.event_generate("<Motion>", warp=True, x=20, y=20)  # mouse over the panel
    R.update()
    app.panel.last_seen -= 30
    app.panel._auto_hide()
    check("panel: the mouse over it keeps it open", app.panel_open())
    app.panel.win.event_generate("<Motion>", warp=True, x=-400, y=-400)  # mouse away
    R.update()
    app.panel.last_seen -= 21
    app.panel._auto_hide()
    check("panel: hides by itself after 20 s", not app.panel_open())


@step
def migration():
    old = {"effect": "Gradient", "effect_speed": "Fast", "fx_tray": False, "fx_break": True,
           "border_style": "Breathe"}
    path = os.path.join(os.path.dirname(m.CONFIG_PATH), "old_config.json")
    json.dump(old, open(path, "w", encoding="utf-8"))
    keep, m.CONFIG_PATH = m.CONFIG_PATH, path
    cfg = m.load_config()
    m.CONFIG_PATH = keep
    check("settings from older versions carry over",
          cfg["tray_effect"] == "Static" and cfg["break_effect"] == "Flow"
          and cfg["panel_effect"] == "Breathe" and cfg["break_speed"] == "Fast")


run_steps(app, report, steps)
app.run()
raise SystemExit(report.code)
