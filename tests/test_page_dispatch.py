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

    def tap_confirm(self):
        self.actions.append("tap_confirm")


class Loop:
    """借用真实的 _analyze_page / _advance_unknown_page，喂一串预设页面。"""

    _analyze_page = main.App._analyze_page
    _toggle_battle = main.App._toggle_battle
    _act_on_page = main.App._act_on_page
    _advance_unknown_page = main.App._advance_unknown_page
    _save_anomaly_frame = main.App._save_anomaly_frame
    MAX_BLIND_TAPS = main.App.MAX_BLIND_TAPS      # property，会读 self.cfg

    def __init__(self, pages, overrides=None):
        self.cfg = config.Config(config._merged(overrides or {}))
        self._anomalies_saved = 0
        self.screen = None
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
    assert loop._option.actions == ["tap_confirm"] * cap
    assert "已停止向游戏发送按键" in log_file()


def test_the_warning_fires_exactly_once(cap, log_file):
    Loop([PAGE_NAME.UNKNOWN] * 40).run()
    assert log_file().count("已停止向游戏发送按键") == 1


def test_recovery_restores_the_full_budget(cap, log_file):
    loop = Loop(
        [PAGE_NAME.UNKNOWN] * 8 + [PAGE_NAME.BATTLE] * 2 + [PAGE_NAME.UNKNOWN] * 8
    ).run()
    assert loop._option.actions.count("tap_confirm") == cap * 2
    assert "页面识别已恢复" in log_file()


def test_the_cap_is_configurable(log_file):
    loop = Loop([PAGE_NAME.UNKNOWN] * 40, overrides={"loop": {"max_blind_taps": 2}}).run()
    assert loop._option.actions == ["tap_confirm"] * 2


def test_recognised_pages_are_never_capped(log_file):
    """SCORE / PAUSE / REWARD_* 都是认出来的页面，按键有依据。"""
    pages = [PAGE_NAME.PAUSE] * 20
    loop = Loop(pages).run()
    assert loop._option.actions == ["tap_confirm"] * 20


def test_normal_cycle_is_unchanged(log_file):
    loop = Loop([
        PAGE_NAME.BATTLE, PAGE_NAME.BATTLE, PAGE_NAME.SCORE,
        PAGE_NAME.REWARD_EXIT, PAGE_NAME.REWARD_AGAIN, PAGE_NAME.PAUSE,
    ]).run()
    assert loop._option.actions == [
        "start_battle", "start_battle", "tap_confirm",
        "switch_again", "tap_confirm", "tap_confirm",
    ]


def test_ui_log_shows_transitions_not_every_tick(log_file):
    """每 3 秒一行重复内容会把真正要紧的告警埋掉。"""
    loop = Loop([PAGE_NAME.BATTLE] * 10 + [PAGE_NAME.PAUSE] * 10).run()
    assert loop.ui == ["当前页面: battle", "当前页面: pause"]


def test_every_tick_still_reaches_the_log_file(log_file):
    Loop([PAGE_NAME.BATTLE] * 5).run()
    assert log_file().count("当前页面: battle") == 5


# --- #16：表驱动派发 ------------------------------------------------------
#
# 表的价值不在于少写几个 elif，而在于"漏了一个页面"可以被机器发现。原来的
# `else: tap_confirm` 会把任何新页面都默默按一下确认键。

class TestPageActionTable:
    def test_every_page_has_a_home(self):
        """新增 PAGE_NAME 却忘了配动作，就该在这里红。

        而不是在游戏里对着一个谁也没想过的页面反复按键 —— 那是 PLANNING §2
        功能 4 第 5 条描述的失败模式。
        """
        handled = set(main.PAGE_ACTIONS) | {PAGE_NAME.BATTLE, PAGE_NAME.UNKNOWN}
        missing = set(PAGE_NAME) - handled
        assert not missing, f"这些页面没有归宿: {sorted(p.value for p in missing)}"

    def test_battle_and_unknown_are_deliberately_absent(self):
        """它们不是"按个键推进"，被排除是有意的，不是漏了。"""
        assert PAGE_NAME.BATTLE not in main.PAGE_ACTIONS
        assert PAGE_NAME.UNKNOWN not in main.PAGE_ACTIONS

    def test_every_action_names_a_real_option_method(self):
        """表里写的是方法名字符串，拼错了只会在真机上炸。"""
        from option import Option
        for page, action in main.PAGE_ACTIONS.items():
            assert hasattr(Option, action), \
                f"{page.value} 指向了 Option 上不存在的 {action!r}"

    def test_an_unmapped_page_warns_instead_of_pressing_something(self, caplog):
        """漏配的页面必须是"什么都不做 + 告警"，不能沿用旧的默认按确认键。"""
        loop = Loop([])
        loop.page_name = PAGE_NAME.SCORE
        with caplog.at_level("WARNING"):
            original = main.PAGE_ACTIONS.pop(PAGE_NAME.SCORE)
            try:
                loop._act_on_page()
            finally:
                main.PAGE_ACTIONS[PAGE_NAME.SCORE] = original
        assert loop._option.actions == [], "漏配的页面不该触发任何输入"
        assert any("没有配置动作" in r.getMessage() for r in caplog.records)


class TestConcernsAreSeparated:
    """#16 的另一半：进出战斗和"这一页做什么"不该缠在一条 if/elif 里。"""

    def test_battle_stops_the_frame(self):
        loop = Loop([])
        loop.page_name = PAGE_NAME.BATTLE
        assert loop._toggle_battle() is True, "战斗页要到此为止，不再做别的"
        assert loop._option.actions == ["start_battle"]

    def test_any_other_page_releases_and_continues(self):
        loop = Loop([])
        loop.page_name = PAGE_NAME.SCORE
        assert loop._toggle_battle() is False
