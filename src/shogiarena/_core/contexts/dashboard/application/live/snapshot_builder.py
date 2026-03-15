"""Helper utilities for building Live View snapshots."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.application.live.view_payloads import LiveViewProgress, LiveViewSnapshot
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize

LiveViewMode = str


def build_live_view_snapshot(
    summary_snapshot: Mapping[str, JsonValue] | None,
) -> LiveViewSnapshot:
    """Build a normalized Live View envelope from summary payloads."""

    summary_payload: Mapping[str, JsonValue] = summary_snapshot or {}

    mode = _infer_mode(summary_payload)
    progress = _derive_progress(summary_payload, mode)

    return {
        "version": 1,
        "mode": mode,
        "progress": progress,
    }


def attach_live_view_payload(summary_payload: Mapping[str, object]) -> JsonObject:
    payload_obj = to_json_object(summary_payload)
    payload_obj.pop("liveView", None)
    payload_obj["liveView"] = json_serialize(build_live_view_snapshot(payload_obj))
    return payload_obj


def _infer_mode(summary: Mapping[str, JsonValue]) -> LiveViewMode:
    candidates: list[JsonValue | None] = [
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


def _normalize_mode_value(candidate: JsonValue | None) -> LiveViewMode | None:
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


def _derive_progress(summary: Mapping[str, JsonValue], mode: LiveViewMode) -> LiveViewProgress | None:
    if not summary:
        return None

    games_raw = summary.get("games")
    if not isinstance(games_raw, Mapping):
        return None
    games: dict[str, JsonValue] = {str(key): json_serialize(value) for key, value in games_raw.items()}

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
    _is_finished = coerce_bool(_raw_finished)
    fallback_state = "finished" if _is_finished else "normal"
    progress_state = _normalize_progress_state(summary.get("liveViewProgressState")) or fallback_state
    is_final = _is_finished
    if mode == "sprt":
        status_raw = summary.get("status")
        if isinstance(status_raw, Mapping):
            status = {str(key): value for key, value in status_raw.items()}
            decision = status.get("decision")
            if isinstance(decision, str) and decision and decision.lower() != "continue":
                is_final = True
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


def _normalize_progress_state(candidate: JsonValue | None) -> str | None:
    if not isinstance(candidate, str):
        return None
    value = candidate.strip().lower()
    if value in {"normal", "paused", "draining", "finished"}:
        return value
    return None


__all__ = ["attach_live_view_payload", "build_live_view_snapshot"]
