"""Finalize orchestration for tournament summary artifacts and logging."""

from __future__ import annotations

import logging

from shogiarena._core.contexts.game_session.application.summary.artifact_service import (
    TournamentSummaryArtifactService,
)
from shogiarena._core.contexts.game_session.application.summary.final_payload_service import (
    TournamentSummaryFinalPayloadService,
)
from shogiarena._core.contexts.game_session.application.summary.reporting_service import (
    TournamentSummaryReportingService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults

logger = logging.getLogger(__name__)


class TournamentSummaryFinalizeService:
    """Finalize tournament summary state, artifacts, and reporting output."""

    def __init__(
        self,
        *,
        payload_service: TournamentSummaryFinalPayloadService,
        artifact_service: TournamentSummaryArtifactService,
        reporting_service: TournamentSummaryReportingService,
    ) -> None:
        self._final_payload_service = payload_service
        self._artifact_service = artifact_service
        self._reporting_service = reporting_service

    async def finalize(self, runtime: TournamentSummaryRuntimeContext, *, results: TournamentResults) -> None:
        logger.debug("Finalizing tournament")

        try:
            await runtime.actions.flush_openbench()
        except RuntimeError as exc:
            if runtime.dependencies.is_openbench_strict_mode:
                raise
            logger.warning("OpenBench final flush failed; continuing (strict=false): %s", exc)

        runtime.actions.save_run_state(True)
        run_dir = runtime.request.run_dir
        (run_dir / "completed.flag").touch()

        if runtime.request.is_dashboard_enabled:
            await runtime.actions.update_dashboard()

        results_data = self._final_payload_service.build_results_payload(results).to_json_object()
        self._artifact_service.write_tournament_results(run_dir, results_data)
        self._reporting_service.log_tournament_results(results)

        db_service = runtime.dependencies.db_service
        games = db_service.get_games_with_players() if db_service else []
        engine_names = [str(e.name) for e in runtime.request.config.engines]
        anchor_name = engine_names[0] if engine_names else None
        btd = self._reporting_service.estimate_btd(games=games, anchor_name=anchor_name, engine_names=engine_names)
        self._reporting_service.log_btd_estimates(results=results, btd=btd, engine_names=engine_names)

        summary = self._final_payload_service.build_final_btd_payload(results=results, btd=btd).to_json_object()
        self._artifact_service.write_summary_btd(run_dir, summary, log_context="final")


__all__ = ["TournamentSummaryFinalizeService"]
