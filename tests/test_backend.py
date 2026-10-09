# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Linux 键鼠动作与重复释放；无需连接真实输入设备。"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import backend as backend_mod  # noqa: E402

KEYS = {"move": "w", "again": "3", "confirm": "a"}


class FakeWI:
    def __init__(self, mode="xtest", ready=True):
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


@pytest.fixture
def kmb():
    wi = FakeWI()
    return backend_mod.KmbBackend(wi, KEYS, lambda: (960, 540)), wi


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
        wi.mode = "dry-run"
        assert b.name == "kmb/dry-run"

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
