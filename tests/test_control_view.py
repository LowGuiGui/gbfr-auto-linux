# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The control window's rules, without GTK: what it shows, allows and saves."""

import json


import control_view as cv


# --- where things live and what is started ------------------------------------------------

def test_folders_follow_the_xdg_variables(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_STATE_HOME": str(tmp_path / "s")}
    assert cv.config_dir(env) == tmp_path / "c" / "gbfr-auto-linux"
    assert cv.state_dir(env) == tmp_path / "s" / "gbfr-auto-linux"


def test_a_bundle_runs_the_runner_with_its_own_environment(tmp_path):
    app = tmp_path / "bundle" / "app"
    python = tmp_path / "bundle" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    assert cv.runner_command(app, 12, env={}) == [
        str(python), "-B", str(app / "maa_runner.py"), "--json", "--runs", "12"]
    assert cv.runner_command(app, 12, until_stopped=True, env={})[-2:] == ["--json", "--until-stopped"]
    assert cv.release_command(app, env={}) == [str(python), "-B", str(app / "maa_runner.py"),
                                               "--release-keys"]


def test_the_repository_layout_and_the_test_seam(tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    assert cv.runner_command(tmp_path, 3, env={})[0] == str(python)
    seam = {"GBFR_RUNNER_COMMAND": json.dumps(["py", "scripted.py", "--scenario", "hold"])}
    assert cv.runner_command(tmp_path, 3, env=seam) == ["py", "scripted.py", "--scenario", "hold",
                                                        "--json", "--runs", "3"]
    assert cv.release_command(tmp_path, env=seam) == ["py", "scripted.py", "--scenario", "hold",
                                                      "--release-keys"]


def test_the_version_comes_from_the_interface_and_a_bundles_manifest(tmp_path):
    repo = tmp_path / "repo"
    (repo / "assets").mkdir(parents=True)
    (repo / "assets" / "interface.json").write_text(json.dumps({"version": "0.4.0-dev"}))
    assert cv.app_version(repo) == "0.4.0-dev"
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "interface.json").write_text(json.dumps({"version": "0.4.0-dev"}))
    assert cv.app_version(bundle / "app") == "0.4.0-dev"
    (bundle / "bundle.json").write_text(json.dumps({"source": "0123456789abcdef"}))
    assert cv.app_version(bundle / "app") == "0.4.0-dev (0123456)"
    assert cv.app_version(tmp_path / "nowhere") == ""


# --- settings ---------------------------------------------------------------------------------

def test_missing_or_broken_settings_fall_back_to_the_defaults(tmp_path):
    assert cv.load_settings(tmp_path / "none.json") == cv.default_settings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert cv.load_settings(broken) == cv.default_settings()
    broken.write_text("[1, 2]")
    assert cv.load_settings(broken) == cv.default_settings()


def test_only_valid_saved_values_are_taken(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"runs": 1000, "until_stopped": "yes",
                                "show": {"log": True, "elapsed": 0, "unknown": True},
                                "window": {"width": 520, "height": 50}, "extra": 1}))
    settings = cv.load_settings(path)
    assert settings["runs"] == 10 and settings["until_stopped"] is False
    assert settings["show"]["log"] is True and settings["show"]["elapsed"] is True
    assert "unknown" not in settings["show"]
    assert settings["window"] == {"width": 520, "height": 760}


def test_settings_survive_a_round_trip_and_leave_no_temporary_file(tmp_path):
    path = tmp_path / "config" / "settings.json"
    settings = cv.default_settings()
    settings.update(runs=7, until_stopped=True)
    settings["show"]["history"] = False
    cv.save_settings(path, settings)
    assert cv.load_settings(path) == settings
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_every_information_item_has_a_default_and_only_the_log_starts_hidden():
    shown = cv.default_settings()["show"]
    assert set(shown) == {key for key, _, _ in cv.INFO_ITEMS}
    assert [key for key, value in shown.items() if not value] == ["log"]


# --- what is shown ------------------------------------------------------------------------

def test_durations():
    assert cv.duration(None) == "—"
    assert cv.duration(4.6) == "0:05"
    assert cv.duration(275) == "4:35"
    assert cv.duration(3723) == "1:02:03"
    assert cv.duration(-3) == "0:00"


def test_before_any_session():
    texts = cv.view({"status": {}, "running": False})
    assert texts["quest"] == "等待任务" and texts["count"] == "0 / — 次成功"
    assert texts["phase"] == "等待开始"
    assert texts["elapsed"] == "本次已用 —" and texts["average"] == "平均每轮 —"


def running(status, **state):
    return dict({"status": status, "running": True, "now": 1000.0}, **state)


def test_a_running_session_shows_progress_time_and_average():
    status = {"phase": "battle", "quest": "解决沙漠狂徒", "completed": 3, "limit": 12,
              "failures": 0, "timing": {"started": 700.0, "runs": [100.0, 80.0, 90.0],
                                        "battle_started": 940.0}}
    texts = cv.view(running(status))
    assert texts["quest"] == "解决沙漠狂徒" and texts["count"] == "3 / 12 次成功"
    assert texts["phase"] == "战斗中"
    assert texts["elapsed"] == "本次已用 5:00（这一场打了 1:00）"
    assert texts["average"] == "平均每轮 1:30（3 轮）"
    assert texts["failures"] == "任务失败 0 次"


