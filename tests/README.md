<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

# tests

Runs on **Linux**, with nothing stubbed. The core modules have no platform
dependencies, so `conftest.py` only puts the repo root on `sys.path` and provides
the `log_file` fixture. The probe's tests use small doubles for the X display and for
`subprocess.run`, written against python-xlib's and subprocess's documented signatures;
`test_gamescope.py` defines the `/proc` and `subprocess.run` doubles, and the probe's tests
import them from there. `test_xtest_input.py` has its own double for the nested X server,
written against python-xlib 0.33's source, including its key lookups answering from the
mapping cached when the connection opened.

    pytest

Issue numbers here and in the code, such as #12 or #46, predate this repository
and refer to the archive, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto).

## What is covered

| File | Covers |
|---|---|
| `test_opencv.py` | template matching, which inputs it takes and which it refuses (PIL images, alpha, grayscale, a template larger than the screen), every read-failure path, resolution sensitivity (#12), and the NCC degeneracy that makes a black frame score 1.0 |
| `test_diagnostics.py` | the blank-frame guard (judged per colour channel), and that the best match always reports its score |
| `test_framediff.py` | frame differences and the motion and input verdicts built on them (the Windows probe's A4 logic) |
| `test_pages.py` | the page-tree engine, and the real tree in `pagetree.py` checked against its own tables and the files in `template/` |
| `test_geometry.py` | client-area offsets, borders and centre, with the numbers measured for #46 |
| `test_supervisor.py` | `decide()`: when to switch, degrade, pause or recover, and the physical-pad watch |
| `test_backend.py` | the intent vocabulary for keyboard/mouse and pad, and that `release_all` keeps a switch from stranding a held key |
| `test_xusb.py` | the XUSB report layout and button bits |
| `test_applog.py` | log destination and fallback, idempotent setup, no propagation to root, tracebacks |
| `test_config.py` | config load/merge/validate, and that `DEFAULT_TOML` and `DEFAULTS` haven't drifted |
| `test_gamescope.py` | `gamescope.py`: finding the game's processes in `/proc` and grouping them by their gamescope (including Steam's runtime container), the process lineage, recognising a gamescope process, and the `gamescopectl` round trip |
| `test_xtest_input.py` | `xtest_input.py`: refusing a display that is not gamescope's nested X server, keys outside the config, modifier keys, keys that need Shift and keypad keys Num Lock would change, presses while the focus is off the game, a button press where another window covers the point, the middle button found through the pointer mapping, presses held back while a modifier or the key itself is down elsewhere, presses whose send failed still released, key and pointer mappings that change at runtime, the point checked again after the pointer moves, the per-round check of held keys (focus, modifiers, the layout, mappings, keys let go elsewhere), a dead connection marking it unready while a vanished window is only a skip, a key held in Num Lock's modifier group, lock keys recognised from the current keymap (Num Lock moved to another key, a former lock key turned level shift, Caps Lock held as an extra Ctrl, Shift Lock never exempt), no key sent while the keyboard is on another layout, a tap whose release failed let go by the next check, the dry run, holds and releases (also when the focus has moved), an emergency release that leaves alone a key whose keycode now means something else, coordinates translated to the root window, a lost connection, and `KmbBackend` driving it |
| `test_linux_probe.py` | the Linux probe's instruments: reading X images, the PipeWire node and capture, the key allow-list, the verdicts and the incremental report |

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
