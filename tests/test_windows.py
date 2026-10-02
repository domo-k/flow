"""Windows: every window lands inside the screen, is fully visible, has a dark title bar and closes
with its X, in every window-animation mode. Opens real windows on screen for about a minute."""
import ctypes

from _harness import Report, load_flow

m = load_flow()
report = Report("Windows")
app = m.App()
report.watchdog(app.root, 150)
R = app.root
user32 = ctypes.windll.user32
L, T, RIGHT, B = m.work_area()


def hwnd(win):
    return user32.GetAncestor(win.winfo_id(), 2)


def dark(win):
    v = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd(win), 20, ctypes.byref(v), ctypes.sizeof(v))
    return v.value == 1


def inspect(mode, name, win, titled=True, inside_work_area=False):
    win.update()
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    vx, vy, vw, vh = m.virtual_screen()
    visible = vx <= x < vx + vw and vy <= y < vy + vh and float(win.attributes("-alpha")) > 0.99
    report.check(f"[{mode}] {name}: on screen and fully visible", visible, f"at {x},{y}")
    if inside_work_area:
        report.check(f"[{mode}] {name}: fully inside the main screen",
                     x >= L and y >= T and x + w <= RIGHT and y + h <= B, f"{x},{y} to {x + w},{y + h}")
    if titled:
        report.check(f"[{mode}] {name}: dark title bar", dark(win))


def close_with_x(mode, name, win, then):
    """Send WM_CLOSE (what the title bar X does) and confirm the window goes away."""
    user32.PostMessageW(hwnd(win), 0x0010, 0, 0)
    R.after(300, lambda: (report.check(f"[{mode}] {name}: closes with X", not win.winfo_exists()), then()))


modes = list(m.WINDOW_ANIMS)


def run_mode(i=0):
    if i >= len(modes):
        return report.finish(app)
    mode = modes[i]
    app.cfg["window_anim"] = mode
    m.apply_theme(app.cfg)
    app.handle("settings")
    sw = app.settings_win
    app.handle("stats")
    st = app.stats_win
    sw.open_advanced()
    adv = sw.advanced
    sw.open_picker()
    pk = sw.picker
    toast = m.Toast(R, "Test", "Session resumed. 35 min until your break.")

    def titled_windows():
        inspect(mode, "Settings", sw.win)
        inspect(mode, "Stats", st.win)
        inspect(mode, "Advanced", adv.win)
        inspect(mode, "Colour wheel", pk.win)
        inspect(mode, "Popup", toast.win, titled=False, inside_work_area=True)
        toast.destroy()
        close_with_x(mode, "Colour wheel", pk.win, lambda: close_with_x(mode, "Advanced", adv.win, lambda: close_with_x(
            mode, "Stats", st.win, lambda: close_with_x(mode, "Settings", sw.win, panel))))

    def panel():
        app.panel_toggled = 0
        app.handle("panel")
        app.panel.win.unbind("<FocusOut>")
        R.after(500, panel_states)

    def panel_states(k=0):
        states = [("running", lambda: setattr(app, "state", "working")),
                  ("paused", lambda: app.pause(None)),
                  ("on a break", lambda: (setattr(app, "state", "break"), setattr(app, "break_total", 600),
                                          setattr(app, "break_remaining", 400)))]
        if k >= len(states):
            app.state = "working"
            app.close_toast()
            app.panel.close()
            return menu()
        states[k][1]()
        app.panel.refresh()
        R.after(400, lambda: (inspect(mode, f"Session panel ({states[k][0]})", app.panel.win, titled=False,
                                      inside_work_area=True), panel_states(k + 1)))

    def menu():
        app.handle("menu", RIGHT - 60, T - 20)
        app.menu.win.unbind("<FocusOut>")
        R.after(600, lambda: (inspect(mode, "Right-click menu", app.menu.win, titled=False, inside_work_area=True),
                              app.menu.run("settings"), R.after(700, from_menu)))

    def from_menu():
        inspect(mode, "Settings (opened from the menu)", app.settings_win.win)
        app.settings_win.win.destroy()
        app.show_welcome()
        R.after(700, welcome)

    def welcome():
        inspect(mode, "Welcome", app.welcome_win.win, titled=False)
        app.welcome_win.finish("go")
        app.close_toast()
        R.after(300, run_mode, i + 1)

    R.after(900, titled_windows)


R.after(500, run_mode)
app.run()
raise SystemExit(report.code)
