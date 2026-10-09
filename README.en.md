<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

[简体中文](README.md) | English

# GBFR Auto for Linux

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **Experimental. The complete farming loop has not been validated against the game.**
>
> - This is the Linux successor of the Windows version, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto), which is paused and archived.
> - The repository contains a Linux CLI, a gamescope/XTest farmer and diagnostic probes.

> [!WARNING]
> **AI-written code.** This repository was written by AI coding assistants (Claude Code and OpenAI Codex) at the owner's direction, and has **not been reviewed line by line by a human**.
>
> - Only the probe has run against the game on Linux (2026-10-05, once per step); the core has automated tests only.
> - Review it yourself before running it, and use it at your own risk.
>
> Details: [About the AI-written code](#about-the-ai-written-code).

Battle automation for Granblue Fantasy: Relink. It recognises the game's screens by template matching and drives the game with simulated input, farming the same quest in a loop.

This file is a translation of the [Chinese README](README.md), which is canonical.

## Status

Legend:

- ✅ ran on the owner's real machine
- 🧪 automated tests only
- ⏳ not done
- ❓ still to be measured

| Part | Status | Evidence |
|---|---|---|
| Platform-free core: template matching, page tree, config, logging, input vocabulary, recovery rules | 🧪 | Automated unit tests; see [tests](tests/README.md) for current coverage |
| Linux probe: find the game's gamescope display, capture frames, send input, detect a focus pause | ✅ | [tools/linux_probe.py](tools/README.md). Ran once against the game on the owner's machine on 2026-10-05, all of L1 to L4. Before that: unit tests, and a dry run in a headless gamescope against a stand-in window |
| Capturing frames from the game under gamescope while its window is in the background | ✅ | Yes, with `gamescopectl screenshot` (2026-10-05, one run): real frames with the game focused, unfocused and covered, 1.1 to 1.3 seconds a frame. Screenshots come at the size of gamescope's output, which need not match the game's own resolution, so templates and click positions need scaling. Capturing the game window over X11 came back black every time (the game renders through Vulkan). PipeWire delivered no frame at all: the connection stayed in format negotiation, for reasons still being investigated. About a frame a second is enough for a loop that only acts on menus; reacting to a fight needs around 10 a second, and of these routes only PipeWire might give that |
| Sending input only to the game under gamescope, without touching the desktop | ✅ | Yes (2026-10-05, one run): Escape sent through XTest to gamescope's nested X server reached the game window and the picture changed (by 89.6, against at most 1.7 at rest before the key), while the terminal on the host desktop received no Escape |
| Whether the game pauses when its window loses focus | ✅ | No (2026-10-05, one run): with the window unfocused the picture moved 132% as much as when focused (ambient motion in town), the game window received no focus events, and the properties on gamescope's root window did not change. The game runs on gamescope's own X server and apparently never sees the host desktop's focus change. On Windows it did pause, and only a focus spoof inside the game process stopped it (two runs, 2026-08-26); going by this run, Linux does not need that spoof. Longer AFK runs still have to confirm it |
| Linux platform layer: finding the game (`gamescope.py`) and input through XTest (`xtest_input.py`) | 🧪 | Automated tests cover the input module's guards (gamescope's nested X server only; the focus and the clicked point on the game window, the point checked again after the pointer moves; configured keys only, none that need Shift or change with Num Lock, none pressed while a modifier or the key itself is held elsewhere or while the keyboard is on another layout; the key and pointer mappings read again before every press; held keys checked again every round; a dry run unless told otherwise) and the loop's capture (a screenshot directory only this user can enter, a new file name for every request, the black bars cut off, a stop that waits neither for the frame nor for a stuck gamescopectl). Not yet run against the game, and not yet in a headless gamescope either. The command line that wires them together is in the next row |
| The app itself: the battle loop (`farm.py`), stop and pause (`control.py`), the command line (`gbfr_auto.py`) | 🧪 | Automated tests cover the loop. The loop stops rather than guess (the game window gone, three captures in a row failing, one screen lasting too long) and pauses after three presses in a row that could not be delivered; once a battle is started, it has the input check the held keys every round and starts the battle again when they were let go; and it lets go of everything on every way out. Stop and pause answer only this user, and one loop runs at a time. Each of these turns its test red when broken on purpose. Not yet run against the game: a dry run comes next (see [Using it](#using-it)). There is no graphical interface; the Windows app's interface and hotkeys are kept in this repository's history |

## The plan

The game runs under Proton inside a nested gamescope session. That may make three of the Windows version's blockers disappear, without porting their workarounds: the game pausing when unfocused ([gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)), the virtual controller's input leaking into other programs ([gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)), and ViGEmBus reaching end of life ([gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)). A [probe](tools/README.md) measures those assumptions:

1. Find the game's gamescope display from the game process's environment.
2. Capture frames while the gamescope window is unfocused or covered.
3. Check whether input sent to gamescope's X server drives the game without touching the desktop.
4. Check whether the game pauses when the gamescope window loses focus.

The first measurements (2026-10-05, once per step) support the inference: no pause when unfocused, XTest input reaches only the game, and gamescopectl captures in the background, so the Linux build excludes the Windows focus spoof and virtual-controller path. Longer AFK runs still have to confirm this.

Stop and pause are GNOME custom shortcuts that run `gbfr_auto.py stop` or `pause`, which tell the running loop over a Unix socket (see [Using it](#using-it)). The reason is that on GNOME's Wayland session, a global key listener only receives keys typed into XWayland windows.

## Using it

The complete farmer is not yet validated against the game (see [Status](#status)), so start with a dry run. The game has to be running under gamescope, started through Steam; the bot never starts or closes it. Set up the environment as described under [Development](#development), then, from the repository directory:

    .venv/bin/python gbfr_auto.py run                          # dry run: recognises screens and logs, sends nothing
    .venv/bin/python gbfr_auto.py run --live                   # sends input to the game
    .venv/bin/python gbfr_auto.py run --live --repeats 10      # stops after ten battles
    .venv/bin/python gbfr_auto.py status                       # what the running loop is doing
    .venv/bin/python gbfr_auto.py pause                        # pause / resume / stop it
    .venv/bin/python gbfr_auto.py release                      # let go of W and the middle button

- **Finding the game.** It looks for the game inside gamescope and refuses to start when there is none, or more than one.
- **Stopping.** Ctrl+C in its terminal, or `gbfr_auto.py stop` from anywhere. On GNOME, bind stop and pause to keys: Settings, Keyboard, View and Customise Shortcuts, Custom Shortcuts, then add one with the command `<repository>/.venv/bin/python <repository>/gbfr_auto.py stop`, and another with `pause`. Only one loop runs at a time.
- **Stopping on its own.** It stops rather than guess: when the game window goes away, when three captures in a row fail, and when one screen lasts longer than its limit (`loop.max_battle_s`, `loop.max_page_s`). It pauses when three presses in a row could not be delivered, for example because a dialog had the focus; `resume` carries on. The exit code is 0 after `--repeats` or a stop, and 1 when it stopped for any other reason. The first failed or blank capture releases held input; three failures still stop the loop. New presses require a fresh usable frame and cleared prior holds. An unconfirmed release pauses the loop for retries. Pause and stop both cancel a pending capture. A failed input transport requires a new bot session after the held inputs are cleared.
- **After a bot crash.** `release` attempts to release every configured key and the middle button. It claims the same session lock before finding the game and refuses an active owner. A skipped or failed release returns exit code 1. Success confirms delivery to X, so check the game's input state as well. Keep game shutdown separate: exit the game through its menu.
- **Files.** `gbfr_auto.toml` (written on the first run: keys, thresholds, limits; the loop refuses to start on a value out of range), `logs/`, and `anomalies/` (frames of unrecognised screens, only with `detect.save_anomaly_frames = true`). Git ignores all three.
- **Old configuration.** Remove the retired `[input]`, `[pad]` and `[inject]` tables from an existing `gbfr_auto.toml`. The program refuses them before finding the game and leaves the file unchanged. Keep `[keys]`, `[loop]`, `[detect]` and `[log]`. `run` defaults to dry-run; only `run --live` enables input. The old `input.dry_run` is not an input safeguard. New files use UTF-8; existing BOM/local-encoding files remain readable.
- **When screens are not recognised.** The templates were cut at a resolution nobody recorded. Run the dry run with `detect.log_scores = true` and `detect.save_anomaly_frames = true`; the scores in the log show how close each template comes, and `detect.template_scale` resizes the templates.

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

| File | What it does |
|---|---|
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
- **Review**: **no human line-by-line review.** The owner sets the direction and runs the tests that need the real game.
- **Verification**:
  - Automated tests on Linux, with Ruff and REUSE checks. See [tests/README.md](tests/README.md) for the current coverage and live-game limitations.
  - Only the probe has run against the game on Linux: on 2026-10-05, once per step; see [Status](#status). The bot itself has not. The tests do not cover capture, input, or anything else that needs the game itself.
- **Risk**: the bot sends input to the game, and step 5 of the probe plan may load a library into the game process. The GPL provides no warranty (GPL-2.0 sections 11 and 12).
- **Copyright**: whether AI-generated output is protected by copyright is legally unsettled. To the extent it is, the licence in [COPYRIGHT](COPYRIGHT) applies. AI output may also resemble its training data.
- **Please do not** submit this code to projects that ban AI-generated contributions (for example Gentoo, NetBSD, QEMU).
- This README is also maintained with AI assistance.

## Notes

- This is an unofficial project, not affiliated with or endorsed by Cygames. Granblue Fantasy: Relink and its artwork belong to Cygames, Inc.

## Licence

GPL-2.0-or-later: version 2 of the GPL or, at your option, any later version. `opencv.py`, `pagetree.py` and `.gitignore` used to contain upstream's GPL-2.0-only code; they were rewritten on 2026-10-05, and upstream's code now exists only in the git history. The licence texts are in [LICENSES/](LICENSES/). The images in `template/` are the game's artwork and are not under the GPL. [COPYRIGHT](COPYRIGHT) says who holds what, and why.
