import time
import tkinter as tk

from applog import get_logger
from window_input import WindowInput

log = get_logger(__name__)


class Option:
    def __init__(self, root: tk.Tk, keys=None):
        self.root = root
        self._is_battle_ing = False
        self._wi = WindowInput()
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

    def start_battle(self):
        if self._is_battle_ing:
            return
        self._is_battle_ing = True
        self._wi.key_press(self._key_move)
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_press(cx, cy, "middle")

    def end_battle(self):
        if not self._is_battle_ing:
            return
        self._is_battle_ing = False
        self._wi.key_release(self._key_move)
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_release(cx, cy, "middle")

    def switch_again(self):
        self._wi.key_tap(self._key_again)

    def tap_enter(self):
        # 名字是继承来的：它按的是 keys.confirm（默认 "a"），不是 Enter。改名会
        # 动到上游文件的多处调用点，留给 #15。
        self._wi.key_tap(self._key_confirm)

    def clear_all(self):
        self.end_battle()

    def _get_center(self):
        # 返回 None 时 start_battle/end_battle 会直接跳过鼠标事件 —— 战斗照跑，
        # 但中键不会按下。原先这条路径一声不吭，看起来就像"按键没生效"。
        try:
            from window_capture import get_window_rect
            rect = get_window_rect(self._wi.hwnd)
            if rect:
                left, top, right, bottom = rect
                return ((right - left) // 2, (bottom - top) // 2)
            log.warning("取窗口矩形失败 (hwnd=%s)，本次跳过鼠标事件", self._wi.hwnd)
        except Exception:
            log.exception("计算窗口中心失败 (hwnd=%s)", getattr(self._wi, "hwnd", None))
        return None
