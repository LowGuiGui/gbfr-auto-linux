<!-- SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui> -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# GBFR farming with Maa: user guide

> [!IMPORTANT]
> **0.3.0 is in development.** It has its own control window and no longer needs MXU. It
> also no longer walks the menus to pick a quest: you open any quest at the quest counter,
> the software takes over from there, farms it with the game's own Repeat Quest, and answers
> 是 (yes) on every tenth-run confirmation so farming goes on. The control window passed its
> real-game acceptance on 2026-11-23: 12 runs in a row from a quest's details page with no
> intervention (across the tenth-run dialog), finish after this run during run 2's battle,
> stop now during run 1's battle, and closing the window during a session, which asked first
> and then stopped. What has been verified so far is in [Limits and evidence](#limits-and-evidence).

The [Chinese guide](maa.md) is authoritative; this is its translation.

## What it does

It is meant for watching: the game plays run after run in the foreground, and the fastest
material farming is not the goal.

You open the quest you want to farm at the quest counter. The software accepts it and
departs. On the first results screen it turns on the game's own Repeat Quest (再次挑战),
and from then on the game starts each run by itself. Each results screen counts once.

Every ten runs the game asks "是否继续挑战该任务？" (continue this quest?). The software
answers 是, so the game starts the next run without going back to town, and farming can
go on indefinitely. When the run limit is reached, or you choose "finish after this run",
the software turns Repeat Quest off on the results screen and ends once the game is back
in town.

Battles are left to the game's Full Assist mode and your AI teammates; the software sends
no input during a battle. If a quest fails, that run is not counted: the software makes
sure Repeat Quest is off and stops, and the game returns to town by itself.

## Before the first use

**In the game:**

- The quest is unlocked and its matchmaking is set to 私人 (private). Any quest can be
  used; the one measured so far is MANIAC Quakadile (解决沙漠狂徒). Other quests are covered
  in [Limits and evidence](#limits-and-evidence).
- Full Assist (战斗托管模式) is on.
- Your current AI party clears the quest reliably. The first time you farm a quest, set the
  count to 1 and check one run.
- The game language is Simplified Chinese and the aspect ratio is 16:9.
- Optional: press Esc once on a loading-tips screen to turn on AUTO SKIP. Loading then
  continues by itself, and it was measured to stay on across a game restart. Without it,
  the software presses 继续 (continue) for you.

**On the computer:**

1. Install GTK 4, libadwaita and their Python bindings for the window. The window runs on
   the system's own Python; no Python packages need installing. On Ubuntu (tested on Ubuntu
   26.04):

   ```sh
   sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
   ```

   Package names on other distributions (not tested): the same on Debian;
   `python3-gobject gtk4 libadwaita` on Fedora; `python-gobject gtk4 libadwaita` on Arch.
   libadwaita 1.5 or later is needed.

2. Build a release directory. This needs Python 3.13 and `uv`:

   ```sh
   python3 tools/build_maa.py --output dist/gbfr-auto-0.3.0
   ```

   The build downloads pinned versions of the text-recognition model and of the Python
   packages the background program uses (MaaFramework among them), checks each hash, and
   changes no system or Steam settings. It never overwrites an existing directory, so older
   versions stay available for rollback. Once built, the release directory is never written
   to; settings, history and logs live in the folders listed under
   [Where files are kept](#where-files-are-kept).

3. Add it to the application menu:

   ```sh
   dist/gbfr-auto-0.3.0/install.sh
   ```

   This writes `~/.local/share/applications/io.github.lowguigui.GbfrAutoLinux.desktop` and
   the icon, and points the link `~/.local/share/gbfr-auto-linux/current` at this release
   directory; the menu entry always starts through that link. Run it again if you move the
   directory. You can also run `./gbfr-control` in the release directory without a menu
   entry, but the desktop notifications on finishing and on errors need the menu entry:
   GNOME shows notifications only for applications it knows.

## Start farming

1. Start the game through Steam, walk to the quest counter and open the quest to farm. Any
   of these screens is a valid start: the quest details page (承接任务 at the lower right),
   matchmaking settings, the ready room with the quest accepted, or the ready panel.
   **After the game starts, click once in the game window.** Once per launch is enough; the
   window then receives keys in the background.
2. Open "GBFR 自动刷取" from the application menu. Only one window opens; choosing the menu
   entry again brings the open one to the front.
3. Enter a run count (1–999, default 10), or turn on "一直刷，直到我停止" (until I stop it).
4. Press 开始 (Start). The software finds GBFR's Gamescope by itself, with no device to pick:
   it accepts only the one running GBFR that Steam started, and does not start when it finds
   none or several.

Then leave the game alone. The window shows the quest name read from the results screen,
"k / N 次成功" (k of N successful runs) and the current stage. If the software stops because
of an error, the window says why and what to do.

## What the window shows

- **Status**: the quest name (read on the first results screen), the run count, the stage,
  the time this session has taken (during a battle, also how long this battle has lasted),
  the average time per run, the number of failed quests, and the reason it stopped. On an
  error a small line below gives the technical reason.
- **Controls**: the run count, "until I stop it", 开始 (Start), 本轮后停 (finish after this
  run) and 立即停止 (stop now). The count cannot be changed while a session runs.
- **Recent sessions**: one line for each of the last five sessions, with when it ended, the
  quest name, the outcome, the count, the average time per run and the total time.
- **打开日志文件夹** (open the log folder): opens the folder with MaaFramework's logs.
- "显示的信息…" (information shown) in the menu at the top right shows or hides each of these
  items, and saves the choice at once. "最近几条日志" (recent log lines) is hidden by default,
  everything else shown; the controls are always shown.
- The pin in the title bar opens GNOME's own window menu, the same as a right click on the
  title bar. Choose 置顶 (Always on Top) there to keep the window above the others, and choose
  it again to undo that. The pin does not show whether the window is on top, because GNOME
  does not let programs read that state.
- When a session finishes or stops with an error, a desktop notification appears. There is
  none when you stop it yourself, nor when Start is refused at once (the game is not running,
  for example), because the window already says why.
- While a session runs, the software keeps the screen from dimming, blanking and locking,
  and lets go when the session ends or the background program exits. This works on GNOME
  only and changes no power or lock settings; when it cannot, the window says so once and
  farming goes on.

The window runs on the system's Python. Each session starts a separate background program
(the runner), which loads MaaFramework, captures the screen and sends the keys. If the
window closes or crashes, the runner stops by itself; if the runner exits unexpectedly, the
window says so and sends a key-up for every key the farming uses.

## What one run looks like

| Stage | What the software does |
|---|---|
| First run | Accepts the quest on its details page, confirms private matchmaking, departs once ready |
| Battle | Watches only. Stops if one battle exceeds 30 minutes (the game's own time limit usually ends the run sooner) |
| Results | Counts once when the results screen appears. On the rewards page it turns Repeat Quest on to continue, or off to stop. It waits for the reward reveal before pressing 3, reads the toggle back, and presses once more if it did not change |
| Later runs | The game starts each run by itself. With Repeat Quest on it skips the chest phase |
| Runs 10, 20, … | The game resets Repeat Quest to off and asks whether to continue, with the cursor on 否 (no). To continue, the software presses Up, confirms on a fresh screenshot that the cursor is on 是, then presses Return. The game turns Repeat Quest back on and does not go to town. To stop, the software leaves the dialog to time out into town |
| Quest failed | The game shows QUEST FAILED, then a single results page with no completion reward. The software does not count it; if Repeat Quest is on, it presses 3 to turn it off (the game would otherwise retry), then stops. The game returns to town after 30 seconds |

Measured on 2026-11-22 (Quakadile, test party): about 6 minutes per run with Repeat Quest
on, about 5 of them in battle; about 70 seconds from results to the next battle, also after
answering 是 on the tenth run.

## Stopping

- **Run limit reached**: stops by itself and ends once the game is back in town.
- **Finish after this run**: press 本轮后停. The software turns Repeat Quest off on this
  run's results screen and ends once the game is back in town.
- **Stop now**: press 立即停止. It only stops the software's input; the game does not
  pause. If Repeat Quest is on, the game keeps farming up to the next tenth-run dialog and
  then returns to town. To stop it at once, press 3 on a rewards page to turn Repeat Quest
  off. The window shows "已按你的要求停止" (stopped as you asked), the counted runs, and
  whether Repeat Quest is still on.
- **Closing the window during a session**: the window asks first. "停止并关闭" (stop and
  close) is the same as stop now, and the window closes once the runner has stopped; "取消"
  (cancel) changes nothing.
- **The runner does not stop in time**: if the runner has not exited 35 seconds after a
  stop was asked for, the window offers "结束后台" (end the background program). That ends
  only this software's runner, never the game, and then sends a key-up for every key the
  farming uses.
- **Quitting the game**: use the game's own menu. Never kill the game, Wine/Proton or
  Gamescope: that can damage the save.

## When it stops with an error

The window gives the reason and what to do, with the technical reason in a small line below.
Common cases:

| The window says | What to do |
|---|---|
| No running game was found | Start the game through Steam, wait for a stable screen, and start again |
| Several Gamescopes running GBFR were found | Keep only one game running |
| A farming session is already running | Perhaps the one in an MXU release directory, or another window. Stop it first, then start |
| The screen did not reach the expected state in time | Look at the game, open the quest (details page or ready room) and start again. Usual causes: a wrong start screen, another quest already accepted, a dialog, a game language other than Simplified Chinese, no click in the window after launch |
| The game or its Gamescope restarted or closed | Restart the game through Steam, wait for a stable screen, click in the window, open the quest and start again. Counted runs stand |
| The quest failed | That run does not count; the game returns to town by itself. Use a stronger party or a lower difficulty, and check one run first |
| 是 could not be chosen on the tenth-run dialog; the game went back to town before the session finished | Counted runs stand. Open the quest at the counter again and restart |
| The battle exceeded its 1800 s limit | Check that Full Assist is on and the party can clear the quest |
| Pressed 3 twice and Repeat Quest is still off | Check whether the game is stuck; open the quest at the counter again and restart |
| Another window or overlay is over the game; a Ctrl, Shift or Alt key is held | Close the overlay, release the key, and start again |
| The resource or the OCR model failed to load | Rebuild the release directory |
| The background program exited unexpectedly | The window has already sent a key-up for every key the farming uses; look at the game. `window-runner.log` next to the log folder and that session's logs help find the cause |

Without a click in the game window after launch, keys may not reach the game at all, which
usually shows as "the screen did not reach the expected state in time". Click the window and
start again.

## Where files are kept

| What | Where |
|---|---|
| Settings: the run count, which items are shown, the window size | `~/.config/gbfr-auto-linux/settings.json` |
| Session history, one line per session | `~/.local/state/gbfr-auto-linux/history.jsonl` |
| MaaFramework's logs, one folder per session, the last 5 kept (about 190 MB per hour of farming) | `~/.local/state/gbfr-auto-linux/logs/` |
| The runner's error output from the latest session | `~/.local/state/gbfr-auto-linux/window-runner.log` |
| The current session's state | `$XDG_RUNTIME_DIR/gbfr-auto-linux/session.json` |

Old and new versions share these files, and deleting them does not affect a release
directory. `uninstall.sh` leaves them alone.

**Memory.** While a session runs, MaaFramework's native memory grows by about 130 MB per hour
(measured 2026-11-22), and all of it is freed when the session ends and the runner exits.
Several hours of farming take a few hundred MB more; this is known and accepted for now.

**Python.** A release directory's `.venv` uses the Python 3.13 that uv manages (under
`~/.local/share/uv/python/`). Removing it with `uv python uninstall` stops every release
directory's runner from starting; rebuild to fix that.

## Updating and rolling back

1. Build the new version into a new directory and leave the old one alone.
2. Stop any running session and close the window.
3. Run `./install.sh` in the new directory. The link now points at the new directory; the
   menu entry itself stays the same.

To roll back, run `./install.sh` in the old directory. Earlier 0.3.0 development builds
(those whose `install.sh` has no `current` link) write their own path into the menu entry,
and GNOME's app grid keeps starting the old path until the entry has disappeared once: to go
back to one of them, run `./uninstall.sh` in the current directory, wait a minute, then run
the old directory's `./install.sh`. An older release directory with MXU (0.2.0, and the 0.3.0
development builds that contain an `mxu` file) keeps using its own `./start.sh`; both share
the session lock, so they never control the game at the same time; new MXU release
directories can no longer be built. `./uninstall.sh` removes the menu entry, the icon and
the link, but only when the link points
at its own directory (or at a directory that no longer exists). Never update any part while
a session is running.

## Limits and evidence

**Supported.** MaaFramework 5.16.1 (the Python package, which brings its native libraries),
Linux x86-64, a GNOME desktop: the window and its notifications may work on other desktops
but have not been tested there, and keeping the screen awake works on GNOME only. The window
needs the system's Python with GTK 4 and libadwaita 1.5 or later; it was tested with GTK
4.22.4 and libadwaita 1.9.1 on Ubuntu 26.04. Game 2.0.6, Simplified Chinese, 16:9, scaled to
1280×720 for recognition. Screenshots come from Gamescope's PipeWire output and input goes
through its libei interface; Gamescope 3.16.22 or later is needed (3.16.20's PipeWire capture
stalls). One game and one session at a time; a second controller is refused. Private quests
only.

**Verified:**

- The game's own behaviour (2026-11-22, Quakadile, in a Gamescope that showed nothing on the
  desktop and played no sound, driven by a measurement script):
  - Tenth-run dialog: after Up and Return chose 是, the same page's toggle read
    "撤销再次挑战" (repeat on), and when its countdown ended run 11 started without town;
    runs 11 and 12 brought no dialog.
  - Quest failure (solo, AI teammates removed): after QUEST FAILED came a single results page
    with 0 rupees and "无奖励道具" (no reward items); with no input it returned to town after
    30 seconds.
  - Holding W during battle did not make this quest's battles shorter (6 runs against 5,
    308 s against 299 s on average).
  - The loading screen's AUTO SKIP stayed on across a game restart.
