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
mapping cached when the connection opened. `test_gbfr_auto.py` reuses that double and `test_farm.py`'s
frames, with a real control socket in a temporary directory. `test_gamescope.py` and
`test_gbfr_auto.py` fail any test that would reach the real nested X server or start a real
process: `gamescopectl` is installed here, and the owner's game may be running.

    pytest

Issue numbers here and in the code, such as #12 or #46, predate this repository
and refer to the archive, [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto).

## What is covered

| File | Covers |
|---|---|
| `test_opencv.py` | template matching, which inputs it takes and which it refuses (PIL images, alpha, grayscale, a template larger than the screen), every read-failure path, resolution sensitivity (#12), and the NCC degeneracy that makes a black frame score 1.0 |
| `test_diagnostics.py` | the blank-frame guard (judged per colour channel), and that the best match always reports its score |
| `test_framediff.py` | frame differences and the motion and input verdicts built on them (also used by the Linux probe) |
| `test_pages.py` | the page-tree engine, and the real tree in `pagetree.py` checked against its own tables and the files in `template/` |
| `test_supervisor.py` | one Linux target: continue when ready, stop on window loss, replacement or input loss |
| `test_backend.py` | Linux keyboard/mouse intents, fresh coordinates, and idempotent held-input release |
| `test_applog.py` | log destination and fallback, idempotent setup, no propagation to root, tracebacks |
| `test_config.py` | config load/merge/validate, explicit rejection of retired input/pad/inject settings, UTF-8 output with legacy encoding reads, and template/default parity |
| `test_gamescope.py` | `gamescope.py`: finding the game's processes in `/proc` and grouping them by their gamescope (including Steam's runtime container), the process lineage, recognising a gamescope process, the focus check, the `gamescopectl` round trip, and the loop's capture: the private screenshot directory, a new file for every request with earlier leftovers cleared, cutting gamescope's black bars and scaling back to the game's size, and giving up on a frame when told to stop, also while `gamescopectl` hangs. A guard fails any of these tests that would start a real process |
| `test_xtest_input.py` | `xtest_input.py`: refusing a display that is not gamescope's nested X server, keys outside the config, modifier keys, keys that need Shift and keypad keys Num Lock would change, presses while the focus is off the game, a button press where another window covers the point, the middle button found through the pointer mapping, presses held back while a modifier or the key itself is down elsewhere, presses whose send failed still released, key and pointer mappings that change at runtime (a keypad key remapped so that Num Lock changes it included), the point checked again after the pointer moves, the per-round check of held keys (focus, modifiers, the layout, mappings, keys let go elsewhere), a dead connection marking it unready while a vanished window is only a skip, a key held or latched in Num Lock's modifier group, lock keys recognised from the current keymap (Num Lock moved to another key, a former lock key turned level shift, Caps Lock held as an extra Ctrl, Shift Lock never exempt), no key sent while the keyboard is on another layout, a tap whose release failed let go by the next check, the dry run, holds and releases (also when the focus has moved), an emergency release that leaves alone a key whose keycode now means something else, coordinates translated to the root window, a lost connection, and `KmbBackend` driving it |
| `test_farm.py` | `farm.py`: every page told apart, the battle started once and let go before any other action, a half-landed start retried and let go, a battle whose held keys were let go started again, capped blind confirms, counting and repeats, every reason to stop (window gone or replaced, input lost, failed captures, a screen lasting too long, stop and pause arriving mid-round, the page limit not turning a late pause into a stop), pausing (paused time not counted, missed presses counted afresh after a resume, a failed release retried while paused), the anomaly-frame cap across runs, score logging for every template, refusing bad arguments and out-of-range settings, the template loader's size and flatness checks, and letting go on every way out |
| `test_control.py` | `control.py`, on real sockets: commands and status, the socket in a private directory and set to 0600, another uid getting no answer, one loop at a time (the lock, a listener without it, a stale socket replaced, foreign files left alone), a silent client not holding up a stop, a failing status contained, passing `accept()` errors retried, `close()` against a replacement with a recycled inode, an unstarted worker, a file that cannot be removed and a failing startup log, and `wait` waking on stop, pause and resume without missing one |
| `test_gbfr_auto.py` | `gbfr_auto.py`: a dry run sending nothing, a live run letting go at the end, Ctrl+C and `stop` reaching the loop, a second loop refused before it looks for the game, refusing to start without the game, on a display that is not gamescope's or with a setting out of range, X errors at startup reported instead of a traceback, retired configuration refused before target discovery, the exit codes, and the stop, pause, resume, status and release commands |
| `test_linux_probe.py` | the Linux probe's instruments: reading X images, the PipeWire node and capture, the key allow-list, the verdicts and the incremental report |

## What is NOT covered, and why

- **The app layer.** `main.py`, `option.py` and their tests (page dispatch,
  template refresh, option keys and modes, hotkeys, score logging, anomaly
  frames, battle counting), the XUSB backend, and Windows geometry helpers
  are historical code. The Linux build uses the modules listed above;
  `docs/provenance/README.md` records their provenance.
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
