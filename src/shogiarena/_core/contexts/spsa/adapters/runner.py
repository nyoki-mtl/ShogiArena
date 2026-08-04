"""SPSA session runner with typed state and explicit collaborators."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import tempfile
from collections.abc import Awaitable
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
from shogiarena._core.contexts.game_session.application.remote_worker_bundle import (
    prepare_remote_worker_bundle,
    worker_bundle_provenance,
)
from shogiarena._core.contexts.game_session.application.session.base_session_runner import BaseSessionRunner
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.result_store import ResultStorePort
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import (
    DashboardProfile,
    OrchestratorPort,
    ProgressReporterPort,
    RunOptions,
)
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.accepted_best import persist_accepted_best
from shogiarena._core.contexts.spsa.adapters.derived_json_scheduler import SpsaDerivedJsonScheduler
from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    FIXED_OPTION_PREFLIGHT_FILENAME,
    run_verified_spsa_fixed_option_preflight,
    validate_sealed_fixed_option_evidence,
)
from shogiarena._core.contexts.spsa.adapters.ledger_observations import (
    SpsaObservationLedger,
)
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import (
    open_spsa_ledger,
    recover_spsa_ledger,
)
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.adapters.orchestrator_update_mixin import (
    SpsaOrchestratorUpdateMixin,
)
from shogiarena._core.contexts.spsa.adapters.runner_dashboard_payloads import (
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
from shogiarena._core.contexts.spsa.adapters.tunable_manifest_preflight import (
    TUNABLE_HANDSHAKE_FILENAME,
    run_clear_hash_role_preflight,
    run_tunable_manifest_preflight,
    validate_sealed_tunable_evidence,
)
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.domain.pair_identity import canonical_pair_ids
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, SpsaGamePayload
from shogiarena._core.contexts.spsa.domain.versioned_rng import (
    SPSA_RNG_SCHEMA,
    HmacSha256Rng,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.content_hashing import sha256_file
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256
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

    dashboard_profiles: tuple[DashboardProfile, ...] = ("spsa",)
    # Class-level default so teardown stays safe for instances built without __init__.
    _derived_json_scheduler: SpsaDerivedJsonScheduler | None = None

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
        self.api_host = str(self.config.dashboard.api_host)
        self.api_port = int(self.config.dashboard.api_port)
        self.num_workers = max(1, self.config.num_workers)
        self.run_dir: Path | None = storage.run_dir

        self._run_metadata_service = RunMetadataPersistenceService()
        self._session_context_factory = SessionContextFactory()
        self._engine_factory_service = engine_factory_service
        self._frozen_run_config_payload: JsonObject | None = None
        self._remote_worker_bundle: WorkerBundleBuildResult | None = None
        self._derived_json_scheduler: SpsaDerivedJsonScheduler | None = None

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
        cleanup_error: BaseException | None = None
        terminal_error: BaseException | None = None
        # Stop background projections before the terminal commit so a stale in-flight
        # projection cannot overwrite the terminal view.
        if self._derived_json_scheduler is not None:
            await self._derived_json_scheduler.drain()
        if self._state.db_service is not None:
            try:
                self._state.db_service.close()
            except BaseException as exc:  # noqa: BLE001 - terminal evidence must survive cleanup failure
                cleanup_error = exc
            self._state.db_service = None
        if cleanup_error is not None:
            self._state.terminal_status = "failed"
            if self._state.terminal_reason in {None, "completed", "early_stopped"}:
                self._state.terminal_reason = "failed"
                self._state.terminal_resumable = False
        try:
            if self._state.ledger_runtime is not None and self._state.terminal_reason is not None:
                self._state.ledger_runtime.commit_terminal(
                    status=self._state.terminal_status or "failed",
                    reason=self._state.terminal_reason,
                    resumable=self._state.terminal_resumable,
                )
                if self._state.ledger is not None:
                    # terminal.json / completed.flag はこの commit を信号にする外部契約なので、
                    # それらを書く前に WAL を畳んで durable にする。
                    self._state.ledger.checkpoint()
                self._project_derived_json()
                terminal_payload = self._state.ledger_runtime.terminal_payload()
                if terminal_payload is not None:
                    write_json_atomic(self.storage.run_dir / "spsa" / "terminal.json", terminal_payload)
                completion_payload = self._state.ledger_runtime.completion_status_payload(
                    cleanup_error=None if cleanup_error is None else str(cleanup_error)
                )
                if completion_payload is not None:
                    write_json_atomic(self.storage.run_dir / "completion_status.json", completion_payload)
                    if completion_payload.get("status") == "clean":
                        try:
                            (self.storage.run_dir / "completed.flag").touch()
                        except OSError as exc:
                            logger.warning("Failed to create completed.flag after SPSA terminal commit: %s", exc)
        except BaseException as exc:  # noqa: BLE001 - always close the ledger after terminalization attempt
            terminal_error = exc
        finally:
            if self._derived_json_scheduler is not None:
                self._derived_json_scheduler.close()
                self._derived_json_scheduler = None
            if self._state.ledger is not None:
                self._state.ledger.close()
                self._state.ledger = None
                self._state.ledger_runtime = None
                self._state.observation_ledger = None
        if cleanup_error is not None:
            raise cleanup_error
        if terminal_error is not None:
            raise terminal_error

    def _prepare_interrupted_stop(self, is_cancelled: bool) -> None:
        reason = RunTerminationReason.CANCELLED if is_cancelled else RunTerminationReason.RUNTIME_ERROR
        self.write_interrupted_run_health(reason, is_provisional=True)

    async def prepare_run_dir(self) -> None:
        if (
            self.run_dir is not None
            and not self._run_options.should_skip_resume
            and (self.run_dir / "spsa" / "ledger.sqlite3").is_file()
        ):
            recover_spsa_ledger(self.run_dir)
        self.config.run_seed = _resolve_spsa_run_seed(
            configured_seed=self.config.run_seed,
            run_dir=self.run_dir,
            should_skip_resume=bool(self._run_options.should_skip_resume),
            experiment_name=self.config.experiment_name,
        )
        self.run_dir, self._frozen_run_config_payload = prepare_spsa_run_directory(
            run_dir=self.run_dir,
            should_skip_resume=bool(self._run_options.should_skip_resume),
            config_payload=self.config.model_dump(mode="json"),
            run_metadata_service=self._run_metadata_service,
            cleanup_run_dir=self.cleanup_run_dir,
        )

    async def init_services(self) -> None:
        assert self.run_dir is not None
        db = self.storage.db_service()
        db.ensure_schema()
        self._state.db_service = db
        if self._state.observation_ledger is None:
            raise RuntimeError("SPSA ledger must be initialized before game.db reconciliation")
        run_id = str(self.config.experiment_name or self.storage.run_dir.name)
        self._state.observation_ledger.reconcile(run_id=run_id, source=db)
        self._project_derived_json()
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
        assert self.run_dir is not None
        self._init_dashboard_services()
        summary_payload = await build_spsa_dashboard_summary_payload(
            summary_service=self._state.spsa_summary_service,
            engine_metadata=self.engine_metadata,
            run_dir=self.run_dir,
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
            experiment_initial_params=self._state.experiment_initial_params,
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
        if self._state.ledger_runtime is None:
            raise ValueError("SPSA runner requires ledger runtime before creating orchestrator")
        return create_spsa_orchestrator(
            config=self.config,
            session_context=session_context,
            hooks=hooks,
            db_service=self._state.db_service,
            engine_factory_service=self._engine_factory_service,
            # See TournamentRunner._create_orchestrator: a summary refresh is dashboard-only work
            # whose cost grows with the run, so it must not stay wired when the dashboard is off.
            summary_updater=self._update_dashboard if self.is_dashboard_enabled else None,
            api_server=self.api_server,
            ledger_runtime=self._state.ledger_runtime,
            derived_json_scheduler=self._derived_json_scheduler,
            remote_worker_bundle=self._remote_worker_bundle,
        )

    async def prepare_domain(self) -> None:
        assert self.run_dir is not None
        state_path = self.run_dir / "state.json"
        ledger_path = self.run_dir / "spsa" / "ledger.sqlite3"
        if state_path.exists() and not ledger_path.is_file():
            raise ValueError(
                "Legacy JSON-only SPSA run has no spsa/ledger.sqlite3; "
                "existing artifacts were not migrated. Use --no-resume to start a fresh run."
            )
        if ledger_path.is_file():
            with open_spsa_ledger(self.run_dir, read_only=True):
                pass
        frozen_payload = self._frozen_run_config_payload
        if frozen_payload is None:
            raise RuntimeError("inputs-only manifest must be written before prepare_domain")
        is_resume = ledger_path.is_file()
        self._remote_worker_bundle = prepare_remote_worker_bundle(
            run_dir=self.run_dir,
            instance_pool=getattr(self, "instance_pool", None),
            is_resume=is_resume,
        )
        remote_worker_provenance = worker_bundle_provenance(self._remote_worker_bundle)
        provenance_overrides: JsonObject = {}
        if remote_worker_provenance is not None:
            provenance_overrides["_remote_worker_bundle"] = remote_worker_provenance
        tunable_evidence: JsonObject
        if is_resume:
            raw_evidence = self.storage.read_json(f"spsa/{TUNABLE_HANDSHAKE_FILENAME}")
            if raw_evidence is None:
                raise ValueError(
                    "SPSA resume requires sealed spsa/tunable_handshake.json; start a fresh run with --no-resume"
                )
            space, tunable_evidence = validate_sealed_tunable_evidence(
                space_path=self.config.space_path,
                normalized_space_path=self.run_dir / "spsa" / "space.normalized.json",
                evidence=raw_evidence,
                clear_hash_required=self.config.variants.apply.is_clear_hash_enabled,
            )
            if self.config.variants.apply.is_clear_hash_enabled and tunable_evidence.get("clear_hash_engines") is None:
                if self.instance_pool is None:
                    raise RuntimeError("SPSA Clear Hash preflight requires an instance pool")
                await run_clear_hash_role_preflight(
                    config=self.config,
                    engine_factory_service=self._engine_factory_service,
                    instance_pool=self.instance_pool,
                )
        else:
            self._materialize_new_engine_configs()
            if self.instance_pool is None:
                raise RuntimeError("SPSA tunable manifest preflight requires an instance pool")
            space, tunable_evidence = await run_tunable_manifest_preflight(
                config=self.config,
                run_dir=self.run_dir,
                engine_factory_service=self._engine_factory_service,
                instance_pool=self.instance_pool,
            )
        initial_params = space.to_param_entries()
        if not initial_params:
            raise ValueError("SPSA space spec has no parameters")
        self._state.experiment_initial_params = {entry.name: float(entry.value) for entry in initial_params}
        target_option_names = tuple(entry.engine_option_name for entry in initial_params if not entry.is_not_used)
        fixed_option_evidence: JsonObject
        if is_resume:
            raw_fixed_option_evidence = self.storage.read_json(f"spsa/{FIXED_OPTION_PREFLIGHT_FILENAME}")
            if raw_fixed_option_evidence is None:
                raise ValueError(
                    "SPSA resume requires sealed spsa/fixed_option_preflight.json; start a fresh run with --no-resume"
                )
            fixed_option_evidence = validate_sealed_fixed_option_evidence(
                evidence=raw_fixed_option_evidence,
                target_option_names=target_option_names,
            )
        else:
            if self.instance_pool is None:
                raise RuntimeError("SPSA fixed-option preflight requires an instance pool")
            fixed_option_report = await run_verified_spsa_fixed_option_preflight(
                engines=self.config.tuned,
                target_option_names=target_option_names,
                run_dir=self.run_dir,
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
                instance_pool=self.instance_pool,
            )
            fixed_option_evidence = fixed_option_report.to_json()
        space_digest = canonical_sha256(space.to_json())
        with open(self.config.start_sfens_path, encoding="utf-8") as handle:
            validation_sfens = [line.rstrip() for line in handle if line.strip()]
        if not validation_sfens:
            raise RuntimeError("No SFENs available")
        run_id = str(self.config.experiment_name or self.run_dir.name)
        persisted_resume_hash: str | None = None
        resume_disposition = "dispatch"
        validated_resume_config: SpsaRunConfig | None = None
        if is_resume:
            manifest = self.storage.read_json("manifest.json")
            if manifest is None or manifest.get("status") != "provenance_sealed":
                raise ValueError("SPSA resume requires a sealed manifest.json")
            manifest_hashes = manifest.get("hashes")
            if not isinstance(manifest_hashes, dict):
                raise ValueError("SPSA resume manifest hashes are missing")
            current_input_hashes = self._run_metadata_service.build_sealed_hashes(
                inputs_config_payload=frozen_payload,
                resolved_config_payload=frozen_payload,
            )
            if manifest.get("inputs_hash") != current_input_hashes.config_fingerprint:
                raise ValueError("SPSA resume config does not match sealed manifest inputs")
            if manifest_hashes.get("schedule_hash") != current_input_hashes.schedule_hash:
                raise ValueError("SPSA resume schedule does not match sealed manifest")
            raw_resume_hash = manifest_hashes.get("resume_hash")
            if not isinstance(raw_resume_hash, str) or not raw_resume_hash:
                raise ValueError("SPSA resume manifest has no resume_hash")
            persisted_resume_hash = raw_resume_hash
            with tempfile.TemporaryDirectory(prefix="shogiarena-spsa-provenance-") as temp_dir:
                staged_run_dir = Path(temp_dir)
                staged_config = self.config.model_copy(deep=True)
                external_config_bytes = {
                    Path(engine.engine_path): Path(engine.engine_path).read_bytes()
                    for engine in [*staged_config.baseline, *staged_config.tuned]
                    if engine.engine_path is not None
                    and Path(engine.engine_path).suffix.lower() in {".yaml", ".yml"}
                    and Path(engine.engine_path).is_file()
                }
                materialize_spsa_engine_configs(
                    config=staged_config,
                    run_dir=staged_run_dir,
                    artifact_resolver=self._engine_factory_service.artifact_resolver,
                    published_run_dir=self.run_dir,
                )
                for engine in [*staged_config.baseline, *staged_config.tuned]:
                    if engine.engine_path is None:
                        continue
                    staged_path = Path(engine.engine_path)
                    try:
                        relative_path = staged_path.relative_to(staged_run_dir)
                    except ValueError:
                        continue
                    existing_path = self.run_dir / relative_path
                    if not existing_path.is_file() or existing_path.read_bytes() != staged_path.read_bytes():
                        raise ValueError("SPSA resolved engine provenance does not match sealed run inputs")
                    engine.engine_path = existing_path
                staged_input_root = staged_run_dir / "inputs" / "engine_configs"
                for staged_path in staged_input_root.rglob("*"):
                    if not staged_path.is_file():
                        continue
                    relative_path = staged_path.relative_to(staged_run_dir)
                    existing_path = self.run_dir / relative_path
                    if not existing_path.is_file() or existing_path.read_bytes() != staged_path.read_bytes():
                        raise ValueError("SPSA resolved engine config bytes do not match sealed run inputs")
                staged_hashes = self._run_metadata_service.build_sealed_hashes(
                    inputs_config_payload=frozen_payload,
                    resolved_config_payload={
                        **staged_config.model_dump(mode="json"),
                        "_spsa_tunable_handshake": tunable_evidence,
                        "_spsa_fixed_option_preflight": fixed_option_evidence,
                        **provenance_overrides,
                    },
                )
                if staged_hashes.resume_hash != persisted_resume_hash:
                    raise ValueError("SPSA resolved provenance does not match sealed resume hash")
                for source_path, verified_bytes in external_config_bytes.items():
                    if not source_path.is_file() or source_path.read_bytes() != verified_bytes:
                        raise ValueError("SPSA external engine config changed during resume validation")
                validated_resume_config = staged_config
            with open_spsa_ledger(self.run_dir, read_only=True) as read_only_ledger:
                read_only_runtime = SpsaLedgerRuntime(read_only_ledger.connection, run_id=run_id)
                read_only_runtime.validate_resume_authority(
                    resume_hash=persisted_resume_hash,
                    space_digest=space_digest,
                    params=initial_params,
                )
                self._validate_rederived_assignments(
                    connection=read_only_ledger.connection,
                    runtime=read_only_runtime,
                    params=initial_params,
                    sfens=validation_sfens,
                )
                resume_disposition = read_only_runtime.resume_disposition(total_updates=int(self.config.num_updates))
                read_only_observations = SpsaObservationLedger(read_only_ledger.connection)
                read_only_db = self.storage.read_only_db_service()
                try:
                    read_only_db.ensure_schema()
                    read_only_observations.validate_reconciliation(
                        run_id=run_id,
                        source=read_only_db,
                    )
                finally:
                    read_only_db.close()
        if validated_resume_config is not None:
            self.config = validated_resume_config
        sealed = self._run_metadata_service.seal_provenance_manifest(
            run_dir=self.run_dir,
            inputs_config_payload=frozen_payload,
            resolved_config_payload={
                **self.config.model_dump(mode="json"),
                "_spsa_tunable_handshake": tunable_evidence,
                "_spsa_fixed_option_preflight": fixed_option_evidence,
                **provenance_overrides,
            },
            package_name="shogiarena",
        )
        self._state.sealed_schedule_hash = sealed.hashes.schedule_hash
        self._state.sealed_resume_hash = sealed.hashes.resume_hash
        resume_hash = self._state.sealed_resume_hash
        if resume_hash is None:
            raise RuntimeError("SPSA resume hash must be sealed before ledger initialization")
        if persisted_resume_hash is not None and resume_hash != persisted_resume_hash:
            raise ValueError("SPSA resolved provenance does not match sealed resume hash")
        # run を所有する長命の writer。ここだけが WAL を宣言する。
        ledger = open_spsa_ledger(self.run_dir, use_write_ahead_logging=True)
        ledger_runtime = SpsaLedgerRuntime(ledger.connection, run_id=run_id)
        ledger_runtime.initialize_run(
            resume_hash=resume_hash,
            space_digest=space_digest,
            sealed_run_seed=_require_run_seed(self.config),
            contract={
                "resume_hash": resume_hash,
                "space_digest": space_digest,
                "config": self.config.model_dump(mode="json"),
            },
            params=initial_params,
        )
        if is_resume:
            writable_disposition = ledger_runtime.resume_disposition(total_updates=int(self.config.num_updates))
            if writable_disposition != resume_disposition:
                raise RuntimeError("SPSA resume disposition changed during validation")
        self._state.has_resumable_terminal = resume_disposition == "resumable"
        self._state.is_finalization_replay = resume_disposition == "finalize"
        completed_updates = ledger_runtime.completed_updates()
        current_theta = ledger_runtime.current_theta()
        params, sfens, update_items = prepare_spsa_domain_inputs(
            config=self.config,
            run_dir=self.run_dir,
            schedule_hash=self._state.sealed_schedule_hash,
            resume_hash=resume_hash,
            authority_completed_updates=completed_updates,
            authority_theta=current_theta,
            is_finalization_replay=self._state.is_finalization_replay,
            space=space,
            fixed_option_preflight_completed=True,
        )
        self._state.params = params
        self._state.sfens = sfens
        self._state.update_items = update_items
        self._state.ledger = ledger
        self._state.ledger_runtime = ledger_runtime
        self._state.observation_ledger = SpsaObservationLedger(ledger.connection)
        self._derived_json_scheduler = SpsaDerivedJsonScheduler(
            run_dir=self.run_dir,
            run_id=run_id,
            min_interval_s=self.config.derived_json_min_interval_s,
        )
        self._project_accepted_best()

    def _materialize_new_engine_configs(self) -> None:
        assert self.run_dir is not None
        published_input_root = self.run_dir / "inputs" / "engine_configs"
        with tempfile.TemporaryDirectory(
            prefix=".spsa-stage-",
            dir=self.run_dir.parent,
        ) as temp_dir:
            staged_run_dir = Path(temp_dir)
            staged_config = self.config.model_copy(deep=True)
            materialize_spsa_engine_configs(
                config=staged_config,
                run_dir=staged_run_dir,
                artifact_resolver=self._engine_factory_service.artifact_resolver,
                published_run_dir=self.run_dir,
            )
            staged_input_root = staged_run_dir / "inputs" / "engine_configs"
            for engine in [*staged_config.baseline, *staged_config.tuned]:
                if engine.engine_path is None:
                    continue
                staged_path = Path(engine.engine_path)
                try:
                    relative_path = staged_path.relative_to(staged_run_dir)
                except ValueError:
                    continue
                engine.engine_path = self.run_dir / relative_path
            has_published_archive = published_input_root.exists() or published_input_root.is_symlink()
            if staged_input_root.exists():
                if has_published_archive:
                    if _archive_snapshot(staged_input_root) != _archive_snapshot(published_input_root):
                        raise ValueError("SPSA unpublished engine archive does not match resolved provenance")
                else:
                    published_input_root.parent.mkdir(parents=True, exist_ok=True)
                    staged_input_root.replace(published_input_root)
            elif has_published_archive:
                raise ValueError("SPSA unpublished engine archive has no resolved provenance")
            self.config = staged_config

    def _validate_rederived_assignments(
        self,
        *,
        connection: sqlite3.Connection,
        runtime: SpsaLedgerRuntime,
        params: list[ParamEntry],
        sfens: list[str],
    ) -> None:
        helper = object.__new__(SpsaOrchestratorUpdateMixin)
        helper.config = self.config
        helper.run_dir = self.run_dir
        updates = connection.execute(
            """
            SELECT update_idx, theta_before_json, schedule_json, state, ltc_required
            FROM updates WHERE run_id = ? ORDER BY update_idx
            """,
            (runtime.run_id,),
        ).fetchall()
        for update_idx_raw, theta_json, schedule_json, state_raw, ltc_required_raw in updates:
            update_idx = int(update_idx_raw)
            assignments = connection.execute(
                """
                SELECT pair_id, assignment_kind FROM pair_assignments
                WHERE run_id = ? AND update_idx = ? ORDER BY pair_id
                """,
                (runtime.run_id, update_idx),
            ).fetchall()
            theta = json.loads(str(theta_json))
            schedule = json.loads(str(schedule_json or "{}"))
            if not isinstance(theta, dict) or not isinstance(schedule, dict):
                raise RuntimeError(f"SPSA update {update_idx} replay inputs are invalid")
            ltc_config = getattr(self.config, "ltc_regression", None)
            ltc_pair_count = int(ltc_config.total_pairs) if bool(ltc_required_raw) and ltc_config is not None else 0
            expected_pair_ids = {
                "SPSA": list(
                    canonical_pair_ids(
                        kind="SPSA",
                        update_idx=update_idx,
                        count=int(self.config.pairs_per_update),
                    )
                ),
                "LTC": list(canonical_pair_ids(kind="LTC", update_idx=update_idx, count=ltc_pair_count)),
            }
            if schedule.get("expected_pair_ids") != expected_pair_ids:
                raise RuntimeError(f"SPSA update {update_idx} expected pair set does not match sealed config")
            if not assignments:
                if str(state_raw) != "PLANNED":
                    raise RuntimeError(f"SPSA update {update_idx} has no durable pair assignments")
                continue
            replay_params = helper._clone_param_entries(params)
            for entry in replay_params:
                if not entry.is_not_used:
                    entry.value = float(theta[entry.name])
            flips = {
                entry.name: (
                    1
                    if helper._make_rng(
                        domain="spsa.flip",
                        update_idx=update_idx,
                        parameter_id=entry.name,
                    ).randint(0, 1)
                    else -1
                )
                for entry in replay_params
                if not entry.is_not_used
            }
            stored_flips = schedule.get("flips")
            if stored_flips != flips:
                raise RuntimeError(f"SPSA update {update_idx} stored flips do not match sealed RNG")
            c_values = schedule.get("c")
            if not isinstance(c_values, dict):
                raise RuntimeError(f"SPSA update {update_idx} schedule has no c values")
            plus = helper._clone_param_entries(replay_params)
            minus = helper._clone_param_entries(replay_params)
            for plus_entry, minus_entry in zip(plus, minus, strict=True):
                if plus_entry.is_not_used:
                    continue
                offset = float(c_values[plus_entry.name]) * int(flips[plus_entry.name])
                plus_entry.value = max(float(plus_entry.min), min(float(plus_entry.max), plus_entry.value + offset))
                minus_entry.value = max(
                    float(minus_entry.min),
                    min(float(minus_entry.max), minus_entry.value - offset),
                )
            plus_options, minus_options = helper._build_engine_option_maps_for_pair(
                plus,
                minus,
                update_idx=update_idx,
            )
            if self.config.is_crn_enabled:
                opening_indices = [pair_idx % len(sfens) for pair_idx in range(int(self.config.pairs_per_update))]
            else:
                opening_indices = [
                    helper._make_rng(
                        domain="spsa.opening",
                        update_idx=update_idx,
                        pair_idx=pair_idx,
                    ).randrange(len(sfens))
                    for pair_idx in range(int(self.config.pairs_per_update))
                ]
            ltc_total_pairs = ltc_pair_count
            ltc_openings = [
                helper._make_rng(
                    domain="spsa.opening",
                    update_idx=update_idx,
                    pair_idx=pair_idx,
                    counter=1,
                ).randrange(len(sfens))
                for pair_idx in range(ltc_total_pairs)
            ]
            for pair_id_raw, assignment_kind_raw in assignments:
                pair_id = str(pair_id_raw)
                assignment_kind = str(assignment_kind_raw)
                try:
                    pair_idx = int(pair_id.rsplit("-p", 1)[1])
                except (IndexError, ValueError) as exc:
                    raise RuntimeError(f"Invalid SPSA pair id: {pair_id}") from exc
                if assignment_kind == "SPSA":
                    if pair_idx >= len(opening_indices):
                        raise RuntimeError(f"SPSA pair is outside configured range: {pair_id}")
                    opening_idx = opening_indices[pair_idx]
                    runtime.assign_pair(
                        update_idx=update_idx,
                        pair_id=pair_id,
                        assignment_kind="SPSA",
                        opening={
                            "opening_idx": opening_idx,
                            "start_sfen": sfens[opening_idx],
                            "batch_idx": pair_idx,
                        },
                        color_assignment=_pair_color_assignment(pair_id),
                        flips=flips,
                        rounding_samples={
                            "plus_options": plus_options,
                            "minus_options": minus_options,
                        },
                    )
                elif assignment_kind == "LTC":
                    if pair_idx >= len(ltc_openings):
                        raise RuntimeError(f"SPSA LTC pair is outside configured range: {pair_id}")
                    runtime.assign_pair(
                        update_idx=update_idx,
                        pair_id=pair_id,
                        assignment_kind="LTC",
                        opening={
                            "opening_idx": pair_idx,
                            "start_sfen": sfens[ltc_openings[pair_idx]],
                        },
                        color_assignment=_pair_color_assignment(pair_id),
                        flips={},
                        rounding_samples={},
                    )
                else:
                    raise RuntimeError(f"Unsupported SPSA assignment kind: {assignment_kind}")

    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort[Any]) -> None:
        assert self._state.params is not None and self._state.sfens is not None and self._state.update_items is not None
        if not isinstance(orchestrator, SpsaOrchestrator):
            raise TypeError("SpsaRunner expected SpsaOrchestrator")
        orchestrator.set_work_items(self._state.update_items, self._state.params, self._state.sfens)
        if self._state.ledger_runtime is None:
            raise RuntimeError("SPSA ledger runtime is unavailable during resume restoration")
        baseline_idx, baseline_theta = self._state.ledger_runtime.accepted_baseline()
        baseline_params = orchestrator._clone_param_entries(self._state.params)
        for entry in baseline_params:
            if not entry.is_not_used:
                entry.value = baseline_theta[entry.name]
        orchestrator._store_ltc_baseline(baseline_params, baseline_idx)

    async def run_orchestrator(
        self,
        orchestrator: OrchestratorPort[Any],
        run_coro: Awaitable[Any],
    ) -> None:
        """Invalidate a resumable terminal immediately before task dispatch."""

        if self._state.is_finalization_replay:
            close = getattr(run_coro, "close", None)
            if callable(close):
                close()
            return None
        if self._state.has_resumable_terminal:
            if self._state.ledger_runtime is None:
                raise RuntimeError("SPSA ledger runtime is unavailable for terminal invalidation")
            self._invalidate_terminal_artifacts()
            self._state.ledger_runtime.invalidate_resumable_terminal()
            self._state.has_resumable_terminal = False
        return await super().run_orchestrator(orchestrator, run_coro)

    def get_dashboard_params(self) -> tuple[Path, str, int, int] | None:
        if not self.is_dashboard_enabled:
            return None
        assert self.run_dir is not None
        return (self.run_dir, str(self.api_host), int(self.api_port), int(self.num_workers))

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
            is_persisted = persist_spsa_game_completion(
                db_service=self._state.db_service,
                event=event,
                observation_ledger=self._state.observation_ledger,
            )

        if is_persisted:
            # The derived JSON views are rebuilt from the whole run, so projecting inline here
            # blocks the loop for progressively longer as the run grows. The ledger stays
            # authoritative, so the compatibility views only need eventual freshness.
            self._request_derived_json_projection()
            self.progress.on_game_complete(
                {
                    "game_id": event.game_id,
                    "update_idx": int(payload.update_idx),
                    "phase": payload.phase,
                    "winner": int(payload.winner_code) if payload.winner_code is not None else None,
                }
            )

    def _project_derived_json(self) -> None:
        """Rebuild the compatibility JSON views synchronously.

        run 開始と terminal でのみ使う。実行中の hot path からは
        :meth:`_request_derived_json_projection` を使うこと。
        """

        if self._state.ledger_runtime is None:
            return
        run_dir = self.run_dir
        if run_dir is None:
            raise RuntimeError("SPSA run directory is unavailable for projection")
        try:
            self._state.ledger_runtime.project_derived_json(run_dir=run_dir)
        except (OSError, ValueError) as exc:
            logger.warning("SPSA ledger remains authoritative after derived JSON projection failure: %s", exc)

    def _request_derived_json_projection(self) -> None:
        """Schedule a coalesced off-loop projection of the compatibility JSON views."""

        scheduler = self._derived_json_scheduler
        if scheduler is None:
            return
        scheduler.request()

    def _project_accepted_best(self) -> None:
        runtime = self._state.ledger_runtime
        params = self._state.params
        if runtime is None or params is None:
            return
        update_idx = runtime.latest_accepted_update_idx()
        if update_idx is None:
            return
        helper = object.__new__(SpsaOrchestratorUpdateMixin)
        helper.config = self.config
        try:
            ledger_commit = runtime.accepted_best_commit(update_idx=update_idx)
            accepted_params = helper._clone_param_entries(params)
            raw_parameters = ledger_commit.get("parameters")
            if not isinstance(raw_parameters, list):
                raise ValueError("accepted-best ledger parameters must be a list")
            accepted_values: dict[str, float] = {}
            for item in raw_parameters:
                if not isinstance(item, dict):
                    raise ValueError("accepted-best ledger parameter must be an object")
                parameter_id = item.get("parameter_id")
                value = item.get("value")
                if not isinstance(parameter_id, str) or not isinstance(value, int | float) or isinstance(value, bool):
                    raise ValueError("accepted-best ledger parameter identity or value is invalid")
                accepted_values[parameter_id] = float(value)
            for entry in accepted_params:
                if entry.is_not_used:
                    continue
                if entry.name not in accepted_values:
                    raise ValueError(f"accepted-best ledger value is missing: {entry.name}")
                entry.value = accepted_values[entry.name]
            persist_accepted_best(
                run_dir=self.storage.run_dir,
                ledger_commit=ledger_commit,
                parameter_wire_values=helper._build_engine_option_map(accepted_params),
                baseline_engine_count=len(self.config.baseline),
                tuned_engine_count=len(self.config.tuned),
            )
        except (OSError, ValueError) as exc:
            logger.warning("SPSA ledger remains authoritative after accepted-best projection failure: %s", exc)

    def _invalidate_terminal_artifacts(self) -> None:
        run_dir = self.run_dir
        if run_dir is None:
            raise RuntimeError("SPSA run directory is unavailable for terminal invalidation")
        for path in (
            run_dir / "completion_status.json",
            run_dir / "completed.flag",
            run_dir / "spsa" / "terminal.json",
            run_dir / "spsa" / "terminal.provisional.json",
        ):
            path.unlink(missing_ok=True)

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
        if self._state.ledger_runtime is None:
            raise RuntimeError("SPSA ledger runtime is unavailable during finalization")
        if self._result_store is None:
            raise TypeError("SpsaRunner requires result_store")
        try:
            self._result_store.save_result(result)
        except BaseException:  # noqa: BLE001 - persist failed terminal before propagating
            self.write_interrupted_run_health(
                RunTerminationReason.RUNTIME_ERROR,
                is_provisional=True,
            )
            raise
        completed_updates = self._state.ledger_runtime.completed_updates()
        if completed_updates >= int(self.config.num_updates):
            self._state.terminal_status = "clean"
            self._state.terminal_reason = "completed"
        else:
            self._state.terminal_status = "clean"
            self._state.terminal_reason = "early_stopped"
        self._state.terminal_resumable = False
        await self.stop_services()
        await super().finalize_and_persist(None)
        self.progress.finalize({"status": "finished", "run_id": result.run_id})
        return result

    def write_interrupted_run_health(
        self,
        reason: RunTerminationReason,
        *,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        """Persist minimal interruption evidence and arm the final ledger commit."""

        if reason == RunTerminationReason.CANCELLED:
            status = "with-anomalies"
            terminal_reason = "cancelled_resumable"
            resumable = True
        else:
            status = "failed"
            terminal_reason = "failed"
            resumable = False
        if cleanup_error is not None:
            status = "failed"
        self._state.terminal_status = status
        self._state.terminal_reason = terminal_reason
        self._state.terminal_resumable = resumable
        if is_provisional or self._state.ledger_runtime is None:
            provisional: JsonObject = {
                "schema_version": 1,
                "status": status,
                "termination_reason": terminal_reason,
                "is_provisional": True,
                "resumable": resumable,
                "cleanup": {
                    "status": "failed" if cleanup_error is not None else "pending",
                    "error": cleanup_error,
                },
            }
            write_json_atomic(self.storage.run_dir / "completion_status.json", provisional)
            write_json_atomic(self.storage.run_dir / "spsa" / "terminal.provisional.json", provisional)


def _require_run_seed(config: SpsaRunConfig) -> str:
    seed = config.run_seed
    if seed is None:
        raise RuntimeError("SPSA run seed must be resolved before domain preparation")
    return seed


def _resolve_spsa_run_seed(
    *,
    configured_seed: str | None,
    run_dir: Path | None,
    should_skip_resume: bool,
    experiment_name: str | None,
) -> str:
    if should_skip_resume or run_dir is None:
        return configured_seed or secrets.token_hex(32)
    ledger_path = run_dir / "spsa" / "ledger.sqlite3"
    if not ledger_path.is_file():
        return configured_seed or secrets.token_hex(32)
    run_id = str(experiment_name or run_dir.name)
    with open_spsa_ledger(run_dir, read_only=True) as ledger:
        row = ledger.connection.execute(
            "SELECT rng_schema, sealed_run_seed FROM run_contract WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"SPSA ledger has no run contract for {run_id}")
    rng_schema, sealed_seed = (str(value) for value in row)
    if rng_schema != SPSA_RNG_SCHEMA:
        raise ValueError(f"SPSA run uses unsupported RNG schema {rng_schema!r}; start a fresh run with --no-resume")
    try:
        normalized_seed = HmacSha256Rng(
            seed_hex=sealed_seed,
            domain="spsa.retry",
            run_id=run_id,
            update_idx=0,
        ).seed_hex.lower()
    except ValueError as exc:
        raise ValueError("SPSA ledger sealed run seed is invalid; existing artifacts were not modified") from exc
    if configured_seed is not None and configured_seed != normalized_seed:
        raise ValueError("SPSA configured run_seed does not match the sealed ledger run seed")
    return normalized_seed


def _archive_snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"SPSA engine archive must be a regular directory: {root}")
    snapshot: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"SPSA engine archive must not contain symlinks: {path}")
        if path.is_dir():
            snapshot[relative_path] = ("directory", None)
        elif path.is_file():
            snapshot[relative_path] = ("file", sha256_file(path))
        else:
            raise ValueError(f"SPSA engine archive contains unsupported entry: {path}")
    return snapshot


def _pair_color_assignment(pair_id: str) -> JsonObject:
    return {
        "games": [
            {"slot": "black", "tuned_as": "black", "game_id": f"{pair_id}-black"},
            {"slot": "white", "tuned_as": "white", "game_id": f"{pair_id}-white"},
        ]
    }


__all__ = ["SpsaRunner"]
