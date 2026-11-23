# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The real pipeline and Agent against a scripted game (see maa_graph.py).

The scripts follow what the game was measured to do on 2026-11-21 and 2026-11-22:
two results pages, `③再次挑战` / `③撤销再次挑战` on the rewards page, no victory
screen once Repeat Quest is on, and on the tenth run a reset toggle plus a dialog
whose 是 turns repeat back on (left alone, it ends in town). A failed quest shows one
results page with no completion reward and goes to town by itself.
"""

import json
from pathlib import Path
import re

import pytest

pytest.importorskip("maa.tasker", reason="MaaFw is not installed")

from maa_graph import RESOURCE, run, screen  # noqa: E402
from maa_messages import explain  # noqa: E402

NAV_KEYS = ["Return", "Return", "3", "Return"]
YES = ["Up", "Return"]


def menus():
    """From the quest the player opened at the counter to departure."""
    return [
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
    return screen("town", "GBFR_See_Town")


def tenth_run_answered_yes():
    """Run 10: the game resets the toggle, its dialog covers the page, 是 turns repeat on."""
    return [*battle(victory=False), titles(),
            # The dialog covers the page before the delayed press is rechecked.
            rewards("off", "seen:GBFR_See_RepeatOff"),
            screen("tenth-run dialog, cursor on 否", "GBFR_See_TenthRun", "GBFR_See_TenthNo",
                   leave_on="key:Up"),
            screen("tenth-run dialog, cursor on 是", "GBFR_See_TenthRun", "GBFR_See_TenthYes",
                   leave_on="key:Return"),
            rewards("on", "action:GBFR_KeepRepeating", after=2)]


def failure(toggle):
    hits = ["GBFR_See_Defeat", "GBFR_See_Results"]
    if toggle:
        hits.append({"on": "GBFR_See_RepeatOn", "off": "GBFR_See_RepeatOff"}[toggle])
    return hits


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


def test_twelve_runs_cross_the_tenth_run_dialog_with_yes(monkeypatch, tmp_path):
    screens = first_run_turning_repeat_on()
    for _ in range(8):
        screens += repeated_run_keeping_it_on()
    screens += [*tenth_run_answered_yes(), *repeated_run_keeping_it_on(), *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=12)
    assert_clean_finish(outcome, 12)
    assert outcome.session["admissions"] == 1  # the menus only once, never from town again
    assert outcome.session["continued"] == 1
    assert outcome.keys == NAV_KEYS + ["3"] + YES + ["3"]


def test_until_stopped_crosses_every_tenth_run_dialog(monkeypatch, tmp_path):
    screens = first_run_turning_repeat_on()
    for block in range(3):
        for _ in range(8 if block == 0 else 9):
            screens += repeated_run_keeping_it_on()
        screens += tenth_run_answered_yes()
    screens += [*battle(victory=False, finish=True), titles(), rewards("on", "key:3"),
                rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, until_stopped=True)
    assert_clean_finish(outcome, 31)
    assert outcome.session["continued"] == 3
    assert outcome.keys == NAV_KEYS + ["3"] + YES * 3 + ["3"]


def test_a_dialog_with_the_cursor_already_on_yes_needs_only_return(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "seen:GBFR_See_RepeatOff"),
               screen("tenth-run dialog, cursor on 是", "GBFR_See_TenthRun", "GBFR_See_TenthYes",
                      leave_on="key:Return"),
               rewards("on", "action:GBFR_KeepRepeating", after=2), *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_clean_finish(outcome, 3)
    assert outcome.keys == NAV_KEYS + ["3", "Return", "3"]


def test_a_dialog_opened_by_the_press_is_answered_the_same_way(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "key:3"),
               screen("tenth-run dialog, cursor on 否", "GBFR_See_TenthRun", "GBFR_See_TenthNo",
                      leave_on="key:Up"),
               screen("tenth-run dialog, cursor on 是", "GBFR_See_TenthRun", "GBFR_See_TenthYes",
                      leave_on="key:Return"),
               rewards("on", "action:GBFR_KeepRepeating", after=2), *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_clean_finish(outcome, 3)
    assert outcome.keys == NAV_KEYS + ["3", "3"] + YES + ["3"]


def test_yes_that_the_game_ignores_is_caught_by_the_toggle(monkeypatch, tmp_path):
    """是 accepted but the page reads off: the ordinary press turns repeat on."""
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "seen:GBFR_See_RepeatOff"),
               screen("tenth-run dialog, cursor on 是", "GBFR_See_TenthRun", "GBFR_See_TenthYes",
                      leave_on="key:Return"),
               rewards("off", "key:3"), rewards("on", "action:GBFR_KeepRepeating", after=2),
               *last_run_turning_it_off()]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_clean_finish(outcome, 3)
    assert outcome.keys == NAV_KEYS + ["3", "Return", "3", "3"]


def test_a_cursor_that_never_reaches_yes_stops_after_three_presses(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "seen:GBFR_See_RepeatOff"),
               screen("tenth-run dialog, stuck on 否", "GBFR_See_TenthRun", "GBFR_See_TenthNo")]
    outcome = run(monkeypatch, tmp_path, screens, limit=5)
    assert_explained_failure(outcome)
    assert "could not be moved to 是" in outcome.session["error"]
    assert outcome.keys == NAV_KEYS + ["3"] + ["Up"] * 3
    assert outcome.session["completed"] == 2


def test_a_dialog_left_unanswered_ends_in_town_as_an_error(monkeypatch, tmp_path):
    """No cursor was read, so nothing was pressed; the game timed the dialog out to town."""
    screens = [*first_run_turning_repeat_on(), *battle(victory=False), titles(),
               rewards("off", "seen:GBFR_See_RepeatOff"),
               screen("tenth-run dialog, no cursor read", "GBFR_See_TenthRun",
                      leave_on="action:GBFR_TenthRun", after=3),
               rewards("off", "action:GBFR_CapHold", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=5)
    assert_explained_failure(outcome)
    assert outcome.session["error"] == "The game returned to town before the session finished"
    assert outcome.session["completed"] == 2
    assert outcome.keys == NAV_KEYS + ["3"]


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
    screens = [*menus()[2:], *battle(victory=True), titles(),
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
    assert outcome.keys == ["Return", "Return", "3", "Escape"]


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
    screens = [*menus(), *battle(victory=False), screen("failed", *failure("off"))]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["completed"] == 0
    assert outcome.session["error"].startswith("The quest failed")
    assert not outcome.session["left_on"]
    assert outcome.keys == NAV_KEYS


def test_a_failure_with_repeat_on_turns_it_off_before_stopping(monkeypatch, tmp_path):
    """Left on, the game would retry the failed quest by itself."""
    screens = [*first_run_turning_repeat_on(), *battle(victory=False),
               screen("failed, repeat on", *failure("on"), leave_on="key:3"),
               screen("failed, repeat off", *failure("off"))]
    outcome = run(monkeypatch, tmp_path, screens, limit=5)
    assert_explained_failure(outcome)
    assert outcome.session["error"].startswith("The quest failed")
    assert outcome.session["completed"] == 1
    assert not outcome.session["left_on"]
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_a_failure_page_whose_toggle_cannot_be_read_is_never_counted(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=False), screen("failed, toggle hidden", *failure(None))]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert "toggle could not be read" in outcome.session["error"]
    assert outcome.session["completed"] == 0
    assert outcome.keys == NAV_KEYS


def test_a_failure_seen_only_on_the_count_recheck_is_not_counted(monkeypatch, tmp_path):
    """The page first reads as plain results (the failure line not drawn yet); the
    count's own recheck sees the failure, so nothing is counted."""
    screens = [*menus(), *battle(victory=False),
               screen("results, failure line not drawn yet", "GBFR_See_Results",
                      leave_on="seen:GBFR_See_Results"),
               screen("failed", *failure("off"))]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["error"].startswith("The quest failed")
    assert outcome.session["completed"] == 0
    assert "本次已计数" not in outcome.messages and not any("次完成" in m for m in outcome.messages)


