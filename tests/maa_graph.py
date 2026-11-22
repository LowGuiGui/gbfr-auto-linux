# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the real GBFR pipeline against a scripted game, offline.

Leaf recognitions (`GBFR_See_*`, what is visible) and the quest-menu nodes are
replaced by a Custom recognition that asks a scripted game whether they are showing.
Nodes composed from leaves and the Agent's `GBFRSession` gate stay native, so the
Tasker exercises the real `And` logic, `next` / `on_error` ordering, the Agent's
accounting and its stop decisions. Every key goes to the script instead of a
controller. Recognition accuracy is not tested here; the screenshot replay checks do.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import threading

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "assets" / "resource"
FRAME = np.full((720, 1280, 3), 96, dtype=np.uint8)


@dataclass(frozen=True)
class Screen:
    """One game screen: the nodes that recognise it and the event that leaves it.

    `leave_on` is "key:<name>" for a key the automation presses, "action:<node>"
    for a node's action finishing, or "seen:<node>" for a scripted recognition of
    that node succeeding (a screen that changes by itself while being looked at).
    `after` counts how many such events the screen needs before it changes. A
    screen without `leave_on` stays. `finish` asks the session to finish after the
    current run as the screen appears, as the control panel would. `gone` means the
    game and its Gamescope have closed: the guard's captures fail and it reports the
    target lost. `stop` presses the client's Stop as the screen appears.
    """

    name: str
    hits: frozenset
    leave_on: str | None = None
    after: int = 1
    finish: bool = False
    gone: bool = False
    stop: bool = False


def screen(name, *hits, leave_on=None, after=1, finish=False, gone=False, stop=False):
    return Screen(name, frozenset(hits), leave_on, after, finish, gone, stop)


class ScriptedGame:
    def __init__(self, screens):
        self.screens = list(screens)
        self.index = 0
        self.count = 0
        self.log = []
        self.on_enter = None

    @property
    def current(self):
        return self.screens[self.index] if self.index < len(self.screens) else None

    def visible(self, node):
        return self.current is not None and node in self.current.hits

    def event(self, kind):
        self.log.append((self.current.name if self.current else None, kind))
        if self.current is not None and self.current.leave_on == kind:
            self.count += 1
            if self.count >= self.current.after:
                self.index += 1
                self.count = 0
                if self.current is not None and self.on_enter is not None:
                    self.on_enter(self.current)

    @property
    def keys(self):
        return [kind[4:] for _, kind in self.log if kind.startswith("key:")]


class FakeGuard:
    """Stands in for NativeGuard: no compositor, X11 or PipeWire is touched."""

    def __init__(self, game):
        self.game = game
        self.controller = None

    @property
    def gone(self):
        return self.game.current is not None and self.game.current.gone

    def check(self):
        pass

    def lost(self):
        return "process 4242 is gone or its identity cannot be read" if self.gone else None

    def capture(self):
        if self.gone:
            from maa_target import ProbeError
            raise ProbeError("Native screenshot failed")
        return FRAME

    def press(self, context, key, duration=100):
        self.game.event("key:" + key)

    def close(self):
        if self.gone:
            from Xlib.error import ConnectionClosedError
            raise ConnectionClosedError("server")  # as python-xlib does on a dead display


