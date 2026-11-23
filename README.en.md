<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

[简体中文](README.md) | English

# GBFR Auto for Linux

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **Maa 0.3.0 is in development: it has its own control window and no longer needs MXU. Open any quest at the quest counter, and the software farms it with the game's own Repeat Quest, answering 是 (yes) on every tenth-run confirmation. The control window passed its real-game acceptance on 2026-11-23 (12 Quakadile runs in a row, finish after this run, stop now, closing the window during a session).**
>
> Kept for rollback are 0.2.0-rc.1 (two material presets, walks the menus to accept the quest) and 0.1.0-rc.2 (returns to town after every run), which use the MXU client.
> The new version uses this project's own window, MaaFramework's native Linux controller and this project's game resources. See the [Maa guide](docs/maa.en.md) for setup, use and stopping.

> [!WARNING]
> **AI-written code.** This repository was written by AI coding assistants (Claude Code and OpenAI Codex) at the owner's direction and has **not been reviewed line by line by a human**.
> Review the validation scope below and the [test guide](tests/README.md) before running it.

This is the Linux successor of the archived Windows version, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto).
Open the quest to farm at the quest counter and choose a number of successful runs or no limit: the software accepts the quest, then farms and counts with the game's own Repeat Quest, answers 是 on every tenth-run confirmation, and ends in town.
Combat depends on the game's Full Assist mode and the current AI party; the quests measured so far are recorded in [catalog.json](assets/catalog.json).
It is meant for letting the game play run after run in the foreground while you watch; it does not aim at the fastest material farming.

This file is a translation of the [Chinese README](README.md), which is canonical.

## Direction and status

Maa provides recognition, scheduling, capture and input. The interface is this project's own control window (GTK 4 and libadwaita), which starts one background program per session that calls MaaFramework's Python API directly; MXU, used before, is no longer needed.
This project maintains game-screen recognition, the farming flow, the window and small accounting/stop hooks rather than another automation engine.

| Component | Current scope |
|---|---|
| Window/runtime | Its own control window (the system Python, GTK 4, libadwaita) and pinned MaaFramework 5.16.1; separate version directories preserve rollback |
| Real-game validation | The control window (2026-11-23): 12 Quakadile runs in a row from the quest's details page with no intervention, finish after this run during run 2's battle, stop now during run 1's battle, and closing the window during a session (asked first, then stopped) all passed, with the window's buttons pressed by a test script; no real failure has been met yet. The background program it starts (2026-11-22, from the command line, the game in its normal window) finished 3 runs, and in another session stopped after run 2 as asked. 0.3.0 development version (2026-11-22, through MXU, game off the desktop): 12 Quakadile runs in a row from the quest's details page with no intervention, 是 on the tenth-run dialog continued without town, and it ended in town. The game behaviour it relies on (是 on the tenth run continues, a failure page returns to town by itself) was measured with a script the same day. 0.2.0-rc.1 (2026-11-22): 12 Quakadile runs in a row with no intervention (across the tenth-run dialog), finish after this run and stop now all passed; the new loop is not yet validated for Blizzadile. 0.1.0-rc.2: Chinese 2.0.6, 16:9, Seedhollow town; Quakadile passed 1/1 and five consecutive runs; Blizzadile passed 1/1 |
| Quest scope | Any private quest, starting from the quest opened at the counter; no recognition depends on a quest name. Only Quakadile has been measured, recorded in [catalog.json](assets/catalog.json) |
| Stop behavior | A number of successful runs (1-999) or until stopped; finish after the current run; immediate automation stop; a failed quest is not counted and stops the session |
| Outside current support | Automatic title continuation, walking to the counter and picking a quest, taking over mid-battle or on a results screen, equipment/spending automation, battle input |

A small success sample does not establish reliability for every party or extended unattended use.
Normal game rewards and experience still apply. Unknown screens, defeat and timeouts stop automation.

## Using the control window

The window runs on the system Python with GTK 4 and libadwaita. On Ubuntu (tested on Ubuntu 26.04), install first:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

Package names for other distributions are in the [Maa guide](docs/maa.en.md). Then, with Python 3.13 and `uv` available, build a release directory and add it to the application menu:

```sh
python3 tools/build_maa.py --output dist/gbfr-auto-0.3.0
dist/gbfr-auto-0.3.0/install.sh
```

Start the game through Steam, open the quest to farm at the quest counter (stay on its details page or the ready room), and click once inside the game window.
Open "GBFR 自动刷取" from the application menu, enter a number of runs or choose "until I stop it", and press Start.
Use one run the first time you farm a quest to check the current party. The window shows the quest name, the count, the stage, the time taken and the recent sessions,
and offers "finish after this run" and "stop now"; it sends a desktop notification when a session finishes or stops with an error.

The [Maa guide](docs/maa.en.md) covers prerequisites, what to do on errors, where files are kept and updating.
Stopping the software does not pause or close the game; exit through the game's own menu.

## Legacy command-line implementation

`gbfr_auto.py`, `farm.py` and the XTest battle loop remain as historical implementation and diagnostic references.
Maa acceptance does not validate the legacy loop. Its default command only recognizes and logs without sending input:

```sh
.venv/bin/python gbfr_auto.py run
.venv/bin/python gbfr_auto.py status
.venv/bin/python gbfr_auto.py stop
```

The legacy loop and Maa share one session lock and cannot control the game together.
Historical probe results are in [tools](tools/README.md).

## Development

