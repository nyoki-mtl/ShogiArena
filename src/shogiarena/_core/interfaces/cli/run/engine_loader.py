"""Utilities for loading USI engines for CLI commands."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.instances.application.entrypoints import (
    create_engine as create_engine_from_context,
)
from shogiarena._core.contexts.instances.application.entrypoints import (
    create_engine_from_mapping as create_engine_from_mapping_from_context,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like

CONFIG_EXTENSIONS = {".yaml", ".yml"}


async def load_engine(
    engine_argument: str,
    *,
    timeout: float = 10.0,
    extra_options: Mapping[str, JsonValue] | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
) -> Any:
    """Load an engine from a config YAML or direct binary path."""

    argument = engine_argument.strip()
    if not argument:
        raise CliError("engine path or config must not be empty")

    root = build_default_root()
    runtime = root.engine_runtime

    # Guarantee a real local instance for engine creation; the runtime factory no
    # longer synthesizes a placeholder instance when no pool is supplied.
    pool = instance_pool if instance_pool is not None else InstancePool.ensure_default_local_pool()

    resolved_argument = Path(resolve_path_like(argument))
    if resolved_argument.suffix.lower() in CONFIG_EXTENSIONS:
        if not resolved_argument.exists():
            raise CliError(f"engine config not found: {resolved_argument}")
        options: JsonObject | None = (
            {str(key): value for key, value in extra_options.items()} if extra_options is not None else None
        )
        return await create_engine_from_context(
            resolved_argument,
            runtime=runtime,
            timeout=timeout,
            extra_options=options,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=pool,
        )

    if not resolved_argument.exists():
        raise CliError(f"engine binary not found: {resolved_argument}")
    if not resolved_argument.is_file():
        raise CliError(f"engine path is not a file: {resolved_argument}")

    engine_mapping: JsonObject = {
        "name": engine_name or resolved_argument.stem,
        "engine_path": str(resolved_argument),
        "working_directory": str(resolved_argument.parent),
    }
    options = {str(key): value for key, value in extra_options.items()} if extra_options else None
    return await create_engine_from_mapping_from_context(
        engine_mapping,
        runtime=runtime,
        timeout=timeout,
        extra_options=options,
        engine_name=engine_name,
        instance_id=instance_id,
        instance_pool=pool,
    )
