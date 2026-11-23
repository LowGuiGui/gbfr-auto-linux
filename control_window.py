#!/usr/bin/python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The control window: run count, Start, finish after this run, stop now, and the
session's progress, in an ordinary GTK 4 / libadwaita window.

It runs on the system Python with the distribution's PyGObject and never loads
MaaFramework itself: each session is one runner child process (maa_runner.py), driven
over the line protocol in maa_protocol.py. If this window closes or crashes, the runner
sees its stdin close and stops the session; if the runner dies, the window says so and
sends a key-up for every key the pipeline uses.
"""

import signal
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))  # started with -I, which leaves the script's folder out

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

import control_view as cv  # noqa: E402
import maa_protocol  # noqa: E402


WINDOW_MENU_HINT = "请在窗口的标题栏上点右键，在弹出的菜单里选“置顶”。"


class RunnerProcess:
    """One runner child: JSON lines in and out, its stderr in a log file."""

    def __init__(self, argv, on_event, on_exit, stderr_path):
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE
                                              | Gio.SubprocessFlags.STDOUT_PIPE)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        launcher.set_stderr_file_path(str(stderr_path))
        self.process = launcher.spawnv(argv)
        self.on_event, self.on_exit = on_event, on_exit
        self.stdin = self.process.get_stdin_pipe()
        self.reader = Gio.DataInputStream.new(self.process.get_stdout_pipe())
        self.saw_ended = False
        self.forced = False
        self._read()

    def _read(self):
        self.reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line)

    def _line(self, stream, result):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:  # stdout closed: the runner is gone or going
            self.process.wait_async(None, self._exited)
            return
        try:
            event = maa_protocol.decode_event(line)
        except (maa_protocol.ProtocolError, ValueError):
            event = None
        if event is not None:
            if event["event"] == "ended":
                self.saw_ended = True
            self.on_event(event)
        self._read()

    def _exited(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        code = process.get_exit_status() if process.get_if_exited() else None
        self.on_exit(self, code)

    def send(self, command):
        try:
            self.stdin.write_all(maa_protocol.encode_command(command).encode(), None)
            return True
        except GLib.Error:
            return False

    def close_stdin(self):
        try:
            self.stdin.close(None)
        except GLib.Error:
            pass

    def force_end(self):
        """Only the runner, never the game: SIGTERM, then SIGKILL after 3 s."""
        self.forced = True
        self.process.send_signal(signal.SIGTERM)
        GLib.timeout_add_seconds(3, self._kill_if_alive)

    def _kill_if_alive(self):
        if self.process.get_identifier() is not None:
            self.process.force_exit()
        return False


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        self.settings_path = cv.config_dir() / "settings.json"
        self.settings = cv.load_settings(self.settings_path)
        size = self.settings["window"]
        super().__init__(application=app, title=cv.APP_NAME, icon_name=cv.APP_ID,
                         default_width=size["width"], default_height=size["height"])
        self.app = app
        self.runner = None
        self.state = {"status": {}, "running": False, "stopping": False, "ready": True,
                      "closing": False, "log": [], "ended_at": None}
        self.deadline = None
        self.items = {}
        self._build()
        self._load_history()
        self.refresh()
        self.connect("close-request", self._on_close_request)
        GLib.timeout_add_seconds(1, self._tick)

    # --- layout ---------------------------------------------------------------------------

    def _label(self, css=(), wrap=True, selectable=False):
        return Gtk.Label(xalign=0, wrap=wrap, selectable=selectable, css_classes=list(css))

    def _build(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("显示的信息…", "app.preferences")
        menu.append("置顶…", "app.window-menu")
        menu.append("打开日志文件夹", "app.open-logs")
        menu.append(f"关于 {cv.APP_NAME}", "app.about")
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                          tooltip_text="菜单")
        header.pack_end(self.menu_button)
        # 置顶… needs the click that chose it (see show_window_menu): remember the menu's
        # clicks, only those made since it opened.
        self._menu_click = None
        popover = self.menu_button.get_popover()
        clicks = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        clicks.connect("event", self._remember_menu_click)
        popover.add_controller(clicks)
        popover.connect("show", lambda *_: setattr(self, "_menu_click", None))
        view.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=14,
                      margin_bottom=18, margin_start=16, margin_end=16)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card"])
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=14,
                        margin_bottom=14, margin_start=16, margin_end=16)
        card.append(inner)
        self.quest = self._label(["heading"])
        self.count = self._label(["title-1"], wrap=False)
        progress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        progress.append(self.quest)
        progress.append(self.count)
        self.phase = self._label()
        self.elapsed = self._label(["dim-label"])
        self.average = self._label(["dim-label"])
        self.failures = self._label(["dim-label"])
        self.message = self._label(selectable=True)
        self.detail = self._label(["caption", "dim-label"], selectable=True)
        self.log = self._label(["caption", "monospace"], selectable=True)
        for key, widget in (("progress", progress), ("phase", self.phase),
                            ("elapsed", self.elapsed), ("average", self.average),
                            ("failures", self.failures), ("message", self.message),
                            ("log", self.log)):
            inner.append(widget)
            self.items[key] = widget
        inner.append(self.detail)
        box.append(card)

        group = Adw.PreferencesGroup(title="这次要刷几次")
        self.runs = Adw.SpinRow.new_with_range(cv.RUNS_MIN, cv.RUNS_MAX, 1)
        self.runs.set_title("成功次数")
        self.runs.set_value(self.settings["runs"])
        self.until = Adw.SwitchRow(title="一直刷，直到我停止", active=self.settings["until_stopped"])
        self.until.connect("notify::active", lambda *_: self.refresh())
        group.add(self.runs)
        group.add(self.until)
        box.append(group)

        buttons = Gtk.Box(spacing=8, homogeneous=True)
        self.start_button = Gtk.Button(label="开始", css_classes=["suggested-action", "pill"])
        self.finish_button = Gtk.Button(label="本轮后停", css_classes=["pill"])
        self.stop_button = Gtk.Button(label="立即停止", css_classes=["destructive-action", "pill"])
        self.start_button.connect("clicked", lambda *_: self.start())
        self.finish_button.connect("clicked", lambda *_: self.finish())
        self.stop_button.connect("clicked", lambda *_: self.stop())
        for button in (self.start_button, self.finish_button, self.stop_button):
            buttons.append(button)
        box.append(buttons)
        self.notice = self._label(["caption"])
        self.notice.set_visible(False)  # shown only while it has something to say
        self.notice.connect("notify::label", lambda label, _: label.set_visible(bool(label.get_label())))
        box.append(self.notice)
        hint = self._label(["caption", "dim-label"])
        hint.set_label("先在游戏的任务柜台打开要刷的任务，停在任务详情页、匹配设置或准备画面，再点开始。"
                       "“本轮后停”会在结算画面关掉再次挑战，等游戏回到城镇；"
                       "“立即停止”只停止软件的按键，游戏本身不会暂停。")
        box.append(hint)

        self.history = Adw.PreferencesGroup(title="最近几次会话")
        self.history_rows = []
        box.append(self.history)
        self.items["history"] = self.history

        self.logs_button = Gtk.Button(label="打开日志文件夹", css_classes=["flat"],
                                      halign=Gtk.Align.START)
        self.logs_button.connect("clicked", lambda *_: self.open_logs())
        box.append(self.logs_button)
        self.items["logs_button"] = self.logs_button

        clamp = Adw.Clamp(maximum_size=560, child=box)
        view.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_content(view)
        # On showing, GTK focuses the first focusable widget: the run count, with "10"
        # selected. Hold the focus on the menu button instead (Enter there only opens the
        # menu, never Start) and let go of it once shown (drop_initial_focus).
        self.set_focus(self.menu_button)

    def drop_initial_focus(self):
        focus = self.get_focus()
        if focus is not None and (focus is self.menu_button or focus.is_ancestor(self.menu_button)):
            self.set_focus(None)
        return False

    def _remember_menu_click(self, _controller, event):
        if event.get_event_type() in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE,
                                      Gdk.EventType.TOUCH_BEGIN, Gdk.EventType.TOUCH_END):
            self._menu_click = event
        return False

    def show_window_menu(self):
        """置顶…: ask GNOME for its own window menu, the one a right click on the title bar
        opens, where 置顶 lives. GTK 4 has no call to keep a window on top and Mutter offers
        programs no protocol for it. The window menu needs the click that asked for it, so
        the menu's last click is passed on once the menu has closed and the pointer is
        back over this window."""
        event = self._menu_click
        if event is None:  # chosen with the keyboard
            self.notice.set_label(WINDOW_MENU_HINT)
            return
        GLib.timeout_add(150, self._ask_for_window_menu, event)

    def _ask_for_window_menu(self, event):
        surface = self.get_surface()
        if surface is None or not surface.show_window_menu(event):
            self.notice.set_label(WINDOW_MENU_HINT)
        return False

    # --- state ----------------------------------------------------------------------------

    def refresh(self):
        state = dict(self.state, now=time.time())
        texts = cv.view(state)
        self.quest.set_label(texts["quest"])
        self.count.set_label(texts["count"])
        self.phase.set_label(texts["phase"])
        self.elapsed.set_label(texts["elapsed"])
        self.average.set_label(texts["average"])
        self.failures.set_label(texts["failures"])
        self.message.set_label(texts["message"])
        self.detail.set_label(f"技术原因：{texts['detail']}" if texts["detail"] else "")
        self.detail.set_visible(bool(texts["detail"]) and self.settings["show"]["message"])
        self.log.set_label(texts["log"] or "（还没有日志）")
        allowed = cv.controls(state)
        self.start_button.set_sensitive(allowed["start"])
        self.finish_button.set_sensitive(allowed["finish"])
        self.stop_button.set_sensitive(allowed["stop"])
        self.runs.set_sensitive(allowed["settings"] and not self.until.get_active())
        self.until.set_sensitive(allowed["settings"])
        for key, widget in self.items.items():
            widget.set_visible(self.settings["show"].get(key, True))
        self.message.set_visible(self.settings["show"]["message"] and bool(texts["message"]))

    def _tick(self):
        if self.state["running"]:
            self.refresh()
        return True

    def _load_history(self):
        for row in self.history_rows:
            self.history.remove(row)
        self.history_rows = []
        rows = cv.history_rows(cv.read_history(cv.state_dir() / "history.jsonl", cv.HISTORY_ROWS))
        for title, subtitle in rows or [("还没有记录", "每次会话结束后在这里加一行。")]:
            row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                subtitle=GLib.markup_escape_text(subtitle))
            self.history.add(row)
            self.history_rows.append(row)

    def save_settings(self):
        self.settings["runs"] = int(self.runs.get_value())
        self.settings["until_stopped"] = self.until.get_active()
        width, height = self.get_default_size()
        if width > 0 and height > 0:
            self.settings["window"] = {"width": width, "height": height}
        try:
            cv.save_settings(self.settings_path, self.settings)
        except OSError as exc:
            self.notice.set_label(f"设置没能保存：{exc}")

    # --- commands --------------------------------------------------------------------------

    def start(self):
        if self.runner is not None:
            return
        self.notice.set_label("")
        self.save_settings()  # may leave a notice that the settings were not saved
        self.state.update(status={}, running=True, stopping=False, finish_sent=False, log=[],
                          ended_at=None,
                          requested=None if self.settings["until_stopped"] else self.settings["runs"])
        try:
            argv = cv.runner_command(APP_DIR, self.settings["runs"], self.settings["until_stopped"])
            self.runner = RunnerProcess(argv, self._on_event, self._on_exit,
                                        cv.state_dir() / "window-runner.log")
        except (GLib.Error, OSError, ValueError) as exc:
            # Nothing was started (no state folder, no runner program): back to idle.
            self.runner = None
            self.state["running"] = False
            self.notice.set_label(f"后台程序没能启动：{getattr(exc, 'message', None) or exc}")
        self.refresh()

    def finish(self):
        if self.runner is not None and self.runner.send("finish"):
            self.state["finish_sent"] = True  # off at once; the runner's status confirms it
            self.notice.set_label("已请求本轮后停止，正在等待当前一轮结算。")
        self.refresh()

    def stop(self):
        if self.runner is None:
            return
        self.runner.send("stop")
        self.state["stopping"] = True
        self.notice.set_label("已请求立即停止操作，正在等待软件停下。游戏本身不会暂停。")
        self._arm_deadline()
        self.refresh()

    def _arm_deadline(self):
        if self.deadline is None:
            self.deadline = GLib.timeout_add_seconds(cv.STOP_DEADLINE_S, self._on_deadline)

    def _clear_deadline(self):
        if self.deadline is not None:
            GLib.source_remove(self.deadline)
            self.deadline = None

    def _on_deadline(self):
        self.deadline = None
        if self.runner is None:
            return False
        dialog = Adw.AlertDialog(heading="后台没有按时停下",
                                 body=f"请求停止 {cv.STOP_DEADLINE_S} 秒后，后台程序还没有退出。"
                                      "可以继续等，也可以结束后台程序（只结束本软件的后台，"
                                      "不碰游戏），然后对用到的按键各发一次松开。")
        dialog.add_response("wait", "继续等")
        dialog.add_response("end", "结束后台")
        dialog.set_response_appearance("end", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_deadline_response)
        dialog.present(self)
        return False

    def _on_deadline_response(self, dialog, response):
        if response == "end" and self.runner is not None:
            self.notice.set_label("正在结束后台程序……")
            self.runner.force_end()
        elif self.runner is not None:
            self._arm_deadline()

    def open_logs(self):
        folder = cv.state_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder))).launch(self, None, None)

    # --- runner events ---------------------------------------------------------------------

    def _on_event(self, event):
        kind = event["event"]
        if kind == "ready":
            self.notice.set_label("")
        elif kind == "status":
            self.state["status"] = event["status"]
        elif kind == "log":
            self.state["log"] = (self.state["log"] + [event["text"]])[-cv.LOG_LINES:]
            if event.get("finish_rejected"):  # offer 本轮后停 again
                self.state["finish_sent"] = False
                self.notice.set_label(event["text"])
        elif kind == "ended":
            # An `error` event always comes just before this one, with the same message as
            # the final status, so the status area alone tells why; the notice is cleared.
            self.state["status"] = event.get("status") or {}
            self.state["ended_at"] = time.time()
            self.notice.set_label("")
            self._notify(event.get("outcome"), self.state["status"])
        self.refresh()

    def _notify(self, outcome, status):
        texts = cv.notification(outcome, status)
        if texts:
            notification = Gio.Notification.new(texts[0])
            notification.set_body(texts[1])
            self.app.send_notification("session-end", notification)

    def _on_exit(self, runner, code):
        if runner is not self.runner:
            return
        self.runner = None
        self._clear_deadline()
        self.state.update(running=False, stopping=False)
        if self.state["ended_at"] is None:
            self.state["ended_at"] = time.time()
        if not runner.saw_ended:
            # It never said how it ended: record an error end for it, and since it may have
            # died holding a key, send a key-up for every key the pipeline uses from a fresh
            # connection (a separate process that finishes even if this window closes).
            why = "被结束了" if runner.forced else f"意外退出了（退出码 {code}）"
            status = dict(self.state["status"], phase="error", message=f"后台程序{why}。",
                          error=f"runner exited without an end event (exit code {code})")
            self.state["status"] = status
            try:
                cv.append_history(cv.state_dir() / "history.jsonl", status, "error")
            except OSError:
                pass
            self._notify("error", status)
            self._release_keys()
        self._load_history()
        self.refresh()
        if self.state["closing"]:
            self.save_settings()
            self.app.quit()

    def _release_keys(self):
        self.notice.set_label("正在对用到的按键各发一次松开……")
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE
                                                  | Gio.SubprocessFlags.STDERR_MERGE)
            process = launcher.spawnv(cv.release_command(APP_DIR))
        except (GLib.Error, ValueError) as exc:
            self.notice.set_label(f"松键程序没能启动：{getattr(exc, 'message', exc)}。"
                                  "请看一眼游戏里有没有键还按着。")
            return
        self.state["releasing"] = True
        process.communicate_utf8_async(None, None, self._released)

    def _released(self, process, result):
        try:
            _, output, _ = process.communicate_utf8_finish(result)
        except GLib.Error:
            output = ""
        self.state["releasing"] = False
        lines = [line for line in (output or "").splitlines() if line.strip()]
        self.notice.set_label((lines[-1] if lines else "松键程序已结束。") + "请看一眼游戏画面。")
        self.refresh()

    # --- closing ---------------------------------------------------------------------------

    def _on_close_request(self, *_):
        if self.runner is None:
            self.save_settings()
            return False
        if self.state["closing"]:
            return True
        dialog = Adw.AlertDialog(heading="正在刷本",
                                 body="关闭窗口会立即停止软件的按键，游戏本身不会暂停。"
                                      "如果“再次挑战”开着，游戏会自己继续刷，最多到下一次第 10 次的确认。")
        dialog.add_response("cancel", "取消")
        dialog.add_response("close", "停止并关闭")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_close_response)
        dialog.present(self)
        return True

    def _on_close_response(self, dialog, response):
        if response != "close" or self.runner is None:
            return
        self.state["closing"] = True
        self.stop()
        self.runner.close_stdin()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=cv.APP_ID)
        self.window = None
        for name, handler in (("preferences", self._preferences), ("open-logs", self._open_logs),
                              ("window-menu", self._window_menu), ("about", self._about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def do_activate(self):
        first = self.window is None
        if first:
            self.window = MainWindow(self)
        self.window.present()
        if first:  # before GTK draws the first frame, so no focus ring ever shows
            GLib.idle_add(self.window.drop_initial_focus, priority=GLib.PRIORITY_HIGH_IDLE)

    def _preferences(self, *_):
        window = self.window
        dialog = Adw.PreferencesDialog(title="显示的信息")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="窗口里显示哪些信息",
                                     description="控制按钮（次数、开始、本轮后停、立即停止）始终显示。")
        for key, label, _ in cv.INFO_ITEMS:
            row = Adw.SwitchRow(title=label, active=window.settings["show"][key])
            row.connect("notify::active", self._toggle, key)
            group.add(row)
        page.add(group)
        dialog.add(page)
        dialog.present(window)

    def _toggle(self, row, _param, key):
        self.window.settings["show"][key] = row.get_active()
        self.window.save_settings()
        self.window.refresh()

    def _open_logs(self, *_):
        self.window.open_logs()

    def _window_menu(self, *_):
        self.window.show_window_menu()

    def _about(self, *_):
        dialog = Adw.AboutDialog(application_name=cv.APP_NAME, application_icon=cv.APP_ID,
                                 version=cv.app_version(APP_DIR),
                                 license_type=Gtk.License.GPL_2_0,
                                 website="https://github.com/LowGuiGui/gbfr-auto-linux",
                                 comments="用游戏自带的“再次挑战”连刷素材，前台挂机观看。")
        dialog.present(self.window)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return App().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
