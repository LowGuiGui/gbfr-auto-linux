<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

[简体中文](README.md) | English

# GBFR Auto for Linux

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **In preparation. The bot does not run against the game yet.**
>
> - This is the Linux successor of the Windows version, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto), which is paused and archived.
> - So far the repository holds the platform-free core carried over from that project, and a probe. The Linux platform layer (capture, input, hotkeys) is not written yet; the probe has made its first run against the game, and the status table below has the results.

> [!WARNING]
> **AI-written code.** This repository was written by an AI coding assistant (Anthropic's Claude Code) at the owner's direction, and has **not been reviewed line by line by a human**.
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
| Platform-free core: template matching, page tree, config, logging, input vocabulary, recovery rules | 🧪 | 180 automated tests, run on Linux in CI |
| Linux probe: find the game's gamescope display, capture frames, send input, detect a focus pause | ✅ | [tools/linux_probe.py](tools/README.md). Ran once against the game on the owner's machine on 2026-10-05, all of L1 to L4. Before that: unit tests, and a dry run in a headless gamescope against a stand-in window |
| Capturing frames from the game under gamescope while its window is in the background | ✅ | Yes, with `gamescopectl screenshot` (2026-10-05, one run): real frames with the game focused, unfocused and covered, 1.1 to 1.3 seconds a frame. Screenshots come at the size of gamescope's output, which need not match the game's own resolution, so templates and click positions need scaling. Capturing the game window over X11 came back black every time (the game renders through Vulkan). PipeWire delivered no frame at all: the connection stayed in format negotiation, for reasons still being investigated. About a frame a second is enough for a loop that only acts on menus; reacting to a fight needs around 10 a second, and of these routes only PipeWire might give that |
| Sending input only to the game under gamescope, without touching the desktop | ✅ | Yes (2026-10-05, one run): Escape sent through XTest to gamescope's nested X server reached the game window and the picture changed (by 89.6, against at most 1.7 at rest before the key), while the terminal on the host desktop received no Escape |
| Whether the game pauses when its window loses focus | ✅ | No (2026-10-05, one run): with the window unfocused the picture moved 132% as much as when focused (ambient motion in town), the game window received no focus events, and the properties on gamescope's root window did not change. The game runs on gamescope's own X server and apparently never sees the host desktop's focus change. On Windows it did pause, and only a focus spoof inside the game process stopped it (two runs, 2026-08-26); going by this run, Linux does not need that spoof. Longer AFK runs still have to confirm it |
| Linux platform layer: finding the game (`gamescope.py`) and input through XTest (`xtest_input.py`) | 🧪 | 141 automated tests. Each of the input module's guards (gamescope's nested X server only; the focus and the clicked point on the game window, the point checked again after the pointer moves; configured keys only, none that need Shift or change with Num Lock, none pressed while a modifier or the key itself is held elsewhere or while the keyboard is on another layout; the key and pointer mappings read again before every press; held keys checked again every round; a dry run unless told otherwise) turns its test red when broken on purpose. Not yet run against the game, and not yet in a headless gamescope either. Frame capture for the loop comes next |
| The app itself: battle loop, user interface, hotkeys | ⏳ | The Windows app layer is kept in this repository's history. The Linux platform layer is under way (row above); the battle loop and its stop and pause commands follow it |

## The plan

The game runs under Proton inside a nested gamescope session. That may make three of the Windows version's blockers disappear, without porting their workarounds: the game pausing when unfocused ([gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)), the virtual controller's input leaking into other programs ([gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)), and ViGEmBus reaching end of life ([gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)). That is an inference, so a [probe](tools/README.md) measures it before any platform code is written:

1. Find the game's gamescope display from the game process's environment.
2. Capture frames while the gamescope window is unfocused or covered.
3. Check whether input sent to gamescope's X server drives the game without touching the desktop.
4. Check whether the game pauses when the gamescope window loses focus.
5. Only if step 4 finds a pause: check whether the old focus spoof, built as an `.asi` for Reloaded-II's ASI loader, still works under Proton.

