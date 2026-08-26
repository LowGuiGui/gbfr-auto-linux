import tkinter as tk

import geometry
from applog import get_logger
from window_input import WindowInput

log = get_logger(__name__)


class Option:
    def __init__(self, root: tk.Tk, keys=None, dry_run=False):
        self.root = root
        self._is_battle_ing = False
        self._wi = WindowInput()
        # 空跑：照常识别、照常记录，但一个按键都不发出去。调模板和看流程时用，
        # 免得对着游戏乱按。
        self._dry_run = bool(dry_run)
        if self._dry_run:
            log.warning("空跑模式：不会向游戏发送任何输入")
        # 按键原本是散在各方法里的字面量。传 None 保留原值，方便单独构造。
        keys = keys or {}
        self._key_move = keys.get("move", "w")
        self._key_again = keys.get("again", "3")
        self._key_confirm = keys.get("confirm", "a")

    def set_target(self, hwnd_or_title):
        self._wi.set_target(hwnd_or_title)

    def is_ready(self):
        return self._wi.is_ready()

    def has_window(self):
        return self._wi.has_window()

    def set_fallback_mode(self):
        self._wi.enable_fallback()

    def enable_inject_mode(self, dll_path=None, progress_cb=None):
        return self._wi.enable_inject(dll_path, progress_cb)

    def disable_inject_mode(self):
        self._wi.disable_inject()

    @property
    def input_mode(self):
        return self._wi.mode

    def _blocked(self, what):
        if self._dry_run:
            log.info("[空跑] 本应执行: %s", what)
            return True
        return False

    def start_battle(self):
        if self._is_battle_ing:
            return
        self._is_battle_ing = True
        if self._blocked("start_battle 按住 %s + 中键" % self._key_move):
            return
        self._wi.key_press(self._key_move)
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_press(cx, cy, "middle")

    def end_battle(self):
        if not self._is_battle_ing:
            return
        self._is_battle_ing = False
        if self._blocked("end_battle 松开 %s + 中键" % self._key_move):
            return
        self._wi.key_release(self._key_move)
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_release(cx, cy, "middle")

    def switch_again(self):
        if self._blocked("switch_again 按 %s" % self._key_again):
            return
        self._wi.key_tap(self._key_again)

    def tap_confirm(self):
        """按 keys.confirm（默认 "a"）。

        原名是 tap_enter，但它从来没按过 Enter —— 上游把 key_tap("enter") 注释
        掉换成了 "a"，名字留在原地。这个名字在 _analyze_page 里是承重的：读派发
        逻辑的人会以为这里发的是 Enter，而它不是。#15。
        """
        if self._blocked("tap_confirm 按 %s" % self._key_confirm):
            return
        self._wi.key_tap(self._key_confirm)

    def clear_all(self):
        self.end_battle()

    def _get_center(self):
        """客户区中心，用窗口相对坐标表达（WindowInput._screen_pos 收的就是这个）。

        原来这里取的是**窗口**中心。窗口化时标题栏只在上面，上下边框不对称
        （实测上 45 下 11），于是中键落点比客户区中心高 17 像素 —— 见 #46。
        左右边框是对称的，所以 x 一直是对的，只有 y 错。无边框模式下两者本来
        就重合，这个改动对它没有任何影响。

        返回 None 时 start_battle/end_battle 会直接跳过鼠标事件 —— 战斗照跑，
        但中键不会按下。原先这条路径一声不吭，看起来就像"按键没生效"。
        """
        try:
            measured = geometry.read(self._wi.hwnd)
            if measured:
                return geometry.client_centre(*measured)
            log.warning("取窗口几何失败 (hwnd=%s)，本次跳过鼠标事件", self._wi.hwnd)
        except Exception:
            log.exception("计算窗口中心失败 (hwnd=%s)", getattr(self._wi, "hwnd", None))
        return None
