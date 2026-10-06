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
也会叫醒 wait：它拿现在的暂停状态和循环上一次读到的比，不一样就返回，所以在"看过了"和
"开始等"之间来的命令不会被睡过去，读别的开关也不会把它算成看过了。

协议：连上以后发一行，一个词；回一行 JSON。几道关：

  - 对端的 uid 要和自己一样（SO_PEERCRED 是内核给的，对端改不了），否则不理。
  - 套接字文件是 0600，放在 $XDG_RUNTIME_DIR 里。那个目录得是本用户的、组和其他人都进不去，
    否则不起来：bind 建出文件到 chmod 收紧之间，是目录挡着别人。不动 umask，那是整个进程
    共用的。
  - 同一时间只跑一个循环。先拿旁边锁文件的排他 flock，拿不到就是另一个正在跑，新的不起来；
    进程死了内核会替它放掉锁。拿到锁以后才去看套接字文件：连得上就是有别人在听（它没拿这把
    锁；发布过的版本都拿），不起来；连不上的是上次没清掉的，删掉重来；那个位置上要是别的东西
    （普通文件、链接、别人的套接字），不碰它，也不起来。两个同时启动的也不会都删掉同一个
    旧文件、各自起一个：先后由锁定。
  - 每个连接一个线程：一个连上了却不说话的客户端，挡不住后面的 stop。请求处理里出的错
    （比如 status 那个回调抛了异常）只落在那一个连接上，服务一直在。close() 返回之前，
    已经连上的也都断开、收完，收尾时不会再有命令进来。

日志：标准的处理器写不出去时，自己经 Handler.handleError 报到 stderr，不会抛到记日志的地方；
处理器要是卡住，每个要记日志的线程都会停在它那把锁上，循环自己每一轮也要记，整个进程都动不
了。这里不为这两种情况另做安排，只是起服务、起连接线程失败时，先把监听、文件、锁和线程表收拾
干净，再记日志：万一日志也出了错，留下的也是干净的。
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
        self._stop = False
        self._paused = False
        # 循环上一次读到的暂停状态。wait 拿现在的和它比；只有读 paused 才更新它，读
        # stop_requested 不会把一次没看到的暂停顺带算成看过了
        self._seen_paused = False
        self.stop_reason = None
        self.pause_reason = None

    @property
    def stop_requested(self):
        return self._stop

    @property
    def paused(self):
        value = self._paused
        self._seen_paused = value
        return value

    def state(self):
        """(paused, pause_reason)，给 status 用。不算"循环看过了"，所以不碰 _seen_paused。"""
        return self._paused, self.pause_reason

    def _changed(self):
        with self._cond:
            self._cond.notify_all()

    # 先叫醒等着的循环，再记日志：写日志的那个 handler 要是卡住或者出错，停下不能跟着晚

    def request_stop(self, reason):
        first = not self._stop
        if first:
            self._stop, self.stop_reason = True, reason
        self._changed()
        if first:
            log.info("要求停下：%s", reason)

    def pause(self, reason):
        self._paused, self.pause_reason = True, reason
        self._changed()
        log.info("暂停：%s", reason)

    def resume(self):
        self._paused, self.pause_reason = False, None
        self._changed()
        log.info("接着跑")

    def wait(self, seconds):
        """最多等 seconds 秒。要求停下了，或者暂停状态和循环上一次读到的不一样了，就提前返回。

        暂停了又接着跑、而循环一次都没读到，就当没发生：状态和它看到的一样，没有要办的事。
        """
        with self._cond:
            self._cond.wait_for(lambda: self._stop or self._paused != self._seen_paused,
                                timeout=seconds)


# Linux 的 struct ucred：pid_t（有符号）、uid_t、gid_t（无符号），各 32 位。uid 按有符号读，
# 2^31 以上的 uid 就成了负数，和 os.getuid() 永远对不上
UCRED = struct.Struct("=iII")


