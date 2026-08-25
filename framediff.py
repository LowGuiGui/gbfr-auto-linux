# -*- coding: utf-8 -*-
"""帧间差分 —— 回答"游戏失焦以后到底是停了，还是只是不理输入"。

#45 的探测缺的最后一块。第 5 段的手柄测试问的是"角色动了吗"，靠人眼看，而人眼
分不清这两种情况：

    整个游戏被暂停了        -> 画面完全静止。这是 1.1 加的防挂机暂停。
    游戏还在跑，只是不收输入 -> 画面照常动，角色不动。

两者的修法完全不同，所以必须分开。方法是连续截图算相邻帧的差，分三段比：

    1. 聚焦 + 不给输入   基准。这一段没有变化，说明画面本来就是静的，测试作废。
    2. 失焦 + 不给输入   和 1 比。掉到接近 0 -> 游戏停了。
    3. 失焦 + 推着摇杆   和 2 比。明显更大 -> 输入进去了。

阈值都是猜的，所以**原始数字必须原样打出来**，判定只作参考 —— 和第 5 段"None of
the above is proof"是同一条规矩。

只依赖 numpy，不碰 Windows，Linux 上可完整测试。
"""

import numpy as np

# 差分的量纲是 0..255 的平均绝对差。下面的常数都是估的，真机数据回来要重校。
#
# 画面完全静止时的地板。低于它就说明这一段根本没有动静 —— 站在菜单里测，三段都
# 是 0，什么结论也得不出来，必须当场说清楚而不是硬给个判定。
STATIC_FLOOR = 0.5

# 失焦后的动静掉到聚焦时的多少以下算"停了"
FROZEN_RATIO = 0.10
# 高于多少算"照常在跑"。中间那段是后台降帧，很多游戏都会做，不是暂停。
RUNNING_RATIO = 0.50

# 推摇杆后要比不推时大多少，才算输入真的进去了
INPUT_RATIO = 1.5
INPUT_MARGIN = 1.0


def to_gray(frame):
    """PIL Image 或 ndarray -> 灰度 float32 数组。

    转灰度不是为了好看：彩色三通道算差分会把同一处变化数三遍，而且对色彩抖动
    （压缩、抖动、HDR 色调映射）更敏感。亮度一路更稳。
    """
    array = np.asarray(frame, dtype=np.float32)
    if array.ndim == 2:
        return array
    if array.ndim != 3:
        raise ValueError(f"expected a 2D or 3D frame, got shape {array.shape}")
    # 只取前三个通道：RGBA 的 alpha 对画面动静没有意义
    rgb = array[:, :, :3]
    return rgb.mean(axis=2)


def frame_delta(first, second):
    """两帧之间的平均绝对差，0..255。

    尺寸对不上返回 None 而不是抛异常 —— 窗口可能在两次截图之间被拖动或改了大小，
    那是真实会发生的事，不该让整段测试崩掉。
    """
    a = to_gray(first)
    b = to_gray(second)
    if a.shape != b.shape:
        return None
    return float(np.abs(a - b).mean())


def summarize(deltas):
    """一串差分值 -> 统计量。丢掉 None（尺寸变了的那几帧）。"""
    values = [d for d in deltas if d is not None]
    if not values:
        return {"count": 0, "mean": 0.0, "max": 0.0, "median": 0.0, "dropped": len(deltas)}
    array = np.array(values, dtype=np.float64)
    return {
        "count": len(values),
        "mean": float(array.mean()),
        "max": float(array.max()),
        "median": float(np.median(array)),
        "dropped": len(deltas) - len(values),
    }


def motion_verdict(focused_idle, unfocused_idle):
    """失焦以后画面还动不动。返回 (代号, 一行 ASCII 说明)。"""
    if focused_idle["count"] == 0 or unfocused_idle["count"] == 0:
        return ("no-data", "Not enough frames were captured to compare anything.")

    if focused_idle["mean"] < STATIC_FLOOR:
        return ("static-scene",
                "Nothing was moving even while focused, so there is no signal to "
                "lose. Re-run inside a quest with visible motion -- a menu or a "
                "paused screen cannot answer this.")

    ratio = unfocused_idle["mean"] / focused_idle["mean"]
    if ratio < FROZEN_RATIO:
        return ("frozen",
                f"Motion fell to {ratio:.1%} of the focused baseline: the game "
                "STOPS when it loses focus. That is the 1.1 anti-AFK pause, and no "
                "input fix alone will help -- the simulation itself is not running.")
    if ratio < RUNNING_RATIO:
        return ("reduced",
                f"Motion fell to {ratio:.1%} of the focused baseline. Not a full "
                "stop -- this looks like a background frame-rate cap, which is "
                "common and mostly harmless for us.")
    return ("running",
            f"Motion held at {ratio:.1%} of the focused baseline: the game keeps "
            "running while unfocused. So the problem is input delivery, not a pause.")


def input_verdict(unfocused_idle, unfocused_input, motion_code):
    """失焦时推摇杆有没有反应。返回 (代号, 一行 ASCII 说明)。"""
    if motion_code in ("frozen", "static-scene", "no-data"):
        return ("moot",
                "Cannot be judged from this run: with the picture frozen or static "
                "there is nothing for input to visibly change.")
    if unfocused_input["count"] == 0:
        return ("no-data", "No frames were captured while the stick was held.")

    idle = unfocused_idle["mean"]
    active = unfocused_input["mean"]
    threshold = max(idle * INPUT_RATIO, idle + INPUT_MARGIN)
    if active >= threshold:
        return ("reaching",
                f"Motion rose from {idle:.2f} to {active:.2f} when the stick went "
                "down: input IS reaching the game while it is unfocused.")
    return ("ignored",
            f"Motion stayed at {active:.2f} against {idle:.2f} idle: the game is "
            "running but NOT acting on the pad while unfocused.")
