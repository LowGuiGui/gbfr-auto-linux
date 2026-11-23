# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import json
from pathlib import Path

from tools.maa_catalog import render

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / name).read_text())


def option_overrides(interface):
    for option in interface["option"].values():
        if "pipeline_override" in option:
            yield option["pipeline_override"]
        for case in option.get("cases", []):
            yield case["pipeline_override"]


def test_one_task_farms_whatever_quest_the_player_opened():
    interface = render({}, load("assets/catalog.json"))
    (task,) = interface["task"]
    assert task["entry"] == "GBFR_Start"
    assert "pipeline_override" not in task  # nothing quest-specific reaches the pipeline


def test_run_options_reach_the_agent_nodes():
    pipeline = load("assets/resource/pipeline/farming.json")
    interface = render({}, load("assets/catalog.json"))
    runs = interface["option"]["Runs"]
    assert runs["pipeline_override"] == {"GBFR_RunLimit": {"attach": {"limit": "{Runs}"}}}
    assert runs["inputs"][0]["pipeline_type"] == "int"
    switch = interface["option"]["UntilStopped"]
    assert [case["name"] for case in switch["cases"]] == ["Yes", "No"]
    for override in option_overrides(interface):
        assert override.keys() <= pipeline.keys()
    assert pipeline["GBFR_UntilStopped"]["enabled"] is False


def test_the_interface_points_at_no_panel_and_starts_no_agent():
    """MXU's fields are gone: no welcome link to its web panel, no Agent process to start,
    no panel port."""
    catalog = load("assets/catalog.json")
    interface = render(load("assets/interface.json"), catalog)
    assert "127.0.0.1" not in interface["task"][0]["description"]
    assert not {"welcome", "agent"} & interface.keys()
    assert "control_panel_port" not in catalog["profile"] and "welcome" not in catalog["text"]


def test_generated_interface_is_current():
    interface = load("assets/interface.json")
    assert render(interface, load("assets/catalog.json")) == interface


def test_the_pipeline_matches_no_quest_name():
    """Any quest works: no recognition expects a verified quest's name or objective."""
    pipeline = load("assets/resource/pipeline/farming.json")
    names = set()
    for quest in load("assets/catalog.json")["verified_quests"]:
        names |= {quest["name"], quest["objective"]}

    def check(value):
        if isinstance(value, dict):
            expected = value.get("expected")
            assert not any(name in str(expected) for name in names), expected
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(pipeline)
