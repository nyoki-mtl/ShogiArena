"""SPSA game setup wiring service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.context_service import (
    SpsaOrchestrationContextRequest,
    SpsaOrchestrationContextResponse,
    SpsaPhase,
    build_orchestration_context,
)
from shogiarena._core.contexts.game_session.application.orchestration.event_payload_service import (
    SpsaScheduledEventCommonRequest,
    build_scheduled_event_common,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_assignment_service import (
    OrchestratorGameAssignmentRequest,
    OrchestratorGameAssignmentResponse,
    OrchestratorGameAssignmentService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    EngineConfigDispatchPort,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionRequest,
    OrchestratorDispatchSelectionResponse,
    OrchestratorDispatchSelectionService,
    SchedulableRemoteInstancePort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject

RemoteInstanceT = TypeVar("RemoteInstanceT", bound=SchedulableRemoteInstancePort)


@dataclass(frozen=True)
class SpsaGameSetupRequest(Generic[RemoteInstanceT]):
    """Input DTO for SPSA game setup."""

    preassigned_game_id: str | None
    scheduler_worker_idx: int
    game_to_worker: Mapping[int, int]
    update_idx: int
    phase: SpsaPhase
    phase_suffix: str
    tuned_token: str
    baseline_token: str
    is_tuned_as_black: bool
    tuned_engine_name: str
    baseline_engine_name: str
    event_family: str
    instance_pool: object | None
    engine_configs: Mapping[str, EngineConfigDispatchPort]
    should_force_enginepool: bool = False
    scheduling_policy: Literal["local", "explicit", "auto"] = "local"
    required_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpsaGameSetupResponse(Generic[RemoteInstanceT]):
    """Resolved setup values required by SPSA game execution."""

    context: SpsaOrchestrationContextResponse
    assignment: OrchestratorGameAssignmentResponse
    dispatch_selection: OrchestratorDispatchSelectionResponse[RemoteInstanceT]
    event_common: JsonObject


class SpsaGameSetupService(Generic[RemoteInstanceT]):
    """Resolve context/assignment/dispatch/event setup for one SPSA game."""

    def __init__(
        self,
        *,
        assignment_service: OrchestratorGameAssignmentService,
        dispatch_selection_service: OrchestratorDispatchSelectionService[RemoteInstanceT],
    ) -> None:
        self._assignment_service = assignment_service
        self._dispatch_selection_service = dispatch_selection_service

    def resolve(
        self,
        *,
        request: SpsaGameSetupRequest[RemoteInstanceT],
        resolve_game_id: Callable[[], str],
        to_numeric_game_id: Callable[[str], int],
        preassign_worker: Callable[[int], int | None],
    ) -> SpsaGameSetupResponse[RemoteInstanceT]:
        context = build_orchestration_context(
            request=SpsaOrchestrationContextRequest(
                tuned_token=request.tuned_token,
                baseline_token=request.baseline_token,
                is_tuned_as_black=request.is_tuned_as_black,
                tuned_engine_name=request.tuned_engine_name,
                baseline_engine_name=request.baseline_engine_name,
                phase=request.phase,
                phase_suffix=request.phase_suffix,
            ),
        )
        assignment = self._assignment_service.resolve(
            request=OrchestratorGameAssignmentRequest(
                preassigned_game_id=request.preassigned_game_id,
                scheduler_worker_idx=request.scheduler_worker_idx,
                game_to_worker=request.game_to_worker,
            ),
            resolve_game_id=resolve_game_id,
            to_numeric_game_id=to_numeric_game_id,
            preassign_worker=preassign_worker,
        )
        dispatch_selection = self._dispatch_selection_service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=request.instance_pool,
                engine_configs=request.engine_configs,
                black_engine_name=context.black_engine_name,
                white_engine_name=context.white_engine_name,
                should_raise_on_missing_instance=True,
                should_force_enginepool=request.should_force_enginepool,
                scheduling_policy=request.scheduling_policy,
                required_tags=request.required_tags,
            ),
        )
        event_common = build_scheduled_event_common(
            request=SpsaScheduledEventCommonRequest(
                update_idx=request.update_idx,
                game_id=assignment.game_id,
                tuned_variant_token=request.tuned_token,
                baseline_variant_token=request.baseline_token,
                variant_label=context.variant_label,
                phase=request.phase,
                is_tuned_as_black=request.is_tuned_as_black,
                black_player=context.black_player_label,
                white_player=context.white_player_label,
                assigned_instance=dispatch_selection.assigned_instance,
                worker_idx=assignment.resolved_worker_idx,
                event_family=request.event_family,
            ),
        )
        return SpsaGameSetupResponse(
            context=context,
            assignment=assignment,
            dispatch_selection=dispatch_selection,
            event_common=event_common,
        )


__all__ = ["SpsaGameSetupRequest", "SpsaGameSetupService"]
