#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""gbfr-auto-linux 的命令行：在 gamescope 里的 GBFR 上跑战斗循环，以及停下、暂停它。

    .venv/bin/python gbfr_auto.py run [--live] [--repeats N]
    .venv/bin/python gbfr_auto.py stop | pause | resume | status
    .venv/bin/python gbfr_auto.py release

run 不加 --live 是空跑：照常截图、认页面、记日志，一个按键都不发。--repeats N 完成 N 次就停。
stop、pause、resume、status 连到正在跑的那个循环（控制套接字在 $XDG_RUNTIME_DIR 里）；把 stop
和 pause 绑到 GNOME 的自定义快捷键上，在哪个窗口里都按得到。跑循环的终端里按 Ctrl+C 也是停下。
release 把配置里的每个键和中键都松一遍，给循环被强杀、键可能还按着的时候用。

游戏得先开着，从 Steam 启动。这个脚本从不启动或关闭游戏。配置是仓库目录里的 gbfr_auto.toml
（第一次运行时生成），日志在 logs/，认不出的画面（打开 detect.save_anomaly_frames 时）在
anomalies/。这几样都不进版本库。
"""

import argparse
import os
import signal
import sys
from contextlib import ExitStack
from pathlib import Path

from Xlib import error as xerror

import applog
import backend
import config
import control
import farm
import gamescope
import supervisor
import xtest_input
from applog import get_logger

log = get_logger(__name__)

REPO = Path(__file__).resolve().parent


class GameNotFound(RuntimeError):
    """没有找到游戏，或者找到的不止一个。"""


def find_game(appid=gamescope.APPID, proc="/proc"):
    """游戏所在的那个 gamescope 实例：返回 (实例名, 宿主上用得了的 DISPLAY 等值)。

    一个都没有、或者不止一个，都不开始：不止一个时随便挑一个，可能就对着另一个游戏按键了。
    """
    groups = gamescope.group_instances(gamescope.find_game_processes(appid, proc), proc)
    if not groups:
        raise GameNotFound("gamescope 里没有找到游戏。先从 Steam 把游戏开起来")
    if len(groups) > 1:
        raise GameNotFound(f"有 {len(groups)} 个 gamescope 实例在跑这个游戏，关掉多余的再来")
    (instance, members), = groups.items()
    return instance, gamescope.instance_values(members)


def connect(values, appid=gamescope.APPID):
    """连上游戏的嵌套 X，找到游戏窗口。"""
    d = gamescope.connect_x(values["display"], values["xauth"])
    window = gamescope.find_game_window(d, appid)
    if window is None:
        d.close()
        raise GameNotFound(f"嵌套 X（{values['display']}）里找不到游戏窗口")
    return d, window


def observer(window, window_input):
    """循环每一轮看的那一眼：窗口还在不在，输入还能不能用。"""
    def observe():
        try:
            window.get_geometry()
            alive = True
        except Exception:
            alive = False
        return supervisor.Observation(window_id=window.id if alive else None,
                                      window_valid=alive, input_ready=window_input.is_ready())
    return observe


def centre_of(window):
    """KmbBackend 要的中心点：游戏窗口自己的中心，相对窗口左上角。每次现取。"""
    def centre():
        try:
            g = window.get_geometry()
        except Exception:
            return None
        return g.width // 2, g.height // 2
    return centre


def cmd_run(args):
    applog.setup(str(REPO / "logs"))
    try:
        cfg = config.load(str(REPO))
    except config.ConfigError as exc:
        return _refuse(exc)
    applog.set_level(cfg.get("log.level"))
    mode = "实跑，会发按键" if args.live else "空跑，不发按键"
    log.info("=== gbfr-auto-linux 开始（%s）===", mode)

    controls = control.Controls()
    state = {}
    try:
        server = control.ControlServer(controls, control.socket_path(), status=lambda: dict(
            state.get("status", lambda: {})(), live=args.live)).start()
    except (control.AlreadyRunning, RuntimeError) as exc:
        return _refuse(exc)

    with ExitStack() as cleanup:
        cleanup.callback(server.close)
        try:
            _, values = find_game(args.appid)
            d, window = connect(values, args.appid)
            cleanup.callback(d.close)
            geometry = window.get_geometry()
            keys = cfg.section("keys")
            window_input = xtest_input.XTestInput(d, window, keys, live=args.live)
            cleanup.callback(window_input.release_all)
            templates = farm.load_templates(REPO / "template", cfg.get("detect.template_scale"))
            capture = gamescope.ScreenshotCapture(
                values["wayland"], values["runtime"], (geometry.width, geometry.height),
                gamescope.private_dir(os.environ["XDG_RUNTIME_DIR"]),
                stop=lambda: controls.stop_requested or controls.paused)
            # 开打 = KmbBackend 按住前进键、按下中键：两样都按住了才算开打。Farm 也在这里面：
            # 配置里出了界的数，它不肯开始，那也是一次不开始，不是一串 traceback
            loop = farm.Farm(capture, backend.KmbBackend(window_input, keys, centre_of(window)),
                             observer(window, window_input), controls, templates, cfg,
                             window_input=window_input, repeats=args.repeats,
                             anomaly_dir=REPO / cfg.get("detect.anomaly_dir"),
                             battle_inputs={keys["move"], "middle"})
        except (GameNotFound, xtest_input.InputRefused, gamescope.CaptureRefused, RuntimeError,
                KeyError, OSError, xerror.DisplayError, xerror.XError) as exc:
            return _refuse(exc)

        state["status"] = lambda: {"page": loop.page, "battles": loop.battles}
        print(f"开始：{mode}。游戏窗口 {window.id:#x}，画面 {geometry.width}x{geometry.height}。"
              f"停下：Ctrl+C，或者 gbfr_auto.py stop")
        previous = {sig: signal.signal(sig, lambda signum, frame: controls.request_stop(
            f"收到信号 {signal.Signals(signum).name}")) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            reason = loop.run()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        done = controls.stop_requested or (args.repeats and loop.battles >= args.repeats)
        print(f"停下：{reason}。完成 {loop.battles} 次战斗。日志：{applog.log_path()}")
        return 0 if done else 1


def _refuse(exc):
    log.error("没有开始：%s", exc)
    print(f"没有开始：{exc}", file=sys.stderr)
    return 1


def cmd_release(args):
    applog.setup(str(REPO / "logs"))
    try:
        cfg = config.load(str(REPO))
    except config.ConfigError as exc:
        return _refuse(exc)
    # Recovery owns the same domain as run and observation probes. Acquiring
    # the existing control server also refuses listeners without our lock.
    try:
        server = control.ControlServer(control.Controls(), control.socket_path(),
                                       status=lambda: {"operation": "release"}).start()
    except (RuntimeError, OSError) as exc:
        return _refuse(exc)
    with ExitStack() as cleanup:
        cleanup.callback(server.close)
        try:
            _, values = find_game(args.appid)
            d, window = connect(values, args.appid)
            cleanup.callback(d.close)
            complete = xtest_input.XTestInput(
                d, window, cfg.section("keys"), live=True).release_everything()
        except (GameNotFound, xtest_input.InputRefused, RuntimeError, OSError,
                xerror.DisplayError, xerror.XError) as exc:
            return _refuse(exc)
        if not complete:
            log.error("部分松开未确认：传输失败或当前映射不允许松开，见日志")
            print("部分松开未确认，见日志；请检查游戏内的按键状态", file=sys.stderr)
            return 1
        print("配置里的键和中键松开请求已发送。请检查游戏内的按键状态")
        return 0


def cmd_send(args):
    try:
        reply = control.send(args.command, control.socket_path())
    except (FileNotFoundError, ConnectionRefusedError):
        print("没有循环在跑", file=sys.stderr)
        return 1
    except (ConnectionError, RuntimeError, OSError) as exc:
        print(f"发不过去：{exc}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(", ".join(f"{key} {value}" for key, value in reply.items() if key != "ok"))
    return 0 if reply.get("ok") else 1


def repeats_count(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("至少 1 次")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="gbfr_auto.py",
                                     description="Battle automation for GBFR under gamescope.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the battle loop (a dry run unless --live)")
    run.add_argument("--live", action="store_true", help="send input to the game")
    run.add_argument("--repeats", type=repeats_count, help="stop after this many battles")
    run.add_argument("--appid", default=gamescope.APPID, help=argparse.SUPPRESS)
    run.set_defaults(handler=cmd_run)
    release = sub.add_parser("release", help="release every configured key and the middle button")
    release.add_argument("--appid", default=gamescope.APPID, help=argparse.SUPPRESS)
    release.set_defaults(handler=cmd_release)
    for name, text in (("stop", "stop the running loop"), ("pause", "pause it"),
                       ("resume", "let it carry on"), ("status", "say what it is doing")):
        sub.add_parser(name, help=text).set_defaults(handler=cmd_send)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
