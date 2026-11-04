"""Deterministic, network-free concurrency contracts for SSH and Remote preparation."""
# ruff: noqa: SLF001

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.test_remote_job_store import _spec

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.ports.worker_deployment import (
    WorkerBundleBuildResult,
    WorkerBundleManifest,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.ssh_transport import _AsyncSshTransport
from shogiarena._core.platform.engine_provisioning.remote_deployment import PreparedRemoteDeployment
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath


@pytest.fixture
def instance() -> Instance:
    return Instance(
        config=InstanceConfig(
            name="concurrency-test",
            type=InstanceType.SSH,
            engine_dir="",
            host="example.invalid",
            user="arena",
        )
    )


@pytest.fixture
def bundle(tmp_path: Path) -> WorkerBundleBuildResult:
    return WorkerBundleBuildResult(
        bundle_path=tmp_path / "worker.zip",
        bundle_sha256="5" * 64,
        manifest=WorkerBundleManifest(
            protocol_version="shogiarena.remote-worker.v1",
            package_version="1.1.0",
            wheel_filename="shogiarena.whl",
            wheel_sha256="1" * 64,
            lock_sha256="2" * 64,
            dependencies_sha256="3" * 64,
            target_os="linux",
            architecture="x86_64",
            python_version="3.12.10",
            uv_version="uv 0.11.29",
            source_policy="snapshot",
            deployment_id="4" * 64,
        ),
    )


class _Sftp:
    def __init__(self) -> None:
        self.exit_calls = 0
        self.directories: list[str] = []

    def exit(self) -> None:
        self.exit_calls += 1

    async def mkdir(self, path: str) -> None:
        self.directories.append(path)


class _Connection:
    def __init__(self, *, block_sftp: bool = False, fail_sftp: bool = False) -> None:
        self.sftp = _Sftp()
        self.sftp_started = asyncio.Event()
        self.sftp_release = asyncio.Event()
        if not block_sftp:
            self.sftp_release.set()
        self.fail_sftp = fail_sftp
        self.closed = False
        self.close_calls = 0
        self.wait_closed_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.close_completed = False
        self.commands: list[str] = []
        self.run_started = asyncio.Event()
        self.run_release = asyncio.Event()
        self.run_release.set()

    async def start_sftp_client(self) -> _Sftp:
        self.sftp_started.set()
        await self.sftp_release.wait()
        if self.fail_sftp:
            raise OSError("injected SFTP initialization failure")
        return self.sftp

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_started.set()
        await self.close_release.wait()
        self.close_completed = True

    async def run(self, command: str, **_kwargs: object) -> SimpleNamespace:
        assert not self.closed
        self.commands.append(command)
        self.run_started.set()
        await self.run_release.wait()
        assert not self.closed
        return SimpleNamespace(exit_status=0, stdout="ok", stderr="")


def _fake_connect(monkeypatch: pytest.MonkeyPatch, *connections: _Connection) -> list[_Connection]:
    pending = iter(connections)
    calls: list[_Connection] = []

    async def connect(*_args: object, **_kwargs: object) -> _Connection:
        connection = next(pending)
        calls.append(connection)
        return connection

    monkeypatch.setattr("shogiarena._core.contexts.instances.application.ssh_transport.asyncssh.connect", connect)
    return calls


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=2)


@pytest.mark.asyncio
async def test_parallel_connect_creates_one_fully_initialized_connection(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
) -> None:
    connection = _Connection(block_sftp=True)
    calls = _fake_connect(monkeypatch, connection)
    transport = _AsyncSshTransport(instance)
    first = asyncio.create_task(transport.connect())
    await _wait(connection.sftp_started)
    second = asyncio.create_task(transport.connect())
    await asyncio.sleep(0)
    assert not second.done()
    assert transport._conn is None and transport._sftp is None
    assert transport.generation == 0
    connection.sftp_release.set()
    await asyncio.gather(first, second)
    assert calls == [connection]
    assert transport._conn is connection and transport._sftp is connection.sftp
    assert transport.generation == 1
    await transport.close()


