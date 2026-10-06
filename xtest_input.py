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
  2. 只发配置 [keys] 里写的那几个键，而且不能是修饰键（Super 组合是 gamescope 自己的快捷
     键，Ctrl、Alt、Shift 会改掉别的键的意思），也不能是要配合修饰键才打得出的键（大写
     字母、Shift 档上的符号：XTest 只发键码，不带 Shift 发出去的是另一个字），也不能是
     Num Lock 会改掉的小键盘键。鼠标只有中键，物理按钮号按服务器的指针映射查。
  3. 每次按下之前，一次读齐服务器此刻的状态再决定：
       - 输入焦点得是游戏窗口或者它里面的窗口（焦点在 Wine 的对话框、覆盖层上就不发）；
       - 键盘映射和指针映射还是构造时的样子：运行中改了映射（比如重新设了一套布局），缓存
         的键码可能已经是另一个字，甚至成了修饰键，或者成了 Num Lock 会改掉的小键盘键，那就
         不再发，并且不再算就绪；
       - 没有修饰键按着（人正按着 Shift，3 就成了 Shift+3）；Caps Lock、Num Lock 常年亮着，
         不算，可它们那一组里要是有键正按着，照样算。哪个键是锁定键按此刻的映射认。Shift
         Lock 不在其内：它让每个键都用 Shift 档，3 就成了 #；
       - 键盘在第一套布局上（XKB 的组）：在几套布局之间切换不改映射，也不按修饰键，只改
         服务器状态里的组，同一个键码打出来的却是另一套布局里的字。鼠标不管这一条；
       - 这个键（或中键）没有被别处按着：这边不替别人按下，更不在之后替别人松开。
     中键还要看点下去的那一点：按钮事件落到指针下面最里层的窗口，那得是游戏窗口或者它
     里面的；指针移过去以后再看一次，移过去可能刚好叫出一个弹窗。
     不满足的就不发，记一笔，连续跳过的次数记在 skipped 上，暂停与否由调用方决定。
  4. 按住不放的键，服务器会自动连发，连发不再经过上面这几道关。所以按着东西时，调用方每
     一轮叫一次 check_holds()：输入不再就绪了、焦点离开了游戏、有修饰键按下了、切换了布局、
     映射变了、或者按着的已经被别处松开了，就全部松开，交给下一轮重新按。连发漏到别处的
     时间最多是一轮。点一下时松开没发出去，那个键就还记着按着，下一轮也由这里再松一次。
  5. 按着什么自己记着，release_all 把它们全部松开，可以重复调用。按下的请求发出去以后，不管
     sync 有没有回来，都当它按着；松开的请求发成功了才不再记着，release_everything 也一样。
     松开不看焦点和修饰键：按着的不管落到哪里都得松开。

读状态出错（多半是连接断了）就不再算就绪：调用方看得到，循环会停下。只有"查的那个窗口在
半路没了"（BadWindow 这类协议错误）当成一次普通的跳过。

没做的三件事，以及为什么：

  - 检查和按下之间不是原子的。要原子就得在检查和按下期间抓住整个服务器（GrabServer），可那
    会让游戏自己的 X 请求也停下来（它的画面经这个 Xwayland 出去）；而且在 gamescope 里，
    XTest 的事件要先经 libei 出去、再从 gamescope 的输入那条路回来，抓着服务器也锁不住这一
    段。检查和按下之间只隔几次本机往返；按着的键每一轮还会再查。
  - 别的客户端抓着键盘或指针（grab）时，事件会送到抓的那个窗口。X 没有请求能查"谁抓着"，
    想知道只能自己去抓一下，而每次抓都会给游戏发 NotifyGrab 的焦点事件：在 Wine 里这可能被
    当成失焦，而失焦正是 Windows 上让游戏暂停的那件事。嵌套 X 里别的客户端只有游戏自己的
    进程和 gamescope 的覆盖层；真跑起来要是看到抓取把输入带走了，先量，再决定。
  - 指针移过去以后，查的是要点的那一点，而不是读出来的指针位置。在 gamescope 里，XTest 的
    移动也要经 libei 绕一圈才生效，sync 回来时指针可能还在原处；按钮事件跟在移动后面走同
    一条路，落在移过去的那一点上，除非有别的客户端约束或者挪动了指针（点的是游戏窗口的
    中心，游戏要挪指针，多半也是挪到那里）。读真实位置再查，在这里会把该发的点击当成没到。
    第一次带鼠标实跑时，量一下移动要多久生效、落点准不准，再定。

