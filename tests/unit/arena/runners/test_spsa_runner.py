from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, Mock

import pytest
import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena.arena.orchestrators.spsa_orchestrator import SpsaGamePayload
from shogiarena.arena.runners.spsa_runner import SpsaRunner
from shogiarena.arena.session import GameCompletionEvent, SessionStopController
from shogiarena.utils.types.types import GameResult


def _make_game_record(*, game_name: str, result: GameResult) -> object:
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "game_name": game_name,
                "game_type": "spsa",
                "black_player": "Tuned",
                "white_player": "Baseline",
                "attributes": {"game_name": game_name, "game_type": "spsa"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result.name, "ply_count": 0},
        }
    )


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
    game_info = _make_game_record(game_name="g-001", result=GameResult.PAUSED)
    event = GameCompletionEvent(game_id="g-001", game_info=game_info, payload=payload)

    await lifecycle.on_game_complete(event)

    runner._handle_game_completion.assert_awaited_once_with(event, payload)


@pytest.mark.asyncio
async def test_spsa_runner_does_not_persist_error_result_when_stop_requested() -> None:
    payload = SpsaGamePayload(
        update_idx=1,
        tuned_params=[],
        current_params=[],
        tuned_as_black=True,
        winner_code=1,
        phase="plus",
    )
    game_info = _make_game_record(game_name="g-stop-error", result=GameResult.ERROR)
    event = GameCompletionEvent(game_id="g-stop-error", game_info=game_info, payload=payload, stop_requested=True)

    db_service = types.SimpleNamespace(
        append_record_list=Mock(),
        get_game_id_by_name=Mock(return_value=None),
        record_game_participation=Mock(),
    )
    progress = types.SimpleNamespace(on_game_complete=Mock())
    runner = types.SimpleNamespace(
        db_service=db_service,
        _completion_lock=asyncio.Lock(),
        _append_spsa_event=Mock(),
        progress=progress,
    )

    await SpsaRunner._handle_game_completion(runner, event, payload)

    db_service.append_record_list.assert_not_called()
    runner._append_spsa_event.assert_not_called()
    progress.on_game_complete.assert_not_called()