@pytest.mark.asyncio
async def test_sftp_user_waits_for_initialization_instead_of_observing_partial_state(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
) -> None:
    connection = _Connection(block_sftp=True)
    _fake_connect(monkeypatch, connection)
    transport = _AsyncSshTransport(instance)
    first = asyncio.create_task(transport.connect())
    await _wait(connection.sftp_started)
    mkdir = asyncio.create_task(transport.mkdir("jobs"))
    await asyncio.sleep(0)
    assert not mkdir.done()
    assert not connection.sftp.directories
    connection.sftp_release.set()
    await asyncio.gather(first, mkdir)
    assert connection.sftp.directories == ["jobs"]
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["failure", "cancellation"])
async def test_failed_initialization_closes_unpublished_connection_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
    cancel: bool,
) -> None:
    failed = _Connection(block_sftp=True, fail_sftp=not cancel)
    replacement = _Connection()
    calls = _fake_connect(monkeypatch, failed, replacement)
    transport = _AsyncSshTransport(instance)
    initial = asyncio.create_task(transport.connect())
    await _wait(failed.sftp_started)
    if cancel:
        initial.cancel()
    else:
        failed.sftp_release.set()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await initial
    assert failed.close_calls == 1 and failed.close_completed
    assert transport._conn is None and transport._sftp is None
    assert transport.generation == 0
    await transport.connect()
    assert calls == [failed, replacement]
    assert transport.generation == 1
    await transport.close()


@pytest.mark.asyncio
async def test_parallel_reconnects_replace_only_the_failed_generation(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
) -> None:
    original, replacement = _Connection(), _Connection(block_sftp=True)
    calls = _fake_connect(monkeypatch, original, replacement)
    transport = _AsyncSshTransport(instance)
    await transport.connect()
    failed_generation = transport.generation
    original.closed = True
    first = asyncio.create_task(transport.reconnect(failed_generation=failed_generation))
    await _wait(replacement.sftp_started)
    second = asyncio.create_task(transport.reconnect(failed_generation=failed_generation))
    await asyncio.sleep(0)
    assert not second.done()
    replacement.sftp_release.set()
    await asyncio.gather(first, second)
    assert calls == [original, replacement]
    assert original.close_calls == 1 and replacement.close_calls == 0
    assert transport.generation == failed_generation + 1
    await transport.close()


@pytest.mark.asyncio
async def test_channel_error_recovery_preserves_healthy_sibling_connection(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
    bundle: WorkerBundleBuildResult,
) -> None:
    connection = _Connection()
    calls = _fake_connect(monkeypatch, connection)
    transport = _AsyncSshTransport(instance)
    await transport.connect()
    executor = RemoteExecutor(instance, bundle)
    executor._transport = transport
    connection.run_release.clear()

    attempts = 0

    async def flaky_channel() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("channel failed while shared SSH remains healthy")
        return "recovered"

    sibling_task = asyncio.create_task(transport.run("sibling-job"))
    await _wait(connection.run_started)
    assert await executor._retry_after_reconnect(flaky_channel) == "recovered"
    assert calls == [connection] and connection.close_calls == 0
    connection.run_release.set()
    assert await sibling_task == (0, "ok", "")
    assert connection.commands == ["sibling-job"]
    await transport.close()


@pytest.mark.asyncio
async def test_close_detaches_connection_and_blocks_new_users_until_close_completes(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
) -> None:
    original, replacement = _Connection(), _Connection()
    calls = _fake_connect(monkeypatch, original, replacement)
    transport = _AsyncSshTransport(instance)
    await transport.connect()
    original.close_release.clear()
    closing = asyncio.create_task(transport.close())
    await _wait(original.wait_closed_started)
    assert transport._conn is None and transport._sftp is None
    command = asyncio.create_task(transport.run("new-job"))
    await asyncio.sleep(0)
    assert not command.done() and calls == [original]
    original.close_release.set()
    await closing
    assert await command == (0, "ok", "")
    assert calls == [original, replacement]
    assert not original.commands and replacement.commands == ["new-job"]
    await transport.close()


class _Endpoint:
    def __init__(self) -> None:
        self.closed = False

    async def connect(self) -> None:
        return

    async def close(self) -> None:
        self.closed = True

    async def endpoint_identity(self) -> str:
        return "ssh://concurrency-test@example.invalid:22#fake-key"


class _Resolver:
    async def expand(self, path: RemotePosixPath) -> RemotePosixPath:
        return path


