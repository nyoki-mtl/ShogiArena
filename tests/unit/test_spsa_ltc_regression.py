from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import SprtConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import (
    LtcPassCriteria,
    LtcRegressionConfig,
)
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression import run_ltc_regression
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.game_results import GameResult


def _make_record(result: GameResult) -> object:
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "game_name": "test",
                "game_type": "spsa",
                "black_player": "b",
                "white_player": "w",
                "attributes": {"game_name": "test", "game_type": "spsa"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result.name, "ply_count": 0},
        }
    )


class DummySpsaOrchestrator(SpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        self._ltc_config = LtcRegressionConfig(
            enabled=True,
            every_n_updates=5,
            total_pairs=1,
            pass_criteria=LtcPassCriteria(min_winrate=0.75),
        )
        self._ltc_results_path = None
        self._ltc_last_completed = None
        self._ltc_baseline_snapshot = None
        self._ltc_baseline_update_idx = None
        self._events: list[dict[str, object]] = []
        self._records: list[dict[str, object]] = []
        self._session_uuid = "session"
        self.num_workers = 1
        self._sfens = ["startpos"]
        self.config = SimpleNamespace(space_path="space.yaml")
        self._stop_event = asyncio.Event()
        self._params_lock = asyncio.Lock()
        self.run_dir = tmp_path

    def _append_spsa_event(self, payload: dict[str, object]) -> None:  # type: ignore[override]
        self._events.append(payload)

    def _record_ltc_result(self, record: dict[str, object]) -> None:  # type: ignore[override]
        self._records.append(record)

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        return (
            -1.0,
            _make_record(GameResult.WHITE_WIN),
            _make_record(GameResult.BLACK_WIN),
        )


class DummySprtPassOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.pass_criteria = LtcPassCriteria(sprt=SprtConfig(elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49))

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        return (
            1.0,
            _make_record(GameResult.BLACK_WIN),
            _make_record(GameResult.WHITE_WIN),
        )


class DummySprtFailOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.pass_criteria = LtcPassCriteria(sprt=SprtConfig(elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49))

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        return (
            -1.0,
            _make_record(GameResult.WHITE_WIN),
            _make_record(GameResult.BLACK_WIN),
        )


def _make_param(value: float) -> ParamEntry:
    return ParamEntry("param", "float", value, -10.0, 10.0, 0.5, 0.1, "", False)


def test_store_ltc_baseline_clones_entries():
    params = [_make_param(1.0), _make_param(2.0)]
    orch = object.__new__(SpsaOrchestrator)
    orch._ltc_baseline_snapshot = None
    orch._ltc_baseline_update_idx = None

    orch._store_ltc_baseline(params, 5)

    assert orch._ltc_baseline_update_idx == 5
    assert orch._ltc_baseline_snapshot is not params
    params[0].value = 99.0
    assert orch._ltc_baseline_snapshot[0].value != params[0].value


@pytest.mark.asyncio
async def test_ltc_regression_records_rejection_without_stopping(tmp_path):
    orch = DummySpsaOrchestrator(tmp_path)
    tuned_params = [_make_param(3.0)]
    baseline_params = [_make_param(1.0)]

    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=tuned_params,
        baseline_params=baseline_params,
        baseline_update_idx=-1,
    )

    assert record["status"] == "failed"
    assert record["is_accepted"] is False
    assert record["baseline_update_idx"] == -1
    assert record["baseline_variant_token"] == "v000000"
    assert record["tuned_variant_token"] == "v000004"
    assert not orch._stop_event.is_set()
    assert orch._ltc_last_completed == 4
    assert any(
        event.get("event") == "ltc_regression_result" and event.get("baseline_variant_token") == "v000000"
        for event in orch._events
    )


@pytest.mark.asyncio
async def test_ltc_regression_uses_sprt_acceptance(tmp_path):
    orch = DummySprtPassOrchestrator(tmp_path)
    tuned_params = [_make_param(3.0)]
    baseline_params = [_make_param(1.0)]

    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=tuned_params,
        baseline_params=baseline_params,
        baseline_update_idx=-1,
    )

    assert record["status"] == "passed"
    assert record["is_accepted"] is True
    assert record["sprt"] is not None
    assert record["sprt"]["decision"] == "accept_h1"
    assert record["pairs_played"] == 1
    assert orch._ltc_last_completed == 4


@pytest.mark.asyncio
async def test_ltc_regression_uses_sprt_rejection(tmp_path):
    orch = DummySprtFailOrchestrator(tmp_path)
    tuned_params = [_make_param(3.0)]
    baseline_params = [_make_param(1.0)]

    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=tuned_params,
        baseline_params=baseline_params,
        baseline_update_idx=-1,
    )

    assert record["status"] == "failed"
    assert record["is_accepted"] is False
    assert record["sprt"] is not None
    assert record["sprt"]["decision"] == "accept_h0"
    assert record["pairs_played"] == 1
