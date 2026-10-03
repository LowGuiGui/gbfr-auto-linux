# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""测试的公共设施。

核心模块都不碰平台，所以这里不再桩任何东西。Windows 那一层的桩（win32*、
pyautogui、pynput、ctypes.windll），以及 key、fake_root 两个 fixture，随应用层
暂存在历史里，见 docs/provenance/README.md。
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def log_file(tmp_path):
    """把日志装到 tmp_path，返回一个读取当前内容的可调用对象。"""
    import logging

    import applog

    # applog.setup 是幂等的，会记住第一次的 handler；测试之间要真正重置。
    applog._file_handler = None
    logger = logging.getLogger(applog.ROOT_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    path = applog.setup(str(tmp_path / "logs"))
    assert path is not None

    def read():
        for handler in logging.getLogger(applog.ROOT_NAME).handlers:
            handler.flush()
        return Path(path).read_text(encoding="utf-8")

    read.path = path
    yield read

    for handler in list(logging.getLogger(applog.ROOT_NAME).handlers):
        logging.getLogger(applog.ROOT_NAME).removeHandler(handler)
        handler.close()
    applog._file_handler = None
