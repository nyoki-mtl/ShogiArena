"""SPSA update query service for dashboard event aggregation and progress tracking."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeGuard, cast

from shogiarena._core.contexts.dashboard.application.spsa.ltc_regression_detail_codec import parse_ltc_regression_detail
from shogiarena._core.contexts.dashboard.application.spsa.variant_resolution import format_variant_label
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    ProgressSnapshot,
    UpdateDetailResponse,
    UpdateEntry,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.best_params_snapshot import (
    build_best_params_snapshot_payload,
)
from shogiarena._core.contexts.spsa.application.dashboard.event_game_views import (
    build_game_event_snapshot,
)
from shogiarena._core.contexts.spsa.application.dashboard.event_game_views import (
    collect_game_id_entries as collect_game_id_entries_from_events,
)
from shogiarena._core.contexts.spsa.application.dashboard.event_updates import build_update_detail_state
from shogiarena._core.contexts.spsa.application.dashboard.event_updates_collection import (
    collect_updates_from_events as collect_spsa_updates_from_events,
)
from shogiarena._core.contexts.spsa.application.dashboard.index_update_merge import (
    merge_index_updates,
)
from shogiarena._core.contexts.spsa.application.dashboard.progress_snapshot import (
    build_progress_snapshot,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_non_negative_int

from .update_detail_builder import SpsaUpdateDetailBuilder

logger = logging.getLogger(__name__)


class SpsaUpdateQueryService:
    """Query SPSA update data, event snapshots, and progress tracking.

    Owns update listing, detail hydration, game event snapshots,
    and progress computation for the dashboard.
    """

    def __init__(
        self,
        *,
        store: DashboardSpsaStorePort,
        db_path: Path,
    ) -> None:
        self._store = store
        self._update_detail_builder = SpsaUpdateDetailBuilder(
            store=store,
            db_path=db_path,
            game_event_snapshot_loader=self.get_game_event_snapshot,
        )

    @staticmethod
    def _is_update_entry_payload(value: Mapping[str, JsonValue]) -> TypeGuard[UpdateEntry]:
        return isinstance(value.get("update_idx"), int)

    def build_update_detail(self, idx: int) -> UpdateDetailResponse:
        """Build detailed information about a specific SPSA update."""
        events = self._store.load_event_entries()
        if not events:
            raise ValueError("no events")

        normalized_events = [to_json_object(event) for event in events]
        detail_state = build_update_detail_state(normalized_events, idx=idx)
        return self._update_detail_builder.build(idx=idx, detail_state=detail_state)

    def get_game_event_snapshot(self, game_id: str) -> JsonObject | None:
        """Return the most recent event payload for the given game."""

        events = self._store.load_event_entries()
        if not events:
            return None
        snapshot = build_game_event_snapshot(events, game_id)
        return to_json_object(snapshot) if snapshot is not None else None

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        """Collect game IDs with their latest timestamps from the event log."""

        events = self._store.load_event_entries()
        if not events:
            return []
        return collect_game_id_entries_from_events(events)

    def load_index_updates(self) -> list[UpdateEntry]:
        """Merge index.json updates with event log enrichment."""

        index_updates = [entry for entry in self._store.load_index_updates() if isinstance(entry, dict)]
        if not index_updates:
            return []

        event_updates = self.collect_updates_from_events()
        merged = merge_index_updates(
            index_updates=index_updates,
            event_updates=event_updates,
            format_variant_label=format_variant_label,
            parse_ltc_regression_detail=parse_ltc_regression_detail,
            normalize_non_negative_idx=coerce_non_negative_int,
        )
        normalized_entries: list[UpdateEntry] = []
        for entry in merged:
            normalized = to_json_object(entry)
            if self._is_update_entry_payload(normalized):
                normalized_entries.append(normalized)
        return normalized_entries

    def collect_updates_from_events(self) -> list[UpdateEntry]:
        """Aggregate update entries from events.jsonl."""

        events = self._store.load_event_entries()
        if not events:
            return []
        normalized_events = [to_json_object(event) for event in events]
        enriched = collect_spsa_updates_from_events(
            normalized_events,
            persist_best_params_snapshot=self._persist_best_params_snapshot,
        )
        normalized_entries: list[UpdateEntry] = []
        for entry in enriched:
            normalized = to_json_object(entry)
            if self._is_update_entry_payload(normalized):
                normalized_entries.append(normalized)
        return normalized_entries

    def compute_progress_snapshot(self, updates: Sequence[UpdateEntry] | None = None) -> ProgressSnapshot:
        """Return completed update count and configured total updates."""

        if updates is None:
            updates = self.load_index_updates()

        total_updates = None
        try:
            meta = self._store.load_meta_data()
        except OSError:
            meta = None
        if meta is not None:
            total_updates = meta.effective_num_updates

        snapshot = build_progress_snapshot(
            completed_updates=len(updates),
            total_updates=total_updates,
        )
        return cast(ProgressSnapshot, snapshot)

    def _persist_best_params_snapshot(
        self,
        entry: Mapping[str, JsonValue],
        ltc_entry: Mapping[str, JsonValue],
    ) -> None:
        ltc_payload = to_json_object(ltc_entry)
        snapshot_payload = build_best_params_snapshot_payload(
            entry=entry,
            ltc_entry=ltc_payload,
            format_variant_label=format_variant_label,
        )
        if snapshot_payload is None:
            return
        self._store.save_best_params_snapshot(
            variant_token=snapshot_payload.variant_token,
            update_idx=snapshot_payload.update_idx,
            params=snapshot_payload.params,
            metadata=snapshot_payload.metadata,
        )
