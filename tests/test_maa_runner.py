# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The runner: one session with MaaFw in-process, driven over a line protocol.

The first half needs nothing but Python. The second half runs the real pipeline and the
Agent's unchanged hooks inside the runner against the scripted game (maa_graph.py), and
starts the runner as a child process the way the control window will. No test reaches
Gamescope, PipeWire, X11 or the real control socket.
"""

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

import control
import maa_options
import maa_protocol
import maa_runner
from maa_target import ProbeError

ROOT = Path(__file__).resolve().parents[1]


# --- without MaaFw ------------------------------------------------------------------

def test_each_session_gets_a_log_folder_and_only_the_newest_stay(tmp_path):
    logs = tmp_path / "logs"
    made = [maa_runner.new_log_dir(logs, 5, datetime(2026, 11, 22, 21, minute)) for minute in range(7)]
    assert sorted(p.name for p in logs.iterdir()) == [p.name for p in made[-5:]]
    again = maa_runner.new_log_dir(logs, 5, datetime(2026, 11, 22, 21, 6))  # same second
    assert again.name == "20261122-210600-1" and again.exists()
    with pytest.raises(ValueError):
        maa_runner.new_log_dir(logs, 0)


def write_model(directory):
    directory.mkdir(parents=True)
    for name in maa_runner.OCR_FILES:
        (directory / name).write_text("x")


def test_the_ocr_model_is_found_or_refused_by_its_files(tmp_path):
    resource = tmp_path / "resource"
    write_model(resource / "model" / "ocr")
    assert maa_runner.ocr_model_dir([resource], env={}) == resource / "model" / "ocr"
    elsewhere = tmp_path / "model"
    write_model(elsewhere / "ocr")
    assert maa_runner.ocr_model_dir([resource], env={"GBFR_MAA_OCR_MODEL": str(elsewhere)}) \
        == elsewhere / "ocr"
    assert maa_runner.ocr_model_dir([resource], elsewhere / "ocr", env={}) == elsewhere / "ocr"
    (elsewhere / "ocr" / "rec.onnx").unlink()
    with pytest.raises(maa_runner.RunnerError, match="OCR model is incomplete"):
        # An explicit directory is never silently replaced by another one.
        maa_runner.ocr_model_dir([resource], elsewhere, env={})


def test_the_marker_tells_a_live_runner_from_a_crashed_one(tmp_path):
    marker = maa_runner.Marker(tmp_path / "runner.json")
    assert marker.previous() is None
    marker.claim()
    assert marker.previous() == "alive"  # this very process
    marker.clear()
    assert not marker.path.exists()
    marker.path.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))  # no such process
    assert marker.previous() == "crashed"
    marker.clear()  # not ours: left for the next runner to see
    assert marker.path.exists()
    marker.path.write_text("{broken")
    assert marker.previous() is None


def test_a_session_answering_on_the_control_socket_counts_as_running(tmp_path):
    path = tmp_path / control.SOCKET_NAME
    assert not maa_runner.session_running(path)
    server = control.ControlServer(control.Controls(), path).start()
    try:
        assert maa_runner.session_running(path)
    finally:
        server.close()
    assert not maa_runner.session_running(path)


class Job:
    def __init__(self, ok=True):
        self.done, self.succeeded = True, ok


class KeyController:
    def __init__(self, failing=()):
        self.released, self.failing = [], failing

    def post_key_up(self, code):
        self.released.append(code)
        return Job(code not in self.failing)


def test_release_sends_a_key_up_for_every_key_the_pipeline_presses():
    controller = KeyController(failing={103})
    failures = maa_runner.release_keys(controller)
    assert controller.released == [28, 1, 103, 4]  # Return, Escape, Up, 3: each is tried
    assert failures == ["Up: Native key release failed"]


class FakeProcess:
    def __init__(self, code=None):
        self.code, self.stdin, self.waited = code, io.BytesIO(), False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def test_the_idle_inhibitor_lives_exactly_as_long_as_its_pipe(tmp_path):
    started = []
    inhibitor = maa_runner.IdleInhibitor(tmp_path / "inhibit.log",
                                         popen=lambda argv, **kw: started.append((argv, kw)) or FakeProcess())
    assert inhibitor.start() is None
    argv, kw = started[0]
    assert argv[0] == "gnome-session-inhibit" and argv[-1] == "cat"
    assert argv[argv.index("--inhibit") + 1] == "idle"  # dimming, blanking and the idle lock
    assert kw["stdin"] == subprocess.PIPE  # only the runner writes it: its end ends the inhibitor
    assert kw["start_new_session"] and kw["stdout"] == subprocess.DEVNULL
    process = inhibitor.process
    assert inhibitor.check() is None
    inhibitor.stop()
    assert process.stdin.closed and process.waited and inhibitor.process is None
    inhibitor.stop()  # a second stop changes nothing


def test_an_inhibitor_that_cannot_hold_says_why(tmp_path):
    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", argv[0])
    assert "could not start" in maa_runner.IdleInhibitor(popen=missing).start()
    log = tmp_path / "inhibit.log"
    inhibitor = maa_runner.IdleInhibitor(log, popen=lambda argv, **kw: FakeProcess(code=1))
    inhibitor.start()
    log.write_text("Failed to inhibit: no session manager\n")
    assert inhibitor.check() == "gnome-session-inhibit exited with 1: Failed to inhibit: no session manager"


class Blocked(io.StringIO):
    """A reader that stopped reading: every write blocks until released."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def write(self, text):
        self.release.wait()
        return super().write(text)


