"""Seed initialization orchestration for tournament summaries."""

from __future__ import annotations

import logging

from shogiarena._core.contexts.game_session.application.summary.artifact_service import (
    TournamentSummaryArtifactService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.application.summary.seed_payload_service import (
    TournamentSummarySeedPayloadService,
)

logger = logging.getLogger(__name__)


class TournamentSummarySeedService:
    """Build and emit initial summary state for a new tournament run."""

    def __init__(
        self,
        *,
        payload_service: TournamentSummarySeedPayloadService,
        artifact_service: TournamentSummaryArtifactService,
    ) -> None:
        self._payload_service = payload_service
        self._artifact_service = artifact_service

    async def seed(self, runtime: TournamentSummaryRuntimeContext) -> None:
        seed_summary = self._payload_service.build(runtime).to_json_object()
        api_server = runtime.dependencies.api_server
        if api_server is not None and seed_summary.get("rules"):
            try:
                schedule_snapshot = await runtime.actions.get_schedule_snapshot()
            except (RuntimeError, ValueError, OSError) as exc:  # pragma: no cover - defensive
                logger.warning("Failed to seed games snapshot: %s", exc, exc_info=True)
            else:
                api_server.broadcast_games_snapshot(schedule_snapshot)
            api_server.broadcast_summary_update(seed_summary, source=runtime.request.summary_source)

        engine_names = [str(e.name) for e in runtime.request.config.engines]
        anchor_name = engine_names[0] if engine_names else None
        btd_summary = self._payload_service.build_initial_btd_summary(
            engine_names=engine_names,
            engines_meta=runtime.request.engine_metadata,
            anchor_name=anchor_name,
        ).to_json_object()
        self._artifact_service.write_summary_btd(runtime.request.run_dir, btd_summary, log_context="initial")


__all__ = ["TournamentSummarySeedService"]
