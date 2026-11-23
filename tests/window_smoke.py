#!/usr/bin/python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Smoke test of the control window against the scripted game. Local only: it needs a
display, GTK 4 and libadwaita, so CI does not run it.

Run it with the system Python inside a private session (its own D-Bus, HOME and XDG
folders), never on a desktop someone is using: it opens the window and clicks through it
by itself, starts several runner processes and ends with the window closed.

    /usr/bin/python3 -I tests/window_smoke.py --out DIR --runner-python .venv/bin/python

The window is the real one (control_window.py), driven in-process: buttons are pressed by
emitting their `clicked` signals and dialogs are answered through their own buttons. Each
session's runner is tests/runner_scripted.py, which the window starts through
GBFR_RUNNER_COMMAND, so the protocol, the child process and its end are all real. The
runner needs MaaFw and the OCR model (GBFR_MAA_OCR_MODEL). Writes report.json with every
check and a PNG of the window after each step; the exit code is 0 only if every check
passed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import control_window as cw  # noqa: E402
from control_window import Adw, GLib, Gtk  # noqa: E402

import gi  # noqa: E402

gi.require_version("Graphene", "1.0")
from gi.repository import Graphene  # noqa: E402

cv = cw.cv


def walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def button(root, label):
    for widget in walk(root):
        if isinstance(widget, Gtk.Button) and (widget.get_label() or "").replace("_", "") == label:
            return widget
    raise LookupError(f"no button {label!r}")


def switch_row(root, title):
    for widget in walk(root):
        if isinstance(widget, Adw.SwitchRow) and widget.get_title() == title:
            return widget
    raise LookupError(f"no switch {title!r}")


def photograph(paintable, widget, path):
    """What the widget shows now, as a PNG (GTK 4 renders it itself; no screenshot tool)."""
    image = paintable.get_current_image()
    width, height = image.get_intrinsic_width(), image.get_intrinsic_height()
    if width <= 0 or height <= 0:
        width, height = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    image.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        return False
    texture = widget.get_native().get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, width, height))
    return texture.save_to_png(str(path))


