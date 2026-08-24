# -*- coding: utf-8 -*-
"""ViGEmClient 的最小 ctypes 封装 —— 虚拟 Xbox360 手柄。

为什么不用 vgamepad 这个包：PyPI 上的 0.1.0 在 **安装时** 就会无条件调用
`msiexec /i ViGEmBusSetup.msi`（它的 setup.py 第 55-56 行），没有跳过开关 —— 那个
开关只存在于还没发布的 GitHub HEAD。在 CI 里 pip install 它，等于往构建机上装内核
驱动，要么卡住要么装上，两种都不能接受。

所以直接带两个二进制走：
    vigem/ViGEmClient.dll        130 KB  客户端库，用 ctypes 调
    vigem/ViGEmBusSetup_x64.msi  876 KB  驱动安装包，用户明确要求时才运行

两个都从 vgamepad 的 sdist 里取（MIT，与本仓库的 GPL-2.0 兼容），由 CI 在构建时
下载解包，**不进版本库** —— 和 #6 把预编译 DLL 移出仓库是同一条规矩。

ViGEmBus 本身是内核驱动。装它是一件重量级、不该悄悄发生的事，所以这里只提供
"检测"和"在明确要求下启动官方安装包"，绝不自动安装。
"""

import ctypes
import os
import subprocess
import sys
from ctypes import Structure, c_byte, c_short, c_uint, c_ushort, c_void_p

VIGEM_ERROR_NONE = 0x20000000
VIGEM_ERROR_BUS_NOT_FOUND = 0xE0000001

XUSB_GAMEPAD_A = 0x1000
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


def _bundle_dir():
    """打包后资源在 _MEIPASS，源码运行时在仓库根。"""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_dll_path():
    return os.path.join(_bundle_dir(), "vigem", "ViGEmClient.dll")


def installer_path():
    return os.path.join(_bundle_dir(), "vigem", "ViGEmBusSetup_x64.msi")


def driver_installed():
    """查注册表判断 ViGEmBus 驱动在不在。

    与 vgamepad 的 setup.py 用的是同一个判据（卸载项里找驱动显示名）。
    返回 (是否已装, 版本或 None)。
    """
    try:
        out = subprocess.check_output(
            ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "/s"],
            text=True, stderr=subprocess.DEVNULL, timeout=60,
        ).lower()
    except (OSError, subprocess.SubprocessError):
        return None, None          # 查不了，不等于没装

    marker = "nefarius virtual gamepad emulation bus driver"
    at = out.find(marker)
    if at < 0:
        return False, None

    version = None
    before = out[:at].rfind("displayversion")
    if before != -1:
        parts = out[before:at].split()
        if len(parts) >= 3:
            version = parts[2]
    return True, version


def launch_installer():
    """启动官方 MSI。会弹 UAC 和安装向导 —— 用户自己点。

    只在调用方明确要求时才调。返回 (是否启动成功, 说明)。
    """
    msi = installer_path()
    if not os.path.exists(msi):
        return False, f"找不到内置安装包: {msi}"
    try:
        subprocess.call(["msiexec", "/i", msi])
    except OSError as e:
        return False, f"启动 msiexec 失败: {e}"
    return True, "安装程序已退出。装完通常需要重启一次再试。"


class VirtualGamepad:
    """一个虚拟 Xbox360 手柄。用 with 语句保证一定会拔掉。"""

    def __init__(self):
        self._dll = None
        self._client = None
        self._target = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def connect(self):
        dll_path = client_dll_path()
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"找不到 ViGEmClient.dll: {dll_path}")

        self._dll = ctypes.CDLL(dll_path)
        d = self._dll
        d.vigem_alloc.restype = c_void_p
        d.vigem_free.argtypes = (c_void_p,)
        d.vigem_connect.argtypes = (c_void_p,)
        d.vigem_connect.restype = c_uint
        d.vigem_disconnect.argtypes = (c_void_p,)
        d.vigem_target_x360_alloc.restype = c_void_p
        d.vigem_target_free.argtypes = (c_void_p,)
        d.vigem_target_add.argtypes = (c_void_p, c_void_p)
        d.vigem_target_add.restype = c_uint
        d.vigem_target_remove.argtypes = (c_void_p, c_void_p)
        d.vigem_target_remove.restype = c_uint
        d.vigem_target_x360_update.argtypes = (c_void_p, c_void_p, XUSB_REPORT)
        d.vigem_target_x360_update.restype = c_uint

        self._client = d.vigem_alloc()
        if not self._client:
            raise RuntimeError("vigem_alloc 返回空 —— 内存分配失败")

        err = d.vigem_connect(self._client)
        if err != VIGEM_ERROR_NONE:
            self._client, client = None, self._client
            d.vigem_free(client)
            if err == VIGEM_ERROR_BUS_NOT_FOUND:
                raise RuntimeError("找不到 ViGEmBus 驱动 —— 驱动没装或没启动")
            raise RuntimeError(f"vigem_connect 失败: 0x{err:08X}")

        self._target = d.vigem_target_x360_alloc()
        err = d.vigem_target_add(self._client, self._target)
        if err != VIGEM_ERROR_NONE:
            self.close()
            raise RuntimeError(f"vigem_target_add 失败: 0x{err:08X}")

    def send(self, report):
        err = self._dll.vigem_target_x360_update(self._client, self._target, report)
        if err != VIGEM_ERROR_NONE:
            raise RuntimeError(f"vigem_target_x360_update 失败: 0x{err:08X}")

    def left_stick_forward(self):
        self.send(XUSB_REPORT(sThumbLY=STICK_MAX))

    def press_a(self):
        self.send(XUSB_REPORT(wButtons=XUSB_GAMEPAD_A))

    def neutral(self):
        self.send(XUSB_REPORT())

    def close(self):
        """一定要跑到 —— 否则虚拟手柄会一直挂在系统里，摇杆还推着。"""
        d = self._dll
        if d is None:
            return
        try:
            if self._target is not None:
                if self._client is not None:
                    d.vigem_target_remove(self._client, self._target)
                d.vigem_target_free(self._target)
        finally:
            self._target = None
            if self._client is not None:
                d.vigem_disconnect(self._client)
                d.vigem_free(self._client)
            self._client = None
            self._dll = None
