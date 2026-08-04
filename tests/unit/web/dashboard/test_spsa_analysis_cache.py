"""AnalysisCacheService のテスト。

warming/ready/error 遷移、invalidation、thread safety を検証する。
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from shogiarena._core.contexts.dashboard.application.spsa.analysis_cache import AnalysisCacheService


def _make_services() -> tuple[MagicMock, MagicMock]:
    """テスト用のモック update_query / analysis サービスを生成する。"""
    update_query = MagicMock()
    update_query.load_index_updates.return_value = [
        {"update_idx": 1, "delta_norm": 0.5},
        {"update_idx": 2, "delta_norm": 0.3},
        {"update_idx": 3, "delta_norm": 0.2},
    ]
    update_query.collect_updates_from_events.return_value = []

    analysis = MagicMock()
    analysis.compute_correlation_analysis.return_value = {
        "correlations": {"param1": 0.8},
        "parameter_names": ["param1"],
        "num_updates": 3,
    }
    analysis.compute_convergence_analysis.return_value = {
        "convergence_metrics": {"is_converging": True},
        "delta_norm_history": [0.5, 0.3, 0.2],
        "num_updates_analyzed": 3,
    }
    return update_query, analysis


def _make_cache(
    update_query: MagicMock | None = None,
    analysis: MagicMock | None = None,
) -> AnalysisCacheService:
    if update_query is None or analysis is None:
        uq, an = _make_services()
        update_query = update_query or uq
        analysis = analysis or an
    return AnalysisCacheService(update_query_service=update_query, analysis_service=analysis)


class TestCorrelationSnapshot:
    """Correlation snapshot tests."""

    def test_ready_on_success(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        snapshot = cache.get_correlation_snapshot()
        assert snapshot["status"] == "ready"
        assert "correlations" in snapshot
        assert snapshot["updated_at"] > 0

    def test_cached_result_reused(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

    def test_error_on_computation_failure(self) -> None:
        uq, an = _make_services()
        an.compute_correlation_analysis.side_effect = RuntimeError("boom")
        cache = _make_cache(uq, an)
        snapshot = cache.get_correlation_snapshot()
        assert snapshot["status"] == "error"
        assert "reason" in snapshot
        assert snapshot["retry_after_ms"] > 0

    def test_invalidation_triggers_recompute(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

        # Change the underlying data
        uq.load_index_updates.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
            {"update_idx": 3, "delta_norm": 0.2},
            {"update_idx": 4, "delta_norm": 0.1},
        ]
        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2

    def test_cached_snapshot_has_stable_updated_at(self) -> None:
        """Cache hit must return the same updated_at (no re-generation)."""
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        snap1 = cache.get_correlation_snapshot()
        snap2 = cache.get_correlation_snapshot()
        assert snap1["updated_at"] == snap2["updated_at"]
        assert an.compute_correlation_analysis.call_count == 1

    def test_dirty_but_same_data_no_recompute(self) -> None:
        """notify_updates_changed + same data = no recompute (fingerprint match)."""
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        # Data didn't change → fingerprint matches → no recompute
        assert an.compute_correlation_analysis.call_count == 1


class TestConvergenceSnapshot:
    """Convergence snapshot tests."""

    def test_ready_on_success(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        snapshot = cache.get_convergence_snapshot()
        assert snapshot["status"] == "ready"
        assert "convergence_metrics" in snapshot

    def test_cached_result_reused(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_convergence_snapshot()
        cache.get_convergence_snapshot()
        assert an.compute_convergence_analysis.call_count == 1

    def test_error_on_computation_failure(self) -> None:
        uq, an = _make_services()
        an.compute_convergence_analysis.side_effect = RuntimeError("boom")
        cache = _make_cache(uq, an)
        snapshot = cache.get_convergence_snapshot()
        assert snapshot["status"] == "error"
        assert "reason" in snapshot

    def test_invalidation_triggers_recompute(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_convergence_snapshot()

        uq.load_index_updates.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
            {"update_idx": 3, "delta_norm": 0.2},
            {"update_idx": 4, "delta_norm": 0.1},
        ]
        cache.notify_updates_changed()
        cache.get_convergence_snapshot()
        assert an.compute_convergence_analysis.call_count == 2


class TestEventsFallback:
    """Verify cache correctly tracks event-sourced data when index is empty."""

    def test_events_fallback_convergence(self) -> None:
        """When index is empty, events data change is detected."""
        uq, an = _make_services()
        uq.load_index_updates.return_value = []
        uq.collect_updates_from_events.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
        ]
        cache = _make_cache(uq, an)
        cache.get_convergence_snapshot()
        assert an.compute_convergence_analysis.call_count == 1

        # Events data changes
        uq.collect_updates_from_events.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
        ]
        cache.notify_updates_changed()
        cache.get_convergence_snapshot()
        assert an.compute_convergence_analysis.call_count == 2

    def test_events_fallback_correlation(self) -> None:
        """When index has <2 entries, events fallback is used and tracked."""
        uq, an = _make_services()
        uq.load_index_updates.return_value = [{"update_idx": 1}]
        uq.collect_updates_from_events.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
        ]
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

        # Events data changes — content change detected
        uq.collect_updates_from_events.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
            {"update_idx": 3, "delta_norm": 0.1},
        ]
        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2


class TestContentChangeDetection:
    """Verify cache detects field-level content changes."""

    def test_ltc_regression_change_invalidates(self) -> None:
        """Change in ltc_regression field triggers recompute."""
        uq, an = _make_services()
        uq.load_index_updates.return_value = [
            {"update_idx": 1, "delta_norm": 0.5, "ltc_regression": None},
            {"update_idx": 2, "delta_norm": 0.3, "ltc_regression": None},
        ]
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

        # Same count, same max_idx, but ltc_regression changed
        uq.load_index_updates.return_value = [
            {"update_idx": 1, "delta_norm": 0.5, "ltc_regression": None},
            {"update_idx": 2, "delta_norm": 0.3, "ltc_regression": {"elo": -15}},
        ]
        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2

    def test_params_change_invalidates(self) -> None:
        """Change in params field triggers recompute."""
        uq, an = _make_services()
        uq.load_index_updates.return_value = [
            {"update_idx": 1, "params": {"p1": 100}},
            {"update_idx": 2, "params": {"p1": 110}},
        ]
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

        uq.load_index_updates.return_value = [
            {"update_idx": 1, "params": {"p1": 100}},
            {"update_idx": 2, "params": {"p1": 120}},  # param value changed
        ]
        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2


class TestThreadSafety:
    """Thread safety tests."""

    def test_concurrent_access(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        results: list[str] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                snapshot = cache.get_correlation_snapshot()
                results.append(snapshot["status"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(s == "ready" for s in results)

    def test_concurrent_invalidation(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        errors: list[Exception] = []

        def invalidator(_i: int) -> None:
            try:
                cache.notify_updates_changed()
                cache.get_correlation_snapshot()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=invalidator, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


class TestNotifyUpdatesChanged:
    """notify_updates_changed tests."""

    def test_no_data_change_no_recompute(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()
        cache.notify_updates_changed()  # dirty but data unchanged
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 1

    def test_data_change_triggers_recompute(self) -> None:
        uq, an = _make_services()
        cache = _make_cache(uq, an)
        cache.get_correlation_snapshot()

        uq.load_index_updates.return_value = [
            {"update_idx": 99, "delta_norm": 0.01},
            {"update_idx": 100, "delta_norm": 0.005},
        ]
        cache.notify_updates_changed()
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2

    def test_correlation_refresh_does_not_clear_convergence_dirty(self) -> None:
        """After one analysis refreshes, the other must still refresh once."""
        uq, an = _make_services()
        cache = _make_cache(uq, an)

        # warm both caches
        cache.get_correlation_snapshot()
        cache.get_convergence_snapshot()
        assert an.compute_correlation_analysis.call_count == 1
        assert an.compute_convergence_analysis.call_count == 1

        # underlying updates changed
        uq.load_index_updates.return_value = [
            {"update_idx": 1, "delta_norm": 0.5},
            {"update_idx": 2, "delta_norm": 0.3},
            {"update_idx": 3, "delta_norm": 0.2},
            {"update_idx": 4, "delta_norm": 0.1},
        ]
        cache.notify_updates_changed()

        # refresh correlation first
        cache.get_correlation_snapshot()
        assert an.compute_correlation_analysis.call_count == 2

        # convergence must also refresh (no stale reuse)
        cache.get_convergence_snapshot()
        assert an.compute_convergence_analysis.call_count == 2


class TestRunProgressInvalidatesWithoutExternalNotification:
    """run が進んだら、外部から通知されなくても分析が追随することを表明する。

    実際に起きた不具合: `notify_updates_changed()` を production から呼ぶ経路が
    存在せず、初回計算の結果が TTL の 5 分間そのまま返り続けていた。
    Parameter Analysis のグラフが #0 と #1 で止まったまま更新されなかった。

    テスト側だけがその入口を叩いていたため、既存の unit テストは全て通っていた。
    ここでは **notify を一切呼ばずに** データだけを進める。
    """

    @staticmethod
    def _revision_state(generation: int) -> MagicMock:
        state = MagicMock()
        state.data_generation = generation
        return state

    def test_correlation_follows_new_updates(self) -> None:
        uq, an = _make_services()
        uq.load_revision_state.return_value = self._revision_state(1)
        cache = _make_cache(uq, an)

        first = cache.get_correlation_snapshot()
        assert first["num_updates"] == 3

        # 対局が進み、ledger の data_generation が増える。通知は行わない。
        uq.load_index_updates.return_value = [{"update_idx": idx, "delta_norm": 0.1 * idx} for idx in range(1, 11)]
        uq.load_revision_state.return_value = self._revision_state(2)
        an.compute_correlation_analysis.return_value = {
            "correlations": {"param1": 0.4},
            "parameter_names": ["param1"],
            "num_updates": 10,
        }

        second = cache.get_correlation_snapshot()

        assert an.compute_correlation_analysis.call_count == 2
        assert second["num_updates"] == 10

    def test_convergence_follows_new_updates(self) -> None:
        uq, an = _make_services()
        uq.load_revision_state.return_value = self._revision_state(1)
        cache = _make_cache(uq, an)

        cache.get_convergence_snapshot()

        uq.load_index_updates.return_value = [{"update_idx": idx, "delta_norm": 0.1 * idx} for idx in range(1, 11)]
        uq.load_revision_state.return_value = self._revision_state(2)
        an.compute_convergence_analysis.return_value = {
            "convergence_metrics": {"is_converging": False},
            "delta_norm_history": [0.1] * 10,
            "num_updates_analyzed": 10,
        }

        second = cache.get_convergence_snapshot()

        assert an.compute_convergence_analysis.call_count == 2
        assert second["num_updates_analyzed"] == 10

    def test_unchanged_generation_still_avoids_reloading(self) -> None:
        """変化していないポーリングでは、ロードも再計算もしない。"""

        uq, an = _make_services()
        uq.load_revision_state.return_value = self._revision_state(7)
        cache = _make_cache(uq, an)

        cache.get_correlation_snapshot()
        load_calls_after_first = uq.load_index_updates.call_count
        cache.get_correlation_snapshot()
        cache.get_correlation_snapshot()

        assert an.compute_correlation_analysis.call_count == 1
        assert uq.load_index_updates.call_count == load_calls_after_first
