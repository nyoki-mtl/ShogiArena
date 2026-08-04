"""`/summary` のスナップショット供給（task 0065 (B)）の契約を固定する。

狙いはリクエストパスから I/O と SQLite を外すことである。GIL convoy の下では、
C 境界を 1 回跨ぐたびに event loop からの GIL 引き渡しを待つので、
**境界を跨ぐ回数そのもの**がリクエストの所要時間を決める。

内側の `SpsaSummaryService` は無変更で、「呼ぶたびに実バイトを読み直す」という
integrity evidence の保証はそちらに残る（`test_spsa_operational_summary.py`）。
"""

from __future__ import annotations

from typing import Any, cast

from shogiarena._core.contexts.dashboard.adapters.spsa.summary_snapshot_cache import (
    SpsaSummarySnapshotCache,
)
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import SpsaSummaryPayload
from shogiarena._core.contexts.spsa.ports.spsa_store_port import SpsaSummaryRefreshPort


class _CountingSummaryService:
    """`compute_summary` の呼び出し回数を数える内側サービス。"""

    def __init__(self) -> None:
        self.calls = 0
        self.wins = 0

    def compute_summary(self) -> SpsaSummaryPayload:
        self.calls += 1
        self.wins += 1
        return cast(SpsaSummaryPayload, {"mode": "spsa", "wins": self.wins})


def _cache(inner: Any, *, max_age_s: float = 60.0) -> SpsaSummarySnapshotCache:
    return SpsaSummarySnapshotCache(inner, max_age_s=max_age_s)


def test_repeated_requests_do_not_recompute() -> None:
    """これが本題。リクエストごとに I/O と SQLite を叩き直さない。"""

    inner = _CountingSummaryService()
    cache = _cache(inner)

    first = cache.compute_summary()
    for _ in range(20):
        cache.compute_summary()

    assert inner.calls == 1
    assert first["wins"] == 1  # type: ignore[typeddict-item]


def test_first_request_computes_when_nothing_has_warmed_the_snapshot() -> None:
    """refresh 経路の走らない run（アーカイブ閲覧など）でも正しく答える。"""

    inner = _CountingSummaryService()
    cache = _cache(inner)

    assert cache.compute_summary()["wins"] == 1  # type: ignore[typeddict-item]
    assert inner.calls == 1


def test_refresh_updates_the_snapshot_that_requests_read() -> None:
    """run 実行中の refresh 経路が書き、`/summary` が読む、という関係を表明する。"""

    inner = _CountingSummaryService()
    cache = _cache(inner)

    cache.compute_summary()
    assert cache.compute_summary()["wins"] == 1  # type: ignore[typeddict-item]

    cache.refresh_summary()

    assert inner.calls == 2
    assert cache.compute_summary()["wins"] == 2  # type: ignore[typeddict-item]
    assert inner.calls == 2


def test_stale_snapshot_is_recomputed() -> None:
    inner = _CountingSummaryService()
    cache = _cache(inner, max_age_s=0.0)

    cache.compute_summary()
    cache.compute_summary()

    assert inner.calls == 2


def test_freshness_bound_is_reported_in_the_payload() -> None:
    """「as of this request」が「as of <= max_age 前」に変わることを黙って渡さない。"""

    inner = _CountingSummaryService()
    cache = _cache(inner, max_age_s=2.5)

    payload = cast(dict[str, Any], cache.compute_summary())
    freshness = payload["summary_freshness"]

    assert freshness["max_age_ms"] == 2500
    assert freshness["age_ms"] == 0

    # 絶対時刻ではなく配る瞬間の経過 ms を載せる。同じ内容から作った payload 同士が
    # 等しくなるので、内容の同一性を見る既存テストを壊さない。
    assert set(freshness) == {"age_ms", "max_age_ms"}


def test_cache_satisfies_the_refresh_port_but_a_plain_service_does_not() -> None:
    """run 側は `isinstance` でこの分岐をする。fake に refresh を生やさせない設計。"""

    assert isinstance(_cache(_CountingSummaryService()), SpsaSummaryRefreshPort)
    assert not isinstance(_CountingSummaryService(), SpsaSummaryRefreshPort)


def test_freshness_field_does_not_leak_into_the_stored_snapshot() -> None:
    """鮮度は配る瞬間に付ける。保存側に混ぜると age が固まってしまう。"""

    inner = _CountingSummaryService()
    cache = _cache(inner)

    cache.compute_summary()
    cache.refresh_summary()
    payload = cast(dict[str, Any], cache.compute_summary())

    assert payload["wins"] == 2
    assert set(payload) == {"mode", "wins", "summary_freshness"}
