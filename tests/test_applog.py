# -*- coding: utf-8 -*-
"""applog.py —— 纯标准库，与平台无关，按原样测。"""

import logging
import os
import stat
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import applog


def test_writes_to_the_preferred_directory(tmp_path, log_file):
    applog.get_logger("probe").info("hello")
    assert "hello" in log_file()
    assert str(tmp_path) in log_file.path


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 在 Windows 上不会真的挡住写入，这条路径只能在 POSIX 上测",
)
def test_falls_back_to_temp_when_preferred_is_unwritable(tmp_path):
    applog._file_handler = None
    logger = logging.getLogger(applog.ROOT_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    readonly = tmp_path / "readonly"
    readonly.mkdir()
    os.chmod(readonly, stat.S_IRUSR | stat.S_IXUSR)
    try:
        path = applog.setup(str(readonly / "nested"))
        assert path is not None
        assert path.startswith(tempfile.gettempdir())
    finally:
        os.chmod(readonly, stat.S_IRWXU)
        for h in list(logging.getLogger(applog.ROOT_NAME).handlers):
            logging.getLogger(applog.ROOT_NAME).removeHandler(h)
            h.close()
        applog._file_handler = None


def test_setup_is_idempotent(tmp_path, log_file):
    first = log_file.path
    assert applog.setup(str(tmp_path / "somewhere-else")) == first
    # 只数文件 handler —— pytest 自己会往 logger 上挂 LogCaptureHandler
    files = [
        h for h in logging.getLogger(applog.ROOT_NAME).handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert len(files) == 1


def test_does_not_propagate_to_root(log_file):
    """--windowed 打包后没有 stderr，冒泡到 root 会走 lastResort 打进虚空。"""
    assert logging.getLogger(applog.ROOT_NAME).propagate is False


def test_tracebacks_reach_the_file(log_file):
    try:
        raise ValueError("管道断了")
    except ValueError:
        applog.get_logger("probe").exception("发送失败")
    text = log_file()
    assert "发送失败" in text
    assert "Traceback" in text
    assert "ValueError: 管道断了" in text


def test_log_path_is_none_before_setup():
    applog._file_handler = None
    assert applog.log_path() is None


def test_the_log_file_carries_a_bom_for_notepad(tmp_path, log_file):
    """不带 BOM 的话，Windows 记事本把中文日志按本地代码页解释 —— 打开就是乱码。"""
    applog.get_logger("probe").info("中文日志一行")
    assert Path(log_file.path).read_bytes()[:3] == b"\xef\xbb\xbf"
    assert "中文日志一行" in Path(log_file.path).read_text(encoding="utf-8-sig")


def test_the_bom_is_written_only_once_across_sessions(tmp_path):
    """追加模式下每次启动都插一个 BOM 的话，日志中间会出现乱码字符。"""
    import logging as _logging
    applog._file_handler = None
    root = _logging.getLogger(applog.ROOT_NAME)
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    path = None
    for session in range(3):
        applog._file_handler = None
        path = applog.setup(str(tmp_path / "logs"))
        applog.get_logger("probe").info("会话 %d", session)
        for h in list(_logging.getLogger(applog.ROOT_NAME).handlers):
            _logging.getLogger(applog.ROOT_NAME).removeHandler(h)
            h.close()
    applog._file_handler = None
    assert Path(path).read_bytes().count(b"\xef\xbb\xbf") == 1
