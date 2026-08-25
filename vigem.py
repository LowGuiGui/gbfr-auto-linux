# -*- coding: utf-8 -*-
"""ViGEmClient 的最小 ctypes 封装 —— 虚拟 Xbox360 手柄。

为什么不用 vgamepad 这个包：PyPI 上的 0.1.0 在 **安装时** 就会无条件调用
`msiexec /i ViGEmBusSetup.msi`（它的 setup.py 第 55-56 行），没有跳过开关 —— 那个
开关只存在于还没发布的 GitHub HEAD。在 CI 里 pip install 它，等于往构建机上装内核
驱动，要么卡住要么装上，两种都不能接受。

所以只带一个用户态库走：
    vigem_bin/ViGEmClient.dll   130 KB   客户端库，用 ctypes 调

目录叫 vigem_bin 而不是 vigem，是**故意**的：PyInstaller 6 的冻结模块走的是
sys.path_hooks 而不是 sys.meta_path，所以 _MEIPASS 下一个叫 vigem 的目录会和
本模块 vigem 争同一个名字。哪个赢取决于 PyiFrozenFinder 的内部顺序 —— 与其推理，
不如让它不可能发生。

从 vgamepad 的 sdist 里取（MIT，与本仓库的 GPL-2.0 兼容），CI 构建时下载解包，
**不进版本库** —— 和 #6 把预编译 DLL 移出仓库是同一条规矩。

**驱动安装包不内置。** 曾经内置过，是个错误：vgamepad 0.1.0 里的 MSI 是
ViGEmBus 1.17.333.0（2021 年），而官方最新是 1.22.0。散发一个五年前的内核驱动
装到别人机器上，既有兼容性风险，又会和别的工具装的新版本打架。检测到没装时，
直接给出官方下载地址，由用户自己装 —— 一个 exe，双击即可，同样不需要 Python。
"""

import ctypes
import os
import subprocess
import sys
from ctypes import Structure, c_byte, c_short, c_uint, c_ushort, c_void_p

VIGEM_ERROR_NONE = 0x20000000
VIGEM_ERROR_BUS_NOT_FOUND = 0xE0000001

STICK_MAX = 32767

# 官方 ViGEmBus 发行版。仓库 2023-11 归档，1.22.0 是最后一版，仍可下载，
# 单个签名 exe，含 x64/x86/arm64。
DRIVER_VERSION = "1.22.0"
DRIVER_DOWNLOAD_URL = (
    "https://github.com/ViGEm/ViGEmBus/releases/download/"
    f"v{DRIVER_VERSION}/ViGEmBus_{DRIVER_VERSION}_x64_x86_arm64.exe"
)


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


BUNDLE_SUBDIR = "vigem_bin"


def client_dll_path():
    return os.path.join(_bundle_dir(), BUNDLE_SUBDIR, "ViGEmClient.dll")


def driver_service_present():
    """看驱动服务键在不在。

    比卸载项可靠：不管是 MSI 装的还是 nefconw 手动装的，只要驱动进了系统，
    SYSTEM\\CurrentControlSet\\Services\\ViGEmBus 就会存在。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\ViGEmBus"):
            return True
    except FileNotFoundError:
        return False
    except (OSError, ImportError):
        return None


def driver_installed():
    """查卸载项判断 ViGEmBus 在不在。

    **只是一条线索，不是结论。** 它找的是 MSI 安装留下的卸载条目；用 nefconw
    手动装驱动（官方支持的方式）根本不写这个条目，于是驱动明明装好了却被判成
    "没装"。判断装没装的唯一可靠办法是真的去连一次 —— 见 probe_gamepad。

    返回 (是否已装, 版本或 None)。
    """
    try:
        out = subprocess.check_output(
            ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "/s"],
            # 这棵子树里是全系统的软件名，什么语言都有。text=True 会按本地代码页
            # 解码，撞上解不出的字节就抛 UnicodeDecodeError —— 那是 ValueError 的
            # 子类，既不是 OSError 也不是 SubprocessError，不 replace 的话会直接
            # 逃出去把整个探测干掉。
            text=True, errors="replace",
            stderr=subprocess.DEVNULL, timeout=60,
        ).lower()
    except (OSError, subprocess.SubprocessError, UnicodeError):
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
