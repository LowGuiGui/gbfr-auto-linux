# -*- coding: utf-8 -*-
"""窗口几何 —— 窗口矩形、客户区、边框、中心点。

#46 需要它，#47 之后也要在它上面加 DPI。**这一版只做客户区/窗口区的换算**，
DPI 感知的切换是另一件事：那个改动会改变每一个既有坐标的含义，必须先在真机上
量过（`TESTING.md` D2），所以不夹带在这里。

—— 为什么要单独一个模块 ——

坐标在这个仓库里有三个空间，之前没有任何地方把它们讲清楚，于是 `_get_center()`
在窗口空间里算了个中心，却当成客户区中心用：

    屏幕坐标      GetWindowRect / ClientToScreen 返回的
    窗口相对坐标   相对 GetWindowRect 左上角。WindowInput._screen_pos 收的是这个
    客户区相对坐标 相对客户区左上角。模板匹配的结果活在这里

`WindowInput._screen_pos(x, y)` 是 `窗口原点 + (x, y)`，这个契约是既有的，本次
不动它 —— 所以下面给出的中心点也是**窗口相对**的。

纯函数部分不碰 Windows，Linux 上可完整测试；要真去读窗口的只有 `read()`。
"""


def border_offset(window_rect, client_origin):
    """客户区左上角相对窗口左上角的偏移。

    真机实测是 (11, 45)：左右各 11 的边框，上面 45 是标题栏加边框。
    """
    return (client_origin[0] - window_rect[0],
            client_origin[1] - window_rect[1])


def chrome(window_rect, client_size, client_origin):
    """四条边各占多少像素。

    存在的理由是 #46 里那个算错的结论：**左右对称，上下不对称。** 左右各 11，
    上 45 下 11。窗口中心和客户区中心因此 x 相同、y 差 17 —— 正好是上下差的
    一半。不把四条边分开列出来，这件事就看不见。
    """
    left, top = border_offset(window_rect, client_origin)
    window_w = window_rect[2] - window_rect[0]
    window_h = window_rect[3] - window_rect[1]
    return {
        "left": left,
        "top": top,
        "right": window_w - client_size[0] - left,
        "bottom": window_h - client_size[1] - top,
    }


def client_centre(window_rect, client_size, client_origin):
    """客户区中心，用**窗口相对**坐标表达。

    这是 `option._get_center()` 该返回的东西。旧的实现返回 `窗口尺寸 // 2`，
    也就是**窗口**的中心，于是中键落点比客户区中心高了 17 像素。
    """
    left, top = border_offset(window_rect, client_origin)
    return (left + client_size[0] // 2, top + client_size[1] // 2)


def window_centre(window_rect):
    """窗口中心，窗口相对。就是旧的实现。

    保留下来不是因为还要用，而是因为测试要拿它和 client_centre 对照 —— 那个差值
    正是 #46 的全部内容。
    """
    return ((window_rect[2] - window_rect[0]) // 2,
            (window_rect[3] - window_rect[1]) // 2)


def to_screen(window_rect, point):
    """窗口相对 -> 屏幕。和 WindowInput._screen_pos 是同一个换算。"""
    return (window_rect[0] + point[0], window_rect[1] + point[1])


def read(hwnd):
    """真去读一个窗口。返回 (window_rect, client_size, client_origin)。

    读不到返回 None —— 窗口可能刚好被关掉，调用方必须能应付。
    """
    try:
        import win32gui
    except ImportError:
        return None
    try:
        window_rect = win32gui.GetWindowRect(hwnd)
        client_rect = win32gui.GetClientRect(hwnd)
        client_origin = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        return None
    if not window_rect or not client_rect:
        return None
    client_size = (client_rect[2] - client_rect[0], client_rect[3] - client_rect[1])
    if client_size[0] <= 0 or client_size[1] <= 0:
        # 最小化的窗口客户区是 0x0。此时任何中心点都是假的，宁可说不知道。
        return None
    return (tuple(window_rect), client_size, tuple(client_origin))
