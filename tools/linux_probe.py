#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Linux 探测：GBFR 在嵌套 gamescope 里到底怎么表现（L1 到 L4）。

写平台代码之前先量。四个问题，每个都有一个可能失败的测量：

    L1  游戏在哪个 gamescope 里：嵌套 X 显示、Wayland 套接字、游戏窗口，以及这个
        gamescope 在 PipeWire 里的视频节点。只读。
    L2  后台能不能截到图：X11 GetImage（根窗口和游戏窗口）、gamescopectl 截图，以及从
        gamescope 的 PipeWire 节点取一帧，在聚焦、失焦、被遮住三种状态下各截一次。全黑算
        失败，由 opencv.is_blank_frame 判定。每一轮再连续取流几秒，量 PipeWire 的帧率。
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
import re
import select
import signal
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


# --- L1：游戏那个 gamescope 在 PipeWire 里的视频节点 -------------------------------

def parent_pid(pid, proc="/proc"):
    """/proc/<pid>/status 里的 PPid。进程已经退出或者读不懂，返回 None。"""
    try:
        text = Path(proc, str(pid), "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("PPid:"):
            value = line.partition(":")[2].strip()
            return int(value) if value.isdigit() else None
    return None


def process_lineage(pid, proc="/proc", limit=64):
    """pid 本身和它的各级父进程，近的在前，到 1 号进程为止（不含）。

    游戏和 gamescope 之间隔着几层启动器，层数随启动方式而变，所以一直往上走。也不认
    进程名：gamescope 会把自己改名成 gamescope-wl，包里还另有一个 gamescopereaper。
    """
    chain = []
    current = pid
    while current is not None and current > 1 and current not in chain and len(chain) < limit:
        chain.append(current)
        current = parent_pid(current, proc)
    return chain


def read_pw_dump(runtime_dir=None, timeout=10, run=subprocess.run):
    """跑一次 pw-dump，返回解析好的 JSON 列表。XDG_RUNTIME_DIR 用游戏进程里的那一份，
    PipeWire 的套接字在那下面。"""
    env = dict(os.environ)
    if runtime_dir:
        env["XDG_RUNTIME_DIR"] = runtime_dir
    result = run(["pw-dump", "-N"], env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"pw-dump exited {result.returncode}: {result.stderr.strip()[:120]!r}")
    return json.loads(result.stdout)


def pipewire_gamescope_nodes(dump):
    """pw-dump 的 JSON -> 每个 gamescope 视频节点的 id、serial、状态和所属进程的进程号。

    gamescope 把它的流命名为 "gamescope"，节点的 node.name 就是这个。节点的 client.id
    指向建它的 Client 对象，上面有两个进程号：pipewire.sec.pid 是 PipeWire 从套接字对端
    凭据读到的，客户端改不了；application.process.id 是客户端自己报的，进程在另一个 PID
    命名空间里时会不一样。两个都收下。
    """
    def props(obj):
        return (obj.get("info") or {}).get("props") or {}

    objects = [o for o in dump if isinstance(o, dict)]
    clients = {o.get("id"): props(o) for o in objects
               if o.get("type") == "PipeWire:Interface:Client"}
    nodes = []
    for o in objects:
        p = props(o)
        if (o.get("type") != "PipeWire:Interface:Node" or not isinstance(o.get("id"), int)
                or p.get("node.name") != "gamescope"):
            continue
        client = clients.get(p.get("client.id"), {})
        pids = {client.get("pipewire.sec.pid"), client.get("application.process.id")}
        nodes.append({"id": o["id"], "serial": p.get("object.serial"),
                      "media_class": p.get("media.class"), "state": o["info"].get("state"),
                      "pids": sorted(pid for pid in pids if isinstance(pid, int))})
    return sorted(nodes, key=lambda n: n["id"])


def resolve_pipewire_node(nodes, lineages):
    """在 gamescope 的节点里挑出游戏那个实例的。lineages 是 L1 找到的每个进程各一条
    process_lineage。返回 (节点或 None, 代号, 说明)。

    沿每条链往上，第一个拥有 gamescope 节点的进程就是这个进程所在的 gamescope；gamescope
    里再套 gamescope 时，近的那层才是游戏用的显示。父进程先退出的进程会被托管给别的进程，
    它的链上可能没有 gamescope，那条链就什么也不提供。所有链都对不上、而 PipeWire 里恰好
    只有一个 gamescope 节点时，先用它，但注明是假定的：它也可能属于另一个 gamescope。
    """
    found = {}
    for lineage in lineages:
        for pid in lineage:
            owned = [n for n in nodes if pid in n["pids"]]
            if owned:
                for n in owned:
                    found.setdefault(n["id"], (n, pid))
                break
    if len(found) == 1:
        node, pid = next(iter(found.values()))
        return node, "matched", (f"Node {node['id']} belongs to process {pid}, an ancestor of "
                                 "the game's processes.")
    if found:
        return None, "ambiguous", (f"Nodes {sorted(found)} each belong to an ancestor of some of "
                                   "the game's processes, so the probe cannot tell which is the "
                                   "game's.")
    if len(nodes) == 1:
        return nodes[0], "assumed", (f"Node {nodes[0]['id']} is the only gamescope node, but it "
                                     "does not belong to an ancestor of the game's processes. It "
                                     "is used on the assumption that it is the game's.")
    if not nodes:
        return None, "none", ("PipeWire has no gamescope node. This gamescope may be built "
                              "without PipeWire, or it has not set up its stream.")
    return None, "ambiguous", (f"{len(nodes)} gamescope nodes, and none belongs to an ancestor "
                               "of the game's processes.")


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
    # 用 absolute() 而不是 resolve()：resolve() 会顺着最后一段的符号链接走，下面清理旧图时
    # 删掉的就成了链接指向的文件，可能在报告目录之外。
    path = Path(path).absolute()
    # exists() 也顺着链接走：指向不存在目标的悬空链接会被当成"没有文件"而留下，gamescope
    # 就会穿过它把图写到链接指向的地方。所以要看目录项本身。
    if path.is_symlink() or path.exists():
        path.unlink()
    result = run_gamescopectl(["screenshot", str(path)], wayland_display, runtime_dir,
                              timeout=timeout, run=run)
    if not wait_for_file(path, timeout, sleep=sleep):
        raise RuntimeError(f"no screenshot file appeared (exit {result.returncode}, "
                           f"stdout {result.stdout.strip()[:120]!r}, "
                           f"stderr {result.stderr.strip()[:120]!r})")
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


# 一帧最多等这么久。正常一秒内就有：起进程、连上 PipeWire、协商格式、等 gamescope 画下一帧。
PIPEWIRE_TIMEOUT = 5.0


def pipewire_pipeline(node_id, location, frames=1):
    """gst-launch-1.0 的命令行：从节点 node_id 取 frames 帧，转成 PNG 存到 location。

    - 按节点 id 连：path 就是 pw_stream_connect 的 target_id，WirePlumber 拿它去对节点 id。
      target-object 也收 serial 或名字，但名字有歧义（每个 gamescope 都叫 gamescope）；
      serial 照源码也该行，在这台机器上的一次 headless 试验里却没连上，id 连上了。
    - node.dont-fallback：节点要是已经没了，WirePlumber 默认把流改接到默认的视频源上，
      那可能是摄像头。设了它，WirePlumber 0.5 回一个 "defined target not found" 错误，
      不改接。值写成 (string)true：不写类型会被解析成布尔值，转成字符串就成了 "TRUE"。
    - 格式钉死在内存里的 BGRx。gamescope 的每种格式都给两份，一份带 DMA-BUF 的 modifier
      并标为必需，一份不带、走共享内存（3.16.20 的 src/pipewire.cpp）。不带 memory:DMABuf
      的 caps 只对得上后一份，DMA-BUF 和 NV12 都绕开了。
    - PNG 用最低的压缩级别：一帧 2560x1440，压缩花的时间不该算进截图的耗时里。
    """
    return ["gst-launch-1.0", "-q",
            "pipewiresrc", f"path={node_id}", f"num-buffers={frames}",
            'stream-properties="props,node.dont-fallback=(string)true"', "!",
            "video/x-raw,format=BGRx", "!", "videoconvert", "!",
            "pngenc", "compression-level=1", "!", "filesink", f"location={location}"]


def run_bounded(cmd, timeout, grace=2.0, env=None, at_deadline=None, popen=subprocess.Popen):
    """跑一个外部命令，最多等 timeout 秒，绝不让探测器挂住。

    到点先调 at_deadline()，趁命令还活着看一眼现场；再发 SIGINT，让 gst-launch 自己收尾、
    好好断开 PipeWire；grace 秒后还没退就杀掉。
    返回 (退出码, stdout 和 stderr 合在一起的输出, 是否超时, at_deadline 的结果)。
    """
    proc = popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
        return proc.returncode, output, False, None
    except subprocess.TimeoutExpired:
        pass
    seen = None
    if at_deadline is not None:
        try:
            seen = at_deadline()
        except Exception as exc:
            seen = short_error(exc)
    proc.send_signal(signal.SIGINT)
    try:
        output, _ = proc.communicate(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate()
    return proc.returncode, output, True, seen


def pipewire_links(dump, node_id):
    """从 node_id 接出去的每条连接的状态，比如 negotiating、paused、active。

    一条都没有，说明 WirePlumber 根本没把流接上；停在 negotiating，说明接上了但格式没谈拢。
    """
    states = []
    for o in dump:
        if not isinstance(o, dict) or o.get("type") != "PipeWire:Interface:Link":
            continue
        info = o.get("info") or {}
        if info.get("output-node-id") == node_id:
            states.append(info.get("state") or "?")
    return sorted(states)


# 测帧率时连续取流的时长。一秒多花在起进程和协商上，剩下的够数出每秒十几帧和一百多帧的差别。
RATE_SECONDS = 3.0
# 帧率管道的后半段：不转码、不存文件，帧到了 fakesink 就扔。silent=false 加上 gst-launch -v，
# 每帧打一行带时间戳的 last-message。sync=false：来一帧收一帧，不按时钟等。
RATE_TAIL = ["!", "video/x-raw,format=BGRx", "!", "fakesink", "silent=false", "sync=false"]
CHAIN_LINE = re.compile(r"last-message = chain .*?, pts: (?:(\d+):(\d\d):(\d\d)\.(\d{9})|none)")
SINK_CAPS = re.compile(r"GstFakeSink:fakesink0\.GstPad:sink: caps = (.+)")
CAPS_FIELD = re.compile(r"([\w-]+)=\(\w+\)([^,]+)")


def pipewire_rate_pipeline(node_id):
    """连续取流的 gst-launch 命令行。目标、不改接和格式的道理同 pipewire_pipeline。

    do-timestamp：gamescope 的帧要是没带时间戳，就用帧到达时的流时间，否则算不出帧率。
    """
    return ["gst-launch-1.0", "-v", "pipewiresrc", f"path={node_id}", "do-timestamp=true",
            'stream-properties="props,node.dont-fallback=(string)true"', *RATE_TAIL]


def parse_rate(output):
    """gst-launch -v 的输出 -> 收到几帧、几帧有时间戳、按时间戳算的帧率、最长的帧间隔，
    以及协商出来的格式。格式一行都没有，说明协商没完成。"""
    frames, stamps = 0, []
    for match in CHAIN_LINE.finditer(output):
        frames += 1
        if match.group(1) is not None:
            h, m, s, ns = (int(g) for g in match.groups())
            stamps.append(h * 3600 + m * 60 + s + ns / 1e9)
    result = {"frames": frames, "stamped": len(stamps), "fps": None, "max_gap_ms": None,
              "caps": None}
    caps = SINK_CAPS.search(output)
    if caps:
        fields = dict(CAPS_FIELD.findall(caps.group(1)))
        result["caps"] = (f"{fields.get('format')} {fields.get('width')}x{fields.get('height')}"
                          f" @ {fields.get('framerate')}")
    if len(stamps) >= 2 and stamps[-1] > stamps[0]:
        result["fps"] = round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 1)
        result["max_gap_ms"] = round(max(b - a for a, b in itertools.pairwise(stamps)) * 1000)
    return result


def describe_rate(rate):
    """帧率结果 -> 给人看的一句话。每秒 10 帧是条粗线：点菜单每秒几帧就够，跟着战斗临场
    反应要 10 帧以上。"""
    if rate.get("skipped"):
        return f"Not measured: {rate['skipped']}."
    if rate.get("fps") is None:
        why = rate.get("error") or f"{rate.get('frames', 0)} frames arrived in {rate.get('seconds')} s"
        return f"No frame rate: {why}. Links from the node at the end: {rate.get('links') or 'none'}."
    enough = ("enough for a loop that reacts to the fight" if rate["fps"] >= 10
              else "below the 10 a second a loop that reacts to the fight would need")
    return (f"{rate['fps']} frames a second over {rate['stamped']} frames, longest gap "
            f"{rate['max_gap_ms']} ms, {rate['caps']}: {enough}.")


class PipeWireCapture:
    """L2 的第四种截图办法：每截一次起一条 gst-launch 管道，从游戏的 gamescope 节点取一帧。

    有报告说，别的程序和 gamescope 协商格式失败时，gamescope 会拆掉自己的节点，直到它重启
    （OpenGamingCollective/gamescope#27）。所以每截一次都重新看节点还在不在，不在了就不再
    起管道。每次的细节放在 last 里，L2 把它和结果写在一起。
    """

    def __init__(self, node_id, path, runtime_dir=None, timeout=PIPEWIRE_TIMEOUT,
                 popen=subprocess.Popen, dump=read_pw_dump):
        self.node_id = node_id
        # 和 capture_gamescopectl 一样用 absolute()：清理旧图时不顺着符号链接走。
        self.path = Path(path).absolute()
        self.runtime_dir = runtime_dir
        self.timeout = timeout
        self.popen = popen
        self.dump = dump
        self.gone = False
        self.last = {}

    def node_present(self):
        nodes = pipewire_gamescope_nodes(self.dump(self.runtime_dir))
        return any(n["id"] == self.node_id for n in nodes)

    def check_node(self):
        """节点还在吗：True、False，或者 pw-dump 读不出来时的错误文字（那不算没了）。"""
        try:
            present = self.node_present()
        except Exception as exc:
            present = short_error(exc)
        self.gone = present is False
        return present

    def run(self, cmd, seconds):
        # 英文的出错信息：报告只收 ASCII，中文的会变成一串转义。
        env = dict(os.environ, LC_ALL="C")
        if self.runtime_dir:
            env["XDG_RUNTIME_DIR"] = self.runtime_dir
        return run_bounded(
            cmd, seconds, env=env,
            at_deadline=lambda: pipewire_links(self.dump(self.runtime_dir), self.node_id),
            popen=self.popen)

    def measure_rate(self, seconds=RATE_SECONDS):
        """连续取流 seconds 秒，量 PipeWire 每秒能给几帧。返回一个字典，从不抛异常。

        到点发 SIGINT 是这里的正常收尾；gst-launch 在那之前自己退出，才是出了错。
        """
        if self.gone:
            return {"skipped": f"node {self.node_id} disappeared after an earlier capture"}
        try:
            code, output, timed_out, links = self.run(pipewire_rate_pipeline(self.node_id),
                                                      seconds)
        except Exception as exc:
            return {"error": short_error(exc), "node_after": self.check_node()}
        rate = parse_rate(output or "")
        rate.update(seconds=seconds, links=links)
        if not timed_out:
            first_error = (output or "").partition("ERROR")
            detail = " ".join((first_error[1] + first_error[2]).split())[:160]
            rate["error"] = f"gst-launch ended on its own with exit {code}: {detail!r}"
        rate["node_after"] = self.check_node()
        return rate

    def __call__(self):
        # 先清空：这一次要是在起进程之前就失败了，报告里不能挂着上一次的细节。
        self.last = {}
        if self.gone:
            self.last["node_after"] = False
            raise RuntimeError(f"node {self.node_id} disappeared after an earlier capture")
        if self.path.is_symlink() or self.path.exists():
            self.path.unlink()
        started = time.monotonic()
        code, output, timed_out, links = self.run(pipewire_pipeline(self.node_id, self.path),
                                                  self.timeout)
        output = " ".join((output or "").split())
        self.last = {"target": f"path={self.node_id}",
                     "gst_ms": round((time.monotonic() - started) * 1000),
                     "exit": code, "timed_out": timed_out}
        if timed_out:
            self.last["links"] = links
        if output:
            self.last["output"] = output[:300]
        present = self.last["node_after"] = self.check_node()
        if timed_out:
            raise RuntimeError(f"no frame within {self.timeout:g} s; links from node "
                               f"{self.node_id}: {links or 'none'}; node still there: {present}")
        if code != 0:
            raise RuntimeError(f"gst-launch exited {code}: {output[:120]!r}")
        if not self.path.exists() or self.path.stat().st_size == 0:
            raise RuntimeError("gst-launch exited 0 but wrote no frame")
        from PIL import Image

        with Image.open(self.path) as image:
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


def within_window(window, ancestor_id, max_depth=64):
    """window 是 ancestor 本身，或者是它的子孙。沿 query_tree().parent 往上走，到根为止。

    XTest 的按键发给嵌套 X 的焦点窗口。焦点要是在别的窗口上（覆盖层、启动器、Wine 的
    对话框），Escape 就发到了游戏以外的地方，那边收到按键也不能算游戏收到。焦点也可能
    不是窗口，而是 None 或 PointerRoot 这样的常量，那同样不算。
    """
    current = window
    for _ in range(max_depth):
        if not hasattr(current, "id") or not current.id:
            return False
        if current.id == ancestor_id:
            return True
        try:
            tree = current.query_tree()
        except Exception:
            return False
        if current.id == tree.root.id:
            return False
        current = tree.parent
    return False


def describe_focus(focus):
    if hasattr(focus, "id"):
        return hex(focus.id)
    return {0: "None", 1: "PointerRoot"}.get(focus, repr(focus))


class TerminalInput:
    """把终端切到 cbreak 且不回显，收集这段时间里送进终端的字节。

    L3 期间焦点在这个终端上。XTest 的按键要是漏到了宿主桌面，就会以 ESC 字节出现在
    这里。标准输入不是终端时什么都不做，read_pending 返回 None，表示无法判断。
    """

    def __init__(self, fd=None):
        if fd is None:
            try:
                fd = sys.stdin.fileno()
            except (OSError, ValueError):
                # 标准输入被关掉或被换成了没有文件描述符的对象：当作不是终端。
                fd = None
        self.fd = fd
        self.saved = None

    def __enter__(self):
        if self.fd is not None and os.isatty(self.fd):
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
             X.KeyRelease: "KeyRelease", X.MappingNotify: "MappingNotify"}
    counts = {}
    while d.pending_events():
        event = d.next_event()
        name = names.get(event.type, f"type{event.type}")
        counts[name] = counts.get(name, 0) + 1
        if event.type in (X.KeyPress, X.KeyRelease):
            key = f"{name} keycode {event.detail}"
            window_id = getattr(getattr(event, "window", None), "id", None)
            if window_id is not None:
                key += f" on {hex(window_id)}"
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

    # 这里只看 gamescope 的节点在不在、属于谁，不取流。放在连 X 之前：连不上 X 时 L1 就此
    # 中断，PipeWire 这一行也就没了。
    lineages = [process_lineage(p["pid"]) for p in members]
    report.result("L1", f"process lineage of pid {members[0]['pid']}",
                  [[pid, read_proc_text(pid, "comm")] for pid in lineages[0]])
    try:
        nodes = pipewire_gamescope_nodes(read_pw_dump(runtime))
        report.result("L1", "PipeWire gamescope nodes", nodes)
        node, code, text = resolve_pipewire_node(nodes, lineages)
        report.result("L1", "PipeWire node", {"code": code, "node": node}, text)
        if node is not None:
            state.update(pw_node=node, pw_node_code=code)
    except Exception as exc:
        report.result("L1", "PipeWire node", short_error(exc))

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
    if state.get("pw_node") is not None:
        # 同一个对象跑完三轮：节点在前一轮没了，后面几轮就不再起管道。
        methods.append(("pipewire", PipeWireCapture(state["pw_node"]["id"],
                                                    report.dir / "pipewire-latest.png",
                                                    state.get("runtime"))))
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
            detail.update(getattr(capture, "last", None) or {})
            report.result("L2", f"{pass_name} / {name}", detail)
            if pass_name != "focused" and grade == "content":
                best.setdefault(name, capture)
        # 单帧截图说明不了连续取流能有多快；每一轮都量，看后台会不会把帧率压下来。
        for name, capture in methods:
            if hasattr(capture, "measure_rate"):
                print(f"  Measuring the {name} frame rate for {RATE_SECONDS:g} seconds. "
                      "Leave the windows as they are.")
                rate = capture.measure_rate()
                report.result("L2", f"{pass_name} / {name} frame rate", rate, describe_rate(rate))
    # 后面的步骤用最快的那个能在后台截到内容的办法；X11 比走文件的 gamescopectl 快。
    # 每帧起一条管道的 PipeWire 排最后：它还没在游戏上量过，而且每多截一次，节点就多一次
    # 被协商失败拆掉的机会，L3、L4 要的是中途不会断的截图。
    for name in ("x11-window", "x11-root", "gamescopectl", "pipewire"):
        if name in best:
            state["capture"], state["capture_name"] = best[name], name
            report.result("L2", "verdict", name, f"background capture works with {name}")
            return
    report.result("L2", "verdict", None,
                  "No method returned content while the game was in the background.")


