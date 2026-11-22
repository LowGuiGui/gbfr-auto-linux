# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read-only Linux target/input checks around native Maa key actions.

Two levels of checking (owner decision R4, 2026-11-21):

- Identity (`NativeGuard.check`): the one GBFR Gamescope, its game process, and the
  PipeWire capture node. It runs a `pw-dump` subprocess, so the Agent calls it when a
  session starts and on each results screen, not per key.
- Input conflicts (`input_conflict`): one cheap read of the nested X server before
  each key: focus on the game window, no modifier held, first keyboard layout, the key
  still mapped as configured and not already held by someone else.

The X11 connection only observes. Maa's Linux controller owns the input transport;
keys are the native `LongPressKey` nodes `GBFR_Key_*` in the resource.
"""

import time

from Xlib import XK
from Xlib import error as xerror

import gamescope
from maa_target import (
    ProbeError, check_target, discover_target, process_stamp, read_node, select_game_window,
)

KEYS = {"Return": 28, "Escape": 1, "Up": 103, "Down": 108, "Left": 105,
        "Right": 106, "w": 17, "a": 30, "f": 33, "r": 19, "3": 4,
        "q": 16, "e": 18}
DEFAULT_HOLD_MS = 100
# Lit locks are not "held modifiers": Caps Lock only changes letters, Num Lock only keypad keys.
LOCK_KEYSYMS = {XK.XK_Caps_Lock, XK.XK_Num_Lock}


class FrameUnavailable(ProbeError):
    """A valid capture contains no recognizable scene, for example a fade."""


def validate_controller(info, target):
    if (info.get("type") != "linux" or info.get("screencap_method") != 4
            or info.get("input_method") != 4 or info.get("use_win32_vk_code") is not False
            or info.get("eis_socket_path") != target.eis
            or type(info.get("pw_node_id")) is not int or info["pw_node_id"] <= 0):
        raise ProbeError("Select GBFR's native Gamescope PipeWire/libei controller")


def wait_native(job, seconds=5):
    deadline = time.monotonic() + seconds
    while not job.done:
        if time.monotonic() >= deadline:
            raise ProbeError("Native controller operation timed out")
        time.sleep(.01)
    if not job.succeeded:
        raise ProbeError("Native controller operation failed")
    return job


# --- nested X keyboard state (read-only) --------------------------------------

def modifier_codes(modifier_map):
    return {code for codes in modifier_map for code in codes if code}


def is_down(keys_down, keycode):
    """QueryKeymap: 32 bytes, byte N holds keycodes 8N..8N+7, low bit first."""
    return bool(keys_down[keycode // 8] & (1 << (keycode % 8)))


def num_lock_changes(keysyms):
    """With Num Lock lit, a key whose second level is a keypad symbol types that instead."""
    if len(keysyms) < 2 or keysyms[1] == keysyms[0]:
        return False
    return 0xFF80 <= keysyms[1] <= 0xFFBD or 0x11000000 <= keysyms[1] <= 0x1100FFFF


def layout_group(mask):
    """The XKB group (keyboard layout) the server keeps in bits 13-14 of the state mask."""
    return (mask >> 13) & 3


def key_codes(display, names):
    """Map each key name to (keycode, keysym), refusing keys that would not type as named."""
    modifiers = modifier_codes(display.get_modifier_mapping())
    result = {}
    for name in names:
        keysym = XK.string_to_keysym(name)
        keycode = display.keysym_to_keycode(keysym) if keysym else 0
        if not keycode:
            raise ProbeError(f"The nested keyboard map has no {name!r}")
        if keycode in modifiers:
            raise ProbeError(f"{name!r} is a modifier on the nested display")
        if display.keycode_to_keysym(keycode, 0) != keysym:
            raise ProbeError(f"{name!r} needs a modifier on the nested display")
        if num_lock_changes((keysym, display.keycode_to_keysym(keycode, 1))):
            raise ProbeError(f"{name!r} is a keypad key that Num Lock would change")
        result[name] = (keycode, keysym)
    return result


def read_keyboard(display):
    info = display.display.info
    rows = display.get_keyboard_mapping(info.min_keycode, info.max_keycode - info.min_keycode + 1)
    return {"keymap": dict(enumerate(rows, start=info.min_keycode)),
            "modifier_map": display.get_modifier_mapping(),
            "mask": display.screen().root.query_pointer().mask,
            "keys_down": display.query_keymap()}


def modifiers_held(state):
    """State bits of held modifiers. A group of only unpressed lock keys does not count."""
    locks = {code for code, keysyms in state["keymap"].items()
             if keysyms and keysyms[0] in LOCK_KEYSYMS}
    held = 0
    for index, codes in enumerate(state["modifier_map"]):
        bit = 1 << index
        if not state["mask"] & bit:
            continue
        members = [code for code in codes if code]
        if (members and all(code in locks for code in members)
                and not any(is_down(state["keys_down"], code) for code in members)):
            continue
        held |= bit
    return held


def focus_inside(display, window_id):
    try:
        return gamescope.within_window(display.get_input_focus().focus, window_id, strict=True)
    except xerror.XError:
        return False  # the focused window vanished between the two queries


def input_conflict(display, window_id, name, keycode, keysym):
    """Why a key must not be sent now, or None when it may be."""
    if not focus_inside(display, window_id):
        return "Nested input focus is not on the game window"
    state = read_keyboard(display)
    keysyms = state["keymap"].get(keycode)
    if (not keysyms or keysyms[0] != keysym or keycode in modifier_codes(state["modifier_map"])
            or num_lock_changes(keysyms)):
        return f"The nested keyboard map changed: keycode {keycode} is no longer {name!r}"
    if modifiers_held(state):
        return "A modifier key is held on the nested display"
    if layout_group(state["mask"]):
        return "The nested keyboard is not on its first layout"
    if is_down(state["keys_down"], keycode):
        return f"{name!r} is already held elsewhere"
    return None


class NativeGuard:
    def __init__(self, controller):
        self.display = None
        self.controller = controller
        self.target = discover_target("881020")
        validate_controller(controller.info, self.target)
        self.node = controller.info["pw_node_id"]
        self.serial = read_node(self.node, self.target)
        try:
            values = gamescope.instance_values(gamescope.find_game_processes("881020"))
            if values["display"] != self.target.display:
                raise ProbeError("The nested display changed during connection")
            self.display = gamescope.connect_x(self.target.display, values["xauth"])
            self.window, self.game_pid, self.game_start = select_game_window(self.display, self.target)
            self.codes = key_codes(self.display, KEYS)
            self.check()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.display is not None:
            self.display.close()
            self.display = None

    def alive(self):
        """Cheap per-key check: the game process that owned the window is still the same."""
        if process_stamp(self.game_pid) != self.game_start:
            raise ProbeError("The game process was replaced")

    def check(self):
        check_target(self.target)
        if hasattr(self, "game_pid"):
            self.alive()
        info = self.controller.info
        validate_controller(info, self.target)
        if info["pw_node_id"] != self.node:
            raise ProbeError("The controller changed capture sources during the session")
        if read_node(self.node, self.target) != self.serial:
            raise ProbeError("The capture source was replaced")

    def capture(self):
        frame = wait_native(self.controller.post_screencap()).get()
        if frame is None or frame.size == 0 or frame.shape[:2] != (720, 1280):
            raise ProbeError("The supported profile requires a usable 1280x720 capture")
        if float(frame.std()) < 1:
            raise FrameUnavailable("The capture is blank")
        return frame

    def press(self, context, key, duration=DEFAULT_HOLD_MS):
        if key not in KEYS or type(duration) is not int or not 50 <= duration <= 500:
            raise ProbeError("Unsupported key or unbounded input duration")
        self.alive()
        keycode, keysym = self.codes[key]
        reason = input_conflict(self.display, self.window.id, key, keycode, keysym)
        if reason:
            raise ProbeError(reason)
        node = "GBFR_Key_" + key
        override = None
        if duration != DEFAULT_HOLD_MS:
            override = {node: {"action": {"type": "LongPressKey",
                                          "param": {"key": [KEYS[key]], "duration": duration}}}}
        result = context.run_action(node, pipeline_override=override or {})
        if result is None or not result.success:
            # A failed or interrupted press must not leave the key down.
            wait_native(self.controller.post_key_up(KEYS[key]))
            raise ProbeError("Native key press failed")
        deadline = time.monotonic() + .5
        while is_down(self.display.query_keymap(), keycode):
            if time.monotonic() >= deadline:
                raise ProbeError("Input release was not observed")
            time.sleep(.02)


def key_nodes():
    """The `GBFR_Key_*` resource nodes: one native LongPressKey per supported key."""
    return {"GBFR_Key_" + name: {"pre_delay": 0, "post_delay": 0,
                                 "action": {"type": "LongPressKey",
                                            "param": {"key": [code], "duration": DEFAULT_HOLD_MS}}}
            for name, code in KEYS.items()}
