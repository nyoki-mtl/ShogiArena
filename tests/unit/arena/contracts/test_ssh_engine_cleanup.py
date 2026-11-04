from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shogiarena._core.contexts.instances.application.engine_process_handles import SSHEngineProcess


@pytest.mark.asyncio
async def test_cancelled_ssh_process_wait_drains_connection_and_probe():
    waiting, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_process():
        waiting.set()
        await asyncio.Event().wait()

    async def wait_closed():
        closing.set()
        await release.wait()

    process = object.__new__(SSHEngineProcess)
    process._proc = SimpleNamespace(wait=wait_process)
    process._conn = SimpleNamespace(close=Mock(), wait_closed=wait_closed)
    process._is_closed = False
    process._instance = SimpleNamespace(stop_network_probe=AsyncMock())
    process._probe_task = object()
    probe = process._probe_task
    task = asyncio.create_task(process.wait())
    await waiting.wait()
    task.cancel("original wait cancellation")
    await closing.wait()
    task.cancel("repeat")
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="original wait cancellation"):
        await task
    process._conn.close.assert_called_once()
    process._instance.stop_network_probe.assert_awaited_once_with(probe)
    assert process._is_closed and process._probe_task is None
