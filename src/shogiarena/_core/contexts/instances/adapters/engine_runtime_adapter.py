"""Runtime adapter for instance application."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_worker import (
    GameExecutionWorkerAdapter,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionSpec
from shogiarena._core.contexts.game_session.ports.game_execution_worker import GameExecutionOutcome
from shogiarena._core.contexts.instances.application.engine_process_spawner import EngineProcessSpawner
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.provisioner import Provisioner
from shogiarena._core.contexts.instances.application.remote_probe import detect_remote_target_cpu
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.instances.ports.engine_runtime_port import EngineOptionValidationMode
from shogiarena._core.platform.engine_provisioning.runtime_factory import EngineRuntimeFactory
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize


class EngineRuntimeAdapter:
    """Adapter delegating engine runtime creation to the engine factory."""

    def __init__(
        self,
        *,
        engine_factory_service: EngineFactoryService,
        game_execution_instance_pool: InstancePool,
    ) -> None:
        self._engine_factory_service = engine_factory_service
        self._game_execution_worker = GameExecutionWorkerAdapter(
            engine_factory_service=engine_factory_service,
            instance_pool=game_execution_instance_pool,
        )

    async def execute(
        self,
        spec: GameExecutionSpec,
        *,
        execution_root: Path,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        secret_values: Mapping[str, str] | None = None,
    ) -> GameExecutionOutcome:
        """Sealed GameExecutionSpecを共通workerへ委譲する。"""

        return await self._game_execution_worker.execute(
            spec,
            execution_root=execution_root,
            progress_queue=progress_queue,
            secret_values=secret_values,
        )

    def request_shutdown(self) -> None:
        """実行中の一局へ停止を伝播する。"""

        self._game_execution_worker.request_shutdown()

    @staticmethod
    def _coerce_config_path(config_path: Any) -> Path:
        if isinstance(config_path, Path):
            return config_path
        if isinstance(config_path, str):
            return Path(config_path)
        if isinstance(config_path, bytes):
            return Path(config_path.decode())
        path = Path(config_path)
        return path

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
        collect_info_strings: bool | None = None,
        collect_raw_io: bool | None = None,
        collect_stderr: bool | None = None,
        collect_outbound: bool | None = None,
        option_validation: EngineOptionValidationMode | None = None,
    ) -> Any:
        resolved_path = self._coerce_config_path(config_path)
        return await self._engine_factory_service.create_engine(
            resolved_path,
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
        self,
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
        option_validation: EngineOptionValidationMode | None = None,
    ) -> Any:
        normalized_mapping = {str(key): json_serialize(value) for key, value in config_mapping.items()}
        return await self._engine_factory_service.create_engine_from_mapping(
            normalized_mapping,
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


class _DefaultEngineRuntimeSupport:
    """Default instances runtime-support adapter for the platform engine factory."""

    @staticmethod
    def _require_instance(instance: object) -> Instance:
        if not isinstance(instance, Instance):
            raise TypeError(f"instance must be Instance, got {type(instance).__name__}")
        return instance

    @staticmethod
    def detect_remote_target_cpu(instance: object) -> Awaitable[str]:
        return detect_remote_target_cpu(_DefaultEngineRuntimeSupport._require_instance(instance))

    @staticmethod
    async def ensure_remote_binary(instance: object, local_binary: Path, remote_binary: str) -> None:
        await Provisioner.ensure_remote_binary(
            _DefaultEngineRuntimeSupport._require_instance(instance),
            local_binary,
            remote_binary,
        )

    @staticmethod
    def build_local_manifest(path: Path) -> Mapping[str, object]:
        return Provisioner.build_local_manifest(path)

    @staticmethod
    def file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    async def ensure_remote_dir_by_manifest(instance: object, local_dir: Path, remote_dir: str) -> None:
        await Provisioner.ensure_remote_dir_by_manifest(
            _DefaultEngineRuntimeSupport._require_instance(instance),
            local_dir,
            remote_dir,
        )

    @staticmethod
    async def ensure_remote_file(instance: object, local_file: Path, remote_file: str) -> None:
        await Provisioner.ensure_remote_file(
            _DefaultEngineRuntimeSupport._require_instance(instance),
            local_file,
            remote_file,
        )

    @staticmethod
    async def verify_remote_file_sha256(instance: object, remote_file: str, expected_sha256: str) -> bool:
        return await Provisioner.verify_remote_file_sha256(
            _DefaultEngineRuntimeSupport._require_instance(instance),
            remote_file,
            expected_sha256,
        )


def create_default_engine_runtime_factory(
    *,
    process_spawner: Callable[..., Awaitable[Any]] = EngineProcessSpawner.spawn,
) -> EngineRuntimeFactory:
    """Create an EngineRuntimeFactory with default ShogiArena instance support."""
    return EngineRuntimeFactory(
        process_spawner=process_spawner,
        support=_DefaultEngineRuntimeSupport(),
        engine_config_factory=lambda path: UsiEngineConfig.from_file(
            path,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        ),
        mapping_config_factory=lambda raw: UsiEngineConfig.from_mapping(raw).resolve_paths(
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        ),
        engine_session_factory=AsyncUsiEngine,
    )


__all__ = [
    "EngineRuntimeAdapter",
    "create_default_engine_runtime_factory",
]
