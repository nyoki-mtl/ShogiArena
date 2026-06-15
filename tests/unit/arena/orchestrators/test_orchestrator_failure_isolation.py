"""Tests for O10 mode-based failure handling in TournamentOrchestrator.run_pending_item."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.tournament.adapters.orchestrator import TournamentOrchestrator
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def _spec(initial_sfen: str = "startpos") -> GameSpec:
    return GameSpec(black_engine="b", white_engine="w", initial_sfen=initial_sfen, game_id="g0001-x", round_num=0)


def _orch(*, sprt: object | None, openbench: object | None) -> TournamentOrchestrator:
    orch = object.__new__(TournamentOrchestrator)
    orch.config = SimpleNamespace(sprt=sprt, openbench=openbench)  # type: ignore[attr-defined]

    async def _boom(_item: GameSpec) -> None:
        raise RuntimeError("boom")

    orch._run_game = _boom  # type: ignore[assignment]
    return orch


@pytest.mark.asyncio
async def test_plain_tournament_isolates_failure_as_error_completion() -> None:
    orch = _orch(sprt=None, openbench=None)
    emitted: list[tuple[str, str]] = []

    async def _emit(*, game_id: str, game_info: object, payload: object, worker_idx: int | None) -> None:
        emitted.append((game_id, game_info.result.name))  # type: ignore[attr-defined]

    orch._emit_game_completion = _emit  # type: ignore[assignment]

    # Isolated: no exception is raised, and an ERROR completion is emitted so the game is terminal.
    await orch.run_pending_item(_spec())

    assert emitted == [("g0001-x", "ERROR")]


@pytest.mark.asyncio
async def test_sprt_run_fails_fast_on_game_failure() -> None:
    orch = _orch(sprt=object(), openbench=None)

    async def _emit(**_kwargs: object) -> None:
        raise AssertionError("fail-fast must not emit an ERROR completion")

    orch._emit_game_completion = _emit  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="boom"):
        await orch.run_pending_item(_spec())


def test_build_error_game_record_expands_startpos_and_carries_metadata() -> None:
    # Regression: "startpos" must be expanded to a full board SFEN (from_dict rejects "startpos").
    record = TournamentOrchestrator._build_error_game_record(_spec(initial_sfen="startpos"))
    assert record.result.name == "ERROR"
    assert record.metadata.black_player == "b"
    assert record.metadata.white_player == "w"
    assert len(record.moves) == 0
    assert "startpos" not in record.init_position_sfen
    assert record.updated_date is not None  # required by the DB persistence path


def test_error_record_persists_through_db_store(tmp_path: Path) -> None:
    # Regression: the synthesized ERROR record must satisfy the DB persistence contract
    # (updated_date / dates / game_name), not raise on append.
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)

    record = TournamentOrchestrator._build_error_game_record(_spec(initial_sfen="startpos"))
    store.append([record])  # must not raise

    loaded = store.load(game_name="g0001-x")
    assert loaded is not None
    assert loaded.result.name == "ERROR"
