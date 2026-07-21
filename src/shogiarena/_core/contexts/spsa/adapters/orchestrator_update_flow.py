"""Execution flow for a single SPSA update."""

from __future__ import annotations

import logging
import time
from typing import Any

import rsshogi.record

from shogiarena._core.contexts.game_session.application.orchestration.ltc_post_update_service import (
    SpsaLtcPostUpdateRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_batch_execution_service import (
    SpsaPendingReservationRequest,
    SpsaRunGamePairRequest,
    SpsaUpdateBatchExecutionRequest,
)
from shogiarena._core.contexts.game_session.application.orchestration.update_recording_service import (
    SpsaUpdateRecordingRequest,
)
from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression import run_ltc_regression
from shogiarena._core.contexts.spsa.adapters.runtime.persistence import update_index_json
from shogiarena._core.contexts.spsa.adapters.runtime.tokens import variant_token
from shogiarena._core.contexts.spsa.application.classic_schedule import compute_classic_schedule_point
from shogiarena._core.contexts.spsa.application.param_io import quantize_value
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


async def run_one_spsa_update(orchestrator: Any, update_idx: int) -> None:
    # Early stop support
    if orchestrator._stop_event.is_set():
        return

    rng = orchestrator._make_rng(int(update_idx))
    params = orchestrator._params
    sfens = orchestrator._sfens

    total_updates = int(orchestrator.config.num_updates)
    pairs_per_update = int(orchestrator.config.pairs_per_update)
    schedule = compute_classic_schedule_point(
        params=params,
        num_updates=total_updates,
        pairs_per_update=pairs_per_update,
        update_idx=int(update_idx),
        alpha=float(orchestrator.config.alpha),
        gamma=float(orchestrator.config.gamma),
        a_mode=str(orchestrator.config.algorithm.A.mode),
        a_value=float(orchestrator.config.algorithm.A.value),
        int_ck_floor=float(orchestrator.config.int_ck_floor),
    )
    k_pair = schedule.k_pair
    pair_index_start = schedule.pair_index_start
    pair_index_end = schedule.pair_index_end

    flips = [0 if p.is_not_used else (1 if rng.randint(0, 1) else -1) for p in params]
    c_values = [float(schedule.c.get(p.name, 0.0)) for p in params]
    r_values = [float(schedule.r.get(p.name, 0.0)) for p in params]

    def add_p(mult: float) -> list[ParamEntry]:
        out: list[ParamEntry] = []
        for p, flip, c_i in zip(params, flips, c_values, strict=False):
            value = p.value if p.is_not_used else max(float(p.min), min(float(p.max), p.value + mult * flip * c_i))
            out.append(
                ParamEntry(
                    p.name,
                    p.type,
                    value,
                    p.min,
                    p.max,
                    p.step,
                    p.delta,
                    p.comment,
                    p.is_not_used,
                    p.option_name,
                    p.value_encoding,
                    p.scale,
                    p.significant_digits,
                )
            )
        return out

    tuned_plus = add_p(1.0)
    tuned_minus = add_p(-1.0)

    perturbations = {
        "plus": orchestrator._compute_variant_offsets(params, tuned_plus),
        "minus": orchestrator._compute_variant_offsets(params, tuned_minus),
    }
    plus_options, minus_options = orchestrator._build_engine_option_maps_for_pair(tuned_plus, tuned_minus, rng=rng)
    _append_variant_artifact(
        orchestrator.run_dir,
        update_idx=update_idx,
        pair_index_start=pair_index_start,
        pair_index_end=pair_index_end,
        k_pair=k_pair,
        plus=tuned_plus,
        minus=tuned_minus,
        flips={p.name: flips[idx] for idx, p in enumerate(params) if not p.is_not_used},
        plus_options=plus_options,
        minus_options=minus_options,
    )

    orchestrator._append_spsa_event(
        {
            "event": "update_pending",
            "update_idx": int(update_idx),
            "params": {p.name: p.value for p in params if not p.is_not_used},
            "timestamp": int(time.time() * 1000),
            "perturbations": perturbations,
            "is_pending": True,
            "c_k": float(max(c_values) if c_values else 0.0),
            "a_k": float(max(r_values) if r_values else 0.0),
            "k_pair": int(k_pair),
            "pair_index_start": int(pair_index_start),
            "pair_index_end": int(pair_index_end),
        }
    )

    # Determine batch size for noise reduction
    batch_size = pairs_per_update
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
    ) -> tuple[float, rsshogi.record.Record, rsshogi.record.Record]:
        return await orchestrator._run_game_pair(
            request.start_sfen,
            list(request.tuned_params),
            list(request.current_params),
            worker_idx=request.worker_idx,
            update_idx=request.update_idx,
            phase=request.phase,
            reserved_ids=request.reserved_ids,
            tuned_option_map=request.tuned_options,
            baseline_option_map=request.current_options,
            event_family=request.event_family,
        )

    score_sum, s_minus = await orchestrator._update_batch_execution_service.execute(
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
        tuned_plus_options=plus_options,
        tuned_minus_options=minus_options,
        current_params=params,
        reserve_pending_game=_reserve_pending_for_batch,
        run_game_pair=_run_game_pair_for_batch,
    )

    # Compute contributions and apply parameter update atomically.
    step = score_sum - s_minus

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
        early_stop = orchestrator.config.early_stop
        early_stop_threshold: float | None = None
        if early_stop is not None and early_stop.type == "delta_norm":
            early_stop_threshold = float(early_stop.threshold)

        delta_norm_sq = 0.0
        changed_params = 0
        for index, param in enumerate(params):
            if param.is_not_used:
                continue
            flip = float(flips[index])
            c_i = float(c_values[index])
            r_i = float(r_values[index])
            grads[param.name] = 0.0 if c_i == 0.0 else float(step / (2.0 * c_i * flip))
            new_value = param.value + r_i * c_i * float(step) * flip
            quantized = quantize_value(
                param,
                new_value,
                should_snap_float=orchestrator.config.is_snap_float_to_step,
                should_round_int=False,
            )
            delta_value = float(quantized - param.value)
            deltas[param.name] = delta_value
            delta_norm_sq += delta_value * delta_value
            if abs(delta_value) > 1e-10:
                changed_params += 1
            param.value = quantized
        delta_norm = float(delta_norm_sq**0.5)
        if early_stop_threshold is not None and delta_norm < early_stop_threshold:
            logger.debug(f"Early stopping triggered: delta_norm={delta_norm:.6f} < {early_stop_threshold}")
            orchestrator._stop_event.set()

        _write_current_artifact(
            orchestrator.run_dir,
            update_idx=update_idx,
            pair_index_end=pair_index_end,
            params=params,
        )

        # Log useful SPSA update summary
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
                extra_fields={
                    "schema_version": "shogiarena.spsa.update.v1",
                    "score_sum": float(step),
                    "score_definition": "plus_wins_minus_plus_losses_draw_zero",
                    "pair_index_start": int(pair_index_start),
                    "pair_index_end": int(pair_index_end),
                    "k_pair": int(k_pair),
                    "c": {p.name: c_values[idx] for idx, p in enumerate(params) if not p.is_not_used},
                    "r": {p.name: r_values[idx] for idx, p in enumerate(params) if not p.is_not_used},
                    "flips": {p.name: flips[idx] for idx, p in enumerate(params) if not p.is_not_used},
                },
            )

        orchestrator._update_recording_service.record(
            request=SpsaUpdateRecordingRequest(
                update_idx=update_idx,
                params=params_map,
                s_plus=score_sum,
                s_minus=s_minus,
                step=step,
                gradients=grads,
                deltas=deltas,
                delta_norm=delta_norm,
                batch_size=batch_size,
                total_games=batch_size * 2,
                perturbations=perturbations,
                c_k=float(max(c_values) if c_values else 0.0),
                a_k=float(max(r_values) if r_values else 0.0),
                iteration_k=k_pair,
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
            s_plus=score_sum,
            s_minus=s_minus,
            step=step,
            gradients=grads,
            deltas=deltas,
            delta_norm=delta_norm,
            batch_size=batch_size,
            total_games=batch_size * 2,
            timestamp=timestamp,
            a_k=float(max(r_values) if r_values else 0.0),
            c_k=float(max(c_values) if c_values else 0.0),
            iteration_k=k_pair,
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
        write_params=lambda updated_params: _write_current_artifact(
            orchestrator.run_dir,
            update_idx=update_idx,
            pair_index_end=pair_index_end,
            params=updated_params,
        ),
        persist_revert_index=_persist_revert_index,
    )


def _write_current_artifact(run_dir: Any, *, update_idx: int, pair_index_end: int, params: list[ParamEntry]) -> None:
    payload: JsonObject = {
        "schema_version": "shogiarena.spsa.current.v1",
        "update_idx": int(update_idx),
        "pair_index_end": int(pair_index_end),
        "theta": {entry.name: float(entry.value) for entry in params if not entry.is_not_used},
    }
    write_json_atomic(run_dir / "spsa" / "current.json", payload)


def _append_variant_artifact(
    run_dir: Any,
    *,
    update_idx: int,
    pair_index_start: int,
    pair_index_end: int,
    k_pair: int,
    plus: list[ParamEntry],
    minus: list[ParamEntry],
    flips: dict[str, int],
    plus_options: JsonObject,
    minus_options: JsonObject,
) -> None:
    variants_path = run_dir / "spsa" / "variants.jsonl"
    variants_path.parent.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {
        "schema_version": "shogiarena.spsa.variant.v1",
        "update_idx": int(update_idx),
        "pair_index_start": int(pair_index_start),
        "pair_index_end": int(pair_index_end),
        "k_pair": int(k_pair),
        "variants": {
            "plus": {
                "id": f"u{int(update_idx):06d}-plus",
                "theta": {entry.name: float(entry.value) for entry in plus if not entry.is_not_used},
                "applied_options": dict(plus_options),
            },
            "minus": {
                "id": f"u{int(update_idx):06d}-minus",
                "theta": {entry.name: float(entry.value) for entry in minus if not entry.is_not_used},
                "applied_options": dict(minus_options),
            },
        },
        "flips": dict(flips),
    }
    import json

    with variants_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


__all__ = ["run_one_spsa_update"]
