# -*- coding: utf-8 -*-
"""geometry.py —— 全部用 Howard 真机上量到的数字。

这些测试的价值在于它们钉死了一个**算错过的结论**：#46 原来说中键偏移是
(11, 45)，实际是 (0, -17)。数字写死在这里，下次再有人凭直觉改就会红。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geometry  # noqa: E402

# 2026-08-24 实测，游戏窗口化 2K：
WINDOW = (629, 315, 3211, 1811)      # GetWindowRect      -> 2582x1496
CLIENT_SIZE = (2560, 1440)           # GetClientRect
CLIENT_ORIGIN = (640, 360)           # ClientToScreen(0,0)


class TestBorderOffset:
    def test_matches_the_measured_offset(self):
        assert geometry.border_offset(WINDOW, CLIENT_ORIGIN) == (11, 45)

    def test_borderless_has_no_offset(self):
        """无边框时窗口区和客户区重合，换算必须退化成恒等。"""
        assert geometry.border_offset((0, 0, 2560, 1440), (0, 0)) == (0, 0)


class TestChrome:
    def test_horizontal_is_symmetric_vertical_is_not(self):
        """#46 的全部原因。左右各 11，上 45 下 11。"""
        edges = geometry.chrome(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)
        assert edges == {"left": 11, "top": 45, "right": 11, "bottom": 11}
        assert edges["left"] == edges["right"], "左右对称，所以中心的 x 是对的"
        assert edges["top"] != edges["bottom"], "上下不对称，所以中心的 y 是错的"

    def test_edges_add_up_to_the_window(self):
        edges = geometry.chrome(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)
        assert edges["left"] + CLIENT_SIZE[0] + edges["right"] == WINDOW[2] - WINDOW[0]
        assert edges["top"] + CLIENT_SIZE[1] + edges["bottom"] == WINDOW[3] - WINDOW[1]


class TestClientCentre:
    def test_lands_on_the_real_centre_in_screen_coordinates(self):
        """把窗口相对的中心换算回屏幕，必须正好是客户区正中。"""
        centre = geometry.client_centre(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)
        assert geometry.to_screen(WINDOW, centre) == (1920, 1080)

    def test_window_relative_value(self):
        assert geometry.client_centre(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN) == (1291, 765)

    def test_the_old_behaviour_was_off_by_zero_minus_seventeen(self):
        """#46 说是 (11, 45)。不是。x 分毫不差，y 高了 17。

        17 = (45 - 11) / 2，也就是上下边框差的一半。
        """
        old = geometry.window_centre(WINDOW)
        new = geometry.client_centre(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)
        assert (old[0] - new[0], old[1] - new[1]) == (0, -17)

    def test_borderless_is_unchanged_by_the_fix(self):
        """无边框下新旧算法必须给出同一个点，否则这个改动会弄坏全屏窗口模式。"""
        window = (0, 0, 2560, 1440)
        assert geometry.client_centre(window, (2560, 1440), (0, 0)) == \
            geometry.window_centre(window)

    def test_odd_sizes_do_not_drift(self):
        """整除截断不能把点甩出客户区。"""
        window, size, origin = (0, 0, 101, 103), (99, 97), (1, 5)
        cx, cy = geometry.client_centre(window, size, origin)
        assert 1 <= cx < 100 and 5 <= cy < 102


class TestRead:
    def test_returns_none_without_win32(self, monkeypatch):
        """Linux 上没有 win32gui（conftest 只桩了个空模块），不能抛。"""
        import builtins
        real_import = builtins.__import__

        def no_win32(name, *args, **kw):
            if name == "win32gui":
                raise ImportError(name)
            return real_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", no_win32)
        assert geometry.read(1234) is None

    def test_minimised_window_is_reported_as_unknown(self, monkeypatch):
        """最小化时客户区是 0x0，此时给出的任何中心点都是假的。"""
        import types
        fake = types.ModuleType("win32gui")
        fake.GetWindowRect = lambda h: (-32000, -32000, -31840, -31972)
        fake.GetClientRect = lambda h: (0, 0, 0, 0)
        fake.ClientToScreen = lambda h, p: (-32000, -32000)
        monkeypatch.setitem(sys.modules, "win32gui", fake)
        assert geometry.read(1234) is None

    def test_reads_a_live_window(self, monkeypatch):
        import types
        fake = types.ModuleType("win32gui")
        fake.GetWindowRect = lambda h: WINDOW
        fake.GetClientRect = lambda h: (0, 0, CLIENT_SIZE[0], CLIENT_SIZE[1])
        fake.ClientToScreen = lambda h, p: CLIENT_ORIGIN
        monkeypatch.setitem(sys.modules, "win32gui", fake)
        assert geometry.read(1234) == (WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)

    def test_win32_raising_is_not_fatal(self, monkeypatch):
        """窗口可能刚好在两次调用之间被关掉。"""
        import types
        fake = types.ModuleType("win32gui")

        def boom(*a, **kw):
            raise Exception("invalid window handle")

        fake.GetWindowRect = boom
        fake.GetClientRect = boom
        fake.ClientToScreen = boom
        monkeypatch.setitem(sys.modules, "win32gui", fake)
        assert geometry.read(1234) is None
