# -*- coding: utf-8 -*-
"""输入后端：把"要做什么"和"怎么做"分开。

Option 原来直接说键盘鼠标的话 —— start_battle 就是"按住 W + 中键"。手柄那边
不是同一套动作，所以支持手柄不是"换个通道"那么简单：得先有一套**意图**词汇，
键鼠和手柄各自去实现它。

词汇很小，因为真正需要的动作就这几个：

    hold_move / release_move      往前走
    battle_press / battle_release 开打（键鼠是中键，手柄是一个按钮）
    again                         再来一次
    confirm                       确认 / 推进菜单
    release_all                   全部松开

**release_all 是硬要求，不是补充。** start_battle 会把 W 按住不放；换后端时
如果不先松开，松开的那一下会发给**另一个**后端，W 就永远按着了 —— 而这正是
"支持随时切换"这件事自己带出来的新故障。

手柄和键鼠还有一个形状上的差别：XUSB_REPORT 是一份**完整状态快照**，不是事件。
按住摇杆的同时按一个键，得把两者合成一份报告重新发一次。所以 PadBackend 必须
自己记住"现在按着什么"，而 KmbBackend 不用。
"""

from applog import get_logger

log = get_logger(__name__)

# 手柄默认映射。**这些是未经验证的猜测** —— 没人在 Relink 里核对过，
# TESTING.md 有一条专门去确认它。放在 config 里就是为了核对完能直接改。
DEFAULT_PAD_MAPPING = {
    "battle": "right_thumb",   # 键鼠那边是中键；手柄上常见的对应是右摇杆按下
    "again": "y",
    "confirm": "a",
}


class InputBackend:
    """所有后端的共同词汇。方法默认什么都不做，子类挑需要的实现。"""

    name = "?"

    def is_ready(self):
        return False

    def hold_move(self):
        pass

    def release_move(self):
        pass

    def battle_press(self):
        pass

    def battle_release(self):
        pass

    def again(self):
        pass

    def confirm(self):
        pass

    def release_all(self):
        """把一切松开。切换后端、出错、退出时都要调用，必须能重复调用。"""


class KmbBackend(InputBackend):
    """键盘 + 鼠标。底下是 WindowInput，注入或兼容模式由它自己管。

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
        # 注入还是兼容是 WindowInput 的状态，不是另一个后端 —— 它换模式的时候
        # 动作词汇一个字都不变。
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


class PadBackend(InputBackend):
    """虚拟手柄（ViGEm）。

    XUSB_REPORT 是全量快照，所以这里必须自己记住按着什么，每次改动都重新合成
    一整份报告发出去。漏掉这一步的表现是"按了新键，旧键就松了"。
    """

    name = "pad"

    def __init__(self, pad, vigem_module, mapping=None, stick_max=None):
        self._pad = pad
        self._vigem = vigem_module
        self._mapping = dict(DEFAULT_PAD_MAPPING)
        if mapping:
            self._mapping.update(mapping)
        self._stick_max = stick_max if stick_max is not None else vigem_module.STICK_MAX
        self._buttons = 0
        self._move = False
        self._warned = set()

    @property
    def pad(self):
        """底下那个虚拟手柄对象。Option 用它判断要不要重建后端。"""
        return self._pad

    def is_ready(self):
        return self._pad is not None

    def _mask(self, action):
        mask = self._vigem.button_mask(self._mapping.get(action, ""))
        if not mask and action not in self._warned:
            # 名字拼错不会报错，只会静悄悄地什么都不按 —— 要说。
            #
            # 但每个动作只说一次：一次点击会走 _hold + _drop 两趟，循环里跑起来
            # 就是每秒几十条同样的告警，把真正要看的东西冲掉。而映射配错恰恰是
            # G1 最可能遇到的情况 —— 那时候日志正需要是能读的。
            self._warned.add(action)
            log.warning("手柄映射 %s=%r 不认识，这个动作发不出去（同样的问题不再重复告警）",
                        action, self._mapping.get(action))
        return mask

    def _push(self):
        """把当前状态合成一份报告发出去。"""
        if not self._pad:
            return False
        report = self._vigem.XUSB_REPORT(
            wButtons=self._buttons,
            sThumbLY=self._stick_max if self._move else 0,
        )
        try:
            self._pad.send(report)
            return True
        except Exception:
            log.warning("虚拟手柄发送失败，手柄可能已经掉了", exc_info=True)
            return False

    def hold_move(self):
        self._move = True
        self._push()

    def release_move(self):
        self._move = False
        self._push()

    def _hold(self, action):
        mask = self._mask(action)
        if mask:
            self._buttons |= mask
            self._push()

    def _drop(self, action):
        mask = self._mask(action)
        if mask:
            self._buttons &= ~mask
            self._push()

    def battle_press(self):
        self._hold("battle")

    def battle_release(self):
        self._drop("battle")

    def _tap(self, action):
        self._hold(action)
        self._drop(action)

    def again(self):
        self._tap("again")

    def confirm(self):
        self._tap("confirm")

    def release_all(self):
        self._buttons = 0
        self._move = False
        self._push()


class NullBackend(InputBackend):
    """什么都不做，但**说出来**。

    空跑模式和"当前没有可用后端"都走这里。它存在的意义是：这两种情况下调用方
    拿到的仍然是一个正常对象，而不是 None —— 少一处 `if backend is not None`，
    就少一处忘了写的地方。
    """

    name = "none"

    def __init__(self, reason="没有可用的输入后端"):
        self._reason = reason
        self._said = False

    @property
    def reason(self):
        return self._reason

    def is_ready(self):
        return False

    def _note(self, what):
        if not self._said:
            self._said = True
            log.warning("%s：动作 %s 及之后的都不会发出去", self._reason, what)

    def hold_move(self):
        self._note("hold_move")

    def release_move(self):
        self._note("release_move")

    def battle_press(self):
        self._note("battle_press")

    def battle_release(self):
        self._note("battle_release")

    def again(self):
        self._note("again")

    def confirm(self):
        self._note("confirm")
