from __future__ import annotations

import asyncio

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.ltc_post_update_service import (
    SpsaLtcPostUpdateRequest,
    SpsaLtcPostUpdateService,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.json_types import JsonObject


def _param(value: float) -> ParamEntry:
    return ParamEntry("p", "float", value, -10.0, 10.0, 0.5, 0.1, "", False)


def _clone(entries: list[ParamEntry]) -> list[ParamEntry]:
    return [
        ParamEntry(e.name, e.type, e.value, e.min, e.max, e.step, e.delta, e.comment, e.is_not_used) for e in entries
    ]


@pytest.mark.asyncio
async def test_process_stores_baseline_when_ltc_passes() -> None:
    service = SpsaLtcPostUpdateService()
    params = [_param(3.0)]
    stored: list[tuple[list[ParamEntry], int]] = []
    persisted: list[tuple[dict[str, float], int, JsonObject]] = []

    async def _run_ltc(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"status": "passed"}

    await service.process(
        request=SpsaLtcPostUpdateRequest(
            update_idx=7,
            should_run_ltc_after_update=True,
            pre_update_snapshot=[_param(1.0)],
            post_update_snapshot=[_param(3.0)],
            baseline_snapshot=[_param(1.0)],
            baseline_update_idx=6,
        ),
        orchestrator=object(),
        params=params,
        params_lock=asyncio.Lock(),
        stop_event=asyncio.Event(),
        run_ltc_regression=_run_ltc,
        clone_param_entries=_clone,
        store_ltc_baseline=lambda snapshot, idx: stored.append((_clone(snapshot), idx)),
        append_spsa_event=lambda _payload: None,
        write_params=lambda _entries: None,
        persist_revert_index=lambda p, ts, extra: persisted.append((p, ts, extra)),
        commit_ltc_decision=lambda _evidence, _passed, _accepted, _reverted, _baseline: None,
    )

    assert len(stored) == 1
    baseline_snapshot, update_idx = stored[0]
    assert update_idx == 7
    assert baseline_snapshot[0].value == 3.0
    assert params[0].value == 3.0
    assert persisted == []


@pytest.mark.asyncio
async def test_process_reverts_params_when_ltc_fails() -> None:
    service = SpsaLtcPostUpdateService()
    params = [_param(3.0)]
    events: list[JsonObject] = []
    persisted: list[tuple[dict[str, float], int, JsonObject]] = []
    writes: list[list[ParamEntry]] = []

    async def _run_ltc(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"status": "failed"}

    await service.process(
        request=SpsaLtcPostUpdateRequest(
            update_idx=8,
            should_run_ltc_after_update=True,
            pre_update_snapshot=[_param(2.0)],
            post_update_snapshot=[_param(3.0)],
            baseline_snapshot=[_param(1.0)],
            baseline_update_idx=5,
        ),
        orchestrator=object(),
        params=params,
        params_lock=asyncio.Lock(),
        stop_event=asyncio.Event(),
        run_ltc_regression=_run_ltc,
        clone_param_entries=_clone,
        store_ltc_baseline=lambda _snapshot, _idx: None,
        append_spsa_event=lambda payload: events.append(payload),
        write_params=lambda entries: writes.append(_clone(entries)),
        persist_revert_index=lambda p, ts, extra: persisted.append((dict(p), ts, dict(extra))),
        commit_ltc_decision=lambda _evidence, _passed, _accepted, _reverted, _baseline: None,
    )

    assert params[0].value == 1.0
    assert len(writes) == 1
    assert writes[0][0].value == 1.0
    assert len(events) == 1
    assert events[0]["ltc_rejected"] is True
    assert events[0]["ltc_reverted_to"] == 5
    assert len(persisted) == 1
    params_map, _timestamp, extra_fields = persisted[0]
    assert params_map == {"p": 1.0}
    assert extra_fields["ltc_rejected"] is True
    assert extra_fields["ltc_reverted_to"] == 5


@pytest.mark.asyncio
async def test_process_reverts_shared_runner_parameter_objects_in_place() -> None:
    service = SpsaLtcPostUpdateService()
    shared_param = _param(3.0)
    runner_params = [shared_param]
    orchestrator_params = [shared_param]

    async def _run_ltc(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"status": "failed"}

    await service.process(
        request=SpsaLtcPostUpdateRequest(
            update_idx=8,
            should_run_ltc_after_update=True,
            pre_update_snapshot=[_param(2.0)],
            post_update_snapshot=[_param(3.0)],
            baseline_snapshot=[_param(1.0)],
            baseline_update_idx=5,
        ),
        orchestrator=object(),
        params=orchestrator_params,
        params_lock=asyncio.Lock(),
        stop_event=asyncio.Event(),
        run_ltc_regression=_run_ltc,
        clone_param_entries=_clone,
        store_ltc_baseline=lambda _snapshot, _idx: None,
        append_spsa_event=lambda _payload: None,
        write_params=lambda _entries: None,
        persist_revert_index=lambda _p, _ts, _extra: None,
        commit_ltc_decision=lambda _evidence, _passed, _accepted, _reverted, _baseline: None,
    )

    assert runner_params[0] is orchestrator_params[0]
    assert runner_params[0].value == 1.0
