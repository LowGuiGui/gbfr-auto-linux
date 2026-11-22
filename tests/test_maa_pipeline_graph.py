# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The real pipeline and Agent against a scripted game (see maa_graph.py).

The scripts follow what the game was measured to do on 2026-11-21: two results
pages, `③再次挑战` / `③撤销再次挑战` on the rewards page, no victory screen once
Repeat Quest is on, and on the tenth run a reset toggle plus a dialog that, left
alone, ends in town.
"""

import json
from pathlib import Path
import re

import pytest

pytest.importorskip("maa.tasker", reason="MaaFw is not installed")

from maa_graph import RESOURCE, run, screen  # noqa: E402
from maa_messages import explain  # noqa: E402

NAV_KEYS = ["Return", "Return", "Return", "3", "Return"]


def menus():
    return [
        screen("quest list", "GBFR_QuestSelected", leave_on="key:Return"),
        screen("quest details", "GBFR_QuestDetails", leave_on="key:Return"),
        screen("matching", "GBFR_Private", leave_on="key:Return"),
        screen("ready room", "GBFR_ReadyTown", "GBFR_See_Town", leave_on="key:3"),
        screen("ready confirm", "GBFR_ReadyConfirm", "GBFR_CancelReady", leave_on="key:Return"),
    ]


def battle(*, victory, finish=False):
    result = [screen("battle", "GBFR_See_Battle", leave_on="action:GBFR_Battle", after=2,
                     finish=finish)]
    if victory:
        result.append(screen("victory", "GBFR_See_Victory", leave_on="action:GBFR_Victory"))
    return result


def titles():
    return screen("results: titles", "GBFR_See_Results", leave_on="action:GBFR_ResultHold", after=2)


def rewards(label, leave_on, after=1, finish=False):
    toggle = {"on": "GBFR_See_RepeatOn", "off": "GBFR_See_RepeatOff"}[label]
    return screen(f"results: rewards, repeat {label}", "GBFR_See_Results", toggle,
                  leave_on=leave_on, after=after, finish=finish)


def first_run_turning_repeat_on():
    return [*menus(), *battle(victory=True), titles(),
            rewards("off", "key:3"), rewards("on", "action:GBFR_KeepRepeating", after=2)]


def repeated_run_keeping_it_on():
    return [*battle(victory=False), titles(), rewards("on", "action:GBFR_KeepRepeating", after=2)]


def last_run_turning_it_off(*, victory=False):
    return [*battle(victory=victory), titles(), rewards("on", "key:3"),
            rewards("off", "action:GBFR_LetItEnd", after=2), town()]


def town():
    return screen("town", "GBFR_See_Town", "GBFR_Town")


def assert_clean_finish(outcome, completed):
    assert outcome.succeeded, outcome.session
    assert outcome.session["phase"] == "finished"
    assert outcome.session["error"] is None
    assert outcome.session["completed"] == outcome.session["episode"] == completed
    assert outcome.final_screen == "town"
    assert all(kind != "native-input" for _, kind in outcome.log)
    # What MXU shows: one line per counted run, then the finish.
    counted = [m for m in outcome.messages if re.fullmatch(r"第 \d+/\d+ 次完成|已完成 \d+ 次.*", m)]
    assert len(counted) == completed
    assert outcome.messages[-1] == "已结束，游戏停在城镇"


def assert_explained_failure(outcome):
    """A failure ends the task, records why, and shows the same reason in MXU's log."""
    assert not outcome.succeeded
    assert outcome.session["phase"] == "error"
    assert outcome.session["message"] == explain(outcome.session["error"])
    assert outcome.messages[-1] == outcome.session["message"]


def test_one_run_leaves_repeat_off_and_finishes_in_town(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=True), titles(),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=1)
    assert_clean_finish(outcome, 1)
    assert outcome.keys == NAV_KEYS
    assert outcome.session["admissions"] == 1


