from __future__ import annotations

import asyncio
import gc
import logging
from typing import Any, cast

import pytest

from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge


class _InstanceStub:
    name = "local"


class _WriterStub:
    def __init__(self, *, is_closing: bool) -> None:
        self._is_closing = is_closing

    def is_closing(self) -> bool:
        return self._is_closing

    def write(self, _payload: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class _ProcessStub:
    def __init__(self, *, writer: _WriterStub | None) -> None:
        self.stdin = writer
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self.pid = 1

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class _HangingProcessStub:
    def __init__(self, *, writer: _WriterStub | None) -> None:
        self.stdin = writer
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self.pid = 1
        self.kill_calls = 0
        self.wait_calls = 0
        self.wait_release = asyncio.Event()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self.wait_release.wait()
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1


class _KillReleasingProcessStub(_HangingProcessStub):
    def kill(self) -> None:
        super().kill()
        self.wait_release.set()


@pytest.mark.asyncio
async def test_send_line_stop_logs_debug_when_process_stdin_is_closed(caplog: pytest.LogCaptureFixture) -> None:
    bridge = SpawnerBackedUSIBridge(
        instance=_InstanceStub(),
        engine_path="/tmp/engine",
        name="dummy",
    )
    bridge.process = cast(Any, _ProcessStub(writer=_WriterStub(is_closing=True)))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError, match="not running or stdin closed, cannot send: stop"):
        await bridge.send_line("stop")

    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_stop_process_bounds_wait_after_kill(caplog: pytest.LogCaptureFixture) -> None:
    bridge = SpawnerBackedUSIBridge(
        instance=_InstanceStub(),
        engine_path="/tmp/engine",
        name="dummy",
    )
    bridge.GRACEFUL_STOP_TIMEOUT_SECONDS = 0.01
    bridge.KILL_WAIT_TIMEOUT_SECONDS = 0.01
    process = _HangingProcessStub(writer=_WriterStub(is_closing=False))
    bridge.process = cast(Any, process)
    caplog.set_level(logging.WARNING)

    await asyncio.wait_for(bridge.stop_process(), timeout=0.2)
    await asyncio.sleep(0)

    assert process.kill_calls == 1
    assert process.wait_calls >= 3
    assert bridge.process is None
    assert bridge._is_stopping is False  # noqa: SLF001
    assert bridge._reap_tasks  # noqa: SLF001
    assert any("did not exit after kill" in record.message for record in caplog.records)

    process.wait_release.set()
    await asyncio.wait_for(_wait_until_reaped(bridge), timeout=0.2)


@pytest.mark.asyncio
async def test_stop_process_kills_and_reaps_before_propagating_cancellation() -> None:
    bridge = SpawnerBackedUSIBridge(
        instance=_InstanceStub(),
        engine_path="/tmp/engine",
        name="dummy",
    )
    process = _KillReleasingProcessStub(writer=_WriterStub(is_closing=False))
    bridge.process = cast(Any, process)
    bridge.GRACEFUL_STOP_TIMEOUT_SECONDS = 0.01

    stop_task = asyncio.create_task(bridge.stop_process())
    await _wait_until_waiting(process)
    stop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stop_task, timeout=0.2)

    assert process.kill_calls == 1
    assert process.wait_calls >= 2
    assert bridge.process is None
    assert bridge._is_stopping is False  # noqa: SLF001
    assert not bridge._reap_tasks  # noqa: SLF001


async def _wait_until_reaped(bridge: SpawnerBackedUSIBridge) -> None:
    while bridge._reap_tasks:  # noqa: SLF001
        await asyncio.sleep(0)


async def _wait_until_waiting(process: _HangingProcessStub) -> None:
    while process.wait_calls == 0:
        await asyncio.sleep(0)


class _ClosingProcessStub(_ProcessStub):
    def __init__(self) -> None:
        super().__init__(writer=_WriterStub(is_closing=False))
        self.returncode = 0
        self.wait_calls = 0
        self.kill_calls = 0
        self.close_calls = 0
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()

    async def wait(self) -> int:
        self.wait_calls += 1
        return 0

    def kill(self) -> None:
        self.kill_calls += 1

    async def close_transport(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()


@pytest.mark.asyncio
async def test_exited_process_cleanup_is_shared_and_drained_under_repeated_cancellation() -> None:
    bridge = SpawnerBackedUSIBridge(instance=_InstanceStub(), engine_path="probe")
    process = _ClosingProcessStub()
    bridge.process = cast(Any, process)
    first = asyncio.create_task(bridge.stop_process())
    await asyncio.wait_for(process.close_started.wait(), 1)
    second = asyncio.create_task(bridge.stop_process())
    try:
        for _ in range(2):
            first.cancel()
            await asyncio.sleep(0)
        assert not first.done() and not second.done()
        assert bridge.process is process
    finally:
        process.close_release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert process.wait_calls == process.close_calls == 1
    assert bridge.process is None


@pytest.mark.asyncio
async def test_preexisting_cancellation_does_not_turn_stderr_reader_cancel_into_forced_kill() -> None:
    bridge = SpawnerBackedUSIBridge(instance=_InstanceStub(), engine_path="probe")
    process = _ClosingProcessStub()
    process.close_release.set()
    bridge.process = cast(Any, process)
    bridge._stderr_task = asyncio.create_task(asyncio.Event().wait())
    started = asyncio.Event()

    async def worker() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert asyncio.current_task().cancelling() > 0
            await bridge.stop_process()
            raise

    task = asyncio.create_task(worker())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.kill_calls == 0
    assert process.wait_calls == process.close_calls == 1
    assert bridge._stderr_task is None
    assert bridge.process is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError])
async def test_broken_stdin_during_quit_does_not_join_its_own_stop_task(error) -> None:
    class BrokenWriter(_WriterStub):
        async def drain(self) -> None:
            raise error("pipe already closed")

    bridge = SpawnerBackedUSIBridge(instance=_InstanceStub(), engine_path="probe")
    process = _ClosingProcessStub()
    process.returncode = None
    process.stdin = BrokenWriter(is_closing=False)
    process.close_release.set()
    bridge.process = cast(Any, process)
    task = asyncio.create_task(bridge.stop_process())
    try:
        await asyncio.wait_for(asyncio.shield(task), 1)
    finally:
        if not task.done():
            # Bound a future regression without cancelling the protected caller
            # and then waiting forever for the deadlocked owned stop operation.
            bridge._stop_task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert process.wait_calls == process.close_calls == 1
    assert bridge.process is None


@pytest.mark.asyncio
async def test_cancelled_stop_owner_retrieves_cleanup_failure_without_another_waiter() -> None:
    class FailingCloseProcess(_ClosingProcessStub):
        async def close_transport(self) -> None:
            await super().close_transport()
            raise OSError("injected transport close failure")

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        bridge = SpawnerBackedUSIBridge(instance=_InstanceStub(), engine_path="probe")
        process = FailingCloseProcess()
        bridge.process = cast(Any, process)
        owner = asyncio.create_task(bridge.stop_process())
        await asyncio.wait_for(process.close_started.wait(), 1)
        for _ in range(2):
            owner.cancel("caller stopped")
            await asyncio.sleep(0)
            assert not owner.done()
        process.close_release.set()
        with pytest.raises(OSError, match="injected transport close failure"):
            await owner
        assert bridge.process is None
        del owner, bridge, process
        gc.collect()
        await asyncio.sleep(0)
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous_handler)
