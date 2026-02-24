"""Utilities for loading USI engines for CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_bridge_spawner import SpawnerBackedUSIBridge
from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.instances.models import Instance
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.cli.errors import CliError
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like

CONFIG_EXTENSIONS = {".yaml", ".yml"}


async def load_engine(
    engine_argument: str,
    *,
    timeout: float = 10.0,
    extra_options: dict[str, Any] | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
) -> AsyncUsiEngine:
    """Load an engine from a config YAML or direct binary path."""

    argument = engine_argument.strip()
    if not argument:
        raise CliError("engine path or config must not be empty")

    resolved_argument = Path(resolve_path_like(argument))
    if resolved_argument.suffix.lower() in CONFIG_EXTENSIONS:
        if not resolved_argument.exists():
            raise CliError(f"engine config not found: {resolved_argument}")
        return await EngineFactory.create_engine(
            resolved_argument,
            timeout=timeout,
            extra_options=extra_options,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
        )

    if not resolved_argument.exists():
        raise CliError(f"engine binary not found: {resolved_argument}")
    if not resolved_argument.is_file():
        raise CliError(f"engine path is not a file: {resolved_argument}")

    config = UsiEngineConfig.from_mapping(
        {
            "name": engine_name or resolved_argument.stem,
            "engine_path": str(resolved_argument),
            "working_directory": str(resolved_argument.parent),
        }
    ).resolve_paths(output_dir=project_dirs.output_dir, engine_dir=project_dirs.engine_dir)

    if extra_options:
        config = config.with_overrides(
            options=extra_options,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
    config = config.resolve_isready_lock_key()

    pool = instance_pool or InstancePool.load_default_local() or InstancePool()
    instance = _resolve_instance(pool, instance_id)

    bridge = SpawnerBackedUSIBridge(
        instance=instance,
        engine_path=config.engine_path or str(resolved_argument),
        working_dir=config.working_directory or str(resolved_argument.parent),
        name=config.name,
        engine_args=list(config.engine_args),
        env=dict(config.environment),
        cpu_affinity=None,
    )

    return AsyncUsiEngine(
        config=config,
        bridge=bridge,
        handshake_timeout=timeout,
    )


def _resolve_instance(pool: InstancePool, instance_id: str | None) -> Instance:
    if instance_id is None:
        return pool.ensure_local_instance()
    instance = pool.get_instance(instance_id)
    if instance is None:
        raise CliError(f"instance not found: {instance_id}")
    return instance
