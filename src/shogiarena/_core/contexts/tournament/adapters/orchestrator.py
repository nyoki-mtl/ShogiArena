"""Tournament orchestrator responsibilities.

This module holds the tournament orchestration block that manages
scheduled game execution for N-engine tournaments.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import rsshogi
from rsshogi.core import parse_usi_position_parts

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    build_engine_config_map,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    resolve_engine_config_entry,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import (
    execute_game as _execute_game_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_item_builders import (
    build_tournament_game_items,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import (
    ensure_remote_deployment as _ensure_remote_deployment_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import (
    get_remote_executor as _get_remote_executor_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    preflight_parallel_resource_capacity,
)
from shogiarena._core.contexts.game_session.application.orchestration.completion_emission_service import (
    OrchestratorCompletionEmissionRequest,
    OrchestratorCompletionEmissionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.concurrent_executor import numeric_game_id
from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_assignment_service import (
    OrchestratorGameAssignmentRequest,
    OrchestratorGameAssignmentService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorExecutionModeRequest,
    OrchestratorGameExecutionModeService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_service import (
    OrchestratorGameExecutionRequest,
    OrchestratorGameExecutionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_preflight_service import (
    emit_game_assigned_event,
    should_skip_cancelled_game,
)
from shogiarena._core.contexts.game_session.application.orchestration.local_execution_spec_service import (
    LocalExecutionSpecRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.runtime_orchestration_service import (
    PendingRuntimeOrchestrationService,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionRequest,
    OrchestratorDispatchSelectionService,
)
from shogiarena._core.contexts.game_session.application.progress.hub import DashboardServerPort, SummaryUpdateCallback
from shogiarena._core.contexts.game_session.application.progress.orchestrator_progress_control import (
    preassign_worker as _preassign_worker_service,
)
from shogiarena._core.contexts.game_session.ports.pending_runtime import (
    PendingRuntimeState,
)
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.dispatch_control import GameDispatchStoppedError
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.schedule_metadata import attach_schedule_metadata
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .remote_game_runner import run_remote_tournament_game

logger = logging.getLogger(__name__)


class TournamentOrchestrator(BaseOrchestrator):
    """Orchestrates scheduled games from the tournament runner.

    This orchestrator is N-engine native and consumes a list of GameSpec
    objects generated by the tournament runner.
    """

    # Arena/tournament runs (incl. SPRT) reclassify orchestrator-stall timeouts as invalid (0047).
    _timeout_reclassification_enabled: bool = True

    def __init__(
        self,
        config: TournamentRunConfig,
        *,
        session: SessionContext,
        hooks: GameLifecycleHooks,
        db_service: DatabaseServicePort | None = None,
        engine_factory_service: EngineFactoryService,
        summary_updater: SummaryUpdateCallback | None = None,
        api_server: DashboardServerPort | None = None,  # Optional API server for SSE broadcasting
        cancelled_provider: Callable[[], set[str]] | None = None,
        remote_worker_bundle: WorkerBundleBuildResult | None = None,
    ) -> None:
        """Initialize tournament orchestrator.

        Args:
            config: Tournament configuration
            session: Runner-prepared session context (run dir, workers, pool)
            hooks: Lifecycle hooks supplied by the runner
            db_service: Database service for game persistence
            engine_factory_service: Engine factory for creating engine instances
            summary_updater: Optional coroutine used to refresh dashboard summary
            api_server: Optional API server for SSE broadcasting
        """
        super().__init__(
            api_server=api_server,
            summary_updater=summary_updater,
            session_context=session,
            hooks=hooks,
            db_service=db_service,
            engine_factory_service=engine_factory_service,
            resource_poll_interval=config.system.resource_poll_interval,
            resource_poll_max_interval=config.system.resource_poll_max_interval,
            resource_allocation_timeout=config.system.instance_scheduling.allocation_timeout,
            default_engine_handshake_timeout=config.system.engine_handshake_timeout,
            engine_lifecycle=config.tournament.engine_lifecycle,
            remote_worker_bundle=remote_worker_bundle,
        )
        self.config = config
        self.run_dir = session.storage.run_dir
        self.num_workers = session.num_workers
        # api_server is stored by BaseOrchestrator; no reassignment needed here

        # Prepare engine configurations
        self.engine_configs = self._prepare_engine_configs()
        # Initialize EnginePool
        self.engine_pool = self.create_engine_pool(self.num_workers)

        # Schedule state (set by runner before run())
        self._completed_game_ids: set[str] = set()
        self._cancelled_provider = cancelled_provider
        self._pending_runtime_service: PendingRuntimeOrchestrationService[GameSpec] = (
            PendingRuntimeOrchestrationService()
        )
        self._dispatch_selection_service: OrchestratorDispatchSelectionService[Instance] = (
            OrchestratorDispatchSelectionService(
                dispatch_decision_service=OrchestratorDispatchDecisionService(),
            )
        )
        self._completion_emission_service = OrchestratorCompletionEmissionService()
        self._execution_mode_service = OrchestratorGameExecutionModeService()
        self._game_execution_service = OrchestratorGameExecutionService(
            execution_mode_service=self._execution_mode_service,
        )
        self._game_assignment_service = OrchestratorGameAssignmentService()
        self._pending_runtime_state: PendingRuntimeState[GameSpec] = self._pending_runtime_service.initialize_state([])

        # Initialize common components (progress, workers, game_runner, extra options)
        self._initialize_common_components(
            num_workers=self.num_workers,
            engines=config.engines,
            rules=config.rules,
            should_start_progress=True,
        )

    def set_work_items(self, schedule: list[GameSpec], completed: set[str]) -> None:
        """Provide game schedule and completed set prior to run()."""
        for index, spec in enumerate(schedule, start=1):
            if spec.display_order is None:
                spec.display_order = index
        self._pending_runtime_service.reset_schedule(self._pending_runtime_state, schedule=schedule)
        self._completed_game_ids = set(completed)

    async def run(self) -> None:
        """Unified public entrypoint for runners.

        Expects schedule to be set via set_work_items() when used by the tournament runner.
        """
        cancelled_ids = self._cancelled_provider() if self._cancelled_provider else set()
        pending = self._pending_runtime_service.collect_pending_specs(
            state=self._pending_runtime_state,
            completed_game_ids=self._completed_game_ids,
            cancelled_game_ids=cancelled_ids,
        )

        if not pending:
            logger.debug("No pending games to run")
            return

        instance_pool = self.instance_pool
        if instance_pool is None:
            raise RuntimeError("Tournament orchestrator requires an instance pool")
        await self.preflight_instance_health()
        preflight_parallel_resource_capacity(
            self,
            instance_pool,
            pending,
            self.num_workers,
            mode=self.config.system.resource_capacity_preflight,
        )

        logger.debug(f"Running {len(pending)} pending games with {self.num_workers} parallel slots")

        await self._pending_runtime_service.execute_pending_items(
            state=self._pending_runtime_state,
            pending_specs=pending,
            concurrency_limit=self.num_workers,
            running_tasks=self._running_tasks,
            worker_tasks=self._worker_tasks,
            runtime=self,
        )

        ep = self.engine_pool
        assert ep is not None
        await ep.shutdown_all()

    async def enqueue_restored_game(self, spec: GameSpec, display_order: int) -> None:
        await self._pending_runtime_service.enqueue_restored_item(
            state=self._pending_runtime_state,
            spec=spec,
            display_order=display_order,
        )

    def should_skip_pending_item(self, item: GameSpec) -> bool:
        cancelled_now = self._cancelled_provider() if self._cancelled_provider else set()
        return item.game_id in cancelled_now

    async def run_pending_item(self, item: GameSpec) -> None:
        try:
            await self._run_game(item)
        except GameDispatchStoppedError:
            # 停止要求で開始しなかった局。異常ではないので fail-fast させず、terminal record も
            # 作らない。実行されなかった局として `not_played` に残る（task 0052）。
            # ここで fail-fast させると、SPRT の正常な早期終了が runtime error になる。
            logger.debug("Game %s was not started because a stop was requested", item.game_id)
            return
        except Exception as exc:
            # SPRT / OpenBench runs fail-fast: an unexpected game error can invalidate the
            # statistical test, so propagate and abort. Plain tournament / generate runs isolate
            # the failure by recording an ERROR completion, so the game is terminal (the run loop
            # will not retry it) and the rest of the schedule keeps running.
            if self.config.sprt is not None or self.config.openbench is not None:
                raise
            logger.error(
                "Game %s failed: %s: %s; recording as ERROR and continuing",
                item.game_id,
                type(exc).__name__,
                exc,
            )
            await self._emit_game_completion(
                game_id=item.game_id,
                game_info=self._build_error_game_record(item, schedule_metadata=self._schedule_metadata_for(item)),
                payload=item,
                worker_idx=None,
            )

    @staticmethod
    def _build_error_game_record(
        game_spec: GameSpec, *, schedule_metadata: JsonObject | None = None
    ) -> rsshogi.record.Record:
        # Expand "startpos" / USI position notation to a full board SFEN (from_dict rejects
        # "startpos"), and stamp the date fields the DB persistence path requires.
        initial_sfen = parse_usi_position_parts(game_spec.initial_sfen).initial_sfen
        now = datetime.now(UTC).isoformat()
        record = rsshogi.record.Record.from_dict(
            {
                "metadata": {
                    "game_name": str(game_spec.game_id),
                    "game_type": "arena",
                    "black_player": str(game_spec.black_engine),
                    "white_player": str(game_spec.white_engine),
                    "start_date": now,
                    "end_date": now,
                    "updated_date": now,
                    "attributes": {
                        "game_name": str(game_spec.game_id),
                        "game_type": "arena",
                        "updated_date": now,
                    },
                },
                "init_position_sfen": initial_sfen,
                "moves": [],
                "result": {"result": GameResult.ERROR.name, "ply_count": 0},
            }
        )
        attach_schedule_metadata(
            game_record=record,
            schedule_metadata=(
                schedule_metadata
                if schedule_metadata is not None
                else coerce_json_object_serialized(game_spec.to_schedule_metadata(), field_name="arena_schedule")
            ),
        )
        return record

    def _schedule_metadata_for(self, game_spec: GameSpec) -> JsonObject:
        order = game_spec.display_order
        if order is None:
            state = getattr(self, "_pending_runtime_state", None)
            schedule = getattr(state, "schedule", None)
            display_order_by_game_id = getattr(schedule, "display_order_by_game_id", {})
            order_index = display_order_by_game_id.get(game_spec.game_id)
            order = order_index + 1 if order_index is not None else None
        return coerce_json_object_serialized(
            game_spec.to_schedule_metadata(display_order=order),
            field_name="arena_schedule",
        )

    def _prepare_engine_configs(self) -> dict[str, EngineConfig]:
        """Ensure each engine has a concrete YAML; synthesize from artifact if needed."""
        out_entries: list[EngineConfig] = []
        cfg_out_dir = self.run_dir / "inputs" / "engine_configs"
        for e in self.config.engines:
            resolved = resolve_engine_config_entry(
                e,
                output_dir=cfg_out_dir,
                extra_options=self.extra_options,
                artifact_resolver=self._engine_factory_service.artifact_resolver,
            )
            out_entries.append(resolved)
        return build_engine_config_map(out_entries)

    async def _run_game(self, game_spec: GameSpec) -> None:
        """Execute a scheduled game with deadlock prevention."""
        if should_skip_cancelled_game(
            game_id=game_spec.game_id,
            cancelled_provider=self._cancelled_provider,
            logger=logger,
        ):
            logger.debug("Skipping cancelled game %s", game_spec.game_id)
            return

        logger.debug(
            "Starting game %s: %s vs %s",
            game_spec.game_id,
            game_spec.black_engine,
            game_spec.white_engine,
        )

        # Pre-assign worker for this game to ensure stable mapping and remember index
        assignment = self._game_assignment_service.resolve(
            request=OrchestratorGameAssignmentRequest(
                preassigned_game_id=game_spec.game_id,
                scheduler_worker_idx=0,
                game_to_worker=self.game_to_worker,
            ),
            resolve_game_id=lambda: game_spec.game_id,
            to_numeric_game_id=numeric_game_id,
            preassign_worker=lambda numeric_id: _preassign_worker_service(
                numeric_game_id=numeric_id,
                num_workers=self.num_workers,
                game_to_worker=self.game_to_worker,
                worker_busy=self.worker_busy,
                logger=logger,
            ),
        )

        # Prepare items and time controls
        black_item, white_item, black_limits, white_limits = self._prepare_game_items(game_spec)
        emit_game_assigned_event(
            progress_queue=self.progress_sink,
            game_id=game_spec.game_id,
            initial_sfen=game_spec.initial_sfen,
            black_name=str(game_spec.black_engine),
            white_name=str(game_spec.white_engine),
            black_limits=black_limits,
            white_limits=white_limits,
        )

        dispatch_selection = self._dispatch_selection_service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=self.instance_pool,
                engine_configs=self.engine_configs,
                black_engine_name=game_spec.black_engine,
                white_engine_name=game_spec.white_engine,
                black_item_instance_override=black_item.instance_override,
                white_item_instance_override=white_item.instance_override,
                scheduling_policy=self.config.system.instance_scheduling.policy,
                required_tags=tuple(self.config.system.instance_scheduling.required_tags),
            ),
        )
        dispatch = dispatch_selection.dispatch

        def _mark_install_complete() -> None:
            game_spec.should_require_install = False

        async def _run_remote_with_instance(remote_instance: Instance) -> rsshogi.record.Record:
            return await self._run_remote_game(
                game_spec=game_spec,
                black_limits=black_limits,
                white_limits=white_limits,
                remote_instance=remote_instance,
                black_item=black_item,
                white_item=white_item,
            )

        game_info = await self._game_execution_service.execute(
            request=OrchestratorGameExecutionRequest(
                execution_mode=OrchestratorExecutionModeRequest(
                    should_require_install=game_spec.should_require_install,
                    black_instance_id=dispatch.black_instance_id,
                    white_instance_id=dispatch.white_instance_id,
                    selected_remote_instance=dispatch_selection.selected_remote_instance,
                ),
                local_execution=LocalExecutionSpecRequest(
                    black_item=black_item,
                    white_item=white_item,
                    initial_sfen=game_spec.initial_sfen,
                    game_id=game_spec.game_id,
                    black_limits=black_limits,
                    white_limits=white_limits,
                    game_round=game_spec.round_num,
                    schedule_metadata=self._schedule_metadata_for(game_spec),
                ),
            ),
            game_execution_spec_factory=BaseOrchestrator.GameExecutionSpec,
            run_remote_with_instance=_run_remote_with_instance,
            execute_local=lambda spec: _execute_game_service(self, spec),
            ensure_remote_install=self._ensure_remote_install,
            mark_install_complete=_mark_install_complete,
        )

        await self._completion_emission_service.emit(
            request=OrchestratorCompletionEmissionRequest(
                game_id=game_spec.game_id,
                game_info=game_info,
                payload=game_spec,
                worker_resolution=assignment.worker_resolution,
            ),
            emit_game_completion=self._emit_game_completion,
        )
        logger.debug("Game %s completed: %s", game_spec.game_id, game_info.result.value)

    async def _ensure_remote_install(self, instance_ids: set[str]) -> None:
        """Ensure remote instances have the arena repository prepared when requested."""
        pool = self.instance_pool
        if pool is None:
            return
        for inst_id in instance_ids:
            instance = pool.get_instance(inst_id)
            if instance is None or not instance.is_ssh:
                continue
            executor = _get_remote_executor_service(self, instance)
            try:
                await _ensure_remote_deployment_service(self, executor, instance)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.error("[%s] remote setup failed: %s", inst_id, exc, exc_info=True)
                raise

    async def _run_remote_game(
        self,
        *,
        game_spec: GameSpec,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        remote_instance: Instance,
        black_item: BaseOrchestrator.EngineGameSpec,
        white_item: BaseOrchestrator.EngineGameSpec,
    ) -> rsshogi.record.Record:
        return await run_remote_tournament_game(
            orchestrator=self,
            game_spec=game_spec,
            black_limits=black_limits,
            white_limits=white_limits,
            remote_instance=remote_instance,
            black_item=black_item,
            white_item=white_item,
            engine_configs=self.engine_configs,
            extra_options=self.extra_options,
            rules=self.config.rules,
        )

    def _prepare_game_items(
        self, game_spec: GameSpec
    ) -> tuple[
        BaseOrchestrator.EngineGameSpec,
        BaseOrchestrator.EngineGameSpec,
        TimeControlLimits,
        TimeControlLimits,
    ]:
        """Build engine items and per-side time control limits for a scheduled game.

        Returns (black_item, white_item, black_limits, white_limits).
        """
        prepared = build_tournament_game_items(
            game_id=game_spec.game_id,
            black_engine_name=game_spec.black_engine,
            white_engine_name=game_spec.white_engine,
            engine_configs=self.engine_configs,
            extra_options=self.extra_options,
            base_time_control=self.config.rules.time_control,
            black_instance_override=game_spec.assigned_instance_black,
            white_instance_override=game_spec.assigned_instance_white,
            engine_game_spec_fn=BaseOrchestrator.EngineGameSpec,
        )
        return (
            prepared.black_item,
            prepared.white_item,
            prepared.black_limits,
            prepared.white_limits,
        )
