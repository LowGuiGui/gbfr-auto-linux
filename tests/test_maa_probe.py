# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Probe guards and reports. No test may discover or control a live compositor."""

import json
import subprocess
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from tools import maa_probe as probe
from tools.maa_headless import evaluate_input


@pytest.fixture(autouse=True)
def no_real_platform(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unit test reached real process, target discovery or native controller")
    monkeypatch.setattr(probe.gamescope, "find_game_processes", forbidden)
    monkeypatch.setattr(probe.gamescope, "nearest_gamescope", forbidden)
    monkeypatch.setattr(probe.gamescope, "read_environ", forbidden)
    monkeypatch.setattr(probe, "check_target", forbidden)
    monkeypatch.setattr(probe.subprocess, "Popen", forbidden)
    monkeypatch.setattr(probe.subprocess, "run", forbidden)


@pytest.fixture
def target():
    return probe.Target(probe.TEST_APPID, 400, "10", 401, "11", "/run/user/123",
                        ":5", "/run/user/123/gamescope-7", 7, "/run/user/123/gamescope-7-ei")


@pytest.fixture
def instance():
    return SimpleNamespace(display_no=7, pipewire_node_id=90,
                           eis_socket_path="/run/user/123/gamescope-7-ei")


def node_dump(pid=400):
    return [{"id": 80, "type": "PipeWire:Interface:Client",
             "info": {"props": {"pipewire.sec.pid": pid, "application.process.id": 400}}},
            {"id": 90, "type": "PipeWire:Interface:Node", "info": {"props": {
                "node.name": "gamescope", "media.class": "Video/Source",
                "client.id": 80, "object.serial": 1234}}}]


def test_observation_disables_input_in_native_configuration(instance):
    config = probe.controller_config(instance)
    assert config["screencap_method"] == 4
    assert config["input_method"] == 0
    assert config["eis_socket_path"] == ""
    assert not config["use_win32_vk_code"]


def test_input_only_trial_is_explicit_and_separate(instance):
    config = probe.controller_config(instance, input_enabled=True, capture=False)
    assert config["input_method"] == 4
    assert config["screencap_method"] == 0
    assert config["pw_node_id"] == 0


def test_missing_capture_node_never_falls_back_to_default(instance):
    instance.pipewire_node_id = 0
    with pytest.raises(probe.ProbeError, match="no capture node"):
        probe.controller_config(instance)


def test_missing_input_socket_refused(instance):
    instance.eis_socket_path = ""
    with pytest.raises(probe.ProbeError, match="no input socket"):
        probe.controller_config(instance, input_enabled=True)


def test_selects_by_verified_instance_number_not_first(target, instance):
    other = SimpleNamespace(display_no=0, pipewire_node_id=1, eis_socket_path="/wrong")
    assert probe.select_instance(target, [other, instance]) is instance


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_socket"])
def test_ambiguous_or_mismatched_instance_refused(target, instance, kind):
    values = [] if kind == "missing" else [instance, instance] if kind == "duplicate" else [instance]
    if kind == "wrong_socket":
        instance.eis_socket_path = "/run/user/123/gamescope-0-ei"
    with pytest.raises(probe.ProbeError):
        probe.select_instance(target, values)


def test_pipewire_node_uses_authenticated_owner_and_stable_serial():
    assert probe.node_identity(node_dump(), 90, 400) == 1234
    with pytest.raises(probe.ProbeError, match="not owned"):
        probe.node_identity(node_dump(pid=999), 90, 400)


@pytest.mark.parametrize("change", ["camera", "foreign_class", "no_serial", "gone", "duplicate"])
def test_no_pipewire_fallback(change):
    dump = node_dump()
    props = dump[1]["info"]["props"]
    if change == "camera":
        props["node.name"] = "camera"
    elif change == "foreign_class":
        props["media.class"] = "Video/Sink"
    elif change == "no_serial":
        props.pop("object.serial")
    elif change == "gone":
        dump.pop()
    else:
        dump.append(dump[1])
    with pytest.raises(probe.ProbeError):
        probe.node_identity(dump, 90, 400)


def test_input_refuses_game_even_with_a_matching_receiver_file(target):
    target = replace(target, appid=probe.GAME_APPID)
    receiver = {"appid": probe.GAME_APPID, "pid": target.member_pid, "token": "test"}
    with pytest.raises(probe.ProbeError, match="restricted"):
        probe.validate_test_receiver(target, receiver, "test")


@pytest.mark.parametrize("mismatch", ["token", "pid", "ancestry", "environment"])
def test_input_requires_running_owned_receiver(target, monkeypatch, mismatch):
    receiver = {"appid": probe.TEST_APPID, "pid": target.member_pid, "token": "test"}
    monkeypatch.setattr(probe.gamescope, "nearest_gamescope", lambda pid: 999 if mismatch == "ancestry" else target.pid)
    monkeypatch.setattr(probe.gamescope, "read_environ", lambda pid: {} if mismatch == "environment" else {
        "SteamAppId": probe.TEST_APPID, "GBFR_MAA_TEST_TOKEN": "test"})
    if mismatch == "token":
        receiver["token"] = "other"
    elif mismatch == "pid":
        receiver["pid"] += 1
    with pytest.raises(probe.ProbeError):
        probe.validate_test_receiver(target, receiver, "test")


def test_input_accepts_only_owned_test_receiver(target, monkeypatch):
    monkeypatch.setattr(probe.gamescope, "nearest_gamescope", lambda pid: target.pid)
    monkeypatch.setattr(probe.gamescope, "read_environ", lambda pid: {
        "SteamAppId": probe.TEST_APPID, "GBFR_MAA_TEST_TOKEN": "test"})
    probe.validate_test_receiver(target, {"appid": probe.TEST_APPID, "pid": 401, "token": "test"}, "test")


def test_empty_discovery_refuses_before_native_work(monkeypatch):
    monkeypatch.setattr(probe.gamescope, "find_game_processes", lambda appid: [])
    with pytest.raises(probe.ProbeError, match="found 0"):
        probe.discover_target(probe.GAME_APPID)


def test_ambiguous_discovery_never_picks_one(monkeypatch):
    monkeypatch.setattr(probe.gamescope, "find_game_processes", lambda appid: [{"pid": 401}, {"pid": 501}])
    monkeypatch.setattr(probe.gamescope, "nearest_gamescope", lambda pid: pid - 1)
    with pytest.raises(probe.ProbeError, match="found 2"):
        probe.discover_target(probe.GAME_APPID)


def test_owned_pid_mismatch_refused(monkeypatch):
    monkeypatch.setattr(probe.gamescope, "find_game_processes", lambda appid: [{"pid": 401}])
    monkeypatch.setattr(probe.gamescope, "nearest_gamescope", lambda pid: 400)
    with pytest.raises(probe.ProbeError, match="not the owned"):
        probe.discover_target(probe.TEST_APPID, expected_pid=999)


def test_process_identity_parses_parentheses_in_comm(tmp_path):
    directory = tmp_path / "55"
    directory.mkdir()
    (directory / "stat").write_text("55 (test ) worker) S " + "0 " * 18 + "123456 0")
    assert probe.process_stamp(55, tmp_path) == "123456"
    with pytest.raises(probe.ProbeError):
        probe.process_stamp(56, tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "121", "nan", "inf"])