class FakeServer:
    """Stands in for the control socket, so tests never take the real session lock."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def close(self):
        pass


@dataclass
class Outcome:
    succeeded: bool
    session: dict
    keys: list
    log: list
    final_screen: str | None
    messages: list  # the focus messages a client such as MXU shows in its log


def composed(recognition):
    """True for an And built only from GBFR_See_* leaves and Custom gates."""
    if recognition.get("type") != "And":
        return False
    for part in recognition["param"]["all_of"]:
        if isinstance(part, str):
            if not part.startswith("GBFR_See_"):
                return False
        elif (part.get("recognition") or {}).get("type") != "Custom":
            return False
    return True


def _speed_overrides(resource, timeout_ms):
    """Script leaves and menu nodes, keep composed nodes native, shorten every wait."""
    overrides = {}
    for name in resource.node_list:
        data = resource.get_node_data(name) or {}
        node: dict = {"pre_delay": 0, "post_delay": 0, "rate_limit": 20}
        if "timeout" in data:
            node["timeout"] = timeout_ms
        recognition = data.get("recognition") or {}
        if recognition.get("type", "DirectHit") != "DirectHit" and (
                name.startswith("GBFR_See_") or not composed(recognition)):
            node["recognition"] = {"type": "Custom",
                                   "param": {"custom_recognition": "GBFRScript"}}
        overrides[name] = node
    return overrides


def run(monkeypatch, tmp_path, screens, *, limit=1, until_stopped=False,
        resource_path=RESOURCE, timeout_ms=400):
    from maa.context import ContextEventSink
    from maa.controller import CustomController
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker

    import maa_agent

    game = ScriptedGame(screens)
    monkeypatch.setattr(maa_agent, "NativeGuard", lambda controller: FakeGuard(game))
    monkeypatch.setattr(maa_agent, "ControlServer", FakeServer)
    hooks = maa_agent.Hooks(tmp_path / "session.json")
    # A stop makes the task's job return before the running action and the task-end
    # notification are done; the outcome is read once the hooks have seen the end.
    ended = threading.Event()
    task_ended = hooks.task_ended

    def observed(succeeded):
        try:
            task_ended(succeeded)
        finally:
            ended.set()
    hooks.task_ended = observed

    def enter(current):
        if current.finish and hooks.session is not None:
            hooks.session.finish_requested = True
        if current.stop:
            tasker.post_stop()
    game.on_enter = enter

    class Script(CustomRecognition):
        def analyze(self, context, argv):
            if not game.visible(argv.node_name):
                return None
            game.event("seen:" + argv.node_name)
            return (0, 0, 1, 1)

    class Gate(CustomRecognition):
        def analyze(self, context, argv):
            return (0, 0, 1, 1) if hooks.want(context, argv.custom_recognition_param) else None

    class Action(CustomAction):
        def run(self, context, argv):
            result = hooks.run(context, argv)
            game.event("action:" + argv.node_name)
            return result

    messages = []

    class Messages(ContextEventSink):
        """MXU looks up each event's name in the node's focus and logs what it finds."""

        def on_raw_notification(self, context, msg, details):
            value = (details.get("focus") or {}).get(msg)
            value = value.get("content") if isinstance(value, dict) else value
            if value:
                messages.append(value)

    class Offline(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return "gbfr-scripted-game"

        def screencap(self):
            return FRAME.copy()

        def denied(self, *args, **kwargs):
            # The pipeline's only inputs go through the Agent's guard. A native input
            # here means a node started sending keys outside the Agent's checks.
            game.event("native-input")
            return False

        start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = denied
        click_key = input_text = key_down = key_up = denied

    Tasker.set_log_dir(tmp_path / "maa-log")
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    resource = Resource()
    resource.use_cpu()
    if not resource.post_bundle(resource_path).wait().succeeded:
        raise RuntimeError("Native resource loading failed")
    resource.register_custom_recognition("GBFRScript", Script())
    resource.register_custom_recognition("GBFRSession", Gate())
    resource.register_custom_action("GBFR", Action())
    controller = Offline()
    controller.post_connection().wait()
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError("Cannot bind the scripted game")
    tasker.add_context_sink(Messages())
    tasker.add_sink(maa_agent.task_sink(hooks))
    overrides = _speed_overrides(resource, timeout_ms)
    overrides["GBFR_RunLimit"] = {"attach": {"limit": limit}}
    overrides["GBFR_UntilStopped"] = {"enabled": until_stopped}
    try:
        job = tasker.post_task("GBFR_Start", overrides).wait()
        ended.wait(5)
        session = json.loads((tmp_path / "session.json").read_text())
        return Outcome(job.succeeded, session, game.keys, game.log,
                       game.current.name if game.current else None, messages)
    finally:
        hooks.close()
