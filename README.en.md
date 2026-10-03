<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

[简体中文](README.md) | English

# GBFR Auto for Linux

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **In preparation. Nothing here runs against the game yet.**
>
> - This is the Linux successor of the Windows version, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto), which is paused and archived.
> - So far the repository holds the platform-free core carried over from that project. The Linux platform layer (capture, input, hotkeys) is not written yet: a probe first measures how the game behaves under gamescope.

> [!WARNING]
> **AI-written code.** This repository was written by an AI coding assistant (Anthropic's Claude Code) at the owner's direction, and has **not been reviewed line by line by a human**.
>
> - None of it has run against the game on Linux yet; the core has automated tests only.
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
| Platform-free core: template matching, page tree, config, logging, input vocabulary, recovery rules | 🧪 | 173 automated tests, run on Linux in CI |
| Linux probe: find the game's gamescope display, capture frames, send input, detect a focus pause | 🧪 | Written ([tools/linux_probe.py](tools/README.md)). Besides its unit tests, it was dry-run in a headless gamescope against a stand-in window. It has not been run against the game yet |
| Capturing frames from the game under gamescope while its window is in the background | ❓ | Not measured on Linux |
| Sending input only to the game under gamescope, without touching the desktop | ❓ | Not measured on Linux |
| Whether the game pauses when its window loses focus | ❓ | On Windows it did, and a focus spoof inside the game process stopped it (two runs, 2026-08-26). Under gamescope the game runs on gamescope's own X server, so it may never notice the host desktop's focus change. Not measured on Linux |
| The app itself: battle loop, user interface, hotkeys | ⏳ | The Windows app layer is kept in this repository's history and will be ported once the probe settles how |

## The plan

The game runs under Proton inside a nested gamescope session. That may make three of the Windows version's blockers disappear, without porting their workarounds: the game pausing when unfocused ([gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)), the virtual controller's input leaking into other programs ([gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)), and ViGEmBus reaching end of life ([gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)). That is an inference, so a [probe](tools/README.md) measures it before any platform code is written:

1. Find the game's gamescope display from the game process's environment.
2. Capture frames while the gamescope window is unfocused or covered.
3. Check whether input sent to gamescope's X server drives the game without touching the desktop.
4. Check whether the game pauses when the gamescope window loses focus.
5. Only if step 4 finds a pause: check whether the old focus spoof, built as an `.asi` for Reloaded-II's ASI loader, still works under Proton.

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
  - 238 automated tests on Linux (173 for the core, 65 for the probe), run in CI on every pull request.
  - Nothing has run against the game on Linux yet. The tests do not cover capture, input, or anything else that needs the game itself.
- **Risk**: the bot sends input to the game, and step 5 of the probe plan may load a library into the game process. The GPL provides no warranty (GPL-2.0 sections 11 and 12).
- **Copyright**: whether AI-generated output is protected by copyright is legally unsettled. To the extent it is, the licence in [COPYRIGHT](COPYRIGHT) applies. AI output may also resemble its training data.
- **Please do not** submit this code to projects that ban AI-generated contributions (for example Gentoo, NetBSD, QEMU).
- This README was drafted by the same AI.

## Notes

- This tool is for learning purposes only; do not use it commercially.
- This is an unofficial project, not affiliated with or endorsed by Cygames. Granblue Fantasy: Relink and its artwork belong to Cygames, Inc.

## Licence

GPL-2.0-or-later; the full text is in [LICENSES/GPL-2.0-or-later.txt](LICENSES/GPL-2.0-or-later.txt). Upstream's work belongs to its author, the images in `template/` are the game's artwork and are not under the GPL, and [COPYRIGHT](COPYRIGHT) says who holds what.
