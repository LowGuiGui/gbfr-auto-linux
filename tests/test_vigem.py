# -*- coding: utf-8 -*-
"""vigem.py —— Windows 才能真的连驱动，但路径解析和失败路径可以在这里测。"""

import ctypes
import os
import subprocess
import sys

import pytest

import vigem


class TestBundlePaths:
    def test_source_run_resolves_next_to_the_repo(self):
        assert vigem.client_dll_path().endswith(
            os.path.join(vigem.BUNDLE_SUBDIR, "ViGEmClient.dll")
        )

    def test_frozen_run_resolves_under_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert vigem.client_dll_path() == str(
            tmp_path / vigem.BUNDLE_SUBDIR / "ViGEmClient.dll"
        )

    def test_the_bundle_dir_does_not_collide_with_the_module_name(self):
        """PyInstaller 6 的冻结模块走 sys.path_hooks，_MEIPASS 下同名目录会和本
        模块抢 `import vigem`。哪边赢取决于 finder 内部顺序 —— 不如让它不可能发生。
        """
        assert vigem.BUNDLE_SUBDIR != "vigem"
        assert vigem.BUNDLE_SUBDIR != os.path.splitext(os.path.basename(vigem.__file__))[0]


class TestDriverDetection:
    """卸载项检测曾经两处都错，在一个装得好好的 1.22.0 上报"没装"。"""

    def _fake_winreg(self, monkeypatch, entries, views_seen=None):
        """entries: {(path, view): {subkey: {value: data}}}"""
        import types
        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE = 0x80000002
        fake.KEY_READ = 0x20019
        fake.KEY_WOW64_64KEY = 0x0100
        fake.KEY_WOW64_32KEY = 0x0200

        class Key:
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def open_key(root, path, reserved=0, access=0):
            if isinstance(root, Key):                 # 打开子键
                if path not in root.data:
                    raise FileNotFoundError(path)
                return Key(root.data[path])
            view = access & (fake.KEY_WOW64_64KEY | fake.KEY_WOW64_32KEY)
            if views_seen is not None:
                views_seen.append((path, view))
            if (path, view) not in entries:
                raise FileNotFoundError(path)
            return Key(entries[(path, view)])

        def enum_key(key, index):
            names = list(key.data)
            if index >= len(names):
                raise OSError("no more")
            return names[index]

        def query(key, name):
            if name not in key.data:
                raise FileNotFoundError(name)
            return (key.data[name], 1)

        fake.OpenKey, fake.EnumKey, fake.QueryValueEx = open_key, enum_key, query
        monkeypatch.setitem(sys.modules, "winreg", fake)
        return fake

    UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    WOW = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"

    def test_a_32bit_install_under_wow6432node_is_found(self, monkeypatch):
        """官方安装器是 32 位的，卸载项落在 WOW6432Node —— 64 位进程默认看不到。"""
        self._fake_winreg(monkeypatch, {
            (self.UNINSTALL, 0): {},
            (self.WOW, 0): {"{guid}": {"DisplayName": "ViGEm Bus Driver",
                                       "DisplayVersion": "1.22.0"}},
        })
        assert vigem.driver_installed() == (True, "1.22.0")

    def test_the_modern_product_name_is_recognised(self, monkeypatch):
        """1.22.0 叫 "ViGEm Bus Driver"，不是老 MSI 那个长名字。"""
        self._fake_winreg(monkeypatch, {
            (self.UNINSTALL, 0): {"{g}": {"DisplayName": "ViGEm Bus Driver",
                                          "DisplayVersion": "1.22.0"}},
        })
        assert vigem.driver_installed()[0] is True

    def test_the_legacy_product_name_still_matches(self, monkeypatch):
        self._fake_winreg(monkeypatch, {
            (self.UNINSTALL, 0): {"{g}": {
                "DisplayName": "Nefarius Virtual Gamepad Emulation Bus Driver",
                "DisplayVersion": "1.17.333"}},
        })
        assert vigem.driver_installed() == (True, "1.17.333")

    def test_both_registry_views_are_queried(self, monkeypatch):
        seen = []
        self._fake_winreg(monkeypatch, {(self.UNINSTALL, 0): {}}, views_seen=seen)
        vigem.driver_installed()
        paths = {p for p, _ in seen}
        assert self.UNINSTALL in paths and self.WOW in paths

    def test_absent_driver_is_reported_absent(self, monkeypatch):
        self._fake_winreg(monkeypatch, {
            (self.UNINSTALL, 0): {"{x}": {"DisplayName": "Some Other Product"}},
        })
        assert vigem.driver_installed() == (False, None)

    def test_unreadable_registry_is_unknown_not_absent(self, monkeypatch):
        """查不了 != 没装。混同会让人去装一个已经装好的驱动。"""
        self._fake_winreg(monkeypatch, {})
        assert vigem.driver_installed() == (None, None)

    def test_missing_winreg_is_unknown(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "winreg", None)
        assert vigem.driver_installed() == (None, None)

    def test_an_entry_without_a_version_still_counts(self, monkeypatch):
        self._fake_winreg(monkeypatch, {
            (self.UNINSTALL, 0): {"{g}": {"DisplayName": "ViGEm Bus Driver"}},
        })
        assert vigem.driver_installed() == (True, None)


