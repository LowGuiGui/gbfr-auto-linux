# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""模板匹配，以及截图的空帧检查。

两件互不依赖的事：

    cv_best_match / cv_find_template   在截图里找一张模板，给出位置和得分
    is_blank_frame                     截图本身是不是坏的（全黑或纯色）

约定：
  - 截图是 RGB 顺序的 uint8 数组，PIL Image 也收。模板从文件读，读进来就转成
    RGB；两边通道顺序一致，分数才有意义。
  - 匹配在彩色图上做，用归一化相关系数（TM_CCOEFF_NORMED），得分落在 0..1，
    不同模板之间可以比。只匹配一个尺度：截图分辨率和截模板时不同就找不到
    （#12，tests/test_opencv.py 里有回归基线）。
  - 读不到模板必须留下痕迹（#4）：缺文件、空文件、解码失败分开记，匹配这一层
    再记一次拒绝。原来静默返回 None，结果每一页都被判成 UNKNOWN。
  - cv2 只用 13 个符号，清单钉在 tests/test_opencv.py 里（#9）。

空帧为什么要单独查：归一化相关的分母是方差，模板是纯色时 OpenCV 直接给满分。
全黑截图配上一张低纹理模板，得到的就是一次高置信度的误判，而不是"认不出"。
匹配这一层分辨不了，只能在截图进来时先验。
"""

import os
from pathlib import Path

import cv2
import numpy as np

from applog import get_logger
from config import DEFAULTS

log = get_logger(__name__)

# 不传阈值时用的默认值，和配置文件里 detect.threshold 的默认值同出一处。
DEFAULT_THRESHOLD = DEFAULTS["detect"]["threshold"]

# 空帧判据：每个颜色通道在整幅图上的标准差都不超过它。量纲是 0..255 的灰阶。
#
# 截图失败时整幅图是同一个颜色，标准差就是 0。平坦区域上下一个灰阶的随机起伏，
# 标准差约 0.8。真实画面通常有几十，哪怕几乎全黑、只有一小块暗淡的内容（tests 里
# 那块占 4% 面积、亮度 40 的方块）也有 7 以上。取 1：刚好盖过那点起伏，离真实内容
# 还很远。
BLANK_FRAME_STD = 1.0


def _brief(obj):
    """日志里怎么称呼一个输入：路径原样照抄，别的对象只报类型名。

    截图是几兆的数组，原样写进日志没有任何用处。
    """
    if isinstance(obj, (str, os.PathLike)):
        return os.fspath(obj)
    return type(obj).__name__


def _cv_read_image(path):
    """从文件读一张图，返回 RGB 的 uint8 数组；读不出来返回 None，并记下原因。

    先自己把字节读进来再交给 imdecode，不让 OpenCV 直接按路径读：那样缺文件、
    空文件和坏文件得到的都只是一个 None，日志里分不出是哪一种。
    """
    try:
        data = Path(path).read_bytes()
    except (OSError, ValueError) as exc:  # ValueError: 路径里有 NUL
        log.error("读取图片失败: %s (%s: %s)", path, type(exc).__name__, exc)
        return None
    if not data:
        log.error("读取图片失败: %s: 图片为空或不存在（文件是 0 字节）", path)
        return None
    bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        log.error("读取图片失败: %s: 解码失败（%d 字节，不是 OpenCV 认得的图片格式）",
                  path, len(data))
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _as_rgb(image):
    """把截图或模板统一成 (高, 宽, 3) 的 uint8 RGB 数组；用不了就返回 None。

    收三种输入：文件路径（当作模板读）、ndarray、PIL Image 这类能转成数组的对象。
    四通道的去掉 alpha。灰度图和其它形状不去猜，记一笔，返回 None。
    """
    if image is None:
        return None
    if isinstance(image, (str, os.PathLike)):
        return _cv_read_image(image)
    pixels = np.asarray(image)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        log.error("图像格式不支持: %s shape=%s dtype=%s",
                  _brief(image), pixels.shape, pixels.dtype)
        return None
    # 切掉 alpha 之后数组不再连续，OpenCV 要的是连续内存
    return np.ascontiguousarray(pixels[:, :, :3])


def cv_best_match(screen, template):
    """模板在截图里得分最高的位置，分数高低都交出来。

    返回 (x, y, w, h, score)：x, y 是命中区域的左上角，w, h 是模板的宽和高，
    score 是 0..1 的相关系数。输入用不了（读不到模板、截图为空、模板比截图还大）
    时返回 None。

    阈值留给调用方：调 detect.threshold 全靠这里交出来的分数。
    """
    screen_rgb = _as_rgb(screen)
    template_rgb = _as_rgb(template)
    if screen_rgb is None or template_rgb is None:
        log.error("模板匹配输入无效: screen=%s template=%s", _brief(screen), _brief(template))
        return None

    h, w = template_rgb.shape[:2]
    screen_h, screen_w = screen_rgb.shape[:2]
    if h > screen_h or w > screen_w:
        log.error("模板 %s (%dx%d) 比截图 (%dx%d) 还大，没法匹配",
                  _brief(template), w, h, screen_w, screen_h)
        return None

    scores = cv2.matchTemplate(screen_rgb, template_rgb, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return int(x), int(y), int(w), int(h), float(best)


def cv_find_template(screen, template, threshold=DEFAULT_THRESHOLD):
    """和 cv_best_match 一样，只是得分低于 threshold 时返回 None。"""
    match = cv_best_match(screen, template)
    if match is None or match[4] < threshold:
        return None
    return match


def is_blank_frame(frame, tolerance=BLANK_FRAME_STD):
    """截图是不是坏的：None、空数组，或者每个通道几乎都只有一个值。

    纯色和全黑一样算坏：截图后端失败时交回来的底色不一定是黑的。判据按通道算
    标准差，所以 (17, 42, 99) 这样的纯色也会被拦下，而一小块暗淡的真实内容不会。
    """
    if frame is None:
        return True
    pixels = np.asarray(frame)
    if pixels.size == 0:
        return True
    if pixels.ndim == 3:
        spread = pixels.reshape(-1, pixels.shape[2]).std(axis=0).max()
    else:
        spread = pixels.std()
    return bool(spread <= tolerance)
