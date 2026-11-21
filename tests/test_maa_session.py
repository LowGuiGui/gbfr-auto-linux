# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from maa_session import Session, SessionError


def complete(session):
    session.admit()
    session.battle()
    session.victory()
    session.settle()


def test_exact_success_limit_and_duplicate_result():
    session = Session("boss", 3)
    for _ in range(3):
        complete(session)
        with pytest.raises(SessionError):
            session.victory()
        with pytest.raises(SessionError):
            session.settle()
    assert session.completed == session.episode == 3
    assert not session.should_continue
    with pytest.raises(SessionError):
        session.admit()


def test_delayed_stop_counts_current_run_only_after_settlement():
    session = Session("boss", 10)
    session.admit()
    session.battle()
    session.finish_requested = True
    assert session.completed == 0
    session.victory()
    session.victory()  # Lingering victory frames are harmless until settlement.
    assert session.completed == 0
    session.settle()
    assert session.completed == 1
    assert not session.should_continue


@pytest.mark.parametrize("event", ["battle", "victory", "settle"])
def test_cannot_adopt_old_battle_or_result(event):
    session = Session("boss")
    with pytest.raises(SessionError):
        getattr(session, event)()
    assert session.completed == session.episode == 0


def test_defeat_never_counts_or_retries():
    session = Session("boss", 10)
    session.admit()
    session.battle()
    session.fail("defeat")
    assert session.completed == 0
    assert not session.should_continue
    with pytest.raises(SessionError):
        session.admit()


@pytest.mark.parametrize("limit", [0, -1, 1000, True, 1.2, "3"])
def test_invalid_limit_refused(limit):
    with pytest.raises(SessionError):
        Session("boss", limit)
