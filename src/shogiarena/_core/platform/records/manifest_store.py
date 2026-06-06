"""Manifest read/write helpers for binary record outputs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)


def load_manifest(path: Path) -> JsonObject:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load manifest from %s: %s", path, exc)
        return {}
    if not isinstance(data, Mapping):
        return {}
    return to_json_object(data)


def append_manifest_entry(
    path: Path,
    *,
    format_id: str,
    file_prefix: str,
    entry: JsonObject,
) -> None:
    manifest = load_manifest(path)
    files: list[JsonObject] = []
    files_raw = manifest.get("files")
    if isinstance(files_raw, list):
        for item in files_raw:
            if isinstance(item, Mapping):
                files.append(to_json_object(item))
    files.append(entry)
    payload: JsonObject = {
        "format": format_id,
        "file_prefix": file_prefix,
        "updated_at_iso": datetime.now(UTC).isoformat(),
        "files": files,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_manifest(path: Path, payload: JsonObject) -> None:
    """Write the records manifest atomically."""

    write_json_atomic(path, payload)


def summarize_manifest_totals(manifest: Mapping[str, JsonValue]) -> tuple[int, int, int, int]:
    files_raw = manifest.get("files")
    if not isinstance(files_raw, list):
        return (0, 0, 0, 0)
    total_games = 0
    total_positions = 0
    total_bytes = 0
    file_count = 0
    for entry in files_raw:
        if not isinstance(entry, Mapping):
            continue
        file_count += 1
        entry_map = to_json_object(entry)
        games = entry_map.get("games")
        positions = entry_map.get("positions")
        bytes_count = entry_map.get("bytes")
        if isinstance(games, int):
            total_games += games
        if isinstance(positions, int):
            total_positions += positions
        if isinstance(bytes_count, int):
            total_bytes += bytes_count
    return (total_games, total_positions, total_bytes, file_count)


__all__ = ["append_manifest_entry", "load_manifest", "summarize_manifest_totals", "write_manifest"]
