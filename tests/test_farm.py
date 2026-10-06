# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""farm.py：战斗循环。

截图、输入、观测、停止和暂停全是替身，模板是几块随机纹理，贴在噪声背景上拼出每一页。
测的是循环自己的规则：开打只按一次、换页先松开、认不出时盲按有上限、计数、到数停下、
各种该停下的情况，以及不管从哪条路出去都先松开。
"""

import copy
from pathlib import Path

import numpy as np
import pytest

import config
import farm
import pages
import supervisor
from pagetree import PAGE_NAME, PAGE_RULES

NAMES = list(dict.fromkeys(pages.templates_used(PAGE_RULES)))
TEMPLATES = {name: np.random.default_rng(i + 1).integers(0, 256, (24, 40, 3), dtype=np.uint8)
             for i, name in enumerate(NAMES)}
SPOTS = {"flag_battle": (10, 10), "flag_battleresult": (60, 10), "flag_again": (10, 60),
         "flag_exit": (60, 60), "flag_continue": (110, 110)}
FLAGS = {
    PAGE_NAME.BATTLE: ["flag_battle"],
    PAGE_NAME.REWARD_AGAIN: ["flag_battleresult", "flag_again"],
    PAGE_NAME.REWARD_EXIT: ["flag_battleresult", "flag_exit"],
    PAGE_NAME.SCORE: ["flag_battleresult"],
    PAGE_NAME.PAUSE: ["flag_continue"],
    PAGE_NAME.UNKNOWN: [],
}


def frame_of(page, seed=99):
    """噪声背景上贴着这一页的模板。噪声和随机纹理的相关系数接近 0，贴上的那块是 1。"""
    frame = np.random.default_rng(seed).integers(0, 256, (180, 200, 3), dtype=np.uint8)
    for name in FLAGS[page]:
        x, y = SPOTS[name]
        tpl = TEMPLATES[name]
        frame[y:y + tpl.shape[0], x:x + tpl.shape[1]] = tpl
    return frame


class Backend:
    """输入后端的替身：记下被叫了什么，并像 XTestInput 那样维护 input.held 和 input.skipped
    （按下去一次就从零数起）。input.accepts 为假时什么都按不下去；input.press_lands 为假时
    只有中键按不下去（取不到窗口中心）。"""

    def __init__(self, window_input=None):
        self.calls = []
        self.input = window_input

    def is_ready(self):
        return True

    def _record(self, name):
        self.calls.append(name)

    def hold_move(self):
        self._record("hold_move")
        if self.input is not None and self.input.accepts:
            self.input.held = sorted(set(self.input.held) | {"w"})
            self.input.skipped = 0
        elif self.input is not None:
            self.input.skipped += 1

    def release_move(self):
        self._record("release_move")
        if self.input is not None:
            self.input.held = [h for h in self.input.held if h != "w"]

    def battle_press(self):
        self._record("battle_press")
        if self.input is not None and self.input.accepts and self.input.press_lands:
            self.input.held = sorted(set(self.input.held) | {"middle"})

    def battle_release(self):
        self._record("battle_release")
        if self.input is not None:
            self.input.held = [h for h in self.input.held if h != "middle"]

    def again(self):
        self._record("again")

    def confirm(self):
        self._record("confirm")
        if self.input is not None and not self.input.accepts:
            self.input.skipped += 1
        elif self.input is not None:
            self.input.skipped = 0

    def release_all(self):
        self._record("release_all")


class Input:
    """XTestInput 里循环会看的那几样：skipped、held、release_all。"""

    def __init__(self, accepts=True, press_lands=True):
        self.accepts = accepts
        self.press_lands = press_lands
        self.skipped = 0
        self.held = []
        self.releases = 0

    def release_all(self):
        self.releases += 1
        self.held = []


class Controls:
    """停止和暂停的替身。等过 max_waits 次就自己要求停止：循环要是该停不停，测试会以
    "收到停止命令"结束、断言失败，而不是一直跑下去。"""

    def __init__(self, max_waits=20):
        self.stop_requested = False
        self.paused = False
        self.pauses = []
        self.waits = []
        self.max_waits = max_waits

    def pause(self, reason):
        self.paused = True
        self.pauses.append(reason)

    def wait(self, seconds):
        self.waits.append(seconds)
        if len(self.waits) >= self.max_waits:
            self.stop_requested = True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Frames:
    """capture() 的替身：按顺序给出这些帧（页面名就拼成那一页），给完了就一直给最后一帧。"""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = 0
        self.last_ms = 1200

    def __call__(self):
        self.calls += 1
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return frame_of(item) if isinstance(item, PAGE_NAME) else item


def cfg(**overrides):
    values = copy.deepcopy(config.DEFAULTS)
    for dotted, value in overrides.items():
        section, key = dotted.split("__")
        values[section][key] = value
    return config.Config(values)


def world(valid=True, hwnd=0x400000, ready=True):
    return supervisor.Observation(hwnd=hwnd, hwnd_valid=valid, kmb_ready=ready)


def make(*frames, window_input=None, settings=None, observe=None, **kwargs):
    window_input = window_input if window_input is not None else Input()
    backend = Backend(window_input)
    controls = Controls()
    clock = Clock()
    f = farm.Farm(Frames(*frames), backend, observe or (lambda: world()), controls, TEMPLATES,
                  settings or cfg(), window_input=window_input, clock=clock, **kwargs)
    return f, backend, controls, clock


class TestRecognising:
    @pytest.mark.parametrize("page", list(FLAGS))
    def test_every_page_is_told_apart(self, page):
        f, *_ = make(page)
        assert f._recognise(frame_of(page))[0] == page


class TestActing:
    def test_a_battle_is_started_once_and_let_go_on_the_result_page(self):
        f, backend, *_ = make(PAGE_NAME.BATTLE, PAGE_NAME.BATTLE, PAGE_NAME.REWARD_AGAIN)
        for _ in range(3):
            assert f.tick() is None
        assert backend.calls == ["hold_move", "battle_press", "release_move", "battle_release",
                                 "confirm"]
        assert f.battles == 1

    @pytest.mark.parametrize("page, call", [
        (PAGE_NAME.SCORE, "confirm"), (PAGE_NAME.PAUSE, "confirm"),
        (PAGE_NAME.REWARD_AGAIN, "confirm"), (PAGE_NAME.REWARD_EXIT, "again"),
    ])
    def test_each_page_gets_its_action(self, page, call):
        """动作照 pagetree.PAGE_ACTIONS。结算页的按钮写着"撤销再次挑战"（flag_again，也就是
        已经选了再战）就确认；写着"再次挑战"（flag_exit，现在选的是退出）就先按 3 换成再战。"""
        f, backend, *_ = make(page)
        f.tick()
        assert backend.calls == [call]

    def test_blind_confirms_on_an_unknown_page_are_capped(self, log_file):
        f, backend, *_ = make(PAGE_NAME.UNKNOWN, settings=cfg(loop__max_blind_taps=2))
        for _ in range(5):
            f.tick()
        assert backend.calls == ["confirm", "confirm"]
        assert log_file().count("不再按键") == 1

    def test_a_known_page_resets_the_blind_count(self):
        f, backend, *_ = make(PAGE_NAME.UNKNOWN, PAGE_NAME.UNKNOWN, PAGE_NAME.SCORE,
                              PAGE_NAME.UNKNOWN, PAGE_NAME.UNKNOWN,
                              settings=cfg(loop__max_blind_taps=2))
        for _ in range(5):
            f.tick()
        assert backend.calls.count("confirm") == 5

    def test_a_battle_start_that_did_not_land_is_tried_again(self):
        """输入那一层没按下去（焦点不在游戏上），下一轮还得再按，而不是以为已经在打了。"""
        window_input = Input(accepts=False)
        f, backend, *_ = make(PAGE_NAME.BATTLE, window_input=window_input)
        f.tick()
        window_input.accepts = True
        f.tick()
        f.tick()
        assert backend.calls == ["hold_move", "battle_press", "hold_move", "battle_press"]


class TestHalfLandedStarts:
    def test_a_start_where_only_w_landed_is_finished_next_round(self):
        """KmbBackend 取不到窗口中心时不发中键：W 按着、中键没有，不能当成已经开打。"""
        window_input = Input(press_lands=False)
        f, backend, *_ = make(PAGE_NAME.BATTLE, window_input=window_input,
                              battle_inputs={"w", "middle"})
        f.tick()
        window_input.press_lands = True
        f.tick()
        f.tick()
        assert backend.calls == ["hold_move", "battle_press", "hold_move", "battle_press"]
        assert window_input.held == ["middle", "w"]

    def test_starts_that_keep_landing_only_halfway_pause_it(self):
        """取不到窗口中心时 KmbBackend 根本不发中键，输入那一层的 skipped 不会动：循环自己数。"""
        window_input = Input(press_lands=False)
        f, backend, controls, _ = make(PAGE_NAME.BATTLE, window_input=window_input,
                                       battle_inputs={"w", "middle"})
        f.tick()
        f.tick()
        assert not controls.paused
        f.tick()
        assert controls.paused and "开打" in controls.pauses[0]
        assert backend.calls[-1] == "release_all" and window_input.held == []

    def test_a_start_that_lands_resets_the_count(self):
        window_input = Input(press_lands=False)
        f, backend, controls, _ = make(PAGE_NAME.BATTLE, PAGE_NAME.BATTLE, PAGE_NAME.SCORE,
                                       PAGE_NAME.BATTLE, PAGE_NAME.BATTLE,
                                       window_input=window_input, battle_inputs={"w", "middle"})
        for _ in range(5):
            f.tick()
        assert not controls.paused

    def test_a_half_landed_start_is_let_go_on_the_next_page(self):
        window_input = Input(press_lands=False)
        f, backend, *_ = make(PAGE_NAME.BATTLE, PAGE_NAME.SCORE, window_input=window_input,
                              battle_inputs={"w", "middle"})
        f.tick()
        f.tick()
        assert backend.calls == ["hold_move", "battle_press", "release_move", "battle_release",
                                 "confirm"]
        assert window_input.held == []


class TestCounting:
    def test_a_result_page_without_a_battle_before_it_does_not_count(self):
        f, *_ = make(PAGE_NAME.SCORE, PAGE_NAME.BATTLE, PAGE_NAME.SCORE, PAGE_NAME.SCORE)
        for _ in range(4):
            f.tick()
        assert f.battles == 1

    def test_it_stops_when_the_count_is_reached(self):
        f, backend, *_ = make(PAGE_NAME.BATTLE, PAGE_NAME.SCORE, repeats=1)
        assert "到数" in f.run()
        assert backend.calls[-1] == "release_all" and f.battles == 1

    @pytest.mark.parametrize("repeats", [0, -1])
    def test_a_count_below_one_is_refused(self, repeats):
        """不限次数是 None；0 次要是照收，循环会先打完一场才发现到数了。"""
        with pytest.raises(ValueError, match="repeats"):
            make(PAGE_NAME.SCORE, repeats=repeats)


class TestStopping:
    def test_a_stop_ends_it_before_the_next_capture(self):
        f, backend, controls, _ = make(PAGE_NAME.SCORE)
        controls.stop_requested = True
        assert "停止" in f.tick()
        assert f._capture.calls == 0 and backend.calls == []

    def test_a_stop_during_the_capture_ends_it_before_matching(self):
        """匹配要花零点几秒；停止已经来了，就不该再花这个时间。"""
        f, backend, controls, _ = make(PAGE_NAME.SCORE)
        capture = f._capture
        matched = []

        def capture_then_stop():
            frame = capture()
            controls.stop_requested = True
            return frame
        f._capture = capture_then_stop
        f._recognise = lambda frame: matched.append(frame) or (PAGE_NAME.SCORE, {})
        assert "停止" in f.tick()
        assert backend.calls == [] and matched == []

    def test_a_lost_window_stops_it_and_says_so(self):
        """supervisor 的说法是"正在重新查找"，这一版并不找，所以理由要是自己的这一句。"""
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: world(valid=False))
        assert f.tick() == "游戏窗口不在了"

    def test_lost_input_stops_it(self):
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: world(ready=False))
        assert f.tick().startswith("输入用不了")

    def test_a_different_window_means_the_game_restarted(self):
        seen = iter([world(hwnd=0x400000), world(hwnd=0x600000)])
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: next(seen))
        assert f.tick() is None
        assert "换了一个" in f.tick()

    def test_the_preferred_backend_is_the_one_checked(self):
        """循环只有一个后端：观测里它可用就跑，supervisor 想退到另一个，就停下。"""
        pad_only = supervisor.Observation(hwnd=0x400000, hwnd_valid=True, kmb_ready=False,
                                          pad_ready=True)
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: pad_only, prefer="pad")
        assert f.tick() is None
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: pad_only)
        assert f.tick().startswith("输入用不了")
        # 两个都可用时，supervisor 照偏好选；偏好没传给它的话，它会选 kmb，循环就停了
        both = supervisor.Observation(hwnd=0x400000, hwnd_valid=True, kmb_ready=True,
                                      pad_ready=True)
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: both, prefer="pad")
        assert f.tick() is None

    def test_an_unknown_backend_preference_is_refused(self):
        """拼错的偏好，supervisor 照样会选出 kmb，循环却永远对不上它，第一轮就停下。"""
        with pytest.raises(ValueError, match="prefer"):
            make(PAGE_NAME.SCORE, prefer="kbm")

    def test_an_action_this_version_cannot_carry_out_stops_it(self):
        """观测里说焦点伪装开着：supervisor 要 spoof_off，这一版没有伪装可关，就停下。"""
        spoofed = supervisor.Observation(hwnd=0x400000, hwnd_valid=True, kmb_ready=True,
                                         spoof_on=True)
        f, *_ = make(PAGE_NAME.SCORE, observe=lambda: spoofed)
        assert "spoof_off" in f.tick()

    def test_three_unusable_captures_in_a_row_stop_it(self):
        blank = np.zeros((180, 200, 3), np.uint8)
        f, *_ = make(None, blank, None)
        assert f.tick() is None and f.tick() is None
        assert "截不到" in f.tick()

    def test_a_good_frame_resets_the_capture_count(self):
        f, *_ = make(None, None, PAGE_NAME.SCORE, None, None, PAGE_NAME.SCORE)
        assert [f.tick() for _ in range(6)] == [None] * 6

    @pytest.mark.parametrize("page, seconds, stops", [
        (PAGE_NAME.SCORE, 121, True), (PAGE_NAME.SCORE, 119, False),
        (PAGE_NAME.BATTLE, 901, True), (PAGE_NAME.BATTLE, 300, False),
    ])
    def test_a_page_that_lasts_too_long_stops_it(self, page, seconds, stops):
        f, _, _, clock = make(page)
        assert f.tick() is None
        clock.now += seconds
        reason = f.tick()
        assert (reason is not None and "超过" in reason) is stops


class TestLateCommands:
    """截图和匹配要花时间。这期间来的停止或暂停，不能再放过一个按键。"""

    def _arrives_during(self, f, controls, step, command):
        original = getattr(f, step)

        def then_command(*args):
            result = original(*args)
            if command == "stop":
                controls.stop_requested = True
            else:
                controls.paused = True
            return result
        setattr(f, step, then_command)

    @pytest.mark.parametrize("step", ["_capture", "_recognise"])
    def test_a_pause_that_arrives_mid_round_sends_nothing(self, step):
        f, backend, controls, _ = make(PAGE_NAME.SCORE)
        self._arrives_during(f, controls, step, "pause")
        assert f.tick() is None
        assert backend.calls == ["release_all"]

    @pytest.mark.parametrize("command", ["pause", "stop"])
    def test_a_command_during_matching_comes_before_a_page_that_lasted_too_long(self, command):
        """匹配期间来了暂停，这一页又刚好待过了头：先办暂停，不然本该能接着跑的循环就停了。
        停止也一样，停下的理由是那条命令。"""
        f, backend, controls, clock = make(PAGE_NAME.SCORE)
        assert f.tick() is None
        clock.now += 1000
        self._arrives_during(f, controls, "_recognise", command)
        reason = f.tick()
        if command == "pause":
            assert reason is None and f._pause_noted
        else:
            assert "停止" in reason
        assert backend.calls[-1] == ("release_all" if command == "pause" else "confirm")

    def test_a_battle_that_ends_as_a_pause_arrives_is_still_counted(self):
        f, _, controls, _ = make(PAGE_NAME.BATTLE, PAGE_NAME.SCORE)
        f.tick()
        self._arrives_during(f, controls, "_recognise", "pause")
        assert f.tick() is None
        assert f.battles == 1

    def test_a_pause_during_a_failed_capture_lets_go_at_once(self):
        f, backend, controls, _ = make(PAGE_NAME.BATTLE, None)
        f.tick()
        self._arrives_during(f, controls, "_capture", "pause")
        assert f.tick() is None
        assert backend.calls == ["hold_move", "battle_press", "release_all"]
        assert f._capture_failures == 0

    def test_a_pause_on_the_third_failed_capture_pauses_instead_of_stopping(self):
        f, _, controls, _ = make(None)
        f.tick()
        f.tick()
        self._arrives_during(f, controls, "_capture", "pause")
        assert f.tick() is None and controls.paused

    def test_a_stop_that_arrives_during_matching_sends_nothing(self):
        f, backend, controls, _ = make(PAGE_NAME.SCORE)
        self._arrives_during(f, controls, "_recognise", "stop")
        assert "停止" in f.tick()
        assert backend.calls == []


class TestPausing:
    def test_a_pause_lets_go_once_and_captures_nothing(self):
        f, backend, controls, _ = make(PAGE_NAME.BATTLE)
        f.tick()
        controls.paused = True
        f.tick()
        f.tick()
        assert backend.calls == ["hold_move", "battle_press", "release_all"]
        assert f._capture.calls == 1
        controls.paused = False
        f.tick()
        assert backend.calls[-2:] == ["hold_move", "battle_press"]

    def test_paused_time_does_not_count_against_the_page(self):
        f, _, controls, clock = make(PAGE_NAME.SCORE)
        f.tick()
        controls.paused = True
        f.tick()
        clock.now += 500
        controls.paused = False
        assert f.tick() is None
        clock.now += 60
        assert f.tick() is None

    def test_presses_that_keep_missing_pause_it(self):
        window_input = Input(accepts=False)
        f, backend, controls, _ = make(PAGE_NAME.SCORE, window_input=window_input)
        for _ in range(3):
            f.tick()
        assert controls.paused and len(controls.pauses) == 1
        assert window_input.releases == 1 and backend.calls[-1] == "release_all"

    def test_a_resume_does_not_pause_again_on_the_misses_already_seen(self):
        """认不出的页面已经点满了，resume 以后一下都不再按：输入那一层的计数还停在 3，可那几次
        暂停前就有人看过了，不能马上又暂停。"""
        window_input = Input(accepts=False)
        f, _, controls, _ = make(PAGE_NAME.UNKNOWN, window_input=window_input,
                                 settings=cfg(loop__max_blind_taps=3))
        for _ in range(3):
            f.tick()
        assert len(controls.pauses) == 1
        controls.paused = False
        f.tick()
        f.tick()
        assert not controls.paused and len(controls.pauses) == 1

    def test_three_new_misses_after_a_resume_pause_it_again(self):
        window_input = Input(accepts=False)
        f, _, controls, _ = make(PAGE_NAME.SCORE, window_input=window_input)
        for _ in range(3):
            f.tick()
        controls.paused = False
        f.tick()
        f.tick()
        assert not controls.paused
        f.tick()
        assert controls.paused and len(controls.pauses) == 2

    def test_a_press_that_lands_after_a_resume_restarts_the_count(self):
        """按下去一次，输入那一层就从零数起；resume 时记下的那个数就不再作数。"""
        window_input = Input(accepts=False)
        f, _, controls, _ = make(PAGE_NAME.SCORE, window_input=window_input)
        for _ in range(3):
            f.tick()
        controls.paused = False
        window_input.accepts = True
        f.tick()
        window_input.accepts = False
        f.tick()
        f.tick()
        assert not controls.paused
        f.tick()
        assert controls.paused and len(controls.pauses) == 2


class TestRun:
    def test_an_error_still_lets_go_of_everything(self):
        f, backend, *_ = make(PAGE_NAME.BATTLE, RuntimeError("boom"))
        assert "出错" in f.run()
        assert backend.calls[-1] == "release_all" and f._input.releases == 1

    def test_it_waits_out_the_rest_of_the_interval(self):
        f, _, controls, clock = make(PAGE_NAME.SCORE, repeats=None)
        ticks = iter([None, "enough"])
        f.tick = lambda: next(ticks)
        assert f.run() == "enough"
        assert controls.waits == [3.0]

    def test_every_template_is_scored_when_scores_are_logged(self, log_file):
        """战斗页在判定树的第一条就命中了；记分数时其余几张也得算，日志里才不缺。"""
        f, *_ = make(PAGE_NAME.BATTLE, settings=cfg(detect__log_scores=True))
        f.tick()
        line = next(line for line in log_file().splitlines() if "页面 battle" in line)
        assert all(name in line for name in NAMES)

    def test_without_score_logging_matching_stops_early(self, log_file):
        f, *_ = make(PAGE_NAME.BATTLE)
        page, scores = f._recognise(frame_of(PAGE_NAME.BATTLE))
        assert page == PAGE_NAME.BATTLE and list(scores) == ["flag_battle"]

    def test_one_log_line_per_round_with_the_scores_when_asked(self, log_file):
        f, *_ = make(PAGE_NAME.SCORE, settings=cfg(detect__log_scores=True))
        f.tick()
        line = next(line for line in log_file().splitlines() if "页面 score" in line)
        assert "确认" in line and "截图 1200 ms" in line and "flag_battleresult 1.000" in line


class TestAnomalies:
    def test_unknown_frames_are_saved_up_to_the_limit(self, tmp_path):
        saved = []
        f, *_ = make(PAGE_NAME.UNKNOWN, anomaly_dir=tmp_path / "anomalies",
                     settings=cfg(detect__save_anomaly_frames=True, detect__max_anomaly_frames=2),
                     save_frame=lambda frame, path: saved.append(path))
        for _ in range(4):
            f.tick()
        assert len(saved) == 2 and all(p.parent == tmp_path / "anomalies" for p in saved)

    def test_the_cap_counts_frames_already_in_the_directory(self, tmp_path):
        """上限管的是目录：每次重跑都再存满一遍，目录就会一直长下去。"""
        anomalies = tmp_path / "anomalies"
        anomalies.mkdir()
        for i in range(2):
            (anomalies / f"unknown-earlier-{i}.png").write_bytes(b"x")
        saved = []
        f, *_ = make(PAGE_NAME.UNKNOWN, anomaly_dir=anomalies,
                     settings=cfg(detect__save_anomaly_frames=True, detect__max_anomaly_frames=3),
                     save_frame=lambda frame, path: saved.append(path))
        for _ in range(3):
            f.tick()
        assert len(saved) == 1

    def test_a_directory_that_cannot_be_listed_does_not_stop_it(self, tmp_path, monkeypatch,
                                                                log_file):
        def unreadable(self, pattern):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(farm.Path, "glob", unreadable)
        saved = []
        f, *_ = make(PAGE_NAME.UNKNOWN, anomaly_dir=tmp_path,
                     settings=cfg(detect__save_anomaly_frames=True),
                     save_frame=lambda frame, path: saved.append(path))
        assert f.tick() is None and f.tick() is None
        assert saved == [] and "数不了" in log_file()

    def test_nothing_is_saved_unless_asked(self, tmp_path):
        saved = []
        f, *_ = make(PAGE_NAME.UNKNOWN, anomaly_dir=tmp_path,
                     save_frame=lambda frame, path: saved.append(path))
        f.tick()
        assert saved == []


class TestConstruction:
    def test_a_window_input_is_required(self):
        """开打按下去没有，只有输入那一层看得到：没有它，就没法判断，不能当成都成功了。"""
        with pytest.raises(TypeError):
            farm.Farm(Frames(PAGE_NAME.SCORE), Backend(), lambda: world(), Controls(),
                      TEMPLATES, cfg())


class TestTemplates:
    TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"

    def test_the_real_templates_load_under_the_tree_s_names(self):
        templates = farm.load_templates(self.TEMPLATE_DIR)
        assert list(templates) == NAMES
        assert all(t.dtype == np.uint8 and t.ndim == 3 and t.shape[2] == 3
                   for t in templates.values())

    def test_a_scale_resizes_them(self):
        full = farm.load_templates(self.TEMPLATE_DIR)
        half = farm.load_templates(self.TEMPLATE_DIR, scale=0.5)
        for name in NAMES:
            h, w = full[name].shape[:2]
            assert half[name].shape[:2] == (round(h * 0.5), round(w * 0.5))

    @pytest.mark.parametrize("scale", [0, -0.5, float("nan"), float("inf")])
    def test_a_scale_that_is_not_a_positive_number_is_refused(self, scale):
        with pytest.raises(RuntimeError, match="正数"):
            farm.load_templates(self.TEMPLATE_DIR, scale=scale)

    def test_a_scale_that_shrinks_a_template_to_nothing_is_refused(self):
        """缩成一个像素的模板没有方差，归一化相关系数对它给满分：每一帧都会被认成战斗页。"""
        with pytest.raises(RuntimeError, match="太小"):
            farm.load_templates(self.TEMPLATE_DIR, scale=0.01)

    @pytest.mark.parametrize("side, refused", [(farm.MIN_TEMPLATE_SIDE - 1, True),
                                               (farm.MIN_TEMPLATE_SIDE, False)])
    def test_a_template_too_small_at_its_own_size_is_refused(self, tmp_path, side, refused):
        """不缩放也照样看大小：模板目录里本来就放着一张太小的，也不能拿去匹配。"""
        from PIL import Image
        noise = np.random.default_rng(7).integers(0, 256, (side, side, 3), dtype=np.uint8)
        Image.fromarray(noise).save(tmp_path / "tiny.png")
        if refused:
            with pytest.raises(RuntimeError, match="太小"):
                farm.load_templates(tmp_path, files=["tiny.png"])
        else:
            assert farm.load_templates(tmp_path, files=["tiny.png"])["tiny"].shape == (side, side, 3)

    def test_a_flat_template_is_refused(self, tmp_path):
        from PIL import Image
        Image.new("RGB", (40, 24), (90, 90, 90)).save(tmp_path / "flat.png")
        with pytest.raises(RuntimeError, match="纯色"):
            farm.load_templates(tmp_path, files=["flat.png"])

    def test_a_missing_template_stops_it_from_starting(self, tmp_path):
        with pytest.raises(RuntimeError, match="flag_battle.png"):
            farm.load_templates(tmp_path)
