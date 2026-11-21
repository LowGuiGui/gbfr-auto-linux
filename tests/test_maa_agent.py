# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import json
from types import SimpleNamespace

from control import Controls
from maa_agent import Hooks
from maa_guard import FrameUnavailable
from maa_session import Session


def prepared(tmp_path):
    hooks = Hooks(tmp_path / "status.json")
    hooks.session = Session("boss", 3)
    hooks.controls = Controls()
    hooks.guard = SimpleNamespace(controller="expired proxy", check=lambda: None,
                                  close=lambda: None, capture=lambda: "fresh image")
    context = SimpleNamespace(tasker=SimpleNamespace(controller="current proxy", stopping=False),
                              get_node_data=lambda name: {"enabled": True})
    return hooks, context


def test_rebinds_callback_scoped_proxy_before_identity_check(tmp_path):
    hooks, context = prepared(tmp_path)
    def check():
        assert hooks.guard.controller == "current proxy"
    hooks.guard.check = check
    args = SimpleNamespace(custom_action_param="{}", node_name="Navigate")
    assert hooks.run(context, args)


def test_screen_change_refuses_input_and_does_not_admit_run(tmp_path):
    hooks, context = prepared(tmp_path)
    context.run_recognition = lambda name, image: SimpleNamespace(hit=False)
    args = SimpleNamespace(custom_action_param=json.dumps({"event": "admit", "key": "Return"}),
                           node_name="Ready")
    assert not hooks.run(context, args)
    assert hooks.session.episode == hooks.session.completed == 0
    assert hooks.session.error == "The recognized screen changed before its action"


def test_transition_budget_stops_a_stuck_menu(tmp_path):
    hooks, context = prepared(tmp_path)
    args = SimpleNamespace(custom_action_param='{"budget": 2}', node_name="Menu")
    assert hooks.run(context, args)
    assert hooks.run(context, args)
    assert not hooks.run(context, args)
    assert "budget exhausted" in hooks.session.error


def test_transient_combat_hud_retries_without_counting_or_input(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    context.run_recognition = lambda name, image: SimpleNamespace(hit=False)
    args = SimpleNamespace(custom_action_param='{"event": "battle"}', node_name="Battle")
    assert hooks.run(context, args)
    assert hooks.session.phase == "starting"
    assert hooks.session.completed == 0
    assert hooks.guard is not None


def test_black_transition_frame_never_authorizes_menu_input(tmp_path):
    hooks, context = prepared(tmp_path)
    def capture():
        raise FrameUnavailable("fade")
    hooks.guard.capture = capture
    context.run_recognition = lambda *args: None
    args = SimpleNamespace(custom_action_param='{"key": "Return"}', node_name="Confirm")
    assert not hooks.run(context, args)
    assert hooks.session.completed == 0
    assert hooks.session.error == "The recognized screen changed before its action"


def test_finish_before_departure_cancels_without_input_or_admission(tmp_path):
    hooks, context = prepared(tmp_path)
    context.get_node_data = lambda name: {"enabled": False}
    context.run_recognition = lambda name, image: SimpleNamespace(hit=True)
    overrides = []
    context.override_pipeline = lambda value: overrides.append(value) or True
    args = SimpleNamespace(custom_action_param=json.dumps({"event": "admit", "key": "Return"}),
                           node_name="Ready")
    assert hooks.run(context, args)
    assert hooks.session.finish_requested
    assert hooks.session.episode == hooks.session.completed == 0
    assert overrides == [{"Ready": {"next": ["GBFR_CancelReady"]}}]
