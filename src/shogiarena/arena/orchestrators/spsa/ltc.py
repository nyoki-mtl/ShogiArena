from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any

import rshogi.record

from shogiarena.arena.services.statistics.sprt import Sprt, SprtDecision, SprtResult

from .identifiers import variant_token

if TYPE_CHECKING:  # pragma: no cover - typing only
    from shogiarena.arena.tuning.param_io import ParamEntry

    from ..spsa_orchestrator import SpsaOrchestrator


logger = logging.getLogger(__name__)


async def run_ltc_regression(
    orchestrator: SpsaOrchestrator,
    *,
    update_idx: int,
    tuned_params: list[ParamEntry],
    baseline_params: list[ParamEntry],
    baseline_update_idx: int | None = None,
) -> dict[str, Any]:
    config = orchestrator._ltc_config
    if config is None:
        return {}

    total_pairs = config.total_pairs
    if total_pairs <= 0:
        return {}

    time_control_override = config.time_control

    baseline_idx = baseline_update_idx if baseline_update_idx is not None else -1
    tuned_variant_token = variant_token(update_idx)
    baseline_variant_token = variant_token(baseline_idx)

    start_ts = int(time.time() * 1000)
    orchestrator._append_spsa_event(
        {
            "event": "ltc_regression_start",
            "update_idx": int(update_idx),
            "total_pairs": total_pairs,
            "ts": start_ts,
            "family": "ltc",
            "ltc": True,
            "tuned_variant_token": tuned_variant_token,
            "baseline_update_idx": int(baseline_idx),
            "baseline_variant_token": baseline_variant_token,
        }
    )

    rng = orchestrator._make_rng(0x5A5A0000 + int(update_idx))
    tuned_wins = 0
    baseline_wins = 0
    draws = 0
    total_games_played = 0
    total_score = 0.0
    pairs_completed = 0

    criteria = config.pass_criteria
    sprt_config = getattr(criteria, "sprt", None) if criteria is not None else None
    sprt: Sprt | None = None
    sprt_result: SprtResult | None = None
    sprt_decision: SprtDecision | None = None
    if sprt_config is not None:
        sprt = Sprt(
            elo0=float(sprt_config.elo0),
            elo1=float(sprt_config.elo1),
            alpha=float(sprt_config.alpha),
            beta=float(sprt_config.beta),
        )

    stop_due_to_sprt = False

    def _submit_to_sprt(game: rshogi.record.GameRecord, tuned_as_black: bool) -> None:
        nonlocal sprt_result, sprt_decision, stop_due_to_sprt
        if sprt is None:
            return
        result = game.result
        normalized = orchestrator._ltc_normalize_result_for_sprt(result, tuned_as_black)
        sprt_result = sprt.add_game_result(normalized)
        sprt_decision = sprt_result.decision
        if sprt_decision != SprtDecision.CONTINUE:
            stop_due_to_sprt = True

    for pair_idx in range(total_pairs):
        worker_slot = pair_idx % max(1, orchestrator.num_workers)
        if not orchestrator._sfens:
            raise RuntimeError("No SFENs available for LTC regression")
        sfen = orchestrator._sfens[rng.randrange(len(orchestrator._sfens))]

        score, gi_b, gi_w = await orchestrator._run_game_pair(
            sfen,
            tuned_params,
            baseline_params,
            worker_idx=worker_slot,
            update_idx=update_idx,
            phase="ltc",
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            event_family="ltc",
            time_control_override=time_control_override,
        )

        pairs_completed += 1

        def _accumulate(game: rshogi.record.GameRecord, tuned_as_black: bool) -> None:
            nonlocal tuned_wins, baseline_wins, draws, total_games_played
            result = game.result
            total_games_played += 1
            if result.is_draw():
                draws += 1
                return
            if result.is_black_win():
                if tuned_as_black:
                    tuned_wins += 1
                else:
                    baseline_wins += 1
                return
            if result.is_white_win():
                if tuned_as_black:
                    baseline_wins += 1
                else:
                    tuned_wins += 1
                return
            draws += 1

        _accumulate(gi_b, True)
        _accumulate(gi_w, False)
        total_score += score

        _submit_to_sprt(gi_b, True)
        _submit_to_sprt(gi_w, False)

        if stop_due_to_sprt:
            break

    effective_games = tuned_wins + baseline_wins
    winrate = (tuned_wins / effective_games) if effective_games > 0 else 0.5
    if winrate in (0.0, 1.0) or effective_games == 0:
        elo: float | None = None
    else:
        elo = -400 * math.log10(1 / winrate - 1)
    average_score = total_score / pairs_completed if pairs_completed else 0.0

    if sprt is not None:
        if sprt_result is None:
            sprt_result = sprt.get_status()
            sprt_decision = sprt_result.decision
        elif sprt_decision is None:
            sprt_decision = sprt_result.decision

    sprt_payload: dict[str, Any] | None = None
    if sprt_result is not None:
        sprt_payload = {
            "llr": sprt_result.llr,
            "lower": sprt_result.lower_bound,
            "upper": sprt_result.upper_bound,
            "decision": sprt_result.decision.value,
            "games": sprt_result.games_played,
            "wins": sprt_result.wins,
            "draws": sprt_result.draws,
            "losses": sprt_result.losses,
            "winrate": sprt_result.win_rate,
            "elo": sprt_result.elo_estimate,
        }

    status = "passed"
    fail_reasons: list[str] = []
    if criteria is not None:
        min_winrate = getattr(criteria, "min_winrate", None)
        if min_winrate is not None and winrate < min_winrate:
            status = "failed"
            fail_reasons.append(f"winrate {winrate:.3f} below threshold {min_winrate:.3f}")

    if sprt_payload is not None and sprt_decision is not None:
        if sprt_decision == SprtDecision.ACCEPT_H0:
            status = "failed"
            fail_reasons.append(f"sprt decision {sprt_decision.value} (llr={sprt_payload['llr']:.3f})")
        elif sprt_decision == SprtDecision.CONTINUE and status != "failed":
            status = "pending"
            fail_reasons.append(
                f"sprt inconclusive after {sprt_payload['games']} games (llr={sprt_payload['llr']:.3f})"
            )

    accepted = status == "passed"
    record = {
        "update_idx": int(update_idx),
        "total_pairs": total_pairs,
        "pairs_played": pairs_completed,
        "total_games": total_games_played,
        "tuned_wins": tuned_wins,
        "baseline_wins": baseline_wins,
        "draws": draws,
        "winrate": winrate,
        "elo": elo,
        "average_score": average_score,
        "status": status,
        "fail_reasons": fail_reasons,
        "accepted": accepted,
        "baseline_update_idx": int(baseline_idx),
        "baseline_variant_token": baseline_variant_token,
        "tuned_variant_token": tuned_variant_token,
        "sprt": sprt_payload,
        "sprt_decision": sprt_decision.value if sprt_decision is not None else None,
    }
    orchestrator._record_ltc_result(record)
    orchestrator._append_spsa_event(
        {
            "event": "ltc_regression_result",
            "update_idx": int(update_idx),
            "status": status,
            "winrate": winrate,
            "elo": elo,
            "tuned_wins": tuned_wins,
            "baseline_wins": baseline_wins,
            "draws": draws,
            "total_games": total_games_played,
            "pairs_played": pairs_completed,
            "fail_reasons": fail_reasons,
            "family": "ltc",
            "ltc": True,
            "accepted": accepted,
            "tuned_variant_token": tuned_variant_token,
            "baseline_update_idx": int(baseline_idx),
            "baseline_variant_token": baseline_variant_token,
            "sprt": sprt_payload,
            "sprt_decision": sprt_decision.value if sprt_decision is not None else None,
        }
    )

    if status == "failed":
        logger.info(
            "LTC regression failed at update %s (winrate=%.3f, elo=%s, sprt_decision=%s)",
            update_idx,
            winrate,
            "N/A" if elo is None else f"{elo:.2f}",
            sprt_decision.value if sprt_decision is not None else "n/a",
        )
    elif status == "pending":
        logger.info(
            "LTC regression inconclusive at update %s (winrate=%.3f, elo=%s, sprt_decision=%s)",
            update_idx,
            winrate,
            "N/A" if elo is None else f"{elo:.2f}",
            sprt_decision.value if sprt_decision is not None else "n/a",
        )

    orchestrator._ltc_last_completed = update_idx
    return record
