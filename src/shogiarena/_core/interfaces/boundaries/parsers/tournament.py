"""Parser helpers for tournament dashboard API boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from shogiarena._core.interfaces.boundaries.parsers.json_object import BoundaryObject as _BoundaryObject
from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject

_BOUNDARY_ID_DASH_TOURNAMENT_API = "BND-DASH-TOURNAMENT-API"


class _TournamentStandingsEntryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine: str
    points: float
    games: float
    wins: float
    draws: float
    losses: float
    win_rate: float
    rating: float
    rank: NonNegativeInt


class _TournamentStandingsPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    standings: list[_TournamentStandingsEntryModel] = Field(default_factory=list)
    engines_meta: list[_BoundaryObject] = Field(default_factory=list)
    updated_at: str


class _TournamentGamesCounterModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    completed: int
    total: int
    cancelled: int


class _TournamentProgressPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    games: _TournamentGamesCounterModel
    in_progress: int
    pending: int
    completion_rate: float
    estimated_time_remaining: str
    updated_at: str


class _TournamentGameBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    game_id: str
    black_player: str | None = None
    white_player: str | None = None
    game_result: str | None = None
    total_plies: NonNegativeInt | None = None
    end_time: str | None = None
    initial_sfen: str | None = None
    time_control_black: str | None = None
    time_control_white: str | None = None


class _TournamentGamesListPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    games: list[_TournamentGameBaseModel]
    total: NonNegativeInt
    offset: NonNegativeInt
    limit: NonNegativeInt


class _TournamentGamePayloadModel(_TournamentGameBaseModel):
    game_id: str
    moves: list[str] = Field(default_factory=list)
    ki2_moves: list[str] = Field(default_factory=list)
    eval_black: list[float | None] = Field(default_factory=list)
    eval_white: list[float | None] = Field(default_factory=list)
    nodes_values: list[float | None] = Field(default_factory=list)
    depth_values: list[float | None] = Field(default_factory=list)
    seldepth_values: list[float | None] = Field(default_factory=list)
    move_times_ms: list[float | None] = Field(default_factory=list)
    wall_times_ms: list[float | None] = Field(default_factory=list)
    engine_wall_times_ms: list[float | None] = Field(default_factory=list)
    latency_deltas_ms: list[float | None] = Field(default_factory=list)
    total_plies: NonNegativeInt | None = None
    start_time: str | None = None


class _TournamentMatchHistoryPayloadModel(_TournamentGamesListPayloadModel):
    signature: str
    source: str
    fetched_at: str


class _TournamentHeadToHeadPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    head_to_head: list[_BoundaryObject] = Field(default_factory=list)
    updated_at: str


class _TournamentPairStatsEntryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pair_id: str
    engines: list[str]
    games: NonNegativeInt
    wins: dict[str, NonNegativeInt]
    draws: NonNegativeInt
    win_rate: dict[str, float | None]
    los: dict[str, float | None]


class _TournamentPairStatsPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pairs: list[_TournamentPairStatsEntryModel]
    total_pairs: NonNegativeInt
    signature: str
    source: str
    fetched_at: str


class _TournamentEngineOptionsPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine: str
    options: _BoundaryObject
    info: _BoundaryObject
    updated_at: str


class _TournamentStreamPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stream: str
    seq: NonNegativeInt
    type: str
    timestamp: NonNegativeInt | None = None
    resume_from: NonNegativeInt | None = None
    data: _BoundaryObject | None = None


TournamentPayloadKind: TypeAlias = Literal[
    "standings",
    "progress",
    "games_list",
    "game",
    "match_history",
    "head_to_head",
    "pair_stats",
    "engine_options",
    "stream",
]

_PAYLOAD_MODEL_BY_KIND: dict[TournamentPayloadKind, type[BaseModel]] = {
    "standings": _TournamentStandingsPayloadModel,
    "progress": _TournamentProgressPayloadModel,
    "games_list": _TournamentGamesListPayloadModel,
    "game": _TournamentGamePayloadModel,
    "match_history": _TournamentMatchHistoryPayloadModel,
    "head_to_head": _TournamentHeadToHeadPayloadModel,
    "pair_stats": _TournamentPairStatsPayloadModel,
    "engine_options": _TournamentEngineOptionsPayloadModel,
    "stream": _TournamentStreamPayloadModel,
}


def parse_tournament_payload(
    kind: TournamentPayloadKind,
    payload: Mapping[str, object],
    *,
    path: str = "root",
) -> JsonObject:
    """Validate and normalize tournament payload by kind."""
    serialized_payload = parse_wire(
        boundary_id=_BOUNDARY_ID_DASH_TOURNAMENT_API,
        payload=payload,
        model=_PAYLOAD_MODEL_BY_KIND[kind],
        path=path,
    )
    return coerce_json_object_serialized(
        serialized_payload.model_dump(mode="python"),
        field_name="Parsed payload",
    )


__all__ = [
    "parse_tournament_payload",
]
