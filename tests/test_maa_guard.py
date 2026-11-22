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
from test_xtest_input import nested

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
    assert key_codes(d, ["3", "w"]) == {"3": THREE, "w": (25, 0x77)}
    for name in ["Shift_L", "A", "KP_End", "Return"]:  # modifier, shifted, keypad, unmapped
        with pytest.raises(ProbeError):
            key_codes(d, [name])


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


def pressing(d, w, result=True):
    guard = NativeGuard.__new__(NativeGuard)
    guard.display, guard.window, guard.codes = d, w.game, {"3": THREE}
    guard.alive = lambda: None
    released = []
    guard.controller = SimpleNamespace(post_key_up=lambda code: released.append(code) or
                                       SimpleNamespace(done=True, succeeded=True))
    actions = []
    context = SimpleNamespace(run_action=lambda node, pipeline_override: actions.append(
        (node, pipeline_override)) or (SimpleNamespace(success=True) if result else None))
    return guard, context, actions, released


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


def test_a_key_still_down_after_the_press_is_reported(monkeypatch):
    d, w = nested()
    guard, context, _, _ = pressing(d, w)
    context.run_action = lambda node, pipeline_override: d.keys_down.add(12) or SimpleNamespace(success=True)
    clock = iter(range(100))
    monkeypatch.setattr(maa_guard.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(maa_guard.time, "sleep", lambda s: None)
    with pytest.raises(ProbeError, match="release"):
        guard.press(context, "3")


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
