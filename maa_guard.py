# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read-only Linux target/input checks around native Maa key actions.

The X11 connection observes nested-window focus and input state. It never injects
events; Maa's Linux controller owns the input transport and normal key release.
"""

import time

import gamescope
from maa_target import ProbeError, check_target, discover_target, read_node
from xtest_input import XTestInput, _codes, _is_down

KEYS = {"Return": 28, "Escape": 1, "Up": 103, "Down": 108, "Left": 105,
        "Right": 106, "w": 17, "a": 30, "f": 33, "r": 19, "3": 4,
        "q": 16, "e": 18}


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
            window = gamescope.find_game_window(self.display, "881020")
            if window is None:
                raise ProbeError("The GBFR window is unavailable")
            self.input = XTestInput(self.display, window, {k: k for k in KEYS}, live=False)
            self.check()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.display is not None:
            self.display.close()
            self.display = None

    def check(self):
        check_target(self.target)
        info = self.controller.info
        validate_controller(info, self.target)
        if info["pw_node_id"] != self.node:
            raise ProbeError("The controller changed capture sources during the session")
        if read_node(self.node, self.target) != self.serial:
            raise ProbeError("The capture source was replaced")

    def capture(self):
        self.check()
        frame = wait_native(self.controller.post_screencap()).get()
        self.check()
        if frame is None or frame.size == 0 or frame.shape[:2] != (720, 1280):
            raise ProbeError("The supported profile requires a usable 1280x720 capture")
        if float(frame.std()) < 1:
            raise ProbeError("The capture is blank")
        return frame

    def press(self, context, key, duration=100):
        if key not in KEYS or type(duration) is not int or not 50 <= duration <= 500:
            raise ProbeError("Unsupported key or unbounded input duration")
        self.check()
        code = self.input._keycode(key)
        if code - 8 != KEYS[key] or not self.input._clear_to_press_key(key, code, "Maa action"):
            raise ProbeError("Nested focus, held input or keyboard layout conflicts with automation")
        try:
            # Register ownership in Maa so a normal task stop also releases the key.
            result = self._key_action(context, "KeyDown", KEYS[key])
            if result is None or not result.success:
                raise ProbeError("Native key-down failed")
            deadline = time.monotonic() + duration / 1000
            while time.monotonic() < deadline and not context.tasker.stopping:
                state = self.input._read_state("bounded hold")
                if (state is None or not self.input._focus_on_game("bounded hold")
                        or self.input._modifier_held(state, "bounded hold")
                        or self.input._other_layout(state, "bounded hold")
                        or not self.input._still_configured(key, code, state["keymap"],
                                                            _codes(state["modifier_map"]))):
                    raise ProbeError("Input state changed during the bounded action")
                time.sleep(.02)
        finally:
            # Release only this action's key. Direct controller release also works
            # while Tasker is stopping and refuses additional pipeline actions.
            released = self._key_action(context, "KeyUp", KEYS[key])
            if released is None or not released.success:
                wait_native(self.controller.post_key_up(KEYS[key]))
        deadline = time.monotonic() + .5
        while True:
            state = self.input._read_state("native release")
            if state is None:
                raise ProbeError("Could not verify input release")
            if not _is_down(state["keys_down"], code):
                break
            if time.monotonic() >= deadline:
                raise ProbeError("Input release was not observed")
            time.sleep(.02)

    @staticmethod
    def _key_action(context, action, code):
        # Direct actions inherit Maa's 200 ms pre/post delays. Between down/up,
        # those delays would turn a menu tap into keyboard autorepeat.
        return context.run_action("GBFR_NativeKey", pipeline_override={
            "GBFR_NativeKey": {"pre_delay": 0, "post_delay": 0,
                               "action": {"type": action, "param": {
                                   "key": code, "auto_up": action == "KeyDown"}}}})
