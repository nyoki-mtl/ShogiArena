"""Snapshot storage and delta computation for Arena Dashboard.

Handles storage and differential updates for summary and games snapshots.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shogiarena.utils.types.coerce import is_strict_numeric
from shogiarena.web.dashboard.backend.live.schema import build_live_view_snapshot
from shogiarena.web.dashboard.backend.live.types import LiveViewSnapshot
from shogiarena.web.dashboard.backend.types import GamesSnapshotPayload, SnapshotMeta

if TYPE_CHECKING:
    from shogiarena.web.dashboard.backend.state_container import DashboardState

logger = logging.getLogger(__name__)


class SnapshotStorage:
    """Manages storage and delta computation for dashboard snapshots.

    Handles summary and games snapshots with validation, sanitization, and
    differential update computation.

    Args:
        state: Shared dashboard state container.
        run_dir: Runtime directory for the session.
    """

    def __init__(
        self,
        state: DashboardState,
        run_dir: Path,
    ) -> None:
        self._state = state
        self._run_dir = run_dir

    def build_live_view(
        self,
        *,
        summary_snapshot: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> LiveViewSnapshot:
        """Construct the Live View envelope.

        Args:
            summary_snapshot: Optional summary snapshot override.
            source: Optional summary source key to pull from shared state.

        Returns:
            Live view envelope dict.
        """
        summary_source: dict[str, Any]
        if summary_snapshot is not None:
            summary_source = dict(summary_snapshot)
        elif source is not None:
            summary_source = dict(self._state.summary_snapshots.get(source, {}))
        else:
            summary_source = {}
        summary_source.pop("liveView", None)

        return build_live_view_snapshot(summary_source)

    def store_summary(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,  # I/O boundary: 外部ペイロード受信
    ) -> dict[str, Any] | None:
        """Store a summary snapshot with validation and sanitization.

        Args:
            payload: Raw summary payload to store.
            source: Summary source key to store under.

        Returns:
            The stored snapshot or None if validation failed.

        """
        try:
            raw_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.warning("Failed to serialise summary snapshot", exc_info=True)
            return None
        if not isinstance(raw_snapshot, dict):
            logger.warning("Summary snapshot payload must be a JSON object")
            return None
        snapshot = cast(dict[str, Any], raw_snapshot)

        _is_number = is_strict_numeric

        # Unified validation: all modes must provide a valid 'games' object.
        games = snapshot.get("games")
        if not isinstance(games, Mapping):
            logger.warning("Summary snapshot missing games payload for source=%s", source)
            return None
        if not _is_number(games.get("completed")) or not _is_number(games.get("total")):
            logger.warning("Summary games payload missing completed/total for source=%s", source)
            return None

        # Sanitize enginesMeta to ensure each entry has a valid name.
        engines_meta = snapshot.get("enginesMeta")
        if engines_meta is not None:
            if isinstance(engines_meta, list):
                before = len(engines_meta)
                sanitized: list[dict[str, Any]] = []
                for entry in engines_meta:
                    if not isinstance(entry, dict):
                        continue
                    name_raw = entry.get("name")
                    if isinstance(name_raw, str):
                        name = name_raw.strip()
                    elif name_raw is None:
                        fallback = entry.get("engine") or entry.get("id")
                        name = str(fallback).strip() if fallback is not None else ""
                    else:
                        name = str(name_raw).strip()
                    if not name:
                        continue
                    coerced = dict(entry)
                    coerced["name"] = name
                    sanitized.append(coerced)
                if len(sanitized) != before:
                    logger.warning(
                        "Sanitized summary enginesMeta entries (before=%s after=%s)",
                        before,
                        len(sanitized),
                    )
                snapshot["enginesMeta"] = sanitized
            else:
                logger.warning(
                    "Summary enginesMeta must be an array; dropping invalid value type=%s",
                    type(engines_meta),
                )
                snapshot.pop("enginesMeta", None)

        snapshot.pop("liveView", None)
        snapshot.setdefault("summaryReady", True)
        snapshot["liveView"] = self.build_live_view(summary_snapshot=snapshot, source=source)
        self._state.summary_snapshots[source] = snapshot
        return snapshot

    @staticmethod
    def extract_summary_diff(
        previous: Mapping[str, Any] | None,
        current: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Compute differential update between two summary snapshots.

        Args:
            previous: Previous snapshot state.
            current: Current snapshot state.

        Returns:
            Dict containing only changed fields.
        """
        if previous is None:
            return dict(current)
        diff: dict[str, Any] = {}
        for key, value in current.items():
            if previous.get(key) != value:
                diff[key] = value
        return diff

    def store_games(self, payload: Mapping[str, Any]) -> GamesSnapshotPayload | None:
        """Store a games list snapshot.

        Args:
            payload: Raw games payload to store.

        Returns:
            The stored snapshot or None if validation failed.
        """
        try:
            raw_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.warning("Failed to serialise games snapshot", exc_info=True)
            return None
        if not isinstance(raw_snapshot, dict):
            logger.warning("Games snapshot payload must be a JSON object")
            return None
        snapshot = cast(GamesSnapshotPayload, raw_snapshot)
        self._state.games_snapshot = snapshot
        return snapshot

    def compute_games_delta(
        self,
        previous: Mapping[str, Any] | None,
        rows: list[Mapping[str, Any]],
        snapshot_meta: Mapping[str, Any],
    ) -> GamesSnapshotPayload:
        """Compute differential update for games list.

        Args:
            previous: Previous games snapshot.
            rows: Current list of game rows.
            snapshot_meta: Metadata for the snapshot.

        Returns:
            Delta envelope with add/update/remove operations.
        """
        prev_rows = previous.get("rows") if isinstance(previous, Mapping) else None
        prev_map: dict[str, Mapping[str, Any]] = {}
        if isinstance(prev_rows, list):
            for row in prev_rows:
                if not isinstance(row, Mapping):
                    continue
                key = self._resolve_game_row_key(row)
                if key:
                    prev_map[key] = row

        curr_map: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            key = self._resolve_game_row_key(row)
            if key:
                curr_map[key] = row

        updates: list[dict[str, Any]] = []
        for key, row in curr_map.items():
            if key not in prev_map:
                updates.append({"op": "add", "row": row})
            elif prev_map[key] != row:
                updates.append({"op": "update", "row": row})

        removed = [{"op": "remove", "id": key} for key in prev_map.keys() if key not in curr_map]

        return {
            "type": "delta",
            "rows": updates + removed,
            "snapshotMeta": cast(SnapshotMeta, dict(snapshot_meta)),
        }

    @staticmethod
    def _resolve_game_row_key(row: Mapping[str, Any]) -> str | None:
        """Extract a unique key from a game row.

        Args:
            row: Game row mapping.

        Returns:
            String key or None if not found.
        """
        for key in ("game_id", "gameId", "id", "order", "display_order"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int):
                return str(value)
        return None

    @staticmethod
    def minimise_patch(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Remove None values from a patch payload.

        Args:
            payload: Raw patch payload.

        Returns:
            Filtered payload without None values.
        """
        return {k: v for k, v in payload.items() if v is not None}
