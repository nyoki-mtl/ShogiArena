"""Completion emission helper shared across orchestrators."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    WorkerResolutionRequest,
    resolve_required_worker_idx,
)

GameInfoT = TypeVar("GameInfoT")
PayloadT = TypeVar("PayloadT")


class _EmitGameCompletionFn(Protocol[GameInfoT, PayloadT]):
    """Callback contract for completion event emission."""

    def __call__(
        self,
        *,
        game_id: str,
        game_info: GameInfoT,
        payload: PayloadT,
        worker_idx: int,
    ) -> Awaitable[None]: ...


@dataclass(frozen=True)
class OrchestratorCompletionEmissionRequest(Generic[GameInfoT, PayloadT]):
    """Input DTO for completion worker resolution and emission."""

    game_id: str
    game_info: GameInfoT
    payload: PayloadT
    worker_resolution: WorkerResolutionRequest


class OrchestratorCompletionEmissionService:
    """Resolve completion worker index and emit completion callback."""

    async def emit(
        self,
        *,
        request: OrchestratorCompletionEmissionRequest[GameInfoT, PayloadT],
        emit_game_completion: _EmitGameCompletionFn[GameInfoT, PayloadT],
    ) -> int:
        worker_idx = resolve_required_worker_idx(request=request.worker_resolution)
        await emit_game_completion(
            game_id=request.game_id,
            game_info=request.game_info,
            payload=request.payload,
            worker_idx=worker_idx,
        )
        return worker_idx


__all__ = [
    "OrchestratorCompletionEmissionRequest",
    "OrchestratorCompletionEmissionService",
]
