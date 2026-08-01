from __future__ import annotations

import asyncio
import json
import logging

import asyncssh
import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
)
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobIdentity,
    RemoteJobResult,
    RemoteJobState,
    RemoteJobStatus,
)
from shogiarena._core.platform.engine_provisioning.remote_deployment import (
    PreparedRemoteDeployment,
    RemoteDeploymentError,
)
from shogiarena._core.platform.engine_provisioning.remote_job_client import (
    RemoteJobClient,
    RemoteJobClientError,
)
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath
from tests.unit.test_remote_job_store import _spec


class _JobTransport:
    def __init__(
        self,
        *,
        malformed_status: bool = False,
        terminal_kind: str = "completed",
    ) -> None:
        self.malformed_status = malformed_status
        self.terminal_kind = terminal_kind
        self.commands: list[str] = []
        self.status_queries = 0
        spec = _spec()
        self.identity = RemoteJobIdentity(
            job_id="job-" + "1" * 32,
            logical_game_id=spec.identity.game_id,
            attempt_id="attempt-" + "2" * 32,
            execution_digest=spec.execution_digest,
        )
        self.result = GameExecutionResult(
            execution_digest=spec.execution_digest,
            game_id=spec.identity.game_id,
            classification="DRAW_BY_REPETITION",
        )

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        del env, timeout
        self.commands.append(command)
        if " reap " in command:
            return 0, '{"failed":0,"timed_out":0,"collectable":0,"deleted":0,"retained":0}\n', ""
        if "/assignments/" in command and "flock -x" in command:
            return 0, self.identity.model_dump_json() + "\n", ""
        if " prepare " in command:
            return 0, self._status(RemoteJobState.PREPARED), ""
        if " start " in command:
            return 0, self._status(RemoteJobState.STARTING), ""
        if command.startswith("cat ") and command.endswith("/status.json"):
            self.status_queries += 1
            if self.malformed_status:
                return 0, "{bad", ""
            state = RemoteJobState.RUNNING if self.status_queries == 1 else RemoteJobState.TERMINAL
            return 0, self._status(state), ""
        if " status " in command:
            self.status_queries += 1
            if self.malformed_status:
                return 0, "{bad", ""
            state = RemoteJobState.RUNNING if self.status_queries == 1 else RemoteJobState.TERMINAL
            return 0, self._status(state), ""
        if command.startswith("cat "):
            progress = {"type": "move_progress", "game_result": "draw"}
            return 0, json.dumps(progress) + "\n" + self.result.model_dump_json() + "\n", ""
        if " result " in command:
            envelope = RemoteJobResult(
                job_id=self.identity.job_id,
                execution_digest=self.identity.execution_digest,
                logical_game_id=self.identity.logical_game_id,
                payload=self.result,
            )
            return 0, envelope.model_dump_json() + "\n", ""
        if " ack " in command:
            return 0, self._status(RemoteJobState.ACKNOWLEDGED), ""
        if " cancel " in command:
            terminal_kind = self.terminal_kind
            self.terminal_kind = "cancelled"
            try:
                return 0, self._status(RemoteJobState.TERMINAL), ""
            finally:
                self.terminal_kind = terminal_kind
        if command.startswith("rm -f"):
            return 0, "", ""
        return 2, "", "unexpected command"

    def _status(self, state: RemoteJobState) -> str:
        terminal = state in {
            RemoteJobState.TERMINAL,
            RemoteJobState.ACKNOWLEDGED,
            RemoteJobState.COLLECTABLE,
        }
        status = RemoteJobStatus(
            identity=self.identity,
            state=state,
            revision=1,
            updated_at="2026-07-27T00:00:00+00:00",
            terminal_kind=self.terminal_kind if terminal else None,
            acknowledged_at="2026-07-27T00:00:01+00:00"
            if state in {RemoteJobState.ACKNOWLEDGED, RemoteJobState.COLLECTABLE}
            else None,
        )
        return status.model_dump_json() + "\n"


