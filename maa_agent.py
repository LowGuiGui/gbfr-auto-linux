#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Official Maa Agent hooks for bounded input, ownership and run accounting.

Recognition, navigation and scheduling remain in the native resource pipeline.
"""

import json
from pathlib import Path
import sys
import time

from control import Controls, ControlServer, socket_path
from maa_guard import FrameUnavailable, NativeGuard
from maa_session import Session, SessionError


class Hooks:
    def __init__(self, status_path):
        self.status_path = Path(status_path)
        self.session = None
        self.guard = None
        self.owner = None
        self.controls = None
        self.calls = {}
        self.battle_started = None
        self.task_id = None
        self.last_node = None

    def status(self):
        result = self.session.snapshot() if self.session else {"phase": "idle"}
        return dict(result, node=self.last_node, task_id=self.task_id)

    def write_status(self):
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.status(), ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.status_path)

    def close(self):
        if self.guard is not None:
            self.guard.close()
            self.guard = None
        if self.owner is not None:
            self.owner.close()
            self.owner = None

    def begin(self, context, params):
        self.close()
        quest = context.get_node_data("GBFR_QuestConfig")["attach"]["quest"]
        limit = context.get_node_data("GBFR_RunLimit")["attach"]["limit"]
        self.session = Session(params.get("quest", quest), params.get("limit", limit))
        self.calls = {}
        self.battle_started = None
        self.controls = Controls()
        self.owner = ControlServer(self.controls, socket_path(), self.status).start()
        self.guard = NativeGuard(context.tasker.controller)

    def run(self, context, argv):
        try:
            self.last_node = argv.node_name
            params = json.loads(argv.custom_action_param)
            event = params.get("event")
            if event == "begin":
                self.begin(context, params)
                self.task_id = argv.task_detail.task_id
            elif self.guard is None or self.session is None:
                raise SessionError("Start a new session from a supported town screen")
            else:
                # Agent proxy handles belong to the current callback context.
                # Keep process/socket identity across callbacks, never its proxy.
                self.guard.controller = context.tasker.controller
                self.guard.check()
                continue_node = context.get_node_data("GBFR_Continue")
                if not continue_node or not continue_node.get("enabled", True):
                    self.session.finish_requested = True
                self.calls[argv.node_name] = self.calls.get(argv.node_name, 0) + 1
                if self.calls[argv.node_name] > params.get("budget", 20):
                    raise SessionError("Screen transition budget exhausted: " + argv.node_name)
                if self.controls.stop_requested or self.controls.paused or context.tasker.stopping:
                    raise SessionError("Automation was stopped")
                if "key" in params or event in {"battle", "victory", "settle"}:
                    # Recheck the *same native recognition* on a newly captured frame.
                    # A screenshot from an earlier transition never authorizes input.
                    for attempt in range(3):
                        try:
                            recognition = context.run_recognition(argv.node_name, self.guard.capture())
                        except FrameUnavailable:
                            recognition = None
                        if recognition is not None and recognition.hit:
                            break
                        if attempt < 2:
                            time.sleep(.2)
                    else:
                        if "key" not in params:
                            # Combat HUDs disappear during camera transitions.
                            # No input or accounting change is authorized by the
                            # old frame; let the native next list inspect again.
                            self.write_status()
                            return True
                        raise SessionError("The recognized screen changed before its action")
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
                    if time.monotonic() - self.battle_started > 600:
                        raise SessionError("The supported battle exceeded ten minutes")
                elif event == "victory":
                    self.session.victory()
                elif event == "settle":
                    self.session.settle()
                    self.calls = {}
                    self.battle_started = None
                    if not self.session.should_continue:
                        if not context.override_pipeline({"GBFR_Continue": {"enabled": False}}):
                            raise SessionError("Could not apply the successful-run limit")
                elif event == "finish":
                    if self.session.phase != "ready":
                        raise SessionError("The session did not reach its settlement boundary")
                    self.session.phase = "finished"
                    self.close()
                elif event == "error":
                    raise SessionError(params["reason"])
            self.write_status()
            return True
        except Exception as exc:
            if self.session:
                self.session.fail(str(exc))
            self.write_status()
            self.close()
            return False


def main():
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction
    from maa.tasker import TaskerEventSink

    hooks = Hooks(Path(__file__).resolve().parent / "runtime" / "session.json")
    class Action(CustomAction):
        def run(self, context, argv):
            return hooks.run(context, argv)
    AgentServer.register_custom_action("GBFR", Action())
    class Events(TaskerEventSink):
        def on_raw_notification(self, tasker, msg, details):
            if details.get("task_id") != hooks.task_id:
                return
            if msg in {"Tasker.Task.Failed", "Tasker.Task.Succeeded"}:
                if hooks.session and hooks.session.phase not in {"finished", "error"}:
                    hooks.session.fail("Automation ended before a verified town finish")
                    hooks.write_status()
                hooks.close()
    AgentServer.add_tasker_sink(Events())
    try:
        if not AgentServer.start_up(sys.argv[-1]):
            raise RuntimeError("Could not connect the GBFR Agent")
        AgentServer.join()
    finally:
        hooks.close()
        AgentServer.shut_down()


if __name__ == "__main__":
    main()
