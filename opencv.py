# -*- coding: utf-8 -*-
# 模板匹配工具
# 用于在全图中查找模板图像的位置，返回匹配位置和匹配得分。

import cv2
import numpy as np
from PIL import Image

from applog import get_logger

log = get_logger(__name__)


BLANK_FRAME_STD = 1.0


def is_blank_frame(image, tolerance=BLANK_FRAME_STD):
    """判断一帧是不是死图（全黑或纯色）。

    为什么必须单独判：TM_CCOEFF_NORMED 的分母是两边的方差。模板和画面都平坦时
    分子分母同时趋零，OpenCV 返回 **1.0** —— 满分。而 PrintWindow 对 D3D 窗口
    经常返回全黑位图。两件事叠起来，得到的不是"认不出页面"，而是一次高置信度的
    **误判**。所以截图必须自己验，指望匹配环节兜底是错的。
    """
    if image is None:
        return True
    if isinstance(image, Image.Image):
        array = np.asarray(image.convert("L"))
    else:
        array = np.asarray(image)
        if array.ndim == 3:
            array = array.mean(axis=2)
    if array.size == 0:
        return True
    return float(array.std()) < tolerance


def _brief(value):
    """日志里只保留有用的部分：路径原样打印，图像对象只留类型名。"""
    return value if isinstance(value, str) else type(value).__name__


def _cv_read_image(path):
    # 用 np.fromfile + imdecode 而不是 cv2.imread：后者遇到中文路径会静默失败。
    #
    # 这个函数原本把所有异常吞成 None。模板读不到 -> 匹配返回 None -> 页面判成
    # UNKNOWN -> 机器人对着游戏一直敲键。整条链路上一个字都不会说，所以这里的
    # 每条失败路径都必须留下记录。
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception:
        log.exception("读取图片失败: %s", path)
        return None

    if data.size == 0:
        log.error("图片为空或不存在: %s", path)
        return None

    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        log.error("图片解码失败（文件损坏或格式不支持）: %s", path)
    return image


def cv_best_match(full_image: str | Image.Image | np.ndarray, template_image: str | Image.Image | np.ndarray)->tuple[int, int, int, int, float] | None:
    """
    使用OpenCV进行模板匹配，在全图中查找模板图像的位置
    
    参数:
        full_image: 待搜索的全图，可以是图像文件路径字符串、PIL Image对象或numpy数组
        template_image: 模板图像，可以是图像文件路径字符串、PIL Image对象或numpy数组
        threshold: 匹配得分阈值，默认0.8，得分大于等于此值则认为匹配成功
    
    返回:
        匹配成功时返回元组，包含匹配位置(x, y)、模板宽高(w, h)和匹配得分(score)；匹配失败返回None
    """
    # 初始化OpenCV格式的图像变量
    full_cv = None
    temp_cv = None

    # 处理全图输入，转换为OpenCV的BGR格式
    if isinstance(full_image, str):
        # 如果是文件路径，直接读取图像
        full_cv = _cv_read_image(full_image)
    elif isinstance(full_image, Image.Image):
        # 如果是PIL Image，先转为numpy数组再转换颜色空间为BGR
        full_cv = cv2.cvtColor(np.array(full_image), cv2.COLOR_RGB2BGR)
    elif isinstance(full_image, np.ndarray):
        # 如果是numpy数组，直接转换颜色空间为BGR（假设输入是RGB格式）
        full_cv = cv2.cvtColor(full_image, cv2.COLOR_RGB2BGR)

    # 处理模板图像输入，转换为OpenCV的BGR格式
    if isinstance(template_image, str):
        # 如果是文件路径，直接读取图像
        temp_cv = _cv_read_image(template_image)
    elif isinstance(template_image, Image.Image):
        # 如果是PIL Image，先转为numpy数组再转换颜色空间为BGR
        temp_cv = cv2.cvtColor(np.array(template_image), cv2.COLOR_RGB2BGR)
    elif isinstance(template_image, np.ndarray):
        # 如果是numpy数组，直接转换颜色空间为BGR（假设输入是RGB格式）
        temp_cv = cv2.cvtColor(template_image, cv2.COLOR_RGB2BGR)

    # 检查输入是否有效，若转换失败则返回None
    if full_cv is None or temp_cv is None:
        # 原本是 print()，而打包后是 --windowed，没有控制台，这行谁也看不见。
        # 实际最常见的原因也不是"类型不对"，而是模板文件读不到。
        log.error(
            "模板匹配输入无效（读取失败或类型不支持）: full_image=%s, template_image=%s",
            _brief(full_image), _brief(template_image),
        )
        return None

    # 获取模板图像的尺寸（OpenCV中图像shape为(高度, 宽度, 通道数)）
    h, w, _ = temp_cv.shape
    # 使用归一化相关系数法进行模板匹配
    match_result = cv2.matchTemplate(full_cv, temp_cv, cv2.TM_CCOEFF_NORMED)
    # 获取匹配结果中的极值和对应位置，max_loc为最佳匹配的左上角坐标
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(match_result)
    x, y = max_loc

    # 无论是否过阈值都把最佳匹配交出去 —— 调参时看不到分数就只能靠猜
    return (x, y, w, h, max_val)

