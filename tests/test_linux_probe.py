# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""tools/linux_probe.py 里不需要 X、gamescope 和游戏的部分。

真正的测量只能在游戏开着时做；这里守住的是测量用的工具本身：找错进程、读错像素、
漏判按键泄漏、文件没写完就去读，这些都会让探测器给出一个看起来可信的错答案。
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import linux_probe as lp  # noqa: E402


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
        found = lp.find_game_processes("881020", proc)
        assert [p["pid"] for p in found] == [101, 102]
        assert found[0]["DISPLAY"] == ":3"
        assert found[0]["XAUTHORITY"] == "/run/user/1000/xauth_test"
        assert "granblue_fantasy_relink.exe" in found[0]["cmdline"]

    def test_one_instance_is_one_group(self, tmp_path):
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV},
                                    102: {"environ": GAME_ENV}})
        groups = lp.group_instances(lp.find_game_processes("881020", proc))
        assert list(groups) == [(":3", "gamescope-1")]
        assert len(groups[(":3", "gamescope-1")]) == 2

    def test_two_instances_are_kept_apart(self, tmp_path):
        """两个实例必须报出来，而不是随便挑一个去量。"""
        other = dict(GAME_ENV, DISPLAY=":4", GAMESCOPE_WAYLAND_DISPLAY="gamescope-2")
        proc = fake_proc(tmp_path, {101: {"environ": GAME_ENV}, 105: {"environ": other}})
        assert len(lp.group_instances(lp.find_game_processes("881020", proc))) == 2

    def test_environ_parsing_keeps_odd_bytes_and_skips_junk(self, tmp_path):
        proc = tmp_path / "proc"
        (proc / "7").mkdir(parents=True)
        (proc / "7" / "environ").write_bytes(b"A=1\0B=x=y\0NOEQUALS\0\0C=\xff\0")
        env = lp.read_environ(7, proc)
        assert env["A"] == "1"
        assert env["B"] == "x=y"
        assert "NOEQUALS" not in env
        assert env["C"].encode("utf-8", "surrogateescape") == b"\xff"

    def test_a_vanished_process_reads_as_none(self, tmp_path):
        assert lp.read_environ(12345, tmp_path) is None

    @pytest.mark.parametrize("display, number", [
        (":3", 3), (":3.0", 3), (":12", 12), ("", None), (None, None), ("host:3", None), (":x", None),
    ])
    def test_display_numbers(self, display, number):
        assert lp.display_number(display) == number


