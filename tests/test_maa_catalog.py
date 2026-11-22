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


def option_overrides(interface):
    for option in interface["option"].values():
        if "pipeline_override" in option:
            yield option["pipeline_override"]
        for case in option.get("cases", []):
            yield case["pipeline_override"]


def test_run_options_cannot_overwrite_selected_quest():
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    interface = render({}, catalog)
    for task in interface["task"]:
        for override in option_overrides(interface):
            assert not (task["pipeline_override"].keys() & override.keys())


def test_run_options_reach_the_agent_nodes():
    pipeline = json.loads((ROOT / "assets/resource/pipeline/farming.json").read_text())
    interface = render({}, json.loads((ROOT / "assets/catalog.json").read_text()))
    runs = interface["option"]["Runs"]
    assert runs["pipeline_override"] == {"GBFR_RunLimit": {"attach": {"limit": "{Runs}"}}}
    assert runs["inputs"][0]["pipeline_type"] == "int"
    switch = interface["option"]["UntilStopped"]
    assert [case["name"] for case in switch["cases"]] == ["Yes", "No"]
    assert {"GBFR_RunLimit", "GBFR_UntilStopped"} <= pipeline.keys()
    assert pipeline["GBFR_UntilStopped"]["enabled"] is False


def test_descriptions_carry_no_panel_address_and_limits_come_from_the_catalog():
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    interface = render({}, catalog)
    for task, quest in zip(interface["task"], catalog["quests"], strict=True):
        assert "127.0.0.1" not in task["description"]
        attach = task["pipeline_override"]["GBFR_QuestConfig"]["attach"]
        assert attach == {"quest": quest["name"], "battle_limit": quest["battle_limit"]}
    assert str(catalog["profile"]["control_panel_port"]) in interface["welcome"]


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
