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
    second = feed(planner, [(400, "tips", None), (401, "tips", None), (403, "battle", None)])
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
