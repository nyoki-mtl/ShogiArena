from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.shared.kernel.run_manifest_reader import (
    is_resumable_manifest,
    read_sealed_manifest_resume_hash,
    sealed_manifest_resume_hash,
)


def test_sealed_manifest_resume_hash_rejects_empty_hash() -> None:
    manifest = {"status": "provenance_sealed", "hashes": {"resume_hash": ""}}

    assert sealed_manifest_resume_hash(manifest) is None
    assert is_resumable_manifest(manifest) is False


def test_read_sealed_manifest_resume_hash_requires_sealed_status(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"status": "inputs_only", "hashes": {"resume_hash": "resume"}}), encoding="utf-8")

    assert read_sealed_manifest_resume_hash(path) is None


def test_read_sealed_manifest_resume_hash_returns_hash(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"status": "provenance_sealed", "hashes": {"resume_hash": "resume"}}),
        encoding="utf-8",
    )

    assert read_sealed_manifest_resume_hash(path) == "resume"
