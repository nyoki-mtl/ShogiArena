"""N-engine native tournament runner."""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import warnings
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, TypedDict, cast

import rshogi
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shogiarena.arena.configs.tournament import (
    EngineConfig,
    GameSpec,
    TournamentResults,
    TournamentRunConfig,
    _EngineWdlCounts,
)
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.orchestrators import base_orchestrator_utils
from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator
from shogiarena.arena.orchestrators.base_orchestrator_utils import build_time_control_limits
from shogiarena.arena.orchestrators.tournament_orchestrator import TournamentOrchestrator
from shogiarena.arena.runners.base_runner import BaseSessionRunner, RunOptions, serialize_rules_config
from shogiarena.arena.runners.reporting import ProgressReporter
from shogiarena.arena.runners.reschedule_loop import RescheduleAction, RescheduleDecision, RescheduleLoop
from shogiarena.arena.runners.results import TournamentRunResult
from shogiarena.arena.runners.session_context_builder import SessionContextBuilder
from shogiarena.arena.runners.tqdm_reporter import TqdmProgressReporter
from shogiarena.arena.scheduler.game_scheduler import GameScheduler, GauntletScheduler, create_scheduler
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver
from shogiarena.arena.services.openbench import OpenBenchClient, OpenBenchClientConfig, OpenBenchError
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.persistence.records import (
    GameParticipationRecord,
    deserialize_participation_records,
)
from shogiarena.arena.services.statistics.btd import BTDEstimator
from shogiarena.arena.services.statistics.pentanomial import compute_pentanomial
from shogiarena.arena.services.statistics.rating_service import EloRatingService
from shogiarena.arena.services.statistics.sprt import Sprt, SprtStateSnapshot
from shogiarena.arena.session import (
    GameCompletionEvent,
    GameLifecycleHooks,
    LifecycleHooksBase,
    SessionContext,
    SessionStopController,
)
from shogiarena.arena.storage import RunStorage
from shogiarena.records.storage.binary_writer import RecordBinaryWriter, RecordBinaryWriterConfig
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common import settings as settings_mod
from shogiarena.utils.common.paths import maybe_resolve_path_option, resolve_path_like
from shogiarena.utils.types.coerce import coerce_int, coerce_str, datetime_to_iso, is_strict_numeric
from shogiarena.utils.types.types import (
    EngineInfoSnapshots,
    EngineOptionsSnapshots,
    GameResult,
    JsonObject,
    JsonValue,
)
from shogiarena.web.dashboard.backend.assets_writer import DashboardProfile

logger = logging.getLogger(__name__)

_OPENBENCH_SPRT_PAIR_PATTERN = re.compile(r"^\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]$")
_GAME_RESULT_ERROR = GameResult.from_str("ERROR")
_GAME_RESULT_BLACK_WIN_BY_DECLARATION = GameResult.from_str("BLACK_WIN_BY_DECLARATION")
_GAME_RESULT_WHITE_WIN_BY_DECLARATION = GameResult.from_str("WHITE_WIN_BY_DECLARATION")
_GAME_RESULT_BLACK_WIN_BY_FORFEIT = GameResult.from_str("BLACK_WIN_BY_FORFEIT")
_GAME_RESULT_WHITE_WIN_BY_FORFEIT = GameResult.from_str("WHITE_WIN_BY_FORFEIT")


def _extract_participation(record: rshogi.record.GameRecord) -> tuple[GameParticipationRecord, ...]:
    raw = record.metadata.attributes.get("_arena_participation")
    if not isinstance(raw, str) or not raw.strip():
        return ()
    return deserialize_participation_records(raw)


_RESULT_ABBREVIATIONS: dict[GameResult, str] = {
    GameResult.BLACK_WIN: "B",
    GameResult.WHITE_WIN: "W",
    GameResult.DRAW_BY_REPETITION: "R",
    GameResult.DRAW_BY_MAX_PLIES: "M",
    GameResult.DRAW_BY_IMPASSE: "I",
    _GAME_RESULT_BLACK_WIN_BY_DECLARATION: "BD",
    _GAME_RESULT_WHITE_WIN_BY_DECLARATION: "WD",
    _GAME_RESULT_BLACK_WIN_BY_FORFEIT: "B",
    _GAME_RESULT_WHITE_WIN_BY_FORFEIT: "W",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "BI",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "WI",
    GameResult.BLACK_WIN_BY_TIMEOUT: "BT",
    GameResult.WHITE_WIN_BY_TIMEOUT: "WT",
    GameResult.PAUSED: "P",
    _GAME_RESULT_ERROR: "ERR",
    GameResult.INVALID: "ERR",
}


_RESULT_REASON_SUFFIXES: dict[GameResult, str] = {
    _GAME_RESULT_BLACK_WIN_BY_DECLARATION: "by Declaration",
    _GAME_RESULT_WHITE_WIN_BY_DECLARATION: "by Declaration",
    _GAME_RESULT_BLACK_WIN_BY_FORFEIT: "by Forfeit",
    _GAME_RESULT_WHITE_WIN_BY_FORFEIT: "by Forfeit",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "by Illegal Move",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "by Illegal Move",
    GameResult.BLACK_WIN_BY_TIMEOUT: "by Timeout",
    GameResult.WHITE_WIN_BY_TIMEOUT: "by Timeout",
    GameResult.DRAW_BY_REPETITION: "by Repetition",
    GameResult.DRAW_BY_MAX_PLIES: "by Max Moves",
    GameResult.DRAW_BY_IMPASSE: "by Impasse",
}


class _CompletedGameSummary(TypedDict, total=False):
    result_code: int | None
    result_abbr: str
    result_label: str
    result_detail: str | None
    total_plies: int | None
    start_time: str | None
    end_time: str | None


class _SummaryPayload(BaseModel):
    """Pydantic model for a completed game summary entry in run_state.json."""

    model_config = ConfigDict(extra="ignore")

    result_code: int | None = None
    result_abbr: str | None = None
    result_label: str | None = None
    result_detail: str | None = None
    total_plies: int | None = None
    end_time: str | None = None

    @field_validator("result_code", "total_plies", mode="before")
    @classmethod
    def _coerce_optional_int(cls, v: object) -> int | None:
        if v is None:
            return None
        return coerce_int(v)

    def to_completed_summary(self) -> _CompletedGameSummary:
        """Convert to _CompletedGameSummary TypedDict."""
        summary: _CompletedGameSummary = {}
        summary["result_code"] = self.result_code
        if self.result_abbr is not None:
            summary["result_abbr"] = self.result_abbr
        if self.result_label is not None:
            summary["result_label"] = self.result_label
        summary["result_detail"] = self.result_detail
        summary["total_plies"] = self.total_plies
        summary["end_time"] = self.end_time
        return summary


class _CancelledGameEntry(BaseModel):
    """Pydantic model for a cancelled game entry in run_state.json."""

    model_config = ConfigDict(extra="ignore")

    game_id: str = ""
    black: str | None = None
    white: str | None = None
    round: int | None = None
    sfen: str | None = None
    assignment: Any = None
    assigned_instance: Any = None


