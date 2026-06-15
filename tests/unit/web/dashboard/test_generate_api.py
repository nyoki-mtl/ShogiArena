from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shogiarena._core.interfaces.dashboard.generate.api import GenerateAPI


class _RuntimeSupportStub:
    def load_run_state(self, _run_dir: Path, **_kwargs: Any) -> dict[str, object]:
        return {
            "schema_version": 2,
            "schedule_hash": "schedule",
            "resume_hash": "resume",
        }


@pytest.mark.asyncio
async def test_generate_summary_reads_records_output_from_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    records_dir = tmp_path / "records"
    run_dir.mkdir()
    records_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": "resume"},
                "schedule": {"kind": "generate"},
                "records_output": {
                    "output_dir": str(records_dir),
                    "format": "sfen",
                    "file_prefix": "selfplay",
                },
                "rules": {"time_control": {"byoyomi": 1000}},
            }
        ),
        encoding="utf-8",
    )
    (records_dir / "records_manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": "a.sfen", "games": 2, "positions": 20, "bytes": 200},
                    {"path": "b.sfen", "games": 3, "positions": 30, "bytes": 300},
                ]
            }
        ),
        encoding="utf-8",
    )
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert payload["total_games"] == 5
    assert payload["total_positions"] == 50
    assert payload["total_bytes"] == 500
    assert payload["record_format"] == "sfen"
    assert payload["output_dir"] == str(records_dir)
    assert payload["file_prefix"] == "selfplay"
    assert payload["rules"] == {"time_control": {"byoyomi": 1000}}
    assert payload["run_status"] == "provenance_sealed"
    assert payload["is_resumable"] is True


@pytest.mark.asyncio
async def test_generate_summary_missing_manifest_is_empty_not_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()  # no manifest.json / records_manifest.json -> missing optional artifacts
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["total_games"] == 0
    assert payload["file_count"] == 0


@pytest.mark.asyncio
async def test_generate_summary_malformed_manifest_returns_500(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text("{ this is not valid json", encoding="utf-8")
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]

    assert response.status == 500
    assert json.loads(response.text)["code"] == "corrupt_run_artifact"


@pytest.mark.asyncio
async def test_generate_summary_corrupt_records_manifest_returns_500(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    records_dir = tmp_path / "records"
    run_dir.mkdir()
    records_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"records_output": {"output_dir": str(records_dir)}}), encoding="utf-8"
    )
    # records_manifest.json exists but holds corrupt JSON (e.g. a truncated write).
    (records_dir / "records_manifest.json").write_text('{"files": [', encoding="utf-8")
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]

    assert response.status == 500
    assert json.loads(response.text)["code"] == "corrupt_run_artifact"


@pytest.mark.asyncio
async def test_generate_summary_schema_invalid_state_returns_500(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # 'config' must be a mapping; a scalar here makes state.json schema-invalid.
    (run_dir / "state.json").write_text(json.dumps({"config": 123}), encoding="utf-8")
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]

    assert response.status == 500
    assert json.loads(response.text)["code"] == "corrupt_run_artifact"


@pytest.mark.asyncio
async def test_generate_summary_malformed_state_returns_500(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Corrupt/unparseable state.json must surface, not silently read as a fresh run.
    (run_dir / "state.json").write_text("{ not valid json", encoding="utf-8")
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]

    assert response.status == 500
    assert json.loads(response.text)["code"] == "corrupt_run_artifact"


class _GameQueryStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def build_games_raw_payload(
        self, db_path: Path, *, limit: int, offset: int, search_query: str | None
    ) -> dict[str, object]:
        self.calls.append({"db_path": db_path, "limit": limit, "offset": offset, "search_query": search_query})
        return {
            "games": [{"game_id": "gen-1", "black_player": "A", "white_player": "B"}],
            "total": 1,
            "offset": offset,
            "limit": limit,
        }


class _RaisingGameQueryStub:
    def build_games_raw_payload(
        self, db_path: Path, *, limit: int, offset: int, search_query: str | None
    ) -> dict[str, object]:
        raise RuntimeError("game.db is corrupt")


@pytest.mark.asyncio
async def test_get_games_converts_service_failure_to_500(tmp_path: Path) -> None:
    from aiohttp.test_utils import make_mocked_request

    api = GenerateAPI(
        db_path=tmp_path / "game.db",
        run_dir=tmp_path,
        runtime_support=_RuntimeSupportStub(),
        game_query=_RaisingGameQueryStub(),
    )
    request = make_mocked_request("GET", "/api/generate/games?limit=25&offset=0")
    response = await api.get_games(request)

    assert response.status == 500
    assert json.loads(response.text)["code"] == "games_query_failed"


@pytest.mark.asyncio
async def test_get_games_returns_generated_games(tmp_path: Path) -> None:
    from aiohttp.test_utils import make_mocked_request

    game_query = _GameQueryStub()
    api = GenerateAPI(
        db_path=tmp_path / "game.db",
        run_dir=tmp_path,
        runtime_support=_RuntimeSupportStub(),
        game_query=game_query,
    )
    request = make_mocked_request("GET", "/api/generate/games?limit=25&offset=0")
    response = await api.get_games(request)
    payload = json.loads(response.text)

    # Regression for the stub that always returned an empty list with total 0.
    assert payload["games"] == [{"game_id": "gen-1", "black_player": "A", "white_player": "B"}]
    assert payload["total"] == 1
    assert game_query.calls[0]["limit"] == 25
    assert game_query.calls[0]["search_query"] is None
