<!-- SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui> -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# GBFR farming with Maa: user guide

> [!IMPORTANT]
> **0.2.0 is a release candidate.** The new loop farms with the game's own Repeat Quest.
> 0.2.0-rc.1 passed its first real-game acceptance on 2026-11-22: 12 Quakadile runs in a
> row with no intervention, finish after this run, and stop now all behaved as intended.
> Later fixes to stop and error reporting have passed offline tests only. See
> [Limits and evidence](#limits-and-evidence).

The [Chinese guide](maa.md) is authoritative; this is its translation.

## What it does

The software takes the first run of a quest for you, then turns on the game's own
Repeat Quest ("再次挑战") so that the game runs the quest again by itself. It counts one
run each time a results screen appears. When the count is reached, or when you ask it
to finish after the current run, it turns Repeat Quest off on the results screen and
ends once the game is back in town.

Every ten repeats the game asks whether to go on. The software does not answer; it
lets the dialog time out, waits for town, and takes the quest again. Battles are left to
the game's Full Assist mode and AI teammates; the software presses no key in battle.

## Before the first use

**In the game:**

- The quest is unlocked. There are two presets, both MANIAC: Quakadile materials
  (quest "解决沙漠狂徒") and Blizzadile materials (quest "我要打造冰冷的铠甲").
- Full Assist ("战斗托管模式") is on.
- Your AI party clears the quest reliably. Use a count of 1 the first time to check.
- The game language is simplified Chinese and the aspect ratio 16:9.
- Optional: press Esc once on a loading-tips screen to turn on AUTO SKIP, so loading
  continues by itself. Without it the software presses "继续" for you.

**On the computer**, build a version directory. It needs Python 3.13 and `uv`:

```sh
python3 tools/build_maa.py --output dist/maa-0.2.0
```

The build downloads pinned MXU, MaaFramework and OCR models, checks each against its
hash, and changes no system or Steam setting. It never overwrites an existing
directory, so an old version can stay for rollback.

Optional: copy `gbfr-auto.desktop` from the version directory to
`~/.local/share/applications/` for a "GBFR 自动刷取" menu entry. It records this
directory's absolute path; copy it again if the directory moves.

## Start farming

1. Start the game through Steam and stop in town, or on the ready screen of the quest
   to farm. **After the game starts, click once inside its window.** Once per launch is
   enough; afterwards the window takes keys while in the background.
2. Open MXU: "GBFR 自动刷取" in the application menu, or `./start.sh` in the version
   directory. The "GBFR 自动刷取" instance already has its controller and resource;
   check that the device is GBFR's Gamescope.
3. Tick one preset only. Enter the number of successful runs (default 10), or turn on
   "一直刷，直到我停止" (until I stop it).
4. Press Start.

Leave the game alone from then on. MXU's log shows "第 k/N 次完成" (run k of N done). When the
software stops on an error, the log also says why.

## What one run looks like

| Stage | What the software does |
|---|---|
| First run | Walks from town to the quest counter, takes the quest with private matching, departs |
| Battle | Watches without pressing. Stops if one battle passes 10 minutes |
| Results | Counts once when the results screen appears. On the rewards page it turns Repeat Quest on to continue or off to stop: it waits until the rewards are shown, presses 3, reads the toggle back, and presses once more if it did not change |
| Later runs | The game starts the next run itself, and skips the treasure-chest phase while Repeat Quest is on |
| Tenth run | The game resets Repeat Quest to off and asks "是否继续挑战该任务？". The software does not answer; the dialog times out into town and the software takes the quest again. In the acceptance this cost only about 25 s per ten runs |

Measured in the acceptance (2026-11-22, Quakadile, test party): with Repeat Quest on, a
run takes about 6 minutes, of which about 5 are battle; 12 runs took 74 minutes.

## Stopping

- **Count reached**: stops by itself and ends once the game is in town.
- **Finish after this run**: open the control panel (the link in MXU's welcome
  message) and press "本轮结束后停下". The software turns Repeat Quest off on this run's
  results screen and ends once the game is in town. One preset per run only, for now.
- **Stop now**: MXU's Stop, or "立即停止操作" in the panel. This stops only the
  software's input; the game does not pause. If Repeat Quest is on, the game keeps
  farming by itself until its tenth-run dialog, then returns to town. To stop it at
  once, press 3 on the rewards page of the results to turn Repeat Quest off. The control
  panel shows "已按你的要求停止" (stopped as you asked) with the runs counted, and says whether
  Repeat Quest is still on. MXU may show the run as failed; that is how MXU shows a
  stopped task, and the panel is the one to trust.
- **Leaving the game**: use the game's own menu. Never force-kill the game, Wine/Proton
  or Gamescope; that can damage the save.

## When it stops with an error

When the software stops on an error, the control panel and MXU's log both say why and
what to do; the panel also has the technical reason folded below. Common cases:

| The panel says | What to do |
|---|---|
| The screen did not become what was expected in time | Look at the game. Return to town or the ready screen and start again. Usual causes: a wrong starting screen, a dialog, a language other than simplified Chinese, no click in the window since launch |
| MXU is not connected to GBFR's Gamescope | Pick GBFR's Gamescope as MXU's device and make sure the game was started through Steam |
| The game or its Gamescope closed during the run | Start the game again through Steam, wait for a stable screen, click in the window, and start again from town. Counted runs stand |
| The battle passed its 600 s limit | Check that Full Assist is on and the party can clear the quest |
| Pressed 3 twice and Repeat Quest is still off | Check whether the game is stuck; start again from town |
| Another window or overlay is over the game; Ctrl, Shift, Alt or similar is held | Close the overlay, release the key, start again |
| The quest failed | Use a stronger party, or check with a count of 1 first |

If you have not clicked inside the game window since launch, keys may not reach the
game at all, which usually ends in the first case. Click the window and start again.

## Updating and rolling back

1. Build the new version into a new directory and leave the old one as it is.
2. Stop the old version's task, close the old MXU, then open the new one.
3. To keep your settings with the same MXU version, copy the old directory's
   `config/mxu-GBFRLinux.json` into the new one while both MXU windows are closed, and
   keep the original where it is. Across MXU versions, use MXU's own export and import.

Rolling back means closing the new version and running `./start.sh` in the old
directory (`./mxu` in the 0.1.0-rc.2 directory). Never update anything while a task runs.

## Limits and evidence

**Supported profile.** MXU 2.11.2 and MaaFramework 5.16.1 on Linux x86-64. Game 2.0.6,
simplified Chinese, 16:9, scaled to 1280×720 for recognition. Capture uses Gamescope's
PipeWire output and input its libei socket; Gamescope 3.16.22 or newer is required
(PipeWire capture stalls on 3.16.20). One game and one session at a time; a second
controller is refused.

**Verified:**

- Real-game acceptance of the new loop (2026-11-22, 0.2.0-rc.1, run through MXU, game
  window in the background):
  - 12 Quakadile runs in a row with no intervention. Run 1 took the quest from the menus,
    and the software pressed no key in runs 2 to 9. The tenth-run dialog went unanswered;
    the game returned to town and the software took the quest again. After run 12 it
    turned Repeat Quest off and finished in town.
  - Finish requested during run 2's battle: run 2 was counted, Repeat Quest was turned
    off, and the session finished in town with no third run.
  - Stop now during run 1's battle: no key was left held, and the game finished the run
    by itself and returned to town.
  - MXU's log showed every count and the finish message; a second controller started
    during the acceptance was refused.
  - After a game restart no loading-tips screen needed a key, so AUTO SKIP stayed on
    (seen once).
- The game's own behaviour (2026-11-21): a measurement script ran 12 Quakadile runs in the real game.
  The results have two pages with a 30 s countdown each, and the Repeat Quest toggle is on
  the second (rewards) page. With repeat on the game skips the treasure-chest phase. On
  the tenth run the game resets the toggle and shows a dialog that, left alone, ends in
  town. Keys sent in the first ~2 s of the rewards page are ignored. After one click in
  the window following launch, keys kept working with the window in the background for
  about 80 minutes.
- Offline tests of the new loop: 20 scripted-game scenarios written from that behaviour,
  including a game that closes mid-run and an immediate stop;
  13 recognition cases on measured frames masked to the regions recognition reads;
  recognition replayed on all 3,406 measured frames with no false hit.
- Menu navigation from town to departure is unchanged from 0.1.0-rc.2, which was
  validated in the real game: Quakadile once and five runs in a row, Blizzadile once.

**Not yet verified:** what choosing "是" in the dialog does; the Repeat Quest loop for
Blizzadile; what the game and the software do after a failed battle; pressing "继续" on a
loading-tips screen (none appeared in the acceptance); the pre-key checks stopping a held
key in the real game; the fixes made after the acceptance (the right reason when the game
closes, an immediate stop recorded as a stop rather than an error, error reasons in MXU's
log), which have run only in offline tests.

**Out of scope:** starting the game, continuing from the title screen, changing
equipment, spending materials, advancing the story, input during battle; other
languages, resolutions and aspect ratios. Whether Full Assist and the AI teammates clear
a quest depends on your party. [Progression scope](progression.md) separates useful growth
goals from the presets implemented here.

## Development

- Quests, copy, battle limits and validation status live in `assets/catalog.json`;
  `tools/maa_catalog.py` renders `assets/interface.json` from it.
- Recognition and flow live in `assets/resource/pipeline/farming.json`: `GBFR_See_*`
  nodes say what is visible, and action nodes combine them with the Agent's
  `GBFRSession` decision.
- `maa_agent.py` counts runs and decides when to stop, `maa_session.py` holds the counting
  rules, `maa_guard.py` checks the target and each key, and `maa_messages.py` turns stop
  reasons into the Chinese messages above.

Health checks:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/reuse lint
```

The recognition cases need the OCR model: point `GBFR_MAA_OCR_MODEL` at a `model`
directory, or fetch one with `python3 tools/build_maa.py --ocr-only --output <dir>`.
`tools/maa_replay.py` runs recognition on saved frames offline, and
`tools/maa_fixtures.py` turns a frame into a committable case. What the tests cover is
in the [test notes](../tests/README.md).