def _peer_uid(conn):
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, UCRED.size)
    return UCRED.unpack(creds)[1]


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
    它不该阻塞：close() 会一直等到正在调它的连接线程结束。
    """

    def __init__(self, controls, path, status=None):
        self._controls = controls
        self._path = Path(path)
        self._status = status or (lambda: {})
        self._sock = None
        self._thread = None
        self._identity = None      # bind 建出的那个文件：(st_dev, st_ino)
        self._lock = None
        self._closing = threading.Event()
        self._workers = {}          # 线程 -> 它的连接
        self._workers_lock = threading.Lock()

    def start(self):
        self._check_dir()
        self._take_lock()
        try:
            self._claim()
            self._listen()
            self._thread = threading.Thread(target=self._serve, name="gbfr-control", daemon=True)
            self._thread.start()
            log.info("控制套接字在 %s", self._path)
        except BaseException:
            # 起到一半失败（比如线程数到了上限，或者记日志出错）：监听的套接字、文件和锁都收回，
            # 免得留下一个没人服务、却让后来者以为"另一个在跑"的空壳
            self.close()
            raise
        return self

    def _check_dir(self):
        """套接字所在的目录得是本用户的，组和其他人都进不去。bind 建出文件到 chmod 之间，
        靠的就是它；也就不用去动整个进程共用的 umask。"""
        parent = self._path.parent
        st = os.stat(parent)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise RuntimeError(f"{parent} 不只是本用户能进（属主 {st.st_uid}，权限 "
                               f"{stat.S_IMODE(st.st_mode):o}），控制套接字不放在这里")

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
        self._sock = sock
        sock.bind(str(self._path))
        st = self._path.lstat()
        self._identity = (st.st_dev, st.st_ino)
        os.chmod(self._path, 0o600)
        sock.listen(8)
        sock.settimeout(0.5)

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
            worker = threading.Thread(target=self._handle_safely, args=(conn,),
                                      name="gbfr-control-conn", daemon=True)
            with self._workers_lock:
                if self._closing.is_set():
                    conn.close()
                    break
                self._workers[worker] = conn
            try:
                worker.start()
            except RuntimeError:
                # 先收拾再记日志：没起来的线程留在表里，close() 去 join 它会出错
                with self._workers_lock:
                    self._workers.pop(worker, None)
                conn.close()
                log.warning("起不了处理连接的线程，这个连接作废", exc_info=True)

    def _handle_safely(self, conn):
        try:
            with conn:
                self._handle(conn)
        except Exception:
            log.warning("控制连接出错，这一个连接作废，服务照常", exc_info=True)
        finally:
            with self._workers_lock:
                self._workers.pop(threading.current_thread(), None)

    def _handle(self, conn):
        conn.settimeout(CONNECTION_TIMEOUT)
        uid = _peer_uid(conn)
        if uid != os.getuid():
            log.warning("别的用户（uid %d）连了控制套接字，不理", uid)
            return
        command = _read_line(conn)
        if self._closing.is_set():
            # 已经在收尾：读到的命令不再办
            conn.sendall(b'{"ok": false, "error": "closing"}\n')
            return
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
            # 回调给的放在前面：它要是也带了 ok、paused 这些键，以这边的为准
            reply = {**extra, "ok": True, "paused": paused, "pause_reason": reason}
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
            # 文件是不是自己建的那一个，趁监听还占着它的 inode 时认：关掉以后，别人删了重建的
            # 新文件可能马上拿到同一个 inode 号
            self._unlink_own()
            self._sock.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        # 已经连上的也断开、等它们收完：close() 返回以后，不能再有命令拨动开关
        with self._workers_lock:
            workers = list(self._workers.items())
        for _, conn in workers:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # 不设上限地等：连接已经断开，读写都会立刻出错，命令也不再办。还能让一个工作线程活着的，
        # 是它正在调的 status 回调，或者卡住的日志处理器。close() 返回以后不能再有应用的代码在
        # 这些线程里跑，所以要等回调返回；回调不该阻塞，这是调用方的事。日志卡住时等也没用：
        # 处理器靠一把锁排队，循环每一轮都要记日志，退出时 logging 还要刷同一批处理器，整个
        # 进程本来就动不了
        for worker, _ in workers:
            worker.join()
        self._drop_lock()

    def _unlink_own(self):
        """只删自己建的那一个：期间要是被换掉了（另一个循环删了重建），留给它。"""
        try:
            st = self._path.lstat()
            if self._identity is not None and (st.st_dev, st.st_ino) == self._identity:
                self._path.unlink()
        except FileNotFoundError:
            pass


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
