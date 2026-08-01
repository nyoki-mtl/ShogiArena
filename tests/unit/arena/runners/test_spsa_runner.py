from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, Mock

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.spsa.adapters.orchestrator_lifecycle_mixin import (
    SpsaOrchestratorLifecycleMixin,
)
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.domain.spsa_models import SpsaGamePayload
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.session_hooks import (
    CallbackGameLifecycleHooks,
    GameCompletionEvent,
    SessionStopController,
)


def _make_game_record(*, game_name: str, result: GameResult) -> object:
    return rsshogi.record.Record.from_dict(
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
    handler = AsyncMock()
    lifecycle = CallbackGameLifecycleHooks(
        stop_controller=SessionStopController(),
        payload_type=SpsaGamePayload,
        on_game_complete_fn=handler,
    )

    payload = SpsaGamePayload(
        update_idx=1,
        tuned_params=[],
        current_params=[],
        is_tuned_as_black=True,
        winner_code=1,
        phase="plus",
    )
    game_info = _make_game_record(game_name="g-001", result=GameResult.PAUSED)
    event = GameCompletionEvent(game_id="g-001", game_info=game_info, payload=payload)

    await lifecycle.on_game_complete(event)

    handler.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_spsa_runner_does_not_persist_error_result_when_stop_requested() -> None:
    payload = SpsaGamePayload(
        update_idx=1,
        tuned_params=[],
        current_params=[],
        is_tuned_as_black=True,
        winner_code=1,
        phase="plus",
    )
    game_info = _make_game_record(game_name="g-stop-error", result=GameResult.ERROR)
    event = GameCompletionEvent(game_id="g-stop-error", game_info=game_info, payload=payload, is_stop_requested=True)

    db_service = types.SimpleNamespace(
        append_record_list=Mock(),
        get_game_id_by_name=Mock(return_value=None),
        record_game_participation=Mock(),
    )
    progress = types.SimpleNamespace(on_game_complete=Mock())
    state = types.SimpleNamespace(
        db_service=db_service,
        completion_lock=asyncio.Lock(),
        observation_ledger=None,
    )
    runner = types.SimpleNamespace(
        _state=state,
        _append_spsa_event=Mock(),
        progress=progress,
    )

    await SpsaRunner._handle_game_completion(runner, event, payload)

    db_service.append_record_list.assert_not_called()
    runner._append_spsa_event.assert_not_called()
    progress.on_game_complete.assert_not_called()


@pytest.mark.asyncio
async def test_spsa_orchestrator_run_serializes_update_items() -> None:
    calls: list[tuple[list[int], object, int]] = []

    async def run_one(update_idx: int) -> None:
        assert update_idx > 0

    async def run_items_concurrently(
        items: list[int],
        run_one_update: object,
        concurrency_limit: int,
    ) -> None:
        calls.append((list(items), run_one_update, concurrency_limit))

    async def preflight_instance_health() -> None:
        return

    orchestrator = types.SimpleNamespace(
        _update_items=[1, 2, 3],
        _params=[object()],
        _sfens=["startpos"],
        num_workers=4,
        run_items_concurrently=run_items_concurrently,
        preflight_instance_health=preflight_instance_health,
        _run_one_spsa_update=run_one,
    )

    await SpsaOrchestratorLifecycleMixin.run(orchestrator)

    assert calls == [([1, 2, 3], run_one, 1)]
