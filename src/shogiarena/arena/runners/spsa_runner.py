from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

import rshogi

from shogiarena.arena.configs.spsa import LtcRegressionConfig, SpsaRunConfig
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.orchestrators import base_orchestrator_utils
from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator
from shogiarena.arena.orchestrators.base_orchestrator_utils import build_time_control_limits
from shogiarena.arena.orchestrators.spsa.identifiers import phase_symbol, variant_token
from shogiarena.arena.orchestrators.spsa_orchestrator import SpsaGamePayload, SpsaOrchestrator
from shogiarena.arena.runners.base_runner import BaseSessionRunner, RunOptions, serialize_rules_config
from shogiarena.arena.runners.reporting import ProgressReporter
from shogiarena.arena.runners.results import SpsaRunResult
from shogiarena.arena.runners.session_context_builder import SessionContextBuilder
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.persistence.records import (
    GameParticipationRecord,
    deserialize_participation_records,
)
from shogiarena.arena.session import (
    GameCompletionEvent,
    GameLifecycleHooks,
    LifecycleHooksBase,
    SessionContext,
    SessionStopController,
)
from shogiarena.arena.storage import RunStorage
from shogiarena.arena.tuning.param_io import ParamEntry, read_params
from shogiarena.db import ShogiRepository
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import maybe_resolve_path_option
from shogiarena.utils.common.run_paths import timestamp_slug
from shogiarena.utils.types.types import (
    EngineInfoSnapshots,
    EngineOptionsSnapshots,
    GameResult,
    JsonObject,
    JsonValue,
)
from shogiarena.web.dashboard.backend.spsa import (
    SpsaDataService,
    SpsaStore,
    SpsaSummaryService,
)

logger = logging.getLogger(__name__)
_GAME_RESULT_ERROR = GameResult.from_str("ERROR")


class SpsaAlgorithmConfig(TypedDict):
    """SPSA アルゴリズムパラメータの設定スナップショット。"""

    num_updates: int
    mobility: float
    scale: float
    a0: float
    A: float | None
    alpha: float
    gamma: float
    crn_enabled: bool
    int_rounding: str
    int_ck_floor: float
    update_mode: str
    snap_float_to_step: bool
    early_stop: object
    update_batch_size: int | None
    inflight_factor: int


class _LtcConfigPayload(TypedDict, total=False):
    """LTC リグレッション設定のシリアライズ済みペイロード。"""

    enabled: bool
    every_n_updates: int
    total_pairs: int
    time_control: dict[str, object]
    pass_criteria: dict[str, object]


def _extract_participation(record: rshogi.record.GameRecord) -> tuple[GameParticipationRecord, ...]:
    raw = record.metadata.attributes.get("_arena_participation")
    if not isinstance(raw, str) or not raw.strip():
        return ()
    return deserialize_participation_records(raw)


