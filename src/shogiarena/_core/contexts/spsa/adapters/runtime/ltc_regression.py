"""LTC regression execution helpers for SPSA runtime."""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import rsshogi

from shogiarena._core.contexts.game_session.application.sprt_service import (
    PENTANOMIAL_MIN_PAIRS_FOR_LLR,
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    Sprt,
    SprtDecision,
    SprtResult,
)
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerPairAssignment
from shogiarena._core.contexts.spsa.domain.observation import winner_code_from_result
from shogiarena._core.contexts.spsa.domain.pair_identity import canonical_pair_ids
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.ports.ledger_ports import SpsaLedgerRuntimePort
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import should_sample_for_sprt, tested_score
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .ltc_regression_events import (
    append_ltc_result_event,
    append_ltc_start_event,
    determine_ltc_status,
    fail_closed_ltc_status_at_budget,
    log_ltc_status,
)
from .tokens import PhaseLiteral, variant_token

logger = logging.getLogger(__name__)


@runtime_checkable
class _LtcConfigPort(Protocol):
    total_pairs: int
    time_control: TimeControlLimits | None
    pass_criteria: Any


@runtime_checkable
class _LtcRunner(Protocol):
    _ltc_config: _LtcConfigPort | None
    _ltc_last_completed: int | None
    _sfens: list[str]
    num_workers: int
    _ledger_runtime: SpsaLedgerRuntimePort

    def _append_spsa_event(self, payload: JsonObject) -> None: ...

    def _record_ltc_result(self, record: JsonObject) -> None: ...

    def _ltc_normalize_result_for_sprt(self, result: GameResult, is_tuned_as_black: bool) -> GameResult: ...

    def _make_rng(
        self,
        *,
        domain: str,
        update_idx: int,
        pair_idx: int | None = None,
        parameter_id: str | None = None,
        counter: int = 0,
    ) -> Any: ...

    def _run_game_pair(
        self,
        start_sfen: str,
        tuned_params: list[ParamEntry],
        current_params: list[ParamEntry],
        worker_idx: int,
        *,
        update_idx: int,
        phase: PhaseLiteral,
        tuned_variant_token: str | None,
        baseline_variant_token: str | None,
        event_family: str = "spsa",
        pair_id: str,
        time_control_override: TimeControlLimits | None = None,
    ) -> Awaitable[tuple[float, rsshogi.record.Record, rsshogi.record.Record]]: ...


@dataclass(slots=True)
class _LtcStats:
    tuned_wins: int = 0
    baseline_wins: int = 0
    draws: int = 0
    total_games_played: int = 0
    total_score: float = 0.0
    pairs_completed: int = 0

    def add_pair_score(self, score: float) -> None:
        self.total_score += score
        self.pairs_completed += 1

    def accumulate_game(self, game: rsshogi.record.Record, *, is_tuned_as_black: bool) -> None:
        result = game.result
        winner_code_from_result(result, is_tuned_as_black=is_tuned_as_black)
        self.total_games_played += 1
        if result.is_draw():
            self.draws += 1
            return
        if result.is_black_win():
            if is_tuned_as_black:
                self.tuned_wins += 1
            else:
                self.baseline_wins += 1
            return
        if result.is_white_win():
            if is_tuned_as_black:
                self.baseline_wins += 1
            else:
                self.tuned_wins += 1
            return
        raise AssertionError("winner_code_from_result accepted an unsupported SPSA result")

    def compute_metrics(self) -> tuple[float, float | None, float]:
        total = self.total_games_played
        # Draw-aware score winrate (W + 0.5*D) / N. The previous decisive-only winrate
        # (W / (W + L)) ignored draws, disagreeing with min_winrate, the SPRT model, and
        # average_score; in a draw-heavy game that masks regressions.
        winrate = ((self.tuned_wins + 0.5 * self.draws) / total) if total > 0 else 0.5
        elo: float | None = None
        if 0.0 < winrate < 1.0:
            elo = -400 * math.log10(1 / winrate - 1)
        average_score = self.total_score / self.pairs_completed if self.pairs_completed else 0.0
        return winrate, elo, average_score


