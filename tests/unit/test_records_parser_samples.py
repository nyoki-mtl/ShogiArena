from __future__ import annotations

import os
from pathlib import Path

import pytest
import rshogi

from shogiarena.utils.types.types import GameResult

_KIF_ENV = "SHOGIARENA_KIF_SAMPLE_DIR"
_CSA_ENV = "SHOGIARENA_CSA_SAMPLE_DIR"


def _sample_files(root: Path | None, suffix: str, limit: int = 2) -> list[Path]:
    if root is None:
        pytest.skip(f"Missing sample directory env: {suffix}")
    if not root.exists():
        pytest.skip(f"Missing sample directory: {root}")
    files = sorted(root.rglob(f"*{suffix}"))
    if not files:
        pytest.skip(f"No {suffix} files under {root}")
    return files[:limit]


def _assert_parse_shape(record: object) -> None:
    assert type(record).__name__ == "GameRecord"
    assert isinstance(record.init_position_sfen, str)
    payload = record.to_dict()
    result = payload.get("result")
    assert isinstance(result, dict)
    assert isinstance(int(record.result), int)
    assert result.get("result") in {member.name for member in GameResult}
    moves = payload.get("moves")
    assert isinstance(moves, list)


def test_parse_kif_samples() -> None:
    kif_root = Path(os.environ[_KIF_ENV]) if _KIF_ENV in os.environ else None
    for path in _sample_files(kif_root, ".kif"):
        parsed = rshogi.record.GameRecord.from_kif_file(path)
        _assert_parse_shape(parsed)


def test_parse_csa_samples() -> None:
    csa_root = Path(os.environ[_CSA_ENV]) if _CSA_ENV in os.environ else None
    for path in _sample_files(csa_root, ".csa"):
        parsed = rshogi.record.GameRecord.from_csa_file(path)
        _assert_parse_shape(parsed)
