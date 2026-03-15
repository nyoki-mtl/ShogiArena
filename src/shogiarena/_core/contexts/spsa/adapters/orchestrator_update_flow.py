"""Execution flow for a single SPSA update."""

from __future__ import annotations

import logging
import time
from typing import Any

import rshogi.record

from shogiarena._core.contexts.game_session.application.orchestration.ltc_post_update_service import (
    SpsaLtcPostUpdateRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_batch_execution_service import (
    SpsaPendingReservationRequest,
    SpsaRunGamePairRequest,
    SpsaUpdateBatchExecutionRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_delta_service import (
    SpsaUpdateDeltaRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_recording_service import (
    SpsaUpdateRecordingRequest,
)
from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression import run_ltc_regression
from shogiarena._core.contexts.spsa.adapters.runtime.persistence import update_index_json
from shogiarena._core.contexts.spsa.adapters.runtime.tokens import variant_token
from shogiarena._core.contexts.spsa.application.param_io import quantize_value, write_params
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


async def run_one_spsa_update(orchestrator: Any, update_idx: int) -> None:
    # Early stop support
    if orchestrator._stop_event.is_set():
        return

    rng = orchestrator._make_rng(int(update_idx))
    params = orchestrator._params
    sfens = orchestrator._sfens

    # Rademacher perturbation (+/-) per parameter
    C = [0.0 if p.is_not_used else (p.step * (1.0 if rng.randint(0, 1) else -1.0)) for p in params]

    # Gain schedules (k is 1-based)
    k = int(update_idx)
    a0 = float(orchestrator.config.a0 or orchestrator.config.mobility)
    A = float(
        orchestrator.config.A
        if orchestrator.config.A is not None
        else max(1.0, 0.1 * float(orchestrator.config.num_updates))
    )
    alpha = float(orchestrator.config.alpha)
    gamma = float(orchestrator.config.gamma)
    a_k = a0 / ((A + float(k)) ** alpha)
    c0 = float(orchestrator.config.scale or 1.0)
    c_k_raw = c0 / (float(k) ** gamma)

    # Apply integer c_k floor if needed
    c_k = c_k_raw
    if any(p.type == "int" and not p.is_not_used for p in params):
        c_k = max(c_k_raw, orchestrator.config.int_ck_floor)

    def add_p(mult: float, C: list[float] = C) -> list[ParamEntry]:
        out: list[ParamEntry] = []
        for p, c in zip(params, C, strict=False):
            value = p.value if p.is_not_used else (p.value + c * mult)
            out.append(ParamEntry(p.name, p.type, value, p.min, p.max, p.step, p.delta, p.comment, p.is_not_used))
        return out

    tuned_plus = add_p(c_k)
    tuned_minus = add_p(-c_k)

    perturbations = {
        "plus": orchestrator._compute_variant_offsets(params, tuned_plus),
        "minus": orchestrator._compute_variant_offsets(params, tuned_minus),
    }

    orchestrator._append_spsa_event(
        {
            "event": "update_pending",
            "update_idx": int(update_idx),
            "params": {p.name: p.value for p in params if not p.is_not_used},
            "timestamp": int(time.time() * 1000),
            "perturbations": perturbations,
            "is_pending": True,
            "c_k": float(c_k),
            "a_k": float(a_k),
        }
    )

    # Determine batch size for noise reduction
    batch_size = orchestrator.config.update_batch_size or 1
    event_family = "spsa"

    def _reserve_pending_for_batch(request: SpsaPendingReservationRequest) -> str:
        return orchestrator._reserve_pending_game(
            update_idx=request.update_idx,
            phase=request.phase,
            is_tuned_as_black=request.is_tuned_as_black,
            worker_idx=request.worker_idx,
            event_family=request.event_family,
        )

    async def _run_game_pair_for_batch(
        request: SpsaRunGamePairRequest[ParamEntry],
    ) -> tuple[float, rshogi.record.GameRecord, rshogi.record.GameRecord]:
        return await orchestrator._run_game_pair(
            request.start_sfen,
            list(request.tuned_params),
            list(request.current_params),
            worker_idx=request.worker_idx,
            update_idx=request.update_idx,
            phase=request.phase,
            reserved_ids=request.reserved_ids,
            event_family=request.event_family,
        )

    s_plus, s_minus = await orchestrator._update_batch_execution_service.execute(
        request=SpsaUpdateBatchExecutionRequest(
            update_idx=int(update_idx),
            batch_size=batch_size,
            num_workers=orchestrator.num_workers,
            inflight_factor=orchestrator.config.inflight_factor,
            is_crn_enabled=orchestrator.config.is_crn_enabled,
            sfens=sfens,
            event_family=event_family,
        ),
        rng=rng,
        tuned_plus=tuned_plus,
        tuned_minus=tuned_minus,
        current_params=params,
        reserve_pending_game=_reserve_pending_for_batch,
        run_game_pair=_run_game_pair_for_batch,
    )

    # Compute contributions and apply parameter update atomically
    step = s_plus - s_minus
    step_factor = step / 2.0

    should_run_ltc_after_update = False
    pre_update_snapshot: list[ParamEntry] | None = None
    post_update_snapshot: list[ParamEntry] | None = None
    baseline_snapshot: list[ParamEntry] | None = None
    baseline_update_idx: int | None = None
    grads: dict[str, float] = {}
    deltas: dict[str, float] = {}
    delta_norm = 0.0

    async with orchestrator._params_lock:
        pre_update_snapshot = orchestrator._clone_param_entries(params)
        if orchestrator._ltc_baseline_snapshot is not None:
            baseline_snapshot = orchestrator._clone_param_entries(orchestrator._ltc_baseline_snapshot)
        baseline_update_idx = orchestrator._ltc_baseline_update_idx
        # Mobility factor mirrors BloodgateSPSA's MOBILITY constant (with optional decay via a_k).
        mobility_factor = float(a_k)
        early_stop = orchestrator.config.early_stop
        early_stop_threshold: float | None = None
        if early_stop is not None and early_stop.type == "delta_norm":
            early_stop_threshold = float(early_stop.threshold)

        # Gradient estimates are retained for telemetry, but updates follow YaneuraOu's script semantics:
        # Δθ_i = mobility * delta_i * shift_i * (step / 2)
        update_delta = orchestrator._update_delta_service.apply(
            request=SpsaUpdateDeltaRequest(
                params=params,
                perturbations=C,
                step=step,
                step_factor=step_factor,
                c_k=c_k,
                mobility_factor=mobility_factor,
                early_stop_delta_norm_threshold=early_stop_threshold,
            ),
            quantize_value=lambda entry, new_value: quantize_value(
                entry,
                new_value,
                should_snap_float=orchestrator.config.is_snap_float_to_step,
                should_round_int=False,
            ),
        )
        grads = update_delta.gradients
        deltas = update_delta.deltas
        delta_norm = update_delta.delta_norm
        if update_delta.should_stop and early_stop_threshold is not None:
            logger.debug(f"Early stopping triggered: delta_norm={delta_norm:.6f} < {early_stop_threshold}")
            orchestrator._stop_event.set()

        # Persist updated params and append event
        write_params(orchestrator.config.parameters_path, params)

        # Log useful SPSA update summary
        changed_params = update_delta.changed_params
        variant_id = variant_token(update_idx)
        top_movers = sorted(grads.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
        top_str = ", ".join(f"{name}({grad:+.3f})" for name, grad in top_movers)
        logger.debug(
            f"SPSA update #{update_idx}: delta_norm={delta_norm:.4f}, step={step:+.3f}, "
            f"changed={changed_params}, vid={variant_id}, top|grad|=[{top_str}]"
        )
        params_map = {p.name: p.value for p in params if not p.is_not_used}

        def _persist_update_index(request: SpsaUpdateRecordingRequest, timestamp: int) -> None:
            update_index_json(
                orchestrator.run_dir,
                orchestrator.config,
                update_idx=request.update_idx,
                params=dict(request.params),
                s_plus=request.s_plus,
                s_minus=request.s_minus,
                step=request.step,
                gradients=dict(request.gradients),
                deltas=dict(request.deltas),
                delta_norm=request.delta_norm,
                batch_size=request.batch_size,
                total_games=request.total_games,
                timestamp=timestamp,
                a_k=request.a_k,
                c_k=request.c_k,
                iteration_k=request.iteration_k,
                perturbations=request.perturbations,
            )

        orchestrator._update_recording_service.record(
            request=SpsaUpdateRecordingRequest(
                update_idx=update_idx,
                params=params_map,
                s_plus=s_plus,
                s_minus=s_minus,
                step=step,
                gradients=grads,
                deltas=deltas,
                delta_norm=delta_norm,
                batch_size=batch_size,
                total_games=batch_size * 2,
                perturbations=perturbations,
                c_k=c_k,
                a_k=a_k,
                iteration_k=k,
            ),
            append_spsa_event=orchestrator._append_spsa_event,
            persist_update_index=_persist_update_index,
        )
        post_update_snapshot = orchestrator._clone_param_entries(params)
        should_run_ltc_after_update = orchestrator._ltc_should_run(update_idx)

    def _persist_revert_index(params_map: dict[str, float], timestamp: int, extra_fields: JsonObject) -> None:
        update_index_json(
            orchestrator.run_dir,
            orchestrator.config,
            update_idx=update_idx,
            params=params_map,
            s_plus=s_plus,
            s_minus=s_minus,
            step=step,
            gradients=grads,
            deltas=deltas,
            delta_norm=delta_norm,
            batch_size=batch_size,
            total_games=batch_size * 2,
            timestamp=timestamp,
            a_k=a_k,
            c_k=c_k,
            iteration_k=k,
            perturbations=perturbations,
            extra_fields=extra_fields,
        )

    await orchestrator._ltc_post_update_service.process(
        request=SpsaLtcPostUpdateRequest(
            update_idx=update_idx,
            should_run_ltc_after_update=should_run_ltc_after_update,
            pre_update_snapshot=pre_update_snapshot,
            post_update_snapshot=post_update_snapshot,
            baseline_snapshot=baseline_snapshot,
            baseline_update_idx=baseline_update_idx,
        ),
        orchestrator=orchestrator,
        params=params,
        params_lock=orchestrator._params_lock,
        stop_event=orchestrator._stop_event,
        run_ltc_regression=run_ltc_regression,
        clone_param_entries=orchestrator._clone_param_entries,
        store_ltc_baseline=orchestrator._store_ltc_baseline,
        append_spsa_event=orchestrator._append_spsa_event,
        write_params=lambda updated_params: write_params(orchestrator.config.parameters_path, updated_params),
        persist_revert_index=_persist_revert_index,
    )


__all__ = ["run_one_spsa_update"]
