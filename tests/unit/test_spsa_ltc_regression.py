from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

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
    return rsshogi.record.Record.from_dict(
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


# The GSPRT trinomial guard suppresses a decision until every outcome (loss, draw, win) has
# occurred, so these fixtures feed two one-sided pairs first and then a pair that adds the missing
# draw/loss (or draw/win) while keeping the mean clearly winning (or losing).
class DummySprtPassOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.total_pairs = 3
        self._ltc_config.pass_criteria = LtcPassCriteria(sprt=SprtConfig(elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49))
        self._pair_calls = 0

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        idx = self._pair_calls
        self._pair_calls += 1
        if idx < 2:
            # tested wins both colors
            return (1.0, _make_record(GameResult.BLACK_WIN), _make_record(GameResult.WHITE_WIN))
        # add a draw and a loss so L/D/W are all populated while the mean stays winning -> accept_h1
        return (-0.5, _make_record(GameResult.DRAW_BY_REPETITION), _make_record(GameResult.BLACK_WIN))


class DummySprtFailOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.total_pairs = 3
        self._ltc_config.pass_criteria = LtcPassCriteria(sprt=SprtConfig(elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49))
        self._pair_calls = 0

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        idx = self._pair_calls
        self._pair_calls += 1
        if idx < 2:
            # tested loses both colors
            return (-1.0, _make_record(GameResult.WHITE_WIN), _make_record(GameResult.BLACK_WIN))
        # add a draw and a win so L/D/W are all populated while the mean stays losing -> accept_h0
        return (0.5, _make_record(GameResult.DRAW_BY_REPETITION), _make_record(GameResult.WHITE_WIN))


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
    assert record["pairs_played"] == 3
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
    assert record["pairs_played"] == 3


class DummySprtPentanomialPassOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.total_pairs = 2  # below the approximation-safety floor
        self._ltc_config.pass_criteria = LtcPassCriteria(
            sprt=SprtConfig(model="gsprt-pentanomial-v1", elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49)
        )

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        # tuned (tested) wins both colours -> pair score 2.0 -> WW bin
        return (1.0, _make_record(GameResult.BLACK_WIN), _make_record(GameResult.WHITE_WIN))


@pytest.mark.asyncio
async def test_ltc_regression_uses_pentanomial_paired_submission(tmp_path):
    # Regression: a pentanomial LTC config must use add_paired_observation, not crash on
    # add_game_result (which is trinomial-only). Two all-win pairs remain pending because the
    # Brownian approximation is intentionally disabled for tiny/zero-variance samples.
    orch = DummySprtPentanomialPassOrchestrator(tmp_path)
    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=[_make_param(3.0)],
        baseline_params=[_make_param(1.0)],
        baseline_update_idx=-1,
    )

    assert record["status"] == "pending"
    assert record["sprt"] is not None
    assert record["sprt"]["decision"] == "continue"
    assert record["pairs_played"] == 2


class DummySprtPentanomialHighMinGamesOrchestrator(DummySpsaOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.total_pairs = 2
        self._ltc_config.pass_criteria = LtcPassCriteria(
            sprt=SprtConfig(model="gsprt-pentanomial-v1", elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49, min_games=100)
        )

    async def _run_game_pair(self, *args, **kwargs):  # type: ignore[override]
        return (1.0, _make_record(GameResult.BLACK_WIN), _make_record(GameResult.WHITE_WIN))


@pytest.mark.asyncio
async def test_ltc_pentanomial_respects_min_games_floor(tmp_path):
    # Regression: min_games must scale the pentanomial min_pairs floor (max(2, ceil(min_games/2)))
    # in LTC too, so 2 pairs cannot decide when min_games=100 demands far more.
    orch = DummySprtPentanomialHighMinGamesOrchestrator(tmp_path)
    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=[_make_param(3.0)],
        baseline_params=[_make_param(1.0)],
        baseline_update_idx=-1,
    )

    assert record["sprt"]["decision"] == "continue"
    assert record["status"] != "passed"


def test_ltc_stats_winrate_is_draw_aware():
    # Regression (S8): winrate must be (W + 0.5*D) / N, not the decisive-only W / (W + L) that
    # ignored draws. With 2 wins / 1 loss / 3 draws the draw-aware winrate is 0.583, not 0.667.
    from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression import _LtcStats

    stats = _LtcStats(tuned_wins=2, baseline_wins=1, draws=3, total_games_played=6)
    winrate, elo, _average = stats.compute_metrics()
    assert winrate == pytest.approx((2 + 0.5 * 3) / 6)  # 0.583, not the decisive 2/3 = 0.667
    assert elo is not None and elo > 0.0  # winrate > 0.5 -> positive Elo


class DummySprtPassHighMinGamesOrchestrator(DummySprtPassOrchestrator):
    def __init__(self, tmp_path) -> None:  # type: ignore[override]
        super().__init__(tmp_path)
        self._ltc_config.pass_criteria = LtcPassCriteria(
            sprt=SprtConfig(elo0=0.0, elo1=400.0, alpha=0.49, beta=0.49, min_games=100)
        )


@pytest.mark.asyncio
async def test_ltc_sprt_decision_masked_below_min_games(tmp_path):
    # Regression (S9): the SPRT decision must not be acted on/reported before min_games, matching
    # the tournament runner. W4/D1/L1 = 6 games would accept_h1 but min_games=100 is not met.
    orch = DummySprtPassHighMinGamesOrchestrator(tmp_path)
    record = await run_ltc_regression(
        orch,
        update_idx=4,
        tuned_params=[_make_param(3.0)],
        baseline_params=[_make_param(1.0)],
        baseline_update_idx=-1,
    )

    assert record["sprt"]["decision"] == "continue"
    assert record["status"] != "passed"
