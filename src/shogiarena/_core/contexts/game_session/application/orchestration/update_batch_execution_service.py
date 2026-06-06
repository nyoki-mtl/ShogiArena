"""SPSA mini-batch execution service."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from shogiarena._core.shared.kernel.json_types import JsonObject

ParamT = TypeVar("ParamT")
RunResultT = TypeVar("RunResultT")
PhaseLiteral = Literal["plus", "minus"]


@dataclass(frozen=True)
class SpsaPendingReservationRequest:
    """Request contract for one pending game reservation."""

    update_idx: int
    phase: PhaseLiteral
    is_tuned_as_black: bool
    worker_idx: int
    event_family: str


@dataclass(frozen=True)
class SpsaRunGamePairRequest(Generic[ParamT]):
    """Request contract for one SPSA game-pair execution."""

    start_sfen: str
    tuned_params: Sequence[ParamT]
    current_params: Sequence[ParamT]
    worker_idx: int
    update_idx: int
    phase: PhaseLiteral
    reserved_ids: tuple[str, str]
    event_family: str
    tuned_options: JsonObject | None = None
    current_options: JsonObject | None = None


ReservePendingGamePort = Callable[[SpsaPendingReservationRequest], str]
RunGamePairPort = Callable[[SpsaRunGamePairRequest[ParamT]], Awaitable[tuple[float, RunResultT, RunResultT]]]


@dataclass(frozen=True)
class SpsaUpdateBatchExecutionRequest:
    """Input DTO for one SPSA mini-batch execution."""

    update_idx: int
    batch_size: int
    num_workers: int
    inflight_factor: int
    is_crn_enabled: bool
    sfens: Sequence[str]
    event_family: str = "spsa"


class SpsaUpdateBatchExecutionService:
    """Execute SPSA mini-batch game pairs with CRN/non-CRN scheduling."""

    async def execute(
        self,
        *,
        request: SpsaUpdateBatchExecutionRequest,
        rng: random.Random,
        tuned_plus: Sequence[ParamT],
        tuned_minus: Sequence[ParamT],
        tuned_plus_options: JsonObject | None,
        tuned_minus_options: JsonObject | None,
        current_params: Sequence[ParamT],
        reserve_pending_game: ReservePendingGamePort,
        run_game_pair: RunGamePairPort[ParamT, RunResultT],
    ) -> tuple[float, float]:
        if not request.sfens:
            raise RuntimeError("SPSA requires at least one SFEN for batch execution")

        max_concurrent = min(request.num_workers * request.inflight_factor, request.batch_size * 2)
        semaphore = asyncio.Semaphore(max_concurrent)

        # Resolve the opening for every batch item up front, in batch order, so the
        # assignment is deterministic and reproducible on resume. Drawing inside the
        # concurrent section below would make rng consumption depend on async
        # scheduling order (non-reproducible).
        if request.is_crn_enabled:
            # Repeated-block CRN: the same pair offset reuses the same opening
            # across update batches.
            opening_indices = [batch_idx % len(request.sfens) for batch_idx in range(request.batch_size)]
        else:
            opening_indices = [rng.randrange(len(request.sfens)) for _ in range(request.batch_size)]

        def reserve_pair(*, phase: PhaseLiteral, worker_slot: int) -> tuple[str, str]:
            return (
                reserve_pending_game(
                    SpsaPendingReservationRequest(
                        update_idx=request.update_idx,
                        phase=phase,
                        is_tuned_as_black=True,
                        worker_idx=worker_slot,
                        event_family=request.event_family,
                    )
                ),
                reserve_pending_game(
                    SpsaPendingReservationRequest(
                        update_idx=request.update_idx,
                        phase=phase,
                        is_tuned_as_black=False,
                        worker_idx=worker_slot,
                        event_family=request.event_family,
                    )
                ),
            )

        async def run_batch_item(batch_idx: int) -> tuple[float, float]:
            worker_slot = int((request.update_idx * request.batch_size + batch_idx) % max(1, request.num_workers))
            plus_reserved = reserve_pair(phase="plus", worker_slot=worker_slot)

            sfen = request.sfens[opening_indices[batch_idx]]
            async with semaphore:
                pair_score, _gi_b1, _gi_w1 = await run_game_pair(
                    SpsaRunGamePairRequest(
                        start_sfen=sfen,
                        tuned_params=tuned_plus,
                        current_params=tuned_minus,
                        worker_idx=worker_slot,
                        update_idx=request.update_idx,
                        phase="plus",
                        reserved_ids=plus_reserved,
                        event_family=request.event_family,
                        tuned_options=tuned_plus_options,
                        current_options=tuned_minus_options,
                    )
                )
                # _run_game_pair returns the mean over the color-reversed pair.
                # SPSA score_sum is plus_wins - plus_losses over games.
                return pair_score * 2.0, 0.0

        batch_results = await asyncio.gather(*[run_batch_item(batch_idx) for batch_idx in range(request.batch_size)])
        total_s_plus = sum(s_plus for s_plus, _s_minus in batch_results)
        total_s_minus = sum(s_minus for _s_plus, s_minus in batch_results)
        return total_s_plus, total_s_minus


__all__ = [
    "SpsaPendingReservationRequest",
    "SpsaRunGamePairRequest",
    "SpsaUpdateBatchExecutionRequest",
    "SpsaUpdateBatchExecutionService",
]
