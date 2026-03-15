"""Summary and progress payload builders for tournament dashboard."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from shogiarena._core.contexts.dashboard.application.tournament.payloads import (
    ProgressPayload,
    StandingEntry,
    StandingsPayload,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardGameQueryPort
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimator


def compute_pair_los(wins_a: int, wins_b: int, draws: int) -> float | None:
    total = wins_a + wins_b + draws
    if total <= 0:
        return None
    variance = total / 4
    if variance <= 0:
        return None
    score = wins_a + 0.5 * draws
    z = (score - total / 2) / math.sqrt(variance)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def build_standings_payload(
    *,
    db_path: Path,
    run_dir: Path,
    game_query: DashboardGameQueryPort,
) -> StandingsPayload:
    games = game_query.load_games(db_path)

    engine_stats: dict[str, dict[str, float]] = {}
    for game in games:
        black = game["black_engine"]
        white = game["white_engine"]
        result = game["result"]

        if black not in engine_stats:
            engine_stats[black] = {"wins": 0, "draws": 0, "losses": 0, "games": 0}
        if white not in engine_stats:
            engine_stats[white] = {"wins": 0, "draws": 0, "losses": 0, "games": 0}

        engine_stats[black]["games"] += 1
        engine_stats[white]["games"] += 1

        game_result = coerce_game_result(result, is_strict=True)
        if game_result.is_black_win():
            engine_stats[black]["wins"] += 1
            engine_stats[white]["losses"] += 1
        elif game_result.is_white_win():
            engine_stats[white]["wins"] += 1
            engine_stats[black]["losses"] += 1
        elif game_result.is_draw():
            engine_stats[black]["draws"] += 1
            engine_stats[white]["draws"] += 1

    engine_names = {str(game["black_engine"]) for game in games if game.get("black_engine")}
    engine_names |= {str(game["white_engine"]) for game in games if game.get("white_engine")}

    estimator_input: list[GameRecordPlayers] = []
    for game in games:
        estimator_input.append(
            {
                "black_player": game["black_engine"],
                "white_player": game["white_engine"],
                "result": game["result"],
            }
        )
    estimator = BTDEstimator().estimate(
        estimator_input,
        engine_names=engine_names,
    )
    ratings = {name: 1500.0 + float(rating) for name, rating in estimator.ratings.items()}

    standings: list[StandingEntry] = []
    for engine, stats in engine_stats.items():
        points = stats["wins"] + stats["draws"] * 0.5
        win_rate = stats["wins"] / stats["games"] if stats["games"] > 0 else 0
        standings.append(
            {
                "engine": engine,
                "points": points,
                "games": stats["games"],
                "wins": stats["wins"],
                "draws": stats["draws"],
                "losses": stats["losses"],
                "win_rate": win_rate,
                "rating": ratings.get(engine, 1500.0),
                "rank": 0,
            }
        )

    standings.sort(key=lambda entry: (-entry["points"], -entry["rating"]))
    for index, standing in enumerate(standings):
        standing["rank"] = index + 1

    engines_meta = _load_summary_engines_meta(run_dir)

    return {
        "standings": standings,
        "enginesMeta": engines_meta,
        "updated_at": datetime.now().isoformat(),
    }


def build_progress_payload(
    *,
    db_path: Path,
    run_dir: Path,
    logger: logging.Logger,
    game_query: DashboardGameQueryPort,
) -> ProgressPayload:
    run_state_path = run_dir / "run_state.json"
    if run_state_path.exists():
        try:
            raw_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            if not isinstance(raw_state, Mapping):
                raise ValueError("run_state.json must be an object")
            state = {str(key): value for key, value in raw_state.items()}
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to parse run_state.json for progress: %s", exc)
        else:
            total = coerce_int(state.get("total_games")) or 0
            completed_game_ids = state.get("completed_game_ids")
            completed = len(completed_game_ids) if isinstance(completed_game_ids, list) else 0
            pending = total - completed
            estimated_minutes = pending * 1
            return {
                "games": {
                    "completed": completed,
                    "total": total,
                    "cancelled": 0,
                },
                "inProgress": 0,
                "pending": pending,
                "completionRate": completed / total if total > 0 else 0,
                "estimatedTimeRemaining": f"{estimated_minutes:02d}:00:00",
                "updatedAt": datetime.now().isoformat(),
            }

        return {
            "games": {
                "completed": 0,
                "total": -1,
                "cancelled": 0,
            },
            "inProgress": 0,
            "pending": -1,
            "completionRate": -1,
            "estimatedTimeRemaining": "Unknown",
            "updatedAt": datetime.now().isoformat(),
        }

    games = game_query.load_games(db_path)
    completed = len(games)
    return {
        "games": {
            "completed": completed,
            "total": -1,
            "cancelled": 0,
        },
        "inProgress": 0,
        "pending": -1,
        "completionRate": -1,
        "estimatedTimeRemaining": "Unknown",
        "updatedAt": datetime.now().isoformat(),
    }


def _load_summary_engines_meta(run_dir: Path) -> list[JsonObject]:
    engines_meta: list[JsonObject] = []
    summary_btd_path = run_dir / "summary_btd.json"
    if not summary_btd_path.exists():
        return engines_meta
    with open(summary_btd_path, encoding="utf-8") as handle:
        summary_data = json.load(handle)
        engines_meta_raw = summary_data.get("enginesMeta", [])
        if isinstance(engines_meta_raw, list):
            normalized_meta: list[JsonObject] = []
            for item in engines_meta_raw:
                if not isinstance(item, Mapping):
                    continue
                normalized_meta.append({str(key): value for key, value in item.items()})
            engines_meta = normalized_meta
    return engines_meta


__all__ = [
    "build_progress_payload",
    "build_standings_payload",
    "compute_pair_los",
]
