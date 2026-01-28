"""Lightweight USI engine process wrapper."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol

logger = logging.getLogger(__name__)


class AsyncUsiProcess:
    """Minimal lifecycle and I/O wrapper around a ``AsyncUSIProcessBridgeProtocol``.

    The class keeps process control concerns isolated from higher-level session
    logic. It deliberately avoids embedding protocol knowledge: callers are
    responsible for interpreting stdout lines and orchestrating handshakes.
    """

    def __init__(self, bridge: AsyncUSIProcessBridgeProtocol) -> None:
        self._bridge = bridge
        self._state_lock = asyncio.Lock()
        self._running = False

    @property
    def name(self) -> str:
        """Engine name exposed by the underlying bridge."""
        return self._bridge.name

    async def start(self) -> None:
        """Start the engine process exactly once."""
        async with self._state_lock:
            if self._running:
                raise RuntimeError(f"USI process {self.name} already running")
            await self._bridge.start_process()
            self._running = True

    async def stop(self) -> None:
        """Stop the engine process if it is running."""
        async with self._state_lock:
            if not self._running:
                raise RuntimeError(f"USI process {self.name} not running")
            try:
                await self._bridge.stop_process()
            finally:
                self._running = False

    def is_running(self) -> bool:
        """Return True when the bridge reports the process alive."""
        return self._running and self._bridge.is_running()

    async def send_line(self, command: str) -> None:
        """Send a single USI command to the engine."""
        if not self.is_running():
            raise RuntimeError(f"USI process {self.name} is not running; cannot send '{command}'")
        await self._bridge.send_line(command)

    def receive_lines(self) -> AsyncIterator[str]:
        """Yield decoded stdout lines from the engine."""
        if not self.is_running():
            raise RuntimeError(f"USI process {self.name} is not running; cannot receive output")
        return self._bridge.receive_lines()
