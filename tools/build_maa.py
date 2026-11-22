#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build a pinned local MXU/resource/Agent bundle without installing system packages."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AGENT_FILES = ("maa_agent.py", "maa_guard.py", "maa_messages.py", "maa_session.py",
               "maa_target.py", "gamescope.py", "control.py", "applog.py")


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch(cache, item):
    path = cache / item["filename"]
    if path.exists():
        if digest(path) != item["sha256"]:
            raise ValueError("Cached dependency hash mismatch: " + path.name)
        return path
    temporary = path.with_suffix(path.suffix + ".part")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "gbfr-auto-linux-builder"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    if digest(temporary) != item["sha256"]:
        raise ValueError("Downloaded dependency hash mismatch: " + path.name)
    temporary.replace(path)
    return path


OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")


def place_ocr(downloads, model):
    (model / "ocr").mkdir(parents=True)
    for name in OCR_FILES:
        shutil.copy2(downloads["ocr-" + name], model / "ocr" / name)
    return model


def fetch_ocr(output, cache):
    """Only the pinned OCR model, for the native recognition fixtures (tests, CI)."""
    if output.exists():
        raise ValueError("Output already exists")
    pins = json.loads((ROOT / "assets/dependencies.json").read_text())["downloads"]
    cache.mkdir(parents=True, exist_ok=True)
    return place_ocr({"ocr-" + n: fetch(cache, pins["ocr-" + n]) for n in OCR_FILES}, output)


def mxu_config(interface, catalog):
    """A ready MXU instance: GBFR's controller and resource, one preset ticked.

    Field names follow the config MXU 2.11.2 itself writes. Options are left to the
    interface defaults. The device is picked in MXU, because it is discovered live.
    """
    controller = interface["controller"][0]["name"]
    tasks = [{"id": quest["id"], "taskName": quest["id"], "enabled": index == 0,
              "enabledByController": {controller: True}, "expanded": index == 0,
              "optionValues": {}}
             for index, quest in enumerate(catalog["quests"])]
    return {
        "version": "1.0",
        "instances": [{"id": "gbfr", "name": "GBFR 自动刷取", "controllerName": controller,
                       "resourceName": interface["resource"][0]["name"], "savedDevice": {},
                       "tasks": tasks}],
        "lastActiveInstanceId": "gbfr",
        # One source for the panel port: the welcome link is rendered from the same value.
        "settings": {"webServerEnabled": True,
                     "webServerPort": catalog["profile"]["control_panel_port"],
                     "allowLanAccess": False, "checkUpdateOnStart": False,
                     "minimizeToTray": False, "helpImproveSoftware": False},
    }


def launcher_files(output):
    """A start script that works from anywhere, and a desktop entry pointing at it.

    The desktop entry needs this bundle's absolute path; it is not installed
    anywhere. Copy it to ~/.local/share/applications to get a menu entry.
    """
    script = "#!/bin/sh\n# Open this bundle's MXU from its own directory.\ncd \"$(dirname \"$0\")\" && exec ./mxu \"$@\"\n"
    entry = ("[Desktop Entry]\nType=Application\nName=GBFR 自动刷取\n"
             "Comment=用游戏自带的再次挑战连刷素材\n"
             f"Exec=\"{output / 'start.sh'}\"\nTerminal=false\nCategories=Game;\n")
    return {"start.sh": (script, 0o755), "gbfr-auto.desktop": (entry, 0o644)}


def build(output, cache, python):
    if output.exists():
        raise ValueError("Output already exists; choose a new version directory for rollback")
    pins = json.loads((ROOT / "assets/dependencies.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {key: fetch(cache, item) for key, item in pins["downloads"].items()}
    output.mkdir(parents=True)
    shutil.copytree(ROOT / "assets/resource", output / "resource", ignore=shutil.ignore_patterns("model"))
    for name in ("interface.json", "control.html", "dependencies.json", "catalog.json"):
        shutil.copy2(ROOT / "assets" / name, output / name)
    (output / "agent").mkdir()
    for name in AGENT_FILES:
        shutil.copy2(ROOT / name, output / "agent" / name)
    shutil.copytree(ROOT / "LICENSES", output / "LICENSES")
    shutil.copy2(ROOT / "COPYRIGHT", output / "COPYRIGHT")
    # README.md is the Chinese guide; the guides also keep their own names so
    # their links to each other work inside the bundle.
    for source, name in (("maa.md", "README.md"), ("maa.md", "maa.md"), ("maa.en.md", "maa.en.md")):
        shutil.copy2(ROOT / "docs" / source, output / name)
    shutil.copy2(ROOT / "docs/progression.md", output / "progression.md")
    notices = output / "third-party"
    notices.mkdir()
    with tarfile.open(downloads["mxu"]) as archive:
        for name, destination in (("./mxu", output / "mxu"), ("./LICENSE", notices / "MXU-LICENSE")):
            member = archive.getmember(name)
            if not member.isfile():
                raise ValueError("Expected an ordinary MXU archive member")
            with archive.extractfile(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    (output / "mxu").chmod(0o755)
    (output / "maafw").mkdir()
    with zipfile.ZipFile(downloads["maafw"]) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if len(parts) == 2 and parts[0] == "bin" and not name.endswith("/"):
                (output / "maafw" / parts[1]).write_bytes(archive.read(name))
            elif "license" in name.lower() and not name.endswith("/"):
                destination = notices / "MaaFramework" / name
                if ".." in Path(name).parts or Path(name).is_absolute():
                    raise ValueError("Unsafe archive path")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
    place_ocr(downloads, output / "resource/model")
    for name in ("MaaCommonAssets-LICENSE", "PaddleOCR-LICENSE"):
        shutil.copy2(downloads[name], notices / name)
    (output / "config").mkdir()
    config = mxu_config(json.loads((ROOT / "assets/interface.json").read_text()),
                        json.loads((ROOT / "assets/catalog.json").read_text()))
    (output / "config/mxu-GBFRLinux.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    for name, (text, mode) in launcher_files(output).items():
        (output / name).write_text(text)
        (output / name).chmod(mode)
    lock = output / "requirements.lock"
    shutil.copy2(ROOT / "requirements-maa-runtime.lock", lock)
    subprocess.run(["uv", "venv", "--python", python, str(output / ".venv")], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(output / ".venv/bin/python"),
                    "--require-hashes", "-r", str(lock)], check=True)
    source = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True))
    files = {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob("*"))
             if path.is_file() and ".venv" not in path.parts}
    (output / "bundle.json").write_text(json.dumps({"source": source, "source_dirty": dirty,
        "mxu": pins["mxu"], "maafw": pins["maafw"], "files": files}, indent=2) + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "dist/downloads")
    parser.add_argument("--python", default="3.13")
    parser.add_argument("--ocr-only", action="store_true",
                        help="Write only the pinned OCR model directory to --output")
    args = parser.parse_args()
    if args.ocr_only:
        print(fetch_ocr(args.output.resolve(), args.cache.resolve()))
    else:
        print(build(args.output.resolve(), args.cache.resolve(), args.python))


if __name__ == "__main__":
    main()
