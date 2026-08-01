"""Remote game execution via SSH instances."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import asyncssh
import yaml
from pydantic import ValidationError

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    GameExecutionResult,
    GameExecutionSpec,
)
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobIdentity,
    RemoteJobState,
    RemoteJobStatus,
    create_remote_job_identity,
)
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceType
from shogiarena._core.contexts.instances.application.remote_probe import detect_remote_target_cpu
from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport, create_transport
from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    RemoteSecretFileHandle,
    RemoteSecretFileRequest,
)
from shogiarena._core.platform.engine_provisioning.remote_artifact_store import RemoteArtifactStore
from shogiarena._core.platform.engine_provisioning.remote_deployment import (
    PreparedRemoteDeployment,
    RemoteDeploymentError,
    RemoteDeploymentManager,
)
from shogiarena._core.platform.engine_provisioning.remote_engine_config import RemoteEngineConfig
from shogiarena._core.platform.engine_provisioning.remote_job_client import (
    RemoteJobClient,
    RemoteJobClientError,
)
from shogiarena._core.platform.engine_provisioning.remote_paths import (
    RemotePathResolver,
    RemotePosixPath,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class RemoteJobExecutionOutcome:
    """Collected result and coordinator/worker provenance for one durable job."""

    events: list[JsonObject]
    identity: RemoteJobIdentity
    endpoint_identity: str
    deployment_id: str
    coordinator_started_at: datetime
    coordinator_completed_at: datetime
    worker_result: GameExecutionResult


class RemoteExecutor:
    """Run a single game on an SSH instance using an immutable deployment."""

    _prepare_tasks: dict[str, asyncio.Future[PreparedRemoteDeployment]] = {}

    def __init__(
        self,
        instance: Instance,
        worker_bundle: WorkerBundleBuildResult,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> None:
        if instance.config.type != InstanceType.SSH:
            raise ValueError("RemoteExecutor requires SSH instance")
        self.instance = instance
        self.worker_bundle = worker_bundle
        self._artifact_resolver = artifact_resolver
        self._logger = logging.getLogger(__name__)
        self._transport: SshTransport | None = None
        self._path_resolver: RemotePathResolver | None = None
        self._deployment_manager: RemoteDeploymentManager | None = None
        self._artifact_store: RemoteArtifactStore | None = None
        self._deployment: PreparedRemoteDeployment | None = None
        self._deployment_cache_key: str | None = None
        self._artifact_paths: dict[str, RemotePosixPath] = {}

    @property
    def transport(self) -> SshTransport:
        if self._transport is None:
            self._transport = create_transport(self.instance)
            self._path_resolver = RemotePathResolver(self._transport)
            self._deployment_manager = RemoteDeploymentManager(self._transport)
            self._artifact_store = RemoteArtifactStore(self._transport)
        return self._transport

    @property
    def path_resolver(self) -> RemotePathResolver:
        if self._path_resolver is None:
            raise RuntimeError("Transport not initialized")
        return self._path_resolver

    @property
    def deployment_manager(self) -> RemoteDeploymentManager:
        if self._deployment_manager is None:
            raise RuntimeError("Transport not initialized")
        return self._deployment_manager

    @property
    def artifact_store(self) -> RemoteArtifactStore:
        if self._artifact_store is None:
            raise RuntimeError("Transport not initialized")
        return self._artifact_store

    async def _ensure_connected(self) -> None:
        await self.transport.connect()

    async def close(self) -> None:
        """Close the cached SSH transport and clear endpoint-scoped helpers."""

        transport = self._transport
        self._transport = None
        self._path_resolver = None
        self._deployment_manager = None
        self._artifact_store = None
        self._deployment = None
        self._deployment_cache_key = None
        self._artifact_paths.clear()
        if transport is not None:
            await transport.close()

    async def ensure_deployment(self, remote_root: str) -> PreparedRemoteDeployment:
        """Ensure the immutable worker deployment is ready once per endpoint."""
        await self._ensure_connected()
        endpoint_identity = await self.transport.endpoint_identity()
        key = f"{endpoint_identity}::{remote_root}::{self.cache_signature()}"
        if self._deployment is not None and self._deployment_cache_key == key:
            return self._deployment
        inflight = RemoteExecutor._prepare_tasks.get(key)
        if inflight is not None:
            # A cancelled waiter must not cancel the shared preparation result.
            prepared = await asyncio.shield(inflight)
            self._deployment = prepared
            self._deployment_cache_key = key
            return prepared

        loop = asyncio.get_running_loop()
        inflight = loop.create_future()
        RemoteExecutor._prepare_tasks[key] = inflight
        try:
            absolute_root = await self.path_resolver.expand(RemotePosixPath(remote_root))
            prepared = await self.deployment_manager.prepare(
                remote_root=absolute_root,
                bundle=self.worker_bundle,
            )
            self._deployment = prepared
            self._deployment_cache_key = key
            if not inflight.done():
                inflight.set_result(prepared)
            return prepared
        except asyncio.CancelledError:
            if not inflight.done():
                inflight.cancel()
            raise
        except BaseException as exc:
            if not inflight.done():
                inflight.set_exception(exc)
                # Mark the exception as observed in case no other waiter is attached.
                inflight.exception()
            raise
        finally:
            if RemoteExecutor._prepare_tasks.get(key) is inflight:
                RemoteExecutor._prepare_tasks.pop(key, None)

    async def provision_game_engine_artifacts(
        self,
        *,
        remote_root: str,
        spec: EngineExecutionSpec,
        engine_source: Path,
        path_sources: tuple[str, ...],
    ) -> None:
        """Spec artifactsをendpoint-aware CASへ配置する。"""

        absolute_root = await self.path_resolver.expand(RemotePosixPath(remote_root))
        engine_artifact = await self.artifact_store.ensure(
            remote_root=absolute_root,
            reference=spec.process.artifact,
            local_path=engine_source,
        )
        self._artifact_paths[spec.process.artifact.logical_id] = engine_artifact.path
        if len(path_sources) != len(spec.usi.path_resources):
            raise ValueError("path resource provenance does not match GameExecutionSpec")
        for resource, source_text in zip(spec.usi.path_resources, path_sources, strict=True):
            source = Path(source_text)
            artifact = await self.artifact_store.ensure(
                remote_root=absolute_root,
                reference=resource.artifact,
                local_path=source,
            )
            self._artifact_paths[resource.artifact.logical_id] = artifact.path

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
        logical_job_key: str,
        timeout: float | None = None,
        on_event: Callable[[JsonObject], None] | None = None,
        on_assignment: Callable[[JsonObject], None] | None = None,
    ) -> RemoteJobExecutionOutcome:
        """Execute a remote pair run and return the collected JSON events."""
        coordinator_started_at = datetime.now(UTC)
        await self._ensure_connected()
        sealed_spec = GameExecutionSpec.model_validate(spec)
        deployment = self._deployment
        if deployment is None:
            raise RuntimeError("remote worker deployment is not prepared")
        instance_root = await self.path_resolver.expand(RemotePosixPath(remote_root))
        client = RemoteJobClient(self.transport, deployment_root=deployment.root)
        try:
            await client.reap(instance_root)
        except RemoteJobClientError as exc:
            await self._reverify_deployment_after_control_failure(deployment, exc)
            raise
        endpoint_identity = await self.transport.endpoint_identity()
        identity = await client.resolve_identity(
            remote_root=instance_root,
            logical_job_key=logical_job_key,
            candidate=create_remote_job_identity(sealed_spec),
        )
        assignment: JsonObject = {
            "schema_version": "shogiarena.remote-assignment.v1",
            "instance_id": self.instance.name,
            "endpoint_identity": endpoint_identity,
            "deployment_id": deployment.deployment_id,
            "job_id": identity.job_id,
            "attempt_id": identity.attempt_id,
            "execution_digest": identity.execution_digest,
            "artifact_digests": sorted(
                {
                    sealed_spec.black_engine.process.artifact.sha256,
                    sealed_spec.white_engine.process.artifact.sha256,
                    *(resource.artifact.sha256 for resource in sealed_spec.black_engine.usi.path_resources),
                    *(resource.artifact.sha256 for resource in sealed_spec.white_engine.usi.path_resources),
                }
            ),
        }
        if on_assignment is not None:
            on_assignment(assignment)
        job_root = instance_root / "jobs" / identity.job_id
        secret_refs = _engine_secret_references(sealed_spec)
        missing_refs = sorted(secret_ref for secret_ref in secret_refs if secret_ref not in os.environ)
        if missing_refs:
            raise ValueError(f"required engine secret references are unavailable: {', '.join(missing_refs)}")
        engine_secret_values = {secret_ref: os.environ[secret_ref] for secret_ref in secret_refs}
        secret_files: list[RemoteSecretFileRequest] = []
        if engine_secret_values:
            secret_files.append(
                RemoteSecretFileRequest(
                    file_id="engine_secret_bundle",
                    payload=json.dumps(engine_secret_values, sort_keys=True).encode("utf-8"),
                    mode=0o600,
                )
            )

        await self.deployment_manager.acquire_lease(deployment, identity.job_id)
        lease_release_safe = True
        try:
            await self._retry_after_reconnect(
                lambda: client.prepare(
                    job_root=job_root,
                    identity=identity,
                    spec=sealed_spec,
                )
            )
            await self._retry_after_reconnect(lambda: self._prepare_job_artifacts(job_root, sealed_spec))
            if secret_files:

                def build_command(handles: Mapping[str, RemoteSecretFileHandle]) -> str:
                    return client.build_start_command(
                        job_root=job_root,
                        secret_bundle_file=(
                            handles["engine_secret_bundle"].remote_path if "engine_secret_bundle" in handles else None
                        ),
                    )

                async def start_with_secret() -> RemoteJobStatus:
                    rc, stdout, stderr = await self.transport.run_with_secret_files(
                        build_command,
                        secret_files,
                    )
                    if rc != 0:
                        raise RemoteJobClientError(
                            f"failed to prepare and start remote job: {stderr.strip() or stdout.strip() or rc}"
                        )
                    return client.parse_status_response(stdout)

                lease_release_safe = False
                started_status = await self._start_with_recovery(
                    client,
                    job_root=job_root,
                    start=start_with_secret,
                )
            else:
                lease_release_safe = False
                started_status = await self._start_with_recovery(
                    client,
                    job_root=job_root,
                    start=lambda: client.start(job_root=job_root),
                )
            if started_status.state in {
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                lease_release_safe = True
            wait_timeout = timeout
            if wait_timeout is None:
                wait_timeout = (sealed_spec.time.startup_grace_ms + sealed_spec.time.outer_deadline_ms + 15_000) / 1000
            try:
                status = await client.wait_for_terminal(job_root=job_root, timeout=wait_timeout)
            except (asyncssh.Error, OSError):
                await self.transport.close()
                await self.transport.connect()
                status = await client.wait_for_terminal(job_root=job_root, timeout=wait_timeout)
            if status.terminal_kind != "completed":
                lease_release_safe = await _cleanup_and_acknowledge_observed_terminal(
                    client,
                    job_root=job_root,
                    identity=identity,
                    logger=self._logger,
                )
                raise RuntimeError(
                    f"remote job terminated as {status.terminal_kind}: {status.error or 'no diagnostic'}"
                )
            events, result = await self._retry_after_reconnect(lambda: client.collect(job_root=job_root, on_event=None))
            if result.execution_digest != sealed_spec.execution_digest:
                raise RuntimeError("remote job result execution digest does not match dispatched spec")
            if result.logical_game_id != sealed_spec.identity.game_id:
                raise RuntimeError("remote job result game ID does not match dispatched spec")
            if not any(event.get("schema_version") == result.payload.schema_version for event in events):
                payload: JsonObject = {
                    str(key): json_serialize(value) for key, value in result.payload.model_dump(mode="json").items()
                }
                events.append(payload)
            if on_event is not None:
                for event in events:
                    on_event(event)
            lease_release_safe = await _cleanup_and_acknowledge_observed_terminal(
                client,
                job_root=job_root,
                identity=identity,
                logger=self._logger,
            )
            if not lease_release_safe:
                raise RemoteJobClientError(
                    f"remote job result was collected but acknowledgement failed: {identity.job_id}"
                )
            return RemoteJobExecutionOutcome(
                events=events,
                identity=identity,
                endpoint_identity=endpoint_identity,
                deployment_id=deployment.deployment_id,
                coordinator_started_at=coordinator_started_at,
                coordinator_completed_at=datetime.now(UTC),
                worker_result=result.payload,
            )
        except TimeoutError:
            try:
                cancelled = await asyncio.shield(client.cancel(job_root))
                if cancelled.state in {
                    RemoteJobState.TERMINAL,
                    RemoteJobState.ACKNOWLEDGED,
                    RemoteJobState.COLLECTABLE,
                }:
                    lease_release_safe = await asyncio.shield(
                        _cleanup_and_acknowledge_observed_terminal(
                            client,
                            job_root=job_root,
                            identity=identity,
                            logger=self._logger,
                        )
                    )
            except (asyncssh.Error, OSError, RuntimeError) as exc:
                self._logger.error("Failed to cancel timed-out remote job %s: %s", identity.job_id, exc)
            raise
        except asyncio.CancelledError:
            try:
                cancelled = await asyncio.shield(client.cancel(job_root))
                if cancelled.state in {
                    RemoteJobState.TERMINAL,
                    RemoteJobState.ACKNOWLEDGED,
                    RemoteJobState.COLLECTABLE,
                }:
                    lease_release_safe = await asyncio.shield(
                        _cleanup_and_acknowledge_observed_terminal(
                            client,
                            job_root=job_root,
                            identity=identity,
                            logger=self._logger,
                        )
                    )
            except (asyncssh.Error, OSError, RuntimeError) as exc:
                self._logger.error("Failed to cancel remote job %s: %s", identity.job_id, exc)
            raise
        except RemoteJobClientError as exc:
            await self._reverify_deployment_after_control_failure(deployment, exc)
            raise
        finally:
            if lease_release_safe:
                try:
                    await self.deployment_manager.release_lease(deployment, identity.job_id)
                except (asyncssh.Error, OSError, RuntimeError) as exc:
                    self._logger.error("Failed to release remote deployment lease: %s", exc)
            else:
                self._logger.warning(
                    "Retaining deployment lease for remote job %s with an unknown or live outcome",
                    identity.job_id,
                )

    async def _reverify_deployment_after_control_failure(
        self,
        deployment: PreparedRemoteDeployment,
        error: RemoteJobClientError,
    ) -> None:
        try:
            await self.deployment_manager.verify(
                deployment,
                python_version=self.worker_bundle.manifest.python_version,
            )
        except RemoteDeploymentError:
            self._deployment = None
            self._deployment_cache_key = None
            raise
        self._logger.debug(
            "Remote worker control failed but deployment integrity remains valid: %s",
            error,
        )

    async def _retry_after_reconnect(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        try:
            return await operation()
        except (asyncssh.Error, OSError):
            await self.transport.close()
            await self.transport.connect()
            return await operation()

    async def _start_with_recovery(
        self,
        client: RemoteJobClient,
        *,
        job_root: RemotePosixPath,
        start: Callable[[], Awaitable[RemoteJobStatus]],
    ) -> RemoteJobStatus:
        try:
            return await start()
        except (asyncssh.Error, OSError):
            await self.transport.close()
            await self.transport.connect()
            status = await client.query_status(job_root)
            if status.state == RemoteJobState.PREPARED:
                return await start()
            if status.state in {
                RemoteJobState.STARTING,
                RemoteJobState.RUNNING,
                RemoteJobState.CANCELLING,
                RemoteJobState.TERMINAL,
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                return status
            raise RemoteJobClientError(
                f"remote job start response was lost in unrecoverable state {status.state}: {status.identity.job_id}"
            ) from None

    def cache_signature(self) -> str:
        """Return the immutable deployment cache signature."""

        return self.worker_bundle.manifest.deployment_id

    async def _prepare_job_artifacts(self, job_root: RemotePosixPath, spec: GameExecutionSpec) -> None:
        commands = [f"mkdir -p {job_root.shell_quote()}"]
        for engine in (spec.black_engine, spec.white_engine):
            artifact = engine.process.artifact
            source = self._artifact_paths.get(artifact.logical_id)
            if source is None or artifact.entrypoint is None:
                raise RuntimeError(f"remote artifact is not provisioned: {artifact.logical_id}")
            target = job_root.join_relative(engine.process.working_directory).join_relative(artifact.entrypoint)
            commands.append(f"mkdir -p {target.parent.shell_quote()}")
            commands.append(f"ln -sfn {source.shell_quote()} {target.shell_quote()}")
            for resource in engine.usi.path_resources:
                resource_source = self._artifact_paths.get(resource.artifact.logical_id)
                if resource_source is None:
                    raise RuntimeError(f"remote artifact is not provisioned: {resource.artifact.logical_id}")
                resource_target = job_root.join_relative(resource.target_relative_path)
                commands.append(f"mkdir -p {resource_target.parent.shell_quote()}")
                commands.append(f"ln -sfn {resource_source.shell_quote()} {resource_target.shell_quote()}")
        rc, stdout, stderr = await self.transport.run("; ".join(commands))
        if rc != 0:
            raise RuntimeError(f"failed to materialize remote job artifacts: {stderr.strip() or stdout.strip()}")


def _engine_secret_references(spec: GameExecutionSpec) -> set[str]:
    return {
        *spec.black_engine.process.secret_environment_refs.values(),
        *spec.white_engine.process.secret_environment_refs.values(),
    }


async def _acknowledge_observed_terminal(
    client: RemoteJobClient,
    *,
    job_root: RemotePosixPath,
    identity: RemoteJobIdentity,
    logger: logging.Logger,
) -> bool:
    try:
        acknowledged = await client.acknowledge(job_root)
    except (asyncssh.Error, OSError, RuntimeError) as exc:
        logger.error("Failed to acknowledge terminal remote job %s: %s", identity.job_id, exc)
        return False
    return acknowledged.state in {
        RemoteJobState.ACKNOWLEDGED,
        RemoteJobState.COLLECTABLE,
    }


async def _cleanup_and_acknowledge_observed_terminal(
    client: RemoteJobClient,
    *,
    job_root: RemotePosixPath,
    identity: RemoteJobIdentity,
    logger: logging.Logger,
) -> bool:
    try:
        await client.cleanup_secret(job_root)
    except (asyncssh.Error, OSError, RuntimeError) as exc:
        logger.error("Failed to clean up secret for remote job %s: %s", identity.job_id, exc)
    return await _acknowledge_observed_terminal(
        client,
        job_root=job_root,
        identity=identity,
        logger=logger,
    )


__all__ = [
    "RemoteExecutor",
    "RemoteJobExecutionOutcome",
]
