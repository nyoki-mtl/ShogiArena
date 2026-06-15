"""Tests for EloRatingService game-id idempotency and resume replay (O9 rating recompute)."""

from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.elo_rating_service import EloRatingService
from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult


def _service() -> EloRatingService:
    return EloRatingService(initial_rating=1500.0, k_factor=32.0)


def _game(black: str, white: str, result: GameResult, game_name: str) -> GameRecordPlayers:
    return {"black_player": black, "white_player": white, "result": result, "game_name": game_name}


def test_update_ratings_dedups_by_game_id() -> None:
    service = _service()
    first = service.update_ratings("a", "b", GameResult.BLACK_WIN, game_id="g1")
    # Re-applying the same game must not move the ratings again.
    second = service.update_ratings("a", "b", GameResult.BLACK_WIN, game_id="g1")
    assert second == first


def test_update_ratings_without_game_id_is_unchanged() -> None:
    # Backward compatibility: callers that do not pass game_id still apply every call.
    service = _service()
    first = service.update_ratings("a", "b", GameResult.BLACK_WIN)
    second = service.update_ratings("a", "b", GameResult.BLACK_WIN)
    assert second != first  # applied twice


def test_restore_from_games_matches_incremental_application() -> None:
    incremental = _service()
    incremental.update_ratings("a", "b", GameResult.BLACK_WIN, game_id="g0001")
    incremental.update_ratings("b", "a", GameResult.BLACK_WIN, game_id="g0002")

    games = [
        _game("a", "b", GameResult.BLACK_WIN, "g0001"),
        _game("b", "a", GameResult.BLACK_WIN, "g0002"),
    ]
    restored = _service()
    applied = restored.restore_from_games(games)

    assert applied == 2
    assert restored._current_ratings == incremental._current_ratings


def test_restore_then_live_update_of_same_game_does_not_double_count() -> None:
    games = [_game("a", "b", GameResult.BLACK_WIN, "g1")]
    service = _service()
    service.restore_from_games(games)
    after_restore = dict(service._current_ratings)

    # A duplicate completion of an already-restored game must be a no-op.
    service.update_ratings("a", "b", GameResult.BLACK_WIN, game_id="g1")
    assert service._current_ratings == after_restore


def test_restore_is_idempotent_when_called_twice() -> None:
    games = [_game("a", "b", GameResult.BLACK_WIN, "g1"), _game("a", "b", GameResult.WHITE_WIN, "g2")]
    service = _service()
    service.restore_from_games(games)
    snapshot = dict(service._current_ratings)

    second = service.restore_from_games(games)
    assert second == 0  # nothing new applied
    assert service._current_ratings == snapshot


def test_restore_skips_rows_missing_fields() -> None:
    service = _service()
    applied = service.restore_from_games(
        [
            _game("", "b", GameResult.BLACK_WIN, "g1"),  # missing black player
            _game("a", "b", GameResult.BLACK_WIN, ""),  # missing game name
        ]
    )
    assert applied == 0
    assert service._current_ratings == {}


@pytest.mark.parametrize("result", [GameResult.BLACK_WIN, GameResult.WHITE_WIN, GameResult.DRAW_BY_REPETITION])
def test_restore_handles_all_decisive_and_draw_results(result: GameResult) -> None:
    service = _service()
    applied = service.restore_from_games([_game("a", "b", result, "g1")])
    assert applied == 1
