"""SPSA summary calculation service with incremental caching."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import cast

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import SpsaSummaryPayload
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.summary_cache_state import (
    create_empty_summary_cache_state,
    load_summary_cache_state,
    persist_summary_cache_state,
    refresh_summary_cache_state_from_events,
)
from shogiarena._core.contexts.spsa.application.dashboard.summary_payload_builder import (
    build_summary_payload as build_summary_payload_use_case,
)

_STORE_LOG_THRESHOLD_MS = 50.0
_CACHE_VERSION = 1
_CACHE_FILENAME = ".cache/summary_cache_v1.json"
logger = logging.getLogger(__name__)


class SpsaSummaryService:
    """Service for computing SPSA summary statistics with incremental caching."""

    def __init__(self, store: DashboardSpsaStorePort) -> None:
        self._store = store
        self._lock = Lock()
        self._cache_path: Path = store.spsa_path(_CACHE_FILENAME)
        self._cache_state = (
            load_summary_cache_state(
                self._cache_path,
                cache_version=_CACHE_VERSION,
            )
            or create_empty_summary_cache_state()
        )

    def compute_summary(self) -> SpsaSummaryPayload:
        """Compute summary statistics from cached aggregates and new events."""

        start_meta = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - start_meta) * 1000.0

        with self._lock:
            has_changed = refresh_summary_cache_state_from_events(
                self._cache_state,
                events_path=self._store.spsa_path("events.jsonl"),
                session_uuid=meta_data.session_uuid,
                logger=logger,
            )
            if has_changed:
                persist_summary_cache_state(
                    self._cache_path,
                    cache_version=_CACHE_VERSION,
                    state=self._cache_state,
                )
            agg = self._cache_state.aggregates
            updates_completed = len(agg.updates_seen)
            engine_stats = {
                name: {"wins": stat.wins, "losses": stat.losses, "draws": stat.draws, "games": stat.games}
                for name, stat in meta_data.engine_stats.items()
            }
            summary = build_summary_payload_use_case(
                experiment_name=meta_data.experiment_name,
                num_updates_total=meta_data.effective_num_updates,
                wins=agg.wins,
                losses=agg.losses,
                draws=agg.draws,
                tuned_black_wins=agg.tuned_black_wins,
                tuned_black_losses=agg.tuned_black_losses,
                tuned_white_wins=agg.tuned_white_wins,
                tuned_white_losses=agg.tuned_white_losses,
                updates_completed=updates_completed,
                last_update_idx=agg.last_update_idx,
                step_history=list(agg.step_history),
                last_delta_norm=agg.last_delta_norm,
                update_timestamps=list(agg.update_timestamps),
                engine_time_controls=meta_data.engine_time_controls,
                default_time_control=meta_data.default_time_control,
                engines=meta_data.engines,
                engine_instances=meta_data.engine_instances,
                engine_stats=engine_stats,
                engines_meta=meta_data.engines_meta,
                spsa_config=meta_data.resolve_spsa_config(),
            )

        if meta_elapsed >= _STORE_LOG_THRESHOLD_MS:
            print(f"[spsa:store:meta] duration_ms={meta_elapsed:.1f}", flush=True)

        return cast(SpsaSummaryPayload, summary)