class Smoke:
    def __init__(self, args, app):
        self.args, self.app = args, app
        self.out = args.out
        self.checks, self.notices, self.sent = [], [], []
        self.sessions = 0
        self.window = None
        self.failed = None

    # --- helpers -----------------------------------------------------------------------

    def check(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:500]})
        print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  ({detail})"), flush=True)

    def wait(self, condition, timeout, what):
        return condition, timeout, what

    def pause(self, seconds):
        until = time.monotonic() + seconds
        return self.wait(lambda: time.monotonic() >= until, seconds + 5, "pause")

    def scenario(self, name):
        """The next session's runner: the scripted game in `name`."""
        self.sessions += 1
        self.state = self.out / "runner" / f"{self.sessions:02d}-{name}"
        os.environ["GBFR_RUNNER_COMMAND"] = json.dumps([
            self.args.runner_python, "-B", str(HERE / "runner_scripted.py"), "--scenario", name,
            "--state", str(self.state), "--history", str(cv.state_dir() / "history.jsonl"),
            "--frame-delay", str(self.args.frame_delay)])

    def texts(self):
        w = self.window
        return {name: getattr(w, name).get_label() for name in
                ("quest", "count", "phase", "elapsed", "average", "failures", "message", "notice", "log")}

    def shot(self, name, widget=None):
        """Photograph the window content (or a dialog) after the next frames are drawn."""
        widget = widget or self.window.get_content()
        paintable = Gtk.WidgetPaintable.new(widget)
        widget.queue_draw()
        yield self.pause(0.4)
        path = self.out / f"{len(list(self.out.glob('*.png'))) + 1:02d}-{name}.png"
        self.check(f"photo {path.name}", photograph(paintable, widget, path))
        (self.out / "texts.jsonl").open("a").write(
            json.dumps({"photo": path.name, **self.texts()}, ensure_ascii=False) + "\n")

    def session_over(self):
        return self.window.runner is None

    def in_held_battle(self):
        status = self.window.state["status"]
        return status.get("phase") == "battle" and status.get("completed") == 1 \
            and status.get("episode") == 2

    def history(self):
        return cv.read_history(cv.state_dir() / "history.jsonl", 20)

    def pin_checks(self, w):
        """The pin's answers. A real press needs a real pointer, which never reaches GTK 4
        windows in a private session: stand-ins play the press's gesture and the surface here,
        to check what the window does with each outcome."""
        w.notice.set_label("")
        w.pin_button.activate()  # what Space or Enter does: there is no press to pass on
        yield self.pause(0.6)  # a button activated so says "clicked" 250 ms later
        self.check("pin from the keyboard explains the right click",
                   w.notice.get_label() == cw.WINDOW_MENU_HINT, w.notice.get_label())

        class Press:
            def __init__(self, event):
                self.event, self.calls = event, []

            def set_state(self, state):
                self.calls.append(state)

            def get_current_event(self):
                return self.event

            def reset(self):
                self.calls.append("reset")

        class Surface:
            def __init__(self, accepts):
                self.accepts, self.events = accepts, []

            def show_window_menu(self, event):
                self.events.append(event)
                return self.accepts

        event = object()
        for name, press, surface, hint in (
                ("a press without an event explains the right click", Press(None), Surface(True), True),
                ("a press GTK cannot send explains the right click", Press(event), Surface(False), True),
                ("a press sent to GNOME shows no hint", Press(event), Surface(True), False)):
            w.notice.set_label("")
            w.get_surface = lambda surface=surface: surface
            try:
                w._pin_pressed(press, 1, 10.0, 10.0)
            finally:
                del w.get_surface
            sent = surface.events == ([] if press.event is None else [event])
            self.check(f"pin: {name}", (w.notice.get_label() == cw.WINDOW_MENU_HINT) == hint and sent
                       and press.calls == [Gtk.EventSequenceState.CLAIMED, "reset"],
                       (w.notice.get_label(), surface.events, press.calls))
        w.notice.set_label("")

    # --- the script ----------------------------------------------------------------------

    def script(self):
        yield self.wait(lambda: self.app.window is not None and self.app.window.get_mapped(), 20,
                        "the window appears")
        w = self.window = self.app.window
        record = cw.cv.notification

        def recording(outcome, status):
            texts = record(outcome, status)
            self.notices.append({"outcome": outcome, "texts": texts})
            return texts
        cw.cv.notification = recording
        send = self.app.send_notification
        self.app.send_notification = lambda *a: (self.sent.append(a[0]), send(*a))

        # 1. Before any session.
        yield from self.shot("idle")
        t = self.texts()
        self.check("idle: waiting texts", t["quest"] == "等待任务" and t["phase"] == "等待开始", t)
        self.check("idle: Start only", w.start_button.get_sensitive() and not w.finish_button.get_sensitive()
                   and not w.stop_button.get_sensitive())
        self.check("idle: empty history row", [r.get_title() for r in w.history_rows] == ["还没有记录"])
        self.check("idle: log lines hidden by default", not w.log.get_visible())
        texts = [widget for widget in walk(w) if isinstance(widget, Gtk.Text)]
        selected = [t.get_text() for t in texts if t.get_selection_bounds()]  # () when none
        self.check("idle: nothing focused, nothing selected", w.get_focus() is None and texts
                   and not selected, (w.get_focus(), selected))
        model = w.menu_button.get_menu_model()
        items = [model.get_item_attribute_value(i, "label").unpack() for i in range(model.get_n_items())]
        self.check("idle: the menu has no 置顶… (the pin took its place)", "置顶…" not in items
                   and not self.app.has_action("window-menu"), items)
        pin = w.pin_button
        self.check("idle: a pin in the header bar", pin.get_ancestor(Adw.HeaderBar) is not None
                   and pin.get_mapped() and pin.get_icon_name() == "view-pin-symbolic"
                   and "置顶" in (pin.get_tooltip_text() or ""), (pin.get_icon_name(), pin.get_tooltip_text()))
        self.check("idle: title and icon", w.get_title() == "GBFR 自动刷取"
                   and w.get_icon_name() == cv.APP_ID, (w.get_title(), w.get_icon_name()))

        # 2. Three runs, to the end.
        self.scenario("three")
        w.runs.set_value(3)
        w.start_button.emit("clicked")
        self.check("three: controls while running", not w.start_button.get_sensitive()
                   and w.finish_button.get_sensitive() and w.stop_button.get_sensitive()
                   and not w.runs.get_sensitive())
        yield self.wait(lambda: w.state["status"].get("phase") == "battle", 30, "a battle")
        yield from self.shot("three-running")
        yield self.wait(self.session_over, 90, "three runs end")
        yield from self.shot("three-finished")
        t, status = self.texts(), w.state["status"]
        self.check("three: finished 3/3", status.get("phase") == "finished" and t["count"] == "3 / 3 次成功", t)
        self.check("three: average over 3 runs", t["average"].endswith("（3 轮）"), t["average"])
        self.check("three: Start again", w.start_button.get_sensitive() and w.runs.get_sensitive())
        self.check("three: notification", self.notices[-1:] and self.notices[-1]["outcome"] == "finished"
                   and self.notices[-1]["texts"] and self.sent[-1:] == ["session-end"], self.notices)
        entries = self.history()
        self.check("three: history line", entries and entries[0]["outcome"] == "finished"
                   and entries[0]["completed"] == 3 and entries[0]["limit"] == 3, entries[:1])
        self.check("three: history row shown", w.history_rows and "已完成 3/3" in w.history_rows[0].get_subtitle(),
                   [r.get_subtitle() for r in w.history_rows])
        self.check("three: runs saved", cv.load_settings(w.settings_path)["runs"] == 3)

        # 3. Finish after this run, asked during run 2's battle.
        self.scenario("hold")
        w.runs.set_value(10)
        w.start_button.emit("clicked")
        yield self.wait(self.in_held_battle, 60, "run 2's battle")
        yield self.pause(1.2)  # let the one-second timer redraw the battle time
        elapsed = w.elapsed.get_label()
        self.check("battle time is this battle's, in wall-clock time",
                   re.search(r"这一场打了 0:0\d）$", elapsed) is not None, elapsed)
        w.finish_button.emit("clicked")
        self.check("finish: button off at once, stop still on", not w.finish_button.get_sensitive()
                   and w.stop_button.get_sensitive() and w.runner is not None)
        yield self.wait(lambda: w.state["status"].get("finish_requested"), 10, "finish acknowledged")
        yield from self.shot("finish-requested")
        self.check("finish: phase note", "已收到本轮后停止请求" in self.texts()["phase"], self.texts()["phase"])
        yield self.wait(self.session_over, 60, "the finished session ends")
        status = w.state["status"]
        self.check("finish: ends after run 2", status.get("phase") == "finished" and status.get("completed") == 2,
                   status)
        keys = (self.state / "keys.txt").read_text().split()
        self.check("finish: the six expected keys", keys == ["Return", "Return", "3", "Return", "3", "3"], keys)

        # 3b. A finish the framework refused can be asked for again.
        self.scenario("refuse")
        w.start_button.emit("clicked")
        yield self.wait(self.in_held_battle, 60, "run 2's battle")
        w.finish_button.emit("clicked")
        yield self.wait(lambda: w.finish_button.get_sensitive(), 15, "finish offered again")
        self.check("refused finish: explained, still running", "没有被接受" in w.notice.get_label()
                   and w.runner is not None, self.texts())
        w.finish_button.emit("clicked")
        yield self.wait(self.session_over, 60, "the finished session ends")
        status = w.state["status"]
        self.check("refused finish: the second request ends it after run 2",
                   status.get("phase") == "finished" and status.get("completed") == 2, status)

        # 4. Stop now, during run 2's battle: no notification for a stop.
        self.scenario("hold")
        sent = len(self.sent)
        w.start_button.emit("clicked")
        yield self.wait(self.in_held_battle, 60, "run 2's battle")
        w.stop_button.emit("clicked")
        self.check("stop: stopping shown", self.texts()["phase"] == "正在停止……", self.texts()["phase"])
        self.check("stop: both stop buttons off", not w.finish_button.get_sensitive()
                   and not w.stop_button.get_sensitive())
        yield self.wait(self.session_over, 40, "the stopped session ends")
        yield from self.shot("stopped")
        status = w.state["status"]
        self.check("stop: stopped with a reason", status.get("phase") == "stopped" and status.get("message"),
                   status)
        self.check("stop: no notification", len(self.sent) == sent, self.sent)

        # 5. The runner dies during a session: the window says so and releases the keys.
        self.scenario("crash")
        w.start_button.emit("clicked")
        yield self.wait(self.session_over, 60, "the runner dies")
        self.check("crash: Start waits for the key release", not w.start_button.get_sensitive()
                   and not (self.state / "released.txt").exists())
        yield self.wait(lambda: (self.state / "released.txt").exists() and "请看一眼游戏画面" in w.notice.get_label(),
                        20, "the key release")
        yield from self.shot("crashed")
        self.check("crash: explained", "意外退出了（退出码 3）" in w.message.get_label()
                   and w.phase.get_label().startswith("操作已停止") and "松开" in w.notice.get_label(),
                   self.texts())
        entries = self.history()
        self.check("crash: history line", entries and entries[0]["outcome"] == "error"
                   and entries[0]["completed"] == 1, entries[:1])
        self.check("crash: notification", self.notices[-1]["outcome"] == "error"
                   and self.notices[-1]["texts"], self.notices[-1:])
        self.check("crash: keys released once", len((self.state / "released.txt").read_text().split()) == 1)
        self.check("crash: Start again", w.start_button.get_sensitive())

        # 5b. Start fails before any runner exists (the state folder is a file): idle again.
        state_dir = cv.state_dir()
        aside = state_dir.with_name(state_dir.name + ".aside")
        state_dir.rename(aside)
        state_dir.write_text("not a folder")
        self.scenario("three")
        w.start_button.emit("clicked")
        self.check("unstartable: explained, Start offered again", w.runner is None
                   and not w.state["running"] and w.start_button.get_sensitive()
                   and "没能启动" in w.notice.get_label(), self.texts())
        state_dir.unlink()
        aside.rename(state_dir)

        # 6. A runner that ignores stop: after the deadline the window offers to end it.
        cv.STOP_DEADLINE_S = 2
        self.scenario("stuck")
        w.start_button.emit("clicked")
        yield self.wait(self.in_held_battle, 60, "run 2's battle")
        w.stop_button.emit("clicked")
        yield self.wait(lambda: isinstance(w.get_visible_dialog(), Adw.AlertDialog), 15, "the deadline dialog")
        dialog = w.get_visible_dialog()
        self.check("stuck: dialog heading", dialog.get_heading() == "后台没有按时停下", dialog.get_heading())
        yield from self.shot("stuck-dialog", dialog)
        pid = int(w.runner.process.get_identifier())
        button(dialog, "结束后台").emit("clicked")
        yield self.wait(self.session_over, 20, "the runner is ended")
        yield self.wait(lambda: (self.state / "released.txt").exists() and "请看一眼游戏画面" in w.notice.get_label(),
                        20, "the key release")
        yield from self.shot("stuck-ended")
        self.check("stuck: runner gone", not Path(f"/proc/{pid}").exists())
        self.check("stuck: keys released", (self.state / "released.txt").exists())
        self.check("stuck: explained", "被结束了" in w.message.get_label(), self.texts())
        cv.STOP_DEADLINE_S = 35

        # 7. Settings: every item can be shown or hidden, and the choice is saved.
        self.app.activate_action("preferences", None)
        yield self.wait(lambda: isinstance(w.get_visible_dialog(), Adw.PreferencesDialog), 10, "settings")
        dialog = w.get_visible_dialog()
        titles = [label for _, label, _ in cv.INFO_ITEMS]
        found = [switch_row(dialog, title).get_active() for title in titles]
        self.check("settings: one switch per item", found == [shown for _, _, shown in cv.INFO_ITEMS], found)
        switch_row(dialog, "最近几条日志").set_active(True)
        switch_row(dialog, "平均每轮用时").set_active(False)
        yield from self.shot("settings", dialog)
        saved = cv.load_settings(w.settings_path)["show"]
        self.check("settings: saved at once", saved["log"] and not saved["average"], saved)
        self.check("settings: window follows", w.log.get_visible() and not w.average.get_visible())
        switch_row(dialog, "平均每轮用时").set_active(True)
        dialog.close()
        yield self.wait(lambda: w.get_visible_dialog() is None, 10, "settings closed")
        self.app.activate_action("about", None)
        yield self.wait(lambda: isinstance(w.get_visible_dialog(), Adw.AboutDialog), 10, "about")
        yield from self.shot("about", w.get_visible_dialog())
        w.get_visible_dialog().close()
        yield self.wait(lambda: w.get_visible_dialog() is None, 10, "about closed")
        yield from self.pin_checks(w)

        # 8. One window only: a second start brings this one forward and exits.
        second = subprocess.Popen(["/usr/bin/python3", "-I", str(Path(cw.__file__))],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        yield self.wait(lambda: second.poll() is not None, 20, "the second start returns")
        self.check("single instance: second start exits 0", second.returncode == 0, second.stderr.read())
        self.check("single instance: still one window", len(self.app.get_windows()) == 1)

        # 9. Closing during a session asks first, then stops and closes.
        self.scenario("hold")
        w.start_button.emit("clicked")
        yield self.wait(self.in_held_battle, 60, "run 2's battle")
        self.last_pid = int(w.runner.process.get_identifier())
        w.close()
        yield self.wait(lambda: isinstance(w.get_visible_dialog(), Adw.AlertDialog), 10, "the close dialog")
        dialog = w.get_visible_dialog()
        self.check("close: asks first", dialog.get_heading() == "正在刷本" and w.get_visible(), dialog.get_heading())
        yield from self.shot("close-dialog", dialog)
        button(dialog, "取消").emit("clicked")
        yield self.pause(0.5)
        self.check("close: cancel keeps the session", w.runner is not None and w.get_visible())
        w.close()
        yield self.wait(lambda: isinstance(w.get_visible_dialog(), Adw.AlertDialog), 10, "the close dialog")
        button(w.get_visible_dialog(), "停止并关闭").emit("clicked")
        self.closing_since = time.monotonic()
        # The window quits by itself once the runner has ended; main() checks the rest.
        yield self.wait(lambda: False, 60, "the window closes")

    def run(self):
        steps = self.script()
        state = {"condition": None, "deadline": 0.0, "what": ""}

        def tick():
            try:
                while True:
                    if state["condition"] is not None:
                        if not state["condition"]():
                            if time.monotonic() > state["deadline"]:
                                raise TimeoutError("timed out waiting for " + state["what"])
                            return True
                    condition, timeout, what = next(steps)
                    state.update(condition=condition, deadline=time.monotonic() + timeout, what=what)
            except StopIteration:
                return False
            except Exception as exc:  # report it and close down, do not hang
                self.failed = repr(exc)
                self.check("script ran to the end", False, repr(exc))
                if self.window is not None and self.window.runner is not None:
                    self.window.runner.close_stdin()
                GLib.timeout_add_seconds(5, lambda: self.app.quit() or False)
                return False
        GLib.timeout_add(100, tick)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runner-python", required=True, help="a Python with MaaFw")
    parser.add_argument("--frame-delay", type=float, default=0.05)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    app = cw.App()
    smoke = Smoke(args, app)
    smoke.run()
    started = time.monotonic()
    code = app.run([sys.argv[0]])
    smoke.check("window closed by itself after stop-and-close", code == 0 and smoke.failed is None
                and time.monotonic() - started > 1)
    if hasattr(smoke, "closing_since"):
        smoke.check("close: runner gone", not Path(f"/proc/{smoke.last_pid}").exists())
        smoke.check("close: quit within 15 s", time.monotonic() - smoke.closing_since < 15)
        status = json.loads((smoke.state / "session.json").read_text())
        smoke.check("close: session stopped", status.get("phase") == "stopped", status.get("phase"))
    saved = cv.load_settings(cv.config_dir() / "settings.json")
    smoke.check("close: settings kept", saved["runs"] == 10 and saved["show"]["log"], saved)
    passed = all(c["ok"] for c in smoke.checks)
    (args.out / "report.json").write_text(json.dumps(
        {"passed": passed, "checks": smoke.checks, "notifications": smoke.notices, "sent": smoke.sent},
        ensure_ascii=False, indent=2) + "\n")
    print("PASSED" if passed else "FAILED", f"{sum(c['ok'] for c in smoke.checks)}/{len(smoke.checks)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
