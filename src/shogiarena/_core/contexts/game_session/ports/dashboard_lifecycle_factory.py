"""Port contracts for game-runtime dashboard lifecycle wiring.

Concrete implementations are injected via composition root, not bootstrap
registry.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import DashboardProfile
from shogiarena._core.shared.kernel.json_types import JsonObject

# ---------------------------------------------------------------------------
# Protocol: asset writer
# ---------------------------------------------------------------------------


@runtime_checkable
class InitDashboardHtmlFn(Protocol):
    """Callable that writes dashboard HTML/CSS/JS assets to *run_dir*."""

    def __call__(
        self,
        run_dir: Path,
        num_workers: int,
        *,
        profiles: Sequence[DashboardProfile] | None = ...,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Protocol: API server
# ---------------------------------------------------------------------------


class DashboardApiServerPort(Protocol):
    """Minimal protocol satisfied by ``ArenaAPIServer``."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class DashboardScheduleBoundaryPort(Protocol):
    """Schedule capability contract wired into the dashboard API server."""

    async def get_schedule_snapshot(self) -> JsonObject: ...

    async def request_reschedule(self, *, seed: str | None = None) -> JsonObject: ...

    async def restore_game(self, game_id: str) -> JsonObject: ...

    async def cancel_pending_games(self) -> JsonObject: ...

    async def cancel_game(self, game_id: str) -> JsonObject: ...

    async def set_game_instance(
        self,
        game_id: str,
        *,
        mode: str,
        shared_instance: str | None = None,
        black_instance: str | None = None,
        white_instance: str | None = None,
        should_require_install: bool = False,
    ) -> JsonObject: ...


@runtime_checkable
class DashboardApiServerFactory(Protocol):
    """Factory that creates a dashboard API server instance."""

    def __call__(
        self,
        db_path: Path,
        port: int,
        run_dir: Path | None = ...,
        instance_pool: object | None = ...,
        *,
        host: str = ...,
        read_only: bool = ...,
        dashboard_num_workers: int = ...,
        dashboard_profiles: tuple[DashboardProfile, ...] | None = ...,
        schedule_boundary: DashboardScheduleBoundaryPort | None = ...,
    ) -> DashboardApiServerPort: ...


__all__ = [
    "DashboardApiServerFactory",
    "DashboardScheduleBoundaryPort",
    "InitDashboardHtmlFn",
]
