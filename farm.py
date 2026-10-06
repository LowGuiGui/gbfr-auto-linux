# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""战斗循环：每隔一段时间看一眼画面，认出是哪一页，做这一页该做的事。

这一层不碰平台。截图、输入和"世界现在是什么样"都从外面传进来：

    capture()   一帧 RGB 数组，已经是游戏自己的分辨率；截不到就是 None
    backend     backend.KmbBackend 这样的输入后端：hold_move、battle_press、again……；
                prefer 说它是哪一种（默认 "kmb"），观测里它不可用就停下
    window_input  能说出"现在按着什么"的那一层（held、skipped、release_all），比如
                xtest_input.XTestInput。必须有：开打按下去没有，只有它看得到
    observe()   一个 supervisor.Observation：游戏窗口还在不在、输入还能不能用
    controls    停止和暂停：stop_requested、paused 两个属性，pause(理由)，以及一个能被
                打断的 wait(秒)

Linux 上它们分别是 gamescope.ScreenshotCapture、套着 xtest_input.XTestInput 的
KmbBackend、命令行里拼出来的观测函数、control.py 的套接字。测试里全是替身，所以整个
循环不需要游戏，也不需要 X。

每一轮（loop.poll_interval_ms，默认 3 秒）：

  1. 看一眼。游戏窗口不在了、换了一个、输入用不了，就全部松开、停下。这一版不重新找
     窗口：游戏重启过，就该由人来重新开始。
  2. 截图。截不到或者是空白帧，这一轮跳过；连着三次就停下。
  3. 认页面。pages.resolve 按 pagetree.PAGE_RULES 的顺序试模板，得分到 detect.threshold
     才算命中。
  4. 计数。打过一场以后进到结算页，算完成一次；给了 repeats 就到数停下。
  5. 动作。战斗页开打（按住前进、按下开打），已经在打就什么都不按；别的页面先把战斗
     的那两个键松开，再做 pagetree.PAGE_ACTIONS 里写的动作。认不出的页面点一下确认，最多
     连着 loop.max_blind_taps 次，之后只记不按。
  6. 宁可停下也不瞎猜。同一页待得太久（战斗页超过 loop.max_battle_s 秒，别的页超过
     loop.max_page_s 秒）就停下；暂停的时间不算，接着跑时重新计时。输入那一层连着三次没
     按下去（焦点、指针或修饰键不对），就暂停，等人接回来。动作发出去之前再看一眼停止和
     暂停：截图和匹配要花一两秒，这期间来的命令不该再放过一个按键。

