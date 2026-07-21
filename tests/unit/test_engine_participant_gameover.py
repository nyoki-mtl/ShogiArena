"""Unit tests for EngineParticipant gameover-token mapping."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rsshogi.types import Color

from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.shared.kernel.game_results import GameResult

# _map_game_result only reads self._role and self.name, so a lightweight stand-in suffices.
_map = EngineParticipant._map_game_result


def _participant(role: Color | None) -> object:
    return SimpleNamespace(_role=role, name="Engine")


@pytest.mark.parametrize(
    ("role", "result", "expected"),
    [
        (Color.BLACK, GameResult.BLACK_WIN, "win"),
        (Color.BLACK, GameResult.WHITE_WIN, "lose"),
        (Color.WHITE, GameResult.WHITE_WIN, "win"),
        (Color.WHITE, GameResult.BLACK_WIN, "lose"),
        (Color.BLACK, GameResult.DRAW_BY_REPETITION, "draw"),
        (Color.WHITE, GameResult.DRAW_BY_MAX_PLIES, "draw"),
    ],
)
def test_map_game_result_decided_tokens(role: Color, result: GameResult, expected: str) -> None:
    assert _map(_participant(role), result) == expected


@pytest.mark.parametrize("result", [GameResult.PAUSED, GameResult.ERROR, GameResult.INVALID])
def test_map_game_result_non_decided_returns_none(result: GameResult) -> None:
    # Non-decided outcomes have no gameover token and must not be rounded to "lose".
    assert _map(_participant(Color.BLACK), result) is None


def test_map_game_result_win_without_role_fails_fast() -> None:
    # A decisive result with no assigned role is a wiring bug; fail fast rather than guess a token.
    with pytest.raises(ValueError, match="role"):
        _map(_participant(None), GameResult.BLACK_WIN)


def test_map_game_result_draw_without_role_is_allowed() -> None:
    # A draw token is role-independent, so it does not require a role.
    assert _map(_participant(None), GameResult.DRAW_BY_REPETITION) == "draw"
