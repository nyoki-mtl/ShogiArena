"""Action execution helpers for instances API handlers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardInstancesPort
from shogiarena._core.contexts.instances.application.entrypoints import (
    HealthChecker,
    Instance,
    InstanceType,
    Provisioner,
    ProvisionError,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.instance_runtime_serialization import serialize_instance_metrics
from shogiarena._core.interfaces.dashboard.instances.config import (
    expand_remote_path,
    resolve_local_path,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_optional_text, coerce_str

logger = logging.getLogger(__name__)


async def perform_health_check(
    instance: Instance,
    *,
    should_force: bool,
    last_health_checks: dict[str, float],
    health_check_min_interval: float,
    db_path: Path | None,
    queue_instances_update: Callable[..., None],
    instances_port: DashboardInstancesPort,
) -> JsonObject:
    """Perform health check on instance and update metrics."""

    try:
        logger.debug("Performing health check on instance %s", instance.name)

        now = time.monotonic()
        last = last_health_checks.get(instance.name)
        if not should_force and last is not None and (now - last) < health_check_min_interval:
            return {
                "action": "health_check",
                "instance_id": instance.name,
                "success": True,
                "reachable": instance.metrics.is_reachable,
                "metrics": serialize_instance_metrics(instance.metrics),
                "timestamp": time.time(),
                "skipped": True,
            }
        last_health_checks[instance.name] = now

        new_metrics = await HealthChecker.check_instance_health(instance)
        instance.update_metrics(new_metrics)
        queue_instances_update("upsert", instance_ids=[instance.name])

        if db_path is not None and new_metrics.is_reachable:
            try:
                instances_port.upsert_instance_spec(db_path, instance=instance)
            except (SQLAlchemyError, OSError) as exc:
                logger.warning("Failed to persist instance spec for %s: %s", instance.name, exc)

        return {
            "action": "health_check",
            "instance_id": instance.name,
            "success": True,
            "reachable": new_metrics.is_reachable,
            "metrics": serialize_instance_metrics(new_metrics),
            "timestamp": time.time(),
        }

    except (TimeoutError, OSError, RuntimeError) as exc:
        logger.error("Health check failed for instance %s: %s", instance.name, exc)
        return {
            "action": "health_check",
            "instance_id": instance.name,
            "success": False,
            "error": str(exc),
            "timestamp": time.time(),
        }


def perform_drain(
    instance: Instance,
    *,
    is_drain_enabled: bool,
    pool: InstancePool | None,
) -> JsonObject:
    """Set drain status on instance."""

    previous_drain_state = instance.is_draining
    if pool is None:
        return {
            "action": "drain" if is_drain_enabled else "undrain",
            "instance_id": instance.name,
            "success": False,
            "error": "Instance pool not available",
            "timestamp": time.time(),
        }
    updated_instance = pool.set_drain(instance.name, is_drain_enabled)
    if updated_instance is not None:
        action_name = "drain" if is_drain_enabled else "undrain"
        logger.info("Successfully set %s on instance %s", action_name, instance.name)
        return {
            "action": action_name,
            "instance_id": instance.name,
            "success": True,
            "old_drain": previous_drain_state,
            "new_drain": is_drain_enabled,
            "timestamp": time.time(),
        }
    return {
        "action": "drain" if is_drain_enabled else "undrain",
        "instance_id": instance.name,
        "success": False,
        "error": "Failed to update drain status",
        "timestamp": time.time(),
    }


async def perform_provision(instance: Instance, payload: JsonObject) -> JsonObject:
    """Provision directory or file payload to SSH instance."""

    if getattr(instance.type, "value", instance.type) != InstanceType.SSH.value:
        raise ValueError("provision is only supported for SSH instances")

    local_raw = payload.get("local_path")
    remote_raw = payload.get("remote_path")
    mode = (coerce_optional_text(payload.get("mode")) or "dir").strip().lower()
    is_executable = coerce_bool(payload.get("executable"))

    local_str = coerce_str(local_raw)
    if not local_str:
        raise ValueError("'local_path' is required")
    remote_str = coerce_str(remote_raw)
    if not remote_str:
        raise ValueError("'remote_path' is required")

    local_path = resolve_local_path(local_str)
    if mode == "dir":
        if not local_path.is_dir():
            raise ValueError("local_path must reference a directory when mode='dir'")
    elif mode == "file":
        if not local_path.is_file():
            raise ValueError("local_path must reference a file when mode='file'")
    else:
        raise ValueError("mode must be either 'dir' or 'file'")

    remote_path = expand_remote_path(remote_str, instance).strip()
    if not remote_path:
        raise ValueError("remote_path resolved to an empty value")

    try:
        if mode == "dir":
            await Provisioner.ensure_remote_dir_by_manifest(instance, local_path, remote_path)
        else:
            await Provisioner.ensure_remote_file(
                instance,
                local_path,
                remote_path,
                should_set_executable=is_executable,
            )
    except ProvisionError as exc:
        logger.error("Provision failed for instance %s: %s", instance.name, exc)
        return {
            "action": "provision",
            "instance_id": instance.name,
            "success": False,
            "error": str(exc),
            "local_path": str(local_path),
            "remote_path": remote_path,
            "mode": mode,
            "timestamp": time.time(),
        }

    logger.info(
        "Provisioned %s (%s) -> %s for instance %s",
        local_path,
        mode,
        remote_path,
        instance.name,
    )

    return {
        "action": "provision",
        "instance_id": instance.name,
        "success": True,
        "local_path": str(local_path),
        "remote_path": remote_path,
        "mode": mode,
        "timestamp": time.time(),
    }


__all__ = [
    "perform_drain",
    "perform_health_check",
    "perform_provision",
]