class _RunStatePayload(BaseModel):
    """Type-safe model for run_state.json deserialization."""

    model_config = ConfigDict(extra="ignore")

    schedule_hash: str | None = None
    completed_game_ids: list[str] = []
    cancelled_game_ids: list[str] = []
    sprt_state: dict[str, Any] | None = None
    openbench_state: dict[str, Any] | None = None
    completed_game_summaries: dict[str, _SummaryPayload] = {}
    game_display_order: dict[str, int] = {}
    original_total_games: int | None = None
    game_instance_overrides: dict[str, Any] | None = None
    cancelled_games: list[_CancelledGameEntry] = []

    @field_validator("completed_game_summaries", mode="before")
    @classmethod
    def _filter_summaries(cls, v: object) -> dict[str, object]:
        if not isinstance(v, dict):
            return {}
        return {k: val for k, val in v.items() if isinstance(k, str) and isinstance(val, dict)}

    @field_validator("cancelled_games", mode="before")
    @classmethod
    def _filter_cancelled(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return [e for e in v if isinstance(e, dict)]

    @field_validator("game_display_order", mode="before")
    @classmethod
    def _coerce_display_order(cls, v: object) -> dict[str, int]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in v.items():
            if not isinstance(key, str):
                continue
            int_val = coerce_int(value)
            if int_val is not None:
                result[key] = int_val
        return result


class TournamentRunner(BaseSessionRunner[TournamentRunResult, None]):
    dashboard_profiles = ("tournament",)
    """N-engine native tournament runner.

    Orchestrates tournaments with arbitrary number of engines using
    game-based execution, stable IDs, and deterministic resume.
    """

    class _TournamentLifecycle(LifecycleHooksBase):
        def __init__(self, runner: "TournamentRunner", controller: SessionStopController) -> None:
            super().__init__(controller)
            self._runner = runner

        async def on_game_complete(self, event: GameCompletionEvent[GameSpec]) -> None:
            await self._runner._handle_game_completion(
                event.payload,
                event.game_info,
                _worker_idx=event.worker_idx,
                stop_requested=event.stop_requested,
            )

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

    @staticmethod
    def _resolve_dashboard_profiles_for_config(config: TournamentRunConfig) -> tuple[DashboardProfile, ...]:
        if config.sprt is not None:
            return ("sprt",)
        if config.generate is not None:
            return ("generate",)
        exp_name = str(config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return ("match",)
        if exp_name == "generate":
            return ("generate",)
        return ("tournament",)

    def _is_generate_run(self) -> bool:
        if self.config.generate is not None:
            return True
        exp_name = str(self.config.experiment_name or "").strip().lower()
        return exp_name == "generate"

    def _resolve_tournament_type(self) -> str:
        if self.config.sprt is not None:
            return "sprt"
        if self._is_generate_run():
            return "generate"
        exp_name = str(self.config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return "match"
        return str(self.config.tournament.scheduler)

    def _resolve_summary_source(self) -> str:
        if self.config.sprt is not None:
            return "sprt"
        if self._is_generate_run():
            return "generate"
        exp_name = str(self.config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return "match"
        return "tournament"

    def _build_metadata_attributes(self, existing: Mapping[str, str] | None) -> dict[str, str]:
        attributes: dict[str, str] = {}
        if existing is not None:
            for key, value in existing.items():
                if value is not None:
                    attributes[str(key)] = str(value)
        attributes["runMode"] = self._summary_source
        experiment = str(self.config.experiment_name or "").strip()
        if experiment:
            attributes["experimentName"] = experiment
        records_output = self.config.records_output
        if records_output is not None:
            attributes["recordFormat"] = records_output.format
        return attributes

    def __init__(
        self,
        config: TournamentRunConfig,
        *,
        instance_pool: InstancePool | None = None,
        storage: RunStorage,
        progress_reporter: ProgressReporter | None = None,
        dashboard_enabled: bool | None = None,
        no_resume: bool = False,
    ):
        """Initialize tournament runner.

        Args:
            config: Complete tournament configuration
        """
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
            dashboard_profiles=self._resolve_dashboard_profiles_for_config(config),
        )
        self.config = config
        self.run_dir = storage.run_dir
        self._dashboard_enabled = enabled
        self._summary_source = self._resolve_summary_source()
        # Create scheduler (inject baseline_count for gauntlet)
        self.scheduler: GameScheduler
        if config.tournament.scheduler == "gauntlet":
            self.scheduler = GauntletScheduler(baseline_count=config.tournament.baseline_count)
        else:
            self.scheduler = create_scheduler(config.tournament.scheduler)
        self.game_schedule: list[GameSpec] = []
        self.completed_game_ids: set[str] = set()
        self._completed_game_summaries: dict[str, _CompletedGameSummary] = {}
        self._run_options = run_options

        # Services
        self.db_service: ArenaDBService | None = None
        self.rating_service: EloRatingService | None = None
        # SPRT state (optional)
        self._sprt: Sprt | None = None
        self._sprt_pair: tuple[str, str] | None = None
        self._sprt_min_games: int = 0
        # Cached metadata/time-control structures for dashboard summaries
        self._engine_metadata_cache: list[JsonObject] | None = None
        self._engine_metadata_runtime_sig: str | None = None
        self._engine_time_controls_cache: tuple[dict[str, str], str | None] | None = None
        self._completion_lock: asyncio.Lock = asyncio.Lock()
        self._record_writer: RecordBinaryWriter | None = None
        self._openbench_client: OpenBenchClient | None = None
        self._openbench_heartbeat_task: asyncio.Task[None] | None = None
        self._openbench_heartbeat_error: OpenBenchError | None = None
        # Rescheduling coordination (dashboard-triggered)
        self._reschedule_lock: asyncio.Lock = asyncio.Lock()
        self._pending_reschedule: list[GameSpec] | None = None
        self._pending_reschedule_seed: int | None = None
        self._stop_when_idle: bool = False
        self._schedule_wait_event: asyncio.Event = asyncio.Event()
        self._schedule_wait_event.set()
        self._session_phase: str = "starting"
        # Track cancelled cards so the dashboard can keep original totals visible between drains.
        self.cancelled_game_ids: set[str] = set()
        self._cancelled_specs: dict[str, GameSpec] = {}
        self.original_total_games: int = 0
        # Cache per-game instance hints (for schedule table) even while idle.
        self._game_assignments: dict[str, dict[str, str | None]] = {}
        # Preserve display order so cancelled entries keep their slot numbers.
        self._game_display_order: dict[str, int] = {}
        # Typed reference to tournament orchestrator (avoids isinstance checks)
        self._tournament_orchestrator: TournamentOrchestrator | None = None

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return self._TournamentLifecycle(self, controller)

    def build_session_context(self) -> SessionContext:
        rd = self.run_dir
        num_workers = int(self.config.tournament.num_parallel)
        run_id = str(self.config.experiment_name or rd.name)
        if self.db_service is None:
            raise ValueError("Tournament runner requires db_service before creating session context")
        if self.rating_service is None:
            raise ValueError("Tournament runner requires rating_service before creating session context")
        services = {
            "db": self.db_service,
            "rating": self.rating_service,
        }
        if self._sprt is not None:
            services["sprt"] = self._sprt
        if not self._run_options.no_resume:
            loaded = SessionContext.load_from_storage(
                storage=self.storage,
                instance_pool=self.instance_pool,
                services=services,
            )
            if loaded is not None:
                return loaded
        return SessionContextBuilder.for_tournament(
            storage=self.storage,
            num_workers=num_workers,
            instance_pool=self.instance_pool,
            run_id=run_id,
            experiment_name=str(self.config.experiment_name or ""),
            scheduler=str(self.config.tournament.scheduler),
            games_per_pair=int(self.config.tournament.games_per_pair),
            num_engines=len(self.config.engines),
            dashboard_enabled=bool(self._dashboard_enabled),
            services=services,
        )

    # run() is provided by BaseSessionRunner

    async def _stop_additional_services(self) -> None:
        await self._stop_openbench_heartbeat_task()
        self._openbench_heartbeat_error = None
        if self._openbench_client is not None:
            await self._openbench_client.close()
            self._openbench_client = None
        if self.db_service:
            self.db_service.close()
            self.db_service = None
        if self._record_writer is not None:
            self._record_writer.close()
            self._record_writer = None

    async def init_services(self) -> None:
        """Initialize database and rating services."""
        logger.debug("Initializing services")

        # Create run directory
        rd = self.run_dir
        rd.mkdir(parents=True, exist_ok=True)

        # Initialize database service
        self.db_service = self.storage.db_service()
        self.db_service.ensure_schema_compatibility()

        # Initialize rating service
        self.rating_service = EloRatingService(
            initial_rating=self.config.rating.initial, k_factor=self.config.rating.k_factor
        )
        self._record_writer = self._create_record_writer()
        # Initialize optional SPRT (requires rating service)
        sprt_conf = self.config.sprt
        if sprt_conf is not None:
            self._sprt = Sprt(
                elo0=float(sprt_conf.elo0),
                elo1=float(sprt_conf.elo1),
                alpha=float(sprt_conf.alpha),
                beta=float(sprt_conf.beta),
            )
            self._sprt_min_games = sprt_conf.min_games
            if len(self.config.engines) == 2:
                self._sprt_pair = (str(self.config.engines[0].name), str(self.config.engines[1].name))
            else:
                self._sprt_pair = None
        await self._init_openbench_client()
        logger.debug("Services initialized (DB/Rating/SPRT)")

    def _resolve_openbench_config(self) -> OpenBenchClientConfig | None:
        raw = self.config.openbench
        if raw is None or not raw.enabled:
            return None
        if len(self.config.engines) != 2:
            raise ValueError("openbench.enabled=true requires exactly two engines")
        if self.config.sprt is None:
            raise ValueError("openbench.enabled=true requires sprt configuration")

        settings_cfg = settings_mod.SETTINGS.openbench
        server = raw.server or (settings_cfg.server if settings_cfg is not None else None)
        username = raw.username or (settings_cfg.username if settings_cfg is not None else None)
        raw_password_env = raw.password_env.strip()
        settings_password_env = (
            settings_cfg.password_env.strip() if settings_cfg is not None and settings_cfg.password_env else ""
        )
        if (
            raw_password_env == "OPENBENCH_PASSWORD"
            and settings_password_env
            and settings_password_env != "OPENBENCH_PASSWORD"
        ):
            # OpenBenchConfig.password_env has a default value, so "omitted in run config" and
            # "explicitly set to OPENBENCH_PASSWORD" are indistinguishable here.
            # Prefer settings.openbench.password_env when it is explicitly customized.
            password_env = settings_password_env
        else:
            password_env = raw_password_env or settings_password_env or "OPENBENCH_PASSWORD"
        password = os.environ.get(password_env, "").strip()
        if not password:
            raise ValueError(
                f"OpenBench password is missing. Set environment variable '{password_env}' (or override password_env)."
            )
        if not server:
            raise ValueError("OpenBench server is not configured")
        if not username:
            raise ValueError("OpenBench username is not configured")

        target_test_id: int | None
        raw_target_test_id = raw.target_test_id
        target_test_id = int(raw_target_test_id) if raw_target_test_id is not None else None
        mode = raw.mode
        if mode == "create_test" and target_test_id is None:
            restored = self._load_openbench_target_from_run_state()
            if restored is not None:
                target_test_id = restored

        create_payload: dict[str, str] | None = None
        create_discovery_timeout_sec = 180.0
        if mode == "create_test":
            create_payload = self._build_openbench_create_payload(
                openbench_server=server,
                openbench_username=username,
                openbench_password=password,
            )
            if raw.create is not None:
                create_discovery_timeout_sec = raw.create.discovery_timeout_sec

        return OpenBenchClientConfig(
            enabled=True,
            mode=mode,
            server=server,
            username=username,
            password=password,
            target_test_id=target_test_id,
            submit_interval_games=int(raw.submit_interval_games),
            strict=bool(raw.strict),
            heartbeat_interval_sec=float(raw.heartbeat_interval_sec),
            poll_interval_sec=float(raw.poll_interval_sec),
            assignment_timeout_sec=float(raw.assignment_timeout_sec),
            allow_insecure_http=bool(raw.allow_insecure_http),
            concurrency=int(max(1, self.config.tournament.num_parallel)),
            create_payload=create_payload,
            create_discovery_timeout_sec=float(create_discovery_timeout_sec),
        )

    def _load_openbench_target_from_run_state(self) -> int | None:
        run_state_path = self.run_dir / "run_state.json"
        if not run_state_path.exists() or self._run_options.no_resume:
            return None
        try:
            with open(run_state_path, encoding="utf-8") as f:
                run_state = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Failed to read run_state for OpenBench target reuse: %s", exc)
            return None
        openbench_state = run_state.get("openbench_state")
        if not isinstance(openbench_state, dict):
            return None
        raw_target = openbench_state.get("target_test_id")
        if is_strict_numeric(raw_target):
            target_id = int(raw_target)
            if target_id > 0:
                return target_id
        return None

    def _build_openbench_create_payload(
        self,
        *,
        openbench_server: str,
        openbench_username: str,
        openbench_password: str,
    ) -> dict[str, str]:
        openbench_cfg = self.config.openbench
        create_cfg = openbench_cfg.create if openbench_cfg is not None else None
        if create_cfg is None or create_cfg.payload is None:
            raise ValueError("openbench.create.payload is required for mode=create_test")
        payload_cfg = create_cfg.payload

        dev_engine = (payload_cfg.dev_engine or "").strip()
        base_engine = (payload_cfg.base_engine or "").strip()
        if not dev_engine or not base_engine:
            raise ValueError("openbench.create.payload.dev_engine/base_engine are required")

        dev_spec = self.config.engines[0]
        base_spec = self.config.engines[1]

        dev_repo = self._resolve_openbench_repo_url(dev_spec, payload_cfg.dev_repo or "")
        base_repo = self._resolve_openbench_repo_url(base_spec, payload_cfg.base_repo or "")
        dev_branch = self._resolve_openbench_branch(dev_spec, payload_cfg.dev_branch or "")
        base_branch = self._resolve_openbench_branch(base_spec, payload_cfg.base_branch or "")

        dev_tc = self._resolve_openbench_time_control(
            payload_cfg.dev_time_control or "",
            base_tc=self.config.rules.time_control,
            engine_tc=dev_spec.time_control,
        )
        base_tc = self._resolve_openbench_time_control(
            payload_cfg.base_time_control or "",
            base_tc=self.config.rules.time_control,
            engine_tc=base_spec.time_control,
        )

        sprt_cfg = self.config.sprt
        test_mode = payload_cfg.test_mode.strip().upper()
        if test_mode not in {"SPRT", "GAMES"}:
            raise ValueError("openbench.create.payload.test_mode must be SPRT or GAMES")
        test_bounds = payload_cfg.test_bounds.strip()
        if test_bounds == "auto":
            if sprt_cfg is None:
                raise ValueError("openbench.create.payload.test_bounds=auto requires sprt config")
            test_bounds = f"[{float(sprt_cfg.elo0)}, {float(sprt_cfg.elo1)}]"
        test_conf = payload_cfg.test_confidence.strip()
        if test_conf == "auto":
            if sprt_cfg is None:
                raise ValueError("openbench.create.payload.test_confidence=auto requires sprt config")
            test_conf = f"[{float(sprt_cfg.beta)}, {float(sprt_cfg.alpha)}]"
        if test_mode == "SPRT":
            lower_elo, upper_elo = self._parse_openbench_float_pair(
                test_bounds,
                field_name="openbench.create.payload.test_bounds",
            )
            if lower_elo >= upper_elo:
                raise ValueError("openbench.create.payload.test_bounds must satisfy lower < upper")
            beta_conf, alpha_conf = self._parse_openbench_float_pair(
                test_conf,
                field_name="openbench.create.payload.test_confidence",
            )
            if not 0.0 < beta_conf < 1.0 or not 0.0 < alpha_conf < 1.0:
                raise ValueError("openbench.create.payload.test_confidence values must be within (0, 1)")

        test_max_games_raw = payload_cfg.test_max_games
        if test_max_games_raw == "auto":
            max_games_val = int(sprt_cfg.max_games or 0) if sprt_cfg is not None else 0
        else:
            max_games_val = int(test_max_games_raw)
        if test_mode == "GAMES" and max_games_val <= 0:
            raise ValueError("openbench.create.payload.test_max_games must be > 0 when test_mode=GAMES")

        scale_nps_raw = payload_cfg.scale_nps
        if scale_nps_raw == "auto":
            scale_nps = self._fetch_openbench_engine_nps(
                base_engine,
                openbench_server=openbench_server,
                openbench_username=openbench_username,
                openbench_password=openbench_password,
            )
        else:
            scale_nps = int(scale_nps_raw)
        if scale_nps <= 0:
            raise ValueError("openbench.create.payload.scale_nps must be > 0")

        dev_options = payload_cfg.dev_options.strip()
        base_options = payload_cfg.base_options.strip()
        for label, options in (("dev_options", dev_options), ("base_options", base_options)):
            if not re.search(r"\bThreads=\d+\b", options):
                raise ValueError(f"openbench.create.payload.{label} must include Threads=<int>")
            if not re.search(r"\bHash=\d+\b", options):
                raise ValueError(f"openbench.create.payload.{label} must include Hash=<int>")

        throughput = payload_cfg.throughput
        workload_size = payload_cfg.workload_size
        if throughput <= 0:
            raise ValueError("openbench.create.payload.throughput must be > 0")
        if workload_size <= 0:
            raise ValueError("openbench.create.payload.workload_size must be > 0")
        upload_pgns = payload_cfg.upload_pgns.strip().upper()
        if upload_pgns not in {"FALSE", "COMPACT", "VERBOSE"}:
            raise ValueError("openbench.create.payload.upload_pgns must be FALSE, COMPACT, or VERBOSE")
        scale_method = payload_cfg.scale_method.strip().upper()
        if scale_method not in {"DEV", "BASE", "BOTH"}:
            raise ValueError("openbench.create.payload.scale_method must be DEV, BASE, or BOTH")

        configured_book_name = payload_cfg.book_name.strip()
        book_name = self._resolve_openbench_book_name(
            configured_book_name,
            openbench_server=openbench_server,
            openbench_username=openbench_username,
            openbench_password=openbench_password,
        )

        return {
            "dev_engine": dev_engine,
            "base_engine": base_engine,
            "dev_repo": dev_repo,
            "base_repo": base_repo,
            "dev_branch": dev_branch,
            "base_branch": base_branch,
            "dev_bench": payload_cfg.dev_bench.strip(),
            "base_bench": payload_cfg.base_bench.strip(),
            "dev_options": dev_options,
            "base_options": base_options,
            "dev_network": payload_cfg.dev_network.strip(),
            "base_network": payload_cfg.base_network.strip(),
            "dev_time_control": dev_tc,
            "base_time_control": base_tc,
            "book_name": book_name,
            "upload_pgns": upload_pgns,
            "test_mode": test_mode,
            "test_bounds": test_bounds,
            "test_confidence": test_conf,
            "test_max_games": str(max_games_val),
            "priority": str(payload_cfg.priority),
            "throughput": str(throughput),
            "workload_size": str(workload_size),
            "syzygy_wdl": payload_cfg.syzygy_wdl.strip().upper(),
            "syzygy_adj": payload_cfg.syzygy_adj.strip().upper(),
            "win_adj": payload_cfg.win_adj.strip(),
            "draw_adj": payload_cfg.draw_adj.strip(),
            "scale_method": scale_method,
            "scale_nps": str(scale_nps),
        }

    def _resolve_openbench_repo_url(self, engine_spec: EngineConfig, configured: str) -> str:
        candidate = configured.strip()
        if candidate and candidate != "auto":
            return candidate
        artifact = (engine_spec.artifact or "").strip()
        if not artifact:
            raise ValueError("openbench.create.payload.*_repo must be set when engine uses engine_path")
        repo_name = artifact.split("/", 1)[0]
        repo = project_dirs.repos.get(repo_name)
        if repo is None or not repo.url:
            raise ValueError(
                f"openbench.create.payload repo URL for '{repo_name}' is missing. "
                "Set settings repos URL or specify *_repo explicitly."
            )
        return str(repo.url)

    @staticmethod
    def _resolve_openbench_branch(engine_spec: EngineConfig, configured: str) -> str:
        candidate = configured.strip()
        if candidate and candidate != "auto":
            return candidate
        artifact = (engine_spec.artifact or "").strip()
        if not artifact:
            raise ValueError("openbench.create.payload.*_branch must be set when engine uses engine_path")
        commit = artifact.split("/", 1)[1]
        return commit

    def _resolve_openbench_time_control(
        self,
        configured: str,
        *,
        base_tc: TimeControlLimits | None,
        engine_tc: TimeControlLimits | None,
    ) -> str:
        value = configured.strip()
        if value and value != "auto":
            return value
        limits = build_time_control_limits(base_tc, engine_tc)
        if limits is None:
            raise ValueError("openbench.create.payload.*_time_control is required (no rules.time_control configured)")
        if limits.node_limit is not None:
            return f"N={int(limits.node_limit)}"
        if limits.depth_limit is not None:
            return f"D={int(limits.depth_limit)}"
        if limits.fixed_time_ms is not None:
            return f"MT={int(limits.fixed_time_ms)}"
        if limits.time_ms is None:
            raise ValueError("Unable to convert time control for OpenBench create payload")
        base_sec = float(limits.time_ms) / 1000.0
        inc_ms = limits.increment_ms if limits.increment_ms is not None else limits.byoyomi_ms
        inc_sec = float(inc_ms or 0) / 1000.0
        return f"{base_sec:.1f}+{inc_sec:.2f}"

    @staticmethod
    def _parse_openbench_float_pair(value: str, *, field_name: str) -> tuple[float, float]:
        match = _OPENBENCH_SPRT_PAIR_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"{field_name} must be formatted as [x, y]")
        try:
            left = float(match.group(1))
            right = float(match.group(2))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"{field_name} must contain numeric values") from exc
        return left, right

    def _fetch_openbench_engine_nps(
        self,
        engine_name: str,
        *,
        openbench_server: str,
        openbench_username: str,
        openbench_password: str,
    ) -> int:
        import urllib.parse
        import urllib.request

        endpoint = f"{str(openbench_server).rstrip('/')}/api/config/{urllib.parse.quote(engine_name)}/"
        data = urllib.parse.urlencode({"username": openbench_username or "", "password": openbench_password}).encode(
            "utf-8"
        )
        request = urllib.request.Request(endpoint, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to fetch OpenBench engine config for scale_nps=auto: {engine_name}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"OpenBench api/config returned invalid payload for engine: {engine_name}")
        if "error" in payload:
            raise ValueError(f"OpenBench api/config error for engine '{engine_name}': {payload['error']}")
        nps_raw = payload.get("nps")
        if not is_strict_numeric(nps_raw):
            raise ValueError(f"OpenBench api/config missing nps for engine '{engine_name}'")
        nps = int(nps_raw)
        if nps <= 0:
            raise ValueError(f"OpenBench api/config has non-positive nps for engine '{engine_name}'")
        return nps

    def _resolve_openbench_book_name(
        self,
        configured_book_name: str,
        *,
        openbench_server: str,
        openbench_username: str,
        openbench_password: str,
    ) -> str:
        if configured_book_name.upper() != "NONE":
            return configured_book_name
        try:
            books = self._fetch_openbench_books(
                openbench_server=openbench_server,
                openbench_username=openbench_username,
                openbench_password=openbench_password,
            )
        except ValueError as exc:
            logger.warning("Failed to auto-resolve OpenBench book list: %s", exc)
            return configured_book_name
        if not books:
            return configured_book_name
        if any(book.upper() == "NONE" for book in books):
            return configured_book_name
        fallback = books[0]
        logger.warning(
            "openbench.create.payload.book_name=NONE is not available on server; falling back to '%s'",
            fallback,
        )
        return fallback

    def _fetch_openbench_books(
        self,
        *,
        openbench_server: str,
        openbench_username: str,
        openbench_password: str,
    ) -> list[str]:
        import urllib.parse
        import urllib.request

        endpoint = f"{str(openbench_server).rstrip('/')}/api/config/"
        data = urllib.parse.urlencode({"username": openbench_username or "", "password": openbench_password}).encode(
            "utf-8"
        )
        request = urllib.request.Request(endpoint, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Failed to fetch OpenBench api/config payload") from exc
        if not isinstance(payload, dict):
            raise ValueError("OpenBench api/config returned invalid payload")
        if "error" in payload:
            raise ValueError(f"OpenBench api/config error: {payload['error']}")
        books_raw = payload.get("books")
        if isinstance(books_raw, dict):
            books = [str(key).strip() for key in books_raw.keys() if str(key).strip()]
        elif isinstance(books_raw, list):
            books = [str(item).strip() for item in books_raw if str(item).strip()]
        else:
            return []
        return sorted(set(books))

    async def _init_openbench_client(self) -> None:
        self._openbench_heartbeat_error = None
        cfg = self._resolve_openbench_config()
        if cfg is None:
            self._openbench_client = None
            return
        client = OpenBenchClient(
            cfg,
            tested_engine=str(self.config.engines[0].name),
            base_engine=str(self.config.engines[1].name),
        )
        try:
            await client.initialize()
        except OpenBenchError as exc:
            await client.close()
            if cfg.strict:
                raise
            logger.warning("OpenBench initialization failed; continuing (strict=false): %s", exc)
            self._openbench_client = None
            return
        self._openbench_client = client
        self._start_openbench_heartbeat_task(client=client, interval_sec=cfg.heartbeat_interval_sec)

    async def _sync_openbench_after_game(self) -> None:
        self._raise_pending_openbench_background_error()
        if self._openbench_client is None or self.db_service is None:
            return
        should_stop = await self._openbench_client.sync(self.db_service)
        if should_stop:
            self.stop_controller.request_stop(reason="openbench-stop")

    async def _flush_openbench(self) -> None:
        self._raise_pending_openbench_background_error()
        if self._openbench_client is None or self.db_service is None:
            return
        should_stop = await self._openbench_client.flush(self.db_service)
        if should_stop:
            self.stop_controller.request_stop(reason="openbench-stop")

    def _start_openbench_heartbeat_task(self, *, client: OpenBenchClient, interval_sec: float) -> None:
        if self._openbench_heartbeat_task is not None:
            self._openbench_heartbeat_task.cancel()
        self._openbench_heartbeat_task = asyncio.create_task(
            self._openbench_heartbeat_loop(client=client, interval_sec=interval_sec),
            name="openbench-heartbeat",
        )

    async def _stop_openbench_heartbeat_task(self) -> None:
        task = self._openbench_heartbeat_task
        self._openbench_heartbeat_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _openbench_heartbeat_loop(self, *, client: OpenBenchClient, interval_sec: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_sec)
                if self._openbench_client is not client:
                    return
                try:
                    should_stop = await client.heartbeat()
                except OpenBenchError as exc:
                    if client.strict:
                        logger.error("OpenBench heartbeat failed in strict mode: %s", exc)
                        self._openbench_heartbeat_error = exc
                        self.stop_controller.request_stop(reason="openbench-heartbeat-error")
                        return
                    logger.warning("OpenBench heartbeat failed; continuing (strict=false): %s", exc)
                    continue
                if should_stop:
                    self.stop_controller.request_stop(reason="openbench-stop")
                    return
        except asyncio.CancelledError:
            return

    def _raise_pending_openbench_background_error(self) -> None:
        if self._openbench_heartbeat_error is None:
            return
        exc = self._openbench_heartbeat_error
        self._openbench_heartbeat_error = None
        raise exc

    def _create_record_writer(self) -> RecordBinaryWriter | None:
        config = self.config.records_output
        if config is None:
            return None
        output_dir = config.output_dir or (self.run_dir / "records")
        file_prefix = config.file_prefix or config.format
        writer_config = RecordBinaryWriterConfig(
            format_id=config.format,
            output_dir=output_dir,
            max_positions_per_file=int(config.max_positions_per_file),
            max_games_per_file=config.max_games_per_file,
            file_prefix=str(file_prefix),
        )
        return RecordBinaryWriter(writer_config)

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

    def _collect_engine_metadata(
        self,
        runtime_options: EngineOptionsSnapshots,
        runtime_info: EngineInfoSnapshots,
    ) -> list[JsonObject]:
        resolver = ArtifactResolver()
        base_extra_options = base_orchestrator_utils.compute_max_ply_extra_options(self.config.rules) or {}
        cfg_source_path = self.config.source_path

        def format_source(label: str, candidate: object | None) -> str:
            if candidate:
                try:
                    path_obj = Path(str(candidate)).expanduser().resolve(strict=False)
                    return f"{label} ({path_obj})"
                except (OSError, RuntimeError, ValueError):
                    return f"{label} ({candidate})"
            return label

        rules_source = format_source("rules.adjudication", cfg_source_path) if base_extra_options else None
        metadata: list[JsonObject] = []
        run_dir = self.run_dir
        for engine in self.config.engines:
            meta: JsonObject = {
                "name": str(engine.name),
                "engine_config_path": str(engine.engine_path) if engine.engine_path else None,
            }
            resolved_engine_path: str | None = None
            if engine.engine_path is not None:
                meta.update(self._read_engine_config_metadata(engine, resolver))
                ep = meta.get("engine_path")
                resolved_engine_path = coerce_str(ep)
            else:
                art = engine.artifact
                build_overrides = engine.build_options or {}
                if art_str := coerce_str(art):
                    try:
                        resolved_engine_path = str(resolver.resolve(art_str, overrides=build_overrides))
                    except (OSError, RuntimeError, ValueError) as exc:
                        logger.debug("Failed to resolve engine binary for %s: %s", engine.name, exc, exc_info=True)
            if resolved_engine_path:
                meta["engine_path"] = resolved_engine_path
            extra_opts: dict[str, object] = dict(engine.options) if engine.options else {}
            if extra_opts:
                meta["extra_options"] = extra_opts
            overlay_opts = engine.load_overlay_options() or {}
            if overlay_opts:
                meta["overlay_options"] = overlay_opts
            config_opts = cast(dict[str, JsonValue] | None, meta.get("config_options"))
            merged = self._merge_option_dicts(config_opts, extra_opts or None, overlay_opts or None)
            source_map: dict[str, str] = {}
            source_details: dict[str, str] = {}
            if config_opts:
                for key in config_opts:
                    source_map[key] = "config"
                    source_details[key] = format_source("engine config", meta.get("engine_config_path"))
            if extra_opts:
                for key in extra_opts:
                    source_map[key] = "override"
                    source_details[key] = format_source("engines[].options", cfg_source_path)
            if overlay_opts:
                for key in overlay_opts:
                    source_map[key] = "overlay"
                    source_details[key] = format_source("options overlays", cfg_source_path)
            if base_extra_options:
                for key, value in base_extra_options.items():
                    merged[key] = cast(JsonValue, value)
                    source_map.setdefault(key, "rules")
                    if rules_source:
                        source_details.setdefault(key, rules_source)

            if merged:
                meta["merged_options"] = dict(merged)
                if source_map:
                    meta["option_sources"] = source_map
                if source_details:
                    meta["option_sources_details"] = source_details
                resolved_options: JsonObject = {}
                for key, value in merged.items():
                    try:
                        resolved_value = maybe_resolve_path_option(
                            key,
                            value,
                            output_dir=run_dir,
                            engine_dir=project_dirs.engine_dir,
                        )
                    except (OSError, RuntimeError, ValueError, TypeError) as exc:
                        logger.debug(
                            "Failed to resolve path option %s for engine %s: %s",
                            key,
                            meta.get("name"),
                            exc,
                            exc_info=True,
                        )
                        resolved_value = value
                    resolved_options[key] = cast(JsonValue, resolved_value)
                meta["resolved_options"] = resolved_options

            engine_name = str(meta["name"])
            runtime_opts = runtime_options.get(engine_name)
            if runtime_opts:
                try:
                    meta["runtime_usi_options"] = json.loads(json.dumps(runtime_opts, ensure_ascii=False))
                except (TypeError, ValueError):
                    meta["runtime_usi_options"] = dict(runtime_opts)
            runtime_meta = runtime_info.get(engine_name)
            if runtime_meta:
                meta["runtime_engine_info"] = dict(runtime_meta)
            metadata.append(meta)
        return metadata

    def _read_engine_config_metadata(self, engine: EngineConfig, resolver: ArtifactResolver) -> JsonObject:
        config_path = engine.engine_path
        if config_path is None:
            return {}
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise TypeError(f"Engine config must be a mapping: {config_path}")

        metadata: JsonObject = {}
        engine_path = raw.get("engine_path")
        artifact = raw.get("artifact")
        build_overrides_raw = raw.get("build_options")
        if build_overrides_raw is None:
            build_overrides: JsonObject = {}
        elif isinstance(build_overrides_raw, dict):
            build_overrides = dict(build_overrides_raw)
        else:
            raise TypeError(f"build_options must be a mapping in engine config: {config_path}")

        if ep_str := coerce_str(engine_path):
            metadata["engine_path"] = resolve_path_like(
                ep_str,
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
            )
        elif art_str := coerce_str(artifact):
            metadata["engine_path"] = str(resolver.resolve(art_str, overrides=build_overrides))
        elif engine_path is not None or artifact is not None:
            raise ValueError(f"Invalid engine config {config_path}: specify a non-empty engine_path or artifact")

        options = raw.get("options")
        if options is not None and not isinstance(options, dict):
            raise TypeError(f"options must be a mapping in engine config: {config_path}")
        if options:
            metadata["config_options"] = options

        return metadata

    @staticmethod
    def _merge_option_dicts(*items: Mapping[str, object] | None) -> dict[str, object]:
        merged: dict[str, object] = {}
        for entry in items:
            if entry:
                merged.update(entry)
        return merged

    @property
    def engine_time_controls(self) -> tuple[dict[str, str], str | None]:
        if self._engine_time_controls_cache is None:
            self._engine_time_controls_cache = self._compute_engine_time_control_specs()
        tc_map, default_spec = self._engine_time_controls_cache
        return dict(tc_map), default_spec

    def _engine_instance_defaults(self) -> dict[str, str | None]:
        mapping: dict[str, str | None] = {}
        for spec in self.config.engines:
            name = str(spec.name)
            inst_raw = spec.instance_id
            if inst_str := coerce_str(inst_raw):
                mapping[name] = inst_str
                continue
            mapping[name] = "local"
        return mapping

    def _compute_engine_time_control_specs(self) -> tuple[dict[str, str], str | None]:
        base_tc = self.config.rules.time_control
        engine_map: dict[str, str] = {}
        default_spec: str | None = None

        base_limits = build_time_control_limits(base_tc, None)
        if base_limits is not None:
            default_spec = base_limits.to_spec_str()

        for engine in self.config.engines:
            limits = build_time_control_limits(base_tc, engine.time_control)
            name = str(engine.name)
            if limits is None:
                engine_map[name] = "-"
                if default_spec is None:
                    default_spec = "-"
                continue
            spec = limits.to_spec_str()
            engine_map[name] = spec
            if default_spec is None:
                default_spec = spec

        return engine_map, default_spec

    # --- Template hooks implementation -----------------------------------
    async def prepare_run_dir(self) -> None:
        rd = self.run_dir
        rd.mkdir(parents=True, exist_ok=True)
        if self._run_options.no_resume:
            self._cleanup_existing_run()
        self._write_run_metadata()

    def _write_run_metadata(self) -> None:
        rd = self.run_dir

        config_payload: JsonObject | None = None
        try:
            config_payload = self._serialize_config()
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to serialize tournament config for metadata dump: %s", exc)
        except OSError as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to serialize tournament config due to IO error: %s", exc)

        if config_payload is not None:
            resolved_path = rd / "config_resolved.yaml"
            if not resolved_path.exists():
                try:
                    resolved_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")
                except (OSError, ValueError) as exc:  # pragma: no cover - defensive logging
                    logger.exception("Failed to write resolved config to %s: %s", resolved_path, exc)

        metadata_path = rd / "run_metadata.json"
        if not metadata_path.exists():
            metadata = {
                "shogiarena_version": self._detect_shogiarena_version(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except (OSError, TypeError, ValueError) as exc:  # pragma: no cover - defensive logging
                logger.exception("Failed to write run metadata to %s: %s", metadata_path, exc)

    def _serialize_config(self) -> JsonObject:
        return self.config.model_dump(mode="json")

    @staticmethod
    def _detect_shogiarena_version() -> str:
        try:
            return importlib_metadata.version("shogiarena")
        except importlib_metadata.PackageNotFoundError:  # pragma: no cover - environment without package
            return "unknown"

    async def prepare_domain(self) -> None:
        await self._setup_tournament()

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:
        if not self._dashboard_enabled:
            return None
        return (
            self.run_dir,
            int(self.config.dashboard.api_port),
            int(self.config.tournament.num_parallel),
        )

    async def seed_initial_summary(self) -> None:
        rd = self.run_dir

        engine_names = [str(e.name) for e in self.config.engines]
        engines_meta = self.engine_metadata
        engine_tc_map, default_tc_spec = self.engine_time_controls
        engine_instances = self._engine_instance_defaults()
        anchor_name = engine_names[0] if engine_names else None

        zero_stats = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engine_names}

        seed_summary: JsonObject = {
            "tournamentType": self._resolve_tournament_type(),
            "mode": self._summary_source,
            "flipPolicy": self.config.rules.initial_positions.flip_policy,
            "numEngines": len(engine_names),
            "runDir": str(rd),
            "leaderboard": [],
            "ratingInitial": self.config.rating.initial,
            "engines": engine_names,
            "enginesMeta": engines_meta,
            "engineTimeControls": engine_tc_map,
            "defaultTimeControl": default_tc_spec,
            "engineInstances": engine_instances,
            "engineStats": zero_stats,
            "pairResults": {},
            "timestamp": datetime.now().isoformat(),
            "games": {
                "completed": 0,
                "total": 0,
                "cancelled": 0,
            },
            "btd": {
                "ratings": {name: {"elo": 0.0, "se": 0.0} for name in engine_names},
                "anchor": anchor_name,
                "gamma_elo": 0.0,
                "gamma_elo_se": 0.0,
                "draw_eq": 0.5,
                "draw_eq_se": 0.0,
                "rating_cov": {name: {} for name in engine_names},
            },
        }
        if self._summary_source in {"sprt", "match"}:
            seed_summary["games"] = {
                "completed": 0,
                "total": 0,
            }

        rules_payload = self._build_rules_payload()
        if rules_payload:
            seed_summary["rules"] = rules_payload
            seed_summary["initialPositions"] = rules_payload.get("initial_positions")
            seed_summary["repetitionOccurrencesToDraw"] = rules_payload.get("repetition_occurrences_to_draw")
            ipos = rules_payload.get("initial_positions")
            seed_summary["flipPolicy"] = (
                seed_summary.get("flipPolicy")
                or rules_payload.get("flip_policy")
                or (ipos.get("flip_policy") if isinstance(ipos, dict) else None)
            )
            seed_summary["tournamentConfig"] = {"rules": rules_payload}
            sprt_payload = self._build_sprt_payload()
            if sprt_payload:
                seed_summary.setdefault("sprt", sprt_payload)
            if self._is_generate_run():
                generate_conf = self.config.generate
                if generate_conf is not None:
                    seed_summary["generateConfig"] = generate_conf.model_dump(mode="json")
                records_output = self.config.records_output
                if records_output is not None:
                    seed_summary["recordsOutput"] = records_output.model_dump(mode="json")

            if self.api_server is not None:
                try:
                    schedule_snapshot = await self.get_schedule_snapshot()
                except (RuntimeError, ValueError, OSError) as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to seed games snapshot: %s", exc, exc_info=True)
                else:
                    self.api_server.broadcast_games_snapshot(schedule_snapshot)
                self.api_server.broadcast_summary_update(seed_summary, source=self._summary_source)

        btd_summary = {
            "ratings": {name: {"elo": 0.0, "se": 0.0} for name in engine_names},
            "anchor": anchor_name,
            "gamma_elo": 0.0,
            "gamma_elo_se": 0.0,
            "enginesMeta": engines_meta,
        }
        summary_btd_path = rd / "summary_btd.json"
        try:
            with open(summary_btd_path, "w", encoding="utf-8") as f:
                json.dump(btd_summary, f, indent=2)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Failed to write initial BTD summary to %s: %s", summary_btd_path, exc, exc_info=True)

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> "TournamentOrchestrator":
        if session_context is None:
            raise ValueError("Tournament runner requires a session context")
        return await self._create_orchestrator(hooks, session_context)

    async def run_pre_orchestration_hooks(self, orchestrator: BaseOrchestrator) -> None:
        # Provide schedule to orchestrator before run if supported
        if self._tournament_orchestrator is not None:
            self._tournament_orchestrator.set_schedule(self.game_schedule, self.completed_game_ids)

    async def _setup_tournament(self) -> bool:
        """Setup new tournament or resume existing.

        Returns:
            True if resuming, False if new
        """
        # Check for existing run
        rd = self.run_dir
        run_state_path = rd / "run_state.json"

        if run_state_path.exists() and not self._run_options.no_resume:
            # Try to resume
            return await self._resume_tournament()
        else:
            # Setup new tournament
            await self._setup_new_tournament()
            return False

    async def _setup_new_tournament(self) -> None:
        """Setup a new tournament."""
        logger.debug("Setting up new tournament in %s", self.run_dir)

        # Warn about flip_policy choices for statistical robustness
        flip = self.config.rules.initial_positions.flip_policy
        # Enforce: game_order=pairwise requires flip_policy=pair_both
        if self.config.tournament.game_order == "pairwise" and flip != "pair_both":
            raise ValueError("game_order='pairwise' requires initial_positions.flip_policy='pair_both'")
        # Warn if baseline_count specified but scheduler is not gauntlet
        if self.config.tournament.scheduler != "gauntlet":
            if int(self.config.tournament.baseline_count) != 1:
                logger.warning(
                    "tournament.baseline_count is specified but scheduler is not 'gauntlet'; the value will be ignored."
                )

        if flip != "pair_both":
            if self.config.sprt is not None:
                logger.warning(
                    f"flip_policy is '{flip}'. For SPRT, flip_policy='pair_both' is recommended for paired samples."
                )
            else:
                logger.debug(
                    "flip_policy is '%s'. Pentanomial stats will be based on available pairs only (reference).",
                    flip,
                )
        else:
            # pair_both with odd games_per_pair produces one-sided leftovers
            if (int(self.config.tournament.games_per_pair) % 2) == 1:
                logger.warning(
                    "games_per_pair is odd with flip_policy='pair_both'. "
                    "One color per pair will be unpaired; consider even number."
                )

        # Generate game schedule
        self.game_schedule = self.scheduler.generate_schedule(
            engines=self.config.engines,
            games_per_pair=self.config.tournament.games_per_pair,
            seed=str(self.config.tournament.seed),
            initial_positions=self.config.rules.initial_positions,
        )

        # Reorder/shuffle schedule based on tournament options
        self.game_schedule = self._reorder_and_shuffle(self.game_schedule)

        logger.debug(f"Generated {len(self.game_schedule)} games")

        # Save run state
        self._completed_game_summaries.clear()
        self._reset_schedule_tracking()
        self._save_run_state()
        self._write_schedule_file(self.game_schedule)
        self._notify_schedule_available()

    async def _resume_tournament(self) -> bool:
        """Resume an existing tournament.

        Returns:
            True if successfully resumed
        """
        logger.debug("Attempting to resume tournament")

        # Load and validate run state
        rd = self.run_dir
        run_state_path = rd / "run_state.json"
        with open(run_state_path) as f:
            raw = json.load(f)

        try:
            state = _RunStatePayload.model_validate(raw)
        except ValidationError:
            logger.warning("Failed to parse run_state.json, cannot resume")
            return False

        # Validate schedule hash
        current_hash = self.config.get_schedule_hash()
        if state.schedule_hash != current_hash:
            logger.warning("Configuration changed, cannot resume. Use --no-resume to start fresh.")
            return False

        # Load completed games
        self.completed_game_ids = set(state.completed_game_ids)
        if self._sprt is not None and state.sprt_state is not None:
            try:
                self._sprt = Sprt.from_snapshot(cast(SprtStateSnapshot, state.sprt_state))
            except (TypeError, ValueError) as exc:
                logger.warning("Failed to restore SPRT state: %s", exc)
        if self._openbench_client is not None and state.openbench_state is not None:
            self._openbench_client.restore_state(state.openbench_state)

        # Parse completed game summaries via Pydantic models
        parsed_summaries = {gid: sp.to_completed_summary() for gid, sp in state.completed_game_summaries.items()}
        if parsed_summaries:
            self._completed_game_summaries = {
                gid: parsed_summaries[gid] for gid in self.completed_game_ids if gid in parsed_summaries
            }
        else:
            self._completed_game_summaries = {}

        # Regenerate schedule (deterministic with same seed)
        self.game_schedule = self.scheduler.generate_schedule(
            engines=self.config.engines,
            games_per_pair=self.config.tournament.games_per_pair,
            seed=str(self.config.tournament.seed),
            initial_positions=self.config.rules.initial_positions,
        )

        # Apply same ordering/shuffle for resume
        self.game_schedule = self._reorder_and_shuffle(self.game_schedule)

        if state.game_display_order:
            self._game_display_order = state.game_display_order
        else:
            self._reset_display_order()

        # Count remaining games
        remaining = len([m for m in self.game_schedule if m.game_id not in self.completed_game_ids])

        logger.debug(f"Resuming tournament: {len(self.completed_game_ids)} completed, {remaining} remaining games")

        cancelled_ids = set(state.cancelled_game_ids)
        payload_by_id = {entry.game_id: entry for entry in state.cancelled_games}
        self._cancelled_specs = {}
        retained_schedule: list[GameSpec] = []
        for spec in self.game_schedule:
            if spec.game_id in cancelled_ids:
                entry = payload_by_id.get(spec.game_id)
                if entry is not None:
                    reconstructed = GameSpec(
                        black_engine=entry.black or spec.black_engine,
                        white_engine=entry.white or spec.white_engine,
                        initial_sfen=entry.sfen or spec.initial_sfen,
                        game_id=str(spec.game_id),
                        round_num=entry.round if entry.round is not None else (spec.round_num or 0),
                    )
                    assignment_payload = entry.assignment
                    if assignment_payload is None and entry.assigned_instance is not None:
                        assignment_payload = entry.assigned_instance
                    self._apply_assignment_override(reconstructed, assignment_payload)
                    self._cancelled_specs[spec.game_id] = reconstructed
                else:
                    self._cancelled_specs[spec.game_id] = spec
                continue
            retained_schedule.append(spec)

        if cancelled_ids:
            self.game_schedule = retained_schedule

        self._ensure_display_order_for_specs(self.game_schedule)
        self._ensure_display_order_for_specs(self._cancelled_specs.values())

        self.cancelled_game_ids = cancelled_ids
        if state.original_total_games is not None and state.original_total_games > 0:
            self.original_total_games = state.original_total_games
        else:
            self.original_total_games = len(self.game_schedule) + len(self.cancelled_game_ids)

        if state.game_instance_overrides is not None:
            for spec in self.game_schedule:
                self._apply_assignment_override(spec, state.game_instance_overrides.get(spec.game_id))
            for spec in self._cancelled_specs.values():
                self._apply_assignment_override(spec, state.game_instance_overrides.get(spec.game_id))

        self._refresh_game_assignments()
        self._notify_schedule_available()

        return True

    def _has_pending_games(self) -> bool:
        return any(
            spec.game_id not in self.completed_game_ids and spec.game_id not in self.cancelled_game_ids
            for spec in self.game_schedule
        )

    def _notify_schedule_available(self) -> None:
        if not self._schedule_wait_event.is_set():
            self._schedule_wait_event.set()
        # Reset idle-stop flag once new work arrives
        self._stop_when_idle = False

    async def _wait_for_new_schedule(self) -> None:
        if self._schedule_wait_event.is_set():
            self._schedule_wait_event.clear()
            return
        logger.debug("Tournament drained; waiting for new schedule before resuming")
        self._session_phase = "waiting"
        await self._schedule_wait_event.wait()
        self._schedule_wait_event.clear()
        self._session_phase = "running"

    @staticmethod
    def _normalize_instance_id(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if normalized.lower() in {"auto", "default"}:
            return None
        return normalized

    def _infer_game_assignment(self, game_spec: GameSpec) -> tuple[str | None, str | None]:
        b_spec = next((e for e in self.config.engines if e.name == game_spec.black_engine), None)
        w_spec = next((e for e in self.config.engines if e.name == game_spec.white_engine), None)
        if b_spec is None or w_spec is None:
            return (None, None)

        b_id = self._normalize_instance_id(b_spec.instance_id) or "local"
        w_id = self._normalize_instance_id(w_spec.instance_id) or "local"
        return (b_id, w_id)

    def _resolve_assignment_for_spec(self, spec: GameSpec) -> dict[str, str | None]:
        default_black, default_white = self._infer_game_assignment(spec)
        override_black = self._normalize_instance_id(spec.assigned_instance_black)
        override_white = self._normalize_instance_id(spec.assigned_instance_white)

        resolved_black = override_black if override_black is not None else default_black
        resolved_white = override_white if override_white is not None else default_white

        if resolved_black == resolved_white:
            combined = resolved_black
        else:
            if resolved_black is None and resolved_white is None:
                combined = None
            else:
                combined = "split"

        return {
            "black": resolved_black,
            "white": resolved_white,
            "combined": combined,
        }

    @staticmethod
    def _shared_override_label(spec: GameSpec) -> str | None:
        black = TournamentRunner._normalize_instance_id(spec.assigned_instance_black)
        white = TournamentRunner._normalize_instance_id(spec.assigned_instance_white)
        if black and white and black == white:
            return black
        if black and not white:
            return black
        if white and not black:
            return white
        if black or white:
            return "split"
        return None

    def _serialize_assignment_override(self, spec: GameSpec) -> JsonObject | None:
        black = self._normalize_instance_id(spec.assigned_instance_black)
        white = self._normalize_instance_id(spec.assigned_instance_white)
        require_install = bool(spec.require_install)
        if black is None and white is None and not require_install:
            return None
        payload: JsonObject = {}
        if black is not None:
            payload["black"] = black
        if white is not None:
            payload["white"] = white
        if black is not None and white is not None and black == white:
            payload.setdefault("shared", black)
        if require_install:
            payload["require_install"] = True
        if (
            payload.get("black") is not None
            and payload.get("white") is not None
            and payload.get("black") != payload.get("white")
        ):
            payload["mode"] = "per_color"
        elif payload:
            payload["mode"] = "shared"
        return payload or None

    def _apply_assignment_override(self, spec: GameSpec, payload: str | dict[str, object] | None) -> None:
        spec.assigned_instance_black = None
        spec.assigned_instance_white = None
        spec.require_install = False

        if payload is None:
            return
        if isinstance(payload, str):
            normalized = self._normalize_instance_id(payload)
            spec.assigned_instance_black = normalized
            spec.assigned_instance_white = normalized
            return
        payload_dict = payload

        shared = self._normalize_instance_id(payload_dict.get("shared"))
        black = self._normalize_instance_id(payload_dict.get("black"))
        white = self._normalize_instance_id(payload_dict.get("white"))
        mode = str(payload_dict.get("mode") or "").strip().lower()

        if mode == "shared" and shared is not None:
            spec.assigned_instance_black = shared
            spec.assigned_instance_white = shared
        else:
            if black is not None:
                spec.assigned_instance_black = black
            if white is not None:
                spec.assigned_instance_white = white
            if shared is not None and spec.assigned_instance_black is None and spec.assigned_instance_white is None:
                spec.assigned_instance_black = shared
                spec.assigned_instance_white = shared

        if bool(payload_dict.get("require_install")):
            spec.require_install = True

    @staticmethod
    def _assignment_mode(spec: GameSpec) -> str:
        black = TournamentRunner._normalize_instance_id(spec.assigned_instance_black)
        white = TournamentRunner._normalize_instance_id(spec.assigned_instance_white)
        if black is None and white is None:
            return "auto"
        if black == white:
            return "shared"
        return "per_color"

    def _refresh_game_assignments(self) -> None:
        assignments: dict[str, dict[str, str | None]] = {}
        for spec in self.game_schedule:
            assignments[spec.game_id] = self._resolve_assignment_for_spec(spec)
        for spec in self._cancelled_specs.values():
            assignments.setdefault(spec.game_id, self._resolve_assignment_for_spec(spec))
        self._game_assignments = assignments

    def _reset_display_order(self) -> None:
        self._game_display_order = {spec.game_id: index for index, spec in enumerate(self.game_schedule, start=1)}

    def _ensure_display_order_for_specs(self, specs: Iterable[GameSpec]) -> None:
        next_index = max(self._game_display_order.values(), default=0) + 1
        for spec in specs:
            gid = spec.game_id
            if gid not in self._game_display_order:
                self._game_display_order[gid] = next_index
                next_index += 1

    def _ensure_display_order_for_id(self, game_id: str) -> int:
        order = self._game_display_order.get(game_id)
        if order is None:
            order = max(self._game_display_order.values(), default=0) + 1
            self._game_display_order[game_id] = order
        return order

    def _reset_schedule_tracking(self) -> None:
        self.cancelled_game_ids.clear()
        self._cancelled_specs.clear()
        self._reset_display_order()
        # Reset counters whenever we materialise a fresh schedule.
        self.original_total_games = len(self.game_schedule)
        self._refresh_game_assignments()

    def _write_schedule_file(self, schedule: list[GameSpec]) -> None:
        """Persist the provided schedule to the run directory for inspection."""

        if self._is_generate_run():
            return

        rd = self.run_dir
        schedule_path = rd / "game_schedule.json"
        active_instances: dict[str, str] = {}
        if self.instance_pool is not None:
            for inst in self.instance_pool.list_instances():
                for gid in inst.active_games.keys():
                    active_instances[gid] = inst.name

        def _status(game_id: str) -> str:
            if game_id in self.cancelled_game_ids:
                return "cancelled"
            if game_id in self.completed_game_ids:
                return "completed"
            if game_id in active_instances:
                return "running"
            return "pending"

        def _combined_assignment(game_id: str) -> str | None:
            if game_id in active_instances:
                return active_instances[game_id]
            entry = self._game_assignments.get(game_id)
            return entry.get("combined") if entry is not None else None

        def _resolved_assignment(game_id: str) -> tuple[str | None, str | None]:
            entry = self._game_assignments.get(game_id)
            if entry is not None:
                return entry.get("black"), entry.get("white")
            return (None, None)

        payload: list[JsonObject] = []
        for spec in schedule:
            resolved_black, resolved_white = _resolved_assignment(spec.game_id)
            payload.append(
                {
                    "game_id": spec.game_id,
                    "black": spec.black_engine,
                    "white": spec.white_engine,
                    "round": spec.round_num,
                    "sfen": spec.initial_sfen,
                    "status": _status(spec.game_id),
                    "assigned_instance": _combined_assignment(spec.game_id),
                    "assigned_override": self._shared_override_label(spec),
                    "assigned_override_black": self._normalize_instance_id(spec.assigned_instance_black),
                    "assigned_override_white": self._normalize_instance_id(spec.assigned_instance_white),
                    "require_install": bool(spec.require_install),
                    "resolved_instance_black": resolved_black,
                    "resolved_instance_white": resolved_white,
                }
            )

        if self._cancelled_specs:
            for spec in self._cancelled_specs.values():
                resolved_black, resolved_white = _resolved_assignment(spec.game_id)
                payload.append(
                    {
                        "game_id": spec.game_id,
                        "black": spec.black_engine,
                        "white": spec.white_engine,
                        "round": spec.round_num,
                        "sfen": spec.initial_sfen,
                        "status": "cancelled",
                        "assigned_instance": _combined_assignment(spec.game_id),
                        "assigned_override": self._shared_override_label(spec),
                        "assigned_override_black": self._normalize_instance_id(spec.assigned_instance_black),
                        "assigned_override_white": self._normalize_instance_id(spec.assigned_instance_white),
                        "require_install": bool(spec.require_install),
                        "resolved_instance_black": resolved_black,
                        "resolved_instance_white": resolved_white,
                    }
                )
        schedule_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _generate_schedule_for_seed(self, *, seed: str | None) -> list[GameSpec]:
        """Generate a full schedule using the provided seed without mutating state."""

        seed_str = str(seed) if seed is not None else str(self.config.tournament.seed)
        games = self.scheduler.generate_schedule(
            engines=self.config.engines,
            games_per_pair=self.config.tournament.games_per_pair,
            seed=seed_str,
            initial_positions=self.config.rules.initial_positions,
        )

        original_seed = self.config.tournament.seed
        try:
            if seed is not None:
                self.config.tournament.seed = int(seed_str)
            ordered = self._reorder_and_shuffle(games)
        finally:
            if seed is not None:
                self.config.tournament.seed = original_seed

        return ordered

    async def _maybe_apply_pending_reschedule(self) -> bool:
        """Apply a queued reschedule request after the orchestrator drains."""

        pending_schedule: list[GameSpec] | None
        pending_seed: int | None
        async with self._reschedule_lock:
            pending_schedule = self._pending_reschedule
            pending_seed = self._pending_reschedule_seed
            self._pending_reschedule = None
            self._pending_reschedule_seed = None

        if pending_schedule is None:
            return False

        if pending_seed is not None:
            self.config.tournament.seed = pending_seed

        self.game_schedule = pending_schedule
        if self._completed_game_summaries:
            self._completed_game_summaries = {
                gid: summary
                for gid, summary in self._completed_game_summaries.items()
                if gid in self.completed_game_ids
            }
        self._reset_schedule_tracking()
        self._write_schedule_file(self.game_schedule)
        self._save_run_state()

        if self._dashboard_enabled:
            await self._update_dashboard()

        self._notify_schedule_available()
        return True

    def _save_run_state(self, finished: bool = False) -> None:
        """Save current run state for resume.

        Args:
            finished: Whether tournament is complete
        """
        summaries_for_state: dict[str, JsonObject] = {}
        for gid in self.completed_game_ids:
            summary = self._completed_game_summaries.get(gid)
            if not summary:
                continue
            summaries_for_state[gid] = {
                "result_code": summary.get("result_code"),
                "result_abbr": summary.get("result_abbr"),
                "result_label": summary.get("result_label"),
                "result_detail": summary.get("result_detail"),
                "total_plies": summary.get("total_plies"),
                "end_time": summary.get("end_time"),
            }

        config_payload: JsonObject = {
            "experiment_name": self.config.experiment_name,
            "engines": [e.name for e in self.config.engines],
            "tournament": {
                "scheduler": self.config.tournament.scheduler,
                "games_per_pair": self.config.tournament.games_per_pair,
                "seed": self.config.tournament.seed,
            },
            "rules": self._build_rules_payload(),
        }
        sprt_conf = self.config.sprt
        if sprt_conf is not None:
            config_payload["sprt"] = sprt_conf.model_dump(mode="json")
        openbench_conf = self.config.openbench
        if openbench_conf is not None:
            config_payload["openbench"] = openbench_conf.model_dump(mode="json")
        records_output = self.config.records_output
        if records_output is not None:
            config_payload["records_output"] = records_output.model_dump(mode="json")

        if self._is_generate_run():
            run_state: JsonObject = {
                "config": config_payload,
                "schedule_hash": self.config.get_schedule_hash(),
                "total_games": len(self.game_schedule),
                "completed_games_count": len(self.completed_game_ids),
                "cancelled_games_count": len(self.cancelled_game_ids),
                "finished": finished,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if self._openbench_client is not None:
                run_state["openbench_state"] = self._openbench_client.snapshot_state()
            rd = self.run_dir
            run_state_path = rd / "run_state.json"
            with open(run_state_path, "w") as f:
                json.dump(run_state, f, indent=2)
            return

        run_state: JsonObject = {
            "config": config_payload,
            "schedule_hash": self.config.get_schedule_hash(),
            "total_games": len(self.game_schedule),
            "completed_game_ids": list(self.completed_game_ids),
            "cancelled_game_ids": list(self.cancelled_game_ids),
            "cancelled_games": [],
            "original_total_games": self.original_total_games
            or (len(self.game_schedule) + len(self.cancelled_game_ids)),
            "game_display_order": dict(self._game_display_order),
            "finished": finished,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._sprt is not None:
            run_state["sprt_state"] = self._sprt.to_snapshot()
        if self._openbench_client is not None:
            run_state["openbench_state"] = self._openbench_client.snapshot_state()

        cancelled_entries: list[JsonObject] = []
        for spec in self._cancelled_specs.values():
            entry = {
                "game_id": spec.game_id,
                "black": spec.black_engine,
                "white": spec.white_engine,
                "round": spec.round_num,
                "sfen": spec.initial_sfen,
            }
            assignment_payload = self._serialize_assignment_override(spec)
            if assignment_payload:
                entry["assignment"] = assignment_payload
                shared_value = assignment_payload.get("shared")
                if shared_value is not None:
                    entry["assigned_instance"] = shared_value
                else:
                    shared_label = self._shared_override_label(spec)
                    if shared_label is not None:
                        entry["assigned_instance"] = shared_label
            else:
                shared_label = self._shared_override_label(spec)
                if shared_label is not None:
                    entry["assigned_instance"] = shared_label
            if bool(spec.require_install):
                entry["require_install"] = True
            cancelled_entries.append(entry)
        run_state["cancelled_games"] = cancelled_entries

        if summaries_for_state:
            run_state["completed_game_summaries"] = summaries_for_state

        overrides: JsonObject = {}
        for spec in self.game_schedule:
            assignment_payload = self._serialize_assignment_override(spec)
            if assignment_payload:
                overrides[spec.game_id] = assignment_payload
        for spec in self._cancelled_specs.values():
            assignment_payload = self._serialize_assignment_override(spec)
            if assignment_payload:
                overrides[spec.game_id] = assignment_payload
        if overrides:
            # Persist per-game instance choices so a resume or dashboard refresh can restore the selection
            run_state["game_instance_overrides"] = overrides

        rd = self.run_dir
        run_state_path = rd / "run_state.json"
        with open(run_state_path, "w") as f:
            json.dump(run_state, f, indent=2)

    def _cleanup_existing_run(self) -> None:
        """Remove existing run artifacts."""
        logger.debug("Cleaning up existing run")
        rd = self.run_dir
        self.cleanup_run_dir(
            rd,
            files=[
                "game.db",
                "run_state.json",
                "game_schedule.json",
                "completed.flag",
                "index.html",
                "data/shogi-board.js",
                "data/arena_port.js",
            ],
            dirs=["spsa", "html", "static"],
        )

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
            summary_updater=self._update_dashboard,
            api_server=self.api_server,
            cancelled_provider=lambda: set(self.cancelled_game_ids),
        )

        # Keep a reference for early stop coordination
        self._orchestrator = orchestrator
        self._tournament_orchestrator = orchestrator

        return orchestrator

    async def _handle_game_completion(
        self,
        game_spec: GameSpec,
        game_info: rshogi.record.GameRecord,
        *,
        _worker_idx: int | None,
        stop_requested: bool,
    ) -> None:
        """Handle post-processing for a completed game."""

        update_dashboard = False
        async with self._completion_lock:
            self._engine_metadata_cache = None
            record = game_info
            metadata_attrs = dict(record.metadata.attributes)
            record.update_metadata(
                {
                    "black_player": game_spec.black_engine,
                    "white_player": game_spec.white_engine,
                    "game_type": ("generate" if self._is_generate_run() else "arena"),
                }
            )
            for key, value in self._build_metadata_attributes(metadata_attrs).items():
                record.set_metadata_attribute(str(key), str(value))
            summary = self._summarize_game_completion(record)

            db = self.db_service
            result = record.result
            should_persist = result != GameResult.PAUSED
            if should_persist and stop_requested and result in (GameResult.PAUSED, _GAME_RESULT_ERROR):
                should_persist = False
            if should_persist and db is not None:
                db.append_record_list([record])
                participation = _extract_participation(record)
                if participation:
                    game_id_raw = record.metadata.attributes.get("game_name")
                    game_id_name = coerce_str(game_id_raw)
                    game_id = db.get_game_id_by_name(str(game_id_name)) if game_id_name else None
                    if game_id is not None:
                        db.record_game_participation(game_id=game_id, participation=participation)
            if should_persist and self._record_writer is not None:
                self._record_writer.append_record(record)

            rating_service = self.rating_service
            if rating_service is not None:
                rating_service.update_ratings(
                    game_spec.black_engine,
                    game_spec.white_engine,
                    result,
                )

            self.completed_game_ids.add(game_spec.game_id)
            self._completed_game_summaries[game_spec.game_id] = summary
            self._update_sprt_state(game_spec, record)
            if self._sprt and self._sprt.is_finished():
                if self._sprt.games_played >= self._sprt_min_games:
                    self.stop_controller.request_stop(reason="sprt-finished")
            try:
                await self._sync_openbench_after_game()
            except OpenBenchError as exc:
                if self._openbench_client is not None and self._openbench_client.strict:
                    raise
                logger.warning("OpenBench submission failed; continuing (strict=false): %s", exc)
            update_dashboard = self._dashboard_enabled
            self._save_run_state()

        if update_dashboard:
            await self._update_dashboard()

        result = record.result
        result_code = result.value
        self.progress.on_game_complete(
            {
                "game_id": game_spec.game_id,
                "black": game_spec.black_engine,
                "white": game_spec.white_engine,
                "result_code": result_code,
                "completed_games": len(self.completed_game_ids),
                "total_games": self.original_total_games or len(self.game_schedule),
            }
        )

    @staticmethod
    def _result_detail_from_result(result: GameResult) -> str | None:
        detail_map = {
            _GAME_RESULT_BLACK_WIN_BY_DECLARATION: "declaration",
            _GAME_RESULT_WHITE_WIN_BY_DECLARATION: "declaration",
            _GAME_RESULT_BLACK_WIN_BY_FORFEIT: "forfeit",
            _GAME_RESULT_WHITE_WIN_BY_FORFEIT: "forfeit",
            GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "illegal move",
            GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "illegal move",
            GameResult.BLACK_WIN_BY_TIMEOUT: "timeout",
            GameResult.WHITE_WIN_BY_TIMEOUT: "timeout",
            GameResult.DRAW_BY_REPETITION: "repetition",
            GameResult.DRAW_BY_MAX_PLIES: "max plies",
            GameResult.DRAW_BY_IMPASSE: "impasse",
            _GAME_RESULT_ERROR: "error",
            GameResult.INVALID: "invalid",
            GameResult.PAUSED: "paused",
        }
        return detail_map.get(result)

    @staticmethod
    def _format_result_label(result: GameResult) -> str:
        if result.is_black_win():
            base = "Black Win"
        elif result.is_white_win():
            base = "White Win"
        elif result.is_draw():
            base = "Draw"
        elif result == GameResult.PAUSED:
            return "Paused"
        elif result == _GAME_RESULT_ERROR:
            return "Error"
        elif result == GameResult.INVALID:
            return "Invalid Game"
        else:
            return result.name.replace("_", " ").title()

        suffix = _RESULT_REASON_SUFFIXES.get(result)
        if suffix:
            return f"{base} {suffix}"
        return base

    @classmethod
    def _classify_game_result(cls, result: GameResult) -> tuple[str, str, str | None]:
        detail = cls._result_detail_from_result(result)
        abbr = _RESULT_ABBREVIATIONS.get(result)
        if abbr is None:
            if result.is_black_win():
                abbr = "B"
            elif result.is_white_win():
                abbr = "W"
            elif result.is_draw():
                abbr = "R"
            else:
                abbr = ""
        label = cls._format_result_label(result)
        return abbr, label, detail

    def _summarize_game_completion(self, record: rshogi.record.GameRecord) -> _CompletedGameSummary:
        result_obj = record.result
        result_code = result_obj.value
        result_abbr, result_label, result_detail = self._classify_game_result(result_obj)

        total_plies = len(record.moves)
        start_raw = record.metadata.start_date
        end_raw = record.metadata.end_date
        start_time = datetime_to_iso(start_raw)
        end_time = datetime_to_iso(end_raw)

        summary: _CompletedGameSummary = {
            "result_code": result_code,
            "result_abbr": result_abbr,
            "result_label": result_label,
            "result_detail": result_detail,
            "total_plies": total_plies,
            "start_time": start_time,
            "end_time": end_time,
        }
        return summary

    def _update_sprt_state(self, game_spec: GameSpec, game_info: rshogi.record.GameRecord) -> None:
        if self._sprt is None or self._sprt_pair is None:
            return
        gr = game_info.result

        a, _ = self._sprt_pair
        black = str(game_spec.black_engine)
        white = str(game_spec.white_engine)

        if a == black:
            result = (
                GameResult.WHITE_WIN
                if gr.is_black_win()
                else GameResult.BLACK_WIN
                if gr.is_white_win()
                else GameResult.DRAW_BY_MAX_PLIES
            )
        elif a == white:
            result = (
                GameResult.WHITE_WIN
                if gr.is_white_win()
                else GameResult.BLACK_WIN
                if gr.is_black_win()
                else GameResult.DRAW_BY_MAX_PLIES
            )
        else:
            return

        self._sprt.add_game_result(result)

    async def get_schedule_snapshot(self) -> JsonObject:
        """Return a schedule summary with status for each game."""

        async with self._reschedule_lock:
            schedule_copy = list(self.game_schedule)
            completed_ids = set(self.completed_game_ids)
            pending_reschedule = self._pending_reschedule is not None
            seed = str(self.config.tournament.seed)

        engine_instance_defaults: dict[str, str | None] = {}
        for spec in self.config.engines:
            name = str(spec.name)
            inst_raw = spec.instance_id
            if inst_str := coerce_str(inst_raw):
                engine_instance_defaults[name] = inst_str
            else:
                engine_instance_defaults[name] = "local"

        active_game_ids: set[str] = set()
        active_instances: dict[str, str] = {}
        active_side_instances: dict[str, dict[str, str]] = {}
        active_start_times: dict[str, float | datetime | str] = {}
        instance_kinds: dict[str, str] = {}
        pool = self.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                inst_name = instance.name
                instance_kinds[inst_name] = instance.type.value
                for gid, active in instance.active_games.items():
                    active_game_ids.add(gid)
                    previous = active_instances.get(gid)
                    if previous is not None and previous != inst_name and previous != "split":
                        active_instances[gid] = "split"
                    elif previous == "split":
                        # Already marked as split; keep it stable
                        pass
                    else:
                        active_instances[gid] = inst_name
                    if active.roles:
                        side_map = active_side_instances.setdefault(gid, {})
                        for role in active.roles:
                            if role.role in {"black", "white"}:
                                side_map[role.role] = inst_name
                    started_raw = active.started_at
                    if started_raw is not None:
                        if gid not in active_start_times:
                            active_start_times[gid] = started_raw
                        else:
                            existing = active_start_times[gid]
                            if is_strict_numeric(existing) and is_strict_numeric(started_raw):
                                active_start_times[gid] = min(existing, started_raw)

        self._ensure_display_order_for_specs(schedule_copy)
        self._ensure_display_order_for_specs(self._cancelled_specs.values())

        def _instance_kind(instance_name: str | None) -> str | None:
            if instance_name is None:
                return None
            kind = instance_kinds.get(instance_name)
            if kind:
                return kind
            if instance_name == "local":
                return "local"
            return None

        items: list[JsonObject] = []
        for game_spec in schedule_copy:
            gid = game_spec.game_id
            display_order = self._ensure_display_order_for_id(gid)
            order_index = display_order - 1
            if gid in completed_ids:
                status = "completed"
            elif gid in active_game_ids:
                status = "running"
            else:
                status = "pending"

            side_map = active_side_instances.get(gid, {})
            assigned_entry = self._game_assignments.get(gid) or {}

            black_default = engine_instance_defaults.get(game_spec.black_engine)
            white_default = engine_instance_defaults.get(game_spec.white_engine)
            resolved_black = assigned_entry.get("black") or black_default
            resolved_white = assigned_entry.get("white") or white_default

            black_instance = side_map.get("black") or resolved_black
            white_instance = side_map.get("white") or resolved_white

            if gid in active_instances:
                assigned_candidate: str | None = active_instances[gid]
            else:
                assigned_candidate = assigned_entry.get("combined")
            if assigned_candidate in {None, "split"}:
                if black_instance and white_instance:
                    if black_instance == white_instance:
                        assigned_candidate = black_instance
                    elif assigned_candidate is None:
                        assigned_candidate = "split"
            elif (
                assigned_candidate
                and assigned_candidate != "split"
                and (black_instance and white_instance and black_instance != white_instance)
            ):
                assigned_candidate = "split"

            assigned_override = self._shared_override_label(game_spec)

            entry = {
                "order": order_index,
                "game_id": gid,
                "black": game_spec.black_engine,
                "white": game_spec.white_engine,
                "round": game_spec.round_num,
                "status": status,
                "assigned_instance": assigned_candidate,
                # Expose the manual override so the dashboard can show the preference separately
                "assigned_override": assigned_override,
                "assigned_override_black": self._normalize_instance_id(game_spec.assigned_instance_black),
                "assigned_override_white": self._normalize_instance_id(game_spec.assigned_instance_white),
                "assigned_mode": self._assignment_mode(game_spec),
                "require_install": bool(game_spec.require_install),
                "resolved_instance_black": resolved_black,
                "resolved_instance_white": resolved_white,
                "black_instance": black_instance,
                "white_instance": white_instance,
                "black_instance_kind": _instance_kind(black_instance),
                "white_instance_kind": _instance_kind(white_instance),
                "initial_sfen": game_spec.initial_sfen,
            }

            summary = self._completed_game_summaries.get(gid) if gid in completed_ids else None
            start_time_value: str | None = None
            if summary:
                summary_start = summary.get("start_time")
                if summary_start:
                    start_time_value = summary_start
            if start_time_value is None:
                raw_started = active_start_times.get(gid)
                match raw_started:
                    case int() | float():
                        start_time_value = datetime.fromtimestamp(raw_started, tz=timezone.utc).isoformat()
                    case datetime() as dt:
                        start_time_value = dt.astimezone(timezone.utc).isoformat()
                    case str() if raw_started:
                        start_time_value = raw_started
            if summary:
                entry.update(
                    {
                        "start_time": summary.get("start_time"),
                        "result_code": summary.get("result_code"),
                        "result_abbr": summary.get("result_abbr") or "",
                        "result_label": summary.get("result_label") or "",
                        "result_detail": summary.get("result_detail") or "",
                        "total_plies": summary.get("total_plies"),
                        "end_time": summary.get("end_time"),
                    }
                )
            else:
                entry.update(
                    {
                        "result_code": None,
                        "result_abbr": "",
                        "result_label": "",
                        "result_detail": "",
                        "total_plies": None,
                        "start_time": None,
                        "end_time": None,
                    }
                )

            if start_time_value is not None:
                entry["start_time"] = start_time_value

            items.append(entry)

        for cancelled_spec in self._cancelled_specs.values():
            assignment_entry = self._game_assignments.get(cancelled_spec.game_id) or {}
            black_default = engine_instance_defaults.get(cancelled_spec.black_engine)
            white_default = engine_instance_defaults.get(cancelled_spec.white_engine)
            resolved_black = assignment_entry.get("black") or black_default
            resolved_white = assignment_entry.get("white") or white_default
            assigned_default: str | None
            if resolved_black and resolved_white:
                if resolved_black == resolved_white:
                    assigned_default = resolved_black
                else:
                    assigned_default = "split"
            else:
                assigned_default = resolved_black or resolved_white
            display_order = self._ensure_display_order_for_id(cancelled_spec.game_id)
            order_index = display_order - 1
            items.append(
                {
                    "order": order_index,
                    "game_id": cancelled_spec.game_id,
                    "black": cancelled_spec.black_engine,
                    "white": cancelled_spec.white_engine,
                    "round": cancelled_spec.round_num,
                    "status": "cancelled",
                    "assigned_instance": assigned_default,
                    "assigned_override": self._shared_override_label(cancelled_spec),
                    "assigned_override_black": self._normalize_instance_id(cancelled_spec.assigned_instance_black),
                    "assigned_override_white": self._normalize_instance_id(cancelled_spec.assigned_instance_white),
                    "assigned_mode": self._assignment_mode(cancelled_spec),
                    "require_install": bool(cancelled_spec.require_install),
                    "resolved_instance_black": resolved_black,
                    "resolved_instance_white": resolved_white,
                    "black_instance": resolved_black,
                    "white_instance": resolved_white,
                    "black_instance_kind": _instance_kind(resolved_black),
                    "white_instance_kind": _instance_kind(resolved_white),
                    "initial_sfen": cancelled_spec.initial_sfen,
                }
            )

        completed_count = sum(1 for spec in schedule_copy if spec.game_id in completed_ids)
        running_count = len(active_game_ids)
        cancelled_count = len(self.cancelled_game_ids)
        original_total = self.original_total_games or (len(schedule_copy) + cancelled_count)
        active_total = max(original_total - cancelled_count, 0)
        pending_count = max(active_total - completed_count - running_count, 0)

        stop_ctrl = self.stop_controller
        session_state = self._session_phase
        waiting = session_state == "waiting"
        idle = waiting and pending_count == 0 and running_count == 0

        return {
            "total_games": active_total,
            "original_total_games": original_total,
            "completed_games": completed_count,
            "running_games": running_count,
            "pending_games": pending_count,
            "cancelled_games": cancelled_count,
            "seed": seed,
            "reschedule_pending": pending_reschedule,
            "is_running": self._orchestrator is not None,
            "session_state": session_state,
            "stop_requested": stop_ctrl.stop_requested,
            "stop_reason": stop_ctrl.reason,
            "stop_when_idle": self._stop_when_idle,
            "is_waiting": waiting,
            "is_idle": idle,
            "schedule": items,
        }

    async def request_reschedule(self, *, seed: str | None = None) -> JsonObject:
        """Queue a reschedule using the provided seed and stop the current run."""

        if seed is None:
            new_seed = random.randint(0, 2**32 - 1)
        else:
            seed_text = str(seed).strip()
            if not seed_text or seed_text.lower() == "auto":
                new_seed = random.randint(0, 2**32 - 1)
            else:
                try:
                    new_seed = int(seed_text)
                except ValueError:
                    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
                    new_seed = int(digest[:8], 16)

        completed_ids = set(self.completed_game_ids)
        completed_specs = [spec for spec in self.game_schedule if spec.game_id in completed_ids]
        pending_count = len(self.game_schedule) - len(completed_specs)
        if pending_count <= 0:
            raise RuntimeError("no pending games remain to reschedule")

        new_schedule = self._generate_schedule_for_seed(seed=str(new_seed))
        if len(new_schedule) < pending_count:
            raise RuntimeError("generated schedule shorter than pending games")

        new_pending = new_schedule[:pending_count]
        combined_schedule = completed_specs + new_pending

        apply_immediately = False
        orchestrator = None
        async with self._reschedule_lock:
            self._pending_reschedule = combined_schedule
            self._pending_reschedule_seed = new_seed
            orchestrator = self._orchestrator
            if orchestrator is None:
                apply_immediately = True

        if apply_immediately:
            await self._maybe_apply_pending_reschedule()
            snapshot = await self.get_schedule_snapshot()
            return {
                "status": "applied",
                "pending_games": snapshot.get("pending_games", 0),
                "seed": str(new_seed),
            }

        # Signal orchestrator to finish inflight games and exit
        self.stop_controller.request_stop(reason="reschedule")
        if orchestrator is not None:
            orchestrator.request_stop()

        snapshot = await self.get_schedule_snapshot()
        return {
            "status": "scheduled",
            "pending_games": snapshot.get("pending_games", 0),
            "seed": str(new_seed),
        }

    async def cancel_pending_games(self) -> JsonObject:
        """Cancel remaining pending games while keeping the session alive for future schedules."""

        active_ids: set[str] = set()
        pool = self.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                active_ids.update(instance.active_games.keys())

        async with self._reschedule_lock:
            remaining_specs = [
                spec
                for spec in self.game_schedule
                if spec.game_id not in self.completed_game_ids and spec.game_id not in active_ids
            ]
            self._pending_reschedule = None
            self._pending_reschedule_seed = None

        cancelled_ids = [spec.game_id for spec in remaining_specs]
        for spec in remaining_specs:
            self._ensure_display_order_for_id(spec.game_id)
            self.cancelled_game_ids.add(spec.game_id)
            self._cancelled_specs[spec.game_id] = spec
        if cancelled_ids:
            self.game_schedule = [spec for spec in self.game_schedule if spec.game_id not in cancelled_ids]

        self.original_total_games = max(
            self.original_total_games,
            len(self.game_schedule) + len(self.cancelled_game_ids),
        )
        self._refresh_game_assignments()
        self._write_schedule_file(self.game_schedule)
        self._save_run_state()

        controller = self.stop_controller
        controller.request_stop(reason="cancelled")
        if self._orchestrator is not None:
            self._orchestrator.request_stop()

        if self._dashboard_enabled:
            await self._update_dashboard()

        return {
            "status": "cancelled",
            "cancelled_games": cancelled_ids,
            "cancelled_count": len(cancelled_ids),
            "running_games": list(active_ids),
            "total_games": self.original_total_games,
        }

    async def cancel_game(self, game_id: str) -> JsonObject:
        """Cancel a single pending game by its identifier."""

        pool = self.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                if game_id in instance.active_games:
                    raise RuntimeError(f"game {game_id} is currently running and cannot be cancelled")

        async with self._reschedule_lock:
            if game_id in self.completed_game_ids:
                raise RuntimeError(f"game {game_id} has already completed")
            if game_id in self.cancelled_game_ids:
                raise RuntimeError(f"game {game_id} has already been cancelled")

            spec_index: int | None = None
            for idx, spec in enumerate(self.game_schedule):
                if spec.game_id == game_id:
                    spec_index = idx
                    target_spec = spec
                    break
            else:
                target_spec = None

            if target_spec is None or spec_index is None:
                raise ValueError(f"game {game_id} not found in pending schedule")

            # Remove from active schedule and track as cancelled
            self.game_schedule.pop(spec_index)
            self._ensure_display_order_for_id(game_id)
            self.cancelled_game_ids.add(game_id)
            self._cancelled_specs[game_id] = target_spec

            self.original_total_games = max(
                self.original_total_games,
                len(self.game_schedule) + len(self.cancelled_game_ids),
            )

            # Update cached metadata so future snapshots and persists exclude the cancelled game
            self._refresh_game_assignments()
            self._write_schedule_file(self.game_schedule)
            self._save_run_state()

        if self._dashboard_enabled:
            await self._update_dashboard()

        pending_count = sum(
            1
            for spec in self.game_schedule
            if spec.game_id not in self.completed_game_ids and spec.game_id not in self.cancelled_game_ids
        )

        return {
            "status": "cancelled",
            "cancelled_game": game_id,
            "pending_games": pending_count,
            "cancelled_count": len(self.cancelled_game_ids),
        }

    async def restore_game(self, game_id: str) -> JsonObject:
        """Restore a previously cancelled game back into the pending schedule."""

        pool = self.instance_pool
        if pool is not None:
            for instance in pool.list_instances():
                if game_id in instance.active_games:
                    raise RuntimeError(f"game {game_id} is currently running and cannot be restored")

        async with self._reschedule_lock:
            already_scheduled = any(spec.game_id == game_id for spec in self.game_schedule)
            restored_spec = self._cancelled_specs.get(game_id)
            if restored_spec is None or game_id not in self.cancelled_game_ids:
                if game_id in self.completed_game_ids:
                    raise RuntimeError(f"game {game_id} has already completed")
                if already_scheduled:
                    raise RuntimeError(f"game {game_id} is already scheduled")
                raise ValueError(f"game {game_id} is not a cancelled game")

            if already_scheduled:
                raise RuntimeError(f"game {game_id} is already scheduled")

            self.cancelled_game_ids.discard(game_id)
            self._cancelled_specs.pop(game_id, None)
            self.completed_game_ids.discard(game_id)
            self._completed_game_summaries.pop(game_id, None)

            display_order = self._game_display_order.get(game_id)
            insert_index = len(self.game_schedule)
            if display_order is not None:
                for idx, spec in enumerate(self.game_schedule):
                    other_order = self._game_display_order.get(spec.game_id)
                    if other_order is not None and other_order > display_order:
                        insert_index = idx
                        break

            self.game_schedule.insert(insert_index, restored_spec)

            effective_order = display_order
            if effective_order is None:
                effective_order = max(self._game_display_order.values(), default=insert_index) + 1
            self._game_display_order[game_id] = effective_order

            if self._tournament_orchestrator is not None:
                await self._tournament_orchestrator.enqueue_restored_game(restored_spec, effective_order)

            self._refresh_game_assignments()
            self._write_schedule_file(self.game_schedule)
            self._save_run_state()
            self._notify_schedule_available()

        if self._dashboard_enabled:
            await self._update_dashboard()

        pending_count = sum(
            1
            for spec in self.game_schedule
            if spec.game_id not in self.completed_game_ids and spec.game_id not in self.cancelled_game_ids
        )

        return {
            "status": "restored",
            "restored_game": game_id,
            "pending_games": pending_count,
            "cancelled_count": len(self.cancelled_game_ids),
        }

    async def set_game_instance(
        self,
        game_id: str,
        *,
        mode: str = "auto",
        shared_instance: str | None = None,
        black_instance: str | None = None,
        white_instance: str | None = None,
        require_install: bool = False,
    ) -> JsonObject:
        """Assign or clear preferred instances for a pending game."""

        normalized_mode = (mode or "auto").strip().lower()
        if normalized_mode not in {"auto", "shared", "per_color"}:
            raise ValueError(f"unsupported assignment mode: {mode}")

        shared_normalized = self._normalize_instance_id(shared_instance)
        black_normalized = self._normalize_instance_id(black_instance)
        white_normalized = self._normalize_instance_id(white_instance)

        if normalized_mode == "auto":
            target_black = None
            target_white = None
        elif normalized_mode == "shared":
            shared_value = shared_normalized or black_normalized or white_normalized
            target_black = shared_value
            target_white = shared_value
        else:
            target_black = black_normalized
            target_white = white_normalized
            if target_black is None and target_white is None and shared_normalized is not None:
                target_black = shared_normalized
                target_white = shared_normalized

        pool = self.instance_pool
        for candidate in (target_black, target_white):
            if candidate is None:
                continue
            if candidate == "local":
                if pool is not None and pool.get_instance("local") is None:
                    pool.ensure_local_instance()
                continue
            if pool is None:
                raise ValueError("instance overrides require an instance pool")
            if pool.get_instance(candidate) is None:
                raise ValueError(f"unknown instance '{candidate}'")

        async with self._reschedule_lock:
            if game_id in self.completed_game_ids:
                raise RuntimeError(f"game {game_id} has already completed")
            if game_id in self.cancelled_game_ids:
                raise RuntimeError(f"game {game_id} has been cancelled")

            target_spec: GameSpec | None = None
            for spec in self.game_schedule:
                if spec.game_id == game_id:
                    target_spec = spec
                    break

            if target_spec is None:
                raise ValueError(f"game {game_id} not found in pending schedule")

            if pool is not None:
                for instance in pool.list_instances():
                    if game_id in instance.active_games:
                        raise RuntimeError(f"game {game_id} is currently running and cannot be reassigned")

            # Persist the override so the orchestrator and dashboard keep using the chosen instances
            target_spec.assigned_instance_black = target_black
            target_spec.assigned_instance_white = target_white
            target_spec.require_install = bool(require_install)
            self._refresh_game_assignments()
            self._write_schedule_file(self.game_schedule)
            self._save_run_state()

        if self._dashboard_enabled:
            await self._update_dashboard()

        resolved = self._resolve_assignment_for_spec(target_spec)

        return {
            "status": "assigned",
            "game_id": game_id,
            "mode": normalized_mode,
            "black_instance": target_spec.assigned_instance_black,
            "white_instance": target_spec.assigned_instance_white,
            "resolved_black": resolved.get("black"),
            "resolved_white": resolved.get("white"),
            "require_install": bool(target_spec.require_install),
        }

    async def run(
        self,
        *,
        progress_reporter: ProgressReporter | None = None,
        tqdm: bool = False,
    ) -> TournamentRunResult | None:
        """Run tournament orchestration with support for dashboard rescheduling."""
        previous = self._progress
        if progress_reporter is not None and tqdm:
            warnings.warn(
                "Both progress_reporter and tqdm=True were provided; progress_reporter takes priority.",
                stacklevel=2,
            )
        if progress_reporter is None and tqdm:
            progress_reporter = TqdmProgressReporter()
        if progress_reporter is not None:
            self._progress = progress_reporter
        controller = self.stop_controller
        try:
            await self.prepare_run_dir()
            await self.prepare_domain()
            await self.init_services()

            dash = self.get_dashboard_params()
            if dash is not None:
                run_dir, port, num_workers = dash
                await self.start_dashboard_server(run_dir, port, num_workers)
                await self.seed_initial_summary()

            session_context = self.build_session_context()
            self.set_session_context(session_context)

            loop = RescheduleLoop()

            async def run_iteration(active_controller: SessionStopController) -> None:
                if not self._has_pending_games():
                    return
                self._session_phase = "running"
                hooks = self.create_lifecycle_hooks(active_controller)
                self.set_lifecycle_hooks(hooks)
                orchestrator = await self.create_orchestrator(hooks, session_context)
                await self.run_pre_orchestration_hooks(orchestrator)
                await BaseSessionRunner.run_orchestrator(self, orchestrator, orchestrator.run())
                self._session_phase = "draining"

            async def decide_next(active_controller: SessionStopController) -> RescheduleDecision:
                if self._services_closed:
                    self._session_phase = "stopped"
                    return RescheduleDecision(action=RescheduleAction.STOP)

                rescheduled = await self._maybe_apply_pending_reschedule()
                if rescheduled:
                    return RescheduleDecision(action=RescheduleAction.CONTINUE, reset_controller=True)

                if active_controller.stop_requested:
                    if active_controller.reason == "cancelled":
                        self._session_phase = "waiting"
                        return RescheduleDecision(action=RescheduleAction.WAIT, reset_controller=True)
                    self._session_phase = "stopping"
                    return RescheduleDecision(action=RescheduleAction.STOP)

                if self._has_pending_games():
                    return RescheduleDecision(action=RescheduleAction.CONTINUE, reset_controller=True)

                if not self._pending_reschedule and not active_controller.stop_requested:
                    logger.debug("All scheduled games completed; stopping tournament runner")
                    self._stop_when_idle = True
                    return RescheduleDecision(action=RescheduleAction.STOP)

                self._session_phase = "waiting"
                return RescheduleDecision(action=RescheduleAction.WAIT, reset_controller=True)

            async def on_wait() -> None:
                await self._wait_for_new_schedule()

            def on_reset(new_controller: SessionStopController) -> None:
                self.reset_stop_controller(new_controller)

            await loop.run(
                controller=controller,
                run_iteration=run_iteration,
                decide_next=decide_next,
                on_wait=on_wait,
                on_reset=on_reset,
            )

            if self.services_closed():
                # Ctrl+C handled inside run_orchestrator closes services; skip result computation.
                self._session_phase = "finished"
                self.progress.finalize({"status": "cancelled"})
                return None

            self._session_phase = "stopping"
            final = await self._calculate_results()
            await self._finalize_tournament(final)
            await self.stop_services()
            self._session_phase = "finished"
            sprt_status = self._sprt.get_status() if self._sprt is not None else None
            result = TournamentRunResult(
                tournament=final,
                sprt=sprt_status,
                run_id=str(self.config.experiment_name or self.run_dir.name),
                run_dir=self.run_dir,
                storage=self.storage,
            )
            self.progress.finalize({"status": "finished", "result": result})
            return result

        except (asyncio.CancelledError, KeyboardInterrupt):
            # Honour Ctrl+C while paused by making sure services stop exactly once.
            controller.request_stop(reason="cancelled")
            self._session_phase = "stopping"
            await self.stop_services()
            self._session_phase = "finished"
            self.progress.finalize({"status": "cancelled"})
            return None
        finally:
            self._progress = previous

    async def _calculate_results(self) -> TournamentResults:
        """Calculate final tournament results.

        Returns:
            Complete tournament results
        """
        logger.debug("Calculating tournament results")

        # Get all games from database
        db = self.db_service
        assert db is not None
        games = db.get_games_with_players()

        # Calculate per-engine statistics
        engine_stats: dict[str, _EngineWdlCounts] = {}
        for engine in self.config.engines:
            entry: _EngineWdlCounts = {
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 0,
            }
            engine_stats[str(engine.name)] = entry

        # Calculate per-pair results
        pair_results = {}

        # Process each game
        for game in games:
            black = game["black_player"]
            white = game["white_player"]
            result = game["result"]

            # Update engine stats
            engine_stats[black]["games"] += 1
            engine_stats[white]["games"] += 1

            if result.is_black_win():
                engine_stats[black]["wins"] += 1
                engine_stats[white]["losses"] += 1
            elif result.is_white_win():
                engine_stats[white]["wins"] += 1
                engine_stats[black]["losses"] += 1
            elif result.is_draw():
                engine_stats[black]["draws"] += 1
                engine_stats[white]["draws"] += 1

            # Update pair results
            pair = (black, white) if black <= white else (white, black)
            if pair not in pair_results:
                pair_results[pair] = {
                    f"{pair[0]}_wins": 0,
                    f"{pair[1]}_wins": 0,
                    "draws": 0,
                }

            if result.is_black_win():
                if black == pair[0]:
                    pair_results[pair][f"{pair[0]}_wins"] += 1
                else:
                    pair_results[pair][f"{pair[1]}_wins"] += 1
            elif result.is_white_win():
                if white == pair[0]:
                    pair_results[pair][f"{pair[0]}_wins"] += 1
                else:
                    pair_results[pair][f"{pair[1]}_wins"] += 1
            elif result.is_draw():
                pair_results[pair]["draws"] += 1

        cancelled_count = len(self.cancelled_game_ids)
        total_games = self.original_total_games or (len(self.game_schedule) + cancelled_count)

        return TournamentResults(
            engine_stats=engine_stats,
            pair_results=pair_results,
            completed_games=list(self.completed_game_ids),
            total_games=total_games,
            completed_games_count=len(self.completed_game_ids),
            cancelled_games_count=cancelled_count,
        )

    async def _update_dashboard(self) -> None:
        """Update dashboard with current results."""
        results = await self._calculate_results()

        # Get leaderboard
        leaderboard = results.get_leaderboard()

        engines_meta = self.engine_metadata
        engine_tc_map, default_tc_spec = self.engine_time_controls
        engine_names = [str(e.name) for e in self.config.engines]
        engine_instances = self._engine_instance_defaults()

        cancelled_count = results.cancelled_games_count
        active_total_games = max(0, results.total_games - cancelled_count)

        summary_data: dict[str, object] = {
            "tournamentType": self._resolve_tournament_type(),
            "mode": self._summary_source,
            "flipPolicy": self.config.rules.initial_positions.flip_policy,
            "numEngines": len(engine_names),
            "runDir": str(self.run_dir),
            "leaderboard": leaderboard,
            "ratingInitial": self.config.rating.initial,
            "engines": engine_names,
            "enginesMeta": engines_meta,
            "engineTimeControls": engine_tc_map,
            "defaultTimeControl": default_tc_spec,
            "engineInstances": engine_instances,
            "engineStats": {
                name: {
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "draws": stats["draws"],
                    "games": stats.get("games", stats["wins"] + stats["losses"] + stats["draws"]),
                }
                for name, stats in results.engine_stats.items()
            },
            "pairResults": {f"{k[0]}_vs_{k[1]}": v for k, v in results.pair_results.items()},
            "timestamp": datetime.now().isoformat(),
            "games": {
                "completed": results.completed_games_count,
                "total": active_total_games,
                "cancelled": cancelled_count,
            },
        }

        rules_payload = self._build_rules_payload()
        if rules_payload:
            summary_data["rules"] = rules_payload
            summary_data["initialPositions"] = rules_payload.get("initial_positions")
            summary_data["repetitionOccurrencesToDraw"] = rules_payload.get("repetition_occurrences_to_draw")
            ipos_val = rules_payload.get("initial_positions")
            summary_data["flipPolicy"] = (
                summary_data.get("flipPolicy")
                or rules_payload.get("flip_policy")
                or (ipos_val.get("flip_policy") if isinstance(ipos_val, dict) else None)
            )
            summary_data["tournamentConfig"] = {"rules": rules_payload}
            sprt_payload = self._build_sprt_payload()
            if sprt_payload and "sprt" not in summary_data:
                summary_data["sprt"] = sprt_payload
            if self._is_generate_run():
                generate_conf = self.config.generate
                if generate_conf is not None:
                    summary_data["generateConfig"] = generate_conf.model_dump(mode="json")
                records_output = self.config.records_output
                if records_output is not None:
                    summary_data["recordsOutput"] = records_output.model_dump(mode="json")
        if self._record_writer is not None:
            summary_data["recordsSummary"] = self._record_writer.get_records_summary()

        # Add BTD rating estimates for standings (order-independent ratings)
        games = self.db_service.get_games_with_players() if self.db_service else []

        # Choose anchor consistently: prefer first engine in config (gauntlet baseline)
        anchor_name = engine_names[0] if engine_names else None

        # Provide full engine list so anchor badge appears before first game
        btd = BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)
        # Build nested covariance: {i: {j: cov}}
        cov_map: dict[str, dict[str, float]] = {}
        rc = btd.rating_cov
        if rc is not None:
            for (i, j), v in rc.items():
                cov_map.setdefault(i, {})[j] = float(v)

        summary_data["btd"] = {
            "ratings": {
                name: {"elo": float(btd.ratings[name]), "se": float(btd.rating_se.get(name, 0.0))}
                for name in btd.ratings.keys()
            },
            "anchor": btd.anchor,
            "gamma_elo": float(btd.gamma_elo),
            "gamma_elo_se": float(btd.gamma_elo_se or 0.0),
            "draw_eq": float(btd.draw_eq),
            "draw_eq_se": float(btd.draw_eq_se or 0.0),
            "rating_cov": cov_map,
        }

        # Add pentanomial stats when available
        if self.db_service:
            penta = compute_pentanomial(self.db_service)
            summary_data["pentanomial"] = penta

        # Add SPRT status if configured (merge config + live status)
        if self._sprt is not None:
            sprt_status = self._sprt.get_status()
            sprt_data = self._build_sprt_payload()
            sprt_data.update(
                {
                    "llr": sprt_status.llr,
                    "lower": sprt_status.lower_bound,
                    "upper": sprt_status.upper_bound,
                    "decision": sprt_status.decision.value,
                    "games": sprt_status.games_played,
                    "wins": sprt_status.wins,
                    "draws": sprt_status.draws,
                    "losses": sprt_status.losses,
                    "elo_estimate": sprt_status.elo_estimate,
                }
            )
            summary_data["sprt"] = sprt_data

        # If exactly 2 engines, enrich with A/B WDL and Elo diff for convenience
        if len(self.config.engines) == 2:
            a_name = str(self.config.engines[0].name)
            b_name = str(self.config.engines[1].name)
            pair_key = (a_name, b_name) if a_name <= b_name else (b_name, a_name)
            pair = results.pair_results.get(pair_key)
            if pair:
                # Normalize names to A/B orientation
                if a_name == pair_key[0]:
                    wins_a = pair.get(f"{pair_key[0]}_wins", 0)
                    wins_b = pair.get(f"{pair_key[1]}_wins", 0)
                else:
                    wins_a = pair.get(f"{pair_key[1]}_wins", 0)
                    wins_b = pair.get(f"{pair_key[0]}_wins", 0)
                draws = pair.get("draws", 0)
                summary_data.update(
                    {
                        "wins_a": wins_a,
                        "wins_b": wins_b,
                        "draws": draws,
                    }
                )

        # Persist and broadcast summary
        if self.api_server:
            schedule_snapshot: JsonObject | None = None
            try:
                schedule_snapshot = await self.get_schedule_snapshot()
            except (RuntimeError, ValueError, OSError) as exc:  # pragma: no cover - defensive
                logger.warning("Failed to generate schedule snapshot for games SSE: %s", exc, exc_info=True)
            else:
                self.api_server.broadcast_games_snapshot(schedule_snapshot)
            self.api_server.broadcast_summary_update(summary_data, source=self._summary_source)

    async def _finalize_tournament(self, results: TournamentResults) -> None:
        """Finalize tournament with results.

        Args:
            results: Final tournament results
        """
        logger.debug("Finalizing tournament")

        try:
            await self._flush_openbench()
        except OpenBenchError as exc:
            if self._openbench_client is not None and self._openbench_client.strict:
                raise
            logger.warning("OpenBench final flush failed; continuing (strict=false): %s", exc)

        # Mark as completed
        self._save_run_state(finished=True)
        rd3 = self.run_dir
        (rd3 / "completed.flag").touch()

        # Final dashboard update
        if self._dashboard_enabled:
            await self._update_dashboard()

        # Write final results
        results_path = rd3 / "tournament_results.json"
        with open(results_path, "w") as f:
            results_data = {
                "leaderboard": results.get_leaderboard(),
                "engine_stats": results.engine_stats,
                "pair_results": {f"{k[0]}_vs_{k[1]}": v for k, v in results.pair_results.items()},
                "total_games": results.total_games,
                "completed_games": results.completed_games_count,
                "timestamp": datetime.now().isoformat(),
            }
            json.dump(results_data, f, indent=2)

        # Log leaderboard
        leaderboard = results.get_leaderboard()
        logger.debug("TOURNAMENT RESULTS")
        logger.debug(f"{'Rank':<6} {'Engine':<20} {'Points':<10} {'W/D/L':<15} {'Win %':<10}")
        for entry in leaderboard:
            wdl = f"{entry['wins']}/{entry['draws']}/{entry['losses']}"
            win_rate = cast(float, entry["win_rate"])
            win_pct = f"{win_rate * 100:.1f}%"
            logger.debug(f"{entry['rank']:<6} {entry['engine']:<20} {entry['points']:<10.1f} {wdl:<15} {win_pct:<10}")

        # Compute and print BTD ratings summary
        games = self.db_service.get_games_with_players() if self.db_service else []

        anchor_name: str | None = None
        if self.config.engines and len(self.config.engines) >= 1:
            anchor_name = str(self.config.engines[0].name)
        engine_names = [str(e.name) for e in self.config.engines]
        btd = BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)

        # Standings by BTD rating
        logger.debug("BTD RATING ESTIMATES (order-independent, with draws + color)")
        logger.debug(f"{'Rank':<6} {'Engine':<20} {'R±95%CI':<18} {'Games':<8} {'Points':<8} {'W/D/L':<15}")

        # Build per-engine games/points from engine_stats
        stats = results.engine_stats
        # Sort engines by BTD rating (fallback to zeros if empty)
        ratings_items = list(btd.ratings.items())
        if not ratings_items:
            ratings_items = [(name, 0.0) for name in engine_names]
        order = sorted(ratings_items, key=lambda x: (-float(x[1]), x[0]))
        for i, (name, r) in enumerate(order, 1):
            se = btd.rating_se.get(name, 0.0) or 0.0
            ci = 1.96 * se
            s = stats.get(name, {"wins": 0, "draws": 0, "losses": 0})
            g = int(s.get("wins", 0)) + int(s.get("draws", 0)) + int(s.get("losses", 0))
            pts = float(s.get("wins", 0)) + 0.5 * float(s.get("draws", 0))
            wdl = f"{s.get('wins', 0)}/{s.get('draws', 0)}/{s.get('losses', 0)}"
            logger.debug(f"{i:<6} {name:<20} {r:>6.1f}±{ci:>6.1f} {g:<8d} {pts:<8.1f} {wdl:<15}")

        # Global parameters
        gci = 1.96 * (btd.gamma_elo_se or 0.0)
        dci = 1.96 * (btd.draw_eq_se or 0.0)
        msg = (
            f"\nBlack advantage: {btd.gamma_elo:.1f}±{gci:.1f} Elo | "
            f"Draw(eq): {btd.draw_eq * 100:.1f}%±{dci * 100:.1f}%"
        )
        logger.debug(msg)

        # Pairwise headline deltas (top 5 by games)
        pairs = []
        for (a, b), pr in results.pair_results.items():
            games_ab = int(pr.get(f"{a}_wins", 0)) + int(pr.get(f"{b}_wins", 0)) + int(pr.get("draws", 0))
            pairs.append(((a, b), games_ab))
        pairs.sort(key=lambda x: -x[1])
        logger.debug("Head-to-Head (ΔR, 95% CI, LOS, W/D/L)")
        for (a, b), _n in pairs[:5]:
            pd = btd.pair_delta(a, b, cov=btd.rating_cov)
            se = pd.standard_error or 0.0
            ci = 1.96 * se
            los = pd.likelihood_of_superiority
            pr = results.pair_results[(a, b)]
            wdl = f"{pr.get(f'{a}_wins', 0)}/{pr.get('draws', 0)}/{pr.get(f'{b}_wins', 0)}"
            hdr = f"{a} vs {b}: ΔR={pd.delta_elo:+.1f}±{ci:.1f} Elo, "
            los_str = ("" if los is None else f"{los * 100:.1f}%").rjust(6)
            logger.debug(hdr + f"LOS={los_str}  W/D/L={wdl}")

        # Prepare engines metadata
        engines_meta: list[JsonObject] = []
        for name in btd.ratings.keys():
            engines_meta.append({"name": name, "elo": float(btd.ratings[name])})

        # Persist machine-readable summary
        summary = {
            "ratings": {
                name: {"elo": float(btd.ratings[name]), "se": float(btd.rating_se.get(name, 0.0))}
                for name in btd.ratings.keys()
            },
            "gamma_elo": btd.gamma_elo,
            "gamma_elo_se": btd.gamma_elo_se,
            "nu": btd.nu,
            "nu_se": btd.nu_se,
            "draw_eq": btd.draw_eq,
            "draw_eq_se": btd.draw_eq_se,
            "pairs": {
                f"{a}_vs_{b}": {
                    "delta_elo": float(pair.delta_elo),
                    "standard_error": float(pair.standard_error or 0.0),
                    "likelihood_of_superiority": float(pair.likelihood_of_superiority or 0.0),
                    "wdl": {
                        a: int(pr.get(f"{a}_wins", 0)),
                        b: int(pr.get(f"{b}_wins", 0)),
                        "draws": int(pr.get("draws", 0)),
                    },
                }
                for (a, b), pr in results.pair_results.items()
                for pair in (btd.pair_delta(a, b, cov=btd.rating_cov),)
            },
        }
        # Add engines_meta to summary
        summary["enginesMeta"] = engines_meta

        rd = self.run_dir
        out = rd / "summary_btd.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

    # --- helpers ---------------------------------------------------------
    def _reorder_and_shuffle(self, games: list[GameSpec]) -> list[GameSpec]:
        """Apply game ordering as configured.

        - auto: 'pair_both' なら pairwise、以外は interleave
        - pairwise: 生成順（ペア連続）。flip_policy='pair_both' 専用
        - interleave: AB1, AC1, BC1, AB2, ...
        - shuffle: deterministic shuffle by tournament.seed
        """
        order = self.config.tournament.game_order
        # Resolve auto
        if order == "auto":
            flip = self.config.rules.initial_positions.flip_policy
            order = "pairwise" if flip == "pair_both" else "interleave"
        shuffle_seed = self.config.tournament.seed

        ordered: list[GameSpec]

        if order == "interleave":
            # Bucket by unordered pair key
            buckets: dict[tuple[str, str], list[GameSpec]] = defaultdict(list)
            for m in games:
                a = m.black_engine
                b = m.white_engine
                key = (a, b) if a <= b else (b, a)
                buckets[key].append(m)

            # Determine deterministic pair order (by pair key)
            pair_keys = sorted(buckets.keys())
            # Interleave one-by-one across pairs
            ordered = []
            max_len = max((len(v) for v in buckets.values()), default=0)
            for idx in range(max_len):
                for key in pair_keys:
                    games = buckets[key]
                    if idx < len(games):
                        ordered.append(games[idx])
        else:
            # Keep original generation order
            ordered = list(games)

        # game_order=shuffle は強制的にシャッフル
        if order == "shuffle":
            rng = random.Random(shuffle_seed)
            rng.shuffle(ordered)

        return ordered
