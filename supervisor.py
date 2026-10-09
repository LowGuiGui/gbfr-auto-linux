# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""当前 Linux 窗口与输入的停机条件；不切换后端，也不重连新目标。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    window_id: int | None = None
    window_valid: bool = False
    input_ready: bool = False


def stop_reason(obs, current_window=None):
    """返回停下的理由，或 None。调用方负责在退出时松开输入。"""
    if not obs.window_valid or obs.window_id is None:
        return "游戏窗口不在了"
    if current_window is not None and obs.window_id != current_window:
        return "游戏窗口换了一个，游戏多半重启过"
    if not obs.input_ready:
        return "输入用不了：键鼠通道不可用"
    return None
