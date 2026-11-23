# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import maa_guard
from maa_guard import NativeGuard, input_conflict, key_codes, key_nodes, validate_controller
from maa_target import ProbeError
from test_xtest_input import KEYMAP, nested

ROOT = Path(__file__).resolve().parents[1]
THREE = (12, 0x33)  # keycode and keysym of "3" in the fake nested keyboard map


@pytest.mark.parametrize("change", [
    {"type": "win32"}, {"input_method": 1}, {"screencap_method": 1},
    {"use_win32_vk_code": True}, {"eis_socket_path": "/other"},
    {"pw_node_id": 0}, {"pw_node_id": True},
])
def test_other_controller_or_input_convention_refused(change):
    info = dict(type="linux", input_method=4, screencap_method=4,
                use_win32_vk_code=False, eis_socket_path="/owned", pw_node_id=25)
    info.update(change)
    with pytest.raises(ProbeError):
        validate_controller(info, SimpleNamespace(eis="/owned"))


def capturing(frame):
    guard = NativeGuard.__new__(NativeGuard)
    guard.check = lambda: pytest.fail("identity check (pw-dump) on every capture")
    job = SimpleNamespace(done=True, succeeded=True, get=lambda: frame)
    guard.controller = SimpleNamespace(post_screencap=lambda: job)
    return guard


@pytest.mark.parametrize("frame", [None, np.zeros((720, 1280, 3), dtype=np.uint8),
                                   np.ones((360, 640, 3), dtype=np.uint8)])
def test_unusable_capture_is_never_accepted(frame):
    with pytest.raises(ProbeError):
        capturing(frame).capture()


def test_capture_skips_the_identity_check():
    frame = np.indices((720, 1280))[0].astype(np.uint8)
    assert capturing(frame).capture() is frame


def test_keys_that_would_not_type_as_named_are_refused():
    d, _ = nested()
    assert key_codes(d, {"3": 4, "w": 17}) == {"3": THREE, "w": (25, 0x77)}
    # modifier, shifted, keypad, unmapped; each given the evdev code of the key it is on
    for name, evdev in [("Shift_L", 42), ("A", 30), ("KP_End", 79), ("Return", 28)]:
        with pytest.raises(ProbeError):
            key_codes(d, {name: evdev})


def test_a_key_moved_to_another_keycode_is_refused():
    """Native input sends the fixed evdev code, so the checks must look at that key."""
    d, _ = nested(keymap={**KEYMAP, 12: ["4", "dollar"], 13: ["3", "numbersign"]})
    with pytest.raises(ProbeError, match="not on its usual key"):
        key_codes(d, {"3": maa_guard.KEYS["3"]})


def test_a_clean_nested_keyboard_allows_the_key():
    d, w = nested()
    assert input_conflict(d, w.game.id, "3", *THREE) is None
    d.focus = w.child  # focus inside the game window still counts
    assert input_conflict(d, w.game.id, "3", *THREE) is None


@pytest.mark.parametrize("change, reason", [
    (lambda d, w: setattr(d, "focus", w.overlay), "focus"),
    (lambda d, w: setattr(w.root, "state", 1 << 2), "modifier"),  # Control held
    (lambda d, w: setattr(w.root, "state", 1 << 13), "layout"),
    (lambda d, w: d.keys_down.add(12), "already held"),
    (lambda d, w: d.keymap.update({12: ["4", "dollar"]}), "map changed"),
])
def test_conflicts_refuse_the_key(change, reason):
    d, w = nested()
    change(d, w)
    assert reason in input_conflict(d, w.game.id, "3", *THREE)


def test_a_lit_caps_lock_is_not_a_held_modifier():
    d, w = nested()
    w.root.state = 1 << 1  # Lock group, only Caps_Lock in it, not pressed
    assert input_conflict(d, w.game.id, "3", *THREE) is None


def pressing(d, w, result=True, *, key_up_works=True):
    """A guard on the fake nested display. Its key-ups lift the key there unless
    `key_up_works` is False; "fails" makes the native key-up job itself fail."""
    guard = NativeGuard.__new__(NativeGuard)
    guard.display, guard.window, guard.codes = d, w.game, {"3": THREE}
    guard.alive = lambda: None
    released = []

    def post_key_up(code):
        released.append(code)
        if key_up_works is True:
            d.keys_down.discard(code + 8)
        return SimpleNamespace(done=True, succeeded=key_up_works != "fails")
    guard.controller = SimpleNamespace(post_key_up=post_key_up)
    actions = []
    context = SimpleNamespace(run_action=lambda node, pipeline_override: actions.append(
        (node, pipeline_override)) or (SimpleNamespace(success=True) if result else None))
    return guard, context, actions, released


