# -*- coding: utf-8 -*-
"""运行期状态与调和。

这个模块存在的理由：需要的东西不是一个功能，是一条**性质** —— 世界变了，程序
要自己跟上，而不是坏掉或者悄悄地什么都不做。会变的东西比想象的多：

  窗口   被拖动、改大小、换窗口模式、拖到另一块 DPI 不同的屏幕、最小化
  进程   游戏重启（hwnd 和 pid 一起变，DLL 也没了）
  通道   注入管道断掉、ViGEmBus 复位、虚拟手柄掉线
  人     插上/拔掉实体手柄、手动暂停游戏再继续、自己去点别的窗口
  我们   伪装忘了关、按键卡在按下状态、以为在打其实没在打

做法是**调和**而不是"启动时配置一次"：每隔一段时间看一眼现在真实是什么样，
和期望比对，然后把差的补上。轮询而不是 Win32 事件钩子，理由有三条：在 Linux
上测得了，不需要消息循环，而且最要紧的几种故障（管道死了、手柄没了）本来就
只能靠轮询发现。

decide() 是**纯函数**：观测进去，决定出来，不碰任何 Windows API。这样这里的
规则可以在 Linux 上完整测试，而平台相关的部分被压到 observe() 那一层薄壳里。
"""

from applog import get_logger

log = get_logger(__name__)

# 人放下手柄之后，等多久再把自动化接回去。太短会在两次输入的间隙抢回控制权。
YIELD_RESUME_S = 3.0


class Observation:
    """某一刻世界的样子。全是普通值，好在测试里直接构造。"""

    def __init__(self, hwnd=None, hwnd_valid=False, geometry=None, focused=None,
                 kmb_ready=False, pad_ready=False, physical_pad_active=False,
                 spoof_on=False):
        self.hwnd = hwnd
        self.hwnd_valid = hwnd_valid
        self.geometry = geometry
        self.focused = focused
        self.kmb_ready = kmb_ready
        self.pad_ready = pad_ready
        self.physical_pad_active = physical_pad_active
        self.spoof_on = spoof_on

    def __repr__(self):
        return (f"Observation(hwnd={self.hwnd} valid={self.hwnd_valid} "
                f"kmb={self.kmb_ready} pad={self.pad_ready} "
                f"phys={self.physical_pad_active} spoof={self.spoof_on})")


class Decision:
    """该变成什么样，以及为此要做哪些动作。

    actions 是给调用方执行的清单，顺序有意义 —— release_all 永远排在换后端
    前面，否则按住的键会被留在旧后端上。
    """

    def __init__(self, backend, paused, reason, actions, degraded=False):
        self.backend = backend          # "pad" / "kmb" / "none"
        self.paused = paused            # 自动化是否应该停手
        self.reason = reason            # 一句话，给人看的
        self.actions = actions          # ["release_all", "reacquire_window", ...]
        self.degraded = degraded        # 是不是退而求其次的选择

    def __repr__(self):
        return (f"Decision(backend={self.backend} paused={self.paused} "
                f"degraded={self.degraded} actions={self.actions} "
                f"reason={self.reason!r})")

    def __eq__(self, other):
        return (isinstance(other, Decision)
                and (self.backend, self.paused, self.degraded, tuple(self.actions))
                == (other.backend, other.paused, other.degraded, tuple(other.actions)))


def _available(obs):
    return {"pad": obs.pad_ready, "kmb": obs.kmb_ready}


