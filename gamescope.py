# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""游戏所在的 gamescope：找到它，连上它的嵌套 X，用 gamescopectl 截图。

探测器（tools/linux_probe.py）先写了这些，之后跑在游戏上的脚本也要用，所以搬到这里，
行为不变。三件事：

    找游戏    /proc 里带这个 appid、又活在某个 gamescope 里的进程，按离它最近的
              gamescope 祖先分组。正常只有一组；不止一组说明有好几个实例在跑这个游戏，
              不能随便挑一个。
    嵌套 X    DISPLAY 和 XAUTHORITY 用游戏进程里的、宿主上用得了的那一份；游戏窗口按
              STEAM_GAME 属性或 WM_CLASS steam_app_<appid> 认。
    截图      gamescopectl screenshot，只发给这个实例（GAMESCOPE_WAYLAND_DISPLAY），
              等文件写完再读。给循环用的 ScreenshotCapture 还会切掉黑边、缩放回游戏
              自己的分辨率。

这里没有任何东西往游戏发输入。
"""

import os
import stat
import subprocess
import time
from pathlib import Path

import numpy as np

from applog import get_logger

log = get_logger(__name__)

APPID = "881020"
X11_SOCKET_DIR = Path("/tmp/.X11-unix")


# --- 找游戏：/proc 里的进程 --------------------------------------------------

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


# 决定连哪个 PipeWire 的环境变量（PipeWire 的 pipewire(1) 手册）：PIPEWIRE_RUNTIME_DIR 优先于
# XDG_RUNTIME_DIR 找套接字所在的目录，PIPEWIRE_REMOTE 是套接字的名字。
PIPEWIRE_VARS = ("PIPEWIRE_REMOTE", "PIPEWIRE_RUNTIME_DIR")


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
            **{key: env.get(key) for key in PIPEWIRE_VARS},
        })
    return sorted(found, key=lambda p: p["pid"])


def nearest_gamescope(pid, proc="/proc"):
    """离 pid 最近的 gamescope 祖先的进程号；链上没有 gamescope 就是 None。"""
    return next((a for a in process_lineage(pid, proc) if is_gamescope_process(a, proc)), None)


def group_instances(processes, proc="/proc"):
    """按所在的 gamescope 分组：离进程最近的 gamescope 祖先是谁，就归谁。正常只有一组。

    不能按环境变量分：Steam Linux Runtime 的容器（pressure-vessel）把容器里进程的
    GAMESCOPE_WAYLAND_DISPLAY 改成 /run/pressure-vessel/gamescope-socket，XAUTHORITY 改成
    /run/pressure-vessel/Xauthority，同一个 gamescope 里的进程就被分成了两组（2026-10-05 在
    这台机器上对着游戏实测）。链上找不到 gamescope 的进程，跟着同一个 DISPLAY 的那个
    gamescope 走；也对不上，就按 DISPLAY 自成一组。
    """
    owners = {p["pid"]: nearest_gamescope(p["pid"], proc) for p in processes}
    by_display = {}
    for p in processes:
        if owners[p["pid"]] is not None:
            by_display.setdefault(p["DISPLAY"], set()).add(owners[p["pid"]])
    groups = {}
    for p in processes:
        owner = owners[p["pid"]]
        if owner is None and len(by_display.get(p["DISPLAY"], ())) == 1:
            owner = next(iter(by_display[p["DISPLAY"]]))
        key = f"gamescope {owner}" if owner is not None else f"DISPLAY {p['DISPLAY']}"
        groups.setdefault(key, []).append(p)
    return groups


def instance_values(members):
    """一个 gamescope 里的进程报的 DISPLAY、GAMESCOPE_WAYLAND_DISPLAY、XAUTHORITY、
    XDG_RUNTIME_DIR 不一定一样：容器里的进程报的是容器里的路径，宿主上不存在。每样先取
    宿主上用得了的那个，都用不了再取第一个非空的。"""
    def pick(key, usable):
        values = [p[key] for p in members if p.get(key)]
        return next((v for v in values if usable(v)), values[0] if values else None)

    def x_socket(display):
        number = display_number(display)
        return number is not None and (X11_SOCKET_DIR / f"X{number}").exists()

    runtime = pick("XDG_RUNTIME_DIR", lambda v: Path(v).is_dir())
    return {
        "display": pick("DISPLAY", x_socket),
        "wayland": pick("GAMESCOPE_WAYLAND_DISPLAY",
                        lambda v: Path(v if v.startswith("/")
                                       else os.path.join(runtime or "", v)).exists()),
        "xauth": pick("XAUTHORITY", lambda v: Path(v).is_file()),
        "runtime": runtime,
    }


def display_number(display):
    """":3" 或 ":3.0" -> 3。认不出返回 None。"""
    if not display or not display.startswith(":"):
        return None
    number = display[1:].split(".", 1)[0]
    return int(number) if number.isdigit() else None


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

    游戏和 gamescope 之间隔着几层启动器，层数随启动方式而变，所以一直往上走。节点归谁
    只按进程号认；进程名只用来认出半路上的 gamescope（见 is_gamescope_process）。
    """
    chain = []
    current = pid
    while current is not None and current > 1 and current not in chain and len(chain) < limit:
        chain.append(current)
        current = parent_pid(current, proc)
    return chain


