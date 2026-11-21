# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Identity checks shared by native Maa probes and the resource Agent."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import gamescope


class ProbeError(RuntimeError):
    pass


def process_stamp(pid, proc=Path("/proc")):
    try:
        # comm may contain spaces or parentheses; fields after its final ')' start at 3.
        value = (Path(proc) / str(pid) / "stat").read_text()
        return value.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        raise ProbeError(f"process {pid} is gone or its identity cannot be read") from None


@dataclass(frozen=True)
class Target:
    appid: str
    pid: int
    start: str
    member_pid: int
    member_start: str
    runtime: str
    display: str
    wayland: str
    display_no: int
    eis: str


def discover_target(appid, *, expected_pid=None):
    processes = gamescope.find_game_processes(appid)
    # No fallback from a matching DISPLAY when process ancestry is unavailable.
    groups = {}
    for member in processes:
        owner = gamescope.nearest_gamescope(member["pid"])
        if owner is not None:
            groups.setdefault(owner, []).append(member)
    if len(groups) != 1:
        raise ProbeError(f"expected one Gamescope for app {appid}; found {len(groups)}")
    pid, members = next(iter(groups.items()))
    if expected_pid is not None and pid != expected_pid:
        raise ProbeError("discovered compositor is not the owned test process")
    values = gamescope.instance_values(members)
    runtime = values["runtime"]
    if not runtime or Path(runtime).resolve() != Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent")).resolve():
        raise ProbeError("game and probe must use the same host runtime directory")
    for key in gamescope.PIPEWIRE_VARS:
        if any(p.get(key) not in (None, os.environ.get(key)) for p in members):
            raise ProbeError("game and probe PipeWire environments differ")
    wayland = Path(values["wayland"] or "")
    if not wayland.is_absolute():
        wayland = Path(runtime) / wayland
    match = re.fullmatch(r"gamescope-(\d+)", wayland.name)
    if not match or wayland.parent.resolve() != Path(runtime).resolve():
        raise ProbeError("no host Gamescope endpoint with an explicit instance number")
    if not wayland.is_socket():
        raise ProbeError("the selected Gamescope socket is unavailable")
    member = members[0]
    return Target(appid, pid, process_stamp(pid), member["pid"],
                  process_stamp(member["pid"]), runtime, values["display"], str(wayland),
                  int(match[1]), str(wayland) + "-ei")


def check_target(target):
    if process_stamp(target.pid) != target.start:
        raise ProbeError("Gamescope was replaced; start a new probe")
    if process_stamp(target.member_pid) != target.member_start:
        raise ProbeError("target process was replaced; start a new probe")
    if gamescope.nearest_gamescope(target.member_pid) != target.pid:
        raise ProbeError("target ancestry changed")
    if not Path(target.wayland).is_socket():
        raise ProbeError("target socket disappeared")


def select_instance(target, instances):
    selected = [i for i in instances if i.display_no == target.display_no]
    if len(selected) != 1:
        raise ProbeError("Maa did not find exactly the selected Gamescope instance")
    instance = selected[0]
    if instance.eis_socket_path and Path(instance.eis_socket_path) != Path(target.eis):
        raise ProbeError("Maa input endpoint does not belong to the selected instance")
    return instance


def node_identity(dump, node_id, pid):
    clients = {o["id"]: (o.get("info") or {}).get("props", {}) for o in dump
               if o.get("type") == "PipeWire:Interface:Client"}
    nodes = [o for o in dump if o.get("type") == "PipeWire:Interface:Node" and o.get("id") == node_id]
    if len(nodes) != 1:
        raise ProbeError("selected PipeWire node is absent or ambiguous")
    props = (nodes[0].get("info") or {}).get("props", {})
    client = clients.get(props.get("client.id"), {})
    # Prefer the PID authenticated by the PipeWire daemon, not a self-reported PID.
    if (props.get("node.name") != "gamescope" or props.get("media.class") != "Video/Source"
            or str(client.get("pipewire.sec.pid")) != str(pid)):
        raise ProbeError("PipeWire source is not owned by the selected Gamescope")
    serial = props.get("object.serial")
    if not isinstance(serial, int) or serial <= 0:
        raise ProbeError("PipeWire source has no stable serial")
    return serial


def read_node(node_id, target):
    result = subprocess.run(["pw-dump", "-N"], capture_output=True, text=True, timeout=3, check=True)
    return node_identity(json.loads(result.stdout), node_id, target.pid)


def controller_config(instance, *, input_enabled=False, capture=True):
    if capture and instance.pipewire_node_id <= 0:
        raise ProbeError("Maa reports no capture node for the selected Gamescope")
    if input_enabled and not instance.eis_socket_path:
        raise ProbeError("Maa reports no input socket for the test instance")
    return {"screencap_method": 4 if capture else 0,
            "input_method": 4 if input_enabled else 0,
            "pw_node_id": instance.pipewire_node_id if capture else 0,
            "eis_socket_path": instance.eis_socket_path if input_enabled else "",
            "use_win32_vk_code": False}

