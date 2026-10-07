"""Flow - focus in sessions, get up for breaks, and come back ready.

The break screen only counts down while you're away from the keyboard and mouse,
so the only way through a break is to actually get up.

Run:  pyw flow.pyw          (normal)
      pyw flow.pyw --demo   (1 min work / 20 s break, for trying it out)
"""
import colorsys
import ctypes
import datetime
import io
import json
import math
import os
import queue
import random
import secrets
import shutil
import struct
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import urllib.request
import wave
import winreg
import winsound
from ctypes import wintypes

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageGrab, ImageOps, ImageTk

FROZEN = getattr(sys, "frozen", False)  # running as Flow.exe

_tk_after = tk.Misc.after


def _after_on_root(self, ms, func=None, *args):
    """Schedule every timer on the main window, which lives as long as Flow does.

    tkinter deletes a window's callbacks when the window closes. A timer still queued for a closed
    window could then fire into a newer callback that reused the same internal name (an error at
    best, the wrong action at worst). Callbacks check their window still exists before acting.
    """
    return _tk_after(self._root(), ms, func, *args)


tk.Misc.after = _after_on_root
_tk_after_cancel = tk.Misc.after_cancel
tk.Misc.after_cancel = lambda self, timer: _tk_after_cancel(self._root(), timer)  # same window as after()
# Settings and stats live in %APPDATA%\Flow, so the exe can sit anywhere.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Flow")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
LEGACY_DIR = os.path.join(os.path.expanduser("~"), "standup-app")  # where earlier versions kept them


def migrate_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    for name in ("config.json", "stats.json"):
        old, new = os.path.join(LEGACY_DIR, name), os.path.join(DATA_DIR, name)
        if os.path.exists(old) and not os.path.exists(new):
            shutil.copy2(old, new)

DEFAULTS = {
    "work_minutes": 50,
    "break_minutes": 10,
    # Stand-up reminders: get out of the chair every so often, even mid-session. Not a screen break:
    # a card asks you to move for a couple of minutes and your session keeps going.
    "stand_reminders": True,
    "stand_every_minutes": 30,
    "stand_for_minutes": 2,
    "stand_renudge_minutes": 3,   # ask again this often if the card is ignored
    "warn_minutes_before": 5,
    # Away from the computer this long while working = you already took a break; timer resets.
    "idle_reset_minutes": 5,
    # During a break, no keyboard/mouse input for this long = you're away, countdown runs.
    "away_threshold_seconds": 3,
    # Use keyboard/mouse activity to tell if you're at the computer. Turn off if you also study or
    # work away from it (e.g. on an iPad): the session timer then always keeps running, breaks count
    # down on the clock, and the next session starts right after a break.
    "use_activity": True,
    # true: touching the computer restarts the break. false: it only pauses the countdown.
    "reset_break_on_input": False,
    "skip_phrase": "i really need to skip this break",
    "timer_mode": "Focus sessions",  # or "Stand-ups only": no sessions or breaks, just a stand-up countdown
    "breaks_enabled": True,       # off: no break screen; sessions just roll on (stand-ups still work)
    "skip_style": "One click",    # how to skip a break: "One click" or "Type a phrase"
    # Chime when a break ends, 0-100 (0 = silent).
    "chime_volume": 8,
    # After a break: nudge me if I haven't come back to the computer.
    "return_nudges": True,
    "return_grace_minutes": 2,
    "return_nudge_every_minutes": 3,
    "return_max_nudges": 5,
    # Shown in nudges, e.g. "Finish my thesis draft".
    "goal": "",
    # Phone notifications through the free ntfy app (https://ntfy.sh). Off until you turn it on.
    "phone_notify": False,
    "ntfy_topic": "",
    # Welcome card when the laptop starts or wakes, or you're back after a long time away.
    "welcome_popup": True,
    "welcome_after_idle_minutes": 60,
    # Appearance
    "theme": "Sunset",            # a name from THEMES, or "Custom"
    "accent": "#ff9248",          # used when theme is "Custom"
    "theme_stops": {},            # your own gradient colours per theme, e.g. {"Ocean": ["#...", ...]}
    # Each animated element has its own effect (Off | Static | Breathe | Flow | Rainbow)
    # and speed (Slow | Medium | Fast).
    "tray_effect": "Flow",
    "tray_speed": "Slow",
    "panel_effect": "Flow",       # the border on the session panel
    "panel_speed": "Slow",
    "break_effect": "Flow",       # the glowing frame and ring on the break screen
    "break_speed": "Slow",
    "ring_direction": "Counter-clockwise",  # which way timer rings run down
    "window_anim": "Slide",       # Slide | Fade | None
}
WELCOME_LINES = [
    "Small steps, done daily, add up to big things.",
    "You don't need to feel ready. You just need to start.",
    "One focused session beats a whole day of half-effort.",
    "Start with the hardest thing. Everything after feels lighter.",
    "Progress, not perfection.",
    "The next best time to start is right now.",
    "Future you is counting on present you.",
    "Motivation follows action. Begin, and it will come.",
]
TIMER_MODES = ("Focus sessions", "Stand-ups only")
PAUSE_CHOICES = (("15 min", 15), ("30 min", 30), ("1 hour", 60), ("Until I resume", None))
WORK_CHOICES = (25, 30, 45, 50, 60, 75, 90)
BREAK_CHOICES = (5, 10, 15, 20)
# Nudges get firmer the longer you stay away. {over} = minutes past the end of the break.
NUDGES = [
    ("Break's over", "Time to head back. Future you will thank you."),
    ("Ready when you are", "Your desk is waiting. Just start with one small thing."),
    ("Still resting?", "Getting up is the hardest part. The rest gets easier once you start."),
    ("Let's go", "{over} min past your break. A short push now beats a long catch-up later."),
    ("Come back", "Rest is done. Momentum starts the moment you sit down."),
]
# Stand-up card: something to do on your feet, and firmer wording each time it's ignored.
MOVES = [
    "Walk to the kitchen and get a glass of water.",
    "Do 10 slow calf raises.",
    "Walk around the room a couple of times.",
    "Reach both arms overhead and hold for five slow breaths.",
    "Do 10 bodyweight squats.",
    "Roll your shoulders back ten times, standing tall.",
    "Lunge forward and stretch your hips, 20 seconds each side.",
    "Pace the hallway, or take the stairs once.",
    "Touch your toes slowly, then roll back up.",
    "Step outside or stand by a window for some fresh air.",
]
STAND_NUDGES = [
    ("Time to stand up", "You've been sitting for {sat} min."),
    ("Still sitting?", "{sat} min in the chair. Two minutes on your feet makes a real difference."),
    ("Up you get", "{sat} min of sitting. Your back and legs will thank you."),
]
PRESETS = {
    "Pomodoro  25 / 5": (25, 5),
    "Standard  50 / 10": (50, 10),
    "Deep work  90 / 15": (90, 15),
}
TIPS = [
    "Roll your shoulders back slowly, ten times.",
    "Look at something far away for 20 seconds. Your eyes will thank you.",
    "Refill your water bottle.",
    "Stand tall, reach both arms overhead and hold for five breaths.",
    "Walk to another room and back.",
    "Tilt your head gently to each side and hold for 15 seconds.",
    "Do ten slow calf raises.",
    "Open a window or step outside for some fresh air.",
    "Put a hand on a wall and stretch your chest, one side at a time.",
    "Shake out your hands and wrists.",
]

# Palette. Neutrals are fixed; the accent colours come from the theme (see apply_theme).
BG, SURFACE, SURFACE_2, BORDER = "#0e1116", "#171b22", "#1f252e", "#2a313b"
FG, MUTED, FAINT = "#eef1f4", "#8b95a1", "#5b6570"
WARN, IDLE = "#f2b84b", "#7d8793"
SIDEBAR = "#12161c"
ACCENT = ACCENT_HOVER = ACCENT_INK = CHART = FG  # set by apply_theme()

# Gradient stops for each theme; the middle stop is the accent colour.
THEMES = {
    "Sunset": ("#ffc56e", "#ff9248", "#e2552f", "#b5446e", "#5e3a8c"),  # golden hour to twilight
    "Ocean": ("#3a7bff", "#4facfe", "#3fd8ff"),
    "Violet": ("#7f5cff", "#a98bff", "#f19cff"),
    "Rose": ("#ff3f7f", "#ff6f91", "#ffa9b8"),
    "Ember": ("#ff3d3d", "#ff6b35", "#ffb03a"),
    "Mono": ("#9aa4b1", "#d5dbe2", "#ffffff"),
}
EFFECTS = ("Off", "Static", "Breathe", "Flow", "Rainbow")
SPEEDS = {"Slow": 30.0, "Medium": 12.0, "Fast": 5.0}  # seconds per colour cycle
WINDOW_ANIMS = ("Slide", "Fade", "None")
RING_DIRECTIONS = ("Clockwise", "Counter-clockwise")
# Animated elements: (settings key prefix, name shown in Settings, effects it offers)
GLYPH = {"panel": "", "break": "", "stand": "", "pause": "", "play": "", "meeting": "",
         "reset": "", "settings": "", "stats": "", "quit": "", "close": "",
         "schedule": "", "skip": "", "back": "", "appearance": "", "general": ""}
FX_ELEMENTS = (("tray", "Tray icon", EFFECTS[1:]),
               ("panel", "Panel border", EFFECTS),
               ("break", "Break screen", EFFECTS))


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def valid_hex(text):
    t = (text or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in t):
        return "#" + t.lower()
    return None


def mix(a, b, t):
    return rgb_hex([x + (y - x) * t for x, y in zip(hex_rgb(a), hex_rgb(b))])


def hsv_hex(h, s, v):
    return rgb_hex([c * 255 for c in colorsys.hsv_to_rgb(h % 1, s, v)])


def luminance(h):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def sample(stops, t, loop=False):
    """Colour at position t (0-1) along a gradient; loop=True wraps back to the first stop."""
    seq = list(stops) + ([stops[0]] if loop else [])
    if len(seq) == 1:
        return seq[0]
    t = t % 1 if loop else min(max(t, 0.0), 1.0)
    pos = t * (len(seq) - 1)
    i = min(int(pos), len(seq) - 2)
    return mix(seq[i], seq[i + 1], pos - i)


def custom_stops(accent):
    """A three-stop gradient around a custom accent: a little cooler, the colour, a little warmer."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in hex_rgb(accent)))
    return (hsv_hex(h - 0.05, s, v * 0.95), accent, hsv_hex(h + 0.05, s * 0.9, min(1, v * 1.08)))


MAX_STOPS = 6


def theme_stops(cfg, name=None):
    """Gradient colours for a theme: your own list if you've edited it, otherwise the preset."""
    name = name or cfg.get("theme")
    edited = cfg.get("theme_stops")
    if isinstance(edited, dict) and isinstance(edited.get(name), list):
        own = [h for h in (valid_hex(x) if isinstance(x, str) else None for x in edited[name]) if h]
        if len(own) >= 2:
            return tuple(own[:MAX_STOPS])
    if name in THEMES:
        return THEMES[name]
    return custom_stops(valid_hex(cfg.get("accent")) or THEMES["Sunset"][1])


class Theme:
    """The colour palette. It never moves by itself; each animated element has its own Motion."""

    def __init__(self, cfg):
        self.stops = theme_stops(cfg)
        self.accent = self.stops[min(1, len(self.stops) - 1)]  # buttons and highlights


RAINBOW = [hsv_hex(i / 12, 0.58, 1.0) for i in range(12)]


class Motion:
    """How one element (tray icon, panel border, break screen) animates the theme colours.

    Off: nothing.  Static: plain theme colours.  Breathe: slow pulse.
    Flow: the theme gradient moves (circulates around borders, turns around rings).
    Rainbow: the same, through every hue.
    """

    def __init__(self, effect, speed, default="Flow", theme=None):
        self.effect = effect if effect in EFFECTS else default
        self.period = SPEEDS.get(speed, 30.0)
        self.theme = theme  # None = follow the current theme

    @property
    def stops(self):
        return (self.theme or THEME).stops

    @property
    def accent(self):
        return (self.theme or THEME).accent

    @property
    def animated(self):
        return self.effect in ("Breathe", "Flow", "Rainbow")

    def phase(self):
        # Breathing reads calmer a little quicker than a full colour loop.
        period = self.period / 3 if self.effect == "Breathe" else self.period
        return (time.monotonic() % period) / period

    def color(self):
        """One colour for right now."""
        p = self.phase()
        if self.effect == "Breathe":
            return mix(mix(self.accent, BG, 0.6), self.accent, (1 - math.cos(2 * math.pi * p)) / 2)
        if self.effect == "Flow":
            return sample(self.stops, p, loop=True)
        if self.effect == "Rainbow":
            return hsv_hex(p, 0.58, 1.0)
        return self.accent

    def ring_colors(self):
        """Colours around a ring, clockwise from 12 o'clock."""
        p = self.phase()
        if self.effect == "Flow":
            return [sample(self.stops, p + i / 24, loop=True) for i in range(25)]
        if self.effect == "Rainbow":
            return [hsv_hex(p + i / 24, 0.58, 1.0) for i in range(25)]
        if self.effect == "Breathe":
            return [self.color()]
        return list(self.stops)

    def loop_colors(self):
        """Colours for a border that circulates."""
        return RAINBOW if self.effect == "Rainbow" else list(self.stops) * 2


THEME = Theme({})
TRAY_FX = PANEL_FX = BREAK_FX = Motion("Flow", "Slow")
STATIC_FX = Motion("Static", "Slow")
ANIM, CCW = "Slide", True


def apply_theme(cfg):
    """Recompute the palette, and each element's own animation, from settings."""
    global THEME, ACCENT, ACCENT_HOVER, ACCENT_INK, CHART, ANIM, CCW, TRAY_FX, PANEL_FX, BREAK_FX
    THEME = Theme(cfg)
    ACCENT = THEME.accent
    ACCENT_HOVER = mix(ACCENT, "#ffffff", 0.18)
    ACCENT_INK = max(("#15110f", "#ffffff"), key=lambda ink: contrast(ink, ACCENT))
    chart = ACCENT
    while contrast(chart, SURFACE) < 3:  # keep chart bars readable on the dark card
        chart = mix(chart, "#ffffff", 0.12)
    CHART = chart
    ANIM = cfg.get("window_anim") if cfg.get("window_anim") in WINDOW_ANIMS else "Slide"
    CCW = cfg.get("ring_direction", "Counter-clockwise") == "Counter-clockwise"
    TRAY_FX = Motion(cfg.get("tray_effect"), cfg.get("tray_speed"))
    if TRAY_FX.effect == "Off":  # the tray icon always shows; "off" just means no animation
        TRAY_FX = Motion("Static", cfg.get("tray_speed"))
    PANEL_FX = Motion(cfg.get("panel_effect"), cfg.get("panel_speed"))
    BREAK_FX = Motion(cfg.get("break_effect"), cfg.get("break_speed"))


def frame_border():
    """The session panel's 1px Windows border: hidden while Flow draws its own animated border."""
    return "none" if PANEL_FX.effect != "Off" else BORDER


def orient(img):
    """Timer rings are drawn clockwise; mirror them for counter-clockwise."""
    return ImageOps.mirror(img) if CCW else img


class EdgeGlow:
    """Animated border along the inside edges of a window (or of a rectangle on it).

    motion: a function returning the Motion to use (so it follows settings changes).
    glow=True fades each side inwards, for a soft light (used on the break screen).
    """

    def __init__(self, win, motion, bg=SURFACE, thickness=None, rect=None, glow=False, radius=None):
        self.win, self.motion, self.bg, self.rect, self.glow = win, motion, bg, rect, glow
        self.t = t = thickness or max(2, px(2))
        # Windows 11 rounds window corners (about 8px); curve the border to match so nothing is cut off.
        self.r = r = max(t, radius if radius is not None else (0 if rect else px(8)))
        self.size = None
        self.line_key = None
        self.photos = [None] * 8
        # Four sides plus four corner pieces (curved, or blended squares), so there are no gaps or notches.
        self.parts = [tk.Label(win, bd=0, highlightthickness=0, bg=bg) for _ in range(8)]
        if rect:
            x, y, w, h = rect
            spots = [dict(x=x + r, y=y, width=w - 2 * r, height=t),
                     dict(x=x + w - t, y=y + r, width=t, height=h - 2 * r),
                     dict(x=x + r, y=y + h - t, width=w - 2 * r, height=t),
                     dict(x=x, y=y + r, width=t, height=h - 2 * r),
                     dict(x=x, y=y), dict(x=x + w - r, y=y), dict(x=x + w - r, y=y + h - r), dict(x=x, y=y + h - r)]
        else:
            spots = [dict(x=r, y=0, relwidth=1, width=-2 * r, height=t),
                     dict(relx=1, x=-t, y=r, width=t, relheight=1, height=-2 * r),
                     dict(x=r, rely=1, y=-t, relwidth=1, width=-2 * r, height=t),
                     dict(x=0, y=r, width=t, relheight=1, height=-2 * r),
                     dict(x=0, y=0), dict(relx=1, x=-r, y=0), dict(relx=1, x=-r, rely=1, y=-r),
                     dict(x=0, rely=1, y=-r)]
        for i, (part, spot) in enumerate(zip(self.parts, spots)):
            if i >= 4:
                spot.update(width=r, height=r)
            part.place(**spot)
        self.render()

    def _arc_corner(self, color, corner, ss=4):
        """A quarter-circle stroke for a rounded corner (0=top-left, then clockwise)."""
        r, t = self.r * ss, self.t * ss
        img = Image.new("RGB", (r, r), self.bg)
        cx, cy = [(r, r), (0, r), (0, 0), (r, 0)][corner]
        rad = r - t / 2
        ImageDraw.Draw(img).ellipse((cx - rad - t / 2, cy - rad - t / 2, cx + rad + t / 2, cy + rad + t / 2),
                                    outline=color, width=t)
        return img.resize((self.r, self.r), Image.LANCZOS)

    def destroy(self):
        for part in self.parts:
            part.destroy()

    def _dims(self):
        if self.rect:
            return self.rect[2], self.rect[3]
        return self.win.winfo_width(), self.win.winfo_height()

    def render(self):
        if not self.win.winfo_exists():
            return
        motion = self.motion()
        if motion.effect == "Off":
            for part in self.parts:
                part.config(image="", bg=self.bg)
            self.photos = [None] * 8
            return
        w, h = self._dims()
        t = self.t
        r = self.r
        if w < 3 * r or h < 3 * r:
            return
        P = 2 * (w + h)
        if self.size != (w, h):  # (re)build the fade mask for this size
            self.size = (w, h)
            self.base = Image.new("RGB", (P, t), self.bg)
            mask = Image.new("L", (1, t))
            for r in range(t):
                mask.putpixel((0, r), int(255 * (1 - r / t) ** 1.7) if self.glow else 255)
            self.mask = mask.resize((P, t))
            self.line_key = None
        if motion.effect in ("Flow", "Rainbow"):  # colours travel around the edges
            colors = motion.loop_colors()
            if self.line_key != (tuple(colors), P):
                self.line_key = (tuple(colors), P)
                line = Image.new("RGB", (P, 1))
                for i in range(P):
                    line.putpixel((i, 0), hex_rgb(sample(colors, i / P, loop=True)))
                self.line = line
            line = ImageChops.offset(self.line, int(motion.phase() * P), 0)
        else:  # Static or Breathe: one colour all round
            line = Image.new("RGB", (P, 1), motion.color())
        img = Image.composite(line.resize((P, t)), self.base, self.mask)
        # The strip walks clockwise from the top-left corner; cut it into the four sides.
        top = img.crop((0, 0, w, t))
        right = img.crop((w, 0, w + h, t)).transpose(Image.ROTATE_270)
        bottom = img.crop((w + h, 0, 2 * w + h, t)).transpose(Image.ROTATE_180)
        left = img.crop((2 * w + h, 0, P, t)).transpose(Image.ROTATE_90)
        pieces = [top.crop((r, 0, w - r, t)), right.crop((0, r, t, h - r)),
                  bottom.crop((r, 0, w - r, t)), left.crop((0, r, t, h - r))]
        if r > t:  # rounded window: curved corners in the colour at each corner of the path
            for corner, pos in enumerate((0, w, w + h, 2 * w + h)):
                pieces.append(self._arc_corner(rgb_hex(img.getpixel((min(pos, P - 1), 0))), corner))
        else:  # square: blend the two sides that meet
            pieces += [ImageChops.lighter(top.crop((0, 0, t, t)), left.crop((0, 0, t, t))),
                       ImageChops.lighter(top.crop((w - t, 0, w, t)), right.crop((0, 0, t, t))),
                       ImageChops.lighter(bottom.crop((w - t, 0, w, t)), right.crop((0, h - t, t, h))),
                       ImageChops.lighter(bottom.crop((0, 0, t, t)), left.crop((0, h - t, t, h)))]
        for i, (part, piece) in enumerate(zip(self.parts, pieces)):
            photo = self.photos[i]
            if photo is None or photo.width() != piece.width or photo.height() != piece.height:
                self.photos[i] = ImageTk.PhotoImage(piece)
                part.config(image=self.photos[i])
            else:
                photo.paste(piece)


