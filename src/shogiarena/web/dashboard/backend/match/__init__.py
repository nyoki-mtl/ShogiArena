"""Match dashboard API package."""

from __future__ import annotations

from shogiarena.web.dashboard.backend.match.api import MatchAPI
from shogiarena.web.dashboard.backend.match.types import (
    ColorCounts,
    ColorTimelineEntry,
    ConfidenceInterval,
    MatchGames,
    MatchPayload,
    MatchTimelineEntry,
    WdlGamesCount,
)

__all__ = [
    "ColorCounts",
    "ColorTimelineEntry",
    "ConfidenceInterval",
    "MatchAPI",
    "MatchGames",
    "MatchPayload",
    "MatchTimelineEntry",
    "WdlGamesCount",
]
