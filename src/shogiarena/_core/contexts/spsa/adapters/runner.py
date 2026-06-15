"""SPSA session runner with typed state and explicit collaborators."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.game_session.adapters.context_factory import SessionContextFactory
from shogiarena._core.contexts.game_session.adapters.dashboard_lifecycle import (
    DashboardCoordinator,
    DashboardLifecycleCoordinator,
)
from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import collect_engine_metadata
from shogiarena._core.contexts.game_session.adapters.engine.runtime_snapshot_cache import (
    fetch_runtime_snapshots,
    resolve_engine_metadata_cache,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import SpsaRunResult
from shogiarena._core.contexts.game_session.adapters.results.store import RunStorageResultStore
from shogiarena._core.contexts.game_session.application.session.base_session_runner import BaseSessionRunner
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.result_store import ResultStorePort
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import (
    OrchestratorPort,
    ProgressReporterPort,
    RunOptions,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.adapters.runner_dashboard_payloads import (
    append_spsa_event_record,
    seed_spsa_initial_summary,
    spsa_engine_configs,
)
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import (
    build_spsa_algorithm_config,
    build_spsa_dashboard_summary_payload,
    build_spsa_final_result,
    build_spsa_rules_payload,
    build_spsa_session_context,
    create_spsa_orchestrator,
    init_spsa_dashboard_services,
    materialize_spsa_engine_configs,
    persist_spsa_game_completion,
    prepare_spsa_domain_inputs,
    prepare_spsa_run_directory,
    serialize_spsa_ltc_config,
)
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.domain.spsa_models import SpsaGamePayload
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort
from shogiarena._core.shared.kernel.session_hooks import (
    CallbackGameLifecycleHooks,
    GameCompletionEvent,
    GameCompletionPayload,
    GameLifecycleHooks,
    SessionStopController,
)

logger = logging.getLogger(__name__)


class SpsaRunner(BaseSessionRunner[SpsaRunResult, None]):
    """Runner for SPSA tuning sessions.

    State is held in a typed ``SpsaRunnerState`` instance;
    orchestration logic delegates to explicit collaborator services.
    """

    dashboard_profiles = ("spsa",)

    def __init__(
        self,
        config: SpsaRunConfig,
        *,
        instance_pool: InstancePool | None = None,
        storage: RunStoragePort,
        engine_factory_service: EngineFactoryService,
        init_dashboard_html: InitDashboardHtmlFn,
        api_server_factory: DashboardApiServerFactory,
        dashboard_service_factory: DashboardSpsaServicesFactory,
        progress_reporter: ProgressReporterPort | None = None,
        is_dashboard_enabled: bool | None = None,
        should_skip_resume: bool = False,
    ) -> None:
        is_enabled = config.dashboard.is_enabled if is_dashboard_enabled is None else bool(is_dashboard_enabled)
        config.dashboard.is_enabled = is_enabled
        if instance_pool is None:
            instance_pool = InstancePool.load_default_local() or InstancePool()
            instance_pool.ensure_local_instance()
        run_options = RunOptions(should_skip_resume=bool(should_skip_resume))
        dashboard_manager = DashboardLifecycleCoordinator(
            instance_pool=instance_pool,
            schedule_boundary=None,
            profiles=self.dashboard_profiles,
            init_dashboard_html=init_dashboard_html,
            api_server_factory=api_server_factory,
        )
        self._dashboard_service_factory = dashboard_service_factory
        result_store: ResultStorePort = RunStorageResultStore(storage=storage)
        super().__init__(
            instance_pool=instance_pool,
            storage=storage,
            run_options=run_options,
            progress_reporter=progress_reporter,
            dashboard_profiles=self.dashboard_profiles,
            dashboard_manager=dashboard_manager,
            dashboard_coordinator=DashboardCoordinator(dashboard_manager),
            result_store=result_store,
        )
        self.config = config
        self._run_options = run_options
        self.is_dashboard_enabled = bool(is_enabled)
        self.api_port = int(self.config.dashboard.api_port)
        self.num_workers = max(1, self.config.num_workers)
        self.run_dir: Path | None = storage.run_dir

        self._run_metadata_service = RunMetadataPersistenceService()
        self._session_context_factory = SessionContextFactory()
        self._engine_factory_service = engine_factory_service
        self._frozen_run_config_payload: JsonObject | None = None

        # -- Typed mutable state -------------------------------------------
        self._state = SpsaRunnerState()

    # ======================================================================
    # Engine metadata
    # ======================================================================

    @property
    def engine_metadata(self) -> list[JsonObject]:
        runtime_options, runtime_info = fetch_runtime_snapshots(self._orchestrator, logger=logger)
        metadata, runtime_sig = resolve_engine_metadata_cache(
            existing_metadata=self._state.engine_metadata_cache,
            existing_runtime_sig=self._state.engine_metadata_runtime_sig,
            runtime_options=runtime_options,
            runtime_info=runtime_info,
            collect_metadata_fn=lambda: collect_engine_metadata(
                engines=spsa_engine_configs(self.config),
                rules=self.config.rules,
                config_source_path=None,
                run_dir=self.run_dir or self.storage.run_dir,
                runtime_options=runtime_options,
                runtime_info=runtime_info,
                artifact_resolver=self._require_artifact_resolver(),
            ),
        )
        self._state.engine_metadata_cache = metadata
        self._state.engine_metadata_runtime_sig = runtime_sig
        assert self._state.engine_metadata_cache is not None
        return self._state.engine_metadata_cache

    def _require_artifact_resolver(self) -> ArtifactResolutionPort:
        resolver = self._engine_factory_service.artifact_resolver
        if resolver is None:
            raise RuntimeError("artifact_resolver not configured on engine_factory_service")
        return resolver

    # ======================================================================
    # Lifecycle hooks
    # ======================================================================

    async def _on_spsa_game_complete(self, event: GameCompletionEvent[SpsaGamePayload]) -> None:
        await self._handle_game_completion(event, event.payload)

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return CallbackGameLifecycleHooks(
            stop_controller=controller,
            payload_type=SpsaGamePayload,
            on_game_complete_fn=self._on_spsa_game_complete,
        )

    def build_session_context(self) -> SessionContext:
        if self._state.db_service is None:
            raise ValueError("SPSA runner requires db_service before creating session context")
        if self.instance_pool is None:
            raise ValueError("SPSA runner requires instance_pool before creating session context")
        return build_spsa_session_context(
            config=self.config,
            storage=self.storage,
            num_workers=self.num_workers,
            is_dashboard_enabled=self.is_dashboard_enabled,
            should_skip_resume=bool(self._run_options.should_skip_resume),
            session_uuid=self._state.session_uuid,
            instance_pool=self.instance_pool,
            session_context_factory=self._session_context_factory,
        )

    def get_sprt_status(self) -> None:
        return None

    # ======================================================================
    # Service lifecycle
    # ======================================================================

    async def _stop_additional_services(self) -> None:
        if self._state.db_service is not None:
            self._state.db_service.close()
            self._state.db_service = None

    async def prepare_run_dir(self) -> None:
        self.run_dir, self._frozen_run_config_payload = prepare_spsa_run_directory(
            run_dir=self.run_dir,
            should_skip_resume=bool(self._run_options.should_skip_resume),
            config_payload=self.config.model_dump(mode="json"),
            run_metadata_service=self._run_metadata_service,
        )

    async def init_services(self) -> None:
        assert self.run_dir is not None
        db = self.storage.db_service()
        db.ensure_schema()
        self._state.db_service = db
        logger.debug("Services initialized (DB)")
        self._init_dashboard_services()

    def _init_dashboard_services(self) -> None:
        (
            self._state.spsa_store,
            self._state.spsa_summary_service,
            self._state.spsa_update_query_service,
            self._state.spsa_game_listing_service,
            self._state.spsa_analysis_service,
        ) = init_spsa_dashboard_services(
            is_dashboard_enabled=self.is_dashboard_enabled,
            run_dir=self.run_dir,
            storage_run_dir=self.storage.run_dir,
            db_service=self._state.db_service,
            dashboard_service_factory=self._dashboard_service_factory,
            store=self._state.spsa_store,
            summary_service=self._state.spsa_summary_service,
            update_query_service=self._state.spsa_update_query_service,
            game_listing_service=self._state.spsa_game_listing_service,
            analysis_service=self._state.spsa_analysis_service,
            logger=logger,
        )

    async def _update_dashboard(self) -> None:
        if not self.api_server:
            return
        self._init_dashboard_services()
        summary_payload = await build_spsa_dashboard_summary_payload(
            summary_service=self._state.spsa_summary_service,
            engine_metadata=self.engine_metadata,
        )
        if summary_payload is not None:
            self.api_server.broadcast_summary_update(summary_payload, source="spsa")

    async def seed_initial_summary(self) -> None:
        assert self.run_dir is not None
        self._init_dashboard_services()
        rules_payload = build_spsa_rules_payload(self.config)
        ltc_meta = serialize_spsa_ltc_config(self.config.ltc_regression)
        spsa_algo_config = build_spsa_algorithm_config(self.config)
        spsa_algo_dict: JsonObject = {**spsa_algo_config, "ltc_regression": ltc_meta}
        seed_spsa_initial_summary(
            run_dir=self.run_dir,
            config=self.config,
            num_workers=self.num_workers,
            params=self._state.params,
            session_uuid=self._state.session_uuid,
            session_started_at_iso=self._state.session_started_at.isoformat(),
            api_server=self.api_server,
            engine_metadata=self.engine_metadata,
            rules_payload=rules_payload,
            spsa_algorithm_config=spsa_algo_dict,
        )

    # ======================================================================
    # Orchestrator and domain setup
    # ======================================================================

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> SpsaOrchestrator:
        if self._state.db_service is None:
            raise ValueError("SPSA runner requires db_service before creating orchestrator")
        if session_context is None:
            raise ValueError("SPSA runner requires session context")
        return create_spsa_orchestrator(
            config=self.config,
            session_context=session_context,
            hooks=hooks,
            db_service=self._state.db_service,
            engine_factory_service=self._engine_factory_service,
            summary_updater=self._update_dashboard,
            api_server=self.api_server,
        )

    async def prepare_domain(self) -> None:
        assert self.run_dir is not None
        frozen_payload = self._frozen_run_config_payload
        if frozen_payload is None:
            raise RuntimeError("inputs-only manifest must be written before prepare_domain")
        materialize_spsa_engine_configs(
            config=self.config,
            run_dir=self.run_dir,
            artifact_resolver=self._engine_factory_service.artifact_resolver,
        )
        sealed = self._run_metadata_service.seal_provenance_manifest(
            run_dir=self.run_dir,
            inputs_config_payload=frozen_payload,
            resolved_config_payload=self.config.model_dump(mode="json"),
            package_name="shogiarena",
        )
        self._state.sealed_schedule_hash = sealed.hashes.schedule_hash
        self._state.sealed_resume_hash = sealed.hashes.resume_hash
        params, sfens, update_items = prepare_spsa_domain_inputs(
            config=self.config,
            run_dir=self.run_dir,
            schedule_hash=self._state.sealed_schedule_hash,
            resume_hash=self._state.sealed_resume_hash,
        )
        self._state.params = params
        self._state.sfens = sfens
        self._state.update_items = update_items

    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort[Any]) -> None:
        assert self._state.params is not None and self._state.sfens is not None and self._state.update_items is not None
        if not isinstance(orchestrator, SpsaOrchestrator):
            raise TypeError("SpsaRunner expected SpsaOrchestrator")
        orchestrator.set_work_items(self._state.update_items, self._state.params, self._state.sfens)

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:
        if not self.is_dashboard_enabled:
            return None
        assert self.run_dir is not None
        return (self.run_dir, int(self.api_port), int(self.num_workers))

    # ======================================================================
    # Game completion
    # ======================================================================

    async def _handle_game_completion(
        self,
        event: GameCompletionEvent[GameCompletionPayload],
        payload: SpsaGamePayload,
    ) -> None:
        if self._state.db_service is None:
            raise ValueError("SPSA runner requires db_service for game completion handling")

        is_persisted = False
        async with self._state.completion_lock:
            is_persisted = persist_spsa_game_completion(db_service=self._state.db_service, event=event)
            game_info = event.game_info

        if is_persisted:
            append_spsa_event_record(
                run_dir=self.run_dir,
                payload=payload,
                game_id=event.game_id,
                game_info=game_info,
                session_uuid=self._state.session_uuid,
            )
            self.progress.on_game_complete(
                {
                    "game_id": event.game_id,
                    "update_idx": int(payload.update_idx),
                    "phase": payload.phase,
                    "winner": int(payload.winner_code),
                }
            )

    # ======================================================================
    # Finalization
    # ======================================================================

    async def finalize_and_persist(self, run_result: None) -> SpsaRunResult:
        if run_result is not None:
            raise TypeError("SpsaRunner.finalize_and_persist expects None run_result")
        summary: JsonObject | None = None
        if self._state.spsa_summary_service is not None:
            raw_summary = await asyncio.to_thread(self._state.spsa_summary_service.compute_summary)
            if isinstance(raw_summary, dict):
                summary = {str(key): json_serialize(value) for key, value in raw_summary.items()}
        run_dir = self.run_dir or self.storage.run_dir
        result = build_spsa_final_result(
            config=self.config,
            run_dir=run_dir,
            storage=self.storage,
            summary=summary,
            params=self._state.params,
        )
        self.progress.finalize({"status": "finished", "run_id": result.run_id})
        await super().finalize_and_persist(None)
        if self._result_store is None:
            raise TypeError("SpsaRunner requires result_store")
        self._result_store.save_result(result)
        return result


__all__ = ["SpsaRunner"]