def test_capture_duration_is_bounded(value):
    with pytest.raises(Exception, match="between 1 and 120"):
        probe.positive_duration(value)


def test_blank_frames_and_duplicates_do_not_become_freshness_proof():
    frames = [{"time": 10.0, "latency_ms": 10, "blank": False, "sha256": "same"},
              {"time": 11.0, "latency_ms": 20, "blank": True, "sha256": "blank"},
              {"time": 12.0, "latency_ms": 3000, "blank": False, "sha256": "same"}]
    result = probe.summarize_frames(frames, 3)
    assert result["usable_frames"] == 2
    assert result["unique_images"] == 1
    assert result["latency_max_ms"] == 3000
    assert result["max_gap_s"] == 2
    assert result["freshness"].startswith("unverified")


def test_empty_capture_report_is_not_a_pass():
    result = probe.summarize_frames([], 1)
    assert result["usable_fps"] == 0
    assert result["latency_p50_ms"] is None


@pytest.mark.parametrize("job_type", ["stub", "maafw"])
def test_capture_records_observations_without_any_input_api(target, tmp_path, monkeypatch, job_type):
    import numpy as np

    ticks = iter(n / 10 for n in range(100))
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(probe, "check_target", lambda target: None)
    monkeypatch.setattr(probe, "read_node", lambda node, target: 1234)
    frame = np.arange(32 * 32 * 3, dtype=np.uint8).reshape(32, 32, 3)
    job = SimpleNamespace(done=True, succeeded=True, get=lambda: frame)
    if job_type == "maafw":
        # Exercise the released binding's result API without a native controller.
        maa_job = pytest.importorskip("maa.job")
        job = maa_job.JobWithResult(1, lambda _: maa_job.MaaStatusEnum.succeeded,
                                   lambda _: maa_job.MaaStatusEnum.succeeded, lambda _: frame)
    controller = SimpleNamespace(
        set_screenshot_use_raw_size=lambda raw: True,
        post_screencap=lambda: job,
    )
    result = probe.capture_frames(controller, target, 90, 1234, tmp_path, 2)
    assert result["usable_frames"] > 0
    assert len(list(tmp_path.glob("sample-*.png"))) <= 3
    assert (tmp_path / "frames.jsonl").read_text()


