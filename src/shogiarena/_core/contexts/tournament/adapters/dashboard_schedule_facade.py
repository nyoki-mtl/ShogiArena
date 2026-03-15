"""Dedicated schedule boundary for the dashboard scheduler API.

Bridges ``ScheduleMutationService`` and ``ScheduleSnapshotService``
to the dashboard layer without exposing the tournament runner itself.
"""

from __future__ import annotations

from collections.abc import Callable

from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_mutation_service import ScheduleMutationService
from shogiarena._core.contexts.tournament.application.session.schedule_snapshot_service import ScheduleSnapshotService
from shogiarena._core.shared.kernel.json_types import JsonObject


class DashboardScheduleFacade:
    """Schedule boundary object wired into the dashboard scheduler API."""

    def __init__(
        self,
        mutation_service: ScheduleMutationService,
        snapshot_service: ScheduleSnapshotService,
        state: TournamentRunnerState,
        schedule_ctx_supplier: Callable[[], TournamentScheduleContext],
    ) -> None:
        self._mutation = mutation_service
        self._snapshot = snapshot_service
        self._state = state
        self._schedule_ctx_supplier = schedule_ctx_supplier

    async def get_schedule_snapshot(self) -> JsonObject:
        return await self._snapshot.get_schedule_snapshot(self._state, self._schedule_ctx_supplier())

    async def request_reschedule(self, *, seed: str | None = None) -> JsonObject:
        return await self._mutation.request_reschedule(self._state, self._schedule_ctx_supplier(), seed=seed)

    async def cancel_pending_games(self) -> JsonObject:
        return await self._mutation.cancel_pending_games(self._state, self._schedule_ctx_supplier())

    async def cancel_game(self, game_id: str) -> JsonObject:
        return await self._mutation.cancel_game(self._state, self._schedule_ctx_supplier(), game_id)

    async def restore_game(self, game_id: str) -> JsonObject:
        return await self._mutation.restore_game(self._state, self._schedule_ctx_supplier(), game_id)

    async def set_game_instance(
        self,
        game_id: str,
        *,
        mode: str = "auto",
        shared_instance: str | None = None,
        black_instance: str | None = None,
        white_instance: str | None = None,
        should_require_install: bool = False,
    ) -> JsonObject:
        return await self._mutation.set_game_instance(
            self._state,
            self._schedule_ctx_supplier(),
            game_id,
            mode=mode,
            shared_instance=shared_instance,
            black_instance=black_instance,
            white_instance=white_instance,
            should_require_install=should_require_install,
        )


__all__ = ["DashboardScheduleFacade"]
