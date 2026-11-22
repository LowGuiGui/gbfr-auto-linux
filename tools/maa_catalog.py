#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Render standard Project Interface tasks from the versioned quest catalog.

The catalog holds the quests, their limits and every user-facing string; this
script only arranges them into `interface.json`.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME_FIELDS = ("NameList", "NameDetails", "NameReady", "NameConfirm", "NameResult")
OBJECTIVE_FIELDS = ("ObjectiveDetails", "ObjectiveBattle")


def quest_override(quest):
    result = {"GBFR_Catalog_" + name: {"expected": quest["name"]} for name in NAME_FIELDS}
    result.update({"GBFR_Catalog_" + name: {"expected": quest["objective"]}
                   for name in OBJECTIVE_FIELDS})
    result["GBFR_QuestConfig"] = {"attach": {"quest": quest["name"],
                                             "battle_limit": quest["battle_limit"]}}
    return result


def render(interface, catalog):
    text = catalog["text"]
    interface = dict(interface)
    interface["task"] = [{
        "name": quest["id"], "label": quest["label"], "entry": "GBFR_Start",
        "description": text["task"].format(**quest),
        "option": ["UntilStopped"], "pipeline_override": quest_override(quest),
    } for quest in catalog["quests"]]
    interface["option"] = {
        "UntilStopped": {
            "type": "switch", "label": text["until_stopped_label"], "default_case": "No",
            "cases": [
                {"name": "Yes", "pipeline_override": {"GBFR_UntilStopped": {"enabled": True}}},
                {"name": "No", "pipeline_override": {"GBFR_UntilStopped": {"enabled": False}},
                 "option": ["Runs"]},
            ]},
        "Runs": {
            "type": "input",
            "inputs": [{"name": "Runs", "label": text["runs_label"], "default": "10",
                        "pipeline_type": "int", "verify": "^[1-9][0-9]{0,2}$",
                        "pattern_msg": text["runs_pattern"]}],
            "pipeline_override": {"GBFR_RunLimit": {"attach": {"limit": "{Runs}"}}}},
    }
    interface["welcome"] = text["welcome"].format(port=catalog["profile"]["control_panel_port"])
    return interface


def main():
    path = ROOT / "assets/interface.json"
    result = render(json.loads(path.read_text()), json.loads((ROOT / "assets/catalog.json").read_text()))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