def decide(prefer, obs, current_backend=None, current_hwnd=None):
    """纯函数。观测 + 偏好 -> 该怎么办。

    prefer 是人在界面上选的模式，**不是**建议：只有当它真的不可用时才退让，
    而且退让要说出来，可用了还要自己回去。悄悄换模式和悄悄不动一样糟。
    """
    actions = []

    # 1. 窗口没了，什么都别谈。游戏重启时 hwnd 会变，得重新找。
    if not obs.hwnd_valid:
        return Decision("none", True, "游戏窗口不在了，正在重新查找",
                        ["release_all", "reacquire_window", "spoof_off"])

    # 2. hwnd 变了 = 换了一个窗口（多半是游戏重启过）。旧后端上按着的键属于一个
    #    已经不存在的窗口，先松开再说。注入连接也必须重建。
    if current_hwnd is not None and obs.hwnd != current_hwnd:
        actions += ["release_all", "reconnect_transport", "spoof_off"]

    # 3. 人在用自己的手柄 —— 让开。不让的话两个手柄会一起塞进同一个角色。
    if obs.physical_pad_active:
        return Decision("none", True, "检测到实体手柄在动，自动化让开",
                        actions + ["release_all"])

    avail = _available(obs)
    backend, degraded, reason = _pick(prefer, avail)

    # 4. 换后端之前一定要先全部松开 —— 否则 hold_move 按下的 W 会留在旧后端，
    #    再也没人去松它。
    if current_backend is not None and backend != current_backend:
        if "release_all" not in actions:
            actions.insert(0, "release_all")

    # 5. 伪装只在手柄模式下开。键鼠模式下伪装会把光标锁死在游戏窗口中央
    #    （2026-08-26 t_kmb 实测），那会让整台机器没法用。
    if obs.spoof_on and backend != "pad":
        actions.append("spoof_off")

    paused = backend == "none"
    return Decision(backend, paused, reason, actions, degraded)


def _pick(prefer, avail):
    """选后端。先按人的意思，不行才退让，并且说清楚为什么。"""
    order = ["pad", "kmb"] if prefer == "pad" else ["kmb", "pad"]

    if avail.get(prefer):
        return prefer, False, f"{prefer} 模式正常"

    for candidate in order:
        if avail.get(candidate):
            return (candidate, True,
                    f"{prefer} 模式当前不可用，暂时退到 {candidate}；可用了会自己回去")

    return "none", True, "键鼠和手柄都不可用，自动化暂停"


class PhysicalPadWatch:
    """认出"人正在用自己的手柄"。

    麻烦在于我们自己的虚拟手柄也占着一个 XInput 槽位，光看"有没有手柄在动"会
    把自己的输入当成人的。所以在**连接虚拟手柄之前**先记下已占用的槽位，之后
    新出现的那个就是我们自己的；其余一律算实体。这样后插的手柄也能认对。

    时间从外面传进来，测试里就不用真的等。
    """

    def __init__(self, xinput_module, dll=None, resume_after=YIELD_RESUME_S):
        self._xi = xinput_module
        self._dll = dll
        self._ours = None
        self._slots_before = set()
        self._last_active = None
        self._resume_after = resume_after

    def note_slots_before_connect(self):
        """接虚拟手柄之前调用一次。"""
        self._slots_before = set(self._connected())
        return self._slots_before

    def note_slots_after_connect(self):
        """接完之后调用。多出来的那个槽位就是我们的。"""
        new = set(self._connected()) - self._slots_before
        # 正好多一个才敢认。多出两个说明同时插了别的东西，宁可不认 —— 认错了
        # 会把人的手柄当成自己的，然后永远不让开。
        self._ours = new.pop() if len(new) == 1 else None
        if self._ours is None:
            log.warning("认不出虚拟手柄占了哪个槽位（新增 %s），"
                        "实体手柄检测这次不可靠", sorted(new))
        return self._ours

    def _connected(self):
        if not self._dll:
            return []
        try:
            return list(self._xi.connected_slots(self._dll))
        except Exception:
            log.debug("读 XInput 槽位失败", exc_info=True)
            return []

    def active(self, now):
        """现在是不是有人在动实体手柄（含刚放下不久的宽限）。"""
        if self._physically_moving():
            self._last_active = now
            return True
        if self._last_active is None:
            return False
        # 放下之后给一段宽限：两次输入之间的空档不该被当成"人走了"。
        if now - self._last_active < self._resume_after:
            return True
        self._last_active = None
        return False

    def _physically_moving(self):
        if not self._dll:
            return False
        for slot in self._connected():
            if slot == self._ours:
                continue
            try:
                reading = self._xi.read(self._dll, slot)
            except Exception:
                log.debug("读 XInput 槽位 %s 失败", slot, exc_info=True)
                continue
            if reading is not None and not self._xi.is_neutral(reading):
                return True
        return False
