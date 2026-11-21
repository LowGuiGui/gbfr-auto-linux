# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the real GBFR pipeline against a scripted game, offline.

Every recognition in the resource is replaced by a Custom recognition that asks a
scripted game whether that node's screen is showing, and every key goes to the
script instead of a controller. The native Tasker then exercises the pipeline's
`next` / `on_error` ordering together with the Agent's accounting and stop logic.
Recognition accuracy is not tested here; the screenshot replay checks do that.
"""

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "assets" / "resource"
FRAME = np.full((720, 1280, 3), 96, dtype=np.uint8)


@dataclass(frozen=True)
class Screen:
    """One game screen: the nodes that recognise it and the event that leaves it.

    `leave_on` is "key:<name>" for a key the automation presses, or
    "action:<node>" for a node's action finishing. `after` counts how many such
    events the screen needs before it changes. A screen without `leave_on` stays.
    """

    name: str
    hits: frozenset
    leave_on: str | None = None
    after: int = 1


def screen(name, *hits, leave_on=None, after=1):
    return Screen(name, frozenset(hits), leave_on, after)


class ScriptedGame:
    def __init__(self, screens):
        self.screens = list(screens)
        self.index = 0
        self.count = 0
        self.log = []

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

    @property
    def keys(self):
        return [kind[4:] for _, kind in self.log if kind.startswith("key:")]


class FakeGuard:
    """Stands in for NativeGuard: no compositor, X11 or PipeWire is touched."""

    def __init__(self, game):
        self.game = game
        self.controller = None

    def check(self):
        pass

    def capture(self):
        return FRAME

    def press(self, context, key, duration=100):
        self.game.event("key:" + key)

    def close(self):
        pass


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


def _speed_overrides(resource, timeout_ms):
    """Script every recognising node and shorten waits so a test runs in seconds."""
    overrides = {}
    for name in resource.node_list:
        if name.startswith("GBFR_Catalog_"):
            continue  # only ever evaluated inside a parent's And, which is scripted
        data = resource.get_node_data(name) or {}
        node: dict = {"pre_delay": 0, "post_delay": 0, "rate_limit": 20}
        if "timeout" in data:
            node["timeout"] = timeout_ms
        if (data.get("recognition") or {}).get("type", "DirectHit") != "DirectHit":
            node["recognition"] = {"type": "Custom",
                                   "param": {"custom_recognition": "GBFRScript"}}
        overrides[name] = node
    return overrides


def run(monkeypatch, tmp_path, screens, *, quest="解决沙漠狂徒", limit=1,
        resource_path=RESOURCE, timeout_ms=400):
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

    class Script(CustomRecognition):
        def analyze(self, context, argv):
            return (0, 0, 1, 1) if game.visible(argv.node_name) else None

    class Action(CustomAction):
        def run(self, context, argv):
            result = hooks.run(context, argv)
            game.event("action:" + argv.node_name)
            return result

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
    resource.register_custom_action("GBFR", Action())
    controller = Offline()
    controller.post_connection().wait()
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError("Cannot bind the scripted game")
    overrides = _speed_overrides(resource, timeout_ms)
    overrides["GBFR_QuestConfig"] = {"attach": {"quest": quest}}
    overrides["GBFR_RunLimit"] = {"attach": {"limit": limit}}
    try:
        job = tasker.post_task("GBFR_Start", overrides).wait()
        session = json.loads((tmp_path / "session.json").read_text())
        return Outcome(job.succeeded, session, game.keys, game.log,
                       game.current.name if game.current else None)
    finally:
        hooks.close()
