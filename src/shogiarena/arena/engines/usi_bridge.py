"""Bridge interfaces for USI engine processes.

This module hosts the protocol contracts that USI process bridges must satisfy.
Keeping the interface alongside other engine-level helpers clarifies module
boundaries: ``AsyncUsiEngine`` and ``AsyncUsiProcess`` depend on bridge implementations,
while orchestrators remain agnostic of the transport details.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncUSIProcessBridgeProtocol(Protocol):
    """USI engine process bridge abstraction.

    Implementations are responsible for launching the underlying executable or
    remote session, streaming stdout lines, and delivering commands. Structural
    typing keeps integrations lightweight—classes only need to provide the
    methods defined here.
    """

    @property
    def name(self) -> str:
        """Return an identifier used for logging and diagnostics."""

    async def start_process(self) -> None:
        """Start the engine process and prepare for I/O."""

    async def stop_process(self) -> None:
        """Terminate the engine process and release resources."""

    async def send_line(self, command: str) -> None:
        """Send a single USI command (without a trailing newline)."""

    def receive_lines(self) -> AsyncIterator[str]:
        """Yield decoded stdout lines from the process."""

    def is_running(self) -> bool:
        """Return True when the process is alive and I/O is available."""
