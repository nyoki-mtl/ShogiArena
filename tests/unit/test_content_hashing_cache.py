from __future__ import annotations

from pathlib import Path

import pytest

import shogiarena._core.shared.kernel.content_hashing as content_hashing


def test_cached_file_hash_reuses_unchanged_content_and_invalidates_on_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "engine"
    path.write_bytes(b"engine-v1")
    original = content_hashing.sha256_file
    calls = 0

    def counting_sha256_file(candidate: Path) -> str:
        nonlocal calls
        calls += 1
        return original(candidate)

    monkeypatch.setattr(content_hashing, "sha256_file", counting_sha256_file)

    first = content_hashing.sha256_file_cached(path)
    second = content_hashing.sha256_file_cached(path)
    path.write_bytes(b"engine-version-2")
    changed = content_hashing.sha256_file_cached(path)

    assert first == second
    assert changed != first
    assert calls == 2


def test_cached_tree_hash_reuses_unchanged_content_and_invalidates_on_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "eval"
    tree.mkdir()
    model = tree / "model.bin"
    model.write_bytes(b"model-v1")
    original = content_hashing.sha256_tree
    calls = 0

    def counting_sha256_tree(candidate: Path) -> str:
        nonlocal calls
        calls += 1
        return original(candidate)

    monkeypatch.setattr(content_hashing, "sha256_tree", counting_sha256_tree)

    first = content_hashing.sha256_path_cached(tree)
    second = content_hashing.sha256_path_cached(tree)
    model.write_bytes(b"model-version-2")
    changed = content_hashing.sha256_path_cached(tree)

    assert first == second
    assert changed != first
    assert calls == 2
