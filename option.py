import time
import tkinter as tk

from window_input import WindowInput


class Option:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._is_battle_ing = False
        self._wi = WindowInput()

    def set_target(self, hwnd_or_title):
        self._wi.set_target(hwnd_or_title)

    def is_ready(self):
        return self._wi.is_ready()

    def start_battle(self):
        if self._is_battle_ing:
            return
        self._is_battle_ing = True
        self._wi.key_press("w")
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_press(cx, cy, "middle")

    def end_battle(self):
        if not self._is_battle_ing:
            return
        self._is_battle_ing = False
        self._wi.key_release("w")
        rect = self._get_center()
        if rect:
            cx, cy = rect
            self._wi.mouse_release(cx, cy, "middle")

    def switch_again(self):
        self._wi.key_tap("3")

    def click_enter(self):
        self._wi.key_tap("enter")

    def clear_all(self):
        self.end_battle()

    def _get_center(self):
        try:
            from window_capture import get_window_rect
            rect = get_window_rect(self._wi.hwnd)
            if rect:
                left, top, right, bottom = rect
                return ((right - left) // 2, (bottom - top) // 2)
        except Exception:
            pass
        return None
