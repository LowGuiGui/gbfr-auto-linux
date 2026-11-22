#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Measure the game's own Repeat Quest with a short, fixed plan of key presses.

One process holds the session lock and one native connection. It captures about
three frames a second, classifies each with the rc.2 recognition nodes, saves one
frame a second for offline analysis, and presses only these keys:

- `3` on the results page that shows the repeat button, when the button's state
  differs from the plan (on until --off-at or --max-runs, then off); the new state
  is read back, with one retry;
- `Escape` once, on the --auto-skip-at'th loading-tips screen (the AUTO SKIP option);
- `Return` on a loading-tips screen that is still waiting after --tips-wait seconds;
- with --tenth-answer yes, `Up` on the tenth-run dialog and, once a fresh frame shows
  the cursor on 是, `Return`;
- with --hold-w, `w` held in bounded pulses (--hold-ms) while a fresh frame shows the
  battle HUD, on the chosen runs only, to compare battle times with and without it.

Screens are named by the current resource's `GBFR_See_*` leaves. A failure screen
ends the run without input, so the failure can be studied by hand. An unrecognised
screen never gets input. The run ends when town has been stable for a
few seconds, when any screen stalls past its limit, on a stop request
(`gbfr_auto.py stop`), on an identity or input-safety failure, or at --hours.
`--replay` runs the same decisions over a saved capture with no input at all.
"""

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEYS = {"3": 4, "Return": 28, "Escape": 1, "Up": 103, "w": 17}
MAX_HOLD_MS = 5000
REPEAT_ROI = [45, 637, 265, 30]
COUNTDOWN_ROI = [900, 662, 185, 35]
CENTER_ROI = [160, 120, 960, 480]
# Order matters: the tenth-run dialog covers a results page.
SCREEN_NODES = (("tenth", "GBFR_See_TenthRun"), ("defeat", "GBFR_See_Defeat"),
                ("results", "GBFR_See_Results"), ("victory", "GBFR_See_Victory"),
                ("tips", "GBFR_See_Tips"), ("town", "GBFR_See_Town"),
                ("battle", "GBFR_See_Battle"), ("confirm", "GBFR_See_SettlementConfirm"))
AUTO_SKIP_ROI = [70, 645, 130, 30]


@dataclass(frozen=True)
class Observation:
    time: float
    screen: str                 # one of SCREEN_NODES' names, "loading", or "unknown"
    repeat: str | None = None   # "on" / "off" on the results page with the button
    choice: str | None = None   # "yes" / "no": the tenth-run dialog's highlighted option
    auto_skip: str | None = None  # "on" / "off": the loading screen's AUTO SKIP label


def dialog_choice(frame):
    """Which option of the tenth-run dialog carries the cursor's glow (1280x720 frame).

    The glow spans the dialog's width, so it is read beside the centred text: about
    80 for the plain row and 107-113 for the highlighted one on recorded frames.
    """
    import cv2
    import numpy as np

    value = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 2].astype(float)

    def band(y0, y1):
        return np.concatenate([value[y0:y1, 340:560].ravel(), value[y0:y1, 720:940].ravel()]).mean()
    yes, no = band(440, 462), band(474, 496)
    if yes - no > 12:
        return "yes"
    if no - yes > 12:
        return "no"
    return None


def auto_skip_state(frame):
    """The loading screen's `AUTO SKIP` label is white when off and blue when on."""
    roi = frame[650:670, 98:178].astype(int)
    b, g, r = roi[..., 0], roi[..., 1], roi[..., 2]
    blue = int(((b > 180) & (b - r > 50)).sum())
    white = int(((b > 200) & (g > 200) & (r > 200)).sum())
    if blue > 200 and blue > white:
        return "on"
    if white > 150 and blue < 50:
        return "off"
    return None