live 为假就是空跑：检查照做、日志照记，一个 XTest 事件都不发。空跑也照样记着"按着"
什么，松开的那一路在空跑里也走得到。所有事件都从 _send 出去，空跑只拦这一处。
"""

import time

from Xlib import X, XK
from Xlib import error as xerror

from applog import get_logger
from gamescope import describe_focus, gamescope_root_properties, within_window

log = get_logger(__name__)

# 鼠标只用中键（KmbBackend.battle_press）。这是逻辑按钮号：1 左键、2 中键、3 右键。XTest 发的
# 是物理按钮号，要经过服务器的指针映射才变成逻辑号，所以每次都按映射倒查一次。
BUTTONS = {"middle": 2}

# key_tap 按下到松开之间等多久，和探测器 L3 用的一样。
TAP_HOLD_S = 0.05

# 亮着也不算"按着修饰键"的锁定键。照 X 协议：Caps Lock 只把小写字母换成大写，Num Lock 只
# 改小键盘上的键；Shift Lock 让每个键都用 Shift 档，所以不在这里。
LOCK_KEYSYMS = {XK.XK_Caps_Lock, XK.XK_Num_Lock}


class InputRefused(RuntimeError):
    """这个 X 显示或这份按键配置不能用来给游戏发输入。"""


def _codes(modifier_map):
    """修饰键映射里出现的所有键码。"""
    return {code for codes in modifier_map for code in codes if code}


def _is_down(keys_down, keycode):
    """QueryKeymap 的应答：32 个字节，第 N 个字节是键码 8N 到 8N+7，低位在前。"""
    return bool(keys_down[keycode // 8] & (1 << (keycode % 8)))


def _physical(logical, pointer_map):
    """GetPointerMapping 的第 N 项是物理按钮 N+1 对应的逻辑按钮。倒查；没有就是 None。"""
    mapping = list(pointer_map)
    return mapping.index(logical) + 1 if logical in mapping else None


def _button_mask(logical):
    """状态里表示这个逻辑按钮按着的那一位：Button1Mask 是 1 << 8，往上依次是 2、3……"""
    return X.Button1Mask << (logical - 1)


def _is_keypad(keysym):
    """X 协议说的小键盘符号：0xFF80 到 0xFFBD，以及厂商的 0x11000000 到 0x1100FFFF。"""
    return 0xFF80 <= keysym <= 0xFFBD or 0x11000000 <= keysym <= 0x1100FFFF


def _num_lock_changes(keysyms):
    """照 X 协议，Num Lock 亮着时，第二档是小键盘符号的键用第二档：KP_End 成了 KP_1。第二档
    和第一档一样的（KP_Add），Num Lock 改不了它。"""
    return len(keysyms) > 1 and _is_keypad(keysyms[1]) and keysyms[1] != keysyms[0]


def _group(mask):
    """状态里的 XKB 组，也就是第几套键盘布局，0 是第一套。X 服务器把它放在第 13、14 位
    （XkbBuildCoreState），QueryPointer 应答里的 mask 也带着它。"""
    return (mask >> 13) & 3


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
        self._held_buttons = {}   # 按钮名 -> 按下时用的物理按钮号
        self.skipped = 0
        self._ready = False

        if not any(name.startswith("GAMESCOPE") for name in gamescope_root_properties(display)):
            raise InputRefused("这个 X 显示的根窗口上没有 gamescope 的属性，不是游戏所在的嵌套 X")
        if not display.has_extension("XTEST"):
            raise InputRefused("嵌套 X 没有 XTEST 扩展")
        modifiers = _codes(display.get_modifier_mapping())
        self._keycodes = {}
        self._keysyms = {}
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
            # Num Lock 亮着不算按着修饰键，所以它会改掉的小键盘键干脆不发
            if _num_lock_changes((keysym, display.keycode_to_keysym(keycode, 1))):
                raise InputRefused(f"{name!r} 是小键盘上的键，Num Lock 一亮打出来的就是另一个字，"
                                   "不发")
            self._keycodes[name] = keycode
            self._keysyms[name] = keysym
        pointer_map = display.get_pointer_mapping()
        for button, logical in BUTTONS.items():
            if _physical(logical, pointer_map) is None:
                raise InputRefused(f"嵌套 X 的指针映射里没有哪个物理按钮对应逻辑上的 {button} 键")
        # 服务器的键码范围，连接建立时就定了。每次读状态都按它读整张键盘映射：python-xlib
        # 的 keysym_to_keycode 查的是连接时缓存的那一张，不收 MappingNotify 就不会更新
        info = display.display.info
        self._first_keycode = info.min_keycode
        self._keycode_count = info.max_keycode - info.min_keycode + 1
        self._ready = True
        if not self._live:
            log.warning("空跑：一个 XTest 事件都不会发，只记日志")

    @property
    def held(self):
        """现在按着的键名和按钮名。"""
        return sorted(self._held_keys.values()) + sorted(self._held_buttons)

    def is_ready(self):
        return self._ready

    def _keycode(self, name):
        if name not in self._keycodes:
            raise ValueError(f"refusing to send {name!r}: not one of the configured keys "
                             f"{sorted(self._keycodes)}")
        return self._keycodes[name]

    def _check_button(self, button):
        if button not in BUTTONS:
            raise ValueError(f"refusing to press {button!r}: only {sorted(BUTTONS)}")

    def _skip(self, what, why, *args):
        self.skipped += 1
        log.warning(f"{why}，%s 不发（连续第 %d 次）", *args, what, self.skipped)

    def _focus_on_game(self, what):
        """焦点在游戏窗口或它里面才返回真。不在就记一笔，skipped 加一。"""
        try:
            focus = self._d.get_input_focus().focus
        except Exception:
            log.warning("读不到嵌套 X 的输入焦点，%s 不发", what, exc_info=True)
            self._ready = False
            return False
        try:
            inside = within_window(focus, self._window.id, strict=True)
        except xerror.XError:
            # 焦点所在的窗口在两次查询之间没了：当成一次普通的焦点不对
            inside = False
        except Exception:
            log.warning("查不出输入焦点所在的窗口，%s 不发", what, exc_info=True)
            self._ready = False
            return False
        if inside:
            return True
        self._skip(what, "嵌套 X 的输入焦点不在游戏窗口上（焦点 %s，游戏 %s）",
                   describe_focus(focus), hex(self._window.id))
        return False

    def _read_state(self, what):
        """一次读齐按下之前要看的服务器状态。读不到就不再算就绪，返回 None。

        键盘映射读整张（键码 -> 各档的符号）：要发的键、按着的键、哪些是锁定键，都按此刻的
        这一张认。mask 里有修饰键、鼠标键和 XKB 组。
        """
        try:
            rows = self._d.get_keyboard_mapping(self._first_keycode, self._keycode_count)
            return {
                "modifier_map": self._d.get_modifier_mapping(),
                "keymap": dict(enumerate(rows, start=self._first_keycode)),
                "mask": self._d.screen().root.query_pointer().mask,
                "keys_down": self._d.query_keymap(),
                "pointer_map": self._d.get_pointer_mapping(),
            }
        except Exception:
            log.warning("读不到嵌套 X 的键盘和指针状态，%s 不发", what, exc_info=True)
            self._ready = False
            return None

    def _modifier_held(self, state, what):
        """有修饰键按着就记一笔、返回真：这时发出去的键会拼成组合键。

        锁定键（Caps Lock、Num Lock）亮着时它那一组的位也是亮的，那不算；但只在组里一个键都
        没有按着时才不算：和 Num Lock 同组的另一个键（比如选档位的键）正按着，照样算。锁定键
        是此刻映射里第一个符号是 Caps_Lock 或 Num_Lock 的键码：映射变过，就按变了以后的认。
        修饰键映射的第 i 组对应状态里的 1 << i 那一位。
        """
        locks = {code for code, keysyms in state["keymap"].items()
                 if keysyms and keysyms[0] in LOCK_KEYSYMS}
        held = 0
        for index, codes in enumerate(state["modifier_map"]):
            bit = 1 << index
            if not state["mask"] & bit:
                continue
            members = [code for code in codes if code]
            if (any(code in locks for code in members)
                    and not any(_is_down(state["keys_down"], code) for code in members)):
                continue
            held |= bit
        if held:
            self._skip(what, "嵌套 X 里有修饰键按着（状态 %#x），发出去会变成组合键", held)
        return bool(held)

    def _other_layout(self, state, what):
        """键盘不在第一套布局上就记一笔、返回真。映射里核对过的是第一套布局的字，换到别的
        布局，同一个键码打出来的是那一套里的字。"""
        group = _group(state["mask"])
        if group:
            self._skip(what, "嵌套 X 的键盘在第 %d 套布局上（切换过布局），同一个键打出来的不是"
                       "配置里的字", group + 1)
        return bool(group)

    def _clear_to_press_key(self, name, keycode, what):
        if not self._ready or not self._focus_on_game(what):
            return False
        state = self._read_state(what)
        if state is None:
            return False
        if not self._still_configured(name, keycode, state["keymap"],
                                      _codes(state["modifier_map"])):
            log.warning("嵌套 X 的键盘映射变了：键码 %d 已经不是 %r（或者成了修饰键、Num Lock 会"
                        "改掉的键），不再发键", keycode, name)
            self._ready = False
            return False
        if self._modifier_held(state, what) or self._other_layout(state, what):
            return False
        if _is_down(state["keys_down"], keycode):
            self._skip(what, "%s 已经被别处按着：这边不替别人按，也不替别人松", name)
            return False
        return True

    def _clear_to_press_button(self, button, what):
        """可以按就返回这一刻该发的物理按钮号，不行就返回 None。"""
        if not self._ready or not self._focus_on_game(what):
            return None
        state = self._read_state(what)
        if state is None:
            return None
        logical = BUTTONS[button]
        detail = _physical(logical, state["pointer_map"])
        if detail is None:
            log.warning("嵌套 X 的指针映射变了：没有哪个物理按钮再对应 %s 键，不再发", button)
            self._ready = False
            return None
        if self._modifier_held(state, what):
            return None
        if state["mask"] & _button_mask(logical):
            self._skip(what, "鼠标 %s 已经被别处按着：这边不替别人按，也不替别人松", button)
            return None
        return detail

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

    def _point_is_on_game(self, root, point, what):
        try:
            target = self._window_at(root, point.x, point.y)
            inside = within_window(target, self._window.id, strict=True)
        except xerror.XError:
            # 半路有个窗口没了（BadWindow 这类）：这一下不发，记一笔
            self._skip(what, "查指针下面的窗口时，有个窗口在半路没了")
            return False
        except Exception:
            log.warning("查不出指针下面的窗口，%s 不发", what, exc_info=True)
            self._ready = False
            return False
        if inside:
            return True
        self._skip(what, "根窗口 (%d, %d) 处最上面的不是游戏窗口（是 %s，游戏 %s）",
                   point.x, point.y, describe_focus(target), hex(self._window.id))
        return False

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
        if not self._clear_to_press_key(name, keycode, what):
            return
        # 先记成按着：请求发出去以后连接才断，服务器也可能已经按下了
        self._held_keys[keycode] = name
        if self._send(X.KeyPress, keycode, what):
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
        if not self._clear_to_press_key(name, keycode, what):
            return
        self._held_keys[keycode] = name
        if not self._send(X.KeyPress, keycode, f"按下 {name}"):
            return
        self.skipped = 0
        self._sleep(self._hold)
        if self._send(X.KeyRelease, keycode, f"松开 {name}"):
            del self._held_keys[keycode]

    def mouse_press(self, x, y, button):
        self._check_button(button)
        if button in self._held_buttons:
            return
        what = f"在窗口内 ({x}, {y}) 按下鼠标 {button}"
        detail = self._clear_to_press_button(button, what)
        if detail is None:
            return
        root = self._d.screen().root
        try:
            # TranslateCoords 把游戏窗口里的点换成根窗口坐标，XTest 的指针移动要的是后者
            point = root.translate_coords(self._window, x, y)
        except Exception:
            log.warning("换算不出指针位置，%s 不发", what, exc_info=True)
            self._ready = False
            return
        if not self._point_is_on_game(root, point, what):
            return
        if not self._send(X.MotionNotify, 0, f"指针移到根窗口 ({point.x}, {point.y})",
                          x=point.x, y=point.y):
            return
        # 指针移过去，可能刚好叫出一个不抢焦点的弹窗：按下之前在那一点上再看一次
        if not self._point_is_on_game(root, point, what):
            return
        self._held_buttons[button] = detail
        if self._send(X.ButtonPress, detail, what):
            self.skipped = 0

    def mouse_release(self, x, y, button):
        self._check_button(button)
        # 按下时 X 把指针隐式抓给了那个窗口，松开发出去也回到它那里，所以不再移指针；
        # 发的是按下时的那个物理按钮号，期间映射变了也一样
        detail = self._held_buttons.get(button)
        if detail is not None and self._send(X.ButtonRelease, detail, f"松开鼠标 {button}"):
            del self._held_buttons[button]

    def check_holds(self):
        """按着东西时，调用方每一轮叫一次。还该按着就返回真。

        按住不放的键，服务器会自动连发，连发不再经过按下时的那几道关。所以每一轮看一眼：
        输入不再就绪了、焦点离开了游戏、有修饰键按下了、（按着键时）切换了布局、映射变了、或者
        （实跑时）按着的已经被别处松开了，就把按着的全部松开，返回假，交给下一轮重新按。

        不再就绪也包括点一下时松开没发出去：那个键还记着按着，这里每一轮都再松一次，而不是
        把它当成该按着的。
        """
        if not self._held_keys and not self._held_buttons:
            return True
        what = "继续按着 " + "、".join(self.held)
        if not self._ready:
            log.warning("输入不再就绪，不再%s，全部松开", what)
            intact = False
        else:
            intact = self._focus_on_game(what)
        state = self._read_state(what) if intact else None
        if (state is None or self._modifier_held(state, what)
                or (self._held_keys and self._other_layout(state, what))
                or not self._held_still_mapped(state)):
            intact = False
        elif self._live:
            lost = [name for code, name in self._held_keys.items()
                    if not _is_down(state["keys_down"], code)]
            lost += [button for button in self._held_buttons
                     if not state["mask"] & _button_mask(BUTTONS[button])]
            if lost:
                log.info("%s 已经不在按下状态（被别处松开了），全部松开，下一轮重新按",
                         "、".join(lost))
                intact = False
        if not intact:
            self.release_all()
        return intact

    def _still_configured(self, name, keycode, keymap, modifiers):
        """此刻的映射里，这个键码还是配置里的那个键：第一个符号没变，不是修饰键，Num Lock 也
        改不了它。构造时查过的，映射一变就得重新查。"""
        keysyms = keymap.get(keycode)
        return (bool(keysyms) and keysyms[0] == self._keysyms[name]
                and keycode not in modifiers and not _num_lock_changes(keysyms))

    def _held_still_mapped(self, state):
        """按着的键码还是配置里的那个键（_still_configured），按着的物理按钮还是中键。映射
        变了，连发出去的就成了另一个键：返回假，并且不再算就绪。"""
        modifiers = _codes(state["modifier_map"])
        changed = [name for code, name in self._held_keys.items()
                   if not self._still_configured(name, code, state["keymap"], modifiers)]
        changed += [button for button, detail in self._held_buttons.items()
                    if _physical(BUTTONS[button], state["pointer_map"]) != detail]
        if changed:
            log.warning("按着的 %s 映射变了，全部松开，不再发键", "、".join(changed))
            self._ready = False
        return not changed

    def release_all(self):
        """把按着的全部松开。停下、暂停、出错、退出时都调用，可以重复调用。"""
        for keycode, name in list(self._held_keys.items()):
            if self._send(X.KeyRelease, keycode, f"松开 {name}"):
                del self._held_keys[keycode]
        for button, detail in list(self._held_buttons.items()):
            if self._send(X.ButtonRelease, detail, f"松开鼠标 {button}"):
                del self._held_buttons[button]

    def release_everything(self):
        """不管记没记着，把配置里的每个键和中键都松一遍。

        给"脚本被强杀以后"用：那时按着什么已经没人记得，只能全松一遍。嵌套 X 会不会替
        断开的连接留着按下的键，没量过，所以这是尽力而为；最稳的还是在游戏窗口里自己把
        W 和中键点一下。记着的按键，松开发成功了才不再记着：发不出去的，release_all 还能
        再试。

        记着的键用按下时的键码。没记着的，只在此刻的映射确认过时才松：那个键码的第一个符号
        还是配置里的字，而且不是修饰键。映射变了，按构造时的键码松开的就是另一个键，可能是
        别人正按着的修饰键。
        """
        try:
            modifiers = _codes(self._d.get_modifier_mapping())
            rows = self._d.get_keyboard_mapping(self._first_keycode, self._keycode_count)
            keymap = dict(enumerate(rows, start=self._first_keycode))
        except Exception:
            log.warning("读不到现在的键盘映射：没记着的键不松", exc_info=True)
            modifiers, keymap = set(), {}
        for name, keycode in self._keycodes.items():
            keysyms = keymap.get(keycode)
            confirmed = (bool(keysyms) and keysyms[0] == self._keysyms[name]
                         and keycode not in modifiers)
            if keycode not in self._held_keys and not confirmed:
                log.warning("现在的键盘映射里，键码 %d 已经不是 %r（或者成了修饰键），这个键不松",
                            keycode, name)
                continue
            if self._send(X.KeyRelease, keycode, f"松开 {name}"):
                self._held_keys.pop(keycode, None)
        try:
            pointer_map = self._d.get_pointer_mapping()
        except Exception:
            pointer_map = None
        for button, logical in BUTTONS.items():
            # 记着的就用按下时的那个物理按钮号。没记着的，只用现在的映射确认过的号：映射里要是
            # 没有中键了，按构造时的号松开，松开的会是另一个逻辑按钮，可能是别人正按着的
            detail = self._held_buttons.get(button)
            if detail is None and pointer_map is not None:
                detail = _physical(logical, pointer_map)
            if detail is None:
                log.warning("现在的指针映射里没有 %s 键，这个键不松", button)
                continue
            if self._send(X.ButtonRelease, detail, f"松开鼠标 {button}"):
                self._held_buttons.pop(button, None)
