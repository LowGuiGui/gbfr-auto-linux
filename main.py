import ctypes
import os
import shutil
import sys
import threading
from datetime import datetime
from enum import Enum

import pyautogui
import tkinter as tk
from pynput import keyboard
from PIL import Image

from option import Option
from opencv import cv_find_template


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)


def exe_dir():
    if hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


TEMPLATE_FILES = [
    "flag_battle.png",
    "flag_battleresult.png",
    "flag_again.png",
    "flag_exit.png",
    "flag_continue.png",
]


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except AttributeError:
        return False


def run_as_admin():
    if is_admin():
        return True
    try:
        if hasattr(sys, "_MEIPASS"):
            exe_path = sys.executable
        else:
            exe_path = sys.argv[0]
        params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", f'"{exe_path}"', params, None, 1
        )
        return False
    except Exception:
        return False


class PAGE_NAME(Enum):
    UNKNOWN = "unknown"
    BATTLE = "battle"
    SCORE = "score"
    REWARD_EXIT = "reward_exit"
    REWARD_AGAIN = "reward_again"
    PAUSE = "pause"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("GBFR Auto")
        self.root.geometry("500x300")
        self.root.iconbitmap(resource_path("icon.ico"))

        self.page_name: PAGE_NAME | None = None
        self.screen: Image = None
        self.temp_dir: dict[str, str] = {}
        self.job_timer_id = None
        self.overlay_window = None
        self._option = Option(root)

        self._has_battle = False
        self._loop_count = 0

        self._build_ui()
        self._init_template_dir()
        self._load_temp_data()
        self._start_listener()

    def _build_ui(self):
        key_tips = tk.Label(
            self.root,
            text="按F1启动自动循环，按F2停止自动循环",
            font=("Arial", 12)
        )
        key_tips.pack(pady=5)

        log_frame = tk.Frame(self.root)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        log_scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _init_template_dir(self):
        external_dir = os.path.join(exe_dir(), "template")
        os.makedirs(external_dir, exist_ok=True)
        copied = 0
        for filename in TEMPLATE_FILES:
            external_path = os.path.join(external_dir, filename)
            if not os.path.exists(external_path):
                internal_path = resource_path(f"template/{filename}")
                if os.path.exists(internal_path):
                    shutil.copy2(internal_path, external_path)
                    copied += 1
        if copied > 0:
            self.log(f"已生成 {copied} 个模板文件到 template 目录")

    def _load_temp_data(self):
        external_dir = os.path.join(exe_dir(), "template")
        for filename in TEMPLATE_FILES:
            key = filename.replace(".png", "")
            external_path = os.path.join(external_dir, filename)
            if os.path.exists(external_path):
                self.temp_dir[key] = external_path
            else:
                self.temp_dir[key] = resource_path(f"template/{filename}")

    def _start_listener(self):
        def run():
            listener = keyboard.Listener(on_press=self._on_press)
            listener.start()
            listener.join()

        listener_thread = threading.Thread(target=run, daemon=True)
        listener_thread.start()

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def show_overlay(self, text):
        if self.overlay_window is not None:
            self.overlay_window.destroy()

        self.overlay_window = tk.Toplevel(self.root)
        self.overlay_window.overrideredirect(True)
        self.overlay_window.attributes("-topmost", True)
        self.overlay_window.attributes("-alpha", 0.85)
        self.overlay_window.geometry("+0+0")

        label = tk.Label(
            self.overlay_window,
            text=text,
            font=("Arial", 20, "bold"),
            fg="lime",
            bg="black",
            padx=15,
            pady=8
        )
        label.pack()

    def hide_overlay(self):
        if self.overlay_window is not None:
            self.overlay_window.destroy()
            self.overlay_window = None

    def _on_press(self, key):
        try:
            if key == keyboard.Key.f1:
                # self.log("F1 按下")
                self.root.after(0, self._on_f1)
            elif key == keyboard.Key.f2:
                # self.log("F2 按下")
                self.root.after(0, self._on_f2)
        except AttributeError:
            pass

    def _on_f1(self):
        if self.job_timer_id is None:
            self._loop_count = 0
            self._has_battle = False
            self.log("启动自动循环")
            self.show_overlay("● 自动循环已启动")
            self._schedule_job_loop()

    def _on_f2(self):
        if self.job_timer_id is not None:
            self.root.after_cancel(self.job_timer_id)
            self.job_timer_id = None
            self.log("停止自动循环")
            self.log(f"共完成 {self._loop_count} 次战斗")
            self.hide_overlay()
            self._option.clear_all()

    def _schedule_job_loop(self):
        self.job_loop()
        self.job_timer_id = self.root.after(3000, self._schedule_job_loop)

    def job_loop(self):
        self.screen = pyautogui.screenshot()
        self.root.after(10, self._analyze_page)

    def _analyze_page(self):
        self.page_name = self._get_current_page_name()

        if self.page_name == PAGE_NAME.BATTLE:
            self._option.start_battle()
            return
        else:
            self._option.end_battle()

        if self.page_name == PAGE_NAME.REWARD_EXIT:
            self._option.switch_again()
        elif self.page_name == PAGE_NAME.REWARD_AGAIN:
            self._option.click_enter()
        else:
            self._option.click_enter()

    def _get_current_page_name(self) -> PAGE_NAME:
        if self.screen is None:
            return PAGE_NAME.UNKNOWN
        if cv_find_template(self.screen, self.temp_dir["flag_battle"]) is not None:
            self._has_battle = True
            return PAGE_NAME.BATTLE
        elif cv_find_template(self.screen, self.temp_dir["flag_battleresult"]) is not None:
            if self._has_battle:
                self._loop_count += 1
                self._has_battle = False
                self.log(f"完成第 {self._loop_count} 次战斗")
            if cv_find_template(self.screen, self.temp_dir["flag_again"]) is not None:
                return PAGE_NAME.REWARD_AGAIN
            elif cv_find_template(self.screen, self.temp_dir["flag_exit"]) is not None:
                return PAGE_NAME.REWARD_EXIT
            else:
                return PAGE_NAME.SCORE
        elif cv_find_template(self.screen, self.temp_dir["flag_continue"]) is not None:
            return PAGE_NAME.PAUSE
        else:
            return PAGE_NAME.UNKNOWN


if __name__ == "__main__":
    if not run_as_admin():
        sys.exit(0)
    root = tk.Tk()
    app = App(root)
    root.mainloop()
