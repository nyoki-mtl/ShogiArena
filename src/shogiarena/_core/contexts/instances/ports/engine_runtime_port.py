"""Runtime gateway contracts for instance context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from shogiarena._core.contexts.instances.application.engine_process_spawner import (
    EngineProcessSpawner,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.provisioner import Provisioner
from shogiarena._core.shared.kernel.json_types import JsonObject


@runtime_checkable
class EngineRuntimePort(Protocol):
    """Port for constructing engine runtimes from configs."""

    async def create_engine(
        self,
        config_path: Any,
        *,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: InstancePool | None = None,
        cpu_affinity: Sequence[int] | None = None,
    ) -> Any: ...

    async def create_engine_from_mapping(
        self,
        config_mapping: Mapping[str, object],
        *,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: InstancePool | None = None,
        cpu_affinity: Sequence[int] | None = None,
    ) -> Any: ...


async def spawn_engine_process(
    *,
    instance: object,
    engine_path: str,
    working_dir: str | None = None,
    env: dict[str, str] | None = None,
    engine_args: list[str] | None = None,
    cpu_affinity: tuple[int, ...] | None = None,
) -> Any:
    """Spawn an engine process through the canonical instance runtime gateway."""

    return await EngineProcessSpawner.spawn(
        cast(Instance, instance),
        engine_path=engine_path,
        working_dir=working_dir,
        env=env,
        engine_args=engine_args,
        cpu_affinity=cpu_affinity,
    )


async def copy_directory_scp(*, instance: object, local_dir: Path, remote_dir: str) -> None:
    """Copy a local directory to a remote instance through the canonical runtime gateway."""

    await Provisioner.copy_directory_scp(cast(Instance, instance), local_dir, remote_dir)


__all__ = ["EngineRuntimePort", "copy_directory_scp", "spawn_engine_process"]
