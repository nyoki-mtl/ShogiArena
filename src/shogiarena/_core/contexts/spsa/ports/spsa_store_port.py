"""Service port contracts used by SPSA orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from shogiarena._core.shared.kernel.json_types import JsonObject


@runtime_checkable
class SpsaStorePort(Protocol):
    """Protocol abstraction for SPSA store dependencies."""

    def load_meta_data(self) -> object: ...
    def spsa_path(self, filename: str) -> Path: ...
    def load_event_entries(self) -> list[object]: ...
    def read_spsa_engine_names(self) -> tuple[str | None, str | None]: ...
    def load_index_updates(self) -> list[object]: ...
    def load_index_metadata(self) -> object: ...
    def load_variants_map(self) -> JsonObject: ...
    def load_ltc_results(self) -> list[object]: ...


@runtime_checkable
class SpsaSummaryServicePort(Protocol):
    """Protocol for SPSA summary computation."""

    def compute_summary(self) -> object: ...


@runtime_checkable
class SpsaSummaryRefreshPort(Protocol):
    """Protocol for summary services that keep a snapshot for readers.

    `/summary` は snapshot を読むだけにして I/O と SQLite をリクエストパスから外している
    （task 0065）。その snapshot を実際に計算し直すのがこの入口で、run 実行中の
    コアレス済み refresh 経路だけが呼ぶ。

    snapshot を持たない summary サービス（テストの fake、直接構築した
    `SpsaSummaryService`）はこれを実装しない。呼び出し側は `isinstance` で分岐する。
    """

    def refresh_summary(self) -> object: ...


@runtime_checkable
class SpsaUpdateQueryPort(Protocol):
    """Protocol for querying SPSA update data and event snapshots."""

    def build_update_detail(self, idx: int) -> object: ...
    def get_game_event_snapshot(self, game_id: str) -> JsonObject | None: ...
    def collect_game_id_entries(self) -> list[tuple[str, int]]: ...
    def load_index_updates(self) -> list[object]: ...
    def collect_updates_from_events(self) -> list[object]: ...
    def compute_progress_snapshot(self, updates: Sequence[object] | None = None) -> object: ...


@runtime_checkable
class SpsaGameListingPort(Protocol):
    """Protocol for SPSA game listing queries."""

    def list_games(self, offset: int, limit: int, search_query: str) -> tuple[list[object], int]: ...


@runtime_checkable
class SpsaAnalysisPort(Protocol):
    """Protocol for SPSA analysis computation."""

    def compute_convergence_analysis(self, updates: list[object]) -> object: ...
    def compute_correlation_analysis(self, updates: list[object]) -> object: ...


__all__ = [
    "SpsaAnalysisPort",
    "SpsaGameListingPort",
    "SpsaStorePort",
    "SpsaSummaryServicePort",
    "SpsaUpdateQueryPort",
]
