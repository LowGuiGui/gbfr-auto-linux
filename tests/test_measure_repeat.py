# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse

import pytest

from tools.measure_repeat import ROOT, Observation, Planner, Settings, private_out, repeat_state


def feed(planner, steps):
    """steps: (time, screen, repeat) tuples; returns every key pressed, with its time."""
    pressed = []
    for t, screen, repeat in steps:
        for key in planner.observe(Observation(t, screen, repeat)):
            pressed.append((t, key))
    return pressed


def events(planner, kind):
    return [e for e in planner.log if e["event"] == kind]


def test_repeat_button_text():
    assert repeat_state("③再次挑战") == "off"
    assert repeat_state("③ 撤销再次挑战") == "on"
    assert repeat_state("躲避大师") is None
    assert repeat_state(None) is None


def test_turns_repeat_on_once_on_the_page_with_the_button_and_reads_it_back():
    planner = Planner(Settings())
    pressed = feed(planner, [(0, "battle", None), (300, "victory", None),
                             (337, "results", None), (360, "results", None),  # titles page
                             (367, "results", "off"), (368, "results", "off"),
                             (369, "results", "on"), (380, "results", "on")])
    assert pressed == [(367, "3")]
    assert planner.runs == 1
    assert events(planner, "repeat_verified")[0]["latency"] == 2


def test_an_unchanged_button_gets_one_retry_and_then_no_more_input():
    planner = Planner(Settings())
    pressed = feed(planner, [(0, "battle", None), (10, "results", "off"), (11, "results", "off"),
                             (13, "results", "off"), (16, "results", "off"),
                             (17, "results", "off"), (30, "results", "off")])
    assert pressed == [(10, "3"), (13, "3")]
    assert events(planner, "repeat_unverified")[0]["seen"] == "off"


def test_a_flicker_between_results_frames_is_not_a_second_run():
    planner = Planner(Settings())
    feed(planner, [(0, "battle", None), (10, "results", None), (11, "unknown", None),
                   (12, "results", None), (20, "battle", None), (30, "results", None)])
    assert planner.runs == 2


def test_a_results_screen_already_showing_at_the_start_is_not_a_run():
    planner = Planner(Settings())
    feed(planner, [(0, "results", None), (5, "tips", None), (6, "results", None)])
    assert planner.runs == 0
    feed(planner, [(10, "battle", None), (20, "results", None)])
    assert planner.runs == 1


def test_repeat_is_turned_off_on_the_chosen_run():
    planner = Planner(Settings(off_at=2))
    pressed = feed(planner, [(0, "battle", None), (10, "results", "on"),
                             (20, "battle", None), (30, "results", "on"), (31, "results", "off")])
    assert pressed == [(30, "3")]
    assert events(planner, "repeat_verified")[0]["state"] == "off"


def test_the_run_cap_turns_repeat_off_even_without_off_at():
    planner = Planner(Settings(max_runs=1))
    assert feed(planner, [(0, "battle", None), (10, "results", "on")]) == [(10, "3")]


def test_loading_tips_wait_then_continue_and_auto_skip_is_pressed_once_on_its_screen():
    planner = Planner(Settings(tips_wait=15, auto_skip_at=2))
    first = feed(planner, [(0, "tips", None), (10, "tips", None), (15, "tips", None),
                           (16, "battle", None)])
    second = obs_feed(planner, [Observation(400, "tips", auto_skip="off"),
                                Observation(401, "tips", auto_skip="off"),
                                Observation(403, "battle")])
    assert first == [(15, "Return")]
    assert second == [(400, "Escape")]
    left = events(planner, "tips_left")
    assert [e["auto_advanced"] for e in left] == [False, True]


def test_an_unrecognised_screen_never_gets_input_and_ends_the_run_at_its_limit():
    planner = Planner(Settings(unknown_limit=120))
    pressed = feed(planner, [(0, "battle", None), (10, "results", "on"), (40, "unknown", None),
                             (100, "unknown", None), (160, "unknown", None),
                             (161, "results", "off")])
    assert pressed == []
    assert planner.finished == "an unrecognised screen lasted 120 s"


def test_the_ready_room_before_departure_gets_a_longer_grace():
    planner = Planner(Settings(start_grace=300, unknown_limit=120))
    feed(planner, [(0, "unknown", None), (200, "unknown", None)])
    assert planner.finished is None
    feed(planner, [(301, "unknown", None)])
    assert planner.finished == "an unrecognised screen lasted 300 s"


def test_a_results_screen_that_never_advances_ends_the_run():
    planner = Planner(Settings(screen_limit=150))
    feed(planner, [(0, "battle", None), (10, "results", "on"), (170, "results", "on")])
    assert planner.finished == "the results screen lasted 150 s"


def test_town_ends_the_run_once_it_is_stable():
    planner = Planner(Settings(town_settle=5))
    feed(planner, [(0, "town", None), (3, "town", None)])
    assert planner.finished is None
    feed(planner, [(6, "town", None)])
    assert planner.finished == "town reached"
    assert feed(planner, [(7, "results", "off")]) == []


