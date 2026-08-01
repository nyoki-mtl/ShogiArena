"""SPSA update query service for dashboard event aggregation and progress tracking."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeGuard, cast

from shogiarena._core.contexts.dashboard.application.spsa.ltc_regression_detail_codec import parse_ltc_regression_detail
from shogiarena._core.contexts.dashboard.application.spsa.variant_resolution import format_variant_label
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    ProgressSnapshot,
    UpdateDetailResponse,
    UpdateEntry,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaStorePort,
    SpsaUpdateNotFoundError,
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

LedgerUpdateLoader = Callable[[int], JsonObject | None]
LedgerUpdatesLoader = Callable[[], list[JsonObject]]
LedgerEventsLoader = Callable[[], list[JsonObject]]
LedgerGameSnapshotLoader = Callable[[str], JsonObject | None]
LedgerGameSnapshotsLoader = Callable[[Sequence[str]], dict[str, JsonObject]]
LedgerGameEntriesLoader = Callable[[], list[tuple[str, int]]]
LedgerLtcResultsLoader = Callable[[], list[JsonObject]]
LedgerRevisionLoader = Callable[[], tuple[str, int, bool]]
GameBatchLoader = Callable[[list[str]], dict[str, JsonObject]]


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
        read_only: bool = False,
        ledger_update_loader: LedgerUpdateLoader | None = None,
        ledger_updates_loader: LedgerUpdatesLoader | None = None,
        ledger_events_loader: LedgerEventsLoader | None = None,
        ledger_game_snapshot_loader: LedgerGameSnapshotLoader | None = None,
        ledger_game_snapshots_loader: LedgerGameSnapshotsLoader | None = None,
        ledger_game_entries_loader: LedgerGameEntriesLoader | None = None,
        ledger_ltc_results_loader: LedgerLtcResultsLoader | None = None,
        ledger_revision_loader: LedgerRevisionLoader | None = None,
        game_batch_loader: GameBatchLoader | None = None,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._ledger_update_loader = ledger_update_loader
        self._ledger_updates_loader = ledger_updates_loader
        self._ledger_events_loader = ledger_events_loader
        self._ledger_game_snapshot_loader = ledger_game_snapshot_loader
        self._ledger_game_snapshots_loader = ledger_game_snapshots_loader
        self._ledger_game_entries_loader = ledger_game_entries_loader
        self._ledger_ltc_results_loader = ledger_ltc_results_loader
        self._ledger_revision_loader = ledger_revision_loader
        self._close_callback = close_callback
        self._update_detail_builder = SpsaUpdateDetailBuilder(
            store=store,
            db_path=db_path,
            read_only=read_only,
            game_batch_loader=game_batch_loader,
            game_event_snapshot_loader=self.get_game_event_snapshot,
        )

    @staticmethod
    def _is_update_entry_payload(value: Mapping[str, JsonValue]) -> TypeGuard[UpdateEntry]:
        return isinstance(value.get("update_idx"), int)

    def build_update_detail(self, idx: int) -> UpdateDetailResponse:
        """Build detailed information about a specific SPSA update."""
        ledger_state = self._ledger_update_loader(idx) if self._ledger_update_loader is not None else None
        if self._ledger_update_loader is not None and ledger_state is None:
            raise SpsaUpdateNotFoundError(f"SPSA update {idx} is not planned in the ledger")
        events = [] if ledger_state is not None else self.load_event_entries()
        if not events and ledger_state is None:
            raise ValueError("no events")

        normalized_events = [to_json_object(event) for event in events]
        detail_state = build_update_detail_state(normalized_events, idx=idx) if normalized_events else {}
        if ledger_state is not None:
            detail_state.update(ledger_state)
        detail_state["session_uuids"] = sorted(
            {
                session_uuid
                for event in normalized_events
                if coerce_non_negative_int(event.get("update_idx")) == idx
                and isinstance((session_uuid := event.get("session_uuid")), str)
                and session_uuid
            }
        )
        return self._update_detail_builder.build(idx=idx, detail_state=detail_state)

    def get_game_event_snapshot(self, game_id: str) -> JsonObject | None:
        """Return the most recent event payload for the given game."""

        if self._ledger_game_snapshot_loader is not None:
            return self._ledger_game_snapshot_loader(game_id)
        events = self.load_event_entries()
        if not events:
            return None
        snapshot = build_game_event_snapshot(events, game_id)
        return to_json_object(snapshot) if snapshot is not None else None

    def get_game_event_snapshots(self, game_ids: Sequence[str]) -> dict[str, JsonObject]:
        """Return game snapshots in one canonical projector refresh."""

        requested = list(dict.fromkeys(game_ids))
        if self._ledger_game_snapshots_loader is not None:
            return self._ledger_game_snapshots_loader(requested)
        return {
            game_id: snapshot
            for game_id in requested
            if (snapshot := self.get_game_event_snapshot(game_id)) is not None
        }

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        """Collect game IDs with their latest timestamps from the event log."""

        if self._ledger_game_entries_loader is not None:
            return self._ledger_game_entries_loader()
        events = self.load_event_entries()
        if not events:
            return []
        return collect_game_id_entries_from_events(events)

    def load_index_updates(self) -> list[UpdateEntry]:
        """Merge index.json updates with event log enrichment."""

        if self._ledger_updates_loader is not None:
            canonical_entries: list[UpdateEntry] = []
            for entry in self._ledger_updates_loader():
                if self._is_update_entry_payload(entry):
                    canonical_entries.append(entry)
            return canonical_entries
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

        if self._ledger_updates_loader is not None:
            return self.load_index_updates()
        events = self.load_event_entries()
        if not events:
            return []
        normalized_events = [to_json_object(event) for event in events]
        enriched = collect_spsa_updates_from_events(
            normalized_events,
        )
        normalized_entries: list[UpdateEntry] = []
        for entry in enriched:
            normalized = to_json_object(entry)
            if self._is_update_entry_payload(normalized):
                normalized_entries.append(normalized)
        return normalized_entries

    def load_event_entries(self) -> list[JsonObject]:
        """Load canonical ledger events when a projector is configured."""

        if self._ledger_events_loader is not None:
            return self._ledger_events_loader()
        return [to_json_object(event) for event in self._store.load_event_entries()]

    def load_ltc_results(self) -> list[JsonObject]:
        """Load canonical ledger LTC decisions when available."""

        if self._ledger_ltc_results_loader is not None:
            return self._ledger_ltc_results_loader()
        return [to_json_object(result) for result in self._store.load_ltc_results()]

    def load_revision_state(self) -> tuple[str, int, bool] | None:
        """Return durable revision state when this run has a ledger projector."""

        if self._ledger_revision_loader is None:
            return None
        return self._ledger_revision_loader()

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

    def close(self) -> None:
        """Release the instance-owned projector, if configured."""

        if self._close_callback is not None:
            self._close_callback()
            self._close_callback = None


__all__ = ["SpsaUpdateNotFoundError", "SpsaUpdateQueryService"]
