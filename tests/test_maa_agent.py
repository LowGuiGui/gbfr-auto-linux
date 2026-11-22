# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import json
from types import SimpleNamespace

import pytest
from Xlib.error import ConnectionClosedError

from control import Controls
from maa_agent import Hooks
from maa_guard import FrameUnavailable
from maa_messages import explain
from maa_session import Session
from maa_target import ProbeError


def prepared(tmp_path):
    hooks = Hooks(tmp_path / "status.json")
    hooks.session = Session("boss", 3)
    hooks.controls = Controls()
    hooks.guard = SimpleNamespace(controller="expired proxy", check=lambda: None,
                                  close=lambda: None, capture=lambda: "fresh image",
                                  lost=lambda: None)
    context = SimpleNamespace(tasker=SimpleNamespace(controller="current proxy", stopping=False),
                              get_node_data=lambda name: {"enabled": True},
                              override_pipeline=lambda value: True)
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


def settled(tmp_path, limit=3):
    hooks, context = prepared(tmp_path)
    hooks.session = Session("boss", limit)
    hooks.session.admit()
    hooks.session.battle()
    hooks.session.settle()
    return hooks, context


def test_session_gate_answers_continue_stop_and_tenth_run(tmp_path):
    hooks, context = settled(tmp_path)
    ask = lambda want: hooks.want(context, json.dumps({"want": want}))  # noqa: E731
    assert ask("continue") and ask("go_on") and not ask("stop") and not ask("cap")
    hooks.session.tenth_run()
    assert ask("cap") and ask("go_on") and not ask("continue") and not ask("stop")
    context.get_node_data = lambda name: {"enabled": False}  # the panel's "finish after this run"
    assert ask("stop") and not ask("go_on") and not ask("cap")
    assert not ask("anything else")


def test_session_gate_is_closed_without_a_healthy_session(tmp_path):
    hooks, context = settled(tmp_path)
    hooks.session.fail("broken")
    assert not hooks.want(context, '{"want": "stop"}')
    hooks.session = None
    assert not hooks.want(context, '{"want": "stop"}')


def test_a_covered_toggle_skips_the_press_instead_of_failing(tmp_path):
    hooks, context = settled(tmp_path)
    pressed = []
    hooks.guard.press = lambda *args: pressed.append(args)
    context.run_recognition = lambda name, image: SimpleNamespace(hit=False)
    args = SimpleNamespace(custom_action_param='{"key": "3", "on_change": "skip", "budget": 2}',
                           node_name="GBFR_RepeatOn")
    assert hooks.run(context, args)
    assert pressed == [] and hooks.session.error is None


def test_the_first_failure_reason_is_kept(tmp_path):
    hooks, context = settled(tmp_path)
    args = SimpleNamespace(custom_action_param='{"budget": 0, "exhausted": "Toggle stuck"}',
                           node_name="GBFR_RepeatOn")
    assert not hooks.run(context, args)
    later = SimpleNamespace(custom_action_param='{"event": "error", "reason": "Timed out"}',
                            node_name="GBFR_UnexpectedScreen")
    assert not hooks.run(context, later)
    assert hooks.session.error == "Toggle stuck"


