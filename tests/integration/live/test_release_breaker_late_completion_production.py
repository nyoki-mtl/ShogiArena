"""Production-composition evidence for a completion arriving after a breaker stop."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_tournament_composition_timeout import (
    _block_after_parallel_bestmove_observations,
    _run_tournament,
    _timeout_origins_in_db,
)

from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin
from shogiarena._core.shared.kernel.timeout_breaker import (
    TIMEOUT_BREAKER_POLICIES,
    TimeoutBreakerPolicy,
)


@pytest.mark.asyncio
async def test_in_flight_completion_after_breaker_stop_is_still_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値到達済みでも、既に走っていた局の terminal record を捨てない。"""

    # 実 policy の数え方・停止経路を通しつつ、release smoke を2並列局へ bounded にする。
    # 同じ強制 stall でも suite 全体の負荷により explanatory-lag の境界が変わり、
    # orchestrator_stall / unknown のどちらにもなり得る。この test の主題は分類境界ではなく
    # breaker 後の late terminal persistence なので、両 invalid origin を同じ閾値にする。
    invalid_origins = {
        TimeoutOrigin.ORCHESTRATOR_STALL.value,
        TimeoutOrigin.UNKNOWN.value,
    }
    for origin in invalid_origins:
        monkeypatch.setitem(
            TIMEOUT_BREAKER_POLICIES,
            origin,
            TimeoutBreakerPolicy(
                consecutive_limit=1,
                ratio_limit=None,
                ratio_min_games=1,
                termination_reason="timeout-burst",
            ),
        )

    _block_after_parallel_bestmove_observations(monkeypatch, observation_count=2, block_s=0.75)

    status = await _run_tournament(
        tmp_path,
        delay_s=0.0,
        time_ms=500,
        margin_ms=0,
        games_per_pair=2,
        num_parallel=2,
    )

    # 最初の completion で breaker が立つ。2件目はその後着なので、completed=2 と
    # DB の2行が揃うことが late terminal persistence の証拠になる。
    assert status["termination_reason"] == "timeout-burst"
    assert status["status"] == "failed"
    assert status["scheduled"] == 2
    assert status["completed"] == 2
    assert status["cancelled"] == 0
    assert status["not_played"] == 0
    timeout_counts = status["timeouts_by_origin"]
    assert set(timeout_counts) <= invalid_origins
    assert sum(timeout_counts.values()) == 2

    rows = _timeout_origins_in_db(tmp_path / "run")
    assert len(rows) == 2
    assert {row_origin for _, row_origin in rows} <= invalid_origins
