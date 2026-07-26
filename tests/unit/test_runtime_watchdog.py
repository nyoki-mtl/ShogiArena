"""RuntimeWatchdog の lag 観測と coverage（task 0047、0052 で coverage 対応）。

ring から event が押し出された区間、watchdog 起動前、restart をまたぐ窓は
「lag なし」ではなく「観測不能」として返すことを固定する（review finding M4）。

summary も attribution と同じ関数で in-flight lag を合成する（review finding M5）。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from shogiarena._core.shared.kernel.runtime_watchdog import LagEvent, RuntimeWatchdog


def _seed_generation(watchdog: RuntimeWatchdog, *, started_at_s: float) -> None:
    """``start()`` を呼ばずに世代情報だけを立てる（純粋な window 計算の検証用）。"""

    watchdog._generation = 1  # noqa: SLF001
    watchdog._generation_started_at_s = started_at_s  # noqa: SLF001
    watchdog._is_running = True  # noqa: SLF001


def test_observe_loop_lag_sums_intersecting_events() -> None:
    watchdog = RuntimeWatchdog()
    _seed_generation(watchdog, started_at_s=0.0)
    # Two recorded loop stalls; only their overlap with the query window counts.
    watchdog._record(LagEvent(channel="loop", start_s=10.0, end_s=10.5, magnitude_ms=500.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="loop", start_s=11.0, end_s=11.2, magnitude_ms=200.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="thread", start_s=10.0, end_s=10.9, magnitude_ms=900.0))  # noqa: SLF001

    # Window [10.2, 11.1] overlaps 0.3s of the first and 0.1s of the second loop event.
    observation = watchdog.observe_loop_lag(10.2, 11.1)
    assert observation.lag_ms_in_window == pytest.approx(300.0 + 100.0, abs=1.0)
    assert observation.is_available is True
    assert observation.coverage_complete is True


def test_observe_loop_lag_ignores_thread_events() -> None:
    watchdog = RuntimeWatchdog()
    _seed_generation(watchdog, started_at_s=0.0)
    watchdog._record(LagEvent(channel="thread", start_s=1.0, end_s=2.0, magnitude_ms=1000.0))  # noqa: SLF001
    assert watchdog.observe_loop_lag(1.0, 2.0).lag_ms_in_window == pytest.approx(0.0, abs=0.01)


def test_observe_loop_lag_is_zero_for_a_degenerate_window() -> None:
    watchdog = RuntimeWatchdog()
    _seed_generation(watchdog, started_at_s=0.0)
    watchdog._record(LagEvent(channel="loop", start_s=1.0, end_s=2.0, magnitude_ms=1000.0))  # noqa: SLF001
    assert watchdog.observe_loop_lag(5.0, 5.0).lag_ms_in_window == 0.0
    assert watchdog.observe_loop_lag(5.0, 4.0).lag_ms_in_window == 0.0


# --- coverage（M4） ----------------------------------------------------------


def test_never_started_watchdog_reports_unavailable_not_zero_lag() -> None:
    watchdog = RuntimeWatchdog()
    observation = watchdog.observe_loop_lag(1.0, 2.0)
    assert observation.is_available is False
    assert observation.coverage_complete is False


def test_window_starting_before_the_watchdog_is_incomplete() -> None:
    watchdog = RuntimeWatchdog()
    _seed_generation(watchdog, started_at_s=10.0)
    assert watchdog.observe_loop_lag(9.9, 11.0).coverage_complete is False
    assert watchdog.observe_loop_lag(10.0, 11.0).coverage_complete is True


def test_ring_overflow_marks_the_evicted_span_as_uncovered() -> None:
    watchdog = RuntimeWatchdog(ring_size=2)
    _seed_generation(watchdog, started_at_s=0.0)
    watchdog._record(LagEvent(channel="loop", start_s=1.0, end_s=1.5, magnitude_ms=500.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="loop", start_s=2.0, end_s=2.5, magnitude_ms=500.0))  # noqa: SLF001
    # This third event evicts the first one; that span is no longer observable.
    watchdog._record(LagEvent(channel="loop", start_s=3.0, end_s=3.5, magnitude_ms=500.0))  # noqa: SLF001

    evicted_span = watchdog.observe_loop_lag(1.2, 3.6)
    assert evicted_span.coverage_complete is False
    assert evicted_span.dropped_event_count == 1
    assert evicted_span.dropped_through_s == pytest.approx(1.5)

    # A window entirely after the frontier is still fully covered.
    assert watchdog.observe_loop_lag(2.0, 3.6).coverage_complete is True


def test_window_at_the_ring_frontier_is_the_boundary() -> None:
    watchdog = RuntimeWatchdog(ring_size=1)
    _seed_generation(watchdog, started_at_s=0.0)
    watchdog._record(LagEvent(channel="loop", start_s=1.0, end_s=1.5, magnitude_ms=500.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="loop", start_s=2.0, end_s=2.5, magnitude_ms=500.0))  # noqa: SLF001

    assert watchdog.observe_loop_lag(1.4999, 3.0).coverage_complete is False
    assert watchdog.observe_loop_lag(1.5, 3.0).coverage_complete is True


@pytest.mark.asyncio
async def test_window_spanning_a_watchdog_restart_is_incomplete() -> None:
    watchdog = RuntimeWatchdog(interval_s=0.02)
    watchdog.start()
    before_restart_s = time.perf_counter()
    await asyncio.sleep(0.03)
    await watchdog.stop()
    watchdog.start()
    try:
        await asyncio.sleep(0.03)
        after_restart_s = time.perf_counter()
        spanning = watchdog.observe_loop_lag(before_restart_s, after_restart_s)
        assert spanning.watchdog_generation == 2
        assert spanning.coverage_complete is False
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_query_after_stop_does_not_claim_complete_coverage() -> None:
    watchdog = RuntimeWatchdog(interval_s=0.02)
    watchdog.start()
    start_s = time.perf_counter()
    await asyncio.sleep(0.03)
    await watchdog.stop()

    observation = watchdog.observe_loop_lag(start_s, time.perf_counter())
    assert observation.coverage_complete is False
    # 停止後に経過時間を進行中の停滞として数えない。
    assert observation.in_flight_lag_ms == 0.0


# --- in-flight lag（M5） -----------------------------------------------------


@pytest.mark.asyncio
async def test_in_flight_stall_is_reported_by_both_the_query_and_the_summary() -> None:
    watchdog = RuntimeWatchdog(interval_s=0.02, loop_threshold_ms=100.0, thread_threshold_ms=100.0)
    watchdog.start()
    try:
        start_s = time.perf_counter()
        # Block the loop synchronously; the heartbeat cannot record anything yet.
        time.sleep(0.3)
        observation = watchdog.observe_loop_lag(start_s, time.perf_counter())
        summary_during_stall = watchdog.summary()
        assert observation.in_flight_lag_ms > 100.0
        assert observation.lag_ms_in_window >= observation.in_flight_lag_ms
        # 記録済み event が無くても artifact 側が「lag なし」と表示しないこと。
        assert summary_during_stall.max_loop_lag_ms > 100.0
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_loop_heartbeat_records_a_blocking_stall() -> None:
    watchdog = RuntimeWatchdog(interval_s=0.02, loop_threshold_ms=100.0, thread_threshold_ms=100.0)
    watchdog.start()
    try:
        time.sleep(0.3)
        await asyncio.sleep(0.1)
    finally:
        await watchdog.stop()

    summary = watchdog.summary()
    assert summary.loop_lag_events >= 1
    assert summary.max_loop_lag_ms >= 200.0


def test_summary_tracks_max_and_counts() -> None:
    watchdog = RuntimeWatchdog()
    watchdog._record(LagEvent(channel="loop", start_s=1.0, end_s=1.3, magnitude_ms=300.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="loop", start_s=2.0, end_s=2.5, magnitude_ms=500.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="thread", start_s=3.0, end_s=3.2, magnitude_ms=200.0))  # noqa: SLF001

    summary = watchdog.summary()
    assert summary.loop_lag_events == 2
    assert summary.thread_lag_events == 1
    assert summary.max_loop_lag_ms == pytest.approx(500.0)
    assert summary.max_thread_lag_ms == pytest.approx(200.0)
    assert summary.dropped_loop_events == 0
    assert summary.is_coverage_complete is True


def test_summary_reports_dropped_events_as_incomplete_coverage() -> None:
    watchdog = RuntimeWatchdog(ring_size=1)
    watchdog._record(LagEvent(channel="loop", start_s=1.0, end_s=1.3, magnitude_ms=300.0))  # noqa: SLF001
    watchdog._record(LagEvent(channel="loop", start_s=2.0, end_s=2.5, magnitude_ms=500.0))  # noqa: SLF001

    summary = watchdog.summary()
    assert summary.dropped_loop_events == 1
    assert summary.is_coverage_complete is False


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_leaves_no_thread() -> None:
    watchdog = RuntimeWatchdog(interval_s=0.02)
    watchdog.start()
    await asyncio.sleep(0.03)
    await watchdog.stop()
    # Second stop must not raise.
    await watchdog.stop()
    assert watchdog._thread is None  # noqa: SLF001


def test_default_ring_covers_a_sustained_stall_burst() -> None:
    """ring 容量が shadow 計測の観測数（559 event）に対して十分であること。

    256 では 303 event を押し出して coverage 不完全になり、以降の timeout が
    軒並み ``unknown`` へ倒れた（task 0052 / Decision 16）。
    """

    from shogiarena._core.shared.kernel.runtime_watchdog import DEFAULT_RING_SIZE

    assert DEFAULT_RING_SIZE >= 1000
    watchdog = RuntimeWatchdog()
    _seed_generation(watchdog, started_at_s=0.0)
    for index in range(600):
        watchdog._record(  # noqa: SLF001
            LagEvent(channel="loop", start_s=float(index), end_s=index + 0.25, magnitude_ms=250.0)
        )

    summary = watchdog.summary()
    assert summary.dropped_loop_events == 0
    assert summary.is_coverage_complete is True
    assert watchdog.observe_loop_lag(0.0, 600.0).coverage_complete is True