def test_a_failure_line_drawn_after_the_count_takes_the_count_back(monkeypatch, tmp_path):
    """The page reads as plain results even on the count's own recheck, and the failure
    line shows only after the run was counted: the count is taken back."""
    screens = [*menus(), *battle(victory=False),
               screen("results, failure line not drawn yet", "GBFR_See_Results",
                      leave_on="action:GBFR_Result"),
               screen("failed", *failure("off"))]
    outcome = run(monkeypatch, tmp_path, screens, limit=3)
    assert_explained_failure(outcome)
    assert outcome.session["error"].startswith("The quest failed")
    assert outcome.session["completed"] == 0 and outcome.session["episode"] == 1
    assert outcome.keys == NAV_KEYS


def test_a_late_failure_line_takes_back_only_its_own_run(monkeypatch, tmp_path):
    screens = [*first_run_turning_repeat_on(), *battle(victory=False),
               screen("results, repeat on, failure line not drawn yet", "GBFR_See_Results",
                      "GBFR_See_RepeatOn", leave_on="action:GBFR_Result"),
               screen("failed, repeat on", *failure("on"), leave_on="key:3"),
               screen("failed, repeat off", *failure("off"))]
    outcome = run(monkeypatch, tmp_path, screens, limit=5)
    assert_explained_failure(outcome)
    assert outcome.session["error"].startswith("The quest failed")
    assert outcome.session["completed"] == 1 and outcome.session["episode"] == 2
    assert not outcome.session["left_on"]
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def finish_as_the_action_starts(monkeypatch, node):
    """A finish request that lands after `node`'s gate said go on, just as its action
    starts: the action sees the request, the recognition before it did not."""
    import maa_agent
    original = maa_agent.Hooks._run
    fired = []

    def _run(self, context, argv):
        if argv.node_name == node and not fired:
            fired.append(node)
            self.session.finish_requested = True
        return original(self, context, argv)
    monkeypatch.setattr(maa_agent.Hooks, "_run", _run)
    return fired


