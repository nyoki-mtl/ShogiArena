"""Response shapes for the CSA dashboard API."""

from __future__ import annotations

from typing import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


class CsaRunEntry(TypedDict, total=False):
    """One bridge run as the summary reports it."""

    run_id: str
    worker_idx: int
    phase: str | None
    wins: int
    losses: int
    draws: int
    games: int
    alerts: int
    bridge_version: str | None
    # Enough to draw a run that owns no game yet, and therefore publishes no
    # worker snapshot for the status panel to read.
    phase_since_ts: int | None
    alert_entries: list[JsonValue]


class CsaGamesProgress(TypedDict):
    completed: int
    total: int
    cancelled: int


class CsaSummary(TypedDict, total=False):
    """Minimal but valid ``TournamentSummary`` for the ``csa`` profile.

    The page fetches this unconditionally on boot and throws when it fails, so it
    has to be answerable before a single game exists.
    """

    is_summary_ready: bool
    summary_source: str
    mode: str
    tournament_type: str | None
    flip_policy: str | None
    num_engines: int
    run_dir: str | None
    leaderboard: list[JsonValue]
    rating_initial: float | None
    engines: list[JsonValue]
    engines_meta: list[JsonValue]
    engine_time_controls: JsonObject
    default_time_control: str | None
    engine_instances: JsonObject
    engine_stats: JsonObject
    pair_results: JsonObject
    timestamp: str
    games: CsaGamesProgress
    csa_runs: list[JsonValue]
    live_view: JsonValue


__all__ = ["CsaGamesProgress", "CsaRunEntry", "CsaSummary"]
