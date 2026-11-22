<!-- SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui> -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# GBFR farming with Maa: user guide

> [!IMPORTANT]
> **0.3.0 is in development.** It no longer walks the menus to pick a quest: you open any
> quest at the quest counter, the software takes over from there, farms it with the game's
> own Repeat Quest, and answers 是 (yes) on every tenth-run confirmation so farming goes on.
> On 2026-11-22 the development version ran 12 runs in a row through MXU against the real
> game, starting from a quest's details page and crossing the tenth-run dialog with no
> intervention; the previous version, 0.2.0-rc.1, passed a real-game acceptance the same
> day. What has been verified so far is in [Limits and evidence](#limits-and-evidence).

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

**On the computer**, build a release directory. This needs Python 3.13 and `uv`:

```sh
python3 tools/build_maa.py --output dist/maa-0.3.0
```

The build downloads pinned versions of MXU, MaaFramework and the text-recognition model,
checks each hash, and changes no system or Steam settings. It never overwrites an existing
directory, so older versions stay available for rollback.

Optional: copy the release's `gbfr-auto.desktop` to `~/.local/share/applications/` to get a
"GBFR 自动刷取" menu entry. It records the directory's absolute path; copy it again if you
move the directory.

## Start farming

1. Start the game through Steam, walk to the quest counter and open the quest to farm. Any
   of these screens is a valid start: the quest details page (承接任务 at the lower right),
   matchmaking settings, the ready room with the quest accepted, or the ready panel.
   **After the game starts, click once in the game window.** Once per launch is enough; the
   window then receives keys in the background.
2. Open MXU from the "GBFR 自动刷取" menu entry, or run `./start.sh` in the release
   directory. The "GBFR 自动刷取" instance already has the controller and resource; check
   that the device is GBFR's Gamescope.
3. Tick "连刷当前任务" (farm the current quest). Enter a run count (default 10), or turn on
   "一直刷，直到我停止" (until I stop it).
4. Press Start.

Then leave the game alone. MXU's log shows "第 k/N 次完成" (run k of N done), and the control
panel shows the quest name read from the results screen. If the software stops because of
an error, the log says why.

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
- **Finish after this run**: open the control panel (the link in MXU's welcome message) and
  press "本轮结束后停下". The software turns Repeat Quest off on this run's results screen and
  ends once the game is back in town.
- **Stop now**: MXU's Stop, or "立即停止操作" in the control panel. It only stops the
  software's input; the game does not pause. If Repeat Quest is on, the game keeps farming
  up to the next tenth-run dialog and then returns to town. To stop it at once, press 3 on
  a rewards page to turn Repeat Quest off. The control panel shows "已按你的要求停止" (stopped
  as you asked), the counted runs, and whether Repeat Quest is still on. MXU may show the run
  as failed; that is how MXU shows a stopped task, and the control panel is authoritative.
- **Quitting the game**: use the game's own menu. Never kill the game, Wine/Proton or
  Gamescope: that can damage the save.

## When it stops with an error

The control panel and MXU's log both give the reason and what to do; the control panel
also has the technical reason folded away. Common cases:

| The panel says | What to do |
|---|---|
| The screen did not reach the expected state in time | Look at the game, open the quest (details page or ready room) and start again. Usual causes: a wrong start screen, another quest already accepted, a dialog, a game language other than Simplified Chinese, no click in the window after launch |
| MXU is not connected to GBFR's Gamescope | Pick GBFR's Gamescope as MXU's device, and check that the game was started from Steam |
| The game or its Gamescope closed during the session | Restart the game through Steam, wait for a stable screen, click in the window, open the quest and start again. Counted runs stand |
| The quest failed | That run does not count; the game returns to town by itself. Use a stronger party or a lower difficulty, and check one run first |
| 是 could not be chosen on the tenth-run dialog; the game went back to town before the session finished | Counted runs stand. Open the quest at the counter again and restart |
| The battle exceeded its 1800 s limit | Check that Full Assist is on and the party can clear the quest |
| Pressed 3 twice and Repeat Quest is still off | Check whether the game is stuck; open the quest at the counter again and restart |
| Another window or overlay is over the game; a Ctrl, Shift or Alt key is held | Close the overlay, release the key, and start again |

Without a click in the game window after launch, keys may not reach the game at all, which
usually shows as the first case. Click the window and start again.

## Updating and rolling back

1. Build the new version into a new directory and leave the old one alone.
2. Stop the old version's task, close the old MXU, and open the new one.
3. 0.3.0 has a single task, "连刷当前任务"; 0.2.0's two presets are gone. Do not carry over
   0.2.0's `config/mxu-GBFRLinux.json`; let the new directory use its own.

To roll back, close the new version and run `./start.sh` from the old directory (`./mxu` in
0.1.0-rc.2). 0.2.0 starts from town or from the ready room of one of its two preset quests.
Never update any part while a task is running.

## Limits and evidence

**Supported.** MXU 2.11.2 and MaaFramework 5.16.1 on Linux x86-64. Game 2.0.6, Simplified
Chinese, 16:9, scaled to 1280×720 for recognition. Screenshots come from Gamescope's
PipeWire output and input goes through its libei interface; Gamescope 3.16.22 or later is
needed (3.16.20's PipeWire capture stalls). One game and one session at a time; a second
controller is refused. Private quests only.

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

**Not yet verified:** against the real game, 0.3.0 has started only from a quest's details
page; starting from matchmaking settings, the ready room or the ready panel has been checked
only on measured screenshots. This version has not repeated finish after this run, stop now
or the refusal of a second controller in the real game, and it has not met a real failure
(failure handling is tested only against the scripted game). The real-game check ran with
the game off the desktop; this version has not run with the game window in the foreground.
Only Quakadile was measured: other quests' battle screens, objective text and battle lengths
have not been seen. The dialog at run 20, what 否 does, and what pressing 3 (retry) does on a
failure page are untested. The pre-key check has not yet been seen stopping a held key in
the real game.

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

Health check:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/reuse lint
```

The recognition fixtures need the OCR model: point `GBFR_MAA_OCR_MODEL` at a `model`
directory, or fetch it with `python3 tools/build_maa.py --ocr-only --output <dir>`.
`tools/maa_replay.py` runs recognition on screenshots offline, and `tools/maa_fixtures.py`
turns a screenshot into a committable fixture. See the [test notes](../tests/README.md) for
what the tests cover.
