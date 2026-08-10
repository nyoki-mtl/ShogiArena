"""Default composition root assembling the full runtime dependency graph.

各 entrypoint (CLI / dashboard / boundary parser) はこの root から
typed deps を取得し、application layer に渡す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import rsshogi.record

from shogiarena._core.contexts.csa_watch.adapters.jsonl_log_source import JsonlLogSource
from shogiarena._core.contexts.csa_watch.adapters.record_sinks import CsaRecordFileWriter, SqliteCsaRecordStore
from shogiarena._core.contexts.csa_watch.adapters.rsshogi_replay import RsshogiBoardReplay
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import CsaLogSourceFactory
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import (
    CsaRecordFileWriterPort,
    CsaRecordStoreFactory,
)
from shogiarena._core.contexts.csa_watch.ports.replay_ports import BoardReplayPort
from shogiarena._core.contexts.dashboard.adapters.archive_snapshot import resolve_archive_databases
from shogiarena._core.contexts.dashboard.adapters.game_repository import (
    build_games_list_raw_payload,
    build_match_history_raw_payload,
    load_game_record,
    load_games_for_dashboard,
)
from shogiarena._core.contexts.dashboard.adapters.instances_persistence import (
    upsert_instance_spec,
)
from shogiarena._core.contexts.dashboard.adapters.interface_dependency_services import (
    DashboardGameQueryAdapter,
    DashboardInstancesAdapter,
    DashboardRuntimeSupportAdapter,
    DashboardSpsaSupportAdapter,
    ReadOnlyArchiveGameQuery,
)
from shogiarena._core.contexts.dashboard.adapters.result_summary_reader import SQLiteResultSummaryReader
from shogiarena._core.contexts.dashboard.adapters.run_state_loader import load_run_state_mapping
from shogiarena._core.contexts.dashboard.adapters.snapshot_storage import SnapshotStorage as SnapshotStorageAdapter
from shogiarena._core.contexts.dashboard.adapters.spsa.ltc_service import SpsaLtcService
from shogiarena._core.contexts.dashboard.adapters.spsa.params_service import SpsaParamsService
from shogiarena._core.contexts.dashboard.adapters.spsa.service_factory import SpsaDashboardServicesFactory
from shogiarena._core.contexts.dashboard.application.event_bus import EventBus
from shogiarena._core.contexts.dashboard.application.events import DashboardEvent
from shogiarena._core.contexts.dashboard.application.game.cache import GameSnapshotCache
from shogiarena._core.contexts.dashboard.application.game.state import GameStateUpdater
from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import build_live_view_snapshot
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.ports.archive_snapshot_ports import ArchiveDatabaseResolverFn
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardInterfaceDependencies,
    configure_dashboard_interface_dependencies,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import SnapshotStoragePort
from shogiarena._core.contexts.game_session.adapters.engine.artifact_resolver import ArtifactResolver
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    normalize_worker_snapshot_dto,
)
from shogiarena._core.contexts.game_session.application.summary.offline_result_summary_service import (
    OfflineResultSummaryRequest,
    OfflineResultSummaryService,
)
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    DashboardScheduleBoundaryPort,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.game_execution_worker import GameExecutionWorkerPort
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import DashboardProfile
from shogiarena._core.contexts.instances.adapters.engine_runtime_adapter import (
    EngineRuntimeAdapter,
    create_default_engine_runtime_factory,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.instances.ports.engine_runtime_port import EngineRuntimePort
from shogiarena._core.contexts.spsa.adapters.runtime_adapter import SpsaRuntimeAdapter
from shogiarena._core.contexts.spsa.ports.dashboard_factory import (
    DashboardSpsaServicesFactory,
    configure_dashboard_service_factory,
)
from shogiarena._core.contexts.spsa.ports.spsa_runtime_port import SpsaRuntimePort
from shogiarena._core.contexts.tournament.adapters.runtime_adapter import TournamentRuntimeAdapter
from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import TournamentRuntimePort
from shogiarena._core.interfaces.dashboard.api_server.server import ArenaAPIServer
from shogiarena._core.interfaces.dashboard.assets_writer import init_dashboard_html
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize


@dataclass(frozen=True, slots=True)
class DefaultRoot:
    """全ての runtime dependency を保持する composition root."""

    engine_factory_service: EngineFactoryService
    engine_runtime: EngineRuntimePort
    game_execution_worker: GameExecutionWorkerPort
    tournament_runtime: TournamentRuntimePort
    spsa_runtime: SpsaRuntimePort
    init_dashboard_html: InitDashboardHtmlFn
    api_server_factory: DashboardApiServerFactory
    dashboard_service_factory: DashboardSpsaServicesFactory
    game_record_loader: GameRecordLoaderFn
    archive_database_resolver: ArchiveDatabaseResolverFn
    csa_log_source_factory: CsaLogSourceFactory
    csa_board_replay: BoardReplayPort
    csa_record_file_writer: CsaRecordFileWriterPort
    csa_record_store_factory: CsaRecordStoreFactory


class GameRecordLoaderFn(Protocol):
    """run DB から Record を読む callable 契約。"""

    def __call__(
        self,
        db_path: Path,
        *,
        game_name: str | None = None,
        game_id: int | None = None,
    ) -> rsshogi.record.Record | None: ...


def _create_snapshot_storage(state: object, run_dir: Path) -> SnapshotStoragePort:
    """Create a snapshot storage adapter with boundary parse/serialize injected."""
    from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
    from shogiarena._core.interfaces.boundaries.dashboard_serializer import serialize_dashboard_snapshot_payload
    from shogiarena._core.interfaces.boundaries.parsers.snapshot import parse_dashboard_snapshot_payload

    assert isinstance(state, DashboardState)
    return SnapshotStorageAdapter(
        state,
        run_dir,
        parse_snapshot=parse_dashboard_snapshot_payload,
        serialize_snapshot=serialize_dashboard_snapshot_payload,
    )


def _build_default_summary_snapshot(*, source: str = "tournament") -> JsonObject:
    timestamp = datetime.now(tz=UTC).isoformat()
    return {
        "is_summary_ready": False,
        "summary_source": source,
        "mode": source,
        "tournament_type": None,
        "flip_policy": None,
        "num_engines": 0,
        "run_dir": None,
        "leaderboard": [],
        "rating_initial": None,
        "engines": [],
        "engines_meta": [],
        "engine_time_controls": {},
        "default_time_control": None,
        "engine_instances": {},
        "engine_stats": {},
        "pair_results": {},
        "timestamp": timestamp,
        "games": {
            "completed": 0,
            "total": 0,
            "cancelled": 0,
        },
        "btd": {
            "ratings": {},
            "anchor": None,
            "gamma_elo": 0.0,
            "gamma_elo_se": 0.0,
            "draw_eq": 0.5,
            "draw_eq_se": 0.0,
            "rating_cov": {},
        },
    }


def _create_dashboard_state(run_dir: Path) -> DashboardState:
    state = DashboardState()

    default_summary = _build_default_summary_snapshot(source="tournament")
    default_summary["run_dir"] = str(run_dir)
    default_summary["live_view"] = json_serialize(build_live_view_snapshot(default_summary))
    state.set_summary_snapshot("tournament", default_summary)

    generate_summary = _build_default_summary_snapshot(source="generate")
    generate_summary["run_dir"] = str(run_dir)
    state.set_summary_snapshot("generate", generate_summary)
    return state


def _create_api_server(
    db_path: Path,
    port: int,
    run_dir: Path | None = None,
    instance_pool: object | None = None,
    *,
    host: str = "127.0.0.1",
    read_only: bool = False,
    ledger_run_dir: Path | None = None,
    dashboard_num_workers: int = 0,
    dashboard_profiles: tuple[DashboardProfile, ...] | None = None,
    schedule_boundary: DashboardScheduleBoundaryPort | None = None,
) -> ArenaAPIServer:
    resolved_run_dir = run_dir or db_path.parent
    # On-demand replay engines need a real local instance; guarantee a pool so the
    # runtime factory never has to synthesize a placeholder. Archived dashboards
    # are read-only and must not create mutable instance state just by starting.
    resolved_instance_pool = (
        instance_pool if instance_pool is not None or read_only else InstancePool.ensure_default_local_pool()
    )
    interface_dependencies = load_dashboard_interface_dependencies()
    state = _create_dashboard_state(resolved_run_dir)
    game_state = GameStateUpdater(
        GameSnapshotCache(state),
        normalize_snapshot=normalize_worker_snapshot_dto,
    )
    snapshot_storage = interface_dependencies.runtime_support.create_snapshot_storage(state, resolved_run_dir)
    event_bus: EventBus[DashboardEvent] = EventBus()
    # アーカイブ閲覧では全 DB 読み取りを immutable=1 へ倒す。`mode=ro` は archive tree に
    # sidecar を作ってしまい、次回起動で archive snapshot resolver がその sidecar を
    # 検出して不要な複製を発動する自己汚染ループになる(task 0066)。
    game_query = (
        ReadOnlyArchiveGameQuery(interface_dependencies.game_query) if read_only else interface_dependencies.game_query
    )
    return ArenaAPIServer(
        db_path=db_path,
        host=host,
        port=port,
        run_dir=resolved_run_dir,
        instance_pool=resolved_instance_pool,
        read_only=read_only,
        ledger_run_dir=ledger_run_dir,
        dashboard_num_workers=dashboard_num_workers,
        dashboard_profiles=dashboard_profiles,
        schedule_boundary=schedule_boundary,
        state=state,
        game_state=game_state,
        snapshot_storage=snapshot_storage,
        event_bus=event_bus,
        game_query=game_query,
    )


@lru_cache(maxsize=1)
def build_default_root() -> DefaultRoot:
    """Build the default runtime dependency graph.

    結果はキャッシュされる。テスト時は直接 DefaultRoot を構築すること。
    """
    InstancePool.configure_default_local_instances_path(project_dirs.output_dir)

    # Engine factory layer
    engine_factory = create_default_engine_runtime_factory()
    artifact_resolver = ArtifactResolver().resolve
    engine_factory_service = EngineFactoryService(
        factory=engine_factory,
        artifact_resolver=artifact_resolver,
    )

    # Engine runtime adapter
    engine_runtime_adapter = EngineRuntimeAdapter(
        engine_factory_service=engine_factory_service,
        game_execution_instance_pool=InstancePool.ensure_default_local_pool(),
    )
    engine_runtime: EngineRuntimePort = engine_runtime_adapter
    game_execution_worker: GameExecutionWorkerPort = engine_runtime_adapter

    # Dashboard factories
    dashboard_html_fn: InitDashboardHtmlFn = init_dashboard_html
    api_server_factory: DashboardApiServerFactory = _create_api_server
    spsa_dashboard_factory = SpsaDashboardServicesFactory()
    configure_dashboard_interface_dependencies(
        DashboardInterfaceDependencies(
            game_query=DashboardGameQueryAdapter(
                games_loader=load_games_for_dashboard,
                game_record_loader=load_game_record,
                games_raw_payload_builder=build_games_list_raw_payload,
                match_history_raw_payload_builder=build_match_history_raw_payload,
            ),
            instances=DashboardInstancesAdapter(
                instance_spec_upserter=upsert_instance_spec,
            ),
            runtime_support=DashboardRuntimeSupportAdapter(
                snapshot_storage_factory=_create_snapshot_storage,
                run_state_loader=load_run_state_mapping,
            ),
            spsa_support=DashboardSpsaSupportAdapter(
                ltc_service_factory=SpsaLtcService,
                params_service_factory=SpsaParamsService,
            ),
        )
    )
    configure_dashboard_service_factory(spsa_dashboard_factory)

    # Tournament runtime adapter
    tournament_runtime: TournamentRuntimePort = TournamentRuntimeAdapter(
        engine_factory_service=engine_factory_service,
        init_dashboard_html=dashboard_html_fn,
        api_server_factory=api_server_factory,
    )

    # SPSA runtime adapter
    spsa_runtime: SpsaRuntimePort = SpsaRuntimeAdapter(
        engine_factory_service=engine_factory_service,
        init_dashboard_html=dashboard_html_fn,
        api_server_factory=api_server_factory,
        dashboard_service_factory=spsa_dashboard_factory,
    )

    return DefaultRoot(
        engine_factory_service=engine_factory_service,
        engine_runtime=engine_runtime,
        game_execution_worker=game_execution_worker,
        tournament_runtime=tournament_runtime,
        spsa_runtime=spsa_runtime,
        init_dashboard_html=dashboard_html_fn,
        api_server_factory=api_server_factory,
        dashboard_service_factory=spsa_dashboard_factory,
        game_record_loader=load_game_record,
        archive_database_resolver=resolve_archive_databases,
        csa_log_source_factory=JsonlLogSource,
        csa_board_replay=RsshogiBoardReplay(),
        csa_record_file_writer=CsaRecordFileWriter(),
        csa_record_store_factory=SqliteCsaRecordStore,
    )


def build_offline_result_summary(
    *,
    db_path: Path,
    request: OfflineResultSummaryRequest,
) -> object:
    """Build an offline result summary from a SQLite game database."""

    return OfflineResultSummaryService().build_summary(
        SQLiteResultSummaryReader(db_path),
        request,
    )


__all__ = [
    "build_default_root",
    "build_offline_result_summary",
]
