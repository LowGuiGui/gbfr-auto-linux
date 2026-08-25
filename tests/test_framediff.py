# -*- coding: utf-8 -*-
"""framediff.py —— A4 的判定逻辑。

真机上这一段只跑一次，而它要区分的两种情况（游戏停了 / 游戏在跑但不理输入）修法
完全不同。判错方向比没测更糟，所以判定的每条分支都要在这里走一遍。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import framediff  # noqa: E402


def _frame(value, size=(8, 8), channels=3):
    """一张纯色图。差分测的是两帧之差，纯色在这里是合理的输入。"""
    if channels is None:
        return np.full(size, value, dtype=np.uint8)
    return np.full((size[0], size[1], channels), value, dtype=np.uint8)


def _stats(mean, count=10):
    return {"count": count, "mean": mean, "max": mean, "median": mean, "dropped": 0}


class TestToGray:
    def test_averages_the_colour_channels(self):
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        frame[:, :, 0] = 30
        frame[:, :, 1] = 60
        frame[:, :, 2] = 90
        assert framediff.to_gray(frame).mean() == pytest.approx(60.0)

    def test_ignores_the_alpha_channel(self):
        """RGBA 里 alpha 参与平均会把亮度算错。"""
        rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        rgba[:, :, :3] = 100
        rgba[:, :, 3] = 255
        assert framediff.to_gray(rgba).mean() == pytest.approx(100.0)

    def test_accepts_an_already_grey_frame(self):
        assert framediff.to_gray(_frame(42, channels=None)).mean() == pytest.approx(42.0)

    def test_rejects_a_shape_it_cannot_read(self):
        with pytest.raises(ValueError):
            framediff.to_gray(np.zeros((2, 2, 2, 2), dtype=np.uint8))


class TestFrameDelta:
    def test_identical_frames_are_zero(self):
        assert framediff.frame_delta(_frame(120), _frame(120)) == 0.0

    def test_measures_the_difference(self):
        assert framediff.frame_delta(_frame(100), _frame(140)) == pytest.approx(40.0)

    def test_is_unsigned(self):
        """变亮和变暗都是"动了"。"""
        assert framediff.frame_delta(_frame(140), _frame(100)) == pytest.approx(40.0)

    def test_size_change_returns_none_instead_of_raising(self):
        """窗口可能在两次截图之间被拖动或改大小，不该让整段测试崩掉。"""
        assert framediff.frame_delta(_frame(100, (8, 8)), _frame(100, (9, 9))) is None


class TestSummarize:
    def test_basic_statistics(self):
        stats = framediff.summarize([1.0, 3.0, 5.0])
        assert stats["count"] == 3
        assert stats["mean"] == pytest.approx(3.0)
        assert stats["max"] == pytest.approx(5.0)
        assert stats["median"] == pytest.approx(3.0)

    def test_drops_unusable_frames_and_counts_them(self):
        stats = framediff.summarize([2.0, None, 4.0])
        assert stats["count"] == 2
        assert stats["dropped"] == 1
        assert stats["mean"] == pytest.approx(3.0)

    def test_all_none_is_not_a_crash(self):
        stats = framediff.summarize([None, None])
        assert stats["count"] == 0 and stats["dropped"] == 2

    def test_empty_input(self):
        assert framediff.summarize([])["count"] == 0


class TestMotionVerdict:
    def test_frozen_when_motion_collapses(self):
        """1.1 那个防挂机暂停应该长这样。"""
        code, text = framediff.motion_verdict(_stats(20.0), _stats(0.1))
        assert code == "frozen"
        text.encode("ascii")

    def test_running_when_motion_holds_up(self):
        assert framediff.motion_verdict(_stats(20.0), _stats(18.0))[0] == "running"

    def test_reduced_is_not_called_frozen(self):
        """后台降帧很常见，把它判成"暂停"会让人去修一个不存在的问题。"""
        assert framediff.motion_verdict(_stats(20.0), _stats(6.0))[0] == "reduced"

    def test_static_scene_is_refused_rather_than_guessed(self):
        """站在菜单里测，三段都接近 0 —— 此时任何判定都是编的。"""
        code, text = framediff.motion_verdict(_stats(0.05), _stats(0.04))
        assert code == "static-scene"
        assert "quest" in text

    def test_static_scene_beats_the_ratio_check(self):
        """两段都是 0 时比值可能是 1.0，看起来像"running"。必须先挡住。"""
        assert framediff.motion_verdict(_stats(0.1), _stats(0.1))[0] == "static-scene"

    def test_no_frames_is_reported_as_such(self):
        assert framediff.motion_verdict(_stats(0.0, count=0), _stats(5.0))[0] == "no-data"

    @pytest.mark.parametrize("unfocused", [0.1, 6.0, 18.0])
    def test_every_explanation_is_ascii(self, unfocused):
        framediff.motion_verdict(_stats(20.0), _stats(unfocused))[1].encode("ascii")


class TestInputVerdict:
    def test_reaching_when_the_stick_adds_motion(self):
        code, text = framediff.input_verdict(_stats(5.0), _stats(15.0), "running")
        assert code == "reaching"
        text.encode("ascii")

    def test_ignored_when_the_stick_changes_nothing(self):
        assert framediff.input_verdict(_stats(5.0), _stats(5.2), "running")[0] == "ignored"

    def test_moot_when_the_picture_is_frozen(self):
        """画面本来就不动，推摇杆看不出任何东西 —— 不能说成"输入被忽略"。"""
        assert framediff.input_verdict(_stats(0.0), _stats(0.0), "frozen")[0] == "moot"

    def test_moot_on_a_static_scene(self):
        assert framediff.input_verdict(_stats(0.0), _stats(0.0), "static-scene")[0] == "moot"

    def test_absolute_margin_guards_a_tiny_baseline(self):
        """idle 接近 0 时，光看比值一点噪声就能变成 1.5 倍。"""
        assert framediff.input_verdict(_stats(0.2), _stats(0.35), "running")[0] == "ignored"

    def test_no_frames_while_holding_the_stick(self):
        assert framediff.input_verdict(
            _stats(5.0), _stats(0.0, count=0), "running")[0] == "no-data"

    def test_reduced_motion_still_allows_an_input_answer(self):
        """后台降帧不妨碍判断输入有没有进去。"""
        assert framediff.input_verdict(_stats(3.0), _stats(12.0), "reduced")[0] == "reaching"
