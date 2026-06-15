"""DTO responses for tournament summary payload assembly."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.game_session.application.summary.dashboard_sections_service import (
    TournamentSummaryDashboardSectionsResponse,
)
from shogiarena._core.contexts.game_session.application.summary.optional_sections_service import (
    TournamentSummaryOptionalSectionsResponse,
)
from shogiarena._core.contexts.game_session.domain.summary_models import EngineWdlCounts
from shogiarena._core.shared.kernel.json_types import JsonObject


def _apply_optional_sections(
    payload: JsonObject,
    optional_sections: TournamentSummaryOptionalSectionsResponse,
) -> None:
    if optional_sections.rules is None:
        return

    payload["rules"] = optional_sections.rules
    payload["initial_positions"] = optional_sections.initial_positions
    payload["repetition_occurrences_to_draw"] = optional_sections.repetition_occurrences_to_draw
    payload["flip_policy"] = payload.get("flip_policy") or optional_sections.flip_policy
    if optional_sections.tournament_config is not None:
        payload["tournament_config"] = optional_sections.tournament_config
    if optional_sections.sprt is not None and "sprt" not in payload:
        payload["sprt"] = optional_sections.sprt
    if optional_sections.generate_config is not None:
        payload["generate_config"] = optional_sections.generate_config
    if optional_sections.records_output is not None:
        payload["records_output"] = optional_sections.records_output


@dataclass(slots=True)
class TournamentSeedSummaryPayloadResponse:
    """Seed summary payload response."""

    base_payload: JsonObject
    optional_sections: TournamentSummaryOptionalSectionsResponse

    def to_json_object(self) -> JsonObject:
        payload: JsonObject = dict(self.base_payload)
        _apply_optional_sections(payload, self.optional_sections)
        return payload


@dataclass(slots=True)
class TournamentDashboardSummaryPayloadResponse:
    """Dashboard summary payload response."""

    base_payload: JsonObject
    optional_sections: TournamentSummaryOptionalSectionsResponse
    dashboard_sections: TournamentSummaryDashboardSectionsResponse

    def to_json_object(self) -> JsonObject:
        payload: JsonObject = dict(self.base_payload)
        _apply_optional_sections(payload, self.optional_sections)
        payload["btd"] = self.dashboard_sections.btd
        if self.dashboard_sections.records_summary is not None:
            payload["records_summary"] = self.dashboard_sections.records_summary
        if self.dashboard_sections.pentanomial is not None:
            payload["pentanomial"] = self.dashboard_sections.pentanomial
        if self.dashboard_sections.sprt is not None:
            payload["sprt"] = self.dashboard_sections.sprt
        if self.dashboard_sections.pair_outcome is not None:
            payload.update(self.dashboard_sections.pair_outcome)
        return payload


@dataclass(slots=True)
class TournamentInitialBtdSummaryPayloadResponse:
    """Initial BTD summary payload response."""

    ratings: JsonObject
    anchor: str | None
    gamma_elo: float
    gamma_elo_se: float
    engines_meta: list[JsonObject]

    def to_json_object(self) -> JsonObject:
        return {
            "ratings": self.ratings,
            "anchor": self.anchor,
            "gamma_elo": self.gamma_elo,
            "gamma_elo_se": self.gamma_elo_se,
            "engines_meta": self.engines_meta,
        }


@dataclass(slots=True)
class TournamentResultsPayloadResponse:
    """Tournament results payload response."""

    leaderboard: list[JsonObject]
    engine_stats: dict[str, EngineWdlCounts]
    pair_results: JsonObject
    total_games: int
    completed_games: int
    timestamp_iso: str

    def to_json_object(self) -> JsonObject:
        return {
            "leaderboard": self.leaderboard,
            "engine_stats": self.engine_stats,
            "pair_results": self.pair_results,
            "total_games": self.total_games,
            "completed_games": self.completed_games,
            "timestamp": self.timestamp_iso,
        }


@dataclass(slots=True)
class TournamentFinalBtdSummaryPayloadResponse:
    """Final BTD summary payload response."""

    ratings: JsonObject
    gamma_elo: float
    gamma_elo_se: float | None
    nu: float
    nu_se: float | None
    draw_eq: float
    draw_eq_se: float | None
    pairs: JsonObject
    engines_meta: list[JsonObject]

    def to_json_object(self) -> JsonObject:
        return {
            "ratings": self.ratings,
            "gamma_elo": self.gamma_elo,
            "gamma_elo_se": self.gamma_elo_se,
            "nu": self.nu,
            "nu_se": self.nu_se,
            "draw_eq": self.draw_eq,
            "draw_eq_se": self.draw_eq_se,
            "pairs": self.pairs,
            "engines_meta": self.engines_meta,
        }


__all__ = [
    "TournamentDashboardSummaryPayloadResponse",
    "TournamentFinalBtdSummaryPayloadResponse",
    "TournamentInitialBtdSummaryPayloadResponse",
    "TournamentResultsPayloadResponse",
    "TournamentSeedSummaryPayloadResponse",
]
