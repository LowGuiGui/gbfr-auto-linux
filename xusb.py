# -*- coding: utf-8 -*-
"""XInput 手柄报告的格式和按钮位。

从 vigem.py 拆出来的平台无关部分。XUSB_REPORT 的字段布局和 XINPUT_GAMEPAD 一致，
按钮位抄自微软文档。它们只是数据格式，和 ViGEmBus 驱动、Windows API 都无关：
backend.PadBackend 用它合成整份手柄状态，vigem.py 把同一份报告交给驱动。
"""

from ctypes import Structure, c_byte, c_short, c_ushort

STICK_MAX = 32767


class XUSB_REPORT(Structure):
    """与 XINPUT_GAMEPAD 兼容的报告结构，字段顺序不能动。"""

    _fields_ = [
        ("wButtons", c_ushort),
        ("bLeftTrigger", c_byte),
        ("bRightTrigger", c_byte),
        ("sThumbLX", c_short),
        ("sThumbLY", c_short),
        ("sThumbRX", c_short),
        ("sThumbRY", c_short),
    ]


# XINPUT_GAMEPAD 的按钮位掩码。值抄自微软文档（ns-xinput-xinput_gamepad），
# 不是记忆 —— 按错一个位在游戏里就是按错一个键，而且不会报错。
XUSB_DPAD_UP        = 0x0001
XUSB_DPAD_DOWN      = 0x0002
XUSB_DPAD_LEFT      = 0x0004
XUSB_DPAD_RIGHT     = 0x0008
XUSB_START          = 0x0010
XUSB_BACK           = 0x0020
XUSB_LEFT_THUMB     = 0x0040
XUSB_RIGHT_THUMB    = 0x0080
XUSB_LEFT_SHOULDER  = 0x0100
XUSB_RIGHT_SHOULDER = 0x0200
XUSB_A              = 0x1000
XUSB_B              = 0x2000
XUSB_X              = 0x4000
XUSB_Y              = 0x8000

# 配置里用名字，代码里用位。名字是给人看的，也是给 config.toml 用的。
BUTTONS = {
    "dpad_up": XUSB_DPAD_UP, "dpad_down": XUSB_DPAD_DOWN,
    "dpad_left": XUSB_DPAD_LEFT, "dpad_right": XUSB_DPAD_RIGHT,
    "start": XUSB_START, "back": XUSB_BACK,
    "left_thumb": XUSB_LEFT_THUMB, "right_thumb": XUSB_RIGHT_THUMB,
    "left_shoulder": XUSB_LEFT_SHOULDER, "right_shoulder": XUSB_RIGHT_SHOULDER,
    "a": XUSB_A, "b": XUSB_B, "x": XUSB_X, "y": XUSB_Y,
}


def button_mask(name):
    """按名字取位掩码。不认识的名字返回 0 —— 调用方要把这当成"没配对"。"""
    return BUTTONS.get(str(name).strip().lower(), 0)
