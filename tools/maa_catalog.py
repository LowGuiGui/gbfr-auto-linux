#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Render the standard Project Interface task from the versioned catalog.

The catalog holds the task's label and every user-facing string; this script only
arranges them into `interface.json`. There is one task: the loop works on whatever
quest the player opened at the counter, so no quest name reaches the pipeline.
`verified_quests` records which quests have been run and with what evidence.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(interface, catalog):
    text = catalog["text"]
    task = catalog["task"]
    interface = dict(interface)
    interface["task"] = [{
        "name": task["id"], "label": task["label"], "entry": "GBFR_Start",
        "description": text["task"], "option": ["UntilStopped"],
    }]
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