class TestWeNeverInstallTheDriver:
    """内核驱动由用户从官方渠道自己装。这里不碰。"""

    def test_no_installer_is_bundled_or_launched(self, monkeypatch, tmp_path):
        assert not hasattr(vigem, "launch_installer")
        assert not hasattr(vigem, "installer_path")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        calls = []
        monkeypatch.setattr(subprocess, "call", lambda *a, **k: calls.append(a))
        pad = vigem.VirtualGamepad()
        with pytest.raises(FileNotFoundError):
            pad.connect()
        assert calls == []

    def test_the_download_url_points_at_the_official_release(self):
        """曾经内置过 vgamepad 里的 1.17.333.0（2021 年），比官方最新落后一大截。"""
        assert vigem.DRIVER_VERSION == "1.22.0"
        assert vigem.DRIVER_DOWNLOAD_URL.startswith(
            "https://github.com/ViGEm/ViGEmBus/releases/download/"
        )
        assert vigem.DRIVER_VERSION in vigem.DRIVER_DOWNLOAD_URL


class TestReportStruct:
    def test_field_layout_matches_XINPUT_GAMEPAD(self):
        """字段顺序和宽度由 ViGEmClient 的 ABI 决定，动了就会静默发错输入。"""
        assert [n for n, _ in vigem.XUSB_REPORT._fields_] == [
            "wButtons", "bLeftTrigger", "bRightTrigger",
            "sThumbLX", "sThumbLY", "sThumbRX", "sThumbRY",
        ]
        assert ctypes.sizeof(vigem.XUSB_REPORT) == 12

    def test_neutral_report_is_all_zero(self):
        r = vigem.XUSB_REPORT()
        assert (r.wButtons, r.sThumbLX, r.sThumbLY) == (0, 0, 0)

    def test_forward_uses_positive_full_scale_y(self):
        r = vigem.XUSB_REPORT(sThumbLY=vigem.STICK_MAX)
        assert r.sThumbLY == 32767


class TestCleanup:
    def test_close_is_safe_before_connect(self):
        """close() 必须能在任何状态下调用 —— 它跑在 finally 里。"""
        vigem.VirtualGamepad().close()

    def test_close_is_idempotent(self):
        pad = vigem.VirtualGamepad()
        pad.close()
        pad.close()


