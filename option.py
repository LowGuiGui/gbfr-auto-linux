import time
import tkinter as tk

import backend as backend_mod
import geometry
import supervisor
from applog import get_logger
from window_input import WindowInput

log = get_logger(__name__)

MODE_KMB = "kmb"
MODE_PAD = "pad"


class Option:
    """动作层。

    对外的词汇是**意图**（开打、再来一次、确认），不是按键 —— 键鼠和手柄对同一个
    意图做的事不一样，见 backend.py。

    这里还负责调和：每隔一段时间看一眼世界现在是什么样（窗口还在吗、管道还通吗、
    人是不是拿起了手柄），和期望比对，把差的补上。规则本身在 supervisor.decide
    里，是纯函数；这里只做观测和执行。
    """

    def __init__(self, root: tk.Tk, keys=None, dry_run=False, pad_mapping=None,
                 prefer=MODE_KMB, clock=time.monotonic):
        self.root = root
        self._is_battle_ing = False
        self._wi = WindowInput()
        self._clock = clock
        # 空跑：照常识别、照常记录，但一个按键都不发出去。调模板和看流程时用，
        # 免得对着游戏乱按。
        self._dry_run = bool(dry_run)
        if self._dry_run:
            log.warning("空跑模式：不会向游戏发送任何输入")
        # 按键原本是散在各方法里的字面量。传 None 保留原值，方便单独构造。
        keys = keys or {}
        self._keys = {
            "move": keys.get("move", "w"),
            "again": keys.get("again", "3"),
            "confirm": keys.get("confirm", "a"),
        }

        self._pad_mapping = pad_mapping
        self._pad = None
        self._padbe = None          # PadBackend，跟着 self._pad 一起建/拆
        self._known_hwnd = None
        self._pad_watch = None
        # 偏好来自配置。默认成 kmb 而不管配置说什么，会让界面上选中的那一项和
        # 实际行为对不上 —— 界面说一套、程序做另一套。
        self._prefer = prefer if prefer in (MODE_KMB, MODE_PAD) else MODE_KMB
        self._title = None          # 记住是怎么找到窗口的，好在游戏重启后再找一次
        self._paused = False
        self._status = "未开始"
        self._degraded = False

        # 晚绑定：直接传 self._get_center 会把**此刻**这个绑定方法钉死，之后
        # 再替换它（测试里替、子类里覆盖）都不会生效 —— 而后端拿到的仍是旧的。
        self._kmb = backend_mod.KmbBackend(
            self._wi, self._keys, lambda: self._get_center())
        self._backend = self._kmb

    # --- 目标与模式 ---------------------------------------------------------

    def set_target(self, hwnd_or_title):
        if isinstance(hwnd_or_title, str):
            self._title = hwnd_or_title
        self._wi.set_target(hwnd_or_title)

    def set_preferred_mode(self, mode):
        """人在界面上选的模式。只有真的用不了才会退让，而且会自己回来。"""
        if mode not in (MODE_KMB, MODE_PAD):
            raise ValueError(f"未知输入模式: {mode!r}")
        self._prefer = mode

    @property
    def preferred_mode(self):
        return self._prefer

    @property
    def status(self):
        return self._status

    @property
    def paused(self):
        """自动化是否该停手（人在用手柄、窗口没了、没有可用后端）。"""
        return self._paused

    @property
    def degraded(self):
        return self._degraded

    @property
    def backend_name(self):
        return self._backend.name

    def is_ready(self):
        return self._backend.is_ready() and not self._paused

    def has_window(self):
        return self._wi.has_window()

    # --- 键鼠通道（注入 / 兼容）--------------------------------------------
    #
    # 这两个是 WindowInput 内部的事，不是另一个后端：换通道时动作词汇一个字都不变。

    def set_fallback_mode(self):
        self._wi.enable_fallback()

    def enable_inject_mode(self, dll_path=None, progress_cb=None):
        return self._wi.enable_inject(dll_path, progress_cb)

    def disable_inject_mode(self):
        self._wi.disable_inject()

    @property
    def input_mode(self):
        return self._wi.mode

    # --- 手柄 ---------------------------------------------------------------

    def enable_pad(self, vigem_module=None, xinput_module=None):
        """接上虚拟手柄。失败抛异常，调用方负责退回键鼠。

        接之前先记下已占用的 XInput 槽位 —— 接完之后多出来的那个就是我们自己的，
        这是"人在用实体手柄"能认对的前提。

        已经接上了就直接返回。Tk 的单选框**每次点击**都会触发 command，包括点
        已经选中的那一项；不挡住的话每点一次就多插一个虚拟手柄，旧的那个还留在
        系统里拔不掉，摇杆可能正推着。
        """
        if self._pad is not None:
            return True

        import vigem as _vigem
        vigem_module = vigem_module or _vigem
        if xinput_module is None:
            import xinput as xinput_module

        dll = None
        try:
            libs = xinput_module.available_libraries()
            if libs:
                dll = xinput_module.load_library(libs[0])
        except Exception:
            log.debug("加载 XInput 失败，实体手柄检测不可用", exc_info=True)

        watch = supervisor.PhysicalPadWatch(xinput_module, dll)
        watch.note_slots_before_connect()

        pad = vigem_module.VirtualGamepad()
        pad.connect()
        watch.note_slots_after_connect()

        self._pad = pad
        self._pad_watch = watch
        return True

    def disable_pad(self):
        if self._padbe is not None:
            try:
                self._padbe.release_all()
            except Exception:
                log.debug("拔手柄前松开输入失败", exc_info=True)
        self._padbe = None
        if self._pad is not None:
            try:
                self._pad.close()
            except Exception:
                log.debug("关闭虚拟手柄失败", exc_info=True)
        self._pad = None
        self._pad_watch = None

    # --- 调和 ---------------------------------------------------------------

    def observe(self):
        """看一眼世界现在的样子。只读，不改任何东西。"""
        hwnd = self._wi.hwnd
        return supervisor.Observation(
            hwnd=hwnd,
            hwnd_valid=self._wi.has_window(),
            focused=None,
            kmb_ready=self._wi.is_ready(),
            pad_ready=self._pad is not None,
            physical_pad_active=self._physical_pad_active(),
            spoof_on=self._wi.spoof_active,
        )

    def _physical_pad_active(self):
        if self._pad_watch is None:
            return False
        try:
            return self._pad_watch.active(self._clock())
        except Exception:
            log.debug("实体手柄检测失败", exc_info=True)
            return False

    def poll(self):
        """调和一次。返回这一轮的 Decision，方便界面显示和测试断言。"""
        current = self._backend.name.split("/")[0]
        obs = self.observe()
        decision = supervisor.decide(
            self._prefer, obs,
            current_backend=current if current in (MODE_KMB, MODE_PAD) else None,
            current_hwnd=self._known_hwnd,
        )
        self._apply(decision, obs)
        return decision

    def _apply(self, decision, obs):
        for action in decision.actions:
            self._do_action(action)

        want = decision.backend
        if want == MODE_PAD:
            self._backend = self._pad_backend()
        elif want == MODE_KMB:
            self._backend = self._kmb
        else:
            # 原因变了就重建，原因没变就留着。留着是为了"只喊一次"；重建是因为
            # 拿旧原因去解释新情况，比不解释更容易误导。
            if (not isinstance(self._backend, backend_mod.NullBackend)
                    or self._backend.reason != decision.reason):
                self._backend = backend_mod.NullBackend(decision.reason)

        self._known_hwnd = obs.hwnd
        self._paused = decision.paused
        self._degraded = decision.degraded
        if decision.reason != self._status:
            log.info("输入状态: %s (后端=%s)", decision.reason, self._backend.name)
        self._status = decision.reason

    def _pad_backend(self):
        """缓存的手柄后端。**不能**每次调和都新建 —— 新对象不知道现在按着什么，
        摇杆会在下一次 release_all 时被漏掉。手柄换了才重建。"""
        import vigem
        if self._padbe is None or self._padbe.pad is not self._pad:
            self._padbe = backend_mod.PadBackend(self._pad, vigem, self._pad_mapping)
        return self._padbe

    def _do_action(self, action):
        try:
            if action == "release_all":
                self._backend.release_all()
                # 松开之后"正在打"就不成立了。留着它，下一次 start_battle 会
                # 直接返回，而人看到的是"脚本不动了"。
                self._is_battle_ing = False
            elif action == "reacquire_window":
                if self._title:
                    self._wi.set_target(self._title)
            elif action == "reconnect_transport":
                self._wi.disable_inject()
            elif action == "spoof_off":
                self._wi.disable_focus_spoof()
        except Exception:
            log.warning("调和动作 %s 失败", action, exc_info=True)

    def panic(self):
        """一切松开、伪装关掉、暂停。

        给那个救命热键用的：键鼠模式下伪装会把光标锁在游戏窗口中央，那时候鼠标
        点不动任何东西，只剩键盘能用。
        """
        log.warning("紧急停止：松开全部输入并关闭焦点伪装")
        try:
            self._backend.release_all()
        except Exception:
            log.debug("紧急停止时松开输入失败", exc_info=True)
        try:
            self._wi.disable_focus_spoof()
        except Exception:
            log.debug("紧急停止时关闭伪装失败", exc_info=True)
        self._is_battle_ing = False
        self._paused = True
        self._status = "已紧急停止"

    # --- 动作 ---------------------------------------------------------------

    def _blocked(self, what):
        if self._dry_run:
            log.info("[空跑] 本应执行: %s", what)
            return True
        if self._paused:
            log.debug("已暂停，跳过: %s", what)
            return True
        return False

    def start_battle(self):
        if self._is_battle_ing:
            return
        self._is_battle_ing = True
        if self._blocked("start_battle 前进 + 开打"):
            return
        self._backend.hold_move()
        self._backend.battle_press()

    def end_battle(self):
        if not self._is_battle_ing:
            return
        self._is_battle_ing = False
        if self._blocked("end_battle 松开前进 + 开打"):
            return
        self._backend.release_move()
        self._backend.battle_release()

    def switch_again(self):
        if self._blocked("switch_again 再来一次"):
            return
        self._backend.again()

    def tap_confirm(self):
        """确认 / 推进菜单。

        原名是 tap_enter，但它从来没按过 Enter —— 上游把 key_tap("enter") 注释
        掉换成了 "a"，名字留在原地。这个名字在 _analyze_page 里是承重的：读派发
        逻辑的人会以为这里发的是 Enter，而它不是。#15。
        """
        if self._blocked("tap_confirm 确认"):
            return
        self._backend.confirm()

    def clear_all(self):
        self.end_battle()
        # end_battle 只在"正在打"的时候松手。崩溃或换后端之后状态可能已经不一致，
        # 所以这里再无条件兜一次底 —— 按着的键留在那里比多发一次松开糟得多。
        try:
            self._backend.release_all()
        except Exception:
            log.debug("清理时松开输入失败", exc_info=True)

    def _get_center(self):
        """客户区中心，用窗口相对坐标表达（WindowInput._screen_pos 收的就是这个）。

        每次都现取，不缓存：窗口随时可能被拖动、改大小、或者换窗口模式。

        原来这里取的是**窗口**中心。窗口化时标题栏只在上面，上下边框不对称
        （实测上 45 下 11），于是中键落点比客户区中心高 17 像素 —— 见 #46。
        左右边框是对称的，所以 x 一直是对的，只有 y 错。无边框模式下两者本来
        就重合，这个改动对它没有任何影响。
        """
        try:
            measured = geometry.read(self._wi.hwnd)
            if measured:
                return geometry.client_centre(*measured)
            log.warning("取窗口几何失败 (hwnd=%s)，本次跳过鼠标事件", self._wi.hwnd)
        except Exception:
            log.exception("计算窗口中心失败 (hwnd=%s)", getattr(self._wi, "hwnd", None))
        return None
