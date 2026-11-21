# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib

import pytest

from tools.build_maa import build, fetch


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
