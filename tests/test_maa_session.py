# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from maa_session import Session, SessionError


def first_run(session):
    session.admit()
    session.battle()
    session.settle()


def repeated_run(session):
    session.repeat_on()
    session.battle()
    session.settle()


def test_first_run_from_the_menus_then_repeats_count_once_each():
    session = Session("boss", 3)
    first_run(session)
    repeated_run(session)
    repeated_run(session)
    assert session.completed == session.episode == 3
    assert session.admissions == 1
    assert not session.should_continue
    session.finish()
    assert session.phase == "finished"


def test_a_results_screen_counts_only_once():
    session = Session("boss", 3)
    first_run(session)
    with pytest.raises(SessionError):
        session.settle()
    assert session.completed == 1


@pytest.mark.parametrize("event", ["battle", "settle", "repeat_on", "repeat_off", "tenth_run",
                                   "town", "finish"])
def test_nothing_from_before_the_session_is_adopted(event):
    session = Session("boss", 3)
    with pytest.raises(SessionError):
        getattr(session, event)()
    assert session.completed == session.episode == 0


def test_a_battle_after_results_needs_repeat_turned_on():
    session = Session("boss", 3)
    first_run(session)
    with pytest.raises(SessionError, match="Repeat Quest was off"):
        session.battle()


def test_turning_repeat_on_at_the_limit_is_refused():
    session = Session("boss", 1)
    first_run(session)
    with pytest.raises(SessionError):
        session.repeat_on()
    session.repeat_off()
    session.finish()


def test_a_stop_during_battle_ends_at_that_run_and_counts_it():
    session = Session("boss", 10)
    first_run(session)
    session.repeat_on()
    session.battle()
    session.finish_requested = True
    session.settle()
    assert session.completed == 2
    assert not session.should_continue
    session.repeat_off()
    session.finish()
    assert session.overrun == 0


def test_a_stop_after_repeat_was_left_on_counts_the_started_run_as_overrun():
    session = Session("boss", 2)
    first_run(session)
    session.repeat_on()
    session.battle()
    session.settle()  # limit reached; suppose turning repeat off failed and the game went on
    session.repeat = True
    session.battle()
    session.settle()
    assert session.completed == 3
    assert session.overrun == 1


def test_the_tenth_run_dialog_resets_repeat_and_the_session_restarts_from_town():
    session = Session("boss", 12)
    first_run(session)
    for _ in range(9):
        repeated_run(session)
    session.tenth_run()
    assert session.cap_seen and not session.repeat
    with pytest.raises(SessionError):
        session.battle()
    session.town()
    first_run(session)
    repeated_run(session)
    assert session.completed == session.episode == 12
    assert session.admissions == 2
    assert not session.cap_seen


def test_until_stopped_never_reaches_a_limit():
    session = Session("boss", None)
    first_run(session)
    for _ in range(30):
        repeated_run(session)
    assert session.should_continue
    session.finish_requested = True
    assert not session.should_continue


def test_defeat_never_counts_or_retries():
    session = Session("boss", 10)
    session.admit()
    session.battle()
    session.fail("defeat")
    assert session.completed == 0
    assert not session.should_continue
    with pytest.raises(SessionError):
        session.admit()


def test_finish_before_departure_is_a_clean_finish():
    session = Session("boss", 3)
    session.finish_requested = True
    session.finish()
    assert session.phase == "finished"
    assert session.completed == session.episode == 0


@pytest.mark.parametrize("limit", [0, -1, 1000, True, 1.2, "3"])
def test_invalid_limit_refused(limit):
    with pytest.raises(SessionError):
        Session("boss", limit)
