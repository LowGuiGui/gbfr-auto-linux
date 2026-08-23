# -*- coding: utf-8 -*-
"""按键来自配置，不再是散落在方法里的字面量。"""

import pytest

from option import Option


class FakeInput:
    def __init__(self):
        self.events = []

    def key_press(self, key):
        self.events.append(("press", key))

    def key_release(self, key):
        self.events.append(("release", key))

    def key_tap(self, key):
        self.events.append(("tap", key))

    def mouse_press(self, x, y, button="left"):
        self.events.append(("mouse_press", button))

    def mouse_release(self, x, y, button="left"):
        self.events.append(("mouse_release", button))


@pytest.fixture
def opt(monkeypatch):
    def build(keys=None):
        monkeypatch.setattr("option.WindowInput", FakeInput)
        o = Option(root=None, keys=keys)
        # 取窗口矩形要 win32，这里让它返回 None：中键会被跳过，按键路径不受影响
        monkeypatch.setattr(o, "_get_center", lambda: None)
        return o
    return build


def test_defaults_match_the_original_literals(opt):
    o = opt()
    o.start_battle()
    o.end_battle()
    o.switch_again()
    o.tap_enter()
    assert o._wi.events == [
        ("press", "w"), ("release", "w"), ("tap", "3"), ("tap", "a"),
    ]


def test_keys_come_from_config(opt):
    o = opt({"move": "up", "again": "5", "confirm": "space"})
    o.start_battle()
    o.end_battle()
    o.switch_again()
    o.tap_enter()
    assert o._wi.events == [
        ("press", "up"), ("release", "up"), ("tap", "5"), ("tap", "space"),
    ]


def test_partial_config_keeps_the_rest_at_defaults(opt):
    o = opt({"move": "z"})
    o.start_battle()
    o.switch_again()
    assert o._wi.events == [("press", "z"), ("tap", "3")]


def test_start_battle_is_idempotent(opt):
    o = opt()
    o.start_battle()
    o.start_battle()
    assert o._wi.events == [("press", "w")]


def test_end_battle_without_start_does_nothing(opt):
    o = opt()
    o.end_battle()
    assert o._wi.events == []


def test_tap_enter_does_not_tap_enter(opt):
    """#15 —— 名字是继承来的，按的其实是 keys.confirm。这里把现状钉住。"""
    o = opt()
    o.tap_enter()
    assert o._wi.events == [("tap", "a")]