class TestPixels:
    def _bgrx(self, pixels, pad=0):
        """pixels: 行列表，每个像素 (r, g, b)。按小端 BGRX 排列，每行末尾补 pad 个字节。"""
        rows = []
        for row in pixels:
            rows.append(b"".join(bytes([b, g, r, 0]) for r, g, b in row) + b"\0" * pad)
        return b"".join(rows)

    def test_lsb_first_bgrx_becomes_rgb(self):
        pixels = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (10, 20, 30)]]
        rgb = lp.ximage_to_rgb(self._bgrx(pixels), 2, 2, 32, lsb_first=True)
        assert rgb.tolist() == [[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [10, 20, 30]]]

    def test_row_padding_is_skipped(self):
        """行尾补齐的字节如果当成像素，整张图会斜着错开。"""
        pixels = [[(1, 2, 3)] * 3, [(4, 5, 6)] * 3]
        rgb = lp.ximage_to_rgb(self._bgrx(pixels, pad=4), 3, 2, 32)
        assert rgb[1].tolist() == [[4, 5, 6]] * 3

    def test_msb_first_xrgb(self):
        data = bytes([0, 9, 8, 7])
        assert lp.ximage_to_rgb(data, 1, 1, 32, lsb_first=False).tolist() == [[[9, 8, 7]]]

    @pytest.mark.parametrize("data, w, h, bpp", [
        (b"\0" * 12, 2, 2, 24),     # 24 bpp is not handled
        (b"\0" * 12, 2, 2, 32),     # too short for 2x2 at 4 bytes a pixel
        (b"", 0, 0, 32),
    ])
    def test_unusable_images_raise(self, data, w, h, bpp):
        with pytest.raises(ValueError):
            lp.ximage_to_rgb(data, w, h, bpp)

    def test_grades(self):
        assert lp.grade_frame(None) == "failed"
        assert lp.grade_frame(np.zeros((40, 40, 3), np.uint8)) == "blank"
        noise = np.random.default_rng(5).integers(0, 255, (40, 40, 3), dtype=np.uint8)
        assert lp.grade_frame(noise) == "content"


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
        lp.run_gamescopectl(["help"], "gamescope-1", "/run/user/1000", run=run)
        cmd, env = run.calls[0]
        assert cmd == ["gamescopectl", "help"]
        assert env["GAMESCOPE_WAYLAND_DISPLAY"] == "gamescope-1"
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"

    def test_screenshot_is_read_back(self, tmp_path):
        def gamescope_saves(path):
            Image.new("RGB", (8, 4), (200, 100, 50)).save(path)
        run = FakeRun(write=gamescope_saves)
        frame = lp.capture_gamescopectl(tmp_path / "shot.png", "gamescope-1", run=run,
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
        lp.capture_gamescopectl(Path("rel/shot.png"), "gamescope-1", run=run, sleep=lambda s: None)
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
        lp.capture_gamescopectl(shot, "gamescope-1", run=run, sleep=lambda s: None)
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
        lp.capture_gamescopectl(shot, "gamescope-1", run=FakeRun(write=gamescope_saves),
                                sleep=lambda s: None)
        assert not outside.exists()
        assert shot.exists() and not shot.is_symlink()

    def test_a_stale_file_from_an_earlier_run_is_not_reused(self, tmp_path):
        """上一次留下的图不能冒充这一次的截图。"""
        shot = tmp_path / "shot.png"
        Image.new("RGB", (8, 4)).save(shot)
        with pytest.raises(RuntimeError, match="no screenshot file appeared"):
            lp.capture_gamescopectl(shot, "gamescope-1", run=FakeRun(returncode=1), timeout=0.3,
                                    sleep=lambda s: None)

    def test_waiting_ends_only_when_the_size_settles(self, tmp_path):
        path = tmp_path / "growing.png"
        sizes = iter([0, 10, 20, 20])
        ticks = iter(range(100))

        def sleep(_):
            path.write_bytes(b"x" * next(sizes))

        assert lp.wait_for_file(path, timeout=50, sleep=sleep, clock=lambda: next(ticks))
        assert path.stat().st_size == 20

    def test_waiting_gives_up(self, tmp_path):
        ticks = iter(range(100))
        assert not lp.wait_for_file(tmp_path / "never.png", timeout=5, sleep=lambda s: None,
                                    clock=lambda: next(ticks))


class TestLineage:
    def test_it_walks_up_to_init(self, tmp_path):
        proc = fake_proc(tmp_path, {2200: {"ppid": 2100}, 2100: {"ppid": 2000},
                                    2000: {"ppid": 1}})
        assert lp.process_lineage(2200, proc) == [2200, 2100, 2000]

    def test_a_vanished_parent_ends_the_chain(self, tmp_path):
        proc = fake_proc(tmp_path, {2200: {"ppid": 2100}})
        assert lp.process_lineage(2200, proc) == [2200, 2100]
        assert lp.process_lineage(5, proc) == [5]

    def test_a_loop_cannot_hang_it(self, tmp_path):
        """进程号会被复用：读到一半，父进程号可能指回链上已有的进程。"""
        proc = fake_proc(tmp_path, {10: {"ppid": 11}, 11: {"ppid": 10}})
        assert lp.process_lineage(10, proc) == [10, 11]

    def test_the_walk_is_bounded(self, tmp_path):
        proc = fake_proc(tmp_path, {pid: {"ppid": pid + 1} for pid in range(100, 200)})
        assert len(lp.process_lineage(100, proc, limit=10)) == 10

    def test_against_the_real_proc(self):
        """真的 /proc：这个测试进程的链从它自己和它的父进程开始。替身写错了格式的话，
        上面几个测试照样过，这个不会。"""
        expected = [os.getpid()] + ([os.getppid()] if os.getppid() > 1 else [])
        assert lp.process_lineage(os.getpid())[:len(expected)] == expected


def pw_object(oid, kind, props, **info):
    """pw-dump 输出里的一项。结构照 pw-dump 的真实输出：顶层 id、type、version、
    permissions、info；info 里有 props，节点的 info 里还有 state 等字段。"""
    return {"id": oid, "type": f"PipeWire:Interface:{kind}", "version": 3,
            "permissions": ["r", "w", "x", "m"],
            "info": {"change-mask": ["props"], "props": props, **info}}


def gamescope_objects(owner_pid, node_id, client_id, serial, reported_pid=None):
    """一个 gamescope 实例在 pw-dump 里留下的 Client 和 Node。"""
    return [
        pw_object(client_id, "Client", {
            "application.name": "gamescope", "application.process.binary": "gamescope",
            "application.process.id": owner_pid if reported_pid is None else reported_pid,
            "pipewire.sec.pid": owner_pid, "object.id": client_id, "object.serial": serial - 1}),
        pw_object(node_id, "Node", {
            "node.name": "gamescope", "media.class": "Video/Source", "client.id": client_id,
            "object.id": node_id, "object.serial": serial},
            state="suspended", error=None, **{"n-input-ports": 0, "n-output-ports": 1}),
    ]


def pw_dump(*instances, extra=()):
    """有声卡节点、端口和 Core 垫底的 pw-dump，再加上给定的 gamescope 实例。"""
    objects = [pw_object(0, "Core", {"core.name": "pipewire-0"}),
               pw_object(30, "Node", {"node.name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
                                      "media.class": "Audio/Sink", "object.serial": 30},
                         state="suspended"),
               pw_object(31, "Port", {"port.name": "playback_FL"})]
    for instance in instances:
        objects += instance
    return objects + list(extra)


# 游戏(2200) <- 启动器(2100) <- gamescope(2000) <- Steam(1500)。另一个 gamescope 是 3000。
LINEAGE = [2200, 2100, 2000, 1500]


def resolve(dump, lineages=(LINEAGE,)):
    return lp.resolve_pipewire_node(lp.pipewire_gamescope_nodes(dump), list(lineages))


class TestPipeWireNode:
    def test_nodes_are_read_with_both_process_ids(self):
        dump = pw_dump(gamescope_objects(2000, 68, 67, 301, reported_pid=12))
        assert lp.pipewire_gamescope_nodes(dump) == [
            {"id": 68, "serial": 301, "media_class": "Video/Source", "state": "suspended",
             "pids": [12, 2000]}]

    def test_the_tagged_source_wins_over_an_untagged_namesake(self):
        """同一个 gamescope 要是露出两个叫 gamescope 的节点，只有标着 Video/Source 的能取流。"""
        untagged = pw_object(69, "Node", {"node.name": "gamescope", "client.id": 67,
                                          "object.serial": 302}, state="idle")
        dump = pw_dump(gamescope_objects(2000, 68, 67, 301), extra=[untagged])
        assert [n["id"] for n in lp.pipewire_gamescope_nodes(dump)] == [68]
        node, code, _ = resolve(dump)
        assert (node["id"], code) == (68, "matched")

    def test_untagged_nodes_count_when_none_is_tagged(self):
        """别的版本的 gamescope 可能不标 media.class：只看名字，照样找得到。"""
        client, node = gamescope_objects(2000, 68, 67, 301)
        del node["info"]["props"]["media.class"]
        assert [n["id"] for n in lp.pipewire_gamescope_nodes(pw_dump([client, node]))] == [68]

    def test_other_objects_and_removed_ones_are_ignored(self):
        """pw-dump 也会列出 info 为 null 的对象（刚被删掉的）。"""
        removed = {"id": 90, "type": "PipeWire:Interface:Node", "version": 3,
                   "permissions": [], "info": None}
        assert lp.pipewire_gamescope_nodes(pw_dump(extra=[removed])) == []

    def test_the_node_of_an_ancestor_is_the_game_s(self):
        node, code, text = resolve(pw_dump(gamescope_objects(3000, 80, 79, 400),
                                           gamescope_objects(2000, 68, 67, 301)))
        assert (node["id"], code) == (68, "matched")
        assert "process 2000" in text

    def test_the_kernel_process_id_counts_when_the_reported_one_differs(self):
        """在另一个 PID 命名空间里，客户端自报的进程号和这边看到的不一样。"""
        node, code, _ = resolve(pw_dump(gamescope_objects(2000, 68, 67, 301, reported_pid=2),
                                        gamescope_objects(3000, 80, 79, 400)))
        assert (node["id"], code) == (68, "matched")

    def test_the_nearest_gamescope_wins_when_one_runs_inside_another(self):
        """外层的 gamescope(1700) 也是游戏的祖先，但游戏用的是里层那个的显示。"""
        nested = [2200, 2100, 2000, 1800, 1700, 1500]
        nodes = lp.pipewire_gamescope_nodes(pw_dump(gamescope_objects(1700, 50, 49, 200),
                                                     gamescope_objects(2000, 68, 67, 301)))
        node, code, _ = lp.resolve_pipewire_node(nodes, [nested], {2000, 1700})
        assert (node["id"], code) == (68, "matched")

    def test_a_process_adopted_outside_gamescope_does_not_spoil_the_match(self):
        """父进程先退出的进程被托管到 gamescope 外面，它那条链上没有 gamescope。"""
        dump = pw_dump(gamescope_objects(2000, 68, 67, 301), gamescope_objects(3000, 80, 79, 400))
        node, code, _ = resolve(dump, [[2300, 1500], LINEAGE])
        assert (node["id"], code) == (68, "matched")

    @pytest.mark.parametrize("instances", [
        [(3000, 80, 79, 400)],
        [(3000, 80, 79, 400), (3100, 90, 89, 500)],
    ])
    def test_a_node_that_matches_nothing_is_never_used(self, instances):
        """哪怕只有它一个：游戏的 gamescope 可能根本没有节点，它属于另一个 gamescope，
        用它测出来的是别的游戏的画面。"""
        node, code, text = resolve(pw_dump(*(gamescope_objects(*i) for i in instances)))
        assert (node, code) == (None, "unmatched")
        assert "none is used" in text

    def test_two_owned_candidates_are_never_guessed_between(self):
        """两条链落在两个不同的 gamescope 上。"""
        dump = pw_dump(gamescope_objects(2000, 68, 67, 301), gamescope_objects(3000, 80, 79, 400))
        assert resolve(dump, [LINEAGE, [3300, 3000, 1500]])[:2] == (None, "ambiguous")

    def test_one_process_with_two_nodes_is_ambiguous_too(self):
        second = pw_object(70, "Node", {"node.name": "gamescope", "media.class": "Video/Source",
                                        "client.id": 67, "object.serial": 302}, state="idle")
        assert resolve(pw_dump(gamescope_objects(2000, 68, 67, 301), extra=[second]))[:2] == (
            None, "ambiguous")

    def test_no_gamescope_node(self):
        assert resolve(pw_dump())[:2] == (None, "none")

    def test_pw_dump_runs_with_the_env_it_is_given(self):
        run = FakeRun(stdout=json.dumps(pw_dump()))
        env = {"XDG_RUNTIME_DIR": "/run/user/1000", "PIPEWIRE_REMOTE": "pipewire-game"}
        assert lp.read_pw_dump(env, run=run)[0]["type"] == "PipeWire:Interface:Core"
        cmd, used = run.calls[0]
        assert cmd == ["pw-dump", "-N"] and used == env

    def test_an_outer_gamescope_is_not_taken_for_the_game_s(self):
        """游戏自己的 gamescope（2000）没有节点，外层的（1700）有：那个节点是外层合成器的画面。"""
        nested = [2200, 2100, 2000, 1800, 1700, 1500]
        dump = pw_dump(gamescope_objects(1700, 50, 49, 200))
        node, code, text = lp.resolve_pipewire_node(lp.pipewire_gamescope_nodes(dump), [nested],
                                                    {2000, 1700})
        assert (node, code) == (None, "unmatched") and "process 2000" in text

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
        assert lp.is_gamescope_process(77, proc) is expected

    def test_this_test_process_is_not_a_gamescope(self):
        assert lp.is_gamescope_process(os.getpid()) is False

    def test_pw_dump_keeps_to_the_time_it_is_given(self):
        run = FakeRun(stdout=json.dumps(pw_dump()))
        lp.read_pw_dump({}, timeout=2, run=run)
        assert run.timeouts == [2]

    def test_a_failing_pw_dump_is_an_error_not_an_empty_graph(self):
        """连不上 PipeWire 时 pw-dump 非零退出。当成"没有 gamescope 节点"就是错答案。"""
        with pytest.raises(RuntimeError, match="pw-dump exited 1"):
            lp.read_pw_dump(run=FakeRun(returncode=1, stderr="connection failed"))


class TestPipeWireEndpoint:
    def test_the_game_s_endpoint_replaces_the_probe_s(self):
        base = {"HOME": "/h", "XDG_RUNTIME_DIR": "/run/user/1", "PIPEWIRE_REMOTE": "probe"}
        env = lp.pipewire_env("/run/user/1000", {"PIPEWIRE_REMOTE": "pipewire-game",
                                                 "PIPEWIRE_RUNTIME_DIR": "/run/pw"}, base=base)
        assert env == {"HOME": "/h", "XDG_RUNTIME_DIR": "/run/user/1000",
                       "PIPEWIRE_REMOTE": "pipewire-game", "PIPEWIRE_RUNTIME_DIR": "/run/pw"}

    def test_what_the_game_does_not_set_is_dropped(self):
        """探测器自己环境里的 PIPEWIRE_REMOTE 会把它带到另一个 PipeWire 上去。"""
        base = {"PIPEWIRE_REMOTE": "probe", "PIPEWIRE_RUNTIME_DIR": "/elsewhere"}
        env = lp.pipewire_env("/run/user/1000", {"PIPEWIRE_REMOTE": None,
                                                 "PIPEWIRE_RUNTIME_DIR": None}, base=base)
        assert env == {"XDG_RUNTIME_DIR": "/run/user/1000"}

    def test_without_the_game_s_values_only_the_runtime_dir_changes(self):
        assert lp.pipewire_env("/r", None, base={"PIPEWIRE_REMOTE": "x"}) == {
            "PIPEWIRE_REMOTE": "x", "XDG_RUNTIME_DIR": "/r"}

    def test_the_game_s_values_are_read_from_proc(self, tmp_path):
        proc = fake_proc(tmp_path, {101: {"environ": dict(GAME_ENV,
                                                          PIPEWIRE_REMOTE="pipewire-game")}})
        found = lp.find_game_processes("881020", proc)[0]
        assert found["PIPEWIRE_REMOTE"] == "pipewire-game" and found["PIPEWIRE_RUNTIME_DIR"] is None

    def test_the_capture_uses_the_game_s_endpoint(self, tmp_path):
        env = {"XDG_RUNTIME_DIR": "/run/user/1000", "PIPEWIRE_REMOTE": "pipewire-game"}
        report = lp.Report(tmp_path, echo=lambda s: None)
        (_, capture), = lp.capture_methods({"pw_node": {"id": 68, "serial": 301}, "pw_env": env},
                                           report)
        report.close()
        gst, dumps, present = FakeGst(), [], present_then(True)
        capture.popen = gst
        capture.dump = lambda env, timeout=None: dumps.append(env) or present(env)
        capture()
        used = gst.calls[0][1]
        assert used["PIPEWIRE_REMOTE"] == "pipewire-game" and used["LC_ALL"] == "C"
        # 看节点还在不在时，pw-dump 也连游戏的那个 PipeWire
        assert dumps and all(d["PIPEWIRE_REMOTE"] == "pipewire-game" for d in dumps)


@pytest.fixture
def l1(tmp_path, monkeypatch):
    """跑 step_l1，游戏进程、pw-dump 和外部命令都是替身。X 连不上，L1 在那里中断，
    这正好看得出 PipeWire 那几行写在连 X 之前。"""
    import subprocess

    game = {"pid": 2200, "comm": "granblue_fantas", "cmdline": "x",
            **{k: GAME_ENV[k] for k in ("DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY", "XAUTHORITY",
                                        "XDG_RUNTIME_DIR")},
            "PIPEWIRE_REMOTE": "pipewire-game", "PIPEWIRE_RUNTIME_DIR": None}
    monkeypatch.setattr(lp, "find_game_processes", lambda appid: [game])
    monkeypatch.setattr(lp, "process_lineage", lambda pid: LINEAGE)
    monkeypatch.setattr(lp, "read_proc_text", lambda pid, name, proc="/proc": "x")
    monkeypatch.setattr(lp, "is_gamescope_process", lambda pid, proc="/proc": pid == 2000)
    monkeypatch.setattr(lp.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout="tool 1.0\n", stderr=""))
    monkeypatch.setattr(lp, "run_gamescopectl", lambda args, wayland, runtime=None, **k:
                        subprocess.CompletedProcess(["gamescopectl", *args], 0, "", "help\n"))

    def no_x(display, xauthority=None):
        raise ConnectionRefusedError("no X here")
    monkeypatch.setattr(lp, "connect_x", no_x)

    def run(read_pw_dump):
        monkeypatch.setattr(lp, "read_pw_dump", read_pw_dump)
        report = lp.Report(tmp_path, echo=lambda s: None)
        state = {}
        with pytest.raises(ConnectionRefusedError):
            lp.step_l1(SimpleNamespace(appid="881020"), report, state)
        report.close()
        return state, (tmp_path / "report.md").read_text()
    return run


