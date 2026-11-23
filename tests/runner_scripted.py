#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The runner's real front-end loop (maa_runner.drive) against the scripted game.

The protocol tests in test_maa_runner.py start this as a child process, exactly as the
control window starts the runner, and talk to it over stdin/stdout. The window's smoke
test (window_smoke.py) puts it in place of the runner through GBFR_RUNNER_COMMAND, so it
takes the runner's own arguments too (`--json`, `--runs N` or `--until-stopped`,
`--release-keys`). Nothing here reaches Gamescope, PipeWire, X11 or the control socket.

Scenarios:
    one, three, twelve
                  one, three or twelve runs that end in town
    endless       repeated runs for as long as a test needs; the test stops the session
    hold          run 1, then run 2's battle lasts until the front end acts: `finish`
                  plays the run out and ends in town, `stop` stops in the battle
    crash         as hold, but the process dies during run 2's battle (exit code 3)
    stuck         as hold, but the runner ignores every request to stop
    refuse        as hold, but the framework refuses the first finish request (the runner's
                  own rejection path reports it), and accepts the next one
"""

import argparse
import dataclasses
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import maa_graph  # noqa: E402
import maa_options  # noqa: E402
import maa_runner  # noqa: E402
import test_maa_pipeline_graph as graph  # noqa: E402

HOLD = 10 ** 9  # battle actions: the held battle never ends by itself
HELD = ("hold", "crash", "stuck", "refuse")
CRASH_CODE = 3
RELEASE_S = 1.0  # about as long as a real release takes to connect and send its key-ups


class Refusing:
    """The runner's tasker, except that it refuses to change the pipeline."""

    def __init__(self, tasker):
        self.tasker = tasker

    def override_pipeline(self, *_args, **_kwargs):
        return False

    def __getattr__(self, name):
        return getattr(self.tasker, name)


class Patch:
    """monkeypatch's setattr, for use outside pytest (the process ends with the test)."""

    @staticmethod
    def setattr(target, name, value):
        setattr(target, name, value)


def held_battle():
    return maa_graph.screen("battle, held", "GBFR_See_Battle", leave_on="action:GBFR_Battle",
                            after=HOLD)


def screens(name):
    if name == "one":
        return [*graph.menus(), *graph.battle(victory=True), graph.titles(),
                graph.rewards("off", "action:GBFR_LetItEnd", after=2), graph.town()]
    if name == "three":
        return [*graph.first_run_turning_repeat_on(), *graph.repeated_run_keeping_it_on(),
                *graph.last_run_turning_it_off()]
    if name == "twelve":
        result = graph.first_run_turning_repeat_on()
        for _ in range(8):
            result += graph.repeated_run_keeping_it_on()
        return result + [*graph.tenth_run_answered_yes(), *graph.repeated_run_keeping_it_on(),
                         *graph.last_run_turning_it_off()]
    if name == "endless":  # repeated runs for as long as a test needs; it stops the session
        result = graph.first_run_turning_repeat_on()
        for _ in range(8):
            result += graph.repeated_run_keeping_it_on()
        return result
    if name in HELD:
        return [*graph.first_run_turning_repeat_on(), held_battle()]
    raise ValueError(name)


def release_hold(game):
    """The front end asked to finish: the held battle ends and run 2 is played out to town,
    the way test_finish_during_battle_ends_after_that_run scripts it."""
    index = len(game.screens) - 1
    game.screens[index] = dataclasses.replace(game.screens[index], after=game.count + 1)
    game.screens += [graph.titles(), graph.rewards("on", "key:3"),
                     graph.rewards("off", "action:GBFR_LetItEnd", after=2), graph.town()]


def crash_in_the_held_battle(game, held_index):
    while game.index < held_index:
        time.sleep(0.05)
    time.sleep(1.0)  # long enough for a front end to show the battle
    os._exit(CRASH_CODE)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--runs", default="12")
    mode.add_argument("--until-stopped", action="store_true")
    mode.add_argument("--release-keys", action="store_true")
    parser.add_argument("--json", action="store_true", help="accepted; this always speaks JSON")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--frame-delay", type=float, default=0.0)
    return parser.parse_args(argv)


def main():
    args = parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    if args.release_keys:  # as the runner's --release-keys, without a game to reach
        time.sleep(RELEASE_S)
        with (args.state / "released.txt").open("a") as record:
            record.write(f"{time.time():.3f}\n")
        print("已对用到的按键各发了一次松开（脚本化游戏）。")
        return 0
    game = maa_graph.ScriptedGame(screens(args.scenario))
    connect, prepare = maa_graph.runner_parts(Patch, game, frame_delay=args.frame_delay)
    values = maa_options.run_values(args.runs, args.until_stopped)

    def make_runner(emit):
        runner = maa_runner.Runner(values, emit,
                                   status_path=args.state / "session.json",
                                   log_dir=args.state / "log",
                                   history_path=args.history,
                                   marker=maa_runner.Marker(args.state / "runner.json"),
                                   connect=connect, prepare=prepare)
        if args.scenario in HELD:
            finish, refused = runner.finish, []

            def finish_and_play_out():
                if args.scenario == "refuse" and not refused:
                    refused.append(True)
                    real, runner.tasker = runner.tasker, Refusing(runner.tasker)
                    try:
                        return finish()  # the runner reports the rejection itself
                    finally:
                        runner.tasker = real
                result = finish()
                release_hold(game)
                return result
            runner.finish = finish_and_play_out
        if args.scenario == "crash":
            threading.Thread(target=crash_in_the_held_battle, args=(game, len(game.screens) - 1),
                             daemon=True).start()
        if args.scenario == "stuck":
            runner.stop = lambda *_args, **_kwargs: True  # acknowledged, never done
        return runner
    code = maa_runner.drive(make_runner, json_mode=True)
    (args.state / "keys.txt").write_text(" ".join(game.keys))
    return code


if __name__ == "__main__":
    sys.exit(main())
