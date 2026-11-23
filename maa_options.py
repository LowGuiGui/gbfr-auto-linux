# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""The part of ProjectInterface v2 this project uses, and its options as a pipeline override.

`assets/interface.json` stays the one definition of the task, in the standard format,
and the runner reads it through this module. Only what the project uses is accepted: one
Linux controller, one resource, one task, and `switch` and `input` options (nested
options included). Anything else is refused when the file is loaded, so a later edit
cannot silently mean something the runner does not do.

Written from the ProjectInterface v2 rules, not from any client's code. Two rules that
matter here: a case or input applies its own `pipeline_override` before its nested
options, later overrides win key by key, and a string that is exactly `{Name}` takes the
input's `pipeline_type` (the run limit becomes an integer), while a placeholder inside a
longer string is replaced as text.
"""

import copy
import json
import re
from pathlib import Path

TASK = "current-quest"
RUNS = "Runs"
UNTIL_STOPPED = "UntilStopped"

TOP_KEYS = {"interface_version", "name", "label", "version", "license", "description",
            "controller", "resource", "task", "option"}
CONTROLLER_KEYS = {"name", "type", "display_short_side", "linux"}
LINUX = {"screencap": "PipeWire", "input": "Libei", "pipewire_source": "Gamescope",
         "use_win32_vk_code": False}
TASK_KEYS = {"name", "label", "entry", "description", "option", "pipeline_override"}
SWITCH_KEYS = {"type", "label", "default_case", "cases"}
CASE_KEYS = {"name", "pipeline_override", "option"}
INPUT_KEYS = {"type", "label", "inputs", "pipeline_override"}
FIELD_KEYS = {"name", "label", "default", "pipeline_type", "verify", "pattern_msg"}
PIPELINE_TYPES = {"int", "string", "bool"}


class InterfaceError(ValueError):
    """interface.json uses something outside the supported subset."""


class OptionError(ValueError):
    """An option value the interface does not allow; `str()` is the Chinese message."""


def _keys(name, value, allowed):
    if not isinstance(value, dict):
        raise InterfaceError(f"{name} must be an object")
    extra = set(value) - allowed
    if extra:
        raise InterfaceError(f"{name} uses unsupported keys: {', '.join(sorted(extra))}")


def _override(name, value):
    if value is not None and not isinstance(value, dict):
        raise InterfaceError(f"{name}.pipeline_override must be an object")


def load(path):
    """Read and validate an interface.json; return (interface, directory it lives in)."""
    path = Path(path)
    interface = json.loads(path.read_text(encoding="utf-8"))
    validate(interface)
    return interface, path.parent


def validate(interface):
    _keys("interface", interface, TOP_KEYS)
    if interface.get("interface_version") != 2:
        raise InterfaceError("Only ProjectInterface version 2 is supported")
    controllers = interface.get("controller")
    if not isinstance(controllers, list) or len(controllers) != 1:
        raise InterfaceError("Exactly one controller is supported")
    controller = controllers[0]
    _keys("controller", controller, CONTROLLER_KEYS)
    if controller.get("type") != "Linux" or controller.get("linux") != LINUX:
        raise InterfaceError("The controller must be the Gamescope PipeWire/libei Linux controller")
    short_side = controller.get("display_short_side")
    if short_side is not None and (type(short_side) is not int or short_side <= 0):
        raise InterfaceError("controller.display_short_side must be a positive integer")
    resources = interface.get("resource")
    if (not isinstance(resources, list) or len(resources) != 1
            or not isinstance(resources[0].get("path"), list) or not resources[0]["path"]
            or not all(isinstance(p, str) and not Path(p).is_absolute() for p in resources[0]["path"])):
        raise InterfaceError("Exactly one resource with relative paths is supported")
    tasks = interface.get("task")
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise InterfaceError("Exactly one task is supported")
    task = tasks[0]
    _keys("task", task, TASK_KEYS)
    if task.get("name") != TASK or not isinstance(task.get("entry"), str):
        raise InterfaceError(f"The task must be {TASK!r} with an entry node")
    _override("task", task.get("pipeline_override"))
    options = interface.get("option") or {}
    if not isinstance(options, dict):
        raise InterfaceError("option must be an object")
    for name, option in options.items():
        _validate_option(name, option, options)
    for name in task.get("option") or []:
        if name not in options:
            raise InterfaceError(f"task option {name!r} is not defined")
    reachable = set()
    _reach(task.get("option") or [], options, reachable, ())
    if not {UNTIL_STOPPED, RUNS} <= reachable:
        raise InterfaceError(f"The task must offer the {UNTIL_STOPPED!r} and {RUNS!r} options")
    if options[UNTIL_STOPPED]["type"] != "switch" or options[RUNS]["type"] != "input":
        raise InterfaceError(f"{UNTIL_STOPPED!r} must be a switch and {RUNS!r} an input")
    return interface


def _validate_option(name, option, options):
    if not isinstance(option, dict):
        raise InterfaceError(f"option {name!r} must be an object")
    kind = option.get("type")
    if kind == "switch":
        _keys(f"option {name!r}", option, SWITCH_KEYS)
        cases = option.get("cases")
        if not isinstance(cases, list) or not cases:
            raise InterfaceError(f"option {name!r} needs cases")
        names = []
        for case in cases:
            _keys(f"option {name!r} case", case, CASE_KEYS)
            _override(f"option {name!r} case", case.get("pipeline_override"))
            for nested in case.get("option") or []:
                if nested not in options:
                    raise InterfaceError(f"option {name!r} nests undefined option {nested!r}")
            names.append(case.get("name"))
        if len(set(names)) != len(names) or option.get("default_case") not in names:
            raise InterfaceError(f"option {name!r} needs unique case names and a default case")
    elif kind == "input":
        _keys(f"option {name!r}", option, INPUT_KEYS)
        _override(f"option {name!r}", option.get("pipeline_override"))
        fields = option.get("inputs")
        if not isinstance(fields, list) or not fields:
            raise InterfaceError(f"option {name!r} needs inputs")
        for field in fields:
            _keys(f"option {name!r} input", field, FIELD_KEYS)
            if not isinstance(field.get("name"), str) or field.get("pipeline_type", "string") not in PIPELINE_TYPES:
                raise InterfaceError(f"option {name!r} has an input without a name or with an unknown type")
            if "verify" in field:
                try:
                    re.compile(field["verify"])
                except re.error as exc:
                    raise InterfaceError(f"option {name!r} has an invalid verify pattern: {exc}") from None
            if "default" in field and not isinstance(field["default"], str):
                raise InterfaceError(f"option {name!r} input defaults must be strings")
    else:
        raise InterfaceError(f"option {name!r} has unsupported type {kind!r}")


def _reach(names, options, seen, path):
    for name in names:
        if name in path:
            raise InterfaceError(f"option {name!r} nests itself")
        seen.add(name)
        option = options[name]
        if option["type"] == "switch":
            for case in option["cases"]:
                _reach(case.get("option") or [], options, seen, path + (name,))


def merge(base, extra):
    """Merge `extra` into `base` key by key; nested objects merge, anything else replaces."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _typed(text, kind):
    if kind == "int":
        return int(text)
    if kind == "bool":
        return text.strip().lower() in {"1", "true", "yes", "y", "on"}
    return text


