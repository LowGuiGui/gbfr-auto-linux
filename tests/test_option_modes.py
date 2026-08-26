# -*- coding: utf-8 -*-
"""Option 的调和与两种操作方式。

supervisor 管规则，这里管**执行**：决定出来之后有没有真的照做。中间任何一环
漏掉，表现都是同一种 —— 脚本安静地不干活。
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from option import Option  # noqa: E402


class FakeWI:
    """WindowInput 的替身。Option 只通过这几个口子碰 Windows。"""

    def __init__(self, ready=True, hwnd=1234):
        self.calls = []
        self.mode = "inject"
        self.hwnd = hwnd
        self._ready = ready
        self.spoof_active = False

    def set_target(self, t):
        self.calls.append(("target", t))

    def is_ready(self):
        return self._ready

    def has_window(self):
        return self.hwnd is not None

    def key_press(self, k):
        self.calls.append(("press", k))

    def key_release(self, k):
        self.calls.append(("release", k))

    def key_tap(self, k):
        self.calls.append(("tap", k))

    def mouse_press(self, x, y, b):
        self.calls.append(("mdown", b))

    def mouse_release(self, x, y, b):
        self.calls.append(("mup", b))

    def enable_fallback(self):
        self.mode = "fallback"

    def disable_inject(self):
        self.calls.append(("disable_inject",))

    def disable_focus_spoof(self):
        self.calls.append(("spoof_off",))
        self.spoof_active = False
        return True


@pytest.fixture
def opt(monkeypatch):
    def build(**kw):
        monkeypatch.setattr("option.WindowInput", lambda: FakeWI(**kw))
        o = Option(root=None)
        monkeypatch.setattr(o, "_get_center", lambda: (960, 540))
        return o
    return build


class TestPreferredMode:
    def test_default_is_kmb(self, opt):
        assert opt().preferred_mode == "kmb"

    def test_unknown_mode_is_refused(self, opt):
        """悄悄接受一个不认识的模式，之后每一步都会以为自己在另一种模式里。"""
        with pytest.raises(ValueError):
            opt().set_preferred_mode("joystick")

    def test_choosing_pad_without_one_degrades_and_says_so(self, opt):
        o = opt()
        o.set_preferred_mode("pad")
        d = o.poll()
        assert d.backend == "kmb"
        assert o.degraded is True
        assert "不可用" in o.status


class TestReconcileExecutes:
    def test_a_lost_window_pauses(self, opt):
        o = opt()
        o._wi.hwnd = None
        o.poll()
        assert o.paused is True
        assert o.is_ready() is False

    def test_a_lost_window_releases_held_keys(self, opt):
        o = opt()
        o.start_battle()
        o._wi.calls.clear()
        o._wi.hwnd = None
        o.poll()
        assert ("release", "w") in o._wi.calls

    def test_a_lost_window_clears_the_battle_flag(self, opt):
        """松了手却还记着"正在打"，下一次 start_battle 会直接返回 —— 而人看到
        的是脚本不动了。"""
        o = opt()
        o.start_battle()
        assert o._is_battle_ing is True
        o._wi.hwnd = None
        o.poll()
        assert o._is_battle_ing is False

    def test_a_restarted_game_is_re_found_by_title(self, opt):
        o = opt()
        o.set_target("Granblue")
        o.poll()
        o._wi.calls.clear()
        o._wi.hwnd = None
        o.poll()
        assert ("target", "Granblue") in o._wi.calls

    def test_spoof_is_dropped_outside_pad_mode(self, opt):
        o = opt()
        o._wi.spoof_active = True
        o.poll()
        assert ("spoof_off",) in o._wi.calls

    def test_status_is_human_readable(self, opt):
        o = opt()
        o.poll()
        assert o.status and o.status != "未开始"


class TestPausedMeansPaused:
    def test_actions_are_skipped_while_paused(self, opt):
        o = opt()
        o._wi.hwnd = None
        o.poll()
        o._wi.calls.clear()
        o.start_battle()
        o.switch_again()
        o.tap_confirm()
        assert o._wi.calls == []

    def test_it_resumes_when_the_window_comes_back(self, opt):
        o = opt()
        o._wi.hwnd = None
        o.poll()
        assert o.paused is True
        o._wi.hwnd = 4321
        o.poll()
        assert o.paused is False
        o.switch_again()
        assert ("tap", "3") in o._wi.calls


class TestPanic:
    def test_it_releases_everything(self, opt):
        o = opt()
        o.start_battle()
        o._wi.calls.clear()
        o.panic()
        assert ("release", "w") in o._wi.calls
        assert ("mup", "middle") in o._wi.calls

    def test_it_turns_the_spoof_off(self, opt):
        """键鼠模式下伪装会把光标锁死。救命开关必须能把它关掉。"""
        o = opt()
        o._wi.spoof_active = True
        o.panic()
        assert ("spoof_off",) in o._wi.calls

    def test_it_stops_further_actions(self, opt):
        o = opt()
        o.panic()
        o._wi.calls.clear()
        o.start_battle()
        assert o._wi.calls == []

    def test_it_works_even_if_releasing_throws(self, opt):
        o = opt()

        class Grumpy:
            name = "kmb"

            def release_all(self):
                raise RuntimeError("管道已经死了")

        o._backend = Grumpy()
        o.panic()          # 不抛就算过
        assert o.paused is True


class TestClearAllIsUnconditional:
    def test_it_releases_even_when_the_battle_flag_is_wrong(self, opt):
        """崩溃或换后端之后状态可能已经不一致。按着的键留在那里最糟。"""
        o = opt()
        o.start_battle()
        o._is_battle_ing = False      # 人为制造不一致
        o._wi.calls.clear()
        o.clear_all()
        assert ("release", "w") in o._wi.calls


class TestPadBackendLifecycle:
    def test_the_pad_backend_is_not_rebuilt_every_poll(self, opt, monkeypatch):
        """每次调和都新建的话，新对象不知道现在按着什么，摇杆会被漏在推着的状态。"""
        o = opt()
        o._pad = object()
        o.set_preferred_mode("pad")
        o.poll()
        first = o._backend
        o.poll()
        assert o._backend is first

    def test_disabling_the_pad_releases_it_first(self, opt):
        o = opt()

        class FakePad:
            def __init__(self):
                self.reports = []
                self.closed = False

            def send(self, r):
                self.reports.append((r.wButtons, r.sThumbLY))

            def close(self):
                self.closed = True

        p = FakePad()
        o._pad = p
        o.set_preferred_mode("pad")
        o.poll()
        o._backend.hold_move()
        o.disable_pad()
        assert p.reports[-1] == (0, 0), "拔手柄之前必须把摇杆归位"
        assert p.closed is True


class TestMainWiring:
    """界面那一层的接线。缺任何一根，调和就永远不会被调用。"""

    SOURCE = (REPO / "main.py").read_text(encoding="utf-8")

    def test_the_loop_reconciles_before_it_acts(self):
        loop = self.SOURCE[self.SOURCE.index("def job_loop(self):"):]
        loop = loop[:loop.index("self.screen = capture(")]
        assert "_sync_input_status()" in loop

    def test_the_loop_stops_when_paused(self):
        loop = self.SOURCE[self.SOURCE.index("def job_loop(self):"):]
        loop = loop[:loop.index("self.screen = capture(")]
        assert "if self._option.paused:" in loop

    def test_f12_is_bound_to_panic(self):
        assert "keyboard.Key.f12" in self.SOURCE
        assert "self._on_panic" in self.SOURCE

    def test_the_backend_radio_exists_for_both_modes(self):
        assert 'value="kmb"' in self.SOURCE
        assert 'value="pad"' in self.SOURCE

    def test_a_failed_pad_connect_falls_back_visibly(self):
        """选中了手柄却接不上，界面还留在"手柄"上 = 界面说一套做一套。"""
        block = self.SOURCE[self.SOURCE.index("def _apply_backend_mode"):]
        block = block[:block.index("def _sync_input_status")]
        assert '_backend_mode.set("kmb")' in block
        assert "self.log(" in block

    def test_panic_stops_the_loop_rather_than_starting_it(self):
        """F1 是启动、F2 才是停止，而 _on_f1 在循环已经在跑时是空操作 ——
        急停里写成 _on_f1 的话，它根本停不下循环，而且看不出来。"""
        block = self.SOURCE[self.SOURCE.index("def _on_panic"):]
        block = block[:block.index("def _on_f1")]
        assert "self._on_f2()" in block
        assert "self._on_f1()" not in block

    def test_the_pad_mapping_is_actually_read_from_config(self):
        """不接上的话 [pad] 就是个摆设：改了配置没效果，也没有任何提示。
        G1 正是要靠改这一段来修映射的。"""
        block = self.SOURCE[self.SOURCE.index("self._option = Option("):]
        block = block[:block.index("self._anomalies_saved")]
        assert 'pad_mapping=self.cfg.section("pad")' in block
