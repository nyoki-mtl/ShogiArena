"""Tournament session runner with typed state and explicit collaborators."""

from __future__ import annotations

import logging
from pathlib import Path
from shutil import rmtree
from typing import Any

import rshogi.record

from shogiarena._core.contexts.game_session.adapters.context_factory import SessionContextFactory
from shogiarena._core.contexts.game_session.adapters.dashboard_lifecycle import (
    DashboardCoordinator,
    DashboardLifecycleCoordinator,
)
from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import (
    collect_engine_metadata,
    compute_engine_time_control_specs,
    engine_instance_defaults,
)
from shogiarena._core.contexts.game_session.adapters.engine.runtime_snapshot_cache import (
    fetch_runtime_snapshots,
    resolve_engine_metadata_cache,
)
from shogiarena._core.contexts.game_session.adapters.openbench.client_types import (
    OpenBenchError,
)
from shogiarena._core.contexts.game_session.adapters.openbench.delegate import OpenBenchDelegate
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    resolve_engine_config_entry,
)
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import (
    TournamentRunResult,
    TournamentRunResultBuilder,
    serialize_rules_config,
)
from shogiarena._core.contexts.game_session.adapters.results.store import RunStorageResultStore
from shogiarena._core.contexts.game_session.application.completion.openbench_context_service import (
    CompletionOpenBenchContextService,
)
from shogiarena._core.contexts.game_session.application.completion.runtime_context_service import (
    CompletionRuntimeContextService,
)
from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.application.elo_rating_service import EloRatingService
from shogiarena._core.contexts.game_session.application.session.base_session_runner import BaseSessionRunner
from shogiarena._core.contexts.game_session.application.session.execution_service import (
    TournamentSessionExecutionService,
)
from shogiarena._core.contexts.game_session.application.session.run_loop_service import TournamentRunLoopService
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunManifestSealError,
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.application.session.run_service import TournamentSessionRunService
from shogiarena._core.contexts.game_session.application.sprt_service import Sprt
from shogiarena._core.contexts.game_session.application.summary.results_service import TournamentSummaryResultsService
from shogiarena._core.contexts.game_session.application.summary.runtime_context import (
    SummaryRuntimeActionRefs,
    SummaryRuntimeBuildRequest,
    SummaryRuntimeDependencies,
    SummaryRuntimeStateRefs,
    TournamentSummaryRuntimeContext,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context_service import (
    TournamentSummaryRuntimeContextService,
)
from shogiarena._core.contexts.game_session.application.summary.session_service import TournamentSummaryService
from shogiarena._core.contexts.game_session.ports.completion_runtime import CompletionRuntimeContext
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.run_runtime import SessionRunRuntimePort
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import (
    OrchestratorPort,
    ProgressReporterPort,
    RunOptions,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.tournament.adapters.dashboard_schedule_facade import DashboardScheduleFacade
from shogiarena._core.contexts.tournament.adapters.orchestrator import (
    TournamentOrchestrator,
)
from shogiarena._core.contexts.tournament.adapters.runner_runtime_context_builders import (
    build_tournament_completion_runtime_context,
    generate_schedule_for_state_store,
)
from shogiarena._core.contexts.tournament.application.mode_strategy import (
    TournamentModeStrategy,
    resolve_tournament_mode_strategy,
)
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.schedule_generation import (
    GameScheduler,
    GauntletScheduler,
    create_scheduler,
)
from shogiarena._core.contexts.tournament.application.schedule_ordering_service import (
    reorder_and_shuffle,
)
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    apply_assignment_override as _apply_assignment_override,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    ensure_display_order_for_specs as _ensure_display_order_for_specs,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    has_pending_games as _has_pending_games,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    notify_schedule_available as _notify_schedule_available,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    refresh_game_assignments as _refresh_game_assignments,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    reset_display_order as _reset_display_order,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    reset_schedule_tracking as _reset_schedule_tracking,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    serialize_assignment_override as _serialize_assignment_override,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    shared_override_label as _shared_override_label,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    wait_for_new_schedule as _wait_for_new_schedule,
)
from shogiarena._core.contexts.tournament.application.session.schedule_mutation_service import ScheduleMutationService
from shogiarena._core.contexts.tournament.application.session.schedule_snapshot_service import ScheduleSnapshotService
from shogiarena._core.contexts.tournament.application.session.state_store import TournamentSessionStateStore
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    TournamentScheduleGeneratorRuntimeContext,
    TournamentStateSaveContext,
    TournamentStateSetupContext,
)
from shogiarena._core.platform.records.binary_writer import (
    RecordBinaryWriter,
    RecordBinaryWriterConfig,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.participation_records import extract_participation as _extract_participation
from shogiarena._core.shared.kernel.session_hooks import (
    CallbackGameLifecycleHooks,
    GameCompletionEvent,
    GameLifecycleHooks,
    SessionStopController,
)
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TournamentRunner
# ---------------------------------------------------------------------------


class TournamentRunner(BaseSessionRunner[TournamentRunResult, None]):
    """N-engine native tournament runner.

    Orchestrates tournaments with arbitrary number of engines using
    game-based execution, stable IDs, and deterministic resume.

    State is held in a typed ``TournamentRunnerState`` instance;
    orchestration logic delegates to explicit collaborator services.
    """

    dashboard_profiles = ("tournament",)
    config: TournamentRunConfig
    scheduler: GameScheduler

    def __init__(
        self,
        config: TournamentRunConfig,
        *,
        instance_pool: InstancePool | None = None,
        storage: RunStoragePort,
        engine_factory_service: EngineFactoryService,
        init_dashboard_html: InitDashboardHtmlFn,
        api_server_factory: DashboardApiServerFactory,
        progress_reporter: ProgressReporterPort | None = None,
        is_dashboard_enabled: bool | None = None,
        should_skip_resume: bool = False,
    ) -> None:
        is_enabled = config.dashboard.is_enabled if is_dashboard_enabled is None else bool(is_dashboard_enabled)
        config.dashboard.is_enabled = is_enabled
        run_options = RunOptions(should_skip_resume=bool(should_skip_resume))
        self._mode_strategy: TournamentModeStrategy = resolve_tournament_mode_strategy(
            config, scheduler_name=str(config.tournament.scheduler)
        )
        if instance_pool is None:
            instance_pool = InstancePool.load_default_local() or InstancePool()
            instance_pool.ensure_local_instance()
        # -- Typed mutable state (created early for facade) ------------------
        self._state = TournamentRunnerState()
        self._state.schedule_wait_event.set()
        self._tournament_orchestrator: TournamentOrchestrator | None = None

        # -- Collaborator services (created early for facade) ----------------
        self._snapshot_service = ScheduleSnapshotService()
        self._mutation_service = ScheduleMutationService(snapshot_service=self._snapshot_service)

        dashboard_profiles = self._mode_strategy.dashboard_profiles
        self._schedule_facade = DashboardScheduleFacade(
            mutation_service=self._mutation_service,
            snapshot_service=self._snapshot_service,
            state=self._state,
            schedule_ctx_supplier=lambda: self._schedule_ctx,
        )
        dashboard_manager = DashboardLifecycleCoordinator(
            instance_pool=instance_pool,
            schedule_boundary=self._schedule_facade,
            profiles=dashboard_profiles,
            init_dashboard_html=init_dashboard_html,
            api_server_factory=api_server_factory,
        )
        super().__init__(
            instance_pool=instance_pool,
            storage=storage,
            run_options=run_options,
            progress_reporter=progress_reporter,
            dashboard_profiles=dashboard_profiles,
            dashboard_manager=dashboard_manager,
            dashboard_coordinator=DashboardCoordinator(dashboard_manager),
            result_store=RunStorageResultStore(storage=storage),
        )
        self.config = config
        self.run_dir = storage.run_dir
        self._dashboard_enabled = is_enabled
        self._summary_source = self._mode_strategy.summary_source

        # -- Collaborator services -----------------------------------------
        self._execution_service = TournamentSessionExecutionService()
        self._completion_service = TournamentSessionCompletionService()
        self._run_loop_service = TournamentRunLoopService()
        self._run_service = TournamentSessionRunService()
        self._run_metadata_service = RunMetadataPersistenceService()
        self._state_store = TournamentSessionStateStore()
        self._summary_service = TournamentSummaryService()
        self._summary_results_service = TournamentSummaryResultsService()
        self._summary_runtime_context_service = TournamentSummaryRuntimeContextService()
        self._completion_openbench_context_service = CompletionOpenBenchContextService()
        self._completion_runtime_context_service = CompletionRuntimeContextService()
        self._result_builder = TournamentRunResultBuilder(
            run_id=str(config.experiment_name or self.run_dir.name),
            run_dir=self.run_dir,
            storage=self.storage,
        )
        self._session_context_factory = SessionContextFactory()
        self._engine_factory_service = engine_factory_service

        self.scheduler: GameScheduler
        if config.tournament.scheduler == "gauntlet":
            self.scheduler = GauntletScheduler(baseline_count=config.tournament.baseline_count)
        else:
            self.scheduler = create_scheduler(config.tournament.scheduler)

        self._run_options = run_options
        self._openbench = OpenBenchDelegate(
            self.config,
            run_dir=self.run_dir,
            should_skip_resume=self._run_options.should_skip_resume,
        )
        self._record_writer: RecordBinaryWriter | None = None
        self._frozen_run_config_payload: JsonObject | None = None

    # ======================================================================
    # Schedule context
    # ======================================================================

    @property
    def _schedule_ctx(self) -> TournamentScheduleContext:
        """Build schedule context from current runner state."""
        return TournamentScheduleContext(
            config=self.config,
            scheduler=self.scheduler,
            instance_pool=self.instance_pool,
            run_dir=self.run_dir,
            stop_controller=self.stop_controller,
            is_dashboard_enabled=self._dashboard_enabled,
            engine_instance_defaults=self._engine_instance_defaults(),
            orchestrator=self._orchestrator,
            tournament_orchestrator=self._tournament_orchestrator,
            save_run_state=self._save_run_state,
            update_dashboard=self._update_dashboard,
            reorder_and_shuffle=self._reorder_and_shuffle,
            is_generate_run=self._is_generate_run,
        )

    # ======================================================================
    # Lifecycle hooks (from BaseSessionRunner template)
    # ======================================================================

    async def _on_tournament_game_complete(self, event: GameCompletionEvent[GameSpec]) -> None:
        await self._handle_game_completion(
            event.payload,
            event.game_info,
            is_stop_requested=event.is_stop_requested,
        )

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return CallbackGameLifecycleHooks(
            stop_controller=controller,
            payload_type=GameSpec,
            on_game_complete_fn=self._on_tournament_game_complete,
        )

    def build_session_context(self) -> SessionContext:
        rd = self.run_dir
        num_workers = int(self.config.tournament.num_parallel)
        run_id = str(self.config.experiment_name or rd.name)
        if self._state.db_service is None:
            raise ValueError("Tournament runner requires db_service before creating session context")
        if self._state.rating_service is None:
            raise ValueError("Tournament runner requires rating_service before creating session context")
        metadata = {
            "runner_type": "tournament",
            "experiment_name": str(self.config.experiment_name or ""),
            "scheduler": str(self.config.tournament.scheduler),
            "games_per_pair": int(self.config.tournament.games_per_pair),
            "num_engines": int(len(self.config.engines)),
            "is_dashboard_enabled": bool(self._dashboard_enabled),
        }
        return self._session_context_factory.build_or_resume(
            storage=self.storage,
            num_workers=num_workers,
            instance_pool=self.instance_pool,
            run_id=run_id,
            metadata=metadata,
            should_skip_resume=bool(self._run_options.should_skip_resume),
        )

    def get_sprt_status(self) -> JsonObject | None:
        sprt_service = self._state.sprt
        if sprt_service is None:
            return None
        return sprt_service.get_status()

    # ======================================================================
    # Completion logic
    # ======================================================================

    async def _create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext,
    ) -> TournamentOrchestrator:
        """Create game-based orchestrator configured for the current session."""
        orchestrator = TournamentOrchestrator(
            config=self.config,
            session=session_context,
            hooks=hooks,
            db_service=self._state.db_service,
            engine_factory_service=self._engine_factory_service,
            summary_updater=self._update_dashboard,
            api_server=self.api_server,
            cancelled_provider=lambda: set(self._state.cancelled_game_ids),
        )
        self._orchestrator = orchestrator
        self._tournament_orchestrator = orchestrator
        return orchestrator

    def _build_completion_runtime_context(self) -> CompletionRuntimeContext:
        return build_tournament_completion_runtime_context(
            completion_runtime_context_service=self._completion_runtime_context_service,
            completion_openbench_context_service=self._completion_openbench_context_service,
            completed_game_summaries=self._state.completed_game_summaries,
            config=self.config,
            summary_source=self._summary_source,
            is_generate_run=self._is_generate_run(),
            db_service=self._state.db_service,
            record_writer=self._record_writer,
            rating_service=self._state.rating_service,
            completed_game_ids=self._state.completed_game_ids,
            sprt_service=self._state.sprt,
            sprt_pair=self._state.sprt_pair,
            sprt_min_games=self._state.sprt_min_games,
            stop_controller=self.stop_controller,
            is_dashboard_enabled=self._dashboard_enabled,
            total_games=int(self._state.original_total_games or len(self._state.game_schedule)),
            save_run_state_fn=self._save_run_state,
            openbench_client=self._openbench.client,
            sync_after_game_fn=lambda: self._openbench.sync_after_game(
                db_service=self._state.db_service,
                stop_controller=self.stop_controller,
            ),
        )

    async def _handle_game_completion(
        self,
        game_spec: GameSpec,
        game_info: rshogi.record.GameRecord,
        *,
        is_stop_requested: bool,
    ) -> None:
        """Handle post-processing for a completed game."""
        should_update_dashboard = False
        result: GameResult
        async with self._state.completion_lock:
            self._state.engine_metadata_cache = None
            runtime_context = self._build_completion_runtime_context()
            should_update_dashboard, result = await self._completion_service.process_game_completion(
                runtime_context,
                game_spec,
                record=game_info,
                is_stop_requested=is_stop_requested,
                extract_participation=_extract_participation,
                openbench_error_type=OpenBenchError,
            )

        if should_update_dashboard:
            await self._update_dashboard()

        self.progress.on_game_complete(
            to_json_object(
                self._completion_service.build_progress_payload(
                    runtime_context.state,
                    game_spec,
                    result=result,
                )
            )
        )

    async def run(
        self,
        *,
        progress_reporter: ProgressReporterPort | None = None,
    ) -> Any:
        """Run tournament orchestration with support for dashboard rescheduling."""
        if not isinstance(self, SessionRunRuntimePort):
            raise TypeError("TournamentRunner does not satisfy session run runtime contract")
        result = await self._run_service.run(
            self,
            execution_service=self._execution_service,
            run_loop_service=self._run_loop_service,
            result_builder=self._result_builder,
            progress_reporter=progress_reporter,
        )
        return result

    async def calculate_results(self) -> Any:
        runtime = self._build_summary_runtime_context()
        return self._summary_results_service.calculate_results(runtime)

    async def _update_dashboard(self) -> None:
        runtime: TournamentSummaryRuntimeContext = self._build_summary_runtime_context()
        await self._summary_service.update_dashboard(runtime)

    async def finalize_tournament(self, results: Any) -> None:
        runtime: TournamentSummaryRuntimeContext = self._build_summary_runtime_context()
        await self._summary_service.finalize_tournament(runtime, results)

    # ======================================================================
    # Runtime, context, and setup logic
    # ======================================================================

    def _build_rules_payload(self) -> JsonObject:
        payload = serialize_rules_config(self.config.rules)
        initial_pos = payload.get("initial_positions")
        if isinstance(initial_pos, dict):
            payload.setdefault("flip_policy", initial_pos.get("flip_policy"))
        return payload

    def _build_sprt_payload(self) -> JsonObject:
        sprt_conf = self.config.sprt
        if sprt_conf is None:
            return {}
        return sprt_conf.model_dump(mode="json")

    def _is_generate_run(self) -> bool:
        return self._mode_strategy.is_generate_run()

    def _build_state_setup_context(self) -> TournamentStateSetupContext:
        scheduler_runtime_context = TournamentScheduleGeneratorRuntimeContext(
            generate_schedule_fn=lambda engines, games_per_pair, seed, initial_positions: (
                generate_schedule_for_state_store(self.scheduler, engines, games_per_pair, seed, initial_positions)
            ),
        )
        return TournamentStateSetupContext(
            run_dir=self.run_dir,
            run_options=self._run_options,
            config=self.config,
            scheduler=scheduler_runtime_context,
            state=self._state,
            openbench=self._openbench,
            db_service=self._state.db_service,
            reorder_and_shuffle=self._reorder_and_shuffle,
            reset_schedule_tracking=lambda: _reset_schedule_tracking(self._state, list(self.config.engines)),
            write_schedule_file=lambda schedule: self._mutation_service.write_schedule_file(
                self._state, self._schedule_ctx, schedule
            ),
            notify_schedule_available=lambda: _notify_schedule_available(self._state),
            reset_display_order=lambda: _reset_display_order(self._state),
            apply_assignment_override=_apply_assignment_override,
            ensure_display_order_for_specs=lambda specs: _ensure_display_order_for_specs(self._state, specs),
            refresh_game_assignments=lambda: _refresh_game_assignments(self._state, list(self.config.engines)),
            build_save_context=self._build_state_save_context,
            schedule_hash=self._state.sealed_schedule_hash,
            resume_hash=self._state.sealed_resume_hash,
        )

    def _build_state_save_context(self) -> TournamentStateSaveContext:
        return TournamentStateSaveContext(
            run_dir=self.run_dir,
            config=self.config,
            state=self._state,
            openbench=self._openbench,
            build_rules_payload=self._build_rules_payload,
            is_generate_run=self._is_generate_run,
            serialize_assignment_override=_serialize_assignment_override,
            shared_override_label=_shared_override_label,
            schedule_hash=self._state.sealed_schedule_hash,
            resume_hash=self._state.sealed_resume_hash,
        )

    def _build_summary_runtime_context(self) -> TournamentSummaryRuntimeContext:
        request = SummaryRuntimeBuildRequest(
            run_dir=self.run_dir,
            config=self.config,
            engine_metadata=self.engine_metadata,
            engine_time_controls=self.engine_time_controls,
            summary_source=self._summary_source,
            is_dashboard_enabled=self._dashboard_enabled,
        )
        state = SummaryRuntimeStateRefs(
            cancelled_game_ids=self._state.cancelled_game_ids,
            game_schedule=self._state.game_schedule,
            completed_game_ids=self._state.completed_game_ids,
            original_total_games=self._state.original_total_games,
        )
        dependencies = SummaryRuntimeDependencies(
            db_service=self._state.db_service,
            api_server=self.api_server,
            record_writer=self._record_writer,
            sprt_service=self._state.sprt,
            openbench_client=self._openbench.client,
        )
        actions = SummaryRuntimeActionRefs(
            engine_instance_defaults=self._engine_instance_defaults,
            resolve_tournament_type=self._mode_strategy.tournament_type,
            build_rules_payload=self._build_rules_payload,
            build_sprt_payload=self._build_sprt_payload,
            is_generate_run=self._is_generate_run,
            get_schedule_snapshot=self._schedule_facade.get_schedule_snapshot,
            flush_openbench=lambda: self._openbench.flush(
                db_service=self._state.db_service, stop_controller=self.stop_controller
            ),
            save_run_state=self._save_run_state,
            update_dashboard=self._update_dashboard,
        )
        return self._summary_runtime_context_service.build_runtime_context(
            request=request,
            state=state,
            dependencies=dependencies,
            actions=actions,
        )

    async def _stop_additional_services(self) -> None:
        await self._openbench.stop()
        if self._state.db_service:
            self._state.db_service.close()
            self._state.db_service = None
        if self._record_writer is not None:
            self._record_writer.close()
            self._record_writer = None

    async def init_services(self) -> None:
        """Initialize database and rating services."""
        logger.debug("Initializing services")
        rd = self.run_dir
        rd.mkdir(parents=True, exist_ok=True)

        self._ensure_db_service()

        self._state.rating_service = EloRatingService(
            initial_rating=self.config.rating.initial, k_factor=self.config.rating.k_factor
        )
        self._record_writer = self._create_record_writer()
        self._backfill_records_output()
        sprt_conf = self.config.sprt
        if sprt_conf is not None:
            self._state.sprt = Sprt(
                elo0=float(sprt_conf.elo0),
                elo1=float(sprt_conf.elo1),
                alpha=float(sprt_conf.alpha),
                beta=float(sprt_conf.beta),
            )
            self._state.sprt_min_games = sprt_conf.min_games
            if len(self.config.engines) == 2:
                self._state.sprt_pair = (str(self.config.engines[0].name), str(self.config.engines[1].name))
            else:
                self._state.sprt_pair = None
        await self._openbench.init(stop_controller=self.stop_controller)
        logger.debug("Services initialized (DB/Rating/SPRT)")

    def _create_record_writer(self) -> RecordBinaryWriter | None:
        config = self.config.records_output
        if config is None:
            return None
        output_dir = config.output_dir or (self.run_dir / "records")
        self._validate_records_output_dir(output_dir)
        file_prefix = config.file_prefix or config.format
        writer_config = RecordBinaryWriterConfig(
            format_id=config.format,
            output_dir=output_dir,
            max_positions_per_file=int(config.max_positions_per_file),
            max_games_per_file=config.max_games_per_file,
            file_prefix=str(file_prefix),
        )
        return RecordBinaryWriter(writer_config)

    def _ensure_db_service(self) -> Any:
        db = self._state.db_service
        if db is None:
            db = self.storage.db_service()
            db.ensure_schema_compatibility()
            self._state.db_service = db
        return db

    def _records_output_dir(self) -> Path | None:
        config = self.config.records_output
        if config is None:
            return self.run_dir / "records"
        return config.output_dir or (self.run_dir / "records")

    def _validate_records_output_dir(self, output_dir: Path) -> None:
        if self._is_path_within(output_dir, self.run_dir):
            return
        has_existing = output_dir.exists() and any(output_dir.iterdir())
        if not has_existing:
            return
        is_resume_attempt = not self._run_options.should_skip_resume and (self.run_dir / "state.json").exists()
        if not is_resume_attempt:
            raise ValueError(
                "records_output.output_dir already contains files and is outside the run directory; "
                "choose an empty output_dir or resume the matching run directory"
            )

    def _backfill_records_output(self) -> None:
        writer = self._record_writer
        db = self._state.db_service
        if writer is None or db is None:
            return
        completed_ids = set(self._state.completed_game_ids)
        written_ids = writer.written_game_ids()
        orphan_ids = written_ids - completed_ids
        if orphan_ids:
            sample = ", ".join(sorted(orphan_ids)[:5])
            raise ValueError(
                "records output contains game IDs that are not completed in game.db; "
                f"start fresh before continuing. Examples: {sample}"
            )
        game_type = "generate" if self._is_generate_run() else "arena"
        for game_id in sorted(completed_ids - written_ids):
            record = db.load_record(game_name=game_id)
            if record is None:
                raise ValueError(f"game.db is missing completed record payload for {game_id}")
            writer.append_record(record, game_id=game_id, game_type=game_type)

    @staticmethod
    def _is_path_within(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
        except ValueError:
            return False
        return True

    @property
    def engine_metadata(self) -> list[JsonObject]:
        runtime_options, runtime_info = fetch_runtime_snapshots(self._orchestrator, logger=logger)
        metadata, runtime_sig = resolve_engine_metadata_cache(
            existing_metadata=self._state.engine_metadata_cache,
            existing_runtime_sig=self._state.engine_metadata_runtime_sig,
            runtime_options=runtime_options,
            collect_metadata_fn=lambda: self._collect_engine_metadata(runtime_options, runtime_info),
        )
        self._state.engine_metadata_cache = metadata
        self._state.engine_metadata_runtime_sig = runtime_sig
        assert self._state.engine_metadata_cache is not None
        return self._state.engine_metadata_cache

    def _collect_engine_metadata(
        self,
        runtime_options: EngineOptionsSnapshots,
        runtime_info: EngineInfoSnapshots,
    ) -> list[JsonObject]:
        resolver = self._engine_factory_service.artifact_resolver
        if resolver is None:
            raise RuntimeError("artifact_resolver not configured on engine_factory_service")
        return collect_engine_metadata(
            engines=self.config.engines,
            rules=self.config.rules,
            config_source_path=self.config.source_path,
            run_dir=self.run_dir,
            runtime_options=runtime_options,
            runtime_info=runtime_info,
            artifact_resolver=resolver,
        )

    @property
    def engine_time_controls(self) -> tuple[dict[str, str], str | None]:
        if self._state.engine_time_controls_cache is None:
            self._state.engine_time_controls_cache = self._compute_engine_time_control_specs()
        cache = self._state.engine_time_controls_cache
        assert cache is not None
        tc_map, default_spec = cache
        return dict(tc_map), default_spec

    def _engine_instance_defaults(self) -> dict[str, str | None]:
        return engine_instance_defaults(self.config.engines)

    def _compute_engine_time_control_specs(self) -> tuple[dict[str, str], str | None]:
        return compute_engine_time_control_specs(self.config.rules, self.config.engines)

    async def prepare_run_dir(self) -> None:
        rd = self.run_dir
        rd.mkdir(parents=True, exist_ok=True)
        if self._run_options.should_skip_resume:
            self._cleanup_existing_run()
        inputs = self._run_metadata_service.write_inputs_only_manifest(
            run_dir=self.run_dir,
            config_payload=self.config.model_dump(mode="json"),
            package_name="shogiarena",
        )
        self._frozen_run_config_payload = inputs.config_payload

    def _seal_artifact_engine_configs(self) -> None:
        cfg_out_dir = self.run_dir / "inputs" / "engine_configs"
        resolver = self._engine_factory_service.artifact_resolver
        changed = False
        for engine in self.config.engines:
            before = engine.engine_path
            resolved = resolve_engine_config_entry(
                engine,
                output_dir=cfg_out_dir,
                extra_options=None,
                artifact_resolver=resolver,
            )
            changed = changed or resolved.engine_path != before
        if changed:
            self.config.clear_run_artifact_hash_cache()

    async def prepare_domain(self) -> None:
        self._ensure_db_service()
        self._seal_artifact_engine_configs()
        frozen_payload = self._frozen_run_config_payload
        if frozen_payload is None:
            raise RuntimeError("inputs-only manifest must be written before prepare_domain")
        resolved_payload = self.config.model_dump(mode="json")
        try:
            sealed = self._run_metadata_service.seal_provenance_manifest(
                run_dir=self.run_dir,
                inputs_config_payload=frozen_payload,
                resolved_config_payload=resolved_payload,
                package_name="shogiarena",
            )
        except RunManifestSealError as exc:
            if self._run_options.should_skip_resume or not (self.run_dir / "state.json").exists():
                raise
            logger.warning("Run manifest could not be sealed; resume will be rejected: %s", exc)
            current_hashes = self._run_metadata_service.build_sealed_hashes(
                inputs_config_payload=frozen_payload,
                resolved_config_payload=resolved_payload,
            )
            self._state.sealed_schedule_hash = current_hashes.schedule_hash
            self._state.sealed_resume_hash = current_hashes.resume_hash
            await self._try_setup_tournament()
            return
        self._state.sealed_schedule_hash = sealed.hashes.schedule_hash
        self._state.sealed_resume_hash = sealed.hashes.resume_hash
        await self._try_setup_tournament()

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:
        if not self._dashboard_enabled:
            return None
        return (
            self.run_dir,
            int(self.config.dashboard.api_port),
            int(self.config.tournament.num_parallel),
        )

    async def seed_initial_summary(self) -> None:
        runtime = self._build_summary_runtime_context()
        await self._summary_service.seed_initial_summary(runtime)

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> TournamentOrchestrator:
        if session_context is None:
            raise ValueError("Tournament runner requires a session context")
        return await self._create_orchestrator(hooks, session_context)

    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort[Any]) -> None:
        if not isinstance(orchestrator, TournamentOrchestrator):
            raise TypeError("TournamentRunner expected TournamentOrchestrator")
        orchestrator.set_work_items(self._state.game_schedule, self._state.completed_game_ids)

    async def _try_setup_tournament(self) -> bool:
        ctx = self._build_state_setup_context()
        return await self._state_store.try_setup_tournament(ctx)

    def has_pending_games(self) -> bool:
        return _has_pending_games(self._state)

    async def wait_for_new_schedule(self) -> None:
        await _wait_for_new_schedule(self._state)

    async def has_applied_pending_reschedule(self) -> bool:
        return await self._mutation_service.has_applied_pending_reschedule(self._state, self._schedule_ctx)

    def _save_run_state(self, is_finished: bool = False) -> None:
        ctx = self._build_state_save_context()
        self._state_store.save_run_state(ctx, is_finished=is_finished)

    def _cleanup_existing_run(self) -> None:
        """Remove existing run artifacts."""
        logger.debug("Cleaning up existing run")
        rd = self.run_dir
        self.cleanup_run_dir(
            rd,
            files=[
                "game.db",
                "state.json",
                "schedule.json",
                "completed.flag",
                "manifest.json",
            ],
            dirs=["spsa", "html", "static", "dashboard", "inputs", "results", "failures", "logs", "records"],
        )
        records_output_dir = self._records_output_dir()
        if records_output_dir is not None and self._is_path_within(records_output_dir, rd):
            rmtree(records_output_dir, ignore_errors=True)

    def _reorder_and_shuffle(self, games: list[GameSpec]) -> list[GameSpec]:
        return reorder_and_shuffle(
            games,
            game_order=self.config.tournament.game_order,
            flip_policy=self.config.rules.initial_positions.flip_policy,
            shuffle_seed=self.config.tournament.seed,
        )


__all__ = ["TournamentRunner"]
