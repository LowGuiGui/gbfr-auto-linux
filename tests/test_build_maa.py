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
