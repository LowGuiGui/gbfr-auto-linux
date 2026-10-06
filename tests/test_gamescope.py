# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""gamescope.py：找游戏所在的 gamescope、连嵌套 X、gamescopectl 截图。

这些原来是探测器的一部分，测试也是从 test_linux_probe.py 原样搬过来的。/proc 和
subprocess.run 的替身（fake_proc、FakeRun）和 GAME_ENV 留在这里，探测器的测试从这里拿。
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import gamescope as gs


def fake_proc(tmp_path, processes):
    """processes: {pid: {"environ": dict | None, "comm": str, "cmdline": str, "ppid": int}}

    status 的格式照内核：每行 "键:\\t值"，PPid 是父进程号。
    """
    root = tmp_path / "proc"
    root.mkdir()
    (root / "self").mkdir()
    for pid, info in processes.items():
        d = root / str(pid)
        d.mkdir()
        if info.get("environ") is not None:
            raw = b"".join(f"{k}={v}".encode() + b"\0" for k, v in info["environ"].items())
            (d / "environ").write_bytes(raw)
        (d / "comm").write_text(info.get("comm", "x") + "\n")
        (d / "cmdline").write_bytes(info.get("cmdline", "x").replace(" ", "\0").encode())
        if "ppid" in info:
            (d / "status").write_text(f"Name:\t{info.get('comm', 'x')}\nUmask:\t0002\n"
                                      f"State:\tS (sleeping)\nTgid:\t{pid}\nNgid:\t0\n"
                                      f"Pid:\t{pid}\nPPid:\t{info['ppid']}\nTracerPid:\t0\n")
    return root


GAME_ENV = {"SteamAppId": "881020", "DISPLAY": ":3", "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-1",
            "XAUTHORITY": "/run/user/1000/xauth_test", "XDG_RUNTIME_DIR": "/run/user/1000"}


