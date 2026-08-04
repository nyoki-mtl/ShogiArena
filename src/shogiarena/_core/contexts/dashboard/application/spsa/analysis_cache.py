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
      1. ledger の ``data_generation`` を見る。前回と同じなら、データは変化して
         いないと**断定できる**のでキャッシュをそのまま返す（ロードもしない）。
      2. 変化している、または ``data_generation`` を読めない場合は
         **実際に分析に使うデータ** をロードし fingerprint を計算する。
      3. fingerprint が前回と同一なら再計算せずキャッシュを返す。
      4. fingerprint が変わった場合のみ再計算してキャッシュを更新する。

    これにより index が空でイベント側だけ進むケースでも正しく検知でき、
    かつ内容不変のポーリングでは再計算を避ける。

    以前は 1 が「外部から ``notify_updates_changed()`` が呼ばれたか」だったが、
    **production にその呼び出し元が存在しなかった**ため、初回計算の結果が
    TTL の 5 分間そのまま返り続けていた（Parameter Analysis が更新されない不具合）。
    テストだけがこの入口を叩いていたので、unit テストでは検出できなかった。
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
        self._correlation_generation: int | None = None

        self._convergence_snapshot: ConvergenceAnalysisSnapshot | None = None
        self._convergence_fingerprint: str = ""
        self._convergence_cached_at: float = 0.0
        self._convergence_generation: int | None = None

    # ------------------------------------------------------------------
    # 外部通知
    # ------------------------------------------------------------------
    def notify_updates_changed(self) -> None:
        """Update が変化した可能性がある旨を明示的に通知する。

        通常はこの呼び出しは要らない。``get_*_snapshot()`` は ledger の
        ``data_generation`` を見て自力で変化を検知する。
        """
        with self._lock:
            self._correlation_dirty = True
            self._convergence_dirty = True

    def _current_data_generation(self) -> int | None:
        """Return the projected data generation, or ``None`` when unavailable.

        ledger の ``data_generation`` は update commit と対局結果の両方で増える
        （1.2.2 で dashboard の更新契機として導入したもの）。読めない場合は
        「変化していないと断定できない」ので ``None`` を返し、呼び出し側は
        fingerprint での判定に落とす。
        """

        try:
            state = self._update_query.load_revision_state()
        except Exception:  # noqa: BLE001 — 変化検知の失敗で分析全体を落とさない
            logger.debug("Failed to read SPSA revision state for analysis cache", exc_info=True)
            return None
        return state.data_generation if state is not None else None

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
            cached_generation = self._correlation_generation

        generation = self._current_data_generation()

        # ロードを省いてよいのは「変化していないと**断定できる**」ときだけ。
        # 以前はここが「dirty でなく TTL 内」だったが、`notify_updates_changed()` を
        # production から呼ぶ経路が無かったため、分析結果が最大 5 分固まっていた。
        if not dirty and cached is not None and generation is not None and generation == cached_generation:
            return cached
        if not dirty and cached is not None and generation is None and (time.monotonic() - cached_at) < _CACHE_TTL_S:
            # data_generation を読めない（アーカイブ閲覧など）。元データが動かない
            # 前提なので、ロード頻度を抑えるために TTL で間引く。
            return cached

        try:
            updates = self._load_updates_for_correlation()
            fp = _compute_fingerprint(updates)

            # Data unchanged — reuse cached result
            if cached is not None and fp == cached_fp:
                with self._lock:
                    self._correlation_dirty = False
                    self._correlation_generation = generation
                return cached

            result = self._analysis.compute_correlation_analysis(updates)
            snapshot = _wrap_ready_correlation(result)
            with self._lock:
                self._correlation_snapshot = snapshot
                self._correlation_fingerprint = fp
                self._correlation_cached_at = time.monotonic()
                self._correlation_generation = generation
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
            cached_generation = self._convergence_generation

        generation = self._current_data_generation()

        if not dirty and cached is not None and generation is not None and generation == cached_generation:
            return cached
        if not dirty and cached is not None and generation is None and (time.monotonic() - cached_at) < _CACHE_TTL_S:
            return cached

        try:
            updates = self._load_updates_for_convergence()
            fp = _compute_fingerprint(updates)

            # Data unchanged — reuse cached result
            if cached is not None and fp == cached_fp:
                with self._lock:
                    self._convergence_dirty = False
                    self._convergence_generation = generation
                return cached

            result = self._analysis.compute_convergence_analysis(updates)
            snapshot = _wrap_ready_convergence(result)
            with self._lock:
                self._convergence_snapshot = snapshot
                self._convergence_fingerprint = fp
                self._convergence_cached_at = time.monotonic()
                self._convergence_generation = generation
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
