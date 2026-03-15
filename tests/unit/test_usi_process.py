import asyncio
from typing import cast

import pytest

from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiProcess
from shogiarena._core.platform.engine_runtime.usi_protocol_types import AsyncUsiProcessBridgePort

_SENTINEL = object()


class DummyBridge(AsyncUsiProcessBridgePort):
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.sent: list[str] = []
        self._running = False
        self._queue: asyncio.Queue[object] = asyncio.Queue()

    @property
    def name(self) -> str:  # noqa: D401
        return "dummy"

    async def start_process(self) -> None:  # noqa: D401
        self.started += 1
        self._running = True

    async def stop_process(self) -> None:  # noqa: D401
        self.stopped += 1
        self._running = False
        await self._queue.put(_SENTINEL)

    async def send_line(self, command: str) -> None:  # noqa: D401
        if not self._running:
            raise RuntimeError("process not running")
        self.sent.append(command)

    def receive_lines(self):  # noqa: D401
        async def iterator():
            while True:
                item = await self._queue.get()
                if item is _SENTINEL:
                    break
                yield cast(str, item)

        return iterator()

    def is_running(self) -> bool:  # noqa: D401
        return self._running

    def queue_line(self, line: str) -> None:
        self._queue.put_nowait(line)

    def close_stream(self) -> None:
        self._queue.put_nowait(_SENTINEL)


@pytest.mark.asyncio
async def test_start_and_stop_require_valid_state() -> None:
    bridge = DummyBridge()
    process = AsyncUsiProcess(bridge)

    with pytest.raises(RuntimeError):
        await process.stop()

    await process.start()
    assert bridge.started == 1
    assert process.is_running()

    with pytest.raises(RuntimeError):
        await process.start()

    await process.stop()
    assert bridge.stopped == 1
    assert not process.is_running()

    with pytest.raises(RuntimeError):
        await process.stop()


@pytest.mark.asyncio
async def test_send_requires_running() -> None:
    bridge = DummyBridge()
    process = AsyncUsiProcess(bridge)

    with pytest.raises(RuntimeError):
        await process.send_line("usi")

    await process.start()
    await process.send_line("usi")
    assert bridge.sent == ["usi"]


@pytest.mark.asyncio
async def test_receive_lines_streams_output() -> None:
    bridge = DummyBridge()
    process = AsyncUsiProcess(bridge)

    await process.start()
    bridge.queue_line("info depth 1")
    bridge.queue_line("info depth 2")
    bridge.close_stream()

    lines: list[str] = []
    async for line in process.receive_lines():
        lines.append(line)

    assert lines == ["info depth 1", "info depth 2"]

    await process.stop()
