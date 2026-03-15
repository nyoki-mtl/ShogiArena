"""Platform-level engine runtime factory implementation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import platform as _platform
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from shogiarena._core.platform.engine_provisioning.provisioning_ports import EngineRuntimeInstancePort
from shogiarena._core.platform.engine_provisioning.runtime_factory_fallbacks import FallbackInstance
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge
from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.paths import PATH_OPTION_KEYS, resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort

logger = logging.getLogger(__name__)


class _InstancePoolPort(Protocol):
    def get_instance(self, instance_id: str) -> EngineRuntimeInstancePort | None: ...

    def ensure_local_instance(self) -> EngineRuntimeInstancePort: ...


class _EngineRuntimeSupportPort(Protocol):
    def detect_remote_target_cpu(self, instance: object) -> str | Awaitable[str]: ...

    async def ensure_remote_binary(self, instance: object, local_binary: Path, remote_binary: str) -> None: ...

    def build_local_manifest(self, path: Path) -> Mapping[str, object]: ...

    def file_sha256(self, path: Path) -> str: ...

    async def ensure_remote_dir_by_manifest(self, instance: object, local_dir: Path, remote_dir: str) -> None: ...

    async def ensure_remote_file(self, instance: object, local_file: Path, remote_file: str) -> None: ...


class _BinaryResolutionConfigPort(Protocol):
    artifact: str | None
    build_options: Mapping[str, object]


_EngineConfigFactory = Callable[[Path], Any]
_EngineMappingFactory = Callable[[Mapping[str, object]], Any]
_EngineSessionFactory = Callable[..., Any]


class EngineRuntimeFactory:
    """Concrete implementation of engine runtime creation for shared contexts."""

    _ensured_remote_dirs: set[str] = set()
    _ensure_dir_locks: dict[str, asyncio.Lock] = {}
    _ensure_file_locks: dict[str, asyncio.Lock] = {}
    _ensure_binary_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        *,
        process_spawner: Callable[..., Awaitable[Any]],
        support: _EngineRuntimeSupportPort,
        engine_config_factory: _EngineConfigFactory,
        mapping_config_factory: _EngineMappingFactory,
        engine_session_factory: _EngineSessionFactory,
    ) -> None:
        self._process_spawner = process_spawner
        self._support = support
        self._engine_config_factory = engine_config_factory
        self._mapping_config_factory = mapping_config_factory
        self._engine_session_factory = engine_session_factory

    async def create_engine(
        self,
        config_path: Path,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: _InstancePoolPort | None = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        config = self._engine_config_factory(config_path)
        config = self._apply_overrides(
            config,
            extra_options=extra_options,
        )
        return await self._create_engine_from_config(
            config,
            timeout=timeout,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=artifact_resolver,
        )

    async def create_engine_from_mapping(
        self,
        config_mapping: Mapping[str, object],
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: _InstancePoolPort | None = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        config = self._mapping_config_factory(config_mapping)
        config = self._apply_overrides(
            config,
            extra_options=extra_options,
        )
        return await self._create_engine_from_config(
            config,
            timeout=timeout,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=artifact_resolver,
        )

    async def _create_engine_from_config(
        self,
        config: Any,
        *,
        timeout: float,
        engine_name: str | None,
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
        cpu_affinity: Sequence[int] | None,
        artifact_resolver: ArtifactResolutionPort | None,
    ) -> Any:
        instance = self._determine_instance(instance_id, instance_pool)

        local_engine_path = config.engine_path
        if not local_engine_path:
            local_engine_path = await self._resolve_engine_binary(
                config,
                instance_id=instance_id,
                instance_pool=instance_pool,
                artifact_resolver=artifact_resolver,
            )
            config = config.with_overrides(engine_path=local_engine_path)

        working_dir_candidate = config.working_directory or str(Path(local_engine_path).parent)
        engine_exec_path, working_dir_exec = await self._compute_exec_paths(instance, local_engine_path)

        options_for_runtime = {str(key): value for key, value in config.options.items()}
        if instance.is_ssh and options_for_runtime:
            await self._rewrite_options_for_remote(instance, options_for_runtime)

        config = config.with_overrides(
            name=engine_name or config.name,
            engine_path=engine_exec_path,
            working_directory=working_dir_exec or working_dir_candidate,
            options=options_for_runtime,
        )

        bridge = SpawnerBackedUSIBridge(
            instance=instance,
            engine_path=engine_exec_path,
            working_dir=working_dir_exec or working_dir_candidate,
            name=config.name,
            engine_args=list(config.engine_args),
            env=dict(config.environment),
            cpu_affinity=tuple(cpu_affinity) if cpu_affinity else None,
            engine_spawner=self._process_spawner,
        )

        handshake_timeout = config.handshake_timeout if config.handshake_timeout is not None else timeout
        return self._engine_session_factory(
            config=config,
            bridge=bridge,
            handshake_timeout=handshake_timeout,
        )

    async def _rewrite_options_for_remote(self, instance: EngineRuntimeInstancePort, options: JsonObject) -> None:
        engine_dir = Path(instance.config.engine_dir)
        if engine_dir.name != "engines" or engine_dir.parent.name != "data":
            raise ValueError(f"Invalid instance.config.engine_dir; expected .../data/engines. Got: {engine_dir}")
        remote_project_root = engine_dir.parent.parent
        remote_eval_root = remote_project_root / "data" / "evals"

        for key, value in list(options.items()):
            if not isinstance(value, str):
                continue
            if key not in PATH_OPTION_KEYS:
                if ("/" in value) or ("\\" in value):
                    logger.warning(
                        "USI option '%s' value contains a path-like separator but key is not path-typed; leaving as-is",
                        key,
                    )
                continue

            resolved = Path(resolve_path_like(value)).resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Extra option '{key}' references missing path: {resolved}")
            if resolved.is_dir():
                local_manifest = self._support.build_local_manifest(resolved)
                digest = str(local_manifest.get("digest", ""))
                short = digest[:8] if digest else "unknown"
                remote_dir_path = remote_eval_root / f"{resolved.name}-{short}"
                remote_dir = str(remote_dir_path)
                lock = self._ensure_dir_locks.setdefault(remote_dir, asyncio.Lock())
                async with lock:
                    if remote_dir not in self._ensured_remote_dirs:
                        await self._support.ensure_remote_dir_by_manifest(instance, resolved, remote_dir)
                        self._ensured_remote_dirs.add(remote_dir)
                    else:
                        logger.debug("[remote-options] skip ensure (cached this run): %s", remote_dir)
                options[key] = remote_dir
            elif resolved.is_file():
                local_hash = self._support.file_sha256(resolved)
                short = local_hash[:8]
                remote_file_path = remote_eval_root / f"{resolved.stem}-{short}{resolved.suffix}"
                remote_file = str(remote_file_path)
                file_lock = self._ensure_file_locks.setdefault(remote_file, asyncio.Lock())
                async with file_lock:
                    await self._support.ensure_remote_file(instance, resolved, remote_file)
                options[key] = remote_file

    async def _resolve_engine_binary(
        self,
        config: _BinaryResolutionConfigPort,
        *,
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> str:
        artifact = (config.artifact or "").strip()
        if not artifact:
            raise RuntimeError("engine config must specify either engine_path or artifact")

        resolver = _maybe_resolve_resolver(artifact_resolver)
        instance: EngineRuntimeInstancePort | None = None
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            instance = instance_pool.get_instance(instance_id)
            if instance is None:
                raise ValueError(f"Instance not found: {instance_id}")
        elif instance_pool is not None:
            instance = instance_pool.ensure_local_instance()

        build_options = dict(config.build_options)
        target_cpu_value = str(build_options.get("target_cpu", "")).strip()
        if not target_cpu_value or target_cpu_value.lower() == "auto":
            if instance is not None and instance.is_ssh:
                detected_cpu = self._support.detect_remote_target_cpu(instance)
                if inspect.isawaitable(detected_cpu):
                    detected_cpu = await detected_cpu
                if not isinstance(detected_cpu, str):
                    raise TypeError(
                        f"detect_remote_target_cpu must return a string (got: {type(detected_cpu).__name__})"
                    )
                build_options["target_cpu"] = detected_cpu
            else:
                build_options["target_cpu"] = detect_target_cpu()

        return str(resolver(artifact, build_options))

    async def _compute_exec_paths(
        self,
        instance: EngineRuntimeInstancePort,
        local_resolved: str,
    ) -> tuple[str, str]:
        if instance.is_ssh:
            system_name = _platform.system()
            if system_name.lower() != "linux":
                raise RuntimeError(
                    "SSH instances require running ShogiArena on a Linux host (WSL2 is supported). "
                    f"Detected host platform: {system_name}. Please run orchestrator on Linux/WSL "
                    "when using remote instances."
                )
            path = Path(local_resolved)
            instance_engine_dir = Path(instance.config.engine_dir)
            engine_path_for_exec = str(instance_engine_dir / path.parent.name / path.name)
            working_dir_for_exec = str(instance_engine_dir / path.parent.name)
            lock = self._ensure_binary_locks.setdefault(engine_path_for_exec, asyncio.Lock())
            try:
                async with lock:
                    await self._support.ensure_remote_binary(instance, path, engine_path_for_exec)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(
                    f"Failed to provision engine binary {path} on instance {instance.name}: {exc}"
                ) from exc
            return engine_path_for_exec, working_dir_for_exec

        return local_resolved, str(Path(local_resolved).parent)

    @staticmethod
    def _determine_instance(
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
    ) -> EngineRuntimeInstancePort:
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            instance = instance_pool.get_instance(instance_id)
            if instance is None:
                raise ValueError(f"Instance not found: {instance_id}")
            return instance

        if instance_pool is not None:
            return instance_pool.ensure_local_instance()

        return FallbackInstance()

    @staticmethod
    def _apply_overrides(
        config: Any,
        *,
        extra_options: JsonObject | None,
    ) -> Any:
        if extra_options:
            for key in extra_options:
                if not isinstance(key, str):
                    raise TypeError(f"extra_options keys must be str; got {type(key).__name__}")
            config = config.with_overrides(
                options=_cast_json_object_as_str(extra_options),
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
            )
        return config.resolve_isready_lock_key()


def _maybe_resolve_resolver(
    resolver: ArtifactResolutionPort | None,
) -> ArtifactResolutionPort:
    if resolver is None:
        raise ValueError("artifact_resolver is required; inject via EngineFactoryService")
    return resolver


def _cast_json_object_as_str(values: Mapping[str, object]) -> JsonObject:
    return {str(key): json_serialize(value) for key, value in values.items()}


__all__ = [
    "EngineRuntimeFactory",
]