def cv_match_template(full_image: str | Image.Image | np.ndarray, template_image: str | Image.Image | np.ndarray, threshold = 0.8, min_distance = None)->list[tuple[int, int, int, int, float]] | None:
    """
    使用OpenCV进行模板匹配，在全图中查找所有匹配的模板位置（支持多目标检测与去重）

    参数:
        full_image: 待搜索的全图，可以是图像文件路径字符串、PIL Image对象或numpy数组
        template_image: 模板图像，可以是图像文件路径字符串、PIL Image对象或numpy数组
        threshold: 匹配得分阈值，默认0.8，得分大于等于此值则认为匹配成功
        min_distance: 匹配点之间的最小距离，用于去重；为None时默认取模板宽高最小值的一半

    返回:
        匹配成功时返回列表，每个元素为包含匹配位置(x, y)、模板宽高(w, h)和匹配得分(score)的字典；无匹配时返回None
    """
    # 初始化OpenCV格式的图像变量
    full_cv = None
    temp_cv = None

    # 处理全图输入，转换为OpenCV的BGR格式
    if isinstance(full_image, str):
        # 如果是文件路径，直接读取图像
        full_cv = _cv_read_image(full_image)
    elif isinstance(full_image, Image.Image):
        # 如果是PIL Image，先转为numpy数组再转换颜色空间为BGR
        full_cv = cv2.cvtColor(np.array(full_image), cv2.COLOR_RGB2BGR)
    elif isinstance(full_image, np.ndarray):
        # 如果是numpy数组，直接转换颜色空间为BGR（假设输入是RGB格式）
        full_cv = cv2.cvtColor(full_image, cv2.COLOR_RGB2BGR)

    # 处理模板图像输入，转换为OpenCV的BGR格式
    if isinstance(template_image, str):
        # 如果是文件路径，直接读取图像
        temp_cv = _cv_read_image(template_image)
    elif isinstance(template_image, Image.Image):
        # 如果是PIL Image，先转为numpy数组再转换颜色空间为BGR
        temp_cv = cv2.cvtColor(np.array(template_image), cv2.COLOR_RGB2BGR)
    elif isinstance(template_image, np.ndarray):
        # 如果是numpy数组，直接转换颜色空间为BGR（假设输入是RGB格式）
        temp_cv = cv2.cvtColor(template_image, cv2.COLOR_RGB2BGR)

    # 检查输入是否有效，若转换失败则返回None
    if full_cv is None or temp_cv is None:
        # 原本是 print()，而打包后是 --windowed，没有控制台，这行谁也看不见。
        # 实际最常见的原因也不是"类型不对"，而是模板文件读不到。
        log.error(
            "模板匹配输入无效（读取失败或类型不支持）: full_image=%s, template_image=%s",
            _brief(full_image), _brief(template_image),
        )
        return None

    # 获取模板图像的尺寸（OpenCV中图像shape为(高度, 宽度, 通道数)）
    h, w, _ = temp_cv.shape
    # 使用归一化相关系数法进行模板匹配
    match_result = cv2.matchTemplate(full_cv, temp_cv, cv2.TM_CCOEFF_NORMED)

    # 找出所有匹配得分大于等于阈值的位置坐标
    locations = np.where(match_result >= threshold)
    points = []
    # 遍历所有匹配位置，收集(x, y, score)三元组
    for y, x in zip(locations[0], locations[1]):
        score = match_result[y, x]
        points.append((x, y, score))

    # 若没有找到任何匹配点，返回None（没匹配上是正常结果，记 debug 即可）
    if not points:
        log.debug("未找到匹配项（阈值: %s, 模板: %s）", threshold, _brief(template_image))
        return None

    # 未指定最小距离时，默认使用模板宽高最小值的一半作为去重距离
    if min_distance is None:
        min_distance = min(w, h) // 2

    # 通过非极大值抑制(NMS)去除距离过近的重复匹配点
    if min_distance > 0:
        # 先按得分从高到低排序，保证高得分的匹配点优先保留
        points = sorted(points, key=lambda x: x[2], reverse=True)
        filtered_points = []

        for point in points:
            x1, y1, score1 = point
            is_duplicate = False

            # 检查当前点与已保留点之间的距离
            for kept in filtered_points:
                x2, y2, _ = kept
                # 计算两点之间的欧氏距离
                distance = np.sqrt((x1 - x2)**2 + (y1 - y2)**2)
                # 若距离小于min_distance，则视为重复匹配，跳过
                if distance < min_distance:
                    is_duplicate = True
                    break

            # 非重复点则保留
            if not is_duplicate:
                filtered_points.append(point)

        points = filtered_points

    # 构建返回结果列表
    ret = []
    for idx, (x, y, score) in enumerate(points):
        ret.append((x, y, w, h, score))

    return ret


def cv_find_template(full_image, template_image, threshold=0.8):
    """cv_best_match 加一道阈值。历史签名，调用方遍布 main.py。"""
    result = cv_best_match(full_image, template_image)
    if result is None or result[4] < threshold:
        return None
    return result
