from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import rshogi.record
import yaml
from rshogi.core import normalize_usi_position

from shogiarena.arena.configs.spsa import LtcRegressionConfig, SpsaRunConfig
from shogiarena.arena.configs.tournament import EngineConfig
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.instances.models import Instance
from shogiarena.arena.session import GameCompletionEvent, GameLifecycleHooks, SessionContext
from shogiarena.arena.tuning.param_io import (
    ParamEntry,
    quantize_value,
    write_params,
)
from shogiarena.utils.types.coerce import coerce_int
from shogiarena.utils.types.types import GameResult

from .base_orchestrator import BaseOrchestrator, DashboardServerProtocol, SummaryUpdateCallback
from .base_orchestrator_utils import (
    build_engine_config_map,
    build_remote_game_info,
    build_time_control_limits,
    build_usi_options,
    enqueue_progress_event,
    make_role_pool_key,
    max_plies_from_rules,
    numeric_game_id,
)
from .spsa.event_log import append_event, record_ltc_result, update_index_json
from .spsa.identifiers import PhaseLiteral, make_game_token, phase_symbol, variant_token
from .spsa.ltc import run_ltc_regression

logger = logging.getLogger(__name__)

__all__ = ["SpsaOrchestrator", "SpsaGamePayload"]


@dataclass(frozen=True)
class SpsaGamePayload:
    """Metadata passed to lifecycle hooks when an SPSA game completes."""

    update_idx: int
    tuned_params: list[ParamEntry]
    current_params: list[ParamEntry]
    tuned_as_black: bool
    winner_code: int
    phase: PhaseLiteral
    event_family: str = "spsa"


