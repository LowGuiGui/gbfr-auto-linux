# -*- coding: utf-8 -*-
"""页面判定树（#16 第二部分）。

第一部分把"这一页做什么"变成了表（main.PAGE_ACTIONS）。剩下的是"这是哪一页"，
而它有两个比 if/elif 长得难看更实际的问题：

1. **优先级是语句顺序。** flag_battle 赢过 flag_battleresult，仅仅因为它写在
   前面。承重、无声、没人写下来 —— 调换两行就改变行为，而没有任何测试会红。
   这里把顺序变成**数据**：RULES 的次序就是优先级，看得见，也测得到。

2. **判定顺手改状态。** 原来的 flag_battleresult 分支里会 `_loop_count += 1`、
   翻 `_has_battle`、往界面写一行日志。于是"问一下现在是哪一页"这个动作是有
   副作用的，既没法重复调用，也没法在 Tk 之外测。

   resolve() 是纯函数：只问 matches(name)，只返回页面。战斗计数留给调用方，见
   main.App._note_battle_transition。

分开之后，整棵判定树在 Linux 上可以完整测试，不需要 Tk、不需要游戏、不需要
任何一张模板图 —— 只要一个假的 matches。
"""


class Rule:
    """一条判定：模板命中就走这一支。

    template  要命中的模板名（传给 matches 的那个）
    page      命中且没有子规则时的页面
    children  子规则，按顺序试；用于"结算页里再分是哪一种"
    fallback  有子规则但一个都没命中时的页面
    """

    __slots__ = ("template", "page", "children", "fallback")

    def __init__(self, template, page=None, children=(), fallback=None):
        if not children and page is None:
            raise ValueError(f"规则 {template!r} 既没有 page 也没有 children")
        if children and fallback is None:
            raise ValueError(f"规则 {template!r} 有子规则却没有 fallback —— "
                             "子规则全不命中时就没有页面可返回了")
        self.template = template
        self.page = page
        self.children = tuple(children)
        self.fallback = fallback

    def __repr__(self):
        return f"Rule({self.template!r} -> {self.page or self.fallback})"


def resolve(rules, matches, unknown):
    """按顺序试每条规则，返回页面。纯函数。

    matches(template_name) -> bool。只在需要时调用，所以顺序也决定了会做多少次
    模板匹配 —— 匹配是这个循环里最贵的一步。
    """
    for rule in rules:
        if not matches(rule.template):
            continue
        for child in rule.children:
            if matches(child.template):
                return child.page
        return rule.fallback if rule.children else rule.page
    return unknown


def templates_used(rules):
    """这棵树引用到的所有模板名。

    用来对着 TEMPLATE_FILES 校验：规则里写了一个不存在的模板，表现是那一支
    永远不命中 —— 安静地永远走不到，正是最难发现的那种坏法。
    """
    names = []
    for rule in rules:
        names.append(rule.template)
        names.extend(child.template for child in rule.children)
    return names


def pages_reachable(rules, unknown):
    """这棵树可能返回的所有页面。用来对着 PAGE_ACTIONS 校验覆盖。"""
    out = {unknown}
    for rule in rules:
        if rule.children:
            out.add(rule.fallback)
            out.update(child.page for child in rule.children)
        else:
            out.add(rule.page)
    return out