def frozen_clock(monkeypatch):
    clock = iter(range(1000))
    monkeypatch.setattr(maa_guard.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(maa_guard.time, "sleep", lambda s: None)


def test_a_press_runs_the_named_native_key_node():
    d, w = nested()
    guard, context, actions, released = pressing(d, w)
    guard.press(context, "3")
    assert actions == [("GBFR_Key_3", {})]
    guard.press(context, "3", duration=250)
    assert actions[1][1]["GBFR_Key_3"]["action"]["param"] == {"key": [4], "duration": 250}
    assert released == []


def test_a_conflict_sends_nothing():
    d, w = nested()
    d.focus = w.overlay
    guard, context, actions, _ = pressing(d, w)
    with pytest.raises(ProbeError, match="focus"):
        guard.press(context, "3")
    assert actions == []


def test_a_failed_native_press_releases_the_key():
    d, w = nested()
    guard, context, _, released = pressing(d, w, result=False)
    with pytest.raises(ProbeError, match="failed"):
        guard.press(context, "3")
    assert released == [4]
    assert not guard.release_unconfirmed  # the key reads up afterwards


@pytest.mark.parametrize("key_up_works", [False, "fails"])
def test_a_failed_press_whose_key_up_does_not_take_is_remembered(monkeypatch, key_up_works):
    d, w = nested()
    guard, context, _, released = pressing(d, w, result=False, key_up_works=key_up_works)
    context.run_action = lambda node, pipeline_override: d.keys_down.add(12)  # down, then failed
    frozen_clock(monkeypatch)
    with pytest.raises(ProbeError, match="press failed"):
        guard.press(context, "3")
    assert released == [4] and guard.release_unconfirmed == {"3"}


def held_after_press(d):
    return lambda node, pipeline_override: d.keys_down.add(12) or SimpleNamespace(success=True)


def test_a_key_still_down_after_the_press_gets_a_key_up(monkeypatch):
    """The press reported success, yet the key still reads down: it is let go like a
    failed press, and the session still stops, because input did not behave."""
    d, w = nested()
    guard, context, _, released = pressing(d, w)
    context.run_action = held_after_press(d)
    frozen_clock(monkeypatch)
    with pytest.raises(ProbeError, match="release"):
        guard.press(context, "3")
    assert released == [4] and 12 not in d.keys_down
    assert not guard.release_unconfirmed


def test_a_key_that_stays_down_after_its_key_up_is_remembered(monkeypatch):
    d, w = nested()
    guard, context, _, released = pressing(d, w, key_up_works=False)
    context.run_action = held_after_press(d)
    frozen_clock(monkeypatch)
    with pytest.raises(ProbeError, match="release"):
        guard.press(context, "3")
    assert released == [4] and guard.release_unconfirmed == {"3"}


@pytest.mark.parametrize("key, duration", [("F12", 100), ("3", 49), ("3", 501), ("3", True)])
def test_unsupported_keys_and_unbounded_holds_are_refused(key, duration):
    d, w = nested()
    guard, context, actions, _ = pressing(d, w)
    with pytest.raises(ProbeError):
        guard.press(context, key, duration)
    assert actions == []


def test_the_resource_carries_exactly_the_guards_key_nodes():
    pipeline = json.loads((ROOT / "assets/resource/pipeline/farming.json").read_text())
    nodes = {name: node for name, node in pipeline.items() if name.startswith("GBFR_Key_")}
    assert nodes == key_nodes()


def test_closing_a_display_whose_server_is_gone_does_not_raise():
    from Xlib.error import ConnectionClosedError

    class Dead:
        def close(self):
            raise ConnectionClosedError("server")

    guard = NativeGuard.__new__(NativeGuard)
    guard.display = Dead()
    guard.close()
    assert guard.display is None
    guard.close()


def watched(monkeypatch, check):
    monkeypatch.setattr(maa_guard, "check_target", check)
    guard = NativeGuard.__new__(NativeGuard)
    guard.target = SimpleNamespace()
    return guard


def test_lost_names_a_vanished_game_or_gamescope(monkeypatch):
    def vanished(target):
        raise ProbeError("process 4242 is gone or its identity cannot be read")
    assert watched(monkeypatch, vanished).lost() == "process 4242 is gone or its identity cannot be read"


def test_lost_checks_the_game_process_itself(monkeypatch):
    guard = watched(monkeypatch, lambda target: None)
    guard.game_pid, guard.game_start = 2 ** 30, "1"  # above any Linux pid_max
    assert "is gone" in guard.lost()


def test_lost_is_none_while_the_target_lives_or_cannot_be_judged(monkeypatch):
    assert watched(monkeypatch, lambda target: None).lost() is None
    def broken(target):
        raise OSError("unexpected")
    assert watched(monkeypatch, broken).lost() is None
