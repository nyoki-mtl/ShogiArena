"""Event payload and status helpers for LTC regression."""

from __future__ import annotations

import logging
import time
from typing import Any

from shogiarena._core.contexts.game_session.application.sprt_service import SprtDecision
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


def determine_ltc_status(
    criteria: Any,
    *,
    winrate: float,
    elo: float | None,
    sprt_payload: JsonObject | None,
    sprt_decision: SprtDecision | None,
) -> tuple[str, list[str]]:
    """Resolve pass/fail/pending status and human-readable reasons."""
    status = "passed"
    fail_reasons: list[str] = []

    min_winrate = getattr(criteria, "min_winrate", None)
    if min_winrate is not None and winrate < min_winrate:
        status = "failed"
        fail_reasons.append(f"winrate {winrate:.3f} below threshold {min_winrate:.3f}")

    # Treat both 10 and -10 as "allow at most a 10 Elo drop" to preserve existing configs
    # that wrote the threshold as a negative Elo bound.
    max_elo_drop = getattr(criteria, "max_elo_drop", None)
    allowed_elo_drop = abs(max_elo_drop) if max_elo_drop is not None else None
    if allowed_elo_drop is not None and elo is not None and elo < -allowed_elo_drop:
        status = "failed"
        fail_reasons.append(f"elo {elo:.1f} below allowed drop of {allowed_elo_drop:.1f}")

    if sprt_payload is not None and sprt_decision is not None:
        if sprt_decision == SprtDecision.ACCEPT_H0:
            status = "failed"
            fail_reasons.append(f"sprt decision {sprt_decision.value} (llr={sprt_payload['llr']:.3f})")
        elif sprt_decision == SprtDecision.CONTINUE and status != "failed":
            status = "pending"
            fail_reasons.append(
                f"sprt inconclusive after {sprt_payload['games']} games (llr={sprt_payload['llr']:.3f})"
            )

    return status, fail_reasons


def append_ltc_start_event(
    runner: Any,
    *,
    update_idx: int,
    total_pairs: int,
    tuned_variant_token: str,
    baseline_idx: int,
    baseline_variant_token: str,
) -> None:
    """Emit LTC regression start event to the SPSA stream."""
    start_ts = int(time.time() * 1000)
    runner._append_spsa_event(
        {
            "event": "ltc_regression_start",
            "update_idx": int(update_idx),
            "total_pairs": total_pairs,
            "ts": start_ts,
            "family": "ltc",
            "is_ltc": True,
            "tuned_variant_token": tuned_variant_token,
            "baseline_update_idx": int(baseline_idx),
            "baseline_variant_token": baseline_variant_token,
        }
    )


def append_ltc_result_event(
    runner: Any,
    *,
    update_idx: int,
    status: str,
    winrate: float,
    elo: float | None,
    tuned_wins: int,
    baseline_wins: int,
    draws: int,
    total_games: int,
    pairs_played: int,
    fail_reasons: list[str],
    is_accepted: bool,
    tuned_variant_token: str,
    baseline_idx: int,
    baseline_variant_token: str,
    sprt_payload: JsonObject | None,
    sprt_decision: SprtDecision | None,
) -> None:
    """Emit LTC regression summary event to the SPSA stream."""
    runner._append_spsa_event(
        {
            "event": "ltc_regression_result",
            "update_idx": int(update_idx),
            "status": status,
            "winrate": winrate,
            "elo": elo,
            "tuned_wins": tuned_wins,
            "baseline_wins": baseline_wins,
            "draws": draws,
            "total_games": total_games,
            "pairs_played": pairs_played,
            "fail_reasons": fail_reasons,
            "family": "ltc",
            "is_ltc": True,
            "is_accepted": is_accepted,
            "tuned_variant_token": tuned_variant_token,
            "baseline_update_idx": int(baseline_idx),
            "baseline_variant_token": baseline_variant_token,
            "sprt": sprt_payload,
            "sprt_decision": sprt_decision.value if sprt_decision is not None else None,
        }
    )


def log_ltc_status(
    *,
    update_idx: int,
    status: str,
    winrate: float,
    elo: float | None,
    sprt_decision: SprtDecision | None,
) -> None:
    """Log notable LTC regression outcomes."""
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