class TestL1PipeWire:
    def test_the_game_s_node_is_kept_for_later_steps(self, l1):
        dump = pw_dump(gamescope_objects(3000, 80, 79, 400), gamescope_objects(2000, 68, 67, 301))
        seen = []
        state, text = l1(lambda env: seen.append(env) or dump)
        assert state["pw_node"]["id"] == 68 and state["pw_node"]["serial"] == 301
        assert "PipeWire node: Node 68 belongs to process 2000" in text
        # pw-dump 和后面的取流都照游戏的 PipeWire 连
        assert seen[0]["PIPEWIRE_REMOTE"] == "pipewire-game" and seen[0] is state["pw_env"]
        assert "PIPEWIRE_RUNTIME_DIR" not in seen[0]
        assert "gamescope processes on the lineages: [2000]" in text

    def test_an_outer_gamescope_s_node_is_not_used(self, l1):
        """游戏自己的 gamescope（2000）没有节点；链上更远的 1500 有一个，那是外层的。"""
        dump = pw_dump(gamescope_objects(1500, 50, 49, 200))
        state, text = l1(lambda env: dump)
        assert "pw_node" not in state
        assert "nearest gamescope above the game (process 2000) owns no PipeWire node" in text

    def test_without_pw_dump_l1_goes_on(self, l1):
        def missing(env):
            raise FileNotFoundError(2, "No such file or directory", "pw-dump")
        state, text = l1(missing)
        assert "pw_node" not in state
        assert "PipeWire node: FileNotFoundError" in text


class TestRunBounded:
    """真的子进程：超时、SIGINT 和最后的 kill 都是操作系统做的，替身证明不了。"""

    def test_a_quick_command_returns_its_output(self):
        code, output, timed_out, seen = lp.run_bounded(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], 10)
        assert (code, timed_out, seen) == (0, False, None)
        assert "out" in output and "err" in output

    def test_a_hanging_command_is_stopped_after_a_look_at_the_scene(self):
        import time
        started = time.monotonic()
        code, output, timed_out, seen = lp.run_bounded(
            [sys.executable, "-c", "import os, time; print(os.getpid(), flush=True); "
                                   "time.sleep(30)"], 0.5, grace=5,
            at_deadline=lambda pid: pid)
        assert timed_out and seen == int(output.split()[0])
        assert time.monotonic() - started < 5
        assert code != 0

    def test_a_command_that_ignores_sigint_is_killed(self):
        import signal
        code, _, timed_out, _ = lp.run_bounded(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGINT, "
                                   "signal.SIG_IGN); time.sleep(30)"], 1.0, grace=0.3)
        assert timed_out and code == -signal.SIGKILL

    def test_a_failing_look_does_not_stop_the_cleanup(self):
        def broken(pid):
            raise RuntimeError("pw-dump went away")
        _, _, timed_out, seen = lp.run_bounded(
            [sys.executable, "-c", "import time; time.sleep(30)"], 0.3, at_deadline=broken)
        assert timed_out and "pw-dump went away" in seen