def test_frame_is_discarded_if_source_changes_during_capture(target, tmp_path, monkeypatch):
    import numpy as np

    serials = iter([1234, 9999])
    monkeypatch.setattr(probe, "check_target", lambda target: None)
    monkeypatch.setattr(probe, "read_node", lambda node, target: next(serials))
    controller = SimpleNamespace(
        set_screenshot_use_raw_size=lambda raw: True,
        post_screencap=lambda: SimpleNamespace(done=True, succeeded=True,
                                             get=lambda: np.ones((16, 16, 3), dtype=np.uint8)),
    )
    with pytest.raises(probe.ProbeError, match="discarding frame"):
        probe.capture_frames(controller, target, 90, 1234, tmp_path, 1)
    assert not list(tmp_path.glob("sample-*.png"))


def test_failed_job_is_not_success():
    with pytest.raises(probe.ProbeError, match="failed"):
        probe.wait_job(SimpleNamespace(done=True, succeeded=False))


def test_hung_job_has_a_deadline():
    with pytest.raises(probe.ProbeError, match="deadline"):
        probe.wait_job(SimpleNamespace(done=False), seconds=0)


def test_uncertain_key_down_is_released_on_error(target, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "check_target", lambda target: None)
    def down(key):
        calls.append(("down", key))
        return SimpleNamespace(done=True, succeeded=False)
    def up(key):
        calls.append(("up", key))
        return SimpleNamespace(done=True, succeeded=True)
    with pytest.raises(probe.ProbeError):
        probe.input_trial(SimpleNamespace(post_key_down=down, post_key_up=up), target, tmp_path, with_mouse=False)
    assert calls == [("down", 17), ("up", 17)]
    assert json.loads((tmp_path / "release.json").read_text())["failures"] == []


def test_worker_timeout_reaps_only_its_child(tmp_path, monkeypatch):
    calls = []
    class Child:
        args = ["test-worker"]
        def poll(self):
            return None
        def terminate(self):
            calls.append("terminate")
        def kill(self):
            calls.append("kill")
        def wait(self, timeout):
            calls.append("wait")
            if calls.count("wait") == 1:
                raise subprocess.TimeoutExpired(self.args, timeout)
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **kw: Child())
    result = probe.run_worker({}, tmp_path / "run", timeout=0)
    assert result["status"] == "timeout"
    assert calls == ["terminate", "wait", "kill", "wait"]


