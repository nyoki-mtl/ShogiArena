"""Resource reservation helpers for ``InstancePool``."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from .instance_models import Instance


@dataclass(frozen=True)
class ResourceRequest:
    """Represents resources required on an instance."""

    slots: int = 0
    engines: int = 0

    def __post_init__(self) -> None:
        if self.slots < 0 or self.engines < 0:
            raise ValueError("resource counts must be >= 0")


def validate_resource_requirements(
    instances: Mapping[str, Instance],
    requirements: Mapping[str, ResourceRequest],
    *,
    log: logging.Logger,
) -> bool:
    """Validate reservation requests against current instance state."""
    for instance_id, req in requirements.items():
        if req.slots <= 0 and req.engines <= 0:
            continue
        instance = instances.get(instance_id)
        if instance is None:
            raise KeyError(f"Unknown instance '{instance_id}'")
        if not instance.metrics.is_reachable:
            log.debug("Instance %s is unreachable; cannot acquire resources", instance_id)
            return False
        if instance.is_draining:
            log.debug("Instance %s is draining; cannot acquire resources", instance_id)
            return False
        if req.slots and instance.available_slots < req.slots:
            log.debug(
                "Instance %s lacks slot capacity (need %s, available %s)",
                instance_id,
                req.slots,
                instance.available_slots,
            )
            return False
        if req.engines and instance.available_engines < req.engines:
            log.debug(
                "Instance %s lacks engine capacity (need %s, available %s)",
                instance_id,
                req.engines,
                instance.available_engines,
            )
            return False
    return True


def acquire_resources_with_rollback(
    instances: Mapping[str, Instance],
    requirements: Mapping[str, ResourceRequest],
    *,
    log: logging.Logger,
) -> bool:
    """Acquire resources atomically and roll back partial reservations on failure."""
    acquired_ids: list[str] = []
    try:
        for instance_id, req in requirements.items():
            if req.slots <= 0 and req.engines <= 0:
                continue
            instance = instances[instance_id]
            acquired = instance.try_acquire_resources(slots=req.slots, engines=req.engines)
            if not acquired:
                log.warning("Race while acquiring resources on %s; rolling back reservation", instance_id)
                raise RuntimeError("failed to acquire requested resources")
            acquired_ids.append(instance_id)
            log.debug(
                "Reserved slots=%s engines=%s on instance %s (in_use=%s/%s, engines=%s/%s)",
                req.slots,
                req.engines,
                instance_id,
                instance.metrics.in_use_slots,
                instance.effective_slots,
                instance.metrics.in_use_engines,
                instance.max_engine_capacity,
            )
    except (RuntimeError, KeyError, ValueError) as exc:
        log.debug("Failed to acquire resources; rolling back: %s", exc, exc_info=True)
        for rollback_id in acquired_ids:
            req = requirements[rollback_id]
            instance = instances[rollback_id]
            instance.release_resources(slots=req.slots, engines=req.engines)
        return False
    return True


def release_instance_resources(
    instances: Mapping[str, Instance],
    allocations: Mapping[str, ResourceRequest],
    *,
    log: logging.Logger,
) -> None:
    """Release previously acquired resources for the given instances."""
    for instance_id, req in allocations.items():
        if req.slots <= 0 and req.engines <= 0:
            continue
        instance = instances.get(instance_id)
        if instance is None:
            log.warning("Attempted to release resources for unknown instance: %s", instance_id)
            continue
        instance.release_resources(slots=req.slots, engines=req.engines)
        log.debug(
            "Released slots=%s engines=%s for instance %s (in_use=%s/%s, engines=%s/%s)",
            req.slots,
            req.engines,
            instance_id,
            instance.metrics.in_use_slots,
            instance.config.slots,
            instance.metrics.in_use_engines,
            instance.max_engine_capacity,
        )