GST_PID = 4242


class FakeGst:
    """subprocess.Popen 的替身，照文档：Popen(args, stdout=, stderr=, env=, text=) 返回进程
    对象；communicate(timeout=None) 返回 (stdout, stderr)，到时抛 TimeoutExpired(cmd,
    timeout)，再调一次不丢输出；send_signal(sig)、kill()；returncode 在进程结束后才有值。

    write：像 filesink 那样把一张 PNG 写到 location=。hang：第一次 communicate 超时，
    直到收到信号。
    """

    def __init__(self, write=True, hang=False, returncode=0, output=""):
        self.write, self.hang = write, hang
        self.returncode, self.output = returncode, output
        self.calls, self.signals = [], []

    def __call__(self, cmd, env=None, stdout=None, stderr=None, text=False):
        import subprocess
        assert stderr == subprocess.STDOUT and text
        self.calls.append((cmd, env))
        fake = self

        class Process:
            returncode = None
            pid = GST_PID

            def communicate(self, timeout=None):
                if fake.hang and not fake.signals:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if fake.write and not fake.signals:
                    location = next(a for a in cmd if a.startswith("location="))
                    Image.new("RGB", (8, 4), (10, 200, 30)).save(location.partition("=")[2])
                self.returncode = -2 if fake.signals else fake.returncode
                return fake.output, None

            def send_signal(self, sig):
                fake.signals.append(sig)

            def kill(self):
                fake.signals.append("kill")

        return Process()


def parse_with_gstreamer(argv, show):
    """拿 gst-launch 自己用的解析器（gst_parse_launchv）解析 argv，不启动管道，不连
    PipeWire；再跑 show 这段代码把要看的属性打出来（可用的名字：p、src、fallback、
    reconnect）。
    系统 Python 没有带 pipewiresrc 的 GStreamer 时跳过。"""
    import shutil
    import subprocess
    python = shutil.which("python3", path="/usr/bin")
    if python is None:
        pytest.skip("no system python3")
    check = (
        "import sys\n"
        "try:\n"
        "    import gi\n"
        "    gi.require_version('Gst', '1.0')\n"
        "    from gi.repository import Gst\n"
        "except Exception:\n"
        "    sys.exit(77)\n"
        "Gst.init(None)\n"
        "if Gst.ElementFactory.find('pipewiresrc') is None: sys.exit(77)\n"
        "p = Gst.parse_launchv(sys.argv[1:])\n"
        "src = p.get_by_name('pipewiresrc0')\n"
        "props = src.get_property('stream-properties')\n"
        "fallback = props.get_value('node.dont-fallback')\n"
        "reconnect = props.get_value('node.dont-reconnect')\n"
        + show)
    result = subprocess.run([python, "-c", check, *argv], capture_output=True, text=True,
                            timeout=30)
    if result.returncode == 77:
        pytest.skip("no GStreamer with pipewiresrc for the system python")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def pts(seconds):
    return f"0:00:{seconds:012.9f}"


def chain_line(stamp):
    """fakesink silent=false 在 gst-launch -v 下每收一帧打的一行，格式照 GStreamer 1.28 的
    真实输出（本机的 videotestsrc 打出来的）。"""
    return ("/GstPipeline:pipeline0/GstFakeSink:fakesink0: last-message = chain   ******* "
            f"(fakesink0:sink) (14745600 bytes, dts: none, pts: {stamp}, duration: none, "
            "offset: -1, offset_end: -1, flags: 00000000 , meta: none) 0x7f0000000000")


SINK_CAPS_LINE = ("/GstPipeline:pipeline0/GstFakeSink:fakesink0.GstPad:sink: caps = video/x-raw, "
                  "format=(string)BGRx, width=(int)2560, height=(int)1440, "
                  "framerate=(fraction)0/1, pixel-aspect-ratio=(fraction)1/1")


def rate_output(stamps, ended=None):
    """ended：管道停下的时刻，gst-launch 停下时打 "Execution ended after ..."（中断和 EOS
    都打，格式照本机 GStreamer 1.28 的真实输出）。None 表示没有这一行，比如进程被杀掉了。"""
    lines = ["Setting pipeline to PAUSED ...", SINK_CAPS_LINE]
    lines += [chain_line(pts(s) if s is not None else "none") for s in stamps]
    lines += ["handling interrupt.", "Interrupt: Stopping pipeline ..."]
    if ended is not None:
        lines += [f"Execution ended after {pts(ended)}", "Setting pipeline to NULL ..."]
    return "\n".join(lines) + "\n"


def consumer_stream(client_id, node_id, pid, link_id, state):
    """一个取流的程序在 pw-dump 里留下的 Client、它的流节点，以及从 gamescope 节点 68 接到
    这个流上的连接。"""
    pids = {} if pid is None else {"application.process.id": pid, "pipewire.sec.pid": pid}
    return [
        pw_object(client_id, "Client", {"application.name": "gst-launch-1.0", **pids}),
        pw_object(node_id, "Node", {"node.name": "gst-launch-1.0", "client.id": client_id,
                                    "media.class": "Stream/Input/Video",
                                    "object.serial": node_id + 1000}, state="running"),
        pw_object(link_id, "Link", {"link.output.node": 68, "link.input.node": node_id},
                  state=state, **{"output-node-id": 68, "output-port-id": 69,
                                  "input-node-id": node_id, "input-port-id": node_id + 1}),
    ]


def present_then(*answers):
    """依次给出的 pw-dump：True 是节点 68 在，False 是不在，字符串是一份图，这一次的
    gst-launch（进程号 GST_PID）的流以这种状态接在节点 68 上。"""
    queue = list(answers)

    def dump(env=None, timeout=None):
        dump.timeouts.append(timeout)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, str):
            return pw_dump(gamescope_objects(2000, 68, 67, 301),
                           extra=consumer_stream(109, 110, GST_PID, 120, answer))
        return pw_dump(gamescope_objects(2000, 68, 67, 301)) if answer else pw_dump()
    dump.timeouts = []
    return dump