class _PrepareManager:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_completed = False
        self.prepared = PreparedRemoteDeployment(
            deployment_id="4" * 64,
            root=RemotePosixPath("/srv/arena/deployments/worker"),
            manifest_sha256="a" * 64,
            bundle_sha256="5" * 64,
        )

    async def prepare(self, **_kwargs: object) -> PreparedRemoteDeployment:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
            return self.prepared
        finally:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.cleanup_completed = True


def _executor(instance: Instance, bundle: WorkerBundleBuildResult, manager: _PrepareManager) -> RemoteExecutor:
    executor = RemoteExecutor(instance, bundle)
    executor._transport = _Endpoint()  # type: ignore[assignment]
    executor._path_resolver = _Resolver()  # type: ignore[assignment]
    executor._deployment_manager = manager  # type: ignore[assignment]
    return executor


@pytest.mark.asyncio
async def test_deployment_preparation_is_owned_by_each_executor(
    instance: Instance, bundle: WorkerBundleBuildResult
) -> None:
    first_manager, second_manager = _PrepareManager(), _PrepareManager()
    first_manager.cleanup_release.set()
    second_manager.cleanup_release.set()
    first_executor = _executor(instance, bundle, first_manager)
    second_executor = _executor(instance, bundle, second_manager)
    second_transport = second_executor.transport
    root = "/srv/arena/shared-endpoint"
    first = asyncio.create_task(first_executor.ensure_deployment(root))
    second = asyncio.create_task(second_executor.ensure_deployment(root))
    try:
        await _wait(first_manager.started)
        await _wait(second_manager.started)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await first_executor.close()
        assert not second.done()
        assert not second_transport.closed  # type: ignore[attr-defined]
        second_manager.release.set()
        assert await second is second_manager.prepared
        assert first_manager.calls == second_manager.calls == 1
    finally:
        first_manager.release.set()
        second_manager.release.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await first_executor.close()
        await second_executor.close()


@pytest.mark.asyncio
async def test_failed_shared_preparation_can_be_retried(instance: Instance, bundle: WorkerBundleBuildResult) -> None:
    failure = OSError("prepare failed")

    class Manager(_PrepareManager):
        async def prepare(self, **_kwargs: object) -> PreparedRemoteDeployment:
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
                raise failure
            return self.prepared

    manager = Manager()
    executor = _executor(instance, bundle, manager)
    root = "/srv/arena/retry"
    first = asyncio.create_task(executor.ensure_deployment(root))
    await _wait(manager.started)
    second = asyncio.create_task(executor.ensure_deployment(root))
    await asyncio.sleep(0)
    manager.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(result is failure for result in results)
    assert await executor.ensure_deployment(root) is manager.prepared
    assert manager.calls == 2
    assert not executor._prepare_tasks and not executor._prepare_waiters


@pytest.mark.asyncio
async def test_cancelled_first_deployment_waiter_preserves_prepare_for_healthy_waiter(
    instance: Instance,
    bundle: WorkerBundleBuildResult,
) -> None:
    manager = _PrepareManager()
    manager.cleanup_release.set()
    executor = _executor(instance, bundle, manager)
    first = asyncio.create_task(executor.ensure_deployment("/srv/arena/leader-cancel"))
    await _wait(manager.started)
    second = asyncio.create_task(executor.ensure_deployment("/srv/arena/leader-cancel"))
    await asyncio.sleep(0)
    key = next(iter(executor._prepare_tasks))
    assert executor._prepare_waiters[key] == 2
    prepare = executor._prepare_tasks[key]
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not prepare.done() and not second.done()
    assert executor._prepare_waiters[key] == 1
    manager.release.set()
    assert await second is manager.prepared
    assert manager.calls == 1
    assert not executor._prepare_tasks and not executor._prepare_waiters


@pytest.mark.asyncio
async def test_repeated_cancellation_of_last_deployment_waiter_drains_prepare_cleanup(
    instance: Instance,
    bundle: WorkerBundleBuildResult,
) -> None:
    manager = _PrepareManager()
    executor = _executor(instance, bundle, manager)
    waiter = asyncio.create_task(executor.ensure_deployment("/srv/arena/last-cancel"))
    await _wait(manager.started)
    key = next(iter(executor._prepare_tasks))
    prepare = executor._prepare_tasks[key]
    waiter.cancel()
    await _wait(manager.cleanup_started)
    waiter.cancel()
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)
    assert not waiter.done() and not prepare.done()
    assert not manager.cleanup_completed
    manager.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert prepare.done() and prepare.cancelled() and manager.cleanup_completed
    assert not executor._prepare_tasks and not executor._prepare_waiters


