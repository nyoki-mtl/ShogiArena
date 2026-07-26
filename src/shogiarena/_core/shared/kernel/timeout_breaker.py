"""無効 timeout に対する run 停止条件（task 0052 / decisions.md Decision 6）。

局単位では無効 timeout を標本から除外するだけで run は続ける。
run 単位では origin ごとの閾値へ達したときにだけ新規 dispatch を止める。

shared kernel に置くのは、閾値を適用する側（game_session の completion）と
resume で counter を復元する側（tournament の state store）の双方が必要とするため。
counter の集計規則を 1 箇所に持たないと、run 中と resume 後で数え方がずれる。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin


@dataclass(frozen=True, slots=True)
class TimeoutBreakerPolicy:
    """origin ごとの run 停止条件。"""

    consecutive_limit: int
    """連続して無効になった局数の上限。1回の停滞は並行局を同時に無効化しうる。"""

    ratio_limit: float | None
    """完了局に対する比率の上限。``None`` なら比率では止めない。"""

    ratio_min_games: int
    """比率で止めるのに必要な最小件数。少数例で止めないためのガード。"""

    termination_reason: str


# decisions.md Decision 16 の shadow 計測（300局 × 4条件）で確定した閾値。
#
# 実測分布:
#   healthy / CPU 高負荷      : unknown 0%、orchestrator_stall 0%
#   軽微な停滞（duty ~24%）   : orchestrator_stall 0.33%（最大連続 1）、unknown 0%
#   継続的な停滞（duty ~89%） : unknown 36.7%（最大連続 12）、orchestrator_stall 1.7%（最大連続 2）
#
# 比率 1% は「軽微な停滞（0.33%）は通し、継続的な停滞（1.7% / 36.7%）は止める」線に引ける。
# orchestrator_stall は最大連続が 2 までしか観測されなかったため、連続だけでは止まらない。
# 比率閾値を持たせないと、停滞が確定していても run が続いてしまうので同じ比率を適用する。
TIMEOUT_BREAKER_POLICIES: dict[str, TimeoutBreakerPolicy] = {
    TimeoutOrigin.ORCHESTRATOR_STALL.value: TimeoutBreakerPolicy(
        consecutive_limit=5,
        ratio_limit=0.01,
        ratio_min_games=5,
        termination_reason="timeout-burst",
    ),
    TimeoutOrigin.UNKNOWN.value: TimeoutBreakerPolicy(
        consecutive_limit=3,
        ratio_limit=0.01,
        ratio_min_games=5,
        termination_reason="timeout-attribution-unknown",
    ),
    TimeoutOrigin.TRANSPORT_TIMEOUT.value: TimeoutBreakerPolicy(
        consecutive_limit=3,
        ratio_limit=0.01,
        ratio_min_games=5,
        termination_reason="transport-timeout",
    ),
}


def resolve_breaker_stop_reason(
    *,
    totals: Mapping[str, int],
    consecutive: Mapping[str, int],
    completed: int,
) -> str | None:
    """counter が停止条件へ達しているかを判定する（純関数）。

    run 中の適用と、resume 時の再評価（review H1）で同じ規則を使う。
    分岐が 2 箇所にあると、resume した run だけ閾値を越えたまま走り続けうる。

    Returns:
        停止すべきなら termination reason。まだなら ``None``。
    """
    for origin, policy in TIMEOUT_BREAKER_POLICIES.items():
        origin_consecutive = consecutive.get(origin, 0)
        origin_total = totals.get(origin, 0)
        is_over_ratio = (
            policy.ratio_limit is not None
            and origin_total >= policy.ratio_min_games
            and completed > 0
            and origin_total / completed > policy.ratio_limit
        )
        if origin_consecutive >= policy.consecutive_limit or is_over_ratio:
            return policy.termination_reason
    return None


def rebuild_timeout_breaker_counters(
    completed_games: Sequence[Mapping[str, object]],
    *,
    totals: Mapping[str, int] | None = None,
    consecutive: Mapping[str, int] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """完了局から breaker counter を再構築する（task 0052 / review M3）。

    run 中の breaker と同じ規則を使う: breaker 対象の origin を持つ ``ERROR`` 局だけを数え、
    それ以外の結果で連続数を戻す。

    連続数は DB の並び（記録順）を完了順とみなす近似になる。総数は正確なので、
    比率による停止判定は resume 後も正しく効く。

    Args:
        completed_games: 数え上げる局。完了順に並んでいること。
        totals: 開始時点の累計。state.json から復元した値に DB の suffix を重ねる用途。
        consecutive: 開始時点の連続数。同上。

    Returns:
        ``(origin 別の累計, origin 別の連続数)``。
    """
    totals = dict(totals or {})
    consecutive = dict(consecutive or {})
    for game in completed_games:
        origin = game.get("timeout_origin") if game.get("result") == GameResult.ERROR else None
        if not isinstance(origin, str) or origin not in TIMEOUT_BREAKER_POLICIES:
            consecutive.clear()
            continue
        current = consecutive.get(origin, 0) + 1
        consecutive.clear()
        consecutive[origin] = current
        totals[origin] = totals.get(origin, 0) + 1
    return totals, consecutive


__all__ = [
    "TIMEOUT_BREAKER_POLICIES",
    "TimeoutBreakerPolicy",
    "rebuild_timeout_breaker_counters",
    "resolve_breaker_stop_reason",
]
