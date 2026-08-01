"""Build SPSA summary payload from aggregate counters and metadata."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.json_types import JsonObject


def _compute_window_stats(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    count = len(values)
    mean_val = sum(values) / count
    if count < 2:
        return mean_val, 0.0
    variance = sum((x - mean_val) ** 2 for x in values) / (count - 1)
    return mean_val, math.sqrt(variance)


def _estimate_eta_seconds(
    *,
    num_updates_total: int | None,
    updates_completed: int,
    update_timestamps: Sequence[int],
) -> int | None:
    if not num_updates_total or num_updates_total <= 0:
        return None
    if updates_completed <= 0:
        return None
    timestamps = sorted(update_timestamps[-10:])
    if len(timestamps) < 2:
        return None
    intervals = [max(0, timestamps[i] - timestamps[i - 1]) / 1000.0 for i in range(1, len(timestamps))]
    if not intervals:
        return None
    avg_interval = sum(intervals) / len(intervals)
    remaining = max(0, int(num_updates_total) - updates_completed)
    return int(remaining * avg_interval) if avg_interval > 0 else None


def build_summary_payload(
    *,
    experiment_name: str | None,
    num_updates_total: int | None,
    wins: int,
    losses: int,
    draws: int,
    tuned_black_wins: int,
    tuned_black_losses: int,
    tuned_white_wins: int,
    tuned_white_losses: int,
    updates_completed: int,
    last_update_idx: int | None,
    step_history: Sequence[float],
    last_delta_norm: float | None,
    update_timestamps: Sequence[int],
    engine_time_controls: Mapping[str, str],
    default_time_control: str | None,
    engines: Sequence[str],
    engine_instances: Mapping[str, str | None],
    engine_stats: Mapping[str, Mapping[str, int | float]],
    engines_meta: Sequence[Mapping[str, object]],
    spsa_config: JsonObject | None,
    current_session_uuid: str | None,
    resume_boundaries: Sequence[Mapping[str, object]],
    operational_status: Mapping[str, object],
) -> JsonObject:
    """Build dashboard summary payload from aggregate values."""

    games_total = wins + losses + draws
    steps = list(step_history)
    recent_steps = steps[-100:]
    step_mean_20, step_std_20 = _compute_window_stats(steps[-20:])
    eta_seconds = _estimate_eta_seconds(
        num_updates_total=num_updates_total,
        updates_completed=updates_completed,
        update_timestamps=update_timestamps,
    )

    winrate = (wins / games_total) if games_total > 0 else 0.0
    progress = (updates_completed / num_updates_total) if num_updates_total and num_updates_total > 0 else 0.0
    recent_step = recent_steps[-1] if recent_steps else 0.0
    recent_delta_norm = last_delta_norm if last_delta_norm is not None else 0.0

    engine_stats_payload = {name: dict(stats) for name, stats in engine_stats.items()}
    engines_list = list(engines)
    if engines_list:
        primary = engines_list[0]
        tuned = engines_list[1] if len(engines_list) > 1 else engines_list[0]
        live_games_total = wins + losses + draws
        engine_stats_payload[primary] = {
            "wins": losses,
            "losses": wins,
            "draws": draws,
            "games": live_games_total,
        }
        engine_stats_payload[tuned] = {
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "games": live_games_total,
        }

    return {
        "mode": "spsa",
        "experiment_name": experiment_name,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "elo": None,
        "btd_elo": None,
        "btd_se": None,
        "btd_los": None,
        "games_total": games_total,
        "draw_rate": (draws / games_total) if games_total > 0 else None,
        "tuned_black_wins": tuned_black_wins,
        "tuned_black_losses": tuned_black_losses,
        "tuned_white_wins": tuned_white_wins,
        "tuned_white_losses": tuned_white_losses,
        "games": {
            "completed": updates_completed,
            "total": num_updates_total or 0,
        },
        "last_update_idx": last_update_idx,
        "step_mean_20": step_mean_20,
        "step_std_20": step_std_20,
        "recent_steps": recent_steps,
        "delta_norm_last": last_delta_norm,
        "eta_seconds": eta_seconds,
        "winrate": winrate,
        "progress": progress,
        "recent_step": recent_step,
        "recent_delta_norm": recent_delta_norm,
        "engine_time_controls": dict(engine_time_controls),
        "default_time_control": default_time_control,
        "engines": engines_list,
        "engines_meta": [dict(entry) for entry in engines_meta],
        "engine_instances": dict(engine_instances),
        "engine_stats": engine_stats_payload,
        "spsa_config": spsa_config,
        "current_session_uuid": current_session_uuid,
        "resume_boundaries": [dict(boundary) for boundary in resume_boundaries],
        "operational_status": dict(operational_status),
    }


__all__ = ["build_summary_payload"]
