# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""GBFR 的页面判定树和配套的表：认哪几张模板、判出哪几页、每一页做什么。

pages.py 是引擎，只管按顺序试规则；这里是数据，描述的是这个游戏本身。这些表原来
写在 main.py 里，和 Tk、热键、Windows 截图在同一个模块。Linux 上 import 不了
main，test_pages 也就没法对着这棵真树测。挪到这里以后，数据和它的一致性检查都不再
依赖平台。

注释里提到的 _advance_unknown_page 和 _note_battle_transition 是 main.App 的方法，
也就是使用这些表的那一层。
"""

from enum import StrEnum, auto

import pages


class PAGE_NAME(StrEnum):
    """判定树给得出的页面，外加一个也没认出来时的 UNKNOWN。

    值由 auto() 生成，就是成员名的小写（"battle"、"reward_exit" ……），界面和日志
    里显示的正是它。StrEnum 的成员本身就是字符串，所以 %s 和 f-string 打出来的
    也是这个值。
    """

    BATTLE = auto()
    REWARD_AGAIN = auto()
    REWARD_EXIT = auto()
    SCORE = auto()
    PAUSE = auto()
    UNKNOWN = auto()


# 认出来的页面各自该做什么。#16：加一个页面从"改 if/elif 链"变成"加一行"。
#
# BATTLE 和 UNKNOWN 不在表里，因为它们不是"按个键推进"这类动作：
#   BATTLE   要按住 W + 中键，并且**不做**后续动作
#   UNKNOWN  走 _advance_unknown_page 的盲按上限逻辑
PAGE_ACTIONS = {
    PAGE_NAME.REWARD_EXIT: "switch_again",
    PAGE_NAME.REWARD_AGAIN: "tap_confirm",
    PAGE_NAME.SCORE: "tap_confirm",
    PAGE_NAME.PAUSE: "tap_confirm",
}

# 这是哪一页。#16 第二部分：判定优先级原来就是 if/elif 的书写顺序 —— 承重、
# 无声、调换两行就改行为。现在**顺序就是这个元组的顺序**，看得见也测得到。
#
# 结算页要再分一层：先看有没有"再来一次"，再看有没有"退出"，都没有就是纯结算页。
PAGE_RULES = (
    pages.Rule("flag_battle", PAGE_NAME.BATTLE),
    pages.Rule("flag_battleresult", children=(
        pages.Rule("flag_again", PAGE_NAME.REWARD_AGAIN),
        pages.Rule("flag_exit", PAGE_NAME.REWARD_EXIT),
    ), fallback=PAGE_NAME.SCORE),
    pages.Rule("flag_continue", PAGE_NAME.PAUSE),
)

# 要加载的模板文件，从上面这棵树里收集，顺序就是它们在树里出现的顺序。清单要是
# 另外手写一份，就多了一处得和树保持一致的地方；现在树是唯一的来源，加一条规则，
# 它用到的模板自动进清单。文件在不在 template/ 里，由 test_pages 对着目录检查。
TEMPLATE_FILES = [f"{name}.png" for name in dict.fromkeys(pages.templates_used(PAGE_RULES))]

# 结算页家族。战斗计数在进到其中任意一页时 +1，和原来 flag_battleresult 命中
# 就计数是同一条规则。
RESULT_PAGES = frozenset({
    PAGE_NAME.REWARD_AGAIN, PAGE_NAME.REWARD_EXIT, PAGE_NAME.SCORE,
})
