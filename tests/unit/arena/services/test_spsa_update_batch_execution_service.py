from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.update_batch_execution_service import (
    SpsaPendingReservationRequest,
    SpsaRunGamePairRequest,
    SpsaUpdateBatchExecutionRequest,
    SpsaUpdateBatchExecutionService,
)


@pytest.mark.asyncio
async def test_execute_runs_crn_batch_with_shared_sfen_per_pair() -> None:
    service = SpsaUpdateBatchExecutionService()
    reserve_calls: list[SpsaPendingReservationRequest] = []
    run_calls: list[tuple[str, str, int]] = []

    def _reserve(request: SpsaPendingReservationRequest) -> str:
        reserve_calls.append(request)
        return f"{request.phase}-{request.worker_idx}-{int(request.is_tuned_as_black)}-{len(reserve_calls)}"

    async def _run_game_pair(request: SpsaRunGamePairRequest[int]) -> tuple[float, object, object]:
        _ = request.tuned_params, request.current_params, request.update_idx, request.reserved_ids, request.event_family
        run_calls.append((request.phase, request.start_sfen, request.worker_idx))
        return (1.0, object(), object())

    score_sum, s_minus = await service.execute(
        request=SpsaUpdateBatchExecutionRequest(
            update_idx=3,
            batch_size=2,
            num_workers=2,
            inflight_factor=1,
            is_crn_enabled=True,
            sfens=["s1", "s2"],
            opening_indices=[0, 1],
            event_family="spsa",
        ),
        tuned_plus=[1],
        tuned_minus=[2],
        tuned_plus_options={"P": 1},
        tuned_minus_options={"P": 2},
        current_params=[3],
        reserve_pending_game=_reserve,
        run_game_pair=_run_game_pair,
    )

    assert score_sum == 4.0
    assert s_minus == 0.0
    assert len(reserve_calls) == 4
    assert len(run_calls) == 2
    assert reserve_calls[0].phase == "plus"
    assert reserve_calls[0].is_tuned_as_black is True
    assert reserve_calls[1].phase == "plus"
    assert reserve_calls[1].is_tuned_as_black is False
    assert run_calls[0][0] == "plus"
    assert run_calls[1][0] == "plus"
    assert run_calls[0][1] == "s1"
    assert run_calls[1][1] == "s2"


@pytest.mark.asyncio
async def test_execute_raises_when_sfens_are_empty() -> None:
    service = SpsaUpdateBatchExecutionService()

    async def _unused_run_game_pair(_request: SpsaRunGamePairRequest[int]) -> tuple[float, object, object]:
        raise RuntimeError("should not run")

    with pytest.raises(RuntimeError, match="at least one SFEN"):
        await service.execute(
            request=SpsaUpdateBatchExecutionRequest(
                update_idx=1,
                batch_size=1,
                num_workers=1,
                inflight_factor=1,
                is_crn_enabled=False,
                sfens=[],
                opening_indices=[],
                event_family="spsa",
            ),
            tuned_plus=[1],
            tuned_minus=[2],
            tuned_plus_options={},
            tuned_minus_options={},
            current_params=[3],
            reserve_pending_game=lambda _request: "gid",
            run_game_pair=_unused_run_game_pair,
        )