class TestDuplicateBusDetection:
    """两份 ViGEmBus 会留下重复的总线设备实例。

    第一版去开 Enum\\Nefarius\\ViGEmBus\\Gen1，在真机上数出 0 个 —— 而手柄
    明明连上了。那是硬件 ID，不是枚举路径；设备是 root 枚举的，实例在
    Enum\\ROOT 底下。这几条测试钉住正确的走法。
    """

    def _registry(self, monkeypatch, tree):
        """tree: {family: {instance: {"HardwareID": [...]}}}；None 表示 ROOT 键不存在。"""
        import types
        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE = 0x80000002

        class Key:
            def __init__(self, data):
                self.data = data
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def open_key(root, path, *a, **k):
            if isinstance(root, Key):
                if path not in root.data:
                    raise FileNotFoundError(path)
                return Key(root.data[path])
            if tree is None:
                raise FileNotFoundError(path)
            return Key(tree)

        def enum_key(key, index):
            names = [k for k in key.data if not isinstance(key.data[k], list)]
            if index >= len(names):
                raise OSError("no more")
            return names[index]

        def query(key, name):
            if name not in key.data:
                raise FileNotFoundError(name)
            return (key.data[name], 7)

        fake.OpenKey, fake.EnumKey, fake.QueryValueEx = open_key, enum_key, query
        monkeypatch.setitem(sys.modules, "winreg", fake)

    def test_it_matches_on_hardware_id_under_ROOT(self, monkeypatch):
        self._registry(monkeypatch, {
            "SYSTEM": {"0001": {"HardwareID": ["Nefarius\\ViGEmBus\\Gen1"]}},
            "OTHER": {"0000": {"HardwareID": ["Something\\Else"]}},
        })
        assert vigem.bus_device_instances() == ["ROOT\\SYSTEM\\0001"]

    def test_two_instances_are_reported(self, monkeypatch):
        self._registry(monkeypatch, {
            "SYSTEM": {
                "0001": {"HardwareID": ["Nefarius\\ViGEmBus\\Gen1"]},
                "0002": {"HardwareID": ["Nefarius\\ViGEmBus\\Gen1"]},
            },
        })
        assert len(vigem.bus_device_instances()) == 2

    def test_the_match_is_case_insensitive(self, monkeypatch):
        """注册表里的大小写不保证。"""
        self._registry(monkeypatch, {
            "SYSTEM": {"0001": {"HardwareID": ["NEFARIUS\\VIGEMBUS\\GEN1"]}},
        })
        assert len(vigem.bus_device_instances()) == 1

    def test_a_single_string_hardware_id_is_handled(self, monkeypatch):
        """HardwareID 通常是 REG_MULTI_SZ，但不能假设一定是列表。"""
        self._registry(monkeypatch, {
            "SYSTEM": {"0001": {"HardwareID": "Nefarius\\ViGEmBus\\Gen1"}},
        })
        assert len(vigem.bus_device_instances()) == 1

    def test_no_matching_device_is_an_empty_list(self, monkeypatch):
        self._registry(monkeypatch, {"SYSTEM": {"0001": {"HardwareID": ["Other\\Thing"]}}})
        assert vigem.bus_device_instances() == []

    def test_unreadable_registry_is_none_not_zero(self, monkeypatch):
        """0 和"读不到"必须分开 —— 报 0 会让人以为驱动没装。"""
        self._registry(monkeypatch, None)
        assert vigem.bus_device_instances() is None


class TestErrorNames:
    def test_the_observed_failure_is_named(self):
        """0xE0000007 是 Howard 那次真实失败的返回码。"""
        assert "TARGET_NOT_PLUGGED_IN" in vigem.error_name(0xE0000007)
        assert "0xE0000007" in vigem.error_name(0xE0000007)

    def test_bus_version_mismatch_is_named(self):
        assert "BUS_VERSION_MISMATCH" in vigem.error_name(0xE0000008)

    def test_an_unknown_code_still_shows_the_hex(self):
        assert "0xDEADBEEF" in vigem.error_name(0xDEADBEEF)
        assert "unknown" in vigem.error_name(0xDEADBEEF)


class TestConnectRetries:
    """ViGEmClient 自己的源码注释就说这条路径有竞态、建议调用方重试。"""

    def test_it_retries_before_giving_up(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(vigem, "RETRY_DELAY_S", 0)
        calls = []

        class FakeDLL:
            def __getattr__(self, name):
                def fn(*a, **k):
                    if name == "vigem_target_add":
                        calls.append(1)
                        return 0xE0000007
                    if name in ("vigem_alloc", "vigem_target_x360_alloc"):
                        return 1234
                    return vigem.VIGEM_ERROR_NONE
                fn.argtypes = fn.restype = None
                return fn

        monkeypatch.setattr(vigem.os.path, "exists", lambda p: True)
        monkeypatch.setattr(vigem.ctypes, "CDLL", lambda p: FakeDLL())
        pad = vigem.VirtualGamepad()
        with pytest.raises(RuntimeError) as excinfo:
            pad.connect()
        assert len(calls) == vigem.RETRY_ATTEMPTS
        assert "TARGET_NOT_PLUGGED_IN" in str(excinfo.value), "错误码要翻成名字"

    def test_a_later_attempt_succeeding_is_recorded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(vigem, "RETRY_DELAY_S", 0)
        state = {"n": 0}

        class FakeDLL:
            def __getattr__(self, name):
                def fn(*a, **k):
                    if name == "vigem_target_add":
                        state["n"] += 1
                        return vigem.VIGEM_ERROR_NONE if state["n"] == 2 else 0xE0000007
                    if name in ("vigem_alloc", "vigem_target_x360_alloc"):
                        return 1234
                    return vigem.VIGEM_ERROR_NONE
                fn.argtypes = fn.restype = None
                return fn

        monkeypatch.setattr(vigem.os.path, "exists", lambda p: True)
        monkeypatch.setattr(vigem.ctypes, "CDLL", lambda p: FakeDLL())
        pad = vigem.VirtualGamepad()
        pad.connect()
        assert pad.attempts_used == 2