def test_events_never_block_the_caller_when_nobody_reads():
    stream = Blocked()
    emitter = maa_runner.Emitter(stream, json_mode=True, on_gone=lambda: None, capacity=100)
    caller = threading.Thread(target=lambda: [emitter.emit("log", text=str(n)) for n in range(1000)],
                              daemon=True)
    try:
        caller.start()
        caller.join(1)
        assert not caller.is_alive(), "emit() waited for the reader"  # MaaFw's threads only queue
        assert emitter.dropped > 0
    finally:
        stream.release.set()
    emitter.close()


class Broken(io.StringIO):
    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")


def test_a_reader_that_went_away_stops_the_session_once():
    gone = []
    emitter = maa_runner.Emitter(Broken(), json_mode=True, on_gone=lambda: gone.append(1))
    for _ in range(3):
        emitter.emit("log", text="x")
    emitter.close()
    assert gone == [1]


class Recorder:
    def __init__(self):
        self.calls = []

    def finish(self):
        self.calls.append("finish")

    def stop(self, reason="requested"):
        self.calls.append("stop:" + reason)


@pytest.mark.parametrize("json_mode, expected", [(True, ["finish", "stop:stop", "stop:front end closed"]),
                                                  (False, ["finish", "stop:stop"])])
def test_commands_and_a_closed_stdin(json_mode, expected):
    runner, events = Recorder(), []
    lines = io.StringIO('finish\n{"v": 1, "cmd": "pause"}\n\n{"v": 1, "cmd": "stop"}\n')
    maa_runner.read_commands(lines, runner, lambda kind, **f: events.append((kind, f)),
                             json_mode=json_mode)
    assert runner.calls == expected  # a closed terminal is not "stop"; a closed pipe is
    assert events and events[0][0] == "log" and "看不懂" in events[0][1]["text"]


def test_the_terminal_shows_phases_once_and_a_closing_line():
    human = maa_runner.Human()
    assert "战斗中" in human("status", {"status": {"phase": "battle"}})
    assert human("status", {"status": {"phase": "battle"}}) == ""
    assert "本轮后停" in human("status", {"status": {"phase": "battle", "finish_requested": True}})
    line = human("ended", {"outcome": "stopped", "status": {"completed": 3, "message": "已按要求停止。"}})
    assert line == "结束：已停止，成功 3 次。已按要求停止。\n"


class LockedRunner:
    """A runner whose lock is held: stop() reports a deadlock instead of hanging."""

    def __init__(self):
        self.lock, self.stopped, self.deadlocked = threading.Lock(), threading.Event(), False

    def stop(self, reason="requested"):
        if not self.lock.acquire(timeout=1):
            self.deadlocked = True
            return
        try:
            self.stopped.set()
        finally:
            self.lock.release()


def test_the_signal_handler_never_waits_for_the_runners_lock():
    import signal
    runner = LockedRunner()
    handler = maa_runner.signal_handler(runner)
    with runner.lock:  # a signal landing while the main thread holds the lock (post_task)
        handler(signal.SIGINT, None)
        assert not runner.deadlocked
    assert runner.stopped.wait(3) and not runner.deadlocked


def test_exactly_one_mode_is_required():
    for argv in ([], ["--runs", "3", "--until-stopped"], ["--runs", "3", "--release-keys"]):
        with pytest.raises(SystemExit):
            maa_runner.parse_args(argv)
    assert maa_runner.parse_args(["--runs", "3"]).log_keep == maa_runner.DEFAULT_LOG_KEEP


