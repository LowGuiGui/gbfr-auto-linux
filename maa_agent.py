#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Official Maa Agent hooks for bounded input, ownership and run accounting.

Recognition, navigation and scheduling stay in the native resource pipeline. The
Agent answers one question for it (`GBFRSession`: should the session go on or stop?)
and records what the pipeline observed.
"""

import json
from pathlib import Path
import sys
import time
import traceback

from control import Controls, ControlServer, socket_path
from maa_guard import FrameUnavailable, NativeGuard
from maa_messages import describe
from maa_session import ENDED, Session, SessionError

# Events whose screen is rechecked on a fresh frame before the session believes it.
OBSERVED = {"battle", "settle"}
# Events that also re-verify the game process and capture source (R4: not per key).
IDENTITY = {"admit", "settle"}
# Reads only: a failed quest's results page, and the quest name beside the title.
FAILED_PAGE = "GBFR_See_Defeat"
QUEST_NAME = "GBFR_Read_Quest"
# Every failure ends the task through this node; MXU's log shows its focus message.
STOPPED_NODE = "GBFR_Stopped"


class Stopped(SessionError):
    """The player or the client stopped the automation. Not an error."""


class Hooks:
    def __init__(self, status_path):
        self.status_path = Path(status_path)
        self.session = None
        self.guard = None
        self.owner = None
        self.controls = None
        self.calls = {}
        self.battle_started = None
        self.battle_limit = 600
        self.task_id = None
        self.last_node = None
        self.startup_error = None  # a failure before the session existed

    def status(self):
        if self.session:
            result = self.session.snapshot()
        elif self.startup_error:
            result = {"phase": "error", "error": self.startup_error}
        else:
            result = {"phase": "idle"}
        return dict(result, message=describe(result), node=self.last_node, task_id=self.task_id)

    def progress(self):
        session = self.session
        if session.limit is None:
            return f"已完成 {session.completed} 次（一直刷，等你停止）"
        return f"第 {session.completed}/{session.limit} 次完成"

    def write_status(self):
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.status(), ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.status_path)

    def close(self):
        """Release the guard and the control socket. Never raises."""
        guard, self.guard = self.guard, None
        owner, self.owner = self.owner, None
        for item in (guard, owner):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    traceback.print_exc()

    def begin(self, context, params):
        self.close()
        self.session = None
        self.startup_error = None
        config = context.get_node_data("GBFR_QuestConfig")["attach"]
        limit = context.get_node_data("GBFR_RunLimit")["attach"]["limit"]
        if type(limit) is str and limit.isdecimal():
            limit = int(limit)  # the client's int input may arrive as a string
        # A flag node, like GBFR_Continue: the "until I stop it" switch enables it.
        until_stopped = (context.get_node_data("GBFR_UntilStopped") or {}).get("enabled") is True
        self.session = Session(None, None if until_stopped else limit)
        self.battle_limit = config.get("battle_limit", 1800)
        self.calls = {}
        self.battle_started = None
        self.controls = Controls()
        self.owner = ControlServer(self.controls, socket_path(), self.status).start()
        self.guard = NativeGuard(context.tasker.controller)

    def _finish_flag(self, context):
        continue_node = context.get_node_data("GBFR_Continue")
        if not continue_node or not continue_node.get("enabled", True):
            self.session.finish_requested = True

    def want(self, context, param):
        """Answer the pipeline's GBFRSession gate; never raises, never sends input."""
        if self.session is None or self.session.error or self.guard is None:
            return False
        try:
            self._finish_flag(context)
        except Exception:
            return False
        session = self.session
        want = json.loads(param or "{}").get("want")
        return {
            "continue": session.should_continue and not session.cap_seen,
            "cap": session.should_continue and session.cap_seen,
            "go_on": session.should_continue,
            "settled": session.phase == "settled",
            "stop": not session.should_continue,
        }.get(want, False)

    def _recheck(self, context, node):
        """The newly captured frame on which the node's own recognition hit again, or None.

        A screenshot from an earlier transition never authorizes input or a count."""
        for attempt in range(3):
            try:
                frame = self.guard.capture()
                recognition = context.run_recognition(node, frame)
            except FrameUnavailable:
                recognition = None
            if recognition is not None and recognition.hit:
                return frame
            if attempt < 2:
                time.sleep(.2)
        return None

    @staticmethod
    def _sees(context, node, frame):
        recognition = context.run_recognition(node, frame)
        return recognition is not None and recognition.hit

    def _read_quest(self, context, frame):
        """The quest's name for the control panel, read once; a failed read changes nothing."""
        if self.session.quest:
            return
        try:
            recognition = context.run_recognition(QUEST_NAME, frame)
            text = recognition.best_result.text.strip() if recognition and recognition.hit else ""
        except Exception:
            text = ""
        self.session.quest = text or None

    def run(self, context, argv):
        """One custom action. Never raises: MaaFw reported an exception that escaped
        this callback as a *successful* action, and the pipeline went on as if nothing
        had failed. Every failure comes back as False instead."""
        try:
            return self._run(context, argv)
        except Exception as exc:
            try:
                self._failed(context, exc)
            except Exception:
                traceback.print_exc()
                self.close()
            return False

    def _diagnose(self, exc):
        """Name a vanished game or Gamescope as the cause, whatever failed first because of it."""
        if self.guard is not None and not isinstance(exc, Stopped):
            lost = self.guard.lost()
            if lost:
                return f"The game or its Gamescope closed during the session ({lost})"
        return str(exc)

    def _failed(self, context, exc):
        try:
            reason = self._diagnose(exc)
            # Keep the first outcome: after a failed action the pipeline's on_error
            # nodes call in again, and their reason would hide the real cause.
            if self.session is None:
                self.startup_error = self.startup_error or reason
            elif self.session.phase not in ENDED:
                if isinstance(exc, Stopped):
                    self.session.stop()
                else:
                    self.session.fail(reason)
            self.write_status()
            message = self.status()["message"]
            if message:
                context.override_pipeline({STOPPED_NODE: {"focus": {"Node.Action.Failed": message}}})
        finally:
            self.close()

    def task_ended(self, succeeded):
        """The client's task ended. MaaFw reports a stopped task as succeeded, and the
        pipeline's only successful end is GBFR_Finished, so an unfinished session whose
        task succeeded was stopped by the player."""
        if self.session and self.session.phase not in ENDED:
            if succeeded:
                self.session.stop()
            else:
                self.session.fail("Automation ended before a verified town finish")
            self.write_status()
        self.close()

    def _run(self, context, argv):
        self.last_node = argv.node_name
        params = json.loads(argv.custom_action_param)
        event = params.get("event")
        if event == "halt":
            return False  # GBFR_Stopped: the task ends failed, after MXU shows why
        if event == "begin":
            self.task_id = argv.task_detail.task_id  # first, so a failed begin still ends cleanly
            self.begin(context, params)
            self.write_status()
            return True
        if self.guard is None or self.session is None:
            raise SessionError("Start a new session from the quest's details page or ready room")
        # Agent proxy handles belong to the current callback context.
        # Keep process/socket identity across callbacks, never its proxy.
        self.guard.controller = context.tasker.controller
        if event in IDENTITY:
            self.guard.check()
        self._finish_flag(context)
        self.calls[argv.node_name] = self.calls.get(argv.node_name, 0) + 1
        if self.calls[argv.node_name] > params.get("budget", 20):
            raise SessionError(params.get("exhausted",
                                          "Screen transition budget exhausted: " + argv.node_name))
        if self.controls.stop_requested or self.controls.paused or context.tasker.stopping:
            raise Stopped("Automation was stopped")
        frame = None
        if "key" in params or event in OBSERVED:
            frame = self._recheck(context, argv.node_name)
            if frame is None:
                if "key" in params and params.get("on_change") != "skip":
                    raise SessionError("The recognized screen changed before its action")
                # Combat HUDs drop during camera transitions, and the tenth-run dialog
                # can cover the results page. Nothing is pressed or counted from the
                # old frame; the native next list inspects the screen again (a missed
                # count retries GBFR_Result, because GBFR_Counted needs a settled run).
                self.write_status()
                return True
        if event == "settle" and self._sees(context, FAILED_PAGE, frame):
            # A failed quest's page also shows the results title. MaaFw's And ignores
            # `inverse` on a referenced node, so the results node cannot exclude it;
            # it is never counted, and the next list's failure nodes take the page.
            context.override_pipeline({"GBFR_Counted": {"focus": {"Node.Action.Succeeded": ""}}})
            self.write_status()
            return True
        if event == "admit":
            if self.session.finish_requested:
                if not context.override_pipeline({argv.node_name: {"next": ["GBFR_CancelReady"]}}):
                    raise SessionError("Could not stop before starting the quest")
                self.write_status()
                return True
            self.session.admit()
        if "key" in params:
            self.guard.press(context, params["key"], params.get("duration", 100))
        if event == "battle":
            self.session.battle()
            self.battle_started = self.battle_started or time.monotonic()
            if time.monotonic() - self.battle_started > self.battle_limit:
                raise SessionError(f"The battle exceeded its {self.battle_limit} s limit")
        elif event == "victory":
            if self.session.phase != "battle":
                raise SessionError("A victory screen appeared without an observed battle")
        elif event == "settle":
            self.session.settle()
            self.calls = {}
            self.battle_started = None
            self._read_quest(context, frame)
            # Shown in the client's log as the pipeline passes GBFR_Counted.
            context.override_pipeline({"GBFR_Counted": {"focus": {
                "Node.Action.Succeeded": self.progress()}}})
        elif event == "repeat_on":
            self.session.repeat_on()
        elif event == "repeat_off":
            self.session.repeat_off()
        elif event == "tenth_run":
            self.session.tenth_run()
        elif event == "tenth_yes":
            self.session.answer_yes()
        elif event == "toggle":
            self.session.toggle_sent()
        elif event == "finish":
            self.session.finish()
            self.close()
        elif event == "failed":
            # GBFR_Defeat matched only with the failure page's toggle reading off.
            self.session.left_on = False
            raise SessionError(params["reason"])
        elif event == "error":
            raise SessionError(params["reason"])
        self.write_status()
        return True


