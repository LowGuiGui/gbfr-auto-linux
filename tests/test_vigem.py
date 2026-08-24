# -*- coding: utf-8 -*-
"""tools/vigem.py —— Windows 才能真的连驱动，但路径解析和失败路径可以在这里测。"""

import ctypes
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import vigem  # noqa: E402


class TestBundlePaths:
    def test_source_run_resolves_next_to_the_repo(self):
        assert vigem.client_dll_path().endswith(os.path.join("vigem", "ViGEmClient.dll"))
        assert vigem.installer_path().endswith(os.path.join("vigem", "ViGEmBusSetup_x64.msi"))

    def test_frozen_run_resolves_under_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert vigem.client_dll_path() == str(tmp_path / "vigem" / "ViGEmClient.dll")
        assert vigem.installer_path() == str(tmp_path / "vigem" / "ViGEmBusSetup_x64.msi")


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


class TestInstallerLaunch:
    def test_missing_msi_is_reported_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        ok, message = vigem.launch_installer()
        assert ok is False
        assert "找不到内置安装包" in message

    def test_present_msi_is_launched_via_msiexec(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        (tmp_path / "vigem").mkdir()
        (tmp_path / "vigem" / "ViGEmBusSetup_x64.msi").write_bytes(b"not really an msi")
        calls = []
        monkeypatch.setattr(subprocess, "call", lambda cmd: calls.append(cmd))
        ok, _ = vigem.launch_installer()
        assert ok is True
        assert calls[0][0] == "msiexec" and calls[0][1] == "/i"

    def test_it_is_never_launched_implicitly(self, monkeypatch, tmp_path):
        """装内核驱动必须是显式动作。构造手柄对象不能触发它。"""
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        calls = []
        monkeypatch.setattr(subprocess, "call", lambda cmd: calls.append(cmd))
        pad = vigem.VirtualGamepad()
        with pytest.raises(FileNotFoundError):
            pad.connect()
        assert calls == []


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
