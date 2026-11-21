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
- `Return` on a loading-tips screen that is still waiting after --tips-wait seconds.

An unrecognised screen never gets input. The run ends when town has been stable for a
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

KEYS = {"3": 4, "Return": 28, "Escape": 1}
REPEAT_ROI = [45, 637, 265, 30]
COUNTDOWN_ROI = [900, 662, 185, 35]
CENTER_ROI = [160, 120, 960, 480]
SCREEN_NODES = (("results", "GBFR_Result"), ("victory", "GBFR_Victory"),
                ("tips", "GBFR_BattleTip"), ("town", "GBFR_SettledTown"),
                ("battle", "GBFR_Battle"))


@dataclass(frozen=True)
class Observation:
    time: float
    screen: str                 # one of SCREEN_NODES' names, or "unknown"
    repeat: str | None = None   # "on" / "off" on the results page with the button


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
        if new:
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
            return []
        if screen == "results":
            return self.results(obs, new)
        if screen == "tips":
            return self.tips(t, new)
        if screen == "town" and t - self.since >= self.s.town_settle:
            self.finish(t, "town reached")
        return []

    def leave(self, t):
        if self.last_known == "tips":
            self.event(t, "tips_left", seconds=t - self.since, returns=self.return_tries,
                       auto_advanced=self.return_tries == 0)

    def results(self, obs, new):
        t, label = obs.time, obs.repeat
        if new and self.battle_since_results:
            self.runs += 1
            self.battle_since_results = False
            self.event(t, "run_settled")
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

    def tips(self, t, new):
        if new:
            self.tips_count += 1
            self.event(t, "tips", count=self.tips_count)
        if self.s.auto_skip_at and self.tips_count == self.s.auto_skip_at and not self.esc_done:
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
        override = {f"GBFR_Catalog_{n}": {"expected": quest} for n in
                    ("NameList", "NameDetails", "NameReady", "NameConfirm", "NameResult")}
        override.update({f"GBFR_Catalog_{n}": {"expected": objective}
                         for n in ("ObjectiveDetails", "ObjectiveBattle")})
        for name, roi in (("Repeat", REPEAT_ROI), ("Countdown", COUNTDOWN_ROI),
                          ("Center", CENTER_ROI)):
            override["Measure_" + name] = {"recognition": "OCR", "roi": roi,
                                           "expected": ".*", "threshold": 0.3}
        self.resource = Resource()
        self.resource.use_cpu()
        if not self.resource.post_bundle(resource_path).wait().succeeded:
            raise RuntimeError("Could not load the recognition resource")
        if not self.resource.override_pipeline(override):
            raise RuntimeError("Could not apply the quest overrides")
        self.controller = Offline()
        self.controller.post_connection().wait()
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, self.controller):
            raise RuntimeError("Could not bind the classifier")
        names = [node for _, node in SCREEN_NODES] + ["Measure_Repeat", "Measure_Countdown",
                                                      "Measure_Center"]
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
        row = {"screen": screen}
        if screen == "results":
            row["repeat_text"] = self._run("Measure_Repeat", frame)[1]
            row["countdown"] = self._run("Measure_Countdown", frame)[1]
        if read_center:
            row["center_text"] = self._run("Measure_Center", frame)[1]
        repeat = repeat_state(row.get("repeat_text"))
        return Observation(t, screen, repeat), row


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

    def press(self, key):
        from xtest_input import _is_down

        self.check(time.monotonic(), full=True)
        code = self.guard._keycode(key)
        if code - 8 != KEYS[key]:
            raise RuntimeError("Unexpected keyboard layout for " + key)
        if not self.guard._clear_to_press_key(key, code, "repeat measurement"):
            raise RuntimeError("Focus, a held key or the layout blocks automation input")
        receipt = {"key": key, "down": time.monotonic()}
        try:
            self._wait_job(self.controller.post_key_down(KEYS[key]), 3)
            time.sleep(0.1)
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
                        default=ROOT / "dist" / "maa-0.1.0-rc.2" / "resource",
                        help="Maa resource with its OCR model (a built bundle's resource/)")
    parser.add_argument("--quest", default="解决沙漠狂徒")
    parser.add_argument("--objective", default="讨伐巨爪岩鳄")
    parser.add_argument("--off-at", type=positive_int, default=None,
                        help="turn repeat off on this run's results page")
    parser.add_argument("--max-runs", type=positive_int, default=12)
    parser.add_argument("--tips-wait", type=float, default=15.0)
    parser.add_argument("--auto-skip-at", type=int, default=2,
                        help="press Escape on this loading-tips screen; 0 never")
    parser.add_argument("--hours", type=float, default=1.75)
    parser.add_argument("--replay", type=Path, help="saved capture directory; no input")
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 3:
        parser.error("--hours must be above 0 and at most 3")
    if not 1 <= args.tips_wait <= 60:
        parser.error("--tips-wait must be 1 to 60 seconds")
    if args.auto_skip_at < 0:
        parser.error("--auto-skip-at cannot be negative")
    return args


def main(argv=None):
    args = parse_args(argv)
    settings = Settings(off_at=args.off_at, max_runs=args.max_runs, tips_wait=args.tips_wait,
                        auto_skip_at=args.auto_skip_at)
    os.umask(0o077)
    args.out.mkdir(parents=True, mode=0o700)
    planner = (run_replay if args.replay else run_live)(args, settings, args.out)
    print(json.dumps({"out": str(args.out), "finished": planner.finished,
                      "runs_settled": planner.runs, "presses": planner.presses},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