The first measurements (2026-10-05, once per step) support the inference: no pause when unfocused, XTest input reaches only the game, and gamescopectl captures in the background, so step 5 is not needed for now. Longer AFK runs still have to confirm this.

Hotkeys are planned as GNOME custom shortcuts that call a small command-line tool, which tells the bot over a Unix socket. The reason is that on GNOME's Wayland session, a global key listener only receives keys typed into XWayland windows.

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
| `geometry.py` | window, client-area and centre arithmetic |
| `backend.py` | the input vocabulary (move, battle, again, confirm, release all), implemented for keyboard/mouse and for a controller |
| `xusb.py` | the Xbox controller report format and button bits |
| `supervisor.py` | the pure decision of when to switch input, pause or recover |
| `config.py` | the TOML configuration |
| `applog.py` | logging |
| `gamescope.py` | finding the game's gamescope (its processes, nested X display and game window), and capturing frames with `gamescopectl`; the probe and the platform layer use it |
| `xtest_input.py` | keyboard and mouse input for the game through XTest on gamescope's nested X server: only to the game's window, only the configured keys, and a dry run unless told otherwise |
| `template/` | the reference images matched against the screen |
| `tools/linux_probe.py` | the Linux probe; see [tools/README.md](tools/README.md) |
| `docs/provenance/` | how this repository's history was produced from the archive |

## History

This repository's history begins with the Windows version's. Its first 84 commits were filtered mechanically out of the archive; [docs/provenance](docs/provenance/README.md) records how, maps old commits to new ones, and explains how to bring back the app layer kept in history.

Issue numbers in the older commit messages read `gbfr_auto#NN`, and those in code comments are bare (such as `#45`). Both refer to the [archive's issues](https://github.com/LowGuiGui/gbfr_auto/issues), not this repository's.

## About the AI-written code

- **Tool**: Anthropic's Claude Code (Claude Opus 5 / 5.5 models), working at the owner's direction.
- **Scope**:
  - Everything except upstream's original code: the fork's changes carried over from the archive (2026-08-23 to 2026-10-02), and everything done in this repository since.
  - Every non-merge commit carries a `Co-Authored-By: Claude` trailer, except upstream's 6 commits and the 3 initial setup commits of 2026-08-23 (ruff and pre-commit config, pinned requirements, restored LICENSE).
- **Review**: **no human line-by-line review.** The owner sets the direction and runs the tests that need the real game.
- **Verification**:
  - 432 automated tests on Linux (180 for the core, 141 for the Linux platform layer, 111 for the probe), run in CI on every pull request.
  - Only the probe has run against the game on Linux: on 2026-10-05, once per step; see [Status](#status). The bot itself has not. The tests do not cover capture, input, or anything else that needs the game itself.
- **Risk**: the bot sends input to the game, and step 5 of the probe plan may load a library into the game process. The GPL provides no warranty (GPL-2.0 sections 11 and 12).
- **Copyright**: whether AI-generated output is protected by copyright is legally unsettled. To the extent it is, the licence in [COPYRIGHT](COPYRIGHT) applies. AI output may also resemble its training data.
- **Please do not** submit this code to projects that ban AI-generated contributions (for example Gentoo, NetBSD, QEMU).
- This README was drafted by the same AI.

## Notes

- This is an unofficial project, not affiliated with or endorsed by Cygames. Granblue Fantasy: Relink and its artwork belong to Cygames, Inc.

## Licence

GPL-2.0-or-later: version 2 of the GPL or, at your option, any later version. `opencv.py`, `pagetree.py` and `.gitignore` used to contain upstream's GPL-2.0-only code; they were rewritten on 2026-10-05, and upstream's code now exists only in the git history. The licence texts are in [LICENSES/](LICENSES/). The images in `template/` are the game's artwork and are not under the GPL. [COPYRIGHT](COPYRIGHT) says who holds what, and why.
