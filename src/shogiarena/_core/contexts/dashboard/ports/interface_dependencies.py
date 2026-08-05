"""Port registry for adapter collaborators used by dashboard interfaces."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import rsshogi

from shogiarena._core.contexts.dashboard.ports.snapshot_storage import SnapshotStoragePort
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaLtcServicePort,
    DashboardSpsaParamsServicePort,
    DashboardSpsaStorePort,
)
from shogiarena._core.shared.kernel.game_record_types import GameRecordEnginesDict
from shogiarena._core.shared.kernel.json_types import JsonObject


class DashboardGamesLoaderFn(Protocol):
    """Load dashboard game records from persistent storage."""

    def __call__(
        self,
        db_path: Path,
        *,
        game_type: str = ...,
        immutable: bool = ...,
    ) -> list[GameRecordEnginesDict]: ...


class DashboardGameRecordLoaderFn(Protocol):
    """Load a game record from the dashboard game database."""

    def __call__(
        self,
        db_path: Path,
        *,
        game_name: str,
        immutable: bool = ...,
    ) -> rsshogi.record.Record | None: ...


class DashboardGamesRawPayloadBuilderFn(Protocol):
    """Build the raw tournament games payload from the dashboard DB."""

    def __call__(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
        immutable: bool = ...,
    ) -> dict[str, object]: ...


class DashboardMatchHistoryRawPayloadBuilderFn(Protocol):
    """Build the raw tournament match history payload from the dashboard DB."""

    def __call__(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        immutable: bool = ...,
    ) -> dict[str, object]: ...


class DashboardInstanceSpecUpsertFn(Protocol):
    """Persist dashboard-visible instance metadata."""

    def __call__(
        self,
        db_path: Path,
        *,
        instance: object,
    ) -> None: ...


class DashboardSpsaLtcServiceFactoryFn(Protocol):
    """Create the dashboard LTC helper from an SPSA store."""

    def __call__(self, store: DashboardSpsaStorePort) -> DashboardSpsaLtcServicePort: ...


class DashboardSpsaParamsServiceFactoryFn(Protocol):
    """Create the dashboard parameter helper from an SPSA store."""

    def __call__(
        self,
        *,
        store: DashboardSpsaStorePort,
        run_dir: Path,
    ) -> DashboardSpsaParamsServicePort: ...


class DashboardSnapshotStorageFactoryFn(Protocol):
    """Create a snapshot storage adapter for dashboard state management."""

    def __call__(
        self,
        state: object,
        run_dir: Path,
    ) -> SnapshotStoragePort: ...


class DashboardRunStateLoaderFn(Protocol):
    """Load ``state.json`` from a run directory as a JSON object."""

    def __call__(
        self,
        run_dir: Path,
        *,
        logger: logging.Logger | None = ...,
    ) -> JsonObject: ...


class DashboardGameQueryPort(Protocol):
    """Cohesive query contract for dashboard game data access."""

    def load_games(
        self,
        db_path: Path,
        *,
        game_type: str = ...,
        immutable: bool = ...,
    ) -> list[GameRecordEnginesDict]: ...

    def load_game_record(
        self,
        db_path: Path,
        *,
        game_name: str,
        immutable: bool = ...,
    ) -> rsshogi.record.Record | None: ...

    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
        immutable: bool = ...,
    ) -> dict[str, object]: ...

    def build_match_history_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        immutable: bool = ...,
    ) -> dict[str, object]: ...


class DashboardInstancesPort(Protocol):
    """Cohesive persistence contract for dashboard instances."""

    def upsert_instance_spec(
        self,
        db_path: Path,
        *,
        instance: object,
    ) -> None: ...


class DashboardRuntimeSupportPort(Protocol):
    """Shared runtime helpers used by dashboard APIs and server bootstrap."""

    def create_snapshot_storage(self, state: object, run_dir: Path) -> SnapshotStoragePort: ...

    def load_run_state(
        self,
        run_dir: Path,
        *,
        logger: logging.Logger | None = ...,
    ) -> JsonObject: ...


class DashboardSpsaSupportPort(Protocol):
    """SPSA-specific dashboard service factory bundle."""

    def create_ltc_service(self, store: DashboardSpsaStorePort) -> DashboardSpsaLtcServicePort: ...

    def create_params_service(
        self,
        *,
        store: DashboardSpsaStorePort,
        run_dir: Path,
    ) -> DashboardSpsaParamsServicePort: ...


@dataclass(frozen=True, slots=True)
class DashboardInterfaceDependencies:
    """Adapter-backed collaborators consumed by dashboard transport code."""

    game_query: DashboardGameQueryPort
    instances: DashboardInstancesPort
    runtime_support: DashboardRuntimeSupportPort
    spsa_support: DashboardSpsaSupportPort


_dependencies: DashboardInterfaceDependencies | None = None


def configure_dashboard_interface_dependencies(
    dependencies: DashboardInterfaceDependencies,
) -> None:
    """Register dashboard interface collaborators from the composition root."""

    global _dependencies
    _dependencies = dependencies


def load_dashboard_interface_dependencies() -> DashboardInterfaceDependencies:
    """Load the configured dashboard interface collaborators."""

    if _dependencies is None:
        raise RuntimeError("dashboard interface dependencies are not configured")
    return _dependencies


__all__ = [
    "DashboardGameQueryPort",
    "DashboardInstancesPort",
    "DashboardRuntimeSupportPort",
    "DashboardSpsaSupportPort",
    "DashboardInterfaceDependencies",
    "configure_dashboard_interface_dependencies",
    "load_dashboard_interface_dependencies",
]
