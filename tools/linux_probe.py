#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Linux 探测：GBFR 在嵌套 gamescope 里到底怎么表现（L1 到 L4）。

写平台代码之前先量。四个问题，每个都有一个可能失败的测量：

    L1  游戏在哪个 gamescope 里：嵌套 X 显示、Wayland 套接字、游戏窗口。只读。
    L2  后台能不能截到图：X11 GetImage（根窗口和游戏窗口）与 gamescopectl 截图，
        在聚焦、失焦、被遮住三种状态下各截一次。全黑算失败，由 opencv.is_blank_frame 判定。
    L3  经 XTest 送进嵌套 X 的 Escape，游戏收不收，宿主桌面会不会也收到。发之前先问。
    L4  失焦以后游戏停不停：聚焦、失焦各连拍一段，用 framediff 的 A4 判定；同时记下
        游戏窗口收到的 FocusIn/FocusOut 和 gamescope 根窗口属性的变化。

判定只作参考，原始数字原样写进报告，和 Windows 探测器同一条规矩。

运行时游戏必须已经开着。探测器从不启动或关闭游戏，不聚焦、不映射、不移动任何窗口，
不发带 Super 的按键（那是 gamescope 自己的快捷键），也不碰 Steam 和 gamescope 的配置。
结果写在 probe-runs/<时间>/ 下，这个目录不进版本库。
"""

import argparse
import itertools
import json
import os
import select
import subprocess
import sys
import termios
import time
import traceback
import tty
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import framediff  # noqa: E402

APPID = "881020"
STEPS = ("L1", "L2", "L3", "L4")
X11_SOCKET_DIR = Path("/tmp/.X11-unix")

# L3 只发这一个键。带 Super 的组合是 gamescope 的快捷键（截图、全屏……），绝不能发。
SAFE_KEYS = frozenset({"Escape"})

# 游戏窗口上的 WM_STATE：1 正常，3 最小化（steam-gaming 记录过嵌套窗口失焦后变成
# Iconic、画面全黑的情况），0 撤回。
WM_STATES = {0: "withdrawn", 1: "normal", 3: "iconic"}
MAP_STATES = {0: "unmapped", 1: "unviewable", 2: "viewable"}


# --- 报告 ---------------------------------------------------------------------

def short_error(exc, limit=160):
    """异常 -> 一行。Xlib 的错误对象里带着整段原始协议字节，原样写进报告没人读得了。"""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:limit]}"


def strip_ansi(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class Report:
    """边跑边写：每条结果立刻落盘并 flush，中途崩溃前面的也都在。

    report.md 给人看，report.jsonl 给程序读。输出全是 ASCII：窗口名之类可能含中文，
    转成转义序列，免得某个终端或编辑器读坏整份报告。
    """

    def __init__(self, out_dir, echo=print):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._md = open(self.dir / "report.md", "a", encoding="ascii", errors="backslashreplace")
        self._jsonl = open(self.dir / "report.jsonl", "a", encoding="ascii")
        self._echo = echo

    def heading(self, text):
        self._write(f"\n## {text}\n")

    def note(self, text):
        self._write(text)

    def result(self, step, name, value, text=None):
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step,
                  "name": name, "value": value}
        self._jsonl.write(json.dumps(record, default=repr, ensure_ascii=True) + "\n")
        self._jsonl.flush()
        self._write(f"- {step} {name}: {value if text is None else text}")

    def _write(self, text):
        safe = str(text).encode("ascii", "backslashreplace").decode("ascii")
        self._md.write(safe + "\n")
        self._md.flush()
        self._echo(safe)

    def close(self):
        self._md.close()
        self._jsonl.close()


def ask_yes(question, read=input):
    """默认是"否"。没有终端可读（EOF）也算"否"，宁可跳过也不在没人确认时发输入。"""
    try:
        answer = read(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def countdown(seconds, message, echo=print, sleep=time.sleep):
    echo(message)
    for left in range(seconds, 0, -1):
        echo(f"  {left}...")
        sleep(1)


# --- L1：进程、显示、窗口 ------------------------------------------------------

def read_environ(pid, proc="/proc"):
    """/proc/<pid>/environ -> dict。读不到（进程已退出、没有权限）返回 None。"""
    try:
        raw = Path(proc, str(pid), "environ").read_bytes()
    except OSError:
        return None
    env = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if not sep or not key:
            continue
        env[key.decode("utf-8", "surrogateescape")] = value.decode("utf-8", "surrogateescape")
    return env


def read_proc_text(pid, name, proc="/proc"):
    try:
        raw = Path(proc, str(pid), name).read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def find_game_processes(appid=APPID, proc="/proc"):
    """属于这个 appid、而且活在某个 gamescope 里面的进程。

    gamescope 只给自己的子进程设 GAMESCOPE_WAYLAND_DISPLAY，并把它们的 DISPLAY 换成
    嵌套的 Xwayland；gamescope 自己的 environ 里仍是宿主的 DISPLAY（setenv 发生在
    exec 之后，/proc 看不到）。所以要拿 SteamAppId 和 GAMESCOPE_WAYLAND_DISPLAY 一起筛。
    """
    found = []
    for entry in Path(proc).iterdir():
        if not entry.name.isdigit():
            continue
        env = read_environ(entry.name, proc)
        if not env:
            continue
        ids = (env.get("SteamAppId"), env.get("SteamGameId"), env.get("STEAM_COMPAT_APP_ID"))
        if appid not in ids or "GAMESCOPE_WAYLAND_DISPLAY" not in env:
            continue
        found.append({
            "pid": int(entry.name),
            "comm": read_proc_text(entry.name, "comm", proc),
            "cmdline": read_proc_text(entry.name, "cmdline", proc)[:160],
            "DISPLAY": env.get("DISPLAY"),
            "GAMESCOPE_WAYLAND_DISPLAY": env.get("GAMESCOPE_WAYLAND_DISPLAY"),
            "XAUTHORITY": env.get("XAUTHORITY"),
            "XDG_RUNTIME_DIR": env.get("XDG_RUNTIME_DIR"),
        })
    return sorted(found, key=lambda p: p["pid"])


def group_instances(processes):
    """按 (DISPLAY, GAMESCOPE_WAYLAND_DISPLAY) 分组。正常只有一组。"""
    groups = {}
    for p in processes:
        groups.setdefault((p["DISPLAY"], p["GAMESCOPE_WAYLAND_DISPLAY"]), []).append(p)
    return groups


def display_number(display):
    """":3" 或 ":3.0" -> 3。认不出返回 None。"""
    if not display or not display.startswith(":"):
        return None
    number = display[1:].split(".", 1)[0]
    return int(number) if number.isdigit() else None


def connect_x(display, xauthority=None):
    """连到嵌套 X。XAUTHORITY 用游戏进程里的那一份：嵌套的 Xwayland 可能要认证。"""
    from Xlib import display as xdisplay

    if xauthority:
        os.environ["XAUTHORITY"] = xauthority
    return xdisplay.Display(display)


def walk_windows(d):
    stack = [d.screen().root]
    while stack:
        w = stack.pop()
        yield w
        try:
            stack.extend(w.query_tree().children)
        except Exception:
            continue


def find_game_window(d, appid=APPID):
    """gamescope 用 STEAM_GAME 属性标出游戏窗口，WM_CLASS 里也是 steam_app_<appid>。两个都认。"""
    from Xlib import X

    needle = f"steam_app_{appid}"
    steam_game = d.intern_atom("STEAM_GAME")
    for w in walk_windows(d):
        try:
            if any(needle in c for c in (w.get_wm_class() or ())):
                return w
            prop = w.get_full_property(steam_game, X.AnyPropertyType)
            if prop is not None and str(appid) in (str(v) for v in prop.value):
                return w
        except Exception:
            continue
    return None


def describe_window(w):
    info = {"id": hex(w.id)}
    for key, read in (("wm_class", lambda: list(w.get_wm_class() or ())),
                      ("name", w.get_wm_name)):
        try:
            info[key] = read()
        except Exception as exc:
            info[key] = f"error: {exc!r}"
    try:
        g = w.get_geometry()
        info["geometry"] = [g.x, g.y, g.width, g.height]
        info["depth"] = g.depth
    except Exception as exc:
        info["geometry"] = f"error: {exc!r}"
    try:
        info["map_state"] = MAP_STATES.get(w.get_attributes().map_state, "?")
    except Exception as exc:
        info["map_state"] = f"error: {exc!r}"
    try:
        state = w.get_wm_state()
        info["wm_state"] = WM_STATES.get(state.state, state.state) if state else None
    except Exception as exc:
        info["wm_state"] = f"error: {exc!r}"
    return info


def gamescope_root_properties(d):
    """根窗口上 GAMESCOPE_* / STEAM_* 属性的当前值。焦点相关的都在这里。"""
    from Xlib import X

    root = d.screen().root
    out = {}
    for atom in root.list_properties():
        name = d.get_atom_name(atom)
        if not name.startswith(("GAMESCOPE", "STEAM")):
            continue
        prop = root.get_full_property(atom, X.AnyPropertyType)
        if prop is None:
            continue
        value = prop.value
        if isinstance(value, bytes):
            out[name] = value.decode("utf-8", "replace")
        else:
            out[name] = [int(v) for v in list(value)[:16]]
    return out


def run_gamescopectl(args, wayland_display, runtime_dir=None, timeout=10, run=subprocess.run):
    """在指定的 gamescope 实例上跑 gamescopectl。不设 GAMESCOPE_WAYLAND_DISPLAY 的话，
    它会去连 gamescope-0，那可能是另一个游戏的实例。"""
    env = dict(os.environ, GAMESCOPE_WAYLAND_DISPLAY=wayland_display)
    if runtime_dir:
        env["XDG_RUNTIME_DIR"] = runtime_dir
    return run(["gamescopectl", *args], env=env, capture_output=True, text=True, timeout=timeout)


# --- L2：截图 -----------------------------------------------------------------

def ximage_to_rgb(data, width, height, bits_per_pixel, lsb_first=True):
    """ZPixmap 原始字节 -> RGB ndarray。只认每像素 32 位，也就是 24/32 位深的常见情况。

    每行可能有补齐，所以行宽按 len(data) // height 算，而不是 width * 4。
    """
    if bits_per_pixel != 32:
        raise ValueError(f"unsupported bits per pixel: {bits_per_pixel}")
    if width <= 0 or height <= 0:
        raise ValueError(f"empty image: {width}x{height}")
    stride = len(data) // height
    if stride < width * 4:
        raise ValueError(f"image data too short: {len(data)} bytes for {width}x{height}")
    rows = np.frombuffer(data, dtype=np.uint8)[: stride * height].reshape(height, stride)
    pixels = rows[:, : width * 4].reshape(height, width, 4)
    # 小端（LSBFirst）在内存里是 B G R X；大端是 X R G B。
    return (pixels[:, :, 2::-1] if lsb_first else pixels[:, :, 1:4]).copy()


def capture_x11(d, window):
    from Xlib import X

    g = window.get_geometry()
    reply = window.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
    bpp = next((f.bits_per_pixel for f in d.display.info.pixmap_formats
                if f.depth == reply.depth), None)
    return ximage_to_rgb(reply.data, g.width, g.height, bpp,
                         d.display.info.image_byte_order == X.LSBFirst)


def wait_for_file(path, timeout, sleep=time.sleep, clock=time.monotonic, interval=0.2):
    """gamescope 在后台线程里存图：命令返回时文件不一定写完。等它出现并且大小停止变化。"""
    deadline = clock() + timeout
    last = -1
    while clock() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return True
        last = size
        sleep(interval)
    return False


def capture_gamescopectl(path, wayland_display, runtime_dir=None, timeout=10,
                         run=subprocess.run, sleep=time.sleep):
    from PIL import Image

    # 截图由 gamescope 自己的进程去写。它的工作目录是启动器的会话目录，不是这个终端的，
    # 所以相对路径会落到那边（或者因为目录不存在而写不出来），这边永远等不到文件。
    path = Path(path).resolve()
    if path.exists():
        path.unlink()
    result = run_gamescopectl(["screenshot", str(path)], wayland_display, runtime_dir,
                              timeout=timeout, run=run)
    if not wait_for_file(path, timeout, sleep=sleep):
        raise RuntimeError(f"no screenshot file appeared (exit {result.returncode}, "
                           f"stdout {result.stdout.strip()[:120]!r}, "
                           f"stderr {result.stderr.strip()[:120]!r})")
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def grade_frame(frame):
    """"content"、"blank" 或 "failed"。全黑或纯色算 blank：截图"成功"了也没用。"""
    from opencv import is_blank_frame

    if frame is None:
        return "failed"
    return "blank" if is_blank_frame(frame) else "content"


def save_frame(frame, path):
    from PIL import Image

    Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(path)


# --- L3：按键与泄漏 -------------------------------------------------------------

def send_key(d, keysym_name, hold=0.05, sleep=time.sleep):
    """经 XTest 往嵌套 X 发一次按键。只发白名单里的键。"""
    if keysym_name not in SAFE_KEYS:
        raise ValueError(f"refusing to send {keysym_name!r}: not in {sorted(SAFE_KEYS)}")
    from Xlib import X, XK

    keycode = d.keysym_to_keycode(XK.string_to_keysym(keysym_name))
    if not keycode:
        raise RuntimeError(f"the nested X server has no keycode for {keysym_name}")
    d.xtest_fake_input(X.KeyPress, keycode)
    d.sync()
    sleep(hold)
    d.xtest_fake_input(X.KeyRelease, keycode)
    d.sync()


class TerminalInput:
    """把终端切到 cbreak 且不回显，收集这段时间里送进终端的字节。

    L3 期间焦点在这个终端上。XTest 的按键要是漏到了宿主桌面，就会以 ESC 字节出现在
    这里。标准输入不是终端时什么都不做，read_pending 返回 None，表示无法判断。
    """

    def __init__(self, fd=None):
        self.fd = sys.stdin.fileno() if fd is None else fd
        self.saved = None

    def __enter__(self):
        if os.isatty(self.fd):
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            termios.tcflush(self.fd, termios.TCIFLUSH)
        return self

    def read_pending(self, wait=0.0):
        if self.saved is None:
            return None
        data = b""
        deadline = time.monotonic() + wait
        while True:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                return data
            chunk = os.read(self.fd, 1024)
            if not chunk:
                return data
            data += chunk

    def __exit__(self, *exc):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def try_capture(capture):
    try:
        return capture()
    except Exception:
        return None


def reaction_verdict(noises, reaction):
    """按键后的帧差，和按键前画面自己的帧差比。阈值沿用 framediff 的 INPUT_RATIO/INPUT_MARGIN。

    画面本身在动时，单看两帧分不出"游戏响应了"和"场景自己在动"，这时只能说无法判断，
    不能说"没反应"：headless 干跑里按键确实送到了，画面判定却给出了"没反应"。
    """
    noises = [n for n in noises if n is not None]
    if not noises or reaction is None:
        return ("no-data", "A frame was missing or changed size, so nothing can be compared.")
    noise = max(noises)
    threshold = max(noise * framediff.INPUT_RATIO, noise + framediff.INPUT_MARGIN)
    if reaction >= threshold:
        return ("reacted", f"The picture changed by {reaction:.2f} after the key, against at "
                           f"most {noise:.2f} on its own: the game acted on the key.")
    if noise >= framediff.STATIC_FLOOR:
        return ("inconclusive", f"The scene changed by up to {noise:.2f} on its own and by "
                                f"{reaction:.2f} after the key, so the picture cannot tell. "
                                "Compare L3-before.png and L3-after.png by eye, or re-run on "
                                "a still screen.")
    return ("no-reaction", f"The picture changed by {reaction:.2f} after the key, against at "
                           f"most {noise:.2f} on its own: no visible reaction.")


def delivery_verdict(counts):
    """按键在 X 这一层有没有送到游戏的窗口。和画面无关，所以画面判不了时它仍然算数。"""
    if counts is None:
        return ("unknown", "The game window was not found, so key delivery was not watched.")
    if counts.get("KeyPress", 0):
        return ("delivered", "The nested X server delivered the key to the game's window.")
    return ("not-seen", "No key event reached the game window or the nested focus window.")


def leak_verdict(received):
    if received is None:
        return ("unknown", "stdin is not a terminal, so a leak to the host cannot be seen.")
    if b"\x1b" in received:
        return ("leaked", f"The host terminal received {received!r}: the key reached the desktop too.")
    return ("contained", f"The host terminal received {received!r}: no Escape reached it.")


# --- L4：失焦暂停 -------------------------------------------------------------

def watch_window(window, keys=False):
    """只登记本客户端想收的事件，不改窗口本身：事件掩码是每个客户端各自的，
    几个客户端可以同时收同一个窗口的按键事件，游戏照样收得到。"""
    from Xlib import X

    mask = X.FocusChangeMask | X.StructureNotifyMask | X.PropertyChangeMask
    if keys:
        mask |= X.KeyPressMask | X.KeyReleaseMask
    window.change_attributes(event_mask=mask)


def drain_events(d):
    from Xlib import X

    names = {X.FocusIn: "FocusIn", X.FocusOut: "FocusOut", X.MapNotify: "MapNotify",
             X.UnmapNotify: "UnmapNotify", X.PropertyNotify: "PropertyNotify",
             X.ConfigureNotify: "ConfigureNotify", X.KeyPress: "KeyPress",
             X.KeyRelease: "KeyRelease"}
    counts = {}
    while d.pending_events():
        event = d.next_event()
        name = names.get(event.type, f"type{event.type}")
        counts[name] = counts.get(name, 0) + 1
        if event.type in (X.KeyPress, X.KeyRelease):
            key = f"{name} keycode {event.detail}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def capture_series(capture, count, interval, sleep=time.sleep):
    frames = []
    for i in range(count):
        frames.append(try_capture(capture))
        if i + 1 < count:
            sleep(interval)
    return frames


def series_deltas(frames):
    out = []
    for a, b in itertools.pairwise(frames):
        out.append(None if a is None or b is None else framediff.frame_delta(a, b))
    return out


# framediff 的判定文字是给 Windows 的 A4 写的（"问题在输入，不在暂停"）。这里换成对
# Linux 下一步有用的读法；判定代号和 framediff 的原文照样记进报告。
L4_MEANING = {
    "frozen": "The game stops when its window loses focus. L5, the focus spoof built as an "
              ".asi, is worth trying.",
    "reduced": "Motion fell but did not stop, which looks like a background frame-rate cap "
               "rather than a pause.",
    "running": "The game keeps running while its window is unfocused, so no focus spoof is "
               "needed for this.",
}


def changed_properties(before, after):
    keys = sorted(set(before) | set(after))
    return {k: [before.get(k), after.get(k)] for k in keys if before.get(k) != after.get(k)}


# --- 各步骤 -------------------------------------------------------------------

def step_l1(args, report, state):
    report.heading("L1  Where is the game?")
    report.result("L1", "kernel", os.uname().release)
    report.result("L1", "host session", f"{os.environ.get('XDG_SESSION_TYPE')} / "
                                        f"{os.environ.get('XDG_CURRENT_DESKTOP')}")
    # 只问 gamescope 本身。gamescopectl 没有 --version：它会把参数当成调试命令，
    # 发给占着 gamescope-0 的那个实例，那可能是另一个游戏。
    try:
        out = subprocess.run(["gamescope", "--version"], capture_output=True, text=True, timeout=10)
        first = strip_ansi(out.stdout or out.stderr).strip().splitlines()[:1]
        report.result("L1", "gamescope --version", first[0] if first else f"exit {out.returncode}")
    except Exception as exc:
        report.result("L1", "gamescope --version", short_error(exc))

    processes = find_game_processes(args.appid)
    report.result("L1", "processes inside gamescope", len(processes))
    for p in processes:
        report.result("L1", f"pid {p['pid']}", p)
    groups = group_instances(processes)
    if len(groups) != 1:
        report.result("L1", "verdict", len(groups),
                      "No game found inside gamescope. Start the game first."
                      if not groups else
                      "More than one gamescope instance runs this game. Close the others "
                      "and run again, so the probe cannot measure the wrong one.")
        return
    (display, wayland), members = next(iter(groups.items()))
    xauth = next((p["XAUTHORITY"] for p in members if p["XAUTHORITY"]), None)
    runtime = next((p["XDG_RUNTIME_DIR"] for p in members if p["XDG_RUNTIME_DIR"]), None)
    state.update(display=display, wayland=wayland, runtime=runtime)
    report.result("L1", "nested DISPLAY", display)
    report.result("L1", "GAMESCOPE_WAYLAND_DISPLAY", wayland)
    report.result("L1", "XAUTHORITY", xauth or "(none)")
    number = display_number(display)
    x_socket = X11_SOCKET_DIR / f"X{number}" if number is not None else None
    report.result("L1", "X socket exists", bool(x_socket and x_socket.exists()), f"{x_socket}")
    if runtime and wayland:
        report.result("L1", "Wayland socket exists", Path(runtime, wayland).exists(),
                      str(Path(runtime, wayland)))

    try:
        help_out = run_gamescopectl(["help"], wayland, runtime)
        text = (help_out.stdout or "") + (help_out.stderr or "")
        (report.dir / "gamescopectl-help.txt").write_text(text, encoding="utf-8", errors="replace")
        report.result("L1", "gamescopectl help", f"exit {help_out.returncode}, "
                      f"{len(text.splitlines())} lines saved to gamescopectl-help.txt")
    except Exception as exc:
        report.result("L1", "gamescopectl help", short_error(exc))

    d = connect_x(display, xauth)
    state["x"] = d
    report.result("L1", "X server", f"{d.display.info.vendor} {d.display.info.release_number}")
    report.result("L1", "XTEST available", bool(d.has_extension("XTEST")))
    root = d.screen().root
    report.result("L1", "root window", describe_window(root))
    window = find_game_window(d, args.appid)
    if window is None:
        report.result("L1", "game window", None, "not found on the nested display")
    else:
        state["window"] = window
        report.result("L1", "game window", describe_window(window))
    focus = d.get_input_focus().focus
    report.result("L1", "nested input focus", hex(focus.id) if hasattr(focus, "id") else focus)
    report.result("L1", "gamescope root properties", gamescope_root_properties(d))


def capture_methods(state, report):
    methods = []
    d = state.get("x")
    if d is not None:
        methods.append(("x11-root", lambda: capture_x11(d, d.screen().root)))
        if state.get("window") is not None:
            methods.append(("x11-window", lambda: capture_x11(d, state["window"])))
    if state.get("wayland"):
        shot = report.dir / "gamescopectl-latest.png"
        methods.append(("gamescopectl",
                        lambda: capture_gamescopectl(shot, state["wayland"], state.get("runtime"))))
    return methods


L2_PASSES = (
    ("focused", "Switch to the game window with Alt+Tab (do not click inside it), and leave it "
                "focused. Capturing in 5 seconds."),
    ("unfocused", "Switch back to this terminal with Alt+Tab. Keep the game window visible "
                  "beside it. Capturing in 5 seconds."),
    ("covered", "Now put another window fully over the game window (do not minimise the game). "
                "Keep this terminal focused. Capturing in 5 seconds."),
)


def step_l2(args, report, state):
    report.heading("L2  Can frames be captured in the background?")
    methods = capture_methods(state, report)
    if not methods:
        report.result("L2", "skipped", "L1 found no nested display")
        return
    best = {}
    for pass_name, instruction in L2_PASSES:
        countdown(5, instruction)
        for name, capture in methods:
            started = time.monotonic()
            try:
                frame = capture()
                error = None
            except Exception as exc:
                frame, error = None, short_error(exc)
            ms = round((time.monotonic() - started) * 1000)
            grade = grade_frame(frame)
            detail = {"grade": grade, "ms": ms}
            if frame is not None:
                detail["shape"] = list(np.asarray(frame).shape)
                detail["mean"] = round(float(np.asarray(frame).mean()), 2)
                save_frame(frame, report.dir / f"L2-{pass_name}-{name}.png")
            if error:
                detail["error"] = error
            report.result("L2", f"{pass_name} / {name}", detail)
            if pass_name != "focused" and grade == "content":
                best.setdefault(name, capture)
    # 后面的步骤用最快的那个能在后台截到内容的办法；X11 比走文件的 gamescopectl 快。
    for name in ("x11-window", "x11-root", "gamescopectl"):
        if name in best:
            state["capture"], state["capture_name"] = best[name], name
            report.result("L2", "verdict", name, f"background capture works with {name}")
            return
    report.result("L2", "verdict", None,
                  "No method returned content while the game was in the background.")


def step_l3(args, report, state):
    report.heading("L3  Does XTest input reach the game, and only the game?")
    d, capture = state.get("x"), state.get("capture")
    if d is None or capture is None:
        report.result("L3", "skipped", "needs the nested display (L1) and a working capture (L2)")
        return
    if not ask_yes("L3 sends Escape to the game twice (open, then close the menu). Is the game "
                   "on a screen where that is harmless, such as in town, and not in a battle "
                   "or a confirmation dialog?"):
        report.result("L3", "skipped", "not confirmed")
        return
    input("Make sure THIS terminal has keyboard focus and the game window is visible, "
          "then press Enter. ")
    # 按键事件发往嵌套 X 的焦点窗口；Wine 可能把焦点放在游戏的子窗口上，所以两个都看。
    focus = d.get_input_focus().focus
    watched = {w.id: w for w in (state.get("window"), focus) if hasattr(w, "id")}
    for w in watched.values():
        watch_window(w, keys=True)
    with TerminalInput() as terminal:
        frames = []
        for _ in range(3):
            frames.append(try_capture(capture))
            time.sleep(0.75)
        drain_events(d)
        send_key(d, "Escape")
        time.sleep(1.5)
        after = try_capture(capture)
        delivered = drain_events(d) if watched else None
        received = terminal.read_pending(wait=0.5)
        send_key(d, "Escape")
        time.sleep(1.5)
        closed = try_capture(capture)
    for name, frame in (("before", frames[-1]), ("after", after), ("closed", closed)):
        if frame is not None:
            save_frame(frame, report.dir / f"L3-{name}.png")
    noises = series_deltas(frames)
    reaction = None if frames[-1] is None or after is None else framediff.frame_delta(frames[-1], after)
    code, text = delivery_verdict(delivered)
    report.result("L3", "key delivery", {"code": code, "watched": sorted(hex(i) for i in watched),
                                         "events": delivered}, text)
    code, text = reaction_verdict(noises, reaction)
    report.result("L3", "picture", {"code": code, "noise": noises, "after_key": reaction}, text)
    code, text = leak_verdict(received)
    report.result("L3", "host leak", code, text)


def step_l4(args, report, state):
    report.heading("L4  Does the game pause when it loses focus?")
    d, capture = state.get("x"), state.get("capture")
    if capture is None:
        report.result("L4", "skipped", "needs a working background capture (L2)")
        return
    if not ask_yes("L4 needs continuous motion on screen, such as a quest in progress or an "
                   "animated scene. It sends no input. Ready?"):
        report.result("L4", "skipped", "not confirmed")
        return
    window = state.get("window")
    if d is not None and window is not None:
        watch_window(window)
        drain_events(d)
    props_before = gamescope_root_properties(d) if d is not None else {}
    phases = {}
    for phase, instruction in (
            ("focused", "Switch to the game with Alt+Tab and leave mouse and keyboard alone."),
            ("unfocused", "Switch back to this terminal with Alt+Tab, keep the game visible, "
                          "and leave it alone.")):
        countdown(5, instruction)
        frames = capture_series(capture, args.frames, args.interval)
        events = drain_events(d) if d is not None and window is not None else None
        deltas = series_deltas(frames)
        phases[phase] = framediff.summarize(deltas)
        report.result("L4", f"{phase} deltas", [None if v is None else round(v, 3) for v in deltas])
        report.result("L4", f"{phase} summary", phases[phase])
        report.result("L4", f"{phase} focus events on the game window", events)
        for i in (0, len(frames) - 1):
            if frames[i] is not None:
                save_frame(frames[i], report.dir / f"L4-{phase}-{i:02d}.png")
    code, text = framediff.motion_verdict(phases["focused"], phases["unfocused"])
    report.result("L4", "framediff", code, text)
    report.result("L4", "verdict", code, L4_MEANING.get(code, text))
    if d is not None:
        report.result("L4", "gamescope root properties that changed",
                      changed_properties(props_before, gamescope_root_properties(d)))


STEP_FUNCTIONS = {"L1": step_l1, "L2": step_l2, "L3": step_l3, "L4": step_l4}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Measure how GBFR behaves under gamescope.")
    parser.add_argument("--steps", default=",".join(STEPS),
                        help="comma-separated subset of L1,L2,L3,L4 (default: all). L1 always "
                             "runs, and asking for L3 or L4 runs L2 too")
    parser.add_argument("--appid", default=APPID)
    parser.add_argument("--out", help="output folder (default: probe-runs/<timestamp>)")
    parser.add_argument("--frames", type=int, default=10, help="frames per L4 phase, at least 2")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between L4 frames")
    args = parser.parse_args(argv)
    if args.frames < 2:
        parser.error("--frames must be at least 2: L4 measures motion between consecutive frames")
    if args.interval < 0:
        parser.error("--interval cannot be negative")
    requested = {s.strip().upper() for s in args.steps.split(",") if s.strip()}
    unknown = sorted(requested - set(STEPS))
    if unknown:
        parser.error(f"unknown steps: {', '.join(unknown)}")
    # 其余每一步都要 L1 找到的显示和窗口，所以 L1 总是跑。L3、L4 还要 L2 选出来的截图办法，
    # 只要了它们而没要 L2，它们会一声不响地跳过。顺序按 STEPS，不按输入。
    if requested & {"L3", "L4"}:
        requested.add("L2")
    args.steps = [s for s in STEPS if s in requested or s == "L1"]
    return args


def main(argv=None):
    args = parse_args(argv)
    out = (Path(args.out) if args.out
           else Path("probe-runs") / time.strftime("%Y%m%d-%H%M%S")).resolve()
    report = Report(out)
    report.note(f"# GBFR Linux probe, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.note(f"steps {','.join(args.steps)}, appid {args.appid}, output {out}")
    state = {}
    try:
        for step in args.steps:
            try:
                STEP_FUNCTIONS[step](args, report, state)
            except KeyboardInterrupt:
                report.result(step, "interrupted", "stopped by the user")
                return 130
            except Exception as exc:
                report.result(step, "crashed", repr(exc),
                              f"crashed: {exc!r}\n" + traceback.format_exc(limit=4))
    finally:
        report.note(f"\nDone. Results are in {out}/")
        report.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
