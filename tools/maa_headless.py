# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run Maa only against a new headless Gamescope and its instrumented receiver."""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.maa_probe import (  # noqa: E402
    TEST_APPID, ProbeError, check_target, discover_target, new_run_dir,
    positive_duration, process_stamp, run_worker, write_json, read_node,
)


def wait_file(path, child, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ProbeError("owned Gamescope exited before its receiver was ready")
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.05)
    raise ProbeError("owned Gamescope receiver did not become ready")


def evaluate_input(out, trial, *, with_mouse):
    received = [json.loads(line) for line in (out / "received.jsonl").read_text().splitlines()]
    expected = [("key_down", 25), ("key_up", 25), ("key_down", 38), ("key_up", 38),
                ("key_down", 12), ("key_up", 12)]
    if with_mouse:
        expected += [("button_down", 2), ("button_up", 2)]
    expected += [("key_down", 25), ("key_up", 25)]
    actual = [(event["event"], event["code"]) for event in received]
    releases = json.loads((trial / "release.json").read_text())
    last_release = next((e["time"] for e in reversed(received)
                         if e["event"] == "key_up" and e["code"] == 25), None)
    state = json.loads((out / "receiver-state.json").read_text())
    held = any(state["keys"]) or bool(state["buttons"])
    latency = None if last_release is None else (last_release - releases["requested"]) * 1000
    return {"expected_events": expected, "received_events": actual,
            "sequence_matches": actual == expected, "keys_or_buttons_held": held,
            "release_observed_ms": latency,
            "passed": actual == expected and not held and latency is not None and 0 <= latency < 1000}


def compare_capture(target, out):
    """Diagnostic comparison only; does not become a replacement Maa transport."""
    import cv2
    import numpy as np
    import gamescope
    from tools.linux_probe import pipewire_pipeline, run_bounded

    report = {}
    check_target(target)
    started = time.monotonic()
    try:
        frame = gamescope.capture_gamescopectl(out / "comparison.png", target.wayland,
                                              target.runtime, timeout=5)
        blank = bool(np.max(np.std(frame.astype(np.float32), axis=(0, 1))) < 1.0)
        report["gamescopectl"] = {"status": "blank" if blank else "content", "shape": list(frame.shape),
                                  "elapsed_ms": (time.monotonic() - started) * 1000}
    except Exception as exc:
        report["gamescopectl"] = {"status": "failed", "error": str(exc)}
    connection_path = out / "capture" / "connection.json"
    if connection_path.exists():
        connection = json.loads(connection_path.read_text())
        serial = read_node(connection["config"]["pw_node_id"], target)
        if serial != connection["node_serial"]:
            raise ProbeError("source changed before comparison")
        command = pipewire_pipeline(serial, str(out / "comparison-pipewire.png"))
        code, output, timed_out, _ = run_bounded(command, timeout=5, grace=1)
        (out / "gstreamer.log").write_text(output)
        frame_path = out / "comparison-pipewire.png"
        frame = cv2.imread(str(frame_path)) if frame_path.exists() else None
        report["gstreamer_pipewire"] = {"exit": code, "timed_out": timed_out,
                                       "bytes": frame_path.stat().st_size if frame_path.exists() else 0,
                                       "decodable_frame": frame is not None}
    return report


def run(seconds):
    os.umask(0o077)
    out = new_run_dir()
    token = secrets.token_hex(16)
    environment = dict(os.environ, SteamAppId=TEST_APPID, SteamGameId=TEST_APPID,
                       STEAM_COMPAT_APP_ID=TEST_APPID, GBFR_MAA_TEST_TOKEN=token,
                       PYTHONDONTWRITEBYTECODE="1")
    command = ["gamescope", "--backend", "headless", "-w", "640", "-h", "360",
               "-W", "640", "-H", "360", "-r", "30", "--", sys.executable, "-B",
               str(ROOT / "tools/maa_standin.py"), "--out", str(out), "--token", token]
    report = {"scope": "owned headless stand-in only; not game acceptance", "checks": {}}
    with (out / "gamescope.log").open("w") as log:
        child = subprocess.Popen(command, env=environment, cwd=out,
                                 stdout=log, stderr=subprocess.STDOUT)
        stamp = process_stamp(child.pid)
        try:
            receiver = wait_file(out / "receiver.json", child)
            target = discover_target(TEST_APPID, expected_pid=child.pid)
            # Use the actual receiver, not its launcher, for identity and loss checks.
            target = replace(target, member_pid=receiver["pid"],
                             member_start=process_stamp(receiver["pid"]))
            report["target"] = asdict(target)
            capture = run_worker({"target": asdict(target), "mode": "capture", "seconds": seconds},
                                 out / "capture", timeout=seconds + 18)
            report["checks"]["capture"] = capture
            # Input-only diagnostics can isolate a transport failure; they cannot pass M1.
            with_capture = capture["status"] == "measured" and capture.get("usable_frames", 0) > 0
            mode = "test-input" if with_capture else "test-input-only"
            trial_dir = out / mode
            inputs = run_worker({"target": asdict(target), "mode": mode,
                                 "receiver": str(out / "receiver.json"), "token": token},
                                trial_dir, timeout=18)
            report["checks"][mode] = inputs
            if inputs["status"] == "measured":
                time.sleep(0.15)
                report["checks"]["receiver"] = evaluate_input(out, trial_dir, with_mouse=with_capture)
            report["checks"]["busy_capture_stop"] = run_worker({
                "target": asdict(target), "mode": "test-stop",
                "receiver": str(out / "receiver.json"), "token": token},
                out / "test-stop", timeout=22)
            report["checks"]["mouse"] = {
                "status": "measured" if with_capture else "untested",
                "reason": "full controller trial" if with_capture else "capture prerequisite failed"}
            if not with_capture:
                report["checks"]["capture_comparison"] = compare_capture(target, out)
            # Close only the owned receiver. The target guard must reject its disappearance.
            check_target(target)
            os.kill(receiver["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                try:
                    check_target(target)
                except ProbeError:
                    report["checks"]["target_loss"] = {"status": "passed", "new_input": 0}
                    break
                time.sleep(0.05)
            else:
                report["checks"]["target_loss"] = {"status": "failed"}
        except (Exception, KeyboardInterrupt) as exc:
            report["error"] = str(exc) or "interrupted"
        finally:
            if child.poll() is None and process_stamp(child.pid) == stamp:
                child.terminate()
                try:
                    child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            report["owned_gamescope_reaped"] = child.poll() is not None
    checks = report["checks"]
    report["fixture_passed"] = bool(
        not report.get("error") and report.get("owned_gamescope_reaped")
        and checks.get("capture", {}).get("usable_frames", 0) > 0
        and checks.get("receiver", {}).get("passed")
        and checks.get("busy_capture_stop", {}).get("passed")
        and checks.get("target_loss", {}).get("status") == "passed")
    report["gate"] = (
        "owned fixture passed; real-game acceptance remains separate" if report["fixture_passed"]
        else "not passed; inspect failed and untested checks; game evidence still required")
    write_json(out / "report.json", report)
    print(f"Private compatibility report: {out / 'report.json'}")
    print(f"Gate: {report['gate']}")
    return 0 if report["fixture_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=positive_duration, default=10)
    return run(parser.parse_args().seconds)


if __name__ == "__main__":
    raise SystemExit(main())
