"""SPSA orchestration event payload helpers."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True)
class SpsaScheduledEventCommonRequest:
    """Input DTO for scheduled SPSA event payload."""

    update_idx: int
    game_id: str
    tuned_variant_token: str
    baseline_variant_token: str
    variant_label: str
    phase: str
    is_tuned_as_black: bool
    black_player: str
    white_player: str
    assigned_instance: str | None
    worker_idx: int
    event_family: str


def build_scheduled_event_common(*, request: SpsaScheduledEventCommonRequest) -> JsonObject:
    """スケジュール済み SPSA イベントの共通ペイロードを構築する。"""

    return {
        "event": "game_scheduled",
        "update_idx": request.update_idx,
        "game_id": request.game_id,
        "variant_token": request.tuned_variant_token,
        "tuned_variant_token": request.tuned_variant_token,
        "baseline_variant_token": request.baseline_variant_token,
        "variant_label": request.variant_label,
        "phase": request.phase,
        "tuned_as_black": request.is_tuned_as_black,
        "black_player": request.black_player,
        "white_player": request.white_player,
        "assigned_instance": request.assigned_instance,
        "worker_idx": request.worker_idx,
        "family": request.event_family,
        "is_ltc": request.event_family == "ltc",
    }


def build_status_payload(
    *,
    event_common: JsonObject,
    status: str,
    start_time: str | None,
) -> JsonObject:
    """ステータス情報を付加したペイロードを構築する。"""

    payload = dict(event_common)
    payload["status"] = status
    payload["start_time"] = start_time
    return payload


__all__ = [
    "SpsaScheduledEventCommonRequest",
    "build_scheduled_event_common",
    "build_status_payload",
]