class TestFindingTheGame:
    def test_only_processes_inside_gamescope_count(self, tmp_path):
        """gamescope 自己也带 SteamAppId，但它的 DISPLAY 是宿主的；拿它当游戏会连错显示。"""
        proc = fake_proc(tmp_path, {
            100: {"environ": {"SteamAppId": "881020", "DISPLAY": ":0"}, "comm": "gamescope"},
            101: {"environ": GAME_ENV, "comm": "granblue_fantas",
                  "cmdline": "Z:\\game\\granblue_fantasy_relink.exe"},
            102: {"environ": dict(GAME_ENV, SteamAppId="", SteamGameId="881020"),
                  "comm": "mangoapp"},
            200: {"environ": {"SteamAppId": "2060160", "DISPLAY": ":2",
                              "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}},
            300: {"environ": None},
        })
        found = gs.find_game_processes("881020", proc)
        assert [p["pid"] for p in found] == [101, 102]
        assert found[0]["DISPLAY"] == ":3"
        assert found[0]["XAUTHORITY"] == "/run/user/1000/xauth_test"
        assert "granblue_fantasy_relink.exe" in found[0]["cmdline"]

    def test_one_instance_is_one_group(self, tmp_path):
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV},
                                    102: {"environ": GAME_ENV}})
        groups = gs.group_instances(gs.find_game_processes("881020", proc), proc)
        assert list(groups) == ["DISPLAY :3"]
        assert len(groups["DISPLAY :3"]) == 2

    def test_two_instances_are_kept_apart(self, tmp_path):
        """两个实例必须报出来，而不是随便挑一个去量。"""
        other = dict(GAME_ENV, DISPLAY=":4", GAMESCOPE_WAYLAND_DISPLAY="gamescope-2")
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV}, 105: {"environ": other}})
        assert len(gs.group_instances(gs.find_game_processes("881020", proc), proc)) == 2

    def test_the_steam_runtime_container_does_not_split_one_gamescope(self, tmp_path):
        """2026-10-05 对着游戏实测：容器外的进程报 gamescope-0，Steam Linux Runtime 容器
        里的进程报 /run/pressure-vessel/gamescope-socket，XAUTHORITY 也换成了容器里的路径。
        按环境变量分组会说"两个实例在跑这个游戏"，其实只有一个 gamescope。"""
        host = dict(GAME_ENV, DISPLAY=":2", GAMESCOPE_WAYLAND_DISPLAY="gamescope-0")
        inside = dict(host, GAMESCOPE_WAYLAND_DISPLAY="/run/pressure-vessel/gamescope-socket",
                      XAUTHORITY="/run/pressure-vessel/Xauthority")
        proc = fake_proc(tmp_path, {
            1500: {"ppid": 1, "comm": "steam"},
            2000: {"ppid": 1500, "comm": "gamescope-wl"},
            2100: {"ppid": 2000, "comm": "reaper", "environ": host},
            2200: {"ppid": 2100, "comm": "srt-bwrap", "environ": inside},
            2300: {"ppid": 2200, "comm": "granblue_fantas", "environ": inside},
        })
        groups = gs.group_instances(gs.find_game_processes("881020", proc), proc)
        assert list(groups) == ["gamescope 2000"]
        assert [m["pid"] for m in groups["gamescope 2000"]] == [2100, 2200, 2300]

    def test_a_process_without_a_gamescope_ancestor_joins_its_display_s(self, tmp_path):
        """父进程先退出、被托管到 gamescope 外面的进程，跟着同一个 DISPLAY 的 gamescope 走。"""
        proc = fake_proc(tmp_path, {
            2000: {"ppid": 1, "comm": "gamescope-wl"},
            2100: {"ppid": 2000, "environ": GAME_ENV},
            2400: {"ppid": 1, "environ": GAME_ENV},
        })
        groups = gs.group_instances(gs.find_game_processes("881020", proc), proc)
        assert list(groups) == ["gamescope 2000"] and len(groups["gamescope 2000"]) == 2

    def test_values_that_exist_on_the_host_win(self, tmp_path, monkeypatch):
        """容器里报的路径在宿主上不存在；gamescopectl 要的是宿主上的套接字名。"""
        runtime = tmp_path / "run-user"
        runtime.mkdir()
        (runtime / "gamescope-0").touch()
        host_auth = tmp_path / "host-auth"
        host_auth.touch()
        x11 = tmp_path / "x11"
        x11.mkdir()
        (x11 / "X2").touch()
        monkeypatch.setattr(gs, "X11_SOCKET_DIR", x11)
        inside = {"DISPLAY": ":99", "GAMESCOPE_WAYLAND_DISPLAY": "/run/pressure-vessel/gamescope-socket",
                  "XAUTHORITY": "/run/pressure-vessel/Xauthority", "XDG_RUNTIME_DIR": str(runtime)}
        host = dict(inside, DISPLAY=":2", GAMESCOPE_WAYLAND_DISPLAY="gamescope-0",
                    XAUTHORITY=str(host_auth))
        # 容器里的进程排在前面也不行
        assert gs.instance_values([inside, host]) == {
            "display": ":2", "wayland": "gamescope-0", "xauth": str(host_auth),
            "runtime": str(runtime)}

    def test_with_nothing_usable_the_first_value_is_kept(self, tmp_path):
        only = {"DISPLAY": ":9", "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-9",
                "XAUTHORITY": str(tmp_path / "none"), "XDG_RUNTIME_DIR": str(tmp_path / "none")}
        assert gs.instance_values([only]) == {
            "display": ":9", "wayland": "gamescope-9", "xauth": str(tmp_path / "none"),
            "runtime": str(tmp_path / "none")}

    def test_environ_parsing_keeps_odd_bytes_and_skips_junk(self, tmp_path):
        proc = tmp_path / "proc"
        (proc / "7").mkdir(parents=True)
        (proc / "7" / "environ").write_bytes(b"A=1\0B=x=y\0NOEQUALS\0\0C=\xff\0")
        env = gs.read_environ(7, proc)
        assert env["A"] == "1"
        assert env["B"] == "x=y"
        assert "NOEQUALS" not in env
        assert env["C"].encode("utf-8", "surrogateescape") == b"\xff"

    def test_a_vanished_process_reads_as_none(self, tmp_path):
        assert gs.read_environ(12345, tmp_path) is None

    @pytest.mark.parametrize("display, number", [
        (":3", 3), (":3.0", 3), (":12", 12), ("", None), (None, None), ("host:3", None), (":x", None),
    ])
    def test_display_numbers(self, display, number):
        assert gs.display_number(display) == number

    def test_the_game_s_values_are_read_from_proc(self, tmp_path):
        proc = fake_proc(tmp_path, {101: {"environ": dict(GAME_ENV,
                                                          PIPEWIRE_REMOTE="pipewire-game")}})
        found = gs.find_game_processes("881020", proc)[0]
        assert found["PIPEWIRE_REMOTE"] == "pipewire-game" and found["PIPEWIRE_RUNTIME_DIR"] is None


