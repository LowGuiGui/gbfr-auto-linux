import tkinter as tk
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController
from pynput.keyboard import Key
from pynput.mouse import Button
import time
class Option:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._is_battle_ing = False
        self._kc = KeyboardController()
        self._mc = MouseController()

    # 按住W和中键开始战斗
    def start_battle(self):
        if self._is_battle_ing:
            return
        self._is_battle_ing = True
        self._kc.press("w")
        self._mc.press(Button.middle)

    # 松开W和中键结束战斗
    def end_battle(self):
        if not self._is_battle_ing:
            return
        self._is_battle_ing = False
        self._kc.release("w")
        self._mc.release(Button.middle)

    # 切换为再次挑战
    def switch_again(self):
        self._kc.press("3")
        time.sleep(0.5)
        self._kc.release("3")

    # 点击确认
    def click_enter(self):
        self._kc.press(Key.enter)
        time.sleep(0.5)
        self._kc.release(Key.enter)

    # 清除所有操作
    def clear_all(self):
        self.end_battle()