"""result_store モジュールのテスト。

不正データに対する復元処理の頑健性を検証する。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from shogiarena.arena.services.persistence.result_store import (
    RunStorageResultStore,
    _parse_datetime,
)


def _make_storage(run_dir: Path) -> MagicMock:
    """テスト用の RunStorage モック。"""
    storage = MagicMock()
    storage.run_dir = run_dir
    return storage


# ── _parse_datetime ──────────────────────────────────────────────


class TestParseDatetime:
    def test_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_valid_iso(self) -> None:
        result = _parse_datetime("2024-01-01T00:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_invalid_iso_returns_none(self) -> None:
        assert _parse_datetime("not-iso") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_datetime("") is None

    def test_partial_iso_returns_none(self) -> None:
        assert _parse_datetime("2024-13-01") is None


# ── SpsaRunResult final_params 検証 ─────────────────────────────


class TestFinalParamsValidation:
    def test_str_values_are_rejected(self, tmp_path: Path) -> None:
        """str 値が Mapping[str, float] として通過しないことを検証。"""
        payload = {
            "run_id": "test-1",
            "type": "SpsaRunResult",
            "run_dir": str(tmp_path),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {
                "final_params": {"param_a": "not-a-float", "param_b": 1.5},
            },
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-1")
        assert result is not None
        # param_a (str) は除外され、param_b (float) のみ残る
        assert dict(result.final_params) == {"param_b": 1.5}  # type: ignore[attr-defined]

    def test_all_valid_floats(self, tmp_path: Path) -> None:
        payload = {
            "run_id": "test-2",
            "type": "SpsaRunResult",
            "run_dir": str(tmp_path),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {
                "final_params": {"x": 1.0, "y": -2.5, "z": 0},
            },
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-2")
        assert result is not None
        assert dict(result.final_params) == {"x": 1.0, "y": -2.5, "z": 0.0}  # type: ignore[attr-defined]

    def test_empty_final_params(self, tmp_path: Path) -> None:
        payload = {
            "run_id": "test-3",
            "type": "SpsaRunResult",
            "run_dir": str(tmp_path),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {"final_params": {}},
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-3")
        assert result is not None
        assert dict(result.final_params) == {}  # type: ignore[attr-defined]

    def test_nan_inf_values_rejected(self, tmp_path: Path) -> None:
        """NaN / Inf 値が final_params から除外されることを検証。"""
        payload = {
            "run_id": "test-4",
            "type": "SpsaRunResult",
            "run_dir": str(tmp_path),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {
                "final_params": {"good": 1.0, "nan_val": float("nan"), "inf_val": float("inf")},
            },
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-4")
        assert result is not None
        assert dict(result.final_params) == {"good": 1.0}  # type: ignore[attr-defined]


# ── 不正な started_at での load ──────────────────────────────────


class TestInvalidDatetimeLoad:
    def test_invalid_started_at_does_not_crash(self, tmp_path: Path) -> None:
        """started_at に不正な ISO 文字列が入っていても load_result が落ちないことを検証。"""
        payload = {
            "run_id": "test-dt",
            "type": "SpsaRunResult",
            "run_dir": str(tmp_path),
            "started_at": "not-iso",
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {"final_params": {"a": 1.0}},
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-dt")
        assert result is not None
        assert result.started_at is None

    def test_unknown_result_type(self, tmp_path: Path) -> None:
        payload = {
            "run_id": "test-unknown",
            "type": "UnknownResultType",
            "run_dir": str(tmp_path),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "summary": {},
            "config_snapshot": {},
            "result": {},
        }
        storage = _make_storage(tmp_path)
        storage.read_json.return_value = payload
        store = RunStorageResultStore(storage)
        result = store.load_result("test-unknown")
        assert result is None
