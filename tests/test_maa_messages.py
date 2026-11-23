# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Every reason the Agent path can stop with has a Chinese explanation."""

import json
from pathlib import Path
import re

import pytest

from maa_messages import explain, stopped

ROOT = Path(__file__).resolve().parents[1]
# The runner shows its own start-up failures, and discovery (maa_target) reaches the
# player through both the runner and the Agent's guard.
SOURCES = ("maa_agent.py", "maa_session.py", "maa_guard.py", "maa_target.py", "maa_runner.py")


def reasons():
    found = set()
    for name in SOURCES:
        text = (ROOT / name).read_text()
        if name == "maa_runner.py":  # RunnerError(code, detail): the detail is the reason
            found |= set(re.findall(r'RunnerError\("\w+",\s*f?"([^"]+)"', text))
            continue
        found |= set(re.findall(r'Error\(f?"([^"]+)"', text))
        if name == "maa_guard.py":  # input_conflict returns its reasons
            found |= set(re.findall(r'return f?"([^"]+)"', text))
    pipeline = json.loads((ROOT / "assets/resource/pipeline/farming.json").read_text())
    for node in pipeline.values():
        param = (node.get("action") or {}).get("param", {}).get("custom_action_param", {})
        found |= {param[key] for key in ("reason", "exhausted") if key in param}
    # f-string fields stand for values; give each a plausible one.
    return sorted(re.sub(r"\{[^}]*\}", "7", reason) for reason in found
                  if reason != "Could not connect the GBFR Agent")  # client start-up, not a session


@pytest.mark.parametrize("reason", reasons())
def test_each_stop_reason_has_a_chinese_explanation(reason):
    message = explain(reason)
    assert not message.startswith("已停止：" + reason), reason
    assert re.search(r"[\u4e00-\u9fff]", message)


def test_values_are_carried_into_the_message_and_unknown_reasons_are_kept():
    assert "600 秒" in explain("The battle exceeded its 600 s limit")
    assert "GBFR_RepeatOn" in explain("Screen transition budget exhausted: GBFR_RepeatOn")
    assert explain("Something new") == "已停止：Something new"
    assert explain(None) is None


def test_a_stop_reports_the_repeat_toggle_as_known_on_off_or_unread():
    assert "还开着" in stopped(3, True)
    assert "没有开着" in stopped(3, False)
    unread = stopped(3, None)
    assert "可能开着" in unread and "没有开着" not in unread
