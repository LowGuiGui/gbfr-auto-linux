# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

from types import SimpleNamespace

import numpy as np
import pytest

from maa_guard import NativeGuard, validate_controller
from maa_target import ProbeError


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


@pytest.mark.parametrize("frame", [None, np.zeros((720, 1280, 3), dtype=np.uint8),
                                   np.ones((360, 640, 3), dtype=np.uint8)])
def test_unusable_capture_is_never_accepted(frame):
    guard = NativeGuard.__new__(NativeGuard)
    guard.check = lambda: None
    job = SimpleNamespace(done=True, succeeded=True, get=lambda: frame)
    guard.controller = SimpleNamespace(post_screencap=lambda: job)
    with pytest.raises(ProbeError):
        guard.capture()


def test_capture_identity_rechecked_after_native_completion():
    guard = NativeGuard.__new__(NativeGuard)
    calls = []
    def check():
        calls.append(True)
        if len(calls) == 2:
            raise ProbeError("source replaced during capture")
    guard.check = check
    job = SimpleNamespace(done=True, succeeded=True,
                          get=lambda: np.indices((720, 1280))[0].astype(np.uint8))
    guard.controller = SimpleNamespace(post_screencap=lambda: job)
    with pytest.raises(ProbeError, match="replaced"):
        guard.capture()
