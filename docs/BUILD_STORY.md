# Build process

I noticed I was sitting at my desk for way too long when studying or working. So I wanted an app that reminds me
to get up. I'd set the timers and breaks, and if I was still sitting when a break started, it wouldn't let me
keep working.

That app is Flow. This is how it got built, from the first version to now. Part 1 is the story and why things
work the way they do. Part 2 is the technical side.

---

## Part 1: The story

### The first version

The first version was called *Stand Up*, and it was pretty bare: an icon in the system tray, a work timer, and a
full-screen break screen that covered everything when it was time to stop.

The one idea from that first version that survived everything after it: **the break only counts down while
you're away from the keyboard and mouse.** If a break just ended after five minutes, I could sit there and wait
it out. This way, the only way through a break is to actually get up. Touch the mouse and the countdown pauses.

The very first test run taught me something too: the chime was painfully loud. Windows' built-in beep ignores
your volume setting, so I replaced it with a soft three-note sound that follows the system volume.

### Making it something I'd actually want to look at

An app you see every day needs to feel good, so the next few days were mostly design. The plain countdown became
a ring. The popups got rounded corners. The settings moved out of a text file and into a proper window, then into
a window with a sidebar. Stats got a little bar chart.

Along the way it changed names twice. *Stand Up* became *Rise*, and then **Flow**, which stuck because that's
what the app is really about: working in a rhythm of focus and rest.

It also picked up a few things I didn't plan at the start:

- A **welcome card** when I open my laptop, asking what my one focus is for the session.
- **Nudges to come back** after a break, because I'd sometimes take a break and just... not come back. The
  nudges can go to my phone too.
- **Themes and animations.** Six colour themes, a colour wheel, and gentle effects like a gradient that slowly
  flows around the edge of a window.

That last one went through a few rounds. At first one "colour effect" setting controlled everything at once, and
it felt cluttered. So I split it up: the tray icon, the panel border and the break screen each get their own
effect and speed.

### The polish round

A lot of the work was small things that looked wrong once I used the app for real. Windows flickered when I saved
my settings. The settings window jumped to a different spot. A popup spilled over onto my second monitor. The
border had little gaps at the corners. The logo looked like a half-loaded loading spinner. Double-clicking the
tray icon opened the panel and closed it again straight away.

None of these are big on their own, but together they're the difference between something that feels finished
and something that doesn't. Each one got tracked down and fixed.

The break screen also got a complete redesign. The first version had a big ring and boxes of text, and it felt
like an alarm going off. Now it's a soft title, a large light countdown and one slim bar. A break should feel like
a break.

### "I don't think the timer is accurate"

At one point I was convinced the timer was wrong. It turned out the timer itself was fine (it stayed within half
a second of a real clock), but digging into it found three real problems:

- The countdown only refreshed once a second, slightly out of step with the actual seconds, so now and then it
  skipped a number. It looked wrong, even though it wasn't.
- If I didn't touch the keyboard or mouse for five minutes, Flow assumed I'd taken a break and quietly restarted
  my session.
- That second one was the real issue, because **I study on my iPad.** The PC looked idle while I was sitting
  right there working.

That changed how I thought about the whole app. Flow had been guessing what I was doing, and guessing wrong. So
now it asks instead. If I come back after a long quiet spell, it says *"Away 7 min. Was that a break?"* And
because I work on another device a lot, there's a "keep running" mode where Flow ignores the keyboard and mouse
completely: the timer just runs, and breaks go by the clock.

### Back to the original idea: getting out of the chair

After all of that, I realised the main thing I wanted had slipped. Screen breaks are good, but my actual problem
was sitting too long. I don't always need to stop working. I just need to stand up.

So Flow now has a separate **sitting clock**. Every 30 minutes a card asks me to stand up and move for a minute
or two. I can keep reading while I do it, and my focus session keeps running. If I ignore it, it comes back a bit
firmer. This is the feature I'd call the heart of the app now.

### Making it a real project

Finally it all went into a Git repository, with automated tests, a one-command build that produces a single
`Flow.exe`, and a CPU fix that made the animated tray icon about three times cheaper to run.

### What I took away from it

- **Use it, then fix it.** Almost every improvement came from using the app day to day, not from planning ahead.
- **Measure before you fix.** "The timer's wrong" and "it's using too much CPU" both turned out to have causes I
  wouldn't have guessed. Measuring first found them.
- **Don't guess what the user is doing.** The most annoying bugs all came from the app assuming something that
  wasn't true for me, like "no typing means you've left".

---

## Part 2: The technical side

### What it's built with

