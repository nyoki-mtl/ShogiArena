"""Dashboard update summary payload service."""

from __future__ import annotations

from datetime import datetime

from shogiarena._core.contexts.game_session.application.summary.dashboard_sections_service import (
    TournamentSummaryDashboardSectionsService,
)
from shogiarena._core.contexts.game_session.application.summary.optional_sections_service import (
    TournamentSummaryOptionalSectionsService,
)
from shogiarena._core.contexts.game_session.application.summary.payload_responses import (
    TournamentDashboardSummaryPayloadResponse,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers


class TournamentSummaryUpdatePayloadService:
    """Build dashboard update payload DTOs."""

    def __init__(self) -> None:
        self._optional_sections_service = TournamentSummaryOptionalSectionsService()
        self._dashboard_sections_service = TournamentSummaryDashboardSectionsService()

    def build(
        self,
        runtime: TournamentSummaryRuntimeContext,
        *,
        results: TournamentResults,
        games: list[GameRecordPlayers],
    ) -> TournamentDashboardSummaryPayloadResponse:
        config = runtime.request.config
        engines_meta = runtime.request.engine_metadata
        engine_tc_map, default_tc_spec = runtime.request.engine_time_controls
        engine_names = [str(e.name) for e in config.engines]
        engine_instances = runtime.actions.engine_instance_defaults()
        cancelled_count = results.cancelled_games_count
        active_total_games = max(0, results.total_games - cancelled_count)

        base_payload: JsonObject = {
            "tournament_type": runtime.actions.resolve_tournament_type(),
            "mode": runtime.request.summary_source,
            "flip_policy": config.rules.initial_positions.flip_policy,
            "num_engines": len(engine_names),
            "run_dir": str(runtime.request.run_dir),
            "leaderboard": results.get_leaderboard(),
            "rating_initial": config.rating.initial,
            "engines": engine_names,
            "engines_meta": engines_meta,
            "engine_time_controls": engine_tc_map,
            "default_time_control": default_tc_spec,
            "engine_instances": engine_instances,
            "engine_stats": {
                name: {
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "draws": stats["draws"],
                    "games": stats.get("games", stats["wins"] + stats["losses"] + stats["draws"]),
                }
                for name, stats in results.engine_stats.items()
            },
            "pair_results": {f"{key[0]}_vs_{key[1]}": value for key, value in results.pair_results.items()},
            "timestamp": datetime.now().isoformat(),
            "games": {
                "completed": results.completed_games_count,
                "total": active_total_games,
                "cancelled": cancelled_count,
            },
        }
        return TournamentDashboardSummaryPayloadResponse(
            base_payload=base_payload,
            optional_sections=self._optional_sections_service.build(runtime),
            dashboard_sections=self._dashboard_sections_service.build(runtime, results=results, games=games),
        )


__all__ = ["TournamentSummaryUpdatePayloadService"]
