# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""control.py：停下、暂停的开关，和那条本地套接字。

套接字是真建的（在 tmp_path 里），客户端也是真连的，所以协议、文件权限、对端 uid 和
"同一时间只跑一个"这几条，是对着内核测的，不是对着替身。
"""

import fcntl
import json
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

import control


@pytest.fixture
def path(tmp_path):
    return tmp_path / "c.sock"


def serve(path, status=None):
    controls = control.Controls()
    return control.ControlServer(controls, path, status=status).start(), controls


def _quietly(action, *args):
    try:
        action(*args)
    except (OSError, ValueError):
        pass


def elapsed(action):
    start = time.monotonic()
    action()
    return time.monotonic() - start


class TestControls:
    def test_a_stop_ends_a_wait_at_once(self):
        c = control.Controls()
        threading.Timer(0.05, c.request_stop, args=("test",)).start()
        assert elapsed(lambda: c.wait(5)) < 1 and c.stop_requested

    def test_a_stop_that_came_before_the_wait_is_not_slept_through(self):
        """哪怕循环已经看到了停止还去等（看过以后版本就不算"变了"），也立刻返回。"""
        c = control.Controls()
        c.request_stop("test")
        assert c.stop_requested
        assert elapsed(lambda: c.wait(5)) < 0.5

    def test_a_pause_wakes_a_wait_too(self):
        c = control.Controls()
        threading.Timer(0.05, c.pause, args=("test",)).start()
        assert elapsed(lambda: c.wait(5)) < 1 and c.paused and c.pause_reason == "test"

    def test_without_a_change_the_wait_takes_its_time(self):
        c = control.Controls()
        assert elapsed(lambda: c.wait(0.2)) >= 0.19

    def test_a_pause_between_the_loop_s_look_and_its_wait_is_not_slept_through(self):
        """循环看过 paused（还是 False）之后、开始等之前来了暂停：wait 不能再睡满一轮。"""
        c = control.Controls()
        assert not c.paused
        c.pause("test")
        assert elapsed(lambda: c.wait(5)) < 0.5

    def test_a_status_read_does_not_hide_a_change_from_the_loop(self):
        c = control.Controls()
        assert not c.paused
        c.pause("test")
        assert c.state() == (True, "test")
        assert elapsed(lambda: c.wait(5)) < 0.5

    def test_reading_the_other_switch_does_not_count_as_seeing_a_pause(self):
        """读过 paused（False），来了暂停，再读 stop_requested：暂停还是没看到，wait 得醒。"""
        c = control.Controls()
        assert not c.paused
        c.pause("test")
        assert not c.stop_requested
        assert elapsed(lambda: c.wait(5)) < 0.5

    def test_once_the_loop_has_seen_the_state_it_waits_again(self):
        c = control.Controls()
        c.pause("test")
        assert c.paused
        assert elapsed(lambda: c.wait(0.2)) >= 0.19

    def test_resume_clears_the_pause(self):
        c = control.Controls()
        c.pause("test")
        c.resume()
        assert not c.paused and c.pause_reason is None and not c.stop_requested


class TestTheSocket:
    def test_commands_reach_the_controls(self, path):
        srv, c = serve(path)
        try:
            assert control.send("pause", path) == {"ok": True} and c.paused
            assert control.send("resume", path) == {"ok": True} and not c.paused
            assert control.send("stop", path) == {"ok": True} and c.stop_requested
        finally:
            srv.close()

    def test_status_carries_the_loop_s_state(self, path):
        srv, _ = serve(path, status=lambda: {"page": "battle", "battles": 2})
        try:
            reply = control.send("status", path)
        finally:
            srv.close()
        assert reply == {"ok": True, "paused": False, "pause_reason": None, "page": "battle",
                         "battles": 2}

    def test_an_unknown_line_is_answered_with_a_refusal(self, path):
        srv, c = serve(path)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect(str(path))
                s.sendall(b"rm -rf\n")
                data = s.recv(4096)
        finally:
            srv.close()
        assert json.loads(data)["ok"] is False and not c.stop_requested

    def test_the_client_refuses_unknown_commands_itself(self, path):
        with pytest.raises(ValueError, match="unknown command"):
            control.send("shutdown", path)

    def test_the_socket_file_is_for_this_user_only(self, path):
        srv, _ = serve(path)
        try:
            st = path.lstat()
            assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
        finally:
            srv.close()

    @pytest.mark.parametrize("mode", [0o755, 0o750, 0o711])
    def test_a_directory_others_can_enter_is_refused(self, tmp_path, mode):
        """bind 建出文件到 chmod 之间，挡住别人的是目录；目录挡不住，就不起来。"""
        shared = tmp_path / "shared"
        shared.mkdir()
        shared.chmod(mode)
        with pytest.raises(RuntimeError, match=f"{mode:o}"):
            serve(shared / "c.sock")
        assert not os.path.lexists(shared / "c.sock")

    def test_the_umask_is_left_alone(self, path, monkeypatch):
        """umask 是整个进程共用的，别的线程这时候建的文件也会被它管。"""
        monkeypatch.setattr(control.os, "umask", lambda *a: pytest.fail("umask changed"))
        srv, _ = serve(path)
        srv.close()

    def test_another_user_gets_no_answer(self, path, monkeypatch):
        """测试里换不了 uid，所以反过来：让服务端以为自己是另一个 uid。"""
        srv, c = serve(path)
        real = os.getuid()
        monkeypatch.setattr(control.os, "getuid", lambda: real + 1)
        try:
            with pytest.raises(ConnectionError):
                control.send("stop", path)
        finally:
            monkeypatch.undo()
            srv.close()
        assert not c.stop_requested

    def test_a_second_loop_cannot_start(self, path):
        srv, _ = serve(path)
        try:
            with pytest.raises(control.AlreadyRunning):
                serve(path)
        finally:
            srv.close()

    def test_a_loop_holding_the_lock_keeps_others_away_from_the_socket(self, path):
        """两个同时启动、又都看到同一个旧套接字时，只有拿到锁的那个去动它。"""
        leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        leftover.bind(str(path))
        leftover.close()
        inode = path.lstat().st_ino
        holder = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            with pytest.raises(control.AlreadyRunning, match="锁"):
                serve(path)
            assert path.lstat().st_ino == inode
        finally:
            os.close(holder)

    def test_the_lock_is_let_go_on_close(self, path):
        first, _ = serve(path)
        first.close()
        second, _ = serve(path)
        second.close()

    def test_a_symlinked_lock_file_is_refused(self, path, tmp_path):
        target = tmp_path / "elsewhere"
        target.write_text("keep me")
        path.with_suffix(".lock").symlink_to(target)
        with pytest.raises(OSError):
            serve(path)
        assert target.read_text() == "keep me" and not os.path.lexists(path)

    def test_a_listener_without_the_lock_is_not_replaced(self, path):
        """拿到了锁，可套接字上有人在听（不用这把锁的老版本，或者别的程序）：不顶掉它。"""
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        inode = path.lstat().st_ino
        try:
            with pytest.raises(control.AlreadyRunning, match="有人在听"):
                serve(path)
            assert path.lstat().st_ino == inode
        finally:
            listener.close()

    def test_a_socket_left_over_from_a_crash_is_replaced(self, path):
        leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        leftover.bind(str(path))
        leftover.close()
        assert os.path.lexists(path)
        srv, _ = serve(path)
        try:
            assert control.send("status", path)["ok"]
        finally:
            srv.close()

    def test_a_file_in_its_place_is_left_alone(self, path):
        path.write_text("keep me")
        with pytest.raises(RuntimeError, match="别的东西"):
            serve(path)
        assert path.read_text() == "keep me"

    def test_a_symlink_in_its_place_is_left_alone(self, path, tmp_path):
        target = tmp_path / "target"
        target.write_text("keep me")
        path.symlink_to(target)
        with pytest.raises(RuntimeError, match="别的东西"):
            serve(path)
        assert path.is_symlink() and target.read_text() == "keep me"

    def test_a_silent_client_does_not_hold_up_a_stop(self, path):
        """连上了却一个字不发（挂起了、崩了）：后面的 stop 照样一秒内送到。"""
        srv, c = serve(path)
        silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        silent.connect(str(path))
        try:
            time.sleep(0.05)
            assert elapsed(lambda: control.send("stop", path)) < 0.5
            assert c.stop_requested
        finally:
            silent.close()
            srv.close()

    def test_a_failing_status_does_not_take_the_server_down(self, path):
        def broken():
            raise RuntimeError("no state yet")
        srv, c = serve(path, status=broken)
        try:
            reply = control.send("status", path)
            assert reply["ok"] and "no state yet" in reply["status_error"]
            assert control.send("stop", path) == {"ok": True} and c.stop_requested
        finally:
            srv.close()

    def test_the_status_callback_cannot_overwrite_the_reply_s_own_fields(self, path):
        srv, c = serve(path, status=lambda: {"ok": False, "paused": False,
                                             "pause_reason": "made up", "page": "battle"})
        c.pause("real")
        try:
            reply = control.send("status", path)
        finally:
            srv.close()
        assert reply == {"ok": True, "paused": True, "pause_reason": "real", "page": "battle"}

    def test_a_status_that_json_cannot_hold_is_still_answered(self, path):
        srv, _ = serve(path, status=lambda: {"held": {"w"}})
        try:
            assert control.send("status", path)["held"] == "{'w'}"
        finally:
            srv.close()

    def test_a_connection_open_at_close_cannot_send_a_command_after_it(self, path):
        """close() 返回以后，已经连上的那个也不能再拨开关：应用这时候正在收尾。"""
        srv, c = serve(path)
        late = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        late.connect(str(path))
        time.sleep(0.05)
        srv.close()
        assert not [t for t in threading.enumerate() if t.name == "gbfr-control-conn"]
        try:
            late.sendall(b"stop\n")
        except OSError:
            pass
        finally:
            late.close()
        time.sleep(0.05)
        assert not c.stop_requested

    def test_close_waits_for_a_slow_status_callback_to_finish(self, path, monkeypatch):
        """status 回调比连接超时还慢：close() 也得等它返回，之后不能再有应用的代码在跑。"""
        monkeypatch.setattr(control, "CONNECTION_TIMEOUT", 0.1)
        entered, finished = threading.Event(), threading.Event()

        def slow_status():
            entered.set()
            time.sleep(1.5)
            finished.set()
            return {}
        srv, _ = serve(path, status=slow_status)
        client = threading.Thread(target=lambda: _quietly(control.send, "status", path))
        client.start()
        try:
            assert entered.wait(2)
        finally:
            srv.close()
        assert finished.is_set()
        assert not [t for t in threading.enumerate() if t.name == "gbfr-control-conn"]
        client.join(5)

    def test_a_command_read_while_closing_is_not_carried_out(self, path, monkeypatch):
        """连接线程刚读到命令，close() 就开始了：这条命令不再办。"""
        srv, c = serve(path)
        reading = threading.Event()

        def read_as_close_begins(conn):
            reading.set()
            srv._closing.wait(2)
            return "stop"
        monkeypatch.setattr(control, "_read_line", read_as_close_begins)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(path))
        try:
            assert reading.wait(2)
            srv.close()
        finally:
            client.close()
        assert not c.stop_requested

    def test_a_thread_that_cannot_start_leaves_nothing_behind(self, path, monkeypatch):
        """起监听线程失败（线程数到了上限）：不能留下一个没人服务、却挡着后来者的空壳。"""
        def no_threads(self):
            raise RuntimeError("can't start new thread")
        monkeypatch.setattr(control.threading.Thread, "start", no_threads)
        with pytest.raises(RuntimeError, match="new thread"):
            serve(path)
        monkeypatch.undo()
        assert not os.path.lexists(path)
        srv, _ = serve(path)
        srv.close()

    def test_close_removes_the_socket_and_nobody_answers_after(self, path):
        srv, _ = serve(path)
        srv.close()
        assert not os.path.lexists(path)
        with pytest.raises(FileNotFoundError):
            control.send("status", path)

    def test_close_leaves_a_replacement_alone(self, path):
        """期间别的循环删了这个文件、建了自己的：收尾时不能把它的删掉。"""
        srv, _ = serve(path)
        path.unlink()
        other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        other.bind(str(path))
        try:
            srv.close()
            assert os.path.lexists(path)
        finally:
            other.close()

    def test_the_path_lives_in_the_runtime_dir(self, monkeypatch):
        assert control.socket_path("/run/user/1000") == Path("/run/user/1000/gbfr-auto-linux.sock")
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        with pytest.raises(RuntimeError, match="XDG_RUNTIME_DIR"):
            control.socket_path()
