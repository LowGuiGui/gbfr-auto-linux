# tests

Runs on **Linux**, against a Windows-only application. `conftest.py` stubs the
platform boundary — `win32*`, `pyautogui`, `pynput`, `ctypes.windll` — at import
time, because `main.py`, `window_capture.py` and `window_input.py` all touch
Windows at *module* level.

Only the boundary is stubbed. The code under test is the real code.

    pytest

## What is covered

| File | Covers |
|---|---|
| `test_opencv.py` | template matching, every read-failure path, resolution sensitivity (#12), and the NCC degeneracy that makes a black frame score 1.0 |
| `test_applog.py` | log destination and fallback, idempotent setup, no propagation to root, tracebacks |
| `test_hotkeys.py` | #13 — the listener must survive any exception, and must not flood the log |
| `test_page_dispatch.py` | #14 — blind-tap cap, recovery, and that recognised pages are untouched |
| `test_template_refresh.py` | #3 — refresh untouched files, never clobber edited ones, source-checkout no-op |

## What is NOT covered, and why

- **Win32 calls** — `PrintWindow`, `GetWindowRect`, DLL injection, the named
  pipe. Stubbing them would test the stub. These need a Windows runner with the
  game, and `PLANNING.md` §5 lists what to measure.
- **Tk rendering.** `FakeRoot` covers the marshalling contract (`after()` is the
  only thread-safe entry point); it does not cover widgets.
- **Anything requiring the game.** Detection accuracy against real screenshots
  is a fixture problem — see `PLANNING.md` on anomaly frame capture, which is
  how that corpus gets collected.

## Conventions

- **Never use flat colour fixtures for matching tests.** `TM_CCOEFF_NORMED`
  divides by variance; flat against flat returns a perfect 1.0. Use
  `_texture()`. `TestUniformRegionsAreDegenerate` pins that behaviour down
  deliberately.
- Tests that assert a *current defect* rather than desired behaviour say so in
  the docstring — `TestResolutionSensitivity` should flip to passing-as-found
  when multi-scale matching lands.
