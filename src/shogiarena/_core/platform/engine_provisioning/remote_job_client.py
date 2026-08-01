"""Coordinator client for agentless durable Remote jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionSpec
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobIdentity,
    RemoteJobResult,
    RemoteJobState,
    RemoteJobStatus,
)
from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshFileTransportPort
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

_POLL_INTERVAL_SEC = 0.25
ModelT = TypeVar("ModelT", bound=BaseModel)


class RemoteJobClientError(RuntimeError):
    """Remote durable job control or validation failure."""


class RemoteJobClient:
    """Use short SSH commands to start, query, collect, and acknowledge a job."""

    def __init__(
        self,
        transport: SshFileTransportPort,
        *,
        deployment_root: RemotePosixPath,
    ) -> None:
        self._transport = transport
        self._deployment_root = deployment_root

    async def resolve_identity(
        self,
        *,
        remote_root: RemotePosixPath,
        logical_job_key: str,
        candidate: RemoteJobIdentity,
    ) -> RemoteJobIdentity:
        """Persist or recover the one durable identity for a logical attempt."""

        key = hashlib.sha256(logical_job_key.encode("utf-8")).hexdigest()
        assignments = remote_root / "jobs" / "assignments"
        locks = remote_root / "jobs" / "locks"
        assignment = assignments / f"{key}.json"
        candidate_path = assignments / f".{key}.{candidate.job_id}.tmp"
        lock = locks / f"{key}.lock"
        inner = (
            f"if [ -f {assignment.shell_quote()} ]; then cat {assignment.shell_quote()}; "
            f"else mv -T {candidate_path.shell_quote()} {assignment.shell_quote()}; "
            f"cat {assignment.shell_quote()}; fi"
        )
        command = "\n".join(
            [
                "set -eu",
                f"mkdir -p {assignments.shell_quote()} {locks.shell_quote()}",
                _heredoc_write(candidate_path, candidate.model_dump_json(), token="__ARENA_JOB_ASSIGNMENT__"),
                f"flock -x {lock.shell_quote()} bash -c {shlex.quote(inner)}",
                f"rm -f -- {candidate_path.shell_quote()}",
            ]
        )
        stdout = await self._run_checked(command, action="resolve remote job identity")
        identity = _parse_last_json_model(stdout, RemoteJobIdentity)
        if identity.logical_game_id != candidate.logical_game_id:
            raise RemoteJobClientError("persisted remote job identity has a different logical game ID")
        if identity.execution_digest != candidate.execution_digest:
            raise RemoteJobClientError("persisted remote job identity has a different execution digest")
        return identity

    async def prepare_and_start(
        self,
        *,
        job_root: RemotePosixPath,
        identity: RemoteJobIdentity,
        spec: GameExecutionSpec,
        secret_bundle_file: str | None = None,
    ) -> RemoteJobStatus:
        """Prepare immutable job inputs and idempotently start the supervisor."""

        await self.prepare(job_root=job_root, identity=identity, spec=spec)
        return await self.start(
            job_root=job_root,
            secret_bundle_file=secret_bundle_file,
        )

    async def prepare(
        self,
        *,
        job_root: RemotePosixPath,
        identity: RemoteJobIdentity,
        spec: GameExecutionSpec,
    ) -> RemoteJobStatus:
        """Persist immutable job inputs before materializing per-job artifacts."""

        stdout = await self._run_checked(
            self.build_prepare_command(job_root=job_root, identity=identity, spec=spec),
            action="prepare remote job",
        )
        return self.parse_status_response(stdout)

    async def start(
        self,
        *,
        job_root: RemotePosixPath,
        secret_bundle_file: str | None = None,
    ) -> RemoteJobStatus:
        """Idempotently start a prepared remote job."""

        stdout = await self._run_checked(
            self.build_start_command(job_root=job_root, secret_bundle_file=secret_bundle_file),
            action="start remote job",
        )
        return self.parse_status_response(stdout)

    def build_prepare_command(
        self,
        *,
        job_root: RemotePosixPath,
        identity: RemoteJobIdentity,
        spec: GameExecutionSpec,
    ) -> str:
        """Build the command that durably prepares immutable job inputs."""

        identity_path = job_root / "request.identity.json"
        spec_path = job_root / "request.spec.json"
        return "\n".join(
            [
                "set -eu",
                f"mkdir -p {job_root.shell_quote()}",
                _heredoc_write(
                    identity_path,
                    identity.model_dump_json(),
                    token="__ARENA_JOB_IDENTITY__",
                ),
                _heredoc_write(
                    spec_path,
                    spec.model_dump_json(),
                    token="__ARENA_JOB_SPEC__",
                ),
                self._worker_command(
                    "prepare",
                    "--job-root",
                    str(job_root),
                    "--identity-file",
                    str(identity_path),
                    "--spec-file",
                    str(spec_path),
                ),
            ]
        )

    def build_start_command(
        self,
        *,
        job_root: RemotePosixPath,
        secret_bundle_file: str | None = None,
    ) -> str:
        """Build the command that starts a prepared job."""

        secret_path = job_root / "secrets.json"
        parts = ["set -eu"]
        if secret_bundle_file is not None:
            parts.append(f"install -m 600 {shlex.quote(secret_bundle_file)} {secret_path.shell_quote()}")
        parts.append(
            self._worker_command(
                "start",
                "--job-root",
                str(job_root),
                *("--secret-file", str(secret_path)) if secret_bundle_file is not None else (),
            )
        )
        return "\n".join(parts)

    def build_prepare_start_command(
        self,
        *,
        job_root: RemotePosixPath,
        identity: RemoteJobIdentity,
        spec: GameExecutionSpec,
        secret_bundle_file: str | None = None,
    ) -> str:
        """Build a prepare/start command for optional secret-file transport."""

        return "\n".join(
            [
                self.build_prepare_command(
                    job_root=job_root,
                    identity=identity,
                    spec=spec,
                ),
                self.build_start_command(
                    job_root=job_root,
                    secret_bundle_file=secret_bundle_file,
                ),
            ]
        )

    @staticmethod
    def parse_status_response(stdout: str) -> RemoteJobStatus:
        """Parse the last status emitted by a control command."""

        return _parse_last_json_model(stdout, RemoteJobStatus)

    async def wait_for_terminal(
        self,
        *,
        job_root: RemotePosixPath,
        timeout: float,
    ) -> RemoteJobStatus:
        """Poll durable status until terminal without inferring outcomes."""

        if timeout <= 0:
            raise ValueError("remote job wait timeout must be positive")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            status = await self.read_durable_status(job_root)
            if status.state in {
                RemoteJobState.TERMINAL,
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                return status
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"remote job status did not become terminal before deadline: {status.identity.job_id}"
                )
            await asyncio.sleep(_POLL_INTERVAL_SEC)

    async def read_durable_status(self, job_root: RemotePosixPath) -> RemoteJobStatus:
        """Read the atomically published status without starting worker Python."""

        stdout = await self._run_checked(
            f"cat {(job_root / 'status.json').shell_quote()}",
            action="read remote job status",
        )
        return _parse_last_json_model(stdout, RemoteJobStatus)

    async def query_status(self, job_root: RemotePosixPath) -> RemoteJobStatus:
        """Query and reconcile one durable job status through the supervisor."""

        stdout = await self._run_checked(
            self._worker_command("status", "--job-root", str(job_root)),
            action="query remote job status",
        )
        return _parse_last_json_model(stdout, RemoteJobStatus)

    async def reap(self, remote_root: RemotePosixPath) -> JsonObject:
        """Run serialized orphan recovery and retention cleanup before dispatch."""

        stdout = await self._run_checked(
            self._worker_command(
                "reap",
                "--jobs-root",
                str(remote_root / "jobs"),
            ),
            action="reap remote jobs",
        )
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RemoteJobClientError("remote job reaper returned no JSON")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RemoteJobClientError(f"invalid remote job reaper response: {exc}") from exc
        if not isinstance(payload, dict):
            raise RemoteJobClientError("remote job reaper response must be a JSON object")
        return {str(key): json_serialize(value) for key, value in payload.items()}

    async def collect(
        self,
        *,
        job_root: RemotePosixPath,
        on_event: Callable[[JsonObject], None] | None,
    ) -> tuple[list[JsonObject], RemoteJobResult]:
        """Collect validated events and terminal result idempotently."""

        events_stdout = await self._run_checked(
            f"cat {(job_root / 'events.jsonl').shell_quote()}",
            action="collect remote job events",
        )
        events: list[JsonObject] = []
        for line in events_stdout.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RemoteJobClientError(f"remote job emitted malformed JSON event: {exc}") from exc
            if not isinstance(payload, dict):
                raise RemoteJobClientError("remote job event must be a JSON object")
            event: JsonObject = {str(key): json_serialize(value) for key, value in payload.items()}
            events.append(event)
            if on_event is not None:
                on_event(event)
        result_stdout = await self._run_checked(
            self._worker_command("result", "--job-root", str(job_root)),
            action="collect remote job result",
        )
        result = _parse_last_json_model(result_stdout, RemoteJobResult)
        return events, result

    async def acknowledge(self, job_root: RemotePosixPath) -> RemoteJobStatus:
        """Acknowledge validated collection."""

        stdout = await self._run_checked(
            self._worker_command("ack", "--job-root", str(job_root)),
            action="acknowledge remote job",
        )
        return _parse_last_json_model(stdout, RemoteJobStatus)

    async def cleanup_secret(self, job_root: RemotePosixPath) -> None:
        """Remove the per-job secret copy without deleting diagnostics."""

        await self._run_checked(
            f"rm -f -- {(job_root / 'secrets.json').shell_quote()}",
            action="clean up remote job secret",
        )

    async def cancel(self, job_root: RemotePosixPath) -> RemoteJobStatus:
        """Request bounded TERM-to-KILL cancellation."""

        stdout = await self._run_checked(
            self._worker_command("cancel", "--job-root", str(job_root)),
            action="cancel remote job",
            timeout=15.0,
        )
        return _parse_last_json_model(stdout, RemoteJobStatus)

    def _worker_command(self, command: str, *arguments: str) -> str:
        python = self._deployment_root / "venv" / "bin" / "python"
        values = [
            str(python),
            "-P",
            "-m",
            "shogiarena._core.platform.engine_provisioning.remote_job_supervisor",
            command,
            *arguments,
        ]
        return "PYTHONDONTWRITEBYTECODE=1 " + " ".join(shlex.quote(value) for value in values)

    async def _run_checked(
        self,
        command: str,
        *,
        action: str,
        timeout: float | None = None,
    ) -> str:
        rc, stdout, stderr = await self._transport.run(command, timeout=timeout)
        if rc != 0:
            raise RemoteJobClientError(f"failed to {action}: {stderr.strip() or stdout.strip() or rc}")
        return stdout


def _heredoc_write(path: RemotePosixPath, payload: str, *, token: str) -> str:
    if f"\n{token}\n" in f"\n{payload}\n":
        raise ValueError("remote job JSON collides with heredoc delimiter")
    return f"cat > {path.shell_quote()} <<'{token}'\n{payload}\n{token}"


def _parse_last_json_model(stdout: str, model: type[ModelT]) -> ModelT:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise RemoteJobClientError("remote job control command returned no JSON")
    try:
        payload = json.loads(lines[-1])
        return model.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RemoteJobClientError(f"invalid remote job control response: {exc}") from exc


__all__ = ["RemoteJobClient", "RemoteJobClientError"]