class TestPipeWireCapture:
    def test_the_pipeline_targets_the_node_and_never_falls_back(self, tmp_path):
        cmd = lp.pipewire_pipeline(301, tmp_path / "x.png")
        assert cmd[:3] == ["gst-launch-1.0", "-q", "pipewiresrc"]
        assert "target-object=301" in cmd and "num-buffers=1" in cmd
        assert not any(a.startswith("path=") for a in cmd)
        assert lp.STREAM_PROPERTIES in cmd
        assert "video/x-raw,format=BGRx" in cmd
        assert cmd[-1] == f"location={tmp_path / 'x.png'}"

    def test_gstreamer_parses_it_the_way_it_is_meant(self, tmp_path):
        """dont-fallback、dont-reconnect 都必须是字符串 "true"：布尔值转成字符串是 "TRUE"。"""
        target = tmp_path / "dir with space" / "x.png"
        printed = parse_with_gstreamer(
            lp.pipewire_pipeline(301, target)[2:],
            "print(src.get_property('target-object'), src.get_property('path'),\n"
            "      src.get_property('num-buffers'), repr(fallback), repr(reconnect),\n"
            "      p.get_by_name('filesink0').get_property('location'), sep='|')\n")
        assert printed == f"301|None|1|'true'|'true'|{target}"

    def test_the_rate_pipeline_parses_the_way_it_is_meant(self):
        printed = parse_with_gstreamer(
            lp.pipewire_rate_pipeline(301)[2:],
            "sink = p.get_by_name('fakesink0')\n"
            "print(src.get_property('target-object'), src.get_property('path'),\n"
            "      src.get_property('do-timestamp'), repr(fallback), repr(reconnect),\n"
            "      sink.get_property('sync'), sink.get_property('silent'), sep='|')\n")
        assert printed == "301|None|True|'true'|'true'|False|False"

    def test_a_frame_is_read_back_and_the_node_checked(self, tmp_path):
        gst = FakeGst()
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png",
                                     {"XDG_RUNTIME_DIR": "/run/user/1000"}, popen=gst,
                                     dump=present_then(True))
        frame = capture()
        assert frame.shape == (4, 8, 3) and frame[0, 0].tolist() == [10, 200, 30]
        cmd, env = gst.calls[0]
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000" and env["LC_ALL"] == "C"
        assert capture.last["exit"] == 0 and capture.last["node_after"] is True
        assert capture.last["timed_out"] is False and gst.signals == []

    def test_a_stall_is_a_result_not_a_hang(self, tmp_path):
        """headless 干跑里连接停在 negotiating、一帧也不来。要报出来，而不是一直等。"""
        gst = FakeGst(write=False, hang=True)
        dump = present_then("negotiating", True)
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=gst, dump=dump)
        with pytest.raises(RuntimeError, match=r"no frame within 5 s; links from node serial 301: "
                                               r"\['negotiating'\]; node still there: True"):
            capture()
        import signal
        assert gst.signals == [signal.SIGINT]
        assert capture.last["links"] == ["negotiating"] and capture.last["timed_out"]
        # 到点那一眼在 SIGINT 之前，限时很短；之后查节点在不在用 pw-dump 默认的限时
        assert dump.timeouts == [lp.DEADLINE_LOOK_TIMEOUT, None]
        assert lp.DEADLINE_LOOK_TIMEOUT <= 2

    def test_gstreamer_errors_are_reported(self, tmp_path):
        output = ("ERROR: from element /GstPipeline:pipeline0/GstPipeWireSrc:pipewiresrc0: "
                  "stream error: defined target not found\nAdditional debug info:\n...")
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=FakeGst(
            write=False, returncode=255, output=output), dump=present_then(True))
        with pytest.raises(RuntimeError, match="gst-launch exited 255.*defined target not found"):
            capture()
        assert capture.last["output"].startswith("ERROR: from element")

    def test_once_the_node_is_gone_no_pipeline_is_started(self, tmp_path):
        gst = FakeGst()
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=gst,
                                     dump=present_then(False))
        capture()
        assert capture.last["node_after"] is False
        with pytest.raises(RuntimeError, match="disappeared"):
            capture()
        assert len(gst.calls) == 1 and capture.last == {"node_after": False}

    def test_a_node_that_reused_the_id_is_not_the_same_node(self, tmp_path):
        """PipeWire 会复用对象 id：游戏的节点（serial 301）没了，另一个 gamescope 的节点拿到了
        同一个 id 68。按 id 认的话它会被当成还在。"""
        def replaced(env=None, timeout=None):
            return pw_dump(gamescope_objects(3000, 68, 79, 999))
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=FakeGst(), dump=replaced)
        capture()
        assert capture.last["node_after"] is False and capture.gone

    def test_an_untagged_selection_survives_a_tagged_newcomer(self, tmp_path):
        """L1 选中的是没标 Video/Source 的节点（serial 301）；后来另一个 gamescope 冒出一个
        标了的。发现时的筛选只留标了的，拿它来查，301 就"没了"，后面的测量全被取消。"""
        client, node = gamescope_objects(2000, 68, 67, 301)
        del node["info"]["props"]["media.class"]

        def graph(env=None, timeout=None):
            return pw_dump([client, node], gamescope_objects(3000, 80, 79, 400))
        assert [n["serial"] for n in lp.pipewire_gamescope_nodes(graph())] == [400]
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=FakeGst(), dump=graph)
        capture()
        assert capture.last["node_after"] is True and not capture.gone

    def test_an_unreadable_graph_does_not_count_as_a_vanished_node(self, tmp_path):
        def broken(env=None, timeout=None):
            raise RuntimeError("pw-dump exited 1")
        gst = FakeGst()
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=gst, dump=broken)
        capture()
        capture()
        assert len(gst.calls) == 2 and "pw-dump exited 1" in capture.last["node_after"]

    def test_a_stale_frame_from_an_earlier_capture_is_not_reused(self, tmp_path):
        shot = tmp_path / "pw.png"
        Image.new("RGB", (8, 4)).save(shot)
        capture = lp.PipeWireCapture(301, shot, popen=FakeGst(write=False),
                                     dump=present_then(True))
        with pytest.raises(RuntimeError, match="wrote no frame"):
            capture()

    def test_a_symlinked_frame_name_never_touches_its_target(self, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("keep me")
        shot = tmp_path / "pw.png"
        shot.symlink_to(outside)
        lp.PipeWireCapture(301, shot, popen=FakeGst(), dump=present_then(True))()
        assert outside.read_text() == "keep me" and not shot.is_symlink()

    def test_details_of_an_earlier_capture_do_not_stick(self, tmp_path):
        """这一次在起进程之前就失败了（比如没装 gst-launch），报告里不能挂着上一次的细节。"""
        def missing(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "gst-launch-1.0")
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=FakeGst(),
                                     dump=present_then(True))
        capture()
        capture.popen = missing
        with pytest.raises(FileNotFoundError):
            capture()
        assert capture.last == {}

    def test_only_the_link_into_this_run_s_stream_counts(self):
        """OBS 之类别的程序也接在 gamescope 的节点上时，它的 active 不能算成这一次的。"""
        unrelated = pw_object(130, "Link", {}, state="active",
                              **{"output-node-id": 30, "input-node-id": 140})
        obs = consumer_stream(119, 121, 5555, 131, "active")
        ours = present_then("paused")()
        assert lp.pipewire_links(ours + obs + [unrelated], 301, GST_PID) == ["paused"]
        never_linked = pw_dump(gamescope_objects(2000, 68, 67, 301), extra=obs)
        assert lp.pipewire_links(never_linked, 301, GST_PID) == []
        # 不知道进程号时，一个连进程号都没报的客户端也不能被当成这一次的。
        pidless = consumer_stream(139, 141, None, 151, "active")
        assert lp.pipewire_links(ours + obs + pidless, 301, None) == []
        assert lp.pipewire_links(pw_dump(), 301, GST_PID) == []