@pytest.mark.asyncio
async def test_client_uses_short_commands_to_start_poll_collect_and_acknowledge() -> None:
    transport = _JobTransport()
    client = RemoteJobClient(
        transport,
        deployment_root=RemotePosixPath("/srv/arena/deployments/deployment"),
    )
    spec = _spec()
    job_root = RemotePosixPath(f"/srv/arena/jobs/{transport.identity.job_id}")
    observed: list[dict[str, object]] = []

    resolved = await client.resolve_identity(
        remote_root=RemotePosixPath("/srv/arena"),
        logical_job_key=spec.identity.job_id,
        candidate=transport.identity,
    )
    started = await client.prepare_and_start(
        job_root=job_root,
        identity=transport.identity,
        spec=spec,
    )
    terminal = await client.wait_for_terminal(job_root=job_root, timeout=1.0)
    events, result = await client.collect(job_root=job_root, on_event=observed.append)
    recollected_events, recollected_result = await client.collect(job_root=job_root, on_event=None)
    acknowledged = await client.acknowledge(job_root)

    assert resolved == transport.identity
    assert started.state == RemoteJobState.STARTING
    assert terminal.state == RemoteJobState.TERMINAL
    assert result.payload == transport.result
    assert events == observed
    assert recollected_events == events
    assert recollected_result == result
    assert acknowledged.state == RemoteJobState.ACKNOWLEDGED
    assert transport.status_queries == 2
    identity_command = next(
        command for command in transport.commands if "/assignments/" in command and "flock -x" in command
    )
    assert "\n__ARENA_JOB_ASSIGNMENT__\nflock -x" in identity_command
    assert "__ARENA_JOB_ASSIGNMENT__;" not in identity_command
    assert any("remote_job_supervisor prepare" in command for command in transport.commands)
    prepare_command = next(command for command in transport.commands if "remote_job_supervisor prepare" in command)
    assert "\n__ARENA_JOB_IDENTITY__\ncat >" in prepare_command
    assert "\n__ARENA_JOB_SPEC__\nPYTHONDONTWRITEBYTECODE=1 " in prepare_command
    assert " -P -m shogiarena._core.platform.engine_provisioning.remote_job_supervisor prepare" in prepare_command
    assert any(command.startswith("cat ") and command.endswith("/status.json") for command in transport.commands)
    assert not any("remote_job_supervisor status" in command for command in transport.commands)
    assert any("remote_job_supervisor result" in command for command in transport.commands)
    assert any("remote_job_supervisor ack" in command for command in transport.commands)


@pytest.mark.asyncio
async def test_client_rejects_malformed_status_without_starting_another_job() -> None:
    transport = _JobTransport(malformed_status=True)
    client = RemoteJobClient(
        transport,
        deployment_root=RemotePosixPath("/srv/arena/deployments/deployment"),
    )
    job_root = RemotePosixPath(f"/srv/arena/jobs/{transport.identity.job_id}")

    with pytest.raises(RemoteJobClientError, match="invalid remote job control response"):
        await client.query_status(job_root)

    assert len(transport.commands) == 1
    assert " status " in transport.commands[0]


@pytest.mark.asyncio
async def test_client_rejects_malformed_durable_status_without_starting_worker_python() -> None:
    transport = _JobTransport(malformed_status=True)
    client = RemoteJobClient(
        transport,
        deployment_root=RemotePosixPath("/srv/arena/deployments/deployment"),
    )
    job_root = RemotePosixPath(f"/srv/arena/jobs/{transport.identity.job_id}")

    with pytest.raises(RemoteJobClientError, match="invalid remote job control response"):
        await client.read_durable_status(job_root)

    assert transport.commands == [f"cat {job_root}/status.json"]


