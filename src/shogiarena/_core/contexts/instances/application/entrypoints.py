"""Consolidated runtime entrypoints for dashboard instance adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from shogiarena._core.contexts.instances.application.config_store import InstanceConfigStore
from shogiarena._core.contexts.instances.application.health_checker import HealthChecker
from shogiarena._core.contexts.instances.application.instance_models import (
    Instance,
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.provisioner import Provisioner, ProvisionError
from shogiarena._core.contexts.instances.ports.engine_runtime_port import EngineOptionValidationMode, EngineRuntimePort
from shogiarena._core.shared.kernel.json_types import JsonObject


async def create_engine(
    config_path: Any,
    *,
    runtime: EngineRuntimePort,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
    collect_info_strings: bool | None = None,
    collect_raw_io: bool | None = None,
    collect_stderr: bool | None = None,
    collect_outbound: bool | None = None,
    option_validation: EngineOptionValidationMode | None = None,
) -> Any:
    """Create an engine runtime from a config path."""

    return await runtime.create_engine(
        config_path,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=instance_pool,
        cpu_affinity=cpu_affinity,
        collect_info_strings=collect_info_strings,
        collect_raw_io=collect_raw_io,
        collect_stderr=collect_stderr,
        collect_outbound=collect_outbound,
        option_validation=option_validation,
    )


async def create_engine_from_mapping(
    config_mapping: Mapping[str, object],
    *,
    runtime: EngineRuntimePort,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
    collect_info_strings: bool | None = None,
    collect_raw_io: bool | None = None,
    collect_stderr: bool | None = None,
    collect_outbound: bool | None = None,
    option_validation: EngineOptionValidationMode | None = None,
) -> Any:
    """Create an engine runtime from a boundary mapping payload."""

    return await runtime.create_engine_from_mapping(
        config_mapping,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=instance_pool,
        cpu_affinity=cpu_affinity,
        collect_info_strings=collect_info_strings,
        collect_raw_io=collect_raw_io,
        collect_stderr=collect_stderr,
        collect_outbound=collect_outbound,
        option_validation=option_validation,
    )


__all__ = [
    "create_engine",
    "create_engine_from_mapping",
    "HealthChecker",
    "Instance",
    "InstanceConfig",
    "InstanceConfigStore",
    "InstancePool",
    "InstanceType",
    "ProvisionError",
    "Provisioner",
]
