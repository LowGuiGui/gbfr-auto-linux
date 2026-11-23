#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run one farming session with MaaFramework in this process, without MXU or an Agent.

The runner finds GBFR's Gamescope, connects MaaFw's Linux controller (PipeWire capture,
libei input), loads the resource, registers the Agent's hooks on its own Resource and runs
the task that interface.json defines, with the run count given on the command line. One
process serves one session and exits when the session ends.

    .venv/bin/python maa_runner.py --runs 12          # 12 successful runs
    .venv/bin/python maa_runner.py --until-stopped    # until you stop it
    .venv/bin/python maa_runner.py --release-keys     # send key-ups for the keys it uses

While it runs, type `finish` and Enter to stop after the current run, or `stop` and
Enter (or press Ctrl+C) to stop now; the game itself never pauses. With `--json` it speaks
the line protocol of maa_protocol.py instead, which is how a front end drives it; then a
closed stdin stops the session.

While a session runs, the screen is kept from dimming, blanking and locking (GNOME's
session idle inhibitor); `--no-inhibit` turns that off.

Logs: each session gets its own folder under $XDG_STATE_HOME/gbfr-auto-linux/logs, and
only the newest few are kept. The session snapshot is written to
$XDG_RUNTIME_DIR/gbfr-auto-linux/session.json.
"""

import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import control
import maa_agent
import maa_guard
import maa_options
import maa_protocol
import maa_target
from control_view import append_history  # one line format for the runner and the window
from maa_messages import explain

GAME_APPID = "881020"
APP_DIR = "gbfr-auto-linux"
DEFAULT_LOG_KEEP = 5  # the owner's open question 6; the plan suggests 5
CONNECT_TIMEOUT_S = 10
STOP_GRACE_S = 20  # a native stop has taken 5 to 10 s (2026-10-09); give up after this.
# A front end that kills a stuck runner must wait longer than this plus the key release.
IDLE_TIMEOUT_S = 15  # after the task ends, wait this long for the tasker to go idle
OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")
FINISH_OVERRIDE = {"GBFR_Continue": {"enabled": False}}  # the panel's "finish after this run"
EXIT_OK, EXIT_ERROR, EXIT_STUCK = 0, 1, 4
INHIBIT_APP_ID = "gbfr-auto-linux"
INHIBIT_REASON = "正在自动刷本"
INTERNAL_MESSAGE = ("软件内部出错，已停止。请看一眼游戏里有没有键还按着；"
                    "日志文件夹里的记录可以帮助查出原因。")

ROOT = Path(__file__).resolve().parent

PHASES = {"idle": "等待开始", "ready": "正在接任务", "starting": "正在进入副本", "battle": "战斗中",
          "settled": "已通关，正在结算", "finished": "已完成，停在城镇",
          "stopped": "已按你的要求停止", "error": "出错，已停止"}


class RunnerError(RuntimeError):
    """The session cannot start or go on. `detail` is technical; `message` is for the player."""

    def __init__(self, code, detail, message=None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self._message = message

    @property
    def message(self):
        return self._message or explain(self.detail)


# --- where things live ---------------------------------------------------------

def state_dir(env=os.environ):
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_DIR


def runtime_dir(env=os.environ):
    base = env.get("XDG_RUNTIME_DIR")
    if not base:
        raise RunnerError("runtime", "XDG_RUNTIME_DIR is not set")
    path = Path(base) / APP_DIR
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def new_log_dir(logs, keep, now=None):
    """A fresh folder for this session's logs; only the newest `keep` sessions stay.

    A MaaFw log is written at debug level and a long session leaves about 300 MB
    (2026-11-22), so old sessions are removed rather than kept forever."""
    if keep < 1:
        raise ValueError("Keep at least the current session's logs")
    logs.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path, number = logs / stamp, 1
    while path.exists():
        path, number = logs / f"{stamp}-{number}", number + 1
    path.mkdir()
    sessions = sorted((p for p in logs.iterdir() if p.is_dir() and not p.is_symlink()),
                      key=lambda p: p.name)
    for old in sessions[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return path


def find_interface(explicit=None):
    if explicit:
        return Path(explicit)
    # The repository keeps it in assets/; a bundle keeps it beside the runner's folder.
    for candidate in (ROOT / "assets" / "interface.json", ROOT.parent / "interface.json"):
        if candidate.is_file():
            return candidate
    raise RunnerError("interface", "interface.json was not found")


def ocr_model_dir(resource_paths, explicit=None, env=os.environ):
    """The directory with the OCR model's three files, checked one by one.

    MaaFw reports success when `post_ocr_model` gets a directory without the model and
    then reads no text at all (checked 2026-11-22), so a wrong path must fail here."""
    if explicit:
        candidates = [Path(explicit)]
    else:
        candidates = [Path(env["GBFR_MAA_OCR_MODEL"])] if env.get("GBFR_MAA_OCR_MODEL") else []
        candidates += [path / "model" for path in resource_paths]
    for model in candidates:
        for directory in (model / "ocr", model):
            if all((directory / name).is_file() for name in OCR_FILES):
                return directory
    raise RunnerError("ocr", "The OCR model is incomplete: looked in "
                      + ", ".join(str(c) for c in candidates))


# --- one runner at a time --------------------------------------------------------

def session_running(socket_path):
    """True when another session answers on the control socket (an Agent under MXU, or a
    runner). The session lock in Hooks.begin still decides a race; this only refuses
    early, before anything connects to the game."""
    try:
        control.send("status", socket_path, timeout=1)
    except (OSError, ValueError):
        return False
    return True


class Marker:
    """runner.json: which process is running a session, so the next runner can tell a
    crashed one (its keys may still be held) from a live one.

    A runner that took over from a crashed one writes `release_pending` until its own
    key release has worked. If it ends before that (stopped early, no game), it leaves
    the marker behind, so the runner after it still releases the keys first."""

    def __init__(self, path):
        self.path = Path(path)

    def previous(self):
        """"alive", "crashed" or None."""
        try:
            data = json.loads(self.path.read_text())
            pid, stamp = int(data["pid"]), data["stamp"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        try:
            return "alive" if maa_target.process_stamp(pid) == stamp else "crashed"
        except maa_target.ProbeError:
            return "crashed"

    def claim(self, release_pending=False):
        temporary = self.path.with_suffix(".tmp")
        pid = os.getpid()
        temporary.write_text(json.dumps({"pid": pid, "stamp": maa_target.process_stamp(pid),
                                         "release_pending": release_pending}))
        temporary.replace(self.path)

    def clear(self):
        try:
            if json.loads(self.path.read_text()).get("pid") == os.getpid():
                self.path.unlink()
        except (OSError, ValueError, AttributeError):
            pass


# --- the game ---------------------------------------------------------------------

def connect_game(appid=GAME_APPID, *, short_side=None, capture=True, expected_pid=None):
    """MaaFw's Linux controller on GBFR's own Gamescope, and nothing else.

    The game's processes are grouped by their nearest Gamescope and exactly one group
    must exist; the capture node and the input socket then come from that one instance
    (maa_target). A second game, a restarting Gamescope or a missing game refuses here."""
    from maa.controller import LinuxController
    from maa.toolkit import Toolkit

    target = maa_target.discover_target(appid, expected_pid=expected_pid)
    instance = maa_target.select_instance(target, Toolkit.find_gamescope_instances())
    config = maa_target.controller_config(instance, input_enabled=True, capture=capture)
    controller = LinuxController(config)
    if short_side:
        controller.set_screenshot_target_short_side(short_side)
    maa_guard.wait_native(controller.post_connection(), "connection", CONNECT_TIMEOUT_S)
    return controller


def release_keys(controller):
    """Send a key-up for every key the pipeline presses; returns the failures.

    A sender that dies holding a key leaves it held, and a key-up from a new connection
    clears it (30 of 30 trials, 2026-10-09, MaaFw 5.14.2, test receiver)."""
    failures = []
    for name, code in maa_guard.KEYS.items():
        try:
            maa_guard.wait_native(controller.post_key_up(code), "key release", 3)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    return failures


# --- the session --------------------------------------------------------------------

class IdleInhibitor:
    """Keep the screen from dimming, blanking and locking while a session runs (the
    owner's choice, 2026-11-22), and never longer.

    `gnome-session-inhibit` holds a GNOME session idle inhibitor for as long as its
    command runs. The command is `cat` reading a pipe whose only writer is this process,
    so the inhibitor ends when the session closes the pipe, and also when this process
    exits or crashes, because the kernel then closes the pipe. A D-Bus inhibitor also
    ends with its client's connection. Without a GNOME session it simply fails, and the
    session goes on."""

    COMMAND = ("gnome-session-inhibit", "--app-id", INHIBIT_APP_ID, "--reason", INHIBIT_REASON,
               "--inhibit", "idle", "cat")

    def __init__(self, log_path=None, popen=None):
        self.log_path = log_path
        self._popen = popen
        self.process = None

    def start(self):
        """None when started, otherwise why not."""
        popen = self._popen or subprocess.Popen  # resolved at call time, so tests can guard it
        try:
            log = open(self.log_path, "w") if self.log_path else subprocess.DEVNULL
        except OSError:
            log = subprocess.DEVNULL
        try:
            # Its own session: a Ctrl+C in the terminal is the runner's "stop", not an exit
            # of the inhibitor; the pipe still ends it.
            self.process = popen(list(self.COMMAND), stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=log, close_fds=True,
                                 start_new_session=True)
        except OSError as exc:
            return f"gnome-session-inhibit could not start: {exc}"
        finally:
            if log is not subprocess.DEVNULL:
                log.close()
        return None

    def check(self):
        """None while the inhibitor holds, otherwise why it does not."""
        if self.process is None:
            return "not started"
        code = self.process.poll()
        if code is None:
            return None
        detail = ""
        if self.log_path:
            try:
                lines = Path(self.log_path).read_text(errors="replace").strip().splitlines()
                detail = lines[-1].strip()[:200] if lines else ""
            except OSError:
                pass
        return f"gnome-session-inhibit exited with {code}" + (f": {detail}" if detail else "")

    def stop(self, timeout=3):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()


QUEST_FAILED = ("The quest failed", "after the quest failed", "failure page's Repeat Quest")


def quest_failed(error):
    """True when a session ended because the quest itself failed (not the software)."""
    return bool(error) and any(marker in error for marker in QUEST_FAILED)


class ReportingHooks(maa_agent.Hooks):
    """The Agent's hooks, unchanged, plus timing for the window and a report each time
    they write the snapshot.

    Timing is wall-clock seconds: `started` when the session begins, one entry in `runs`
    per counted run (from the previous count, the first from the start), and
    `battle_started` while a battle is on. The Agent's own Session stays free of clocks.
    The hooks' own `battle_started` is the battle limit's monotonic clock and is never
    touched here; the window's battle time is kept apart in `battle_since`."""

    def __init__(self, status_path, report, clock=time.time):
        super().__init__(status_path)
        self._report = report
        self._clock = clock
        self.started = None
        self.runs = []
        self.battle_since = None
        self._last_count = None  # (completed, when)

    def _observe(self):
        session = self.session
        if session is None:
            return
        now = self._clock()
        if self.started is None:
            self.started = now
            self._last_count = (session.completed, now)
        completed, when = self._last_count
        if session.completed > completed:
            self.runs.append(round(now - when, 1))
            self._last_count = (session.completed, now)
        elif session.completed < completed:  # a count taken back: its run failed after all
            del self.runs[session.completed:]
            self._last_count = (session.completed, when)
        if session.phase == "battle":
            self.battle_since = self.battle_since or now
        else:
            self.battle_since = None

    def status(self):
        result = super().status()
        result["timing"] = {"started": self.started, "runs": list(self.runs),
                            "battle_started": self.battle_since}
        result["failures"] = 1 if quest_failed(result.get("error")) else 0
        return result

    def write_status(self):
        try:
            self._observe()
        except Exception:
            traceback.print_exc()
        super().write_status()
        try:
            self._report(self.status())
        except Exception:
            traceback.print_exc()


class Runner:
    """One session. `run()` blocks until it ends; `finish()` and `stop()` may come from
    any thread at any time, also before the task has started.

    `log_dir` is a folder, or a function that makes one; it is called only once nothing
    else is running, so a refused runner never pushes an older session's logs out.
    `connect` returns a connected controller and `prepare(resource)` may register extra
    recognitions and return a base override; tests use both to put a scripted game in
    place of Gamescope."""

    def __init__(self, values, emit, *, status_path, log_dir, history_path=None, interface_path=None,
                 marker=None,
                 socket_path=None, appid=GAME_APPID, ocr_model=None, connect=None, prepare=None,
                 inhibitor=None):
        self.interface_path = interface_path
        self.interface = None
        self.base = None
        self.values = values
        self.emit = emit
        self.status_path = Path(status_path)
        self.log_dir = log_dir
        self.marker = marker
        self.socket_path = socket_path
        self.appid = appid
        self.ocr_model = ocr_model
        self.connect = connect or self._connect_game
        self.prepare = prepare
        self.inhibitor = inhibitor
        self.history_path = history_path
        self.lock = threading.Lock()
        self.ended = threading.Event()
        self.outcome = None
        self.error = None
        self.task_id = None
        self.tasker = None
        self.controller = None
        self.hooks = None
        self.finish_requested = False
        self.release_pending = False  # a crashed runner's keys are not released yet
        # Keys may still be down and this runner could not make sure they are not; the
        # `ended` event says so, and the front end sends key-ups from a fresh connection.
        self.release_needed = False
        self.stop_requested_at = None

    # Called from other threads -----------------------------------------------------

    def finish(self):
        """Stop after the current run: the pipeline turns Repeat Quest off on the next
        results screen and ends in town."""
        with self.lock:
            self.finish_requested = True
            tasker, task_id = self.tasker, self.task_id
        if tasker is not None and task_id is not None:
            if not tasker.override_pipeline(task_id, FINISH_OVERRIDE):
                with self.lock:
                    self.finish_requested = False
                # The front end offers the request again on `finish_rejected`.
                self.emit("log", text="本轮后停的请求没有被接受，请再试一次或者立即停止。",
                          finish_rejected=True)
                return False
        return True

    def stop(self, reason="requested"):
        """Stop now. Input is released by MaaFw; the game itself keeps going."""
        with self.lock:
            first = self.stop_requested_at is None
            if first:
                self.stop_requested_at = time.monotonic()
            tasker = self.tasker
        if first and tasker is not None:
            tasker.post_stop()
        return True

    # The session -------------------------------------------------------------------

    def _connect_game(self):
        return connect_game(self.appid, short_side=maa_options.short_side(self.interface))

    def _check_stop(self):
        if self.stop_requested_at is not None:
            raise RunnerError("stopped", "Stopped before the session started")

    def _load_resource(self):
        from maa.resource import Resource

        paths = maa_options.resource_paths(self.interface, self.base)
        ocr = ocr_model_dir(paths, self.ocr_model)
        resource = Resource()
        resource.use_cpu()
        for path in paths:
            if not resource.post_bundle(path).wait().succeeded:
                raise RunnerError("resource", "Native resource loading failed: " + str(path))
        if not resource.post_ocr_model(ocr).wait().succeeded:
            raise RunnerError("resource", "Native OCR model loading failed: " + str(ocr))
        return resource

    def _task_sink(self):
        from maa.tasker import TaskerEventSink

        runner = self

        class Events(TaskerEventSink):
            def on_raw_notification(self, tasker, msg, details):
                if msg not in ("Tasker.Task.Succeeded", "Tasker.Task.Failed"):
                    return
                with runner.lock:
                    mine = runner.task_id is None or details.get("task_id") == runner.task_id
                if mine:  # one runner posts one task; a notice before its id is known is ours
                    runner._task_ended(msg == "Tasker.Task.Succeeded")
        return Events()

    def _focus_sink(self):
        from maa.context import ContextEventSink

        runner = self

        class Focus(ContextEventSink):
            """The node's focus text for this event: what the window shows as log lines."""

            def on_raw_notification(self, context, msg, details):
                value = (details.get("focus") or {}).get(msg)
                value = value.get("content") if isinstance(value, dict) else value
                if value:
                    runner.emit("log", text=str(value))
        return Focus()

    def _task_ended(self, succeeded):
        try:
            if self.hooks is not None:
                self.hooks.task_ended(succeeded)
        except Exception:
            traceback.print_exc()
        status = self.hooks.status() if self.hooks is not None else {"phase": "idle"}
        phase = status.get("phase")
        if phase in maa_protocol.OUTCOMES:
            outcome = phase
        elif self.stop_requested_at is not None:  # stopped before the Agent's session began
            outcome = "stopped"
            status = dict(status, phase="stopped", message=explain("Automation was stopped"))
        else:  # the task ended although the session never did; say so instead of nothing
            outcome = "error"
            reason = "Automation ended before a verified town finish"
            status = dict(status, phase="error", error=reason, message=explain(reason))
        self.outcome = outcome
        self._record(status, outcome)
        self.emit("ended", outcome=outcome, status=status, release_needed=self._needs_release())
        self.ended.set()

    def _needs_release(self):
        return self.release_needed or (self.hooks is not None and self.hooks.release_needed)

    def _record(self, status, outcome):
        """Add the session to the history; never lets that end the session badly."""
        if self.history_path is not None:
            try:
                append_history(self.history_path, status, outcome)
            except Exception:
                traceback.print_exc()

    def _fail(self, exc):
        self.error = exc
        self.outcome = "stopped" if exc.code == "stopped" else "error"
        if exc.code == "stopped":
            status = {"phase": "stopped", "completed": 0, "message": exc.message}
        else:
            self.emit("error", code=exc.code, message=exc.message, detail=exc.detail)
            status = {"phase": "error", "error": exc.detail, "message": exc.message}
            if self.task_id is not None and self.hooks is not None:  # a session that ran
                status = dict(self.hooks.status(), **status)
                self._record(status, self.outcome)
        self.emit("ended", outcome=self.outcome, status=status,
                  release_needed=self._needs_release())

    def run(self):
        """Blocking. Returns "finished", "stopped" or "error"."""
        from maa.define import LoggingLevelEnum
        from maa.tasker import Tasker

        try:
            try:
                Tasker.set_stdout_level(LoggingLevelEnum.Off)
                self.interface, self.base = maa_options.load(find_interface(self.interface_path))
                override = maa_options.build_override(self.interface, self.values)
                release_first = self._claim()
                if callable(self.log_dir):
                    self.log_dir = self.log_dir()
                Tasker.set_log_dir(Path(self.log_dir))
                Tasker.set_save_draw(False)
                self._inhibit()
                self._check_stop()
                self.controller = self.connect()
                self._check_stop()
                if release_first:
                    self._recover()
                resource = self._load_resource()
                self.hooks = ReportingHooks(self.status_path,
                                            lambda status: self.emit("status", status=status))
                action, gate = maa_agent.custom_objects(self.hooks)
                resource.register_custom_action(maa_agent.ACTION, action)
                resource.register_custom_recognition(maa_agent.GATE, gate)
                if self.prepare is not None:
                    override = maa_options.merge(self.prepare(resource), override)
                tasker = Tasker()
                if not tasker.bind(resource, self.controller):
                    raise RunnerError("bind", "Cannot bind the resource and the controller")
                tasker.add_sink(self._task_sink())
                tasker.add_context_sink(self._focus_sink())
                self._check_stop()
                with self.lock:
                    job = tasker.post_task(maa_options.entry(self.interface), override)
                    self.task_id = job.job_id
                    self.tasker = tasker
                    finish, stop = self.finish_requested, self.stop_requested_at is not None
                if stop:
                    tasker.post_stop()
                elif finish:
                    self.finish()
                self._confirm_inhibit()
                self._wait_for_end()
            except maa_options.OptionError as exc:  # already a Chinese sentence
                self._fail(RunnerError("options", str(exc), message=str(exc)))
            except maa_options.InterfaceError as exc:
                self._fail(RunnerError("interface", "interface.json: " + str(exc)))
            except maa_target.ProbeError as exc:
                self._fail(RunnerError("target", str(exc)))
            except RunnerError as exc:
                self._fail(exc)
            except Exception as exc:  # never end without telling the front end why
                traceback.print_exc()
                self._fail(RunnerError("internal", repr(exc), message=INTERNAL_MESSAGE))
        finally:
            self._teardown()
        return self.outcome

    def _claim(self):
        """Refuse when another session runs; True when a crashed runner may have left keys held."""
        if self.socket_path is not None and session_running(self.socket_path):
            raise RunnerError("busy", "Another session is already running")
        if self.marker is None:
            return False
        previous = self.marker.previous()
        if previous == "alive":
            raise RunnerError("busy", "Another session is already running (runner)")
        self.release_pending = previous == "crashed"
        self.marker.claim(release_pending=self.release_pending)
        return self.release_pending

    def _recover(self):
        """Send the key-ups a crashed runner may owe, before this session sends anything.

        The keys go out under the session lock, so they never cut into input from a
        session that started meanwhile. If any key-up fails, nothing starts: that key may
        still be down, and the checks before each key look only at the key about to be
        pressed (a held Up would not stop a Return)."""
        lock = None
        if self.socket_path is not None:
            try:
                lock = control.take_lock(self.socket_path)
            except control.AlreadyRunning as exc:
                raise RunnerError("busy", "Another session is already running: " + str(exc)) from None
        try:
            failures = release_keys(self.controller)
        finally:
            if lock is not None:
                control.drop_lock(lock)
        if failures:
            self.release_needed = True
            raise RunnerError("release", "Keys a crashed session may have held could not be "
                                         "released: " + "; ".join(failures))
        self.release_pending = False
        if self.marker is not None:
            self.marker.claim(release_pending=False)
        self.emit("log", text="上次运行没有正常结束，已先松开用到的按键。")

    def _wait_for_end(self):
        while not self.ended.wait(0.2):
            started = self.stop_requested_at
            if started is not None and time.monotonic() - started > STOP_GRACE_S:
                failures = release_keys(self.controller) if self.controller is not None else []
                # Even after key-ups that worked, a press the framework still runs may land
                # before this process exits: the front end sends key-ups again afterwards.
                self.release_needed = True
                raise RunnerError("stuck", f"The session did not stop within {STOP_GRACE_S} s"
                                  + (f"; key release failed: {failures}" if failures else ""))

    def _inhibit(self):
        if self.inhibitor is not None:
            problem = self.inhibitor.start()
            if problem:
                self._inhibit_failed(problem)

    def _confirm_inhibit(self):
        """By now (connect and resource load took a while) a failed inhibitor has exited."""
        if self.inhibitor is not None and self.inhibitor.process is not None:
            problem = self.inhibitor.check()
            if problem:
                self._inhibit_failed(problem)

    def _inhibit_failed(self, problem):
        self.emit("log", text="没能阻止熄屏和锁屏，刷本照常进行；挂机时屏幕可能会变暗、锁屏。"
                              f"（技术原因：{problem}）")

    def _teardown(self):
        tasker = self.tasker
        if tasker is not None and self.ended.is_set():
            deadline = time.monotonic() + IDLE_TIMEOUT_S
            while tasker.running and time.monotonic() < deadline:
                time.sleep(0.05)
        if self.hooks is not None:
            self.hooks.close()
        if self.inhibitor is not None:
            self.inhibitor.stop()  # the screen may sleep again
        # A runner that may leave a key down (stuck, or a key-up it could not confirm)
        # leaves its marker: the next one then treats it as crashed and releases the keys
        # first. clear() only removes a marker this process wrote.
        if self.marker is not None and not self.release_pending and not self._needs_release():
            self.marker.clear()


# --- the front end side ----------------------------------------------------------------

class Human:
    """Plain lines for a terminal."""

    def __init__(self):
        self.phase = None
        self.finish = False

    def __call__(self, kind, fields):
        if kind == "ready":
            return (f"运行器已启动（MaaFw {fields['maafw']}，Python {fields['python']}）。"
                    "输入 finish 回车：本轮后停；输入 stop 回车或按 Ctrl+C：立即停止。\n")
        if kind == "status":
            status, lines = fields["status"], []
            if status.get("phase") != self.phase:
                self.phase = status.get("phase")
                lines.append(PHASES.get(self.phase, str(self.phase)))
            if status.get("finish_requested") and not self.finish:
                self.finish = True
                lines.append("已收到本轮后停的请求")
            return "".join(line + "\n" for line in lines)
        if kind == "log":
            return fields["text"] + "\n"
        if kind == "error":
            return f"{fields['message']}\n（技术原因：{fields['detail']}）\n"
        if kind == "ended":
            status = fields.get("status") or {}
            label = {"finished": "已完成", "stopped": "已停止", "error": "出错停止"}[fields["outcome"]]
            message = status.get("message")
            text = f"结束：{label}，成功 {status.get('completed', 0)} 次。" + (f"{message}\n" if message else "\n")
            if fields.get("release_needed"):
                text += "可能还有键按着：可以运行 maa_runner.py --release-keys 补发一次松开。\n"
            return text
        return ""


class Emitter:
    """Events to the front end. MaaFw's threads only queue them and one thread writes, so a
    slow or vanished reader never blocks the framework; a failed write means nobody is
    reading any more."""

    def __init__(self, stream, *, json_mode, on_gone, capacity=10000):
        self.stream = stream
        self.format = (lambda kind, fields: maa_protocol.encode_event(kind, **fields)) \
            if json_mode else Human()
        self.on_gone = on_gone
        self.queue = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self.gone = False
        self.thread = threading.Thread(target=self._write, name="gbfr-runner-events", daemon=True)
        self.thread.start()

    def emit(self, kind, **fields):
        try:
            self.queue.put_nowait((kind, fields))
        except queue.Full:
            self.dropped += 1

    def _write(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            if self.gone:
                continue
            try:
                text = self.format(*item)
                if text:
                    self.stream.write(text)
                    self.stream.flush()
            except (OSError, ValueError):
                self.gone = True
                try:
                    self.on_gone()
                except Exception:
                    traceback.print_exc()

    def close(self, timeout=5):
        try:
            self.queue.put(None, timeout=timeout)
        except queue.Full:
            return
        self.thread.join(timeout)


def read_commands(stream, runner, emit, *, json_mode):
    """Commands from stdin until it closes; a closed stdin stops a protocol session."""
    try:
        for line in stream:
            try:
                command = maa_protocol.decode_command(line)
            except maa_protocol.ProtocolError as exc:
                emit("log", text=f"看不懂这条命令，已忽略：{line.strip()[:80]}（{exc}）")
                continue
            if command == "finish":
                runner.finish()
            elif command in ("stop", "quit"):
                runner.stop(command)
    except (OSError, ValueError):
        pass
    if json_mode:
        runner.stop("front end closed")


def protocol_stdout():
    """The real stdout for protocol lines only; everything else that writes to file
    descriptor 1 (MaaFw's native logging included) is sent to stderr instead."""
    sys.stdout.flush()
    stream = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return stream


def signal_handler(runner):
    """Ctrl+C or SIGTERM: stop. The handler runs on the main thread between two bytecodes,
    possibly while that thread holds `runner.lock`, so it must not take the lock itself;
    a short-lived thread does, once the lock is free."""
    def handle(*_):
        threading.Thread(target=runner.stop, args=("signal",), name="gbfr-runner-signal",
                         daemon=True).start()
    return handle


def maafw_version():
    try:
        from importlib.metadata import version
        return version("MaaFw").lstrip("v")
    except Exception:
        return "?"


def drive(make_runner, *, json_mode, stdin=None, stdout=None):
    """Run a session with the command reader, the event writer and the signal handlers.
    Returns the process exit code."""
    stdin = stdin if stdin is not None else sys.stdin
    if stdout is None:
        stdout = protocol_stdout() if json_mode else sys.stdout
    holder = {}
    emitter = Emitter(stdout, json_mode=json_mode,
                      on_gone=lambda: holder["runner"].stop("front end gone"))
    runner = make_runner(emitter.emit)
    holder["runner"] = runner
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, signal_handler(runner))
    threading.Thread(target=read_commands, args=(stdin, runner, emitter.emit),
                     kwargs={"json_mode": json_mode}, name="gbfr-runner-commands",
                     daemon=True).start()
    emitter.emit("ready", pid=os.getpid(), maafw=maafw_version(),
                 python=sys.version.split()[0])
    outcome = runner.run()
    emitter.close()
    if runner.error is not None and runner.error.code == "stuck":
        sys.stdout.flush()
        os._exit(EXIT_STUCK)  # the framework did not let go; do not wait for it at exit
    return EXIT_OK if outcome in ("finished", "stopped") else EXIT_ERROR


def framework_logs(folder):
    """MaaFw's log for a process that runs no session: its own folder, never a session's."""
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    folder.mkdir(parents=True, exist_ok=True)
    Tasker.set_log_dir(folder)
    Tasker.set_stdout_level(LoggingLevelEnum.Off)


def release_only(appid, out):
    """--release-keys: connect input only and send the key-ups. It holds the session lock
    while it does, so it never cuts into input from a session that is running."""
    marker = Marker(runtime_dir() / "runner.json")
    try:
        lock = control.take_lock(control.socket_path())
    except control.AlreadyRunning:
        out.write("另一个刷取正在运行，没有松键；它停下以后再试。\n")
        return EXIT_ERROR
    try:
        framework_logs(state_dir() / "release-log")
        try:
            controller = connect_game(appid, capture=False)
        except maa_target.ProbeError as exc:
            out.write(explain(str(exc)) + "\n")
            return EXIT_ERROR
        failures = release_keys(controller)
    finally:
        control.drop_lock(lock)
    if failures:
        out.write("松键时出错：" + "；".join(failures) + "\n")
        return EXIT_ERROR
    if marker.previous() == "crashed":
        marker.path.unlink(missing_ok=True)
    out.write("已对用到的按键（" + "、".join(maa_guard.KEYS) + "）各发了一次松开。\n")
    return EXIT_OK


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run one GBFR farming session with MaaFramework.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--runs", help="successful runs, 1 to 999")
    mode.add_argument("--until-stopped", action="store_true", help="farm until stopped")
    mode.add_argument("--release-keys", action="store_true",
                      help="only send key-ups for the keys the session uses")
    parser.add_argument("--json", action="store_true",
                        help="speak the line protocol on stdin/stdout (for a front end)")
    parser.add_argument("--interface", type=Path, help="interface.json (default: found beside the runner)")
    parser.add_argument("--ocr-model", type=Path,
                        help="the OCR model's directory, or a `model` directory containing `ocr/`")
    parser.add_argument("--no-inhibit", action="store_true",
                        help="do not keep the screen from dimming and locking during the session")
    parser.add_argument("--log-keep", type=int, default=DEFAULT_LOG_KEEP,
                        help=f"sessions whose logs are kept (default {DEFAULT_LOG_KEEP})")
    parser.add_argument("--appid", default=GAME_APPID, help=argparse.SUPPRESS)  # stand-in tests
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.release_keys:
            return release_only(args.appid, sys.stdout)
        runtime = runtime_dir()
    except RunnerError as exc:
        print(exc.message, file=sys.stderr)
        return EXIT_ERROR
    values = maa_options.run_values(args.runs, args.until_stopped)

    def make_runner(emit):
        return Runner(values, emit, interface_path=args.interface,
                      status_path=runtime / "session.json",
                      history_path=state_dir() / "history.jsonl",
                      log_dir=lambda: new_log_dir(state_dir() / "logs", args.log_keep),
                      marker=Marker(runtime / "runner.json"),
                      socket_path=control.socket_path(), appid=args.appid,
                      ocr_model=args.ocr_model,
                      inhibitor=None if args.no_inhibit else IdleInhibitor(runtime / "inhibit.log"))
    return drive(make_runner, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