@pytest.mark.asyncio
async def test_remote_executor_production_path_uses_durable_job_protocol() -> None:
    class Transport(_JobTransport):
        def __init__(
            self,
            *,
            terminal_kind: str = "completed",
            disconnect_status_count: int = 1,
            status_never_terminal: bool = False,
            cleanup_error: bool = False,
            acknowledge_error: bool = False,
            cancel_error: bool = False,
            disconnect_prepare_count: int = 0,
            disconnect_start_count: int = 0,
            disconnect_result_count: int = 0,
            reap_error: bool = False,
        ) -> None:
            super().__init__(terminal_kind=terminal_kind)
            self.close_count = 0
            self.connect_count = 0
            self.disconnect_status_count = disconnect_status_count
            self.status_never_terminal = status_never_terminal
            self.cleanup_error = cleanup_error
            self.acknowledge_error = acknowledge_error
            self.cancel_error = cancel_error
            self.disconnect_prepare_count = disconnect_prepare_count
            self.disconnect_start_count = disconnect_start_count
            self.disconnect_result_count = disconnect_result_count
            self.reap_error = reap_error

        async def connect(self) -> None:
            self.connect_count += 1

        async def close(self) -> None:
            self.close_count += 1

        async def endpoint_identity(self) -> str:
            return "ssh://worker@example:22#host-key"

        async def run(
            self,
            command: str,
            *,
            env: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> tuple[int, str, str]:
            if " reap " in command and self.reap_error:
                self.commands.append(command)
                return 70, "", "simulated worker deployment failure"
            if "ln -sfn" in command:
                self.commands.append(command)
                return 0, "", ""
            if " prepare " in command and self.disconnect_prepare_count > 0:
                await super().run(command, env=env, timeout=timeout)
                self.disconnect_prepare_count -= 1
                raise OSError("simulated lost prepare response")
            if " start " in command and self.disconnect_start_count > 0:
                await super().run(command, env=env, timeout=timeout)
                self.disconnect_start_count -= 1
                raise OSError("simulated lost start response")
            if " result " in command and self.disconnect_result_count > 0:
                await super().run(command, env=env, timeout=timeout)
                self.disconnect_result_count -= 1
                raise OSError("simulated lost result response")
            is_status_read = " status " in command or (command.startswith("cat ") and command.endswith("/status.json"))
            if is_status_read and self.disconnect_status_count > 0:
                self.commands.append(command)
                self.disconnect_status_count -= 1
                raise OSError("simulated SSH disconnect")
            if is_status_read and self.status_never_terminal:
                self.commands.append(command)
                self.status_queries += 1
                return 0, self._status(RemoteJobState.RUNNING), ""
            if command.startswith("rm -f") and self.cleanup_error:
                raise asyncssh.Error(1, "simulated cleanup disconnect")
            if " ack " in command and self.acknowledge_error:
                raise asyncssh.Error(1, "simulated acknowledge disconnect")
            if " cancel " in command and self.cancel_error:
                raise asyncssh.Error(1, "simulated cancel disconnect")
            return await super().run(command, env=env, timeout=timeout)

    class PathResolver:
        @staticmethod
        async def expand(path: RemotePosixPath) -> RemotePosixPath:
            return path

    class DeploymentManager:
        def __init__(self, *, release_error: bool = False, integrity_error: bool = False) -> None:
            self.acquired: list[str] = []
            self.released: list[str] = []
            self.verified: list[str] = []
            self.release_error = release_error
            self.integrity_error = integrity_error

        async def verify(
            self,
            deployment: PreparedRemoteDeployment,
            *,
            python_version: str,
        ) -> None:
            self.verified.append(python_version)
            if self.integrity_error:
                raise RemoteDeploymentError(
                    f"existing deployment failed integrity verification: {deployment.deployment_id}"
                )

        async def acquire_lease(self, _deployment: object, lease_id: str) -> None:
            self.acquired.append(lease_id)

        async def release_lease(self, _deployment: object, lease_id: str) -> None:
            if self.release_error:
                raise asyncssh.Error(1, "simulated lease release disconnect")
            self.released.append(lease_id)

    deployment = PreparedRemoteDeployment(
        deployment_id="d" * 64,
        root=RemotePosixPath("/srv/arena/deployments/deployment"),
        manifest_sha256="a" * 64,
        bundle_sha256="b" * 64,
    )

    def build_executor(
        transport: Transport,
        manager: DeploymentManager,
    ) -> RemoteExecutor:
        executor = object.__new__(RemoteExecutor)
        executor.instance = type("InstanceStub", (), {"name": "worker"})()
        executor.worker_bundle = type(
            "WorkerBundleStub",
            (),
            {"manifest": type("ManifestStub", (), {"python_version": "3.12.10"})()},
        )()
        executor._transport = transport  # type: ignore[assignment]  # noqa: SLF001
        executor._path_resolver = PathResolver()  # type: ignore[assignment]  # noqa: SLF001
        executor._deployment_manager = manager  # type: ignore[assignment]  # noqa: SLF001
        executor._artifact_store = None  # noqa: SLF001
        executor._deployment = deployment  # noqa: SLF001
        executor._artifact_paths = {  # noqa: SLF001
            "black-artifact": RemotePosixPath("/srv/cas/black"),
            "white-artifact": RemotePosixPath("/srv/cas/white"),
        }
        executor._logger = logging.getLogger(__name__)  # noqa: SLF001
        return executor

    spec = _spec()
    transport = Transport()
    manager = DeploymentManager()
    executor = build_executor(transport, manager)
    observed: list[dict[str, object]] = []

    outcome = await executor.run_remote_pair(
        remote_root="/srv/arena",
        spec=spec.model_dump(mode="json"),
        logical_job_key="run:test",
        on_event=observed.append,
    )
    await executor.close()

    assert outcome.events == observed
    assert outcome.endpoint_identity == "ssh://worker@example:22#host-key"
    assert manager.acquired == [transport.identity.job_id]
    assert manager.released == [transport.identity.job_id]
    assert any("/assignments/" in command for command in transport.commands)
    assert any("remote_job_supervisor prepare" in command for command in transport.commands)
    assert all("__REMOTE_EXIT_RC" not in command for command in transport.commands)
    assert transport.connect_count == 2
    assert transport.close_count == 2
    assert executor._transport is None  # noqa: SLF001

    for boundary, disconnect_kwargs in (
        ("prepare", {"disconnect_prepare_count": 1}),
        ("start", {"disconnect_start_count": 1}),
        ("result", {"disconnect_result_count": 1}),
    ):
        recovery_transport = Transport(
            disconnect_status_count=0,
            **disconnect_kwargs,
        )
        recovery_manager = DeploymentManager()
        recovery_executor = build_executor(recovery_transport, recovery_manager)
        recovery_events: list[dict[str, object]] = []

        recovery_outcome = await recovery_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key=f"run:{boundary}-response-loss",
            on_event=recovery_events.append,
        )
        await recovery_executor.close()

        assert recovery_outcome.events == recovery_events
        assert recovery_outcome.worker_result == recovery_transport.result
        assert recovery_manager.released == [recovery_transport.identity.job_id]
        assert recovery_transport.connect_count == 2
        assert recovery_transport.close_count == 2

    failed_transport = Transport(terminal_kind="failed", disconnect_status_count=0)
    failed_manager = DeploymentManager()
    failed_executor = build_executor(failed_transport, failed_manager)
    with pytest.raises(RuntimeError, match="terminated as failed"):
        await failed_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
        )
    await failed_executor.close()
    assert any("remote_job_supervisor ack" in command for command in failed_transport.commands)
    assert failed_manager.released == [failed_transport.identity.job_id]

    secondary_error_transport = Transport(
        terminal_kind="failed",
        disconnect_status_count=0,
        cleanup_error=True,
        acknowledge_error=True,
    )
    secondary_error_manager = DeploymentManager()
    secondary_error_executor = build_executor(secondary_error_transport, secondary_error_manager)
    with pytest.raises(RuntimeError, match="terminated as failed"):
        await secondary_error_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
        )
    await secondary_error_executor.close()
    assert secondary_error_manager.released == []

    completed_ack_error_transport = Transport(
        disconnect_status_count=0,
        acknowledge_error=True,
    )
    completed_ack_error_manager = DeploymentManager()
    completed_ack_error_executor = build_executor(
        completed_ack_error_transport,
        completed_ack_error_manager,
    )
    with pytest.raises(RemoteJobClientError, match="result was collected but acknowledgement failed"):
        await completed_ack_error_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
        )
    await completed_ack_error_executor.close()
    assert completed_ack_error_manager.released == []
    assert completed_ack_error_manager.verified == ["3.12.10"]

    corrupt_transport = Transport(disconnect_status_count=0, reap_error=True)
    corrupt_manager = DeploymentManager(integrity_error=True)
    corrupt_executor = build_executor(corrupt_transport, corrupt_manager)
    with pytest.raises(RemoteDeploymentError, match="integrity verification"):
        await corrupt_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:corrupt-deployment",
            on_event=None,
        )
    assert corrupt_manager.verified == ["3.12.10"]
    assert corrupt_manager.acquired == []
    assert corrupt_executor._deployment is None  # noqa: SLF001
    assert corrupt_executor._deployment_cache_key is None  # noqa: SLF001

    release_error_transport = Transport(disconnect_status_count=0)
    release_error_manager = DeploymentManager(release_error=True)
    release_error_executor = build_executor(release_error_transport, release_error_manager)
    release_error_outcome = await release_error_executor.run_remote_pair(
        remote_root="/srv/arena",
        spec=spec.model_dump(mode="json"),
        logical_job_key="run:test",
        on_event=None,
    )
    await release_error_executor.close()
    assert release_error_outcome.worker_result == release_error_transport.result
    assert release_error_manager.released == []

    unknown_transport = Transport(disconnect_status_count=2)
    unknown_manager = DeploymentManager()
    unknown_executor = build_executor(unknown_transport, unknown_manager)
    with pytest.raises(OSError, match="simulated SSH disconnect"):
        await unknown_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
        )
    await unknown_executor.close()
    assert unknown_manager.acquired == [unknown_transport.identity.job_id]
    assert unknown_manager.released == []

    timeout_transport = Transport(
        disconnect_status_count=0,
        status_never_terminal=True,
    )
    timeout_manager = DeploymentManager()
    timeout_executor = build_executor(timeout_transport, timeout_manager)
    with pytest.raises(TimeoutError):
        await timeout_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
            timeout=0.001,
        )
    await timeout_executor.close()
    assert any("remote_job_supervisor cancel" in command for command in timeout_transport.commands)
    assert any("remote_job_supervisor ack" in command for command in timeout_transport.commands)
    assert timeout_manager.released == [timeout_transport.identity.job_id]

    cancel_error_transport = Transport(
        disconnect_status_count=0,
        status_never_terminal=True,
        cancel_error=True,
    )
    cancel_error_manager = DeploymentManager()
    cancel_error_executor = build_executor(cancel_error_transport, cancel_error_manager)
    with pytest.raises(TimeoutError):
        await cancel_error_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
            timeout=0.001,
        )
    await cancel_error_executor.close()
    assert cancel_error_manager.released == []

    cancelled_transport = Transport(
        disconnect_status_count=0,
        status_never_terminal=True,
    )
    cancelled_manager = DeploymentManager()
    cancelled_executor = build_executor(cancelled_transport, cancelled_manager)
    task = asyncio.create_task(
        cancelled_executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=spec.model_dump(mode="json"),
            logical_job_key="run:test",
            on_event=None,
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await cancelled_executor.close()
    assert any("remote_job_supervisor cancel" in command for command in cancelled_transport.commands)
    assert any("remote_job_supervisor ack" in command for command in cancelled_transport.commands)
    assert cancelled_manager.released == [cancelled_transport.identity.job_id]
