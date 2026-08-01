"""Typed Local/Remote game result contract tests。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_pair_execution import (
    _extract_result_envelope,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionResult


def test_remote_result_envelope_uses_same_typed_classification_as_local() -> None:
    local = GameExecutionResult(
        execution_digest="a" * 64,
        game_id="game-1",
        classification="DRAW_BY_REPETITION",
    )
    remote = _extract_result_envelope(  # noqa: SLF001
        [
            {"type": "move_progress", "game_result": "DRAW_BY_REPETITION"},
            local.model_dump(mode="json"),
        ]
    )

    assert remote == local


def test_result_contract_rejects_unknown_classification() -> None:
    with pytest.raises(ValidationError, match="unknown GameResult"):
        GameExecutionResult(
            execution_digest="a" * 64,
            game_id="game-1",
            classification="DRAW",
        )
