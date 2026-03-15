from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.game_assignment_service import (
    OrchestratorGameAssignmentRequest,
    OrchestratorGameAssignmentService,
)


def test_resolve_uses_generated_game_id_and_preassigned_worker_when_available() -> None:
    service = OrchestratorGameAssignmentService()

    response = service.resolve(
        request=OrchestratorGameAssignmentRequest(
            preassigned_game_id=None,
            scheduler_worker_idx=1,
            game_to_worker={7: 5},
        ),
        resolve_game_id=lambda: "generated-game-id",
        to_numeric_game_id=lambda game_id: 7,
        preassign_worker=lambda numeric_id: 2,
    )

    assert response.game_id == "generated-game-id"
    assert response.numeric_game_id == 7
    assert response.preassigned_worker_idx == 2
    assert response.resolved_worker_idx == 2
    assert response.worker_resolution.preassigned_worker == 2
    assert response.worker_resolution.numeric_game_id == 7
    assert response.worker_resolution.fallback_worker_idx == 1


def test_resolve_uses_preassigned_game_id_and_mapping_worker() -> None:
    service = OrchestratorGameAssignmentService()

    response = service.resolve(
        request=OrchestratorGameAssignmentRequest(
            preassigned_game_id="fixed-game-id",
            scheduler_worker_idx=0,
            game_to_worker={42: 4},
        ),
        resolve_game_id=lambda: "unused-generated-id",
        to_numeric_game_id=lambda game_id: 42,
        preassign_worker=lambda numeric_id: None,
    )

    assert response.game_id == "fixed-game-id"
    assert response.numeric_game_id == 42
    assert response.preassigned_worker_idx is None
    assert response.resolved_worker_idx == 4
    assert response.worker_resolution.preassigned_worker is None
    assert response.worker_resolution.numeric_game_id == 42
    assert response.worker_resolution.fallback_worker_idx == 0


def test_resolve_falls_back_to_scheduler_worker_when_mapping_missing() -> None:
    service = OrchestratorGameAssignmentService()

    response = service.resolve(
        request=OrchestratorGameAssignmentRequest(
            preassigned_game_id=None,
            scheduler_worker_idx=9,
            game_to_worker={},
        ),
        resolve_game_id=lambda: "generated-for-fallback",
        to_numeric_game_id=lambda game_id: 999,
        preassign_worker=lambda numeric_id: None,
    )

    assert response.preassigned_worker_idx is None
    assert response.resolved_worker_idx == 9
    assert response.worker_resolution.preassigned_worker is None
    assert response.worker_resolution.numeric_game_id == 999
    assert response.worker_resolution.fallback_worker_idx == 9


def test_resolve_does_not_call_generator_when_game_id_is_preassigned() -> None:
    service = OrchestratorGameAssignmentService()
    called = False

    def _resolve_game_id() -> str:
        nonlocal called
        called = True
        return "unexpected"

    response = service.resolve(
        request=OrchestratorGameAssignmentRequest(
            preassigned_game_id="already-fixed",
            scheduler_worker_idx=0,
            game_to_worker={},
        ),
        resolve_game_id=_resolve_game_id,
        to_numeric_game_id=lambda game_id: 1,
        preassign_worker=lambda numeric_id: 0,
    )

    assert response.game_id == "already-fixed"
    assert called is False
    assert response.worker_resolution.preassigned_worker == 0
    assert response.worker_resolution.numeric_game_id == 1