def test_a_finish_landing_after_the_gate_still_turns_repeat_off(monkeypatch, tmp_path):
    """The rewards page read repeat on and the gate said go on. The page does show repeat
    on, so that is recorded, and the finish then turns it off as on any rewards page."""
    fired = finish_as_the_action_starts(monkeypatch, "GBFR_KeepRepeating")
    screens = [*first_run_turning_repeat_on()[:-1],
               rewards("on", "action:GBFR_KeepRepeating"),
               rewards("on", "key:3"),
               rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=10)
    assert fired
    assert_clean_finish(outcome, 1)
    assert outcome.session["finish_requested"]
    assert outcome.keys == NAV_KEYS + ["3", "3"]


def test_a_finish_landing_as_yes_is_about_to_be_pressed_sends_no_yes(monkeypatch, tmp_path):
    """The press's own recheck asks the gate again, so 是 is not pressed once a finish
    request has landed, and the dialog is left to time out as at the limit. What the game
    does when it times out with the cursor on 是 was not measured; the script sends it to
    town, as measured with the cursor on 否."""
    fired = finish_as_the_action_starts(monkeypatch, "GBFR_TenthYes")
    screens = first_run_turning_repeat_on()
    for _ in range(8):
        screens += repeated_run_keeping_it_on()
    *tenth, _, _ = tenth_run_answered_yes()
    screens += [*tenth,
                screen("tenth-run dialog, cursor on 是, left to time out", "GBFR_See_TenthRun",
                       "GBFR_See_TenthYes", leave_on="action:GBFR_TenthRun", after=3),
                rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=12)
    assert fired
    assert_clean_finish(outcome, 10)
    assert outcome.session["continued"] == 0
    assert outcome.keys == NAV_KEYS + ["3", "Up"]  # no Return on 是


def test_a_count_whose_recheck_misses_is_retried_not_skipped(monkeypatch, tmp_path):
    """The results page is recognised, then hidden for every recheck: nothing is counted
    from the old frame, and the count is taken again once the page shows."""
    screens = [*menus(), *battle(victory=True),
               screen("results, first look", "GBFR_See_Results", leave_on="seen:GBFR_See_Results"),
               screen("results hidden for a moment", leave_on="action:GBFR_Result"),
               titles(), rewards("off", "action:GBFR_LetItEnd", after=2), town()]
    outcome = run(monkeypatch, tmp_path, screens, limit=1)
    assert_clean_finish(outcome, 1)
    assert outcome.keys == NAV_KEYS
    actions = [kind for _, kind in outcome.log if kind == "action:GBFR_Result"]
    assert len(actions) == 2


def test_a_results_page_that_never_confirms_stops_without_counting(monkeypatch, tmp_path):
    screens = [*menus(), *battle(victory=True),
               screen("results, first look", "GBFR_See_Results", leave_on="seen:GBFR_See_Results"),
               screen("results hidden", leave_on="action:GBFR_Result"),
               screen("results, looked at again", "GBFR_See_Results",
                      leave_on="seen:GBFR_See_Results"),
               screen("results hidden again", leave_on="action:GBFR_Result"),
               screen("results, a third look", "GBFR_See_Results", leave_on="seen:GBFR_See_Results"),
               screen("results hidden a third time", leave_on="action:GBFR_Result"),
               titles()]
    outcome = run(monkeypatch, tmp_path, screens, limit=1)
    assert_explained_failure(outcome)
    assert outcome.session["error"] == "The results screen could not be confirmed for counting"
    assert outcome.session["completed"] == 0
    assert not any("次完成" in m for m in outcome.messages)


@pytest.mark.parametrize("scenario", ["3 on the first results", "是 on the tenth-run dialog"])
def test_a_stop_before_the_toggle_is_read_back_says_repeat_may_be_on(monkeypatch, tmp_path,
                                                                     scenario):
    if scenario == "3 on the first results":
        screens = [*menus(), *battle(victory=True), titles(), rewards("off", "key:3"),
                   screen("results: rewards, repeat on, stopped", "GBFR_See_Results",
                          "GBFR_See_RepeatOn", stop=True)]
        completed = 1
    else:
        screens = first_run_turning_repeat_on()
        for _ in range(8):
            screens += repeated_run_keeping_it_on()
        *tenth, _ = tenth_run_answered_yes()
        screens += [*tenth, screen("results: rewards, repeat on, stopped", "GBFR_See_Results",
                                   "GBFR_See_RepeatOn", stop=True)]
        completed = 10
    outcome = run(monkeypatch, tmp_path, screens, limit=12)
    assert outcome.session["phase"] == "stopped"
    assert outcome.session["completed"] == completed
    assert outcome.session["left_on"] is None
    assert "可能开着" in outcome.session["message"]


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
                # The cursor reads on 否, but the session is done: no Up, no Return.
                screen("tenth-run dialog", "GBFR_See_TenthRun", "GBFR_See_TenthNo",
                       leave_on="action:GBFR_TenthRun", after=3),
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
        assert name == "GBFR_Finished" or event in {"error", "halt", "failed"}, name
