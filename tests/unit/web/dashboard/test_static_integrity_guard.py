"""Static integrity guard テスト。

build-id 一致/不一致/欠落時の動作を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.interfaces.dashboard.static_handler import StaticAssetsHandler


@pytest.fixture()
def tmp_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create temporary run_static and builtin_static directories."""
    run_static = tmp_path / "run_static"
    builtin_static = tmp_path / "builtin_static"
    run_static.mkdir()
    builtin_static.mkdir()
    return run_static, builtin_static


def _write_build_meta(directory: Path, build_id: str) -> None:
    meta = {"build_id": build_id, "built_at": 1000}
    (directory / "build-meta.json").write_text(json.dumps(meta), encoding="utf-8")


class TestCheckBuildIntegrity:
    """_check_build_integrity tests."""

    def test_matching_build_ids(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        _write_build_meta(run_static, "abc123")
        _write_build_meta(builtin_static, "abc123")
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is True

    def test_mismatched_build_ids(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        _write_build_meta(run_static, "abc123")
        _write_build_meta(builtin_static, "def456")
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is False

    def test_missing_run_meta(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        _write_build_meta(builtin_static, "abc123")
        # No build-meta.json in run_static
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is True

    def test_missing_builtin_meta(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        _write_build_meta(run_static, "abc123")
        # No build-meta.json in builtin_static
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is True

    def test_both_missing(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is True

    def test_invalid_json_in_run(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        (run_static / "build-meta.json").write_text("not json", encoding="utf-8")
        _write_build_meta(builtin_static, "abc123")
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is False

    def test_invalid_json_in_builtin(self, tmp_dirs: tuple[Path, Path]) -> None:
        run_static, builtin_static = tmp_dirs
        _write_build_meta(run_static, "abc123")
        (builtin_static / "build-meta.json").write_text("{invalid", encoding="utf-8")
        assert StaticAssetsHandler._check_build_integrity(run_static, builtin_static) is False