@dataclass(slots=True)
class _SprtTracker:
    sprt: Sprt | None
    min_games: int = 0
    sprt_result: SprtResult | None = None
    sprt_decision: SprtDecision | None = None
    should_stop_due_to_sprt: bool = False

    def submit(self, runner: _LtcRunner, game: rsshogi.record.Record, *, is_tuned_as_black: bool) -> None:
        if self.sprt is None:
            return
        result = game.result
        # PAUSED is excluded from the LTC SPRT sample; ERROR/INVALID fail-fast (regression gating
        # must not fold non-game outcomes into draws).
        if not should_sample_for_sprt(result, context="LTC trinomial game"):
            return
        normalized = runner._ltc_normalize_result_for_sprt(result, is_tuned_as_black)
        self._record(self.sprt.add_game_result(normalized))

    def submit_pair(self, game_black: rsshogi.record.Record, game_white: rsshogi.record.Record) -> None:
        """Submit a complete colour-reversed pair to the pentanomial model (no buffering)."""
        if self.sprt is None:
            return
        # A pentanomial observation needs both halves to be valid played games. If either is PAUSED
        # the pair is incomplete and is skipped; ERROR/INVALID in either half fails fast.
        if not (
            should_sample_for_sprt(game_black.result, context="LTC pentanomial pair (black)")
            and should_sample_for_sprt(game_white.result, context="LTC pentanomial pair (white)")
        ):
            return
        black_score = tested_score(game_black.result, is_tested_black=True)
        white_score = tested_score(game_white.result, is_tested_black=False)
        self._record(self.sprt.add_paired_observation(black_score=black_score, white_score=white_score))

    def _record(self, sprt_result: SprtResult) -> None:
        self.sprt_result = sprt_result
        # Mirror the runner: only act on (and report) a decision once games_played >= min_games.
        decision = sprt_result.decision
        if sprt_result.games_played < self.min_games:
            decision = SprtDecision.CONTINUE
        self.sprt_decision = decision
        if decision != SprtDecision.CONTINUE:
            self.should_stop_due_to_sprt = True

    @property
    def is_pentanomial(self) -> bool:
        return self.sprt is not None and self.sprt.model == SPRT_MODEL_GSPRT_PENTANOMIAL

    def finalize(self) -> None:
        if self.sprt is None:
            return
        if self.sprt_result is None:
            self._record(self.sprt.get_status())


@dataclass(slots=True)
class _LtcRunContext:
    total_pairs: int
    baseline_idx: int
    tuned_variant_token: str
    baseline_variant_token: str
    time_control_override: TimeControlLimits | None
    criteria: Any
    stats: _LtcStats
    sprt_tracker: _SprtTracker


def _require_ltc_runner(orchestrator: object) -> _LtcRunner:
    if not isinstance(orchestrator, _LtcRunner):
        raise TypeError("orchestrator does not satisfy LTC runner contract")
    return orchestrator


def _prepare_ltc_run(
    runner: _LtcRunner,
    *,
    update_idx: int,
    baseline_update_idx: int | None,
) -> _LtcRunContext | None:
    config = runner._ltc_config
    if config is None:
        return None

    total_pairs = config.total_pairs
    if total_pairs <= 0:
        return None

    time_control_override = config.time_control
    baseline_idx = baseline_update_idx if baseline_update_idx is not None else -1
    tuned_variant_token = variant_token(update_idx)
    baseline_variant_token = variant_token(baseline_idx)

    append_ltc_start_event(
        runner,
        update_idx=update_idx,
        total_pairs=total_pairs,
        tuned_variant_token=tuned_variant_token,
        baseline_idx=baseline_idx,
        baseline_variant_token=baseline_variant_token,
    )

    criteria = config.pass_criteria
    return _LtcRunContext(
        total_pairs=total_pairs,
        baseline_idx=baseline_idx,
        tuned_variant_token=tuned_variant_token,
        baseline_variant_token=baseline_variant_token,
        time_control_override=time_control_override,
        criteria=criteria,
        stats=_LtcStats(),
        sprt_tracker=_build_sprt_tracker(criteria),
    )