class TestFrameRate:
    def test_the_rate_is_read_from_real_gstreamer_output(self):
        """真的 gst-launch -v：GStreamer 自带的测试图案源，每秒 30 帧，不碰 PipeWire。替身
        的输出格式写错了的话，下面那些测试照样过，这个不会。"""
        import shutil
        import subprocess
        if shutil.which("gst-launch-1.0") is None or subprocess.run(
                ["gst-inspect-1.0", "videotestsrc"], capture_output=True).returncode != 0:
            pytest.skip("no gst-launch-1.0 with videotestsrc")
        # 两段 caps 不能直接相连（第二段会被当成元件名），中间隔一个 videoconvert，
        # 后半段照用探测器自己的 RATE_TAIL。
        # 和探测器一样按时间收尾：到点 SIGINT，而不是数够几帧就 EOS。
        cmd = ["gst-launch-1.0", "-v", "videotestsrc", "is-live=true", "do-timestamp=true",
               "!", "video/x-raw,width=64,height=36,framerate=30/1", "!",
               "videoconvert", *lp.RATE_TAIL]
        _, output, timed_out, _ = lp.run_bounded(cmd, 1.5, env=dict(os.environ, LC_ALL="C"))
        assert timed_out
        rate = lp.parse_rate(output)
        assert rate["frames"] == rate["stamped"] >= 30
        assert rate["window_s"] is not None and rate["first_frame_ms"] < 100
        assert 28 <= rate["fps"] <= 32
        assert rate["caps"] == "BGRx 64x36 @ 30/1"

    def test_the_rate_comes_from_the_timestamps(self):
        rate = lp.parse_rate(rate_output([0.0, 0.1, 0.2, 0.5]))
        assert rate == {"frames": 4, "stamped": 4, "fps": 8.0, "max_gap_ms": 300,
                        "first_frame_ms": 0, "window_s": None, "caps": "BGRx 2560x1440 @ 0/1"}

    def test_a_late_first_frame_is_part_of_the_window(self):
        """先空等两秒半，最后半秒才来 15 帧。从第一帧算起是每秒 28 帧、没有长空档；从管道
        开始跑算起，才是每秒 5 帧、空等了两秒半。"""
        rate = lp.parse_rate(rate_output([2.5 + i / 30 for i in range(15)], ended=3.0))
        assert rate["first_frame_ms"] == 2500
        assert rate["fps"] == 5.0 and rate["max_gap_ms"] == 2500
        text = lp.describe_rate(rate)
        assert "first after 2500 ms" in text and "below the 10 a second" in text

    def test_a_stream_that_stalls_does_not_look_smooth(self):
        """半秒里来了 15 帧，后面两秒半一帧也没有。只看帧与帧之间是每秒 30 帧、最长空档
        33 ms；算到管道停下，才是每秒 5 帧、冻了两秒半。"""
        rate = lp.parse_rate(rate_output([i / 30 for i in range(15)], ended=3.0))
        assert rate["window_s"] == 3.0
        assert rate["fps"] == 5.0 and rate["max_gap_ms"] == 2533
        assert "below the 10 a second" in lp.describe_rate(dict(rate, links=["active"]))

    def test_a_steady_stream_keeps_its_rate(self):
        rate = lp.parse_rate(rate_output([i / 30 for i in range(61)], ended=2.02))
        assert rate["fps"] == 30.2 and rate["max_gap_ms"] == 33
        assert "enough" in lp.describe_rate(rate)

    def test_a_stream_right_at_the_line_is_enough(self):
        """每秒正好 10 帧：3 秒里 0.0 到 2.9 秒各来一帧。少算一帧会得出 9.7、判成不够。"""
        rate = lp.parse_rate(rate_output([i / 10 for i in range(30)], ended=3.0))
        assert rate["fps"] == 10.0 and rate["max_gap_ms"] == 100
        assert "enough" in lp.describe_rate(rate)

    def test_without_the_end_of_the_run_there_is_no_verdict(self):
        """进程被杀掉，没打出停下的那一行：一串帧之后冻没冻住看不出来，不能说够。"""
        rate = lp.parse_rate(rate_output([i / 30 for i in range(15)]))
        text = lp.describe_rate(rate)
        assert rate["window_s"] is None and rate["fps"] is not None
        assert "not judged" in text and "enough" not in text

    def test_a_freeze_in_the_middle_is_not_enough(self):
        """平均每秒 20 多帧，中间冻了 0.8 秒：跟着战斗反应还是跟不上。"""
        stamps = [i / 30 for i in range(31)] + [1.8 + i / 30 for i in range(36)]
        rate = lp.parse_rate(rate_output(stamps, ended=3.0))
        assert rate["fps"] > 10 and rate["max_gap_ms"] == 800
        assert "froze for 800 ms" in lp.describe_rate(rate)

    def test_frames_without_a_timestamp_are_counted_but_give_no_rate(self):
        rate = lp.parse_rate(rate_output([None, None, 0.25]))
        assert (rate["frames"], rate["stamped"], rate["fps"]) == (3, 1, None)

    def test_no_caps_line_means_the_format_never_settled(self):
        assert lp.parse_rate("Setting pipeline to PAUSED ...\n")["caps"] is None

    def test_the_deadline_is_the_normal_end(self, tmp_path):
        import signal
        gst = FakeGst(write=False, hang=True,
                      output=rate_output([i / 60 for i in range(120)], ended=2.0))
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=gst,
                                     dump=present_then("active", True))
        rate = capture.measure_rate()
        assert rate["fps"] == 60.0 and rate["window_s"] == 2.0
        assert rate["links"] == ["active"] and "error" not in rate
        assert rate["node_after"] is True and gst.signals == [signal.SIGINT]
        cmd, env = gst.calls[0]
        assert cmd[:2] == ["gst-launch-1.0", "-v"] and env["LC_ALL"] == "C"

    def test_a_pipeline_that_ends_by_itself_is_an_error(self, tmp_path):
        output = ("Setting pipeline to PAUSED ...\nERROR: from element "
                  "/GstPipeline:pipeline0/GstPipeWireSrc:pipewiresrc0: target not found\n")
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", dump=present_then(True),
                                     popen=FakeGst(write=False, returncode=1, output=output))
        rate = capture.measure_rate()
        assert rate["fps"] is None
        assert "exit 1" in rate["error"] and "target not found" in rate["error"]

    def test_an_early_end_after_a_burst_is_not_a_good_rate(self, tmp_path):
        """停之前来过几帧，帧率算得出来；但管道自己停了，那几帧不代表连续取流。"""
        output = rate_output([i / 30 for i in range(12)], ended=0.4) + (
            "ERROR: from element /GstPipeline:pipeline0/GstPipeWireSrc:pipewiresrc0: "
            "stream error\n")
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", dump=present_then(True),
                                     popen=FakeGst(write=False, returncode=1, output=output))
        rate = capture.measure_rate()
        text = lp.describe_rate(rate)
        assert rate["fps"] is not None and "error" in rate
        assert text.startswith("No usable frame rate: gst-launch ended on its own")
        assert "Before it stopped:" in text and "enough" not in text

    def test_no_rate_is_measured_once_the_node_is_gone(self, tmp_path):
        gst = FakeGst()
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=gst,
                                     dump=present_then(False))
        capture()
        assert "disappeared" in capture.measure_rate()["skipped"]
        assert len(gst.calls) == 1

    def test_a_missing_gst_launch_is_reported_not_raised(self, tmp_path):
        def missing(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "gst-launch-1.0")
        capture = lp.PipeWireCapture(301, tmp_path / "pw.png", popen=missing,
                                     dump=present_then(True))
        rate = capture.measure_rate()
        assert "FileNotFoundError" in rate["error"] and rate["node_after"] is True

    @pytest.mark.parametrize("rate, words", [
        ({"fps": 59.9, "stamped": 150, "max_gap_ms": 40, "window_s": 2.98,
          "caps": "BGRx 2560x1440 @ 0/1"},
         "59.9 frames a second over 150 frames in 2.98 s, longest gap 40 ms, "
         "BGRx 2560x1440 @ 0/1: enough"),
        ({"fps": 10.0, "stamped": 30, "max_gap_ms": 500, "window_s": 3.0,
          "caps": "BGRx 2560x1440 @ 0/1"}, "enough"),
        ({"fps": 30.0, "stamped": 60, "max_gap_ms": 501, "window_s": 3.0,
          "caps": "BGRx 2560x1440 @ 0/1"}, "froze for 501 ms"),
        ({"fps": 4.0, "stamped": 9, "max_gap_ms": 400, "window_s": 3.0,
          "caps": "BGRx 2560x1440 @ 0/1"}, "below the 10 a second"),
        ({"fps": 30.0, "stamped": 60, "max_gap_ms": 40, "window_s": None,
          "caps": "BGRx 2560x1440 @ 0/1"}, "end of the run unknown"),
        ({"fps": None, "frames": 0, "seconds": 3.0, "links": ["negotiating"]},
         "No frame rate: 0 frames arrived in 3.0 s. Links from the node at the end: "
         "['negotiating']"),
        ({"skipped": "node 68 disappeared after an earlier capture"}, "Not measured"),
    ])
    def test_descriptions(self, rate, words):
        assert words in lp.describe_rate(rate)


