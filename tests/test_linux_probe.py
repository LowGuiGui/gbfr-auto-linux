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

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import linux_probe as lp  # noqa: E402


def fake_proc(tmp_path, processes):
    """processes: {pid: {"environ": dict | None, "comm": str, "cmdline": str}}"""
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

    def __init__(self, write=None, returncode=0):
        self.calls = []
        self.write = write
        self.returncode = returncode

    def __call__(self, cmd, env=None, capture_output=False, text=False, timeout=None):
        import subprocess
        self.calls.append((cmd, env))
        if self.write:
            self.write(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr="")


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
        assert sent == (tmp_path / "rel" / "shot.png").resolve()

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

        d = EventDisplay([SimpleNamespace(type=X.KeyPress, detail=9),
                          SimpleNamespace(type=X.KeyRelease, detail=9),
                          SimpleNamespace(type=X.FocusOut, detail=3)])
        assert lp.drain_events(d) == {"KeyPress": 1, "KeyPress keycode 9": 1, "KeyRelease": 1,
                                      "KeyRelease keycode 9": 1, "FocusOut": 1}

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