apply_theme(DEFAULTS)

UI_SCALE = 1.0  # screen pixels per 96-dpi pixel, set at startup
F = {}          # font families, set at startup

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GetTickCount.restype = wintypes.DWORD


def px(n):
    return int(round(n * UI_SCALE))


def font(kind, size):
    """size > 0 is points; size < 0 is pixels (tk convention)."""
    return (F.get(kind, "Segoe UI"), size)


def pick_fonts(root):
    families = set(tkfont.families(root))

    def first(*names):
        return next((n for n in names if n in families), "Segoe UI")

    return {
        "display": first("Segoe UI Variable Display Semib", "Segoe UI Semibold"),
        "display_light": first("Segoe UI Variable Display Light", "Segoe UI Light"),
        "text": first("Segoe UI Variable Text", "Segoe UI"),
        "text_semibold": first("Segoe UI Variable Text Semibold", "Segoe UI Semibold"),
        "icons": first("Segoe Fluent Icons", "Segoe MDL2 Assets"),
    }


def spaced(text):
    """Letter-spaced caps for small section labels."""
    return " ".join(text.upper()).replace("   ", "     ")


def fmt_num(v):
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


# ---------- Windows helpers ----------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    """Seconds since the last keyboard or mouse input anywhere in Windows."""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    user32.GetLastInputInfo(ctypes.byref(lii))
    return ((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def virtual_screen():
    """Bounding box of all monitors: x, y, width, height."""
    return tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))


