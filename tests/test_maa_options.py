# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The runner reads interface.json the way MXU does, and refuses what it does not support."""

import copy
import json
from pathlib import Path

import pytest

import maa_options
from maa_options import InterfaceError, OptionError

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT / "assets" / "interface.json"


def interface():
    return json.loads(INTERFACE.read_text())


def test_the_projects_interface_is_accepted():
    loaded, base = maa_options.load(INTERFACE)
    assert base == INTERFACE.parent
    assert maa_options.entry(loaded) == "GBFR_Start"
    assert maa_options.short_side(loaded) == 720
    assert maa_options.resource_paths(loaded, base) == [(ROOT / "assets" / "resource").resolve()]


def test_a_run_limit_becomes_the_override_mxu_sent_in_the_real_game_check():
    # What MXU's low-level API received in the 0.3.0-dev real-game check (Runs = 12).
    sent = {"GBFR_UntilStopped": {"enabled": False}, "GBFR_RunLimit": {"attach": {"limit": 12}}}
    override = maa_options.build_override(interface(), maa_options.run_values(12))
    assert override == sent
    assert list(override) == list(sent)  # the switch's own override first, then the nested input
    assert type(override["GBFR_RunLimit"]["attach"]["limit"]) is int


def test_until_stopped_drops_the_run_limit():
    override = maa_options.build_override(interface(), maa_options.run_values(until_stopped=True))
    assert override == {"GBFR_UntilStopped": {"enabled": True}}


def test_defaults_apply_when_no_value_is_given():
    assert maa_options.build_override(interface(), {}) == {
        "GBFR_UntilStopped": {"enabled": False}, "GBFR_RunLimit": {"attach": {"limit": 10}}}


@pytest.mark.parametrize("runs", ["0", "1000", "abc", "", " 5", None, "05"])
def test_run_counts_outside_1_to_999_are_refused_in_chinese(runs):
    with pytest.raises(OptionError, match="1 到 999"):
        maa_options.build_override(interface(), maa_options.run_values(runs))


@pytest.mark.parametrize("runs", ["1", "999", 3])
def test_valid_run_counts(runs):
    override = maa_options.build_override(interface(), maa_options.run_values(runs))
    assert override["GBFR_RunLimit"]["attach"]["limit"] == int(runs)


def test_an_unknown_switch_case_is_refused():
    with pytest.raises(OptionError):
        maa_options.build_override(interface(), {"UntilStopped": "Maybe"})


def test_only_a_whole_placeholder_takes_the_inputs_type():
    data = interface()
    data["option"]["Runs"]["pipeline_override"] = {
        "GBFR_RunLimit": {"attach": {"limit": "{Runs}", "label": "x{Runs}", "list": ["{Runs}"]}}}
    override = maa_options.build_override(data, maa_options.run_values(7))
    assert override["GBFR_RunLimit"]["attach"] == {"limit": 7, "label": "x7", "list": [7]}


def test_later_overrides_merge_key_by_key():
    data = interface()
    data["task"][0]["pipeline_override"] = {"GBFR_RunLimit": {"attach": {"limit": 1, "keep": True}},
                                           "GBFR_Other": {"enabled": True}}
    override = maa_options.build_override(data, maa_options.run_values(4))
    assert override["GBFR_RunLimit"] == {"attach": {"limit": 4, "keep": True}}
    assert override["GBFR_Other"] == {"enabled": True}
    assert "pipeline_override" in data["task"][0]  # the interface itself is not modified
    assert data["task"][0]["pipeline_override"]["GBFR_RunLimit"]["attach"]["limit"] == 1


def broken(change):
    data = interface()
    change(data)
    return data


@pytest.mark.parametrize("change", [
    lambda d: d.update(preset=[]),                                     # a PI feature we do not use
    lambda d: d.update(interface_version=1),
    lambda d: d["controller"].append(copy.deepcopy(d["controller"][0])),
    lambda d: d["controller"][0].update(type="Adb"),
    lambda d: d["controller"][0]["linux"].update(input="UInput"),
    lambda d: d["resource"][0].update(path=["/absolute/resource"]),
    lambda d: d["task"].append(copy.deepcopy(d["task"][0])),
    lambda d: d["task"][0].update(name="another-task"),
    lambda d: d["task"][0].update(repeat=3),
    lambda d: d["option"]["UntilStopped"].update(type="select"),
    lambda d: d["option"]["UntilStopped"]["cases"][0].update(doc="x"),
    lambda d: d["option"]["UntilStopped"].update(default_case="Maybe"),
    lambda d: d["option"]["Runs"]["inputs"][0].update(pipeline_type="float"),
    lambda d: d["option"]["Runs"]["inputs"][0].update(verify="("),
    lambda d: d["option"]["UntilStopped"]["cases"][1].update(option=["Missing"]),
    lambda d: d["option"]["UntilStopped"]["cases"][1].update(option=["UntilStopped"]),
    lambda d: d["option"]["UntilStopped"]["cases"][1].update(option=[]),  # Runs unreachable
    lambda d: d["option"].pop("Runs"),
])
def test_anything_outside_the_supported_subset_is_refused(change):
    with pytest.raises(InterfaceError):
        maa_options.validate(broken(change))
