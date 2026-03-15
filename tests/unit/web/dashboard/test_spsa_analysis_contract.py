"""Analysis contract テスト。

API レスポンスに status 必須、各 status のフィールド検証。
"""

from __future__ import annotations

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    AnalysisStatus,
    ConvergenceAnalysisSnapshot,
    CorrelationAnalysisSnapshot,
)


class TestCorrelationAnalysisSnapshot:
    """CorrelationAnalysisSnapshot contract tests."""

    def test_ready_snapshot_has_status(self) -> None:
        snapshot: CorrelationAnalysisSnapshot = {
            "status": "ready",
            "correlations": {"p1": 0.5},
            "parameter_names": ["p1"],
            "num_updates": 1,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "ready"
        assert "correlations" in snapshot

    def test_warming_snapshot(self) -> None:
        snapshot: CorrelationAnalysisSnapshot = {
            "status": "warming",
            "reason": "Computing analysis...",
            "retry_after_ms": 5000,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "warming"
        assert snapshot["reason"] == "Computing analysis..."
        assert snapshot["retry_after_ms"] == 5000

    def test_error_snapshot(self) -> None:
        snapshot: CorrelationAnalysisSnapshot = {
            "status": "error",
            "reason": "Computation failed",
            "retry_after_ms": 5000,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "error"
        assert snapshot["reason"] == "Computation failed"

    def test_status_field_is_required_type(self) -> None:
        """Verify AnalysisStatus type accepts valid values."""
        valid_statuses: list[AnalysisStatus] = ["ready", "warming", "error"]
        for status in valid_statuses:
            snapshot: CorrelationAnalysisSnapshot = {
                "status": status,
                "updated_at": 1000,
            }
            assert snapshot["status"] == status


class TestConvergenceAnalysisSnapshot:
    """ConvergenceAnalysisSnapshot contract tests."""

    def test_ready_snapshot_has_data(self) -> None:
        snapshot: ConvergenceAnalysisSnapshot = {
            "status": "ready",
            "convergence_metrics": {
                "is_converging": True,
                "recent_avg_delta_norm": 0.1,
            },
            "delta_norm_history": [0.5, 0.3, 0.2],
            "num_updates_analyzed": 3,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "ready"
        assert "convergence_metrics" in snapshot
        assert "delta_norm_history" in snapshot

    def test_warming_snapshot(self) -> None:
        snapshot: ConvergenceAnalysisSnapshot = {
            "status": "warming",
            "reason": "Warming up...",
            "retry_after_ms": 5000,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "warming"

    def test_error_snapshot(self) -> None:
        snapshot: ConvergenceAnalysisSnapshot = {
            "status": "error",
            "reason": "Failed",
            "retry_after_ms": 5000,
            "updated_at": 1000,
        }
        assert snapshot["status"] == "error"
        assert snapshot["retry_after_ms"] == 5000
