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
import sys
import time
from ctypes import Structure, c_byte, c_short, c_uint, c_ushort, c_void_p

VIGEM_ERROR_NONE = 0x20000000
VIGEM_ERROR_BUS_NOT_FOUND = 0xE0000001

# 报告里给名字而不是裸十六进制。取自 ViGEmClient 的 include/ViGEm/Client.h。
VIGEM_ERRORS = {
    0x20000000: "VIGEM_ERROR_NONE",
    0xE0000001: "VIGEM_ERROR_BUS_NOT_FOUND",
    0xE0000002: "VIGEM_ERROR_NO_FREE_SLOT",
    0xE0000003: "VIGEM_ERROR_INVALID_TARGET",
    0xE0000004: "VIGEM_ERROR_REMOVAL_FAILED",
    0xE0000005: "VIGEM_ERROR_ALREADY_CONNECTED",
    0xE0000006: "VIGEM_ERROR_TARGET_UNINITIALIZED",
    0xE0000007: "VIGEM_ERROR_TARGET_NOT_PLUGGED_IN",
    0xE0000008: "VIGEM_ERROR_BUS_VERSION_MISMATCH",
    0xE0000009: "VIGEM_ERROR_BUS_ACCESS_FAILED",
    0xE0000010: "VIGEM_ERROR_CALLBACK_ALREADY_REGISTERED",
    0xE0000011: "VIGEM_ERROR_CALLBACK_NOT_FOUND",
    0xE0000012: "VIGEM_ERROR_BUS_ALREADY_CONNECTED",
    0xE0000013: "VIGEM_ERROR_BUS_INVALID_HANDLE",
    0xE0000014: "VIGEM_ERROR_XUSB_USERINDEX_OUT_OF_RANGE",
    0xE0000015: "VIGEM_ERROR_INVALID_PARAMETER",
    0xE0000016: "VIGEM_ERROR_NOT_SUPPORTED",
    0xE0000017: "VIGEM_ERROR_WINAPI",
    0xE0000018: "VIGEM_ERROR_TIMED_OUT",
    0xE0000019: "VIGEM_ERROR_IS_DISPOSING",
}


def error_name(code):
    return VIGEM_ERRORS.get(code, "unknown") + f" (0x{code:08X})"

STICK_MAX = 32767

# 见 connect()：设备就绪的等待本来就是有竞态的，客户端源码建议重试。
RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 1.0

# 官方 ViGEmBus 发行版。仓库 2023-11 归档，1.22.0 是最后一版，仍可下载，
# 单个签名 exe，含 x64/x86/arm64。
#
# 注意这个 exe 是**安装程序**，直接双击就装。带 /extract 参数只会把驱动文件
# （ViGEmBus.inf/.sys/.cat + nefconw.exe）解出来，什么都不装 —— 解压出来的
# 那个文件夹很容易被误当成"装好了"。
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


# 卸载项里可能出现的显示名。1.22.0 的 ProductName 是 "ViGEm Bus Driver"；
# 老的 1.17.333 MSI 用的是长名字（vgamepad 的 setup.py 就是照那个写的，
# 我们最初也照抄了 —— 于是在 1.22.0 上永远匹配不上）。
_UNINSTALL_MARKERS = (
    "vigem bus driver",
    "nefarius virtual gamepad emulation bus driver",
    "vigembus",
)

_UNINSTALL_PATHS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def driver_installed():
    """在卸载项里找 ViGEmBus。

    **只是一条线索，不是结论。** 用 nefconw 手动装驱动根本不写卸载项，而驱动
    照样是装好的。判断装没装的唯一可靠办法是真的去连一次 —— 见 probe_gamepad。

    返回 (是否已装, 显示版本或 None)；读不到注册表时返回 (None, None)。

    两件事必须做对，之前两件都错了：
      1. **两个注册表视图都要查。** 官方安装器是 32 位的，它的卸载项落在
         WOW6432Node 下；64 位进程默认看不到那一半。
      2. **显示名不止一个。** 见 _UNINSTALL_MARKERS。
    """
    try:
        import winreg
    except ImportError:
        return None, None

    views = [0]
    for flag in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        value = getattr(winreg, flag, None)
        if value:
            views.append(value)

    readable = False
    for path in _UNINSTALL_PATHS:
        for view in views:
            try:
                root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path,
                                      0, winreg.KEY_READ | view)
            except OSError:
                continue
            readable = True
            with root:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, name, 0,
                                            winreg.KEY_READ | view) as entry:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                            if not any(m in display.lower() for m in _UNINSTALL_MARKERS):
                                continue
                            try:
                                version = str(winreg.QueryValueEx(entry, "DisplayVersion")[0])
                            except OSError:
                                version = None
                            return True, version
                    except (OSError, ValueError):
                        continue

    return (False, None) if readable else (None, None)


