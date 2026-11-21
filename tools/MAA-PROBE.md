<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Maa compatibility probe

This optional diagnostic uses MaaFw 5.16.1 on Python 3.13. It does not replace the
existing farmer. A successful library import or API response is not proof of game
compatibility. The real-game command only observes an already-running game.

## Installation

Use a separate checkout and environment for the trial:

```sh
uv venv --python 3.13 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-maa.lock
```

The lock includes the existing development dependencies so that the repository's
pytest, Ruff and REUSE checks run in the same environment. No system packages are
installed by these commands. Gamescope, PipeWire, `pw-dump`, and libei must already
be available. The comparison diagnostic also uses `gamescopectl`, GStreamer and
the `pipewiresrc` / PNG plugins documented in [the original probe guide](README.md).

## Isolated integration trial

```sh
.venv/bin/python -B tools/maa_headless.py --seconds 10
```

The launcher creates a headless Gamescope with a distinct test app identity and an
animated X11 receiver. It checks the actual receiver's process ancestry and private
test token before enabling Maa input. It records received key/button events and
key state, tests target loss, and terminates only the processes it created.

The report distinguishes these measurements:

- Native capture with input disabled, including frame dimensions and timing.
- The required keys and explicit held-key release. If capture fails, an input-only
  trial isolates keyboard delivery; it does not validate the combined controller
  or mouse path.
- A native `KeyDown` pipeline with `auto_up: true`, stopped during a pending capture.
  Require a fresh observed hold before stop, all keys/buttons clear within one
  second, and continuously clear receiver samples for at least 1.2 seconds.
  Sample timestamps and frame counters must advance with gaps at most 250 ms;
  atomic snapshots prevent partial reads. Missing/stale state, missing matching
  ups, or any press after release cannot pass. The shared measurement also handles
  middle-button and combined holds. An adjacent X11 key up/down pair with the same
  server timestamp can be recognized as auto-repeat before the final release;
  that pair alone never passes, and its timing is reported separately. The
  receiver drains events queued by synchronous state queries before publishing
  each sample, preserving observation order. This is sampled X state, not proof
  of unobserved intervals. Release latency comes from the first clear
  sample; event timing and asynchronous stop-job completion are reported separately.
  If capture finishes before stop, `release_passed` still reports key cleanup,
  but `passed` stays false because the pending-capture case was not exercised.
- On capture failure, comparison with `gamescopectl` and GStreamer on the same
  owned source. These are diagnostics, not automatic alternative transports.

The launcher returns 1 while the full compatibility gate remains unmet. Inspect
each result; a completed report is not a passing compatibility verdict. Headless
results cannot establish real-game rendering, host input isolation, physical-key
interaction, or sustained reliability.

## Owner-run game capture

Start GBFR through Steam as usual and leave it in a scene with visible animation.
Stop any existing farmer. The probe shares its ownership lock and will refuse a
competing session. Run the focused test first:

```sh
.venv/bin/python -B tools/maa_probe.py game-capture --visibility focused
```

There is a five-second setup interval. During that interval, focus the game using
Alt+Tab, then leave the scene animating. The measurement window is 60 seconds;
connection failure or a failed frame may end it sooner. If it fails, stop and
inspect the report before doing more tests.

If the first test captures usable frames, repeat in the other visibility modes:

```sh
.venv/bin/python -B tools/maa_probe.py game-capture --visibility unfocused
.venv/bin/python -B tools/maa_probe.py game-capture --visibility covered
```

For `unfocused`, leave the game visible while another window has focus. For
`covered`, cover the game with another window. Arrange the windows yourself during
the setup interval; the tool does not move or focus them. The visibility label
records the condition you arranged; it is not an independently verified focus check.
Ctrl+C stops the probe. The existing `stop` and `pause` commands also cancel this
observation session; resuming a completed diagnostic requires a new invocation.

`game-capture` configures Maa with **input method 0 (None)**. It never submits input,
including on cleanup, and has no live-input option. Input trials against GBFR are
deferred until capture and the remaining safeguards have been reviewed.

## Reports and limits

Reports live in the checkout's ignored `probe-runs/maa-*/` directories, created
with private permissions. They contain local process/socket identifiers, native
logs, timing data, and up to three PNG samples per capture trial. Keep them local.
Native workers have a process deadline so a blocked library cannot keep a capture
probe running indefinitely. Worker termination is not proof of held-input cleanup.

Capture reports distinguish usable images, unique image hashes and latency. A
changed hash can demonstrate image change in an animated scene; an unchanged hash
on a static scene does not prove staleness. Maa's native capture can return a cached
frame on timeout, and the binding does not provide producer timestamps here, so
freshness is explicitly unverified. Inspect the images and native logs as well as
the reported rate.

The full adoption gate still requires sustained real-game capture in all three
modes, mouse/hold behavior, physical input and layout checks, desktop isolation,
disconnect cleanup and repeated control-latency measurements. Do not infer those
results from the keyboard-only trial.

API and protocol references:
[Linux controllers](https://github.com/MaaXYZ/MaaFramework/blob/v5.16.1/docs/en_us/2.4-ControlMethods.md),
[pipeline auto-release](https://github.com/MaaXYZ/MaaFramework/blob/v5.16.1/docs/en_us/3.1-PipelineProtocol.md),
[Python bindings](https://github.com/MaaXYZ/MaaFramework/tree/v5.16.1/source/binding/Python/maa).
