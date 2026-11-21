# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The real pipeline and Agent against a scripted game (see maa_graph.py)."""

import pytest

pytest.importorskip("maa.tasker", reason="MaaFw is not installed")

from maa_graph import run, screen  # noqa: E402


def one_run_from_quest_list():
    return [
        screen("quest list", "GBFR_QuestSelected", leave_on="key:Return"),
        screen("quest details", "GBFR_QuestDetails", leave_on="key:Return"),
        screen("matching", "GBFR_Private", leave_on="key:Return"),
        screen("ready room", "GBFR_ReadyTown", leave_on="key:3"),
        screen("ready confirm", "GBFR_ReadyConfirm", leave_on="key:Return"),
        screen("battle", "GBFR_Battle", leave_on="action:GBFR_Battle", after=3),
        screen("victory", "GBFR_Victory", leave_on="action:GBFR_Victory"),
        screen("results", "GBFR_Result", leave_on="action:GBFR_Result", after=2),
        screen("settlement", "GBFR_SettlementConfirm", leave_on="key:Return"),
        screen("town", "GBFR_SettledTown", "GBFR_Finished", "GBFR_Town"),
    ]


def test_one_run_counts_once_and_finishes_in_town(monkeypatch, tmp_path):
    outcome = run(monkeypatch, tmp_path, one_run_from_quest_list(), limit=1)
    assert outcome.succeeded
    assert outcome.session["completed"] == outcome.session["episode"] == 1
    assert outcome.session["phase"] == "finished"
    assert outcome.session["error"] is None
    assert outcome.keys == ["Return", "Return", "Return", "3", "Return", "Return"]
    assert outcome.final_screen == "town"
    assert all(kind != "native-input" for _, kind in outcome.log)


def test_unknown_start_stops_without_input_or_success(monkeypatch, tmp_path):
    outcome = run(monkeypatch, tmp_path, [screen("unknown")])
    assert not outcome.succeeded
    assert outcome.session["completed"] == outcome.session["episode"] == 0
    assert outcome.session["phase"] == "error"
    assert "supported transition" in outcome.session["error"]
    assert outcome.keys == []


def test_a_results_screen_left_from_before_is_never_counted(monkeypatch, tmp_path):
    stale = [screen("old results", "GBFR_Victory", "GBFR_Result", "GBFR_SettlementConfirm")]
    outcome = run(monkeypatch, tmp_path, stale)
    assert not outcome.succeeded
    assert outcome.session["completed"] == 0
    assert outcome.session["phase"] == "error"
    assert outcome.keys == []