def bus_device_instances():
    """列出 ViGEmBus 的设备实例。

    **多于一个就是问题**：装过两遍 ViGEmBus 会留下重复的总线设备，客户端连上
    其中一个、设备却在另一个上枚举。

    第一版直接去开 Enum\\Nefarius\\ViGEmBus\\Gen1，结果在真机上数出 0 个 ——
    而手柄明明连上了。那是硬件 ID，不是枚举路径：这个设备是 root 枚举的，实例
    在 Enum\\ROOT 底下，硬件 ID 只是它的一个属性。所以改成遍历 ROOT 子树、按
    HardwareID 匹配。

    返回实例路径列表；读不到注册表时返回 None。
    """
    try:
        import winreg
    except ImportError:
        return None

    target = "nefarius\\vigembus\\gen1"
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Enum\ROOT") as root:
            for family in _subkeys(winreg, root):
                try:
                    with winreg.OpenKey(root, family) as family_key:
                        for instance in _subkeys(winreg, family_key):
                            try:
                                with winreg.OpenKey(family_key, instance) as dev:
                                    ids = winreg.QueryValueEx(dev, "HardwareID")[0]
                            except OSError:
                                continue
                            if isinstance(ids, str):
                                ids = [ids]
                            if any(target == str(i).lower() for i in ids or ()):
                                found.append(f"ROOT\\{family}\\{instance}")
                except OSError:
                    continue
    except OSError:
        return None
    return found


def _subkeys(winreg, key):
    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            break
        index += 1
    return names


def loaded_driver_path():
    """注册的驱动服务指向哪个 .sys。

    装了两遍时，服务指向的文件和驱动仓库里实际加载的可能对不上。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\ViGEmBus") as key:
            return str(winreg.QueryValueEx(key, "ImagePath")[0])
    except (OSError, ImportError):
        return None


# 已知会同时使用 ViGEmBus 的软件。它们不是"冲突"本身，但装了两份驱动、
# 或者服务把总线占住，都会表现成我们这种失败。
KNOWN_VIGEM_USERS = (
    ("SunshineService", "Sunshine (game streaming host)"),
    ("HidHide", "HidHide (hides physical controllers)"),
    ("DS4Windows", "DS4Windows"),
)


def other_vigem_users():
    """查一下本机还有谁在用 ViGEmBus。返回 [(服务名, 说明, 是否在运行)]。"""
    try:
        import winreg
    except ImportError:
        return None
    found = []
    for service, description in KNOWN_VIGEM_USERS:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SYSTEM\CurrentControlSet\Services\{service}",
            ) as key:
                try:
                    start = winreg.QueryValueEx(key, "Start")[0]
                except OSError:
                    start = None
                # Start: 2=自动 3=手动 4=禁用
                found.append((service, description, start))
        except (OSError, ValueError):
            continue
    return found


class VirtualGamepad:
    """一个虚拟 Xbox360 手柄。用 with 语句保证一定会拔掉。"""

    def __init__(self):
        self._dll = None
        self._client = None
        self._target = None
        self.attempts_used = 0

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

        # vigem_target_add 会先 PLUGIN_TARGET（此时 Windows 已经响了插入音），
        # 再发 WAIT_DEVICE_READY 等设备就绪。等待失败时客户端会自己把设备拔掉
        # （拔出音），并把 **vigem_target_remove 的** 返回值交出来 —— 于是错误码
        # 常常是 TARGET_NOT_PLUGGED_IN，掩盖了真正失败的那一步。
        #
        # ViGEmClient 的源码注释就写着这条路径"不是 100% 可靠……希望调用方忽略
        # 这些错误并重试"。所以这里重试，而不是一次就放弃。
        last = None
        for attempt in range(RETRY_ATTEMPTS):
            err = d.vigem_target_add(self._client, self._target)
            if err == VIGEM_ERROR_NONE:
                self.attempts_used = attempt + 1
                return
            last = err
            if attempt + 1 < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY_S)

        self.close()
        raise RuntimeError(
            f"vigem_target_add failed after {RETRY_ATTEMPTS} attempts: "
            f"{error_name(last)}"
        )

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
