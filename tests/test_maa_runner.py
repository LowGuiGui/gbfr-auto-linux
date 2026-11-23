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
import control_view
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


def test_log_rotation_leaves_folders_it_did_not_make(tmp_path):
    """The window opens this folder for the player, who may keep things in it."""
    logs = tmp_path / "logs"
    kept = logs / "20261101-manual"
    kept.mkdir(parents=True)
    (kept / "memo.txt").write_text("keep me")
    (logs / "notes").mkdir()
    made = [maa_runner.new_log_dir(logs, 5, datetime(2026, 11, 22, 21, minute)) for minute in range(7)]
    assert sorted(p.name for p in logs.iterdir()) == sorted(
        [p.name for p in made[-5:]] + ["20261101-manual", "notes"])
    assert (kept / "memo.txt").read_text() == "keep me"


def held_lock(tmp_path):
    """Another loop's session lock, held by an open file as a session would hold it."""
    path = tmp_path / control.SOCKET_NAME
    return path, control.take_lock(path)


def quiet_helper(monkeypatch, tmp_path, lock_socket):
    """The release helper in tmp_path: its runtime folder, its lock, no framework logs."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(maa_runner.control, "socket_path", lambda: lock_socket)
    monkeypatch.setattr(maa_runner, "framework_logs", lambda folder: None)


def test_the_key_release_helper_refuses_while_another_session_holds_the_lock(monkeypatch, tmp_path):
    path, fd = held_lock(tmp_path)
    quiet_helper(monkeypatch, tmp_path, path)
    monkeypatch.setattr(maa_runner, "connect_game", lambda *a, **k: pytest.fail("connected"))
    out = io.StringIO()
    try:
        assert maa_runner.release_only("881020", out) == maa_runner.EXIT_ERROR
    finally:
        control.drop_lock(fd)
    assert "正在运行" in out.getvalue()


def test_the_key_release_helper_releases_under_the_lock_and_forgets_a_crash(monkeypatch, tmp_path):
    path = tmp_path / control.SOCKET_NAME
    quiet_helper(monkeypatch, tmp_path, path)
    marker = maa_runner.Marker(maa_runner.runtime_dir() / "runner.json")
    marker.path.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))  # a crashed runner
    held = []

    def release(controller):
        with pytest.raises(control.AlreadyRunning):  # the helper holds the lock meanwhile
            control.drop_lock(control.take_lock(path))
        held.append(controller)
        return []
    monkeypatch.setattr(maa_runner, "connect_game", lambda *a, **k: "controller")
    monkeypatch.setattr(maa_runner, "release_keys", release)
    out = io.StringIO()
    assert maa_runner.release_only("881020", out) == maa_runner.EXIT_OK
    assert held == ["controller"] and not marker.path.exists()
    control.drop_lock(control.take_lock(path))  # and lets go of it afterwards


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


def test_a_rejected_finish_says_so_and_can_be_sent_again(tmp_path):
    class Tasker:
        def __init__(self, answers):
            self.answers = list(answers)

        def override_pipeline(self, task_id, override):
            assert (task_id, override) == (7, maa_runner.FINISH_OVERRIDE)
            return self.answers.pop(0)
    events = []
    runner = maa_runner.Runner(maa_options.run_values(3), lambda kind, **f: events.append((kind, f)),
                               status_path=tmp_path / "session.json", log_dir=tmp_path / "log")
    runner.tasker, runner.task_id = Tasker([False, True]), 7
    assert runner.finish() is False and not runner.finish_requested
    assert events == [("log", {"text": "本轮后停的请求没有被接受，请再试一次或者立即停止。",
                               "finish_rejected": True})]
    assert runner.finish() is True and runner.finish_requested and len(events) == 1


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_the_hooks_time_each_counted_run_and_the_current_battle(tmp_path):
    from maa_session import Session
    clock, reports = Clock(), []
    hooks = maa_runner.ReportingHooks(tmp_path / "session.json", reports.append, clock=clock)
    hooks.session = Session(None, 3)
    hooks.write_status()                         # the session begins at 1000
    clock.now, hooks.session.phase = 1030, "battle"
    hooks.battle_started = 5.0                   # the battle limit's own monotonic clock
    hooks.write_status()
    assert reports[-1]["timing"]["battle_started"] == 1030
    clock.now = 1300
    hooks.session.settle()                       # run 1 counted 300 s after the start
    hooks.write_status()
    clock.now, hooks.session.repeat = 1360, True
    hooks.session.battle()
    hooks.write_status()
    clock.now = 1580
    hooks.session.settle()                       # run 2: 280 s after run 1
    hooks.write_status()
    timing = reports[-1]["timing"]
    assert timing == {"started": 1000, "runs": [300.0, 280.0], "battle_started": None}
    assert hooks.battle_started == 5.0           # the guard's clock is left alone
    assert reports[-1]["failures"] == 0
    assert json.loads((tmp_path / "session.json").read_text())["timing"]["runs"] == [300.0, 280.0]


def test_only_a_failed_quest_counts_as_a_failure():
    assert maa_runner.quest_failed("The quest failed: the party fell")
    assert maa_runner.quest_failed("Repeat Quest could not be turned off after the quest failed")
    assert maa_runner.quest_failed("The failure page's Repeat Quest toggle could not be read")
    assert not maa_runner.quest_failed("The battle exceeded its 1800 s limit")
    assert not maa_runner.quest_failed(None)
    assert control_view.average([300.0, 280.0, 290.0]) == 290.0 and control_view.average([]) is None


def test_history_keeps_one_line_per_session_and_reads_newest_first(tmp_path):
    path, clock = tmp_path / "state" / "history.jsonl", Clock(2000.0)
    for n in range(7):
        status = {"quest": "解决沙漠狂徒", "completed": n, "limit": 10, "failures": 0,
                  "timing": {"started": 1000.0, "runs": [300.0] * n}}
        control_view.append_history(path, status, "finished", clock=clock)
    with path.open("a") as stream:
        stream.write("{broken\n")
    entries = control_view.read_history(path)
    assert [e["completed"] for e in entries] == [6, 5, 4, 3, 2]
    assert entries[0]["elapsed"] == 1000.0 and entries[0]["average"] == 300.0
    assert entries[-1]["outcome"] == "finished" and entries[0]["ended"].startswith("19")
    assert control_view.read_history(tmp_path / "missing.jsonl") == []


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


def test_a_failed_release_refuses_the_session_and_stays_remembered(monkeypatch, tmp_path, maa_ready):
    """A key the crashed runner held may still be down (Up, say), and the first key of
    the new session (Return) would not notice it: nothing starts."""
    marker = tmp_path / "runner.json"
    marker.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))
    monkeypatch.setattr(maa_runner, "release_keys", lambda controller: ["Up: Native key release failed"])
    outcome, _, events, game, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1))
    assert outcome == "error" and game.keys == []
    (error,) = kinds(events, "error")
    assert error["code"] == "release" and "Up" in error["detail"]
    assert "没有开始" in error["message"]
    (ended,) = kinds(events, "ended")
    assert ended["release_needed"] is True  # the window tries again from a fresh connection
    assert json.loads(marker.read_text())["release_pending"] is True


def test_a_crashed_runners_keys_are_released_only_under_the_session_lock(monkeypatch, tmp_path,
                                                                        maa_ready):
    marker = tmp_path / "runner.json"
    marker.write_text(json.dumps({"pid": 2 ** 22 + 7, "stamp": "1"}))
    path, fd = held_lock(tmp_path)  # another loop holds it, without answering on its socket
    monkeypatch.setattr(maa_runner, "release_keys", lambda controller: pytest.fail("released"))
    try:
        outcome, _, events, game, _ = session(monkeypatch, tmp_path, one_run(),
                                              maa_options.run_values(1), socket_path=path)
    finally:
        control.drop_lock(fd)
    assert outcome == "error" and game.keys == []
    assert kinds(events, "error")[0]["code"] == "busy"
    assert json.loads(marker.read_text())["release_pending"] is True


def test_a_key_the_guard_could_not_release_is_handed_on(monkeypatch, tmp_path, maa_ready):
    """The guard saw a key stay down after its key-up: the session ends saying so, and the
    marker stays, so the window and the next runner each send key-ups again."""
    import maa_graph

    def press(self, context, key, duration=100):
        self.release_unconfirmed = {key}
        raise ProbeError("Input release was not observed")
    monkeypatch.setattr(maa_graph.FakeGuard, "press", press)
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1))
    assert outcome == "error"
    (ended,) = kinds(events, "ended")
    assert ended["release_needed"] is True
    assert maa_runner.Marker(tmp_path / "runner.json").path.exists()


def test_a_session_that_ends_cleanly_needs_no_release(monkeypatch, tmp_path, maa_ready):
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, one_run(), maa_options.run_values(1))
    assert outcome == "finished" and kinds(events, "ended")[0]["release_needed"] is False
    assert not (tmp_path / "runner.json").exists()


def test_a_runner_that_cannot_stop_asks_for_a_release_from_a_fresh_connection(monkeypatch, tmp_path):
    """The framework did not let go within the grace time. Its key-ups on the old connection
    may have failed, or a press may still land before the process exits: either way the
    window sends key-ups again once the runner is gone, and the marker stays."""
    events = []
    runner = maa_runner.Runner(maa_options.run_values(1), lambda kind, **f: events.append((kind, f)),
                               status_path=tmp_path / "session.json", log_dir=tmp_path / "log",
                               marker=maa_runner.Marker(tmp_path / "runner.json"))
    runner.marker.claim()
    runner.controller = "controller"
    runner.stop_requested_at = time.monotonic() - maa_runner.STOP_GRACE_S - 1
    monkeypatch.setattr(maa_runner, "release_keys", lambda controller: ["Up: Native key release failed"])
    with pytest.raises(maa_runner.RunnerError) as stuck:
        runner._wait_for_end()
    assert stuck.value.code == "stuck"
    runner._fail(stuck.value)
    runner._teardown()
    (ended,) = kinds(events, "ended")
    assert ended["outcome"] == "error" and ended["release_needed"] is True
    assert (tmp_path / "runner.json").exists()


def test_a_session_reports_its_run_times_and_writes_its_history(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = [*g.first_run_turning_repeat_on(), *g.repeated_run_keeping_it_on(),
               *g.last_run_turning_it_off()]
    history = tmp_path / "history.jsonl"
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(3),
                                       history_path=history)
    assert outcome == "finished"
    (ended,) = kinds(events, "ended")
    assert len(ended["status"]["timing"]["runs"]) == 3 and ended["status"]["failures"] == 0
    # Through the real battle action: the window's battle time is wall-clock, not the
    # monotonic clock the hooks keep for their battle limit.
    battles = [f["status"]["timing"]["battle_started"] for f in kinds(events, "status")
               if f["status"].get("phase") == "battle"]
    assert battles and all(abs(started - time.time()) < 600 for started in battles), battles
    (entry,) = control_view.read_history(history)
    assert entry["outcome"] == "finished" and entry["completed"] == 3
    assert entry["average"] is not None and entry["elapsed"] is not None


def test_a_failed_quest_is_counted_as_a_failure_in_the_history(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = [*g.menus(), *g.battle(victory=False), g.screen("failed", *g.failure("off"))]
    history = tmp_path / "history.jsonl"
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(3),
                                       history_path=history)
    assert outcome == "error"
    assert kinds(events, "ended")[0]["status"]["failures"] == 1
    (entry,) = control_view.read_history(history)
    assert entry["failures"] == 1 and entry["outcome"] == "error" and entry["completed"] == 0


def test_a_count_taken_back_leaves_no_run_time_or_success(monkeypatch, tmp_path, maa_ready):
    g = graph()
    screens = [*g.menus(), *g.battle(victory=False),
               g.screen("results, failure line not drawn yet", "GBFR_See_Results",
                        leave_on="action:GBFR_Result"),
               g.screen("failed", *g.failure("off"))]
    history = tmp_path / "history.jsonl"
    outcome, _, events, _, _ = session(monkeypatch, tmp_path, screens, maa_options.run_values(3),
                                       history_path=history)
    assert outcome == "error"
    status = kinds(events, "ended")[0]["status"]
    assert status["completed"] == 0 and status["timing"]["runs"] == [] and status["failures"] == 1
    (entry,) = control_view.read_history(history)
    assert entry["completed"] == 0 and entry["average"] is None and entry["failures"] == 1


def test_a_refused_start_leaves_no_history(monkeypatch, tmp_path, maa_ready):
    def no_game():
        raise ProbeError("expected one Gamescope for app 881020; found 0")
    history = tmp_path / "history.jsonl"
    session(monkeypatch, tmp_path, [], maa_options.run_values(3), connect=no_game,
            history_path=history)
    assert not history.exists()


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


def in_run_2s_battle(event):
    status = event.get("status") or {}
    return event["event"] == "status" and status.get("phase") == "battle" \
        and status.get("completed") == 1 and status.get("episode") == 2


def test_finish_over_the_protocol_plays_the_run_out(tmp_path, maa_ready):
    process = child(tmp_path, "hold", runs="10", frame_delay=0.02)
    read_events(process, until=in_run_2s_battle)
    process.stdin.write(maa_protocol.encode_command("finish"))
    process.stdin.flush()
    events = read_events(process)
    assert process.wait(30) == 0
    assert events[-1]["event"] == "ended" and events[-1]["outcome"] == "finished"
    assert events[-1]["status"]["completed"] == 2 and events[-1]["status"]["finish_requested"]
    assert (tmp_path / "state" / "keys.txt").read_text().split() == \
        ["Return", "Return", "3", "Return", "3", "3"]


def test_closing_stdin_stops_the_session(tmp_path, maa_ready):
    process = child(tmp_path, "endless", frame_delay=0.03)
    read_events(process, until=first_count)
    started = time.monotonic()
    process.stdin.close()  # the window went away
    events = read_events(process)
    assert process.wait(30) == 0
    assert time.monotonic() - started < 10
    assert events[-1]["event"] == "ended" and events[-1]["outcome"] == "stopped"