def test_receiver_requires_real_release_and_no_stranded_input(tmp_path):
    trial = tmp_path / "trial"
    trial.mkdir()
    expected = [("key_down", 25), ("key_up", 25), ("key_down", 38), ("key_up", 38),
                ("key_down", 12), ("key_up", 12), ("key_down", 25), ("key_up", 25)]
    rows = [{"event": kind, "code": key, "time": 1.01} for kind, key in expected]
    (tmp_path / "received.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    probe.write_json(trial / "release.json", {"requested": 1.0})
    probe.write_json(tmp_path / "receiver-state.json", {"keys": [0] * 32, "buttons": 0})
    assert evaluate_input(tmp_path, trial, with_mouse=False)["passed"]
    probe.write_json(tmp_path / "receiver-state.json", {"keys": [1] * 32, "buttons": 0})
    assert not evaluate_input(tmp_path, trial, with_mouse=False)["passed"]
    # Negative control: successful API writes with no received release must fail.
    probe.write_json(tmp_path / "receiver-state.json", {"keys": [0] * 32, "buttons": 0})
    (tmp_path / "received.jsonl").write_text("\n".join(json.dumps(row) for row in rows[:-1]))
    assert not evaluate_input(tmp_path, trial, with_mouse=False)["passed"]


def test_game_capture_claims_same_lock_before_discovery(tmp_path, monkeypatch):
    class BusyServer:
        def __init__(self, controls, path):
            assert path.name == "gbfr-auto-linux.sock"
        def start(self):
            raise RuntimeError("another session owns this lock")
    monkeypatch.setattr(probe, "ControlServer", BusyServer)
    monkeypatch.setattr(probe, "socket_path", lambda: tmp_path / "gbfr-auto-linux.sock")
    monkeypatch.setattr(probe, "new_run_dir", lambda: tmp_path)
    assert probe.main(["game-capture", "--visibility", "focused"]) == 2
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "refused"


def test_game_capture_cli_never_requests_input(target, tmp_path, monkeypatch):
    calls = []
    class Server:
        def __init__(self, controls, path):
            self.controls = controls
        def start(self):
            calls.append("start")
            return self
        def close(self):
            calls.append("close")
    def worker(request, out, **kwargs):
        assert request["mode"] == "capture"
        assert "receiver" not in request and "token" not in request
        calls.append("capture")
        return {"status": "measured", "usable_frames": 3}
    monkeypatch.setattr(probe, "ControlServer", Server)
    monkeypatch.setattr(probe, "socket_path", lambda: tmp_path / "gbfr-auto-linux.sock")
    monkeypatch.setattr(probe, "new_run_dir", lambda: tmp_path)
    monkeypatch.setattr(probe, "discover_target", lambda appid: replace(target, appid=appid))
    monkeypatch.setattr(probe.Controls, "wait", lambda self, seconds: None)
    monkeypatch.setattr(probe, "run_worker", worker)
    assert probe.main(["game-capture", "--visibility", "focused"]) == 0
    assert calls == ["start", "capture", "close"]


def test_target_manifest_roundtrip(target):
    assert probe.Target(**json.loads(json.dumps(asdict(target)))) == target


@pytest.mark.parametrize("needs_kill", [False, True])
def test_worker_is_reaped_when_cancellation_callback_raises(tmp_path, monkeypatch, needs_kill):
    calls = []

    class Child:
        args = ["owned-probe-worker"]
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout):
            calls.append("wait")
            if needs_kill and "kill" not in calls:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = -9 if needs_kill else -15

        def kill(self):
            calls.append("kill")

    child = Child()
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *args, **kwargs: child)

    def cancelled():
        raise AttributeError("unexpected control callback failure")

    with pytest.raises(AttributeError, match="control callback"):
        probe.run_worker({"mode": "capture"}, tmp_path / "trial", timeout=10, cancelled=cancelled)
    assert child.returncode is not None
    assert calls == (["terminate", "wait", "kill", "wait"] if needs_kill else ["terminate", "wait"])


@pytest.mark.parametrize("case", ["clear", "at deadline", "held", "repeat pair", "late",
                                  "key returns", "button returns", "missing up", "late event", "missing sample",
                                  "pre-release repeat", "repeat only", "unmatched press", "different timestamp",
                                  "stale", "future", "frozen", "counter reset", "gap", "not held", "bad event"])
