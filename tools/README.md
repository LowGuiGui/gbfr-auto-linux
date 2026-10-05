<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

# tools

## linux_probe.py

Measures how the game behaves under gamescope, before any platform code is written. It asks
four questions, and each is answered by a measurement that can come out either way:

| Step | Question | How |
|---|---|---|
| L1 | Where is the game? | Reads `/proc` for processes that carry the game's Steam app id and were started by gamescope, and takes the nested `DISPLAY` and `GAMESCOPE_WAYLAND_DISPLAY` from their environment. Connects to that X display and finds the window gamescope marks as the game. Also finds this gamescope's video node in PipeWire with `pw-dump`: gamescope publishes its output as a node named `gamescope`, and the probe keeps the one owned by an ancestor of the game's processes. A node no ancestor owns is never used, even when it is the only one, because it may belong to another gamescope. Read-only; the stream is not opened. |
| L2 | Can frames be captured while the game is in the background? | X11 `GetImage` of the root window and of the game window, `gamescopectl screenshot`, and one frame from gamescope's PipeWire node through `gst-launch-1.0`, each with the game focused, unfocused, and covered. A black or flat frame counts as a failure (`opencv.is_blank_frame`). For PipeWire it also records whether the node survived each capture, and when no frame arrives within 5 seconds it records the state of the link (for example `negotiating`) instead of waiting. In each pass it then streams from the node for 3 seconds and reports frames per second, the longest gap between frames and the negotiated format: a loop that reacts to the fight would need 10 or more frames a second in the background. |
| L3 | Does input sent through XTest reach the game, and only the game? | Sends Escape twice to the nested X server, after asking. Checks whether the X server delivered the key to the game's window, whether the picture changed, and whether the key also reached this terminal on the host. |
| L4 | Does the game pause when its window loses focus? | Captures a series of frames while the game is focused and another while it is not, and compares the motion with the logic of the Windows probe's A4 test (`framediff`). Also records the focus events the game window receives. |

L5, the focus spoof built as an `.asi`, only matters if L4 finds a pause. It is not part of
this tool. Its source is parked in history; `git restore --source=1db218a --
hook/gbfr_hook.c` brings it back.

### Running it

The game must already be running under gamescope, launched from Steam as usual. From a
terminal placed beside the game window:

    .venv/bin/python tools/linux_probe.py

To run only some steps, pass for example `--steps L1,L2`. L1 always runs, because every other
step needs what it finds. Asking for L3 or L4 runs L2 as well, because both use the capture
method L2 picks. L3 and L4 prefer X11, then `gamescopectl`, and use PipeWire only when neither
of those captured anything in the background.

The PipeWire method needs `pw-dump` and `gst-launch-1.0` with `pipewiresrc` and `pngenc`. On
Ubuntu those come from `pipewire-bin`, `gstreamer1.0-tools`, `gstreamer1.0-pipewire` and
`gstreamer1.0-plugins-good`. Without them the method is skipped or reports an error, and the
other methods run as before.

The probe prompts you through window switches. Switch with Alt+Tab rather than by clicking
inside the game, because a click is an in-game action. L3 asks before sending anything; run it
on a screen where Escape is harmless, such as in town. L4 needs a scene with continuous
motion.

Results go to `probe-runs/<timestamp>/`, which git ignores: `report.md` to read,
`report.jsonl` for scripts, the captured PNGs, and the output of `gamescopectl help`. The
report is written as the probe goes, so a crash keeps everything up to that point.

### What it never does

- start, stop or close the game
- focus, map, move or raise any window
- send any key except Escape, and never a Super combination, since those are gamescope's own
  shortcuts
- run `gamescopectl` without naming the game's gamescope instance, which could reach another
  game's instance instead
- open any PipeWire video source other than the game's gamescope node. The stream targets the
  node's `object.serial`, which PipeWire never reuses (object ids are reused), and sets
  `node.dont-fallback`, so if the node has gone, WirePlumber answers with an error instead of
  connecting the default video source, which could be a camera
- change Steam, gamescope or Reloaded-II settings

### What has been checked

- Unit tests (`tests/test_linux_probe.py`) cover finding the game in `/proc`, reading X
  images, the `gamescopectl` round trip, the key allow-list, the verdicts and the report.
  Breaking each of those guards on purpose turns its test red.
- Finding the PipeWire node is covered by unit tests only. The fake `pw-dump` output follows
  the structure PipeWire 1.6.2 prints (Client and Node objects, the owning process ids in the
  client's properties), and walking up the process tree is also tested against the real
  `/proc`. It has not been seen with the game.
- The PipeWire capture is covered by unit tests with a stand-in for `gst-launch-1.0`, plus
  real child processes for the time limit: a command that hangs is interrupted, and one that
  ignores the interrupt is killed. A test parses the command line with GStreamer's own
  parser, without starting it. That `node.dont-fallback` stops WirePlumber from connecting
  another video source comes from reading WirePlumber 0.5.13's source; it has not been
  triggered here. Not checked: getting a frame from a real gamescope over PipeWire.
- Targeting by serial was tried once on this machine (PipeWire 1.6.2, WirePlumber 0.5.13)
  against GStreamer's test pattern published as a PipeWire video source: it delivered frames,
  as targeting by id did.
- The frame rate is read from the per-frame lines that `gst-launch-1.0 -v` prints. A test
  runs GStreamer's own test pattern at 30 frames a second through the same sink and expects
  28 to 32. It skips where `gst-launch-1.0` is not installed.
- A dry run against a real gamescope 3.16.20 in headless mode, with a stand-in X11 window
  marked as the game. Discovery, capturing the window over X11, and `gamescopectl screenshot`
  (about 0.4 s a frame) all worked. Escape sent through XTest reached the stand-in, which
  logged both presses, and the probe saw them delivered. Capturing the root window fails with
  `BadMatch`, as expected for a rootless Xwayland.
- Not checked: anything about the game itself. The stand-in draws with plain X11 while the
  game renders through Vulkan, so the game's frames may still come back black over X11.
  Measuring that is what L2 is for.
