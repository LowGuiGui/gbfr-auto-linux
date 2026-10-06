# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""停下、暂停、接着跑：循环那边的开关，和从别处拨动它的一条本地套接字。

GNOME 的 Wayland 会话里，Python 拿不到全局热键：pynput 只看得到发给 XWayland 窗口的键。所以
停下和暂停走一条 Unix 套接字。循环开着 $XDG_RUNTIME_DIR/gbfr-auto-linux.sock，命令行的
stop、pause、resume、status 连过去发一个词；所有者再把 stop 和 pause 绑到 GNOME 的自定义
快捷键上（设置、键盘、自定义快捷键），在哪个窗口里都按得到。终端里的 Ctrl+C 也是停下。

Controls 是循环看的那一面（farm.Farm 的 controls）：stop_requested、paused 两个属性，
pause(理由)，以及 wait(秒)。停止命令一到，wait 立刻返回，停下不用等完一整轮。暂停和接着跑
也会叫醒 wait，而且从循环上一次看这两个属性的那一刻算起：在"看过了"和"开始等"之间来的
命令，不会被睡过去。

协议：连上以后发一行，一个词；回一行 JSON。几道关：

  - 对端的 uid 要和自己一样（SO_PEERCRED 是内核给的，对端改不了），否则不理。
  - 套接字文件是 0600，放在 $XDG_RUNTIME_DIR 里（本用户的 0700 目录）。
  - 同一时间只跑一个循环。先拿旁边锁文件的排他 flock，拿不到就是另一个正在跑，新的不起来；
    进程死了内核会替它放掉锁。拿到锁以后才去看套接字文件：连得上就是另一个循环（没用这把
    锁的老版本），不起来；连不上的是上次没清掉的，删掉重来；那个位置上要是别的东西
    （普通文件、链接、别人的套接字），不碰它，也不起来。两个同时启动的也不会都删掉同一个
    旧文件、各自起一个：先后由锁定。
  - 每个连接一个线程：一个连上了却不说话的客户端，挡不住后面的 stop。请求处理里出的错
    （比如 status 那个回调抛了异常）只落在那一个连接上，服务一直在。