async def run_ltc_regression(
    orchestrator: object,
    *,
    update_idx: int,
    tuned_params: list[ParamEntry],
    baseline_params: list[ParamEntry],
    baseline_update_idx: int | None = None,
) -> JsonObject:
    runner = _require_ltc_runner(orchestrator)
    context = _prepare_ltc_run(runner, update_idx=update_idx, baseline_update_idx=baseline_update_idx)
    if context is None:
        return {}

    assignments = _build_ltc_assignments(
        runner,
        update_idx=update_idx,
        total_pairs=context.total_pairs,
    )
    runner._ledger_runtime.assign_ltc_pairs_and_start(
        update_idx=update_idx,
        assignments=assignments,
    )
    await _run_ltc_pairs(
        runner,
        stats=context.stats,
        sprt_tracker=context.sprt_tracker,
        update_idx=update_idx,
        assignments=assignments,
        tuned_params=tuned_params,
        baseline_params=baseline_params,
        tuned_variant_token=context.tuned_variant_token,
        baseline_variant_token=context.baseline_variant_token,
        time_control_override=context.time_control_override,
    )
    record = _finalize_ltc_regression(runner, update_idx=update_idx, context=context)
    runner._ltc_last_completed = update_idx
    return record


async def _run_ltc_pairs(
    runner: _LtcRunner,
    *,
    stats: _LtcStats,
    sprt_tracker: _SprtTracker,
    update_idx: int,
    assignments: list[LedgerPairAssignment],
    tuned_params: list[ParamEntry],
    baseline_params: list[ParamEntry],
    tuned_variant_token: str,
    baseline_variant_token: str,
    time_control_override: TimeControlLimits | None,
) -> None:
    for pair_idx, assignment in enumerate(assignments):
        worker_slot = pair_idx % max(1, runner.num_workers)
        pair_id = assignment.pair_id
        sfen = str(assignment.opening["start_sfen"])

        score, game_black, game_white = await runner._run_game_pair(
            sfen,
            tuned_params,
            baseline_params,
            worker_idx=worker_slot,
            update_idx=update_idx,
            phase="ltc",
            tuned_variant_token=tuned_variant_token,
            baseline_variant_token=baseline_variant_token,
            event_family="ltc",
            pair_id=pair_id,
            time_control_override=time_control_override,
        )

        stats.add_pair_score(score)
        stats.accumulate_game(game_black, is_tuned_as_black=True)
        stats.accumulate_game(game_white, is_tuned_as_black=False)

        if sprt_tracker.is_pentanomial:
            # LTC always holds a complete colour-reversed pair, so submit it directly (no pending).
            sprt_tracker.submit_pair(game_black, game_white)
        else:
            sprt_tracker.submit(runner, game_black, is_tuned_as_black=True)
            sprt_tracker.submit(runner, game_white, is_tuned_as_black=False)

        if sprt_tracker.should_stop_due_to_sprt:
            break


def _build_ltc_assignments(
    runner: _LtcRunner,
    *,
    update_idx: int,
    total_pairs: int,
) -> list[LedgerPairAssignment]:
    if not runner._sfens:
        raise RuntimeError("No SFENs available for LTC regression")
    assignments: list[LedgerPairAssignment] = []
    for pair_idx, pair_id in enumerate(canonical_pair_ids(kind="LTC", update_idx=update_idx, count=total_pairs)):
        opening_idx = runner._make_rng(
            domain="spsa.opening",
            update_idx=update_idx,
            pair_idx=pair_idx,
            counter=1,
        ).randrange(len(runner._sfens))
        sfen = runner._sfens[opening_idx]
        assignments.append(
            LedgerPairAssignment(
                pair_id=pair_id,
                opening={"opening_idx": opening_idx, "start_sfen": sfen},
                color_assignment={
                    "games": [
                        {"slot": "black", "tuned_as": "black", "game_id": f"{pair_id}-black"},
                        {"slot": "white", "tuned_as": "white", "game_id": f"{pair_id}-white"},
                    ]
                },
                flips={},
                rounding_samples={},
            )
        )
    return assignments


