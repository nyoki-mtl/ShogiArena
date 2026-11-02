from __future__ import annotations

import asyncio

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
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
@pytest.mark.parametrize("secondary_failure", [False, True])
@pytest.mark.parametrize("cancel_batch", [False, True])
async def test_execute_drains_other_pairs_before_propagating_failure(
    failure, secondary_failure, cancel_batch, caplog
) -> None:
    started, failed, release, settled = (asyncio.Event() for _ in range(4))

    async def run_pair(request):
        if request.start_sfen == "fail":
            await started.wait()
            failed.set()
            raise failure("pair failed")
        started.set()
        was_cancelled = False
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Terminal persistence drains before propagating cancellation.
            was_cancelled = True
            await release.wait()
        settled.set()
        if secondary_failure:
            raise OSError("second DB failure")
        if was_cancelled:
            raise asyncio.CancelledError
        return 0.0, None, None

    task = asyncio.create_task(
        SpsaUpdateBatchExecutionService().execute(
            request=SpsaUpdateBatchExecutionRequest(
                update_idx=1,
                batch_size=2,
                num_workers=2,
                inflight_factor=1,
                sfens=["fail", "drain"],
                opening_indices=[0, 1],
            ),
            tuned_plus=[],
            tuned_minus=[],
            tuned_plus_options=None,
            tuned_minus_options=None,
            current_params=[],
            reserve_pending_game=lambda request: f"{request.pair_id}-{request.game_slot}",
            run_game_pair=run_pair,
        )
    )
    try:
        async with asyncio.timeout(3):
            await failed.wait()
            await asyncio.sleep(0.01)
            assert not task.done()
            if cancel_batch:
                task.cancel()
                await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError if cancel_batch else failure):
                await task
        assert settled.is_set()
        if secondary_failure:
            assert "spsa-u000001-p000001" in caplog.text
            assert any(record.exc_info and str(record.exc_info[1]) == "second DB failure" for record in caplog.records)
        elif not cancel_batch:
            assert not caplog.records
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


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