"""

import fcntl
import json
import os
import socket
import stat
import struct
import threading
from pathlib import Path

from applog import get_logger

log = get_logger(__name__)

SOCKET_NAME = "gbfr-auto-linux.sock"
COMMANDS = ("stop", "pause", "resume", "status")
# 一条命令最多这么长。一个词加换行用不了这么多，多出来的不是这边的客户端。
MAX_LINE = 64
# 一个连接最多等这么久把那一行发完。每个连接各有各的线程，慢的只耽误它自己。
CONNECTION_TIMEOUT = 1.0


class AlreadyRunning(RuntimeError):
    """另一个循环已经开着控制套接字。"""


def socket_path(runtime_dir=None):
    base = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        raise RuntimeError("没有 XDG_RUNTIME_DIR，不知道控制套接字该放在哪里")
    return Path(base, SOCKET_NAME)


class Controls:
    """停下和暂停的开关。哪个线程都可以拨，循环自己的线程来看。"""

    def __init__(self):
        self._cond = threading.Condition()
        self._version = 0
        self._seen = 0      # 循环上一次看 stop_requested 或 paused 时的版本
        self._stop = False
        self._paused = False
        self.stop_reason = None
        self.pause_reason = None

    @property
    def stop_requested(self):
        self._seen = self._version
        return self._stop

    @property
    def paused(self):
        self._seen = self._version
        return self._paused

    def state(self):
        """(paused, pause_reason)，给 status 用。不算"循环看过了"，所以不碰 _seen。"""
        return self._paused, self.pause_reason

    def _changed(self):
        with self._cond:
            self._version += 1
            self._cond.notify_all()

    def request_stop(self, reason):
        if not self._stop:
            self._stop, self.stop_reason = True, reason
            log.info("要求停下：%s", reason)
        self._changed()

    def pause(self, reason):
        self._paused, self.pause_reason = True, reason
        log.info("暂停：%s", reason)
        self._changed()

    def resume(self):
        self._paused, self.pause_reason = False, None
        log.info("接着跑")
        self._changed()

    def wait(self, seconds):
        """最多等 seconds 秒。要求停下了，或者自循环上一次看过开关以来暂停、接着跑过，就
        提前返回。"""
        with self._cond:
            start = self._seen
            self._cond.wait_for(lambda: self._stop or self._version != start, timeout=seconds)


def _peer_uid(conn):
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def _read_line(conn):
    data = b""
    while b"\n" not in data and len(data) <= MAX_LINE:
        chunk = conn.recv(MAX_LINE + 1)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0].decode("utf-8", "replace").strip() if b"\n" in data else None


class ControlServer:
    """在后台线程里收命令。start() 开始听，close() 收掉，并删掉自己建的套接字文件。

    status 返回一个可以转成 JSON 的 dict，status 命令把它原样带回去（页面、完成次数……）。
    """

    def __init__(self, controls, path, status=None):
        self._controls = controls
        self._path = Path(path)
        self._status = status or (lambda: {})
        self._sock = None
        self._thread = None
        self._inode = None
        self._lock = None
        self._closing = threading.Event()

    def start(self):
        self._take_lock()
        try:
            self._claim()
            self._listen()
        except BaseException:
            self._drop_lock()
            raise
        self._thread = threading.Thread(target=self._serve, name="gbfr-control", daemon=True)
        self._thread.start()
        log.info("控制套接字在 %s", self._path)
        return self

    def _take_lock(self):
        """锁文件和套接字放在一起，O_NOFOLLOW：那个位置上是链接的话，宁可起不来。"""
        lock_path = self._path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise AlreadyRunning(f"{lock_path} 被别的进程锁着：另一个循环正在跑") from None
        self._lock = fd

    def _drop_lock(self):
        # 锁文件留在那里不删：删掉再建，两个进程就可能各锁一个文件
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            os.close(self._lock)
            self._lock = None

    def _listen(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # bind 按 umask 建文件；先收紧再 bind，免得有一小段时间别人也能连
        old_umask = os.umask(0o177)
        try:
            sock.bind(str(self._path))
        finally:
            os.umask(old_umask)
        os.chmod(self._path, 0o600)
        self._inode = self._path.lstat().st_ino
        sock.listen(8)
        sock.settimeout(0.5)
        self._sock = sock

    def _claim(self):
        if not os.path.lexists(self._path):
            return
        st = self._path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f"{self._path} 上已经有别的东西（不是本用户的套接字），不碰它")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(str(self._path))
        except (ConnectionRefusedError, FileNotFoundError):
            log.info("删掉上次留下的控制套接字 %s", self._path)
            self._path.unlink()
            return
        finally:
            probe.close()
        raise AlreadyRunning(f"{self._path} 有人在听：另一个循环正在跑")

    def _serve(self):
        while not self._closing.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_safely, args=(conn,), name="gbfr-control-conn",
                             daemon=True).start()

    def _handle_safely(self, conn):
        with conn:
            try:
                self._handle(conn)
            except Exception:
                log.warning("控制连接出错，这一个连接作废，服务照常", exc_info=True)

    def _handle(self, conn):
        conn.settimeout(CONNECTION_TIMEOUT)
        uid = _peer_uid(conn)
        if uid != os.getuid():
            log.warning("别的用户（uid %d）连了控制套接字，不理", uid)
            return
        command = _read_line(conn)
        if command == "stop":
            self._controls.request_stop("收到 stop 命令")
            reply = {"ok": True}
        elif command == "pause":
            self._controls.pause("收到 pause 命令")
            reply = {"ok": True}
        elif command == "resume":
            self._controls.resume()
            reply = {"ok": True}
        elif command == "status":
            paused, reason = self._controls.state()
            try:
                extra = dict(self._status())
            except Exception as exc:
                log.warning("取状态失败", exc_info=True)
                extra = {"status_error": f"{type(exc).__name__}: {exc}"}
            reply = {"ok": True, "paused": paused, "pause_reason": reason, **extra}
        else:
            reply = {"ok": False, "error": f"unknown command {command!r}"}
        conn.sendall((json.dumps(reply, ensure_ascii=False, default=str) + "\n").encode("utf-8"))

    def close(self):
        self._closing.set()
        if self._sock is not None:
            # 先 shutdown：Linux 上它会让阻塞着的 accept 立刻返回，不用等超时
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        # 只删自己建的那一个：期间要是被换掉了（另一个循环删了重建），留给它
        try:
            if self._inode is not None and self._path.lstat().st_ino == self._inode:
                self._path.unlink()
        except FileNotFoundError:
            pass
        self._drop_lock()


def send(command, path, timeout=3):
    """连到正在跑的循环，发一个命令，返回它回的 dict。

    没有循环在跑时，抛 FileNotFoundError（没有套接字文件）或者 ConnectionRefusedError
    （文件是上次留下的）。
    """
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}: one of {', '.join(COMMANDS)}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall((command + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        raise ConnectionError("控制套接字没有回话（对端可能不认这个用户）")
    return json.loads(data.decode("utf-8"))
