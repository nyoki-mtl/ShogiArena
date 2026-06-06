"""Game execution helpers for the SPSA orchestrator."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import rshogi
import rshogi.record
from rshogi.core import normalize_usi_position

from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import (
    execute_game as _execute_game_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_item_builders import (
    build_spsa_game_items,
)
from shogiarena._core.contexts.game_session.application.orchestration.completion_emission_service import (
    OrchestratorCompletionEmissionRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.concurrent_executor import numeric_game_id
from shogiarena._core.contexts.game_session.application.orchestration.engine_option_hook_service import (
    SpsaEngineOptionHookPort,
    SpsaEngineOptionHookRequest,
    apply_engine_option_hooks,
)
from shogiarena._core.contexts.game_session.application.orchestration.event_payload_service import (
    build_status_payload,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorExecutionModeRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_service import (
    OrchestratorGameExecutionRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_preflight_service import (
    emit_game_assigned_event,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_setup_service import (
    SpsaGameSetupRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.local_execution_spec_service import (
    LocalExecutionSpecRequest,
)
from shogiarena._core.contexts.game_session.application.progress.orchestrator_progress_control import (
    preassign_worker as _preassign_worker_service,
)
from shogiarena._core.contexts.game_session.ports.session_runner_ports import (
    BeforeGameHookPort,
    BeforeGameHookRequest,
    BeforeGameHookResult,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import (
    make_role_pool_key,
)
from shogiarena._core.contexts.spsa.adapters.runtime.tokens import phase_symbol, variant_token
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, PhaseLiteral, SpsaGamePayload
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

logger = logging.getLogger(__name__)


class SpsaOrchestratorGameplayMixin:
    config: Any
    num_workers: int
    game_to_worker: dict[int, int]
    worker_busy: set[int]
    progress_queue: Any
    instance_pool: Any
    engine_configs: dict[str, Any]
    baseline_config: Path
    tuned_config: Path
    _game_setup_service: Any
    _game_preflight_service: Any
    _event_payload_service: Any
    _engine_option_hook_service: Any
    _game_execution_service: Any
    _completion_emission_service: Any
    extra_options: JsonObject | None

    _make_rng: Any
    _build_engine_option_map: Any
    _make_game_id: Any
    _append_spsa_event: Any
    _run_remote_game: Any
    _emit_game_completion: Any

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
        tuned_option_map: JsonObject | None = None,
        baseline_option_map: JsonObject | None = None,
        event_family: str = "spsa",
        time_control_override: TimeControlLimits | None = None,
    ) -> tuple[float, rshogi.record.GameRecord, rshogi.record.GameRecord]:
        """Play a tuned-vs-baseline pair (tuned black/white) and return mean score."""
        black_reserved = reserved_ids[0] if reserved_ids else None
        white_reserved = reserved_ids[1] if reserved_ids else None
        r_b, gi_b = await self._run_game(
            start_sfen=start_sfen,
            tuned_params=tuned_params,
            current_params=current_params,
            worker_idx=worker_idx,
            is_tuned_as_black=True,
            update_idx=update_idx,
            phase=phase,
            preassigned_game_id=black_reserved,
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            tuned_option_map=tuned_option_map,
            baseline_option_map=baseline_option_map,
            event_family=event_family,
            time_control_override=time_control_override,
        )
        r_w, gi_w = await self._run_game(
            start_sfen=start_sfen,
            tuned_params=tuned_params,
            current_params=current_params,
            worker_idx=worker_idx,
            is_tuned_as_black=False,
            update_idx=update_idx,
            phase=phase,
            preassigned_game_id=white_reserved,
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            tuned_option_map=tuned_option_map,
            baseline_option_map=baseline_option_map,
            event_family=event_family,
            time_control_override=time_control_override,
        )
        score = ((-1.0, +1.0, 0.0)[r_b] + (-1.0, +1.0, 0.0)[r_w]) / 2.0
        return score, gi_b, gi_w

    async def _run_game(
        self,
        *,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        worker_idx: int,
        is_tuned_as_black: bool,
        update_idx: int,
        phase: PhaseLiteral,
        preassigned_game_id: str | None = None,
        tuned_variant_token: str | None = None,
        baseline_variant_token: str | None = None,
        tuned_option_map: JsonObject | None = None,
        baseline_option_map: JsonObject | None = None,
        event_family: str = "spsa",
        time_control_override: TimeControlLimits | None = None,
    ) -> tuple[int, rshogi.record.GameRecord]:
        """Run a single SPSA game with structure aligned to TournamentOrchestrator.

        Returns (winner_code, GameRecord) where winner_code is 1 for tuned win,
        0 for baseline win, 2 for draw.
        """
        if tuned_option_map is None or baseline_option_map is None:
            rng = self._make_rng(update_idx + worker_idx)
            tuned_option_map = self._build_engine_option_map(tuned_params, rng=rng, should_allow_stochastic=True)
            baseline_option_map = self._build_engine_option_map(
                current_params,
                rng=rng,
                should_allow_stochastic=True,
            )

        tuned_token = tuned_variant_token or variant_token(update_idx)
        baseline_token = baseline_variant_token or tuned_token
        tuned_engine_name = str(self.config.tuned[0].name or "tuned")
        baseline_engine_name = str(self.config.baseline[0].name or "baseline")

        def _preassign_worker(numeric_id: int) -> int | None:
            return _preassign_worker_service(
                numeric_game_id=numeric_id,
                num_workers=self.num_workers,
                game_to_worker=self.game_to_worker,
                worker_busy=self.worker_busy,
                logger=logger,
            )

        # Allow forcing EnginePool path even when both roles target the same SSH instance.
        # Set ARENA_SPSA_FORCE_ENGINEPOOL=1 to disable the remote runner optimization.
        should_force_enginepool = str(os.environ.get("ARENA_SPSA_FORCE_ENGINEPOOL", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        setup = self._game_setup_service.resolve(
            request=SpsaGameSetupRequest(
                preassigned_game_id=preassigned_game_id,
                scheduler_worker_idx=worker_idx,
                game_to_worker=self.game_to_worker,
                update_idx=update_idx,
                phase=phase,
                phase_suffix=phase_symbol(phase),
                tuned_token=tuned_token,
                baseline_token=baseline_token,
                is_tuned_as_black=is_tuned_as_black,
                tuned_engine_name=tuned_engine_name,
                baseline_engine_name=baseline_engine_name,
                event_family=event_family,
                instance_pool=self.instance_pool,
                engine_configs=self.engine_configs,
                should_force_enginepool=should_force_enginepool,
            ),
            resolve_game_id=lambda: self._make_game_id(tuned_token, phase),
            to_numeric_game_id=numeric_game_id,
            preassign_worker=_preassign_worker,
        )
        context = setup.context
        assignment = setup.assignment
        dispatch_selection = setup.dispatch_selection
        event_common = setup.event_common
        game_id = assignment.game_id
        # Prepare engine items and per-side time control limits
        black_item, white_item, black_limits, white_limits = self._prepare_game_items(
            is_tuned_as_black=is_tuned_as_black,
            time_control_override=time_control_override,
        )
        emit_game_assigned_event(
            progress_queue=self.progress_queue,
            game_id=game_id,
            initial_sfen=start_sfen,
            black_name=context.black_player_label,
            white_name=context.white_player_label,
            black_limits=black_limits,
            white_limits=white_limits,
        )

        hook_request = SpsaEngineOptionHookRequest(
            tuned_pool_key=make_role_pool_key(tuned_engine_name, "tuned"),
            baseline_pool_key=make_role_pool_key(baseline_engine_name, "baseline"),
            tuned_options=tuned_option_map,
            baseline_options=baseline_option_map,
            tuned_label=context.tuned_label,
            baseline_label=context.baseline_label,
            clear_hash=bool(self.config.variants.apply.is_clear_hash_enabled),
            after_setoption=str(self.config.variants.apply.after_setoption),
        )

        class _SpsaBeforeGameHook:
            async def run(self, request: BeforeGameHookRequest) -> BeforeGameHookResult | None:
                names = await apply_engine_option_hooks(
                    engines_by_key={
                        pool_key: cast(SpsaEngineOptionHookPort, engine)
                        for pool_key, engine in request.engines_by_pool_key.items()
                    },
                    request=hook_request,
                )
                if not names:
                    return None
                return BeforeGameHookResult(display_name_overrides=names)

        _hook: BeforeGameHookPort = _SpsaBeforeGameHook()

        dispatch = dispatch_selection.dispatch
        selected_remote_instance = dispatch_selection.selected_remote_instance
        if preassigned_game_id is None:
            pending_payload = build_status_payload(
                event_common=event_common,
                status="pending",
                start_time=None,
            )
            self._append_spsa_event(pending_payload)

        is_mark_running_called = False

        async def mark_running_once() -> None:
            nonlocal is_mark_running_called
            if is_mark_running_called:
                return
            is_mark_running_called = True
            running_payload = build_status_payload(
                event_common=event_common,
                status="running",
                start_time=datetime.now(tz=UTC).isoformat(),
            )
            self._append_spsa_event(running_payload)

        async def _run_remote_with_instance(remote_instance: Instance) -> rshogi.record.GameRecord:
            await mark_running_once()
            return await self._run_remote_game(
                start_sfen=start_sfen,
                tuned_params=tuned_params,
                current_params=current_params,
                is_tuned_as_black=is_tuned_as_black,
                game_id=game_id,
                black_limits=black_limits,
                white_limits=white_limits,
                remote_instance=remote_instance,
                tuned_label=context.tuned_label,
                baseline_label=context.baseline_label,
                tuned_options=tuned_option_map,
                baseline_options=baseline_option_map,
            )

        gi = await self._game_execution_service.execute(
            request=OrchestratorGameExecutionRequest(
                execution_mode=OrchestratorExecutionModeRequest(
                    should_require_install=False,
                    black_instance_id=dispatch.black_instance_id,
                    white_instance_id=dispatch.white_instance_id,
                    selected_remote_instance=selected_remote_instance,
                ),
                local_execution=LocalExecutionSpecRequest(
                    black_item=black_item,
                    white_item=white_item,
                    initial_sfen=normalize_usi_position(start_sfen),
                    game_id=game_id,
                    black_limits=black_limits,
                    white_limits=white_limits,
                    before_game_hook=_hook,
                    on_game_start=mark_running_once,
                ),
            ),
            game_execution_spec_factory=BaseOrchestrator.GameExecutionSpec,
            run_remote_with_instance=_run_remote_with_instance,
            execute_local=lambda spec: _execute_game_service(self, spec),
        )

        gi.update_metadata(
            {
                "game_type": "spsa",
                "black_player": context.black_player_label,
                "white_player": context.white_player_label,
            }
        )

        # Winner wrt tuned perspective
        winner = self._calculate_winner_code(gi, is_tuned_as_black)

        payload = SpsaGamePayload(
            update_idx=update_idx,
            tuned_params=list(tuned_params),
            current_params=list(current_params),
            is_tuned_as_black=is_tuned_as_black,
            winner_code=winner,
            phase=phase,
            event_family=event_family,
        )
        final_worker_idx = await self._completion_emission_service.emit(
            request=OrchestratorCompletionEmissionRequest(
                game_id=game_id,
                game_info=gi,
                payload=payload,
                worker_resolution=assignment.worker_resolution,
            ),
            emit_game_completion=self._emit_game_completion,
        )
        event_common["worker_idx"] = final_worker_idx
        return winner, gi

    def _prepare_game_items(
        self,
        *,
        is_tuned_as_black: bool,
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
        prepared = build_spsa_game_items(
            is_tuned_as_black=is_tuned_as_black,
            base_spec=base_spec,
            tuned_spec=tuned_spec,
            baseline_name=str(self.config.baseline[0].name or "baseline"),
            tuned_name=str(self.config.tuned[0].name or "tuned"),
            baseline_config_path=self.baseline_config,
            tuned_config_path=self.tuned_config,
            extra_options=self.extra_options,
            base_time_control=self.config.rules.time_control,
            time_control_override=time_control_override,
            engine_game_spec_fn=BaseOrchestrator.EngineGameSpec,
        )
        return (
            prepared.black_item,
            prepared.white_item,
            prepared.black_limits,
            prepared.white_limits,
        )

    @staticmethod
    def _ltc_normalize_result_for_sprt(result: GameResult, is_tuned_as_black: bool) -> GameResult:
        if is_tuned_as_black:
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
    def _calculate_winner_code(game_info: rshogi.record.GameRecord, is_tuned_as_black: bool) -> int:
        result = game_info.result
        if result.is_black_win():
            return 1 if is_tuned_as_black else 0
        if result.is_white_win():
            return 0 if is_tuned_as_black else 1
        return 2
