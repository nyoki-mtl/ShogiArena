"""SPSA application entrypoints exposed to interfaces."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.spsa.ports.spsa_runtime_port import (
    SpsaRunConfigBuildRequest,
    SpsaRuntimePort,
)


def build_spsa_run_config(
    payload: Mapping[str, object],
    *,
    source_path: Path | None,
    runtime: SpsaRuntimePort,
) -> Any:
    """Build SPSA run config from boundary payload."""

    request = SpsaRunConfigBuildRequest(source_path=source_path)
    return runtime.build_run_config(payload, request=request)


async def run_spsa_session(
    config: Any,
    *,
    storage: Any,
    should_skip_resume: bool,
    instance_pool: object | None,
    runtime: SpsaRuntimePort,
) -> None:
    """Run an SPSA session through the configured runner."""

    await runtime.run_session(
        config,
        storage=storage,
        should_skip_resume=should_skip_resume,
        instance_pool=instance_pool,
    )


def create_spsa_run_storage(run_dir: Path, *, runtime: SpsaRuntimePort) -> Any:
    """Create filesystem run storage for SPSA sessions."""

    return runtime.create_run_storage(run_dir)


def preflight_spsa_dry_run(
    config: Any,
    *,
    work_dir: Path,
    instance_pool: object | None,
    runtime: SpsaRuntimePort,
) -> None:
    """Run fixed-option checks without creating a run archive."""

    runtime.preflight_dry_run(config, work_dir=work_dir, instance_pool=instance_pool)


def spsa_engine_trace_logger_names(*, runtime: SpsaRuntimePort) -> tuple[str, ...]:
    """Logger names used when SPSA engine trace is enabled."""

    return runtime.engine_trace_logger_names()


__all__ = [
    "build_spsa_run_config",
    "create_spsa_run_storage",
    "preflight_spsa_dry_run",
    "run_spsa_session",
    "spsa_engine_trace_logger_names",
]