class SpsaOrchestrator(BaseOrchestrator):
    def __init__(
        self,
        config: SpsaRunConfig,
        *,
        session: SessionContext,
        hooks: GameLifecycleHooks,
        summary_updater: SummaryUpdateCallback | None = None,
        api_server: DashboardServerProtocol | None = None,
    ) -> None:
        super().__init__(
            api_server=api_server,
            summary_updater=summary_updater,
            session_context=session,
            hooks=hooks,
            resource_poll_interval=config.system.resource_poll_interval,
            resource_poll_max_interval=config.system.resource_poll_max_interval,
        )
        # Align naming with TournamentOrchestrator: expose unified config
        self.config = config
        self.num_workers = session.num_workers
        # Align with Tournament: store provided run_dir without extra validation here
        self.run_dir: Path = session.storage.run_dir
        metadata = session.metadata or {}
        session_uuid = str(metadata.get("session_uuid") or "").strip()
        self._session_uuid: str = session_uuid or session.run_id

        # Unique game id sequencer
        self._gid_seq: int = 0
        # Domain state (injected by runner)
        self._update_items: list[int] = []
        self._params: list[ParamEntry] = []
        self._sfens: list[str] = []
        # Lock for parameter updates/events across concurrent updates
        self._params_lock: asyncio.Lock = asyncio.Lock()

        self._ltc_config: LtcRegressionConfig | None
        if config.ltc_regression is not None and config.ltc_regression.enabled:
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
            start_progress=True,
        )
        # Broadcast completion after DB save to avoid API/DB race

    def _append_spsa_event(self, payload: dict[str, object]) -> None:
        append_event(self.run_dir, self._session_uuid, payload)

    def _record_ltc_result(self, record: dict[str, object]) -> None:
        record_ltc_result(
            run_dir=self.run_dir,
            session_uuid=self._session_uuid,
            results_path=self._ltc_results_path,
            record=record,
        )

    def _reserve_pending_game(
        self,
        *,
        update_idx: int,
        phase: PhaseLiteral,
        tuned_as_black: bool,
        worker_idx: int,
        event_family: str = "spsa",
    ) -> str:
        """Allocate a game id upfront and emit a pending schedule event."""
        vtoken = variant_token(update_idx)
        game_id = self._make_game_id(vtoken, phase)
        tuned_label = self._tuned_player_label(str(self.config.tuned[0].name or "tuned"), vtoken, phase)
        baseline_label = self._baseline_player_label(str(self.config.baseline[0].name or "baseline"), vtoken)
        variant_label = vtoken if phase == "ltc" else vtoken + phase_symbol(phase)
        payload = {
            "event": "game_scheduled",
            "update_idx": int(update_idx),
            "game_id": game_id,
            "variant_token": vtoken,
            "variant_label": variant_label,
            "phase": phase,
            "tuned_as_black": tuned_as_black,
            "black_player": tuned_label if tuned_as_black else baseline_label,
            "white_player": baseline_label if tuned_as_black else tuned_label,
            "status": "pending",
            "assigned_instance": None,
            "worker_idx": worker_idx,
            "start_time": None,
            "family": event_family,
            "ltc": event_family == "ltc",
        }
        self._append_spsa_event(payload)
        return game_id

    def _make_game_id(self, variant_token: str, phase: PhaseLiteral) -> str:
        self._gid_seq = (self._gid_seq + 1) % 10_000_000
        token = make_game_token(self._gid_seq)
        if phase == "ltc":
            return f"{variant_token}-ltc-{token}"
        return f"{variant_token}-{token}"

    def _tuned_player_label(
        self,
        _base_name: str,
        variant_token: str,
        phase: PhaseLiteral,
    ) -> str:
        if phase == "ltc":
            return f"{variant_token}-tuned"
        return f"{variant_token}-{phase}"

    @staticmethod
    def _baseline_player_label(_base_name: str, variant_token: str) -> str:
        return f"{variant_token}-base"

    async def run(self) -> None:
        # Validate that runner injected update items and parameters
        if not self._update_items:
            raise RuntimeError("SpsaOrchestrator requires update items via set_update_items() before run()")
        if not self._params:
            raise RuntimeError("SpsaOrchestrator requires parameters via set_update_items() before run()")
        if not self._sfens:
            raise RuntimeError("SpsaOrchestrator requires SFENs via set_update_items() before run()")

        # Run update items concurrently using the shared scheduler
        await self.run_items_concurrently(
            self._update_items, self._run_one_spsa_update, concurrency_limit=self.num_workers
        )

    async def _run_ltc_regression(
        self,
        *,
        update_idx: int,
        tuned_params: list[ParamEntry],
        baseline_params: list[ParamEntry],
        baseline_update_idx: int | None = None,
    ) -> dict[str, object]:
        """Backwards-compatible hook for tests that stub LTC execution."""

        return await run_ltc_regression(
            self,
            update_idx=update_idx,
            tuned_params=tuned_params,
            baseline_params=baseline_params,
            baseline_update_idx=baseline_update_idx,
        )

    def _prepare_engine_configs(self) -> dict[str, EngineConfig]:
        """Write engine YAMLs and build name-to-spec map for baseline/tuned.

        Returns a dict mapping engine name to the original EngineConfig objects.
        Also stores baseline_config/tuned_config paths as attributes for later
        use and attaches a dynamic 'engine_config' attribute to each spec for
        interface parity with TournamentOrchestrator.
        """
        out_dir = self.run_dir / "spsa"
        out_dir.mkdir(parents=True, exist_ok=True)
        base_spec = self.config.baseline[0]
        tuned_spec = self.config.tuned[0]
        # Merge overlay (max-move sync) + overlays/options like local path does
        base_opts = build_usi_options(getattr(self, "extra_options", None), base_spec) or {}
        tuned_opts = build_usi_options(getattr(self, "extra_options", None), tuned_spec) or {}

        # Write minimal YAMLs using artifact form when available to defer resolution
        def _write(spec: EngineConfig, filename: str, opts: dict[str, object]) -> Path:
            y: dict[str, object] = {"name": spec.name}
            art = spec.artifact
            if art and art.strip():
                y["artifact"] = art
                if spec.build_options:
                    y["build_options"] = dict(spec.build_options)
            else:
                # Extract engine_path from the referenced engine_path YAML
                if spec.engine_path is None:
                    raise ValueError("SPSA engine requires either artifact or engine_path")
                raw = yaml.safe_load(Path(spec.engine_path).read_text(encoding="utf-8")) or {}
                ep = raw.get("engine_path")
                if not isinstance(ep, str) or not ep.strip():
                    raise ValueError(f"engine_path missing in engine config: {spec.engine_path}")
                y["engine_path"] = str(ep)
            if spec.mate_default_ply_limit is not None and spec.mate_default_ply_limit > 0:
                y["mate_default_ply_limit"] = spec.mate_default_ply_limit
            if spec.mate_default_node_limit is not None and spec.mate_default_node_limit > 0:
                y["mate_default_node_limit"] = spec.mate_default_node_limit
            y["mate_default_infinite"] = spec.mate_default_infinite
            y["mate_wait_for_bestmove"] = spec.mate_wait_for_bestmove
            if spec.isready_sync_strategy:
                y["isready_sync_strategy"] = spec.isready_sync_strategy
            if spec.isready_lock_key and spec.isready_lock_key.strip():
                y["isready_lock_key"] = spec.isready_lock_key.strip()
            if spec.isready_lock_template and spec.isready_lock_template.strip():
                y["isready_lock_template"] = spec.isready_lock_template.strip()
            if spec.isready_lock_check_key and spec.isready_lock_check_key.strip():
                y["isready_lock_check_key"] = spec.isready_lock_check_key.strip()
            if spec.isready_lock_check_template and spec.isready_lock_check_template.strip():
                y["isready_lock_check_template"] = spec.isready_lock_check_template.strip()
            if spec.isready_lock_check_templates:
                y["isready_lock_check_templates"] = [str(item) for item in spec.isready_lock_check_templates]
            y["isready_lock_skip_if_exists"] = spec.isready_lock_skip_if_exists
            if spec.handshake_timeout is not None:
                y["handshake_timeout"] = float(spec.handshake_timeout)
            if opts:
                y["options"] = opts
            p = out_dir / filename
            p.write_text(yaml.safe_dump(y, sort_keys=False), encoding="utf-8")
            return p

        self.baseline_config = _write(base_spec, "engine_baseline.yaml", base_opts)
        self.tuned_config = _write(tuned_spec, "engine_tuned.yaml", tuned_opts)

        base_spec.engine_path = self.baseline_config
        tuned_spec.engine_path = self.tuned_config

        entries: list[EngineConfig] = [base_spec, tuned_spec]
        return build_engine_config_map(entries)

    def _compute_variant_offsets(
        self,
        reference_params: list[ParamEntry],
        variant_params: list[ParamEntry],
    ) -> dict[str, float]:
        """Compute quantized parameter offsets for a specific SPSA variant."""
        offsets: dict[str, float] = {}
        snap_float = bool(self.config.snap_float_to_step)
        reference_lookup: dict[str, ParamEntry] = {p.name: p for p in reference_params if not p.not_used}
        for variant_entry in variant_params:
            if variant_entry.not_used:
                continue
            reference_entry = reference_lookup.get(variant_entry.name)
            if reference_entry is None:
                continue
            reference_value = quantize_value(reference_entry, reference_entry.v, snap_float=snap_float)
            variant_value = quantize_value(variant_entry, variant_entry.v, snap_float=snap_float)
            offsets[variant_entry.name] = float(variant_value - reference_value)
        return offsets

    @staticmethod
    def _clone_param_entries(entries: list[ParamEntry]) -> list[ParamEntry]:
        return [
            ParamEntry(
                entry.name,
                entry.type,
                entry.v,
                entry.min,
                entry.max,
                entry.step,
                entry.delta,
                entry.comment,
                entry.not_used,
            )
            for entry in entries
        ]

    def _store_ltc_baseline(self, params: list[ParamEntry], update_idx: int) -> None:
        self._ltc_baseline_snapshot = self._clone_param_entries(params)
        self._ltc_baseline_update_idx = update_idx

    async def _run_game_pair(
        self,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        worker_idx: int,
        *,
        update_idx: int,
        phase: PhaseLiteral,
        reserved_ids: tuple[str, str] | None = None,
        tuned_variant_token: str | None = None,
        baseline_variant_token: str | None = None,
        event_family: str = "spsa",
        time_control_override: TimeControlLimits | None = None,
    ) -> tuple[float, rshogi.record.GameRecord, rshogi.record.GameRecord]:
        """Play a tuned-vs-baseline pair (tuned black/white) and return mean score."""
        black_reserved = reserved_ids[0] if reserved_ids else None
        white_reserved = reserved_ids[1] if reserved_ids else None
        r_b, gi_b = await self._run_single_spsa_game(
            start_sfen=start_sfen,
            tuned_params=tuned_params,
            current_params=current_params,
            worker_idx=worker_idx,
            tuned_as_black=True,
            update_idx=update_idx,
            phase=phase,
            preassigned_game_id=black_reserved,
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            event_family=event_family,
            time_control_override=time_control_override,
        )
        r_w, gi_w = await self._run_single_spsa_game(
            start_sfen=start_sfen,
            tuned_params=tuned_params,
            current_params=current_params,
            worker_idx=worker_idx,
            tuned_as_black=False,
            update_idx=update_idx,
            phase=phase,
            preassigned_game_id=white_reserved,
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            event_family=event_family,
            time_control_override=time_control_override,
        )
        score = ((-1.0, +1.0, 0.0)[r_b] + (-1.0, +1.0, 0.0)[r_w]) / 2.0
        return score, gi_b, gi_w

    async def _run_single_spsa_game(
        self,
        *,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        worker_idx: int,
        tuned_as_black: bool,
        update_idx: int,
        phase: PhaseLiteral,
        preassigned_game_id: str | None = None,
        tuned_variant_token: str | None = None,
        baseline_variant_token: str | None = None,
        event_family: str = "spsa",
        time_control_override: TimeControlLimits | None = None,
    ) -> tuple[int, rshogi.record.GameRecord]:
        """Run a single SPSA game with structure aligned to TournamentOrchestrator.

        Returns (winner_code, GameRecord) where winner_code is 1 for tuned win,
        0 for baseline win, 2 for draw.
        """
        rng = self._make_rng(update_idx + worker_idx)  # Deterministic but different per game
        tuned_option_map = self._build_engine_option_map(tuned_params, rng=rng, allow_stochastic=True)
        baseline_option_map = self._build_engine_option_map(current_params, allow_stochastic=False)

        tuned_token = tuned_variant_token or variant_token(update_idx)
        baseline_token = baseline_variant_token or tuned_token
        tuned_label = self._tuned_player_label(str(self.config.tuned[0].name or "tuned"), tuned_token, phase)
        baseline_label = self._baseline_player_label(str(self.config.baseline[0].name or "baseline"), baseline_token)

        # Build or reuse unique game_id (variant token + digest)
        if preassigned_game_id is not None:
            game_id = preassigned_game_id
        else:
            game_id = self._make_game_id(tuned_token, phase)
        # Pre-assign worker slot for stable UI mapping and prepare items/limits
        num_id = numeric_game_id(game_id)
        preassigned_worker_idx = self.preassign_worker(
            num_id,
            self.num_workers,
            self.game_to_worker,
            self.worker_busy,
        )
        resolved_worker_idx: int | None = preassigned_worker_idx
        if resolved_worker_idx is None:
            mapped_idx = self.game_to_worker.get(num_id)
            if mapped_idx is not None:
                resolved_worker_idx = int(mapped_idx)
            else:
                # Fall back to the logical slot requested by the scheduler; this should not happen
                resolved_worker_idx = int(worker_idx)
        # Prepare engine items and per-side time control limits
        black_item, white_item, black_limits, white_limits = self._prepare_game_items(
            tuned_as_black=tuned_as_black,
            time_control_override=time_control_override,
        )
        progress_q = self.progress_queue
        if progress_q is not None:
            initial_sfen = normalize_usi_position(start_sfen or "startpos")
            black_tc_spec = black_limits.to_spec_str() if black_limits is not None else None
            white_tc_spec = white_limits.to_spec_str() if white_limits is not None else None
            enqueue_progress_event(
                progress_q,
                game_id,
                {
                    "type": "game_assigned",
                    "game_id": game_id,
                    "initial_sfen": initial_sfen,
                    "black_name": tuned_label if tuned_as_black else baseline_label,
                    "white_name": baseline_label if tuned_as_black else tuned_label,
                    "time_control_black": black_tc_spec,
                    "time_control_white": white_tc_spec,
                },
                fallback_move_count=0,
                allow_default_str=True,
            )

        async def _hook(engines_by_key: dict[str, AsyncUsiEngine]) -> dict[AsyncUsiEngine, str] | None:
            tuned_key = make_role_pool_key(str(self.config.tuned[0].name or "tuned"), "tuned")
            base_key = make_role_pool_key(str(self.config.baseline[0].name or "baseline"), "baseline")
            tuned_engine = engines_by_key.get(tuned_key)
            base_engine = engines_by_key.get(base_key)
            if tuned_engine is not None:
                await tuned_engine.apply_engine_options(tuned_option_map)
            names: dict[AsyncUsiEngine, str] = {}
            if tuned_engine is not None:
                names[tuned_engine] = tuned_label
            if base_engine is not None:
                if baseline_option_map:
                    await base_engine.apply_engine_options(baseline_option_map)
                names[base_engine] = baseline_label
            return names

        # If both roles are assigned to the same SSH instance, run remotely for low latency
        same_instance_remote = False
        inst_pool = self.instance_pool
        if inst_pool is not None:
            base_spec = self.config.baseline[0]
            tuned_spec = self.config.tuned[0]
            b_id = base_spec.instance_id
            t_id = tuned_spec.instance_id
            if b_id and t_id and b_id == t_id:
                inst = inst_pool.get_instance(b_id)
                if inst is None:
                    raise ValueError(f"Instance not found: {b_id}")
                if inst.is_ssh:
                    same_instance_remote = True
                    remote_instance = inst
                else:
                    remote_instance = None
            else:
                remote_instance = None
        else:
            remote_instance = None

        variant_label = tuned_token if phase == "ltc" else tuned_token + phase_symbol(phase)
        assigned_instance = remote_instance.name if remote_instance is not None else None
        event_common = {
            "event": "game_scheduled",
            "update_idx": update_idx,
            "game_id": game_id,
            "variant_token": tuned_token,
            "tuned_variant_token": tuned_token,
            "baseline_variant_token": baseline_token,
            "variant_label": variant_label,
            "phase": phase,
            "tuned_as_black": tuned_as_black,
            "black_player": tuned_label if tuned_as_black else baseline_label,
            "white_player": baseline_label if tuned_as_black else tuned_label,
            "assigned_instance": assigned_instance,
            "worker_idx": resolved_worker_idx,
            "family": event_family,
            "ltc": event_family == "ltc",
        }
        if preassigned_game_id is None:
            pending_payload = dict(event_common)
            pending_payload["status"] = "pending"
            pending_payload["start_time"] = None
            self._append_spsa_event(pending_payload)

        mark_running_called = False

        async def mark_running_once() -> None:
            nonlocal mark_running_called
            if mark_running_called:
                return
            mark_running_called = True
            running_payload = dict(event_common)
            running_payload["status"] = "running"
            running_payload["start_time"] = datetime.now(tz=timezone.utc).isoformat()
            self._append_spsa_event(running_payload)

        # Allow forcing EnginePool path even when both roles target the same SSH instance.
        # Set ARENA_SPSA_FORCE_ENGINEPOOL=1 to disable the remote runner optimization.
        force_enginepool = str(os.environ.get("ARENA_SPSA_FORCE_ENGINEPOOL", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if same_instance_remote and remote_instance is not None and not force_enginepool:
            await mark_running_once()
            gi = await self._run_single_spsa_game_remote(
                start_sfen=start_sfen,
                tuned_params=tuned_params,
                current_params=current_params,
                tuned_as_black=tuned_as_black,
                game_id=game_id,
                black_limits=black_limits,
                white_limits=white_limits,
                remote_instance=remote_instance,
                tuned_label=tuned_label,
                baseline_label=baseline_label,
                tuned_options=tuned_option_map,
                baseline_options=baseline_option_map,
            )
        else:
            exec_spec = BaseOrchestrator.GameExecutionSpec(
                black_item=black_item,
                white_item=white_item,
                initial_sfen=normalize_usi_position(start_sfen),
                game_id=game_id,
                black_limits=black_limits,
                white_limits=white_limits,
                before_game_hook=_hook,
                on_game_start=mark_running_once,
            )
            gi = await self._execute_game(exec_spec)

        gi.update_metadata(
            {
                "game_type": "spsa",
                "black_player": tuned_label if tuned_as_black else baseline_label,
                "white_player": baseline_label if tuned_as_black else tuned_label,
            }
        )

        # Winner wrt tuned perspective
        winner = self._calculate_winner_code(gi, tuned_as_black)

        payload = SpsaGamePayload(
            update_idx=update_idx,
            tuned_params=list(tuned_params),
            current_params=list(current_params),
            tuned_as_black=tuned_as_black,
            winner_code=winner,
            phase=phase,
            event_family=event_family,
        )
        final_worker_idx = self.game_to_worker.get(num_id)
        if final_worker_idx is None and resolved_worker_idx is not None:
            final_worker_idx = resolved_worker_idx
        event_common["worker_idx"] = final_worker_idx

        event = GameCompletionEvent(
            game_id=game_id,
            game_info=gi,
            payload=payload,
            worker_idx=int(final_worker_idx) if final_worker_idx is not None else None,
            stop_requested=self._stop_event.is_set(),
        )
        await self._notify_game_complete(event)
        return winner, gi

    async def _run_single_spsa_game_remote(
        self,
        *,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        tuned_as_black: bool,
        game_id: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        remote_instance: Instance,
        tuned_label: str,
        baseline_label: str,
        tuned_options: dict[str, object],
        baseline_options: dict[str, object],
    ) -> rshogi.record.GameRecord:
        """Execute a single SPSA game by delegating both engines to one remote instance."""
        # Prepare variant-id names for display consistency
        black_display = tuned_label if tuned_as_black else baseline_label
        white_display = baseline_label if tuned_as_black else tuned_label
        black_name = black_display
        white_name = white_display

        # Resolve extra options (baseline) and build SPSA overlay for tuned
        # Use the same option merging logic as tournament to include EvalDir and overlays
        base_spec = self.config.baseline[0]
        tuned_spec = self.config.tuned[0]
        base_opts = build_usi_options(getattr(self, "extra_options", None), base_spec) or {}
        tuned_opts = build_usi_options(getattr(self, "extra_options", None), tuned_spec) or {}

        base_opts.update(baseline_options)
        tuned_opts.update(tuned_options)

        # Prepare engine config paths written earlier by _prepare_engine_configs
        b_cfg = self.tuned_config if tuned_as_black else self.baseline_config
        w_cfg = self.baseline_config if tuned_as_black else self.tuned_config

        max_plies = max_plies_from_rules(self.config.rules)
        executor, remote_root, spec = await self._prepare_remote_game_spec(
            remote_instance=remote_instance,
            black_config_path=b_cfg,
            white_config_path=w_cfg,
            black_options=(tuned_opts if tuned_as_black else base_opts),
            white_options=(base_opts if tuned_as_black else tuned_opts),
            start_sfen=start_sfen,
            game_id=game_id,
            black_name=black_name,
            white_name=white_name,
            black_limits=black_limits,
            white_limits=white_limits,
            max_plies=max_plies,
        )

        # Stream events into orchestrator's progress queue and aggregate per-move stats
        progress_q = self.progress_queue
        agg_move_times: list[int | None] = []
        agg_wall_times: list[int | None] = []
        agg_nodes: list[int | None] = []
        agg_depth: list[int | None] = []
        agg_seldepth: list[int | None] = []
        agg_evals: list[int | None] = []
        agg_moves: list[str] = []

        # Track latest ply to assign sensible move_count for non-move events
        last_ply_seen: int = 0

        def on_event(ev: dict[str, object]) -> None:
            nonlocal last_ply_seen
            # Aggregate per-move stats (from move_progress)
            last_ply_seen = self._update_aggregates_from_event(
                ev,
                last_ply_seen,
                agg_moves,
                agg_evals,
                agg_nodes,
                agg_depth,
                agg_seldepth,
                agg_move_times,
                agg_wall_times,
            )
            # Bridge to dashboard progress queue
            enqueue_progress_event(
                progress_q,
                game_id,
                ev,
                fallback_move_count=last_ply_seen,
            )
            # latency metrics removed

        # Info: game start (minimal)
        logger.info("[%s] start game %s: %s vs %s", remote_instance.name, game_id, black_display, white_display)

        events = await executor.run_remote_pair(remote_root=remote_root, spec=spec, on_event=on_event)

        # Extract final result code
        final_result = next(
            (
                e.get("result_code")
                for e in reversed(events)
                if isinstance(e, dict) and e.get("result_code") is not None
            ),
            None,
        )
        if final_result is None:
            raise RuntimeError("Remote game did not produce a result_code in move_progress events")

        # Build GameRecord from final payload.

        gi = build_remote_game_info(
            start_sfen=start_sfen,
            moves=agg_moves,
            result_code=int(final_result),
            game_id=game_id,
            black_name=black_display,
            white_name=white_display,
            black_limits=black_limits,
            white_limits=white_limits,
            move_times=agg_move_times,
            wall_times=agg_wall_times,
            nodes=agg_nodes,
            depth=agg_depth,
            seldepth=agg_seldepth,
            evals=agg_evals,
        )
        # Info: game end (minimal)
        logger.info(
            "[%s] end game %s: result=%s",
            remote_instance.name,
            game_id,
            gi.result.value,
        )
        gi.update_metadata(
            {
                "game_type": "spsa",
                "black_player": black_display,
                "white_player": white_display,
            }
        )
        return gi

    # --- Event aggregation/bridging helpers -------------------------------
    @staticmethod
    def _update_aggregates_from_event(
        ev: dict[str, object],
        last_ply_seen: int,
        agg_moves: list[str],
        agg_evals: list[int | None],
        agg_nodes: list[int | None],
        agg_depth: list[int | None],
        agg_seldepth: list[int | None],
        agg_move_times: list[int | None],
        agg_wall_times: list[int | None],
    ) -> int:
        if ev.get("type") == "move_progress":
            last_ply_val = coerce_int(ev.get("ply")) or 0
            last_ply_seen = max(last_ply_seen, last_ply_val)
            move = ev.get("move")
            if isinstance(move, str) and move.strip():
                agg_moves.append(move)
                agg_evals.append(coerce_int(ev.get("eval_cp")))
                agg_nodes.append(coerce_int(ev.get("nodes")))
                agg_depth.append(coerce_int(ev.get("depth")))
                agg_seldepth.append(coerce_int(ev.get("seldepth")))
                agg_move_times.append(coerce_int(ev.get("time_ms")))
                agg_wall_times.append(coerce_int(ev.get("wall_time_ms")))
        return last_ply_seen

    def _prepare_game_items(
        self,
        *,
        tuned_as_black: bool,
        time_control_override: TimeControlLimits | None = None,
    ) -> tuple[
        BaseOrchestrator.EngineGameSpec,
        BaseOrchestrator.EngineGameSpec,
        TimeControlLimits,
        TimeControlLimits,
    ]:
        """Build engine pool items and per-side time controls for the next game."""
        base_spec = self.config.baseline[0]
        tuned_spec = self.config.tuned[0]
        base_tc = self.config.rules.time_control

        def pick_limits(spec: EngineConfig) -> TimeControlLimits:
            limits = build_time_control_limits(base_tc, spec.time_control)
            if limits is None:
                raise RuntimeError(
                    f"Missing required time_control for SPSA engine '{spec.name}'. "
                    "Add a time_control block to the engine_config."
                )
            return limits

        base_key = make_role_pool_key(str(self.config.baseline[0].name or "baseline"), "baseline")
        tuned_key = make_role_pool_key(str(self.config.tuned[0].name or "tuned"), "tuned")

        if tuned_as_black:
            black_limits = pick_limits(tuned_spec)
            white_limits = pick_limits(base_spec)
            black_item = BaseOrchestrator.EngineGameSpec(
                pool_key=tuned_key, config_path=self.tuned_config, extra_options=getattr(self, "extra_options", None)
            )
            white_item = BaseOrchestrator.EngineGameSpec(
                pool_key=base_key, config_path=self.baseline_config, extra_options=getattr(self, "extra_options", None)
            )
        else:
            black_limits = pick_limits(base_spec)
            white_limits = pick_limits(tuned_spec)
            black_item = BaseOrchestrator.EngineGameSpec(
                pool_key=base_key, config_path=self.baseline_config, extra_options=getattr(self, "extra_options", None)
            )
            white_item = BaseOrchestrator.EngineGameSpec(
                pool_key=tuned_key, config_path=self.tuned_config, extra_options=getattr(self, "extra_options", None)
            )

        if time_control_override is not None:
            black_limits = time_control_override
            white_limits = time_control_override

        return black_item, white_item, black_limits, white_limits

    @staticmethod
    def _ltc_normalize_result_for_sprt(result: GameResult, tuned_as_black: bool) -> GameResult:
        if tuned_as_black:
            if result.is_black_win():
                return GameResult.WHITE_WIN
            if result.is_white_win():
                return GameResult.BLACK_WIN
        else:
            if result.is_black_win():
                return GameResult.BLACK_WIN
            if result.is_white_win():
                return GameResult.WHITE_WIN
        if result.is_draw():
            return GameResult.DRAW_BY_REPETITION
        return GameResult.DRAW_BY_REPETITION

    @staticmethod
    def _calculate_winner_code(game_info: rshogi.record.GameRecord, tuned_as_black: bool) -> int:
        result = game_info.result
        if result.is_black_win():
            return 1 if tuned_as_black else 0
        if result.is_white_win():
            return 0 if tuned_as_black else 1
        return 2

    def _make_rng(self, idx: int) -> random.Random:
        seed_src = f"{self.config.parameters_path}|{idx}"
        h = int(hashlib.sha1(seed_src.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(h)

    def _stochastic_round(self, value: float, rng: random.Random) -> int:
        """Apply stochastic rounding for integers to reduce bias."""
        if self.config.int_rounding == "stochastic":
            floor_val = int(value)
            frac = value - floor_val
            return floor_val + (1 if rng.random() < frac else 0)
        else:
            return int(round(value))

    def _build_engine_option_map(
        self,
        params: list[ParamEntry],
        *,
        rng: random.Random | None = None,
        allow_stochastic: bool = False,
    ) -> dict[str, object]:
        options: dict[str, object] = {}
        for entry in params:
            if entry.not_used:
                continue
            qv = quantize_value(entry, entry.v, snap_float=self.config.snap_float_to_step)
            if entry.type == "int":
                if allow_stochastic and rng is not None:
                    options[entry.name] = self._stochastic_round(qv, rng)
                else:
                    options[entry.name] = int(round(qv))
            else:
                options[entry.name] = qv
        return options

    def _ltc_should_run(self, update_idx: int) -> bool:
        config = self._ltc_config
        if config is None:
            return False
        if update_idx % config.every_n_updates != 0:
            return False
        if self._ltc_last_completed == update_idx:
            return False
        return True

    async def _run_one_spsa_update(self, update_idx: int) -> None:
        # Early stop support
        if self._stop_event.is_set():
            return

        rng = self._make_rng(int(update_idx))
        params = self._params
        sfens = self._sfens

        # Rademacher perturbation (+/-) per parameter
        C = [0.0 if p.not_used else (p.step * (1.0 if rng.randint(0, 1) else -1.0)) for p in params]

        # Gain schedules (k is 1-based)
        k = int(update_idx)
        a0 = float(self.config.a0 or self.config.mobility)
        A = float(self.config.A if self.config.A is not None else max(1.0, 0.1 * float(self.config.num_updates)))
        alpha = float(self.config.alpha)
        gamma = float(self.config.gamma)
        a_k = a0 / ((A + float(k)) ** alpha)
        c0 = float(self.config.scale or 1.0)
        c_k_raw = c0 / (float(k) ** gamma)

        # Apply integer c_k floor if needed
        c_k = c_k_raw
        if any(p.type == "int" and not p.not_used for p in params):
            c_k = max(c_k_raw, self.config.int_ck_floor)

        def add_p(mult: float, C: list[float] = C) -> list[ParamEntry]:
            out: list[ParamEntry] = []
            for p, c in zip(params, C, strict=False):
                v = p.v if p.not_used else (p.v + c * mult)
                out.append(ParamEntry(p.name, p.type, v, p.min, p.max, p.step, p.delta, p.comment, p.not_used))
            return out

        tuned_plus = add_p(c_k)
        tuned_minus = add_p(-c_k)

        perturbations = {
            "plus": self._compute_variant_offsets(params, tuned_plus),
            "minus": self._compute_variant_offsets(params, tuned_minus),
        }

        self._append_spsa_event(
            {
                "event": "update_pending",
                "update_idx": int(update_idx),
                "params": {p.name: p.v for p in params if not p.not_used},
                "timestamp": int(time.time() * 1000),
                "perturbations": perturbations,
                "pending": True,
                "c_k": float(c_k),
                "a_k": float(a_k),
            }
        )

        # Determine batch size for noise reduction
        batch_size = self.config.update_batch_size or 1

        # Mini-batch averaging: run multiple game pairs and average the results
        total_s_plus = 0.0
        total_s_minus = 0.0

        # Control concurrency based on inflight_factor and batch_size
        max_concurrent = min(self.num_workers * self.config.inflight_factor, batch_size * 2)
        semaphore = asyncio.Semaphore(max_concurrent)
        event_family = "spsa"

        async def run_batch_item(batch_idx: int) -> tuple[float, float]:
            worker_slot = int((update_idx * batch_size + batch_idx) % max(1, self.num_workers))
            plus_reserved = (
                self._reserve_pending_game(
                    update_idx=int(update_idx),
                    phase="plus",
                    tuned_as_black=True,
                    worker_idx=worker_slot,
                    event_family=event_family,
                ),
                self._reserve_pending_game(
                    update_idx=int(update_idx),
                    phase="plus",
                    tuned_as_black=False,
                    worker_idx=worker_slot,
                    event_family=event_family,
                ),
            )
            minus_reserved = (
                self._reserve_pending_game(
                    update_idx=int(update_idx),
                    phase="minus",
                    tuned_as_black=True,
                    worker_idx=worker_slot,
                    event_family=event_family,
                ),
                self._reserve_pending_game(
                    update_idx=int(update_idx),
                    phase="minus",
                    tuned_as_black=False,
                    worker_idx=worker_slot,
                    event_family=event_family,
                ),
            )
            async with semaphore:
                # CRN: Use same SFEN for +c/-c if crn_enabled, different SFENs otherwise
                if self.config.crn_enabled:
                    # Common Random Number - same SFEN for both +c and -c (variance reduction)
                    sfen = sfens[rng.randrange(len(sfens))]
                    s_plus, _gi_b1, _gi_w1 = await self._run_game_pair(
                        sfen,
                        tuned_plus,
                        params,
                        worker_idx=worker_slot,
                        update_idx=int(update_idx),
                        phase="plus",
                        reserved_ids=plus_reserved,
                        event_family=event_family,
                    )
                    s_minus, _gi_b2, _gi_w2 = await self._run_game_pair(
                        sfen,
                        tuned_minus,
                        params,
                        worker_idx=worker_slot,
                        update_idx=int(update_idx),
                        phase="minus",
                        reserved_ids=minus_reserved,
                        event_family=event_family,
                    )
                else:
                    # Different SFENs for diversity (original behavior without CRN)
                    sfen_plus = sfens[rng.randrange(len(sfens))]
                    sfen_minus = sfens[rng.randrange(len(sfens))]
                    s_plus, _gi_b1, _gi_w1 = await self._run_game_pair(
                        sfen_plus,
                        tuned_plus,
                        params,
                        worker_idx=worker_slot,
                        update_idx=int(update_idx),
                        phase="plus",
                        reserved_ids=plus_reserved,
                        event_family=event_family,
                    )
                    s_minus, _gi_b2, _gi_w2 = await self._run_game_pair(
                        sfen_minus,
                        tuned_minus,
                        params,
                        worker_idx=worker_slot,
                        update_idx=int(update_idx),
                        phase="minus",
                        reserved_ids=minus_reserved,
                        event_family=event_family,
                    )
                return s_plus, s_minus

        # Run batch items concurrently
        batch_results = await asyncio.gather(*[run_batch_item(i) for i in range(batch_size)])

        # Average the results across the batch
        for s_plus, s_minus in batch_results:
            total_s_plus += s_plus
            total_s_minus += s_minus

        s_plus = total_s_plus / batch_size
        s_minus = total_s_minus / batch_size

        # Compute contributions and apply parameter update atomically
        step = s_plus - s_minus
        step_factor = step / 2.0

        run_ltc_after_update = False
        pre_update_snapshot: list[ParamEntry] | None = None
        post_update_snapshot: list[ParamEntry] | None = None
        baseline_snapshot: list[ParamEntry] | None = None
        baseline_update_idx: int | None = None

        async with self._params_lock:
            pre_update_snapshot = self._clone_param_entries(params)
            if self._ltc_baseline_snapshot is not None:
                baseline_snapshot = self._clone_param_entries(self._ltc_baseline_snapshot)
            baseline_update_idx = self._ltc_baseline_update_idx
            # Mobility factor mirrors BloodgateSPSA's MOBILITY constant (with optional decay via a_k).
            mobility_factor = float(a_k)

            # Gradient estimates are retained for telemetry, but updates follow YaneuraOu's script semantics:
            # Δθ_i = mobility * delta_i * shift_i * (step / 2)
            grads: dict[str, float] = {}
            deltas: dict[str, float] = {}
            delta_norm_sq = 0.0
            for i, p in enumerate(params):
                if p.not_used:
                    continue
                c_i = C[i] if i < len(C) else 0.0
                if c_i != 0.0 and c_k != 0.0:
                    grads[p.name] = float(step / (2.0 * c_k * c_i))
                else:
                    grads[p.name] = 0.0

                delta_theta = mobility_factor * float(p.delta) * step_factor * c_i
                new_v = p.v + delta_theta
                # Quantize and clamp to ensure runtime/persisted consistency
                qv = quantize_value(
                    p,
                    new_v,
                    snap_float=self.config.snap_float_to_step,
                    round_int=False,
                )
                delta_val = float(qv - p.v)
                deltas[p.name] = delta_val
                delta_norm_sq += delta_val * delta_val
                p.v = qv

            # Check early stopping after computing delta_norm
            delta_norm = delta_norm_sq**0.5
            if self.config.early_stop is not None:
                if self.config.early_stop.get("type") == "delta_norm":
                    threshold_raw = self.config.early_stop.get("threshold", 1e-3)
                    threshold = float(threshold_raw) if isinstance(threshold_raw, int | float | str) else 1e-3
                    if delta_norm < threshold:
                        logger.debug(f"Early stopping triggered: delta_norm={delta_norm:.6f} < {threshold}")
                        self._stop_event.set()

            # Persist updated params and append event
            write_params(self.config.parameters_path, params)

            # Log useful SPSA update summary
            changed_params = sum(1 for name, delta_val in deltas.items() if abs(delta_val) > 1e-10)
            from shogiarena.arena.orchestrators.spsa.identifiers import variant_token

            variant_id = variant_token(update_idx)
            top_movers = sorted(grads.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
            top_str = ", ".join(f"{name}({grad:+.3f})" for name, grad in top_movers)
            logger.debug(
                f"SPSA update #{update_idx}: delta_norm={delta_norm:.4f}, step={step:+.3f}, "
                f"changed={changed_params}, vid={variant_id}, top|grad|=[{top_str}]"
            )
            self._append_spsa_event(
                {
                    "event": "update",
                    "update_idx": int(update_idx),
                    "params": {p.name: p.v for p in params if not p.not_used},
                    "s_plus": float(s_plus),
                    "s_minus": float(s_minus),
                    "step": float(step),
                    "gradients": grads,
                    "deltas": deltas,
                    "delta_norm": (delta_norm_sq**0.5),
                    "batch_size": batch_size,
                    "total_games": batch_size * 2,
                    "perturbations": perturbations,
                    "c_k": float(c_k),
                    "a_k": float(a_k),
                }
            )

            # Update index.json for optimized queries
            update_index_json(
                self.run_dir,
                self.config,
                update_idx=update_idx,
                params={p.name: p.v for p in params if not p.not_used},
                s_plus=s_plus,
                s_minus=s_minus,
                step=step,
                gradients=grads,
                deltas=deltas,
                delta_norm=delta_norm,
                batch_size=batch_size,
                total_games=batch_size * 2,
                timestamp=int(time.time() * 1000),
                a_k=a_k,
                c_k=c_k,
                iteration_k=k,
                perturbations=perturbations,
            )
            post_update_snapshot = self._clone_param_entries(params)
            run_ltc_after_update = self._ltc_should_run(update_idx)

        baseline_params_for_ltc = baseline_snapshot
        baseline_idx_for_ltc = baseline_update_idx
        if baseline_params_for_ltc is None and pre_update_snapshot is not None:
            baseline_params_for_ltc = pre_update_snapshot
            baseline_idx_for_ltc = (update_idx - 1) if update_idx > 0 else -1

        if run_ltc_after_update:
            if post_update_snapshot is None or baseline_params_for_ltc is None:
                logger.warning(
                    "Skipping LTC regression for update %s: insufficient parameter snapshots (post=%s, baseline=%s)",
                    update_idx,
                    post_update_snapshot is not None,
                    baseline_params_for_ltc is not None,
                )
            elif not self._stop_event.is_set():
                effective_baseline_idx = baseline_idx_for_ltc if baseline_idx_for_ltc is not None else -1
                ltc_record = await run_ltc_regression(
                    self,
                    update_idx=update_idx,
                    tuned_params=post_update_snapshot,
                    baseline_params=baseline_params_for_ltc,
                    baseline_update_idx=effective_baseline_idx,
                )
                if ltc_record:
                    status = ltc_record.get("status")
                    if status == "passed":
                        async with self._params_lock:
                            self._store_ltc_baseline(post_update_snapshot, update_idx)
                    elif status == "failed" and baseline_params_for_ltc is not None:
                        revert_params_map: dict[str, float] | None = None
                        async with self._params_lock:
                            params.clear()
                            params.extend(self._clone_param_entries(baseline_params_for_ltc))
                            write_params(self.config.parameters_path, params)
                            revert_params_map = {p.name: p.v for p in params if not p.not_used}

                        if revert_params_map is not None:
                            revert_timestamp = int(time.time() * 1000)
                            logger.info(
                                "Reverting update %s parameters to baseline update %s after LTC rejection",
                                update_idx,
                                effective_baseline_idx,
                            )
                            self._append_spsa_event(
                                {
                                    "event": "update",
                                    "update_idx": int(update_idx),
                                    "params": revert_params_map,
                                    "timestamp": revert_timestamp,
                                    "ltc_rejected": True,
                                    "ltc_reverted_to": max(0, int(effective_baseline_idx)),
                                }
                            )
                            normalized_revert_idx = max(0, int(effective_baseline_idx))
                            update_index_json(
                                self.run_dir,
                                self.config,
                                update_idx=update_idx,
                                params=revert_params_map,
                                s_plus=s_plus,
                                s_minus=s_minus,
                                step=step,
                                gradients=grads,
                                deltas=deltas,
                                delta_norm=delta_norm,
                                batch_size=batch_size,
                                total_games=batch_size * 2,
                                timestamp=revert_timestamp,
                                a_k=a_k,
                                c_k=c_k,
                                iteration_k=k,
                                perturbations=perturbations,
                                extra_fields={
                                    "ltc_rejected": True,
                                    "ltc_reverted_to": normalized_revert_idx,
                                },
                            )

    def set_update_items(self, items: list[int], params: list[ParamEntry], sfens: list[str]) -> None:
        """Set update items for SPSA tuning.

        Note: This method modifies the `not_used` flag of ParamEntry objects in the params list.
        The passed params list elements will have their `not_used` flag set to False.
        If you need to preserve the original state, pass a copy of the list.

        Args:
            items: List of update item indices
            params: List of parameter entries (will be modified: not_used=False)
            sfens: List of starting positions in SFEN format
        """
        if not items:
            raise ValueError("update items must be non-empty")
        if not params:
            raise ValueError("params must be non-empty")
        if not sfens:
            raise ValueError("sfens must be non-empty")
        # normalize flags for params - WARNING: modifies input list elements
        active_params: list[ParamEntry] = []
        for p in params:
            if p.not_used:
                continue
            p.not_used = False
            active_params.append(p)
        self._update_items = list(items)
        self._params = active_params
        self._store_ltc_baseline(active_params, -1)
        self._sfens = list(sfens)

    # Progress queue consumption is launched via BaseOrchestrator.start_progress_consumer.
