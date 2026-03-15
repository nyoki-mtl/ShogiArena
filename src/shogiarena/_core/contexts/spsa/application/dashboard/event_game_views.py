"""Pure event-view helpers for SPSA dashboard projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.scalar_coercion.api import timestamp_to_iso


def build_game_event_snapshot(events: Sequence[Mapping[str, object]], game_id: str) -> dict[str, object] | None:
    """Return latest game_result payload for the given game ID."""

    normalized = game_id.strip()
    if not normalized:
        return None
    for event in reversed(events):
        if event.get("event") != "game_result":
            continue
        gid = str(event.get("game_id") or "").strip()
        if gid != normalized:
            continue
        payload = dict(event)
        payload.setdefault("black_player", payload.get("black_engine"))
        payload.setdefault("white_player", payload.get("white_engine"))
        payload.setdefault("num_moves", payload.get("moves_count"))
        if "end_time" not in payload:
            ts_val = payload.get("ts")
            end_iso = timestamp_to_iso(ts_val)
            payload["end_time"] = end_iso or ts_val
        return payload
    return None


def collect_game_id_entries(events: Sequence[Mapping[str, object]]) -> list[tuple[str, int]]:
    """Collect distinct game IDs with their latest timestamps."""

    latest: dict[str, int] = {}
    for event in events:
        if event.get("event") != "game_result":
            continue
        gid = str(event.get("game_id") or "").strip()
        if not gid:
            continue
        ts = event.get("ts")
        ts_value = int(ts) if isinstance(ts, int | float) else 0
        prev = latest.get(gid)
        if prev is None or ts_value > prev:
            latest[gid] = ts_value
    return sorted(latest.items(), key=lambda item: item[1], reverse=True)


__all__ = [
    "build_game_event_snapshot",
    "collect_game_id_entries",
]
