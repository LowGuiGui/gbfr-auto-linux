# -*- coding: utf-8 -*-
# 配置层。
#
# 在此之前每个可调数值都是散落在源码里的字面量：轮询间隔在 main.py，匹配阈值在
# opencv.py，按键在 option.py。接下来的每个功能都会再加一批旋钮（截图后端、缩放
# 范围、面板停靠边、输入后端、每关配置），继续堆字面量是最糟的选择。
#
# 只依赖标准库，且不碰 tkinter/win32 —— opencv.py 要读匹配阈值，而它是全仓库唯一
# 能在 Linux 上直接测的模块，不能被拖进平台依赖。
#
# 读取用 tomllib（3.11+ 标准库）。我们从不以程序方式写配置，只在文件不存在时把下
# 面那份带注释的默认模板整个写出去，之后完全交给用户手改。

import os
import tomllib

from applog import get_logger

log = get_logger(__name__)

CONFIG_FILENAME = "gbfr_auto.toml"

# 结构即校验：键必须存在于此，且类型必须与默认值一致，否则拒绝并回退。
DEFAULTS = {
    "loop": {
        "poll_interval_ms": 3000,
        "max_blind_taps": 5,
    },
    "detect": {
        "threshold": 0.8,
        # 每帧把每个模板的最高分记进日志。调阈值和缩放范围时必开；这是把
        # "它不工作"变成一份数据的唯一办法。
        "log_scores": False,
        # 认不出页面时把截图存下来。每个失败都会变成一张可以用来修模板的样本。
        "save_anomaly_frames": False,
        "anomaly_dir": "anomalies",
        "max_anomaly_frames": 50,
    },
    "input": {
        "mode": "fallback",
        # 空跑：照常识别、照常记录，但**不向游戏发送任何按键**。
        "dry_run": False,
    },
    "inject": {
        "watchdog_ms": 15000,
    },
    "keys": {
        "move": "w",
        "again": "3",
        "confirm": "a",
    },
    "log": {
        # 只有等级可配。轮转大小和份数不行 —— applog.setup() 必须在读配置之前
        # 跑完（读配置本身就要记日志），那时 handler 已经建好了。
        "level": "DEBUG",
    },
}

DEFAULT_TOML = """\
# gbfr_auto 配置文件。
#
# 删掉某一行就会用回默认值；删掉整个文件，下次启动会重新生成这份带注释的模板。
# 认不出的键会被忽略并在日志里报出来，不会让程序起不来。

[loop]
poll_interval_ms = 3000     # 每隔多久截一次图并判断页面
max_blind_taps   = 5        # 连续认不出页面时，最多盲按几次就停手并告警

[detect]
threshold           = 0.8   # 模板匹配得分阈值，0-1
log_scores          = false # 每帧记录每个模板的最高分（调参时打开）
save_anomaly_frames = false # 认不出页面时把截图存下来，用于事后修模板
anomaly_dir         = "anomalies"
max_anomaly_frames  = 50    # 存满就不再存，避免把磁盘塞爆

[input]
mode    = "fallback"        # "fallback"（抢焦点）或 "inject"（DLL 注入）
dry_run = false             # true = 照常识别与记录，但不向游戏发送任何按键

[inject]
watchdog_ms = 15000         # 启用注入模式的超时

[keys]
move    = "w"               # 战斗中按住的前进键
again   = "3"               # 结算页切换到"再战"
confirm = "a"               # 确认／推进流程

[log]
level = "DEBUG"             # DEBUG / INFO / WARNING
"""


class Config:
    """只读配置。用点号路径取值：cfg.get("detect.threshold")。"""

    def __init__(self, values, path=None):
        self._values = values
        self.path = path

    def get(self, dotted, default=None):
        node = self._values
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not None:
                    return default
                raise KeyError(f"配置项不存在: {dotted}")
            node = node[part]
        return node

    def section(self, name):
        return dict(self._values.get(name, {}))

    def as_dict(self):
        return {k: dict(v) for k, v in self._values.items()}

    def __repr__(self):
        return f"Config(path={self.path!r})"


def _merged(user):
    """把用户配置盖在默认值上，逐键校验。

    未知键和类型不符的键都会被拒绝并记录 —— 静默忽略配置错误，就等于让人对着
    一个不生效的设置调半天。
    """
    result = {section: dict(items) for section, items in DEFAULTS.items()}

    for section, items in user.items():
        if section not in DEFAULTS:
            log.warning("配置中有未知的段落 [%s]，已忽略", section)
            continue
        if not isinstance(items, dict):
            log.warning("配置段落 [%s] 格式不对，已忽略", section)
            continue
        for key, value in items.items():
            if key not in DEFAULTS[section]:
                log.warning("配置中有未知的键 %s.%s，已忽略", section, key)
                continue
            expected = type(DEFAULTS[section][key])
            # bool 是 int 的子类，必须先挡掉，否则 true 会被当成合法的整数
            if isinstance(value, bool) != (expected is bool) or not isinstance(value, expected):
                log.warning(
                    "配置项 %s.%s 类型应为 %s，实际是 %s，已回退到默认值 %r",
                    section, key, expected.__name__, type(value).__name__,
                    DEFAULTS[section][key],
                )
                continue
            result[section][key] = value
    return result


def _write_default(path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_TOML)
        return True
    except OSError as e:
        log.warning("写入默认配置失败 %s: %s", path, e)
        return False


def load(directory):
    """读取 <directory>/gbfr_auto.toml，不存在就先写一份带注释的默认配置。

    任何失败都退回默认值并记录 —— 配置坏掉不该让程序起不来。
    """
    path = os.path.join(directory, CONFIG_FILENAME)

    if not os.path.exists(path):
        if _write_default(path):
            log.info("已生成默认配置文件: %s", path)
        return Config(_merged({}), path)

    try:
        with open(path, "rb") as f:
            user = tomllib.load(f)
    except OSError as e:
        log.warning("读取配置失败，使用默认值 %s: %s", path, e)
        return Config(_merged({}), path)
    except tomllib.TOMLDecodeError as e:
        log.warning("配置文件格式错误，使用默认值 %s: %s", path, e)
        return Config(_merged({}), path)

    return Config(_merged(user), path)