# gamescope 合成器进程的名字：主线程会把自己改名成 gamescope-wl。gamescopereaper 是它替
# 子进程收尸用的另一个程序，不是合成器。
GAMESCOPE_NAMES = frozenset({"gamescope", "gamescope-wl"})


def is_gamescope_process(pid, proc="/proc"):
    """pid 是不是一个 gamescope 合成器：进程名是 gamescope 或 gamescope-wl，或者可执行文件
    叫 gamescope（升级以后正在跑的旧文件，链接后面会带 " (deleted)"）。"""
    if read_proc_text(pid, "comm", proc) in GAMESCOPE_NAMES:
        return True
    try:
        target = os.readlink(Path(proc, str(pid), "exe"))
    except OSError:
        return False
    return Path(target.removesuffix(" (deleted)")).name == "gamescope"


# --- 嵌套的 X ----------------------------------------------------------------

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


def within_window(window, ancestor_id, max_depth=64, strict=False):
    """window 是 ancestor 本身，或者是它的子孙。沿 query_tree().parent 往上走，到根为止。

    XTest 的按键发给嵌套 X 的焦点窗口。焦点要是在别的窗口上（覆盖层、启动器、Wine 的
    对话框），Escape 就发到了游戏以外的地方，那边收到按键也不能算游戏收到。焦点也可能
    不是窗口，而是 None 或 PointerRoot 这样的常量，那同样不算。

    QueryTree 出错时默认当成"不在里面"。strict 为真就把异常抛出去，让调用方分清"窗口在
    半路没了"和"连接断了"：后者不该被当成一次普通的焦点不对。
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
            if strict:
                raise
            return False
        if current.id == tree.root.id:
            return False
        current = tree.parent
    return False


def describe_focus(focus):
    if hasattr(focus, "id"):
        return hex(focus.id)
    return {0: "None", 1: "PointerRoot"}.get(focus, repr(focus))


# --- gamescopectl ------------------------------------------------------------

def run_gamescopectl(args, wayland_display, runtime_dir=None, timeout=10, run=subprocess.run):
    """在指定的 gamescope 实例上跑 gamescopectl。不设 GAMESCOPE_WAYLAND_DISPLAY 的话，
    它会去连 gamescope-0，那可能是另一个游戏的实例。"""
    env = dict(os.environ, GAMESCOPE_WAYLAND_DISPLAY=wayland_display)
    if runtime_dir:
        env["XDG_RUNTIME_DIR"] = runtime_dir
    return run(["gamescopectl", *args], env=env, capture_output=True, text=True, timeout=timeout)


def wait_for_file(path, timeout, sleep=time.sleep, clock=time.monotonic, interval=0.2, stop=None):
    """gamescope 在后台线程里存图：命令返回时文件不一定写完。等它出现并且大小停止变化。

    stop 是一个返回真就不再等的函数：循环收到停止命令时，不该再为一帧等上几秒。这时也
    返回 False。
    """
    deadline = clock() + timeout
    last = -1
    while clock() < deadline:
        if stop is not None and stop():
            return False
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return True
        last = size
        sleep(interval)
    return False


def capture_gamescopectl(path, wayland_display, runtime_dir=None, timeout=10,
                         run=subprocess.run, sleep=time.sleep, stop=None):
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
    if not wait_for_file(path, timeout, sleep=sleep, stop=stop):
        raise RuntimeError(f"no screenshot file appeared (exit {result.returncode}, "
                           f"stdout {result.stdout.strip()[:120]!r}, "
                           f"stderr {result.stderr.strip()[:120]!r})")
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


# --- 给循环用的截图 -------------------------------------------------------------

# 截图放在 $XDG_RUNTIME_DIR 下的这个子目录里。XDG_RUNTIME_DIR 是 tmpfs，画面不落盘；目录
# 是 0700，别的用户看不到画面，也没法在里面放一个符号链接让 gamescope 把图写到别处。
CAPTURE_DIR_NAME = "gbfr-auto-linux"


class CaptureRefused(RuntimeError):
    """截图目录不安全，不往里面写。"""


def private_dir(base, name=CAPTURE_DIR_NAME):
    """base 下只有本用户能进的子目录，没有就建。

    已经存在的话，必须是自己的、真正的目录（不是链接），而且组和其他人没有任何权限，
    否则拒绝：gamescope 会往里面写截图，这边再从里面读回来。
    """
    path = Path(base, name)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise CaptureRefused(f"{path} 不是目录（可能是符号链接），不往里面写截图")
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise CaptureRefused(f"{path} 不只是本用户能进（属主 {st.st_uid}，权限 "
                             f"{stat.S_IMODE(st.st_mode):o}），不往里面写截图")
    return path


def fit_to_screen(frame, size):
    """把 gamescope 的截图变回游戏自己的分辨率。size 是 (宽, 高)，也就是嵌套 X 屏幕的大小。

    gamescope 截下来的是它输出画面的大小（2026-10-05 实测 2941x1653，游戏是 2560x1440），
    游戏的画面按比例放大，居中放在里面，比例不一样时两边或上下会有黑边。按同样的比例
    算出游戏那一块，切出来，再缩放回 size。之后模板匹配和点击坐标都在游戏自己的像素里。
    """
    import cv2

    out_h, out_w = frame.shape[:2]
    width, height = size
    if (out_w, out_h) == (width, height):
        return frame
    scale = min(out_w / width, out_h / height)
    content_w, content_h = round(width * scale), round(height * scale)
    x0, y0 = (out_w - content_w) // 2, (out_h - content_h) // 2
    content = np.ascontiguousarray(frame[y0:y0 + content_h, x0:x0 + content_w])
    # 缩小用 INTER_AREA，放大用 INTER_LINEAR：OpenCV 文档对两个方向各推荐的那个
    interpolation = cv2.INTER_AREA if scale > 1 else cv2.INTER_LINEAR
    return cv2.resize(content, (width, height), interpolation=interpolation)


class ScreenshotCapture:
    """循环用的截图：一次一张 gamescopectl screenshot，读完就删，切掉黑边、缩放成游戏的
    分辨率。

    调用一次返回一帧 (高, 宽, 3) 的 RGB 数组，截不到就返回 None，原因记进日志；连续几次
    截不到该怎么办由循环决定。directory 应该是 private_dir() 给的目录。stop 返回真时不再
    等这一帧。
    """

    def __init__(self, wayland_display, runtime_dir, size, directory, timeout=5.0, stop=None,
                 run=subprocess.run, sleep=time.sleep, clock=time.monotonic):
        self._wayland = wayland_display
        self._runtime = runtime_dir
        self._size = size
        self._path = Path(directory, "frame.png")
        self._timeout = timeout
        self._stop = stop
        self._run = run
        self._sleep = sleep
        self._clock = clock
        self.last_ms = None

    def __call__(self):
        start = self._clock()
        try:
            frame = capture_gamescopectl(self._path, self._wayland, self._runtime,
                                         timeout=self._timeout, run=self._run, sleep=self._sleep,
                                         stop=self._stop)
        except Exception as exc:
            if self._stop is not None and self._stop():
                log.info("收到停止，这一帧不等了")
            else:
                log.warning("截图失败: %s: %s", type(exc).__name__, str(exc)[:200])
            return None
        finally:
            # 画面读进内存就删：截图里是游戏画面，不该在目录里留着
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
        self.last_ms = round((self._clock() - start) * 1000)
        return fit_to_screen(frame, self._size)
