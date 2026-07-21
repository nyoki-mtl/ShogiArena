"""Instance resource reservation helpers extracted from BaseOrchestrator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, runtime_checkable

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool, ResourceRequest
from shogiarena._core.contexts.instances.application.slot_policy import OptionsPort, estimate_required_slots
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import make_role_pool_key
from shogiarena._core.shared.kernel.json_types import JsonObject

from .config_builders import build_usi_options
from .config_engine import EngineConfig

logger = logging.getLogger(__name__)


@runtime_checkable
class _EngineConfigPort(OptionsPort, Protocol):
    instance_id: str | None


def _is_engine_config_port(value: object) -> TypeGuard[_EngineConfigPort]:
    if not isinstance(value, _EngineConfigPort):
        return False
    options = value.options
    return options is None or isinstance(options, Mapping)


@runtime_checkable
class _EngineConfigOwnerPort(Protocol):
    engine_configs: Mapping[str, object]


class _ResourceUsageItemPort(Protocol):
    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def pool_key(self) -> str: ...
    @property
    def instance_override(self) -> str | None: ...
    @property
    def extra_options(self) -> JsonObject | None: ...


class _ParallelResourceGamePort(Protocol):
    game_id: str
    black_engine: str
    white_engine: str
    assigned_instance_black: str | None
    assigned_instance_white: str | None


@dataclass(frozen=True)
class _ProjectedResourceItem:
    pool_key: str
    instance_override: str | None
    extra_options: JsonObject | None


@dataclass(frozen=True)
class _CapacityIssue:
    game_ids: tuple[str, ...]
    instance_id: str
    resource_name: str
    required: int
    capacity: int


def collect_instance_usage(
    orchestrator: Any, pool: InstancePool, *items: _ResourceUsageItemPort
) -> dict[str, ResourceRequest]:
    """Collect per-instance slot/engine requirements for a game spec."""

    owner = orchestrator
    if not isinstance(owner, _EngineConfigOwnerPort):
        raise AttributeError("engine_configs not initialized on orchestrator")
    engine_configs = owner.engine_configs
    if not isinstance(engine_configs, Mapping):
        raise TypeError("engine_configs must be a mapping of engine name to config spec")
    typed_configs: Mapping[str, object] = {str(key): value for key, value in engine_configs.items()}

    usage: dict[str, ResourceRequest] = {}
    for item in items:
        pool_key = item.pool_key
        engine_name = pool_key.split("#", 1)[0]
        if engine_name not in typed_configs:
            raise KeyError(f"engine '{engine_name}' is missing in engine_configs")
        spec_obj = typed_configs[engine_name]
        if not _is_engine_config_port(spec_obj):
            raise TypeError(f"engine config '{engine_name}' must expose 'instance_id' and 'options'")
        spec = spec_obj
        instance_id = item.instance_override or spec.instance_id or "local"
        instance = pool.get_instance(instance_id)
        if instance is None:
            if instance_id == "local":
                instance = pool.ensure_local_instance()
            else:
                raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")
        slots_required = estimate_required_slots(spec, item.extra_options)
        current = usage.get(instance_id)
        if current is None:
            usage[instance_id] = ResourceRequest(slots=slots_required, engines=1)
        else:
            usage[instance_id] = ResourceRequest(
                slots=current.slots + slots_required,
                engines=current.engines + 1,
            )
    return usage


def _require_engine_config(engine_configs: Mapping[str, object], engine_name: str) -> EngineConfig:
    spec_obj = engine_configs.get(engine_name)
    if spec_obj is None:
        raise KeyError(f"engine '{engine_name}' is missing in engine_configs")
    if not isinstance(spec_obj, EngineConfig):
        raise TypeError(f"engine config '{engine_name}' must be an EngineConfig")
    return spec_obj


def collect_tournament_game_instance_usage(
    orchestrator: Any,
    pool: InstancePool,
    game_spec: _ParallelResourceGamePort,
) -> dict[str, ResourceRequest]:
    """Collect projected instance usage for one tournament game."""

    owner = orchestrator
    if not isinstance(owner, _EngineConfigOwnerPort):
        raise AttributeError("engine_configs not initialized on orchestrator")
    engine_configs = owner.engine_configs
    if not isinstance(engine_configs, Mapping):
        raise TypeError("engine_configs must be a mapping of engine name to config spec")
    typed_configs: Mapping[str, object] = {str(key): value for key, value in engine_configs.items()}

    black_name = str(game_spec.black_engine)
    white_name = str(game_spec.white_engine)
    black_config = _require_engine_config(typed_configs, black_name)
    white_config = _require_engine_config(typed_configs, white_name)
    extra_options = getattr(owner, "extra_options", None)
    black_item = _ProjectedResourceItem(
        pool_key=make_role_pool_key(black_name, "black"),
        instance_override=game_spec.assigned_instance_black,
        extra_options=build_usi_options(extra_options, black_config),
    )
    white_item = _ProjectedResourceItem(
        pool_key=make_role_pool_key(white_name, "white"),
        instance_override=game_spec.assigned_instance_white,
        extra_options=build_usi_options(extra_options, white_config),
    )
    return collect_instance_usage(owner, pool, black_item, white_item)


def _merge_resource_request(
    target: dict[str, ResourceRequest],
    source: Mapping[str, ResourceRequest],
    *,
    multiplier: int,
) -> None:
    for instance_id, request in source.items():
        current = target.get(instance_id)
        current_slots = current.slots if current is not None else 0
        current_engines = current.engines if current is not None else 0
        slots = current_slots + (request.slots * multiplier)
        engines = current_engines + (request.engines * multiplier)
        if slots < 0 or engines < 0:
            raise ValueError("resource preflight accumulator became negative")
        if slots == 0 and engines == 0:
            target.pop(instance_id, None)
        else:
            target[instance_id] = ResourceRequest(slots=slots, engines=engines)


def _first_capacity_issue(
    pool: InstancePool,
    requirements: Mapping[str, ResourceRequest],
    game_ids: tuple[str, ...],
) -> _CapacityIssue | None:
    for instance_id, req in requirements.items():
        if req.slots <= 0 and req.engines <= 0:
            continue
        instance = pool.get_instance(instance_id)
        if instance is None:
            if instance_id == "local":
                instance = pool.ensure_local_instance()
            else:
                raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")
        slot_capacity = instance.effective_slots
        if slot_capacity is not None and req.slots > slot_capacity:
            return _CapacityIssue(
                game_ids=game_ids,
                instance_id=instance_id,
                resource_name="slot",
                required=req.slots,
                capacity=slot_capacity,
            )
        max_engines = instance.max_engine_capacity
        if instance.is_engine_capacity_known and req.engines > max_engines:
            return _CapacityIssue(
                game_ids=game_ids,
                instance_id=instance_id,
                resource_name="engine",
                required=req.engines,
                capacity=max_engines,
            )
    return None


def _format_game_window(game_ids: tuple[str, ...]) -> str:
    if len(game_ids) <= 4:
        return ", ".join(game_ids)
    return f"{game_ids[0]}, {game_ids[1]}, ..., {game_ids[-1]}"


def _format_capacity_issue(issue: _CapacityIssue, requested_parallelism: int) -> str:
    games = _format_game_window(issue.game_ids)
    return (
        f"tournament.num_parallel={requested_parallelism} cannot be satisfied by configured instance "
        f"resource limits. Games [{games}] would require {issue.required} {issue.resource_name}(s) "
        f"on instance '{issue.instance_id}', but the instance exposes only {issue.capacity}. "
        "ShogiArena estimates slot demand from Threads/USI_Threads and Ponder/USI_Ponder "
        "(ponder-off engines reserve ceil(Threads / 2) slots per side). "
        "Reduce tournament.num_parallel, lower engine Threads, increase instance slots/max_engines, "
        "or assign engines across more instances. Set system.resource_capacity_preflight=warn or off "
        "only when resource throttling is intentional."
    )


def preflight_parallel_resource_capacity(
    orchestrator: Any,
    pool: InstancePool,
    pending_items: Sequence[_ParallelResourceGamePort],
    requested_parallelism: int,
    *,
    mode: str,
) -> None:
    """Validate that scheduled games can satisfy the requested parallelism."""

    if mode == "off":
        return
    if mode not in {"warn", "error"}:
        raise ValueError("resource_capacity_preflight must be one of: off, warn, error")
    if requested_parallelism <= 1 or len(pending_items) < requested_parallelism:
        return

    projected = [collect_tournament_game_instance_usage(orchestrator, pool, game_spec) for game_spec in pending_items]
    window: dict[str, ResourceRequest] = {}
    window_ids: list[str] = []
    for index, game_spec in enumerate(pending_items):
        _merge_resource_request(window, projected[index], multiplier=1)
        window_ids.append(str(game_spec.game_id))
        if len(window_ids) > requested_parallelism:
            remove_index = index - requested_parallelism
            _merge_resource_request(window, projected[remove_index], multiplier=-1)
            window_ids.pop(0)
        if len(window_ids) < requested_parallelism:
            continue
        issue = _first_capacity_issue(pool, window, tuple(window_ids))
        if issue is None:
            continue
        message = _format_capacity_issue(issue, requested_parallelism)
        if mode == "warn":
            logger.warning("%s", message)
            return
        raise RuntimeError(message)

    logger.debug(
        "Resource capacity preflight passed for %s pending game(s) at num_parallel=%s",
        len(pending_items),
        requested_parallelism,
    )


async def await_instance_resources(
    orchestrator: Any,
    pool: InstancePool,
    requirements: Mapping[str, ResourceRequest],
    game_id: str,
    *,
    poll_interval: float | None = None,
    max_interval: float | None = None,
) -> None:
    """Wait until all required resources are available, reserving them atomically."""

    owner = orchestrator
    resolved_poll = poll_interval if poll_interval is not None else (owner._resource_poll_interval or 0.1)
    resolved_max = max_interval if max_interval is not None else (owner._resource_poll_max_interval or 1.0)
    if resolved_poll <= 0 or resolved_max <= 0:
        raise ValueError("poll intervals must be positive")
    if resolved_poll > resolved_max:
        raise ValueError("poll_interval must be <= max_interval")

    # Validate hard capacity upfront to avoid waiting forever.
    for instance_id, req in requirements.items():
        if req.slots <= 0 and req.engines <= 0:
            continue
        instance = pool.get_instance(instance_id)
        if instance is None:
            if instance_id == "local":
                instance = pool.ensure_local_instance()
            else:
                raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")
        slot_capacity = instance.effective_slots
        if slot_capacity is not None and req.slots and slot_capacity < req.slots:
            raise RuntimeError(
                f"Game {game_id} requires {req.slots} slot(s) on instance '{instance_id}' "
                f"but the instance exposes only {slot_capacity} slot(s). "
                "Adjust either engine Threads or instance slots."
            )
        max_engines = instance.max_engine_capacity
        if instance.is_engine_capacity_known and req.engines and max_engines < req.engines:
            raise RuntimeError(
                f"Game {game_id} requires {req.engines} engine(s) on instance '{instance_id}' "
                f"but the instance allows only {max_engines} concurrent engine(s). "
                "Adjust the instance's max_engines or redistribute engines."
            )

    delay = resolved_poll
    while True:
        acquired = pool.try_acquire_resources(requirements)
        if acquired:
            return

        if owner._stop_event.is_set():
            raise RuntimeError(f"Stop requested while waiting for instance resources (game {game_id})")

        await asyncio.sleep(delay)
        delay = min(delay * 1.5, resolved_max)


__all__ = [
    "await_instance_resources",
    "collect_instance_usage",
    "collect_tournament_game_instance_usage",
    "preflight_parallel_resource_capacity",
]