def task_sink(hooks):
    """Tell the hooks when the client's task ends: finished, stopped or failed."""
    from maa.tasker import TaskerEventSink

    class Events(TaskerEventSink):
        def on_raw_notification(self, tasker, msg, details):
            if details.get("task_id") != hooks.task_id:
                return
            if msg in {"Tasker.Task.Failed", "Tasker.Task.Succeeded"}:
                try:
                    hooks.task_ended(msg == "Tasker.Task.Succeeded")
                except Exception:
                    traceback.print_exc()
    return Events()


def main():
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition

    hooks = Hooks(Path(__file__).resolve().parent / "runtime" / "session.json")

    class Action(CustomAction):
        def run(self, context, argv):
            return hooks.run(context, argv)

    class Gate(CustomRecognition):
        def analyze(self, context, argv):
            return (0, 0, 1, 1) if hooks.want(context, argv.custom_recognition_param) else None

    AgentServer.register_custom_action("GBFR", Action())
    AgentServer.register_custom_recognition("GBFRSession", Gate())

    AgentServer.add_tasker_sink(task_sink(hooks))
    try:
        if not AgentServer.start_up(sys.argv[-1]):
            raise RuntimeError("Could not connect the GBFR Agent")
        AgentServer.join()
    finally:
        hooks.close()
        AgentServer.shut_down()


if __name__ == "__main__":
    main()
