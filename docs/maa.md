<!-- SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui> -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Native Maa resource candidate

This candidate uses MXU's existing interface and MaaFramework's native Linux
controller. It is separate from the legacy `gbfr_auto.py` command-line loop.
Neither a complete progression guide nor a general combat bot is included.

## Compatibility

The pinned pair is MXU 2.11.2 and MaaFramework 5.16.1 on Linux x86-64. The current
screen profile is simplified Chinese, 16:9, normalized to 1280×720. Native capture
uses Gamescope's PipeWire source; input uses its libei socket with Linux key codes.
The Agent refuses a controller connected to a different game, display, input
convention or replaced capture source.

Gamescope needs working PipeWire support. Version 3.16.20 has an upstream loop-lock
bug with newer PipeWire; the fix is included from 3.16.22. An unmodified 3.16.31
build passed the isolated capture/input fixture. Game acceptance and the supported
quest list must be checked separately; a fixture pass is not a completed game run.

The two catalog entries are MANIAC Quakadile and Blizzadile material quests.
Their evidence fields distinguish candidates from accepted routes. The current
strategy uses the game's Full Assist setting and AI teammates, without automation
combat input. Recommended quest power does not guarantee
that a particular party can win without player input.

Full Assist must be enabled before starting this profile. Older guides limit it to
Extreme; the [current official systems page](https://relink-ragnarok.granbluefantasy.com/en/systems/)
extends it to Maniac, Proud and quests through the expansion story. This does not
establish support for every mechanic or arena. See [progression scope](progression.md)
for the distinction between useful growth goals and implemented presets.

## Build and run

Use Python 3.13 and `uv`. From the repository:

```sh
python3 tools/build_maa.py --output dist/maa-candidate
```

The builder verifies downloaded archives/models against `assets/dependencies.json`,
creates a runtime-only environment, and copies the resource, Agent and licenses.
It refuses to overwrite an existing version directory. System packages and Steam
configuration are not changed. The output records its source revision, dirty state
and payload hashes in `bundle.json`; build from a clean signed checkpoint for a
reproducible candidate. The Python environment uses a managed interpreter and is
intended for local installation, not relocation to an arbitrary computer.

Start GBFR through Steam in a compatible Gamescope session, then run `./mxu` from
the bundle directory. Select GBFR's Gamescope device and the Chinese resource.
Choose one material preset and a successful-run limit. The task must already be
unlocked, and the AI party must be able to clear it. Start from Seedhollow town,
the quest counter, or a supported difficulty/quest-list menu. Automatic title-screen
continuation and arbitrary battle/result recovery are outside this candidate.

The welcome link opens the progress/stop panel using MXU's existing loopback server.
The default local port is 12792. Only one game and one controlling session are
supported. A second Agent or the legacy loop cannot acquire the same session lock.
The finish-current-run panel requires exactly one queued preset; it disables that
button for multiple queued tasks because MXU's per-task override does not cancel
later queued presets. Immediate stop remains available.

## What completion means

One success requires the session's own ready confirmation, an observed battle,
victory, reward settlement and return to town. A previous results screen cannot
create a success. Repeated victory/result frames cannot add another count.

The flow uses private matching. It waits through the game's results countdown and
does not enable the in-game repeat toggle; keep that toggle off. The software starts
each new run from town. It stops on defeat, an unsupported screen or a bounded
transition/battle timeout. The current two routes have a ten-minute wall-clock
battle budget, starting when their battle HUD is first observed; cinematics count
toward that budget. It does not change equipment, spend materials or advance
the story. Repeated battles still grant the game's normal rewards and experience.

“Finish this run” waits for rewards and town, then prevents another admission.
“Stop operations now” stops automation; it does not pause or close the game.
If the game is already fighting, its AI and timers continue. Leave the game using
its own menu when finished.

## Recovery and updates

If recognition stops, inspect the game and the client's error screenshot/log.
Return to a supported starting screen before starting a new session. Never
interpret an uncertain battle as a successful completion. A stopped client may
display its last cached screenshot; request live screenshots before acting.

Keep the previous version directory intact. Build an update into a new directory,
stop the old task, then exit the old client before opening the new one. Preserve
the old configuration with the old version; import compatible task settings through
MXU's configuration export/import controls. Do not overwrite an old environment or
copy live runtime locks/status files. Rollback means reopening the preserved old
client with its preserved configuration. Do not update components during a task.

After an Agent failure, use the client's normal Stop action first; native Maa tracks
the bounded keys registered by the pipeline. Check the game before resuming. A full
client/compositor crash is not a verified automatic-release condition. Do not run
the legacy command's broad release operation against an active Maa session, force
focus or kill the game as a recovery shortcut. Recover through the game's own menu
and reconnect only when the game responds normally.

## Development boundary

`assets/catalog.json` defines goals, visible quest names and prerequisites.
`tools/maa_catalog.py` generates standard task/option overrides. Shared screen
recognition and navigation live in `assets/resource/pipeline/farming.json`.
`maa_agent.py` owns only target/input checks, session accounting and stop semantics;
native Maa owns capture, recognition, input delivery and pipeline scheduling.
`assets/control.html` is a small status/stop view served by the existing MXU server.

Run the repository health checks:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/reuse lint
```

`tools/maa_replay.py RESOURCE IMAGE NODE...` checks native recognition on saved
images with all input disabled. Check each intended screen and unrelated negative
frames. Supply `--override PRESET.json` with a task's `pipeline_override` to check a
different catalog goal against both its own images and images from another quest.
Keep private full-screen captures/logs outside version control. A supported
release additionally needs real consecutive quest completions, exact-count and
delayed-stop trials, failure/ownership controls and independent-bundle validation.
