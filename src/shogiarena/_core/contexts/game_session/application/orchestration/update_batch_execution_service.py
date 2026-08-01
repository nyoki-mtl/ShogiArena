"""SPSA mini-batch execution service."""

from __future__ import annotations

import asyncio
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
    pair_id: str
    game_slot: Literal["black", "white"]


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
    pair_id: str
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
    opening_indices: Sequence[int]
    event_family: str = "spsa"


@dataclass(frozen=True)
class SpsaPairAssignmentRequest:
    """Deterministic pair identity and opening resolved before dispatch."""

    update_idx: int
    pair_id: str
    batch_idx: int
    opening_idx: int
    start_sfen: str
    reserved_ids: tuple[str, str]


RecordPairAssignmentPort = Callable[[SpsaPairAssignmentRequest], None]
AssignmentsReadyPort = Callable[[], None]


class SpsaUpdateBatchExecutionService:
    """Execute SPSA mini-batch game pairs with CRN/non-CRN scheduling."""

    async def execute(
        self,
        *,
        request: SpsaUpdateBatchExecutionRequest,
        tuned_plus: Sequence[ParamT],
        tuned_minus: Sequence[ParamT],
        tuned_plus_options: JsonObject | None,
        tuned_minus_options: JsonObject | None,
        current_params: Sequence[ParamT],
        reserve_pending_game: ReservePendingGamePort,
        run_game_pair: RunGamePairPort[ParamT, RunResultT],
        record_pair_assignment: RecordPairAssignmentPort | None = None,
        on_assignments_ready: AssignmentsReadyPort | None = None,
    ) -> tuple[float, float]:
        if not request.sfens:
            raise RuntimeError("SPSA requires at least one SFEN for batch execution")

        max_concurrent = min(request.num_workers * request.inflight_factor, request.batch_size * 2)
        semaphore = asyncio.Semaphore(max_concurrent)

        opening_indices = list(request.opening_indices)
        if len(opening_indices) != request.batch_size:
            raise ValueError("SPSA opening assignment count must match batch_size")
        if any(index < 0 or index >= len(request.sfens) for index in opening_indices):
            raise ValueError("SPSA opening assignment is outside the SFEN range")

        def reserve_pair(*, phase: PhaseLiteral, worker_slot: int) -> tuple[str, str]:
            pair_id = f"spsa-u{request.update_idx:06d}-p{len(reserved_pairs):06d}"
            return (
                reserve_pending_game(
                    SpsaPendingReservationRequest(
                        update_idx=request.update_idx,
                        phase=phase,
                        is_tuned_as_black=True,
                        worker_idx=worker_slot,
                        event_family=request.event_family,
                        pair_id=pair_id,
                        game_slot="black",
                    )
                ),
                reserve_pending_game(
                    SpsaPendingReservationRequest(
                        update_idx=request.update_idx,
                        phase=phase,
                        is_tuned_as_black=False,
                        worker_idx=worker_slot,
                        event_family=request.event_family,
                        pair_id=pair_id,
                        game_slot="white",
                    )
                ),
            )

        reserved_pairs: list[tuple[str, str]] = []
        for batch_idx in range(request.batch_size):
            worker_slot = int((request.update_idx * request.batch_size + batch_idx) % max(1, request.num_workers))
            reserved_pairs.append(reserve_pair(phase="plus", worker_slot=worker_slot))

        if record_pair_assignment is not None:
            for batch_idx, opening_idx in enumerate(opening_indices):
                record_pair_assignment(
                    SpsaPairAssignmentRequest(
                        update_idx=request.update_idx,
                        pair_id=f"spsa-u{request.update_idx:06d}-p{batch_idx:06d}",
                        batch_idx=batch_idx,
                        opening_idx=opening_idx,
                        start_sfen=request.sfens[opening_idx],
                        reserved_ids=reserved_pairs[batch_idx],
                    )
                )
        if on_assignments_ready is not None:
            on_assignments_ready()

        async def run_batch_item(batch_idx: int) -> tuple[float, float]:
            worker_slot = int((request.update_idx * request.batch_size + batch_idx) % max(1, request.num_workers))
            plus_reserved = reserved_pairs[batch_idx]

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
                        pair_id=f"spsa-u{request.update_idx:06d}-p{batch_idx:06d}",
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
    "SpsaPairAssignmentRequest",
    "SpsaRunGamePairRequest",
    "SpsaUpdateBatchExecutionRequest",
    "SpsaUpdateBatchExecutionService",
]