def test_stop_release_requires_sustained_fresh_receiver_state(tmp_path, monkeypatch, case):
    requested = 100.0
    now = [requested]
    held = [0] * 32
    held[25 // 8] = 1 << (25 % 8)
    before = {"time": 99.95, "frame": 0, "keys": held, "buttons": 0}
    if case == "not held":
        before = {**before, "keys": [0] * 32}
    release = 1.0 if case == "at deadline" else 1.1 if case == "late" else 0.15
    rows = [{"event": "key_up", "code": 25, "time": requested + release}]
    if case == "late event":
        rows[0]["time"] += 0.03
    if case == "missing up":
        rows = []
    if case in ("pre-release repeat", "repeat only", "unmatched press", "different timestamp"):
        pair = [{"event": "key_up", "code": 25, "time": 100.05, "server_time": 1000},
                {"event": "key_down", "code": 25, "time": 100.051,
                 "server_time": 1001 if case == "different timestamp" else 1000}]
        rows = (pair[1:] if case == "unmatched press" else pair) + ([] if case == "repeat only" else rows)
    if case == "bad event":
        rows.append({"event": "key_down", "code": 25, "time": float("nan")})
    if case == "repeat pair":
        rows += [{"event": "key_down", "code": 25, "time": 100.6},
                 {"event": "key_up", "code": 25, "time": 100.61}]
    (tmp_path / "received.jsonl").write_text(''.join(json.dumps(r) + "\n" for r in rows))

    def publish():
        index = int((now[0] - requested + 1e-7) / 0.05)
        elapsed = round(index * 0.05, 6)
        state = {"time": requested + elapsed, "frame": index + 1,
                 "keys": held if elapsed < release or case == "held" else [0] * 32,
                 "buttons": 0}
        if case == "key returns" and elapsed >= 0.7:
            state["keys"] = held
        if case == "button returns" and elapsed >= 0.7:
            state["buttons"] = 512
        if case in ("stale", "future"):
            state["time"] += -5 if case == "stale" else 1
        if case == "frozen":
            state = before
        if case == "counter reset" and elapsed >= 0.7:
            state["frame"] = 0
        if case != "missing sample":
            probe.write_json(tmp_path / "receiver-state.json", state)

    def sleep(seconds):
        now[0] += 0.4 if case == "gap" and 100.5 < now[0] < 100.6 else seconds
        publish()
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probe.time, "sleep", sleep)
    publish()
    result = probe.observe_release(tmp_path / "receiver.json", requested, before)
    assert result["release_passed"] is (case in ("clear", "at deadline", "pre-release repeat")), result
    if result["release_passed"]:
        assert result["release_observed_ms"] == pytest.approx(release * 1000)
        assert result["clear_observed_ms"] >= 1200
        assert len(result["receiver_samples"]) > 20
    else:
        assert result["reason"]


@pytest.mark.parametrize("corruption", ["truncated", "keys", "buttons", "timestamp", "frame"])
def test_stop_probe_refuses_malformed_samples(tmp_path, corruption):
    state = {"time": 100.0, "frame": 10, "keys": [0] * 32, "buttons": 0}
    if corruption == "keys":
        state["keys"] = [0]
    elif corruption == "buttons":
        state["buttons"] = -1
    elif corruption == "timestamp":
        state["time"] = float("nan")
    elif corruption == "frame":
        state["frame"] = True
    path = tmp_path / "receiver-state.json"
    path.write_text('{"time":' if corruption == "truncated" else json.dumps(state))
    with pytest.raises(probe.ProbeError):
        probe.read_receiver_state(path, 100.05)


@pytest.mark.parametrize("fail_replace", [False, True])
def test_receiver_state_is_atomic_and_uses_an_owned_temporary_file(tmp_path, monkeypatch, fail_replace):
    from tools import maa_standin
    path = tmp_path / "receiver-state.json"
    path.write_text('{"frame": 1}')
    sentinel = tmp_path / "receiver-state.json.tmp"
    sentinel.write_text("leave alone")
    original = maa_standin.os.replace

    def replace(source, target):
        from pathlib import Path
        assert json.loads(path.read_text()) == {"frame": 1}
        assert json.loads(Path(source).read_text()) == {"frame": 2}
        assert Path(source).stat().st_mode & 0o777 == 0o600
        if fail_replace:
            raise OSError("replace failed")
        original(source, target)
    monkeypatch.setattr(maa_standin.os, "replace", replace)
    if fail_replace:
        with pytest.raises(OSError):
            maa_standin.write_state(path, {"frame": 2})
    else:
        maa_standin.write_state(path, {"frame": 2})
    assert json.loads(path.read_text()) == {"frame": 1 if fail_replace else 2}
    assert sentinel.read_text() == "leave alone"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name, sentinel.name]