def test_three_runs_use_the_games_repeat_after_the_first(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *repeated_run_keeping_it_on(),
               *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_clean_finish(outcome, 3)
    assert outcome.session["admissions"] == 1
    # Menus once, then only the toggle: on after run 1, off after run 3.
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_twelve_runs_cross_the_tenth_run_dialog_by_starting_again_from_town(monkeypatch, tmp_path):
    screens = first_run_turning_repeat_on()
    for _ in range(8):
        screens += repeated_run_keeping_it_on()
    screens += [*battle(victory=False), titles(),
                # The game reset the toggle; its dialog covers the page before the
                # delayed press is rechecked, so nothing is pressed.
                rewards("off", "seen:GBFR_See_RepeatOff"),
                screen("tenth-run dialog", "GBFR_See_TenthRun", leave_on="action:GBFR_TenthRun", after=3),
                rewards("off", "action:GBFR_CapHold", after=2),
                screen("town after the dialog", "GBFR_See_Town", "GBFR_Town",
                       leave_on="action:GBFR_TownRestart"),
                *first_run_turning_repeat_on(), *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=12)
    assert_clean_finish(outcome, 12)
    assert outcome.session["admissions"] == 2
    assert outcome.keys == NAV_KEYS + ["3"] + NAV_KEYS + ["3", "3"]


def test_a_dialog_opened_by_the_press_is_handled_the_same_way(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "key:3"),
               screen("tenth-run dialog", "GBFR_See_TenthRun", leave_on="action:GBFR_TenthRun", after=2),
               rewards("off", "action:GBFR_CapHold", after=2),
               screen("town after the dialog", "GBFR_See_Town", "GBFR_Town",
                      leave_on="action:GBFR_TownRestart"),
               *menus(), *battle(victory=True), titles(),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_clean_finish(outcome, 3)
    assert outcome.keys == NAV_KEYS + ["3", "3"] + NAV_KEYS


def test_finish_requested_during_battle_ends_after_that_run(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False, finish=True), titles(),
               rewards("on", "key:3"), rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=10)
    assert_clean_finish(outcome, 2)
    assert outcome.session["finish_requested"]
    assert outcome.session["overrun"] == 0
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_finish_requested_on_the_rewards_page_turns_repeat_back_off(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on()[:-1],
               rewards("on", "action:GBFR_KeepRepeating"),
               rewards("on", "key:3", finish=True),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=10)
    assert_clean_finish(outcome, 1)
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_until_stopped_ignores_the_limit(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *repeated_run_keeping_it_on(),
               *battle(victory=False, finish=True), titles(), rewards("on", "key:3"),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=1, until_stopped=True)
    assert_clean_finish(outcome, 3)
    assert outcome.session["limit"] is None


def test_repeat_left_on_by_the_player_is_kept_without_a_press(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=True), titles(),
               rewards("on", "action:GBFR_KeepRepeating", after=2), *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=2)
    assert_clean_finish(outcome, 2)
    assert outcome.keys == NAV_KEYS + ["3"]


def test_starting_from_the_ready_room(monkeypatch, tmp_path):
    screens = [*menus()[3:], *battle(victory=True), titles(),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=1)
    assert_clean_finish(outcome, 1)
    assert outcome.keys == ["3", "Return"]


def test_finish_requested_before_departure_cancels_without_a_run(monkeypatch, tmp_path):
    screens = [*menus()[:-1],
               screen("ready confirm", "GBFR_ReadyConfirm", "GBFR_CancelReady",
                      leave_on="key:Escape", finish=True),
               town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert outcome.succeeded, outcome.session
    assert outcome.session["phase"] == "finished"
    assert outcome.session["completed"] == outcome.session["episode"] == 0
    assert outcome.keys == ["Return", "Return", "Return", "3", "Escape"]


def test_a_toggle_that_never_changes_stops_after_two_presses(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=True), titles(), rewards("off", None)]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["error"] == "Repeat Quest could not be turned on"
    assert outcome.session["completed"] == 1
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_a_new_run_after_repeat_was_turned_off_is_refused(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=True), titles(),
               rewards("off", "action:GBFR_LetItEnd", after=2), *battle(victory=False)]
    outcome = run(monkeypatch, tmp_path, screens, limit=1)
    assert not outcome.succeeded
    assert "Repeat Quest was off" in outcome.session["error"]
    assert outcome.session["completed"] == 1