# --- with MaaFw: the real pipeline and hooks against the scripted game ----------------

@pytest.fixture
def maa_ready():
    pytest.importorskip("maa.tasker", reason="MaaFw is not installed")
    model = os.environ.get("GBFR_MAA_OCR_MODEL")
    try:
        maa_runner.ocr_model_dir([], Path(model) if model else None, env={})
    except (maa_runner.RunnerError, TypeError):
        pytest.skip("No OCR model: set GBFR_MAA_OCR_MODEL (the runner refuses to start without it)")


def graph():
    import test_maa_pipeline_graph
    return test_maa_pipeline_graph


def session(monkeypatch, tmp_path, screens, values, *, frame_delay=0.0, connect=None, **options):
    """Run one in-process session; finish or stop when a scripted screen asks for it."""
    import maa_graph

    game = maa_graph.ScriptedGame(screens)
    scripted_connect, prepare = maa_graph.runner_parts(monkeypatch, game, frame_delay=frame_delay)
    events, connects = [], []

    def counting_connect():
        connects.append(1)
        return (connect or scripted_connect)()

    options.setdefault("log_dir", tmp_path / "log")
    runner = maa_runner.Runner(values, lambda kind, **f: events.append((kind, f)),
                               status_path=tmp_path / "session.json",
                               marker=maa_runner.Marker(tmp_path / "runner.json"),
                               connect=counting_connect, prepare=prepare, **options)

    def enter(current):  # as the window would: from another thread, while the screen shows
        if current.finish:
            threading.Thread(target=runner.finish).start()
        if current.stop:
            threading.Thread(target=runner.stop).start()
    game.on_enter = enter
    outcome = runner.run()
    return outcome, runner, events, game, connects


def kinds(events, kind):
    return [fields for name, fields in events if name == kind]


