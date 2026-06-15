"""Seed summary payload service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from shogiarena._core.contexts.game_session.application.summary.optional_sections_service import (
    TournamentSummaryOptionalSectionsService,
)
from shogiarena._core.contexts.game_session.application.summary.payload_responses import (
    TournamentInitialBtdSummaryPayloadResponse,
    TournamentSeedSummaryPayloadResponse,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.shared.kernel.json_types import JsonObject


class TournamentSummarySeedPayloadService:
    """Build seed summary payload DTOs."""

    def __init__(self) -> None:
        self._optional_sections_service = TournamentSummaryOptionalSectionsService()

    def build(self, runtime: TournamentSummaryRuntimeContext) -> TournamentSeedSummaryPayloadResponse:
        config = runtime.request.config
        engine_names = [str(e.name) for e in config.engines]
        engines_meta = runtime.request.engine_metadata
        engine_tc_map, default_tc_spec = runtime.request.engine_time_controls
        engine_instances = runtime.actions.engine_instance_defaults()
        anchor_name = engine_names[0] if engine_names else None
        zero_stats = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engine_names}

        base_payload: JsonObject = {
            "tournament_type": runtime.actions.resolve_tournament_type(),
            "mode": runtime.request.summary_source,
            "flip_policy": config.rules.initial_positions.flip_policy,
            "num_engines": len(engine_names),
            "run_dir": str(runtime.request.run_dir),
            "leaderboard": [],
            "rating_initial": config.rating.initial,
            "engines": engine_names,
            "engines_meta": engines_meta,
            "engine_time_controls": engine_tc_map,
            "default_time_control": default_tc_spec,
            "engine_instances": engine_instances,
            "engine_stats": zero_stats,
            "pair_results": {},
            "timestamp": datetime.now().isoformat(),
            "games": {
                "completed": 0,
                "total": 0,
                "cancelled": 0,
            },
            "btd": {
                "ratings": {name: {"elo": 0.0, "se": 0.0} for name in engine_names},
                "anchor": anchor_name,
                "gamma_elo": 0.0,
                "gamma_elo_se": 0.0,
                "draw_eq": 0.5,
                "draw_eq_se": 0.0,
                "rating_cov": {name: {} for name in engine_names},
            },
        }
        if runtime.request.summary_source in {"sprt", "match"}:
            base_payload["games"] = {
                "completed": 0,
                "total": 0,
            }

        return TournamentSeedSummaryPayloadResponse(
            base_payload=base_payload,
            optional_sections=self._optional_sections_service.build(runtime),
        )

    @staticmethod
    def build_initial_btd_summary(
        *,
        engine_names: Sequence[str],
        engines_meta: Sequence[JsonObject],
        anchor_name: str | None,
    ) -> TournamentInitialBtdSummaryPayloadResponse:
        return TournamentInitialBtdSummaryPayloadResponse(
            ratings={name: {"elo": 0.0, "se": 0.0} for name in engine_names},
            anchor=anchor_name,
            gamma_elo=0.0,
            gamma_elo_se=0.0,
            engines_meta=list(engines_meta),
        )


__all__ = ["TournamentSummarySeedPayloadService"]