def step_l3(args, report, state):
    report.heading("L3  Does XTest input reach the game, and only the game?")
    d, capture, game = state.get("x"), state.get("capture"), state.get("window")
    if d is None or capture is None:
        report.result("L3", "skipped", "needs the nested display (L1) and a working capture (L2)")
        return
    if game is None:
        report.result("L3", "skipped", "L1 did not find the game window, so there is nothing "
                                       "to aim the key at")
        return
    if not ask_yes("L3 sends Escape to the game twice (open, then close the menu). Is the game "
                   "on a screen where that is harmless, such as in town, and not in a battle "
                   "or a confirmation dialog?"):
        report.result("L3", "skipped", "not confirmed")
        return
    input("Make sure THIS terminal has keyboard focus and the game window is visible, "
          "then press Enter. ")
    # 按键发往嵌套 X 的焦点窗口。Wine 可能把焦点放在游戏的子窗口上，那可以；焦点在游戏
    # 以外的窗口上就一个键也不发。每次发之前都重新看一次，焦点可能在中途换走。
    focus = d.get_input_focus().focus
    if not within_window(focus, game.id):
        report.result("L3", "skipped", {"focus": describe_focus(focus), "game": hex(game.id)},
                      "The nested X focus is not on the game window, so Escape would reach "
                      "something else. Bring the game to the front inside gamescope and run "
                      "L3 again.")
        return
    with TerminalInput() as terminal:
        frames = []
        for _ in range(3):
            frames.append(try_capture(capture))
            time.sleep(0.75)
        # 取基准帧花了两秒多，焦点可能已经换走。发第一个键之前再看一次，并且盯住此刻真正
        # 有焦点的那个窗口。
        focus = d.get_input_focus().focus
        if not within_window(focus, game.id):
            report.result("L3", "skipped", {"focus": describe_focus(focus), "game": hex(game.id)},
                          "The nested focus left the game while the baseline frames were "
                          "taken, so no key was sent. Run L3 again.")
            return
        watched = {w.id: w for w in (game, focus)}
        for w in watched.values():
            watch_window(w, keys=True)
        drain_events(d)
        send_key(d, "Escape")
        time.sleep(1.5)
        after = try_capture(capture)
        delivered = drain_events(d)
        received = terminal.read_pending(wait=0.5)
        closed = None
        if within_window(d.get_input_focus().focus, game.id):
            send_key(d, "Escape")
            time.sleep(1.5)
            closed = try_capture(capture)
        else:
            report.result("L3", "second Escape", "not sent",
                          "The nested focus left the game after the first Escape, so the second "
                          "was not sent. Close the game's menu yourself.")
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
    phases, samples = {}, {}
    for phase, instruction in (
            ("focused", "Switch to the game with Alt+Tab and leave mouse and keyboard alone."),
            ("unfocused", "Switch back to this terminal with Alt+Tab, keep the game visible, "
                          "and leave it alone.")):
        countdown(5, instruction)
        # 在这一段自己的焦点状态里取样。这一步开始前和结束后，焦点都在终端上，和失焦段
        # 一样，拿这两个时刻比只会比出"没有变化"。
        samples[phase] = gamescope_root_properties(d) if d is not None else {}
        report.result("L4", f"{phase} gamescope root properties", samples[phase])
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
        report.result("L4", "gamescope root properties, focused vs unfocused",
                      changed_properties(samples["focused"], samples["unfocused"]))


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
