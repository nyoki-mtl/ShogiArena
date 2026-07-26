"""completed 集合と ``game.db`` の不変条件（task 0052 / review finding M3・Decision 8）。

- terminal record は stop 要求の有無にかかわらず保存する。
- 保存できない中断は ``PAUSED`` として non-terminal に留め、resume で再実行できるようにする。
- policy 違反は state を変更する **前** に fail-fast させ、部分 commit を残さない。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.ports.completion_runtime import (
    CompletionMetadataContext,
    CompletionOpenBenchContext,
    CompletionPersistenceContext,
    CompletionRatingContext,
    CompletionRuntimeContext,
    CompletionStateContext,
)
from shogiarena._core.shared.kernel.game_results import GameResult


class _RecordMetadataStub:
    def __init__(self, attributes: dict[str, str]) -> None:
        self.attributes = attributes
        self.start_date = datetime(2026, 7, 25, 0, 0, 0)
        self.end_date = datetime(2026, 7, 25, 0, 1, 0)


class _RecordStub:
    def __init__(self, result: GameResult, *, timeout_origin: str | None = None) -> None:
        attributes: dict[str, str] = {"game_name": "g0001"}
        if timeout_origin is not None:
            attributes["timeout_origin"] = timeout_origin
        self.result = result
        self.moves: list[str] = []
        self.metadata = _RecordMetadataStub(attributes)

    def update_metadata(self, values: dict[str, str]) -> None:
        del values

    def set_metadata_attribute(self, key: str, value: str) -> None:
        self.metadata.attributes[key] = value


class _DbStub:
    def __init__(self) -> None:
        self.appended: list[_RecordStub] = []

    def append_record_list(self, records: list[_RecordStub]) -> None:
        self.appended.extend(records)

    def get_game_id_by_name(self, name: str) -> int | None:
        del name
        return None


class _RatingStub:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, GameResult]] = []

    def update_ratings(self, black: str, white: str, result: GameResult, *, game_id: str) -> None:
        del game_id
        self.updates.append((black, white, result))


class _SprtStub:
    model = "trinomial"

    def __init__(self) -> None:
        self.results: list[GameResult] = []
        self.games_played = 0

    def add_game_result(self, result: GameResult) -> None:
        self.results.append(result)
        self.games_played += 1

    def is_finished(self) -> bool:
        return False


def _game_spec() -> Any:
    return SimpleNamespace(
        game_id="g0001",
        black_engine="black",
        white_engine="white",
        round_num=0,
        initial_sfen="startpos",
    )


def _context(
    *,
    db: _DbStub | None = None,
    rating: _RatingStub | None = None,
    sprt: _SprtStub | None = None,
    saved: list[None] | None = None,
) -> CompletionRuntimeContext:
    async def sync_after_game() -> None:
        return None

    return CompletionRuntimeContext(
        metadata=CompletionMetadataContext(
            is_generate_run=False,
            summary_source="tournament",
            experiment_name=None,
            record_format=None,
        ),
        persistence=CompletionPersistenceContext(db_service=cast(Any, db), record_writer=None),
        rating=CompletionRatingContext(rating_service=cast(Any, rating)),
        state=CompletionStateContext(
            completed_game_ids=set(),
            completed_game_summaries={},
            sprt_service=cast(Any, sprt),
            sprt_pair=("black", "white") if sprt is not None else None,
            sprt_min_games=0,
            stop_controller=cast(Any, SimpleNamespace(request_stop=lambda **_kwargs: None)),
            is_dashboard_enabled=False,
            total_games=1,
            save_run_state=lambda: (saved if saved is not None else []).append(None),
            consecutive_invalid_timeouts_by_origin={},
            invalid_timeouts_by_origin={},
        ),
        openbench=CompletionOpenBenchContext(is_strict_mode=False, sync_after_game=sync_after_game),
    )


async def _complete(
    context: CompletionRuntimeContext,
    record: _RecordStub,
    *,
    is_stop_requested: bool,
) -> tuple[bool, GameResult]:
    service = TournamentSessionCompletionService()
    return await service.process_game_completion(
        context,
        _game_spec(),
        record=cast(Any, record),
        is_stop_requested=is_stop_requested,
        extract_participation=lambda _record: (),
        openbench_error_type=RuntimeError,
    )


def test_terminal_error_is_persisted_even_while_stopping() -> None:
    service = TournamentSessionCompletionService()

    assert service.should_persist_record(GameResult.ERROR, is_stop_requested=True) is True
    assert service.should_persist_record(GameResult.ERROR, is_stop_requested=False) is True
    # PAUSED だけが non-terminal。
    assert service.should_persist_record(GameResult.PAUSED, is_stop_requested=False) is False


@pytest.mark.asyncio
async def test_late_invalid_timeout_error_reaches_both_the_db_and_completed_set() -> None:
    """stop 要求中に到着した ERROR も DB と completed 集合の双方へ入る（不変条件）。"""

    db = _DbStub()
    context = _context(db=db)
    record = _RecordStub(GameResult.ERROR, timeout_origin="orchestrator_stall")

    _update_dashboard, result = await _complete(context, record, is_stop_requested=True)

    assert result == GameResult.ERROR
    assert [r.metadata.attributes["game_name"] for r in db.appended] == ["g0001"]
    assert context.state.completed_game_ids == {"g0001"}


@pytest.mark.asyncio
async def test_paused_completion_is_neither_persisted_nor_completed() -> None:
    db = _DbStub()
    context = _context(db=db)

    _update_dashboard, result = await _complete(context, _RecordStub(GameResult.PAUSED), is_stop_requested=True)

    assert result == GameResult.PAUSED
    assert db.appended == []
    assert context.state.completed_game_ids == set()


@pytest.mark.asyncio
async def test_unclassified_error_fails_before_mutating_state() -> None:
    """SPRT が扱えない ERROR は state を変更する前に raise する（部分 commit を残さない）。"""

    db = _DbStub()
    rating = _RatingStub()
    sprt = _SprtStub()
    context = _context(db=db, rating=rating, sprt=sprt)

    with pytest.raises(ValueError, match="non-decisive game result"):
        await _complete(context, _RecordStub(GameResult.ERROR), is_stop_requested=False)

    assert db.appended == []
    assert rating.updates == []
    assert sprt.results == []
    assert context.state.completed_game_ids == set()
    assert context.state.completed_game_summaries == {}


@pytest.mark.asyncio
async def test_invalid_timeout_error_is_excluded_from_the_sprt_sample_but_still_completes() -> None:
    db = _DbStub()
    sprt = _SprtStub()
    context = _context(db=db, sprt=sprt)

    _update_dashboard, result = await _complete(
        context,
        _RecordStub(GameResult.ERROR, timeout_origin="unknown"),
        is_stop_requested=False,
    )

    assert result == GameResult.ERROR
    assert sprt.results == []
    assert context.state.completed_game_ids == {"g0001"}
    assert len(db.appended) == 1


@pytest.mark.asyncio
async def test_duplicate_completion_does_not_double_count() -> None:
    db = _DbStub()
    rating = _RatingStub()
    sprt = _SprtStub()
    context = _context(db=db, rating=rating, sprt=sprt)

    await _complete(context, _RecordStub(GameResult.BLACK_WIN), is_stop_requested=False)
    await _complete(context, _RecordStub(GameResult.BLACK_WIN), is_stop_requested=False)

    assert len(db.appended) == 1
    assert len(rating.updates) == 1
    assert sprt.games_played == 1
    assert context.state.completed_game_ids == {"g0001"}
