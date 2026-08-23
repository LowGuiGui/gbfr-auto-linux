# -*- coding: utf-8 -*-
"""让这个仓库能在 Linux 上被测试。

main.py / window_capture.py / window_input.py 在 **模块级** 就碰 Windows：
`ctypes.windll.user32`、`import win32gui`、`import pyautogui`。所以桩必须在它们
被 import 之前装好 —— conftest 是 pytest 最先加载的文件，正好。

这里只桩掉平台边界，不桩业务逻辑：测的仍是仓库里真实的函数。
opencv.py 和 applog.py 本来就与平台无关，完全按原样测。
"""

import ctypes
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# --- Windows 模块桩 ---------------------------------------------------------
# 必须在 import main 之前执行，因此写在模块级而不是 fixture 里。

_WIN32_MODULES = (
    "win32gui", "win32con", "win32api", "win32process", "win32event",
    "win32file", "win32pipe", "win32security", "win32ui", "pywintypes",
    "pyautogui",
)
for _name in _WIN32_MODULES:
    sys.modules.setdefault(_name, types.ModuleType(_name))


class _FakeKey:
    """pynput.keyboard.Key 的替身，只需要身份可比较。"""
    f1 = "<f1>"
    f2 = "<f2>"
    f3 = "<f3>"
    f4 = "<f4>"


if "pynput" not in sys.modules:
    _pynput = types.ModuleType("pynput")
    _kb = types.ModuleType("pynput.keyboard")
    _ms = types.ModuleType("pynput.mouse")
    _kb.Controller = object
    _kb.Key = _FakeKey
    _kb.Listener = object
    _ms.Controller = object
    _ms.Button = object
    _pynput.keyboard = _kb
    _pynput.mouse = _ms
    sys.modules.update({"pynput": _pynput, "pynput.keyboard": _kb, "pynput.mouse": _ms})

if not hasattr(ctypes, "windll"):
    ctypes.windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(), gdi32=types.SimpleNamespace(),
        shell32=types.SimpleNamespace(),
    )


@pytest.fixture
def key():
    """pynput 按键常量（真机上是 pynput 的，测试里是桩的）。"""
    from pynput import keyboard
    return keyboard.Key


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


class FakeRoot:
    """Tk root 的替身。

    after() 立刻同步执行回调，模拟主循环随后的调度；同时记录调用发生在哪个线程，
    好断言跨线程编组确实发生了。
    """

    def __init__(self, explode=False):
        self.calls = []
        self.explode = explode

    def after(self, delay, func, *args):
        import threading
        self.calls.append((threading.current_thread().name, delay, func, args))
        if self.explode:
            raise TypeError("模拟处理器故障")
        func(*args)


@pytest.fixture
def fake_root():
    return FakeRoot