class TestL2Choice:
    def run_l2(self, tmp_path, monkeypatch, methods):
        monkeypatch.setattr(lp, "countdown", lambda *a, **k: None)
        monkeypatch.setattr(lp, "capture_methods", lambda state, report: methods)
        report = lp.Report(tmp_path, echo=lambda s: None)
        state = {}
        lp.step_l2(SimpleNamespace(), report, state)
        report.close()
        lines = (tmp_path / "report.jsonl").read_text().splitlines()
        records = [json.loads(line) for line in lines]
        return state, records

    def content(self):
        return np.random.default_rng(7).integers(0, 255, (16, 16, 3), dtype=np.uint8)

    def test_pipewire_is_chosen_only_when_nothing_else_works(self, tmp_path, monkeypatch):
        blank = np.zeros((16, 16, 3), np.uint8)
        state, _ = self.run_l2(tmp_path, monkeypatch, [("gamescopectl", lambda: blank),
                                                       ("pipewire", self.content)])
        assert state["capture_name"] == "pipewire"

    def test_gamescopectl_goes_before_pipewire(self, tmp_path, monkeypatch):
        state, _ = self.run_l2(tmp_path, monkeypatch, [("pipewire", self.content),
                                                       ("gamescopectl", self.content)])
        assert state["capture_name"] == "gamescopectl"

    def test_the_capture_details_go_into_the_report(self, tmp_path, monkeypatch):
        class Detailed:
            last = {"exit": 0, "node_after": True}

            def __call__(self):
                return np.random.default_rng(7).integers(0, 255, (16, 16, 3), dtype=np.uint8)
        _, records = self.run_l2(tmp_path, monkeypatch, [("pipewire", Detailed())])
        detail = next(r["value"] for r in records if r["name"] == "focused / pipewire")
        assert detail["grade"] == "content" and detail["node_after"] is True

    def test_a_capture_whose_node_vanished_is_not_handed_on(self, tmp_path, monkeypatch):
        """失焦那轮截到了内容、后来节点没了：交给 L3、L4 只会一帧也截不到。"""
        class VanishesAfterContent:
            gone = False

            def __call__(self):
                frame = np.random.default_rng(7).integers(0, 255, (16, 16, 3), dtype=np.uint8)
                self.gone = True
                return frame
        blank = np.zeros((16, 16, 3), np.uint8)
        state, records = self.run_l2(tmp_path, monkeypatch, [("gamescopectl", lambda: blank),
                                                             ("pipewire", VanishesAfterContent())])
        assert "capture" not in state
        assert any(r["name"] == "pipewire not used" for r in records)

    def test_every_pass_measures_the_frame_rate(self, tmp_path, monkeypatch):
        class Rated:
            def __call__(self):
                return np.random.default_rng(7).integers(0, 255, (16, 16, 3), dtype=np.uint8)

            def measure_rate(self):
                return {"fps": 59.9, "stamped": 150, "max_gap_ms": 40, "caps": "BGRx"}
        _, records = self.run_l2(tmp_path, monkeypatch, [("pipewire", Rated())])
        rates = [r["name"] for r in records if r["name"].endswith("frame rate")]
        assert rates == ["focused / pipewire frame rate", "unfocused / pipewire frame rate",
                         "covered / pipewire frame rate"]

    def test_capture_methods_offer_pipewire_only_with_a_node(self, tmp_path):
        report = lp.Report(tmp_path, echo=lambda s: None)
        assert lp.capture_methods({}, report) == []
        assert lp.capture_methods({"pw_node": {"id": 68, "serial": None}}, report) == []
        names = [n for n, _ in lp.capture_methods({"pw_node": {"id": 68, "serial": 301}}, report)]
        report.close()
        assert names == ["pipewire"]


class FakeDisplay:
    """python-xlib Display 的替身，只有 send_key 用到的方法，签名照 Xlib 的文档：
    keysym_to_keycode(keysym)、xtest_fake_input(event_type, detail=0, ...)、sync()。"""

    def __init__(self, keycode=9):
        self.keycode = keycode
        self.calls = []

    def keysym_to_keycode(self, keysym):
        self.calls.append(("keycode", keysym))
        return self.keycode

    def xtest_fake_input(self, event_type, detail=0, time=0, root=0, x=0, y=0):
        self.calls.append(("fake", event_type, detail))

    def sync(self):
        self.calls.append(("sync",))


class TestSendingKeys:
    def test_escape_is_pressed_then_released(self):
        from Xlib import X, XK
        d = FakeDisplay()
        lp.send_key(d, "Escape", sleep=lambda s: None)
        fakes = [c for c in d.calls if c[0] == "fake"]
        assert fakes == [("fake", X.KeyPress, 9), ("fake", X.KeyRelease, 9)]
        assert ("keycode", XK.string_to_keysym("Escape")) in d.calls

    @pytest.mark.parametrize("key", ["Super_L", "Super_R", "s", "Return", "w"])
    def test_anything_else_is_refused_before_touching_the_display(self, key):
        """Super 组合是 gamescope 的快捷键；别的键在游戏里可能确认了什么。"""
        d = FakeDisplay()
        with pytest.raises(ValueError, match="refusing"):
            lp.send_key(d, key)
        assert d.calls == []

    def test_a_missing_keycode_is_an_error_not_a_silent_no_op(self):
        with pytest.raises(RuntimeError, match="no keycode"):
            lp.send_key(FakeDisplay(keycode=0), "Escape", sleep=lambda s: None)


class TestVerdicts:
    def test_reaction_needs_to_beat_the_noise(self):
        assert lp.reaction_verdict([0.2, 0.3], 9.0)[0] == "reacted"
        assert lp.reaction_verdict([0.2, 0.3], 0.4)[0] == "no-reaction"
        assert lp.reaction_verdict([None, None], 9.0)[0] == "no-data"
        assert lp.reaction_verdict([0.2], None)[0] == "no-data"

    def test_a_moving_scene_is_inconclusive_not_no_reaction(self):
        """headless 干跑里按键确实送到了，画面判定却说"没反应"：场景自己一直在动。"""
        code, text = lp.reaction_verdict([44.3, 15.0], 11.2)
        assert code == "inconclusive"
        assert "by eye" in text

    def test_the_noisiest_baseline_pair_sets_the_bar(self):
        assert lp.reaction_verdict([0.3, 6.0], 8.0)[0] == "inconclusive"

    def test_delivery(self):
        assert lp.delivery_verdict(None)[0] == "unknown"
        assert lp.delivery_verdict({})[0] == "not-seen"
        # 焦点变化或只有松开，都不等于按键送到了
        assert lp.delivery_verdict({"FocusOut": 1, "KeyRelease": 1})[0] == "not-seen"
        assert lp.delivery_verdict({"KeyPress": 1, "KeyPress keycode 9": 1})[0] == "delivered"

    def test_key_events_are_counted_with_their_keycode(self):
        from types import SimpleNamespace

        from Xlib import X

        class EventDisplay:
            """pending_events()/next_event() 照 python-xlib：事件对象带 type 和 detail。"""
            def __init__(self, events):
                self.events = list(events)

            def pending_events(self):
                return len(self.events)

            def next_event(self):
                return self.events.pop(0)

        game = SimpleNamespace(id=0x400000)
        d = EventDisplay([SimpleNamespace(type=X.KeyPress, detail=9, window=game),
                          SimpleNamespace(type=X.KeyRelease, detail=9, window=game),
                          SimpleNamespace(type=X.FocusOut, detail=3, window=game)])
        assert lp.drain_events(d) == {"KeyPress": 1, "KeyPress keycode 9 on 0x400000": 1,
                                      "KeyRelease": 1, "KeyRelease keycode 9 on 0x400000": 1,
                                      "FocusOut": 1}

    def test_leaks(self):
        assert lp.leak_verdict(None)[0] == "unknown"
        assert lp.leak_verdict(b"")[0] == "contained"
        assert lp.leak_verdict(b"\x1b")[0] == "leaked"

    def test_a_pipe_is_not_a_terminal(self):
        """标准输入被重定向时，泄漏检查要说"无法判断"，而不是"没泄漏"。"""
        read_fd, write_fd = os.pipe()
        try:
            with lp.TerminalInput(read_fd) as terminal:
                assert terminal.read_pending(0.0) is None
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_motion_series_through_framediff(self):
        rng = np.random.default_rng(1)
        moving = [rng.integers(0, 255, (30, 30, 3), dtype=np.uint8) for _ in range(5)]
        still = [moving[0].copy() for _ in range(5)]
        focused = lp.framediff.summarize(lp.series_deltas(moving))
        unfocused = lp.framediff.summarize(lp.series_deltas(still))
        assert lp.framediff.motion_verdict(focused, unfocused)[0] == "frozen"

    def test_missing_frames_become_gaps(self):
        frame = np.zeros((4, 4, 3), np.uint8)
        assert lp.series_deltas([frame, None, frame, frame]) == [None, None, 0.0]

    def test_a_failing_capture_becomes_a_missing_frame(self):
        def boom():
            raise OSError("display went away")
        assert lp.capture_series(boom, 3, 0, sleep=lambda s: None) == [None, None, None]

    def test_changed_properties(self):
        before = {"GAMESCOPE_FOCUSED_WINDOW": [1], "STEAM_GAME": [881020]}
        after = {"GAMESCOPE_FOCUSED_WINDOW": [2], "STEAM_GAME": [881020], "NEW": [1]}
        assert lp.changed_properties(before, after) == {
            "GAMESCOPE_FOCUSED_WINDOW": [[1], [2]], "NEW": [None, [1]]}


