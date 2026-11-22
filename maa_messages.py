# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Chinese explanations of why a session stopped, with what to do next.

The session keeps its technical reason (English, stable for tests and logs); the
control panel shows `explain(reason)`. Each entry says what happened and what the
player should do; an unknown reason is shown as it is.
"""

import re

# (pattern, message). The first matching pattern wins; groups fill {0}, {1}, ...
MESSAGES = (
    (r"Start a new session from town", "请从城镇或这个任务的准备画面开始。"),
    (r"did not reach a supported transition",
     "画面在限定时间内没有变成预期的样子，已停止。请看一眼游戏：回到城镇或该任务的准备画面，"
     "再重新开始。"),
    (r"Select GBFR's native Gamescope",
     "MXU 连接的不是 GBFR 的 Gamescope。请在设备里选择“GBFR Gamescope”，并确认游戏是通过 Steam 启动的。"),
    (r"requires a usable 1280x720 capture",
     "截图不可用或尺寸不对：需要 16:9 的游戏画面。请检查游戏的分辨率设置。"),
    (r"Repeat Quest could not be turned on",
     "按了两次 3，“再次挑战”仍然没有打开，已停止。游戏会照常回到城镇。"),
    (r"Repeat Quest could not be turned off",
     "按了两次 3，“再次挑战”仍然开着，已停止。游戏可能继续刷，最多到第 10 次；"
     "想马上停，请在结算的奖励页按 3 关掉。"),
    (r"Repeat Quest was off, yet the game started another run",
     "游戏开始了新一轮，但软件没有确认“再次挑战”是开着的，已停止。这一轮不计数。"),
    (r"The quest failed", "任务失败，已停止，没有开始下一轮。请确认队伍能稳定通关。"),
    (r"The battle exceeded its (\d+) s limit",
     "战斗超过了 {0} 秒上限，已停止。请确认完全辅助模式已开、当前队伍能通关。"),
    (r"Automation was stopped", "已按要求停止。"),
    (r"Automation ended before a verified town finish",
     "任务在确认回到城镇之前结束了（例如被立即停止）。已计数的次数有效。"),
    (r"Nested input focus is not on the game window",
     "游戏画面里有别的窗口或覆盖层，为安全没有按键，已停止。关掉它后重新开始。"),
    (r"A modifier key is held",
     "检测到 Ctrl、Shift、Alt 之类的键按着，为避免变成组合键没有按键，已停止。"),
    (r"not on its first layout", "游戏里的键盘布局被切换过，已停止。请切回第一个布局。"),
    (r"keyboard map changed|is already held elsewhere",
     "键盘状态和开始时不一样（键位变了或键被按住），已停止。"),
    (r"The game process was replaced", "游戏进程变了（游戏可能重启过），已停止。"),
    (r"capture source", "截图来源变了（游戏或 Gamescope 可能重启过），已停止。"),
    (r"Screen transition budget exhausted: (\S+)",
     "同一个画面重复处理太多次（{0}），已停止。请看一眼游戏当前画面。"),
    (r"The recognized screen changed before its action",
     "准备按键时画面变了，为安全没有按键，已停止。"),
    (r"unrelated battle",
     "画面上的战斗不是这次会话开始的，已停止，没有计数。请从城镇或准备画面重新开始。"),
    (r"cannot start in the current session state|requires a battle observed|"
     r"victory screen appeared without|may only be turned on|can only be turned off|"
     r"cannot restart from town|did not reach its settlement boundary|outside a results screen",
     "软件记录的进度和画面对不上，已停止，没有多计数。请看一眼游戏当前画面，"
     "从城镇或准备画面重新开始。"),
    (r"Choose a quest|successful-run limit",
     "次数设置无效：请填 1 到 999 的整数，或打开“一直刷”。"),
    (r"nested keyboard map has no|is a modifier on the nested|needs a modifier on the nested|keypad key",
     "游戏里的键盘映射不支持需要的按键（可能切换过键盘布局），没有开始。"),
    (r"nested display changed", "游戏的显示在连接时变了（游戏可能刚重启），请等游戏稳定后再开始。"),
    (r"Unsupported key or unbounded input duration", "软件内部错误（不支持的按键），已停止。请报告这个问题。"),
    (r"Could not stop before starting", "出发前停止没有成功，已停止。请看一眼游戏当前画面。"),
    (r"Native key press failed|Input release was not observed|Native controller operation",
     "按键没有正常完成，已停止。软件已尝试松开按键；请确认游戏里没有键被按住。"),
)


def explain(reason):
    if not reason:
        return None
    for pattern, message in MESSAGES:
        match = re.search(pattern, reason)
        if match:
            return message.format(*match.groups())
    return "已停止：" + reason
