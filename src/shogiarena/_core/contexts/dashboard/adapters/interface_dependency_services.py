"""Adapter bundles that expose cohesive dashboard dependency services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import rshogi

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameRecordLoaderFn,
    DashboardGamesLoaderFn,
    DashboardGamesRawPayloadBuilderFn,
    DashboardInstanceSpecUpsertFn,
    DashboardMatchHistoryRawPayloadBuilderFn,
    DashboardRunStateLoaderFn,
    DashboardSnapshotStorageFactoryFn,
    DashboardSpsaLtcServiceFactoryFn,
    DashboardSpsaParamsServiceFactoryFn,
)
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import SnapshotStoragePort
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaLtcServicePort,
    DashboardSpsaParamsServicePort,
    DashboardSpsaStorePort,
)
from shogiarena._core.shared.kernel.game_record_types import GameRecordEnginesDict
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class DashboardGameQueryAdapter:
    """Bundle game/query related dashboard collaborators into one adapter."""

    games_loader: DashboardGamesLoaderFn
    game_record_loader: DashboardGameRecordLoaderFn
    games_raw_payload_builder: DashboardGamesRawPayloadBuilderFn
    match_history_raw_payload_builder: DashboardMatchHistoryRawPayloadBuilderFn

    def load_games(
        self,
        db_path: Path,
        *,
        game_type: str = "arena",
    ) -> list[GameRecordEnginesDict]:
        return self.games_loader(db_path, game_type=game_type)

    def load_game_record(
        self,
        db_path: Path,
        *,
        game_name: str,
    ) -> rshogi.record.GameRecord | None:
        return self.game_record_loader(db_path, game_name=game_name)

    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
    ) -> dict[str, object]:
        return self.games_raw_payload_builder(
            db_path,
            limit=limit,
            offset=offset,
            search_query=search_query,
        )

    def build_match_history_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, object]:
        return self.match_history_raw_payload_builder(db_path, limit=limit, offset=offset)


@dataclass(frozen=True, slots=True)
class DashboardInstancesAdapter:
    """Bundle dashboard instance persistence collaborators."""

    instance_spec_upserter: DashboardInstanceSpecUpsertFn

    def upsert_instance_spec(
        self,
        db_path: Path,
        *,
        instance: object,
    ) -> None:
        self.instance_spec_upserter(db_path, instance=instance)


@dataclass(frozen=True, slots=True)
class DashboardRuntimeSupportAdapter:
    """Bundle run-state and snapshot-storage dashboard collaborators."""

    snapshot_storage_factory: DashboardSnapshotStorageFactoryFn
    run_state_loader: DashboardRunStateLoaderFn

    def create_snapshot_storage(self, state: object, run_dir: Path) -> SnapshotStoragePort:
        return self.snapshot_storage_factory(state, run_dir)

    def load_run_state(
        self,
        run_dir: Path,
        *,
        logger: logging.Logger | None = None,
    ) -> JsonObject:
        return self.run_state_loader(run_dir, logger=logger)


@dataclass(frozen=True, slots=True)
class DashboardSpsaSupportAdapter:
    """Bundle SPSA-specific dashboard service factories."""

    ltc_service_factory: DashboardSpsaLtcServiceFactoryFn
    params_service_factory: DashboardSpsaParamsServiceFactoryFn

    def create_ltc_service(self, store: DashboardSpsaStorePort) -> DashboardSpsaLtcServicePort:
        return self.ltc_service_factory(store)

    def create_params_service(
        self,
        *,
        store: DashboardSpsaStorePort,
        run_dir: Path,
    ) -> DashboardSpsaParamsServicePort:
        return self.params_service_factory(store=store, run_dir=run_dir)


__all__ = [
    "DashboardGameQueryAdapter",
    "DashboardInstancesAdapter",
    "DashboardRuntimeSupportAdapter",
    "DashboardSpsaSupportAdapter",
]
