"""Snapshot storage and delta computation for Arena Dashboard.

Handles storage and differential updates for summary and games snapshots.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from shogiarena._core.contexts.dashboard.application.events import GamesSnapshotPayload
from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import attach_live_view_payload
from shogiarena._core.contexts.dashboard.application.snapshot_delta import (
    compute_games_delta as compute_games_delta_from_context,
)
from shogiarena._core.contexts.dashboard.application.snapshot_delta import (
    extract_summary_diff as extract_summary_diff_from_context,
)
from shogiarena._core.contexts.dashboard.application.snapshot_delta import (
    minimise_patch as minimise_patch_from_context,
)
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import (
    SnapshotPayloadParserFn,
    SnapshotPayloadSerializerFn,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.json_coercion import (
    is_str_object_mapping as is_context_str_object_mapping,
)
from shogiarena._core.shared.kernel.json_coercion import (
    to_json_object as to_json_object_from_context,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import is_strict_numeric

logger = logging.getLogger(__name__)


class SnapshotStorage:
    """Manages storage and delta computation for dashboard snapshots.

    Handles summary and games snapshots with validation, sanitization, and
    differential update computation.

    Args:
        state: Shared dashboard state container.
        run_dir: Runtime directory for the session.
        parse_snapshot: Boundary parser for dashboard snapshot payloads.
        serialize_snapshot: Boundary serializer for dashboard snapshot payloads.
    """

    def __init__(
        self,
        state: DashboardState,
        run_dir: Path,
        *,
        parse_snapshot: SnapshotPayloadParserFn,
        serialize_snapshot: SnapshotPayloadSerializerFn,
    ) -> None:
        self._state = state
        self._run_dir = run_dir
        self._parse_snapshot = parse_snapshot
        self._serialize_snapshot = serialize_snapshot

    def store_summary(
        self,
        payload: Mapping[str, JsonValue],
        *,
        source: str,  # I/O boundary: 外部ペイロード受信
    ) -> JsonObject | None:
        """Store a summary snapshot with validation and sanitization.

        Args:
            payload: Raw summary payload to store.
            source: Summary source key to store under.

        Returns:
            The stored snapshot or None if validation failed.

        """
        try:
            raw_snapshot = to_json_object_from_context(payload)
            if not is_context_str_object_mapping(raw_snapshot):
                logger.warning("Summary snapshot payload must be an object")
                return None
            parsed_snapshot = self._parse_snapshot(
                "summary",
                raw_snapshot,
                path=f"summary_snapshot[{source}]",
            )
        except (TypeError, ValueError, ContractParseError):
            logger.warning("Failed to serialise summary snapshot", exc_info=True)
            return None
        snapshot = parsed_snapshot

        _is_number = is_strict_numeric

        # Unified validation: all modes must provide a valid 'games' object.
        games_raw = snapshot.get("games")
        if not is_context_str_object_mapping(games_raw):
            logger.warning("Summary snapshot missing games payload for source=%s", source)
            return None
        games = to_json_object_from_context(games_raw)
        if not _is_number(games.get("completed")) or not _is_number(games.get("total")):
            logger.warning("Summary games payload missing completed/total for source=%s", source)
            return None

        # Sanitize enginesMeta to ensure each entry has a valid name.
        engines_meta = snapshot.get("enginesMeta")
        if engines_meta is not None:
            if isinstance(engines_meta, list):
                before = len(engines_meta)
                sanitized: list[JsonObject] = []
                for entry in engines_meta:
                    if not is_context_str_object_mapping(entry):
                        continue
                    entry_map = to_json_object_from_context(entry)
                    name_raw = entry_map.get("name")
                    if isinstance(name_raw, str):
                        name = name_raw.strip()
                    elif name_raw is None:
                        fallback = entry_map.get("engine") or entry_map.get("id")
                        name = str(fallback).strip() if fallback is not None else ""
                    else:
                        name = str(name_raw).strip()
                    if not name:
                        continue
                    coerced = dict(entry_map)
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
        if "is_summary_ready" not in snapshot and "summaryReady" in snapshot:
            snapshot["is_summary_ready"] = snapshot.pop("summaryReady")
        snapshot.setdefault("is_summary_ready", True)
        snapshot = attach_live_view_payload(snapshot)
        try:
            sanitised = self._serialize_snapshot(
                "summary",
                snapshot,
                path=f"summary_snapshot[{source}]",
            )
        except (TypeError, ValueError, ContractParseError):
            logger.warning("Failed to serialise summary snapshot", exc_info=True)
            return None
        self._state.set_summary_snapshot(source, sanitised)
        return sanitised

    @staticmethod
    def extract_summary_diff(
        previous: Mapping[str, JsonValue] | None,
        current: Mapping[str, JsonValue],
    ) -> JsonObject:
        """Compute differential update between two summary snapshots.

        Args:
            previous: Previous snapshot state.
            current: Current snapshot state.

        Returns:
            Dict containing only changed fields.
        """
        return extract_summary_diff_from_context(previous, current)

    def store_games(self, payload: Mapping[str, JsonValue]) -> GamesSnapshotPayload | None:
        """Store a games list snapshot.

        Args:
            payload: Raw games payload to store.

        Returns:
            The stored snapshot or None if validation failed.
        """
        try:
            raw_snapshot = to_json_object_from_context(payload)
            if not is_context_str_object_mapping(raw_snapshot):
                logger.warning("Games snapshot payload must be an object")
                return None
            parsed_snapshot = self._parse_snapshot(
                "games",
                raw_snapshot,
                path="games_snapshot",
            )
        except (TypeError, ValueError, ContractParseError):
            logger.warning("Failed to serialise games snapshot", exc_info=True)
            return None
        if not isinstance(parsed_snapshot, dict):
            logger.warning("Games snapshot payload must be a JSON object")
            return None
        kind_value = parsed_snapshot.get("kind")
        revision_value = parsed_snapshot.get("revision")
        base_revision_value = parsed_snapshot.get("base_revision")
        rows_value = parsed_snapshot.get("rows")
        snapshot_meta_value = parsed_snapshot.get("snapshotMeta")
        if not isinstance(kind_value, str):
            logger.warning("Games snapshot missing kind field")
            return None
        if not isinstance(revision_value, int):
            logger.warning("Games snapshot missing revision field")
            return None
        if base_revision_value is not None and not isinstance(base_revision_value, int):
            logger.warning("Games snapshot base_revision must be null or int")
            return None
        if not isinstance(rows_value, list):
            logger.warning("Games snapshot rows must be a list")
            return None
        rows: list[JsonObject] = []
        for index, row in enumerate(rows_value):
            if not is_context_str_object_mapping(row):
                logger.warning("Games snapshot row must be an object: index=%s", index)
                return None
            rows.append(to_json_object_from_context(row))
        snapshot_meta: JsonObject = (
            to_json_object_from_context(snapshot_meta_value)
            if is_context_str_object_mapping(snapshot_meta_value)
            else {}
        )
        snapshot: GamesSnapshotPayload = {
            "kind": kind_value,
            "revision": revision_value,
            "base_revision": base_revision_value,
            "rows": rows,
            "snapshotMeta": snapshot_meta,
        }
        try:
            sanitised = self._serialize_snapshot("games", snapshot, path="games_snapshot")
        except (TypeError, ValueError, ContractParseError):
            logger.warning("Failed to serialise games snapshot", exc_info=True)
            return None
        sanitized_kind = sanitised.get("kind")
        sanitized_revision = sanitised.get("revision")
        sanitized_base_revision = sanitised.get("base_revision")
        sanitized_rows = sanitised.get("rows")
        sanitized_meta = sanitised.get("snapshotMeta")
        if not isinstance(sanitized_kind, str):
            logger.warning("Serialized games snapshot missing kind field")
            return None
        if not isinstance(sanitized_revision, int):
            logger.warning("Serialized games snapshot missing revision field")
            return None
        if sanitized_base_revision is not None and not isinstance(sanitized_base_revision, int):
            logger.warning("Serialized games snapshot base_revision must be null or int")
            return None
        if not isinstance(sanitized_rows, list):
            logger.warning("Serialized games snapshot rows must be a list")
            return None
        normalized_rows: list[JsonObject] = []
        for index, row in enumerate(sanitized_rows):
            if not is_context_str_object_mapping(row):
                logger.warning("Serialized games snapshot row must be an object: index=%s", index)
                return None
            normalized_rows.append(to_json_object_from_context(row))
        normalized_meta = (
            to_json_object_from_context(sanitized_meta) if is_context_str_object_mapping(sanitized_meta) else {}
        )
        normalized_snapshot: GamesSnapshotPayload = {
            "kind": sanitized_kind,
            "revision": sanitized_revision,
            "base_revision": sanitized_base_revision,
            "rows": normalized_rows,
            "snapshotMeta": normalized_meta,
        }
        self._state.set_games_snapshot(normalized_snapshot)
        return normalized_snapshot

    def compute_games_delta(
        self,
        previous: Mapping[str, JsonValue] | None,
        rows: Sequence[Mapping[str, JsonValue]],
        snapshot_meta: Mapping[str, JsonValue],
        *,
        revision: int,
        base_revision: int | None,
    ) -> GamesSnapshotPayload:
        """Compute differential update for games list.

        Args:
            previous: Previous games snapshot.
            rows: Current list of game rows.
            snapshot_meta: Metadata for the snapshot.

        Returns:
            Delta envelope with add/update/remove operations.
        """
        return compute_games_delta_from_context(
            previous,
            rows,
            snapshot_meta,
            revision=revision,
            base_revision=base_revision,
        )

    @staticmethod
    def minimise_patch(payload: Mapping[str, JsonValue]) -> JsonObject:
        """Remove None values from a patch payload.

        Args:
            payload: Raw patch payload.

        Returns:
            Filtered payload without None values.
        """
        return minimise_patch_from_context(payload)
