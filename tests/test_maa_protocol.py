# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The line protocol between a front end and the runner."""

import json

import pytest

import maa_protocol
from maa_protocol import ProtocolError, decode_command, decode_event, encode_command, encode_event


def test_events_round_trip_as_single_versioned_lines():
    line = encode_event("log", text="第 1/12 次完成")
    assert line.endswith("\n") and line.count("\n") == 1
    assert "第 1/12 次完成" in line  # readable, not \u-escaped
    assert decode_event(line) == {"v": 1, "event": "log", "text": "第 1/12 次完成"}


def test_commands_round_trip():
    for command in maa_protocol.COMMANDS:
        assert decode_command(encode_command(command)) == command


def test_a_person_can_type_bare_words():
    assert decode_command("finish\n") == "finish"
    assert decode_command("  stop  ") == "stop"
    assert decode_command("\n") is None


@pytest.mark.parametrize("line", ['{"v": 2, "cmd": "stop"}', '{"cmd": "stop"}', "pause",
                                  '{"v": 1, "cmd": "pause"}', "{not json", '[1, 2]'])
def test_other_versions_and_unknown_commands_are_refused(line):
    with pytest.raises(ProtocolError):
        decode_command(line)


def test_unknown_events_and_versions_are_refused():
    with pytest.raises(ProtocolError):
        encode_event("progress", value=1)
    with pytest.raises(ProtocolError):
        decode_event(json.dumps({"v": 2, "event": "log", "text": "x"}))
    with pytest.raises(ProtocolError):
        encode_command("pause")
