import ctypes
import hashlib
import json
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
import config as config_module
from applog import get_logger
from option import Option
from opencv import cv_best_match, is_blank_frame
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


# 记录"我们上一次写进 template/ 的内容"，用来分辨哪些文件被用户改过。
TEMPLATE_MANIFEST = ".bundled.json"


def file_sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


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


# run_as_admin() 的三种结果。原来用一个 bool 表示，而 False 同时意味着"已经用
# 管理员重开了、本进程该正常退出"和"提权失败了" —— 于是两者都 exit(0)。
ELEVATION_ALREADY = "already"        # 本来就是管理员，继续跑
ELEVATION_RELAUNCHED = "relaunched"  # 已重新启动，本进程正常退出（0 是对的）
ELEVATION_FAILED = "failed"          # 提权失败，必须非零退出

# ShellExecuteW 的返回值：> 32 才是成功，<= 32 是错误码。
# 用户点"否"的 UAC 提示返回 SE_ERR_ACCESSDENIED (5)。
SE_SUCCESS_THRESHOLD = 32
_SE_ERRORS = {
    0: "out of memory or resources",
    2: "file not found",
    3: "path not found",
    5: "access denied -- the UAC prompt was refused",
    8: "out of memory",
    26: "a sharing violation occurred",
    27: "incomplete or invalid file association",
    31: "no application associated with this file type",
    32: "the required DLL was not found",
}