def _finalize_ltc_regression(
    runner: _LtcRunner,
    *,
    update_idx: int,
    context: _LtcRunContext,
) -> JsonObject:
    winrate, elo, average_score = context.stats.compute_metrics()
    context.sprt_tracker.finalize()
    sprt_payload = _build_sprt_payload(context.sprt_tracker.sprt_result, context.sprt_tracker.sprt_decision)
    status, fail_reasons = determine_ltc_status(
        context.criteria,
        winrate=winrate,
        elo=elo,
        sprt_payload=sprt_payload,
        sprt_decision=context.sprt_tracker.sprt_decision,
    )
    status, fail_reasons = fail_closed_ltc_status_at_budget(
        status,
        fail_reasons,
        pairs_played=context.stats.pairs_completed,
        total_pairs=context.total_pairs,
    )
    is_accepted = status == "passed"
    record = _build_ltc_record(
        update_idx=update_idx,
        context=context,
        status=status,
        winrate=winrate,
        elo=elo,
        average_score=average_score,
        fail_reasons=fail_reasons,
        is_accepted=is_accepted,
        sprt_payload=sprt_payload,
    )
    runner._record_ltc_result(record)
    append_ltc_result_event(
        runner,
        update_idx=update_idx,
        status=status,
        winrate=winrate,
        elo=elo,
        tuned_wins=context.stats.tuned_wins,
        baseline_wins=context.stats.baseline_wins,
        draws=context.stats.draws,
        total_games=context.stats.total_games_played,
        pairs_played=context.stats.pairs_completed,
        fail_reasons=fail_reasons,
        is_accepted=is_accepted,
        tuned_variant_token=context.tuned_variant_token,
        baseline_idx=context.baseline_idx,
        baseline_variant_token=context.baseline_variant_token,
        sprt_payload=sprt_payload,
        sprt_decision=context.sprt_tracker.sprt_decision,
    )
    log_ltc_status(
        update_idx=update_idx,
        status=status,
        winrate=winrate,
        elo=elo,
        sprt_decision=context.sprt_tracker.sprt_decision,
    )
    return record


def _build_ltc_record(
    *,
    update_idx: int,
    context: _LtcRunContext,
    status: str,
    winrate: float,
    elo: float | None,
    average_score: float,
    fail_reasons: list[str],
    is_accepted: bool,
    sprt_payload: JsonObject | None,
) -> JsonObject:
    return {
        "update_idx": int(update_idx),
        "total_pairs": context.total_pairs,
        "pairs_played": context.stats.pairs_completed,
        "total_games": context.stats.total_games_played,
        "tuned_wins": context.stats.tuned_wins,
        "baseline_wins": context.stats.baseline_wins,
        "draws": context.stats.draws,
        "winrate": winrate,
        "elo": elo,
        "average_score": average_score,
        "status": status,
        "fail_reasons": fail_reasons,
        "is_accepted": is_accepted,
        "baseline_update_idx": int(context.baseline_idx),
        "baseline_variant_token": context.baseline_variant_token,
        "tuned_variant_token": context.tuned_variant_token,
        "sprt": sprt_payload,
        "sprt_decision": (
            context.sprt_tracker.sprt_decision.value if context.sprt_tracker.sprt_decision is not None else None
        ),
    }


def _build_sprt_tracker(criteria: Any) -> _SprtTracker:
    sprt_config = criteria.sprt if criteria is not None else None
    if sprt_config is None:
        return _SprtTracker(sprt=None)
    sprt = Sprt(
        elo0=float(sprt_config.elo0),
        elo1=float(sprt_config.elo1),
        alpha=float(sprt_config.alpha),
        beta=float(sprt_config.beta),
        model=str(sprt_config.model),
        # Scale the pentanomial decision floor to min_games (2 games per pair), matching the
        # tournament runner. Ignored by the trinomial model.
        min_pairs=max(PENTANOMIAL_MIN_PAIRS_FOR_LLR, math.ceil(sprt_config.min_games / 2)),
    )
    return _SprtTracker(sprt=sprt, min_games=int(sprt_config.min_games))


def _build_sprt_payload(sprt_result: SprtResult | None, decision: SprtDecision | None = None) -> JsonObject | None:
    if sprt_result is None:
        return None
    # Use the tracker's (min_games-masked) decision so the payload agrees with the LTC status.
    effective = decision if decision is not None else sprt_result.decision
    return {
        "llr": sprt_result.llr,
        "lower": sprt_result.lower_bound,
        "upper": sprt_result.upper_bound,
        "decision": effective.value,
        "games": sprt_result.games_played,
        "wins": sprt_result.wins,
        "draws": sprt_result.draws,
        "losses": sprt_result.losses,
        "winrate": sprt_result.win_rate,
        "elo": sprt_result.elo_estimate,
    }


__all__ = ["run_ltc_regression"]
