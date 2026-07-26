"""Run-health terminal artifact model（task 0052）。

``completion_status.json`` は run の verdict を表す **terminal commit** であり、
finalize が走ったという事実の marker ではない。

status（3値）と termination reason（何が起きたか）を分離することで、
SPRT の正常早期終了と異常中断を区別できるようにする（review finding M2）。

判定は explicit failure、正常 termination、anomaly の順で行う純関数として実装し、
IO や service state から独立させる（decisions.md Decision 7）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from shogiarena._core.shared.kernel.json_types import JsonObject

# ``completion_status.json`` の format 版数。1.1.0 で初公開するため 1 から始める。
COMPLETION_STATUS_SCHEMA_VERSION = 1


class RunHealthStatus(StrEnum):
    """run の verdict。公開集合はこの3値を維持する。

    ``failed`` は「この run の結果を完了した測定として扱えない」という意味であり、
    異常終了を意味しない。利用者が意図して停止した run も ``failed`` になる。
    実際に何が起きたかは ``termination_reason`` が表す。
    """

    CLEAN = "clean"
    WITH_ANOMALIES = "with-anomalies"
    FAILED = "failed"


class RunTerminationReason(StrEnum):
    """run が終了した理由。``status`` と対で読む。"""

    # 正常終了
    SCHEDULE_COMPLETE = "schedule-complete"
    SPRT_FINISHED = "sprt-finished"
    # 操作による中断（故障ではない）
    CANCELLED = "cancelled"
    # 安全装置による停止
    TIMEOUT_BURST = "timeout-burst"
    TIMEOUT_ATTRIBUTION_UNKNOWN = "timeout-attribution-unknown"
    TRANSPORT_TIMEOUT = "transport-timeout"
    # 異常
    INCOMPLETE = "incomplete"
    RUNTIME_ERROR = "runtime-error"
    FINALIZATION_ERROR = "finalization-error"
    CLEANUP_ERROR = "cleanup-error"


_NORMAL_TERMINATION_REASONS: frozenset[RunTerminationReason] = frozenset(
    {
        RunTerminationReason.SCHEDULE_COMPLETE,
        RunTerminationReason.SPRT_FINISHED,
    }
)

# 正常 termination 以外はすべて failure 扱いにする（新しい reason を足しても fail closed のまま）。
_FAILURE_REASONS: frozenset[RunTerminationReason] = frozenset(RunTerminationReason) - _NORMAL_TERMINATION_REASONS

# ``SessionStopController.reason`` から termination reason への写像。
# ここに無い reason は「認識できない停止」として schedule 完了状況から判定する。
_STOP_REASON_TO_TERMINATION: Mapping[str, RunTerminationReason] = {
    "sprt-finished": RunTerminationReason.SPRT_FINISHED,
    "cancelled": RunTerminationReason.CANCELLED,
    "timeout-burst": RunTerminationReason.TIMEOUT_BURST,
    "timeout-attribution-unknown": RunTerminationReason.TIMEOUT_ATTRIBUTION_UNKNOWN,
    "transport-timeout": RunTerminationReason.TRANSPORT_TIMEOUT,
}


def resolve_termination_reason(
    *,
    stop_reason: str | None,
    is_schedule_complete: bool,
    has_valid_sprt_decision: bool,
) -> RunTerminationReason:
    """stop reason と完了状況から termination reason を決める（純関数）。

    Args:
        stop_reason: ``SessionStopController.reason``。停止要求が無ければ ``None``。
        is_schedule_complete: 予定局がすべて完了または取消済みか。
        has_valid_sprt_decision: SPRT が有効な decision へ到達しているか。
            stop reason 文字列だけを成功証拠にしないため、呼び出し側で
            ``is_finished()`` と decision の存在を同時に確認した結果を渡す。

    Returns:
        確定した termination reason。
    """
    normalized = (stop_reason or "").strip()
    mapped = _STOP_REASON_TO_TERMINATION.get(normalized)
    if mapped is RunTerminationReason.SPRT_FINISHED:
        # 早期終了を主張していても decision が無ければ成功証拠にしない。
        return RunTerminationReason.SPRT_FINISHED if has_valid_sprt_decision else RunTerminationReason.INCOMPLETE
    if mapped is not None:
        return mapped
    # 認識できない停止（reschedule、openbench-stop など）と停止要求なしは同じ扱いにする。
    # 予定を完走していれば正常終了、していなければ incomplete。
    if is_schedule_complete:
        return RunTerminationReason.SCHEDULE_COMPLETE
    return RunTerminationReason.INCOMPLETE


@dataclass(frozen=True, slots=True)
class RunHealthInputs:
    """terminal status の入力。memory 上で組み立ててから atomic write する。"""

    termination_reason: RunTerminationReason
    scheduled: int
    completed: int
    cancelled: int
    error_games: int
    # origin 別 timeout 件数。origin 情報を持たない DB では空になる。
    timeouts_by_origin: Mapping[str, int] = field(default_factory=dict)
    # 証拠が原因を確定できなかった timeout の件数（``unknown`` origin）。
    # coverage 欠落、ring overflow、watchdog restart、explanatory lag のいずれかで発生する。
    coverage_incomplete_timeouts: int = 0
    # 認識できなかった stop reason をそのまま残す（診断用の additive key）。
    stop_reason: str | None = None

    @property
    def not_played(self) -> int:
        """予定したが実施しなかった局数。

        ``incomplete`` という語は「異常に欠けた」と読まれ、``status=clean`` と並ぶと
        事故と誤読されるため使わない。正常な SPRT 早期終了でもこの値は増える。
        """
        return max(0, self.scheduled - self.completed - self.cancelled)

    @property
    def is_schedule_complete(self) -> bool:
        return self.not_played == 0

    @property
    def has_anomalies(self) -> bool:
        return self.error_games > 0 or self.coverage_incomplete_timeouts > 0


def resolve_run_health_status(inputs: RunHealthInputs) -> RunHealthStatus:
    """termination reason と anomaly から status を決める（純関数）。

    判定順は explicit failure、正常 termination、anomaly とする。
    failure または cancellation reason があれば game count にかかわらず ``failed``。
    """
    reason = inputs.termination_reason
    if reason in _FAILURE_REASONS:
        return RunHealthStatus.FAILED
    if reason is RunTerminationReason.SCHEDULE_COMPLETE and not inputs.is_schedule_complete:
        # 正常 termination を主張しているが完走していない。証拠が無いので failed に倒す。
        return RunHealthStatus.FAILED
    return RunHealthStatus.WITH_ANOMALIES if inputs.has_anomalies else RunHealthStatus.CLEAN


def build_completion_status_payload(
    inputs: RunHealthInputs,
    *,
    watchdog: JsonObject | None = None,
    is_provisional: bool = False,
    cleanup_error: str | None = None,
) -> JsonObject:
    """``completion_status.json`` の payload を組み立てる（純関数）。

    reader は未知の additive key を許容すること。

    Args:
        watchdog: watchdog summary。取得できない場合のみ ``None``。
        is_provisional: cleanup の結果をまだ反映していない暫定 status か
            （decisions.md Decision 7 の 2 段 commit）。中断経路では cleanup 前に
            一度書いてから、cleanup 後に確定へ昇格する。
        cleanup_error: service cleanup が失敗した場合の要約。中断理由そのものは
            置き換えず、additive key として併記する。
    """
    payload: JsonObject = {
        "schema_version": COMPLETION_STATUS_SCHEMA_VERSION,
        "status": resolve_run_health_status(inputs).value,
        "termination_reason": inputs.termination_reason.value,
        "scheduled": inputs.scheduled,
        "completed": inputs.completed,
        "cancelled": inputs.cancelled,
        "not_played": inputs.not_played,
        "error_games": inputs.error_games,
        "timeouts_by_origin": dict(sorted(inputs.timeouts_by_origin.items())),
        "coverage_incomplete_timeouts": inputs.coverage_incomplete_timeouts,
        "watchdog": watchdog,
        "is_provisional": is_provisional,
    }
    if cleanup_error is not None:
        payload["cleanup_error"] = cleanup_error
    if inputs.stop_reason is not None and inputs.stop_reason not in _STOP_REASON_TO_TERMINATION:
        # 認識できなかった stop reason は診断のために残す（additive key）。
        payload["stop_reason"] = inputs.stop_reason
    return payload


__all__ = [
    "COMPLETION_STATUS_SCHEMA_VERSION",
    "RunHealthInputs",
    "RunHealthStatus",
    "RunTerminationReason",
    "build_completion_status_payload",
    "resolve_run_health_status",
    "resolve_termination_reason",
]
