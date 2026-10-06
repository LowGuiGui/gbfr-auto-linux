# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""control.py：停下、暂停的开关，和那条本地套接字。

套接字是真建的（在 tmp_path 里），客户端也是真连的，所以协议、文件权限、对端 uid 和
"同一时间只跑一个"这几条，是对着内核测的，不是对着替身。
"""

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
        c = control.Controls()
        c.request_stop("test")
        assert elapsed(lambda: c.wait(5)) < 0.5

    def test_a_pause_wakes_a_wait_too(self):
        c = control.Controls()
        threading.Timer(0.05, c.pause, args=("test",)).start()
        assert elapsed(lambda: c.wait(5)) < 1 and c.paused and c.pause_reason == "test"

    def test_without_a_change_the_wait_takes_its_time(self):
        c = control.Controls()
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

    def test_the_file_is_private_from_the_moment_it_exists(self, path, monkeypatch):
        """bind 建文件时就得是 0600，不能等后面的 chmod：中间那一小段别人也连得上。"""
        monkeypatch.setattr(control.os, "chmod", lambda *a, **k: None)
        srv, _ = serve(path)
        try:
            assert stat.S_IMODE(path.lstat().st_mode) == 0o600
        finally:
            monkeypatch.undo()
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