Requires Python 3.12 or newer; CI and the local setup use 3.13.

    uv venv --python 3.13
    uv pip install -r requirements.txt -r requirements-dev.txt
    .venv/bin/pytest
    .venv/bin/ruff check .
    .venv/bin/reuse lint

- Every change goes on its own branch and reaches `dev` through a pull request.
- Before anything merges into `dev`, CI must pass: `ruff`, `pytest`, and `reuse`, which checks that every file states its copyright holders and licence.
- [tests/README.md](tests/README.md) says what the tests cover and what they cannot.

## Project structure

The first five rows are the Maa path. The legacy CLI and shared checks remain in
the repository.

| File | What it does |
|---|---|
| `assets/catalog.json`, `assets/interface.json` | Material goals, prerequisites and standard client options |
| `assets/resource/` | Shared native Maa recognition and quest flow |
| `maa_agent.py`, `maa_guard.py`, `maa_session.py`, `maa_target.py`, `maa_messages.py` | Target/input checks, ownership, successful-run accounting, stop semantics and Chinese stop explanations |
| `control_window.py`, `control_view.py`, `maa_runner.py`, `maa_protocol.py`, `maa_options.py` | The control window and the rules for what it shows and allows; the background program (runner), one per session; the JSON-lines protocol between them; `interface.json`'s options |
| `tools/build_maa.py`, `assets/bundle/`, `assets/icons/` | The release-directory build, its start/install/uninstall scripts and icon |
| `opencv.py` | template matching, and the guard against blank frames |
| `pages.py` | the engine that works out which screen is showing, from an ordered rule tree |
| `pagetree.py` | the game's actual tree: which templates, which screens, and what to do on each |
| `framediff.py` | how much changed between two frames; the probe uses it to tell a paused game from a running one |
| `backend.py` | the input vocabulary (move, battle, again, confirm, release all), implemented for Linux keyboard/mouse |
| `supervisor.py` | stopping when the selected window disappears, is replaced, or loses input |
| `config.py` | the TOML configuration |
| `applog.py` | logging |
| `gamescope.py` | finding the game's gamescope (its processes, nested X display and game window), and capturing frames with `gamescopectl`; for the loop, also cutting gamescope's black bars and scaling frames back to the game's own resolution. The probe and the platform layer use it |
| `xtest_input.py` | keyboard and mouse input for the game through XTest on gamescope's nested X server: only to the game's window, only the configured keys, and a dry run unless told otherwise |
| `farm.py` | the battle loop: capture, recognise, act, and stop rather than guess; no platform code |
| `control.py` | stop, pause and resume: the switches the loop reads, and the private local socket that sets them |
| `gbfr_auto.py` | the command line: `run` (a dry run unless `--live`), `stop`, `pause`, `resume`, `status`, `release` |
| `template/` | the reference images matched against the screen |
| `tools/linux_probe.py` | the Linux probe; see [tools/README.md](tools/README.md) |
| `docs/provenance/` | how this repository's history was produced from the archive |

## History

This repository's history begins with the Windows version's. Its first 84 commits were filtered mechanically out of the archive; [docs/provenance](docs/provenance/README.md) records how, maps old commits to new ones, and explains how to bring back the app layer kept in history.

Issue numbers in the older commit messages read `gbfr_auto#NN`, and those in code comments are bare (such as `#45`). Both refer to the [archive's issues](https://github.com/LowGuiGui/gbfr_auto/issues), not this repository's.

## About the AI-written code

- **Tools**: Claude Code and OpenAI Codex, working at the owner's direction.
- **Scope**:
  - Everything except upstream's original code: the fork's changes carried over from the archive (2026-08-23 to 2026-10-02), and everything done in this repository since.
  - AI-assisted commits identify the contributing assistant in a `Co-Authored-By` trailer. Older commits name Claude; the Linux cleanup names Codex. Upstream's 6 commits and the 3 initial setup commits of 2026-08-23 have no such trailer.
- **Review**: **no human line-by-line review.** The owner sets the project direction.
- **Verification**:
  - Automated tests on Linux, with Ruff and REUSE checks. See [tests/README.md](tests/README.md) for the current coverage and live-game limitations.
  - Real-game acceptance of the Maa candidate is recorded separately from automated checks; see [Direction and status](#direction-and-status) and the [Maa guide](docs/maa.en.md). It does not establish acceptance of the legacy loop.
- **Risk**: the bot sends input to the game. The GPL provides no warranty (GPL-2.0 sections 11 and 12).
- **Copyright**: whether AI-generated output is protected by copyright is legally unsettled. To the extent it is, the licence in [COPYRIGHT](COPYRIGHT) applies. AI output may also resemble its training data.
- **Please do not** submit this code to projects that ban AI-generated contributions (for example Gentoo, NetBSD, QEMU).
- This README is also maintained with AI assistance.

## Notes

- This is an unofficial project, not affiliated with or endorsed by Cygames. Granblue Fantasy: Relink and its artwork belong to Cygames, Inc.

## Licence

GPL-2.0-or-later: version 2 of the GPL or, at your option, any later version. `opencv.py`, `pagetree.py` and `.gitignore` used to contain upstream's GPL-2.0-only code; they were rewritten on 2026-10-05, and upstream's code now exists only in the git history. The licence texts are in [LICENSES/](LICENSES/). The images in `template/` are the game's artwork and are not under the GPL. [COPYRIGHT](COPYRIGHT) says who holds what, and why.
