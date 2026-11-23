# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The line protocol between a front end (the control window, a test) and the runner.

One runner process serves one session. The session's settings come on its command line
(`--runs N` or `--until-stopped`); after that, the front end writes commands to the
runner's stdin and reads events from its stdout, one JSON object per line, each carrying
the protocol version `v`.

Commands (front end -> runner):
    {"v": 1, "cmd": "finish"}   stop after the current run (the game's Repeat Quest is
                                turned off on the results screen)
    {"v": 1, "cmd": "stop"}     stop now; the game itself does not pause
    {"v": 1, "cmd": "quit"}     the same as stop, from a front end that is closing
A bare word (`finish`, `stop`, `quit`) is accepted too, for people typing in a terminal.
Closing stdin means the front end is gone: the runner stops the session and exits.

Events (runner -> front end):
    ready   {"pid", "maafw", "python"}          the runner started
    status  {"status": {...}}                    the session snapshot (maa_agent.Hooks.status)
    log     {"text"}                             a line for the player, e.g. "第 1/12 次完成";
                                                 `finish_rejected: true` when a finish request
                                                 was not accepted and may be sent again
    error   {"code", "message", "detail"}        the session cannot start or go on;
                                                 `message` is Chinese, `detail` technical
    ended   {"outcome", "status"}                outcome: finished, stopped or error
"""

import json

VERSION = 1
COMMANDS = ("finish", "stop", "quit")
EVENTS = ("ready", "status", "log", "error", "ended")
OUTCOMES = ("finished", "stopped", "error")


class ProtocolError(ValueError):
    pass


def encode_event(kind, **fields):
    if kind not in EVENTS:
        raise ProtocolError(f"unknown event {kind!r}")
    return json.dumps({"v": VERSION, "event": kind, **fields}, ensure_ascii=False) + "\n"


def decode_event(line):
    message = json.loads(line)
    if not isinstance(message, dict) or message.get("v") != VERSION:
        raise ProtocolError("event from another protocol version")
    if message.get("event") not in EVENTS:
        raise ProtocolError(f"unknown event {message.get('event')!r}")
    return message


def encode_command(command):
    if command not in COMMANDS:
        raise ProtocolError(f"unknown command {command!r}")
    return json.dumps({"v": VERSION, "cmd": command}) + "\n"


def decode_command(line):
    """The command in one input line, or None for a blank line."""
    text = line.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"not JSON: {exc}") from None
        if not isinstance(message, dict) or message.get("v") != VERSION:
            raise ProtocolError("command from another protocol version")
        text = message.get("cmd")
    if text not in COMMANDS:
        raise ProtocolError(f"unknown command {text!r}")
    return text
