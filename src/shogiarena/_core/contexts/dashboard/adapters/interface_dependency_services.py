"""Adapter bundles that expose cohesive dashboard dependency services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import rsshogi

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
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
        immutable: bool = False,
    ) -> list[GameRecordEnginesDict]:
        return self.games_loader(db_path, game_type=game_type, immutable=immutable)

    def load_game_record(
        self,
        db_path: Path,
        *,
        game_name: str,
        immutable: bool = False,
    ) -> rsshogi.record.Record | None:
        return self.game_record_loader(db_path, game_name=game_name, immutable=immutable)

    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
        immutable: bool = False,
    ) -> dict[str, object]:
        return self.games_raw_payload_builder(
            db_path,
            limit=limit,
            offset=offset,
            search_query=search_query,
            immutable=immutable,
        )

    def build_match_history_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        immutable: bool = False,
    ) -> dict[str, object]:
        return self.match_history_raw_payload_builder(
            db_path,
            limit=limit,
            offset=offset,
            immutable=immutable,
        )


@dataclass(frozen=True, slots=True)
class ReadOnlyArchiveGameQuery:
    """アーカイブ閲覧用に、すべての DB 読み取りを ``immutable=1`` へ倒す decorator。

    ``mode=ro`` はアーカイブに ``-wal`` / ``-shm`` を**新規作成する**(sidecar が 1 つも
    無い clean な WAL アーカイブでも作る)。それはツリー不変の契約に反するだけでなく、
    次回起動時に archive snapshot resolver が自分の作った sidecar を検出して不要な複製を
    発動する自己汚染ループを生む。

    ``immutable=1`` が un-checkpointed WAL を無視して読み落とす件は、この decorator を
    使う経路が resolver で解決済みの DB path だけを見ることで防ぐ(task 0066)。
    ライブ run の dashboard はこの decorator を通してはならない。変化を検出できなくなる。
    """

    inner: DashboardGameQueryPort

    def load_games(
        self,
        db_path: Path,
        *,
        game_type: str = "arena",
        immutable: bool = False,
    ) -> list[GameRecordEnginesDict]:
        del immutable
        return self.inner.load_games(db_path, game_type=game_type, immutable=True)

    def load_game_record(
        self,
        db_path: Path,
        *,
        game_name: str,
        immutable: bool = False,
    ) -> rsshogi.record.Record | None:
        del immutable
        return self.inner.load_game_record(db_path, game_name=game_name, immutable=True)

    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
        immutable: bool = False,
    ) -> dict[str, object]:
        del immutable
        return self.inner.build_games_raw_payload(
            db_path,
            limit=limit,
            offset=offset,
            search_query=search_query,
            immutable=True,
        )

    def build_match_history_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        immutable: bool = False,
    ) -> dict[str, object]:
        del immutable
        return self.inner.build_match_history_raw_payload(
            db_path,
            limit=limit,
            offset=offset,
            immutable=True,
        )


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
    "ReadOnlyArchiveGameQuery",
]
