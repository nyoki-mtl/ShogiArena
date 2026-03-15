"""Final summary/result payload service."""

from __future__ import annotations

from datetime import datetime

from shogiarena._core.contexts.game_session.application.summary.final_btd_sections_service import (
    TournamentSummaryFinalBtdSectionsService,
)
from shogiarena._core.contexts.game_session.application.summary.payload_responses import (
    TournamentFinalBtdSummaryPayloadResponse,
    TournamentResultsPayloadResponse,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimate


class TournamentSummaryFinalPayloadService:
    """Build final result artifact payload DTOs."""

    def __init__(self) -> None:
        self._final_btd_sections_service = TournamentSummaryFinalBtdSectionsService()

    @staticmethod
    def build_results_payload(results: TournamentResults) -> TournamentResultsPayloadResponse:
        pair_results: JsonObject = {f"{key[0]}_vs_{key[1]}": value for key, value in results.pair_results.items()}
        return TournamentResultsPayloadResponse(
            leaderboard=results.get_leaderboard(),
            engine_stats=results.engine_stats,
            pair_results=pair_results,
            total_games=results.total_games,
            completed_games=results.completed_games_count,
            timestamp_iso=datetime.now().isoformat(),
        )

    def build_final_btd_payload(
        self,
        *,
        results: TournamentResults,
        btd: BTDEstimate,
    ) -> TournamentFinalBtdSummaryPayloadResponse:
        sections = self._final_btd_sections_service.build(results=results, btd=btd)
        return TournamentFinalBtdSummaryPayloadResponse(
            ratings=sections.ratings,
            gamma_elo=btd.gamma_elo,
            gamma_elo_se=btd.gamma_elo_se,
            nu=btd.nu,
            nu_se=btd.nu_se,
            draw_eq=btd.draw_eq,
            draw_eq_se=btd.draw_eq_se,
            pairs=sections.pairs,
            engines_meta=sections.engines_meta,
        )


__all__ = ["TournamentSummaryFinalPayloadService"]