def test_a_failed_quest_stops_without_counting(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=False), screen("failed", "GBFR_See_Defeat")]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["completed"] == 0
    assert "failed" in outcome.session["error"]
    assert outcome.keys == NAV_KEYS


def test_unknown_start_stops_without_input_or_success(monkeypatch, tmp_path):
    outcome = run(monkeypatch, tmp_path, [screen("unknown")])
    assert_explained_failure(outcome)
    assert outcome.session["completed"] == outcome.session["episode"] == 0
    assert "supported transition" in outcome.session["error"]
    assert outcome.keys == []


@pytest.mark.parametrize("label", ["on", "off"])
def test_a_results_screen_left_from_before_is_never_counted(monkeypatch, tmp_path, label):
    stale = [rewards(label, None)]
    outcome = run(monkeypatch, tmp_path, stale)
    assert not outcome.succeeded
    assert outcome.session["completed"] == 0
    assert outcome.session["phase"] == "error"
    assert outcome.keys == []


def test_a_limit_of_ten_lets_the_tenth_run_dialog_time_out_into_town(monkeypatch, tmp_path):
    screens = first_run_turning_repeat_on()
    for _ in range(8):
        screens += repeated_run_keeping_it_on()
    screens += [*battle(victory=False), titles(),
                rewards("off", "action:GBFR_LetItEnd"),
                screen("tenth-run dialog", "GBFR_See_TenthRun", leave_on="action:GBFR_TenthRun", after=3),
                rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=10)
    assert_clean_finish(outcome, 10)
    assert outcome.session["admissions"] == 1
    assert outcome.keys == NAV_KEYS + ["3"]  # nothing pressed on the dialog


def test_a_game_that_closes_mid_battle_ends_the_task_at_once_with_that_reason(monkeypatch, tmp_path):
    screens = [*menus(), screen("battle", "GBFR_See_Battle", leave_on="action:GBFR_Battle", after=2),
               screen("battle, game closed", "GBFR_See_Battle", gone=True)]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["error"].startswith("The game or its Gamescope closed during the session")
    assert outcome.session["completed"] == 0
    assert outcome.keys == NAV_KEYS
    # The failed battle check goes straight to the error path; nothing runs as if it had passed.
    after = [kind for name, kind in outcome.log
             if name == "battle, game closed" and kind.startswith("action:")]
    assert after == ["action:GBFR_Battle", "action:GBFR_UnexpectedScreen", "action:GBFR_Stopped"]


def test_stop_during_the_first_battle_is_a_stop_not_an_error(monkeypatch, tmp_path):
    screens = [*menus()[:-1],
               screen("ready confirm", "GBFR_ReadyConfirm", "GBFR_CancelReady", leave_on="key:Return"),
               screen("battle", "GBFR_See_Battle", stop=True)]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert outcome.session["phase"] == "stopped"
    assert outcome.session["error"] is None
    assert outcome.session["message"].startswith("已按要求停止，已计数的 0 次有效")
    assert "没有开着" in outcome.session["message"]
    assert outcome.keys == NAV_KEYS


def test_stop_after_repeat_was_turned_on_warns_that_the_game_keeps_farming(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), screen("battle", "GBFR_See_Battle", stop=True)]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert outcome.session["phase"] == "stopped"
    assert outcome.session["completed"] == 1
    assert "还开着" in outcome.session["message"]


def test_only_the_finish_node_ends_the_pipeline_successfully():
    """`Hooks.task_ended` reads a successful task end of an unfinished session as the
    player's stop. That holds only while every other end of the pipeline fails."""
    pipeline = json.loads((Path(RESOURCE) / "pipeline" / "farming.json").read_text())
    # GBFR_CancelReady is reached by an Agent override of GBFR_ReadyConfirm's next.
    seen, todo = set(), ["GBFR_Start", "GBFR_CancelReady"]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo += pipeline[name].get("next", []) + pipeline[name].get("on_error", [])
    for name in sorted(seen):
        node = pipeline[name]
        if node.get("next"):
            continue
        event = node["action"]["param"]["custom_action_param"].get("event")
        assert name == "GBFR_Finished" or event in {"error", "halt"}, name
