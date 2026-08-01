"""Sealed GameExecutionSpec worker runtime contract。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import rsshogi.record

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
    GameExecutionSpec,
)


@dataclass(frozen=True, slots=True)
class GameExecutionOutcome:
    """Worker内部recordとwire result envelope。"""

    record: rsshogi.record.Record
    result: GameExecutionResult


class GameExecutionWorkerPort(Protocol):
    """一局specを現在のworker filesystemで実行するport。"""

    async def execute(
        self,
        spec: GameExecutionSpec,
        *,
        execution_root: Path,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        secret_values: Mapping[str, str] | None = None,
    ) -> GameExecutionOutcome: ...

    def request_shutdown(self) -> None: ...


__all__ = ["GameExecutionOutcome", "GameExecutionWorkerPort"]