class FakeRun:
    """subprocess.run 的替身。签名和返回值照 subprocess 的文档：返回 CompletedProcess。"""

    def __init__(self, write=None, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.write = write
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, cmd, env=None, capture_output=False, text=False, timeout=None):
        import subprocess
        self.calls.append((cmd, env))
        self.timeouts = getattr(self, "timeouts", []) + [timeout]
        if self.write:
            self.write(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout,
                                           stderr=self.stderr)


class TestGamescopectl:
    def test_it_targets_the_right_instance(self, tmp_path):
        run = FakeRun()
        gs.run_gamescopectl(["help"], "gamescope-1", "/run/user/1000", run=run)
        cmd, env = run.calls[0]
        assert cmd == ["gamescopectl", "help"]
        assert env["GAMESCOPE_WAYLAND_DISPLAY"] == "gamescope-1"
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"

    def test_screenshot_is_read_back(self, tmp_path):
        def gamescope_saves(path):
            Image.new("RGB", (8, 4), (200, 100, 50)).save(path)
        run = FakeRun(write=gamescope_saves)
        frame = gs.capture_gamescopectl(tmp_path / "shot.png", "gamescope-1", run=run,
                                        sleep=lambda s: None)
        assert run.calls[0][0][:2] == ["gamescopectl", "screenshot"]
        assert frame.shape == (4, 8, 3)
        assert frame[0, 0].tolist() == [200, 100, 50]

    def test_the_path_given_to_gamescope_is_absolute(self, tmp_path, monkeypatch):
        """gamescope 在自己的工作目录里解析路径。相对路径会让截图写到别处，这边等到超时。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "rel").mkdir()

        def gamescope_saves(path):
            Image.new("RGB", (8, 4)).save(path)
        run = FakeRun(write=gamescope_saves)
        gs.capture_gamescopectl(Path("rel/shot.png"), "gamescope-1", run=run, sleep=lambda s: None)
        sent = Path(run.calls[0][0][-1])
        assert sent.is_absolute()
        assert sent == Path.cwd() / "rel" / "shot.png"

    def test_a_symlinked_screenshot_name_never_touches_its_target(self, tmp_path):
        """resolve() 会顺着符号链接走到目标，清理旧图时删掉的就是目标文件了。"""
        outside = tmp_path / "outside.txt"
        outside.write_text("keep me")
        report_dir = tmp_path / "report"
        report_dir.mkdir()
        shot = report_dir / "gamescopectl-latest.png"
        shot.symlink_to(outside)

        def gamescope_saves(path):
            Image.new("RGB", (8, 4)).save(path)
        run = FakeRun(write=gamescope_saves)
        gs.capture_gamescopectl(shot, "gamescope-1", run=run, sleep=lambda s: None)
        assert outside.read_bytes() == b"keep me"
        assert Path(run.calls[0][0][-1]) == shot
        assert not shot.is_symlink()

    def test_a_dangling_symlink_is_removed_not_written_through(self, tmp_path):
        """exists() 对悬空链接返回 False。只查它的话链接会留下，截图就穿过链接写到外面去。"""
        outside = tmp_path / "outside.png"
        report_dir = tmp_path / "report"
        report_dir.mkdir()
        shot = report_dir / "gamescopectl-latest.png"
        shot.symlink_to(outside)
        assert not shot.exists() and shot.is_symlink()

        def gamescope_saves(path):
            Image.new("RGB", (8, 4)).save(path)
        gs.capture_gamescopectl(shot, "gamescope-1", run=FakeRun(write=gamescope_saves),
                                sleep=lambda s: None)
        assert not outside.exists()
        assert shot.exists() and not shot.is_symlink()

    def test_a_stale_file_from_an_earlier_run_is_not_reused(self, tmp_path):
        """上一次留下的图不能冒充这一次的截图。"""
        shot = tmp_path / "shot.png"
        Image.new("RGB", (8, 4)).save(shot)
        with pytest.raises(RuntimeError, match="no screenshot file appeared"):
            gs.capture_gamescopectl(shot, "gamescope-1", run=FakeRun(returncode=1), timeout=0.3,
                                    sleep=lambda s: None)

    def test_waiting_ends_only_when_the_size_settles(self, tmp_path):
        path = tmp_path / "growing.png"
        sizes = iter([0, 10, 20, 20])
        ticks = iter(range(100))

        def sleep(_):
            path.write_bytes(b"x" * next(sizes))

        assert gs.wait_for_file(path, timeout=50, sleep=sleep, clock=lambda: next(ticks))
        assert path.stat().st_size == 20

    def test_waiting_gives_up(self, tmp_path):
        ticks = iter(range(100))
        assert not gs.wait_for_file(tmp_path / "never.png", timeout=5, sleep=lambda s: None,
                                    clock=lambda: next(ticks))


class TestLineage:
    def test_it_walks_up_to_init(self, tmp_path):
        proc = fake_proc(tmp_path, {2200: {"ppid": 2100}, 2100: {"ppid": 2000},
                                    2000: {"ppid": 1}})
        assert gs.process_lineage(2200, proc) == [2200, 2100, 2000]

    def test_a_vanished_parent_ends_the_chain(self, tmp_path):
        proc = fake_proc(tmp_path, {2200: {"ppid": 2100}})
        assert gs.process_lineage(2200, proc) == [2200, 2100]
        assert gs.process_lineage(5, proc) == [5]

    def test_a_loop_cannot_hang_it(self, tmp_path):
        """进程号会被复用：读到一半，父进程号可能指回链上已有的进程。"""
        proc = fake_proc(tmp_path, {10: {"ppid": 11}, 11: {"ppid": 10}})
        assert gs.process_lineage(10, proc) == [10, 11]

    def test_the_walk_is_bounded(self, tmp_path):
        proc = fake_proc(tmp_path, {pid: {"ppid": pid + 1} for pid in range(100, 200)})
        assert len(gs.process_lineage(100, proc, limit=10)) == 10

    def test_against_the_real_proc(self):
        """真的 /proc：这个测试进程的链从它自己和它的父进程开始。替身写错了格式的话，
        上面几个测试照样过，这个不会。"""
        expected = [os.getpid()] + ([os.getppid()] if os.getppid() > 1 else [])
        assert gs.process_lineage(os.getpid())[:len(expected)] == expected


class TestRecognisingGamescope:
    @pytest.mark.parametrize("comm, exe, expected", [
        ("gamescope-wl", "/usr/games/gamescope", True),
        ("gamescope-wl", None, True),
        ("gamescope", None, True),
        ("x", "/usr/games/gamescope (deleted)", True),
        ("gamescopereaper", "/usr/games/gamescopereaper", False),
        ("bash", "/usr/bin/bash", False),
    ])
    def test_gamescope_processes_are_recognised(self, tmp_path, comm, exe, expected):
        """gamescopereaper 只替 gamescope 收尸，不是合成器，不能拦住往上的查找。"""
        proc = fake_proc(tmp_path, {77: {"comm": comm}})
        if exe:
            (proc / "77" / "exe").symlink_to(exe)
        assert gs.is_gamescope_process(77, proc) is expected

    def test_this_test_process_is_not_a_gamescope(self):
        assert gs.is_gamescope_process(os.getpid()) is False


class FakeWindow:
    """python-xlib Window 的替身：id，query_tree() 的应答带 root、parent、children（根的
    parent 是 id 为 0 的窗口），以及 change_attributes(**keys)。"""

    def __init__(self, wid, parent=None):
        self.id = wid
        self.parent = parent
        self.masks = []

    def query_tree(self):
        root = self
        while root.parent is not None:
            root = root.parent
        parent = self.parent if self.parent is not None else SimpleNamespace(id=0)
        return SimpleNamespace(root=root, parent=parent, children=[])

    def change_attributes(self, onerror=None, **keys):
        self.masks.append(keys)


def window_tree():
    root = FakeWindow(0x35B)
    game = FakeWindow(0x400000, root)
    child = FakeWindow(0x400010, game)
    overlay = FakeWindow(0x500000, root)
    return root, game, child, overlay


class TestAimingAtTheGame:
    def test_the_game_and_its_children_count(self):
        root, game, child, overlay = window_tree()
        assert gs.within_window(game, game.id)
        assert gs.within_window(child, game.id)

    @pytest.mark.parametrize("focus", ["overlay", "root", 0, 1])
    def test_anything_else_does_not(self, focus):
        """覆盖层、根窗口，以及 None / PointerRoot 这两个常量，都不是游戏。"""
        root, game, child, overlay = window_tree()
        target = {"overlay": overlay, "root": root}.get(focus, focus)
        assert not gs.within_window(target, game.id)