def _substitute(value, fields):
    if isinstance(value, dict):
        return {key: _substitute(item, fields) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, fields) for item in value]
    if isinstance(value, str):
        for name, (text, kind) in fields.items():
            if value == "{" + name + "}":
                return _typed(text, kind)
        for name, (text, _) in fields.items():
            value = value.replace("{" + name + "}", text)
    return value


def _apply(name, options, values, override):
    option = options[name]
    if option["type"] == "switch":
        chosen = values.get(name, option["default_case"])
        case = next((c for c in option["cases"] if c["name"] == chosen), None)
        if case is None:
            raise OptionError(f"选项 {option.get('label', name)} 没有 {chosen!r} 这一项")
        merge(override, case.get("pipeline_override") or {})
        for nested in case.get("option") or []:
            _apply(nested, options, values, override)
        return
    given = values.get(name, {})
    if not isinstance(given, dict):
        given = {option["inputs"][0]["name"]: given}
    fields = {}
    for field in option["inputs"]:
        text = str(given.get(field["name"], field.get("default", "")))
        if "verify" in field and not re.search(field["verify"], text):
            raise OptionError(field.get("pattern_msg") or f"{field.get('label', field['name'])} 的值不对")
        fields[field["name"]] = (text, field.get("pipeline_type", "string"))
    merge(override, _substitute(option.get("pipeline_override") or {}, fields))


def build_override(interface, values):
    """The pipeline override for the task with the given option values.

    `values` maps option names to a case name (switch) or to the text of the input
    (an input with one field; a dict for several). Missing values take the defaults."""
    task = interface["task"][0]
    options = interface.get("option") or {}
    override = merge({}, task.get("pipeline_override") or {})
    for name in task.get("option") or []:
        _apply(name, options, values, override)
    return override


def run_values(runs=None, until_stopped=False):
    """Option values for "N successful runs" or "until I stop it"."""
    if until_stopped:
        return {UNTIL_STOPPED: "Yes"}
    return {UNTIL_STOPPED: "No", RUNS: str(runs)}


def resource_paths(interface, base):
    return [Path(base, path).resolve() for path in interface["resource"][0]["path"]]


def entry(interface):
    return interface["task"][0]["entry"]


def short_side(interface):
    return interface["controller"][0].get("display_short_side")
