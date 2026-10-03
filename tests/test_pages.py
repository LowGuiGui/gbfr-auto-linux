# -*- coding: utf-8 -*-
"""页面判定树（#16 第二部分）。

这棵树以前**一条测试都没有** —— `_get_current_page_name` 在每个测试里都是桩，
所以承重的优先级顺序和藏在判定里的副作用都没人守着。调换两行就改行为，而全绿。

现在判定是纯函数，整棵树可以在 Linux 上完整测：不需要 Tk、不需要游戏、不需要
任何一张模板图。
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pages  # noqa: E402


def matcher(*hits):
    """一个假的 matches：只有列出来的模板算命中，并记录问过哪些。"""
    seen = []

    def matches(name):
        seen.append(name)
        return name in hits

    matches.seen = seen
    return matches


class TestResolve:
    RULES = (
        pages.Rule("a", "PAGE_A"),
        pages.Rule("b", children=(
            pages.Rule("b1", "PAGE_B1"),
            pages.Rule("b2", "PAGE_B2"),
        ), fallback="PAGE_B"),
        pages.Rule("c", "PAGE_C"),
    )

    def test_a_plain_rule_returns_its_page(self):
        assert pages.resolve(self.RULES, matcher("a"), "UNKNOWN") == "PAGE_A"

    def test_nothing_matching_is_unknown(self):
        assert pages.resolve(self.RULES, matcher(), "UNKNOWN") == "UNKNOWN"

    def test_children_are_tried_in_order(self):
        assert pages.resolve(self.RULES, matcher("b", "b2"), "UNKNOWN") == "PAGE_B2"

    def test_the_first_matching_child_wins(self):
        assert pages.resolve(self.RULES, matcher("b", "b1", "b2"),
                             "UNKNOWN") == "PAGE_B1"

    def test_no_child_matching_falls_back(self):
        assert pages.resolve(self.RULES, matcher("b"), "UNKNOWN") == "PAGE_B"

    def test_a_child_only_counts_under_its_parent(self):
        """b1 命中但 b 没命中，就轮不到 b1 —— 子规则不是顶层规则。"""
        assert pages.resolve(self.RULES, matcher("b1"), "UNKNOWN") == "UNKNOWN"


class TestOrderIsPriorityAndItIsData:
    """原来这条规则是"写在前面的赢"，藏在 if/elif 里，没有任何测试。"""

    def test_the_earlier_rule_wins(self):
        rules = (pages.Rule("a", "FIRST"), pages.Rule("b", "SECOND"))
        assert pages.resolve(rules, matcher("a", "b"), "U") == "FIRST"

    def test_reversing_the_table_reverses_the_outcome(self):
        """顺序是承重的。这条测试的意义就是把这件事变成显式的。"""
        forward = (pages.Rule("a", "FIRST"), pages.Rule("b", "SECOND"))
        reverse = (pages.Rule("b", "SECOND"), pages.Rule("a", "FIRST"))
        both = matcher("a", "b")
        assert pages.resolve(forward, both, "U") == "FIRST"
        assert pages.resolve(reverse, matcher("a", "b"), "U") == "SECOND"


class TestResolveIsPure:
    def test_it_can_be_called_twice_with_the_same_answer(self):
        """原来的实现第二次调用会再加一次战斗计数。"""
        rules = (pages.Rule("a", "PAGE_A"),)
        m = matcher("a")
        assert pages.resolve(rules, m, "U") == pages.resolve(rules, m, "U")

    def test_it_stops_asking_once_a_rule_matches(self):
        """模板匹配是这个循环里最贵的一步，问一次少一次。"""
        rules = (pages.Rule("a", "A"), pages.Rule("b", "B"))
        m = matcher("a")
        pages.resolve(rules, m, "U")
        assert "b" not in m.seen


class TestRuleValidation:
    """配错规则要当场炸，不要变成一支永远走不到的分支。"""

    def test_a_rule_with_neither_page_nor_children_is_refused(self):
        with pytest.raises(ValueError, match="page"):
            pages.Rule("x")

    def test_children_without_a_fallback_are_refused(self):
        with pytest.raises(ValueError, match="fallback"):
            pages.Rule("x", children=(pages.Rule("y", "Y"),))


class TestTheRealTree:
    """main.py 里那棵真树，以及它和别的表对不对得上。"""

    def _main(self):
        import main
        return main

    def test_battle_beats_the_result_page(self):
        """原来这条只靠 flag_battle 写在前面成立。"""
        m = self._main()
        assert pages.resolve(m.PAGE_RULES, matcher("flag_battle", "flag_battleresult"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.BATTLE

    def test_result_with_again(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES,
                             matcher("flag_battleresult", "flag_again"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.REWARD_AGAIN

    def test_result_with_exit(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES,
                             matcher("flag_battleresult", "flag_exit"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.REWARD_EXIT

    def test_bare_result_is_the_score_page(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES, matcher("flag_battleresult"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.SCORE

    def test_again_beats_exit(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES,
                             matcher("flag_battleresult", "flag_again", "flag_exit"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.REWARD_AGAIN

    def test_pause(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES, matcher("flag_continue"),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.PAUSE

    def test_nothing_is_unknown(self):
        m = self._main()
        assert pages.resolve(m.PAGE_RULES, matcher(),
                             m.PAGE_NAME.UNKNOWN) == m.PAGE_NAME.UNKNOWN

    def test_every_template_the_tree_uses_is_shipped(self):
        """规则里写一个不存在的模板，表现是那一支永远不命中 —— 安静地永远
        走不到，最难发现的那种坏法。"""
        m = self._main()
        shipped = {f.replace(".png", "") for f in m.TEMPLATE_FILES}
        for name in pages.templates_used(m.PAGE_RULES):
            assert name in shipped, f"{name} 不在 TEMPLATE_FILES 里"

    def test_every_reachable_page_has_an_action_or_is_deliberately_special(self):
        m = self._main()
        special = {m.PAGE_NAME.BATTLE, m.PAGE_NAME.UNKNOWN}
        for page in pages.pages_reachable(m.PAGE_RULES, m.PAGE_NAME.UNKNOWN):
            assert page in m.PAGE_ACTIONS or page in special, \
                f"{page} 判定得出来，却没人知道该拿它做什么"

    def test_result_pages_matches_the_tree(self):
        """RESULT_PAGES 决定战斗计数。它和树对不上，计数就会漏或者重。"""
        m = self._main()
        from_tree = {r.fallback for r in m.PAGE_RULES if r.children}
        from_tree |= {c.page for r in m.PAGE_RULES for c in r.children}
        assert from_tree == set(m.RESULT_PAGES)


class TestBattleCounting:
    """战斗计数原来藏在判定分支里，所以"问一下现在是哪一页"是有副作用的：
    问两次就多记一次战斗。现在它是独立的一步，可以单独测。
    """

    class Counter:
        """借用真实的 _note_battle_transition。"""

        import main as _m
        _note_battle_transition = _m.App._note_battle_transition

        def __init__(self):
            self._has_battle = False
            self._loop_count = 0
            self.ui = []

        def log(self, msg):
            self.ui.append(msg)

    def _run(self, *page_names):
        import main
        c = self.Counter()
        for name in page_names:
            c._note_battle_transition(getattr(main.PAGE_NAME, name))
        return c

    def test_a_full_cycle_counts_one_battle(self):
        c = self._run("BATTLE", "REWARD_EXIT")
        assert c._loop_count == 1

    def test_the_result_page_alone_counts_nothing(self):
        """没打过就到结算页 —— 启动时正好停在那儿，不该凭空记一次。"""
        assert self._run("REWARD_EXIT")._loop_count == 0

    def test_lingering_on_the_result_page_counts_once(self):
        """结算页会连续出现好几帧。每帧记一次的话计数会暴涨。"""
        c = self._run("BATTLE", "REWARD_EXIT", "REWARD_EXIT", "REWARD_EXIT")
        assert c._loop_count == 1

    def test_every_result_variant_closes_the_battle(self):
        for variant in ("REWARD_AGAIN", "REWARD_EXIT", "SCORE"):
            assert self._run("BATTLE", variant)._loop_count == 1, variant

    def test_two_cycles_count_two(self):
        c = self._run("BATTLE", "SCORE", "BATTLE", "SCORE")
        assert c._loop_count == 2

    def test_unknown_frames_do_not_break_the_pairing(self):
        """战斗和结算之间夹几帧认不出来的画面，是常态。"""
        c = self._run("BATTLE", "UNKNOWN", "UNKNOWN", "REWARD_EXIT")
        assert c._loop_count == 1

    def test_a_pause_in_the_middle_does_not_lose_the_battle(self):
        c = self._run("BATTLE", "PAUSE", "BATTLE", "REWARD_EXIT")
        assert c._loop_count == 1

    def test_it_tells_the_user(self):
        c = self._run("BATTLE", "REWARD_EXIT")
        assert any("完成第 1 次战斗" in m for m in c.ui)

    def test_detection_no_longer_counts_by_itself(self):
        """守住这次拆分：判定里再出现计数，就又回到了"问一次改一次状态"。"""
        import inspect

        import main
        src = inspect.getsource(main.App._get_current_page_name)
        assert "_loop_count" not in src
        assert "_has_battle" not in src
