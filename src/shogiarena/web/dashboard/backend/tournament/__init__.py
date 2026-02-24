"""Tournament-related backend modules for the arena dashboard."""

from __future__ import annotations

from .api import TournamentAPI
from .types import (
    EngineOptionsResponse,
    GamesCounter,
    GamesListResponse,
    MatchHistoryResponse,
    PairStatsEntry,
    PairStatsResponse,
    ProgressPayload,
    StandingEntry,
    StandingsPayload,
    TournamentGameEntry,
)

__all__ = [
    "EngineOptionsResponse",
    "GamesCounter",
    "GamesListResponse",
    "MatchHistoryResponse",
    "PairStatsEntry",
    "PairStatsResponse",
    "ProgressPayload",
    "StandingEntry",
    "StandingsPayload",
    "TournamentAPI",
    "TournamentGameEntry",
]
