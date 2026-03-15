"""Dashboard update orchestration for tournament summaries."""

from __future__ import annotations

import logging

from shogiarena._core.contexts.game_session.application.summary.results_service import TournamentSummaryResultsService
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.application.summary.update_payload_service import (
    TournamentSummaryUpdatePayloadService,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers

logger = logging.getLogger(__name__)


class TournamentSummaryDashboardUpdateService:
    """Build and broadcast dashboard summary updates."""

    def __init__(
        self,
        *,
        payload_service: TournamentSummaryUpdatePayloadService,
        results_service: TournamentSummaryResultsService,
    ) -> None:
        self._update_payload_service = payload_service
        self._results_service = results_service

    async def update(self, runtime: TournamentSummaryRuntimeContext) -> None:
        results: TournamentResults = self._results_service.calculate_results(runtime)
        db_service = runtime.dependencies.db_service
        games: list[GameRecordPlayers] = list(db_service.get_games_with_players()) if db_service else []
        summary_data = self._update_payload_service.build(runtime, results=results, games=games).to_json_object()

        api_server = runtime.dependencies.api_server
        if api_server:
            try:
                schedule_snapshot = await runtime.actions.get_schedule_snapshot()
            except (RuntimeError, ValueError, OSError) as exc:  # pragma: no cover - defensive
                logger.warning("Failed to generate schedule snapshot for games SSE: %s", exc, exc_info=True)
            else:
                api_server.broadcast_games_snapshot(schedule_snapshot)
            api_server.broadcast_summary_update(summary_data, source=runtime.request.summary_source)


__all__ = ["TournamentSummaryDashboardUpdateService"]
