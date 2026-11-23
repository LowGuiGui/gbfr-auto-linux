# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib

import pytest

from tools.build_maa import build, fetch, fetch_ocr, launcher_files, mxu_config


def test_existing_bundle_is_never_overwritten(tmp_path):
    artifact = tmp_path / "bundle"
    artifact.mkdir()
    (artifact / "settings").write_text("preserve")
    with pytest.raises(ValueError, match="already exists"):
        build(artifact, tmp_path / "cache", "3.13")
    assert (artifact / "settings").read_text() == "preserve"
    assert not (tmp_path / "cache").exists()


def test_corrupt_cache_is_refused_without_network_or_replacement(tmp_path):
    cached = tmp_path / "runtime.zip"
    cached.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="hash mismatch"):
        fetch(tmp_path, {"filename": cached.name, "sha256": "0" * 64})
    assert cached.read_bytes() == b"damaged"


def test_verified_cache_does_not_need_network(tmp_path):
    cached = tmp_path / "runtime.zip"
    cached.write_bytes(b"pinned fixture")
    item = {"filename": cached.name, "sha256": hashlib.sha256(cached.read_bytes()).hexdigest()}
    assert fetch(tmp_path, item) == cached


def test_ocr_only_uses_the_pinned_hash_checked_files(tmp_path):
    import json
    from tools.build_maa import OCR_FILES, ROOT
    pins = json.loads((ROOT / "assets/dependencies.json").read_text())["downloads"]
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in OCR_FILES:  # a damaged cache must be refused, never used or replaced
        (cache / pins["ocr-" + name]["filename"]).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="hash mismatch"):
        fetch_ocr(tmp_path / "model", cache)
    assert not (tmp_path / "model").exists()


def test_mxu_config_prefills_one_instance_from_the_interface_and_catalog():
    import json
    from tools.build_maa import ROOT
    interface = json.loads((ROOT / "assets/interface.json").read_text())
    catalog = json.loads((ROOT / "assets/catalog.json").read_text())
    config = mxu_config(interface, catalog)
    (instance,) = config["instances"]
    assert instance["controllerName"] == interface["controller"][0]["name"]
    assert instance["resourceName"] == interface["resource"][0]["name"]
    assert [task["taskName"] for task in instance["tasks"]] == [task["name"] for task in interface["task"]]
    assert [task["enabled"] for task in instance["tasks"]] == [True] + [False] * (len(instance["tasks"]) - 1)
    assert config["lastActiveInstanceId"] == instance["id"]
    assert config["settings"]["webServerPort"] == catalog["profile"]["control_panel_port"]
    assert config["settings"]["allowLanAccess"] is False


def test_launchers_point_at_this_bundle(tmp_path):
    files = launcher_files(tmp_path / "maa-0.2.0")
    script, mode = files["start.sh"]
    assert script.startswith("#!/bin/sh") and "exec ./mxu" in script and mode == 0o755
    entry, _ = files["gbfr-auto.desktop"]
    assert f'Exec="{tmp_path / "maa-0.2.0" / "start.sh"}"' in entry


# --- the control window's bundle ---------------------------------------------------------

def test_the_window_bundle_names_match_the_window():
    import os
    import control_view
    from tools.build_maa import APP_FILES, APP_ID, BUNDLE_SCRIPTS, ROOT
    assert APP_ID == control_view.APP_ID
    assert all((ROOT / name).is_file() for name in APP_FILES)
    assert all(os.access(ROOT / "assets/bundle" / name, os.X_OK) for name in BUNDLE_SCRIPTS)
    for name in ("install.sh", "uninstall.sh"):
        assert f"id={APP_ID}\n" in (ROOT / "assets/bundle" / name).read_text()
    assert (ROOT / "assets/icons" / f"{APP_ID}.svg").is_file()


def test_the_bundle_carries_every_local_module_its_code_imports():
    import ast
    from pathlib import Path
    from tools.build_maa import APP_FILES, ROOT
    local = {path.stem for path in ROOT.glob("*.py")}
    needed = set()
    for name in APP_FILES:
        for node in ast.walk(ast.parse((ROOT / name).read_text())):
            if isinstance(node, ast.Import):
                needed |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                needed.add(node.module.split(".")[0])
    assert needed & local <= {Path(name).stem for name in APP_FILES}


def fake_downloads(tmp_path):
    from tools.build_maa import WINDOW_DOWNLOADS
    folder = tmp_path / "downloads"
    folder.mkdir()
    for key in WINDOW_DOWNLOADS:
        (folder / key).write_text(key)
    return {key: folder / key for key in WINDOW_DOWNLOADS}


def test_the_window_bundle_has_no_mxu_and_its_runner_finds_everything(tmp_path):
    import os
    import subprocess
    import sys
    from tools.build_maa import OCR_FILES, assemble_window
    bundle = assemble_window(tmp_path / "bundle", fake_downloads(tmp_path))
    names = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*")}
    assert {"app/control_window.py", "app/maa_runner.py", "interface.json", "README.md",
            "icons/io.github.lowguigui.GbfrAutoLinux.svg", "third-party/PaddleOCR-LICENSE"} <= names
    assert not [n for n in names if "mxu" in n.lower() or n.split("/")[0] in
                ("agent", "maafw", "config", "control.html", "start.sh", "gbfr-auto.desktop")]
    assert all(os.access(bundle / name, os.X_OK) for name in ("gbfr-control", "install.sh", "uninstall.sh"))
    # The runner, imported from the bundle's app/ folder, finds the bundle's own files.
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import maa_runner, maa_options; "
            "i = maa_runner.find_interface(); p = maa_options.resource_paths(json.loads(i.read_text()), i.parent); "
            "print(i); print(p[0]); print(maa_runner.ocr_model_dir(p, env={}))")
    found = subprocess.run([sys.executable, "-I", "-c", code, str(bundle / "app")], capture_output=True,
                           text=True, timeout=60, cwd=tmp_path)
    assert found.stdout.split() == [str(bundle / "interface.json"), str(bundle / "resource"),
                                    str(bundle / "resource" / "model" / "ocr")], found.stderr
    assert all((bundle / "resource/model/ocr" / name).is_file() for name in OCR_FILES)


