"""Helper utilities for building Live View snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shogiarena.utils.types.coerce import coerce_bool, coerce_int, coerce_str
from shogiarena.web.dashboard.backend.live.types import LiveViewProgress, LiveViewSnapshot

LiveViewMode = str


def build_live_view_snapshot(
    summary_snapshot: Mapping[str, Any] | None,
) -> LiveViewSnapshot:
    """Build a normalized Live View envelope from summary payloads."""

    summary_payload: Mapping[str, Any] = summary_snapshot or {}

    mode = _infer_mode(summary_payload)
    progress = _derive_progress(summary_payload, mode)

    return {
        "version": 1,
        "mode": mode,
        "progress": progress,
    }


def _infer_mode(summary: Mapping[str, Any]) -> LiveViewMode:
    candidates: list[Any] = [
        summary.get("liveViewMode"),
        summary.get("mode"),
        summary.get("tournamentType"),
        summary.get("tournament_type"),
    ]
    for candidate in candidates:
        normalized = _normalize_mode_value(candidate)
        if normalized:
            return normalized

    return "unknown"


def _normalize_mode_value(candidate: Any) -> LiveViewMode | None:
    if not isinstance(candidate, str):
        return None
    value = candidate.strip().lower()
    if not value:
        return None
    if value == "spsa":
        return "spsa"
    if value == "match":
        return "match"
    if value == "sprt":
        return "sprt"
    return "tournament"


def _derive_progress(summary: Mapping[str, Any], mode: LiveViewMode) -> LiveViewProgress | None:
    if not summary:
        return None

    games = summary.get("games")
    if not isinstance(games, Mapping):
        return None

    completed = coerce_int(games.get("completed"))
    total = coerce_int(games.get("total"))
    cancelled = coerce_int(games.get("cancelled"))

    if mode == "spsa":
        unit_label = "updates"
        kind = "updates"
    else:
        unit_label = "games"
        kind = mode if mode in {"sprt", "match"} else "games"

    timestamp = coerce_str(summary.get("timestamp"))
    _raw_finished = summary.get("tournamentFinished")
    _is_finished = (isinstance(_raw_finished, str) and _raw_finished.strip().lower() == "finished") or coerce_bool(
        _raw_finished
    )
    fallback_state = "finished" if _is_finished else "normal"
    progress_state = _normalize_progress_state(summary.get("liveViewProgressState")) or fallback_state
    is_final = _is_finished
    if mode == "sprt":
        status = summary.get("status")
        if isinstance(status, Mapping):
            decision = status.get("decision")
            if isinstance(decision, str) and decision and decision.lower() != "continue":
                is_final = True
        # TODO: sprt/match の paused/draining は将来の run_state 連携で追加する。
    elif mode == "match":
        # TODO: sprt/match の paused/draining は将来の run_state 連携で追加する。
        pass
    if not is_final and completed is not None and total and total > 0 and completed >= total:
        is_final = True
    has_progress_fields = (
        any(value is not None for value in (completed, total, cancelled, timestamp))
        or progress_state != "normal"
        or is_final
    )
    if not has_progress_fields:
        return None

    return {
        "kind": kind,
        "unitLabel": unit_label,
        "completed": completed,
        "total": total,
        "cancelled": cancelled,
        "isFinal": is_final,
        "state": progress_state,
        "updatedAt": timestamp,
    }


def _normalize_progress_state(candidate: Any) -> str | None:
    if not isinstance(candidate, str):
        return None
    value = candidate.strip().lower()
    if value in {"normal", "paused", "draining", "finished"}:
        return value
    return None


__all__ = ["build_live_view_snapshot"]