def run_as_admin():
    """返回 ELEVATION_* 之一。

    原来这里**不看 ShellExecuteW 的返回值**，所以"用户在 UAC 弹窗上点了否"和
    "成功以管理员重开"是完全一样的结果：两者都 return False，调用方都 exit(0)。
    #1 说的"失败却报告成功"，根源就在这里 —— 不只是退出码写错了。
    """
    if is_admin():
        return ELEVATION_ALREADY
    try:
        if hasattr(sys, "_MEIPASS"):
            exe_path = sys.executable
        else:
            exe_path = sys.argv[0]
        params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
        # HINSTANCE 在 64 位上是指针宽度。默认 restype 是 c_int，会把高位截掉；
        # 虽然错误码都很小、截断后照样 <= 32，但没有理由把判断建在截断上。
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
        result = shell_execute(None, "runas", f'"{exe_path}"', params, None, 1)
        code = int(result or 0)
        if code > SE_SUCCESS_THRESHOLD:
            log.info("已请求以管理员身份重新启动，本进程退出")
            return ELEVATION_RELAUNCHED
        reason = _SE_ERRORS.get(code, "see ShellExecuteW return values")
        log.error("提权失败：ShellExecuteW 返回 %d (%s)", code, reason)
        return ELEVATION_FAILED
    except Exception:
        log.exception("以管理员身份重新启动失败")
        return ELEVATION_FAILED


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
        parts = [f"[{ts}]"]
        # INFO 以上一律标出级别 —— 包括 App 自己的告警，否则一条 WARNING 看起来
        # 和普通叙述一模一样，正好淹掉最该被看见的那句。
        if record.levelno > logging.INFO:
            parts.append(record.levelname)
        # 下层模块的消息标出来源，免得和 App 自己的话混作一团
        if not record.name.endswith(".main"):
            parts.append(f"{record.name.split('.', 1)[-1]}:")
        parts.append(record.getMessage())
        line = " ".join(parts)
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
    def __init__(self, root, cfg=None):
        self.cfg = cfg or config_module.Config(config_module._merged({}))
        self.root = root
        self.root.title("GBFR Auto")
        self.root.geometry("500x300")
        self.root.iconbitmap(resource_path("icon.ico"))

        self.page_name: PAGE_NAME | None = None
        self.screen: Image = None
        self.temp_dir: dict[str, str] = {}
        self.job_timer_id = None
        self.overlay_window = None
        self._option = Option(
            root,
            keys=self.cfg.section("keys"),
            dry_run=self.cfg.get("input.dry_run"),
        )
        self._anomalies_saved = 0

        self._has_battle = False
        self._loop_count = 0
        self._target_window = tk.StringVar(value="")
        self._input_mode = tk.StringVar(value=self.cfg.get("input.mode"))
        self._is_enabling_inject = False
        # 注入是异步的，而看门狗会在超时后把界面切回兼容模式 —— 但那条工作线程
        # 是 daemon 且没人能取消它。这两个编号让"迟到的成功"可以被认出来：
        #   _inject_attempt  最新一次尝试的编号，用来识别被更新尝试取代的旧线程
        #   _inject_wanted   仍然想要其结果的那一次；看门狗超时后清零
        # 见 #2。
        self._inject_attempt = 0
        self._inject_wanted = 0
        self._hotkey_error_logged = False
        self._unknown_streak = 0
        self._last_logged_page = None

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
            self._inject_attempt += 1
            attempt = self._inject_attempt
            self._inject_wanted = attempt
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
                            self._on_inject_finished(bool(payload), attempt,
                                                     log_on_switch)
                            return
                        elif kind == "error":
                            if self._inject_wanted == attempt:
                                self._on_inject_failed(payload)
                            else:
                                log.debug("注入线程在超时后才报错，忽略: %s", payload)
                            return
                except queue.Empty:
                    pass
                # 条件是"有没有被更新的尝试取代"，不是 _is_enabling_inject。
                # 看门狗超时会把后者清掉，若照旧以它为准，排空就此停住，线程晚到
                # 的 done 永远没人看见 —— 注入于是在后台悄悄成功，而界面显示兼容
                # 模式。那正是 #2。
                if self._inject_attempt == attempt:
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
                if self._inject_wanted == attempt and t.is_alive():
                    # 结果不再被需要，但**不要**停掉排空：线程还活着，它的结果
                    # 仍然可能到来，而那时我们需要把它拆掉。
                    self._inject_wanted = 0
                    self._is_enabling_inject = False
                    self.log("启用注入模式超时（超过15秒），已取消")
                    self._input_mode.set("fallback")
                    self._option.set_fallback_mode()

            self.root.after(self.cfg.get("inject.watchdog_ms"), _watchdog)
        else:
            self._option.disable_inject_mode()
            self._option.set_fallback_mode()
            self._is_enabling_inject = False
            if log_on_switch:
                self.log("已切换到兼容模式（抢焦点）")

    def _on_inject_finished(self, ok, attempt, log_on_switch):
        """注入线程出结果了 —— 可能比看门狗还晚。

        这是 #2 的关键分支。看门狗超时后界面已经切回兼容模式，可线程照样在跑；
        它要是随后成功了，钩子就是活的，而界面说的是另一回事。两条输入路径同时
        存在，且没有任何地方能看出哪条是真的。

        所以迟到的成功必须**拆掉**，而不是接受。界面是用户看到的东西，让实际
        状态去迁就它，比反过来悄悄改界面要诚实。
        """
        if self._inject_wanted == attempt:
            if ok:
                self._on_inject_enabled(log_on_switch)
            return

        if not ok:
            log.debug("注入线程在超时后报告失败，无需处理")
            return

        self.log("注入在超时之后才完成，已拆除以保持与界面一致")
        try:
            self._option.disable_inject_mode()
        except Exception:
            log.exception("拆除迟到的注入失败")
        self._option.set_fallback_mode()

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
        """把内置模板铺到 exe 旁边的 template/，并在升级时刷新没被改过的那些。

        原先只在文件不存在时复制，于是外部目录一旦生成就永久遮蔽内置版本：新版
        改了模板，跑过老版本的人永远拿不到（#3）。

        但这个目录同时是用户按自己分辨率替换模板的地方（#12），直接覆盖会毁掉他
        们的修改。所以用一份清单记下"我们上次写进去的是什么"：文件内容仍与清单
        一致 —— 也就是没被动过 —— 才刷新；动过的原样保留，把新版内置模板放成
        同名 .new 搁在旁边，用不用由人决定。
        """
        external_dir = os.path.join(exe_dir(), "template")
        try:
            os.makedirs(external_dir, exist_ok=True)
        except OSError as e:
            self.log(f"创建 template 目录失败: {e}")
            return

        manifest_path = os.path.join(external_dir, TEMPLATE_MANIFEST)
        manifest = self._read_template_manifest(manifest_path)

        copied = 0
        refreshed = []
        kept = []

        for filename in TEMPLATE_FILES:
            internal_path = resource_path(f"template/{filename}")
            external_path = os.path.join(external_dir, filename)

            bundled_hash = file_sha256(internal_path)
            if bundled_hash is None:
                log.warning("内置模板读取失败，跳过: %s", internal_path)
                continue

            if not os.path.exists(external_path):
                if self._copy_template(internal_path, external_path):
                    manifest[filename] = bundled_hash
                    copied += 1
                continue

            external_hash = file_sha256(external_path)
            if external_hash == bundled_hash:
                # 已经是最新，只把账记上（老版本升上来时补一次清单）
                manifest[filename] = bundled_hash
                continue

            if manifest.get(filename) == external_hash:
                # 内容仍是我们上次写进去的，说明用户没动过，可以安全刷新
                if self._copy_template(internal_path, external_path):
                    manifest[filename] = bundled_hash
                    refreshed.append(filename)
            else:
                # 用户改过，或者来自还没有清单的旧版本 —— 两种情况都不许覆盖
                kept.append(filename)
                self._offer_new_template(internal_path, external_path + ".new")

        if copied:
            self.log(f"已生成 {copied} 个模板文件到 template 目录")
        if refreshed:
            self.log(f"已更新 {len(refreshed)} 个未修改的模板: {'、'.join(refreshed)}")
        if kept:
            log.warning(
                "内置模板有更新，但以下文件与我们写入的版本不一致，已原样保留："
                "%s。新版已另存为同名 .new 文件，需要时自行替换。",
                "、".join(kept),
            )

        self._write_template_manifest(manifest_path, manifest)

    def _copy_template(self, internal_path, external_path):
        try:
            shutil.copy2(internal_path, external_path)
            return True
        except OSError as e:
            self.log(f"复制模板 {os.path.basename(external_path)} 失败: {e}")
            return False

    def _offer_new_template(self, internal_path, sidecar_path):
        """把新版内置模板放到用户文件旁边，不动用户的文件。"""
        if file_sha256(sidecar_path) == file_sha256(internal_path):
            return          # 已经放过同样的内容，不必重写
        try:
            shutil.copy2(internal_path, sidecar_path)
        except OSError as e:
            log.warning("写入 %s 失败: %s", sidecar_path, e)

    def _read_template_manifest(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}      # 首次运行，或从没有清单的旧版本升上来
        except (OSError, ValueError) as e:
            log.warning("模板清单读取失败，将视为全部已被修改: %s", e)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_template_manifest(self, path, manifest):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)
        except OSError as e:
            # 写不进去不致命，只是下次升级时会把所有文件当成"被改过"
            log.warning("模板清单写入失败: %s", e)

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
            self._unknown_streak = 0
            self._last_logged_page = None
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
        self.job_timer_id = self.root.after(
            self.cfg.get("loop.poll_interval_ms"), self._schedule_job_loop
        )

    def job_loop(self):
        target = self._target_window.get().strip() or None
        if target:
            self._option.set_target(target)
        self.screen = capture(target)
        if self.screen is None:
            self.log(f"截图失败: {target or '全屏'}")
            return
        if is_blank_frame(self.screen):
            # 空帧不是"认不出页面"，是截图坏了。分开报，否则日志会把人指向
            # 分辨率和模板，而真正的问题在截图后端。
            log.error(
                "截到的是空白帧（全黑或纯色），本轮跳过。"
                "游戏多半在独占全屏，或者截图后端拿不到这个 D3D 窗口。"
            )
            return
        self.root.after(10, self._analyze_page)

    def _matches(self, key):
        """某个模板是否命中。阈值来自配置，分数可选记录。

        分数是调 detect.threshold、以及之后做多尺度匹配的唯一依据。没有它，
        "它不工作"就只能靠猜。
        """
        result = cv_best_match(self.screen, self.temp_dir[key])
        if result is None:
            return False
        score = result[4]
        if self.cfg.get("detect.log_scores"):
            log.debug("匹配得分 %-20s %.4f", key, score)
        return score >= self.cfg.get("detect.threshold")

    def _save_anomaly_frame(self):
        """认不出页面时把画面存下来 —— 每个失败都变成一张可以用来修模板的样本。"""
        if not self.cfg.get("detect.save_anomaly_frames"):
            return
        limit = self.cfg.get("detect.max_anomaly_frames")
        if self._anomalies_saved >= limit:
            return
        directory = os.path.join(exe_dir(), self.cfg.get("detect.anomaly_dir"))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f"unknown-{stamp}.png")
            self.screen.save(path)
        except (OSError, ValueError, AttributeError) as e:
            log.warning("保存异常帧失败: %s", e)
            return
        self._anomalies_saved += 1
        log.info("已保存异常帧 %s (%d/%d)", path, self._anomalies_saved, limit)

    @property
    def MAX_BLIND_TAPS(self):
        """连续多少帧认不出页面就停止盲按。默认 5 帧，按 3 秒轮询约等于 15 秒。"""
        return self.cfg.get("loop.max_blind_taps")

    def _analyze_page(self):
        self.page_name = self._get_current_page_name()

        # 每帧都记进日志文件，界面上只在页面变化时提一句 —— 否则一分钟 20 行
        # 一模一样的内容，真正要紧的告警会被埋掉。
        log.debug("当前页面: %s", self.page_name.value)
        if self.page_name != self._last_logged_page:
            self._last_logged_page = self.page_name
            self.log(f"当前页面: {self.page_name.value}")

        if self.page_name != PAGE_NAME.UNKNOWN and self._unknown_streak:
            if self._unknown_streak > self.MAX_BLIND_TAPS:
                log.info("页面识别已恢复: %s", self.page_name.value)
            self._unknown_streak = 0

        if self.page_name == PAGE_NAME.BATTLE:
            self._option.start_battle()
            return
        else:
            self._option.end_battle()

        if self.page_name == PAGE_NAME.UNKNOWN:
            self._advance_unknown_page()
        elif self.page_name == PAGE_NAME.REWARD_EXIT:
            self._option.switch_again()
        else:
            # REWARD_AGAIN / SCORE / PAUSE 都是认出来的页面，按键推进是有依据的
            self._option.tap_confirm()

    def _advance_unknown_page(self):
        """认不出页面时推进流程，但不允许无限期盲按。

        在 UNKNOWN 上按键是上游有意为之，用来推掉没有建模的对话框（README 写作
        "按 Enter 推进流程"），所以保留。出事的是检测整体失效的时候 —— 分辨率与
        模板不匹配，或者模板根本读不到 —— 那时每一帧都是 UNKNOWN，机器人就对着
        游戏一直敲键，界面上还一个字都不说。这里给它一个上限。
        """
        self._unknown_streak += 1
        self._save_anomaly_frame()

        if self._unknown_streak <= self.MAX_BLIND_TAPS:
            self._option.tap_confirm()
            return

        # 只在越过阈值的那一帧告警一次，之后安静地什么都不做
        if self._unknown_streak == self.MAX_BLIND_TAPS + 1:
            log.warning(
                "连续 %d 帧无法识别页面，已停止向游戏发送按键。"
                "常见原因：游戏分辨率与模板图片不匹配，或模板文件读取失败"
                "（若是后者，上方会有 opencv 的报错）。",
                self.MAX_BLIND_TAPS,
            )

    def _get_current_page_name(self) -> PAGE_NAME:
        if self.screen is None:
            return PAGE_NAME.UNKNOWN
        # 判定优先级就是这里的书写顺序：flag_battle 先于 flag_battleresult，仅仅
        # 因为它写在前面。这条规则是承重的 —— 调换顺序会改变行为（#16）。
        if self._matches("flag_battle"):
            self._has_battle = True
            return PAGE_NAME.BATTLE
        elif self._matches("flag_battleresult"):
            if self._has_battle:
                self._loop_count += 1
                self._has_battle = False
                self.log(f"完成第 {self._loop_count} 次战斗")
            if self._matches("flag_again"):
                return PAGE_NAME.REWARD_AGAIN
            elif self._matches("flag_exit"):
                return PAGE_NAME.REWARD_EXIT
            else:
                return PAGE_NAME.SCORE
        elif self._matches("flag_continue"):
            return PAGE_NAME.PAUSE
        else:
            return PAGE_NAME.UNKNOWN


if __name__ == "__main__":
    # 必须在 run_as_admin() 之前 —— 提权失败是启动期最早、也最需要留痕的失败。
    applog.setup(exe_dir())
    log.info("=== GBFR Auto 启动 ===")
    # 配置要在 setup() 之后读 —— 读配置的过程本身就会记日志。
    _cfg = config_module.load(exe_dir())
    applog.set_level(_cfg.get("log.level"))
    _elevation = run_as_admin()
    if _elevation == ELEVATION_RELAUNCHED:
        # 这一条退 0 是对的：活儿交给新起的管理员进程了，本进程正常收场。
        sys.exit(0)
    if _elevation == ELEVATION_FAILED:
        # 而这一条必须非零。包装它的启动器、批处理或 CI 步骤，靠的就是退出码来
        # 区分"跑完了"和"根本没起来"。
        log.error("=== GBFR Auto 未能以管理员身份启动，退出 ===")
        sys.exit(1)
    root = tk.Tk()
    app = App(root, cfg=_cfg)
    root.mainloop()
