"""Dispatch decision helpers shared across orchestrators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


class EngineConfigDispatchPort(Protocol):
    """Minimal engine config contract for dispatch decisions."""

    @property
    def instance_id(self) -> str | None: ...


class RemoteInstanceDispatchPort(Protocol):
    """Minimal remote instance contract used by dispatch decisions."""

    @property
    def is_ssh(self) -> bool: ...


REMOTE_INSTANCE_T = TypeVar("REMOTE_INSTANCE_T", bound=RemoteInstanceDispatchPort)
REMOTE_INSTANCE_T_CO = TypeVar("REMOTE_INSTANCE_T_CO", bound=RemoteInstanceDispatchPort, covariant=True)


class InstancePoolDispatchPort(Protocol[REMOTE_INSTANCE_T_CO]):
    """Minimal instance pool lookup contract for dispatch decisions."""

    def get_instance(self, instance_id: str) -> REMOTE_INSTANCE_T_CO | None: ...


@dataclass(frozen=True)
class RemoteDispatchRequest:
    """Input DTO used to resolve per-side dispatch metadata."""

    black_engine_name: str
    white_engine_name: str
    black_item_instance_override: str | None = None
    white_item_instance_override: str | None = None
    should_raise_on_missing_instance: bool = False


@dataclass(frozen=True)
class RemoteDispatchDecision(Generic[REMOTE_INSTANCE_T]):
    """Resolved instance assignment and remote execution decision."""

    black_instance_id: str | None
    white_instance_id: str | None
    remote_instance: REMOTE_INSTANCE_T | None


@dataclass(frozen=True)
class WorkerResolutionRequest:
    """Input DTO for completion worker index resolution."""

    preassigned_worker: int | None
    game_to_worker: Mapping[int, int]
    numeric_game_id: int
    fallback_worker_idx: int | None = None


def decide_remote_dispatch(
    pool: InstancePoolDispatchPort[REMOTE_INSTANCE_T] | None,
    *,
    engine_configs: Mapping[str, EngineConfigDispatchPort],
    request: RemoteDispatchRequest,
) -> RemoteDispatchDecision[REMOTE_INSTANCE_T]:
    """Resolve per-side instance IDs and whether same-instance remote execution is possible."""

    black_spec = engine_configs.get(request.black_engine_name)
    white_spec = engine_configs.get(request.white_engine_name)
    default_black_id = black_spec.instance_id if black_spec is not None else None
    default_white_id = white_spec.instance_id if white_spec is not None else None
    black_instance_id = request.black_item_instance_override or default_black_id
    white_instance_id = request.white_item_instance_override or default_white_id

    remote_instance: REMOTE_INSTANCE_T | None = None
    if pool is not None and black_instance_id and white_instance_id and black_instance_id == white_instance_id:
        candidate = pool.get_instance(black_instance_id)
        if candidate is None and request.should_raise_on_missing_instance:
            raise ValueError(f"Instance not found: {black_instance_id}")
        if candidate is not None and candidate.is_ssh:
            remote_instance = candidate

    return RemoteDispatchDecision(
        black_instance_id=black_instance_id,
        white_instance_id=white_instance_id,
        remote_instance=remote_instance,
    )


def resolve_completion_worker_idx(
    *,
    request: WorkerResolutionRequest,
) -> int | None:
    """Resolve completion worker index from preassignment, mapping, and fallback."""

    if request.preassigned_worker is not None:
        return int(request.preassigned_worker)
    mapped_idx = request.game_to_worker.get(request.numeric_game_id)
    if mapped_idx is not None:
        return int(mapped_idx)
    return request.fallback_worker_idx


def resolve_required_worker_idx(
    *,
    request: WorkerResolutionRequest,
) -> int:
    """Resolve worker index and fail fast when no candidate is available."""

    resolved = resolve_completion_worker_idx(
        request=request,
    )
    if resolved is None:
        raise ValueError("Worker index could not be resolved")
    return int(resolved)


__all__ = [
    "EngineConfigDispatchPort",
    "InstancePoolDispatchPort",
    "RemoteDispatchRequest",
    "RemoteDispatchDecision",
    "RemoteInstanceDispatchPort",
    "WorkerResolutionRequest",
    "decide_remote_dispatch",
    "resolve_completion_worker_idx",
    "resolve_required_worker_idx",
]
