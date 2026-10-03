# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""输入后端的动作词汇。

这一层的价值全在"两种后端对同一个意图给出各自正确的动作"，以及**换后端时不能
把按着的键落下**。后者不是理论问题：hold_move 按住 W 之后换后端，松开的那一下
会发给另一个后端，W 就永远按着了。
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import backend as backend_mod  # noqa: E402

KEYS = {"move": "w", "again": "3", "confirm": "a"}


class FakeWI:
    def __init__(self, mode="inject", ready=True):
        self.calls = []
        self.mode = mode
        self._ready = ready

    def is_ready(self):
        return self._ready

    def key_press(self, k):
        self.calls.append(("press", k))

    def key_release(self, k):
        self.calls.append(("release", k))

    def key_tap(self, k):
        self.calls.append(("tap", k))

    def mouse_press(self, x, y, b):
        self.calls.append(("mdown", x, y, b))

    def mouse_release(self, x, y, b):
        self.calls.append(("mup", x, y, b))


class FakePad:
    def __init__(self, explode=False):
        self.reports = []
        self.explode = explode

    def send(self, report):
        if self.explode:
            raise RuntimeError("手柄掉了")
        self.reports.append((report.wButtons, report.sThumbLY))


@pytest.fixture
def kmb():
    wi = FakeWI()
    return backend_mod.KmbBackend(wi, KEYS, lambda: (960, 540)), wi


@pytest.fixture
def pad():
    import xusb
    p = FakePad()
    return backend_mod.PadBackend(p, xusb), p


class TestKmb:
    def test_move_uses_the_configured_key(self, kmb):
        b, wi = kmb
        b.hold_move()
        assert wi.calls == [("press", "w")]

    def test_battle_uses_the_client_centre(self, kmb):
        b, wi = kmb
        b.battle_press()
        assert wi.calls == [("mdown", 960, 540, "middle")]

    def test_name_shows_the_transport(self, kmb):
        b, wi = kmb
        wi.mode = "fallback"
        assert b.name == "kmb/fallback"

    def test_no_geometry_skips_pressing_the_middle_button(self):
        """按不下去只是这一次没打上，可以接受 —— #46 定的行为。"""
        wi = FakeWI()
        b = backend_mod.KmbBackend(wi, KEYS, lambda: None)
        b.battle_press()
        assert wi.calls == []

    def test_no_geometry_still_releases_the_middle_button(self):
        """松不开就完全是另一回事：中键会一直按着。宁可用 (0,0) 也要发出去。"""
        wi = FakeWI()
        b = backend_mod.KmbBackend(wi, KEYS, lambda: None)
        b._battle_held = True
        b.battle_release()
        assert wi.calls == [("mup", 0, 0, "middle")]


class TestReleaseAllIsTheSafetyValve:
    def test_held_move_is_released(self, kmb):
        b, wi = kmb
        b.hold_move()
        wi.calls.clear()
        b.release_all()
        assert ("release", "w") in wi.calls

    def test_held_battle_is_released(self, kmb):
        b, wi = kmb
        b.battle_press()
        wi.calls.clear()
        b.release_all()
        assert ("mup", 960, 540, "middle") in wi.calls

    def test_nothing_held_sends_nothing(self, kmb):
        b, wi = kmb
        b.release_all()
        assert wi.calls == [], "没按着就不该乱发松开"

    def test_it_can_be_called_twice(self, kmb):
        """出错路径上会被重复调用，第二次必须是空操作。"""
        b, wi = kmb
        b.hold_move()
        b.release_all()
        wi.calls.clear()
        b.release_all()
        assert wi.calls == []


