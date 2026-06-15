"""Schedule snapshot builder from games snapshot data."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject


def build_schedule_snapshot_from_games_snapshot(snapshot: Mapping[str, object]) -> JsonObject | None:
    """Build ``/api/schedule`` payload from a stored games snapshot when valid."""

    if snapshot.get("kind") != "bulk":
        return None
    revision = snapshot.get("revision")
    rows = snapshot.get("rows")
    snapshot_meta = snapshot.get("snapshot_meta")

    if not isinstance(revision, int) or revision < 0:
        return None
    if not isinstance(rows, list):
        return None
    if not is_str_object_mapping(snapshot_meta):
        return None

    normalized_rows: list[JsonObject] = []
    for row in rows:
        if not is_str_object_mapping(row):
            return None
        normalized_rows.append(to_json_object(row))

    payload = to_json_object(snapshot_meta)
    payload["schedule"] = normalized_rows
    payload["revision"] = revision
    return payload
