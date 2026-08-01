"""Dispatch selection helpers shared across orchestrators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeGuard, TypeVar

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


class _SchedulingConfigPort(Protocol):
    @property
    def tags(self) -> list[str]: ...

    @property
    def operating_system(self) -> str: ...

    @property
    def architecture(self) -> str: ...


class _SchedulingMetricsPort(Protocol):
    @property
    def is_reachable(self) -> bool: ...

    @property
    def in_use_slots(self) -> int: ...

    @property
    def in_use_engines(self) -> int: ...


class SchedulableRemoteInstancePort(RemoteInstanceDispatchPort, Protocol):
    @property
    def name(self) -> str: ...

    @property
    def config(self) -> _SchedulingConfigPort: ...

    @property
    def metrics(self) -> _SchedulingMetricsPort: ...

    @property
    def is_draining(self) -> bool: ...

    @property
    def effective_slots(self) -> int | None: ...

    @property
    def max_engine_capacity(self) -> int: ...

    @property
    def is_engine_capacity_known(self) -> bool: ...


RemoteInstanceT = TypeVar("RemoteInstanceT", bound=SchedulableRemoteInstancePort)
RemoteInstanceTCo = TypeVar(
    "RemoteInstanceTCo",
    bound=SchedulableRemoteInstancePort,
    covariant=True,
)


class _SchedulingPoolPort(Protocol[RemoteInstanceTCo]):
    def list_instances(self) -> list[RemoteInstanceTCo]: ...


def _is_scheduling_pool(
    value: object,
) -> TypeGuard[_SchedulingPoolPort[RemoteInstanceT]]:
    return callable(getattr(value, "list_instances", None))


def _is_schedulable_instance(value: object) -> TypeGuard[SchedulableRemoteInstancePort]:
    required = (
        "name",
        "config",
        "metrics",
        "is_draining",
        "effective_slots",
        "max_engine_capacity",
        "is_engine_capacity_known",
        "is_ssh",
    )
    return all(hasattr(value, name) for name in required)


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
    scheduling_policy: Literal["local", "explicit", "auto"] = "local"
    required_tags: tuple[str, ...] = ()
    required_engine_slots: int = 2
    target_operating_system: Literal["linux"] = "linux"
    target_architecture: Literal["x86_64"] = "x86_64"


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
        if request.scheduling_policy == "auto":
            return self._resolve_auto(request)
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
        if request.scheduling_policy == "local" and dispatch.remote_instance is not None:
            configured_remote_ids = sorted(
                instance_id for instance_id in _configured_instance_ids(request) if instance_id is not None
            )
            raise ValueError(
                "Remote instance assignment requires "
                "system.instance_scheduling.policy=explicit; configured instance IDs: "
                + ", ".join(configured_remote_ids)
            )
        if request.scheduling_policy == "explicit":
            if dispatch.black_instance_id is None or dispatch.white_instance_id is None:
                raise ValueError(
                    "system.instance_scheduling.policy=explicit requires instance_id for both engine roles"
                )
            if dispatch.remote_instance is None:
                raise ValueError(
                    "explicit Remote scheduling requires both engine roles to target the same reachable SSH instance"
                )
            if not _is_schedulable_instance(dispatch.remote_instance):
                raise TypeError("explicit Remote instance does not expose scheduler capabilities")
            reason = _scheduler_rejection_reason(
                dispatch.remote_instance,
                required_tags=set(request.required_tags),
                required_engine_slots=request.required_engine_slots,
                target_operating_system=request.target_operating_system,
                target_architecture=request.target_architecture,
            )
            if reason is not None:
                raise RuntimeError(f"Explicit instance '{dispatch.black_instance_id}' is not eligible: {reason}")
        remote_instance = dispatch.remote_instance
        selected_remote_instance = None if request.should_force_enginepool else remote_instance
        assigned_instance = dispatch.black_instance_id if remote_instance is not None else None
        return OrchestratorDispatchSelectionResponse(
            dispatch=dispatch,
            selected_remote_instance=selected_remote_instance,
            assigned_instance=assigned_instance,
        )

    def _resolve_auto(
        self,
        request: OrchestratorDispatchSelectionRequest[RemoteInstanceT],
    ) -> OrchestratorDispatchSelectionResponse[RemoteInstanceT]:
        configured_ids = _configured_instance_ids(request)
        if any(instance_id for instance_id in configured_ids):
            raise ValueError(
                "system.instance_scheduling.policy=auto cannot be combined with engine "
                "instance_id or per-game instance overrides"
            )
        pool = request.instance_pool
        if pool is None or not _is_scheduling_pool(pool):
            raise ValueError("automatic instance scheduling requires a configured instance pool")
        required_tags = set(request.required_tags)
        candidates: list[RemoteInstanceT] = []
        rejected: list[str] = []
        for raw_instance in pool.list_instances():
            candidate = raw_instance
            if not _is_schedulable_instance(raw_instance):
                continue
            reason = _scheduler_rejection_reason(
                raw_instance,
                required_tags=required_tags,
                required_engine_slots=request.required_engine_slots,
                target_operating_system=request.target_operating_system,
                target_architecture=request.target_architecture,
            )
            if reason is None:
                candidates.append(candidate)
            else:
                rejected.append(f"{raw_instance.name}: {reason}")
        if not candidates:
            detail = "; ".join(rejected) if rejected else "no SSH instances are configured"
            raise RuntimeError(f"No eligible instance for automatic scheduling: {detail}")
        selected = min(
            candidates,
            key=lambda instance: (
                instance.metrics.in_use_slots + instance.metrics.in_use_engines,
                instance.name,
            ),
        )
        dispatch = RemoteDispatchDecision(
            black_instance_id=selected.name,
            white_instance_id=selected.name,
            remote_instance=selected,
        )
        selected_remote = None if request.should_force_enginepool else selected
        return OrchestratorDispatchSelectionResponse(
            dispatch=dispatch,
            selected_remote_instance=selected_remote,
            assigned_instance=selected.name,
        )


def _configured_instance_ids(
    request: OrchestratorDispatchSelectionRequest[RemoteInstanceT],
) -> set[str | None]:
    black_config = request.engine_configs.get(request.black_engine_name)
    white_config = request.engine_configs.get(request.white_engine_name)
    return {
        request.black_item_instance_override,
        request.white_item_instance_override,
        black_config.instance_id if black_config is not None else None,
        white_config.instance_id if white_config is not None else None,
    }


def _scheduler_rejection_reason(
    instance: SchedulableRemoteInstancePort,
    *,
    required_tags: set[str],
    required_engine_slots: int,
    target_operating_system: str,
    target_architecture: str,
) -> str | None:
    if not instance.is_ssh:
        return "not an SSH worker"
    if not instance.metrics.is_reachable:
        return "health preflight is not reachable"
    if instance.is_draining:
        return "instance is draining"
    if instance.config.operating_system != target_operating_system:
        return f"operating system is {instance.config.operating_system}"
    if instance.config.architecture != target_architecture:
        return f"architecture is {instance.config.architecture}"
    missing_tags = sorted(required_tags - set(instance.config.tags))
    if missing_tags:
        return f"missing required tags: {', '.join(missing_tags)}"
    slots = instance.effective_slots
    if slots is None:
        return "slot capacity is unknown"
    if slots < required_engine_slots:
        return f"slot capacity {slots} is below required {required_engine_slots}"
    if not instance.is_engine_capacity_known:
        return "engine capacity is unknown"
    if instance.max_engine_capacity < 2:
        return f"engine capacity {instance.max_engine_capacity} is below required 2"
    return None


__all__ = [
    "OrchestratorDispatchSelectionService",
    "OrchestratorDispatchSelectionRequest",
    "OrchestratorDispatchSelectionResponse",
    "SchedulableRemoteInstancePort",
]