@pytest.mark.asyncio
async def test_async_assignment_is_awaited_before_remote_job_lease_and_start(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
    bundle: WorkerBundleBuildResult,
) -> None:
    manager = _PrepareManager()
    executor = _executor(instance, bundle, manager)
    executor._deployment = manager.prepared
    assignment_started, assignment_release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def reap(self, _root: RemotePosixPath) -> None:
            calls.append("reap")

        async def resolve_identity(self, **kwargs: object) -> object:
            calls.append("resolve")
            return kwargs["candidate"]

    async def acquire_lease(*_args: object) -> None:
        calls.append("lease")
        raise RuntimeError("stop probe before job starts")

    async def on_assignment(payload: object) -> None:
        assert isinstance(payload, dict) and payload["deployment_id"] == manager.prepared.deployment_id
        calls.append("assignment-start")
        assignment_started.set()
        await assignment_release.wait()
        calls.append("assignment-complete")

    monkeypatch.setattr(
        "shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor.RemoteJobClient",
        Client,
    )
    monkeypatch.setattr(manager, "acquire_lease", acquire_lease, raising=False)
    running = asyncio.create_task(
        executor.run_remote_pair(
            remote_root="/srv/arena",
            spec=_spec().model_dump(mode="json"),
            logical_job_key="test-assignment",
            on_assignment=on_assignment,
        )
    )
    await _wait(assignment_started)
    assert calls == ["reap", "resolve", "assignment-start"] and not running.done()
    assignment_release.set()
    with pytest.raises(RuntimeError, match="stop probe before job starts"):
        await running
    assert calls == ["reap", "resolve", "assignment-start", "assignment-complete", "lease"]


@pytest.mark.asyncio
@pytest.mark.parametrize("start_recovery", [False, True], ids=["operation", "job-start"])
async def test_recovery_uses_the_generation_captured_before_the_failed_operation(
    instance: Instance,
    bundle: WorkerBundleBuildResult,
    start_recovery: bool,
) -> None:
    class Transport(_Endpoint):
        generation = 11

        def __init__(self) -> None:
            self.reconnect_generations: list[int] = []

        async def reconnect(self, *, failed_generation: int) -> None:
            self.reconnect_generations.append(failed_generation)

    transport = Transport()
    executor = RemoteExecutor(instance, bundle)
    executor._transport = transport  # type: ignore[assignment]
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Another job replaces the connection while this operation's error is pending.
            transport.generation = 12
            raise OSError("response lost from old generation")
        return "retried"

    if start_recovery:
        from shogiarena._core.contexts.game_session.ports.remote_job import RemoteJobState

        recovered_status = SimpleNamespace(state=RemoteJobState.RUNNING)

        class Client:
            async def query_status(self, _root: RemotePosixPath) -> object:
                return recovered_status

        result = await executor._start_with_recovery(
            Client(),
            job_root=RemotePosixPath("/srv/arena/jobs/test"),
            start=operation,
        )
        assert result is recovered_status and attempts == 1
    else:
        assert await executor._retry_after_reconnect(operation) == "retried"
        assert attempts == 2
    assert transport.reconnect_generations == [11]


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline_exhausted", [False, True])
async def test_terminal_wait_recovery_keeps_generation_and_original_deadline(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
    bundle: WorkerBundleBuildResult,
    deadline_exhausted: bool,
) -> None:
    from shogiarena._core.contexts.game_session.adapters.orchestration import remote_executor

    clock = SimpleNamespace(now=100.0)
    loop = SimpleNamespace(time=lambda: clock.now)
    monkeypatch.setattr(remote_executor, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))

    class Transport(_Endpoint):
        generation = 11

        def __init__(self) -> None:
            super().__init__()
            self.reconnect_generations: list[int] = []

        async def reconnect(self, *, failed_generation: int) -> None:
            self.reconnect_generations.append(failed_generation)
            clock.now += 2.0

    transport = Transport()
    executor = RemoteExecutor(instance, bundle)
    executor._transport = transport  # type: ignore[assignment]
    timeouts: list[float] = []
    terminal = SimpleNamespace(terminal_kind="completed")

    class Client:
        async def wait_for_terminal(self, **kwargs: object) -> object:
            timeouts.append(kwargs["timeout"])  # type: ignore[arg-type]
            if len(timeouts) == 1:
                clock.now += 9.0 if deadline_exhausted else 4.0
                # A sibling already recovered the shared connection before this error arrived.
                transport.generation = 12
                raise OSError("old polling channel failed")
            return terminal

    async def wait() -> object:
        return await executor._wait_for_terminal_with_recovery(
            Client(),  # type: ignore[arg-type]
            job_root=RemotePosixPath("/srv/arena/jobs/poll"),
            timeout=10.0,
        )

    if deadline_exhausted:
        with pytest.raises(TimeoutError, match="before deadline"):
            await wait()
        assert timeouts == [10.0]
    else:
        assert await wait() is terminal
        assert timeouts == [10.0, 4.0]
    assert transport.reconnect_generations == [11]
    assert not transport.closed


