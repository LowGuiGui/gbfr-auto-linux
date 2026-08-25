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


class TestDryRun:
    """空跑：照常识别、照常记录，但一个按键都不发出去。"""

    def test_no_input_is_sent(self, opt, log_file):
        o = opt()
        o._dry_run = True
        o.start_battle()
        o.end_battle()
        o.switch_again()
        o.tap_enter()
        assert o._wi.events == []

    def test_it_says_what_it_would_have_done(self, opt, log_file):
        o = opt()
        o._dry_run = True
        o.switch_again()
        text = log_file()
        assert "[空跑]" in text
        assert "switch_again" in text

    def test_battle_state_still_tracks(self, opt, log_file):
        """空跑不能把状态机也停掉 —— 页面判定还得照常跑。"""
        o = opt()
        o._dry_run = True
        o.start_battle()
        assert o._is_battle_ing is True
        o.end_battle()
        assert o._is_battle_ing is False

    def test_enabling_it_is_announced_loudly(self, opt, log_file):
        opt({"move": "w"})._dry_run = False
        from option import Option
        Option(root=None, dry_run=True)
        assert "空跑模式" in log_file()


# --- #46：中键要落在客户区中心，不是窗口中心 ---------------------------------

WINDOW = (629, 315, 3211, 1811)      # 真机实测，窗口化 2K
CLIENT_SIZE = (2560, 1440)
CLIENT_ORIGIN = (640, 360)


class RecordingInput(FakeInput):
    """连坐标一起记下来 —— 这一组测的就是坐标。"""

    def __init__(self):
        super().__init__()
        self.hwnd = 1234
        self.points = []

    def mouse_press(self, x, y, button="left"):
        self.points.append(("press", x, y, button))

    def mouse_release(self, x, y, button="left"):
        self.points.append(("release", x, y, button))


class TestClientCentre:
    @pytest.fixture
    def opt_with_window(self, monkeypatch):
        import option as option_module
        monkeypatch.setattr("option.WindowInput", RecordingInput)

        def build(measured=(WINDOW, CLIENT_SIZE, CLIENT_ORIGIN)):
            monkeypatch.setattr(option_module.geometry, "read", lambda hwnd: measured)
            return Option(root=None, keys=None)

        return build

    def test_centre_is_the_client_centre(self, opt_with_window):
        assert opt_with_window()._get_center() == (1291, 765)

    def test_not_the_old_window_centre(self, opt_with_window):
        """旧值是 (1291, 748)。差的那 17 像素就是这个 issue。"""
        assert opt_with_window()._get_center() != (1291, 748)

    def test_middle_click_lands_on_the_client_centre(self, opt_with_window):
        """走完整条路径：start_battle -> _get_center -> mouse_press。"""
        o = opt_with_window()
        o.start_battle()
        assert o._wi.points == [("press", 1291, 765, "middle")]

    def test_release_uses_the_same_point(self, opt_with_window):
        o = opt_with_window()
        o.start_battle()
        o.end_battle()
        assert [p[1:3] for p in o._wi.points] == [(1291, 765), (1291, 765)]

    def test_borderless_is_unaffected(self, opt_with_window):
        """无边框时客户区和窗口区重合，落点不该有任何变化。"""
        o = opt_with_window(((0, 0, 2560, 1440), (2560, 1440), (0, 0)))
        assert o._get_center() == (1280, 720)

    def test_unreadable_window_skips_the_mouse_instead_of_crashing(self, opt_with_window):
        o = opt_with_window(None)
        assert o._get_center() is None
        o.start_battle()
        assert o._wi.points == [], "拿不到几何就不该按中键"
        assert ("press", "w") in o._wi.events, "但按键照发，战斗还是要开始"

    def test_geometry_raising_is_swallowed(self, monkeypatch):
        import option as option_module
        monkeypatch.setattr("option.WindowInput", RecordingInput)

        def boom(hwnd):
            raise RuntimeError("window vanished")

        monkeypatch.setattr(option_module.geometry, "read", boom)
        assert Option(root=None, keys=None)._get_center() is None
