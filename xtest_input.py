# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""键鼠输入：经 XTest 送进游戏所在的嵌套 X，只送给游戏。

backend.KmbBackend 管"要做什么"（按住前进、开打、再来一次、确认、全部松开），"怎么送到
游戏"交给一个窗口输入对象：key_press、key_release、key_tap、mouse_press、mouse_release、
is_ready 和 mode。Windows 上那是 WindowInput（抢焦点或者注入），这里的 XTestInput 是
Linux 上的那一个。

为什么用 XTest：2026-10-05 对着游戏测过一次，经 XTest 送进嵌套 X 的 Escape 游戏收到了，
宿主桌面没有收到；嵌套 gamescope 失焦时游戏也照常在跑。字母、数字和鼠标还没对着游戏
试过，那几样是推断。

每道关都是为了不把按键送到别处：

  1. 只认嵌套 X：根窗口上得有 gamescope 的 GAMESCOPE_* 属性，还得有 XTEST 扩展，否则
     构造时就拒绝。宿主那边的 X（GNOME 的 Xwayland）没有这些属性，连错了就停在这里。
  2. 按下之前看一眼嵌套 X 的输入焦点，得是游戏窗口或者它里面的窗口。焦点在别处（Wine
     的对话框、覆盖层）就不发，记一笔，连续跳过的次数记在 skipped 上，暂停与否由调用方
     决定。鼠标还要多一道：按钮事件落到指针下面的窗口，而不是焦点所在的窗口，所以中键
     按下之前还要确认那一点上最里层的窗口是游戏窗口或者它里面的。松开不看这些：按着的
     不管落到哪里都得松开。
  3. 只发配置 [keys] 里写的那几个键，而且不能是修饰键：Super 组合是 gamescope 自己的
     快捷键，Ctrl、Alt、Shift 会改掉别的键的意思。修饰键按嵌套 X 自己的修饰键映射认。
     也不能是要配合修饰键才打得出的键（大写字母、Shift 档上的符号）：XTest 只发键码，
     不带 Shift 发出去的是另一个字。鼠标只有中键。
  4. 按着什么自己记着，release_all 把它们全部松开，可以重复调用。

