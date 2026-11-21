# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Bounded Maa compatibility measurements. Real-game commands never enable input."""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gamescope  # noqa: E402
from control import ControlServer, Controls, socket_path  # noqa: E402

GAME_APPID = "881020"
TEST_APPID = "900000001"
PINNED_VERSION = "5.16.1"


# Re-export the established probe API; the runtime uses the same identity checks.
from maa_target import (  # noqa: E402,F401
    ProbeError, Target, check_target, controller_config, discover_target,
    node_identity, process_stamp, read_node, select_instance,
)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)



def wait_job(job, seconds=6):
    deadline = time.monotonic() + seconds
    while not job.done:
        if time.monotonic() >= deadline:
            raise ProbeError("native operation exceeded the probe deadline")
        time.sleep(0.01)
    if not job.succeeded:
        raise ProbeError("native operation failed; inspect native.log and debug/maafw.log")
    return job


def summarize_frames(frames, duration):
    usable = [f for f in frames if not f["blank"]]
    times = [f["time"] for f in usable]
    latencies = sorted(f["latency_ms"] for f in frames)
    def percentile(p):
        return latencies[min(len(latencies) - 1, math.ceil(p * len(latencies)) - 1)] if latencies else None
    return {"frames": len(frames), "usable_frames": len(usable),
            "usable_fps": len(usable) / duration if duration > 0 else 0,
            "unique_images": len({f["sha256"] for f in usable}),
            "max_gap_s": max((b - a for a, b in zip(times, times[1:], strict=False)), default=None),
            "latency_p50_ms": percentile(0.50), "latency_p95_ms": percentile(0.95),
            "latency_max_ms": max(latencies, default=None),
            "freshness": "unverified: image hashes are not producer timestamps"}


def capture_frames(controller, target, node_id, serial, out, seconds):
    import cv2
    import numpy as np

    controller.set_screenshot_use_raw_size(True)
    frames = []
    start = time.monotonic()
    with (out / "frames.jsonl").open("w", buffering=1) as log:
        while time.monotonic() - start < seconds:
            check_target(target)
            if read_node(node_id, target) != serial:
                raise ProbeError("PipeWire node was replaced")
            before = time.monotonic()
            job = wait_job(controller.post_screencap())
            frame = job.get()
            after = time.monotonic()
            check_target(target)
            if read_node(node_id, target) != serial:
                raise ProbeError("PipeWire source changed during capture; discarding frame")
            if frame is None or frame.size == 0:
                raise ProbeError("Maa returned an empty frame")
            blank = bool(np.max(np.std(frame.astype(np.float32), axis=(0, 1))) < 1.0)
            row = {"index": len(frames), "time": after, "latency_ms": (after - before) * 1000,
                   "shape": list(frame.shape), "blank": blank,
                   "sha256": hashlib.sha256(frame.tobytes()).hexdigest()}
            log.write(json.dumps(row) + "\n")
            if len(frames) < 3:
                if not cv2.imwrite(str(out / f"sample-{len(frames)}.png"), frame):
                    raise ProbeError("could not save sample frame")
            frames.append(row)
            time.sleep(max(0, 0.2 - (time.monotonic() - before)))
    return summarize_frames(frames, time.monotonic() - start)


def validate_test_receiver(target, receiver, token):
    if (target.appid != TEST_APPID or receiver.get("appid") != TEST_APPID
            or not token or receiver.get("token") != token
            or receiver.get("pid") != target.member_pid):
        raise ProbeError("input is restricted to the explicitly owned test receiver")
    if gamescope.nearest_gamescope(receiver["pid"]) != target.pid:
        raise ProbeError("test receiver does not belong to the owned compositor")
    environment = gamescope.read_environ(receiver["pid"]) or {}
    if (environment.get("SteamAppId") != TEST_APPID
            or environment.get("GBFR_MAA_TEST_TOKEN") != token):
        raise ProbeError("running receiver does not carry the private test identity")


