<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

# tests

Runs on **Linux**, with nothing stubbed. The core modules have no platform
dependencies, so `conftest.py` only puts the repo root on `sys.path` and provides
the `log_file` fixture.

    pytest

Issue numbers here and in the code, such as #12 or #46, predate this repository
and refer to the archive, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto).

## What is covered

| File | Covers |
|---|---|
| `test_opencv.py` | template matching, every read-failure path, resolution sensitivity (#12), and the NCC degeneracy that makes a black frame score 1.0 |
| `test_diagnostics.py` | the blank-frame guard, and that the best match always reports its score |
| `test_framediff.py` | frame differences and the motion and input verdicts built on them (the Windows probe's A4 logic) |
| `test_pages.py` | the page-tree engine, and the real tree in `pagetree.py` checked against its own tables |
| `test_geometry.py` | client-area offsets, borders and centre, with the numbers measured for #46 |
| `test_supervisor.py` | `decide()`: when to switch, degrade, pause or recover, and the physical-pad watch |
| `test_backend.py` | the intent vocabulary for keyboard/mouse and pad, and that `release_all` keeps a switch from stranding a held key |
| `test_xusb.py` | the XUSB report layout and button bits |
| `test_applog.py` | log destination and fallback, idempotent setup, no propagation to root, tracebacks |
| `test_config.py` | config load/merge/validate, and that `DEFAULT_TOML` and `DEFAULTS` haven't drifted |

## What is NOT covered, and why

- **The app layer.** `main.py`, `option.py` and their tests (page dispatch,
  template refresh, option keys and modes, hotkeys, score logging, anomaly
  frames, battle counting) are parked in history until the Linux port needs
  them. `docs/provenance/README.md` says how to bring them back.
- **Anything requiring the game.** Capture, input and detection accuracy against
  real frames need the game running under gamescope; the Linux probe measures
  those first.

## Conventions

- **Never use flat colour fixtures for matching tests.** `TM_CCOEFF_NORMED`
  divides by variance; flat against flat returns a perfect 1.0. Use
  `_texture()`. `TestUniformRegionsAreDegenerate` pins that behaviour down
  deliberately.
- Tests that assert a *current defect* rather than desired behaviour say so in
  the docstring — `TestResolutionSensitivity` should flip to passing-as-found
  when multi-scale matching lands.
