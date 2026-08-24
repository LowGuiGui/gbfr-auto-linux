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

import codecs
import locale
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
# gbfr_auto configuration / gbfr_auto 配置文件
#
# Save this file as UTF-8. Notepad's "ANSI" will break the comments below.
# Delete any line to fall back to its default; delete the whole file and it is
# regenerated on next start. Unknown keys are ignored and reported in the log.
#
# 存盘请选 UTF-8，记事本的「ANSI」会让下面的注释读不出来。
# 删掉某一行就会用回默认值；删掉整个文件，下次启动会重新生成这份模板。
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
        # utf-8-sig：这个文件是给人用记事本改的。不带 BOM 的话记事本按本地代码页
        # 解释，中文注释全是乱码；而且它另存时多半会补上 BOM，于是下次就读不了。
        # 一开始就带上，来回都稳。
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(DEFAULT_TOML)
        return True
    except OSError as e:
        log.warning("写入默认配置失败 %s: %s", path, e)
        return False


def _decode(raw):
    """把配置文件的字节解成文本，容忍记事本会做的两件事。

    返回 (文本, 提示) —— 提示不为空时说明文件不是标准的 UTF-8，应当告诉用户。

    两个坑都很常见：
      1. 记事本存 UTF-8 默认加 BOM，而 tomllib 不认 BOM，直接报语法错误；
      2. 记事本也可能按 ANSI（简中就是 GBK）存，那是 UnicodeDecodeError。
    两种情况原来都会退回默认值，于是用户改了半天配置一点不生效 —— 比报错更糟。
    """
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        pass

    fallback = locale.getpreferredencoding(False) or "cp1252"
    try:
        text = raw.decode(fallback)
    except (UnicodeDecodeError, LookupError):
        return None, f"配置文件既不是 UTF-8，也不是本地编码 {fallback}"
    return text, (
        f"配置文件不是 UTF-8（已按本地编码 {fallback} 读取）。"
        "请用记事本「另存为」并把编码选成 UTF-8，否则下次可能读不出来。"
    )


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
            raw = f.read()
    except OSError as e:
        log.warning("读取配置失败，使用默认值 %s: %s", path, e)
        return Config(_merged({}), path)

    text, note = _decode(raw)
    if text is None:
        log.warning("%s：%s。本次使用默认值。", path, note)
        return Config(_merged({}), path)
    if note:
        log.warning("%s：%s", path, note)

    try:
        user = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        log.warning("配置文件格式错误，使用默认值 %s: %s", path, e)
        return Config(_merged({}), path)

    return Config(_merged(user), path)