- The runner against the real game (2026-11-22, from the command line, without MXU, the game
  in its normal window): 3 runs from Quakadile's details page, with the quest name read; then
  another session with finish after this run asked during run 2's battle, which ended in
  town after 2 runs. No key was left held after either session, and the screen was kept
  awake only while a session ran.
- The control window (2026-11-23, in a private headless GNOME Shell, with the scripted game
  in place of the runner's game): 3 runs to the end, finish after this run during run 2's
  battle (2 runs done), stop now, the key-up after a runner that died and after one that
  would not stop, the confirmation when closing, the display settings, a single window, and
  the desktop notification on finishing. The built release directory, with no game running,
  answered Start by asking for the game to be started, and the installed menu entry opened
  the window.
- The control window's real-game acceptance (2026-11-23, the game started the usual way,
  the window opened from a release directory): started on Quakadile's details page with a
  limit of 12 and finished with no intervention; the software pressed only the eight expected
  keys, answered 是 on run 10's dialog, and the window read the quest name. 本轮后停 (finish
  after this run) during run 2's battle ended in town after 2 runs; 立即停止 (stop now) during
  run 1's battle stopped the keys at once, and the game finished that battle and returned to
  town by itself; closing the window during a session asked first, then stopped and closed,
  and the runner exited. The window's per-run times were within 0.1 s of MaaFramework's log;
  the screen was kept awake only while a session ran; no key was left held after any
  session. The window's buttons were pressed by a test script, not with a pointer.
