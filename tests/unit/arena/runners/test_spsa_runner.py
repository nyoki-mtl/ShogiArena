from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from shogiarena.arena.orchestrators.spsa_orchestrator import SpsaGamePayload
from shogiarena.arena.runners.spsa_runner import SpsaRunner
from shogiarena.arena.session import GameCompletionEvent, SessionStopController
from shogiarena.records import GameInfo
from shogiarena.utils.common.constants import MOVE_END


@pytest.mark.asyncio
async def test_spsa_lifecycle_forwards_completion() -> None:
    runner = types.SimpleNamespace(_handle_game_completion=AsyncMock())
    lifecycle = SpsaRunner._SpsaLifecycle(runner, SessionStopController())

    payload = SpsaGamePayload(
        update_idx=1,
        tuned_params=[],
        current_params=[],
        tuned_as_black=True,
        winner_code=1,
        phase="plus",
    )
    game_info = GameInfo(
        game_name="g-001",
        game_type="spsa",
        black_player_name="Tuned",
        white_player_name="Baseline",
        moves=[MOVE_END],
    )
    event = GameCompletionEvent(game_id="g-001", game_info=game_info, payload=payload)

    await lifecycle.on_game_complete(event)

    runner._handle_game_completion.assert_awaited_once_with(event, payload)


@pytest.mark.asyncio
async def test_spsa_lifecycle_rejects_non_spsa_payload() -> None:
    runner = types.SimpleNamespace(_handle_game_completion=AsyncMock())
    lifecycle = SpsaRunner._SpsaLifecycle(runner, SessionStopController())

    game_info = GameInfo(
        game_name="g-002",
        game_type="spsa",
        black_player_name="Tuned",
        white_player_name="Baseline",
        moves=[MOVE_END],
    )
    event = GameCompletionEvent(game_id="g-002", game_info=game_info, payload=object())

    with pytest.raises(TypeError):
        await lifecycle.on_game_complete(event)

    runner._handle_game_completion.assert_not_awaited()