@dataclass(frozen=True)
class Settings:
    off_at: int | None = None   # turn repeat off on this run's results page
    max_runs: int = 12          # turn repeat off on this run's results page at the latest
    tips_wait: float = 15.0
    auto_skip_at: int = 2       # 0 leaves AUTO SKIP alone
    verify_seconds: float = 2.5
    max_tries: int = 2
    max_presses: int = 40
    town_settle: float = 5.0
    reveal: float = 0.0         # wait this long on the rewards page before pressing 3
    tenth_answer: str = "none"  # "none" lets the dialog time out; "yes" chooses 是
    hold_w: str = "none"        # runs whose battles get w pulses: none / odd / even / all
    max_w_pulses: int = 400     # per battle
    defeat_hold: float = 10.0   # seconds on a failure screen before the run ends
    start_grace: float = 300.0  # unrecognised screens allowed before the first known one
    unknown_limit: float = 120.0
    screen_limit: float = 150.0
    battle_limit: float = 900.0


def repeat_state(text):
    """Read the repeat button: `③再次挑战` is off, `③撤销再次挑战` is on."""
    if not text:
        return None
    text = "".join(text.split())
    if "撤销" in text and "挑战" in text:
        return "on"
    if text.endswith("再次挑战"):
        return "off"
    return None


