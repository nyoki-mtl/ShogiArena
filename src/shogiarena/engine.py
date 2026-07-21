"""Public engine runtime API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings, UsiThinkRequest
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    AnalysisHandle,
    EngineLifecycleEvent,
    EngineProcessInfo,
    PonderHandle,
    UsiAnalyzeItem,
    UsiAnalyzePosition,
    UsiAnalyzeResetPolicy,
    UsiEngineSession,
    UsiEngineStartError,
    UsiEngineState,
    UsiIoEvent,
    UsiMateResult,
    UsiOptionValidationMode,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import (
    AsyncUsiProcessBridgePort,
    UsiBound,
    UsiEvalValue,
    UsiOption,
    UsiProtocolParser,
    UsiThinkPV,
    UsiThinkResult,
    move_from_usi,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


def _resolve_instance_pool(instance_id: str | None, instance_pool: InstancePool | None) -> InstancePool | None:
    """Guarantee a local pool for the implicit-local case.

    The runtime factory no longer synthesizes a placeholder instance, so a plain
    ``create_engine*()`` call (no ``instance_id``, no ``instance_pool``) must be given a
    real local pool. When ``instance_id`` is set without a pool, ``None`` is returned so
    the factory fails fast (an explicit instance requires an explicit pool).
    """

    if instance_pool is not None:
        return instance_pool
    if instance_id is not None:
        return None
    return InstancePool.ensure_default_local_pool()


async def create_engine(
    config_path: str | Path,
    *,
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
    option_validation: UsiOptionValidationMode | None = None,
) -> UsiEngineSession:
    """Create an engine instance using the default ShogiArena runtime wiring."""

    return await build_default_root().engine_runtime.create_engine(
        config_path,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=_resolve_instance_pool(instance_id, instance_pool),
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
    option_validation: UsiOptionValidationMode | None = None,
) -> UsiEngineSession:
    """Create an engine instance from a mapping using the default runtime wiring."""

    return await build_default_root().engine_runtime.create_engine_from_mapping(
        config_mapping,
        timeout=timeout,
        extra_options=extra_options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=_resolve_instance_pool(instance_id, instance_pool),
        cpu_affinity=cpu_affinity,
        collect_info_strings=collect_info_strings,
        collect_raw_io=collect_raw_io,
        collect_stderr=collect_stderr,
        collect_outbound=collect_outbound,
        option_validation=option_validation,
    )


__all__ = [
    "AnalysisHandle",
    "AsyncUsiProcessBridgePort",
    "EngineLifecycleEvent",
    "EngineProcessInfo",
    "InstancePool",
    "JsonObject",
    "JsonValue",
    "PonderHandle",
    "PonderHitTimings",
    "UsiAnalyzeItem",
    "UsiAnalyzePosition",
    "UsiAnalyzeResetPolicy",
    "UsiBound",
    "UsiEngineSession",
    "UsiEngineConfig",
    "UsiEngineStartError",
    "UsiEngineState",
    "UsiEvalValue",
    "UsiIoEvent",
    "UsiMateResult",
    "UsiOption",
    "UsiOptionValidationMode",
    "UsiProtocolParser",
    "UsiThinkPV",
    "UsiThinkRequest",
    "UsiThinkResult",
    "create_engine",
    "create_engine_from_mapping",
    "move_from_usi",
]
