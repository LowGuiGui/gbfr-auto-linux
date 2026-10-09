# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Linux 键鼠动作词汇：前进、开打、再战、确认、全部松开。

只保留当前循环调用的键鼠实现；目标检查、空跑与底层按键状态由 XTestInput 管理。
出错、暂停和退出都必须调用 release_all，且重复调用不能再发按下事件。
"""

from applog import get_logger

log = get_logger(__name__)


class KmbBackend:
    """Linux 键盘 + 鼠标动作。底下的 XTestInput 负责发送与空跑。

    中键要坐标，而坐标必须**每次现取** —— 窗口可能刚被拖过。centre_fn 返回
    None 表示这一次取不到几何，那就跳过鼠标事件（战斗照跑，中键不发），这是
    #46 已经定下的行为。
    """

    def __init__(self, window_input, keys, centre_fn):
        self._wi = window_input
        self._keys = keys
        self._centre = centre_fn
        self._move_held = False
        self._battle_held = False

    @property
    def name(self):
        return f"kmb/{self._wi.mode}"

    def is_ready(self):
        return self._wi.is_ready()

    def hold_move(self):
        self._wi.key_press(self._keys["move"])
        self._move_held = True

    def release_move(self):
        self._wi.key_release(self._keys["move"])
        self._move_held = False

    def battle_press(self):
        centre = self._centre()
        if centre is None:
            return
        self._wi.mouse_press(centre[0], centre[1], "middle")
        self._battle_held = True

    def battle_release(self):
        # 没按下去就没有要松的。少了这一条，几何读不到时 end_battle 会凭空发一个
        # 中键抬起 —— 一个从来没按下过的键。
        if not self._battle_held:
            return
        centre = self._centre()
        if centre is None:
            # 按下去了却松不开，比按不下去严重得多：中键会一直按着。位置取不到
            # 也要发出去，用 (0,0) 也比不发强。
            log.warning("取窗口几何失败，中键改用 (0,0) 松开 —— 按着不放更糟")
            centre = (0, 0)
        self._wi.mouse_release(centre[0], centre[1], "middle")
        self._battle_held = False

    def again(self):
        self._wi.key_tap(self._keys["again"])

    def confirm(self):
        self._wi.key_tap(self._keys["confirm"])

    def release_all(self):
        if self._move_held:
            self.release_move()
        if self._battle_held:
            self.battle_release()
