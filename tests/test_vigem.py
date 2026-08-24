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
    def test_missing_reg_command_reports_unknown_not_absent(self, monkeypatch):
        """查不了 != 没装。把两者混同会让人去装一个已经装好的驱动。"""
        def boom(*a, **k):
            raise FileNotFoundError("reg")
        monkeypatch.setattr(subprocess, "check_output", boom)
        assert vigem.driver_installed() == (None, None)

    def test_absent_driver_is_reported_absent(self, monkeypatch):
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "some other software")
        assert vigem.driver_installed() == (False, None)

    def test_present_driver_and_version_are_parsed(self, monkeypatch):
        blob = (
            "    DisplayName    REG_SZ    Something Else\n"
            "    DisplayVersion    REG_SZ    1.17.333.0\n"
            "    DisplayName    REG_SZ    Nefarius Virtual Gamepad Emulation Bus Driver\n"
        )
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: blob)
        installed, version = vigem.driver_installed()
        assert installed is True
        assert version == "1.17.333.0"

    def test_timeout_is_treated_as_unknown(self, monkeypatch):
        def slow(*a, **k):
            raise subprocess.TimeoutExpired("reg", 60)
        monkeypatch.setattr(subprocess, "check_output", slow)
        assert vigem.driver_installed() == (None, None)


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


class TestRegistryDecodingCannotCrashUs:
    """reg query 扫的是全系统软件名，什么语言都有。text=True 按本地代码页解码，
    撞上解不出的字节就抛 UnicodeDecodeError —— 它是 ValueError 的子类，既不是
    OSError 也不是 SubprocessError，漏掉的话会直接把整个探测干掉。
    """

    def test_a_decode_error_is_treated_as_unknown(self, monkeypatch):
        def undecodable(*a, **k):
            raise UnicodeDecodeError("gbk", b"\xff", 0, 1, "illegal multibyte")
        monkeypatch.setattr(subprocess, "check_output", undecodable)
        assert vigem.driver_installed() == (None, None)

    def test_replacement_characters_do_not_break_detection(self, monkeypatch):
        blob = (
            "    DisplayName    REG_SZ    ��� garbled product\n"
            "    DisplayVersion    REG_SZ    1.22.0\n"
            "    DisplayName    REG_SZ    Nefarius Virtual Gamepad Emulation Bus Driver\n"
        )
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: blob)
        installed, version = vigem.driver_installed()
        assert installed is True and version == "1.22.0"

    def test_errors_replace_is_actually_requested(self):
        """靠 errors='replace' 才不会抛。这条防止有人把它删掉。"""
        import inspect
        src = inspect.getsource(vigem.driver_installed)
        assert 'errors="replace"' in src
