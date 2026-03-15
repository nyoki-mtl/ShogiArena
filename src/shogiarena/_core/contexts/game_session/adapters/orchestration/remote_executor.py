"""Remote game execution via SSH instances."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceType
from shogiarena._core.contexts.instances.application.provisioner import Provisioner
from shogiarena._core.contexts.instances.application.remote_probe import detect_remote_target_cpu
from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport, create_transport
from shogiarena._core.platform.engine_provisioning.remote_engine_config import RemoteEngineConfig
from shogiarena._core.platform.engine_provisioning.remote_execution_config import RemoteExecutionConfig
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePathResolver, RemoteProjectLocator
from shogiarena._core.platform.engine_provisioning.remote_repo_manager import RemoteRepoSpec, RemoteRepoSynchronizer
from shogiarena._core.platform.engine_provisioning.remote_repo_preparer import RemoteRepoPreparer
from shogiarena._core.platform.engine_provisioning.remote_stream_runner import (
    RemoteStreamConsumer,
    build_remote_runner_command,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort


class RemoteExecutor:
    """Run a single game on an SSH instance using a cloned repo and uv."""

    _prepared_roots: set[str] = set()
    _prepare_tasks: dict[str, asyncio.Future[None]] = {}

    def __init__(
        self,
        instance: Instance,
        repo: RemoteRepoSpec,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> None:
        if instance.config.type != InstanceType.SSH:
            raise ValueError("RemoteExecutor requires SSH instance")
        self.instance = instance
        self.repo = repo
        self._artifact_resolver = artifact_resolver
        self._logger = logging.getLogger(__name__)
        self._config = RemoteExecutionConfig.from_env()
        self._project_locator = RemoteProjectLocator()
        self._transport: SshTransport | None = None
        self._path_resolver: RemotePathResolver | None = None
        self._repo_preparer: RemoteRepoPreparer | None = None
        self._stream_consumer: RemoteStreamConsumer | None = None
        self._provisioned_bins: set[str] = set()

    @property
    def transport(self) -> SshTransport:
        if self._transport is None:
            self._transport = create_transport(self.instance)
            self._path_resolver = RemotePathResolver(self._transport)

            async def _copy_directory(instance: Instance, local_dir: Path, remote_dir: str) -> None:
                await Provisioner.copy_directory_scp(instance, local_dir, remote_dir)

            self._repo_preparer = RemoteRepoPreparer(
                instance=self.instance,
                repo=self.repo,
                transport=self._transport,
                config=self._config,
                path_resolver=self._path_resolver,
                project_locator=self._project_locator,
                repo_provisioner_factory=RemoteRepoSynchronizer,
                copy_directory=_copy_directory,
                logger=self._logger,
            )
            self._stream_consumer = RemoteStreamConsumer(self.instance, self._transport, self._logger)
        return self._transport

    @property
    def path_resolver(self) -> RemotePathResolver:
        if self._path_resolver is None:
            raise RuntimeError("Transport not initialized")
        return self._path_resolver

    @property
    def repo_preparer(self) -> RemoteRepoPreparer:
        if self._repo_preparer is None:
            raise RuntimeError("Transport not initialized")
        return self._repo_preparer

    @property
    def stream_consumer(self) -> RemoteStreamConsumer:
        if self._stream_consumer is None:
            raise RuntimeError("Transport not initialized")
        return self._stream_consumer

    async def _ensure_connected(self) -> None:
        await self.transport.connect()

    async def ensure_repo(self, remote_root: str) -> None:
        """Ensure the remote repository (plus optional overlay) is ready once per root."""
        await self._ensure_connected()
        key = self._cache_key(remote_root)
        if key in RemoteExecutor._prepared_roots:
            return

        inflight = RemoteExecutor._prepare_tasks.get(key)
        if inflight is not None:
            await inflight
            return

        loop = asyncio.get_running_loop()
        inflight = loop.create_future()
        RemoteExecutor._prepare_tasks[key] = inflight
        try:
            absolute_root = await self.path_resolver.expand(remote_root)
            await self.repo_preparer.ensure_repo(remote_root=remote_root, absolute_root=absolute_root)
            await self.repo_preparer.ensure_overlay(absolute_root)
            RemoteExecutor._prepared_roots.add(key)
            if not inflight.done():
                inflight.set_result(None)
        except (OSError, RuntimeError, ValueError) as exc:
            if not inflight.done():
                inflight.set_exception(exc)
                # Mark the exception as observed in case no other waiter is attached.
                inflight.exception()
            raise
        finally:
            RemoteExecutor._prepare_tasks.pop(key, None)

    async def provision_engine_binary(self, local_bin: Path) -> str:
        """Upload the engine binary if needed and return its remote path."""
        remote_dir = Path(self.instance.config.engine_dir) / local_bin.parent.name
        remote_bin = str(remote_dir / local_bin.name)
        if remote_bin in self._provisioned_bins:
            self._logger.debug("[%s] engine ready: %s", self.instance.name, remote_bin)
            return remote_bin
        await Provisioner.ensure_remote_binary(self.instance, local_bin, remote_bin)
        self._provisioned_bins.add(remote_bin)
        self._logger.debug("[%s] engine ready: %s", self.instance.name, remote_bin)
        return remote_bin

    async def resolve_local_binary(self, config_path: Path) -> Path:
        """Resolve a local engine binary path from configuration or artifacts."""
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        try:
            config = RemoteEngineConfig.model_validate(raw_config)
        except ValidationError as exc:
            raise ValueError(f"invalid engine config: {exc}") from exc

        if config.engine_path:
            return Path(config.engine_path)
        artifact = config.artifact
        if not artifact:
            raise ValueError("engine config must specify engine_path or artifact")
        overrides = self._normalize_build_overrides(config.build_options)
        target_cpu = str(overrides.get("target_cpu", "")).strip()
        if not target_cpu or target_cpu.lower() == "auto":
            overrides["target_cpu"] = await detect_remote_target_cpu(self.instance)

        if self._artifact_resolver is None:
            raise RuntimeError("artifact_resolver not configured on RemoteExecutor")
        return self._artifact_resolver(artifact, overrides)

    def _normalize_build_overrides(self, raw: Mapping[str, JsonScalar] | None) -> JsonObject:
        if raw is None:
            return {}
        if isinstance(raw, Mapping):
            return {str(k): json_serialize(v) for k, v in raw.items()}
        raise TypeError("build_options must be a mapping")

    async def run_remote_pair(
        self,
        *,
        remote_root: str,
        spec: JsonObject,
        timeout: float | None = None,
        on_event: Callable[[JsonObject], None] | None = None,
    ) -> list[JsonObject]:
        """Execute a remote pair run and return the collected JSON events."""
        await self._ensure_connected()
        base_abs = await self.path_resolver.expand(remote_root)
        remote_spec = f"{base_abs}/.tmp/spec.json"
        spec_json = json.dumps(spec, ensure_ascii=False)
        command = build_remote_runner_command(
            base_abs,
            remote_spec,
            spec_json,
            github_token=self._config.github_token if self._config.should_export_github_token else None,
        )
        return await self.stream_consumer.collect(command, timeout=timeout, on_event=on_event)

    def _cache_key(self, remote_root: str) -> str:
        return f"{self.instance.name}::{remote_root}::{self.cache_signature()}"

    def cache_signature(self) -> str:
        """Return a cache signature for repo sync decisions."""
        override_ref = self._config.override_ref or ""
        sync_mode = self._config.sync_mode.value
        return f"{self.repo.url}@{self.repo.ref}:{override_ref}:{sync_mode}"


__all__ = [
    "RemoteExecutor",
]