- 0.3.0's development version against the real game (2026-11-22, through MXU, in a
  Gamescope that showed nothing on the desktop and played no sound): started on Quakadile's
  details page with a limit of 12 and finished with no intervention. The software pressed
  only the eight expected keys: four to depart, 3 on run 1's results to turn Repeat Quest on,
  Up and Return on run 10's dialog, and 3 on run 12's results to turn it off. After 是, run
  11 started 72 seconds later without town, the same gap as every other run. It ended in
  town; the control panel read the quest name "解决沙漠狂徒", and MXU's log showed each count
  and the finish.
- 0.2.0-rc.1's real-game acceptance (2026-11-22, through MXU, game window in the
  background): 12 Quakadile runs in a row with no intervention (that version let the
  tenth-run dialog time out and accepted the quest again from town); finish requested
  during battle 2; stop now during battle 1. MXU's log showed each count and the finish; a
  second controller was refused.
- 0.3.0's offline tests: scripted-game scenarios built from the measured behaviour,
  including 是 on the tenth run, three 是 answers in a row when farming until stopped, a
  cursor that never reaches 是, a dialog left unanswered until town, a failure with Repeat
  Quest on and off, and a failure page first recognised by the count's recheck; recognition
  of 19 measured screenshots (masked to the regions read), including the failure page and
  the dialog with the cursor on 是 and on 否; and a frame-by-frame replay of that day's
  failed run and 12-run block through the new recognition.