class Planner:
    """Decide the next key from observations. No capture, input or clock of its own."""

    def __init__(self, settings):
        self.s = settings
        self.runs = 0
        self.battle_since_results = False
        self.last_known = None
        self.since = None
        self.unknown_since = None
        self.tips_count = 0
        self.pending = None
        self.settled = False
        self.esc_done = False
        self.return_tries = 0
        self.last_press_at = -math.inf
        self.presses = 0
        self.finished = None
        self.log = []
        self.toggle_seen = None     # when the rewards page's toggle first showed
        self.tenth = None           # progress of answering the tenth-run dialog
        self.answered_yes = False   # 是 was chosen on this results page
        self.w_pulses = 0
        self.battle_started = None
        self.last_auto_skip = None

    def event(self, t, kind, **data):
        self.log.append({"time": t, "event": kind, "runs": self.runs, **data})

    def finish(self, t, reason):
        if self.finished is None:
            self.finished = reason
            self.event(t, "finished", reason=reason)

    def desired_repeat(self):
        off = self.runs >= self.s.max_runs or (self.s.off_at is not None
                                               and self.runs >= self.s.off_at)
        return "off" if off else "on"

    def press(self, t, key, reason):
        if self.presses >= self.s.max_presses:
            self.finish(t, "the press budget was used up")
            return []
        self.presses += 1
        self.last_press_at = t
        self.event(t, "press", key=key, reason=reason)
        return [key]

    def wants_w(self):
        run = self.runs + 1
        return {"none": False, "all": True, "odd": run % 2 == 1,
                "even": run % 2 == 0}[self.s.hold_w]

    def observe(self, obs):
        t, screen = obs.time, obs.screen
        if self.finished is not None:
            return []
        if screen == "unknown":
            # The ready room and menus before departure are not recognised screens.
            limit = self.s.unknown_limit if self.last_known else self.s.start_grace
            self.unknown_since = t if self.unknown_since is None else self.unknown_since
            if t - self.unknown_since >= limit:
                self.finish(t, "an unrecognised screen lasted %.0f s" % limit)
            return []
        self.unknown_since = None
        new = screen != self.last_known
        if obs.auto_skip is not None and obs.auto_skip != self.last_auto_skip:
            self.last_auto_skip = obs.auto_skip
            self.event(t, "auto_skip_label", state=obs.auto_skip, screen=screen)
        if new:
            if screen == "tenth" and self.last_known == "results":
                # The dialog covers the rewards page; keep that page's state.
                self.last_known = screen
                self.event(t, "screen", screen=screen)
                self.tenth = self.tenth or {"stage": "seen", "at": t, "tries": 0}
            elif screen == "results" and self.last_known == "tenth":
                self.last_known = screen
                self.event(t, "screen", screen=screen, after_dialog=True)
            else:
                self.leave(t)
                self.last_known, self.since = screen, t
                self.pending, self.settled = None, False
                self.esc_done, self.return_tries = False, 0
                self.event(t, "screen", screen=screen)
        limit = self.s.battle_limit if screen == "battle" else self.s.screen_limit
        if screen != "town" and t - self.since >= limit:
            self.finish(t, f"the {screen} screen lasted {limit:.0f} s")
            return []
        if screen == "battle":
            self.battle_since_results = True
            if self.battle_started is None:
                self.battle_started = t
                self.event(t, "battle_started", run=self.runs + 1, hold_w=self.wants_w())
            if self.wants_w() and self.w_pulses < self.s.max_w_pulses:
                self.w_pulses += 1
                return ["w"]
            return []
        if screen == "defeat":
            if t - self.since >= self.s.defeat_hold:
                self.finish(t, "failure screen")
            return []
        if screen == "tenth":
            return self.tenth_dialog(obs)
        if screen == "results":
            return self.results(obs, new)
        if screen == "tips":
            return self.tips(t, new, obs.auto_skip)
        if screen == "town" and t - self.since >= self.s.town_settle:
            self.finish(t, "town reached")
        return []

    def leave(self, t):
        if self.last_known == "tips":
            self.event(t, "tips_left", seconds=t - self.since, returns=self.return_tries,
                       auto_advanced=self.return_tries == 0)

    def tenth_dialog(self, obs):
        t = obs.time
        if self.tenth is None:
            self.tenth = {"stage": "seen", "at": t, "tries": 0}
        state = self.tenth
        if self.s.tenth_answer != "yes" or state["stage"] == "done":
            return []
        if state["stage"] == "seen":
            if obs.choice != "no" or t - state["at"] < 1.0:
                return []
            state.update(stage="moved", at=t, tries=state["tries"] + 1)
            return self.press(t, "Up", "move the dialog cursor to 是")
        if state["stage"] == "moved":
            if obs.choice == "yes":
                state.update(stage="done", at=t)
                self.answered_yes = True
                return self.press(t, "Return", "choose 是: continue the quest")
            if t - state["at"] >= self.s.verify_seconds:
                if state["tries"] >= self.s.max_tries:
                    state["stage"] = "done"
                    self.event(t, "tenth_unanswered", seen=obs.choice)
                    return []
                state.update(stage="seen", at=t - 1.0)
        return []

    def results(self, obs, new):
        t, label = obs.time, obs.repeat
        if new and self.battle_since_results:
            self.runs += 1
            self.battle_since_results = False
            started = self.battle_started
            self.event(t, "run_settled", battle_seconds=None if started is None else t - started,
                       hold_w=self.s.hold_w != "none" and self.w_pulses > 0, w_pulses=self.w_pulses)
            self.battle_started, self.w_pulses = None, 0
            self.toggle_seen, self.tenth, self.answered_yes = None, None, False
        if label is not None and self.toggle_seen is None:
            self.toggle_seen = t
        if self.answered_yes:
            if label is not None and not self.settled:
                self.event(t, "repeat_state_after_yes", state=label)
                self.settled = True
            return []
        if self.tenth is not None and self.pending is None:
            return []  # the dialog came up on this page; never press 3 into it
        # The reward reveal ignores input. On every tenth run the game's dialog comes up
        # about 2 s into the page, so wait longer there rather than press 3 into it.
        reveal = max(self.s.reveal, 6.0) if self.runs % 10 == 0 and self.s.reveal else self.s.reveal
        if self.pending is None and label is not None and t - self.toggle_seen < reveal:
            return []
        want = self.desired_repeat()
        if self.pending is not None:
            if label == self.pending["want"]:
                self.event(t, "repeat_verified", state=label, tries=self.pending["tries"],
                           latency=t - self.pending["first"])
                self.pending, self.settled = None, True
                return []
            if t - self.pending["at"] < self.s.verify_seconds or label is None:
                return []
            if self.pending["tries"] >= self.s.max_tries:
                self.event(t, "repeat_unverified", want=self.pending["want"], seen=label)
                self.pending, self.settled = None, True
                return []
            self.pending["tries"] += 1
            self.pending["at"] = t
            return self.press(t, "3", f"retry: turn repeat {want}")
        if self.settled or label is None:
            return []
        if label == want:
            self.event(t, "repeat_state", state=label)
            self.settled = True
            return []
        self.pending = {"want": want, "at": t, "first": t, "tries": 1}
        return self.press(t, "3", f"turn repeat {want}")

    def tips(self, t, new, auto_skip=None):
        if new:
            self.tips_count += 1
            self.event(t, "tips", count=self.tips_count)
        # Escape toggles AUTO SKIP, so it is pressed only on a frame whose label reads off:
        # an unreadable label may hide an AUTO SKIP that is already on.
        if (self.s.auto_skip_at and self.tips_count == self.s.auto_skip_at and not self.esc_done
                and auto_skip == "off"):
            self.esc_done = True
            return self.press(t, "Escape", "turn on AUTO SKIP")
        if (t - self.since >= self.s.tips_wait and self.return_tries < self.s.max_tries
                and t - self.last_press_at >= self.s.verify_seconds + 0.5):
            self.return_tries += 1
            return self.press(t, "Return", "continue past loading tips")
        return []