def test_the_press_budget_stops_the_run():
    planner = Planner(Settings(max_presses=1))
    feed(planner, [(0, "battle", None), (10, "results", "off"), (13, "results", "off")])
    assert planner.presses == 1
    assert planner.finished == "the press budget was used up"


def test_frames_may_not_be_written_where_git_would_track_them(tmp_path):
    existing = tmp_path / "already"
    existing.mkdir()
    with pytest.raises(argparse.ArgumentTypeError, match="new output"):
        private_out(str(existing))
    assert private_out(str(tmp_path / "fresh")) == (tmp_path / "fresh").resolve()
    with pytest.raises(argparse.ArgumentTypeError, match="private"):
        private_out(str(ROOT / "tools" / "repeat-frames"))


def obs_feed(planner, steps):
    """steps: Observation values; returns every key pressed, with its time."""
    pressed = []
    for obs in steps:
        for key in planner.observe(obs):
            pressed.append((obs.time, key))
    return pressed


def test_the_toggle_waits_out_the_reward_reveal():
    planner = Planner(Settings(reveal=2.5))
    pressed = feed(planner, [(0, "battle", None), (300, "results", None),
                             (330, "results", "off"), (331, "results", "off"),
                             (332.6, "results", "off"), (333, "results", "on")])
    assert pressed == [(332.6, "3")]


def tenth_page(planner):
    """Settle run 10 and show its rewards page with the toggle reset to off."""
    planner.runs = 9
    return [Observation(0, "battle"), Observation(300, "results"),
            Observation(330, "results", "off"), Observation(332, "results", "off")]


def test_the_tenth_run_dialog_is_answered_yes_only_after_the_cursor_is_seen_on_it():
    planner = Planner(Settings(reveal=2.5, tenth_answer="yes"))
    steps = tenth_page(planner) + [
        Observation(332.5, "tenth", choice="no"), Observation(333, "tenth", choice="no"),
        Observation(333.6, "tenth", choice="no"),  # one second after the dialog: Up
        Observation(334, "tenth", choice="yes"),   # the cursor moved: Return
        Observation(335, "results", "on"), Observation(340, "results", "on")]
    pressed = obs_feed(planner, steps)
    assert pressed == [(333.6, "Up"), (334, "Return")]
    assert events(planner, "repeat_state_after_yes")[0]["state"] == "on"
    assert planner.runs == 10


def test_without_an_answer_the_dialog_and_its_page_get_no_input():
    planner = Planner(Settings(reveal=2.5))
    steps = tenth_page(planner) + [Observation(332.5, "tenth", choice="no"),
                                   Observation(340, "tenth", choice="no"),
                                   Observation(359, "results", "off"), Observation(361, "town")]
    assert obs_feed(planner, steps) == []


def test_a_cursor_that_does_not_move_is_never_confirmed():
    planner = Planner(Settings(reveal=2.5, tenth_answer="yes"))
    steps = tenth_page(planner) + [Observation(332.5 + i, "tenth", choice="no") for i in range(12)]
    pressed = obs_feed(planner, steps)
    assert [key for _, key in pressed] == ["Up", "Up"]
    assert events(planner, "tenth_unanswered")


def test_w_pulses_go_only_to_the_chosen_runs_battles_and_their_times_are_kept():
    planner = Planner(Settings(hold_w="even"))
    pressed = feed(planner, [(0, "battle", None), (5, "battle", None), (300, "results", None),
                             (330, "results", "on"), (370, "battle", None), (373, "battle", None),
                             (376, "victory", None), (650, "results", None)])
    assert pressed == [(370, "w"), (373, "w")]
    settled = events(planner, "run_settled")
    assert [e["battle_seconds"] for e in settled] == [300, 280]
    assert [e["w_pulses"] for e in settled] == [0, 2]
    assert planner.presses == 0  # pulses have their own budget, not the key budget


def test_a_failure_screen_gets_no_input_and_ends_the_run():
    planner = Planner(Settings())
    pressed = feed(planner, [(0, "battle", None), (200, "defeat", None), (205, "defeat", None),
                             (211, "defeat", None)])
    assert pressed == []
    assert planner.finished == "failure screen"


def test_escape_is_never_pressed_while_auto_skip_reads_on():
    planner = Planner(Settings(auto_skip_at=1))
    pressed = obs_feed(planner, [Observation(0, "tips", auto_skip="on"),
                                 Observation(1, "tips", auto_skip="on")])
    assert pressed == []
    assert events(planner, "auto_skip_label")[0]["state"] == "on"


def test_escape_waits_for_a_frame_whose_label_reads_off():
    planner = Planner(Settings(auto_skip_at=1, tips_wait=100))
    unreadable = obs_feed(planner, [Observation(0, "tips"), Observation(1, "tips")])
    readable = obs_feed(planner, [Observation(2, "tips", auto_skip="off")])
    assert unreadable == []
    assert readable == [(2, "Escape")]
