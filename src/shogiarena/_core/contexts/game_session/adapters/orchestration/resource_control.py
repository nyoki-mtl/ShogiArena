"""Instance resource reservation helpers extracted from BaseOrchestrator."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol, TypeGuard, runtime_checkable

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool, ResourceRequest
from shogiarena._core.contexts.instances.application.slot_policy import OptionsPort, estimate_required_slots
from shogiarena._core.shared.kernel.json_types import JsonObject


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
    pool_key: str
    instance_override: str | None
    extra_options: JsonObject | None


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


__all__ = ["await_instance_resources", "collect_instance_usage"]