Flow is a single Python file, about 3,700 lines. The interface is Tkinter, with the custom pieces (rounded
buttons, toggles, the rings and bars) drawn with Pillow at a higher resolution and scaled down so the edges stay
smooth. The tray icon uses `pystray`. Anything Windows-specific goes through `ctypes`: reading how long the
keyboard and mouse have been idle (`GetLastInputInfo`), dark title bars and rounded corners (DWM attributes), and
the registry for starting with Windows. PyInstaller packs it all into one 19 MB `Flow.exe` that runs without
Python installed. Settings and stats are plain JSON files in `%APPDATA%\Flow`.

### How it's put together

At the centre is a small state machine. You're either *working*, on a *break*, *returning* from one, *paused*, or
*waiting* to start. A tick runs once a second and moves things along. It adds up the real time that has passed
(using `time.monotonic()`) rather than counting ticks, so a slow tick never makes the timer drift.

The sitting clock runs alongside that, separately. It counts while you're working, and resets when you stand up,
take a break, or (in the right mode) your laptop sleeps.

A second loop runs about twelve times a second for anything animated: the tray ring, the glowing borders and the
live countdowns. It skips drawing whenever nothing has visibly changed.

Colours and animation are kept apart. A theme is just a list of two to six colours. Each animated part of the app
(the tray icon, the panel border, the break screen) has its own "motion" setting that decides how it uses those
colours: still, breathing, flowing around, or rainbow.

One rule that saved a lot of trouble: the tray icon runs on its own thread, and only Tkinter's thread is allowed
to touch the windows. So tray clicks are dropped onto a queue, and the main thread picks them up.

### The bugs that took the most work

**Windows that opened invisible.** To stop new windows flashing in the top-left corner before jumping to the
middle, I created them off-screen and see-through, then moved them into place. For some windows the move quietly
never happened, so they were "open" but stuck off-screen, only showing up in Alt-Tab. Worse, the colour picker
was set to block other windows until it closed, so once it got stuck off-screen, nothing could be closed at all.
I found it with a test that recorded where each window was, and how see-through, the moment it first appeared.
The fix was to always force the final position before revealing a window, and to stop the colour picker blocking
everything else.

**The black settings window.** Changing the theme rebuilds the settings window, which flickered. Pausing Windows'
drawing during the rebuild got rid of the flicker, but sometimes the window came back completely black. The fix
that finally worked is a bit of a trick: take a screenshot of the window, lay it exactly on top as an image,
rebuild everything underneath, then take the image away. Capturing the screen every 12 milliseconds during a save
confirmed you never see a half-drawn frame.

**Too much CPU.** Flow was using about 15% of a CPU core, which is a lot for a tray icon. Timing it showed the
animated tray icon was the main cost, and most of that was surprising: the tray library saves every single frame
to a temporary file on disk and reads it back. Building the icon in memory instead (with the Windows function
`CreateIconFromResourceEx`) took each update from about 7 milliseconds to about half a millisecond. Drawing the
frames at a lower resolution, which looks identical at tray size, saved more. The tray animation went from about
8.5% of a core to about 2.6%.

**Timers firing into the wrong place.** This one was strange. Every so often the tests logged an error that made
no sense, a button being called with the wrong arguments. It turned out that when a window closes, Tkinter throws
away that window's timer callbacks, but a timer that's still queued can then fire into a *newer* callback that
happened to reuse the same internal name. Mostly that just causes an error, but it could in theory press the
wrong button. Now all timers live on the main window, which never closes while the app runs.

**Opening the laptop in the morning.** In keep-running mode, time spent with the laptop asleep counts as working,
which is what I want if I'm studying on my iPad with the laptop closed for twenty minutes. Overnight, though, it
meant opening the laptop to an instant break screen. Now a sleep longer than two sessions starts fresh.

And a few smaller ones: the panel grew wider after it had been placed and ended up on my second monitor; the
glowing border had gaps where Windows 11 rounds the window corners, so the corners are now drawn as matching
curves; and my taskbar is at the top of the screen, which broke every popup that assumed it was at the bottom.

### Testing

There are three test suites, 167 checks in total, and they all run with one command:

```powershell
py tests\run_all.py
```

The behaviour tests cover sessions, breaks, nudges, pausing, quiet spells, sleep, stand-ups, every animation
effect and every settings page. The timer test checks the countdown against a real clock. The window tests open
every window in every animation mode and check that it lands on screen, has a dark title bar, closes with its X,
and stays inside the main monitor.

They run the real app, but with throwaway settings in a temporary folder, so they never touch my actual settings.
Idle time is faked, so "you've been away for seven minutes" takes a second to test instead of seven minutes. And
each test has a timeout, after one early test got stuck and left windows sitting on my screen.

My favourite bug from the tests themselves: I stored the fake idle time in a variable called `IDLE`, which is
also the name of Flow's grey colour. So the test quietly turned a colour into a number, and the app crashed in a
way that made no sense until I spotted it.
