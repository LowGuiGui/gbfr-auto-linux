# -*- coding: utf-8 -*-
"""调和规则。

decide() 是纯函数，所以这些规则可以在 Linux 上完整测 —— 而它们正是"世界变了
要自己跟上"这条性质的全部内容。每一条都对应一个真会发生的场景：游戏重启、
管道断了、人拿起手柄、伪装忘了关。
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import supervisor  # noqa: E402


def obs(**kw):
    base = dict(hwnd=1234, hwnd_valid=True, kmb_ready=True, pad_ready=True)
    base.update(kw)
    return supervisor.Observation(**base)


class TestPreferenceIsHonoured:
    def test_the_chosen_mode_wins_when_it_works(self):
        d = supervisor.decide("pad", obs())
        assert d.backend == "pad"
        assert d.degraded is False
        assert d.paused is False

    def test_kmb_is_chosen_when_asked_for(self):
        assert supervisor.decide("kmb", obs()).backend == "kmb"

    def test_it_is_not_a_suggestion(self):
        """手柄可用时选了键鼠，就不该"顺手"换成手柄。"""
        assert supervisor.decide("kmb", obs(pad_ready=True)).backend == "kmb"


class TestDegrading:
    def test_it_falls_back_when_the_chosen_mode_is_gone(self):
        d = supervisor.decide("pad", obs(pad_ready=False))
        assert d.backend == "kmb"
        assert d.degraded is True

    def test_degrading_says_why_and_says_it_will_return(self):
        """悄悄换模式和悄悄不动一样糟。"""
        d = supervisor.decide("pad", obs(pad_ready=False))
        assert "不可用" in d.reason
        assert "回去" in d.reason

    def test_it_returns_to_the_chosen_mode_by_itself(self):
        degraded = supervisor.decide("pad", obs(pad_ready=False))
        assert degraded.backend == "kmb"
        restored = supervisor.decide("pad", obs(pad_ready=True),
                                     current_backend="kmb")
        assert restored.backend == "pad"
        assert restored.degraded is False

    def test_nothing_available_pauses_rather_than_pretending(self):
        d = supervisor.decide("pad", obs(pad_ready=False, kmb_ready=False))
        assert d.backend == "none"
        assert d.paused is True


class TestSwitchingCannotStrandHeldKeys:
    """hold_move 按住的 W 留在旧后端上，就再也没人去松它了。"""

    def test_a_switch_releases_everything_first(self):
        d = supervisor.decide("pad", obs(pad_ready=False), current_backend="pad")
        assert d.actions[0] == "release_all", "松开必须排在换后端前面"

    def test_staying_on_the_same_backend_releases_nothing(self):
        d = supervisor.decide("pad", obs(), current_backend="pad")
        assert "release_all" not in d.actions

    def test_release_is_not_duplicated(self):
        d = supervisor.decide("pad", obs(hwnd=99, pad_ready=False),
                              current_backend="pad", current_hwnd=1234)
        assert d.actions.count("release_all") == 1


class TestTheWindowGoingAway:
    def test_an_invalid_window_pauses_and_re_finds_it(self):
        d = supervisor.decide("pad", obs(hwnd_valid=False))
        assert d.backend == "none"
        assert d.paused is True
        assert "reacquire_window" in d.actions

    def test_it_lets_go_of_held_keys_too(self):
        d = supervisor.decide("pad", obs(hwnd_valid=False))
        assert "release_all" in d.actions

    def test_a_restarted_game_reconnects_the_transport(self):
        """游戏重启后 hwnd 和 pid 都变了，注入的 DLL 也随进程没了。"""
        d = supervisor.decide("pad", obs(hwnd=5678), current_hwnd=1234)
        assert "reconnect_transport" in d.actions
        assert "release_all" in d.actions

    def test_the_same_window_does_not_reconnect(self):
        d = supervisor.decide("pad", obs(hwnd=1234), current_hwnd=1234)
        assert "reconnect_transport" not in d.actions


class TestYieldingToAPerson:
    def test_a_moving_physical_pad_pauses_automation(self):
        d = supervisor.decide("pad", obs(physical_pad_active=True))
        assert d.paused is True
        assert d.backend == "none"

    def test_it_lets_go_so_the_two_pads_do_not_fight(self):
        d = supervisor.decide("pad", obs(physical_pad_active=True))
        assert "release_all" in d.actions

    def test_it_says_why(self):
        d = supervisor.decide("pad", obs(physical_pad_active=True))
        assert "实体手柄" in d.reason

    def test_it_applies_in_kmb_mode_too(self):
        """人拿起手柄就是要自己玩，跟自动化用什么通道无关。"""
        assert supervisor.decide("kmb", obs(physical_pad_active=True)).paused is True


class TestSpoofIsGatedToPadMode:
    """键鼠模式下开伪装会把光标锁死在游戏窗口中央（2026-08-26 t_kmb 实测），
    整台机器都没法用了。"""

    def test_spoof_is_turned_off_outside_pad_mode(self):
        d = supervisor.decide("kmb", obs(spoof_on=True))
        assert "spoof_off" in d.actions

    def test_spoof_may_stay_on_in_pad_mode(self):
        d = supervisor.decide("pad", obs(spoof_on=True))
        assert "spoof_off" not in d.actions

    def test_degrading_out_of_pad_mode_also_drops_the_spoof(self):
        """退到键鼠却把伪装留着，光标就被锁住了，而人并没有选这个。"""
        d = supervisor.decide("pad", obs(pad_ready=False, spoof_on=True))
        assert d.backend == "kmb"
        assert "spoof_off" in d.actions

    def test_a_lost_window_drops_the_spoof(self):
        d = supervisor.decide("pad", obs(hwnd_valid=False, spoof_on=True))
        assert "spoof_off" in d.actions


class FakeXInput:
    def __init__(self, slots=(), moving=()):
        self.slots = list(slots)
        self.moving = set(moving)

    def connected_slots(self, dll, count=4):
        return list(self.slots)

    def read(self, dll, index=0):
        return index

    def is_neutral(self, reading):
        return reading not in self.moving


class TestPhysicalPadWatch:
    """我们自己的虚拟手柄也占一个 XInput 槽位。认不出它，就会把自己的输入当成
    人的，然后永远让开。"""

    def _watch(self, before, after, moving=()):
        xi = FakeXInput(slots=before)
        w = supervisor.PhysicalPadWatch(xi, dll=object(), resume_after=3.0)
        w.note_slots_before_connect()
        xi.slots = list(after)
        xi.moving = set(moving)
        w.note_slots_after_connect()
        return w, xi

    def test_it_identifies_our_own_slot(self):
        w, _ = self._watch(before=[0], after=[0, 1])
        assert w._ours == 1

    def test_our_own_input_is_not_mistaken_for_a_person(self):
        w, _ = self._watch(before=[0], after=[0, 1], moving=[1])
        assert w.active(now=100.0) is False

    def test_a_real_pad_moving_is_detected(self):
        w, _ = self._watch(before=[0], after=[0, 1], moving=[0])
        assert w.active(now=100.0) is True

    def test_a_pad_plugged_in_later_still_counts_as_physical(self):
        """后插的手柄不是我们的 —— 我们只有一个槽位，而且早就记下了。"""
        w, xi = self._watch(before=[0], after=[0, 1])
        xi.slots = [0, 1, 2]
        xi.moving = {2}
        assert w.active(now=100.0) is True

    def test_it_waits_before_taking_back_control(self):
        """两次输入之间的空档不该被当成"人走了"。"""
        w, xi = self._watch(before=[0], after=[0, 1], moving=[0])
        assert w.active(now=100.0) is True
        xi.moving = set()
        assert w.active(now=101.0) is True, "刚放下就抢回来会打架"
        assert w.active(now=104.0) is False

    def test_an_ambiguous_connect_refuses_to_guess(self):
        """同时多出两个槽位时认错的代价是把人的手柄当成自己的 —— 宁可不认。"""
        w, _ = self._watch(before=[0], after=[0, 1, 2])
        assert w._ours is None

    def test_no_dll_means_no_detection_rather_than_a_crash(self):
        w = supervisor.PhysicalPadWatch(FakeXInput(), dll=None)
        assert w.active(now=1.0) is False


class TestDecisionEquality:
    def test_equal_decisions_compare_equal(self):
        a = supervisor.decide("pad", obs())
        b = supervisor.decide("pad", obs())
        assert a == b

    def test_different_backends_do_not(self):
        assert supervisor.decide("pad", obs()) != supervisor.decide("kmb", obs())


@pytest.mark.parametrize("prefer", ["pad", "kmb"])
def test_reason_is_always_human_readable(prefer):
    for kwargs in ({}, {"pad_ready": False}, {"kmb_ready": False},
                   {"pad_ready": False, "kmb_ready": False},
                   {"hwnd_valid": False}, {"physical_pad_active": True}):
        d = supervisor.decide(prefer, obs(**kwargs))
        assert d.reason and len(d.reason) > 5
