"""
Factory utilities for constructing configured ``AsyncUsiEngine`` sessions.

Responsibilities:
- Load and normalize ``UsiEngineConfig`` from YAML
- Resolve artifacts into local binaries and provision them to remote hosts
- Rewrite path-based USI options for remote execution
- Instantiate ``SpawnerBackedUSIBridge`` and ``AsyncUsiEngine`` objects ready for use
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from shogiarena.arena.engines.usi_bridge_spawner import SpawnerBackedUSIBridge
from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.instances.models import Instance, InstanceConfig, InstanceType
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.instances.provision import Provisioner, ProvisionError
from shogiarena.arena.instances.remote_probe import detect_remote_target_cpu
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import PATH_OPTION_KEYS, resolve_path_like
from shogiarena.utils.cpuinfo import detect_target_cpu

logger = logging.getLogger(__name__)


class EngineFactory:
    """Build ``AsyncUsiEngine`` instances with optional remote provisioning support."""

    _ensured_remote_dirs: set[str] = set()
    _ensure_dir_locks: dict[str, asyncio.Lock] = {}
    _ensure_file_locks: dict[str, asyncio.Lock] = {}
    _ensure_binary_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def create_engine(
        config_path: Path,
        timeout: float = 10.0,
        extra_options: dict[str, Any] | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: InstancePool | None = None,
        cpu_affinity: Sequence[int] | None = None,
    ) -> AsyncUsiEngine:
        """Load configuration and construct a ``AsyncUsiEngine`` instance."""
        if not config_path.exists():
            raise FileNotFoundError(f"Engine config file not found: {config_path}")

        logger.debug("Creating AsyncUsiEngine from config: %s", config_path)

        config = UsiEngineConfig.from_file(
            config_path,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )

        if extra_options:
            for key in extra_options:
                if not isinstance(key, str):
                    raise TypeError(f"extra_options keys must be str; got {type(key).__name__}")
            config = config.with_overrides(
                options=extra_options,
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
            )

        instance = EngineFactory._determine_instance(instance_id, instance_pool)

        local_engine_path = config.engine_path
        if not local_engine_path:
            local_engine_path = await EngineFactory._resolve_engine_binary(
                config, instance_id=instance_id, instance_pool=instance_pool
            )
            config = replace(config, engine_path=local_engine_path)

        working_dir_candidate = config.working_directory or str(Path(local_engine_path).parent)
        engine_exec_path, working_dir_exec = await EngineFactory._compute_exec_paths(instance, local_engine_path)

        options_for_runtime = dict(config.options)
        if instance.is_ssh and options_for_runtime:
            await EngineFactory._rewrite_options_for_remote(instance, options_for_runtime)

        config = replace(
            config,
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
        )

        engine = AsyncUsiEngine(
            config=config,
            bridge=bridge,
            handshake_timeout=timeout,
        )
        return engine

    @staticmethod
    async def _resolve_engine_binary(
        config: UsiEngineConfig,
        *,
        instance_id: str | None,
        instance_pool: InstancePool | None,
    ) -> str:
        if config.engine_path:
            return config.engine_path

        # Fallback to artifact resolution when engine_path is omitted
        art = (config.artifact or "").strip()
        if not art:
            raise RuntimeError("engine config must specify either engine_path or artifact")

        resolver = ArtifactResolver(overwrite=False)
        overrides_dict = dict(config.build_options)

        inst: Instance | None = None
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            inst = instance_pool.get_instance(instance_id)
            if not inst:
                raise ValueError(f"Instance not found: {instance_id}")
        elif instance_pool is not None:
            inst = instance_pool.ensure_local_instance()

        target_cpu_value = str(overrides_dict.get("target_cpu", "")).strip()
        if not target_cpu_value or target_cpu_value.lower() == "auto":
            if inst is not None and inst.is_ssh:
                overrides_dict["target_cpu"] = await detect_remote_target_cpu(inst)
            else:
                overrides_dict["target_cpu"] = detect_target_cpu()

        return str(resolver.resolve(art, overrides=overrides_dict))

    @staticmethod
    def _determine_instance(instance_id: str | None, instance_pool: InstancePool | None) -> Instance:
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            inst = instance_pool.get_instance(instance_id)
            if not inst:
                raise ValueError(f"Instance not found: {instance_id}")
            return inst
        if instance_pool is not None:
            return instance_pool.ensure_local_instance()
        return Instance(
            config=InstanceConfig(
                name="local",
                type=InstanceType.LOCAL,
                engine_dir="",
                slots=4,
            )
        )

    @staticmethod
    async def _compute_exec_paths(instance: Instance, local_resolved: str) -> tuple[str, str]:
        if instance.is_ssh:
            system_name = platform.system()
            if system_name.lower() != "linux":
                raise RuntimeError(
                    "SSH instances require running ShogiArena on a Linux host (WSL2 is supported). "
                    f"Detected host platform: {system_name}. Please run the orchestrator on Linux/WSL "
                    "when using remote instances."
                )
            p = Path(local_resolved)
            engine_path_for_exec = str(Path(instance.config.engine_dir) / p.parent.name / p.name)
            working_dir_for_exec = str(Path(instance.config.engine_dir) / p.parent.name)
            try:
                lock = EngineFactory._ensure_binary_locks.setdefault(engine_path_for_exec, asyncio.Lock())
                async with lock:
                    await Provisioner.ensure_remote_binary(instance, p, engine_path_for_exec)
            except (ProvisionError, OSError) as exc:
                raise ProvisionError(
                    f"Failed to provision engine binary {p} on instance {instance.name}: {exc}"
                ) from exc
            return engine_path_for_exec, working_dir_for_exec
        return local_resolved, str(Path(local_resolved).parent)

    @staticmethod
    async def _rewrite_options_for_remote(instance: Instance, options: dict[str, Any]) -> None:
        _engine_dir = Path(instance.config.engine_dir)
        if _engine_dir.name != "engines" or _engine_dir.parent.name != "data":
            raise ValueError(f"Invalid instance.config.engine_dir; expected .../data/engines. Got: {_engine_dir}")
        remote_project_root = _engine_dir.parent.parent
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
                local_manifest = Provisioner.build_local_manifest(resolved)
                digest = str(local_manifest.get("digest", ""))
                short = digest[:8] if digest else "unknown"
                remote_dir_path = remote_eval_root / f"{resolved.name}-{short}"
                remote_dir = str(remote_dir_path)
                lock = EngineFactory._ensure_dir_locks.setdefault(remote_dir, asyncio.Lock())
                async with lock:
                    if remote_dir not in EngineFactory._ensured_remote_dirs:
                        t0 = time.perf_counter()
                        await Provisioner.ensure_remote_dir_by_manifest(instance, resolved, remote_dir)
                        dt = (time.perf_counter() - t0) * 1000.0
                        logger.debug(
                            "[remote-options] ensured eval dir %s -> %s in %.1f ms",
                            resolved,
                            remote_dir,
                            dt,
                        )
                        EngineFactory._ensured_remote_dirs.add(remote_dir)
                    else:
                        logger.debug("[remote-options] skip ensure (cached this run): %s", remote_dir)
                options[key] = remote_dir
            elif resolved.is_file():
                local_hash = Provisioner._sha256_file(resolved)
                short = local_hash[:8]
                remote_file_path = remote_eval_root / f"{resolved.stem}-{short}{resolved.suffix}"
                remote_file = str(remote_file_path)
                file_lock = EngineFactory._ensure_file_locks.setdefault(remote_file, asyncio.Lock())
                async with file_lock:
                    await Provisioner.ensure_remote_file(instance, resolved, remote_file)
                options[key] = remote_file