class TestReport:
    def test_results_survive_a_crash_later_in_the_run(self, tmp_path):
        report = lp.Report(tmp_path, echo=lambda s: None)
        report.result("L1", "found", 1)
        # 不调 close()：模拟进程在这之后死掉
        assert "L1 found: 1" in (tmp_path / "report.md").read_text()
        record = json.loads((tmp_path / "report.jsonl").read_text().splitlines()[0])
        assert record["step"] == "L1" and record["value"] == 1

    def test_output_is_ascii(self, tmp_path):
        report = lp.Report(tmp_path, echo=lambda s: None)
        report.result("L1", "game window", {"name": "碧蓝幻想"})
        report.close()
        (tmp_path / "report.md").read_bytes().decode("ascii")
        (tmp_path / "report.jsonl").read_bytes().decode("ascii")

    def test_questions_default_to_no(self):
        assert lp.ask_yes("go?", read=lambda prompt: "") is False
        assert lp.ask_yes("go?", read=lambda prompt: " Y ") is True

        def eof(prompt):
            raise EOFError
        assert lp.ask_yes("go?", read=eof) is False


class TestCommandLine:
    def test_l1_always_runs_and_order_is_fixed(self):
        assert lp.parse_args(["--steps", "l4,L2"]).steps == ["L1", "L2", "L4"]
        assert lp.parse_args([]).steps == ["L1", "L2", "L3", "L4"]

    def test_l3_and_l4_bring_l2_along(self):
        """L3、L4 用的是 L2 选出来的截图办法。没有 L2，它们只会一声不响地跳过。"""
        assert lp.parse_args(["--steps", "L3"]).steps == ["L1", "L2", "L3"]
        assert lp.parse_args(["--steps", "L4"]).steps == ["L1", "L2", "L4"]
        assert lp.parse_args(["--steps", "L1"]).steps == ["L1"]

    @pytest.mark.parametrize("argv", [
        ["--frames", "1"], ["--frames", "0"], ["--frames", "-3"], ["--interval", "-0.5"],
    ])
    def test_settings_that_cannot_measure_are_rejected(self, argv):
        """--frames 0 曾让 L4 在保存第一帧时 IndexError；--frames 1 只能得出 no-data。"""
        with pytest.raises(SystemExit):
            lp.parse_args(argv)

    def test_unknown_steps_are_rejected(self):
        with pytest.raises(SystemExit):
            lp.parse_args(["--steps", "L1,L9"])

    def test_without_a_game_it_says_so_and_stops_cleanly(self, tmp_path, monkeypatch):
        """游戏没开时，L1 要说清楚，其余步骤跳过，而不是去连一个不存在的显示。"""
        import subprocess
        monkeypatch.setattr(lp, "find_game_processes", lambda appid: [])
        monkeypatch.setattr(lp.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout="tool 1.0\n", stderr=""))
        assert lp.main(["--out", str(tmp_path)]) == 0
        text = (tmp_path / "report.md").read_text()
        assert "No game found inside gamescope" in text
        assert "L2 skipped" in text and "L3 skipped" in text and "L4 skipped" in text


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
        assert lp.within_window(game, game.id)
        assert lp.within_window(child, game.id)

    @pytest.mark.parametrize("focus", ["overlay", "root", 0, 1])
    def test_anything_else_does_not(self, focus):
        """覆盖层、根窗口，以及 None / PointerRoot 这两个常量，都不是游戏。"""
        root, game, child, overlay = window_tree()
        target = {"overlay": overlay, "root": root}.get(focus, focus)
        assert not lp.within_window(target, game.id)


class FocusDisplay:
    """L3 用到的 python-xlib Display 方法，签名照文档。焦点按 focus_sequence 依次给出。"""

    def __init__(self, focus_sequence):
        self.focus_sequence = list(focus_sequence)
        self.sent = []

    def get_input_focus(self):
        focus = self.focus_sequence.pop(0) if len(self.focus_sequence) > 1 else self.focus_sequence[0]
        return SimpleNamespace(focus=focus)

    def keysym_to_keycode(self, keysym):
        return 9

    def xtest_fake_input(self, event_type, detail=0, time=0, root=0, x=0, y=0):
        self.sent.append((event_type, detail))

    def sync(self):
        pass

    def pending_events(self):
        return 0


@pytest.fixture
def l3(tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "ask_yes", lambda question, read=input: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(lp.time, "sleep", lambda seconds: None)

    def run(focus_sequence):
        root, game, child, overlay = window_tree()
        named = {"game": game, "child": child, "overlay": overlay}
        d = FocusDisplay([named[f] for f in focus_sequence])
        report = lp.Report(tmp_path, echo=lambda s: None)
        state = {"x": d, "capture": lambda: np.zeros((4, 4, 3), np.uint8), "window": game}
        lp.step_l3(SimpleNamespace(), report, state)
        report.close()
        return d, (tmp_path / "report.md").read_text()
    return run


class TestL3Aim:
    def test_nothing_is_sent_when_the_focus_is_elsewhere(self, l3):
        d, text = l3(["overlay"])
        assert d.sent == []
        assert "not on the game window" in text

    def test_both_escapes_go_out_when_a_game_child_has_focus(self, l3):
        from Xlib import X
        d, text = l3(["child"])
        assert [t for t, _ in d.sent] == [X.KeyPress, X.KeyRelease, X.KeyPress, X.KeyRelease]
        assert "key delivery" in text

    def test_a_focus_change_during_the_baseline_sends_nothing(self, l3):
        """取基准帧要两秒多。焦点在这期间换走了，第一个 Escape 也不能发。"""
        d, text = l3(["game", "overlay"])
        assert d.sent == []
        assert "left the game while the baseline frames were taken" in text

    def test_the_second_escape_waits_for_the_game_to_have_focus_again(self, l3):
        """第一次 Escape 之后焦点跑到了别处：第二次不能跟着发过去。"""
        d, text = l3(["game", "game", "overlay"])
        assert len(d.sent) == 2
        assert "second Escape: The nested focus left the game" in text


class TestL4Sampling:
    def test_properties_are_compared_between_the_two_phases(self, tmp_path, monkeypatch):
        """以前两次取样都在终端有焦点的时候，只在游戏有焦点时才变的属性永远比不出来。"""
        phase = {"now": "terminal"}

        def countdown(seconds, message, echo=print, sleep=None):
            phase["now"] = "focused" if message.startswith("Switch to the game") else "unfocused"

        focused_window = {"terminal": [9], "focused": [1], "unfocused": [9]}
        monkeypatch.setattr(lp, "countdown", countdown)
        monkeypatch.setattr(lp, "ask_yes", lambda question, read=input: True)
        monkeypatch.setattr(lp, "gamescope_root_properties",
                            lambda d: {"GAMESCOPE_FOCUSED_WINDOW": focused_window[phase["now"]]})
        monkeypatch.setattr(lp.time, "sleep", lambda seconds: None)

        rng = np.random.default_rng(3)
        report = lp.Report(tmp_path, echo=lambda s: None)
        state = {"x": SimpleNamespace(pending_events=lambda: 0),
                 "capture": lambda: rng.integers(0, 255, (8, 8, 3), dtype=np.uint8)}
        lp.step_l4(SimpleNamespace(frames=3, interval=0), report, state)
        report.close()

        records = [json.loads(line) for line in (tmp_path / "report.jsonl").read_text().splitlines()]
        diff = next(r["value"] for r in records
                    if r["name"] == "gamescope root properties, focused vs unfocused")
        assert diff == {"GAMESCOPE_FOCUSED_WINDOW": [[1], [9]]}