live 为假就是空跑：检查照做、日志照记，一个 XTest 事件都不发。空跑也照样记着"按着"
什么，松开的那一路在空跑里也走得到。所有事件都从 _send 出去，空跑只拦这一处。
"""

import time

from Xlib import X, XK

from applog import get_logger
from gamescope import describe_focus, gamescope_root_properties, within_window

log = get_logger(__name__)

# 鼠标只用中键（KmbBackend.battle_press）。这是逻辑按钮号：1 左键、2 中键、3 右键。XTest 发的
# 是物理按钮号，要经过服务器的指针映射才变成逻辑号，所以构造时按映射倒查一次。
BUTTONS = {"middle": 2}

# key_tap 按下到松开之间等多久，和探测器 L3 用的一样。
TAP_HOLD_S = 0.05


class InputRefused(RuntimeError):
    """这个 X 显示或这份按键配置不能用来给游戏发输入。"""


class XTestInput:
    """KmbBackend 要的窗口输入对象，经 XTest 发到嵌套 X 里的游戏窗口。

    坐标和 Windows 那边的约定一样，是相对游戏窗口左上角的。
    """

    mode = "xtest"

    def __init__(self, display, window, keys, live=False, sleep=time.sleep, hold=TAP_HOLD_S):
        """display：连好的嵌套 X（python-xlib 的 Display）。window：游戏窗口。keys：配置里的
        [keys]，比如 {"move": "w", "again": "3", "confirm": "a"}。"""
        self._d = display
        self._window = window
        self._live = bool(live)
        self._sleep = sleep
        self._hold = hold
        self._held_keys = {}      # 键码 -> 键名
        self._held_buttons = {}   # 按钮号 -> 按钮名
        self.skipped = 0
        self._ready = False

        if not any(name.startswith("GAMESCOPE") for name in gamescope_root_properties(display)):
            raise InputRefused("这个 X 显示的根窗口上没有 gamescope 的属性，不是游戏所在的嵌套 X")
        if not display.has_extension("XTEST"):
            raise InputRefused("嵌套 X 没有 XTEST 扩展")
        modifiers = {code for codes in display.get_modifier_mapping() for code in codes if code}
        self._keycodes = {}
        for name in dict.fromkeys(keys.values()):
            keysym = XK.string_to_keysym(name)
            keycode = display.keysym_to_keycode(keysym) if keysym else 0
            if not keycode:
                raise InputRefused(f"嵌套 X 的键盘映射里没有 {name!r}")
            if keycode in modifiers:
                raise InputRefused(f"{name!r} 在嵌套 X 里是修饰键，不发")
            # keysym_to_keycode 找的是"哪个键上有这个符号"，不管它在第几档。不在第一档的
            # 符号（"A" 在 a 键的 Shift 档上），发那个键码打出来的是第一档的字
            if display.keycode_to_keysym(keycode, 0) != keysym:
                raise InputRefused(f"{name!r} 在嵌套 X 里要配合修饰键才打得出来，不发：只发"
                                   "不按 Shift 等键就能打出的那个字")
            self._keycodes[name] = keycode
        # get_pointer_mapping() 的第 N 项是物理按钮 N+1 对应的逻辑按钮
        mapping = list(display.get_pointer_mapping())
        self._buttons = {}
        for button, logical in BUTTONS.items():
            if logical not in mapping:
                raise InputRefused(f"嵌套 X 的指针映射里没有哪个物理按钮对应逻辑上的 {button} 键")
            self._buttons[button] = mapping.index(logical) + 1
        self._ready = True
        if not self._live:
            log.warning("空跑：一个 XTest 事件都不会发，只记日志")

    @property
    def held(self):
        """现在按着的键名和按钮名。"""
        return sorted(self._held_keys.values()) + sorted(self._held_buttons.values())

    def is_ready(self):
        return self._ready

    def _keycode(self, name):
        if name not in self._keycodes:
            raise ValueError(f"refusing to send {name!r}: not one of the configured keys "
                             f"{sorted(self._keycodes)}")
        return self._keycodes[name]

    def _button(self, button):
        if button not in self._buttons:
            raise ValueError(f"refusing to press {button!r}: only {sorted(self._buttons)}")
        return self._buttons[button]

    def _focus_on_game(self, what):
        """焦点在游戏窗口或它里面才返回真。不在就记一笔，skipped 加一。"""
        try:
            focus = self._d.get_input_focus().focus
        except Exception:
            log.warning("读不到嵌套 X 的输入焦点，%s 不发", what, exc_info=True)
            self._ready = False
            return False
        if within_window(focus, self._window.id):
            return True
        self.skipped += 1
        log.warning("嵌套 X 的输入焦点不在游戏窗口上（焦点 %s，游戏 %s），%s 不发（连续第 %d 次）",
                    describe_focus(focus), hex(self._window.id), what, self.skipped)
        return False

    def _window_at(self, root, rx, ry, max_depth=32):
        """根窗口坐标 (rx, ry) 处最里层的窗口，也就是按钮事件会落到的那个。

        TranslateCoords 应答里的 child 是目标窗口里包含这个点、映射着的子窗口；从根开始一层层
        往下找，到没有子窗口为止。不靠"先移指针再 QueryPointer"：空跑时指针不动，这样也查
        得出来。
        """
        window = root
        for _ in range(max_depth):
            child = window.translate_coords(root, rx, ry).child
            if not getattr(child, "id", 0):
                return window
            window = child
        return window

    def _send(self, event_type, detail, what, x=0, y=0):
        """发一个 XTest 事件。空跑只在这里拦。连接出错就不再算就绪。"""
        if not self._live:
            log.info("[空跑] 本应发送: %s", what)
            return True
        try:
            self._d.xtest_fake_input(event_type, detail, x=x, y=y)
            self._d.sync()
        except Exception:
            log.warning("XTest 发送失败: %s", what, exc_info=True)
            self._ready = False
            return False
        return True

    def key_press(self, name):
        keycode = self._keycode(name)
        if keycode in self._held_keys:
            return
        what = f"按下 {name}"
        if not self._ready or not self._focus_on_game(what):
            return
        if self._send(X.KeyPress, keycode, what):
            self._held_keys[keycode] = name
            self.skipped = 0

    def key_release(self, name):
        keycode = self._keycode(name)
        if keycode in self._held_keys and self._send(X.KeyRelease, keycode, f"松开 {name}"):
            del self._held_keys[keycode]

    def key_tap(self, name):
        keycode = self._keycode(name)
        if keycode in self._held_keys:
            # 正按着的键再点一下，松开的那一下会把按着的也松掉
            log.warning("%s 正按着，这次不点", name)
            return
        what = f"点 {name}"
        if not self._ready or not self._focus_on_game(what):
            return
        if not self._send(X.KeyPress, keycode, f"按下 {name}"):
            return
        self._held_keys[keycode] = name
        self.skipped = 0
        self._sleep(self._hold)
        if self._send(X.KeyRelease, keycode, f"松开 {name}"):
            del self._held_keys[keycode]

    def mouse_press(self, x, y, button):
        detail = self._button(button)
        if detail in self._held_buttons:
            return
        what = f"在窗口内 ({x}, {y}) 按下鼠标 {button}"
        if not self._ready or not self._focus_on_game(what):
            return
        root = self._d.screen().root
        try:
            # TranslateCoords 把游戏窗口里的点换成根窗口坐标，XTest 的指针移动要的是后者
            point = root.translate_coords(self._window, x, y)
            target = self._window_at(root, point.x, point.y)
        except Exception:
            log.warning("换算不出指针位置，%s 不发", what, exc_info=True)
            return
        if not within_window(target, self._window.id):
            self.skipped += 1
            log.warning("根窗口 (%d, %d) 处最上面的不是游戏窗口（是 %s，游戏 %s），%s 不发"
                        "（连续第 %d 次）", point.x, point.y, describe_focus(target),
                        hex(self._window.id), what, self.skipped)
            return
        if (self._send(X.MotionNotify, 0, f"指针移到根窗口 ({point.x}, {point.y})",
                       x=point.x, y=point.y)
                and self._send(X.ButtonPress, detail, what)):
            self._held_buttons[detail] = button
            self.skipped = 0

    def mouse_release(self, x, y, button):
        detail = self._button(button)
        # 按下时 X 把指针隐式抓给了那个窗口，松开发出去也回到它那里，所以不再移指针
        if detail in self._held_buttons and self._send(X.ButtonRelease, detail,
                                                       f"松开鼠标 {button}"):
            del self._held_buttons[detail]

    def release_all(self):
        """把按着的全部松开。停下、暂停、出错、退出时都调用，可以重复调用。"""
        for keycode, name in list(self._held_keys.items()):
            if self._send(X.KeyRelease, keycode, f"松开 {name}"):
                del self._held_keys[keycode]
        for detail, button in list(self._held_buttons.items()):
            if self._send(X.ButtonRelease, detail, f"松开鼠标 {button}"):
                del self._held_buttons[detail]

    def release_everything(self):
        """不管记没记着，把配置里的每个键和中键都松一遍。

        给"脚本被强杀以后"用：那时按着什么已经没人记得，只能全松一遍。嵌套 X 会不会替
        断开的连接留着按下的键，没量过，所以这是尽力而为；最稳的还是在游戏窗口里自己把
        W 和中键点一下。
        """
        for name, keycode in self._keycodes.items():
            self._send(X.KeyRelease, keycode, f"松开 {name}")
        for button, detail in self._buttons.items():
            self._send(X.ButtonRelease, detail, f"松开鼠标 {button}")
        self._held_keys.clear()
        self._held_buttons.clear()
