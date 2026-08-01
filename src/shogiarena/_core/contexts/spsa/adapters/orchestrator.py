"""SPSA orchestration runtime primitives."""

from __future__ import annotations

import asyncio
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import (
    LtcRegressionConfig,
    SpsaRunConfig,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.application.orchestration.completion_emission_service import (
    OrchestratorCompletionEmissionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_assignment_service import (
    OrchestratorGameAssignmentService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorGameExecutionModeService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_service import (
    OrchestratorGameExecutionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_setup_service import (
    SpsaGameSetupService,
)
from shogiarena._core.contexts.game_session.application.orchestration.ltc_post_update_service import (
    SpsaLtcPostUpdateService,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_batch_execution_service import (
    SpsaUpdateBatchExecutionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_recording_service import (
    SpsaUpdateRecordingService,
)
from shogiarena._core.contexts.game_session.application.progress.hub import DashboardServerPort, SummaryUpdateCallback
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin import SpsaOrchestratorGameplayMixin
from shogiarena._core.contexts.spsa.adapters.orchestrator_lifecycle_mixin import SpsaOrchestratorLifecycleMixin
from shogiarena._core.contexts.spsa.adapters.orchestrator_remote_game_mixin import SpsaOrchestratorRemoteGameMixin
from shogiarena._core.contexts.spsa.adapters.orchestrator_update_mixin import SpsaOrchestratorUpdateMixin
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.ports.ledger_ports import SpsaLedgerRuntimePort
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks


class SpsaOrchestrator(
    SpsaOrchestratorUpdateMixin,
    SpsaOrchestratorGameplayMixin,
    SpsaOrchestratorRemoteGameMixin,
    SpsaOrchestratorLifecycleMixin,
    BaseOrchestrator,
):
    def __init__(
        self,
        config: SpsaRunConfig,
        *,
        session: SessionContext,
        hooks: GameLifecycleHooks,
        db_service: DatabaseServicePort | None = None,
        engine_factory_service: EngineFactoryService,
        summary_updater: SummaryUpdateCallback | None = None,
        api_server: DashboardServerPort | None = None,
        ledger_runtime: SpsaLedgerRuntimePort,
        remote_worker_bundle: WorkerBundleBuildResult | None = None,
    ) -> None:
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
            remote_worker_bundle=remote_worker_bundle,
        )
        # Align naming with TournamentOrchestrator: expose unified config
        self.config = config
        self.num_workers = session.num_workers
        # Align with Tournament: store provided run_dir without extra validation here
        self.run_dir: Path = session.storage.run_dir
        metadata = session.metadata or {}
        session_uuid = str(metadata.get("session_uuid") or "").strip()
        self._session_uuid: str = session_uuid or session.run_id
        self._ledger_runtime = ledger_runtime

        # Unique game id sequencer
        self._gid_seq: int = 0
        # Domain state (injected by runner)
        self._update_items: list[int] = []
        self._params: list[ParamEntry] = []
        self._sfens: list[str] = []
        # Lock for parameter updates/events across concurrent updates
        self._params_lock: asyncio.Lock = asyncio.Lock()

        self._ltc_config: LtcRegressionConfig | None
        if config.ltc_regression is not None and config.ltc_regression.is_enabled:
            self._ltc_config = config.ltc_regression
        else:
            self._ltc_config = None
        self._ltc_last_completed: int | None = None
        self._ltc_results_path: Path | None = None
        self._ltc_baseline_snapshot: list[ParamEntry] | None = None
        self._ltc_baseline_update_idx: int | None = None
        if self._ltc_config is not None:
            ltc_dir = self.run_dir / "spsa" / "ltc"
            ltc_dir.mkdir(parents=True, exist_ok=True)
            self._ltc_results_path = ltc_dir / "results.jsonl"

        # Step 1: prepare engine configs (write YAML and cache entries)
        self.engine_configs = self._prepare_engine_configs()

        # Step 2: initialize EnginePool with role-aware capacity
        base_name = str(self.config.baseline[0].name or "baseline")
        tuned_name = str(self.config.tuned[0].name or "tuned")
        self.engine_pool = self.init_engine_pool_for_roles(self.num_workers, base_name, tuned_name)

        # Step 3: initialize shared components (progress state, GameRunner, extra options)
        self._initialize_common_components(
            num_workers=self.num_workers,
            engines=list(self.engine_configs.values()),
            rules=self.config.rules,
            should_start_progress=True,
        )
        dispatch_decision_service: OrchestratorDispatchDecisionService[Instance] = OrchestratorDispatchDecisionService()
        self._dispatch_selection_service: OrchestratorDispatchSelectionService[Instance] = (
            OrchestratorDispatchSelectionService(
                dispatch_decision_service=dispatch_decision_service,
            )
        )
        self._completion_emission_service = OrchestratorCompletionEmissionService()
        self._execution_mode_service = OrchestratorGameExecutionModeService()
        self._game_execution_service = OrchestratorGameExecutionService(
            execution_mode_service=self._execution_mode_service,
        )
        self._update_batch_execution_service = SpsaUpdateBatchExecutionService()
        self._update_recording_service = SpsaUpdateRecordingService()
        self._game_assignment_service = OrchestratorGameAssignmentService()
        self._game_setup_service: SpsaGameSetupService[Instance] = SpsaGameSetupService(
            assignment_service=self._game_assignment_service,
            dispatch_selection_service=self._dispatch_selection_service,
        )
        self._ltc_post_update_service = SpsaLtcPostUpdateService()
        # Broadcast completion after DB save to avoid API/DB race

    # Progress queue consumption is launched via BaseOrchestrator.start_progress_consumer.