def test_twelve_runs_end_in_town_with_the_eight_expected_keys(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = g.first_run_turning_repeat_on()
    for _ in range(8):
        screens += g.repeated_run_keeping_it_on()
    screens += [*g.tenth_run_answered_yes(), *g.repeated_run_keeping_it_on(),
                *g.last_run_turning_it_off()]
    outcome, runner, events, game, _ = session(monkeypatch, tmp_path, screens,
                                               maa_options.run_values(12))
    assert outcome == "finished"
    assert game.keys == g.NAV_KEYS + ["3", "Up", "Return", "3"]
    assert all(kind != "native-input" for _, kind in game.log)
    logs = [f["text"] for f in kinds(events, "log")]
    assert [t for t in logs if "次完成" in t] == [f"第 {n}/12 次完成" for n in range(1, 13)]
    assert logs[-1] == "已结束，游戏停在城镇"
    (ended,) = kinds(events, "ended")
    assert ended["outcome"] == "finished" and ended["status"]["completed"] == 12
    assert json.loads((tmp_path / "session.json").read_text())["phase"] == "finished"
    assert not (tmp_path / "runner.json").exists()  # the marker goes with a clean end
    assert not kinds(events, "error")


def test_finish_during_battle_ends_after_that_run(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = [*g.first_run_turning_repeat_on(), *g.battle(victory=False, finish=True), g.titles(),
               g.rewards("on", "key:3"), g.rewards("off", "action:GBFR_LetItEnd", after=2), g.town()]
    outcome, _, events, game, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(10),
                                          frame_delay=0.02)
    assert outcome == "finished"
    (ended,) = kinds(events, "ended")
    assert ended["status"]["completed"] == 2 and ended["status"]["finish_requested"]
    assert game.keys == g.NAV_KEYS + ["3", "3"]


def test_stop_now_is_a_stop_with_the_repeat_state_explained(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = [*g.first_run_turning_repeat_on(),
               g.screen("battle, stopped", "GBFR_See_Battle", stop=True)]
    outcome, _, events, game, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(10),
                                          frame_delay=0.02)
    assert outcome == "stopped"
    (ended,) = kinds(events, "ended")
    assert ended["status"]["phase"] == "stopped" and ended["status"]["completed"] == 1
    assert "已按要求停止" in ended["status"]["message"] and "还开着" in ended["status"]["message"]
    assert game.keys == g.NAV_KEYS + ["3"]


def test_a_stop_before_the_task_starts_connects_to_nothing(monkeypatch, tmp_path, maa_ready):
    import maa_graph

    game = maa_graph.ScriptedGame([])
    connect, prepare = maa_graph.runner_parts(monkeypatch, game)
    events, connects = [], []
    runner = maa_runner.Runner(maa_options.run_values(3), lambda kind, **f: events.append((kind, f)),
                               status_path=tmp_path / "s.json", log_dir=tmp_path / "log",
                               connect=lambda: connects.append(1), prepare=prepare)
    runner.stop()
    assert runner.run() == "stopped"
    assert connects == [] and game.keys == []
    (ended,) = kinds(events, "ended")
    assert "还没有开始刷" in ended["status"]["message"]
    assert not kinds(events, "error")


def test_no_game_is_explained_before_anything_connects(monkeypatch, tmp_path, maa_ready):
    def no_game():
        raise ProbeError("expected one Gamescope for app 881020; found 0")
    outcome, _, events, game, _ = session(monkeypatch, tmp_path, [], maa_options.run_values(3),
                                          connect=no_game)
    assert outcome == "error"
    (error,) = kinds(events, "error")
    assert error["code"] == "target" and "没有找到正在运行的游戏" in error["message"]
    assert kinds(events, "ended")[0]["outcome"] == "error"
    assert game.keys == []


def test_a_bad_run_count_is_refused_before_connecting(monkeypatch, tmp_path, maa_ready):
    outcome, _, events, _, connects = session(monkeypatch, tmp_path, [], maa_options.run_values("0"))
    assert outcome == "error" and connects == []
    assert "1 到 999" in kinds(events, "error")[0]["message"]


def test_a_running_session_refuses_a_second_one(monkeypatch, tmp_path, maa_ready):
    path = tmp_path / control.SOCKET_NAME
    server = control.ControlServer(control.Controls(), path).start()
    made = []
    try:
        outcome, _, events, _, connects = session(
            monkeypatch, tmp_path, [], maa_options.run_values(3), socket_path=path,
            log_dir=lambda: made.append(1) or tmp_path / "log")
    finally:
        server.close()
    assert outcome == "error" and connects == []
    assert kinds(events, "error")[0]["code"] == "busy"
    assert made == []  # a refused runner makes no log folder, so it evicts no older one


class FakeInhibitor:
    def __init__(self, fail_check=None):
        self.calls, self.process, self.fail_check = [], None, fail_check

    def start(self):
        self.calls.append("start")
        self.process = object()

    def check(self):
        self.calls.append("check")
        return self.fail_check

    def stop(self):
        self.calls.append("stop")
        self.process = None


def one_run():
    g = graph()
    return [*g.menus(), *g.battle(victory=True), g.titles(),
            g.rewards("off", "action:GBFR_LetItEnd", after=2), g.town()]


def test_the_screen_is_kept_awake_only_while_a_session_runs(monkeypatch, tmp_path, maa_ready):
    inhibitor = FakeInhibitor()
    outcome, _, _, _, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1),
                                  inhibitor=inhibitor)
    assert outcome == "finished" and inhibitor.calls == ["start", "check", "stop"]


def test_the_inhibitor_ends_with_a_failed_session_and_never_starts_for_a_refused_one(
        monkeypatch, tmp_path, maa_ready):
    def no_game():
        raise ProbeError("expected one Gamescope for app 881020; found 0")
    failed = FakeInhibitor()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    session(monkeypatch, tmp_path / "a", [], maa_options.run_values(1), connect=no_game, inhibitor=failed)
    assert failed.calls == ["start", "stop"]
    path = tmp_path / control.SOCKET_NAME
    server = control.ControlServer(control.Controls(), path).start()
    refused = FakeInhibitor()
    try:
        session(monkeypatch, tmp_path / "b", [], maa_options.run_values(1), socket_path=path,
                inhibitor=refused)
    finally:
        server.close()
    assert "start" not in refused.calls  # (the teardown's stop() of nothing is harmless)


def test_a_screen_that_cannot_be_kept_awake_does_not_stop_the_farming(monkeypatch, tmp_path, maa_ready):
    inhibitor = FakeInhibitor(fail_check="gnome-session-inhibit exited with 1")
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1),
                                       inhibitor=inhibitor)
    assert outcome == "finished"
    assert any("没能阻止熄屏和锁屏" in f["text"] for f in kinds(events, "log"))


def test_an_unexpected_failure_still_ends_with_a_reason(monkeypatch, tmp_path, maa_ready):
    def broken():
        raise RuntimeError("Failed to create Linux controller.")
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, [], maa_options.run_values(3),
                                       connect=broken)
    assert outcome == "error"
    (error,) = kinds(events, "error")
    assert error["code"] == "internal" and error["message"] == maa_runner.INTERNAL_MESSAGE
    assert "Failed to create Linux controller" in error["detail"]
    assert kinds(events, "ended")[0]["outcome"] == "error"


