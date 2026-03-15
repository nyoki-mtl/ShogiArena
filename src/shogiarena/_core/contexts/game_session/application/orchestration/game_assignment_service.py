"""Shared game-id and worker-assignment resolution helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    WorkerResolutionRequest,
    resolve_required_worker_idx,
)


@dataclass(frozen=True)
class OrchestratorGameAssignmentRequest:
    """Input DTO for shared game assignment resolution."""

    preassigned_game_id: str | None
    scheduler_worker_idx: int
    game_to_worker: Mapping[int, int]


@dataclass(frozen=True)
class OrchestratorGameAssignmentResponse:
    """Resolved game identity and worker-assignment values."""

    game_id: str
    numeric_game_id: int
    preassigned_worker_idx: int | None
    resolved_worker_idx: int
    worker_resolution: WorkerResolutionRequest


class OrchestratorGameAssignmentService:
    """Resolve game identity and worker assignment for orchestrators."""

    def resolve(
        self,
        *,
        request: OrchestratorGameAssignmentRequest,
        resolve_game_id: Callable[[], str],
        to_numeric_game_id: Callable[[str], int],
        preassign_worker: Callable[[int], int | None],
    ) -> OrchestratorGameAssignmentResponse:
        game_id = request.preassigned_game_id or resolve_game_id()
        numeric_id = to_numeric_game_id(game_id)
        preassigned_worker_idx = preassign_worker(numeric_id)
        worker_resolution = WorkerResolutionRequest(
            preassigned_worker=preassigned_worker_idx,
            game_to_worker=request.game_to_worker,
            numeric_game_id=numeric_id,
            fallback_worker_idx=request.scheduler_worker_idx,
        )
        resolved_worker_idx = resolve_required_worker_idx(
            request=worker_resolution,
        )
        return OrchestratorGameAssignmentResponse(
            game_id=game_id,
            numeric_game_id=numeric_id,
            preassigned_worker_idx=preassigned_worker_idx,
            resolved_worker_idx=resolved_worker_idx,
            worker_resolution=worker_resolution,
        )


__all__ = [
    "OrchestratorGameAssignmentRequest",
    "OrchestratorGameAssignmentResponse",
    "OrchestratorGameAssignmentService",
]
