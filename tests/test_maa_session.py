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
                                   "answer_yes", "finish"])
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


def test_repeat_read_on_after_a_finish_request_is_recorded():
    """The gate said go on, then a finish request landed before the action recorded what
    the page shows. The toggle is on in the game, so the session says so: the finish waits
    until it is turned off, and a run the game starts first is adopted as an overrun."""
    session = Session("boss", 3)
    first_run(session)
    session.finish_requested = True
    session.repeat_on()
    assert session.repeat and session.left_on
    with pytest.raises(SessionError):
        session.finish()
    session.repeat_off()
    session.finish()
    assert session.phase == "finished" and session.completed == 1


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


def test_yes_on_the_tenth_run_dialog_continues_without_another_admission():
    session = Session("boss", 12)
    first_run(session)
    for _ in range(9):
        repeated_run(session)
    session.tenth_run()
    assert session.cap_seen and not session.repeat
    with pytest.raises(SessionError):
        session.battle()  # the dialog reset the toggle: not yet believed on
    session.answer_yes()
    assert not session.cap_seen and not session.repeat
    repeated_run(session)  # the results page reads repeat on again
    repeated_run(session)
    assert session.completed == session.episode == 12
    assert session.admissions == 1
    assert session.continued == 1


@pytest.mark.parametrize("stop", ["limit", "finish", "stopped"])
def test_the_dialog_is_not_answered_when_the_session_should_stop(stop):
    session = Session("boss", 10 if stop == "limit" else 20)
    first_run(session)
    for _ in range(9):
        repeated_run(session)
    session.tenth_run()
    session.finish_requested = stop == "finish"
    if stop == "stopped":
        session.stop()
    with pytest.raises(SessionError):
        session.answer_yes()
    assert session.continued == 0


def test_a_session_needs_no_quest_name():
    session = Session(None, 3)
    first_run(session)
    assert session.quest is None and session.completed == 1


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


def test_a_count_is_taken_back_when_its_page_turns_out_to_be_a_failure():
    session = Session("boss", 3)
    first_run(session)
    session.repeat_on()
    session.battle()
    session.settle()
    session.repeat_on()  # read on the page before its failure line showed
    session.uncount()
    assert session.completed == 1 and session.episode == 2
    assert session.phase == "battle" and not session.repeat
    session.fail("The quest failed")
    assert session.completed == 1
    with pytest.raises(SessionError):
        Session("boss", 3).uncount()  # nothing was counted


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


def test_a_stop_is_its_own_ending_not_an_error():
    session = Session("boss", 3)
    session.admit()
    session.battle()
    session.stop()
    assert session.phase == "stopped"
    assert session.error is None
    assert not session.should_continue
    finished = Session("boss", 1)
    first_run(finished)
    finished.finish()
    finished.stop()  # a late stop notification never rewrites a finished session
    assert finished.phase == "finished"


def test_left_on_follows_the_games_toggle_across_results_screens():
    session = Session("boss", 5)
    first_run(session)
    assert not session.left_on
    session.repeat_on()
    session.battle()
    session.settle()
    # The session re-reads the toggle on each results page, but the game keeps it on.
    assert session.left_on and not session.repeat
    session.tenth_run()
    assert not session.left_on
    session.answer_yes()
    assert session.left_on is None  # the game turns it on; believed only once a page reads it
    session.repeat_on()
    assert session.left_on
    session.toggle_sent()
    assert session.left_on is None  # 3 pressed, the page not read back yet
    session.repeat_off()
    assert session.left_on is False