def input_trial(controller, target, out, *, with_mouse):
    """Direct Maa calls are tested only on the owned receiver, never on GBFR."""
    events = []

    def action(name, function, *args):
        check_target(target)
        before = time.monotonic()
        wait_job(function(*args), 3)
        events.append({"action": name, "time": before, "done": time.monotonic()})
        write_json(out / "sent.json", events)

    held_keys = set()
    mouse_held = False
    try:
        # Linux evdev codes, not X11 keycodes or Win32 virtual key codes.
        for name, key in (("W", 17), ("A", 30), ("3", 4)):
            held_keys.add(key)  # include possibly delivered calls that later report failure
            action(name + " down", controller.post_key_down, key)
            time.sleep(0.15)
            action(name + " up", controller.post_key_up, key)
            held_keys.remove(key)
        if with_mouse:
            mouse_held = True
            action("middle down", controller.post_touch_down, 320, 180, 2)
            time.sleep(0.25)
            action("middle up", controller.post_touch_up, 2)
            mouse_held = False
        held_keys.add(17)
        action("hold W", controller.post_key_down, 17)
        time.sleep(0.4)
    finally:
        # Cleanup does not depend on target validation or another screenshot.
        release_at = time.monotonic()
        failures = []
        for key in held_keys:
            try:
                wait_job(controller.post_key_up(key), 3)
            except Exception as exc:
                failures.append(str(exc))
        if mouse_held:
            try:
                wait_job(controller.post_touch_up(2), 3)
            except Exception as exc:
                failures.append(str(exc))
        write_json(out / "release.json", {"requested": release_at,
                   "completed": time.monotonic(), "failures": failures})
        if failures:
            raise ProbeError("one or more cleanup operations failed")
    return {"api_actions": len(events), "receiver_verification": "pending"}


RECEIVER_MAX_GAP_S = 0.25
RELEASE_LIMIT_S = 1.0
RELEASE_CLEAR_S = 1.2


def read_receiver_state(path, now=None):
    """Read a fresh atomic X-query sample, not the receiver's last input event."""
    try:
        state = json.loads(Path(path).read_text())
        now = time.monotonic() if now is None else now
        stamp, frame = state["time"], state["frame"]
        keys, buttons = state["keys"], state["buttons"]
        valid = (type(stamp) in (int, float) and math.isfinite(stamp)
                 and 0 <= now - stamp <= RECEIVER_MAX_GAP_S
                 and type(frame) is int and frame >= 0
                 and isinstance(keys, list) and len(keys) == 32
                 and all(type(k) is int and 0 <= k <= 255 for k in keys)
                 and type(buttons) is int and 0 <= buttons <= 0xFFFF)
        if not valid:
            raise ValueError("stale or invalid receiver sample")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProbeError(f"receiver state unavailable: {exc}") from exc
    return state


