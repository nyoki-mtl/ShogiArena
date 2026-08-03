"""A pair's two colour-reversed games must run concurrently, within the game-slot budget.

The pair score is the plain mean of the two games and both use the same opening and the same
perturbed parameters, so the order is irrelevant to the estimator. Running them sequentially
left half of the configured workers idle.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import Mock

import pytest

from shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin import SpsaOrchestratorGameplayMixin
from shogiarena._core.contexts.spsa.domain.observation import SpsaObservationError


class _Harness(SpsaOrchestratorGameplayMixin):
    """Drive `_run_game_pair` with `_execute_game` replaced by an instrumented stub."""

    def __init__(self, *, game_slots: int, game_duration_s: float = 0.05) -> None:
        self._game_slot_semaphore = asyncio.Semaphore(game_slots)
        self._stop_event = asyncio.Event()
        self._game_duration_s = game_duration_s
        self.in_flight = 0
        self.max_in_flight = 0
        self.started: list[str] = []
        self.results: dict[str, int] = {}

    async def _execute_game(self, **kwargs: Any) -> tuple[int, Any]:
        game_id = str(kwargs["preassigned_game_id"])
        self.started.append(game_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._game_duration_s)
        finally:
            self.in_flight -= 1
        return self.results.get(game_id, 1), object()


def _pair_kwargs() -> dict[str, Any]:
    return {
        "start_sfen": "startpos",
        "tuned_params": [],
        "current_params": [],
        "worker_idx": 0,
        "update_idx": 1,
        "phase": "plus",
        "pair_id": "spsa-u000001-p000000",
        "reserved_ids": ("spsa-u000001-p000000-black", "spsa-u000001-p000000-white"),
    }


@pytest.mark.asyncio
async def test_pair_sides_run_concurrently_when_slots_allow() -> None:
    harness = _Harness(game_slots=2)

    score, _black, _white = await harness._run_game_pair(**_pair_kwargs())

    assert harness.max_in_flight == 2, "both sides of a pair must be in flight at once"
    # tuned wins both games -> mean of (+1, +1)
    assert score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_game_slot_budget_bounds_in_flight_games() -> None:
    # One slot means the pair degrades to the previous sequential behaviour rather than
    # over-subscribing the engine pool.
    harness = _Harness(game_slots=1)

    await harness._run_game_pair(**_pair_kwargs())

    assert harness.max_in_flight == 1


@pytest.mark.asyncio
async def test_two_pairs_share_the_same_game_budget() -> None:
    harness = _Harness(game_slots=4)

    await asyncio.gather(
        harness._run_game_pair(
            **{
                **_pair_kwargs(),
                "pair_id": "spsa-u000001-p000000",
                "reserved_ids": ("p0-black", "p0-white"),
            }
        ),
        harness._run_game_pair(
            **{
                **_pair_kwargs(),
                "pair_id": "spsa-u000001-p000001",
                "reserved_ids": ("p1-black", "p1-white"),
            }
        ),
    )

    # 2 pairs x 2 sides, all within the 4-slot budget.
    assert harness.max_in_flight == 4
    assert sorted(harness.started) == ["p0-black", "p0-white", "p1-black", "p1-white"]


@pytest.mark.asyncio
async def test_pair_score_is_order_independent() -> None:
    harness = _Harness(game_slots=2)
    # tuned wins as black (1), loses as white (0)
    harness.results = {
        "spsa-u000001-p000000-black": 1,
        "spsa-u000001-p000000-white": 0,
    }

    score, _black, _white = await harness._run_game_pair(**_pair_kwargs())

    assert score == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_failing_side_does_not_leave_the_other_running() -> None:
    harness = _Harness(game_slots=2, game_duration_s=0.02)

    async def _execute_game(**kwargs: Any) -> tuple[int, Any]:
        game_id = str(kwargs["preassigned_game_id"])
        harness.started.append(game_id)
        harness.in_flight += 1
        harness.max_in_flight = max(harness.max_in_flight, harness.in_flight)
        try:
            await asyncio.sleep(0.02)
            if game_id.endswith("-black"):
                raise RuntimeError("synthetic black failure")
        finally:
            harness.in_flight -= 1
        return 1, object()

    harness._execute_game = _execute_game  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="synthetic black failure"):
        await harness._run_game_pair(**_pair_kwargs())

    # The surviving side settled before the failure propagated, so nothing is left detached.
    assert harness.in_flight == 0


@pytest.mark.asyncio
async def test_both_sides_failing_records_one_quarantine() -> None:
    """Quarantine belongs to the pair's variant, so concurrent sides must record it once.

    The ledger rejects a second write whose evidence differs from the first, so recording twice
    turns a plain observation failure into a misleading ledger integrity error.
    """

    harness = _Harness(game_slots=4, game_duration_s=0.01)
    harness._ledger_runtime = Mock()
    harness._reserve_pending_game = Mock(side_effect=lambda **kwargs: str(kwargs["game_id"]))

    async def _execute_game(**kwargs: Any) -> tuple[int, Any]:
        game_id = str(kwargs["preassigned_game_id"])
        await asyncio.sleep(0.01)
        # Distinct classifications: this is the case the ledger treats as conflicting evidence.
        raise SpsaObservationError(f"SPSA observation failed: {'ERROR' if '-black' in game_id else 'INVALID'}")

    harness._execute_game = _execute_game  # type: ignore[method-assign]

    with pytest.raises(SpsaObservationError):
        await harness._run_game_pair(**_pair_kwargs())

    assert harness._ledger_runtime.record_variant_quarantine.call_count == 1


@pytest.mark.asyncio
async def test_pending_stop_falls_back_to_sequential_sides() -> None:
    """A pending stop must not start a second game that cannot finish.

    Sequential execution lets the first incomplete observation short-circuit the pair, which is
    what keeps a stop from being recorded as two attempted games.
    """

    harness = _Harness(game_slots=4)
    harness._stop_event.set()

    await harness._run_game_pair(**_pair_kwargs())

    assert harness.max_in_flight == 1