@pytest.mark.asyncio
async def test_terminal_wait_cancellation_does_not_reconnect(
    instance: Instance, bundle: WorkerBundleBuildResult
) -> None:
    class Transport(_Endpoint):
        generation = 11

        async def reconnect(self, *, failed_generation: int) -> None:
            pytest.fail("cancellation must not reconnect")

    failure = asyncio.CancelledError("cancel terminal wait")

    class Client:
        async def wait_for_terminal(self, **_kwargs: object) -> object:
            raise failure

    executor = RemoteExecutor(instance, bundle)
    executor._transport = Transport()  # type: ignore[assignment]
    with pytest.raises(asyncio.CancelledError) as caught:
        await executor._wait_for_terminal_with_recovery(
            Client(),  # type: ignore[arg-type]
            job_root=RemotePosixPath("/srv/arena/jobs/poll"),
            timeout=10.0,
        )
    assert caught.value is failure


@pytest.mark.asyncio
@pytest.mark.parametrize("retiring_finishes_first", [False, True], ids=["newcomer-first", "retirement-first"])
async def test_new_deployment_waiter_does_not_join_a_retiring_cancelled_prepare(
    instance: Instance,
    bundle: WorkerBundleBuildResult,
    retiring_finishes_first: bool,
) -> None:
    class Manager(_PrepareManager):
        def __init__(self) -> None:
            super().__init__()
            self.new_started = asyncio.Event()
            self.new_release = asyncio.Event()

        async def prepare(self, **_kwargs: object) -> PreparedRemoteDeployment:
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                try:
                    await self.release.wait()
                finally:
                    self.cleanup_started.set()
                    await self.cleanup_release.wait()
                    self.cleanup_completed = True
            else:
                self.new_started.set()
                await self.new_release.wait()
            return self.prepared

    manager = Manager()
    executor = _executor(instance, bundle, manager)
    root = "/srv/arena/new-waiter-during-retirement"
    retiring = asyncio.create_task(executor.ensure_deployment(root))
    newcomer: asyncio.Task[PreparedRemoteDeployment] | None = None
    try:
        await _wait(manager.started)
        key = next(iter(executor._prepare_tasks))
        retiring_prepare = executor._prepare_tasks[key]
        retiring.cancel()
        await _wait(manager.cleanup_started)
        assert not retiring.done() and not retiring_prepare.done()
        assert key not in executor._prepare_tasks
        assert key not in executor._prepare_waiters

        newcomer = asyncio.create_task(executor.ensure_deployment(root))
        await _wait(manager.new_started)
        new_prepare = executor._prepare_tasks[key]
        assert new_prepare is not retiring_prepare
        assert executor._prepare_waiters[key] == 1
        assert manager.calls == 2

        if retiring_finishes_first:
            manager.cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await retiring
            # Retiring cleanup must not remove the replacement task or its waiter count.
            assert executor._prepare_tasks[key] is new_prepare
            assert executor._prepare_waiters[key] == 1
            assert not newcomer.done()
            manager.new_release.set()
            assert await newcomer is manager.prepared
        else:
            manager.new_release.set()
            assert await newcomer is manager.prepared
            assert not retiring.done() and not retiring_prepare.done()
            assert not manager.cleanup_completed
            manager.cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await retiring
        assert manager.cleanup_completed and retiring_prepare.cancelled()
        assert not executor._prepare_tasks and not executor._prepare_waiters
    finally:
        manager.release.set()
        manager.new_release.set()
        manager.cleanup_release.set()
        tasks = [retiring] if newcomer is None else [retiring, newcomer]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
