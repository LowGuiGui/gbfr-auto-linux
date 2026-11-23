# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Chinese explanations of why a session stopped, with what to do next.

The session keeps its technical reason (English, stable for tests and logs); the
control panel and MXU's log show `describe(status)`. Each entry says what happened and
what the player should do; an unknown reason is shown as it is. A stop the player asked
for is not an error and has its own message.
"""

import re

# (pattern, message). The first matching pattern wins; groups fill {0}, {1}, ...
MESSAGES = (
    (r"closed during the session",
     "游戏或它的 Gamescope 在运行中关闭了，已停止，没有再按键。已计数的次数有效。"
     "请通过 Steam 重新启动游戏，等画面稳定后再开始。"),
    (r"Start a new session from the quest", "请先在任务柜台打开要刷的任务（详情页或准备画面），再开始。"),
    (r"did not reach a supported transition",
     "画面在限定时间内没有变成预期的样子，已停止。请看一眼游戏：打开要刷的任务"
     "（详情页或准备画面），再重新开始。"),
    (r"Select GBFR's native Gamescope",
     "连接的不是 GBFR 的 Gamescope。请确认游戏是通过 Steam 启动的；用 MXU 时，请在设备里选择“GBFR Gamescope”。"),
    (r"expected one Gamescope for app \d+; found 0$",
     "没有找到正在运行的游戏，没有开始。请先通过 Steam 启动游戏，等画面稳定后再开始。"),
    (r"expected one Gamescope for app \d+; found (\d+)$",
     "找到了 {0} 个运行着 GBFR 的 Gamescope，不知道该控制哪一个，没有开始。请只保留一个游戏在运行。"),
    (r"same host runtime directory|PipeWire environments differ|no host Gamescope endpoint|"
     r"selected Gamescope socket is unavailable|did not find exactly the selected Gamescope|"
     r"input endpoint does not belong|reports no capture node|reports no input socket|"
     r"PipeWire node is absent or ambiguous|PipeWire source is not owned|PipeWire source has no stable",
     "找不到游戏的截图或输入通道，没有开始。Gamescope 可能刚重启，或者游戏不是通过 Steam 启动的；"
     "请等游戏画面稳定后再开始。"),
    (r"Expected one visible GBFR game window",
     "没有找到唯一的游戏窗口（游戏可能还在加载），没有开始。请等游戏画面稳定后再开始。"),
    (r"Gamescope was replaced|target process was replaced|target ancestry changed|"
     r"target socket disappeared|is gone or its identity cannot be read|not the owned test process",
     "游戏或它的 Gamescope 重启过或者关闭了，已停止。请等游戏画面稳定后再开始。"),
    (r"XDG_RUNTIME_DIR is not set", "桌面会话的运行目录（XDG_RUNTIME_DIR）没有设置，没有开始。请在桌面会话里运行。"),
    (r"interface\.json", "任务定义文件 interface.json 缺失或者有运行器不支持的写法，没有开始。请重新构建版本目录。"),
    (r"Cannot bind the resource and the controller",
     "资源和游戏连接没能绑在一起，没有开始。请重新开始；还不行就看一下日志。"),
    (r"The session did not stop within (\d+) s",
     "请求停止后 {0} 秒内软件没有停下来，已结束运行并尝试松开按键。请看一眼游戏里有没有键还按着。"),
    (r"Another session is already running|另一个循环正在跑",
     "已经有一个刷取在运行（可能是 MXU 版本目录里的那个），没有开始。先停掉它，再开始。"),
    (r"The OCR model is incomplete|Native resource loading failed|Native OCR model loading failed",
     "资源或 OCR 模型加载失败，没有开始。请重新构建版本目录。"),
    (r"Stopped before the session started", "已按要求停止，还没有开始刷，没有按任何键。"),
    (r"requires a usable 1280x720 capture",
     "截图不可用或尺寸不对：需要 16:9 的游戏画面。请检查游戏的分辨率设置。"),
    (r"Repeat Quest could not be turned on",
     "按了两次 3，“再次挑战”仍然没有打开，已停止。游戏会照常回到城镇。"),
    (r"Repeat Quest could not be turned off after the quest failed",
     "任务失败了，按了两次 3，“再次挑战”仍然开着，已停止。游戏可能会重试这个任务；"
     "请在结算页按 3 关掉。"),
    (r"Repeat Quest could not be turned off",
     "按了两次 3，“再次挑战”仍然开着，已停止。游戏可能继续刷，最多到第 10 次；"
     "想马上停，请在结算的奖励页按 3 关掉。"),
    (r"Repeat Quest was off, yet the game started another run",
     "游戏开始了新一轮，但软件没有确认“再次挑战”是开着的，已停止。这一轮不计数。"),
    (r"The quest failed",
     "任务失败，这一轮不计数，已停止。“再次挑战”已确认关着，游戏会自己回到城镇。"
     "已计数的次数有效。请确认队伍能稳定通关。"),
    (r"failure page's Repeat Quest toggle could not be read",
     "任务失败了，但读不到结算页上“再次挑战”的状态，已停止。请在结算页确认它是关着的。"),
    (r"The game returned to town before the session finished",
     "会话还没结束，游戏就回到了城镇（可能是第 10 次的确认没有选上“是”），已停止。"
     "已计数的次数有效。"),
    (r"cursor could not be moved to 是|是 on the tenth-run dialog was not accepted",
     "第 10 次的确认弹窗里没能选上“是”，已停止。游戏会回到城镇，已计数的次数有效。"),
    (r"The results screen could not be confirmed for counting",
     "结算画面几次都没能确认，这一轮没有计数，已停止。请看一眼游戏当前画面；"
     "“再次挑战”如果开着，游戏会自己继续刷。已计数的次数有效。"),
    (r"The battle exceeded its (\d+) s limit",
     "战斗超过了 {0} 秒上限，已停止。请确认完全辅助模式已开、当前队伍能通关。"),
    (r"Automation was stopped", "已按要求停止。"),
    (r"Automation ended before a verified town finish",
     "任务在确认回到城镇之前意外结束了。已计数的次数有效。请看一眼游戏当前画面。"),
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
     "画面上的战斗不是这次会话开始的，已停止，没有计数。请打开要刷的任务（详情页或准备画面）重新开始。"),
    (r"cannot start in the current session state|requires a battle observed|"
     r"victory screen appeared without|may only be turned on|can only be turned off|"
     r"may only be answered|did not reach its settlement boundary|outside a results screen",
     "软件记录的进度和画面对不上，已停止，没有多计数。请看一眼游戏当前画面，"
     "打开要刷的任务（详情页或准备画面）重新开始。"),
    (r"successful-run limit",
     "次数设置无效：请填 1 到 999 的整数，或打开“一直刷”。"),
    (r"nested keyboard map has no|is a modifier on the nested|needs a modifier on the nested|keypad key|"
     r"not on its usual key",
     "游戏里的键盘映射不支持需要的按键（可能切换过键盘布局），没有开始。"),
    (r"nested display changed", "游戏的显示在连接时变了（游戏可能刚重启），请等游戏稳定后再开始。"),
    (r"Unsupported key or unbounded input duration", "软件内部错误（不支持的按键），已停止。请报告这个问题。"),
    (r"Could not stop before starting", "出发前停止没有成功，已停止。请看一眼游戏当前画面。"),
    (r"Native screenshot",
     "截图没有成功，已停止，没有再按键。请看一眼游戏是否还在正常运行，再重新开始。"),
    (r"Native key press failed|Input release was not observed|Native key release",
     "按键没有正常完成，已停止。软件已尝试松开按键；请确认游戏里没有键被按住。"),
    (r"^Native \w[\w ]* (failed|timed out)$",
     "软件和游戏之间的底层操作没有完成，已停止，没有再按键。请看一眼游戏是否还在正常运行，再重新开始。"),
)


def stopped(completed, left_on):
    """The player stopped the automation; the game itself does not pause."""
    message = f"已按要求停止，已计数的 {completed} 次有效。游戏没有暂停。"
    if left_on is None:
        return message + ("“再次挑战”刚切换过，还没读到结果，可能开着。开着的话游戏会自己继续刷，"
                          "最多到第 10 次弹窗后回城；想马上停，请在结算的奖励页看一眼，开着就按 3 关掉。")
    if left_on:
        return message + ("“再次挑战”还开着，游戏会自己继续刷，最多到第 10 次弹窗后回城；"
                          "想马上停，请在结算的奖励页按 3 关掉。")
    return message + "“再次挑战”没有开着，游戏不会自己开始下一轮。"


def describe(status):
    """The message for a session snapshot: its error, its stop, or None while it runs."""
    if status.get("error"):
        return explain(status["error"])
    if status.get("phase") == "stopped":
        return stopped(status.get("completed", 0), status.get("left_on", False))
    return None


def explain(reason):
    if not reason:
        return None
    for pattern, message in MESSAGES:
        match = re.search(pattern, reason)
        if match:
            return message.format(*match.groups())
    return "已停止：" + reason