class SpsaRunner(BaseSessionRunner[SpsaRunResult, None]):
    dashboard_profiles = ("spsa",)
    """Runner for SPSA tuning sessions (dashboard + orchestrator lifecycle).

    Keeps scripts thin and aligns entry pattern with TournamentRunner.
    """

    class _SpsaLifecycle(LifecycleHooksBase):
        def __init__(self, runner: SpsaRunner, controller: SessionStopController) -> None:
            super().__init__(controller)
            self._runner = runner

        async def on_game_complete(self, event: GameCompletionEvent[SpsaGamePayload]) -> None:
            await self._runner._handle_game_completion(event, event.payload)

    def _build_rules_payload(self) -> JsonObject:
        return serialize_rules_config(self.config.rules)

    def _build_spsa_algorithm_config(self) -> SpsaAlgorithmConfig:
        """Build a dict of SPSA algorithm parameters for serialization."""
        cfg = self.config
        return {
            "num_updates": cfg.num_updates,
            "mobility": cfg.mobility,
            "scale": cfg.scale,
            "a0": cfg.a0,
            "A": float(cfg.A) if cfg.A is not None else None,
            "alpha": cfg.alpha,
            "gamma": cfg.gamma,
            "crn_enabled": cfg.crn_enabled,
            "int_rounding": cfg.int_rounding,
            "int_ck_floor": cfg.int_ck_floor,
            "update_mode": cfg.update_mode,
            "snap_float_to_step": cfg.snap_float_to_step,
            "early_stop": cfg.early_stop,
            "update_batch_size": (int(cfg.update_batch_size) if cfg.update_batch_size is not None else None),
            "inflight_factor": cfg.inflight_factor,
        }

    @staticmethod
    def _serialize_ltc_config(config: LtcRegressionConfig | None) -> JsonObject | None:
        if config is None or not config.enabled:
            return None

        data: JsonObject = {
            "enabled": True,
            "every_n_updates": int(config.every_n_updates),
            "total_pairs": int(config.total_pairs),
        }

        if config.time_control is not None:
            data["time_control"] = config.time_control.model_dump()
        if config.pass_criteria is not None:
            data["pass_criteria"] = config.pass_criteria.model_dump()

        return data

    def __init__(
        self,
        config: SpsaRunConfig,
        *,
        instance_pool: InstancePool | None = None,
        storage: RunStorage,
        progress_reporter: ProgressReporter | None = None,
        dashboard_enabled: bool | None = None,
        no_resume: bool = False,
    ) -> None:
        enabled = config.dashboard.enabled if dashboard_enabled is None else bool(dashboard_enabled)
        if enabled and not storage.dashboard_compatible:
            logger.warning("Dashboard disabled (storage is not dashboard-compatible)")
            enabled = False
        config.dashboard.enabled = enabled
        run_options = RunOptions(no_resume=bool(no_resume))
        super().__init__(
            instance_pool=instance_pool,
            storage=storage,
            run_options=run_options,
            progress_reporter=progress_reporter,
        )
        # Align naming with TournamentRunner
        self.config = config
        self._run_options = run_options
        self.enable_dashboard = bool(enabled)
        self.api_port = int(self.config.dashboard.api_port)
        self.num_workers = max(1, self.config.num_workers)
        self.run_dir: Path | None = storage.run_dir
        self.db_service: ArenaDBService | None = None
        # domain state prepared in prepare_domain
        self._params: list[ParamEntry] | None = None
        self._sfens: list[str] | None = None
        self._update_items: list[int] | None = None
        self._completion_lock: asyncio.Lock = asyncio.Lock()
        self._session_uuid: str = uuid.uuid4().hex
        self._session_started_at: datetime = datetime.now(tz=timezone.utc)
        self._spsa_store: SpsaStore | None = None
        self._spsa_summary_service: SpsaSummaryService | None = None
        self._spsa_data_service: SpsaDataService | None = None
        self._engine_metadata_cache: list[JsonObject] | None = None
        self._engine_metadata_runtime_sig: str | None = None

    # run() is provided by BaseSessionRunner

    def _compute_engine_time_control_specs(self) -> tuple[dict[str, str], str | None]:
        base_tc = self.config.rules.time_control
        engine_map: dict[str, str] = {}
        default_spec: str | None = None

        def apply_engine(name: str, tc_override: TimeControlLimits | None) -> None:
            nonlocal default_spec
            limits = build_time_control_limits(base_tc, tc_override)
            if limits is None:
                engine_map[name] = "-"
                if default_spec is None:
                    default_spec = "-"
                return
            spec = limits.to_spec_str()
            engine_map[name] = spec
            if default_spec is None:
                default_spec = spec

        for spec in tuple(self.config.baseline) + tuple(self.config.tuned):
            engine_name = (spec.name or "").strip() or "engine"
            apply_engine(engine_name, spec.time_control)

        return engine_map, default_spec

    def _compute_engine_instances(self) -> dict[str, str | None]:
        mapping: dict[str, str | None] = {}
        for spec in tuple(self.config.baseline) + tuple(self.config.tuned):
            name = (spec.name or "").strip() or "engine"
            inst_raw = spec.instance_id
            if inst_raw and inst_raw.strip():
                mapping[name] = inst_raw.strip()
            else:
                mapping[name] = "local"
        return mapping

    def _get_runtime_snapshots(self) -> tuple[EngineOptionsSnapshots, EngineInfoSnapshots]:
        orchestrator = self._orchestrator
        runtime_options: EngineOptionsSnapshots = {}
        runtime_info: EngineInfoSnapshots = {}
        if orchestrator is not None:
            try:
                runtime_options = orchestrator.get_engine_option_snapshots()
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                logger.debug("Failed to fetch runtime USI options from orchestrator: %s", exc, exc_info=True)
            try:
                runtime_info = orchestrator.get_engine_info_snapshots()
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                logger.debug("Failed to fetch runtime engine info from orchestrator: %s", exc, exc_info=True)
        return runtime_options, runtime_info

    @staticmethod
    def _merge_option_dicts(*items: Mapping[str, object] | None) -> dict[str, object]:
        merged: dict[str, object] = {}
        for entry in items:
            if entry:
                merged.update(entry)
        return merged

    @property
    def engine_metadata(self) -> list[JsonObject]:
        runtime_options, runtime_info = self._get_runtime_snapshots()
        try:
            runtime_sig = json.dumps(runtime_options, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - defensive fallback
            runtime_sig = None
        if (
            self._engine_metadata_cache is None
            or runtime_sig is not None
            and runtime_sig != self._engine_metadata_runtime_sig
        ):
            self._engine_metadata_cache = self._collect_engine_metadata(runtime_options, runtime_info)
            self._engine_metadata_runtime_sig = runtime_sig
        return self._engine_metadata_cache

    def _collect_engine_metadata(
        self,
        runtime_options: EngineOptionsSnapshots,
        runtime_info: EngineInfoSnapshots,
    ) -> list[JsonObject]:
        resolver = ArtifactResolver()
        base_extra_options = base_orchestrator_utils.compute_max_ply_extra_options(self.config.rules) or {}

        metadata: list[JsonObject] = []
        seen: set[str] = set()
        run_dir = self.run_dir or project_dirs.output_dir

        for engine in tuple(self.config.baseline) + tuple(self.config.tuned):
            name = (engine.name or "").strip() or "engine"
            if name in seen:
                continue
            seen.add(name)

            meta: JsonObject = {
                "name": name,
                "engine_config_path": str(engine.engine_path) if engine.engine_path else None,
            }

            resolved_engine_path: str | None = None
            art = engine.artifact
            build_overrides = engine.build_options or {}
            if art and art.strip():
                try:
                    resolved_engine_path = str(resolver.resolve(art, overrides=build_overrides))
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.debug("Failed to resolve engine binary for %s: %s", name, exc, exc_info=True)
            if resolved_engine_path:
                meta["engine_path"] = resolved_engine_path

            extra_opts: dict[str, object] = dict(engine.options) if engine.options else {}
            overlay_opts_dict: dict[str, object] = engine.load_overlay_options() or {}
            if extra_opts:
                meta["extra_options"] = dict(extra_opts)
            if overlay_opts_dict:
                meta["overlay_options"] = dict(overlay_opts_dict)

            merged = self._merge_option_dicts(
                None,
                extra_opts or None,
                overlay_opts_dict or None,
            )

            source_map: dict[str, str] = {}
            for key in extra_opts:
                source_map[str(key)] = "override"
            for key in overlay_opts_dict:
                source_map.setdefault(str(key), "overlay")
            if base_extra_options:
                for key, value in base_extra_options.items():
                    merged[key] = cast(JsonValue, value)
                    source_map.setdefault(key, "rules")

            if merged:
                meta["merged_options"] = dict(merged)
                if source_map:
                    meta["option_sources"] = source_map
                resolved_options: JsonObject = {}
                for key, value in merged.items():
                    try:
                        resolved_value = maybe_resolve_path_option(
                            key,
                            value,
                            output_dir=Path(run_dir),
                            engine_dir=project_dirs.engine_dir,
                        )
                    except (OSError, RuntimeError, ValueError, TypeError) as exc:
                        logger.debug(
                            "Failed to resolve path option %s for engine %s: %s",
                            key,
                            name,
                            exc,
                            exc_info=True,
                        )
                        resolved_value = value
                    resolved_options[key] = cast(JsonValue, resolved_value)
                meta["resolved_options"] = resolved_options

            runtime_opts = runtime_options.get(name)
            if runtime_opts:
                try:
                    meta["runtime_usi_options"] = json.loads(json.dumps(runtime_opts, ensure_ascii=False))
                except (TypeError, ValueError):
                    meta["runtime_usi_options"] = dict(runtime_opts)
            runtime_meta = runtime_info.get(name)
            if runtime_meta:
                meta["runtime_engine_info"] = dict(runtime_meta)

            metadata.append(meta)

        return metadata

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return self._SpsaLifecycle(self, controller)

    def build_session_context(self) -> SessionContext:
        if self.db_service is None:
            raise ValueError("SPSA runner requires db_service before creating session context")
        run_id = str(self.config.experiment_name or self.storage.run_dir.name)
        services = {"db": self.db_service}
        if not self._run_options.no_resume:
            loaded = SessionContext.load_from_storage(
                storage=self.storage,
                instance_pool=self.instance_pool,
                services=services,
            )
            if loaded is not None:
                return loaded
        return SessionContextBuilder.for_spsa(
            storage=self.storage,
            num_workers=self.num_workers,
            instance_pool=self.instance_pool,
            run_id=run_id,
            experiment_name=str(self.config.experiment_name or ""),
            dashboard_enabled=bool(self.enable_dashboard),
            no_resume=bool(self._run_options.no_resume),
            session_uuid=self._session_uuid,
            services=services,
        )

    async def _stop_additional_services(self) -> None:
        if self.db_service is not None:
            self.db_service.close()
            self.db_service = None

    # --- Helper steps used by run() --------------------------------------
    async def prepare_run_dir(self) -> None:
        rd = self.run_dir or (project_dirs.output_dir / "spsa" / "exp" / timestamp_slug())
        rd.mkdir(parents=True, exist_ok=True)
        # No-resume handling: cleanup known artifacts for a fresh start
        if self._run_options.no_resume:
            BaseSessionRunner.cleanup_run_dir(
                rd,
                files=[
                    "game.db",
                    "index.html",
                    "data/shogi-board.js",
                    "data/arena_port.js",
                ],
                dirs=["spsa", "static", "html"],
            )
        self.run_dir = rd

    async def init_services(self) -> None:
        assert self.run_dir is not None
        self.db_service = self.storage.db_service()
        self.db_service.ensure_schema_compatibility()
        logger.debug("Services initialized (DB)")
        self._init_dashboard_services()

    def _init_dashboard_services(self) -> None:
        if not self.enable_dashboard or not self.storage.dashboard_compatible:
            return
        if self.run_dir is None or self.db_service is None:
            return

        def ensure_shogidb() -> None:
            if self.db_service is None:
                return
            self.db_service.ensure_schema_compatibility()

        def get_shogidb() -> ShogiRepository | None:
            if self.db_service is None:
                return None
            try:
                return self.db_service.get_shogidb()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Failed to open ShogiRepository for SPSA dashboard: %s", exc, exc_info=True)
                return None

        if self._spsa_store is None:
            self._spsa_store = SpsaStore(
                run_dir=self.run_dir,
                db_path=self.storage.run_dir / "game.db",
                ensure_shogidb=ensure_shogidb,
                shogidb_supplier=get_shogidb,
            )
        if self._spsa_store is not None and self._spsa_summary_service is None:
            self._spsa_summary_service = SpsaSummaryService(store=self._spsa_store)
        if self._spsa_store is not None and self._spsa_data_service is None:
            self._spsa_data_service = SpsaDataService(
                store=self._spsa_store,
                ensure_shogidb=ensure_shogidb,
                shogidb_supplier=get_shogidb,
            )

    async def _update_dashboard(self) -> None:
        if not self.api_server:
            return
        self._init_dashboard_services()
        summary_payload: JsonObject | None = None
        if self._spsa_summary_service is not None:
            summary_payload = cast(JsonObject, await asyncio.to_thread(self._spsa_summary_service.compute_summary))
            summary_payload.setdefault("tournamentType", "spsa")
            summary_payload.setdefault("mode", "spsa")
            summary_payload["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
            summary_payload["enginesMeta"] = self.engine_metadata
        if summary_payload is not None:
            self.api_server.broadcast_summary_update(summary_payload, source="spsa")

    async def seed_initial_summary(self) -> None:
        assert self.run_dir is not None
        self._init_dashboard_services()
        engines_list: list[str] = []
        if self.config.baseline:
            engines_list.append(str(self.config.baseline[0].name or "baseline"))
        if self.config.tuned:
            engines_list.append(str(self.config.tuned[0].name or "tuned"))

        engine_tc_map, default_tc_spec = self._compute_engine_time_control_specs()
        engine_instances = self._compute_engine_instances()

        seed_summary: JsonObject = {
            "tournamentType": "spsa",
            "mode": "spsa",
            "flipPolicy": None,
            "numEngines": len(engines_list),
            "runDir": str(self.run_dir),
            "leaderboard": [],
            "engines": engines_list,
            "enginesMeta": self.engine_metadata,
            "engineTimeControls": engine_tc_map,
            "defaultTimeControl": default_tc_spec,
            "engineInstances": engine_instances,
            "engineStats": {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list},
            "pairResults": {},
            "timestamp": datetime.now().isoformat(),
            "games": {
                "completed": 0,
                "total": 0,
                "cancelled": 0,
            },
            "btd": {
                "ratings": {name: {"elo": 0.0, "se": 0.0} for name in engines_list},
                "anchor": engines_list[0] if engines_list else None,
                "gamma_elo": 0.0,
                "gamma_elo_se": 0.0,
                "draw_eq": 0.5,
                "draw_eq_se": 0.0,
                "rating_cov": {name: {} for name in engines_list},
            },
        }

        rules_payload = self._build_rules_payload()
        if rules_payload:
            seed_summary["rules"] = rules_payload
            seed_summary["initialPositions"] = rules_payload.get("initial_positions")
            seed_summary["repetitionOccurrencesToDraw"] = rules_payload.get("repetition_occurrences_to_draw")
            init_pos = rules_payload.get("initial_positions")
            init_pos_flip = init_pos.get("flip_policy") if isinstance(init_pos, dict) else None
            seed_summary["flipPolicy"] = rules_payload.get("flip_policy") or init_pos_flip or seed_summary["flipPolicy"]
            seed_summary["tournamentConfig"] = {"rules": rules_payload}
            sprt_config = rules_payload.get("sprt")
            if sprt_config:
                seed_summary.setdefault("sprt", sprt_config)

        # Build SPSA algorithm config for Rules tab display
        ltc_meta = self._serialize_ltc_config(self.config.ltc_regression)
        spsa_algo_config = self._build_spsa_algorithm_config()
        spsa_algo_dict: JsonObject = {**spsa_algo_config, "ltc_regression": ltc_meta}
        seed_summary["spsaConfig"] = spsa_algo_dict

        if self.api_server is not None:
            self.api_server.broadcast_summary_update(seed_summary, source="spsa")

        # Write SPSA meta for API consumption
        spsa_dir = self.run_dir / "spsa"
        spsa_dir.mkdir(parents=True, exist_ok=True)
        # 初期パラメータのスナップショット（ベースライン比較用）
        # Store internal float values (not quantized to int) for accurate baseline comparison
        initial_params_map: dict[str, float] = {}
        if self._params is not None:
            for p in self._params:
                initial_params_map[p.name] = float(p.v)

        meta: JsonObject = {
            "type": "spsa",
            "experiment_name": self.config.experiment_name,
            "parameters_path": str(self.config.parameters_path),
            "start_sfens_path": str(self.config.start_sfens_path),
            **spsa_algo_dict,
            "num_workers": int(self.num_workers),
            "initial_params": initial_params_map,
            "session_uuid": self._session_uuid,
            "session_started_at": self._session_started_at.isoformat(),
        }

        meta["engine_time_controls"] = engine_tc_map
        meta["default_time_control"] = default_tc_spec
        meta["engines"] = engines_list
        meta["engine_instances"] = engine_instances
        meta["engine_stats"] = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list}
        meta["enginesMeta"] = self.engine_metadata

        (spsa_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> SpsaOrchestrator:
        if self.db_service is None:
            raise ValueError("SPSA runner requires db_service before creating orchestrator")
        if session_context is None:
            raise ValueError("SPSA runner requires session context")
        orch = SpsaOrchestrator(
            config=self.config,
            session=session_context,
            hooks=hooks,
            summary_updater=self._update_dashboard,
            api_server=self.api_server,
        )
        return orch

    async def prepare_domain(self) -> None:
        # Ensure working copy of parameters under run_dir/spsa/params and point config there
        assert self.run_dir is not None
        orig_params_path = Path(self.config.parameters_path)
        run_params_dir = self.run_dir / "spsa" / "params"
        run_params_dir.mkdir(parents=True, exist_ok=True)
        run_params_path = run_params_dir / orig_params_path.name
        if not run_params_path.exists():
            run_params_path.write_text(Path(orig_params_path).read_text(encoding="utf-8"), encoding="utf-8")
        # Repoint config to the working params file
        self.config.parameters_path = str(run_params_path)
        # Record a run state marker once the SPSA domain is prepared
        state_path = self.run_dir / "run_state.json"
        if not state_path.exists():
            now = datetime.now(timezone.utc).isoformat()
            state = {
                "type": "spsa",
                "created_at": now,
                "updated_at": now,
                "finished": False,
                "completed_updates": 0,
                "total_updates": self.config.num_updates,
            }
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Read parameters
        params = read_params(self.config.parameters_path)
        if not params:
            raise ValueError("SPSA parameters file is empty or unreadable")
        # Read SFENs
        with open(self.config.start_sfens_path, encoding="utf-8") as f:
            sfens = [line.rstrip() for line in f if line.strip()]
        if not sfens:
            raise RuntimeError("No SFENs available")
        # Build update items strictly from config (no fallback)
        n = self.config.num_updates
        if n <= 0:
            raise ValueError("SPSA requires a positive 'num_updates'")
        update_items = list(range(1, n + 1))

        self._params = params
        self._sfens = sfens
        self._update_items = update_items

    async def run_pre_orchestration_hooks(self, orchestrator: BaseOrchestrator) -> None:
        assert self._params is not None and self._sfens is not None and self._update_items is not None
        if not isinstance(orchestrator, SpsaOrchestrator):
            raise TypeError("SpsaRunner expected SpsaOrchestrator")
        orchestrator.set_update_items(self._update_items, self._params, self._sfens)

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:
        if not self.enable_dashboard:
            return None
        assert self.run_dir is not None
        return (self.run_dir, int(self.api_port), int(self.num_workers))

    async def _handle_game_completion(
        self,
        event: GameCompletionEvent,
        payload: SpsaGamePayload,
    ) -> None:
        if self.db_service is None:
            raise ValueError("SPSA runner requires db_service for game completion handling")

        persisted = False
        async with self._completion_lock:
            game_info = event.game_info
            game_info.update_metadata({"game_type": "spsa"}, strict=True)
            result = game_info.result
            should_persist = result != GameResult.PAUSED
            if (
                should_persist
                and event.stop_requested
                and result
                in (
                    GameResult.PAUSED,
                    _GAME_RESULT_ERROR,
                    GameResult.INVALID,
                )
            ):
                should_persist = False

            if should_persist:
                self.db_service.append_record_list([game_info])
                participation = _extract_participation(game_info)
                if participation:
                    game_id_name = game_info.game_name
                    game_id = self.db_service.get_game_id_by_name(str(game_id_name)) if game_id_name else None
                    if game_id is not None:
                        self.db_service.record_game_participation(game_id=game_id, participation=participation)
                persisted = True

        if persisted:
            self._append_spsa_event(payload, event.game_id, game_info)
            self.progress.on_game_complete(
                {
                    "game_id": event.game_id,
                    "update_idx": int(payload.update_idx),
                    "phase": payload.phase,
                    "winner": int(payload.winner_code),
                }
            )

    def _append_spsa_event(self, payload: SpsaGamePayload, game_id: str, game_info: rshogi.record.GameRecord) -> None:
        if self.run_dir is None:
            return

        spsa_dir = self.run_dir / "spsa"
        spsa_dir.mkdir(parents=True, exist_ok=True)
        # Use update_idx-based variant_id (v000001 format) instead of hash
        tuned_vid = variant_token(payload.update_idx)
        current_vid = variant_token(0)  # Baseline is always v000000
        resolved_game_id = game_info.game_name or game_id
        black_tc = game_info.black_time_control
        white_tc = game_info.white_time_control
        tc_black = black_tc.to_spec() if black_tc is not None else None
        tc_white = white_tc.to_spec() if white_tc is not None else None
        end_time = game_info.metadata.end_date
        result = game_info.result
        variant_base = variant_token(payload.update_idx)
        variant_suffix = phase_symbol(payload.phase)
        variant_label = variant_base + variant_suffix if variant_suffix else variant_base

        event_record = {
            "event": "game_result",
            "update_idx": int(payload.update_idx),
            "winner": int(payload.winner_code),
            "tuned_as_black": bool(payload.tuned_as_black),
            "phase": payload.phase,
            "tuned_variant": tuned_vid,
            "baseline_variant": current_vid,
            "game_id": resolved_game_id or game_id,
            "variant_token": variant_base,
            "variant_label": variant_label,
            "black_player": game_info.metadata.black_player,
            "white_player": game_info.metadata.white_player,
            "initial_sfen": game_info.init_position_sfen,
            "num_moves": len(game_info.moves),
            "time_control_black": tc_black,
            "time_control_white": tc_white,
            "end_time": end_time,
            "result_code": result.value if result is not None else None,
            "ts": int(time.time() * 1000),
            "session_uuid": self._session_uuid,
            "family": payload.event_family,
            "ltc": payload.event_family == "ltc",
        }
        with open(spsa_dir / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_record, ensure_ascii=False) + "\n")

    async def finalize_and_persist(self, run_result: None) -> SpsaRunResult:
        summary: JsonObject | None = None
        if self._spsa_summary_service is not None:
            summary = cast(JsonObject, await asyncio.to_thread(self._spsa_summary_service.compute_summary))
        final_params: dict[str, float] = {}
        if self._params is not None:
            for entry in self._params:
                final_params[entry.name] = float(entry.v)
        run_dir = self.run_dir or self.storage.run_dir
        result = SpsaRunResult(
            summary=summary,
            final_params=final_params,
            run_id=str(self.config.experiment_name or run_dir.name),
            run_dir=run_dir,
            storage=self.storage,
        )
        self.progress.finalize({"status": "finished", "result": result})
        await super().finalize_and_persist(None)
        self._result_store.save_result(result)
        return result