def test_licence_files_of_the_runtime_are_copied_out_of_the_environment(tmp_path):
    from tools.build_maa import wheel_notices
    site = tmp_path / ".venv/lib/python3.13/site-packages"
    for path, text in (("maafw-5.16.1.dist-info/licenses/LICENSE.md", "lgpl"),
                       ("six-1.17.0.dist-info/LICENSE", "mit"),
                       ("six-1.17.0.dist-info/RECORD", "not a licence")):
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text(text)
    copied = wheel_notices(tmp_path / ".venv", tmp_path / "third-party")
    assert sorted(p.relative_to(tmp_path / "third-party").as_posix() for p in copied) == [
        "python/maafw-5.16.1/licenses/LICENSE.md", "python/six-1.17.0/LICENSE"]


def test_the_manifest_hashes_every_file_outside_the_environment(tmp_path, monkeypatch):
    import json
    import tools.build_maa as build_maa
    bundle = tmp_path / "bundle"
    (bundle / ".venv/bin").mkdir(parents=True)
    (bundle / ".venv/bin/python").write_text("runtime")
    (bundle / "app").mkdir()
    (bundle / "app/control_window.py").write_text("window")
    answers = {"rev-parse": "abc123\n", "status": ""}
    monkeypatch.setattr(build_maa.subprocess, "check_output",
                        lambda argv, text: answers[argv[3]])
    build_maa.write_manifest(bundle, kind="window")
    manifest = json.loads((bundle / "bundle.json").read_text())
    assert manifest["source"] == "abc123" and manifest["source_dirty"] is False
    assert manifest["kind"] == "window"
    assert manifest["files"] == {"app/control_window.py": hashlib.sha256(b"window").hexdigest()}


def install_env(tmp_path):
    import os
    return {"HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"]}


def test_install_points_the_menu_at_this_bundle_and_uninstall_only_removes_its_own(tmp_path):
    import os
    import shutil
    import subprocess
    from tools.build_maa import APP_ID, BUNDLE_SCRIPTS, ROOT
    bundles = []
    for name in ("gbfr-0.4.0 a", "gbfr-0.5.0"):  # a space in the path must survive
        bundle = tmp_path / name
        (bundle / "icons").mkdir(parents=True)
        shutil.copy2(ROOT / "assets/icons" / f"{APP_ID}.svg", bundle / "icons")
        for script in BUNDLE_SCRIPTS:
            shutil.copy2(ROOT / "assets/bundle" / script, bundle / script)
        bundles.append(bundle)
    old, new = bundles
    env = install_env(tmp_path)
    entry = tmp_path / "home/.local/share/applications" / f"{APP_ID}.desktop"
    icon = tmp_path / "home/.local/share/icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    # As on the owner's desktop: scalable/apps already exists, and an icon cache written by
    # another installer is as new as the theme folder, so GTK would trust it.
    theme = icon.parent.parent.parent
    icon.parent.mkdir(parents=True)
    cache = theme / "icon-theme.cache"
    cache.write_bytes(b"cache from another installer")
    for path in (cache, theme):
        os.utime(path, (1_700_000_000, 1_700_000_000))

    def run(bundle, script):
        return subprocess.run(["sh", str(bundle / script)], env=env, capture_output=True, text=True,
                              timeout=30)
    assert run(old, "install.sh").returncode == 0
    assert theme.stat().st_mtime > cache.stat().st_mtime  # the old cache no longer counts
    text = entry.read_text()
    assert f'Exec="{old}/gbfr-control"\n' in text and f"Icon={APP_ID}\n" in text
    assert "Name=GBFR 自动刷取\n" in text and icon.is_file()
    assert run(new, "install.sh").returncode == 0  # switching versions moves the entry
    assert f'Exec="{new}/gbfr-control"\n' in entry.read_text()
    refused = run(old, "uninstall.sh")
    assert refused.returncode == 1 and entry.exists() and icon.exists()
    os.utime(theme, (1_700_000_000, 1_700_000_000))
    assert run(new, "uninstall.sh").returncode == 0
    assert not entry.exists() and not icon.exists()
    assert theme.stat().st_mtime > cache.stat().st_mtime
    assert run(new, "uninstall.sh").returncode == 0  # nothing left to remove is fine


def test_install_refuses_a_path_the_menu_entry_cannot_hold(tmp_path):
    import shutil
    import subprocess
    from tools.build_maa import ROOT
    bundle = tmp_path / "gbfr-$HOME"
    bundle.mkdir()
    shutil.copy2(ROOT / "assets/bundle/install.sh", bundle / "install.sh")
    result = subprocess.run(["sh", str(bundle / "install.sh")], env=install_env(tmp_path),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and not (tmp_path / "home").exists()


def test_the_launcher_opens_the_window_with_the_system_python():
    from tools.build_maa import ROOT
    text = (ROOT / "assets/bundle/gbfr-control").read_text()
    assert 'exec /usr/bin/python3 -I "$here/app/control_window.py" "$@"' in text
    assert "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" in text
