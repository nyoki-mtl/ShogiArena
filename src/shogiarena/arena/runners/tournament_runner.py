"""N-engine native tournament runner."""

import asyncio
import hashlib
import json
import logging
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, TypedDict, cast

import yaml

from shogiarena.arena.configs.base import AdjudicationSettings
from shogiarena.arena.configs.tournament import ArenaConfig, EngineSpec, GameSpec, TournamentResults
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.orchestrators import base_orchestrator_utils
from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator
from shogiarena.arena.orchestrators.base_orchestrator_utils import build_time_control_limits
from shogiarena.arena.orchestrators.tournament_orchestrator import TournamentOrchestrator
from shogiarena.arena.runners.base_runner import BaseSessionRunner, _jsonify, serialize_rules_config
from shogiarena.arena.runners.reschedule_loop import RescheduleAction, RescheduleDecision, RescheduleLoop
from shogiarena.arena.runners.session_context_builder import SessionContextBuilder
from shogiarena.arena.scheduler.game_scheduler import GameScheduler, GauntletScheduler, create_scheduler
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver
from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.statistics.btd import BTDEstimator
from shogiarena.arena.services.statistics.pentanomial import compute_pentanomial
from shogiarena.arena.services.statistics.rating_service import RatingService
from shogiarena.arena.services.statistics.sprt import Sprt
from shogiarena.arena.session import (
    GameCompletionEvent,
    GameLifecycleHooks,
    LifecycleHooksBase,
    SessionContext,
    SessionStopController,
)
from shogiarena.records import GameInfo
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import maybe_resolve_path_option, resolve_path_like
from shogiarena.utils.types.types import GameResult
from shogiarena.web.dashboard.backend.assets_writer import DashboardProfile

logger = logging.getLogger(__name__)


_RESULT_ABBREVIATIONS: dict[GameResult, str] = {
    GameResult.BLACK_WIN: "B",
    GameResult.WHITE_WIN: "W",
    GameResult.DRAW_BY_REPETITION: "R",
    GameResult.DRAW_BY_MAX_PLIES: "M",
    GameResult.BLACK_WIN_BY_DECLARATION: "BD",
    GameResult.WHITE_WIN_BY_DECLARATION: "WD",
    GameResult.BLACK_WIN_BY_FORFEIT: "B",
    GameResult.WHITE_WIN_BY_FORFEIT: "W",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "BI",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "WI",
    GameResult.BLACK_WIN_BY_TIMEOUT: "BT",
    GameResult.WHITE_WIN_BY_TIMEOUT: "WT",
    GameResult.PAUSED: "P",
    GameResult.ERROR: "ERR",
    GameResult.INVALID: "ERR",
}


_RESULT_REASON_SUFFIXES: dict[GameResult, str] = {
    GameResult.BLACK_WIN_BY_DECLARATION: "by Declaration",
    GameResult.WHITE_WIN_BY_DECLARATION: "by Declaration",
    GameResult.BLACK_WIN_BY_FORFEIT: "by Forfeit",
    GameResult.WHITE_WIN_BY_FORFEIT: "by Forfeit",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "by Illegal Move",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "by Illegal Move",
    GameResult.BLACK_WIN_BY_TIMEOUT: "by Timeout",
    GameResult.WHITE_WIN_BY_TIMEOUT: "by Timeout",
    GameResult.DRAW_BY_REPETITION: "by Repetition",
    GameResult.DRAW_BY_MAX_PLIES: "by Max Moves",
}


class _CompletedGameSummary(TypedDict, total=False):
    result_code: int | None
    result_abbr: str
    result_label: str
    result_detail: str | None
    total_plies: int | None
    start_time: str | None
    end_time: str | None


