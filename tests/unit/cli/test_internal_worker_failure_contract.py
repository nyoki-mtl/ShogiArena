from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from shogiarena._core.interfaces.cli.internal import remote


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [KeyError("key"), AssertionError("assertion"), AttributeError("attribute")])
async def test_unexpected_execution_failure_gets_error_envelope(monkeypatch, failure):
    sink = io.StringIO()
    monkeypatch.setattr(remote.sys, "stdout", sink)
    queue = asyncio.Queue()

    async def execute():
        raise failure

    worker = SimpleNamespace(request_shutdown=lambda: None)
    execution = asyncio.create_task(execute())
    streamer = asyncio.create_task(remote._stream_progress(queue))
    assert await remote._finish_execution(worker, execution, streamer, queue) == 1
    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(rows) == 1 and rows[0]["type"] == "error"
    assert type(failure).__name__ in rows[0]["message"]
    assert "Traceback" in rows[0]["trace"]


@pytest.mark.asyncio
async def test_broken_output_remains_primary_when_engine_cleanup_also_fails(caplog):
    started = asyncio.Event()

    async def execute():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("injected cleanup failure")

    async def stream():
        await started.wait()
        raise BrokenPipeError("injected broken stdout")

    execution, streamer = asyncio.create_task(execute()), asyncio.create_task(stream())
    worker = SimpleNamespace(request_shutdown=lambda: None)
    with pytest.raises(BrokenPipeError, match="injected broken stdout"):
        await remote._finish_execution(worker, execution, streamer, asyncio.Queue())
    assert execution.done() and streamer.done()
    assert "cleanup failed" in caplog.text and "injected cleanup failure" in caplog.text