def test_battle_limit_comes_from_the_quest(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.battle_limit = 0
    hooks.session.admit()
    context.run_recognition = lambda name, image: SimpleNamespace(hit=True)
    args = SimpleNamespace(custom_action_param='{"event": "battle"}', node_name="GBFR_Battle")
    hooks.battle_started = 1.0
    assert not hooks.run(context, args)
    assert hooks.session.error == "The battle exceeded its 0 s limit"


def results_page(failed=False, name="解决沙漠狂徒"):
    """run_recognition on a fresh results frame: the page itself, the failure leaf, the name."""
    def recognise(node, image):
        if node == "GBFR_See_Defeat":
            return SimpleNamespace(hit=failed)
        if node == "GBFR_Read_Quest":
            return SimpleNamespace(hit=bool(name), best_result=SimpleNamespace(text=f" {name} "))
        return SimpleNamespace(hit=True)
    return recognise


SETTLE = SimpleNamespace(custom_action_param='{"event": "settle", "budget": 1}', node_name="GBFR_Result")


def test_each_count_rewrites_the_progress_message(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    hooks.session.battle()
    overrides = []
    context.override_pipeline = lambda value: overrides.append(value) or True
    context.run_recognition = results_page()
    assert hooks.run(context, SETTLE)
    assert overrides == [{"GBFR_Counted": {"focus": {"Node.Action.Succeeded": "第 1/3 次完成"}}}]
    hooks.session.limit = None
    assert hooks.progress() == "已完成 1 次（一直刷，等你停止）"


def test_the_quest_name_is_read_once_from_the_first_results_page(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.quest = None
    hooks.session.admit()
    hooks.session.battle()
    context.run_recognition = results_page()
    assert hooks.run(context, SETTLE)
    assert hooks.session.quest == "解决沙漠狂徒"
    hooks.session.repeat_on()
    hooks.session.battle()
    context.run_recognition = results_page(name="misread")
    assert hooks.run(context, SETTLE)
    assert hooks.session.quest == "解决沙漠狂徒" and hooks.session.completed == 2


def test_an_unreadable_quest_name_never_stops_the_count(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.quest = None
    hooks.session.admit()
    hooks.session.battle()
    recognise = results_page()

    def broken(node, image):
        if node == "GBFR_Read_Quest":
            raise RuntimeError("OCR failed")
        return recognise(node, image)
    context.run_recognition = broken
    assert hooks.run(context, SETTLE)
    assert hooks.session.completed == 1 and hooks.session.quest is None


def test_a_failed_quests_results_page_is_never_counted(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    hooks.session.battle()
    overrides = []
    context.override_pipeline = lambda value: overrides.append(value) or True
    context.run_recognition = results_page(failed=True)
    assert hooks.run(context, SETTLE)  # the next list's failure nodes take the page
    assert hooks.session.completed == 0 and hooks.session.phase == "battle"
    assert overrides == [{"GBFR_Counted": {"focus": {"Node.Action.Succeeded": ""}}}]


def test_a_failed_quest_ends_with_repeat_known_off(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    hooks.session.battle()
    hooks.session.left_on = True  # an earlier run had turned it on
    args = SimpleNamespace(custom_action_param=json.dumps(
        {"event": "failed", "reason": "The quest failed; Repeat Quest is off"}), node_name="GBFR_Defeat")
    assert not hooks.run(context, args)
    assert hooks.session.phase == "error" and not hooks.session.left_on
    assert hooks.session.completed == 0


def test_yes_on_the_tenth_run_dialog_is_recorded_after_its_key(tmp_path):
    hooks, context = prepared(tmp_path)
    pressed = []
    hooks.guard.press = lambda context, key, duration=100: pressed.append(key)
    first = hooks.session
    for step in (first.admit, first.battle, first.settle, first.tenth_run):
        step()
    context.run_recognition = lambda name, image: SimpleNamespace(hit=True)
    args = SimpleNamespace(custom_action_param=json.dumps(
        {"key": "Return", "event": "tenth_yes", "budget": 2, "on_change": "skip"}), node_name="GBFR_TenthYes")
    assert hooks.run(context, args)
    assert pressed == ["Return"]
    assert hooks.session.continued == 1 and not hooks.session.cap_seen


def failing_capture():
    raise ProbeError("Native screenshot failed")


def watching(context):
    overrides = []
    context.override_pipeline = lambda value: overrides.append(value) or True
    return overrides


def test_a_failure_after_the_game_closed_names_the_closed_game(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    hooks.guard.capture = failing_capture
    hooks.guard.lost = lambda: "process 4242 is gone or its identity cannot be read"
    context.run_recognition = lambda name, image: SimpleNamespace(hit=True)
    overrides = watching(context)
    args = SimpleNamespace(custom_action_param='{"event": "battle"}', node_name="GBFR_Battle")
    assert not hooks.run(context, args)
    error = hooks.session.error
    assert error.startswith("The game or its Gamescope closed during the session")
    assert "4242" in error
    assert overrides == [{"GBFR_Stopped": {"focus": {"Node.Action.Failed": explain(error)}}}]
    assert hooks.guard is None


def test_a_failure_with_the_game_still_running_keeps_its_own_reason(tmp_path):
    hooks, context = prepared(tmp_path)
    hooks.session.admit()
    hooks.guard.capture = failing_capture
    context.run_recognition = lambda name, image: SimpleNamespace(hit=True)
    args = SimpleNamespace(custom_action_param='{"event": "battle"}', node_name="GBFR_Battle")
    assert not hooks.run(context, args)
    assert hooks.session.error == "Native screenshot failed"


def test_a_close_that_raises_neither_escapes_nor_hides_the_reason(tmp_path):
    hooks, context = prepared(tmp_path)
    def dead_display():
        raise ConnectionClosedError("server")
    hooks.guard.close = dead_display
    args = SimpleNamespace(custom_action_param='{"budget": 0, "exhausted": "Toggle stuck"}',
                           node_name="GBFR_RepeatOn")
    assert hooks.run(context, args) is False  # not an exception MaaFw would count as success
    assert hooks.session.error == "Toggle stuck"
    assert hooks.guard is None
    hooks.close()


def test_a_stop_request_is_recorded_as_a_stop_and_explained(tmp_path):
    hooks, context = settled(tmp_path)
    hooks.session.repeat_on()
    hooks.session.battle()
    hooks.controls.request_stop("test")
    overrides = watching(context)
    args = SimpleNamespace(custom_action_param='{"event": "battle"}', node_name="GBFR_Battle")
    assert not hooks.run(context, args)
    later = SimpleNamespace(custom_action_param='{"event": "error", "reason": "Timed out"}',
                            node_name="GBFR_UnexpectedScreen")
    assert not hooks.run(context, later)
    status = hooks.status()
    assert status["phase"] == "stopped" and status["error"] is None
    assert status["message"].startswith("已按要求停止，已计数的 1 次有效")
    assert "还开着" in status["message"]
    assert overrides[-1] == {"GBFR_Stopped": {"focus": {"Node.Action.Failed": status["message"]}}}


@pytest.mark.parametrize("succeeded, phase, error", [
    (True, "stopped", None),
    (False, "error", "Automation ended before a verified town finish"),
])
def test_how_the_task_ended_decides_between_stop_and_error(tmp_path, succeeded, phase, error):
    hooks, _ = prepared(tmp_path)
    hooks.session.admit()
    hooks.task_ended(succeeded)
    assert (hooks.session.phase, hooks.session.error) == (phase, error)
    assert hooks.guard is None


def test_a_task_end_after_the_finish_changes_nothing(tmp_path):
    hooks, _ = prepared(tmp_path)
    hooks.session.limit = 1
    hooks.session.admit()
    hooks.session.battle()
    hooks.session.settle()
    hooks.session.finish()
    hooks.task_ended(True)
    assert hooks.session.phase == "finished"


def test_a_failure_before_the_session_exists_is_still_reported(tmp_path):
    hooks = Hooks(tmp_path / "status.json")
    hooks.session = Session("old", 1)  # left over from an earlier task
    data = {"GBFR_QuestConfig": {"attach": {"battle_limit": 1800}},
            "GBFR_RunLimit": {"attach": {"limit": 0}}}
    context = SimpleNamespace(get_node_data=lambda name: data.get(name),
                              override_pipeline=lambda value: True)
    args = SimpleNamespace(custom_action_param='{"event": "begin"}', node_name="GBFR_Start",
                           task_detail=SimpleNamespace(task_id=7))
    assert not hooks.run(context, args)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["phase"] == "error" and "successful-run limit" in status["error"]
    assert "次数设置无效" in status["message"]


def test_the_session_lock_is_released_even_if_the_guard_cannot_close(tmp_path):
    hooks = Hooks(tmp_path / "status.json")
    released = []
    def dead_display():
        raise ConnectionClosedError("server")
    hooks.guard = SimpleNamespace(close=dead_display)
    hooks.owner = SimpleNamespace(close=lambda: released.append("control socket"))
    hooks.close()  # also runs from the task-end sink and at Agent exit, outside run()
    assert released == ["control socket"]
    assert hooks.guard is None and hooks.owner is None
