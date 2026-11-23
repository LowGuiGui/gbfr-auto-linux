# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""What the control window shows and allows, worked out without GTK.

The window (control_window.py) only draws what these functions return, so the rules can
be tested anywhere, CI included. The status rules follow the old web panel
(assets/control.html); the session snapshot comes from the runner (maa_runner.py).
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

APP_ID = "io.github.lowguigui.GbfrAutoLinux"
APP_NAME = "GBFR 自动刷取"
APP_DIR = "gbfr-auto-linux"
SETTINGS_VERSION = 1
RUNS_MIN, RUNS_MAX = 1, 999
# The runner gives up on a stop after 20 s and then releases keys (3 s per key at most);
# the window waits longer before it offers to end the runner itself.
STOP_DEADLINE_S = 35
HISTORY_ROWS = 5
LOG_LINES = 5

# Each information item can be shown or hidden in the settings (the owner's choice,
# 2026-11-23); the controls are always shown. (key, label, shown by default)
INFO_ITEMS = (
    ("progress", "任务名和进度", True),
    ("phase", "阶段", True),
    ("message", "停止原因", True),
    ("elapsed", "本次已用时间", True),
    ("average", "平均每轮用时", True),
    ("failures", "失败次数", True),
    ("history", "最近几次会话", True),
    ("logs_button", "“打开日志文件夹”按钮", True),
    ("log", "最近几条日志", False),
)

PHASES = {"ready": "正在接任务", "starting": "正在进入副本", "battle": "战斗中",
          "settled": "已通关，正在结算", "finished": "已完成，停在城镇",
          "stopped": "已按你的要求停止",
          "error": "操作已停止。请查看游戏当前画面，再从支持的起点重新开始。"}
ENDED = ("finished", "stopped", "error")
OUTCOMES = {"finished": "已完成", "stopped": "已停止", "error": "出错停止"}


# --- where things live ---------------------------------------------------------------

def config_dir(env=os.environ):
    return Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_DIR


def state_dir(env=os.environ):
    return Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / APP_DIR


def runner_command(app_dir, runs=None, until_stopped=False, env=os.environ):
    """The runner's command line, from this program's own folder.

    In a bundle the code lives in `app/` and the runner's environment in `../.venv`; in the
    repository both sit at the top. GBFR_RUNNER_COMMAND (a JSON list) replaces the program
    part, for tests that put a scripted game in place of the real one."""
    app_dir = Path(app_dir)
    if env.get("GBFR_RUNNER_COMMAND"):
        base = list(json.loads(env["GBFR_RUNNER_COMMAND"]))
    else:
        python = env.get("GBFR_RUNNER_PYTHON")
        if not python:
            candidates = (app_dir.parent / ".venv" / "bin" / "python",
                          app_dir / ".venv" / "bin" / "python")
            python = next((str(c) for c in candidates if c.exists()), sys.executable)
        base = [python, "-B", str(app_dir / "maa_runner.py")]
    mode = ["--until-stopped"] if until_stopped else ["--runs", str(runs)]
    return base + ["--json"] + mode


def app_version(app_dir):
    """The version for About: interface.json's, plus the source commit in a bundle.

    The repository keeps interface.json in assets/; a bundle keeps it, and bundle.json
    with the commit it was built from, beside its app/ folder."""
    app_dir = Path(app_dir)
    version = source = ""
    for path in (app_dir / "assets" / "interface.json", app_dir.parent / "interface.json"):
        try:
            version = str(json.loads(path.read_text(encoding="utf-8")).get("version") or "")
            break
        except (OSError, ValueError, AttributeError):
            continue
    if app_dir.name == "app":
        try:
            manifest = json.loads((app_dir.parent / "bundle.json").read_text(encoding="utf-8"))
            source = str(manifest.get("source") or "")[:7]
        except (OSError, ValueError, AttributeError):
            pass
    return f"{version} ({source})" if version and source else version or source


def release_command(app_dir, env=os.environ):
    command = runner_command(app_dir, 1, env=env)
    return command[:command.index("--json")] + ["--release-keys"]


# --- settings -------------------------------------------------------------------------

def default_settings():
    return {"version": SETTINGS_VERSION, "runs": 10, "until_stopped": False,
            "show": {key: shown for key, _, shown in INFO_ITEMS},
            "window": {"width": 460, "height": 760}}


def load_settings(path):
    """The saved settings over the defaults; anything unreadable falls back to a default."""
    settings = default_settings()
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict):
        return settings
    runs = saved.get("runs")
    if type(runs) is int and RUNS_MIN <= runs <= RUNS_MAX:
        settings["runs"] = runs
    if type(saved.get("until_stopped")) is bool:
        settings["until_stopped"] = saved["until_stopped"]
    for key, value in (saved.get("show") or {}).items():
        if key in settings["show"] and type(value) is bool:
            settings["show"][key] = value
    window = saved.get("window") or {}
    for key in ("width", "height"):
        if type(window.get(key)) is int and 200 <= window[key] <= 4000:
            settings["window"][key] = window[key]
    return settings