class Classifier:
    """Name the screen in a 1280x720 frame with rc.2's nodes; recognition only."""

    def __init__(self, resource_path, quest, objective, log_dir):
        from maa.controller import CustomController
        from maa.define import LoggingLevelEnum
        from maa.pipeline import JPipelineParser
        from maa.resource import Resource
        from maa.tasker import Tasker

        class Offline(CustomController):
            def connect(self):
                return True

            def request_uuid(self):
                return "gbfr-repeat-classifier"

            def screencap(self):
                raise AssertionError("the classifier only reads supplied frames")

            def denied(self, *args, **kwargs):
                raise AssertionError("the classifier never sends input")

            start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = denied
            click_key = input_text = key_down = key_up = denied

        Tasker.set_log_dir(log_dir)
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        Tasker.set_save_draw(False)
        # Resources up to 0.2.0-rc.1 match the quest's name and objective; later ones
        # match no quest name, and these overrides are skipped for them.
        override = {f"GBFR_Catalog_{n}": {"expected": quest} for n in
                    ("NameList", "NameDetails", "NameReady", "NameConfirm", "NameResult")}
        override.update({f"GBFR_Catalog_{n}": {"expected": objective}
                         for n in ("ObjectiveDetails", "ObjectiveBattle")})
        for name, roi in (("Repeat", REPEAT_ROI), ("Countdown", COUNTDOWN_ROI),
                          ("Center", CENTER_ROI)):
            override["Measure_" + name] = {"recognition": "OCR", "roi": roi,
                                           "expected": ".*", "threshold": 0.3}
        override["Measure_AutoSkip"] = {"recognition": "OCR", "roi": AUTO_SKIP_ROI,
                                        "expected": "AUTO ?SKIP", "threshold": 0.3}
        self.resource = Resource()
        self.resource.use_cpu()
        if not self.resource.post_bundle(resource_path).wait().succeeded:
            raise RuntimeError("Could not load the recognition resource")
        known = set(self.resource.node_list)
        override = {name: value for name, value in override.items()
                    if not name.startswith("GBFR_Catalog_") or name in known}
        if not self.resource.override_pipeline(override):
            raise RuntimeError("Could not apply the quest overrides")
        self.controller = Offline()
        self.controller.post_connection().wait()
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, self.controller):
            raise RuntimeError("Could not bind the classifier")
        names = [node for _, node in SCREEN_NODES] + [
            "GBFR_See_RepeatOn", "GBFR_See_RepeatOff", "Measure_Repeat", "Measure_Countdown",
            "Measure_Center", "Measure_AutoSkip"]
        self.nodes = {name: JPipelineParser.parse_pipeline_data(self.resource.get_node_data(name))
                      for name in names}

    def _run(self, name, frame):
        node = self.nodes[name]
        detail = self.tasker.post_recognition(node.recognition.type, node.recognition.param,
                                              frame).wait().get()
        hit, text = False, None
        for item in detail.nodes if detail else []:
            result = item.recognition
            if result and result.hit:
                hit = True
                best = result.best_result
                text = getattr(best, "text", None) if best is not None else None
        return hit, text

    def classify(self, frame, t, *, read_center=False):
        screen = "unknown"
        for name, node in SCREEN_NODES:
            if self._run(node, frame)[0]:
                screen = name
                break
        if screen in ("unknown", "tips") and self._run("Measure_AutoSkip", frame)[0]:
            screen = "tips" if screen == "tips" else "loading"
        row = {"screen": screen}
        repeat = choice = auto_skip = None
        if screen == "results":
            row["repeat_text"] = self._run("Measure_Repeat", frame)[1]
            row["countdown"] = self._run("Measure_Countdown", frame)[1]
            if self._run("GBFR_See_RepeatOn", frame)[0]:
                repeat = "on"
            elif self._run("GBFR_See_RepeatOff", frame)[0]:
                repeat = "off"
        if screen == "tenth":
            choice = row["choice"] = dialog_choice(frame)
        if screen in ("tips", "loading"):
            auto_skip = row["auto_skip"] = auto_skip_state(frame)
        if read_center:
            row["center_text"] = self._run("Measure_Center", frame)[1]
        return Observation(t, screen, repeat, choice, auto_skip), row