不管从哪条路出去（停止命令、到数、限制、出错），都先把按着的全部松开。空跑由输入那一层
管（XTestInput 的 live），这里照常做每一个决定、记每一行日志。
"""

import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import pages
import supervisor
from applog import get_logger
from opencv import cv_best_match, is_blank_frame
from pagetree import PAGE_ACTIONS, PAGE_NAME, PAGE_RULES, RESULT_PAGES, TEMPLATE_FILES

log = get_logger(__name__)

# 连着这么多轮截不到画面（或者截到空白帧）就停下：截图那一路坏了，接着跑只会对着坏画面
# 瞎按。
CAPTURE_FAILURES_TO_STOP = 3
# 输入那一层连着这么多次没按下去，或者开打连着这么多次没全按下去，就暂停。
SKIPS_TO_PAUSE = 3
# 缩放以后模板至少要这么宽、这么高（像素）。再小就认不出东西了：缩成一个像素的模板没有
# 方差，归一化相关系数对它给满分，第一条规则就会把每一帧都认成战斗页。
MIN_TEMPLATE_SIDE = 8


def load_templates(directory, scale=1.0, files=TEMPLATE_FILES):
    """读 pagetree 要用的模板，按 scale 缩放，返回 {模板名: RGB 数组}。

    读不到就抛异常，不带着缺的模板开跑：缺了哪一张，那一页就永远认不出来，循环只会一直
    盲按确认。scale 是 detect.template_scale：模板截图时的分辨率和现在游戏的不一样时用。
    它得是一个正的有限数，缩出来的每张模板至少 MIN_TEMPLATE_SIDE 见方、而且不是一整块纯色，
    否则也抛异常：那样的模板跟什么画面都"像"。
    """
    import cv2
    from PIL import Image

    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) \
            or scale <= 0:
        raise RuntimeError(f"detect.template_scale 得是一个正数，现在是 {scale!r}")
    templates = {}
    for filename in files:
        path = Path(directory, filename)
        try:
            with Image.open(path) as image:
                pixels = np.asarray(image.convert("RGB"))
        except OSError as exc:
            raise RuntimeError(f"读不到模板 {path}: {exc}") from exc
        if scale != 1.0:
            height, width = pixels.shape[:2]
            size = (round(width * scale), round(height * scale))
            if min(size) < MIN_TEMPLATE_SIDE:
                raise RuntimeError(f"detect.template_scale = {scale} 把模板 {filename} 缩成了 "
                                   f"{size[0]}x{size[1]}，太小了，认不出东西")
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            pixels = cv2.resize(pixels, size, interpolation=interpolation)
        if is_blank_frame(pixels):
            raise RuntimeError(f"模板 {filename} 是一整块纯色（缩放倍数 {scale}），拿它匹配，"
                               "什么画面都像")
        templates[Path(filename).stem] = pixels
    return templates


def save_png(frame, path):
    from PIL import Image

    Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(path)


class Farm:
    """一轮一轮地跑，直到该停下。run() 返回停下的理由，一句给人看的话。"""

    def __init__(self, capture, backend, observe, controls, templates, cfg, window_input,
                 repeats=None, clock=time.monotonic, anomaly_dir=None, save_frame=save_png,
                 battle_inputs=None, prefer="kmb"):
        """battle_inputs：开打以后 window_input.held 里应该有的那几样（比如 {"w", "middle"}）。
        全都按住了才算开打；只按住一部分（中键因为取不到窗口中心没发出去），下一轮接着按。
        不给就退一步，按住了任何东西都算。"""
        self._capture = capture
        self._backend = backend
        self._observe = observe
        self._controls = controls
        self._templates = templates
        self._cfg = cfg
        self._input = window_input
        self._repeats = repeats
        self._clock = clock
        self._anomaly_dir = Path(anomaly_dir) if anomaly_dir else None
        self._save_frame = save_frame
        self._battle_inputs = set(battle_inputs) if battle_inputs else None
        self._prefer = prefer
        self.battles = 0
        self.page = None
        self._page_since = None
        self._seen_battle = False
        self._battling = False
        self._unknown_streak = 0
        self._failed_starts = 0     # 开打连着几次没全按下去
        self._capture_failures = 0
        self._anomalies = None      # 第一次要存时，从目录里已有的文件数起
        self._window = None
        self._pause_noted = False

    # --- 外面调用的 -----------------------------------------------------------

    def run(self):
        interval = self._cfg.get("loop.poll_interval_ms") / 1000
        log.info("开始：每 %.1f 秒一轮%s", interval,
                 f"，完成 {self._repeats} 次就停" if self._repeats else "")
        reason = None
        try:
            while reason is None:
                started = self._clock()
                reason = self.tick()
                if reason is None:
                    self._controls.wait(max(0.0, interval - (self._clock() - started)))
        except Exception:
            log.exception("循环出错")
            reason = "出错了，见日志里的 traceback"
        finally:
            self._release("停下")
        log.info("停下：%s。这一趟完成了 %d 次战斗", reason, self.battles)
        return reason

    def tick(self):
        """跑一轮。该停下就返回理由，还要接着跑就返回 None。"""
        if self._controls.stop_requested:
            return "收到停止命令"
        if self._controls.paused:
            self._enter_pause()
            return None
        if self._pause_noted:
            log.info("接着跑")
            self._pause_noted = False
            self._failed_starts = 0
            # 暂停的时间不算在这一页上：从下一次认出页面重新计时
            self.page = None

        reason = self._check_world()
        if reason:
            return reason

        frame = self._capture()
        if self._controls.stop_requested:
            return "收到停止命令"
        # 截图期间来的暂停先办：不然一张截坏的图会让它记一次失败、按着的键留到下一轮，
        # 正好是第三次失败的话，循环就停了，而不是暂停
        if self._controls.paused:
            self._enter_pause()
            return None
        if frame is None or is_blank_frame(frame):
            self._capture_failures += 1
            log.warning("这一轮没有可用的画面（%s，连续第 %d 次）",
                        "截图失败" if frame is None else "空白帧", self._capture_failures)
            if self._capture_failures >= CAPTURE_FAILURES_TO_STOP:
                return f"连续 {self._capture_failures} 轮截不到可用的画面"
            return None
        self._capture_failures = 0

        started = self._clock()
        page, scores = self._recognise(frame)
        match_ms = round((self._clock() - started) * 1000)
        now = self._clock()
        if page != self.page:
            self.page, self._page_since = page, now

        if page == PAGE_NAME.BATTLE:
            self._seen_battle = True
        elif page in RESULT_PAGES and self._seen_battle:
            self._seen_battle = False
            self.battles += 1
            log.info("完成第 %d 次战斗", self.battles)
            if self._repeats is not None and self.battles >= self._repeats:
                return f"完成了 {self.battles} 次，到数了"

        limit = self._cfg.get("loop.max_battle_s" if page == PAGE_NAME.BATTLE
                              else "loop.max_page_s")
        stayed = now - self._page_since
        if stayed > limit:
            return f"在 {page} 页上待了 {stayed:.0f} 秒，超过了 {limit} 秒"

        # 截图和匹配要花时间，这期间来了停止或暂停，就不再按这一下
        if self._controls.stop_requested:
            return "收到停止命令"
        if self._controls.paused:
            self._enter_pause()
            return None
        action = self._act(page, frame)
        log.info("页面 %s | %s | 截图 %s ms | 匹配 %d ms%s", page, action,
                 getattr(self._capture, "last_ms", None), match_ms, self._scores_text(scores))

        skipped = getattr(self._input, "skipped", 0)
        if skipped >= SKIPS_TO_PAUSE or self._failed_starts >= SKIPS_TO_PAUSE:
            # 开打没全按下去，输入那一层未必知道（KmbBackend 取不到窗口中心时，中键根本没发），
            # 所以这里自己也数着
            why = (f"连着 {skipped} 次没按下去：焦点、指针或修饰键不对" if skipped >= SKIPS_TO_PAUSE
                   else f"连着 {self._failed_starts} 次开打没全按下去")
            self._controls.pause(why)
            self._release("暂停")
            log.warning("%s，暂停，等人看过以后 resume", why)
            self._pause_noted = True
        return None

    # --- 每一轮里的几步 -------------------------------------------------------

    def _enter_pause(self):
        if not self._pause_noted:
            self._release("暂停")
            log.info("已暂停，等 resume")
            self._pause_noted = True

    def _check_world(self):
        obs = self._observe()
        decision = supervisor.decide(self._prefer, obs, current_hwnd=self._window)
        if not obs.hwnd_valid:
            return "游戏窗口不在了"
        if self._window is None:
            self._window = obs.hwnd
        if "reconnect_transport" in decision.actions:
            return "游戏窗口换了一个，游戏多半重启过"
        if decision.backend != self._prefer:
            # 循环只有这一个后端；supervisor 退到别的后端，就是这一个用不了
            return f"输入用不了：{decision.reason}"
        # supervisor 还可能要别的（spoof_off：关掉 Windows 上的焦点伪装）。这一版做不了的，
        # 宁可停下，也不当没看见
        unsupported = [a for a in decision.actions
                       if a not in ("release_all", "reconnect_transport", "reacquire_window")]
        if unsupported:
            return f"supervisor 要做 {', '.join(unsupported)}，这一版做不了"
        return None

    def _recognise(self, frame):
        threshold = self._cfg.get("detect.threshold")
        found = {}
        if self._cfg.get("detect.log_scores"):
            # 记分数时每张模板都算一遍：判定树命中早的那一条就不往下试了，日志里会缺几张
            found = {name: cv_best_match(frame, tpl) for name, tpl in self._templates.items()}

        def matches(name):
            if name not in found:
                found[name] = cv_best_match(frame, self._templates[name])
            return found[name] is not None and found[name][4] >= threshold

        page = pages.resolve(PAGE_RULES, matches, PAGE_NAME.UNKNOWN)
        return page, {name: (hit[4] if hit else None) for name, hit in found.items()}

    def _act(self, page, frame):
        if page == PAGE_NAME.BATTLE:
            self._unknown_streak = 0
            if self._battling:
                return "在打"
            self._backend.hold_move()
            self._backend.battle_press()
            # 输入那一层可能没按下去（焦点、指针、修饰键），也可能只按下去一半；那就下一轮再按
            self._battling = self._battle_started()
            if self._battling:
                self._failed_starts = 0
                return "开打"
            self._failed_starts += 1
            return f"开打，没全按下去（连续第 {self._failed_starts} 次）"
        self._failed_starts = 0
        self._end_battle()
        if page == PAGE_NAME.UNKNOWN:
            return self._unknown_page(frame)
        self._unknown_streak = 0
        action = PAGE_ACTIONS.get(page)
        if action == "switch_again":
            self._backend.again()
            return "再来一次"
        if action == "tap_confirm":
            self._backend.confirm()
            return "确认"
        log.warning("页面 %s 没有配置动作（pagetree.PAGE_ACTIONS 里是 %r），这一轮不按",
                    page, action)
        return "不按"

    def _unknown_page(self, frame):
        self._unknown_streak += 1
        self._save_anomaly(frame)
        cap = self._cfg.get("loop.max_blind_taps")
        if self._unknown_streak <= cap:
            self._backend.confirm()
            return f"认不出，点确认（连续第 {self._unknown_streak} 次）"
        if self._unknown_streak == cap + 1:
            log.warning("连着 %d 轮认不出页面，不再按键。常见原因：模板和现在的分辨率对不上"
                        "（detect.template_scale），或者游戏停在了模板里没有的画面上", cap)
        return "认不出，不按"

    def _battle_started(self):
        held = set(self._input.held)
        return self._battle_inputs <= held if self._battle_inputs else bool(held)

    def _end_battle(self):
        # 只按下去一半的开打（W 按住了、中键没有）也算按着，换页时一样要松开
        if self._battling or self._input.held:
            self._backend.release_move()
            self._backend.battle_release()
            self._battling = False

    def _release(self, why):
        for releaser in (self._backend, self._input):
            if releaser is None:
                continue
            try:
                releaser.release_all()
            except Exception:
                log.warning("松开失败（%s）", why, exc_info=True)
        self._battling = False

    def _save_anomaly(self, frame):
        if not self._cfg.get("detect.save_anomaly_frames") or self._anomaly_dir is None:
            return
        limit = self._cfg.get("detect.max_anomaly_frames")
        if self._anomalies is None:
            # 上限管的是这个目录，不是这一次运行：每次重跑都再存满一遍，目录会越来越大
            try:
                self._anomalies = (len(list(self._anomaly_dir.glob("unknown-*.png")))
                                   if self._anomaly_dir.is_dir() else 0)
            except OSError as exc:
                # 数不了就不存了：和存图失败一样只记一笔，不能为它停下循环
                log.warning("数不了 %s 里已有的画面（%s），这一趟不再存", self._anomaly_dir, exc)
                self._anomalies = limit
        if self._anomalies >= limit:
            return
        path = self._anomaly_dir / f"unknown-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
        try:
            self._anomaly_dir.mkdir(parents=True, exist_ok=True)
            self._save_frame(frame, path)
        except OSError as exc:
            log.warning("存认不出的画面失败: %s", exc)
            return
        self._anomalies += 1
        log.info("存下认不出的画面 %s（%d/%d）", path, self._anomalies, limit)

    def _scores_text(self, scores):
        if not self._cfg.get("detect.log_scores"):
            return ""
        parts = [f"{name} {'-' if score is None else f'{score:.3f}'}"
                 for name, score in scores.items()]
        return " | 得分 " + ", ".join(parts)