def primary_screen():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def work_area():
    """Primary monitor minus the taskbar: left, top, right, bottom."""
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def already_running():
    kernel32.CreateMutexW(None, False, "StandUpApp_SingleInstance")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _colorref(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return r | (g << 8) | (b << 16)


def style_window(win, border=None):
    """Windows 11 chrome: dark title bar, rounded corners, optional border colour."""
    win.update_idletasks()
    win._flow_border = border  # remembered so animate_in() can re-apply it (see there)
    hwnd = user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT: the real top-level window, even when owned
    attrs = [(20, 1), (33, 2)]  # DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE=round
    if border == "none":
        attrs.append((34, -2))  # DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE (0xFFFFFFFE)
    elif border:
        attrs.append((34, _colorref(border)))  # DWMWA_BORDER_COLOR
    for attr, value in attrs:
        v = ctypes.c_int(value)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        except OSError:
            pass  # older Windows: plain square window


def cover_window(win):
    """Lay a still picture of a window exactly over it, so a rebuild underneath can't be seen
    half-drawn. Pass the result to uncover() once the rebuild is done."""
    try:
        win.update_idletasks()
        x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
        picture = ImageGrab.grab((x, y, x + w, y + h), all_screens=True)
    except (OSError, tk.TclError):
        return None
    cover = tk.Toplevel(win)
    cover.overrideredirect(True)
    cover.attributes("-topmost", True)
    cover.geometry(f"{w}x{h}+{x}+{y}")
    cover.photo = ImageTk.PhotoImage(picture)
    tk.Label(cover, image=cover.photo, bd=0, highlightthickness=0).pack()
    cover.update()
    return cover


def uncover(win, cover):
    """Finish drawing the rebuilt window under the picture, then remove the picture."""
    if cover is None:
        return
    try:
        win.update()
    except tk.TclError:
        pass
    cover.after(40, cover.destroy)


def hidden_toplevel(parent):
    """A new window that starts invisible and off-screen, so it never flashes at Windows' default
    spot (top-left). animate_in() reveals it once it has been moved into place."""
    w = tk.Toplevel(parent)
    w.attributes("-alpha", 0.0)
    w.geometry("+-20000+-20000")
    w.update_idletasks()  # create the real window now, so style_window() can give it a dark title bar
    return w


def animate_in(win, x, y, target_alpha=1.0, rise=0, ms=200):
    """Fade (and optionally slide up) a window into place, per the window animation setting.

    Windows start off-screen (see hidden_toplevel), so every path here moves the window to (x, y)
    and applies it straight away before making it visible.
    """
    if ANIM == "Fade":
        rise = 0
    if ANIM == "None":
        rise = 0
    win.attributes("-alpha", 0.0)
    win.geometry(f"+{x}+{int(y + rise)}")
    win.update_idletasks()
    if hasattr(win, "_flow_border"):
        # Windows can swap in a new frame for owned windows after styling; style the final one.
        style_window(win, win._flow_border)
    if ANIM == "None":
        win.attributes("-alpha", target_alpha)
        return
    steps = 12

    def step(i=1):
        if not win.winfo_exists():
            return
        t = 1 - (1 - i / steps) ** 3  # ease-out
        win.attributes("-alpha", target_alpha * t)
        win.geometry(f"+{x}+{int(y + rise * (1 - t))}")
        if i < steps:
            win.after(ms // steps, step, i + 1)

    step()


def tray_corner(w, h, margin=12):
    """Where a popup of size w x h should go: the corner next to the tray, whether the taskbar
    is at the bottom (the usual) or at the top. Returns x, y and whether it hangs from the top."""
    left, top, right, bottom = work_area()
    _, screen_h = primary_screen()
    from_top = top > 0 and bottom >= screen_h  # taskbar along the top edge
    x = max(left + px(margin), right - w - px(margin))
    y = top + px(margin) if from_top else bottom - h - px(margin)
    return x, y, from_top


def center_on_primary(win):
    win.update_idletasks()
    left, top, right, bottom = work_area()
    x = left + (right - left - win.winfo_reqwidth()) // 2
    y = top + (bottom - top - win.winfo_reqheight()) // 2
    win.geometry(f"+{x}+{y}")
    win.update_idletasks()
    return x, y


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME, OLD_RUN_NAMES = "Flow", ("Rise", "StandUp")


def startup_command():
    if FROZEN:
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def get_startup():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except OSError:
        return False


def set_startup(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def pin_tray_icon():
    """Always show the tray icon instead of hiding it behind the ^ overflow (Windows 11)."""
    exe = sys.executable.lower()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as icons:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(icons, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(icons, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                try:
                    path = winreg.QueryValueEx(key, "ExecutablePath")[0]
                except OSError:
                    continue
                if path.lower() == exe:
                    winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)


def migrate_startup():
    """Earlier versions were called Rise / Stand Up; move their startup entry over to Flow."""
    found = False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        for old in OLD_RUN_NAMES:
            try:
                winreg.QueryValueEx(key, old)
                winreg.DeleteValue(key, old)
                found = True
            except OSError:
                pass
    if found or get_startup():
        set_startup(True)  # also refreshes the path if Flow.exe moved


def chime(volume):
    """Soft three-note chime, played in the background. volume is 0-100 and follows Windows volume."""
    if volume <= 0:
        return
    rate, amp = 44100, 32767 * min(volume, 100) / 100
    samples = []
    for freq, secs in ((523, 0.16), (659, 0.16), (784, 0.45)):
        n = int(rate * secs)
        for i in range(n):
            fade = min(1.0, i / (rate * 0.01)) * (1 - i / n) ** 2  # quick attack, gentle decay
            samples.append(int(amp * fade * math.sin(2 * math.pi * freq * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    data = buf.getvalue()
    threading.Thread(target=winsound.PlaySound, args=(data, winsound.SND_MEMORY), daemon=True).start()


def send_phone(topic, title, message, priority=3):
    """Push a notification to the ntfy app on your phone, in the background.

    Returns a one-item list that becomes [True] or [False] once the send finishes.
    """
    result = []

    def post():
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(topic.strip())}",
                data=message.encode("utf-8"),
                headers={"Title": title, "Priority": str(priority), "Tags": "person_standing"})
            urllib.request.urlopen(req, timeout=10).close()
            result.append(True)
        except (OSError, ValueError):
            result.append(False)

    if topic.strip():
        threading.Thread(target=post, daemon=True).start()
    else:
        result.append(False)
    return result


# ---------- config & stats ----------

def load_config():
    cfg = dict(DEFAULTS)
    exists = os.path.exists(CONFIG_PATH)
    if exists:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            saved = json.load(f)
        cfg.update(saved)
        if "tray_effect" not in saved and "effect" in saved:
            # Older versions had one shared effect; carry it over to each element.
            old = {"Gradient": "Flow"}.get(saved["effect"], saved["effect"])
            speed = saved.get("effect_speed", "Slow")
            border = {"Circulate": "Flow"}.get(saved.get("border_style", "Circulate"), saved.get("border_style"))
            cfg.update(tray_effect=old if saved.get("fx_tray", True) else "Static", tray_speed=speed,
                       panel_effect="Off" if saved.get("fx_borders") is False else border, panel_speed=speed,
                       break_effect=old if saved.get("fx_break", True) else "Off", break_speed=speed)
        for old_key in ("effect", "effect_speed", "border_style", "fx_borders", "fx_break", "fx_tray"):
            cfg.pop(old_key, None)
        if not isinstance(cfg.get("theme_stops"), dict):
            cfg["theme_stops"] = {}
    if not cfg["ntfy_topic"]:
        # Hard-to-guess topic name: anyone who knows it can see the notifications.
        cfg["ntfy_topic"] = f"flow-{secrets.token_hex(5)}"
        exists = False
    if not exists:
        save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_stats():
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fmt_clock(seconds):
    seconds = max(0, math.ceil(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------- drawing (PIL, supersampled for smooth edges) ----------

def rounded(w, h, radius, fill, bg, outline=None, ss=4):
    img = Image.new("RGB", (w * ss, h * ss), bg)
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=fill,
        outline=outline, width=ss if outline else 0)
    return img.resize((w, h), Image.LANCZOS)


def _cap_line(d, a, b, width, fill):
    d.line((a, b), fill=fill, width=width)
    r = width / 2
    for x, y in (a, b):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def _as_colors(colors):
    return [colors] if isinstance(colors, str) else list(colors)


def gradient_arc(d, box, frac, colors, width, caps=True):
    """Clockwise arc from 12 o'clock covering frac of the circle.

    colors are spread around the whole circle, so a shorter arc shows less of the gradient.
    """
    colors = _as_colors(colors)
    if frac <= 0.004:
        return
    frac = min(frac, 1.0)
    if frac >= 0.998 and len(colors) > 1 and colors[0] != colors[-1]:
        colors = colors + [colors[0]]  # closed ring: blend the end back into the start
    x0, y0, x1, y1 = box
    c, rc = (x0 + x1) / 2, (x1 - x0) / 2 - width / 2
    if len(colors) == 1:
        if frac >= 0.998:
            d.ellipse(box, outline=colors[0], width=width)
        else:
            d.arc(box, -90, -90 + 360 * frac, fill=colors[0], width=width)
    else:
        n = max(6, int(120 * frac))
        for i in range(n):
            a0 = -90 + 360 * frac * i / n
            a1 = -90 + 360 * frac * (i + 1) / n
            d.arc(box, a0, min(a1 + 1.2, -90 + 360 * frac), fill=sample(colors, frac * (i + 0.5) / n), width=width)
    if caps and frac < 0.998:  # round caps
        for u in (0.0, frac):
            ang = math.radians(-90 + 360 * u)
            x, y = c + rc * math.cos(ang), c + rc * math.sin(ang)
            d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=sample(colors, u))


def _glow(size, draw_fn, blur, strength):
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    layer.putalpha(layer.getchannel("A").point(lambda a: int(a * strength)))
    return layer


def ring_mark(size, frac, colors, center=None, bg=None, track=(125, 135, 147, 110), stroke=34,
              glow=False, tip=False, base=256):
    """Flow's mark: a timer ring that empties clockwise as time runs out.

    colors: one colour or a gradient around the ring.
    center: None (the logo has no centre mark), or "pause" (two bars) for the paused tray icon.
    glow: soft halo under the ring.  tip: bright point where the ring ends.
    base: internal drawing size (sizes below are for 256); smaller is faster, for tiny icons.
    """
    colors = _as_colors(colors)
    S, k = base, base / 256
    img = Image.new("RGBA", (S, S), bg or (0, 0, 0, 0))
    m, t, c = round((30 if glow else 14) * k), max(1, round(stroke * k)), S / 2
    box = (m, m, S - m, S - m)
    ImageDraw.Draw(img).ellipse(box, outline=track, width=t)
    if glow:
        img.alpha_composite(_glow(S, lambda g: gradient_arc(g, box, frac, colors, t), 14 * k, 0.8))
    d = ImageDraw.Draw(img)
    gradient_arc(d, box, frac, colors, t)
    lead = sample(colors, 0)
    if tip and 0.004 < frac < 0.998:
        ang = math.radians(-90 + 360 * frac)
        rc = S / 2 - m - t / 2
        x, y = c + rc * math.cos(ang), c + rc * math.sin(ang)
        img.alpha_composite(_glow(S, lambda g: g.ellipse((x - t, y - t, x + t, y + t), fill="#ffffff"), 10 * k, 0.55))
        d = ImageDraw.Draw(img)
        r = t * 0.3
        d.ellipse((x - r, y - r, x + r, y + r), fill=mix(sample(colors, frac), "#ffffff", 0.75))
    if center == "pause":
        for dx in (-22 * k, 22 * k):
            d.rounded_rectangle((c + dx - 11 * k, c - 38 * k, c + dx + 11 * k, c + 38 * k), radius=8 * k, fill=lead)
    return img.resize((size, size), Image.LANCZOS)


def badge_image(size, color=None, bg=None):
    """Small mark used on cards and popups. color=None uses the theme gradient."""
    colors = color if color else list(THEME.stops)
    return ring_mark(size, 1.0, colors, bg=bg, track=SURFACE_2 if bg else (125, 135, 147, 110))


def app_logo(size):
    """App icon (exe, taskbar, title bars): a glowing gradient timer ring on a dark tile."""
    S = 256
    # Tile: soft vertical gradient, rounded, with a faint rim.
    grad = Image.new("RGBA", (1, S))
    for y in range(S):
        grad.putpixel((0, y), hex_rgb(mix("#1e252f", "#0a0d11", y / (S - 1))) + (255,))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((6, 6, S - 6, S - 6), radius=60, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle((6, 6, S - 6, S - 6), radius=60, outline="#2c3440", width=3)
    small = size <= 32
    r = 214 if small else 190  # bolder ring at tiny sizes so it stays readable
    ring = orient(ring_mark(r, 1.0, list(THEME.stops), track="#242b35",
                            stroke=46 if small else 30, glow=not small))
    img.alpha_composite(ring, ((S - r) // 2, (S - r) // 2))
    return img.resize((size, size), Image.LANCZOS)


# ---------- widgets ----------

class PillButton(tk.Canvas):
    @staticmethod
    def styles():  # fill, hover fill, text colour (read at build time so theme changes apply)
        return {
            "primary": (ACCENT, ACCENT_HOVER, ACCENT_INK),
            "secondary": (SURFACE_2, BORDER, FG),
            "ghost": (None, SURFACE_2, MUTED),
        }

    def __init__(self, parent, text, command, kind="secondary", bg=SURFACE, width=None, height=34, size=10):
        f = tkfont.Font(family=F.get("text_semibold", "Segoe UI"), size=size)
        w = px(width) if width else f.measure(text) + px(32)
        h = px(height)
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        fill, hover, ink = self.styles()[kind]
        self.imgs = [ImageTk.PhotoImage(rounded(w, h, px(8), c or bg, bg)) for c in (fill, hover)]
        self.bg_item = self.create_image(0, 0, anchor="nw", image=self.imgs[0])
        self.create_text(w // 2, h // 2, text=text, fill=ink, font=f)
        self.command = command
        self.bind("<Enter>", lambda e: self.itemconfig(self.bg_item, image=self.imgs[1]))
        self.bind("<Leave>", lambda e: self.itemconfig(self.bg_item, image=self.imgs[0]))
        self.bind("<ButtonRelease-1>", lambda e: self.command())


class Card(tk.Canvas):
    """Rounded surface panel. Pack widgets into .body, then call .finish()."""

    def __init__(self, parent, width, bg=BG, fill=SURFACE, pad=18, radius=12):
        super().__init__(parent, width=width, height=10, bg=bg, highlightthickness=0, bd=0)
        self.w, self.pad, self.radius, self.fill, self.outer = width, px(pad), px(radius), fill, bg
        self.body = tk.Frame(self, bg=fill)

    def finish(self):
        self.body.update_idletasks()
        h = self.body.winfo_reqheight() + 2 * self.pad
        self.config(height=h)
        self.img = ImageTk.PhotoImage(rounded(self.w, h, self.radius, self.fill, self.outer, outline=BORDER))
        self.create_image(0, 0, anchor="nw", image=self.img)
        self.create_window(self.pad, self.pad, anchor="nw", window=self.body, width=self.w - 2 * self.pad)
        return self


class Toggle(tk.Canvas):
    def __init__(self, parent, value, bg=SURFACE):
        w, h = px(42), px(24)
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.imgs = {on: ImageTk.PhotoImage(self._draw(w, h, on, bg)) for on in (True, False)}
        self.item = self.create_image(0, 0, anchor="nw")
        self.bind("<ButtonRelease-1>", lambda e: self.set(not self.value))
        self.set(bool(value))

    @staticmethod
    def _draw(w, h, on, bg, ss=4):
        W, H = w * ss, h * ss
        img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=ACCENT if on else SURFACE_2,
                            outline=None if on else BORDER, width=0 if on else ss)
        r = H / 2 - 4 * ss * UI_SCALE
        cx = W - H / 2 if on else H / 2
        d.ellipse((cx - r, H / 2 - r, cx + r, H / 2 + r), fill="#ffffff" if on else MUTED)
        return img.resize((w, h), Image.LANCZOS)

    def set(self, value):
        self.value = value
        self.itemconfig(self.item, image=self.imgs[value])


class Stepper(tk.Frame):
    def __init__(self, parent, value, lo, hi, step, suffix, bg=SURFACE):
        super().__init__(parent, bg=bg)
        self.lo, self.hi = lo, hi
        PillButton(self, "−", lambda: self.set(self.value - step), bg=bg, width=32, height=32).pack(side="left")
        self.label = tk.Label(self, font=font("text_semibold", 10), fg=FG, bg=bg, width=7)
        self.label.pack(side="left")
        PillButton(self, "+", lambda: self.set(self.value + step), bg=bg, width=32, height=32).pack(side="left")
        self.label.bind("<MouseWheel>", lambda e: self.set(self.value + (step if e.delta > 0 else -step)))
        self.suffix = suffix
        self.set(float(value))

    def set(self, value):
        self.value = max(self.lo, min(self.hi, value))
        self.label.config(text=f"{fmt_num(self.value)} {self.suffix}")


class Slider(tk.Canvas):
    def __init__(self, parent, value, on_release=None, bg=SURFACE, width=150):
        self.w, self.h, self.bg = px(width), px(24), bg
        super().__init__(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.on_release, self.on_change = on_release, None
        self.item = self.create_image(0, 0, anchor="nw")
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.bind(ev, self._drag)
        self.bind("<ButtonRelease-1>", lambda e: self.on_release and self.on_release(self.value))
        self.set(value)

    def _drag(self, e):
        k = px(8)
        self.set(round(100 * (e.x - k) / max(1, self.w - 2 * k)))

    def set(self, value):
        self.value = int(max(0, min(100, value)))
        ss = 4
        W, H = self.w * ss, self.h * ss
        img = Image.new("RGB", (W, H), self.bg)
        d = ImageDraw.Draw(img)
        k, th, cy = px(8) * ss, px(4) * ss, H / 2
        xv = k + (W - 2 * k) * self.value / 100
        d.rounded_rectangle((k, cy - th / 2, W - k, cy + th / 2), radius=th / 2, fill=SURFACE_2)
        d.rounded_rectangle((k, cy - th / 2, max(xv, k + th), cy + th / 2), radius=th / 2, fill=ACCENT)
        kr = px(7) * ss
        d.ellipse((xv - kr, cy - kr, xv + kr, cy + kr), fill="#ffffff")
        self.img = ImageTk.PhotoImage(img.resize((self.w, self.h), Image.LANCZOS))
        self.itemconfig(self.item, image=self.img)
        if self.on_change:
            self.on_change(self.value)


class NavList(tk.Frame):
    """Settings sidebar: icon + name per page; the current page gets a raised pill and an accent bar."""

    def __init__(self, parent, items, on_select, bg, width):
        super().__init__(parent, bg=bg)
        self.on_select, self.rows = on_select, {}
        h = px(40)
        selected = ImageTk.PhotoImage(rounded(width, h, px(8), SURFACE_2, bg))
        hover = ImageTk.PhotoImage(rounded(width, h, px(8), mix(bg, SURFACE_2, 0.5), bg))
        self.imgs = (selected, hover)
        for name, glyph in items:
            c = tk.Canvas(self, width=width, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
            c.pack(pady=(0, px(2)))
            c.bg_item = c.create_image(0, 0, anchor="nw")
            c.bar = c.create_rectangle(0, h * 0.28, px(3), h * 0.72, fill=ACCENT, width=0, state="hidden")
            c.icon = c.create_text(px(22), h // 2, text=glyph, font=font("icons", 11), fill=MUTED)
            c.label = c.create_text(px(44), h // 2, text=name, anchor="w", font=font("text", 10), fill=MUTED)
            c.bind("<ButtonRelease-1>", lambda e, n=name: self.select(n))
            c.bind("<Enter>", lambda e, c=c: c.itemconfig(c.bg_item, image=hover) if not c.on else None)
            c.bind("<Leave>", lambda e, c=c: c.itemconfig(c.bg_item, image="") if not c.on else None)
            c.on = False
            self.rows[name] = c

    def mark(self, name):
        for n, c in self.rows.items():
            c.on = n == name
            c.itemconfig(c.bg_item, image=self.imgs[0] if c.on else "")
            c.itemconfig(c.bar, state="normal" if c.on else "hidden")
            c.itemconfig(c.icon, fill=ACCENT if c.on else MUTED)
            c.itemconfig(c.label, fill=FG if c.on else MUTED, font=font("text_semibold" if c.on else "text", 10))

    def select(self, name):
        self.mark(name)
        self.on_select(name)


class Tabs(tk.Frame):
    """Segmented tab bar: the selected tab gets a raised pill."""

    def __init__(self, parent, names, on_select, bg=BG, size=10, pad=28, height=34):
        super().__init__(parent, bg=bg)
        self.on_select, self.tabs, self.value = on_select, {}, None
        f = tkfont.Font(family=F.get("text_semibold", "Segoe UI"), size=size)
        h = px(height)
        for name in names:
            w = f.measure(name) + px(pad)
            c = tk.Canvas(self, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
            c.imgs = {True: ImageTk.PhotoImage(rounded(w, h, px(8), SURFACE_2, bg, outline=BORDER)),
                      False: ImageTk.PhotoImage(rounded(w, h, px(8), bg, bg))}
            c.bg_item = c.create_image(0, 0, anchor="nw")
            c.text_item = c.create_text(w // 2, h // 2, text=name, font=f)
            c.bind("<ButtonRelease-1>", lambda e, n=name: self.select(n))
            c.pack(side="left", padx=(0, px(4)))
            self.tabs[name] = c

    def select(self, name):
        self.value = name
        for n, c in self.tabs.items():
            c.itemconfig(c.bg_item, image=c.imgs[n == name])
            c.itemconfig(c.text_item, fill=FG if n == name else MUTED)
        self.on_select(name)


def text_entry(parent, text, width=26, bg=SURFACE):
    border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    inner = tk.Frame(border, bg=SURFACE_2, padx=px(10), pady=px(7))
    inner.pack()
    e = tk.Entry(inner, font=font("text", 10), bg=SURFACE_2, fg=FG, insertbackground=FG,
                 relief="flat", highlightthickness=0, width=width)
    e.pack()
    e.insert(0, text)
    e.bind("<FocusIn>", lambda _: border.config(bg=ACCENT))
    e.bind("<FocusOut>", lambda _: border.config(bg=BORDER))
    return border, e


def section_label(parent, text, bg=SURFACE):
    tk.Label(parent, text=spaced(text), font=font("text_semibold", 8), fg=FAINT, bg=bg,
             anchor="w").pack(fill="x", pady=(0, px(10)))


def setting_row(parent, title, desc, make_control, bg=SURFACE, first=False):
    row = tk.Frame(parent, bg=bg)
    row.pack(fill="x", pady=(0 if first else px(14), 0))
    text = tk.Frame(row, bg=bg)
    text.pack(side="left", fill="x", expand=True)
    tk.Label(text, text=title, font=font("text", 10), fg=FG, bg=bg, anchor="w").pack(fill="x")
    if desc:
        tk.Label(text, text=desc, font=font("text", 9), fg=MUTED, bg=bg, anchor="w",
                 justify="left", wraplength=px(230)).pack(fill="x")
    control = make_control(row)
    control.pack(side="right", padx=(px(12), 0))
    return control


def window_header(parent, title, subtitle):
    tk.Label(parent, text=title, font=font("display", 18), fg=FG, bg=BG, anchor="w").pack(fill="x")
    tk.Label(parent, text=subtitle, font=font("text", 10), fg=MUTED, bg=BG, anchor="w").pack(fill="x", pady=(px(2), 0))


# ---------- windows ----------

def bar_image(w, h, frac, colors, bg, track=SURFACE_2, ss=4):
    """A slim rounded progress bar; the filled part shows the given colours as a gradient."""
    colors = [colors] if isinstance(colors, str) else list(colors)
    W, H = w * ss, h * ss
    img = Image.new("RGB", (W, H), bg)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=track)
    fw = int(W * max(0.0, min(1.0, frac)))
    if fw > H:
        grad = Image.new("RGB", (fw, H))
        gd = ImageDraw.Draw(grad)
        step = max(1, fw // 240)
        for x in range(0, fw, step):
            gd.rectangle((x, 0, x + step, H), fill=sample(colors, x / max(1, W - 1)))
        mask = Image.new("L", (fw, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, fw - 1, H - 1), radius=H // 2, fill=255)
        img.paste(grad, (0, 0), mask)
    return img.resize((w, h), Image.LANCZOS)


class BreakOverlay:
    """Full-screen, always-on-top cover over every monitor. Calm on purpose: a big, light countdown,
    one slim bar, one line of status and a gentle suggestion."""

    def __init__(self, root, phrase, on_skip, clock_only=False, skip_style="One click"):
        self.phrase, self.on_skip = phrase, on_skip
        vx, vy, vw, vh = virtual_screen()
        pw, ph = primary_screen()
        # Fit the layout to the primary screen's height.
        k = self.k = min(UI_SCALE * 1.1, ph * 0.74 / 640)

        def s(n):
            return int(round(n * k))
        self.s = s

        self.win = w = hidden_toplevel(root)
        w.overrideredirect(True)
        w.geometry(f"{vw}x{vh}+{vx}+{vy}")
        w.configure(bg=BG, cursor="arrow")
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", lambda: None)

        self.cw, ch = s(720), s(560)
        c = self.canvas = tk.Canvas(w, width=self.cw, height=ch, bg=BG, highlightthickness=0)
        c.place(x=-vx + pw // 2, y=-vy + ph // 2 - s(30), anchor="center")
        mid = self.cw // 2

        self.eyebrow = c.create_text(mid, s(30), text=spaced("Break"), fill=ACCENT,
                                     font=font("text_semibold", -s(13)))
        c.create_text(mid, s(82), text="Time to breathe", fill=FG, font=font("display_light", -s(46)))
        c.create_text(mid, s(128), text=("Step away from your desk and rest your eyes." if clock_only else
                                          "Step away from the screen. The timer only runs while you're away."),
                      fill=MUTED, font=font("text", -s(16)))

        self.clock = c.create_text(mid, s(262), fill=FG, font=font("display_light", -s(150)))

        self.bar_w, self.bar_h, self.bar_y = s(440), max(4, s(6)), s(368)
        self.bar = c.create_image(mid, self.bar_y, anchor="center")

        self.status_y = s(408)
        self.dot = c.create_oval(0, 0, 0, 0, width=0)
        self.status = c.create_text(mid, self.status_y, font=font("text", -s(15)))
        self.status_state = None

        self.tips = random.sample(TIPS, len(TIPS))
        self.tip_index, self.tip_ticks = 0, 0
        self.tip = c.create_text(mid, s(500), text=self.tips[0], fill=MUTED, width=s(620),
                                 justify="center", font=font("text", -s(17)))

        # Skipping, tucked at the bottom of the primary screen: one click, or (strict) a typed phrase.
        skip = tk.Frame(w, bg=BG)
        skip.place(x=-vx + pw // 2, y=-vy + ph - s(46), anchor="s")
        self.entry = None
        if skip_style == "Type a phrase":
            tk.Label(skip, text=f'Emergency?  Type  "{phrase}"  and press Enter',
                     font=font("text", -s(13)), fg=FAINT, bg=BG).pack(pady=(0, s(8)))
            border, self.entry = text_entry(skip, "", width=34, bg=BG)
            self.entry.config(justify="center")
            border.pack()
            self.entry.bind("<Return>", self._check_phrase)
        else:
            PillButton(skip, "Skip break", self.on_skip, kind="ghost", bg=BG, height=36, size=10).pack()

        # Glowing frame around the primary screen: bright at the edge, fading inwards.
        self.edge = None
        if BREAK_FX.effect != "Off":
            self.edge = EdgeGlow(w, lambda: BREAK_FX, bg=BG, thickness=max(8, s(28)), rect=(-vx, -vy, pw, ph),
                                 glow=True)

        self.last = (1.0, True)
        animate_in(w, vx, vy, ms=450)
        w.focus_force()

    def fx(self):
        """Advance the animated parts (called many times a second by the app)."""
        if self.edge:
            self.edge.render()
        self.canvas.itemconfig(self.eyebrow, fill=BREAK_FX.color())
        frac, away = self.last
        if away and hasattr(self, "stamp"):  # count down live between the app's once-a-second ticks
            live = fmt_clock(self.remaining - (time.monotonic() - self.stamp))
            if live != self.canvas.itemcget(self.clock, "text"):
                self.canvas.itemconfig(self.clock, text=live)
        now = time.monotonic()
        if away and BREAK_FX.animated and now - getattr(self, "_bar_drawn", 0) > 0.2:
            self._bar_drawn = now  # keep the bar's colours moving smoothly too
            self.bar_img = ImageTk.PhotoImage(orient(bar_image(self.bar_w, self.bar_h, frac,
                                                               BREAK_FX.ring_colors(), BG)))
            self.canvas.itemconfig(self.bar, image=self.bar_img)

    def _check_phrase(self, _event):
        if self.entry.get().strip().lower() == self.phrase.strip().lower():
            self.on_skip()
        else:
            self.entry.delete(0, "end")

    def _set_status(self, away):
        if self.status_state == away:
            return
        self.status_state = away
        s, c, mid = self.s, self.canvas, self.cw // 2
        text = ("Counting down while you're away" if away else "Paused while you're at the computer")
        color = MUTED if away else WARN
        c.itemconfig(self.status, text=text, fill=color)
        f = tkfont.Font(family=F.get("text"), size=-s(15))
        half = (f.measure(text) + s(18)) // 2
        r = max(3, s(4))
        dx = mid - half + r
        c.coords(self.dot, dx - r, self.status_y - r, dx + r, self.status_y + r)
        c.itemconfig(self.dot, fill=ACCENT if away else WARN)
        c.coords(self.status, mid + s(9), self.status_y)

    def update(self, remaining, total, away):
        frac = remaining / total if total else 0
        self.last = (frac, away)
        self.remaining, self.stamp = remaining, time.monotonic()
        colors = BREAK_FX.ring_colors() if away else [WARN]
        self.bar_img = ImageTk.PhotoImage(orient(bar_image(self.bar_w, self.bar_h, frac, colors, BG)))
        self.canvas.itemconfig(self.bar, image=self.bar_img)
        self.canvas.itemconfig(self.clock, text=fmt_clock(remaining))
        self._set_status(away)

        self.tip_ticks += 1
        if self.tip_ticks >= 20:
            self.tip_ticks = 0
            self.tip_index = (self.tip_index + 1) % len(self.tips)
            self.canvas.itemconfig(self.tip, text=self.tips[self.tip_index])

        # Stay on top even if another window tries to come forward.
        self.win.lift()
        self.win.attributes("-topmost", True)
        if self.win.focus_get() is None:
            self.win.focus_force()

    def destroy(self):
        self.win.destroy()


class Toast:
    """Small card in the bottom-right corner that closes itself."""

    def __init__(self, root, title, body, buttons=(), seconds=12, tone=None):
        self.win = w = hidden_toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=SURFACE)

        wrap = tk.Frame(w, bg=SURFACE, padx=px(16), pady=px(14))
        wrap.pack(fill="both")
        self.tone = tone  # set for warnings: they stay amber so they read as warnings
        self.badge = tk.Canvas(wrap, width=px(36), height=px(36), bg=SURFACE, highlightthickness=0)
        self.badge_item = self.badge.create_image(0, 0, anchor="nw")
        self.badge.pack(side="left", anchor="n")
        self._draw_badge()

        text = tk.Frame(wrap, bg=SURFACE)
        text.pack(side="left", padx=(px(12), 0))
        tk.Label(text, text=title, font=font("text_semibold", 11), fg=FG, bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(text, text=body, font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w",
                 justify="left", wraplength=px(250)).pack(fill="x", pady=(px(2), 0))
        if buttons:
            row = tk.Frame(text, bg=SURFACE)
            row.pack(fill="x", pady=(px(12), 0))
            for i, (label, action) in enumerate(buttons):
                PillButton(row, label, lambda a=action: (self.destroy(), a()),
                           kind="primary" if i == 0 else "secondary", height=30, size=9
                           ).pack(side="left", padx=(0, px(8)))

        # Shrinking bar shows when the card will close; hovering pauses it.
        self.bar = tk.Canvas(w, height=px(3), bg=SURFACE, highlightthickness=0)
        self.bar.pack(fill="x", side="bottom")
        self.bar_item = self.bar.create_rectangle(0, 0, 0, px(3), fill=tone or TRAY_FX.color(), width=0)
        self.total = self.left = seconds * 1000
        self.hover = False
        w.bind("<Enter>", lambda e: setattr(self, "hover", True))
        w.bind("<Leave>", lambda e: setattr(self, "hover", False))

        style_window(w, border=BORDER)
        w.update_idletasks()
        x, y, from_top = tray_corner(w.winfo_reqwidth(), w.winfo_reqheight(), margin=16)
        animate_in(w, x, y, rise=-px(16) if from_top else px(16))
        self._countdown()

    def _countdown(self):
        if not self.win.winfo_exists():
            return
        if not self.hover:
            self.left -= 100
        if self.left <= 0:
            return self.destroy()
        self.bar.coords(self.bar_item, 0, 0, self.bar.winfo_width() * self.left / self.total, px(3))
        if not self.tone and TRAY_FX.animated:  # move in step with the tray icon
            self._draw_badge()
            self.bar.itemconfig(self.bar_item, fill=TRAY_FX.color())
        self.win.after(100, self._countdown)

    def _draw_badge(self):
        colors = [self.tone] if self.tone else TRAY_FX.ring_colors()
        img = ring_mark(px(36), 1.0, colors, bg=SURFACE, track=SURFACE_2)
        self.badge_img = ImageTk.PhotoImage(img)
        self.badge.itemconfig(self.badge_item, image=self.badge_img)

    def destroy(self):
        if self.win.winfo_exists():
            self.win.destroy()


class WelcomeWindow:
    """'Ready to start?' card shown when the laptop starts or wakes up."""

    def __init__(self, app):
        self.app = app
        cfg = app.cfg
        self.win = w = hidden_toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=SURFACE)
        W = px(440)

        body = tk.Frame(w, bg=SURFACE, padx=px(28), pady=px(24))
        body.pack(fill="both")

        # Brand row
        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        mark = tk.Canvas(top, width=px(24), height=px(24), bg=SURFACE, highlightthickness=0)
        self.mark_img = ImageTk.PhotoImage(badge_image(px(24), bg=SURFACE))
        mark.create_image(0, 0, anchor="nw", image=self.mark_img)
        mark.pack(side="left")
        tk.Label(top, text="Flow", font=font("display", 12), fg=FG, bg=SURFACE).pack(side="left", padx=(px(8), 0))
        tk.Label(top, text=datetime.date.today().strftime("%A, %d %b").replace(" 0", " "),
                 font=font("text", 9), fg=MUTED, bg=SURFACE).pack(side="right")

        # Greeting
        tk.Label(body, text=self.greeting(), font=font("display", 24), fg=FG, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(22), 0))
        tk.Label(body, text="Ready to make this session count?", font=font("text", 12), fg=MUTED,
                 bg=SURFACE, anchor="w").pack(fill="x")

        # Motivating line, with an accent rule
        quote = tk.Frame(body, bg=SURFACE)
        quote.pack(fill="x", pady=(px(18), 0))
        tk.Frame(quote, bg=ACCENT, width=px(3)).pack(side="left", fill="y")
        tk.Label(quote, text=random.choice(WELCOME_LINES), font=font("text", 10), fg=FG, bg=SURFACE,
                 anchor="w", justify="left", wraplength=W - px(80)).pack(side="left", padx=(px(12), 0))

        # One focus
        tk.Label(body, text=spaced("Your one focus"), font=font("text_semibold", 8), fg=FAINT, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(22), px(8)))
        border, self.goal = text_entry(body, cfg["goal"], width=44)
        border.pack(anchor="w")
        tk.Label(body, text="e.g. Finish chapter 3 of my thesis. Nudges will remind you of it.",
                 font=font("text", 9), fg=FAINT, bg=SURFACE, anchor="w").pack(fill="x", pady=(px(6), 0))

        # Plan + yesterday
        info = tk.Frame(body, bg=SURFACE)
        info.pack(fill="x", pady=(px(16), 0))
        plan = f"{fmt_num(float(cfg['work_minutes']))} min focus  ·  {fmt_num(float(cfg['break_minutes']))} min breaks"
        if cfg.get("timer_mode") == "Stand-ups only":
            plan = f"Stand up every {fmt_num(float(cfg['stand_every_minutes']))} min  ·  no screen breaks"
        tk.Label(info, text=plan, font=font("text_semibold", 9), fg=MUTED, bg=SURFACE).pack(side="left")
        change = tk.Label(info, text="Change", font=font("text_semibold", 9), fg=ACCENT, bg=SURFACE, cursor="hand2")
        change.pack(side="left", padx=(px(10), 0))
        change.bind("<ButtonRelease-1>", lambda e: (w.attributes("-topmost", False), app.handle("settings")))
        tk.Label(body, text=self.yesterday(), font=font("text", 9), fg=MUTED, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(4), 0))

        # Actions
        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x", pady=(px(24), 0))
        PillButton(row, "Let's go", lambda: self.finish("go"), kind="primary", width=140, height=40,
                   size=11).pack(side="left")
        PillButton(row, "Give me 5 min", lambda: self.finish("later"), height=40).pack(side="left", padx=(px(8), 0))
        PillButton(row, "Not today", lambda: self.finish("not_today"), kind="ghost", height=40
                   ).pack(side="right")

        w.bind("<Return>", lambda e: self.finish("go"))
        w.bind("<Escape>", lambda e: self.finish("later"))
        style_window(w, border=BORDER)
        x, y = center_on_primary(w)
        animate_in(w, x, y, rise=px(20), ms=300)
        w.focus_force()
        self.goal.focus_set()
        self.goal.icursor("end")

    @staticmethod
    def greeting():
        h = datetime.datetime.now().hour
        if 5 <= h < 12:
            return "Good morning."
        if 12 <= h < 17:
            return "Good afternoon."
        if 17 <= h < 22:
            return "Good evening."
        return "Late night session?"

    def yesterday(self):
        day = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        s = self.app.stats.get(day)
        if not s or not s.get("breaks_taken"):
            return "Fresh start. Let's build the habit, one session at a time."
        text = f"Yesterday you took {s['breaks_taken']} break{'s' if s['breaks_taken'] != 1 else ''}"
        back = s.get("on_time_returns", 0) + s.get("late_returns", 0)
        if back:
            text += f" and came back on time {s.get('on_time_returns', 0)} of {back}"
        return text + ". Let's beat it."

    def finish(self, choice):
        goal = self.goal.get().strip()
        self.win.destroy()
        self.app.welcome_done(choice, goal)


class StandPrompt:
    """Card in the middle of the screen asking you to get out of the chair for a couple of minutes.
    It isn't a screen break: your focus session keeps running. Phases: ask -> moving -> done."""

    def __init__(self, app, moving=False):
        self.app = app
        self.level = 0
        self.move = random.choice(MOVES)
        self.win = w = hidden_toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=SURFACE)
        self.W = px(400)
        self.body = tk.Frame(w, bg=SURFACE, padx=px(28), pady=px(24))
        self.body.pack(fill="both")
        self.phase = None
        self.show_moving() if moving else self.show_ask()
        style_window(w, border=BORDER)
        self.edge = EdgeGlow(w, lambda: PANEL_FX if PANEL_FX.effect != "Off" else STATIC_FX, bg=SURFACE)
        x, y = self._spot()
        animate_in(w, x, y, rise=px(20), ms=300)

    def _spot(self):
        self.win.update_idletasks()
        left, top, right, bottom = work_area()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        return left + (right - left - w) // 2, top + (bottom - top - h) // 2

    def _reset(self):
        for child in self.body.winfo_children():
            child.destroy()
        tk.Frame(self.body, bg=SURFACE, width=self.W - px(56), height=0).pack()  # steady width

    def _place(self):
        if self.win.winfo_exists() and self.phase:
            x, y = self._spot()
            self.win.geometry(f"+{x}+{y}")

    def _eyebrow(self, text, color):
        top = tk.Frame(self.body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=GLYPH["stand"], font=font("icons", 16), fg=color, bg=SURFACE).pack(side="left")
        tk.Label(top, text=spaced(text), font=font("text_semibold", 8), fg=color, bg=SURFACE).pack(
            side="left", padx=(px(10), 0))

    def _suggestion(self):
        quote = tk.Frame(self.body, bg=SURFACE)
        quote.pack(fill="x", pady=(px(16), 0))
        tk.Frame(quote, bg=ACCENT, width=px(3)).pack(side="left", fill="y")
        tk.Label(quote, text=self.move, font=font("text", 10), fg=FG, bg=SURFACE, anchor="w", justify="left",
                 wraplength=self.W - px(90)).pack(side="left", padx=(px(12), 0))

    def show_ask(self):
        self.phase = "ask"
        self._reset()
        sat = max(1, round(self.app.sit_elapsed / 60))
        title, sub = STAND_NUDGES[min(self.level, len(STAND_NUDGES) - 1)]
        color = WARN if self.level else ACCENT
        self._eyebrow("Stand-up", color)
        tk.Label(self.body, text=title, font=font("display", 22), fg=FG, bg=SURFACE, anchor="w").pack(
            fill="x", pady=(px(14), 0))
        tk.Label(self.body, text=sub.format(sat=sat), font=font("text", 11), fg=MUTED, bg=SURFACE, anchor="w",
                 justify="left", wraplength=self.W - px(56)).pack(fill="x")
        self._suggestion()
        row = tk.Frame(self.body, bg=SURFACE)
        row.pack(fill="x", pady=(px(22), 0))
        PillButton(row, "I'm up", self.app.stand_up, kind="primary", width=130, height=40, size=11).pack(side="left")
        PillButton(row, "5 more min", lambda: self.app.stand_snooze(5), height=40).pack(side="left", padx=(px(8), 0))
        tk.Label(self.body, text="In a meeting? Meeting mode in the tray menu holds these back.",
                 font=font("text", 8), fg=FAINT, bg=SURFACE, anchor="w").pack(fill="x", pady=(px(14), 0))
        self._place()

    def show_moving(self):
        self.phase = "moving"
        self._reset()
        self.total = self.app.minutes("stand_for_minutes") * 60
        self.ends = time.monotonic() + self.total
        self._eyebrow("On your feet", ACCENT)
        self.clock = tk.Label(self.body, text=fmt_clock(self.total), font=font("display_light", 40), fg=FG,
                              bg=SURFACE, anchor="w")
        self.clock.pack(fill="x", pady=(px(8), 0))
        self.bar_w, self.bar_h = self.W - px(56), max(3, px(5))
        self.bar = tk.Canvas(self.body, width=self.bar_w, height=self.bar_h, bg=SURFACE, highlightthickness=0)
        self.bar.pack(anchor="w", pady=(px(4), 0))
        self.bar_item = self.bar.create_image(0, 0, anchor="nw")
        tk.Label(self.body, text="Keep moving. Flow will chime when you can sit back down.", font=font("text", 10),
                 fg=MUTED, bg=SURFACE, anchor="w", justify="left", wraplength=self.W - px(56)).pack(
            fill="x", pady=(px(12), 0))
        self._suggestion()
        row = tk.Frame(self.body, bg=SURFACE)
        row.pack(fill="x", pady=(px(20), 0))
        PillButton(row, "I'm done", lambda: self.app.stand_done(), height=36, size=10).pack(side="left")
        self._place()
        self._run_clock()

    def _run_clock(self):
        if not self.win.winfo_exists() or self.phase != "moving":
            return
        left = self.ends - time.monotonic()
        if left <= 0:
            return self.app.stand_done()
        self.clock.config(text=fmt_clock(left))
        img = orient(bar_image(self.bar_w, self.bar_h, left / self.total, list(THEME.stops), SURFACE))
        self.bar_img = ImageTk.PhotoImage(img)
        self.bar.itemconfig(self.bar_item, image=self.bar_img)
        self.win.after(200, self._run_clock)

    def show_done(self, every):
        self.phase = "done"
        self._reset()
        self._eyebrow("Nice work", ACCENT)
        tk.Label(self.body, text="Back to it.", font=font("display", 22), fg=FG, bg=SURFACE, anchor="w").pack(
            fill="x", pady=(px(14), 0))
        tk.Label(self.body, text=f"Next stand-up in {fmt_num(every)} min.", font=font("text", 11), fg=MUTED,
                 bg=SURFACE, anchor="w").pack(fill="x")
        self._place()
        self.win.after(2500, self.destroy)

    def renudge(self):
        """Ignored for a while: ask again, a bit firmer, and bring the card back to the front."""
        self.level += 1
        self.show_ask()
        self.win.lift()
        self.win.attributes("-topmost", True)

    def destroy(self):
        if self.win.winfo_exists():
            self.win.destroy()


_CreateIconFromResourceEx = user32.CreateIconFromResourceEx
_CreateIconFromResourceEx.restype = ctypes.c_void_p
_CreateIconFromResourceEx.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint]


class TrayIcon(pystray.Icon):
    """Tray icon whose clicks go to Flow: left = focus session panel, right = Flow's own menu."""

    def __init__(self, *args, on_click=None, **kwargs):
        self.on_click = on_click
        super().__init__(*args, **kwargs)

    def _assert_icon_handle(self):
        """Make the Windows icon straight from memory. (pystray writes every frame to a temp .ico
        file and loads it back, which is slow for an animated icon.)"""
        if self._icon_handle:
            return
        buf = io.BytesIO()
        self.icon.save(buf, "PNG")
        data = buf.getvalue()
        handle = _CreateIconFromResourceEx(data, len(data), True, 0x00030000, 0, 0, 0x0040)  # LR_DEFAULTSIZE
        if not handle:  # fall back to pystray's own way
            return super()._assert_icon_handle()
        self._icon_handle = wintypes.HICON(handle)

    def _on_notify(self, wparam, lparam):
        if lparam in (0x0202, 0x0205):  # WM_LBUTTONUP, WM_RBUTTONUP
            user32.SetForegroundWindow(self._hwnd)  # lets Flow's popup take keyboard focus
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self.on_click(lparam == 0x0205, pt.x, pt.y)


class MenuRow(tk.Canvas):
    """One clickable line in the tray menu: icon, label, optional hint on the right."""

    def __init__(self, parent, glyph, text, command, width, hint="", quiet=False):
        h = px(36)
        super().__init__(parent, width=width, height=h, bg=SURFACE, highlightthickness=0, bd=0, cursor="hand2")
        self.hover_img = ImageTk.PhotoImage(rounded(width, h, px(8), SURFACE_2, SURFACE))
        self.bg_item = self.create_image(0, 0, anchor="nw")
        self.icon = self.create_text(px(22), h // 2, text=glyph, font=font("icons", 11), fill=MUTED)
        self.create_text(px(44), h // 2, text=text, anchor="w", font=font("text", 10), fill=MUTED if quiet else FG)
        if hint:
            self.create_text(width - px(12), h // 2, text=hint, anchor="e", font=font("text", 9), fill=FAINT)
        self.bind("<Enter>", lambda e: (self.itemconfig(self.bg_item, image=self.hover_img),
                                        self.itemconfig(self.icon, fill=ACCENT)))
        self.bind("<Leave>", lambda e: (self.itemconfig(self.bg_item, image=""), self.itemconfig(self.icon, fill=MUTED)))
        self.bind("<ButtonRelease-1>", lambda e: command())


class TrayMenu:
    """Flow's right-click menu: status, pause, session lengths and shortcuts, in Flow's own style."""

    def __init__(self, app, x, y):
        self.app = app
        a = app
        self.win = w = hidden_toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=SURFACE)
        W = px(316)
        inner = W - px(16)
        body = tk.Frame(w, bg=SURFACE, padx=px(8), pady=px(8))
        body.pack(fill="both")

        # Header: live ring + status
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x", padx=px(10), pady=(px(6), px(10)))
        ring = tk.Canvas(head, width=px(40), height=px(40), bg=SURFACE, highlightthickness=0)
        self.ring_img = ImageTk.PhotoImage(a.render_tray(a.tray_frame()).resize((px(40), px(40)), Image.LANCZOS))
        ring.create_image(0, 0, anchor="nw", image=self.ring_img)
        ring.pack(side="left")
        text = tk.Frame(head, bg=SURFACE)
        text.pack(side="left", padx=(px(12), 0))
        tk.Label(text, text="Flow", font=font("display", 12), fg=FG, bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(text, text=a.status_text(), font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x")

        def sep():
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", padx=px(8), pady=px(6))

        def row(glyph, label, cmd, hint="", quiet=False):
            MenuRow(body, glyph, label, lambda: self.run(*cmd), inner, hint=hint, quiet=quiet).pack()

        def chips(label, glyph, options, key=None, cmd=None):
            line = tk.Frame(body, bg=SURFACE)
            line.pack(fill="x", pady=px(3))
            tk.Label(line, text=glyph, font=font("icons", 11), fg=MUTED, bg=SURFACE, width=2).pack(
                side="left", padx=(px(12), px(4)))
            tk.Label(line, text=label, font=font("text", 10), fg=FG, bg=SURFACE).pack(side="left")
            for text_, value in reversed(options):
                selected = key is not None and float(a.cfg[key]) == value
                command = (lambda v=value: self.run("set", key, v)) if key else (lambda v=value: self.run(*cmd, v))
                PillButton(line, text_, command, kind="primary" if selected else "secondary", bg=SURFACE,
                           width=40 if len(text_) <= 3 else None, height=26, size=8).pack(side="right", padx=(px(4), 0))
            tk.Frame(line, bg=SURFACE, width=px(6)).pack(side="right")

        sep()
        if a.state == "paused":
            row(GLYPH["play"], "Resume", ("resume",), hint=a.status_text().replace("Paused: ", "").replace("Paused", ""))
        elif a.state != "break":
            chips("Pause", GLYPH["pause"], [("15m", 15), ("30m", 30), ("1h", 60), ("∞", None)], cmd=("pause",))
        if a.state == "working":
            row(GLYPH["stand"], "Stand up now", ("stand_now",),
                hint=f"in {max(1, math.ceil(a.time_to_stand() / 60))} min" if a.cfg["stand_reminders"] else "")
        if a.state != "break" and not a.stand_only():
            row(GLYPH["break"], "Take a break now", ("break_now",))
        if a.state == "working" and a.cfg["breaks_enabled"] and not a.stand_only():
            row(GLYPH["skip"], "Undo skip" if a.skip_next else "Skip next break", ("skip_next",),
                hint="next break skipped" if a.skip_next else "")
        row(GLYPH["meeting"], "Meeting mode", ("snooze", 60), hint="1 hour")
        row(GLYPH["reset"], "Restart stand-up timer" if a.stand_only() else "Restart session timer", ("reset",))
        sep()
        def around(current, choices, n):
            """n choices that always include your current length (so it shows as selected)."""
            values = set(choices) | {float(current)}
            while len(values) > n:
                values.remove(max(values - {float(current)}, key=lambda v: abs(v - float(current))))
            return [(fmt_num(v), v) for v in sorted(values)]

        if a.stand_only():
            chips("Stand up every", GLYPH["stand"], around(a.cfg["stand_every_minutes"], (20, 30, 45, 60), 4),
                  key="stand_every_minutes")
        else:
            chips("Focus", GLYPH["schedule"], around(a.cfg["work_minutes"], (25, 45, 50, 60, 90), 5),
                  key="work_minutes")
            chips("Break", GLYPH["break"], around(a.cfg["break_minutes"], BREAK_CHOICES, 4), key="break_minutes")
        sep()
        row(GLYPH["panel"], "Focus session", ("panel",), hint="left-click icon")
        row(GLYPH["settings"], "Settings", ("settings",))
        row(GLYPH["stats"], "Your stats", ("stats",))
        sep()
        row(GLYPH["quit"], "Quit Flow", ("quit",), quiet=True)

        # Open beside the pointer, inside the screen (above it if the taskbar is at the bottom).
        w.update_idletasks()
        mw, mh = w.winfo_reqwidth(), w.winfo_reqheight()
        left, top, right, bottom = work_area()
        mx = min(max(left + px(8), x - mw + px(16)), right - mw - px(8))
        below = y < (top + bottom) / 2
        my = y + px(8) if below else y - mh - px(8)
        my = min(max(top + px(8), my), bottom - mh - px(8))
        style_window(w, border=BORDER)
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<FocusOut>", lambda e: w.after(150, self._close_if_unfocused))
        self.opened_at = time.monotonic()
        animate_in(w, mx, my, rise=-px(10) if below else px(10), ms=160)
        w.focus_force()

    def _close_if_unfocused(self):
        if not self.win.winfo_exists():
            return
        if time.monotonic() - self.opened_at < 0.6:
            self.win.focus_force()
            return
        if self.win.focus_get() is None:
            self.close()

    def close(self):
        if self.win.winfo_exists():
            self.win.destroy()

    def run(self, *command):
        self.close()
        self.app.handle(*command)


class SessionPanel:
    """Small card above the tray: time left, pause/resume, quick actions. Opens on left-click."""

    def __init__(self, app):
        self.app = app
        self.win = w = hidden_toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=SURFACE)
        self.W = px(380)
        self.body = tk.Frame(w, bg=SURFACE, padx=px(20), pady=px(18))
        self.body.pack(fill="both")
        self.mode = None
        self.refresh()

        style_window(w, border=frame_border())
        self.edge = EdgeGlow(w, lambda: PANEL_FX, bg=SURFACE)
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<FocusOut>", lambda e: w.after(150, self._close_if_unfocused))
        self.opened_at = self.last_seen = time.monotonic()
        w.focus_force()
        self._auto_hide()

    AUTO_HIDE_SECONDS = 20

    def _auto_hide(self):
        """Close by itself after 20 s; the countdown waits while the mouse is over the panel."""
        w = self.win
        if not w.winfo_exists():
            return
        now = time.monotonic()
        px_, py_ = w.winfo_pointerxy()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        if x <= px_ < x + w.winfo_width() and y <= py_ < y + w.winfo_height():
            self.last_seen = now
        if now - self.last_seen >= self.AUTO_HIDE_SECONDS:
            return self.close()
        w.after(500, self._auto_hide)

    def _close_if_unfocused(self):
        if not self.win.winfo_exists():
            return
        if time.monotonic() - self.opened_at < 1.0:  # clicks on the tray icon just after opening
            self.win.focus_force()
            return
        if self.win.focus_get() is None:
            self.close()

    def close(self):
        if self.win.winfo_exists():
            self.win.destroy()

    def _mode(self):
        a = self.app
        if a.state == "working":
            return "snoozed" if time.monotonic() < a.snooze_until else "running"
        return a.state

    def _place(self):
        w = self.win
        w.update_idletasks()
        self.placed_size = (w.winfo_reqwidth(), w.winfo_reqheight())
        x, y, from_top = tray_corner(*self.placed_size)
        if self.mode_was_none:
            animate_in(w, x, y, rise=-px(14) if from_top else px(14))
        else:
            w.geometry(f"+{x}+{y}")

    def refresh(self):
        mode = self._mode()
        if mode != self.mode or getattr(self, "restyle", False):
            self.restyle = False
            self.mode_was_none = self.mode is None
            self.mode = mode
            if self.mode_was_none:
                self._build()
                self._update()  # fill in the text first, so the size is final before placing
            else:
                cover = cover_window(self.win)
                self._build()
                self._update()
                uncover(self.win, cover)
            self._place()
            return
        self._update()
        w = self.win
        w.update_idletasks()
        if (w.winfo_reqwidth(), w.winfo_reqheight()) != self.placed_size:
            self._place()  # text changed its size: keep it tucked inside the screen

    def _build(self):
        a, b, mode = self.app, self.body, self.mode
        for child in b.winfo_children():
            child.destroy()

        top = tk.Frame(b, bg=SURFACE)
        top.pack(fill="x")
        mark = tk.Canvas(top, width=px(20), height=px(20), bg=SURFACE, highlightthickness=0)
        self.mark_img = ImageTk.PhotoImage(badge_image(px(20), IDLE if mode == "paused" else None, bg=SURFACE))
        mark.create_image(0, 0, anchor="nw", image=self.mark_img)
        mark.pack(side="left")
        tk.Label(top, text="Flow", font=font("display", 11), fg=FG, bg=SURFACE).pack(side="left", padx=(px(8), 0))
        close = tk.Label(top, text=GLYPH["close"], font=font("icons", 9), fg=MUTED, bg=SURFACE, cursor="hand2")
        close.pack(side="right")
        close.bind("<ButtonRelease-1>", lambda e: self.close())
        close.bind("<Enter>", lambda e: close.config(fg=FG))
        close.bind("<Leave>", lambda e: close.config(fg=MUTED))

        tk.Frame(b, bg=SURFACE, width=self.W, height=0).pack()  # same width in every state
        # Big, light time with a slim bar under it: calm, like the break screen.
        self.status = tk.Label(b, font=font("text_semibold", 9), bg=SURFACE, anchor="w")
        self.status.pack(fill="x", pady=(px(18), 0))
        self.clock = tk.Label(b, font=font("display_light", 36), fg=FG, bg=SURFACE, anchor="w")
        self.clock.pack(fill="x")
        self.bar_w, self.bar_h = self.W, max(3, px(5))  # full width of the content
        self.bar = tk.Canvas(b, width=self.bar_w, height=self.bar_h, bg=SURFACE, highlightthickness=0)
        self.bar.pack(anchor="w", pady=(px(6), 0))
        self.bar_item = self.bar.create_image(0, 0, anchor="nw")
        self.bar_key = None
        self.sub = tk.Label(b, font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w", justify="left",
                            wraplength=self.bar_w)
        self.sub.pack(fill="x", pady=(px(10), 0))
        self.stand_lbl = None
        if a.cfg["stand_reminders"] and mode in ("running", "snoozed") and not a.stand_only():
            line = tk.Frame(b, bg=SURFACE)
            line.pack(fill="x", pady=(px(8), 0))
            tk.Label(line, text=GLYPH["stand"], font=font("icons", 10), fg=MUTED, bg=SURFACE).pack(side="left")
            self.stand_lbl = tk.Label(line, font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w")
            self.stand_lbl.pack(side="left", padx=(px(6), 0))
            stand = tk.Label(line, text="Stand up now", font=font("text_semibold", 9), fg=ACCENT, bg=SURFACE,
                             cursor="hand2")
            stand.pack(side="right")
            stand.bind("<ButtonRelease-1>", lambda e: (self.close(), a.handle("stand_now")))
        if a.cfg["goal"]:
            focus = tk.Frame(b, bg=SURFACE)
            focus.pack(fill="x", pady=(px(12), 0))
            tk.Label(focus, text=spaced("Focus"), font=font("text_semibold", 7), fg=FAINT, bg=SURFACE).pack(side="left")
            tk.Label(focus, text=a.cfg["goal"], font=font("text", 10), fg=FG, bg=SURFACE, anchor="w",
                     justify="left", wraplength=self.bar_w - px(60)).pack(side="left", padx=(px(10), 0))

        def act(*command):
            return lambda: a.handle(*command)

        if mode in ("running", "snoozed"):
            section = tk.Label(b, text=spaced("Pause for"), font=font("text_semibold", 8), fg=FAINT,
                               bg=SURFACE, anchor="w")
            section.pack(fill="x", pady=(px(18), px(8)))
            chips = tk.Frame(b, bg=SURFACE)
            chips.pack(fill="x")
            for label, mins in PAUSE_CHOICES:
                PillButton(chips, label, act("pause", mins), height=30, size=9).pack(side="left", padx=(0, px(6)))
            row = tk.Frame(b, bg=SURFACE)
            row.pack(fill="x", pady=(px(16), 0))
            if a.stand_only():
                PillButton(row, "Stand up now", lambda: (self.close(), a.handle("stand_now")), kind="primary",
                           height=36, size=9).pack(side="left")
            else:
                PillButton(row, "Take a break now", act("break_now"), kind="primary", height=36,
                           size=9).pack(side="left")
            PillButton(row, "Settings", act("settings"), kind="ghost", height=36, size=9).pack(side="right")
        elif mode == "paused":
            row = tk.Frame(b, bg=SURFACE)
            row.pack(fill="x", pady=(px(18), 0))
            PillButton(row, "Resume", act("resume"), kind="primary", width=120, height=36, size=10).pack(side="left")
            PillButton(row, "Settings", act("settings"), kind="ghost", height=36, size=9).pack(side="right")
        elif mode == "waiting":
            row = tk.Frame(b, bg=SURFACE)
            row.pack(fill="x", pady=(px(18), 0))
            PillButton(row, "Start session", lambda: (self.close(), a.show_welcome()), kind="primary",
                       height=36, size=10).pack(side="left")
            PillButton(row, "Settings", act("settings"), kind="ghost", height=36, size=9).pack(side="right")

    def _update(self):
        a, mode = self.app, self.mode
        work = a.minutes("work_minutes") * 60
        left = a.time_to_break()  # live, so the seconds tick over cleanly
        frac = min(1.0, max(0.0, 1 - left / work)) if work else 0
        if a.stand_only():
            every = a.minutes("stand_every_minutes") * 60
            left = a.time_to_stand()
            frac = min(1.0, max(0.0, 1 - left / every)) if every else 0
        if a.stand_only() and mode in ("running", "snoozed"):
            if a.stand_open():
                status, color, sub = "TIME TO STAND UP", WARN, "Get out of the chair for a minute or two."
                left, frac = 0, 1
            else:
                status, color = "UNTIL YOU STAND UP", ACCENT
                sub = f"A stand-up every {fmt_num(a.minutes('stand_every_minutes'))} min. No screen breaks."
        elif mode == "running":
            status, color, sub = "FOCUS SESSION", ACCENT, ("until your break" if a.break_coming()
                                                         else "until this session ends")
        elif mode == "snoozed":
            status, color = "MEETING MODE", WARN
            sub = f"Breaks held for {max(1, math.ceil((a.snooze_until - time.monotonic()) / 60))} more min"
        elif mode == "paused":
            status, color = "PAUSED", IDLE
            sub = (f"Resumes by itself in {fmt_clock(a.pause_until - time.monotonic())}" if a.pause_until
                   else "Paused until you resume")
        elif mode == "break":
            status, color, sub = "ON A BREAK", ACCENT, "Step away from the desk"
            left, frac = a.break_remaining, 1 - a.break_remaining / a.break_total if a.break_total else 0
        elif mode == "returning":
            status, color, sub = "BREAK'S OVER", WARN, "Your next session starts when you're back"
            left, frac = 0, 1
        else:  # waiting
            status, color, sub = "NOT STARTED", MUTED, "Start a session when you're ready"
            left, frac = (a.minutes("stand_every_minutes") * 60 if a.stand_only() else work), 0
        self.status.config(text=status, fg=color)
        self.sub.config(text=sub)
        self.clock.config(text=fmt_clock(left))
        if self.stand_lbl:
            if a.stand_open():
                self.stand_lbl.config(text="Time to stand up", fg=WARN)
            else:
                mins = max(1, math.ceil(a.time_to_stand() / 60))
                self.stand_lbl.config(text=f"Stand up in {mins} min", fg=MUTED)
        # Slim bar shows the time left; redraw only when it visibly changes.
        colors = list(THEME.stops) if mode in ("running", "break") else [color if color != MUTED else IDLE]
        key = (round((1 - frac) * 400), tuple(colors))
        if key != self.bar_key:
            self.bar_key = key
            img = orient(bar_image(self.bar_w, self.bar_h, max(0.0, 1 - frac), colors, SURFACE))
            self.bar_img = ImageTk.PhotoImage(img)
            self.bar.itemconfig(self.bar_item, image=self.bar_img)


def swatch_image(size, stops, selected, bg=SURFACE, ss=4):
    """Round gradient swatch for the theme picker; the selected one gets a ring."""
    S = size * ss
    grad = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(grad)
    for x in range(S):
        d.line((x, 0, x, S), fill=sample(stops, x / (S - 1)))
    img = Image.new("RGB", (S, S), bg)
    inset = 5 * ss if selected else 2 * ss
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((inset, inset, S - inset, S - inset), fill=255)
    img.paste(grad, (0, 0), mask)
    if selected:
        ImageDraw.Draw(img).ellipse((ss, ss, S - ss, S - ss), outline=FG, width=int(1.5 * ss))
    return img.resize((size, size), Image.LANCZOS)


def wheel_image(n, bg):
    """HSV colour wheel: hue around the circle, saturation from the centre out."""
    img = Image.new("RGB", (n, n), bg)
    pix = img.load()
    r = n / 2
    for y in range(n):
        dy = y + 0.5 - r
        for x in range(n):
            dx = x + 0.5 - r
            dist = math.hypot(dx, dy)
            if dist <= r:
                h = (math.atan2(-dy, dx) / (2 * math.pi)) % 1
                pix[x, y] = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(1.0, dist / r), 1.0))
    mask = Image.new("L", (n * 4, n * 4), 0)  # smooth rim
    ImageDraw.Draw(mask).ellipse((2, 2, n * 4 - 3, n * 4 - 3), fill=255)
    out = Image.new("RGB", (n, n), bg)
    out.paste(img, (0, 0), mask.resize((n, n), Image.LANCZOS))
    return out


class ColorPicker:
    """Colour wheel + brightness + hex/RGB fields."""

    def __init__(self, parent, initial, on_pick):
        self.on_pick, self._busy = on_pick, False
        r, g, b = hex_rgb(initial)
        self.h, self.s, self.v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

        self.win = w = hidden_toplevel(parent)
        w.title("Flow · Pick a colour")
        w.configure(bg=BG)
        w.resizable(False, False)
        outer = tk.Frame(w, bg=BG)
        outer.pack(padx=px(24), pady=(px(20), px(22)))
        window_header(outer, "Pick a colour", "Click or drag on the wheel, or type exact values.")

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="x", pady=(px(18), 0))
        n = self.n = px(220)
        self.wheel = tk.Canvas(body, width=n, height=n, bg=BG, highlightthickness=0, cursor="crosshair")
        self.wheel.pack(side="left")
        self.wheel_img = ImageTk.PhotoImage(wheel_image(n, BG))
        self.wheel.create_image(0, 0, anchor="nw", image=self.wheel_img)
        self.ring_out = self.wheel.create_oval(0, 0, 0, 0, outline="#000000", width=3)
        self.ring_in = self.wheel.create_oval(0, 0, 0, 0, outline="#ffffff", width=2)
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.wheel.bind(ev, self._wheel)

        side = tk.Frame(body, bg=BG)
        side.pack(side="left", fill="y", padx=(px(22), 0))
        self.swatch = tk.Canvas(side, width=px(160), height=px(56), bg=BG, highlightthickness=0)
        self.swatch.pack(anchor="w")
        self.swatch_item = self.swatch.create_image(0, 0, anchor="nw")

        def caption(text):
            tk.Label(side, text=text, font=font("text", 9), fg=MUTED, bg=BG, anchor="w").pack(
                fill="x", pady=(px(12), px(4)))

        caption("Brightness")
        self.bright = Slider(side, self.v * 100, bg=BG, width=160)
        self.bright.on_change = self._bright
        self.bright.pack(anchor="w")
        caption("Hex")
        border, self.hex = text_entry(side, "", width=12, bg=BG)
        border.pack(anchor="w")
        caption("R        G        B")
        row = tk.Frame(side, bg=BG)
        row.pack(anchor="w")
        self.rgb = []
        for i in range(3):
            border, e = text_entry(row, "", width=3, bg=BG)
            border.pack(side="left", padx=(0, px(6)))
            e.bind("<Return>", self._typed_rgb)
            e.bind("<FocusOut>", self._typed_rgb)
            self.rgb.append(e)
        self.hex.bind("<Return>", self._typed_hex)
        self.hex.bind("<KeyRelease>", self._typed_hex)

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(px(20), 0))
        PillButton(foot, "Use this colour", self._use, kind="primary", bg=BG).pack(side="right")
        PillButton(foot, "Cancel", w.destroy, bg=BG).pack(side="right", padx=(0, px(8)))

        self.refresh()
        style_window(w, border=BORDER)
        x, y = center_on_primary(w)
        animate_in(w, x, y)
        w.focus_force()  # not modal: it must never lock Flow's other windows

    def value(self):
        return hsv_hex(self.h, self.s, self.v)

    def _wheel(self, e):
        r = self.n / 2
        dx, dy = e.x - r, e.y - r
        self.h = (math.atan2(-dy, dx) / (2 * math.pi)) % 1
        self.s = min(1.0, math.hypot(dx, dy) / r)
        if self.v < 0.15:  # picking a hue on a black colour would show no change
            self.v = 1.0
        self.refresh()

    def _bright(self, v):
        if not self._busy:
            self.v = v / 100
            self.refresh(source="bright")

    def _set_rgb(self, rgb):
        self.h, self.s, self.v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))

    def _typed_hex(self, _e=None):
        v = valid_hex(self.hex.get())
        if v and v != self.value():
            self._set_rgb(hex_rgb(v))
            self.refresh(source="hex")

    def _typed_rgb(self, _e=None):
        try:
            rgb = [max(0, min(255, int(e.get()))) for e in self.rgb]
        except ValueError:
            return self.refresh()
        self._set_rgb(rgb)
        self.refresh(source="rgb")

    def refresh(self, source=None):
        self._busy = True
        value = self.value()
        self.swatch_img = ImageTk.PhotoImage(rounded(px(160), px(56), px(12), value, BG, outline=BORDER))
        self.swatch.itemconfig(self.swatch_item, image=self.swatch_img)
        r = self.n / 2
        ang = self.h * 2 * math.pi
        x, y = r + self.s * (r - px(2)) * math.cos(ang), r - self.s * (r - px(2)) * math.sin(ang)
        k = px(8)
        self.wheel.coords(self.ring_out, x - k, y - k, x + k, y + k)
        self.wheel.coords(self.ring_in, x - k + 2, y - k + 2, x + k - 2, y + k - 2)
        if source != "bright":
            self.bright.set(self.v * 100)
        if source != "hex":
            self.hex.delete(0, "end")
            self.hex.insert(0, value)
        if source != "rgb":
            for e, c in zip(self.rgb, hex_rgb(value)):
                e.delete(0, "end")
                e.insert(0, str(c))
        self._busy = False

    def _use(self):
        value = self.value()
        self.win.destroy()
        self.on_pick(value)


class SettingsWindow:
    PAGES = (("Schedule", "schedule"), ("Stand up", "stand"), ("Break", "break"), ("Back to work", "back"),
             ("Appearance", "appearance"), ("General", "general"))
    TABS = tuple(name for name, _ in PAGES)
    PAGE_INFO = {
        "Schedule": "How long you focus, and how long you rest.",
        "Stand up": "Get out of the chair regularly, even in the middle of a session.",
        "Break": "What happens on the break screen.",
        "Back to work": "Getting you back to your desk after a break.",
        "Appearance": "Colours and animations.",
        "General": "Sound, startup and the welcome screen.",
    }
    APPEARANCE_KEYS = ("theme", "accent", "theme_stops", "window_anim", "ring_direction", "tray_effect", "tray_speed",
                       "panel_effect", "panel_speed", "break_effect", "break_speed")

    def __init__(self, app, tab=None):
        self.app = app
        self.current = tab or self.TABS[0]
        self.picker = self.advanced = None
        self.win = w = hidden_toplevel(app.root)
        w.title("Flow \u00b7 Settings")
        w.configure(bg=BG)
        w.resizable(False, False)
        self.W = px(480)
        self._build()
        style_window(w, border=BORDER)
        x, y = center_on_primary(w)
        animate_in(w, x, y)
        w.focus_force()
        self._animate_preview()

    def _build(self):
        """Build (or rebuild, after a theme change) everything inside the window, in place."""
        app, cfg, w, W = self.app, self.app.cfg, self.win, self.W
        w.iconphoto(False, *app.window_icon)
        self.outer = outer = tk.Frame(w, bg=BG)  # packed at the end, so a rebuild swaps in one step
        new_outer = outer

        # Sidebar: brand + page list with icons.
        side = tk.Frame(outer, bg=SIDEBAR)
        side.pack(side="left", fill="y")
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(24), px(22)))
        mark = tk.Canvas(brand, width=px(30), height=px(30), bg=SIDEBAR, highlightthickness=0)
        self.mark_img = ImageTk.PhotoImage(badge_image(px(30), bg=SIDEBAR))
        mark.create_image(0, 0, anchor="nw", image=self.mark_img)
        mark.pack(side="left")
        names = tk.Frame(brand, bg=SIDEBAR)
        names.pack(side="left", padx=(px(10), 0))
        tk.Label(names, text="Flow", font=font("display", 13), fg=FG, bg=SIDEBAR, anchor="w").pack(fill="x")
        tk.Label(names, text="Settings", font=font("text", 9), fg=MUTED, bg=SIDEBAR, anchor="w").pack(fill="x")
        self.nav = NavList(side, [(name, GLYPH[icon]) for name, icon in self.PAGES], self.show,
                           bg=SIDEBAR, width=px(184))
        self.nav.pack(padx=px(12))
        tk.Label(side, text="Changes apply when\nyou press Save.", font=font("text", 8), fg=FAINT, bg=SIDEBAR,
                 justify="left", anchor="w").pack(side="bottom", fill="x", padx=px(24), pady=px(20))

        main = tk.Frame(outer, bg=BG)
        main.pack(side="left", fill="both", padx=px(28), pady=(px(22), px(22)))
        self.page_title = tk.Label(main, font=font("display", 18), fg=FG, bg=BG, anchor="w")
        self.page_title.pack(fill="x")
        self.page_sub = tk.Label(main, font=font("text", 10), fg=MUTED, bg=BG, anchor="w")
        self.page_sub.pack(fill="x", pady=(px(2), 0))
        outer = main

        self.content = tk.Frame(outer, bg=BG)
        self.content.pack(fill="x", pady=(px(16), 0))
        self.pages = {name: tk.Frame(self.content, bg=BG) for name in self.TABS}
        self._schedule(self.pages["Schedule"], cfg)
        self._stand(self.pages["Stand up"], cfg)
        self._break_screen(self.pages["Break"], cfg)
        self._back_to_work(self.pages["Back to work"], cfg)
        self._appearance(self.pages["Appearance"], cfg)
        self._general(self.pages["General"], cfg)
        # Fixed height so the window doesn't jump around between tabs.
        self.content.update_idletasks()
        tallest = max(p.winfo_reqheight() for p in self.pages.values())
        self.content.config(width=W, height=tallest)
        self.content.pack_propagate(False)

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(px(18), 0))
        PillButton(foot, "Advanced...", self.open_advanced, kind="ghost", bg=BG).pack(side="left")
        PillButton(foot, "Save", self.save, kind="primary", bg=BG).pack(side="right")
        PillButton(foot, "Close", w.destroy, bg=BG).pack(side="right", padx=(0, px(8)))
        self.saved = tk.Label(foot, text="", font=font("text_semibold", 9), fg=ACCENT, bg=BG)
        self.saved.pack(side="right", padx=(0, px(12)))

        self.nav.select(self.current)
        old = getattr(self, "_old_outer", None)
        if old is not None:
            old.destroy()
            self._old_outer = None
        new_outer.pack(fill="both", expand=True)

    def show(self, name):
        self.current = name
        self.nav.mark(name)
        self.page_title.config(text=name)
        self.page_sub.config(text=self.PAGE_INFO[name])
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="x", anchor="n")

    def flash_saved(self):
        self.saved.config(text="✓  Saved")
        if getattr(self, "_saved_timer", None):
            self.win.after_cancel(self._saved_timer)
        self._saved_timer = self.win.after(2500, lambda: self.saved.winfo_exists() and self.saved.config(text=""))

    # -- appearance page

    def _appearance(self, page, cfg):
        fx_keys = tuple(f"{p}_{k}" for p, _, _ in FX_ELEMENTS for k in ("effect", "speed"))
        self.pending = {k: cfg[k] for k in ("theme", "accent", "window_anim", "ring_direction") + fx_keys}
        edited = cfg.get("theme_stops") if isinstance(cfg.get("theme_stops"), dict) else {}
        self.pending["theme_stops"] = {k: list(v) for k, v in edited.items()}  # a copy we can edit
        self.fx_edge = self.fx_ring = None

        # 1. Colours: the palette used everywhere. It never animates on its own.
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Colours")
        top = tk.Frame(b, bg=SURFACE)
        top.pack(fill="x")
        grid = tk.Frame(top, bg=SURFACE)
        grid.pack(side="left")
        self.swatches = {}
        for i, name in enumerate(list(THEMES) + ["Custom"]):
            cell = tk.Frame(grid, bg=SURFACE)
            cell.grid(row=0, column=i, padx=(0, px(8)))
            cv = tk.Canvas(cell, width=px(32), height=px(32), bg=SURFACE, highlightthickness=0, cursor="hand2")
            cv.pack()
            cv.item = cv.create_image(0, 0, anchor="nw")
            cv.bind("<ButtonRelease-1>", lambda e, n=name: self.pick_theme(n))
            tk.Label(cell, text=name, font=font("text", 8), fg=MUTED, bg=SURFACE).pack()
            self.swatches[name] = cv
        self.logo_preview = tk.Canvas(top, width=px(56), height=px(56), bg=SURFACE, highlightthickness=0)
        self.logo_item = self.logo_preview.create_image(0, 0, anchor="nw")
        self.logo_preview.pack(side="right")

        row = tk.Frame(b, bg=SURFACE)
        row.pack(fill="x", pady=(px(12), 0))
        tk.Label(row, text="Custom colour", font=font("text", 10), fg=FG, bg=SURFACE).pack(side="left")
        PillButton(row, "Colour wheel...", self.open_picker, height=32, size=9).pack(side="right")
        border, self.hex_entry = text_entry(row, Theme(self.pending).accent, width=9)
        border.pack(side="right", padx=(0, px(8)))
        self.hex_entry.bind("<KeyRelease>", self._hex_typed)

        row = tk.Frame(b, bg=SURFACE)
        row.pack(fill="x", pady=(px(12), 0))
        tk.Label(row, text="Gradient", font=font("text", 10), fg=FG, bg=SURFACE).pack(side="left")
        self.chips = tk.Frame(row, bg=SURFACE)
        self.chips.pack(side="right")
        tk.Label(b, text="The colours your animations flow through. Click one to change it, + to add, \u00d7 to remove.",
                 font=font("text", 9), fg=FAINT, bg=SURFACE, anchor="w", justify="left",
                 wraplength=self.W - px(40)).pack(fill="x", pady=(px(6), 0))
        card.finish().pack()

        # 2. Animations: each element is set on its own.
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Animations")
        self.fx_tabs = Tabs(b, [name for _, name, _ in FX_ELEMENTS], self.show_fx, bg=SURFACE,
                            size=9, pad=22, height=30)
        self.fx_tabs.pack(anchor="w")
        self.fx_preview = tk.Frame(b, bg=BG, width=self.W - px(36), height=px(76))
        self.fx_preview.pack(pady=(px(12), 0))
        self.fx_preview.pack_propagate(False)
        self.fx_controls = tk.Frame(b, bg=SURFACE)
        self.fx_controls.pack(fill="x", pady=(px(12), 0))
        self.fx_tabs.select(getattr(self, "fx_name", FX_ELEMENTS[0][1]))  # stay where you were
        card.finish().pack(pady=(px(12), 0))

        # 3. Motion: things that aren't about colour.
        card = Card(page, self.W)
        b = card.body
        def choice(label, options, key, first=False):
            def make(parent):
                t = Tabs(parent, options, lambda v, k=key: self.pending.__setitem__(k, v), bg=SURFACE,
                         size=9, pad=20, height=30)
                t.select(self.pending[key])
                return t
            setting_row(b, label, None, make, first=first)

        choice("Rings run down", RING_DIRECTIONS, "ring_direction", first=True)
        choice("Windows appear", WINDOW_ANIMS, "window_anim")
        card.finish().pack(pady=(px(12), 0))
        self.refresh_swatches()

    FX_HINTS = {
        "tray": "The ring in your taskbar tray. Flow turns the gradient around it; Breathe gently pulses it.",
        "panel": "The edge of the panel you open from the tray. Flow sends the colours slowly around it.",
        "break": "The glowing frame around your screen and the countdown bar while you're on a break.",
    }

    def pending_motion(self):
        p = self.fx_prefix
        return Motion(self.pending[f"{p}_effect"], self.pending[f"{p}_speed"], theme=Theme(self.pending))

    def show_fx(self, name):
        """Show the controls and a live preview for one animated element."""
        self.fx_name = name
        prefix, _, effects = next(e for e in FX_ELEMENTS if e[1] == name)
        self.fx_prefix = prefix
        for frame in (self.fx_controls, self.fx_preview):
            for child in frame.winfo_children():
                child.destroy()
        self.fx_edge = self.fx_ring = self.fx_bar = None

        def choice(label, options, key, first=False):
            def make(parent):
                t = Tabs(parent, options, lambda v, k=key: self.pending.__setitem__(k, v), bg=SURFACE,
                         size=9, pad=20, height=30)
                t.select(self.pending[key] if self.pending[key] in options else options[1])
                return t
            setting_row(self.fx_controls, label, None, make, first=first)

        choice("Effect", effects, f"{prefix}_effect", first=True)
        choice("Speed", tuple(SPEEDS), f"{prefix}_speed")
        tk.Label(self.fx_controls, text=self.FX_HINTS[prefix], font=font("text", 9), fg=FAINT, bg=SURFACE,
                 anchor="w", justify="left", wraplength=self.W - px(40)).pack(fill="x", pady=(px(8), 0))

        pv = self.fx_preview
        if prefix == "tray":
            self.fx_ring = tk.Canvas(pv, width=px(56), height=px(56), bg=BG, highlightthickness=0)
            self.fx_ring.item = self.fx_ring.create_image(0, 0, anchor="nw")
            self.fx_ring.place(relx=0.5, rely=0.5, anchor="center")
            self.fx_ring.stroke, self.fx_ring.glow = 40, False
        elif prefix == "panel":
            mini = tk.Frame(pv, bg=SURFACE, width=px(250), height=px(58))
            mini.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(mini, text=spaced("Focus session"), font=font("text_semibold", 7), fg=ACCENT,
                     bg=SURFACE).place(x=px(16), y=px(12))
            tk.Label(mini, text="27:39", font=font("display_light", 14), fg=FG, bg=SURFACE).place(x=px(14), y=px(26))
            self.fx_edge = EdgeGlow(mini, self.pending_motion, bg=SURFACE)
        else:
            self.fx_edge = EdgeGlow(pv, self.pending_motion, bg=BG, thickness=px(9), glow=True)
            mini = tk.Frame(pv, bg=BG)
            mini.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(mini, text="04:12", font=font("display_light", 16), fg=FG, bg=BG).pack()
            self.fx_bar = tk.Canvas(mini, width=px(150), height=px(4), bg=BG, highlightthickness=0)
            self.fx_bar.item = self.fx_bar.create_image(0, 0, anchor="nw")
            self.fx_bar.pack(pady=(px(4), 0))


    def refresh_swatches(self):
        for name, cv in self.swatches.items():
            stops = theme_stops(self.pending, name)
            cv.img = ImageTk.PhotoImage(swatch_image(px(32), stops, name == self.pending["theme"]))
            cv.itemconfig(cv.item, image=cv.img)
        self.refresh_gradient()
        # Still preview of the chosen colours (the mark used on Flow's windows and icon).
        img = ring_mark(px(56), 1.0, list(Theme(self.pending).stops), track=SURFACE_2, glow=True,
                        bg=SURFACE)
        self.logo_preview.img = ImageTk.PhotoImage(img)
        self.logo_preview.itemconfig(self.logo_item, image=self.logo_preview.img)

    def pick_theme(self, name):
        self.pending["theme"] = name
        self.hex_entry.delete(0, "end")
        self.hex_entry.insert(0, Theme(self.pending).accent)
        self.refresh_swatches()

    def set_custom(self, value):
        self.pending.update(theme="Custom", accent=value)
        self.pending["theme_stops"].pop("Custom", None)  # a new custom colour gets a fresh gradient
        self.refresh_swatches()

    # -- gradient editor

    def refresh_gradient(self):
        """Chips for the current theme's gradient: click to change, x to remove, + to add."""
        for child in self.chips.winfo_children():
            child.destroy()
        stops = list(theme_stops(self.pending))
        n = px(28)
        for i, color in enumerate(stops):
            c = tk.Canvas(self.chips, width=n, height=n, bg=SURFACE, highlightthickness=0, cursor="hand2")
            c.pack(side="left", padx=(0, px(6)))
            c.img = ImageTk.PhotoImage(rounded(n, n, px(7), color, SURFACE, outline=BORDER))
            c.create_image(0, 0, anchor="nw", image=c.img)
            if len(stops) > 2:  # remove button, shown on hover
                k = px(13)
                ink = max(("#15110f", "#ffffff"), key=lambda x: contrast(x, color))
                c.x = c.create_text(n - k // 2 - 1, k // 2 + 1, text="\u00d7", fill=ink,
                                    font=font("text_semibold", 9), state="hidden")
                c.bind("<Enter>", lambda e, c=c: c.itemconfig(c.x, state="normal"))
                c.bind("<Leave>", lambda e, c=c: c.itemconfig(c.x, state="hidden"))
            c.bind("<ButtonRelease-1>", lambda e, i=i, n=n: self._chip_click(e, i, n))
        if len(stops) < MAX_STOPS:
            add = tk.Canvas(self.chips, width=n, height=n, bg=SURFACE, highlightthickness=0, cursor="hand2")
            add.pack(side="left", padx=(0, px(6)))
            add.img = ImageTk.PhotoImage(rounded(n, n, px(7), SURFACE_2, SURFACE, outline=BORDER))
            add.create_image(0, 0, anchor="nw", image=add.img)
            add.create_text(n // 2, n // 2, text="+", fill=FG, font=font("text_semibold", 11))
            add.bind("<ButtonRelease-1>", lambda e: self._edit_stop(len(stops), stops[-1]))
        if self.pending["theme"] in self.pending["theme_stops"]:
            PillButton(self.chips, "Reset", self._reset_gradient, kind="ghost", height=28, size=9).pack(side="left")

    def _chip_click(self, event, i, n):
        stops = list(theme_stops(self.pending))
        if len(stops) > 2 and event.x > n - px(13) and event.y < px(13):  # the x in the corner
            del stops[i]
            self._set_stops(stops)
        else:
            self._edit_stop(i, stops[i])

    def _edit_stop(self, i, initial):
        def picked(value):
            stops = list(theme_stops(self.pending))
            if i < len(stops):
                stops[i] = value
            else:
                stops.append(value)
            self._set_stops(stops)
        self._open_wheel(initial, picked)

    def _set_stops(self, stops):
        self.pending["theme_stops"][self.pending["theme"]] = stops
        self.hex_entry.delete(0, "end")
        self.hex_entry.insert(0, Theme(self.pending).accent)
        self.refresh_swatches()

    def _reset_gradient(self):
        self.pending["theme_stops"].pop(self.pending["theme"], None)
        self.hex_entry.delete(0, "end")
        self.hex_entry.insert(0, Theme(self.pending).accent)
        self.refresh_swatches()

    def _open_wheel(self, initial, on_pick):
        if self.picker and self.picker.win.winfo_exists():
            self.picker.win.destroy()
        self.picker = ColorPicker(self.win, initial, on_pick)

    def _hex_typed(self, _e=None):
        value = valid_hex(self.hex_entry.get())
        if value and (value != self.pending["accent"] or self.pending["theme"] != "Custom"):
            if value != Theme(self.pending).accent:  # typing the preset's own colour keeps the preset
                self.set_custom(value)

    def open_picker(self):
        def picked(value):
            self.hex_entry.delete(0, "end")
            self.hex_entry.insert(0, value)
            self.set_custom(value)
        self._open_wheel(Theme(self.pending).accent, picked)

    def _animate_preview(self):
        """Live preview of the selected animated element, in the chosen colours and effect."""
        if not self.win.winfo_exists():
            return
        if self.current == "Appearance":
            try:
                if self.fx_edge:
                    self.fx_edge.render()
                ring = self.fx_ring
                bar = getattr(self, "fx_bar", None)
                if bar and bar.winfo_exists():
                    frac = 1 - (time.monotonic() % 12) / 12
                    img = bar_image(px(150), px(4), max(frac, 0.03), self.pending_motion().ring_colors(), BG)
                    if self.pending["ring_direction"] == "Counter-clockwise":
                        img = ImageOps.mirror(img)
                    bar.img = ImageTk.PhotoImage(img)
                    bar.itemconfig(bar.item, image=bar.img)
                if ring and ring.winfo_exists():
                    frac = 1 - (time.monotonic() % 12) / 12
                    size = int(ring["width"])
                    img = ring_mark(size, max(frac, 0.02), self.pending_motion().ring_colors(),
                                    track=SURFACE_2, stroke=ring.stroke, glow=ring.glow, bg=BG)
                    if self.pending["ring_direction"] == "Counter-clockwise":
                        img = ImageOps.mirror(img)
                    ring.img = ImageTk.PhotoImage(img)
                    ring.itemconfig(ring.item, image=ring.img)
            except tk.TclError:
                pass  # switching elements mid-frame
        self.win.after(90, self._animate_preview)


    # -- pages

    def _schedule(self, page, cfg):
        card = Card(page, self.W)
        b = card.body
        section_label(b, "What Flow times")

        def mode_choice(parent):
            t = Tabs(parent, TIMER_MODES, lambda v: None, bg=SURFACE, size=9, pad=20, height=30)
            t.select(cfg["timer_mode"] if cfg["timer_mode"] in TIMER_MODES else TIMER_MODES[0])
            return t
        self.timer_mode = setting_row(b, "Timer", None, mode_choice, first=True)
        tk.Label(b, text="Stand-ups only: no sessions or screen breaks. The timer counts down to your next "
                         "stand-up (set how often on the Stand up page).", font=font("text", 9), fg=FAINT,
                 bg=SURFACE, anchor="w", justify="left", wraplength=self.W - px(40)).pack(fill="x", pady=(px(6), 0))
        card.finish().pack()

        card = Card(page, self.W)
        b = card.body
        section_label(b, "Durations")
        self.work = setting_row(b, "Work session", "How long you sit before a break.",
                                lambda p: Stepper(p, cfg["work_minutes"], 5, 240, 5, "min"), first=True)
        self.brk = setting_row(b, "Break length", "Time away from the desk.",
                               lambda p: Stepper(p, cfg["break_minutes"], 1, 60, 1, "min"))
        self.warn = setting_row(b, "Heads-up", "A reminder before the break starts.",
                                lambda p: Stepper(p, cfg["warn_minutes_before"], 0, 15, 1, "min"))
        chips = tk.Frame(b, bg=SURFACE)
        chips.pack(fill="x", pady=(px(16), 0))
        tk.Label(chips, text="Presets", font=font("text", 9), fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, px(10)))
        for label, (wm, bm) in PRESETS.items():
            name = label.split()[0]
            PillButton(chips, f"{name}  {wm}/{bm}", lambda wm=wm, bm=bm: (self.work.set(wm), self.brk.set(bm)),
                       height=28, size=9).pack(side="left", padx=(0, px(6)))
        tk.Label(b, text="Tip: scroll over a number to change it quickly. You can also change durations "
                         "from the tray icon's menu.", font=font("text", 9), fg=FAINT, bg=SURFACE,
                 anchor="w", justify="left", wraplength=self.W - px(40)).pack(fill="x", pady=(px(14), 0))
        card.finish().pack(pady=(px(12), 0))

    def _stand(self, page, cfg):
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Stand-up reminders")
        self.stand_on = setting_row(b, "Remind me to stand up",
                                    "A card asks you to move for a moment. Your session keeps running.",
                                    lambda p: Toggle(p, cfg["stand_reminders"]), first=True)
        self.stand_every = setting_row(b, "Every", "How long you can sit before Flow asks.",
                                       lambda p: Stepper(p, cfg["stand_every_minutes"], 10, 120, 5, "min"))
        self.stand_for = setting_row(b, "Move for", "A short countdown while you're on your feet.",
                                     lambda p: Stepper(p, cfg["stand_for_minutes"], 1, 10, 1, "min"))
        self.stand_again = setting_row(b, "Ask again after", "If the card is ignored, it comes back firmer.",
                                       lambda p: Stepper(p, cfg["stand_renudge_minutes"], 1, 15, 1, "min"))
        tk.Label(b, text="Screen breaks count as standing up. No reminder shows when a break is less than "
                         "5 minutes away, while paused, or in meeting mode.", font=font("text", 9), fg=FAINT,
                 bg=SURFACE, anchor="w", justify="left", wraplength=self.W - px(40)).pack(fill="x", pady=(px(14), 0))
        card.finish().pack()

    def _break_screen(self, page, cfg):
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Break screen")
        self.breaks_on = setting_row(b, "Screen breaks", "Off: no break screen. Sessions roll straight into the "
                                     "next, and stand-up reminders still keep you moving.",
                                     lambda p: Toggle(p, cfg["breaks_enabled"]), first=True)
        self.reset = setting_row(b, "Strict mode", "Touching the computer restarts the break instead of pausing it.",
                                 lambda p: Toggle(p, cfg["reset_break_on_input"]))

        def skip_choice(parent):
            t = Tabs(parent, ("One click", "Type a phrase"), lambda v: None, bg=SURFACE, size=9, pad=20, height=30)
            t.select(cfg["skip_style"] if cfg["skip_style"] in ("One click", "Type a phrase") else "One click")
            return t
        self.skip_style = setting_row(b, "Skipping a break", None, skip_choice)
        tk.Label(b, text="Skip phrase", font=font("text", 10), fg=FG, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(14), 0))
        tk.Label(b, text="Used when skipping is set to \"Type a phrase\".",
                 font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x", pady=(0, px(8)))
        border, self.phrase = text_entry(b, cfg["skip_phrase"], width=46)
        border.pack(anchor="w")
        card.finish().pack()

    def _back_to_work(self, page, cfg):
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Nudges")
        self.nudges = setting_row(b, "Nudge me back to work",
                                  "If you haven't come back after a break, keep reminding me.",
                                  lambda p: Toggle(p, cfg["return_nudges"]), first=True)
        self.grace = setting_row(b, "First nudge", "Minutes after the break ends.",
                                 lambda p: Stepper(p, cfg["return_grace_minutes"], 1, 30, 1, "min"))
        self.every = setting_row(b, "Then every", "Up to 5 nudges, each one a bit firmer.",
                                 lambda p: Stepper(p, cfg["return_nudge_every_minutes"], 1, 30, 1, "min"))
        tk.Label(b, text="Your goal", font=font("text", 10), fg=FG, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(14), 0))
        tk.Label(b, text="What you're working towards. Nudges will remind you of it.",
                 font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x", pady=(0, px(8)))
        border, self.goal = text_entry(b, cfg["goal"], width=46)
        border.pack(anchor="w")
        card.finish().pack()

        card = Card(page, self.W)
        b = card.body
        section_label(b, "Phone notifications")
        self.phone = setting_row(b, "Send nudges to my phone",
                                 "So you get them even if you're away from the computer.",
                                 lambda p: Toggle(p, cfg["phone_notify"]), first=True)
        tk.Label(b, text="1. Install the free ntfy app (Android or iPhone).\n"
                         "2. Tap +, then subscribe to this topic:",
                 font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w", justify="left"
                 ).pack(fill="x", pady=(px(12), px(8)))
        row = tk.Frame(b, bg=SURFACE)
        row.pack(fill="x")
        border, self.topic = text_entry(row, cfg["ntfy_topic"], width=30)
        border.pack(side="left")
        PillButton(row, "Send test", self.send_test, height=34, size=9).pack(side="left", padx=(px(8), 0))
        self.test_status = tk.Label(b, text="Keep the topic private: anyone who knows it can read these.",
                                    font=font("text", 9), fg=FAINT, bg=SURFACE, anchor="w")
        self.test_status.pack(fill="x", pady=(px(8), 0))
        card.finish().pack(pady=(px(12), 0))

    def _general(self, page, cfg):
        card = Card(page, self.W)
        b = card.body
        section_label(b, "Sound & startup")

        def volume_control(parent):
            f = tk.Frame(parent, bg=SURFACE)
            value = tk.Label(f, font=font("text_semibold", 9), fg=MUTED, bg=SURFACE, width=4, anchor="e")
            self.volume = Slider(f, cfg["chime_volume"], on_release=lambda v: chime(v))
            self.volume.on_change = lambda v: value.config(text="Off" if v == 0 else str(v))
            self.volume.set(self.volume.value)
            self.volume.pack(side="left")
            value.pack(side="left", padx=(px(6), 0))
            return f
        setting_row(b, "Chime volume", "Plays when a break ends and with each nudge. Release to preview.",
                    volume_control, first=True)
        self.startup = setting_row(b, "Start with Windows", "Open Flow automatically when you sign in.",
                                   lambda p: Toggle(p, get_startup()))
        self.welcome = setting_row(b, "Welcome screen",
                                   "Ask if I'm ready when I start or wake my laptop, or come back after an hour away.",
                                   lambda p: Toggle(p, cfg["welcome_popup"]))
        card.finish().pack()

    # -- actions

    def send_test(self):
        result = send_phone(self.topic.get(), "Flow is connected",
                            "Nudges will show up here when it's time to get back to work.")
        self.test_status.config(text="Sending...", fg=MUTED)

        def check():
            if not self.win.winfo_exists():
                return
            if not result:
                return self.win.after(200, check)
            if result[0]:
                self.test_status.config(text="Sent. Check your phone.", fg=ACCENT)
            else:
                self.test_status.config(text="Couldn't send. Check your internet connection and the topic.",
                                        fg=WARN)
        check()

    def save(self):
        cfg = dict(self.app.cfg)
        work = self.work.value
        cfg.update(
            work_minutes=work,
            timer_mode=self.timer_mode.value or TIMER_MODES[0],
            break_minutes=self.brk.value,
            warn_minutes_before=min(self.warn.value, max(0, work - 1)),
            reset_break_on_input=self.reset.value,
            stand_reminders=self.stand_on.value,
            stand_every_minutes=self.stand_every.value,
            stand_for_minutes=self.stand_for.value,
            stand_renudge_minutes=self.stand_again.value,
            skip_phrase=self.phrase.get().strip() or DEFAULTS["skip_phrase"],
            breaks_enabled=self.breaks_on.value,
            skip_style=self.skip_style.value or "One click",
            chime_volume=self.volume.value,
            return_nudges=self.nudges.value,
            return_grace_minutes=self.grace.value,
            return_nudge_every_minutes=self.every.value,
            goal=self.goal.get().strip(),
            phone_notify=self.phone.value,
            ntfy_topic=self.topic.get().strip() or cfg["ntfy_topic"],
            welcome_popup=self.welcome.value,
            **self.pending,
        )
        cfg["accent"] = Theme(cfg).accent
        restyle = any(cfg.get(k) != self.app.cfg.get(k) for k in self.APPEARANCE_KEYS)
        self.app.apply_settings(cfg)
        try:
            set_startup(self.startup.value)
        except OSError:
            pass
        if restyle:
            self.rebuild()
        self.flash_saved()  # stay open; just confirm the save

    def rebuild(self):
        """Redraw the contents (new colours or values) in place: no close, no jump, no flicker.
        It comes back on the same page and the same Appearance element you were editing."""
        cover = cover_window(self.win)
        self._old_outer = self.outer
        self._build()
        style_window(self.win, border=BORDER)
        uncover(self.win, cover)

    def open_advanced(self):
        if self.advanced and self.advanced.win.winfo_exists():
            return self.advanced.win.lift()
        self.advanced = AdvancedWindow(self)


class AdvancedWindow:
    """Less common settings, with proper controls (no raw config file)."""

    def __init__(self, settings):
        self.settings, self.app = settings, settings.app
        cfg = self.app.cfg
        self.win = w = hidden_toplevel(settings.win)
        w.title("Flow · Advanced")
        w.configure(bg=BG)
        w.resizable(False, False)
        W = px(460)
        outer = tk.Frame(w, bg=BG)
        outer.pack(padx=px(24), pady=(px(20), px(22)))
        window_header(outer, "Advanced", "Fine-tune how Flow notices you and when it steps in.")

        card = Card(outer, W)
        b = card.body
        section_label(b, "Detecting you")
        self.away = setting_row(b, "Away after", "No keyboard or mouse input for this long counts as away "
                                "during a break.", lambda p: Stepper(p, cfg["away_threshold_seconds"], 1, 60, 1, "sec"),
                                first=True)
        self.idle = setting_row(b, "Natural break", "If you're away this long mid-session, "
                                "Flow asks whether it was a break.",
                                lambda p: Stepper(p, cfg["idle_reset_minutes"], 1, 60, 1, "min"))
        self.activity = setting_row(b, "Watch keyboard & mouse",
                                    "Turn off if you also study away from this computer (like on an iPad). "
                                    "The timer then always keeps running and breaks go by the clock.",
                                    lambda p: Toggle(p, cfg["use_activity"]))
        card.finish().pack(pady=(px(18), 0))

        card = Card(outer, W)
        b = card.body
        section_label(b, "Reminders")
        self.max_nudges = setting_row(b, "Nudges after a break", "How many times to remind you to come back.",
                                      lambda p: Stepper(p, cfg["return_max_nudges"], 1, 10, 1, "max"), first=True)
        self.welcome = setting_row(b, "Welcome back after", "Show the welcome screen when you return after "
                                   "being away this long.",
                                   lambda p: Stepper(p, cfg["welcome_after_idle_minutes"], 15, 240, 15, "min"))
        card.finish().pack(pady=(px(12), 0))

        card = Card(outer, W)
        b = card.body
        section_label(b, "Reset")
        tk.Label(b, text="Put every setting back to how Flow started. Your goal, phone topic and stats are kept.",
                 font=font("text", 9), fg=MUTED, bg=SURFACE, anchor="w", justify="left",
                 wraplength=W - px(40)).pack(fill="x")
        self.reset_row = tk.Frame(b, bg=SURFACE)
        self.reset_row.pack(fill="x", pady=(px(10), 0))
        self._reset_button()
        card.finish().pack(pady=(px(12), 0))

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(px(18), 0))
        PillButton(foot, "Save", self.save, kind="primary", bg=BG).pack(side="right")
        PillButton(foot, "Close", w.destroy, bg=BG).pack(side="right", padx=(0, px(8)))
        self.saved = tk.Label(foot, text="", font=font("text_semibold", 9), fg=ACCENT, bg=BG)
        self.saved.pack(side="right", padx=(0, px(12)))

        style_window(w, border=BORDER)
        x, y = center_on_primary(w)
        animate_in(w, x, y)
        w.focus_force()

    def _reset_button(self, confirm=False):
        for child in self.reset_row.winfo_children():
            child.destroy()
        if confirm:
            PillButton(self.reset_row, "Yes, reset everything", self.reset, kind="primary", height=32,
                       size=9).pack(side="left")
            PillButton(self.reset_row, "Cancel", self._reset_button, kind="ghost", height=32,
                       size=9).pack(side="left", padx=(px(8), 0))
        else:
            PillButton(self.reset_row, "Reset to defaults", lambda: self._reset_button(True), height=32,
                       size=9).pack(side="left")

    def _flash(self, text):
        self.saved.config(text=text)
        self.win.after(2500, lambda: self.saved.winfo_exists() and self.saved.config(text=""))

    def save(self):
        cfg = dict(self.app.cfg)
        cfg.update(away_threshold_seconds=self.away.value, idle_reset_minutes=self.idle.value,
                   use_activity=self.activity.value,
                   return_max_nudges=int(self.max_nudges.value), welcome_after_idle_minutes=self.welcome.value)
        self.app.apply_settings(cfg)
        self._flash("✓  Saved")

    def reset(self):
        cfg = dict(DEFAULTS)
        for keep in ("goal", "ntfy_topic", "phone_notify"):
            cfg[keep] = self.app.cfg[keep]
        self.app.apply_settings(cfg)
        self.win.destroy()
        if self.settings.win.winfo_exists():
            self.settings.rebuild()
            self.settings.flash_saved()



class BarChart(tk.Canvas):
    """Stand-ups per day, with a hover tooltip per bar."""

    def __init__(self, parent, days, rows, width, bg=SURFACE):
        self.days, self.rows, self.bg = days, rows, bg
        self.W, self.H = width, px(190)
        super().__init__(parent, width=self.W, height=self.H, bg=bg, highlightthickness=0, bd=0)
        self.top, self.base = px(34), self.H - px(30)
        self.slot = self.W / len(days)
        vals = [r.get("stand_ups", 0) for r in rows]
        self.vmax = max(4, max(vals))
        self.vmax += self.vmax % 2  # even, so the midline lands on a whole number

        ss = 4
        img = Image.new("RGB", (self.W * ss, self.H * ss), bg)
        d = ImageDraw.Draw(img)
        for frac in (0.5, 1.0):  # recessive gridlines
            y = (self.base - (self.base - self.top) * frac) * ss
            d.line((0, y, self.W * ss, y), fill=SURFACE_2, width=ss)
        d.line((0, self.base * ss, self.W * ss, self.base * ss), fill=BORDER, width=ss)
        bw, r = min(self.slot * 0.5, px(34)), px(4)
        for i, v in enumerate(vals):
            if not v:
                continue
            cx = (i + 0.5) * self.slot
            y = self.base - (self.base - self.top) * v / self.vmax
            box = ((cx - bw / 2) * ss, y * ss, (cx + bw / 2) * ss, self.base * ss)
            d.rounded_rectangle(box, radius=r * ss, fill=CHART)
            d.rectangle((box[0], box[3] - r * ss, box[2], box[3]), fill=CHART)  # square base
        self.img = ImageTk.PhotoImage(img.resize((self.W, self.H), Image.LANCZOS))
        self.create_image(0, 0, anchor="nw", image=self.img)

        grid_font = font("text", 8)
        self.create_text(0, self.top - px(8), text=str(self.vmax), fill=FAINT, font=grid_font, anchor="w")
        self.create_text(0, (self.top + self.base) / 2 - px(8), text=str(self.vmax // 2), fill=FAINT,
                         font=grid_font, anchor="w")
        today = len(days) - 1
        for i, day in enumerate(days):
            cx = (i + 0.5) * self.slot
            is_today = i == today
            self.create_text(cx, self.base + px(15), text="Today" if is_today else day.strftime("%a"),
                             fill=FG if is_today else MUTED,
                             font=font("text_semibold" if is_today else "text", 9))
        # Direct label on today's bar only.
        v = vals[today]
        y = self.base - (self.base - self.top) * v / self.vmax
        self.create_text((today + 0.5) * self.slot, y - px(10), text=str(v), fill=FG,
                         font=font("text_semibold", 9))

        self.tip_items, self.hover_i = [], None
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self._show_tip(None))

    def _hover(self, e):
        i = int(e.x // self.slot)
        self._show_tip(i if 0 <= i < len(self.days) and self.top - px(20) <= e.y <= self.H else None)

    def _show_tip(self, i):
        if i == self.hover_i:
            return
        self.hover_i = i
        for item in self.tip_items:
            self.delete(item)
        self.tip_items = []
        if i is None:
            return
        r = self.rows[i]
        text = (f"{self.days[i]:%A %d %b}\n"
                f"{r.get('breaks_taken', 0)} taken  ·  {r.get('breaks_skipped', 0)} skipped\n"
                f"Stood up {r.get('stand_ups', 0)}x\n"
                f"Longest sit {r.get('longest_sitting_minutes', 0)} min\n"
                f"Back late {r.get('late_returns', 0)}x  ·  {r.get('minutes_over', 0)} min over")
        t = self.create_text(0, 0, text=text, fill=FG, font=font("text", 9), anchor="nw")
        x0, y0, x1, y1 = self.bbox(t)
        tw, th = x1 - x0 + px(20), y1 - y0 + px(14)
        cx = (i + 0.5) * self.slot
        x = int(min(max(cx - tw / 2, 0), self.W - tw))
        y = px(2)
        self.tip_img = ImageTk.PhotoImage(rounded(tw, th, px(8), SURFACE_2, self.bg, outline=BORDER))
        bg_item = self.create_image(x, y, anchor="nw", image=self.tip_img)
        self.coords(t, x + px(10), y + px(7))
        self.tag_raise(t)
        self.tip_items = [bg_item, t]


class StatsWindow:
    def __init__(self, app):
        self.app = app
        self.win = w = hidden_toplevel(app.root)
        w.title("Flow · Stats")
        w.configure(bg=BG)
        w.resizable(False, False)
        self._build()
        style_window(w, border=BORDER)
        x, y = center_on_primary(w)
        animate_in(w, x, y)
        w.focus_force()

    def rebuild(self):
        """Redraw in place, e.g. after a theme change, without flicker."""
        cover = cover_window(self.win)
        old = self.outer
        self._build(pack=False)
        old.destroy()
        self.outer.pack(padx=px(24), pady=(px(20), px(22)))
        uncover(self.win, cover)

    def _build(self, pack=True):
        app, w = self.app, self.win
        stats = app.stats
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=n) for n in range(6, -1, -1)]
        rows = [stats.get(d.isoformat(), {}) for d in days]
        t = rows[-1]
        w.iconphoto(False, *app.window_icon)
        W = px(480)

        self.outer = outer = tk.Frame(w, bg=BG)
        if pack:
            outer.pack(padx=px(24), pady=(px(20), px(22)))
        window_header(outer, "Your week", f"{days[0]:%d %b} – {today:%d %b %Y}  ·  tiles show today")

        tiles = tk.Frame(outer, bg=BG)
        tiles.pack(fill="x", pady=(px(18), 0))
        gap = px(12)
        tile_w = (W - 3 * gap) // 4
        for i, (value, label) in enumerate((
                (t.get("stand_ups", 0), "Stand-ups"),
                (t.get("breaks_taken", 0), "Breaks"),
                (t.get("breaks_skipped", 0), "Skipped"),
                (f"{t.get('longest_sitting_minutes', 0)}m", "Longest sit"))):
            card = Card(tiles, tile_w, pad=16)
            tk.Label(card.body, text=str(value), font=font("display", 22), fg=FG, bg=SURFACE,
                     anchor="w").pack(fill="x")
            tk.Label(card.body, text=label, font=font("text", 9), fg=MUTED, bg=SURFACE,
                     anchor="w").pack(fill="x")
            card.finish().pack(side="left", padx=(0 if i == 0 else gap, 0))

        card = Card(outer, W)
        tk.Label(card.body, text="Stand-ups per day", font=font("text_semibold", 10), fg=FG,
                 bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(card.body, text="Last 7 days. Hover a bar for details.", font=font("text", 9), fg=MUTED,
                 bg=SURFACE, anchor="w").pack(fill="x", pady=(0, px(10)))
        BarChart(card.body, days, rows, W - 2 * px(18)).pack()
        card.finish().pack(pady=(px(12), 0))

        taken = sum(r.get("breaks_taken", 0) for r in rows)
        skipped = sum(r.get("breaks_skipped", 0) for r in rows)
        on_time = sum(r.get("on_time_returns", 0) for r in rows)
        late = sum(r.get("late_returns", 0) for r in rows)
        stood = sum(r.get("stand_ups", 0) for r in rows)
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(px(16), 0))
        tk.Label(foot, text=f"This week: stood up {stood}x · {taken} breaks · {skipped} skipped · "
                            f"back on time {on_time} of {on_time + late}",
                 font=font("text", 9), fg=MUTED, bg=BG).pack(side="left")
        PillButton(foot, "Close", w.destroy, bg=BG).pack(side="right")


# ---------- the app ----------

class App:
    def __init__(self, demo=False):
        global UI_SCALE
        self.demo = demo
        self.root = tk.Tk()
        self.root.withdraw()
        UI_SCALE = self.root.winfo_fpixels("1i") / 96
        F.update(pick_fonts(self.root))
        self.cfg = load_config()
        self.cfg_mtime = os.path.getmtime(CONFIG_PATH)
        self.stats = load_stats()
        apply_theme(self.cfg)
        self.refresh_window_icons()

        self.state = "working"          # working | break | returning | waiting | paused
        self.welcome_win = None
        self.welcome_again_at = 0.0     # "Give me 5 min" -> ask again at this time
        self.last_wall = time.time()    # wall clock jumps when the laptop sleeps
        self.long_away = False
        self.work_elapsed = 0.0         # seconds into the current focus session
        self.sit_elapsed = 0.0          # seconds sitting since you last stood up (stand-ups and breaks)
        self.stand = None               # the stand-up card, while it's open
        self.stand_snooze_until = 0.0
        self.stand_next_nudge = 0.0
        self.stand_nudges = 0
        self.break_ended_at = 0.0       # when "returning" started
        self.next_nudge = 0.0
        self.nudges_sent = 0
        self.break_remaining = 0.0
        self.warned = False
        self.snooze_until = 0.0
        self.overlay = None
        self.toast = None
        self.settings_win = None
        self.stats_win = None
        self.panel = None
        self.panel_toggled = 0.0
        self.skip_next = False          # "Skip next break" from the tray menu
        self.quiet_since = 0.0          # when a quiet spell (no input) began while working
        self.menu = None
        self.pause_until = 0.0          # 0 = paused until resumed by hand
        self.paused_from = "working"
        self.last_tick = time.monotonic()
        self.last_status = None
        self.quitting = False

        self.icon_key = self.tray_frame()
        self.cmds = queue.Queue()        # tray thread -> tk thread
        self.icon = TrayIcon("flow", self.render_tray(self.icon_key), "Flow",
                             on_click=lambda right, x, y: self.cmds.put(("menu", x, y) if right else ("panel",)))
        self.icon.run_detached()
        try:
            migrate_startup()
        except OSError:
            pass
        # Windows registers the tray icon a moment after it appears; pin it then.
        for delay in (4000, 30000):
            self.root.after(delay, lambda: self._quietly(pin_tray_icon))
        if self.cfg["welcome_popup"]:
            self.root.after(800, self.show_welcome)
        self.root.after(1000, self.tick)
        self.root.after(200, self.animate_fx)

    def refresh_window_icons(self):
        # Several sizes so the taskbar and title bar both get a sharp icon; follows the theme.
        self.window_icon = [ImageTk.PhotoImage(app_logo(s)) for s in (256, 48, 32, 16)]
        self.root.iconphoto(True, *self.window_icon)

    def animate_fx(self):
        """Colour effects: the session panel's border, the break-screen frame and the tray icon."""
        if self.quitting:
            return
        while not self.cmds.empty():  # tray clicks feel instant
            self.handle(*self.cmds.get())
            if self.quitting:
                return
        try:
            if self.stand_open():
                self.stand.edge.render()
        except tk.TclError:
            pass
        try:
            if self.panel_open():
                self.panel.edge.render()
                if self.panel.mode == self.panel._mode():
                    self.panel._update()  # live countdown
        except tk.TclError:
            pass  # panel closing mid-frame
        if self.overlay:
            self.overlay.fx()
        self.update_tray()
        self.root.after(80, self.animate_fx)

    def update_tray(self):
        """Redraw the tray icon when it visibly changes, at most ~7 times a second so it stays smooth."""
        now = time.monotonic()
        if now - getattr(self, "_tray_drawn", 0) < 0.14:
            return
        frame = self.tray_frame()
        if frame != self.icon_key:
            self.icon_key = frame
            self._tray_drawn = now
            self.icon.icon = self.render_tray(frame)

    @staticmethod
    def _quietly(fn):
        try:
            fn()
        except OSError:
            pass

    # -- settings

    def minutes(self, key):
        if self.demo:
            return {"work_minutes": 1, "break_minutes": 1 / 3, "warn_minutes_before": 0.5,
                    "stand_every_minutes": 0.5, "stand_for_minutes": 1 / 6, "stand_renudge_minutes": 0.25,
                    "idle_reset_minutes": 5, "return_grace_minutes": 0.25,
                    "return_nudge_every_minutes": 0.25}.get(key, float(self.cfg[key]))
        return float(self.cfg[key])

    def reload_config_if_changed(self):
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
            if mtime != self.cfg_mtime:
                self.cfg_mtime = mtime
                self.cfg = load_config()
                apply_theme(self.cfg)
                self.refresh_window_icons()
                self.icon_key = None
        except (OSError, ValueError):
            pass  # half-saved or invalid file; keep the previous settings

    def apply_settings(self, cfg):
        self.cfg = cfg
        save_config(cfg)
        self.cfg_mtime = os.path.getmtime(CONFIG_PATH)
        self.last_status = None  # refresh tray text
        apply_theme(cfg)
        self.refresh_window_icons()
        self.icon_key = None     # redraw the tray icon in the new colours
        # Keep everything in sync: redraw any open window in the new colours, in place.
        if self.panel_open():
            self.panel.restyle = True
            self.panel.refresh()
        if self.stats_win and self.stats_win.win.winfo_exists():
            self.stats_win.rebuild()
        sw = self.settings_win
        if sw and sw.win.winfo_exists():
            for child in (sw.picker, sw.advanced):
                if child and child.win.winfo_exists():
                    child.win.iconphoto(False, *self.window_icon)

    # -- stats

    def bump(self, key, value=1, mode="add"):
        day = self.stats.setdefault(datetime.date.today().isoformat(), {})
        if mode == "max":
            day[key] = max(day.get(key, 0), value)
        else:
            day[key] = day.get(key, 0) + value
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)

    # -- tray menu (runs on pystray's thread, so it only queues commands)

    # (The tray's right-click menu is Flow's own TrayMenu window; see handle("menu").)


    def time_to_stand(self):
        """Seconds until the next stand-up reminder (0 when it's due or showing)."""
        left = self.minutes("stand_every_minutes") * 60 - self.sit_elapsed
        if self.state == "working" and not self.stand_open():
            left -= max(0.0, time.monotonic() - self.last_tick)
        return max(0.0, left, self.stand_snooze_until - time.monotonic())

    def stand_only(self):
        """Stand-ups-only mode: no focus sessions or breaks; the timer counts down to the next stand-up."""
        return self.cfg.get("timer_mode") == "Stand-ups only"

    def break_coming(self):
        """Will this session end in a screen break? (Not if breaks are off or the next one is skipped.)"""
        return self.cfg["breaks_enabled"] and not self.skip_next and not self.stand_only()

    def time_to_break(self):
        """Seconds until the break, counted live (not just at the last tick)."""
        now = time.monotonic()
        left = self.minutes("work_minutes") * 60 - self.work_elapsed
        if self.state == "working":
            left -= max(0.0, now - self.last_tick)
        if now < self.snooze_until:  # meeting mode / "5 more min" holds the break back
            left = max(left, self.snooze_until - now)
        return max(0.0, left)

    def status_text(self):
        if self.state == "paused":
            if self.pause_until:
                return f"Paused: resumes in {max(1, math.ceil((self.pause_until - time.monotonic()) / 60))} min"
            return "Paused"
        if self.state == "break":
            return "On a break"
        if self.state == "returning":
            return "Break's over. Waiting for you"
        if self.state == "waiting":
            return "Ready when you are"
        now = time.monotonic()
        if now < self.snooze_until:
            return f"Meeting mode: {math.ceil((self.snooze_until - now) / 60)} min left"
        if self.stand_only():
            if self.stand_open():
                return "Time to stand up"
            return f"Stand up in {max(1, math.ceil(self.time_to_stand() / 60))} min"
        left = self.time_to_break()
        if not self.break_coming():
            return f"Session ends in {max(1, math.ceil(left / 60))} min"
        return f"Next break in {max(1, math.ceil(left / 60))} min" if left > 0 else "Break due"

    def _open_single(self, attr, cls):
        win = getattr(self, attr)
        if win and win.win.winfo_exists():
            win.win.deiconify()
            win.win.lift()
            win.win.focus_force()
        else:
            setattr(self, attr, cls(self))

    def handle(self, name, *args):
        if name == "skip_next":
            self.skip_next = not self.skip_next
            self.close_toast()
            self.toast = Toast(self.root, "Next break skipped" if self.skip_next else "Next break is back on",
                               "When this session ends, the next one starts straight away." if self.skip_next
                               else "Your break will start when this session ends.", seconds=6)
        elif name == "break_now" and self.state != "break":
            self.start_break()
        elif name == "snooze":
            self.snooze(args[0])
        elif name == "pause" and self.state not in ("paused", "break"):
            self.pause(args[0])
        elif name == "resume" and self.state == "paused":
            self.resume()
        elif name == "menu":
            if self.menu and self.menu.win.winfo_exists():
                self.menu.close()
            else:
                self.menu = TrayMenu(self, *args)
        elif name == "panel":
            now = time.monotonic()
            if now - self.panel_toggled < 0.8:  # second click of a double-click: keep what we just did
                return
            self.panel_toggled = now
            if self.panel and self.panel.win.winfo_exists():
                self.panel.close()
            else:
                self.panel = SessionPanel(self)
        elif name == "reset":
            self.reset_work()
            if self.stand_only():
                self.sit_elapsed = 0.0  # in stand-ups-only mode, the stand-up countdown is the timer
                self.close_stand()
        elif name == "stand_now" and self.state == "working":
            self.close_stand()
            self.stand = StandPrompt(self, moving=True)
        elif name == "preset":
            cfg = dict(self.cfg)
            cfg["work_minutes"], cfg["break_minutes"] = args
            self.apply_settings(cfg)
        elif name == "set":
            key, value = args
            cfg = dict(self.cfg)
            cfg[key] = value
            if key == "work_minutes":
                cfg["warn_minutes_before"] = min(float(cfg["warn_minutes_before"]), max(0, value - 1))
            self.apply_settings(cfg)
        elif name == "settings":
            self._open_single("settings_win", SettingsWindow)
        elif name == "stats":
            self._open_single("stats_win", StatsWindow)
        elif name == "quit":
            self.quitting = True
            self.icon.stop()
            self.root.destroy()

    # -- timer logic

    def check_break_due(self, now):
        """Heads-up before the break, then the break itself (unless meeting mode holds it back)."""
        work = self.minutes("work_minutes") * 60
        warn_at = work - self.minutes("warn_minutes_before") * 60
        if now < self.snooze_until or self.stand_only():
            return
        if self.work_elapsed >= work and not self.cfg["breaks_enabled"]:
            self.bump("sessions_done")
            self.reset_work()
            self.toast = Toast(self.root, "Session done", "Screen breaks are off, so the next session has "
                               "started. Stand-up reminders still keep you moving.", seconds=8)
        elif self.work_elapsed >= work and self.skip_next:
            self.skip_next = False
            self.bump("breaks_skipped")
            self.reset_work()
            self.toast = Toast(self.root, "Break skipped", "As you asked. The next session has started.",
                               seconds=8)
        elif self.work_elapsed >= work:
            self.start_break()
        elif not self.cfg["breaks_enabled"] or self.skip_next:
            pass  # no heads-up for a break that isn't coming
        elif self.work_elapsed >= warn_at and not self.warned:
            self.warned = True
            mins = math.ceil((work - self.work_elapsed) / 60)
            self.toast = Toast(
                self.root, f"Break in {mins} min",
                "Start wrapping up. Time to stand up soon.",
                buttons=[("Break now", self.start_break),
                         ("5 more min", lambda: self.snooze(mins + 5))],
                tone=WARN)

    def ask_was_break(self, away_seconds):
        """Back after a quiet spell: let them say whether it was a break (a fresh session) or not."""
        mins = max(1, round(away_seconds / 60))

        def was_break():
            self.reset_work()
            self.reset_sitting()
            self.toast = Toast(self.root, "Fresh session started",
                               f"Next break in {fmt_num(self.minutes('work_minutes'))} min.", seconds=5)

        self.close_toast()
        self.toast = Toast(self.root, f"Away {mins} min. Was that a break?",
                           "If you stepped away, start a fresh session. If you were reading or watching, "
                           "keep going.", buttons=[("It was a break", was_break), ("I was here", lambda: None)],
                           seconds=30)

    def reset_work(self):
        self.work_elapsed = 0.0
        self.quiet_since = 0.0
        self.warned = False
        self.close_toast()

    def snooze(self, minutes):
        self.snooze_until = time.monotonic() + minutes * 60
        self.bump("snoozes")

    def pause(self, minutes=None):
        """Freeze the session timer. minutes=None pauses until resumed by hand."""
        self.close_toast()
        self.paused_from = self.state
        self.state = "paused"
        self.pause_until = time.monotonic() + minutes * 60 if minutes else 0.0
        body = (f"Back in {fmt_num(minutes)} min. Your session picks up where it left off." if minutes
                else "Your session is on hold. Resume from the tray icon whenever you're ready.")
        if not self.panel_open():  # the panel already shows it
            self.toast = Toast(self.root, "Session paused", body, seconds=6, tone=IDLE)

    def resume(self):
        self.pause_until = 0.0
        self.state = "working"
        if self.paused_from != "working":  # paused before a session had started
            self.reset_work()
        self.paused_from = "working"
        left = max(1, math.ceil((self.minutes("work_minutes") * 60 - self.work_elapsed) / 60))
        self.close_toast()
        if not self.panel_open():
            msg = (f"Next stand-up in {max(1, math.ceil(self.time_to_stand() / 60))} min." if self.stand_only()
                   else f"Session resumed. {left} min until your break.")
            self.toast = Toast(self.root, "Back to it", msg, seconds=6)

    # -- stand-ups

    def stand_open(self):
        return bool(self.stand and self.stand.win.winfo_exists())

    def close_stand(self):
        if self.stand:
            self.stand.destroy()
            self.stand = None

    def check_stand_due(self, now):
        """Ask you to stand up once you've sat long enough, unless something better is coming."""
        if not (self.cfg["stand_reminders"] or self.stand_only()) or self.stand_open() or self.state != "working":
            return
        if now < self.stand_snooze_until or now < self.snooze_until:  # snoozed, or meeting mode
            return
        if self.sit_elapsed < self.minutes("stand_every_minutes") * 60:
            return
        if self.break_coming() and self.time_to_break() < 5 * 60:  # a screen break will get you up anyway
            return
        self.close_toast()
        self.stand_nudges = 0
        self.stand = StandPrompt(self)
        self.stand_next_nudge = now + self.minutes("stand_renudge_minutes") * 60
        chime(float(self.cfg["chime_volume"]))
        self.phone("Time to stand up", f"You've been sitting for {round(self.sit_elapsed / 60)} min. "
                                       "Get up and move for a couple of minutes.")

    def stand_up(self):
        """'I'm up': start the short on-your-feet countdown."""
        if self.stand_open():
            self.stand.show_moving()

    def stand_done(self):
        """Moved for long enough (or pressed 'I'm done'): sitting starts again from zero."""
        self.bump("stand_ups")
        self.bump("longest_sitting_minutes", round(self.sit_elapsed / 60), mode="max")
        self.sit_elapsed = 0.0
        self.stand_snooze_until = 0.0
        if self.stand_open():
            self.stand.show_done(self.minutes("stand_every_minutes"))
        chime(float(self.cfg["chime_volume"]))

    def reset_sitting(self):
        """You got up some other way (a break, the laptop slept): the sitting clock starts again."""
        self.bump("longest_sitting_minutes", round(self.sit_elapsed / 60), mode="max")
        self.sit_elapsed = 0.0
        self.close_stand()

    def stand_snooze(self, minutes):
        self.close_stand()
        self.stand_snooze_until = time.monotonic() + minutes * 60
        self.bump("stand_snoozes")

    def panel_open(self):
        return bool(self.panel and self.panel.win.winfo_exists())

    def close_toast(self):
        if self.toast:
            self.toast.destroy()
            self.toast = None

    def start_break(self):
        self.close_toast()
        self.quiet_since = 0.0
        self.bump("longest_sitting_minutes", round(self.sit_elapsed / 60), mode="max")
        self.sit_elapsed = 0.0
        self.close_stand()
        self.state = "break"
        self.break_total = self.break_remaining = self.minutes("break_minutes") * 60
        self.overlay = BreakOverlay(self.root, self.cfg["skip_phrase"], clock_only=not self.cfg["use_activity"],
                                    skip_style=self.cfg["skip_style"],
                                    on_skip=lambda: self.end_break(skipped=True))
        self.overlay.update(self.break_remaining, self.break_total, away=False)

    def end_break(self, skipped):
        if self.overlay:
            self.overlay.destroy()
        self.overlay = None
        self.state = "working"
        self.reset_work()
        self.snooze_until = 0.0
        self.bump("breaks_skipped" if skipped else "breaks_taken")
        if skipped:
            return
        chime(float(self.cfg["chime_volume"]))
        if not self.cfg["use_activity"]:
            self.toast = Toast(self.root, "Break's over", "Your next session has started. Back to it!"
                               + (f"\nFocus: {self.cfg['goal']}" if self.cfg["goal"] else ""), seconds=10)
            self.phone("Break's over", "Your next session has started. Back to it!")
            return
        if not self.cfg["return_nudges"]:
            self.toast = Toast(self.root, "Break done. Welcome back!",
                               "Your next work session has started.", seconds=6)
            return
        # Wait for them to come back; the work timer starts when they do.
        now = time.monotonic()
        self.state = "returning"
        self.break_ended_at = now
        self.nudges_sent = 0
        self.next_nudge = now + self.minutes("return_grace_minutes") * 60
        self.toast = Toast(self.root, "Break's over", "Head back when you're ready. "
                           "Your next session starts as soon as you're back.", seconds=120)
        self.phone("Break's over", "Time to head back to your desk.")

    def show_welcome(self):
        if self.state in ("break", "returning"):
            return
        if self.welcome_win and self.welcome_win.win.winfo_exists():
            self.welcome_win.win.lift()
            return
        self.close_toast()
        self.state = "waiting"
        self.quiet_since = 0.0
        self.welcome_again_at = 0.0
        self.welcome_win = WelcomeWindow(self)

    def welcome_done(self, choice, goal):
        self.welcome_win = None
        if goal != self.cfg["goal"]:
            cfg = dict(self.cfg)
            cfg["goal"] = goal
            self.apply_settings(cfg)
        if choice == "go":
            self.state = "working"
            self.snooze_until = 0.0
            self.reset_work()
            self.sit_elapsed = 0.0
            mins = fmt_num(self.minutes("work_minutes"))
            first = (f"Next stand-up in {fmt_num(self.minutes('stand_every_minutes'))} min." if self.stand_only()
                     else f"Next break in {mins} min.")
            self.toast = Toast(self.root, "Session started",
                               first + (f"\nFocus: {goal}" if goal else ""), seconds=6)
        elif choice == "later":
            self.state = "waiting"
            self.welcome_again_at = time.monotonic() + 5 * 60
        else:
            self.state = "paused"
            self.pause_until, self.paused_from = 0.0, "waiting"
            self.toast = Toast(self.root, "Enjoy your time off",
                               "Flow is paused. Click Resume in the tray menu whenever you're ready.",
                               seconds=8, tone=IDLE)

    def phone(self, title, message, priority=3):
        if self.cfg["phone_notify"]:
            send_phone(self.cfg["ntfy_topic"], title, message, priority)

    def nudge(self):
        over = max(1, round((time.monotonic() - self.break_ended_at) / 60))
        title, body = NUDGES[min(self.nudges_sent, len(NUDGES) - 1)]
        body = body.format(over=over)
        if self.cfg["goal"]:
            body += f" Remember why: {self.cfg['goal']}."
        self.nudges_sent += 1
        self.close_toast()
        self.toast = Toast(self.root, title, body, seconds=120, tone=WARN)
        chime(float(self.cfg["chime_volume"]))
        self.phone(title, body, priority=4 if self.nudges_sent >= 3 else 3)

    def welcome_back(self, now):
        over = (now - self.break_ended_at) / 60 - self.minutes("return_grace_minutes")
        self.state = "working"
        self.reset_work()
        if over > 0:
            self.bump("late_returns")
            self.bump("minutes_over", round(over))
            body = "Glad you're back. Let's make this session count."
        else:
            self.bump("on_time_returns")
            body = "Right on time. Nice discipline. Let's go."
        goal = self.cfg["goal"]
        self.toast = Toast(self.root, "Welcome back!", body + (f"\nWorking towards: {goal}" if goal else ""),
                           seconds=8)

    def tick(self):
        try:
            self._tick()
        finally:
            if not self.quitting:
                self.root.after(1000, self.tick)

    def _tick(self):
        now = time.monotonic()
        gap, self.last_tick = now - self.last_tick, now

        while not self.cmds.empty():
            self.handle(*self.cmds.get())
            if self.quitting:
                return
        self.reload_config_if_changed()

        idle = idle_seconds()
        idle_reset = self.minutes("idle_reset_minutes") * 60

        # Welcome card: laptop woke from sleep, or back after a long time away.
        wall = time.time()
        woke, self.last_wall = wall - self.last_wall > 5 * 60, wall
        came_back = False
        if idle >= self.minutes("welcome_after_idle_minutes") * 60:
            self.long_away = True
        elif self.long_away and idle < 5:
            self.long_away, came_back = False, True
        if (self.cfg["welcome_popup"] and self.cfg["use_activity"] and (woke or came_back)
                and self.state in ("working", "paused", "waiting")):
            self.show_welcome()
        if self.state == "waiting" and self.welcome_again_at and now >= self.welcome_again_at:
            self.show_welcome()

        long_sleep = gap >= max(2 * self.minutes("work_minutes") * 60, 3600)
        if self.state == "working" and not self.cfg["use_activity"] and long_sleep:
            # Laptop was off far longer than a session (e.g. overnight): start fresh rather than
            # opening to a break screen. Shorter sleeps keep running, for studying on another device.
            self.reset_work()
            self.reset_sitting()
            gap = 0.0
        if self.state == "working" and not self.cfg["use_activity"]:
            self.work_elapsed += gap  # always running: no quiet-spell or sleep resets
            self.check_break_due(now)
        elif self.state == "working":
            if gap >= idle_reset:
                # The laptop was asleep: that was a break for sure.
                self.reset_work()
                self.reset_sitting()
                gap = 0.0
            else:
                # No input for a while might be a break, or reading / watching a lecture.
                # Keep counting, and ask when they're back rather than guessing.
                if idle >= idle_reset and not self.quiet_since:
                    self.quiet_since = now - idle
                elif idle < 5 and self.quiet_since:
                    self.ask_was_break((now - idle) - self.quiet_since)  # last input minus when it went quiet
                    self.quiet_since = 0.0
                self.work_elapsed += gap
                self.check_break_due(now)

        elif self.state == "paused":
            if self.pause_until and now >= self.pause_until:
                self.resume()
                chime(float(self.cfg["chime_volume"]))

        elif self.state == "returning":
            if idle < now - self.break_ended_at:  # input since the break ended: they're back
                self.welcome_back(now)
            elif now >= self.next_nudge and self.nudges_sent < int(self.cfg["return_max_nudges"]):
                self.nudge()
                self.next_nudge = now + self.minutes("return_nudge_every_minutes") * 60

        elif self.state == "break":
            away = idle >= float(self.cfg["away_threshold_seconds"]) or not self.cfg["use_activity"]
            if away:
                self.break_remaining -= gap
            elif self.cfg["reset_break_on_input"]:
                self.break_remaining = self.break_total
            if self.break_remaining <= 0:
                self.end_break(skipped=False)
            elif self.overlay:
                self.overlay.update(self.break_remaining, self.break_total, away)

        # Sitting clock: runs while you're working, stops while you're on your feet.
        if self.state == "working" and not (self.stand_open() and self.stand.phase in ("moving", "done")):
            # No input doesn't mean you stood up (reading, lectures, the iPad). After a long quiet
            # spell Flow asks "Was that a break?", and "It was a break" resets this clock.
            self.sit_elapsed += gap
            self.check_stand_due(now)
        elif self.state != "working":
            self.close_stand()
        if self.stand_open() and self.stand.phase == "ask" and now >= self.stand_next_nudge:
            self.stand_nudges += 1
            self.stand.renudge()
            self.stand_next_nudge = now + self.minutes("stand_renudge_minutes") * 60
            if self.stand_nudges <= 5:
                chime(float(self.cfg["chime_volume"]))
                self.phone("Still sitting?", f"{round(self.sit_elapsed / 60)} min in the chair. Time to stand up.",
                           priority=4 if self.stand_nudges >= 2 else 3)

        if self.panel and self.panel.win.winfo_exists():
            self.panel.refresh()

        self.update_tray()
        status = self.status_text()
        if status != self.last_status:
            self.last_status = status
            self.icon.title = f"Flow · {status}"


    TRAY_STEPS = 120  # ring positions; one step = 1/120 of the session

    def tray_frame(self):
        """What the tray ring should show right now: (colours, fraction left, centre, effect step)."""
        steps = self.TRAY_STEPS
        # With the tray effect on, the colours move in small steps (redraws are capped in update_tray).
        fx = int(TRAY_FX.phase() * 360) if TRAY_FX.animated else 0
        if self.state == "break":
            left = self.break_remaining / self.break_total if getattr(self, "break_total", 0) else 1
            return "theme", round(left * steps), None, fx
        if self.state == "returning":
            blink = int(time.monotonic()) % 2  # the ring gently blinks until you're back
            return (WARN if blink else mix(WARN, BG, 0.55)), steps, None, blink
        if self.state == "waiting":
            return IDLE, steps, None, 0
        if self.stand_only():
            every = self.minutes("stand_every_minutes") * 60
            left = round(min(1.0, self.time_to_stand() / every) * steps) if every else steps
            if self.state == "paused":
                return IDLE, left, "pause", 0
            if time.monotonic() < self.snooze_until:
                return IDLE, left, None, 0
            if self.stand_open():
                blink = int(time.monotonic()) % 2  # gently blinks while the stand-up card is waiting
                return (WARN if blink else mix(WARN, BG, 0.55)), steps, None, blink
            return "theme", left, None, fx
        work = self.minutes("work_minutes") * 60
        left = round(max(0.0, 1 - self.work_elapsed / work) * steps) if work else steps
        if self.state == "paused":
            return IDLE, left, "pause", 0
        if time.monotonic() < self.snooze_until:
            return IDLE, left, None, 0
        if self.warned:
            return WARN, left, None, 0
        return "theme", left, None, fx

    def render_tray(self, frame):
        colors, left, center, _ = frame
        if colors == "theme":
            colors = TRAY_FX.ring_colors()
        return orient(ring_mark(64, left / self.TRAY_STEPS, colors, center=center, stroke=40, base=128))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    migrate_data()
    # Its own taskbar identity, so Windows shows the Flow icon instead of grouping it under Python.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Flow.FocusBreaks")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    App(demo="--demo" in sys.argv).run()
