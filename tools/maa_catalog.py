#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Render standard Project Interface tasks from the versioned quest catalog."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME_FIELDS = ("NameList", "NameDetails", "NameReady", "NameConfirm", "NameResult")
OBJECTIVE_FIELDS = ("ObjectiveDetails", "ObjectiveBattle")


def quest_override(quest):
    result = {"GBFR_Catalog_" + name: {"expected": quest["name"]} for name in NAME_FIELDS}
    result.update({"GBFR_Catalog_" + name: {"expected": quest["objective"]}
                   for name in OBJECTIVE_FIELDS})
    result["GBFR_QuestConfig"] = {"attach": {"quest": quest["name"]}}
    return result


def render(interface, catalog):
    interface = dict(interface)
    interface["task"] = [{
        "name": quest["id"], "label": quest["label"], "entry": "GBFR_Start",
        "description": f"任务：{quest['name']}。{quest['difficulty']}，推荐战力 {quest['recommended_power']}。"
                       "需要解锁任务，且当前 AI 队伍能稳定通关。从希德霍姆城镇或任务中心开始，完成后返回城镇。"
                       "\n\n此预设仍在验证，请先用 1 次确认当前队伍能够完成。一次只勾选一个预设。"
                       " [查看进度和停止](http://127.0.0.1:12792/api/local-file?path=control.html)",
        "option": ["Runs"], "pipeline_override": quest_override(quest),
    } for quest in catalog["quests"]]
    interface["option"] = {"Runs": {
        "type": "select", "label": "成功通关次数", "default_case": "1",
        "cases": [{"name": str(n), "pipeline_override": {"GBFR_RunLimit": {"attach": {"limit": n}}}}
                  for n in (1, 3, 10, 30, 999)]}}
    interface["welcome"] = "[查看进度、选择本轮后停止](http://127.0.0.1:12792/api/local-file?path=control.html)"
    return interface


def main():
    path = ROOT / "assets/interface.json"
    result = render(json.loads(path.read_text()), json.loads((ROOT / "assets/catalog.json").read_text()))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
