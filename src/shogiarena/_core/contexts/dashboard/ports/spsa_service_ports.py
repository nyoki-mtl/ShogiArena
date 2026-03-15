"""Protocol contracts used by SPSA backend services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.dashboard.application.spsa.event_types import SpsaEvent
from shogiarena._core.contexts.dashboard.application.spsa.io_models.index_io_models import IndexMetadata
from shogiarena._core.contexts.dashboard.application.spsa.io_models.meta_io_models import SpsaMetaData
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    ConvergenceAnalysis,
    CorrelationAnalysis,
    GameListEntry,
    LtcSummary,
    ParamsPayload,
    ProgressSnapshot,
    SpsaSummaryPayload,
    UpdateDetailResponse,
    UpdateEntry,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


@runtime_checkable
class DashboardSpsaStorePort(Protocol):
    """Protocol abstraction for SPSA store dependencies."""

    def load_meta_data(self) -> SpsaMetaData: ...
    def spsa_path(self, filename: str) -> Path: ...
    def load_event_entries(self) -> list[SpsaEvent]: ...
    def read_spsa_engine_names(self) -> tuple[str | None, str | None]: ...
    def load_index_updates(self) -> list[UpdateEntry]: ...
    def load_index_metadata(self) -> IndexMetadata: ...
    def load_variants_map(self) -> JsonObject: ...
    def load_ltc_results(self) -> list[SpsaEvent]: ...
    def save_best_params_snapshot(
        self,
        *,
        variant_token: str,
        update_idx: int,
        params: Mapping[str, float],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None: ...


@runtime_checkable
class DashboardSpsaUpdateQueryPort(Protocol):
    """Protocol for querying SPSA update data and event snapshots."""

    def build_update_detail(self, idx: int) -> UpdateDetailResponse: ...
    def get_game_event_snapshot(self, game_id: str) -> JsonObject | None: ...
    def collect_game_id_entries(self) -> list[tuple[str, int]]: ...
    def load_index_updates(self) -> list[UpdateEntry]: ...
    def collect_updates_from_events(self) -> list[UpdateEntry]: ...
    def compute_progress_snapshot(self, updates: Sequence[UpdateEntry] | None = None) -> ProgressSnapshot: ...


@runtime_checkable
class DashboardSpsaGameListingPort(Protocol):
    """Protocol for SPSA game listing queries."""

    def list_games(self, offset: int, limit: int, search_query: str) -> tuple[list[GameListEntry], int]: ...


@runtime_checkable
class DashboardSpsaAnalysisPort(Protocol):
    """Protocol for SPSA analysis computation."""

    def compute_convergence_analysis(self, updates: list[UpdateEntry]) -> ConvergenceAnalysis: ...
    def compute_correlation_analysis(self, updates: list[UpdateEntry]) -> CorrelationAnalysis: ...


@runtime_checkable
class DashboardSpsaSummaryServicePort(Protocol):
    """Protocol for SPSA summary computation."""

    def compute_summary(self) -> SpsaSummaryPayload: ...


@runtime_checkable
class DashboardSpsaLtcServicePort(Protocol):
    """Protocol for SPSA LTC summary helpers."""

    def compute_ltc_summary(
        self,
        *,
        enriched_results: list[JsonObject] | None = ...,
    ) -> LtcSummary: ...

    def enrich_ltc_results(self, results: Sequence[SpsaEvent | Mapping[str, JsonValue]]) -> list[JsonObject]: ...


@runtime_checkable
class DashboardSpsaParamsServicePort(Protocol):
    """Protocol for SPSA parameter metadata helpers."""

    def load_variant_entry(self, variant_id: str) -> JsonObject | None: ...

    def build_params_payload(self) -> ParamsPayload: ...
