"""Public engine runtime API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings, UsiThinkRequest
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    AnalysisHandle,
    AsyncUsiProcess,
    PonderHandle,
    UsiEngineStartError,
    UsiEngineState,
    UsiMateResult,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import (
    AsyncUsiProcessBridgePort,
    UsiOption,
    UsiProtocolParser,
    UsiThinkPV,
    UsiThinkResult,
    move_from_usi,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


async def create_engine(
    config_path: str | Path,
    *,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
) -> Any:
    """Create an engine instance using the default ShogiArena runtime wiring."""

    return await build_default_root().engine_runtime.create_engine(
        config_path,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=instance_pool,
        cpu_affinity=cpu_affinity,
    )


async def create_engine_from_mapping(
    config_mapping: Mapping[str, object],
    *,
    timeout: float = 10.0,
    extra_options: JsonObject | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
) -> Any:
    """Create an engine instance from a mapping using the default runtime wiring."""

    return await build_default_root().engine_runtime.create_engine_from_mapping(
        config_mapping,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=instance_pool,
        cpu_affinity=cpu_affinity,
    )


__all__ = [
    "AnalysisHandle",
    "AsyncUsiEngine",
    "AsyncUsiProcess",
    "AsyncUsiProcessBridgePort",
    "PonderHandle",
    "PonderHitTimings",
    "SpawnerBackedUSIBridge",
    "UsiEngineConfig",
    "UsiEngineStartError",
    "UsiEngineState",
    "UsiMateResult",
    "UsiOption",
    "UsiProtocolParser",
    "UsiThinkPV",
    "UsiThinkRequest",
    "UsiThinkResult",
    "create_engine",
    "create_engine_from_mapping",
    "move_from_usi",
]