class Recorder:
    def __init__(self, out, *, save_images=True):
        self.out = out
        self.save_images = save_images
        if save_images:
            (out / "frames").mkdir(parents=True)
        self.frames = (out / "frames.jsonl").open("w")
        self.events = (out / "events.jsonl").open("w")
        self.last_saved = -math.inf
        self.written = 0

    def planner_events(self, planner, **extra):
        for item in planner.log[self.written:]:
            self.event(dict(item, **extra))
        self.written = len(planner.log)

    def frame(self, frame, t, wall, row, *, force=False, tag=""):
        import cv2

        if self.save_images and (force or t - self.last_saved >= 1.0):
            name = f"frames/{wall:.2f}{tag}.jpg"
            cv2.imwrite(str(self.out / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            row = dict(row, file=name)
            self.last_saved = t
        self.frames.write(json.dumps(dict(row, time=t, wall=wall), ensure_ascii=False) + "\n")
        self.frames.flush()

    def event(self, value):
        self.events.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.events.flush()

    def close(self):
        self.frames.close()
        self.events.close()


class LiveGame:
    """Capture and the planned keys through Maa's Gamescope controller."""

    def __init__(self):
        import gamescope
        from maa.controller import LinuxController
        from maa.library import Library
        from maa.toolkit import Toolkit
        from maa_target import process_stamp, select_game_window
        from tools.maa_probe import (PINNED_VERSION, check_target, controller_config,
                                     discover_target, read_node, select_instance, wait_job)
        from xtest_input import XTestInput

        self._check_target, self._read_node = check_target, read_node
        self._process_stamp, self._wait_job = process_stamp, wait_job
        self.display = None
        if Library.version().lstrip("v") != PINNED_VERSION:
            raise RuntimeError("MaaFramework is not the pinned version")
        self.target = discover_target("881020")
        check_target(self.target)
        instance = select_instance(self.target, Toolkit.find_gamescope_instances())
        self.node = instance.pipewire_node_id
        self.serial = read_node(self.node, self.target)
        self.controller = LinuxController(controller_config(instance, input_enabled=True))
        self.controller.set_screenshot_target_short_side(720)
        wait_job(self.controller.post_connection(), 8)
        values = gamescope.instance_values(gamescope.find_game_processes("881020"))
        if values["display"] != self.target.display:
            raise RuntimeError("The nested display changed during connection")
        self.display = gamescope.connect_x(self.target.display, values["xauth"])
        window, self.game_pid, self.game_start = select_game_window(self.display, self.target)
        self.guard = XTestInput(self.display, window, {k: k for k in KEYS}, live=False,
                                announce_dry_run=False)
        self.checked_at = -math.inf

    def check(self, now, *, full=False):
        self._check_target(self.target)
        if self._process_stamp(self.game_pid) != self.game_start:
            raise RuntimeError("The game process was replaced")
        if full or now - self.checked_at >= 10:
            if self._read_node(self.node, self.target) != self.serial:
                raise RuntimeError("The capture source was replaced")
            self.checked_at = now

    def capture(self):
        frame = self._wait_job(self.controller.post_screencap(), 5).get()
        if frame is None or frame.size == 0:
            raise RuntimeError("Empty capture")
        return frame

    def press(self, key, hold_ms=100):
        from xtest_input import _is_down

        if key not in KEYS or type(hold_ms) is not int or not 50 <= hold_ms <= MAX_HOLD_MS:
            raise RuntimeError("Unsupported key or unbounded hold")
        # A w pulse repeats every few seconds; the pw-dump identity check stays at 10 s.
        self.check(time.monotonic(), full=key != "w")
        code = self.guard._keycode(key)
        if code - 8 != KEYS[key]:
            raise RuntimeError("Unexpected keyboard layout for " + key)
        if not self.guard._clear_to_press_key(key, code, "repeat measurement"):
            raise RuntimeError("Focus, a held key or the layout blocks automation input")
        receipt = {"key": key, "down": time.monotonic()}
        try:
            self._wait_job(self.controller.post_key_down(KEYS[key]), 3)
            time.sleep(hold_ms / 1000)
        finally:
            receipt["up"] = time.monotonic()
            self._wait_job(self.controller.post_key_up(KEYS[key]), 3)
        deadline = time.monotonic() + 0.5
        while True:
            state = self.guard._read_state("release check")
            if state is not None and not _is_down(state["keys_down"], code):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("The key release was not observed")
            time.sleep(0.02)
        receipt["released"] = time.monotonic()
        return receipt

    def close(self):
        if self.display is not None:
            self.display.close()
            self.display = None


def run_live(args, settings, out):
    import cv2

    from control import ControlServer, Controls, socket_path

    controls = Controls()
    server = ControlServer(controls, socket_path()).start()
    recorder = Recorder(out)
    planner = Planner(settings)
    game = None
    try:
        classifier = Classifier(args.resource, args.quest, args.objective, out / "maa-log")
        game = LiveGame()
        game.check(time.monotonic(), full=True)
        start = time.monotonic()
        last_center = -math.inf
        while planner.finished is None:
            now = time.monotonic()
            if controls.stop_requested or controls.paused:
                planner.finish(now, "stop requested")
                break
            if now - start >= args.hours * 3600:
                planner.finish(now, "time limit reached")
                break
            game.check(now)
            frame = cv2.resize(game.capture(), (1280, 720), interpolation=cv2.INTER_AREA)
            stalled = planner.last_known == "results" and now - (planner.since or now) > 65
            read_center = (planner.unknown_since is not None or stalled) and now - last_center >= 5
            obs, row = classifier.classify(frame, now, read_center=read_center)
            last_center = now if read_center else last_center
            keys = planner.observe(obs)
            recorder.frame(frame, now, time.time(), row, force=bool(keys or read_center))
            recorder.planner_events(planner)
            for key in keys:
                if key == "w":
                    receipt = game.press(key, args.hold_ms)
                    recorder.event({"event": "press_receipt", **receipt})
                    continue
                receipt = game.press(key)
                time.sleep(0.4)
                after = cv2.resize(game.capture(), (1280, 720), interpolation=cv2.INTER_AREA)
                recorder.frame(after, time.monotonic(), time.time(), {"after_press": key},
                               force=True, tag="-after-" + key)
                recorder.event({"event": "press_receipt", **receipt})
            time.sleep(max(0, 0.33 - (time.monotonic() - now)))
    except BaseException as exc:
        planner.finish(time.monotonic(), f"error: {exc}")
        raise
    finally:
        recorder.planner_events(planner)
        write_summary(out, args, settings, planner)
        recorder.close()
        if game is not None:
            game.close()
        server.close()
    return planner


def run_replay(args, settings, out):
    import cv2

    capture = Path(args.replay)
    rows = [json.loads(line) for line in (capture / "frames.jsonl").read_text().splitlines()]
    classifier = Classifier(args.resource, args.quest, args.objective, out / "maa-log")
    recorder = Recorder(out, save_images=False)
    planner = Planner(settings)
    try:
        for row in rows:
            if "file" not in row:
                continue
            image = cv2.imread(str(capture / row["file"]))
            frame = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)
            t = row.get("wall_time", row.get("wall", row.get("time")))
            obs, result = classifier.classify(frame, t)
            planner.observe(obs)
            recorder.frame(frame, t, t, dict(result, source=row["file"]))
            recorder.planner_events(planner, replay=True)
            if planner.finished is not None:
                break
    finally:
        recorder.planner_events(planner, replay=True)
        write_summary(out, args, settings, planner)
        recorder.close()
    return planner


def write_summary(out, args, settings, planner):
    screens = [e for e in planner.log if e["event"] == "screen"]
    summary = {"mode": "replay" if args.replay else "live", "quest": args.quest,
               "settings": asdict(settings), "finished": planner.finished,
               "runs_settled": planner.runs, "presses": planner.presses,
               "screens": screens, "log": planner.log}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


def private_out(value):
    path = Path(value).resolve()
    if path.exists():
        raise argparse.ArgumentTypeError("choose a new output directory")
    if path.is_relative_to(ROOT):
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)],
                                 check=False).returncode == 0
        if not ignored:
            raise argparse.ArgumentTypeError("frames are private; use a directory git ignores")
    return path


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=private_out, required=True,
                        help="new private directory for frames and logs")
    parser.add_argument("--resource", type=Path,
                        default=ROOT / "dist" / "maa-0.2.0-rc.1" / "resource",
                        help="Maa resource with its OCR model (a built bundle's resource/)")
    parser.add_argument("--quest", default="解决沙漠狂徒")
    parser.add_argument("--objective", default="讨伐巨爪岩鳄")
    parser.add_argument("--off-at", type=positive_int, default=None,
                        help="turn repeat off on this run's results page")
    parser.add_argument("--max-runs", type=positive_int, default=12)
    parser.add_argument("--tips-wait", type=float, default=15.0)
    parser.add_argument("--auto-skip-at", type=int, default=2,
                        help="press Escape on this loading-tips screen; 0 never")
    parser.add_argument("--reveal", type=float, default=2.5,
                        help="seconds to wait on the rewards page before pressing 3")
    parser.add_argument("--tenth-answer", choices=("none", "yes"), default="none",
                        help="answer the tenth-run dialog: none lets it time out")
    parser.add_argument("--hold-w", choices=("none", "odd", "even", "all"), default="none",
                        help="runs whose battles get w pulses (run 1 is odd)")
    parser.add_argument("--hold-ms", type=int, default=3000, help="length of one w pulse")
    parser.add_argument("--hours", type=float, default=1.75)
    parser.add_argument("--replay", type=Path, help="saved capture directory; no input")
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 3:
        parser.error("--hours must be above 0 and at most 3")
    if not 1 <= args.tips_wait <= 60:
        parser.error("--tips-wait must be 1 to 60 seconds")
    if args.auto_skip_at < 0:
        parser.error("--auto-skip-at cannot be negative")
    if not 500 <= args.hold_ms <= MAX_HOLD_MS:
        parser.error(f"--hold-ms must be 500 to {MAX_HOLD_MS}")
    if not 0 <= args.reveal <= 10:
        parser.error("--reveal must be 0 to 10 seconds")
    return args


def main(argv=None):
    args = parse_args(argv)
    settings = Settings(off_at=args.off_at, max_runs=args.max_runs, tips_wait=args.tips_wait,
                        auto_skip_at=args.auto_skip_at, reveal=args.reveal,
                        tenth_answer=args.tenth_answer, hold_w=args.hold_w)
    os.umask(0o077)
    args.out.mkdir(parents=True, mode=0o700)
    planner = (run_replay if args.replay else run_live)(args, settings, args.out)
    print(json.dumps({"out": str(args.out), "finished": planner.finished,
                      "runs_settled": planner.runs, "presses": planner.presses},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
