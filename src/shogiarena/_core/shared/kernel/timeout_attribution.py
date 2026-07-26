"""Timeout origin classification（task 0047、0052 で証拠モデルへ置換）。

timeout が「engine が本当に遅かった（``engine_deadline``）」のか「orchestrator/event loop が
停止した（``orchestrator_stall``）」のかを、**証拠**から判定する純関数。

0047 の「watchdog lag を全量控除する」方式は廃止した。lag は engine が実際に探索していた
時間でもあるため、控除式からは因果関係が得られず、正当な時間切れ負けを無効局へ倒しうる
（review finding H1）。

判定の入力は次の3つである。

- GameClock が確定した deadline と budget。
- ShogiArena が ``bestmove`` を最初に観測した時刻（実到着時刻の **上界**）。
- その窓に対する watchdog の lag と coverage。

lag が判定に影響するのは、その lag が超過分を説明できる大きさのときだけとする
（**explanatory lag**）。観測時刻は上界なので、実到着は最悪でも ``観測時刻 - lag`` 以上である。
したがって ``overshoot_ms > lag_ms_in_window`` なら、lag をすべて観測遅延と仮定しても実到着は
deadline より後になり、engine が deadline を超えたと断定できる。

lag を測る窓は ``[窓の開始, 観測上界]`` とする。``bestmove`` を観測した **後** に起きた停滞は、
その観測が遅れた理由を説明できないため、因果の判定には使わない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagObservation


class TimeoutOrigin(StrEnum):
    """timeout の原因分類。DB / manifest には value(str) を保存する。"""

    ENGINE_DEADLINE = "engine_deadline"
    ORCHESTRATOR_STALL = "orchestrator_stall"
    TRANSPORT_TIMEOUT = "transport_timeout"
    UNKNOWN = "unknown"
    # attribution opt-out（SPSA / テスト / 別セッション）。従来どおり時間切れ負けとして扱う。
    UNATTRIBUTED = "unattributed"


class ObservationBasis(StrEnum):
    """``bestmove`` 観測時刻の由来。delivery coverage を主張できるかを決める。"""

    LOCAL_PIPE = "local_pipe"
    REMOTE_TRANSPORT = "remote_transport"
    THIRD_PARTY_PORT = "third_party_port"


@dataclass(frozen=True, slots=True)
class TimeoutWindow:
    """GameClock が move 開始時に確定した deadline snapshot。

    ``budget_ms`` が ``None`` の mode（``search_limits`` / infinite / depth / nodes）は
    wall-clock deadline を持たないため、game-clock timeout として推定しない。
    """

    started_at_s: float
    budget_ms: float | None
    deadline_s: float | None
    clock_mode: str


@dataclass(frozen=True, slots=True)
class DeliveryCoverage:
    """engine 出力から timestamp 採取点までの経路を attribution 用に coverage できるか。"""

    basis: ObservationBasis
    is_covered: bool


@dataclass(frozen=True, slots=True)
class TimeoutEvidence:
    """origin 判定の入力。すべて immutable。"""

    site: str
    """timeout を検出した箇所（``loop_top_expired`` など）。"""

    detected_at_s: float
    """timeout を検出した monotonic 時刻。``bestmove`` 未観測時の窓終端に使う。"""

    is_attribution_enabled: bool
    """attribution が opt-in されているか。False なら ``unattributed``。"""

    window: TimeoutWindow | None = None
    bestmove_observed_at_s: float | None = None
    """``bestmove`` を最初に観測した monotonic 時刻（実到着時刻の上界）。未観測は ``None``。"""

    delivery: DeliveryCoverage | None = None
    """``None`` は timestamp を提供しない実装（capability absence）。"""

    coverage: LoopLagObservation | None = None
    is_transport_failure: bool = False
    """protocol wait / handshake の失敗など、engine の思考時間超過ではない失敗か。"""


@dataclass(frozen=True, slots=True)
class TimeoutAttributionDecision:
    """origin と、その判定根拠。log と run-health artifact の双方から追える。"""

    origin: TimeoutOrigin
    site: str
    reason: str
    clock_mode: str | None = None
    deadline_s: float | None = None
    budget_ms: float | None = None
    bestmove_observed_at_s: float | None = None
    overshoot_ms: float | None = None
    lag_ms_in_window: float | None = None
    is_coverage_complete: bool = False

    @property
    def is_invalid(self) -> bool:
        """勝敗を付けず rating / SPRT から除外すべきか。"""
        return is_invalid_timeout_origin(self.origin)


def _is_finite(*values: float | None) -> bool:
    return all(value is not None and math.isfinite(value) for value in values)


def classify_timeout_evidence(evidence: TimeoutEvidence) -> TimeoutAttributionDecision:
    """証拠から timeout origin を決める（純関数）。

    判定順は decisions.md の Origin Decision Table に対応する。
    証拠が engine 起因と orchestrator 起因を区別できない場合は ``unknown`` とし、
    正常な勝敗へは倒さない。
    """
    site = evidence.site

    def decide(
        origin: TimeoutOrigin,
        reason: str,
        *,
        overshoot_ms: float | None = None,
    ) -> TimeoutAttributionDecision:
        window = evidence.window
        coverage = evidence.coverage
        return TimeoutAttributionDecision(
            origin=origin,
            site=site,
            reason=reason,
            clock_mode=window.clock_mode if window is not None else None,
            deadline_s=window.deadline_s if window is not None else None,
            budget_ms=window.budget_ms if window is not None else None,
            bestmove_observed_at_s=evidence.bestmove_observed_at_s,
            overshoot_ms=overshoot_ms,
            lag_ms_in_window=coverage.lag_ms_in_window if coverage is not None else None,
            is_coverage_complete=bool(coverage is not None and coverage.coverage_complete),
        )

    if not evidence.is_attribution_enabled:
        return decide(TimeoutOrigin.UNATTRIBUTED, "attribution-disabled")

    window = evidence.window
    if window is None:
        # clock 状態が揃わない防御経路。正常な勝敗へは倒さない。
        return decide(TimeoutOrigin.UNKNOWN, "missing-clock-window")

    if window.budget_ms is None or window.deadline_s is None:
        # wall-clock budget を持たない mode の wait 失敗は engine の時間超過ではない。
        return decide(TimeoutOrigin.TRANSPORT_TIMEOUT, "no-wall-clock-budget")

    if evidence.is_transport_failure:
        return decide(TimeoutOrigin.TRANSPORT_TIMEOUT, "transport-failure")

    if not _is_finite(window.started_at_s, window.budget_ms, window.deadline_s) or window.budget_ms < 0.0:
        return decide(TimeoutOrigin.UNKNOWN, "invalid-clock-window")
    if window.deadline_s < window.started_at_s:
        return decide(TimeoutOrigin.UNKNOWN, "inverted-clock-window")

    observed_at_s = evidence.bestmove_observed_at_s
    if observed_at_s is not None and not math.isfinite(observed_at_s):
        return decide(TimeoutOrigin.UNKNOWN, "invalid-observation-time")

    # deadline 以前に観測済みで、その後の処理で expiry した場合だけが positive orchestrator evidence。
    # coverage の有無に関係なく成立する（観測時刻そのものが証拠）。
    if observed_at_s is not None and observed_at_s <= window.deadline_s:
        return decide(TimeoutOrigin.ORCHESTRATOR_STALL, "bestmove-observed-before-deadline", overshoot_ms=0.0)

    coverage = evidence.coverage
    if coverage is None or not coverage.is_available:
        return decide(TimeoutOrigin.UNKNOWN, "watchdog-coverage-unavailable")
    if not coverage.coverage_complete:
        # ring overflow、watchdog restart、起動前の窓。lag 0 と証拠喪失を混同しない。
        return decide(TimeoutOrigin.UNKNOWN, "watchdog-coverage-incomplete")

    delivery = evidence.delivery
    if delivery is None or not delivery.is_covered:
        # remote transport や third-party port で delivery path を保証できない場合、
        # 「deadline 後に観測した / 観測できなかった」を engine 起因の証拠に使わない。
        return decide(TimeoutOrigin.UNKNOWN, "delivery-coverage-missing")

    end_s = observed_at_s if observed_at_s is not None else evidence.detected_at_s
    if not math.isfinite(end_s) or end_s < window.deadline_s:
        return decide(TimeoutOrigin.UNKNOWN, "invalid-observation-window")

    overshoot_ms = (end_s - window.deadline_s) * 1000.0
    lag_ms = max(0.0, coverage.lag_ms_in_window)
    if overshoot_ms > lag_ms:
        # 観測遅延を最大に見積もっても実到着が deadline より後になる。
        return decide(TimeoutOrigin.ENGINE_DEADLINE, "overshoot-exceeds-lag", overshoot_ms=overshoot_ms)
    # explanatory lag: 停滞だけで超過を説明できるため因果を確定できない。
    return decide(TimeoutOrigin.UNKNOWN, "explanatory-lag", overshoot_ms=overshoot_ms)


def is_invalid_timeout_origin(origin: TimeoutOrigin) -> bool:
    """勝敗を付けず rating / SPRT から除外すべき origin か（無効局）。"""
    return origin in (TimeoutOrigin.ORCHESTRATOR_STALL, TimeoutOrigin.UNKNOWN, TimeoutOrigin.TRANSPORT_TIMEOUT)


def is_invalid_timeout_origin_value(value: str | None) -> bool:
    """record 属性などの文字列 origin が無効局かを安全に判定する（未知/None は False）。"""
    if value is None:
        return False
    try:
        return is_invalid_timeout_origin(TimeoutOrigin(value))
    except ValueError:
        return False


__all__ = [
    "DeliveryCoverage",
    "ObservationBasis",
    "TimeoutAttributionDecision",
    "TimeoutEvidence",
    "TimeoutOrigin",
    "TimeoutWindow",
    "classify_timeout_evidence",
    "is_invalid_timeout_origin",
    "is_invalid_timeout_origin_value",
]
