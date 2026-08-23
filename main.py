import ctypes
import logging
import os
import shutil
import sys
import threading
from datetime import datetime
from enum import Enum

import pyautogui
import tkinter as tk
from tkinter import ttk
from pynput import keyboard
from PIL import Image

import applog
from applog import get_logger
from option import Option
from opencv import cv_find_template
from window_capture import capture, list_window_titles

log = get_logger("main")


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_path, relative_path))


def exe_dir():
    if hasattr(sys, "_MEIPASS"):
        return os.path.dirname(os.path.abspath(sys.executable))
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
        # 注意返回值把两件事混为一谈：上面的 return False 是"已重新以管理员启动，
        # 本进程该退出"，这里的是"提权失败"。区分它们是 #1 的事；这里至少先让
        # 失败留下痕迹，否则 #1 连诊断的依据都没有。
        log.exception("以管理员身份重新启动失败")
        return False


class TkLogHandler(logging.Handler):
    """把日志送进界面的日志框。

    logging 可能从任意线程被调用（pynput 监听线程、注入线程），而 Tk 只能在主
    线程碰。root.after 是 Tkinter 里少数几个跨线程安全的调用，用它做编组。
    """

    def __init__(self, root, sink, level=logging.INFO):
        super().__init__(level)
        self._root = root
        self._sink = sink

    def emit(self, record):
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        if record.name.endswith(".main"):
            line = f"[{ts}] {record.getMessage()}"
        else:
            # 下层模块的消息标出来源和级别，免得和 App 自己的话混作一团
            source = record.name.split(".", 1)[-1]
            line = f"[{ts}] {record.levelname} {source}: {record.getMessage()}"
        if record.exc_info:
            # 堆栈留在日志文件里就够了，塞进这个小框只会把别的信息挤没
            line += "（堆栈见日志文件）"
        try:
            self._root.after(0, self._sink, line)
        except Exception:
            # 窗口已销毁。丢掉即可 —— 文件 handler 那边照样记着。
            pass


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
        self._target_window = tk.StringVar(value="")
        self._input_mode = tk.StringVar(value="fallback")
        self._is_enabling_inject = False
        self._hotkey_error_logged = False

        self._build_ui()
        # 日志框建好之后才能接 handler；在此之前的消息只进文件。
        applog.add_handler(TkLogHandler(self.root, self._append_log))
        log_path = applog.log_path()
        if log_path:
            self.log(f"日志文件: {log_path}")
        else:
            self.log("警告: 找不到可写目录，本次运行不会留下日志文件")

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

        window_frame = tk.Frame(self.root)
        window_frame.pack(fill=tk.X, padx=10, pady=2)

        tk.Label(window_frame, text="目标窗口:").pack(side=tk.LEFT)
        self._window_combo = ttk.Combobox(
            window_frame, textvariable=self._target_window,
            width=30, state="normal"
        )
        self._window_combo.pack(side=tk.LEFT, padx=5)
        self._window_combo.bind("<<ComboboxSelected>>", self._on_window_selected)
        tk.Button(window_frame, text="刷新", width=6,
                  command=self._refresh_window_list).pack(side=tk.LEFT, padx=2)
        tk.Label(window_frame, text="(留空为全屏)", fg="gray").pack(side=tk.LEFT)
        self._refresh_window_list()

        mode_frame = tk.Frame(self.root)
        mode_frame.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(mode_frame, text="输入模式:").pack(side=tk.LEFT)
        tk.Radiobutton(
            mode_frame, text="兼容模式（抢焦点）",
            variable=self._input_mode, value="fallback",
            command=lambda: self._apply_input_mode(log_on_switch=True)
        ).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(
            mode_frame, text="注入模式（后台）",
            variable=self._input_mode, value="inject",
            command=lambda: self._apply_input_mode(log_on_switch=True)
        ).pack(side=tk.LEFT, padx=5)

        log_frame = tk.Frame(self.root)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        log_scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _refresh_window_list(self):
        titles = [""] + list_window_titles()
        self._window_combo["values"] = titles

    def _on_window_selected(self, event=None):
        target = self._target_window.get().strip() or None
        if target:
            self._option.set_target(target)
            if self._option.is_ready():
                self._apply_input_mode(log_on_switch=True)
                mode_label = "兼容模式（抢焦点）" if self._input_mode.get() == "fallback" else "注入模式（后台）"
                self.log(f"已绑定目标窗口: {target}（{mode_label}）")
            else:
                self.log(f"警告: 找不到窗口「{target}」")
        else:
            self.log("未指定目标窗口，使用全屏模式")

    def _apply_input_mode(self, log_on_switch=False):
        mode = self._input_mode.get()
        if mode == "inject":
            if self._is_enabling_inject:
                self.log("注入模式正在启用中，请稍候...")
                return
            self._is_enabling_inject = True
            self.log("正在启用注入模式，请稍候...")

            import queue
            log_queue = queue.Queue()

            def _drain_queue():
                try:
                    while True:
                        item = log_queue.get_nowait()
                        if item is None:
                            return
                        kind, payload = item
                        if kind == "log":
                            self.log(payload)
                        elif kind == "done":
                            if payload:
                                self._on_inject_enabled(log_on_switch)
                            else:
                                pass
                            return
                        elif kind == "error":
                            self._on_inject_failed(payload)
                            return
                except queue.Empty:
                    pass
                if self._is_enabling_inject:
                    self.root.after(100, _drain_queue)

            self.root.after(100, _drain_queue)

            def _enable_thread():
                try:
                    log_queue.put(("log", "[线程] 注入线程已启动"))

                    def _progress(msg):
                        log_queue.put(("log", f"  → {msg}"))

                    log_queue.put(("log", "[线程] 正在调用 enable_inject_mode..."))
                    result = self._option.enable_inject_mode(progress_cb=_progress)
                    log_queue.put(("log", f"[线程] enable_inject_mode 返回: {result}"))
                    log_queue.put(("done", True))
                except Exception as e:
                    import traceback as tb
                    err_detail = f"{e}\n{tb.format_exc()}"
                    log_queue.put(("error", err_detail))
                except BaseException as e:
                    err_detail = f"致命错误: {e}"
                    log_queue.put(("error", err_detail))

            t = threading.Thread(target=_enable_thread, daemon=True)
            t.start()

            def _watchdog():
                if self._is_enabling_inject and t.is_alive():
                    self._is_enabling_inject = False
                    self.log("启用注入模式超时（超过15秒），已取消")
                    self._input_mode.set("fallback")
                    self._option.set_fallback_mode()
                    log_queue.put(None)

            self.root.after(15000, _watchdog)
        else:
            self._option.disable_inject_mode()
            self._option.set_fallback_mode()
            self._is_enabling_inject = False
            if log_on_switch:
                self.log("已切换到兼容模式（抢焦点）")

    def _on_inject_enabled(self, log_on_switch):
        self._is_enabling_inject = False
        if log_on_switch:
            self.log("已切换到注入模式（后台）")

    def _on_inject_failed(self, error):
        self._is_enabling_inject = False
        self.log(f"启用注入模式失败: {error}，回退到兼容模式")
        self._input_mode.set("fallback")
        self._option.set_fallback_mode()

    def _init_template_dir(self):
        external_dir = os.path.join(exe_dir(), "template")
        try:
            os.makedirs(external_dir, exist_ok=True)
        except OSError as e:
            self.log(f"创建 template 目录失败: {e}")
            return
        copied = 0
        for filename in TEMPLATE_FILES:
            external_path = os.path.join(external_dir, filename)
            if not os.path.exists(external_path):
                internal_path = resource_path(f"template/{filename}")
                if os.path.exists(internal_path):
                    try:
                        shutil.copy2(internal_path, external_path)
                        copied += 1
                    except OSError as e:
                        self.log(f"复制模板 {filename} 失败: {e}")
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
        """界面日志。

        走 logging 而不是直接写控件，这样同一条消息也会落进日志文件 —— 挂机时
        没人盯着窗口，事后能翻的只有文件。这里的 log 是模块级 logger，不是本方法。
        """
        log.info(message)

    def _append_log(self, line):
        """真正写控件的地方，只由 TkLogHandler 在主线程调用。"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
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
        # 这个回调跑在 pynput 的监听线程上，任何逸出的异常都会让 Listener 直接停掉
        # —— 连 F1/F2 一起失效。原先这里只兜 AttributeError，而 F3 抛的是
        # TypeError，于是按一次 F3 整套热键就全废了（#13）。一律兜住。
        #
        # 所有动作都经 root.after 交回主线程：Tk 不是线程安全的。F3/F4 原本是直接
        # 在监听线程上调的，这个隐患随它们一并消失。
        try:
            if key == keyboard.Key.f1:
                self.root.after(0, self._on_f1)
            elif key == keyboard.Key.f2:
                self.root.after(0, self._on_f2)
        except Exception:
            # 全局监听会收到用户在任何窗口里的每一次按键。持续失败会把日志刷满，
            # 所以第一次记 ERROR，之后降级到 DEBUG。
            if not self._hotkey_error_logged:
                self._hotkey_error_logged = True
                log.exception("热键处理失败（监听器继续运行）")
            else:
                log.debug("热键处理再次失败", exc_info=True)

    def _on_f1(self):
        if self.job_timer_id is None:
            self._loop_count = 0
            self._has_battle = False
            target = self._target_window.get().strip() or None
            if target:
                self._option.set_target(target)
                if not self._option.has_window():
                    self.log(f"警告: 找不到目标窗口「{target}」，按键可能无效")
                else:
                    self._apply_input_mode(log_on_switch=True)
                    if self._input_mode.get() == "inject" and not self._option.is_ready():
                        self.log("注入未就绪，正在等待 DLL 连接...")
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
        target = self._target_window.get().strip() or None
        if target:
            self._option.set_target(target)
        self.screen = capture(target)
        if self.screen is None and target is not None:
            self.log(f"找不到窗口: {target}")
            return
        self.root.after(10, self._analyze_page)

    def _analyze_page(self):
        self.page_name = self._get_current_page_name()
        self.log(f"当前页面: {self.page_name}")
        if self.page_name == PAGE_NAME.BATTLE:
            self._option.start_battle()
            return
        else:
            self._option.end_battle()

        if self.page_name == PAGE_NAME.REWARD_EXIT:
            self._option.switch_again()
        elif self.page_name == PAGE_NAME.REWARD_AGAIN:
            self._option.tap_enter()
        else:
            self._option.tap_enter()

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
    # 必须在 run_as_admin() 之前 —— 提权失败是启动期最早、也最需要留痕的失败。
    applog.setup(exe_dir())
    log.info("=== GBFR Auto 启动 ===")
    if not run_as_admin():
        sys.exit(0)
    root = tk.Tk()
    app = App(root)
    root.mainloop()
