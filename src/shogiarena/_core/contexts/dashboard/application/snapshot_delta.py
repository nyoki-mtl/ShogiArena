"""Pure transformation/delta helpers for dashboard snapshot processing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .events import GamesSnapshotPayload


def extract_summary_diff(
    previous: Mapping[str, JsonValue] | None,
    current: Mapping[str, JsonValue],
) -> JsonObject:
    """Compute a field-level difference between snapshots."""
    if previous is None:
        return {str(key): value for key, value in current.items()}

    diff: JsonObject = {}
    for key, value in current.items():
        if previous.get(key) != value:
            diff[str(key)] = value
    return diff


def _resolve_game_row_key(row: Mapping[str, JsonValue]) -> str | None:
    """Extract a deterministic row identifier from a game row."""
    for key in ("game_id", "game_id", "id", "order", "display_order"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def compute_games_delta(
    previous: Mapping[str, JsonValue] | None,
    rows: Sequence[Mapping[str, JsonValue]],
    snapshot_meta: Mapping[str, JsonValue],
    *,
    revision: int,
    base_revision: int | None,
) -> GamesSnapshotPayload:
    """Build an additive/update/remove delta payload from two game-row snapshots."""
    prev_rows = previous.get("rows") if previous is not None else None
    prev_map: dict[str, JsonObject] = {}
    if isinstance(prev_rows, list):
        for row in prev_rows:
            if not is_str_object_mapping(row):
                continue
            row_map = to_json_object(row)
            key = _resolve_game_row_key(row_map)
            if key:
                prev_map[key] = row_map

    curr_map: dict[str, JsonObject] = {}
    for row in rows:
        if not is_str_object_mapping(row):
            continue
        row_map = to_json_object(row)
        key = _resolve_game_row_key(row_map)
        if key:
            curr_map[key] = row_map

    updates: list[JsonObject] = []
    for key, row in curr_map.items():
        if key not in prev_map:
            updates.append({"op": "add", "row": row})
        elif prev_map[key] != row:
            updates.append({"op": "update", "row": row})

    removed: list[JsonObject] = [{"op": "remove", "id": key} for key in prev_map.keys() if key not in curr_map]

    return {
        "kind": "delta",
        "revision": int(revision),
        "base_revision": int(base_revision) if base_revision is not None else None,
        "rows": updates + removed,
        "snapshot_meta": to_json_object(snapshot_meta),
    }


def minimise_patch(payload: Mapping[str, JsonValue]) -> JsonObject:
    """Filter patch payload keys whose value is None."""
    return {str(key): value for key, value in payload.items() if value is not None}
