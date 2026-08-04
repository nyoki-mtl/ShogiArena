"""Serve the SPSA summary from a snapshot so requests do no I/O.

task 0065 の計測で、`compute_summary()` の遅さの正体は I/O ではなく **GIL の再取得待ち**
だと分かった。1 リクエストで 60〜80 回の C 境界（ファイル読み、SQLite 呼び出し）を跨ぎ、
その 1 回ごとに event loop からの GIL 引き渡しを待つ。

`json_serialize` の最適化（同 task の (A)）は convoy の**握っている側**を軽くしたが、
`/summary` がリクエスト同期で 60〜80 境界を跨ぐ設計そのものは残る。convoy は
「握る側 × 跨ぐ側」の積なので、将来 loop に仕事が増えれば再発する。ここはその積の
後者を撃つ。

run 実行中は、progress イベント駆動のコアレス済み refresh（`_update_dashboard` →
`build_spsa_dashboard_summary_payload`）が既に worker thread 上で `compute_summary()` を
呼んで SSE に流している。REST の `/summary` は同じものを独立に計算し直していた。
このキャッシュはその計算結果を 1 箇所に集約する。
"""

from __future__ import annotations

import time
from threading import Lock

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import SpsaSummaryPayload
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaSummaryServicePort

# キャッシュを再計算せずに返してよい上限。
#
# **run 実行中の実際の陳腐化を決めるのはこの値ではなく refresh の頻度である。**
# progress イベント駆動の refresh が上書きし続けるので、配る payload の age は
# refresh 間隔（実測で約 2.3 秒）で抑えられる。この値はそれより大きくてよく、
# 大きくするほどリクエスト起因の再計算が減る。
#
# この値が効くのは refresh の走らない経路——アーカイブされた run の閲覧や
# run 開始直後——であり、そこでは元データが動かないので陳腐化に実害がない。
# 上限は `summary_freshness.max_age_ms` として payload に載せて宣言する。
DEFAULT_MAX_AGE_S = 5.0


class SpsaSummarySnapshotCache(DashboardSpsaSummaryServicePort):
    """Serve the last computed summary, recomputing only when it is missing or stale.

    **`compute_summary()` を実装する内側のサービスには手を入れない。**
    0054/0056 が固めた整合性テストは内側のサービスを直接叩いており、
    「呼ぶたびに実バイトを読み直す」ことを表明している。その保証は内側に残す。
    """

    def __init__(
        self,
        inner: DashboardSpsaSummaryServicePort,
        *,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> None:
        self._inner = inner
        self._max_age_s = max(0.0, max_age_s)
        self._lock = Lock()
        self._payload: SpsaSummaryPayload | None = None
        self._computed_at_perf: float = 0.0

    def compute_summary(self) -> SpsaSummaryPayload:
        """Return the cached summary, recomputing only when missing or stale.

        `/summary` のリクエストパスはここを通る。キャッシュが有効なら
        ファイル読みも SQLite クエリも一切行わないので、C 境界を跨がない。
        """

        with self._lock:
            payload = self._payload
            age_s = time.perf_counter() - self._computed_at_perf
            if payload is not None and age_s <= self._max_age_s:
                return self._with_freshness(payload, age_ms=int(age_s * 1000.0))
        return self.refresh_summary()

    def refresh_summary(self) -> SpsaSummaryPayload:
        """Recompute the summary and store it as the new snapshot.

        run 実行中の refresh 経路（対局の進捗ごと、コアレス済み、worker thread 上）が
        呼ぶ。`/summary` はこの結果を読むだけになる。
        """

        computed = self._inner.compute_summary()
        computed_at_perf = time.perf_counter()
        with self._lock:
            self._payload = computed
            self._computed_at_perf = computed_at_perf
        return self._with_freshness(computed, age_ms=0)

    def _with_freshness(self, payload: SpsaSummaryPayload, *, age_ms: int) -> SpsaSummaryPayload:
        """Attach the observable freshness bound.

        「as of this request」が「as of ≤ max_age 前」に変わるのが、このキャッシュで
        意識的に払う唯一の代償である。黙って渡さず、payload に載せて観測可能にする。

        絶対時刻ではなく **配る瞬間の経過 ms** を載せる。受け手が知りたいのは
        「この数字はいま何 ms 古いか」であって、時計ずれのありうる絶対時刻ではない。
        副次的に、同じ内容から作った payload 同士が等しくなるので、
        内容の同一性を見る既存テストを壊さない。
        """

        enriched: SpsaSummaryPayload = {**payload}
        enriched["summary_freshness"] = {
            "age_ms": age_ms,
            "max_age_ms": int(self._max_age_s * 1000.0),
        }
        return enriched


__all__ = ["DEFAULT_MAX_AGE_S", "SpsaSummarySnapshotCache"]
