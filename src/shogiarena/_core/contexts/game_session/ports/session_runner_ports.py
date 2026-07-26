"""Shared session runner port contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class BeforeGameHookRequest:
    """対局前フックへの typed input。"""

    engines_by_pool_key: Mapping[str, object]


@dataclass(frozen=True)
class BeforeGameHookResult:
    """対局前フックからの typed output。"""

    display_name_overrides: dict[str, str]


class BeforeGameHookPort(Protocol):
    """対局前フック契約。"""

    async def run(self, request: BeforeGameHookRequest) -> BeforeGameHookResult | None: ...


class DashboardServerPort(Protocol):
    @property
    def api_server(self) -> Any | None: ...

    def set_worker_snapshot(
        self, worker_idx: int, snapshot: Mapping[str, object], *, should_broadcast: bool = True
    ) -> None: ...

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, object]) -> None: ...

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, object]) -> None: ...

    def broadcast_engine_io(self, worker_idx: int, payload: Mapping[str, object]) -> None: ...

    def broadcast_engine_io_batch(self, worker_idx: int, payload: Mapping[str, object]) -> None: ...

    def has_engine_io_subscribers(self, gid: str) -> bool: ...

    def clear_engine_logs(self, game_id: str | int) -> None: ...

    def update_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, object],
        info: Mapping[str, str] | None = None,
    ) -> None: ...

    def broadcast_summary_update(self, payload: Mapping[str, object], *, source: str) -> None: ...

    def broadcast_games_snapshot(self, snapshot: Mapping[str, object], *, event_type: str = "bulk") -> None: ...

    async def stop(self) -> None: ...

    async def start_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int: ...

    async def stop_server(self) -> None: ...


__all__ = [
    "BeforeGameHookPort",
    "BeforeGameHookRequest",
    "BeforeGameHookResult",
    "DashboardServerPort",
]
