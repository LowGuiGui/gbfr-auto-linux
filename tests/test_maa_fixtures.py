# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Native recognition of every checked leaf on measured game frames.

Each fixture is a frame captured from the game on 2026-11-21 (Maniac Quakadile,
simplified Chinese, 1280x720), blacked out outside the leaf regions by
`tools/maa_fixtures.py`. Every fixture states the exact set of leaves that must hit,
so each one is a positive case for its own leaves and a confusable negative for the
rest (the other toggle state, the other results page, the dialog over the page).

OCR needs the PaddleOCR models that `tools/build_maa.py` puts in a bundle. Point
GBFR_MAA_OCR_MODEL at a `model` directory, or keep a built bundle under `dist/`;
without either the test is skipped.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("maa.tasker", reason="MaaFw is not installed")

from tools.maa_fixtures import LEAVES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/maa"

CASES = {
    "results-titles": {"GBFR_See_Results"},
    # The page's reveal animation: the toggle already reads, but presses are ignored.
    "results-rewards-revealing": {"GBFR_See_Results", "GBFR_See_RepeatOff"},
    "results-rewards-repeat-off": {"GBFR_See_Results", "GBFR_See_RepeatOff"},
    "results-rewards-repeat-on": {"GBFR_See_Results", "GBFR_See_RepeatOn"},
    "results-rewards-repeat-on-later-run": {"GBFR_See_Results", "GBFR_See_RepeatOn"},
    # The dialog blurs the page behind it: neither the page nor its toggle is read.
    "tenth-run-dialog": {"GBFR_See_TenthRun"},
    "results-rewards-off-after-dialog": {"GBFR_See_Results", "GBFR_See_RepeatOff"},
    # With repeat on, the field after the kill shows loot and "return to town": no leaf.
    "field-after-kill-repeat-on": set(),
    "victory": {"GBFR_See_Victory"},
    "town": {"GBFR_See_Town"},
    "town-after-tenth-run": {"GBFR_See_Town"},
    "loading-tips": {"GBFR_See_Tips"},
    "battle": {"GBFR_See_Battle"},
}


def ocr_models():
    configured = os.environ.get("GBFR_MAA_OCR_MODEL")
    candidates = [Path(configured)] if configured else []
    candidates += sorted(ROOT.glob("dist/maa-*/resource/model"), reverse=True)
    return next((path for path in candidates if (path / "ocr/rec.onnx").is_file()), None)


@pytest.fixture(scope="module")
def resource(tmp_path_factory):
    import shutil

    models = ocr_models()
    if models is None:
        pytest.skip("No OCR model: set GBFR_MAA_OCR_MODEL or build a bundle under dist/")
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    base = tmp_path_factory.mktemp("resource")
    shutil.copytree(ROOT / "assets/resource", base / "resource")
    (base / "resource/model").symlink_to(models.resolve(), target_is_directory=True)
    Tasker.set_log_dir(base / "maa-log")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    return base / "resource"


def test_every_fixture_has_a_case():
    assert {path.stem for path in FIXTURES.glob("*.jpg")} == CASES.keys()


@pytest.mark.parametrize("name", sorted(CASES))
def test_exactly_the_expected_leaves_hit(resource, name):
    from tools.maa_replay import replay

    hits = replay(resource, FIXTURES / f"{name}.jpg", LEAVES)
    assert {leaf for leaf, hit in hits.items() if hit} == CASES[name]
