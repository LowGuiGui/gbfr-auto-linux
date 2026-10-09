# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Linux 会话不能跟着窗口替换自动重连，也不能在输入掉线后继续。"""

import pytest

from supervisor import Observation, stop_reason


@pytest.mark.parametrize("current", [None, 1234])
def test_the_same_live_window_and_ready_input_can_continue(current):
    assert stop_reason(Observation(1234, True, True), current) is None


@pytest.mark.parametrize("obs, reason", [
    (Observation(), "游戏窗口不在了"),
    (Observation(None, True, True), "游戏窗口不在了"),
    (Observation(1234, False, True), "游戏窗口不在了"),
    (Observation(5678, True, True), "游戏窗口换了一个，游戏多半重启过"),
    (Observation(1234, True, False), "输入用不了：键鼠通道不可用"),
])
def test_unusable_or_replaced_targets_stop(obs, reason):
    assert stop_reason(obs, current_window=1234) == reason
