# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib

import pytest

from tools.build_maa import build, fetch, fetch_ocr


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


def test_the_pins_hold_only_what_the_build_fetches():
    """No MXU download or MaaFramework archive is left: the build fetches every pinned
    download, and the runner's MaaFw comes from the hash-pinned lock."""
    import json
    from tools.build_maa import ROOT, WINDOW_DOWNLOADS
    pins = json.loads((ROOT / "assets/dependencies.json").read_text())
    assert set(pins["downloads"]) == set(WINDOW_DOWNLOADS)
    assert set(pins) == {"maafw", "ocr_revision", "downloads"}


def test_the_builder_offers_no_mxu_bundle():
    import inspect
    import subprocess
    import sys
    from tools.build_maa import ROOT
    usage = subprocess.run([sys.executable, "-I", str(ROOT / "tools/build_maa.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
    assert usage.returncode == 0 and "--output" in usage.stdout
    assert "mxu" not in usage.stdout.lower()
    assert list(inspect.signature(build).parameters) == ["output", "cache", "python"]


def test_no_bundled_module_starts_an_agent_server():
    """Importing maa.agent switches MaaFw into AgentServer mode for the whole process. The
    runner registers the hooks in its own process, so nothing it loads may import it."""
    import ast
    from tools.build_maa import APP_FILES, ROOT
    found = []
    for name in APP_FILES:
        for node in ast.walk(ast.parse((ROOT / name).read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            found += [(name, module) for module in modules
                      if module == "maa.agent" or module.startswith("maa.agent.")]
    assert not found


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


def bundle_with_scripts(path):
    """A release directory with what the menu scripts use: the icon, the scripts, the
    runner's Python and the manifest a finished build writes last."""
    import shutil
    from tools.build_maa import APP_ID, BUNDLE_SCRIPTS, ROOT
    (path / "icons").mkdir(parents=True)
    shutil.copy2(ROOT / "assets/icons" / f"{APP_ID}.svg", path / "icons")
    for script in BUNDLE_SCRIPTS:
        shutil.copy2(ROOT / "assets/bundle" / script, path / script)
    (path / ".venv/bin").mkdir(parents=True)
    (path / ".venv/bin/python").write_text("runtime")
    (path / ".venv/bin/python").chmod(0o755)
    (path / "bundle.json").write_text("{}")
    return path


@pytest.mark.parametrize("missing", ["bundle.json", ".venv/bin/python", "icons"])
def test_install_refuses_an_unfinished_bundle_and_keeps_the_current_one(tmp_path, missing):
    """A build that stopped part-way leaves its scripts without the runner or the manifest
    (or an icon); installing it would point the menu at a bundle that cannot run."""
    import os
    import shutil
    from tools.build_maa import APP_ID
    good = bundle_with_scripts(tmp_path / "good")
    broken = bundle_with_scripts(tmp_path / "broken")
    target = broken / missing
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    env = install_env(tmp_path)
    share = tmp_path / "home/.local/share"
    link = share / "gbfr-auto-linux/current"
    assert run_script(good / "install.sh", env).returncode == 0
    entry = (share / "applications" / f"{APP_ID}.desktop").read_text()
    result = run_script(broken / "install.sh", env)
    assert result.returncode == 1 and "没有安装" in result.stderr
    assert os.readlink(link) == str(good.resolve())
    assert (share / "applications" / f"{APP_ID}.desktop").read_text() == entry
    assert sorted(path.name for path in link.parent.iterdir()) == ["current"]


def test_an_install_that_fails_part_way_leaves_the_link_where_it_was(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder, so the icon copy would not fail")
    good = bundle_with_scripts(tmp_path / "good")
    new = bundle_with_scripts(tmp_path / "new")
    env = install_env(tmp_path)
    share = tmp_path / "home/.local/share"
    link = share / "gbfr-auto-linux/current"
    assert run_script(good / "install.sh", env).returncode == 0
    apps = share / "icons/hicolor/scalable/apps"
    apps.chmod(0o500)  # the icon cannot be written
    try:
        result = run_script(new / "install.sh", env)
    finally:
        apps.chmod(0o700)
    assert result.returncode != 0
    assert os.readlink(link) == str(good.resolve())


def test_a_build_that_stops_part_way_says_how_to_retry(tmp_path):
    partial = tmp_path / "gbfr"
    (partial / "app").mkdir(parents=True)  # no bundle.json: the build did not finish
    with pytest.raises(ValueError, match="did not finish"):
        build(partial, tmp_path / "cache", "3.13")


def run_script(path, env):
    import subprocess
    return subprocess.run(["sh", str(path)], env=env, capture_output=True, text=True, timeout=30)


def test_install_moves_only_the_link_and_uninstall_only_removes_its_own(tmp_path):
    import os
    from tools.build_maa import APP_ID
    old = bundle_with_scripts(tmp_path / "gbfr-0.4.0 a")  # a space in the path must survive
    new = bundle_with_scripts(tmp_path / "gbfr-0.5.0")
    env = install_env(tmp_path)
    share = tmp_path / "home/.local/share"
    entry = share / "applications" / f"{APP_ID}.desktop"
    icon = share / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    link = share / "gbfr-auto-linux/current"
    # As on the owner's desktop: scalable/apps already exists, and an icon cache written by
    # another installer is as new as the theme folder, so GTK would trust it.
    theme = icon.parent.parent.parent
    icon.parent.mkdir(parents=True)
    cache = theme / "icon-theme.cache"
    cache.write_bytes(b"cache from another installer")
    for path in (cache, theme):
        os.utime(path, (1_700_000_000, 1_700_000_000))

    assert run_script(old / "install.sh", env).returncode == 0
    assert theme.stat().st_mtime > cache.stat().st_mtime  # the old cache no longer counts
    text = entry.read_text()
    assert f'Exec="{link}/gbfr-control"\n' in text and f"Icon={APP_ID}\n" in text
    assert "Name=GBFR 自动刷取\n" in text and icon.is_file()
    assert os.readlink(link) == str(old.resolve())
    # Switching versions moves the link alone: GNOME's app grid keeps launching the command
    # an entry had when its icon was made, so the entry must never change.
    assert run_script(new / "install.sh", env).returncode == 0
    assert os.readlink(link) == str(new.resolve()) and entry.read_text() == text
    assert sorted(path.name for path in link.parent.iterdir()) == ["current"]  # no temporary left
    refused = run_script(old / "uninstall.sh", env)
    assert refused.returncode == 1 and entry.exists() and icon.exists()
    assert os.readlink(link) == str(new.resolve())
    os.utime(theme, (1_700_000_000, 1_700_000_000))
    assert run_script(new / "uninstall.sh", env).returncode == 0
    assert not entry.exists() and not icon.exists() and not os.path.lexists(link)
    assert not link.parent.exists()  # its folder held nothing else
    assert theme.stat().st_mtime > cache.stat().st_mtime
    assert run_script(new / "uninstall.sh", env).returncode == 0  # nothing left to remove is fine


def test_the_link_resolves_to_the_real_bundle_and_a_window_keeps_its_own(tmp_path):
    """Started through the link, the launcher runs the bundle the link named at that moment,
    so a window keeps its own files and About its own commit after the link moves."""
    import json
    import os
    import subprocess
    from pathlib import Path
    import control_view
    from tools.build_maa import ROOT
    a, b = (bundle_with_scripts(tmp_path / name) for name in ("a", "b"))
    for bundle, source in ((a, "aaaaaaa111"), (b, "bbbbbbb222")):
        (bundle / "app").mkdir()
        (bundle / "interface.json").write_text(json.dumps({"version": "0.3.0"}))
        (bundle / "bundle.json").write_text(json.dumps({"source": source}))
    env = install_env(tmp_path)
    link = tmp_path / "home/.local/share/gbfr-auto-linux/current"
    assert run_script(a / "install.sh", env).returncode == 0
    assert run_script(link / "install.sh", env).returncode == 0  # through the link itself
    assert os.readlink(link) == str(a.resolve())  # the real folder, never the link
    line = next(text for text in (ROOT / "assets/bundle/gbfr-control").read_text().splitlines()
                if text.startswith("here="))
    here = subprocess.run(["sh", "-c", line + '\nprintf %s "$here"', str(link / "gbfr-control")],
                          capture_output=True, text=True, timeout=30).stdout
    assert here == str(a.resolve())
    assert run_script(b / "install.sh", env).returncode == 0
    assert control_view.app_version(Path(here) / "app") == "0.3.0 (aaaaaaa)"
    assert control_view.app_version(link.resolve() / "app") == "0.3.0 (bbbbbbb)"


def test_uninstall_removes_a_link_whose_bundle_is_gone(tmp_path):
    import os
    import shutil
    from tools.build_maa import APP_ID
    old = bundle_with_scripts(tmp_path / "old")
    new = bundle_with_scripts(tmp_path / "new")
    env = install_env(tmp_path)
    share = tmp_path / "home/.local/share"
    assert run_script(old / "install.sh", env).returncode == 0
    shutil.rmtree(old)  # deleted by hand while the menu still pointed at it
    assert run_script(new / "uninstall.sh", env).returncode == 0
    assert not (share / "applications" / f"{APP_ID}.desktop").exists()
    assert not os.path.lexists(share / "gbfr-auto-linux/current")


def test_uninstall_leaves_no_file_where_a_deleted_icon_folder_was(tmp_path):
    import shutil
    bundle = bundle_with_scripts(tmp_path / "gbfr")
    env = install_env(tmp_path)
    theme = tmp_path / "home/.local/share/icons/hicolor"
    assert run_script(bundle / "install.sh", env).returncode == 0
    shutil.rmtree(theme)  # removed by hand while the menu entry stayed
    assert run_script(bundle / "uninstall.sh", env).returncode == 0
    assert not theme.exists()
    assert run_script(bundle / "install.sh", env).returncode == 0 and theme.is_dir()  # still works


def test_install_refuses_a_link_path_the_entry_cannot_hold_but_takes_any_bundle_path(tmp_path):
    bundle = bundle_with_scripts(tmp_path / "gbfr-$HOME")  # only the link goes into the entry
    env = install_env(tmp_path)
    assert run_script(bundle / "install.sh", env).returncode == 0
    share = tmp_path / "share-$x"
    result = run_script(bundle / "install.sh", dict(env, XDG_DATA_HOME=str(share)))
    assert result.returncode == 1 and not share.exists()


def test_the_launcher_opens_the_window_with_the_system_python():
    from tools.build_maa import ROOT
    text = (ROOT / "assets/bundle/gbfr-control").read_text()
    assert 'exec /usr/bin/python3 -I -B "$here/app/control_window.py" "$@"' in text
    assert "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" in text


def test_the_launcher_leaves_no_bytecode_in_the_release_directory(tmp_path):
    """The window imports its modules from app/; without -B, Python would write
    app/__pycache__ into a release directory that is never written to after its build.
    The launcher's own lines run, with this interpreter in place of the system one."""
    import shutil
    import subprocess
    import sys
    from tools.build_maa import ROOT
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    shutil.copy2(ROOT / "assets/bundle/gbfr-control", bundle / "gbfr-control")
    (bundle / "app/helper.py").write_text("VALUE = 1\n")
    (bundle / "app/control_window.py").write_text(  # as the real one: -I leaves its folder out
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "import helper\nprint(helper.VALUE)\n")
    lines = (bundle / "gbfr-control").read_text().splitlines()
    here = next(line for line in lines if line.startswith("here="))
    run = next(line for line in lines if line.startswith("exec /usr/bin/python3 "))
    script = here + "\n" + run.replace("/usr/bin/python3", sys.executable, 1)
    result = subprocess.run(["sh", "-c", script, str(bundle / "gbfr-control")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and result.stdout.strip() == "1", result.stderr
    assert not (bundle / "app/__pycache__").exists()
