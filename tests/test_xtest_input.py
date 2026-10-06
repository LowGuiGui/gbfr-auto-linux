# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""xtest_input.py：经 XTest 给嵌套 X 里的游戏发键和鼠标。

嵌套 X 用替身，方法签名照 python-xlib 0.33 的源码：xtest_fake_input(event_type,
detail=0, time=X.CurrentTime, root=X.NONE, x=0, y=0)、get_modifier_mapping() 返回八组键码、
keycode_to_keysym(keycode, index)、窗口的 translate_coords(src_window, src_x, src_y) 返回带
child、x、y 的应答、连接建立时的应答里有 min_keycode 和 max_keycode
（TestTheDoubleFollowsPythonXlib 对着库本身核对这些字段名）。这里守的是那几道关：连错显示、
焦点不在游戏上、指针下面不是游戏、键不在配置里或者要修饰键、切换了键盘布局、空跑时发了东西、
按着的没松开。
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
          9: ["Escape"], 133: ["Super_L"], 50: ["Shift_L"], 37: ["Control_L"], 77: ["Num_Lock"],
          66: ["Caps_Lock"], 92: ["ISO_Level3_Shift"], 87: ["KP_End", "KP_1"],
          86: ["KP_Add", "KP_Add"]}
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
    """根窗口：除了几何和 query_tree，还有属性，以及 query_pointer。state 是应答里的 mask：
    修饰键和鼠标键此刻按着哪些。"""

    def __init__(self, wid, props):
        super().__init__(wid)
        self.atoms = {i + 1: name for i, name in enumerate(props)}
        self.props = props
        self.state = 0

    def query_pointer(self):
        return SimpleNamespace(same_screen=1, root=self, child=X.NONE, root_x=0, root_y=0,
                               win_x=0, win_y=0, mask=self.state)

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
        self.keymap = dict(keymap)
        # python-xlib 连接时缓存的那一张：keysym_to_keycode、keycode_to_keysym 查它，不收
        # MappingNotify 就不更新。测试里改 keymap 是改服务器上的映射，缓存不跟着变
        self.cached_keymap = dict(keymap)
        self.modifiers = [list(codes) for codes in modifiers]
        self.pointer_map = pointer_map
        # 连接建立时的应答（python-xlib 的 Display.display.info）：服务器的键码范围
        self.display = SimpleNamespace(info=SimpleNamespace(min_keycode=8, max_keycode=255))
        self.events = []
        self.fail = False
        self.keys_down = set()      # 像服务器那样记着哪些键按着：fake 的按下、松开会改它
        self.on_motion = None       # 指针移过去以后要发生的事（比如叫出一个弹窗）

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
        """照 python-xlib：查缓存；有好几个键都带这个符号时，取档位最低、其次键码最小的那个。"""
        found = [(index, code) for code, names in self.cached_keymap.items()
                 for index, name in enumerate(names) if XK.string_to_keysym(name) == keysym]
        return min(found)[1] if found else 0

    def keycode_to_keysym(self, keycode, index):
        """照 python-xlib：查缓存。"""
        names = self.cached_keymap.get(keycode, [])
        return XK.string_to_keysym(names[index]) if index < len(names) else X.NoSymbol

    def get_keyboard_mapping(self, first_keycode, count):
        """照 python-xlib：问服务器，从 first_keycode 起 count 个键，每个键一组各档的符号。
        超出服务器的键码范围，服务器回 BadValue。"""
        info = self.display.info
        if first_keycode < info.min_keycode or first_keycode + count - 1 > info.max_keycode:
            raise ValueError("BadValue: keycode range outside the server's")
        return [tuple(XK.string_to_keysym(name) for name in self.keymap.get(code, []))
                for code in range(first_keycode, first_keycode + count)]

    def query_keymap(self):
        """照 python-xlib：32 个整数，第 N 个是键码 8N 到 8N+7，低位在前。"""
        bits = [0] * 32
        for code in self.keys_down:
            bits[code // 8] |= 1 << (code % 8)
        return bits

    def get_input_focus(self):
        return SimpleNamespace(focus=self.focus, revert_to=X.RevertToParent)

    def xtest_fake_input(self, event_type, detail=0, time=X.CurrentTime, root=X.NONE, x=0, y=0):
        if self.fail:
            raise ConnectionResetError("connection to the X server lost")
        self.events.append((event_type, detail) if event_type != X.MotionNotify
                           else (event_type, detail, x, y))
        if event_type == X.KeyPress:
            self.keys_down.add(detail)
        elif event_type == X.KeyRelease:
            self.keys_down.discard(detail)
        elif event_type in (X.ButtonPress, X.ButtonRelease):
            # 物理按钮经指针映射成逻辑按钮，状态里记的是逻辑的那一位
            bit = X.Button1Mask << (self.pointer_map[detail - 1] - 1)
            self.root.state = (self.root.state | bit if event_type == X.ButtonPress
                               else self.root.state & ~bit)
        elif event_type == X.MotionNotify and self.on_motion:
            self.on_motion()

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

    def test_a_keypad_key_num_lock_would_change_is_refused(self):
        """照 X 协议，Num Lock 亮着时小键盘上的键用第二档：配置写 KP_End，打出来的是 KP_1。"""
        d, w = nested()
        with pytest.raises(InputRefused, match="小键盘"):
            live_input(d, w, keys=dict(KEYS, confirm="KP_End"))

    def test_a_keypad_key_num_lock_leaves_alone_is_accepted(self):
        d, w = nested()
        assert live_input(d, w, keys=dict(KEYS, confirm="KP_Add")).is_ready()

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

    @pytest.mark.parametrize("live", [True, False])
    def test_tapping_a_held_key_does_not_let_go_of_it(self, live):
        """空跑时服务器上什么都没按下去，"别处按着"那一关拦不住；得靠"自己正按着"这一条。"""
        d, w = nested()
        xi = XTestInput(d, w.game, {"move": "w", "again": "w", "confirm": "a"}, live=live,
                        sleep=lambda s: None)
        xi.key_press("w")
        xi.key_tap("w")
        assert d.events == ([(X.KeyPress, 25)] if live else []) and xi.held == ["w"]

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
        assert not xi.is_ready()
        d.fail = False
        xi.key_tap("a")
        assert d.events == []

    def test_a_press_whose_send_failed_is_still_let_go(self):
        """请求可能已经到了服务器，只是 sync 没回来：当它按着，之后照样松开。"""
        d, w = nested()
        xi = live_input(d, w)
        d.fail = True
        xi.key_press("w")
        xi.mouse_press(5, 5, "middle")
        assert xi.held == ["w"]
        d.fail = False
        xi.release_all()
        assert (X.KeyRelease, 25) in d.events and xi.held == []


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


class TestPointerTarget:
    def test_a_window_that_pops_up_under_the_pointer_gets_no_click(self):
        """指针移过去刚好叫出一个不抢焦点的弹窗：按钮事件会落到它上面，所以移完再看一次。"""
        d, w = nested()

        def popup():
            w.overlay.rect = (0, 0, 50, 50)
        d.on_motion = popup
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        assert d.events == [(X.MotionNotify, 0, 5, 5)]
        assert xi.held == [] and xi.skipped == 1


class TestServerState:
    def test_a_failed_state_read_marks_the_input_unready(self):
        d, w = nested()

        def gone():
            raise ConnectionResetError("connection to the X server lost")
        w.root.query_pointer = gone
        xi = live_input(d, w)
        xi.key_press("w")
        assert not xi.is_ready() and d.events == []

    def test_a_key_someone_else_holds_is_neither_pressed_nor_released(self):
        """人正按着 W：这边按下是空操作，之后松开却会把人的那一下也松掉。"""
        d, w = nested()
        d.keys_down.add(25)
        xi = live_input(d, w)
        xi.key_press("w")
        xi.key_tap("w")
        xi.release_all()
        assert d.events == [] and xi.skipped == 2

    def test_a_middle_button_someone_else_holds_is_not_pressed(self):
        d, w = nested()
        w.root.state = X.Button2Mask
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        assert d.events == [] and xi.skipped == 1

    @pytest.mark.parametrize("change", ["other symbol", "now a modifier"])
    def test_a_key_whose_mapping_changed_is_not_sent(self, change):
        d, w = nested()
        xi = live_input(d, w)
        if change == "other symbol":
            d.keymap[25] = ["z", "Z"]
        else:
            d.modifiers[2] = [37, 25]
        xi.key_press("w")
        assert d.events == [] and not xi.is_ready()

    @pytest.mark.parametrize("held", [False, True])
    def test_a_keypad_key_remapped_so_num_lock_changes_it_is_not_sent(self, held):
        """构造时 KP_Add 两档都是 KP_Add，Num Lock 改不了它；运行中第二档换成了 KP_1，Num Lock
        一亮打出来的就是 KP_1。按下之前和按着的每一轮都得重新查这一条。"""
        d, w = nested()
        xi = live_input(d, w, keys=dict(KEYS, move="KP_Add"))
        if held:
            xi.key_press("KP_Add")
            d.events.clear()
        d.keymap[86] = ["KP_Add", "KP_1"]
        if held:
            assert not xi.check_holds()
            assert d.events == [(X.KeyRelease, 86)] and xi.held == []
        else:
            xi.key_tap("KP_Add")
            assert d.events == []
        assert not xi.is_ready()

    def test_the_middle_button_follows_a_new_pointer_mapping(self):
        d, w = nested()
        xi = live_input(d, w)
        d.pointer_map = (1, 3, 2)
        xi.mouse_press(5, 5, "middle")
        xi.mouse_release(5, 5, "middle")
        assert d.events[1:] == [(X.ButtonPress, 3), (X.ButtonRelease, 3)]

    def test_a_pointer_mapping_that_lost_the_middle_button_stops_presses(self):
        d, w = nested()
        xi = live_input(d, w)
        d.pointer_map = (1, 0, 3)
        xi.mouse_press(5, 5, "middle")
        assert d.events == [] and not xi.is_ready()


class TestConnectionTrouble:
    """连接断了不能被当成一次普通的跳过：调用方得看到"不再就绪"，循环才会停。"""

    def test_a_dead_connection_during_the_focus_walk_marks_it_unready(self):
        d, w = nested()
        d.focus = w.child

        def gone():
            raise ConnectionResetError("connection to the X server lost")
        w.child.query_tree = gone
        xi = live_input(d, w)
        xi.key_press("w")
        assert not xi.is_ready() and xi.skipped == 0

    def test_a_focus_window_that_vanished_is_only_a_skip(self):
        from Xlib import error as xerror
        d, w = nested()
        d.focus = w.overlay

        def vanished():
            # python-xlib 的错误对象要靠 display 解析应答；这里不走构造，只要它的类型
            raise xerror.BadWindow.__new__(xerror.BadWindow)
        w.overlay.query_tree = vanished
        xi = live_input(d, w)
        xi.key_press("w")
        assert xi.is_ready() and xi.skipped == 1

    def test_a_dead_connection_while_finding_the_window_under_the_point_marks_it_unready(self):
        d, w = nested()

        def gone(src_window, src_x, src_y):
            raise ConnectionResetError("connection to the X server lost")
        w.game.translate_coords = gone
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        assert not xi.is_ready() and d.events == []


    def test_a_dead_connection_while_translating_the_point_marks_it_unready(self):
        d, w = nested()

        def gone(src_window, src_x, src_y):
            raise ConnectionResetError("connection to the X server lost")
        w.root.translate_coords = gone
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        assert not xi.is_ready() and d.events == []


class TestHolding:
    """按住的键会自动连发，连发不再过按下时的那几道关：每一轮由 check_holds 再看一眼。"""

    def held_w_and_middle(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        xi.mouse_press(5, 5, "middle")
        d.events.clear()
        return d, w, xi

    def test_nothing_held_needs_no_look(self):
        d, w = nested()
        assert live_input(d, w).check_holds()

    def test_a_hold_whose_guards_still_stand_stays(self):
        d, w, xi = self.held_w_and_middle()
        assert xi.check_holds()
        assert d.events == [] and xi.held == ["w", "middle"]

    @pytest.mark.parametrize("change", ["focus moved", "modifier pressed",
                                        "key let go elsewhere", "button let go elsewhere"])
    def test_a_hold_whose_guards_fell_is_let_go(self, change):
        d, w, xi = self.held_w_and_middle()
        if change == "focus moved":
            d.focus = w.overlay
        elif change == "modifier pressed":
            w.root.state |= X.ControlMask
        elif change == "key let go elsewhere":
            d.keys_down.discard(25)
        else:
            w.root.state &= ~X.Button2Mask
        assert not xi.check_holds()
        assert xi.held == []
        assert (X.ButtonRelease, 2) in d.events or change == "button let go elsewhere"

    @pytest.mark.parametrize("change", ["symbol", "modifier", "pointer"])
    def test_a_mapping_that_changed_under_a_hold_lets_go(self, change):
        """连发出去的是键码：映射变了，按着的就成了另一个键。"""
        d, w, xi = self.held_w_and_middle()
        if change == "symbol":
            d.keymap[25] = ["z", "Z"]
        elif change == "modifier":
            d.modifiers[2] = [37, 25]
        else:
            d.pointer_map = (1, 3, 2)
        assert not xi.check_holds()
        assert xi.held == [] and not xi.is_ready()

    def test_the_dry_run_does_not_expect_keys_to_be_down(self):
        """空跑什么都没按下去，服务器上自然没有按着的键：那不算"被别处松开了"。"""
        d, w = nested()
        xi = XTestInput(d, w.game, KEYS)
        xi.key_press("w")
        assert xi.check_holds() and xi.held == ["w"]

    def test_a_tap_whose_release_failed_is_let_go_by_the_next_check(self):
        """松开没发出去，那个键还记着按着、服务器上也还按着：下一轮的检查不能把它当成该按着
        的（它会一直连发），而是再松一次。"""
        d, w = nested()

        def connection_drops(seconds):
            d.fail = True
        xi = XTestInput(d, w.game, KEYS, live=True, sleep=connection_drops)
        xi.key_tap("3")
        assert xi.held == ["3"] and not xi.is_ready()
        d.fail = False
        assert not xi.check_holds()
        assert d.events == [(X.KeyPress, 12), (X.KeyRelease, 12)] and xi.held == []

    def test_release_everything_skips_a_middle_button_the_mapping_no_longer_has(self):
        """按构造时的号去松，松开的会是现在映射到那个号上的另一个按钮。"""
        d, w = nested()
        xi = live_input(d, w)
        d.pointer_map = (1, 0, 3)
        xi.release_everything()
        assert not [e for e in d.events if e[0] == X.ButtonRelease]

    @pytest.mark.parametrize("change", ["other symbol", "now a modifier", "now Ctrl"])
    def test_release_everything_leaves_alone_a_key_whose_keycode_changed(self, change):
        """没记着的键按构造时的键码松：映射变了，松开的就是另一个键，可能是别人正按着的 Ctrl。"""
        d, w = nested()
        xi = live_input(d, w)
        if change in ("other symbol", "now Ctrl"):
            d.keymap[25] = ["z", "Z"] if change == "other symbol" else ["Control_L"]
        if change in ("now a modifier", "now Ctrl"):
            d.modifiers[2] = [37, 105, 25]
        xi.release_everything()
        assert (X.KeyRelease, 25) not in d.events
        assert {(X.KeyRelease, 12), (X.KeyRelease, 38), (X.ButtonRelease, 2)} <= set(d.events)

    def test_release_everything_still_lets_go_of_a_key_it_holds_after_a_mapping_change(self):
        """自己按下的，不管映射变成什么，都得松开。"""
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        d.keymap[25] = ["Control_L"]
        d.modifiers[2] = [37, 105, 25]
        xi.release_everything()
        assert (X.KeyRelease, 25) in d.events and xi.held == []

    def test_release_everything_without_a_keymap_lets_go_only_of_what_it_holds(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")

        def gone(first_keycode, count):
            raise ConnectionResetError("connection to the X server lost")
        d.get_keyboard_mapping = gone
        d.events.clear()
        xi.release_everything()
        assert [e for e in d.events if e[0] == X.KeyRelease] == [(X.KeyRelease, 25)]

    def test_release_everything_keeps_what_it_could_not_release(self):
        """松开没发出去，就还记着：之后的 release_all 还能再试。"""
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        d.fail = True
        xi.release_everything()
        assert xi.held == ["w"]
        d.fail = False
        xi.release_all()
        assert (X.KeyRelease, 25) in d.events and xi.held == []


class TestModifiersHeldElsewhere:
    @pytest.mark.parametrize("state", [X.ShiftMask, X.ControlMask, X.Mod1Mask, X.Mod4Mask,
                                       X.Mod5Mask, X.ShiftMask | X.LockMask, X.Mod3Mask])
    def test_nothing_is_pressed_while_a_modifier_is_down(self, state):
        """人正按着 Shift（或者别的客户端按着 Ctrl）：发出去的 3 会变成 Shift+3。Mod3 这一组
        一个键都没有：亮着就说明有什么在设它，也算。"""
        d, w = nested()
        w.root.state = state
        xi = live_input(d, w)
        xi.key_press("w")
        xi.key_tap("3")
        xi.mouse_press(5, 5, "middle")
        assert d.events == [] and xi.skipped == 3

    @pytest.mark.parametrize("state", [X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask,
                                       X.Button1Mask])
    def test_caps_lock_num_lock_and_mouse_buttons_do_not_count(self, state):
        """Num Lock 在这张映射里是 Mod2（键码 77）。鼠标键不是修饰键。"""
        d, w = nested()
        w.root.state = state
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [(X.KeyPress, 12), (X.KeyRelease, 12)]

    def test_a_key_down_in_num_lock_s_group_still_counts(self):
        """Num Lock 亮着、和它同组的另一个键（这里是 92）也正按着：那一位就不只是锁定的亮。"""
        d, w = nested()
        d.modifiers[4] = [77, 92]
        d.keys_down.add(92)
        w.root.state = X.Mod2Mask
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    def test_a_modifier_latched_in_num_lock_s_group_counts(self):
        """StickyKeys 把和 Num Lock 同组的选档位键"粘"上了：松了手，QueryKeymap 里一个键都没
        按着，那一位却亮着，发出去的键会带上它。组里不全是锁定键，就分不清，算按着。"""
        d, w = nested()
        d.modifiers[4] = [77, 92]
        w.root.state = X.Mod2Mask
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    def test_a_lock_key_alone_in_its_group_counts_while_it_is_held(self):
        """键上的符号是 Caps_Lock，却单独占着 Control 那一组（按着时当 Ctrl 用）：正按着，就算。"""
        d, w = nested()
        d.modifiers[1] = [0, 0]
        d.modifiers[2] = [66]
        d.keys_down.add(66)
        w.root.state = X.ControlMask
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    def test_num_lock_moved_to_another_key_still_does_not_count(self):
        """运行中换了映射，Num Lock 到了另一个键码上：锁定键按此刻的映射认。"""
        d, w = nested()
        xi = live_input(d, w)
        del d.keymap[77]
        d.keymap[78] = ["Num_Lock"]
        d.modifiers[4] = [78, 0]
        w.root.state = X.Mod2Mask
        xi.key_tap("3")
        assert d.events == [(X.KeyPress, 12), (X.KeyRelease, 12)]

    def test_a_former_lock_key_turned_level_shift_counts(self):
        """原来的 Caps Lock 键运行中成了选档位的键：它那一组亮着，就是修饰键亮着。"""
        d, w = nested()
        xi = live_input(d, w)
        d.keymap[66] = ["ISO_Level3_Shift"]
        d.modifiers[1] = [0, 0]
        d.modifiers[7] = [92, 66]
        w.root.state = X.Mod5Mask
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    def test_caps_lock_held_as_an_extra_ctrl_counts(self):
        """GNOME 里"Caps Lock 也当 Ctrl 用"：键上的符号还是 Caps_Lock，按着时亮的却是 Control。
        锁定键自己正按着，那一组也算按着修饰键。"""
        d, w = nested()
        d.modifiers[1] = [0, 0]
        d.modifiers[2] = [37, 105, 66]
        d.keys_down.add(66)
        w.root.state = X.ControlMask
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    @pytest.mark.parametrize("index, state", [(1, X.LockMask), (0, X.ShiftMask)],
                             ids=["on Lock", "on Shift"])
    def test_shift_lock_counts(self, index, state):
        """Shift Lock 让每个键都用 Shift 档，3 就成了 #。X 协议里它挂在 Lock 上，XKB 的
        caps:shiftlock 把它挂在 Shift 上。"""
        modifiers = [list(codes) for codes in MODIFIERS]
        modifiers[1] = [0, 0]
        modifiers[index] = modifiers[index] + [66]
        d, w = nested(keymap={**KEYMAP, 66: ["Shift_Lock"]}, modifiers=modifiers)
        w.root.state = state
        xi = live_input(d, w)
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 1

    def test_releases_go_out_even_with_a_modifier_down(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        w.root.state = X.ShiftMask
        xi.release_all()
        assert d.events == [(X.KeyPress, 25), (X.KeyRelease, 25)]


class TestKeyboardLayouts:
    """在几套布局之间切换只改服务器状态里的 XKB 组（mask 的第 13、14 位），映射和修饰键
    都不变。"""

    @pytest.mark.parametrize("layout", [2, 3, 4])
    def test_no_key_goes_out_on_another_layout(self, layout):
        """第二套布局上，w 那个键码打出来的可能是 ц：键不发，鼠标不管布局。"""
        d, w = nested()
        w.root.state = (layout - 1) << 13
        xi = live_input(d, w)
        xi.key_press("w")
        xi.key_tap("3")
        assert d.events == [] and xi.skipped == 2 and xi.is_ready()
        xi.mouse_press(5, 5, "middle")
        assert d.events == [(X.MotionNotify, 0, 5, 5), (X.ButtonPress, 2)]

    def test_a_layout_switch_under_a_held_key_lets_go(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.key_press("w")
        xi.mouse_press(5, 5, "middle")
        w.root.state |= 1 << 13
        assert not xi.check_holds()
        assert xi.held == [] and xi.is_ready()

    def test_a_held_button_alone_does_not_care_about_the_layout(self):
        d, w = nested()
        xi = live_input(d, w)
        xi.mouse_press(5, 5, "middle")
        w.root.state |= 1 << 13
        assert xi.check_holds() and xi.held == ["middle"]


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
                       "get_pointer_mapping", "get_keyboard_mapping", "query_keymap",
                       "get_input_focus", "has_extension", "sync", "screen", "get_atom_name"):
            assert hasattr(xdisplay.Display, method), method

    def test_query_pointer_replies_with_a_mask(self):
        from Xlib.protocol import request
        names = [f.name for f in request.QueryPointer._reply.fields if f.name]
        assert "mask" in names

    def test_the_connection_setup_reply_carries_the_keycode_range(self):
        """python-xlib 自己也是从 Display.display.info 的这两个字段读键码范围的。"""
        from Xlib.protocol import display as xprotocol
        names = [f.name for f in xprotocol.ConnectionSetupRequest._success_reply.fields if f.name]
        assert {"min_keycode", "max_keycode"} <= set(names)

