"""Worker stdout writes must not block the game event loop."""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from shogiarena._core.shared.kernel.async_completion import complete_before_cancelling


def _write_and_flush(stream: TextIO, payload: str) -> None:
    stream.write(payload + "\n")
    stream.flush()


async def write_line(payload: str) -> None:
    """Finish one complete line before releasing its writer ownership."""
    await complete_before_cancelling(asyncio.to_thread(_write_and_flush, sys.stdout, payload))