def test_keys_are_released_first_after_a_crashed_runner(monkeypatch, tmp_path, maa_ready):
    g = graph()
    (tmp_path / "runner.json").write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))
    released = []
    monkeypatch.setattr(maa_runner, "release_keys", lambda controller: released.append(controller) or [])
    screens = [*g.menus(), *g.battle(victory=True), g.titles(),
               g.rewards("off", "action:GBFR_LetItEnd", after=2), g.town()]
    outcome, _, events, game, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(1))
    assert outcome == "finished" and len(released) == 1
    assert "上次运行没有正常结束" in kinds(events, "log")[0]["text"]
    assert not (tmp_path / "runner.json").exists()


def test_a_crash_stays_remembered_until_its_keys_are_released(monkeypatch, tmp_path, maa_ready):
    marker = tmp_path / "runner.json"
    marker.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))  # a crashed runner

    def no_game():
        raise ProbeError("expected one Gamescope for app 881020; found 0")
    session(monkeypatch, tmp_path, [], maa_options.run_values(1), connect=no_game)
    data = json.loads(marker.read_text())
    # Left behind with this process's id: once it exits, the next runner sees a crash.
    assert data["release_pending"] is True and data["pid"] == os.getpid()


def test_a_failed_release_also_stays_remembered(monkeypatch, tmp_path, maa_ready):
    marker = tmp_path / "runner.json"
    marker.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))
    monkeypatch.setattr(maa_runner, "release_keys", lambda controller: ["Up: Native key release failed"])
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1))
    assert outcome == "finished"
    assert "松键时出错" in kinds(events, "log")[0]["text"]
    assert json.loads(marker.read_text())["release_pending"] is True


# --- with MaaFw: the runner as a child process, over the protocol -----------------------

def child(tmp_path, scenario, *, runs="12", frame_delay=0.0):
    return subprocess.Popen(
        [sys.executable, "-B", str(ROOT / "tests" / "runner_scripted.py"), "--scenario", scenario,
         "--runs", runs, "--state", str(tmp_path / "state"), "--frame-delay", str(frame_delay)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path), XDG_STATE_HOME=str(tmp_path / "xdg")))


def read_events(process, until=None, timeout=60):
    """Every stdout line must be a protocol event; stop after the event `until` matches.

    A reader thread keeps a hung child from hanging the test."""
    import queue

    lines = getattr(process, "lines", None)
    if lines is None:
        lines = process.lines = queue.Queue()

        def pump():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        threading.Thread(target=pump, daemon=True).start()
    events, deadline = [], time.monotonic() + timeout
    while True:
        line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
        if line is None:
            return events
        events.append(maa_protocol.decode_event(line))
        if until is not None and until(events[-1]):
            return events


def test_a_protocol_session_from_ready_to_ended(tmp_path, maa_ready):
    process = child(tmp_path, "twelve")
    events = read_events(process)
    code = process.wait(30)
    assert events[0]["event"] == "ready" and events[0]["maafw"] not in ("", "?")
    assert events[-1]["event"] == "ended" and events[-1]["outcome"] == "finished"
    assert events[-1]["status"]["completed"] == 12
    assert any(e["event"] == "status" for e in events)
    assert code == 0
    assert (tmp_path / "state" / "keys.txt").read_text().split() == \
        ["Return", "Return", "3", "Return", "3", "Up", "Return", "3"]


def first_count(event):
    return event["event"] == "log" and event["text"] == "第 1/12 次完成"


def test_a_stop_command_over_the_protocol(tmp_path, maa_ready):
    process = child(tmp_path, "endless", frame_delay=0.03)
    read_events(process, until=first_count)
    process.stdin.write(maa_protocol.encode_command("stop"))
    process.stdin.flush()
    events = read_events(process)
    assert process.wait(30) == 0
    assert events[-1]["event"] == "ended" and events[-1]["outcome"] == "stopped"
    assert 1 <= events[-1]["status"]["completed"] <= 3


def test_closing_stdin_stops_the_session(tmp_path, maa_ready):
    process = child(tmp_path, "endless", frame_delay=0.03)
    read_events(process, until=first_count)
    started = time.monotonic()
    process.stdin.close()  # the window went away
    events = read_events(process)
    assert process.wait(30) == 0
    assert time.monotonic() - started < 10
    assert events[-1]["event"] == "ended" and events[-1]["outcome"] == "stopped"
