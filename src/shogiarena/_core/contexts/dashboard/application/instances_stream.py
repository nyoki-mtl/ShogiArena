"""Streaming and snapshot projection helpers for instances API."""

from __future__ import annotations

import asyncio
import json

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_optional_text


async def drain_updates(update_queue: asyncio.Queue[JsonObject]) -> list[JsonObject]:
    """Drain queued instance updates without blocking."""

    updates: list[JsonObject] = []
    while True:
        try:
            updates.append(update_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return updates


def build_instances_delta(snapshot: JsonObject, updates: list[JsonObject]) -> JsonObject | None:
    """Build compact delta payload from buffered update notifications."""

    if not updates:
        return None
    upsert_ids: set[str] = set()
    removed_ids: set[str] = set()
    should_send_full = False
    for update in updates:
        kind = update.get("kind")
        ids_raw = update.get("instance_ids")
        ids: list[str]
        if isinstance(ids_raw, list):
            ids = []
            for item in ids_raw:
                normalized = coerce_optional_text(item)
                if normalized:
                    ids.append(normalized)
        else:
            ids = []
        if kind == "full":
            should_send_full = True
        elif kind == "remove":
            removed_ids.update(ids)
        else:
            upsert_ids.update(ids)

    if should_send_full:
        return {
            "instances": snapshot.get("instances", []),
            "stats": snapshot.get("stats", {}),
            "timestamp": snapshot.get("timestamp"),
        }

    instances = snapshot.get("instances", [])
    if not isinstance(instances, list):
        raise TypeError("instances snapshot must be a list")
    updated = []
    for entry in instances:
        if not isinstance(entry, dict):
            continue
        entry_id = dict(entry).get("id")
        if isinstance(entry_id, str) and entry_id in upsert_ids:
            updated.append(entry)

    return {
        "instances": updated,
        "removed": sorted(removed_ids),
        "stats": snapshot.get("stats", {}),
        "timestamp": snapshot.get("timestamp"),
    }


def signature_for_instances_snapshot(snapshot: JsonObject) -> str:
    """Compute stable signature for polling-based SSE change detection."""

    pruned = dict(snapshot)
    pruned.pop("timestamp", None)
    instances = pruned.get("instances")
    if isinstance(instances, list):
        normalized_instances = []
        for entry in instances:
            if not isinstance(entry, dict):
                normalized_instances.append(entry)
                continue
            inst_copy = dict(entry)
            metrics = inst_copy.get("metrics")
            if isinstance(metrics, dict):
                metrics_copy = dict(metrics)
                metrics_copy.pop("timestamp", None)
                inst_copy["metrics"] = metrics_copy
            normalized_instances.append(inst_copy)
        pruned["instances"] = normalized_instances
    return json.dumps(pruned, sort_keys=True, ensure_ascii=False)


__all__ = [
    "build_instances_delta",
    "drain_updates",
    "signature_for_instances_snapshot",
]