@pytest.mark.parametrize("verdict, cleanup_fails", [(True, False), (False, False), (False, True)])
def test_native_stop_uses_receiver_verdict_and_always_attempts_explicit_up(
        tmp_path, monkeypatch, target, verdict, cleanup_fails):
    import sys
    now = [100.0]
    events = []
    held = [0] * 32
    held[3] = 2

    class Tasker:
        stops = 0

        def bind(self, *args):
            return True

        def post_task(self, name):
            events.append("task")

        def post_stop(self):
            self.stops += 1
            return "stop" + str(self.stops)

    monkeypatch.setitem(sys.modules, "maa.resource", SimpleNamespace(
        Resource=lambda: SimpleNamespace(post_bundle=lambda _: "bundle")))
    monkeypatch.setitem(sys.modules, "maa.tasker", SimpleNamespace(Tasker=Tasker))
    monkeypatch.setattr(probe, "read_receiver_state", lambda *a: {
        "time": now[0], "frame": 1, "keys": held, "buttons": 0})
    monkeypatch.setattr(probe, "check_target", lambda _: None)
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probe.time, "sleep", lambda t: now.__setitem__(0, now[0] + t))

    def observe(*args):
        events.append("observe")
        now[0] += 1.4
        return {"release_passed": verdict, "release_observed_ms": 50,
                "clear_observed_ms": 1250}

    def wait(job, seconds=6):
        events.append(job)
        if job == "stop1":
            assert "observe" in events
            now[0] += 5
        if job == "stop2" and cleanup_fails:
            raise probe.ProbeError("cleanup stop failed")
    monkeypatch.setattr(probe, "observe_release", observe)
    monkeypatch.setattr(probe, "wait_job", wait)
    controller = SimpleNamespace(post_screencap=lambda: SimpleNamespace(done=False),
                                 post_key_up=lambda code: "up")
    if cleanup_fails:
        with pytest.raises(probe.ProbeError, match="cleanup stop"):
            probe.stop_trial(controller, target, tmp_path, tmp_path / "receiver.json")
    else:
        result = probe.stop_trial(controller, target, tmp_path, tmp_path / "receiver.json")
        assert result["passed"] is verdict
        assert result["stop_complete_ms"] == pytest.approx(6400)
        assert result["release_observed_ms"] == 50
    assert events[-1] == "up"
    measurement = json.loads((tmp_path / "stop-measurement.json").read_text())
    assert measurement["release_passed"] is verdict



@pytest.mark.parametrize("keys", [(), (25,)])
@pytest.mark.parametrize("case", ["clear", "missing up", "not held", "button returns", "repeat pair"])
def test_middle_and_combined_release_require_matching_ups(tmp_path, monkeypatch, keys, case):
    now = [100.0]
    held = [0] * 32
    for key in keys:
        held[key // 8] |= 1 << (key % 8)
    before = {"time": 99.95, "frame": 0, "keys": held,
              "buttons": 0 if case == "not held" else 512}
    rows = [{"event": "key_up", "code": key, "time": 100.1} for key in keys]
    if case != "missing up":
        rows.append({"event": "button_up", "code": 2, "time": 100.1})
    if case == "repeat pair":
        rows += [{"event": "button_down", "code": 2, "time": 100.6},
                 {"event": "button_up", "code": 2, "time": 100.61}]
    (tmp_path / "received.jsonl").write_text(''.join(json.dumps(r) + "\n" for r in rows))

    def publish():
        index = int((now[0] - 100 + 1e-7) / 0.05)
        elapsed = round(index * 0.05, 6)
        state = {"time": 100 + elapsed, "frame": index + 1,
                 "keys": held if elapsed < 0.1 else [0] * 32,
                 "buttons": 512 if elapsed < 0.1 or (case == "button returns" and elapsed >= 0.7) else 0}
        probe.write_json(tmp_path / "receiver-state.json", state)

    def sleep(seconds):
        now[0] += seconds
        publish()
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probe.time, "sleep", sleep)
    publish()
    result = probe.observe_release(tmp_path / "receiver.json", 100.0, before,
                                   keys=keys, buttons=(2,))
    assert result["release_passed"] is (case == "clear"), result
    if case == "clear":
        assert result["first_button_up_ms"] == pytest.approx(100)
        assert result["release_observed_ms"] == pytest.approx(100)
        assert result["clear_observed_ms"] >= 1200



def test_receiver_drains_release_enqueued_by_state_query_before_publishing_clear(monkeypatch):
    import io
    from tools import maa_standin
    queued = []
    events = io.StringIO()
    times = iter([100.0, 100.001])
    monkeypatch.setattr(maa_standin.time, "monotonic", lambda: next(times))

    def query_keymap():
        # Mirrors Xlib reading an input event while waiting for QueryKeymap's reply.
        queued.append(SimpleNamespace(type=3, detail=25, state=0, time=5000))
        return [0] * 32
    display = SimpleNamespace(query_keymap=query_keymap,
                              pending_events=lambda: len(queued),
                              next_event=lambda: queued.pop(0))
    window = SimpleNamespace(query_pointer=lambda: SimpleNamespace(mask=0))
    state = maa_standin.sample_input(display, window, events, {3: "key_up"}, 0x1F00)
    event = json.loads(events.getvalue())
    assert event == {"event": "key_up", "code": 25, "time": 100.0, "state": 0, "server_time": 5000}
    assert not any(state["keys"]) and not state["buttons"]
    assert event["time"] <= state["time"]
    assert not queued
