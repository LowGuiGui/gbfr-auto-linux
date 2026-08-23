# -*- coding: utf-8 -*-
"""#3 —— 升级时刷新没被改过的模板，绝不覆盖用户改过的。"""

import json

import pytest

import main


class Extractor:
    """借用真实的模板释放逻辑，把 exe_dir / resource_path 指到 tmp。"""

    _init_template_dir = main.App._init_template_dir
    _copy_template = main.App._copy_template
    _offer_new_template = main.App._offer_new_template
    _read_template_manifest = main.App._read_template_manifest
    _write_template_manifest = main.App._write_template_manifest

    def __init__(self):
        self.ui = []

    def log(self, message):
        self.ui.append(message)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """bundled/ 是打包进程序的模板，exe/ 是可执行文件所在目录。"""
    bundled = tmp_path / "bundled"
    exe = tmp_path / "exe"
    bundled.mkdir()
    exe.mkdir()

    monkeypatch.setattr(main, "exe_dir", lambda: str(exe))
    monkeypatch.setattr(
        main, "resource_path",
        lambda rel: str(bundled / rel.split("/")[-1]),
    )

    class World:
        template_dir = exe / "template"

        @staticmethod
        def ship(version):
            for name in main.TEMPLATE_FILES:
                (bundled / name).write_bytes(f"{version}-{name}".encode())

        @staticmethod
        def run():
            app = Extractor()
            app._init_template_dir()
            return app

        @staticmethod
        def read(name):
            return (World.template_dir / name).read_bytes().decode()

        @staticmethod
        def manifest():
            return json.loads((World.template_dir / main.TEMPLATE_MANIFEST).read_text())

        @staticmethod
        def listing():
            return sorted(p.name for p in World.template_dir.iterdir())

    return World


def test_first_run_extracts_everything(world, log_file):
    world.ship("v1")
    app = world.run()
    assert set(main.TEMPLATE_FILES) <= set(world.listing())
    assert len(world.manifest()) == len(main.TEMPLATE_FILES)
    assert "已生成 5 个模板文件" in app.ui[0]


def test_untouched_files_refresh_on_upgrade(world, log_file):
    world.ship("v1")
    world.run()
    world.ship("v2")
    app = world.run()
    for name in main.TEMPLATE_FILES:
        assert world.read(name) == f"v2-{name}"
    assert any("已更新" in line for line in app.ui)


def test_user_edits_survive_an_upgrade(world, log_file):
    world.ship("v1")
    world.run()
    (world.template_dir / "flag_battle.png").write_bytes(b"MY-1920x1080-VERSION")

    world.ship("v2")
    world.run()

    assert world.read("flag_battle.png") == "MY-1920x1080-VERSION"
    assert world.read("flag_again.png") == "v2-flag_again.png"


def test_the_new_version_is_offered_alongside(world, log_file):
    world.ship("v1")
    world.run()
    (world.template_dir / "flag_battle.png").write_bytes(b"MY-VERSION")
    world.ship("v2")
    world.run()

    assert world.read("flag_battle.png.new") == "v2-flag_battle.png"
    assert "已原样保留" in log_file()


def test_rerunning_changes_nothing(world, log_file):
    world.ship("v1")
    world.run()
    before = {n: world.read(n) for n in world.listing()}
    app = world.run()
    assert {n: world.read(n) for n in world.listing()} == before
    assert app.ui == []


def test_legacy_directory_without_a_manifest_is_left_alone(world, log_file):
    """老版本升上来：无从证明文件没被改过，就一律不覆盖。"""
    world.ship("v1")
    world.template_dir.mkdir()
    for name in main.TEMPLATE_FILES:
        (world.template_dir / name).write_bytes(f"v1-{name}".encode())

    world.ship("v2")
    world.run()

    for name in main.TEMPLATE_FILES:
        assert world.read(name) == f"v1-{name}"
        assert world.read(name + ".new") == f"v2-{name}"


def test_identical_content_backfills_the_manifest_without_copying(world, log_file):
    world.ship("v2")
    world.template_dir.mkdir()
    for name in main.TEMPLATE_FILES:
        (world.template_dir / name).write_bytes(f"v2-{name}".encode())

    app = world.run()
    assert len(world.manifest()) == len(main.TEMPLATE_FILES)
    assert not any(n.endswith(".new") for n in world.listing())
    assert app.ui == []


def test_source_checkout_is_a_no_op(tmp_path, monkeypatch, log_file):
    """源码运行时 exe_dir() 就是仓库根，"外部"目录其实就是被跟踪的 template/。"""
    repo = tmp_path / "repo"
    (repo / "template").mkdir(parents=True)
    for name in main.TEMPLATE_FILES:
        (repo / "template" / name).write_bytes(f"real-{name}".encode())

    monkeypatch.setattr(main, "exe_dir", lambda: str(repo))
    monkeypatch.setattr(main, "resource_path", lambda rel: str(repo / rel))

    before = {n: (repo / "template" / n).read_bytes() for n in main.TEMPLATE_FILES}
    Extractor()._init_template_dir()
    Extractor()._init_template_dir()

    assert {n: (repo / "template" / n).read_bytes() for n in main.TEMPLATE_FILES} == before
    assert not any(p.name.endswith(".new") for p in (repo / "template").iterdir())
