"""Analysis キャッシュサービス。

correlation / convergence の計算結果をメモリキャッシュし、
analysis ごとの dirty フラグ + 内容 fingerprint + TTL による無効化を提供する。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    AnalysisStatus,
    ConvergenceAnalysis,
    ConvergenceAnalysisSnapshot,
    CorrelationAnalysis,
    CorrelationAnalysisSnapshot,
    UpdateEntry,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaAnalysisPort,
    DashboardSpsaUpdateQueryPort,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 300  # 5 minutes
_DEFAULT_RETRY_AFTER_MS = 5_000

# Fingerprint に含めるフィールド。分析結果に影響するキーのみ。
_FINGERPRINT_KEYS: tuple[str, ...] = (
    "update_idx",
    "is_pending",
    "ended_at",
    "delta_norm",
    "wins",
    "losses",
    "draws",
    "phase_wdl",
    "ltc_regression",
    "has_ltc_regression",
    "is_ltc_rejected",
    "ltc_reverted_to",
    "params",
    "deltas",
)


def _compute_fingerprint(updates: list[UpdateEntry]) -> str:
    """Compute a content-aware fingerprint from update entries.

    Uses a hash of analysis-relevant fields from every entry, so any content
    change that would affect correlation / convergence output triggers cache
    invalidation.
    """
    if not updates:
        return "empty"
    parts: list[str] = []
    for entry in updates:
        row = {k: entry.get(k) for k in _FINGERPRINT_KEYS}
        parts.append(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str))
    digest = hashlib.md5("\n".join(parts).encode(), usedforsecurity=False).hexdigest()  # noqa: S324
    return f"{len(updates)}:{digest}"


class AnalysisCacheService:
    """Correlation / convergence の計算結果をキャッシュするサービス。

    スレッドセーフ: 内部ロックで保護。

    無効化戦略:
      1. 外部から ``notify_updates_changed()`` が呼ばれると
         correlation / convergence の dirty フラグがそれぞれ立つ
      2. ``get_*_snapshot()`` は対応する dirty が立っていなければ
         TTL 内キャッシュをそのまま返す
      3. dirty or TTL 切れの場合、**実際に分析に使うデータ** をロードし
         fingerprint を計算。前回と同一なら再計算せずキャッシュを返す。
      4. fingerprint が変わった場合のみ再計算してキャッシュを更新する。

    これにより index が空でイベント側だけ進むケースでも正しく検知でき、
    かつ内容不変のポーリングでは再計算を避ける。
    """

    def __init__(
        self,
        update_query_service: DashboardSpsaUpdateQueryPort,
        analysis_service: DashboardSpsaAnalysisPort,
    ) -> None:
        self._update_query = update_query_service
        self._analysis = analysis_service
        self._lock = threading.Lock()
        self._correlation_dirty = True  # 初回は必ず計算する
        self._convergence_dirty = True  # 初回は必ず計算する

        self._correlation_snapshot: CorrelationAnalysisSnapshot | None = None
        self._correlation_fingerprint: str = ""
        self._correlation_cached_at: float = 0.0

        self._convergence_snapshot: ConvergenceAnalysisSnapshot | None = None
        self._convergence_fingerprint: str = ""
        self._convergence_cached_at: float = 0.0

    # ------------------------------------------------------------------
    # 外部通知
    # ------------------------------------------------------------------
    def notify_updates_changed(self) -> None:
        """Update が変化した可能性がある旨を通知する。

        次の ``get_*_snapshot()`` 呼び出しでデータを再読み込みし、
        fingerprint が変化していれば再計算する。
        """
        with self._lock:
            self._correlation_dirty = True
            self._convergence_dirty = True

    # ------------------------------------------------------------------
    # Correlation
    # ------------------------------------------------------------------
    def get_correlation_snapshot(self) -> CorrelationAnalysisSnapshot:
        """キャッシュ済み or 同期計算した correlation 分析結果を v2 snapshot で返す。"""
        with self._lock:
            dirty = self._correlation_dirty
            cached = self._correlation_snapshot
            cached_at = self._correlation_cached_at
            cached_fp = self._correlation_fingerprint

        now = time.monotonic()
        ttl_valid = (now - cached_at) < _CACHE_TTL_S

        # Fast path: not dirty and within TTL
        if not dirty and cached is not None and ttl_valid:
            return cached

        try:
            updates = self._load_updates_for_correlation()
            fp = _compute_fingerprint(updates)

            # Data unchanged — reuse cached result
            if cached is not None and fp == cached_fp and ttl_valid:
                with self._lock:
                    self._correlation_dirty = False
                return cached

            result = self._analysis.compute_correlation_analysis(updates)
            snapshot = _wrap_ready_correlation(result)
            with self._lock:
                self._correlation_snapshot = snapshot
                self._correlation_fingerprint = fp
                self._correlation_cached_at = time.monotonic()
                self._correlation_dirty = False
            return snapshot
        except Exception:
            logger.exception("Failed to compute correlation analysis")
            return _error_snapshot_correlation("Correlation analysis computation failed")

    # ------------------------------------------------------------------
    # Convergence
    # ------------------------------------------------------------------
    def get_convergence_snapshot(self) -> ConvergenceAnalysisSnapshot:
        """キャッシュ済み or 同期計算した convergence 分析結果を v2 snapshot で返す。"""
        with self._lock:
            dirty = self._convergence_dirty
            cached = self._convergence_snapshot
            cached_at = self._convergence_cached_at
            cached_fp = self._convergence_fingerprint

        now = time.monotonic()
        ttl_valid = (now - cached_at) < _CACHE_TTL_S

        # Fast path: not dirty and within TTL
        if not dirty and cached is not None and ttl_valid:
            return cached

        try:
            updates = self._load_updates_for_convergence()
            fp = _compute_fingerprint(updates)

            # Data unchanged — reuse cached result
            if cached is not None and fp == cached_fp and ttl_valid:
                with self._lock:
                    self._convergence_dirty = False
                return cached

            result = self._analysis.compute_convergence_analysis(updates)
            snapshot = _wrap_ready_convergence(result)
            with self._lock:
                self._convergence_snapshot = snapshot
                self._convergence_fingerprint = fp
                self._convergence_cached_at = time.monotonic()
                self._convergence_dirty = False
            return snapshot
        except Exception:
            logger.exception("Failed to compute convergence analysis")
            return _error_snapshot_convergence("Convergence analysis computation failed")

    # ------------------------------------------------------------------
    # Data loading (same logic used by actual analysis)
    # ------------------------------------------------------------------
    def _load_updates_for_correlation(self) -> list[UpdateEntry]:
        updates = self._update_query.load_index_updates()
        if len(updates) < 2:
            updates = self._update_query.collect_updates_from_events()
        return updates

    def _load_updates_for_convergence(self) -> list[UpdateEntry]:
        updates = self._update_query.load_index_updates()
        if not updates:
            updates = self._update_query.collect_updates_from_events()
        return updates


def _now_ms() -> int:
    return int(time.time() * 1000)


def _wrap_ready_correlation(data: CorrelationAnalysis) -> CorrelationAnalysisSnapshot:
    status: AnalysisStatus = "ready"
    snapshot: CorrelationAnalysisSnapshot = {"status": status, "updated_at": _now_ms()}
    snapshot.update(data)
    return snapshot


def _wrap_ready_convergence(data: ConvergenceAnalysis) -> ConvergenceAnalysisSnapshot:
    status: AnalysisStatus = "ready"
    snapshot: ConvergenceAnalysisSnapshot = {"status": status, "updated_at": _now_ms()}
    snapshot.update(data)
    return snapshot


def _error_snapshot_correlation(reason: str) -> CorrelationAnalysisSnapshot:
    status: AnalysisStatus = "error"
    return CorrelationAnalysisSnapshot(
        status=status,
        reason=reason,
        retry_after_ms=_DEFAULT_RETRY_AFTER_MS,
        updated_at=_now_ms(),
    )


def _error_snapshot_convergence(reason: str) -> ConvergenceAnalysisSnapshot:
    status: AnalysisStatus = "error"
    return ConvergenceAnalysisSnapshot(
        status=status,
        reason=reason,
        retry_after_ms=_DEFAULT_RETRY_AFTER_MS,
        updated_at=_now_ms(),
    )