def test_farming_until_stopped_and_the_phase_notes():
    status = {"phase": "settled", "completed": 9, "limit": None, "repeat": True}
    texts = cv.view(running(status))
    assert texts["count"] == "9 / 一直刷 次成功"
    assert texts["quest"].startswith("当前任务")  # the name comes with the first results screen
    assert texts["phase"] == "已通关，正在结算（再次挑战已打开）"
    texts = cv.view(running({"phase": "battle", "continued": 1, "finish_requested": True}))
    assert texts["phase"] == "战斗中（已在第 10 次确认里选“是” 1 次） · 已收到本轮后停止请求"
    texts = cv.view(running({"phase": "finished", "overrun": 1}, running=False))
    assert texts["phase"] == "已完成，停在城镇 · 多完成 1 次：停止时下一轮已经开始"


def test_stopping_and_an_ended_session():
    assert cv.view(running({"phase": "battle"}, stopping=True))["phase"] == "正在停止……"
    status = {"phase": "error", "message": "看不到游戏画面。", "error": "Native screenshot failed",
              "timing": {"started": 100.0, "runs": []}}
    texts = cv.view({"status": status, "running": False, "now": 9999.0, "ended_at": 400.0})
    assert texts["elapsed"] == "本次已用 5:00"  # frozen at the end, not counting on
    assert texts["message"] == "看不到游戏画面。" and texts["detail"] == "Native screenshot failed"
    assert texts["phase"].startswith("操作已停止")
    refused = {"phase": "error", "message": "没有找到游戏。", "error": "found 0"}
    texts = cv.view({"status": refused, "running": False, "requested": 3})
    assert (texts["phase"], texts["quest"], texts["count"]) == ("没能开始", "等待任务", "0 / 3 次成功")


def test_between_start_and_the_first_snapshot():
    texts = cv.view({"status": {}, "running": True, "requested": 12})
    assert (texts["phase"], texts["count"]) == ("正在连接游戏……", "0 / 12 次成功")
    texts = cv.view({"status": {"phase": "idle"}, "running": True, "requested": None})
    assert (texts["phase"], texts["count"]) == ("正在连接游戏……", "0 / 一直刷 次成功")


def test_only_the_last_few_log_lines_are_shown():
    lines = [f"第 {n}/12 次完成" for n in range(1, 9)]
    assert cv.view({"status": {}, "log": lines})["log"].splitlines() == lines[-cv.LOG_LINES:]


def test_which_controls_can_be_used():
    idle = cv.controls({"status": {}, "running": False})
    assert idle == {"start": True, "settings": True, "finish": False, "stop": False}
    busy = cv.controls(running({"phase": "battle"}))
    assert busy == {"start": False, "settings": False, "finish": True, "stop": True}
    assert cv.controls(running({"finish_requested": True}))["finish"] is False
    assert cv.controls(running({}, finish_sent=True))["finish"] is False  # before the runner confirms
    stopping = cv.controls(running({}, stopping=True))
    assert not stopping["finish"] and not stopping["stop"]
    assert cv.controls({"status": {}, "running": False, "closing": True})["start"] is False
    assert cv.controls({"status": {}, "running": False, "releasing": True})["start"] is False


def test_history_rows():
    rows = cv.history_rows([
        {"ended": "2026-11-23T14:05:09+08:00", "quest": "解决沙漠狂徒", "outcome": "finished",
         "completed": 12, "limit": 12, "elapsed": 1925.0, "average": 158.4, "failures": 0},
        {"ended": "not a date", "quest": None, "outcome": "error", "completed": 0, "limit": None,
         "elapsed": None, "average": None, "failures": 1},
    ])
    assert rows[0] == ("11-23 14:05  解决沙漠狂徒", "已完成 12/12 · 平均每轮 2:38 · 共 32:05")
    assert rows[1] == ("not a date  未读到任务名", "出错停止 0/一直刷 · 任务失败 1 次")


def test_notifications_on_finishing_and_on_errors_only():
    finished = cv.notification("finished", {"quest": "解决沙漠狂徒", "completed": 12})
    assert finished == ("GBFR 自动刷取：刷完了", "解决沙漠狂徒，成功 12 次，游戏停在城镇。")
    assert cv.notification("stopped", {"completed": 3}) is None
    failed = cv.notification("error", {"message": "任务失败了。\n请看一眼游戏。",
                                       "timing": {"started": 1.0}})
    assert failed == ("GBFR 自动刷取：出错停止", "任务失败了。 请看一眼游戏。")
    # Refused at Start (no game, another session): the window says why; no notification.
    assert cv.notification("error", {"phase": "error", "message": "先启动游戏。"}) is None


def test_history_is_read_newest_first_and_bad_lines_are_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    assert cv.read_history(path) == []
    path.write_text("\n".join([json.dumps({"n": 1}), "{broken", json.dumps({"n": 2}),
                               json.dumps({"n": 3})]) + "\n")
    assert [e["n"] for e in cv.read_history(path, 2)] == [3, 2]
    assert [e["n"] for e in cv.read_history(path)] == [3, 2, 1]


def test_keys_are_released_after_a_runner_that_ended_unsure_or_never_said():
    assert cv.release_after_exit(saw_ended=False, release_needed=False)
    assert cv.release_after_exit(saw_ended=True, release_needed=True)
    assert not cv.release_after_exit(saw_ended=True, release_needed=False)


def test_the_view_rules_load_without_the_frameworks():
    # The window runs on the system Python, which has neither MaaFw nor python-xlib.
    import subprocess
    import sys
    from pathlib import Path
    code = ("import sys, control_view, maa_protocol; "
            "print(sorted({'maa_runner', 'maa_agent', 'maa', 'Xlib', 'gi', 'numpy'} & set(sys.modules)))")
    result = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); " + code],
                            cwd=Path(cv.__file__).parent, capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == "[]", result.stderr
