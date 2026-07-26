"""Summary/result services extracted from tournament runner."""

from __future__ import annotations

from shogiarena._core.contexts.game_session.application.summary.artifact_service import (
    TournamentSummaryArtifactService,
)
from shogiarena._core.contexts.game_session.application.summary.dashboard_update_service import (
    TournamentSummaryDashboardUpdateService,
)
from shogiarena._core.contexts.game_session.application.summary.final_payload_service import (
    TournamentSummaryFinalPayloadService,
)
from shogiarena._core.contexts.game_session.application.summary.finalize_service import (
    TournamentSummaryFinalizeService,
)
from shogiarena._core.contexts.game_session.application.summary.reporting_service import (
    TournamentSummaryReportingService,
)
from shogiarena._core.contexts.game_session.application.summary.results_service import TournamentSummaryResultsService
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.application.summary.seed_payload_service import (
    TournamentSummarySeedPayloadService,
)
from shogiarena._core.contexts.game_session.application.summary.seed_service import TournamentSummarySeedService
from shogiarena._core.contexts.game_session.application.summary.update_payload_service import (
    TournamentSummaryUpdatePayloadService,
)
from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.json_types import JsonObject


class TournamentSummaryService:
    """Build/emit seed and periodic summaries plus final artifacts."""

    def __init__(self) -> None:
        seed_payload_service = TournamentSummarySeedPayloadService()
        update_payload_service = TournamentSummaryUpdatePayloadService()
        final_payload_service = TournamentSummaryFinalPayloadService()
        self._artifact_service = TournamentSummaryArtifactService()
        self._reporting_service = TournamentSummaryReportingService()
        results_service = TournamentSummaryResultsService()
        self._seed_service = TournamentSummarySeedService(
            payload_service=seed_payload_service,
            artifact_service=self._artifact_service,
        )
        self._dashboard_update_service = TournamentSummaryDashboardUpdateService(
            payload_service=update_payload_service,
            results_service=results_service,
        )
        self._finalize_service = TournamentSummaryFinalizeService(
            payload_service=final_payload_service,
            artifact_service=self._artifact_service,
            reporting_service=self._reporting_service,
        )

    async def seed_initial_summary(self, runtime: TournamentSummaryRuntimeContext) -> None:
        await self._seed_service.seed(runtime)

    async def update_dashboard(self, runtime: TournamentSummaryRuntimeContext) -> None:
        """Update dashboard with current results."""
        await self._dashboard_update_service.update(runtime)

    async def finalize_tournament(self, runtime: TournamentSummaryRuntimeContext, results: TournamentResults) -> None:
        """Finalize tournament with result artifacts and logs."""
        await self._finalize_service.finalize(runtime, results=results)

    def write_interrupted_status(
        self,
        runtime: TournamentSummaryRuntimeContext,
        reason: RunTerminationReason,
        *,
        results: TournamentResults,
        watchdog: JsonObject | None = None,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        """finalize へ到達できない終了の terminal status を書く（task 0052）。"""
        self._finalize_service.write_interrupted_status(
            runtime.request.run_dir,
            reason,
            results=results,
            watchdog=watchdog,
            is_provisional=is_provisional,
            cleanup_error=cleanup_error,
        )


__all__ = ["TournamentSummaryService"]
