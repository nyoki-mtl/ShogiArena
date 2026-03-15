from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.completion_emission_service import (
    OrchestratorCompletionEmissionRequest,
    OrchestratorCompletionEmissionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    WorkerResolutionRequest,
)


@pytest.mark.asyncio
async def test_emit_resolves_worker_and_calls_callback() -> None:
    service = OrchestratorCompletionEmissionService()
    calls: list[tuple[str, str, str, int]] = []

    async def _emit(game_id: str, game_info: str, payload: str, worker_idx: int) -> None:
        calls.append((game_id, game_info, payload, worker_idx))

    resolved = await service.emit(
        request=OrchestratorCompletionEmissionRequest(
            game_id="42",
            game_info="game-info",
            payload="payload",
            worker_resolution=WorkerResolutionRequest(
                preassigned_worker=3,
                game_to_worker={42: 1},
                numeric_game_id=42,
                fallback_worker_idx=0,
            ),
        ),
        emit_game_completion=_emit,
    )

    assert resolved == 3
    assert calls == [("42", "game-info", "payload", 3)]


@pytest.mark.asyncio
async def test_emit_uses_mapping_when_preassign_missing() -> None:
    service = OrchestratorCompletionEmissionService()
    calls: list[int] = []

    async def _emit(game_id: str, game_info: object, payload: object, worker_idx: int) -> None:
        calls.append(worker_idx)

    resolved = await service.emit(
        request=OrchestratorCompletionEmissionRequest(
            game_id="42",
            game_info={"result": "draw"},
            payload={"kind": "completion"},
            worker_resolution=WorkerResolutionRequest(
                preassigned_worker=None,
                game_to_worker={42: 5},
                numeric_game_id=42,
                fallback_worker_idx=0,
            ),
        ),
        emit_game_completion=_emit,
    )

    assert resolved == 5
    assert calls == [5]


@pytest.mark.asyncio
async def test_emit_raises_when_worker_cannot_be_resolved() -> None:
    service = OrchestratorCompletionEmissionService()

    async def _emit(game_id: str, game_info: object, payload: object, worker_idx: int) -> None:
        raise AssertionError("emit callback should not be called")

    with pytest.raises(ValueError, match="Worker index could not be resolved"):
        await service.emit(
            request=OrchestratorCompletionEmissionRequest(
                game_id="42",
                game_info=None,
                payload=None,
                worker_resolution=WorkerResolutionRequest(
                    preassigned_worker=None,
                    game_to_worker={},
                    numeric_game_id=42,
                    fallback_worker_idx=None,
                ),
            ),
            emit_game_completion=_emit,
        )
