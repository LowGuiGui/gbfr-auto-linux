# -*- coding: utf-8 -*-
"""xusb.py：手柄报告的格式和按钮位。

这两组测试原来分别在 test_vigem.py 和 test_backend.py 里，跟着 xusb.py 一起挪过来。
"""

import ctypes

import xusb


class TestReportStruct:
    def test_field_layout_matches_XINPUT_GAMEPAD(self):
        """字段顺序和宽度由 ViGEmClient 的 ABI 决定，动了就会静默发错输入。"""
        assert [n for n, _ in xusb.XUSB_REPORT._fields_] == [
            "wButtons", "bLeftTrigger", "bRightTrigger",
            "sThumbLX", "sThumbLY", "sThumbRX", "sThumbRY",
        ]
        assert ctypes.sizeof(xusb.XUSB_REPORT) == 12

    def test_neutral_report_is_all_zero(self):
        r = xusb.XUSB_REPORT()
        assert (r.wButtons, r.sThumbLX, r.sThumbLY) == (0, 0, 0)

    def test_forward_uses_positive_full_scale_y(self):
        r = xusb.XUSB_REPORT(sThumbLY=xusb.STICK_MAX)
        assert r.sThumbLY == 32767


class TestButtonMasks:
    def test_names_resolve_to_the_documented_bits(self):
        """值抄自微软文档。按错一位在游戏里就是按错一个键。"""
        assert xusb.button_mask("a") == 0x1000
        assert xusb.button_mask("y") == 0x8000
        assert xusb.button_mask("right_thumb") == 0x0080
        assert xusb.button_mask("left_shoulder") == 0x0100

    def test_unknown_is_zero_not_an_exception(self):
        assert xusb.button_mask("no_such_button") == 0
        assert xusb.button_mask("") == 0

    def test_names_are_case_insensitive(self):
        assert xusb.button_mask("A") == xusb.button_mask("a")