class TournamentRunner(BaseSessionRunner[TournamentResults, None]):
    dashboard_profiles = ("tournament",)
    """N-engine native tournament runner.

    Orchestrates tournaments with arbitrary number of engines using
    game-based execution, stable IDs, and deterministic resume.
    """

    class _TournamentLifecycle(LifecycleHooksBase):
        def __init__(self, runner: "TournamentRunner", controller: SessionStopController) -> None:
            super().__init__(controller)
            self._runner = runner

        async def on_game_complete(self, event: GameCompletionEvent) -> None:
            payload = event.payload
            if not isinstance(payload, GameSpec):
                raise TypeError("Tournament lifecycle hook expects GameSpec payload")
            await self._runner._handle_game_completion(
                payload,
                event.game_info,
                worker_idx=event.worker_idx,
                stop_requested=event.stop_requested,
            )

    def _build_rules_payload(self) -> dict[str, Any]:
        payload = serialize_rules_config(self.config.rules)
        if isinstance(payload.get("initial_positions"), dict):
            payload.setdefault("flip_policy", payload["initial_positions"].get("flip_policy"))
        return payload

    def _build_sprt_payload(self) -> dict[str, Any]:
        sprt_conf = getattr(self.config, "sprt", None)
        if sprt_conf is None:
            return {}
        return _jsonify(asdict(sprt_conf))

    @staticmethod
    def _resolve_dashboard_profiles_for_config(config: ArenaConfig) -> tuple[DashboardProfile, ...]:
        if getattr(config, "sprt", None) is not None:
            return ("sprt",)
        exp_name = str(config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return ("match",)
        return ("tournament",)

    def _resolve_tournament_type(self) -> str:
        if getattr(self.config, "sprt", None) is not None:
            return "sprt"
        exp_name = str(self.config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return "match"
        return str(self.config.tournament.scheduler)

    def _resolve_summary_source(self) -> str:
        if getattr(self.config, "sprt", None) is not None:
            return "sprt"
        exp_name = str(self.config.experiment_name or "").strip().lower()
        if exp_name == "match":
            return "match"
        return "tournament"

    def __init__(
        self,
        config: ArenaConfig,
        *,
        instance_pool: InstancePool | None = None,
        overwrite: bool = False,
    ):
        """Initialize tournament runner.

        Args:
            config: Complete tournament configuration
        """
        super().__init__(
            instance_pool=instance_pool,
            dashboard_profiles=self._resolve_dashboard_profiles_for_config(config),
        )
        self.config = config
        self._summary_source = self._resolve_summary_source()
        # Create scheduler (inject baseline_count for gauntlet)
        self.scheduler: GameScheduler
        if config.tournament.scheduler == "gauntlet":
            self.scheduler = GauntletScheduler(baseline_count=int(getattr(config.tournament, "baseline_count", 1)))
        else:
            self.scheduler = create_scheduler(config.tournament.scheduler)
        self.game_schedule: list[GameSpec] = []
        self.completed_game_ids: set[str] = set()
        self._completed_game_summaries: dict[str, _CompletedGameSummary] = {}
        self._overwrite = bool(overwrite)

        # Services
        self.db_service: ArenaDBService | None = None
        self.rating_service: RatingService | None = None
        # SPRT state (optional)
        self._sprt: Sprt | None = None
        self._sprt_pair: tuple[str, str] | None = None
        self._sprt_min_games: int = 0
        # Cached metadata/time-control structures for dashboard summaries
        self._engine_metadata_cache: list[dict[str, Any]] | None = None
        self._engine_metadata_runtime_sig: str | None = None
        self._engine_time_controls_cache: tuple[dict[str, str], str | None] | None = None
        self._completion_lock: asyncio.Lock = asyncio.Lock()
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

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return self._TournamentLifecycle(self, controller)

    def build_session_context(self) -> SessionContext:
        rd = self.config.run_dir
        assert rd is not None
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
        return SessionContextBuilder.for_tournament(
            run_dir=rd,
            num_workers=num_workers,
            instance_pool=self.instance_pool,
            run_id=run_id,
            experiment_name=str(self.config.experiment_name or ""),
            scheduler=str(self.config.tournament.scheduler),
            games_per_pair=int(self.config.tournament.games_per_pair),
            num_engines=len(self.config.engines),
            dashboard_enabled=bool(self.config.dashboard.enabled),
            services=services,
        )

    # run() is provided by BaseSessionRunner

    async def _stop_additional_services(self) -> None:
        if self.db_service:
            self.db_service.close()
            self.db_service = None

    async def init_services(self) -> None:
        """Initialize database and rating services."""
        logger.debug("Initializing services")

        # Create run directory
        rd = self.config.run_dir
        assert rd is not None
        rd.mkdir(parents=True, exist_ok=True)

        # Initialize database service
        db_path = rd / "game.db"
        self.db_service = ArenaDBService(db_path)
        self.db_service.ensure_schema_compatibility()

        # Initialize rating service
        self.rating_service = RatingService(
            initial_rating=self.config.rating.initial, k_factor=self.config.rating.k_factor
        )
        # Initialize optional SPRT (requires rating service)
        sprt_conf = getattr(self.config, "sprt", None)
        if sprt_conf is not None:
            self._sprt = Sprt(
                elo0=float(sprt_conf.elo0),
                elo1=float(sprt_conf.elo1),
                alpha=float(sprt_conf.alpha),
                beta=float(sprt_conf.beta),
            )
            self._sprt_min_games = int(getattr(sprt_conf, "min_games", 0) or 0)
            if len(self.config.engines) == 2:
                self._sprt_pair = (str(self.config.engines[0].name), str(self.config.engines[1].name))
            else:
                self._sprt_pair = None
        logger.debug("Services initialized (DB/Rating/SPRT)")

    @property
    def engine_metadata(self) -> list[dict[str, Any]]:
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

    def _get_runtime_snapshots(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
        orchestrator = getattr(self, "_orchestrator", None)
        runtime_options: dict[str, dict[str, Any]] = {}
        runtime_info: dict[str, dict[str, str]] = {}
        if orchestrator is not None:
            get_opts = getattr(orchestrator, "get_engine_option_snapshots", None)
            if callable(get_opts):
                try:
                    runtime_options = get_opts()
                except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                    logger.debug("Failed to fetch runtime USI options from orchestrator: %s", exc, exc_info=True)
            get_info = getattr(orchestrator, "get_engine_info_snapshots", None)
            if callable(get_info):
                try:
                    runtime_info = get_info()
                except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                    logger.debug("Failed to fetch runtime engine info from orchestrator: %s", exc, exc_info=True)
        return runtime_options, runtime_info

    def _collect_engine_metadata(
        self,
        runtime_options: dict[str, dict[str, Any]],
        runtime_info: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        resolver = ArtifactResolver()
        base_extra_options = base_orchestrator_utils.compute_max_ply_extra_options(self.config.rules) or {}
        cfg_source_path = getattr(self.config, "source_path", None)

        def format_source(label: str, candidate: Any | None) -> str:
            if candidate:
                try:
                    path_obj = Path(str(candidate)).expanduser().resolve(strict=False)
                    return f"{label} ({path_obj})"
                except (OSError, RuntimeError, ValueError):
                    return f"{label} ({candidate})"
            return label

        rules_source = format_source("rules.adjudication", cfg_source_path) if base_extra_options else None
        metadata: list[dict[str, Any]] = []
        run_dir = self.config.run_dir or self.config.output_dir
        for engine in self.config.engines:
            meta: dict[str, Any] = {
                "name": str(engine.name),
                "engine_config_path": str(engine.engine_config) if engine.engine_config else None,
            }
            resolved_engine_path: str | None = None
            if engine.engine_config is not None:
                meta.update(self._read_engine_config_metadata(engine, resolver))
                resolved_engine_path = meta.get("engine_path")
            else:
                art = getattr(engine, "artifact", None)
                build_overrides = getattr(engine, "build_options", {}) or {}
                if isinstance(art, str) and art.strip():
                    try:
                        resolved_engine_path = str(resolver.resolve(art, overrides=build_overrides))
                    except (OSError, RuntimeError, ValueError) as exc:
                        logger.debug("Failed to resolve engine binary for %s: %s", engine.name, exc, exc_info=True)
            if resolved_engine_path:
                meta["engine_path"] = resolved_engine_path
            if isinstance(engine.options, dict) and engine.options:
                meta["extra_options"] = dict(engine.options)
            overlay_opts = engine.load_overlay_options()
            if overlay_opts:
                meta["overlay_options"] = overlay_opts
            merged = self._merge_option_dicts(
                meta.get("config_options"),
                meta.get("extra_options"),
                meta.get("overlay_options"),
            )
            source_map: dict[str, str] = {}
            source_details: dict[str, str] = {}
            config_opts = meta.get("config_options")
            if isinstance(config_opts, dict):
                for key in config_opts:
                    source_map[key] = "config"
                    source_details[key] = format_source("engine config", meta.get("engine_config_path"))
            extra_opts = meta.get("extra_options")
            if isinstance(extra_opts, dict):
                for key in extra_opts:
                    source_map[key] = "override"
                    source_details[key] = format_source("engines[].options", cfg_source_path)
            overlay_opt_map = meta.get("overlay_options")
            if isinstance(overlay_opt_map, dict):
                for key in overlay_opt_map:
                    source_map[key] = "overlay"
                    source_details[key] = format_source("options overlays", cfg_source_path)
            if base_extra_options:
                for key, value in base_extra_options.items():
                    merged[key] = value
                    source_map.setdefault(key, "rules")
                    if rules_source:
                        source_details.setdefault(key, rules_source)

            if merged:
                meta["merged_options"] = dict(merged)
                if source_map:
                    meta["option_sources"] = source_map
                if source_details:
                    meta["option_sources_details"] = source_details

                resolved_options: dict[str, Any] = {}
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
                    resolved_options[key] = resolved_value
                meta["resolved_options"] = resolved_options

            runtime_opts = runtime_options.get(meta["name"])
            if runtime_opts:
                try:
                    meta["runtime_usi_options"] = json.loads(json.dumps(runtime_opts, ensure_ascii=False))
                except (TypeError, ValueError):
                    meta["runtime_usi_options"] = dict(runtime_opts)
            runtime_meta = runtime_info.get(meta["name"])
            if runtime_meta:
                meta["runtime_engine_info"] = dict(runtime_meta)
            metadata.append(meta)
        return metadata

    def _read_engine_config_metadata(self, engine: EngineSpec, resolver: ArtifactResolver) -> dict[str, Any]:
        config_path = engine.engine_config
        if config_path is None:
            return {}
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise TypeError(f"Engine config must be a mapping: {config_path}")

        metadata: dict[str, Any] = {}
        engine_path = raw.get("engine_path")
        artifact = raw.get("artifact")
        build_overrides_raw = raw.get("build_options")
        if build_overrides_raw is None:
            build_overrides: dict[str, Any] = {}
        elif isinstance(build_overrides_raw, dict):
            build_overrides = dict(build_overrides_raw)
        else:
            raise TypeError(f"build_options must be a mapping in engine config: {config_path}")

        if isinstance(engine_path, str) and engine_path.strip():
            metadata["engine_path"] = resolve_path_like(
                engine_path,
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
            )
        elif isinstance(artifact, str) and artifact.strip():
            metadata["engine_path"] = str(resolver.resolve(artifact, overrides=build_overrides))
        elif engine_path is not None or artifact is not None:
            raise ValueError(f"Invalid engine config {config_path}: specify a non-empty engine_path or artifact")

        options = raw.get("options")
        if options is not None and not isinstance(options, dict):
            raise TypeError(f"options must be a mapping in engine config: {config_path}")
        if options:
            metadata["config_options"] = options

        return metadata

    @staticmethod
    def _merge_option_dicts(*items: Any) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for entry in items:
            if isinstance(entry, dict) and entry:
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
            inst_raw = getattr(spec, "instance_id", None)
            if isinstance(inst_raw, str):
                normalized = inst_raw.strip()
                if normalized:
                    mapping[name] = normalized
                    continue
            mapping[name] = "local"
        return mapping

    def _compute_engine_time_control_specs(self) -> tuple[dict[str, str], str | None]:
        base_tc = getattr(self.config.rules, "time_control", None)
        engine_map: dict[str, str] = {}
        default_spec: str | None = None

        base_limits = build_time_control_limits(base_tc, None)
        if base_limits is not None:
            default_spec = base_limits.to_spec_str()

        for engine in self.config.engines:
            limits = build_time_control_limits(base_tc, getattr(engine, "time_control", None))
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
        rd = self.config.run_dir
        assert rd is not None
        rd.mkdir(parents=True, exist_ok=True)
        if self._overwrite:
            self._cleanup_existing_run()
        self._write_run_metadata()

    def _write_run_metadata(self) -> None:
        rd = self.config.run_dir
        assert rd is not None

        config_payload: dict[str, Any] | None = None
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

    def _serialize_config(self) -> dict[str, Any]:
        raw = asdict(self.config)
        serialized = self._convert_for_serialization(raw)
        if not isinstance(serialized, dict):
            raise TypeError("Serialized ArenaConfig must be a mapping")
        return cast(dict[str, Any], serialized)

    def _convert_for_serialization(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self._convert_for_serialization(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._convert_for_serialization(v) for v in value]
        if isinstance(value, tuple):
            return [self._convert_for_serialization(v) for v in value]
        if isinstance(value, set):
            converted = [self._convert_for_serialization(v) for v in value]
            return sorted(converted, key=lambda item: repr(item))
        if isinstance(value, Path):
            return str(value)
        return value

    @staticmethod
    def _detect_shogiarena_version() -> str:
        try:
            return importlib_metadata.version("shogiarena")
        except importlib_metadata.PackageNotFoundError:  # pragma: no cover - environment without package
            return "unknown"

    async def prepare_domain(self) -> None:
        await self._setup_tournament()

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:
        if not self.config.dashboard.enabled:
            return None
        rd = self.config.run_dir
        assert rd is not None
        return (
            rd,
            int(self.config.dashboard.api_port),
            int(self.config.tournament.num_parallel),
        )

    async def seed_initial_summary(self) -> None:
        rd = self.config.run_dir
        assert rd is not None

        engine_names = [str(e.name) for e in self.config.engines]
        engines_meta = self.engine_metadata
        engine_tc_map, default_tc_spec = self.engine_time_controls
        engine_instances = self._engine_instance_defaults()
        anchor_name = engine_names[0] if engine_names else None

        zero_stats = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engine_names}

        seed_summary: dict[str, Any] = {
            "tournamentType": self._resolve_tournament_type(),
            "mode": self._summary_source,
            "flipPolicy": getattr(self.config.rules.initial_positions, "flip_policy", None),
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
            seed_summary["flipPolicy"] = (
                seed_summary.get("flipPolicy")
                or rules_payload.get("flip_policy")
                or (rules_payload.get("initial_positions") or {}).get("flip_policy")
            )
            seed_summary["tournamentConfig"] = {"rules": rules_payload}
            sprt_payload = self._build_sprt_payload()
            if sprt_payload:
                seed_summary.setdefault("sprt", sprt_payload)

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
        if isinstance(orchestrator, TournamentOrchestrator):
            orchestrator.set_schedule(self.game_schedule, self.completed_game_ids)

    async def _setup_tournament(self) -> bool:
        """Setup new tournament or resume existing.

        Returns:
            True if resuming, False if new
        """
        # Check for existing run
        rd = self.config.run_dir
        assert rd is not None
        run_state_path = rd / "run_state.json"

        if run_state_path.exists() and not self._overwrite:
            # Try to resume
            return await self._resume_tournament()
        else:
            # Setup new tournament
            await self._setup_new_tournament()
            return False

    async def _setup_new_tournament(self) -> None:
        """Setup a new tournament."""
        logger.debug(f"Setting up new tournament in {self.config.run_dir}")

        # Warn about flip_policy choices for statistical robustness
        flip = getattr(self.config.rules.initial_positions, "flip_policy", "")
        # Enforce: game_order=pairwise requires flip_policy=pair_both
        if self.config.tournament.game_order == "pairwise" and flip != "pair_both":
            raise ValueError("game_order='pairwise' requires initial_positions.flip_policy='pair_both'")
        # Warn if baseline_count specified but scheduler is not gauntlet
        if self.config.tournament.scheduler != "gauntlet":
            if int(getattr(self.config.tournament, "baseline_count", 1)) != 1:
                logger.warning(
                    "tournament.baseline_count is specified but scheduler is not 'gauntlet'; the value will be ignored."
                )

        if flip != "pair_both":
            if getattr(self.config, "sprt", None) is not None:
                logger.warning(
                    f"flip_policy is '{flip}'. For SPRT, flip_policy='pair_both' is recommended for paired samples."
                )
            else:
                logger.info(
                    f"flip_policy is '{flip}'. Pentanomial stats will be based on available pairs only (reference)."
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
        logger.info("Attempting to resume tournament")

        # Load run state
        rd = self.config.run_dir
        assert rd is not None
        run_state_path = rd / "run_state.json"
        with open(run_state_path) as f:
            run_state = json.load(f)

        # Validate schedule hash
        current_hash = self.config.get_schedule_hash()
        saved_hash = run_state.get("schedule_hash")
        if saved_hash != current_hash:
            logger.warning("Configuration changed, cannot resume. Use --overwrite to start fresh.")
            return False

        # Load completed games
        self.completed_game_ids = set(run_state.get("completed_game_ids", []))

        summaries_payload = run_state.get("completed_game_summaries")
        parsed_summaries: dict[str, _CompletedGameSummary] = {}
        if isinstance(summaries_payload, dict):
            for gid, payload in summaries_payload.items():
                if not isinstance(gid, str) or not isinstance(payload, dict):
                    continue
                summary: _CompletedGameSummary = {}
                result_code_raw = payload.get("result_code")
                if isinstance(result_code_raw, int | float) and not isinstance(result_code_raw, bool):
                    summary["result_code"] = int(result_code_raw)
                elif result_code_raw is None:
                    summary["result_code"] = None

                result_abbr_raw = payload.get("result_abbr")
                if isinstance(result_abbr_raw, str):
                    summary["result_abbr"] = result_abbr_raw

                result_label_raw = payload.get("result_label")
                if isinstance(result_label_raw, str):
                    summary["result_label"] = result_label_raw

                result_detail_raw = payload.get("result_detail")
                if isinstance(result_detail_raw, str):
                    summary["result_detail"] = result_detail_raw
                elif result_detail_raw is None:
                    summary["result_detail"] = None

                total_plies_raw = payload.get("total_plies")
                if isinstance(total_plies_raw, int | float) and not isinstance(total_plies_raw, bool):
                    summary["total_plies"] = int(total_plies_raw)

                end_time_raw = payload.get("end_time")
                if isinstance(end_time_raw, str):
                    summary["end_time"] = end_time_raw
                elif end_time_raw is None:
                    summary["end_time"] = None

                parsed_summaries[gid] = summary

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

        display_order_payload = run_state.get("game_display_order")
        if isinstance(display_order_payload, dict):
            restored: dict[str, int] = {}
            for key, value in display_order_payload.items():
                if not isinstance(key, str):
                    continue
                if isinstance(value, int):
                    restored[key] = value
                elif isinstance(value, float) and value.is_integer():
                    restored[key] = int(value)
            if restored:
                self._game_display_order = restored
            else:
                self._reset_display_order()
        else:
            self._reset_display_order()

        # Count remaining games
        remaining = len([m for m in self.game_schedule if m.game_id not in self.completed_game_ids])

        logger.debug(f"Resuming tournament: {len(self.completed_game_ids)} completed, {remaining} remaining games")

        cancelled_ids = set(run_state.get("cancelled_game_ids", []))
        cancelled_specs_payload = run_state.get("cancelled_games", []) or []
        payload_by_id = {entry.get("game_id"): entry for entry in cancelled_specs_payload if isinstance(entry, dict)}
        self._cancelled_specs = {}
        retained_schedule: list[GameSpec] = []
        for spec in self.game_schedule:
            if spec.game_id in cancelled_ids:
                entry = payload_by_id.get(spec.game_id)
                if entry:
                    reconstructed = GameSpec(
                        black_engine=str(entry.get("black", spec.black_engine)),
                        white_engine=str(entry.get("white", spec.white_engine)),
                        initial_sfen=str(entry.get("sfen", spec.initial_sfen)),
                        game_id=str(spec.game_id),
                        round_num=int(entry.get("round", spec.round_num) or 0),
                    )
                    assignment_payload = entry.get("assignment")
                    if assignment_payload is None and "assigned_instance" in entry:
                        assignment_payload = entry.get("assigned_instance")
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
        original_total = run_state.get("original_total_games")
        if isinstance(original_total, int) and original_total > 0:
            self.original_total_games = original_total
        else:
            self.original_total_games = len(self.game_schedule) + len(self.cancelled_game_ids)

        overrides = run_state.get("game_instance_overrides")
        if isinstance(overrides, dict):
            for spec in self.game_schedule:
                self._apply_assignment_override(spec, overrides.get(spec.game_id))
            for spec in self._cancelled_specs.values():
                self._apply_assignment_override(spec, overrides.get(spec.game_id))

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
        logger.info("Tournament drained; waiting for new schedule before resuming")
        self._session_phase = "waiting"
        await self._schedule_wait_event.wait()
        self._schedule_wait_event.clear()
        self._session_phase = "running"

    @staticmethod
    def _normalize_instance_id(value: str | None) -> str | None:
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

        b_id = self._normalize_instance_id(getattr(b_spec, "instance_id", None)) or "local"
        w_id = self._normalize_instance_id(getattr(w_spec, "instance_id", None)) or "local"
        return (b_id, w_id)

    def _resolve_assignment_for_spec(self, spec: GameSpec) -> dict[str, str | None]:
        default_black, default_white = self._infer_game_assignment(spec)
        override_black = self._normalize_instance_id(getattr(spec, "assigned_instance_black", None))
        override_white = self._normalize_instance_id(getattr(spec, "assigned_instance_white", None))

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
        black = TournamentRunner._normalize_instance_id(getattr(spec, "assigned_instance_black", None))
        white = TournamentRunner._normalize_instance_id(getattr(spec, "assigned_instance_white", None))
        if black and white and black == white:
            return black
        if black and not white:
            return black
        if white and not black:
            return white
        if black or white:
            return "split"
        return None

    def _serialize_assignment_override(self, spec: GameSpec) -> dict[str, Any] | None:
        black = self._normalize_instance_id(getattr(spec, "assigned_instance_black", None))
        white = self._normalize_instance_id(getattr(spec, "assigned_instance_white", None))
        require_install = bool(getattr(spec, "require_install", False))
        if black is None and white is None and not require_install:
            return None
        payload: dict[str, Any] = {}
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

    def _apply_assignment_override(self, spec: GameSpec, payload: Any) -> None:
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
        if not isinstance(payload, dict):
            return

        shared = self._normalize_instance_id(payload.get("shared"))
        black = self._normalize_instance_id(payload.get("black"))
        white = self._normalize_instance_id(payload.get("white"))
        mode = str(payload.get("mode") or "").strip().lower()

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

        if bool(payload.get("require_install")):
            spec.require_install = True

    @staticmethod
    def _assignment_mode(spec: GameSpec) -> str:
        black = TournamentRunner._normalize_instance_id(getattr(spec, "assigned_instance_black", None))
        white = TournamentRunner._normalize_instance_id(getattr(spec, "assigned_instance_white", None))
        if black is None and white is None:
            return "auto"
        if black == white:
            return "shared"
        return "per_color"
        return "auto"

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

        rd = self.config.run_dir
        if rd is None:
            return
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
            return entry.get("combined") if isinstance(entry, dict) else None

        def _resolved_assignment(game_id: str) -> tuple[str | None, str | None]:
            entry = self._game_assignments.get(game_id)
            if isinstance(entry, dict):
                return entry.get("black"), entry.get("white")
            return (None, None)

        payload: list[dict[str, Any]] = []
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
                    "require_install": bool(getattr(spec, "require_install", False)),
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
                        "require_install": bool(getattr(spec, "require_install", False)),
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

        if self.config.dashboard.enabled:
            await self._update_dashboard()

        self._notify_schedule_available()
        return True

    def _save_run_state(self, finished: bool = False) -> None:
        """Save current run state for resume.

        Args:
            finished: Whether tournament is complete
        """
        adj_settings = self.config.rules.adjudication
        if not isinstance(adj_settings, AdjudicationSettings):
            adj_settings = AdjudicationSettings(**adj_settings)

        summaries_for_state: dict[str, dict[str, Any]] = {}
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

        config_payload: dict[str, Any] = {
            "experiment_name": self.config.experiment_name,
            "engines": [e.name for e in self.config.engines],
            "tournament": {
                "scheduler": self.config.tournament.scheduler,
                "games_per_pair": self.config.tournament.games_per_pair,
                "seed": self.config.tournament.seed,
            },
            "rules": {
                "adjudication": {
                    "enable_max_plies": adj_settings.enable_max_plies,
                    "max_plies": adj_settings.max_plies,
                    "enable_resign": adj_settings.enable_resign,
                },
            },
        }
        sprt_conf = getattr(self.config, "sprt", None)
        if sprt_conf is not None:
            config_payload["sprt"] = _jsonify(asdict(sprt_conf))

        run_state: dict[str, Any] = {
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

        cancelled_entries: list[dict[str, Any]] = []
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
            if bool(getattr(spec, "require_install", False)):
                entry["require_install"] = True
            cancelled_entries.append(entry)
        run_state["cancelled_games"] = cancelled_entries

        if summaries_for_state:
            run_state["completed_game_summaries"] = summaries_for_state

        overrides: dict[str, Any] = {}
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

        rd = self.config.run_dir
        assert rd is not None
        run_state_path = rd / "run_state.json"
        with open(run_state_path, "w") as f:
            json.dump(run_state, f, indent=2)

    def _cleanup_existing_run(self) -> None:
        """Remove existing run artifacts."""
        logger.info("Cleaning up existing run")
        rd = self.config.run_dir
        assert rd is not None
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

        return orchestrator

    async def _handle_game_completion(
        self,
        game_spec: GameSpec,
        game_info: GameInfo,
        *,
        worker_idx: int | None,
        stop_requested: bool,
    ) -> None:
        """Handle post-processing for a completed game."""

        update_dashboard = False
        async with self._completion_lock:
            self._engine_metadata_cache = None
            game_info.black_player_name = game_spec.black_engine
            game_info.white_player_name = game_spec.white_engine
            summary = self._summarize_game_completion(game_info)

            db = self.db_service
            should_persist = game_info.game_result != GameResult.PAUSED
            if should_persist and stop_requested and game_info.game_result in {GameResult.PAUSED, GameResult.ERROR}:
                should_persist = False
            if should_persist and db is not None:
                db.append_game_info_list([game_info])
                participation = getattr(game_info, "_arena_participation", None)
                if participation:
                    game_id = db.get_game_id_by_name(str(game_info.game_name))
                    if game_id is not None:
                        db.record_game_participation(game_id=game_id, participation=participation)

            if self.rating_service and game_info.game_result is not None:
                self.rating_service.update_ratings(
                    game_spec.black_engine,
                    game_spec.white_engine,
                    game_info.game_result,
                )

            self.completed_game_ids.add(game_spec.game_id)
            self._completed_game_summaries[game_spec.game_id] = summary
            self._save_run_state()

            self._update_sprt_state(game_spec, game_info)
            if self._sprt and self._sprt.is_finished():
                if self._sprt.games_played >= self._sprt_min_games:
                    self.stop_controller.request_stop(reason="sprt-finished")
            update_dashboard = self.config.dashboard.enabled

        if update_dashboard:
            await self._update_dashboard()

    @staticmethod
    def _result_detail_from_result(result: GameResult) -> str | None:
        detail_map = {
            GameResult.BLACK_WIN_BY_DECLARATION: "declaration",
            GameResult.WHITE_WIN_BY_DECLARATION: "declaration",
            GameResult.BLACK_WIN_BY_FORFEIT: "forfeit",
            GameResult.WHITE_WIN_BY_FORFEIT: "forfeit",
            GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "illegal move",
            GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "illegal move",
            GameResult.BLACK_WIN_BY_TIMEOUT: "timeout",
            GameResult.WHITE_WIN_BY_TIMEOUT: "timeout",
            GameResult.DRAW_BY_REPETITION: "repetition",
            GameResult.DRAW_BY_MAX_PLIES: "max plies",
            GameResult.ERROR: "error",
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
        elif result == GameResult.ERROR:
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
    def _classify_game_result(cls, result: GameResult | None) -> tuple[str, str, str | None]:
        if result is None:
            return "", "", None
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

    def _summarize_game_completion(self, game_info: GameInfo) -> _CompletedGameSummary:
        result_raw = getattr(game_info, "game_result", None)
        result_obj: GameResult | None
        if isinstance(result_raw, GameResult):
            result_obj = result_raw
        else:
            result_obj = None
            if result_raw is not None:
                try:
                    result_obj = GameResult(int(result_raw))
                except (TypeError, ValueError):
                    result_obj = None

        result_code = int(result_obj) if isinstance(result_obj, GameResult) else None
        result_abbr, result_label, result_detail = self._classify_game_result(result_obj)

        total_plies = int(getattr(game_info, "num_moves", 0) or 0)
        start_date = getattr(game_info, "start_date", None)
        end_date = getattr(game_info, "end_date", None)
        start_time = start_date.isoformat() if isinstance(start_date, datetime) else None
        end_time = end_date.isoformat() if isinstance(end_date, datetime) else None

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

    def _update_sprt_state(self, game_spec: GameSpec, game_info: GameInfo) -> None:
        if self._sprt is None or self._sprt_pair is None:
            return
        gr = getattr(game_info, "game_result", None)
        if not isinstance(gr, GameResult):
            return

        a, b = self._sprt_pair
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

    async def get_schedule_snapshot(self) -> dict[str, Any]:
        """Return a schedule summary with status for each game."""

        async with self._reschedule_lock:
            schedule_copy = list(self.game_schedule)
            completed_ids = set(self.completed_game_ids)
            pending_reschedule = self._pending_reschedule is not None
            seed = str(self.config.tournament.seed)

        engine_instance_defaults: dict[str, str | None] = {}
        for spec in self.config.engines:
            name = str(spec.name)
            inst_raw = getattr(spec, "instance_id", None)
            if isinstance(inst_raw, str) and inst_raw.strip():
                engine_instance_defaults[name] = inst_raw.strip()
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
                    roles = getattr(active, "roles", []) or []
                    if roles:
                        side_map = active_side_instances.setdefault(gid, {})
                        for role in roles:
                            if role and getattr(role, "role", None) in {"black", "white"}:
                                side_map[str(role.role)] = inst_name
                    started_raw = getattr(active, "started_at", None)
                    if started_raw is not None:
                        if gid not in active_start_times:
                            active_start_times[gid] = started_raw
                        else:
                            existing = active_start_times[gid]
                            if isinstance(existing, int | float) and isinstance(started_raw, int | float):
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

        items: list[dict[str, Any]] = []
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
                "require_install": bool(getattr(game_spec, "require_install", False)),
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
                if isinstance(summary_start, str) and summary_start:
                    start_time_value = summary_start
            if start_time_value is None:
                raw_started = active_start_times.get(gid)
                if isinstance(raw_started, int | float):
                    start_time_value = datetime.fromtimestamp(raw_started, tz=timezone.utc).isoformat()
                elif isinstance(raw_started, datetime):
                    start_time_value = raw_started.astimezone(timezone.utc).isoformat()
                elif isinstance(raw_started, str) and raw_started:
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
                    "require_install": bool(getattr(cancelled_spec, "require_install", False)),
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

    async def request_reschedule(self, *, seed: str | None = None) -> dict[str, Any]:
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

    async def cancel_pending_games(self) -> dict[str, Any]:
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

        if self.config.dashboard.enabled:
            await self._update_dashboard()

        return {
            "status": "cancelled",
            "cancelled_games": cancelled_ids,
            "cancelled_count": len(cancelled_ids),
            "running_games": list(active_ids),
            "total_games": self.original_total_games,
        }

    async def cancel_game(self, game_id: str) -> dict[str, Any]:
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

        if self.config.dashboard.enabled:
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

    async def restore_game(self, game_id: str) -> dict[str, Any]:
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

            orchestrator = self._orchestrator
            if isinstance(orchestrator, TournamentOrchestrator):
                await orchestrator.enqueue_restored_game(restored_spec, effective_order)

            self._refresh_game_assignments()
            self._write_schedule_file(self.game_schedule)
            self._save_run_state()
            self._notify_schedule_available()

        if self.config.dashboard.enabled:
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
    ) -> dict[str, Any]:
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

        if self.config.dashboard.enabled:
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

    async def run(self) -> TournamentResults | None:
        """Run tournament orchestration with support for dashboard rescheduling."""

        await self.prepare_run_dir()
        await self.prepare_domain()
        await self.init_services()

        dash = self.get_dashboard_params()
        if dash is not None:
            run_dir, port, num_workers = dash
            await self.start_dashboard_server(run_dir, port, num_workers)
            await self.seed_initial_summary()

        session_context = self.build_session_context()
        if session_context is not None:
            self._session_context = session_context

        controller = self._stop_controller

        try:
            loop = RescheduleLoop()

            async def run_iteration(active_controller: SessionStopController) -> None:
                if not self._has_pending_games():
                    return
                self._session_phase = "running"
                hooks = self.create_lifecycle_hooks(active_controller)
                self._lifecycle_hooks = hooks
                orchestrator = await self.create_orchestrator(hooks, session_context)
                await self.run_pre_orchestration_hooks(orchestrator)
                await BaseSessionRunner.run_orchestrator(self, orchestrator, orchestrator.run())
                self._session_phase = "draining"

            async def decide_next(active_controller: SessionStopController) -> RescheduleDecision:
                if getattr(self, "_services_closed", False):
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
                    logger.info("All scheduled games completed; stopping tournament runner")
                    self._stop_when_idle = True
                    return RescheduleDecision(action=RescheduleAction.STOP)

                self._session_phase = "waiting"
                return RescheduleDecision(action=RescheduleAction.WAIT, reset_controller=True)

            async def on_wait() -> None:
                await self._wait_for_new_schedule()

            def on_reset(new_controller: SessionStopController) -> None:
                self._stop_controller = new_controller

            await loop.run(
                controller=controller,
                run_iteration=run_iteration,
                decide_next=decide_next,
                on_wait=on_wait,
                on_reset=on_reset,
            )

        except (asyncio.CancelledError, KeyboardInterrupt):
            # Honour Ctrl+C while paused by making sure services stop exactly once.
            controller.request_stop(reason="cancelled")
            self._session_phase = "stopping"
            await self.stop_services()
            self._session_phase = "finished"
            return None

        if self.services_closed():
            # Ctrl+C handled inside run_orchestrator closes services; skip result computation.
            self._session_phase = "finished"
            return None

        self._session_phase = "stopping"
        final = await self._calculate_results()
        await self._finalize_tournament(final)
        await self.stop_services()
        self._session_phase = "finished"
        return final

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
        engine_stats: dict[str, dict[str, int]] = {}
        for engine in self.config.engines:
            engine_stats[str(engine.name)] = {
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 0,
            }

        # Calculate per-pair results
        pair_results = {}

        # Process each game
        for game in games:
            black = game["black_player"]
            white = game["white_player"]
            result = game["result"]

            if not isinstance(result, GameResult):
                raise TypeError("Database returned a non-GameResult value for game result")

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

        summary_data: dict[str, Any] = {
            "tournamentType": self._resolve_tournament_type(),
            "mode": self._summary_source,
            "flipPolicy": getattr(self.config.rules.initial_positions, "flip_policy", None),
            "numEngines": len(engine_names),
            "runDir": str(self.config.run_dir) if self.config.run_dir is not None else None,
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
            summary_data["flipPolicy"] = (
                summary_data.get("flipPolicy")
                or rules_payload.get("flip_policy")
                or (rules_payload.get("initial_positions") or {}).get("flip_policy")
            )
            summary_data["tournamentConfig"] = {"rules": rules_payload}
            sprt_payload = self._build_sprt_payload()
            if sprt_payload and "sprt" not in summary_data:
                summary_data["sprt"] = sprt_payload

        # Add BTD rating estimates for standings (order-independent ratings)
        games = self.db_service.get_games_with_players() if self.db_service else []

        # Choose anchor consistently: prefer first engine in config (gauntlet baseline)
        anchor_name = engine_names[0] if engine_names else None

        # Provide full engine list so anchor badge appears before first game
        btd = BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)
        # Build nested covariance: {i: {j: cov}}
        cov_map: dict[str, dict[str, float]] = {}
        rc = getattr(btd, "rating_cov", None)
        if isinstance(rc, dict):
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
        rd2 = self.config.run_dir
        assert rd2 is not None
        if self.api_server:
            schedule_snapshot: dict[str, Any] | None = None
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

        # Mark as completed
        self._save_run_state(finished=True)
        rd3 = self.config.run_dir
        assert rd3 is not None
        (rd3 / "completed.flag").touch()

        # Final dashboard update
        if self.config.dashboard.enabled:
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
        logger.info("TOURNAMENT RESULTS")
        logger.info(f"{'Rank':<6} {'Engine':<20} {'Points':<10} {'W/D/L':<15} {'Win %':<10}")
        for entry in leaderboard:
            wdl = f"{entry['wins']}/{entry['draws']}/{entry['losses']}"
            win_pct = f"{entry['win_rate'] * 100:.1f}%"
            logger.info(f"{entry['rank']:<6} {entry['engine']:<20} {entry['points']:<10.1f} {wdl:<15} {win_pct:<10}")

        # Compute and print BTD ratings summary
        games = self.db_service.get_games_with_players() if self.db_service else []

        anchor_name: str | None = None
        if self.config.engines and len(self.config.engines) >= 1:
            anchor_name = str(self.config.engines[0].name)
        engine_names = [str(e.name) for e in self.config.engines]
        btd = BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)

        # Standings by BTD rating
        logger.info("BTD RATING ESTIMATES (order-independent, with draws + color)")
        logger.info(f"{'Rank':<6} {'Engine':<20} {'R±95%CI':<18} {'Games':<8} {'Points':<8} {'W/D/L':<15}")

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
            logger.info(f"{i:<6} {name:<20} {r:>6.1f}±{ci:>6.1f} {g:<8d} {pts:<8.1f} {wdl:<15}")

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
        engines_meta: list[dict[str, Any]] = []
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

        rd = self.config.run_dir
        assert rd is not None
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
            flip = getattr(self.config.rules.initial_positions, "flip_policy", "")
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