**Not yet verified:** in the real-game acceptance the window's buttons were pressed by a
test script, and nobody has watched the desktop notifications appear on a real desktop.
Against the real game, 0.3.0 has started only from a quest's details page; starting from
matchmaking settings, the ready room or the ready panel has been checked only on measured
screenshots. This version has not repeated the refusal of a second controller in the real
game, and it has not met a real failure (failure handling is tested only against the
scripted game). Only Quakadile was measured: other quests' battle screens,
objective text and battle lengths have not been seen. The dialog at run 20, what 否 does,
and what pressing 3 (retry) does on a failure page are untested. The pre-key check has not
yet been seen stopping a held key in the real game.

**Out of scope:** starting the game, continuing from the title screen, walking to the
counter and picking a quest, equipment, spending materials, story progress, battle input;
other languages, resolutions and aspect ratios. Whether Full Assist and AI teammates clear a
quest depends on your party. For the difference between useful progression goals and the
quests verified so far, see [progression scope](progression.md).

## Development

- The task, its text and the verified quests are in `assets/catalog.json`;
  `tools/maa_catalog.py` renders `assets/interface.json` from it.
- Recognition and flow are in `assets/resource/pipeline/farming.json`: `GBFR_See_*` nodes
  say what is visible, and action nodes combine them with the Agent's `GBFRSession` gate. No
  recognition depends on a quest's name.
- `maa_agent.py` counts runs and decides when to stop, `maa_session.py` holds the counting
  rules, `maa_guard.py` checks the target and each key, and `maa_messages.py` turns stop
  reasons into the Chinese messages above.
- `control_window.py` is the window, and `control_view.py` holds the rules for what it shows
  and when each button can be pressed (no GTK needed, so CI tests them). For each session
  the window starts a `maa_runner.py` child and talks to it in `maa_protocol.py`'s JSON
  lines; the runner's command line is described in [tools/README.md](../tools/README.md).
- The release directory's start, install and uninstall scripts are in `assets/bundle/`, and
  the icon is in `assets/icons/`.

Health check:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/reuse lint
```

The recognition fixtures need the OCR model: point `GBFR_MAA_OCR_MODEL` at a `model`
directory, or fetch it with `python3 tools/build_maa.py --ocr-only --output <dir>`.
`tools/maa_replay.py` runs recognition on screenshots offline, and `tools/maa_fixtures.py`
turns a screenshot into a committable fixture. The window's smoke test,
`tests/window_smoke.py`, runs locally only: it opens the window and presses its buttons by
itself, so run it in a private desktop session. See the [test notes](../tests/README.md) for
what the tests cover.