def receiver_holds_inputs(state, *, keys=(25,), buttons=()):
    """Match the measured X keycodes and logical mouse buttons in a fresh sample."""
    return (bool(keys or buttons)
            and all(type(k) is int and 0 <= k < 256
                    and state["keys"][k // 8] & (1 << (k % 8)) for k in keys)
            and all(type(b) is int and 1 <= b <= 5
                    and state["buttons"] & (1 << (b + 7)) for b in buttons))


def receiver_holds_w(state):
    return receiver_holds_inputs(state)


def observe_release(receiver_path, requested, before, *, keys=(25,), buttons=()):
    """Require advancing, clear samples for 1.2 s after release within 1 s.

    This is sampled receiver evidence, not proof of unsampled hardware state.
    Events can disprove a release or corroborate its up, but cannot pass alone.
    """
    directory = Path(receiver_path).parent
    samples = []
    first_clear = None
    result = {"release_passed": False, "release_observed_ms": None,
              "clear_observed_ms": 0, "receiver_samples": samples}
    try:
        if (not receiver_holds_inputs(before, keys=keys, buttons=buttons)
                or not 0 <= requested - before["time"] <= RECEIVER_MAX_GAP_S):
            raise ProbeError("no fresh held state immediately before stop")
        last = before
        deadline = requested + RELEASE_LIMIT_S + RELEASE_CLEAR_S + RECEIVER_MAX_GAP_S
        while time.monotonic() <= deadline:
            state = read_receiver_state(directory / "receiver-state.json")
            if state != last:
                if (state["frame"] <= last["frame"] or state["time"] <= last["time"]
                        or state["time"] - last["time"] > RECEIVER_MAX_GAP_S):
                    raise ProbeError("receiver samples did not advance continuously")
                last = state
                samples.append(state)
                if state["time"] >= requested:
                    held = any(state["keys"]) or bool(state["buttons"])
                    if held and first_clear is not None:
                        raise ProbeError("input became held again after release")
                    if not held and first_clear is None:
                        first_clear = state["time"]
                        result["release_observed_ms"] = (first_clear - requested) * 1000
                    if first_clear is not None:
                        result["clear_observed_ms"] = (state["time"] - first_clear) * 1000
                        if first_clear - requested > RELEASE_LIMIT_S:
                            raise ProbeError("release exceeded one second")
                        if state["time"] - first_clear >= RELEASE_CLEAR_S:
                            text = (directory / "received.jsonl").read_text()
                            if text and not text.endswith("\n"):
                                raise ProbeError("receiver event write incomplete")
                            rows = [json.loads(line) for line in text.splitlines()]
                            now = time.monotonic()
                            if not all(isinstance(r, dict)
                                       and type(r.get("time")) in (int, float)
                                       and math.isfinite(r["time"]) and r["time"] <= now
                                       and r.get("event") in ("key_up", "key_down", "button_up", "button_down")
                                       and type(r.get("code")) is int for r in rows):
                                raise ProbeError("invalid receiver event stream")
                            rows = [r for r in rows if requested <= r["time"]]
                            expected = [("key_up", k) for k in keys]
                            expected += [("button_up", b) for b in buttons]
                            up_times = {}
                            for event, code in expected:
                                ups = [r["time"] for r in rows if r["event"] == event
                                       and r["code"] == code and r["time"] <= first_clear]
                                if not ups:
                                    raise ProbeError("receiver did not record every matching up")
                                up_times[event, code] = max(ups)
                            release_started = min(up_times.values())
                            repeats = 0
                            for index, row in enumerate(rows):
                                if row["event"] not in ("key_down", "button_down"):
                                    continue
                                if row["time"] >= release_started:
                                    raise ProbeError("receiver observed a press after release")
                                previous = rows[index - 1] if index else {}
                                # Core X11 auto-repeat emits an adjacent up/down pair
                                # with the same server timestamp while the key stays
                                # held. It is not the final release and cannot prove
                                # cleanup; require a later up and sustained clear state.
                                repeat = (row["event"] == "key_down" and row["code"] in keys
                                          and previous.get("event") == "key_up"
                                          and previous.get("code") == row["code"]
                                          and type(row.get("server_time")) is int
                                          and 0 <= row["server_time"] <= 0xFFFFFFFF
                                          and row["server_time"] == previous.get("server_time")
                                          and 0 <= row["time"] - previous["time"] <= RECEIVER_MAX_GAP_S)
                                if not repeat:
                                    raise ProbeError("unconfirmed press before release")
                                repeats += 1
                            key_ups = [r["time"] for r in rows if r["event"] == "key_up" and r["code"] in keys]
                            button_ups = [r["time"] for r in rows if r["event"] == "button_up" and r["code"] in buttons]
                            return {**result, "release_passed": True,
                                    "pre_release_repeat_pairs": repeats,
                                    "matching_up_events": [{"event": event, "code": code,
                                        "observed_ms": (stamp - requested) * 1000}
                                        for (event, code), stamp in up_times.items()],
                                    "first_key_up_ms": (min(key_ups) - requested) * 1000 if key_ups else None,
                                    "first_button_up_ms": (min(button_ups) - requested) * 1000 if button_ups else None}
            if first_clear is None and time.monotonic() - requested > RELEASE_LIMIT_S:
                raise ProbeError("release not observed within one second")
            time.sleep(0.01)
        raise ProbeError("insufficient continuously clear receiver samples")
    except (ProbeError, OSError, ValueError, KeyError, TypeError) as exc:
        return {**result, "reason": str(exc)}


def stop_trial(controller, target, out, receiver_path):
    """Measure native pipeline auto-release while a real capture is pending."""
    from maa.resource import Resource
    from maa.tasker import Tasker

    pipeline = out / "resource" / "pipeline"
    pipeline.mkdir(parents=True)
    write_json(pipeline / "probe.json", {"Hold": {
        "recognition": "DirectHit", "action": "KeyDown", "key": 17,
        "auto_up": True, "pre_delay": 0, "post_delay": 10000,
    }})
    resource = Resource()
    wait_job(resource.post_bundle(pipeline.parent))
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise ProbeError("could not bind the native cancellation probe")
    state_path = Path(receiver_path).parent / "receiver-state.json"
    begin = time.monotonic()
    try:
        tasker.post_task("Hold")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = read_receiver_state(state_path)
            if state["time"] >= begin and receiver_holds_w(state):
                break
            time.sleep(0.01)
        else:
            raise ProbeError("pipeline key press was not observed by the receiver")
        check_target(target)
        capture = controller.post_screencap()
        time.sleep(0.1)
        pending = not capture.done
        before = read_receiver_state(state_path)
        if not receiver_holds_w(before):
            raise ProbeError("pipeline hold ended before the stop request")
        requested = time.monotonic()
        stop = tasker.post_stop()
        # Observe release independently of asynchronous task completion. A pipeline
        # delay can keep the stop job pending even though the key has been released.
        measurement = {"capture_pending_at_stop": pending, "requested": requested,
                       **observe_release(receiver_path, requested, before)}
        write_json(out / "stop-measurement.json", measurement)
        wait_job(stop, 12)
        completed = time.monotonic()
        release_passed = measurement["release_passed"]
        return {**measurement, "stop_complete_ms": (completed - requested) * 1000,
                "release_passed": release_passed, "passed": pending and release_passed,
                "scope": "one pipeline key with auto_up; " + (
                    "capture pending at stop" if pending else "capture completed before stop")}
    finally:
        # This second call is harmless for an already stopped test task.
        try:
            wait_job(tasker.post_stop(), 8)
        finally:
            wait_job(controller.post_key_up(17), 3)


def native_worker(request, out):
    from importlib.metadata import version
    from maa.controller import LinuxController
    from maa.library import Library
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.define import LoggingLevelEnum

    Tasker.set_log_dir(out / "debug")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    native = Library.version()
    if native.lstrip("v") != PINNED_VERSION or version("MaaFw").lstrip("v") != PINNED_VERSION:
        raise ProbeError("probe requires the pinned MaaFw 5.16.1 runtime")
    target = Target(**request["target"])
    check_target(target)
    instance = select_instance(target, Toolkit.find_gamescope_instances())
    input_enabled = request["mode"] in ("test-input", "test-input-only", "test-stop")
    capture = request["mode"] != "test-input-only"
    if input_enabled:
        receiver = json.loads(Path(request["receiver"]).read_text())
        validate_test_receiver(target, receiver, request.get("token"))
    config = controller_config(instance, input_enabled=input_enabled, capture=capture)
    serial = read_node(instance.pipewire_node_id, target) if capture else None
    write_json(out / "connection.json", {"version": native, "config": config,
               "node_serial": serial, "target": asdict(target)})
    before = time.monotonic()
    controller = LinuxController(config)
    controller.set_screenshot_use_raw_size(True)
    wait_job(controller.post_connection(), 8)
    result = {"connected": True, "connection_ms": (time.monotonic() - before) * 1000,
              "version": native, "mode": request["mode"]}
    write_json(out / "result.json", result)
    if request["mode"] == "test-stop":
        result.update(stop_trial(controller, target, out, request["receiver"]))
    elif input_enabled:
        result.update(input_trial(controller, target, out, with_mouse=capture))
    else:
        result.update(capture_frames(controller, target, instance.pipewire_node_id, serial,
                                     out, request["seconds"]))
    write_json(out / "result.json", result)


def run_worker(request, out, *, timeout, cancelled=lambda: False):
    out = Path(out).resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_json(out / "request.json", request)
    started = time.monotonic()
    with (out / "native.log").open("w") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                  "_worker", str(out / "request.json")],
                                 cwd=out, stdout=log, stderr=subprocess.STDOUT)
        try:
            while child.poll() is None:
                if cancelled():
                    raise KeyboardInterrupt
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(child.args, timeout)
                time.sleep(0.05)
        except BaseException as exc:
            # The cancellation callback can fail too. Reap this owned
            # worker before propagating an unexpected supervisor exception.
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            if not isinstance(exc, (subprocess.TimeoutExpired, KeyboardInterrupt)):
                raise
            result = {"status": "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted",
                      "error": "probe worker stopped; no input is enabled in game capture"}
        else:
            result_file = out / "result.json"
            result = json.loads(result_file.read_text()) if result_file.exists() else {
                "status": "failed", "error": "native worker exited without a result"}
            result["worker_exit"] = child.returncode
            if child.returncode != 0:
                result["status"] = "failed"
            else:
                result.setdefault("status", "measured")
    result["elapsed_s"] = time.monotonic() - started
    write_json(out / "result.json", result)
    return result


