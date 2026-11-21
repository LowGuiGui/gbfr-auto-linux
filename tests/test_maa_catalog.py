# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import json
from pathlib import Path

from tools.maa_catalog import quest_override, render

ROOT = Path(__file__).resolve().parents[1]


def test_each_goal_updates_every_shared_quest_recognition():
    pipeline = json.loads((ROOT / "assets/resource/pipeline/farming.json").read_text())
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    for quest in catalog["quests"]:
        overrides = quest_override(quest)
        assert all(name in pipeline for name in overrides)
        assert overrides["GBFR_QuestConfig"]["attach"]["quest"] == quest["name"]
        for name, value in overrides.items():
            if "Name" in name:
                assert value["expected"] == quest["name"]
            elif "Objective" in name:
                assert value["expected"] == quest["objective"]


def test_run_count_cannot_overwrite_selected_quest():
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    interface = render({}, catalog)
    for task in interface["task"]:
        for choice in interface["option"]["Runs"]["cases"]:
            assert not (task["pipeline_override"].keys() & choice["pipeline_override"].keys())


def test_generated_interface_is_current():
    interface = json.loads((ROOT / "assets/interface.json").read_text())
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    assert render(interface, catalog) == interface


def test_shared_flow_has_no_unoverridable_quest_name():
    pipeline = json.loads((ROOT / "assets/resource/pipeline/farming.json").read_text())
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    names = {q["name"] for q in catalog["quests"]}

    def check(value):
        if isinstance(value, dict):
            assert value.get("expected") not in names
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    for name, node in pipeline.items():
        if not name.startswith("GBFR_Catalog_"):
            check(node)
