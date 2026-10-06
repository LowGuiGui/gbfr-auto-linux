# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""gbfr_auto.py：命令行把找游戏、截图、输入、循环和控制套接字接到一起。

游戏和嵌套 X 是替身（嵌套 X 用 test_xtest_input 的那个，页面用 test_farm 拼出来的画面），
控制套接字是真的。仓库目录和 XDG_RUNTIME_DIR 都换到 tmp_path 里，不碰真的 logs/、
gbfr_auto.toml 和运行时目录。
"""

import logging
import os
import signal
from types import SimpleNamespace

import pytest

import applog
import control
import gamescope
import gbfr_auto
from pagetree import PAGE_NAME
from test_farm import TEMPLATES, frame_of
from test_gamescope import GAME_ENV, fake_proc
from test_xtest_input import nested


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    """这里的测试一个都不该碰真的游戏：所有者的游戏可能正开着。忘了换掉找游戏、连嵌套 X 的
    测试会连上真的游戏，带 --live 的还会真的按键；忘了换掉截图的会跑真的 gamescopectl。所以
    连嵌套 X 和起进程在这里一律拦下。命令行把出错都接住了，所以收尾时再看一次有没有人试过。"""
    attempts = []

    def refuse(what):
        def refused(*args, **kwargs):
            attempts.append(what)
            raise AssertionError(f"a test reached the real {what}")
        return refused
    monkeypatch.setattr(gamescope, "connect_x", refuse("nested X server"))
    monkeypatch.setattr(gamescope.subprocess, "Popen", refuse("subprocess"))
    yield
    assert not attempts, f"a test reached something real: {attempts!r}"


@pytest.fixture
def home(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    # 一轮 10 毫秒，测试里不等真的 3 秒；每一页最多 1 秒，该停不停的循环会以"超过"结束、
    # 退出码 1，而不是一直跑下去
    (repo / "gbfr_auto.toml").write_text(
        "[loop]\npoll_interval_ms = 10\nmax_page_s = 1\nmax_battle_s = 1\n", encoding="utf-8")
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setattr(gbfr_auto, "REPO", repo)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    _reset_logging()
    yield SimpleNamespace(repo=repo, runtime=runtime, socket=runtime / control.SOCKET_NAME)
    _reset_logging()


def _reset_logging():
    """applog.setup 只认第一次的目录；每个测试都要让日志落到自己的 tmp_path 里。"""
    applog._file_handler = None
    logger = logging.getLogger(applog.ROOT_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


class FakeCapture:
    """gamescope.ScreenshotCapture 的替身：按顺序给出这些页面的画面。on_call(次数) 给测试
    在截图的那一刻插一手（发信号、发 stop）。"""

    def __init__(self, pages, on_call=None):
        self.pages = list(pages)
        self.on_call = on_call
        self.calls = 0
        self.last_ms = 5

    def __call__(self):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return frame_of(page)


@pytest.fixture
def game(monkeypatch):
    d, w = nested()
    d.closed = False
    d.close = lambda: setattr(d, "closed", True)
    w.game.get_geometry = lambda: SimpleNamespace(x=0, y=0, width=200, height=180)
    values = {"display": ":9", "wayland": "gamescope-9", "xauth": None, "runtime": "/run/test"}
    monkeypatch.setattr(gbfr_auto, "find_game", lambda appid, proc="/proc": ("gamescope 1", values))
    monkeypatch.setattr(gbfr_auto, "connect", lambda values, appid: (d, w.game))
    monkeypatch.setattr(gbfr_auto.farm, "load_templates", lambda directory, scale=1.0: TEMPLATES)
    state = SimpleNamespace(d=d, w=w, capture=FakeCapture([PAGE_NAME.BATTLE, PAGE_NAME.SCORE]))

    def make_capture(wayland, runtime, size, directory, stop=None):
        state.capture_args = (wayland, runtime, size, directory)
        return state.capture
    monkeypatch.setattr(gbfr_auto.gamescope, "ScreenshotCapture", make_capture)
    return state


class TestRun:
    def test_a_dry_run_recognises_and_sends_nothing(self, home, game, capsys):
        assert gbfr_auto.main(["run", "--repeats", "1"]) == 0
        assert game.d.events == [] and game.d.closed
        assert not os.path.lexists(home.socket)
        assert "空跑" in capsys.readouterr().out
        log = (home.repo / "logs" / applog.LOG_FILENAME).read_text(encoding="utf-8")
        assert "[空跑] 本应发送: 按下 w" in log and "完成第 1 次战斗" in log

    def test_the_capture_gets_the_game_s_instance_and_size(self, home, game):
        gbfr_auto.main(["run", "--repeats", "1"])
        wayland, runtime, size, directory = game.capture_args
        assert (wayland, runtime, size) == ("gamescope-9", "/run/test", (200, 180))
        assert directory == home.runtime / gamescope.CAPTURE_DIR_NAME

    def test_a_live_run_sends_and_lets_go_at_the_end(self, home, game):
        from Xlib import X
        assert gbfr_auto.main(["run", "--live", "--repeats", "1"]) == 0
        events = game.d.events
        assert (X.KeyPress, 25) in events and (X.ButtonPress, 2) in events
        assert events.index((X.KeyRelease, 25)) > events.index((X.KeyPress, 25))
        assert events.index((X.ButtonRelease, 2)) > events.index((X.ButtonPress, 2))

    def test_ctrl_c_stops_it_cleanly(self, home, game):
        before = signal.getsignal(signal.SIGINT)
        game.capture.on_call = lambda n: os.kill(os.getpid(), signal.SIGINT) if n == 1 else None
        assert gbfr_auto.main(["run"]) == 0
        assert signal.getsignal(signal.SIGINT) is before

    def test_a_stop_command_reaches_the_running_loop(self, home, game):
        game.capture.on_call = (lambda n: control.send("stop", home.socket) if n == 2 else None)
        assert gbfr_auto.main(["run"]) == 0
        assert game.capture.calls == 2

    def test_a_second_loop_does_not_start(self, home, game, monkeypatch):
        monkeypatch.setattr(gbfr_auto, "find_game", lambda *a, **k: pytest.fail("looked for the game"))
        first = control.ControlServer(control.Controls(), home.socket).start()
        try:
            assert gbfr_auto.main(["run"]) == 1
        finally:
            first.close()

    def test_without_the_game_it_does_not_start(self, home, monkeypatch, capsys):
        monkeypatch.setattr(gbfr_auto.gamescope, "find_game_processes", lambda appid, proc: [])
        assert gbfr_auto.main(["run"]) == 1
        assert "没有找到游戏" in capsys.readouterr().err
        assert not os.path.lexists(home.socket)

    def test_a_display_that_is_not_gamescope_s_is_refused(self, home, game, monkeypatch):
        d, w = nested(props={})
        d.close = lambda: None
        w.game.get_geometry = lambda: SimpleNamespace(x=0, y=0, width=200, height=180)
        monkeypatch.setattr(gbfr_auto, "connect", lambda values, appid: (d, w.game))
        assert gbfr_auto.main(["run", "--live"]) == 1
        assert d.events == []

    def test_an_x_error_at_startup_is_a_refusal_not_a_traceback(self, home, game, capsys):
        from Xlib import error as xerror

        def vanished():
            raise xerror.DisplayConnectionError(":9", "connection refused")
        game.w.game.get_geometry = vanished
        assert gbfr_auto.main(["run"]) == 1
        assert "没有开始" in capsys.readouterr().err
        assert game.d.closed and not os.path.lexists(home.socket)

    def test_a_setting_out_of_range_is_a_refusal_not_a_traceback(self, home, game, capsys):
        """配置只查类型；循环不肯接的数（这里是阈值），命令行要说清楚，套接字也要收回。"""
        with open(home.repo / "gbfr_auto.toml", "a", encoding="utf-8") as f:
            f.write("[detect]\nthreshold = -1.0\n")
        assert gbfr_auto.main(["run"]) == 1
        assert "detect.threshold" in capsys.readouterr().err
        assert game.d.closed and not os.path.lexists(home.socket)

    def test_a_limit_ends_it_with_a_failure_code(self, home, game):
        """宁可停下的那几种（这里是连着三次截不到）算没跑完：退出码 1。"""
        game.capture.__class__ = type("NoFrames", (FakeCapture,), {"__call__": lambda self: None})
        assert gbfr_auto.main(["run"]) == 1


class TestCommands:
    @pytest.fixture
    def running(self, home):
        controls = control.Controls()
        server = control.ControlServer(controls, home.socket,
                                       status=lambda: {"page": "battle", "battles": 3}).start()
        yield controls
        server.close()

    def test_pause_resume_and_stop_reach_the_loop(self, running):
        assert gbfr_auto.main(["pause"]) == 0 and running.paused
        assert gbfr_auto.main(["resume"]) == 0 and not running.paused
        assert gbfr_auto.main(["stop"]) == 0 and running.stop_requested

    def test_status_says_what_it_is_doing(self, running, capsys):
        assert gbfr_auto.main(["status"]) == 0
        out = capsys.readouterr().out
        assert "page battle" in out and "battles 3" in out

    def test_without_a_loop_it_says_so(self, home, capsys):
        assert gbfr_auto.main(["stop"]) == 1
        assert "没有循环在跑" in capsys.readouterr().err

    def test_release_lets_go_of_every_configured_key_and_the_middle_button(self, home, game):
        from Xlib import X
        assert gbfr_auto.main(["release"]) == 0
        assert sorted(game.d.events) == sorted([(X.KeyRelease, 25), (X.KeyRelease, 12),
                                                (X.KeyRelease, 38), (X.ButtonRelease, 2)])


class TestFindingTheGame:
    def test_none_running_is_refused(self, tmp_path):
        with pytest.raises(gbfr_auto.GameNotFound, match="没有找到游戏"):
            gbfr_auto.find_game("881020", fake_proc(tmp_path, {}))

    def test_two_instances_are_refused(self, tmp_path):
        other = dict(GAME_ENV, DISPLAY=":4", GAMESCOPE_WAYLAND_DISPLAY="gamescope-2")
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV}, 105: {"environ": other}})
        with pytest.raises(gbfr_auto.GameNotFound, match="2 个"):
            gbfr_auto.find_game("881020", proc)

    def test_one_instance_gives_its_values(self, tmp_path):
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV}})
        _, values = gbfr_auto.find_game("881020", proc)
        assert values["display"] == ":3" and values["wayland"] == "gamescope-1"


class TestHelpers:
    def test_a_window_that_is_gone_is_seen_as_gone(self):
        def gone():
            raise ConnectionError("BadWindow")
        window = SimpleNamespace(id=0x400000, get_geometry=gone)
        obs = gbfr_auto.observer(window, SimpleNamespace(is_ready=lambda: True))()
        assert not obs.hwnd_valid and obs.hwnd is None

    def test_the_centre_is_the_window_s_own(self):
        window = SimpleNamespace(get_geometry=lambda: SimpleNamespace(width=2560, height=1440))
        assert gbfr_auto.centre_of(window)() == (1280, 720)

    @pytest.mark.parametrize("argv", [["run", "--repeats", "0"], ["run", "--repeats", "-2"], []])
    def test_bad_arguments_are_refused(self, argv):
        with pytest.raises(SystemExit):
            gbfr_auto.parse_args(argv)

    def test_run_is_a_dry_run_unless_live(self):
        args = gbfr_auto.parse_args(["run"])
        assert args.live is False and args.repeats is None
        assert gbfr_auto.parse_args(["run", "--live", "--repeats", "3"]).repeats == 3