class TestPad:
    def test_move_pushes_the_stick_forward(self, pad):
        import xusb
        b, p = pad
        b.hold_move()
        assert p.reports == [(0, xusb.STICK_MAX)]

    def test_button_and_stick_coexist_in_one_report(self, pad):
        """XUSB_REPORT 是全量快照。忘了合成的话，按新键会把摇杆松掉。"""
        import xusb
        b, p = pad
        b.hold_move()
        b.battle_press()
        buttons, stick = p.reports[-1]
        assert stick == xusb.STICK_MAX, "按按钮不该把摇杆松掉"
        assert buttons == xusb.XUSB_RIGHT_THUMB

    def test_release_all_zeroes_everything(self, pad):
        b, p = pad
        b.hold_move()
        b.battle_press()
        b.release_all()
        assert p.reports[-1] == (0, 0)

    def test_a_tap_goes_down_then_up(self, pad):
        import xusb
        b, p = pad
        b.confirm()
        assert p.reports[-2][0] == xusb.XUSB_A
        assert p.reports[-1][0] == 0

    def test_an_unknown_button_name_sends_nothing(self):
        """拼错名字不会报错，只会静静地什么都不按。至少不能发出错误的键。"""
        import xusb
        p = FakePad()
        b = backend_mod.PadBackend(p, xusb, mapping={"confirm": "nonsense"})
        b.confirm()
        assert p.reports == []

    def test_a_dead_pad_does_not_raise(self, ):
        """手柄掉线时调用方还在跑循环，不能让它炸出来。"""
        import xusb
        b = backend_mod.PadBackend(FakePad(explode=True), xusb)
        b.hold_move()          # 不抛就算过
        b.release_all()

    def test_mapping_is_overridable(self):
        import xusb
        p = FakePad()
        b = backend_mod.PadBackend(p, xusb, mapping={"confirm": "b"})
        b.confirm()
        assert p.reports[-2][0] == xusb.XUSB_B


class TestNullBackend:
    def test_it_is_never_ready(self):
        assert backend_mod.NullBackend().is_ready() is False

    def test_it_says_so_once(self, caplog):
        import logging
        b = backend_mod.NullBackend("测试原因")
        logger = logging.getLogger("gbfr")
        records = []

        class Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = Collect(level=logging.WARNING)
        logger.addHandler(h)
        old = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            for _ in range(10):
                b.confirm()
        finally:
            logger.removeHandler(h)
            logger.setLevel(old)
        assert len(records) == 1, "要说，但只说一次"


class TestBadMappingDoesNotFloodTheLog:
    """映射配错正是 G1 最可能碰到的情况 —— 那时候日志恰恰最需要能读。
    一次 tap 会走 _hold + _drop 两趟，不挡的话循环里每秒几十条同样的告警。
    """

    def _warnings(self, fn, times):
        import logging
        recs = []

        class Collect(logging.Handler):
            def emit(self, record):
                recs.append(record)

        logger = logging.getLogger("gbfr")
        h = Collect(level=logging.WARNING)
        old = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(h)
        try:
            for _ in range(times):
                fn()
        finally:
            logger.removeHandler(h)
            logger.setLevel(old)
        return recs

    def test_it_warns_once_per_action_not_once_per_call(self):
        import xusb
        b = backend_mod.PadBackend(FakePad(), xusb, mapping={"confirm": "typo"})
        assert len(self._warnings(b.confirm, 20)) == 1

    def test_each_broken_action_gets_its_own_warning(self):
        """两个都配错了，只报一个会让人以为修好一个就够了。"""
        import xusb
        b = backend_mod.PadBackend(
            FakePad(), xusb, mapping={"confirm": "typo", "again": "alsotypo"})
        recs = self._warnings(lambda: (b.confirm(), b.again()), 5)
        assert len(recs) == 2

    def test_a_good_mapping_warns_never(self):
        import xusb
        b = backend_mod.PadBackend(FakePad(), xusb)
        assert self._warnings(b.confirm, 20) == []

    def test_the_pad_is_reachable_without_touching_a_private_attribute(self):
        import xusb
        p = FakePad()
        assert backend_mod.PadBackend(p, xusb).pad is p
