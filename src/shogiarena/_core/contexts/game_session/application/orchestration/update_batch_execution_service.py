"""SPSA mini-batch execution service."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

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
        current_params: Sequence[ParamT],
        reserve_pending_game: ReservePendingGamePort,
        run_game_pair: RunGamePairPort[ParamT, RunResultT],
    ) -> tuple[float, float]:
        if not request.sfens:
            raise RuntimeError("SPSA requires at least one SFEN for batch execution")

        max_concurrent = min(request.num_workers * request.inflight_factor, request.batch_size * 2)
        semaphore = asyncio.Semaphore(max_concurrent)

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
            minus_reserved = reserve_pair(phase="minus", worker_slot=worker_slot)

            async with semaphore:
                if request.is_crn_enabled:
                    sfen = request.sfens[rng.randrange(len(request.sfens))]
                    s_plus, _gi_b1, _gi_w1 = await run_game_pair(
                        SpsaRunGamePairRequest(
                            start_sfen=sfen,
                            tuned_params=tuned_plus,
                            current_params=current_params,
                            worker_idx=worker_slot,
                            update_idx=request.update_idx,
                            phase="plus",
                            reserved_ids=plus_reserved,
                            event_family=request.event_family,
                        )
                    )
                    s_minus, _gi_b2, _gi_w2 = await run_game_pair(
                        SpsaRunGamePairRequest(
                            start_sfen=sfen,
                            tuned_params=tuned_minus,
                            current_params=current_params,
                            worker_idx=worker_slot,
                            update_idx=request.update_idx,
                            phase="minus",
                            reserved_ids=minus_reserved,
                            event_family=request.event_family,
                        )
                    )
                else:
                    sfen_plus = request.sfens[rng.randrange(len(request.sfens))]
                    sfen_minus = request.sfens[rng.randrange(len(request.sfens))]
                    s_plus, _gi_b1, _gi_w1 = await run_game_pair(
                        SpsaRunGamePairRequest(
                            start_sfen=sfen_plus,
                            tuned_params=tuned_plus,
                            current_params=current_params,
                            worker_idx=worker_slot,
                            update_idx=request.update_idx,
                            phase="plus",
                            reserved_ids=plus_reserved,
                            event_family=request.event_family,
                        )
                    )
                    s_minus, _gi_b2, _gi_w2 = await run_game_pair(
                        SpsaRunGamePairRequest(
                            start_sfen=sfen_minus,
                            tuned_params=tuned_minus,
                            current_params=current_params,
                            worker_idx=worker_slot,
                            update_idx=request.update_idx,
                            phase="minus",
                            reserved_ids=minus_reserved,
                            event_family=request.event_family,
                        )
                    )
                return s_plus, s_minus

        batch_results = await asyncio.gather(*[run_batch_item(batch_idx) for batch_idx in range(request.batch_size)])
        total_s_plus = sum(s_plus for s_plus, _s_minus in batch_results)
        total_s_minus = sum(s_minus for _s_plus, s_minus in batch_results)
        return total_s_plus / request.batch_size, total_s_minus / request.batch_size


__all__ = [
    "SpsaPendingReservationRequest",
    "SpsaRunGamePairRequest",
    "SpsaUpdateBatchExecutionRequest",
    "SpsaUpdateBatchExecutionService",
]
