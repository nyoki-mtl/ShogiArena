"""Local subprocess streams with an explicit transport-disconnection signal."""

from __future__ import annotations

import asyncio
from asyncio.subprocess import SubprocessStreamProtocol


class LocalProcessProtocol(asyncio.SubprocessProtocol):
    """Keep final transport loss distinct from the stream protocol's stdin loss.

    Process.wait() may return as soon as the exit status is known. The separate
    signal also waits for every pipe's connection_lost callback to finish.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, *, limit: int) -> None:
        self.streams = SubprocessStreamProtocol(limit=limit, loop=loop)
        self.disconnected: asyncio.Future[None] = loop.create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.streams.connection_made(transport)

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        self.streams.pipe_data_received(fd, data)

    def pipe_connection_lost(self, fd: int, exc: Exception | None) -> None:
        self.streams.pipe_connection_lost(fd, exc)

    def process_exited(self) -> None:
        self.streams.process_exited()

    def pause_writing(self) -> None:
        self.streams.pause_writing()

    def resume_writing(self) -> None:
        self.streams.resume_writing()

    def connection_lost(self, exc: Exception | None) -> None:
        try:
            self.streams.connection_lost(exc)
        finally:
            if not self.disconnected.done():
                self.disconnected.set_result(None)