def new_run_dir():
    import tempfile
    base = ROOT / "probe-runs"
    base.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=time.strftime("maa-%Y%m%d-%H%M%S-"), dir=base))


def positive_duration(value):
    number = float(value)
    if not math.isfinite(number) or not 1 <= number <= 120:
        raise argparse.ArgumentTypeError("duration must be between 1 and 120 seconds")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("game-capture", help="observe GBFR through Maa; input is disabled")
    capture.add_argument("--visibility", choices=("focused", "unfocused", "covered"), required=True)
    capture.add_argument("--seconds", type=positive_duration, default=60)
    capture.add_argument("--settle", type=positive_duration, default=5)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.command == "_worker":
        out = args.request.resolve().parent
        try:
            native_worker(json.loads(args.request.read_text()), out)
            return 0
        except Exception as exc:
            result_file = out / "result.json"
            result = json.loads(result_file.read_text()) if result_file.exists() else {}
            result.update(status="failed", error=str(exc))
            write_json(result_file, result)
            return 1
    out = new_run_dir()
    try:
        # Same lock domain as the existing farmer. No game discovery until ownership is held.
        controls = Controls()
        server = ControlServer(controls, socket_path()).start()
        try:
            target = discover_target(GAME_APPID)
            print(f"Observation only. Arrange the game {args.visibility}; capture begins in {args.settle:g}s.", flush=True)
            controls.wait(args.settle)
            if controls.stop_requested or controls.paused:
                raise ProbeError("capture cancelled before connection")
            result = run_worker({"target": asdict(target), "mode": "capture",
                                 "seconds": args.seconds, "visibility": args.visibility},
                                out / args.visibility, timeout=args.seconds + 18,
                                cancelled=lambda: controls.stop_requested or controls.paused)
        finally:
            server.close()
        print(f"Result: {result['status']}. Private report: {out / args.visibility / 'result.json'}")
        return 0 if result["status"] == "measured" and result.get("usable_frames", 0) else 1
    except KeyboardInterrupt:
        write_json(out / "result.json", {"status": "interrupted"})
        print(f"Capture interrupted. Private report: {out / 'result.json'}")
        return 130
    except (OSError, ProbeError, RuntimeError) as exc:
        write_json(out / "result.json", {"status": "refused", "error": str(exc)})
        print(f"Probe refused: {exc}. Private report: {out / 'result.json'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
