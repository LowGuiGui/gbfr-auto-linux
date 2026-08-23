# -*- coding: utf-8 -*-
"""#14 —— 认不出页面时不能无限期往游戏里敲键。"""

import pytest

import config
import main
from main import PAGE_NAME


class FakeOption:
    def __init__(self):
        self.actions = []

    def start_battle(self):
        self.actions.append("start_battle")

    def end_battle(self):
        pass

    def switch_again(self):
        self.actions.append("switch_again")

    def tap_enter(self):
        self.actions.append("tap_enter")


class Loop:
    """借用真实的 _analyze_page / _advance_unknown_page，喂一串预设页面。"""

    _analyze_page = main.App._analyze_page
    _advance_unknown_page = main.App._advance_unknown_page
    MAX_BLIND_TAPS = main.App.MAX_BLIND_TAPS      # property，会读 self.cfg

    def __init__(self, pages, overrides=None):
        self.cfg = config.Config(config._merged(overrides or {}))
        self._option = FakeOption()
        self._pages = list(pages)
        self._unknown_streak = 0
        self._last_logged_page = None
        self.ui = []

    def _get_current_page_name(self):
        return self._pages.pop(0)

    def log(self, message):
        self.ui.append(message)

    def run(self):
        while self._pages:
            self._analyze_page()
        return self


@pytest.fixture
def cap():
    return config.DEFAULTS["loop"]["max_blind_taps"]


def test_blind_tapping_is_capped(cap, log_file):
    loop = Loop([PAGE_NAME.UNKNOWN] * 40).run()
    assert loop._option.actions == ["tap_enter"] * cap
    assert "已停止向游戏发送按键" in log_file()


def test_the_warning_fires_exactly_once(cap, log_file):
    Loop([PAGE_NAME.UNKNOWN] * 40).run()
    assert log_file().count("已停止向游戏发送按键") == 1


def test_recovery_restores_the_full_budget(cap, log_file):
    loop = Loop(
        [PAGE_NAME.UNKNOWN] * 8 + [PAGE_NAME.BATTLE] * 2 + [PAGE_NAME.UNKNOWN] * 8
    ).run()
    assert loop._option.actions.count("tap_enter") == cap * 2
    assert "页面识别已恢复" in log_file()


def test_the_cap_is_configurable(log_file):
    loop = Loop([PAGE_NAME.UNKNOWN] * 40, overrides={"loop": {"max_blind_taps": 2}}).run()
    assert loop._option.actions == ["tap_enter"] * 2


def test_recognised_pages_are_never_capped(log_file):
    """SCORE / PAUSE / REWARD_* 都是认出来的页面，按键有依据。"""
    pages = [PAGE_NAME.PAUSE] * 20
    loop = Loop(pages).run()
    assert loop._option.actions == ["tap_enter"] * 20


def test_normal_cycle_is_unchanged(log_file):
    loop = Loop([
        PAGE_NAME.BATTLE, PAGE_NAME.BATTLE, PAGE_NAME.SCORE,
        PAGE_NAME.REWARD_EXIT, PAGE_NAME.REWARD_AGAIN, PAGE_NAME.PAUSE,
    ]).run()
    assert loop._option.actions == [
        "start_battle", "start_battle", "tap_enter",
        "switch_again", "tap_enter", "tap_enter",
    ]


def test_ui_log_shows_transitions_not_every_tick(log_file):
    """每 3 秒一行重复内容会把真正要紧的告警埋掉。"""
    loop = Loop([PAGE_NAME.BATTLE] * 10 + [PAGE_NAME.PAUSE] * 10).run()
    assert loop.ui == ["当前页面: battle", "当前页面: pause"]


def test_every_tick_still_reaches_the_log_file(log_file):
    Loop([PAGE_NAME.BATTLE] * 5).run()
    assert log_file().count("当前页面: battle") == 5
