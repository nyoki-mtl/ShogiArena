from __future__ import annotations

import asyncio
import logging

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

    async def wait(self) -> int:
        self.wait_calls += 1
        await asyncio.Event().wait()
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1


@pytest.mark.asyncio
async def test_send_line_stop_logs_debug_when_process_stdin_is_closed(caplog: pytest.LogCaptureFixture) -> None:
    bridge = SpawnerBackedUSIBridge(
        instance=_InstanceStub(),
        engine_path="/tmp/engine",
        name="dummy",
    )
    bridge.process = _ProcessStub(writer=_WriterStub(is_closing=True))
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
    bridge.process = process
    caplog.set_level(logging.WARNING)

    await asyncio.wait_for(bridge.stop_process(), timeout=0.2)

    assert process.kill_calls == 1
    assert process.wait_calls == 2
    assert bridge.process is None
    assert bridge._is_stopping is False  # noqa: SLF001
    assert any("did not exit after kill" in record.message for record in caplog.records)
