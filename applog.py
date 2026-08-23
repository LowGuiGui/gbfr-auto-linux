# -*- coding: utf-8 -*-
# 集中式日志。
#
# 除 main.py 外的模块没有任何上报渠道 —— App.log() 是 App 的方法，直接写 Tk
# 文本框 —— 这正是下层模块只能静默吞掉异常的原因。这里给它们一个标准通道：
#
#     from applog import get_logger
#     log = get_logger(__name__)
#
# 只依赖标准库，不碰 tkinter：opencv.py 是全仓库唯一与平台无关、可在 Linux 上
# 直接测试的模块，把 tkinter 拖进它的依赖链会毁掉这一点。Tk 那一侧的 handler
# 定义在 main.py 里。

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler

# 所有模块的日志都挂在这个父 logger 下。模块本身是顶层模块（main、opencv、
# option ...），没有共同的包前缀，加上前缀才能一处配置、且不会连带捞进 PIL、
# comtypes 等第三方库的输出。
ROOT_NAME = "gbfr"

LOG_FILENAME = "gbfr_auto.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3
_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_file_handler = None


def get_logger(name):
    """取得模块 logger。name 传 __name__ 即可。"""
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def _writable_dir(*candidates):
    """返回第一个确实可写的目录，全都不可写则返回 None。

    首选 exe 旁边（用户找得到），装在 Program Files 之类只读位置时退回临时目录。
    """
    for d in candidates:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".gbfr_write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return d
        except OSError:
            continue
    return None


def setup(preferred_dir=None, level=logging.DEBUG):
    """装上轮转文件 handler，返回日志文件路径；无处可写时返回 None。

    重复调用是安全的（第二次直接返回既有路径），且必须在 run_as_admin() 之前
    调用 —— 提权失败是启动期最早、也最需要留痕的失败之一。
    """
    global _file_handler

    logger = logging.getLogger(ROOT_NAME)
    logger.setLevel(level)
    # 不向 root 冒泡：没装 handler 时 logging 会走 lastResort 打到 stderr，
    # 而 --windowed 打包后根本没有 stderr。
    logger.propagate = False

    if _file_handler is not None:
        return getattr(_file_handler, "baseFilename", None)

    log_dir = _writable_dir(preferred_dir, tempfile.gettempdir())
    if log_dir is None:
        return None

    path = os.path.join(log_dir, LOG_FILENAME)
    try:
        handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None

    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    handler.setLevel(level)
    logger.addHandler(handler)
    _file_handler = handler
    return path


def add_handler(handler):
    """挂一个额外的 handler（main.py 用它接上 Tk 日志框）。"""
    logging.getLogger(ROOT_NAME).addHandler(handler)


def log_path():
    """当前日志文件路径；尚未 setup 或无处可写时返回 None。"""
    return getattr(_file_handler, "baseFilename", None)


def set_level(level):
    """启动读到配置之后再调整等级。

    setup() 必须先跑（读配置的过程本身就要记日志），所以等级只能事后设。
    """
    logger = logging.getLogger(ROOT_NAME)
    resolved = logging.getLevelNamesMapping().get(str(level).upper())
    if resolved is None:
        logger.warning("日志等级无法识别: %r，保持 %s", level, logging.getLevelName(logger.level))
        return False
    logger.setLevel(resolved)
    if _file_handler is not None:
        _file_handler.setLevel(resolved)
    return True
