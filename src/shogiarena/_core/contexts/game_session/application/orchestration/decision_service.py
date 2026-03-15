"""Dispatch decision orchestration helpers shared across orchestrators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, TypeGuard, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    EngineConfigDispatchPort,
    InstancePoolDispatchPort,
    RemoteDispatchDecision,
    RemoteDispatchRequest,
    RemoteInstanceDispatchPort,
    decide_remote_dispatch,
)

RemoteInstanceT = TypeVar("RemoteInstanceT", bound=RemoteInstanceDispatchPort)


def _is_instance_pool_dispatch_port(value: object) -> TypeGuard[InstancePoolDispatchPort[RemoteInstanceT]]:
    getter = getattr(value, "get_instance", None)
    return callable(getter)


@dataclass(frozen=True)
class OrchestratorDispatchDecisionRequest(Generic[RemoteInstanceT]):
    """Input DTO for dispatch decision resolution."""

    instance_pool: object | None
    engine_configs: Mapping[str, EngineConfigDispatchPort]
    dispatch_request: RemoteDispatchRequest


class OrchestratorDispatchDecisionService(Generic[RemoteInstanceT]):
    """Resolve dispatch decisions from DTO input."""

    def resolve(
        self,
        *,
        request: OrchestratorDispatchDecisionRequest[RemoteInstanceT],
    ) -> RemoteDispatchDecision[RemoteInstanceT]:
        raw_pool = request.instance_pool
        if raw_pool is None:
            pool: InstancePoolDispatchPort[RemoteInstanceT] | None = None
        elif _is_instance_pool_dispatch_port(raw_pool):
            pool = raw_pool
        else:
            raise TypeError("instance_pool must expose get_instance(instance_id)")
        return decide_remote_dispatch(
            pool,
            engine_configs=request.engine_configs,
            request=request.dispatch_request,
        )


__all__ = ["OrchestratorDispatchDecisionRequest", "OrchestratorDispatchDecisionService"]
