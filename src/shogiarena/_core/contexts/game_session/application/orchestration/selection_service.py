"""Dispatch selection helpers shared across orchestrators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionRequest,
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    EngineConfigDispatchPort,
    RemoteDispatchDecision,
    RemoteInstanceDispatchPort,
)
from shogiarena._core.contexts.game_session.application.orchestration.request_service import (
    build_dispatch_request,
)

RemoteInstanceT = TypeVar("RemoteInstanceT", bound=RemoteInstanceDispatchPort)


@dataclass(frozen=True)
class OrchestratorDispatchSelectionRequest(Generic[RemoteInstanceT]):
    """Input DTO for orchestrator dispatch selection."""

    instance_pool: object | None
    engine_configs: Mapping[str, EngineConfigDispatchPort]
    black_engine_name: str
    white_engine_name: str
    black_item_instance_override: str | None = None
    white_item_instance_override: str | None = None
    should_raise_on_missing_instance: bool = False
    should_force_enginepool: bool = False


@dataclass(frozen=True)
class OrchestratorDispatchSelectionResponse(Generic[RemoteInstanceT]):
    """Resolved dispatch and execution-path selection values."""

    dispatch: RemoteDispatchDecision[RemoteInstanceT]
    selected_remote_instance: RemoteInstanceT | None
    assigned_instance: str | None


class OrchestratorDispatchSelectionService(Generic[RemoteInstanceT]):
    """Resolve dispatch decision and selected execution path."""

    def __init__(
        self,
        *,
        dispatch_decision_service: OrchestratorDispatchDecisionService[RemoteInstanceT],
    ) -> None:
        self._dispatch_decision_service = dispatch_decision_service

    def resolve(
        self,
        *,
        request: OrchestratorDispatchSelectionRequest[RemoteInstanceT],
    ) -> OrchestratorDispatchSelectionResponse[RemoteInstanceT]:
        dispatch_request = build_dispatch_request(
            black_engine_name=request.black_engine_name,
            white_engine_name=request.white_engine_name,
            black_item_instance_override=request.black_item_instance_override,
            white_item_instance_override=request.white_item_instance_override,
            should_raise_on_missing_instance=request.should_raise_on_missing_instance,
        )
        dispatch = self._dispatch_decision_service.resolve(
            request=OrchestratorDispatchDecisionRequest(
                instance_pool=request.instance_pool,
                engine_configs=request.engine_configs,
                dispatch_request=dispatch_request,
            ),
        )
        remote_instance = dispatch.remote_instance
        selected_remote_instance = None if request.should_force_enginepool else remote_instance
        assigned_instance = dispatch.black_instance_id if remote_instance is not None else None
        return OrchestratorDispatchSelectionResponse(
            dispatch=dispatch,
            selected_remote_instance=selected_remote_instance,
            assigned_instance=assigned_instance,
        )


__all__ = [
    "OrchestratorDispatchSelectionService",
    "OrchestratorDispatchSelectionRequest",
    "OrchestratorDispatchSelectionResponse",
]
