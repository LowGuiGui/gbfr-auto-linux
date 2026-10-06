# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""xtest_input.py：经 XTest 给嵌套 X 里的游戏发键和鼠标。

嵌套 X 用替身，方法签名照 python-xlib 0.33 的源码：xtest_fake_input(event_type,
detail=0, time=X.CurrentTime, root=X.NONE, x=0, y=0)、get_modifier_mapping() 返回八组键码、
keycode_to_keysym(keycode, index)、窗口的 translate_coords(src_window, src_x, src_y) 返回带
child、x、y 的应答（TestTheDoubleFollowsPythonXlib 对着库本身核对这些字段名）。这里守的是
那几道关：连错显示、焦点不在游戏上、指针下面不是游戏、键不在配置里或者要修饰键、空跑时发了
东西、按着的没松开。
"""

from types import SimpleNamespace

import pytest
from Xlib import X, XK

import backend
from test_gamescope import FakeWindow
from xtest_input import BUTTONS, InputRefused, XTestInput

KEYS = {"move": "w", "again": "3", "confirm": "a"}
# 美式键盘上常见的映射：键码（evdev + 8）-> 各档的符号，第一档不按修饰键，第二档按 Shift
KEYMAP = {25: ["w", "W"], 12: ["3", "numbersign"], 38: ["a", "A"], 21: ["equal", "plus"],
          9: ["Escape"], 133: ["Super_L"], 50: ["Shift_L"], 37: ["Control_L"]}
# shift, lock, control, mod1 .. mod5，每组两个位置，0 是空位
MODIFIERS = [[50, 62], [66, 0], [37, 105], [64, 108], [77, 0], [0, 0], [133, 134], [92, 0]]
GAMESCOPE_PROPS = {"GAMESCOPE_FOCUSED_WINDOW": [0x400000], "GAMESCOPE_INPUT_COUNTER": [3174]}


class GeoWindow(FakeWindow):
    """FakeWindow 加上几何。rect 是根窗口坐标里的 (x, y, 宽, 高)；kids 按叠放次序，后加的在上。

    translate_coords 照 X 协议的 TranslateCoords：把 src_window 里的点换成本窗口的坐标，child
    是本窗口里包含这个点、映射着、而且在最上面的子窗口，没有就是 X.NONE。
    """

    def __init__(self, wid, parent=None, rect=(0, 0, 2560, 1440), mapped=True):
        super().__init__(wid, parent)
        self.rect = rect
        self.mapped = mapped
        self.kids = []
        if parent is not None:
            parent.kids.append(self)

    def contains(self, rx, ry):
        x, y, w, h = self.rect
        return self.mapped and x <= rx < x + w and y <= ry < y + h

    def translate_coords(self, src_window, src_x, src_y):
        rx, ry = src_window.rect[0] + src_x, src_window.rect[1] + src_y
        child = next((k for k in reversed(self.kids) if k.contains(rx, ry)), X.NONE)
        return SimpleNamespace(same_screen=1, child=child, x=rx - self.rect[0], y=ry - self.rect[1])


class PropertyRoot(GeoWindow):
    """根窗口：除了几何和 query_tree，还有属性。"""

    def __init__(self, wid, props):
        super().__init__(wid)
        self.atoms = {i + 1: name for i, name in enumerate(props)}
        self.props = props

    def list_properties(self):
        return list(self.atoms)

    def get_full_property(self, atom, property_type):
        return SimpleNamespace(value=self.props[self.atoms[atom]])


class NestedX:
    """python-xlib Display 的替身：XTestInput 用到的方法，签名照源码。"""

    def __init__(self, root, focus, xtest=True, keymap=KEYMAP, modifiers=MODIFIERS,
                 pointer_map=(1, 2, 3, 4, 5, 6, 7)):
        self.root = root
        self.focus = focus
        self.xtest = xtest
        self.keymap = keymap
        self.modifiers = modifiers
        self.pointer_map = pointer_map
        self.events = []
        self.fail = False

    def screen(self):
        return SimpleNamespace(root=self.root)

    def get_atom_name(self, atom):
        return self.root.atoms[atom]

    def has_extension(self, extension):
        return extension == "XTEST" and self.xtest

    def get_modifier_mapping(self):
        return self.modifiers

    def get_pointer_mapping(self):
        """第 N 项是物理按钮 N+1 对应的逻辑按钮，照 python-xlib 的文档。"""
        return list(self.pointer_map)

    def keysym_to_keycode(self, keysym):
        """照 python-xlib：有好几个键都带这个符号时，取档位最低、其次键码最小的那个。"""
        found = [(index, code) for code, names in self.keymap.items()
                 for index, name in enumerate(names) if XK.string_to_keysym(name) == keysym]
        return min(found)[1] if found else 0

    def keycode_to_keysym(self, keycode, index):
        names = self.keymap.get(keycode, [])
        return XK.string_to_keysym(names[index]) if index < len(names) else X.NoSymbol

    def get_input_focus(self):
        return SimpleNamespace(focus=self.focus, revert_to=X.RevertToParent)

    def xtest_fake_input(self, event_type, detail=0, time=X.CurrentTime, root=X.NONE, x=0, y=0):
        if self.fail:
            raise ConnectionResetError("connection to the X server lost")
        self.events.append((event_type, detail) if event_type != X.MotionNotify
                           else (event_type, detail, x, y))

    def sync(self):
        pass


def nested(props=GAMESCOPE_PROPS, offset=(0, 0), **display):
    """根窗口里一个铺满的游戏窗口（左上角在 offset），它里面一个子窗口，游戏上面右下角一个
    小覆盖层。点击用的 (5, 5) 和 (1280, 720) 都不在覆盖层下面。"""
    root = PropertyRoot(0x35B, props)
    game = GeoWindow(0x400000, root, rect=(offset[0], offset[1], 2560, 1440))
    child = GeoWindow(0x400010, game, rect=(offset[0] + 100, offset[1] + 100, 200, 200))
    overlay = GeoWindow(0x500000, root, rect=(2000, 1200, 200, 100))
    d = NestedX(root, game, **display)
    return d, SimpleNamespace(root=root, game=game, overlay=overlay, child=child)


def live_input(d, windows, keys=KEYS, **kwargs):
    return XTestInput(d, windows.game, keys, live=True, sleep=lambda s: None, **kwargs)


class TestOnlyTheNestedDisplay:
    @pytest.mark.parametrize("props", [{}, {"STEAM_GAME": [881020]}, {"_NET_WM_NAME": b"x"}])
    def test_a_display_without_gamescope_properties_is_refused(self, props):
        """宿主的 Xwayland 没有 GAMESCOPE_* 属性。STEAM_* 也不够：那不是 gamescope 设的。"""
        d, w = nested(props=props)
        with pytest.raises(InputRefused, match="gamescope"):
            live_input(d, w)
        assert d.events == []

    def test_without_xtest_nothing_can_be_sent(self):
        d, w = nested(xtest=False)
        with pytest.raises(InputRefused, match="XTEST"):
            live_input(d, w)

    def test_the_real_nested_display_is_accepted(self):
        d, w = nested()
        assert live_input(d, w).is_ready()


class TestOnlyConfiguredKeys:
    @pytest.mark.parametrize("name", ["Super_L", "Shift_L", "Control_L"])
    def test_a_modifier_is_refused_when_the_keys_are_read(self, name):
        """修饰键按嵌套 X 自己的映射认，不靠一张名字表。"""
        d, w = nested()
        with pytest.raises(InputRefused, match="修饰键"):
            live_input(d, w, keys=dict(KEYS, move=name))

    @pytest.mark.parametrize("name", ["A", "W", "plus", "numbersign"])
    def test_a_key_that_needs_shift_is_refused(self, name):
        """XTest 只发键码：配置写 "A"，不带 Shift 发出去的是 a，等于悄悄换了一个动作。"""
        d, w = nested()
        with pytest.raises(InputRefused, match="修饰键才打得出来"):
            live_input(d, w, keys=dict(KEYS, again=name))

    @pytest.mark.parametrize("name", ["no_such_key", "F13"])
    def test_a_key_the_keymap_lacks_is_refused(self, name):
        d, w = nested()
        with pytest.raises(InputRefused, match="键盘映射"):
            live_input(d, w, keys=dict(KEYS, confirm=name))

    @pytest.mark.parametrize("call", [
        lambda i: i.key_press("Escape"), lambda i: i.key_tap("s"), lambda i: i.key_release("q"),
    ])
    def test_anything_outside_the_config_is_refused_before_touching_the_display(self, call):
        d, w = nested()
        xi = live_input(d, w)
        with pytest.raises(ValueError, match="refusing"):
            call(xi)
        assert d.events == []

    def test_the_middle_button_is_found_through_the_pointer_mapping(self):
        """物理 3 号映射成逻辑 2 号（中键）时，XTest 得发 3 号，发 2 号出来的是别的键。"""
        d, w = nested(pointer_map=(1, 3, 2))
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        xi.mouse_release(5, 5, "middle")
        assert d.events[1:] == [(X.ButtonPress, 3), (X.ButtonRelease, 3)]
        d.events.clear()
        xi.release_everything()
        assert (X.ButtonRelease, 3) in d.events

    def test_a_mapping_without_a_middle_button_is_refused(self):
        d, w = nested(pointer_map=(1, 0, 3))
        with pytest.raises(InputRefused, match="指针映射"):
            live_input(d, w)

    @pytest.mark.parametrize("button", ["left", "right", 1, 2])
    def test_only_the_middle_button(self, button):
        d, w = nested()
        with pytest.raises(ValueError, match="refusing"):
            live_input(d, w).mouse_press(10, 10, button)
        assert d.events == []


class TestSending:
    def test_a_held_key_is_pressed_once_and_released_once(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        xi.key_press("w")
        assert xi.held == ["w"]
        xi.key_release("w")
        xi.key_release("w")
        assert d.events == [(X.KeyPress, 25), (X.KeyRelease, 25)]
        assert xi.held == []

    def test_a_tap_presses_waits_and_releases(self):
        d, w = nested()
        waits = []
        xi = XTestInput(d, w.game, KEYS, live=True, sleep=waits.append, hold=0.05)
        xi.key_tap("3")
        assert d.events == [(X.KeyPress, 12), (X.KeyRelease, 12)]
        assert waits == [0.05]

    def test_tapping_a_held_key_does_not_let_go_of_it(self):
        d, w = nested()
        xi = live_input(d, w, keys={"move": "w", "again": "w", "confirm": "a"})
        xi.key_press("w")
        xi.key_tap("w")
        assert d.events == [(X.KeyPress, 25)] and xi.held == ["w"]

    def test_the_middle_button_goes_to_the_window_s_point_on_the_root(self):
        """坐标相对游戏窗口；XTest 的指针移动要根窗口坐标，由 TranslateCoords 换算。"""
        d, w = nested(offset=(100, 50))
        xi = live_input(d, w)
        xi.mouse_press(1280, 720, "middle")
        xi.mouse_release(1280, 720, "middle")
        assert d.events == [(X.MotionNotify, 0, 1380, 770), (X.ButtonPress, BUTTONS["middle"]),
                            (X.ButtonRelease, BUTTONS["middle"])]

    def test_release_all_lets_go_of_everything_and_can_be_repeated(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        xi.mouse_press(5, 5, "middle")
        d.events.clear()
        xi.release_all()
        xi.release_all()
        assert sorted(d.events) == sorted([(X.KeyRelease, 25), (X.ButtonRelease, 2)])
        assert xi.held == []

    def test_release_everything_does_not_depend_on_what_was_recorded(self):
        """脚本被强杀以后按着什么没人记得，这时每个配置过的键和中键都松一遍。"""
        d, w = nested()
        live_input(d, w).release_everything()
        assert sorted(d.events) == sorted([(X.KeyRelease, 25), (X.KeyRelease, 12),
                                           (X.KeyRelease, 38), (X.ButtonRelease, 2)])

    def test_a_lost_connection_stops_further_presses(self):
        d, w = nested()
        xi = live_input(d, w)
        d.fail = True
        xi.key_press("w")
        assert not xi.is_ready() and xi.held == []
        d.fail = False
        xi.key_tap("a")
        assert d.events == []


class TestFocus:
    @pytest.mark.parametrize("focus", ["overlay", "root", X.NONE, X.PointerRoot])
    def test_nothing_is_pressed_while_the_focus_is_elsewhere(self, focus):
        d, w = nested()
        d.focus = getattr(w, focus) if isinstance(focus, str) else focus
        xi = live_input(d, w)
        xi.key_press("w")
        xi.key_tap("a")
        xi.mouse_press(5, 5, "middle")
        assert d.events == [] and xi.skipped == 3

    def test_a_child_of_the_game_window_counts(self):
        d, w = nested()
        d.focus = w.child
        xi = live_input(d, w)
        xi.key_tap("a")
        assert d.events == [(X.KeyPress, 38), (X.KeyRelease, 38)]

    def test_a_press_that_gets_through_resets_the_count(self):
        d, w = nested()
        d.focus = w.overlay
        xi = live_input(d, w)
        xi.key_tap("a")
        xi.key_tap("a")
        assert xi.skipped == 2
        d.focus = w.game
        xi.key_tap("a")
        assert xi.skipped == 0

    def test_no_button_press_where_another_window_covers_the_point(self):
        """焦点在游戏上，可是点下去的地方盖着别的窗口：按钮事件会落到那个窗口上。"""
        d, w = nested()
        xi = live_input(d, w)
        xi.mouse_press(2050, 1250, "middle")
        assert d.events == [] and xi.skipped == 1 and xi.held == []

    def test_an_unmapped_window_over_the_point_does_not_count(self):
        d, w = nested()
        w.overlay.mapped = False
        xi = live_input(d, w)
        xi.mouse_press(2050, 1250, "middle")
        assert d.events[-1] == (X.ButtonPress, 2)

    def test_a_child_of_the_game_under_the_point_counts(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.mouse_press(150, 150, "middle")
        assert d.events == [(X.MotionNotify, 0, 150, 150), (X.ButtonPress, 2)]

    def test_the_dry_run_checks_the_point_too(self):
        """空跑不移指针，所以要在按下的那一点上查，而不是查指针现在在哪。"""
        d, w = nested()
        xi = XTestInput(d, w.game, KEYS)
        xi.mouse_press(2050, 1250, "middle")
        assert xi.held == [] and xi.skipped == 1

    def test_a_held_key_is_released_wherever_the_focus_went(self):
        """松开不看焦点：W 按在游戏上，焦点跑到了对话框上，也得松开。"""
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        xi.mouse_press(5, 5, "middle")
        d.focus = w.overlay
        xi.key_release("w")
        xi.mouse_release(5, 5, "middle")
        assert d.events[-2:] == [(X.KeyRelease, 25), (X.ButtonRelease, 2)]
        assert xi.held == []


class TestDryRun:
    def test_nothing_is_sent_but_everything_is_logged(self, log_file):
        d, w = nested()
        xi = XTestInput(d, w.game, KEYS, sleep=lambda s: None)
        xi.key_press("w")
        assert xi.held == ["w"]
        xi.key_tap("3")
        xi.mouse_press(5, 5, "middle")
        xi.release_all()
        xi.release_everything()
        assert d.events == []
        assert xi.held == []
        text = log_file()
        assert "空跑" in text and "[空跑] 本应发送: 按下 w" in text
        assert "[空跑] 本应发送: 松开 w" in text and "按下鼠标 middle" in text

    def test_the_dry_run_still_checks_the_focus(self):
        d, w = nested()
        d.focus = w.overlay
        xi = XTestInput(d, w.game, KEYS)
        xi.key_press("w")
        assert xi.held == [] and xi.skipped == 1


class TestWithTheBackend:
    def test_kmb_backend_drives_it_through_battle_and_release(self):
        """KmbBackend 调的方法名和参数，这里要接得住：按住前进加中键开打，全部松开。"""
        d, w = nested()
        xi = live_input(d, w)
        kmb = backend.KmbBackend(xi, KEYS, lambda: (1280, 720))
        assert kmb.is_ready() and kmb.name == "kmb/xtest"
        kmb.hold_move()
        kmb.battle_press()
        kmb.again()
        kmb.release_all()
        assert d.events == [(X.KeyPress, 25), (X.MotionNotify, 0, 1280, 720), (X.ButtonPress, 2),
                            (X.KeyPress, 12), (X.KeyRelease, 12),
                            (X.KeyRelease, 25), (X.ButtonRelease, 2)]
        assert xi.held == []


class TestTheDoubleFollowsPythonXlib:
    """替身里的字段名和方法要和 python-xlib 本身对得上，否则测试过了，真跑起来却报错。"""

    def test_translate_coords_replies_with_child_x_and_y(self):
        """TranslateCoords 应答的字段是 child、x、y；dst_x、dst_y 是 WarpPointer 请求的字段。"""
        from Xlib.protocol import request
        names = [f.name for f in request.TranslateCoords._reply.fields if f.name]
        assert {"child", "x", "y"} <= set(names) and "dst_x" not in names

    def test_the_display_methods_exist_with_these_signatures(self):
        import inspect

        from Xlib import display as xdisplay
        from Xlib.ext import xtest
        params = list(inspect.signature(xtest.fake_input).parameters)
        assert params == ["self", "event_type", "detail", "time", "root", "x", "y"]
        for method in ("keysym_to_keycode", "keycode_to_keysym", "get_modifier_mapping",
                       "get_pointer_mapping", "get_input_focus", "has_extension", "sync",
                       "screen", "get_atom_name"):
            assert hasattr(xdisplay.Display, method), method