def save_settings(path, settings):
    """Atomic: a crash while saving leaves the old file, never half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


# --- what is shown ----------------------------------------------------------------------

def duration(seconds):
    """4:35, or 1:02:03 from an hour on."""
    if seconds is None:
        return "—"
    seconds = int(round(max(0, seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def average(runs):
    return sum(runs) / len(runs) if runs else None


def view(state):
    """The texts for the status area.

    `state`: status (the runner's last snapshot, or {}), running, stopping, ready, now
    (wall clock), log (recent lines), ended_at (when the last session ended), requested
    (the run count asked for at Start, None for until stopped)."""
    status = state.get("status") or {}
    phase = status.get("phase")
    running = state.get("running", False)
    timing = status.get("timing") or {}
    started = timing.get("started")
    refused = phase == "error" and not started  # refused before the session began
    connecting = running and phase in (None, "idle")
    active = phase not in (None, "idle") and not refused
    quest = status.get("quest") or ("当前任务（第一次结算后显示任务名）" if active or connecting
                                    else "等待任务")
    # The runner's snapshot has the limit once the session runs; before that, and for a
    # refused start, the count asked for at Start. 0 means none asked yet.
    limit = status["limit"] if "limit" in status else state.get("requested", 0)
    count = f"{status.get('completed', 0)} / {'一直刷' if limit is None else (limit or '—')} 次成功"
    if not state.get("ready", True):
        phase_text = "正在准备……"
    elif state.get("stopping") and running:
        phase_text = "正在停止……"
    elif connecting:
        phase_text = "正在连接游戏……"
    elif refused:
        phase_text = "没能开始"  # the message says why
    else:
        phase_text = PHASES.get(phase, "等待开始")
        if phase == "settled" and status.get("repeat"):
            phase_text += "（再次挑战已打开）"
        elif status.get("continued", 0) > 0 and phase not in ENDED:
            phase_text += f"（已在第 10 次确认里选“是” {status['continued']} 次）"
        if status.get("finish_requested") and running:
            phase_text += " · 已收到本轮后停止请求"
        if status.get("overrun"):
            phase_text += f" · 多完成 {status['overrun']} 次：停止时下一轮已经开始"
    end = state.get("now") if running or state.get("ended_at") is None else state["ended_at"]
    elapsed = (end - started) if started and end else None
    runs = timing.get("runs") or []
    mean = average(runs)
    battle = timing.get("battle_started")
    elapsed_text = f"本次已用 {duration(elapsed)}" if elapsed is not None else "本次已用 —"
    if running and battle and state.get("now"):
        elapsed_text += f"（这一场打了 {duration(state['now'] - battle)}）"
    return {
        "quest": quest,
        "count": count,
        "phase": phase_text,
        "message": status.get("message") or "",
        "detail": status.get("error") or "",
        "elapsed": elapsed_text,
        "average": f"平均每轮 {duration(mean)}（{len(runs)} 轮）" if mean is not None else "平均每轮 —",
        "failures": f"任务失败 {status.get('failures', 0)} 次",
        "log": "\n".join((state.get("log") or [])[-LOG_LINES:]),
    }


def controls(state):
    """Which controls can be used now."""
    status = state.get("status") or {}
    ready, running = state.get("ready", True), state.get("running", False)
    stopping, closing = state.get("stopping", False), state.get("closing", False)
    # After a runner died, Start waits until its keys have been released: the release
    # takes no session lock, so its key-ups could land in a new session's presses.
    return {"start": ready and not running and not closing and not state.get("releasing"),
            "settings": not running,
            "finish": running and not stopping and not status.get("finish_requested")
            and not state.get("finish_sent"),
            "stop": running and not stopping}


def history_rows(entries):
    """(title, subtitle) per past session, newest first."""
    rows = []
    for entry in entries:
        when = entry.get("ended") or ""
        try:
            when = datetime.fromisoformat(when).strftime("%m-%d %H:%M")
        except ValueError:
            pass
        limit = entry.get("limit")
        done = f"{entry.get('completed', 0)}/{limit if limit else '一直刷'}"
        title = f"{when}  {entry.get('quest') or '未读到任务名'}"
        parts = [f"{OUTCOMES.get(entry.get('outcome'), entry.get('outcome') or '?')} {done}"]
        if entry.get("average"):
            parts.append(f"平均每轮 {duration(entry['average'])}")
        if entry.get("elapsed"):
            parts.append(f"共 {duration(entry['elapsed'])}")
        if entry.get("failures"):
            parts.append(f"任务失败 {entry['failures']} 次")
        rows.append((title, " · ".join(parts)))
    return rows


def notification(outcome, status):
    """(title, body) for the desktop notification at a session's end, or None.

    On finishing and on errors, the owner's choice (2026-11-23). A stop the player asked
    for needs no notice, and neither does a start refused at once (no game, another
    session): the player has just clicked Start and reads the reason in the window."""
    completed = status.get("completed", 0)
    quest = status.get("quest") or "当前任务"
    if outcome == "finished":
        return f"{APP_NAME}：刷完了", f"{quest}，成功 {completed} 次，游戏停在城镇。"
    if outcome == "error" and (status.get("timing") or {}).get("started"):
        message = status.get("message") or status.get("error") or "出错停止。"
        return f"{APP_NAME}：出错停止", re.sub(r"\s+", " ", message)[:300]
    return None


def append_history(path, status, outcome, clock=time.time):
    """One line per finished, stopped or failed session. The runner writes it when a
    session ends; the window writes it only for a runner that died without saying how."""
    timing = status.get("timing") or {}
    started = timing.get("started")
    mean = average(timing.get("runs") or [])
    entry = {"ended": datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds"),
             "quest": status.get("quest"), "outcome": outcome,
             "completed": status.get("completed", 0), "limit": status.get("limit"),
             "elapsed": round(clock() - started, 1) if started else None,
             "average": round(mean, 1) if mean is not None else None,
             "failures": status.get("failures", 0), "message": status.get("message"),
             "error": status.get("error")}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_history(path, count=5):
    """The newest `count` entries, newest first; unreadable lines are skipped."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines):
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
        if len(entries) >= count:
            break
    return entries
