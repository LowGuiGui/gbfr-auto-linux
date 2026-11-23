#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The runner's real front-end loop (maa_runner.drive) against the scripted game.

For the protocol tests in test_maa_runner.py only: they start this as a child process,
exactly as the control window will start the runner, and talk to it over stdin/stdout.
Nothing here reaches Gamescope, PipeWire, X11 or the control socket.
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import maa_graph  # noqa: E402
import maa_options  # noqa: E402
import maa_runner  # noqa: E402
import test_maa_pipeline_graph as graph  # noqa: E402


class Patch:
    """monkeypatch's setattr, for use outside pytest (the process ends with the test)."""

    @staticmethod
    def setattr(target, name, value):
        setattr(target, name, value)


def screens(name):
    if name == "one":
        return [*graph.menus(), *graph.battle(victory=True), graph.titles(),
                graph.rewards("off", "action:GBFR_LetItEnd", after=2), graph.town()]
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
    raise ValueError(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--runs", default="12")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--frame-delay", type=float, default=0.0)
    args = parser.parse_args()
    game = maa_graph.ScriptedGame(screens(args.scenario))
    connect, prepare = maa_graph.runner_parts(Patch, game, frame_delay=args.frame_delay)
    args.state.mkdir(parents=True, exist_ok=True)

    def make_runner(emit):
        return maa_runner.Runner(maa_options.run_values(args.runs), emit,
                                 status_path=args.state / "session.json",
                                 log_dir=args.state / "log",
                                 marker=maa_runner.Marker(args.state / "runner.json"),
                                 connect=connect, prepare=prepare)
    code = maa_runner.drive(make_runner, json_mode=True)
    (args.state / "keys.txt").write_text(" ".join(game.keys))
    return code


if __name__ == "__main__":
    sys.exit(main())
